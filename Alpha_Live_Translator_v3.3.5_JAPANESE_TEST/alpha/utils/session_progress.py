"""Session progress timestamps, heartbeats, and process health snapshots."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LONG_SESSION_STABILITY_MODE,
    LONG_SESSION_HEALTH_HEARTBEAT_INTERVAL_S,
    HEALTH_TIMELINE_INTERVAL_S,
)

_lock = threading.Lock()
_timestamps: dict[str, float] = {}
_counters: dict[str, int] = {
    "partial_autosave_count": 0,
    "thread_dump_count": 0,
    "active_session_hang_suspected_count": 0,
    "active_session_hang_confirmed_count": 0,
    "crash_hook_triggered_count": 0,
    "previous_incomplete_run_detected_count": 0,
}
_max_ui_heartbeat_age_ms: float = 0.0
_max_async_log_queue_size: int = 0
_current_run_status: str = "idle"
_listening_started_mono: float = 0.0
_last_progress_heartbeat_mono: float = 0.0
_last_ui_snapshot_mono: float = 0.0
_last_long_session_health_mono: float = 0.0
_last_health_timeline_mono: float = 0.0

_PROGRESS_INTERVAL_S = 30.0
_UI_SNAPSHOT_INTERVAL_S = 30.0
_PROCESS_SNAPSHOT_INTERVAL_S = 60.0


def _mono() -> float:
    return time.monotonic()


def _age_ms(key: str) -> float:
    with _lock:
        ts = _timestamps.get(key, 0.0)
    if ts <= 0:
        return -1.0
    return round((_mono() - ts) * 1000.0, 1)


def touch_progress(key: str) -> None:
    with _lock:
        _timestamps[key] = _mono()


def set_run_status(status: str) -> None:
    global _current_run_status
    with _lock:
        _current_run_status = status


def get_run_status() -> str:
    with _lock:
        return _current_run_status


def mark_listening_started() -> None:
    global _listening_started_mono
    with _lock:
        _listening_started_mono = _mono()
        _current_run_status = "in_progress"
    touch_progress("listening_started")


def mark_listening_stopped(status: str = "completed") -> None:
    set_run_status(status)


def increment_counter(key: str, amount: int = 1) -> None:
    with _lock:
        _counters[key] = int(_counters.get(key, 0)) + amount


def get_counters() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def note_ui_heartbeat_age_ms(age_ms: float) -> None:
    global _max_ui_heartbeat_age_ms
    if age_ms > _max_ui_heartbeat_age_ms:
        _max_ui_heartbeat_age_ms = age_ms


def note_async_log_queue_size(size: int) -> None:
    global _max_async_log_queue_size
    if size > _max_async_log_queue_size:
        _max_async_log_queue_size = size


def elapsed_listening_seconds() -> float:
    with _lock:
        if _listening_started_mono <= 0:
            return 0.0
        return round(_mono() - _listening_started_mono, 1)


def _safe_qsize(q: Any) -> int:
    if q is None:
        return -1
    try:
        return int(q.qsize())
    except Exception:
        return -1


def _host_snapshot(host: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "listening": bool(getattr(host, "is_listening", False)),
        "is_stopping": bool(getattr(host, "_is_stopping", False)),
        "is_finalizing": bool(getattr(host, "_is_finalizing", False)),
        "exported_ui_segment_count": int(getattr(host, "_exported_ui_segment_count", 0) or 0),
        "transcript_store_count": 0,
        "ui_queue_size": _safe_qsize(getattr(host, "transcript_queue", None)),
        "audio_queue_size": _safe_qsize(getattr(host, "_audio_q", None)),
    }
    store = getattr(host, "transcript_store", None)
    if store is not None and hasattr(store, "segment_count"):
        try:
            snap["transcript_store_count"] = int(store.segment_count())
        except Exception:
            pass
    try:
        from alpha.transcription.japanese_sentence_assembler import (
            get_japanese_continuity_assembler,
        )

        asm = get_japanese_continuity_assembler(host)
        if asm is not None and hasattr(asm, "_speaker_distribution_snapshot"):
            snap["speaker_distribution"] = asm._speaker_distribution_snapshot()
    except Exception:
        pass
    return snap


def build_progress_payload(host: Any = None) -> dict[str, Any]:
    from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_event_counts

    event_counts = get_japanese_accuracy_event_counts()
    async_health: dict[str, Any] = {}
    try:
        from alpha.utils.async_debug_log import get_async_log_health

        async_health = get_async_log_health()
        note_async_log_queue_size(int(async_health.get("queue_size", 0)))
    except Exception:
        pass

    payload: dict[str, Any] = {
        "app_version": APP_VERSION,
        "app_codename": APP_CODENAME,
        "JAPANESE_STT_PROFILE": JAPANESE_STT_PROFILE,
        "JAPANESE_KEYTERM_PROFILE": JAPANESE_KEYTERM_PROFILE,
        "elapsed_seconds": elapsed_listening_seconds(),
        "current_run_status": get_run_status(),
        "ui_heartbeat_age_ms": _age_ms("last_ui_heartbeat"),
        "audio_received_age_ms": _age_ms("last_audio_frame_received"),
        "audio_sent_age_ms": _age_ms("last_audio_frame_sent_to_deepgram"),
        "deepgram_message_age_ms": _age_ms("last_deepgram_message"),
        "deepgram_final_age_ms": _age_ms("last_deepgram_final"),
        "stable_commit_age_ms": _age_ms("last_stable_commit"),
        "ui_commit_age_ms": _age_ms("last_ui_commit"),
        "async_log_flush_age_ms": _age_ms("last_async_log_flush"),
        "artifact_autosave_age_ms": _age_ms("last_artifact_autosave"),
        "async_log_queue_size": async_health.get("queue_size", -1),
        "transcript_ui_queue_size": _safe_qsize(
            getattr(host, "transcript_queue", None) if host else None
        ),
        "audio_queue_size": _safe_qsize(getattr(host, "_audio_q", None) if host else None),
        "internal_stable_commit_count": int(event_counts.get("STABLE_JAPANESE_COMMIT", 0)),
        "EMERGENCY_COMMIT_count": int(event_counts.get("EMERGENCY_COMMIT", 0)),
        "duplicate_damage_detected_count": int(
            event_counts.get("BUSINESS_JP_DUPLICATE_DAMAGE_DETECTED", 0)
        ),
        "idempotency_check_failed_count": int(
            event_counts.get("BUSINESS_JP_CLEANUP_IDEMPOTENCY_CHECK_FAILED", 0)
        ),
        "short_valid_term_dropped_count": 0,
    }
    if host is not None:
        payload.update(_host_snapshot(host))
        payload["exported_ui_segment_count"] = int(
            getattr(host, "_exported_ui_segment_count", 0) or 0
        )
    counters = get_counters()
    payload["partial_autosave_count"] = counters.get("partial_autosave_count", 0)
    payload["thread_dump_count"] = counters.get("thread_dump_count", 0)
    try:
        from alpha.utils.ui_event_bus import get_ui_event_bus

        payload.update(get_ui_event_bus().stats())
    except Exception:
        pass
    try:
        from alpha.utils.tk_thread_guard import get_tk_guard_stats

        payload.update(get_tk_guard_stats())
    except Exception:
        pass
    try:
        from alpha.utils.lock_monitor import get_lock_monitor_stats

        payload.update(get_lock_monitor_stats())
    except Exception:
        pass
    try:
        from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

        payload.update(get_language_pipeline_worker().stats())
    except Exception:
        pass
    asm = getattr(host, "_jp_continuity_assembler", None) if host else None
    if asm is not None:
        payload["snapshot_cached_return_count"] = int(
            getattr(asm, "_snapshot_cached_return_count", 0)
        )
    payload["tk_safe_pipeline_mode"] = True
    return payload


def build_long_session_health_payload(host: Any = None) -> dict[str, Any]:
    """Extended health snapshot for long-session stability mode."""
    payload = build_progress_payload(host)
    payload["long_session_stability_mode"] = LONG_SESSION_STABILITY_MODE
    payload["max_ui_heartbeat_age_ms"] = round(_max_ui_heartbeat_age_ms, 1)
    ui_hb = float(payload.get("ui_heartbeat_age_ms", -1))
    payload["ui_alive"] = ui_hb >= 0 and ui_hb < 2000
    listening = bool(payload.get("listening", False))
    payload["session_phase"] = (
        "listening" if listening else ("stopping" if payload.get("is_stopping") else "idle")
    )
    payload["max_async_log_queue_size"] = _max_async_log_queue_size
    try:
        from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_event_counts

        ec = get_japanese_accuracy_event_counts()
        payload["transcript_ui_insert_slow_count"] = int(
            ec.get("TRANSCRIPT_UI_INSERT_SLOW", 0)
        )
        payload["ui_queue_drain_slow_count"] = int(ec.get("UI_QUEUE_DRAIN_SLOW", 0))
        payload["ui_queue_tick_slow_count"] = int(ec.get("UI_QUEUE_TICK_SLOW", 0))
        payload["audio_queue_overflow_count"] = int(
            ec.get("AUDIO_QUEUE_OVERFLOW", 0)
        )
        payload["audio_queue_overflow_after_stop_count"] = int(
            ec.get("AUDIO_QUEUE_OVERFLOW_AFTER_STOP", 0)
        )
    except Exception:
        pass
    payload["python_thread_count"] = threading.active_count()
    try:
        import psutil  # type: ignore

        proc = psutil.Process()
        payload["process_memory_mb"] = round(proc.memory_info().rss / (1024 * 1024), 1)
        payload["process_thread_count"] = proc.num_threads()
        if payload["process_memory_mb"] > 1500:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log
                from alpha.utils.flight_recorder import record_flight_event

                jp_accuracy_log(
                    "PROCESS_MEMORY_HIGH",
                    process_memory_mb=payload["process_memory_mb"],
                )
                record_flight_event(
                    "process_memory_warning",
                    host=host,
                    force=True,
                    process_memory_mb=payload["process_memory_mb"],
                )
            except Exception:
                pass
    except Exception:
        try:
            from alpha.utils.process_health_telemetry import collect_process_metrics

            metrics = collect_process_metrics()
            rss = metrics.get("process_memory_rss_mb", -1)
            if rss >= 0:
                payload["process_memory_mb"] = rss
                payload.update(
                    {
                        k: v
                        for k, v in metrics.items()
                        if k.startswith("memory_")
                    }
                )
            else:
                payload["process_memory_mb"] = -1
        except Exception:
            payload["process_memory_mb"] = -1
    try:
        from alpha.utils.partial_autosave_worker import (
            get_last_success_mono,
            is_worker_alive,
        )

        payload["partial_autosave_worker_alive"] = is_worker_alive()
        payload["partial_autosave_last_success_mono"] = get_last_success_mono()
    except Exception:
        pass
    try:
        from alpha.utils.session_watchdog import get_watchdog_thread_alive

        payload["watchdog_thread_alive"] = get_watchdog_thread_alive()
    except Exception:
        pass
    return payload


def maybe_emit_long_session_health_heartbeat(host: Any = None, *, force: bool = False) -> None:
    global _last_long_session_health_mono, _last_health_timeline_mono
    if not LONG_SESSION_STABILITY_MODE:
        return
    now = _mono()
    if not force and (now - _last_long_session_health_mono) < LONG_SESSION_HEALTH_HEARTBEAT_INTERVAL_S:
        return
    _last_long_session_health_mono = now
    payload = build_long_session_health_payload(host)
    try:
        from alpha.utils.component_stall_classifier import classify_component_stalls
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("LONG_SESSION_HEALTH_HEARTBEAT", **payload)
        freeze_guard_log_sync("LONG_SESSION_HEALTH_HEARTBEAT", **payload)
        classify_component_stalls(payload, host=host)
    except Exception:
        pass
    try:
        from alpha.utils.run_artifacts import write_health_timeline_line, write_last_health_snapshot

        write_last_health_snapshot(payload)
        if force or (now - _last_health_timeline_mono) >= HEALTH_TIMELINE_INTERVAL_S:
            _last_health_timeline_mono = now
            write_health_timeline_line(payload)
    except Exception:
        pass
    try:
        from alpha.utils.flight_recorder import record_flight_event

        record_flight_event("long_session_health_heartbeat", host=host, force=True)
        record_flight_event("progress_heartbeat", host=host, force=True)
    except Exception:
        pass


def maybe_emit_progress_heartbeat(host: Any = None, *, force: bool = False) -> None:
    maybe_emit_long_session_health_heartbeat(host, force=force)
    global _last_progress_heartbeat_mono
    now = _mono()
    if not force and (now - _last_progress_heartbeat_mono) < _PROGRESS_INTERVAL_S:
        return
    _last_progress_heartbeat_mono = now
    payload = build_progress_payload(host)
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("RUN_PROGRESS_HEARTBEAT", **payload)
        freeze_guard_log_sync("RUN_PROGRESS_HEARTBEAT", **payload)
    except Exception:
        pass
    try:
        from alpha.utils.run_artifacts import write_last_health_snapshot

        write_last_health_snapshot(payload)
    except Exception:
        pass


def maybe_emit_ui_heartbeat_snapshot(host: Any, *, force: bool = False) -> None:
    global _last_ui_snapshot_mono
    now = _mono()
    if not force and (now - _last_ui_snapshot_mono) < _UI_SNAPSHOT_INTERVAL_S:
        return
    _last_ui_snapshot_mono = now
    snap = _host_snapshot(host)
    snap["ui_heartbeat_age_ms"] = _age_ms("last_ui_heartbeat")
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync

        freeze_guard_log_sync("UI_HEARTBEAT_TICK_SNAPSHOT", **snap)
    except Exception:
        pass


def maybe_emit_process_health_snapshot(host: Any = None, *, force: bool = False) -> None:
    global _last_process_snapshot_mono
    now = _mono()
    if not force and (now - _last_process_snapshot_mono) < _PROCESS_SNAPSHOT_INTERVAL_S:
        return
    _last_process_snapshot_mono = now
    try:
        from alpha.utils.process_health_telemetry import (
            collect_process_metrics,
            evaluate_process_thresholds,
            write_process_health_timeline,
        )

        payload = collect_process_metrics()
        payload["elapsed_seconds"] = elapsed_listening_seconds()
        evaluate_process_thresholds(payload)
        try:
            from alpha.utils.freeze_guard_log import freeze_guard_log_sync
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("PROCESS_HEALTH_SNAPSHOT", **payload)
            freeze_guard_log_sync("PROCESS_HEALTH_SNAPSHOT", **payload)
        except Exception:
            pass
        write_process_health_timeline(payload)
    except Exception:
        pass


def build_diagnostic_summary_extra() -> dict[str, Any]:
    extra = {
        "active_session_hang_suspected_count": get_counters().get(
            "active_session_hang_suspected_count", 0
        ),
        "active_session_hang_confirmed_count": get_counters().get(
            "active_session_hang_confirmed_count", 0
        ),
        "max_ui_heartbeat_age_ms": round(_max_ui_heartbeat_age_ms, 1),
        "max_async_log_queue_size": _max_async_log_queue_size,
        **get_counters(),
    }
    try:
        from alpha.utils.ui_callback_tracer import get_ui_callback_trace

        extra.update(get_ui_callback_trace())
    except Exception:
        pass
    try:
        from alpha.utils.partial_autosave_worker import (
            get_last_success_mono,
            is_worker_alive,
        )

        extra["partial_autosave_worker_alive"] = is_worker_alive()
        extra["partial_autosave_last_success_ts"] = get_last_success_mono()
    except Exception:
        pass
    try:
        from alpha.utils.thread_dump import (
            get_last_thread_dump_paths,
            get_thread_dump_failed_count,
        )

        paths = get_last_thread_dump_paths()
        extra["thread_dump_path"] = paths.get("last", "")
        extra["thread_dump_success"] = bool(paths.get("last"))
        extra["thread_dump_failed_count"] = get_thread_dump_failed_count()
    except Exception:
        pass
    try:
        from alpha.utils.ui_thread_guard import get_guard_counters

        extra.update(get_guard_counters())
    except Exception:
        pass
    return extra
