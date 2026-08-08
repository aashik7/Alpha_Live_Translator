"""Thread-safe in-memory store for finalized transcript segments."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional

from alpha.transcription.speaker_boundary_guard import speakers_confirmed_same


@dataclass
class TranscriptSegment:
    """One accepted transcript line with optional translation metadata."""

    speaker: Optional[int]
    text: str
    timestamp: Optional[str] = None
    source_language: Optional[str] = None
    target_language: Optional[str] = None
    translated_text: Optional[str] = None
    canonical_utterance_id: Optional[str] = None


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
        canonical_utterance_id=None,
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
            canonical_utterance_id=str(canonical_utterance_id).strip()
            if canonical_utterance_id
            else None,
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

    def update_last_segment_unsafe_speaker_scan(
        self, speaker, text, timestamp=None
    ) -> bool:
        """Reverse-scan for this speaker's latest row and overwrite it.

        **Do not call from production code.** Renamed from
        `update_last_segment` by BUG_FIX_ROADMAP.md Batch 3 item 17: every
        caller paired it with a *different*, safer read
        (`get_last_segment_if_active` or the true-last `get_last_segment`)
        and then wrote through this reverse scan under a **separate** lock
        acquisition -- a check-then-act race where an intervening
        different-speaker append makes the write land on an older row than
        the one the decision was made from. All call sites now use
        `update_last_segment_if_active`.

        Retained (rather than deleted) only because
        `tests/test_task2g_acceptance_gate.py` deliberately pins this
        method's behavior to document the safe/unsafe delta. The explicit
        name is the guard: it cannot now be reached by reflex.
        """
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
            canonical_utterance_id=segment.canonical_utterance_id,
        )

    def get_last_segment(self) -> Optional[TranscriptSegment]:
        """Return the store's true last segment, whoever it belongs to.

        BUG_FIX_ROADMAP.md Batch 3 item 17 removed this method's optional
        `speaker` filter: with a speaker it reverse-scanned past intervening
        turns and returned a stale earlier row (the positional "last line"
        bug of TASK_2E_FINDINGS.md item 3). The no-speaker form is
        legitimate and unchanged -- it is genuinely "the last row" -- so it
        stays. Speaker-qualified lookups must use
        `get_last_segment_if_active`, or
        `get_last_segment_unsafe_speaker_scan` if the old semantics really
        are wanted.
        """
        with self._lock:
            if not self._segments:
                return None
            return self._copy_segment(self._segments[-1])

    def get_last_segment_unsafe_speaker_scan(
        self, speaker
    ) -> Optional[TranscriptSegment]:
        """Reverse-scan backwards for this speaker's latest row.

        **Do not call from production code.** Reaches back past an
        intervening different-speaker turn and returns a row that is no
        longer the one a new final would continue. Retained only because
        `tests/test_task2g_acceptance_gate.py` pins it to document the
        safe/unsafe delta; see the note on
        `update_last_segment_unsafe_speaker_scan`.
        """
        speaker_num = int(speaker) if speaker is not None else None
        with self._lock:
            for segment in reversed(self._segments):
                if segment.speaker == speaker_num:
                    return self._copy_segment(segment)
        return None

    def get_last_segment_if_active(self, speaker) -> Optional[TranscriptSegment]:
        """Return the store's true last segment, only if it belongs to `speaker`.

        fixes TASK_2E_FINDINGS.md item 3 (positional "last line" lookup):
        unlike get_last_segment(speaker), this never reaches backward past
        an intervening different-speaker turn to find some earlier row that
        happens to match -- a speaker change since is a hard boundary, and
        callers must treat that as "no valid previous segment" rather than
        merge into a stale one. Uses speakers_confirmed_same (fail-closed on
        unknown/None speakers) instead of a raw equality check.
        """
        speaker_num = int(speaker) if speaker is not None else None
        with self._lock:
            if not self._segments:
                return None
            last = self._segments[-1]
            if not speakers_confirmed_same(last.speaker, speaker_num):
                return None
            return self._copy_segment(last)

    def update_last_segment_if_active(self, speaker, text, timestamp=None) -> bool:
        """Update the store's true last segment, only if it belongs to `speaker`.

        fixes TASK_2E_FINDINGS.md item 3 (positional "last line" update):
        the write-side counterpart to get_last_segment_if_active -- refuses
        to overwrite any row except the store's actual last one, and only
        when that row is confirmed (fail-closed) to belong to this speaker.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        speaker_num = int(speaker) if speaker is not None else None
        with self._lock:
            if not self._segments:
                return False
            last = self._segments[-1]
            if not speakers_confirmed_same(last.speaker, speaker_num):
                return False
            last.text = cleaned
            if timestamp and not last.timestamp:
                last.timestamp = timestamp
            return True

    def add_translation(
        self,
        original_text,
        translated_text,
        speaker=None,
        timestamp=None,
        canonical_utterance_id="",
    ) -> None:
        cleaned_original = (original_text or "").strip()
        cleaned_translation = (translated_text or "").strip()
        if not cleaned_original or not cleaned_translation:
            return
        speaker_num = int(speaker) if speaker is not None else None
        cid = str(canonical_utterance_id or "").strip()
        with self._lock:
            # fixes BUG_FIX_ROADMAP.md Batch 3 item 18: this used to match
            # only by exact text equality against every stored segment. If
            # the segment's text was revised (e.g. a later correction)
            # between when the translation request went out and when the
            # result came back, no segment's text equals the original
            # request text anymore, the loop finds nothing, and the
            # translation is dropped with no log at all. When the caller
            # can supply the segment's canonical_utterance_id, match on
            # that first -- it survives text revisions. If an id was
            # supplied but no segment carries it, that is itself worth
            # knowing (fail loud, not silently fall back to a possibly
            # wrong text match) rather than reintroducing the same silent
            # drop through the back door.
            if cid:
                for segment in reversed(self._segments):
                    if segment.canonical_utterance_id != cid:
                        continue
                    segment.translated_text = cleaned_translation
                    if timestamp and not segment.timestamp:
                        segment.timestamp = timestamp
                    return
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "TRANSLATION_STORE_ID_MATCH_NOT_FOUND",
                        canonical_utterance_id=cid,
                        original_text_preview=cleaned_original[:120],
                    )
                except Exception:
                    pass
                return
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
