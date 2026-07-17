"""Validate accuracy regression rollback & safe correction gate 8.5.22.3."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from alpha.constants import (
    ACCURACY_BASELINE_85221_PRESERVED,
    ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED,
    APP_VERSION,
    AUTO_EXPORT_ALPHA_TXT_ENABLED,
    BUSINESS_ACCURACY_EXPANSION_85222_ENABLED,
    BUSINESS_CORRECTION_GUARD_85221_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    DISABLE_HARMFUL_85222_EXPANSION_RULES,
    DISABLE_POLITE_CLOSING_ZO_PREFIX,
    EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED,
    EVIDENCE_POINTER_FIXES_FROM_85222_PRESERVED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LATEST_ACCURACY_ZIP_FLUSH_FIX_ENABLED,
    LATEST_POINTER_COMPLETED_STATUS_FIX_ENABLED,
    LATEST_UPLOAD_ZIP_POINTER_FIX_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    SAFE_CORRECTION_GATE_ENABLED,
    STOP_PATH_MINIMAL_MODE,
    STRICT_IDEMPOTENT_CORRECTIONS_ONLY,
    TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
    TEMP_AUDIO_RETENTION_ENABLED,
    TEMP_AUDIO_RETENTION_HOURS,
)
from alpha.transcription.japanese_business_accuracy import (
    run_business_correction_guard_selftest,
    run_safe_correction_85223_selftest,
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


def _count_punctuation_start_lines(alpha_text: str) -> int:
    count = 0
    for line in alpha_text.splitlines():
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line.strip())
        if not m:
            continue
        body = m.group(1).strip()
        if body.startswith("、") or body.startswith("。"):
            count += 1
    return count


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
    is_current_run = run_version.startswith("3.3.5.5.8.5.22.3")
    is_legacy_run = bool(run_version) and not is_current_run

    guard_selftest = run_business_correction_guard_selftest()
    safe_selftest = run_safe_correction_85223_selftest()
    alpha_path = Path("troubleshooting/Alpha.txt")
    latest_alpha = Path("troubleshooting/latest_alpha_output.txt")
    alpha_body = alpha_path.read_text(encoding="utf-8") if alpha_path.exists() else ""
    pointer_info = _read_pointer_status()
    zip_ok, zip_entry_size = _zip_alpha_entry_size()
    line_count = len([ln for ln in alpha_body.splitlines() if ln.strip() and ln.startswith("[Speaker")])

    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.22.3",
        "deepgram_unchanged": (
            DEEPGRAM_MODEL == "nova-3"
            and DEEPGRAM_LANGUAGE == "ja"
            and DEEPGRAM_ENDPOINTING_MS == 500
            and DEEPGRAM_UTTERANCE_END_MS == 1500
        ),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "raw_deepgram_immutable": RAW_DEEPGRAM_IMMUTABLE,
        "rollback_enabled": ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED,
        "safe_correction_gate_enabled": SAFE_CORRECTION_GATE_ENABLED,
        "harmful_expansion_disabled": not BUSINESS_ACCURACY_EXPANSION_85222_ENABLED,
        "disable_harmful_rules": DISABLE_HARMFUL_85222_EXPANSION_RULES,
        "disable_polite_zo_prefix": DISABLE_POLITE_CLOSING_ZO_PREFIX,
        "strict_idempotent": STRICT_IDEMPOTENT_CORRECTIONS_ONLY,
        "evidence_pointer_preserved": EVIDENCE_POINTER_FIXES_FROM_85222_PRESERVED,
        "accuracy_baseline_85221": ACCURACY_BASELINE_85221_PRESERVED,
        "pointer_fix_enabled": EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED,
        "pointer_completed_fix": LATEST_POINTER_COMPLETED_STATUS_FIX_ENABLED,
        "upload_zip_pointer_fix": LATEST_UPLOAD_ZIP_POINTER_FIX_ENABLED,
        "accuracy_zip_flush_fix": LATEST_ACCURACY_ZIP_FLUSH_FIX_ENABLED,
        "full_logging_enabled": FULL_ACCURACY_LOGGING_STILL_ENABLED,
        "guard_enabled": BUSINESS_CORRECTION_GUARD_85221_ENABLED,
        "guard_selftest": guard_selftest.get("ok") is True,
        "safe_correction_selftest": safe_selftest.get("ok") is True,
        "safe_gate_in_code": has(
            "alpha/transcription/japanese_business_accuracy.py",
            "def safe_correction_gate",
        ),
        "doudouzo_repair_in_code": has(
            "alpha/transcription/japanese_business_accuracy.py",
            "DOUDOZO_REGRESSION_REPAIRED",
        ),
        "fake_reclass_blocked_in_code": has(
            "alpha/transcription/japanese_sentence_assembler.py",
            "TRANSLATION_READY_FAKE_RECLASSIFICATION_BLOCKED",
        ),
        "correction_logs_raw_immutable": has(
            "alpha/transcription/japanese_business_accuracy.py",
            "raw_deepgram_mutated=False",
        ),
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
        "pointer_finalize_scheduled": has(
            "alpha/utils/stop_finalize_worker.py",
            "schedule_evidence_pointer_finalization_background",
        ),
        "accuracy_zip_writer": has(
            "alpha/utils/accuracy_evidence_export.py",
            "write_latest_accuracy_evidence_zip",
        ),
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

    if is_current_run:
        checks["no_double_osewa"] = "おお世話" not in alpha_body
        checks["no_triple_koko"] = "こここは注意" not in alpha_body
        checks["no_doudouzo"] = "どうどうぞ" not in alpha_body
        checks["no_itsuitsumo"] = "いついつも" not in alpha_body
        checks["no_konokonotabi"] = "このこのたび" not in alpha_body
        punct_count = _count_punctuation_start_lines(alpha_body)
        checks["punctuation_start_count_zero"] = punct_count == 0
        if pointer_info["pointer_status"] == "completed":
            checks["latest_pointer_completed"] = True
        else:
            checks["latest_pointer_completed"] = False
            warnings.append(f"pointer_status={pointer_info['pointer_status'] or 'missing'}")
        if pointer_info["index_status"] == "completed":
            checks["latest_artifacts_index_completed"] = True
        else:
            checks["latest_artifacts_index_completed"] = False
            warnings.append(f"index_status={pointer_info['index_status'] or 'missing'}")
        upload_zips = list((run_folder / "upload_package").glob("UPLOAD_PACKAGE_*.zip")) if run_folder else []
        if upload_zips and pointer_info["upload_zip"]:
            checks["upload_zip_path_populated"] = True
        elif not upload_zips:
            checks["upload_zip_path_populated"] = True
            warnings.append("upload_zip_not_created_yet_offline_package_pending")
        else:
            checks["upload_zip_path_populated"] = False
        if line_count > 0:
            if zip_ok and zip_entry_size > 0:
                checks["accuracy_zip_entry_nonempty"] = True
            else:
                checks["accuracy_zip_entry_nonempty"] = False
                warnings.append(f"accuracy_zip_entry_size={zip_entry_size}")
    elif is_legacy_run:
        warnings.append(f"post_run_85223_metrics_deferred_run_version={run_version}")
        if pointer_info["pointer_status"] == "in_progress":
            warnings.append("legacy_pointer_in_progress_retest_required_85223")
    else:
        warnings.append("no_current_85223_run_for_post_run_checks")

    if run_folder and is_current_run:
        checks["accuracy_index_exists"] = (
            run_folder / "accuracy" / "ACCURACY_EVIDENCE_INDEX.json"
        ).exists()
        checks["accuracy_alpha_exists"] = (
            run_folder / "accuracy" / "Alpha_for_accuracy_check.txt"
        ).exists()
        manifest = run_folder / "audio_temp" / "audio_manifest.json"
        if manifest.exists():
            checks["audio_manifest_exists"] = True
            checks["audio_summary_exists"] = (
                run_folder / "audio_temp" / "audio_temp_summary.txt"
            ).exists()
        tr_path = run_folder / "accuracy" / "translation_readiness_summary.json"
        if tr_path.exists() and is_current_run:
            try:
                tr = json.loads(tr_path.read_text(encoding="utf-8"))
                checks["translation_summary_85223_fields"] = all(
                    k in tr
                    for k in (
                        "exact_duplicate_continuation_count",
                        "accuracy_regression_rollback_85223",
                        "safe_correction_gate_enabled",
                        "harmful_85222_expansion_disabled",
                        "evidence_pointer_fix_preserved",
                        "latest_accuracy_zip_fix_preserved",
                        "name_correction_count",
                        "name_correction_skipped_count",
                        "latest_pointer_status_fixed",
                    )
                )
                checks["raw_mutation_count_zero"] = int(tr.get("raw_mutation_count", -1)) == 0
                ratio = float(tr.get("translation_ready_ratio", 0.0))
                if is_current_run and ratio < 0.80:
                    warnings.append(f"translation_ready_ratio_below_target={ratio}")
            except Exception:
                checks["translation_summary_85223_fields"] = False

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.22.3 ACCURACY REGRESSION ROLLBACK VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
        f"pointer_status: {pointer_info.get('pointer_status', '')}",
        f"index_status: {pointer_info.get('index_status', '')}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    if not safe_selftest.get("ok"):
        lines.append(
            "Safe correction selftest failures: "
            + "; ".join(safe_selftest.get("failures", []))
        )

    out = Path("troubleshooting/validation/validate_accuracy_85223_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
