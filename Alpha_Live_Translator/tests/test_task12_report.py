"""Task 12 — exhaustive audit of every _mark_required_step call site in
stop_finalize_worker.py for the silent-cascade failure class Tasks 8, 9,
and 10 each hit one instance of.

Confirmed defect (two call sites, both fixed here):

1. The utterance_reconstruction block used to call
   compute_utterance_reconstruction_ok(...) directly -- the only
   required-step computation in the whole sequence with NO containment at
   all (not inside run_timed_step, no try/except at the call site). Any
   exception there would propagate out of _run_finalize_worker entirely,
   skipping every subsequent _mark_required_step call for the rest of the
   function: exactly the reported "utterance_reconstruction onward, all
   failing, with empty failed_steps/timed_out_steps" pattern. Fixed by
   wrapping the computation in run_timed_step, same as every other block.

2. _write_minimal_runtime_artifacts(host, dg_result=dg_result) -- which
   marks "run_manifest" as its own last line -- was called completely
   unwrapped. Any exception anywhere in its body (before reaching that
   last line) would propagate the same way. Fixed the same way, with an
   explicit fallback mirroring the established
   canonical_ledger_validation/stable_export and final_export pattern.

Every other _mark_required_step block was audited and confirmed already
safe (either wrapped in run_timed_step with an explicit fallback, or
computed from plain dict/bool operations that cannot raise) -- see
TASK_12_REPORT.md for the full table.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
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


class _FakeIdentity:
    """Minimal real-run-folder stand-in (a genuine temp directory) so the
    folder-dependent required steps (canonical_ledger_validation,
    stable_export, final_export, run_manifest) can genuinely succeed in
    this harness instead of always failing regardless of this task's fix
    (the gap Task 10's own tests explicitly called out and scoped around).
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.run_folder = tempfile.mkdtemp(prefix="alpha_task12_")
        self.run_timestamp = "ts"
        self.app_version = "test"
        self.stop_ui_callback_duration_ms = 0.0
        self.stop_finalize_completed = False
        self.deepgram_close_status = ""


def _run_real_stop_and_wait(host, *, timeout_seconds: float = 20.0) -> None:
    host.translation_worker.stop_accepting()
    sfw.begin_stop_from_ui(host)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _pump(host, seconds=0.05)
        if sfw.get_stop_finalize_snapshot().get("worker_done"):
            break
    _pump(host, seconds=0.3)


def _patch_dg_timing():
    return (
        patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.05),
        patch("alpha.transcription.deepgram_client.STOP_CAPTURE_OPEN_FLUSH_MAX_S", 0.05),
        patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.02),
    )


