"""Global crash guard logging — exceptions, thread dumps, crash-safe artifacts."""

from __future__ import annotations

import atexit
import faulthandler
import io
import json
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from alpha.constants import APP_CODENAME, APP_VERSION

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOG_DIR = _PROJECT_ROOT / "logs"
_LOG_FILE = _LOG_DIR / f"v{APP_VERSION}_crash_guard.log"

_log_queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=2000)
_writer_started = False
_writer_thread: Optional[threading.Thread] = None
_installed = False
_orig_excepthook = sys.excepthook
_orig_threading_excepthook = getattr(threading, "excepthook", None)


def _completed_run_already_finalized() -> tuple[bool, str]:
    try:
        from alpha.utils.troubleshooting_paths import get_current_run_folder

        run_folder = get_current_run_folder()
        if run_folder is None:
            return False, ""
        live = run_folder / "artifacts" / "LIVE_RUN_STATUS.json"
        if not live.exists():
            return False, ""
        payload = json.loads(live.read_text(encoding="utf-8"))
        status = str(payload.get("status", ""))
        completed = status in ("completed", "completed_with_warnings") and bool(
            payload.get("stop_finalize_completed", False)
        )
        return completed, status
    except Exception:
        return False, ""


def _timestamp() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def _safe_qsize(q: Any) -> int:
    if q is None:
        return -1
    try:
        return int(q.qsize())
    except Exception:
        return -1


def _host_context(host: Any = None) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    if host is None:
        return ctx
    try:
        ctx["audio_queue_size"] = _safe_qsize(getattr(host, "_audio_q", None))
        ctx["ui_queue_size"] = _safe_qsize(getattr(host, "transcript_queue", None))
        ctx["listening"] = bool(getattr(host, "listening", False))
    except Exception:
        pass
    try:
        from alpha.transcription.japanese_sentence_assembler import (
            get_japanese_continuity_assembler,
        )

        assembler = get_japanese_continuity_assembler(host)
        snap = assembler.get_buffer_snapshot()
        if snap:
            ctx.update(snap)
    except Exception:
        pass
    return ctx


def _format_line(event: str, data: dict[str, Any]) -> str:
    payload = {
        "event": event,
        "app_version": APP_VERSION,
        "app_codename": APP_CODENAME,
        "thread_name": threading.current_thread().name,
        **data,
    }
    return f"{_timestamp()} | {json.dumps(payload, ensure_ascii=False, default=str)}"


def _writer_loop() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_LOG_FILE, "a", encoding="utf-8") as handle:
        while True:
            line = _log_queue.get()
            if line is None:
                break
            handle.write(line + "\n")
            handle.flush()


def _start_writer() -> None:
    global _writer_started, _writer_thread
    if _writer_started:
        return
    _writer_started = True
    _writer_thread = threading.Thread(
        target=_writer_loop, name="CrashGuardLogWriter", daemon=True
    )
    _writer_thread.start()
    atexit.register(shutdown_crash_guard_logging)


def shutdown_crash_guard_logging() -> None:
    if not _writer_started:
        return
    try:
        _log_queue.put_nowait(None)
    except Exception:
        pass
    if _writer_thread is not None:
        _writer_thread.join(timeout=2.0)


def crash_guard_log(event: str, **data: Any) -> None:
    _start_writer()
    try:
        _log_queue.put_nowait(_format_line(event, data))
    except queue.Full:
        pass


def _write_thread_stacks_sync() -> str:
    buf = io.StringIO()
    try:
        faulthandler.dump_traceback(file=buf, all_threads=True)
    except Exception:
        traceback.print_exc(file=buf)
    return buf.getvalue()


def _emergency_sync_log(event: str, **data: Any) -> None:
    """Synchronous emergency writer — must not rely on async queue during crash."""
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync

        freeze_guard_log_sync(event, **data)
    except Exception:
        pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **data)
    except Exception:
        pass
    crash_guard_log(event, **data)


