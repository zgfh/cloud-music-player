"""
播放控制组件 - Flet 版本
"""

import asyncio
import logging
from typing import Callable, Optional

import flet as ft

from ...utils.platform_ui import get_button_icon_size
from ...utils.theme import (
    Color,
    FontSize,
    Gradient,
    Radius,
    Space,
    glow,
    tint,
)

logger = logging.getLogger(__name__)


class PlaybackControlComponent:
    """播放控制组件"""

    def __init__(
        self,
        page: ft.Page,
        app_context: dict,
        playback_controller,
        on_play_mode_change_callback=None,
    ):
        self.page = page
        self.app_context = app_context
        self.playback_controller = playback_controller
        self.on_play_mode_change_callback = on_play_mode_change_callback
        self.playback_service = playback_controller.playback_service
        self._button_busy = False
        self._updating_progress = False
        self._seek_timer = None
        self._seek_pending = False
        self._cached_duration = 0
        self._built = False

    def build(self):
        """构建并返回控制组件"""
        if self._built and hasattr(self, "_container"):
            return self._container

        icon_primary = get_button_icon_size(primary=True)
        icon_secondary = get_button_icon_size(secondary=True)
        play_button_size = 64 if icon_primary >= 24 else 56

        # === 进度条（霓虹滑轨） ===
        self.current_time_label = ft.Text(
            "00:00",
            size=FontSize.CAPTION,
            color=Color.PRIMARY,
            width=45,
            weight=ft.FontWeight.BOLD,
        )
        self.progress_slider = ft.Slider(
            min=0,
            max=100,
            value=0,
            expand=True,
            on_change=self._on_seek,
            active_color=Color.PRIMARY,
            inactive_color=Color.BG_ELEVATED,
            thumb_color=Color.PRIMARY,
        )
        self.total_time_label = ft.Text(
            "00:00",
            size=FontSize.CAPTION,
            color=Color.TEXT_MUTED,
            width=45,
        )

        # === 音量 ===
        self.volume_slider = ft.Slider(
            min=0,
            max=100,
            value=self.playback_service.get_volume() if self.playback_service else 70,
            expand=True,
            on_change=self._on_volume_change,
            active_color=Color.ACCENT,
            inactive_color=Color.BG_ELEVATED,
            thumb_color=Color.ACCENT,
        )

        # === 播放模式（带文字的分段胶囊，避免多个相似图标难以辨认） ===
        self._mode_visuals = {}

        def mode_button(icon, label, tooltip, mode):
            icon_control = ft.Icon(
                icon, size=get_button_icon_size(small=True), color=Color.TEXT_MUTED
            )
            label_control = ft.Text(
                label,
                size=FontSize.MICRO,
                color=Color.TEXT_MUTED,
                weight=ft.FontWeight.BOLD,
            )
            button = ft.Container(
                content=ft.Row(
                    [icon_control, label_control],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=4,
                ),
                height=36,
                padding=ft.Padding(left=9, right=9, top=0, bottom=0),
                border_radius=Radius.MD,
                tooltip=tooltip,
                on_click=lambda e: self._set_play_mode(mode),
            )
            self._mode_visuals[mode] = (icon_control, label_control)
            return button

        self.normal_button = mode_button(
            ft.Icons.FORMAT_LIST_NUMBERED, "顺序", "顺序播放", "normal"
        )
        self.repeat_one_button = mode_button(
            ft.Icons.REPEAT_ONE, "单曲", "单曲循环", "repeat_one"
        )
        self.repeat_all_button = mode_button(
            ft.Icons.REPEAT, "列表", "列表循环", "repeat_all"
        )
        self.shuffle_button = mode_button(
            ft.Icons.SHUFFLE, "随机", "随机播放", "shuffle"
        )

        # === 播放控制按钮 ===
        def transport_button(icon, tooltip, callback):
            return ft.IconButton(
                icon,
                tooltip=tooltip,
                width=48,
                height=48,
                icon_size=icon_secondary,
                icon_color=Color.TEXT_PRIMARY,
                style=ft.ButtonStyle(
                    bgcolor={
                        ft.ControlState.DEFAULT: Color.BG_ELEVATED,
                        ft.ControlState.HOVERED: tint(Color.PRIMARY, "1F"),
                        ft.ControlState.PRESSED: tint(Color.PRIMARY, "33"),
                    },
                    side=ft.BorderSide(1, Color.BORDER_STRONG),
                    shape=ft.CircleBorder(),
                ),
                on_click=callback,
            )

        self.prev_button = transport_button(
            ft.Icons.SKIP_PREVIOUS_ROUNDED, "上一曲", self._on_previous_song
        )

        # 主播放键：圆形渐变 + 霓虹光晕
        self.play_icon = ft.Icon(
            ft.Icons.PLAY_ARROW, color=Color.PRIMARY_TEXT, size=play_button_size - 22
        )
        self.play_pause_button = ft.Container(
            content=self.play_icon,
            width=play_button_size,
            height=play_button_size,
            border_radius=play_button_size / 2,
            gradient=Gradient.primary(),
            border=ft.Border.all(1, tint(Color.PRIMARY, "99")),
            shadow=glow(Color.GLOW_CYAN, radius=18, alpha="59"),
            tooltip="播放 / 暂停",
            on_click=self._on_toggle_playback,
        )

        self.next_button = transport_button(
            ft.Icons.SKIP_NEXT_ROUNDED, "下一曲", self._on_next_song
        )
        self.stop_button = ft.IconButton(
            ft.Icons.STOP_ROUNDED,
            tooltip="停止",
            icon_size=get_button_icon_size(small=True),
            icon_color=Color.TEXT_MUTED,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: Color.BG_SURFACE_ALT,
                    ft.ControlState.HOVERED: tint(Color.DANGER, "1A"),
                },
                shape=ft.CircleBorder(),
            ),
            on_click=self._on_stop_playback,
        )

        mode_selector = ft.Container(
            content=ft.Row(
                [
                    self.normal_button,
                    self.repeat_one_button,
                    self.repeat_all_button,
                    self.shuffle_button,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                spacing=2,
            ),
            bgcolor=Color.BG_SURFACE_ALT,
            border=ft.Border.all(1, Color.BORDER),
            border_radius=Radius.LG,
            padding=4,
        )

        # === 组装（控制台卡片） ===
        self._container = ft.Container(
            content=ft.Column(
                [
                    # 进度条
                    ft.Row(
                        [
                            self.current_time_label,
                            self.progress_slider,
                            self.total_time_label,
                        ],
                        spacing=Space.XS,
                    ),
                    # 主播放操作：保持严格居中和清晰的大小层级
                    ft.Row(
                        [
                            self.prev_button,
                            self.play_pause_button,
                            self.next_button,
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=Space.XL,
                    ),
                    # 次级操作：停止、音量和播放模式各自成组
                    ft.Row(
                        [
                            self.stop_button,
                            ft.Container(width=1, height=20, bgcolor=Color.BORDER),
                            ft.Icon(
                                ft.Icons.VOLUME_UP_ROUNDED,
                                color=Color.TEXT_MUTED,
                                size=17,
                            ),
                            self.volume_slider,
                        ],
                        spacing=Space.XS,
                    ),
                    mode_selector,
                ],
                spacing=Space.MD,
            ),
            gradient=Gradient.surface(),
            border=ft.Border.all(1, Color.BORDER_STRONG),
            border_radius=Radius.XL,
            padding=Space.LG,
            shadow=glow(Color.GLOW_VIOLET, radius=24, alpha="14"),
        )

        self._built = True
        self.update_mode_buttons()
        return self._container

    async def _safe_button_action(self, action_func, action_name: str):
        """防止重复点击"""
        if self._button_busy:
            return
        self._button_busy = True
        try:
            await action_func()
        except Exception as e:
            logger.error(f"{action_name}失败: {e}")
        finally:
            self._button_busy = False

    async def _on_previous_song(self, e):
        await self._safe_button_action(self.playback_controller.previous_song, "上一曲")

    async def _on_next_song(self, e):
        await self._safe_button_action(self.playback_controller.next_song, "下一曲")

    async def _on_toggle_playback(self, e):
        await self._safe_button_action(
            self.playback_controller.toggle_playback, "切换播放"
        )

    async def _on_stop_playback(self, e):
        await self._safe_button_action(self.playback_controller.stop_playback, "停止")

    def _on_volume_change(self, e):
        """音量变化"""
        if self.playback_service:
            volume = int(e.control.value)
            # 配置使用 0-100，播放器接口使用 0.0-1.0；必须实时下发，
            # 不能只保存到配置等下一首歌加载时才生效。
            self.playback_service.set_audio_volume(volume / 100.0)
            self.playback_service.set_volume(volume)

    def _set_play_mode(self, mode: str):
        """设置播放模式"""
        from ...services.playback_controller import PlayMode

        mode_map = {
            "normal": PlayMode.NORMAL,
            "repeat_one": PlayMode.REPEAT_ONE,
            "repeat_all": PlayMode.REPEAT_ALL,
            "shuffle": PlayMode.SHUFFLE,
        }
        self.playback_controller.set_play_mode(mode_map[mode])
        if self.on_play_mode_change_callback:
            self.on_play_mode_change_callback(mode)
        self.update_mode_buttons()

    def _on_seek(self, e):
        """进度条拖拽，带防抖"""
        if self._updating_progress:
            return
        if self._seek_timer:
            self._seek_timer.cancel()

        # Flet 会复用同一个 Slider 控件。若在防抖定时器触发时才读取
        # e.control.value，期间的进度刷新可能已把它改回旧播放位置，导致
        # 用户看到滑块回跳且 seek 实际没有发生。因此必须立刻保存目标值。
        requested_value = float(e.control.value)
        self._seek_pending = True

        def do_seek():
            try:
                position = requested_value / 100.0
                duration = self.get_current_duration()
                if duration > 0:
                    target = position * duration
                    self.playback_service.seek_to_position(target)
            finally:
                self._seek_pending = False

        import threading

        self._seek_timer = threading.Timer(0.5, do_seek)
        self._seek_timer.start()

    def update_progress(self):
        """更新进度条和时间显示"""
        if not self._built or not self.playback_service:
            return
        if not hasattr(self, "progress_slider"):
            return
        # 用户操作尚在防抖窗口内时保留其选择，避免定时刷新造成视觉回跳。
        if self._seek_pending:
            return
        self._updating_progress = True
        try:
            position = self.get_current_position()
            duration = self.get_current_duration()
            if duration > 0:
                progress = (position / duration) * 100
                self.progress_slider.value = min(progress, 100)
            else:
                self.progress_slider.value = 0
            self.update_time_display(position, duration)
            self.page.update()
        finally:
            self._updating_progress = False

    def update_time_display(self, position: float, duration: float):
        """格式化时间显示"""
        pos_min = int(position // 60)
        pos_sec = int(position % 60)
        dur_min = int(duration // 60)
        dur_sec = int(duration % 60)
        self.current_time_label.value = f"{pos_min:02d}:{pos_sec:02d}"
        self.total_time_label.value = f"{dur_min:02d}:{dur_sec:02d}"

    def reset_progress(self):
        """重置进度条"""
        self._updating_progress = True
        self.progress_slider.value = 0
        self.current_time_label.value = "00:00"
        self.total_time_label.value = "00:00"
        self._cached_duration = 0
        self._updating_progress = False
        self.page.update()

    def get_current_position(self) -> float:
        """获取当前播放位置"""
        try:
            if (
                self.playback_service
                and hasattr(self.playback_service, "audio_player")
                and self.playback_service.audio_player
            ):
                return self.playback_service.audio_player.get_position()
        except:
            pass
        return 0

    def get_current_duration(self) -> float:
        """获取总时长"""
        try:
            if self._cached_duration > 0:
                return self._cached_duration
            if (
                self.playback_service
                and hasattr(self.playback_service, "audio_player")
                and self.playback_service.audio_player
            ):
                dur = self.playback_service.audio_player.get_duration()
                if dur > 0:
                    self._cached_duration = dur
                    return dur
        except:
            pass
        return 0

    def update_play_pause_button(self, is_playing: bool):
        """更新播放/暂停按钮状态（渐变 + 光晕随状态切换）"""
        if not self._built or not hasattr(self, "play_pause_button"):
            return
        if is_playing:
            self.play_icon.name = ft.Icons.PAUSE
            self.play_pause_button.gradient = ft.LinearGradient(
                begin=ft.Alignment(-1, -1),
                end=ft.Alignment(1, 1),
                colors=Gradient.WARNING,
            )
            self.play_pause_button.shadow = glow(Color.WARNING, radius=18, alpha="59")
        else:
            self.play_icon.name = ft.Icons.PLAY_ARROW
            self.play_pause_button.gradient = Gradient.primary()
            self.play_pause_button.shadow = glow(Color.GLOW_CYAN, radius=18, alpha="59")
        self.page.update()

    def update_mode_buttons(self):
        """更新播放模式分段按钮状态。"""
        if not self._built or not hasattr(self, "normal_button"):
            return
        from ...services.playback_controller import PlayMode

        mode = self.playback_controller.get_play_mode()
        buttons = {
            PlayMode.NORMAL: self.normal_button,
            PlayMode.REPEAT_ONE: self.repeat_one_button,
            PlayMode.REPEAT_ALL: self.repeat_all_button,
            PlayMode.SHUFFLE: self.shuffle_button,
        }
        mode_keys = {
            PlayMode.NORMAL: "normal",
            PlayMode.REPEAT_ONE: "repeat_one",
            PlayMode.REPEAT_ALL: "repeat_all",
            PlayMode.SHUFFLE: "shuffle",
        }
        for m, btn in buttons.items():
            icon, label = self._mode_visuals[mode_keys[m]]
            if m == mode:
                btn.bgcolor = tint(Color.PRIMARY, "26")
                btn.border = ft.Border.all(1, tint(Color.PRIMARY, "73"))
                btn.shadow = glow(Color.PRIMARY, radius=10, alpha="1F")
                icon.color = Color.PRIMARY
                label.color = Color.PRIMARY
            else:
                btn.bgcolor = None
                btn.border = ft.Border.all(1, "#00000000")
                btn.shadow = None
                icon.color = Color.TEXT_MUTED
                label.color = Color.TEXT_MUTED
        self.page.update()

    def enable_controls(self, enabled: bool):
        """启用/禁用控制"""
        for btn in [
            self.prev_button,
            self.play_pause_button,
            self.next_button,
            self.stop_button,
        ]:
            btn.disabled = not enabled
        self.page.update()