class UtteranceReconstructionCascadeFixTests(unittest.TestCase):
    """VALIDATE item 1 for the utterance_reconstruction block: force
    compute_utterance_reconstruction_ok to raise during a REAL Stop
    sequence and confirm (a) the exception is logged, (b)
    utterance_reconstruction is marked False with a clear reason, (c)
    every OTHER required step is still correctly marked based on its own
    real condition -- not skipped.

    fixes TASK_12_REPORT.md test-stability note: runs in its own
    subprocess (see _run_as_subprocess_entry) for the same reason the
    3-scenario test does -- a shared in-process host running a second real
    Stop sequence hits the separate, pre-existing, out-of-scope
    final_export cross-session gap flagged in the report, which would
    otherwise falsely appear as a second "cascaded" failure here.
    """

    def _run_body(self) -> dict:
        session_id = "sess-12-ur-exc"
        run_id = "run-12-ur-exc"
        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        register_ui_main_thread()
        fake_ident = _FakeIdentity(run_id)

        freeze_guard_calls: list[tuple] = []

        def _capture_freeze_guard_log(event, **data):
            freeze_guard_calls.append((event, data))

        p1, p2, p3 = _patch_dg_timing()
        with p1, p2, p3, patch(
            "alpha.utils.run_identity.get_current_run_identity",
            return_value=fake_ident,
        ), patch(
            "alpha.utils.stop_finalize_worker.compute_utterance_reconstruction_ok",
            side_effect=RuntimeError("simulated condition-computation crash"),
        ), patch(
            "alpha.utils.stop_finalize_worker.freeze_guard_log",
            side_effect=_capture_freeze_guard_log,
        ):
            host = get_shared_integration_host(session_id, run_id)
            host._listen_language = "en"
            host._start_ui_event_bus_drain_loop()
            reset_utterance_lifecycle(host, session_id=session_id)
            owner = get_utterance_lifecycle(host)
            decision = owner.accept_boundary_proposal(
                action="commit_new",
                text="この発言はエラーの後でも記録されるはずです",
                speaker=1,
                channel=0,
                canonical_utterance_id="utt-12-ur-exc",
                source_version=1,
                source_raw_event_ids=["raw-12-ur-exc-1"],
                commit_reason="test_commit",
                translation_eligible=True,
            )
            self.assertTrue(decision.get("success"), decision)
            host.translation_worker.enqueue_stable_segment(
                segment_id=1,
                source_language="en",
                source_text="text",
                canonical_utterance_id="utt-12-ur-exc",
                source_version=1,
                source_record_id=str(decision.get("record_id") or ""),
                session_id=session_id,
            )

            _run_real_stop_and_wait(host)

            host._teardown()

        ctl.reset_for_run("teardown-12-ur-exc")
        failure_events = [
            data for event, data in freeze_guard_calls
            if event == "STOP_FINALIZE_STEP_FAILED"
            and data.get("step_name") == "utterance_reconstruction_check"
        ]
        return {
            "decision_success": bool(decision.get("success")),
            "failure_events": failure_events,
            "required_step_ok": dict(sfw._required_step_ok),
            "status": sfw.compute_core_final_status(),
        }

    def test_exception_marks_only_utterance_reconstruction_false_others_unaffected(
        self,
    ) -> None:
        result = _run_case_in_subprocess("UtteranceReconstructionCascadeFixTests")
        self.assertTrue(result["decision_success"], result)

        failure_events = result["failure_events"]
        self.assertTrue(
            failure_events,
            f"expected a logged STOP_FINALIZE_STEP_FAILED for "
            f"utterance_reconstruction_check; result: {result}",
        )
        failure_data = failure_events[0]
        self.assertEqual(failure_data.get("exception_type"), "RuntimeError")
        self.assertIn(
            "simulated condition-computation crash",
            str(failure_data.get("exception_message", "")),
        )

        required_step_ok = result["required_step_ok"]
        self.assertFalse(required_step_ok.get("utterance_reconstruction"))
        status = result["status"]
        self.assertIn("utterance_reconstruction", status["failed_required_steps"])
        self.assertEqual(
            len(status["failed_required_steps"]), 1,
            f"the exception must not cascade to any other required step: {status}",
        )
        for other_step in (
            "canonical_ledger_validation",
            "stable_export",
            "final_export",
            "translation_reconciliation",
            "translation_drain",
            "loading_state_drain",
            "run_manifest",
        ):
            self.assertTrue(
                required_step_ok.get(other_step),
                f"{other_step} must still be correctly marked based on its "
                f"own real condition, not skipped by the earlier exception",
            )


