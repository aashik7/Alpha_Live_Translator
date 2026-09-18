"""The meeting's language cannot be changed while the meeting is running.

WHAT WAS BROKEN
---------------
Deepgram is told the language once, in the query string of the socket the Start
opens. `on_language_change` rewrites `self._listen_language`, but the live
socket cannot be told, so a switch mid-meeting left the OLD language
transcribing while the header claimed the new one -- the operator's report was
"I changed the language and it stopped working; I have to stop and start
again". The canonical ledger carries no language of its own either, so the
finalize pass stamps whatever is selected at the end onto records the previous
language produced.

The two dropdowns are one control. There are exactly two languages, so
`on_language_change` makes picking either side decide the other, and the swap
button writes both. Locking one and leaving the others is not locking
anything.

WHAT THESE TESTS PIN
--------------------
* pressing Start locks every control that can change the meeting language --
  both header combos, both hamburger combos and the swap button -- from the
  moment "Starting…" appears, because the run's language is snapshotted then
* stopping gives them back as PICKERS (`readonly`), never as text boxes
  (`normal`) -- the window has nothing to type into
* the real teardown, the one a failed Start also goes through, unlocks them
* the header can still narrow "Japanese" to "JP" while locked: `CTkComboBox.set`
  writes through its entry, and a disabled entry refuses the write silently, so
  item 71's responsive labels would have stopped changing with no error
* the DISPLAY language (the UI's own English/Japanese) stays switchable during
  a meeting -- item 88e tests that path and it is not what breaks
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import strings  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402

# Every control that can change the meeting language, and the state it must be
# in when no session is running. A combo is a picker, so "readonly".
PICKERS = (
    ("source_combo", "readonly"),
    ("target_combo", "readonly"),
    ("source_combo_menu", "readonly"),
    ("target_combo_menu", "readonly"),
    ("swap_button", "normal"),
)


def _tk_available():
    try:
        import tkinter

        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_tk_available(), "Tk display unavailable in this environment")
class TheLanguageIsFixedForTheRunTest(unittest.TestCase):
    def setUp(self):
        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)
        self.app = AlphaApp()
        self.app.withdraw()
        self.app.update()
        self.addCleanup(self._destroy)

    def _destroy(self):
        try:
            self.app.is_listening = False
            for job in self.app.tk.call("after", "info"):
                try:
                    self.app.after_cancel(job)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.app.destroy()
        except Exception:
            pass

    def states(self):
        return {
            name: str(getattr(self.app, name).cget("state"))
            for name, _ in PICKERS
            if getattr(self.app, name, None) is not None
        }

    def assert_all_present(self):
        missing = [name for name, _ in PICKERS if getattr(self.app, name, None) is None]
        self.assertEqual(
            missing, [], "a control this test is supposed to cover was not built"
        )

    def test_a_running_session_locks_every_control_that_picks_the_language(self):
        self.assert_all_present()
        self.app._set_listen_button_state(True)
        for name, state in self.states().items():
            with self.subTest(control=name):
                self.assertEqual(
                    state,
                    "disabled",
                    "%s can still change the meeting language while the session "
                    "runs; Deepgram was told the language when the socket opened "
                    "and cannot be told again" % (name,),
                )

    def test_the_starting_window_is_locked_too(self):
        """`_start_listening` snapshots the dropdown before the worker runs."""
        self.assert_all_present()
        self.app._set_starting_status()
        for name, state in self.states().items():
            with self.subTest(control=name):
                self.assertEqual(
                    state,
                    "disabled",
                    "%s was still live during \u201cStarting\u2026\u201d, so a switch there "
                    "would run the session on the snapshot while the header "
                    "showed the other language" % (name,),
                )

    def test_a_stop_gives_them_back_as_pickers_not_text_boxes(self):
        self.assert_all_present()
        self.app._set_listen_button_state(True)
        self.app._set_stopped_ui_state()
        actual = self.states()
        for name, expected in PICKERS:
            with self.subTest(control=name):
                self.assertEqual(
                    actual[name],
                    expected,
                    "%s came back as %r, not %r -- a combo restored to "
                    "\"normal\" is a text box the operator can type into"
                    % (name, actual[name], expected),
                )

    def test_the_real_teardown_unlocks_them(self):
        """The path a failed Start also takes: `_stop_listening(graceful=False)`."""
        self.assert_all_present()
        self.app._set_starting_status()
        self.app._stop_listening_immediate()
        actual = self.states()
        for name, expected in PICKERS:
            with self.subTest(control=name):
                self.assertEqual(
                    actual[name],
                    expected,
                    "%s stayed locked after the real teardown; a Start that "
                    "fails would leave the language unchangeable until restart"
                    % (name,),
                )

    def test_the_header_can_still_narrow_its_labels_while_locked(self):
        """Item 71's responsive labels, with the language locked.

        `CTkComboBox.set` writes through the entry widget, and a disabled entry
        drops the write without raising -- so the narrow header would keep the
        wide label and overflow the row it was narrowed to fit.
        """
        self.assert_all_present()
        self.app.source_language.set("Japanese")
        self.app.target_language.set("English")
        self.app._set_listen_button_state(True)

        self.app._set_header_language_abbreviated(True)
        self.assertEqual(
            str(self.app.source_combo.get()),
            self.app._header_language_label("Japanese", True),
            "the locked header kept the wide label when the row narrowed",
        )
        self.app._set_header_language_abbreviated(False)
        self.assertEqual(
            str(self.app.source_combo.get()),
            self.app._header_language_label("Japanese", False),
            "the locked header kept the abbreviation when the row widened",
        )
        self.assertEqual(
            str(self.app.source_combo.cget("state")),
            "disabled",
            "writing the label must not leave the control unlocked",
        )

    def test_the_display_language_stays_switchable_during_a_meeting(self):
        """Item 88e's path. Not what breaks, so not what gets locked."""
        combo = getattr(self.app, "ui_language_combo_menu", None)
        button = getattr(self.app, "ui_language_button", None)
        self.app._set_listen_button_state(True)
        if combo is not None:
            self.assertNotEqual(
                str(combo.cget("state")),
                "disabled",
                "the UI's own language is re-rendered live and must stay "
                "switchable while a meeting runs",
            )
        if button is not None:
            self.assertNotEqual(str(button.cget("state")), "disabled")


