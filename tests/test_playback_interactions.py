"""
播放交互测试：慢网络下载提示、切歌竞态、失败反馈

直接驱动真实 PlaybackView 的协程入口（play_selected_song），
用 FakeNextcloudClient 模拟慢网络，断言：
- 状态胶囊在每个阶段有明确反馈（切换中/下载中/播放中/下载失败）
- 新点击会让旧的慢下载失效，不会出现"旧歌顶掉新选择"
- 下载/播放失败对用户可见，而不是只写日志
"""

import asyncio
from types import SimpleNamespace

import flet as ft
from fakes import add_remote_song

from nextcloud_music_player.services.playback_controller import PlayMode


async def test_play_downloaded_song_skips_download(playback_env):
    """已下载的歌曲立即播放，不发起网络请求"""
    info = add_remote_song(playback_env.library, "local.mp3", downloaded=True)

    ok = await playback_env.view.play_selected_song(info)

    assert ok is True
    assert playback_env.client.download_calls == []
    assert playback_env.player.loaded_files[-1].endswith("local.mp3")
    assert playback_env.player.stopped_count == 0
    assert playback_env.view.status_label.value == "播放中"
    assert playback_env.view.playback_control_component.play_icon.icon == ft.Icons.PAUSE


async def test_existing_local_file_wins_even_with_stale_metadata(playback_env):
    """本地文件存在时应直接播放，即使下载标记已经过期。"""
    info = add_remote_song(playback_env.library, "cached.mp3", downloaded=True)
    info["is_downloaded"] = False

    ok = await playback_env.view.play_selected_song(info)

    assert ok is True
    assert playback_env.client.download_calls == []


async def test_native_completion_triggers_repeat_one(playback_env):
    """原生完成事件应触发单曲循环，不依赖末尾进度采样。"""
    info = add_remote_song(playback_env.library, "loop.mp3", downloaded=True)
    playback_env.view.handle_play_selected([info])
    await asyncio.sleep(0.05)
    assert playback_env.player.playing is True
    initial_loads = len(playback_env.player.loaded_files)
    # FakeConfigManager 不持久化播放列表；为控制器提供与真实配置一致的缓存。
    playback_env.view.playlist_manager._current_playlist_cache = {
        "id": 1,
        "songs": [{"name": "loop.mp3", "info": info}],
        "current_index": 0,
    }

    playback_env.player.playing = False
    playback_env.player.completed = True
    playback_env.view._update_progress_only()
    # 完成处理自身有 0.2 秒去抖；另留时间等待播放锁和 UI 回调。
    await asyncio.sleep(0.5)

    assert len(playback_env.player.loaded_files) == initial_loads + 1
    assert playback_env.player.loaded_files[-1].endswith("loop.mp3")


async def test_volume_slider_applies_volume_immediately(playback_env):
    """拖动音量后应立即下发给播放器，并持久化百分比配置。"""
    component = playback_env.view.playback_control_component

    component._on_volume_change(SimpleNamespace(control=SimpleNamespace(value=35)))

    assert playback_env.player.volume == 0.35
    assert playback_env.config.get("player.volume") == 35


async def test_sleep_timer_defaults_to_30_minutes_and_can_be_adjusted(playback_env):
    """睡眠定时器默认 30 分钟，启动后保存用户调整值并可取消。"""
    component = playback_env.view.playback_control_component
    controller = playback_env.view.playback_controller

    assert component.sleep_timer_input.value == "30"
    component.sleep_timer_input.value = "45"
    await component._on_sleep_timer_toggle(None)

    assert controller.is_sleep_timer_active() is True
    assert playback_env.config.get("player.sleep_timer_minutes") == 45
    assert component.sleep_timer_input.disabled is True
    assert component.sleep_timer_button.content == "取消"
    assert component.sleep_timer_status.value == "45 分钟后停止播放"

    await component._on_sleep_timer_toggle(None)
    assert controller.is_sleep_timer_active() is False
    assert component.sleep_timer_input.disabled is False
    assert component.sleep_timer_button.content == "启动"


