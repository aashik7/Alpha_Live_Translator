"""Regression tests for BUG_FIX_ROADMAP.md Batch 4, item 21.

Confirmed defect: `_confirm_transcript_commits` measured three counts
(`transcript_queue_remaining`, `transcript_batch_remaining`,
`language_pipeline_pending_task_count`) and only **logged** them, never
comparing any to zero. It returned None. Its caller did:

    commit_confirm_ok = run_timed_step(
        host, "transcript_commit_confirm", lambda: _confirm_transcript_commits(host)
    )

`run_timed_step` returns whether the step finished without raising or
timing out -- not the function's verdict -- and this function never
raised. So `commit_confirm_ok` was effectively a constant `True`, and
`compute_utterance_reconstruction_ok` gated a run's reported status on
it. A Stop that finished with transcript items still queued reported
`completed`.

Measured against real evidence before changing the gate: 279
`STOP_TRANSCRIPT_COMMITS_CONFIRMED` events across all captured runs, of
which **2** had `transcript_queue_remaining: 1` -- two runs that already
reported success while genuinely undrained. The other 277 were fully
zero, so tightening the gate fixes those two without breaking the rest.

One non-obvious detail this pins: `_safe_qsize` returns **-1**, not 0,
when the queue is absent or `qsize()` raises. A naive `== 0` check would
read "unmeasurable" as "not drained" by accident, or (worse, if written
as `<= 0`) as "drained". The fix treats unmeasurable as explicitly
not-confirmed with its own reason string -- "we could not check" must
never be reported as "we checked and it was empty".
"""

import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.stop_finalize_worker import (  # noqa: E402
    _confirm_transcript_commits,
    compute_utterance_reconstruction_ok,
)


class _Host:
    def __init__(self, *, queued=0, batch=0, with_queue=True):
        if with_queue:
            q = queue.Queue()
            for i in range(queued):
                q.put({"text": f"pending-{i}"})
            self.transcript_queue = q
        self._transcript_ui_batch_buffer = [{"text": "b"} for _ in range(batch)]


class TestConfirmTranscriptCommitsReturnsARealVerdict(unittest.TestCase):
    def test_returns_a_verdict_instead_of_none(self):
        result = _confirm_transcript_commits(_Host())
        self.assertIsInstance(
            result,
            dict,
            "the check must return its verdict, not None -- returning None "
            "is what forced the caller to fall back to run_timed_step's "
            "'did not crash' boolean",
        )
        self.assertIn("ok", result)

    def test_fully_drained_passes(self):
        result = _confirm_transcript_commits(_Host())
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "")

    def test_undrained_transcript_queue_fails(self):
        # The exact shape seen twice in real evidence.
        result = _confirm_transcript_commits(_Host(queued=1))
        self.assertFalse(
            result["ok"],
            "a Stop with transcript items still queued must not confirm",
        )
        self.assertEqual(result["reason"], "transcript_queue_not_drained")
        self.assertEqual(result["transcript_queue_remaining"], 1)

    def test_undrained_ui_batch_buffer_fails(self):
        result = _confirm_transcript_commits(_Host(batch=2))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "transcript_ui_batch_not_drained")

    def test_unmeasurable_queue_is_not_reported_as_drained(self):
        # _safe_qsize returns -1 here, not 0.
        result = _confirm_transcript_commits(_Host(with_queue=False))
        self.assertEqual(result["transcript_queue_remaining"], -1)
        self.assertFalse(
            result["ok"],
            "'could not measure' must never be reported as 'measured empty'",
        )
        self.assertEqual(result["reason"], "transcript_queue_unmeasurable")

    def test_pending_language_pipeline_timers_do_not_fail_the_check(self):
        # pending_task_count counts LanguagePipelineWorker's scheduled
        # future flush/quarantine TIMERS, not queued transcript commits. A
        # timer scheduled a few hundred ms out that Stop's own flush has
        # already made moot is normal; gating on it would manufacture false
        # failures. It is still logged as evidence.
        class _Worker:
            def pending_task_count(self):
                return 3

        with patch(
            "alpha.utils.language_pipeline_worker.get_language_pipeline_worker",
            return_value=_Worker(),
        ):
            result = _confirm_transcript_commits(_Host())

        self.assertTrue(result["ok"])
        self.assertEqual(result["language_pipeline_pending_task_count"], 3)
        self.assertTrue(result["language_pipeline_measured"])

    def test_unreadable_language_pipeline_is_recorded_not_swallowed(self):
        # A failed probe must not silently become a 0 that reads as
        # "measured, and it was empty".
        with patch(
            "alpha.utils.language_pipeline_worker.get_language_pipeline_worker",
            side_effect=RuntimeError("module unavailable"),
        ):
            result = _confirm_transcript_commits(_Host())

        self.assertTrue(result["ok"])
        self.assertFalse(result["language_pipeline_measured"])


class TestVerdictActuallyGatesTheReportedStatus(unittest.TestCase):
    """The consumer side -- proves the verdict is not merely returned but
    reaches the status computation, which is the defect's real impact."""

    def test_failed_confirm_makes_utterance_reconstruction_fail(self):
        ok, reason = compute_utterance_reconstruction_ok(
            object(),
            assembler_flush_ok=True,
            commit_confirm_ok=False,
            is_japanese_session_fn=lambda _host: False,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "commit_confirm_failed")

    def test_passed_confirm_still_allows_success(self):
        ok, _reason = compute_utterance_reconstruction_ok(
            object(),
            assembler_flush_ok=True,
            commit_confirm_ok=True,
            is_japanese_session_fn=lambda _host: False,
        )
        self.assertTrue(ok)

    def test_undrained_queue_flows_through_to_a_failed_status(self):
        # End-to-end of the two halves this item joins: a real undrained
        # host must produce a False utterance_reconstruction, which is what
        # the old code could never do.
        verdict = _confirm_transcript_commits(_Host(queued=1))
        ok, _reason = compute_utterance_reconstruction_ok(
            object(),
            assembler_flush_ok=True,
            commit_confirm_ok=verdict["ok"],
            is_japanese_session_fn=lambda _host: False,
        )
        self.assertFalse(
            ok,
            "an undrained transcript queue must make the run report a "
            "failed utterance_reconstruction, not completed",
        )


if __name__ == "__main__":
    unittest.main()
