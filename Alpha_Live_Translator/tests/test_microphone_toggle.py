"""The microphone can be switched off, and it is off by default.

WHY THIS EXISTS
---------------
Alpha transcribes ONE language per session -- `AUTHORITATIVE_UI_TO_DEEPGRAM` is
strictly `{"English": "en", "Japanese": "ja"}`, every profile is
`is_auto: False`, and an unmapped selection is refused rather than guessed. Mic
and system audio are MERGED into a single stream before Deepgram
(`timeline_mixer.py`'s `mix_frame`), and diarization is off. So in a bilingual
meeting the operator's own speech is fed to the OTHER language's ASR,
transcribed as nonsense, and that nonsense reaches the canonical transcript.

Measured with the real `TeamsSourceGate` over the real audio of the 2026-08-21
runs: **12.7%** of frames carried the mic into the ASR on one run and **2.3%**
on the other -- and in BOTH the operator barely spoke (mic active 4.7% / 1.2%).
The gate is echo suppression, not language separation: `mic_active and not
system_active` picks `"mic"`, which is exactly when the operator is talking.

WHAT THIS DOES NOT CLAIM
------------------------
The switch feeds `_system_audio_only` inside `_start_listening_worker`, which
also opens WASAPI and the Deepgram socket and cannot be driven from a unit
test. `TheWiringIsPresent` therefore pins the wiring at source level -- the same
approach items 46/47 used, where a grep for the CODE passed for days while a
grep for a CALLER was the check that actually mattered. The behaviour of that
branch when taken is already proven: it is the branch the Stage 1 benchmark
flag has always used to skip the microphone.

WHY SKIPPING THE MIC IS SAFE
-----------------------------
`TimelineMixer._take_samples` zero-pads a short buffer, so with no mic frames
`mic_rms` is 0.0, `mic_active` is always False, the gate degrades cleanly to
system-only, and `mix_frame`'s "none" branch returns the louder source, which is
the system. Nothing waits on a mic frame that never arrives.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import MICROPHONE_CAPTURE_ENABLED_DEFAULT  # noqa: E402

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover
    TK_AVAILABLE = False


class SwitchRecorder:
    """Stands in for a `CTkSwitch`; records select/deselect and state."""

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


class TheDefaultIsMicrophoneOff(unittest.TestCase):
    def test_the_constant_defaults_to_microphone_off(self):
        """The switch reads "Meeting audio only", so ON means the mic is OFF."""
        self.assertFalse(
            MICROPHONE_CAPTURE_ENABLED_DEFAULT,
            "the microphone would be captured by default, which puts the "
            "operator's own speech into the other language's ASR",
        )


class TheToggleKeepsBothSwitchesInStep(unittest.TestCase):
    """Drives the real `toggle_microphone_capture` and its sync helper."""

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        class Host:
            toggle_microphone_capture = AlphaApp.toggle_microphone_capture
            _sync_mic_switches = (
                AlphaApp._sync_mic_switches
            )
            _set_mic_switch_enabled = AlphaApp._set_mic_switch_enabled
            _set_listen_button_state = AlphaApp._set_listen_button_state

            def __init__(self):
                self._microphone_capture_enabled = False
                self.mic_switch = SwitchRecorder(0)
                self.mic_switch_menu = SwitchRecorder(0)
                self._compact_mode = False
                self._menu_visible = False
                self.listen_button = None
                self.listen_button_menu = None
                self.status_updates = []

            def _update_status_bar(self, listening=False):
                self.status_updates.append(listening)

        self.host = Host()

    def test_the_default_shows_the_microphone_as_off(self):
        self.host._sync_mic_switches()
        self.assertEqual(self.host.mic_switch.get(), 0)
        self.assertEqual(self.host.mic_switch_menu.get(), 0)

    def test_turning_the_switch_on_enables_the_microphone(self):
        self.host.mic_switch.select()
        self.host.toggle_microphone_capture()
        self.assertTrue(self.host._microphone_capture_enabled)
        self.assertEqual(self.host.mic_switch_menu.get(), 1)

    def test_turning_it_back_off_disables_the_microphone(self):
        self.host.mic_switch.select()
        self.host.toggle_microphone_capture()
        self.host.mic_switch.deselect()
        self.host.toggle_microphone_capture()
        self.assertFalse(self.host._microphone_capture_enabled)
        self.assertEqual(self.host.mic_switch_menu.get(), 0)

    def test_the_menu_switch_drives_it_in_compact_mode(self):
        """In compact mode the header switch is not even mapped."""
        self.host._compact_mode = True
        self.host._menu_visible = True
        self.host.mic_switch_menu.select()
        self.host.toggle_microphone_capture()
        self.assertTrue(self.host._microphone_capture_enabled)
        self.assertEqual(
            self.host.mic_switch.get(),
            1,
            "the header switch did not follow the menu switch",
        )

    def test_a_missing_switch_is_not_an_error(self):
        self.host.mic_switch_menu = None
        self.host._sync_mic_switches()

    def test_listening_locks_the_switches(self):
        """The value is read at Start, so it must not look changeable mid-session."""
        self.host._set_listen_button_state(True)
        self.assertEqual(self.host.mic_switch.state, "disabled")
        self.assertEqual(self.host.mic_switch_menu.state, "disabled")

    def test_stopping_unlocks_the_switches(self):
        self.host._set_listen_button_state(True)
        self.host._set_listen_button_state(False)
        self.assertEqual(self.host.mic_switch.state, "normal")
        self.assertEqual(self.host.mic_switch_menu.state, "normal")


class TheWiringIsPresent(unittest.TestCase):
    """Items 46/47's lesson: grep for the CALLER, not for the code."""

    def _source(self):
        return (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8", errors="replace"
        )

    def test_the_switch_feeds_the_system_audio_only_branch(self):
        source = self._source()
        self.assertIn("if not self._microphone_capture_enabled:", source)
        self.assertIn("_system_audio_only = True", source)

    def test_the_microphone_is_started_only_when_that_branch_allows_it(self):
        """`_start_microphone_capture` must keep its single guarded call site."""
        source = self._source()
        self.assertEqual(
            source.count("self._start_microphone_capture()"),
            1,
            "a second, unguarded microphone start appeared",
        )
        guard = source.index("if not _system_audio_only:")
        call = source.index("self._start_microphone_capture()")
        self.assertLess(guard, call, "the microphone start escaped its guard")

    def test_turning_it_off_is_recorded(self):
        self.assertIn("MICROPHONE_CAPTURE_DISABLED_BY_USER", self._source())


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheControlIsReachableAtEveryWidth(unittest.TestCase):
    """The bug this class exists for.

    The first shipped version put the header switch behind
    `LAYOUT_WIDE_BREAKPOINT` (1050) to avoid an overflow, which opened a dead
    zone at 800-1050 design px: the header switch was hidden and the hamburger
    was not shown either, so the control could not be reached at all. With
    `DEFAULT_WINDOW_WIDTH = 900` the app opened INSIDE that zone, and the user
    reported exactly that -- no way to turn the microphone on.

    Geometry is measured on a real MAPPED root, and each test builds its own:
    an unrealised window reports widths that make these assertions vacuous, and
    a root shared across a class leaks CTk state into the other Tk suites.
    """

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.app = AlphaApp()
        self.app.deiconify()
        self.app.update()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def _at(self, width):
        self.app.geometry(f"{width}x800")
        self.app.update()
        self.app._apply_responsive_layout()
        self.app.update()
        header = self.app.right_header_cluster.master
        return header.winfo_reqwidth(), header.winfo_width()

    def _reachable(self):
        return bool(
            self.app.mic_switch.winfo_ismapped()
            or self.app.hamburger_button.winfo_ismapped()
        )

    def test_it_is_reachable_at_every_width(self):
        for width in (600, 700, 790, 800, 820, 900, 1000, 1100, 1400, 1920):
            self._at(width)
            self.assertTrue(
                self._reachable(),
                f"the microphone control cannot be reached at {width} device px",
            )

    def test_it_is_reachable_at_the_default_window_size(self):
        """The exact case the user hit."""
        from alpha.ui.theme import DEFAULT_WINDOW_WIDTH

        self._at(DEFAULT_WINDOW_WIDTH)
        self.assertTrue(self._reachable())

    def test_the_switch_stays_the_compact_form(self):
        """A default CTkCheckBox or CTkSwitch is 150 device px whatever its
        label; this one is built narrow on purpose and has to stay that way.

        The bound used to be 114, which was the HEADER's spare width at 900.
        That number stopped meaning anything when item 88c moved the control
        into the status strip, and it went stale quietly: relabelling "Mic" to
        "Mic off" pushed the widget to 117 and failed a test whose premise had
        already gone. The guard now says what it actually guards -- comfortably
        under the default form -- and the real constraint, that the strip it
        lives in fits, is the test below.
        """
        self._at(1400)
        self.assertLess(self.app.mic_switch.winfo_reqwidth(), 150)

    def test_the_status_strip_holds_its_contents(self):
        """The constraint that replaced the 114: whatever the label says, the
        cluster carrying mic, standby and the timer has to fit the strip."""
        for width in (900, 1400, 1920):
            self._at(width)
            cluster = self.app._status_right_cluster
            self.assertGreaterEqual(
                cluster.winfo_width(),
                cluster.winfo_reqwidth(),
                f"the status strip is squeezed at {width}",
            )
            strip_right = (
                self.app.status_bar_frame.winfo_rootx()
                + self.app.status_bar_frame.winfo_width()
            )
            for name in ("mic_switch", "signal_label", "timer_label"):
                widget = getattr(self.app, name, None)
                if widget is None or not widget.winfo_ismapped():
                    continue
                overflow = (widget.winfo_rootx() + widget.winfo_width()) - strip_right
                self.assertLessEqual(
                    overflow, 0, f"{name} is {overflow}px past the strip at {width}"
                )

    def test_the_header_does_not_overflow_at_the_default_width(self):
        req, got = self._at(900)
        self.assertLessEqual(req, got, "the header overflows at 900 design px")

    def test_the_header_does_not_overflow_where_the_switch_is_shown(self):
        for width in (900, 1000, 1100, 1400, 1920):
            req, got = self._at(width)
            self.assertLessEqual(req, got, f"the header overflows at {width}")

    def test_it_no_longer_depends_on_a_breakpoint_at_all(self):
        """Item 88c removed the failure mode instead of re-tuning it.

        This used to assert the hand-off: below 800 the header switch hid and
        the hamburger carried the control. That hand-off was the whole source
        of the original bug -- two thresholds that had to agree, and once did
        not. The control now lives in the status strip, which is shown at every
        width, so there is no threshold left to get wrong and nothing to hand
        over to. The switch has to be mapped at every width, including the ones
        where the header itself is gone.
        """
        for width in (600, 700, 790, 800, 900, 1400, 1920):
            self._at(width)
            self.assertTrue(
                self.app.mic_switch.winfo_ismapped(),
                f"the microphone control is not on screen at {width}",
            )
        self.assertIsNotNone(self.app.mic_switch_menu)

    def test_it_is_not_in_the_header_any_more(self):
        """Measured at 5px past the header's right edge at 800 in Japanese,
        before the display-language button was even added. It moved because it
        did not fit, so a test keeps it out."""
        self._at(900)
        header_widgets = []

        def walk(widget):
            for child in widget.winfo_children():
                header_widgets.append(child)
                walk(child)

        walk(self.app.right_header_cluster.master)
        self.assertNotIn(self.app.mic_switch, header_widgets)

    def test_the_default_is_microphone_off_on_a_real_window(self):
        self._at(1400)
        self.assertEqual(self.app.mic_switch.get(), 0)
        self.assertEqual(self.app.mic_switch_menu.get(), 0)
        self.assertFalse(self.app._microphone_capture_enabled)


if __name__ == "__main__":
    unittest.main()
