"""ViewManager lifecycle event regression tests."""

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

from nextcloud_music_player.views.playback_view import PlaybackView
from nextcloud_music_player.views.view_manager import ViewManager


def test_pause_and_resume_lifecycle_states():
    """Flet 0.86 uses PAUSE (not PAUSED) for background transitions."""
    manager = ViewManager.__new__(ViewManager)
    manager.app_backgrounded = False
    manager.file_list_view = SimpleNamespace(on_app_resumed=lambda: None)

    manager._on_app_lifecycle_state_change(
        SimpleNamespace(state=ft.AppLifecycleState.PAUSE)
    )
    assert manager.app_backgrounded is True

    manager._on_app_lifecycle_state_change(
        SimpleNamespace(state=ft.AppLifecycleState.RESUME)
    )
    assert manager.app_backgrounded is False


@pytest.mark.asyncio
async def test_playback_ui_timer_stops_after_session_is_destroyed(monkeypatch):
    view = PlaybackView.__new__(PlaybackView)
    view._view_active = True
    view._built = True
    view.view_manager = SimpleNamespace(app_backgrounded=False)

    async def no_wait(_delay):
        return None

    def destroyed_update():
        raise RuntimeError("An attempt to fetch destroyed session.")

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    view._update_progress_only = destroyed_update

    await view._schedule_ui_update()

    assert view._view_active is False


@pytest.mark.asyncio
async def test_playback_ui_timer_stops_after_controls_are_frozen(monkeypatch):
    """页面控件被冻结后应结束刷新任务，避免持续输出错误日志。"""
    view = PlaybackView.__new__(PlaybackView)
    view._view_active = True
    view._built = True
    view.view_manager = SimpleNamespace(app_backgrounded=False)

    async def no_wait(_delay):
        return None

    def frozen_update():
        raise RuntimeError("Frozen controls cannot be updated.")

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    view._update_progress_only = frozen_update

    await view._schedule_ui_update()

    assert view._view_active is False
