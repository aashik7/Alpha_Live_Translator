"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, item 12.

Confirmed defect: main_window.py::_should_repair_previous_segment used
`if norm_curr in norm_prev: return False, "current_contained_in_previous"`
-- current counted as "nothing new" whenever it was a literal substring
ANYWHERE inside previous, including the middle. A rewording/correction of
previous that happens to share a verbatim chunk somewhere in the old text
was misclassified as non-continuation, so _try_segment_repair never
merged the correction in.

Note on severity (traced via _try_segment_repair -> the caller at
main_window.py ~line 6253): when should_repair is False, `current` still
falls through to _meeting_buffer_process_candidate / commit as its own
segment downstream -- it is never dropped. This is a missed-merge /
transcript-quality bug (previous stays uncorrected, current becomes a
separate line), not a silent content-loss bug like items 10/11/19.

The fix narrows the check to prefix-or-suffix of previous -- the only
shapes that evidence current is a truncated partial repeat rather than a
coincidental substring match.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402


class _SourceLanguageVar:
    def get(self):
        return "English"


class _Host:
    _should_repair_previous_segment = AlphaApp._should_repair_previous_segment
    _is_standalone_short_reply = AlphaApp._is_standalone_short_reply
    _selected_source_language_ui = AlphaApp._selected_source_language_ui
    _looks_incomplete_segment = AlphaApp._looks_incomplete_segment
    _ends_with_english_connector = AlphaApp._ends_with_english_connector
    _segment_repair_gap_ms = AlphaApp._segment_repair_gap_ms
    _normalize_compare = AlphaApp._normalize_compare
    _has_strong_sentence_ending = AlphaApp._has_strong_sentence_ending
    _text_looks_english_or_romaji = AlphaApp._text_looks_english_or_romaji
    _is_japanese_manual_mode = AlphaApp._is_japanese_manual_mode
    _strip_language_flag = AlphaApp._strip_language_flag

    _MEETING_BUFFER_SHORT_COMPLETE = AlphaApp._MEETING_BUFFER_SHORT_COMPLETE
    _MEETING_BUFFER_WEAK_ENDINGS = AlphaApp._MEETING_BUFFER_WEAK_ENDINGS
    _STRONG_SENTENCE_ENDINGS = AlphaApp._STRONG_SENTENCE_ENDINGS

    def __init__(self):
        self.source_language = _SourceLanguageVar()


def _check(previous, current, speaker=1):
    host = _Host()
    meta = {"speaker": speaker}
    return host._should_repair_previous_segment(previous, current, meta, meta)


class TestCoincidentalMiddleSubstringNoLongerBlocksRepair(unittest.TestCase):
    def test_reworded_previous_sharing_a_verbatim_chunk_is_not_skipped(self):
        # previous is incomplete (no strong sentence ending) so the repair
        # gate is open; current is a short verbatim chunk that appears in
        # the MIDDLE of previous, not as a prefix or suffix.
        previous = "so anyway I think the budget number was around three"
        current = "budget number"
        self.assertIn(current, previous)
        self.assertFalse(previous.startswith(current))
        self.assertFalse(previous.endswith(current))

        should_repair, reason = _check(previous, current)

        self.assertNotEqual(
            reason,
            "current_contained_in_previous",
            "a coincidental mid-line substring match must not block repair",
        )


class TestGenuineContainmentStillBlocksRepair(unittest.TestCase):
    def test_current_is_a_prefix_of_previous_still_skipped(self):
        previous = "so anyway I think the budget"
        current = "so anyway I think"
        self.assertTrue(previous.startswith(current))

        should_repair, reason = _check(previous, current)

        self.assertFalse(should_repair)
        self.assertEqual(reason, "current_contained_in_previous")

    def test_current_is_a_suffix_of_previous_still_skipped(self):
        previous = "so anyway I think the budget"
        current = "the budget"
        self.assertTrue(previous.endswith(current))

        should_repair, reason = _check(previous, current)

        self.assertFalse(should_repair)
        self.assertEqual(reason, "current_contained_in_previous")

    def test_exact_duplicate_still_skipped(self):
        text = "so anyway I think"
        should_repair, reason = _check(text, text)
        self.assertFalse(should_repair)
        self.assertEqual(reason, "exact_duplicate")


if __name__ == "__main__":
    unittest.main()
