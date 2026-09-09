"""
设置视图 - 菜单式二级导航：首页为功能菜单列表，点击进入对应子页面
（下载进度 / 缓存管理 / 应用日志 / 谷歌云盘 / 应用信息）
"""

import asyncio
import logging
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

import flet as ft

from ..utils.log_buffer import (
    LOG_FILE_NAME,
    clear_buffer,
    get_buffered_log_count,
    get_recent_logs,
    set_log_level,
)
from ..utils.notify import show_snack_bar
from ..utils.theme import Color, FontSize, Radius, Space, tint

logger = logging.getLogger(__name__)

_LOG_LINE_LIMIT = 300
_LOG_INITIAL_LINES = 40
_LOG_PAGE_LINES = 50

# 菜单入口 -> 子页面标识
_SUB_PAGES = ("download", "cache", "logs", "gdrive", "about")


def _package_version(name: str) -> str:
    try:
        return pkg_version(name)
    except PackageNotFoundError:
        return "dev"


def _dark_input(**kwargs) -> ft.TextField:
    """统一暗色科技风输入框样式（与连接页保持一致）"""
    base = dict(
        bgcolor=Color.BG_SURFACE_ALT,
        border_color=Color.BORDER,
        focused_border_color=Color.PRIMARY,
        border_width=1,
        border_radius=Radius.MD,
        color=Color.TEXT_PRIMARY,
        hint_style=ft.TextStyle(color=Color.TEXT_DISABLED),
        label_style=ft.TextStyle(color=Color.TEXT_SECONDARY),
        cursor_color=Color.PRIMARY,
        content_padding=ft.Padding(left=14, right=14, top=12, bottom=12),
    )
    base.update(kwargs)
    return ft.TextField(**base)


