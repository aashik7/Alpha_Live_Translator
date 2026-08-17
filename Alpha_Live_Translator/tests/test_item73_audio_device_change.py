"""Regression tests for CLIENT_DELIVERY_SPRINT_v5.md item 73.

`alpha/audio/wasapi.py` binds the capture stream to ONE device index at
Start and PortAudio has no follow-the-default behaviour. If the user
switches the Windows default output mid-meeting -- headset, dock,
Bluetooth, all routine on a laptop -- everything else follows the new
device while capture stays on the old one. Measured behaviour of that
state: the stream delivers **0 bytes**, `is_active()` stays **True**, and
nothing raises. The reader loop only breaks on `not is_active()` or an
exception, so neither fires and the sole existing signal is a `print()`
every ~5 seconds.

Three design decisions are pinned here because each was reached by
measurement and would otherwise look arbitrary:

1. **The identifier is the MMDevice endpoint ID**, read outside PortAudio.
   PortAudio cannot answer the question at all -- `Pa_Initialize()`
   snapshots the device list, so `get_default_wasapi_loopback()` returns
   the start-of-session default forever (a second `PyAudio()` while the
   first is alive returns in 0.048 ms with an identical index). Index is
   not identity either: it is a dense concatenation across host APIs, so
   plugging in a headset shifts every WASAPI index. Nor is name: two
   distinct render endpoints on the dev machine compose the identical
   friendly name.

2. **Unknown is not "changed".** A failed read returns `""` and must be
   treated as no evidence. Warning on a COM hiccup teaches the operator to
   ignore the warning.

3. **The baseline never moves.** It is the endpoint the stream is BOUND
   to. The first draft re-baselined onto each new default, which reported
   the user switching BACK -- a recovery -- as a second fault.

Not covered by any test here, and honestly untestable without hardware:
that a real default-device switch changes the endpoint ID (certain by API
contract, never observed), and that the stream really starves rather than
erroring afterwards. That inference is the single assumption the item
rests on.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio.wasapi import WasapiCaptureMixin  # noqa: E402


class _WatchHost(WasapiCaptureMixin):
    """Drives the real watch loop with a scripted endpoint sequence."""

    def __init__(self, ids, tail="BASE"):
        self._stop_event = threading.Event()
        self._wasapi_default_endpoint_baseline = "BASE"
        self._wasapi_device_change_reported = False
        self._ids = list(ids)
        self._tail = tail
        self.events = []

    def _read_default_endpoint_id(self):
        return self._ids.pop(0) if self._ids else self._tail

    def _report_default_device_changed(self, baseline, current):
        self.events.append(("CHANGED", baseline, current))

    def _report_default_device_restored(self):
        self.events.append(("RESTORED",))


def _run_watch(ids, tail="BASE", settle=0.30):
    host = _WatchHost(ids, tail=tail)
    thread = threading.Thread(
        target=host._wasapi_device_watch_worker,
        kwargs={"poll_seconds": 0.01},
        daemon=True,
    )
    thread.start()
    time.sleep(settle)
    host._stop_event.set()
    thread.join(timeout=2.0)
    return host.events


class TestWatchLoopDecisions(unittest.TestCase):
    def test_no_change_reports_nothing(self):
        self.assertEqual(_run_watch(["BASE"] * 20), [])

    def test_a_real_change_reports_once_not_every_poll(self):
        events = _run_watch(["NEW"] * 20, tail="NEW")
        self.assertEqual(events, [("CHANGED", "BASE", "NEW")])

    def test_switching_back_is_a_recovery_not_a_second_fault(self):
        events = _run_watch(["NEW", "NEW", "NEW", "BASE", "BASE"])
        self.assertEqual(events, [("CHANGED", "BASE", "NEW"), ("RESTORED",)])

    def test_a_single_poll_blip_is_debounced_away(self):
        # Requires two CONSECUTIVE disagreeing reads: sleep/resume and driver
        # re-enumeration can momentarily report a different endpoint.
        self.assertEqual(_run_watch(["NEW", "BASE"] * 10), [])

    def test_an_unreadable_endpoint_is_unknown_not_changed(self):
        self.assertEqual(_run_watch([""] * 20, tail=""), [])

    def test_no_baseline_means_no_comparison(self):
        host = _WatchHost(["NEW"] * 20, tail="NEW")
        host._wasapi_default_endpoint_baseline = ""
        thread = threading.Thread(
            target=host._wasapi_device_watch_worker,
            kwargs={"poll_seconds": 0.01},
            daemon=True,
        )
        thread.start()
        time.sleep(0.2)
        host._stop_event.set()
        thread.join(timeout=2.0)
        self.assertEqual(host.events, [])

    def test_the_loop_exits_when_the_session_stops(self):
        host = _WatchHost(["BASE"] * 200)
        thread = threading.Thread(
            target=host._wasapi_device_watch_worker,
            kwargs={"poll_seconds": 0.01},
            daemon=True,
        )
        thread.start()
        host._stop_event.set()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive(), "watch thread must not outlive Stop")


class _LifecycleHost(WasapiCaptureMixin):
    """Real _close_wasapi_stream over stubbed stream/PyAudio handles."""

    def __init__(self):
        self._wasapi_stream = None
        self._pyaudio = None
        self._wasapi_reader_thread = None
        self._wasapi_device_watch_thread = None
        self._wasapi_default_endpoint_baseline = "SESSION-1-DEVICE"
        self._wasapi_device_change_reported = True


class TestSessionStateIsResetWhereTheSessionEnds(unittest.TestCase):
    """`_wasapi_rate` and `_diag_wasapi_device_name` are set per session and
    cleared nowhere, so a second Start whose device acquisition raises keeps
    the first session's values. Item 73's state must not join them."""

    def test_close_clears_the_baseline_and_the_latch(self):
        host = _LifecycleHost()
        WasapiCaptureMixin._close_wasapi_stream(host)
        self.assertEqual(host._wasapi_default_endpoint_baseline, "")
        self.assertFalse(host._wasapi_device_change_reported)
        self.assertIsNone(host._wasapi_device_watch_thread)

    def test_close_is_idempotent(self):
        # It runs from main_window, stop_finalize_worker AND the failure path
        # inside _start_wasapi_loopback, so twice per session is normal.
        host = _LifecycleHost()
        WasapiCaptureMixin._close_wasapi_stream(host)
        WasapiCaptureMixin._close_wasapi_stream(host)
        self.assertEqual(host._wasapi_default_endpoint_baseline, "")

    def test_a_second_session_cannot_inherit_the_first_devices_baseline(self):
        host = _LifecycleHost()
        WasapiCaptureMixin._close_wasapi_stream(host)
        # Session 2 fails to acquire a device, so nothing re-sets the
        # baseline. It must stay empty rather than still naming session 1's
        # device, or the watcher would compare against a device this session
        # never captured from.
        self.assertEqual(host._wasapi_default_endpoint_baseline, "")


class TestSurfacingChoices(unittest.TestCase):
    def test_a_device_change_never_opens_a_modal(self):
        """A modal blocks the Tk mainloop, and the only other mid-session
        modal in this app stops the session right after. A device change is
        recoverable -- the user can switch back -- so it must not seize the
        UI mid-meeting."""
        marshalled = []

        class Host(WasapiCaptureMixin):
            signal_label = None

            def _run_on_ui_thread(self, callback):
                marshalled.append(callback)

        with patch("tkinter.messagebox.showerror") as modal:
            Host()._report_default_device_changed("BASE", "NEW")
        modal.assert_not_called()
        self.assertEqual(len(marshalled), 1, "the UI update must be marshalled")

    def test_the_change_is_logged_with_both_endpoint_ids(self):
        logged = []

        class Host(WasapiCaptureMixin):
            signal_label = None

            def _run_on_ui_thread(self, callback):
                pass

        with patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log",
            side_effect=lambda event, **kw: logged.append((event, kw)),
        ):
            Host()._report_default_device_changed("BASE", "NEW")

        events = [e for e, _ in logged]
        self.assertIn("AUDIO_OUTPUT_DEVICE_CHANGED", events)
        payload = dict(logged[events.index("AUDIO_OUTPUT_DEVICE_CHANGED")][1])
        self.assertEqual(payload.get("baseline_endpoint_id"), "BASE")
        self.assertEqual(payload.get("current_endpoint_id"), "NEW")

    def test_reporting_survives_a_missing_ui(self):
        # The watchdog must never become the failure it exists to report.
        class Host(WasapiCaptureMixin):
            pass

        Host()._report_default_device_changed("BASE", "NEW")
        Host()._report_default_device_restored()


class TestEndpointReaderFailsSoft(unittest.TestCase):
    def test_a_broken_com_layer_returns_unknown_rather_than_raising(self):
        from alpha.audio import default_endpoint

        # A plain MagicMock here would be VACUOUS -- `ctypes.oledll.ole32.X()`
        # on a mock returns another mock instead of raising, so the function
        # would return "" via the NULL-pointer path and the except branch
        # would never run. This raises on attribute access, which is what a
        # missing/blocked ole32 actually does.
        class _Boom:
            def __getattr__(self, name):
                raise OSError("ole32 unavailable")

        with patch.object(default_endpoint.ctypes, "oledll", _Boom(), create=True):
            self.assertEqual(default_endpoint.read_default_render_endpoint_id(), "")

    def test_com_initialize_reports_failure_rather_than_raising(self):
        from alpha.audio import default_endpoint

        class _Boom:
            def __getattr__(self, name):
                raise OSError("no ole32")

        with patch.object(default_endpoint.ctypes, "windll", _Boom(), create=True):
            self.assertFalse(default_endpoint.com_initialize_mta())
            default_endpoint.com_uninitialize()  # must not raise


if __name__ == "__main__":
    unittest.main()