def handle_crash_event(
    event: str,
    *,
    exception_type: str = "",
    exception_message: str = "",
    traceback_text: str = "",
    host: Any = None,
    thread_name: str = "",
) -> None:
    """Central crash/hang handler — thread dumps, partial artifacts, crash-safe index."""
    if event in ("CLOSED_BEFORE_START_CLASSIFIED", "START_FAILED_USER_CLOSED_CLASSIFIED"):
        summary = {
            "post_run_exit_detected": True,
            "exit_type": event,
            "exception_type": exception_type,
            "exception_message": exception_message,
            "stop_finalize_completed": False,
            "final_status_preserved": True,
            "status_overwrite_prevented": True,
        }
        _emergency_sync_log(event, **summary)
        _emergency_sync_log("POST_RUN_STATUS_PRESERVED")
        return
    if event.startswith("POST_RUN_"):
        summary = {
            "post_run_exit_detected": True,
            "exit_type": event,
            "exception_type": exception_type,
            "exception_message": exception_message,
            "stop_finalize_completed": True,
            "final_status_preserved": True,
            "status_overwrite_prevented": True,
        }
        try:
            from alpha.utils.troubleshooting_paths import get_artifact_path

            path = get_artifact_path("live_run_status").parent / "POST_RUN_EXIT_SUMMARY.json"
            path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        _emergency_sync_log(event, **summary)
        _emergency_sync_log("POST_RUN_STATUS_PRESERVED")
        return
    is_completed, final_status = _completed_run_already_finalized()
    if is_completed:
        exit_type = "POST_RUN_EXCEPTION_AFTER_COMPLETION"
        if exception_type == "KeyboardInterrupt":
            exit_type = "POST_RUN_MANUAL_EXIT"
        summary = {
            "post_run_exit_detected": True,
            "exit_type": exit_type,
            "exception_type": exception_type,
            "exception_message": exception_message,
            "stop_finalize_completed": True,
            "final_status_preserved": True,
            "status_overwrite_prevented": True,
        }
        try:
            from alpha.utils.troubleshooting_paths import get_artifact_path

            path = get_artifact_path("live_run_status").parent / "POST_RUN_EXIT_SUMMARY.json"
            path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            note = path.parent / "post_run_exit_note.json"
            note.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        _emergency_sync_log(exit_type, **summary)
        _emergency_sync_log("POST_RUN_STATUS_PRESERVED", final_status=final_status)
        _emergency_sync_log("CRASH_HOOK_SKIPPED_FOR_COMPLETED_RUN", final_status=final_status)
        _emergency_sync_log("COMPLETED_RUN_STATUS_OVERWRITE_PREVENTED", final_status=final_status)
        return
    try:
        from alpha.utils.session_progress import increment_counter

        increment_counter("crash_hook_triggered_count")
    except Exception:
        pass

    stack_text = _write_thread_stacks_sync()
    _emergency_sync_log(
        "CRASH_HOOK_TRIGGERED",
        source_event=event,
        exception_type=exception_type or None,
        exception_message=exception_message or None,
    )
    _emergency_sync_log(
        event,
        exception_type=exception_type or None,
        exception_message=exception_message or None,
        thread_name=thread_name or threading.current_thread().name,
        traceback_preview=(traceback_text or stack_text)[:4000],
    )
    if traceback_text:
        _emergency_sync_log(
            "UNHANDLED_EXCEPTION_CAPTURED",
            exception_type=exception_type,
            exception_message=exception_message,
        )
    _emergency_sync_log(
        "THREAD_STACK_DUMP_WRITTEN",
        dump_preview=stack_text[:2000],
    )
    try:
        from alpha.utils.run_artifacts import (
            autosave_partial_artifacts_background,
            write_crash_safe_index,
            write_thread_dump_file,
        )

        write_thread_dump_file("", reason=event)
        autosave_partial_artifacts_background(reason="crash", host=host)
        try:
            from alpha.utils.flight_recorder import flush_flight_recorder, record_flight_event

            record_flight_event(
                "crash_hook_triggered",
                host=host,
                force=True,
                source_event=event,
            )
            flush_flight_recorder()
        except Exception:
            pass
        write_crash_safe_index(status="crashed", reason=event)
        _emergency_sync_log("CRASH_SAFE_ARTIFACT_INDEX_WRITTEN", reason=event)
        _emergency_sync_log("PARTIAL_ALPHA_OUTPUT_WRITTEN_ON_CRASH", reason=event)
    except Exception:
        pass
    try:
        from alpha.utils.async_debug_log import emergency_sync_flush

        emergency_sync_flush()
    except Exception:
        pass


