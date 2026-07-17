"""Validate business CER benchmark integrity 8.5.23.4."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    BUSINESS_CER_BENCHMARK_INTEGRITY_ENABLED,
    BUSINESS_VIDEO_TEST_PROTOCOL_ENABLED,
    CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LATEST_ANALYZER_REPORT_SYNC_ENABLED,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    REFERENCE_ALPHA_HASH_BINDING_ENABLED,
    REPORT_SYNCHRONIZATION_85234_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STOP_PATH_MINIMAL_MODE,
    UPLOAD_PACKAGE_ANALYZER_REPORTS_ENABLED,
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
        "version": APP_VERSION == "3.3.5.5.8.5.23.4",
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
        "anti_overfit": ANTI_OVERFIT_MODE_ENABLED,
        "minimal_level": AUTO_BUSINESS_CORRECTION_LEVEL == "minimal" and is_minimal_correction_mode(),
        "lesson_specific_disabled": LESSON_SPECIFIC_CORRECTIONS_DISABLED,
        "guard_selftest": run_business_correction_guard_selftest().get("ok") is True,
        "minimal_selftest": run_minimal_correction_selftest().get("ok") is True,
        "full_logging": FULL_ACCURACY_LOGGING_STILL_ENABLED,
        "85234_flags": all(
            (
                BUSINESS_CER_BENCHMARK_INTEGRITY_ENABLED,
                REPORT_SYNCHRONIZATION_85234_ENABLED,
                CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE,
                LATEST_ANALYZER_REPORT_SYNC_ENABLED,
                UPLOAD_PACKAGE_ANALYZER_REPORTS_ENABLED,
                BUSINESS_VIDEO_TEST_PROTOCOL_ENABLED,
                REFERENCE_ALPHA_HASH_BINDING_ENABLED,
            )
        ),
        "report_sync_module": Path("alpha/utils/accuracy_report_sync.py").exists(),
        "hash_binding_module": Path("alpha/utils/reference_alpha_hash.py").exists(),
        "score_alignment_fields": _has("score_latest_accuracy.py", "alignment_coverage_verdict"),
        "analyze_integrity_fields": _has("analyze_alpha_vs_reference.py", "alignment_integrity_verdict"),
        "hash_binding_in_score": _has("score_latest_accuracy.py", "alpha_sha256"),
        "latest_reports_folder": Path("troubleshooting/accuracy_benchmark/latest_reports").exists(),
        "protocol_md": Path("troubleshooting/accuracy_benchmark/BUSINESS_18MIN_CER_TEST_PROTOCOL.md").exists(),
        "manifest_script": Path("create_benchmark_manifest.py").exists(),
        "package_analyzer_reports": _has("package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_ANALYZER_REPORTS"),
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
            checks["index_85234_fields"] = all(
                k in idx
                for k in (
                    "business_cer_benchmark_integrity_enabled",
                    "report_synchronization_85234_enabled",
                    "cer_trust_requires_alignment_coverage",
                    "latest_report_sync_status",
                    "latest_score_report_path",
                    "latest_alignment_report_json_path",
                )
            )
            if idx.get("translation_ready_ratio", 1.0) < 0.8:
                warnings.append("translation_ready_ratio_below_80")
            if not idx.get("score_should_be_used_for_decision"):
                warnings.append("no_trusted_cer_yet_after_alignment_coverage")
        except Exception:
            checks["index_85234_fields"] = False

    if not Path("troubleshooting/accuracy_benchmark/reference_transcripts/business_18min_test01.txt").exists():
        warnings.append("no_18min_business_test_run_yet")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.23.4 BUSINESS CER BENCHMARK INTEGRITY VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_85234_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
