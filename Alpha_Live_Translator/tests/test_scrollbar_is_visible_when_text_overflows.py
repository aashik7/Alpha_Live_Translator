"""The transcript and translation panes must actually show a scrollbar.

WHAT WAS BROKEN
---------------
Reported from a live test: no scroller anywhere in the application.

`_create_styled_text` packs the text widget first, with `expand=True`:

    text_widget.pack(side="left", fill="both", expand=True)

and `check_scrollbar_visibility` packs the scrollbar later, on demand:

    scrollbar.pack(side="right", fill="y")

In Tk's packer the first widget with `expand=True` claims the entire cavity, so
a sibling packed AFTER it is allocated from what is left, which is nothing.
Measured on a realised window:

    scrollbar packed FIRST -> width 16, winfo_ismapped() True,  text width 484
    scrollbar packed LAST  -> width  1, winfo_ismapped() False, text width 500

The summary panel packs its scrollbar first and always had a working one, which
is why the defect looked like it could not be a pack-order problem.

The fix is `before=text_widget` on the show path. It has to be there rather than
only at creation, because the auto-hide calls `pack_forget()` and re-packing
sends the widget back to the end of the pack order.

The failure was silent: `check_scrollbar_visibility` wraps its whole body in
`except Exception: pass`, so nothing would have been logged even if Tk had
objected.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless CI without a display
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class ScrollbarVisibilityTest(unittest.TestCase):
    """Uses the real `check_scrollbar_visibility` against real widgets.

    The window is really mapped -- `winfo_width()` reports 1 on an unrealised
    window, so a withdrawn root would make every assertion here vacuous.
    """

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.root = tk.Tk()
        self.root.geometry("500x300")
        self.root.update()
        self.frame = tk.Frame(self.root)
        self.frame.pack(fill="both", expand=True)
        self.scrollbar = tk.Scrollbar(self.frame, width=16)
        self.text = tk.Text(self.frame, wrap="word")
        # Creation order exactly as `_create_styled_text` has it.
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.configure(command=self.text.yview)

        class Host:
            check_scrollbar_visibility = AlphaApp.check_scrollbar_visibility

        self.host = Host()
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def _fill(self, lines=300):
        for i in range(lines):
            self.text.insert("end", f"line {i}\n")
        self.root.update()

    def _check(self):
        self.host.check_scrollbar_visibility(self.text, self.scrollbar)
        self.root.update()

    def test_an_overflowing_pane_shows_a_scrollbar_with_real_width(self):
        self._fill()
        self._check()
        self.assertTrue(
            self.scrollbar.winfo_ismapped(), "the scrollbar was never mapped"
        )
        self.assertGreater(
            self.scrollbar.winfo_width(),
            1,
            "the scrollbar is mapped but has no width -- packed after the "
            "expand=True text widget",
        )

    def test_the_text_widget_gives_up_the_scrollbar_width(self):
        self._fill()
        self._check()
        self.assertLess(
            self.text.winfo_width(),
            self.frame.winfo_width(),
            "the text widget still occupies the whole frame",
        )

    def test_a_pane_that_fits_hides_the_scrollbar(self):
        self.text.insert("end", "one short line\n")
        self.root.update()
        self._check()
        self.assertFalse(
            self.scrollbar.winfo_ismapped(),
            "the scrollbar is showing for content that fits",
        )

    def test_it_comes_back_after_being_hidden(self):
        """The auto-hide calls pack_forget(); re-packing must restore the
        position, not append to the end of the pack order."""
        self.text.insert("end", "short\n")
        self.root.update()
        self._check()
        self.assertFalse(self.scrollbar.winfo_ismapped())
        self._fill()
        self._check()
        self.assertTrue(self.scrollbar.winfo_ismapped(), "it never came back")
        self.assertGreater(self.scrollbar.winfo_width(), 1)

    def test_repeated_toggling_keeps_a_usable_width(self):
        for _ in range(4):
            self.text.delete("1.0", "end")
            self.text.insert("end", "short\n")
            self.root.update()
            self._check()
            self._fill(200)
            self._check()
        self.assertTrue(self.scrollbar.winfo_ismapped())
        self.assertGreater(self.scrollbar.winfo_width(), 1)


if __name__ == "__main__":
    unittest.main()
