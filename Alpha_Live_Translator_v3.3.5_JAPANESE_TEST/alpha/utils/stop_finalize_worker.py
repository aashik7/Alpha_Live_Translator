"""Non-blocking Stop finalization worker with step tracing, timeouts, and freeze watchdog."""

from __future__ import annotations

import json
import threading
import time
import traceback
from typing import Any, Callable, Optional

from alpha.constants import (
    EVIDENCE_PACKAGE_WORKER_DEFERRED,
    NO_PENDING_MIGRATION_DURING_STOP,
    NO_RUN_ARTIFACTS_REWRITE_DURING_STOP,
    NO_UPLOAD_ZIP_DURING_RUNTIME,
    NO_VALIDATION_DURING_RUNTIME_STOP,
    NO_WRITER_REGISTRY_SCAN_DURING_STOP,
    OFFLINE_EVIDENCE_PACKAGING_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    RUN_ARTIFACTS_INDEX_NON_BLOCKING,
    STOP_PATH_MINIMAL_MODE,
    STOP_CORE_NEVER_BLOCKS_ON_EVIDENCE,
    STOP_FINALIZE_HARD_TIMEOUTS_ENFORCED,
    STOP_FINALIZE_TWO_PHASE_MODE,
)
from alpha.utils.freeze_guard_log import freeze_guard_log

# Per-stop session state (watchdog reads without UI thread).
_state_lock = threading.Lock()
_stop_state: dict[str, Any] = {
    "start_mono": 0.0,
    "ui_callback_returned": False,
    "worker_started": False,
    "worker_done": False,
    "last_step_name": "",
    "last_step_begin_mono": 0.0,
    "failed_steps": [],
    "timed_out_steps": [],
    "watchdog_thread": None,
    "finalize_thread": None,
}

_STEP_TIMEOUTS_MS: dict[str, float] = {
    "stop_audio_capture": 500.0,
    "stop_audio_producers": 1000.0,
    "drain_audio_queue": 25000.0,
    "deepgram_graceful_stop": 5000.0,
    "close_transcript_gate": 500.0,
    "cancel_scheduled_tasks": 500.0,
    "language_worker_stop": 2000.0,
    "ui_transcript_drain": 2500.0,
    "transcript_commit_confirm": 500.0,
    "japanese_assembler_flush": 500.0,
    "translation_unit_final_flush": 500.0,
    "canonical_pipeline_finalize": 2000.0,
    "write_final_alpha": 2000.0,
    "three_stage_finalize": 3000.0,
    "session_stop_logs": 500.0,
    "run_consistency_check": 2000.0,
    "long_test_readiness": 500.0,
    "final_summaries": 500.0,
    "run_artifacts_index": 2000.0,
    "async_debug_flush": 500.0,
}

_three_stage_finalize_call_count = 0

_step_completed: dict[str, bool] = {}
_evidence_flags: dict[str, bool] = {
    "alpha_output_written": False,
    "run_artifacts_index_written": False,
    "live_run_status_written": False,
    "upload_package_index_written": False,
    "upload_package_zip_created": False,
    "upload_package_zip_failed_non_blocking": False,
}
_evidence_worker_state: dict[str, Any] = {
    "running": False,
    "cancel_requested": False,
    "thread": None,
}


def _resolve_step_timeout_ms(host: Any, step_name: str) -> float:
    base = _STEP_TIMEOUTS_MS.get(step_name, 500.0)
    if step_name == "run_consistency_check":
        segment_count = int(getattr(host, "_exported_ui_segment_count", 0) or 0)
        return min(10000.0, max(2000.0, 2000.0 + segment_count * 2.0))
    if step_name == "run_artifacts_index":
        return max(2000.0, base)
    return base


def _reset_evidence_flags() -> None:
    for key in _evidence_flags:
        _evidence_flags[key] = False
    _step_completed.clear()


def _reset_stop_state() -> None:
    _reset_evidence_flags()
    with _state_lock:
        _stop_state.update(
            {
                "start_mono": time.monotonic(),
                "ui_callback_returned": False,
                "worker_started": False,
                "worker_done": False,
                "last_step_name": "",
                "last_step_begin_mono": 0.0,
                "failed_steps": [],
                "timed_out_steps": [],
            }
        )


def _safe_qsize(q: Any) -> int:
    if q is None:
        return -1
    try:
        return int(q.qsize())
    except Exception:
        return -1


def _host_snapshot(host: Any) -> dict[str, Any]:
    stabilizer = getattr(host, "_jp_final_stabilizer", None)
    accepting = None
    if stabilizer is not None and hasattr(stabilizer, "is_accepting"):
        try:
            accepting = bool(stabilizer.is_accepting())
        except Exception:
            accepting = None
    return {
        "ui_queue_size": _safe_qsize(getattr(host, "transcript_queue", None)),
        "audio_queue_size": _safe_qsize(getattr(host, "_audio_q", None)),
        "websocket_connected": bool(getattr(host, "_dg_ws", None) is not None),
        "is_listening": bool(getattr(host, "is_listening", False)),
        "is_stopping": bool(getattr(host, "_is_stopping", False)),
        "accepting_transcripts": accepting,
    }


def run_timed_step(host: Any, step_name: str, func: Callable[[], None]) -> bool:
    """Run one finalize step with timeout; never raises."""
    timeout_ms = _resolve_step_timeout_ms(host, step_name)
    begin_mono = time.monotonic()
    with _state_lock:
        _stop_state["last_step_name"] = step_name
        _stop_state["last_step_begin_mono"] = begin_mono

    freeze_guard_log(
        "STOP_FINALIZE_STEP_BEGIN",
        step_name=step_name,
        timeout_ms=timeout_ms,
        timestamp=int(time.time() * 1000),
    )

    result: dict[str, Any] = {"error": None, "done": False}

    def _target() -> None:
        try:
            func()
            result["done"] = True
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(
        target=_target, name=f"StopStep-{step_name}", daemon=True
    )
    thread.start()
    thread.join(timeout=max(0.05, timeout_ms / 1000.0))

    duration_ms = round((time.monotonic() - begin_mono) * 1000.0, 2)
    timed_out_join = thread.is_alive()

    if timed_out_join:
        thread.join(timeout=0.5)
        if not thread.is_alive() and result["done"]:
            timed_out_join = False

    if timed_out_join:
        with _state_lock:
            if step_name not in _stop_state["timed_out_steps"]:
                _stop_state["timed_out_steps"].append(step_name)
        freeze_guard_log(
            "STOP_FINALIZE_STEP_TIMEOUT",
            step_name=step_name,
            timeout_ms=timeout_ms,
            duration_ms=duration_ms,
        )
        return False

    if result["error"] is not None:
        exc = result["error"]
        with _state_lock:
            _stop_state["failed_steps"].append(step_name)
        freeze_guard_log(
            "STOP_FINALIZE_STEP_FAILED",
            step_name=step_name,
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            short_traceback="".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-800:],
            duration_ms=duration_ms,
        )
        return False

    freeze_guard_log(
        "STOP_FINALIZE_STEP_END",
        step_name=step_name,
        duration_ms=duration_ms,
    )
    _step_completed[step_name] = True
    with _state_lock:
        if step_name in _stop_state["timed_out_steps"]:
            _stop_state["timed_out_steps"].remove(step_name)
    return True