class RunManifestCascadeFixTests(unittest.TestCase):
    """VALIDATE item 1 for the run_manifest block: force
    _write_minimal_runtime_artifacts to raise during a REAL Stop sequence
    and confirm (a) the exception is logged, (b) run_manifest is marked
    False with a clear reason, (c) every step that runs BEFORE it in the
    sequence is still correctly marked (proving this block's failure
    cannot retroactively poison earlier markings, and that the sequence
    still reaches its natural end instead of aborting into the outer
    exception handler).

    fixes TASK_12_REPORT.md test-stability note: runs in its own
    subprocess -- see UtteranceReconstructionCascadeFixTests for why.
    """

    def _run_body(self) -> dict:
        session_id = "sess-12-rm-exc"
        run_id = "run-12-rm-exc"
        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        register_ui_main_thread()
        fake_ident = _FakeIdentity(run_id)

        freeze_guard_calls: list[tuple] = []

        def _capture_freeze_guard_log(event, **data):
            freeze_guard_calls.append((event, data))

        p1, p2, p3 = _patch_dg_timing()
        with p1, p2, p3, patch(
            "alpha.utils.run_identity.get_current_run_identity",
            return_value=fake_ident,
        ), patch(
            "alpha.utils.stop_finalize_worker._write_minimal_runtime_artifacts",
            side_effect=RuntimeError("simulated artifact-write crash"),
        ), patch(
            "alpha.utils.stop_finalize_worker.freeze_guard_log",
            side_effect=_capture_freeze_guard_log,
        ):
            host = get_shared_integration_host(session_id, run_id)
            host._listen_language = "en"
            host._start_ui_event_bus_drain_loop()
            reset_utterance_lifecycle(host, session_id=session_id)
            owner = get_utterance_lifecycle(host)
            decision = owner.accept_boundary_proposal(
                action="commit_new",
                text="この発言はrun_manifestのエラーの前に記録されます",
                speaker=1,
                channel=0,
                canonical_utterance_id="utt-12-rm-exc",
                source_version=1,
                source_raw_event_ids=["raw-12-rm-exc-1"],
                commit_reason="test_commit",
                translation_eligible=True,
            )
            self.assertTrue(decision.get("success"), decision)
            host.translation_worker.enqueue_stable_segment(
                segment_id=1,
                source_language="en",
                source_text="text",
                canonical_utterance_id="utt-12-rm-exc",
                source_version=1,
                source_record_id=str(decision.get("record_id") or ""),
                session_id=session_id,
            )

            _run_real_stop_and_wait(host)

            host._teardown()

        ctl.reset_for_run("teardown-12-rm-exc")
        failure_events = [
            data for event, data in freeze_guard_calls
            if event == "STOP_FINALIZE_STEP_FAILED"
            and data.get("step_name") == "write_minimal_runtime_artifacts"
        ]
        return {
            "decision_success": bool(decision.get("success")),
            "failure_events": failure_events,
            "required_step_ok": dict(sfw._required_step_ok),
            "status": sfw.compute_core_final_status(),
            "worker_done": bool(sfw.get_stop_finalize_snapshot().get("worker_done")),
        }

    def test_exception_marks_only_run_manifest_false_earlier_steps_unaffected(
        self,
    ) -> None:
        result = _run_case_in_subprocess("RunManifestCascadeFixTests")
        self.assertTrue(result["decision_success"], result)

        failure_events = result["failure_events"]
        self.assertTrue(
            failure_events,
            f"expected a logged STOP_FINALIZE_STEP_FAILED for "
            f"write_minimal_runtime_artifacts; result: {result}",
        )
        failure_data = failure_events[0]
        self.assertEqual(failure_data.get("exception_type"), "RuntimeError")
        self.assertIn(
            "simulated artifact-write crash",
            str(failure_data.get("exception_message", "")),
        )

        required_step_ok = result["required_step_ok"]
        self.assertFalse(required_step_ok.get("run_manifest"))
        status = result["status"]
        self.assertIn("run_manifest", status["failed_required_steps"])
        self.assertEqual(
            len(status["failed_required_steps"]), 1,
            f"the exception must not cascade to any other required step: {status}",
        )
        for earlier_step in (
            "audio_summary",
            "raw_event_persistence",
            "utterance_reconstruction",
            "canonical_ledger_validation",
            "stable_export",
            "final_export",
            "translation_reconciliation",
            "translation_drain",
            "loading_state_drain",
        ):
            self.assertTrue(
                required_step_ok.get(earlier_step),
                f"{earlier_step} (which runs before the failing step) must "
                f"still be correctly marked, not retroactively invalidated",
            )
        # And the sequence reached its natural end -- did not abort into
        # the outer exception handler (which would leave finalize_completed
        # False and never populate the run-id-scoped cache).
        self.assertTrue(result["worker_done"])


class RunTimedStepContainmentGuaranteeTests(unittest.TestCase):
    """Foundational property every OTHER already-safe block in this file
    relies on (audio_summary, raw_event_persistence,
    translation_reconciliation, translation_drain/loading_state_drain,
    canonical_ledger_validation/stable_export, final_export): a function
    that raises inside run_timed_step can never propagate past it."""

    def setUp(self) -> None:
        sfw._reset_stop_state()

    def test_exception_inside_run_timed_step_never_propagates(self) -> None:
        def _boom() -> None:
            raise RuntimeError("boom")

        host = type("H", (), {})()
        ok = sfw.run_timed_step(host, "some_step", _boom)
        self.assertFalse(ok)
        # Proves control genuinely returned to the caller -- this line
        # executing at all is the assertion.
        self.assertIn("some_step", sfw._stop_state["failed_steps"])

    def test_caller_can_still_mark_the_step_false_after_run_timed_step_fails(self) -> None:
        def _boom() -> None:
            raise RuntimeError("boom")

        host = type("H", (), {})()
        ok = sfw.run_timed_step(host, "some_step", _boom)
        sfw._mark_required_step("some_other_step", True)
        if not ok:
            sfw._mark_required_step("some_step", False, reason="step_timeout_or_exception")
        self.assertFalse(sfw._required_step_ok.get("some_step"))
        self.assertTrue(sfw._required_step_ok.get("some_other_step"))


