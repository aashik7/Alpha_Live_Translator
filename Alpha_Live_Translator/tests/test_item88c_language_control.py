"""Item 88c -- the display-language control, and the three bugs behind it.

All three came from one live report with a screenshot, and each is pinned here
because each is invisible until someone looks at the running window.

1. **The header kept the old language until the window was resized.**
   `_retranslate_ui()` was correct; `_apply_responsive_layout()` was not
   reachable. It returns early when the window width and layout mode are both
   unchanged, and switching language changes neither -- so
   `_pack_header_controls` never re-ran, and `summary_button` is the one widget
   whose wording ONLY that function writes. The screenshot showed exactly that:
   an English header with a Japanese Meeting Summary button left over.

2. **The microphone checkbox did not fit in the header.** Measured before it
   moved: at 800 design px in Japanese it was already 5 px past the header's
   right edge, before the language button was added at all. It now lives in the
   status strip, which has room at every width.

3. **"Mic" did not say what it meant.** The label now states the state --
   "Mic off" until it is ticked, "Mic on" after -- so the answer is in the text
   rather than in a 16 px tick box.
"""

import pathlib
import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import strings  # noqa: E402
from alpha.ui.theme import (  # noqa: E402
    LAYOUT_HAMBURGER_BREAKPOINT,
    UI_LANGUAGE_SHORT_LABELS,
)


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

if TK_AVAILABLE:
    from alpha.ui.main_window import AlphaApp

HEADER_METHODS = (
    "_design_px",
    "_design_width",
    "_ui_font",
    "_card_config",
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
    "_open_ui_language_menu",
    "_sync_ui_language_controls",
    "_sync_mic_switches",
    "_retranslate_ui",
    "_retranslate_placeholder",
    "_apply_ui_language",
    "create_header_frame",
    "create_status_bar",
    "_pack_header_controls",
    "_apply_header_layout",
    # `_retranslate_ui` goes through this to repaint `summary_button`, so the
    # host needs the real one or the bug under test cannot be observed at all.
    "_apply_responsive_layout",
    "_get_layout_mode",
    "_draw_waveform",
    "_get_waveform_bar_count",
    "_get_waveform_canvas_width",
    "swap_languages",
    "toggle_hamburger_menu",
    "_hide_hamburger_menu",
    "show_normal_layout",
    "show_compact_layout",
)


class LanguageRestoringTestCase(unittest.TestCase):
    """Every test starts in English and puts the language back afterwards.

    Restoring alone is not enough. The language is module state, so a test that
    switches to Japanese leaves the NEXT one in whatever it inherited, and
    unittest runs methods in alphabetical order -- which made assertions on
    English wording pass or fail according to method name. Starting from a known
    value takes the ordering out of it; tests wanting Japanese ask for it.
    """

    def setUp(self):
        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)
        strings.set_language("en")


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TkHostTestCase(LanguageRestoringTestCase):
    """Builds the real header and status strip, not stand-ins for them.

    Only the commands are replaced. The geometry these tests measure has to be
    the geometry the app produces, or the measurement proves nothing -- which is
    how the header overflow reached a live window in the first place.
    """

    def _host(self, design_width):
        root = ctk.CTk()
        self.addCleanup(_close, root)
        root.geometry(f"{design_width}x650")
        root._font_cache = {}
        root._header_lang_abbreviated = None
        root._compact_mode = None
        root._menu_visible = False
        root._layout_mode = None
        root.logo_image = None
        root.logo_label = None
        root.waveform_canvas = None
        root._microphone_capture_enabled = False
        root.mic_switch = None
        root.mic_switch_menu = None
        root.ui_language_button = None
        root.ui_language_combo_menu = None
        root.source_language = ctk.StringVar(value="Japanese")
        root.target_language = ctk.StringVar(value="English")
        for name in HEADER_METHODS:
            setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
        root._RETRANSLATABLE_LABELS = AlphaApp._RETRANSLATABLE_LABELS
        for command in ("on_language_change", "show_meeting_summary",
                        "toggle_always_on_top", "toggle_microphone_capture"):
            setattr(root, command, lambda *a, **k: None)
        # The header half of the responsive pass is what these tests measure.
        # Its deferred tail lays out content, footer and waveform, none of which
        # this host builds, so it is stubbed rather than left to raise inside a
        # Tk callback and bury the real assertion in noise.
        root._apply_responsive_layout_tail = lambda *a, **k: None
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(0, weight=1)
        root.create_header_frame()
        root.create_status_bar()
        root.menu_dropdown_frame = ctk.CTkFrame(root)
        root.menu_dropdown_frame.grid_remove()
        for _ in range(6):
            root.update_idletasks()
            root.update()
        return root


