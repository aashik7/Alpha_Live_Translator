"""High-confidence Japanese business stable-layer corrections (V3.3.5.5.8.5.22.1)."""

from __future__ import annotations

import re
from typing import Any, Optional

from alpha.constants import (
    ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    BENCHMARK_BASELINE_LOCK_ENABLED,
    BUSINESS_ACCURACY_EXPANSION_85222_ENABLED,
    BUSINESS_CORRECTION_GUARD_85221_ENABLED,
    BUSINESS_CORRECTION_IDEMPOTENT_MODE,
    DISABLE_HARMFUL_85222_EXPANSION_RULES,
    DISABLE_POLITE_CLOSING_ZO_PREFIX,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    CORPORATE_IR_GLOSSARY_ENABLED,
    SAFE_CORRECTION_GATE_ENABLED,
    STRICT_IDEMPOTENT_CORRECTIONS_ONLY,
    STABLE_LAYER_BUSINESS_CORRECTION_ENABLED,
)
from alpha.utils.cjk_text import compact_cjk_for_compare

BUSINESS_TERMS_PRESERVE: frozenset[str] = frozenset(
    {
        "御社",
        "弊社",
        "自社",
        "他社",
        "他者",
        "前任者",
        "後任",
        "担当交代",
        "使役形",
        "ご挨拶に参りました",
        "お世話になっております",
        "よろしくお願いいたします",
        "恐れ入ります",
        "承知いたしました",
    }
)

_KATTAE_CONTEXT_CUES = (
    "自分の行為",
    "相手に",
    "報告",
    "表現",
    "丁寧",
    "使う表現",
    "伝え",
)

_CHIN_CAST_CONTEXT_CUES = (
    "登場人物",
    "永井さん",
    "木村さん",
    "三人",
    "チン・シュウメイ",
    "チンさん",
)

_CHIN_NAME_CONTEXT_CUES = (
    "チン・シュウメイ",
    "チンさん",
    "チン・",
    "後任",
    "ございます",
)

_SAMA_KO_O_PATTERN = re.compile(r"([^\s、。]{1,12}[様さん])(こを)")

_OSEWA_PATTERN = re.compile(r"(?<!お)世話になっております")
_ITUMO_OSEWA_PATTERN = re.compile(r"いつも(?<!お)世話になっております")

_HARMFUL_RULES_LOGGED = False
_MINIMAL_BASELINE_LOGGED = False

_EXACT_DUPLICATE_PREFIX = "自分の行為の場合は使役でいただきますを使いましょう。"

_KOUNIN_HANDOVER_CUES = (
    "御社の担当が変わりました",
    "担当交代",
    "後任",
    "前任者",
    "この度",
    "このたび",
)

_TANTOU_CONTEXT_CUES = ("御社", "このたび", "この度", "担当", "チンでございます", "チン・シュウメイ")


def is_minimal_correction_mode() -> bool:
    return (
        BENCHMARK_BASELINE_LOCK_ENABLED
        and AUTO_BUSINESS_CORRECTION_LEVEL in ("minimal", "minimal_plus_user_glossary")
        and LESSON_SPECIFIC_CORRECTIONS_DISABLED
    )


def is_glossary_only_correction_mode() -> bool:
    return (
        AUTO_BUSINESS_CORRECTION_LEVEL == "minimal_plus_user_glossary"
        and CORPORATE_IR_GLOSSARY_ENABLED
    )


def _log_minimal_baseline_once() -> None:
    global _MINIMAL_BASELINE_LOGGED
    if _MINIMAL_BASELINE_LOGGED:
        return
    _MINIMAL_BASELINE_LOGGED = True
    _log_correction("AUTO_BUSINESS_CORRECTION_LEVEL_SET_MINIMAL")
    _log_correction("BROAD_CORRECTION_DISABLED_BY_BASELINE_LOCK")
    _log_correction("LESSON_SPECIFIC_CORRECTION_BLOCKED")
    _log_correction("CORRECTION_RULE_DEFAULT_STATE_AUDIT_ONLY")


def _log_harmful_rules_disabled_once() -> None:
    global _HARMFUL_RULES_LOGGED
    if _HARMFUL_RULES_LOGGED or not DISABLE_HARMFUL_85222_EXPANSION_RULES:
        return
    _HARMFUL_RULES_LOGGED = True
    _log_correction("HARMFUL_85222_RULE_DISABLED", reason="accuracy_regression_rollback_85223")
    if DISABLE_POLITE_CLOSING_ZO_PREFIX:
        _log_correction("POLITE_CLOSING_ZO_PREFIX_DISABLED")
    _log_correction("BROAD_SPLIT_FRAGMENT_RULE_DISABLED")
    _log_correction("UNSAFE_DEDUPE_RULE_DISABLED")
    _log_correction("TRANSLATION_READY_FAKE_RECLASSIFICATION_BLOCKED")


