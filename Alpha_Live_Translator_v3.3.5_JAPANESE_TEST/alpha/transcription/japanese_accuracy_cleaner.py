"""Conservative Japanese stable-text cleanup candidates for accuracy mode."""

from __future__ import annotations

import re
from typing import Any, Optional

_REASON_CONTEXT_MARKERS = (
    "理由",
    "どうして",
    "なんで",
    "その理由",
    "理由を教えて",
)

_KEIGO_SUBJECT_PREFIXES = ("あなたが", "私が", "僕が")
_KEIGO_FOLLOWERS = ("は誰", "はだれ", "は誰ですか", "は誰々")

_SONKEI_TEIRU_CONTEXT_MARKERS = (
    "尊敬してい人は誰々です",
    "僕が尊敬してい人",
    "私が尊敬してい人",
    "あなたが尊敬してい人",
)

_SARASARA_EXACT_PHRASES = (
    "さらて普通に答える",
    "さらって普通に答える",
    "さらさって普通に答える",
)

_LEADING_YO_PREVIOUS_SUFFIXES = (
    "思うんです",
    "思うんですよ",
    "あると思うんです",
)


def _apply_literal_replacement(
    text: str,
    *,
    before: str,
    after: str,
    confidence: float,
    label: str,
    changes: list[dict[str, Any]],
) -> tuple[str, float]:
    count = text.count(before)
    if count <= 0:
        return text, 1.0
    updated = text.replace(before, after)
    changes.append(
        {
            "label": label,
            "before": before,
            "after": after,
            "count": count,
        }
    )
    return updated, confidence


def _apply_regex_replacement(
    text: str,
    *,
    pattern: str,
    replacement: str,
    confidence: float,
    label: str,
    changes: list[dict[str, Any]],
) -> tuple[str, float]:
    updated, count = re.subn(pattern, replacement, text)
    if count <= 0:
        return text, 1.0
    changes.append(
        {
            "label": label,
            "before_pattern": pattern,
            "after": replacement,
            "count": count,
        }
    )
    return updated, confidence


def _apply_keigo_respect_fix(
    text: str,
    changes: list[dict[str, Any]],
) -> tuple[str, float]:
    candidate = text
    confidence_floor = 1.0
    for prefix in _KEIGO_SUBJECT_PREFIXES:
        broken = f"{prefix}敬してる人"
        fixed = f"{prefix}尊敬してる人"
        if broken not in candidate:
            continue
        follower_ok = any(
            candidate.find(broken) >= 0
            and candidate[candidate.find(broken) + len(broken) :].startswith(follower)
            for follower in _KEIGO_FOLLOWERS
        )
        if not follower_ok:
            continue
        candidate, confidence = _apply_literal_replacement(
            candidate,
            before=broken,
            after=fixed,
            confidence=0.96,
            label="contextual_keigo_son_fix",
            changes=changes,
        )
        confidence_floor = min(confidence_floor, confidence)
    return candidate, confidence_floor


def _apply_sonkei_teiru_missing_fix(
    text: str,
    changes: list[dict[str, Any]],
) -> tuple[str, float]:
    candidate = text
    confidence_floor = 1.0
    if not any(marker in candidate for marker in _SONKEI_TEIRU_CONTEXT_MARKERS):
        return candidate, confidence_floor
    broken = "尊敬してい人"
    if broken not in candidate:
        return candidate, confidence_floor
    candidate, confidence = _apply_literal_replacement(
        candidate,
        before=broken,
        after="尊敬してる人",
        confidence=0.96,
        label="contextual_sonkei_teiru_missing_fix",
        changes=changes,
    )
    return candidate, min(confidence_floor, confidence)


def _apply_sarasara_answer_exact_fix(
    text: str,
    changes: list[dict[str, Any]],
) -> tuple[str, float]:
    candidate = text
    confidence_floor = 1.0
    if "普通に答える" not in candidate:
        return candidate, confidence_floor
    for phrase in _SARASARA_EXACT_PHRASES:
        if phrase not in candidate:
            continue
        candidate, confidence = _apply_literal_replacement(
            candidate,
            before=phrase,
            after="さらっと普通に答える",
            confidence=0.91,
            label="contextual_sarasara_answer_exact",
            changes=changes,
        )
        confidence_floor = min(confidence_floor, confidence)
    return candidate, confidence_floor


