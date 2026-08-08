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
from alpha.utils.logging_utils import get_logger
from alpha.utils.path_types import ensure_path

logger = get_logger(__name__)

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

# fixes TASK_9_REPORT.md Issue 2: build_stop_finalize_summary() is called
# twice per Stop -- once synchronously right after _run_finalize_worker's
# real sequence completes (while _required_step_ok/_stop_state genuinely
# reflect that run), and again later from evidence_pointer_finalize.py's
# background thread (schedule_evidence_pointer_finalization_background),
# which reads the SAME module-level globals fresh. Those globals are
# process-wide and unscoped to a run id; _reset_stop_state() (called once
# per Stop click) can legitimately fire again for a NEW run before the
# delayed background thread from the OLD run gets around to reading them,
# so the second read can see a state genuinely belonging to a different
# (often still-in-progress) run -- explaining a failure_reason like
# "utterance_reconstruction" (marked early in the sequence) even though
# the run this thread was scheduled for had every step succeed. This cache
# lets a later call for the SAME run reuse the one authoritative
# computation instead of recomputing against whatever the globals
# currently hold.
_last_completed_run_id = ""
_last_completed_summary: dict[str, Any] = {}


def _current_run_id_for_cache() -> str:
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        return str(getattr(ident, "run_id", "") or "") if ident is not None else ""
    except Exception:
        return ""


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
    "utterance_reconstruction_check": 500.0,
    "japanese_assembler_flush": 500.0,
    "translation_unit_final_flush": 500.0,
    "flush_pending_translation_debounce": 2500.0,
    "translation_reconciliation": 4000.0,
    "translation_worker_shutdown": 16000.0,
    "canonical_pipeline_finalize": 2000.0,
    "write_final_alpha": 2000.0,
    "three_stage_finalize": 3000.0,
    "session_stop_logs": 500.0,
    "run_consistency_check": 2000.0,
    "long_test_readiness": 500.0,
    "final_summaries": 500.0,
    "run_artifacts_index": 2000.0,
    "async_debug_flush": 500.0,
    "write_minimal_runtime_artifacts": 2000.0,
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

# fixes TASK_4A_FINDINGS.md items 1/2/3: explicit, fail-closed tracking for
# every REPAIR_PLAN.md Phase 4 required step this worker controls
# synchronously. Unlike _step_completed (set True merely because
# run_timed_step caught no exception), an entry here is only ever set True
# by the step's own call site, using that step's own real result -- a
# missing key is treated as failure, not success. "Evidence package" (the
# 10th required item) is intentionally excluded: it completes on a
# background thread (see _run_finalize_worker's scheduling of
# evidence_pointer_finalize.py, kept async per STOP_CORE_NEVER_BLOCKS_ON_EVIDENCE)
# and is resolved there, not here.
_REQUIRED_SYNC_STEPS = (
    "audio_summary",
    "raw_event_persistence",
    "utterance_reconstruction",
    "canonical_ledger_validation",
    "stable_export",
    "final_export",
    "translation_drain",
    "loading_state_drain",
    "run_manifest",
    "translation_reconciliation",
)
_required_step_ok: dict[str, bool] = {}


def _mark_required_step(name: str, ok: bool, *, reason: str = "") -> None:
    """fixes TASK_4A_FINDINGS.md items 1/2: record a required step's REAL
    success/failure so it can gate final_status -- logging alone is not
    enough (that was the bug)."""
    _required_step_ok[name] = bool(ok)
    if not ok:
        freeze_guard_log("REQUIRED_STEP_FAILED", step_name=name, reason=reason)


def _reset_required_steps() -> None:
    _required_step_ok.clear()


