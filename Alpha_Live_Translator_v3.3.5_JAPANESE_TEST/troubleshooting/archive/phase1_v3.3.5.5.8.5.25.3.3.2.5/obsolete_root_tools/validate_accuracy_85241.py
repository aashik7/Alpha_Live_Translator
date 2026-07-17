"""Validate Boundary Merge Revision & Clean Alpha Export 8.5.24.1."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    BOUNDARY_EXPORT_SOURCE_UI_SEGMENTS_ENABLED,
    BOUNDARY_MERGE_REVISION_ENABLED,
    BOUNDARY_REPORT_PACKAGE_FIX_ENABLED,
    BOUNDARY_SUMMARY_PATH_FIX_ENABLED,
    CLEAN_ALPHA_EXPORT_ENABLED,
    CUMULATIVE_MERGE_DUPLICATE_GUARD_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_BOUNDARY_STABILIZER_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STABLE_LINE_REVISION_MODEL_ENABLED,
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
        "version": APP_VERSION == "3.3.5.5.8.5.24.1",
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
        "85241_flags": all(
            (
                BOUNDARY_MERGE_REVISION_ENABLED,
                STABLE_LINE_REVISION_MODEL_ENABLED,
                CLEAN_ALPHA_EXPORT_ENABLED,
                CUMULATIVE_MERGE_DUPLICATE_GUARD_ENABLED,
                BOUNDARY_EXPORT_SOURCE_UI_SEGMENTS_ENABLED,
                BOUNDARY_REPORT_PACKAGE_FIX_ENABLED,
                BOUNDARY_SUMMARY_PATH_FIX_ENABLED,
            )
        ),
        "stabilizer_module": Path("alpha/transcription/japanese_boundary_stabilizer.py").exists(),
        "revision_model": Path("alpha/transcription/stable_line_revision.py").exists(),
        "assembler_revision": _has("alpha/transcription/japanese_sentence_assembler.py", "boundary_should_revise"),
        "snapshot_revise": _has("alpha/utils/transcript_snapshot_store.py", "revise_last_transcript_snapshot"),
        "repair_script": Path("repair_clean_alpha_export.py").exists(),
        "package_85241": _has("package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_85241_COMPLETED"),
        "scoring_cumulative_check": _has("score_latest_accuracy.py", "alpha_output_cumulative_duplicate_suspected"),
        "live_smoke_pointers": _has("alpha/utils/troubleshooting_paths.py", "LATEST_LIVE_RUN_POINTER_UPDATED"),
        "smoke_pointer": _has("alpha/utils/alpha_output_protection.py", "LATEST_SMOKE_RUN_POINTER_UPDATED"),
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
        "alpha/transcription/japanese_boundary_stabilizer.py",
        "alpha/transcription/stable_line_revision.py",
        "alpha/transcription/japanese_sentence_assembler.py",
        "repair_clean_alpha_export.py",
        "package_latest_troubleshooting_run.py",
        "score_latest_accuracy.py",
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
            after = rep.get("cumulative_after", {})
            before = rep.get("cumulative_before", {})
            if after.get("cumulative_duplicate_count", 999) >= before.get("cumulative_duplicate_count", 0):
                warnings.append("repair_did_not_reduce_cumulative_duplicates")
        except Exception:
            pass
    else:
        checks["repair_ran"] = False
        warnings.append("no_repair_run_yet")

    try:
        from alpha.transcription.stable_line_revision import (
            detect_cumulative_duplicate,
            get_stable_line_revision_manager,
            reset_stable_line_revision_manager,
        )

        reset_stable_line_revision_manager()
        mgr = get_stable_line_revision_manager()
        mgr.create_line("主任、忙しい時に三日も休んで申し訳ありませんでした。")
        applied = mgr.apply_boundary_output(
            {
                "output_text": "主任、忙しい時に三日も休んで申し訳ありませんでした。大変だったね。",
                "should_revise": True,
                "output_action": "revise_previous_line",
                "emit_now": True,
            },
            previous_text="主任、忙しい時に三日も休んで申し訳ありませんでした。",
        )
        checks["revision_model_works"] = applied.get("applied") == "revise" and len(mgr.get_active_lines()) == 1
        checks["cumulative_guard_works"] = detect_cumulative_duplicate(
            "主任、忙しい時に三日も休んで申し訳ありませんでした。",
            "主任、忙しい時に三日も休んで申し訳ありませんでした。大変だったね。",
        )
    except Exception:
        checks["revision_model_works"] = False
        checks["cumulative_guard_works"] = False

    try:
        from alpha.transcription.japanese_boundary_stabilizer import JapaneseBoundaryStabilizer

        s = JapaneseBoundaryStabilizer()
        r = s.process("が続きます。", previous_line="前の文です。", commit_reason="demo")
        checks["stabilizer_output_contract"] = "output_action" in r and "should_revise" in r
    except Exception:
        checks["stabilizer_output_contract"] = False

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

    warnings.append("no_new_live_test_after_24_1")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.24.1 BOUNDARY MERGE REVISION VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_85241_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
