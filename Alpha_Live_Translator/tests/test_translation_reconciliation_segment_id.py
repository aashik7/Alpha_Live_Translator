"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, item 9c.

Confirmed defect (live runs ...155334 ja and ...155842 en, both ended
final_status="failed"): reconcile_translation_gaps force-resubmitted a
genuinely-untranslated utterance using
`segment_id=rec["sequence_number"]` -- the canonical LEDGER's own record
counter -- while the translation worker's segment_id space is the
separate `main_window._translation_segment_seq` counter. Both start at 1
and increment per item, so they collide: in run ...155842 the
untranslated utterance `U-15` was ledger record #14, and segment ids
1..14 were already used by unrelated translation jobs, so the worker
rejected the resubmit as a DUPLICATE. `forced_count` stayed 0,
`unresolved` stayed 1, and the whole self-healing safety net was inert
while `U-15`'s translation stayed genuinely missing from the output.

Two behaviors are pinned here:
1. The forced submit must use an id from the host's translation counter,
   never the ledger's `sequence_number`, so it cannot collide.
2. A rejection that really does mean "already delivered" (duplicate /
   obsolete-superseded) must count as resolved, not as an unresolved gap
   -- `enqueue_stable_segment` returns a bare False for every cause, so
   the reconciler distinguishes them via the worker's public counters.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.stop_finalize_worker import (  # noqa: E402
    TranslationReconciliationError,
    reconcile_translation_gaps,
)


class _Worker:
    """Translation worker stub mimicking the real segment_id dedup."""

    def __init__(self, used_segment_ids=(), reject_reason=None):
        self._seen_request_ids = set(used_segment_ids)
        self._revision_events = []
        self._reject_reason = reject_reason
        self._counters = {}
        self.calls = []

    def get_counters(self):
        return dict(self._counters)

    def enqueue_stable_segment(self, **kwargs):
        self.calls.append(kwargs)
        sid = int(kwargs.get("segment_id") or 0)
        if self._reject_reason:
            self._counters[self._reject_reason] = (
                int(self._counters.get(self._reject_reason, 0) or 0) + 1
            )
            return False
        if sid in self._seen_request_ids:
            # Real worker's first dedup branch.
            self._counters["DUPLICATE_SUBMISSIONS_REJECTED"] = (
                int(self._counters.get("DUPLICATE_SUBMISSIONS_REJECTED", 0) or 0) + 1
            )
            return False
        self._seen_request_ids.add(sid)
        return True


class _Host:
    def __init__(self, worker, translation_seq=14):
        self.translation_worker = worker
        self._translation_segment_seq = translation_seq
        self._listen_language = "en"


def _record(utterance_id, sequence_number, text="hello there"):
    return {
        "record_id": f"canon-{sequence_number:06d}",
        "sequence_number": sequence_number,
        "final_text": text,
        "created_at": 1.0,
        "commit_reason": "speech_final",
        "metadata": {
            "canonical_utterance_id": utterance_id,
            "translation_eligible": True,
            "source_version": 11,
            "session_id": "sess-1",
        },
    }


class TestForcedSubmitDoesNotUseLedgerSequence(unittest.TestCase):
    def test_ledger_sequence_collision_no_longer_rejects_a_real_gap(self):
        # Reproduces run ...155842 exactly: U-15 is ledger record #14 and
        # has no translation, while segment ids 1..14 are already used.
        worker = _Worker(used_segment_ids=range(1, 15))
        host = _Host(worker, translation_seq=14)

        with patch(
            "alpha.transcription.canonical_transcript_ledger.get_active_records",
            return_value=[_record("U-15", 14)],
        ):
            result = reconcile_translation_gaps(host)

        # Reaching here at all proves nothing was left unresolved --
        # reconcile_translation_gaps raises instead of returning when it
        # cannot deliver a gap.
        self.assertEqual(result["gap_count"], 1)
        self.assertEqual(
            result["forced_count"],
            1,
            "the genuine gap must now be healed instead of colliding",
        )
        # The submitted id must come from the host counter (15), never the
        # ledger's sequence_number (14).
        self.assertEqual(worker.calls[0]["segment_id"], 15)
        self.assertNotEqual(worker.calls[0]["segment_id"], 14)
        self.assertEqual(host._translation_segment_seq, 15)

    def test_multiple_gaps_get_distinct_ids(self):
        worker = _Worker(used_segment_ids=range(1, 15))
        host = _Host(worker, translation_seq=14)

        with patch(
            "alpha.transcription.canonical_transcript_ledger.get_active_records",
            return_value=[_record("U-15", 14), _record("U-16", 15, "second line")],
        ):
            result = reconcile_translation_gaps(host)

        ids = [c["segment_id"] for c in worker.calls]
        self.assertEqual(len(set(ids)), 2, "each forced submit needs its own id")
        self.assertEqual(result["forced_count"], 2)


class TestRejectionReasonsAreDistinguished(unittest.TestCase):
    def test_genuine_duplicate_counts_as_resolved(self):
        # A real "already delivered" rejection must not fail the run.
        worker = _Worker(reject_reason="DUPLICATE_SUBMISSIONS_REJECTED")
        host = _Host(worker)

        with patch(
            "alpha.transcription.canonical_transcript_ledger.get_active_records",
            return_value=[_record("U-15", 14)],
        ):
            result = reconcile_translation_gaps(host)

        # Returning (not raising) is itself the assertion that this was
        # treated as resolved.
        self.assertEqual(result["forced_count"], 1)

    def test_worker_shut_down_is_still_a_real_failure(self):
        # The opposite case must still raise -- this is a genuine gap.
        worker = _Worker(reject_reason="QUOTA_OR_DISABLED_SUBMISSIONS_REJECTED")
        host = _Host(worker)

        with patch(
            "alpha.transcription.canonical_transcript_ledger.get_active_records",
            return_value=[_record("U-15", 14)],
        ):
            with self.assertRaises(TranslationReconciliationError):
                reconcile_translation_gaps(host)


if __name__ == "__main__":
    unittest.main()
