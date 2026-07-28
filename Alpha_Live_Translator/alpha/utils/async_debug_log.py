"""Async batched NDJSON debug log writer — never blocks the UI thread on disk I/O."""

from __future__ import annotations

import atexit
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import APP_CODENAME, APP_VERSION
from alpha.utils.logging_utils import sanitize_log_data

DEBUG_SESSION_ID = "46ae0c"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_log_queue: queue.Queue[Optional[dict[str, Any]]] = queue.Queue(maxsize=8000)
_writer_started = False
_writer_thread: Optional[threading.Thread] = None
_log_path: Optional[Path] = None
_last_flush_mono = 0.0
_dropped_verbose = 0
_suppressed_repeat: dict[str, int] = {}
_started_logged = False
_lock = threading.Lock()
_events_written = 0
_events_suppressed = 0
_shutdown_sent = False
_degraded_mode = False
_emergency_sync_count = 0
_last_successful_write_mono = 0.0
_last_health_snapshot_mono = 0.0
_dropped_degraded_count = 0

_QUEUE_WARN = 1000
_QUEUE_DEGRADED = 3000
_QUEUE_CRITICAL = 5000
_HEALTH_SNAPSHOT_INTERVAL_S = 30.0

_CRITICAL_EVENTS = frozenset(
    {
        "RUN_PROGRESS_HEARTBEAT",
        "PARTIAL_ALPHA_OUTPUT_AUTOSAVED",
        "PARTIAL_RUN_INDEX_AUTOSAVED",
        "CRASH_HOOK_TRIGGERED",
        "UI_MAINLOOP_STALL_SUSPECTED",
        "UI_MAINLOOP_STALL_CONFIRMED",
        "STOP_LISTENING_BEGIN",
        "STOP_LISTENING_DONE",
        "FINAL_LIVE_SESSION_SUMMARY",
        "STABLE_JAPANESE_COMMIT",
        "STABLE_JAPANESE_COMMIT_SUMMARY",
        "ASYNC_LOG_WRITER_STALLED",
        "DEGRADED_LOGGING_MODE_ENABLED",
        "ASYNC_LOG_EMERGENCY_SYNC_WRITE",
    }
)

_FLUSH_INTERVAL_S = 0.35
_VERBOSE_DROP_EVENTS = frozenset(
    {
        "after_loop_scheduled",
        "after_schedule",
    }
)
_RUNTIME_EVIDENCE_EVENTS = frozenset(
    {
        "RUN_STARTED",
        "UI_PERFORMANCE_MODE_ENABLED",
        "JAPANESE_ACCURACY_MODE_ENABLED",
        "DEBUG_VERBOSE_UI_LOOP_DISABLED",
        "DEEPGRAM_CONNECT_BEGIN",
        "DEEPGRAM_CONNECT_END",
        "START_LISTENING",
        "STOP_LISTENING_BEGIN",
        "STOP_LISTENING_DONE",
        "STOP_UI_CALLBACK_RETURNED",
        "RUN_ID_CREATED",
        "ARTIFACT_ROOT_SELECTED",
        "RUN_ARTIFACTS_INDEX_UPDATED",
        "RUN_CONSISTENCY_CHECK_BEGIN",
        "RUN_CONSISTENCY_CHECK_PASSED",
        "RUN_CONSISTENCY_CHECK_FAILED",
        "DEEPGRAM_GRACEFUL_STOP_BEGIN",
        "DEEPGRAM_AUDIO_SEND_STOPPED",
        "DEEPGRAM_CLOSE_REQUESTED",
        "DEEPGRAM_CLOSE_NORMAL",
        "DEEPGRAM_CLOSE_LATE_NORMAL",
        "DEEPGRAM_CLOSE_TIMEOUT",
        "DEEPGRAM_GRACEFUL_STOP_DONE",
        "DEEPGRAM_CLOSE_ERROR",
        "FINAL_LIVE_SESSION_SUMMARY",
        "FINAL_UI_PERFORMANCE_SUMMARY",
        "LONG_SESSION_ACCURACY_SUMMARY",
        "TRANSLATION_UNIT_FLUSHED_SUMMARY",
        "ASYNC_LOG_WRITER_FLUSHED_ON_STOP",
        "RUN_ARTIFACTS_INDEX_CREATED",
        "LONG_TEST_READY_FOR_NEXT_STAGE",
        "ASYNC_LOG_FLUSH_REQUESTED",
        "ASYNC_LOG_FLUSH_COMPLETED",
        "ASYNC_LOG_FLUSH_SUCCESS",
        "ASYNC_LOG_FLUSH_DELAY",
        "ASYNC_LOG_QUEUE_DEPTH",
        "ASYNC_LOG_FINAL_FLUSH_COMPLETED",
        "ASYNC_LOG_FLUSH_TIMEOUT",
        "ASYNC_LOG_FLUSH_FAILED",
        "ASYNC_LOG_WRITER_ALREADY_CLOSED",
        "ASYNC_LOG_WRITER_STARTED",
        "ASYNC_LOG_EMERGENCY_WRITE",
    }
)


