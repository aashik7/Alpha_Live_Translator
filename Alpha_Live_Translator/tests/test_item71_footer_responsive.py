"""Footer and window-title tests for item 71 Phase 3b.

Written from a user report against the shipped Phase 1-3 build, with two
screenshots: at a wide width the footer looked right, and shrinking it produced
a row of jammed buttons whose primary label was cut to "Start Listen".

Four causes, each pinned below because each fails silently -- the app keeps
running and only *looks* wrong:

1. **Breakpoints were compared against device pixels.** `winfo_width()` reports
   device pixels and CustomTkinter runs the window at
   `ScalingTracker.get_widget_scaling` (1.5 on the development display), so a
   window created at 900 reports **1350**. Every threshold in the design
   document is a CSS px, so comparing 1350 to 700 or 1050 answered a different
   question and the same physical window laid out differently on a 100% and a
   150% machine.

2. **Fixed pixel widths clipped labels.** `FOOTER_BTN_WIDTH_COMPACT` (88) was
   applied to the primary button below 500. The rendered button font is 20 px,
   so "Start Listening" needs 138 px of glyphs plus padding while 88 buys 132.
   The design sizes these buttons by padding, not by a fixed width.

3. **Four buttons were kept on one row.** A `CTkButton` does not clip itself --
   it requests text + padding -- but once the requested widths exceed the row,
   Tk shrinks every one below its request and the labels are cut. The design
   gives each group `flex: 1 1 100%` below 700 px, i.e. its own row.

4. **The start/stop button was hidden below 800 px** while Clear was kept. The
   design never hides it at any width, and it is the one control a meeting
   cannot proceed without.

What these tests do NOT cover: whether the result looks right, and what happens
*during* a resize. `UI_REDESIGN_PROMPT.md` states a green suite is not evidence
for this file. A live visual check is still required.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _close(root):
    """Destroy a CTk root without leaving its own `after` jobs armed."""
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
    import tkinter.font as tkfont

    import customtkinter as ctk

    _close(ctk.CTk())
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    TK_AVAILABLE = False

from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.ui.theme import (  # noqa: E402
    APP_WINDOW_TITLE,
    DEFAULT_WINDOW_WIDTH,
    FOOTER_ACTIONS_STRETCH_BREAKPOINT,
    FOOTER_STACK_BREAKPOINT,
    LISTEN_BUTTON_LABELS,
)

REAL_METHODS = (
    "_design_px",
    "_design_width",
    "_ui_font",
    "_footer_button_width",
    "_apply_footer_layout",
    "_primary_button_config",
    "_secondary_button_config",
    "create_footer",
)

BUTTON_NAMES = ("listen", "copy", "export", "clear")


def _footer(design_width):
    """Build the real footer at a real width and measure every button.

    A fresh root per width: this environment lays out a first paint reliably
    but does not reflow dependably after widgets are re-gridded, so each width
    is measured on its own paint rather than by resizing one window.
    """
    root = ctk.CTk()
    root.geometry(f"{design_width}x650")
    root._font_cache = {}
    for name in REAL_METHODS:
        setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
    for command in (
        "toggle_listening",
        "copy_translation_to_clipboard",
        "export_transcript_placeholder",
        "clear_text",
    ):
        setattr(root, command, lambda: None)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    root.create_footer()
    root.footer_frame.grid_configure(row=1)
    for _ in range(6):
        root.update_idletasks()
        root.update()
    root._apply_footer_layout(root._design_width())
    for _ in range(8):
        root.update_idletasks()
        root.update()

    measured = {}
    for name, button in zip(BUTTON_NAMES, root._footer_buttons):
        font = tkfont.Font(root=root, font=button._text_label.cget("font"))
        measured[name] = {
            "x": button.winfo_rootx(),
            "y": button.winfo_rooty(),
            "w": button.winfo_width(),
            "h": button.winfo_height(),
            "mapped": bool(button.winfo_ismapped()),
            "text": button.cget("text"),
            "text_px": font.measure(button.cget("text")),
        }
    measured["_design_width"] = root._design_width()
    measured["_footer_w"] = root.footer_frame.winfo_width()
    measured["_footer_x"] = root.footer_frame.winfo_rootx()
    _close(root)
    return measured


def _overlaps(a, b):
    return (
        a["x"] < b["x"] + b["w"]
        and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"]
        and b["y"] < a["y"] + a["h"]
    )


def _is_two_rows(m):
    listen_bottom = m["listen"]["y"] + m["listen"]["h"]
    return all(m[n]["y"] >= listen_bottom - 2 for n in ("copy", "export", "clear"))


WIDTHS = (400, 430, 500, 600, 699, 700, 800, 900, 1050, 1200, 1400)


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestNothingIsEverClippedOrOverlapping(unittest.TestCase):
    """The user's points 1 and 3."""

    def test_no_label_is_ever_cut_off(self):
        for width in WIDTHS:
            m = _footer(width)
            for name in BUTTON_NAMES:
                b = m[name]
                if not b["mapped"]:
                    continue
                self.assertLessEqual(
                    b["text_px"],
                    b["w"],
                    f"at {width}px the {name} button shows {b['text']!r}, which "
                    f"needs {b['text_px']}px, in {b['w']}px",
                )

    def test_no_two_buttons_ever_overlap(self):
        for width in WIDTHS:
            m = _footer(width)
            for i, first in enumerate(BUTTON_NAMES):
                for second in BUTTON_NAMES[i + 1:]:
                    if not (m[first]["mapped"] and m[second]["mapped"]):
                        continue
                    self.assertFalse(
                        _overlaps(m[first], m[second]),
                        f"at {width}px {first} and {second} overlap: "
                        f"{m[first]} vs {m[second]}",
                    )

    def test_nothing_extends_past_the_footer(self):
        for width in WIDTHS:
            m = _footer(width)
            for name in BUTTON_NAMES:
                b = m[name]
                if not b["mapped"]:
                    continue
                right_edge = (b["x"] - m["_footer_x"]) + b["w"]
                self.assertLessEqual(
                    right_edge,
                    m["_footer_w"] + 2,
                    f"at {width}px the {name} button ends at {right_edge} in a "
                    f"footer {m['_footer_w']} wide",
                )


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestStartStopIsNeverHidden(unittest.TestCase):
    """The user's point 2.

    The old hamburger branch called `left_controls_frame.grid_remove()` and
    gridded `clear_btn` alone, so below 800px the footer offered Clear and no
    way to start or stop a session except through the hamburger menu.
    """

    def test_every_footer_button_is_visible_at_every_width(self):
        for width in WIDTHS:
            m = _footer(width)
            for name in BUTTON_NAMES:
                self.assertTrue(
                    m[name]["mapped"], f"the {name} button is hidden at {width}px"
                )

    def test_the_primary_button_is_the_one_that_fills_a_narrow_footer(self):
        m = _footer(420)
        self.assertTrue(_is_two_rows(m))
        self.assertGreater(
            m["listen"]["w"],
            m["clear"]["w"],
            "the start/stop button is the primary control and must dominate "
            "the narrow footer, not Clear",
        )


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestTheDesignsTwoFooterBreakpoints(unittest.TestCase):
    """The user's point 4 -- follow the design document."""

    def test_one_row_at_and_above_the_stack_breakpoint(self):
        for width in (FOOTER_STACK_BREAKPOINT, 800, 1050, 1400):
            m = _footer(width)
            self.assertFalse(
                _is_two_rows(m), f"the footer should be a single row at {width}px"
            )

    def test_two_rows_below_it(self):
        """`@media (max-width: 700px)` gives both groups `flex: 1 1 100%`."""
        for width in (400, 500, 600, FOOTER_STACK_BREAKPOINT - 1):
            m = _footer(width)
            self.assertTrue(
                _is_two_rows(m), f"the footer should wrap to two rows at {width}px"
            )

    def test_the_primary_button_stretches_only_when_stacked(self):
        stacked = _footer(600)
        single = _footer(900)
        self.assertGreater(
            stacked["listen"]["w"],
            single["listen"]["w"] * 2,
            "stacked, the start/stop button takes the whole row "
            "(`.atf-stop-button { flex: 1 1 auto }`)",
        )

    def test_action_buttons_share_the_row_below_430(self):
        """`@media (max-width: 430px)` gives them `flex: 1 1 auto`, which grows
        each by an equal amount from its natural size -- not to equal widths."""
        narrow = _footer(FOOTER_ACTIONS_STRETCH_BREAKPOINT - 10)
        wider = _footer(FOOTER_ACTIONS_STRETCH_BREAKPOINT + 10)
        growth = {
            name: narrow[name]["w"] - wider[name]["w"]
            for name in ("copy", "export", "clear")
        }
        self.assertTrue(
            all(value > 0 for value in growth.values()),
            f"every action button should grow below the breakpoint: {growth}",
        )
        self.assertLessEqual(
            max(growth.values()) - min(growth.values()),
            4,
            f"growth should be shared equally: {growth}",
        )

    def test_actions_sit_at_their_natural_width_between_430_and_700(self):
        between = _footer(600)
        single = _footer(900)
        for name in ("copy", "export", "clear"):
            self.assertEqual(
                between[name]["w"],
                single[name]["w"],
                f"{name} should keep its natural width at 600px",
            )


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestWidthsComeFromTheTextNotAConstant(unittest.TestCase):
    def test_a_longer_label_gets_a_wider_button(self):
        m = _footer(900)
        self.assertGreater(
            m["copy"]["w"],
            m["clear"]["w"],
            "'Copy Translation' must be wider than 'Clear'; a shared constant "
            "is what clipped the longer labels",
        )

    def test_the_primary_button_is_sized_for_its_widest_label(self):
        """Start/Stop must not resize the moment a session begins."""
        root = _host_only()
        try:
            width = root._footer_button_width(LISTEN_BUTTON_LABELS, 16)
            for label in LISTEN_BUTTON_LABELS:
                self.assertGreaterEqual(
                    width, root._footer_button_width([label], 16)
                )
        finally:
            _close(root)

    def test_a_measurement_failure_falls_back_instead_of_raising(self):
        class _Host:
            _footer_button_width = AlphaApp._footer_button_width

        self.assertIsInstance(_Host()._footer_button_width(["x"], 16), int)


