"""Validate evidence protection & audit coverage improvement 8.5.23.2."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    AUTO_EXPORT_ALPHA_TXT_ENABLED,
    CER_REFERENCE_VALIDATION_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    EVIDENCE_PROTECTION_85232_ENABLED,
    EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LATEST_ACCURACY_ZIP_FLUSH_FIX_ENABLED,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    REAL_LIVE_ALPHA_PROTECTION_ENABLED,
    REFERENCE_TRANSCRIPT_QUALITY_CHECK_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED,
    STOP_PATH_MINIMAL_MODE,
    TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
    TEMP_AUDIO_RETENTION_ENABLED,
    TEMP_AUDIO_RETENTION_HOURS,
    VISIBLE_ERROR_AUDIT_EXPANDED,
)
from alpha.transcription.japanese_business_accuracy import (
    is_minimal_correction_mode,
    run_business_correction_guard_selftest,
    run_minimal_correction_selftest,
)


def has(path: str, token: str) -> bool:
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def _latest_run_folder() -> Path | None:
    runs = Path("troubleshooting/runs")
    if not runs.exists():
        return None
    candidates = [p for p in runs.iterdir() if p.is_dir() and p.name != "_pending"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _read_pointer_status() -> dict[str, str]:
    result = {"pointer_status": "", "index_status": ""}
    pointer = Path("troubleshooting/latest/LATEST_RUN_POINTER.json")
    index = Path("troubleshooting/latest/LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt")
    if pointer.exists():
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
            result["pointer_status"] = str(data.get("status", ""))
        except Exception:
            pass
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            if line.startswith("status="):
                result["index_status"] = line.split("=", 1)[-1].strip()
    return result


def main() -> int:
    run_folder = _latest_run_folder()
    pointer_info = _read_pointer_status()
    latest_live = Path("troubleshooting/latest/latest_live_alpha_output.txt")
    per_run_alpha = None
    if run_folder:
        per_run_alpha = run_folder / "transcripts" / "Alpha output.txt"

    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.23.2",
        "deepgram_unchanged": (
            DEEPGRAM_MODEL == "nova-3"
            and DEEPGRAM_LANGUAGE == "ja"
            and DEEPGRAM_ENDPOINTING_MS == 500
            and DEEPGRAM_UTTERANCE_END_MS == 1500
        ),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "raw_deepgram_immutable": RAW_DEEPGRAM_IMMUTABLE,
        "evidence_protection_enabled": EVIDENCE_PROTECTION_85232_ENABLED,
        "real_live_alpha_protection": REAL_LIVE_ALPHA_PROTECTION_ENABLED,
        "smoke_overwrite_blocked": SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED,
        "visible_audit_expanded": VISIBLE_ERROR_AUDIT_EXPANDED,
        "reference_quality_check": REFERENCE_TRANSCRIPT_QUALITY_CHECK_ENABLED,
        "cer_reference_validation": CER_REFERENCE_VALIDATION_ENABLED,
        "anti_overfit_mode": ANTI_OVERFIT_MODE_ENABLED,
        "auto_correction_minimal": AUTO_BUSINESS_CORRECTION_LEVEL == "minimal",
        "lesson_specific_disabled": LESSON_SPECIFIC_CORRECTIONS_DISABLED,
        "minimal_mode_active": is_minimal_correction_mode(),
        "guard_selftest": run_business_correction_guard_selftest().get("ok") is True,
        "minimal_selftest": run_minimal_correction_selftest().get("ok") is True,
        "alpha_protection_module": Path("alpha/utils/alpha_output_protection.py").exists(),
        "reference_quality_script": Path("reference_transcript_quality_check.py").exists(),
        "score_has_validation_fields": all(
            tok in Path("score_latest_accuracy.py").read_text(encoding="utf-8")
            for tok in ("trusted_score", "reference_quality_verdict", "CER_REFERENCE_VALIDATION")
        ),
        "audit_expanded_fields": all(
            tok in Path("alpha/transcription/japanese_visible_error_audit.py").read_text(encoding="utf-8")
            for tok in (
                "visible_error_high_count",
                "name_risk_count",
                "sentence_boundary_risk_count",
            )
        ),
        "auto_alpha_export": AUTO_EXPORT_ALPHA_TXT_ENABLED,
        "temp_audio_retention": TEMP_AUDIO_RETENTION_ENABLED,
        "retention_hours_2": TEMP_AUDIO_RETENTION_HOURS == 2,
        "wav_excluded": not TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
        "stop_minimal_preserved": STOP_PATH_MINIMAL_MODE and RUNTIME_EVIDENCE_PACKAGE_DISABLED,
        "pointer_fix_enabled": EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED,
        "accuracy_zip_flush_fix": LATEST_ACCURACY_ZIP_FLUSH_FIX_ENABLED,
        "full_logging_enabled": FULL_ACCURACY_LOGGING_STILL_ENABLED,
        "no_deepl": not has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization_active": not has("alpha/constants.py", 'diarize_model = "'),
        "no_ui_redesign_flag": not has("alpha/constants.py", "UI_REDESIGN_ENABLED = True"),
        "no_broad_correction_added": not has(
            "alpha/transcription/japanese_business_accuracy.py",
            "BUSINESS_ACCURACY_EXPANSION_85222_ENABLED = True",
        ),
    }

    warnings: list[str] = []

    if latest_live.exists() and latest_live.stat().st_size > 0:
        checks["latest_live_alpha_output_exists"] = True
    elif per_run_alpha and per_run_alpha.exists() and per_run_alpha.stat().st_size > 0:
        warnings.append("latest_live_alpha_output_pending_next_live_stop")
        checks["latest_live_alpha_output_configured"] = True
    else:
        checks["latest_live_alpha_output_configured"] = has(
            "alpha/utils/alpha_output_protection.py", "latest_live_alpha_output.txt"
        )

    latest_index = Path("troubleshooting/latest_accuracy_evidence_index.json")
    if not latest_index.exists():
        latest_index = Path("troubleshooting/latest/latest_accuracy_evidence_index.json")
    if latest_index.exists():
        checks["latest_accuracy_evidence_index_exists"] = True
        try:
            idx = json.loads(latest_index.read_text(encoding="utf-8"))
            if idx.get("app_version", "").startswith("3.3.5.5.8.5.23.2"):
                checks["index_85232_fields"] = all(
                    k in idx
                    for k in (
                        "evidence_protection_enabled",
                        "latest_live_alpha_output_path",
                        "visible_error_audit_expanded",
                        "reference_transcript_quality_check_enabled",
                    )
                )
            else:
                warnings.append("legacy_index_85232_fields_deferred")
            if idx.get("cer_score") is None:
                warnings.append("cer_not_calculated_yet")
            if not idx.get("reference_transcript_used"):
                warnings.append("no_valid_reference_transcript_provided")
        except Exception:
            checks["index_85232_fields"] = False

    if pointer_info["pointer_status"] == "completed":
        checks["latest_pointer_completed"] = True
    else:
        warnings.append(f"pointer_status={pointer_info['pointer_status'] or 'missing'}")

    smoke_dirs = list(Path("troubleshooting/smoke_tests").glob("*")) if Path("troubleshooting/smoke_tests").exists() else []
    if smoke_dirs:
        checks["smoke_test_folder_exists"] = True

    if run_folder and (run_folder / "accuracy" / "visible_error_audit.json").exists():
        try:
            audit = json.loads(
                (run_folder / "accuracy" / "visible_error_audit.json").read_text(encoding="utf-8")
            )
            checks["visible_error_audit_expanded_fields"] = all(
                k in audit for k in ("visible_error_high_count", "name_risk_count", "candidates")
            )
        except Exception:
            checks["visible_error_audit_expanded_fields"] = False

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.23.2 EVIDENCE PROTECTION VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_85232_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
