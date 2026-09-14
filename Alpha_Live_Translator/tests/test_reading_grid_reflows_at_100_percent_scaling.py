"""At 100 % scaling, hiding or showing a reading pane must re-flow the grid at once.

THE REPORT (2026-09-15, 26.5.24, on the real external monitor)
--------------------------------------------------------------
"Hide the transcript and the translation pane does not resize; drag the window
by hand and it does." The app's own LAYOUT_SNAPSHOT from that run, window at
x=2391 on the 100 % monitor:

    Hide at 890 design px   translation pane 583 px   (should be ~845)
    user drags by 3 px      translation pane 845 px
    Hide again              585 px
    drag                    863 px

Reproduced on that physical monitor: after Hide, even after a full second of
idle, grid column 0's bbox was 9 px while the translation pane stayed at 583.
A 1 px resize made it 851/843. The laptop (150 %) never shows it.

THE CAUSE -- a Tk 8.6 grid behaviour, reproduced in plain tkinter
-----------------------------------------------------------------
When a slave is removed or added, Tk's grid asks the master for its new
requested size and re-arranges on the next idle pass -- but only if that
requested size is more than 1 px in both directions. At 1 px it returns without
re-arranging, and nothing re-arranges until the master gets a <Configure>, i.e.
a resize. Plain tkinter, no CustomTkinter, same result on either monitor:

    pane w=1 h=1 padx=8   master req (18,1) -> (9,1)   STALE
    pane w=1 h=2 padx=8   master req (18,2) -> (9,2)   reflowed
    pane w=1 h=5 padx=0   master req (2,5)  -> (1,5)   STALE
    pane w=2 h=5 padx=0   master req (4,5)  -> (2,5)   reflowed

`_build_content_column` built every reading pane as a 1x1 CTkFrame with
`grid_propagate(False)`, so the weights alone decide the 70/30 split. At 150 %
CustomTkinter scales that to 2 px and the grid re-flows; at 100 % it stays 1 px,
`content_wrapper` requests (9, 1), and the re-flow is skipped.

The ScalingTracker path reaches the same pixel sizes on any machine, so these
tests run it at 1.0 on the real AlphaApp. Nothing here resizes the window
between an action and its measurement: a resize is exactly what hides this bug.
"""

import sys
import time
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
    try:
        root.destroy()
    except Exception:
        pass


def _own_the_default_root(test, root):
    """Point tkinter's default root at the window under test, for this test only.

    CustomTkinter builds a CTkImage's scaled photo with `ImageTk.PhotoImage(...)`
    and no `master`, so it lands on `tkinter._default_root`. Earlier test modules
    leave that pointing at another root; rescaling this window's logo to a new
    scale then fails with "image ... doesn't exist". One root in production, so
    only tests can hit it.
    """
    import tkinter

    previous = getattr(tkinter, "_default_root", None)
    tkinter._default_root = root

    def restore():
        tkinter._default_root = previous

    test.addCleanup(restore)


