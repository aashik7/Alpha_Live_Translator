"""Layout tests for item 71's reading grid.

Three behaviours the user asked for, pinned as measured geometry rather
than as assertions about the code:

* translation is the primary pane and the original transcript sits beside
  it at **70/30**, matching the design's
  `.atf-reading-grid { grid-template-columns: minmax(0, 70fr) 8px minmax(220px, 30fr) }`
* the transcript pane and the meeting summary are both **hidden until
  their button is pressed** -- the design's default state is
  `.atf-original-hidden`, a single full-width translation column
* below the stack breakpoint the grid becomes rows, matching the design's
  `@media (max-width: 700px) { grid-template-columns: 1fr }`

**Why every case builds a fresh root.** This environment reflows the grid
on first paint but NOT after a later `grid_remove()`: removing a pane
leaves its sibling's width unchanged, and `winfo_ismapped()` stops
tracking. Verified directly -- two 50/50 panes in a 600 px window measure
300/300, and after removing one the other still measures 300 instead of
600. So a single root cannot be used to measure a toggle; each
configuration is measured on its own first paint, which is accurate.

That limitation is also why these tests cannot cover the toggle
*transition*. `UI_REDESIGN_PROMPT.md` already states that automated tests
cannot catch a UI regression here and requires a live visual check; these
pin the geometry contract, not the interaction.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tkinter as tk

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    TK_AVAILABLE = False

from alpha.ui.main_window import (  # noqa: E402
    CONTENT_PRIMARY_WEIGHT,
    CONTENT_REFERENCE_WEIGHT,
    CONTENT_STACK_BREAKPOINT,
    AlphaApp,
)


def _measure(mode, *, transcript, summary, width=1200, height=700):
    """Lay the real `_apply_content_layout` out once and measure the result."""
    root = tk.Tk()
    try:
        root.geometry(f"{width}x{height}")
        wrapper = tk.Frame(root)
        wrapper.pack(fill="both", expand=True)
        left = tk.Frame(wrapper)
        transcript_col = tk.Frame(wrapper)
        right = tk.Frame(wrapper)

        class _Host:
            _apply_content_layout = AlphaApp._apply_content_layout

        host = _Host()
        host.content_wrapper = wrapper
        host.left_column = left
        host.transcript_column = transcript_col
        host.right_column = right
        host._initial_verse_visible = transcript
        host.summary_panel_visible = summary

        host._apply_content_layout(mode)
        for _ in range(4):
            root.update_idletasks()
            root.update()
        return {
            "translation_w": left.winfo_width(),
            "transcript_w": transcript_col.winfo_width(),
            "summary_w": right.winfo_width(),
            "translation_h": left.winfo_height(),
            "transcript_h": transcript_col.winfo_height(),
        }
    finally:
        root.destroy()


HIDDEN_W = 1  # an ungridded Tk frame reports width 1


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestReadingGridGeometry(unittest.TestCase):
    def test_default_shows_translation_alone_full_width(self):
        m = _measure("wide", transcript=False, summary=False)
        self.assertEqual(m["transcript_w"], HIDDEN_W, "transcript must start hidden")
        self.assertEqual(m["summary_w"], HIDDEN_W, "summary must start hidden")
        self.assertGreater(
            m["translation_w"], 1000, "translation should take the whole width"
        )

    def test_transcript_pane_takes_thirty_percent_beside_translation(self):
        m = _measure("wide", transcript=True, summary=False)
        total = m["translation_w"] + m["transcript_w"]
        share = 100 * m["transcript_w"] / total
        self.assertAlmostEqual(
            share,
            CONTENT_REFERENCE_WEIGHT,
            delta=2,
            msg=f"expected ~30% for the transcript, got {share:.1f}% ({m})",
        )
        self.assertGreater(
            m["translation_w"],
            m["transcript_w"],
            "translation is the primary pane and must be the wider one",
        )

    def test_summary_uses_the_same_reference_width(self):
        m = _measure("wide", transcript=False, summary=True)
        total = m["translation_w"] + m["summary_w"]
        share = 100 * m["summary_w"] / total
        self.assertAlmostEqual(share, CONTENT_REFERENCE_WEIGHT, delta=2, msg=str(m))

    def test_both_reference_panes_can_be_open_together(self):
        m = _measure("wide", transcript=True, summary=True)
        for key in ("transcript_w", "summary_w"):
            self.assertGreater(m[key], HIDDEN_W, f"{key} should be visible ({m})")
        self.assertGreater(
            m["translation_w"],
            m["transcript_w"],
            "translation stays the widest pane even with both open",
        )


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestResponsiveStacking(unittest.TestCase):
    def test_narrow_windows_stack_the_panes_into_rows(self):
        narrow = CONTENT_STACK_BREAKPOINT - 100
        m = _measure("compact", transcript=True, summary=False, width=narrow)
        # Stacked means both panes span the full width and split the height.
        self.assertAlmostEqual(m["translation_w"], m["transcript_w"], delta=2)
        self.assertGreater(
            m["translation_h"],
            m["transcript_h"],
            "translation keeps the larger share of height when stacked",
        )

    def test_medium_also_stacks(self):
        m = _measure("medium", transcript=True, summary=False, width=800)
        self.assertAlmostEqual(m["translation_w"], m["transcript_w"], delta=2)


class TestLayoutConstants(unittest.TestCase):
    """These run with or without a display."""

    def test_weights_match_the_design_ratio(self):
        self.assertEqual(CONTENT_PRIMARY_WEIGHT, 70)
        self.assertEqual(CONTENT_REFERENCE_WEIGHT, 30)

    def test_stack_breakpoint_matches_the_designs_media_query(self):
        self.assertEqual(CONTENT_STACK_BREAKPOINT, 700)

    def test_the_old_row_weight_helper_no_longer_writes_geometry(self):
        """It used to set `left_column` row weights. Those rows no longer hold
        both panes, so writing them would be a silent no-op -- the body was
        removed rather than left looking effective."""

        class _Host:
            _apply_left_column_panel_weights = (
                AlphaApp._apply_left_column_panel_weights
            )

            def __init__(self):
                self.left_column = self

            def grid_rowconfigure(self, *a, **k):  # pragma: no cover
                raise AssertionError("must not configure rows any more")

        _Host()._apply_left_column_panel_weights()


if __name__ == "__main__":
    unittest.main()