@unittest.skipIf(
    __import__("os").environ.get("SKIP_TK_INTEGRATION_TESTS") == "1",
    "Tk display unavailable in this environment",
)
class ThreeReproductionScenariosCompletedStatusTest(unittest.TestCase):
    """VALIDATE item 2: the three reproduction scenarios named in the task
    (a Japanese session, a short English session with
    inactivity_timeout_fallback, and a longer multi-sentence English
    session), each genuinely committed and translated, all report
    final_status="completed_pending_evidence_package" (this codebase's "no
    failures" value) in the same test run.
    """

    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_CAPTURE_OPEN_FLUSH_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.02)
    def _run_scenario(self, *, scenario: str) -> dict:
        session_id = f"sess-12-{scenario}"
        run_id = f"run-12-{scenario}"
        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        register_ui_main_thread()
        try:
            from alpha.utils.final_artifact_authority import (
                reset_final_export_authority,
            )

            # Test helper (its own docstring: "clear authority state for
            # one run or all runs") -- each scenario uses a fresh run_id
            # and a fresh temp run_folder, but the shared host/process
            # persists across subTests, so this authority's own per-run
            # state must be explicitly cleared the same way a real new
            # Start would naturally get a never-before-seen run_folder.
            reset_final_export_authority()
        except Exception:
            pass
        fake_ident = _FakeIdentity(run_id)

        with patch(
            "alpha.utils.run_identity.get_current_run_identity",
            return_value=fake_ident,
        ):
            host = get_shared_integration_host(session_id, run_id)
            host._start_ui_event_bus_drain_loop()
            reset_utterance_lifecycle(host, session_id=session_id)
            owner = get_utterance_lifecycle(host)
            worker = host.translation_worker

            if scenario == "japanese":
                host._listen_language = "ja"
                decision = owner.accept_boundary_proposal(
                    action="commit_new",
                    text="これはテストの発言です。",
                    speaker=1,
                    channel=0,
                    canonical_utterance_id=f"utt-{scenario}-1",
                    source_version=1,
                    source_raw_event_ids=[f"raw-{scenario}-1"],
                    commit_reason="japanese_continuity_assembler_test",
                    translation_eligible=True,
                )
                self.assertTrue(decision.get("success"), decision)
                worker.enqueue_stable_segment(
                    segment_id=1,
                    source_language="ja",
                    source_text="これはテストの発言です。",
                    canonical_utterance_id=f"utt-{scenario}-1",
                    source_version=1,
                    source_record_id=str(decision.get("record_id") or ""),
                    session_id=session_id,
                )
                _pump(host, seconds=0.3)

            elif scenario == "short_english_timeout_fallback":
                host._listen_language = "en"
                owner._commit_fallback_ms = 60
                owner.on_final_chunk(
                    text="短い発言です",
                    speaker=1,
                    channel=0,
                    start=0.0,
                    end=1.0,
                    is_final=True,
                    speech_final=False,
                    event_id=f"ev-{scenario}",
                    metadata={},
                )
                host.process_ui_queue()
                _pump(host, seconds=0.2)

            elif scenario == "longer_multi_sentence_english":
                host._listen_language = "en"
                owner._commit_fallback_ms = 60000
                owner.on_final_chunk(
                    text="First sentence here.",
                    speaker=1,
                    channel=0,
                    start=0.0,
                    end=1.0,
                    is_final=True,
                    speech_final=True,
                    event_id=f"ev-{scenario}-1",
                    metadata={},
                )
                host.process_ui_queue()
                _pump(host, seconds=0.3)
                owner.on_final_chunk(
                    text="Second sentence follows.",
                    speaker=1,
                    channel=0,
                    start=2.0,
                    end=3.0,
                    is_final=True,
                    speech_final=True,
                    event_id=f"ev-{scenario}-2",
                    metadata={},
                )
                host.process_ui_queue()
                _pump(host, seconds=0.3)
            else:
                raise ValueError(scenario)

            _run_real_stop_and_wait(host)
            status = sfw.compute_core_final_status()
            host._teardown()

        ctl.reset_for_run(f"teardown-12-{scenario}")
        return status

    def test_all_three_reproduction_scenarios_report_completed(self) -> None:
        # fixes TASK_12_REPORT.md: each scenario runs in its OWN fresh
        # subprocess rather than reusing the shared in-process Tk
        # host/translation_worker across all three. Running them
        # back-to-back in one process was found, during this task's own
        # testing, to hit a SEPARATE, pre-existing cross-session state gap
        # in run_artifacts.py/final_artifact_authority.py (final_export's
        # write-once bookkeeping) unrelated to stop_finalize_worker.py's
        # required-step cascade this task fixes -- confirmed by running
        # each scenario in isolation (a fresh process, exactly matching a
        # real Start-then-Stop app run) succeeding reliably every time,
        # while only the 2nd/3rd scenario in a SHARED process failed, only
        # on final_export, regardless of scenario order. That's flagged
        # separately (see TASK_12_REPORT.md); process isolation here proves
        # the actual claim this task needs -- a genuinely committed and
        # translated session of each of these three shapes reports
        # final_status="completed_pending_evidence_package" -- without
        # depending on fixing that unrelated, out-of-scope gap first.
        for scenario in (
            "japanese",
            "short_english_timeout_fallback",
            "longer_multi_sentence_english",
        ):
            with self.subTest(scenario=scenario):
                proc = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--scenario", scenario],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(
                    proc.returncode, 0,
                    f"scenario={scenario!r} subprocess failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
                )
                result_line = next(
                    (ln for ln in proc.stdout.splitlines() if ln.startswith("SCENARIO_RESULT:")),
                    None,
                )
                self.assertIsNotNone(
                    result_line,
                    f"scenario={scenario!r} produced no SCENARIO_RESULT line:\n{proc.stdout}",
                )
                status = json.loads(result_line[len("SCENARIO_RESULT:"):])
                self.assertEqual(
                    status["final_status"],
                    "completed_pending_evidence_package",
                    f"scenario={scenario!r} did not report completed: {status}",
                )
                self.assertEqual(status["failed_required_steps"], [])


