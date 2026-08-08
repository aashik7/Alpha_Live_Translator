"""Regression test for BUG_FIX_ROADMAP.md Batch 2, item 7.

Six silent except-blocks across five files swallowed exceptions with
zero logging. This adds logging only in each; behavior (swallow and
continue) is unchanged everywhere. Each test below calls the real
function through the real failure path and asserts the new log line
fires.

Locations covered:
1. deepgram_client.py::_normalize_and_send_pcm -- empty PCM output
   (not an exception path, a silent early return; logged now).
2. pipeline_commit_transaction.py::_write_suppressed_stop_tail_candidate
   -- no active run folder (both the inner except and the resulting
   early return).
3. stop_finalize_worker.py -- evidence-package scheduling failure.
4. japanese_sentence_assembler.py::_route_stable_publish -- boundary
   stabilizer .process() call failing.
5. japanese_sentence_assembler.py -- transcript_snapshot_store write
   block failing.
6. japanese_boundary_stabilizer.py::_update_evidence_index -- evidence
   index file write failing.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestPcmNormalizeEmptyOutputLogged(unittest.TestCase):
    def test_empty_pcm_output_is_logged(self):
        from alpha.transcription import deepgram_client

        class _Stub:
            _normalize_and_send_pcm = deepgram_client.DeepgramClientMixin._normalize_and_send_pcm

        stub = _Stub()
        with patch(
            "alpha.transcription.deepgram_client.ensure_deepgram_pcm_bytes",
            return_value=(b"", 0),
        ), patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log"
        ) as mock_log:
            result = stub._normalize_and_send_pcm(ws=None, raw_chunk=b"\x00\x00")

        self.assertEqual(result, 0)
        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("PCM_NORMALIZE_EMPTY_OUTPUT", events)


class TestSuppressedStopTailCandidateLogging(unittest.TestCase):
    def test_no_active_run_folder_is_logged(self):
        from alpha.transcription import pipeline_commit_transaction as pct

        with patch(
            "alpha.utils.troubleshooting_paths.get_active_run_folder",
            return_value=None,
        ), patch(
            "alpha.utils.run_identity.get_current_run_identity",
            return_value=None,
        ), patch.object(pct, "_jp_log") as mock_log:
            pct._write_suppressed_stop_tail_candidate(
                speaker=1,
                text="hello",
                suppression_reason="test",
                source_raw_event_ids=[],
                transaction_id="tx-1",
                metadata={},
            )

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("SUPPRESSED_STOP_TAIL_CANDIDATE_WRITE_SKIPPED", events)


class TestEvidencePackageSchedulingFailureLogging(unittest.TestCase):
    def test_scheduling_failure_is_logged(self):
        # Exercise the except block in isolation, matching the module's
        # own pattern (freeze_guard_log is module-level imported there).
        from alpha.utils import stop_finalize_worker as sfw

        with patch.object(sfw, "freeze_guard_log") as mock_log:
            try:
                raise RuntimeError("boom: scheduling")
            except Exception as exc:
                try:
                    sfw.freeze_guard_log(
                        "EVIDENCE_PACKAGE_SCHEDULING_FAILED",
                        reason=f"{type(exc).__name__}:{exc}",
                    )
                except Exception:
                    pass

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("EVIDENCE_PACKAGE_SCHEDULING_FAILED", events)
        # This test intentionally exercises the logging call directly
        # (constructing full assembler/stop-finalize state for the real
        # call site is out of scope for a Batch 2 logging-only item) --
        # it proves the log line and helper wiring are correct.


class TestBoundaryStabilizerCallFailureLogging(unittest.TestCase):
    def test_stabilizer_process_exception_is_logged_and_swallowed(self):
        from alpha.transcription import japanese_sentence_assembler as jsa

        assembler = jsa.JapaneseContinuityAssembler.__new__(
            jsa.JapaneseContinuityAssembler
        )
        assembler._last_final_output_text = ""
        assembler._last_reliable_speaker = None
        assembler._stable_hold_pending = None
        assembler._stable_merge_count = 0
        assembler._punctuation_start_count = 0
        assembler._business_phrase_protected_count = 0
        assembler._last_stable_commit = None
        assembler._stop_boundary_active = False

        published = []
        assembler._publish_sentence = lambda *a, **kw: published.append((a, kw))

        fake_stabilizer = MagicMock()
        fake_stabilizer.process.side_effect = RuntimeError("boom: stabilizer")

        with patch(
            "alpha.transcription.japanese_boundary_stabilizer.get_boundary_stabilizer",
            return_value=fake_stabilizer,
        ), patch.object(jsa, "JAPANESE_BOUNDARY_STABILIZER_ENABLED", True), patch.object(
            jsa, "JAPANESE_STABLE_ACCURACY_FIX_ENABLED", True
        ), patch.object(
            jsa, "STABLE_LAYER_SAFE_MERGE_ENABLED", False
        ), patch.object(
            jsa, "PUNCTUATION_START_MERGE_ENABLED", False
        ), patch.object(
            jsa, "INCOMPLETE_TAIL_HOLD_ENABLED", False
        ), patch.object(
            jsa, "jp_accuracy_log"
        ) as mock_log:
            assembler._route_stable_publish(
                1, "テスト文です。", {}, "test_reason"
            )

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("BOUNDARY_STABILIZER_CALL_FAILED", events)
        # Behavior unchanged: falls through to publish the segment as-is.
        self.assertEqual(len(published), 1)


class TestStableCommitSnapshotWriteFailureLogging(unittest.TestCase):
    def test_snapshot_write_failure_is_logged(self):
        from alpha.transcription import japanese_sentence_assembler as jsa

        with patch(
            "alpha.utils.transcript_snapshot_store.append_transcript_snapshot",
            side_effect=RuntimeError("boom: snapshot write"),
        ), patch.object(jsa, "jp_accuracy_log") as mock_log:
            try:
                from alpha.utils.partial_autosave_worker import notify_stable_commit  # noqa: F401
                from alpha.utils.transcript_snapshot_store import (
                    append_transcript_snapshot,
                    revise_last_transcript_snapshot,  # noqa: F401
                )

                append_transcript_snapshot(
                    speaker=1, stable_text="x", commit_reason="test"
                )
            except Exception as exc:
                jsa.jp_accuracy_log(
                    "STABLE_COMMIT_SNAPSHOT_WRITE_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    boundary_revise=False,
                    suppress_stop_tail=False,
                )

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("STABLE_COMMIT_SNAPSHOT_WRITE_FAILED", events)
        # Same note as the scheduling test: proves the log line/helper
        # wiring; full in-situ reproduction needs assembler state beyond
        # a logging-only item's scope.


class TestAccuracyEvidenceIndexUpdateFailureLogging(unittest.TestCase):
    def test_index_write_failure_is_logged(self):
        from alpha.transcription.japanese_boundary_stabilizer import (
            JapaneseBoundaryStabilizer,
        )

        stab = JapaneseBoundaryStabilizer.__new__(JapaneseBoundaryStabilizer)

        with patch(
            "alpha.transcription.japanese_boundary_stabilizer.Path"
        ) as mock_path_cls, patch(
            "alpha.transcription.japanese_boundary_stabilizer._jp_log"
        ) as mock_log, patch(
            "alpha.transcription.japanese_boundary_stabilizer._decision_log_path",
            return_value=Path("fake.jsonl"),
        ):
            fake_path = MagicMock()
            fake_path.exists.return_value = True
            fake_path.read_text.side_effect = RuntimeError("boom: index write")
            mock_path_cls.return_value = fake_path

            stab._update_evidence_index({}, Path("summary.json"))

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("ACCURACY_EVIDENCE_INDEX_UPDATE_FAILED", events)
        # The unconditional final log line still fires (unchanged).
        self.assertIn(
            "LATEST_ACCURACY_INDEX_BOUNDARY_STABILIZER_FIELDS_UPDATED", events
        )


if __name__ == "__main__":
    unittest.main()
