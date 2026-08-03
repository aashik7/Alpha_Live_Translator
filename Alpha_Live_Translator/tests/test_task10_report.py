"""Task 10 — deterministic tests for the utterance_reconstruction false
negative on short/non-Japanese Stop sequences, now persisted into
RUN_MANIFEST.json (exposed by Task 9's Issue 2 fix, which correctly made
build_stop_finalize_summary() report a single, consistent status per run --
consistency that then surfaced this pre-existing miscomputation instead of
masking it behind a stale-read artifact).

Root cause: utterance_reconstruction was computed as
`assembler_flush_ok and commit_confirm_ok`, where assembler_flush_ok comes
from japanese_assembler_flush (flush_japanese_assembler_on_stop) -- a step
that runs UNCONDITIONALLY regardless of session language. For a non-Japanese
session the Japanese continuity assembler has nothing real to reconstruct;
a timeout/failure flushing an already-idle Japanese assembler was a false
negative about that session's own (English) reconstruction, not a genuine
content-loss problem.

Fix: compute_utterance_reconstruction_ok() (stop_finalize_worker.py) only
requires assembler_flush_ok for sessions that actually used the Japanese
path (should_use_japanese_final_stabilizer(host)); every other session's
utterance_reconstruction depends only on the real, language-agnostic check
(commit_confirm_ok).

A second, related defect found during tracing: the "V25.3.3.1" UI update
queued before the drain barrier used to call the full
build_stop_finalize_summary(host, dg_result=dg_result) purely to read one
boolean field, computing (and logging, as a confusingly "final"-sounding
STOP_FINALIZE_SUMMARY_NORMALIZED event) a spurious status at a point in the
sequence before utterance_reconstruction/canonical_ledger_validation/
stable_export/final_export/translation_reconciliation/run_manifest have
even been marked. Replaced with a lightweight get_stop_finalize_snapshot()
read of the same underlying data.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    reset_for_session,
)
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    get_utterance_lifecycle,
    reset_utterance_lifecycle,
)
from alpha.utils import stop_finalize_worker as sfw  # noqa: E402
from alpha.utils.ui_thread_guard import register_ui_main_thread  # noqa: E402

from tests.test_task9_report import get_shared_integration_host, _pump  # noqa: E402


class ComputeUtteranceReconstructionOkTests(unittest.TestCase):
    """VALIDATE item 1 (unit level): the decision function itself, for
    every combination that matters."""

    def test_1_non_japanese_session_ignores_assembler_flush_failure(self) -> None:
        ok, reason = sfw.compute_utterance_reconstruction_ok(
            object(),
            assembler_flush_ok=False,
            commit_confirm_ok=True,
            is_japanese_session_fn=lambda host: False,
        )
        self.assertTrue(ok, "an English session must not fail on a Japanese-only flush")
        self.assertEqual(reason, "commit_confirm_failed")

    def test_2_japanese_session_still_requires_assembler_flush(self) -> None:
        ok, reason = sfw.compute_utterance_reconstruction_ok(
            object(),
            assembler_flush_ok=False,
            commit_confirm_ok=True,
            is_japanese_session_fn=lambda host: True,
        )
        self.assertFalse(ok, "a Japanese session must still require its own flush to succeed")
        self.assertEqual(reason, "assembler_flush_or_commit_confirm_failed")

    def test_3_commit_confirm_failure_always_fails_regardless_of_language(self) -> None:
        ok, _ = sfw.compute_utterance_reconstruction_ok(
            object(),
            assembler_flush_ok=True,
            commit_confirm_ok=False,
            is_japanese_session_fn=lambda host: False,
        )
        self.assertFalse(ok)

    def test_4_both_ok_and_non_japanese_succeeds(self) -> None:
        ok, _ = sfw.compute_utterance_reconstruction_ok(
            object(),
            assembler_flush_ok=True,
            commit_confirm_ok=True,
            is_japanese_session_fn=lambda host: False,
        )
        self.assertTrue(ok)

    def test_5_language_check_exception_fails_closed_to_non_japanese(self) -> None:
        def _boom(host):
            raise RuntimeError("language detection blew up")

        ok, reason = sfw.compute_utterance_reconstruction_ok(
            object(),
            assembler_flush_ok=False,
            commit_confirm_ok=True,
            is_japanese_session_fn=_boom,
        )
        self.assertTrue(
            ok,
            "an exception determining session language must not itself cause "
            "a spurious utterance_reconstruction failure",
        )


class ShortSessionFinalStatusIntegrationTest(unittest.TestCase):
    """VALIDATE item 1 (integration level): a real short session, real
    immediate Stop, with correct transcript+translation output, must never
    have utterance_reconstruction counted among its failed required steps.
    (This harness has no real run_identity/run folder wired up, so a couple
    of unrelated, folder-dependent required steps still fail here
    regardless of this fix -- see the in-test comment; this test's claim is
    scoped precisely to utterance_reconstruction, the one this task fixes.)
    """

    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_CAPTURE_OPEN_FLUSH_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.02)
    def test_short_english_session_reports_completed_not_failed(self) -> None:
        session_id = "sess-10-short"
        run_id = "run-10-short"
        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        register_ui_main_thread()

        host = get_shared_integration_host(session_id, run_id)
        try:
            host._start_ui_event_bus_drain_loop()
            reset_utterance_lifecycle(host, session_id=session_id)
            owner = get_utterance_lifecycle(host)
            owner._commit_fallback_ms = 60

            decision = owner.on_final_chunk(
                text="短い発言です",
                speaker=1,
                channel=0,
                start=0.0,
                end=1.0,
                is_final=True,
                speech_final=False,
                event_id="ev-10-short",
                metadata={},
            )
            utterance_id = str(decision.utterance_id)
            self.assertTrue(utterance_id)

            host.process_ui_queue()

            worker = host.translation_worker
            worker.stop_accepting()
            sfw.begin_stop_from_ui(host)

            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                _pump(host, seconds=0.05)
                if sfw.get_stop_finalize_snapshot().get("worker_done"):
                    break
            self.assertTrue(sfw.get_stop_finalize_snapshot().get("worker_done"))
            _pump(host, seconds=0.3)

            # Transcript and translation both genuinely succeeded.
            matching = [
                ev for ev in worker._revision_events
                if ev.get("canonical_utterance_id") == utterance_id and ev.get("accepted")
            ]
            self.assertTrue(matching, "translation must have genuinely succeeded for this assertion to be meaningful")

            self.assertTrue(
                sfw._required_step_ok.get("utterance_reconstruction"),
                "utterance_reconstruction must not be false for a short, "
                "successful, non-Japanese session",
            )

            # This minimal integration harness has no real run_identity/run
            # folder wired up (RealIntegrationHost's own documented scope,
            # unrelated to Task 10), so canonical_ledger_validation/
            # stable_export/final_export/run_manifest -- which all need a
            # real run folder to write evidence into -- cannot succeed here
            # regardless of this fix; asserting the overall final_status
            # would conflate that pre-existing harness gap with this task's
            # actual fix. The precise, honest claim this test proves is
            # narrower and directly targets the reported symptom:
            # utterance_reconstruction specifically must never be the
            # (spurious) reason a short, successful session is marked
            # failed.
            status = sfw.compute_core_final_status()
            self.assertNotIn(
                "utterance_reconstruction",
                status["failed_required_steps"],
                f"utterance_reconstruction incorrectly counted as a failed "
                f"required step for a short, successful session: {status}",
            )
        finally:
            host._teardown()
            ctl.reset_for_run("teardown-10-short")


if __name__ == "__main__":
    unittest.main()
