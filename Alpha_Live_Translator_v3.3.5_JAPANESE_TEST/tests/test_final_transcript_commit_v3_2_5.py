"""Final transcript commit tests for Alpha Live Translator v3.2.5."""

import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, data, opcode=None):
        self.sent.append((data, opcode))

    def close(self):
        self.closed = True


class CommitHost(DeepgramClientMixin):
    def __init__(self, ws=None):
        self._dg_ws = ws or FakeWebSocket()
        self._audio_q = queue.Queue()
        self._stop_event = threading.Event()
        self.is_listening = False
        self.transcript_queue = queue.Queue()
        self.committed = []
        self.ui_flush_calls = 0
        self._ensure_graceful_stop_state()

    def publish_transcript_event(self, text, speaker=None, timestamp=None, is_final=True, queue_item=None):
        self.committed.append(
            {
                "speaker": speaker,
                "text": text,
                "is_final": is_final,
                "queue_item": queue_item,
            }
        )
        if queue_item is not None:
            self.transcript_queue.put(queue_item)

    def _request_ui_transcript_queue_flush(self, timeout_seconds=1.0):
        self.ui_flush_calls += 1


class TestFinalTranscriptCommitV325(unittest.TestCase):
    def test_commit_allowed_while_listening(self):
        host = CommitHost()
        host.is_listening = True
        self.assertTrue(host._allow_final_transcript_commit())
        self.assertTrue(host._commit_final_transcript_segment(1, "hello world"))
        self.assertEqual(len(host.committed), 1)

    def test_commit_allowed_while_finalizing(self):
        host = CommitHost()
        host.is_listening = False
        host._is_finalizing = True
        host._dg_receiver_allowed = True
        self.assertTrue(host._allow_final_transcript_commit())
        self.assertTrue(host._commit_final_transcript_segment(1, "tail sentence"))
        self.assertEqual(len(host.committed), 1)

    def test_commit_blocked_after_receiver_disabled(self):
        host = CommitHost()
        host.is_listening = False
        host._is_finalizing = False
        host._dg_receiver_allowed = False
        self.assertFalse(host._allow_final_transcript_commit())
        self.assertFalse(host._commit_final_transcript_segment(1, "too late"))
        self.assertEqual(len(host.committed), 0)

    @patch("alpha.transcription.deepgram_client.STOP_CAPTURE_OPEN_FLUSH_MAX_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_FINALIZE_WAIT_MAX_S", 0.08)
    @patch("alpha.transcription.deepgram_client.STOP_CLOSE_WAIT_MAX_S", 0.03)
    def test_receiver_allowed_during_finalize_wait(self):
        host = CommitHost()
        checks = []

        def checker(h):
            checks.append(bool(h._dg_receiver_allowed))

        host._dg_receiver_allowed_check = checker
        host.stop_gracefully(timeout_seconds=2.0)
        self.assertTrue(checks)
        self.assertTrue(all(checks))
        self.assertFalse(host._dg_receiver_allowed)

    @patch("alpha.transcription.deepgram_client.STOP_CAPTURE_OPEN_FLUSH_MAX_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.03)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_FINALIZE_WAIT_MAX_S", 0.04)
    @patch("alpha.transcription.deepgram_client.STOP_CLOSE_WAIT_MAX_S", 0.02)
    def test_stop_twice_is_safe(self):
        host = CommitHost()
        first = host.stop_gracefully(timeout_seconds=1.0)
        second = host.stop_gracefully(timeout_seconds=1.0)
        self.assertFalse(first.get("skipped"))
        self.assertTrue(second.get("skipped"))

    @patch("alpha.transcription.deepgram_client.STOP_CAPTURE_OPEN_FLUSH_MAX_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.03)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_FINALIZE_WAIT_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_CLOSE_WAIT_MAX_S", 0.02)
    def test_finalize_before_receiver_disabled(self):
        host = CommitHost()
        receiver_during_finalize = {"value": False}

        def checker(h):
            if h._is_finalizing:
                receiver_during_finalize["value"] = bool(h._dg_receiver_allowed)

        host._dg_receiver_allowed_check = checker
        host.stop_gracefully(timeout_seconds=1.0)
        self.assertTrue(receiver_during_finalize["value"])
        self.assertFalse(host._dg_receiver_allowed)
        self.assertGreaterEqual(host.ui_flush_calls, 1)


if __name__ == "__main__":
    unittest.main()
