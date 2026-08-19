"""A revised translation must replace its superseded line, not stack above it.

WHAT WAS BROKEN
---------------
`_clear_translation_loading_item` records where it wrote a completed
translation so `_remove_translation_item_for_utterance` can take that exact
line out when a revision supersedes it:

    start_idx = box.index(tk.END)
    box.insert(tk.END, label)
    box.insert(tk.END, cleaned + "\\n", "body")
    box.mark_set(completed_mark, start_idx)

`tk.END` is one character PAST where `insert(tk.END, ...)` actually writes,
because Tk maintains a trailing newline of its own. The mark therefore landed
on the line AFTER the translation, and the removal path's
`delete(mark, "mark lineend + 1 chars")` deleted a newline while the stale
translation stayed on screen. Probed against real Tk: removing the middle of
three completed translations left all three.

The same `start_idx` also sized the `speaker_label` tag range, so that was off
by a line too.

This is the third instance of one shape in this file. `interim_anchor` and
`segment_anchor` were the other two, fixed in the same pass, and the fix is
identical in all three: re-establish the empty last line so the position is a
line start, measure at `"end-1c"` rather than `"end"`, and give the mark LEFT
gravity so a later append at that position cannot carry it forward.

A fourth instance exists at the pending-placeholder mark
(`box.mark_set(mark_name, "end")`), but that branch is gated off by
`TRANSLATION_PENDING_PLACEHOLDER_VISIBLE = False` — item 68 disabled it for
this very hazard — so it is left alone and logged rather than changed.

WHY NOTHING CAUGHT IT
---------------------
Only visible when the real method writes into a real Tk widget. Existing
coverage asserts on the identity registry rather than on the widget, and
`UI_CHANGE_BASELINE_AUDIT.md` records that both Tk-touching test files are
skipped under `SKIP_TK_INTEGRATION_TESTS=1`.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless CI without a display
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TranslationRevisionReplacesLineTest(unittest.TestCase):
    """Drives the real `_clear_translation_loading_item`."""

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.root = tk.Tk()
        self.root.withdraw()
        self.box = tk.Text(self.root)

        class Host:
            _clear_translation_loading_item = (
                AlphaApp._clear_translation_loading_item
            )
            _remove_translation_item_for_utterance = (
                AlphaApp._remove_translation_item_for_utterance
            )
            _ui_speaker_label_text = AlphaApp._ui_speaker_label_text

            def __init__(self, box):
                self.translated_verse_box = box
                self._translation_loading_items = {}
                self._translation_items_by_utterance = {}
                self.skips = []

            def _clear_text_placeholder(self, *a, **k):
                pass

            def _log_translation_display_skip(self, **kw):
                self.skips.append(kw)

        self.host = Host(self.box)

    def tearDown(self):
        self.root.destroy()

    def _complete(self, key, text, version=1, segment_id=None):
        self.host._clear_translation_loading_item(
            segment_id=segment_id if segment_id is not None else abs(hash(key)) % 10000,
            terminal_state="completed",
            replace_with_text=text,
            canonical_utterance_id=key,
            source_version=version,
        )

    def _content(self):
        return self.box.get("1.0", "end")

    def _lines(self):
        return [l for l in self._content().splitlines() if l.strip()]

    def test_three_translations_write_three_lines(self):
        self._complete("u1", "first translation")
        self._complete("u2", "second translation")
        self._complete("u3", "third translation")
        self.assertEqual(len(self._lines()), 3, self._content())

    def test_removing_one_takes_out_exactly_that_line(self):
        self._complete("u1", "first translation")
        self._complete("u2", "second translation")
        self._complete("u3", "third translation")
        removed = self.host._remove_translation_item_for_utterance(
            canonical_utterance_id="u2", source_version=1
        )
        self.assertTrue(removed, "the removal reported failure")
        content = self._content()
        self.assertNotIn("second translation", content, content)
        self.assertIn("first translation", content)
        self.assertIn("third translation", content)
        self.assertEqual(len(self._lines()), 2, content)

    def test_a_revision_does_not_leave_the_superseded_text_on_screen(self):
        self._complete("u1", "the original wording", version=1)
        self.host._remove_translation_item_for_utterance(
            canonical_utterance_id="u1", source_version=1
        )
        self._complete("u1", "the revised wording", version=2)
        content = self._content()
        self.assertNotIn("the original wording", content, content)
        self.assertIn("the revised wording", content)
        self.assertEqual(len(self._lines()), 1, content)

    def test_each_completed_translation_is_one_logical_line(self):
        """The removal range is `mark -> mark lineend + 1 chars`, exactly one
        logical line, so this function must never write two."""
        self._complete("u1", "a translation with several words in it")
        self.assertEqual(len(self._lines()), 1, self._content())

    def test_the_completed_mark_covers_its_own_text(self):
        """Pins the mechanism. If the mark drifts past its line again the
        removal silently deletes a newline instead."""
        self._complete("u1", "anchored translation")
        entry = self.host._translation_items_by_utterance.get("u1")
        self.assertIsNotNone(entry)
        mark = entry.get("mark")
        self.assertIsNotNone(mark, "no mark recorded for the completed line")
        self.assertIn(mark, self.box.mark_names())
        self.assertIn("anchored translation", self.box.get(mark, "end"))
        self.assertEqual(self.box.mark_gravity(mark), "left")


if __name__ == "__main__":
    unittest.main()
