"""The microphone switch has to work while a meeting is running.

Until now the switch was read once, at Start, and locked for the rest of the
session -- so an operator who realised mid-meeting that their own voice was
missing had to Stop and Start again, losing the running transcript's
continuity to get it.

The audio graph never needed that restart. Measured on the real
`DeepgramTimelineMixer`, with a session configured at Start as
`configure_sources(2, 48000, mic_available=False)`:

    frames before the mic = 60   mic_rms 0.0      chosen_source 'none'
    frames after the mic  = 61   mic carrying 50
                                 mic_rms 6320.6   chosen_source 'mixed'

Nothing was reconfigured between those two measurements. `mic_audio_queue` is
created at Start and lives until Stop, the mixer drains it every tick, and
`push_mic` latches `_mic_source_available` by itself -- `configure_sources`
only seeds that flag. A stream opened mid-session is therefore picked up with
no restart of anything.

Opening and closing the stream mid-session was already proven too: that is
exactly what `_rebind_microphone_to_default_device` does on every default-input
change. This file makes the switch use the same road.
"""

import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio.microphone import MicrophoneCaptureMixin  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402


class SwitchRecorder:
    """The two bits of a CTkSwitch this code touches."""

    def __init__(self, value=0):
        self._value = value
        self.state = "normal"

    def get(self):
        return self._value

    def select(self):
        self._value = 1

    def deselect(self):
        self._value = 0

    def configure(self, **kw):
        if "state" in kw:
            self.state = kw["state"]


class MicHost:
    """Bare host borrowing the real switch handler and the real apply."""

    toggle_microphone_capture = AlphaApp.toggle_microphone_capture
    _sync_mic_switches = AlphaApp._sync_mic_switches
    _set_listen_button_state = AlphaApp._set_listen_button_state
    _apply_microphone_capture_live = (
        MicrophoneCaptureMixin._apply_microphone_capture_live
    )
    _carry_microphone_switch = MicrophoneCaptureMixin._carry_microphone_switch
    _schedule_microphone_capture_apply = (
        MicrophoneCaptureMixin._schedule_microphone_capture_apply
    )

    def __init__(self, listening=True, mic_on=False, stream=None):
        self._microphone_capture_enabled = mic_on
        self.mic_switch = SwitchRecorder(1 if mic_on else 0)
        self.mic_switch_menu = SwitchRecorder(1 if mic_on else 0)
        self._compact_mode = False
        self._menu_visible = False
        self.listen_button = None
        self.listen_button_menu = None
        self.is_listening = listening
        self._stop_event = threading.Event()
        self._mic_stream = stream
        self._mic_chunks_captured = 0
        # what the apply is expected to do
        self.opened = 0
        self.closed = 0
        self.boundaries = 0
        self.dispatched = []
        self.confirm_result = True
        self.open_raises = None
        self.status_updates = []

    # --- the pieces the apply drives ------------------------------------
    def _schedule_audio_rebind(self, fn):
        self.dispatched.append(fn)
        fn()
        return True

    def _start_microphone_capture(self):
        if self.open_raises is not None:
            raise self.open_raises
        self.opened += 1
        self._mic_stream = object()
        self._mic_chunks_captured += 1 if self.confirm_result else 0

    def _close_microphone_stream(self):
        self.closed += 1
        self._mic_stream = None

    def _await_capture_confirmation(self, counter_attr, before):
        return bool(self.confirm_result)

    def _mark_device_swap_boundary(self):
        self.boundaries += 1
        return True

    def _update_status_bar(self, listening=False):
        self.status_updates.append(listening)


