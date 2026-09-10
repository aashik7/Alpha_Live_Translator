"""Unplug the headset and the operator's own voice must keep being captured.

WHAT WAS BROKEN
---------------
Phase 5 taught SYSTEM audio to follow the default output device. The microphone
was never taught anything: `_start_microphone_capture` reads
`sd.default.device[0]` once at session start (`microphone.py:54`) and nothing
ever rebinds it, and `default_endpoint.py` only ever read the default RENDER
endpoint (`_E_RENDER`), so a default INPUT change was not even detected.

Plugging in or unplugging a headset changes BOTH Windows defaults. So the
shipped behaviour on the single most ordinary mid-meeting event was: system
audio follows the headset, the microphone stays bound to a device that may no
longer exist, `mic_available` still reads True, and the operator's own half of
the conversation stops reaching the transcript for the rest of the session. Half
alive, no warning, and only Stop/Start recovered it -- the exact restart this
whole area exists to avoid.

WHAT THE FIX RESTS ON
---------------------
`sd._terminate()` + `sd._initialize()` re-enumerates PortAudio, measured at
**22.2 ms** on the development machine -- cheap enough to run on the UI thread,
unlike the WASAPI rebind. Only `microphone.py` uses sounddevice, so that
re-init touches nothing else; PyAudio/pyaudiowpatch is a separate library with
its own PortAudio and its stream is unaffected.

The one step that cannot be proven without hardware is the same one item 73
already rests on: that PortAudio reports the NEW default only after a re-init.
Measured for the render side (a second PyAudio while the first is alive returns
in 0.048 ms with an identical index); inferred, not measured, for capture.

THE MIC IS LEGITIMATELY ABSENT SOMETIMES
----------------------------------------
Two supported cases, and a rebind must respect both rather than "helpfully"
starting a microphone: the operator turned the mic off with the UI switch
(`_microphone_capture_enabled`), and the system-audio-only benchmark mode. A
mic that was never running must never be started by a device change.

WHAT THESE TESTS PIN
--------------------
* the default CAPTURE endpoint is readable, and fails soft exactly like render
* one debounce/latch state machine serves both device flavours
* an input-device change is detected, debounced, and reported once
* the rebind closes, re-initialises PortAudio, then reopens -- in that order
* it never starts a mic the operator disabled or never had
* a failed mic rebind leaves the session running and says so
* the session-wide stop event is never touched
"""

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio import default_endpoint  # noqa: E402
from alpha.audio.microphone import MicrophoneCaptureMixin  # noqa: E402
from alpha.audio.wasapi import WasapiCaptureMixin  # noqa: E402

MIC_OLD, MIC_NEW = "MIC-OLD-ENDPOINT", "MIC-NEW-ENDPOINT"


class TheCaptureEndpointIsReadableTest(unittest.TestCase):
    def test_the_reader_exists(self):
        self.assertTrue(
            hasattr(default_endpoint, "read_default_capture_endpoint_id"),
            "the default INPUT endpoint is never read, so a headset change is "
            "not even detected on the microphone side",
        )

    def test_render_and_capture_ask_for_different_dataflows(self):
        """Both must not quietly resolve to the same device."""
        self.assertNotEqual(
            getattr(default_endpoint, "_E_RENDER", 0),
            getattr(default_endpoint, "_E_CAPTURE", 0),
            "capture is reading the render dataflow, so it would report the "
            "speakers as the microphone",
        )

    def test_a_broken_com_layer_returns_unknown_rather_than_raising(self):
        """Same contract as render: "" is UNKNOWN, never "changed"."""

        class _Boom:
            def __getattr__(self, name):
                raise OSError("ole32 unavailable")

        with patch.object(default_endpoint.ctypes, "oledll", _Boom(), create=True):
            self.assertEqual(
                default_endpoint.read_default_capture_endpoint_id(), ""
            )

    def test_it_is_exported(self):
        self.assertIn("read_default_capture_endpoint_id", default_endpoint.__all__)


class OneStateMachineServesBothDevicesTest(unittest.TestCase):
    """The debounce and the report-once latch, extracted rather than copied.

    Written against the helper directly because the same three decisions --
    two consecutive reads before reporting, "" is unknown, going back is a
    recovery not a second fault -- now have to hold for two device flavours,
    and a second hand-copied implementation is how they drift apart.
    """

    def _evaluate(self, **kwargs):
        from alpha.audio.wasapi import evaluate_endpoint_change

        return evaluate_endpoint_change(**kwargs)

    def test_no_change_does_nothing(self):
        pending, action = self._evaluate(
            baseline="A", current="A", pending="", reported=False
        )
        self.assertEqual((pending, action), ("", None))

    def test_a_single_blip_is_debounced_away(self):
        pending, action = self._evaluate(
            baseline="A", current="B", pending="", reported=False
        )
        self.assertEqual((pending, action), ("B", None))

    def test_two_consecutive_disagreeing_reads_report(self):
        pending, action = self._evaluate(
            baseline="A", current="B", pending="B", reported=False
        )
        self.assertEqual(action, "changed")

    def test_it_reports_once_not_every_poll(self):
        _, action = self._evaluate(
            baseline="A", current="B", pending="B", reported=True
        )
        self.assertIsNone(action)

    def test_unknown_is_not_changed(self):
        pending, action = self._evaluate(
            baseline="A", current="", pending="B", reported=False
        )
        self.assertEqual((pending, action), ("", None))

    def test_going_back_is_a_recovery(self):
        _, action = self._evaluate(
            baseline="A", current="A", pending="", reported=True
        )
        self.assertEqual(action, "restored")

    def test_no_baseline_means_no_comparison(self):
        _, action = self._evaluate(
            baseline="", current="B", pending="B", reported=False
        )
        self.assertIsNone(action)