@unittest.skipUnless(_tk_available(), "Tk display unavailable in this environment")
class TheLockHasNoWayAroundItTest(unittest.TestCase):
    """Both of these changed the language through a locked combo. Measured:

        1. text half of the locked combo posts the list : True
        2. a pick from an already-open list changes it  : True -> English

    `_make_combos_fully_clickable` bound `<Button-1>` on the combo's entry
    straight to `_open_dropdown_menu`, which checks no state, and a disabled tk
    Entry still delivers user bindings. And a list already posted when the lock
    lands -- a Start finishing while it is open -- still delivers its pick to
    the combo's `command`.
    """

    def setUp(self):
        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)
        self.app = AlphaApp()
        # Mapped, not withdrawn: an unmapped widget does not dispatch a
        # generated `<Button-1>` at all, so the same test on a withdrawn window
        # passes without measuring anything.
        self.app.geometry("1200x700")
        self.app.deiconify()
        self.app.update()
        self.addCleanup(self._destroy)
        self.opened = []
        self.app.source_combo._open_dropdown_menu = lambda: self.opened.append("open")
        self.app.source_language.set("Japanese")
        self.app.target_language.set("English")
        self.app._sync_language_combo_displays()
        self.app.update()

    def _destroy(self):
        try:
            self.app.is_listening = False
            for job in self.app.tk.call("after", "info"):
                try:
                    self.app.after_cancel(job)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.app.destroy()
        except Exception:
            pass

    def click_text_half(self):
        self.opened.clear()
        self.app.source_combo._entry.event_generate("<Button-1>", x=5, y=5)
        self.app.update()
        return bool(self.opened)

    def test_the_text_half_of_a_locked_combo_does_not_open_the_list(self):
        self.app._set_listen_button_state(True)
        self.app.update()
        self.assertFalse(
            self.click_text_half(),
            "clicking the text half of a locked combo still posted the "
            "language list -- the binding called the opener directly and "
            "checked no state",
        )
        self.app._set_stopped_ui_state()
        self.app.update()
        self.assertTrue(
            self.click_text_half(),
            "the whole combo must still be clickable once released; a lock "
            "that works by breaking the control is not a lock",
        )

    def test_a_pick_from_a_list_that_was_already_open_is_ignored(self):
        """The list can be posted when the lock lands, and the pick arrives."""
        self.app._set_listen_button_state(True)
        self.app.update()
        for name, choice in (
            ("source_combo", self.app._header_language_label("English", False)),
            ("source_combo_menu", "English"),
        ):
            with self.subTest(control=name):
                self.app.source_language.set("Japanese")
                self.app._sync_language_combo_displays()
                self.app.update()
                getattr(self.app, name)._dropdown_callback(choice)
                self.app.update()
                self.assertEqual(
                    self.app.source_language.get(),
                    "Japanese",
                    "a pick delivered through %s changed the meeting language "
                    "while the session was running" % (name,),
                )
        self.assertEqual(
            str(self.app.source_combo.get()),
            self.app._header_language_label("Japanese", False),
            "the label was left showing a language the session is not using",
        )


if __name__ == "__main__":
    unittest.main()
