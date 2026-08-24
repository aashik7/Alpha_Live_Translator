"""Item 91d -- Show runs the same layout pass a resize runs.

THE REPORT, AND THE SENTENCE THAT SOLVED IT
-------------------------------------------
On an external monitor, Hide removed the transcript and Show brought nothing
back, taking its own button with it. Three earlier attempts missed it. What
finally located it was the reporter's own summary:

    "clicking the button changes nothing, but if I resize the window then after
     the first refresh the UI looks normal"

The layout is RIGHT the next time it runs. So the click was never computing a
wrong layout -- it was running a *smaller* one:

    click  -> _place_toggle_button + _apply_content_layout      the reading grid
    resize -> _apply_responsive_layout: content_wrapper, status bar, footer,
              brand block and the header layout, THEN the reading grid

Nothing above `transcript_column` was touched by a click. Their log agrees:
every probed widget reads `mapped:0` after Show, including the
`hide_initial_button` that had just been gridded -- which is what an unmapped
ANCESTOR does to its descendants, not four independent failures.

WHAT THIS TEST DOES
-------------------
The failing state cannot be produced on a 150 % display -- `set_widget_scaling`
compounds with the display's own factor, so the reporter's scaling 1.0 is
unreachable. So the END STATE is injected instead of the cause: an ancestor is
left unmapped once, exactly as their log describes, and Show has to recover the
whole subtree.

Measured against the code before the fix, this is what these assertions saw:

    PRE-FIX  after SHOW   wrapper=0 column=0 card=0 hide=0
    PRE-FIX  + idle       wrapper=0 column=0 card=0 hide=0

and after:

    after SHOW            wrapper=1 column=1 card=1 hide=1

NOTHING between the click and the assertion calls `update()`. That is
deliberate: a test that lets Tk settle first cannot see this class of bug, and
that is how it survived three rounds.
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

if TK_AVAILABLE:
    from alpha.ui.main_window import AlphaApp


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class ShowRecoversAnUnmappedAncestor(unittest.TestCase):
    def setUp(self):
        self.app = AlphaApp()
        self.addCleanup(self._destroy)
        self.app.deiconify()
        self.app.geometry("1350x900")
        self._settle()
        self.app._apply_responsive_layout()
        self._settle()
        self.app._initial_verse_visible = True
        self.app._sync_transcript_visibility()
        self._settle()

    def _destroy(self):
        try:
            _close(self.app)
        except Exception:
            pass

    def _settle(self, times=6):
        for _ in range(times):
            self.app.update_idletasks()
            self.app.update()

    def _mapped(self):
        return {
            "wrapper": bool(self.app.content_wrapper.winfo_ismapped()),
            "column": bool(self.app.transcript_column.winfo_ismapped()),
            "card": bool(self.app.initial_verse_frame.winfo_ismapped()),
            "hide": bool(self.app.hide_initial_button.winfo_ismapped()),
        }

    def _break_the_ancestor(self):
        """The reporter's end state: something above the pane is unmapped.

        Injected once, the way a DPI change leaves it -- not on every layout
        call, which would be a saboteur no fix could beat.
        """
        self.app.toggle_initial_verse()          # Hide
        self._settle()
        self.app.content_wrapper.grid_remove()
        self._settle()
        self.assertEqual(
            self._mapped(),
            {"wrapper": False, "column": False, "card": False, "hide": False},
            "the injection did not reproduce the reported state",
        )

    def test_the_click_itself_puts_everything_back(self):
        """No update() between the click and the assertion -- see the docstring."""
        self._break_the_ancestor()
        self.app.toggle_initial_verse()          # Show
        self.assertEqual(
            self._mapped(),
            {"wrapper": True, "column": True, "card": True, "hide": True},
        )

    def test_the_hide_button_comes_back_with_its_container(self):
        """Their log's strangest line: the just-gridded Hide button reading
        mapped:0. It is not a separate failure and needs no separate fix."""
        self._break_the_ancestor()
        self.app.toggle_initial_verse()
        self.assertTrue(self.app.hide_initial_button.winfo_ismapped())
        self.assertFalse(self.app.show_initial_button.winfo_ismapped())

    def test_hide_still_works_from_the_recovered_state(self):
        """Recovery must not leave the toggle stuck on."""
        self._break_the_ancestor()
        self.app.toggle_initial_verse()
        self._settle()
        self.app.toggle_initial_verse()
        self._settle()
        self.assertFalse(self.app.transcript_column.winfo_ismapped())
        self.assertTrue(self.app.show_initial_button.winfo_ismapped())

    def test_a_healthy_toggle_is_unaffected(self):
        """The ordinary path has to keep behaving; this is not a repair-only
        code path."""
        self.app.toggle_initial_verse()
        self._settle()
        self.assertFalse(self.app.transcript_column.winfo_ismapped())
        self.app.toggle_initial_verse()
        self._settle()
        self.assertTrue(self.app.transcript_column.winfo_ismapped())
        self.assertTrue(self.app.initial_verse_frame.winfo_ismapped())


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TheGridOwnerAlsoOwnsBeingOnScreen(unittest.TestCase):
    """Pins the mechanism, so a refactor cannot quietly drop it again.

    `_apply_content_layout` laid out the panes INSIDE `content_wrapper` but
    never asserted that the wrapper itself was on the grid. Nothing else did
    either, except the resize path. That gap is the bug.
    """

    def setUp(self):
        self.app = AlphaApp()
        self.addCleanup(lambda: _close(self.app))
        self.app.deiconify()
        self.app.geometry("1350x900")
        for _ in range(6):
            self.app.update_idletasks()
            self.app.update()

    def test_the_layout_puts_its_own_container_back(self):
        self.app.content_wrapper.grid_remove()
        self.app.update_idletasks()
        self.assertFalse(self.app.content_wrapper.winfo_ismapped())
        self.app._apply_content_layout(design_width=self.app._design_width())
        self.app.update_idletasks()
        self.assertTrue(
            self.app.content_wrapper.winfo_ismapped(),
            "the reading grid was laid out inside a frame that is not on screen",
        )

    def test_it_is_a_no_op_when_the_container_is_already_up(self):
        """An ordinary resize must not pay for the repair."""
        before = self.app.content_wrapper.grid_info()
        self.app._apply_content_layout(design_width=self.app._design_width())
        self.app.update_idletasks()
        after = self.app.content_wrapper.grid_info()
        for key in ("row", "column", "sticky"):
            self.assertEqual(str(before.get(key)), str(after.get(key)))
        self.assertTrue(self.app.content_wrapper.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