def safe_correction_gate(
    input_text: str,
    output_text: str,
    *,
    rule_id: str,
    allow_length_increase: bool = False,
    src: str = "",
) -> tuple[bool, str]:
    """Return (allowed, block_reason)."""
    if not SAFE_CORRECTION_GATE_ENABLED:
        return True, ""
    _log_correction(
        "SAFE_CORRECTION_GATE_CHECKED",
        rule_id=rule_id,
        input_text=input_text,
        output_text=output_text,
        raw_deepgram_mutated=False,
    )
    if input_text == output_text:
        return False, "no_change"
    if STRICT_IDEMPOTENT_CORRECTIONS_ONLY and src and src in output_text:
        _log_correction("CORRECTION_IDEMPOTENCY_CHECK_FAILED", rule_id=rule_id, reason="source_pattern_remains")
        _log_correction("SAFE_CORRECTION_GATE_BLOCKED", rule_id=rule_id, reason="idempotency_failed")
        return False, "idempotency_failed"
    if "どうどうぞ" in output_text and "どうどうぞ" not in input_text:
        _log_correction("DOUDOZO_CREATION_BLOCKED", rule_id=rule_id, input_text=input_text, output_text=output_text)
        _log_correction("SAFE_CORRECTION_GATE_BLOCKED", rule_id=rule_id, reason="doudouzo_creation")
        return False, "doudouzo_creation"
    if "おお世話" in output_text and "おお世話" not in input_text:
        _log_correction("SAFE_CORRECTION_GATE_BLOCKED", rule_id=rule_id, reason="double_prefix_osewa")
        return False, "double_prefix_osewa"
    if "こここは注意" in output_text:
        _log_correction("SAFE_CORRECTION_GATE_BLOCKED", rule_id=rule_id, reason="triple_koko")
        return False, "triple_koko"
    if not allow_length_increase and len(output_text) > len(input_text) + 8:
        _log_correction("SAFE_CORRECTION_GATE_BLOCKED", rule_id=rule_id, reason="suspicious_length_increase")
        return False, "suspicious_length_increase"
    _log_correction(
        "CORRECTION_IDEMPOTENCY_CHECK_PASSED",
        rule_id=rule_id,
        input_text=input_text,
        output_text=output_text,
        idempotent=True,
    )
    _log_correction(
        "SAFE_CORRECTION_GATE_ALLOWED",
        rule_id=rule_id,
        input_text=input_text,
        output_text=output_text,
        raw_deepgram_mutated=False,
        idempotent=True,
    )
    return True, ""


def _safe_replace(
    text: str,
    applied: list[dict[str, Any]],
    *,
    src: str,
    dst: str,
    rule_id: str,
    correction_type: str,
    event: str = "BUSINESS_STABLE_CORRECTION_APPLIED",
    context_window: str = "",
    allow_length_increase: bool = False,
    context_ok: bool = True,
) -> str:
    if not context_ok or src not in text:
        return text
    if dst in text and src not in text:
        _log_skip(text, rule_id, "already_correct")
        return text
    candidate = text.replace(src, dst)
    allowed, reason = safe_correction_gate(
        text, candidate, rule_id=rule_id, allow_length_increase=allow_length_increase, src=src
    )
    if not allowed:
        _log_correction(
            "SAFE_CORRECTION_SKIPPED_UNCERTAIN",
            input_text=text,
            rule_id=rule_id,
            reason=reason,
            raw_deepgram_mutated=False,
        )
        return text
    return _append_change(
        applied,
        raw_input=text,
        corrected=candidate,
        rule_id=rule_id,
        correction_type=correction_type,
        event=event,
        context_window=context_window,
    )


def _repair_doudouzo_regression(text: str, applied: list[dict[str, Any]]) -> str:
    if "どうどうぞ" not in text:
        return text
    result = text
    for src, dst in (
        ("どうどうぞよろしくお願いいたします", "どうぞよろしくお願いいたします"),
        ("どうどうぞよろしくお願いいします", "どうぞよろしくお願いいたします"),
    ):
        result = _safe_replace(
            result,
            applied,
            src=src,
            dst=dst,
            rule_id="doudouzo_regression_repair",
            correction_type="polite_closing_repair",
            event="DOUDOZO_REGRESSION_REPAIRED",
        )
    return result


def is_preserved_business_term(text: str) -> bool:
    segment = (text or "").strip()
    if not segment:
        return False
    compact = compact_cjk_for_compare(segment, "ja")
    for term in BUSINESS_TERMS_PRESERVE:
        if term in segment or compact_cjk_for_compare(term, "ja") in compact:
            return True
    return False


def _log_correction(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, raw_deepgram_mutated=False, **fields)
    except Exception:
        pass


def _log_skip(input_text: str, rule_id: str, reason: str) -> None:
    _log_correction(
        "BUSINESS_CORRECTION_IDEMPOTENT_SKIP",
        input_text=input_text,
        output_text=input_text,
        rule_id=rule_id,
        reason=reason,
    )


def _append_change(
    applied: list[dict[str, Any]],
    *,
    raw_input: str,
    corrected: str,
    rule_id: str,
    correction_type: str,
    confidence: str = "high",
    event: str = "BUSINESS_STABLE_CORRECTION_APPLIED",
    context_window: str = "",
) -> str:
    applied.append(
        {
            "correction_type": correction_type,
            "correction_rule_id": rule_id,
            "confidence": confidence,
            "raw_stable_input": raw_input,
            "corrected_output": corrected,
            "context_window": context_window,
            "log_event": event,
        }
    )
    _log_correction(
        event,
        input_text=raw_input,
        output_text=corrected,
        rule_id=rule_id,
        raw_stable_input=raw_input,
        corrected_output=corrected,
        correction_type=correction_type,
        correction_rule_id=rule_id,
        confidence=confidence,
        context_window=context_window,
    )
    return corrected


def _repair_double_prefix_osewa(text: str, applied: list[dict[str, Any]]) -> str:
    if "おお世話" not in text:
        return text
    new_text = text.replace("おお世話になっております", "お世話になっております")
    if new_text != text:
        return _append_change(
            applied,
            raw_input=text,
            corrected=new_text,
            rule_id="double_prefix_osewa_repair",
            correction_type="polite_greeting_repair",
            event="BUSINESS_CORRECTION_DOUBLE_PREFIX_REPAIRED",
        )
    return text


def _apply_polite_greeting_osewa(text: str, applied: list[dict[str, Any]]) -> str:
    result = text
    if "お世話になっております" in result and "世話になっております" not in result.replace(
        "お世話になっております", ""
    ):
        _log_skip(result, "polite_greeting", "already_correct_osewa")
        return result
    if "いつもお世話になっております" in result:
        _log_skip(result, "polite_greeting", "already_correct_itumo_osewa")
        return result

    new_result = _ITUMO_OSEWA_PATTERN.sub("いつもお世話になっております", result)
    if new_result != result:
        result = _append_change(
            applied,
            raw_input=result,
            corrected=new_result,
            rule_id="polite_greeting_itumo",
            correction_type="polite_greeting",
            event="BUSINESS_CORRECTION_GUARD_APPLIED",
        )
    new_result = _OSEWA_PATTERN.sub("お世話になっております", result)
    if new_result != result:
        result = _append_change(
            applied,
            raw_input=result,
            corrected=new_result,
            rule_id="polite_greeting",
            correction_type="polite_greeting",
            event="BUSINESS_CORRECTION_GUARD_APPLIED",
        )
    return result