def get_async_debug_log_path() -> Path:
    from alpha.utils.troubleshooting_paths import get_log_path

    return get_log_path("async_debug")


def rebind_runtime_log_writer() -> None:
    global _log_path
    with _lock:
        _log_path = None


def get_debug_event_stats() -> dict[str, int]:
    with _lock:
        return {
            "diagnostic_events_written": int(_events_written),
            "diagnostic_events_suppressed": int(_events_suppressed),
        }


def _writer_loop() -> None:
    global _last_flush_mono, _dropped_verbose
    buffer: list[str] = []
    last_path: Optional[Path] = None
    while True:
        try:
            item = _log_queue.get(timeout=_FLUSH_INTERVAL_S)
        except queue.Empty:
            item = None
        if item is None:
            if buffer:
                _flush_buffer(get_async_debug_log_path(), buffer)
                buffer = []
            with _lock:
                if _log_queue.empty() and _shutdown_sent:
                    break
            continue
        if item.get("_shutdown"):
            if buffer:
                _flush_buffer(get_async_debug_log_path(), buffer)
            return
        if item.get("_flush_only"):
            if buffer:
                _flush_buffer(get_async_debug_log_path(), buffer)
                buffer = []
            continue
        line = item.get("line")
        if line:
            buffer.append(line)
        path = get_async_debug_log_path()
        if len(buffer) >= 48 or (time.monotonic() - _last_flush_mono) >= _FLUSH_INTERVAL_S:
            _flush_buffer(path, buffer)
            buffer = []


def _flush_buffer(path: Path, lines: list[str]) -> None:
    global _last_flush_mono, _last_successful_write_mono
    if not lines:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        _last_flush_mono = time.monotonic()
        _last_successful_write_mono = _last_flush_mono
        try:
            from alpha.utils.session_progress import touch_progress

            touch_progress("last_async_log_flush")
        except Exception:
            pass
    except Exception:
        pass


def get_async_log_health() -> dict[str, Any]:
    with _lock:
        qsize = _log_queue.qsize()
        writer_alive = bool(_writer_thread is not None and _writer_thread.is_alive())
    return {
        "queue_size": qsize,
        "writer_thread_alive": writer_alive,
        "last_flush_age_ms": round(
            max(0.0, (time.monotonic() - _last_flush_mono) * 1000.0), 1
        )
        if _last_flush_mono > 0
        else -1,
        "last_successful_write_age_ms": round(
            max(0.0, (time.monotonic() - _last_successful_write_mono) * 1000.0), 1
        )
        if _last_successful_write_mono > 0
        else -1,
        "dropped_verbose_count": int(_dropped_verbose),
        "dropped_degraded_count": int(_dropped_degraded_count),
        "emergency_sync_count": int(_emergency_sync_count),
        "degraded_mode": bool(_degraded_mode),
    }


def ensure_async_logger_healthy_non_blocking() -> dict[str, Any]:
    """Best-effort health check/restart without blocking startup."""
    emergency_sync_write("ASYNC_LOGGER_HEALTH_CHECK")
    health = get_async_log_health()
    try:
        writer_alive = bool(health.get("writer_thread_alive"))
        if not writer_alive:
            emergency_sync_write("ASYNC_LOGGER_RESTART_ATTEMPTED")
            with _lock:
                global _writer_started, _writer_thread, _shutdown_sent
                _writer_started = False
                _writer_thread = None
                _shutdown_sent = False
            _ensure_writer()
            health = get_async_log_health()
            if bool(health.get("writer_thread_alive")):
                emergency_sync_write("ASYNC_LOGGER_RESTARTED")
            else:
                emergency_sync_write("ASYNC_LOGGER_SAFE_MODE_FALLBACK")
        return health
    except Exception:
        emergency_sync_write("ASYNC_LOGGER_SAFE_MODE_FALLBACK")
        return get_async_log_health()


def set_degraded_logging_mode(enabled: bool) -> None:
    global _degraded_mode
    if _degraded_mode == enabled:
        return
    _degraded_mode = enabled
    if enabled:
        emergency_sync_write("DEGRADED_LOGGING_MODE_ENABLED")


