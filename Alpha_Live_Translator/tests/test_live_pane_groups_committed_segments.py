"""The live pane must group English lines the same way the export does.

CONFIRMED BY A LIVE RUN, NOT ARGUED
-----------------------------------
This was filed as PLAUSIBLE during the item 74 verification and could not be
reproduced then. The live run of 2026-08-20 confirmed it from its own artifacts:
one session produced two different shapes of the same text.

    transcripts/Alpha output.txt   84 lines, median 24 words, max 354 chars,
                                   nothing over 400        <- correct
    the live pane                  36 lines, median 50 words, max 1509 chars,
                                   8 over 400              <- one line per record

36 is exactly the exported record count, i.e. the pane rendered one unbroken
line per commit with no grouping applied.

WHY THAT PATH
-------------
`duplicate_protection.py` routes every translation-eligible commit to
`_on_store_segment_added` / `_on_store_segment_updated`, and both end at
`_insert_speaker_segment_line`. The grouped renderer
`_render_transcript_from_store` is only reached when a segment is NOT
translation-eligible, or when neither hook exists -- and `AlphaApp` defines
both. So the pane a user watches during a meeting was the one path of the four
that C8 enumerates which did not group, and text visibly reflowed the moment it
committed, which is the symptom item 69 was filed for.

THE CAP HAD TO MOVE WITH IT
---------------------------
`MAX_RENDERED_UI_SEGMENTS` was enforced by deleting ONE logical line per excess
SEGMENT. That is only correct while a segment is always one line. Grouping makes
it 1-3, so counting segments would under-trim by the lines-per-entry factor and
strand half-entries at the top of the pane. This is item 74(a), which was
measured LATENT at the time and is made live by the change above; the trim now
records the lines each segment actually wrote.
"""

import sys
import unittest
from collections import deque
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

from alpha.constants import MAX_RENDERED_UI_SEGMENTS  # noqa: E402
from alpha.summary.transcript_store import TranscriptStore  # noqa: E402

FOUR_SENTENCES = (
    "Imagine being able to pick up any book and remember everything from it. "
    "This kind of superpower memory recall might feel like fiction. "
    "But there are people in the real world that seem to carry an incredible "
    "ability. They hold on to information in a way most of us cannot manage."
)


def _host(language="en"):
    from alpha.ui.main_window import AlphaApp

    class Host:
        _insert_speaker_segment_line = AlphaApp._insert_speaker_segment_line
        _readable_segment_parts = AlphaApp._readable_segment_parts
        _segment_language = AlphaApp._segment_language
        _ui_speaker_label_text = AlphaApp._ui_speaker_label_text
        _speaker_tag = AlphaApp._speaker_tag
        # Item 75 added the meta row and the current-entry highlight to the
        # same insert, so the host carries those collaborators too -- same
        # reason as the item 82 line above.
        _ensure_entry_tags = AlphaApp._ensure_entry_tags
        _entry_meta_text = AlphaApp._entry_meta_text
        _entry_meta_time_text = AlphaApp._entry_meta_time_text
        _highlight_current_entry = AlphaApp._highlight_current_entry

        def __init__(self):
            self._listen_language = language
            self.transcript_store = TranscriptStore()
            self._displayed_segment_lines = deque()

    return Host()


def _lines(box):
    return [l for l in box.get("1.0", "end").splitlines() if l.strip()]


