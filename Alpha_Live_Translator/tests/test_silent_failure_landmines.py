"""Regression tests for items 60-63 — silent-failure landmines.

All four were verified against the code before being fixed; none came from the
audit workflow (which returned zero findings).

* **60** — `_assembler_commit_gate_failed` was set at three sites and cleared at
  exactly one, inside `reset()`. A single failed proposal therefore discarded
  **every remaining commit of the session**, silently, while the run still
  reported `completed`. Never fired in the recorded corpus, which is exactly why
  the first occurrence would go unnoticed.
* **61** — a disabled canonical ledger reported `ok=True` with nothing written
  and no signal anywhere that the store feeding the FINAL export was off.
* **62** — the Japanese path discarded `_display_transcript_item`'s return, so a
  `retry_pending` verdict never reached its handler: the item was dropped
  instead of retried.
* **63** — `_assembler_exception_recovery_buffer` was **written and never read**.
  Grep showed an init, a reset and one write, and zero reads: text stashed there
  on an assembler exception was guaranteed lost.

The gate in 60 is kept, not deleted — refusing to keep committing into a broken
transaction is correct. What changed is its *scope*: the failure belongs to the
utterance that broke, not to the rest of the session.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class CommitGateScopeTest(unittest.TestCase):
    """Item 60 — the gate must degrade, not latch for the session."""

    def _assembler(self):
        from alpha.transcription.japanese_sentence_assembler import (
            get_japanese_continuity_assembler,
        )

        class _Host:
            _live_session_id = "sess-item60"
            _listen_language = "ja"
            _is_finalizing = False
            _is_stopping = False
            is_listening = True

            def _publish_final_transcript_segment(self, *a, **k):
                return True

        return get_japanese_continuity_assembler(_Host())

    def test_the_gate_records_which_utterance_tripped_it(self):
        assembler = self._assembler()
        self.assertTrue(
            hasattr(assembler, "_commit_gate_failed_utterance_id"),
            "the gate has no utterance scope, so it can only latch for the session",
        )

    def test_a_new_utterance_clears_the_gate(self):
        """The whole point: one broken transaction must not kill the session."""
        assembler = self._assembler()
        assembler._assembler_commit_gate_failed = True
        assembler._commit_gate_failed_utterance_id = "jp-utt-broken"
        assembler._current_canonical_utterance_id = "jp-utt-fresh"
        assembler._publish_sentence(1, "新しい発話です。", {}, "test_new_utterance")
        # The gate may legitimately re-trip on THIS utterance (the fixture has
        # no real commit path). What must never survive is the OLD utterance's
        # failure latching the session shut.
        self.assertNotEqual(
            "jp-utt-broken",
            assembler._commit_gate_failed_utterance_id,
            "the previous utterance's failure is still latched, so every "
            "remaining commit of the session would be silently discarded",
        )

    def test_the_gate_still_blocks_the_utterance_that_broke(self):
        """The integrity purpose is kept: do not keep committing into a
        transaction that has already failed."""
        assembler = self._assembler()
        assembler._assembler_commit_gate_failed = True
        assembler._commit_gate_failed_utterance_id = "jp-utt-broken"
        assembler._current_canonical_utterance_id = "jp-utt-broken"
        assembler._publish_sentence(1, "同じ発話の続きです。", {}, "test_same_utterance")
        self.assertTrue(
            assembler._assembler_commit_gate_failed,
            "the gate must still hold for the utterance whose transaction broke",
        )


class DisabledLedgerIsVisibleTest(unittest.TestCase):
    """Item 61."""

    def test_a_disabled_ledger_announces_itself(self):
        from alpha.transcription import canonical_transcript_ledger as ctl

        self.assertTrue(
            hasattr(ctl, "_LEDGER_DISABLED_WARNED"),
            "nothing anywhere reports that the store feeding the FINAL export "
            "is switched off",
        )

    def test_the_skip_path_still_returns_no_record_id(self):
        """This part was already sound and must stay that way: callers must not
        be able to mistake a skipped write for a stored record."""
        source = (
            PROJECT_ROOT / "alpha" / "transcription" / "canonical_transcript_ledger.py"
        ).read_text(encoding="utf-8")
        line = [
            l for l in source.splitlines()
            if '"skipped": True' in l and "return" in l
        ]
        self.assertEqual(1, len(line), "the skip branch changed shape")
        self.assertIn('"ok": True', line[0])
        self.assertNotIn("record_id", line[0])


class RetryPendingIsPropagatedTest(unittest.TestCase):
    """Item 62 — the signal has to survive the Japanese path."""

    def test_the_japanese_commit_path_returns_the_retry_verdict(self):
        source = (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8"
        )
        start = source.index("def _commit_transcript_item_to_store")
        source = source[start : start + 12000]
        self.assertIn(
            "retry_pending",
            source,
            "the Japanese path discards the retry verdict, so the item is "
            "dropped instead of retried",
        )
        self.assertNotIn(
            "\n        DuplicateProtectionMixin._display_transcript_item(self, item)\n",
            source,
            "the return value is being discarded again",
        )


class AssemblerExceptionRecoveryTest(unittest.TestCase):
    """Item 63 — the recovery buffer must actually be recovered."""

    def test_the_recovery_buffer_is_no_longer_write_only(self):
        """It was init'd, reset and written -- and never read. The name
        promised a recovery that no code performed."""
        source = Path(
            PROJECT_ROOT / "alpha" / "transcription" / "japanese_sentence_assembler.py"
        ).read_text(encoding="utf-8")
        write_site = source.index("_assembler_exception_recovery_buffer = entry")
        tail = source[write_site : write_site + 600]
        self.assertIn(
            "_quarantine_recovery_pending",
            tail,
            "the stashed fragment is not queued for replay, so it is still lost",
        )

    def test_recovery_reuses_the_existing_drain_rather_than_a_second_mechanism(self):
        from alpha.transcription.japanese_sentence_assembler import (
            JapaneseContinuityAssembler,
        )

        self.assertTrue(
            hasattr(JapaneseContinuityAssembler, "_drain_quarantine_recovery"),
            "item 43's drain is the one recovery path; do not add a second",
        )


if __name__ == "__main__":
    unittest.main()
