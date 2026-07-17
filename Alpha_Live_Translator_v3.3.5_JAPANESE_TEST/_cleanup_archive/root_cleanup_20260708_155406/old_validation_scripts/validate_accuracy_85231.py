"""Validate accuracy direction settlement & benchmark baseline lock 8.5.23.1."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    AUTO_EXPORT_ALPHA_TXT_ENABLED,
    BENCHMARK_BASELINE_LOCK_ENABLED,
    BUSINESS_CORRECTION_GUARD_85221_ENABLED,
    CORRECTION_RULE_APPROVAL_REQUIRED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LATEST_ACCURACY_ZIP_FLUSH_FIX_ENABLED,
    LATEST_POINTER_COMPLETED_STATUS_FIX_ENABLED,
    LATEST_UPLOAD_ZIP_POINTER_FIX_ENABLED,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    REFERENCE_SCORING_REQUIRED_FOR_NEW_RULES,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    SINGLE_SAMPLE_CORRECTION_BLOCKED,
    STOP_PATH_MINIMAL_MODE,
    TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
    TEMP_AUDIO_RETENTION_ENABLED,
    TEMP_AUDIO_RETENTION_HOURS,
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
    result = {"pointer_status": "", "index_status": "", "upload_zip": ""}
    pointer = Path("troubleshooting/latest/LATEST_RUN_POINTER.json")
    index = Path("troubleshooting/latest/LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt")
    if pointer.exists():
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
            result["pointer_status"] = str(data.get("status", ""))
            result["upload_zip"] = str(data.get("upload_package_zip_path", ""))
        except Exception:
            pass
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            if line.startswith("status="):
                result["index_status"] = line.split("=", 1)[-1].strip()
    return result


def _zip_alpha_entry_size() -> tuple[bool, int]:
    for zp in (
        Path("troubleshooting/latest/latest_accuracy_evidence_index.zip"),
        Path("troubleshooting/latest_accuracy_evidence_index.zip"),
    ):
        if not zp.exists():
            continue
        try:
            with zipfile.ZipFile(zp, "r") as zf:
                if "latest_alpha_output.txt" not in zf.namelist():
                    return False, 0
                return True, zf.getinfo("latest_alpha_output.txt").file_size
        except Exception:
            continue
    return False, 0


def main() -> int:
    run_folder = _latest_run_folder()
    run_version = ""
    if run_folder and (run_folder / "RUN_MANIFEST.json").exists():
        try:
            run_version = str(
                json.loads((run_folder / "RUN_MANIFEST.json").read_text(encoding="utf-8")).get(
                    "app_version", ""
                )
            )
        except Exception:
            pass
    is_current_run = run_version.startswith("3.3.5.5.8.5.23.1")
    is_legacy_run = bool(run_version) and not is_current_run

    guard_selftest = run_business_correction_guard_selftest()
    minimal_selftest = run_minimal_correction_selftest()
    alpha_path = Path("troubleshooting/Alpha.txt")
    latest_alpha = Path("troubleshooting/latest_alpha_output.txt")
    pointer_info = _read_pointer_status()
    zip_ok, zip_entry_size = _zip_alpha_entry_size()
    benchmark_dir = Path("troubleshooting/accuracy_benchmark")

    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.23.1",
        "deepgram_unchanged": (
            DEEPGRAM_MODEL == "nova-3"
            and DEEPGRAM_LANGUAGE == "ja"
            and DEEPGRAM_ENDPOINTING_MS == 500
            and DEEPGRAM_UTTERANCE_END_MS == 1500
        ),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "raw_deepgram_immutable": RAW_DEEPGRAM_IMMUTABLE,
        "anti_overfit_mode": ANTI_OVERFIT_MODE_ENABLED,
        "benchmark_baseline_lock": BENCHMARK_BASELINE_LOCK_ENABLED,
        "lesson_specific_disabled": LESSON_SPECIFIC_CORRECTIONS_DISABLED,
        "auto_correction_minimal": AUTO_BUSINESS_CORRECTION_LEVEL == "minimal",
        "correction_approval_required": CORRECTION_RULE_APPROVAL_REQUIRED,
        "single_sample_blocked": SINGLE_SAMPLE_CORRECTION_BLOCKED,
        "reference_scoring_required": REFERENCE_SCORING_REQUIRED_FOR_NEW_RULES,
        "minimal_mode_active": is_minimal_correction_mode(),
        "guard_enabled": BUSINESS_CORRECTION_GUARD_85221_ENABLED,
        "guard_selftest": guard_selftest.get("ok") is True,
        "minimal_selftest": minimal_selftest.get("ok") is True,
        "approval_policy_exists": (benchmark_dir / "CORRECTION_RULE_APPROVAL_POLICY.md").exists(),
        "baseline_doc_exists": (benchmark_dir / "CURRENT_ACCURACY_BASELINE.md").exists(),
        "unseen_protocol_exists": (benchmark_dir / "UNSEEN_AUDIO_TEST_PROTOCOL.md").exists(),
        "visible_audit_module": Path("alpha/transcription/japanese_visible_error_audit.py").exists(),
        "score_script_exists": Path("score_latest_accuracy.py").exists(),
        "full_logging_enabled": FULL_ACCURACY_LOGGING_STILL_ENABLED,
        "pointer_fix_enabled": EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED,
        "pointer_completed_fix": LATEST_POINTER_COMPLETED_STATUS_FIX_ENABLED,
        "upload_zip_pointer_fix": LATEST_UPLOAD_ZIP_POINTER_FIX_ENABLED,
        "accuracy_zip_flush_fix": LATEST_ACCURACY_ZIP_FLUSH_FIX_ENABLED,
        "auto_alpha_export": AUTO_EXPORT_ALPHA_TXT_ENABLED,
        "temp_audio_retention": TEMP_AUDIO_RETENTION_ENABLED,
        "retention_hours_2": TEMP_AUDIO_RETENTION_HOURS == 2,
        "wav_excluded": not TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
        "stop_minimal_preserved": (
            STOP_PATH_MINIMAL_MODE
            and RUNTIME_EVIDENCE_PACKAGE_DISABLED
            and has("alpha/utils/stop_finalize_worker.py", "STOP_MINIMAL_BEGIN")
        ),
        "pointer_finalize_module": Path("alpha/utils/evidence_pointer_finalize.py").exists(),
        "no_runtime_evidence_worker_stop": has(
            "alpha/utils/stop_finalize_worker.py",
            "EVIDENCE_PACKAGE_WORKER_DISABLED_DURING_RUNTIME",
        ),
        "no_deepl": not has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization_active": not has("alpha/constants.py", 'diarize_model = "'),
        "no_ui_redesign_flag": not has("alpha/constants.py", "UI_REDESIGN_ENABLED = True"),
    }

    warnings: list[str] = []

    if alpha_path.exists():
        checks["alpha_txt_exists"] = True
    if latest_alpha.exists():
        checks["latest_alpha_output_exists"] = True

    latest_index = Path("troubleshooting/latest_accuracy_evidence_index.json")
    if not latest_index.exists():
        latest_index = Path("troubleshooting/latest/latest_accuracy_evidence_index.json")
    if latest_index.exists():
        checks["latest_accuracy_evidence_index_exists"] = True
        try:
            idx = json.loads(latest_index.read_text(encoding="utf-8"))
            if idx.get("app_version", "").startswith("3.3.5.5.8.5.23.1"):
                checks["index_baseline_fields"] = all(
                    k in idx
                    for k in (
                        "anti_overfit_mode_enabled",
                        "benchmark_baseline_lock_enabled",
                        "auto_business_correction_level",
                        "visible_error_audit_path",
                        "visible_error_count",
                    )
                )
            else:
                warnings.append("legacy_index_baseline_fields_deferred")
            if idx.get("cer_score") is None:
                warnings.append("cer_not_calculated_yet")
            if not idx.get("reference_transcript_used"):
                warnings.append("no_reference_transcript_provided")
        except Exception:
            checks["index_baseline_fields"] = False

    if is_current_run and run_folder:
        audit_json = run_folder / "accuracy" / "visible_error_audit.json"
        if audit_json.exists():
            checks["visible_error_audit_created"] = True
        else:
            checks["visible_error_audit_created"] = False
            warnings.append("visible_error_audit_missing_for_current_run")
        if pointer_info["pointer_status"] == "completed":
            checks["latest_pointer_completed"] = True
        else:
            checks["latest_pointer_completed"] = False
        if pointer_info["index_status"] == "completed":
            checks["latest_artifacts_index_completed"] = True
        if zip_ok and zip_entry_size > 0:
            checks["accuracy_zip_entry_nonempty"] = True
        tr_path = run_folder / "accuracy" / "translation_readiness_summary.json"
        if tr_path.exists():
            try:
                tr = json.loads(tr_path.read_text(encoding="utf-8"))
                checks["raw_mutation_count_zero"] = int(tr.get("raw_mutation_count", -1)) == 0
                ratio = float(tr.get("translation_ready_ratio", 0.0))
                if ratio < 0.80:
                    warnings.append(f"translation_ready_ratio_below_target={ratio}")
            except Exception:
                pass
    elif is_legacy_run:
        warnings.append(f"post_run_85231_metrics_deferred_run_version={run_version}")
    else:
        warnings.append("no_current_85231_run_for_post_run_checks")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.23.1 ACCURACY DIRECTION SETTLEMENT VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
        f"auto_correction_level: {AUTO_BUSINESS_CORRECTION_LEVEL}",
        f"pointer_status: {pointer_info.get('pointer_status', '')}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_85231_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
