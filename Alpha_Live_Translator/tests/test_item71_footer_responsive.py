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

3. **Four buttons were kept on one unwrappable row.** A `CTkButton` does not
   clip itself -- it requests text + padding -- but once the requested widths
   exceed the row, Tk shrinks every one below its request and the labels are
   cut. The design gives each group `flex: 1 1 100%` below 700 px, which in a
   `nowrap` footer means the two groups shrink to half the row each, and
   `.atf-action-group { flex-wrap: wrap }` lets the action buttons flow onto
   extra lines inside their half instead of being squeezed.

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
    FOOTER_STACK_BREAKPOINT,
    LISTEN_BUTTON_LABELS,
)

REAL_METHODS = (
    "_design_px",
    "_design_width",
    "_ui_font",
    "_footer_button_width",
    "_apply_footer_layout",
    "_sync_hamburger_action_buttons",
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


def _groups_share_a_row(m):
    """True when the start/stop button and the action group sit side by side.

    The two groups never stack: `.atf-footer` carries no `flex-wrap` anywhere
    in the design, so `flex: 1 1 100%` on both makes them shrink against each
    other on one row rather than wrap onto two.
    """
    listen = m["listen"]
    return any(
        m[n]["y"] < listen["y"] + listen["h"] and listen["y"] < m[n]["y"] + m[n]["h"]
        for n in ("copy", "export", "clear")
    )


def _action_lines(m):
    """How many lines the action buttons occupy, counting only mapped ones.

    Unmapped buttons still report a stale `winfo_rooty()` from their last
    placement rather than raising, so counting them here would make a button
    that left the footer for the hamburger menu look like it was still on a
    line of its own.
    """
    mapped = [n for n in ("copy", "export", "clear") if m[n]["mapped"]]
    return len({m[n]["y"] for n in mapped})


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

    def test_start_stop_is_visible_in_the_footer_at_every_width(self):
        """The one button this class's docstring is actually about. The
        other three are checked separately -- at 400/430/500 they now leave
        the footer for the hamburger menu on purpose (Phase 3d,
        `TestActionsMoveToTheHamburgerRatherThanWrap`), which is not the
        "hidden with no way to start or stop a session" bug this guards."""
        for width in WIDTHS:
            m = _footer(width)
            self.assertTrue(
                m["listen"]["mapped"], f"start/stop is hidden at {width}px"
            )

    def test_the_primary_button_is_the_one_that_fills_a_narrow_footer(self):
        # 380: below where the action group still fits one line, so
        # Start/Stop is the footer's ONLY content and spans the full row --
        # there is no second group left to share it with.
        m = _footer(380)
        self.assertFalse(m["copy"]["mapped"])
        self.assertGreater(
            m["listen"]["w"],
            m["_footer_w"] * 0.8,
            "with the action group moved to the hamburger menu, start/stop "
            "should fill the row rather than sit at its old half-row width",
        )


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestTheDesignsTwoFooterBreakpoints(unittest.TestCase):
    """The user's point 4 -- follow the design document."""

    def test_the_two_groups_always_share_one_row(self):
        """The footer has no `flex-wrap` at any width, so the groups never
        stack -- above 700 they sit at their natural widths with an elastic
        gap between them, below 700 they shrink against each other.

        Widths below ~560 are excluded here: at those widths the action group
        no longer fits its half of the row on ONE line, and per
        `TestActionsMoveToTheHamburgerRatherThanWrap` below it moves to the
        hamburger menu instead of wrapping -- there is no second group left in
        the footer to share a row with."""
        for width in (600, FOOTER_STACK_BREAKPOINT - 1,
                      FOOTER_STACK_BREAKPOINT, 800, 1050, 1400):
            m = _footer(width)
            self.assertTrue(
                _groups_share_a_row(m),
                f"the two footer groups should share one row at {width}px",
            )

    def test_the_primary_button_never_stretches(self):
        """The design's `.atf-stop-button { flex: 1 1 auto }` stretches
        Start/Stop to half the row below 700px. Phase 3e removes that by
        explicit user request: it made Start/Stop visibly bigger than the
        other three footer buttons. Its width must stay the same at every
        width where the action group is ALSO in the footer -- 410 design px
        (just past where the group still fits one line) and 900 both share a
        row with it, so its own width should be identical, not larger at the
        narrower one."""
        narrow = _footer(410)
        wide = _footer(900)
        self.assertTrue(narrow["copy"]["mapped"], "test premise: not hamburger-routed")
        self.assertEqual(
            narrow["listen"]["w"],
            wide["listen"]["w"],
            "start/stop must not resize just because the footer narrowed",
        )

    def test_the_primary_button_fills_the_row_only_once_alone_in_it(self):
        """The full-row fill is real, but it is not a stretch reacting to
        width -- it only happens once the action group has moved to the
        hamburger menu and there is nothing left to share the row with."""
        alone = _footer(380)
        shared = _footer(410)
        self.assertFalse(alone["copy"]["mapped"])
        self.assertTrue(shared["copy"]["mapped"])
        self.assertGreater(
            alone["listen"]["w"],
            shared["listen"]["w"],
            "start/stop should only be wider when it is the row's only content",
        )

    def test_action_buttons_never_stretch(self):
        """The design's `@media (max-width: 430px)` gives the action buttons
        `flex: 1 1 auto`. Phase 3e removes that too, by the same request as
        Start/Stop: their width must stay the natural, text-driven size at
        every width where they are in the footer at all, never grown to fill
        the row."""
        narrow = _footer(410)
        wide = _footer(900)
        for name in ("copy", "export", "clear"):
            self.assertTrue(narrow[name]["mapped"], "test premise")
            self.assertEqual(
                narrow[name]["w"],
                wide[name]["w"],
                f"{name} must not resize just because the footer narrowed",
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
class TestActionsMoveToTheHamburgerRatherThanWrap(unittest.TestCase):
    """Phase 3d. `_apply_footer_layout` used to let the action group wrap onto
    a second line inside its half of the row -- Copy alone on top, Export and
    Clear below -- whenever it did not fit on one. A user report, with a
    screenshot, showed exactly that shape and called it broken.

    Measured, the wrap was not confined to a narrow band near any one
    threshold: `_footer_button_width` sizes buttons from their label text, and
    "Copy Translation" alone is wider than half the row from 400 design px up
    to ~550. A single fixed cutoff (`HAMBURGER_ACTIONS_BREAKPOINT`, tried
    first) either left the wrap active well past it or moved the cutoff high
    enough to swallow widths where one line was in fact possible. The decision
    is now the ACTUAL computed line count -- more than one line moves the
    whole group to the hamburger menu instead of wrapping.

    The threshold itself later moved (Phase 3e) when the available-width
    calculation was corrected to subtract Start/Stop's REAL width rather than
    assume it takes half the row; the exact number below is a re-measurement
    of where this environment's font metrics put the crossover, not a
    constant the code exposes.
    """

    def test_the_wrap_is_gone_across_the_whole_band_it_used_to_appear_in(self):
        for width in range(350, 410, 10):
            m = _footer(width)
            for name in ("copy", "export", "clear"):
                self.assertFalse(
                    m[name]["mapped"],
                    f"{name} should have left the footer for the hamburger "
                    f"menu at {width}px, not be wrapped inside it",
                )
            self.assertTrue(m["listen"]["mapped"])

    def test_one_line_stays_in_the_footer_once_it_fits(self):
        m = _footer(900)
        for name in ("copy", "export", "clear"):
            self.assertTrue(m[name]["mapped"])
        self.assertEqual(_action_lines(m), 1)


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
