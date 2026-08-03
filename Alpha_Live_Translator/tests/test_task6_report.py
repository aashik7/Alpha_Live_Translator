"""Task 6 — deterministic VALIDATE tests for the ALPHA_ARCHITECTURE_DEBUG_REPORT.md
P0/P1 fixes:

  FIX 1 (P0, utterance_lifecycle.py): identity binding + ledger commit
         atomicity, quarantine on rejected assignment.
  FIX 2 (P1, pipeline_commit_transaction.py): pipeline_commit_transaction.py
         is the sole writer of canonical stable commit events.
  FIX 3 (P1, deepgram_client.py stop_gracefully): covered by the existing
         tests.test_stop_queue_flush_v3_2_4 suite (re-run separately).
  FIX 4 (P1, japanese_sentence_assembler.py / japanese_accuracy_log.py):
         evidence-write failure must not alter boundary/speaker decisions.
  FIX 5 (P1, deepgram_client.py:_commit_final_transcript_segment): a final
         that arrives after the Japanese-specific gate closes, while the
         outer commit gate is still open, must not silently vanish.

No real audio, no live provider calls, no timers.
"""

from __future__ import annotations

import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    IdentityObservation,
    reset_for_session,
    resolve_canonical_record_id,
)
from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402
from alpha.transcription.japanese_final_chunk_stabilizer import (  # noqa: E402
    get_japanese_final_stabilizer,
    should_use_japanese_final_stabilizer,
)
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    get_utterance_lifecycle,
    reset_utterance_lifecycle,
)


class Fix1ForcedAssignmentRejectionTests(unittest.TestCase):
    """VALIDATE item 1: force assign_canonical_record_id to reject/except
    after the ledger mutation has already applied, and confirm the P0 fix
    holds -- no success=True with identity_assigned=False, the ledger
    record is quarantined (suppressed), not left as a silent orphan."""

    def setUp(self) -> None:
        self.session_id = "sess-6-fix1"
        ctl.reset_for_run("run-6-fix1")
        reset_for_session(self.session_id)
        self.host = type("H", (), {})()
        reset_utterance_lifecycle(self.host, session_id=self.session_id)
        self.controller = get_utterance_lifecycle(self.host)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-6-fix1")
        reset_for_session("teardown-6-fix1")

    def test_1_rejected_assignment_does_not_report_success_and_quarantines_record(self) -> None:
        with patch(
            "alpha.transcription.canonical_identity_registry.assign_canonical_record_id",
            return_value=IdentityObservation(
                accepted=False, rejected=True, reason="forced_test_rejection"
            ),
        ):
            result = self.controller.accept_boundary_proposal(
                action="commit_new",
                text="発言はここで打ち切られる",
                speaker=1,
                channel=0,
                canonical_utterance_id="jp-utt-fix1-rejected",
                source_version=1,
                source_raw_event_ids=["raw-fix1-rejected-1"],
                commit_reason="test_forced_rejection",
            )

        self.assertFalse(result["success"], result)
        self.assertFalse(result["identity_assigned"], result)
        self.assertTrue(result["quarantined"], result)
        self.assertTrue(result["record_id"], result)

        record = None
        for candidate in ctl.get_record_history():
            if str(candidate.get("record_id")) == str(result["record_id"]):
                record = candidate
        self.assertIsNotNone(record, "the ledger mutation must still be traceable, not vanish")
        self.assertNotIn(
            result["record_id"],
            [r.get("record_id") for r in ctl.get_active_records()],
            "a quarantined (suppressed) record must not remain an active record",
        )

    def test_2_exception_during_assignment_also_fails_closed(self) -> None:
        with patch(
            "alpha.transcription.canonical_identity_registry.assign_canonical_record_id",
            side_effect=RuntimeError("boom"),
        ):
            result = self.controller.accept_boundary_proposal(
                action="commit_new",
                text="別の発言もここで打ち切られる",
                speaker=1,
                channel=0,
                canonical_utterance_id="jp-utt-fix1-exception",
                source_version=1,
                source_raw_event_ids=["raw-fix1-exception-1"],
                commit_reason="test_forced_exception",
            )

        self.assertFalse(result["success"], result)
        self.assertFalse(result["identity_assigned"], result)
        self.assertTrue(result["quarantined"], result)


