"""When Windows moves the default output, capture must follow it.

WHAT WAS BROKEN
---------------
Capture binds to one PortAudio device index at Start and never follows. The app
already detects the move -- a 2 s watcher polls the OS over COM, outside
PortAudio, because `Pa_Initialize()` snapshots the device list and
`get_default_wasapi_loopback()` therefore returns the start-of-session default
forever. But on a confirmed change `_report_default_device_changed` did exactly
two things: wrote one accuracy-log row and raised a UI signal. It never closed,
reopened or re-indexed the stream.

After the switch the old endpoint yields zero frames, so the reader falls into
its idle branch, prints "No loopback audio yet" every few seconds, raises
nothing and enqueues nothing. The microphone is a separate `sounddevice`
stream on a capture endpoint and is unaffected, so the session keeps
transcribing the local mic while all far-end audio stops -- half-alive rather
than dead. Recovery was manual only: make the original device default again, or
Stop and Start and lose continuity mid-meeting.

THE HAZARD THAT SHAPES THE FIX
------------------------------
The obvious implementation -- set `self._stop_event`, close, clear it, start
again -- is wrong and would be far worse than the bug. `self._stop_event` is
the SESSION-wide stop: created once in `main_window`, cleared at Start, set at
Stop, and read in 28 places including the microphone, the audio mixer worker
and the Deepgram sender and reconnect loops. Setting it would stop the whole
session, and clearing it would bring none of those threads back.

So the WASAPI capture gets its own stop event. The reader and the watcher exit
on EITHER that or the session one, which lets capture be torn down and
restarted on its own.

Two more, both already visible in the existing code:

* `_close_wasapi_stream` skips its watcher join when called FROM the watcher
  thread (`watch is not threading.current_thread()`), then nulls the handle --
  so a rebind driven from the watcher would leave the old watcher alive and
  `_start_wasapi_loopback` would spawn a second one. The rebind is marshalled
  to the UI thread.
* A flapping device must not start a rebind storm.

WHAT THESE TESTS PIN
--------------------
* a confirmed device change schedules a rebind, and does not perform one inline
* the rebind never touches the session-wide stop event
* it closes and reopens, in that order
* one rebind at a time, and a cooldown between them
* a failed rebind leaves the warning up rather than reporting success
"""

import ast
import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio.wasapi import WasapiCaptureMixin  # noqa: E402

WASAPI_SRC = PROJECT_ROOT / "alpha" / "audio" / "wasapi.py"


class Host:
    _report_default_device_changed = WasapiCaptureMixin._report_default_device_changed
    _rebind_wasapi_to_default_device = (
        WasapiCaptureMixin._rebind_wasapi_to_default_device
    )
    _refresh_connection_indicator = WasapiCaptureMixin._refresh_connection_indicator

    def __init__(self, *, start_fails=False):
        self._stop_event = threading.Event()
        self._wasapi_stop_event = threading.Event()
        self._audio_device_changed = False
        self._wasapi_device_change_reported = False
        self._wasapi_default_endpoint_baseline = "OLD-ENDPOINT"
        self._diag_wasapi_device_name = "Speakers"
        self.calls = []
        self.marshalled = []
        self._start_fails = start_fails

    # -- what the mixin calls into ---------------------------------------
    def _run_on_ui_thread(self, fn):
        self.marshalled.append(fn)

    def _read_default_endpoint_id(self):
        # The failure path re-baselines from this so device detection survives
        # a failed rebind. "" is UNKNOWN, which correctly starts no watcher --
        # that behaviour has its own tests in
        # `test_a_failed_rebind_recovers_and_is_measured.py`; these tests are
        # about the rebind itself.
        return ""

    def _close_wasapi_stream(self):
        self.calls.append("close")

    def _start_wasapi_loopback(self, show_error_dialog=True):
        # Signature mirrors the real one. The rebind passes
        # `show_error_dialog=False` so a failure cannot open a modal on the Tk
        # main thread it runs on; a stub without the keyword makes the rebind
        # raise TypeError, which its broad `except` then reports as an ordinary
        # device-rebind failure -- green-looking teardown over a signature bug.
        self.calls.append("start")
        self.show_error_dialog_seen = show_error_dialog
        if self._start_fails:
            raise RuntimeError("no device")

    def run_marshalled(self):
        for fn in list(self.marshalled):
            fn()
        self.marshalled.clear()