def _check_queue_health() -> None:
    global _last_health_snapshot_mono
    try:
        with _lock:
            qsize = _log_queue.qsize()
    except Exception:
        return
    try:
        from alpha.utils.session_progress import note_async_log_queue_size

        note_async_log_queue_size(qsize)
    except Exception:
        pass
    now = time.monotonic()
    if (now - _last_health_snapshot_mono) >= _HEALTH_SNAPSHOT_INTERVAL_S:
        _last_health_snapshot_mono = now
        emergency_sync_write("ASYNC_LOG_HEALTH_SNAPSHOT", **get_async_log_health())
    if qsize > _QUEUE_CRITICAL:
        set_degraded_logging_mode(True)
        emergency_sync_write("ASYNC_LOG_QUEUE_HIGH", queue_size=qsize, level="critical")
    elif qsize > _QUEUE_DEGRADED:
        set_degraded_logging_mode(True)
        emergency_sync_write("ASYNC_LOG_QUEUE_HIGH", queue_size=qsize, level="degraded")
    elif qsize > _QUEUE_WARN:
        emergency_sync_write("ASYNC_LOG_QUEUE_HIGH", queue_size=qsize, level="warning")
    if _writer_started and _writer_thread is not None and not _writer_thread.is_alive():
        emergency_sync_write("ASYNC_LOG_WRITER_STALLED")


def emergency_sync_write(message: str, **data: Any) -> None:
    """Write one line synchronously to debug log during crash/degraded mode."""
    global _emergency_sync_count, _last_successful_write_mono, _last_flush_mono
    _emergency_sync_count += 1
    path = get_async_debug_log_path()
    safe_data = sanitize_log_data(data or {})
    safe_data.setdefault("app_version", APP_VERSION)
    safe_data.setdefault("app_codename", APP_CODENAME)
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": f"session-v{APP_VERSION}",
        "hypothesisId": "EMERGENCY",
        "location": "async_debug_log.py:emergency_sync_write",
        "message": message,
        "data": safe_data,
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, ensure_ascii=True)
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        now = time.monotonic()
        _last_successful_write_mono = now
        # Count emergency sync as a successful flush age update to avoid false 120s gaps.
        _last_flush_mono = now
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "ASYNC_LOG_EMERGENCY_WRITE",
                message=message,
                emergency_count=_emergency_sync_count,
                queue_depth=_log_queue.qsize(),
            )
        except Exception:
            pass
    except Exception:
        pass
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync

        freeze_guard_log_sync("ASYNC_LOG_EMERGENCY_SYNC_WRITE", message=message, **safe_data)
    except Exception:
        pass


def emergency_sync_flush() -> None:
    try:
        _log_queue.put_nowait({"_flush_only": True})
    except Exception:
        pass


def _should_drop_in_degraded(message: str, force: bool) -> bool:
    if force or message in _RUNTIME_EVIDENCE_EVENTS or message in _CRITICAL_EVENTS:
        return False
    if message.startswith("STABLE_JAPANESE_COMMIT"):
        return False
    if message in ("RUN_PROGRESS_HEARTBEAT", "PARTIAL_ALPHA_OUTPUT_AUTOSAVED"):
        return False
    return True


def _ensure_writer() -> None:
    global _writer_started, _writer_thread, _started_logged
    if _writer_started:
        return
    _writer_started = True
    _writer_thread = threading.Thread(
        target=_writer_loop, name="AsyncDebugLogWriter", daemon=True
    )
    _writer_thread.start()
    atexit.register(shutdown_async_debug_logging)
    if not _started_logged:
        _started_logged = True
        enqueue_ndjson_log(
            run_id=f"session-v{APP_VERSION}",
            hypothesis_id="SESSION",
            location="async_debug_log.py:_ensure_writer",
            message="ASYNC_LOG_WRITER_STARTED",
            data={"debug_log_path": str(get_async_debug_log_path())},
            force=True,
        )


def enqueue_ndjson_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[dict[str, Any]] = None,
    force: bool = False,
) -> None:
    """Queue one NDJSON line for background flush."""
    global _events_written, _events_suppressed, _dropped_degraded_count
    from alpha.constants import DEBUG_AFTER_LOOP_VERBOSE, DEBUG_UI_LOOP_VERBOSE

    _check_queue_health()

    if _degraded_mode and _should_drop_in_degraded(message, force):
        with _lock:
            _dropped_degraded_count += 1
        try:
            from alpha.utils.freeze_guard_log import freeze_guard_log_sync

            if _dropped_degraded_count % 500 == 1:
                freeze_guard_log_sync(
                    "DEBUG_EVENT_DROPPED_DEGRADED_MODE",
                    message=message,
                    dropped_degraded_count=_dropped_degraded_count,
                )
        except Exception:
            pass
        return

    is_runtime = message in _RUNTIME_EVIDENCE_EVENTS
    if not force and not is_runtime:
        if message == "after_loop_scheduled" and not DEBUG_AFTER_LOOP_VERBOSE:
            with _lock:
                _events_suppressed += 1
                _suppressed_repeat[message] = int(_suppressed_repeat.get(message, 0)) + 1
            return
        if message in _VERBOSE_DROP_EVENTS and not DEBUG_UI_LOOP_VERBOSE:
            with _lock:
                _events_suppressed += 1
                _dropped_verbose += 1
            return

    safe_data = sanitize_log_data(data or {})
    safe_data.setdefault("app_version", APP_VERSION)
    safe_data.setdefault("app_codename", APP_CODENAME)
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": safe_data,
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, ensure_ascii=True)
    _ensure_writer()
    try:
        _log_queue.put_nowait({"line": line})
        with _lock:
            _events_written += 1
    except queue.Full:
        with _lock:
            _events_suppressed += 1
            _dropped_verbose += 1
        try:
            _log_queue.get_nowait()
            _log_queue.put_nowait({"line": line})
            with _lock:
                _events_written += 1
        except Exception:
            pass


