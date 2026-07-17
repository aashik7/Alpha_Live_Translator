"""Thread-safe in-memory store for finalized transcript segments."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TranscriptSegment:
    """One accepted transcript line with optional translation metadata."""

    speaker: Optional[int]
    text: str
    timestamp: Optional[str] = None
    source_language: Optional[str] = None
    target_language: Optional[str] = None
    translated_text: Optional[str] = None


class TranscriptStore:
    """Thread-safe store of finalized transcript segments for local summarization."""

    def __init__(self):
        self._segments: List[TranscriptSegment] = []
        self._lock = threading.Lock()

    def add_transcript(
        self,
        speaker,
        text,
        timestamp=None,
        source_language=None,
        target_language=None,
    ) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        speaker_num = int(speaker) if speaker is not None else None
        segment = TranscriptSegment(
            speaker=speaker_num,
            text=cleaned,
            timestamp=timestamp,
            source_language=source_language,
            target_language=target_language,
        )
        with self._lock:
            self._segments.append(segment)

    def add_translation(
        self,
        original_text,
        translated_text,
        speaker=None,
        timestamp=None,
    ) -> None:
        cleaned_original = (original_text or "").strip()
        cleaned_translation = (translated_text or "").strip()
        if not cleaned_original or not cleaned_translation:
            return
        speaker_num = int(speaker) if speaker is not None else None
        with self._lock:
            for segment in reversed(self._segments):
                if segment.text != cleaned_original:
                    continue
                if speaker_num is not None and segment.speaker != speaker_num:
                    continue
                segment.translated_text = cleaned_translation
                if timestamp and not segment.timestamp:
                    segment.timestamp = timestamp
                return

    def clear(self) -> None:
        with self._lock:
            self._segments.clear()

    def get_all(self) -> List[TranscriptSegment]:
        with self._lock:
            return list(self._segments)

    def get_plain_text(self) -> str:
        lines = []
        with self._lock:
            for segment in self._segments:
                prefix = f"[Speaker {segment.speaker}] " if segment.speaker is not None else ""
                lines.append(f"{prefix}{segment.text}")
        return "\n".join(lines)

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._segments) == 0