try:
    import customtkinter as ctk
    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

    _close(ctk.CTk())
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class _AppAtScale(unittest.TestCase):
    SCALE = 1.0

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self._real_dpi = ScalingTracker.__dict__["get_window_dpi_scaling"]
        self.addCleanup(self._restore_dpi)
        self.app = AlphaApp()
        self.addCleanup(_close, self.app)
        _own_the_default_root(self, self.app)
        self.app.deiconify()
        self.settle(1.5)
        self.put_on_monitor(self.SCALE)

    def _restore_dpi(self):
        ScalingTracker.get_window_dpi_scaling = self._real_dpi

    def put_on_monitor(self, scale):
        ScalingTracker.get_window_dpi_scaling = classmethod(lambda cls, w: scale)
        ScalingTracker.window_dpi_scaling_dict[self.app] = scale
        self.app.block_update_dimensions_event()
        ScalingTracker.update_scaling_callbacks_for_window(self.app)
        self.app.unblock_update_dimensions_event()
        self.settle(0.6)

    def settle(self, seconds=0.6):
        """Idle passes only. Never a resize."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.update_idletasks()
            self.app.update()
            time.sleep(0.01)

    def layout(self, design_geometry, transcript_visible):
        self.app.geometry(design_geometry)
        self.settle(1.0)
        self.app._apply_responsive_layout()
        self.settle()
        if bool(self.app._initial_verse_visible) != transcript_visible:
            self.app._initial_verse_visible = transcript_visible
            self.app._sync_transcript_visibility()
        self.settle()

    def widths(self):
        return self.app.content_wrapper.winfo_width(), self.app.left_column.winfo_width()


class TheGridReflowsAtOneHundredPercentTest(_AppAtScale):
    SCALE = 1.0

    def test_hiding_the_transcript_gives_the_translation_the_whole_width(self):
        self.layout("890x650", transcript_visible=True)
        wrapper, left = self.widths()
        self.assertLess(left, wrapper * 0.8, "fixture: the transcript is not taking its share")

        self.app.toggle_initial_verse()  # Hide
        self.settle(1.0)
        wrapper, left = self.widths()
        self.assertGreater(
            left,
            wrapper - 20,
            "after Hide the translation pane stayed at %d of %d px until a resize" % (left, wrapper),
        )

    def test_showing_the_transcript_gives_it_its_share_without_a_resize(self):
        self.layout("890x650", transcript_visible=True)
        self.app.toggle_initial_verse()  # Hide
        self.settle(1.0)
        self.app.toggle_initial_verse()  # Show
        self.settle(1.0)
        wrapper, left = self.widths()
        transcript = self.app.transcript_column.winfo_width()
        self.assertTrue(self.app.transcript_column.winfo_ismapped())
        self.assertLess(left, wrapper * 0.8, "after Show the translation pane kept the whole width")
        self.assertGreater(transcript, wrapper * 0.2, "after Show the transcript pane got %d px" % transcript)

    def test_hiding_in_the_stacked_layout_gives_the_translation_the_height(self):
        self.layout("640x800", transcript_visible=True)
        wrapper_h = self.app.content_wrapper.winfo_height()
        self.assertLess(
            self.app.left_column.winfo_height(), wrapper_h * 0.8, "fixture: not stacked with two rows"
        )

        self.app.toggle_initial_verse()  # Hide
        self.settle(1.0)
        wrapper_h = self.app.content_wrapper.winfo_height()
        left_h = self.app.left_column.winfo_height()
        self.assertGreater(
            left_h,
            wrapper_h - 30,
            "after Hide in the stacked layout the translation kept %d of %d px" % (left_h, wrapper_h),
        )

    def test_every_reading_column_requests_more_than_one_pixel_at_any_scaling(self):
        """The condition itself. CustomTkinter never scales below 0.4."""
        for name in ("left_column", "transcript_column", "right_column"):
            column = getattr(self.app, name, None)
            if column is None:
                continue
            with self.subTest(column=name):
                self.assertGreaterEqual(round(column.cget("width") * 0.4), 2)
                self.assertGreaterEqual(round(column.cget("height") * 0.4), 2)


class TheSplitIsStillSeventyThirtyTest(_AppAtScale):
    """Guard: the tiny seed exists so the weights alone decide the split."""

    SCALE = 1.0

    def test_seventy_thirty_at_one_hundred_percent(self):
        for geometry in ("900x700", "1200x700", "1400x700"):
            with self.subTest(window=geometry):
                self.layout(geometry, transcript_visible=True)
                left = self.app.left_column.winfo_width()
                transcript = self.app.transcript_column.winfo_width()
                share = 100.0 * left / (left + transcript)
                self.assertAlmostEqual(share, 70.0, delta=1.5, msg="%s: %d/%d" % (geometry, left, transcript))


class TheLaptopIsUnchangedTest(_AppAtScale):
    SCALE = 1.5

    def test_hide_and_show_still_reflow_at_one_hundred_fifty_percent(self):
        self.layout("890x650", transcript_visible=True)
        self.app.toggle_initial_verse()  # Hide
        self.settle(1.0)
        wrapper, left = self.widths()
        self.assertGreater(left, wrapper - 30)
        self.app.toggle_initial_verse()  # Show
        self.settle(1.0)
        left = self.app.left_column.winfo_width()
        share = 100.0 * left / (left + self.app.transcript_column.winfo_width())
        self.assertAlmostEqual(share, 70.0, delta=1.5)


if __name__ == "__main__":
    unittest.main()
