"""Local meeting summary foundation (no external LLM in V5)."""

from alpha.summary.summary_service import SummaryService
from alpha.summary.transcript_store import TranscriptSegment, TranscriptStore

__all__ = [
    "SummaryService",
    "TranscriptSegment",
    "TranscriptStore",
]