class TheSwitchStaysUsableDuringAMeeting(unittest.TestCase):
    """Replaces the old contract, which locked the switch while listening.

    The lock was correct only while the value was read once at Start. Now that
    flipping it applies immediately, a disabled switch would be the bug: it
    would hide a control that works.
    """

    def setUp(self):
        self.host = MicHost(listening=False)

    def test_the_switch_is_not_locked_while_listening(self):
        self.host._set_listen_button_state(True)
        self.assertEqual(self.host.mic_switch.state, "normal")
        self.assertEqual(self.host.mic_switch_menu.state, "normal")

    def test_it_is_still_usable_after_stopping(self):
        self.host._set_listen_button_state(True)
        self.host._set_listen_button_state(False)
        self.assertEqual(self.host.mic_switch.state, "normal")
        self.assertEqual(self.host.mic_switch_menu.state, "normal")


class TurningItOnDuringAMeeting(unittest.TestCase):
    def setUp(self):
        self.host = MicHost(listening=True, mic_on=False, stream=None)

    def _flip_on(self):
        self.host.mic_switch.select()
        self.host.toggle_microphone_capture()

    def test_the_stream_is_opened(self):
        self._flip_on()
        self.assertTrue(self.host._microphone_capture_enabled)
        self.assertEqual(self.host.opened, 1)
        self.assertIsNotNone(self.host._mic_stream)

    def test_the_work_never_runs_on_the_ui_thread(self):
        """`sd.InputStream` open is device work; the mainloop must not wait."""
        self._flip_on()
        self.assertEqual(
            len(self.host.dispatched),
            1,
            "the open did not go through the shared rebind worker",
        )

    def test_the_seam_is_marked_so_the_assembler_does_not_glue_across_it(self):
        """A new acoustic source mid-utterance is the same seam as a device swap."""
        self._flip_on()
        self.assertEqual(self.host.boundaries, 1)

    def test_an_already_open_microphone_is_left_alone(self):
        self.host._mic_stream = object()
        self._flip_on()
        self.assertEqual(self.host.opened, 0)
        self.assertEqual(self.host.closed, 0)
        self.assertEqual(self.host.boundaries, 0)

    def test_a_microphone_that_will_not_open_does_not_kill_the_meeting(self):
        self.host.open_raises = OSError("no input device")
        self._flip_on()
        self.assertEqual(self.host.opened, 0)
        self.assertIsNone(self.host._mic_stream)

    def test_a_stream_that_delivers_no_audio_is_not_called_a_success(self):
        """Opening a device is not the same as a microphone that works.

        Driven through the apply directly, not through the switch: a second
        toggle would return False merely because the stream is already open,
        which would pass this test without ever reaching the confirmation.
        """
        self.host.confirm_result = False
        self.host._microphone_capture_enabled = True
        self.assertFalse(
            self.host._apply_microphone_capture_live(),
            "a silent microphone was reported as applied",
        )
        self.assertEqual(self.host.opened, 1, "the stream was never opened at all")


class TurningItOffDuringAMeeting(unittest.TestCase):
    def setUp(self):
        self.host = MicHost(listening=True, mic_on=True, stream=object())

    def _flip_off(self):
        self.host.mic_switch.deselect()
        self.host.toggle_microphone_capture()

    def test_the_stream_is_closed(self):
        self._flip_off()
        self.assertFalse(self.host._microphone_capture_enabled)
        self.assertEqual(self.host.closed, 1)
        self.assertIsNone(self.host._mic_stream)

    def test_the_seam_is_marked_here_too(self):
        self._flip_off()
        self.assertEqual(self.host.boundaries, 1)

    def test_an_already_closed_microphone_is_left_alone(self):
        self.host._mic_stream = None
        self._flip_off()
        self.assertEqual(self.host.closed, 0)
        self.assertEqual(self.host.boundaries, 0)