def log_exception(
    exc: BaseException,
    *,
    source: str,
    callback_name: str = "",
    host: Any = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    data = {
        "source": source,
        "callback_name": callback_name or None,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "traceback": tb,
    }
    if extra:
        data.update(extra)
    data.update(_host_context(host))
    crash_guard_log("uncaught_exception", **data)
    try:
        from alpha.utils.async_debug_log import flush_async_debug_logging

        flush_async_debug_logging(timeout_ms=500.0)
    except Exception:
        pass


def _sys_excepthook(exc_type, exc, tb):
    if exc is not None:
        tb_text = "".join(traceback.format_exception(exc_type, exc, tb))
        handle_crash_event(
            "UNHANDLED_EXCEPTION_CAPTURED",
            exception_type=getattr(exc_type, "__name__", str(exc_type)),
            exception_message=str(exc),
            traceback_text=tb_text,
        )
    if _orig_excepthook:
        _orig_excepthook(exc_type, exc, tb)


def _threading_excepthook(args):
    tb_text = "".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
    )
    host = None
    try:
        from alpha.utils.session_watchdog import get_watchdog_host

        host = get_watchdog_host()
    except Exception:
        pass
    handle_crash_event(
        "THREAD_EXCEPTION_CAPTURED",
        exception_type=getattr(args.exc_type, "__name__", str(args.exc_type)),
        exception_message=str(args.exc_value),
        traceback_text=tb_text,
        host=host,
        thread_name=str(getattr(args.thread, "name", "")),
    )
    if _orig_threading_excepthook:
        _orig_threading_excepthook(args)


def safe_after_callback(
    func: Callable[..., Any],
    *args: Any,
    host: Any = None,
    callback_name: str = "",
    **kwargs: Any,
) -> Any:
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        log_exception(
            exc,
            source="tk_after_callback",
            callback_name=callback_name or getattr(func, "__name__", repr(func)),
            host=host,
        )
        return None


def install_crash_guards(app_cls: Optional[type] = None) -> None:
    global _installed
    if _installed:
        return
    _installed = True

    sys.excepthook = _sys_excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _threading_excepthook  # type: ignore[attr-defined]

    if app_cls is None:
        return

    _orig_after = app_cls.after

    def patched_after(self, ms, func=None, *args, **kwargs):
        if func is None:
            return _orig_after(self, ms)
        cb_name = getattr(func, "__name__", repr(func))

        @wraps(func)
        def _safe_wrapper():
            return safe_after_callback(func, *args, host=self, callback_name=cb_name)

        return _orig_after(self, ms, _safe_wrapper)

    app_cls.after = patched_after  # type: ignore[method-assign]

    if hasattr(app_cls, "_run_ui_queue_tick"):
        _orig_ui_tick = app_cls._run_ui_queue_tick

        @wraps(_orig_ui_tick)
        def patched_ui_tick(self, *a, **kw):
            try:
                return _orig_ui_tick(self, *a, **kw)
            except Exception as exc:
                log_exception(exc, source="ui_queue_tick", host=self)
                try:
                    self._schedule_ui_queue_tick()
                except Exception:
                    pass

        app_cls._run_ui_queue_tick = patched_ui_tick  # type: ignore[method-assign]

    if hasattr(app_cls, "_process_ui_queue_once"):
        _orig_process = app_cls._process_ui_queue_once

        @wraps(_orig_process)
        def patched_process(self, *a, **kw):
            try:
                return _orig_process(self, *a, **kw)
            except Exception as exc:
                log_exception(exc, source="ui_queue_drain", host=self)
                return None

        app_cls._process_ui_queue_once = patched_process  # type: ignore[method-assign]


def get_crash_guard_log_path() -> Path:
    return _LOG_FILE
