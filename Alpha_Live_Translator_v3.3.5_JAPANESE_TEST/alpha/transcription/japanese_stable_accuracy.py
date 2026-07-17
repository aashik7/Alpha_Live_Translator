"""Japanese stable-layer accuracy helpers (V3.3.5.5.8.5.21)."""

from __future__ import annotations

import time
from typing import Any, Optional

from alpha.utils.cjk_text import compact_cjk_for_compare

PUNCTUATION_START_PREFIXES: tuple[str, ...] = (
    "、",
    "。",
    "です。",
    "ます。",
    "ました。",
    "ですね。",
    "ですよね。",
    "ということです。",
    "という感じです。",
)

BUSINESS_PHRASES_PROTECTED: frozenset[str] = frozenset(
    {
        "はい。",
        "いいえ。",
        "承知しました。",
        "承知いたしました。",
        "ありがとうございます。",
        "よろしくお願いします。",
        "よろしくお願いいたします。",
        "失礼します。",
        "失礼いたします。",
        "お疲れ様です。",
        "そうですね。",
        "いつもお世話になっております。",
        "恐れ入ります。",
        "少々お待ちください。",
        "確認いたします。",
    }
)

SHORT_FRAGMENT_STANDALONE_EXCEPTIONS: frozenset[str] = frozenset(BUSINESS_PHRASES_PROTECTED)

INCOMPLETE_TAIL_HOLD_SUFFIXES: tuple[str, ...] = (
    "なんですが",
    "ですけど",
    "なので",
    "について",
    "として",
    "っていう",
    "という",
    "では",
    "まで",
    "から",
    "を",
    "で",
    "に",
    "が",
    "の",
)

STABLE_MERGE_MAX_PREVIOUS_CHARS = 180
STABLE_MERGE_WINDOW_MS = 8000.0
SHORT_FRAGMENT_MAX_COMPACT = 10
INCOMPLETE_TAIL_HOLD_MS_NORMAL = 2000
INCOMPLETE_TAIL_HOLD_MS_MAX = 3500


def count_japanese_chars(text: str) -> int:
    return len(compact_cjk_for_compare(text or "", "ja"))


def normalize_phrase(text: str) -> str:
    return (text or "").strip()


def is_protected_business_phrase(text: str) -> bool:
    segment = normalize_phrase(text)
    if not segment:
        return False
    compact = compact_cjk_for_compare(segment, "ja")
    for phrase in BUSINESS_PHRASES_PROTECTED:
        if compact == compact_cjk_for_compare(phrase, "ja"):
            return True
        if segment == phrase or segment.rstrip("。") + "。" == phrase:
            return True
    return False


def is_punctuation_start_fragment(text: str) -> bool:
    segment = normalize_phrase(text)
    if not segment:
        return False
    for prefix in PUNCTUATION_START_PREFIXES:
        if segment.startswith(prefix):
            return True
    return False


def is_clear_sentence(text: str) -> bool:
    segment = normalize_phrase(text)
    if not segment:
        return False
    if segment.endswith(("。", "！", "？", "!", "?")):
        return True
    return is_protected_business_phrase(segment)


def is_short_fragment_candidate(text: str) -> bool:
    segment = normalize_phrase(text)
    if not segment:
        return False
    return count_japanese_chars(segment) < SHORT_FRAGMENT_MAX_COMPACT


def should_hold_short_fragment(text: str, *, is_stop_flush: bool) -> bool:
    if is_stop_flush:
        return False
    segment = normalize_phrase(text)
    if not segment:
        return False
    if is_protected_business_phrase(segment):
        return False
    if is_clear_sentence(segment):
        return False
    return is_short_fragment_candidate(segment)


def has_incomplete_tail_for_hold(text: str) -> tuple[bool, str]:
    segment = normalize_phrase(text)
    if not segment:
        return False, ""
    for suffix in INCOMPLETE_TAIL_HOLD_SUFFIXES:
        if segment.endswith(suffix):
            return True, suffix
    return False, ""


def should_hold_incomplete_tail(text: str, *, is_stop_flush: bool) -> bool:
    if is_stop_flush:
        return False
    incomplete, _ = has_incomplete_tail_for_hold(text)
    return incomplete and not is_protected_business_phrase(text)


