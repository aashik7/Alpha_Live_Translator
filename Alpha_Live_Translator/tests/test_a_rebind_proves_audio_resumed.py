"""The last three device-continuity findings.

R14 -- THE REBIND RUNS INSIDE THE Tk MAINLOOP
---------------------------------------------
Phase 5 marshalled the rebind to the UI thread, and it had a real reason:
`_close_wasapi_stream` SKIPS its watcher join when called FROM the watcher
thread (`watch is not threading.current_thread()`) and then nulls the handle, so
a rebind driven inline from the watcher leaves the old watcher alive and
`_start_wasapi_loopback` spawns a second one.

But `_run_on_ui_thread` is `after(0, callback)`, and every `after` callback
occupies the mainloop until it returns. So the whole rebind -- up to 1.0 s of
reader join, up to 1.0 s of watcher join, `PyAudio.terminate()`, a fresh
`PyAudio()` that re-enumerates every host API on Windows, and `open()` -- ran on
the thread that also paints the transcript, pumps the UI queue and ticks the
update timer. Every device change froze the UI for that whole window.

A short-lived worker thread escapes both: it is not the watcher, so the join
runs; and it is not the mainloop, so nothing freezes.

Two things the move exposes, both fixed here:

* **Single-flight has to be taken atomically.** `_wasapi_rebind_in_progress` is
  set INSIDE the rebind, so two spawns could both pass the check and two
  teardowns could interleave.
* **A rebind in flight when the session stops would resurrect capture**, because
  it calls `_start_wasapi_loopback` after Stop. The UI-marshalled version has
  the same latent hazard -- an `after(0, ...)` can fire after Stop -- so this is
  a fix either way, not a new requirement.

R12 -- "THE DEVICE OPENED" IS NOT "AUDIO IS FLOWING"
---------------------------------------------------
The rebind cleared `_audio_device_changed` as soon as `_start_wasapi_loopback()`
returned. Opening a WASAPI loopback stream succeeds whether or not the endpoint
delivers anything -- that is precisely the item 73 condition: `is_active()` True,
zero bytes, nothing raised. So a rebind onto a silent endpoint took the warning
down and painted the indicator healthy over a capture path producing nothing.
Looks healthy, output wrong: this project's signature failure.

The rebind now waits, bounded, for the capture thread's own chunk counter to
move before declaring success. Waiting is only acceptable because of R14.

THE AVAILABILITY LATCHES
------------------------
`_sys_source_available` / `_mic_source_available` are set True on the first
chunk and cleared only in `__init__`/`reset()`, yet every frame's meta reports
them as availability. A source silent for minutes still reads available.

Those two fields are consumed by the source gate and the evidence writers, so
changing their MEANING is its own review. The fix is additive: new
`system_source_live` / `mic_source_live` keys derived from when each source last
delivered. What makes that meaningful rather than a silence detector is that a
quiet room still delivers chunks of ZEROS -- so "nothing for N seconds" really
does mean the device stopped producing.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio.timeline_mixer import DeepgramTimelineMixer  # noqa: E402
from alpha.audio.wasapi import WasapiCaptureMixin  # noqa: E402


class _RebindHost:
    """Drives the REAL rebind. Close/start are stubbed; threading is real."""

    _refresh_connection_indicator = WasapiCaptureMixin._refresh_connection_indicator
    _wasapi_capture_stop_event = WasapiCaptureMixin._wasapi_capture_stop_event
    _wasapi_stop_requested = WasapiCaptureMixin._wasapi_stop_requested
    _rebind_single_flight_lock = WasapiCaptureMixin._rebind_single_flight_lock
    _await_capture_confirmation = WasapiCaptureMixin._await_capture_confirmation
    # Core to the rebind's contract, not optional like the mic poll --
    # borrowed rather than guarded at the call site, so deleting or
    # renaming it fails loudly instead of silently skipping the
    # utterance boundary.
    _mark_device_swap_boundary = WasapiCaptureMixin._mark_device_swap_boundary

    def __init__(self, *, produces_audio=True, start_fails=False):
        self._stop_event = threading.Event()
        self._audio_device_changed = True
        self._wasapi_device_change_reported = True
        self._wasapi_default_endpoint_baseline = ""
        self._wasapi_device_watch_thread = None
        self._diag_wasapi_device_name = "Speakers"
        self._wasapi_chunks_captured = 0
        self._produces_audio = produces_audio
        self._start_fails = start_fails
        self.calls = []
        self.marshalled = []
        self.threads_used = []

    def _borrow(self, name):
        impl = getattr(WasapiCaptureMixin, name, None)
        if impl is None:
            raise AttributeError("%s has not been written yet" % name)
        return impl

    def _report_default_device_changed(self, baseline, current):
        return self._borrow("_report_default_device_changed")(self, baseline, current)

    def _rebind_wasapi_to_default_device(self):
        return self._borrow("_rebind_wasapi_to_default_device")(self)

    def _schedule_audio_rebind(self, fn):
        return self._borrow("_schedule_audio_rebind")(self, fn)

    def _start_device_watch(self):
        return False

    def _read_default_endpoint_id(self):
        return "NEW-ENDPOINT"

    def _run_on_ui_thread(self, fn):
        self.marshalled.append(fn)

    def _close_wasapi_stream(self):
        self.calls.append("close")
        self.threads_used.append(threading.current_thread())

    def _start_wasapi_loopback(self, show_error_dialog=True):
        self.calls.append("start")
        if self._start_fails:
            raise OSError("no device")
        if self._produces_audio:
            # The reader would bump this as chunks arrive.
            def produce():
                time.sleep(0.05)
                self._wasapi_chunks_captured += 1

            threading.Thread(target=produce, daemon=True).start()


class TheRebindLeavesTheMainloopAloneTest(unittest.TestCase):
    """R14."""

    def test_the_rebind_does_not_run_on_the_ui_thread(self):
        host = _RebindHost()
        host._report_default_device_changed("OLD", "NEW")
        # Anything marshalled must be UI PAINTING only, never the rebind.
        names = [getattr(fn, "__name__", "") for fn in host.marshalled]
        self.assertNotIn(
            "_rebind_wasapi_to_default_device",
            names,
            "the rebind is still handed to the Tk mainloop, which it occupies "
            "for the whole PortAudio teardown and re-enumeration",
        )

    def test_the_rebind_still_happens(self):
        host = _RebindHost()
        host._report_default_device_changed("OLD", "NEW")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and "start" not in host.calls:
            time.sleep(0.02)
        self.assertEqual(host.calls[:2], ["close", "start"])

    def test_it_runs_off_both_the_caller_and_the_mainloop(self):
        host = _RebindHost()
        caller = threading.current_thread()
        host._report_default_device_changed("OLD", "NEW")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not host.threads_used:
            time.sleep(0.02)
        self.assertTrue(host.threads_used, "the rebind never ran")
        self.assertIsNot(
            host.threads_used[0],
            caller,
            "the rebind ran on the calling (watch) thread, where "
            "`_close_wasapi_stream` skips its own watcher join",
        )

    def test_a_rebind_after_stop_does_not_resurrect_capture(self):
        """A worker in flight at Stop must not reopen the device."""
        host = _RebindHost()
        host._stop_event.set()
        host._rebind_wasapi_to_default_device()
        self.assertEqual(
            host.calls, [], "a rebind reopened capture after the session stopped"
        )

    def test_two_simultaneous_rebinds_tear_down_once(self):
        host = _RebindHost()
        barrier = threading.Barrier(2, timeout=5.0)

        def go():
            barrier.wait()
            host._rebind_wasapi_to_default_device()

        threads = [threading.Thread(target=go, daemon=True) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        self.assertEqual(
            host.calls.count("close"),
            1,
            "single-flight is not atomic; two teardowns interleaved",
        )


class TheRebindProvesAudioResumedTest(unittest.TestCase):
    """R12."""

    def test_a_device_that_yields_audio_clears_the_warning(self):
        host = _RebindHost(produces_audio=True)
        self.assertTrue(host._rebind_wasapi_to_default_device())
        self.assertFalse(host._audio_device_changed)

    def test_a_device_that_opens_but_stays_silent_keeps_the_warning_up(self):
        """The item 73 condition exactly: opens fine, delivers nothing."""
        host = _RebindHost(produces_audio=False)
        result = host._rebind_wasapi_to_default_device()
        self.assertFalse(
            result, "a silent endpoint was reported as a successful rebind"
        )
        self.assertTrue(
            host._audio_device_changed,
            "the warning came down over a capture path producing nothing -- "
            "the indicator would read healthy while no audio is captured",
        )

    def test_a_failed_open_still_keeps_the_warning_up(self):
        host = _RebindHost(start_fails=True)
        self.assertFalse(host._rebind_wasapi_to_default_device())
        self.assertTrue(host._audio_device_changed)


class TheMixerReportsLivenessTest(unittest.TestCase):
    def setUp(self):
        self.mixer = DeepgramTimelineMixer()
        self.mixer.configure_sources(2, 48000, mic_available=True)

    def _meta(self):
        _, meta = self.mixer._build_frame()
        return meta

    def test_a_source_that_just_delivered_is_live(self):
        self.mixer.push_system(np.zeros(960, dtype=np.int16).tobytes(), 2, 48000)
        self.assertTrue(self._meta()["system_source_live"])

    def test_a_source_that_never_delivered_is_not_live(self):
        self.assertFalse(self._meta()["system_source_live"])
        self.assertFalse(self._meta()["mic_source_live"])

    def test_liveness_goes_false_once_the_source_stops(self):
        """The whole point: `system_source_available` stays True forever, and a
        reader of the evidence needs to be able to tell that apart from a
        source that is actually still delivering."""
        from alpha.config import SOURCE_LIVENESS_WINDOW_S

        self.mixer.push_system(np.zeros(960, dtype=np.int16).tobytes(), 2, 48000)
        self.mixer._sys_last_chunk_mono = (
            time.monotonic() - SOURCE_LIVENESS_WINDOW_S - 1.0
        )
        meta = self._meta()
        self.assertTrue(
            meta["system_source_available"],
            "the existing latched field must keep its meaning -- the source "
            "gate and the evidence writers both consume it",
        )
        self.assertFalse(
            meta["system_source_live"],
            "a source silent for longer than the window still reads live",
        )

    def test_a_quiet_room_is_still_live(self):
        """Silence arrives as chunks of ZEROS, so this is a device-stopped
        signal and not a silence detector."""
        for _ in range(3):
            self.mixer.push_mic(np.zeros(320, dtype=np.int16).tobytes())
        self.assertTrue(self._meta()["mic_source_live"])

    def test_reset_clears_liveness_too(self):
        self.mixer.push_system(np.zeros(960, dtype=np.int16).tobytes(), 2, 48000)
        self.mixer.reset()
        self.assertFalse(self._meta()["system_source_live"])


if __name__ == "__main__":
    unittest.main()
