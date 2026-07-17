"""Graceful stop / Deepgram finalize tests for Alpha Live Translator v3.2.2."""

import json
import queue
import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.deepgram_client import (  # noqa: E402
    DeepgramClientMixin,
    GRACEFUL_STOP_DEFAULT_TIMEOUT_S,
)


class FakeWebSocket:
  """Minimal stand-in for websocket-client send/close."""

  def __init__(self):
    self.sent = []
    self.closed = False
    self.send_raises = False

  def send(self, data, opcode=None):
    if self.send_raises:
      raise OSError("send failed")
    self.sent.append((data, opcode))

  def close(self):
    self.closed = True


class BrokenWebSocket:
  def send(self, data, opcode=None):
    raise OSError("broken socket")

  def close(self):
    raise OSError("broken close")


class GracefulStopHost(DeepgramClientMixin):
  """Lightweight host object for mixin unit tests."""

  def __init__(self, ws=None):
    self._dg_ws = ws
    self._audio_q = queue.Queue()
    self._stop_event = threading.Event()
    self.is_listening = True
    self._ensure_graceful_stop_state()


class TestGracefulStopV322(unittest.TestCase):
  def test_request_finalize_sends_exact_payload(self):
    host = GracefulStopHost(FakeWebSocket())
    self.assertTrue(host.request_finalize())
    payload = json.loads(host._dg_ws.sent[0][0])
    self.assertEqual(payload, {"type": "Finalize"})

  def test_request_close_stream_sends_exact_payload(self):
    host = GracefulStopHost(FakeWebSocket())
    self.assertTrue(host.request_close_stream())
    payload = json.loads(host._dg_ws.sent[0][0])
    self.assertEqual(payload, {"type": "CloseStream"})

  def test_closed_socket_requests_are_safe(self):
    host = GracefulStopHost(ws=None)
    self.assertFalse(host.request_finalize())
    self.assertFalse(host.request_close_stream())

  def test_send_failures_are_handled_safely(self):
    host = GracefulStopHost(BrokenWebSocket())
    self.assertFalse(host.request_finalize())
    self.assertFalse(host.request_close_stream())

  def test_graceful_stop_twice_is_idempotent(self):
    ws = FakeWebSocket()
    host = GracefulStopHost(ws)
    first = host.stop_gracefully(timeout_seconds=0.2)
    second = host.stop_gracefully(timeout_seconds=0.2)
    self.assertFalse(first.get("skipped"))
    self.assertTrue(second.get("skipped"))
    finalize_messages = [
      json.loads(item[0])
      for item in ws.sent
      if isinstance(item[0], str) and item[0].startswith("{")
    ]
    self.assertEqual(
      [msg for msg in finalize_messages if msg.get("type") == "Finalize"],
      [{"type": "Finalize"}],
    )
    self.assertEqual(
      [msg for msg in finalize_messages if msg.get("type") == "CloseStream"],
      [{"type": "CloseStream"}],
    )
    self.assertTrue(ws.closed)
    self.assertTrue(host._stop_event.is_set())

  def test_graceful_stop_respects_timeout(self):
    host = GracefulStopHost(FakeWebSocket())
    start = time.perf_counter()
    result = host.stop_gracefully(timeout_seconds=0.15)
    elapsed = time.perf_counter() - start
    self.assertLess(elapsed, GRACEFUL_STOP_DEFAULT_TIMEOUT_S)
    self.assertLessEqual(elapsed, 1.5)
    self.assertTrue(result.get("timed_out") or result.get("finalized"))


if __name__ == "__main__":
  unittest.main()
