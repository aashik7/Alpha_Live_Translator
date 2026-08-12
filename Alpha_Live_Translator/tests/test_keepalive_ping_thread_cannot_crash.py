"""Regression test for live run ...20260812-150116 -- the keepalive's own crash.

Item 44's keepalive works: that run detected the drop, retried the reconnect 5
times with growing backoff, reconnected, and marked the gap at 30.4s for a 34s
outage. It also crashed the app while doing it.

Passing `ping_interval` makes websocket-client start a `_send_ping` thread. In
1.6.0 that thread reads `self.stop_ping.wait(...)` with no guard, and
`stop_ping` is `None` until `_start_ping_thread` assigns it. During the WiFi
drop that raced and raised

    AttributeError: 'NoneType' object has no attribute 'wait'

in `Thread-6 (_send_ping)`. It is a bare thread with no handler, so it reached
the app's thread excepthook: `CRASH_HOOK_TRIGGERED`,
`UNHANDLED_EXCEPTION_CAPTURED`, `LIVE_RUN_STATUS_UPDATED {'reason': 'crash'}`,
and a partial output written at 15:03:50 -- the app reporting itself crashed in
the middle of the outage it was supposed to be surviving.

There was no ping thread at all before the keepalive, so this failure arrived
with it. These tests drive the real subclass against the real base class.
"""

import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.deepgram_client import (  # noqa: E402
    _keepalive_websocket_app_class,
    _websocket,
)


def _app():
    cls = _keepalive_websocket_app_class()
    return cls("wss://example.invalid/v1/listen")


class PingThreadSurvivesTeardownTest(unittest.TestCase):
    def test_the_guarded_class_still_is_a_websocketapp(self):
        self.assertTrue(
            issubclass(_keepalive_websocket_app_class(), _websocket().WebSocketApp)
        )

    def test_send_ping_with_no_stop_event_does_not_raise(self):
        """The exact crash: stop_ping is None when the ping thread reads it."""
        app = _app()
        app.stop_ping = None
        app.ping_interval = 0.01
        app.keep_running = True
        try:
            app._send_ping()
        except AttributeError as exc:  # pragma: no cover - this is the defect
            self.fail(f"ping thread still crashes on teardown: {exc}")

    def test_a_real_attribute_error_is_still_raised(self):
        """The guard must not become a blanket except that hides real bugs."""
        app = _app()
        app.stop_ping = threading.Event()
        app.stop_ping.set()

        def boom(*_a, **_kw):
            raise AttributeError("something genuinely broken")

        app.stop_ping.wait = boom
        with self.assertRaises(AttributeError):
            app._send_ping()

    def test_normal_stop_still_returns_promptly(self):
        """A set stop event means "stop"; the thread must exit, not spin."""
        app = _app()
        app.stop_ping = threading.Event()
        app.stop_ping.set()
        app.ping_interval = 30
        app.keep_running = True
        done = threading.Event()

        def run():
            app._send_ping()
            done.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(done.wait(5), "ping thread did not exit on stop_ping.set()")

    def test_the_crash_is_reproducible_on_the_unguarded_base_class(self):
        """Pins that the guard is what fixes it, not something else."""
        base = _websocket().WebSocketApp("wss://example.invalid/v1/listen")
        base.stop_ping = None
        base.ping_interval = 0.01
        base.keep_running = True
        with self.assertRaises(AttributeError):
            base._send_ping()


class BothCallSitesUseTheGuardedClassTest(unittest.TestCase):
    """One un-guarded construction is one crash on the next disconnect."""

    def test_no_raw_websocketapp_construction_remains(self):
        import inspect

        from alpha.transcription import deepgram_client

        src = inspect.getsource(deepgram_client)
        body = src.split("def _keepalive_websocket_app_class", 1)[1]
        body = body.split("\n\n\n", 1)[-1]  # skip the factory's own reference
        self.assertNotIn(
            "_websocket().WebSocketApp(",
            body,
            "a WebSocketApp is still built without the ping-thread guard",
        )

    def test_connect_and_reconnect_both_construct_guarded(self):
        import inspect

        from alpha.transcription import deepgram_client

        src = inspect.getsource(deepgram_client)
        self.assertGreaterEqual(
            src.count("_keepalive_websocket_app_class()("),
            2,
            "expected the initial-connect and reconnect call sites",
        )


if __name__ == "__main__":
    unittest.main()