def _apply_riifu_to_riyuu_fix(
    text: str,
    *,
    nearby_context: str,
    changes: list[dict[str, Any]],
) -> tuple[str, float]:
    broken = "リーフが浮かんでこない"
    if broken not in text:
        return text, 1.0
    context = f"{nearby_context or ''}{text}"
    if not any(marker in context for marker in _REASON_CONTEXT_MARKERS):
        return text, 1.0
    confidence = 0.96 if "理由" in context else 0.94
    return _apply_literal_replacement(
        text,
        before=broken,
        after="理由が浮かんでこない",
        confidence=confidence,
        label="contextual_riifu_to_riyuu",
        changes=changes,
    )


def repair_leading_yo_fragment(
    text: str,
    *,
    previous_segment: str = "",
) -> dict[str, Any]:
    original = (text or "").strip()
    previous = (previous_segment or "").strip()
    if not original.startswith("よ私が思うに") or not previous:
        return {
            "original": original,
            "candidate": original,
            "repaired": False,
            "applied_to_ui": False,
            "reason": "no_leading_yo_repair_needed",
        }
    for suffix in _LEADING_YO_PREVIOUS_SUFFIXES:
        if not previous.endswith(suffix):
            continue
        repaired = f"私が思うに{original[len('よ私が思うに'):]}"
        return {
            "original": original,
            "candidate": repaired,
            "repaired": True,
            "applied_to_ui": True,
            "previous_segment_tail": previous[-40:],
            "update_previous_suffix": (
                "よ" if suffix == "思うんです" else ""
            ),
            "reason": "leading_yo_fragment_repaired",
        }
    return {
        "original": original,
        "candidate": original,
        "repaired": False,
        "applied_to_ui": False,
        "reason": "leading_yo_without_matching_previous_tail",
    }


