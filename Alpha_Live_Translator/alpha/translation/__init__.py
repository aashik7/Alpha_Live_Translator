"""DeepL translation integration for Alpha Live Translator."""

from alpha.translation.deepl_client import DeepLClient, DeepLError
from alpha.translation.language_map import (
    get_deepl_source_code,
    get_deepl_target_code,
    is_same_language,
    target_for_source,
)
from alpha.translation.translation_worker import TranslationResult, TranslationWorker

__all__ = [
    "DeepLClient",
    "DeepLError",
    "TranslationResult",
    "TranslationWorker",
    "get_deepl_source_code",
    "get_deepl_target_code",
    "is_same_language",
    "target_for_source",
]