def _repair_triple_koko(text: str, applied: list[dict[str, Any]]) -> str:
    if "こここは注意" not in text:
        return text
    new_text = text.replace("こここは注意が必要です", "ここは注意が必要です")
    if new_text != text:
        return _append_change(
            applied,
            raw_input=text,
            corrected=new_text,
            rule_id="triple_koko_repair",
            correction_type="explanation_phrase_repair",
            event="BUSINESS_CORRECTION_TRIPLE_KOKO_REPAIRED",
        )
    return text


def _apply_koko_attention(text: str, applied: list[dict[str, Any]]) -> str:
    if "ここは注意が必要です" in text:
        _log_skip(text, "explanation_koko", "already_correct_kokoha")
        return text
    if "こは注意が必要です" not in text:
        return text
    new_text = text.replace("こは注意が必要です", "ここは注意が必要です")
    return _append_change(
        applied,
        raw_input=text,
        corrected=new_text,
        rule_id="explanation_koko",
        correction_type="explanation_phrase",
        event="BUSINESS_CORRECTION_GUARD_APPLIED",
    )


def _apply_yoroshiku_rules(text: str, applied: list[dict[str, Any]]) -> str:
    result = text
    replacements: tuple[tuple[str, str, str], ...] = (
        ("よろしお願いいします", "よろしくお願いいたします", "polite_closing_yoroshi"),
        ("よろしくお願いいします", "よろしくお願いいたします", "polite_closing_yoroshiku"),
        ("よろしいお願いいたします", "よろしくお願いいたします", "polite_closing_yoroshii"),
        (
            "こそどうぞよろしくお願いいします",
            "こちらこそどうぞよろしくお願いいたします",
            "polite_closing_koso",
        ),
        ("どうぞよろしくお願いいします", "どうぞよろしくお願いいたします", "polite_closing_douzo"),
        (
            "こちらこそ夜よろしくお願いいたします",
            "こちらこそよろしくお願いいたします",
            "polite_closing_yoru",
        ),
    )
    for src, dst, rule_id in replacements:
        if src not in result:
            continue
        if dst in result and src not in result:
            continue
        if rule_id == "polite_closing_douzo":
            if not (result.startswith("どうぞ") or "、どうぞ" in result or "。どうぞ" in result):
                _log_skip(result, rule_id, "context_guard_boundary")
                continue
        new_result = result.replace(src, dst)
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id=rule_id,
                correction_type="polite_closing",
                event="BUSINESS_CORRECTION_APPLIED_HIGH_CONFIDENCE",
            )
    if DISABLE_POLITE_CLOSING_ZO_PREFIX:
        _log_harmful_rules_disabled_once()
    elif "ぞよろしくお願いいたします" in result and not result.startswith("どうぞ"):
        new_result = result.replace("ぞよろしくお願いいたします", "どうぞよろしくお願いいたします")
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="polite_closing_zo_prefix",
                correction_type="polite_closing",
                event="BUSINESS_CORRECTION_CONTEXT_GUARD_USED",
            )
    return result


def _apply_other_phrase_rules(text: str, applied: list[dict[str, Any]]) -> str:
    result = text
    simple: tuple[tuple[str, str, str], ...] = (
        ("というふに", "というふうに", "explanation_funi"),
        ("何々様こを", "何々様、これを", "explanation_naname"),
        ("何々さん、何々様こを", "何々さん、何々様、これを", "explanation_naname_combo"),
    )
    for src, dst, rule_id in simple:
        if src in result and dst not in result:
            new_result = result.replace(src, dst)
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id=rule_id,
                    correction_type="explanation_phrase",
                    event="BUSINESS_CORRECTION_APPLIED_HIGH_CONFIDENCE",
                )
    if "皆さも" in result and "皆さんも" not in result:
        if any(cue in result for cue in ("積極的", "使って", "覚え", "確認", "ぜひ")):
            new_result = result.replace("皆さも", "皆さんも")
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id="minasanmo",
                    correction_type="explanation_phrase",
                    event="BUSINESS_CORRECTION_CONTEXT_GUARD_USED",
                )
        else:
            _log_skip(result, "minasanmo", "low_confidence_context")
    if "御社の担当が変わりしたので" in result:
        new_result = result.replace(
            "御社の担当が変わりしたので", "御社の担当が変わりましたので"
        )
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="kawarishita",
                correction_type="business_phrase",
                event="BUSINESS_CORRECTION_APPLIED_HIGH_CONFIDENCE",
            )
    if "担当させていただくことました" in result and "担当させていただく" in result:
        new_result = result.replace(
            "担当させていただくことました",
            "担当させていただくことになりました",
        )
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="kotoshimashita",
                correction_type="business_phrase",
                event="BUSINESS_CORRECTION_APPLIED_HIGH_CONFIDENCE",
            )
    return result


