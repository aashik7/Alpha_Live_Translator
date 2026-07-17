"""Validate Japanese stable transcript accuracy patch 8.5.21."""

from __future__ import annotations

from pathlib import Path

from alpha.constants import (
    APP_VERSION,
    ASSEMBLER_EXCEPTION_NO_DIRECT_COMMIT,
    BUSINESS_PHRASE_PROTECTION_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    INCOMPLETE_TAIL_HOLD_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STABLE_ACCURACY_FIX_ENABLED,
    JAPANESE_STT_PROFILE,
    OFFLINE_EVIDENCE_PACKAGING_ENABLED,
    PUNCTUATION_START_MERGE_ENABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STABLE_LAYER_SAFE_MERGE_ENABLED,
    STOP_PATH_MINIMAL_MODE,
    TRANSLATION_READINESS_METRICS_ENABLED,
)
from alpha.transcription.japanese_stable_accuracy import (
    is_punctuation_start_fragment,
    is_protected_business_phrase,
    should_hold_short_fragment,
)


def has(path: str, token: str) -> bool:
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def main() -> int:
    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.21",
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
        "accuracy_fix_enabled": JAPANESE_STABLE_ACCURACY_FIX_ENABLED,
        "stable_layer_merge": STABLE_LAYER_SAFE_MERGE_ENABLED,
        "punctuation_merge": PUNCTUATION_START_MERGE_ENABLED,
        "short_fragment_logic": should_hold_short_fragment("こで", is_stop_flush=False),
        "incomplete_tail_hold": INCOMPLETE_TAIL_HOLD_ENABLED,
        "business_phrase_protection": (
            BUSINESS_PHRASE_PROTECTION_ENABLED
            and is_protected_business_phrase("承知いたしました。")
        ),
        "assembler_exception_blocked": (
            ASSEMBLER_EXCEPTION_NO_DIRECT_COMMIT
            and has(
                "alpha/transcription/japanese_sentence_assembler.py",
                "ASSEMBLER_EXCEPTION_DIRECT_COMMIT_BLOCKED",
            )
        ),
        "translation_readiness_metrics": (
            TRANSLATION_READINESS_METRICS_ENABLED
            and has(
                "alpha/transcription/japanese_sentence_assembler.py",
                "write_translation_readiness_summary",
            )
        ),
        "stop_minimal_preserved": (
            STOP_PATH_MINIMAL_MODE
            and RUNTIME_EVIDENCE_PACKAGE_DISABLED
            and has("alpha/utils/stop_finalize_worker.py", "STOP_MINIMAL_BEGIN")
        ),
        "no_runtime_upload": has("alpha/constants.py", "NO_UPLOAD_ZIP_DURING_RUNTIME = True"),
        "offline_package_script": Path("package_latest_troubleshooting_run.py").exists(),
        "no_deepl": not has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization": JAPANESE_STT_PROFILE == "no_diarize",
        "punctuation_detector": is_punctuation_start_fragment("、そうですね。"),
        "accuracy_decision_log": Path("alpha/utils/accuracy_decision_log.py").exists(),
        "stable_accuracy_module": Path(
            "alpha/transcription/japanese_stable_accuracy.py"
        ).exists(),
    }
    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.21 JAPANESE STABLE TRANSCRIPT ACCURACY VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_accuracy_8521_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
