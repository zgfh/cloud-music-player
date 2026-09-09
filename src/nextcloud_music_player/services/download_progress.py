"""线程安全的下载进度状态：按文件维护下载列表，供下载任务与设置页共享。

原生后台下载的进度/完成回调来自系统队列线程，requests 回退路径来自
线程池，因此所有方法都必须线程安全。
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Iterable, Union

# 终态之外的速度计算最小时间片，避免除零或瞬间速度爆炸
_MIN_ELAPSED = 0.001


@dataclass
class DownloadFileState:
    """单个文件的下载状态"""

    filename: str
    status: str = "queued"  # queued / downloading / completed / failed
    downloaded_bytes: int = 0
    total_bytes: int = 0
    message: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    def as_dict(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        data = asdict(self)
        data.pop("started_at")
        data.pop("finished_at")
        end = self.finished_at or now
        elapsed = max(end - self.started_at, 0.0) if self.started_at else 0.0
        speed = self.downloaded_bytes / elapsed if elapsed > 0 else 0.0
        remaining = max(self.total_bytes - self.downloaded_bytes, 0)
        data["elapsed_seconds"] = elapsed
        data["eta_seconds"] = (
            remaining / speed
            if self.status == "downloading" and remaining and speed > 0
            else 0.0
        )
        return data


class DownloadProgressTracker:
    """下载列表状态机：enqueue 入队、update 进度、finish 终态、snapshot 聚合。"""

    def __init__(self):
        self._lock = threading.Lock()
        # 保序（入队顺序），文件名为键；同名重新下载会重置条目
        self._files: dict[str, DownloadFileState] = {}

    def enqueue(self, filenames: Union[str, Iterable[str]]) -> int:
        """入队一个或多个文件，返回实际新增/重置的数量"""
        names = [filenames] if isinstance(filenames, str) else list(filenames)
        added = 0
        with self._lock:
            for name in names:
                state = self._files.get(name)
                if state is None or state.status in ("completed", "failed"):
                    self._files[name] = DownloadFileState(filename=name)
                    added += 1
        return added

    def mark_downloading(self, filename: str) -> None:
        """标记开始传输（重置该文件的进度计数）"""
        with self._lock:
            state = self._files.setdefault(
                filename, DownloadFileState(filename=filename)
            )
            state.status = "downloading"
            state.message = ""
            state.downloaded_bytes = 0
            state.total_bytes = 0
            state.started_at = time.monotonic()
            state.finished_at = 0.0

    def update(
        self, filename: str, downloaded_bytes: int, total_bytes: int = 0
    ) -> None:
        """更新某个文件的进度；迟到的进度不会复活已终态的条目"""
        with self._lock:
            state = self._files.get(filename)
            if state is None:
                return
            if state.status == "queued":
                # 进度先于标记到达（原生任务已开始）：就地激活
                state.status = "downloading"
                state.started_at = time.monotonic()
            elif state.status != "downloading":
                return
            state.downloaded_bytes = max(0, int(downloaded_bytes))
            if total_bytes:
                state.total_bytes = max(0, int(total_bytes))

    def finish(self, filename: str, success: bool, message: str = "") -> None:
        """标记终态；success 时把进度补齐到总量"""
        with self._lock:
            state = self._files.setdefault(
                filename, DownloadFileState(filename=filename)
            )
            state.status = "completed" if success else "failed"
            state.message = message or ("下载完成" if success else "下载失败")
            now = time.monotonic()
            if not state.started_at:
                state.started_at = now
            state.finished_at = now
            if success and state.total_bytes:
                state.downloaded_bytes = max(state.downloaded_bytes, state.total_bytes)

    def file_states(self) -> list[dict]:
        """按入队顺序返回各文件状态（UI 列表用）"""
        with self._lock:
            now = time.monotonic()
            return [state.as_dict(now) for state in self._files.values()]

    def snapshot(self) -> dict:
        """聚合计数与活动文件信息；字段与设置页摘要消费方对齐"""
        with self._lock:
            states = list(self._files.values())
            now = time.monotonic()
        if not states:
            return {
                "status": "idle",
                "filename": "",
                "queued": 0,
                "downloading": 0,
                "completed": 0,
                "failed": 0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "speed_bps": 0.0,
                "elapsed_seconds": 0.0,
                "eta_seconds": 0.0,
                "message": "暂无下载任务",
            }

        counts = {"queued": 0, "downloading": 0, "completed": 0, "failed": 0}
        for state in states:
            counts[state.status] += 1

        active = [s for s in states if s.status == "downloading"]
        speed = 0.0
        for state in active:
            elapsed = max(now - state.started_at, _MIN_ELAPSED)
            speed += state.downloaded_bytes / elapsed

        if active:
            # 展示最近开始传输的文件
            current = max(active, key=lambda s: s.started_at)
            status = "downloading"
            filename = current.filename
        elif counts["queued"]:
            status = "queued"
            filename = ""
        elif counts["completed"] and counts["failed"]:
            status = "partial"
            filename = ""
        elif counts["failed"]:
            status = "failed"
            filename = states[-1].filename
        else:
            status = "completed"
            filename = states[-1].filename

        last_terminal = next(
            (s for s in reversed(states) if s.status in ("completed", "failed")), None
        )
        started_times = [s.started_at for s in states if s.started_at]
        if started_times:
            end_time = (
                now
                if active
                else max((s.finished_at for s in states if s.finished_at), default=now)
            )
            elapsed_seconds = max(end_time - min(started_times), 0.0)
        else:
            elapsed_seconds = 0.0
        downloaded_bytes = sum(s.downloaded_bytes for s in states)
        total_bytes = sum(s.total_bytes for s in states)
        eta_seconds = (
            max(total_bytes - downloaded_bytes, 0) / speed
            if active and total_bytes > downloaded_bytes and speed > 0
            else 0.0
        )
        return {
            "status": status,
            "filename": filename,
            "queued": counts["queued"],
            "downloading": counts["downloading"],
            "completed": counts["completed"],
            "failed": counts["failed"],
            "downloaded_bytes": downloaded_bytes,
            "total_bytes": total_bytes,
            "speed_bps": speed,
            "elapsed_seconds": elapsed_seconds,
            "eta_seconds": eta_seconds,
            "message": last_terminal.message if last_terminal else "",
        }