def _host_only():
    root = ctk.CTk()
    root._font_cache = {}
    for name in ("_design_px", "_design_width", "_ui_font", "_footer_button_width"):
        setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
    return root


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestBreakpointsAreMeasuredInDesignPixels(unittest.TestCase):
    """The user's point 1, at its root.

    Every threshold in the design document is a CSS px. `winfo_width()` is
    device pixels.
    """

    def test_design_width_divides_out_display_scaling(self):
        for asked in (400, 700, 900, 1200):
            root = _host_only()
            try:
                root.geometry(f"{asked}x650")
                for _ in range(4):
                    root.update_idletasks()
                    root.update()
                self.assertEqual(
                    root._design_width(),
                    asked,
                    f"a window created at {asked} reported "
                    f"{root.winfo_width()} device px",
                )
            finally:
                _close(root)

    def test_it_falls_back_to_the_creation_width_before_the_first_paint(self):
        class _Host:
            _design_width = AlphaApp._design_width

            def winfo_width(self):
                return 1  # what Tk reports before the window is mapped

        self.assertEqual(_Host()._design_width(), DEFAULT_WINDOW_WIDTH)

    def test_a_broken_scaling_lookup_does_not_raise(self):
        class _Host:
            _design_width = AlphaApp._design_width

        self.assertEqual(_Host()._design_width(), DEFAULT_WINDOW_WIDTH)


class TestWindowTitle(unittest.TestCase):
    """The user's point 6. Runs with or without a display."""

    def test_the_title_carries_no_version_number(self):
        self.assertEqual(APP_WINDOW_TITLE, "Alpha Meeting Assistant")
        self.assertNotIn("V", APP_WINDOW_TITLE.replace("Version", ""))

    def test_app_version_is_still_stamped_into_diagnostics(self):
        """Stripping it from the title must not strip it from the evidence
        trail -- run ids, log filenames and artifact manifests are how a
        delivered run is traced back to the build it came from.

        Asserted on the identity record's shape rather than by minting one:
        `create_run_identity_once` installs a process-wide singleton, and this
        repo has already been bitten by tests that leave global state behind.
        """
        from alpha.constants import APP_VERSION
        from alpha.utils.run_identity import RunIdentity

        self.assertTrue(APP_VERSION, "APP_VERSION must not be emptied")
        self.assertIn(
            "app_version",
            RunIdentity.__dataclass_fields__,
            "every run must still record which build produced it",
        )
        self.assertNotIn(APP_VERSION, APP_WINDOW_TITLE)


if __name__ == "__main__":
    unittest.main()
