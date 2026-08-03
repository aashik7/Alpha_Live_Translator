"""Task 3C — deterministic Phase 3 acceptance-gate tests.

No real audio, no live Deepgram/DeepL calls, no timing-based flakiness:
DeepL is replaced by an instant in-process fake client; a real (but hidden)
Tk root + Text widget is used so the actual mark-based display logic in
main_window.py runs unmodified, but the translation-specific methods are
borrowed onto a minimal host instead of constructing the full GUI (which
requires customtkinter theming, audio widgets, etc. unrelated to this test).
"""

from __future__ import annotations

import queue
import sys
import tkinter as tk
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.translation.translation_worker import (  # noqa: E402
    TERMINAL_COMPLETED,
    TERMINAL_PERMANENTLY_FAILED,
    TranslationResult,
    TranslationWorker,
)


class FakeDeepLClient:
    """Instant, in-process fake -- no network, fully deterministic."""

    def __init__(self, prefix: str = "[EN] ") -> None:
        self.prefix = prefix
        self.available = True

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        return f"{self.prefix}{text}"


class TranslationUIHost(tk.Tk):
    """Minimal host borrowing AlphaApp's translation methods.

    Avoids constructing the full GUI (customtkinter theming, audio panes,
    etc., unrelated to translation ownership) while running the exact same
    production code for every method under test.
    """

    submit_text_for_translation = AlphaApp.submit_text_for_translation
    _flush_pending_translation_submit = AlphaApp._flush_pending_translation_submit
    _show_translation_loading_item = AlphaApp._show_translation_loading_item
    _clear_translation_loading_item = AlphaApp._clear_translation_loading_item
    _on_translation_worker_result = AlphaApp._on_translation_worker_result
    _handle_translation_worker_result = AlphaApp._handle_translation_worker_result
    _append_translation_result = AlphaApp._append_translation_result
    _remove_translation_item_for_utterance = AlphaApp._remove_translation_item_for_utterance
    _log_translation_display_skip = AlphaApp._log_translation_display_skip
    _get_translated_transcript_for_copy_export = AlphaApp._get_translated_transcript_for_copy_export
    _ui_speaker_label_text = AlphaApp._ui_speaker_label_text
    _clear_text_placeholder = AlphaApp._clear_text_placeholder
    _set_translation_status = AlphaApp._set_translation_status
    _record_translation_segment = AlphaApp._record_translation_segment
    loading_indicators_pending = AlphaApp.loading_indicators_pending

    def __init__(self, session_id: str = "sess-3c", listen_language: str = "en") -> None:
        super().__init__()
        self.withdraw()
        self.translated_verse_box = tk.Text(self)
        self.translated_verse_box._placeholder_text = ""
        self.translated_verse_box._placeholder_active = False
        self.translation_worker = None
        self.translation_enabled = True
        self._live_session_id = session_id
        self._listen_language = listen_language
        self._translation_segment_seq = 0
        self._translation_items_by_utterance = {}
        self._pending_translations_by_utterance = {}
        self._translation_debounce_after_ids = {}
        self._translation_loading_items = {}
        self._ui_callback_stats = {
            "scheduled": 0, "started": 0, "widget_updated": 0,
            "loading_cleared": 0, "completed": 0, "cancelled": 0,
        }
        self._translation_status_message = ""
        self._ui_call_queue: "queue.Queue" = queue.Queue()
        self._owner_thread = threading.current_thread()

    def _run_on_ui_thread(self, fn) -> None:
        # Tkinter widgets may only be touched from the thread that created
        # the root. Real production code marshals via self.after(0, fn) on
        # an active mainloop; there is no running mainloop in this harness,
        # so calls made from a background worker thread are queued instead
        # and drained deterministically by pump_ui_calls() on the main
        # thread -- same ordering guarantee (FIFO), no timing dependency.
        if threading.current_thread() is self._owner_thread:
            fn()
        else:
            self._ui_call_queue.put(fn)

    def pump_ui_calls(self) -> int:
        drained = 0
        while True:
            try:
                fn = self._ui_call_queue.get_nowait()
            except queue.Empty:
                break
            fn()
            drained += 1
        return drained

    def key(self, canonical_utterance_id: str):
        return (self._live_session_id, canonical_utterance_id)


