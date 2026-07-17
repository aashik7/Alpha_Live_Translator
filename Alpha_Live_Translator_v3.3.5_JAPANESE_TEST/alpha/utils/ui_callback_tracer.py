"""Timing wrapper for Tkinter root.after callbacks."""

from __future__ import annotations

import threading
import time
import traceback
from functools import wraps
from typing import Any, Callable, Optional

_tracer_lock = threading.Lock()
_last_callback_name: str = ""
_last_callback_start_ts: float = 0.0
_last_callback_end_ts: float = 0.0
_last_callback_duration_ms: float = 0.0
_last_successful_callback_name: str = ""
_last_successful_callback_ts: float = 0.0
_installed = False


def get_ui_callback_trace() -> dict[str, Any]:
    with _tracer_lock:
        return {
            "last_ui_callback_name": _last_callback_name,
            "last_ui_callback_start_ts": _last_callback_start_ts,
            "last_ui_callback_end_ts": _last_callback_end_ts,
            "last_ui_callback_duration_ms": round(_last_callback_duration_ms, 2),
            "last_successful_ui_callback_name": _last_successful_callback_name,
            "last_successful_ui_callback_ts": _last_successful_callback_ts,
        }


def _record_callback(
    name: str,
    *,
    start_ts: float,
    end_ts: float,
    duration_ms: float,
    success: bool,
) -> None:
    global _last_callback_name, _last_callback_start_ts, _last_callback_end_ts
    global _last_callback_duration_ms, _last_successful_callback_name
    global _last_successful_callback_ts
    with _tracer_lock:
        _last_callback_name = name
        _last_callback_start_ts = start_ts
        _last_callback_end_ts = end_ts
        _last_callback_duration_ms = duration_ms
        if success:
            _last_successful_callback_name = name
            _last_successful_callback_ts = end_ts


def _log_slow(name: str, duration_ms: float, exc: Optional[BaseException] = None) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        if exc is not None:
            jp_accuracy_log(
                "UI_AFTER_CALLBACK_EXCEPTION",
                callback_name=name,
                duration_ms=round(duration_ms, 2),
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            return
        if duration_ms > 200:
            jp_accuracy_log(
                "UI_AFTER_CALLBACK_VERY_SLOW",
                callback_name=name,
                duration_ms=round(duration_ms, 2),
            )
        elif duration_ms > 50:
            jp_accuracy_log(
                "UI_AFTER_CALLBACK_SLOW",
                callback_name=name,
                duration_ms=round(duration_ms, 2),
            )
    except Exception:
        pass


def wrap_after_callback(func: Callable[..., Any], name: str = "") -> Callable[..., Any]:
    cb_name = name or getattr(func, "__name__", repr(func))

    @wraps(func)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        start_ts = time.time()
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000.0
            _record_callback(
                cb_name,
                start_ts=start_ts,
                end_ts=time.time(),
                duration_ms=duration_ms,
                success=False,
            )
            _log_slow(cb_name, duration_ms, exc=exc)
            raise
        duration_ms = (time.perf_counter() - start) * 1000.0
        _record_callback(
            cb_name,
            start_ts=start_ts,
            end_ts=time.time(),
            duration_ms=duration_ms,
            success=True,
        )
        if duration_ms > 50:
            _log_slow(cb_name, duration_ms)
        return result

    return _wrapped


def install_ui_after_callback_tracer(app_cls: type) -> None:
    global _installed
    if _installed:
        return
    _installed = True
    _orig_after = app_cls.after

    def patched_after(self, ms, func=None, *args, **kwargs):
        if func is None:
            return _orig_after(self, ms)
        wrapped = wrap_after_callback(func)
        return _orig_after(self, ms, wrapped, *args, **kwargs)

    app_cls.after = patched_after  # type: ignore[method-assign]