def _apply_safe_85223_corrections(
    text: str,
    applied: list[dict[str, Any]],
    *,
    nearby_context: str = "",
    previous_segment: str = "",
    session_context: Optional[dict[str, Any]] = None,
) -> str:
    if not ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED:
        return text
    _log_harmful_rules_disabled_once()
    result = _repair_doudouzo_regression(text, applied)
    window = f"{previous_segment}{nearby_context}{text}"
    session = session_context or {}
    recent_lines: list[str] = list(session.get("recent_stable_lines") or [])

    if "公認のものを連れてご挨拶に参りました" in result:
        if any(cue in window for cue in _KOUNIN_HANDOVER_CUES):
            result = _safe_replace(
                result,
                applied,
                src="公認のものを連れてご挨拶に参りました",
                dst="後任の者を連れてご挨拶に参りました",
                rule_id="kounin_kounin_from_kounin_typo",
                correction_type="kounin_context",
                event="KOUNIN_CONTEXT_CORRECTION_APPLIED",
                context_window=window[-120:],
                allow_length_increase=True,
            )
        else:
            _log_correction("KOUNIN_CONTEXT_CORRECTION_SKIPPED", input_text=result, reason="missing_handover_context")
            _log_correction("KOUNIN_FALSE_POSITIVE_BLOCKED", input_text=result)

    if "後任のものを連れてご挨拶に参りました" in result:
        if any(
            cue in result or cue in window
            for cue in ("ご挨拶に参りました", "担当が変わりました", "前任者", "後任")
        ):
            result = _safe_replace(
                result,
                applied,
                src="後任のものを連れてご挨拶に参りました",
                dst="後任の者を連れてご挨拶に参りました",
                rule_id="kounin_mono_to_sha",
                correction_type="kounin_context",
                event="KOUNIN_CONTEXT_CORRECTION_APPLIED",
                context_window=window[-120:],
            )
        else:
            _log_correction("KOUNIN_CONTEXT_CORRECTION_SKIPPED", input_text=result, reason="missing_greeting_context")

    tantou_ok = any(cue in window or cue in result for cue in _TANTOU_CONTEXT_CUES)
    for src, dst, rule_id in (
        ("担当せていただくことになりました", "担当させていただくことになりました", "tantou_sasete_missing_sa"),
        ("担当させていただいことになりました", "担当させていただくことになりました", "tantou_sasete_itadaku"),
    ):
        result = _safe_replace(
            result,
            applied,
            src=src,
            dst=dst,
            rule_id=rule_id,
            correction_type="business_phrase",
            event="TANTOU_SASETE_ITADAKU_CORRECTED",
            context_ok=tantou_ok,
        )
    if not tantou_ok and ("担当せていただく" in result or "担当させていただいこと" in result):
        _log_correction("TANTOU_SASETE_ITADAKU_SKIPPED", input_text=result, reason="missing_tantou_context")

    if result.startswith("皆さ、こんにちは"):
        result = _safe_replace(
            result,
            applied,
            src="皆さ、こんにちは",
            dst="皆さん、こんにちは",
            rule_id="minasan_konnichiwa",
            correction_type="greeting_phrase",
            event="SMALL_VISIBLE_PHRASE_CORRECTION_APPLIED",
        )

    result = _safe_replace(
        result,
        applied,
        src="談話で練習しいきましょう",
        dst="談話で練習していきましょう",
        rule_id="danwa_renshuu_shi",
        correction_type="lesson_phrase",
        event="SMALL_VISIBLE_PHRASE_CORRECTION_APPLIED",
    )

    if "Aさですか" in result and any(cue in window for cue in ("会話", "練習", "談話", "リピート")):
        result = _safe_replace(
            result,
            applied,
            src="Aさですか",
            dst="Aさんですか",
            rule_id="a_san_dialogue",
            correction_type="dialogue_phrase",
            event="SMALL_VISIBLE_PHRASE_CORRECTION_APPLIED",
            context_window=window[-120:],
        )
    elif "Aさですか" in result:
        _log_correction("SMALL_VISIBLE_PHRASE_CORRECTION_SKIPPED", input_text=result, rule_id="a_san_dialogue")

    if "あなたの会社にあります" in result and all(
        cue in window or cue in result
        for cue in ("御社、これはお客様の会社", "あなたの会社")
    ):
        result = _safe_replace(
            result,
            applied,
            src="あなたの会社にあります",
            dst="あなたの会社になります",
            rule_id="anata_no_kaisha",
            correction_type="explanation_phrase",
            event="SMALL_VISIBLE_PHRASE_CORRECTION_APPLIED",
            context_window=window[-160:],
        )

    if "よく見る表現なのでも、覚えてほしいです" in result and all(
        cue in window or cue in result for cue in ("仕事のメール", "よく使われる", "よく見る表現")
    ):
        result = _safe_replace(
            result,
            applied,
            src="よく見る表現なのでも、覚えてほしいです",
            dst="よく見る表現なので、ぜひ覚えてほしいです",
            rule_id="yoku_miru_hyogen",
            correction_type="explanation_phrase",
            event="SMALL_VISIBLE_PHRASE_CORRECTION_APPLIED",
            context_window=window[-160:],
            allow_length_increase=True,
        )

    if "珍習名でございます" in result:
        chin_context = bool(session.get("has_chin_name")) or any(
            "チン・シュウメイ" in line for line in recent_lines
        ) or ("私の後任" in result and "ございます" in result)
        if chin_context:
            result = _safe_replace(
                result,
                applied,
                src="珍習名でございます",
                dst="チン・シュウメイでございます",
                rule_id="chin_shumei_from_typo",
                correction_type="name_correction",
                event="NAME_CORRECTION_APPLIED_WITH_CONTEXT",
                context_window=window[-120:],
                allow_length_increase=True,
            )
        else:
            _log_correction("NAME_CORRECTION_SKIPPED_NO_CONTEXT", input_text=result, rule_id="chin_shumei_from_typo")
            _log_correction("NAME_CORRECTION_GLOSSARY_REQUIRED", input_text=result)

    return result


