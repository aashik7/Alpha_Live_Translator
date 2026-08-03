"""Task 4C — final QA validation for the Phase 4 finalization/evidence repair.

Deterministic only: no real audio, no live provider calls, no timers.
Reuses IdentityTestHost from test_task1_identity_repair.py (same proven
commit path: host._display_transcript_item -> execute_pipeline_commit) so
canonical records committed here carry real, correct lineage/identity
exactly like the production path, instead of hand-built fixtures that might
not match real record shape.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import alpha.utils.canonical_finalize as cf  # noqa: E402
import alpha.utils.stop_finalize_worker as sfw  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import reset_for_session  # noqa: E402
from alpha.transcription.utterance_lifecycle import reset_utterance_lifecycle  # noqa: E402
from alpha.translation.translation_worker import TranslationWorker  # noqa: E402
from tests.test_task1_identity_repair import IdentityTestHost  # noqa: E402


class FakeDeepLClient:
    def __init__(self) -> None:
        self.available = True

    def translate_text(self, text, source_lang, target_lang):
        return f"[EN] {text}"


class Task4CAcceptanceGateTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.run_folder = Path(self.tmpdir.name)
        sfw._reset_stop_state()
        self.host = IdentityTestHost(session_id="sess-4c", run_id="run-4c")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()
        sfw._reset_stop_state()
        ctl.reset_for_run("teardown-4c")
        reset_for_session("teardown-4c")
        reset_utterance_lifecycle(self.host, session_id="teardown-4c")

    def _commit_record(self, uid, text, *, version=1, raw_ids=None, speaker=1):
        item = {
            "speaker": speaker,
            "text": text,
            "is_final": True,
            "session_id": self.host._live_session_id,
            "channel_index": 0,
            "canonical_utterance_id": uid,
            "provider_utterance_id": f"prov-{uid}-{version}",
            "source_version": version,
            "source_raw_event_ids": raw_ids if raw_ids is not None else [f"raw-{uid}-{version}"],
            "translation_eligible": True,
            "lifecycle_state": "COMMITTED",
            "canonical_decision": "TERMINAL_COMMIT",
        }
        self.host._display_transcript_item(dict(item))

    def _canonical_commits_rows(self):
        path = self.run_folder / "evidence_streams" / "canonical_commits.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ------------------------------------------------------------------
    # 1. Empty-Stable-reconstruction test
    # ------------------------------------------------------------------
    def test_1_empty_reconstruction_never_marked_completed(self) -> None:
        # Genuinely empty session: nothing committed.
        result = cf.finalize_canonical_pipeline(self.host, run_folder=self.run_folder)
        sfw._mark_required_step(
            "canonical_ledger_validation", bool(result.get("ok")), reason=str(result.get("error") or "")
        )
        sfw._mark_required_step(
            "stable_export", bool(result.get("ok")), reason=str(result.get("error") or "")
        )
        for name in sfw._REQUIRED_SYNC_STEPS:
            if name not in ("canonical_ledger_validation", "stable_export"):
                sfw._mark_required_step(name, True)
        status = sfw.compute_core_final_status()
        self.assertNotEqual(
            status["final_status"], "completed",
            f"empty reconstruction must never report completed: {status}",
        )
        self.assertNotEqual(status["final_status"], "completed_pending_evidence_package")

    # ------------------------------------------------------------------
    # 2. Required-exception test
    # ------------------------------------------------------------------
    def test_2_each_required_step_failure_yields_failed_status_with_reason(self) -> None:
        for failing_step in sfw._REQUIRED_SYNC_STEPS:
            with self.subTest(step=failing_step):
                sfw._reset_stop_state()
                for name in sfw._REQUIRED_SYNC_STEPS:
                    sfw._mark_required_step(name, name != failing_step)
                status = sfw.compute_core_final_status()
                self.assertEqual(status["final_status"], "failed")
                self.assertTrue(status["stop_finalize_failed"])
                self.assertEqual(status["failure_reason"], failing_step)

    def test_2b_real_exception_in_canonical_finalize_propagates_to_failure(self) -> None:
        self._commit_record("U-1", "hello world this is a real record")
        with patch(
            "alpha.transcription.canonical_transcript_ledger.freeze_snapshot",
            side_effect=RuntimeError("forced_test_failure"),
        ):
            result = cf.finalize_canonical_pipeline(self.host, run_folder=self.run_folder)
        self.assertFalse(result.get("ok"))
        self.assertIn("forced_test_failure", str(result.get("error", "")))

    # ------------------------------------------------------------------
    # 3. Reconciliation test
    # ------------------------------------------------------------------
    def test_3_raw_canonical_ui_export_counts_reconcile(self) -> None:
        self._commit_record("U-1", "first committed sentence here")
        self._commit_record("U-2", "second committed sentence here")
        self._commit_record("U-3", "third committed sentence here")

        result = cf.finalize_canonical_pipeline(self.host, run_folder=self.run_folder)
        self.assertTrue(result.get("ok"), f"finalize_canonical_pipeline failed: {result}")

        snap = ctl.get_frozen_snapshot()
        self.assertEqual(snap.get("active_record_count"), 3)

        ui_segment_count = self.host.transcript_store.segment_count()
        self.assertEqual(ui_segment_count, 3, "UI store segment count must match canonical count")

        commit_rows = self._canonical_commits_rows()
        self.assertEqual(len(commit_rows), 3, "canonical_commits.jsonl row count must match")

        consistency = ctl.validate_internal_consistency()
        self.assertTrue(consistency.get("ok"), f"consistency check failed: {consistency}")
        self.assertEqual(consistency.get("active_record_count"), 3)

    # ------------------------------------------------------------------
    # 4. Lineage test
    # ------------------------------------------------------------------
    def test_4_every_canonical_record_has_valid_lineage(self) -> None:
        self._commit_record("U-1", "lineage bearing sentence one", raw_ids=["raw-a1", "raw-a2"])
        self._commit_record("U-2", "lineage bearing sentence two", raw_ids=["raw-b1"])

        result = cf.finalize_canonical_pipeline(self.host, run_folder=self.run_folder)
        self.assertTrue(result.get("ok"), f"finalize_canonical_pipeline failed: {result}")

        commit_rows = self._canonical_commits_rows()
        self.assertEqual(len(commit_rows), 2)
        for row in commit_rows:
            self.assertTrue(
                row.get("source_raw_event_ids") or row.get("synthetic_record"),
                f"record missing lineage and not marked synthetic: {row}",
            )

        consistency = ctl.validate_internal_consistency()
        self.assertEqual(consistency.get("stable_records_without_lineage"), 0)

    # ------------------------------------------------------------------
    # 5. Translation-reference test
    # ------------------------------------------------------------------
    def test_5_every_translation_references_existing_canonical_record(self) -> None:
        self._commit_record("U-1", "sentence that will be translated")
        cf.finalize_canonical_pipeline(self.host, run_folder=self.run_folder)
        committed_uids = {row.get("canonical_utterance_id") for row in self._canonical_commits_rows()}
        self.assertIn("U-1", committed_uids)

        worker = TranslationWorker(
            run_id="run-4c", evidence_dir=None, client=FakeDeepLClient(), enabled=True
        )
        accepted = worker.enqueue_stable_segment(
            segment_id=1, source_language="en", source_text="sentence that will be translated",
            canonical_utterance_id="U-1", source_version=1, session_id=self.host._live_session_id,
        )
        self.assertTrue(accepted)

        self.host._run_identity_folder = self.run_folder
        with patch("alpha.utils.run_identity.get_current_run_identity") as mock_ident:
            mock_ident.return_value = type(
                "Ident", (), {"run_id": "run-4c", "run_folder": str(self.run_folder)}
            )()
            sfw._write_translation_and_ui_evidence_streams(
                self.host, translation_summary=None, ui_drain={}, worker=worker,
            )

        jobs_path = self.run_folder / "evidence_streams" / "translation_jobs.jsonl"
        self.assertTrue(jobs_path.exists(), "translation_jobs.jsonl was not written")
        rows = [json.loads(line) for line in jobs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(rows, "translation_jobs.jsonl has no rows")
        referenced_uids = {row.get("canonical_utterance_id") for row in rows}
        self.assertIn("U-1", referenced_uids)
        self.assertTrue(
            referenced_uids.issubset(committed_uids),
            f"translation referenced a canonical_utterance_id not in the committed set: "
            f"{referenced_uids - committed_uids}",
        )

    # ------------------------------------------------------------------
    # 6. Evidence-separation test
    # ------------------------------------------------------------------
    def test_6_no_synthetic_event_in_provider_events(self) -> None:
        from alpha.utils.accuracy_stage_capture import get_accuracy_stage_compare_path

        raw_path = get_accuracy_stage_compare_path("raw_provider_events", self.run_folder)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        real_event = {
            "raw_event_id": "raw-real-1",
            "raw_text": "genuine deepgram final",
            "metadata": {},
        }
        synthetic_event = {
            "raw_event_id": "raw-synth-1",
            "raw_text": "assembler reconstructed text",
            "metadata": {"synthetic_record": True},
        }
        with open(raw_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(real_event) + "\n")
            fh.write(json.dumps(synthetic_event) + "\n")

        snap = {"records": []}
        cf.write_separated_evidence_streams(self.run_folder, snap)

        out_path = self.run_folder / "evidence_streams" / "provider_events.jsonl"
        self.assertTrue(out_path.exists())
        rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = [row.get("raw_event_id") for row in rows]
        self.assertIn("raw-real-1", ids)
        self.assertNotIn("raw-synth-1", ids, "synthetic event leaked into provider_events.jsonl")


if __name__ == "__main__":
    unittest.main()
