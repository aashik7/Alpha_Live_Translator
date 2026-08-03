"""Task 9 — deterministic tests for Issues 1 and 2, plus the ONE real-thread
integration test for Issue 3.

Issue 1: reconcile_translation_gaps() used to log
TRANSLATION_RECONCILIATION_FORCED_SUBMIT *before* calling
enqueue_stable_segment(), so neither an exception nor a plain rejection
from that call was ever visible, and a rejected/failed forced submission
was silently treated the same as a successful one (forced_count simply
didn't increment, no error, no step failure). Root cause: main_window.py's
_begin_graceful_stop calls translation_worker.stop_accepting() on the UI
thread the instant Stop is clicked, before this background reconciliation
step even runs, so the ordinary accept-gate always rejected every forced
submission. Fixed with a narrow, explicit force=True bypass of only that
gate (translation_worker.py), plus outcome-aware logging and a raised
TranslationReconciliationError when any gap is left unresolved
(stop_finalize_worker.py).

Issue 2: build_stop_finalize_summary() is called a second time, later,
from evidence_pointer_finalize.py's background thread -- reading the same
process-global, run-id-unscoped _required_step_ok/_stop_state that a
subsequent Stop's _reset_stop_state() can have already reset for a
different run by the time that delayed thread runs. Fixed with a run-id
scoped cache: the one synchronous, authoritative computation for a run is
reused by any later call for the SAME run id instead of recomputing
against whatever the shared globals currently hold.

Issue 3: build a real, unmocked integration harness (real Tk root, real Tk
.after() debounce timer, real TranslationWorker background thread, real
stop_finalize_worker background thread) and prove a commit with
commit_reason="inactivity_timeout_fallback" followed immediately by a real
Stop still results in the segment reaching translation_worker for real.
This is the standing regression test for this whole failure class going
forward.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
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
from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402
from alpha.transcription.duplicate_protection import DuplicateProtectionMixin  # noqa: E402
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    reset_utterance_lifecycle,
)
from alpha.translation.translation_worker import TranslationWorker  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import stop_finalize_worker as sfw  # noqa: E402
from alpha.utils.ui_thread_guard import register_ui_main_thread  # noqa: E402


# ---------------------------------------------------------------------
# Issue 1
# ---------------------------------------------------------------------

class FakeWorkerRaises:
    def __init__(self) -> None:
        self._revision_events: list[dict] = []

    def enqueue_stable_segment(self, **kwargs) -> bool:
        raise RuntimeError("simulated DeepL client init failure")


class FakeWorkerRejects:
    def __init__(self) -> None:
        self._revision_events: list[dict] = []

    def enqueue_stable_segment(self, **kwargs) -> bool:
        return False


class Issue1ReconciliationFailureVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_id = "run-9-issue1"
        ctl.reset_for_run(self.run_id)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-9-issue1")

    def _commit(self, utterance_id: str) -> None:
        result = ctl.append_record(
            speaker=1,
            assembler_text="text",
            final_text="text",
            source_raw_event_ids=[f"raw-{utterance_id}"],
            commit_reason="inactivity_timeout_fallback",
            metadata={
                "canonical_utterance_id": utterance_id,
                "translation_eligible": True,
                "source_version": 1,
                "session_id": "sess-9-issue1",
            },
        )
        self.assertTrue(result.get("ok"), result)

    def test_1_exception_from_forced_submit_is_logged_with_full_detail(self) -> None:
        self._commit("utt-raises")
        worker = FakeWorkerRaises()
        host = type("H", (), {"translation_worker": worker, "_listen_language": "en"})()

        with self.assertLogs("alpha.utils.stop_finalize_worker", level="ERROR") as log_ctx:
            with self.assertRaises(sfw.TranslationReconciliationError):
                sfw.reconcile_translation_gaps(host)

        joined = "\n".join(log_ctx.output)
        self.assertIn("TRANSLATION_RECONCILIATION_FORCED_SUBMIT_EXCEPTION", joined)
        self.assertIn("RuntimeError", joined)
        self.assertIn("simulated DeepL client init failure", joined)

    def test_2_rejected_forced_submit_raises_and_counts_as_failure(self) -> None:
        self._commit("utt-rejects")
        worker = FakeWorkerRejects()
        host = type("H", (), {"translation_worker": worker, "_listen_language": "en"})()

        with self.assertLogs("alpha.utils.stop_finalize_worker", level="ERROR") as log_ctx:
            with self.assertRaises(sfw.TranslationReconciliationError):
                sfw.reconcile_translation_gaps(host)

        self.assertTrue(
            any("TRANSLATION_RECONCILIATION_FORCED_SUBMIT_REJECTED" in m for m in log_ctx.output)
        )

    def test_3_run_timed_step_reports_failure_not_a_silent_no_op(self) -> None:
        self._commit("utt-step-failure")
        worker = FakeWorkerRejects()
        host = type("H", (), {"translation_worker": worker, "_listen_language": "en"})()

        ok = sfw.run_timed_step(
            host, "translation_reconciliation", lambda: sfw.reconcile_translation_gaps(host)
        )
        self.assertFalse(
            ok, "a failed forced submission must make the whole step report failure"
        )

    def test_4_successful_forced_submit_with_force_true_bypasses_stop_accepting(self) -> None:
        # Reproduces the exact live-testing precondition: Stop already
        # called stop_accepting() before reconciliation runs.
        self._commit("utt-force-bypass")
        worker = TranslationWorker(run_id=self.run_id, client=None, enabled=True)
        worker.stop_accepting()
        self.assertFalse(worker._accepting)
        host = type("H", (), {"translation_worker": worker, "_listen_language": "en"})()

        result = sfw.reconcile_translation_gaps(host)

        self.assertEqual(result["forced_count"], 1)
        self.assertEqual(len(worker._revision_events), 1)
        self.assertTrue(worker._revision_events[0]["accepted"])
        self.assertEqual(
            worker._counters["STABLE_TRANSLATION_JOBS_ACCEPTED"], 1,
            "a forced submission must genuinely increment the same counter a "
            "normal accepted submission would",
        )

    def test_5_force_false_is_still_rejected_while_not_accepting(self) -> None:
        worker = TranslationWorker(run_id=self.run_id, client=None, enabled=True)
        worker.stop_accepting()
        accepted = worker.enqueue_stable_segment(
            segment_id=1, source_language="en", source_text="hello",
            canonical_utterance_id="utt-not-forced", force=False,
        )
        self.assertFalse(accepted)


# ---------------------------------------------------------------------
# Issue 2
# ---------------------------------------------------------------------

class Issue2FinalStatusComputedOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        sfw._reset_stop_state()
        for name in sfw._REQUIRED_SYNC_STEPS:
            sfw._mark_required_step(name, True)
        with sfw._state_lock:
            sfw._stop_state["finalize_completed"] = True

    def tearDown(self) -> None:
        sfw._reset_stop_state()
        with sfw._state_lock:
            sfw._last_completed_run_id = ""
            sfw._last_completed_summary = {}

    def test_1_second_call_for_same_run_reuses_first_computation_not_stale_globals(self) -> None:
        class FakeIdentity:
            run_id = "run-9-issue2"

        host = type("H", (), {})()
        with patch(
            "alpha.utils.run_identity.get_current_run_identity",
            return_value=FakeIdentity(),
        ):
            first = sfw.build_stop_finalize_summary(host)
            self.assertEqual(first["final_status"], "completed_pending_evidence_package")

            # Simulate a NEW run's Stop resetting the shared globals before
            # the delayed evidence-pointer background thread gets around to
            # calling build_stop_finalize_summary() again for the OLD run.
            sfw._reset_required_steps()
            with sfw._state_lock:
                sfw._stop_state["failed_steps"] = []
                sfw._stop_state["timed_out_steps"] = []

            second = sfw.build_stop_finalize_summary(host)

        self.assertEqual(
            second["final_status"], "completed_pending_evidence_package",
            "a later call for the SAME run id must reuse the first "
            "authoritative computation, not recompute against globals that "
            "may already belong to a different run",
        )
        self.assertEqual(second, first)

    def test_2_different_run_id_does_not_reuse_stale_cache(self) -> None:
        class FakeIdentityA:
            run_id = "run-9-issue2-a"

        class FakeIdentityB:
            run_id = "run-9-issue2-b"

        host = type("H", (), {})()
        with patch(
            "alpha.utils.run_identity.get_current_run_identity",
            return_value=FakeIdentityA(),
        ):
            first = sfw.build_stop_finalize_summary(host)
        self.assertEqual(first["final_status"], "completed_pending_evidence_package")

        sfw._reset_required_steps()  # a genuinely different, in-progress run
        with patch(
            "alpha.utils.run_identity.get_current_run_identity",
            return_value=FakeIdentityB(),
        ):
            second = sfw.build_stop_finalize_summary(host)

        self.assertEqual(
            second["final_status"], "failed",
            "a different run id must never reuse another run's cached summary",
        )

    def test_3_no_run_identity_falls_through_to_fresh_computation(self) -> None:
        host = type("H", (), {})()
        with patch(
            "alpha.utils.run_identity.get_current_run_identity", return_value=None
        ):
            result = sfw.build_stop_finalize_summary(host)
        self.assertIn("final_status", result)


# ---------------------------------------------------------------------
# Issue 3 -- real-thread integration harness (the standing regression test)
# ---------------------------------------------------------------------

class FakeDeepLClient:
    """Instant, in-process fake -- no network, fully deterministic, but the
    REAL TranslationWorker background thread still calls it for real."""

    def __init__(self) -> None:
        self.available = True

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        return f"[EN] {text}"


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.closed = False

    def send(self, data, opcode=None) -> None:
        self.sent.append((data, opcode))

    def close(self) -> None:
        self.closed = True


class RealIntegrationHost(tk.Tk, DeepgramClientMixin, DuplicateProtectionMixin):
    """The smallest real subset wiring main_window.py, stop_finalize_worker.py,
    and translation_worker.py together as in production: a real (hidden) Tk
    root so submit_text_for_translation's real .after() debounce timer
    genuinely fires via Tk's own scheduler, the real transcript-queue drain
    loop (DuplicateProtectionMixin), the real UI-hook methods
    (_on_store_segment_added/_updated) borrowed unmodified from AlphaApp,
    and a real TranslationWorker with its real background thread started.

    Only the Deepgram WebSocket wait is stubbed (a FakeWebSocket plus an
    overridden _wait_for_final_transcripts_after_finalize) -- that transport
    is explicitly frozen/out of scope for this task and irrelevant to the
    translation-delivery race under test; everything else in the stop path
    (main_window.py, stop_finalize_worker.py, translation_worker.py) runs
    unmodified and for real.
    """

    submit_text_for_translation = AlphaApp.submit_text_for_translation
    _flush_pending_translation_submit = AlphaApp._flush_pending_translation_submit
    flush_pending_translation_submissions = AlphaApp.flush_pending_translation_submissions
    _show_translation_loading_item = AlphaApp._show_translation_loading_item
    _clear_translation_loading_item = AlphaApp._clear_translation_loading_item
    _remove_translation_item_for_utterance = AlphaApp._remove_translation_item_for_utterance
    _log_translation_display_skip = AlphaApp._log_translation_display_skip
    _on_store_segment_added = AlphaApp._on_store_segment_added
    _on_store_segment_updated = AlphaApp._on_store_segment_updated
    _transcript_box = AlphaApp._transcript_box
    _remove_interim_line_from_display = AlphaApp._remove_interim_line_from_display
    _insert_speaker_segment_line = AlphaApp._insert_speaker_segment_line
    _speaker_tag = AlphaApp._speaker_tag
    _ui_speaker_label_text = AlphaApp._ui_speaker_label_text
    _clear_text_placeholder = AlphaApp._clear_text_placeholder
    _maybe_scroll_transcript_box = AlphaApp._maybe_scroll_transcript_box
    _refresh_transcript_scrollbar = AlphaApp._refresh_transcript_scrollbar
    publish_transcript_event = AlphaApp.publish_transcript_event
    _run_on_ui_thread = AlphaApp._run_on_ui_thread
    _start_ui_event_bus_drain_loop = AlphaApp._start_ui_event_bus_drain_loop
    _register_ui_event_bus_handlers = AlphaApp._register_ui_event_bus_handlers

    def __init__(self, session_id: str, run_id: str) -> None:
        # fixes TASK_10_REPORT.md test-stability finding: tkinter/Tcl does
        # not reliably tolerate many independent tk.Tk() root
        # creations/destructions within a single process (observed as a
        # fatal, uncatchable process-level crash -- exit code 3, no Python
        # traceback -- when this harness's per-iteration create+destroy
        # pattern ran alongside the rest of the suite under `unittest
        # discover`, which runs every test file in one process). The Tk
        # root and its widgets are now created exactly ONCE per process
        # (see get_shared_integration_host()); __init__ only runs the first
        # time. All session-specific state (translation worker, queues,
        # transcript store, ids) is (re)established by reset_for_session(),
        # which every test now calls instead of constructing a new host.
        super().__init__()
        self.withdraw()
        self.initial_verse_box = tk.Text(self)
        self.translated_verse_box = tk.Text(self)
        self.translated_verse_box._placeholder_text = ""
        self.translated_verse_box._placeholder_active = False
        self._displayed_segment_count = 0
        self._exported_ui_segment_count = 0
        self._ui_render_limit_warned = False
        self._transcript_ui_scroll_last_mono = 0.0
        self.reset_for_session(session_id, run_id)

    def reset_for_session(self, session_id: str, run_id: str) -> None:
        """Re-establish all per-session state on the SAME persistent Tk
        root/interpreter -- shuts down any previous translation_worker
        first, matching a real app's Start/Stop session-to-session
        lifecycle (one long-lived main window, fresh worker/queues per
        session), rather than tearing down and recreating Tk itself."""
        old_worker = getattr(self, "translation_worker", None)
        if old_worker is not None:
            try:
                old_worker.shutdown(timeout_seconds=1.0)
            except Exception:
                pass
        # Cancel any still-armed self-rescheduling process_ui_queue() tick
        # from a PRIOR session on this same persistent root before starting
        # a new one (see the tracked-after_id override below).
        pending_tick = getattr(self, "_process_ui_queue_after_id", None)
        if pending_tick is not None:
            try:
                self.after_cancel(pending_tick)
            except Exception:
                pass
            self._process_ui_queue_after_id = None

        from alpha.summary.transcript_store import TranscriptStore

        self.transcript_store = TranscriptStore()
        self.transcript_queue: "queue.Queue" = queue.Queue()
        self._live_session_id = session_id
        self._listen_language = "en"
        self._frozen_ledger_error_count = 0

        self.translation_worker = TranslationWorker(
            run_id=run_id, evidence_dir=None, client=FakeDeepLClient(), enabled=True,
        )
        self.translation_worker.start()
        self.translation_enabled = True
        self._translation_segment_seq = 0
        self._translation_items_by_utterance = {}
        self._pending_translations_by_utterance = {}
        self._translation_debounce_after_ids = {}
        self._translation_loading_items = {}

        # Deepgram transport stand-ins -- frozen/out of scope, made instant.
        self._dg_ws = FakeWebSocket()
        self._audio_q = queue.Queue()
        self._stop_event = threading.Event()
        self.is_listening = True
        self._is_stopping = False
        self._is_finalizing = False
        self._stop_finalize_started = False
        self._ensure_graceful_stop_state()

    def process_ui_queue(self) -> None:
        # Override of DuplicateProtectionMixin.process_ui_queue: identical
        # behavior, but tracks its own self-rescheduled after() id so it can
        # be cancelled (reset_for_session/_teardown) instead of ticking
        # forever on this persistent root across unrelated later tests.
        self._process_ui_queue_once()
        if getattr(self, "winfo_exists", lambda: False)():
            from alpha.constants import UI_UPDATE_INTERVAL_MS

            self._process_ui_queue_after_id = self.after(
                UI_UPDATE_INTERVAL_MS, self.process_ui_queue
            )

    def _wait_for_final_transcripts_after_finalize(self, max_seconds=0.0) -> None:
        # Real Deepgram-close waits are frozen/out of scope for this test --
        # the race under test is the translation debounce/worker/reconcile
        # one, not Deepgram transport timing.
        return

    def _wait_bounded(self, seconds, deadline=None) -> None:
        return

    def _teardown(self) -> None:
        """Best-effort cleanup between iterations -- shuts down the
        translation worker and cancels the self-rescheduling
        process_ui_queue() tick. The Tk root itself is intentionally never
        destroyed mid-process; see the stability note in __init__."""
        try:
            self.translation_worker.shutdown(timeout_seconds=1.0)
        except Exception:
            pass
        pending_tick = getattr(self, "_process_ui_queue_after_id", None)
        if pending_tick is not None:
            try:
                self.after_cancel(pending_tick)
            except Exception:
                pass
            self._process_ui_queue_after_id = None


_shared_integration_host: "RealIntegrationHost | None" = None


def get_shared_integration_host(session_id: str, run_id: str) -> "RealIntegrationHost":
    """One Tk root/interpreter for the whole test process (see the
    stability note on RealIntegrationHost.__init__); each call resets it
    for a fresh session on the same persistent root."""
    global _shared_integration_host
    if _shared_integration_host is None:
        _shared_integration_host = RealIntegrationHost(session_id, run_id)
    else:
        _shared_integration_host.reset_for_session(session_id, run_id)
    return _shared_integration_host


def _pump(host: "RealIntegrationHost", *, seconds: float, interval: float = 0.02) -> None:
    """Genuinely run the real Tk event loop (processing real .after() timers)
    for a bounded real-time window, instead of manually invoking callbacks."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            host.update()
        except tk.TclError:
            break
        time.sleep(interval)