async def test_sleep_timer_stops_playback_when_countdown_finishes(
    playback_env, monkeypatch
):
    """睡眠倒计时结束后必须通过控制器停止播放并同步 UI。"""
    controller = playback_env.view.playback_controller

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    playback_env.player.playing = True
    playback_env.view.playback_service.current_song_state["is_playing"] = True

    await controller._sleep_timer_worker(1800)

    assert playback_env.player.stopped_count == 1
    assert playback_env.view.status_label.value == "停止"


async def test_seek_uses_user_value_even_if_progress_refresh_changes_slider(
    playback_env,
):
    """防抖期间的进度刷新不能覆盖用户选择的跳转位置。"""
    component = playback_env.view.playback_control_component
    component._cached_duration = 200
    component.progress_slider.value = 60

    component._on_seek(SimpleNamespace(control=component.progress_slider))
    component.update_progress()

    # 待处理时保留用户设置的滑块位置；定时器随后按事件值而非控件现值跳转。
    assert component.progress_slider.value == 60
    component.progress_slider.value = 5
    await asyncio.sleep(0.6)
    assert playback_env.player.seek_calls == [120]


async def test_switching_song_resets_progress_and_cancels_pending_seek(playback_env):
    """下一曲开始时必须归零，上一曲的延迟跳转不能落到新曲上。"""
    first = add_remote_song(playback_env.library, "first.mp3", downloaded=True)
    second = add_remote_song(playback_env.library, "second.mp3", downloaded=True)
    assert await playback_env.view.play_selected_song(first) is True
    component = playback_env.view.playback_control_component
    component._cached_duration = 200
    component.progress_slider.value = 60
    component._on_seek(SimpleNamespace(control=component.progress_slider))

    assert await playback_env.view.play_selected_song(second) is True
    assert component.progress_slider.value == 0
    assert component.current_time_label.value == "00:00"
    assert component.total_time_label.value == "00:00"
    assert component._cached_duration == 0
    await asyncio.sleep(0.6)
    assert playback_env.player.seek_calls == []


async def test_stop_is_sent_even_when_cached_state_is_stale(playback_env):
    """内部状态暂时不同步时，停止按钮仍必须命令原生播放器停止。"""
    playback_env.player.playing = True
    playback_env.view.playback_service.current_song_state["is_playing"] = False

    await playback_env.view.playback_control_component._on_stop_playback(None)

    assert playback_env.player.stopped_count == 1
    assert playback_env.player.playing is False
    assert playback_env.view.status_label.value == "停止"
    assert playback_env.view.playback_control_component.progress_slider.value == 0


async def test_stop_works_while_paused(playback_env):
    """暂停中点击停止也应归零并进入停止状态。"""
    info = add_remote_song(playback_env.library, "paused.mp3", downloaded=True)
    assert await playback_env.view.play_selected_song(info) is True
    assert playback_env.view.playback_control_component.play_icon.icon == ft.Icons.PAUSE
    await playback_env.view.playback_controller.toggle_playback()
    assert playback_env.view.status_label.value == "暂停"
    assert (
        playback_env.view.playback_control_component.play_icon.icon
        == ft.Icons.PLAY_ARROW
    )

    await playback_env.view.playback_control_component._on_stop_playback(None)

    assert playback_env.player.stopped_count == 1
    assert playback_env.player.paused is False
    assert playback_env.view.status_label.value == "停止"


async def test_undownloaded_song_shows_downloading_status(playback_env):
    """未下载歌曲在慢网络下必须有"下载中"提示，完成后进入播放中"""
    add_remote_song(playback_env.library, "slow.mp3")
    playback_env.client.download_delay = 0.3
    info = playback_env.library.get_song_info("slow.mp3")

    task = asyncio.create_task(playback_env.view.play_selected_song(info))
    await asyncio.sleep(0.1)  # 下载进行中

    assert playback_env.view.status_label.value == "下载中..."
    assert len(playback_env.client.download_calls) == 1

    ok = await task
    assert ok is True
    assert playback_env.player.loaded_files[-1].endswith("slow.mp3")
    assert playback_env.view.status_label.value == "播放中"


