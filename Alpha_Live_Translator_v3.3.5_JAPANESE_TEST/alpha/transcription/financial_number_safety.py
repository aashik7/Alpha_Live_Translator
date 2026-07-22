"""Full-span financial number correction safety (8.5.25.2.1)."""

from __future__ import annotations

import re
from typing import Any

from alpha.constants import (
    FINANCIAL_NUMBER_SEMANTIC_VALIDATION_ENABLED,
    FULL_SPAN_FINANCIAL_NUMBER_CORRECTION_ENABLED,
    MALFORMED_NUMERIC_OUTPUT_BLOCK_ENABLED,
)

_KANJI_DIGIT = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000, "万": 10_000, "億": 100_000_000}

_FIN_SPAN_RE = re.compile(
    r"[0-9０-９一二三四五六七八九十百千万億兆点．。,，％%]+(?:円|万円|億円|億|万|パーセント|％|%)?"
)

_MALFORMED_PATTERNS = [
    (re.compile(r"十\d+億"), "mixed_kanji_arabic_oku"),
    (re.compile(r"百\d+万"), "mixed_kanji_arabic_man"),
    (re.compile(r"\d一億"), "digit_kanji_oku"),
    (re.compile(r"\d+億[一二三四五六七八九十百千]"), "arabic_kanji_oku_mix"),
    (re.compile(r"十\d+億\d+万"), "ten11_oku_style"),
    (re.compile(r"\d+億二\d+万"), "arabic_kanji_man_mix"),
    (re.compile(r"(?<![第\d])\d+。\d+(?:[%％パーセント]|の増|の減|増益|減益|増収|減収)"), "malformed_decimal_percent"),
]

_DATE_SAFE = re.compile(r"\d{4}年\d{1,2}月")


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _normalize_digits(text: str) -> str:
    trans = str.maketrans("０１２３４５６７８９，．", "0123456789,.")
    return text.translate(trans)


def _parse_simple_japanese_number(token: str) -> float | None:
    """Best-effort parse for financial equivalence checks."""
    t = _normalize_digits(token).replace(",", "").replace("円", "").replace("パーセント", "").replace("%", "").replace("％", "")
    if not t:
        return None
    if re.fullmatch(r"[\d.]+", t):
        try:
            return float(t)
        except ValueError:
            return None
    total = 0.0
    current = 0
    num = 0
    for ch in t:
        if ch.isdigit():
            num = num * 10 + int(ch)
        elif ch == ".":
            break
        elif ch in _KANJI_DIGIT:
            val = _KANJI_DIGIT[ch]
            if val >= 10_000:
                current = (current + (num or 1)) * val
                num = 0
            elif val >= 10:
                if num == 0:
                    num = 1
                current += num * val
                num = 0
            else:
                num = num * 10 + val
        else:
            continue
    total = current + num
    if "億" in token:
        m = re.search(r"([\d一二三四五六七八九十百千万]+)億", token)
        oku = _parse_simple_japanese_number(m.group(1) + "万") if m else 0
        if oku is None:
            oku = 0
        rest = token.split("億", 1)[-1]
        rest_val = _parse_simple_japanese_number(rest) if rest else 0
        if oku is not None and rest_val is not None:
            return oku * 1e8 + rest_val
    if "万" in token:
        m = re.search(r"([\d一二三四五六七八九十百千]+)万", token)
        man = _parse_simple_japanese_number(m.group(1)) if m else 0
        if man is not None:
            return man * 1e4
    return total if total else None


def numbers_semantically_equivalent(before: str, after: str, *, tolerance: float = 1.0) -> bool:
    if not FINANCIAL_NUMBER_SEMANTIC_VALIDATION_ENABLED:
        return before.strip() != after.strip()
    b = _parse_simple_japanese_number(before)
    a = _parse_simple_japanese_number(after)
    if b is None or a is None:
        return _normalize_digits(before) == _normalize_digits(after)
    return abs(b - a) <= tolerance


def find_financial_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for m in _FIN_SPAN_RE.finditer(text):
        span = m.group(0)
        if len(span) < 2:
            continue
        if any(ch in span for ch in "0123456789０-９一二三四五六七八九十百千万億"):
            spans.append((m.start(), m.end(), span))
    spans.sort(key=lambda x: (x[1] - x[0]), reverse=True)
    return spans


