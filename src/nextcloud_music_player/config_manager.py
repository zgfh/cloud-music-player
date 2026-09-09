"""
配置管理器 - 负责保存和加载用户配置和数据
"""

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _serialize_for_json(obj):
    """将对象序列化为JSON兼容格式"""
    if isinstance(obj, Path):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    else:
        return obj


class ConfigManager:
    """配置管理器，负责持久化配置和数据"""

    def __init__(self, app_name: str = "nextcloud_music_player"):
        self.app_name = app_name
        self.config_dir = self._get_config_directory()
        self.config_file = self.config_dir / "config.json"

        # 确保配置目录存在，处理权限问题
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"配置目录: {self.config_dir}")
        except (PermissionError, OSError) as e:
            logger.warning(f"无法创建配置目录 {self.config_dir}: {e}")
            # 如果无法创建配置目录，使用临时目录
            import sys
            import tempfile

            if sys.platform == "ios" or "iOS" in str(sys.platform):
                # iOS特殊处理：使用相对路径
                self.config_dir = Path(".") / f"{self.app_name}_config"
                try:
                    self.config_dir.mkdir(parents=True, exist_ok=True)
                    self.config_file = self.config_dir / "config.json"
                    logger.info(f"iOS备用配置目录: {self.config_dir}")
                except Exception as e2:
                    logger.warning(f"iOS备用方案也失败: {e2}")
                    # 最后的备用方案：使用当前目录的配置文件
                    self.config_dir = Path(".")
                    self.config_file = Path(f"{self.app_name}_config.json")
                    logger.info(f"最终备用配置文件: {self.config_file}")
            else:
                # 桌面平台使用临时目录
                self.config_dir = (
                    Path(tempfile.gettempdir()) / f"{self.app_name}_config"
                )
                try:
                    self.config_dir.mkdir(parents=True, exist_ok=True)
                    self.config_file = self.config_dir / "config.json"
                    logger.info(f"临时配置目录: {self.config_dir}")
                except:
                    # 最后的后备方案：使用当前目录
                    self.config_dir = Path(".")
                    self.config_file = Path(f"{self.app_name}_config.json")
                    logger.info(f"当前目录配置文件: {self.config_file}")

        # 默认配置
        self.default_config = {
            "connection": {
                # 音乐来源类型：nextcloud | smb | gdrive
                "source_type": "nextcloud",
                "server_url": "http://cloud.home.daozzg.com",
                "username": "guest",
                "password": "",
                "default_sync_folder": "/mp3/音乐/当月抖音热播流行歌曲484首/",
                "sync_folders": ["/mp3/音乐/当月抖音热播流行歌曲484首/"],
                "auto_connect": False,
                "nextcloud": {"auto_connect": False},
                "remember_credentials": True,
                # SMB 来源配置（路径均为共享内相对路径，'/' 为共享根）
                "smb": {
                    "auto_connect": False,
                    "host": "",
                    "port": 445,
                    "share": "",
                    "username": "",
                    "password": "",
                    "domain": "",
                    "default_sync_folder": "/",
                    "sync_folders": ["/"],
                },
                # Google Drive 来源配置（default_sync_folder 为 Drive 文件夹 ID，空为根目录）
                "gdrive": {
                    "auto_connect": False,
                    "client_id": "",
                    "client_secret": "",
                    "refresh_token": "",
                    "access_token": "",
                    "token_expiry": 0,
                    "default_sync_folder": "",
                    "sync_folders": [],
                },
            },
            "player": {
                "volume": 70,
                "play_mode": "repeat_one",
                "auto_play_on_select": True,
                "sleep_timer_minutes": 30,
            },
            "metadata": {
                "musicbrainz_enabled": True,
            },
            "app": {
                "last_view": "playback",
                "window_size": [800, 600],
                "cache_max_size": 1073741824,  # 1GB
            },
        }

        # 加载配置
        self.config = self.load_config()

    def get_config_directory(self) -> Path:
        """获取配置目录路径"""
        return self.config_dir

    def _get_config_directory(self) -> Path:
        """获取配置目录路径"""
        # 检查是否在iOS环境中运行
        try:
            import sys

            # 检测iOS环境
            if sys.platform == "ios" or "iOS" in str(sys.platform):
                # 尝试使用框架提供的路径（Toga 或 Flet）
                try:
                    try:
                        import toga

                        if (
                            hasattr(toga.App, "app")
                            and toga.App.app
                            and hasattr(toga.App.app, "paths")
                        ):
                            if hasattr(toga.App.app.paths, "data"):
                                config_dir = toga.App.app.paths.data / self.app_name
                                return config_dir
                            elif hasattr(toga.App.app.paths, "app"):
                                config_dir = (
                                    toga.App.app.paths.app / "Documents" / self.app_name
                                )
                                return config_dir
                    except ImportError:
                        pass
                except Exception as e:
                    logger.warning(f"无法获取框架路径: {e}")

                # iOS备用方案：使用相对路径
                try:
                    # 直接使用当前应用目录下的Documents文件夹
                    current_dir = Path.cwd()
                    config_dir = current_dir / "Documents" / self.app_name
                    return config_dir
                except Exception as e:
                    logger.warning(f"iOS备用方案失败: {e}")
                    # 最终的iOS备用方案
                    return Path(".") / self.app_name

            # 尝试使用框架的应用路径（适用于桌面平台）
            try:
                import toga

                if (
                    hasattr(toga.App, "app")
                    and toga.App.app
                    and hasattr(toga.App.app, "paths")
                ):
                    try:
                        if hasattr(toga.App.app.paths, "data"):
                            app_data_dir = toga.App.app.paths.data
                            config_dir = app_data_dir / self.app_name
                            return config_dir
                    except:
                        pass
            except ImportError:
                pass

        except (ImportError, AttributeError):
            pass

        # 传统平台的目录选择
        if os.name == "nt":  # Windows
            config_dir = Path(os.environ.get("APPDATA", "")) / self.app_name
        elif os.name == "posix":  # macOS/Linux
            if "darwin" in os.sys.platform:  # macOS
                config_dir = (
                    Path.home() / "Library" / "Application Support" / self.app_name
                )
            else:  # Linux
                config_dir = Path.home() / ".config" / self.app_name
        else:
            config_dir = Path.home() / f".{self.app_name}"

        return config_dir

    def get_log_directory(self) -> Path:
        """获取日志目录路径"""
        try:
            # 日志目录通常与配置目录在同一级别
            log_dir = self.config_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir
        except (PermissionError, OSError) as e:
            # 如果无法创建日志目录，使用临时目录
            import tempfile

            temp_log_dir = Path(tempfile.gettempdir()) / f"{self.app_name}_logs"
            try:
                temp_log_dir.mkdir(parents=True, exist_ok=True)
                return temp_log_dir
            except:
                # 最后的后备方案：返回当前目录
                return Path(".")

    def get_cache_directory(self) -> Path:
        """获取缓存目录路径 - 用于存储下载的音乐文件

        ⚠️ iOS重要修改：现在使用Documents目录实现音乐文件持久化
        这样可以确保iOS应用升级时音乐文件不会丢失
        """
        try:
            import sys

            # 🎵 iOS环境中使用Documents/music目录（持久化存储）
            if sys.platform == "ios" or "iOS" in str(sys.platform):
                try:
                    try:
                        import toga

                        if (
                            hasattr(toga.App, "app")
                            and toga.App.app
                            and hasattr(toga.App.app, "paths")
                        ):
                            if hasattr(toga.App.app.paths, "data"):
                                music_dir = (
                                    toga.App.app.paths.data / self.app_name / "music"
                                )
                                music_dir.mkdir(parents=True, exist_ok=True)
                                logger.info(
                                    f"🎵 iOS音乐存储目录（data持久化）: {music_dir}"
                                )
                                return music_dir
                            elif hasattr(toga.App.app.paths, "app"):
                                music_dir = (
                                    toga.App.app.paths.app
                                    / "Documents"
                                    / self.app_name
                                    / "music"
                                )
                                music_dir.mkdir(parents=True, exist_ok=True)
                                logger.info(
                                    f"🎵 iOS音乐存储目录（Documents持久化）: {music_dir}"
                                )
                                return music_dir
                    except ImportError:
                        pass
                except Exception as e:
                    logger.warning(f"无法获取iOS持久化音乐路径: {e}")

                # iOS备用方案：使用相对路径下的Documents/music目录
                try:
                    current_dir = Path.cwd()
                    music_dir = current_dir / "Documents" / self.app_name / "music"
                    music_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(f"🎵 iOS音乐存储备用目录: {music_dir}")
                    return music_dir
                except Exception as e:
                    logger.warning(f"iOS音乐存储备用方案失败: {e}")
                    # 最终备用方案
                    music_dir = Path(".") / f"{self.app_name}_music"
                    try:
                        music_dir.mkdir(parents=True, exist_ok=True)
                        logger.info(f"🎵 iOS音乐存储最终备用目录: {music_dir}")
                        return music_dir
                    except:
                        return Path(".")

            # 桌面平台保持原有缓存逻辑
            if os.name == "nt":  # Windows
                cache_dir = (
                    Path(os.environ.get("LOCALAPPDATA", "")) / self.app_name / "Cache"
                )
            elif os.name == "posix":  # macOS/Linux
                if "darwin" in sys.platform:  # macOS
                    cache_dir = Path.home() / "Library" / "Caches" / self.app_name
                else:  # Linux
                    cache_dir = Path.home() / ".cache" / self.app_name
            else:
                cache_dir = self.config_dir / "cache"

            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir

        except Exception as e:
            logger.warning(f"无法创建缓存目录，使用临时目录: {e}")
            # 备用方案：使用临时目录
            import tempfile

            temp_cache_dir = Path(tempfile.gettempdir()) / f"{self.app_name}_cache"
            try:
                temp_cache_dir.mkdir(parents=True, exist_ok=True)
                return temp_cache_dir
            except:
                return Path(".")

    def get_documents_directory(self) -> Path:
        """获取文档目录路径 - 用于存储用户数据（播放列表、用户设置等）"""
        try:
            import sys

            # iOS环境中使用Documents目录
            if sys.platform == "ios" or "iOS" in str(sys.platform):
                try:
                    # 备用方案：使用框架路径
                    try:
                        import toga

                        if (
                            hasattr(toga.App, "app")
                            and toga.App.app
                            and hasattr(toga.App.app, "paths")
                        ):
                            if hasattr(toga.App.app.paths, "data"):
                                docs_dir = toga.App.app.paths.data / self.app_name
                                docs_dir.mkdir(parents=True, exist_ok=True)
                                return docs_dir
                    except ImportError:
                        pass
                except:
                    pass

            # 桌面平台的文档目录
            if os.name == "nt":  # Windows
                docs_dir = Path.home() / "Documents" / self.app_name
            elif os.name == "posix":  # macOS/Linux
                docs_dir = Path.home() / "Documents" / self.app_name
            else:
                docs_dir = self.config_dir / "documents"

            docs_dir.mkdir(parents=True, exist_ok=True)
            return docs_dir

        except Exception as e:
            logger.warning(f"无法创建文档目录，使用配置目录: {e}")
            # 备用方案：使用配置目录
            docs_dir = self.config_dir / "documents"
            try:
                docs_dir.mkdir(parents=True, exist_ok=True)
                return docs_dir
            except:
                return self.config_dir

    def get_temp_directory(self) -> Path:
        """获取临时目录路径 - 用于临时文件和正在下载的文件"""
        try:
            import sys
            import tempfile

            # iOS环境中使用tmp目录
            if sys.platform == "ios" or "iOS" in str(sys.platform):
                temp_dir = Path(tempfile.gettempdir()) / self.app_name
            else:
                temp_dir = Path(tempfile.gettempdir()) / self.app_name

            temp_dir.mkdir(parents=True, exist_ok=True)
            return temp_dir

        except Exception as e:
            logger.warning(f"无法创建临时目录: {e}")
            return Path(".")

    def load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)

                # 合并默认配置和加载的配置
                config = copy.deepcopy(self.default_config)
                self._deep_merge(config, loaded_config)
                logger.info(f"配置文件已加载: {self.config_file}")
                return config
            else:
                logger.info("配置文件不存在，使用默认配置")
                return copy.deepcopy(self.default_config)

        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return copy.deepcopy(self.default_config)

    def save_config(self) -> bool:
        """保存配置文件"""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            logger.info(f"配置文件已保存: {self.config_file}")
            return True
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False

    def get(self, key_path: str, default=None) -> Any:
        """获取配置值，支持点号路径如 'connection.server_url'"""
        keys = key_path.split(".")
        value = self.config

        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key_path: str, value: Any) -> None:
        """设置配置值，支持点号路径"""
        keys = key_path.split(".")
        config = self.config

        # 导航到倒数第二层
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]

        # 设置最后一层的值
        config[keys[-1]] = value

    def get_connection_config(self) -> Dict[str, str]:
        """获取连接配置"""
        return self.config.get("connection", {})

    def save_connection_config(
        self,
        server_url: str,
        username: str,
        password: str,
        sync_folder: str = "",
        remember: bool = True,
    ) -> None:
        """保存连接配置"""
        connection_config = {
            "server_url": server_url,
            "username": username,
            "default_sync_folder": sync_folder,
            "remember_credentials": remember,
        }

        # 只有在选择记住凭据时才保存密码
        if remember:
            connection_config["password"] = password
        else:
            connection_config["password"] = ""

        self.config["connection"].update(connection_config)
        self.save_config()

    def get_player_config(self) -> Dict[str, Any]:
        """获取播放器配置"""
        return self.config.get("player", {})

    def save_player_config(
        self, volume: int = None, play_mode: str = None, auto_play: bool = None
    ) -> None:
        """保存播放器配置"""
        player_config = {}
        if volume is not None:
            player_config["volume"] = volume
        if play_mode is not None:
            player_config["play_mode"] = play_mode
        if auto_play is not None:
            player_config["auto_play_on_select"] = auto_play

        self.config["player"].update(player_config)
        self.save_config()

    def load_playlists(self) -> Dict[str, Any]:
        """加载播放列表缓存"""
        playlist_file = self.config_dir / "playlists.json"

        try:
            if playlist_file.exists():
                with open(playlist_file, "r", encoding="utf-8") as f:
                    playlists_data = json.load(f)
                logger.info("播放列表缓存已加载")
                return playlists_data
        except json.JSONDecodeError as e:
            logger.error(f"播放列表缓存文件损坏: {e}")
            # 备份损坏的文件
            try:
                backup_file = playlist_file.with_suffix(".json.backup")
                playlist_file.rename(backup_file)
                logger.info(f"已备份损坏的播放列表文件到: {backup_file}")
            except Exception as backup_error:
                logger.error(f"备份损坏文件失败: {backup_error}")
        except Exception as e:
            logger.error(f"加载播放列表缓存失败: {e}")

        # 返回默认结构
        default_data = {"playlists": [], "current_playlist_id": None, "next_id": 1}

        # 尝试保存默认结构到新文件
        try:
            self.save_playlists(default_data)
        except Exception as e:
            logger.error(f"保存默认播放列表失败: {e}")

        return default_data

    def save_playlists(self, playlists_data: Dict[str, Any]) -> None:
        """保存播放列表缓存"""
        try:
            from datetime import datetime

            # 更新保存时间
            playlists_data["last_updated"] = datetime.now().isoformat()

            # 序列化所有Path对象
            serialized_data = _serialize_for_json(playlists_data)

            playlist_file = self.config_dir / "playlists.json"
            with open(playlist_file, "w", encoding="utf-8") as f:
                json.dump(serialized_data, f, indent=2, ensure_ascii=False)
            logger.info("播放列表缓存已保存")
        except Exception as e:
            logger.error(f"保存播放列表缓存失败: {e}")

    def add_playlist(self, name: str, songs: list, folder_path: str = "") -> int:
        """添加新的播放列表"""
        try:
            from datetime import datetime

            playlists_data = self.load_playlists()

            # 创建新播放列表
            playlist_id = playlists_data["next_id"]
            new_playlist = {
                "id": playlist_id,
                "name": name,
                "songs": songs,
                "folder_path": folder_path,
                "created_at": datetime.now().isoformat(),
                "last_played": None,
                "play_count": 0,
            }

            # 添加到列表开头（最新的在前面）
            playlists_data["playlists"].insert(0, new_playlist)
            playlists_data["next_id"] = playlist_id + 1
            playlists_data["current_playlist_id"] = playlist_id

            # 保存
            self.save_playlists(playlists_data)
            logger.info(f"已添加新播放列表: {name} (ID: {playlist_id})")
            return playlist_id

        except Exception as e:
            logger.error(f"添加播放列表失败: {e}")
            return -1

    def get_playlist_by_id(self, playlist_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取播放列表"""
        try:
            playlists_data = self.load_playlists()
            for playlist in playlists_data["playlists"]:
                if playlist["id"] == playlist_id:
                    return playlist
            return None
        except Exception as e:
            logger.error(f"获取播放列表失败: {e}")
            return None

    def update_playlist_play_info(self, playlist_id: int) -> None:
        """更新播放列表的播放信息"""
        try:
            from datetime import datetime

            playlists_data = self.load_playlists()

            for playlist in playlists_data["playlists"]:
                if playlist["id"] == playlist_id:
                    playlist["last_played"] = datetime.now().isoformat()
                    playlist["play_count"] = playlist.get("play_count", 0) + 1
                    break

            playlists_data["current_playlist_id"] = playlist_id
            self.save_playlists(playlists_data)

        except Exception as e:
            logger.error(f"更新播放列表信息失败: {e}")

    def delete_playlist(self, playlist_id: int) -> bool:
        """删除播放列表"""
        try:
            playlists_data = self.load_playlists()

            # 查找并删除播放列表
            for i, playlist in enumerate(playlists_data["playlists"]):
                if playlist["id"] == playlist_id:
                    del playlists_data["playlists"][i]
                    logger.info(
                        f"已删除播放列表: {playlist['name']} (ID: {playlist_id})"
                    )

                    # 如果删除的是当前播放列表，清除当前播放列表ID
                    if playlists_data["current_playlist_id"] == playlist_id:
                        playlists_data["current_playlist_id"] = None

                    self.save_playlists(playlists_data)
                    return True

            logger.warning(f"未找到播放列表 ID: {playlist_id}")
            return False

        except Exception as e:
            logger.error(f"删除播放列表失败: {e}")
            return False

    def clear_cache(self) -> bool:
        """清除所有缓存文件（音乐列表、播放列表等）"""
        try:
            # 清除音乐列表缓存（现在由 music_library 管理）
            music_list_file = self.config_dir / "music_list.json"
            if music_list_file.exists():
                music_list_file.unlink()
                logger.info("音乐列表缓存已清除")

            # 清除播放列表缓存
            playlists_file = self.config_dir / "playlists.json"
            if playlists_file.exists():
                playlists_file.unlink()
                logger.info("播放列表缓存已清除")

            return True
        except Exception as e:
            logger.error(f"清除缓存失败: {e}")
            return False

    def _deep_merge(self, target: dict, source: dict) -> None:
        """深度合并字典"""
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                self._deep_merge(target[key], value)
            else:
                target[key] = value

    def get_temp_cache_directory(self) -> Path:
        """获取真正的临时缓存目录 - 用于临时文件和正在下载的文件

        这个目录在iOS升级时会被清理，适合存储临时文件
        """
        try:
            import sys
            import tempfile

            # iOS环境中使用系统临时目录
            if sys.platform == "ios" or "iOS" in str(sys.platform):
                try:
                    try:
                        import toga

                        if (
                            hasattr(toga.App, "app")
                            and toga.App.app
                            and hasattr(toga.App.app, "paths")
                        ):
                            if hasattr(toga.App.app.paths, "cache"):
                                temp_dir = (
                                    toga.App.app.paths.cache / self.app_name / "temp"
                                )
                                temp_dir.mkdir(parents=True, exist_ok=True)
                                return temp_dir
                    except ImportError:
                        pass
                except:
                    pass

                # 使用系统临时目录
                temp_dir = Path(tempfile.gettempdir()) / self.app_name / "temp"
            else:
                temp_dir = Path(tempfile.gettempdir()) / self.app_name / "temp"

            temp_dir.mkdir(parents=True, exist_ok=True)
            return temp_dir

        except Exception as e:
            logger.warning(f"无法创建临时缓存目录: {e}")
            return Path(".")

    def get_music_directory(self) -> Path:
        """获取音乐文件存储目录 - iOS版本使用持久化存储

        这个方法专门用于音乐文件存储，确保在iOS升级时不会丢失
        """
        return (
            self.get_cache_directory()
        )  # 现在get_cache_directory在iOS上返回持久化目录

    def migrate_music_files_to_persistent_storage(self) -> bool:
        """将音乐文件从临时缓存目录迁移到持久化存储目录

        在iOS升级后首次启动时调用此方法
        """
        try:
            import shutil
            import sys

            # 只在iOS上执行迁移
            if not (sys.platform == "ios" or "iOS" in str(sys.platform)):
                return True

            # 获取新的持久化目录和旧的缓存目录
            new_music_dir = self.get_music_directory()
            old_cache_dir = self.get_temp_cache_directory()

            # 检查是否有旧的音乐文件需要迁移
            old_music_files = []
            if old_cache_dir.exists():
                for file_path in old_cache_dir.rglob("*.mp3"):
                    old_music_files.append(file_path)
                for file_path in old_cache_dir.rglob("*.m4a"):
                    old_music_files.append(file_path)
                for file_path in old_cache_dir.rglob("*.wav"):
                    old_music_files.append(file_path)
                for file_path in old_cache_dir.rglob("*.flac"):
                    old_music_files.append(file_path)

            if not old_music_files:
                logger.info("没有发现需要迁移的音乐文件")
                return True

            # 执行迁移
            migrated_count = 0
            for old_file in old_music_files:
                try:
                    new_file = new_music_dir / old_file.name
                    if not new_file.exists():
                        shutil.move(str(old_file), str(new_file))
                        migrated_count += 1
                        logger.info(f"已迁移音乐文件: {old_file.name}")
                except Exception as e:
                    logger.warning(f"迁移文件失败 {old_file.name}: {e}")

            logger.info(
                f"🎵 音乐文件迁移完成，共迁移 {migrated_count} 个文件到持久化存储"
            )
            return True

        except Exception as e:
            logger.error(f"音乐文件迁移失败: {e}")
            return False

    def check_and_create_persistent_directories(self) -> None:
        """检查并创建所有必要的持久化目录"""
        try:
            # 确保配置目录存在
            self.config_dir.mkdir(parents=True, exist_ok=True)

            # 确保音乐目录存在
            music_dir = self.get_music_directory()
            music_dir.mkdir(parents=True, exist_ok=True)

            # 确保日志目录存在
            log_dir = self.get_log_directory()
            log_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"持久化目录结构已创建:")
            logger.info(f"  - 配置目录: {self.config_dir}")
            logger.info(f"  - 音乐目录: {music_dir}")
            logger.info(f"  - 日志目录: {log_dir}")

        except Exception as e:
            logger.error(f"创建持久化目录失败: {e}")
