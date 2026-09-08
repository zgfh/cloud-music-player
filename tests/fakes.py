"""
无头交互测试替身（fakes）

不启动 Flet 界面、不连真实网络、不播真实音频：
- FakePage 替代 ft.Page，记录 update/对话框操作
- FakeAudioPlayer 替代平台音频播放器，记录加载/播放/停止
- FakeNextcloudClient 模拟慢网络（可配置每个文件的下载延迟、失败、404）
- FakeMusicLibrary / FakeConfigManager 提供内存态数据

视图层代码原封不动地跑在替身之上，点击回调直接以协程方式驱动，
因此整套测试可以全自动跑，无需截图人工核对。
"""

import asyncio
import os
from pathlib import Path


class FakePage:
    """Flet Page 替身：实现视图层用到的接口并记录调用"""

    def __init__(self):
        self.update_calls = 0
        self.dialogs = []  # show_dialog 打开的对话框栈
        self.popped_dialogs = 0
        self.overlay = []
        self.launched_urls = []  # launch_url 打开过的外部链接

    def update(self, *controls):
        self.update_calls += 1

    async def launch_url(self, url, **kwargs):
        self.launched_urls.append(url)

    def show_dialog(self, dialog):
        dialog.open = True  # 与真实 Page.show_dialog 一致
        self.dialogs.append(dialog)

    def pop_dialog(self):
        self.popped_dialogs += 1
        return self.dialogs.pop() if self.dialogs else None


def last_notification_text(page) -> str:
    """Return the message from the latest top-overlay notification."""
    notification = page.overlay[-1]
    return notification.content.content.controls[1].value


class FakeAudioPlayer:
    """平台音频播放器替身：记录加载历史与播放状态"""

    def __init__(self):
        self.loaded_files = []  # 按顺序记录 load() 过的文件
        self.playing = False
        self.paused = False
        self.stopped_count = 0
        self.completed = False
        self.volume = 0.7
        self.seek_calls = []

    def load(self, path):
        self.loaded_files.append(str(path))
        return True

    def play(self):
        if self.loaded_files:
            self.playing = True
            self.paused = False
            return True
        return False

    def pause(self):
        if self.playing:
            self.playing = False
            self.paused = True
            return True
        return False

    def stop(self):
        self.stopped_count += 1
        self.playing = False
        self.paused = False
        return True

    def is_playing(self):
        return self.playing

    def has_completed(self):
        completed = self.completed
        self.completed = False
        return completed

    def get_duration(self):
        return 180.0 if self.playing else 0.0

    def get_position(self):
        return 10.0 if self.playing else 0.0

    def seek(self, position):
        self.seek_calls.append(position)
        return True

    def set_volume(self, volume):
        self.volume = volume
        return True


class FakeNextcloudClient:
    """NextCloud 客户端替身：模拟慢网络 / 下载失败 / 目录 404 等场景。

    download_delay 可以是统一秒数，或 {文件名: 秒数} 字典，用于构造
    "先点慢歌 A 再点快歌 B" 的竞态场景。
    """

    def __init__(
        self,
        files=None,
        directories=None,
        download_delay=0.0,
        list_delay=0.0,
        download_error=None,
        list_error=None,
        dir_errors=None,
        connect_ok=True,
        connect_delay=0.0,
        connect_error=None,
    ):
        self.files = files or []  # list_music_files 返回值
        self.directories = directories or {}  # {路径: [{'name': ...}]}
        self.download_delay = download_delay
        self.list_delay = list_delay
        self.download_error = download_error
        self.list_error = list_error
        self.dir_errors = dir_errors or {}  # {路径: Exception}
        self.connect_ok = connect_ok
        self.connect_delay = connect_delay
        self.connect_error = connect_error

        self.download_calls = []  # [(remote_path, filename)]
        self.list_calls = []
        self.dir_calls = []

    def _delay_for(self, filename, delay):
        if isinstance(delay, dict):
            return delay.get(filename, 0.0)
        return delay

    async def test_connection(self):
        await asyncio.sleep(self.connect_delay)
        if self.connect_error:
            raise self.connect_error
        return self.connect_ok

    async def list_music_files(self, folder_path=""):
        self.list_calls.append(folder_path)
        await asyncio.sleep(self.list_delay)
        if self.list_error:
            raise self.list_error
        return [dict(f, sync_folder=folder_path) for f in self.files]

    async def download_file(self, file_path, file_name, local_path=None):
        self.download_calls.append((file_path, file_name))
        await asyncio.sleep(self._delay_for(file_name, self.download_delay))
        if self.download_error:
            raise self.download_error
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"ID3" + b"\x00" * 64)
        return str(local_path)

    async def list_directories(self, folder_path=""):
        self.dir_calls.append(folder_path)
        await asyncio.sleep(self.list_delay)
        if folder_path in self.dir_errors:
            raise self.dir_errors[folder_path]
        return self.directories.get(folder_path, self.directories.get("/", []))


