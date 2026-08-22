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

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import strings  # noqa: E402
from alpha.ui.theme import UI_LANGUAGE_SHORT_LABELS  # noqa: E402


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
    def setUp(self):
        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)


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

    WIDTHS = (800, 850, 900, 1050, 1200)
    HEADER_CONTROLS = ("summary_button", "always_on_top_switch", "ui_language_button")

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

    def test_header_button_above_the_breakpoint_hamburger_below(self):
        for design_width, expect_header in ((700, False), (799, False),
                                            (800, True), (900, True), (1200, True)):
            with self.subTest(width=design_width):
                root = self._host(design_width)
                root._apply_header_layout(
                    design_width,
                    "compact" if design_width < 800
                    else ("wide" if design_width >= 1050 else "medium"),
                )
                for _ in range(6):
                    root.update_idletasks()
                    root.update()
                self.assertEqual(
                    bool(root.ui_language_button.winfo_ismapped()), expect_header,
                    f"language button visibility wrong at {design_width}",
                )
                self.assertEqual(
                    bool(root.hamburger_button.winfo_ismapped()), not expect_header,
                    f"the hamburger has to take over wherever the button does not",
                )

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

    def test_it_sits_to_the_left_of_the_timer(self):
        root = self._host(900)
        for _ in range(4):
            root.update_idletasks()
            root.update()
        self.assertLess(
            root.mic_switch.winfo_rootx(),
            root.timer_label.winfo_rootx(),
            "the mic control belongs left of the running time",
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


if __name__ == "__main__":
    unittest.main()
