"""Regression tests for BUG_FIX_ROADMAP.md item 19b.

Confirmed live defect (run `v3.3.5.5.8.5.26.5.3-20260809-050227`, ja):
`japanese_boundary_stabilizer.py::_map_output_contract` matched on the
ACTION NAME and forced `should_revise=True` for both `merge_with_previous`
and `merge_pending_and_current`. Those two are structurally different:

  merge_with_previous       merges `_previous_line` -- the line ALREADY
                            COMMITTED downstream. Revising it is correct,
                            and its call site passes update_previous=True.
  merge_pending_and_current merges `_pending` -- the stabilizer's OWN held
                            buffer, never committed anywhere. The result is
                            a brand-new utterance. Its call site deliberately
                            does NOT pass update_previous, i.e. it asks to
                            append.

Because the contract mapper matched the name, the second call site's intent
was overridden and the new utterance was written OVER an unrelated committed
record in place. Measured in that run: 3 of 3 `merge_pending_and_current`
events each destroyed a different committed sentence -- 194 characters of
real speech -- e.g.

    prev (committed) : 残り十六時間も起きてない。…仕事をしてるんですよね。
    incoming (new)   : 満足する仕事ができたら人生もより満足すると思うんだよね。
    -> merge_pending_and_current -> SUPERSEDE canon-000005 -> 85 chars destroyed

The correlation held 9/9 across every run with recorded data, and the run's
own `export_coverage_report.json` still said `export_lossless: true` because
it validates lineage rather than text.

These tests pin the contract at the level that actually decides the outcome:
the merged-pending result must APPEND, while a genuine previous-line merge
must still REVISE.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.japanese_boundary_stabilizer import (  # noqa: E402
    JapaneseBoundaryStabilizer,
)


def contract(action, *, update_previous=False, emit_now=True, suppress=False):
    """Call the real contract mapper without constructing a live session."""
    return JapaneseBoundaryStabilizer._map_output_contract(
        JapaneseBoundaryStabilizer.__new__(JapaneseBoundaryStabilizer),
        emit_now=emit_now,
        action=action,
        reason="test",
        output_text="なにか",
        update_previous=update_previous,
        suppress=suppress,
    )


class TestMergedPendingAppends(unittest.TestCase):
    """The destructive case: a merged pending buffer is a NEW utterance."""

    def test_merge_pending_and_current_appends(self):
        c = contract("merge_pending_and_current")

        self.assertFalse(
            c["should_revise"],
            "a merged pending buffer was never committed, so revising "
            "overwrites an unrelated committed record",
        )
        self.assertTrue(c["should_append"])
        self.assertEqual(c["output_action"], "append_new_line")

    def test_merge_pending_and_current_does_not_replace_previous(self):
        # `replaces_previous` is what the downstream ledger reads to decide
        # SUPERSEDE vs append -- it is the field that actually destroyed text.
        c = contract("merge_pending_and_current")
        self.assertFalse(c["replaces_previous"])

    def test_merged_pending_is_still_emitted_and_exported(self):
        # Appending must not turn into dropping: the text still has to reach
        # the UI and the export, just as a new line rather than a revision.
        c = contract("merge_pending_and_current")
        self.assertTrue(c["should_emit_to_ui"])
        self.assertTrue(c["should_export"])
        self.assertFalse(c["suppress_current"])


class TestGenuinePreviousLineMergeStillRevises(unittest.TestCase):
    """The other half must not regress -- this one legitimately revises."""

    def test_merge_with_previous_still_revises(self):
        c = contract("merge_with_previous", update_previous=True)
        self.assertTrue(c["should_revise"])
        self.assertTrue(c["replaces_previous"])
        self.assertFalse(c["should_append"])
        self.assertEqual(c["output_action"], "revise_previous_line")

    def test_merge_with_previous_revises_even_without_the_flag(self):
        # The call site passes update_previous=True, but the action name is
        # kept as a second guarantee so a future refactor of that call site
        # cannot silently turn a revision into a duplicate line.
        c = contract("merge_with_previous")
        self.assertTrue(c["should_revise"])

    def test_explicit_update_previous_still_revises_for_any_action(self):
        c = contract("some_other_action", update_previous=True)
        self.assertTrue(c["should_revise"])


class TestUnrelatedContractBranchesUnchanged(unittest.TestCase):
    """Guards that the surrounding decision table did not shift."""

    def test_hold_pending_unchanged(self):
        c = contract("merge_pending_and_current", emit_now=False)
        self.assertEqual(c["output_action"], "hold_pending")
        self.assertFalse(c["should_revise"])
        self.assertFalse(c["should_append"])

    def test_suppression_unchanged(self):
        c = contract("suppress_duplicate_continuation")
        self.assertEqual(c["output_action"], "suppress_current")
        self.assertTrue(c["suppress_current"])
        self.assertFalse(c["should_emit_to_ui"])

    def test_plain_append_unchanged(self):
        c = contract("append_new_line")
        self.assertEqual(c["output_action"], "append_new_line")
        self.assertTrue(c["should_append"])
        self.assertFalse(c["should_revise"])

    def test_punctuation_cleanup_unchanged(self):
        appended = contract("cleanup_punctuation_only")
        self.assertEqual(appended["output_action"], "append_new_line")
        self.assertFalse(appended["should_revise"])

        # With update_previous=True this never reaches the
        # cleanup_punctuation_only branch -- the `update_previous or ...`
        # test above it wins and yields revise_previous_line. That ordering
        # is unchanged by item 19b (it was the same before, since the old
        # condition also began with `update_previous or ...`); asserting it
        # here so a future edit to that branch order is caught.
        # Side note, out of item 19b's scope: this makes the
        # `punctuation_cleanup_revision` output_action unreachable.
        revised = contract("cleanup_punctuation_only", update_previous=True)
        self.assertEqual(revised["output_action"], "revise_previous_line")
        self.assertTrue(revised["should_revise"])


if __name__ == "__main__":
    unittest.main()