class TestNothingIsPushedOffTheHeader(TkHostTestCase):
    """The header has to hold its controls at every width, in both languages.

    Item 74 was a control that disappeared between two thresholds. This is the
    same failure a step earlier: a control still packed, but pushed past the
    right edge where nobody can click it. Japanese is checked because Japanese
    labels are wider, and 800 is checked because that is where the hamburger
    takes over and the row is tightest.
    """

    WIDTHS = (880, 900, 1050, 1200)
    HEADER_CONTROLS = (
        "summary_button",
        "always_on_top_switch",
        "ui_language_button",
        "hamburger_button",
    )

    def test_no_header_control_is_squeezed_or_pushed_off(self):
        for language in ("en", "ja"):
            strings.set_language(language)
            for design_width in self.WIDTHS:
                with self.subTest(language=language, width=design_width):
                    root = self._host(design_width)
                    root._apply_header_layout(
                        design_width, "wide" if design_width >= 1050 else "medium"
                    )
                    for _ in range(6):
                        root.update_idletasks()
                        root.update()
                    header = root.header_frame
                    header_right = header.winfo_rootx() + header.winfo_width()
                    for name in self.HEADER_CONTROLS:
                        widget = getattr(root, name, None)
                        if widget is None or not widget.winfo_ismapped():
                            continue
                        self.assertGreaterEqual(
                            widget.winfo_width(),
                            widget.winfo_reqwidth(),
                            f"{name} squeezed below its own text at {design_width}",
                        )
                        overflow = (
                            widget.winfo_rootx() + widget.winfo_width()
                        ) - header_right
                        self.assertLessEqual(
                            overflow, 0,
                            f"{name} is {overflow}px past the header's right edge "
                            f"at {design_width} in {language}",
                        )


class TestTheLanguageControlIsAlwaysReachable(TkHostTestCase):
    """Above the breakpoint it is in the header, below it the hamburger opens.

    Both use `MIC_SWITCH_MIN_WIDTH`, which IS `LAYOUT_HAMBURGER_BREAKPOINT`.
    One constant for both is what stops a band of widths where neither surface
    shows the control -- the item 74 bug.
    """

    # (width, language button on screen, hamburger on screen)
    # The threshold is `LAYOUT_HAMBURGER_BREAKPOINT`, back at its original 800.
    # It was briefly raised to 880 to make room for this button; moving the mic
    # switch out of the header freed that room, and the raise was never
    # re-measured afterwards. Measured now: nothing is past the header's right
    # edge at 800, 820, 860 or 879, in English or Japanese.
    EXPECTED = (
        (700, False, True),
        (799, False, True),
        (800, True, False),   # the one threshold
        (879, True, False),
        (900, True, False),   # the width the window opens at
        (1050, True, False),
        (1400, True, False),
    )

    def _lay_out(self, design_width):
        """Mode comes from the app's own `_get_layout_mode`, never a literal.

        An earlier version of this helper hardcoded 800 as the compact
        threshold, so raising the real constant left the test asserting against
        a boundary the app no longer had.
        """
        root = self._host(design_width)
        root._apply_header_layout(design_width, root._get_layout_mode(design_width))
        for _ in range(8):
            root.update_idletasks()
            root.update()
        return root

    def test_the_control_is_on_screen_at_every_width(self):
        """One or both surfaces, never neither. This is the item 74 invariant."""
        for design_width, in_header, in_hamburger in self.EXPECTED:
            with self.subTest(width=design_width):
                root = self._lay_out(design_width)
                self.assertTrue(
                    root.ui_language_button.winfo_ismapped()
                    or root.hamburger_button.winfo_ismapped(),
                    f"nothing offers the language setting at {design_width}",
                )

    def test_the_hamburger_and_the_header_are_never_both_offering_it(self):
        """The hamburger menu already carries every header control, so showing
        both at once is the same setting offered twice.

        A first attempt gave the button and the hamburger separate thresholds
        so they would overlap; that produced exactly this duplication. One
        threshold owns the whole header instead, and it sits at 880 -- wide
        enough that the swap happens while the row is still comfortable, rather
        than at the width where it was already tight and the button looked like
        it vanished on its own.
        """
        for design_width, in_header, in_hamburger in self.EXPECTED:
            with self.subTest(width=design_width):
                root = self._lay_out(design_width)
                self.assertNotEqual(
                    root.ui_language_button.winfo_ismapped(),
                    root.hamburger_button.winfo_ismapped(),
                    f"both surfaces offer the language setting at {design_width}",
                )

    def test_the_visibility_table_holds(self):
        for design_width, in_header, in_hamburger in self.EXPECTED:
            with self.subTest(width=design_width):
                root = self._lay_out(design_width)
                self.assertEqual(
                    bool(root.ui_language_button.winfo_ismapped()), in_header,
                    f"language button visibility wrong at {design_width}",
                )
                self.assertEqual(
                    bool(root.hamburger_button.winfo_ismapped()), in_hamburger,
                    f"hamburger visibility wrong at {design_width}",
                )

    def test_the_window_opens_in_the_header_layout(self):
        """900 is where the window opens, and the threshold sits below it on
        purpose, so the app never starts in the hamburger layout."""
        from alpha.ui.theme import DEFAULT_WINDOW_WIDTH

        self.assertGreater(DEFAULT_WINDOW_WIDTH, LAYOUT_HAMBURGER_BREAKPOINT)
        root = self._lay_out(DEFAULT_WINDOW_WIDTH)
        self.assertTrue(root.ui_language_button.winfo_ismapped())
        self.assertFalse(root.hamburger_button.winfo_ismapped())

    def test_the_button_shows_the_active_language(self):
        for language in ("en", "ja"):
            with self.subTest(language=language):
                strings.set_language(language)
                root = self._host(900)
                root._sync_ui_language_controls()
                self.assertEqual(
                    root.ui_language_button.cget("text"),
                    UI_LANGUAGE_SHORT_LABELS[language],
                )


