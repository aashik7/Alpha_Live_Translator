"""UI main-thread identification and cross-thread / blocking-call guards."""

from __future__ import annotations

import threading
from typing import Any, Optional

UI_MAIN_THREAD_ID: Optional[int] = None
_ui_thread_blocking_call_blocked_count = 0
_tkinter_cross_thread_access_blocked_count = 0
_guard_lock = threading.Lock()


def register_ui_main_thread() -> None:
    global UI_MAIN_THREAD_ID
    UI_MAIN_THREAD_ID = threading.get_ident()


def is_ui_main_thread() -> bool:
    if UI_MAIN_THREAD_ID is None:
        return False
    return threading.get_ident() == UI_MAIN_THREAD_ID


def increment_ui_thread_blocking_blocked() -> None:
    global _ui_thread_blocking_call_blocked_count
    with _guard_lock:
        _ui_thread_blocking_call_blocked_count += 1


def increment_tkinter_cross_thread_blocked() -> None:
    global _tkinter_cross_thread_access_blocked_count
    with _guard_lock:
        _tkinter_cross_thread_access_blocked_count += 1


def get_guard_counters() -> dict[str, int]:
    with _guard_lock:
        return {
            "ui_thread_blocking_call_blocked_count": int(
                _ui_thread_blocking_call_blocked_count
            ),
            "tkinter_cross_thread_access_blocked_count": int(
                _tkinter_cross_thread_access_blocked_count
            ),
        }


def log_tkinter_cross_thread_access_blocked(operation: str, **extra: Any) -> None:
    increment_tkinter_cross_thread_blocked()
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "TKINTER_CROSS_THREAD_ACCESS_BLOCKED",
            operation=operation,
            thread_name=threading.current_thread().name,
            **extra,
        )
    except Exception:
        pass


def guard_ui_thread_blocking_call(operation: str, **extra: Any) -> bool:
    """Return True if call is allowed; False if blocked on UI thread."""
    if not is_ui_main_thread():
        return True
    increment_ui_thread_blocking_blocked()
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "UI_THREAD_BLOCKING_CALL_BLOCKED",
            operation=operation,
            **extra,
        )
    except Exception:
        pass
    return False


def require_background_thread(operation: str) -> bool:
    """Return True if current thread may perform Tkinter/widget access."""
    if is_ui_main_thread():
        return True
    log_tkinter_cross_thread_access_blocked(operation)
    return False
