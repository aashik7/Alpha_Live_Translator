"""Instrumented locks — log slow waits for deadlock/hang forensics."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

_SLOW_WAIT_MS = 100.0
_VERY_SLOW_WAIT_MS = 1000.0


def _log_lock_event(event: str, lock_name: str, wait_ms: float, **extra: Any) -> None:
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


class InstrumentedRLock:
    """RLock wrapper that logs slow acquisition waits."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.RLock()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        start = time.perf_counter()
        if timeout < 0:
            acquired = self._lock.acquire(blocking=blocking)
        else:
            acquired = self._lock.acquire(blocking=blocking, timeout=timeout)
        if not acquired:
            return False
        wait_ms = (time.perf_counter() - start) * 1000.0
        if wait_ms > _VERY_SLOW_WAIT_MS:
            _log_lock_event("LOCK_WAIT_VERY_SLOW", self._name, wait_ms)
            _log_lock_event("LOCK_POTENTIAL_DEADLOCK_SUSPECTED", self._name, wait_ms)
        elif wait_ms > _SLOW_WAIT_MS:
            _log_lock_event("LOCK_WAIT_SLOW", self._name, wait_ms)
        if wait_ms > _SLOW_WAIT_MS:
            _log_lock_event("LOCK_ACQUIRED_AFTER_WAIT", self._name, wait_ms)
        return True

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "InstrumentedRLock":
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


def instrumented_lock(name: str) -> InstrumentedRLock:
    return InstrumentedRLock(name)