class Task3CAcceptanceGateTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._hosts: list[TranslationUIHost] = []

    def tearDown(self) -> None:
        for host in self._hosts:
            try:
                worker = getattr(host, "translation_worker", None)
                if worker is not None and worker._thread is not None:
                    worker.shutdown(timeout_seconds=1.0)
            except Exception:
                pass
            try:
                host.destroy()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _make_host(self, **kwargs) -> TranslationUIHost:
        host = TranslationUIHost(**kwargs)
        self._hosts.append(host)
        return host

    def _make_host_and_worker(self, **kwargs):
        host = self._make_host(**kwargs)
        worker = TranslationWorker(
            run_id="run-3c", evidence_dir=None,
            on_translation_ready=host._on_translation_worker_result,
            client=FakeDeepLClient(), enabled=True,
        )
        host.translation_worker = worker
        return host, worker

    def _pop_queued_job(self, worker: TranslationWorker):
        job, target_lang = worker._queue.get_nowait()
        return job, target_lang

    def _make_result(
        self, job, target_lang, translated_text, *, status: str = "success"
    ) -> TranslationResult:
        terminal = TERMINAL_COMPLETED if status == "success" else TERMINAL_PERMANENTLY_FAILED
        return TranslationResult(
            run_id=job.run_id,
            segment_id=job.segment_id,
            source_segment_id=job.source_segment_id,
            translation_sequence=job.translation_sequence,
            source_language=job.source_language,
            target_language=target_lang,
            source_text=job.source_text,
            source_text_hash=job.source_text_hash,
            translated_text=translated_text,
            status=status,
            terminal_state=terminal,
            canonical_utterance_id=job.canonical_utterance_id,
            source_version=job.source_version,
            source_record_id=job.source_record_id,
            session_id=job.session_id,
            provider_completed_at=time.time(),
            completed_at=time.time(),
        )

    def _submit_and_flush(self, host, worker, text, *, canonical_utterance_id, source_version=1, replace_pending=False):
        host.submit_text_for_translation(
            text, canonical_utterance_id=canonical_utterance_id,
            source_version=source_version, replace_pending=replace_pending,
        )
        host._flush_pending_translation_submit(host.key(canonical_utterance_id))

    # ------------------------------------------------------------------
    # 1. Source-revision-preserves-other-translation
    # ------------------------------------------------------------------
    def test_1_source_revision_preserves_other_translation(self) -> None:
        host, worker = self._make_host_and_worker()
        self._submit_and_flush(host, worker, "Hello A", canonical_utterance_id="U-A")
        job_a, tgt_a = self._pop_queued_job(worker)
        worker._handle_result(self._make_result(job_a, tgt_a, "[EN] Hello A"))

        self._submit_and_flush(host, worker, "Hello B", canonical_utterance_id="U-B")
        job_b, tgt_b = self._pop_queued_job(worker)
        worker._handle_result(self._make_result(job_b, tgt_b, "[EN] Hello B"))

        self.assertIn("Hello A", host._translation_items_by_utterance["U-A"]["line_text"])
        item_b_before = dict(host._translation_items_by_utterance["U-B"])

        # Revise A's source (v1 -> v2): remove A's obsolete translation line.
        removed = host._remove_translation_item_for_utterance(
            canonical_utterance_id="U-A", source_version=2
        )
        self.assertTrue(removed)
        self.assertNotIn("U-A", host._translation_items_by_utterance)
        # B's item must be completely untouched: same dict, same mark.
        self.assertEqual(host._translation_items_by_utterance["U-B"], item_b_before)
        self.assertIn("Hello B", host._translation_items_by_utterance["U-B"]["line_text"])

    # ------------------------------------------------------------------
    # 2. Rapid-dual-revision test
    # ------------------------------------------------------------------
    def test_2_rapid_dual_revision_no_cross_contamination(self) -> None:
        host, worker = self._make_host_and_worker()
        for uid, text in (("U-A", "A v1"), ("U-B", "B v1")):
            self._submit_and_flush(host, worker, text, canonical_utterance_id=uid)
            job, tgt = self._pop_queued_job(worker)
            worker._handle_result(self._make_result(job, tgt, f"[EN] {text}"))

        # Rapid succession: arm both pending payloads back-to-back with NO
        # flush in between -- both must coexist independently.
        host.submit_text_for_translation(
            "A v2", canonical_utterance_id="U-A", source_version=2, replace_pending=True
        )
        host.submit_text_for_translation(
            "B v2", canonical_utterance_id="U-B", source_version=2, replace_pending=True
        )
        key_a, key_b = host.key("U-A"), host.key("U-B")
        self.assertIn(key_a, host._pending_translations_by_utterance)
        self.assertIn(key_b, host._pending_translations_by_utterance)
        self.assertEqual(host._pending_translations_by_utterance[key_a]["text"], "A v2")
        self.assertEqual(host._pending_translations_by_utterance[key_b]["text"], "B v2")

        host._flush_pending_translation_submit(key_a)
        host._flush_pending_translation_submit(key_b)
        job_a, tgt_a = self._pop_queued_job(worker)
        job_b, tgt_b = self._pop_queued_job(worker)
        self.assertEqual(job_a.canonical_utterance_id, "U-A")
        self.assertEqual(job_b.canonical_utterance_id, "U-B")
        worker._handle_result(self._make_result(job_a, tgt_a, "[EN] A v2"))
        worker._handle_result(self._make_result(job_b, tgt_b, "[EN] B v2"))

        self.assertIn("A v2", host._translation_items_by_utterance["U-A"]["line_text"])
        self.assertIn("B v2", host._translation_items_by_utterance["U-B"]["line_text"])
        self.assertEqual(host._translation_items_by_utterance["U-A"]["source_version"], 2)
        self.assertEqual(host._translation_items_by_utterance["U-B"]["source_version"], 2)

    # ------------------------------------------------------------------
    # 3. Stale-response test
    # ------------------------------------------------------------------
    def test_3_stale_version1_response_discarded_after_version2(self) -> None:
        host, worker = self._make_host_and_worker()
        self._submit_and_flush(host, worker, "v1 text", canonical_utterance_id="U-1", source_version=1)
        job_v1, tgt_v1 = self._pop_queued_job(worker)

        # source_version 2 commits (supersedes v1) BEFORE v1's provider
        # response arrives.
        self._submit_and_flush(
            host, worker, "v2 text", canonical_utterance_id="U-1",
            source_version=2, replace_pending=True,
        )
        job_v2, tgt_v2 = self._pop_queued_job(worker)

        # Late v1 response arrives first, then v2's.
        worker._handle_result(self._make_result(job_v1, tgt_v1, "STALE v1 translation"))
        worker._handle_result(self._make_result(job_v2, tgt_v2, "CORRECT v2 translation"))

        displayed = host._translation_items_by_utterance.get("U-1")
        self.assertIsNotNone(displayed)
        self.assertIn("CORRECT v2 translation", displayed["line_text"])
        self.assertNotIn("STALE v1", displayed.get("line_text", ""))
        self.assertEqual(displayed["source_version"], 2)

        # Defense-in-depth: directly exercise the NEW UI-layer version guard
        # (independent of the worker's own, already-correct check) by
        # attempting to apply a v1 result straight to the UI after v2 is
        # already displayed.
        skipped_before = dict(host._translation_items_by_utterance["U-1"])
        host._clear_translation_loading_item(
            segment_id=999999,
            terminal_state="completed",
            replace_with_text="FORCED STALE v1 text",
            canonical_utterance_id="U-1",
            source_version=1,
        )
        self.assertEqual(host._translation_items_by_utterance["U-1"], skipped_before)

    # ------------------------------------------------------------------
    # 4. Repeated-text test
    # ------------------------------------------------------------------
    def test_4_repeated_text_two_utterances_both_translated(self) -> None:
        host, worker = self._make_host_and_worker()
        accepted_1 = worker.enqueue_stable_segment(
            segment_id=101, source_language="en", source_text="Thank you.",
            canonical_utterance_id="U-1", source_version=1, session_id=host._live_session_id,
        )
        accepted_2 = worker.enqueue_stable_segment(
            segment_id=102, source_language="en", source_text="Thank you.",
            canonical_utterance_id="U-2", source_version=1, session_id=host._live_session_id,
        )
        self.assertTrue(accepted_1, "first utterance's submission must be accepted")
        self.assertTrue(accepted_2, "second utterance's identical-text submission must also be accepted")
        self.assertEqual(worker._counters["DUPLICATE_SUBMISSIONS_REJECTED"], 0)

    # ------------------------------------------------------------------
    # 5. Japanese Stop-flush test
    # ------------------------------------------------------------------
    def test_5_japanese_final_line_not_dropped_on_stop(self) -> None:
        # fixes REPAIR_PLAN.md Phase 3 acceptance gate: "Final Japanese
        # source line cannot be dropped during Stop." (ROOT_CAUSE.md's
        # current content is Task-1-scoped and does not name this symptom;
        # validated directly against the literal Phase 3 acceptance gate
        # text instead -- see TASK_3C_REPORT.md.)
        host = self._make_host(listen_language="ja")
        worker = TranslationWorker(
            run_id="run-3c-ja", evidence_dir=None,
            on_translation_ready=host._on_translation_worker_result,
            client=FakeDeepLClient(prefix="[JA->EN] "), enabled=True,
        )
        host.translation_worker = worker
        self.assertTrue(worker.start())
        accepted = worker.enqueue_stable_segment(
            segment_id=1, source_language="ja", source_text="最後の文です。",
            canonical_utterance_id="U-final", source_version=1,
            session_id=host._live_session_id,
        )
        self.assertTrue(accepted)
        summary = worker.shutdown(timeout_seconds=3.0)
        host.pump_ui_calls()

        self.assertEqual(summary["TRANSLATION_QUEUE_PENDING_AT_EXIT"], 0)
        self.assertEqual(summary["MISSING_TRANSLATION_SEGMENT_IDS"], 0)
        self.assertEqual(summary["UNRESOLVED_TRANSLATION_SEQUENCES"], [])
        self.assertTrue(summary["TRANSLATION_WORKER_STOPPED"])
        item = host._translation_items_by_utterance.get("U-final")
        self.assertIsNotNone(item, "final Japanese line's translation must not be dropped on Stop")
        self.assertIn("[JA->EN]", item.get("line_text", "") or "")

    # ------------------------------------------------------------------
    # 6. Loading-state test
    # ------------------------------------------------------------------
    def test_6_zero_loading_indicators_after_burst_and_stop(self) -> None:
        host = self._make_host()
        worker = TranslationWorker(
            run_id="run-3c-burst", evidence_dir=None,
            on_translation_ready=host._on_translation_worker_result,
            client=FakeDeepLClient(), enabled=True,
        )
        host.translation_worker = worker
        self.assertTrue(worker.start())

        for i in range(3):
            uid = f"U-{i}"
            self._submit_and_flush(host, worker, f"text {i} v1", canonical_utterance_id=uid, source_version=1)
            self._submit_and_flush(
                host, worker, f"text {i} v2", canonical_utterance_id=uid,
                source_version=2, replace_pending=True,
            )

        summary = worker.shutdown(timeout_seconds=3.0)
        host.pump_ui_calls()
        self.assertTrue(summary["TRANSLATION_WORKER_STOPPED"])
        self.assertEqual(
            host.loading_indicators_pending(), 0,
            f"loading items still pending: {host._translation_loading_items}",
        )


if __name__ == "__main__":
    unittest.main()
