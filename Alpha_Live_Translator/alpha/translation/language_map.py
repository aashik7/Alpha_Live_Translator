"""Explicit Alpha language code → DeepL language mapping (no auto-detect)."""

from __future__ import annotations

from typing import Optional

# Alpha codes / UI names → DeepL source
_SOURCE = {
    "ja": "JA",
    "japanese": "JA",
    "en": "EN",
    "english": "EN",
    "en-us": "EN",
    "en-gb": "EN",
}

# Alpha source → DeepL target for opposite language
_TARGET_FOR_SOURCE = {
    "ja": "EN-US",
    "japanese": "EN-US",
    "en": "JA",
    "english": "JA",
    "en-us": "JA",
    "en-gb": "JA",
}


def normalize_alpha_language(language: str) -> str:
    raw = (language or "").strip()
    low = raw.lower().replace("_", "-")
    if low.startswith("ja"):
        return "ja"
    if low.startswith("en") or low == "english":
        return "en"
    if low == "japanese":
        return "ja"
    return low


def get_deepl_source_code(language: str) -> Optional[str]:
    """Return DeepL source code for Alpha/UI language, or None."""
    key = (language or "").strip()
    if not key:
        return None
    low = key.lower()
    if low in _SOURCE:
        return _SOURCE[low]
    # UI display names
    if key in ("Japanese", "English"):
        return "JA" if key == "Japanese" else "EN"
    norm = normalize_alpha_language(key)
    return _SOURCE.get(norm)


def get_deepl_target_code(language: str) -> Optional[str]:
    """Return DeepL target for a *target* language name/code."""
    norm = normalize_alpha_language(language)
    if norm == "ja":
        return "JA"
    if norm == "en":
        return "EN-US"
    # If caller passed a source language, map to opposite target.
    return _TARGET_FOR_SOURCE.get(norm) or _TARGET_FOR_SOURCE.get((language or "").lower())


def target_for_source(source_language: str) -> Optional[str]:
    """DeepL target given Alpha source language (ja→EN-US, en→JA)."""
    return _TARGET_FOR_SOURCE.get(normalize_alpha_language(source_language))


def is_same_language(source_language: str, target_language: str) -> bool:
    s = get_deepl_source_code(source_language)
    t = get_deepl_source_code(target_language)
    if s is None or t is None:
        return False
    return s == t
