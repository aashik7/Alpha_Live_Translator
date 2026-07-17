"""Validate Japanese business accuracy evidence patch 8.5.22."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ACCURACY_EVIDENCE_MODE_ENABLED,
    APP_VERSION,
    ASSEMBLER_EXCEPTION_NO_DIRECT_COMMIT,
    AUTO_EXPORT_ALPHA_TXT_ENABLED,
    AUTO_EXPORT_ALPHA_TXT_ON_STOP,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    JAPANESE_BUSINESS_ACCURACY_8522_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STABLE_ACCURACY_FIX_ENABLED,
    JAPANESE_STT_PROFILE,
    OFFLINE_EVIDENCE_PACKAGING_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STOP_PATH_MINIMAL_MODE,
    STOP_TAIL_CLEANUP_ENABLED,
    STABLE_LAYER_BUSINESS_CORRECTION_ENABLED,
    SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA,
    TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
    TEMP_AUDIO_RETENTION_ENABLED,
    TEMP_AUDIO_RETENTION_HOURS,
    WRITE_INCOMPLETE_STOP_TAIL_TO_DEBUG_FILE,
)
from alpha.transcription.japanese_business_accuracy import apply_business_stable_corrections


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


def main() -> int:
    run_folder = _latest_run_folder()
    run_exists = run_folder is not None

    biz = apply_business_stable_corrections(
        "世話になっております。",
        nearby_context="",
    )

    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.22",
        "deepgram_unchanged": (
            DEEPGRAM_MODEL == "nova-3"
            and DEEPGRAM_LANGUAGE == "ja"
            and DEEPGRAM_ENDPOINTING_MS == 500
            and DEEPGRAM_UTTERANCE_END_MS == 1500
        ),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "raw_deepgram_immutable": RAW_DEEPGRAM_IMMUTABLE,
        "raw_logging_at_ingress": has(
            "alpha/transcription/japanese_final_chunk_stabilizer.py",
            "log_raw_deepgram_final",
        ),
        "no_raw_logging_on_publish": not has(
            "alpha/transcription/japanese_sentence_assembler.py",
            "log_raw_deepgram_final(",
        ),
        "accuracy_fix_8521_preserved": JAPANESE_STABLE_ACCURACY_FIX_ENABLED,
        "business_accuracy_8522": JAPANESE_BUSINESS_ACCURACY_8522_ENABLED,
        "auto_alpha_export": AUTO_EXPORT_ALPHA_TXT_ENABLED and AUTO_EXPORT_ALPHA_TXT_ON_STOP,
        "alpha_export_module": Path("alpha/utils/accuracy_evidence_export.py").exists(),
        "alpha_export_hook": has(
            "alpha/utils/stop_finalize_worker.py",
            "export_alpha_evidence_on_stop",
        ),
        "temp_audio_retention": TEMP_AUDIO_RETENTION_ENABLED,
        "retention_hours_2": TEMP_AUDIO_RETENTION_HOURS == 2,
        "wav_excluded_from_package": not TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
        "business_correction_layer": STABLE_LAYER_BUSINESS_CORRECTION_ENABLED,
        "business_module": Path("alpha/transcription/japanese_business_accuracy.py").exists(),
        "business_correction_works": biz.get("applied") is True,
        "stop_tail_cleanup": STOP_TAIL_CLEANUP_ENABLED,
        "suppress_stop_tail": SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA,
        "stop_tail_debug_file": WRITE_INCOMPLETE_STOP_TAIL_TO_DEBUG_FILE,
        "stop_tail_hook": has(
            "alpha/transcription/japanese_sentence_assembler.py",
            "STOP_TAIL_SUPPRESSED_FROM_ALPHA",
        ),
        "accuracy_evidence_mode": ACCURACY_EVIDENCE_MODE_ENABLED,
        "translation_readiness_8522_fields": has(
            "alpha/transcription/japanese_sentence_assembler.py",
            "business_correction_count",
        ),
        "stop_minimal_preserved": (
            STOP_PATH_MINIMAL_MODE
            and RUNTIME_EVIDENCE_PACKAGE_DISABLED
            and has("alpha/utils/stop_finalize_worker.py", "STOP_MINIMAL_BEGIN")
        ),
        "runtime_safety_logs": has(
            "alpha/utils/stop_finalize_worker.py",
            "RUNTIME_BASELINE_START_STOP_PRESERVED",
        ),
        "no_runtime_upload": has("alpha/constants.py", "NO_UPLOAD_ZIP_DURING_RUNTIME = True"),
        "offline_package_script": Path("package_latest_troubleshooting_run.py").exists(),
        "package_accuracy_folder": has(
            "package_latest_troubleshooting_run.py",
            '"accuracy"',
        ),
        "no_deepl": not has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization": JAPANESE_STT_PROFILE == "no_diarize",
        "assembler_exception_blocked": ASSEMBLER_EXCEPTION_NO_DIRECT_COMMIT,
        "offline_packaging_enabled": OFFLINE_EVIDENCE_PACKAGING_ENABLED,
    }

    warnings: list[str] = []
    post_run_checks: dict[str, bool] = {}
    if run_exists and run_folder is not None:
        run_version = ""
        manifest_path = run_folder / "RUN_MANIFEST.json"
        if manifest_path.exists():
            try:
                run_version = str(json.loads(manifest_path.read_text(encoding="utf-8")).get("app_version", ""))
            except Exception:
                pass
        is_8522_run = run_version.startswith("3.3.5.5.8.5.22") or APP_VERSION in run_version

        alpha_txt = Path("troubleshooting/Alpha.txt")
        post_run_checks["alpha_txt_after_run"] = alpha_txt.exists()
        acc_alpha = run_folder / "accuracy" / "Alpha_for_accuracy_check.txt"
        post_run_checks["accuracy_alpha_after_run"] = acc_alpha.exists()
        post_run_checks["accuracy_index_after_run"] = (
            run_folder / "accuracy" / "ACCURACY_EVIDENCE_INDEX.json"
        ).exists()
        manifest = run_folder / "audio_temp" / "audio_manifest.json"
        summary = run_folder / "audio_temp" / "audio_temp_summary.txt"
        if manifest.exists():
            post_run_checks["audio_manifest_after_run"] = True
            post_run_checks["audio_summary_after_run"] = summary.exists()
        elif is_8522_run:
            warnings.append("audio_manifest_missing_for_latest_8522_run")
        tr_path = run_folder / "accuracy" / "translation_readiness_summary.json"
        if tr_path.exists():
            try:
                tr = json.loads(tr_path.read_text(encoding="utf-8"))
                post_run_checks["translation_summary_8522_fields"] = all(
                    k in tr
                    for k in (
                        "business_correction_count",
                        "stop_tail_suppressed_count",
                        "accuracy_evidence_ready",
                    )
                )
            except Exception:
                post_run_checks["translation_summary_8522_fields"] = False
        elif is_8522_run:
            warnings.append("translation_readiness_summary_missing_for_8522_run")
        stop_tail = run_folder / "transcripts" / "incomplete_stop_tail.txt"
        alpha_body = alpha_txt.read_text(encoding="utf-8") if alpha_txt.exists() else ""
        if stop_tail.exists():
            tail_text = stop_tail.read_text(encoding="utf-8")
            post_run_checks["stop_tail_debug_written"] = bool(tail_text.strip())
            if "後任の" in tail_text and "後任の" not in alpha_body.split("#")[-1]:
                post_run_checks["incomplete_tail_not_in_alpha"] = True
            elif "後任の" in alpha_body:
                post_run_checks["incomplete_tail_not_in_alpha"] = False
        biz_log = run_folder / "logs" / "japanese_accuracy.log"
        if biz_log.exists():
            log_text = biz_log.read_text(encoding="utf-8", errors="ignore")
            if "BUSINESS_STABLE_CORRECTION_APPLIED" in log_text:
                post_run_checks["business_corrections_logged"] = (
                    "raw_deepgram_mutated" in log_text or "raw_mutated" in log_text
                )
        if not is_8522_run:
            warnings.append(
                f"post_run_checks_deferred_latest_run_version={run_version or 'unknown'}"
            )
        else:
            checks.update(post_run_checks)
    else:
        warnings.append("no_run_folder_for_post_run_checks")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.22 JAPANESE BUSINESS ACCURACY EVIDENCE VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_accuracy_8522_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
