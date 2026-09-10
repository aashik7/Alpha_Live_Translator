"""A failed device rebind must not seize the UI, and must not end detection.

The owner's requirement for this whole area, in his words: headphones plugged in
or unplugged mid-meeting, the app keeps working, it does not stop, it does not
break, and the user never has to press Stop and then Start again. Three of the
ways the shipped rebind fell short of that are pinned here.

WHAT WAS BROKEN
---------------
1. THE MODAL (breaks "it must not break").
   `_start_wasapi_loopback`'s failure path calls `_show_wasapi_error`, and that
   helper calls `messagebox.showerror` SYNCHRONOUSLY when it is already on the
   main thread. The rebind is marshalled to the Tk main thread, so a rebind that
   could not open the new device froze the entire mainloop -- transcript
   rendering, the UI queue pump, the update timer -- until somebody clicked OK,
   mid-meeting. Item 73 pinned exactly this decision in its own test: "A device
   change never opens a modal ... it must not seize the UI mid-meeting." Phase 5
   made that path reachable again by moving the rebind onto the main thread.

   The modal is NOT wrong at Start. There the user just pressed a button and is
   waiting for an answer. It is wrong for a device change, which is
   recoverable and unattended. So the fix distinguishes the two callers rather
   than deleting the dialog.

2. NO DETECTOR LEFT (breaks "no Stop/Start").
   The same failure path calls `_close_wasapi_stream()`, which clears
   `_wasapi_default_endpoint_baseline` and nulls the watch thread, and then
   re-raises. Nothing restarts the watcher, and the watcher is otherwise only
   started inside `_start_wasapi_loopback` behind a non-empty-baseline gate. So
   after one failed rebind the session had no device detection at all: if the
   user plugged the original device back in -- the obvious recovery -- nothing
   noticed, nothing retried, and system audio stayed dead until Stop/Start.
   That is the restart this feature exists to avoid.

3. THE GAP WAS NEVER MEASURED (breaks "same quantity").
   The rebind logged STARTED / COMPLETED / FAILED with no timing at all. Capture
   is closed and reopened inside that window and the mixer zero-fills the
   missing system audio, so the gap reaches Deepgram as silence. Nothing
   recorded how long it lasted, which makes it indistinguishable afterwards from
   a genuinely quiet room -- the item 80 audio-loss class all over again.

WHAT THESE TESTS PIN
--------------------
* a failed rebind opens no modal, and Start still does
* a failed rebind leaves a baseline and a running watcher, so the next device
  change is still detected and retried
* a failed rebind does not report success
* the capture gap is measured and carried on the evidence event
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

from alpha.audio import wasapi as wasapi_module  # noqa: E402
from alpha.audio.wasapi import WasapiCaptureMixin  # noqa: E402


class _StartHost:
    """Drives the REAL `_start_wasapi_loopback` failure path.

    Device acquisition raises, which is what a default endpoint that has gone
    away actually does, so the real `except` block runs -- including the call
    to `_show_wasapi_error`.
    """

    _start_wasapi_loopback = WasapiCaptureMixin._start_wasapi_loopback
    _show_wasapi_error = WasapiCaptureMixin._show_wasapi_error
    _wasapi_capture_stop_event = WasapiCaptureMixin._wasapi_capture_stop_event

    def __init__(self):
        self._wasapi_stream = None
        self._pyaudio = None
        self._wasapi_reader_thread = None
        self._wasapi_device_watch_thread = None
        self._wasapi_default_endpoint_baseline = ""
        self._wasapi_device_change_reported = False
        self._wasapi_rebind_in_progress = False
        self.after_calls = []

    def _get_wasapi_loopback_device(self):
        raise OSError("the default endpoint went away")

    def _close_wasapi_stream(self):
        self._wasapi_default_endpoint_baseline = ""
        self._wasapi_device_watch_thread = None

    def after(self, delay, callback):
        self.after_calls.append(callback)


class AFailedRebindOpensNoModalTest(unittest.TestCase):
    """`_show_wasapi_error` blocks the mainloop when already on it."""

    def setUp(self):
        # The real function imports pyaudio at the top; the failure happens
        # after that, in device acquisition.
        patcher = patch.object(wasapi_module, "_import_pyaudio", lambda: object())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_start_failure_still_tells_the_user(self):
        """Not a regression to smuggle in: at Start the user pressed a button
        and is waiting for an answer, so the dialog belongs there."""
        host = _StartHost()
        with patch("tkinter.messagebox.showerror") as modal:
            with self.assertRaises(OSError):
                host._start_wasapi_loopback()
        self.assertEqual(
            modal.call_count, 1, "Start no longer reports a capture failure"
        )

    def test_a_rebind_failure_does_not(self):
        host = _StartHost()
        with patch("tkinter.messagebox.showerror") as modal:
            with self.assertRaises(OSError):
                host._start_wasapi_loopback(show_error_dialog=False)
        modal.assert_not_called()

    def test_the_dialog_is_never_scheduled_either(self):
        """`_show_wasapi_error` falls back to `after(0, ...)` off the main
        thread, so suppression has to stop both branches, not just the
        synchronous one."""
        host = _StartHost()
        done = threading.Event()
        errors = []

        def run():
            try:
                host._start_wasapi_loopback(show_error_dialog=False)
            except OSError:
                pass
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(done.wait(timeout=5.0), "the start call never returned")
        self.assertEqual(errors, [])
        self.assertEqual(
            host.after_calls, [], "the dialog was marshalled instead of suppressed"
        )


class _RebindHost:
    """Drives the REAL rebind over stubbed close/start."""

    _rebind_wasapi_to_default_device = (
        WasapiCaptureMixin._rebind_wasapi_to_default_device
    )
    _refresh_connection_indicator = WasapiCaptureMixin._refresh_connection_indicator
    _wasapi_capture_stop_event = WasapiCaptureMixin._wasapi_capture_stop_event

    def __init__(self, *, start_fails=False, current_endpoint="NEW-ENDPOINT"):
        self._stop_event = threading.Event()
        self._audio_device_changed = True
        self._wasapi_device_change_reported = True
        self._wasapi_default_endpoint_baseline = ""
        self._wasapi_device_watch_thread = None
        self._diag_wasapi_device_name = "Speakers"
        self._start_fails = start_fails
        self._current_endpoint = current_endpoint
        self.calls = []

    # Looked up at call time rather than bound at class-definition time, so the
    # module still imports against a tree where the helper does not exist yet
    # and every test below reports its own pre-fix failure instead of the whole
    # file erroring out at collection.
    def _start_device_watch(self):
        impl = getattr(WasapiCaptureMixin, "_start_device_watch", None)
        if impl is None:
            raise AttributeError("_start_device_watch has not been written yet")
        return impl(self)

    def _read_default_endpoint_id(self):
        return self._current_endpoint

    def _wasapi_device_watch_worker(self, poll_seconds=2.0):
        # Returns immediately: a real SupervisedThread still starts and stops,
        # so the watcher restart is exercised for real without needing COM.
        return None

    def _close_wasapi_stream(self):
        self.calls.append("close")
        self._wasapi_default_endpoint_baseline = ""
        self._wasapi_device_watch_thread = None

    def _start_wasapi_loopback(self, show_error_dialog=True):
        self.calls.append(("start", show_error_dialog))
        if self._start_fails:
            raise OSError("no device")
        self._wasapi_default_endpoint_baseline = self._current_endpoint


class _LogCapture:
    def __init__(self):
        self.rows = []

    def __call__(self, event, **fields):
        self.rows.append((event, fields))

    def field(self, event, name):
        for logged, fields in self.rows:
            if logged == event:
                return fields.get(name)
        return None

    def events(self):
        return [e for e, _ in self.rows]


class _LogPatch:
    """`jp_accuracy_log` is imported inside the function, so patch the module."""

    def __init__(self, test):
        self.capture = _LogCapture()
        self._patcher = patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log", self.capture
        )
        self._patcher.start()
        test.addCleanup(self._patcher.stop)


class AFailedRebindKeepsDetectingTest(unittest.TestCase):
    def test_the_rebind_asks_the_start_to_stay_silent(self):
        host = _RebindHost()
        _LogPatch(self)
        host._rebind_wasapi_to_default_device()
        starts = [c for c in host.calls if isinstance(c, tuple) and c[0] == "start"]
        self.assertEqual(
            starts,
            [("start", False)],
            "the rebind let the start path open a modal on the UI thread",
        )

    def test_a_failed_rebind_leaves_a_baseline(self):
        host = _RebindHost(start_fails=True)
        _LogPatch(self)
        host._rebind_wasapi_to_default_device()
        self.assertEqual(
            host._wasapi_default_endpoint_baseline,
            "NEW-ENDPOINT",
            "no baseline survived the failure, so the watcher has nothing to "
            "compare against and the next device change is invisible",
        )

    def test_a_failed_rebind_leaves_a_running_watcher(self):
        host = _RebindHost(start_fails=True)
        _LogPatch(self)
        host._rebind_wasapi_to_default_device()
        watch = host._wasapi_device_watch_thread
        self.addCleanup(lambda: watch.stop(1.0) if watch is not None else None)
        self.assertIsNotNone(
            watch,
            "device detection ended with the failed rebind; plugging the "
            "original device back in would never be noticed and only "
            "Stop/Start would recover",
        )

    def test_a_failed_rebind_still_reports_failure(self):
        host = _RebindHost(start_fails=True)
        log = _LogPatch(self).capture
        self.assertFalse(host._rebind_wasapi_to_default_device())
        self.assertTrue(
            host._audio_device_changed, "a failed rebind cleared the warning"
        )
        self.assertIn("AUDIO_DEVICE_REBIND_FAILED", log.events())

    def test_an_unreadable_endpoint_does_not_fabricate_a_baseline(self):
        """"" is UNKNOWN. Baselining on it would make every later poll read as
        a change and queue a rebind per poll."""
        host = _RebindHost(start_fails=True, current_endpoint="")
        _LogPatch(self)
        host._rebind_wasapi_to_default_device()
        self.assertEqual(host._wasapi_default_endpoint_baseline, "")
        self.assertIsNone(
            host._wasapi_device_watch_thread,
            "a watcher was started with no baseline to compare against",
        )


class TheCaptureGapIsMeasuredTest(unittest.TestCase):
    """R6: the silent gap looks exactly like item 80 unless it is recorded."""

    def test_a_successful_rebind_reports_how_long_capture_was_down(self):
        host = _RebindHost()
        log = _LogPatch(self).capture
        host._rebind_wasapi_to_default_device()
        gap = log.field("AUDIO_DEVICE_REBIND_COMPLETED", "capture_gap_seconds")
        self.assertIsNotNone(
            gap,
            "the rebind recorded no gap, so lost far-end audio is "
            "indistinguishable from a quiet room afterwards; logged %r"
            % (log.rows,),
        )
        self.assertIsInstance(gap, float)
        self.assertGreaterEqual(gap, 0.0)

    def test_the_measurement_is_real_not_a_constant(self):
        class SlowHost(_RebindHost):
            def _close_wasapi_stream(self):
                time.sleep(0.15)
                super()._close_wasapi_stream()

        host = SlowHost()
        log = _LogPatch(self).capture
        host._rebind_wasapi_to_default_device()
        gap = log.field("AUDIO_DEVICE_REBIND_COMPLETED", "capture_gap_seconds")
        self.assertGreaterEqual(
            gap, 0.1, "the reported gap did not track a real 150 ms teardown"
        )

    def test_a_failed_rebind_reports_its_elapsed_time_too(self):
        host = _RebindHost(start_fails=True)
        log = _LogPatch(self).capture
        host._rebind_wasapi_to_default_device()
        elapsed = log.field("AUDIO_DEVICE_REBIND_FAILED", "seconds_until_failure")
        self.assertIsNotNone(
            elapsed,
            "a failed rebind recorded no timing; logged %r" % (log.rows,),
        )


if __name__ == "__main__":
    unittest.main()
