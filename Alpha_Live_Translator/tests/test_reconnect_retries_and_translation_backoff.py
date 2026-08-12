"""Regression tests for live run ...20260812-142447 -- the retest that failed.

The user dropped WiFi at 14:30:00 and restored it at 14:30:45. Two independent
faults made the app never come back, and neither is visible in any log as an
error, which is why both survived earlier review.

**Reconnect made exactly one attempt.** The item 44 keepalive worked -- the
dead socket WAS detected and `_deepgram_on_close` fired (the run has
`DEEPGRAM_AUDIO_GAP_MARKED`). But `_schedule_reconnect` is single-flight on
`_dg_reconnecting`, and `websocket-client` calls `on_close` from *inside*
`run_forever`. So when the attempt failed because the network was still down,
the `on_close -> _schedule_reconnect()` retry signal arrived while
`_dg_reconnecting` was still True and was silently dropped; the `finally` then
cleared the flag with nobody left to call again. Result: last transcript commit
14:30:01, 217 audio chunks discarded, session ended `failed`, nothing after the
network returned. Retrying is now the loop's own job.

**A dropped connection was classified as permanently fatal for translation.**
`deepl.ConnectionException` subclasses `DeepLException`, so it fell to the
catch-all `retryable=False` branch: the run shows `status: permanently_failed`,
`retry_count: 0`, `successful_translations: 0`. The one translation request of
the session was issued at 14:30:01 -- during the drop -- and was never retried.

Both are tested by driving the real code: the reconnect loop with a socket that
fails the way a dead network does, and the real DeepL error mapper.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import deepgram_client as dc  # noqa: E402


class _DeadSocket:
    """A WebSocketApp whose run_forever cannot connect.

    Calls on_close from INSIDE run_forever before returning, which is what
    websocket-client does on a failed connect and is precisely why the old
    single-attempt code swallowed its own retry signal.
    """

    attempts: list = []
    on_close_from_inside = True

    def __init__(self, *_a, **kw):
        self._on_close = kw.get("on_close")

    def close(self):
        pass

    def run_forever(self, **_kw):
        type(self).attempts.append(time.time())
        if self.on_close_from_inside and self._on_close:
            self._on_close(self, 1006, "network down")


class _Host:
    _schedule_reconnect = dc.DeepgramClientMixin._schedule_reconnect
    _reconnect_deepgram = dc.DeepgramClientMixin._reconnect_deepgram
    _deepgram_on_close = dc.DeepgramClientMixin._deepgram_on_close
    _mark_deepgram_gap_if_any = dc.DeepgramClientMixin._mark_deepgram_gap_if_any
    deepgram_gap_seconds = dc.DeepgramClientMixin.deepgram_gap_seconds

    def __init__(self):
        self.is_listening = True
        self._stop_event = threading.Event()
        self._is_stopping = False
        self._dg_stop_sending_audio = False
        self._dg_reconnect_lock = threading.RLock()
        self._dg_reconnecting = False
        self._dg_ws = None
        self._audio_q = None
        self._dg_backoff_seconds = 1.0
        self._dg_disconnected_at = 0.0
        self._dg_replay_buffer = []
        self._dg_awaiting_transcript_reset = False
        self._listen_language = "en"
        self._last_committed_speaker = 1
        self.gap_markers = []

    def _build_deepgram_url(self):
        return "wss://example.invalid/v1/listen"

    def _publish_final_transcript_segment(self, _spk, text, *_a, **_kw):
        self.gap_markers.append(text)
        return True

    def _deepgram_on_message(self, *_a, **_kw):
        return None

    def _deepgram_on_open(self, *_a, **_kw):
        return None

    def _deepgram_on_error(self, *_a, **_kw):
        return None


class ReconnectKeepsRetryingTest(unittest.TestCase):
    def setUp(self):
        # A FRESH socket class per test: these reconnect loops run in daemon
        # threads that can outlive the test that started them, and a shared
        # class-level counter would let one test's late attempt be counted by
        # the next one.
        self.sock = type("_Sock", (_DeadSocket,), {"attempts": []})
        self._real_ws = dc._websocket
        sock = self.sock
        dc._websocket = lambda: type("M", (), {"WebSocketApp": sock})
        self.addCleanup(self._restore)

    def _restore(self):
        dc._websocket = self._real_ws

    def _run_outage(self, seconds, host=None):
        host = host or _Host()
        host._deepgram_on_close(None, 1006, "connection lost")
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.05)
        host.is_listening = False
        host._stop_event.set()
        return host

    def test_a_failed_attempt_is_followed_by_another(self):
        """The whole defect: one failed attempt used to end transcription."""
        self._run_outage(4.0)
        self.assertGreaterEqual(
            len(self.sock.attempts),
            2,
            "reconnect stopped after the first failure -- the app stays dead "
            "for the rest of the session even once the network returns",
        )

    def test_backoff_grows_rather_than_spinning(self):
        """The loop runs until Stop, so a zero backoff would spin a core."""
        self._run_outage(8.0)
        gaps = [
            b - a for a, b in zip(self.sock.attempts, self.sock.attempts[1:])
        ]
        self.assertTrue(gaps, "expected at least two attempts to measure a gap")
        self.assertGreaterEqual(min(gaps), 0.9, f"attempts too close together: {gaps}")
        for earlier, later in zip(gaps, gaps[1:]):
            self.assertGreaterEqual(later, earlier - 0.2, f"backoff shrank: {gaps}")

    def test_reconnect_stops_when_listening_stops(self):
        host = _Host()
        host._deepgram_on_close(None, 1006, "connection lost")
        time.sleep(1.4)
        host.is_listening = False
        host._stop_event.set()
        settled = len(self.sock.attempts)
        time.sleep(2.5)
        self.assertEqual(
            len(self.sock.attempts),
            settled,
            "reconnect kept trying after Stop",
        )

    def test_a_zero_backoff_cannot_spin(self):
        host = _Host()
        host._dg_backoff_seconds = 0.0
        self._run_outage(4.0, host=host)
        self.assertLessEqual(
            len(self.sock.attempts),
            4,
            "a 0s backoff spun instead of waiting -- 0*2 is still 0",
        )


class GapMarkerReportsTheRealOutageTest(unittest.TestCase):
    """The marker used to be emitted before the socket was known to connect.

    Run ...142447 reported `gap_seconds: 2.6` for a 45-second outage, and reset
    the clock, so the real duration could never be recorded. It now fires from
    `_deepgram_on_open` -- once, when the connection is genuinely back.
    """

    def test_marking_is_wired_to_open_not_to_the_attempt(self):
        import inspect

        on_open = inspect.getsource(dc.DeepgramClientMixin._deepgram_on_open)
        self.assertIn("_mark_deepgram_gap_if_any", on_open)
        reconnect = inspect.getsource(dc.DeepgramClientMixin._reconnect_deepgram)
        self.assertNotIn(
            "self._mark_deepgram_gap_if_any()",
            reconnect,
            "marking from the reconnect attempt reports the time to the first "
            "try, not the real outage",
        )

    def test_first_connect_of_a_session_marks_nothing(self):
        host = _Host()
        host._dg_disconnected_at = 0.0
        self.assertIsNone(host._mark_deepgram_gap_if_any())
        self.assertEqual(host.gap_markers, [])

    def test_a_real_outage_is_marked_once_with_its_true_length(self):
        host = _Host()
        host._dg_disconnected_at = time.time() - 45.0
        gap = host._mark_deepgram_gap_if_any()
        self.assertIsNotNone(gap)
        self.assertGreaterEqual(gap, 44.0)
        self.assertEqual(len(host.gap_markers), 1)
        # Second call: the clock was cleared, so no duplicate marker.
        self.assertIsNone(host._mark_deepgram_gap_if_any())
        self.assertEqual(len(host.gap_markers), 1)


class ConnectionFailuresAreRetryableTest(unittest.TestCase):
    """A dropped network must not be a permanent translation failure."""

    def _classify(self, exc):
        from alpha.translation import deepl_client

        try:
            deepl_client.DeepLError  # noqa: B018 - import check
        except AttributeError:  # pragma: no cover
            self.skipTest("deepl_client unavailable")
        return deepl_client

    def test_connection_exception_is_retryable(self):
        mod = self._classify(None)
        src = __import__("inspect").getsource(mod)
        self.assertIn("ConnectionException", src)
        self.assertIn('code="connection_failed", retryable=True', src)

    def test_the_type_check_precedes_the_substring_rules(self):
        """`"auth" in low` is broad enough to swallow a connection error."""
        src = __import__("inspect").getsource(self._classify(None))
        conn = src.index("connection_failed")
        auth = src.index('code="auth_failed"')
        self.assertLess(
            conn,
            auth,
            "the connection check must run before the message-substring rules",
        )

    def test_quota_stays_permanent(self):
        """Retrying a quota failure would burn the remaining budget."""
        src = __import__("inspect").getsource(self._classify(None))
        self.assertIn('code="quota_exceeded", retryable=False', src)

    def test_auth_stays_permanent(self):
        src = __import__("inspect").getsource(self._classify(None))
        self.assertIn('code="auth_failed", retryable=False', src)


if __name__ == "__main__":
    unittest.main()
