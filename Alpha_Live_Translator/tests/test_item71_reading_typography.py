"""Typography tests for item 71's reading panes.

The design document is CSS. Three measured facts decide how its numbers reach
Tk, and each one is pinned below because getting any of them wrong is silent --
the text still renders, just at the wrong size or with the spacing dropped.

1. **A CustomTkinter font size is pixels, not points.**
   `CTkFont(family="Segoe UI", size=14)` and `tkinter.font.Font(size=-14)`
   produce identical metrics on 5.2.2. So the design's `font-size: 18px` is
   `size=18`, and converting px to points (18 * 0.75 = 14) would render the
   client's transcript a quarter smaller than specified.

2. **CustomTkinter does not scale a raw `tk.Text`.** CTk multiplies its own
   widgets by `ScalingTracker.get_widget_scaling`; the two reading panes are
   raw `tk.Text` widgets and receive none of it. Measured on a 150% display:
   one `CTkFont(size=16)` object renders at 18 pt inside a `CTkLabel` and at
   12 pt inside the `tk.Text`. That is a pre-existing defect -- the app's
   primary content was a third smaller than the chrome around it on exactly
   the kind of laptop this ships to -- and `_design_px` is the fix.

3. **`spacing1` is resolved from the tags on the first character of a display
   line.** Every rendered entry begins with the `speaker_label` tag, so
   paragraph spacing configured on the `body` tag would apply to nothing on
   the lines it was written for. It goes on the widget instead.

Not covered here: whether the result *looks* right. `UI_REDESIGN_PROMPT.md`
states that automated tests cannot catch a UI regression in this app and that
a live visual check is required; these pin the contract, not the appearance.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def _close(root):
    """Destroy a CTk root without leaving its own `after` jobs to fire.

    `CTk.__init__` schedules `windows_set_titlebar_icon` on a 200 ms timer.
    Destroying sooner than that leaves the callback armed against a dead
    interpreter, and Tk prints `invalid command name ...` to stderr for each
    one. Harmless, but it buries real errors in the full-suite output.
    """
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
    import tkinter as tk
    import tkinter.font as tkfont

    import customtkinter as ctk

    _close(ctk.CTk())
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    TK_AVAILABLE = False

from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.ui.theme import (  # noqa: E402
    FONT_FAMILY,
    INTERIM_FONT_PX,
    PANE_BG,
    READING_TYPOGRAPHY,
    SPEAKER_LABEL_FONT_PX,
)

HOST_METHODS = (
    "_design_px",
    "_ui_font",
    "_scaled_design_font",
    "_apply_reading_typography",
    "_refresh_reading_typography",
)


def _host(root):
    """Bind the real methods to a real CTk root.

    `ScalingTracker.get_widget_scaling` walks the widget's toplevel, so a stub
    object silently falls into `_design_px`'s except branch and reports a
    scaling of 1.0 -- which would make every assertion below pass for the
    wrong reason on a scaled display.
    """
    root._font_cache = {}
    for name in HOST_METHODS:
        setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
    return root


def _styled(root, role, stacked):
    widget = tk.Text(root, wrap="word", width=40, height=8)
    widget._scrollbar = None
    widget._pane_frame = None
    # `_create_styled_text` defines these two before handing the widget over,
    # and it defines them with a colour only. Reproduce that starting point so
    # what the assertions see is what `_apply_reading_typography` did.
    widget.tag_configure("body", foreground="#F8FAFC")
    widget.tag_configure("interim", foreground="#64748B")
    root._apply_reading_typography(widget, role, stacked)
    return widget


def _linespace(root, spec):
    """Measure a font spec, whatever form Tk hands back.

    `cget("font")` and `tag_cget(tag, "font")` return the font's *name* as a
    string, not the object that was passed in, so the assertions below compare
    rendered metrics rather than reading a size attribute off a CTkFont. That
    is also the stronger check: it proves what Tk actually draws.
    """
    return tkfont.Font(root=root, font=spec).metrics("linespace")


def _linespace_at_px(root, px):
    """Metrics of the same family requested explicitly in pixels."""
    return tkfont.Font(root=root, family=FONT_FAMILY, size=-px).metrics("linespace")


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestCustomTkinterFontUnits(unittest.TestCase):
    """Fact 1. If this ever changes, every size in READING_TYPOGRAPHY is wrong."""

    def test_a_ctk_font_size_is_pixels_not_points(self):
        root = ctk.CTk()
        try:
            ctk_font = ctk.CTkFont(family="Segoe UI", size=14)
            as_pixels = tkfont.Font(root=root, family="Segoe UI", size=-14)
            as_points = tkfont.Font(root=root, family="Segoe UI", size=14)
            self.assertEqual(
                ctk_font.metrics("linespace"), as_pixels.metrics("linespace")
            )
            self.assertNotEqual(
                ctk_font.metrics("linespace"), as_points.metrics("linespace")
            )
        finally:
            _close(root)


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestDisplayScaling(unittest.TestCase):
    """Fact 2."""

    def test_design_px_follows_the_display_scaling_factor(self):
        root = _host(ctk.CTk())
        try:
            scaling = ctk.ScalingTracker.get_widget_scaling(root)
            for css_px in (11, 12, 14, 18, 20):
                self.assertEqual(
                    root._design_px(css_px), max(1, round(css_px * scaling))
                )
        finally:
            _close(root)

    def test_a_broken_scaling_lookup_falls_back_to_unscaled(self):
        """The fallback must reproduce today's behaviour, not raise: this runs
        while the window is being built."""

        class _Host:
            _design_px = AlphaApp._design_px

        self.assertEqual(_Host()._design_px(18), 18)

    def test_the_pane_font_is_scaled_the_same_way_as_ctk_chrome(self):
        root = _host(ctk.CTk())
        try:
            scaling = ctk.ScalingTracker.get_widget_scaling(root)
            spec = READING_TYPOGRAPHY[("translation", False)]
            widget = _styled(root, "translation", False)
            self.assertEqual(
                _linespace(root, widget.cget("font")),
                _linespace_at_px(root, max(1, round(spec["font_px"] * scaling))),
            )
            if scaling != 1.0:
                self.assertNotEqual(
                    _linespace(root, widget.cget("font")),
                    _linespace_at_px(root, spec["font_px"]),
                    "an unscaled pane font is the defect this fixes",
                )
        finally:
            _close(root)


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestParagraphSpacingReachesEveryEntry(unittest.TestCase):
    """Fact 3."""

    def test_spacing_is_on_the_widget_not_the_body_tag(self):
        root = _host(ctk.CTk())
        try:
            widget = _styled(root, "translation", False)
            for option in ("spacing1", "spacing2", "spacing3"):
                self.assertGreater(
                    int(widget.cget(option)),
                    0,
                    f"{option} must be set on the widget so it survives a line "
                    "that starts with the speaker_label tag",
                )
                self.assertIn(
                    str(widget.tag_cget("body", option)),
                    ("", "0"),
                    f"{option} on the body tag would be dead weight -- Tk reads "
                    "it from the first character of the display line",
                )
        finally:
            _close(root)

    def test_an_entry_beginning_with_a_speaker_label_still_gets_its_spacing(self):
        root = _host(ctk.CTk())
        try:
            widget = _styled(root, "translation", False)
            widget.pack()
            widget.insert("end", "Speaker 1: ", "speaker_label")
            widget.insert("end", "One short line.\n", "body")
            root.update_idletasks()
            root.update()
            height = widget.dlineinfo("1.0")[3]
            font_linespace = _linespace(root, widget.cget("font"))
            expected_padding = int(widget.cget("spacing1")) + int(
                widget.cget("spacing3")
            )
            self.assertGreaterEqual(
                height,
                font_linespace + expected_padding,
                "the entry rhythm was dropped on a speaker-labelled line",
            )
        finally:
            _close(root)


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestTagsThatWouldOtherwiseBeLeftBehind(unittest.TestCase):
    def test_speaker_label_is_configured_up_front(self):
        """Four sites build this tag lazily with a hardcoded
        `("Segoe UI", 12, "bold")` -- raw Tk, so 12 *points* and unscaled. Each
        is guarded by `if tag not in box.tag_names()`, so configuring it here
        is what keeps it from being left at a size the body no longer uses."""
        root = _host(ctk.CTk())
        try:
            widget = _styled(root, "translation", False)
            self.assertIn("speaker_label", widget.tag_names())
            self.assertEqual(
                _linespace(root, widget.tag_cget("speaker_label", "font")),
                _linespace_at_px(root, root._design_px(SPEAKER_LABEL_FONT_PX)),
            )
            self.assertEqual(
                tkfont.Font(
                    root=root, font=widget.tag_cget("speaker_label", "font")
                ).cget("weight"),
                "bold",
            )
        finally:
            _close(root)

    def test_the_interim_preview_is_smaller_and_muted(self):
        root = _host(ctk.CTk())
        try:
            widget = _styled(root, "translation", False)
            interim = _linespace(root, widget.tag_cget("interim", "font"))
            self.assertEqual(
                interim, _linespace_at_px(root, root._design_px(INTERIM_FONT_PX))
            )
            self.assertLess(interim, _linespace(root, widget.cget("font")))
            self.assertEqual(
                widget.tag_cget("interim", "foreground").lower(), "#64748b"
            )
        finally:
            _close(root)

    def test_a_placeholder_font_tuple_is_rebuilt_at_scale_keeping_its_slant(self):
        root = _host(ctk.CTk())
        try:
            scaled = root._scaled_design_font(("Segoe UI", 13, "italic"))
            self.assertEqual(scaled.cget("size"), root._design_px(13))
            self.assertEqual(scaled.cget("slant"), "italic")
        finally:
            _close(root)

    def test_a_malformed_font_spec_is_returned_unchanged(self):
        root = _host(ctk.CTk())
        try:
            self.assertIsNone(root._scaled_design_font(None))
        finally:
            _close(root)


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestResponsiveTypography(unittest.TestCase):
    def test_stacking_switches_to_the_designs_mobile_type_scale(self):
        root = _host(ctk.CTk())
        try:
            wide = _styled(root, "translation", False)
            narrow = _styled(root, "translation", True)
            self.assertGreater(
                int(wide.cget("padx")),
                int(narrow.cget("padx")),
                "the design tightens content padding below 700px",
            )
            self.assertGreaterEqual(
                _linespace(root, wide.cget("font")),
                _linespace(root, narrow.cget("font")),
            )
        finally:
            _close(root)

    def test_refresh_is_skipped_when_the_branch_has_not_changed(self):
        """It runs on every resize tick, and reconfiguring a font forces Tk to
        re-measure and re-wrap the entire widget."""
        root = _host(ctk.CTk())
        applied = []
        try:
            root.translated_verse_box = None
            root.initial_verse_box = None
            root._reading_typography_stacked = None
            root._apply_reading_typography = lambda *a, **k: applied.append(a)

            root._refresh_reading_typography("wide")
            self.assertEqual(len(applied), 2, "first call must apply both panes")
            root._refresh_reading_typography("wide")
            self.assertEqual(len(applied), 2, "same branch must be a no-op")
            root._refresh_reading_typography("compact")
            self.assertEqual(len(applied), 4, "crossing the breakpoint re-applies")
        finally:
            _close(root)


class TestDesignValues(unittest.TestCase):
    """These run with or without a display."""

    def test_every_pane_and_breakpoint_has_a_spec(self):
        for role in ("translation", "transcript"):
            for stacked in (False, True):
                self.assertIn((role, stacked), READING_TYPOGRAPHY)

    def test_sizes_match_the_design_stylesheet(self):
        # .atf-translation-entry p { font-size: 18px; line-height: 1.58 }
        self.assertEqual(READING_TYPOGRAPHY[("translation", False)]["font_px"], 18)
        self.assertEqual(READING_TYPOGRAPHY[("translation", False)]["line_height"], 1.58)
        # .atf-mobile-preview .atf-translation-entry p { font-size: 17px }
        self.assertEqual(READING_TYPOGRAPHY[("translation", True)]["font_px"], 17)
        # .atf-original-entry p { font-size: 14px; line-height: 1.55 }
        self.assertEqual(READING_TYPOGRAPHY[("transcript", False)]["font_px"], 14)
        self.assertEqual(READING_TYPOGRAPHY[("transcript", True)]["font_px"], 14)
        # .atf-incoming-entry { font-size: 12px } / .atf-entry-meta { font-size: 11px }
        self.assertEqual(INTERIM_FONT_PX, 12)
        self.assertEqual(SPEAKER_LABEL_FONT_PX, 11)

    def test_translation_reads_larger_than_the_reference_transcript(self):
        """The whole point of the redesign: translation is the primary pane."""
        for stacked in (False, True):
            self.assertGreater(
                READING_TYPOGRAPHY[("translation", stacked)]["font_px"],
                READING_TYPOGRAPHY[("transcript", stacked)]["font_px"],
            )

    def test_the_two_panes_are_tinted_apart(self):
        self.assertNotEqual(PANE_BG["translation"], PANE_BG["transcript"])


if __name__ == "__main__":
    unittest.main()
