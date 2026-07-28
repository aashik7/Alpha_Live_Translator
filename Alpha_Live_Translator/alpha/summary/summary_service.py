"""Local MVP summary generation from stored transcript text (no external API)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Set

from alpha.summary.transcript_store import TranscriptSegment, TranscriptStore

_EMPTY_MESSAGE = "No transcript is available yet. Start listening first."

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "him",
    "his",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "my",
    "no",
    "not",
    "of",
    "on",
    "or",
    "our",
    "she",
    "so",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "they",
    "this",
    "to",
    "up",
    "us",
    "was",
    "we",
    "were",
    "what",
    "when",
    "with",
    "you",
    "your",
}


class SummaryService:
    """Generate a lightweight structured summary from transcript segments."""

    def generate_summary(self, transcript_text: str) -> str:
        cleaned = (transcript_text or "").strip()
        if not cleaned:
            return _EMPTY_MESSAGE
        return self._build_summary(cleaned)

    def generate_summary_from_store(self, store: TranscriptStore) -> str:
        if store is None or store.is_empty():
            return _EMPTY_MESSAGE
        return self.generate_summary(store.get_plain_text())

    def generate_summary_from_segments(self, segments: Iterable[TranscriptSegment]) -> str:
        lines = []
        for segment in segments:
            prefix = f"[Speaker {segment.speaker}] " if segment.speaker is not None else ""
            lines.append(f"{prefix}{segment.text}")
        return self.generate_summary("\n".join(lines))

    def _build_summary(self, transcript_text: str) -> str:
        lines = [line.strip() for line in transcript_text.splitlines() if line.strip()]
        speakers = self._extract_speakers(lines)
        word_count = self._count_words(transcript_text)
        minute_estimate = max(1, round(word_count / 150)) if word_count else 0
        key_points = self._extract_key_points(lines)
        repeated_terms = self._extract_repeated_terms(transcript_text)

        parts = ["Key points", "---------"]
        if key_points:
            parts.extend(f"• {point}" for point in key_points)
        else:
            parts.append("• No distinct key points detected yet.")

        parts.append("")
        parts.append("Speakers detected")
        parts.append("-----------------")
        if speakers:
            parts.append(", ".join(f"Speaker {num}" for num in sorted(speakers)))
        else:
            parts.append("No speaker labels found.")

        parts.append("")
        parts.append("Conversation length estimate")
        parts.append("----------------------------")
        parts.append(f"~{word_count} words ({minute_estimate} min estimated)")

        parts.append("")
        parts.append("Important repeated terms")
        parts.append("------------------------")
        if repeated_terms:
            parts.append(", ".join(repeated_terms))
        else:
            parts.append("None identified yet.")

        return "\n".join(parts)

    @staticmethod
    def _extract_speakers(lines: List[str]) -> Set[int]:
        speakers: Set[int] = set()
        for line in lines:
            match = re.match(r"\[Speaker (\d+)\]", line)
            if match:
                speakers.add(int(match.group(1)))
        return speakers

    @staticmethod
    def _count_words(text: str) -> int:
        return len(re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE))

    @staticmethod
    def _extract_key_points(lines: List[str], max_points: int = 5) -> List[str]:
        points: List[str] = []
        seen = set()
        for line in lines:
            body = re.sub(r"^\[Speaker \d+\]\s*", "", line).strip()
            if len(body) < 12:
                continue
            normalized = body.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            snippet = body if len(body) <= 120 else body[:117] + "..."
            points.append(snippet)
            if len(points) >= max_points:
                break
        return points

    @staticmethod
    def _extract_repeated_terms(text: str, max_terms: int = 8) -> List[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text.lower())
        filtered = [token for token in tokens if token not in _STOP_WORDS]
        counts = Counter(filtered)
        return [word for word, count in counts.most_common(max_terms) if count > 1]
