"""
播放服务层 - 封装音乐播放控制逻辑
提供给视图层使用的抽象接口，解耦视图与应用核心逻辑
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

try:
    import pygame
except ImportError:
    pygame = None

from ..platform_audio import create_audio_player, is_ios, is_mobile

logger = logging.getLogger(__name__)


class PlaybackService:
    """播放服务 - 处理音乐播放控制、播放列表管理等业务逻辑"""

    def __init__(
        self,
        config_manager,
        music_service,
        play_music_callback=None,
        add_background_task_callback=None,
        page=None,
    ):
        """
        初始化播放服务

        Args:
            config_manager: 配置管理器实例
            music_service: 音乐服务实例（用于获取歌曲信息、下载等操作）
            play_music_callback: 播放音乐的回调函数（可选，为兼容性保留）
            add_background_task_callback: 添加后台任务的回调函数
            page: Flet Page 实例（提供时使用 Flet 原生 Audio 播放，移动端必需）
        """
        self.config_manager = config_manager
        self.music_service = music_service
        self._play_music_callback = play_music_callback
        self._add_background_task_callback = add_background_task_callback

        # 创建平台特定的音频播放器
        self.audio_player = create_audio_player(page=page)
        logger.info(f"使用音频播放器: {type(self.audio_player).__name__}")

        # 向后兼容：初始化pygame音频系统（如果需要）
        if not is_mobile():
            self._init_audio_system()

        # 当前播放状态
        self.current_song = None
        self.current_playlist_data = None
        self.current_song_info = None
        self.current_song_state = {
            "is_playing": False,
            "is_paused": False,
            "position": 0,
            "duration": 0,
            "play_count": 0,
            "last_played": None,
        }

        # 播放状态回调
        self._pause_music_callback = None
        self._stop_music_callback = None
        self._get_play_mode_callback = None
        self._get_is_playing_callback = None
        self._set_volume_callback = None
        self._seek_to_position_callback = None
        self._get_duration_callback = None
        self._set_play_mode_callback = None

        self._current_play_mode = None

        # Flet Page（用于创建 Flet Audio 播放器）
        self._page = page
        self._play_lock = asyncio.Lock()

    def _init_audio_system(self):
        """初始化音频系统"""
        try:
            if pygame is None:
                logger.warning("pygame 未安装，音频播放功能不可用")
                return

            # 初始化pygame mixer
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
                logger.info("pygame音频系统初始化成功")
            else:
                logger.info("pygame音频系统已经初始化")
        except Exception as e:
            logger.error(f"初始化音频系统失败: {e}")
            # 尝试重新初始化
            try:
                pygame.mixer.quit()
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
                logger.info("重新初始化pygame音频系统成功")
            except Exception as retry_error:
                logger.error(f"重新初始化音频系统也失败: {retry_error}")

    def _ensure_audio_system(self) -> bool:
        """确保音频系统可用"""
        # 优先使用新的平台音频播放器
        if self.audio_player:
            return True

        # 向后兼容：检查pygame（仅桌面平台）
        if is_mobile():
            logger.error("移动平台上pygame不可用")
            return False

        if pygame is None:
            logger.error("pygame 未安装，无法播放音频")
            return False

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
                logger.info("重新初始化pygame音频系统")
        except Exception as e:
            logger.error(f"确保音频系统可用失败: {e}")
            return False

        return True

    def update_music_service(self, music_service):
        """更新音乐服务实例"""
        self.music_service = music_service
        logger.info("播放服务: 音乐服务已更新")

    def set_playback_callbacks(
        self,
        pause_callback,
        stop_callback,
        get_play_mode_callback,
        get_is_playing_callback,
        set_volume_callback=None,
        seek_to_position_callback=None,
        get_duration_callback=None,
        set_play_mode_callback=None,
    ):
        """设置播放控制回调函数"""
        self._pause_music_callback = pause_callback
        self._stop_music_callback = stop_callback
        self._get_play_mode_callback = get_play_mode_callback
        self._get_is_playing_callback = get_is_playing_callback
        self._set_volume_callback = set_volume_callback
        self._seek_to_position_callback = seek_to_position_callback
        self._get_duration_callback = get_duration_callback
        self._set_play_mode_callback = set_play_mode_callback

    def load_playlists(self) -> Dict[str, Any]:
        """加载播放列表数据"""
        return self.config_manager.load_playlists()

    def save_playlists(self, playlists_data: Dict[str, Any]):
        """保存播放列表数据"""
        self.config_manager.save_playlists(playlists_data)

    def get_playlist_by_id(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        """根据ID获取播放列表"""
        return self.config_manager.get_playlist_by_id(playlist_id)

    def get_song_info(self, song_name: str) -> Optional[Dict[str, Any]]:
        """获取歌曲信息"""
        if self.music_service:
            return self.music_service.get_song_info(song_name)
        return None

    def get_volume(self) -> int:
        """获取音量设置"""
        return self.config_manager.get("player.volume", 70)

    def set_volume(self, volume: int):
        """设置音量"""
        self.config_manager.set("player.volume", volume)
        self.config_manager.save_config()

    def set_current_song(self, song_path: str):
        """设置当前播放歌曲"""
        # 确保路径是字符串格式，处理Path对象
        if hasattr(song_path, "__fspath__"):
            self.current_song = os.fspath(song_path)
        else:
            self.current_song = str(song_path)

    async def _play_audio_player(self) -> bool:
        """播放并等待原生命令完成（Flet/iOS 不可使用 fire-and-forget）。"""
        play_async = getattr(self.audio_player, "play_async", None)
        if play_async is not None:
            return await play_async()
        return self.audio_player.play()

    def get_current_song(self) -> Optional[str]:
        """获取当前播放歌曲路径"""
        return self.current_song

    def get_current_song_name(self) -> Optional[str]:
        """获取当前播放歌曲名称"""
        if self.current_song:
            return os.path.basename(self.current_song)
        return None

    async def play_music(self):
        """串行执行播放请求，避免多个请求互相销毁播放器。"""
        async with self._play_lock:
            return await self._play_music_locked()

    async def _play_music_locked(self) -> bool:
        """播放音乐"""
        try:
            if not self.current_song or not os.path.exists(self.current_song):
                logger.error(f"当前歌曲文件不存在: {self.current_song}")
                return False

            # 使用新的平台音频播放器
            if self.audio_player:
                # 如果当前暂停状态，恢复播放
                if self.current_song_state["is_paused"]:
                    success = self.audio_player.play()
                    if success:
                        self.current_song_state["is_paused"] = False
                        self.current_song_state["is_playing"] = True
                        logger.info("恢复播放音乐")
                    return

                logger.info(f"准备播放音乐: {self.current_song}")

                # 为了确保单曲循环正确工作，总是重新加载文件
                # 特别是在播放完成后需要重新开始的情况下
                try:
                    # 先停止当前播放（如果有的话）
                    if (
                        self.current_song_state["is_playing"]
                        or self.current_song_state["is_paused"]
                    ):
                        logger.info("停止当前播放，准备重新开始")
                        stop_async = getattr(self.audio_player, "stop_async", None)
                        if stop_async is not None:
                            await stop_async()
                        else:
                            self.audio_player.stop()
                            await asyncio.sleep(0.1)
                        # 重置播放状态
                        self.current_song_state["is_playing"] = False
                        self.current_song_state["is_paused"] = False

                    # 重新加载文件
                    logger.info("重新加载音频文件")
                    load_success = self.audio_player.load(self.current_song)
                    logger.info(f"音频文件加载结果: {load_success}")

                    if load_success:
                        # 开始播放
                        logger.info("开始播放音频")
                        play_success = await self._play_audio_player()
                        logger.info(f"音频播放结果: {play_success}")

                        if play_success:
                            # 更新播放状态
                            self.current_song_state["is_playing"] = True
                            self.current_song_state["is_paused"] = False
                            self.current_song_state["last_played"] = (
                                datetime.now().isoformat()
                            )
                            logger.info("播放状态已更新")

                            # 获取并缓存音频时长
                            try:
                                duration = self.audio_player.get_duration()
                                if duration > 0:
                                    self.current_song_state["duration"] = duration
                                    logger.info(f"缓存音频时长: {duration:.2f}秒")
                                else:
                                    logger.debug("无法从播放器获取时长，保持原有值")
                            except Exception as e:
                                logger.warning(f"获取音频时长失败: {e}")

                            # 设置音量
                            volume = self.get_volume() / 100.0  # 转换为0.0-1.0范围
                            self.audio_player.set_volume(volume)

                            logger.info(
                                f"开始播放: {os.path.basename(self.current_song)}"
                            )
                            return True
                        else:
                            logger.error("音频播放器播放失败")
                    else:
                        logger.error("音频播放器加载文件失败")
                except Exception as audio_error:
                    logger.error(f"音频播放过程出现异常: {audio_error}")

                # 如果新的播放器失败，尝试重新初始化播放器
                logger.warning("尝试重新初始化音频播放器")
                try:
                    from ..platform_audio import create_audio_player

                    old_player = self.audio_player
                    dispose = getattr(old_player, "dispose", None)
                    if dispose is not None:
                        dispose()
                    self.audio_player = create_audio_player(page=self._page)

                    if self.audio_player and self.audio_player.load(self.current_song):
                        if await self._play_audio_player():
                            self.current_song_state["is_playing"] = True
                            self.current_song_state["is_paused"] = False
                            self.current_song_state["last_played"] = (
                                datetime.now().isoformat()
                            )
                            logger.info("重新初始化播放器后播放成功")
                            return True
                except Exception as reinit_error:
                    logger.error(f"重新初始化播放器失败: {reinit_error}")

                # Flet 是移动端唯一播放器，失败后不能落入 pygame 分支。
                if is_mobile():
                    return False

            # 备用方案：使用pygame（向后兼容）
            if not self._ensure_audio_system():
                logger.error("音频系统不可用")
                return

            # 如果当前暂停状态，恢复播放
            if self.current_song_state["is_paused"]:
                pygame.mixer.music.unpause()
                self.current_song_state["is_paused"] = False
                self.current_song_state["is_playing"] = True
                logger.info("恢复播放音乐")
                return

            logger.info(f"准备播放音乐: {self.current_song}")
            # 加载并播放新歌曲
            pygame.mixer.music.load(self.current_song)
            pygame.mixer.music.play()

            # 更新播放状态
            self.current_song_state["is_playing"] = True
            self.current_song_state["is_paused"] = False
            self.current_song_state["last_played"] = datetime.now().isoformat()

            logger.info(f"开始播放: {os.path.basename(self.current_song)}")
            return True

        except Exception as e:
            logger.error(f"播放音乐失败: {e}")
            # 如果内置播放失败，尝试使用回调
            if self._play_music_callback:
                try:
                    await self._play_music_callback()
                except Exception as callback_error:
                    logger.error(f"回调播放也失败: {callback_error}")
            return False

    def add_background_task(self, task):
        """添加后台任务"""
        if self._add_background_task_callback:
            self._add_background_task_callback(task)

    def update_playlist_current_song(self, song_name: str):
        """更新播放列表中的当前歌曲状态"""
        try:
            playlists_data = self.load_playlists()
            current_playlist_id = playlists_data.get("current_playlist")

            if current_playlist_id:
                for playlist in playlists_data.get("playlists", []):
                    if playlist.get("id") == current_playlist_id:
                        # 更新当前歌曲
                        playlist["current_song"] = song_name
                        playlist["last_played"] = datetime.now().isoformat()

                        # 更新播放统计
                        for song in playlist.get("songs", []):
                            if song.get("name") == song_name:
                                song["play_count"] = song.get("play_count", 0) + 1
                                song["last_played"] = datetime.now().isoformat()
                                break

                        break

                self.save_playlists(playlists_data)
                logger.info(f"已更新播放列表中的当前歌曲: {song_name}")
        except Exception as e:
            logger.error(f"更新播放列表当前歌曲失败: {e}")

    def is_nextcloud_connected(self) -> bool:
        """检查NextCloud是否连接"""
        return self.music_service and self.music_service.has_nextcloud_client()

    async def download_and_play_song(self, song_name: str, remote_path: str) -> bool:
        """下载并播放歌曲"""
        try:
            if not self.is_nextcloud_connected():
                logger.error("NextCloud客户端未连接")
                return False

            logger.info(f"正在下载歌曲: {song_name}")
            success = await self.music_service.download_file(remote_path, song_name)

            if success:
                # 获取更新后的歌曲信息
                updated_info = self.music_service.get_song_info(song_name)

                # 尝试多种方法获取本地文件路径
                local_path = None

                # 方法1: 从歌曲信息中获取filepath
                if updated_info and updated_info.get("filepath"):
                    potential_path = updated_info["filepath"]
                    if os.path.exists(potential_path):
                        local_path = potential_path
                        logger.info(f"使用歌曲信息中的路径: {local_path}")

                if local_path:
                    # 设置为当前歌曲并播放
                    self.set_current_song(local_path)
                    await self.play_music()
                    logger.info(f"歌曲下载并播放成功: {song_name}")
                    return True
                else:
                    logger.error(f"找不到下载的歌曲文件: {song_name}")
                    return False
            else:
                logger.error(f"歌曲下载失败: {song_name}")
                return False

        except Exception as e:
            logger.error(f"下载并播放歌曲失败: {e}")
            return False

    async def play_song_from_playlist(self, song_name: str) -> bool:
        """从播放列表播放歌曲"""
        try:
            logger.info(f"准备播放歌曲: {song_name}")

            # 获取歌曲信息
            song_info = self.get_song_info(song_name)
            if not song_info:
                logger.error(f"未找到歌曲信息: {song_name}")
                return False

            # 检查是否已下载
            if song_info.get("downloaded", False):
                # 本地播放
                filepath = song_info.get("local_path", "")
                if os.path.exists(filepath):
                    self.set_current_song(filepath)
                    await self.play_music()
                    logger.info(f"本地播放歌曲: {song_name}")
                    return True
                else:
                    logger.warning(f"本地文件不存在: {filepath}")

            # 在线播放 - 需要下载
            remote_path = song_info.get("remote_path", "")
            if remote_path:
                return await self.download_and_play_song(song_name, remote_path)
            else:
                logger.error(f"歌曲缺少远程路径信息: {song_name}")
                return False

        except Exception as e:
            logger.error(f"播放歌曲失败: {e}")
            return False

    def load_current_playlist(self) -> Optional[Dict[str, Any]]:
        """加载当前播放列表"""
        try:
            playlists_data = self.load_playlists()
            current_playlist_id = playlists_data.get("current_playlist")

            if current_playlist_id:
                self.current_playlist_data = self.get_playlist_by_id(
                    current_playlist_id
                )
                return self.current_playlist_data
            return None
        except Exception as e:
            logger.error(f"加载当前播放列表失败: {e}")
            return None

    def get_current_playlist_song_info(
        self, song_name: str
    ) -> Optional[Dict[str, Any]]:
        """获取当前播放列表中歌曲的详细信息"""
        try:
            # 同时从音乐服务和播放列表获取信息
            song_info = {}

            # 从音乐服务获取基础信息
            if self.music_service:
                library_info = self.music_service.get_song_info(song_name)
                if library_info:
                    song_info.update(library_info)

            # 从当前播放列表获取播放统计等信息
            if self.current_playlist_data:
                for song in self.current_playlist_data.get("songs", []):
                    if song.get("name") == song_name:
                        song_info.update(song)
                        break

            return song_info if song_info else None

        except Exception as e:
            logger.error(f"获取播放列表歌曲信息失败: {e}")
            return None

    def sync_current_song_with_app(self) -> Optional[str]:
        """与应用当前歌曲状态同步"""
        if self.current_song:
            return os.path.basename(self.current_song)
        return None

    async def pause_music(self):
        """暂停音乐"""
        try:
            # 使用新的平台音频播放器
            if (
                self.audio_player
                and self.current_song_state["is_playing"]
                and not self.current_song_state["is_paused"]
            ):
                pause_async = getattr(self.audio_player, "pause_async", None)
                paused = (
                    await pause_async()
                    if pause_async is not None
                    else self.audio_player.pause()
                )
                if paused:
                    self.current_song_state["is_paused"] = True
                    self.current_song_state["is_playing"] = False
                    logger.info("音乐已暂停")
                    return
                else:
                    logger.error("音频播放器暂停失败")

            # 备用方案：使用pygame（向后兼容）
            if not self._ensure_audio_system():
                return

            if (
                self.current_song_state["is_playing"]
                and not self.current_song_state["is_paused"]
            ):
                pygame.mixer.music.pause()
                self.current_song_state["is_paused"] = True
                self.current_song_state["is_playing"] = False
                logger.info("音乐已暂停")
        except Exception as e:
            logger.error(f"暂停音乐失败: {e}")
            # 如果内置暂停失败，尝试使用回调
            if self._pause_music_callback:
                try:
                    await self._pause_music_callback()
                except Exception as callback_error:
                    logger.error(f"回调暂停也失败: {callback_error}")

    async def stop_music(self):
        """停止音乐"""
        try:
            # 只要播放器存在就发送停止命令。原生状态事件可能有延迟，
            # 不能因内部 is_playing 暂时为 False 而吞掉用户的停止操作。
            if self.audio_player:
                stop_async = getattr(self.audio_player, "stop_async", None)
                stopped = (
                    await stop_async()
                    if stop_async is not None
                    else self.audio_player.stop()
                )
                if stopped:
                    self.current_song_state["is_playing"] = False
                    self.current_song_state["is_paused"] = False
                    self.current_song_state["position"] = 0
                    logger.info("音乐已停止")
                    return True
                else:
                    logger.error("音频播放器停止失败")

                # 已有平台播放器时不应再落入 pygame，尤其移动端 pygame 不可用。
                return False

            # 备用方案：使用pygame（向后兼容）
            if not self._ensure_audio_system():
                return

            if (
                self.current_song_state["is_playing"]
                or self.current_song_state["is_paused"]
            ):
                pygame.mixer.music.stop()
                self.current_song_state["is_playing"] = False
                self.current_song_state["is_paused"] = False
                self.current_song_state["position"] = 0
                logger.info("音乐已停止")
                return True
            return False
        except Exception as e:
            logger.error(f"停止音乐失败: {e}")
            # 如果内置停止失败，尝试使用回调
            if self._stop_music_callback:
                try:
                    await self._stop_music_callback()
                except Exception as callback_error:
                    logger.error(f"回调停止也失败: {callback_error}")
            return False

    def get_play_mode(self):
        """获取播放模式"""
        # 优先返回内部存储的播放模式
        if self._current_play_mode:
            return self._current_play_mode

        # 备用：使用回调函数
        if self._get_play_mode_callback:
            return self._get_play_mode_callback()

        # 从配置文件加载
        try:
            from .playback_controller import PlayMode

            mode_string = self.config_manager.get("player.play_mode", "repeat_one")
            mode_map = {
                "normal": PlayMode.NORMAL,
                "repeat_one": PlayMode.REPEAT_ONE,
                "repeat_all": PlayMode.REPEAT_ALL,
                "shuffle": PlayMode.SHUFFLE,
            }
            self._current_play_mode = mode_map.get(mode_string, PlayMode.REPEAT_ONE)
            logger.debug(f"从配置加载播放模式: {mode_string}")
            return self._current_play_mode
        except Exception as e:
            logger.error(f"获取播放模式失败: {e}")
            return None

    def is_playing(self) -> bool:
        """检查是否正在播放"""
        try:
            # 优先使用新的平台音频播放器
            if self.audio_player:
                is_audio_playing = self.audio_player.is_playing()
                # 同步内部状态
                if is_audio_playing != self.current_song_state["is_playing"]:
                    self.current_song_state["is_playing"] = is_audio_playing
                    if is_audio_playing:
                        self.current_song_state["is_paused"] = False
                return is_audio_playing

            # 备用方案：使用内部状态和pygame验证
            if self.current_song_state["is_playing"]:
                # 如果pygame可用，验证实际播放状态
                if pygame and pygame.mixer.get_init():
                    pygame_playing = pygame.mixer.music.get_busy()
                    if not pygame_playing and not self.current_song_state["is_paused"]:
                        # pygame显示未播放且未暂停，更新状态
                        self.current_song_state["is_playing"] = False
                        return False
                return True

            # 如果内部状态显示未播放，检查pygame状态作为备用
            if pygame and pygame.mixer.get_init():
                return pygame.mixer.music.get_busy()

            return False
        except Exception as e:
            logger.error(f"检查播放状态失败: {e}")
            # 如果检查失败，尝试使用回调
            if self._get_is_playing_callback:
                try:
                    return self._get_is_playing_callback()
                except Exception as callback_error:
                    logger.error(f"回调检查播放状态也失败: {callback_error}")
            return False

    def has_completed(self) -> bool:
        """查询并消费播放器上报的自然播放结束事件。"""
        if not self.audio_player:
            return False
        callback = getattr(self.audio_player, "has_completed", None)
        if callback is None:
            return False
        try:
            completed = bool(callback())
            if completed:
                self.current_song_state["is_playing"] = False
                self.current_song_state["is_paused"] = False
            return completed
        except Exception as e:
            logger.warning(f"读取播放完成状态失败: {e}")
            return False

    def set_audio_volume(self, volume: float):
        """设置音频音量（0.0-1.0）"""
        try:
            # 使用新的平台音频播放器
            if self.audio_player:
                success = self.audio_player.set_volume(volume)
                if success:
                    logger.info(f"音量设置成功: {volume}")
                    return
                else:
                    logger.warning("音频播放器设置音量失败")

            # 备用方案：使用回调
            if self._set_volume_callback:
                self._set_volume_callback(volume)
            else:
                logger.warning("没有可用的音量设置方法")

        except Exception as e:
            logger.error(f"设置音量失败: {e}")

    def seek_to_position(self, position: float):
        """跳转到指定位置"""
        try:
            # 优先使用音频播放器的跳转功能
            if self.audio_player:
                success = self.audio_player.seek(position)
                if success:
                    logger.info(f"跳转到位置: {position:.2f}秒")
                    return True
                else:
                    logger.warning("音频播放器不支持跳转功能")

            # 备用：使用回调函数
            if self._seek_to_position_callback:
                self._seek_to_position_callback(position)
                return True

            logger.warning("无法跳转到指定位置：音频播放器和回调函数都不可用")
            return False
        except Exception as e:
            logger.error(f"跳转到指定位置失败: {e}")
            return False

    def get_duration(self) -> float:
        """获取音频总时长"""
        try:
            # 优先使用音频播放器的获取时长功能
            if self.audio_player:
                duration = self.audio_player.get_duration()
                logger.debug(f"从播放器获取时长: {duration}")
                if duration > 0:  # iOS现在返回0而不是-1表示无效
                    return duration

            # 备用：使用回调函数
            if self._get_duration_callback:
                return self._get_duration_callback()

            # 返回存储的时长
            cached_duration = self.current_song_state.get("duration", 0.0)
            logger.debug(f"使用缓存时长: {cached_duration}")
            return cached_duration
        except Exception as e:
            logger.error(f"获取音频时长失败: {e}")
            return 0.0

    def set_play_mode(self, play_mode):
        """设置播放模式"""
        # 存储到内部状态
        self._current_play_mode = play_mode
        logger.info(f"设置播放模式: {play_mode.value if play_mode else None}")

        # 调用回调函数（如果有）
        if self._set_play_mode_callback:
            self._set_play_mode_callback(play_mode)

    def set_play_mode_by_string(self, mode_string: str):
        """通过字符串设置播放模式"""
        # 导入播放模式枚举
        try:
            from .playback_controller import PlayMode

            # 模式映射
            mode_map = {
                "normal": PlayMode.NORMAL,
                "repeat_one": PlayMode.REPEAT_ONE,
                "repeat_all": PlayMode.REPEAT_ALL,
                "shuffle": PlayMode.SHUFFLE,
            }

            if mode_string in mode_map:
                self.set_play_mode(mode_map[mode_string])

                # 保存播放模式到配置
                self.config_manager.set("player.play_mode", mode_string)
                self.config_manager.save_config()

                return True
            return False
        except Exception as e:
            logger.error(f"设置播放模式失败: {e}")
            return False

    def get_playback_state(self) -> Dict[str, Any]:
        """获取实时播放状态"""
        try:
            # 从音频播放器获取实时状态
            if self.audio_player:
                position = self.audio_player.get_position()
                duration = self.audio_player.get_duration()
                is_playing = self.audio_player.is_playing()

                # 如果返回的值为负数（表示不支持），使用存储的状态
                if position < 0:
                    position = self.current_song_state.get("position", 0)
                if duration < 0:
                    duration = self.current_song_state.get("duration", 0)

                # 更新存储的状态
                self.current_song_state["position"] = position
                self.current_song_state["duration"] = duration
                self.current_song_state["is_playing"] = is_playing

                return {
                    "position": position,
                    "duration": duration,
                    "is_playing": is_playing,
                    "is_paused": self.current_song_state.get("is_paused", False),
                    "current_song": self.current_song,
                    "play_count": self.current_song_state.get("play_count", 0),
                    "last_played": self.current_song_state.get("last_played", None),
                }
            else:
                # 返回存储的状态
                return dict(self.current_song_state)

        except Exception as e:
            logger.error(f"获取播放状态失败: {e}")
            return dict(self.current_song_state)