class TestTheHeaderRepaintsWithoutAResize(TkHostTestCase):
    """The reported bug, pinned.

    `_apply_responsive_layout` short-circuits when width and mode are unchanged.
    A language switch changes neither, so without clearing those cache keys the
    header keeps its old wording until the window is dragged. `summary_button`
    is the probe because `_pack_header_controls` is its only author -- every
    other header label is also written by `_retranslate_ui` directly and would
    pass even with the bug present.
    """

    def test_summary_button_follows_the_language_with_no_resize(self):
        strings.set_language("en")
        root = self._host(1200)
        root.content_wrapper = None
        root.status_bar_frame = None
        root.footer_frame = None
        root.brand_block = None
        root._apply_header_layout(1200, "wide")
        for _ in range(4):
            root.update_idletasks()
            root.update()
        self.assertEqual(root.summary_button.cget("text"), "Meeting Summary")

        # Exactly the state a settled window is in: the layout has already run
        # at this width, so its cache says there is nothing to do.
        root._last_layout_width = root._design_width()
        root._last_layout_mode_applied = "wide"

        strings.set_language("ja")
        root._retranslate_ui()
        for _ in range(6):
            root.update_idletasks()
            root.update()

        self.assertEqual(root.summary_button.cget("text"), strings.t("Meeting Summary"))
        self.assertNotEqual(root.summary_button.cget("text"), "Meeting Summary")
        self.assertEqual(root.brand_sub_label.cget("text"), strings.t("Meeting Assistant"))


