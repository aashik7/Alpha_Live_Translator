"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, item 20 (audit §1.3).

Confirmed defect: `_commit_final_transcript_segment` built the lifecycle's
`event_id` as

    meta.get("event_id") or meta.get("request_id") or f"dg-final-{ns}"

`segment_metadata` never carries an "event_id" key, so the first term was
always None and `request_id` -- Deepgram's **connection-level** id,
identical for every utterance in the session -- always won. The unique
fallback was unreachable.

Why it mattered: event_id feeds `active.lineage_ids`, which becomes
`source_raw_event_ids` on the canonical ledger record, which
`stable_revision_decision._same_revision_chain` consumes via
`_lineage_overlap()`. A session constant makes that overlap non-zero for
**every** pair of utterances, degrading the lineage half of the
same-segment test to a constant-true check. Measured in real runs: one
such id appears in 13 of 14 canonical records (`...155842`) and in 30
records (`...133236`).

The connection id is not lost -- it is passed separately as
`deepgram_request_id`. The Japanese path was never affected; it supplies
per-event `raw-NNNNNN` ids that genuinely vary.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402

CONNECTION_REQUEST_ID = "019fe02a-b64c-7721-ab1e-8794d3c4734e"


class _Host:
    _commit_final_transcript_segment = (
        DeepgramClientMixin._commit_final_transcript_segment
    )

    def __init__(self):
        self._listen_language = "en"
        self.published = []

    def _allow_final_transcript_commit(self):
        return True

    def _publish_final_transcript_segment(self, speaker, text, metadata=None):
        self.published.append((speaker, text))
        return True


def _capture_event_ids(calls=2):
    """Drive the real commit path and collect each lifecycle event_id."""
    host = _Host()
    seen = []

    lifecycle = MagicMock()
    lifecycle.on_final_chunk.side_effect = lambda **kw: (
        seen.append(kw), MagicMock(should_commit=False)
    )[1]

    with patch(
        "alpha.transcription.utterance_lifecycle.get_utterance_lifecycle",
        return_value=lifecycle,
    ), patch(
        "alpha.transcription.utterance_lifecycle.should_use_utterance_lifecycle",
        return_value=True,
    ), patch(
        "alpha.transcription.japanese_final_chunk_stabilizer.should_use_japanese_final_stabilizer",
        return_value=False,
    ):
        for i in range(calls):
            host._commit_final_transcript_segment(
                1,
                f"utterance number {i}",
                metadata={
                    # Exactly what segment_metadata supplies: a
                    # connection-level request_id and no event_id.
                    "request_id": CONNECTION_REQUEST_ID,
                    "channel_index": [0, 1],
                    "speech_final": True,
                },
            )
    return seen


class TestFinalEventIdIsPerUtterance(unittest.TestCase):
    def test_event_id_is_not_the_connection_request_id(self):
        seen = _capture_event_ids(1)
        self.assertTrue(seen, "the lifecycle should have been reached")
        self.assertNotEqual(
            seen[0]["event_id"],
            CONNECTION_REQUEST_ID,
            "event_id must not be the session-constant connection request_id "
            "-- that is what made _lineage_overlap constant-true",
        )

    def test_consecutive_finals_get_distinct_event_ids(self):
        seen = _capture_event_ids(2)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(
            seen[0]["event_id"],
            seen[1]["event_id"],
            "two different utterances must not share a lineage id",
        )

    def test_connection_request_id_is_still_reported_separately(self):
        # The fix must not lose the provider's connection id -- it simply
        # belongs in its own field, not in per-utterance lineage.
        seen = _capture_event_ids(1)
        self.assertEqual(seen[0]["deepgram_request_id"], CONNECTION_REQUEST_ID)

    def test_explicit_event_id_in_metadata_still_wins(self):
        # Callers that DO supply a real per-event id keep priority; only the
        # request_id fallback was removed.
        host = _Host()
        seen = []
        lifecycle = MagicMock()
        lifecycle.on_final_chunk.side_effect = lambda **kw: (
            seen.append(kw), MagicMock(should_commit=False)
        )[1]

        with patch(
            "alpha.transcription.utterance_lifecycle.get_utterance_lifecycle",
            return_value=lifecycle,
        ), patch(
            "alpha.transcription.utterance_lifecycle.should_use_utterance_lifecycle",
            return_value=True,
        ), patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.should_use_japanese_final_stabilizer",
            return_value=False,
        ):
            host._commit_final_transcript_segment(
                1,
                "some text",
                metadata={
                    "event_id": "raw-000042",
                    "request_id": CONNECTION_REQUEST_ID,
                },
            )

        self.assertEqual(seen[0]["event_id"], "raw-000042")


if __name__ == "__main__":
    unittest.main()
