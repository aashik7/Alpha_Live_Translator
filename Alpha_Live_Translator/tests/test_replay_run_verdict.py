"""Regression tests for `tools/replay_run.py`'s content-loss verdict.

`CLIENT_DELIVERY_SPRINT_v5.md` item 38. The tool's first verdict function
matched committed utterances to ledger records on `canonical_utterance_id`
alone and scored all 6 replayable runs as loss-free, exiting 0 — while 14
real sentences were missing from the exports. The id reached the ledger
carrying the wrong text, so an id-level check could not see the loss it
was built to find.

`test_id_only_check_is_blind_to_the_collision` pins that failure mode
directly: it asserts the old measure reports nothing on input the new one
flags. That is the proof the fix is load-bearing, kept as an assertion
rather than a one-off manual revert so a later simplification back to
id-only matching fails here instead of silently going quiet again.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.replay_run import (  # noqa: E402
    UnknownRowShape,
    _dropped_content,
    _recorded_gaps_s,
    _unreached_utterances,
    classify_row,
)

# Shortened from run ...20260807-160529, id jp-utt-19dbf8832ec0: two
# textually disjoint sentences committed under one id, one ledger record.
UID = "jp-utt-19dbf8832ec0"
FIRST = "ですよ。違いますねでやっぱりこっちにいると日本の行事を味わうことが難しいので、"
SECOND = "だろう楽しい雰囲気とかもそんなには感じられないんですけど、"


class DroppedContentTest(unittest.TestCase):
    def test_id_collision_with_disjoint_text_is_a_loss(self):
        dropped = _dropped_content([(UID, FIRST), (UID, SECOND)], {UID: SECOND})
        self.assertEqual(1, len(dropped))
        self.assertEqual(FIRST, dropped[0]["text"])
        self.assertEqual("overwritten_by_id_collision", dropped[0]["reason"])

    def test_id_only_check_is_blind_to_the_collision(self):
        """The bug this file exists for. Same input, two measures."""
        commits = [(UID, FIRST), (UID, SECOND)]
        ledger = {UID: SECOND}
        self.assertEqual([], _unreached_utterances(commits, ledger))
        self.assertEqual(1, len(_dropped_content(commits, ledger)))

    def test_genuine_revision_is_not_a_loss(self):
        """An extension of what it replaces. Must not be flagged."""
        extended = FIRST + "そうですよね。"
        self.assertEqual([], _dropped_content([(UID, FIRST), (UID, extended)], {UID: extended}))

    def test_whitespace_difference_is_not_a_loss(self):
        """The ledger stores post-cleanup text; spacing must not count."""
        self.assertEqual([], _dropped_content([(UID, "a b c")], {UID: "abc"}))

    def test_missing_ledger_record_is_reported_separately(self):
        dropped = _dropped_content([(UID, FIRST)], {})
        self.assertEqual(["no_ledger_record"], [d["reason"] for d in dropped])
        self.assertEqual([UID], _unreached_utterances([(UID, FIRST)], {}))

    def test_export_cross_check_is_reported_when_available(self):
        """Second independent signal, so the verdict is not one field deep."""
        dropped = _dropped_content(
            [(UID, FIRST), (UID, SECOND)], {UID: SECOND}, export_text=SECOND
        )
        self.assertTrue(dropped[0]["absent_from_export"])
        present = _dropped_content(
            [(UID, FIRST), (UID, SECOND)], {UID: SECOND}, export_text=FIRST + SECOND
        )
        self.assertFalse(present[0]["absent_from_export"])

    def test_empty_commit_text_is_not_counted(self):
        self.assertEqual([], _dropped_content([(UID, "   ")], {}))


class RecordedGapsTest(unittest.TestCase):
    """Item 38b. Real inter-arrival gaps drive the real-timer replay's
    waits -- a wrong gap here means a wrong wall-clock wait, silently."""

    def test_gaps_are_consecutive_deltas(self):
        rows = [{"timestamp": 10.0}, {"timestamp": 14.5}, {"timestamp": 16.0}]
        self.assertEqual([4.5, 1.5], _recorded_gaps_s(rows))

    def test_one_row_has_no_gap(self):
        self.assertEqual([], _recorded_gaps_s([{"timestamp": 1.0}]))

    def test_non_monotonic_timestamp_clamps_to_zero_not_negative(self):
        """A replay tool must not hand time.sleep() a negative duration."""
        rows = [{"timestamp": 10.0}, {"timestamp": 9.0}]
        self.assertEqual([0.0], _recorded_gaps_s(rows))

    def test_missing_timestamp_is_zero_not_a_crash(self):
        rows = [{"timestamp": 10.0}, {}, {"timestamp": 12.0}]
        self.assertEqual([0.0, 0.0], _recorded_gaps_s(rows))


class ClassifyRowTest(unittest.TestCase):
    """Input partitioning never skips a row it does not recognise."""

    def test_ingress_carries_raw_deepgram_text_and_confidence(self):
        row = {"metadata": {"raw_deepgram_text": "x"}, "confidence": 0.99}
        self.assertEqual("ingress", classify_row(row))

    def test_assembler_re_emission_carries_neither(self):
        self.assertEqual("assembler_re_emission", classify_row({"metadata": {}}))

    def test_disagreeing_markers_raise_rather_than_being_skipped(self):
        with self.assertRaises(UnknownRowShape):
            classify_row({"metadata": {"raw_deepgram_text": "x"}, "confidence": None})
        with self.assertRaises(UnknownRowShape):
            classify_row({"metadata": {}, "confidence": 0.5})


if __name__ == "__main__":
    unittest.main()
