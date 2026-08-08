"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, item 17.

Confirmed defect: every production caller decided what to write using one
lookup and then wrote through `TranscriptStore.update_last_segment`'s
reverse scan under a **separate** lock acquisition. Because the two
acquisitions are independent, a different-speaker row appended in between
makes the write land on an *older* row than the one the decision was made
from -- a check-then-act race that silently revises a stale line.

Three production write sites had this shape:
  * duplicate_protection.py `_apply_transcript_to_store` (read via
    get_last_segment_if_active)
  * main_window.py `_try_segment_repair` (read via the true-last
    get_last_segment())
  * main_window.py `_recover_interim_tail_on_stop` (read via
    _check_stop_tail_duplicate's last_segments[-1])

...plus one unsafe *read*: `_commit_transcript_item_to_store` derived
`previous_text` from `get_last_segment(speaker)`, whose reverse scan
reaches back past an intervening speaker.

All four now use the `..._if_active` variants, and the unsafe methods were
renamed to `..._unsafe_speaker_scan` so they cannot be reached by reflex.
They were renamed rather than deleted because
`tests/test_task2g_acceptance_gate.py` deliberately pins their behavior to
document the safe/unsafe delta.

The Stop-tail call site needed more than a swap: it **ignored the return
value**, so the strict variant alone would have silently dropped the
merged tail whenever it refused -- on the last-chance Stop path, where a
drop is permanent. It now appends instead, matching items 10/11/11b's
rule that a visible extra line beats a lost one.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402


class TestUnsafeVariantsNoLongerReachableByReflex(unittest.TestCase):
    def test_old_unsafe_write_name_is_gone(self):
        store = TranscriptStore()
        self.assertFalse(
            hasattr(store, "update_last_segment"),
            "the unsafe reverse-scan write must not be callable under its "
            "old, innocuous-looking name",
        )

    def test_old_speaker_filtered_read_no_longer_accepts_a_speaker(self):
        store = TranscriptStore()
        store.add_segment(1, "speaker one first line")
        store.add_segment(2, "speaker two interjects")
        with self.assertRaises(TypeError):
            store.get_last_segment(1)

    def test_dead_alias_removed(self):
        store = TranscriptStore()
        self.assertFalse(hasattr(store, "get_last_segment_for_speaker"))

    def test_no_arg_true_last_read_still_works(self):
        # Legitimately used by _try_segment_repair -- must not be collateral.
        store = TranscriptStore()
        store.add_segment(1, "first")
        store.add_segment(2, "second")
        self.assertEqual(store.get_last_segment().text, "second")

    def test_renamed_unsafe_variants_still_behave_as_documented(self):
        store = TranscriptStore()
        store.add_segment(1, "speaker one first line")
        store.add_segment(2, "speaker two interjects")

        self.assertEqual(
            store.get_last_segment_unsafe_speaker_scan(1).text,
            "speaker one first line",
        )
        self.assertTrue(
            store.update_last_segment_unsafe_speaker_scan(1, "overwritten")
        )
        self.assertEqual(store.get_all()[0].text, "overwritten")


class TestSafeWriteRefusesTheStaleRow(unittest.TestCase):
    """The exact race the three call sites were exposed to."""

    def test_intervening_speaker_makes_the_safe_write_refuse(self):
        store = TranscriptStore()
        store.add_segment(1, "speaker one first line")
        # The decision was made when speaker 1's row was last; this arrives
        # between the read and the write.
        store.add_segment(2, "speaker two interjects")

        self.assertFalse(
            store.update_last_segment_if_active(1, "merged onto a stale row")
        )
        self.assertEqual(store.get_all()[0].text, "speaker one first line")
        self.assertEqual(len(store.get_all()), 2)

    def test_unsafe_write_would_have_corrupted_the_stale_row(self):
        # Same setup through the old path -- proves the race was real and
        # not theoretical.
        store = TranscriptStore()
        store.add_segment(1, "speaker one first line")
        store.add_segment(2, "speaker two interjects")

        self.assertTrue(
            store.update_last_segment_unsafe_speaker_scan(1, "merged onto a stale row")
        )
        self.assertEqual(store.get_all()[0].text, "merged onto a stale row")

    def test_safe_write_still_updates_the_true_last_row(self):
        store = TranscriptStore()
        store.add_segment(1, "speaker one first line")
        self.assertTrue(store.update_last_segment_if_active(1, "extended"))
        self.assertEqual(store.get_all()[0].text, "extended")
        self.assertEqual(len(store.get_all()), 1)


if __name__ == "__main__":
    unittest.main()
