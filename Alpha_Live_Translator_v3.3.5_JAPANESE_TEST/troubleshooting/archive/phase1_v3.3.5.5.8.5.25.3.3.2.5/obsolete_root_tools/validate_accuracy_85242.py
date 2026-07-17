"""Validate Residual Duplicate & Punctuation Cleanup 8.5.24.2."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    BOUNDARY_DECISIONS_FINALIZATION_ENABLED,
    BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ENABLED,
    BOUNDARY_EXPORT_SOURCE_UI_SEGMENTS_ENABLED,
    BOUNDARY_MERGE_REVISION_ENABLED,
    BOUNDARY_REPORT_PACKAGE_FIX_ENABLED,
    BOUNDARY_SUMMARY_PATH_FIX_ENABLED,
    CLEAN_ALPHA_EXPORT_ENABLED,
    CLEAN_ALPHA_EXPORT_REQUIRED_FOR_SCORING,
    CUMULATIVE_ALPHA_REJECTION_ENABLED,
    CUMULATIVE_MERGE_DUPLICATE_GUARD_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FINAL_CLEAN_TRANSCRIPT_PERSISTENCE_ENABLED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_BOUNDARY_STABILIZER_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    PUNCTUATION_ARTIFACT_CLEANUP_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STABLE_LINE_REVISION_MODEL_ENABLED,
    STABLE_REVISION_HISTORY_PERSISTENCE_ENABLED,
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
        "version": APP_VERSION == "3.3.5.5.8.5.24.2",
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
        "85242_flags": all(
            (
                RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
                PUNCTUATION_ARTIFACT_CLEANUP_ENABLED,
                FINAL_CLEAN_TRANSCRIPT_PERSISTENCE_ENABLED,
                STABLE_REVISION_HISTORY_PERSISTENCE_ENABLED,
                BOUNDARY_DECISIONS_FINALIZATION_ENABLED,
                BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ENABLED,
                CUMULATIVE_ALPHA_REJECTION_ENABLED,
                CLEAN_ALPHA_EXPORT_REQUIRED_FOR_SCORING,
                BOUNDARY_MERGE_REVISION_ENABLED,
                STABLE_LINE_REVISION_MODEL_ENABLED,
                CLEAN_ALPHA_EXPORT_ENABLED,
                CUMULATIVE_MERGE_DUPLICATE_GUARD_ENABLED,
                BOUNDARY_EXPORT_SOURCE_UI_SEGMENTS_ENABLED,
                BOUNDARY_REPORT_PACKAGE_FIX_ENABLED,
                BOUNDARY_SUMMARY_PATH_FIX_ENABLED,
            )
        ),
        "cleanup_module": Path("alpha/transcription/final_output_cleanup.py").exists(),
        "finalize_module": Path("alpha/utils/boundary_evidence_finalize.py").exists(),
        "stabilizer_module": Path("alpha/transcription/japanese_boundary_stabilizer.py").exists(),
        "revision_model": Path("alpha/transcription/stable_line_revision.py").exists(),
        "finalize_on_stop": _has("alpha/transcription/stable_line_revision.py", "finalize_on_stop"),
        "repair_script": Path("repair_clean_alpha_export.py").exists(),
        "package_85242": _has("package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_85242_COMPLETED"),
        "scoring_cumulative_check": _has("score_latest_accuracy.py", "detect_cumulative_alpha_lines_v2"),
        "scoring_punctuation_check": _has("score_latest_accuracy.py", "punctuation_artifact_count"),
        "analyzer_punctuation_check": _has("analyze_alpha_vs_reference.py", "punctuation_artifact_count"),
        "package_boundary_summary": _has("package_latest_troubleshooting_run.py", "boundary_summary_included"),
        "package_clean_active": _has("package_latest_troubleshooting_run.py", "clean_active_transcript_included"),
        "no_broad_word_correction": not _has(
            "alpha/transcription/japanese_business_accuracy.py",
            "BUSINESS_ACCURACY_EXPANSION_85222_ENABLED = True",
        ),
        "no_deepl": not _has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not _has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not _has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization_active": not _has("alpha/constants.py", 'diarize_model = "'),
        "no_ui_redesign": not _has("alpha/constants.py", "UI_REDESIGN_ENABLED = True"),
        "report_sync_preserved": Path("alpha/utils/accuracy_report_sync.py").exists(),
        "latest_live_alpha_exists": Path("troubleshooting/latest/latest_live_alpha_output.txt").exists(),
    }
    warnings: list[str] = []

    for mod in (
        "alpha/transcription/final_output_cleanup.py",
        "alpha/utils/boundary_evidence_finalize.py",
        "alpha/transcription/japanese_boundary_stabilizer.py",
        "alpha/transcription/stable_line_revision.py",
        "repair_clean_alpha_export.py",
        "package_latest_troubleshooting_run.py",
        "score_latest_accuracy.py",
        "analyze_alpha_vs_reference.py",
        "validate_accuracy_85242.py",
        "runtime_smoke_start_stop_85242.py",
    ):
        try:
            import py_compile

            py_compile.compile(mod, doraise=True)
            checks[f"compile_{mod.replace('/', '_')}"] = True
        except Exception:
            checks[f"compile_{mod.replace('/', '_')}"] = False

    repair_dir = Path("troubleshooting/accuracy_benchmark/clean_export_repair")
    if repair_dir.exists() and list(repair_dir.glob("*_repair_report.json")):
        checks["repair_ran"] = True
        latest = sorted(repair_dir.glob("*_repair_report.json"))[-1]
        try:
            rep = json.loads(latest.read_text(encoding="utf-8"))
            if rep.get("residual_duplicate_after_count", 999) > 0:
                warnings.append("repair_still_has_residual_duplicates")
            if rep.get("punctuation_artifact_after_count", 999) > 0:
                warnings.append("repair_still_has_punctuation_artifacts")
        except Exception:
            pass
    else:
        checks["repair_ran"] = False
        warnings.append("no_repair_run_yet")

    try:
        from alpha.transcription.final_output_cleanup import cleanup_punctuation_artifacts, sweep_residual_duplicates

        cleaned, changed = cleanup_punctuation_artifacts("三時ね。、二時半から")
        checks["punctuation_cleanup_works"] = changed and "。、" not in cleaned
        out, metrics = sweep_residual_duplicates(
            [
                "なんですよ何時から三時です。三時ね。",
                "なんですよ何時から三時です。三時ね。、二時半から部長がお使いになることになっているわね。",
            ]
        )
        checks["residual_sweep_works"] = len(out) == 1 and metrics.get("residual_duplicate_suppressed_count", 0) >= 0
    except Exception:
        checks["punctuation_cleanup_works"] = False
        checks["residual_sweep_works"] = False

    idx = Path("troubleshooting/latest/latest_accuracy_evidence_index.json")
    if idx.exists():
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            if data.get("translation_ready_ratio", 1.0) < 0.8:
                warnings.append("translation_ready_ratio_below_80")
            if not data.get("score_should_be_used_for_decision", True):
                warnings.append("cer_still_not_trusted")
        except Exception:
            pass

    warnings.append("no_new_live_test_after_24_2")
    warnings.append("reference_transcript_not_human_cleaned")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.24.2 RESIDUAL DUPLICATE & PUNCTUATION CLEANUP VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_85242_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
