"""Map UI language names to DeepL API language codes."""

from typing import Optional

# UI display names (from language dropdowns) -> DeepL codes
_UI_TO_DEEPL = {
    "English": "EN",
    "Japanese": "JA",
    "Chinese (Mandarin)": "ZH",
    "Russian": "RU",
}


def get_deepl_source_code(language_name: str) -> Optional[str]:
    """Return DeepL source language code for a UI language name, or None if unsupported."""
    if not language_name:
        return None
    return _UI_TO_DEEPL.get(language_name.strip())


def get_deepl_target_code(language_name: str) -> Optional[str]:
    """Return DeepL target language code for a UI language name, or None if unsupported."""
    return get_deepl_source_code(language_name)


def is_same_language(source_language: str, target_language: str) -> bool:
    """Return True when source and target map to the same DeepL code."""
    source_code = get_deepl_source_code(source_language)
    target_code = get_deepl_target_code(target_language)
    if source_code is None or target_code is None:
        return False
    return source_code == target_code