class SettingsView:
    """设置视图（菜单 + 子页面）"""

    def __init__(self, page: ft.Page, app_context: dict, view_manager):
        self.page = page
        self.app_context = app_context
        self.view_manager = view_manager
        self._built = False
        self._log_lines: list[str] = []
        self._log_file: Path | None = None
        self._visible_log_limit = _LOG_INITIAL_LINES
        self._log_tail_task = None
        self._view_active = False
        self._loading_older_logs = False
        self._cached_songs: list[dict] = []
        self._selected_cache_names: set[str] = set()
        # 当前所在页面："menu" 或 _SUB_PAGES 之一
        self._sub_page = "menu"
        # 菜单页各入口的副标题控件（每次渲染重建），供轮询刷新
        self._menu_subtitles: dict[str, ft.Text] = {}
        # 下载列表最近一次渲染签名（避免无变化时整列表重建）
        self._download_list_signature = None

    def rebuild(self):
        """重建视图（Flet 0.86 控件脱离页面后被冻结且不可复用）。

        保留当前子页面状态：从其它标签页切回设置时，停留 在离开前的页面。
        """
        self._cancel_log_tail()
        self._built = False
        return self.build()

    # ------------------------------------------------------------------
    # 页面骨架与导航
    # ------------------------------------------------------------------

    def build(self):
        """构建并返回视图内容（根据 _sub_page 渲染当前页面）"""
        if self._built and hasattr(self, "_container"):
            return self._container

        self._container = ft.Container(
            padding=Space.LG,
            expand=True,
            bgcolor=Color.BG_APP,
        )
        self._built = True
        self._render()
        return self._container

    def _render(self):
        """把当前子页面的内容（全新控件）填充进根容器"""
        builders = {
            "menu": self._build_menu_page,
            "download": self._build_download_page,
            "cache": self._build_cache_page,
            "logs": self._build_logs_page,
            "gdrive": self._build_gdrive_page,
            "about": self._build_about_page,
        }
        builder = builders.get(self._sub_page, self._build_menu_page)
        self._container.content = builder()

    def _open_sub_page(self, name: str):
        """进入子页面：重建内容并刷新该页数据"""
        if name not in _SUB_PAGES:
            return
        self._sub_page = name
        self._render()
        if name == "logs":
            self._visible_log_limit = _LOG_INITIAL_LINES
        self._refresh_page_data()
        self._try_page_update()

    def _back_to_menu(self, e=None):
        """返回设置菜单页"""
        self._sub_page = "menu"
        self._render()
        self._refresh_page_data()
        self._try_page_update()

    def _refresh_page_data(self):
        """刷新当前页面依赖的动态数据（控件存在时才生效）"""
        if self._sub_page == "menu":
            self._refresh_menu_subtitles()
        elif self._sub_page == "download":
            self._refresh_download_progress()
        elif self._sub_page == "cache":
            self._refresh_cache_list()
        elif self._sub_page == "logs":
            self._refresh_logs(limit=self._visible_log_limit)

    def _try_page_update(self):
        try:
            self.page.update()
        except Exception:
            pass

    def _page_scaffold(self, header: ft.Control, *cards: ft.Control) -> ft.ListView:
        """子页面骨架：头部（返回 + 标题）+ 内容卡片"""
        return ft.ListView(
            controls=[header, *cards],
            spacing=Space.MD,
            expand=True,
            padding=0,
        )

    def _menu_header(self) -> ft.Row:
        return self._titled_header(ft.Icons.TUNE, "设置", "SETTINGS · 下载、诊断与日志")

    def _detail_header(self, title: str, subtitle: str) -> ft.Row:
        """子页面头部：返回按钮 + 标题"""
        back = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_IOS_NEW,
            icon_color=Color.PRIMARY,
            icon_size=18,
            tooltip="返回设置",
            on_click=self._back_to_menu,
            style=ft.ButtonStyle(
                bgcolor=Color.BG_SURFACE,
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )
        return ft.Row(
            [back, self._title_block(title, subtitle)],
            spacing=Space.SM,
        )

    def _titled_header(self, icon: str, title: str, subtitle: str) -> ft.Row:
        return ft.Row(
            [
                ft.Container(
                    content=ft.Icon(icon, color=Color.PRIMARY, size=20),
                    width=36,
                    height=36,
                    border_radius=Radius.SM,
                    bgcolor=tint(Color.PRIMARY, "14"),
                    border=ft.Border.all(1, tint(Color.PRIMARY, "33")),
                ),
                self._title_block(title, subtitle),
            ],
            spacing=Space.MD,
        )

    def _title_block(self, title: str, subtitle: str) -> ft.Column:
        return ft.Column(
            [
                ft.Text(
                    title,
                    size=FontSize.TITLE + 4,
                    weight=ft.FontWeight.BOLD,
                    color=Color.TEXT_PRIMARY,
                ),
                ft.Text(
                    subtitle,
                    size=FontSize.MICRO,
                    color=Color.TEXT_MUTED,
                    style=ft.TextStyle(letter_spacing=2),
                ),
            ],
            spacing=0,
        )

    # ------------------------------------------------------------------
    # 菜单页
    # ------------------------------------------------------------------

    def _build_menu_page(self) -> ft.ListView:
        self._menu_subtitles = {}

        def entry(icon: str, title: str, subtitle: str, page_id: str) -> ft.Control:
            subtitle_text = ft.Text(
                subtitle,
                size=FontSize.CAPTION,
                color=Color.TEXT_MUTED,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
            self._menu_subtitles[page_id] = subtitle_text
            return ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            content=ft.Icon(icon, color=Color.PRIMARY, size=20),
                            width=36,
                            height=36,
                            border_radius=Radius.SM,
                            bgcolor=tint(Color.PRIMARY, "14"),
                            border=ft.Border.all(1, tint(Color.PRIMARY, "33")),
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    title,
                                    size=FontSize.BODY + 1,
                                    weight=ft.FontWeight.W_500,
                                    color=Color.TEXT_PRIMARY,
                                ),
                                subtitle_text,
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.Icon(
                            ft.Icons.CHEVRON_RIGHT,
                            color=Color.TEXT_MUTED,
                            size=18,
                        ),
                    ],
                    spacing=Space.MD,
                ),
                padding=Space.MD,
                bgcolor=Color.BG_SURFACE,
                border=ft.Border.all(1, Color.BORDER),
                border_radius=Radius.LG,
                ink=True,
                on_click=lambda e, pid=page_id: self._open_sub_page(pid),
            )

        self.musicbrainz_switch = ft.Switch(
            label="启用 MusicBrainz 在线查询",
            value=bool(
                self.app_context["config_manager"].get(
                    "metadata.musicbrainz_enabled", True
                )
            ),
            on_change=self._on_musicbrainz_enabled_change,
            active_color=Color.PRIMARY,
            label_text_style=ft.TextStyle(color=Color.TEXT_PRIMARY),
        )
        musicbrainz_card = ft.Container(
            content=ft.Column(
                [
                    self.musicbrainz_switch,
                    ft.Text(
                        "关闭后仍可编辑并保存歌曲信息，只禁用 MusicBrainz 联网查询",
                        size=FontSize.CAPTION,
                        color=Color.TEXT_MUTED,
                    ),
                ],
                spacing=Space.XS,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
        )

        entries = [
            entry(
                ft.Icons.DOWNLOAD_ROUNDED,
                "下载进度",
                "暂无下载任务",
                "download",
            ),
            entry(
                ft.Icons.STORAGE,
                "缓存管理",
                self._cache_summary(),
                "cache",
            ),
            entry(
                ft.Icons.TERMINAL,
                "应用日志",
                self._log_summary(),
                "logs",
            ),
            entry(
                ft.Icons.ADD_TO_DRIVE,
                "谷歌云盘",
                self._gdrive_summary(),
                "gdrive",
            ),
            entry(
                ft.Icons.INFO_OUTLINED,
                "应用信息",
                f"v{_package_version('cloud-music-player')} · "
                f"Flet {_package_version('flet')}",
                "about",
            ),
        ]

        return ft.ListView(
            controls=[self._menu_header(), musicbrainz_card, *entries],
            spacing=Space.MD,
            expand=True,
            padding=0,
        )

    def _on_musicbrainz_enabled_change(self, e):
        """立即保存 MusicBrainz 在线查询开关。"""
        cm = self.app_context["config_manager"]
        cm.set("metadata.musicbrainz_enabled", bool(e.control.value))
        cm.save_config()
        self.show_message(
            (
                "MusicBrainz 在线查询已开启"
                if e.control.value
                else "MusicBrainz 在线查询已关闭"
            ),
            "success" if e.control.value else "info",
        )

    def _cache_summary(self) -> str:
        music_service = self.app_context.get("music_service")
        getter = getattr(music_service, "get_cached_songs", None)
        if not callable(getter):
            return "查看已下载到本机的音乐"
        songs = getter()
        total = sum(item.get("size", 0) for item in songs)
        return f"{len(songs)} 首 · {self._format_bytes(total)}"

    def _log_summary(self) -> str:
        level = str(
            self.app_context["config_manager"].get("app.log_level", "INFO")
        ).upper()
        if level not in ("INFO", "DEBUG"):
            level = "INFO"
        return f"级别 {level} · 内存缓冲 {get_buffered_log_count()} 条"

    def _refresh_menu_subtitles(self) -> bool:
        """刷新菜单页副标题（下载状态实时、其余按需），返回是否有变化"""
        changed = False
        subtitle = self._menu_subtitles.get("download")
        if subtitle is not None:
            text = self._download_menu_summary()
            if subtitle.value != text:
                subtitle.value = text
                changed = True
        return changed

    def _download_menu_summary(self) -> str:
        music_service = self.app_context.get("music_service")
        tracker = getattr(music_service, "download_progress", None)
        if tracker is None:
            return "暂无下载任务"
        state = tracker.snapshot()
        status = state["status"]
        if status == "downloading":
            text = f"正在下载 {state['downloading']} 首"
            if state["speed_bps"]:
                text += f" · {self._format_bytes(state['speed_bps'])}/s"
        elif status == "queued":
            text = f"等待下载 {state['queued']} 首"
        elif status == "partial":
            text = f"完成 {state['completed']} 首 · 失败 {state['failed']} 首"
        elif status == "completed":
            text = f"下载完成 {state['completed']} 首"
        elif status == "failed":
            text = f"下载失败 {state['failed']} 首"
        else:
            text = "暂无下载任务"
        if status in ("downloading", "queued") and state["queued"]:
            text += f" · 排队 {state['queued']}"
        return text

    # ------------------------------------------------------------------
    # 下载进度页
    # ------------------------------------------------------------------

    _DL_STATUS_LABELS = {
        "queued": "等待中",
        "downloading": "下载中",
        "completed": "完成",
        "failed": "失败",
    }
    _DL_STATUS_ICONS = {
        "queued": (ft.Icons.HOURGLASS_TOP, Color.TEXT_MUTED),
        "downloading": (ft.Icons.DOWNLOAD_ROUNDED, Color.PRIMARY),
        "completed": (ft.Icons.CHECK_CIRCLE_OUTLINE, Color.SUCCESS),
        "failed": (ft.Icons.ERROR_OUTLINE, Color.DANGER),
    }

    def _build_download_page(self) -> ft.ListView:
        header = self._detail_header("下载进度", "DOWNLOAD · 队列与实时状态")

        self.download_status_text = ft.Text(
            "暂无下载任务",
            size=FontSize.BODY + 1,
            weight=ft.FontWeight.W_500,
            color=Color.TEXT_PRIMARY,
        )
        self.download_filename_text = ft.Text(
            "从文件页选择歌曲后，可在这里查看实时进度",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.download_progress_bar = ft.ProgressBar(
            value=0,
            color=Color.PRIMARY,
            bgcolor=Color.BORDER,
        )
        self.download_progress_text = ft.Text(
            "0 B / 0 B",
            size=FontSize.CAPTION,
            color=Color.TEXT_SECONDARY,
        )
        self.download_queue_text = ft.Text(
            "等待 0 · 完成 0 · 失败 0",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
        )
        download_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.DOWNLOAD_ROUNDED,
                                size=18,
                                color=Color.PRIMARY,
                            ),
                            self.download_status_text,
                        ],
                        spacing=Space.SM,
                    ),
                    self.download_filename_text,
                    self.download_progress_bar,
                    ft.Row(
                        [self.download_progress_text, self.download_queue_text],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                ],
                spacing=Space.SM,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
        )

        # 下载列表（每首一行：状态图标 + 文件名 + 进度/结果）
        self.download_list = ft.ListView(
            height=280,
            spacing=2,
            padding=Space.XS,
        )
        self.download_list_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.LIST_ALT, size=16, color=Color.PRIMARY),
                            ft.Text(
                                "下载列表",
                                size=FontSize.BODY + 1,
                                weight=ft.FontWeight.W_500,
                                color=Color.TEXT_PRIMARY,
                            ),
                        ],
                        spacing=Space.SM,
                    ),
                    ft.Container(
                        content=self.download_list,
                        bgcolor=Color.BG_APP_ALT,
                        border=ft.Border.all(1, Color.BORDER),
                        border_radius=Radius.MD,
                        padding=Space.XS,
                    ),
                ],
                spacing=Space.SM,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
        )

        hint_card = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.CLOUD_DONE_OUTLINED,
                        size=16,
                        color=Color.TEXT_MUTED,
                    ),
                    ft.Text(
                        "下载由系统后台执行：切后台、锁屏不影响进行中的任务",
                        size=FontSize.CAPTION,
                        color=Color.TEXT_MUTED,
                    ),
                ],
                spacing=Space.SM,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_APP_ALT,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
        )

        # 页面重建后列表签名重置，强制首轮重绘
        self._download_list_signature = None
        return self._page_scaffold(
            header, download_card, self.download_list_card, hint_card
        )

    def _download_list_row(self, state: dict) -> ft.Row:
        """单个文件的下载行：状态图标 + 文件名 + 进度/结果"""
        status = state.get("status", "queued")
        icon, color = self._DL_STATUS_ICONS.get(
            status, (ft.Icons.INSERT_DRIVE_FILE_OUTLINED, Color.TEXT_MUTED)
        )
        label = self._DL_STATUS_LABELS.get(status, status)
        downloaded = state.get("downloaded_bytes", 0)
        total = state.get("total_bytes", 0)
        if status == "failed" and state.get("message"):
            detail = str(state["message"])[:48]
        elif total:
            detail = f"{self._format_bytes(downloaded)} / {self._format_bytes(total)}"
            if status == "downloading":
                detail += f" · {downloaded / total:.0%}"
        elif downloaded:
            detail = self._format_bytes(downloaded)
        else:
            detail = label
        elapsed = state.get("elapsed_seconds", 0)
        eta = state.get("eta_seconds", 0)
        if elapsed and status == "downloading":
            detail += f" · 用时 {self._format_duration(elapsed)}"
            if eta:
                detail += f" · 剩余约 {self._format_duration(eta)}"
        elif elapsed and status in ("completed", "failed"):
            detail += f" · 共 {self._format_duration(elapsed)}"
        return ft.Row(
            [
                ft.Icon(icon, size=18, color=color),
                ft.Text(
                    state.get("filename", ""),
                    size=FontSize.BODY,
                    color=Color.TEXT_PRIMARY,
                    expand=True,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    detail,
                    size=FontSize.CAPTION,
                    color=(
                        color
                        if status in ("downloading", "failed")
                        else Color.TEXT_SECONDARY
                    ),
                ),
            ],
            spacing=Space.MD,
        )

    # ------------------------------------------------------------------
    # 缓存管理页
    # ------------------------------------------------------------------

    def _build_cache_page(self) -> ft.ListView:
        header = self._detail_header("缓存管理", "STORAGE · 已下载音乐")

        self.cache_summary_text = ft.Text(
            "0 首 · 0 B", size=FontSize.CAPTION, color=Color.TEXT_SECONDARY
        )
        self.cache_selection_text = ft.Text(
            "已选 0 首", size=FontSize.CAPTION, color=Color.TEXT_MUTED
        )
        self.cache_list = ft.Column(spacing=Space.XS)
        self.select_all_cache_checkbox = ft.Checkbox(
            label="全选", value=False, on_change=self._toggle_all_cache
        )
        self.clear_selected_cache_button = ft.OutlinedButton(
            "清理所选",
            icon=ft.Icons.DELETE_OUTLINE,
            disabled=True,
            on_click=self._clear_selected_cache,
            style=ft.ButtonStyle(
                color=Color.DANGER_TEXT,
                icon_color=Color.DANGER,
                side=ft.BorderSide(1, tint(Color.DANGER, "40")),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )
        cache_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.STORAGE, size=18, color=Color.PRIMARY),
                            ft.Text(
                                "已下载音乐",
                                size=FontSize.BODY + 1,
                                weight=ft.FontWeight.W_500,
                                color=Color.TEXT_PRIMARY,
                            ),
                            self.cache_summary_text,
                        ],
                        spacing=Space.SM,
                    ),
                    ft.Text(
                        "查看已下载到本机的音乐，可按需选择清理",
                        size=FontSize.CAPTION,
                        color=Color.TEXT_MUTED,
                    ),
                    self.cache_list,
                    ft.Row(
                        [
                            self.select_all_cache_checkbox,
                            self.cache_selection_text,
                            self.clear_selected_cache_button,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        wrap=True,
                    ),
                ],
                spacing=Space.SM,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
        )

        return self._page_scaffold(header, cache_card)

    # ------------------------------------------------------------------
    # 应用日志页
    # ------------------------------------------------------------------

    def _build_logs_page(self) -> ft.ListView:
        header = self._detail_header("应用日志", "LOGS · 级别与实时输出")

        current_level = str(
            self.app_context["config_manager"].get("app.log_level", "INFO")
        ).upper()
        if current_level not in ("INFO", "DEBUG"):
            current_level = "INFO"
        self.level_selector = ft.SegmentedButton(
            selected=[current_level],
            segments=[
                ft.Segment(value="INFO", label="INFO", icon=ft.Icons.INFO_OUTLINED),
                ft.Segment(
                    value="DEBUG", label="DEBUG", icon=ft.Icons.BUG_REPORT_OUTLINED
                ),
            ],
            allow_multiple_selection=False,
            allow_empty_selection=False,
            on_change=self._on_log_level_change,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.SELECTED: tint(Color.PRIMARY, "26"),
                    ft.ControlState.DEFAULT: Color.BG_SURFACE_ALT,
                },
                color={
                    ft.ControlState.SELECTED: Color.PRIMARY,
                    ft.ControlState.DEFAULT: Color.TEXT_SECONDARY,
                },
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.MD),
            ),
        )

        self.log_path_text = ft.Text(
            "日志文件: -",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.log_meta_text = ft.Text(
            "0 行",
            size=FontSize.CAPTION,
            color=Color.TEXT_SECONDARY,
        )
        self.log_list = ft.ListView(
            height=330,
            spacing=0,
            auto_scroll=True,
            auto_scroll_animation=0,
            on_scroll=self._on_log_scroll,
            scroll_interval=100,
            padding=Space.SM,
        )

        self.refresh_button = ft.OutlinedButton(
            "刷新",
            icon=ft.Icons.REFRESH,
            on_click=self._refresh_clicked,
            style=ft.ButtonStyle(
                color=Color.TEXT_SECONDARY,
                icon_color=Color.PRIMARY,
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )
        self.copy_button = ft.OutlinedButton(
            "复制",
            icon=ft.Icons.CONTENT_COPY,
            on_click=self._copy_logs,
            style=ft.ButtonStyle(
                color=Color.TEXT_SECONDARY,
                icon_color=Color.INFO,
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )
        self.share_button = ft.OutlinedButton(
            "分享",
            icon=ft.Icons.SHARE,
            on_click=self._share_logs,
            style=ft.ButtonStyle(
                color=Color.TEXT_SECONDARY,
                icon_color=Color.ACCENT,
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )
        self.clear_button = ft.OutlinedButton(
            "清空",
            icon=ft.Icons.DELETE_SWEEP,
            on_click=self._clear_logs,
            style=ft.ButtonStyle(
                color=Color.DANGER_TEXT,
                icon_color=Color.DANGER,
                side=ft.BorderSide(1, tint(Color.DANGER, "40")),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )

        log_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.TERMINAL,
                                size=16,
                                color=Color.SUCCESS,
                            ),
                            ft.Text(
                                "运行日志",
                                size=FontSize.BODY + 1,
                                weight=ft.FontWeight.W_500,
                                color=Color.TEXT_PRIMARY,
                            ),
                        ],
                        spacing=Space.SM,
                    ),
                    ft.Row([self.level_selector], spacing=Space.XS),
                    self.log_path_text,
                    ft.Container(
                        content=self.log_list,
                        expand=True,
                        bgcolor=Color.BG_APP_ALT,
                        border=ft.Border.all(1, Color.BORDER),
                        border_radius=Radius.MD,
                    ),
                    self.log_meta_text,
                    ft.Row(
                        [
                            self.refresh_button,
                            self.copy_button,
                            self.share_button,
                            self.clear_button,
                        ],
                        spacing=Space.XS,
                        wrap=True,
                    ),
                ],
                spacing=Space.SM,
                expand=True,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
            expand=True,
        )

        return self._page_scaffold(header, log_card)

    # ------------------------------------------------------------------
    # 谷歌云盘设置页
    # ------------------------------------------------------------------

    def _build_gdrive_page(self) -> ft.ListView:
        header = self._detail_header("谷歌云盘", "GDRIVE · 服务端点")

        self.gdrive_api_base_input = _dark_input(
            key="gdrive_api_base",
            label="API 地址",
            value=self._gdrive_api_base_config(),
            hint_text="留空使用 Google 官方端点",
            prefix_icon=ft.Icons.LANGUAGE,
        )
        self.gdrive_save_button = ft.OutlinedButton(
            "保存",
            icon=ft.Icons.SAVE_OUTLINED,
            on_click=self._save_gdrive_settings,
            style=ft.ButtonStyle(
                color=Color.PRIMARY,
                icon_color=Color.PRIMARY,
                side=ft.BorderSide(1, tint(Color.PRIMARY, "40")),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )
        endpoint_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.ADD_TO_DRIVE,
                                size=18,
                                color=Color.PRIMARY,
                            ),
                            ft.Text(
                                "服务端点",
                                size=FontSize.BODY + 1,
                                weight=ft.FontWeight.W_500,
                                color=Color.TEXT_PRIMARY,
                            ),
                        ],
                        spacing=Space.SM,
                    ),
                    self.gdrive_api_base_input,
                    self.gdrive_save_button,
                ],
                spacing=Space.SM,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
        )

        hint_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.INFO_OUTLINED,
                                size=16,
                                color=Color.TEXT_MUTED,
                            ),
                            # expand 约束宽度让长文案换行，否则单行文本横向溢出
                            ft.Text(
                                "自定义地址用于代理网关或测试服务器；保存后连接页的"
                                "谷歌云盘授权与同步请求都会改走该地址",
                                size=FontSize.CAPTION,
                                color=Color.TEXT_MUTED,
                                expand=True,
                            ),
                        ],
                        spacing=Space.SM,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    ft.Text(
                        "地址会自动拼接：/drive/v3（文件 API）、/auth（授权页）、"
                        "/token（令牌）。例如 http://192.168.1.10:8931",
                        size=FontSize.CAPTION,
                        color=Color.TEXT_MUTED,
                    ),
                ],
                spacing=Space.SM,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_APP_ALT,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
        )

        return self._page_scaffold(header, endpoint_card, hint_card)

    def _gdrive_api_base_config(self) -> str:
        return str(
            self.app_context["config_manager"].get("connection.gdrive.api_base_url", "")
            or ""
        )

    def _gdrive_summary(self) -> str:
        base = self._gdrive_api_base_config().strip()
        return "使用 Google 官方端点" if not base else f"自定义端点 {base}"

    def _save_gdrive_settings(self, e):
        """保存自定义 API 地址（去空白与尾部斜杠后持久化）"""
        raw = (self.gdrive_api_base_input.value or "").strip().rstrip("/")
        try:
            cm = self.app_context["config_manager"]
            cm.set("connection.gdrive.api_base_url", raw)
            cm.save_config()
        except Exception as ex:
            logger.error(f"保存谷歌云盘端点失败: {ex}")
            self.show_message("保存失败，请查看应用日志", "error")
            return
        self.gdrive_api_base_input.value = raw
        logger.info(f"谷歌云盘 API 地址已保存: {raw or '(官方端点)'}")
        self.show_message("谷歌云盘设置已保存", "success")

    # ------------------------------------------------------------------
    # 应用信息页
    # ------------------------------------------------------------------

    def _build_about_page(self) -> ft.ListView:
        header = self._detail_header("应用信息", "ABOUT · 版本与环境")

        platform_name = str(getattr(self.page, "platform", "") or sys.platform)
        info_rows = ft.Column(
            [
                self._info_row(
                    "平台", f"{platform_name} · Python {sys.version.split()[0]}"
                ),
                self._info_row("Flet 版本", _package_version("flet")),
                self._info_row("应用版本", _package_version("cloud-music-player")),
                ft.Row(
                    [
                        ft.Text(
                            "GitHub",
                            size=FontSize.CAPTION,
                            color=Color.TEXT_MUTED,
                            width=80,
                        ),
                        ft.TextButton(
                            "zgfh/cloud-music-player",
                            icon=ft.Icons.OPEN_IN_NEW,
                            url="https://github.com/zgfh/cloud-music-player",
                        ),
                    ],
                    spacing=Space.SM,
                ),
            ],
            spacing=Space.SM,
        )
        info_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.INFO_OUTLINED,
                                size=16,
                                color=Color.PRIMARY,
                            ),
                            ft.Text(
                                "版本信息",
                                size=FontSize.BODY + 1,
                                weight=ft.FontWeight.W_500,
                                color=Color.TEXT_PRIMARY,
                            ),
                        ],
                        spacing=Space.SM,
                    ),
                    info_rows,
                ],
                spacing=Space.MD,
            ),
            padding=Space.MD,
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
        )

        return self._page_scaffold(header, info_card)

    @staticmethod
    def _info_row(key: str, value: str) -> ft.Row:
        return ft.Row(
            [
                ft.Text(
                    key,
                    size=FontSize.CAPTION,
                    color=Color.TEXT_MUTED,
                    width=80,
                ),
                ft.Text(
                    value,
                    size=FontSize.CAPTION,
                    color=Color.TEXT_SECONDARY,
                    expand=True,
                    selectable=True,
                ),
            ],
            spacing=Space.SM,
        )

    # ------------------------------------------------------------------
    # 日志数据与操作
    # ------------------------------------------------------------------

    def _log_file_path(self) -> Path | None:
        try:
            log_dir = self.app_context["config_manager"].get_log_directory()
            return Path(log_dir) / LOG_FILE_NAME
        except Exception:
            return None

    def _load_log_lines(self, limit: int) -> list[str]:
        """优先读日志文件（含历史会话），文件不可用时退回内存缓冲"""
        self._log_file = self._log_file_path()
        if self._log_file is not None:
            try:
                if self._log_file.exists():
                    return self._read_file_tail(self._log_file, limit)
            except OSError as ex:
                logger.warning(f"读取日志文件失败: {ex}")
        return get_recent_logs(limit)

    @staticmethod
    def _read_file_tail(path: Path, limit: int) -> list[str]:
        """从文件末尾分块读取，避免日志很大时加载整个文件。"""
        if limit <= 0:
            return []
        chunks: list[bytes] = []
        newline_count = 0
        with path.open("rb") as log_file:
            log_file.seek(0, 2)
            position = log_file.tell()
            while position > 0 and newline_count <= limit:
                chunk_size = min(8192, position)
                position -= chunk_size
                log_file.seek(position)
                chunk = log_file.read(chunk_size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
        content = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
        return content.splitlines()[-limit:]

    @staticmethod
    def _log_text(line: str) -> ft.Text:
        color = Color.TEXT_MUTED
        if " - ERROR - " in line or " - CRITICAL - " in line:
            color = Color.DANGER_TEXT
        elif " - WARNING - " in line:
            color = Color.WARNING_TEXT
        elif " - INFO - " in line:
            color = Color.TEXT_SECONDARY
        return ft.Text(
            line if line.strip() else " ",
            size=FontSize.MICRO + 1,
            color=color,
            font_family="monospace",
            selectable=True,
        )

    def _refresh_logs(self, limit: int | None = None):
        """重新加载日志内容到列表（仅日志页控件存在时刷新 UI）"""
        if limit is None:
            limit = self._visible_log_limit
        self._log_lines = self._load_log_lines(limit)
        if not hasattr(self, "log_list"):
            return

        self.log_list.controls.clear()
        for line in self._log_lines:
            self.log_list.controls.append(self._log_text(line))

        if self._log_file is not None:
            self.log_path_text.value = f"日志文件: {self._log_file}"
        else:
            self.log_path_text.value = "日志文件不可用，显示内存缓冲（当前会话）"
        self.log_meta_text.value = f"显示最近 {len(self._log_lines)} 行 · 内存缓冲 {get_buffered_log_count()} 条"
        self._try_page_update()

    def _refresh_clicked(self, e):
        self._refresh_logs(limit=self._visible_log_limit)

    @staticmethod
    def _format_bytes(value: float) -> str:
        value = max(float(value or 0), 0.0)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return "0 B"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(int(seconds or 0), 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    # ------------------------------------------------------------------
    # 下载进度刷新
    # ------------------------------------------------------------------

    def _refresh_download_progress(self) -> bool:
        """从共享 tracker 刷新下载摘要/列表/菜单副标题，返回控件是否变化。"""
        music_service = self.app_context.get("music_service")
        tracker = getattr(music_service, "download_progress", None)
        if tracker is None:
            return False
        changed = self._refresh_menu_subtitles()
        if not hasattr(self, "download_status_text"):
            return changed

        state = tracker.snapshot()
        status = state["status"]
        labels = {
            "idle": "暂无下载任务",
            "queued": f"等待下载 {state['queued']} 首",
            "downloading": f"正在下载 {state['downloading']} 首",
            "partial": f"完成 {state['completed']} · 失败 {state['failed']}",
            "completed": f"下载完成 {state['completed']} 首",
            "failed": f"下载失败 {state['failed']} 首",
        }
        downloaded = state["downloaded_bytes"]
        total = state["total_bytes"]
        speed = state["speed_bps"]
        # ProgressBar.value=None 会显示持续滚动的不确定进度，只能用于确实
        # 正在下载但服务端未返回 Content-Length 的场景。空闲/排队/终态必须静止。
        if status == "downloading":
            progress = min(downloaded / total, 1.0) if total else None
        elif status == "completed" and state["completed"]:
            progress = 1.0
        else:
            progress = 0.0
        filename = state["filename"] or "从文件页选择歌曲后，可在这里查看实时进度"
        progress_text = self._format_bytes(downloaded)
        if total:
            progress_text += f" / {self._format_bytes(total)}"
            if status == "downloading":
                progress_text += f" · {downloaded / total:.0%}"
        if status == "downloading" and speed:
            progress_text += f" · {self._format_bytes(speed)}/s"
        elapsed = state.get("elapsed_seconds", 0)
        eta = state.get("eta_seconds", 0)
        if elapsed:
            progress_text += f" · 用时 {self._format_duration(elapsed)}"
        if status == "downloading" and eta:
            progress_text += f" · 剩余约 {self._format_duration(eta)}"
        values = (
            labels.get(status, status),
            filename,
            progress,
            progress_text,
            f"等待 {state['queued']} · 完成 {state['completed']} · 失败 {state['failed']}",
        )
        old_values = (
            self.download_status_text.value,
            self.download_filename_text.value,
            self.download_progress_bar.value,
            self.download_progress_text.value,
            self.download_queue_text.value,
        )
        if values != old_values:
            self.download_status_text.value = values[0]
            self.download_filename_text.value = values[1]
            self.download_progress_bar.value = values[2]
            self.download_progress_text.value = values[3]
            self.download_queue_text.value = values[4]
            changed = True

        # 下载列表：内容签名变化时整体重建（行内无可复用的动态控件）
        if hasattr(self, "download_list"):
            states = tracker.file_states()
            signature = tuple(
                (
                    item["filename"],
                    item["status"],
                    item["downloaded_bytes"],
                    item["total_bytes"],
                    int(item.get("elapsed_seconds", 0)),
                    int(item.get("eta_seconds", 0)),
                )
                for item in states
            )
            if signature != self._download_list_signature:
                self._download_list_signature = signature
                self.download_list.controls.clear()
                self.download_list.controls.extend(
                    self._download_list_row(item) for item in states
                )
                changed = True
        return changed

    # ------------------------------------------------------------------
    # 缓存管理操作
    # ------------------------------------------------------------------

    def _refresh_cache_list(self):
        if not hasattr(self, "cache_summary_text"):
            return
        music_service = self.app_context.get("music_service")
        getter = getattr(music_service, "get_cached_songs", None)
        self._cached_songs = getter() if callable(getter) else []
        available = {item["name"] for item in self._cached_songs}
        self._selected_cache_names.intersection_update(available)
        total_size = sum(item.get("size", 0) for item in self._cached_songs)
        self.cache_summary_text.value = (
            f"{len(self._cached_songs)} 首 · {self._format_bytes(total_size)}"
        )
        self.cache_list.controls.clear()
        if not self._cached_songs:
            self.cache_list.controls.append(
                ft.Text("暂无已下载音乐", size=FontSize.CAPTION, color=Color.TEXT_MUTED)
            )
        for item in self._cached_songs:
            name = item["name"]
            subtitle = self._format_bytes(item.get("size", 0))
            if item.get("download_time"):
                subtitle += f" · {str(item['download_time']).replace('T', ' ')[:16]}"
            checkbox = ft.Checkbox(
                label=name,
                value=name in self._selected_cache_names,
                data=name,
                on_change=self._toggle_cache_item,
                expand=True,
            )
            self.cache_list.controls.append(
                ft.Row(
                    [
                        checkbox,
                        ft.Text(
                            subtitle, size=FontSize.CAPTION, color=Color.TEXT_MUTED
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            )
        self._update_cache_selection_controls()

    def _update_cache_selection_controls(self):
        count = len(self._selected_cache_names)
        total = len(self._cached_songs)
        self.cache_selection_text.value = f"已选 {count} 首"
        self.clear_selected_cache_button.disabled = count == 0
        self.select_all_cache_checkbox.disabled = total == 0
        self.select_all_cache_checkbox.value = total > 0 and count == total

    def _toggle_cache_item(self, e):
        name = e.control.data
        if e.control.value:
            self._selected_cache_names.add(name)
        else:
            self._selected_cache_names.discard(name)
        self._update_cache_selection_controls()
        self.page.update()

    def _toggle_all_cache(self, e):
        self._selected_cache_names = (
            {item["name"] for item in self._cached_songs} if e.control.value else set()
        )
        self._refresh_cache_list()
        self.page.update()

    def _clear_selected_cache(self, e):
        music_service = self.app_context.get("music_service")
        remover = getattr(music_service, "remove_cached_songs", None)
        if not callable(remover) or not self._selected_cache_names:
            return
        try:
            deleted, freed = remover(sorted(self._selected_cache_names))
            self._selected_cache_names.clear()
            self._refresh_cache_list()
            self.page.update()
            self.show_message(
                f"已清理 {deleted} 首，释放 {self._format_bytes(freed)}", "success"
            )
        except Exception as ex:
            logger.error(f"清理音乐缓存失败: {ex}")
            self.show_message(f"清理失败: {ex}", "error")

    # ------------------------------------------------------------------
    # 日志滚动 / 级别 / 增量跟随
    # ------------------------------------------------------------------

    def _on_log_scroll(self, e):
        """上滑到顶部时按页加载更早日志。"""
        if self._loading_older_logs or self._visible_log_limit >= _LOG_LINE_LIMIT:
            return
        pixels = float(getattr(e, "pixels", 1) or 0)
        minimum = float(getattr(e, "min_scroll_extent", 0) or 0)
        delta = getattr(e, "scroll_delta", None)
        if pixels <= minimum + 24 and (delta is None or delta < 0):
            self._loading_older_logs = True
            try:
                self._visible_log_limit = min(
                    _LOG_LINE_LIMIT, self._visible_log_limit + _LOG_PAGE_LINES
                )
                self._refresh_logs(limit=self._visible_log_limit)
            finally:
                self._loading_older_logs = False

    @staticmethod
    def _new_log_lines(
        old_lines: list[str], latest_lines: list[str]
    ) -> list[str] | None:
        """返回旧尾部之后的新增行；找不到旧尾部表示日志已轮转或清空。"""
        if not old_lines:
            return latest_lines
        last_line = old_lines[-1]
        for index in range(len(latest_lines) - 1, -1, -1):
            if latest_lines[index] == last_line:
                return latest_lines[index + 1 :]
        return None

    async def _tail_logs(self):
        """设置页可见期间轮询：下载状态常刷，日志仅在其页面打开时增量追加。"""
        while self._view_active:
            await asyncio.sleep(1.0)
            if not self._view_active or not self._built:
                break
            try:
                download_changed = self._refresh_download_progress()
                if self._sub_page != "logs":
                    if download_changed:
                        self._try_page_update()
                    continue
                latest = self._load_log_lines(_LOG_LINE_LIMIT)
                new_lines = self._new_log_lines(self._log_lines, latest)
                if new_lines is None:
                    self._refresh_logs(limit=self._visible_log_limit)
                    continue
                if not new_lines:
                    if download_changed:
                        self._try_page_update()
                    continue
                self._log_lines.extend(new_lines)
                if len(self._log_lines) > _LOG_LINE_LIMIT:
                    overflow = len(self._log_lines) - _LOG_LINE_LIMIT
                    del self._log_lines[:overflow]
                    del self.log_list.controls[:overflow]
                self.log_list.controls.extend(
                    self._log_text(line) for line in new_lines
                )
                self.log_meta_text.value = (
                    f"显示最近 {len(self._log_lines)} 行 · "
                    f"内存缓冲 {get_buffered_log_count()} 条"
                )
                self._try_page_update()
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                if "destroyed session" in str(ex).lower():
                    break
                logger.warning(f"增量刷新日志失败: {ex}")

    def _cancel_log_tail(self):
        self._view_active = False
        if self._log_tail_task and not self._log_tail_task.done():
            self._log_tail_task.cancel()
        self._log_tail_task = None

    def _on_log_level_change(self, e):
        """切换日志级别：立即生效并持久化"""
        try:
            selected = next(iter(self.level_selector.selected), "INFO")
        except Exception:
            selected = "INFO"
        if selected not in ("INFO", "DEBUG"):
            return
        set_log_level(selected, self.app_context["config_manager"])
        self.show_message(f"日志级别已切换为 {selected}", "success")
        self._refresh_logs()

    async def _copy_logs(self, e):
        """复制当前显示的日志到系统剪贴板"""
        if not self._log_lines:
            self.show_message("暂无日志可复制", "warning")
            return
        text = "\n".join(self._log_lines)
        try:
            # Clipboard 是 Service，会在构造时自动注册；放入 overlay 会被
            # Flutter 当作可视控件构建并报 "Unknown control: clipboard"。
            clipboard = ft.Clipboard()
            await clipboard.set(text)
            self.show_message(f"已复制 {len(self._log_lines)} 行日志", "success")
        except Exception as ex:
            logger.error(f"复制日志失败: {ex}")
            self.show_message(f"复制失败: {ex}", "error")

    async def _share_logs(self, e):
        """通过系统分享面板分享日志文本（iOS 可 AirDrop/发送给自己）"""
        if not self._log_lines:
            self.show_message("暂无日志可分享", "warning")
            return
        try:
            share = ft.Share()
            await share.share_text(
                "\n".join(self._log_lines),
                title="分享应用日志",
                subject="Cloud Music Player 日志",
            )
        except Exception as ex:
            logger.error(f"分享日志失败: {ex}")
            self.show_message(f"分享失败: {ex}", "error")

    def _clear_logs(self, e):
        """清空内存缓冲并截断日志文件"""
        clear_buffer()
        truncated = False
        if self._log_file is None:
            self._log_file = self._log_file_path()
        if self._log_file is not None and self._log_file.exists():
            try:
                self._log_file.write_text("", encoding="utf-8")
                truncated = True
            except OSError as ex:
                logger.warning(f"清空日志文件失败: {ex}")
        logger.info("应用日志已清空")
        self._refresh_logs()
        self.show_message("日志已清空" if truncated else "内存日志已清空", "info")

    def show_message(self, message: str, message_type: str = "info"):
        """在页面顶部显示消息。"""
        show_snack_bar(self.page, message, message_type)

    def on_view_activated(self):
        """视图激活后刷新当前页面并启动轮询。"""
        self._view_active = True
        self._refresh_page_data()
        self._try_page_update()
        if not self._log_tail_task or self._log_tail_task.done():
            self._log_tail_task = asyncio.create_task(self._tail_logs())

    def on_view_deactivated(self):
        """离开设置页后停止轮询。"""
        self._cancel_log_tail()