def can_merge_punctuation_with_previous(
    fragment: str,
    previous: Optional[dict[str, Any]],
    *,
    current_speaker: int,
    stop_boundary_active: bool,
    now_mono: Optional[float] = None,
) -> tuple[bool, str]:
    if stop_boundary_active:
        return False, "stop_boundary"
    if not is_punctuation_start_fragment(fragment):
        return False, "not_punctuation_start"
    if not previous:
        return False, "no_previous_stable"
    prev_text = str(previous.get("text") or "").strip()
    if not prev_text:
        return False, "empty_previous"
    if count_japanese_chars(prev_text) > STABLE_MERGE_MAX_PREVIOUS_CHARS:
        return False, "previous_too_long"
    prev_speaker = int(previous.get("speaker") or 0)
    if prev_speaker and current_speaker and prev_speaker != current_speaker:
        return False, "speaker_mismatch"
    prev_mono = float(previous.get("mono") or 0.0)
    now = now_mono if now_mono is not None else time.monotonic()
    age_ms = (now - prev_mono) * 1000.0 if prev_mono > 0 else 0.0
    if age_ms > STABLE_MERGE_WINDOW_MS:
        return False, "outside_merge_window"
    return True, "safe_merge"


def merge_punctuation_fragment(previous_text: str, fragment_text: str) -> str:
    prev = (previous_text or "").strip()
    frag = (fragment_text or "").strip()
    if not prev:
        return frag
    if not frag:
        return prev
    if frag.startswith("、") or frag.startswith("。"):
        return f"{prev}{frag}"
    return f"{prev}{frag}"


def merge_short_fragments(previous_text: str, fragment_text: str) -> str:
    return merge_punctuation_fragment(previous_text, fragment_text)


_LEADING_PUNCTUATION_STRIP_MAP: tuple[tuple[str, str], ...] = (
    ("、それでは", "それでは"),
    ("、漢字は", "漢字は"),
    ("、もうこれは", "もうこれは"),
)


def strip_leading_punctuation_mark(text: str) -> tuple[str, bool, str]:
    """Remove leading punctuation when merge with previous is not safe."""
    segment = normalize_phrase(text)
    if not segment:
        return segment, False, ""
    for src, dst in _LEADING_PUNCTUATION_STRIP_MAP:
        if segment.startswith(src):
            return segment.replace(src, dst, 1), True, f"mapped_{dst[:4]}"
    if segment.startswith("、") or segment.startswith("。"):
        return segment[1:].lstrip(), True, "leading_punctuation_removed"
    return segment, False, ""


def apply_punctuation_start_post_correction(
    text: str,
    *,
    previous_stable: Optional[dict[str, Any]],
    current_speaker: int,
    stop_boundary_active: bool,
    now_mono: Optional[float] = None,
) -> dict[str, Any]:
    """Merge or strip punctuation-start fragments after business correction."""
    segment = normalize_phrase(text)
    if not segment:
        return {"text": segment, "action": "unchanged", "reason": "empty", "merged_previous": False}

    if not is_punctuation_start_fragment(segment):
        return {
            "text": segment,
            "action": "unchanged",
            "reason": "not_punctuation_start",
            "merged_previous": False,
        }

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "PUNCTUATION_START_POST_CORRECTION_DETECTED",
            fragment_text=segment,
            speaker=current_speaker,
            raw_mutated=False,
        )
    except Exception:
        pass

    can_merge, merge_reason = can_merge_punctuation_with_previous(
        segment,
        previous_stable,
        current_speaker=current_speaker,
        stop_boundary_active=stop_boundary_active,
        now_mono=now_mono,
    )
    if can_merge and previous_stable:
        previous_text = str(previous_stable.get("text") or "")
        merged = merge_punctuation_fragment(previous_text, segment)
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "PUNCTUATION_START_MERGED_AFTER_CORRECTION",
                fragment_text=segment,
                previous_text_before=previous_text,
                previous_text_after=merged,
                reason=merge_reason,
                raw_mutated=False,
            )
        except Exception:
            pass
        return {
            "text": merged,
            "action": "merged_previous",
            "reason": merge_reason,
            "merged_previous": True,
            "fragment_text": segment,
            "previous_text_before": previous_text,
        }

    stripped, changed, strip_reason = strip_leading_punctuation_mark(segment)
    if changed:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "PUNCTUATION_START_LEADING_MARK_REMOVED",
                fragment_text=segment,
                output_text=stripped,
                reason=strip_reason,
                raw_mutated=False,
            )
        except Exception:
            pass
        return {
            "text": stripped,
            "action": "leading_removed",
            "reason": strip_reason,
            "merged_previous": False,
            "fragment_text": segment,
        }

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "PUNCTUATION_START_MERGE_SKIPPED_WITH_REASON",
            fragment_text=segment,
            reason=merge_reason or "unresolved",
            raw_mutated=False,
        )
    except Exception:
        pass
    return {
        "text": segment,
        "action": "merge_skipped",
        "reason": merge_reason or "unresolved",
        "merged_previous": False,
        "fragment_text": segment,
    }
