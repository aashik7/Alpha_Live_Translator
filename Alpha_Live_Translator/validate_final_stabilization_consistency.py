#!/usr/bin/env python3
"""Cross-report consistency gate for final_stabilization packages.

Every PASS/FAIL is computed from evidence files. NOT_RUN is never promoted to PASSED.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"_load_error": True, "path": str(path)}


def _status(payload: dict[str, Any], key: str) -> str:
    if not payload or payload.get("_load_error"):
        return "MISSING"
    val = payload.get(key)
    if val is None:
        # allow nested
        for k, v in payload.items():
            if k == key or (isinstance(v, str) and k.endswith(key)):
                val = v
                break
    if val is None:
        return "MISSING"
    s = str(val).upper()
    if s in {"PASSED", "FAILED", "NOT_RUN", "BLOCKED", "READY_FOR_SHORT_LIVE_TEST"}:
        return s
    if s in {"TRUE", "OK", "PASS"}:
        return "PASSED"
    if s in {"FALSE", "FAIL"}:
        return "FAILED"
    return s


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True)
    args = parser.parse_args()
    root = Path(args.package_dir)

    failures: list[str] = []
    notes: list[str] = []

    decision = (root / "FINAL_STABILIZATION_DECISION_REPORT.txt").read_text(
        encoding="utf-8", errors="replace"
    ) if (root / "FINAL_STABILIZATION_DECISION_REPORT.txt").exists() else ""
    cursor = (root / "Cursor final report.txt").read_text(
        encoding="utf-8", errors="replace"
    ) if (root / "Cursor final report.txt").exists() else ""
    manifest = _load(root / "implementation_manifest.json")
    readiness = _load(root / "SHORT_LIVE_TEST_READINESS.json")
    startup = _load(root / "REAL_ALPHA_STARTUP_COMPARISON.json")
    ui = _load(root / "UI_EVENT_LOOP_RESPONSIVENESS.json")
    ui_val = _load(root / "UI_RESPONSIVENESS_VALIDATION.json")
    graceful = _load(root / "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION.json")
    counters = _load(root / "TRANSLATION_COUNTER_VALIDATION.json")
    order = _load(root / "TRANSLATION_ORDER_VALIDATION.json")
    ja_freeze = _load(root / "JAPANESE_FREEZE_VERIFICATION.json")
    en_freeze = _load(root / "ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION.json")
    en_nodiar = _load(root / "ENGLISH_NO_DIARIZATION_VALIDATION.json")
    speaker = _load(root / "GENERIC_SPEAKER_VALIDATION.json")
    bilingual = _load(root / "BIDIRECTIONAL_TRANSLATION_VALIDATION.json")
    start_stop = _load(root / "START_STOP_VALIDATION.json")
    copy_clear = _load(root / "COPY_EXPORT_CLEAR_VALIDATION.json")
    after = _load(root / "FINAL_STABILIZATION_AFTER.json")

    # Splash exclusion
    if startup.get("splash_excluded") is not True and after.get("splash_excluded") is not True:
        failures.append("splash_not_excluded")
    if startup.get("measurement") and "real_alpha" not in str(startup.get("measurement")):
        failures.append("startup_not_real_alpha_measurement")

    # NOT_RUN must never appear as PASSED in decision text for freezes
    ja_st = _status(ja_freeze, "JAPANESE_FREEZE_VERIFICATION")
    en_st = _status(en_freeze, "ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION")
    if ja_st == "NOT_RUN" and "JAPANESE_FREEZE_VERIFICATION=PASSED" in decision:
        failures.append("japanese_freeze_not_run_claimed_passed")
    if en_st == "NOT_RUN" and "ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION=PASSED" in decision:
        failures.append("english_freeze_not_run_claimed_passed")

    # Graceful vs pending
    gsum = graceful.get("summary") or {}
    if _status(graceful, "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION") == "PASSED":
        if int(gsum.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", -1)) != 0:
            failures.append("graceful_passed_but_pending_nonzero")
        if int(gsum.get("TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT", -1)) != 0:
            failures.append("graceful_passed_but_inflight_nonzero")
        if int(gsum.get("ORDERING_BUFFER_PENDING_AT_EXIT", -1)) != 0:
            failures.append("graceful_passed_but_ordering_nonzero")
        unfinished = gsum.get("UNFINISHED_TRANSLATION_SEGMENT_IDS") or gsum.get(
            "MISSING_TRANSLATION_SEGMENT_IDS"
        ) or []
        if unfinished:
            failures.append("graceful_passed_but_unfinished_ids")

    # Counter contradictions
    c = counters.get("counters") or counters.get("summary_excerpt") or {}
    if int(c.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM", 0) or 0) != 0:
        if _status(counters, "TRANSLATION_COUNTER_VALIDATION") == "PASSED" or _status(
            counters, "TRANSLATION_COUNTER_SEMANTICS_VALIDATION"
        ) == "PASSED":
            failures.append("interim_sent_nonzero_but_counter_passed")
    if int(c.get("DUPLICATE_TRANSLATION_REQUESTS_SENT", 0) or 0) != 0:
        if _status(counters, "TRANSLATION_COUNTER_VALIDATION") == "PASSED" or _status(
            counters, "TRANSLATION_COUNTER_SEMANTICS_VALIDATION"
        ) == "PASSED":
            failures.append("dup_sent_nonzero_but_counter_passed")

    # Decision / readiness / manifest agreement
    ready_status = str(readiness.get("STATUS") or readiness.get("SHORT_LIVE_TEST_READINESS") or "")
    manifest_status = str(manifest.get("STATUS") or manifest.get("final_status") or "")
    if "READY_FOR_SHORT_LIVE_TEST" in decision and ready_status not in (
        "READY_FOR_SHORT_LIVE_TEST",
        "PASSED",
    ):
        failures.append("decision_ready_but_readiness_json_not")
    if "STATUS = BLOCKED" in decision or "STATUS=BLOCKED" in decision:
        if ready_status == "READY_FOR_SHORT_LIVE_TEST":
            failures.append("decision_blocked_but_readiness_ready")
    if manifest_status and ready_status and manifest_status != ready_status:
        # allow manifest to use same STATUS field
        if not (
            manifest_status in decision
            or ready_status == manifest.get("STATUS")
        ):
            failures.append("manifest_status_mismatch")

    # Cursor report must match decision on STATUS line
    for token in ("READY_FOR_SHORT_LIVE_TEST", "STATUS = BLOCKED", "STATUS=BLOCKED"):
        if (token in decision) != (token in cursor):
            failures.append(f"cursor_decision_mismatch:{token}")
            break

    # UI hard gate reflection
    ui_passed = _status(ui_val, "UI_RESPONSIVENESS_VALIDATION")
    if ui_passed == "PASSED" and int(ui.get("delays_above_500_ms", 0) or 0) > 0:
        failures.append("ui_passed_but_delays_above_500")

    # Speaker / bilingual / start-stop presence
    for name, payload, key in (
        ("speaker", speaker, "GENERIC_SPEAKER_VALIDATION"),
        ("bilingual", bilingual, "BIDIRECTIONAL_TRANSLATION_VALIDATION"),
        ("start_stop", start_stop, "START_STOP_VALIDATION"),
        ("copy_export_clear", copy_clear, "COPY_EXPORT_CLEAR_VALIDATION"),
        ("order", order, "TRANSLATION_ORDER_VALIDATION"),
        ("en_nodiar", en_nodiar, "ENGLISH_NO_DIARIZATION_VALIDATION"),
    ):
        st = _status(payload, key)
        if st == "MISSING":
            failures.append(f"missing_report:{name}")
        elif st == "NOT_RUN":
            notes.append(f"{name}=NOT_RUN")

    # If readiness READY, required gates must be PASSED (not NOT_RUN)
    if ready_status == "READY_FOR_SHORT_LIVE_TEST":
        required = {
            "japanese_freeze": ja_st,
            "english_freeze": en_st,
            "english_no_diarization": _status(en_nodiar, "ENGLISH_NO_DIARIZATION_VALIDATION"),
            "speaker": _status(speaker, "GENERIC_SPEAKER_VALIDATION"),
            "bilingual": _status(bilingual, "BIDIRECTIONAL_TRANSLATION_VALIDATION"),
            "graceful": _status(graceful, "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION"),
            "ui": ui_passed if ui_passed != "MISSING" else "FAILED",
            "start_stop": _status(start_stop, "START_STOP_VALIDATION"),
            "copy_export_clear": _status(copy_clear, "COPY_EXPORT_CLEAR_VALIDATION"),
        }
        for k, v in required.items():
            if v != "PASSED":
                failures.append(f"ready_but_{k}_not_passed:{v}")

    passed = not failures
    result = {
        "FINAL_STABILIZATION_REPORT_CONSISTENCY": "PASSED" if passed else "FAILED",
        "failures": failures,
        "notes": notes,
        "statuses": {
            "japanese_freeze": ja_st,
            "english_freeze": en_st,
            "readiness": ready_status,
            "ui": ui_passed,
            "graceful": _status(graceful, "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION"),
        },
        "computed_from_evidence": True,
    }
    (root / "FINAL_STABILIZATION_REPORT_CONSISTENCY.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