async def test_download_for_play_immediately_updates_playlist_icon(playback_env):
    """在线播放触发下载后，播放列表应立即改为绿色已下载状态。"""
    info = add_remote_song(playback_env.library, "online.mp3")
    playlist_data = {
        "playlists": [
            {
                "id": 1,
                "name": "默认播放列表",
                "songs": [{"name": "online.mp3", "info": info, "state": {}}],
                "current_index": 0,
            }
        ],
        "current_playlist_id": 1,
        "next_id": 2,
    }
    playback_env.config.load_playlists = lambda: playlist_data
    playback_env.config.save_playlists = lambda data: playlist_data.update(data)
    playback_env.view.playlist_manager.invalidate_cache()
    playback_env.view.playlist_component.refresh_display()

    before_icon = playback_env.view.playlist_component.song_list.controls[
        0
    ].content.controls[-1]
    assert before_icon.icon == ft.Icons.CLOUD_DOWNLOAD_OUTLINED

    ok = await playback_env.view.play_selected_song(info)

    assert ok is True
    after_icon = playback_env.view.playlist_component.song_list.controls[
        0
    ].content.controls[-1]
    assert after_icon.icon == ft.Icons.TASK_ALT


async def test_playing_song_prefetches_connected_next_song(playback_env):
    """当前歌曲开始播放后，应静默下载同一在线来源的下一首。"""
    first = add_remote_song(playback_env.library, "first.mp3", downloaded=True)
    second = add_remote_song(playback_env.library, "second.mp3")
    playlist = {
        "id": 1,
        "songs": [
            {"name": "first.mp3", "info": first, "state": {}},
            {"name": "second.mp3", "info": second, "state": {}},
        ],
        "current_index": 0,
    }
    playback_env.view.playlist_manager._current_playlist_cache = playlist
    playback_env.view.playlist_manager.invalidate_cache = lambda: None
    playback_env.view.play_mode = playback_env.view.playback_controller.play_mode = (
        PlayMode.NORMAL
    )

    assert await playback_env.view.play_selected_song(first) is True
    assert playback_env.view._prefetch_task is not None
    assert await playback_env.view._prefetch_task is True

    assert playback_env.library.get_song_info("second.mp3")["is_downloaded"] is True
    assert ("/remote/second.mp3", "second.mp3") in playback_env.client.download_calls


async def test_next_song_is_not_prefetched_when_source_is_offline(playback_env):
    first = add_remote_song(playback_env.library, "first.mp3", downloaded=True)
    second = add_remote_song(playback_env.library, "second.mp3")
    playback_env.view.playlist_manager._current_playlist_cache = {
        "id": 1,
        "songs": [
            {"name": "first.mp3", "info": first, "state": {}},
            {"name": "second.mp3", "info": second, "state": {}},
        ],
        "current_index": 0,
    }
    playback_env.view.playlist_manager.invalidate_cache = lambda: None
    playback_env.view.play_mode = playback_env.view.playback_controller.play_mode = (
        PlayMode.NORMAL
    )
    playback_env.music_service.source_clients.clear()

    assert await playback_env.view.play_selected_song(first) is True
    assert playback_env.view._prefetch_task is None
    assert playback_env.library.get_song_info("second.mp3")["is_downloaded"] is False


