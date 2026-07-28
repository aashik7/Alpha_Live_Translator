"""Authoritative UI dropdown → Deepgram language routing.

Production resolver used by the live UI and by verify_language_routing.py.
Do not duplicate this mapping in tests.
"""

from __future__ import annotations

from typing import Any

from alpha.config import LANGUAGE_MAP

# Explicit bilingual contract (must match LANGUAGE_MAP entries).
AUTHORITATIVE_UI_TO_DEEPGRAM: dict[str, str] = {
    "English": "en",
    "Japanese": "ja",
}


class UnknownLanguageSelectionError(ValueError):
    """Raised when a dropdown value cannot be mapped to a Deepgram language code."""


def normalize_ui_language_label(ui_label: str | None) -> str:
    """Strip whitespace; plain English/Japanese labels are already flag-free."""
    text = str(ui_label or "").strip()
    # Accept flagged labels that embed the plain name.
    for plain in ("English", "Japanese", "Chinese (Mandarin)", "Russian"):
        if text == plain or plain in text:
            return plain
    return text


def resolve_ui_language_to_deepgram_code(
    ui_label: str | None,
    *,
    force_deepgram_language: str | None = None,
    allow_force_override: bool = False,
) -> str:
    """Map a UI source-language label to a Deepgram language code.

    Rules:
    - Known UI labels resolve via LANGUAGE_MAP (English→en, Japanese→ja, …).
    - A known selection never silently falls back to another language.
    - Unknown selections raise UnknownLanguageSelectionError (no silent ja/en).
    - force_deepgram_language is ignored for known selections unless
      allow_force_override=True (diagnostic-only; live UI must leave it False).
    """
    selected = normalize_ui_language_label(ui_label)
    mapped = LANGUAGE_MAP.get(selected)
    if mapped:
        if (
            allow_force_override
            and force_deepgram_language
            and str(force_deepgram_language).strip()
            and str(force_deepgram_language).strip() != str(mapped)
        ):
            return str(force_deepgram_language).strip()
        return str(mapped)

    # Accept raw Deepgram codes only when already normalized.
    raw = selected.lower()
    if raw in {str(v).lower() for v in LANGUAGE_MAP.values()}:
        for code in LANGUAGE_MAP.values():
            if str(code).lower() == raw:
                return str(code)

    raise UnknownLanguageSelectionError(
        f"Unknown or unsupported language selection: {ui_label!r} "
        f"(normalized={selected!r}). Refusing silent fallback."
    )


def build_language_profile(ui_label: str | None) -> dict[str, Any]:
    """Build the language profile used at Start Listening."""
    selected = normalize_ui_language_label(ui_label)
    try:
        code = resolve_ui_language_to_deepgram_code(selected)
    except UnknownLanguageSelectionError:
        return {
            "profile_id": "unsupported",
            "is_auto": False,
            "deepgram_language": None,
            "allowed_languages": [],
            "selection_supported": False,
            "unsupported_reason": "language_not_supported",
            "selected_ui_label": selected,
        }
    norm = code.split("-")[0].lower() if "-" in code else code.lower()
    return {
        "profile_id": f"manual_{code.replace('-', '_')}",
        "is_auto": False,
        "deepgram_language": code,
        "allowed_languages": [norm or code],
        "selection_supported": True,
        "unsupported_reason": None,
        "selected_ui_label": selected,
    }


def assert_bilingual_contract() -> None:
    """Fail loudly if LANGUAGE_MAP drifts from the bilingual contract."""
    for display, code in AUTHORITATIVE_UI_TO_DEEPGRAM.items():
        actual = LANGUAGE_MAP.get(display)
        if actual != code:
            raise RuntimeError(
                f"LANGUAGE_MAP drift: {display!r} expected {code!r}, got {actual!r}"
            )


__all__ = [
    "AUTHORITATIVE_UI_TO_DEEPGRAM",
    "UnknownLanguageSelectionError",
    "normalize_ui_language_label",
    "resolve_ui_language_to_deepgram_code",
    "build_language_profile",
    "assert_bilingual_contract",
]
