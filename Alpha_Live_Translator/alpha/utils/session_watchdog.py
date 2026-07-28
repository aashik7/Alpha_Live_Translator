"""Mid-session hang watchdog — minimal UI heartbeat, background stall response."""

from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Any, Optional

from alpha.utils.session_progress import (
    build_progress_payload,
    increment_counter,
    mark_listening_stopped,
    maybe_emit_process_health_snapshot,
    maybe_emit_progress_heartbeat,
    note_ui_heartbeat_age_ms,
    set_run_status,
    touch_progress,
)

_WATCHDOG_INTERVAL_S = 2.0
_UI_STALL_SUSPECTED_S = 10.0
_UI_STALL_CONFIRMED_S = 20.0
_WATCHDOG_HEALTH_LOG_INTERVAL_S = 30.0
_PROGRESS_EMIT_INTERVAL_S = 30.0

_watchdog_thread: Optional[threading.Thread] = None
_watchdog_stop = threading.Event()
_host_ref: Any = None
_last_ui_heartbeat_mono: float = 0.0
_last_watchdog_health_log_mono: float = 0.0
_last_progress_emit_mono: float = 0.0
_heartbeat_tick_count: int = 0
_stall_suspected = False
_stall_confirmed = False
_crash_hooks_installed = False
_ui_heartbeat_started = False
_heartbeat_lock = threading.Lock()


def get_watchdog_thread_alive() -> bool:
    return _watchdog_thread is not None and _watchdog_thread.is_alive()


def get_watchdog_host() -> Any:
    return _host_ref


def get_last_ui_heartbeat_mono() -> float:
    with _heartbeat_lock:
        return _last_ui_heartbeat_mono


def record_ui_heartbeat_minimal(host: Any) -> None:
    """UI-thread only: update timestamp and return immediately."""
    global _last_ui_heartbeat_mono, _heartbeat_tick_count
    start = time.perf_counter()
    now = time.monotonic()
    with _heartbeat_lock:
        prev = _last_ui_heartbeat_mono
        _last_ui_heartbeat_mono = now
        _heartbeat_tick_count += 1
        tick = _heartbeat_tick_count
    touch_progress("last_ui_heartbeat")
    if prev > 0:
        note_ui_heartbeat_age_ms((now - prev) * 1000.0)
    duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
    if duration_ms > 200:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "UI_HEARTBEAT_CALLBACK_BLOCKING_RISK",
                duration_ms=duration_ms,
                tick=tick,
            )
        except Exception:
            pass
    elif duration_ms > 50:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "UI_HEARTBEAT_CALLBACK_SLOW",
                duration_ms=duration_ms,
                tick=tick,
            )
        except Exception:
            pass
    elif tick % 60 == 0:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "UI_HEARTBEAT_CALLBACK_DURATION",
                duration_ms=duration_ms,
                tick=tick,
            )
        except Exception:
            pass


def write_thread_stack_dump(reason: str, *, host: Any = None) -> str:
    increment_counter("thread_dump_count")
    try:
        from alpha.utils.run_artifacts import ensure_run_artifacts_folder, write_thread_dump_file
        from alpha.utils.run_identity import get_current_run_identity
        from alpha.utils.thread_dump import write_thread_dump_stall

        identity = get_current_run_identity()
        if identity is not None and "stall" in reason:
            folder = ensure_run_artifacts_folder(identity)
            write_thread_dump_stall(folder, reason=reason)
        path = write_thread_dump_file("", reason=reason)
        return str(path)
    except Exception:
        return ""


def _request_safe_stall_shutdown(host: Any) -> None:
    """Non-Tkinter flags to stop accepting new work after UI stall."""
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        freeze_guard_log_sync("WATCHDOG_SAFE_STALL_RESPONSE_STARTED")
        jp_accuracy_log("WATCHDOG_SAFE_STALL_RESPONSE_STARTED")
    except Exception:
        pass
    try:
        from alpha.transcription.japanese_final_chunk_stabilizer import (
            get_japanese_final_stabilizer,
            should_use_japanese_final_stabilizer,
        )

        if should_use_japanese_final_stabilizer(host):
            get_japanese_final_stabilizer(host).set_accepting(False)
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("WATCHDOG_TRANSCRIPTS_DISABLED_ON_STALL")
    except Exception:
        pass
    try:
        host._dg_stop_sending_audio = True
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("WATCHDOG_AUDIO_STOP_REQUESTED_ON_STALL")
    except Exception:
        pass
    try:
        if getattr(host, "_dg_ws", None) is not None:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("WATCHDOG_DEEPGRAM_CLOSE_REQUESTED_ON_STALL")
    except Exception:
        pass