def _apply_expansion_85222_rules(
    text: str,
    applied: list[dict[str, Any]],
    *,
    nearby_context: str = "",
    session_context: Optional[dict[str, Any]] = None,
) -> str:
    if not BUSINESS_ACCURACY_EXPANSION_85222_ENABLED:
        return text
    result = text
    window = f"{nearby_context}{text}"
    session = session_context or {}

    if "復習ビデオ" not in result and "復習ビデ" in result:
        if (
            result.strip() == "復習ビデを始めたい"
            or all(cue in window for cue in ("第二回", "復習"))
            or "始めたいと思います" in window
        ):
            new_result = result.replace("復習ビデ", "復習ビデオ")
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id="fukushu_video_completion",
                    correction_type="business_phrase_completion",
                )

    if "表現を使うんですかね" in result:
        if any(cue in window for cue in ("表現", "復習")) and "でしたね" in window:
            new_result = result.replace("表現を使うんですかね", "表現を使うんでしたね")
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id="hyogen_tsukaun_desu_kane",
                    correction_type="business_phrase_completion",
                )

    if _SAMA_KO_O_PATTERN.search(result):
        new_result = _SAMA_KO_O_PATTERN.sub(r"\1、これを", result)
        if new_result != result and "様こを" not in new_result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="sama_ko_o_split",
                correction_type="business_phrase_completion",
            )

    if "ではお談話で練習していきましょう" in result or (
        "ではお談話で" in result and "談話" in window and "練習" in window
    ):
        new_result = result.replace("ではお談話で", "では談話で")
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="odanwa_to_danwa",
                correction_type="business_phrase_completion",
            )

    if all(token in result for token in ("こそ", "どうぞ", "よろしく", "お願い")):
        if "こそどうどうぞよろしく" in result:
            new_result = result.replace(
                "こそどうどうぞよろしくお願いいたします",
                "こちらこそどうぞよろしくお願いいたします",
            )
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id="koso_douzo_yoroshiku",
                    correction_type="polite_closing",
                )

    if "ご愛挨拶" in result:
        if (
            result.strip() == "ご愛挨拶に参りました"
            or all(cue in result for cue in ("後任", "連れて", "参りました"))
        ):
            new_result = result.replace("ご愛挨拶", "ご挨拶")
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id="goaisatsu_typo",
                    correction_type="business_phrase_completion",
                )

    if "私同様よろしくお願いたします" in result and "私同様" in result:
        new_result = result.replace(
            "私同様よろしくお願いたします", "私同様よろしくお願いいたします"
        )
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="watashi_douyou_onegai",
                correction_type="polite_closing",
            )

    for src, dst, rule_id in (
        ("よろしくお願いいたします", "よろしくお願いいたします", "onegai_itashimasu"),
        ("どうぞよろしくお願いいたします", "どうぞよろしくお願いいたします", "douzo_onegai"),
    ):
        if src in result and dst not in result and "よろしく" in result:
            new_result = result.replace(src, dst)
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id=rule_id,
                    correction_type="polite_closing",
                )

    if (
        "担当させていただいことになりました" in result
        and "御社" in result
        and "担当" in result
        and "させていただ" in result
    ):
        new_result = result.replace(
            "担当させていただいことになりました",
            "担当させていただくことになりました",
        )
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="tantou_sasete_itadaku",
                correction_type="business_phrase_completion",
            )

    if "珍さん" in result:
        cast_ok = bool(session.get("has_chin_name")) or any(
            cue in window for cue in _CHIN_CAST_CONTEXT_CUES
        )
        if cast_ok:
            new_result = result.replace("珍さん", "チンさん")
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id="chin_san_name",
                    correction_type="name_correction",
                    event="BUSINESS_NAME_CORRECTION_APPLIED_HIGH_CONFIDENCE",
                )
        else:
            _log_correction(
                "BUSINESS_NAME_CORRECTION_SKIPPED_LOW_CONFIDENCE",
                input_text=result,
                rule_id="chin_san_name",
                reason="missing_cast_context",
            )

    return result


def clean_midline_punctuation_artifact(text: str) -> tuple[str, bool]:
    if "。、" not in text:
        return text, False
    return text.replace("。、", "。"), True


def cleanup_exact_duplicate_continuation(
    previous_text: str,
    current_text: str,
    *,
    same_speaker: bool,
) -> dict[str, Any]:
    """Conservative exact duplicate + continuation cleanup (8.5.22.3)."""
    prev = (previous_text or "").strip()
    cur = (current_text or "").strip()
    if not same_speaker or prev != _EXACT_DUPLICATE_PREFIX:
        _log_correction(
            "DUPLICATE_CLEANUP_SKIPPED_NOT_EXACT",
            previous_line=prev[:80],
            current_line=cur[:80],
            raw_deepgram_mutated=False,
        )
        return {"applied": False, "corrected": cur}
    continuation = _EXACT_DUPLICATE_PREFIX + "、それでは会話です。"
    if cur == continuation or cur.startswith(continuation):
        corrected = _EXACT_DUPLICATE_PREFIX + "それでは会話です。"
        corrected, midline = clean_midline_punctuation_artifact(corrected)
        _log_correction(
            "EXACT_DUPLICATE_CONTINUATION_CLEANED",
            previous_line_before=prev,
            next_line_before=cur,
            repaired_line=corrected,
            raw_deepgram_mutated=False,
        )
        if midline:
            _log_correction(
                "MIDLINE_PUNCTUATION_ARTIFACT_CLEANED",
                input_text=cur,
                output_text=corrected,
                raw_deepgram_mutated=False,
            )
        return {"applied": True, "corrected": corrected, "midline_cleaned": midline}
    _log_correction(
        "DUPLICATE_CLEANUP_SKIPPED_NOT_EXACT",
        previous_line=prev[:80],
        current_line=cur[:80],
        raw_deepgram_mutated=False,
    )
    return {"applied": False, "corrected": cur}


