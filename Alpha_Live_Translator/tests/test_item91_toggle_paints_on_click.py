"""Item 91 -- pressing Show has to draw the pane, not schedule it.

The reported bug: on an external monitor, clicking Show Transcript did nothing.
Resizing the window by hand afterwards made the pane appear. So the flag and the
grid were right all along; the geometry was simply never applied.

`grid_remove()` unmaps on its own, which is why Hide always looked fine. The
`grid()` that brings a pane BACK needs the geometry manager to run, and nothing
in main_window.py ever asked it to -- every layout change was left for whenever
Tk next reached an idle pass.

The whole point of this file is what it does NOT do: there is no `update()`
between the click and the assertion. Adding one would make it pass against the
broken code, which is exactly how this survived several rounds of testing.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _close(root):
    try:
        for job in root.tk.call("after", "info"):
            try:
                root.after_cancel(job)
            except Exception:
                pass
    except Exception:
        pass
    root.destroy()


try:
    import customtkinter as ctk

    _close(ctk.CTk())
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class ShowDrawsTheTranscript(unittest.TestCase):
    """Drives the real AlphaApp, because the defect is in real geometry.

    1121 x 650 is the window the reporter's external monitor produced, read out
    of the app's own LAYOUT_SNAPSHOT.jsonl. The other two cover the laptop and a
    large screen, since the fix must not depend on which one it is.
    """

    WINDOWS = ("1121x650", "1350x975", "1920x1080")

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.app = AlphaApp()
        self.addCleanup(self._destroy)
        self.app.deiconify()
        self.app.update()

    def _destroy(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def _settle(self, times=8):
        for _ in range(times):
            self.app.update_idletasks()
            self.app.update()

    def _start_visible(self, geometry):
        self.app.geometry(geometry)
        self._settle()
        self.app._apply_responsive_layout()
        self._settle()
        self.app._initial_verse_visible = True
        self.app._sync_transcript_visibility()
        self._settle()

    def test_show_maps_the_pane_without_a_resize(self):
        """The reported bug. No update between the click and the assertion."""
        for geometry in self.WINDOWS:
            with self.subTest(window=geometry):
                self._start_visible(geometry)
                self.app.toggle_initial_verse()          # hide
                self.app.toggle_initial_verse()          # show
                self.assertTrue(
                    self.app.transcript_column.winfo_ismapped(),
                    f"the transcript column is still unmapped after Show at {geometry}",
                )
                self.assertTrue(
                    self.app.initial_verse_frame.winfo_ismapped(),
                    f"the transcript card is still unmapped after Show at {geometry}",
                )

    def test_hide_unmaps_the_pane_without_a_resize(self):
        for geometry in self.WINDOWS:
            with self.subTest(window=geometry):
                self._start_visible(geometry)
                self.app.toggle_initial_verse()
                self.assertFalse(self.app.transcript_column.winfo_ismapped())

    def test_the_pane_has_real_width_when_shown(self):
        """Mapped is not enough -- a 2 px sliver is mapped too."""
        self._start_visible("1121x650")
        self.app.toggle_initial_verse()
        self.app.toggle_initial_verse()
        self.assertGreater(self.app.transcript_column.winfo_width(), 100)

    def test_the_toggle_button_follows_the_pane(self):
        """Hide lives inside the card, so a card that does not draw takes its
        button with it -- which is what the reporter saw."""
        self._start_visible("1121x650")
        self.app.toggle_initial_verse()
        self.assertTrue(self.app.show_initial_button.winfo_ismapped())
        self.app.toggle_initial_verse()
        self.assertTrue(self.app.hide_initial_button.winfo_ismapped())
        self.assertFalse(self.app.show_initial_button.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
