"""
平台检测和平台特定的音频播放器实现
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


def is_ios() -> bool:
    """检测是否运行在iOS平台"""
    return sys.platform == "ios" or "iOS" in str(sys.platform)


def is_macos() -> bool:
    """检测是否运行在macOS平台"""
    return sys.platform == "darwin"


def is_mobile() -> bool:
    """检测是否运行在移动平台"""
    return is_ios()


class AudioPlayerProtocol(Protocol):
    """音频播放器协议接口"""

    def load(self, file_path: str) -> bool:
        """加载音频文件"""
        ...

    def play(self) -> bool:
        """播放音频"""
        ...

    def pause(self) -> bool:
        """暂停播放"""
        ...

    def stop(self) -> bool:
        """停止播放"""
        ...

    def is_playing(self) -> bool:
        """检查是否正在播放"""
        ...

    def has_completed(self) -> bool:
        """当前音轨是否已由播放器报告自然播放完成。"""
        ...

    def set_volume(self, volume: float) -> bool:
        """设置音量 (0.0-1.0)"""
        ...

    def get_duration(self) -> float:
        """获取音频时长（秒）"""
        ...

    def get_position(self) -> float:
        """获取当前播放位置（秒）"""
        ...

    def seek(self, position: float) -> bool:
        """跳转到指定位置（秒）"""
        ...

    async def play_async(self) -> bool:
        """异步开始播放（支持需要等待原生播放器就绪的平台）"""
        ...


class PygameAudioPlayer:
    """基于pygame的音频播放器（桌面平台）"""

    def __init__(self):
        self._pygame = None
        self._current_file = None
        self._is_paused = False
        self._volume = 0.7
        # 位置跟踪相关
        self._start_time = None
        self._pause_time = None
        self._seek_offset = 0.0
        self._cached_duration = None
        self._init_pygame()

    def _init_pygame(self):
        """初始化pygame"""
        try:
            import pygame

            self._pygame = pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
            logger.info("pygame音频播放器初始化成功")
        except ImportError:
            logger.error("pygame未安装，无法使用pygame音频播放器")
        except Exception as e:
            logger.error(f"初始化pygame失败: {e}")

    def load(self, file_path: str) -> bool:
        """加载音频文件"""
        if not self._pygame:
            return False

        try:
            if not os.path.exists(file_path):
                logger.error(f"音频文件不存在: {file_path}")
                return False

            self._pygame.mixer.music.load(file_path)
            self._current_file = file_path

            # 重置位置跟踪
            self._start_time = None
            self._pause_time = None
            self._seek_offset = 0.0

            # 清除缓存的时长，确保重新计算
            self._cached_duration = None

            logger.info(f"音频文件加载成功: {file_path}")
            return True
        except Exception as e:
            logger.error(f"加载音频文件失败: {e}")
            return False

    def play(self) -> bool:
        """播放音频"""
        if not self._pygame or not self._current_file:
            return False

        try:
            import time

            if self._is_paused:
                self._pygame.mixer.music.unpause()
                self._is_paused = False
                # 恢复播放时，调整开始时间
                if self._pause_time and self._start_time:
                    pause_duration = time.time() - self._pause_time
                    self._start_time += pause_duration
                self._pause_time = None
            else:
                self._pygame.mixer.music.play()
                # 记录播放开始时间
                self._start_time = time.time()
                self._seek_offset = 0.0
            logger.info("开始播放音频")
            return True
        except Exception as e:
            logger.error(f"播放音频失败: {e}")
            return False

    def pause(self) -> bool:
        """暂停播放"""
        if not self._pygame:
            return False

        try:
            import time

            self._pygame.mixer.music.pause()
            self._is_paused = True
            # 记录暂停时间
            self._pause_time = time.time()
            logger.info("暂停播放")
            return True
        except Exception as e:
            logger.error(f"暂停播放失败: {e}")
            return False

    def stop(self) -> bool:
        """停止播放"""
        if not self._pygame:
            return False

        try:
            self._pygame.mixer.music.stop()
            self._is_paused = False
            # 重置位置跟踪
            self._start_time = None
            self._pause_time = None
            self._seek_offset = 0.0
            logger.info("停止播放")
            return True
        except Exception as e:
            logger.error(f"停止播放失败: {e}")
            return False

    def is_playing(self) -> bool:
        """检查是否正在播放"""
        if not self._pygame:
            return False

        try:
            return self._pygame.mixer.music.get_busy() and not self._is_paused
        except:
            return False

    def set_volume(self, volume: float) -> bool:
        """设置音量 (0.0-1.0)"""
        if not self._pygame:
            return False

        try:
            self._volume = max(0.0, min(1.0, volume))
            self._pygame.mixer.music.set_volume(self._volume)
            return True
        except Exception as e:
            logger.error(f"设置音量失败: {e}")
            return False

    def get_duration(self) -> float:
        """获取音频时长（秒）- pygame不支持直接获取，尝试使用音频库"""
        if not self._current_file:
            logger.debug("get_duration: 没有当前文件")
            return 0.0

        if not os.path.exists(self._current_file):
            logger.debug(f"get_duration: 文件不存在: {self._current_file}")
            return 0.0

        # 缓存时长，避免重复计算
        if self._cached_duration is not None and self._cached_duration > 0:
            return self._cached_duration

        try:
            # 尝试使用mutagen库获取音频时长
            try:
                import mutagen

                logger.debug(f"尝试使用mutagen获取时长: {self._current_file}")
                audio_file = mutagen.File(self._current_file)
                if (
                    audio_file is not None
                    and hasattr(audio_file, "info")
                    and hasattr(audio_file.info, "length")
                ):
                    duration = float(audio_file.info.length)
                    if duration > 0:
                        logger.info(f"通过mutagen获取音频时长: {duration:.2f}秒")
                        self._cached_duration = duration
                        return duration
                    else:
                        logger.debug(f"mutagen获取的时长无效: {duration}")
                else:
                    logger.debug("mutagen无法解析音频文件或没有时长信息")
            except ImportError:
                logger.debug("mutagen库不可用")
            except Exception as e:
                logger.debug(f"mutagen获取音频时长失败: {e}")

            # 尝试使用wave库（仅支持WAV格式）
            if self._current_file.lower().endswith(".wav"):
                try:
                    import wave

                    logger.debug(f"尝试使用wave库获取时长: {self._current_file}")
                    with wave.open(self._current_file, "rb") as wav_file:
                        frames = wav_file.getnframes()
                        sample_rate = wav_file.getframerate()
                        duration = frames / float(sample_rate)
                        if duration > 0:
                            logger.info(f"通过wave库获取音频时长: {duration:.2f}秒")
                            self._cached_duration = duration
                            return duration
                except Exception as e:
                    logger.debug(f"wave库获取音频时长失败: {e}")

            # 尝试使用 eyed3 库（专门用于MP3）
            if self._current_file.lower().endswith(".mp3"):
                try:
                    import eyed3

                    logger.debug(f"尝试使用eyed3获取时长: {self._current_file}")
                    audiofile = eyed3.load(self._current_file)
                    if audiofile and audiofile.info and audiofile.info.time_secs:
                        duration = float(audiofile.info.time_secs)
                        if duration > 0:
                            logger.info(f"通过eyed3获取音频时长: {duration:.2f}秒")
                            self._cached_duration = duration
                            return duration
                except ImportError:
                    logger.debug("eyed3库不可用")
                except Exception as e:
                    logger.debug(f"eyed3获取音频时长失败: {e}")

            logger.warning(f"所有方法都无法获取音频时长: {self._current_file}")

        except Exception as e:
            logger.error(f"获取音频时长时发生错误: {e}")

        return 0.0

    def get_position(self) -> float:
        """获取当前播放位置（秒）- 通过时间跟踪实现"""
        if not self._start_time:
            logger.debug("get_position: 没有开始时间，返回 0.0")
            return 0.0

        import time

        if self._is_paused and self._pause_time:
            # 如果暂停，返回暂停时的位置
            position = (self._pause_time - self._start_time) + self._seek_offset
            logger.debug(f"get_position: 暂停状态，位置 {position:.2f}秒")
        else:
            # 如果播放中，计算当前位置
            current_time = time.time()
            position = (current_time - self._start_time) + self._seek_offset
            logger.debug(f"get_position: 播放状态，位置 {position:.2f}秒")

        # 确保位置不超过歌曲时长
        duration = self.get_duration()
        if duration > 0 and position > duration:
            position = duration
            logger.debug(f"get_position: 位置超出时长，调整为 {position:.2f}秒")

        # 确保位置不为负数
        if position < 0:
            position = 0.0
            logger.debug("get_position: 位置为负数，调整为 0.0")

        return position

    def seek(self, position: float) -> bool:
        """跳转到指定位置（秒）- pygame有限支持"""
        if not self._pygame or not self._current_file:
            return False

        try:
            import time

            # pygame不支持直接跳转，但可以尝试使用set_pos()
            # 注意：这个功能在某些音频格式上可能不稳定
            if hasattr(self._pygame.mixer.music, "set_pos"):
                # set_pos接受秒为单位的位置
                self._pygame.mixer.music.set_pos(position)
                logger.info(f"pygame跳转到位置: {position:.2f}秒")

                # 更新位置跟踪
                self._start_time = time.time()
                self._seek_offset = position
                self._pause_time = None

                return True
            else:
                logger.warning("pygame版本不支持set_pos功能")

                # 尝试重新加载和播放
                try:
                    logger.info("尝试通过重新加载文件实现跳转")
                    was_playing = self.is_playing()
                    self._pygame.mixer.music.stop()
                    self._pygame.mixer.music.load(self._current_file)

                    if was_playing:
                        self._pygame.mixer.music.play(start=position)
                        # 更新位置跟踪
                        self._start_time = time.time()
                        self._seek_offset = position
                        self._pause_time = None
                        self._is_paused = False

                    return True
                except Exception as retry_e:
                    logger.error(f"pygame重新加载跳转也失败: {retry_e}")
                    return False

        except Exception as e:
            logger.error(f"pygame跳转位置失败: {e}")
            return False


class FletAudioPlayer:
    """基于 Flet flet_audio 扩展的音频播放器（Flet 运行时，含 iOS/Android）

    通过 Flutter 的 audioplayers 插件播放，底层是各平台原生播放器，
    不依赖 pygame/rubicon，是移动端的首选实现。
    """

    def __init__(self, page):
        import flet as ft
        import flet_audio as fta
        from flet_audio import AudioState

        self._ft = ft
        self._fta = fta
        self._AudioState = AudioState
        self._page = page
        self._audio = None
        self._current_file = None
        self._volume = 0.7
        self._state = AudioState.STOPPED
        self._duration = 0.0
        self._position_ms = 0
        self._position_ts = 0.0
        self._loaded = asyncio.Event()
        self._completed = False
        self._pause_requested = False
        self._tasks = set()

    def _run(self, coro):
        """在事件循环中调度协程（协议方法本身是同步的）"""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            task.add_done_callback(self._log_task_error)
        except RuntimeError:
            asyncio.run(coro)

    @staticmethod
    def _log_task_error(task):
        """记录后台音频命令异常，避免静默误报成功。"""
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Flet音频后台命令执行失败")

    def _to_src(self, file_path: str) -> str:
        """转换为音频源地址。

        浏览器会话无法读取服务器本地磁盘路径，run_web.py 已把音乐目录
        挂载为 /local-music/ 静态资源，Web 会话改用该 URL 播放。
        """
        try:
            if getattr(self._page, "web", False):
                from urllib.parse import quote

                return f"/local-music/{quote(os.path.basename(file_path))}"
        except Exception:
            pass
        return file_path

    def _ensure_audio(self, src: str):
        """Audio Service 必须带 src 创建（Dart 端 update() 无 src 会抛错）"""
        if self._audio is None:
            self._audio = self._fta.Audio(
                src=src,
                volume=self._volume,
                release_mode=self._fta.ReleaseMode.STOP,
                on_loaded=self._on_loaded,
                on_duration_change=self._on_duration_change,
                on_state_change=self._on_state_change,
                on_position_change=self._on_position_change,
            )
            # Flet 0.86 的 Service 构造时会自动注册。仅为测试桩或不自动注册
            # 的兼容运行时补注册，避免同一个 Audio 在 registry 中出现两次。
            if self._audio not in self._page.services:
                self._page.services.append(self._audio)
                self._page.update()

    def _on_loaded(self, _e):
        self._loaded.set()

    def _on_duration_change(self, e):
        try:
            self._duration = e.duration.in_seconds
        except Exception:
            pass

    def _on_state_change(self, e):
        try:
            # pause 是异步原生命令；暂停请求之后迟到的 PLAYING 事件不能
            # 再把播放器状态和界面恢复成播放。
            if self._pause_requested and e.state == self._AudioState.PLAYING:
                return
            self._state = e.state
            if e.state == self._AudioState.COMPLETED:
                self._completed = True
        except Exception:
            pass

    def _on_position_change(self, e):
        import time

        try:
            self._position_ms = int(e.position)
            self._position_ts = time.time()
        except Exception:
            pass

    def dispose(self):
        """移除失效的 Flet service，并取消尚未完成的原生命令。"""
        for task in tuple(self._tasks):
            task.cancel()
        self._tasks.clear()
        if self._audio is not None:
            try:
                self._page.services.remove(self._audio)
                self._page.update()
            except (ValueError, AttributeError):
                pass
        self._audio = None
        self._loaded.clear()

    def load(self, file_path: str) -> bool:
        import time

        try:
            file_path_str = (
                os.fspath(file_path)
                if hasattr(file_path, "__fspath__")
                else str(file_path)
            )
            if not os.path.exists(file_path_str):
                logger.error(f"音频文件不存在: {file_path_str}")
                return False

            src = self._to_src(file_path_str)
            source_changed = self._audio is None or self._audio.src != src
            if source_changed:
                self._loaded.clear()
            self._ensure_audio(src)
            if self._audio.src != src:
                self._audio.src = src
                self._page.update()
            self._current_file = file_path_str
            self._state = self._AudioState.STOPPED
            self._duration = 0.0
            self._position_ms = 0
            # 新曲收到首个 position 事件前，以加载时刻作为外推基准，不能
            # 沿用上一首的时间戳（初始值 0 也会被误算为已播放很久）。
            self._position_ts = time.time()
            self._completed = False
            self._pause_requested = False
            logger.info(f"Flet音频加载成功: {file_path_str} (src={src})")
            return True
        except Exception as e:
            logger.error(f"Flet加载音频失败: {e}")
            return False

    def play(self) -> bool:
        try:
            if not self._audio:
                return False
            self._pause_requested = False
            self._run(self._audio.play())
            logger.info("Flet开始播放音频")
            return True
        except Exception as e:
            logger.error(f"Flet播放失败: {e}")
            return False

    async def play_async(self) -> bool:
        """等待 service 注册提交后播放，并确认原生命令完成。"""
        if not self._audio:
            return False
        try:
            self._pause_requested = False
            # iOS audioplayers 对本地 DeviceFileSource 不保证触发 on_loaded；
            # page.update 已提交 service 后让出一轮循环即可发送 play。
            await asyncio.sleep(0)
            await self._audio.play()
            logger.info("Flet开始播放音频（已确认命令完成）")
            return True
        except Exception:
            logger.exception("Flet播放失败")
            return False

    def pause(self) -> bool:
        try:
            if not self._audio:
                return False
            self._pause_requested = True
            self._run(self.pause_async())
            logger.info("Flet暂停播放")
            return True
        except Exception as e:
            logger.error(f"Flet暂停失败: {e}")
            return False

    async def pause_async(self) -> bool:
        """等待原生暂停命令完成，避免迟到的播放事件恢复状态。"""
        if not self._audio:
            return False
        try:
            self._pause_requested = True
            await self._audio.pause()
            self._state = getattr(self._AudioState, "PAUSED", self._AudioState.STOPPED)
            return True
        except Exception:
            self._pause_requested = False
            logger.exception("Flet暂停失败")
            return False

    def stop(self) -> bool:
        try:
            if not self._audio:
                return False
            self._run(self.stop_async())
            return True
        except Exception as e:
            logger.error(f"Flet停止失败: {e}")
            return False

    async def stop_async(self) -> bool:
        """顺序完成暂停和归零，避免旧命令在新歌播放后才抵达。"""
        if not self._audio:
            return False
        try:
            await self._audio.pause()
            await self._audio.seek(self._ft.Duration(seconds=0))
            self._state = self._AudioState.STOPPED
            self._position_ms = 0
            return True
        except Exception:
            logger.exception("Flet停止音频失败")
            return False

    def is_playing(self) -> bool:
        try:
            return self._state == self._AudioState.PLAYING
        except Exception:
            return False

    def has_completed(self) -> bool:
        """返回并消费一次自然结束事件，避免 UI 轮询重复切歌。"""
        completed = self._completed
        self._completed = False
        return completed

    def set_volume(self, volume: float) -> bool:
        try:
            self._volume = max(0.0, min(1.0, volume))
            if self._audio:
                self._audio.volume = self._volume
            return True
        except Exception as e:
            logger.error(f"Flet设置音量失败: {e}")
            return False

    def get_duration(self) -> float:
        try:
            # Audio 会通过 on_duration_change 主动上报。UI 定时刷新中反复调用
            # invoke_method(get_duration) 会在 iOS listener 忙时堆积并超时。
            return self._duration
        except Exception:
            return 0.0

    def get_position(self) -> float:
        import time

        try:
            position = self._position_ms / 1000.0
            if self.is_playing() and self._position_ts > 0:
                # 位置事件约每秒一次，播放中按时间差外推，保证进度条平滑
                position += max(0.0, time.time() - self._position_ts)
                if self._duration > 0:
                    position = min(position, self._duration)
            return position
        except Exception:
            return 0.0

    def seek(self, position: float) -> bool:
        import time

        try:
            if not self._audio:
                return False
            self._run(self._audio.seek(self._ft.Duration(seconds=position)))
            self._position_ms = int(position * 1000)
            self._position_ts = time.time()
            return True
        except Exception as e:
            logger.error(f"Flet跳转失败: {e}")
            return False


class iOSAudioPlayer:
    """基于iOS AVFoundation的音频播放器（rubicon-objc）"""

    def __init__(self):
        self._player = None
        self._current_file = None
        self._volume = 0.7
        self._audio_manager = None
        self._completed = False
        self._completion_callback = None
        self._delegate = None
        self._init_avfoundation()

    def _init_avfoundation(self):
        """初始化AVFoundation"""
        try:
            # 尝试导入iOS的AVFoundation
            from rubicon.objc import NSObject, ObjCClass, ObjCProtocol, objc_method

            # 获取AVFoundation类
            self.AVAudioPlayer = ObjCClass("AVAudioPlayer")
            self.NSURL = ObjCClass("NSURL")
            self.NSString = ObjCClass("NSString")
            self.AVAudioSession = ObjCClass("AVAudioSession")

            owner = self
            try:
                protocols = [ObjCProtocol("AVAudioPlayerDelegate")]
            except NameError:
                protocols = []

            class _AudioPlayerDelegate(NSObject, protocols=protocols):
                @objc_method
                def audioPlayerDidFinishPlaying_successfully_(
                    self, player, successfully: bool
                ):
                    owner._handle_playback_finished(bool(successfully))

            self._delegate = _AudioPlayerDelegate.alloc().init()

            # 初始化后台音频管理器
            try:
                from .ios_background_audio import get_ios_audio_manager

                self._audio_manager = get_ios_audio_manager()
                if self._audio_manager and self._audio_manager.is_configured:
                    logger.info("iOS后台音频管理器配置成功")
                else:
                    logger.warning("iOS后台音频管理器配置失败")
            except ImportError:
                logger.warning("无法导入iOS后台音频管理器")

            # 配置音频会话以支持后台播放
            session = self.AVAudioSession.sharedInstance()

            # 设置音频会话类别为播放，支持后台播放
            try:
                # 尝试使用更完整的API
                success = session.setCategory_withOptions_error_(
                    "AVAudioSessionCategoryPlayback",
                    0,  # AVAudioSessionCategoryOptions 默认
                    None,
                )

                if not success:
                    # 回退到简单API
                    success = session.setCategory_error_(
                        "AVAudioSessionCategoryPlayback", None
                    )

                if success:
                    session.setActive_error_(True, None)
                    logger.info("iOS音频会话配置成功，支持后台播放")
                else:
                    logger.warning("设置iOS音频会话类别失败")

            except Exception as e:
                logger.warning(f"配置iOS音频会话失败: {e}")
                # 最后尝试简单方法
                try:
                    session.setCategory("AVAudioSessionCategoryPlayback", error=None)
                    session.setActive(True, error=None)
                    logger.info("iOS音频会话配置成功（简单方法）")
                except:
                    logger.error("所有iOS音频会话配置方法都失败")

            logger.info("iOS AVFoundation音频播放器初始化成功")

        except ImportError as e:
            logger.error(f"无法导入AVFoundation: {e}")
        except Exception as e:
            logger.error(f"初始化iOS音频播放器失败: {e}")

    def load(self, file_path: str) -> bool:
        """加载音频文件"""
        try:
            # 确保文件路径是字符串格式
            if hasattr(file_path, "__fspath__"):
                file_path_str = os.fspath(file_path)
            else:
                file_path_str = str(file_path)

            if not os.path.exists(file_path_str):
                logger.error(f"音频文件不存在: {file_path_str}")
                return False

            # 创建NSURL - 确保传入字符串
            file_url = self.NSURL.fileURLWithPath(
                self.NSString.stringWithString(file_path_str)
            )
            logger.debug(f"iOS load: 创建文件URL: {file_url}")

            # 创建AVAudioPlayer
            error_ptr = None
            self._player = self.AVAudioPlayer.alloc().initWithContentsOfURL_error_(
                file_url, error_ptr
            )

            if self._player:
                self._player.delegate = self._delegate
                # 准备播放
                prepare_success = self._player.prepareToPlay()
                logger.debug(f"iOS load: prepareToPlay 结果: {prepare_success}")

                # 设置音量
                self._player.setVolume_(self._volume)

                # 验证加载是否成功
                duration = self._player.duration  # 这是属性，不是方法
                logger.info(f"iOS音频文件加载成功: {file_path}, 时长: {duration:.2f}秒")

                self._current_file = file_path
                self._completed = False
                return True
            else:
                logger.error(f"无法创建AVAudioPlayer: {file_path}")
                if error_ptr:
                    logger.error(f"错误详情: {error_ptr}")
                return False

        except Exception as e:
            logger.error(f"iOS加载音频文件失败: {e}")
            import traceback

            logger.error(f"错误堆栈: {traceback.format_exc()}")
            return False

    def play(self) -> bool:
        """播放音频"""
        try:
            # 激活音频会话
            if self._audio_manager:
                self._audio_manager.activate_session()

            if self._player:
                # AVAudioPlayer.play() 在 Objective-C 中返回 void，
                # 不能用返回值判断成败，这里以 isPlaying 属性为准
                self._player.play()
                if self._player.isPlaying:
                    logger.info("iOS开始播放音频")
                    return True
                logger.warning("iOS调用play()后isPlaying仍为False")
                return False
            return False
        except Exception as e:
            logger.error(f"iOS播放音频失败: {e}")
            return False

    def pause(self) -> bool:
        """暂停播放"""
        try:
            if self._player:
                self._player.pause()
                logger.info("iOS暂停播放")
                return True
            return False
        except Exception as e:
            logger.error(f"iOS暂停播放失败: {e}")
            return False

    def stop(self) -> bool:
        """停止播放"""
        try:
            if self._player:
                self._player.stop()
                logger.info("iOS停止播放")

                # 可选：停止播放后停用音频会话（如果不需要保持后台能力）
                # if self._audio_manager:
                #     self._audio_manager.deactivate_session()

                return True
            return False
        except Exception as e:
            logger.error(f"iOS停止播放失败: {e}")
            return False

    def is_playing(self) -> bool:
        """检查是否正在播放"""
        try:
            if self._player:
                # isPlaying 是属性，不是方法
                return bool(self._player.isPlaying)
            return False
        except Exception:
            return False

    def set_completion_callback(self, callback) -> None:
        """Register a callback fired directly by AVAudioPlayer at natural EOF."""
        self._completion_callback = callback

    def _handle_playback_finished(self, successfully: bool) -> None:
        self._completed = successfully
        if successfully and self._completion_callback:
            try:
                self._completion_callback()
            except Exception:
                logger.exception("iOS播放完成回调失败")

    def has_completed(self) -> bool:
        completed = self._completed
        self._completed = False
        return completed

    def set_volume(self, volume: float) -> bool:
        """设置音量 (0.0-1.0)"""
        try:
            self._volume = max(0.0, min(1.0, volume))
            if self._player:
                self._player.setVolume_(self._volume)
            return True
        except Exception as e:
            logger.error(f"iOS设置音量失败: {e}")
            return False

    def get_duration(self) -> float:
        """获取音频时长（秒）"""
        try:
            if self._player:
                duration = self._player.duration  # 这是属性，不是方法
                logger.debug(f"iOS get_duration: raw={duration}")
                # 检查是否为有效时长
                if duration is not None and duration > 0:
                    return float(duration)
                else:
                    logger.warning(f"iOS get_duration: 无效时长 {duration}")
                    return 0.0
            logger.debug("iOS get_duration: 没有播放器")
            return 0.0
        except Exception as e:
            logger.error(f"iOS get_duration 异常: {e}")
            return 0.0

    def get_position(self) -> float:
        """获取当前播放位置（秒）"""
        try:
            if self._player:
                position = self._player.currentTime  # 这是属性，不是方法

                # iOS特殊处理：添加防抖机制，减少频繁的位置查询导致的卡顿
                if hasattr(self, "_last_position_time"):
                    import time

                    current_time = time.time()
                    # 如果距离上次查询不到0.1秒，使用缓存值
                    if current_time - self._last_position_time < 0.1:
                        if hasattr(self, "_cached_position"):
                            logger.debug(
                                f"iOS get_position: 使用缓存位置 {self._cached_position:.2f}"
                            )
                            return self._cached_position

                logger.debug(f"iOS get_position: raw={position}")

                # 检查是否为有效位置
                if position is not None and position >= 0:
                    # 缓存位置和时间
                    import time

                    self._cached_position = float(position)
                    self._last_position_time = time.time()
                    return self._cached_position
                else:
                    logger.warning(f"iOS get_position: 无效位置 {position}")
                    return 0.0
            logger.debug("iOS get_position: 没有播放器")
            return 0.0
        except Exception as e:
            logger.error(f"iOS get_position 异常: {e}")
            return 0.0

    def seek(self, position: float) -> bool:
        """跳转到指定位置（秒）"""
        try:
            if self._player:
                # iOS特殊处理：添加防抖机制，避免频繁seek
                if hasattr(self, "_last_seek_time"):
                    import time

                    current_time = time.time()
                    # 如果距离上次seek不到0.2秒，忽略这次操作
                    if current_time - self._last_seek_time < 0.2:
                        logger.debug(f"iOS seek: 忽略频繁的seek操作 {position}")
                        return True

                # 在AVAudioPlayer中，currentTime是可读写属性
                self._player.currentTime = position
                logger.debug(f"iOS seek: 设置位置为 {position}")

                # 记录seek时间，用于防抖
                import time

                self._last_seek_time = time.time()

                # 清除位置缓存，强制下次重新获取
                if hasattr(self, "_cached_position"):
                    del self._cached_position
                if hasattr(self, "_last_position_time"):
                    del self._last_position_time

                return True
            return False
        except Exception as e:
            logger.error(f"iOS跳转位置失败: {e}")
            return False


class FallbackAudioPlayer:
    """备用音频播放器（使用系统命令）"""

    def __init__(self):
        self._current_file = None
        self._process = None
        self._volume = 0.7

    def load(self, file_path: str) -> bool:
        """加载音频文件"""
        if not os.path.exists(file_path):
            logger.error(f"音频文件不存在: {file_path}")
            return False

        self._current_file = file_path
        logger.info(f"备用播放器加载文件: {file_path}")
        return True

    def play(self) -> bool:
        """播放音频"""
        if not self._current_file:
            return False

        try:
            import shlex
            import subprocess

            # 尝试使用系统音频播放命令
            if is_macos():
                # macOS使用afplay
                cmd = f"afplay {shlex.quote(self._current_file)}"
            else:
                # 其他平台尝试常见播放器
                cmd = f"mpg123 {shlex.quote(self._current_file)}"

            # 在后台执行
            self._process = subprocess.Popen(cmd, shell=True)
            logger.info(f"备用播放器开始播放: {self._current_file}")
            return True

        except Exception as e:
            logger.error(f"备用播放器播放失败: {e}")
            return False

    def pause(self) -> bool:
        """暂停播放"""
        # 备用播放器暂不支持暂停
        return False

    def stop(self) -> bool:
        """停止播放"""
        try:
            if self._process:
                self._process.terminate()
                self._process = None
                logger.info("备用播放器停止播放")
                return True
            return False
        except Exception as e:
            logger.error(f"备用播放器停止失败: {e}")
            return False

    def is_playing(self) -> bool:
        """检查是否正在播放"""
        if self._process:
            return self._process.poll() is None
        return False

    def set_volume(self, volume: float) -> bool:
        """设置音量 (0.0-1.0)"""
        self._volume = max(0.0, min(1.0, volume))
        # 备用播放器暂不支持音量控制
        return True

    def get_duration(self) -> float:
        """获取音频时长（秒）"""
        return -1.0

    def get_position(self) -> float:
        """获取当前播放位置（秒）"""
        return -1.0

    def seek(self, position: float) -> bool:
        """跳转到指定位置（秒）"""
        return False


def create_audio_player(page=None) -> AudioPlayerProtocol:
    """创建适合当前运行环境的音频播放器

    Args:
        page: Flet Page 实例。非 iOS 的 Flet 运行时使用 Audio service。
              iOS 优先使用 AVFoundation，保持界面重构前已验证的播放路径。
    """

    if is_ios():
        logger.info("检测到iOS平台，优先创建AVFoundation音频播放器")
        player = iOSAudioPlayer()
        if hasattr(player, "AVAudioPlayer"):
            return player
        logger.warning("iOS AVFoundation初始化失败，尝试Flet Audio播放器")

    if page is not None:
        try:
            player = FletAudioPlayer(page)
            logger.info("检测到Flet运行时，创建Flet Audio音频播放器")
            return player
        except Exception as e:
            logger.warning(f"Flet Audio播放器创建失败: {e}，尝试其他播放器")

    if is_ios():
        logger.warning("iOS播放器初始化失败，使用备用播放器")
        return FallbackAudioPlayer()

    else:
        # 桌面平台尝试使用pygame
        logger.info("检测到桌面平台，创建pygame音频播放器")
        player = PygameAudioPlayer()

        # 验证pygame是否可用
        if player._pygame:
            return player
        else:
            logger.warning("pygame不可用，使用备用播放器")
            return FallbackAudioPlayer()