class Issue3RealThreadIntegrationTest(unittest.TestCase):
    """The standing regression test for this failure class: real Tk root,
    real .after() debounce timer, real TranslationWorker thread, real
    stop_finalize_worker background thread -- no stepped/simulated time."""

    @patch("alpha.transcription.deepgram_client.STOP_QUEUE_FLUSH_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_CAPTURE_OPEN_FLUSH_MAX_S", 0.05)
    @patch("alpha.transcription.deepgram_client.STOP_SETTLE_DELAY_S", 0.02)
    def _run_once(self, iteration: int) -> None:
        session_id = f"sess-9-integration-{iteration}"
        run_id = f"run-9-integration-{iteration}"

        ctl.reset_for_run(run_id)
        reset_for_session(session_id)

        register_ui_main_thread()
        host = get_shared_integration_host(session_id, run_id)
        try:
            host._start_ui_event_bus_drain_loop()

            from alpha.transcription.utterance_lifecycle import get_utterance_lifecycle

            reset_utterance_lifecycle(host, session_id=session_id)
            owner = get_utterance_lifecycle(host)
            owner._commit_fallback_ms = 60  # noqa: SLF001 -- real short fallback for a fast real timer

            # Real commit path start: arm an active utterance with
            # speech_final=False so the REAL inactivity-timeout Timer/after()
            # gets armed, exactly like a genuine trailing utterance at Stop.
            initial_decision = owner.on_final_chunk(
                text="短い発言です",
                speaker=1,
                channel=0,
                start=0.0,
                end=1.0,
                is_final=True,
                speech_final=False,
                event_id=f"ev-{iteration}",
                metadata={},
            )
            # UtteranceLifecycleOwner assigns the canonical_utterance_id
            # itself (sequential "U-N" ids) -- it is not caller-supplied.
            utterance_id = str(initial_decision.utterance_id)
            self.assertTrue(utterance_id)

            host.process_ui_queue()

            # Immediately trigger a real Stop -- the exact race under test:
            # the debounce timer, the inactivity-timeout Timer, the real
            # TranslationWorker thread, and stop_finalize_worker's real
            # background finalize thread are all now live concurrently.
            worker = host.translation_worker
            worker.stop_accepting()  # the exact real _begin_graceful_stop precondition
            sfw.begin_stop_from_ui(host)

            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                _pump(host, seconds=0.05)
                snap = sfw.get_stop_finalize_snapshot()
                if snap.get("worker_done"):
                    break
            self.assertTrue(
                sfw.get_stop_finalize_snapshot().get("worker_done"),
                "the real stop-finalize background thread did not complete "
                "within the bounded real-time wait",
            )
            _pump(host, seconds=0.3)

            revision_events = list(worker._revision_events)
            matching = [
                ev for ev in revision_events
                if ev.get("canonical_utterance_id") == utterance_id and ev.get("accepted")
            ]
            self.assertTrue(
                matching,
                f"iteration {iteration}: canonical_utterance_id={utterance_id!r} "
                f"never reached translation_worker; revision_events={revision_events}",
            )
            self.assertGreaterEqual(
                worker._counters["STABLE_TRANSLATION_JOBS_ACCEPTED"], 1,
                f"iteration {iteration}: STABLE_TRANSLATION_JOBS_ACCEPTED did "
                "not increment for a genuinely delivered forced/backstopped job",
            )
            # fixes TASK_10_REPORT.md VALIDATE item 2: this exact scenario
            # (English, inactivity_timeout_fallback, immediate real Stop)
            # used to also incorrectly mark utterance_reconstruction failed
            # -- assert it specifically is never among the failed required
            # steps here (this harness has no real run folder wired up, so
            # a couple of unrelated, folder-dependent steps still fail
            # regardless -- see test_task10_report.py for the precise,
            # scoped claim and rationale).
            status = sfw.compute_core_final_status()
            self.assertNotIn(
                "utterance_reconstruction",
                status["failed_required_steps"],
                f"iteration {iteration}: utterance_reconstruction incorrectly "
                f"counted as failed for a short English session: {status}",
            )
        finally:
            host._teardown()
            ctl.reset_for_run(f"teardown-9-integration-{iteration}")

    def test_inactivity_timeout_fallback_survives_immediate_real_stop_5x(self) -> None:
        # VALIDATE item 3: run at least 5 times in a row to catch
        # timing-dependent flakiness, not once.
        for i in range(5):
            with self.subTest(iteration=i):
                self._run_once(i)


if __name__ == "__main__":
    unittest.main()
