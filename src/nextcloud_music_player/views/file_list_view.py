"""
文件列表视图 - Flet 版本
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List

import flet as ft

from ..utils.notify import show_snack_bar
from ..utils.theme import (
    Color,
    FontSize,
    Radius,
    Space,
    glow,
    tint,
)

logger = logging.getLogger(__name__)


class FileListView:
    """文件列表管理视图"""

    def __init__(self, page: ft.Page, app_context: dict, view_manager):
        self.page = page
        self.app_context = app_context
        self.music_service = app_context["music_service"]
        self.view_manager = view_manager
        self.music_files = []
        self.selected_files = set()
        self.last_selected_name = None  # 最后点选的歌曲，播放时从它开始
        self.is_syncing = False
        self._built = False
        # 下载队列：iOS 切后台进程挂起会中断下载协程，
        # 未完成项留在 pending，回前台（on_app_resumed）自动续传
        self._pending_downloads: list[tuple[str, str]] = []
        self._download_task: asyncio.Task | None = None

    def rebuild(self):
        """重建视图（Flet 0.86 控件脱离页面后被冻结且不可复用）"""
        self._built = False
        return self.build()

    def build(self):
        """构建并返回视图内容"""
        if self._built and hasattr(self, "_container"):
            return self._container

        # === 标题区 ===
        title_row = ft.Row(
            [
                ft.Container(
                    content=ft.Icon(
                        ft.Icons.LIBRARY_MUSIC_OUTLINED, color=Color.PRIMARY, size=20
                    ),
                    width=36,
                    height=36,
                    border_radius=Radius.SM,
                    bgcolor=tint(Color.PRIMARY, "14"),
                    border=ft.Border.all(1, tint(Color.PRIMARY, "33")),
                ),
                ft.Column(
                    [
                        ft.Text(
                            "音乐库",
                            size=FontSize.TITLE + 4,
                            weight=ft.FontWeight.BOLD,
                            color=Color.TEXT_PRIMARY,
                        ),
                        ft.Text(
                            "LIBRARY · 云端文件",
                            size=FontSize.MICRO,
                            color=Color.TEXT_MUTED,
                            style=ft.TextStyle(letter_spacing=2),
                        ),
                    ],
                    spacing=0,
                ),
            ],
            spacing=Space.MD,
        )

        # 操作栏
        self.sync_button = ft.FilledButton(
            "同步",
            icon=ft.Icons.SYNC,
            on_click=self._sync_music_list,
            style=ft.ButtonStyle(
                bgcolor=Color.BG_ELEVATED,
                color=Color.PRIMARY,
                icon_color=Color.PRIMARY,
                side=ft.BorderSide(1, tint(Color.PRIMARY, "40")),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )

        self.search_input = ft.TextField(
            hint_text="搜索歌曲 / 艺术家...",
            prefix_icon=ft.Icons.SEARCH,
            on_submit=self._search_music,
            bgcolor=Color.BG_SURFACE_ALT,
            border_color=Color.BORDER,
            focused_border_color=Color.PRIMARY,
            border_width=1,
            border_radius=Radius.CIRCLE,
            color=Color.TEXT_PRIMARY,
            hint_style=ft.TextStyle(color=Color.TEXT_DISABLED),
            cursor_color=Color.PRIMARY,
            content_padding=ft.Padding(left=16, right=16, top=10, bottom=10),
            expand=True,
        )

        self.search_button = ft.IconButton(
            ft.Icons.SEARCH,
            tooltip="搜索",
            icon_color=Color.TEXT_SECONDARY,
            on_click=self._search_music,
        )

        # 同步状态栏（点击查看所有来源/目录详情）
        self.folder_text = ft.Text(
            "同步状态：0/0",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.folder_button = ft.TextButton(
            "查看详情",
            icon=ft.Icons.CHEVRON_RIGHT,
            on_click=self._show_sync_details,
            style=ft.ButtonStyle(
                color=Color.PRIMARY,
                icon_color=Color.PRIMARY,
                padding=0,
            ),
        )

        # 选择与播放操作栏
        self.play_button = ft.FilledButton(
            "播放",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._play_selected,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DISABLED: Color.BG_ELEVATED,
                    ft.ControlState.DEFAULT: Color.PRIMARY,
                },
                color={
                    ft.ControlState.DISABLED: Color.TEXT_DISABLED,
                    ft.ControlState.DEFAULT: Color.PRIMARY_TEXT,
                },
                icon_color={
                    ft.ControlState.DISABLED: Color.TEXT_DISABLED,
                    ft.ControlState.DEFAULT: Color.PRIMARY_TEXT,
                },
                elevation={ft.ControlState.DEFAULT: 4, ft.ControlState.PRESSED: 1},
                shadow_color=tint(Color.PRIMARY, "59"),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )
        self.select_all_button = ft.TextButton(
            "全选",
            on_click=self._select_all,
            style=ft.ButtonStyle(color=Color.TEXT_SECONDARY),
        )
        self.delete_button = ft.TextButton(
            "删除",
            on_click=self._delete_selected,
            style=ft.ButtonStyle(color=Color.DANGER_TEXT),
        )
        # 统计栏（数据芯片风）
        self.stats_text = ft.Text(
            "总数 0 · 已选 0 · 已下载 0",
            size=FontSize.CAPTION,
            color=Color.TEXT_SECONDARY,
            style=ft.TextStyle(letter_spacing=1),
        )

        # 文件列表
        self.file_list = ft.ListView(
            expand=True,
            spacing=6,
            padding=ft.Padding(left=0, right=0, top=0, bottom=Space.SM),
        )

        # 下载栏
        self.download_button = ft.FilledButton(
            "下载",
            icon=ft.Icons.CLOUD_DOWNLOAD,
            on_click=self._download_selected,
            disabled=True,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DISABLED: Color.BG_ELEVATED,
                    ft.ControlState.DEFAULT: Color.BG_ELEVATED,
                },
                color={
                    ft.ControlState.DISABLED: Color.TEXT_DISABLED,
                    ft.ControlState.DEFAULT: Color.SUCCESS_TEXT,
                },
                icon_color={
                    ft.ControlState.DISABLED: Color.TEXT_DISABLED,
                    ft.ControlState.DEFAULT: Color.SUCCESS,
                },
                side=ft.BorderSide(1, tint(Color.SUCCESS, "40")),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )
        self.selection_action_row = ft.Row(
            [
                self.select_all_button,
                self.download_button,
                self.play_button,
                self.delete_button,
            ],
            spacing=Space.XS,
        )
        # 组装
        self._container = ft.Container(
            content=ft.Column(
                controls=[
                    title_row,
                    ft.Row(
                        [self.sync_button, self.search_input, self.search_button],
                        spacing=Space.XS,
                    ),
                    ft.Row([self.folder_text, self.folder_button], spacing=Space.XS),
                    self.selection_action_row,
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(
                                    ft.Icons.QUERY_STATS, size=14, color=Color.PRIMARY
                                ),
                                self.stats_text,
                            ],
                            spacing=Space.SM,
                        ),
                        bgcolor=tint(Color.PRIMARY, "0D"),
                        border=ft.Border.all(1, tint(Color.PRIMARY, "26")),
                        padding=ft.Padding(left=12, right=12, top=8, bottom=8),
                        border_radius=Radius.CIRCLE,
                        width=float("inf"),
                    ),
                    ft.Container(
                        content=self.file_list,
                        expand=True,
                        bgcolor=Color.BG_APP_ALT,
                        border=ft.Border.all(1, Color.BORDER),
                        border_radius=Radius.LG,
                        padding=Space.XS,
                    ),
                ],
                spacing=Space.MD,
                expand=True,
            ),
            padding=Space.LG,
            expand=True,
            bgcolor=Color.BG_APP,
        )

        self._built = True
        self.reload_music_list()
        return self._container

    def build_file_item(self, song: Dict[str, Any]) -> ft.Container:
        """构建单个文件项（暗色卡片，选中态霓虹发光）"""
        name = song.get("name", "Unknown")
        title = song.get("custom_title") or song.get("title", name)
        if title.endswith(".mp3"):
            title = title[:-4]
        artist = song.get("artist", "未知艺术家")
        is_downloaded = song.get("is_downloaded", False)
        source_type = song.get("source_type", "nextcloud")
        origins = song.get("origins") or []
        source_label = {
            "nextcloud": "Nextcloud",
            "smb": "SMB",
            "gdrive": "Google Drive",
        }.get(source_type, source_type or "未知来源")
        source_text = f"{len(origins)} 个来源" if len(origins) > 1 else source_label
        availability = f"{source_text} · " + ("已下载" if is_downloaded else "需联网")
        size = song.get("size", 0)

        size_str = f"{float(size) / 1024 / 1024:.1f}MB" if size else ""
        download_icon = (
            ft.Icons.TASK_ALT if is_downloaded else ft.Icons.CLOUD_DOWNLOAD_OUTLINED
        )
        source_connected = (
            any(
                origin.get("source_type") in self.music_service.source_clients
                for origin in origins
            )
            if origins
            else source_type in self.music_service.source_clients
        )
        download_color = (
            Color.SUCCESS
            if is_downloaded
            else (Color.PRIMARY if source_connected else Color.TEXT_DISABLED)
        )

        selected = name in self.selected_files
        check_icon = ft.Icon(
            ft.Icons.CHECK_CIRCLE if selected else ft.Icons.RADIO_BUTTON_UNCHECKED,
            color=Color.PRIMARY if selected else Color.TEXT_DISABLED,
            size=20,
        )

        return ft.Container(
            key=f"song:{name}",
            content=ft.Row(
                [
                    check_icon,
                    ft.Icon(download_icon, color=download_color, size=18),
                    ft.Column(
                        [
                            ft.Text(
                                title,
                                size=FontSize.BODY + 1,
                                weight=ft.FontWeight.W_500,
                                color=(
                                    Color.TEXT_PRIMARY
                                    if selected
                                    else Color.TEXT_PRIMARY
                                ),
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                f"{artist} · {availability}"
                                + (f" · {size_str}" if size_str else ""),
                                size=FontSize.CAPTION,
                                color=Color.TEXT_MUTED,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.TextButton(
                        "详情",
                        icon=ft.Icons.INFO_OUTLINE,
                        on_click=lambda e, n=name: self._show_song_details(n),
                        style=ft.ButtonStyle(
                            color=Color.PRIMARY,
                            icon_color=Color.PRIMARY,
                            padding=ft.Padding(left=8, right=8, top=4, bottom=4),
                        ),
                    ),
                ],
                spacing=Space.SM,
            ),
            padding=ft.Padding(left=12, right=12, top=10, bottom=10),
            border_radius=Radius.MD,
            on_click=lambda e, n=name: self._toggle_select(n),
            bgcolor=tint(Color.PRIMARY, "1A") if selected else Color.BG_SURFACE,
            border=ft.Border.all(
                1, tint(Color.PRIMARY, "66") if selected else Color.BORDER
            ),
            shadow=glow(Color.PRIMARY, radius=12, alpha="26") if selected else None,
        )

    def _show_song_details(self, song_name: str):
        """打开只修改音乐库索引、不触碰源文件的歌曲信息弹窗。"""
        song = self.music_service.get_song_info(song_name) or {}
        # 旧索引可能把 "0172.五月天-倔强" 整串当成歌名。
        # 未经手动/MusicBrainz 编辑的记录，打开时用新规则重新解析。
        parsed = {}
        if not song.get("metadata_source"):
            library = self.app_context.get("music_library")
            parser = getattr(library, "extract_song_info_from_filename", None)
            if parser:
                parsed = parser(song_name)
        field_style = dict(
            bgcolor=Color.BG_SURFACE_ALT,
            border_color=Color.BORDER,
            focused_border_color=Color.PRIMARY,
            color=Color.TEXT_PRIMARY,
            border_radius=Radius.MD,
        )
        self.song_detail_name = song_name
        self.song_detail_mbid = song.get("musicbrainz_mbid", "")
        self.song_title_input = ft.TextField(
            label="自定义歌名",
            value=(
                song.get("custom_title") or parsed.get("title") or song.get("title", "")
            ),
            hint_text="仅改变应用内显示，不修改文件名",
            **field_style,
        )
        self.song_artist_input = ft.TextField(
            label="歌手",
            value=parsed.get("artist") or song.get("artist", ""),
            **field_style,
        )
        self.song_album_input = ft.TextField(
            label="专辑", value=song.get("album", ""), **field_style
        )
        self.song_year_input = ft.TextField(
            label="年份", value=str(song.get("year", "") or ""), **field_style
        )
        self.song_query_status = ft.Text(
            "可按当前歌手和歌名查询 MusicBrainz",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
        )
        self.song_candidate_list = ft.ListView(height=190, spacing=Space.XS)
        musicbrainz_enabled = bool(
            self.app_context["config_manager"].get("metadata.musicbrainz_enabled", True)
        )
        self.song_query_button = ft.OutlinedButton(
            "查询",
            icon=ft.Icons.SEARCH,
            on_click=lambda e: asyncio.create_task(self._query_song_metadata()),
            disabled=not musicbrainz_enabled,
        )
        if not musicbrainz_enabled:
            self.song_query_status.value = "MusicBrainz 在线查询已在设置中关闭"
        self.song_detail_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("歌曲详情", color=Color.TEXT_PRIMARY),
            content=ft.Container(
                width=520,
                content=ft.Column(
                    [
                        ft.Text(
                            f"源文件：{song_name}",
                            size=FontSize.CAPTION,
                            color=Color.TEXT_MUTED,
                        ),
                        self.song_title_input,
                        self.song_artist_input,
                        ft.Row(
                            [self.song_album_input, self.song_year_input],
                            spacing=Space.SM,
                        ),
                        ft.Row(
                            [self.song_query_button, self.song_query_status],
                            spacing=Space.SM,
                        ),
                        self.song_candidate_list,
                    ],
                    spacing=Space.SM,
                    tight=True,
                ),
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton(
                    "保存", icon=ft.Icons.SAVE, on_click=self._save_song_details
                ),
            ],
        )
        self.page.show_dialog(self.song_detail_dialog)

    async def _query_song_metadata(self):
        if not self.app_context["config_manager"].get(
            "metadata.musicbrainz_enabled", True
        ):
            self.song_query_status.value = "MusicBrainz 在线查询已关闭"
            self.song_query_status.color = Color.TEXT_MUTED
            self.page.update()
            return
        title = (self.song_title_input.value or "").strip()
        artist = (self.song_artist_input.value or "").strip()
        if not title and not artist:
            self.song_query_status.value = "请先填写歌名或歌手"
            self.song_query_status.color = Color.DANGER_TEXT
            self.page.update()
            return
        self.song_query_button.disabled = True
        self.song_query_status.value = "正在查询…"
        self.song_query_status.color = Color.INFO_TEXT
        self.page.update()
        try:
            if not hasattr(self, "_musicbrainz_service"):
                from ..services.musicbrainz_service import MusicBrainzService

                self._musicbrainz_service = MusicBrainzService()
            candidates = await self._musicbrainz_service.search(artist, title)
            self.song_candidate_list.controls.clear()
            for candidate in candidates:
                subtitle = " · ".join(
                    part
                    for part in (
                        candidate.get("artist", ""),
                        candidate.get("album", ""),
                        candidate.get("year", ""),
                        f"匹配 {candidate.get('confidence', 0)}%",
                    )
                    if part
                )
                self.song_candidate_list.controls.append(
                    ft.ListTile(
                        title=ft.Text(
                            candidate.get("title", ""), color=Color.TEXT_PRIMARY
                        ),
                        subtitle=ft.Text(subtitle, color=Color.TEXT_MUTED),
                        trailing=ft.OutlinedButton(
                            "选择",
                            on_click=lambda e, item=candidate: self._select_song_candidate(
                                item
                            ),
                        ),
                    )
                )
            self.song_query_status.value = (
                f"找到 {len(candidates)} 个候选，请选择后保存"
                if candidates
                else "未找到匹配结果，可手动编辑后保存"
            )
            self.song_query_status.color = (
                Color.SUCCESS_TEXT if candidates else Color.TEXT_MUTED
            )
        except Exception as ex:
            logger.error("MusicBrainz 查询失败: %s", ex)
            self.song_query_status.value = (
                f"{ex}；仍可手动编辑后保存"
                if "MusicBrainz 服务暂不可用" in str(ex)
                else "查询失败，可手动编辑后保存"
            )
            self.song_query_status.color = Color.DANGER_TEXT
        finally:
            self.song_query_button.disabled = False
            self.page.update()

    def _select_song_candidate(self, candidate: dict):
        """候选只回填表单；点击保存后才持久化。"""
        self.song_title_input.value = candidate.get("title", "")
        self.song_artist_input.value = candidate.get("artist", "")
        self.song_album_input.value = candidate.get("album", "")
        self.song_year_input.value = candidate.get("year", "")
        self.song_detail_mbid = candidate.get("mbid", "")
        self.song_query_status.value = "已选择候选，点击“保存”确认"
        self.song_query_status.color = Color.SUCCESS_TEXT
        self.page.update()

    def _save_song_details(self, e):
        metadata = {
            "custom_title": (self.song_title_input.value or "").strip(),
            "artist": (self.song_artist_input.value or "").strip(),
            "album": (self.song_album_input.value or "").strip(),
            "year": (self.song_year_input.value or "").strip(),
            "musicbrainz_mbid": self.song_detail_mbid,
            "metadata_source": "musicbrainz" if self.song_detail_mbid else "manual",
            "metadata_updated_at": datetime.now().isoformat(),
        }
        if not self.music_service.update_song_metadata(self.song_detail_name, metadata):
            self.show_message("歌曲信息保存失败", "error")
            return
        self.page.pop_dialog()
        self.reload_music_list(keep_scroll=True)
        self.show_message("歌曲信息已保存，源文件未修改", "success")

    def _toggle_select(self, name: str):
        """切换文件选中状态"""
        if name in self.selected_files:
            self.selected_files.remove(name)
            if self.last_selected_name == name:
                self.last_selected_name = None
        else:
            self.selected_files.add(name)
            self.last_selected_name = name
        self._update_stats()
        self.reload_music_list(keep_scroll=True)

    def _update_stats(self):
        """更新统计栏"""
        total = len(self.music_files)
        selected = len(self.selected_files)
        downloaded = sum(1 for s in self.music_files if s.get("is_downloaded", False))
        self.stats_text.value = f"总数 {total} · 已选 {selected} · 已下载 {downloaded}"
        self.download_button.disabled = selected == 0
        self.download_button.text = f"下载（{selected}）" if selected else "下载"
        self.select_all_button.text = (
            "取消全选" if total > 0 and selected == total else "全选"
        )
        self.page.update()

    def reload_music_list(self, keep_scroll=False):
        """重新加载音乐列表"""
        try:
            self.music_files = self.music_service.get_all_songs()
            self.file_list.controls.clear()
            for song in self.music_files:
                self.file_list.controls.append(self.build_file_item(song))
            self._update_stats()
            if not keep_scroll:
                self.page.update()
        except Exception as e:
            logger.error(f"加载音乐列表失败: {e}")

    async def _sync_music_list(self, e):
        """同步音乐列表"""
        if self.is_syncing:
            return
        self.is_syncing = True
        self.sync_button.disabled = True
        self.sync_button.text = "同步中…"
        self.show_message("正在同步...", "info")
        self.page.update()

        try:
            files = await self.music_service.sync_all_sources()
            self._update_sync_status()
            self.reload_music_list()
            self.show_message(f"同步完成，共 {len(files)} 首歌曲", "success")
        except Exception as ex:
            self.show_message(f"同步失败: {str(ex)}", "error")
        finally:
            self.is_syncing = False
            self.sync_button.disabled = False
            self.sync_button.text = "同步"
            self.page.update()

    def _search_music(self, e):
        """搜索音乐"""
        query = self.search_input.value.strip() if self.search_input.value else ""
        try:
            self.music_files = self.music_service.search_songs(query)
            self.file_list.controls.clear()
            for song in self.music_files:
                self.file_list.controls.append(self.build_file_item(song))
            self._update_stats()
            self.page.update()
        except Exception as ex:
            logger.error(f"搜索失败: {ex}")

    def _select_all(self, e):
        """全选/取消全选"""
        if len(self.selected_files) == len(self.music_files):
            self.selected_files.clear()
        else:
            self.selected_files = {s.get("name", "") for s in self.music_files}
        self.reload_music_list(keep_scroll=True)

    def _delete_selected(self, e):
        """从本地音乐库索引移除选中歌曲。"""
        if not self.selected_files:
            self.show_message("请先选择要删除的音乐", "warning")
            return
        for name in list(self.selected_files):
            if self.music_service.has_song(name):
                self.music_service.remove_song(name)
        self.selected_files.clear()
        self.last_selected_name = None
        self.reload_music_list()
        self.show_message("已从音乐库移除", "success")

    def _get_selected_start_index(self, selected_files: List[Dict[str, Any]]) -> int:
        """计算播放起始索引：从最后点选的歌曲开始，未命中则回到 0"""
        if self.last_selected_name:
            for i, song in enumerate(selected_files):
                if song.get("name") == self.last_selected_name:
                    return i
        return 0

    def _add_to_playlist(self, e):
        """添加选中文件到播放列表"""
        if not self.selected_files:
            self.show_message("请先选择文件", "warning")
            return
        playback_view = self.view_manager.get_view("playback")
        selected_files = [
            s for s in self.music_files if s.get("name") in self.selected_files
        ]
        if playback_view:
            playback_view.handle_play_selected(
                selected_files,
                start_index=self._get_selected_start_index(selected_files),
            )
            self.view_manager.switch_to_view("playback")
            self.show_message(
                f"已添加 {len(selected_files)} 首歌曲到播放列表", "success"
            )

    def _play_selected(self, e):
        """播放选中文件（从最后点选的歌曲开始）"""
        if not self.selected_files:
            self.show_message("请先选择文件", "warning")
            return
        playback_view = self.view_manager.get_view("playback")
        selected_files = [
            s for s in self.music_files if s.get("name") in self.selected_files
        ]
        if playback_view:
            playback_view.handle_play_selected(
                selected_files,
                start_index=self._get_selected_start_index(selected_files),
            )
            self.view_manager.switch_to_view("playback")

    async def _download_selected(self, e):
        """把选中的文件排入下载队列，由后台 worker 整批处理"""
        if not self.selected_files:
            return

        added = 0
        for name in list(self.selected_files):
            song = next((s for s in self.music_files if s.get("name") == name), None)
            if song and not song.get("is_downloaded", False):
                remote_path = song.get("remote_path", "")
                if remote_path:
                    self._pending_downloads.append((name, remote_path))
                    added += 1

        self.selected_files.clear()
        self.reload_music_list()

        if not added:
            self.show_message("选中歌曲均已下载", "info")
            return

        self.show_message(f"开始下载 {added} 首歌曲...", "info")
        self._start_download_worker()

    def _start_download_worker(self):
        """启动下载 worker；已在运行时不重复启动（新排队项由现有 worker 继续消化）"""
        if self._download_task and not self._download_task.done():
            return
        self._download_task = asyncio.create_task(self._download_worker())

    async def _download_worker(self):
        """整批消化下载队列。

        music_service.download_batch 在原生可用时把整批一次性提交给
        系统后台会话：切后台/锁屏/应用被杀都继续，每首完成经
        on_complete 回调刷新列表。requests 回退路径逐首执行，进程挂
        起会中断协程，未完成项留在 _pending_downloads，回前台由
        on_app_resumed() 重新拉起 worker 续传。
        """
        from ..ios_background_task import begin_background_task, end_background_task

        # 申请约 30s 的 iOS 后台宽限，尽量让收尾处理在切后台后完成
        bg_token = begin_background_task("music-download")
        success_count = 0
        failed_count = 0
        try:
            while self._pending_downloads:
                raw_batch = self._pending_downloads.copy()
                self._pending_downloads.clear()
                # 队列存 (name, remote_path)，服务层约定 (remote_path, filename)
                batch = [(remote_path, name) for name, remote_path in raw_batch]
                try:
                    success, failed = await self.music_service.download_batch(
                        batch, on_complete=self._on_download_file_complete
                    )
                except asyncio.CancelledError:
                    # 取消时把本批放回队首，续传时从这里继续
                    self._pending_downloads[:0] = raw_batch
                    raise
                success_count += success
                failed_count += failed
                self.reload_music_list(keep_scroll=True)
        finally:
            end_background_task(bg_token)

        if success_count or failed_count:
            message = f"下载完成，成功 {success_count} 首"
            message_type = "success"
            if failed_count:
                message += f"，失败 {failed_count} 首"
                message_type = "warning"
            self.show_message(message, message_type)
            self.reload_music_list()

    def _on_download_file_complete(self, filename: str, success: bool):
        """单首下载完成：刷新列表的已下载标识（事件循环线程调用）"""
        try:
            self.reload_music_list(keep_scroll=True)
        except Exception as e:
            logger.debug(f"下载完成刷新列表失败: {e}")

    def on_app_resumed(self):
        """应用回到前台：下载队列有剩余且 worker 已死时自动续传"""
        if not self._pending_downloads:
            return
        if self._download_task and not self._download_task.done():
            return
        remaining = len(self._pending_downloads)
        logger.info(f"应用回到前台，续传下载剩余 {remaining} 首")
        self.show_message(f"继续下载剩余 {remaining} 首...", "info")
        self._start_download_worker()

    def show_message(self, message: str, message_type: str = "info"):
        """在页面顶部显示消息。"""
        show_snack_bar(self.page, message, message_type)

    def on_view_activated(self):
        """视图激活时刷新列表"""
        self.reload_music_list()
        self._update_sync_status()
        self.page.update()

    @staticmethod
    def _source_label(source_type: str) -> str:
        return {
            "nextcloud": "Nextcloud",
            "smb": "SMB",
            "gdrive": "Google Drive",
        }.get(source_type, source_type or "未知来源")

    def _sync_report(self) -> List[Dict[str, Any]]:
        """返回最近同步报告；尚未同步时也列出所有已配置目录。"""
        report = getattr(self.music_service, "last_sync_report", None) or []
        if report:
            return report
        result = []
        for source_type in self.music_service.source_clients:
            for folder in self.music_service.get_sync_folders(source_type):
                result.append(
                    {
                        "source_type": source_type,
                        "folder": folder,
                        "folder_label": self.music_service.get_sync_folder_label(
                            source_type, folder
                        ),
                        "song_count": 0,
                        "synced_count": 0,
                        "status": "pending",
                        "error": "",
                    }
                )
        return result

    def _update_sync_status(self):
        report = self._sync_report()
        synced = sum(int(item.get("synced_count", 0)) for item in report)
        total = sum(int(item.get("song_count", 0)) for item in report)
        self.folder_text.value = f"同步状态：{synced}/{total}"

    def _show_sync_details(self, e):
        report = self._sync_report()
        rows = []
        for item in report:
            ok = item.get("status") == "success"
            pending = item.get("status") == "pending"
            status = "等待同步" if pending else ("同步完成" if ok else "同步失败")
            count = int(item.get("song_count", 0))
            subtitle = (
                f"{item.get('folder_label') or item.get('folder') or '/'}"
                f"\n歌曲数量：{count} · {status}"
            )
            if item.get("error"):
                subtitle += f"\n{item['error']}"
            rows.append(
                ft.ListTile(
                    leading=ft.Icon(
                        (
                            ft.Icons.SCHEDULE
                            if pending
                            else (
                                ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE
                            )
                        ),
                        color=(
                            Color.TEXT_MUTED
                            if pending
                            else (Color.SUCCESS if ok else Color.DANGER_TEXT)
                        ),
                    ),
                    title=ft.Text(self._source_label(item.get("source_type", ""))),
                    subtitle=ft.Text(subtitle),
                )
            )
        if not rows:
            rows.append(ft.Text("尚未配置同步目录", color=Color.TEXT_MUTED))
        dialog = ft.AlertDialog(
            title=ft.Text("同步目录详情"),
            content=ft.Container(
                content=ft.Column(rows, tight=True, scroll=ft.ScrollMode.AUTO),
                width=420,
                height=min(420, max(100, len(rows) * 96)),
            ),
            actions=[
                ft.TextButton("关闭", on_click=lambda event: self.page.pop_dialog())
            ],
        )
        self.page.show_dialog(dialog)
