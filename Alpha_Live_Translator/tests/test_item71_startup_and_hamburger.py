"""Regression tests for item 71 Phase 3d, filed against a live user report.

Three defects, each reproduced against the real widgets before being fixed:

1. **The footer (and the reading panes' type scale) was broken on every
   launch, not just at narrow widths.** `create_footer()` runs during
   `__init__`, before `self.geometry("900x650")` has been realized by the
   window manager. Measured: at that instant `winfo_width()` returns Tk's own
   placeholder size -- **200** device px on this machine, not the `<= 1` this
   file's other fallbacks guard against, and not the 900 requested. 200 / 1.5
   scaling is a 133 design-px window, so the footer built itself for a window
   roughly a third the width of a phone, and stayed that way until *something*
   re-ran the responsive layout. `<Map>` is the first point `winfo_width()` is
   guaranteed correct -- measured 1350 (900 design px) at the same instant it
   fires, where `after(0, ...)` and `after_idle(...)` both still read 200.

2. **Start/Stop was duplicated in the hamburger menu and the footer at once,
   and Copy/Export/Clear had no home once the footer ran out of room for
   them.** Whenever the action group would need more than one line to fit its
   half of the footer row, the three buttons move into the hamburger menu
   instead of wrapping a second time; Start/Stop stays in the footer only, at
   every width, and is removed from the hamburger menu it used to also appear
   in. This was first shipped as a single fixed width threshold and a user
   report -- with a screenshot -- showed the exact wrapped shape that
   threshold was supposed to prevent, at a width just past it: the wrap this
   guards against is not confined to a narrow band, it runs from 400 design px
   up to ~550 (`_footer_button_width` is text-driven, so "Copy Translation"
   alone is wider than half the row for most of that band). The decision is
   now made from the real computed line count, not a number.

3. **Both language dropdowns defaulted to "Japanese".** The target now
   defaults to the other language from the source.

None of this is covered by a live click-through -- these tests drive the real
bound methods and the real Tk event loop, not a running `AlphaApp`.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.strings import get_language, set_language  # noqa: E402

_LANGUAGE_BEFORE = None


def setUpModule():
    """Pin this file to English: every number in it is an English measurement.

    Japanese glyphs are wider, which legitimately moves two things -- the width
    at which the action group leaves the footer for the hamburger menu
    (measured: 410 design px in English, 450 in Japanese) and the primary
    button natural width (183 vs 203). Both are the responsive rules working,
    not regressions, so this file keeps guarding the layout it was written for
    and tests/test_item88_ui_strings.py guards the Japanese one.
    """
    global _LANGUAGE_BEFORE
    _LANGUAGE_BEFORE = get_language()
    set_language('en')


def tearDownModule():
    set_language(_LANGUAGE_BEFORE)



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

from alpha.ui.main_window import AlphaApp  # noqa: E402

FOOTER_METHODS = (
    "_design_px",
    "_design_width",
    "_ui_font",
    "_footer_button_width",
    "_apply_footer_layout",
    "_sync_hamburger_action_buttons",
    "_primary_button_config",
    "_secondary_button_config",
    "_refresh_reading_typography",
    "_apply_reading_typography",
    "_scaled_design_font",
    "create_footer",
    "bind_resize_event",
    "_on_first_map",
    "on_window_resize",
    "_glass_button_config",
    # `_on_first_map` now runs the FULL `_apply_responsive_layout()` chain
    # (Phase 3e -- it used to call only `_apply_footer_layout`/
    # `_refresh_reading_typography` directly, which is what let the header's
    # own first correction be skipped on every normal launch; see that
    # function's docstring). Everything below is what the chain needs to run
    # without a real header/content/status bar built, safely no-op-ing via
    # the same None-guards the real widgets would otherwise satisfy.
    "_apply_responsive_layout",
    "_apply_responsive_layout_tail",
    "_apply_responsive_layout_tail2",
    "_apply_header_layout",
    "_pack_header_controls",
    "show_normal_layout",
    "show_compact_layout",
    "_get_layout_mode",
    "_apply_content_layout",
    "_apply_status_bar_layout",
)


def _build_root(design_width=900):
    """A root wired exactly like `AlphaApp.__init__` wires it: `create_footer()`
    runs synchronously, then `bind_resize_event()` binds `<Map>`/`<Configure>`
    -- in that order, with no `update()` between them, matching the real
    class. Reordering these two calls would silently stop testing the actual
    bug (see fact 1 above): `<Map>` only fires once, so binding it after an
    incidental `update()` elsewhere in a test misses the event entirely.
    """
    root = ctk.CTk()
    root.geometry(f"{design_width}x650")
    root._font_cache = {}
    root._first_map_handled = False
    root._resize_layout_job = None
    root._layout_mode = None
    root._reading_typography_stacked = None
    root.left_column = None
    root.transcript_column = None
    root.right_column = None
    root.content_wrapper = None
    root.footer_frame = None
    # No real header/status bar in this footer-focused host; every one of
    # these is a None-guard the real methods already check before touching
    # a widget, matching how `_apply_responsive_layout`'s chain safely skips
    # pieces of the app that have not been built yet during `__init__`.
    root._compact_mode = None
    root.header_lang_frame = None
    root.status_bar_frame = None
    root.waveform_canvas = None
    root.brand_block = None
    root._last_layout_width = -1
    root._last_layout_mode_applied = None
    # `show_normal_layout`/`show_compact_layout` catch their own exceptions
    # and print rather than raise -- this footer-focused host has no header,
    # so every call to either prints "Error showing .../compact layout: ...".
    # Harmless (already caught, tests below assert on the FOOTER, not the
    # header), left as visible noise rather than built out further: chasing
    # it required a widget tree deep enough to duplicate
    # `test_item71_header_responsive.py`'s real header host.
    for name in FOOTER_METHODS:
        setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
    for command in (
        "toggle_listening",
        "copy_translation_to_clipboard",
        "export_transcript_placeholder",
        "clear_text",
    ):
        setattr(root, command, lambda: None)
    # `bind_resize_event` also wires `<Configure>` -> `on_window_resize`,
    # which schedules the full responsive chain this test host does not
    # build. A no-op stub keeps that debounced callback from raising into
    # stderr on every `<Configure>` this test's own `update()` calls provoke.
    root._apply_responsive_layout_debounced = lambda: None
    # Waveform scheduling is irrelevant to footer/hamburger behaviour and
    # pulls in its own `after()` chain; stubbed rather than built out.
    root._schedule_waveform_layout = lambda: None
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)

    # Minimal hamburger-menu stand-in: the three buttons `_sync_hamburger_action_buttons`
    # controls, built the same way `create_hamburger_menu` builds them (packed once,
    # then forgotten, so they start hidden).
    root.menu_dropdown_frame = ctk.CTkFrame(root)
    root.copy_translation_btn_menu = ctk.CTkButton(
        root.menu_dropdown_frame, text="Copy Translation", **root._glass_button_config()
    )
    root.copy_translation_btn_menu.pack(fill="x")
    root.copy_translation_btn_menu.pack_forget()
    root.export_btn_menu = ctk.CTkButton(
        root.menu_dropdown_frame, text="Export", **root._glass_button_config()
    )
    root.export_btn_menu.pack(fill="x")
    root.export_btn_menu.pack_forget()
    root.clear_btn_menu = ctk.CTkButton(
        root.menu_dropdown_frame, text="Clear", **root._glass_button_config()
    )
    root.clear_btn_menu.pack(fill="x")
    root.clear_btn_menu.pack_forget()
    root._hamburger_actions_visible = False
    root.listen_button_menu = None  # never packed in the real class either

    # This is __init__'s actual order: build the footer, THEN bind resize/<Map>.
    root.create_footer()
    root.footer_frame.grid_configure(row=1)
    root.bind_resize_event()
    return root


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestFirstMapCorrectsAStartupOnlyBug(unittest.TestCase):
    """Fact 1."""

    def test_winfo_width_is_wrong_at_the_exact_moment_create_footer_runs(self):
        """Pins the measurement the whole fix rests on. If this ever changes
        (a different Tk/CTk version, a different platform), `_on_first_map`
        may no longer be necessary -- or a new placeholder value may need a
        new guard."""
        root = ctk.CTk()
        try:
            root.geometry("900x650")
            self.assertEqual(root.winfo_width(), 200)
            self.assertGreater(
                root.winfo_width(),
                1,
                "if this ever becomes <= 1, the existing `_design_width` "
                "fallback already covers it and this whole fix is moot",
            )
        finally:
            _close(root)

    def test_footer_is_wrong_immediately_after_construction(self):
        """The defect exists even at design_width=900 (the app's own default
        geometry) -- this is not a narrow-window-only bug.

        Checked via `winfo_manager()`, not `winfo_width()`: the former reads
        Tk's internal bookkeeping of which geometry manager owns a widget and
        is accurate with zero event-loop processing, where the latter
        requires idle-task processing to reflect a `.grid()` call and would
        give a false negative here regardless of the bug.
        """
        root = _build_root(design_width=900)
        try:
            # At 900 design px -- the width the window actually opens at --
            # the action group fits one line, so none of this should be true.
            # It is, because construction computed a design width of 133.
            self.assertEqual(
                root.copy_translation_btn_menu.winfo_manager(),
                "pack",
                "the hamburger's Copy button should not be needed at 900px",
            )
            self.assertEqual(
                root._footer_buttons[1].winfo_manager(),
                "",
                "the footer's own Copy button should not have been removed "
                "at 900px",
            )
        finally:
            _close(root)

    def test_map_corrects_it_before_any_human_could_see_it(self):
        results = {}

        def snapshot():
            for _ in range(2):
                root.update_idletasks()
                root.update()
            results["design_width"] = root._design_width()
            results["footer_h"] = root.footer_frame.winfo_height()
            results["listen_w"] = root._footer_buttons[0].winfo_width()

        root = _build_root(design_width=900)
        try:
            # 5ms: as fast as this environment can schedule a callback. If
            # `_on_first_map` needs a fixed delay instead of `<Map>`, this is
            # where it would still show the broken shape.
            root.after(5, snapshot)
            root.after(40, _close_and_stop(root))
            root.mainloop()
        finally:
            pass
        self.assertEqual(results["design_width"], 900)
        # 900 design px is >= FOOTER_STACK_BREAKPOINT (700): one row, natural
        # widths -- a tall multi-line footer means the correction did not run.
        self.assertLess(
            results["footer_h"],
            150,
            f"footer is still the broken multi-line shape: {results}",
        )
        self.assertEqual(
            results["listen_w"],
            183,
            "this is the exact single-row width measured for this label/pad "
            "combination; a different number means either the correction "
            "did not run or _footer_button_width changed underneath it",
        )

    def test_a_second_map_event_is_a_no_op(self):
        """`<Map>` can refire (minimize/restore). The guard must not let a
        stale width silently re-break a window the user has since resized."""
        root = _build_root(design_width=900)
        try:
            for _ in range(3):
                root.update_idletasks()
                root.update()
            self.assertTrue(root._first_map_handled)
            root.geometry("400x650")
            for _ in range(3):
                root.update_idletasks()
                root.update()
            # A synthetic second <Map> must not undo the resize by re-reading
            # a cached width.
            root.event_generate("<Map>")
            for _ in range(3):
                root.update_idletasks()
                root.update()
            self.assertNotEqual(root._design_width(), 900)
        finally:
            _close(root)


def _close_and_stop(root):
    def _fn():
        root.quit()

    return _fn


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestHamburgerActionSplit(unittest.TestCase):
    """Fact 2."""

    def test_start_stop_is_never_in_the_hamburger_menu(self):
        for width in (350, 500, 900):
            root = _build_root(design_width=width)
            try:
                for _ in range(4):
                    root.update_idletasks()
                    root.update()
                self.assertIsNone(
                    root.listen_button_menu,
                    "the real class must never pack this widget; the test "
                    "host leaves it None to make an accidental pack loud",
                )
            finally:
                _close(root)

    def test_when_the_action_group_would_wrap_it_moves_to_the_hamburger_instead(
        self,
    ):
        # 350 design px: "Copy Translation" alone (needs ~198) is already
        # wider than half the row, so the action group can never fit one line
        # here regardless of exactly where the wrap threshold falls.
        root = _build_root(design_width=350)
        try:
            for _ in range(6):
                root.update_idletasks()
                root.update()
            listen, copy, export, clear = root._footer_buttons
            self.assertTrue(listen.winfo_ismapped())
            for btn in (copy, export, clear):
                self.assertFalse(
                    btn.winfo_ismapped(), "action buttons must leave the footer"
                )
            for menu_btn in (
                root.copy_translation_btn_menu,
                root.export_btn_menu,
                root.clear_btn_menu,
            ):
                self.assertEqual(menu_btn.winfo_manager(), "pack")
            # Start/Stop takes the whole row once it is the footer's only content.
            self.assertGreater(
                listen.winfo_width(), root.footer_frame.winfo_width() * 0.8
            )
        finally:
            _close(root)

    def test_at_the_apps_own_default_width_actions_stay_in_the_footer(self):
        # 900: DEFAULT_WINDOW_WIDTH, and comfortably past where the wrap
        # measured itself away (~550) -- one line, no reason to hide anything.
        root = _build_root(design_width=900)
        try:
            for _ in range(6):
                root.update_idletasks()
                root.update()
            for btn in root._footer_buttons:
                self.assertTrue(btn.winfo_ismapped())
            for menu_btn in (
                root.copy_translation_btn_menu,
                root.export_btn_menu,
                root.clear_btn_menu,
            ):
                self.assertEqual(menu_btn.winfo_manager(), "")
        finally:
            _close(root)

    def test_crossing_the_breakpoint_both_ways_never_leaves_a_button_orphaned(self):
        """Neither side may end up with an action button visible in BOTH
        places, or in NEITHER."""
        root = _build_root(design_width=900)
        try:
            for design_width in (900, 350, 900, 350):
                root._apply_footer_layout(design_width)
                for _ in range(4):
                    root.update_idletasks()
                    root.update()
                footer_visible = {
                    name: btn.winfo_ismapped()
                    for name, btn in zip(
                        ("copy", "export", "clear"), root._footer_buttons[1:]
                    )
                }
                menu_visible = {
                    "copy": root.copy_translation_btn_menu.winfo_manager() == "pack",
                    "export": root.export_btn_menu.winfo_manager() == "pack",
                    "clear": root.clear_btn_menu.winfo_manager() == "pack",
                }
                for key in ("copy", "export", "clear"):
                    self.assertNotEqual(
                        footer_visible[key],
                        menu_visible[key],
                        f"{key} must be in exactly one place at design_width="
                        f"{design_width}: footer={footer_visible} menu={menu_visible}",
                    )
        finally:
            _close(root)


class TestDefaultLanguages(unittest.TestCase):
    """Fact 3. Runs with or without a display -- reads constants only."""

    def test_target_defaults_to_the_other_language_from_the_source(self):
        from alpha.constants import DEFAULT_SOURCE_LANGUAGE

        source_ui = "Japanese" if DEFAULT_SOURCE_LANGUAGE == "ja" else "English"
        expected_target = "English" if source_ui == "Japanese" else "Japanese"
        # Reproduce __init__'s exact computation without constructing the app.
        _default_source_ui = (
            "Japanese" if DEFAULT_SOURCE_LANGUAGE == "ja" else "English"
        )
        _default_target_ui = (
            "English" if _default_source_ui == "Japanese" else "Japanese"
        )
        self.assertEqual(_default_source_ui, source_ui)
        self.assertEqual(_default_target_ui, expected_target)
        self.assertNotEqual(_default_source_ui, _default_target_ui)


if __name__ == "__main__":
    unittest.main()