class _WatchHost:
    """Drives the REAL watch loop with scripted capture-endpoint reads."""

    _wasapi_device_watch_worker = WasapiCaptureMixin._wasapi_device_watch_worker
    _wasapi_stop_requested = WasapiCaptureMixin._wasapi_stop_requested
    _wasapi_capture_stop_event = WasapiCaptureMixin._wasapi_capture_stop_event
    _poll_default_input_device = WasapiCaptureMixin._poll_default_input_device

    def __init__(self, capture_reads, tail=MIC_NEW):
        self._stop_event = threading.Event()
        self._wasapi_default_endpoint_baseline = "SPEAKERS"
        self._wasapi_device_change_reported = False
        self._mic_default_endpoint_baseline = MIC_OLD
        self._mic_device_change_reported = False
        self._capture_reads = list(capture_reads)
        self._tail = tail
        self.input_events = []

    def _read_default_endpoint_id(self):
        return "SPEAKERS"  # render never moves in these tests

    def _read_default_capture_endpoint_id(self):
        # `tail` is what the OS keeps reporting once the script runs out. It
        # has to be settable: a recovery script ends ON the baseline, and a
        # tail that snapped back to the new device would report a genuine
        # SECOND change -- correct behaviour, wrong fixture.
        return self._capture_reads.pop(0) if self._capture_reads else self._tail

    def _report_default_device_changed(self, baseline, current):
        raise AssertionError("the render side must not fire here")

    def _report_default_device_restored(self):
        raise AssertionError("the render side must not fire here")

    def _report_default_input_device_changed(self, baseline, current):
        self.input_events.append(("CHANGED", baseline, current))

    def _report_default_input_device_restored(self):
        self.input_events.append(("RESTORED",))


class TheWatcherSeesTheInputDeviceTest(unittest.TestCase):
    def setUp(self):
        self._saved = (
            default_endpoint.com_initialize_mta,
            default_endpoint.com_uninitialize,
        )
        default_endpoint.com_initialize_mta = lambda: True
        default_endpoint.com_uninitialize = lambda: None

        def restore():
            (
                default_endpoint.com_initialize_mta,
                default_endpoint.com_uninitialize,
            ) = self._saved

        self.addCleanup(restore)

    def _run(self, capture_reads, settle=0.25, tail=MIC_NEW):
        host = _WatchHost(capture_reads, tail=tail)
        thread = threading.Thread(
            target=host._wasapi_device_watch_worker,
            kwargs={"poll_seconds": 0.01},
            daemon=True,
        )
        thread.start()
        import time

        time.sleep(settle)
        host._stop_event.set()
        thread.join(timeout=2.0)
        return host.input_events

    def test_a_real_input_change_is_reported_once(self):
        events = self._run([MIC_NEW] * 20)
        self.assertEqual(events, [("CHANGED", MIC_OLD, MIC_NEW)])

    def test_a_blip_is_debounced_away(self):
        self.assertEqual(self._run([MIC_NEW, MIC_OLD] * 10, tail=MIC_OLD), [])

    def test_plugging_the_old_mic_back_in_is_a_recovery(self):
        events = self._run(
            [MIC_NEW, MIC_NEW, MIC_NEW, MIC_OLD, MIC_OLD],
            settle=0.3,
            tail=MIC_OLD,
        )
        self.assertEqual(
            events, [("CHANGED", MIC_OLD, MIC_NEW), ("RESTORED",)]
        )


