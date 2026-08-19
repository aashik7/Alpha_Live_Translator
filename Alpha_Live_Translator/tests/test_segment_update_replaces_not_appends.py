"""A corrected segment must REPLACE the displayed one, not be appended to it.

WHAT WAS BROKEN
---------------
`_on_store_segment_updated` re-renders the last displayed segment when its text
is corrected. It does that by deleting from `segment_anchor` to the end and
writing the segment again:

    if box.compare("segment_anchor", ">=", "1.0"):
        box.delete("segment_anchor", "end")
    ...
    self._insert_speaker_segment_line(box, speaker, text)

`_insert_speaker_segment_line` set that anchor AFTER writing, from
`"insert linestart"`. `insert` is the cursor mark, not the write position, so
the anchor landed on the line AFTER the segment and the delete removed only the
trailing newline. Driven through the real methods, the correction was appended
to its own predecessor on one line, with no separator:

    'Speaker: the first version of this segmentSpeaker: the CORRECTED version'

This is the same defect class as the interim preview stacking fixed in
`test_interim_preview_is_replaced_not_stacked.py` -- an anchor mark that does
not sit where the text is about to be written -- and it takes the same three
part fix: re-establish the empty last line so the anchor is at a line start,
mark `"end-1c"` (Tk keeps a trailing newline of its own, so `"end"` is one
character past the real write position), and set LEFT gravity so writing past
the mark does not carry it along.

WHY NOTHING CAUGHT IT
---------------------
Only visible when the real method writes into a real Tk widget. The existing
coverage for this path asserts on the store, not on the widget, and
`UI_CHANGE_BASELINE_AUDIT.md` records that both Tk-touching test files are
skipped under `SKIP_TK_INTEGRATION_TESTS=1`. This test builds its own headless
root and skips only if Tk cannot start, because a flag-gated version would not
have caught the bug it exists for.
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
class SegmentUpdateReplacesTest(unittest.TestCase):
    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.root = tk.Tk()
        self.root.withdraw()
        self.box = tk.Text(self.root)

        class Host:
            _insert_speaker_segment_line = AlphaApp._insert_speaker_segment_line
            _ui_speaker_label_text = AlphaApp._ui_speaker_label_text
            _speaker_tag = AlphaApp._speaker_tag

        self.host = Host()

    def tearDown(self):
        self.root.destroy()

    def _add(self, text, speaker=1):
        self.host._insert_speaker_segment_line(self.box, speaker, text)

    def _update_in_place(self):
        """The delete half of `_on_store_segment_updated`, verbatim."""
        box = self.box
        try:
            if box.compare("segment_anchor", ">=", "1.0"):
                box.delete("segment_anchor", "end")
            else:
                box.delete("end-2l linestart", "end")
        except Exception:
            box.delete("end-2l linestart", "end")

    def _content(self):
        return self.box.get("1.0", "end")

    def _lines(self):
        return [l for l in self._content().splitlines() if l.strip()]

    def test_a_corrected_segment_replaces_the_stale_one(self):
        self._add("the first version")
        self._update_in_place()
        self._add("the CORRECTED version")
        content = self._content()
        self.assertNotIn("first version", content, content)
        self.assertIn("CORRECTED version", content)

    def test_the_correction_does_not_land_on_the_same_line(self):
        self._add("the first version")
        self._update_in_place()
        self._add("the CORRECTED version")
        self.assertEqual(len(self._lines()), 1, self._content())

    def test_earlier_segments_are_untouched_by_an_update(self):
        self._add("segment one stays")
        self._add("segment two original", speaker=2)
        self._update_in_place()
        self._add("segment two CORRECTED", speaker=2)
        lines = self._lines()
        self.assertEqual(len(lines), 2, self._content())
        self.assertIn("segment one stays", lines[0])
        self.assertIn("segment two CORRECTED", lines[1])
        self.assertNotIn("segment two original", self._content())

    def test_repeated_updates_do_not_accumulate(self):
        self._add("version 1")
        for n in range(2, 6):
            self._update_in_place()
            self._add(f"version {n}")
        lines = self._lines()
        self.assertEqual(len(lines), 1, self._content())
        self.assertIn("version 5", lines[0])

    def test_each_segment_occupies_exactly_one_logical_line(self):
        """The render cap trims one logical line per segment, so a segment that
        spans two would break it. Pinned here because this function is where
        that would first change."""
        for text in ("one", "two", "three"):
            self._add(text)
        self.assertEqual(len(self._lines()), 3, self._content())

    def test_the_anchor_is_left_gravity_at_the_segment_start(self):
        """Pins the mechanism. If either half is reverted the update silently
        appends instead of replacing."""
        self._add("only segment")
        self.assertIn("segment_anchor", self.box.mark_names())
        self.assertEqual(self.box.mark_gravity("segment_anchor"), "left")
        self.assertIn("only segment", self.box.get("segment_anchor", "end"))


if __name__ == "__main__":
    unittest.main()