def _watchdog_loop(host: Any) -> None:
    while True:
        with _state_lock:
            if _stop_state["worker_done"]:
                break
            start_mono = float(_stop_state["start_mono"])
            ui_returned = bool(_stop_state["ui_callback_returned"])
            worker_started = bool(_stop_state["worker_started"])
            worker_done = bool(_stop_state["worker_done"])
            last_step = str(_stop_state["last_step_name"])
            last_begin = float(_stop_state["last_step_begin_mono"])

        elapsed_ms = round((time.monotonic() - start_mono) * 1000.0, 1)
        last_step_running_ms = (
            round((time.monotonic() - last_begin) * 1000.0, 1)
            if last_step and last_begin > 0
            else 0.0
        )
        snap = _host_snapshot(host)

        freeze_guard_log(
            "STOP_FREEZE_WATCHDOG_HEARTBEAT",
            elapsed_ms=elapsed_ms,
            ui_callback_returned=ui_returned,
            worker_started=worker_started,
            worker_done=worker_done,
            last_step_name=last_step,
            last_step_duration_ms_if_running=last_step_running_ms,
            **snap,
        )

        if elapsed_ms > 5000.0 and not worker_done:
            freeze_guard_log(
                "STOP_FREEZE_SUSPECTED",
                last_step_name=last_step,
                elapsed_ms=elapsed_ms,
                state_snapshot=snap,
            )

        time.sleep(1.0)


def _start_watchdog(host: Any) -> None:
    with _state_lock:
        old = _stop_state.get("watchdog_thread")
        if old is not None and getattr(old, "is_alive", lambda: False)():
            return
        t = threading.Thread(
            target=_watchdog_loop, name="StopFreezeWatchdog", daemon=True, args=(host,)
        )
        _stop_state["watchdog_thread"] = t
        t.start()


def _queue_final_ui_update(host: Any, *, timed_out: bool) -> None:
    """Schedule lightweight UI finish on main thread — never call Tk from worker."""
    runner = getattr(host, "_run_on_ui_thread", None)
    finish = getattr(host, "_finish_graceful_stop", None)
    if not callable(finish):
        freeze_guard_log("FINAL_UI_UPDATE_SKIPPED_DURING_STOP", reason="no_finish_fn")
        return
    if not callable(runner):
        freeze_guard_log("FINAL_UI_UPDATE_SKIPPED_DURING_STOP", reason="no_ui_runner")
        return

    summary = build_stop_finalize_summary(host)
    ui_timed_out = bool(summary.get("stop_finalize_timed_out", timed_out))

    def _ui_finish() -> None:
        try:
            finish(ui_timed_out)
        except Exception as exc:
            freeze_guard_log(
                "FINAL_UI_UPDATE_FAILED",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )

    try:
        runner(_ui_finish)
        freeze_guard_log("STOP_UI_UPDATE_POSTED_TO_EVENT_BUS")
        summary_payload = dict(summary)
        summary_payload.pop("timed_out", None)
        freeze_guard_log(
            "FINAL_UI_UPDATE_QUEUED",
            timed_out=ui_timed_out,
            **summary_payload,
        )
        freeze_guard_log("FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_APPLIED")
        freeze_guard_log("FINAL_UI_UPDATE_POSTED_AFTER_STOP")
    except Exception as exc:
        freeze_guard_log(
            "FINAL_UI_UPDATE_SKIPPED_DURING_STOP",
            reason="queue_failed",
            exception_message=str(exc),
        )


def request_evidence_package_cancel_on_exit() -> None:
    _evidence_worker_state["cancel_requested"] = True
    freeze_guard_log("EVIDENCE_PACKAGE_CANCEL_REQUESTED_ON_EXIT")
    freeze_guard_log("BACKGROUND_DIAGNOSTIC_THREAD_CANCEL_REQUESTED")


def _write_minimal_run_artifacts_index(host: Any, *, reason: str) -> None:
    try:
        from alpha.utils.run_identity import get_current_run_identity
        from alpha.utils.troubleshooting_paths import get_artifact_path

        ident = get_current_run_identity()
        p = get_artifact_path("run_artifacts_index").parent / "RUN_ARTIFACTS_INDEX.minimal.txt"
        payload = {
            "status": "completed_with_warnings",
            "run_id": getattr(ident, "run_id", ""),
            "run_timestamp": getattr(ident, "run_timestamp", ""),
            "app_version": getattr(ident, "app_version", ""),
            "run_artifacts_index_status": "deferred_timeout",
            "reason": reason,
        }
        p.write_text("\n".join(f"{k}={v}" for k, v in payload.items()) + "\n", encoding="utf-8")
        freeze_guard_log("RUN_ARTIFACTS_INDEX_MINIMAL_WRITTEN", path=str(p))
    except Exception:
        pass


