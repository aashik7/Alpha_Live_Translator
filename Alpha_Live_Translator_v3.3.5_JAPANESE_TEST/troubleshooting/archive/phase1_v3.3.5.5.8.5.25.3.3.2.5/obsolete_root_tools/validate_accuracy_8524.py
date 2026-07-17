"""Validate Japanese Boundary Stabilizer 8.5.24."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    JAPANESE_BOUNDARY_DECISION_LOG_ENABLED,
    JAPANESE_BOUNDARY_STABILIZER_ENABLED,
    JAPANESE_BOUNDARY_STABILIZER_MODE,
    JAPANESE_DUPLICATE_CONTINUATION_GUARD_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_LEADING_FRAGMENT_HOLD_ENABLED,
    JAPANESE_MIDLINE_PUNCTUATION_CLEANUP_ENABLED,
    JAPANESE_SAFE_MERGE_ENABLED,
    JAPANESE_STT_PROFILE,
    JAPANESE_STOP_FLUSH_BOUNDARY_SAFE,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
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
        "version": APP_VERSION == "3.3.5.5.8.5.24",
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
        "8524_flags": all(
            (
                JAPANESE_BOUNDARY_STABILIZER_ENABLED,
                JAPANESE_BOUNDARY_STABILIZER_MODE == "safe_active",
                JAPANESE_BOUNDARY_DECISION_LOG_ENABLED,
                JAPANESE_LEADING_FRAGMENT_HOLD_ENABLED,
                JAPANESE_SAFE_MERGE_ENABLED,
                JAPANESE_DUPLICATE_CONTINUATION_GUARD_ENABLED,
                JAPANESE_MIDLINE_PUNCTUATION_CLEANUP_ENABLED,
                JAPANESE_STOP_FLUSH_BOUNDARY_SAFE,
            )
        ),
        "stabilizer_module": Path("alpha/transcription/japanese_boundary_stabilizer.py").exists(),
        "assembler_integration": _has(
            "alpha/transcription/japanese_sentence_assembler.py", "JAPANESE_BOUNDARY_STABILIZER_ENABLED"
        ),
        "simulate_script": Path("simulate_boundary_stabilizer.py").exists(),
        "report_sync_preserved": Path("alpha/utils/accuracy_report_sync.py").exists(),
        "package_preserved": _has("package_latest_troubleshooting_run.py", "UPLOAD_PACKAGE_8524_COMPLETED"),
        "no_broad_word_correction": not _has(
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

    for mod in (
        "alpha/transcription/japanese_boundary_stabilizer.py",
        "simulate_boundary_stabilizer.py",
        "alpha/transcription/japanese_sentence_assembler.py",
        "package_latest_troubleshooting_run.py",
    ):
        try:
            import py_compile

            py_compile.compile(mod, doraise=True)
            checks[f"compile_{mod.replace('/', '_')}"] = True
        except Exception:
            checks[f"compile_{mod.replace('/', '_')}"] = False

    sim_dir = Path("troubleshooting/accuracy_benchmark/boundary_simulation")
    if sim_dir.exists() and list(sim_dir.glob("*_boundary_simulation_report.json")):
        checks["boundary_simulation_ran"] = True
        latest = sorted(sim_dir.glob("*_boundary_simulation_report.json"))[-1]
        try:
            rep = json.loads(latest.read_text(encoding="utf-8"))
            if rep.get("leading_fragment_after_count", 999) > rep.get("leading_fragment_before_count", 0):
                warnings.append("leading_fragment_increased_in_simulation")
        except Exception:
            pass
    else:
        checks["boundary_simulation_ran"] = False
        warnings.append("no_boundary_simulation_yet")

    try:
        from alpha.transcription.japanese_boundary_stabilizer import JapaneseBoundaryStabilizer

        s = JapaneseBoundaryStabilizer()
        out, _ = s.simulate_lines(["のビデオから新しいトピック", "挨拶をするに入ります。"])
        checks["stabilizer_simulate_works"] = len(out) >= 1
    except Exception:
        checks["stabilizer_simulate_works"] = False

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

    warnings.append("no_new_live_19min_test_after_implementation")

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.24 JAPANESE BOUNDARY STABILIZER VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_8524_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