class ADeviceChangeSchedulesARebindTest(unittest.TestCase):
    def test_the_change_does_not_rebind_inline(self):
        """Inline would run on the watcher thread, where the close skips its
        own watcher join and the restart then spawns a second watcher."""
        host = Host()
        host._report_default_device_changed("OLD-ENDPOINT", "NEW-ENDPOINT")
        self.assertEqual(
            host.calls, [], "the rebind ran on the caller's thread"
        )
        # `_refresh_connection_indicator` marshals too, so count the rebind
        # specifically rather than the queue length.
        scheduled = [
            getattr(fn, "__name__", "") for fn in host.marshalled
        ]
        self.assertIn(
            "_rebind_wasapi_to_default_device",
            scheduled,
            "no rebind was scheduled for the UI thread; queued %r" % (scheduled,),
        )

    def test_the_scheduled_rebind_closes_then_starts(self):
        host = Host()
        host._report_default_device_changed("OLD-ENDPOINT", "NEW-ENDPOINT")
        host.run_marshalled()
        self.assertEqual(host.calls, ["close", "start"])

    def test_the_session_stop_event_is_never_touched(self):
        """Setting it would stop the mic, the mixer and the Deepgram sender,
        and clearing it would bring none of them back."""
        host = Host()
        host._report_default_device_changed("OLD-ENDPOINT", "NEW-ENDPOINT")
        host.run_marshalled()
        self.assertFalse(
            host._stop_event.is_set(),
            "the rebind set the SESSION-wide stop event",
        )

    def test_a_successful_rebind_clears_the_warning(self):
        host = Host()
        host._report_default_device_changed("OLD-ENDPOINT", "NEW-ENDPOINT")
        self.assertTrue(host._audio_device_changed, "the warning never went up")
        host.run_marshalled()
        self.assertFalse(
            host._audio_device_changed,
            "the warning stayed up after capture successfully followed",
        )

    def test_a_failed_rebind_leaves_the_warning_up(self):
        host = Host(start_fails=True)
        host._report_default_device_changed("OLD-ENDPOINT", "NEW-ENDPOINT")
        host.run_marshalled()
        self.assertTrue(
            host._audio_device_changed,
            "a rebind that could not open a device reported success",
        )

    def test_only_one_rebind_runs_at_a_time(self):
        """A flapping device must not start a storm."""
        host = Host()
        for _ in range(5):
            host._report_default_device_changed("OLD-ENDPOINT", "NEW-ENDPOINT")
        host.run_marshalled()
        self.assertEqual(
            host.calls.count("start"),
            1,
            "a flapping device queued %d rebinds" % host.calls.count("start"),
        )


class TheWatcherWakesInsideTheJoinWindowTest(unittest.TestCase):
    """The second-watcher hazard, pinned at the real timings.

    `_close_wasapi_stream` joins the watcher for 1.0 s and then nulls the
    handle whether or not the join succeeded. A rebind leaves the SESSION stop
    event clear, so a watcher parked in `self._stop_event.wait(2.0)` cannot
    wake inside that window: the join times out, the handle is dropped, and
    `_start_wasapi_loopback` -- which clears the capture event first -- spawns
    a second watcher beside the still-sleeping first one. Two watchers both
    report the next device change, and each schedules its own rebind.

    Driven at the production numbers (2.0 s poll, 1.0 s join) on purpose: at a
    10 ms poll the bug is invisible.
    """

    def setUp(self):
        from alpha.audio import default_endpoint

        self.default_endpoint = default_endpoint
        self._saved = (
            default_endpoint.com_initialize_mta,
            default_endpoint.com_uninitialize,
        )
        default_endpoint.com_initialize_mta = lambda: True
        default_endpoint.com_uninitialize = lambda: None

    def tearDown(self):
        (
            self.default_endpoint.com_initialize_mta,
            self.default_endpoint.com_uninitialize,
        ) = self._saved

    def _watch_host(self):
        class WatchHost(WasapiCaptureMixin):
            def __init__(self):
                self._stop_event = threading.Event()
                self._wasapi_default_endpoint_baseline = "BASE"
                self._wasapi_device_change_reported = False

            def _read_default_endpoint_id(self):
                return "BASE"

        return WatchHost()

    def test_the_capture_event_alone_ends_the_watcher(self):
        host = self._watch_host()
        thread = threading.Thread(
            target=host._wasapi_device_watch_worker,
            kwargs={"poll_seconds": 2.0},
            daemon=True,
        )
        thread.start()
        time.sleep(0.1)
        host._wasapi_capture_stop_event().set()
        thread.join(timeout=1.0)
        self.assertFalse(
            thread.is_alive(),
            "the watcher did not wake inside the 1 s join, so the rebind would "
            "drop its handle and start a second watcher",
        )
        self.assertFalse(
            host._stop_event.is_set(),
            "ending the watcher stopped the whole session",
        )

    def test_the_session_event_still_ends_the_watcher(self):
        host = self._watch_host()
        thread = threading.Thread(
            target=host._wasapi_device_watch_worker,
            kwargs={"poll_seconds": 0.01},
            daemon=True,
        )
        thread.start()
        time.sleep(0.05)
        host._stop_event.set()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive(), "Stop no longer ends the watcher")


class TheStopEventIsNotSharedTest(unittest.TestCase):
    """Source-level, walked with the AST: the module quotes the session event
    in prose explaining why it must not be used."""

    def _fn(self, name):
        tree = ast.parse(WASAPI_SRC.read_text(encoding="utf-8"))
        return next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == name
        )

    def test_the_rebind_never_sets_the_session_event(self):
        fn = self._fn("_rebind_wasapi_to_default_device")
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            if getattr(n.func, "attr", "") not in ("set", "clear"):
                continue
            recv = ast.unparse(n.func.value)
            self.assertNotIn(
                "_stop_event",
                recv.replace("_wasapi_stop_event", ""),
                "the rebind touches the session-wide stop event at line %d"
                % n.lineno,
            )

    def test_the_reader_and_watcher_honour_the_capture_event(self):
        for name in ("_wasapi_reader_worker", "_wasapi_device_watch_worker"):
            with self.subTest(fn=name):
                body = ast.unparse(self._fn(name))
                # Either the event directly or the helper that consults both.
                self.assertTrue(
                    "_wasapi_stop_event" in body or "_wasapi_stop_requested" in body,
                    "%s cannot be stopped without stopping the whole session"
                    % name,
                )


if __name__ == "__main__":
    unittest.main()
