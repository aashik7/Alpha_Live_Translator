"""Monitored locks — slow-wait logging, try-acquire, owner tracking."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

_SLOW_WAIT_MS = 100.0
_VERY_SLOW_WAIT_MS = 1000.0

_lock_stats: dict[str, Any] = {
    "assembler_lock_owner": None,
    "assembler_lock_waiters": 0,
    "callback_under_lock_blocked_count": 0,
}


def get_lock_monitor_stats() -> dict[str, Any]:
    return dict(_lock_stats)


def _log_lock(event: str, lock_name: str, wait_ms: float, **extra: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            event,
            lock_name=lock_name,
            wait_ms=round(wait_ms, 2),
            thread_name=threading.current_thread().name,
            **extra,
        )
    except Exception:
        pass


class MonitoredRLock:
    """RLock with slow-wait instrumentation and optional try-acquire."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.RLock()
        self._owner_thread_name: str = ""

    @property
    def name(self) -> str:
        return self._name

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        start = time.perf_counter()
        if timeout < 0:
            acquired = self._lock.acquire(blocking=blocking)
        else:
            acquired = self._lock.acquire(blocking=blocking, timeout=timeout)
        if not acquired:
            if self._name == "japanese_assembler":
                _lock_stats["assembler_lock_waiters"] = (
                    int(_lock_stats.get("assembler_lock_waiters", 0)) + 1
                )
            return False
        wait_ms = (time.perf_counter() - start) * 1000.0
        self._owner_thread_name = threading.current_thread().name
        if self._name == "japanese_assembler":
            _lock_stats["assembler_lock_owner"] = self._owner_thread_name
            _lock_stats["assembler_lock_waiters"] = max(
                0, int(_lock_stats.get("assembler_lock_waiters", 0)) - 1
            )
        if wait_ms > _VERY_SLOW_WAIT_MS:
            _log_lock("LOCK_WAIT_VERY_SLOW", self._name, wait_ms)
        elif wait_ms > _SLOW_WAIT_MS:
            _log_lock("LOCK_WAIT_SLOW", self._name, wait_ms)
        if wait_ms > _SLOW_WAIT_MS:
            _log_lock(
                "LOCK_OWNER_RECORDED",
                self._name,
                wait_ms,
                owner=self._owner_thread_name,
            )
        return True

    def try_acquire(self, timeout: float = 0.0) -> bool:
        return self.acquire(blocking=True, timeout=timeout)

    def release(self) -> None:
        self._lock.release()
        if self._name == "japanese_assembler":
            _lock_stats["assembler_lock_owner"] = None

    def __enter__(self) -> "MonitoredRLock":
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


def monitored_lock(name: str) -> MonitoredRLock:
    return MonitoredRLock(name)
