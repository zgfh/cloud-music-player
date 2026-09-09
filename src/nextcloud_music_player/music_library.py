"""
Music library management for the Cloud Music Player.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .config_manager import ConfigManager

logger = logging.getLogger(__name__)

MUSIC_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}


class MusicLibrary:
    """Manages the local music library with metadata support."""

    def __init__(self):
        """Initialize the music library."""
        self.songs: Dict[str, Dict] = {}  # song_name -> song_info mapping

        # 使用ConfigManager来获取配置目录
        config_manager = ConfigManager()
        config_dir = config_manager.config_dir

        # 设置音乐下载目录
        self.music_dir = config_dir / "music"
        self.music_dir.mkdir(parents=True, exist_ok=True)

        # 统一使用 music_list.json 管理音乐信息
        self.music_list_file = config_dir / "music_list.json"

        self.load_music_list()

    def add_song_with_info(
        self, song_name: str, file_path: str, song_info: Dict
    ) -> None:
        """Add a song with metadata to the library."""
        if os.path.exists(file_path):
            self.songs[song_name] = {
                **song_info,
                "filepath": file_path,
                "is_downloaded": True,
                "download_time": datetime.now().isoformat(),
            }
            self.save_music_list()

    def add_remote_song(
        self,
        song_name: str,
        remote_path: str,
        size: int = 0,
        modified: str = "",
        etag: str = "",
        source_type: str = "nextcloud",
        sync_folder: str = "",
    ) -> None:
        """Add a remote song while retaining every origin for the same filename."""
        song_info = self.extract_song_info_from_filename(song_name)
        origin = {
            "source_type": source_type,
            "remote_path": remote_path,
            "size": size,
            "modified": modified,
            "etag": etag,
            "sync_folder": sync_folder,
        }
        # 文件名仍是播放列表兼容主键；远端候选保存在 origins，首次发现的
        # 来源保持为稳定主来源，后同步来源不再覆盖它。
        if song_name in self.songs:
            existing = self.songs[song_name]
            origins = existing.setdefault("origins", [])
            if not origins and existing.get("remote_path"):
                origins.append(
                    {
                        "source_type": existing.get("source_type", "nextcloud"),
                        "remote_path": existing.get("remote_path", ""),
                        "size": existing.get("size", 0),
                        "modified": existing.get("modified", ""),
                        "etag": existing.get("etag", ""),
                        "sync_folder": existing.get("sync_folder", ""),
                    }
                )
            match = next(
                (
                    item
                    for item in origins
                    if item.get("source_type") == source_type
                    and item.get("remote_path") == remote_path
                ),
                None,
            )
            if match is None:
                origins.append(origin)
            else:
                match.update(origin)
            self.save_music_list()
            return
        self.songs[song_name] = {
            **song_info,
            "filename": song_name,
            "remote_path": remote_path,
            "size": size,
            "modified": modified,
            "etag": etag,
            "sync_folder": sync_folder,
            "source_type": source_type,
            "origins": [origin],
            "is_downloaded": False,
            "filepath": None,
        }
        self.save_music_list()

    def update_remote_song(self, song_name: str, file_info: Dict) -> None:
        """Update remote song information."""
        if song_name in self.songs:
            self.songs[song_name].update(
                {
                    "remote_path": file_info["path"],
                    "size": file_info.get("size", 0),
                    "modified": file_info.get("modified", ""),
                    "etag": file_info.get("etag", ""),
                }
            )
            self.save_music_list()

    def mark_song_downloaded(self, song_name: str, local_path: str) -> None:
        """Mark a song as downloaded and set its local path."""
        if song_name in self.songs:
            self.songs[song_name]["is_downloaded"] = True
            self.songs[song_name]["filepath"] = local_path
            self.songs[song_name]["download_time"] = datetime.now().isoformat()
            self.save_music_list()

    def is_song_downloaded(self, song_name: str) -> bool:
        """Check if a song is downloaded locally."""
        song = self.get_song_info(song_name)
        if not song:
            return False

        # Check if marked as downloaded and file exists
        is_downloaded = song.get("is_downloaded", False)
        filepath = song.get("filepath")
        logger.info(f"Checking download status for song: {song_name}")

        if is_downloaded and filepath and os.path.exists(filepath):
            logger.info(f"Song '{song_name}' is downloaded.")
            return True

        if not filepath:
            filepath = str(self.music_dir / song_name)
            logger.info(f"Using default music directory for song: {filepath}")

        if filepath and os.path.exists(filepath):
            logger.info(f"Song '{song_name}' is now marked as downloaded.")
            self.songs[song_name]["filepath"] = filepath
            self.songs[song_name]["is_downloaded"] = True
            self.save_music_list()
            return True

        # If file doesn't exist, mark as not downloaded
        if song_name in self.songs:
            logger.info(
                f"Song '{song_name}' is not downloaded. Changing is_downloaded to False."
            )
            self.songs[song_name]["is_downloaded"] = False
            self.songs[song_name]["filepath"] = None
            self.save_music_list()

        return False

    def is_file_cached(self, song_name: str) -> bool:
        """Check if a song file is cached (downloaded) locally."""
        return self.is_song_downloaded(song_name)

    def get_song_info(self, song_name: str) -> Optional[Dict]:
        """Get song information."""
        song = self.songs.get(song_name)
        if not song:
            logger.info(f"Song '{song_name}' not found in library.")
            self.load_music_list()
            song = self.songs.get(song_name)
        return song

    def extract_song_info_from_filename(self, filename: str) -> Dict:
        """Extract song info from filename."""
        # Remove file extension
        name_without_ext = os.path.splitext(filename)[0]

        # 移除常见的曲序前缀，如 "0172." / "01 - " / "1_"。
        name_without_ext = re.sub(r"^\s*\d{1,4}\s*[._-]\s*", "", name_without_ext)

        # 兼容 "Artist - Title" 和中文曲库常见的 "Artist-Title"。
        if "-" in name_without_ext:
            parts = name_without_ext.split("-", 1)
            return {
                "title": parts[1].strip(),
                "artist": parts[0].strip(),
                "album": "未知专辑",
            }
        return {
            "title": name_without_ext,
            "artist": "未知艺术家",
            "album": "未知专辑",
        }

    def update_song_metadata(self, song_name: str, metadata: Dict) -> bool:
        """保存用户确认的展示信息，不修改源文件名或音频标签。"""
        song = self.songs.get(song_name)
        if not song:
            return False
        allowed = {
            "custom_title",
            "artist",
            "album",
            "year",
            "musicbrainz_mbid",
            "metadata_source",
            "metadata_updated_at",
        }
        for key in allowed:
            if key in metadata:
                song[key] = str(metadata[key] or "").strip()
        self.save_music_list()
        return True

    def has_song(self, song_name: str) -> bool:
        """Check if a song exists in the library."""
        return song_name in self.songs

    def remove_song(self, song_name: str) -> None:
        """Remove a song from the library."""
        if song_name in self.songs:
            del self.songs[song_name]
            self.save_music_list()

    def get_song_path(self, song_name: str) -> str:
        """Get the file path for a song."""
        song_info = self.get_song_info(song_name)
        logger.info(f"Getting file path for song: {song_name}, info: {song_info}")
        filepath = song_info.get("filepath", "")
        # 确保返回字符串格式的路径
        if hasattr(filepath, "__fspath__"):
            return os.fspath(filepath)
        return str(filepath)

    def get_local_file_path(self, song_name: str) -> Optional[str]:
        """Get the local file path for a song if it's downloaded."""
        if self.is_song_downloaded(song_name):

            return self.get_song_path(song_name)
        return None

    def list_songs(self) -> List[str]:
        """Get a list of all song names in the library."""
        return list(self.songs.keys())

    def search_songs(self, query: str) -> List[str]:
        """Search for songs by title, artist, or album."""
        query = query.lower()
        results = []

        for song_name, song_info in self.songs.items():
            if (
                query in song_info.get("title", "").lower()
                or query in song_info.get("custom_title", "").lower()
                or query in song_info.get("artist", "").lower()
                or query in song_info.get("album", "").lower()
                or query in song_name.lower()
            ):
                results.append(song_name)

        return results

    def get_artists(self) -> List[str]:
        """Get a list of all artists."""
        artists = set()
        for song_info in self.songs.values():
            artists.add(song_info.get("artist", "未知艺术家"))
        return sorted(list(artists))

    def get_albums(self) -> List[str]:
        """Get a list of all albums."""
        albums = set()
        for song_info in self.songs.values():
            albums.add(song_info.get("album", "未知专辑"))
        return sorted(list(albums))

    def get_songs_by_artist(self, artist: str) -> List[str]:
        """Get all songs by a specific artist."""
        return [
            name
            for name, info in self.songs.items()
            if info.get("artist", "未知艺术家") == artist
        ]

    def get_songs_by_album(self, album: str) -> List[str]:
        """Get all songs from a specific album."""
        return [
            name
            for name, info in self.songs.items()
            if info.get("album", "未知专辑") == album
        ]

    def clear(self) -> None:
        """Clear all songs from the library."""
        self.songs.clear()
        self.save_music_list()

    def clear_cache(self) -> None:
        """Clear the library cache and reset."""
        self.songs.clear()
        # 删除音乐列表文件
        if self.music_list_file.exists():
            try:
                self.music_list_file.unlink()
            except Exception as e:
                logger.error(f"Failed to delete music list file: {e}")

    def get_cached_songs(self) -> List[Dict]:
        """Return real audio files in the managed cache directory.

        Disk is the source of truth: stale index flags are ignored and orphaned
        audio files are included, so the cache page count matches actual storage.
        """
        cached = []
        seen_paths = set()
        for song_name, song_info in self.songs.items():
            if not song_info.get("is_downloaded", False):
                continue
            filepath = song_info.get("filepath")
            if not filepath:
                continue
            path = Path(filepath)
            try:
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(self.music_dir.resolve()):
                    continue
                size = path.stat().st_size
            except OSError:
                continue
            seen_paths.add(resolved)
            cached.append(
                {
                    "name": song_name,
                    "filepath": str(path),
                    "size": size,
                    "download_time": song_info.get("download_time", ""),
                }
            )
        # 兼容升级、异常退出或旧版本遗留：文件已落盘但索引尚未写入。
        try:
            for path in self.music_dir.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in MUSIC_EXTENSIONS:
                    continue
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                cached.append(
                    {
                        "name": path.relative_to(self.music_dir).as_posix(),
                        "filepath": str(path),
                        "size": path.stat().st_size,
                        "download_time": "",
                        "orphaned": True,
                    }
                )
        except OSError as ex:
            logger.warning(f"扫描音乐缓存目录失败: {ex}")
        return sorted(cached, key=lambda item: item["name"].lower())

    def remove_cached_songs(self, song_names: List[str]) -> tuple[int, int]:
        """Delete selected downloaded files while keeping their remote metadata.

        Returns ``(deleted_count, freed_bytes)``. Paths outside the managed music
        directory are never removed, which protects against a corrupted index.
        """
        music_root = self.music_dir.resolve()
        deleted_count = 0
        freed_bytes = 0
        changed = False
        for song_name in dict.fromkeys(song_names):
            song_info = self.songs.get(song_name)
            filepath = song_info.get("filepath") if song_info else None
            if not filepath:
                # 允许清理由缓存扫描发现的孤立文件，路径仍受 music_root 约束。
                filepath = self.music_dir / song_name
            if not filepath:
                continue
            path = Path(filepath)
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(music_root):
                    logger.warning(f"拒绝删除音乐缓存目录外的文件: {path}")
                    continue
                size = resolved.stat().st_size if resolved.is_file() else 0
                if resolved.exists():
                    resolved.unlink()
                freed_bytes += size
                deleted_count += 1
                if song_info:
                    song_info["is_downloaded"] = False
                    song_info["filepath"] = None
                    song_info.pop("download_time", None)
                    changed = True
            except OSError as ex:
                logger.error(f"删除缓存文件失败 {path}: {ex}")
        if changed:
            self.save_music_list()
        return deleted_count, freed_bytes

    def get_songs_count(self) -> int:
        """Get the number of songs in the library."""
        return len(self.songs)

    def _make_json_serializable(self, obj):
        """Convert objects to JSON serializable format."""
        if isinstance(obj, Path):
            return str(obj)
        elif isinstance(obj, dict):
            return {
                key: self._make_json_serializable(value) for key, value in obj.items()
            }
        elif isinstance(obj, list):
            return [self._make_json_serializable(item) for item in obj]
        else:
            return obj

    def save_music_list(self) -> None:
        """Save the music list to file."""
        try:
            # 确保所有数据都可以序列化
            serializable_songs = self._make_json_serializable(self.songs)

            music_data = {
                "music_list": serializable_songs,
                "last_sync": datetime.now().isoformat(),
                "sync_folder": getattr(self, "sync_folder", ""),
                "cache_stats": {
                    "total_songs": len(self.songs),
                    "downloaded_songs": len(
                        [
                            s
                            for s in self.songs.values()
                            if s.get("is_downloaded", False)
                        ]
                    ),
                    "cache_size": self._calculate_cache_size(),
                },
            }

            with open(self.music_list_file, "w", encoding="utf-8") as f:
                json.dump(music_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save music list: {e}")
            logger.error(f"Error details: {str(e)}")
            # 打印problematic data for debugging
            for song_name, song_info in self.songs.items():
                for key, value in song_info.items():
                    if isinstance(value, Path):
                        logger.error(
                            f"Found Path object in song {song_name}, key {key}: {value}"
                        )

    def save_music_list(self) -> None:
        """Save the music list to file."""
        try:
            music_data = {
                "music_list": self.songs,
                "last_sync": datetime.now().isoformat(),
                "sync_folder": getattr(self, "sync_folder", ""),
                "cache_stats": {
                    "total_songs": len(self.songs),
                    "downloaded_songs": len(
                        [
                            s
                            for s in self.songs.values()
                            if s.get("is_downloaded", False)
                        ]
                    ),
                    "cache_size": self._calculate_cache_size(),
                },
            }

            with open(self.music_list_file, "w", encoding="utf-8") as f:
                json.dump(music_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save music list: {e}")

    def load_music_list(self) -> None:
        """Load the music list from file."""
        try:
            if self.music_list_file.exists():
                with open(self.music_list_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.songs = data["music_list"]
                    self.sync_folder = data.get("sync_folder", "")
                    logger.info(f"Loaded {len(self.songs)} songs from music list.")

        except Exception as e:
            logger.error(f"Failed to load music list: {e}")
            self.songs = {}
            self.sync_folder = ""

    def get_all_songs(self) -> Dict[str, Dict]:
        """获取所有歌曲信息"""
        return self.songs.copy()

    def _calculate_cache_size(self) -> int:
        """Calculate total size of downloaded music files."""
        total_size = 0
        for song_info in self.songs.values():
            if song_info.get("is_downloaded", False):
                filepath = song_info.get("filepath")
                if filepath and os.path.exists(filepath):
                    try:
                        total_size += os.path.getsize(filepath)
                    except OSError:
                        pass
        return total_size
