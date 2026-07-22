"""Append-only crash-resilient flight recorder for long-session forensics."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()
_seq = 0
_file_path: Optional[Path] = None
_file_handle: Any = None
_session_start_mono: float = 0.0
_started = False

_CRITICAL_EVENTS = frozenset(
    {
        "app_start",
        "run_created",
        "listen_start_requested",
        "listen_started",
        "deepgram_connecting",
        "deepgram_connected",
        "audio_capture_started",
        "first_audio_frame_received",
        "first_deepgram_message",
        "first_deepgram_final",
        "stable_commit",
        "ui_commit",
        "autosave_success",
        "progress_heartbeat",
        "long_session_health_heartbeat",
        "watchdog_ok",
        "ui_stall_suspected",
        "ui_stall_confirmed",
        "audio_stall_suspected",
        "deepgram_stall_suspected",
        "stable_pipeline_stall_suspected",
        "ui_commit_stall_suspected",
        "async_logger_stall_suspected",
        "partial_autosave_stall_suspected",
        "component_stall_classification",
        "process_memory_warning",
        "stop_clicked",
        "stop_finalize_started",
        "stop_finalize_completed",
        "manual_window_close_requested",
        "window_close_requested",
        "crash_hook_triggered",
        "run_completed",
        "process_exit_marker",
    }
)


def _wall_time() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def _next_seq() -> int:
    global _seq
    with _lock:
        _seq += 1
        return _seq


def _process_memory_mb() -> float:
    try:
        from alpha.utils.process_health_telemetry import collect_process_metrics

        metrics = collect_process_metrics()
        rss = float(metrics.get("process_memory_rss_mb", -1))
        return rss if rss >= 0 else -1.0
    except Exception:
        return -1.0


def _build_context(host: Any = None) -> dict[str, Any]:
    from alpha.constants import LONG_SESSION_STABILITY_MODE
    from alpha.utils.session_progress import build_progress_payload, elapsed_listening_seconds

    ctx: dict[str, Any] = {}
    try:
        ctx.update(build_progress_payload(host))
    except Exception:
        pass
    ctx["monotonic_elapsed_seconds"] = round(elapsed_listening_seconds(), 1)
    ctx["long_session_stability_mode"] = LONG_SESSION_STABILITY_MODE
    ctx["process_memory_mb"] = _process_memory_mb()
    ctx["python_thread_count"] = threading.active_count()
    try:
        from alpha.utils.partial_autosave_worker import is_worker_alive

        ctx["partial_autosave_worker_alive"] = is_worker_alive()
    except Exception:
        pass
    try:
        from alpha.utils.session_watchdog import get_watchdog_thread_alive

        ctx["watchdog_thread_alive"] = get_watchdog_thread_alive()
    except Exception:
        pass
    if host is not None:
        ctx["listening"] = bool(getattr(host, "is_listening", False))
        ctx["is_stopping"] = bool(getattr(host, "_is_stopping", False))
        ctx["active_state"] = (
            "listening"
            if ctx["listening"]
            else ("stopping" if ctx.get("is_stopping") else "idle")
        )
    return ctx


def start_flight_recorder(artifact_folder: Path) -> Path:
    """Open FLIGHT_RECORDER.log in append+line-buffered mode."""
    global _file_path, _file_handle, _session_start_mono, _started
    artifact_folder.mkdir(parents=True, exist_ok=True)
    path = artifact_folder / "FLIGHT_RECORDER.log"
    with _lock:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except Exception:
                pass
        _file_path = path
        _file_handle = open(path, "a", encoding="utf-8", buffering=1)
        _session_start_mono = time.monotonic()
        _started = True
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("FLIGHT_RECORDER_STARTED", path=str(path))
    except Exception:
        pass
    record_flight_event("flight_recorder_started", path=str(path))
    return path


def get_flight_recorder_path() -> Optional[Path]:
    return _file_path


def record_flight_event(
    event_name: str,
    *,
    host: Any = None,
    force: bool = False,
    **extra: Any,
) -> None:
    """Write one JSONL line synchronously with flush."""
    if not force and event_name not in _CRITICAL_EVENTS:
        return
    global _file_handle
    if _file_handle is None:
        return
    try:
        from alpha.utils.run_identity import get_current_run_identity

        identity = get_current_run_identity()
        run_id = identity.run_id if identity else ""
    except Exception:
        run_id = ""
    ctx = _build_context(host)
    record: dict[str, Any] = {
        "seq": _next_seq(),
        "wall_time": _wall_time(),
        "monotonic_elapsed_seconds": ctx.get("monotonic_elapsed_seconds", 0.0),
        "run_id": run_id,
        "event_name": event_name,
        "active_state": ctx.get("active_state", "unknown"),
        "listening": ctx.get("listening", False),
        "is_stopping": ctx.get("is_stopping", False),
        "stable_commit_count": ctx.get("internal_stable_commit_count", 0),
        "ui_segment_count": ctx.get("exported_ui_segment_count", 0),
        "audio_queue_size": ctx.get("audio_queue_size", -1),
        "transcript_ui_queue_size": ctx.get("transcript_ui_queue_size", -1),
        "async_log_queue_size": ctx.get("async_log_queue_size", -1),
        "last_ui_heartbeat_age_ms": ctx.get("ui_heartbeat_age_ms", -1),
        "last_audio_received_age_ms": ctx.get("audio_received_age_ms", -1),
        "last_deepgram_message_age_ms": ctx.get("deepgram_message_age_ms", -1),
        "last_deepgram_final_age_ms": ctx.get("deepgram_final_age_ms", -1),
        "last_stable_commit_age_ms": ctx.get("stable_commit_age_ms", -1),
        "memory_mb": ctx.get("process_memory_mb", -1),
    }
    record.update(extra)
    line = json.dumps(record, ensure_ascii=False, default=str)
    try:
        with _lock:
            if _file_handle is not None:
                _file_handle.write(line + "\n")
                _file_handle.flush()
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("FLIGHT_RECORDER_EVENT_WRITTEN", event_name=event_name, seq=record["seq"])
        except Exception:
            pass
    except Exception as exc:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("FLIGHT_RECORDER_ERROR", error=str(exc), event_name=event_name)
        except Exception:
            pass


def flush_flight_recorder() -> None:
    with _lock:
        if _file_handle is not None:
            try:
                _file_handle.flush()
            except Exception:
                pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("FLIGHT_RECORDER_FLUSHED")
    except Exception:
        pass


def close_flight_recorder(*, reason: str = "shutdown") -> None:
    record_flight_event("process_exit_marker", force=True, reason=reason)
    flush_flight_recorder()
    global _file_handle
    with _lock:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except Exception:
                pass
            _file_handle = None


def read_last_flight_event(folder: Path) -> Optional[dict[str, Any]]:
    path = folder / "FLIGHT_RECORDER.log"
    if not path.exists():
        return None
    last: Optional[dict[str, Any]] = None
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    except Exception:
        return None
    return last
