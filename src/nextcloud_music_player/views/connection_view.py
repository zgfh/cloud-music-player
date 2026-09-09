"""
连接配置视图 - Flet 版本
"""

import asyncio
import logging
import os
import threading
import time

import flet as ft

from ..utils.notify import show_snack_bar
from ..utils.theme import (
    Color,
    FontSize,
    Gradient,
    Radius,
    Space,
    glow,
    tint,
)

logger = logging.getLogger(__name__)


def _dark_input(**kwargs) -> ft.TextField:
    """统一暗色科技风输入框样式"""
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


class ConnectionView:
    """NextCloud 连接配置视图"""

    def __init__(self, page: ft.Page, app_context: dict, view_manager):
        self.page = page
        self.app_context = app_context
        self.view_manager = view_manager
        self.password_visible = False
        self.is_connected = False
        self._built = False
        self._save_timer = None
        self._auto_connect_started = False  # 自动连接每会话只触发一次
        self._auto_connect_in_progress = False
        self.source_clients = app_context.setdefault("source_clients", {})

    def rebuild(self):
        """重建视图（Flet 0.86 控件脱离页面后被冻结且不可复用）"""
        self._built = False
        return self.build()

    def build(self):
        """构建并返回视图内容"""
        if self._built and hasattr(self, "_container"):
            return self._container

        try:
            return self._do_build()
        except Exception as e:
            logger.error(f"ConnectionView build 失败: {e}", exc_info=True)
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Text(f"构建错误: {e}", color=Color.DANGER_TEXT),
                    ]
                ),
                padding=20,
                bgcolor=Color.BG_APP,
            )

    def _do_build(self):
        config_manager = self.app_context["config_manager"]
        config = config_manager.get("connection", {})
        smb_config = config.get("smb", {})
        gdrive_config = config.get("gdrive", {})
        source_type = config.get("source_type", "nextcloud")

        # === Hero 头部：渐变徽标 + 标题 ===
        logo = ft.Container(
            content=ft.Icon(ft.Icons.CLOUD_DONE_OUTLINED, color=Color.PRIMARY, size=26),
            width=52,
            height=52,
            border_radius=Radius.LG,
            gradient=Gradient.surface(),
            border=ft.Border.all(1, tint(Color.PRIMARY, "59")),
            shadow=glow(Color.PRIMARY, radius=16, alpha="33"),
        )
        self.title_text = ft.Text(
            {"smb": "SMB", "gdrive": "GOOGLE DRIVE"}.get(source_type, "NEXTCLOUD"),
            size=FontSize.TITLE + 6,
            weight=ft.FontWeight.BOLD,
            color=Color.TEXT_PRIMARY,
            style=ft.TextStyle(letter_spacing=4),
        )
        subtitle_text = ft.Text(
            "MUSIC · 服务器连接",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
            style=ft.TextStyle(letter_spacing=2),
        )
        hero = ft.Row(
            [
                logo,
                ft.Column(
                    [self.title_text, subtitle_text],
                    spacing=2,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            spacing=Space.MD,
        )

        # === 来源类型切换：Nextcloud / SMB / Google Drive ===
        # 注意：Flet 0.86 的 selected 类型是 list[str]，
        # 传 set 会在 iOS 首次序列化时崩溃（msgpack 无法打包 set）
        self.source_selector = ft.SegmentedButton(
            key="source_selector",
            selected=[source_type],
            segments=[
                ft.Segment(
                    value="nextcloud",
                    label="Nextcloud",
                    icon=ft.Icons.CLOUD_OUTLINED,
                ),
                ft.Segment(
                    value="smb",
                    label="SMB 共享",
                    icon=ft.Icons.LAN_OUTLINED,
                ),
                ft.Segment(
                    value="gdrive",
                    label="Google 云盘",
                    icon=ft.Icons.ADD_TO_DRIVE,
                ),
            ],
            allow_multiple_selection=False,
            allow_empty_selection=False,
            on_change=self._on_source_type_changed,
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

        # === 状态胶囊 ===
        self.status_dot = ft.Container(
            width=8,
            height=8,
            border_radius=4,
            bgcolor=Color.DANGER,
            shadow=glow(Color.DANGER, radius=8, alpha="80"),
        )
        self.status_text = ft.Text(
            "未连接",
            size=FontSize.STATUS,
            weight=ft.FontWeight.BOLD,
            color=Color.DANGER_TEXT,
            style=ft.TextStyle(letter_spacing=1),
        )
        self.status_container = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.SATELLITE_ALT, size=16, color=Color.TEXT_MUTED),
                    self.status_dot,
                    self.status_text,
                ],
                spacing=Space.SM,
            ),
            bgcolor=tint(Color.DANGER, "1F"),
            border=ft.Border.all(1, tint(Color.DANGER, "40")),
            padding=ft.Padding(left=14, right=14, top=10, bottom=10),
            border_radius=Radius.CIRCLE,
            width=float("inf"),
        )

        # === 表单字段（暗色填充风格） ===
        self.url_input = _dark_input(
            key="nextcloud_url",
            label="服务器地址",
            value=config.get("server_url", "http://cloud.home.daozzg.com"),
            hint_text="https://your-nextcloud.com",
            prefix_icon=ft.Icons.LANGUAGE,
        )

        self.username_input = _dark_input(
            key="nextcloud_username",
            label="用户名",
            value=config.get("username", "guest"),
            hint_text="输入用户名",
            prefix_icon=ft.Icons.PERSON_OUTLINE,
        )

        self.password_input = _dark_input(
            key="nextcloud_password",
            label="密码",
            value=(
                config.get("password", "")
                if config.get("remember_credentials", True)
                else ""
            ),
            hint_text="输入密码",
            password=True,
            can_reveal_password=True,
            prefix_icon=ft.Icons.KEY,
        )

        self.sync_folder_input = _dark_input(
            key="nextcloud_sync_folder",
            label="同步文件夹（多个路径用换行分隔）",
            value="\n".join(
                config.get("sync_folders")
                or [
                    config.get(
                        "default_sync_folder", "/mp3/音乐/当月抖音热播流行歌曲484首/"
                    )
                ]
            ),
            hint_text="/Music\n/Podcasts",
            prefix_icon=ft.Icons.FOLDER_OUTLINED,
            on_change=self._on_sync_folder_changed,
            multiline=True,
            min_lines=2,
            max_lines=4,
            expand=1,
        )

        self.browse_button = ft.OutlinedButton(
            "浏览",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._browse_folder,
            style=ft.ButtonStyle(
                bgcolor=Color.BG_SURFACE,
                color=Color.TEXT_SECONDARY,
                icon_color=Color.PRIMARY,
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.MD),
            ),
        )

        # === SMB 来源：向导式连接 ===
        # 只暴露一个地址框，其余参数（凭据/共享/目录）由向导引导选择，
        # 结果保存在 _smb_settings 并持久化，供自动连接与测试使用
        self._smb_settings = {
            "host": smb_config.get("host", ""),
            "port": smb_config.get("port", 445),
            "domain": smb_config.get("domain", ""),
            "username": (
                smb_config.get("username", "")
                if config.get("remember_credentials", True)
                else ""
            ),
            "password": (
                smb_config.get("password", "")
                if config.get("remember_credentials", True)
                else ""
            ),
            "share": smb_config.get("share", ""),
            "sync_folder": smb_config.get("default_sync_folder", "/"),
        }

        self.smb_host_input = _dark_input(
            key="smb_host",
            label="服务器地址",
            value=self._smb_settings["host"],
            hint_text="如 192.168.1.100 或 nas.local",
            prefix_icon=ft.Icons.LAN_OUTLINED,
        )
        self.smb_sync_folder_input = _dark_input(
            key="smb_sync_folders",
            label="同步文件夹（多个路径用换行分隔）",
            value="\n".join(
                smb_config.get("sync_folders")
                or [smb_config.get("default_sync_folder", "/")]
            ),
            hint_text="/\n/Music",
            multiline=True,
            min_lines=2,
            max_lines=4,
            expand=1,
        )
        self.smb_browse_button = ft.OutlinedButton(
            "浏览", icon=ft.Icons.FOLDER_OPEN, on_click=self._browse_folder
        )
        self.smb_edit_button = ft.OutlinedButton(
            "修改连接设置",
            icon=ft.Icons.EDIT_OUTLINED,
            on_click=lambda e: self._open_smb_wizard(),
            style=ft.ButtonStyle(
                color=Color.TEXT_SECONDARY,
                icon_color=Color.INFO,
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.MD),
            ),
        )

        smb_hint = ft.Text(
            "点击「建立连接」后，将引导你选择身份（访客/用户）、共享与文件夹",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
        )

        # === Google Drive 来源：OAuth 授权 + 文件夹选择 ===
        # Client ID/Secret 来自用户自建的 Google Cloud OAuth 客户端（桌面应用类型），
        # 「授权」经系统浏览器完成 Google 账号登录（loopback 回调回收授权码），
        # 结果保存在 _gdrive_settings 并持久化，供自动连接与令牌刷新使用
        remember_credentials = config.get("remember_credentials", True)
        self._gdrive_settings = {
            "client_id": gdrive_config.get("client_id", ""),
            "client_secret": (
                gdrive_config.get("client_secret", "") if remember_credentials else ""
            ),
            "refresh_token": (
                gdrive_config.get("refresh_token", "") if remember_credentials else ""
            ),
            "access_token": (
                gdrive_config.get("access_token", "") if remember_credentials else ""
            ),
            "token_expiry": gdrive_config.get("token_expiry", 0),
            "sync_folder": gdrive_config.get("default_sync_folder", ""),
            # 自定义端点在设置页维护；授权/建连时再从配置实时读取，
            # 避免连接页已构建后设置变更不生效
            "api_base_url": gdrive_config.get("api_base_url", ""),
        }
        self._gdrive_folder_ids = list(
            dict.fromkeys(
                str(folder)
                for folder in (
                    gdrive_config.get("sync_folders")
                    or (
                        [self._gdrive_settings["sync_folder"]]
                        if self._gdrive_settings["sync_folder"]
                        else []
                    )
                )
            )
        )
        self._gdrive_folder_labels = dict(gdrive_config.get("sync_folder_labels") or {})

        self.gdrive_client_id_input = _dark_input(
            key="gdrive_client_id",
            label="OAuth Client ID",
            value=self._gdrive_settings["client_id"],
            hint_text="形如 1234-abc.apps.googleusercontent.com",
            prefix_icon=ft.Icons.BADGE_OUTLINED,
        )

        self.gdrive_client_secret_input = _dark_input(
            key="gdrive_client_secret",
            label="OAuth Client Secret",
            value=self._gdrive_settings["client_secret"],
            hint_text="形如 GOCSPX-...",
            password=True,
            can_reveal_password=True,
            prefix_icon=ft.Icons.KEY,
        )

        self.gdrive_auth_status = ft.Text(
            "已授权 ✓" if self._gdrive_settings["refresh_token"] else "未授权",
            size=FontSize.CAPTION,
            color=(
                Color.SUCCESS_TEXT
                if self._gdrive_settings["refresh_token"]
                else Color.TEXT_MUTED
            ),
            expand=1,
        )

        self.gdrive_authorize_button = ft.OutlinedButton(
            "授权",
            key="gdrive_authorize",
            icon=ft.Icons.LOGIN,
            on_click=self._on_authorize_gdrive_clicked,
            style=ft.ButtonStyle(
                bgcolor=Color.BG_SURFACE,
                color=Color.TEXT_SECONDARY,
                icon_color=Color.PRIMARY,
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.MD),
            ),
        )

        self.gdrive_sync_folder_input = _dark_input(
            key="gdrive_sync_folder",
            label="同步文件夹（多个文件夹用换行分隔）",
            value="\n".join(
                self._gdrive_folder_labels.get(folder, folder)
                for folder in self._gdrive_folder_ids
            ),
            hint_text="点击「浏览」逐个添加；留空表示根目录",
            prefix_icon=ft.Icons.FOLDER_OUTLINED,
            multiline=True,
            min_lines=2,
            max_lines=4,
            expand=1,
        )

        self.gdrive_browse_button = ft.OutlinedButton(
            "浏览",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._browse_folder,
            style=ft.ButtonStyle(
                bgcolor=Color.BG_SURFACE,
                color=Color.TEXT_SECONDARY,
                icon_color=Color.PRIMARY,
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.MD),
            ),
        )

        gdrive_hint = ft.Text(
            "在 Google Cloud Console → API 和服务 → 凭据 创建 OAuth 客户端"
            "（应用类型选「桌面应用」），把 Client ID/Secret 填入上方后点击「授权」"
            "（仅申请只读权限 drive.readonly）",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
        )

        self.remember_password_switch = ft.Switch(
            label="记住密码",
            value=config.get("remember_credentials", True),
            label_position=ft.LabelPosition.RIGHT,
            active_color=Color.PRIMARY,
            label_text_style=ft.TextStyle(color=Color.TEXT_SECONDARY),
        )

        self.auto_connect_switch = ft.Switch(
            label=f"启动时自动连接 {self.title_text.value}",
            value=self._source_auto_connect_enabled(source_type, config),
            label_position=ft.LabelPosition.RIGHT,
            active_color=Color.PRIMARY,
            label_text_style=ft.TextStyle(color=Color.TEXT_SECONDARY),
            on_change=self._on_auto_connect_changed,
        )

        # === 主按钮：霓虹发光 ===
        self.connect_button = ft.FilledButton(
            "建立连接",
            key="connect_button",
            icon=ft.Icons.BOLT,
            on_click=self._on_connect_clicked,
            expand=2,
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
                elevation={ft.ControlState.DEFAULT: 6, ft.ControlState.PRESSED: 2},
                shadow_color=tint(Color.PRIMARY, "66"),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )

        self.disconnect_button = ft.OutlinedButton(
            "断开",
            icon=ft.Icons.LINK_OFF,
            on_click=self._disconnect_from_nextcloud,
            disabled=True,
            expand=1,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.DISABLED: Color.TEXT_DISABLED,
                    ft.ControlState.DEFAULT: Color.DANGER_TEXT,
                },
                icon_color={
                    ft.ControlState.DISABLED: Color.TEXT_DISABLED,
                    ft.ControlState.DEFAULT: Color.DANGER,
                },
                side=ft.BorderSide(1, tint(Color.DANGER, "59")),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )

        self.test_button = ft.OutlinedButton(
            "测试",
            icon=ft.Icons.NETWORK_CHECK,
            on_click=self._test_connection,
            expand=1,
            style=ft.ButtonStyle(
                color=Color.TEXT_SECONDARY,
                icon_color=Color.INFO,
                side=ft.BorderSide(1, Color.BORDER),
                shape=ft.RoundedRectangleBorder(radius=Radius.CIRCLE),
            ),
        )

        # === 组装：表单卡片（按来源类型显示其一） ===
        self.nextcloud_form_card = ft.Container(
            content=ft.Column(
                [
                    self.url_input,
                    self.username_input,
                    self.password_input,
                    ft.Row(
                        [self.sync_folder_input, self.browse_button], spacing=Space.XS
                    ),
                ],
                spacing=Space.SM,
            ),
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
            padding=Space.LG,
            visible=(source_type == "nextcloud"),
        )

        self.smb_form_card = ft.Container(
            content=ft.Column(
                [
                    self.smb_host_input,
                    smb_hint,
                    ft.Row(
                        [self.smb_sync_folder_input, self.smb_browse_button],
                        spacing=Space.XS,
                    ),
                    self.smb_edit_button,
                ],
                spacing=Space.SM,
            ),
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
            padding=Space.LG,
            visible=(source_type == "smb"),
        )

        self.gdrive_form_card = ft.Container(
            content=ft.Column(
                [
                    self.gdrive_client_id_input,
                    self.gdrive_client_secret_input,
                    ft.Row(
                        [self.gdrive_authorize_button, self.gdrive_auth_status],
                        spacing=Space.SM,
                    ),
                    ft.Row(
                        [self.gdrive_sync_folder_input, self.gdrive_browse_button],
                        spacing=Space.XS,
                    ),
                    gdrive_hint,
                ],
                spacing=Space.SM,
            ),
            bgcolor=Color.BG_SURFACE,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
            padding=Space.LG,
            visible=(source_type == "gdrive"),
        )

        action_row = ft.Row(
            [
                self.connect_button,
                self.disconnect_button,
                self.test_button,
            ],
            spacing=Space.SM,
        )
        active_form = {
            "nextcloud": self.nextcloud_form_card,
            "smb": self.smb_form_card,
            "gdrive": self.gdrive_form_card,
        }[source_type]
        active_form.content.controls.extend([self.status_container, action_row])
        self._container = ft.Container(
            content=ft.Column(
                [
                    ft.ListView(
                        controls=[
                            hero,
                            self.source_selector,
                            self.nextcloud_form_card,
                            self.smb_form_card,
                            self.gdrive_form_card,
                            ft.Container(
                                content=ft.Column(
                                    [
                                        self.remember_password_switch,
                                        self.auto_connect_switch,
                                    ],
                                    spacing=Space.XS,
                                ),
                                padding=ft.Padding(
                                    left=Space.XS,
                                    top=Space.XS,
                                    bottom=Space.XS,
                                    right=Space.XS,
                                ),
                            ),
                        ],
                        spacing=Space.MD,
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            padding=Space.LG,
            expand=True,
            bgcolor=Color.BG_APP,
        )

        self._built = True

        # 自动连接（每会话只触发一次，视图重建不重复连接）
        auto_connect_requested = any(
            self._source_auto_connect_enabled(source, config)
            for source in ("nextcloud", "smb", "gdrive")
        )
        if auto_connect_requested and not self._auto_connect_started:
            self._auto_connect_started = True
            self._auto_connect_in_progress = True
            self._set_connecting_state(True)
            asyncio.create_task(self._auto_connect_all_sources())

        return self._container

    def _on_sync_folder_changed(self, e):
        """同步文件夹变化时自动保存（带防抖，Nextcloud 表单专用）"""
        config_key = "connection.default_sync_folder"
        if self._save_timer:
            self._save_timer.cancel()

        def delayed_save():
            try:
                new_value = e.control.value.strip()
                self.app_context["config_manager"].set(config_key, new_value)
                self.app_context["config_manager"].set(
                    "connection.sync_folders", self._parse_folder_lines(new_value)
                )
                self.app_context["config_manager"].save_config()
                logger.info(f"同步目录已自动保存: {new_value}")
            except Exception as ex:
                logger.error(f"自动保存失败: {ex}")

        self._save_timer = threading.Timer(2.0, delayed_save)
        self._save_timer.start()

    @staticmethod
    def _parse_folder_lines(value: str) -> list[str]:
        """把换行分隔的目录转为稳定去重列表。"""
        return list(
            dict.fromkeys(
                line.strip() for line in str(value or "").splitlines() if line.strip()
            )
        )

    @staticmethod
    def _source_auto_connect_enabled(source_type: str, config: dict) -> bool:
        """读取来源独立开关；旧全局开关仅兼容为 Nextcloud。"""
        section = config.get(source_type, {})
        if source_type == "nextcloud":
            return bool(
                section.get("auto_connect") or config.get("auto_connect", False)
            )
        return bool(section.get("auto_connect", False))

    def _on_auto_connect_changed(self, e):
        """自动连接开关按当前来源立即持久化。"""
        source_type = self._current_source_type()
        enabled = bool(e.control.value)
        cm = self.app_context["config_manager"]
        cm.set(f"connection.{source_type}.auto_connect", enabled)
        if source_type == "nextcloud":
            cm.set("connection.auto_connect", enabled)
        cm.save_config()

    async def _auto_connect_all_sources(self):
        """启动时并行恢复所有启用了自动连接的来源。"""
        await asyncio.sleep(1)
        config = self.app_context["config_manager"].get("connection", {})
        sources = [
            source
            for source in ("nextcloud", "smb", "gdrive")
            if self._source_auto_connect_enabled(source, config)
        ]
        if not sources:
            self._auto_connect_in_progress = False
            self._update_connection_status(False)
            return
        results = await asyncio.gather(
            *(self._auto_connect_source(source) for source in sources),
            return_exceptions=True,
        )
        failures = [
            f"{source}: {result}"
            for source, result in zip(sources, results)
            if isinstance(result, Exception)
        ]
        current = self._current_source_type()
        self.is_connected = current in self.source_clients
        self._auto_connect_in_progress = False
        self._update_connection_status(self.is_connected)
        if failures:
            self.show_message("部分来源自动连接失败：" + "；".join(failures), "warning")
        elif self.source_clients:
            self.show_message(
                f"已自动连接 {len(self.source_clients)} 个音乐来源", "success"
            )

    async def _auto_connect_source(self, source_type: str):
        """静默恢复单个来源，不切换页面，也不覆盖其它来源。"""
        client = self._build_client_from_form(source_type)
        if not await client.test_connection():
            raise ConnectionError("连接测试失败")
        self.source_clients[source_type] = client
        self.app_context["nextcloud_client"] = client
        if self.app_context.get("music_service"):
            self.app_context["music_service"].update_source_client(source_type, client)
        logger.info("自动连接成功: %s", source_type)

    def _current_source_type(self) -> str:
        """当前选择的来源类型：nextcloud | smb | gdrive"""
        try:
            selected = self.source_selector.selected
            return next(iter(selected)) if selected else "nextcloud"
        except Exception:
            return "nextcloud"

    def _on_source_type_changed(self, e):
        """切换来源类型：切换表单与标题，并立即持久化选择"""
        source_type = self._current_source_type()
        self.is_connected = source_type in self.source_clients
        self.nextcloud_form_card.visible = source_type == "nextcloud"
        self.smb_form_card.visible = source_type == "smb"
        self.gdrive_form_card.visible = source_type == "gdrive"
        self.title_text.value = {"smb": "SMB", "gdrive": "GOOGLE DRIVE"}.get(
            source_type, "NEXTCLOUD"
        )
        try:
            self.app_context["config_manager"].set(
                "connection.source_type", source_type
            )
            self.app_context["config_manager"].save_config()
        except Exception as ex:
            logger.error(f"保存来源类型失败: {ex}")
        # iOS/Flet 对初始 hidden 表单使用 Offstage；仅切 visible 时测试 key
        # 偶尔不会重新挂载。当前视图完整重建可确保新来源表单可交互。
        if getattr(self.view_manager, "current_view", None) is self:
            self.view_manager.switch_to_view("connection")
        else:
            self._update_connection_status(self.is_connected)
            self.page.update()

    def _on_connect_clicked(self, e):
        """连接入口：SMB 优先复用完整的上次设置，否则进入配置向导。"""
        if self._current_source_type() == "smb":
            try:
                host = self._parse_smb_address()
            except ValueError as ex:
                self.show_message(str(ex), "error")
                return
            saved_host = str(self._smb_settings.get("host", "")).strip()
            if self._smb_settings.get("share") and host == saved_host:
                asyncio.create_task(self._connect_to_server(e))
                return
            # 首次配置或地址被修改时必须重新选择身份与共享。
            self._open_smb_wizard()
            return
        asyncio.create_task(self._connect_to_server(e))

    def _parse_smb_address(self) -> str:
        """读取地址框，支持 host 或 host:port，返回规范化 host（端口写入设置）"""
        raw = (self.smb_host_input.value or "").strip()
        if not raw:
            raise ValueError("请先输入 SMB 服务器地址")
        host = raw
        if raw.count(":") == 1:
            host, _, port_part = raw.partition(":")
            if port_part.strip().isdigit():
                self._smb_settings["port"] = int(port_part.strip())
            else:
                raise ValueError("地址格式不正确，应为 主机名 或 主机名:端口")
        return host.strip()

    def _open_smb_wizard(self):
        """打开 SMB 三步连接向导（地址 → 身份 → 共享 → 文件夹）"""
        from .components.smb_connect_wizard import SMBConnectWizard

        try:
            host = self._parse_smb_address()
        except ValueError as ex:
            self.show_message(str(ex), "error")
            return

        self._smb_settings["host"] = host

        def on_complete(result):
            self._on_smb_wizard_complete(result)

        def on_error(message):
            self.show_message(message, "info")

        wizard = SMBConnectWizard(self.page, defaults=self._smb_settings)
        wizard.show(on_complete, on_error)

    def _on_smb_wizard_complete(self, result):
        """向导完成：写入配置、接管客户端并进入文件列表"""
        self._smb_settings = {
            "host": result.host,
            "port": result.port,
            "domain": result.domain,
            "username": result.username,
            "password": result.password,
            "share": result.share,
            "sync_folder": result.sync_folder,
        }
        self.smb_host_input.value = result.host
        existing_folders = self._parse_folder_lines(self.smb_sync_folder_input.value)
        if (result.sync_folder or "/") not in existing_folders:
            existing_folders.append(result.sync_folder or "/")
        self.smb_sync_folder_input.value = "\n".join(existing_folders)

        # remember_credentials 关闭时仍保存结构信息，但不落盘明文密码
        remember = self.remember_password_switch.value
        cm = self.app_context["config_manager"]
        try:
            cm.set("connection.source_type", "smb")
            cm.set("connection.smb.host", result.host)
            cm.set("connection.smb.port", result.port)
            cm.set("connection.smb.domain", result.domain)
            cm.set("connection.smb.share", result.share)
            cm.set("connection.smb.default_sync_folder", result.sync_folder or "/")
            cm.set("connection.smb.sync_folders", existing_folders)
            cm.set("connection.smb.username", result.username if remember else "")
            cm.set("connection.smb.password", result.password if remember else "")
            cm.set("connection.remember_credentials", remember)
            cm.save_config()
        except Exception as ex:
            logger.error(f"保存 SMB 配置失败: {ex}")

        self.app_context["nextcloud_client"] = result.client
        self.source_clients["smb"] = result.client
        if self.app_context.get("music_service"):
            self.app_context["music_service"].update_source_client("smb", result.client)
        if self.app_context.get("lyrics_service"):
            self.app_context["lyrics_service"].update_clients(
                nextcloud_client=result.client
            )

        self.is_connected = True
        self._update_connection_status(True)
        self.show_message(
            f"连接成功！共享 '{result.share}'，目录 {result.sync_folder or '/'}",
            "success",
        )
        self.view_manager.switch_to_view("file_list")

    def _on_authorize_gdrive_clicked(self, e):
        """授权按钮入口：启动浏览器 OAuth 流程"""
        asyncio.create_task(self._authorize_gdrive())

    def _gdrive_api_base_url(self) -> str:
        """实时读取设置页配置的自定义 API 地址（留空 = Google 官方端点）"""
        return str(
            self.app_context["config_manager"].get("connection.gdrive.api_base_url", "")
            or ""
        ).strip()

    async def _authorize_gdrive(self):
        """Google 账号 OAuth 授权：loopback 接收器 + 系统浏览器换码"""
        from .. import gdrive_client as gdrive

        client_id = self.gdrive_client_id_input.value.strip()
        client_secret = self.gdrive_client_secret_input.value or ""
        if not client_id or not client_secret:
            self.show_message("请先填写 Client ID 和 Client Secret", "error")
            return

        endpoints = gdrive.resolve_endpoints(self._gdrive_api_base_url())
        self._update_gdrive_auth_state(waiting=True)
        receiver = gdrive.LoopbackOAuthReceiver()
        try:
            receiver.start()
            auth_url = gdrive.build_authorization_url(
                client_id,
                receiver.redirect_uri,
                auth_url=endpoints["oauth_auth"],
            )
            # Device-mode E2E delivers the callback directly to the loopback
            # receiver. Opening Safari backgrounds the test app and disconnects
            # Flet's remote tester, so keep the app in the foreground there.
            is_flet_test = any(
                os.environ.get(name)
                for name in (
                    "FLET_TEST",
                    "FLET_TEST_DEVICE_MODE",
                    "FLET_TEST_PLATFORM",
                )
            )
            if not is_flet_test:
                try:
                    await self.page.launch_url(auth_url)
                except Exception as launch_ex:
                    # 浏览器拉起失败（无默认浏览器/测试宿主）不应终止授权：
                    # loopback 接收器继续等待，用户可手动打开授权页
                    logger.warning(f"打开浏览器失败，请手动访问授权页: {launch_ex}")
                    self.show_message(
                        f"无法自动打开浏览器，请手动访问：{auth_url}", "warning"
                    )

            # 阻塞等待放到线程，避免卡住事件循环（用户最多有 5 分钟完成授权）
            code = await asyncio.to_thread(receiver.wait_for_code, 300.0)

            def _exchange():
                return gdrive.exchange_authorization_code(
                    client_id,
                    client_secret,
                    code,
                    receiver.redirect_uri,
                    token_url=endpoints["oauth_token"],
                )

            payload = await asyncio.to_thread(_exchange)
        except Exception as ex:
            logger.error(f"Google 授权失败: {ex}")
            self.show_message(f"授权失败: {ex}", "error")
            return
        finally:
            receiver.close()
            self._update_gdrive_auth_state(waiting=False)

        try:
            expires_in = int(payload.get("expires_in", 3600) or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        self._gdrive_settings.update(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": payload.get("refresh_token", ""),
                "access_token": payload.get("access_token", ""),
                "token_expiry": time.time() + expires_in,
            }
        )
        self._save_config()
        self._update_gdrive_auth_state()
        self.show_message("Google 账号授权成功，点击「建立连接」开始使用", "success")

    def _update_gdrive_auth_state(self, waiting: bool = False):
        """更新授权状态文本与按钮可用性"""
        if waiting:
            self.gdrive_auth_status.value = "正在等待浏览器授权..."
            self.gdrive_auth_status.color = Color.INFO_TEXT
            self.gdrive_authorize_button.disabled = True
        elif self._gdrive_settings.get("refresh_token"):
            self.gdrive_auth_status.value = "已授权 ✓"
            self.gdrive_auth_status.color = Color.SUCCESS_TEXT
            self.gdrive_authorize_button.disabled = False
        else:
            self.gdrive_auth_status.value = "未授权"
            self.gdrive_auth_status.color = Color.TEXT_MUTED
            self.gdrive_authorize_button.disabled = False
        self.page.update()

    def _persist_gdrive_tokens(self, tokens: dict):
        """GoogleDriveClient 令牌刷新回调：更新内存并按需持久化"""
        self._gdrive_settings.update(tokens)
        if not self.remember_password_switch.value:
            return
        try:
            cm = self.app_context["config_manager"]
            cm.set("connection.gdrive.access_token", tokens.get("access_token", ""))
            cm.set("connection.gdrive.refresh_token", tokens.get("refresh_token", ""))
            try:
                cm.set(
                    "connection.gdrive.token_expiry",
                    float(tokens.get("token_expiry", 0) or 0),
                )
            except (TypeError, ValueError):
                cm.set("connection.gdrive.token_expiry", 0)
            cm.save_config()
        except Exception as ex:
            logger.error(f"持久化 Google Drive 令牌失败: {ex}")

    def _build_client_from_form(self, source_type=None):
        """按当前来源类型构造客户端；SMB 优先使用向导保存的完整设置"""
        source_type = source_type or self._current_source_type()
        if source_type == "smb":
            try:
                host = self._parse_smb_address()
            except ValueError:
                host = self._smb_settings.get("host", "")
            settings = self._smb_settings
            if not host:
                raise ValueError("请先输入 SMB 服务器地址")
            if not settings.get("share"):
                raise ValueError("请点击「建立连接」，通过向导选择共享和文件夹")

            from ..smb_client import SMBClient

            return SMBClient(
                host=host,
                username=settings.get("username", ""),
                password=settings.get("password", ""),
                port=settings.get("port", 445),
                domain=settings.get("domain", ""),
                share=settings["share"],
            )

        if source_type == "gdrive":
            client_id = self.gdrive_client_id_input.value.strip()
            client_secret = self.gdrive_client_secret_input.value or ""
            if not client_id or not client_secret:
                raise ValueError("请填写 Google OAuth Client ID 和 Client Secret")
            if not self._gdrive_settings.get("refresh_token"):
                raise ValueError("请先点击「授权」完成 Google 账号授权")

            from ..gdrive_client import GoogleDriveClient

            return GoogleDriveClient(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=self._gdrive_settings.get("refresh_token", ""),
                access_token=self._gdrive_settings.get("access_token", ""),
                token_expiry=self._gdrive_settings.get("token_expiry", 0),
                on_tokens_updated=self._persist_gdrive_tokens,
                api_base_url=self._gdrive_api_base_url(),
            )

        server_url = self.url_input.value.strip()
        username = self.username_input.value.strip()
        password = self.password_input.value or ""
        if not server_url or not username or not password:
            raise ValueError("请填写完整的连接信息")
        from ..nextcloud_client import NextCloudClient

        return NextCloudClient(server_url, username, password)

    async def _connect_to_server(self, e):
        """连接到当前选择的音乐来源"""
        t_start = time.monotonic()

        try:
            client = self._build_client_from_form()
        except ValueError as ex:
            self.show_message(str(ex), "error")
            return
        except Exception as ex:
            # 构造失败（如缓存目录不可写）也必须给出反馈，否则表现为点击无响应
            logger.error(f"构造客户端失败: {ex}", exc_info=True)
            self.show_message(f"初始化连接失败: {ex}", "error")
            return

        self.show_message("正在连接...", "info")
        self._set_connecting_state(True)
        self.connect_button.disabled = True
        self.page.update()

        try:
            # app_context['nextcloud_client'] 槽位承载"当前来源客户端"
            # （NextCloudClient / SMBClient 共用同一事实接口）
            self.app_context["nextcloud_client"] = client
            source_type = self._current_source_type()
            self.source_clients[source_type] = client

            if self.app_context.get("music_service"):
                self.app_context["music_service"].update_source_client(
                    source_type, client
                )
            if self.app_context.get("lyrics_service"):
                self.app_context["lyrics_service"].update_clients(
                    nextcloud_client=client
                )

            success = await client.test_connection()

            if success:
                self.is_connected = True
                self._update_connection_status(True)
                self.show_message("连接成功！", "success")
                logger.info(
                    f"✅ 连接成功，点击到完成总耗时 {time.monotonic() - t_start:.1f}s"
                )
                self._save_config()
                self.view_manager.switch_to_view("file_list")
            else:
                self.show_message("连接失败，请检查服务器地址和凭据", "error")
                self._update_connection_status(False)
                logger.info(f"❌ 连接失败，总耗时 {time.monotonic() - t_start:.1f}s")
        except Exception as ex:
            logger.error(f"连接失败 (总耗时 {time.monotonic() - t_start:.1f}s): {ex}")
            self.show_message(f"连接错误: {str(ex)}", "error")
            self._update_connection_status(False)
        finally:
            self.connect_button.disabled = False
            self.page.update()

    async def _disconnect_from_nextcloud(self, e):
        """仅断开当前页签的来源，不影响其它已连接来源。"""
        source_type = self._current_source_type()
        self.source_clients.pop(source_type, None)
        self.app_context["nextcloud_client"] = next(
            iter(self.source_clients.values()), None
        )
        if self.app_context.get("music_service"):
            self.app_context["music_service"].update_source_client(source_type, None)
        if self.app_context.get("lyrics_service"):
            self.app_context["lyrics_service"].update_clients(nextcloud_client=None)
        self.is_connected = False
        self._update_connection_status(False)
        self.show_message(f"已断开 {source_type} 连接", "info")

    async def _test_connection(self, e):
        """测试连接"""
        try:
            temp_client = self._build_client_from_form()
        except ValueError as ex:
            self.show_message(str(ex), "error")
            return
        except Exception as ex:
            logger.error(f"构造客户端失败: {ex}", exc_info=True)
            self.show_message(f"初始化连接失败: {ex}", "error")
            return

        self.show_message("正在测试连接...", "info")
        self._set_connecting_state(True)
        self.test_button.disabled = True
        self.page.update()

        try:
            success = await temp_client.test_connection()
            if success:
                self.show_message("连接测试成功！", "success")
            else:
                self.show_message("连接测试失败", "error")
        except Exception as ex:
            self.show_message(f"测试错误: {str(ex)}", "error")
        finally:
            self.test_button.disabled = False
            self._update_connection_status(self.is_connected)
            self.page.update()

    def _save_config(self):
        """保存连接配置（含来源类型与 SMB 凭据）"""
        try:
            cm = self.app_context["config_manager"]
            source_type = self._current_source_type()
            remember = self.remember_password_switch.value

            cm.set("connection.source_type", source_type)

            # Nextcloud 字段始终保存（保留另一来源的表单内容）
            cm.set("connection.server_url", self.url_input.value.strip())
            cm.set("connection.username", self.username_input.value.strip())
            nextcloud_folders = self._parse_folder_lines(self.sync_folder_input.value)
            cm.set("connection.sync_folders", nextcloud_folders)
            cm.set(
                "connection.default_sync_folder",
                nextcloud_folders[0] if nextcloud_folders else "",
            )
            cm.set(
                f"connection.{source_type}.auto_connect",
                self.auto_connect_switch.value,
            )
            if source_type == "nextcloud":
                cm.set("connection.auto_connect", self.auto_connect_switch.value)
            cm.set("connection.remember_credentials", remember)
            cm.set(
                "connection.password",
                self.password_input.value or "" if remember else "",
            )

            # SMB 字段（来自向导结果 _smb_settings）
            s = self._smb_settings
            cm.set(
                "connection.smb.host",
                self.smb_host_input.value.strip() or s.get("host", ""),
            )
            cm.set("connection.smb.share", s.get("share", ""))
            smb_folders = self._parse_folder_lines(
                self.smb_sync_folder_input.value
            ) or ["/"]
            cm.set("connection.smb.default_sync_folder", smb_folders[0])
            cm.set("connection.smb.sync_folders", smb_folders)
            cm.set("connection.smb.username", s.get("username", ""))
            cm.set("connection.smb.domain", s.get("domain", ""))
            try:
                cm.set("connection.smb.port", int(s.get("port", 445) or 445))
            except (TypeError, ValueError):
                cm.set("connection.smb.port", 445)
            cm.set(
                "connection.smb.password",
                s.get("password", "") or "" if remember else "",
            )

            # Google Drive 字段（Client ID/Secret 来自输入框，令牌来自授权结果）
            # remember_credentials 关闭时不落盘任何凭据
            g = self._gdrive_settings
            cm.set(
                "connection.gdrive.client_id",
                self.gdrive_client_id_input.value.strip(),
            )
            cm.set(
                "connection.gdrive.client_secret",
                self.gdrive_client_secret_input.value or "" if remember else "",
            )
            cm.set(
                "connection.gdrive.refresh_token",
                g.get("refresh_token", "") if remember else "",
            )
            cm.set(
                "connection.gdrive.access_token",
                g.get("access_token", "") if remember else "",
            )
            try:
                cm.set(
                    "connection.gdrive.token_expiry",
                    float(g.get("token_expiry", 0) or 0),
                )
            except (TypeError, ValueError):
                cm.set("connection.gdrive.token_expiry", 0)
            # 兼容旧版可直接输入 Drive 文件夹 ID 的用法。正常通过浏览器选择时，
            # 输入框显示的是 labels；只有用户手动改写内容时才把它视为 ID。
            displayed_folders = [
                self._gdrive_folder_labels.get(folder, folder)
                for folder in self._gdrive_folder_ids
            ]
            entered_folders = self._parse_folder_lines(
                self.gdrive_sync_folder_input.value
            )
            if entered_folders != displayed_folders:
                self._gdrive_folder_ids = entered_folders
                self._gdrive_folder_labels = {
                    folder: label
                    for folder, label in self._gdrive_folder_labels.items()
                    if folder in self._gdrive_folder_ids
                }
            cm.set(
                "connection.gdrive.default_sync_folder",
                (self._gdrive_folder_ids or [""])[0],
            )
            cm.set(
                "connection.gdrive.sync_folders",
                self._gdrive_folder_ids,
            )
            cm.set(
                "connection.gdrive.sync_folder_labels",
                self._gdrive_folder_labels,
            )

            cm.save_config()
            logger.info("连接配置已保存")
        except Exception as ex:
            logger.error(f"保存配置失败: {ex}")

    def _update_connection_status(self, connected: bool):
        """更新连接状态显示（霓虹状态胶囊）"""
        if connected:
            self.status_text.value = "已连接"
            self.status_text.color = Color.SUCCESS_TEXT
            self.status_dot.bgcolor = Color.SUCCESS
            self.status_dot.shadow = glow(Color.SUCCESS, radius=8, alpha="80")
            self.status_container.bgcolor = tint(Color.SUCCESS, "1F")
            self.status_container.border = ft.Border.all(1, tint(Color.SUCCESS, "40"))
            self.connect_button.disabled = True
            self.disconnect_button.disabled = False
        else:
            self.status_text.value = "未连接"
            self.status_text.color = Color.DANGER_TEXT
            self.status_dot.bgcolor = Color.DANGER
            self.status_dot.shadow = glow(Color.DANGER, radius=8, alpha="80")
            self.status_container.bgcolor = tint(Color.DANGER, "1F")
            self.status_container.border = ft.Border.all(1, tint(Color.DANGER, "40"))
            self.connect_button.disabled = False
            self.disconnect_button.disabled = True
        self.page.update()

    def _set_connecting_state(self, connecting: bool):
        """连接过程中在顶部状态条实时反馈（避免提示藏在屏幕底部看不见）"""
        if connecting:
            self.status_text.value = "连接中"
            self.status_text.color = Color.INFO_TEXT
            self.status_dot.bgcolor = Color.INFO
            self.status_dot.shadow = glow(Color.INFO, radius=8, alpha="80")
            self.status_container.bgcolor = tint(Color.INFO, "1F")
            self.status_container.border = ft.Border.all(1, tint(Color.INFO, "40"))
            self.connect_button.disabled = True
            self.disconnect_button.disabled = True
        self.page.update()

    def show_message(self, message: str, message_type: str = "info"):
        """在页面顶部显示消息。"""
        show_snack_bar(self.page, message, message_type)

    def on_view_activated(self):
        """视图激活时检查连接状态"""
        if self._auto_connect_in_progress:
            self._set_connecting_state(True)
            return
        source_type = self._current_source_type()
        self.is_connected = source_type in self.source_clients
        self._update_connection_status(self.is_connected)

    def _browse_folder(self, e):
        """浏览文件夹（按当前来源选择对应的远程目录输入框与配置键）"""
        source_type = self._current_source_type()
        source_client = self.source_clients.get(source_type)
        # 兼容旧上下文中仅提供单客户端的调用。
        if not source_client and not self.source_clients:
            source_client = self.app_context.get("nextcloud_client")
        if not source_client:
            # Google Drive 授权成功后令牌已经足够创建客户端。用户通常会直接
            # 点击“浏览”，无需再猜测还要先点一次“建立连接”。
            if self._current_source_type() == "gdrive":
                asyncio.create_task(self._connect_and_browse_gdrive())
                return
            self.show_message("请先连接服务器", "error")
            return

        self._open_folder_selector(source_client)

    async def _connect_and_browse_gdrive(self):
        """按需建立 Google Drive 连接，然后打开文件夹选择器。"""
        try:
            client = self._build_client_from_form()
        except ValueError as ex:
            self.show_message(str(ex), "error")
            return
        except Exception as ex:
            logger.error(f"初始化 Google Drive 浏览连接失败: {ex}", exc_info=True)
            self.show_message(f"初始化连接失败: {ex}", "error")
            return

        self.gdrive_browse_button.disabled = True
        self.show_message("正在连接 Google Drive...", "info")
        self.page.update()
        try:
            if not await client.test_connection():
                self.show_message("Google Drive 连接失败，请重新授权后重试", "error")
                return
            self.app_context["nextcloud_client"] = client
            self.source_clients["gdrive"] = client
            if self.app_context.get("music_service"):
                self.app_context["music_service"].update_source_client("gdrive", client)
            if self.app_context.get("lyrics_service"):
                self.app_context["lyrics_service"].update_clients(
                    nextcloud_client=client
                )
            self.is_connected = True
            self._update_connection_status(True)
            self._save_config()
            self._open_folder_selector(client)
        except Exception as ex:
            logger.error(f"连接 Google Drive 以浏览文件夹失败: {ex}", exc_info=True)
            self.show_message(f"Google Drive 连接错误: {ex}", "error")
        finally:
            self.gdrive_browse_button.disabled = False
            self.page.update()

    def _open_folder_selector(self, source_client=None):
        """使用当前来源客户端打开远程文件夹选择器。"""

        if self._current_source_type() == "gdrive":
            folder_input = self.gdrive_sync_folder_input
            config_key = "connection.gdrive.default_sync_folder"
            folders_key = "connection.gdrive.sync_folders"
            folders = list(self._gdrive_folder_ids)
        elif self._current_source_type() == "smb":
            folder_input = self.smb_sync_folder_input
            config_key = "connection.smb.default_sync_folder"
            folders_key = "connection.smb.sync_folders"
            folders = self._parse_folder_lines(folder_input.value)
        else:
            folder_input = self.sync_folder_input
            config_key = "connection.default_sync_folder"
            folders_key = "connection.sync_folders"
            folders = self._parse_folder_lines(folder_input.value)

        from .folder_selector import FolderSelector

        current_folder = folders[-1] if folders else "/"
        current_display = ""
        if self._current_source_type() == "gdrive":
            current_display = self._gdrive_folder_labels.get(current_folder, "")
        selector = FolderSelector(
            self.page,
            source_client
            or self.source_clients.get(self._current_source_type())
            or self.app_context["nextcloud_client"],
            current_folder,
            initial_display_path=current_display,
        )

        def on_selected(path):
            if self._current_source_type() == "gdrive":
                folders = list(self._gdrive_folder_ids)
            else:
                folders = self._parse_folder_lines(folder_input.value)
            if path not in folders:
                folders.append(path)
            display_path = selector.selected_display_path or path or "/"
            if self._current_source_type() == "gdrive":
                self._gdrive_folder_ids = folders
                self._gdrive_folder_labels[path] = display_path
                folder_input.value = "\n".join(
                    self._gdrive_folder_labels.get(folder, folder) for folder in folders
                )
            else:
                folder_input.value = "\n".join(folders)
            self.page.update()
            self.show_message(f"已选择文件夹: {display_path}", "success")
            self.app_context["config_manager"].set(
                config_key, folders[0] if folders else path
            )
            self.app_context["config_manager"].set(folders_key, folders)
            if self._current_source_type() == "gdrive":
                self.app_context["config_manager"].set(
                    "connection.gdrive.sync_folder_labels",
                    self._gdrive_folder_labels,
                )
            self.app_context["config_manager"].save_config()

        selector.show_dialog(on_selected)
