"""Stop finalization tests for Alpha Live Translator v3.2.3."""

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

from alpha.transcription.deepgram_client import (  # noqa: E402
    DeepgramClientMixin,
    GRACEFUL_CLOSE_WAIT_S,
    GRACEFUL_DRAIN_MAX_S,
    GRACEFUL_FINALIZE_WAIT_S,
)


class FakeWebSocket:
    """Tracks send order and close timing."""

    def __init__(self):
        self.sent = []
        self.closed = False
        self.close_before_finalize = False

    def send(self, data, opcode=None):
        self.sent.append((data, opcode))

    def close(self):
        finalized = any(
            isinstance(item[0], str)
            and json.loads(item[0]).get("type") == "Finalize"
            for item in self.sent
        )
        if not finalized:
            self.close_before_finalize = True
        self.closed = True


class BrokenWebSocket:
    def send(self, data, opcode=None):
        raise OSError("broken socket")

    def close(self):
        raise OSError("broken close")


class StopFinalizeHost(DeepgramClientMixin):
    def __init__(self, ws=None):
        self._dg_ws = ws
        self._audio_q = queue.Queue()
        self._stop_event = threading.Event()
        self.is_listening = True
        self._ensure_graceful_stop_state()
        self.receiver_checks = []

    def _receiver_check(self, host):
        self.receiver_checks.append(
            (host._dg_receiver_allowed, host._stop_event.is_set())
        )


def _control_message_types(ws):
    types = []
    for data, _opcode in ws.sent:
        if isinstance(data, str) and data.startswith("{"):
            types.append(json.loads(data).get("type"))
    return types


class TestStopFinalizeV323(unittest.TestCase):
    def test_request_finalize_payload(self):
        host = StopFinalizeHost(FakeWebSocket())
        self.assertTrue(host.request_finalize())
        self.assertEqual(json.loads(host._dg_ws.sent[0][0]), {"type": "Finalize"})

    def test_request_close_stream_payload(self):
        host = StopFinalizeHost(FakeWebSocket())
        self.assertTrue(host.request_close_stream())
        self.assertEqual(json.loads(host._dg_ws.sent[0][0]), {"type": "CloseStream"})

    @patch("alpha.transcription.deepgram_client.GRACEFUL_DRAIN_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.GRACEFUL_FINALIZE_WAIT_S", 0.08)
    @patch("alpha.transcription.deepgram_client.GRACEFUL_CLOSE_WAIT_S", 0.05)
    def test_stop_gracefully_order_and_socket_close(self):
        ws = FakeWebSocket()
        host = StopFinalizeHost(ws)
        host._dg_receiver_allowed_check = host._receiver_check
        result = host.stop_gracefully(timeout_seconds=7.0)
        self.assertFalse(result.get("skipped"))
        types = _control_message_types(ws)
        self.assertIn("Finalize", types)
        self.assertIn("CloseStream", types)
        self.assertLess(types.index("Finalize"), types.index("CloseStream"))
        self.assertFalse(ws.close_before_finalize)
        self.assertTrue(ws.closed)
        self.assertTrue(host._stop_event.is_set())
        self.assertTrue(host.receiver_checks)
        self.assertTrue(all(allowed for allowed, _ in host.receiver_checks))
        self.assertTrue(all(not stop_set for _, stop_set in host.receiver_checks))

    @patch("alpha.transcription.deepgram_client.GRACEFUL_DRAIN_MAX_S", 0.03)
    @patch("alpha.transcription.deepgram_client.GRACEFUL_FINALIZE_WAIT_S", 0.05)
    @patch("alpha.transcription.deepgram_client.GRACEFUL_CLOSE_WAIT_S", 0.03)
    def test_stop_gracefully_twice_is_safe(self):
        ws = FakeWebSocket()
        host = StopFinalizeHost(ws)
        first = host.stop_gracefully(timeout_seconds=7.0)
        second = host.stop_gracefully(timeout_seconds=7.0)
        self.assertFalse(first.get("skipped"))
        self.assertTrue(second.get("skipped"))
        self.assertEqual(_control_message_types(ws).count("Finalize"), 1)
        self.assertEqual(_control_message_types(ws).count("CloseStream"), 1)

    def test_closed_socket_requests_are_safe(self):
        host = StopFinalizeHost(ws=None)
        self.assertFalse(host.request_finalize())
        self.assertFalse(host.request_close_stream())

    def test_send_failures_are_handled_safely(self):
        host = StopFinalizeHost(BrokenWebSocket())
        self.assertFalse(host.request_finalize())
        self.assertFalse(host.request_close_stream())

    def test_phase_constants_match_spec(self):
        self.assertEqual(GRACEFUL_DRAIN_MAX_S, 1.5)
        self.assertEqual(GRACEFUL_FINALIZE_WAIT_S, 4.0)
        self.assertEqual(GRACEFUL_CLOSE_WAIT_S, 1.5)


if __name__ == "__main__":
    unittest.main()
