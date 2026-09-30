"""Script-aware CJK text cleanup helpers (Japanese now, Chinese-ready later)."""

from __future__ import annotations

import re
from typing import Callable, Optional

from alpha.constants import (
    CJK_CLEANUP_MAX_CHARS,
    CJK_REPEAT_SCAN_MAX_UNIT,
    LOG_PREVIEW_MAX_CHARS,
)

LogFn = Optional[Callable[[str, dict], None]]

_CJK_MODE_CODES = frozenset({"ja", "ja-jp", "zh", "zh-cn", "zh-tw"})

_KANA_RE = re.compile(r"^[\u3040-\u30ffー]+$")


def is_kana_only(text: str) -> bool:
    segment = (text or "").strip()
    return bool(segment) and bool(_KANA_RE.fullmatch(segment))


def _protected_short_kana_repeat_unit(unit: str) -> bool:
    """Protect natural Japanese reduplication like そもそも / いろいろ."""
    return is_kana_only(unit) and 1 < len(unit) <= 4


def detect_kana_prefix_overlap_removal(original: str, cleaned: str) -> bool:
    """True when cleanup removed a short kana-only repeated prefix."""
    orig = (original or "").strip()
    clean = (cleaned or "").strip()
    if not orig or orig == clean:
        return False
    compact_orig = compact_cjk_for_compare(orig, "ja")
    compact_clean = compact_cjk_for_compare(clean, "ja")
    if len(compact_orig) <= len(compact_clean):
        return False
    removed = compact_orig[: len(compact_orig) - len(compact_clean)]
    if not removed:
        return False
    for size in range(1, min(5, len(removed) + 1)):
        unit = removed[:size]
        if _protected_short_kana_repeat_unit(unit):
            return True
    return False


_NATURAL_SHORT_REPEATS_JA = frozenset(
    {"はいはい", "うんうん", "そうそう", "まあまあ"}
)
_NATURAL_SHORT_REPEATS_ZH = frozenset({"谢谢谢谢"})

_CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
_JAPANESE_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
_CHINESE_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9]")
_SPEAKER_PREFIX_RE = re.compile(r"^\s*\[speaker\s+\d+\]\s*", re.I)
_COMPACT_PUNCT_RE = re.compile(r'[.,!?;:。、！？，．・"\'“”‘’`~()\[\]{}<>]')


def _preview(text: str, max_len: int | None = None) -> str:
    limit = max_len if max_len is not None else LOG_PREVIEW_MAX_CHARS
    return (text or "")[:limit]


def _cleanup_perf_guard(
    text: str,
    language_code: str,
    log_fn: LogFn,
    reason: str,
) -> bool:
    text_len = len(text or "")
    if text_len <= CJK_CLEANUP_MAX_CHARS:
        return False
    if log_fn:
        log_fn(
            "[CJK] cleanup perf guard triggered",
            {
                "text_len": text_len,
                "language_code": language_code,
                "reason": reason,
            },
        )
    return True


def is_cjk_mode(language_code: str) -> bool:
    code = str(language_code or "").lower()
    if code in _CJK_MODE_CODES:
        return True
    return code.startswith("zh")


def contains_cjk(text: str) -> bool:
    return bool(_CJK_CHAR_RE.search(text or ""))


def is_japanese_char(ch: str) -> bool:
    return bool(_JAPANESE_CHAR_RE.match(ch or ""))


def is_chinese_char(ch: str) -> bool:
    return bool(_CHINESE_CHAR_RE.match(ch or ""))


def is_cjk_char(ch: str) -> bool:
    return is_japanese_char(ch) or is_chinese_char(ch)


def _natural_short_repeats(language_code: str) -> frozenset[str]:
    code = str(language_code or "").lower()
    if code.startswith("zh"):
        return _NATURAL_SHORT_REPEATS_ZH
    return _NATURAL_SHORT_REPEATS_JA


