"""Validate Corporate IR Glossary & Number Accuracy Layer 8.5.25."""

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
    CORPORATE_IR_GLOSSARY_ENABLED,
    CORPORATE_IR_GLOSSARY_PATH,
    CORPORATE_TERM_AUDIT_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FINANCIAL_NUMBER_AUDIT_ENABLED,
    FINANCIAL_NUMBER_CORRECTION_ENABLED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    GLOSSARY_KEYTERM_BOOST_ENABLED,
    GLOSSARY_KEYTERM_MAX,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    PUNCTUATION_ARTIFACT_CLEANUP_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STABLE_GLOSSARY_CORRECTION_ENABLED,
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
        "version": APP_VERSION == "3.3.5.5.8.5.25",
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
        "8525_flags": all(
            (
                CORPORATE_IR_GLOSSARY_ENABLED,
                GLOSSARY_KEYTERM_BOOST_ENABLED,
                STABLE_GLOSSARY_CORRECTION_ENABLED,
                FINANCIAL_NUMBER_AUDIT_ENABLED,
                FINANCIAL_NUMBER_CORRECTION_ENABLED,
                CORPORATE_TERM_AUDIT_ENABLED,
                RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
                PUNCTUATION_ARTIFACT_CLEANUP_ENABLED,
                CLEAN_ALPHA_EXPORT_ENABLED,
                BOUNDARY_DECISIONS_FINALIZATION_ENABLED,
                BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ENABLED,
            )
        ),
        "glossary_file": Path(CORPORATE_IR_GLOSSARY_PATH).exists(),
        "glossary_loader": Path("alpha/transcription/corporate_ir_glossary.py").exists(),
        "stable_corrector": Path("alpha/transcription/corporate_ir_stable_corrector.py").exists(),
        "pipeline_integration": _has("alpha/utils/boundary_evidence_finalize.py", "apply_corporate_ir_stable_corrections"),
        "deepgram_glossary_boost": _has("alpha/transcription/deepgram_client.py", "DEEPGRAM_GLOSSARY_KEYTERMS_APPLIED"),
        "package_glossary": _has("package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_GLOSSARY_INCLUDED"),
        "scoring_glossary": _has("score_latest_accuracy.py", "SCORING_GLOSSARY_LAYER_METADATA_INCLUDED"),
        "no_deepl": not _has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not _has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not _has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization_active": not _has("alpha/constants.py", 'diarize_model = "'),
        "no_ui_redesign": not _has("alpha/constants.py", "UI_REDESIGN_ENABLED = True"),
    }
    warnings: list[str] = []

    for mod in (
        "alpha/transcription/corporate_ir_glossary.py",
        "alpha/transcription/corporate_ir_stable_corrector.py",
        "alpha/utils/boundary_evidence_finalize.py",
        "package_latest_troubleshooting_run.py",
        "score_latest_accuracy.py",
        "analyze_alpha_vs_reference.py",
        "validate_accuracy_8525.py",
        "runtime_smoke_start_stop_8525.py",
    ):
        try:
            import py_compile

            py_compile.compile(mod, doraise=True)
            checks[f"compile_{mod.replace('/', '_')}"] = True
        except Exception:
            checks[f"compile_{mod.replace('/', '_')}"] = False

    try:
        from alpha.transcription.corporate_ir_glossary import (
            build_deepgram_keyterms_from_glossary,
            load_corporate_ir_glossary,
            validate_corporate_ir_glossary,
        )

        data = load_corporate_ir_glossary()
        checks["glossary_validator"] = validate_corporate_ir_glossary(data).get("ok") is True
        kt = build_deepgram_keyterms_from_glossary(data)
        checks["keyterm_cap"] = len(kt) <= GLOSSARY_KEYTERM_MAX
    except Exception:
        checks["glossary_validator"] = False
        checks["keyterm_cap"] = False

    try:
        from alpha.transcription.corporate_ir_stable_corrector import apply_corporate_ir_stable_corrections

        out = apply_corporate_ir_stable_corrections(
            ["さくら作プラスは既存円の工程価格についてご案内します。"],
            run_folder=Path("troubleshooting/validation/_glossary_test_run"),
        )
        corrected = out["lines"][0]
        checks["stable_corrector_works"] = "さくらさくプラス" in corrected or "既存園" in corrected
    except Exception:
        checks["stable_corrector_works"] = False

    warnings.append("no_new_live_test_after_25")
    warnings.append("trusted_score_still_false_expected")
    warnings.append("reference_transcript_not_human_cleaned")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.25 CORPORATE IR GLOSSARY VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_8525_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
