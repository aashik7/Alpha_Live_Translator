"""Structured event payloads for backend ↔ UI communication."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TranscriptEvent:
    """A finalized or interim transcript segment."""

    text: str
    speaker: Optional[str] = None
    timestamp: Optional[str] = None
    is_final: bool = True


@dataclass
class TranslationEvent:
    """A translation result from DeepL or same-language passthrough."""

    original_text: str
    translated_text: str
    source_language: str
    target_language: str
    speaker: Optional[str] = None
    timestamp: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class StatusEvent:
    """Application or session status change."""

    status: str
    message: Optional[str] = None


@dataclass
class ErrorEvent:
    """Recoverable or fatal error from backend pipeline."""

    message: str
    source: Optional[str] = None
    recoverable: bool = True
