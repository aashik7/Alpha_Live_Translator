"""Regression tests for item 71 Phase 3e, filed against a live user report
with two screenshots and a follow-up on the footer.

Three defects, each reproduced against the real widgets before being fixed.

1. **The header's mode-switching compared design-px thresholds against
   device px.** `_apply_responsive_layout` used to read `self.winfo_width()`
   directly and feed it to `_get_layout_mode`, whose thresholds
   (`LAYOUT_WIDE_BREAKPOINT` 1050, `LAYOUT_HAMBURGER_BREAKPOINT` 800) are
   design px -- the same class of bug the footer and reading grid had earlier
   in item 71, just not yet applied to the header's own path. Measured on
   this 150% display: a window created at 900 design px reports 1350 device
   px, so under the old code `_get_layout_mode(1350)` returned **"wide"**
   (1350 >= 1050) for a window that should have been "medium" -- the header
   applied NONE of `_pack_header_controls`'s narrowing (full 155px combo
   boxes, full "Meeting Summary" text) at a width that could not actually fit
   it, which is what pushed the swap toggle and summary button off-screen in
   the user's second screenshot. Fixed by computing `design_width` once via
   `_design_width()` and threading it through the whole chain instead of
   `winfo_width()`.

2. **Even with that fixed, "Japanese"/"English" still did not fit "medium"
   mode's existing 128px combo boxes.** The header's language dropdowns now
   abbreviate to "JP"/"EN" below "wide" mode, which is what actually frees
   the row -- the abbreviation needs roughly a third of the space the full
   names do. Display-only: `self.source_language`/`self.target_language`
   still always hold "Japanese"/"English", the same as before; a header
   selection is translated back via `_strip_language_flag` before being
   written to either.

3 (carried from the same report). **Start/Stop was visibly taller/wider than
   the other three footer buttons**, and the footer's own alignment changed
   shape between 400px and 700px. Both were explicit, permanent requests
   overriding the design's own CSS: one shared height for all four footer
   buttons, and Start/Stop always left-aligned / the action group always
   right-aligned at natural width, with no 50/50 stretch, at every width up
   to the hamburger cutover. Covered here rather than in
   `test_item71_footer_responsive.py` because it is the alignment/height
   half of the SAME report the header fixes come from.
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
    import customtkinter as ctk

    _close(ctk.CTk())
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    TK_AVAILABLE = False

from alpha.ui.main_window import (  # noqa: E402
    AlphaApp,
    LAYOUT_HAMBURGER_BREAKPOINT,
    LAYOUT_WIDE_BREAKPOINT,
)

# ---------------------------------------------------------------------------
# Fact 1: mode-switching uses design px, not device px
# ---------------------------------------------------------------------------

MODE_METHODS = (
    "_design_px",
    "_design_width",
    "_get_layout_mode",
    "_apply_responsive_layout",
)


def _build_mode_host(design_width):
    """A root wired enough to drive `_apply_responsive_layout` for real,
    without constructing the header/footer/content it would otherwise touch
    -- `_apply_header_layout` is replaced with a recorder so this test can
    see exactly what width and mode it was called with, which is the whole
    point: proving the VALUE passed downstream is design px.
    """
    root = ctk.CTk()
    root.geometry(f"{design_width}x650")
    root._layout_mode = None
    root._last_layout_width = -1
    root._last_layout_mode_applied = None
    root.brand_block = None
    root.content_wrapper = None
    root.status_bar_frame = None
    root.footer_frame = None
    for name in MODE_METHODS:
        setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
    calls = []
    root._apply_header_layout = lambda w, m: calls.append((w, m))
    # Without this, `winfo_width()` still reads Tk's pre-map placeholder (200
    # device px on this machine -> 133 design px) and every test below would
    # be measuring THAT bug instead of the one this file targets -- the same
    # timing fact `_on_first_map` exists to correct for the footer.
    for _ in range(4):
        root.update_idletasks()
        root.update()
    return root, calls


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestModeSwitchingUsesDesignPixels(unittest.TestCase):
    def test_a_900_design_px_window_reports_1350_device_px_on_this_display(self):
        """Pins the measurement the whole fix rests on."""
        root = ctk.CTk()
        try:
            root.geometry("900x650")
            for _ in range(4):
                root.update_idletasks()
                root.update()
            scaling = ctk.ScalingTracker.get_widget_scaling(root)
            self.assertEqual(root.winfo_width(), round(900 * scaling))
            if scaling == 1.0:
                self.skipTest("this display has no scaling; the bug cannot reproduce here")
        finally:
            _close(root)

    def test_the_header_receives_design_width_not_device_width(self):
        for design_width in (1200, 1050, 900, 800, 700):
            root, calls = _build_mode_host(design_width)
            try:
                root._apply_responsive_layout()
                self.assertEqual(len(calls), 1)
                received_width, _mode = calls[0]
                self.assertEqual(
                    received_width,
                    design_width,
                    f"asked for a {design_width}px window, header layout was "
                    f"called with {received_width} -- should be design px",
                )
            finally:
                _close(root)

    def test_900_design_px_is_medium_not_wide(self):
        """The exact failure: old code fed `winfo_width()` (1350 device px on
        this display) into a design-px threshold, so `_get_layout_mode`
        returned "wide" for a 900px window -- which is what skipped
        `_pack_header_controls`'s narrowing and let Meeting Summary run off
        the header, reported with a screenshot."""
        root, calls = _build_mode_host(900)
        try:
            root._apply_responsive_layout()
            _width, mode = calls[0]
            self.assertEqual(mode, "medium")
            self.assertNotEqual(
                mode,
                "wide",
                "900px must not be treated as wide -- that is the bug",
            )
        finally:
            _close(root)

    def test_mode_boundaries_land_exactly_on_the_design_constants(self):
        cases = [
            (LAYOUT_WIDE_BREAKPOINT, "wide"),
            (LAYOUT_WIDE_BREAKPOINT - 1, "medium"),
            (LAYOUT_HAMBURGER_BREAKPOINT, "medium"),
            (LAYOUT_HAMBURGER_BREAKPOINT - 1, "compact"),
        ]
        for design_width, expected in cases:
            root, calls = _build_mode_host(design_width)
            try:
                root._apply_responsive_layout()
                if expected == "compact":
                    # Compact mode never reaches _apply_header_layout in the
                    # real class either -- show_compact_layout replaces it.
                    self.assertEqual(root._layout_mode, expected)
                else:
                    self.assertEqual(calls[0][1], expected)
            finally:
                _close(root)


# ---------------------------------------------------------------------------
# Fact 2: header language abbreviation
# ---------------------------------------------------------------------------

HEADER_METHODS = (
    "_design_px",
    "_design_width",
    "_ui_font",
    "_glass_button_config",
    "_glass_icon_button_config",
    "_language_dropdown_wrapper_config",
    "_header_glass_combo_config",
    "_flagged_language_values",
    "_language_flag_label",
    "_header_language_label",
    "_strip_language_flag",
    "_make_language_combo",
    "_sync_language_combo_displays",
    "_set_header_language_abbreviated",
    "_load_logo",
    "_deferred_apply_logo",
    "create_header_frame",
    "_pack_header_controls",
    "_apply_header_layout",
    "_get_layout_mode",
    "swap_languages",
    "toggle_hamburger_menu",
    "_hide_hamburger_menu",
    "show_normal_layout",
    "show_compact_layout",
)


def _build_header_host(design_width, source="Japanese", target="English"):
    root = ctk.CTk()
    root.geometry(f"{design_width}x650")
    root._font_cache = {}
    root._header_lang_abbreviated = None
    root._compact_mode = None
    root._menu_visible = False
    root.logo_image = None
    root.logo_label = None
    root.source_language = ctk.StringVar(value=source)
    root.target_language = ctk.StringVar(value=target)
    for name in HEADER_METHODS:
        setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
    root.on_language_change = lambda *a, **k: None
    root.show_meeting_summary = lambda: None
    root.toggle_always_on_top = lambda: None
    # The header gained a second switch ("Meeting audio only"), so this host
    # needs its command and the state it reads -- the same fixture maintenance
    # every header collaborator has needed. Real widgets, so the geometry these
    # tests measure stays real; only the command is a stand-in.
    root.toggle_microphone_capture = lambda: None
    root._microphone_capture_enabled = False
    root.mic_switch = None
    root.mic_switch_menu = None
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    root.create_header_frame()
    # Real class builds this in create_hamburger_menu(); a bare stand-in is
    # enough for `_hide_hamburger_menu`'s grid_remove call.
    root.menu_dropdown_frame = ctk.CTkFrame(root)
    root.menu_dropdown_frame.grid_remove()
    root._menu_visible = False
    for _ in range(6):
        root.update_idletasks()
        root.update()
    return root


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestHeaderLanguageAbbreviation(unittest.TestCase):
    def test_full_names_at_wide_and_abbreviated_at_medium(self):
        wide = _build_header_host(1200)
        medium = _build_header_host(900)
        try:
            wide._apply_header_layout(1200, "wide")
            medium._apply_header_layout(900, "medium")
            for _ in range(4):
                wide.update_idletasks(); wide.update()
                medium.update_idletasks(); medium.update()
            self.assertEqual(wide.source_combo.get(), "Japanese")
            self.assertEqual(wide.target_combo.get(), "English")
            self.assertEqual(medium.source_combo.get(), "JP")
            self.assertEqual(medium.target_combo.get(), "EN")
        finally:
            _close(wide)
            _close(medium)

    def test_the_canonical_language_never_becomes_the_abbreviation(self):
        """The whole point: `source_language`/`target_language` are read
        everywhere else in this file (translation routing, export, summary).
        They must stay "Japanese"/"English" regardless of what the header
        combo box is currently displaying."""
        root = _build_header_host(900)
        try:
            root._apply_header_layout(900, "medium")
            for _ in range(4):
                root.update_idletasks()
                root.update()
            self.assertEqual(root.source_combo.get(), "JP")
            self.assertEqual(root.source_language.get(), "Japanese")
            self.assertEqual(root.target_language.get(), "English")
        finally:
            _close(root)

    def test_selecting_the_abbreviation_writes_the_full_name(self):
        """Simulates the CTkComboBox command callback CTk itself fires on a
        user selection -- exercises the same `on_select` closure the real
        header combo is built with."""
        root = _build_header_host(900)
        try:
            root._apply_header_layout(900, "medium")
            for _ in range(4):
                root.update_idletasks()
                root.update()
            root.source_combo._command("EN")
            self.assertEqual(root.source_language.get(), "English")
        finally:
            _close(root)

    def test_abbreviated_combo_narrower_than_full_name_combo(self):
        wide = _build_header_host(1200)
        medium = _build_header_host(900)
        try:
            wide._apply_header_layout(1200, "wide")
            medium._apply_header_layout(900, "medium")
            self.assertLess(
                int(medium.source_combo_wrap.cget("width")),
                int(wide.source_combo_wrap.cget("width")),
            )
        finally:
            _close(wide)
            _close(medium)

    def test_reverting_to_wide_restores_full_names(self):
        root = _build_header_host(900)
        try:
            root._apply_header_layout(900, "medium")
            for _ in range(4):
                root.update_idletasks(); root.update()
            self.assertEqual(root.source_combo.get(), "JP")
            root._apply_header_layout(1200, "wide")
            for _ in range(4):
                root.update_idletasks(); root.update()
            self.assertEqual(root.source_combo.get(), "Japanese")
        finally:
            _close(root)

    def test_swap_keeps_the_current_abbreviation_state(self):
        root = _build_header_host(900, source="Japanese", target="English")
        try:
            root._apply_header_layout(900, "medium")
            for _ in range(4):
                root.update_idletasks()
                root.update()
            self.assertEqual(root.source_combo.get(), "JP")
            root.swap_languages()
            for _ in range(4):
                root.update_idletasks()
                root.update()
            self.assertEqual(root.source_language.get(), "English")
            self.assertEqual(root.target_language.get(), "Japanese")
            # Swapped, but still abbreviated -- the width mode did not change.
            self.assertEqual(root.source_combo.get(), "EN")
            self.assertEqual(root.target_combo.get(), "JP")
        finally:
            _close(root)

    def test_a_malformed_language_falls_back_to_the_flagged_label(self):
        class _Host:
            _language_flag_label = AlphaApp._language_flag_label
            _header_language_label = AlphaApp._header_language_label

        host = _Host()
        self.assertEqual(host._header_language_label("Russian", abbreviated=True), "Russian")
        self.assertEqual(host._header_language_label("Japanese", abbreviated=False), "Japanese")


# ---------------------------------------------------------------------------
# Fact 3: footer alignment/height, carried from the same report
# ---------------------------------------------------------------------------

FOOTER_METHODS = (
    "_design_px",
    "_design_width",
    "_ui_font",
    "_footer_button_width",
    "_apply_footer_layout",
    "_sync_hamburger_action_buttons",
    "_primary_button_config",
    "_secondary_button_config",
    "create_footer",
    "_glass_button_config",
)


def _build_footer_host(design_width):
    root = ctk.CTk()
    root.geometry(f"{design_width}x650")
    root._font_cache = {}
    for name in FOOTER_METHODS:
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
    root.menu_dropdown_frame = ctk.CTkFrame(root)
    for attr in ("copy_translation_btn_menu", "export_btn_menu", "clear_btn_menu"):
        btn = ctk.CTkButton(root.menu_dropdown_frame, text=attr, **root._glass_button_config())
        btn.pack(fill="x")
        btn.pack_forget()
        setattr(root, attr, btn)
    root._hamburger_actions_visible = False
    root.create_footer()
    root.footer_frame.grid_configure(row=1)
    for _ in range(6):
        root.update_idletasks()
        root.update()
    root._apply_footer_layout(root._design_width())
    for _ in range(8):
        root.update_idletasks()
        root.update()
    return root


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestFooterButtonsAreUniform(unittest.TestCase):
    def test_all_four_buttons_share_one_height(self):
        for width in (410, 600, 900, 1200):
            root = _build_footer_host(width)
            try:
                heights = {
                    name: btn.winfo_height()
                    for name, btn in zip(
                        ("listen", "copy", "export", "clear"), root._footer_buttons
                    )
                    if btn.winfo_ismapped()
                }
                self.assertEqual(
                    len(set(heights.values())),
                    1,
                    f"footer buttons have different heights at {width}px: {heights}",
                )
            finally:
                _close(root)

    def test_start_stop_never_grows_past_its_natural_width_while_sharing_the_row(self):
        narrow = _build_footer_host(410)
        wide = _build_footer_host(1200)
        try:
            self.assertTrue(narrow._footer_buttons[1].winfo_ismapped())
            self.assertEqual(
                narrow._footer_buttons[0].winfo_width(),
                wide._footer_buttons[0].winfo_width(),
            )
        finally:
            _close(narrow)
            _close(wide)

    def test_start_stop_stays_left_and_actions_stay_right(self):
        root = _build_footer_host(900)
        try:
            listen, copy, export, clear = root._footer_buttons
            footer_left = root.footer_frame.winfo_rootx()
            footer_right = footer_left + root.footer_frame.winfo_width()
            self.assertLess(
                listen.winfo_rootx() - footer_left, 40,
                "start/stop should sit at the footer's left edge",
            )
            self.assertLess(
                footer_right - (clear.winfo_rootx() + clear.winfo_width()), 40,
                "the last action button should sit at the footer's right edge",
            )
        finally:
            _close(root)


if __name__ == "__main__":
    unittest.main()