class _MicHost:
    """Drives the REAL mic report + rebind over a stubbed sounddevice."""

    # Looked up at CALL time, never bound in the class body. Binding a
    # not-yet-written mixin method here makes the whole module fail at
    # COLLECTION, which costs the per-test pre-fix signal that is the entire
    # point of writing these first. Recorded in FIX_SEQUENCE.md after the last
    # pass hit it; hit again here, hence the comment.
    def _borrow(self, name):
        impl = getattr(MicrophoneCaptureMixin, name, None)
        if impl is None:
            raise AttributeError("%s has not been written yet" % name)
        return impl

    def _report_default_input_device_changed(self, baseline, current):
        return self._borrow("_report_default_input_device_changed")(
            self, baseline, current
        )

    def _rebind_microphone_to_default_device(self):
        return self._borrow("_rebind_microphone_to_default_device")(self)

    def __init__(self, *, mic_enabled=True, running=True, start_fails=False):
        self._stop_event = threading.Event()
        self._mic_stream = object() if running else None
        self._microphone_capture_enabled = mic_enabled
        self._mic_default_endpoint_baseline = MIC_OLD
        self._mic_device_change_reported = True
        self._start_fails = start_fails
        self.calls = []
        self.marshalled = []

    def _run_on_ui_thread(self, fn):
        self.marshalled.append(fn)

    def _schedule_audio_rebind(self, fn):
        # Mirrors the real shared scheduler, which spawns a worker thread.
        # Captured instead of run so the test drives it deterministically.
        self.marshalled.append(fn)
        return True

    def _read_default_capture_endpoint_id(self):
        # The real object has this on the same mixin; the rebind re-baselines
        # through it after a successful reopen.
        return MIC_NEW

    def _close_microphone_stream(self):
        self.calls.append("close")
        self._mic_stream = None

    def _start_microphone_capture(self):
        self.calls.append("start")
        if self._start_fails:
            raise OSError("no input device")
        self._mic_stream = object()

    def run_marshalled(self):
        for fn in list(self.marshalled):
            fn()
        self.marshalled.clear()


class TheMicrophoneRebindTest(unittest.TestCase):
    def setUp(self):
        self.reinit = []

        class _FakeSD:
            def _terminate(_self):
                self.reinit.append("terminate")

            def _initialize(_self):
                self.reinit.append("initialize")

        patcher = patch(
            "alpha.audio.microphone._import_sounddevice", lambda: _FakeSD()
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_change_is_scheduled_not_run_inline(self):
        """The device-watch thread must not do the work itself: a headset plug
        fires this and the WASAPI rebind together, and that one is expensive."""
        host = _MicHost()
        host._report_default_input_device_changed(MIC_OLD, MIC_NEW)
        self.assertEqual(host.calls, [], "the rebind ran on the watcher thread")
        names = [getattr(fn, "__name__", "") for fn in host.marshalled]
        self.assertIn("_rebind_microphone_to_default_device", names)

    def test_it_closes_reinitialises_then_reopens(self):
        """Without the re-init PortAudio keeps reporting the OLD default, so
        the reopen would bind the dead device again and the whole rebind would
        be a no-op that looks like a success."""
        host = _MicHost()
        host._report_default_input_device_changed(MIC_OLD, MIC_NEW)
        host.run_marshalled()
        self.assertEqual(host.calls, ["close", "start"])
        self.assertEqual(
            self.reinit,
            ["terminate", "initialize"],
            "PortAudio was not re-enumerated between close and reopen",
        )

    def test_it_never_starts_a_microphone_the_operator_turned_off(self):
        host = _MicHost(mic_enabled=False, running=False)
        host._report_default_input_device_changed(MIC_OLD, MIC_NEW)
        host.run_marshalled()
        self.assertEqual(
            host.calls, [], "a device change switched the microphone back on"
        )

    def test_it_never_starts_a_microphone_that_was_never_running(self):
        """System-audio-only benchmark mode, or a mic that failed to open at
        Start while the session deliberately carried on."""
        host = _MicHost(running=False)
        host._report_default_input_device_changed(MIC_OLD, MIC_NEW)
        host.run_marshalled()
        self.assertEqual(host.calls, [])

    def test_a_failed_rebind_does_not_kill_the_session(self):
        host = _MicHost(start_fails=True)
        host._report_default_input_device_changed(MIC_OLD, MIC_NEW)
        host.run_marshalled()
        self.assertFalse(
            host._stop_event.is_set(),
            "a microphone rebind stopped the whole session",
        )

    def test_the_session_stop_event_is_never_touched(self):
        host = _MicHost()
        host._report_default_input_device_changed(MIC_OLD, MIC_NEW)
        host.run_marshalled()
        self.assertFalse(host._stop_event.is_set())

    def test_only_one_rebind_runs_for_a_flapping_device(self):
        host = _MicHost()
        for _ in range(5):
            host._report_default_input_device_changed(MIC_OLD, MIC_NEW)
        host.run_marshalled()
        self.assertEqual(
            host.calls.count("start"),
            1,
            "a flapping headset queued %d microphone rebinds"
            % host.calls.count("start"),
        )

    def test_a_successful_rebind_rebaselines_so_the_next_change_is_seen(self):
        host = _MicHost()
        host._report_default_input_device_changed(MIC_OLD, MIC_NEW)
        host.run_marshalled()
        self.assertEqual(
            host._mic_default_endpoint_baseline,
            MIC_NEW,
            "the baseline still names the old microphone, so the watcher would "
            "report a change on every poll from here",
        )
        self.assertFalse(host._mic_device_change_reported)


if __name__ == "__main__":
    unittest.main()