async def test_new_click_supersedes_slow_download(playback_env):
    """先点慢歌 A 再点快歌 B：A 的下载结果必须被丢弃，不能顶掉 B。

    这是"切换点击新歌曲后还是播放旧的歌曲"的竞态根因：
    两个并发下载，慢的那个后完成时会把最新选择顶掉。
    """
    add_remote_song(playback_env.library, "A.mp3")
    add_remote_song(playback_env.library, "B.mp3")
    playback_env.client.download_delay = {"A.mp3": 0.5, "B.mp3": 0.05}

    task_a = asyncio.create_task(
        playback_env.view.play_selected_song(
            playback_env.library.get_song_info("A.mp3")
        )
    )
    await asyncio.sleep(0.02)  # 让 A 进入下载中

    ok_b = await playback_env.view.play_selected_song(
        playback_env.library.get_song_info("B.mp3")
    )
    assert ok_b is True
    assert playback_env.player.loaded_files[-1].endswith("B.mp3")

    ok_a = await task_a  # A 的下载此刻才完成
    assert ok_a is False
    # A 全程不应被加载播放
    assert not any(p.endswith("A.mp3") for p in playback_env.player.loaded_files)
    assert playback_env.view.status_label.value == "播放中"


async def test_switching_song_stops_old_playback_immediately(playback_env):
    """点新歌（未下载、下载慢）时旧歌应立刻停止并显示下载中，而不是继续放旧歌"""
    old_info = add_remote_song(playback_env.library, "old.mp3", downloaded=True)
    add_remote_song(playback_env.library, "new.mp3")
    playback_env.client.download_delay = {"new.mp3": 0.3}

    assert await playback_env.view.play_selected_song(old_info) is True
    assert playback_env.player.playing is True

    task = asyncio.create_task(
        playback_env.view.play_selected_song(
            playback_env.library.get_song_info("new.mp3")
        )
    )
    await asyncio.sleep(0.05)

    # 旧歌已停、状态明确显示正在下载新歌
    assert playback_env.player.playing is False
    assert playback_env.view.status_label.value == "下载中..."

    ok = await task
    assert ok is True
    assert playback_env.player.loaded_files[-1].endswith("new.mp3")
    assert playback_env.player.playing is True


async def test_download_failure_shows_error_status(playback_env):
    """下载失败必须反馈给用户，而不是静默只写日志"""
    add_remote_song(playback_env.library, "gone.mp3")
    playback_env.client.download_error = RuntimeError("404 Not Found")

    ok = await playback_env.view.play_selected_song(
        playback_env.library.get_song_info("gone.mp3")
    )

    assert ok is False
    assert playback_env.view.status_label.value == "下载失败"
    assert playback_env.player.loaded_files == []


async def test_download_exception_is_caught(playback_env):
    """下载抛异常（网络中断等）同样反馈且不崩溃"""
    add_remote_song(playback_env.library, "boom.mp3")
    playback_env.client.download_error = ConnectionError("network reset")

    ok = await playback_env.view.play_selected_song(
        playback_env.library.get_song_info("boom.mp3")
    )

    assert ok is False
    assert playback_env.view.status_label.value == "下载失败"


async def test_failed_download_skips_to_next_available_song(playback_env):
    """自动播放时，第一首下载失败应继续播放下一首本地歌曲。"""
    failed = add_remote_song(playback_env.library, "gone.mp3")
    available = add_remote_song(playback_env.library, "available.mp3", downloaded=True)
    playback_env.client.download_error = RuntimeError("404 Not Found")

    playback_env.view.playlist_manager._current_playlist_cache = {
        "id": 1,
        "songs": [
            {"name": "gone.mp3", "info": failed},
            {"name": "available.mp3", "info": available},
        ],
        "current_index": 0,
    }

    assert await playback_env.view._play_or_skip_unavailable(failed) is True

    assert playback_env.player.loaded_files[-1].endswith("available.mp3")
    assert playback_env.view.status_label.value == "播放中"


async def test_play_request_without_remote_path_fails_visibly(playback_env):
    """歌曲缺少远程路径时给出"无法播放"，而不是静默返回"""
    playback_env.library.add_remote_song("stale.mp3", "")
    playback_env.library.songs["stale.mp3"]["filepath"] = ""  # 确保不走本地分支

    ok = await playback_env.view.play_selected_song(
        playback_env.library.get_song_info("stale.mp3")
    )

    assert ok is False
    assert playback_env.view.status_label.value == "无法播放"
