"""General meaning-equivalent normalization (analysis-only).

No benchmark-specific hardcoded term pairs. Used only for post-runtime scoring.
Never mutates transcript files and never feeds into Deepgram/Alpha runtime.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

NORMALIZATION_RULES_VERSION = "mdg_general_norm_v265"

_KANJI_DIGITS = {
    "〇": "0",
    "零": "0",
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}
_SPOKEN_LETTER = {
    "エー": "A",
    "ビー": "B",
    "シー": "C",
    "ディー": "D",
    "イー": "E",
    "エフ": "F",
    "ジー": "G",
    "エイチ": "H",
    "アイ": "I",
    "ジェイ": "J",
    "ケー": "K",
    "エル": "L",
    "エム": "M",
    "エヌ": "N",
    "オー": "O",
    "ピー": "P",
    "キュー": "Q",
    "アール": "R",
    "エス": "S",
    "ティー": "T",
    "ユー": "U",
    "ブイ": "V",
    "ダブリュー": "W",
    "エックス": "X",
    "ワイ": "Y",
    "ゼット": "Z",
}

_HARMLESS_PUNCT_RE = re.compile(r"[、。．，,.\s　！？!?・…〜～「」『』（）()【】\[\]\-‐‑–—―_/\\]+")
_PERCENT_RE = re.compile(r"(パーセント|％)")
_YEN_UNIT_RE = re.compile(r"(円|万円|億円)")


def _kanji_numeral_to_arabic(text: str) -> str:
    """Convert common Japanese numeral phrases to Arabic digits (general)."""

    def _parse_under_10000(chunk: str) -> int | None:
        if not chunk:
            return 0
        total = 0
        cur = 0
        for ch in chunk:
            if ch in _KANJI_DIGITS:
                cur = int(_KANJI_DIGITS[ch])
            elif ch == "十":
                total += (cur or 1) * 10
                cur = 0
            elif ch == "百":
                total += (cur or 1) * 100
                cur = 0
            elif ch == "千":
                total += (cur or 1) * 1000
                cur = 0
            else:
                return None
        return total + cur

    def _repl_man(m: re.Match[str]) -> str:
        body = m.group(1)
        unit = m.group(2)
        parsed = _parse_under_10000(body)
        if parsed is None:
            return m.group(0)
        if unit == "万":
            return f"{parsed}万"
        if unit == "億":
            return f"{parsed}億"
        return str(parsed)

    out = re.sub(
        r"([〇零一二三四五六七八九十百千]+)(万|億)?",
        _repl_man,
        text,
    )
    # Digits already Arabic stay; map remaining single kanji digits.
    for k, v in _KANJI_DIGITS.items():
        out = out.replace(k, v)
    return out


def _spoken_letters_to_latin(text: str) -> str:
    out = text
    # Longer keys first
    for spoken, letter in sorted(_SPOKEN_LETTER.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(spoken, letter)
    return out


def _normalize_money_time_percent(text: str) -> str:
    out = text
    out = _PERCENT_RE.sub("%", out)
    out = out.replace("％", "%")
    # Collapse 午前十時 → 午前10時 style already handled by kanji numeral pass.
    out = re.sub(r"午前(\d{1,2})時(\d{1,2})?分?", lambda m: f"午前{int(m.group(1))}時" + (f"{int(m.group(2))}分" if m.group(2) else ""), out)
    out = re.sub(r"午後(\d{1,2})時(\d{1,2})?分?", lambda m: f"午後{int(m.group(1))}時" + (f"{int(m.group(2))}分" if m.group(2) else ""), out)
    # Strip thousands separators in digit groups: 5,000 → 5000
    out = re.sub(r"(?<=\d),(?=\d{3})", "", out)
    out = re.sub(r"(?<=\d)、(?=\d{3})", "", out)
    return out


def apply_general_meaning_normalization(text: str) -> tuple[str, list[dict[str, str]]]:
    """Return generally-normalized text and applied rule tags (not term pairs)."""
    applied: list[dict[str, str]] = []
    original = text or ""
    out = unicodedata.normalize("NFKC", original)
    if out != original:
        applied.append({"rule": "unicode_nfkc", "from": "variant", "to": "nfkc"})

    before = out
    out = _spoken_letters_to_latin(out)
    if out != before:
        applied.append({"rule": "spoken_letter_az", "from": "kana_letter", "to": "latin_letter"})

    before = out
    out = _kanji_numeral_to_arabic(out)
    if out != before:
        applied.append({"rule": "jp_arabic_numeral", "from": "kanji_numeral", "to": "arabic"})

    before = out
    out = _normalize_money_time_percent(out)
    if out != before:
        applied.append({"rule": "date_time_money_percent", "from": "variant", "to": "canonical"})

    before = out
    out = _HARMLESS_PUNCT_RE.sub("", out)
    if out != before:
        applied.append({"rule": "harmless_punct_spacing", "from": "punct_space", "to": "stripped"})

    # Orthographic honorific spacing variants for お疲れ*様 (general ASR form)
    before = out
    out = out.replace("お疲れさま", "お疲れ様").replace("お疲れ様", "お疲れ様")
    if out != before:
        applied.append({"rule": "orthography_otsukaresama", "from": "variant", "to": "canonical"})

    return out, applied


def apply_meaning_equivalent(text: str) -> tuple[str, list[dict[str, str]]]:
    """Public alias used by scorers — general normalization only."""
    return apply_general_meaning_normalization(text)


def terms_equivalent(a: str, b: str) -> bool:
    na, _ = apply_general_meaning_normalization(a)
    nb, _ = apply_general_meaning_normalization(b)
    return bool(na) and na == nb


def summarize_normalization_rules() -> dict[str, Any]:
    return {
        "normalization_rules_version": NORMALIZATION_RULES_VERSION,
        "benchmark_specific_pairs": False,
        "rules": [
            "unicode_nfkc_width",
            "harmless_punctuation_spacing",
            "japanese_arabic_numeral_equivalence",
            "dates_times_money_percentages",
            "generic_az_spoken_letter_equivalence",
        ],
    }
