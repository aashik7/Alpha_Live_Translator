"""Centralized hard-fail acceptance for translation beta repair evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def evaluate_graceful_shutdown_gate(summary: Dict[str, Any]) -> Tuple[bool, List[str]]:
    failures: List[str] = []
    checks = {
        "TRANSLATION_QUEUE_PENDING_AT_EXIT": 0,
        "TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT": 0,
        "ORDERING_BUFFER_PENDING_AT_EXIT": 0,
        "MISSING_TRANSLATION_SEGMENT_IDS": 0,
        "TRANSLATION_REQUESTS_SENT_FROM_INTERIM": 0,
        "DUPLICATE_TRANSLATION_REQUESTS_SENT": 0,
        "DUPLICATE_TRANSLATION_COMMITS": 0,
        "OUT_OF_ORDER_TRANSLATION_COMMITS": 0,
        "SOURCE_TRANSCRIPT_MODIFICATIONS": 0,
    }
    for key, expected in checks.items():
        got = int(summary.get(key, -1) or 0)
        if got != expected:
            failures.append(f"{key}={got} expected={expected}")
    unfinished = summary.get("UNFINISHED_TRANSLATION_SEGMENT_IDS") or summary.get(
        "unfinished_segment_ids"
    ) or []
    if unfinished:
        failures.append(f"unfinished_ids={unfinished}")
    if not bool(summary.get("TRANSLATION_WORKER_STOPPED")):
        failures.append("TRANSLATION_WORKER_STOPPED=false")
    return (len(failures) == 0), failures


def evaluate_overall_acceptance(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Compute OVERALL_ACCEPTANCE strictly from evidence objects."""
    failures: List[str] = []

    def require(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    require(
        evidence.get("JAPANESE_FREEZE_VERIFICATION") == "PASSED",
        "japanese_freeze",
    )
    require(
        evidence.get("ENGLISH_NO_DIARIZATION_VALIDATION") == "PASSED",
        "english_no_diarization",
    )
    require(bool(evidence.get("generic_speaker_label_enabled")), "speaker_label")
    require(bool(evidence.get("ja_to_en_ok")), "ja_to_en")
    require(bool(evidence.get("en_to_ja_ok")), "en_to_ja")
    require(
        evidence.get("TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION") == "PASSED",
        "graceful_shutdown",
    )
    require(
        evidence.get("TRANSLATION_TIMEOUT_HANDLING_VALIDATION") == "PASSED",
        "timeout_handling",
    )
    require(
        evidence.get("TRANSLATION_REPORT_CONSISTENCY") == "PASSED",
        "report_consistency",
    )
    require(
        evidence.get("TRANSLATION_LIVE_INTEGRATION_VALIDATION") == "PASSED",
        "live_integration",
    )
    require(evidence.get("TRANSLATION_SMOKE_TEST") == "PASSED", "smoke")
    require(bool(evidence.get("api_key_absent_from_logs")), "api_key_in_logs")
    require(bool(evidence.get("language_map_ok")), "language_map")
    require(bool(evidence.get("true_e2e_latency_reported")), "e2e_latency")

    counters = evidence.get("counters") or {}
    require(int(counters.get("INTERIM_SUBMISSIONS_REJECTED", 0)) >= 1, "interim_rejected")
    require(int(counters.get("DUPLICATE_SUBMISSIONS_REJECTED", 0)) >= 1, "dup_rejected")
    require(int(counters.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM", -1)) == 0, "interim_sent")
    require(int(counters.get("DUPLICATE_TRANSLATION_REQUESTS_SENT", -1)) == 0, "dup_sent")
    require(int(counters.get("DUPLICATE_TRANSLATION_COMMITS", -1)) == 0, "dup_commits")
    require(int(counters.get("OUT_OF_ORDER_TRANSLATION_COMMITS", -1)) == 0, "ooo_commits")
    source_mod = counters.get("SOURCE_TRANSCRIPT_MODIFICATIONS")
    if source_mod is None:
        source_mod = counters.get("source_transcript_modifications", -1)
    require(int(source_mod) == 0, "source_mod")

    graceful = evidence.get("graceful_shutdown_summary") or {}
    require(int(graceful.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", -1)) == 0, "pending_q")
    require(int(graceful.get("TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT", -1)) == 0, "in_flight")
    require(int(graceful.get("ORDERING_BUFFER_PENDING_AT_EXIT", -1)) == 0, "ordering")
    require(int(graceful.get("MISSING_TRANSLATION_SEGMENT_IDS", -1)) == 0, "missing")
    require(bool(graceful.get("TRANSLATION_WORKER_STOPPED")), "worker_stopped")

    passed = len(failures) == 0
    return {
        "OVERALL_ACCEPTANCE": "PASSED" if passed else "FAILED",
        "failures": failures,
        "computed_from_evidence": True,
    }
