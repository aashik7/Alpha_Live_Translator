"""Item 88 -- the Japanese UI string table, and the traps it has to avoid.

Three things are being guarded here, in descending order of how much damage
they would do if they broke.

1. **The default costs nothing.** With the shipped default (`en`) `t()` has to
   be an exact identity function, so merging this feature cannot change the
   app's behaviour at all. Tested with `is`, not `==`.

2. **A string that is also a key is never translated.** `SOURCE_LANGUAGES` and
   `TARGET_LANGUAGES` read "English" and "Japanese", and those same words are
   what `_resolve_deepgram_language()` and the DeepL target switch on. If they
   ever appear in the table, speech recognition routes to the wrong language
   and nothing raises. That is the one failure this feature could plausibly
   cause, so it gets an explicit test rather than a comment.

3. **A key that matches nothing does nothing, silently.** A mistyped key sits
   in the table looking translated and renders in English forever. Every key is
   therefore checked to appear verbatim somewhere in `alpha/`.

The Tk case at the bottom is the item 74 lesson: a label that fits in English
is not evidence it fits in Japanese, and the footer is where that was found
last time.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import strings  # noqa: E402
from alpha.constants import SOURCE_LANGUAGES, TARGET_LANGUAGES  # noqa: E402


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


class LanguageRestoringTestCase(unittest.TestCase):
    """Every test here mutates a module global, so every test puts it back.

    Without this a single failure part-way through would leave the whole rest
    of the suite running in Japanese.
    """

    def setUp(self):
        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)


class TestEnglishIsFree(LanguageRestoringTestCase):
    def test_shipped_default_is_english(self):
        self.assertEqual(strings.DEFAULT_UI_LANGUAGE, "en")

    def test_english_returns_the_very_same_object(self):
        strings.set_language("en")
        for value in ("Start Listening", "", "not a key", None, 0, 123, ["x"]):
            self.assertIs(strings.t(value), value)

    def test_english_never_consults_the_table(self):
        """Even a key that exists must come back untranslated under `en`."""
        strings.set_language("en")
        for key in strings._JA:
            self.assertIs(strings.t(key), key)


class TestFallbacksAreTotal(LanguageRestoringTestCase):
    def test_unknown_key_passes_through(self):
        strings.set_language("ja")
        self.assertEqual(strings.t("no such string in the table"), "no such string in the table")

    def test_non_string_passes_through(self):
        strings.set_language("ja")
        for value in (None, 0, 12.5, ["a"], {"b": 1}):
            self.assertIs(strings.t(value), value)

    def test_unknown_language_falls_back_to_english(self):
        for code in ("zz", "", "  ", None, "EN-GB"):
            strings.set_language(code)
            self.assertEqual(strings.get_language(), "en")
            self.assertIs(strings.t("Start Listening"), "Start Listening")

    def test_known_language_is_case_and_space_insensitive(self):
        strings.set_language("  JA  ")
        self.assertEqual(strings.get_language(), "ja")

    def test_japanese_actually_translates(self):
        strings.set_language("ja")
        self.assertEqual(strings.t("Start Listening"), "認識を開始")
        self.assertNotEqual(strings.t("Ready"), "Ready")


class TestTableIsSane(LanguageRestoringTestCase):
    def test_no_value_is_empty(self):
        """An empty translation would render as a blank label."""
        for key, value in strings._JA.items():
            self.assertTrue(value.strip(), f"empty translation for {key!r}")

    def test_no_value_is_left_in_english(self):
        for key, value in strings._JA.items():
            self.assertNotEqual(key, value, f"{key!r} is not translated")

    def test_language_names_are_never_translated(self):
        """The dropdown values double as logic keys -- see the module docstring.

        `_resolve_deepgram_language()` and the DeepL target both switch on these
        exact words, in `main_window.py`, `deepgram_client.py` and
        `duplicate_protection.py`. Translating them breaks routing silently.
        """
        for name in list(SOURCE_LANGUAGES) + list(TARGET_LANGUAGES):
            self.assertNotIn(
                name,
                strings._JA,
                f"{name!r} is a dropdown value AND a logic key; it must stay English",
            )

    def test_every_key_exists_verbatim_in_the_source(self):
        """A key that matches no literal is a typo that fails silently."""
        sources = []
        for path in sorted((PROJECT_ROOT / "alpha").rglob("*.py")):
            if path.name == "strings.py":
                continue
            sources.append(path.read_text(encoding="utf-8", errors="replace"))
        blob = "\n".join(sources)
        missing = [key for key in strings._JA if key not in blob]
        self.assertEqual(missing, [], f"keys that match no literal in alpha/: {missing}")


class TestTranslateAll(LanguageRestoringTestCase):
    def test_tuple_stays_a_tuple(self):
        strings.set_language("ja")
        result = strings.translate_all(("Start Listening", "Stop Listening"))
        self.assertIsInstance(result, tuple)
        self.assertEqual(result, ("認識を開始", "認識を停止"))

    def test_english_leaves_the_labels_alone(self):
        strings.set_language("en")
        labels = ("Start Listening", "Stop Listening", "Starting…")
        self.assertEqual(strings.translate_all(labels), labels)


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TestJapaneseFooterFits(LanguageRestoringTestCase):
    """The footer is where an English-tuned width last hid a real bug.

    Item 74's mic switch was unreachable between 800 and 1050 because a
    threshold was chosen against English text and never checked at the width
    the window actually opens at. Japanese labels change every one of those
    measurements, so the same guarantee is re-checked here in Japanese: the
    primary button must stay on screen, and no label may be squeezed below the
    width its own glyphs need.
    """

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

    def _build_footer(self, design_width):
        from alpha.ui.main_window import AlphaApp

        root = ctk.CTk()
        self.addCleanup(_close, root)
        root.geometry(f"{design_width}x650")
        root._font_cache = {}
        for name in self.REAL_METHODS:
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
        # An unrealised window makes every geometry assertion vacuous, so the
        # paint is forced before anything is measured.
        root.deiconify()
        for _ in range(6):
            root.update_idletasks()
            root.update()
        root._apply_footer_layout(root._design_width())
        for _ in range(8):
            root.update_idletasks()
            root.update()
        return root

    def test_no_japanese_footer_label_is_clipped(self):
        strings.set_language("ja")
        for design_width in (800, 900, 1050, 1280):
            with self.subTest(width=design_width):
                root = self._build_footer(design_width)
                for button in root._footer_buttons:
                    if not button.winfo_ismapped():
                        continue
                    label = button._text_label
                    font = tkfont.Font(root=root, font=label.cget("font"))
                    needed = font.measure(label.cget("text"))
                    self.assertGreaterEqual(
                        button.winfo_width(),
                        needed,
                        f"{label.cget('text')!r} needs {needed}px but the button "
                        f"is {button.winfo_width()}px at width {design_width}",
                    )

    def test_primary_button_is_reachable_at_every_width(self):
        """Start/Stop is the one control a meeting cannot proceed without."""
        strings.set_language("ja")
        for design_width in (800, 900, 1050, 1280):
            with self.subTest(width=design_width):
                root = self._build_footer(design_width)
                primary = root._footer_buttons[0]
                self.assertTrue(
                    primary.winfo_ismapped(),
                    f"the primary button is not on screen at width {design_width}",
                )
                self.assertGreater(primary.winfo_width(), 0)


if __name__ == "__main__":
    unittest.main()