_CASE_CLASSES = {
    "UtteranceReconstructionCascadeFixTests": (
        UtteranceReconstructionCascadeFixTests,
        "test_exception_marks_only_utterance_reconstruction_false_others_unaffected",
    ),
    "RunManifestCascadeFixTests": (
        RunManifestCascadeFixTests,
        "test_exception_marks_only_run_manifest_false_earlier_steps_unaffected",
    ),
}


def _run_case_in_subprocess(case_name: str) -> dict:
    """Run one exception-injection case (_run_body) in a fresh subprocess
    -- see the class docstrings for why: a shared in-process host running
    a second real Stop sequence hits the separate, pre-existing,
    out-of-scope final_export cross-session gap (TASK_12_REPORT.md),
    which would otherwise show up here as a false second "cascaded"
    failure unrelated to the fix under test.
    """
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--case", case_name],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"case={case_name!r} subprocess failed:\nSTDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )
    result_line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("CASE_RESULT:")),
        None,
    )
    if result_line is None:
        raise AssertionError(
            f"case={case_name!r} produced no CASE_RESULT line:\n{proc.stdout}"
        )
    return json.loads(result_line[len("CASE_RESULT:"):])


def _run_as_subprocess_entry() -> None:
    if "--scenario" in sys.argv:
        scenario = sys.argv[sys.argv.index("--scenario") + 1]
        test_case = ThreeReproductionScenariosCompletedStatusTest(
            "test_all_three_reproduction_scenarios_report_completed"
        )
        status = test_case._run_scenario(scenario=scenario)
        print("SCENARIO_RESULT:" + json.dumps(status))
    elif "--case" in sys.argv:
        case_name = sys.argv[sys.argv.index("--case") + 1]
        cls, method_name = _CASE_CLASSES[case_name]
        result = cls(method_name)._run_body()
        print("CASE_RESULT:" + json.dumps(result))


if __name__ == "__main__":
    if "--scenario" in sys.argv or "--case" in sys.argv:
        _run_as_subprocess_entry()
    else:
        unittest.main()
