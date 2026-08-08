"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, item 10 (audit §3.4 row 2).

Confirmed defect: main_window.py::_check_stop_tail_duplicate decided the
leftover interim was "already committed" whenever it was ANY substring of
ANY of the last 5 committed segments (`norm_interim in norm_seg`). This
runs at Stop on the last-chance commit path and returns
`commit_text=None`, so a false match there loses the text permanently --
the highest-severity instance of the containment anti-pattern in the
main_window group.

An interim is the in-progress hypothesis building toward a final, so the
real evidence that it is already committed is that a committed segment
*equals* it or *starts with* it. An arbitrary interior substring is
coincidence: a short but genuinely new closing utterance that happens to
appear somewhere inside an earlier line was silently discarded.

The fix narrows the match to equality-or-prefix. These tests pin both
sides: the coincidental interior match must now survive, and every
genuine already-committed shape must still be skipped.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402


class _Segment:
    def __init__(self, text, speaker=1):
        self.text = text
        self.speaker = speaker


class _Store:
    def __init__(self, segments):
        self._segments = list(segments)

    def get_all(self):
        return list(self._segments)


class _Host:
    _check_stop_tail_duplicate = AlphaApp._check_stop_tail_duplicate
    _merge_text_with_overlap_info = AlphaApp._merge_text_with_overlap_info

    def __init__(self, segments):
        self.transcript_store = _Store(segments)

    def _normalize_compare(self, text):
        return " ".join((text or "").strip().lower().split())

    def _is_japanese_manual_mode(self):
        return False


class TestCoincidentalInteriorMatchIsNotDropped(unittest.TestCase):
    def test_short_new_utterance_inside_older_line_is_preserved(self):
        # A genuinely new closing utterance whose text also appears in the
        # MIDDLE of an earlier committed line. Pre-fix this was
        # skip_already_committed with commit_text=None -> permanent loss.
        # Note the interim must match the older segment's interior exactly
        # (no trailing punctuation difference) for this to be a real
        # reproduction -- an earlier draft of this test used "Thank you."
        # against "...thank you for joining...", which never matched at all
        # and so passed even against the pre-fix code.
        host = _Host([
            _Segment("Well, thank you for joining us today, everyone."),
            _Segment("Let's wrap up here."),
        ])
        result = host._check_stop_tail_duplicate("thank you for")

        self.assertNotEqual(result["decision"], "skip_already_committed")
        self.assertIsNotNone(
            result["commit_text"],
            "a coincidental interior match must not discard the text",
        )

    def test_interior_match_against_the_last_segment_is_also_preserved(self):
        host = _Host([_Segment("I said okay to that plan earlier.")])
        result = host._check_stop_tail_duplicate("okay to that")

        self.assertNotEqual(result["decision"], "skip_already_committed")
        self.assertIsNotNone(result["commit_text"])


class TestGenuinelyCommittedTailsAreStillSkipped(unittest.TestCase):
    def test_exact_match_is_still_skipped(self):
        host = _Host([
            _Segment("Some earlier line."),
            _Segment("This is the last sentence."),
        ])
        result = host._check_stop_tail_duplicate("This is the last sentence.")
        self.assertEqual(result["decision"], "skip_already_committed")
        self.assertIsNone(result["commit_text"])

    def test_exact_match_against_an_older_segment_is_still_skipped(self):
        host = _Host([
            _Segment("This is the last sentence."),
            _Segment("Something else entirely."),
        ])
        result = host._check_stop_tail_duplicate("This is the last sentence.")
        self.assertEqual(result["decision"], "skip_already_committed")

    def test_interim_prefix_of_committed_final_is_still_skipped(self):
        # The normal shape: the interim was mid-way through the utterance
        # when the completed final committed.
        host = _Host([_Segment("This is the last sentence of this test.")])
        result = host._check_stop_tail_duplicate("This is the last")
        self.assertEqual(result["decision"], "skip_already_committed")
        self.assertIsNone(result["commit_text"])

    def test_too_short_interim_still_short_circuits(self):
        host = _Host([_Segment("Anything at all.")])
        result = host._check_stop_tail_duplicate("ok")
        self.assertEqual(result["decision"], "skip_too_short")

    def test_genuinely_new_tail_still_commits(self):
        host = _Host([_Segment("Completely unrelated earlier line.")])
        result = host._check_stop_tail_duplicate("And here is a brand new closing thought.")
        self.assertEqual(result["decision"], "commit_new_tail")
        self.assertEqual(
            result["commit_text"], "And here is a brand new closing thought."
        )


if __name__ == "__main__":
    unittest.main()