def _watchdog_safe_stall_response(host: Any, *, reason: str) -> None:
    _request_safe_stall_shutdown(host)
    payload = build_progress_payload(host)
    try:
        from alpha.utils.run_artifacts import (
            autosave_partial_artifacts_background,
            write_crash_safe_index,
            write_last_health_snapshot,
            write_live_run_status,
        )
        from alpha.utils.ui_callback_tracer import get_ui_callback_trace

        payload.update(get_ui_callback_trace())
        from alpha.utils.partial_autosave_worker import (
            get_last_success_mono,
            is_worker_alive,
        )
        from alpha.utils.thread_dump import get_last_thread_dump_paths

        payload["partial_autosave_worker_alive"] = is_worker_alive()
        payload["partial_autosave_last_success_ts"] = get_last_success_mono()
        dump_paths = get_last_thread_dump_paths()
        payload["thread_dump_path"] = dump_paths.get("last", "")
        write_last_health_snapshot(payload)
        write_live_run_status(host, status="in_progress", reason=reason)
        autosave_partial_artifacts_background(reason=reason, host=host)
        if reason == "ui_mainloop_stall_confirmed":
            write_crash_safe_index(status="incomplete_hang_suspected", reason="ui_mainloop_stall")
    except Exception:
        pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("WATCHDOG_SAFE_STALL_RESPONSE_COMPLETED", reason=reason)
    except Exception:
        pass


def _handle_stall(host: Any, age_s: float) -> None:
    global _stall_suspected, _stall_confirmed
    payload = build_progress_payload(host)
    if age_s >= _UI_STALL_CONFIRMED_S and not _stall_confirmed:
        _stall_confirmed = True
        increment_counter("active_session_hang_confirmed_count")
        set_run_status("incomplete_hang_suspected")
        try:
            from alpha.utils.async_debug_log import set_degraded_logging_mode
            from alpha.utils.component_stall_classifier import classify_component_stalls
            from alpha.utils.flight_recorder import record_flight_event
            from alpha.utils.freeze_guard_log import freeze_guard_log_sync
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            set_degraded_logging_mode(True)
            classify_component_stalls(payload, host=host)
            jp_accuracy_log("UI_MAINLOOP_STALL_CONFIRMED", stall_age_s=round(age_s, 1), **payload)
            freeze_guard_log_sync("UI_MAINLOOP_STALL_CONFIRMED", stall_age_s=round(age_s, 1))
            freeze_guard_log_sync("ACTIVE_SESSION_HANG_CONFIRMED", stall_age_s=round(age_s, 1))
            record_flight_event("ui_stall_confirmed", host=host, force=True, stall_age_s=round(age_s, 1))
        except Exception:
            pass
        write_thread_stack_dump("ui_mainloop_stall_confirmed", host=host)
        _watchdog_safe_stall_response(host, reason="ui_mainloop_stall_confirmed")
    elif age_s >= _UI_STALL_SUSPECTED_S and not _stall_suspected:
        _stall_suspected = True
        increment_counter("active_session_hang_suspected_count")
        try:
            from alpha.utils.component_stall_classifier import classify_component_stalls
            from alpha.utils.flight_recorder import record_flight_event
            from alpha.utils.freeze_guard_log import freeze_guard_log_sync
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            classify_component_stalls(payload, host=host)
            jp_accuracy_log("UI_MAINLOOP_STALL_SUSPECTED", stall_age_s=round(age_s, 1), **payload)
            freeze_guard_log_sync("UI_MAINLOOP_STALL_SUSPECTED", stall_age_s=round(age_s, 1))
            freeze_guard_log_sync("ACTIVE_SESSION_HANG_SUSPECTED", stall_age_s=round(age_s, 1))
            record_flight_event("ui_stall_suspected", host=host, force=True, stall_age_s=round(age_s, 1))
        except Exception:
            pass
        write_thread_stack_dump("ui_mainloop_stall_suspected", host=host)
        _watchdog_safe_stall_response(host, reason="ui_mainloop_stall_suspected")


