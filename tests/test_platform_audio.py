import asyncio
import sys
from types import SimpleNamespace

import pytest

from nextcloud_music_player import platform_audio
from nextcloud_music_player.platform_audio import FletAudioPlayer
from nextcloud_music_player.services.playback_service import PlaybackService


class FakeFletAudio:
    def __init__(self, **kwargs):
        self.src = kwargs["src"]
        self.volume = kwargs["volume"]
        self.on_loaded = kwargs["on_loaded"]
        self.play_calls = 0
        self.command_calls = []

    async def play(self):
        self.play_calls += 1

    async def pause(self):
        self.command_calls.append("pause")

    async def seek(self, position):
        self.command_calls.append(("seek", position))


class FakePage:
    def __init__(self):
        self.services = []
        self.update_calls = 0

    def update(self):
        self.update_calls += 1


@pytest.fixture
def flet_audio_player(monkeypatch):
    fake_flet = SimpleNamespace(Duration=lambda **kwargs: kwargs)
    fake_audio_module = SimpleNamespace(
        Audio=FakeFletAudio,
        AudioState=SimpleNamespace(STOPPED="stopped", PLAYING="playing"),
        ReleaseMode=SimpleNamespace(STOP="stop"),
    )
    monkeypatch.setitem(sys.modules, "flet", fake_flet)
    monkeypatch.setitem(sys.modules, "flet_audio", fake_audio_module)
    return FletAudioPlayer(FakePage())


@pytest.mark.asyncio
async def test_play_does_not_depend_on_ios_loaded_event(flet_audio_player, tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3")
    assert flet_audio_player.load(song)

    # iOS 的本地文件源可能不会发送 on_loaded，play 仍应直接执行。
    assert await flet_audio_player.play_async() is True
    assert flet_audio_player._audio.play_calls == 1


@pytest.mark.asyncio
async def test_reloading_same_source_keeps_loaded_state(flet_audio_player, tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3")
    assert flet_audio_player.load(song)
    flet_audio_player._audio.on_loaded(None)

    assert flet_audio_player.load(song)
    assert await flet_audio_player.play_async() is True
    assert flet_audio_player._audio.play_calls == 1


def test_changing_source_updates_flet_service(flet_audio_player, tmp_path):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"ID3")
    second.write_bytes(b"ID3")
    assert flet_audio_player.load(first)
    initial_updates = flet_audio_player._page.update_calls

    assert flet_audio_player.load(second)
    assert flet_audio_player._page.update_calls == initial_updates + 1
    assert not flet_audio_player._loaded.is_set()


def test_new_source_position_starts_at_zero(flet_audio_player, tmp_path, monkeypatch):
    """切换歌曲后，首个原生位置事件到达前不能沿用旧曲时间差。"""
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"ID3")
    second.write_bytes(b"ID3")
    assert flet_audio_player.load(first)
    flet_audio_player._position_ms = 90000
    flet_audio_player._position_ts = 1

    monkeypatch.setattr("time.time", lambda: 1000.0)
    assert flet_audio_player.load(second)
    flet_audio_player._state = flet_audio_player._AudioState.PLAYING

    assert flet_audio_player.get_position() == 0


@pytest.mark.asyncio
async def test_stop_commands_complete_in_order(flet_audio_player, tmp_path):
    """暂停和归零必须在返回前按顺序完成，不能延迟干扰下一首歌。"""
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3")
    assert flet_audio_player.load(song)

    assert await flet_audio_player.stop_async() is True
    assert flet_audio_player._audio.command_calls == [
        "pause",
        ("seek", {"seconds": 0}),
    ]


@pytest.mark.asyncio
async def test_playback_service_waits_for_async_player_command():
    """重试播放也必须等待 Flet 命令完成，不能把排队当作播放成功。"""

    class AsyncPlayer:
        def __init__(self):
            self.sync_calls = 0
            self.async_calls = 0

        def play(self):
            self.sync_calls += 1
            return True

        async def play_async(self):
            self.async_calls += 1
            return False

    service = PlaybackService.__new__(PlaybackService)
    service.audio_player = AsyncPlayer()

    assert await service._play_audio_player() is False
    assert service.audio_player.async_calls == 1
    assert service.audio_player.sync_calls == 0


def test_dispose_removes_flet_service(flet_audio_player, tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3")
    assert flet_audio_player.load(song)
    assert len(flet_audio_player._page.services) == 1

    flet_audio_player.dispose()

    assert flet_audio_player._page.services == []
    assert flet_audio_player._audio is None


def test_ios_prefers_avfoundation_even_when_page_is_available(monkeypatch):
    class FakeIOSPlayer:
        AVAudioPlayer = object()

    monkeypatch.setattr(platform_audio, "is_ios", lambda: True)
    monkeypatch.setattr(platform_audio, "iOSAudioPlayer", FakeIOSPlayer)

    player = platform_audio.create_audio_player(page=FakePage())

    assert isinstance(player, FakeIOSPlayer)