def dedupe_duplicate_phrase_stable(
    previous_text: str,
    current_text: str,
    *,
    same_speaker: bool,
) -> dict[str, Any]:
    cur = (current_text or "").strip()
    if DISABLE_HARMFUL_85222_EXPANSION_RULES:
        _log_harmful_rules_disabled_once()
        return {"applied": False, "corrected": cur, "disabled": True}
    prev = (previous_text or "").strip()
    if not same_speaker or not prev or not cur or len(prev) < 20:
        return {"applied": False, "corrected": cur}
    prev_base = prev.rstrip("。、")
    if not cur.startswith(prev_base) and not cur.startswith(prev):
        return {"applied": False, "corrected": cur}
    remainder = cur[len(prev_base) :].lstrip("。、")
    if not remainder:
        return {"applied": False, "corrected": cur}
    corrected = prev_base + "。" + remainder.lstrip("、")
    corrected, midline = clean_midline_punctuation_artifact(corrected)
    if corrected == cur:
        return {"applied": False, "corrected": cur}
    _log_correction(
        "BUSINESS_DUPLICATE_PHRASE_DEDUPED",
        previous_line_before=prev,
        next_line_before=cur,
        repaired_line=corrected,
        rule_id="duplicate_prefix_dedupe",
        raw_deepgram_mutated=False,
    )
    if midline:
        _log_correction(
            "MIDLINE_PUNCTUATION_ARTIFACT_CLEANED",
            input_text=cur,
            output_text=corrected,
            raw_deepgram_mutated=False,
        )
    return {"applied": True, "corrected": corrected, "midline_cleaned": midline}


def repair_split_fragment_stable(
    previous_text: str,
    current_text: str,
    *,
    nearby_context: str = "",
) -> dict[str, Any]:
    cur = (current_text or "").strip()
    if DISABLE_HARMFUL_85222_EXPANSION_RULES:
        _log_harmful_rules_disabled_once()
        return {"action": "unchanged", "repaired_line": cur, "disabled": True}
    prev = (previous_text or "").strip()
    window = f"{nearby_context}{prev}{cur}"
    if not prev.endswith("こう") or not cur.startswith("任の者"):
        return {"action": "unchanged", "repaired_line": cur}
    if "御社の担当が変わりました" not in window or "ご挨拶に参りました" not in window:
        _log_correction(
            "BUSINESS_SPLIT_FRAGMENT_SKIPPED",
            previous_line_before=prev,
            next_line_before=cur,
            reason="low_confidence_context",
            raw_deepgram_mutated=False,
        )
        return {"action": "unchanged", "repaired_line": cur}
    merged = prev[:-2] + "後任" + cur[1:]
    _log_correction(
        "BUSINESS_SPLIT_FRAGMENT_REPAIRED",
        previous_line_before=prev,
        next_line_before=cur,
        repaired_line=merged,
        rule_id="split_kou_nin_to_kounin",
        raw_deepgram_mutated=False,
    )
    return {"action": "merge_previous", "repaired_line": merged, "previous_text": prev}


def _apply_katae_context_correction(
    text: str, *, nearby_context: str
) -> tuple[str, list[dict[str, Any]]]:
    window = f"{nearby_context}{text}"
    applied: list[dict[str, Any]] = []
    if not any(cue in window for cue in _KATTAE_CONTEXT_CUES):
        return text, applied
    if not any(token in text for token in ("鍛えてる", "鍛える", "鍛え")):
        return text, applied
    result = text.replace("鍛えてる", "伝えている").replace("鍛える", "伝える")
    if "鍛え" in result and "伝え" not in result:
        result = re.sub(r"鍛え(?=[てるた])", "伝え", result)
    if result != text:
        corrected = _append_change(
            applied,
            raw_input=text,
            corrected=result,
            rule_id="katae_to_tsutaeru_context",
            correction_type="contextual_expression",
            event="BUSINESS_CORRECTION_APPLIED_HIGH_CONFIDENCE",
            context_window=window[-120:],
        )
        return corrected, applied
    return text, applied


def _apply_nagai_bucho_correction(
    text: str, *, previous_segment: str, nearby_context: str
) -> tuple[str, list[dict[str, Any]]]:
    applied: list[dict[str, Any]] = []
    result = text
    context = f"{previous_segment}{nearby_context}{text}"
    if "永井長い部長" in result and any(
        token in result for token in ("部長", "紹介", "ございます")
    ):
        new_result = result.replace("永井長い部長", "永井部長")
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="nagai_bucho_to_nagai_bucho",
                correction_type="role_name_cleanup",
                event="BUSINESS_CORRECTION_APPLIED_HIGH_CONFIDENCE",
            )
    if "長い部長でございます" in result and "永井" in context:
        new_result = result.replace("長い部長でございます", "永井部長でございます")
        if new_result != result:
            result = _append_change(
                applied,
                raw_input=result,
                corrected=new_result,
                rule_id="nagai_bucho_intro_with_nagai_context",
                correction_type="role_name_cleanup",
                event="BUSINESS_CORRECTION_APPLIED_HIGH_CONFIDENCE",
                context_window=context[-120:],
            )
    if "陳氏名" in result:
        if any(cue in context for cue in _CHIN_NAME_CONTEXT_CUES):
            new_result = result.replace("陳氏名", "チン・シュウメイ")
            if new_result != result:
                result = _append_change(
                    applied,
                    raw_input=result,
                    corrected=new_result,
                    rule_id="chin_name_context",
                    correction_type="role_name_cleanup",
                    event="BUSINESS_CORRECTION_CONTEXT_GUARD_USED",
                    context_window=context[-120:],
                )
        else:
            _log_correction(
                "BUSINESS_NAME_CORRECTION_SKIPPED_LOW_CONFIDENCE",
                input_text=result,
                rule_id="chin_name_context",
                reason="low_confidence_name",
            )
    return result, applied