def detect_malformed_numeric_output(text: str) -> list[dict[str, Any]]:
    if not MALFORMED_NUMERIC_OUTPUT_BLOCK_ENABLED:
        return []
    findings: list[dict[str, Any]] = []
    for pattern, reason in _MALFORMED_PATTERNS:
        for m in pattern.finditer(text):
            snippet = m.group(0)
            if _DATE_SAFE.search(text[max(0, m.start() - 8) : m.end() + 8]):
                continue
            findings.append({"pattern": reason, "match": snippet, "position": m.start()})
            _jp_log("MALFORMED_NUMERIC_OUTPUT_DETECTED", pattern=reason, match=snippet)
    return findings


def apply_safe_financial_number_correction(
    text: str,
    *,
    alias: str,
    expected: str,
    label: str = "",
    context_terms: list[str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Apply correction only to a complete financial span with semantic validation."""
    context_terms = context_terms or []
    if context_terms and not any(c in text for c in context_terms):
        return text, None

    if not FULL_SPAN_FINANCIAL_NUMBER_CORRECTION_ENABLED:
        if alias in text and alias != expected:
            new_text = text.replace(alias, expected)
            if new_text != text:
                return new_text, {"before": alias, "after": expected, "validation_status": "legacy_substring"}
        return text, None

    spans = find_financial_spans(text)
    if not spans:
        return text, None

    for start, end, span in spans:
        if alias == span:
            if numbers_semantically_equivalent(span, expected):
                _jp_log("FINANCIAL_NUMBER_FULL_SPAN_DETECTED", span=span)
                _jp_log("FINANCIAL_NUMBER_PARSE_COMPLETED", label=label)
                _jp_log("FINANCIAL_NUMBER_SEMANTIC_EQUIVALENCE_CONFIRMED", before=span, after=expected)
                new_text = text[:start] + expected + text[end:]
                _jp_log("FINANCIAL_NUMBER_CORRECTION_APPLIED_SAFE", label=label, before=span, after=expected)
                return new_text, {
                    "before": span,
                    "after": expected,
                    "alias": alias,
                    "label": label,
                    "validation_status": "semantic_equivalent_full_span",
                    "full_span": span,
                }
            _jp_log("FINANCIAL_NUMBER_SEMANTIC_EQUIVALENCE_FAILED", before=span, after=expected)
            return text, None

        if alias in span and alias != span:
            _jp_log("FINANCIAL_NUMBER_SUBSTRING_REPLACEMENT_BLOCKED", alias=alias, span=span)
            if numbers_semantically_equivalent(span, expected):
                _jp_log("FINANCIAL_NUMBER_SEMANTIC_EQUIVALENCE_CONFIRMED", before=span, after=expected)
                new_text = text[:start] + expected + text[end:]
                if not detect_malformed_numeric_output(new_text):
                    _jp_log("FINANCIAL_NUMBER_CORRECTION_APPLIED_SAFE", label=label, before=span, after=expected)
                    return new_text, {
                        "before": span,
                        "after": expected,
                        "alias": alias,
                        "label": label,
                        "validation_status": "parent_span_semantic_equivalent",
                        "full_span": span,
                    }
            _jp_log("DANGEROUS_FINANCIAL_CORRECTION_BLOCKED", alias=alias, span=span, expected=expected)
            return text, {
                "before": alias,
                "after": expected,
                "label": label,
                "validation_status": "blocked_substring",
                "blocked": True,
                "full_span": span,
            }

    if alias in text:
        _jp_log("FINANCIAL_NUMBER_SUBSTRING_REPLACEMENT_BLOCKED", alias=alias, reason="no_full_span_match")
        return text, {
            "before": alias,
            "after": expected,
            "label": label,
            "validation_status": "blocked_no_span",
            "blocked": True,
        }
    return text, None


def audit_financial_text(text: str) -> dict[str, Any]:
    malformed = detect_malformed_numeric_output(text)
    return {
        "malformed_numeric_output_count": len(malformed),
        "malformed_items": malformed,
        "dangerous_correction_count": len(malformed),
    }
