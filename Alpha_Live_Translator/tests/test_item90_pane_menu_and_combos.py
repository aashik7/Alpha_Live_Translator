"""Item 90 -- two things that were reported as regressions, not requests.

1. **"Clear All Text" vanished from the right-click menu.** Item 88d replaced
   it with "Copy Transcript" rather than adding beside it. Copying on
   right-click was asked for; removing Clear was not. Both are there now, with
   a separator between them, which is the actual guard against the slip of the
   mouse that motivated the removal.

2. **Only the arrow opened a dropdown.** CustomTkinter 5.2.2 binds `<Button-1>`
   to the canvas tags "right_parts" and "dropdown_arrow" only -- read out of
   `inspect.getsource(ctk.CTkComboBox)`, not assumed -- so clicking the text
   half of a readonly combo did nothing at all.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import strings  # noqa: E402


def _close(root):
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


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TheRightClickMenuKeepsBoth(unittest.TestCase):
    """Drives the real `_create_context_menu`.

    Both panes are None, which the method already skips over, so the menu it
    builds is the real one without needing the text widgets built too.
    """

    def setUp(self):
        root = ctk.CTk()
        self.addCleanup(_close, root)
        self.addCleanup(strings.set_language, strings.get_language())
        strings.set_language("en")
        root.initial_verse_box = None
        root.translated_verse_box = None
        for name in ("clear_text", "copy_live_transcript_to_clipboard",
                     "copy_translation_to_clipboard"):
            setattr(root, name, lambda *a, **k: None)
        root._create_context_menu = types.MethodType(
            AlphaApp._create_context_menu, root
        )
        root._create_context_menu()
        self.root = root
        self.menu = root.context_menu

    def _labels(self):
        out = []
        for index in range(self.menu.index("end") + 1):
            try:
                out.append(self.menu.entrycget(index, "label"))
            except Exception:
                out.append("<separator>")
        return out

    def test_copy_is_still_first(self):
        self.assertEqual(self._labels()[0], "Copy Transcript")

    def test_clear_all_text_is_back(self):
        self.assertIn("Clear All Text", self._labels())

    def test_a_separator_stands_between_them(self):
        labels = self._labels()
        self.assertEqual(
            labels.index("<separator>") - labels.index("Copy Transcript"), 1,
            f"nothing separates copy from clear: {labels}",
        )

    def test_clear_actually_clears(self):
        """A label with no command would look right and do nothing."""
        called = []
        self.root.clear_text = lambda: called.append(True)
        self.root._create_context_menu()
        menu = self.root.context_menu
        index = next(
            i for i in range(menu.index("end") + 1)
            if menu.type(i) == "command"
            and menu.entrycget(i, "label") == "Clear All Text"
        )
        menu.invoke(index)
        self.assertEqual(called, [True])


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TheWholeComboOpensTheDropdown(unittest.TestCase):
    def setUp(self):
        root = ctk.CTk()
        self.addCleanup(_close, root)
        self.root = root
        root.source_combo = ctk.CTkComboBox(
            root, values=["English", "Japanese"], state="readonly"
        )
        for name in ("target_combo", "source_combo_menu", "target_combo_menu",
                     "ui_language_combo_menu"):
            setattr(root, name, None)
        root._make_combos_fully_clickable = types.MethodType(
            AlphaApp._make_combos_fully_clickable, root
        )

    def test_customtkinter_leaves_the_entry_unbound(self):
        """The premise. If a future version binds it, this fix is redundant."""
        before = self.root.source_combo._entry.bind("<Button-1>")
        self.assertFalse(before, f"entry already had a binding: {before!r}")

    def test_the_entry_opens_the_dropdown_afterwards(self):
        self.root._make_combos_fully_clickable()
        self.assertTrue(self.root.source_combo._entry.bind("<Button-1>"))

    def test_it_survives_combos_that_do_not_exist_yet(self):
        """Four of the five are None here, as they are before the hamburger is
        built. A crash there would take the whole window down."""
        self.root._make_combos_fully_clickable()

    def test_the_cursor_says_it_is_clickable(self):
        self.root._make_combos_fully_clickable()
        self.assertEqual(self.root.source_combo._entry.cget("cursor"), "hand2")


if __name__ == "__main__":
    unittest.main()