def build_japanese_cleanup_candidate(
    text: str,
    *,
    previous_segment: str = "",
    nearby_context: str = "",
) -> dict[str, Any]:
    original = (text or "").strip()
    candidate = original
    changes: list[dict[str, Any]] = []
    confidence_floor = 1.0

    yo_repair = repair_leading_yo_fragment(
        candidate,
        previous_segment=previous_segment,
    )
    if yo_repair["repaired"]:
        candidate = yo_repair["candidate"]
        changes.append(
            {
                "label": "leading_yo_fragment_repaired",
                "before": yo_repair["original"],
                "after": yo_repair["candidate"],
                "count": 1,
            }
        )
        confidence_floor = min(confidence_floor, 0.95)

    candidate, confidence = _apply_literal_replacement(
        candidate,
        before="っいう",
        after="っていう",
        confidence=0.99,
        label="literal_small_tsu_to_tteiu",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_regex_replacement(
        candidate,
        pattern=r"(か|どうか|なんで|なんでか|そういう|なんでそういう意見なのか)ていう",
        replacement=r"\1っていう",
        confidence=0.95,
        label="contextual_teiu_to_tteiu",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_literal_replacement(
        candidate,
        before="なんでかいうと",
        after="なんでかっていうと",
        confidence=0.97,
        label="literal_nandeka_iuto_fix",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_literal_replacement(
        candidate,
        before="なんでか言うと",
        after="なんでかっていうと",
        confidence=0.97,
        label="literal_nandeka_iu_fix",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_literal_replacement(
        candidate,
        before="かもしれなって",
        after="かもしれないって",
        confidence=0.96,
        label="literal_kamoshirenatte",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_literal_replacement(
        candidate,
        before="かもしれなって思いました",
        after="かもしれないって思いました",
        confidence=0.96,
        label="literal_kamoshirenatte_omoimashita",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_regex_replacement(
        candidate,
        pattern=r"かもしれなっ思(う|った|いました)",
        replacement=r"かもしれないって思\1",
        confidence=0.96,
        label="regex_kamoshirena_small_tsu_omou",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_regex_replacement(
        candidate,
        pattern=r"([ぁ-んァ-ヶ一-龯ー]{3,})っ思いました",
        replacement=r"\1って思いました",
        confidence=0.95,
        label="regex_clause_small_tsu_omoimashita",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_literal_replacement(
        candidate,
        before="教えてくだい",
        after="教えてください",
        confidence=0.99,
        label="literal_kudasai_fix",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_literal_replacement(
        candidate,
        before="なんでっわかんない",
        after="なんでってわかんない",
        confidence=0.97,
        label="literal_nandette_fix",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_keigo_respect_fix(candidate, changes)
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_sonkei_teiru_missing_fix(candidate, changes)
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_riifu_to_riyuu_fix(
        candidate,
        nearby_context=nearby_context or previous_segment,
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_literal_replacement(
        candidate,
        before="理由が浮かんでこなんだよね",
        after="理由が浮かんでこないんだよね",
        confidence=0.96,
        label="literal_riyuu_konanda_yo_ne",
        changes=changes,
    )
    confidence_floor = min(confidence_floor, confidence)

    confidence_floor = min(confidence_floor, confidence)

    candidate, confidence = _apply_sarasara_answer_exact_fix(candidate, changes)
    confidence_floor = min(confidence_floor, confidence)

    candidate = candidate.strip()
    changed = candidate != original
    if not changed:
        return {
            "original": original,
            "candidate": original,
            "changes": [],
            "confidence": 0.0,
            "applied_to_ui": False,
            "reason": "no_conservative_cleanup_change",
        }

    confidence_value = round(confidence_floor, 2)
    applied_to_ui = confidence_value >= 0.90
    return {
        "original": original,
        "candidate": candidate,
        "changes": changes,
        "confidence": confidence_value,
        "applied_to_ui": applied_to_ui,
        "reason": (
            "high_confidence_conservative_cleanup"
            if applied_to_ui
            else "candidate_below_ui_confidence_threshold"
        ),
    }


def detect_keyterm_overbias_candidates(text: str) -> list[dict[str, Any]]:
    segment = (text or "").strip()
    if not segment:
        return []

    findings: list[dict[str, Any]] = []
    for pattern, confidence, label in (
        (r"な(?:と|んと)か監督", 0.83, "suspicious_keyterm_bias_around_kantoku"),
        (r"なんとか.?監督", 0.83, "suspicious_keyterm_bias_around_nantoka"),
        (r"(?:さらささら|サラサさら|さらサラ|サラサラ).{0,4}って", 0.72, "suspicious_sarasara_bias"),
    ):
        match = re.search(pattern, segment)
        if not match:
            continue
        findings.append(
            {
                "text": segment,
                "suspected_area": match.group(0),
                "confidence": round(confidence, 2),
                "reason": label,
            }
        )
    return findings


def detect_raw_stt_error_candidates(text: str) -> list[dict[str, Any]]:
    segment = (text or "").strip()
    if not segment:
        return []

    findings: list[dict[str, Any]] = []
    for pattern, label in (
        (r"な(?:と|んと)か監督", "suspicious_kantoku_stt_error"),
        (r"リーフが浮かんでこない", "suspicious_riifu_stt_error"),
        (r"(?:あなたが|私が|僕が)敬してる人", "missing_son_before_keigo"),
        (r"尊敬してい人", "missing_teiru_in_sonkei_phrase"),
    ):
        match = re.search(pattern, segment)
        if not match:
            continue
        findings.append(
            {
                "text": segment,
                "suspected_area": match.group(0),
                "reason": label,
            }
        )
    return findings


_BUSINESS_COMPANY_CONTEXT = (
    "会社",
    "お客様",
    "自社",
    "御社",
    "挨拶",
    "紹介",
    "営業",
    "開発",
)

_BUSINESS_SUCCESSOR_CONTEXT = (
    "担当",
    "前任",
    "後の担当者",
    "連れて",
    "ご挨拶",
    "担当交代",
    "前任者",
    "後任",
)

_BUSINESS_KUDASAI_CONTEXT = (
    "リピート",
    "確認",
    "表",
    "発音",
    "使って",
    "注意",
)

_BUSINESS_GREETING_CONTEXT = (
    "挨拶",
    "表現",
    "お世話",
)

_BUSINESS_CANDIDATE_PATTERNS: list[tuple[str, str, str]] = [
    (r"エトー様|干藤様", "etou_to_etou_name", "name_variant"),
    (r"陳州名|珍習名|珍州名", "chin_shumei_variant", "name_variant"),
    (r"長い", "nagai_to_nagai_name", "name_variant"),
    (r"他者紹介", "tasha_vs_tasha_intro", "company_term"),
    (r"何々さん|何々様", "nanan_variant", "placeholder_name"),
    (r"私とも|わたくしども", "watashidomo_variant", "pronoun_variant"),
]

_DUPLICATE_DAMAGE_PATTERNS: list[tuple[str, str]] = [
    ("いついつも", "いつも"),
    ("このこのたび", "このたび"),
    ("おお世話", "お世話"),
    ("後後任", "後任"),
    ("担当担当交代", "担当交代"),
    ("使役使役形", "使役形"),
    ("参参りました", "参りました"),
    ("くださいください", "ください"),
]

_idempotency_guard_logged = False
_live_skip_second_idempotency_pass = False
_idempotency_pass_log_count = 0
_MAX_IDEMPOTENCY_PASS_LOGS = 3
_selftest_completed = False
_skip_optional_risk_scan = False


def run_business_cleanup_selftest_once() -> bool:
    """Run idempotency unit cases once at startup — not per live segment."""
    global _selftest_completed, _live_skip_second_idempotency_pass
    if _selftest_completed:
        return True
    _selftest_completed = True
    cases = [
        "いつもお世話になっております",
        "このたび御社のサービスを",
        "翌日までにお送りします",
        "他社との比較検討",
        "後任の方に引き継ぎ",
        "担当交代の件",
        "使役形について",
        "参りましたので",
    ]
    failures: list[str] = []
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("BUSINESS_CLEANUP_SELFTEST_RUN_ONCE", case_count=len(cases))
    except Exception:
        pass
    for text in cases:
        first = normalize_business_cleanup_once(text, verify_second_pass=True)
        second = normalize_business_cleanup_once(first["candidate"], verify_second_pass=True)
        if second["candidate"] != first["candidate"]:
            failures.append(text[:40])
        for bad in ("いついつも", "このこのたび"):
            if bad in first["candidate"]:
                failures.append(f"dup_{bad}")
    passed = not failures
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        if passed:
            jp_accuracy_log("BUSINESS_CLEANUP_SELFTEST_PASSED", case_count=len(cases))
            jp_accuracy_log("BUSINESS_CLEANUP_LIVE_OVERHEAD_REDUCED")
            _live_skip_second_idempotency_pass = True
        else:
            jp_accuracy_log("BUSINESS_CLEANUP_SELFTEST_FAILED", failures=failures[:5])
    except Exception:
        pass
    return passed


def _context_has_any(context: str, markers: tuple[str, ...]) -> bool:
    return any(marker in context for marker in markers)


def _record_rule(
    applied: list[dict[str, Any]],
    *,
    rule_id: str,
    confidence: str,
    raw_input: str,
    stable_before: str,
    stable_after: str,
    reason: str,
    context_window: str,
    skipped: bool = False,
    skip_reason: str = "",
) -> None:
    entry: dict[str, Any] = {
        "rule_id": rule_id,
        "confidence": confidence,
        "raw_input": raw_input,
        "stable_before": stable_before,
        "stable_after": stable_after,
        "reason": reason if not skipped else skip_reason,
        "context_window": context_window[-160:],
        "applied_to_raw": False,
        "applied_to_ui": confidence == "high" and not skipped,
        "skipped": skipped,
    }
    applied.append(entry)


def _apply_literal_once(
    text: str,
    *,
    before: str,
    after: str,
    rule_id: str,
    confidence: str,
    reason: str,
    context_window: str,
    applied: list[dict[str, Any]],
    stats: dict[str, int],
    skip_if_after_present: bool = True,
    replace_all: bool = False,
) -> str:
    if before not in text:
        return text
    if skip_if_after_present and after in text and before not in text.replace(after, "", 1):
        stats["skipped_already_correct_count"] = (
            int(stats.get("skipped_already_correct_count", 0)) + 1
        )
        _record_rule(
            applied,
            rule_id=rule_id,
            confidence=confidence,
            raw_input=before,
            stable_before=text,
            stable_after=text,
            reason=reason,
            context_window=context_window,
            skipped=True,
            skip_reason="already_correct",
        )
        return text
    if after in text and before in text:
        idx = text.find(before)
        surrounding = text[max(0, idx - 4) : idx + len(before) + 4]
        if after in surrounding:
            stats["duplicate_prevented_count"] = (
                int(stats.get("duplicate_prevented_count", 0)) + 1
            )
            _record_rule(
                applied,
                rule_id=rule_id,
                confidence=confidence,
                raw_input=before,
                stable_before=text,
                stable_after=text,
                reason=reason,
                context_window=context_window,
                skipped=True,
                skip_reason="duplicate_prevented",
            )
            return text
    updated = text.replace(before, after) if replace_all else text.replace(before, after, 1)
    if updated == text:
        return text
    _record_rule(
        applied,
        rule_id=rule_id,
        confidence=confidence,
        raw_input=before,
        stable_before=text,
        stable_after=updated,
        reason=reason,
        context_window=context_window,
    )
    return updated


def _apply_regex_once(
    text: str,
    *,
    pattern: str,
    replacement: str,
    rule_id: str,
    confidence: str,
    reason: str,
    context_window: str,
    applied: list[dict[str, Any]],
    stats: dict[str, int],
    raw_input_label: str = "",
) -> str:
    match = re.search(pattern, text)
    if not match:
        return text
    updated = re.sub(pattern, replacement, text, count=1)
    if updated == text:
        return text
    _record_rule(
        applied,
        rule_id=rule_id,
        confidence=confidence,
        raw_input=raw_input_label or match.group(0),
        stable_before=text,
        stable_after=updated,
        reason=reason,
        context_window=context_window,
    )
    return updated


def _apply_kono_tabi_gosha_rules(
    text: str,
    *,
    context_window: str,
    applied: list[dict[str, Any]],
    stats: dict[str, int],
) -> str:
    candidate = text
    if "このたび御社" in candidate:
        return candidate

    if "この度御社" in candidate:
        candidate = _apply_literal_once(
            candidate,
            before="この度御社",
            after="このたび御社",
            rule_id="kono_tabi_gosha",
            confidence="high",
            reason="opening_business_phrase_kono_do",
            context_window=context_window,
            applied=applied,
            stats=stats,
            skip_if_after_present=False,
        )
        if "このたび御社" in candidate:
            return candidate

    candidate = _apply_regex_once(
        candidate,
        pattern=r"(?<![この])たび御社",
        replacement="このたび御社",
        rule_id="kono_tabi_gosha",
        confidence="high",
        reason="opening_business_phrase_tabi_gosha",
        context_window=context_window,
        applied=applied,
        stats=stats,
        raw_input_label="たび御社",
    )

    if "このたび御社" not in candidate:
        candidate = _apply_regex_once(
            candidate,
            pattern=r"(?<![このたび])(?<!この度)(?<![たび])度御社",
            replacement="このたび御社",
            rule_id="kono_tabi_gosha",
            confidence="high",
            reason="opening_business_phrase_do_gosha",
            context_window=context_window,
            applied=applied,
            stats=stats,
            raw_input_label="度御社",
        )
    return candidate


def _apply_itsumo_osewa_rule(
    text: str,
    *,
    context: str,
    context_window: str,
    applied: list[dict[str, Any]],
    stats: dict[str, int],
) -> str:
    if "もお世話になっております" not in text:
        return text
    if not _context_has_any(context, _BUSINESS_GREETING_CONTEXT):
        return text
    if "いつもお世話になっております" in text and "もお世話になっております" not in text.replace(
        "いつもお世話になっております", "", 1
    ):
        stats["skipped_already_correct_count"] = (
            int(stats.get("skipped_already_correct_count", 0)) + 1
        )
        return text
    return _apply_regex_once(
        text,
        pattern=r"(?<!いつ)もお世話になっております",
        replacement="いつもお世話になっております",
        rule_id="itsumo_osewa",
        confidence="high",
        reason="greeting_lesson_context",
        context_window=context_window,
        applied=applied,
        stats=stats,
        raw_input_label="もお世話になっております",
    )


def sanitize_business_duplicate_prefixes(text: str) -> tuple[str, list[str]]:
    """Safety-net sanitizer for known cleanup duplicate prefixes."""
    candidate = text
    fixed: list[str] = []
    for bad, good in _DUPLICATE_DAMAGE_PATTERNS:
        if bad in candidate:
            candidate = candidate.replace(bad, good)
            fixed.append(bad)
    return candidate, fixed


def detect_duplicate_damage(text: str) -> list[str]:
    segment = (text or "").strip()
    if not segment:
        return []
    return [bad for bad, _good in _DUPLICATE_DAMAGE_PATTERNS if bad in segment]


def _apply_business_rules_core(
    text: str,
    *,
    nearby_context: str = "",
    previous_segment: str = "",
    applied: list[dict[str, Any]],
    stats: dict[str, int],
) -> str:
    candidate = (text or "").strip()
    context = f"{previous_segment}{nearby_context}{candidate}"
    context_window = context

    if "他者の人" in candidate and "他社の人" not in candidate:
        if _context_has_any(context, _BUSINESS_COMPANY_CONTEXT):
            candidate = _apply_literal_once(
                candidate,
                before="他者の人",
                after="他社の人",
                rule_id="tasha_no_hito_to_tasha",
                confidence="high",
                reason="business_company_context",
                context_window=context_window,
                applied=applied,
                stats=stats,
                replace_all=True,
            )
    elif "他社の人" in candidate:
        stats["skipped_already_correct_count"] = (
            int(stats.get("skipped_already_correct_count", 0)) + 1
        )

    for before, after in (
        ("短頭交代の挨拶", "担当交代の挨拶"),
        ("短頭交代", "担当交代"),
    ):
        if before in candidate and after not in candidate:
            candidate = _apply_literal_once(
                candidate,
                before=before,
                after=after,
                rule_id="tantou_koutai_homophone",
                confidence="high",
                reason="role_change_homophone",
                context_window=context_window,
                applied=applied,
                stats=stats,
            )

    if "公認の者" in candidate and "後任の者" not in candidate:
        if _context_has_any(context, _BUSINESS_SUCCESSOR_CONTEXT):
            candidate = _apply_literal_once(
                candidate,
                before="公認の者",
                after="後任の者",
                rule_id="kounin_no_mono_to_kounin",
                confidence="high",
                reason="successor_context",
                context_window=context_window,
                applied=applied,
                stats=stats,
            )
    elif (
        "公認" in candidate
        and "後任" not in candidate
        and _context_has_any(context, ("担当交代", "前任者", "後任", "後の担当者"))
    ):
        candidate = _apply_literal_once(
            candidate,
            before="公認",
            after="後任",
            rule_id="kounin_to_kounin",
            confidence="high",
            reason="successor_context_strong",
            context_window=context_window,
            applied=applied,
            stats=stats,
        )

    if "使役系" in candidate and "使役形" not in candidate:
        if _context_has_any(
            context, ("させていただきます", "ていただきます", "動詞", "担当")
        ):
            candidate = _apply_literal_once(
                candidate,
                before="使役系",
                after="使役形",
                rule_id="shieki_kei_to_shieki_kei",
                confidence="high",
                reason="grammar_lesson_context",
                context_window=context_window,
                applied=applied,
                stats=stats,
            )

    if "ご挨拶に回りました" in candidate and "ご挨拶に参りました" not in candidate:
        candidate = _apply_literal_once(
            candidate,
            before="ご挨拶に回りました",
            after="ご挨拶に参りました",
            rule_id="mawarimashita_to_mairimashita",
            confidence="high",
            reason="polite_visit_expression",
            context_window=context_window,
            applied=applied,
            stats=stats,
        )

    if "くだい" in candidate and "ください" not in candidate:
        if _context_has_any(context, _BUSINESS_KUDASAI_CONTEXT):
            candidate = _apply_literal_once(
                candidate,
                before="くだい",
                after="ください",
                rule_id="kudai_to_kudasai",
                confidence="high",
                reason="polite_instruction_context",
                context_window=context_window,
                applied=applied,
                stats=stats,
            )

    candidate = _apply_kono_tabi_gosha_rules(
        candidate,
        context_window=context_window,
        applied=applied,
        stats=stats,
    )

    candidate = _apply_itsumo_osewa_rule(
        candidate,
        context=context,
        context_window=context_window,
        applied=applied,
        stats=stats,
    )

    if "疲れ様でした" in candidate:
        if "授業疲れ様でした" in candidate:
            candidate = _apply_literal_once(
                candidate,
                before="授業疲れ様でした",
                after="授業、おつかれさまでした",
                rule_id="otsukaresama_opening",
                confidence="high",
                reason="lesson_opening_greeting",
                context_window=context_window,
                applied=applied,
                stats=stats,
            )
        elif candidate.strip().startswith("疲れ様でした") and "おつかれさまでした" not in candidate:
            candidate = _apply_literal_once(
                candidate,
                before="疲れ様でした",
                after="おつかれさまでした",
                rule_id="otsukaresama_opening_short",
                confidence="high",
                reason="lesson_opening_greeting",
                context_window=context_window,
                applied=applied,
                stats=stats,
            )

    return candidate.strip()


def normalize_business_cleanup_once(
    text: str,
    *,
    nearby_context: str = "",
    previous_segment: str = "",
    verify_second_pass: Optional[bool] = None,
) -> dict[str, Any]:
    """Apply business cleanup once with idempotency guard and duplicate protection."""
    global _idempotency_guard_logged, _idempotency_pass_log_count, _live_skip_second_idempotency_pass
    import time as _time

    seg_start = _time.perf_counter()
    original = (text or "").strip()
    applied: list[dict[str, Any]] = []
    stats: dict[str, int] = {
        "skipped_already_correct_count": 0,
        "duplicate_prevented_count": 0,
        "duplicate_damage_detected_count": 0,
        "duplicate_damage_fixed_count": 0,
        "duplicate_damage_reverted_count": 0,
        "idempotency_check_failed_count": 0,
        "sanitizer_applied_count": 0,
    }

    if not _idempotency_guard_logged:
        _idempotency_guard_logged = True
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("BUSINESS_JP_CLEANUP_IDEMPOTENCY_GUARD_ACTIVE")
        except Exception:
            pass

    candidate = _apply_business_rules_core(
        original,
        nearby_context=nearby_context,
        previous_segment=previous_segment,
        applied=applied,
        stats=stats,
    )

    sanitized, fixed_patterns = sanitize_business_duplicate_prefixes(candidate)
    if fixed_patterns:
        stats["sanitizer_applied_count"] = len(fixed_patterns)
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            for pattern in fixed_patterns:
                jp_accuracy_log(
                    "BUSINESS_JP_DUPLICATE_SANITIZER_APPLIED",
                    pattern=pattern,
                    stable_before=candidate[:160],
                    stable_after=sanitized[:160],
                )
        except Exception:
            pass
        candidate = sanitized

    damage_before_commit = detect_duplicate_damage(candidate)
    if damage_before_commit:
        stats["duplicate_damage_detected_count"] = len(damage_before_commit)
        repaired, fixed = sanitize_business_duplicate_prefixes(candidate)
        remaining = detect_duplicate_damage(repaired)
        if not remaining:
            stats["duplicate_damage_fixed_count"] = len(damage_before_commit)
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "BUSINESS_JP_DUPLICATE_DAMAGE_DETECTED",
                    patterns=damage_before_commit,
                    stable_before=original[:160],
                )
                jp_accuracy_log(
                    "BUSINESS_JP_DUPLICATE_DAMAGE_FIXED",
                    patterns=fixed,
                    stable_after=repaired[:160],
                )
            except Exception:
                pass
            candidate = repaired
        else:
            stats["duplicate_damage_reverted_count"] = 1
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "BUSINESS_JP_DUPLICATE_DAMAGE_DETECTED",
                    patterns=damage_before_commit,
                    stable_before=original[:160],
                )
                jp_accuracy_log(
                    "BUSINESS_JP_DUPLICATE_DAMAGE_REVERTED",
                    patterns=remaining,
                    reverted_to=original[:160],
                )
            except Exception:
                pass
            candidate = original
            applied = [a for a in applied if not a.get("applied_to_ui")]

    do_second_pass = verify_second_pass if verify_second_pass is not None else (
        not _live_skip_second_idempotency_pass
    )
    if do_second_pass:
        second_pass = _apply_business_rules_core(
            candidate,
            nearby_context=nearby_context,
            previous_segment=previous_segment,
            applied=[],
            stats={"skipped_already_correct_count": 0, "duplicate_prevented_count": 0},
        )
        second_sanitized, _ = sanitize_business_duplicate_prefixes(second_pass)
        idempotent_ok = second_sanitized == candidate and not detect_duplicate_damage(candidate)
    else:
        idempotent_ok = not detect_duplicate_damage(candidate)

    if idempotent_ok:
        if _idempotency_pass_log_count < _MAX_IDEMPOTENCY_PASS_LOGS:
            _idempotency_pass_log_count += 1
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "BUSINESS_JP_CLEANUP_IDEMPOTENCY_CHECK_PASSED",
                    stable_text_preview=candidate[:120],
                )
            except Exception:
                pass
    else:
        stats["idempotency_check_failed_count"] = 1
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "BUSINESS_JP_CLEANUP_IDEMPOTENCY_CHECK_FAILED",
                first_pass=candidate[:120],
                second_pass=second_sanitized[:120],
            )
            jp_accuracy_log(
                "BUSINESS_JP_CLEANUP_REVERTED_NON_IDEMPOTENT",
                stable_before=original[:160],
                damaged_candidate=candidate[:160],
            )
        except Exception:
            pass
        candidate = original
        applied = [a for a in applied if not a.get("applied_to_ui")]

    high_applied = [a for a in applied if a.get("applied_to_ui")]
    changed = candidate != original
    elapsed_ms = round((_time.perf_counter() - seg_start) * 1000.0, 2)
    if elapsed_ms > 30:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "BUSINESS_CLEANUP_SLOW",
                duration_ms=elapsed_ms,
                text_len=len(original),
            )
        except Exception:
            pass
    if elapsed_ms <= 100 and _idempotency_pass_log_count % 20 == 0:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("BUSINESS_CLEANUP_PER_SEGMENT_MS", duration_ms=elapsed_ms)
        except Exception:
            pass
    return {
        "original": original,
        "candidate": candidate.strip(),
        "changes": applied,
        "stats": stats,
        "idempotent_ok": idempotent_ok or not changed,
        "high_confidence_count": len(high_applied),
        "duration_ms": elapsed_ms,
    }


def business_japanese_stable_cleanup(
    text: str,
    *,
    nearby_context: str = "",
    previous_segment: str = "",
) -> dict[str, Any]:
    """High-confidence business Japanese cleanup on stable layer only."""
    global _skip_optional_risk_scan
    original = (text or "").strip()
    context = f"{previous_segment}{nearby_context}{original}"
    risks: list[dict[str, Any]] = []

    result = normalize_business_cleanup_once(
        original,
        nearby_context=nearby_context,
        previous_segment=previous_segment,
    )
    candidate = result["candidate"]
    applied = result["changes"]
    stats = result.get("stats") or {}
    duration_ms = float(result.get("duration_ms") or 0.0)
    if duration_ms > 100:
        _skip_optional_risk_scan = True
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "BUSINESS_CLEANUP_OPTIONAL_SCAN_SKIPPED",
                duration_ms=duration_ms,
            )
        except Exception:
            pass

    if not _skip_optional_risk_scan:
        for pattern, rule_id, category in _BUSINESS_CANDIDATE_PATTERNS:
            if re.search(pattern, candidate):
                risks.append(
                    {
                        "rule_id": rule_id,
                        "category": category,
                        "confidence": "low",
                        "stable_text": candidate[:160],
                        "reason": "candidate_only_not_auto_applied",
                    }
                )

    for entry in applied:
        if entry.get("skipped"):
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                skip_reason = str(entry.get("reason", ""))
                event = (
                    "BUSINESS_JP_CLEANUP_SKIPPED_ALREADY_CORRECT"
                    if "already_correct" in skip_reason
                    else "BUSINESS_JP_CLEANUP_DUPLICATE_PREVENTED"
                )
                jp_accuracy_log(event, **{k: v for k, v in entry.items() if k != "skipped"})
            except Exception:
                pass

    high_applied = [a for a in applied if a.get("applied_to_ui")]
    changed = candidate != original
    return {
        "original": original,
        "candidate": candidate,
        "changes": applied,
        "risk_candidates": risks,
        "applied_to_ui": bool(high_applied) and changed and result.get("idempotent_ok", True),
        "high_confidence_count": len(high_applied),
        "risk_candidate_count": len(risks),
        "cleanup_stats": stats,
        "context_window": context[-160:],
    }
