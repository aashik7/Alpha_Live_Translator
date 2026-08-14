"""Items 68 and 69: what the user actually watches during a live meeting.

Both come from the two files the user shared on 2026-08-14.

**Item 68 -- `Speaker: … ⏳` placeholder rows.** `Translation - en.txt` holds 6
real translation blocks and 6 placeholder rows: half the pane was progress
hints. The row is only a hint -- `_clear_translation_loading_item` deletes it
and then appends the finished translation at `tk.END` regardless of where the
row sat, and ordering comes from the worker's `translation_sequence` buffer. So
hiding it cannot reorder anything.

The subtle part is the Tk MARK, and it is why this is not a one-line deletion.
The mark must be skipped along with the text: a mark set at `"end"` with nothing
under it ends up positioned *before* the next appended line, and the removal
path deletes `mark -> mark lineend + 1 chars` -- which would delete a real
translation. Both removal sites guard with `box.compare(...)` inside
`except Exception`, so an absent mark is a no-op; a dangling one is not.

**Item 69 -- the live preview is one growing paragraph.**
`Live Transcript - en.txt` has 283 UI lines of which only 5 are committed, and
the ⏳ preview grows past 2000 characters before settling. Grouping it is
inherently safe: the preview is deleted (`interim_anchor` -> `end`) and
rewritten on every tick, so several lines are removed together and no committed
text is involved.

Driven without Tk -- these are the pure decisions, borrowed onto a stub.
"""

import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha import constants  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402


class _Var:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value


class _PreviewHost:
    """Only what _interim_preview_lines touches."""

    _interim_preview_lines = AlphaApp._interim_preview_lines

    def __init__(self, language="en"):
        self._listen_language = language
        self.source_language = _Var(language)


LONG_PREVIEW = (
    "If you could give me 45 minutes twice a week that's all you need to do. "
    "You know, I hear this a lot now about this science based training. "
    "But I actually don't know what science they're talking about. "
    "This is all very interesting, but if it doesn't work in practicality, "
    "what's it worth? So I've just got my posture really good."
)


class InterimPreviewIsGroupedTest(unittest.TestCase):
    """Item 69."""

    def _lines(self, text, language="en"):
        return list(_PreviewHost(language)._interim_preview_lines(text))

    def test_a_long_preview_becomes_several_lines(self):
        lines = self._lines(LONG_PREVIEW)
        self.assertGreater(len(lines), 1, "preview still renders as one paragraph")

    def test_only_the_last_line_is_marked_pending(self):
        """One hourglass. Repeating it reads as several pending utterances."""
        lines = self._lines(LONG_PREVIEW)
        self.assertTrue(lines[-1][1])
        self.assertTrue(all(not is_last for _, is_last in lines[:-1]))

    def test_no_word_is_lost_or_added(self):
        lines = self._lines(LONG_PREVIEW)
        self.assertEqual(
            " ".join(text for text, _ in lines).split(), LONG_PREVIEW.split()
        )

    def test_a_short_preview_stays_one_line(self):
        lines = self._lines("If you could")
        self.assertEqual([(t, l) for t, l in lines], [("If you could", True)])

    def test_empty_preview_yields_nothing(self):
        self.assertEqual(self._lines(""), [])
        self.assertEqual(self._lines("   "), [])

    def test_japanese_preview_is_left_alone(self):
        """Japanese has its own boundaries; English terminators do not describe
        it."""
        text = "これは一つ目です。これは二つ目です。これは三つ目です。"
        lines = self._lines(text, language="ja")
        self.assertEqual(len(lines), 1)

    def test_grouping_can_be_switched_off(self):
        with patch.object(constants, "INTERIM_PREVIEW_LINE_GROUPING_ENABLED", False):
            import alpha.ui.main_window as mw

            with patch.object(mw, "INTERIM_PREVIEW_LINE_GROUPING_ENABLED", False):
                lines = self._lines(LONG_PREVIEW)
        self.assertEqual(len(lines), 1)

    def test_a_failure_falls_back_to_one_line_rather_than_raising(self):
        """This runs on the UI thread; raising there is worse than not
        grouping."""

        class Broken(_PreviewHost):
            @property
            def _listen_language(self):
                raise RuntimeError("boom")

            @_listen_language.setter
            def _listen_language(self, value):
                pass

        lines = list(Broken()._interim_preview_lines(LONG_PREVIEW))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][0], LONG_PREVIEW)


class PendingPlaceholderIsHiddenTest(unittest.TestCase):
    """Item 68."""

    def test_the_placeholder_is_off_by_default(self):
        self.assertFalse(constants.TRANSLATION_PENDING_PLACEHOLDER_VISIBLE)

    def test_the_text_and_the_mark_are_gated_together(self):
        """A mark without its row is worse than the row: it sits before the
        next appended line and its removal deletes a real translation."""
        src = inspect.getsource(AlphaApp._show_translation_loading_item)
        gate = src.index("if TRANSLATION_PENDING_PLACEHOLDER_VISIBLE:")
        self.assertGreater(src.index("box.mark_set(mark_name"), gate)
        self.assertGreater(src.index('"body"'), gate)

    def test_the_registry_entry_is_still_created(self):
        """Counts and revision tracking must not change -- only the pixels."""
        src = inspect.getsource(AlphaApp._show_translation_loading_item)
        gate = src.index("if TRANSLATION_PENDING_PLACEHOLDER_VISIBLE:")
        self.assertGreater(src.index("registry[int(segment_id)]"), gate)
        self.assertIn("mark_name = f\"tr_load_", src[:gate])

    def test_removal_tolerates_a_missing_mark(self):
        """What makes hiding the mark safe at all."""
        src = inspect.getsource(AlphaApp._clear_translation_loading_item)
        compare_at = src.index("box.compare(mark_name")
        self.assertIn("except Exception", src[compare_at:])

    def test_the_finished_translation_is_appended_at_the_end(self):
        """Proof the row is not an ordering slot: the real text goes to END,
        not to the mark."""
        src = inspect.getsource(AlphaApp._clear_translation_loading_item)
        self.assertIn("box.insert(tk.END, cleaned", src)


if __name__ == "__main__":
    unittest.main()
