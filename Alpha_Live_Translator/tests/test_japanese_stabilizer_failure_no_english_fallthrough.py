"""Regression test for BUG_FIX_ROADMAP.md Batch 2, item 7b (audit §2.10).

Confirmed defect: in deepgram_client.py::_commit_final_transcript_segment,
the language-path decision (should_use_japanese_final_stabilizer) and the
Japanese stabilizer work (get/ingest) shared a single try/except. Any
failure inside -- most importantly `stabilizer.ingest(...)` raising --
was printed to console and then **fell through** into the
English/generic block below.

Two distinct wrong outcomes were reachable from that fall-through:

1. Common case: should_use_utterance_lifecycle() independently rejects
   Japanese (its own inner guard), so the lifecycle block is skipped and
   the final reaches _publish_final_transcript_segment anyway -- i.e. the
   Japanese continuity assembler is bypassed entirely and a raw,
   unassembled Deepgram fragment is committed.
2. Narrow case: if the Japanese guard itself is what broke AND
   host._listen_language is unset/empty, should_use_utterance_lifecycle()
   returns True (it falls back to a lang check that defaults to "", which
   does not start with "ja"), so the Japanese final IS fed into
   utterance_lifecycle.on_final_chunk -- the English-only controller.
   This is the outcome audit §2.10 describes.

Fix: split the two try blocks. Fall-through is preserved only for the
case where it is correct (language path genuinely undeterminable); a
confirmed Japanese session whose stabilizer fails now publishes directly
and logs, never reaching the English/generic path.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402


class _Host:
    """Minimal host exposing only what _commit_final_transcript_segment uses."""

    _commit_final_transcript_segment = (
        DeepgramClientMixin._commit_final_transcript_segment
    )

    def __init__(self, listen_language="ja"):
        self._listen_language = listen_language
        self.is_listening = True
        self._is_finalizing = False
        self._is_stopping = False
        self.published = []

    def _allow_final_transcript_commit(self):
        return True

    def _publish_final_transcript_segment(self, speaker, text, metadata=None):
        self.published.append((speaker, text))
        return True


class TestJapaneseStabilizerFailureDoesNotReachEnglishPath(unittest.TestCase):
    def test_ingest_failure_publishes_directly_and_never_enters_lifecycle(self):
        host = _Host(listen_language="ja")
        fake_stabilizer = MagicMock()
        fake_stabilizer.ingest.side_effect = RuntimeError("boom: ingest")

        with patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.should_use_japanese_final_stabilizer",
            return_value=True,
        ), patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.get_japanese_final_stabilizer",
            return_value=fake_stabilizer,
        ), patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.is_accepting_japanese_transcripts",
            return_value=True,
        ), patch(
            "alpha.transcription.utterance_lifecycle.get_utterance_lifecycle"
        ) as mock_lifecycle, patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log"
        ) as mock_log:
            result = host._commit_final_transcript_segment(1, "テスト文です。")

        self.assertTrue(result)
        # Text preserved, published directly.
        self.assertEqual(host.published, [(1, "テスト文です。")])
        # The English-only controller must never see a Japanese final.
        mock_lifecycle.assert_not_called()
        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("JAPANESE_STABILIZER_INGEST_FAILED", events)

    def test_ingest_failure_with_unset_language_still_avoids_lifecycle(self):
        # The narrow audit §2.10 path: with _listen_language unset,
        # should_use_utterance_lifecycle() would return True if reached.
        # The fix must ensure it is never reached for a confirmed
        # Japanese session.
        host = _Host(listen_language="")
        fake_stabilizer = MagicMock()
        fake_stabilizer.ingest.side_effect = RuntimeError("boom: ingest")

        with patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.should_use_japanese_final_stabilizer",
            return_value=True,
        ), patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.get_japanese_final_stabilizer",
            return_value=fake_stabilizer,
        ), patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.is_accepting_japanese_transcripts",
            return_value=True,
        ), patch(
            "alpha.transcription.utterance_lifecycle.get_utterance_lifecycle"
        ) as mock_lifecycle:
            host._commit_final_transcript_segment(1, "テスト文です。")

        mock_lifecycle.assert_not_called()
        self.assertEqual(host.published, [(1, "テスト文です。")])

    def test_successful_japanese_ingest_is_unchanged(self):
        host = _Host(listen_language="ja")
        fake_stabilizer = MagicMock()
        fake_stabilizer.ingest.return_value = True

        with patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.should_use_japanese_final_stabilizer",
            return_value=True,
        ), patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.get_japanese_final_stabilizer",
            return_value=fake_stabilizer,
        ), patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.is_accepting_japanese_transcripts",
            return_value=True,
        ):
            result = host._commit_final_transcript_segment(1, "テスト文です。")

        self.assertTrue(result)
        fake_stabilizer.ingest.assert_called_once()
        # Normal Japanese path must NOT publish directly -- the assembler
        # owns publishing from here.
        self.assertEqual(host.published, [])

    def test_undeterminable_language_publishes_directly_not_via_lifecycle(self):
        # When the language decision itself fails, the pre-fix code fell
        # through to the English/generic block -- where
        # should_use_utterance_lifecycle() re-derives the same decision
        # from the same (now broken) helper, and on ITS inner failure
        # falls back to a lang check defaulting to "", which does not
        # start with "ja" and so returns True. That is the exact narrow
        # path by which a Japanese final could reach the English-only
        # controller (audit §2.10). The fix must not reach that block at
        # all on a detection failure.
        host = _Host(listen_language="")

        with patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.should_use_japanese_final_stabilizer",
            side_effect=RuntimeError("boom: detection"),
        ), patch(
            "alpha.transcription.utterance_lifecycle.get_utterance_lifecycle"
        ) as mock_lifecycle, patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log"
        ) as mock_log:
            result = host._commit_final_transcript_segment(1, "テスト文です。")

        self.assertTrue(result)
        # Text preserved.
        self.assertEqual(host.published, [(1, "テスト文です。")])
        # The English-only controller must never be reached.
        mock_lifecycle.assert_not_called()
        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("JAPANESE_PATH_DETECTION_FAILED", events)


if __name__ == "__main__":
    unittest.main()
