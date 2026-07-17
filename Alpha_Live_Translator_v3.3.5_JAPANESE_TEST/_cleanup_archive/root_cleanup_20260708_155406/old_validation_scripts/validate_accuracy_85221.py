"""Validate business correction guard hotfix 8.5.22.1."""

from __future__ import annotations

import json
import re
from pathlib import Path

from alpha.constants import (
    APP_VERSION,
    AUTO_EXPORT_ALPHA_TXT_ENABLED,
    BUSINESS_CORRECTION_GUARD_85221_ENABLED,
    BUSINESS_CORRECTION_IDEMPOTENT_MODE,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_ENABLED,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    PUNCTUATION_START_POST_CORRECTION_MERGE_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STOP_PATH_MINIMAL_MODE,
    STOP_TAIL_VALIDATION_NA_FIX_ENABLED,
    TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
    TEMP_AUDIO_RETENTION_ENABLED,
    TEMP_AUDIO_RETENTION_HOURS,
)
from alpha.transcription.japanese_business_accuracy import run_business_correction_guard_selftest


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


def _evaluate_stop_tail_validation(
    run_folder: Path | None,
    alpha_body: str,
) -> dict[str, object]:
    if run_folder is None:
        return {
            "stop_tail_status": "no_run",
            "stop_tail_debug_required": False,
            "stop_tail_debug_written": "N/A",
            "incomplete_tail_not_in_alpha": True,
            "pass": True,
        }

    stop_tail = run_folder / "transcripts" / "incomplete_stop_tail.txt"
    summary_path = run_folder / "accuracy" / "translation_readiness_summary.json"
    suppressed = 0
    if summary_path.exists():
        try:
            suppressed = int(
                json.loads(summary_path.read_text(encoding="utf-8")).get(
                    "stop_tail_suppressed_count", 0
                )
            )
        except Exception:
            pass

    bare_tail = "こちらが私の後任の"
    tail_in_alpha = bare_tail in alpha_body and alpha_body.rstrip().endswith(bare_tail)

    if stop_tail.exists() and stop_tail.read_text(encoding="utf-8").strip():
        debug_written = True
        status = "suppressed"
        debug_required = True
        not_in_alpha = bare_tail not in alpha_body or not tail_in_alpha
        passed = not_in_alpha
    elif suppressed > 0:
        debug_written = stop_tail.exists()
        status = "suppressed"
        debug_required = True
        not_in_alpha = not tail_in_alpha
        passed = not_in_alpha and debug_written
    else:
        status = "no_tail_detected"
        debug_required = False
        debug_written = "N/A"
        not_in_alpha = not tail_in_alpha
        passed = not_in_alpha

    return {
        "stop_tail_status": status,
        "stop_tail_debug_required": debug_required,
        "stop_tail_debug_written": debug_written,
        "incomplete_tail_not_in_alpha": not_in_alpha,
        "pass": passed,
    }


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
    is_current_run = run_version.startswith("3.3.5.5.8.5.22.1")
    is_legacy_8522_run = run_version.startswith("3.3.5.5.8.5.22") and not is_current_run

    guard_selftest = run_business_correction_guard_selftest()
    alpha_path = Path("troubleshooting/Alpha.txt")
    alpha_body = alpha_path.read_text(encoding="utf-8") if alpha_path.exists() else ""
    stop_tail_eval = _evaluate_stop_tail_validation(run_folder, alpha_body)

    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.22.1",
        "deepgram_unchanged": (
            DEEPGRAM_MODEL == "nova-3"
            and DEEPGRAM_LANGUAGE == "ja"
            and DEEPGRAM_ENDPOINTING_MS == 500
            and DEEPGRAM_UTTERANCE_END_MS == 1500
        ),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "raw_deepgram_immutable": RAW_DEEPGRAM_IMMUTABLE,
        "guard_enabled": BUSINESS_CORRECTION_GUARD_85221_ENABLED,
        "idempotent_mode": BUSINESS_CORRECTION_IDEMPOTENT_MODE,
        "post_correction_merge": PUNCTUATION_START_POST_CORRECTION_MERGE_ENABLED,
        "stop_tail_validation_fix": STOP_TAIL_VALIDATION_NA_FIX_ENABLED,
        "timed_out_kwarg_fix": FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_ENABLED,
        "full_logging_enabled": FULL_ACCURACY_LOGGING_STILL_ENABLED,
        "guard_selftest": guard_selftest.get("ok") is True,
        "auto_alpha_export": AUTO_EXPORT_ALPHA_TXT_ENABLED,
        "temp_audio_retention": TEMP_AUDIO_RETENTION_ENABLED,
        "retention_hours_2": TEMP_AUDIO_RETENTION_HOURS == 2,
        "wav_excluded": not TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
        "stop_minimal_preserved": (
            STOP_PATH_MINIMAL_MODE
            and RUNTIME_EVIDENCE_PACKAGE_DISABLED
            and has("alpha/utils/stop_finalize_worker.py", "STOP_MINIMAL_BEGIN")
        ),
        "timed_out_fix_in_code": has(
            "alpha/utils/stop_finalize_worker.py",
            "FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_APPLIED",
        ),
        "stop_tail_validation_logic": stop_tail_eval["pass"] is True,
    }

    warnings: list[str] = []
    if stop_tail_eval.get("stop_tail_status") == "no_tail_detected":
        warnings.append("STOP_TAIL_VALIDATION_NO_TAIL_DETECTED")
        warnings.append("STOP_TAIL_VALIDATION_DEBUG_NOT_REQUIRED")
        warnings.append("STOP_TAIL_VALIDATION_FALSE_FAILURE_FIXED")
    if alpha_path.exists():
        checks["alpha_txt_exists"] = True
        if is_current_run:
            checks["no_double_osewa"] = "おお世話" not in alpha_body
            checks["no_triple_koko"] = "こここは注意" not in alpha_body
            punct_count = _count_punctuation_start_lines(alpha_body)
            checks["punctuation_start_count_zero"] = punct_count == 0
            if punct_count > 0:
                warnings.append(f"punctuation_start_lines_in_alpha={punct_count}")
        elif is_legacy_8522_run:
            if "おお世話" in alpha_body:
                warnings.append("legacy_run_contains_double_osewa_retest_required")
            punct_count = _count_punctuation_start_lines(alpha_body)
            if punct_count > 0:
                warnings.append(f"legacy_run_punctuation_start_lines={punct_count}")
    elif is_current_run:
        warnings.append("alpha_txt_missing_for_current_run")

    if run_folder and is_current_run:
        checks["accuracy_alpha_exists"] = (
            run_folder / "accuracy" / "Alpha_for_accuracy_check.txt"
        ).exists()
        checks["accuracy_index_exists"] = (
            run_folder / "accuracy" / "ACCURACY_EVIDENCE_INDEX.json"
        ).exists()
        manifest = run_folder / "audio_temp" / "audio_manifest.json"
        if manifest.exists():
            checks["audio_manifest_exists"] = True
            checks["audio_summary_exists"] = (
                run_folder / "audio_temp" / "audio_temp_summary.txt"
            ).exists()
        tr_path = run_folder / "accuracy" / "translation_readiness_summary.json"
        if tr_path.exists():
            try:
                tr = json.loads(tr_path.read_text(encoding="utf-8"))
                checks["translation_summary_85221_fields"] = all(
                    k in tr
                    for k in (
                        "double_prefix_repair_count",
                        "triple_koko_repair_count",
                        "punctuation_start_post_correction_merge_count",
                        "validation_false_failure_fixed",
                    )
                )
                if int(tr.get("raw_mutation_count", -1)) != 0:
                    checks["raw_mutation_count_zero"] = False
                else:
                    checks["raw_mutation_count_zero"] = True
            except Exception:
                checks["translation_summary_85221_fields"] = False
        freeze_log = run_folder / "logs" / "freeze_guard.log"
        if freeze_log.exists():
            fl = freeze_log.read_text(encoding="utf-8", errors="ignore")
            checks["no_duplicate_timed_out_exception"] = (
                "got multiple values for keyword argument 'timed_out'" not in fl
            )
    elif run_folder and is_legacy_8522_run:
        warnings.append("post_run_85221_metrics_deferred_until_retest")
    elif run_folder:
        warnings.append(f"post_run_checks_deferred_run_version={run_version or 'unknown'}")

    checks["no_deepl"] = not has("alpha/constants.py", "DEEPL_ENABLED = True")
    checks["no_groq"] = not has("alpha/constants.py", "GROQ_ENABLED = True")
    checks["no_meetingbaas"] = not has("alpha/constants.py", "MEETINGBAAS_ENABLED = True")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.22.1 BUSINESS CORRECTION GUARD VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
        f"stop_tail_status: {stop_tail_eval.get('stop_tail_status')}",
        f"stop_tail_debug_written: {stop_tail_eval.get('stop_tail_debug_written')}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    if not guard_selftest.get("ok"):
        lines.append("Guard selftest failures: " + "; ".join(guard_selftest.get("failures", [])))

    out = Path("troubleshooting/validation/validate_accuracy_85221_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
