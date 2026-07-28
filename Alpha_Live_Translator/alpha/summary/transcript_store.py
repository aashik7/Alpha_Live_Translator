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
    """Thread-safe store of clean finalized transcript segments."""

    def __init__(self):
        self._segments: List[TranscriptSegment] = []
        self._lock = threading.Lock()

    def add_segment(
        self,
        speaker,
        text,
        timestamp=None,
        source_language=None,
        target_language=None,
    ) -> None:
        """Add one accepted clean transcript segment."""
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

    def add_transcript(
        self,
        speaker,
        text,
        timestamp=None,
        source_language=None,
        target_language=None,
    ) -> None:
        """Backward-compatible alias for add_segment."""
        self.add_segment(
            speaker=speaker,
            text=text,
            timestamp=timestamp,
            source_language=source_language,
            target_language=target_language,
        )

    def update_last_segment(self, speaker, text, timestamp=None) -> bool:
        """Update the latest segment for the same speaker; returns False if none found."""
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        speaker_num = int(speaker) if speaker is not None else None
        with self._lock:
            for segment in reversed(self._segments):
                if segment.speaker != speaker_num:
                    continue
                segment.text = cleaned
                if timestamp and not segment.timestamp:
                    segment.timestamp = timestamp
                return True
        return False

    def _copy_segment(self, segment: TranscriptSegment) -> TranscriptSegment:
        return TranscriptSegment(
            speaker=segment.speaker,
            text=segment.text,
            timestamp=segment.timestamp,
            source_language=segment.source_language,
            target_language=segment.target_language,
            translated_text=segment.translated_text,
        )

    def get_last_segment(self, speaker=None) -> Optional[TranscriptSegment]:
        """Return the latest segment, optionally filtered by speaker."""
        with self._lock:
            if speaker is None:
                if not self._segments:
                    return None
                return self._copy_segment(self._segments[-1])

            speaker_num = int(speaker) if speaker is not None else None
            for segment in reversed(self._segments):
                if segment.speaker == speaker_num:
                    return self._copy_segment(segment)
        return None

    def get_last_segment_for_speaker(self, speaker) -> Optional[TranscriptSegment]:
        """Backward-compatible alias for get_last_segment(speaker=...)."""
        return self.get_last_segment(speaker=speaker)

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

    def get_clean_text(self) -> str:
        """Return canonical transcript text for copy/export (one segment per line)."""
        from alpha.utils.ui_speaker_label import format_ui_speaker_line

        lines = []
        with self._lock:
            for segment in self._segments:
                lines.append(format_ui_speaker_line(segment.text))
        return "\n".join(lines)

    def get_plain_text(self) -> str:
        """Backward-compatible alias for get_clean_text."""
        return self.get_clean_text()

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._segments) == 0

    def segment_count(self) -> int:
        with self._lock:
            return len(self._segments)
