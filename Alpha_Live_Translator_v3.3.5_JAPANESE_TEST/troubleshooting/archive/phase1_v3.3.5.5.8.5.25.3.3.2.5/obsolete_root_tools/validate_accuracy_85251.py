"""Validate Lossless Clean Export & Glossary Evidence Sync 8.5.25.1."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    BOUNDARY_DECISIONS_FINALIZATION_ENABLED,
    BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ENABLED,
    CLEAN_ALPHA_EXPORT_ENABLED,
    CONSERVATIVE_DUPLICATE_SUPPRESSION_ENABLED,
    CORPORATE_IR_GLOSSARY_ENABLED,
    CORPORATE_IR_GLOSSARY_PATH,
    CORPORATE_TERM_AUDIT_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    EXPORT_COVERAGE_GATE_ENABLED,
    FINANCIAL_NUMBER_AUDIT_ENABLED,
    FINANCIAL_NUMBER_CORRECTION_ENABLED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    GLOSSARY_EVIDENCE_SYNC_FIX_ENABLED,
    GLOSSARY_KEYTERM_BOOST_ENABLED,
    GLOSSARY_KEYTERM_MAX,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LATEST_INDEX_GLOSSARY_SYNC_FIX_ENABLED,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    LOSSLESS_CLEAN_EXPORT_ENABLED,
    PUNCTUATION_ARTIFACT_CLEANUP_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    SCORE_GLOSSARY_METADATA_SYNC_ENABLED,
    STABLE_GLOSSARY_CORRECTION_ENABLED,
    STOP_PATH_MINIMAL_MODE,
    SUPPRESSION_DECISION_LOG_ENABLED,
    VALID_SEGMENT_LOSS_BLOCKER_ENABLED,
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
        "version": APP_VERSION == "3.3.5.5.8.5.25.1",
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
        "minimal_plus_glossary": AUTO_BUSINESS_CORRECTION_LEVEL == "minimal_plus_user_glossary",
        "minimal_mode": is_minimal_correction_mode(),
        "anti_overfit": ANTI_OVERFIT_MODE_ENABLED,
        "lesson_specific_disabled": LESSON_SPECIFIC_CORRECTIONS_DISABLED,
        "guard_selftest": run_business_correction_guard_selftest().get("ok") is True,
        "minimal_selftest": run_minimal_correction_selftest().get("ok") is True,
        "full_logging": FULL_ACCURACY_LOGGING_STILL_ENABLED,
        "85251_flags": all(
            (
                LOSSLESS_CLEAN_EXPORT_ENABLED,
                EXPORT_COVERAGE_GATE_ENABLED,
                VALID_SEGMENT_LOSS_BLOCKER_ENABLED,
                CONSERVATIVE_DUPLICATE_SUPPRESSION_ENABLED,
                SUPPRESSION_DECISION_LOG_ENABLED,
                GLOSSARY_EVIDENCE_SYNC_FIX_ENABLED,
                SCORE_GLOSSARY_METADATA_SYNC_ENABLED,
                LATEST_INDEX_GLOSSARY_SYNC_FIX_ENABLED,
                CORPORATE_IR_GLOSSARY_ENABLED,
                STABLE_GLOSSARY_CORRECTION_ENABLED,
                FINANCIAL_NUMBER_AUDIT_ENABLED,
                RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
                PUNCTUATION_ARTIFACT_CLEANUP_ENABLED,
                CLEAN_ALPHA_EXPORT_ENABLED,
                BOUNDARY_DECISIONS_FINALIZATION_ENABLED,
                BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ENABLED,
            )
        ),
        "glossary_file": Path(CORPORATE_IR_GLOSSARY_PATH).exists(),
        "export_coverage_module": Path("alpha/utils/clean_export_coverage.py").exists(),
        "glossary_sync_module": Path("alpha/utils/glossary_evidence_sync.py").exists(),
        "suppression_log_support": _has("alpha/utils/clean_export_coverage.py", "write_suppression_decisions"),
        "lossless_gate": _has("alpha/utils/clean_export_coverage.py", "finalize_lossless_clean_export"),
        "glossary_canonical_summary": _has("alpha/utils/glossary_evidence_sync.py", "write_glossary_correction_summary"),
        "index_export_coverage_fields": _has(
            "alpha/utils/boundary_evidence_finalize.py", "LATEST_INDEX_EXPORT_COVERAGE_FIELDS_UPDATED"
        ),
        "score_export_coverage_fields": _has("score_latest_accuracy.py", "SCORING_EXPORT_COVERAGE_METADATA_INCLUDED"),
        "package_export_coverage": _has("package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_EXPORT_COVERAGE_INCLUDED"),
        "package_suppression_decisions": _has(
            "package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_SUPPRESSION_DECISIONS_INCLUDED"
        ),
        "package_glossary_summary": _has(
            "package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_GLOSSARY_SUMMARY_INCLUDED"
        ),
        "repair_lossless_mode": _has("repair_clean_alpha_export.py", "--lossless"),
        "pipeline_integration": _has(
            "alpha/utils/boundary_evidence_finalize.py", "conservative_sweep_residual_duplicates"
        ),
        "no_deepl": not _has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not _has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not _has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization_active": not _has("alpha/constants.py", 'diarize_model = "'),
        "no_ui_redesign": not _has("alpha/constants.py", "UI_REDESIGN_ENABLED = True"),
    }

    for mod in (
        "alpha/utils/clean_export_coverage.py",
        "alpha/utils/glossary_evidence_sync.py",
        "alpha/utils/boundary_evidence_finalize.py",
        "alpha/transcription/corporate_ir_glossary.py",
        "alpha/transcription/corporate_ir_stable_corrector.py",
        "package_latest_troubleshooting_run.py",
        "score_latest_accuracy.py",
        "analyze_alpha_vs_reference.py",
        "repair_clean_alpha_export.py",
        "validate_accuracy_85251.py",
        "runtime_smoke_start_stop_85251.py",
    ):
        try:
            import py_compile

            py_compile.compile(mod, doraise=True)
            checks[f"compile_{mod.replace('/', '_')}"] = True
        except Exception:
            checks[f"compile_{mod.replace('/', '_')}"] = False

    try:
        from alpha.utils.clean_export_coverage import (
            analyze_export_coverage,
            build_lossless_export_lines,
            is_meaningful_segment,
        )

        checks["meaningful_segment"] = is_meaningful_segment("売上高は百三十六億円となりました。")
        recovered, _ = build_lossless_export_lines(
            ["line one"],
            [{"text": "売上高は百三十六億円となりました。", "source": "ui", "segment_id": "ui-1"}],
        )
        checks["lossless_recovery"] = len(recovered) >= 2
        cov = analyze_export_coverage(export_lines=recovered)
        checks["coverage_report_fields"] = "export_coverage_ratio" in cov
    except Exception:
        checks["meaningful_segment"] = False
        checks["lossless_recovery"] = False
        checks["coverage_report_fields"] = False

    warnings = [
        "no_new_live_test_after_25.1",
        "trusted_score_still_false",
        "reference_transcript_not_human_cleaned",
    ]
    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.25.1 LOSSLESS CLEAN EXPORT VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
        "Warnings: " + ", ".join(warnings),
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_85251_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