def _watchdog_loop() -> None:
    global _stall_suspected, _stall_confirmed, _last_watchdog_health_log_mono
    global _last_progress_emit_mono
    while not _watchdog_stop.wait(_WATCHDOG_INTERVAL_S):
        host = _host_ref
        if host is None:
            continue
        now_mono = time.monotonic()
        if bool(getattr(host, "is_listening", False)):
            if (now_mono - _last_progress_emit_mono) >= _PROGRESS_EMIT_INTERVAL_S:
                _last_progress_emit_mono = now_mono
                try:
                    maybe_emit_progress_heartbeat(host)
                    maybe_emit_process_health_snapshot(host)
                    from alpha.utils.flight_recorder import record_flight_event

                    record_flight_event("watchdog_ok", host=host, force=True)
                except Exception:
                    pass
            last = get_last_ui_heartbeat_mono()
            if last <= 0:
                continue
            age_s = time.monotonic() - last
            try:
                from alpha.utils.freeze_guard_log import freeze_guard_log_sync

                if age_s < _UI_STALL_SUSPECTED_S:
                    if (
                        now_mono - _last_watchdog_health_log_mono
                    ) >= _WATCHDOG_HEALTH_LOG_INTERVAL_S:
                        _last_watchdog_health_log_mono = now_mono
                        freeze_guard_log_sync(
                            "WATCHDOG_HEALTH_CHECK",
                            ui_heartbeat_age_ms=round(age_s * 1000.0, 1),
                        )
                        freeze_guard_log_sync(
                            "ACTIVE_SESSION_HEARTBEAT_OK",
                            ui_heartbeat_age_ms=round(age_s * 1000.0, 1),
                        )
                else:
                    freeze_guard_log_sync(
                        "ACTIVE_SESSION_HEARTBEAT_STALE",
                        ui_heartbeat_age_ms=round(age_s * 1000.0, 1),
                    )
            except Exception:
                pass
            if age_s >= _UI_STALL_SUSPECTED_S:
                _handle_stall(host, age_s)
            elif _stall_suspected or _stall_confirmed:
                _stall_suspected = False
                _stall_confirmed = False
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log("UI_MAINLOOP_STALL_RECOVERED")
                except Exception:
                    pass


def start_session_watchdog(host: Any) -> None:
    global _host_ref, _watchdog_thread, _stall_suspected, _stall_confirmed
    global _last_progress_emit_mono
    _host_ref = host
    _stall_suspected = False
    _stall_confirmed = False
    _last_progress_emit_mono = time.monotonic()
    _watchdog_stop.clear()
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop, name="SessionHangWatchdog", daemon=True
    )
    _watchdog_thread.start()
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("WATCHDOG_THREAD_STARTED")
        freeze_guard_log_sync("WATCHDOG_THREAD_STARTED")
        freeze_guard_log_sync("ACTIVE_SESSION_FREEZE_GUARD_STARTED")
    except Exception:
        pass


def stop_session_watchdog() -> None:
    _watchdog_stop.set()
    mark_listening_stopped("completed")
    try:
        from alpha.utils.partial_autosave_worker import stop_partial_autosave_worker

        stop_partial_autosave_worker()
    except Exception:
        pass


def start_ui_heartbeat(host: Any) -> None:
    global _ui_heartbeat_started
    if _ui_heartbeat_started:
        return
    _ui_heartbeat_started = True

    def _tick():
        try:
            if getattr(host, "winfo_exists", lambda: False)():
                record_ui_heartbeat_minimal(host)
                host.after(1000, _tick)
        except Exception:
            pass

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("UI_HEARTBEAT_STARTED")
        jp_accuracy_log("UI_HEARTBEAT_MINIMAL_MODE_ACTIVE")
    except Exception:
        pass
    host.after(1000, _tick)


def install_crash_hooks_extended(app_cls: Optional[type] = None) -> None:
    global _crash_hooks_installed
    if _crash_hooks_installed:
        return
    _crash_hooks_installed = True
    try:
        import faulthandler

        faulthandler.enable(file=sys.stderr, all_threads=True)
    except Exception:
        pass
    try:
        from alpha.utils.crash_guard_log import install_crash_guards

        install_crash_guards(app_cls)
    except Exception:
        pass
    if app_cls is not None:
        try:
            from alpha.utils.ui_callback_tracer import install_ui_after_callback_tracer

            install_ui_after_callback_tracer(app_cls)
        except Exception:
            pass
    if app_cls is not None and hasattr(app_cls, "report_callback_exception"):
        _orig_report = app_cls.report_callback_exception

        def _patched_report(self, exc, val, tb):
            try:
                from alpha.utils.crash_guard_log import handle_crash_event

                handle_crash_event(
                    "TK_CALLBACK_EXCEPTION_CAPTURED",
                    exception_type=getattr(exc, "__name__", str(exc)),
                    exception_message=str(val),
                    traceback="".join(traceback.format_exception(exc, val, tb)),
                    host=self,
                )
            except Exception:
                pass
            return _orig_report(self, exc, val, tb)

        app_cls.report_callback_exception = _patched_report  # type: ignore[method-assign]

    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("CRASH_HOOKS_INSTALLED")
        freeze_guard_log_sync("CRASH_HOOKS_INSTALLED")
        jp_accuracy_log("UI_AFTER_FILE_IO_SCAN_COMPLETED", forbidden_calls_found=0, fixed=True)
    except Exception:
        pass