class FakeMusicLibrary:
    """音乐库替身：内存态歌曲表，下载目录指向临时目录"""

    def __init__(self, tmp_path):
        self.songs = {}
        self.music_dir = Path(tmp_path) / "music"
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.sync_folder = "/"

    def add_remote_song(
        self, song_name, remote_path, size=0, modified="", etag="",
        source_type="nextcloud", sync_folder="",
    ):
        if song_name not in self.songs:
            self.songs[song_name] = {
                "name": song_name,
                "filename": song_name,
                "remote_path": remote_path,
                "source_type": source_type,
                "size": size,
                "modified": modified,
                "etag": etag,
                "sync_folder": sync_folder,
                "is_downloaded": False,
                "filepath": "",
            }

    def mark_song_downloaded(self, song_name, local_path):
        if song_name in self.songs:
            self.songs[song_name]["is_downloaded"] = True
            self.songs[song_name]["filepath"] = str(local_path)

    def get_song_info(self, song_name):
        return self.songs.get(song_name)

    def update_song_metadata(self, song_name, metadata):
        if song_name not in self.songs:
            return False
        self.songs[song_name].update(metadata)
        return True

    def get_all_songs(self):
        return {name: dict(info) for name, info in self.songs.items()}

    def is_file_cached(self, filename):
        filepath = (self.songs.get(filename) or {}).get("filepath", "")
        return bool(filepath) and os.path.exists(filepath)

    def has_song(self, song_name):
        return song_name in self.songs

    def remove_song(self, song_name):
        self.songs.pop(song_name, None)

    def save_music_list(self):
        pass

    def search_songs(self, query):
        query = query.lower()
        return [
            n for n, info in self.songs.items()
            if query in n.lower()
            or query in str(info.get("custom_title", "")).lower()
            or query in str(info.get("artist", "")).lower()
            or query in str(info.get("album", "")).lower()
        ]

    def get_local_file_path(self, song_name):
        return (self.songs.get(song_name) or {}).get("filepath", "")

    def clear_cache(self):
        pass


class FakeConfigManager:
    """配置管理器替身：内存态读写，点分键（section.option）"""

    def __init__(self, config=None):
        self._config = {"connection": {}, "player": {"volume": 70}}
        for section, options in (config or {}).items():
            self._config.setdefault(section, {}).update(options)

    def get(self, key, default=None):
        # 与真实 ConfigManager 一致：全路径点分键逐层取值，
        # 裸段落键返回整个字典
        value = self._config
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def set(self, key, value):
        target = self._config
        keys = key.split(".")
        for part in keys[:-1]:
            if not isinstance(target.get(part), dict):
                target[part] = {}
            target = target[part]
        target[keys[-1]] = value

    def save_config(self):
        pass

    def load_playlists(self):
        return {"playlists": [], "current_playlist": None}

    def save_playlists(self, data):
        pass

    def get_playlist_by_id(self, playlist_id):
        return None

    def clear_cache(self):
        pass

    def get_connection_config(self):
        return dict(self._config.get("connection", {}))


class FakeViewManager:
    """视图管理器替身：记录视图切换"""

    def __init__(self):
        self.switched_to = []
        self.views = {}

    def switch_to_view(self, name):
        self.switched_to.append(name)

    def get_view(self, name):
        return self.views.get(name)


def make_music_service(music_library, nextcloud_client, config_manager, monkeypatch):
    """真实 MusicService + 假依赖；ffmpeg 转码替换为 no-op 避免测试转码真实文件"""
    from nextcloud_music_player.utils import audio_normalize

    async def _noop_normalize(_path):
        return False

    monkeypatch.setattr(audio_normalize, "normalize_audio_async", _noop_normalize)

    from nextcloud_music_player.services.music_service import MusicService

    return MusicService(music_library, nextcloud_client, config_manager)


def add_remote_song(library, name, remote_path=None, downloaded=False):
    """向假音乐库登记一首歌；downloaded=True 时同时在本地落一个文件"""
    library.add_remote_song(name, remote_path or f"/remote/{name}")
    if downloaded:
        local = library.music_dir / name
        local.write_bytes(b"ID3" + b"\x00" * 64)
        library.mark_song_downloaded(name, str(local))
    return library.get_song_info(name)
