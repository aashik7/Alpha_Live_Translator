"""The Summary button says the feature is coming, and does nothing else.

WHY
---
Item 76 (the meeting summary as a modal overlay) is open, and the ledger records
it as "not needed for the client's request". A button that half-opens an
unfinished panel during a client meeting is worse than one that says plainly
that the feature is not ready.

WHAT IS DELIBERATELY NOT REMOVED
---------------------------------
The panel itself, `summary_service.generate_summary_from_store`, the
`SUMMARY_UPDATED` event and `_set_summary_panel_text` are all still present and
still wired to each other. Only the button's entry point changed. Deleting the
panel with it would be a far larger change than the one asked for, and item 76
is the row that decides its future.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TheButtonAnnouncesItIsNotReady(unittest.TestCase):
    """Drives the real `AlphaApp.show_meeting_summary`."""

    def setUp(self):
        import alpha.ui.main_window as mw

        self.mw = mw
        self.shown = []
        self.real_box = mw.messagebox

        outer = self

        class FakeBox:
            @staticmethod
            def showinfo(title, message):
                outer.shown.append((title, message))

            @staticmethod
            def showerror(title, message):  # must never be reached
                outer.shown.append(("ERROR", message))

        mw.messagebox = FakeBox

        class Host:
            show_meeting_summary = mw.AlphaApp.show_meeting_summary

            def __init__(self):
                # Present so that a regression back to the panel path would be
                # visible here rather than raising and looking like a stub.
                self.summary_panel_visible = False
                self.panel_calls = []

            def show_summary_panel(self):
                self.panel_calls.append("show")

            def hide_summary_panel(self):
                self.panel_calls.append("hide")

        self.host = Host()

    def tearDown(self):
        self.mw.messagebox = self.real_box

    def test_it_shows_a_notice(self):
        self.host.show_meeting_summary()
        self.assertEqual(len(self.shown), 1)

    def test_the_notice_says_the_feature_is_coming(self):
        self.host.show_meeting_summary()
        self.assertIn("coming soon", self.shown[0][1].lower())

    def test_the_title_matches_the_button(self):
        self.host.show_meeting_summary()
        self.assertEqual(self.shown[0][0], self.mw.MEETING_SUMMARY_BUTTON_TEXT)

    def test_it_does_not_open_the_panel(self):
        self.host.show_meeting_summary()
        self.assertEqual(self.host.panel_calls, [])

    def test_it_does_not_open_the_panel_even_when_one_is_marked_visible(self):
        self.host.summary_panel_visible = True
        self.host.show_meeting_summary()
        self.assertEqual(self.host.panel_calls, [])
        self.assertEqual(len(self.shown), 1)

    def test_it_is_not_an_error_dialog(self):
        self.host.show_meeting_summary()
        self.assertNotEqual(self.shown[0][0], "ERROR")

    def test_clicking_twice_is_harmless(self):
        self.host.show_meeting_summary()
        self.host.show_meeting_summary()
        self.assertEqual(len(self.shown), 2)
        self.assertEqual(self.host.panel_calls, [])


class TheSummaryMachineryIsStillThere(unittest.TestCase):
    """Item 76 has to be able to pick this back up."""

    def _source(self):
        return (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8", errors="replace"
        )

    def test_the_panel_helpers_survive(self):
        source = self._source()
        for name in (
            "def show_summary_panel",
            "def hide_summary_panel",
            "def _set_summary_panel_text",
        ):
            self.assertIn(name, source, f"{name} was removed with the button")

    def test_the_summary_service_is_still_reachable(self):
        self.assertIn("generate_summary_from_store", self._source())

    def test_the_button_still_calls_this_handler(self):
        self.assertIn("command=self.show_meeting_summary", self._source())


if __name__ == "__main__":
    unittest.main()