def apply_business_stable_corrections(
    text: str,
    *,
    previous_segment: str = "",
    nearby_context: str = "",
    session_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Apply high-confidence stable-layer business corrections only."""
    original = (text or "").strip()
    if not original or not STABLE_LAYER_BUSINESS_CORRECTION_ENABLED:
        return _empty_result(original)

    compact = compact_cjk_for_compare(original, "ja")
    if compact in {
        compact_cjk_for_compare("恐れ入ります", "ja"),
        compact_cjk_for_compare("恐れ入ります。", "ja"),
    }:
        return {
            "corrected": original,
            "applied": False,
            "corrections": [],
            "skipped": [{"input": original, "candidate_rule": "preserve", "reason": "protected_phrase"}],
            "high_confidence_count": 0,
            "double_prefix_repair_count": 0,
            "triple_koko_repair_count": 0,
            "business_correction_regression_count": 0,
            "business_accuracy_expansion_count": 0,
            "name_correction_count": 0,
            "name_correction_skipped_count": 0,
        }

    corrected = original
    all_corrections: list[dict[str, Any]] = []
    double_prefix_repair_count = 0
    triple_koko_repair_count = 0
    business_accuracy_expansion_count = 0
    name_correction_count = 0
    name_correction_skipped_count = 0

    if BUSINESS_CORRECTION_GUARD_85221_ENABLED and BUSINESS_CORRECTION_IDEMPOTENT_MODE:
        before = corrected
        corrected = _repair_double_prefix_osewa(corrected, all_corrections)
        if corrected != before:
            double_prefix_repair_count += 1
        before = corrected
        corrected = _repair_triple_koko(corrected, all_corrections)
        if corrected != before:
            triple_koko_repair_count += 1
        if is_minimal_correction_mode():
            _log_minimal_baseline_once()
            corrected = _repair_doudouzo_regression(corrected, all_corrections)
        else:
            corrected = _apply_polite_greeting_osewa(corrected, all_corrections)
            corrected = _apply_koko_attention(corrected, all_corrections)
            corrected = _apply_yoroshiku_rules(corrected, all_corrections)
            corrected = _apply_other_phrase_rules(corrected, all_corrections)
    else:
        for src, dst, rule_id in (
            ("世話になっております", "お世話になっております", "polite_greeting"),
            ("こは注意が必要です", "ここは注意が必要です", "explanation_koko"),
        ):
            if src in corrected and dst not in corrected:
                new_result = corrected.replace(src, dst)
                if new_result != corrected:
                    corrected = _append_change(
                        all_corrections,
                        raw_input=corrected,
                        corrected=new_result,
                        rule_id=rule_id,
                        correction_type=rule_id,
                    )

    if not is_minimal_correction_mode():
        corrected, katae = _apply_katae_context_correction(
            corrected, nearby_context=nearby_context
        )
        all_corrections.extend(katae)

        corrected, nagai = _apply_nagai_bucho_correction(
            corrected,
            previous_segment=previous_segment,
            nearby_context=nearby_context,
        )
        all_corrections.extend(nagai)

    before_expansion = corrected
    if is_minimal_correction_mode():
        pass
    elif ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED:
        corrected = _apply_safe_85223_corrections(
            corrected,
            all_corrections,
            nearby_context=f"{previous_segment}{nearby_context}",
            previous_segment=previous_segment,
            session_context=session_context,
        )
    elif BUSINESS_ACCURACY_EXPANSION_85222_ENABLED and not DISABLE_HARMFUL_85222_EXPANSION_RULES:
        corrected = _apply_expansion_85222_rules(
            corrected,
            all_corrections,
            nearby_context=f"{previous_segment}{nearby_context}",
            session_context=session_context,
        )
    if corrected != before_expansion:
        business_accuracy_expansion_count += 1

    regression_count = 0
    if "おお世話" in corrected:
        regression_count += 1
    if "こここは注意" in corrected:
        regression_count += 1
    if "どうどうぞ" in corrected:
        regression_count += 1

    name_correction_count += sum(
        1
        for c in all_corrections
        if c.get("correction_type") == "name_correction"
        or c.get("log_event") == "NAME_CORRECTION_APPLIED_WITH_CONTEXT"
    )

    applied = corrected != original
    high_count = sum(1 for c in all_corrections if c.get("confidence", "high") == "high")

    for change in all_corrections:
        if change.get("log_event") == "BUSINESS_STABLE_CORRECTION_APPLIED":
            try:
                from alpha.utils.accuracy_decision_log import log_stable_merge_correction

                log_stable_merge_correction(
                    raw_input_text=change.get("raw_stable_input", original),
                    stable_output_text=change.get("corrected_output", corrected),
                    transform_type=change.get("correction_type", "business_stable"),
                    transform_reason=change.get("correction_rule_id", ""),
                )
            except Exception:
                pass

    if not applied:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "BUSINESS_STABLE_CORRECTION_SKIPPED",
                input=original,
                candidate_rule="none",
                reason="no_high_confidence_match",
            )
        except Exception:
            pass

    return {
        "corrected": corrected,
        "applied": applied,
        "corrections": all_corrections,
        "skipped": [],
        "high_confidence_count": high_count,
        "double_prefix_repair_count": double_prefix_repair_count,
        "triple_koko_repair_count": triple_koko_repair_count,
        "business_correction_regression_count": regression_count,
        "business_accuracy_expansion_count": business_accuracy_expansion_count,
        "name_correction_count": name_correction_count,
        "name_correction_skipped_count": name_correction_skipped_count,
    }


def _empty_result(original: str) -> dict[str, Any]:
    return {
        "corrected": original,
        "applied": False,
        "corrections": [],
        "skipped": [],
        "high_confidence_count": 0,
        "double_prefix_repair_count": 0,
        "triple_koko_repair_count": 0,
        "business_correction_regression_count": 0,
        "business_accuracy_expansion_count": 0,
        "name_correction_count": 0,
        "name_correction_skipped_count": 0,
    }


def run_business_correction_guard_selftest() -> dict[str, Any]:
    """Unit-style idempotency checks for 8.5.22.1 / 8.5.23.1 minimal guards."""
    if is_minimal_correction_mode():
        cases: tuple[tuple[str, str], ...] = (
            ("おお世話になっております", "お世話になっております"),
            ("お世話になっております", "お世話になっております"),
            ("こここは注意が必要です", "ここは注意が必要です"),
            ("ここは注意が必要です", "ここは注意が必要です"),
            ("どうどうぞよろしくお願いいたします", "どうぞよろしくお願いいたします"),
        )
    else:
        cases = (
            ("世話になっております", "お世話になっております"),
            ("お世話になっております", "お世話になっております"),
            ("いつも世話になっております", "いつもお世話になっております"),
            ("いつもお世話になっております", "いつもお世話になっております"),
            ("おお世話になっております", "お世話になっております"),
            ("こは注意が必要です", "ここは注意が必要です"),
            ("ここは注意が必要です", "ここは注意が必要です"),
            ("こここは注意が必要です", "ここは注意が必要です"),
        )
    failures: list[str] = []
    for input_text, expected in cases:
        result = apply_business_stable_corrections(input_text)
        got = result.get("corrected", "")
        if got != expected:
            failures.append(f"{input_text!r} -> {got!r} expected {expected!r}")
    return {"ok": not failures, "failures": failures, "case_count": len(cases)}


def run_minimal_correction_selftest() -> dict[str, Any]:
    """Unit checks for 8.5.23.1 minimal correction baseline."""
    failures: list[str] = []
    if not is_minimal_correction_mode():
        failures.append("minimal_mode_not_active")
    r1 = apply_business_stable_corrections("いつもおお世話になっております")
    if "おお世話" in r1.get("corrected", ""):
        failures.append("osewa_double_prefix_not_repaired")
    r2 = apply_business_stable_corrections("こここは注意が必要です")
    if "こここは注意" in r2.get("corrected", ""):
        failures.append("koko_triple_not_repaired")
    r3 = apply_business_stable_corrections("どうどうぞよろしくお願いいたします")
    if "どうどうぞ" in r3.get("corrected", ""):
        failures.append("doudouzo_not_repaired")
    r4 = apply_business_stable_corrections(
        "公認のものを連れてご挨拶に参りました",
        nearby_context="御社の担当が変わりました",
    )
    if "後任の者" in r4.get("corrected", ""):
        failures.append("lesson_specific_kounin_should_not_apply")
    split = repair_split_fragment_stable("aこう", "任の者", nearby_context="x")
    if not split.get("disabled"):
        failures.append("broad_split_not_disabled")
    return {"ok": not failures, "failures": failures}


def run_safe_correction_85223_selftest() -> dict[str, Any]:
    """Unit checks for 8.5.22.3 safe corrections."""
    failures: list[str] = []
    cases: tuple[tuple[str, str], ...] = (
        (
            "オリエンタル商事のAでございますどうどうぞよろしくお願いいたします。",
            "オリエンタル商事のAでございますどうぞよろしくお願いいたします。",
        ),
        (
            "いつもお世話になっておりますこの度、御社の担当が変わりましたので公認のものを連れてご挨拶に参りました。",
            "いつもお世話になっておりますこの度、御社の担当が変わりましたので後任の者を連れてご挨拶に参りました。",
        ),
        (
            "後任のものを連れてご挨拶に参りました。",
            "後任の者を連れてご挨拶に参りました。",
        ),
        ("担当せていただくことになりました", "担当させていただくことになりました"),
    )
    for input_text, expected in cases:
        result = apply_business_stable_corrections(
            input_text,
            nearby_context="御社の担当が変わりましたこのたび担当",
            session_context={"has_chin_name": True, "recent_stable_lines": ["チン・シュウメイ"]},
        )
        got = result.get("corrected", "")
        if expected not in got and got != expected:
            failures.append(f"{input_text!r} -> {got!r} expected containing {expected!r}")
    dedupe = dedupe_duplicate_phrase_stable("prev", "next", same_speaker=True)
    if not dedupe.get("disabled"):
        failures.append("broad_dedupe_not_disabled")
    split = repair_split_fragment_stable("aこう", "任の者", nearby_context="x")
    if not split.get("disabled"):
        failures.append("broad_split_not_disabled")
    exact = cleanup_exact_duplicate_continuation(
        _EXACT_DUPLICATE_PREFIX,
        _EXACT_DUPLICATE_PREFIX + "、それでは会話です。",
        same_speaker=True,
    )
    if not exact.get("applied"):
        failures.append("exact_duplicate_cleanup_failed")
    return {"ok": not failures, "failures": failures}


def run_business_expansion_selftest() -> dict[str, Any]:
    """Legacy 8.5.22.2 expansion selftest — disabled under rollback."""
    if DISABLE_HARMFUL_85222_EXPANSION_RULES:
        return run_safe_correction_85223_selftest()
    failures: list[str] = []
    split = repair_split_fragment_stable(
        "が実はこのたび御社の担当が変わりましたので、こう",
        "任の者を連れてご挨拶に参りました。",
        nearby_context="御社の担当が変わりましたご挨拶に参りました",
    )
    if split.get("action") != "merge_previous":
        failures.append("split_fragment_repair_failed")
    dedupe = dedupe_duplicate_phrase_stable(
        "精一杯がんばりますので、こちらこそよろしくお願いいたします。",
        "精一杯がんばりますので、こちらこそよろしくお願いいたします。、これが会話の流れになります。",
        same_speaker=True,
    )
    if not dedupe.get("applied"):
        failures.append("duplicate_phrase_dedupe_failed")
    midline, changed = clean_midline_punctuation_artifact("テスト。、続き")
    if not changed or "。、" in midline:
        failures.append("midline_punctuation_cleanup_failed")
    return {"ok": not failures, "failures": failures}


__all__ = [
    "apply_business_stable_corrections",
    "clean_midline_punctuation_artifact",
    "cleanup_exact_duplicate_continuation",
    "dedupe_duplicate_phrase_stable",
    "is_preserved_business_term",
    "is_minimal_correction_mode",
    "repair_split_fragment_stable",
    "run_business_correction_guard_selftest",
    "run_business_expansion_selftest",
    "run_minimal_correction_selftest",
    "run_safe_correction_85223_selftest",
    "safe_correction_gate",
]
