"""Runtime evidence logging, UI perf counters, and long-test readiness."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from alpha.constants import APP_VERSION, JAPANESE_STT_PROFILE, LONG_TEST_BASELINE_MODE
from alpha.utils.async_debug_log import enqueue_ndjson_log, log_runtime_debug_event
from alpha.utils.japanese_accuracy_log import (
    get_japanese_accuracy_event_counts,
    jp_accuracy_log,
)

LONG_SESSION_INTERVAL_S = 120.0

_RUNTIME_DEBUG_EVENTS = frozenset(
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
        "DEEPGRAM_CLOSE_REQUESTED",
        "DEEPGRAM_CLOSE_NORMAL",
        "DEEPGRAM_CLOSE_ERROR",
        "FINAL_LIVE_SESSION_SUMMARY",
        "FINAL_UI_PERFORMANCE_SUMMARY",
        "LONG_SESSION_ACCURACY_SUMMARY",
        "TRANSLATION_UNIT_FLUSHED_SUMMARY",
        "ASYNC_LOG_WRITER_FLUSHED_ON_STOP",
        "RUN_ARTIFACTS_INDEX_CREATED",
        "RUN_ARTIFACTS_INDEX_UPDATED",
        "RUN_ID_CREATED",
        "ARTIFACT_ROOT_SELECTED",
        "RUN_CONSISTENCY_CHECK_PASSED",
        "RUN_CONSISTENCY_CHECK_FAILED",
        "RUN_CONSISTENCY_CHECK_BEGIN",
        "DEEPGRAM_GRACEFUL_STOP_BEGIN",
        "DEEPGRAM_AUDIO_SEND_STOPPED",
        "DEEPGRAM_CLOSE_LATE_NORMAL",
        "DEEPGRAM_CLOSE_TIMEOUT",
        "DEEPGRAM_GRACEFUL_STOP_DONE",
        "STOP_UI_CALLBACK_RETURNED",
        "LONG_TEST_READY_FOR_NEXT_STAGE",
        "ASYNC_LOG_FLUSH_REQUESTED",
        "ASYNC_LOG_FLUSH_COMPLETED",
        "ASYNC_LOG_FLUSH_TIMEOUT",
    }
)


@dataclass
class UIPerformanceCounters:
    transcript_ui_insert_slow_count: int = 0
    ui_queue_drain_slow_count: int = 0
    ui_queue_tick_slow_count: int = 0
    after_loop_scheduled_count: int = 0
    audio_queue_overflow_after_stop_count: int = 0
    max_ui_queue_tick_ms: float = 0.0
    max_transcript_insert_ms: float = 0.0
    max_ui_queue_depth: int = 0
    diagnostic_events_written: int = 0
    diagnostic_events_suppressed: int = 0
    _session_start_mono: float = field(default_factory=time.monotonic)

    def elapsed_seconds(self) -> float:
        return round(time.monotonic() - self._session_start_mono, 1)

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    def as_summary_dict(self) -> dict[str, Any]:
        return {
            "transcript_ui_insert_slow_count": self.transcript_ui_insert_slow_count,
            "ui_queue_drain_slow_count": self.ui_queue_drain_slow_count,
            "ui_queue_tick_slow_count": self.ui_queue_tick_slow_count,
            "after_loop_scheduled_count": self.after_loop_scheduled_count,
            "audio_queue_overflow_after_stop_count": self.audio_queue_overflow_after_stop_count,
            "max_ui_queue_tick_ms": round(self.max_ui_queue_tick_ms, 2),
            "max_transcript_insert_ms": round(self.max_transcript_insert_ms, 2),
            "max_ui_queue_depth": self.max_ui_queue_depth,
            "diagnostic_events_written": self.diagnostic_events_written,
            "diagnostic_events_suppressed": self.diagnostic_events_suppressed,
            "elapsed_seconds": self.elapsed_seconds(),
        }


_ui_perf = UIPerformanceCounters()
_long_session_last_mono: float = 0.0
_readiness_score_sum: float = 0.0
_readiness_score_count: int = 0


def get_ui_performance_counters() -> UIPerformanceCounters:
    return _ui_perf


def reset_runtime_evidence_session() -> None:
    global _long_session_last_mono, _readiness_score_sum, _readiness_score_count
    _ui_perf.reset()
    _long_session_last_mono = time.monotonic()
    _readiness_score_sum = 0.0
    _readiness_score_count = 0


def note_readiness_score(score: float) -> None:
    global _readiness_score_sum, _readiness_score_count
    _readiness_score_sum += float(score)
    _readiness_score_count += 1


def mirror_runtime_event(message: str, data: Optional[dict[str, Any]] = None) -> None:
    if message not in _RUNTIME_DEBUG_EVENTS:
        return
    log_runtime_debug_event(message, **(data or {}))


def emit_final_ui_performance_summary() -> dict[str, Any]:
    payload = _ui_perf.as_summary_dict()
    jp_accuracy_log("FINAL_UI_PERFORMANCE_SUMMARY", **payload)
    mirror_runtime_event("FINAL_UI_PERFORMANCE_SUMMARY", payload)
    return payload


def emit_long_session_accuracy_summary(
    assembler: Any,
    *,
    reason: str,
    host: Any = None,
) -> dict[str, Any]:
    event_counts = get_japanese_accuracy_event_counts()
    elapsed = _ui_perf.elapsed_seconds()
    exported = int(getattr(host, "_exported_ui_segment_count", 0) or 0) if host else 0
    stable_count = int(event_counts.get("STABLE_JAPANESE_COMMIT", 0))
    avg_score = (
        round(_readiness_score_sum / _readiness_score_count, 3)
        if _readiness_score_count > 0
        else 0.0
    )
    unit_summary = {}
    if assembler is not None and hasattr(assembler, "_translation_unit_builder"):
        unit_summary = assembler._translation_unit_builder.summary_counts()
    risky_count = len(getattr(assembler, "_risky_segments", []) or [])
    tail = (getattr(assembler, "_last_final_output_text", "") or "")[-300:]
    speaker_dist = {}
    if assembler is not None and hasattr(assembler, "_speaker_distribution_snapshot"):
        speaker_dist = assembler._speaker_distribution_snapshot()
    ready_ratio = float(unit_summary.get("TRANSLATION_UNIT_READY_RATIO", 0.0) or 0.0)
    payload = {
        "reason": reason,
        "elapsed_seconds": elapsed,
        "internal_stable_commit_count": stable_count,
        "stable_commit_count": stable_count,
        "exported_ui_segment_count": exported,
        "translation_unit_count": unit_summary.get("translation_unit_count", 0),
        "ready_translation_unit_count": unit_summary.get("ready_translation_unit_count", 0),
        "translation_ready_ratio": ready_ratio,
        "TRANSLATION_UNIT_READY_RATIO": ready_ratio,
        "risky_segment_count": risky_count,
        "cleanup_applied_count": int(getattr(assembler, "_cleanup_applied_to_ui_count", 0) or 0),
        "cleanup_not_applied_count": int(
            getattr(assembler, "_cleanup_low_confidence_not_applied_count", 0) or 0
        ),
        "speaker_distribution": speaker_dist,
        "noise_quarantine_count": int(event_counts.get("NOISE_FRAGMENT_QUARANTINED", 0)),
        "noise_fragment_quarantined_count": int(
            event_counts.get("NOISE_FRAGMENT_QUARANTINED", 0)
        ),
        "stale_final_dropped_count": int(event_counts.get("STALE_FINAL_DROPPED", 0)),
        "emergency_commit_count": int(event_counts.get("EMERGENCY_COMMIT", 0)),
        "EMERGENCY_COMMIT_count": int(event_counts.get("EMERGENCY_COMMIT", 0)),
        "safe_hold_timeout_count": int(event_counts.get("SAFE_HOLD_TIMEOUT_COMMIT", 0)),
        "safe_chunk_boundary_count": int(event_counts.get("SAFE_CHUNK_BOUNDARY_COMMIT", 0)),
        "raw_stt_error_candidate_count": int(event_counts.get("RAW_STT_ERROR_CANDIDATE", 0)),
        "keyterm_overbias_candidate_count": int(
            event_counts.get("KEYTERM_OVERBIAS_CANDIDATE", 0)
        ),
        "average_translation_ready_score": avg_score,
        "latest_output_tail_preview": tail,
    }
    jp_accuracy_log("LONG_SESSION_ACCURACY_SUMMARY", **payload)
    mirror_runtime_event("LONG_SESSION_ACCURACY_SUMMARY", payload)
    return payload


def should_emit_long_session_summary() -> bool:
    if not LONG_TEST_BASELINE_MODE:
        return False
    global _long_session_last_mono
    now = time.monotonic()
    if now - _long_session_last_mono >= LONG_SESSION_INTERVAL_S:
        _long_session_last_mono = now
        return True
    return False


def emit_translation_unit_flushed_summary(assembler: Any) -> dict[str, Any]:
    if assembler is None:
        return {}
    unit_summary = assembler._translation_unit_builder.summary_counts()
    preview = assembler._translation_unit_builder.units_preview(limit=3)
    payload = {
        **unit_summary,
        "translation_unit_preview": preview,
    }
    jp_accuracy_log("TRANSLATION_UNIT_FLUSHED_SUMMARY", **payload)
    mirror_runtime_event("TRANSLATION_UNIT_FLUSHED_SUMMARY", payload)
    return payload


def evaluate_long_test_readiness(
    *,
    host: Any,
    assembler: Any,
    emergency_commit_count: int,
    consistency_result: Optional[dict[str, Any]] = None,
    stop_state: Optional[dict[str, Any]] = None,
) -> tuple[bool, list[str], list[str]]:
    """Return (ready, blocking_reasons, warning_reasons)."""
    from pathlib import Path

    from alpha.utils.run_identity import RUN_TYPE_LIVE, get_current_run_identity
    from alpha.utils.run_artifacts import get_current_index_path

    blocking: list[str] = []
    warnings: list[str] = []
    elapsed_min = max(0.1, _ui_perf.elapsed_seconds() / 60.0)
    slow_insert_budget = max(2, int(2 * (elapsed_min / 10.0)))
    slow_drain_budget = max(2, int(2 * (elapsed_min / 10.0)))
    slow_tick_budget = max(2, int(2 * (elapsed_min / 10.0)))

    identity = get_current_run_identity()
    stop_state = stop_state or {}

    if not stop_state.get("ui_callback_returned", True):
        blocking.append("stop_ui_callback_missing")
    worker_complete = bool(
        stop_state.get("worker_done", False)
        or stop_state.get("finalize_completed", False)
    )
    if not worker_complete:
        blocking.append("stop_finalize_worker_incomplete")
    elif not stop_state.get("worker_done", False):
        warnings.append("stop_finalize_worker_done_pending")

    if emergency_commit_count > 0:
        blocking.append("emergency_commit_present")
    if _ui_perf.audio_queue_overflow_after_stop_count > 0:
        blocking.append("audio_queue_overflow_after_stop")
    if _ui_perf.transcript_ui_insert_slow_count > slow_insert_budget:
        blocking.append("too_many_transcript_insert_slow")
    if _ui_perf.ui_queue_drain_slow_count > slow_drain_budget:
        blocking.append("too_many_ui_queue_drain_slow")
    if _ui_perf.ui_queue_tick_slow_count > slow_tick_budget:
        blocking.append("too_many_ui_queue_tick_slow")

    try:
        from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_log_path
        from alpha.utils.async_debug_log import get_async_debug_log_path
        from alpha.utils.diagnostic_test_log import get_log_file_path

        if not get_japanese_accuracy_log_path().exists():
            blocking.append("accuracy_log_missing")
        if not get_async_debug_log_path().exists():
            blocking.append("debug_log_missing")
        if not get_log_file_path().exists():
            warnings.append("diagnostic_log_missing")
    except Exception:
        warnings.append("log_path_check_failed")

    index_path = get_current_index_path()
    index_exists = bool(index_path and index_path.exists())
    if identity and identity.run_type == RUN_TYPE_LIVE:
        if not index_exists and not identity.index_created:
            blocking.append("run_artifacts_index_missing")
        elif index_exists:
            text = index_path.read_text(encoding="utf-8", errors="ignore")
            if "MagicMock" in text:
                blocking.append("magicmock_in_live_index")
            if "RUN_TYPE=live" not in text and "RUN_TYPE=mock_test" in text:
                blocking.append("live_index_has_mock_run_type")
    elif identity and identity.run_type != RUN_TYPE_LIVE:
        warnings.append(f"non_live_run_type={identity.run_type}")

    if consistency_result and not consistency_result.get("passed", False):
        for reason in consistency_result.get("blocking_reasons") or []:
            blocking.append(f"consistency_{reason}")
        for reason in consistency_result.get("warning_reasons") or []:
            warnings.append(f"consistency_{reason}")

    speaker_dist = {}
    if assembler is not None and hasattr(assembler, "_speaker_distribution_snapshot"):
        speaker_dist = assembler._speaker_distribution_snapshot()
    if speaker_dist:
        dominant = max(speaker_dist, key=speaker_dist.get)
        total = sum(int(v) for v in speaker_dist.values())
        if total > 0 and speaker_dist.get(dominant, 0) / total < 0.5:
            blocking.append("unstable_speaker_distribution")

    if stop_state.get("timed_out_steps"):
        for step in stop_state["timed_out_steps"]:
            if step == "deepgram_graceful_stop":
                dg_status = str((stop_state or {}).get("deepgram_close_status", ""))
                if dg_status in ("normal", "late_normal"):
                    warnings.append("deepgram_graceful_stop_late_normal_close")
                else:
                    warnings.append("deepgram_graceful_stop_timeout")
            else:
                warnings.append(f"step_timeout_{step}")

    ready = len(blocking) == 0
    return ready, blocking, warnings


def emit_long_test_readiness(
    *,
    host: Any,
    assembler: Any,
    emergency_commit_count: int,
    consistency_result: Optional[dict[str, Any]] = None,
    stop_state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    ready, blocking, warnings = evaluate_long_test_readiness(
        host=host,
        assembler=assembler,
        emergency_commit_count=emergency_commit_count,
        consistency_result=consistency_result,
        stop_state=stop_state,
    )
    payload = {
        "ready": ready,
        "reasons": blocking + warnings,
        "blocking_reasons": blocking,
        "warning_reasons": warnings,
    }
    jp_accuracy_log("LONG_TEST_READY_FOR_NEXT_STAGE", **payload)
    mirror_runtime_event("LONG_TEST_READY_FOR_NEXT_STAGE", payload)
    return payload


_SUSPICIOUS_KANTOKU = re.compile(r"な(?:と|んと)か監督")


def categorize_japanese_accuracy_issues(
    *,
    raw_text: str,
    stable_text: str,
    candidate_text: str,
    cleanup_candidate: Optional[dict[str, Any]] = None,
    readiness_reasons: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    stable = (stable_text or "").strip()
    raw = (raw_text or "").strip()
    candidate = (candidate_text or stable).strip()
    cleanup = cleanup_candidate or {}

    def add(
        category: str,
        *,
        confidence: float = 0.0,
        applied_to_ui: bool = False,
        translation_ready_impact: str = "",
        reason: str = "",
    ) -> None:
        findings.append(
            {
                "category": category,
                "raw_text": raw[:160],
                "stable_text": stable[:160],
                "candidate_text": candidate[:160],
                "confidence": confidence,
                "applied_to_ui": applied_to_ui,
                "translation_ready_impact": translation_ready_impact,
                "reason": reason,
            }
        )

    if "尊敬してい人" in stable or "敬してる人" in stable:
        add(
            "missing_word",
            confidence=0.96 if "尊敬してる人" in candidate else 0.0,
            applied_to_ui=bool(cleanup.get("applied_to_ui")),
            translation_ready_impact="keigo_missing_teiru",
            reason="missing_teiru_in_sonkei_phrase",
        )
    if _SUSPICIOUS_KANTOKU.search(stable):
        add(
            "keyterm_overbias",
            translation_ready_impact="not_ready",
            reason="suspicious_kantoku_pattern",
        )
    if "リーフが浮かんでこない" in stable:
        add(
            "wrong_word",
            translation_ready_impact="riifu_stt_error",
            reason="riifu_instead_of_riyuu",
        )
    if stable.startswith("よ私が"):
        add(
            "speech_disfluency",
            translation_ready_impact="leading_yo_fragment",
            reason="leading_yo_split",
        )
    for tail in ("の", "が", "を", "に", "で", "から", "っていう"):
        if stable.endswith(tail):
            add(
                "incomplete_tail",
                translation_ready_impact="weak_ending",
                reason=f"ends_with_{tail}",
            )
            break
    if "さらて普通に" in stable or "さらさって普通に" in stable:
        add(
            "speech_disfluency",
            confidence=float(cleanup.get("confidence") or 0.0),
            applied_to_ui=bool(cleanup.get("applied_to_ui")),
            reason="sarasara_disfluency",
        )
    if cleanup.get("changes"):
        cat = (
            "cleanup_high_confidence_applied"
            if cleanup.get("applied_to_ui")
            else "cleanup_low_confidence_not_applied"
        )
        add(
            cat,
            confidence=float(cleanup.get("confidence") or 0.0),
            applied_to_ui=bool(cleanup.get("applied_to_ui")),
            reason=str(cleanup.get("reason") or ""),
        )
    for reason in readiness_reasons or []:
        if "keyterm_overbias" in reason:
            add("keyterm_overbias", reason=reason)
        if "emergency_commit" in reason:
            add("noise_or_late_fragment", reason=reason)
    return findings


def log_japanese_accuracy_issue_candidates(findings: list[dict[str, Any]]) -> None:
    for finding in findings:
        jp_accuracy_log("JAPANESE_ACCURACY_ISSUE_CANDIDATE", **finding)


def finalize_run_evidence(host: Any, *, reason: str = "stop_listening") -> dict[str, Any]:
    """Emit end-of-run summaries (legacy entry — prefer finalize_run_evidence_safe)."""
    return finalize_run_evidence_safe(host, reason=reason)


def finalize_run_evidence_safe(host: Any, *, reason: str = "stop_listening") -> dict[str, Any]:
    """Emit end-of-run summaries without widget reads; never raises."""
    from pathlib import Path

    from alpha.transcription.japanese_sentence_assembler import (
        get_japanese_continuity_assembler,
    )
    from alpha.utils.async_debug_log import get_debug_event_stats
    from alpha.utils.freeze_guard_log import freeze_guard_log
    from alpha.utils.japanese_accuracy_log import (
        get_japanese_accuracy_event_counts,
        get_japanese_accuracy_log_path,
    )

    freeze_guard_log("FINAL_SUMMARY_BEGIN", reason=reason)
    try:
        assembler = get_japanese_continuity_assembler(host)
        emit_long_session_accuracy_summary(assembler, reason=reason, host=host)
        emit_translation_unit_flushed_summary(assembler)

        stats = get_debug_event_stats()
        _ui_perf.diagnostic_events_written = int(stats.get("diagnostic_events_written", 0))
        _ui_perf.diagnostic_events_suppressed = int(
            stats.get("diagnostic_events_suppressed", 0)
        )
        ui_summary = emit_final_ui_performance_summary()

        event_counts = get_japanese_accuracy_event_counts()
        emergency = int(event_counts.get("EMERGENCY_COMMIT", 0) or 0)
        freeze_guard_log("FINAL_SUMMARY_DONE", emergency_commit_count=emergency)
        return {
            "emergency_commit_count": emergency,
            "ui_summary": ui_summary,
        }
    except Exception as exc:
        freeze_guard_log(
            "FINAL_SUMMARY_FAILED",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )
        return {}


def build_artifact_index_extra(
    host: Any,
    *,
    assembler: Any = None,
    summary: Optional[dict[str, Any]] = None,
    readiness: Optional[dict[str, Any]] = None,
    stop_state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_event_counts

    event_counts = get_japanese_accuracy_event_counts()
    unit_summary = {}
    speaker_dist = {}
    internal_stable = int(event_counts.get("STABLE_JAPANESE_COMMIT", 0))
    if assembler is not None:
        if hasattr(assembler, "_translation_unit_builder"):
            unit_summary = assembler._translation_unit_builder.summary_counts()
        if hasattr(assembler, "_speaker_distribution_snapshot"):
            speaker_dist = assembler._speaker_distribution_snapshot()

    perf = get_ui_performance_counters().as_summary_dict()
    identity = None
    try:
        from alpha.utils.run_identity import get_current_run_identity

        identity = get_current_run_identity()
    except Exception:
        pass

    extra = {
        "internal_stable_commit_count": internal_stable,
        "translation_unit_count": unit_summary.get("translation_unit_count", 0),
        "ready_translation_unit_count": unit_summary.get("ready_translation_unit_count", 0),
        "translation_ready_ratio": unit_summary.get("TRANSLATION_UNIT_READY_RATIO", 0.0),
        "speaker_distribution": speaker_dist,
        "emergency_commit_count": int(event_counts.get("EMERGENCY_COMMIT", 0)),
        "transcript_ui_insert_slow_count": perf.get("transcript_ui_insert_slow_count", 0),
        "ui_queue_drain_slow_count": perf.get("ui_queue_drain_slow_count", 0),
        "ui_queue_tick_slow_count": perf.get("ui_queue_tick_slow_count", 0),
        "audio_queue_overflow_after_stop_count": perf.get(
            "audio_queue_overflow_after_stop_count", 0
        ),
        "long_test_ready_for_next_stage": str(
            (readiness or {}).get("ready", False)
        ).lower(),
        "long_test_ready_reasons": (readiness or {}).get("reasons", []),
        "long_test_blocking_reasons": (readiness or {}).get("blocking_reasons", []),
        "long_test_warning_reasons": (readiness or {}).get("warning_reasons", []),
    }
    if identity is not None:
        extra["deepgram_close_status"] = identity.deepgram_close_status
        extra["stop_ui_callback_duration_ms"] = identity.stop_ui_callback_duration_ms
        extra["deepgram_graceful_stop_duration_ms"] = (
            identity.deepgram_graceful_stop_duration_ms
        )
    if stop_state:
        extra["timed_out_steps"] = stop_state.get("timed_out_steps", [])
        extra["failed_steps"] = stop_state.get("failed_steps", [])
        extra["stop_finalize_completed"] = stop_state.get("stop_finalize_completed", False)
        extra["stop_finalize_failed"] = stop_state.get("stop_finalize_failed", False)
        extra["stop_finalize_timed_out"] = stop_state.get("stop_finalize_timed_out", False)
        extra["deepgram_close_status"] = stop_state.get(
            "deepgram_close_status", extra.get("deepgram_close_status", "")
        )
        extra["deepgram_close_late_normal"] = stop_state.get("deepgram_close_late_normal", False)
        extra["stop_finalize_duration_ms"] = stop_state.get("stop_finalize_duration_ms", 0)
    return extra


def write_run_artifacts_index_safe(
    host: Any,
    *,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Update RUN_ARTIFACTS_INDEX at stop; never writes live index for mock runs."""
    from alpha.utils.run_artifacts import update_run_artifacts_index_at_stop
    from alpha.utils.run_identity import RUN_TYPE_LIVE, get_current_run_identity

    identity = get_current_run_identity()
    if identity is None:
        return None
    if identity.run_type != RUN_TYPE_LIVE:
        return update_run_artifacts_index_at_stop(
            identity=identity, host=host, extra=extra
        )
    return update_run_artifacts_index_at_stop(identity=identity, host=host, extra=extra)