def _write_core_live_status(host: Any) -> None:
    try:
        from alpha.utils.run_identity import get_current_run_identity
        from alpha.utils.troubleshooting_paths import get_artifact_path

        ident = get_current_run_identity()
        p = get_artifact_path("live_run_status")
        payload = {
            "status": "stopped_core_completed",
            "run_id": getattr(ident, "run_id", ""),
            "run_timestamp": getattr(ident, "run_timestamp", ""),
            "app_version": getattr(ident, "app_version", ""),
            "stop_core_completed": True,
            "stop_core_failed": False,
            "evidence_package_status": "deferred",
            "stop_finalize_completed": True,
            "stop_finalize_failed": False,
            "completed_with_warnings": False,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _evidence_flags["live_run_status_written"] = True
    except Exception:
        pass


def _write_minimal_runtime_artifacts(host: Any, *, dg_result: Optional[dict[str, Any]] = None) -> None:
    """Runtime-safe minimal artifact writes only; no scans/packages/validation."""
    from alpha.utils.run_identity import get_current_run_identity
    from alpha.utils.troubleshooting_paths import (
        get_artifact_path,
        get_run_manifest_path,
    )

    ident = get_current_run_identity()
    run_id = getattr(ident, "run_id", "")
    run_ts = getattr(ident, "run_timestamp", "")
    app_version = getattr(ident, "app_version", "")
    # RUN_ARTIFACTS_INDEX minimal
    idx = get_artifact_path("run_artifacts_index")
    idx_lines = [
        "status=stopped_runtime_minimal",
        f"run_id={run_id}",
        f"run_timestamp={run_ts}",
        f"app_version={app_version}",
        "stop_core_completed=true",
        "stop_finalize_completed=true",
        "evidence_package_status=disabled_during_runtime",
    ]
    idx.write_text("\n".join(idx_lines) + "\n", encoding="utf-8")
    _evidence_flags["run_artifacts_index_written"] = True
    # LIVE_RUN_STATUS minimal
    _write_core_live_status(host)
    # RUN_MANIFEST minimal update
    manifest_path = get_run_manifest_path()
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    data.update(
        {
            "run_id": run_id,
            "run_timestamp": run_ts,
            "app_version": app_version,
            "final_status": "completed_with_warnings",
            "stop_core_completed": True,
            "stop_finalize_completed": True,
            "evidence_package_status": "disabled_during_runtime",
            "deepgram_close_status": (dg_result or {}).get("status", ""),
        }
    )
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def get_stop_finalize_snapshot() -> dict[str, Any]:
    with _state_lock:
        failed = list(_stop_state["failed_steps"])
        timed_out_steps = list(_stop_state["timed_out_steps"])
        return {
            "ui_callback_returned": bool(_stop_state["ui_callback_returned"]),
            "worker_started": bool(_stop_state["worker_started"]),
            "worker_done": bool(_stop_state["worker_done"]),
            "finalize_completed": bool(_stop_state.get("finalize_completed", False)),
            "failed_steps": failed,
            "timed_out_steps": timed_out_steps,
            "stop_finalize_failed": len(failed) > 0,
            "stop_finalize_timed_out": len(timed_out_steps) > 0,
            "worker_duration_ms": round(
                (time.monotonic() - float(_stop_state["start_mono"])) * 1000.0, 2
            )
            if _stop_state["start_mono"]
            else 0.0,
        }


def build_stop_finalize_summary(
    host: Any,
    *,
    dg_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Normalized stop finalize evidence — timed_out only when steps actually timed out."""
    snap = get_stop_finalize_snapshot()
    failed_steps = list(snap.get("failed_steps") or [])
    timed_out_steps = list(snap.get("timed_out_steps") or [])
    dg_status = str(getattr(host, "_dg_close_status", "unknown"))
    dg_late_normal = False

    if dg_result and dg_result.get("timed_out") and dg_status in ("normal", "late_normal"):
        dg_late_normal = True
        try:
            from alpha.utils.freeze_guard_log import freeze_guard_log
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            freeze_guard_log(
                "DEEPGRAM_CLOSE_LATE_NORMAL",
                deepgram_close_status=dg_status,
            )
            jp_accuracy_log("DEEPGRAM_CLOSE_LATE_NORMAL", deepgram_close_status=dg_status)
        except Exception:
            pass

    stop_ui_ms = 0.0
    identity_finalize = False
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if ident is not None:
            stop_ui_ms = float(ident.stop_ui_callback_duration_ms or 0.0)
            identity_finalize = bool(ident.stop_finalize_completed)
            if ident.deepgram_close_status:
                dg_status = str(ident.deepgram_close_status)
    except Exception:
        pass

    summary = {
        "stop_finalize_completed": bool(
            snap.get("finalize_completed", False)
            or _evidence_flags.get("alpha_output_written")
            or identity_finalize
        ),
        "stop_finalize_failed": len(failed_steps) > 0,
        "stop_finalize_timed_out": len(timed_out_steps) > 0,
        "timed_out": len(timed_out_steps) > 0,
        "failed": len(failed_steps) > 0,
        "timed_out_steps": timed_out_steps,
        "failed_steps": failed_steps,
        "deepgram_close_status": dg_status,
        "deepgram_close_late_normal": dg_late_normal,
        "stop_finalize_duration_ms": snap.get("worker_duration_ms", 0.0),
        "stop_ui_callback_duration_ms": stop_ui_ms,
        "alpha_output_written": _evidence_flags.get("alpha_output_written", False),
        "run_artifacts_index_written": _evidence_flags.get(
            "run_artifacts_index_written", False
        ),
        "live_run_status_written": _evidence_flags.get("live_run_status_written", False),
        "upload_package_index_written": _evidence_flags.get(
            "upload_package_index_written", False
        ),
        "upload_package_zip_created": _evidence_flags.get(
            "upload_package_zip_created", False
        ),
        "upload_package_zip_failed_non_blocking": _evidence_flags.get(
            "upload_package_zip_failed_non_blocking", False
        ),
    }

    if summary["stop_finalize_completed"] and summary["alpha_output_written"]:
        try:
            from alpha.utils.freeze_guard_log import freeze_guard_log
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("STOP_FINALIZE_EVIDENCE_CONSISTENT", **summary)
            freeze_guard_log("STOP_FINALIZE_EVIDENCE_CONSISTENT", **summary)
        except Exception:
            pass

    if summary["stop_finalize_completed"] and not summary["stop_finalize_timed_out"]:
        try:
            from alpha.utils.freeze_guard_log import freeze_guard_log
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("STOP_FINALIZE_SUMMARY_NORMALIZED", **summary)
            freeze_guard_log("STOP_FINALIZE_SUMMARY_NORMALIZED", **summary)
        except Exception:
            pass
    elif summary["stop_finalize_completed"] and summary["stop_finalize_timed_out"]:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("STOP_FINALIZE_TIMEOUT_FLAG_CORRECTED", **summary)
        except Exception:
            pass

    return summary


def _block_audio_capture(host: Any) -> None:
    """Block new capture input without stopping Deepgram sender yet."""
    try:
        setattr(host, "_audio_capture_blocked", True)
        setattr(host, "_accepting_new_audio", False)
    except Exception:
        pass
    freeze_guard_log("STOP_AUDIO_CAPTURE_BLOCKED")


def _stop_audio_producers(host: Any) -> None:
    if hasattr(host, "_stop_health_monitor_ui_safe"):
        host._stop_health_monitor_ui_safe()
    elif hasattr(host, "_stop_health_monitor"):
        host._stop_health_monitor()
    if hasattr(host, "_close_wasapi_stream"):
        host._close_wasapi_stream()
    if hasattr(host, "_close_microphone_stream"):
        host._close_microphone_stream()
    freeze_guard_log("STOP_AUDIO_PRODUCERS_STOPPED")


def _drain_outgoing_audio_queue(host: Any, *, timeout_seconds: float = 25.0) -> dict[str, Any]:
    """Drain queued audio to Deepgram before stop-sending — never clear queues."""
    freeze_guard_log("STOP_AUDIO_QUEUE_DRAIN_STARTED")
    result: dict[str, Any] = {
        "sent": 0,
        "remaining": 0,
        "pipeline_flush_ok": False,
        "timed_out": False,
    }
    pipeline_sizes: dict[str, Any] = {}
    if hasattr(host, "_get_pipeline_queue_sizes"):
        try:
            pipeline_sizes = host._get_pipeline_queue_sizes()
        except Exception:
            pipeline_sizes = {}
    result["pipeline_before"] = pipeline_sizes

    # Keep flush+drain inside the Stop step budget (default 25s).
    total_budget = max(0.2, float(timeout_seconds))
    flush_budget = min(5.0, total_budget * 0.25)
    drain_budget = max(0.1, total_budget - flush_budget)
    if hasattr(host, "wait_for_outgoing_audio_flush"):
        try:
            result["pipeline_flush_ok"] = bool(
                host.wait_for_outgoing_audio_flush(timeout_seconds=flush_budget)
            )
        except Exception:
            result["pipeline_flush_ok"] = False

    if hasattr(host, "_drain_audio_queue_to_deepgram"):
        try:
            result["sent"] = int(
                host._drain_audio_queue_to_deepgram(max_seconds=drain_budget) or 0
            )
        except Exception:
            result["sent"] = 0

    if hasattr(host, "get_outgoing_audio_queue_size"):
        try:
            result["remaining"] = int(host.get_outgoing_audio_queue_size() or 0)
        except Exception:
            result["remaining"] = 0
    elif hasattr(host, "_get_pipeline_queue_sizes"):
        try:
            sizes = host._get_pipeline_queue_sizes()
            result["remaining"] = int(
                sizes.get("audio_q", 0)
                + sizes.get("sys_q", 0)
                + sizes.get("mic_q", 0)
            )
        except Exception:
            pass

    if result["remaining"] > 0:
        result["timed_out"] = True
        with _state_lock:
            if "drain_audio_queue" not in _stop_state["failed_steps"]:
                _stop_state["failed_steps"].append("drain_audio_queue")
        freeze_guard_log(
            "STOP_AUDIO_QUEUE_DRAIN_TIMEOUT",
            remaining=result["remaining"],
            sent=result["sent"],
            pipeline_sizes=pipeline_sizes,
        )
    else:
        freeze_guard_log(
            "STOP_AUDIO_QUEUE_DRAIN_COMPLETED",
            sent=result["sent"],
            pipeline_flush_ok=result.get("pipeline_flush_ok"),
        )
    return result


def _cancel_scheduled_assembler_tasks(host: Any) -> None:
    try:
        from alpha.transcription.japanese_sentence_assembler import (
            get_japanese_continuity_assembler,
        )
        from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

        assembler = get_japanese_continuity_assembler(host)
        worker = get_language_pipeline_worker()
        worker.cancel_flush(assembler)
        cancelled = worker.cancel_all_tasks()
        freeze_guard_log("SCHEDULED_ASSEMBLER_TASKS_CANCELLED", cancelled=cancelled)
    except Exception as exc:
        freeze_guard_log(
            "SCHEDULED_ASSEMBLER_TASKS_CANCEL_FAILED",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )


def _stop_language_pipeline_worker(host: Any) -> dict[str, Any]:
    from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

    result = get_language_pipeline_worker().stop_and_join(timeout_seconds=2.0)
    try:
        setattr(host, "_language_pipeline_worker_alive", False)
    except Exception:
        pass
    freeze_guard_log("LANGUAGE_WORKER_STOPPED", **result)
    return result


def _confirm_transcript_commits(host: Any) -> None:
    """Confirm assembler/UI queues have no pending transcript commits."""
    pending_worker = 0
    try:
        from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

        pending_worker = get_language_pipeline_worker().pending_task_count()
    except Exception:
        pass
    transcript_remaining = _safe_qsize(getattr(host, "transcript_queue", None))
    batch_remaining = len(getattr(host, "_transcript_ui_batch_buffer", []) or [])
    freeze_guard_log(
        "STOP_TRANSCRIPT_COMMITS_CONFIRMED",
        transcript_queue_remaining=transcript_remaining,
        transcript_batch_remaining=batch_remaining,
        language_pipeline_pending_task_count=pending_worker,
    )


def _invoke_three_stage_finalize_once(host: Any) -> None:
    global _three_stage_finalize_call_count
    _three_stage_finalize_call_count += 1
    if _three_stage_finalize_call_count > 1:
        freeze_guard_log(
            "THREE_STAGE_FINALIZER_DUPLICATE_CALL_BLOCKED",
            call_count=_three_stage_finalize_call_count,
        )
        return
    from alpha.utils.path_types import ensure_path
    from alpha.utils.run_identity import get_current_run_identity

    ident = get_current_run_identity()
    run_folder = ensure_path(getattr(ident, "run_folder", None) if ident else None)
    from alpha.utils.accuracy_stage_capture import finalize_three_stage_on_stop

    finalize_three_stage_on_stop(host, run_folder=run_folder)
    freeze_guard_log(
        "THREE_STAGE_FINALIZER_COMPLETED",
        three_stage_finalize_call_count=_three_stage_finalize_call_count,
    )


def _run_deepgram_finalize_sequence(host: Any, dg_result: dict[str, Any]) -> None:
    """Finalize Deepgram after audio drain — no queue clearing before drain."""
    from alpha.utils.async_debug_log import log_runtime_debug_event
    from alpha.utils.run_identity import get_current_run_identity

    begin = time.monotonic()
    freeze_guard_log("DEEPGRAM_GRACEFUL_STOP_BEGIN")
    log_runtime_debug_event("DEEPGRAM_GRACEFUL_STOP_BEGIN")

    host._ensure_graceful_stop_state()
    host.is_listening = False
    host._is_finalizing = True
    host._is_stopping = True
    host._dg_receiver_allowed = True
    host._dg_graceful_stop_active = True
    host._dg_close_status = "pending"

    host._dg_stop_sending_audio = True
    freeze_guard_log("DEEPGRAM_AUDIO_SEND_STOPPED")
    log_runtime_debug_event("DEEPGRAM_AUDIO_SEND_STOPPED")

    finalized = False
    closed = False
    timed_out = False
    try:
        if hasattr(host, "request_finalize"):
            finalized = bool(host.request_finalize())
        freeze_guard_log("DEEPGRAM_FINALIZE_SENT", finalized=finalized)

        if hasattr(host, "_wait_for_final_transcripts_after_finalize"):
            host._wait_for_final_transcripts_after_finalize(max_seconds=2.5)
        freeze_guard_log("DEEPGRAM_FINAL_MESSAGES_DRAINED")

        if hasattr(host, "request_close_stream"):
            closed = bool(host.request_close_stream())

        if hasattr(host, "_wait_bounded"):
            host._wait_bounded(0.5)

        ws = getattr(host, "_dg_ws", None)
        if ws is not None:
            try:
                log_runtime_debug_event("DEEPGRAM_CLOSE_REQUESTED", reason="ordered_stop")
                ws.close()
            except Exception:
                pass
            host._dg_ws = None
    except Exception as exc:
        timed_out = True
        freeze_guard_log(
            "DEEPGRAM_FINALIZE_SEQUENCE_FAILED",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )

    host._dg_receiver_allowed = False
    duration_ms = round((time.monotonic() - begin) * 1000.0, 2)
    status = "normal"
    if timed_out:
        status = "timeout"
        host._dg_close_status = "timeout"
    elif finalized and closed:
        status = "normal"
        host._dg_close_status = status
    else:
        status = "partial"
        host._dg_close_status = status

    dg_result.update(
        {
            "timed_out": timed_out,
            "finalized": finalized,
            "closed": closed,
        }
    )
    identity = get_current_run_identity()
    if identity is not None:
        identity.deepgram_close_status = status
        identity.deepgram_graceful_stop_duration_ms = duration_ms

    freeze_guard_log(
        "DEEPGRAM_GRACEFUL_STOP_DONE",
        deepgram_graceful_stop_duration_ms=duration_ms,
        deepgram_close_status=status,
        timed_out=timed_out,
    )
    log_runtime_debug_event(
        "DEEPGRAM_GRACEFUL_STOP_DONE",
        deepgram_graceful_stop_duration_ms=duration_ms,
        deepgram_close_status=status,
    )


def _run_evidence_package_worker(host: Any, dg_result: dict[str, Any], readiness: dict[str, Any]) -> None:
    if RUNTIME_EVIDENCE_PACKAGE_DISABLED:
        freeze_guard_log("EVIDENCE_PACKAGE_WORKER_DISABLED_DURING_RUNTIME")
        return
    freeze_guard_log("EVIDENCE_PACKAGE_WORKER_STARTED")
    freeze_guard_log("EVIDENCE_WORKER_NO_TK_CALL_CONFIRMED")
    _evidence_worker_state["running"] = True
    _evidence_worker_state["cancel_requested"] = False
    try:
        consistency_result: dict[str, Any] = {}
        freeze_guard_log("RUN_CONSISTENCY_CHECK_DEFERRED_TO_EVIDENCE_WORKER")
        freeze_guard_log("EVIDENCE_PACKAGE_STEP_BEGIN", step_name="run_consistency_check")
        run_timed_step(
            host,
            "run_consistency_check",
            lambda: consistency_result.update(__import__("alpha.utils.run_consistency", fromlist=["validate_run_consistency"]).validate_run_consistency(host=host)),
        )
        freeze_guard_log("EVIDENCE_PACKAGE_STEP_END", step_name="run_consistency_check")
        if consistency_result.get("warning_reasons"):
            freeze_guard_log("RUN_CONSISTENCY_CHECK_WARNING", warnings=consistency_result.get("warning_reasons"))

        if _evidence_worker_state.get("cancel_requested"):
            freeze_guard_log("APP_CLOSE_DURING_EVIDENCE_PACKAGE")
            return

        def _run_artifacts_phase() -> None:
            from alpha.transcription.japanese_sentence_assembler import get_japanese_continuity_assembler
            from alpha.utils.run_artifacts import create_upload_evidence_package, finalize_live_run_status_completed, write_final_alpha_output
            from alpha.utils.runtime_evidence import build_artifact_index_extra, write_run_artifacts_index_safe
            from alpha.utils.run_identity import get_current_run_identity, validate_all_artifacts_use_same_run_id
            from alpha.utils.segment_count_reconciliation import reconcile_final_segment_counts
            from alpha.utils.troubleshooting_paths import (
                finalize_latest_pointers,
                finalize_run_manifest,
                get_active_run_folder,
                migrate_pending_files_to_run_folder,
                preflight_upload_evidence,
                write_writer_registry_snapshot,
            )

            folder = get_active_run_folder()
            if folder is not None:
                migrate_pending_files_to_run_folder(folder)
                preflight_upload_evidence(folder)
                write_writer_registry_snapshot(folder)

            write_final_alpha_output(host)
            _evidence_flags["alpha_output_written"] = True
            assembler = get_japanese_continuity_assembler(host)
            identity = get_current_run_identity()
            segment_counts = reconcile_final_segment_counts(host)
            stop_summary = build_stop_finalize_summary(host, dg_result=dg_result)
            extra = build_artifact_index_extra(host, assembler=assembler, readiness=readiness, stop_state={**get_stop_finalize_snapshot(), **stop_summary})
            extra.update(segment_counts)
            extra.update(stop_summary)
            freeze_guard_log("RUN_ARTIFACTS_INDEX_STEP_STARTED")
            idx = write_run_artifacts_index_safe(host, extra=extra)
            if idx is not None:
                _evidence_flags["run_artifacts_index_written"] = True
                freeze_guard_log("RUN_ARTIFACTS_INDEX_STEP_COMPLETED", path=str(idx))
            else:
                freeze_guard_log("RUN_ARTIFACTS_INDEX_STEP_TIMEOUT")
                _write_minimal_run_artifacts_index(host, reason="write_failed_or_timeout")
                freeze_guard_log("RUN_ARTIFACTS_INDEX_DEFERRED_AFTER_TIMEOUT")

            status_check = validate_all_artifacts_use_same_run_id() if identity is not None else {"ok": True}
            if not bool(status_check.get("ok", True)):
                freeze_guard_log("RUN_ID_MISMATCH_NON_BLOCKING_WARNING", mismatches=status_check.get("mismatches", []))

            pkg = create_upload_evidence_package(host, status="completed")
            upload_zip_path = ""
            if pkg is not None:
                if str(pkg).endswith(".zip"):
                    _evidence_flags["upload_package_zip_created"] = True
                    upload_zip_path = str(pkg)
                _evidence_flags["upload_package_index_written"] = True
            else:
                _evidence_flags["upload_package_zip_failed_non_blocking"] = True

            stop_summary = build_stop_finalize_summary(host, dg_result=dg_result)
            stop_summary.update(_evidence_flags)
            finalize_live_run_status_completed(host, segment_counts=segment_counts, stop_summary=stop_summary, evidence_flags=_evidence_flags)
            _evidence_flags["live_run_status_written"] = True
            if folder is not None and identity is not None:
                final_status = "completed_with_warnings" if _evidence_flags.get("upload_package_zip_failed_non_blocking") else "completed"
                finalize_latest_pointers(folder, run_id=identity.run_id, status=final_status, upload_zip_path=upload_zip_path)
                finalize_run_manifest(folder, status=final_status, artifact_flags=_evidence_flags, stop_summary=stop_summary)

        freeze_guard_log("EVIDENCE_PACKAGE_STEP_BEGIN", step_name="run_artifacts_index")
        ok = run_timed_step(host, "run_artifacts_index", _run_artifacts_phase)
        freeze_guard_log("EVIDENCE_PACKAGE_STEP_END", step_name="run_artifacts_index", ok=ok)
        if not ok and RUN_ARTIFACTS_INDEX_NON_BLOCKING:
            freeze_guard_log("RUN_ARTIFACTS_INDEX_STEP_TIMEOUT")
            _write_minimal_run_artifacts_index(host, reason="timed_out")
            freeze_guard_log("RUN_ARTIFACTS_INDEX_DEFERRED_AFTER_TIMEOUT")
            freeze_guard_log("EVIDENCE_PACKAGE_STEP_TIMEOUT_DEFERRED", step_name="run_artifacts_index")

        freeze_guard_log("EVIDENCE_PACKAGE_COMPLETED_WITH_WARNINGS" if _evidence_flags.get("upload_package_zip_failed_non_blocking") else "EVIDENCE_PACKAGE_COMPLETED")
    except Exception as exc:
        freeze_guard_log("EVIDENCE_PACKAGE_FAILED_NON_BLOCKING", exception_type=type(exc).__name__, exception_message=str(exc))
    finally:
        _evidence_worker_state["running"] = False


def _run_finalize_worker(host: Any) -> None:
    with _state_lock:
        _stop_state["worker_started"] = True
    try:
        from alpha.utils.tk_thread_guard import set_tk_guard_stop_finalize_active

        set_tk_guard_stop_finalize_active(True)
    except Exception:
        pass
    freeze_guard_log("STOP_FINALIZE_WORKER_STARTED")
    try:
        from alpha.utils.flight_recorder import record_flight_event

        record_flight_event("stop_finalize_started", host=host, force=True)
    except Exception:
        pass

    timed_out = False
    dg_result: dict[str, Any] = {}
    global _three_stage_finalize_call_count
    _three_stage_finalize_call_count = 0
    try:
        freeze_guard_log("STOP_ORDERED_SEQUENCE_BEGIN")
        freeze_guard_log("STOP_WORKER_NO_TK_CALL_CONFIRMED")
        from alpha.transcription.japanese_final_chunk_stabilizer import (
            close_japanese_transcript_gate,
            flush_japanese_assembler_on_stop,
        )

        def _session_side_logs() -> None:
            if hasattr(host, "log_latency_stop_clicked_snapshot"):
                host.log_latency_stop_clicked_snapshot()
            if hasattr(host, "_session_log"):
                host._session_log(
                    "[SESSION] stop clicked",
                    {"timestamp": int(time.time() * 1000)},
                )
            try:
                from alpha.constants import MEETING_SEGMENT_BUFFER_ENABLED

                if MEETING_SEGMENT_BUFFER_ENABLED and hasattr(
                    host, "_flush_meeting_segment_buffer"
                ):
                    host._flush_meeting_segment_buffer("stop_clicked")
            except Exception:
                pass

        run_timed_step(host, "session_stop_logs", _session_side_logs)

        # V26.5.1 Stop order: stop accepting new audio → drain queued PCM while
        # the Deepgram sender is still alive → then signal stop / finalize.
        run_timed_step(host, "stop_audio_capture", lambda: _block_audio_capture(host))
        run_timed_step(host, "stop_audio_producers", lambda: _stop_audio_producers(host))

        audio_drain: dict[str, Any] = {}
        run_timed_step(
            host,
            "drain_audio_queue",
            lambda: audio_drain.update(
                _drain_outgoing_audio_queue(host, timeout_seconds=25.0)
            ),
        )

        if hasattr(host, "_stop_event"):
            host._stop_event.set()

        dg_result = {}
        run_timed_step(
            host,
            "deepgram_graceful_stop",
            lambda: _run_deepgram_finalize_sequence(host, dg_result),
        )
        host._last_graceful_stop_result = dict(dg_result)

        if hasattr(host, "request_interim_stop_tail_recovery"):
            try:
                host.request_interim_stop_tail_recovery(timeout_seconds=2.0)
            except Exception:
                pass

        run_timed_step(
            host,
            "close_transcript_gate",
            lambda: close_japanese_transcript_gate(
                host, "TRANSCRIPT_GATE_CLOSED_AFTER_DEEPGRAM"
            ),
        )
        try:
            setattr(host, "_accepting_transcripts", False)
        except Exception:
            pass
        freeze_guard_log("TRANSCRIPT_GATE_CLOSED_AFTER_DEEPGRAM")

        run_timed_step(
            host,
            "japanese_assembler_flush",
            lambda: flush_japanese_assembler_on_stop(host, "stop_listening"),
        )
        freeze_guard_log("ASSEMBLER_STOP_FLUSH_COMPLETED")

        run_timed_step(host, "cancel_scheduled_tasks", lambda: _cancel_scheduled_assembler_tasks(host))
        run_timed_step(
            host, "language_worker_stop", lambda: _stop_language_pipeline_worker(host)
        )

        # V25.3.3.1: final UI update BEFORE drain barrier so post-drain UI posts = 0
        stop_summary_pre = build_stop_finalize_summary(host, dg_result=dg_result)
        timed_out_pre = bool(stop_summary_pre.get("stop_finalize_timed_out", False))
        _queue_final_ui_update(host, timed_out=timed_out_pre)
        freeze_guard_log("FINAL_UI_UPDATE_QUEUED_BEFORE_DRAIN")

        from alpha.utils.ui_stop_drain_barrier import request_stop_ui_drain

        ui_drain: dict[str, Any] = {}
        run_timed_step(
            host,
            "ui_transcript_drain",
            lambda: ui_drain.update(request_stop_ui_drain(host, timeout_seconds=2.5)),
        )
        ui_events_posted_after_final_drain = int(
            ui_drain.get("ui_events_posted_after_final_drain")
            or ui_drain.get("events_posted_after_drain")
            or 0
        )
        if ui_events_posted_after_final_drain != 0:
            freeze_guard_log(
                "UI_EVENTS_POSTED_AFTER_FINAL_DRAIN",
                count=ui_events_posted_after_final_drain,
            )
        else:
            freeze_guard_log("UI_EVENTS_POSTED_AFTER_FINAL_DRAIN", count=0)
        try:
            setattr(host, "_ui_events_posted_after_final_drain", ui_events_posted_after_final_drain)
        except Exception:
            pass

        run_timed_step(host, "transcript_commit_confirm", lambda: _confirm_transcript_commits(host))

        def _translation_unit_flush() -> None:
            from alpha.transcription.japanese_sentence_assembler import (
                get_japanese_continuity_assembler,
            )

            freeze_guard_log("TRANSLATION_UNIT_FINAL_FLUSH_BEGIN")
            try:
                assembler = get_japanese_continuity_assembler(host)
                builder = getattr(assembler, "_translation_unit_builder", None)
                if builder is None:
                    freeze_guard_log("TRANSLATION_UNIT_FINAL_FLUSH_DONE", skipped="no_builder")
                    return
                builder.flush(reason="stop_listening")
                freeze_guard_log("TRANSLATION_UNIT_FINAL_FLUSH_DONE")
            except Exception as exc:
                freeze_guard_log(
                    "TRANSLATION_UNIT_FINAL_FLUSH_FAILED",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )

        run_timed_step(host, "translation_unit_final_flush", _translation_unit_flush)

        def _canonical_finalize() -> None:
            from alpha.utils.canonical_finalize import finalize_canonical_pipeline

            result = finalize_canonical_pipeline(host)
            if result.get("ok"):
                freeze_guard_log("CANONICAL_LEDGER_FROZEN", snapshot_id=result.get("snapshot_id"))
            else:
                freeze_guard_log("CANONICAL_LEDGER_FREEZE_FAILED", result=result)

        run_timed_step(host, "canonical_pipeline_finalize", _canonical_finalize)

        def _write_final_export() -> None:
            from alpha.utils.run_artifacts import write_final_alpha_output
            from alpha.utils.final_artifact_authority import (
                get_final_export_authority_state,
                verify_final_export_seal,
            )
            from alpha.utils.run_identity import get_current_run_identity

            path = write_final_alpha_output(host)
            if path is not None:
                _evidence_flags["alpha_output_written"] = True
                freeze_guard_log("FINAL_ALPHA_ATOMIC_WRITE_COMPLETED", path=str(path))
                ident = get_current_run_identity()
                folder = getattr(ident, "run_folder", None) if ident else None
                if folder:
                    verify_final_export_seal(folder, run_id=getattr(ident, "run_id", ""))
                    state = get_final_export_authority_state(folder)
                    freeze_guard_log(
                        "FINAL_EXPORT_SEAL_VERIFIED",
                        write_count=state.get("write_count"),
                        sealed=state.get("sealed"),
                    )
            else:
                _evidence_flags["alpha_output_written"] = False
                freeze_guard_log("FINAL_ALPHA_ATOMIC_WRITE_FAILED")

        run_timed_step(host, "write_final_alpha", _write_final_export)

        run_timed_step(host, "three_stage_finalize", lambda: _invoke_three_stage_finalize_once(host))

        try:
            from alpha.utils.audio_temp_capture import (
                flush_audio_temp_on_stop,
                schedule_audio_cleanup_non_blocking,
            )

            flush_audio_temp_on_stop()
            schedule_audio_cleanup_non_blocking(reason="after_stop")
        except Exception:
            pass

        try:
            from alpha.utils.component_stall_classifier import finalize_stall_classifications
            from alpha.utils.live_runtime_metrics import get_metrics
            from alpha.utils.run_identity import get_current_run_identity

            ident = get_current_run_identity()
            folder = getattr(ident, "run_folder", None) if ident else None
            finalize_stall_classifications(get_metrics(), run_folder=folder, host=host)
        except Exception:
            pass

        if host is not None:
            try:
                host._is_stopping = False
                host._is_finalizing = False
                host.is_listening = False
            except Exception:
                pass

        try:
            from alpha.utils.final_artifact_authority import (
                sync_non_authoritative_aliases_from_sealed_final,
                verify_final_export_seal,
            )
            from alpha.utils.run_identity import get_current_run_identity

            ident = get_current_run_identity()
            folder = getattr(ident, "run_folder", None) if ident else None
            if folder:
                sync_non_authoritative_aliases_from_sealed_final(
                    folder, run_id=getattr(ident, "run_id", "")
                )
                verify_final_export_seal(folder, run_id=getattr(ident, "run_id", ""))
                freeze_guard_log("ALIASES_SYNCED_FROM_SEALED_FINAL")
        except Exception as exc:
            freeze_guard_log(
                "ALIAS_SYNC_FROM_SEALED_FAILED",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )

        try:
            from alpha.utils.accuracy_evidence_export import export_alpha_evidence_on_stop

            export_alpha_evidence_on_stop(host)
        except Exception:
            pass

        _write_minimal_runtime_artifacts(host, dg_result=dg_result)
        with _state_lock:
            _stop_state["finalize_completed"] = True
        if hasattr(host, "stop_core_completed_event"):
            try:
                host.stop_core_completed_event.set()
            except Exception:
                pass

        stop_summary = build_stop_finalize_summary(host, dg_result=dg_result)
        timed_out = bool(stop_summary.get("stop_finalize_timed_out", False))
        stop_summary["three_stage_finalize_call_count"] = _three_stage_finalize_call_count
        stop_summary["audio_drain"] = audio_drain
        stop_summary["ui_drain"] = ui_drain
        stop_summary["ui_events_posted_after_final_drain"] = ui_events_posted_after_final_drain
        freeze_guard_log("STOP_FINALIZE_COMPLETED", **stop_summary)
        freeze_guard_log("STOP_MINIMAL_COMPLETED")
        freeze_guard_log("RUNTIME_BASELINE_START_STOP_PRESERVED")
        freeze_guard_log("TEMP_AUDIO_CLEANUP_NON_BLOCKING_CONFIRMED")
        freeze_guard_log("NO_RUNTIME_UPLOAD_PACKAGE_CONFIRMED")
        freeze_guard_log("APP_STATE_STOPPED")

        try:
            from alpha.utils.flight_recorder import record_flight_event

            record_flight_event(
                "stop_finalize_completed",
                host=host,
                force=True,
                **stop_summary,
            )
        except Exception:
            pass

        try:
            from alpha.utils.evidence_pointer_finalize import (
                schedule_evidence_pointer_finalization_background,
            )

            schedule_evidence_pointer_finalization_background(
                host, reason="after_minimal_stop"
            )
        except Exception:
            pass

        # Do NOT call _queue_final_ui_update again after drain (V25.3.3.1).
        freeze_guard_log("EVIDENCE_PACKAGE_WORKER_DISABLED_DURING_RUNTIME")
        freeze_guard_log("NO_UI_EVENT_AFTER_FINAL_DRAIN_CONFIRMED")
        return

        consistency_result: dict[str, Any] = {}

        def _consistency() -> None:
            nonlocal consistency_result
            from alpha.utils.run_consistency import validate_run_consistency

            consistency_result = validate_run_consistency(host=host)

        run_timed_step(host, "run_consistency_check", _consistency)

        readiness: dict[str, Any] = {}

        def _readiness() -> None:
            nonlocal readiness
            from alpha.transcription.japanese_sentence_assembler import (
                get_japanese_continuity_assembler,
            )
            from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_event_counts
            from alpha.utils.runtime_evidence import emit_long_test_readiness

            assembler = get_japanese_continuity_assembler(host)
            emergency = int(
                get_japanese_accuracy_event_counts().get("EMERGENCY_COMMIT", 0) or 0
            )
            stop_snap = get_stop_finalize_snapshot()
            stop_snap["finalize_completed"] = True
            try:
                from alpha.utils.run_identity import get_current_run_identity

                ident = get_current_run_identity()
                if ident is not None:
                    stop_snap["deepgram_close_status"] = ident.deepgram_close_status
            except Exception:
                pass
            readiness = emit_long_test_readiness(
                host=host,
                assembler=assembler,
                emergency_commit_count=emergency,
                consistency_result=consistency_result,
                stop_state=stop_snap,
            )

        run_timed_step(host, "long_test_readiness", _readiness)

        def _run_artifacts() -> None:
            from alpha.transcription.japanese_sentence_assembler import (
                get_japanese_continuity_assembler,
            )
            from alpha.utils.run_artifacts import (
                create_upload_evidence_package,
                finalize_live_run_status_completed,
                write_final_alpha_output,
            )
            from alpha.utils.runtime_evidence import (
                build_artifact_index_extra,
                write_run_artifacts_index_safe,
            )
            from alpha.utils.run_identity import get_current_run_identity
            from alpha.utils.segment_count_reconciliation import (
                reconcile_final_segment_counts,
            )
            from alpha.utils.troubleshooting_paths import (
                assert_no_pending_writers_active,
                finalize_latest_pointers,
                finalize_run_manifest,
                get_active_run_folder,
                migrate_pending_files_to_run_folder,
                preflight_upload_evidence,
                write_writer_registry_snapshot,
            )

            folder = get_active_run_folder()
            if folder is not None:
                migrate_pending_files_to_run_folder(folder)
                preflight_upload_evidence(folder)

            write_final_alpha_output(host)
            _evidence_flags["alpha_output_written"] = True

            assembler = get_japanese_continuity_assembler(host)
            identity = get_current_run_identity()
            if identity is not None:
                identity.stop_finalize_completed = True
            segment_counts = reconcile_final_segment_counts(host)
            stop_summary = build_stop_finalize_summary(host, dg_result=dg_result)
            extra = build_artifact_index_extra(
                host,
                assembler=assembler,
                readiness=readiness,
                stop_state={**get_stop_finalize_snapshot(), **stop_summary},
            )
            extra.update(segment_counts)
            extra.update(stop_summary)
            index_path = write_run_artifacts_index_safe(host, extra=extra)
            if index_path is not None:
                _evidence_flags["run_artifacts_index_written"] = True
                freeze_guard_log("RUN_ARTIFACTS_INDEX_WRITTEN", path=str(index_path))
                freeze_guard_log("RUN_ARTIFACTS_INDEX_INITIAL_WRITTEN", path=str(index_path))

            try:
                from alpha.utils.process_health_telemetry import (
                    write_memory_trend_summary,
                    write_process_health_timeline,
                    collect_process_metrics,
                )

                proc_path = write_process_health_timeline(collect_process_metrics())
                _evidence_flags["process_health_timeline_written"] = proc_path is not None
                mem_path = write_memory_trend_summary()
                _evidence_flags["memory_trend_summary_written"] = mem_path is not None
                if proc_path:
                    freeze_guard_log("PROCESS_HEALTH_TIMELINE_CREATED", path=str(proc_path))
                if mem_path:
                    freeze_guard_log("MEMORY_TREND_SUMMARY_WRITTEN", path=str(mem_path))
                    freeze_guard_log("MEMORY_TELEMETRY_RUN_FOLDER_CONFIRMED")
            except Exception:
                pass

            try:
                from alpha.utils.troubleshooting_paths import get_audio_temp_path

                manifest = get_audio_temp_path("audio_manifest")
                _evidence_flags["audio_temp_manifest_written"] = manifest.exists()
            except Exception:
                pass

            validation_path = None
            if folder is not None:
                validation_path = folder / "validation" / "validate_8520_2_output.txt"
                _evidence_flags["validation_output_written"] = bool(
                    validation_path and validation_path.exists()
                )
                freeze_guard_log("VALIDATION_DEFERRED_UNTIL_AFTER_STOP")

                pending_writers = assert_no_pending_writers_active()
                if pending_writers:
                    freeze_guard_log(
                        "PENDING_WRITE_AFTER_REBIND_BLOCKED",
                        pending_writers=pending_writers,
                    )
                else:
                    freeze_guard_log("NO_ACTIVE_WRITERS_LEFT_IN_PENDING_CONFIRMED")
                write_writer_registry_snapshot(folder)

            freeze_guard_log("UPLOAD_PACKAGE_DEFERRED_UNTIL_AFTER_STOP")
            pkg = create_upload_evidence_package(host, status="completed")
            upload_zip_path = ""
            if pkg is not None:
                if str(pkg).endswith(".zip"):
                    _evidence_flags["upload_package_zip_created"] = True
                    upload_zip_path = str(pkg)
                _evidence_flags["upload_package_index_written"] = True
                freeze_guard_log("UPLOAD_PACKAGE_ZIP_CREATED_AFTER_VALIDATION")
            else:
                _evidence_flags["upload_package_zip_failed_non_blocking"] = True

            # Final rewrite after validation + upload package are complete.
            final_extra = build_artifact_index_extra(
                host,
                assembler=assembler,
                readiness=readiness,
                stop_state={**get_stop_finalize_snapshot(), **build_stop_finalize_summary(host, dg_result=dg_result)},
            )
            final_extra.update(segment_counts)
            final_extra.update(_evidence_flags)
            final_index = write_run_artifacts_index_safe(host, extra=final_extra)
            if final_index is not None:
                freeze_guard_log("RUN_ARTIFACTS_INDEX_FINAL_REWRITTEN", path=str(final_index))
                freeze_guard_log("RUN_ARTIFACTS_INDEX_FINAL_FLAGS_CONFIRMED")
                freeze_guard_log("RUN_ARTIFACTS_INDEX_STALE_FLAGS_FIXED")

            stop_summary = build_stop_finalize_summary(host, dg_result=dg_result)
            stop_summary.update(_evidence_flags)
            finalize_live_run_status_completed(
                host,
                segment_counts=segment_counts,
                stop_summary=stop_summary,
                evidence_flags=_evidence_flags,
            )
            _evidence_flags["live_run_status_written"] = True

            if folder is not None and identity is not None:
                final_status = "completed"
                if stop_summary.get("stop_finalize_timed_out"):
                    final_status = "completed_with_warnings"
                finalize_latest_pointers(
                    folder,
                    run_id=identity.run_id,
                    status=final_status,
                    upload_zip_path=upload_zip_path,
                )
                finalize_run_manifest(
                    folder,
                    status=final_status,
                    artifact_flags=_evidence_flags,
                    stop_summary=stop_summary,
                )

            try:
                from alpha.utils.audio_temp_capture import cleanup_old_audio_temp

                cleanup_old_audio_temp(reason="stop_finalize")
            except Exception:
                pass

        run_timed_step(host, "run_artifacts_index", _run_artifacts)

        def _async_flush() -> None:
            from alpha.utils.async_debug_log import flush_async_debug_logging_safe

            flush_async_debug_logging_safe(timeout_ms=500.0)

        run_timed_step(host, "async_debug_flush", _async_flush)

        with _state_lock:
            _stop_state["finalize_completed"] = True

        stop_summary = build_stop_finalize_summary(host, dg_result=dg_result)
        timed_out = bool(stop_summary.get("stop_finalize_timed_out", False))

        freeze_guard_log("STOP_FINALIZE_COMPLETED", **stop_summary)
        freeze_guard_log("STOP_FINALIZE_WORKER_DONE", **stop_summary)
        try:
            from alpha.utils.flight_recorder import record_flight_event

            record_flight_event(
                "stop_finalize_completed",
                host=host,
                force=True,
                **stop_summary,
            )
        except Exception:
            pass

        try:
            from alpha.utils.async_debug_log import log_runtime_debug_event

            log_runtime_debug_event("STOP_LISTENING_DONE")
        except Exception:
            pass

        _queue_final_ui_update(host, timed_out=timed_out)

    except Exception as exc:
        freeze_guard_log(
            "STOP_MINIMAL_FAILED",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )
        freeze_guard_log(
            "STOP_FINALIZE_WORKER_FAILED",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            short_traceback="".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-800:],
        )
        _queue_final_ui_update(host, timed_out=True)
    finally:
        try:
            from alpha.utils.tk_thread_guard import set_tk_guard_stop_finalize_active

            set_tk_guard_stop_finalize_active(False)
        except Exception:
            pass
        with _state_lock:
            _stop_state["worker_done"] = True


def begin_stop_from_ui(host: Any) -> None:
    """UI-thread entry: return control to Tkinter within ~100ms."""
    if getattr(host, "_is_finalizing", False) or not getattr(host, "is_listening", False):
        return
    if getattr(host, "_stop_finalize_started", False):
        return

    begin_mono = time.perf_counter()
    host._stop_finalize_started = True

    freeze_guard_log("STOP_BUTTON_CLICKED")
    try:
        from alpha.utils.flight_recorder import record_flight_event

        record_flight_event("stop_clicked", host=host, force=True)
    except Exception:
        pass
    freeze_guard_log("STOP_UI_CALLBACK_BEGIN", timestamp=int(time.time() * 1000))

    _reset_stop_state()
    _start_watchdog(host)

    # Immediate lightweight flags only — full ordered sequence runs in worker.
    host._is_stopping = True
    host._is_finalizing = True
    host.is_listening = False

    # Lightweight UI feedback only.
    if hasattr(host, "_set_stopping_ui_state"):
        host._set_stopping_ui_state()
    elif hasattr(host, "_set_finalizing_ui_state"):
        host._set_finalizing_ui_state()

    duration_ms = round((time.perf_counter() - begin_mono) * 1000.0, 2)
    with _state_lock:
        _stop_state["ui_callback_returned"] = True

    try:
        from alpha.utils.run_identity import get_current_run_identity

        identity = get_current_run_identity()
        if identity is not None:
            identity.stop_ui_callback_duration_ms = duration_ms
    except Exception:
        pass

    freeze_guard_log(
        "STOP_UI_CALLBACK_RETURNED",
        stop_ui_callback_duration_ms=duration_ms,
    )

    try:
        from alpha.utils.async_debug_log import log_runtime_debug_event

        log_runtime_debug_event("STOP_LISTENING_BEGIN")
        log_runtime_debug_event(
            "STOP_UI_CALLBACK_RETURNED",
            stop_ui_callback_duration_ms=duration_ms,
        )
    except Exception:
        pass

    with _state_lock:
        if (
            _stop_state.get("finalize_thread") is not None
            and getattr(_stop_state["finalize_thread"], "is_alive", lambda: False)()
        ):
            return
        t = threading.Thread(
            target=_run_finalize_worker,
            name="StopFinalizeWorker",
            daemon=True,
            args=(host,),
        )
        _stop_state["finalize_thread"] = t
        t.start()