def normalize_cjk_spacing_line(text: str) -> str:
    segment = (text or "").replace("\u3000", " ")
    segment = re.sub(r"[ \t]+", " ", segment).strip()
    chars = list(segment)
    compact: list[str] = []
    for idx, ch in enumerate(chars):
        if ch == " " and idx > 0 and idx < len(chars) - 1:
            prev_ch = chars[idx - 1]
            next_ch = chars[idx + 1]
            if is_cjk_char(prev_ch) and is_cjk_char(next_ch):
                continue
        compact.append(ch)
    segment = "".join(compact)
    segment = re.sub(r"\s+([。、！？，．])", r"\1", segment)
    segment = re.sub(
        r"([。、！？，．])\s+(?=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff])",
        r"\1",
        segment,
    )
    segment = re.sub(r"([（「『［【｛〈《])\s+", r"\1", segment)
    return segment.strip()


def normalize_cjk_spacing(text: str) -> str:
    raw = (text or "").replace("\u3000", " ")
    if "\n" not in raw:
        return normalize_cjk_spacing_line(raw)
    return "\n".join(normalize_cjk_spacing_line(line) for line in raw.splitlines()).strip()


def compact_cjk_for_compare(text: str, language_code: str = "ja") -> str:
    _ = language_code
    segment = _SPEAKER_PREFIX_RE.sub("", text or "")
    segment = normalize_cjk_spacing(segment)
    segment = re.sub(r"[\s\u3000]", "", segment)
    segment = _COMPACT_PUNCT_RE.sub("", segment)
    return segment.lower()


def _segment_from_compact_target(
    segment: str, target_compact: str, language_code: str = "ja"
) -> str:
    segment = (segment or "").strip()
    target_compact = (target_compact or "").strip()
    if not segment or not target_compact:
        return segment
    for end in range(1, len(segment) + 1):
        if compact_cjk_for_compare(segment[:end], language_code) == target_compact:
            return segment[:end].strip()
    return segment


def _compact_tail_segment(
    segment: str, tail_compact: str, language_code: str = "ja"
) -> str:
    tail_compact = (tail_compact or "").strip()
    if not tail_compact:
        return ""
    segment = (segment or "").strip()
    for start in range(len(segment), -1, -1):
        tail = segment[start:]
        if compact_cjk_for_compare(tail, language_code) == tail_compact:
            return tail
    return ""


def _reconstruct_segment_from_compact_target(
    segment: str, target_compact: str, language_code: str = "ja"
) -> str:
    segment = (segment or "").strip()
    target_compact = (target_compact or "").strip()
    if not segment or not target_compact:
        return segment
    for end in range(1, len(segment) + 1):
        if compact_cjk_for_compare(segment[:end], language_code) == target_compact:
            return segment[:end].strip()
    for split in range(1, len(target_compact)):
        prefix_compact = target_compact[:split]
        suffix_compact = target_compact[split:]
        prefix_text = _segment_from_compact_target(
            segment, prefix_compact, language_code
        )
        if compact_cjk_for_compare(prefix_text, language_code) != prefix_compact:
            continue
        suffix_text = _compact_tail_segment(segment, suffix_compact, language_code)
        if not suffix_text:
            continue
        candidate = (prefix_text + suffix_text).strip()
        if compact_cjk_for_compare(candidate, language_code) == target_compact:
            return candidate
    return segment


