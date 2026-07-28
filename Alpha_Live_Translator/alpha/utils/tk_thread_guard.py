"""Runtime guard — block or reroute Tkinter scheduling from background threads."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from alpha.constants import FORBID_BACKGROUND_TK_CALLS

_guard_installed = False
_guard_registration_mono: Optional[float] = None
_background_tk_call_blocked_count = 0
_background_tk_call_blocked_count_startup_pre_guard = 0
_background_tk_call_blocked_count_active_session = 0
_background_tk_call_blocked_count_stop_finalize = 0
_session_listening_active = False
_stop_finalize_active = False
_tk_call_sites_safe = 0
_tk_call_sites_refactored = 0
_scan_completed = False
_guard_lock = threading.Lock()

_STARTUP_CALLBACKS = frozenset(
    {
        "update",
        "check_dpi_scaling",
        "_update_dimensions_event",
        "_windows_set_titlebar_icon",
        "_deferred_apply_logo",
        "_deferred_post_show_init",
        "_emit_deferred_startup_logs",
        "_set_initial_pane_ratio",
    }
)


def set_tk_guard_session_active(active: bool) -> None:
    global _session_listening_active
    with _guard_lock:
        _session_listening_active = active


def set_tk_guard_stop_finalize_active(active: bool) -> None:
    global _stop_finalize_active
    with _guard_lock:
        _stop_finalize_active = active


def get_tk_guard_stats() -> dict[str, int]:
    with _guard_lock:
        return {
            "background_tk_call_blocked_count": _background_tk_call_blocked_count,
            "background_tk_call_blocked_count_total": _background_tk_call_blocked_count,
            "background_tk_call_blocked_count_startup_pre_guard": (
                _background_tk_call_blocked_count_startup_pre_guard
            ),
            "background_tk_call_blocked_count_active_session": (
                _background_tk_call_blocked_count_active_session
            ),
            "background_tk_call_blocked_count_stop_finalize": (
                _background_tk_call_blocked_count_stop_finalize
            ),
            "tk_call_sites_safe": _tk_call_sites_safe,
            "tk_call_sites_refactored": _tk_call_sites_refactored,
        }


def _classify_blocked_tk_call(callback_name: str, thread_name: str) -> str:
    if _stop_finalize_active or thread_name.startswith(("StopStep-", "StopFinalize")):
        return "stop_finalize"
    if _session_listening_active:
        return "active_session"
    if _guard_registration_mono is not None and (
        time.monotonic() - _guard_registration_mono < 10.0
    ):
        if callback_name in _STARTUP_CALLBACKS:
            return "startup_pre_guard"
    if thread_name in ("MainThread", "ui_main"):
        return "startup_pre_guard"
    return "startup_pre_guard"


def _increment_blocked(category: str) -> None:
    global _background_tk_call_blocked_count
    global _background_tk_call_blocked_count_startup_pre_guard
    global _background_tk_call_blocked_count_active_session
    global _background_tk_call_blocked_count_stop_finalize
    with _guard_lock:
        _background_tk_call_blocked_count += 1
        if category == "startup_pre_guard":
            _background_tk_call_blocked_count_startup_pre_guard += 1
        elif category == "active_session":
            _background_tk_call_blocked_count_active_session += 1
        elif category == "stop_finalize":
            _background_tk_call_blocked_count_stop_finalize += 1
        else:
            _background_tk_call_blocked_count_startup_pre_guard += 1


def _increment_refactored() -> None:
    global _tk_call_sites_refactored
    with _guard_lock:
        _tk_call_sites_refactored += 1


def install_tk_thread_guard(app_cls: type) -> None:
    """Patch after/after_cancel to reroute background Tk scheduling via UIEventBus."""
    global _guard_installed, _guard_registration_mono
    if _guard_installed:
        return
    _guard_installed = True
    _guard_registration_mono = time.monotonic()

    from alpha.utils.ui_thread_guard import is_ui_main_thread

    _orig_after = app_cls.after
    _orig_cancel = app_cls.after_cancel

    def guarded_after(self, ms, func=None, *args, **kwargs):
        if FORBID_BACKGROUND_TK_CALLS and not is_ui_main_thread():
            cb_name = getattr(func, "__name__", repr(func))
            thread_name = threading.current_thread().name
            category = _classify_blocked_tk_call(cb_name, thread_name)
            _increment_blocked(category)
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                event = (
                    "BACKGROUND_TK_CALL_STARTUP_PRE_GUARD"
                    if category == "startup_pre_guard"
                    else "BACKGROUND_TK_CALL_BLOCKED"
                )
                jp_accuracy_log(
                    event,
                    operation="after",
                    interval_ms=ms,
                    callback_name=cb_name,
                    thread_name=thread_name,
                    tk_guard_category=category,
                )
            except Exception:
                pass
            if func is not None:
                from alpha.utils.ui_event_bus import get_ui_event_bus

                get_ui_event_bus().post_schedule_after(int(ms), func, args, kwargs)
                _increment_refactored()
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "TK_CALL_SITE_REFACTORED",
                        operation="after",
                        callback_name=cb_name,
                    )
                except Exception:
                    pass
            return ""
        return _orig_after(self, ms, func, *args, **kwargs)

    def guarded_cancel(self, after_id):
        if FORBID_BACKGROUND_TK_CALLS and not is_ui_main_thread():
            thread_name = threading.current_thread().name
            category = _classify_blocked_tk_call("after_cancel", thread_name)
            _increment_blocked(category)
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                event = (
                    "BACKGROUND_TK_CALL_STARTUP_PRE_GUARD"
                    if category == "startup_pre_guard"
                    else "BACKGROUND_TK_CALL_BLOCKED"
                )
                jp_accuracy_log(
                    event,
                    operation="after_cancel",
                    after_id=str(after_id),
                    thread_name=thread_name,
                    tk_guard_category=category,
                )
            except Exception:
                pass
            return
        return _orig_cancel(self, after_id)

    app_cls.after = guarded_after  # type: ignore[method-assign]
    app_cls.after_cancel = guarded_cancel  # type: ignore[method-assign]

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("TK_THREAD_GUARD_INSTALLED")
    except Exception:
        pass


def scan_tk_call_sites(project_root: Optional[Any] = None) -> dict[str, int]:
    """Static scan of .after( in allowed modules — diagnostic only."""
    global _scan_completed, _tk_call_sites_safe
    from pathlib import Path

    root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
    patterns = ("alpha/ui/main_window.py", "alpha/transcription/japanese_sentence_assembler.py")
    safe = 0
    for rel in patterns:
        path = root / rel
        if path.exists():
            safe += path.read_text(encoding="utf-8", errors="ignore").count(".after(")
    with _guard_lock:
        _tk_call_sites_safe = safe
        _scan_completed = True
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("TK_CALL_SITE_SCAN_COMPLETED", safe_count=safe)
        jp_accuracy_log("TK_CALL_SITE_SAFE", count=safe)
    except Exception:
        pass
    return {"safe": safe, "refactored": _tk_call_sites_refactored}