def _body_lines(box):
    """Only the lines carrying transcript text.

    Item 75 renders a tagged `entry_meta` row above each entry, so an entry is
    no longer one logical line. These assertions are about the BODY -- how the
    text was grouped and how many entries survive the cap -- so the meta rows
    are filtered by tag rather than by their text.
    """
    out = []
    for number, line in enumerate(box.get("1.0", "end").splitlines(), start=1):
        if not line.strip():
            continue
        if "entry_meta" in box.tag_names(f"{number}.0"):
            continue
        out.append(line)
    return out


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheLivePaneGroups(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.box = tk.Text(self.root)
        self.host = _host()

    def tearDown(self):
        self.root.destroy()

    def test_a_multi_sentence_commit_becomes_several_lines(self):
        written = self.host._insert_speaker_segment_line(self.box, 1, FOUR_SENTENCES)
        self.assertGreater(
            written, 1, "the commit was rendered as one unbroken line, as before"
        )
        self.assertEqual(len(_lines(self.box)), written)

    def test_no_rendered_line_is_a_wall_of_text(self):
        self.host._insert_speaker_segment_line(self.box, 1, FOUR_SENTENCES)
        self.assertLess(
            max(len(l) for l in _lines(self.box)),
            400,
            "a rendered line exceeded the readable width the export keeps to",
        )

    def test_no_word_is_lost_by_the_grouping(self):
        self.host._insert_speaker_segment_line(self.box, 1, FOUR_SENTENCES)
        rendered = " ".join(_lines(self.box))
        for word in FOUR_SENTENCES.replace(".", " ").split():
            self.assertIn(word, rendered, f"{word!r} was dropped by the grouping")

    def test_the_count_returned_matches_what_was_written(self):
        """The render cap trims by this number; if it lies, the cap trims the
        wrong amount."""
        for text in ("Short one.", FOUR_SENTENCES, "Another short line here."):
            box = tk.Text(self.root)
            written = self.host._insert_speaker_segment_line(box, 1, text)
            self.assertEqual(written, len(_lines(box)), repr(text))

    def test_japanese_is_never_regrouped(self):
        """C9: Japanese gets its boundaries from the assembler."""
        host = _host(language="ja")
        box = tk.Text(self.root)
        jp = "これは一つ目です。これは二つ目です。これは三つ目です。"
        written = host._insert_speaker_segment_line(box, 1, jp)
        self.assertEqual(
            len(_body_lines(box)), 1, "Japanese was split by the English rule"
        )
        # meta row + one body line; the body is what C9 protects.
        self.assertEqual(written, 2)

    def test_empty_text_writes_nothing(self):
        self.assertEqual(self.host._insert_speaker_segment_line(self.box, 1, "  "), 0)
        self.assertEqual(_lines(self.box), [])

    def test_it_falls_back_to_raw_text_when_grouping_fails(self):
        host = _host()

        class Boom:
            def _readable_parts(self, segment):
                raise RuntimeError("grouping unavailable")

        host.transcript_store = Boom()
        box = tk.Text(self.root)
        written = host._insert_speaker_segment_line(box, 1, FOUR_SENTENCES)
        self.assertEqual(len(_body_lines(box)), 1)
        self.assertEqual(written, len(_lines(box)))
        self.assertIn("Imagine being able", " ".join(_lines(box)))


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheRenderCapCountsLinesNotSegments(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def _fill(self, count, text):
        host = _host()
        box = tk.Text(self.root)
        limit = int(MAX_RENDERED_UI_SEGMENTS)
        history = host._displayed_segment_lines
        for _ in range(count):
            written = host._insert_speaker_segment_line(box, 1, text) or 1
            history.append(written)
            while len(history) > limit:
                stale = int(history.popleft() or 1)
                for _ in range(max(1, stale)):
                    try:
                        box.delete("1.0", "2.0")
                    except Exception:
                        break
        return box, host

    def test_multi_line_entries_still_bound_the_widget(self):
        limit = int(MAX_RENDERED_UI_SEGMENTS)
        box, host = self._fill(limit + 120, FOUR_SENTENCES)
        per_entry = host._displayed_segment_lines[0]
        self.assertGreater(per_entry, 1, "fixture did not produce a multi-line entry")
        self.assertEqual(
            len(_lines(box)),
            limit * per_entry,
            "the widget drifted from the cap once entries spanned several lines",
        )

    def test_the_oldest_entry_is_removed_whole(self):
        """Trimming by segment count left half-entries stranded at the top."""
        box, _ = self._fill(int(MAX_RENDERED_UI_SEGMENTS) + 5, FOUR_SENTENCES)
        # Item 75: an entry now STARTS with its meta row, so the top of the pane
        # being tagged `entry_meta` is the proof that a whole entry was removed
        # and the next one begins cleanly -- the same thing this test always
        # asserted, expressed against the current entry shape.
        self.assertIn(
            "entry_meta",
            box.tag_names("1.0"),
            f"the top of the pane is a stranded fragment: {_lines(box)[0][:60]!r}",
        )

    def test_a_single_line_entry_still_caps_exactly(self):
        limit = int(MAX_RENDERED_UI_SEGMENTS)
        box, _ = self._fill(limit + 40, "One short sentence.")
        # The cap bounds SEGMENTS, not lines; item 75 makes each of them a meta
        # row plus one body line, so the widget holds exactly twice the limit
        # and not one line more. Drift here is what the cap arithmetic breaking
        # would look like.
        self.assertEqual(len(_body_lines(box)), limit)
        self.assertEqual(len(_lines(box)), limit * 2)


if __name__ == "__main__":
    unittest.main()
