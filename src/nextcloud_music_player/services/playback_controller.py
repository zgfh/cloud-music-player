"""
播放控制器 - 专门处理播放控制逻辑
包括上一曲、下一曲、播放模式控制等功能
"""

import asyncio
import logging
import math
import random
import time
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PlayMode(Enum):
    """播放模式枚举"""

    NORMAL = "normal"
    REPEAT_ONE = "repeat_one"
    REPEAT_ALL = "repeat_all"
    SHUFFLE = "shuffle"


class PlaybackController:
    """播放控制器 - 负责播放逻辑控制"""

    def __init__(
        self,
        playback_service,
        playlist_manager,
        play_song_callback=None,
        ui_update_callback=None,
    ):
        """
        初始化播放控制器

        Args:
            playback_service: 播放服务实例
            playlist_manager: 播放列表管理器实例
            play_song_callback: 播放歌曲的回调函数
            ui_update_callback: UI更新回调函数
        """
        self.playback_service = playback_service
        self.playlist_manager = playlist_manager
        self.play_song_callback = play_song_callback
        self.ui_update_callback = ui_update_callback
        self.play_mode = PlayMode.REPEAT_ONE
        self._sleep_timer_task: asyncio.Task | None = None
        self._sleep_timer_deadline: float | None = None
        self._monotonic = time.monotonic
        self._sleep_timer_minutes = int(
            self.playback_service.config_manager.get("player.sleep_timer_minutes", 30)
            or 30
        )

        logger.info("播放控制器初始化完成")

    def set_play_mode(self, mode: PlayMode):
        """设置播放模式"""
        self.play_mode = mode
        logger.info(f"播放模式已设置为: {mode.value}")

    def get_play_mode(self) -> PlayMode:
        """获取当前播放模式"""
        return self.play_mode

    async def toggle_playback(self):
        """切换播放/暂停状态"""
        try:
            if self.playback_service.is_playing():
                await self.playback_service.pause_music()
                logger.info("播放已暂停")
                # 通知UI更新按钮状态
                if self.ui_update_callback:
                    self.ui_update_callback(False)
            else:
                resumed = await self.resume_music()
                if resumed:
                    logger.info("播放已恢复")
                    # 通知UI更新按钮状态
                    if self.ui_update_callback:
                        self.ui_update_callback(True)
        except Exception as e:
            logger.error(f"切换播放状态失败: {e}")
            raise

    async def resume_music(self):
        """恢复音乐播放"""
        try:
            # 检查是否有暂停的音乐
            if hasattr(
                self.playback_service, "current_song_state"
            ) and self.playback_service.current_song_state.get("is_paused"):
                # 使用播放服务的音频播放器恢复播放
                if (
                    hasattr(self.playback_service, "audio_player")
                    and self.playback_service.audio_player
                ):
                    play = getattr(self.playback_service, "_play_audio_player", None)
                    resumed = (
                        await play()
                        if play is not None
                        else self.playback_service.audio_player.play()
                    )
                    if resumed:
                        self.playback_service.current_song_state["is_paused"] = False
                        self.playback_service.current_song_state["is_playing"] = True
                        logger.info("音乐已恢复播放")
                        return True
                    else:
                        logger.error("音频播放器恢复播放失败")

                # 备用方案：使用pygame（向后兼容）
                try:
                    import pygame

                    if pygame.mixer.get_init():
                        pygame.mixer.music.unpause()
                        self.playback_service.current_song_state["is_paused"] = False
                        self.playback_service.current_song_state["is_playing"] = True
                        logger.info("音乐已恢复播放（pygame）")
                        return
                except ImportError:
                    logger.warning("pygame不可用")
                except Exception as pygame_error:
                    logger.error(f"pygame恢复播放失败: {pygame_error}")

            # 如果没有暂停的音乐，尝试重新播放当前歌曲
            if (
                hasattr(self.playback_service, "current_song")
                and self.playback_service.current_song
            ):
                await self.playback_service.play_music()
                resumed = self.playback_service.is_playing()
                if resumed:
                    logger.info("重新开始播放当前歌曲")
                return resumed
            else:
                logger.warning("没有可恢复的音乐")
                return False

        except Exception as e:
            logger.error(f"恢复音乐播放失败: {e}")
            raise

    async def stop_playback(self):
        """停止播放"""
        try:
            stopped = await self.playback_service.stop_music()
            if not stopped:
                logger.warning("播放器未能确认停止")
                return False
            logger.info("播放已停止")
            # 通知UI更新按钮状态
            if self.ui_update_callback:
                self.ui_update_callback(False, is_stopped=True)
            return True
        except Exception as e:
            logger.error(f"停止播放失败: {e}")
            raise

    def get_sleep_timer_minutes(self) -> int:
        """返回保存的睡眠定时器时长。"""
        return self._sleep_timer_minutes

    def is_sleep_timer_active(self) -> bool:
        return bool(self._sleep_timer_task and not self._sleep_timer_task.done())

    def get_sleep_timer_remaining_seconds(self) -> int:
        """返回基于单调时钟计算的剩余秒数，避免系统时间变化影响倒计时。"""
        if not self.is_sleep_timer_active() or self._sleep_timer_deadline is None:
            return 0
        return max(0, math.ceil(self._sleep_timer_deadline - self._monotonic()))

    def start_sleep_timer(self, minutes: int) -> bool:
        """启动睡眠定时器，到期后停止播放。"""
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            return False
        if not 1 <= minutes <= 1440:
            return False

        self.cancel_sleep_timer()
        self._sleep_timer_minutes = minutes
        config = self.playback_service.config_manager
        config.set("player.sleep_timer_minutes", minutes)
        config.save_config()
        self._sleep_timer_deadline = self._monotonic() + minutes * 60
        self._sleep_timer_task = asyncio.create_task(self._sleep_timer_worker())
        return True

    def cancel_sleep_timer(self) -> None:
        task = self._sleep_timer_task
        self._sleep_timer_task = None
        self._sleep_timer_deadline = None
        if task and not task.done():
            task.cancel()

    async def _sleep_timer_worker(self) -> None:
        try:
            while True:
                deadline = self._sleep_timer_deadline
                if deadline is None:
                    return
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)
            await self.stop_playback()
        except asyncio.CancelledError:
            return
        finally:
            if self._sleep_timer_task is asyncio.current_task():
                self._sleep_timer_task = None
                self._sleep_timer_deadline = None

    async def previous_song(self):
        """播放上一曲"""
        try:
            logger.info("开始切换到上一曲")
            current_playlist = self.playlist_manager.get_current_playlist()
            if not current_playlist or not current_playlist.get("songs"):
                logger.warning("播放列表为空，无法切换到上一曲")
                return False

            current_index = current_playlist.get("current_index", 0)
            songs = current_playlist["songs"]

            # 根据播放模式确定上一首歌曲
            new_index = self._calculate_previous_index(current_index, len(songs))

            # 更新播放列表索引
            current_playlist["current_index"] = new_index
            self.playlist_manager.save_current_playlist(current_playlist)

            # 播放选中的歌曲 - 添加保护机制
            selected_song = songs[new_index]
            if self.play_song_callback:
                try:
                    logger.info(
                        f"准备播放上一曲: {selected_song['info'].get('title', '未知')}"
                    )
                    await self.play_song_callback(selected_song["info"])
                    logger.info(
                        f"已切换到上一曲: {selected_song['info'].get('title', '未知')}"
                    )
                except Exception as callback_error:
                    logger.error(f"播放回调失败: {callback_error}")
                    # 即使播放失败，也要返回True，因为索引已经更新了
                    return True

            return True

        except Exception as e:
            logger.error(f"上一曲失败: {e}")
            import traceback

            logger.error(f"详细错误: {traceback.format_exc()}")
            return False

    async def next_song(self):
        """播放下一曲"""
        try:
            logger.info("开始切换到下一曲")
            current_playlist = self.playlist_manager.get_current_playlist()
            if not current_playlist or not current_playlist.get("songs"):
                logger.warning("播放列表为空，无法切换到下一曲")
                return False

            current_index = current_playlist.get("current_index", 0)
            songs = current_playlist["songs"]

            # 最多尝试一轮；连接或下载失败的歌曲自动跳过。
            attempted = 0
            new_index = current_index
            while attempted < len(songs):
                new_index = self._calculate_next_index(new_index, len(songs))
                current_playlist["current_index"] = new_index
                self.playlist_manager.save_current_playlist(current_playlist)
                selected_song = songs[new_index]
                attempted += 1
                if not self.play_song_callback:
                    return True
                try:
                    logger.info(
                        f"准备播放下一曲: {selected_song['info'].get('title', '未知')}"
                    )
                    if await self.play_song_callback(selected_song["info"]):
                        return True
                    logger.warning("歌曲不可用，继续下一首")
                except Exception as callback_error:
                    logger.error(f"播放回调失败: {callback_error}")
            logger.error("播放列表中没有可用歌曲")
            return False

        except Exception as e:
            logger.error(f"下一曲失败: {e}")
            import traceback

            logger.error(f"详细错误: {traceback.format_exc()}")
            return False

    async def auto_play_next_song(self):
        """自动播放下一曲（歌曲结束时调用）"""
        try:
            logger.info("开始自动播放下一曲逻辑")

            # 根据播放模式决定是否自动播放下一曲
            if self.play_mode == PlayMode.REPEAT_ONE:
                # 单曲循环：重新播放当前歌曲
                logger.info("单曲循环模式：重新播放当前歌曲")
                current_playlist = self.playlist_manager.get_current_playlist()
                if current_playlist and current_playlist.get("songs"):
                    current_index = current_playlist.get("current_index", 0)
                    songs = current_playlist["songs"]
                    if 0 <= current_index < len(songs):
                        selected_song = songs[current_index]
                        if self.play_song_callback:
                            return bool(
                                await self.play_song_callback(selected_song["info"])
                            )
                        return False
            elif self.play_mode == PlayMode.NORMAL:
                # 顺序播放只前进到列表末尾；回到第一首是列表循环的语义。
                current_playlist = self.playlist_manager.get_current_playlist()
                if not current_playlist or not current_playlist.get("songs"):
                    return False
                current_index = current_playlist.get("current_index", 0)
                if current_index >= len(current_playlist["songs"]) - 1:
                    logger.info("顺序播放模式：已到播放列表末尾")
                    return False
                return await self.next_song()
            else:
                # 其他模式：播放下一曲
                logger.info(f"{self.play_mode.value}模式：播放下一曲")
                return await self.next_song()

            return False

        except Exception as e:
            logger.error(f"自动播放下一曲失败: {e}")
            return False

    def _calculate_previous_index(self, current_index: int, total_songs: int) -> int:
        """计算上一首歌曲的索引"""
        if total_songs == 0:
            return 0

        if self.play_mode == PlayMode.SHUFFLE:
            # 随机模式：随机选择一首（排除当前）
            available_indices = [i for i in range(total_songs) if i != current_index]
            if available_indices:
                return random.choice(available_indices)
            else:
                return current_index
        else:
            # 顺序模式：上一首
            return (current_index - 1) % total_songs

    def _calculate_next_index(self, current_index: int, total_songs: int) -> int:
        """计算下一首歌曲的索引"""
        if total_songs == 0:
            return 0

        if self.play_mode == PlayMode.SHUFFLE:
            # 随机模式：随机选择一首（排除当前）
            available_indices = [i for i in range(total_songs) if i != current_index]
            if available_indices:
                return random.choice(available_indices)
            else:
                return current_index
        elif self.play_mode == PlayMode.REPEAT_ONE:
            # 单曲循环：保持当前歌曲（在手动切换时仍然切换到下一首）
            return (current_index + 1) % total_songs
        else:
            # 顺序播放或列表循环
            return (current_index + 1) % total_songs

    def get_current_song_info(self) -> Optional[Dict[str, Any]]:
        """获取当前歌曲信息"""
        try:
            current_playlist = self.playlist_manager.get_current_playlist()
            if not current_playlist or not current_playlist.get("songs"):
                return None

            current_index = current_playlist.get("current_index", 0)
            songs = current_playlist["songs"]

            if 0 <= current_index < len(songs):
                return songs[current_index]["info"]

            return None

        except Exception as e:
            logger.error(f"获取当前歌曲信息失败: {e}")
            return None

    def get_playlist_info(self) -> Dict[str, Any]:
        """获取播放列表信息"""
        try:
            current_playlist = self.playlist_manager.get_current_playlist()
            if not current_playlist:
                return {
                    "total_songs": 0,
                    "current_index": 0,
                    "current_song": None,
                    "has_previous": False,
                    "has_next": False,
                }

            songs = current_playlist.get("songs", [])
            current_index = current_playlist.get("current_index", 0)
            total_songs = len(songs)

            return {
                "total_songs": total_songs,
                "current_index": current_index,
                "current_song": (
                    songs[current_index]["info"]
                    if 0 <= current_index < total_songs
                    else None
                ),
                "has_previous": total_songs > 1,
                "has_next": total_songs > 1,
            }

        except Exception as e:
            logger.error(f"获取播放列表信息失败: {e}")
            return {
                "total_songs": 0,
                "current_index": 0,
                "current_song": None,
                "has_previous": False,
                "has_next": False,
            }

    async def play_song_by_index(self, index: int):
        """根据索引播放歌曲"""
        try:
            current_playlist = self.playlist_manager.get_current_playlist()
            if not current_playlist or not current_playlist.get("songs"):
                logger.warning("播放列表为空，无法播放歌曲")
                return False

            songs = current_playlist["songs"]
            if 0 <= index < len(songs):
                # 更新播放列表索引
                current_playlist["current_index"] = index
                self.playlist_manager.save_current_playlist(current_playlist)

                # 播放选中的歌曲
                selected_song = songs[index]
                if self.play_song_callback:
                    await self.play_song_callback(selected_song["info"])

                logger.info(
                    f"已播放索引 {index} 的歌曲: {selected_song['info'].get('title', '未知')}"
                )
                return True
            else:
                logger.warning(f"歌曲索引 {index} 超出范围（总共 {len(songs)} 首歌曲）")
                return False

        except Exception as e:
            logger.error(f"根据索引播放歌曲失败: {e}")
            return False