def _fix_adjacent_repeat_with_suffix(
    segment: str,
    compact: str,
    *,
    min_unit: int,
    max_unit: int,
    language_code: str,
    protect_short_kana_repeat: bool = False,
) -> tuple[str, str, str, str] | None:
    natural = _natural_short_repeats(language_code)
    for start in range(len(compact)):
        rest = compact[start:]
        if len(rest) < min_unit * 2:
            continue
        upper = min(max_unit, len(rest) // 2)
        for unit_len in range(upper, min_unit - 1, -1):
            prefix = rest[:unit_len]
            if prefix in natural:
                continue
            if protect_short_kana_repeat and _protected_short_kana_repeat_unit(prefix):
                continue
            if rest[unit_len : unit_len * 2] != prefix:
                continue
            suffix = rest[unit_len * 2 :]
            if not suffix:
                continue
            target_compact = compact[:start] + prefix + suffix
            cleaned = _reconstruct_segment_from_compact_target(
                segment, target_compact, language_code
            )
            if cleaned != segment:
                return cleaned, prefix, suffix, "adjacent_repeat_with_suffix"
    return None


def remove_cjk_local_repeats_once(
    text: str,
    language_code: str = "ja",
    log_fn: LogFn = None,
) -> str:
    segment = (text or "").strip()
    if _cleanup_perf_guard(segment, language_code, log_fn, "local_repeat_scan"):
        return segment
    compact = compact_cjk_for_compare(segment, language_code)
    result = _fix_adjacent_repeat_with_suffix(
        segment,
        compact,
        min_unit=4,
        max_unit=CJK_REPEAT_SCAN_MAX_UNIT,
        language_code=language_code,
    )
    if result:
        cleaned, unit, suffix, reason = result
        if log_fn:
            log_fn(
                "[CJK] local repeat fixed",
                {
                    "original_preview": _preview(segment),
                    "cleaned_preview": _preview(cleaned),
                    "repeated_unit_preview": _preview(unit, 80),
                    "suffix_preview": _preview(suffix, 80),
                    "language_code": language_code,
                    "reason": reason,
                },
            )
        return cleaned
    return segment


def remove_cjk_local_repeats(
    text: str,
    language_code: str = "ja",
    log_fn: LogFn = None,
) -> str:
    segment = (text or "").strip()
    for _ in range(4):
        cleaned = remove_cjk_local_repeats_once(segment, language_code, log_fn)
        if cleaned == segment:
            break
        segment = cleaned
    return segment


def merge_boundary_prefix_overlap(
    previous: str,
    current: str,
    language_code: str = "ja",
    log_fn: LogFn = None,
) -> str:
    """Aggressive prefix-overlap cleanup only when merging two transcript fragments."""
    merged = ((previous or "").strip() + (current or "").strip()).strip()
    if not merged:
        return merged
    return remove_cjk_prefix_overlap_once(
        merged,
        language_code,
        log_fn,
        protect_short_kana_repeat=False,
        min_unit=4,
    )


def remove_cjk_prefix_overlap_once(
    text: str,
    language_code: str = "ja",
    log_fn: LogFn = None,
    *,
    protect_short_kana_repeat: bool = True,
    min_unit: int = 2,
) -> str:
    segment = (text or "").strip()
    if _cleanup_perf_guard(segment, language_code, log_fn, "prefix_overlap_scan"):
        return segment
    compact = compact_cjk_for_compare(segment, language_code)
    if len(compact) < 4:
        return segment
    result = _fix_adjacent_repeat_with_suffix(
        segment,
        compact,
        min_unit=min_unit,
        max_unit=min(24, CJK_REPEAT_SCAN_MAX_UNIT),
        language_code=language_code,
        protect_short_kana_repeat=protect_short_kana_repeat,
    )
    if result:
        cleaned, prefix, _suffix, reason = result
        if len(compact_cjk_for_compare(cleaned, language_code)) >= 4:
            if log_fn:
                log_fn(
                    "[CJK] prefix overlap fixed",
                    {
                        "original_preview": _preview(segment),
                        "cleaned_preview": _preview(cleaned),
                        "prefix_preview": _preview(prefix, 80),
                        "language_code": language_code,
                        "reason": reason,
                    },
                )
            return cleaned
    return segment


def remove_cjk_prefix_overlap(
    text: str,
    language_code: str = "ja",
    log_fn: LogFn = None,
) -> str:
    segment = (text or "").strip()
    for _ in range(4):
        cleaned = remove_cjk_prefix_overlap_once(segment, language_code, log_fn)
        if cleaned == segment:
            break
        segment = cleaned
    return segment


def fix_cjk_boundary_punctuation(text: str, language_code: str = "ja") -> str:
    segment = (text or "").strip()
    if not segment:
        return segment
    code = str(language_code or "").lower()
    if not code.startswith("ja"):
        return segment
    for ending in ("しています", "しました", "です", "ます"):
        token = f"{ending}かどうぞ"
        if token in segment:
            segment = segment.replace(token, f"{ending}。どうぞ")
    return segment


def fix_cjk_boundary_punctuation_with_log(
    text: str,
    language_code: str = "ja",
    log_fn: LogFn = None,
) -> str:
    original = (text or "").strip()
    cleaned = fix_cjk_boundary_punctuation(original, language_code)
    if cleaned != original and log_fn:
        log_fn(
            "[CJK] boundary punctuation fixed",
            {
                "original_preview": _preview(original),
                "cleaned_preview": _preview(cleaned),
                "language_code": language_code,
                "reason": "ka_douzo_boundary",
            },
        )
    return cleaned


_PROTECTED_DUPLICATE_UNITS = frozenset(
    {
        "そもそも",
        "いろいろ",
        "たまたま",
        "ますます",
        "それぞれ",
        "サラサラ",
        "なんとかかんとか",
    }
)


def _should_protect_duplicate_unit(unit: str) -> bool:
    unit = (unit or "").strip()
    if not unit:
        return True
    if unit in _PROTECTED_DUPLICATE_UNITS:
        return True
    if unit in _NATURAL_SHORT_REPEATS_JA:
        return True
    doubled = unit + unit
    if doubled in _PROTECTED_DUPLICATE_UNITS:
        return True
    # Item 49: the natural repeats are stored doubled (はいはい), so the unit
    # (はい) has to be checked doubled too -- it never matched before.
    if doubled in _NATURAL_SHORT_REPEATS_JA:
        return True
    return False


# Item 49: a phrase the speaker said twice with a pause written between the
# copies, starting a phrase: 「サンプル、サンプル」「Java、Javaの」.
_REPEAT_SEPARATORS = r"\s、。，,．.！？!?"
_SEPARATED_REPEAT_RE = re.compile(
    rf"(?<![^{_REPEAT_SEPARATORS}])(?P<unit>[^{_REPEAT_SEPARATORS}]{{2,}}?)"
    rf"[{_REPEAT_SEPARATORS}]+(?P=unit)"
)
# A repeat that holds a number is a number ("スリーセブンスリーセブン" is 3737,
# 「二十、二十一」 is 20 and 21).
_NUMBER_WORD_RE = re.compile(
    r"[0-9０-９〇一二三四五六七八九十百千万億]"
    r"|ゼロ|ワン|ツー|スリー|フォー|ファイブ|シックス|セブン|エイト|ナイン"
)
_SENTENCE_ENDS = "。．.！？!?"


def _repeat_is_speech(unit: str, separator: str, goes_on: bool) -> bool:
    """A repeat both collapse rules leave as said. Review of item 49, 2026-09-30.

    * a number: 「二十、二十一、二十二」 lost the 20, 「10、100、1000」 the 10 and 100
    * a sentence end between the copies with the second going on: that is a new
      sentence -- usually the other person's echo, 「声の高さです。声の高さですか。」
      -- and collapsing it deleted the first sentence
    """
    if _NUMBER_WORD_RE.search(unit):
        return True
    return goes_on and any(ch in _SENTENCE_ENDS for ch in separator)


def collapse_exact_duplicate_phrase(
    text: str,
    language_code: str = "ja",
) -> tuple[str, bool]:
    """Collapse a repeat the speaker separated: 「サンプル、サンプル」 -> 「サンプル」.

    Item 49 (2026-09-29). This collapsed ANY adjacent repeat of 1-6 characters,
    and in Japanese those are words far more often than glitches: it turned
    ここ into こ, 課題ナンバー二二七 into 二七, スリーセブンセブン into
    スリーセブン, サーバーバージョン into サーバージョン and もったいないな into
    もったいな, and rebuilt the text without the 。 between sentences -- 20+
    lines of the retained meetings, every one then translated wrong. Now only a
    repeat with punctuation or a space between the two copies, at the start of
    a phrase, is collapsed, and only the second copy and its separator go, so
    nothing that follows is touched. An adjacent repeat is left as said, and so
    are a number and a sentence the next one echoes (`_repeat_is_speech`).
    """
    _ = language_code
    segment = (text or "").strip()
    changed = False

    def is_speech(m: re.Match) -> bool:
        unit = m.group("unit")
        after = segment[m.end() : m.end() + 1]
        return _should_protect_duplicate_unit(unit) or _repeat_is_speech(
            unit,
            segment[m.end("unit") : m.end() - len(unit)],
            goes_on=bool(after) and not re.match(rf"[{_REPEAT_SEPARATORS}]", after),
        )

    for _ in range(12):
        match = next(
            (m for m in _SEPARATED_REPEAT_RE.finditer(segment) if not is_speech(m)),
            None,
        )
        if match is None:
            break
        segment = segment[: match.start()] + match.group("unit") + segment[match.end() :]
        changed = True
    return segment, changed


_SPACED_COMPACT_MIN_COMPACT = 6
_SPACED_COMPACT_RATIO_MIN = 0.65
_SPACED_COMPACT_RATIO_MAX = 0.80


def _compact_common_prefix_len(left: str, right: str) -> int:
    count = 0
    for left_ch, right_ch in zip(left, right):
        if left_ch != right_ch:
            break
        count += 1
    return count


def _text_offset_for_compact_index(
    segment: str, compact_index: int, language_code: str = "ja"
) -> int:
    """Map compact char index to source text offset."""
    if compact_index <= 0:
        return 0
    for end in range(1, len(segment) + 1):
        if len(compact_cjk_for_compare(segment[:end], language_code)) > compact_index:
            return end
    return len(segment)


def collapse_spaced_compact_duplicate(
    text: str,
    language_code: str = "ja",
) -> tuple[str, bool]:
    """Collapse spaced CJK prefix when a compact continuation largely repeats it."""
    if not is_cjk_mode(language_code):
        return (text or "").strip(), False
    segment = (text or "").strip()
    if not segment or " " not in segment:
        return segment, False
    if _cleanup_perf_guard(segment, language_code, None, "spaced_compact_scan"):
        return segment, False

    best_candidate: tuple[int, str, int] | None = None
    for pos, ch in enumerate(segment):
        if ch != " ":
            continue
        left = segment[:pos].strip()
        right = segment[pos:].strip()
        if not left or not right:
            continue
        compact_right = compact_cjk_for_compare(right, language_code)
        if len(compact_right) < _SPACED_COMPACT_MIN_COMPACT:
            continue
        for text_pos in range(len(left)):
            tail_compact = compact_cjk_for_compare(left[text_pos:], language_code)
            if len(tail_compact) < _SPACED_COMPACT_MIN_COMPACT:
                continue
            common = _compact_common_prefix_len(tail_compact, compact_right)
            if common < int(len(tail_compact) * _SPACED_COMPACT_RATIO_MIN):
                continue
            if len(compact_right) <= len(tail_compact):
                continue
            tail_text = left[text_pos:]
            if text_pos > 0 and tail_text.count(" ") < 1:
                continue
            prefix = left[:text_pos].rstrip()
            candidate = f"{prefix}{right}".strip() if prefix else right
            if candidate == segment:
                continue
            prefix_len = len(prefix)
            if best_candidate is None or prefix_len > best_candidate[2]:
                best_candidate = (len(candidate), candidate, prefix_len)

    if best_candidate is None:
        return segment, False
    return best_candidate[1], True


_SPACED_LATIN_ACRONYM_RE = re.compile(
    r"(?<![A-Za-z])((?:[A-Za-z]+\s+)+[A-Za-z]+)(?![A-Za-z])"
)
_ACRONYM_BEFORE_NO_RE = re.compile(r"([A-Z]{2,6})\s+の")


def normalize_latin_acronym_spacing(text: str) -> tuple[str, bool]:
    """Join spaced Latin acronyms (e.g. e sl → ESL) inside Japanese transcript."""
    segment = (text or "").strip()
    if not segment:
        return segment, False
    changed = False

    def _join_acronym(match: re.Match[str]) -> str:
        nonlocal changed
        chunk = match.group(1)
        letters = re.findall(r"[A-Za-z]", chunk)
        if 2 <= len(letters) <= 6:
            changed = True
            return "".join(letters).upper()
        return chunk

    updated = _SPACED_LATIN_ACRONYM_RE.sub(_join_acronym, segment)
    normalized = _ACRONYM_BEFORE_NO_RE.sub(r"\1の", updated)
    if normalized != updated:
        changed = True
    return normalized, changed


# Item 49: a restart the speaker (or the provider) made, the second copy going
# on: 「ありがとうございありがとうございます」「inTokyo、inTokyoの」.
_PREFIX_EXTENSION_RE = re.compile(
    rf"(?P<unit>[^{_REPEAT_SEPARATORS}]{{4,30}}?)"
    rf"[{_REPEAT_SEPARATORS}]*(?=(?P=unit)[^{_REPEAT_SEPARATORS}])"
)


def collapse_prefix_extension_duplicate(
    text: str,
    language_code: str = "ja",
) -> tuple[str, bool]:
    """Collapse A + B where B starts with A (prefix-extension STT duplicate).

    Item 49 (2026-09-29): works on the text itself, dropping the first copy
    and the separator after it. It used to rebuild the line from a
    punctuation-free copy, which dropped the 。 before the repeat
    (「がんばってください。ありがとうございありがとうございます。」 ->
    「がんばってくださいありがとうございます。」), and it cut a natural
    「もちろんもちろん」 that nothing followed. The second copy must now go on
    (an extension, not an exact repeat), and `_repeat_is_speech` keeps numbers
    and a sentence the next one echoes.
    """
    _ = language_code
    segment = (text or "").strip()
    changed = False
    for _ in range(10):
        match = next(
            (
                m
                for m in _PREFIX_EXTENSION_RE.finditer(segment)
                if not _repeat_is_speech(
                    m.group("unit"), segment[m.end("unit") : m.end()], goes_on=True
                )
            ),
            None,
        )
        if match is None:
            break
        segment = segment[: match.start()] + segment[match.end() :]
        changed = True
    return segment, changed


def cleanup_japanese_per_fragment(
    text: str,
    language_code: str = "ja",
) -> tuple[str, str, dict[str, bool]]:
    """Lightweight per-fragment cleanup before sentence assembler."""
    segment = (text or "").strip()
    flags: dict[str, bool] = {
        "spaced_compact_duplicate_collapse": False,
        "exact_duplicate_collapse": False,
        "prefix_extension_duplicate_collapse": False,
        "latin_acronym_spacing_normalized": False,
    }
    if not segment:
        return segment, "", flags
    reason_parts: list[str] = []

    normalized = normalize_cjk_spacing(segment)
    if normalized != segment:
        segment = normalized
        reason_parts.append("spacing")

    acronym, acr_changed = normalize_latin_acronym_spacing(segment)
    if acr_changed:
        segment = acronym
        flags["latin_acronym_spacing_normalized"] = True
        reason_parts.append("latin_acronym_spacing_normalized")

    collapsed, dup = collapse_exact_duplicate_phrase(segment, language_code)
    if dup:
        segment = collapsed
        flags["exact_duplicate_collapse"] = True
        reason_parts.append("exact_duplicate_collapse")

    prefix_ext, pfx_changed = collapse_prefix_extension_duplicate(
        segment, language_code
    )
    if pfx_changed:
        segment = prefix_ext
        flags["prefix_extension_duplicate_collapse"] = True
        reason_parts.append("prefix_extension_duplicate_collapse")

    return segment, "+".join(reason_parts) if reason_parts else "", flags


def cleanup_japanese_transcript_precision(
    text: str,
    language_code: str = "ja",
) -> tuple[str, str, dict[str, bool]]:
    """Japanese transcript precision cleanup with per-step flags for logging."""
    segment = (text or "").strip()
    flags: dict[str, bool] = {
        "spaced_compact_duplicate_collapse": False,
        "exact_duplicate_collapse": False,
        "prefix_extension_duplicate_collapse": False,
        "latin_acronym_spacing_normalized": False,
    }
    if not segment:
        return segment, "", flags
    reason_parts: list[str] = []

    normalized = normalize_cjk_spacing(segment)
    if normalized != segment:
        segment = normalized
        reason_parts.append("spacing")

    acronym, acr_changed = normalize_latin_acronym_spacing(segment)
    if acr_changed:
        segment = acronym
        flags["latin_acronym_spacing_normalized"] = True
        reason_parts.append("latin_acronym_spacing_normalized")

    spaced, sp_changed = collapse_spaced_compact_duplicate(segment, language_code)
    if sp_changed:
        segment = spaced
        flags["spaced_compact_duplicate_collapse"] = True
        reason_parts.append("spaced_compact_duplicate_collapse")

    collapsed, dup = collapse_exact_duplicate_phrase(segment, language_code)
    if dup:
        segment = collapsed
        flags["exact_duplicate_collapse"] = True
        reason_parts.append("exact_duplicate_collapse")

    prefix_ext, pfx_changed = collapse_prefix_extension_duplicate(
        segment, language_code
    )
    if pfx_changed:
        segment = prefix_ext
        flags["prefix_extension_duplicate_collapse"] = True
        reason_parts.append("prefix_extension_duplicate_collapse")

    bounded = fix_cjk_boundary_punctuation(segment, language_code)
    if bounded != segment:
        segment = bounded
        reason_parts.append("boundary_punctuation")

    return segment, "+".join(reason_parts) if reason_parts else "", flags


def cleanup_japanese_post_merge(
    text: str,
    language_code: str = "ja",
) -> tuple[str, str]:
    """Safe post-merge cleanup: spacing, precision dedup, boundary punct only."""
    cleaned, reason, _flags = cleanup_japanese_transcript_precision(text, language_code)
    return cleaned, reason


_STT_ERROR_PATTERNS = (
    re.compile(r"ドバイス"),
    re.compile(r"なんなんでんか"),
    re.compile(r"慈悲でしょうか"),
    re.compile(r"そワイス"),
    re.compile(r"そジヒョ"),
    re.compile(r"おもそも"),
    re.compile(r"ブラシ"),
    re.compile(r"言うま"),
    re.compile(r"言うまで"),
    re.compile(r"思いまし"),
    re.compile(r"自分の中でっる"),
    re.compile(r"ばあなたが"),
    re.compile(r"このね私が"),
    re.compile(r"なんだけどブラシ"),
)


def detect_raw_stt_error_suspected(text: str) -> bool:
    segment = (text or "").strip()
    if not segment:
        return False
    return any(pattern.search(segment) for pattern in _STT_ERROR_PATTERNS)


def cleanup_cjk_text(
    text: str,
    language_code: str = "ja",
    *,
    enable_local: bool = True,
    enable_prefix: bool = True,
    enable_boundary: bool = True,
    log_fn: LogFn = None,
) -> str:
    if not is_cjk_mode(language_code):
        return (text or "").strip()
    segment = (text or "").strip()
    if _cleanup_perf_guard(segment, language_code, log_fn, "cleanup_cjk_text"):
        return normalize_cjk_spacing(segment)
    segment = normalize_cjk_spacing(segment)
    if enable_local:
        segment = remove_cjk_local_repeats(segment, language_code, log_fn)
    if enable_prefix:
        segment = remove_cjk_prefix_overlap(segment, language_code, log_fn)
    if enable_boundary:
        segment = fix_cjk_boundary_punctuation_with_log(
            segment, language_code, log_fn
        )
    return segment
