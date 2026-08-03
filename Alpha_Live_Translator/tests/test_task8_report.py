"""Task 8 — deterministic VALIDATE tests for the structural fix closing the
"committed but never translated" failure class.

Part A root cause: `_on_store_segment_added`/`_on_store_segment_updated`
(main_window.py) are the ONE shared hook every commit reason funnels
through (English _commit_locked's 6 reasons via
duplicate_protection.py::_display_transcript_item, the Japanese continuity
assembler's commits via the same route, and Japanese manual-mode's direct
callers). Translation submission used to run AFTER all transcript-widget
rendering and behind an unconditional `if box is None: return` -- so a
torn-down/unavailable transcript box, or any exception in the rendering
code that ran first, silently dropped translation for an
already-successfully-committed record. The fix moves translation
submission ahead of rendering in both hooks.

Part B: a required stop-finalize step, translation_reconciliation, force-
submits any committed, translation-eligible canonical_utterance_id with no
matching accepted translation_worker job -- a self-healing backstop for
any future, still-undiscovered skip path.

No real audio, no live provider calls, no Tk mainloop, no real timers.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    reset_for_session,
)
from alpha.transcription.duplicate_protection import DuplicateProtectionMixin  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import stop_finalize_worker as sfw  # noqa: E402


class _RecordingSubmitHost(DuplicateProtectionMixin):
    """Combines the real production commit path (DuplicateProtectionMixin's
    _display_transcript_item) with the real production UI hooks
    (AlphaApp._on_store_segment_added / _on_store_segment_updated), the
    same method-borrowing pattern used throughout this engagement. The
    transcript box is deliberately left unset (None) so
    _transcript_box() returns None -- proving translation submission no
    longer depends on it."""

    _on_store_segment_added = AlphaApp._on_store_segment_added
    _on_store_segment_updated = AlphaApp._on_store_segment_updated
    _transcript_box = AlphaApp._transcript_box
    _remove_interim_line_from_display = AlphaApp._remove_interim_line_from_display
    _remove_translation_item_for_utterance = AlphaApp._remove_translation_item_for_utterance
    _log_translation_display_skip = AlphaApp._log_translation_display_skip

    def __init__(self, session_id: str) -> None:
        self.transcript_store = TranscriptStore()
        self._live_session_id = session_id
        self._frozen_ledger_error_count = 0
        self.initial_verse_box = None
        self.translated_verse_box = None
        self.submitted: list[dict] = []

    def submit_text_for_translation(self, text, **kwargs):
        self.submitted.append({"text": text, **kwargs})


# The 6 English _commit_locked() reasons found in utterance_lifecycle.py,
# plus the Japanese continuity-assembler's own commit_reason shapes
# (japanese_continuity_assembler_<reason> / stop_flush_incomplete_tail /
# assembler_exception_direct_commit_fallback) and Japanese manual-mode's
# lifecycle_commit_reason -- every commit_reason literal this codebase
# produces that reaches duplicate_protection.py::_display_transcript_item.
ALL_COMMIT_REASONS = [
    "utterance_end",
    "inactivity_timeout_fallback",
    "speech_final",
    "boundary_before_new_utterance",
    "speech_final_new_utterance",
    "supersede_then_commit",
    "japanese_continuity_assembler_safe_hold_timeout",
    "stop_flush_incomplete_tail",
    "assembler_exception_direct_commit_fallback",
]


class PartAEveryCommitReasonReachesTranslationTests(unittest.TestCase):
    """VALIDATE item 1: every commit_reason this codebase produces reaches
    submit_text_for_translation() (or, per Task 7, the debounce map -- here
    verified via the direct stub since Task 7 already covers the debounce
    mechanics)."""

    def setUp(self) -> None:
        self.session_id = "sess-8-partA"
        ctl.reset_for_run("run-8-partA")
        reset_for_session(self.session_id)
        self.host = _RecordingSubmitHost(self.session_id)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-8-partA")
        reset_for_session("teardown-8-partA")

    def test_1_every_known_commit_reason_reaches_submit_text_for_translation(self) -> None:
        for idx, reason in enumerate(ALL_COMMIT_REASONS):
            with self.subTest(commit_reason=reason):
                utterance_id = f"utt-{idx}-{reason}"
                item = {
                    "speaker": 1,
                    "text": f"発言 {reason}",
                    "is_final": True,
                    "session_id": self.session_id,
                    "channel_index": 0,
                    "canonical_utterance_id": utterance_id,
                    "provider_utterance_id": f"prov-{idx}",
                    "source_version": 1,
                    "source_raw_event_ids": [f"raw-{idx}-1"],
                    "translation_eligible": True,
                    "lifecycle_commit_reason": reason,
                }
                before = len(self.host.submitted)
                self.host._display_transcript_item(dict(item))
                self.assertEqual(
                    len(self.host.submitted), before + 1,
                    f"commit_reason={reason!r} did not reach submit_text_for_translation",
                )
                self.assertEqual(
                    self.host.submitted[-1]["canonical_utterance_id"], utterance_id
                )


class PartAUiTeardownDoesNotBlockTranslationTests(unittest.TestCase):
    """The specific structural defect: translation submission must not
    depend on the transcript widget being renderable, nor be skipped by an
    exception in the UI-rendering code that used to run first."""

    def setUp(self) -> None:
        self.session_id = "sess-8-partA-ui"
        ctl.reset_for_run("run-8-partA-ui")
        reset_for_session(self.session_id)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-8-partA-ui")
        reset_for_session("teardown-8-partA-ui")

    def test_1_on_store_segment_added_submits_even_with_no_transcript_box(self) -> None:
        host = _RecordingSubmitHost(self.session_id)
        self.assertIsNone(host._transcript_box())
        host._on_store_segment_added(
            1, "torn down box add", canonical_utterance_id="utt-a", source_version=1,
        )
        self.assertEqual(len(host.submitted), 1)
        self.assertEqual(host.submitted[0]["canonical_utterance_id"], "utt-a")

    def test_2_on_store_segment_updated_submits_even_with_no_transcript_box(self) -> None:
        host = _RecordingSubmitHost(self.session_id)
        self.assertIsNone(host._transcript_box())
        host._on_store_segment_updated(
            1, "torn down box update", canonical_utterance_id="utt-b", source_version=2,
        )
        self.assertEqual(len(host.submitted), 1)
        self.assertEqual(host.submitted[0]["canonical_utterance_id"], "utt-b")

    def test_3_rendering_exception_does_not_block_translation_submit(self) -> None:
        class _ExplodingBox:
            def configure(self, *a, **k):
                raise RuntimeError("simulated Tk teardown failure")

        host = _RecordingSubmitHost(self.session_id)
        host.initial_verse_box = _ExplodingBox()
        with self.assertRaises(RuntimeError):
            host._on_store_segment_added(
                1, "exploding box add", canonical_utterance_id="utt-c", source_version=1,
            )
        self.assertEqual(
            len(host.submitted), 1,
            "translation must already have been submitted before the "
            "rendering code (which now runs second) has a chance to raise",
        )


class InactivityTimeoutFallbackTranslationRegressionTest(unittest.TestCase):
    """VALIDATE item 5: dedicated named regression test for the original
    bug report -- a short utterance committed via
    inactivity_timeout_fallback, immediate Stop, must reach translation.
    Reproduces the reported shape end to end through
    _display_transcript_item (the real commit + hook path), so this exact
    failure can never silently return."""

    def setUp(self) -> None:
        self.session_id = "sess-8-repro"
        ctl.reset_for_run("run-8-repro")
        reset_for_session(self.session_id)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-8-repro")
        reset_for_session("teardown-8-repro")

    def test_short_utterance_inactivity_timeout_fallback_reaches_translation(self) -> None:
        host = _RecordingSubmitHost(self.session_id)
        item = {
            "speaker": 1,
            "text": "短い発言",
            "is_final": True,
            "session_id": self.session_id,
            "channel_index": 0,
            "canonical_utterance_id": "utt-timeout-repro",
            "provider_utterance_id": "prov-timeout-repro",
            "source_version": 1,
            "source_raw_event_ids": ["raw-timeout-repro-1"],
            "translation_eligible": True,
            "lifecycle_commit_reason": "inactivity_timeout_fallback",
            "canonical_decision": "TERMINAL_COMMIT",
        }
        host._display_transcript_item(item)
        self.assertEqual(len(host.submitted), 1)
        self.assertEqual(host.submitted[0]["canonical_utterance_id"], "utt-timeout-repro")


class FakeReconciliationWorker:
    def __init__(self) -> None:
        self._revision_events: list[dict] = []
        self.enqueue_calls: list[dict] = []

    def enqueue_stable_segment(self, **kwargs) -> bool:
        self.enqueue_calls.append(kwargs)
        self._revision_events.append(
            {
                "canonical_utterance_id": kwargs.get("canonical_utterance_id", ""),
                "accepted": True,
            }
        )
        return True


class PartBReconciliationSafetyNetTests(unittest.TestCase):
    """VALIDATE item 2: manually construct a committed record with NO
    debounce-map entry and NO queued job at all (simulating an entirely
    new/unknown future skip path this task never discovered) and confirm
    the reconciliation step force-submits it and logs a WARNING."""

    def setUp(self) -> None:
        self.run_id = "run-8-partB"
        ctl.reset_for_run(self.run_id)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-8-partB")

    def _commit_ledger_record(self, *, utterance_id: str, text: str, translation_eligible: bool = True) -> str:
        result = ctl.append_record(
            speaker=1,
            assembler_text=text,
            final_text=text,
            source_raw_event_ids=[f"raw-{utterance_id}"],
            commit_reason="inactivity_timeout_fallback",
            metadata={
                "canonical_utterance_id": utterance_id,
                "translation_eligible": translation_eligible,
                "source_version": 1,
                "session_id": "sess-8-partB",
            },
        )
        self.assertTrue(result.get("ok"), result)
        return str(result.get("record_id"))

    def test_1_committed_record_with_no_queued_job_is_force_submitted_and_warned(self) -> None:
        record_id = self._commit_ledger_record(
            utterance_id="utt-orphan-1", text="誰も送信しなかった発言",
        )
        worker = FakeReconciliationWorker()
        host = type("H", (), {"translation_worker": worker, "_listen_language": "ja"})()

        with self.assertLogs("alpha.utils.stop_finalize_worker", level="WARNING") as log_ctx:
            ok = sfw.run_timed_step(
                host,
                "translation_reconciliation",
                lambda: sfw.reconcile_translation_gaps(host),
            )

        self.assertTrue(ok, "reconciliation step must not raise/timeout in the normal case")
        self.assertEqual(len(worker.enqueue_calls), 1)
        call = worker.enqueue_calls[0]
        self.assertEqual(call["canonical_utterance_id"], "utt-orphan-1")
        self.assertEqual(call["source_record_id"], record_id)
        self.assertTrue(
            any("TRANSLATION_RECONCILIATION_FORCED_SUBMIT" in m for m in log_ctx.output),
            log_ctx.output,
        )

    def test_2_already_submitted_record_is_not_force_submitted_again(self) -> None:
        self._commit_ledger_record(utterance_id="utt-already-submitted", text="既に送信済みの発言")
        worker = FakeReconciliationWorker()
        worker._revision_events.append(
            {"canonical_utterance_id": "utt-already-submitted", "accepted": True}
        )
        host = type("H", (), {"translation_worker": worker, "_listen_language": "ja"})()

        sfw.reconcile_translation_gaps(host)

        self.assertEqual(
            len(worker.enqueue_calls), 0,
            "a record already reflected in translation_worker._revision_events "
            "must not be force-submitted a second time",
        )

    def test_3_translation_ineligible_record_is_never_force_submitted(self) -> None:
        self._commit_ledger_record(
            utterance_id="utt-ineligible", text="翻訳対象外の発言", translation_eligible=False,
        )
        worker = FakeReconciliationWorker()
        host = type("H", (), {"translation_worker": worker, "_listen_language": "ja"})()

        sfw.reconcile_translation_gaps(host)

        self.assertEqual(len(worker.enqueue_calls), 0)

    def test_4_required_step_is_registered_and_failure_is_not_swallowed(self) -> None:
        self.assertIn("translation_reconciliation", sfw._REQUIRED_SYNC_STEPS)

        def _boom() -> None:
            raise RuntimeError("simulated reconciliation crash")

        sfw._reset_required_steps()
        for name in sfw._REQUIRED_SYNC_STEPS:
            sfw._mark_required_step(name, name != "translation_reconciliation")

        ok = sfw.run_timed_step(
            type("H", (), {})(), "translation_reconciliation", _boom
        )
        self.assertFalse(
            ok, "run_timed_step must report failure when the step function raises"
        )
        sfw._mark_required_step(
            "translation_reconciliation", bool(ok), reason="test_forced_failure"
        )
        status = sfw.compute_core_final_status()
        self.assertEqual(status["final_status"], "failed")
        self.assertEqual(status["failure_reason"], "translation_reconciliation")


if __name__ == "__main__":
    unittest.main()
