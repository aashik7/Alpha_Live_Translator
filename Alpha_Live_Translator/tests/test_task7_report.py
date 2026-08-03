"""Task 7 — deterministic VALIDATE tests for the inactivity_timeout_fallback
translation-loss regression.

Root cause (see TASK_7_REPORT.md): utterance_lifecycle.py's on_timeout /
_commit_locked already bind canonical_utterance_id correctly for a
timeout-fallback commit, identical to any other commit reason -- that part
was never broken. The actual defect is in main_window.py: a newly committed
segment is coalesced behind a 120-350ms Tk .after() debounce timer
(submit_text_for_translation) before translation_worker.enqueue_stable_segment()
is ever called. Stop's finalize sequence (stop_finalize_worker.py) used to
call translation_worker.stop_accepting()/shutdown() with no knowledge of
that still-armed timer, silently abandoning any job whose debounce window
had not yet elapsed -- most likely exactly the segments committed via
inactivity_timeout_fallback, since by definition they tend to fire close to
Stop.

No real audio, no live provider calls, no Tk mainloop, no real timers.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    UtteranceLifecycleOwner,
)
from alpha.ui.main_window import AlphaApp  # noqa: E402


class Fix1TimeoutFallbackIdentityBindingTests(unittest.TestCase):
    """VALIDATE item 1: commit a record via the inactivity-timeout-fallback
    path and confirm canonical_utterance_id is non-empty and correctly bound
    in the emitted commit decision -- i.e. on_timeout uses the exact same
    identity-carrying _commit_locked path as every other commit reason, not
    a separate/degraded branch."""

    def setUp(self) -> None:
        self.owner = UtteranceLifecycleOwner(host=None, commit_fallback_ms=50)
        self.owner.reset_for_session("sess-7-fix1")

    def test_1_timeout_fallback_commit_carries_real_canonical_utterance_id(self) -> None:
        decision = self.owner.on_final_chunk(
            text="これはタイムアウトでコミットされる発言です",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=False,
            event_id="ev-timeout-1",
            metadata={},
        )
        self.assertIsNotNone(decision)
        token = self.owner._timeout_token  # noqa: SLF001 -- deterministic test access
        result = self.owner.on_timeout(token=token)
        self.assertIsNotNone(result, "on_timeout must actually commit the active utterance")
        self.assertTrue(result.should_commit)
        self.assertEqual(result.reason, "inactivity_timeout_fallback")
        self.assertTrue(
            str(result.utterance_id or "").strip(),
            "a timeout-fallback commit must carry a real canonical_utterance_id",
        )
        self.assertEqual(
            result.metadata.get("canonical_utterance_id"),
            result.utterance_id,
            "the emitted commit metadata must carry the same canonical_utterance_id",
        )
        self.assertEqual(result.metadata.get("lifecycle_commit_reason"), "inactivity_timeout_fallback")


class FakeTranslationWorker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue_stable_segment(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True


class _TranslationFlushHost:
    """Borrows the exact production methods under test onto a lightweight
    host, the same method-borrowing pattern used throughout this
    engagement's other test files (e.g. DuplicateProtectionMixin hosts)."""

    flush_pending_translation_submissions = AlphaApp.flush_pending_translation_submissions
    _flush_pending_translation_submit = AlphaApp._flush_pending_translation_submit
    _show_translation_loading_item = AlphaApp._show_translation_loading_item
    _clear_translation_loading_item = AlphaApp._clear_translation_loading_item

    def __init__(self) -> None:
        self.translation_worker = FakeTranslationWorker()
        self.translation_enabled = True
        self._live_session_id = "sess-7-fix2"
        self._listen_language = "en"
        self.translated_verse_box = None
        self._pending_translations_by_utterance = {}
        self._translation_debounce_after_ids = {}
        self._translation_segment_seq = 0

    def after(self, delay_ms, callback):
        # Synchronous stand-in for Tk's .after(): no real mainloop in tests.
        callback()
        return "fake-after-id"

    def after_cancel(self, after_id) -> None:
        pass


class Fix2PendingTranslationFlushTests(unittest.TestCase):
    """VALIDATE item 2: a segment sitting in the debounce window (armed
    timer, not yet fired) at the moment Stop wants to flush it must reach
    translation_worker.enqueue_stable_segment() exactly once, with its real
    canonical_utterance_id, instead of being silently abandoned."""

    def setUp(self) -> None:
        self.host = _TranslationFlushHost()

    def test_1_pending_debounced_job_is_flushed_exactly_once(self) -> None:
        key = ("sess-7-fix2", "jp-utt-timeout-committed")
        self.host._pending_translations_by_utterance[key] = {
            "text": "これはタイムアウトでコミットされる発言です",
            "speaker": 1,
            "timestamp": 0.0,
            "replace_pending": False,
            "session_id": "sess-7-fix2",
            "canonical_utterance_id": "jp-utt-timeout-committed",
            "source_version": 1,
            "source_record_id": "rec-timeout-1",
        }
        self.host._translation_debounce_after_ids[key] = "armed-timer-id"

        flushed = self.host.flush_pending_translation_submissions(timeout_seconds=1.0)

        self.assertEqual(flushed, 1)
        self.assertEqual(len(self.host.translation_worker.calls), 1)
        call = self.host.translation_worker.calls[0]
        self.assertEqual(call["canonical_utterance_id"], "jp-utt-timeout-committed")
        self.assertEqual(call["source_record_id"], "rec-timeout-1")
        self.assertEqual(
            self.host._pending_translations_by_utterance, {},
            "the flushed job must be removed from the pending map, not left duplicated",
        )

    def test_2_no_pending_jobs_is_a_safe_no_op(self) -> None:
        flushed = self.host.flush_pending_translation_submissions(timeout_seconds=1.0)
        self.assertEqual(flushed, 0)
        self.assertEqual(len(self.host.translation_worker.calls), 0)

    def test_3_two_pending_jobs_both_flushed_independently(self) -> None:
        key_a = ("sess-7-fix2", "jp-utt-a")
        key_b = ("sess-7-fix2", "jp-utt-b")
        self.host._pending_translations_by_utterance[key_a] = {
            "text": "発言A", "speaker": 1, "session_id": "sess-7-fix2",
            "canonical_utterance_id": "jp-utt-a", "source_version": 1,
            "source_record_id": "rec-a",
        }
        self.host._pending_translations_by_utterance[key_b] = {
            "text": "発言B", "speaker": 2, "session_id": "sess-7-fix2",
            "canonical_utterance_id": "jp-utt-b", "source_version": 1,
            "source_record_id": "rec-b",
        }
        self.host._translation_debounce_after_ids[key_a] = "timer-a"
        self.host._translation_debounce_after_ids[key_b] = "timer-b"

        flushed = self.host.flush_pending_translation_submissions(timeout_seconds=1.0)

        self.assertEqual(flushed, 2)
        queued_ids = {c["canonical_utterance_id"] for c in self.host.translation_worker.calls}
        self.assertEqual(queued_ids, {"jp-utt-a", "jp-utt-b"})


if __name__ == "__main__":
    unittest.main()
