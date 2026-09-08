"""Phase 6, the follow-tail leftovers from the reading-pane review.

Two things the follow-tail work left behind:

1. `_render_transcript_from_store_now` rebuilds the pane through
   `_insert_formatted_text`, which does `delete("1.0", "end")` first. A reader
   who had scrolled up therefore lost their place and landed at the top --
   `scroll_to_tail` correctly declined to jump them to the bottom, but nothing
   put them back where they were.

2. Only the TRANSCRIPT writer had a behavioural test. The translation pane is
   the one the reader actually complained about, and all three of its writers
   (`_show_translation_loading_item`, `_clear_translation_loading_item`,
   `_append_translation_result`) must keep routing through `scroll_to_tail`
   rather than a bare `see(tk.END)`.
"""

import ast
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TRANSLATION_WRITERS = (
    "_show_translation_loading_item",
    "_clear_translation_loading_item",
    "_append_translation_result",
)


class FakeBox:
    """A tk.Text stand-in that records what was asked of it."""

    def __init__(self, follow_tail=True, top_index="42.0"):
        self._follow_tail = follow_tail
        self._top_index = top_index
        self.seen: list[str] = []

    def index(self, spec):
        if spec == "@0,0":
            return self._top_index
        raise AssertionError(f"unexpected index({spec!r})")

    def see(self, where):
        self.seen.append(str(where))


class TheReaderKeepsTheirPlaceAcrossARebuild(unittest.TestCase):
    def _capture(self):
        from alpha.ui.follow_tail import capture_reader_position

        return capture_reader_position

    def test_a_scrolled_up_reader_is_put_back(self):
        box = FakeBox(follow_tail=False, top_index="17.0")
        restore = self._capture()(box)
        self.assertIsNotNone(restore, "no restore was captured for a scrolled-up reader")
        restore()
        self.assertEqual(
            box.seen, ["17.0"], "the reader was not returned to where they were"
        )

    def test_a_reader_at_the_tail_is_left_alone(self):
        """`scroll_to_tail` already handles them; restoring would fight it."""
        box = FakeBox(follow_tail=True)
        self.assertIsNone(self._capture()(box))

    def test_a_widget_that_never_opted_in_is_left_alone(self):
        box = FakeBox()
        del box._follow_tail
        self.assertIsNone(self._capture()(box))

    def test_it_never_raises_on_a_widget_that_cannot_answer(self):
        class Hostile:
            _follow_tail = False

            def index(self, spec):
                raise RuntimeError("no such widget")

        self.assertIsNone(self._capture()(Hostile()))

    def test_a_restore_that_fails_is_swallowed(self):
        class HalfDead(FakeBox):
            def see(self, where):
                raise RuntimeError("widget destroyed mid-render")

        restore = self._capture()(HalfDead(follow_tail=False))
        self.assertIsNotNone(restore)
        restore()  # must not raise


class TheRebuildPathUsesIt(unittest.TestCase):
    def _fn(self, name):
        source = (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8", errors="replace"
        )
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.unparse(node)
        self.fail(f"{name} not found")

    def test_the_store_rebuild_captures_and_restores(self):
        body = self._fn("_render_transcript_from_store_now")
        self.assertIn(
            "capture_reader_position",
            body,
            "the rebuild still drops the reader at the top of the pane",
        )


class TheTranslationPaneRespectsTheReader(unittest.TestCase):
    """The pane the reader actually complained about."""

    def _source(self):
        return (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8", errors="replace"
        )

    def _fn(self, name):
        for node in ast.walk(ast.parse(self._source())):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail(f"{name} not found")

    def test_every_translation_writer_routes_through_scroll_to_tail(self):
        for name in TRANSLATION_WRITERS:
            body = ast.unparse(self._fn(name))
            self.assertIn(
                "scroll_to_tail",
                body,
                f"{name} does not defer to the reader's follow-tail state",
            )

    def test_no_translation_writer_forces_a_bare_jump_to_the_end(self):
        """A bare `see(tk.END)` would drag a scrolled-up reader to the bottom."""
        for name in TRANSLATION_WRITERS:
            node = self._fn(name)
            for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
                if getattr(call.func, "attr", "") != "see":
                    continue
                arg = ast.unparse(call.args[0]) if call.args else ""
                self.fail(
                    f"{name} calls see({arg}) directly instead of scroll_to_tail"
                )

    def test_the_gate_actually_blocks_a_scrolled_up_reader(self):
        """Behavioural, not structural: the shared helper must decline."""
        from alpha.ui.follow_tail import scroll_to_tail

        box = FakeBox(follow_tail=False)
        scroll_to_tail(box)
        self.assertEqual(box.seen, [], "the translation pane jumped a reader to the end")

    def test_the_gate_still_follows_for_a_reader_at_the_tail(self):
        from alpha.ui.follow_tail import scroll_to_tail

        box = FakeBox(follow_tail=True)
        scroll_to_tail(box)
        self.assertEqual(len(box.seen), 1, "the pane stopped following the tail")


if __name__ == "__main__":
    unittest.main()
