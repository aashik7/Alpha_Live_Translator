"""Regression test for item 44 (reopened) — websocket keepalive.

Found by a live network-drop test on 2026-08-12, not by reading code. Item 44
originally shipped only a gap marker, on the assumption that the reconnect
machinery ("fix 5" — backoff, replay buffer, single-flight lock) already
worked. It does not, and never did: run `...20260812-095935` shows the WiFi
dropped at 10:20:54 and **zero** reconnect or close events followed, while 411
audio chunks were captured and discarded and the transcript stayed frozen until
Stop. The run ended `final_status: failed`.

Cause: `websocket-client`'s `run_forever()` was called with no `ping_interval`.
A WiFi drop kills the TCP connection without sending a FIN, so the socket goes
silent rather than closed, `run_forever()` blocks indefinitely waiting for data
that never arrives, `_deepgram_on_close` never fires, and `_schedule_reconnect`
is therefore never called. Every part of the reconnect chain was unreachable in
the one scenario it exists for.

These are source-level assertions rather than a live socket test: opening a real
websocket is not possible in this suite, and the defect is precisely that a
parameter was absent from the call. Asserting on the call site is what actually
catches a regression here.
"""

import inspect
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import (  # noqa: E402
    DG_WS_PING_INTERVAL_S,
    DG_WS_PING_TIMEOUT_S,
)
from alpha.transcription import deepgram_client  # noqa: E402

_SOURCE = inspect.getsource(deepgram_client)


class KeepaliveIsConfiguredTest(unittest.TestCase):
    def test_every_run_forever_call_sets_a_ping_interval(self):
        """The whole defect: one un-pinged call is one silent freeze."""
        calls = re.findall(r"run_forever\((.*?)\)", _SOURCE, re.S)
        self.assertGreaterEqual(len(calls), 2, "expected the connect and reconnect call sites")
        for args in calls:
            self.assertIn(
                "ping_interval",
                args,
                "a run_forever() call has no ping_interval -- a dropped WiFi "
                "connection there will never be detected and the reconnect "
                "chain will never run",
            )
            self.assertIn("ping_timeout", args)

    def test_ping_timeout_is_shorter_than_the_interval(self):
        """websocket-client rejects timeout >= interval at runtime, which would
        turn this fix into a hard failure on every connect."""
        self.assertLess(float(DG_WS_PING_TIMEOUT_S), float(DG_WS_PING_INTERVAL_S))

    def test_detection_is_fast_enough_to_beat_a_short_drop(self):
        """The reported outage was 36s. Interval + timeout is the worst-case
        detection delay; it has to leave room for backoff and replay inside a
        drop that short, or the user still sees a freeze."""
        worst_case = float(DG_WS_PING_INTERVAL_S) + float(DG_WS_PING_TIMEOUT_S)
        self.assertLessEqual(
            worst_case, 20.0, f"worst-case detection {worst_case}s is too slow"
        )

    def test_values_are_positive(self):
        self.assertGreater(float(DG_WS_PING_INTERVAL_S), 0)
        self.assertGreater(float(DG_WS_PING_TIMEOUT_S), 0)


class GapMarkerStillWiredTest(unittest.TestCase):
    """The marker built earlier for item 44 only has value once a close is
    actually detected -- keep them tied together."""

    def test_reconnect_marks_the_gap(self):
        self.assertIn("_mark_deepgram_gap_if_any", _SOURCE)

    def test_close_handler_starts_the_gap_clock(self):
        self.assertIn("_dg_disconnected_at", _SOURCE)


if __name__ == "__main__":
    unittest.main()
