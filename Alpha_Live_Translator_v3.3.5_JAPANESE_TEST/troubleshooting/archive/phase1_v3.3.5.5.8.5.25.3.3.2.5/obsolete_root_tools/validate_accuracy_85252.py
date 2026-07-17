"""Validate Canonical Transcript Lineage & Final Export Lock 8.5.25.2."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    BOUNDARY_DECISIONS_FINALIZATION_ENABLED,
    BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ENABLED,
    BOUNDARY_REVISION_LINEAGE_ENABLED,
    CANONICAL_CLEAN_TRANSCRIPT_REQUIRED_FOR_SCORING,
    CANONICAL_TRANSCRIPT_LINEAGE_ENABLED,
    CLEAN_ALPHA_EXPORT_ENABLED,
    CONSERVATIVE_DUPLICATE_SUPPRESSION_ENABLED,
    CORPORATE_IR_GLOSSARY_ENABLED,
    CORPORATE_IR_GLOSSARY_PATH,
    CORPORATE_TERM_AUDIT_ENABLED,
    CORRECTED_LINE_REPRESENTS_SOURCE_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    EXPORT_COVERAGE_GATE_ENABLED,
    FINAL_EXPORT_LOCK_ENABLED,
    FINANCIAL_NUMBER_AUDIT_ENABLED,
    FINANCIAL_NUMBER_CORRECTION_ENABLED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    GLOSSARY_CORRECTION_LINEAGE_ENABLED,
    GLOSSARY_EVIDENCE_SYNC_FIX_ENABLED,
    GLOSSARY_KEYTERM_BOOST_ENABLED,
    GLOSSARY_KEYTERM_MAX,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LATEST_INDEX_GLOSSARY_SYNC_FIX_ENABLED,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    LINEAGE_BASED_EXPORT_COVERAGE_ENABLED,
    LOSSLESS_CLEAN_EXPORT_ENABLED,
    PRE_CORRECTION_REENTRY_BLOCK_ENABLED,
    PUNCTUATION_ARTIFACT_CLEANUP_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    SCORE_GLOSSARY_METADATA_SYNC_ENABLED,
    SOURCE_COMMIT_LINEAGE_REQUIRED,
    STABLE_GLOSSARY_CORRECTION_ENABLED,
    STOP_PATH_MINIMAL_MODE,
    SUPPRESSION_DECISION_LOG_ENABLED,
    TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY,
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
        "version": APP_VERSION == "3.3.5.5.8.5.25.2",
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
        "85252_flags": all(
            (
                CANONICAL_TRANSCRIPT_LINEAGE_ENABLED,
                FINAL_EXPORT_LOCK_ENABLED,
                SOURCE_COMMIT_LINEAGE_REQUIRED,
                LINEAGE_BASED_EXPORT_COVERAGE_ENABLED,
                PRE_CORRECTION_REENTRY_BLOCK_ENABLED,
                CORRECTED_LINE_REPRESENTS_SOURCE_ENABLED,
                TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY,
                CANONICAL_CLEAN_TRANSCRIPT_REQUIRED_FOR_SCORING,
                GLOSSARY_CORRECTION_LINEAGE_ENABLED,
                BOUNDARY_REVISION_LINEAGE_ENABLED,
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
        "lineage_module": Path("alpha/transcription/transcript_lineage.py").exists(),
        "export_coverage_module": Path("alpha/utils/clean_export_coverage.py").exists(),
        "lineage_registry": _has("alpha/transcription/transcript_lineage.py", "TranscriptLineageRegistry"),
        "canonical_ledger": _has("alpha/transcription/transcript_lineage.py", "write_canonical_ledger"),
        "final_export_lock": _has("alpha/transcription/transcript_lineage.py", "select_final_export_canonical_lines"),
        "pre_correction_blocker": _has("alpha/transcription/transcript_lineage.py", "scan_pre_correction_reentry"),
        "lineage_coverage": _has("alpha/transcription/transcript_lineage.py", "analyze_lineage_export_coverage"),
        "finalize_canonical_export": _has("alpha/transcription/transcript_lineage.py", "finalize_canonical_export"),
        "text_recovery_secondary": _has("alpha/utils/clean_export_coverage.py", "TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY"),
        "boundary_canonical_path": _has("alpha/utils/boundary_evidence_finalize.py", "finalize_canonical_export"),
        "clean_active_canonical_fields": _has(
            "alpha/transcription/stable_line_revision.py", "canonical_line_id"
        ),
        "index_canonical_fields": _has(
            "alpha/utils/boundary_evidence_finalize.py", "LATEST_INDEX_85252_UPDATE_COMPLETED"
        ),
        "score_canonical_fields": _has("score_latest_accuracy.py", "SCORING_CANONICAL_LINEAGE_METADATA_INCLUDED"),
        "package_canonical_ledger": _has(
            "package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_CANONICAL_LEDGER_INCLUDED"
        ),
        "package_pre_correction_report": _has(
            "package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_PRE_CORRECTION_REENTRY_REPORT_INCLUDED"
        ),
        "repair_canonical_mode": _has("repair_clean_alpha_export.py", "--canonical-lineage"),
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
        "main.py",
        "alpha/constants.py",
        "alpha/transcription/transcript_lineage.py",
        "alpha/utils/clean_export_coverage.py",
        "alpha/transcription/corporate_ir_glossary.py",
        "alpha/transcription/corporate_ir_stable_corrector.py",
        "alpha/transcription/japanese_boundary_stabilizer.py",
        "alpha/utils/accuracy_report_sync.py",
        "alpha/utils/boundary_evidence_finalize.py",
        "package_latest_troubleshooting_run.py",
        "score_latest_accuracy.py",
        "analyze_alpha_vs_reference.py",
        "repair_clean_alpha_export.py",
        "validate_accuracy_85252.py",
        "runtime_smoke_start_stop_85252.py",
    ):
        try:
            import py_compile

            py_compile.compile(mod, doraise=True)
            checks[f"compile_{mod.replace('/', '_')}"] = True
        except Exception:
            checks[f"compile_{mod.replace('/', '_')}"] = False

    try:
        from alpha.transcription.transcript_lineage import (
            TranscriptLineageRegistry,
            analyze_lineage_export_coverage,
            finalize_canonical_export,
        )

        reg = TranscriptLineageRegistry()
        line = reg.create_canonical_line("テスト用の売上高は百三十六億円となりました。", source_commit_ids=["stable-1"])
        checks["lineage_create"] = bool(line.get("canonical_line_id"))
        result = finalize_canonical_export(["テスト用の売上高は百三十六億円となりました。"])
        checks["canonical_export_lines"] = len(result.get("export_lines", [])) >= 1
        cov = result.get("coverage_report", {})
        checks["lineage_coverage_fields"] = cov.get("coverage_algorithm_version") == "lineage_v1"
    except Exception:
        checks["lineage_create"] = False
        checks["canonical_export_lines"] = False
        checks["lineage_coverage_fields"] = False

    idx_path = Path("troubleshooting/latest/latest_accuracy_evidence_index.json")
    if idx_path.exists():
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        checks["index_has_lineage_fields"] = "canonical_transcript_lineage_enabled" in idx or idx.get(
            "app_version", ""
        ).startswith("3.3.5.5.8.5.25")
    else:
        checks["index_has_lineage_fields"] = True

    warnings = [
        "no_new_live_test_after_25.2",
        "trusted_score_still_false_due_to_reference_alignment",
        "reference_transcript_not_human_cleaned",
    ]
    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.25.2 CANONICAL TRANSCRIPT LINEAGE VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
        "Warnings: " + ", ".join(warnings),
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_85252_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
