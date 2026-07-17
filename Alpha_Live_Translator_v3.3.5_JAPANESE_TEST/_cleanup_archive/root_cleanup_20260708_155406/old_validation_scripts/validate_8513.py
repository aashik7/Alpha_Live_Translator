"""Validation for V3.3.5.5.8.5.13 Business Japanese accuracy guard."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    KEYTERM_PROFILE_BUSINESS_JAPANESE,
    VALID_SHORT_JAPANESE_LIST_TERMS,
    resolve_japanese_keyterms,
)
from alpha.transcription.japanese_accuracy_cleaner import business_japanese_stable_cleanup as biz_cleanup
from alpha.transcription.japanese_sentence_assembler import JapaneseContinuityAssembler


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    if APP_VERSION != "3.3.5.5.8.5.13":
        failures.append("version")
    if APP_CODENAME != "Business Japanese Accuracy Guard & Keyterm Profile Cleanup":
        failures.append("codename")
    if JAPANESE_KEYTERM_PROFILE != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("keyterm_profile")
    if DEEPGRAM_MODEL != "nova-3" or DEEPGRAM_LANGUAGE != "ja":
        failures.append("deepgram_config")
    if JAPANESE_STT_PROFILE != "no_diarize":
        failures.append("stt_profile")
    if DEEPGRAM_ENDPOINTING_MS != 500 or DEEPGRAM_UTTERANCE_END_MS != 1500:
        failures.append("deepgram_timing")

    terms, profile, _ = resolve_japanese_keyterms()
    esl_markers = ("ESL", "トワイス", "ジヒョ", "英語のレベル")
    if any(m in terms for m in esl_markers):
        failures.append("esl_twice_terms_in_active_profile")
    if "他社" not in terms or "担当交代" not in terms or "翌日" not in terms:
        failures.append("business_keyterms_missing")
    if profile != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("resolved_profile_not_business")

    if "翌日" not in VALID_SHORT_JAPANESE_LIST_TERMS:
        failures.append("yokujitsu_not_in_valid_short_terms")

    # Business cleanup rules
    r = biz_cleanup(
        "次、他者の人の名前を紹介する場合",
        nearby_context="自社の人をお客様に紹介する",
    )
    if "他社の人" not in r["candidate"]:
        failures.append("rule_a_tasha_no_hito")
    r2 = biz_cleanup("短頭交代の挨拶")
    if "担当交代" not in r2["candidate"]:
        failures.append("rule_b_tantou_koutai")
    r3 = biz_cleanup(
        "公認の者を連れてご挨拶に回りました",
        nearby_context="担当交代 前任者",
    )
    if "後任" not in r3["candidate"] or "参りました" not in r3["candidate"]:
        failures.append("rule_c_e")

    # Quarantine bypass for 翌日
    class _Host:
        is_listening = True
        listening = True
        _is_stopping = False
        _teams_latest_source_snapshot = {}

    asm = JapaneseContinuityAssembler(_Host())
    if asm._should_quarantine("翌日"):
        failures.append("yokujitsu_still_quarantined")

    from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_log_path
    from alpha.utils.diagnostic_test_log import get_log_file_path
    from alpha.utils.freeze_guard_log import get_freeze_guard_log_path

    for path, token in (
        (get_japanese_accuracy_log_path(), "8.5.13_japanese_accuracy"),
        (get_log_file_path(), "8.5.13_diagnostic"),
        (get_freeze_guard_log_path(), "8.5.13_freeze_guard"),
    ):
        if token not in path.name:
            failures.append(f"log_path_{token}")

    latest = ROOT / "LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt"
    if not latest.exists():
        warnings.append("latest_live_index_not_yet_created")

    if failures:
        print("BUSINESS JAPANESE VALIDATION: FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1

    level = "PASSED_WITH_WARNINGS" if warnings else "PASSED"
    print(f"BUSINESS JAPANESE VALIDATION: {level}")
    print(f"  keyterm_profile={JAPANESE_KEYTERM_PROFILE}")
    print(f"  business_keyterms_count={len(terms)}")
    print(f"  esl_twice_in_profile=false")
    for w in warnings:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