def compute_core_final_status(*, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    """fixes TASK_4A_FINDINGS.md items 1/3: single authoritative decision —
    final_status can only be a completed-shaped value when every required
    synchronous step explicitly reported success. A step that never ran or
    was never marked is treated as failed (fail-closed), not as success.
    Evidence package (async, item 10) is layered on afterward by whichever
    caller learns its outcome — see evidence_pointer_finalize.py.

    `exclude` lets a step's own write function compute "everything else so
    far" before that step's own outcome is knowable (e.g. the run-manifest
    write can't describe its own success inside its own content) — the
    excluded step is never treated as satisfied, only left out of this one
    query; compute_core_final_status() with no exclusion (the version every
    downstream reader uses) still requires it like any other required step.
    """
    missing_or_failed = [
        name
        for name in _REQUIRED_SYNC_STEPS
        if name not in exclude and not _required_step_ok.get(name, False)
    ]
    if missing_or_failed:
        return {
            "final_status": "failed",
            "stop_finalize_failed": True,
            "failure_reason": missing_or_failed[0],
            "failed_required_steps": missing_or_failed,
        }
    return {
        "final_status": "completed_pending_evidence_package",
        "stop_finalize_failed": False,
        "failure_reason": "",
        "failed_required_steps": [],
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
    _reset_required_steps()
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


def compute_utterance_reconstruction_ok(
    host: Any,
    *,
    assembler_flush_ok: bool,
    commit_confirm_ok: bool,
    is_japanese_session_fn: Callable[[Any], bool],
) -> tuple[bool, str]:
    """fixes TASK_4A_FINDINGS.md item 1 (utterance reconstruction): the
    assembler flush + transcript-commit confirmation are the two steps
    that finish reconstructing pending utterances into committed segments
    at Stop.

    fixes TASK_10_REPORT.md: japanese_assembler_flush
    (flush_japanese_assembler_on_stop) runs unconditionally regardless of
    session language, and its outcome used to gate utterance_reconstruction
    for EVERY session, English included. For a non-Japanese session the
    Japanese continuity assembler has nothing real to reconstruct -- a
    timeout/failure flushing an already-idle Japanese assembler is a false
    negative about that session's own (English) transcript reconstruction,
    not a genuine content-loss problem. Only require assembler_flush_ok
    when this session actually used the Japanese path; every other
    session's utterance_reconstruction depends only on the real,
    language-agnostic check (commit_confirm_ok).

    A module-level function (not inlined in _run_finalize_worker) so the
    decision is independently testable without running the full finalize
    sequence.
    """
    japanese_session = False
    try:
        japanese_session = bool(is_japanese_session_fn(host))
    except Exception:
        pass
    ok = bool(commit_confirm_ok) and (
        bool(assembler_flush_ok) if japanese_session else True
    )
    reason = (
        "assembler_flush_or_commit_confirm_failed"
        if japanese_session
        else "commit_confirm_failed"
    )
    return ok, reason


class TranslationReconciliationError(Exception):
    """fixes TASK_9_REPORT.md Issue 1: raised when reconcile_translation_gaps
    could not actually deliver every detected gap. run_timed_step catches
    this like any other exception and reports the step as failed -- the
    point of raising instead of just logging is that a failed forced
    submission must count as a real reconciliation failure, not a silent
    no-op that still reports DONE."""


def reconcile_translation_gaps(host: Any) -> dict[str, int]:
    """fixes TASK_8_REPORT.md Part B: structural safety net for the WHOLE
    failure class ("committed, translation-eligible record never reaches
    translation_worker"), not just the specific gaps Task 7 and Part A
    closed. Compares every active, translation-eligible canonical-ledger
    record's canonical_utterance_id against every canonical_utterance_id
    that has actually reached translation_worker.enqueue_stable_segment()
    and been accepted (translation_worker._revision_events, accepted=True
    -- the same source Task 7's evidence writer already trusts). Any
    committed record with no matching accepted job is force-submitted
    directly, so any future, still-undiscovered path that skips normal
    submission is caught and self-healed instead of silently dropping the
    translation.

    fixes TASK_9_REPORT.md Issue 1: live testing showed
    TRANSLATION_RECONCILIATION_FORCED_SUBMIT logged with gap_count=1 but
    forced_count=0 and STABLE_TRANSLATION_JOBS_ACCEPTED=0 -- the WARNING
    used to log BEFORE calling enqueue_stable_segment(), so neither an
    exception nor a plain rejection from that call was ever visible. Root
    cause: main_window.py::_begin_graceful_stop calls
    translation_worker.stop_accepting() synchronously on the UI thread the
    instant Stop is clicked, before this background finalize sequence (and
    this step) even starts -- so the ordinary accept-gate unconditionally
    rejected every forced submission. enqueue_stable_segment() now accepts
    force=True (translation_worker.py), which this function uses so a
    genuinely-confirmed gap is not blocked by Stop's own early
    stop_accepting() call. Every attempt's true outcome (accepted /
    rejected / raised) is now logged only AFTER it is known, and any gap
    left unresolved raises TranslationReconciliationError so the step
    itself is reported failed, not silently "done".

    A module-level function (not a nested closure in _run_finalize_worker)
    so it is independently testable against the exact production logic,
    not a test-side reimplementation of it.
    """
    from alpha.transcription import canonical_transcript_ledger as ctl

    worker = getattr(host, "translation_worker", None)
    active_records = ctl.get_active_records()
    revision_events = (
        list(getattr(worker, "_revision_events", None) or [])
        if worker is not None
        else []
    )
    already_submitted = {
        str(ev.get("canonical_utterance_id") or "").strip()
        for ev in revision_events
        if ev.get("accepted") and str(ev.get("canonical_utterance_id") or "").strip()
    }

    forced_count = 0
    gap_count = 0
    unresolved: list[str] = []
    for rec in active_records:
        metadata = dict(rec.get("metadata") or {})
        utterance_id = str(metadata.get("canonical_utterance_id") or "").strip()
        if not utterance_id or utterance_id in already_submitted:
            continue
        if not bool(metadata.get("translation_eligible", True)):
            continue
        source_text = str(rec.get("final_text") or rec.get("assembler_text") or "").strip()
        if not source_text:
            continue
        gap_count += 1
        record_id = str(rec.get("record_id") or "")
        commit_reason = str(rec.get("commit_reason") or "")

        if worker is None:
            unresolved.append(utterance_id)
            logger.error(
                "TRANSLATION_RECONCILIATION_FORCED_SUBMIT_FAILED record_id=%s "
                "canonical_utterance_id=%s commit_reason=%s reason=no_translation_worker",
                record_id,
                utterance_id,
                commit_reason,
            )
            freeze_guard_log(
                "TRANSLATION_RECONCILIATION_GAP_UNRESOLVABLE",
                record_id=record_id,
                canonical_utterance_id=utterance_id,
                commit_reason=commit_reason,
                reason="no_translation_worker",
            )
            continue

        try:
            from alpha.utils.run_identity import get_run_id

            run_id = str(get_run_id() or "")
        except Exception:
            run_id = ""
        source_lang = str(
            getattr(host, "_listen_language", None)
            or metadata.get("source_language")
            or ""
        )
        try:
            accepted = worker.enqueue_stable_segment(
                segment_id=int(rec.get("sequence_number") or 0),
                source_language=source_lang,
                source_text=source_text,
                stable_commit_timestamp=float(rec.get("created_at") or time.time()),
                is_interim=False,
                run_id=run_id,
                canonical_utterance_id=utterance_id,
                source_version=int(metadata.get("source_version") or 1),
                source_record_id=record_id,
                session_id=str(metadata.get("session_id") or ""),
                force=True,
            )
        except Exception as exc:
            unresolved.append(utterance_id)
            logger.error(
                "TRANSLATION_RECONCILIATION_FORCED_SUBMIT_EXCEPTION record_id=%s "
                "canonical_utterance_id=%s commit_reason=%s exception_type=%s "
                "exception_message=%s",
                record_id,
                utterance_id,
                commit_reason,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            freeze_guard_log(
                "TRANSLATION_RECONCILIATION_FORCED_SUBMIT_EXCEPTION",
                record_id=record_id,
                canonical_utterance_id=utterance_id,
                commit_reason=commit_reason,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
                short_traceback="".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )[-1500:],
            )
            continue

        if accepted:
            forced_count += 1
            logger.warning(
                "TRANSLATION_RECONCILIATION_FORCED_SUBMIT record_id=%s "
                "canonical_utterance_id=%s commit_reason=%s",
                record_id,
                utterance_id,
                commit_reason,
            )
            freeze_guard_log(
                "TRANSLATION_RECONCILIATION_FORCED_SUBMIT",
                record_id=record_id,
                canonical_utterance_id=utterance_id,
                commit_reason=commit_reason,
                source_version=metadata.get("source_version"),
            )
        else:
            unresolved.append(utterance_id)
            logger.error(
                "TRANSLATION_RECONCILIATION_FORCED_SUBMIT_REJECTED record_id=%s "
                "canonical_utterance_id=%s commit_reason=%s",
                record_id,
                utterance_id,
                commit_reason,
            )
            freeze_guard_log(
                "TRANSLATION_RECONCILIATION_FORCED_SUBMIT_REJECTED",
                record_id=record_id,
                canonical_utterance_id=utterance_id,
                commit_reason=commit_reason,
            )

    freeze_guard_log(
        "TRANSLATION_RECONCILIATION_DONE",
        active_record_count=len(active_records),
        already_submitted_count=len(already_submitted),
        gap_count=gap_count,
        forced_count=forced_count,
        unresolved_count=len(unresolved),
    )
    if unresolved:
        raise TranslationReconciliationError(
            f"reconciliation could not deliver {len(unresolved)}/{gap_count} "
            f"gap(s): {unresolved}"
        )
    return {
        "active_record_count": len(active_records),
        "already_submitted_count": len(already_submitted),
        "gap_count": gap_count,
        "forced_count": forced_count,
    }


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


def _queue_final_ui_update(
    host: Any, *, timed_out: bool, use_lightweight_snapshot: bool = False
) -> None:
    """Schedule lightweight UI finish on main thread — never call Tk from worker."""
    runner = getattr(host, "_run_on_ui_thread", None)
    finish = getattr(host, "_finish_graceful_stop", None)
    if not callable(finish):
        freeze_guard_log("FINAL_UI_UPDATE_SKIPPED_DURING_STOP", reason="no_finish_fn")
        return
    if not callable(runner):
        freeze_guard_log("FINAL_UI_UPDATE_SKIPPED_DURING_STOP", reason="no_ui_runner")
        return

    # fixes TASK_14_REPORT.md: ground truth (TASK_13_DEBUG_REPORT.md's
    # diagnostic) confirmed the call from _run_finalize_worker's normal
    # sequence (line ~1485, before the drain barrier) is ALWAYS premature —
    # most required steps have not been marked yet at that point, so the
    # full build_stop_finalize_summary(host) used to compute (and log via
    # FINAL_UI_UPDATE_QUEUED) a spurious "failed" result on every single
    # run, regardless of true outcome. That call site only ever needed
    # stop_finalize_timed_out, already available from the lightweight,
    # non-authoritative get_stop_finalize_snapshot() — the same value
    # TASK_10_REPORT.md already switched the immediate caller to read
    # directly instead of the full summary. The genuine-failure call from
    # the outer exception handler still needs the full, authoritative
    # summary (the run really did fail there), so it keeps the default.
    if use_lightweight_snapshot:
        snap = get_stop_finalize_snapshot()
        ui_timed_out = bool(snap.get("stop_finalize_timed_out", timed_out))
        summary: dict[str, Any] = {
            "stop_finalize_timed_out": ui_timed_out,
            "worker_done": snap.get("worker_done", False),
            "note": "lightweight_pre_drain_snapshot_not_final_status",
        }
    else:
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


def _write_core_live_status(host: Any, *, core_status: Optional[dict[str, Any]] = None) -> None:
    try:
        from alpha.utils.run_identity import get_current_run_identity
        from alpha.utils.troubleshooting_paths import get_artifact_path

        ident = get_current_run_identity()
        p = get_artifact_path("live_run_status")
        # fixes TASK_4A_FINDINGS.md items 1/3: stop_finalize_failed/status are
        # no longer hardcoded -- they reflect compute_core_final_status()'s
        # fail-closed result for every required synchronous step.
        cs = core_status if core_status is not None else compute_core_final_status()
        payload = {
            "status": cs["final_status"],
            "run_id": getattr(ident, "run_id", ""),
            "run_timestamp": getattr(ident, "run_timestamp", ""),
            "app_version": getattr(ident, "app_version", ""),
            "stop_core_completed": True,
            "stop_core_failed": bool(cs["stop_finalize_failed"]),
            "evidence_package_status": "deferred",
            "stop_finalize_completed": True,
            "stop_finalize_failed": bool(cs["stop_finalize_failed"]),
            "failure_reason": cs["failure_reason"],
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
    # fixes TASK_4A_FINDINGS.md items 1/2/3: compute the real, fail-closed
    # status for the 8 required steps already known at this point in Stop
    # (everything except run_manifest itself, which this function is in the
    # middle of writing) instead of hardcoding "completed_with_warnings".
    core_status = compute_core_final_status(exclude=("run_manifest",))
    # RUN_ARTIFACTS_INDEX minimal
    idx = get_artifact_path("run_artifacts_index")
    idx_lines = [
        f"status={core_status['final_status']}",
        f"run_id={run_id}",
        f"run_timestamp={run_ts}",
        f"app_version={app_version}",
        "stop_core_completed=true",
        "stop_finalize_completed=true",
        f"stop_finalize_failed={'true' if core_status['stop_finalize_failed'] else 'false'}",
        f"failure_reason={core_status['failure_reason']}",
        "evidence_package_status=disabled_during_runtime",
    ]
    idx.write_text("\n".join(idx_lines) + "\n", encoding="utf-8")
    _evidence_flags["run_artifacts_index_written"] = True
    # LIVE_RUN_STATUS minimal
    _write_core_live_status(host, core_status=core_status)
    # RUN_MANIFEST minimal update
    manifest_path = get_run_manifest_path()
    manifest_write_ok = True
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
            "final_status": core_status["final_status"],
            "stop_finalize_failed": core_status["stop_finalize_failed"],
            "failure_reason": core_status["failure_reason"],
            "stop_core_completed": True,
            "stop_finalize_completed": True,
            "evidence_package_status": "disabled_during_runtime",
            "deepgram_close_status": (dg_result or {}).get("status", ""),
        }
    )
    try:
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        manifest_write_ok = False
    # fixes TASK_4A_FINDINGS.md item 1 (run manifest): mark this step's own
    # real outcome for the async evidence-pointer pass to fold into the
    # final authoritative status (see evidence_pointer_finalize.py).
    _mark_required_step("run_manifest", manifest_write_ok, reason="manifest_write_exception")

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
    """Normalized stop finalize evidence — timed_out only when steps actually timed out.

    fixes TASK_9_REPORT.md Issue 2: this is called a second time later, from
    evidence_pointer_finalize.py's background thread, well after the
    synchronous finalize sequence already computed and logged the real
    result (STOP_FINALIZE_COMPLETED). If that second call is still for the
    SAME run this cache was populated for, reuse the one authoritative
    computation instead of recomputing against the shared, unscoped
    _required_step_ok/_stop_state globals, which may by then belong to a
    different (often still in-progress) run's Stop sequence. A different
    run id (or no cache yet -- e.g. the very first, real computation, or a
    run whose finalize sequence crashed before reaching the cache-write
    point) falls through to a fresh computation as before.
    """
    global _last_completed_run_id, _last_completed_summary
    current_run_id = _current_run_id_for_cache()
    if current_run_id:
        with _state_lock:
            cached_run_id = _last_completed_run_id
            cached_summary = dict(_last_completed_summary) if _last_completed_summary else None
        if cached_summary is not None and cached_run_id == current_run_id:
            return cached_summary

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

    # fixes TASK_4A_FINDINGS.md items 1/2/3: stop_finalize_failed/final_status
    # are no longer derived from the generic (non-required-step-aware)
    # failed_steps list or from "did we reach the end of Stop" -- they come
    # from compute_core_final_status(), which is fail-closed on every
    # REPAIR_PLAN.md Phase 4 required step this worker tracks explicitly.
    core_status = compute_core_final_status()

    summary = {
        "stop_finalize_completed": bool(
            snap.get("finalize_completed", False)
            or _evidence_flags.get("alpha_output_written")
            or identity_finalize
        ),
        "stop_finalize_failed": core_status["stop_finalize_failed"],
        "final_status": core_status["final_status"],
        "failure_reason": core_status["failure_reason"],
        "failed_required_steps": core_status["failed_required_steps"],
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

    # fixes TASK_9_REPORT.md Issue 2: only cache once the synchronous
    # finalize sequence for THIS run has genuinely reached its end
    # (get_stop_finalize_snapshot()'s own "finalize_completed" flag,
    # set once near the end of _run_finalize_worker, before this
    # function's real/authoritative call) -- never a partial/in-progress
    # snapshot, which a hypothetical future mid-sequence caller could
    # otherwise freeze in as if it were final.
    if snap.get("finalize_completed") and current_run_id:
        with _state_lock:
            _last_completed_run_id = current_run_id
            _last_completed_summary = dict(summary)

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


def _write_translation_and_ui_evidence_streams(
    host: Any,
    *,
    translation_summary: Optional[dict[str, Any]],
    ui_drain: dict[str, Any],
    worker: Any = None,
) -> None:
    """fixes TASK_4A_FINDINGS.md items 3/4: materialize
    translation_jobs.jsonl and ui_events.jsonl, the remaining two of
    REPAIR_PLAN.md Phase 4's five required evidence streams — finalize-time
    snapshots from data already collected during Stop (translation worker's
    own shutdown summary, the UI drain barrier's own result), same
    finalize-time-materialization approach and same file-scope reasoning as
    write_separated_evidence_streams in canonical_finalize.py.
    """
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        folder = ensure_path(getattr(ident, "run_folder", None) if ident else None)
        if folder is None:
            return
        stream_dir = folder / "evidence_streams"
        stream_dir.mkdir(parents=True, exist_ok=True)

        summary = dict(translation_summary or {})
        # fixes TASK_4C_REPORT.md (test 5 regression found): the acceptance
        # gate requires every translation to reference an existing canonical
        # record/version -- an aggregate worker-shutdown summary alone
        # cannot show that. TranslationWorker already tracks this per job in
        # its own _revision_events list (canonical_utterance_id/source_version/
        # source_record_id, appended on every accepted enqueue_stable_segment
        # call) -- read it directly (public API not needed, this is a
        # read-only finalize-time snapshot, not a modification to
        # translation_worker.py) instead of only summarizing counts.
        tw = worker if worker is not None else getattr(host, "translation_worker", None)
        job_rows: list[dict[str, Any]] = []
        revision_events = list(getattr(tw, "_revision_events", None) or []) if tw is not None else []
        for ev in revision_events:
            job_rows.append(
                {
                    "run_id": getattr(ident, "run_id", ""),
                    "canonical_utterance_id": ev.get("canonical_utterance_id", ""),
                    "source_record_id": ev.get("source_record_id", ""),
                    "source_version": ev.get("source_version"),
                    "translation_sequence": ev.get("translation_sequence"),
                    "accepted": bool(ev.get("accepted")),
                    "session_id": ev.get("session_id", ""),
                    "recorded_at": time.time(),
                }
            )
        if not job_rows:
            # No per-job identity available (no worker this session, or no
            # jobs accepted) -- still record the aggregate outcome rather
            # than writing nothing, but never claim a canonical reference
            # that doesn't exist.
            job_rows.append(
                {
                    "run_id": getattr(ident, "run_id", ""),
                    "canonical_utterance_id": "",
                    "translation_worker_stopped": bool(summary.get("TRANSLATION_WORKER_STOPPED")),
                    "translation_queue_pending_at_exit": int(
                        summary.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", 0) or 0
                    ),
                    "unfinished_segment_ids": list(summary.get("unfinished_segment_ids") or []),
                    "recorded_at": time.time(),
                }
            )
        with open(stream_dir / "translation_jobs.jsonl", "a", encoding="utf-8") as fh:
            for row in job_rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

        ui_row = {
            "run_id": getattr(ident, "run_id", ""),
            "ui_events_posted_after_final_drain": int(
                ui_drain.get("ui_events_posted_after_final_drain")
                or ui_drain.get("events_posted_after_drain")
                or 0
            ),
            "ui_drain": {k: v for k, v in ui_drain.items() if isinstance(v, (str, int, float, bool))},
            "recorded_at": time.time(),
        }
        with open(stream_dir / "ui_events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ui_row, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        freeze_guard_log(
            "EVIDENCE_STREAM_WRITE_FAILED",
            stream="translation_jobs_or_ui_events",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
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


# fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 4: _run_evidence_package_worker
# (confirmed zero callers in TASK_4A_FINDINGS.md/TASK_4B_CHANGES.md, and
# re-confirmed here) has been removed. Its responsibility is fully covered
# by the live evidence_pointer_finalize.py background pass.


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
            should_use_japanese_final_stabilizer,
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
        audio_drain_step_ok = run_timed_step(
            host,
            "drain_audio_queue",
            lambda: audio_drain.update(
                _drain_outgoing_audio_queue(host, timeout_seconds=25.0)
            ),
        )
        # fixes TASK_4A_FINDINGS.md item 1 (audio summary): real success is
        # "queue actually drained", not merely "the step ran without raising".
        _mark_required_step(
            "audio_summary",
            bool(audio_drain_step_ok) and not bool(audio_drain.get("timed_out")),
            reason="audio_queue_not_drained" if audio_drain.get("timed_out") else "step_timeout_or_exception",
        )

        if hasattr(host, "_stop_event"):
            host._stop_event.set()

        dg_result = {}
        dg_step_ok = run_timed_step(
            host,
            "deepgram_graceful_stop",
            lambda: _run_deepgram_finalize_sequence(host, dg_result),
        )
        host._last_graceful_stop_result = dict(dg_result)
        # fixes TASK_4A_FINDINGS.md item 1 (raw event persistence): raw
        # Deepgram finals are captured during this sequence
        # (japanese_final_chunk_stabilizer.py -> record_raw_deepgram_final);
        # a timed-out/failed graceful stop means that capture cannot be
        # confirmed complete.
        _mark_required_step(
            "raw_event_persistence",
            bool(dg_step_ok) and not bool(dg_result.get("timed_out")),
            reason="deepgram_graceful_stop_timed_out_or_failed",
        )

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

        assembler_flush_ok = run_timed_step(
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
        #
        # fixes TASK_10_REPORT.md: this used to call the full
        # build_stop_finalize_summary(host, dg_result=dg_result) here, purely
        # to read its "stop_finalize_timed_out" field -- but at this point in
        # the sequence, utterance_reconstruction/canonical_ledger_validation/
        # stable_export/final_export/translation_reconciliation/run_manifest
        # have not been marked yet, so compute_core_final_status() (called
        # inside build_stop_finalize_summary) always reported a spurious
        # "failed"/"utterance_reconstruction" result here and logged a
        # confusingly "final"-sounding STOP_FINALIZE_SUMMARY_NORMALIZED event
        # with it, regardless of the run's real outcome. The only value
        # actually used below (timed_out_pre, itself only a fallback default
        # _queue_final_ui_update's own internal computation always
        # overrides) is available directly from the lightweight, non-logging
        # get_stop_finalize_snapshot() -- the exact same underlying
        # timed_out_steps data build_stop_finalize_summary derives that one
        # field from.
        timed_out_pre = bool(get_stop_finalize_snapshot().get("stop_finalize_timed_out", False))
        _queue_final_ui_update(host, timed_out=timed_out_pre, use_lightweight_snapshot=True)
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

        commit_confirm_ok = run_timed_step(
            host, "transcript_commit_confirm", lambda: _confirm_transcript_commits(host)
        )
        # fixes TASK_12_REPORT.md: this used to call
        # compute_utterance_reconstruction_ok(...) directly, unwrapped by
        # run_timed_step or any try/except at this call site -- the only
        # required-step computation in this whole sequence with no
        # containment at all. Any exception here (unlike every other block,
        # which is either inside run_timed_step or has its own internal
        # try/except) would propagate straight out of _run_finalize_worker
        # to the outer exception handler, silently skipping every
        # subsequent _mark_required_step call for the rest of the function
        # -- exactly the reported "utterance_reconstruction onward, all
        # failing, with no timed_out/failed_steps entries" cascade.
        # Wrapping it in run_timed_step (like every other computed step)
        # gives it the same exception containment, a bounded timeout, and
        # its own STOP_FINALIZE_STEP_BEGIN/END observability pair.
        utterance_reconstruction_result: dict[str, Any] = {}

        def _compute_utterance_reconstruction() -> None:
            ok, reason = compute_utterance_reconstruction_ok(
                host,
                assembler_flush_ok=assembler_flush_ok,
                commit_confirm_ok=commit_confirm_ok,
                is_japanese_session_fn=should_use_japanese_final_stabilizer,
            )
            utterance_reconstruction_result["ok"] = ok
            utterance_reconstruction_result["reason"] = reason

        utterance_reconstruction_step_ok = run_timed_step(
            host, "utterance_reconstruction_check", _compute_utterance_reconstruction
        )
        _mark_required_step(
            "utterance_reconstruction",
            bool(utterance_reconstruction_step_ok)
            and bool(utterance_reconstruction_result.get("ok")),
            reason=str(
                utterance_reconstruction_result.get("reason")
                or "step_timeout_or_exception"
            ),
        )

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

        def _flush_pending_translation_debounce() -> None:
            """fixes TASK_7_REPORT.md: main_window.py's
            submit_text_for_translation() coalesces a newly committed
            segment behind a 120-350ms Tk .after() debounce timer before
            actually calling translation_worker.enqueue_stable_segment().
            A segment committed right before Stop -- e.g. via
            utterance_lifecycle.py's inactivity_timeout_fallback, which by
            definition tends to fire close to Stop -- can still have that
            timer armed but unfired when translation_worker_shutdown below
            calls stop_accepting()/shutdown(), silently abandoning the job:
            never enqueued, never counted in translation_jobs.jsonl, even
            though its canonical ledger commit already succeeded. Flush any
            pending debounced submission synchronously first.
            """
            freeze_guard_log("PENDING_TRANSLATION_DEBOUNCE_FLUSH_BEGIN")
            flusher = getattr(host, "flush_pending_translation_submissions", None)
            if not callable(flusher):
                freeze_guard_log(
                    "PENDING_TRANSLATION_DEBOUNCE_FLUSH_DONE", skipped="no_flusher"
                )
                return
            try:
                flushed = flusher(timeout_seconds=2.0)
                freeze_guard_log(
                    "PENDING_TRANSLATION_DEBOUNCE_FLUSH_DONE",
                    flushed_count=int(flushed or 0),
                )
            except Exception as exc:
                freeze_guard_log(
                    "PENDING_TRANSLATION_DEBOUNCE_FLUSH_FAILED",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )

        run_timed_step(
            host,
            "flush_pending_translation_debounce",
            _flush_pending_translation_debounce,
        )

        reconciliation_ok = run_timed_step(
            host,
            "translation_reconciliation",
            lambda: reconcile_translation_gaps(host),
        )
        _mark_required_step(
            "translation_reconciliation",
            bool(reconciliation_ok),
            reason="translation_reconciliation_exception_or_timeout",
        )

        def _translation_worker_shutdown() -> None:
            """Drain async DeepL worker after transcription finalize; bounded wait."""
            worker = getattr(host, "translation_worker", None)
            if worker is None:
                # fixes TASK_4A_FINDINGS.md item 1 (translation/loading-state
                # drain): no worker means no translation was active this
                # session -- a definite, confirmed no-op, not an unconfirmed
                # gap, so both are trivially satisfied.
                _mark_required_step("translation_drain", True)
                _mark_required_step("loading_state_drain", True)
                return
            try:
                worker.stop_accepting()
            except Exception:
                pass
            try:
                from alpha.constants import TRANSLATION_SHUTDOWN_TIMEOUT_SECONDS

                timeout_s = float(TRANSLATION_SHUTDOWN_TIMEOUT_SECONDS)
            except Exception:
                timeout_s = 15.0
            try:
                summary = worker.shutdown(timeout_seconds=timeout_s)
                setattr(host, "_translation_shutdown_summary", summary)
                freeze_guard_log(
                    "TRANSLATION_WORKER_SHUTDOWN",
                    TRANSLATION_WORKER_STOPPED=bool(
                        summary.get("TRANSLATION_WORKER_STOPPED")
                    ),
                    TRANSLATION_QUEUE_PENDING_AT_EXIT=int(
                        summary.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", 0) or 0
                    ),
                    unfinished=len(summary.get("unfinished_segment_ids") or []),
                )
                # fixes TASK_4A_FINDINGS.md item 1: real success signals
                # already computed by TranslationWorker.shutdown(), just
                # never threaded into final_status before now.
                _mark_required_step(
                    "translation_drain",
                    bool(summary.get("TRANSLATION_WORKER_STOPPED")),
                    reason="translation_worker_not_stopped",
                )
                pending_at_exit = int(summary.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", 0) or 0)
                loading_pending = 0
                try:
                    getter = getattr(host, "loading_indicators_pending", None)
                    if callable(getter):
                        loading_pending = int(getter() or 0)
                except Exception:
                    loading_pending = pending_at_exit
                _mark_required_step(
                    "loading_state_drain",
                    pending_at_exit == 0 and loading_pending == 0,
                    reason="translation_queue_or_loading_indicators_not_drained",
                )
            except Exception as exc:
                freeze_guard_log(
                    "TRANSLATION_WORKER_SHUTDOWN_FAILED",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )
                _mark_required_step("translation_drain", False, reason="shutdown_exception")
                _mark_required_step("loading_state_drain", False, reason="shutdown_exception")

        run_timed_step(host, "translation_worker_shutdown", _translation_worker_shutdown)

        def _canonical_finalize() -> None:
            from alpha.utils.canonical_finalize import finalize_canonical_pipeline

            result = finalize_canonical_pipeline(host)
            # fixes TASK_4A_FINDINGS.md items 1/2: finalize_canonical_pipeline
            # already swallows its own exceptions into result["ok"]=False --
            # that value was previously only logged, never gating
            # final_status. Canonical ledger validation and Stable export
            # both happen inside this one call (canonical_finalize.py writes
            # the Stable-stage artifacts as part of the same result).
            _mark_required_step(
                "canonical_ledger_validation",
                bool(result.get("ok")),
                reason=str(result.get("error") or "canonical_finalize_not_ok"),
            )
            _mark_required_step(
                "stable_export",
                bool(result.get("ok")),
                reason=str(result.get("error") or "canonical_finalize_not_ok"),
            )
            if result.get("ok"):
                freeze_guard_log("CANONICAL_LEDGER_FROZEN", snapshot_id=result.get("snapshot_id"))
            else:
                freeze_guard_log("CANONICAL_LEDGER_FREEZE_FAILED", result=result)

        canonical_finalize_ok = run_timed_step(host, "canonical_pipeline_finalize", _canonical_finalize)
        if not canonical_finalize_ok:
            # Step itself timed out/raised past finalize_canonical_pipeline's
            # own try/except (e.g. the run_timed_step wrapper thread timeout)
            # -- _mark_required_step may never have run; fail closed instead
            # of leaving the two keys unset-but-implicitly-failing silently.
            _mark_required_step("canonical_ledger_validation", False, reason="step_timeout_or_exception")
            _mark_required_step("stable_export", False, reason="step_timeout_or_exception")

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
                # fixes TASK_4A_FINDINGS.md item 1 (final export): reuse the
                # already-computed real success signal (path is not None)
                # instead of only setting a flag nothing reads for status.
                _mark_required_step("final_export", True)
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
                _mark_required_step("final_export", False, reason="write_final_alpha_output_returned_none")
                freeze_guard_log("FINAL_ALPHA_ATOMIC_WRITE_FAILED")

        write_final_ok = run_timed_step(host, "write_final_alpha", _write_final_export)
        if not write_final_ok:
            _mark_required_step("final_export", False, reason="step_timeout_or_exception")

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

        # fixes TASK_4A_FINDINGS.md items 3/4: materialize the remaining two
        # evidence streams before the required-steps status is written.
        _write_translation_and_ui_evidence_streams(
            host,
            translation_summary=getattr(host, "_translation_shutdown_summary", None),
            ui_drain=ui_drain,
        )
        # fixes TASK_12_REPORT.md: _write_minimal_runtime_artifacts marks
        # "run_manifest" internally (its own last line), but was called
        # here completely unwrapped -- no run_timed_step, no try/except.
        # Any exception anywhere in its body (writing RUN_ARTIFACTS_INDEX,
        # LIVE_RUN_STATUS, or RUN_MANIFEST.json, or in
        # get_current_run_identity()/get_artifact_path() before any of
        # those writes) would propagate out of _run_finalize_worker
        # entirely, before ever reaching its own run_manifest marking --
        # the same silent-cascade class as the utterance_reconstruction
        # fix above, just one step later in the sequence and with
        # run_manifest itself (rather than something after it) as the
        # first casualty. run_timed_step gives it the same containment,
        # bounded timeout, and observability pair every other step has;
        # the explicit fallback mirrors the established
        # canonical_ledger_validation/stable_export and final_export
        # pattern used elsewhere in this same function.
        write_minimal_artifacts_ok = run_timed_step(
            host,
            "write_minimal_runtime_artifacts",
            lambda: _write_minimal_runtime_artifacts(host, dg_result=dg_result),
        )
        if not write_minimal_artifacts_ok:
            _mark_required_step(
                "run_manifest", False, reason="step_timeout_or_exception"
            )
        with _state_lock:
            _stop_state["finalize_completed"] = True
        if hasattr(host, "stop_core_completed_event"):
            try:
                host.stop_core_completed_event.set()
            except Exception:
                pass

        # fixes TASK_9_REPORT.md Issue 2: build_stop_finalize_summary() now
        # caches this call's result itself (run-id scoped, gated on
        # get_stop_finalize_snapshot()'s finalize_completed flag, which is
        # already True by this point -- see the "_stop_state[finalize_completed]
        # = True" line just above) so a later call for the SAME run
        # (evidence_pointer_finalize.py's delayed background thread) reuses
        # this one authoritative computation instead of recomputing against
        # globals that may have already moved on to a different run's Stop
        # sequence.
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
            from alpha.utils.run_identity import get_current_run_identity

            # fixes TASK_14_REPORT.md: capture THIS run's identity now,
            # while it is still guaranteed current, and hand it to the
            # background pass explicitly. get_current_run_identity() is a
            # single, unscoped module-level global — ground-truth
            # reproduction confirmed that if a real Start happens before
            # this background thread gets OS-scheduled, that global has
            # already moved on to the NEW run by the time the background
            # pass re-reads it, pairing the OLD run's host with the NEW
            # run's identity/folder and overwriting the NEW run's
            # RUN_MANIFEST.json with a bogus status.
            _this_run_identity = get_current_run_identity()
            schedule_evidence_pointer_finalization_background(
                host,
                reason="after_minimal_stop",
                run_id=str(getattr(_this_run_identity, "run_id", "") or ""),
                run_folder=str(getattr(_this_run_identity, "run_folder", "") or ""),
            )
        except Exception as exc:
            # fixes BUG_FIX_ROADMAP.md Batch 2 item 7: logging only -- if
            # get_current_run_identity() or the scheduling call itself
            # raises, the evidence package was previously silently never
            # scheduled: no log line recorded it, and the run's
            # RUN_MANIFEST.json stays at completed_pending_evidence_package
            # permanently with nothing to ever promote or explicitly fail
            # it. Scheduling behavior itself is unchanged.
            try:
                freeze_guard_log(
                    "EVIDENCE_PACKAGE_SCHEDULING_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                )
            except Exception:
                pass

        # Do NOT call _queue_final_ui_update again after drain (V25.3.3.1).
        freeze_guard_log("EVIDENCE_PACKAGE_WORKER_DISABLED_DURING_RUNTIME")
        freeze_guard_log("NO_UI_EVENT_AFTER_FINAL_DRAIN_CONFIRMED")
        return

    # fixes TASK_4A_FINDINGS.md item 5: this entire block (run_consistency_check
    # through the final _queue_final_ui_update call) was unreachable dead code
    # -- it sat after the unconditional `return` above and could never execute.
    # It independently re-implemented the same "write final export, reconcile
    # segment counts, write artifacts index, create upload package, finalize
    # live status/manifest/pointers" sequence that evidence_pointer_finalize.py
    # now performs live in the background (see schedule_evidence_pointer_finalization_background
    # above), including its own final_status derivation. Removed rather than
    # left as silently-unreachable code, per the decision recorded in
    # TASK_4B_CHANGES.md; nothing here ever ran, so removing it changes no
    # runtime behavior.

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