class NothingIsTouchedOutsideAMeeting(unittest.TestCase):
    def test_flipping_it_before_start_opens_nothing(self):
        """Outside a session the value is still just read at Start."""
        host = MicHost(listening=False)
        host.mic_switch.select()
        host.toggle_microphone_capture()
        self.assertTrue(host._microphone_capture_enabled)
        self.assertEqual(host.opened, 0)
        self.assertEqual(host.dispatched, [])

    def test_a_stop_already_in_flight_is_respected(self):
        """Reopening after Stop would leave a stream behind a dead session."""
        host = MicHost(listening=True)
        host._stop_event.set()
        host.mic_switch.select()
        host.toggle_microphone_capture()
        self.assertEqual(host.opened, 0)


class TwoFlipsDoNotRaceEachOther(unittest.TestCase):
    def test_a_second_apply_while_one_runs_does_not_do_the_work_twice(self):
        host = MicHost(listening=True, mic_on=True, stream=None)
        host._mic_capture_apply_in_progress = True
        self.assertFalse(host._apply_microphone_capture_live())
        self.assertEqual(host.opened, 0)

    def test_a_flip_that_lands_mid_apply_is_not_lost(self):
        """The worst direction for this control to fail.

        `_await_capture_confirmation` waits up to REBIND_AUDIO_CONFIRM_S
        (3.0 s), so an operator who turns the microphone on and straight back
        off is inside that window. Dropping the second flip would leave the
        stream live while the switch reads OFF.
        """
        host = MicHost(listening=True, mic_on=True, stream=None)

        def flip_off_during_the_wait(counter_attr, before):
            host._microphone_capture_enabled = False
            host.mic_switch.deselect()
            # What the contending apply does when it finds the claim taken.
            host._apply_microphone_capture_live()
            return True

        host._await_capture_confirmation = flip_off_during_the_wait

        host._apply_microphone_capture_live()

        self.assertEqual(host.opened, 1, "the first flip never opened the stream")
        self.assertEqual(
            host.closed, 1, "the microphone was left live while the switch read OFF"
        )
        self.assertIsNone(host._mic_stream)

    def test_the_flag_is_released_even_when_the_open_throws(self):
        host = MicHost(listening=True, mic_on=True, stream=None)
        host.open_raises = OSError("boom")
        host._apply_microphone_capture_live()
        self.assertFalse(getattr(host, "_mic_capture_apply_in_progress", False))


class TheDeviceWatcherStillRefusesToTurnItOn(unittest.TestCase):
    """A default-input change must never switch someone's voice back on."""

    def test_a_rebind_does_nothing_while_the_switch_says_off(self):
        class Host:
            _rebind_microphone_to_default_device = (
                MicrophoneCaptureMixin._rebind_microphone_to_default_device
            )

            def __init__(self):
                self._stop_event = threading.Event()
                self._microphone_capture_enabled = False
                self._mic_stream = object()

        self.assertFalse(Host()._rebind_microphone_to_default_device())


class TheWiringIsPresent(unittest.TestCase):
    """Items 46/47's lesson: grep for the CALLER, not for the code."""

    def _main_window(self):
        return (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8", errors="replace"
        )

    def test_the_start_path_still_owns_the_only_direct_open(self):
        """The live apply lives in the audio layer, not beside the Start path."""
        self.assertEqual(
            self._main_window().count("self._start_microphone_capture()"),
            1,
            "a second, unguarded microphone start appeared in the window",
        )

    def test_the_toggle_reaches_the_live_apply(self):
        self.assertIn(
            "_schedule_microphone_capture_apply", self._main_window()
        )

    def test_a_flip_made_while_starting_is_reconciled_once_listening(self):
        """`is_listening` is False during "Starting…", so the apply no-ops then."""
        source = self._main_window()
        start_true = source.index("self.is_listening = True")
        reconcile = source.index(
            "_schedule_microphone_capture_apply", start_true
        )
        button = source.index("self._set_listen_button_state(True)", start_true)
        self.assertLess(
            start_true,
            reconcile,
            "the reconcile runs before the session is marked as listening",
        )
        self.assertLess(
            button,
            reconcile,
            "the reconcile should follow the button state, not precede it",
        )


if __name__ == "__main__":
    unittest.main()