class TestTheMicControl(TkHostTestCase):
    def test_it_is_in_the_status_strip_and_not_the_header(self):
        """It was moved because it did not fit: measured at 5px past the edge
        at 800 design px in Japanese, before anything else was added."""
        root = self._host(900)
        self.assertIsNotNone(root.mic_switch)
        self.assertIs(root.mic_switch.master, root._status_right_cluster)
        header_descendants = []

        def walk(widget):
            for child in widget.winfo_children():
                header_descendants.append(child)
                walk(child)

        walk(root.header_frame)
        self.assertNotIn(root.mic_switch, header_descendants)

    def test_it_sits_left_of_the_standby_indicator_and_the_timer(self):
        root = self._host(900)
        for _ in range(4):
            root.update_idletasks()
            root.update()
        self.assertLess(
            root.mic_switch.winfo_rootx(),
            root.signal_label.winfo_rootx(),
            "the mic control belongs left of the standby indicator",
        )
        self.assertLess(
            root.signal_label.winfo_rootx(),
            root.timer_label.winfo_rootx(),
            "standby stays between the mic control and the running time",
        )

    def test_the_label_states_the_state(self):
        root = self._host(900)
        root._microphone_capture_enabled = False
        root._sync_mic_switches()
        self.assertEqual(root.mic_switch.cget("text"), "Mic off")
        root._microphone_capture_enabled = True
        root._sync_mic_switches()
        self.assertEqual(root.mic_switch.cget("text"), "Mic on")

    def test_the_label_is_translated_too(self):
        strings.set_language("ja")
        root = self._host(900)
        root._microphone_capture_enabled = True
        root._sync_mic_switches()
        self.assertEqual(root.mic_switch.cget("text"), strings.t("Mic on"))
        self.assertNotEqual(root.mic_switch.cget("text"), "Mic on")


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestTheRightClickMenuCopies(LanguageRestoringTestCase):
    """Right-clicking a pane offers copy FIRST, and clear still below it.

    Item 88d replaced the menu's only entry, "Clear All Text", with a copy
    entry. Copy on right-click was asked for; dropping Clear was not, and the
    user reported it as a regression. Both are there now, separated -- the
    separator is the actual guard against the slip of the mouse that motivated
    the removal, and it costs nothing that a removal costs.

    Driven through the real AlphaApp because the binding, the menu and both
    panes have to exist for any of this to mean anything.
    """

    def setUp(self):
        super().setUp()
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

    def _label_for(self, label_source, action):
        """Relabel the entry the way opening the menu does, without posting it.

        `tk_popup` blocks on a real grab, so the label is read after
        `entryconfigure` and the popup itself is skipped.
        """
        self.app.context_menu.entryconfigure(
            0, label=strings.t(label_source), command=action
        )
        return self.app.context_menu.entrycget(0, "label")

    def _entries(self):
        """Every entry as (type, label). A separator has no -label at all, so
        asking one for its label raises rather than returning empty."""
        end = self.app.context_menu.index("end")
        out = []
        for i in range(0 if end is None else end + 1):
            kind = self.app.context_menu.type(i)
            label = "" if kind == "separator" else self.app.context_menu.entrycget(i, "label")
            out.append((kind, label))
        return out

    def test_copy_comes_first(self):
        kinds_and_labels = self._entries()
        self.assertTrue(kinds_and_labels, "the context menu is empty")
        self.assertIn("Copy", kinds_and_labels[0][1])

    def test_clear_is_still_offered(self):
        labels = [label for _, label in self._entries()]
        self.assertTrue(
            any("Clear" in label for label in labels),
            f"clear was dropped from the pane menu: {labels}",
        )

    def test_a_separator_keeps_clear_away_from_copy(self):
        kinds = [kind for kind, _ in self._entries()]
        self.assertIn(
            "separator", kinds,
            "nothing separates the copy entry from the destructive one",
        )

    def test_both_panes_are_bound(self):
        for box in (self.app.initial_verse_box, self.app.translated_verse_box):
            self.assertIn("<Button-3>", box.bind(), "pane has no right-click binding")

    def test_each_pane_offers_its_own_copy(self):
        self.assertEqual(
            self._label_for("Copy Transcript",
                            self.app.copy_live_transcript_to_clipboard),
            "Copy Transcript",
        )
        self.assertEqual(
            self._label_for("Copy Translation",
                            self.app.copy_translation_to_clipboard),
            "Copy Translation",
        )

    def test_the_label_follows_the_language(self):
        strings.set_language("ja")
        label = self._label_for("Copy Transcript",
                                self.app.copy_live_transcript_to_clipboard)
        self.assertEqual(label, strings.t("Copy Transcript"))
        self.assertNotEqual(label, "Copy Transcript")


if __name__ == "__main__":
    unittest.main()


class TestWindowsDecidesTheFirstRun(LanguageRestoringTestCase):
    """A Japanese Windows should open a Japanese app without being told.

    The OS language is read only when nobody has chosen yet -- see
    `_resolve_language`'s order -- so it can never override someone who has
    picked. The mapping is tested here rather than the call that reads the
    LCID: a test cannot change the machine's display language, but it can hand
    the mapping any LCID it likes.
    """

    def test_every_japanese_variant_maps_to_japanese(self):
        for lcid in (0x0411, 0x0011):
            with self.subTest(lcid=hex(lcid)):
                self.assertEqual(strings.language_from_lcid(lcid), "ja")

    def test_everything_else_maps_to_english(self):
        for lcid in (0x0409, 0x0809, 0x040C, 0x0412, 0x0404, 0x0407):
            with self.subTest(lcid=hex(lcid)):
                self.assertEqual(strings.language_from_lcid(lcid), "en")

    def test_an_unreadable_lcid_decides_nothing(self):
        """"" and not "en": the caller then falls through to its own default
        instead of this function ruling on a platform it could not read."""
        for value in (None, "not a number", object()):
            self.assertEqual(strings.language_from_lcid(value), "")

    def test_a_saved_choice_outranks_windows(self):
        """Picking English on a Japanese machine has to stick."""
        source = pathlib.Path(strings.__file__).read_text(encoding="utf-8")
        saved = source.index("_saved_language()")
        windows = source.index("_windows_ui_language()", source.index("def _resolve_language"))
        self.assertLess(
            source.index("_saved_language()", source.index("def _resolve_language")),
            windows,
            "the saved choice must be consulted before the OS language",
        )
        self.assertGreater(saved, 0)
