"""Validation for V3.3.5.5.8.5.14 Business cleanup idempotency."""
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
from alpha.transcription.japanese_accuracy_cleaner import (
    business_japanese_stable_cleanup as biz_cleanup,
    detect_duplicate_damage,
    normalize_business_cleanup_once,
)
from alpha.transcription.japanese_sentence_assembler import JapaneseContinuityAssembler

_DUPLICATE_BAD = (
    "いついつも",
    "このこのたび",
    "おお世話",
    "後後任",
    "担当担当交代",
    "使役使役形",
    "参参りました",
    "くださいください",
)


def _assert_no_duplicates(text: str, label: str, failures: list[str]) -> None:
    for bad in _DUPLICATE_BAD:
        if bad in text:
            failures.append(f"{label}_contains_{bad}")


def _assert_idempotent(text: str, label: str, failures: list[str], **ctx: str) -> None:
    first = normalize_business_cleanup_once(text, **ctx)
    second = normalize_business_cleanup_once(first["candidate"], **ctx)
    if second["candidate"] != first["candidate"]:
        failures.append(f"idempotent_{label}")
    _assert_no_duplicates(first["candidate"], label, failures)


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    if APP_VERSION != "3.3.5.5.8.5.14":
        failures.append("version")
    if APP_CODENAME != "Business Cleanup Idempotency & Keyterm Profile Tuning":
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
    esl_markers = ("ESL", "トワイス", "ジヒョ", "英語のレベル", "自分の意見")
    if any(m in terms for m in esl_markers):
        failures.append("esl_twice_terms_in_active_profile")
    if not (30 <= len(terms) <= 60):
        failures.append("keyterms_outside_safe_limit")
    if profile != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("resolved_profile_not_business")
    for required in ("他社", "担当交代", "翌日", "このたび"):
        if required not in terms:
            failures.append(f"missing_keyterm_{required}")

    if "翌日" not in VALID_SHORT_JAPANESE_LIST_TERMS:
        failures.append("yokujitsu_not_in_valid_short_terms")

    # Rule A
    r = biz_cleanup(
        "次、他者の人の名前を紹介する場合",
        nearby_context="自社の人をお客様に紹介する",
    )
    if "他社の人" not in r["candidate"]:
        failures.append("rule_a_tasha_no_hito")

    # Rule B-E
    r2 = biz_cleanup("短頭交代の挨拶")
    if "担当交代" not in r2["candidate"]:
        failures.append("rule_b_tantou_koutai")
    r3 = biz_cleanup(
        "公認の者を連れてご挨拶に回りました",
        nearby_context="担当交代 前任者",
    )
    if "後任" not in r3["candidate"] or "参りました" not in r3["candidate"]:
        failures.append("rule_c_e")

    # Rule H — いつも idempotency
    cases_h = [
        ("いつもお世話になっております", "いつもお世話になっております"),
        (
            "もお世話になっております",
            "いつもお世話になっております",
        ),
        (
            "は永井さんがいつもお世話になっておりますと会話を始めています",
            "は永井さんがいつもお世話になっておりますと会話を始めています",
        ),
    ]
    for inp, expected in cases_h:
        out = biz_cleanup(inp, nearby_context="挨拶 表現 お世話")["candidate"]
        if out != expected:
            failures.append(f"rule_h_{inp[:12]}")
        _assert_no_duplicates(out, "rule_h", failures)
        if "いついつも" in out:
            failures.append("rule_h_itsuitsumo")

    # Rule G — このたび idempotency
    cases_g = [
        ("このたび御社の担当が変わりました", "このたび御社の担当が変わりました"),
        ("実はこのたび御社の担当が変わりました", "実はこのたび御社の担当が変わりました"),
        (
            "この度御社を担当させていただくことになりました",
            "このたび御社を担当させていただくことになりました",
        ),
        (
            "たび御社を担当させていただくことになりました",
            "このたび御社を担当させていただくことになりました",
        ),
        (
            "度御社を担当させていただくことになりました",
            "このたび御社を担当させていただくことになりました",
        ),
        (
            "が実はこの度御社の担当が変わりましたので、後任の者を連れてご挨拶に回りました。",
            "が実はこのたび御社の担当が変わりましたので、後任の者を連れてご挨拶に参りました。",
        ),
    ]
    for inp, expected in cases_g:
        out = biz_cleanup(inp, nearby_context="担当交代 御社")["candidate"]
        if out != expected:
            failures.append(f"rule_g_{inp[:12]}_got_{out[:20]}")
        _assert_no_duplicates(out, "rule_g", failures)
        if "このこのたび" in out:
            failures.append("rule_g_konokonotabi")

    # 8.5.13 regression cases
    reg = biz_cleanup(
        "は永井さがいつもお世話になっておりますと会話を始めています",
        nearby_context="担当交代 挨拶 表現",
    )
    if "いついつも" in reg["candidate"]:
        failures.append("regression_itsuitsumo")
    reg2 = biz_cleanup(
        "が実はこの度御社の担当が変わりましたので、後任の者を連れてご挨拶に回りました。",
        nearby_context="担当交代",
    )
    if "このこのたび" in reg2["candidate"]:
        failures.append("regression_konokonotabi")

    _assert_idempotent(
        "もお世話になっております",
        "itsumo",
        failures,
        nearby_context="挨拶 表現",
    )
    _assert_idempotent(
        "度御社を担当させていただくことになりました",
        "konotabi",
        failures,
        nearby_context="御社 担当",
    )

    if not detect_duplicate_damage("いついつもお世話"):
        failures.append("duplicate_detector_broken")

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
        (get_japanese_accuracy_log_path(), "8.5.14_japanese_accuracy"),
        (get_log_file_path(), "8.5.14_diagnostic"),
        (get_freeze_guard_log_path(), "8.5.14_freeze_guard"),
    ):
        if token not in path.name:
            failures.append(f"log_path_{token}")

    alpha_out = ROOT / "Alpha output.txt"
    if alpha_out.exists():
        content = alpha_out.read_text(encoding="utf-8", errors="replace")
        for bad in ("いついつも", "このこのたび"):
            if bad in content:
                warnings.append(f"alpha_output_contains_{bad}")

    latest = ROOT / "LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt"
    if not latest.exists():
        warnings.append("latest_live_index_not_yet_created")
    elif "8.5.14" not in latest.read_text(encoding="utf-8", errors="replace"):
        warnings.append("latest_live_index_not_8514_yet")

    if failures:
        print("BUSINESS JAPANESE 8.5.14 VALIDATION: FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1

    level = "PASSED_WITH_WARNINGS" if warnings else "PASSED"
    print(f"BUSINESS JAPANESE 8.5.14 VALIDATION: {level}")
    print(f"  keyterm_profile={JAPANESE_KEYTERM_PROFILE}")
    print(f"  business_keyterms_count={len(terms)}")
    print(f"  esl_twice_in_profile=false")
    print(f"  idempotency_guard=active")
    for w in warnings:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
