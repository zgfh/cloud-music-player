"""
音乐服务层 - 封装音乐播放、列表管理等业务逻辑
提供给视图层使用的抽象接口，解耦视图与应用核心逻辑
"""

import asyncio
import base64
import inspect
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from .download_progress import DownloadProgressTracker

logger = logging.getLogger(__name__)


class MusicService:
    """音乐服务 - 处理音乐文件、播放列表、同步等业务逻辑"""

    def __init__(
        self,
        music_library,
        nextcloud_client,
        config_manager,
        lyrics_service=None,
        source_clients=None,
    ):
        """
        初始化音乐服务

        Args:
            music_library: 音乐库实例
            nextcloud_client: NextCloud客户端实例（可以为None）
            config_manager: 配置管理器实例
            lyrics_service: 歌词服务实例（可选）
        """
        self.music_library = music_library
        self.nextcloud_client = nextcloud_client
        self.source_clients = source_clients if source_clients is not None else {}
        if nextcloud_client and not self.source_clients:
            source_type = config_manager.get("connection.source_type", "nextcloud")
            self.source_clients[source_type] = nextcloud_client
        self.config_manager = config_manager
        self.lyrics_service = lyrics_service
        self.download_progress = DownloadProgressTracker()
        # 最近一次同步的逐目录报告，供音乐库页面展示同步摘要和详情。
        self.last_sync_report: List[Dict[str, Any]] = []

        # 回调函数
        self._playlist_change_callback: Optional[Callable[[List[str], int], None]] = (
            None
        )
        self._sync_folder_change_callback: Optional[Callable[[str], None]] = None

    def set_playlist_change_callback(self, callback: Callable[[List[str], int], None]):
        """设置播放列表变化的回调函数"""
        self._playlist_change_callback = callback

    def set_sync_folder_change_callback(self, callback: Callable[[str], None]):
        """设置同步文件夹变化的回调函数"""
        self._sync_folder_change_callback = callback

    def update_nextcloud_client(self, nextcloud_client):
        """更新NextCloud客户端实例"""
        self.nextcloud_client = nextcloud_client
        # 同时更新歌词服务的客户端
        if self.lyrics_service:
            self.lyrics_service.update_clients(nextcloud_client=nextcloud_client)
        logger.info("NextCloud客户端已更新")

    def update_source_client(self, source_type: str, client) -> None:
        """注册或移除一个来源客户端，并保持旧活动客户端字段兼容。"""
        if client is None:
            self.source_clients.pop(source_type, None)
        else:
            self.source_clients[source_type] = client
            self.nextcloud_client = client
        logger.info("来源客户端已更新: %s", source_type)

    def get_source_client(self, source_type: str):
        return self.source_clients.get(source_type)

    @staticmethod
    def _normalise_folders(value, fallback="") -> List[str]:
        if isinstance(value, list):
            return list(dict.fromkeys(str(v).strip() for v in value if str(v).strip()))
        value = str(value or "").strip()
        return [value] if value else ([fallback] if fallback else [])

    def get_sync_folders(self, source_type: str) -> List[str]:
        base = (
            "connection" if source_type == "nextcloud" else f"connection.{source_type}"
        )
        folders = self.config_manager.get(f"{base}.sync_folders", None)
        default = self.config_manager.get(f"{base}.default_sync_folder", "")
        # Google Drive 的空字符串表示根目录，显式保留一个根目录任务。
        if folders is None:
            return self._normalise_folders(default) or (
                [""] if source_type == "gdrive" else []
            )
        result = self._normalise_folders(folders)
        return result or ([""] if source_type == "gdrive" and default == "" else [])

    def get_sync_folder_label(self, source_type: str, folder: str) -> str:
        """返回同步目录的友好名称；Google Drive 内部仍使用文件夹 ID。"""
        if source_type == "gdrive":
            labels = self.config_manager.get("connection.gdrive.sync_folder_labels", {})
            if isinstance(labels, dict):
                return str(labels.get(folder, folder or "/"))
        return folder or "/"

    async def sync_all_sources(self) -> List[Dict[str, Any]]:
        """同步所有已连接来源的所有配置目录，汇总进同一个音乐库。"""
        if not self.source_clients:
            raise Exception("尚未连接任何音乐来源")
        merged: List[Dict[str, Any]] = []
        errors = []
        report: List[Dict[str, Any]] = []
        for source_type, client in list(self.source_clients.items()):
            folders = self.get_sync_folders(source_type)
            if not folders:
                continue
            for folder in folders:
                try:
                    files = await client.list_music_files(folder)
                    for file_info in files:
                        item = dict(file_info)
                        item["source_type"] = source_type
                        item["sync_folder"] = item.get("sync_folder", folder)
                        self.music_library.add_remote_song(
                            item["name"],
                            item.get("path", ""),
                            item.get("size", 0),
                            item.get("modified", ""),
                            sync_folder=item.get("sync_folder", folder),
                            source_type=source_type,
                        )
                        merged.append(item)
                    report.append(
                        {
                            "source_type": source_type,
                            "folder": folder,
                            "folder_label": self.get_sync_folder_label(
                                source_type, folder
                            ),
                            "song_count": len(files),
                            "synced_count": len(files),
                            "status": "success",
                            "error": "",
                        }
                    )
                except Exception as ex:
                    logger.error("同步 %s:%s 失败: %s", source_type, folder, ex)
                    errors.append(f"{source_type}({folder or '/'}): {ex}")
                    report.append(
                        {
                            "source_type": source_type,
                            "folder": folder,
                            "folder_label": self.get_sync_folder_label(
                                source_type, folder
                            ),
                            "song_count": 0,
                            "synced_count": 0,
                            "status": "error",
                            "error": str(ex),
                        }
                    )
        self.last_sync_report = report
        if not merged and errors:
            raise Exception("；".join(errors))
        self.music_library.sync_folder = " · ".join(
            f"{source}:{folder or '/'}"
            for source in self.source_clients
            for folder in self.get_sync_folders(source)
        )
        return merged

    def set_lyrics_service(self, lyrics_service):
        """设置歌词服务实例"""
        self.lyrics_service = lyrics_service
        if lyrics_service:
            lyrics_service.update_clients(
                nextcloud_client=self.nextcloud_client, music_library=self.music_library
            )
        logger.info("歌词服务已设置")

    def get_all_songs(self) -> List[Dict[str, Any]]:
        """获取所有歌曲列表"""
        try:
            songs_dict = self.music_library.get_all_songs()
            songs_list = []

            for song_name, song_info in songs_dict.items():
                # 确保每个歌曲信息都包含名称
                song_data = song_info.copy() if isinstance(song_info, dict) else {}
                song_data["name"] = song_name
                songs_list.append(song_data)

            return songs_list
        except Exception as e:
            logger.error(f"获取歌曲列表失败: {e}")
            return []

    def update_song_metadata(self, song_name: str, metadata: Dict[str, Any]) -> bool:
        """保存歌曲展示信息到音乐库索引，不修改任何源文件。"""
        updater = getattr(self.music_library, "update_song_metadata", None)
        return bool(updater and updater(song_name, metadata))

    def is_file_cached(self, filename: str) -> bool:
        """检查文件是否已缓存"""
        try:
            return self.music_library.is_file_cached(filename)
        except Exception as e:
            logger.error(f"检查文件缓存状态失败: {e}")
            return False

    def get_default_sync_folder(self) -> str:
        """获取当前来源类型（nextcloud/smb/gdrive）的默认同步文件夹"""
        source_type = self.config_manager.get("connection.source_type", "nextcloud")
        if source_type == "smb":
            return self.config_manager.get("connection.smb.default_sync_folder", "/")
        if source_type == "gdrive":
            # Google Drive 的同步文件夹为 Drive 文件夹 ID，空表示根目录
            return self.config_manager.get("connection.gdrive.default_sync_folder", "")
        return self.config_manager.get("connection.default_sync_folder", "")

    async def sync_music_files(self, sync_folder: str = "") -> List[Dict[str, Any]]:
        """
        同步音乐文件

        Args:
            sync_folder: 指定的同步文件夹路径

        Returns:
            同步后的音乐文件列表
        """
        if not self.nextcloud_client:
            raise Exception("NextCloud客户端未连接")

        try:
            # 如果没有指定文件夹，使用默认配置
            if not sync_folder.strip():
                sync_folder = self.get_default_sync_folder()

            logger.info(f"开始同步音乐文件，文件夹: {sync_folder}")

            # 获取远程文件列表
            music_files = await self.nextcloud_client.list_music_files(sync_folder)
            source_type = self.config_manager.get("connection.source_type", "nextcloud")

            # 通知同步文件夹变化
            if self._sync_folder_change_callback:
                self._sync_folder_change_callback(sync_folder)

            # 更新音乐库
            if music_files:
                for file_info in music_files:
                    self.music_library.add_remote_song(
                        file_info["name"],
                        file_info.get("path", ""),
                        file_info.get("size", 0),
                        file_info.get("modified", ""),
                        sync_folder=file_info.get("sync_folder", sync_folder),
                        source_type=source_type,
                    )

                # 保存同步文件夹信息
                self.music_library.sync_folder = sync_folder

            logger.info(f"同步完成，共获得 {len(music_files)} 个音乐文件")
            return music_files

        except Exception as e:
            logger.error(f"同步音乐文件失败: {e}")
            raise

    def set_playlist_from_files(
        self, music_files: List[Dict[str, Any]], start_index: int = 0
    ):
        """
        从音乐文件列表设置播放列表

        Args:
            music_files: 音乐文件列表
            start_index: 开始播放的索引
        """
        try:
            # 提取文件名列表
            playlist = [file_info["name"] for file_info in music_files]

            # 通知播放列表变化
            if self._playlist_change_callback:
                self._playlist_change_callback(playlist, start_index)

            logger.info(
                f"设置播放列表，共 {len(playlist)} 首歌曲，开始索引: {start_index}"
            )

        except Exception as e:
            logger.error(f"设置播放列表失败: {e}")
            raise

    def remove_song(self, song_name: str):
        """删除歌曲"""
        try:
            if self.music_library.has_song(song_name):
                self.music_library.remove_song(song_name)
                logger.info(f"删除歌曲: {song_name}")
            else:
                logger.warning(f"歌曲不存在: {song_name}")
        except Exception as e:
            logger.error(f"删除歌曲失败: {e}")
            raise

    def get_song_info(self, song_name: str) -> Dict[str, Any]:
        """获取歌曲信息"""
        try:
            return self.music_library.get_song_info(song_name)
        except Exception as e:
            logger.error(f"获取歌曲信息失败: {e}")
            return {}

    def update_song_info(self, song_name: str, updated_info: Dict[str, Any]):
        """更新歌曲信息"""
        try:
            self.music_library.songs[song_name] = updated_info
            self.music_library.save_music_list()
            logger.info(f"更新歌曲信息: {song_name}")
        except Exception as e:
            logger.error(f"更新歌曲信息失败: {e}")
            raise

    def search_songs(self, query: str) -> List[Dict[str, Any]]:
        """搜索歌曲"""
        try:
            if query.strip():
                # search_songs 返回的是歌曲名称列表
                search_results = self.music_library.search_songs(query)
                all_songs_dict = self.music_library.get_all_songs()

                result_list = []
                for song_name in search_results:
                    if song_name in all_songs_dict:
                        song_data = all_songs_dict[song_name].copy()
                        song_data["name"] = song_name
                        result_list.append(song_data)

                return result_list
            else:
                return self.get_all_songs()
        except Exception as e:
            logger.error(f"搜索歌曲失败: {e}")
            return []

    def has_song(self, song_name: str) -> bool:
        """检查是否存在指定歌曲"""
        try:
            return self.music_library.has_song(song_name)
        except Exception as e:
            logger.error(f"检查歌曲存在性失败: {e}")
            return False

    def get_local_file_path(self, song_name: str) -> str:
        """获取歌曲的本地文件路径"""
        try:
            return self.music_library.get_local_file_path(song_name)
        except Exception as e:
            logger.error(f"获取本地文件路径失败: {e}")
            return ""

    async def download_file(self, file_path: str, filename: str) -> bool:
        """下载单个文件，成功返回 True，失败返回 False。"""
        self.download_progress.enqueue(filename)
        return await self._download_one(file_path, filename)

    async def download_batch(
        self, items: List[Tuple[str, str]], on_complete=None
    ) -> Tuple[int, int]:
        """批量下载 items: [(remote_path, filename)]，返回 (成功数, 失败数)。

        原生可用时把整批一次性提交给系统后台会话：此后应用挂起、锁屏
        甚至被杀，所有任务都由 nsurlsessiond 继续执行，每首完成唤醒应
        用落库；不可用（SMB 来源、非 iOS 等）时逐首 requests，剩余队列
        由调用方在前台续传。on_complete(filename, success) 在每首完成
        时于事件循环线程回调。
        """
        if not items:
            return 0, 0
        self.download_progress.enqueue([name for _, name in items])

        if self._native_batch_applicable():
            tasks = [
                asyncio.create_task(
                    self._download_one(path, name, on_complete=on_complete)
                )
                for path, name in items
            ]
            results = await asyncio.gather(*tasks)
            success = sum(1 for result in results if result)
            return success, len(results) - success

        success_count = 0
        failed_count = 0
        for path, name in items:
            if await self._download_one(path, name, on_complete=on_complete):
                success_count += 1
            else:
                failed_count += 1
        return success_count, failed_count

    def _native_batch_applicable(self) -> bool:
        """当前来源/平台是否可整批提交原生后台会话"""
        from .. import ios_background_download as native

        if not native.is_available() or not self.nextcloud_client:
            return False
        client = self.nextcloud_client
        if (
            getattr(client, "username", None) is None
            or getattr(client, "password", None) is None
        ):
            return False
        # server_url 非 http(s)（SMB 等）没有原生等价路径
        return (
            native.build_webdav_url(getattr(client, "server_url", ""), "/x") is not None
        )

    async def _download_one(
        self,
        file_path: str,
        filename: str,
        raise_on_error: bool = False,
        on_complete=None,
    ) -> bool:
        """单个文件的完整下载流程；异常默认吞掉计为失败。"""
        ok = False
        try:
            ok = await self._download_one_inner(file_path, filename)
        except Exception as e:
            self.download_progress.finish(filename, False, str(e))
            logger.error(f"下载文件失败 {filename}: {e}")
            if raise_on_error:
                raise
        finally:
            if on_complete is not None:
                try:
                    on_complete(filename, ok)
                except Exception as cb_error:
                    logger.warning(f"下载完成回调异常 {filename}: {cb_error}")
        return ok

    async def _download_one_inner(self, file_path: str, filename: str) -> bool:
        tracker = self.download_progress
        if self.is_file_cached(filename):
            logger.info(f"文件已缓存，无需下载: {filename}")
            tracker.finish(filename, True, "文件已在本地")
            return True

        local_path = self.music_library.music_dir / filename
        song_info = self.music_library.get_song_info(filename) or {}
        origins = list(song_info.get("origins") or [])
        if not origins:
            origins = [
                {
                    "source_type": song_info.get("source_type"),
                    "remote_path": song_info.get("remote_path", file_path),
                }
            ]
        candidates = [
            (origin, self.source_clients.get(origin.get("source_type")))
            for origin in origins
            if self.source_clients.get(origin.get("source_type"))
        ]
        if not candidates and self.nextcloud_client:
            candidates = [(origins[0], self.nextcloud_client)]
        if not candidates:
            source_names = ", ".join(
                dict.fromkeys(str(o.get("source_type") or "未知") for o in origins)
            )
            raise Exception(f"歌曲来源未连接: {source_names}")

        last_error = None
        for origin, client in candidates:
            origin_path = origin.get("remote_path") or file_path
            try:
                native_result = await self._download_native(
                    origin_path, filename, local_path, client=client
                )
                if native_result is not None:
                    success = native_result[0]
                else:
                    tracker.mark_downloading(filename)
                    download_method = client.download_file
                    parameters = inspect.signature(download_method).parameters.values()
                    supports_progress = any(
                        p.name == "progress_callback" or p.kind == p.VAR_KEYWORD
                        for p in parameters
                    )
                    if supports_progress:
                        success = await download_method(
                            origin_path,
                            filename,
                            local_path,
                            progress_callback=lambda downloaded, total=0: tracker.update(
                                filename, downloaded, total
                            ),
                        )
                    else:
                        success = await download_method(
                            origin_path, filename, local_path
                        )
                if success:
                    await self._post_download(filename, local_path, origin_path)
                    tracker.finish(filename, True)
                    return True
                last_error = RuntimeError("下载返回失败")
            except Exception as ex:
                last_error = ex
                logger.warning(
                    "从 %s 下载 %s 失败，尝试备用来源: %s",
                    origin.get("source_type"),
                    filename,
                    ex,
                )
        raise last_error or RuntimeError("所有来源下载失败")

    async def _post_download(self, filename: str, local_path, file_path: str) -> None:
        """下载成功后的收尾：转码、标记已下载、抓取歌词。"""
        # 低采样率 MP3（32kHz 等）在部分播放链路解码劣化，转码为 44.1kHz
        from ..utils.audio_normalize import normalize_audio_async

        try:
            await normalize_audio_async(local_path)
        except Exception as norm_error:
            logger.warning(f"音频标准化跳过: {norm_error}")

        # 更新音乐库中的下载状态
        self.music_library.mark_song_downloaded(filename, str(local_path))
        logger.info(f"下载成功并更新状态: {filename}")

        # 同时尝试下载歌词文件（失败不影响下载结果）
        if self.lyrics_service:
            try:
                song_info = self.music_library.get_song_info(filename)
                song_remote_path = (
                    song_info.get("remote_path", file_path) if song_info else file_path
                )

                lyrics_ok = await self.lyrics_service.download_lyrics(
                    filename, song_remote_path
                )
                if lyrics_ok:
                    logger.info(f"歌词下载成功: {filename}")
                else:
                    logger.debug(f"歌词下载失败或不存在: {filename}")

            except Exception as lyrics_error:
                logger.warning(f"启动歌词下载失败: {lyrics_error}")

    async def _download_native(
        self, file_path: str, filename: str, local_path, client=None
    ) -> Optional[Tuple[bool, Optional[str], Optional[str]]]:
        """iOS 优先走原生后台 NSURLSession（切后台/锁屏/被杀均不中断）。

        仅支持带 HTTP 凭据的 NextCloud 来源；SMB 等其它来源、非 iOS
        平台或会话不可用返回 None，调用方回退 requests 路径。已提交
        但传输失败以 (False, path, error) 表达，不再回退——例外是
        ATS 拦截（-1022）这类确定性配置错误，禁用原生路径并回退。
        """
        from .. import ios_background_download as native

        if not native.is_available():
            return None

        client = client or self.nextcloud_client
        username = getattr(client, "username", None)
        password = getattr(client, "password", None)
        url = native.build_webdav_url(getattr(client, "server_url", ""), file_path)
        if not (url and username is not None and password is not None):
            return None

        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers = {"Authorization": f"Basic {credentials}"}

        def _on_progress(downloaded: int, total: int = 0):
            self.download_progress.update(filename, downloaded, total)

        # 先标记再提交：submit 返回后进度回调可能已从系统队列线程到达
        self.download_progress.mark_downloading(filename)
        try:
            fut = native.submit(
                url, headers, local_path, key=filename, on_progress=_on_progress
            )
        except Exception as e:
            logger.warning(f"原生后台下载不可用，回退应用内下载: {e}")
            # 提交失败回退 requests，由回退路径重新标记
            return None

        success, final_path, error = await fut

        if not success:
            logger.error(f"原生后台下载失败 {filename}: {error}")
            if error and "-1022" in error:
                # ATS 拦截明文 HTTP：Info.plist 例外未生效，重试不会好转，
                # 禁用原生路径，本文件及后续下载回退 requests
                native.disable(f"ATS 拦截明文 HTTP (-1022): {error}")
                return None
        return success, final_path, error

    def handle_orphan_native_download(
        self, key: str, success: bool, final_path: Optional[str], error: str = None
    ) -> None:
        """原生后台任务在应用重启后完成时的落库回调（主事件循环线程执行）。

        应用被杀时 nsurlsessiond 继续已提交的任务，系统重启应用交付
        结果；此时原等待协程已不存在，由此处统一落库。
        """
        if success and final_path:
            try:
                self.music_library.mark_song_downloaded(key, final_path)
                logger.info(f"后台遗留下载已落库: {key}")
            except Exception as e:
                logger.error(f"后台遗留下载落库失败 {key}: {e}")
        else:
            logger.error(f"后台遗留下载失败 {key}: {error or '未知原因'}")
        message = (error or "后台下载失败") if not success else ""
        self.download_progress.finish(key, bool(success), message)

    def has_nextcloud_client(self) -> bool:
        """检查是否有NextCloud客户端连接"""
        return bool(self.source_clients) or self.nextcloud_client is not None

    def clear_cache(self):
        """清除缓存"""
        try:
            self.config_manager.clear_cache()
            if self.music_library:
                self.music_library.clear_cache()
            logger.info("缓存已清除")
        except Exception as e:
            logger.error(f"清除缓存失败: {e}")
            raise

    def get_cached_songs(self) -> List[Dict[str, Any]]:
        """获取可在设置页管理的已下载音乐。"""
        if not self.music_library:
            return []
        return self.music_library.get_cached_songs()

    def remove_cached_songs(self, song_names: List[str]) -> tuple[int, int]:
        """选择性删除音乐缓存，保留远端音乐索引。"""
        if not self.music_library:
            return 0, 0
        result = self.music_library.remove_cached_songs(song_names)
        logger.info(f"已清理 {result[0]} 首音乐缓存，释放 {result[1]} 字节")
        return result

    def get_connection_config(self) -> dict:
        """获取连接配置"""
        try:
            return self.config_manager.get_connection_config()
        except Exception as e:
            logger.error(f"获取连接配置失败: {e}")
            return {}
