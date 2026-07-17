"""Validate alignment coverage repair & evidence index sync 8.5.23.4.1."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ALIGNMENT_COVERAGE_REPAIR_852341_ENABLED,
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    CER_TRUST_ALIGNMENT_V2_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    EVIDENCE_INDEX_SYNC_AFTER_SCORING_ENABLED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LATEST_REPORT_SYNC_STRICT_MATCH_ENABLED,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    LINE_COUNT_MISMATCH_TOLERANCE_ENABLED,
    PARAGRAPH_WINDOW_ALIGNMENT_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    SLIDING_TEXT_WINDOW_ALIGNMENT_ENABLED,
    STOP_PATH_MINIMAL_MODE,
)
from alpha.transcription.japanese_business_accuracy import (
    is_minimal_correction_mode,
    run_business_correction_guard_selftest,
    run_minimal_correction_selftest,
)


def _has(path: str, token: str) -> bool:
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def main() -> int:
    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.23.4.1",
        "deepgram_unchanged": (
            DEEPGRAM_MODEL == "nova-3"
            and DEEPGRAM_LANGUAGE == "ja"
            and DEEPGRAM_ENDPOINTING_MS == 500
            and DEEPGRAM_UTTERANCE_END_MS == 1500
        ),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "raw_deepgram_immutable": RAW_DEEPGRAM_IMMUTABLE,
        "stop_baseline": STOP_PATH_MINIMAL_MODE and RUNTIME_EVIDENCE_PACKAGE_DISABLED,
        "minimal_level": AUTO_BUSINESS_CORRECTION_LEVEL == "minimal" and is_minimal_correction_mode(),
        "anti_overfit": ANTI_OVERFIT_MODE_ENABLED,
        "lesson_specific_disabled": LESSON_SPECIFIC_CORRECTIONS_DISABLED,
        "guard_selftest": run_business_correction_guard_selftest().get("ok") is True,
        "minimal_selftest": run_minimal_correction_selftest().get("ok") is True,
        "full_logging": FULL_ACCURACY_LOGGING_STILL_ENABLED,
        "852341_flags": all(
            (
                ALIGNMENT_COVERAGE_REPAIR_852341_ENABLED,
                PARAGRAPH_WINDOW_ALIGNMENT_ENABLED,
                SLIDING_TEXT_WINDOW_ALIGNMENT_ENABLED,
                CER_TRUST_ALIGNMENT_V2_ENABLED,
                EVIDENCE_INDEX_SYNC_AFTER_SCORING_ENABLED,
                LATEST_REPORT_SYNC_STRICT_MATCH_ENABLED,
                LINE_COUNT_MISMATCH_TOLERANCE_ENABLED,
            )
        ),
        "alignment_v2_module": Path("alpha/utils/alignment_v2.py").exists(),
        "analyze_v2_fields": _has("analyze_alpha_vs_reference.py", "run_alignment_v2"),
        "score_trust_v2_fields": _has("score_latest_accuracy.py", "trusted_score_after_alignment_v2"),
        "score_char_coverage": _has("score_latest_accuracy.py", "char_coverage_used_for_trust"),
        "report_sync_v2": _has("alpha/utils/accuracy_report_sync.py", "EVIDENCE_INDEX_ALIGNMENT_V2_FIELDS_UPDATED"),
        "package_852341": _has("package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_852341_COMPLETED"),
        "interpretation_md": Path(
            "troubleshooting/accuracy_benchmark/CURRENT_23_4_SCORE_INTERPRETATION.md"
        ).exists(),
        "no_new_auto_corrections": not _has(
            "alpha/transcription/japanese_business_accuracy.py",
            "BUSINESS_ACCURACY_EXPANSION_85222_ENABLED = True",
        ),
        "no_deepl": not _has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not _has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not _has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization_active": not _has("alpha/constants.py", 'diarize_model = "'),
        "no_ui_redesign": not _has("alpha/constants.py", "UI_REDESIGN_ENABLED = True"),
        "latest_live_alpha_exists": Path("troubleshooting/latest/latest_live_alpha_output.txt").exists(),
    }
    warnings: list[str] = []

    for mod in (
        "analyze_alpha_vs_reference.py",
        "score_latest_accuracy.py",
        "alpha/utils/accuracy_report_sync.py",
        "package_latest_troubleshooting_run.py",
    ):
        try:
            import py_compile

            py_compile.compile(mod, doraise=True)
            checks[f"compile_{mod.replace('/', '_').replace('.', '_')}"] = True
        except Exception:
            checks[f"compile_{mod.replace('/', '_').replace('.', '_')}"] = False

    try:
        from alpha.utils.accuracy_report_sync import sync_latest_accuracy_reports

        sync_latest_accuracy_reports()
        checks["report_sync_runs"] = True
    except Exception:
        checks["report_sync_runs"] = False

    latest_index = Path("troubleshooting/latest/latest_accuracy_evidence_index.json")
    if latest_index.exists():
        try:
            idx = json.loads(latest_index.read_text(encoding="utf-8"))
            checks["index_v2_fields"] = all(
                k in idx
                for k in (
                    "alignment_integrity_verdict_v2",
                    "unaligned_alpha_char_ratio",
                    "final_trusted_score",
                    "score_should_be_used_for_decision",
                    "evidence_index_sync_after_scoring_enabled",
                )
            )
            if idx.get("translation_ready_ratio", 1.0) < 0.8:
                warnings.append("translation_ready_ratio_below_80")
            if not idx.get("score_should_be_used_for_decision"):
                warnings.append("score_not_trusted_yet")
        except Exception:
            checks["index_v2_fields"] = False

    report_set = Path("troubleshooting/accuracy_benchmark/latest_reports/LATEST_REPORT_SET_INDEX.json")
    if report_set.exists():
        try:
            rs = json.loads(report_set.read_text(encoding="utf-8"))
            checks["latest_report_set_index"] = "report_hashes" in rs and "alignment_algorithm_version" in rs
            if not rs.get("report_set_consistent"):
                warnings.append("latest_report_set_inconsistent")
        except Exception:
            checks["latest_report_set_index"] = False
    else:
        checks["latest_report_set_index"] = False
        warnings.append("no_latest_report_set_index")

    align_latest = Path("troubleshooting/accuracy_benchmark/latest_reports/latest_alignment_report.json")
    if align_latest.exists():
        try:
            align = json.loads(align_latest.read_text(encoding="utf-8"))
            checks["alignment_v2_in_output"] = str(align.get("alignment_algorithm_version", "")).startswith("v2")
            if not checks["alignment_v2_in_output"]:
                warnings.append("alignment_v2_not_in_latest_report")
        except Exception:
            checks["alignment_v2_in_output"] = False

    if not Path("troubleshooting/accuracy_benchmark/reference_transcripts/business_18min_test01.txt").exists():
        warnings.append("no_18min_business_test_run_yet")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.23.4.1 ALIGNMENT COVERAGE REPAIR VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_852341_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
