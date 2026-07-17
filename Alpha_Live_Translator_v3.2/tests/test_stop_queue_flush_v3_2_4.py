"""Queue flush stop-finalization tests for Alpha Live Translator v3.2.4."""

import json
import queue
import sys
import threading
import time
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
        self.close_before_finalize = False

    def send(self, data, opcode=None):
        self.sent.append((data, opcode))

    def close(self):
        has_finalize = any(
            isinstance(payload, str)
            and payload.startswith("{")
            and json.loads(payload).get("type") == "Finalize"
            for payload, _ in self.sent
        )
        if not has_finalize:
            self.close_before_finalize = True
        self.closed = True


class Host(DeepgramClientMixin):
    def __init__(self, ws):
        self._dg_ws = ws
        self._audio_q = queue.Queue()
        self._stop_event = threading.Event()
        self._ensure_graceful_stop_state()
        self.is_listening = True
        self.drained_chunks = []

    def start_sender(self):
        def sender():
            while not self._stop_event.is_set():
                if getattr(self, "_dg_stop_sending_audio", False):
                    time.sleep(0.01)
                    continue
                try:
                    chunk = self._audio_q.get(timeout=0.05)
                except queue.Empty:
                    continue
                self.drained_chunks.append(chunk)
                self._dg_ws.send(chunk, opcode=2)

        thread = threading.Thread(target=sender, daemon=True)
        thread.start()
        return thread


def _control_types(sent_items):
    result = []
    for payload, _ in sent_items:
        if isinstance(payload, str) and payload.startswith("{"):
            result.append(json.loads(payload).get("type"))
    return result


class TestStopQueueFlushV324(unittest.TestCase):
    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.5)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_FINALIZE_WAIT_MAX_S", 0.08)
    @patch("alpha.transcription.deepgram_client.STOP_CLOSE_WAIT_MAX_S", 0.03)
    def test_queue_flush_happens_before_finalize(self):
        ws = FakeWebSocket()
        host = Host(ws)
        for payload in [b"a", b"b", b"c", b"d"]:
            host._audio_q.put(payload)
        host.start_sender()

        result = host.stop_gracefully(timeout_seconds=2.0)

        self.assertFalse(result.get("skipped"))
        self.assertEqual(host.get_outgoing_audio_queue_size(), 0)
        self.assertEqual(host.drained_chunks, [b"a", b"b", b"c", b"d"])
        types = _control_types(ws.sent)
        self.assertIn("Finalize", types)
        self.assertIn("CloseStream", types)
        self.assertLess(types.index("Finalize"), types.index("CloseStream"))
        self.assertFalse(ws.close_before_finalize)
        self.assertTrue(ws.closed)

    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.12)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_FINALIZE_WAIT_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_CLOSE_WAIT_MAX_S", 0.02)
    def test_flush_timeout_does_not_crash(self):
        ws = FakeWebSocket()
        host = Host(ws)
        # No sender thread: queue does not drain before timeout.
        host._audio_q.put(b"late-a")
        host._audio_q.put(b"late-b")

        result = host.stop_gracefully(timeout_seconds=1.0)

        self.assertFalse(result.get("skipped"))
        self.assertGreaterEqual(host.get_outgoing_audio_queue_size(), 1)
        types = _control_types(ws.sent)
        self.assertIn("Finalize", types)
        self.assertIn("CloseStream", types)
        self.assertTrue(ws.closed)

    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.03)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.01)
    @patch("alpha.transcription.deepgram_client.STOP_FINALIZE_WAIT_MAX_S", 0.03)
    @patch("alpha.transcription.deepgram_client.STOP_CLOSE_WAIT_MAX_S", 0.01)
    def test_stop_twice_is_safe(self):
        ws = FakeWebSocket()
        host = Host(ws)
        first = host.stop_gracefully(timeout_seconds=1.0)
        second = host.stop_gracefully(timeout_seconds=1.0)
        self.assertFalse(first.get("skipped"))
        self.assertTrue(second.get("skipped"))
        self.assertEqual(_control_types(ws.sent).count("Finalize"), 1)
        self.assertEqual(_control_types(ws.sent).count("CloseStream"), 1)


if __name__ == "__main__":
    unittest.main()
