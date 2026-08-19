"""The interim preview must be REPLACED each tick, never stacked.

WHAT WAS BROKEN
---------------
`_update_interim_line_only` set the anchor with `mark_set("interim_anchor",
"end")` and then inserted the preview with `insert("end", ...)`. Tk's `"end"`
index sits past the final newline, and a mark placed there keeps the DEFAULT
right gravity, so the inserted preview lands BEFORE the mark. The paired
`_remove_interim_line_from_display` then ran `delete("interim_anchor", "end")`
over an empty range and removed nothing.

Driven over four interim ticks against a real `tk.Text`, the pane held four
stacked preview rows and four hourglasses where one is correct:

    Speaker: a committed sentence.
    Speaker: hello wor ⏳
    Speaker: hello world how ⏳
    Speaker: hello world how are you ⏳
    Speaker: hello world how are you doing today ⏳

Five anchor/gravity combinations were measured against real Tk. Only
`"end-1c"` with LEFT gravity both collapses to one preview and leaves the
committed text alone:

    mark_set("end")                  -> 3 hourglasses   (what shipped)
    mark_set("end-1c")               -> 3
    mark_set("end")     + left       -> 3
    mark_set("end-1c")  + right      -> 3
    mark_set("end-1c")  + left       -> 1   <- correct

`"end-1c"` is the position where `insert("end", ...)` actually places text, and
left gravity is what keeps the mark from being carried along by it.

WHY NOTHING CAUGHT IT
---------------------
This is only visible when the REAL app method writes into a REAL Tk widget.
The existing coverage either binds the mixin or substitutes a fake box, and
`UI_CHANGE_BASELINE_AUDIT.md` records that both Tk-touching test files are
skipped under `SKIP_TK_INTEGRATION_TESTS=1`. This test therefore builds its own
headless root and skips only when Tk genuinely cannot start, rather than
honouring that flag -- a flag-gated version of this test would not have caught
the bug it exists for.

The same audit, and item 75's ledger row, both stated that right gravity puts
appended text INSIDE the delete range. That is backwards; it puts it outside,
which is the whole defect. Both were corrected with this fix.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tkinter as tk

    _root_probe = tk.Tk()
    _root_probe.withdraw()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless CI without a display
    TK_AVAILABLE = False

HOURGLASS = "⏳"


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class InterimPreviewReplacementTest(unittest.TestCase):
    """Drives the real AlphaApp methods, deliberately not the mixin."""

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.root = tk.Tk()
        self.root.withdraw()
        self.box = tk.Text(self.root)
        box = self.box

        class Host:
            _update_interim_line_only = AlphaApp._update_interim_line_only
            _remove_interim_line_from_display = (
                AlphaApp._remove_interim_line_from_display
            )
            _interim_preview_lines = AlphaApp._interim_preview_lines
            _ui_speaker_label_text = AlphaApp._ui_speaker_label_text
            _speaker_tag = AlphaApp._speaker_tag
            # `_interim_preview_lines` groups for English only; without this
            # the multi-line case silently degrades to one line and the test
            # that covers whole-preview removal stops covering anything.
            _listen_language = "en"
            _latest_interim_speaker = 1
            _last_operation_hint = ""

            def _transcript_box(self):
                return box

            def _interim_log(self, *a, **k):
                pass

            def _refresh_transcript_scrollbar(self, *a, **k):
                pass

        self.host = Host()
        self.box.insert("end", "Speaker: a committed sentence.\n")

    def tearDown(self):
        self.root.destroy()

    def _tick(self, text):
        self.host._latest_interim_text = text
        self.host._update_interim_line_only()

    def _content(self):
        return self.box.get("1.0", "end")

    def test_four_ticks_leave_exactly_one_preview(self):
        for text in (
            "hello wor",
            "hello world how",
            "hello world how are you",
            "hello world how are you doing today",
        ):
            self._tick(text)
        content = self._content()
        self.assertEqual(
            content.count(HOURGLASS),
            1,
            f"the preview stacked instead of being replaced:\n{content}",
        )

    def test_only_the_latest_preview_text_survives(self):
        self._tick("first draft")
        self._tick("second draft")
        content = self._content()
        self.assertNotIn("first draft", content)
        self.assertIn("second draft", content)

    def test_committed_text_above_the_preview_is_never_touched(self):
        for text in ("one", "one two", "one two three"):
            self._tick(text)
        self.assertIn("Speaker: a committed sentence.", self._content())

    def test_an_empty_interim_clears_the_preview_entirely(self):
        self._tick("something showing")
        self.assertIn(HOURGLASS, self._content())
        self._tick("")
        content = self._content()
        self.assertNotIn(HOURGLASS, content)
        self.assertIn("Speaker: a committed sentence.", content)

    def test_a_multi_line_preview_is_removed_whole(self):
        """Item 69 renders a long preview as several lines; all of them belong
        to the same anchor and must go together."""
        long_preview = (
            "This is a first sentence about something. This is a second "
            "sentence that follows on. This is a third sentence closing the "
            "group. And here is a fourth one starting the next. A fifth one "
            "keeps it going. A sixth finishes the thought entirely."
        )
        self._tick(long_preview)
        self.assertGreater(
            len([l for l in self._content().splitlines() if l.strip()]),
            2,
            "expected a multi-line preview for this input",
        )
        self._tick("short again")
        content = self._content()
        self.assertEqual(content.count(HOURGLASS), 1, content)
        self.assertNotIn("first sentence", content)

    def test_the_anchor_is_left_gravity_at_the_real_insert_point(self):
        """Pins the mechanism, not just the symptom: if either half of this is
        reverted the preview silently stacks again."""
        self._tick("showing")
        self.assertIn("interim_anchor", self.box.mark_names())
        self.assertEqual(self.box.mark_gravity("interim_anchor"), "left")
        self.assertNotEqual(
            self.box.get("interim_anchor", "end"),
            "",
            "the anchor sits past the preview, so removal would delete nothing",
        )


if __name__ == "__main__":
    unittest.main()