def log_async_event(message: str, **data: Any) -> None:
    enqueue_ndjson_log(
        run_id=f"session-v{APP_VERSION}",
        hypothesis_id="PERF",
        location="async_debug_log.py:log_async_event",
        message=message,
        data=data,
        force=True,
    )


def log_runtime_debug_event(message: str, **data: Any) -> None:
    enqueue_ndjson_log(
        run_id=f"session-v{APP_VERSION}",
        hypothesis_id="RUNTIME",
        location="async_debug_log.py:log_runtime_debug_event",
        message=message,
        data=data,
        force=message in _RUNTIME_EVIDENCE_EVENTS,
    )


def flush_async_debug_logging(timeout_ms: float = 500.0) -> bool:
    """Drain queue and flush buffer; returns False on timeout."""
    return flush_async_debug_logging_safe(timeout_ms=timeout_ms)


def flush_async_debug_logging_safe(timeout_ms: float = 500.0) -> bool:
    """Non-blocking-safe flush; never raises."""
    global _shutdown_sent
    if not _writer_started:
        log_runtime_debug_event("ASYNC_LOG_WRITER_ALREADY_CLOSED")
        return True
    try:
        log_runtime_debug_event("ASYNC_LOG_FLUSH_REQUESTED", timeout_ms=timeout_ms)
    except Exception:
        pass
    deadline = time.monotonic() + max(0.05, timeout_ms / 1000.0)
    try:
        _log_queue.put_nowait({"_flush_only": True})
    except queue.Full:
        pass
    except Exception:
        log_runtime_debug_event("ASYNC_LOG_FLUSH_FAILED", reason="enqueue_flush_only")
        return False
    while time.monotonic() < deadline:
        try:
            with _lock:
                pending = _log_queue.qsize()
            if pending <= 1:
                try:
                    health = get_async_log_health()
                    log_runtime_debug_event(
                        "ASYNC_LOG_FLUSH_COMPLETED", pending_queue=pending
                    )
                    log_runtime_debug_event(
                        "ASYNC_LOG_FLUSH_SUCCESS", **health
                    )
                    log_runtime_debug_event(
                        "ASYNC_LOG_QUEUE_DEPTH", queue_size=health.get("queue_size")
                    )
                    log_runtime_debug_event(
                        "ASYNC_LOG_FINAL_FLUSH_COMPLETED",
                        pending_queue=pending,
                        **health,
                    )
                    log_runtime_debug_event(
                        "ASYNC_LOG_WRITER_FLUSHED_ON_STOP",
                        debug_log_path=str(get_async_debug_log_path()),
                    )
                except Exception:
                    pass
                return True
        except Exception:
            pass
        time.sleep(0.02)
    try:
        log_runtime_debug_event("ASYNC_LOG_FLUSH_TIMEOUT", timeout_ms=timeout_ms)
    except Exception:
        pass
    return False


def shutdown_async_debug_logging() -> None:
    global _writer_started, _shutdown_sent
    if not _writer_started:
        return
    flush_async_debug_logging(timeout_ms=500.0)
    dropped = 0
    with _lock:
        dropped = int(_dropped_verbose)
    if dropped > 0:
        log_async_event(
            "ASYNC_LOG_DROPPED_VERBOSE_EVENT",
            dropped_verbose_count=dropped,
            suppressed_after_loop=_suppressed_repeat.get("after_loop_scheduled", 0),
        )
    _shutdown_sent = True
    log_async_event("ASYNC_LOG_WRITER_STOPPED")
    try:
        _log_queue.put_nowait({"_shutdown": True})
    except Exception:
        pass
    if _writer_thread is not None:
        _writer_thread.join(timeout=2.0)
    _writer_started = False


def note_batch_flush(line_count: int, *, duration_ms: float) -> None:
    log_async_event(
        "ASYNC_LOG_BATCH_FLUSH",
        lines_flushed=line_count,
        duration_ms=round(duration_ms, 2),
    )