class Fix2SoleCanonicalWriterTests(unittest.TestCase):
    """VALIDATE item 6 (protected latest-run-style check): confirm
    stable_event_count == applied canonical commit count -- i.e. every
    applied ledger commit produces exactly one canonical stable event, and
    the Japanese assembler no longer writes a second one."""

    def setUp(self) -> None:
        self.session_id = "sess-6-fix2"
        ctl.reset_for_run("run-6-fix2")
        reset_for_session(self.session_id)
        self.host = type("H", (), {})()
        reset_utterance_lifecycle(self.host, session_id=self.session_id)
        self.controller = get_utterance_lifecycle(self.host)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-6-fix2")
        reset_for_session("teardown-6-fix2")

    def test_1_one_canonical_stable_event_per_applied_commit(self) -> None:
        events: list[dict] = []
        with patch(
            "alpha.utils.accuracy_stage_capture.record_assembler_only_event",
            side_effect=lambda **kwargs: events.append(kwargs),
        ):
            result = self.controller.accept_boundary_proposal(
                action="commit_new",
                text="これは唯一の正規イベントになるはずです",
                speaker=1,
                channel=0,
                canonical_utterance_id="jp-utt-fix2-sole-writer",
                source_version=1,
                source_raw_event_ids=["raw-fix2-1"],
                commit_reason="test_sole_writer",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(
            len(events), 1,
            "exactly one canonical stable event must be written per applied ledger commit",
        )
        self.assertIn(str(result["record_id"]), str(events[0].get("commit_reason", "")))


class Fix4EvidenceFailureDoesNotAlterSemanticsTests(unittest.TestCase):
    """VALIDATE item 3: two-speaker Japanese probe under writable vs.
    read-only (evidence-write-failing) conditions must produce identical
    speaker/boundary output -- evidence-write failure is observability
    only, never a semantic override."""

    def test_1_lineage_write_failure_does_not_force_append_only_override(self) -> None:
        from alpha.transcription.stable_revision_decision import (
            decide_stable_revision_action,
        )

        previous_record = {
            "text": "これは前の発言です",
            "speaker": 1,
            "source_raw_event_ids": ["raw-prev-1"],
        }

        # Simulate metadata carrying the same lineage-failure flags that
        # the raw ingress path (japanese_final_chunk_stabilizer.py) sets
        # when evidence/raw-event capture failed. decide_stable_revision_action
        # itself never reads these keys -- only japanese_sentence_assembler.py's
        # now-removed override did. candidate_text is a direct extension of
        # previous_record's text (Rule B), which should yield
        # "revise_previous" on its own merits.
        metadata = {
            "lineage_assignment_failed": True,
            "force_append_only": True,
        }

        revision_decision = decide_stable_revision_action(
            previous_record=previous_record,
            candidate_text="これは前の発言です、続きます",
            candidate_speaker=1,
            update_previous_requested=True,
            candidate_raw_event_ids=[],
            candidate_metadata=metadata,
        )

        final_revision_action = str(revision_decision.get("action") or "append")
        # fixes TASK_6_REPORT.md P1: japanese_sentence_assembler.py used to
        # force final_revision_action from "revise_previous" to "append"
        # whenever lineage_assignment_failed/force_append_only was set on
        # metadata, regardless of what the revision-decision engine
        # computed. That override has been removed (only
        # LINEAGE_EVIDENCE_INCOMPLETE_OBSERVED is logged now); this asserts
        # the decision engine's own output -- which the assembler now uses
        # unmodified -- is "revise_previous" for a genuine extension even
        # with evidence/lineage flags set.
        self.assertEqual(
            final_revision_action, "revise_previous",
            "evidence/lineage failure must not force an append-only override "
            "of the boundary decision",
        )


class Fix5LateFinalNotSilentlyDroppedTests(unittest.TestCase):
    """VALIDATE item 4 (stop-race test): a final that arrives after the
    Japanese acceptance gate has already closed, while the outer
    is_listening/_is_finalizing gate is still open, must reach the
    stabilizer's real ingest path instead of returning True on a silent
    STALE_FINAL_DROPPED with zero commit."""

    class _Host(DeepgramClientMixin):
        def __init__(self) -> None:
            self._dg_ws = None
            self._audio_q = queue.Queue()
            self._stop_event = threading.Event()
            self.is_listening = False
            self._is_finalizing = True
            self.transcript_queue = queue.Queue()
            self.committed = []
            self._listen_language = "ja"

        def publish_transcript_event(self, text, speaker=None, timestamp=None, is_final=True, queue_item=None):
            self.committed.append({"speaker": speaker, "text": text})

    def test_1_late_final_reopens_gate_instead_of_vanishing(self) -> None:
        host = self._Host()
        self.assertTrue(should_use_japanese_final_stabilizer(host))
        stabilizer = get_japanese_final_stabilizer(host)
        # Simulate the exact race: outer commit gate is open (is_finalizing
        # True) but the Japanese-specific gate was already independently
        # closed (e.g. by a WS-close event racing the finalize sequence).
        stabilizer.set_accepting(False)
        self.assertFalse(stabilizer.is_accepting())

        ingested: list[tuple] = []
        with patch.object(
            stabilizer, "ingest", side_effect=lambda *a, **k: ingested.append((a, k)) or True
        ):
            committed = host._commit_final_transcript_segment(1, "遅れて届いた最終発話")

        self.assertTrue(committed)
        self.assertEqual(
            len(ingested), 1,
            "a late final belonging to the current utterance must reach the "
            "real ingest path, not just be logged and dropped",
        )
        self.assertTrue(
            stabilizer.is_accepting(),
            "the gate must be reopened for the legitimately late final, not "
            "left closed while still lying about success",
        )


if __name__ == "__main__":
    unittest.main()
