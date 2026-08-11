"""Regression tests for problem B — `CLIENT_DELIVERY_SPRINT_v5.md` item 43.

Quarantined Japanese fragments were destroyed outright. Two sites did it, both
leaving only a `NOISE_FRAGMENT_DROPPED` log line and no text:
`_drop_expired_quarantine_locked` (after `JAPANESE_NOISE_QUARANTINE_DROP_S`)
and `flush_quarantine_on_stop` (anything not a "valid short list term").

Measured across the recorded corpus: **4 distinct fragments were dropped and 3
were real Japanese speech** — `寝れた、幸せ、`, `。忘れちゃうし、`, `最近また` —
with only a bare `、` being true noise. Item 34 (tune the noise threshold) was
superseded because that sample is far too small to tune on, so quarantine is
made non-destructive instead: recover and commit, flagged, rather than delete.

**The one fragment class that still must not reach the commit path** is text
with no word characters. `accept_boundary_proposal` fails with `"empty_text"`
on blank text, and the assembler turns any proposal failure into
`_assembler_commit_gate_failed`, which is cleared only by `reset()` — so
committing the bare `、` would silently kill every remaining commit in the
session. That case is logged explicitly (`NOISE_FRAGMENT_NOT_COMMITTABLE`)
instead of silently dropped. `NoiseWithoutWordCharactersTest` pins it.
"""

import sys
import time
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha import constants  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    reset_for_session,
)
from alpha.transcription.japanese_sentence_assembler import (  # noqa: E402
    get_japanese_continuity_assembler,
)
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    reset_utterance_lifecycle,
)

# Real fragments the recorded corpus destroyed.
REAL_SPEECH = "寝れた、幸せ、"
REAL_SPEECH_2 = "最近また"
TRUE_NOISE = "、"
FOLLOW_UP = "それで昨日はよく眠れましたか。"


class _Host:
    def __init__(self) -> None:
        self._live_session_id = "sess-quarantine-regression"
        self._listen_language = "ja"
        self._is_finalizing = False
        self._is_stopping = False
        self.is_listening = True

    def _publish_final_transcript_segment(
        self,
        speaker: Any,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
        queue_item: Optional[dict[str, Any]] = None,
        commit_reason: Optional[str] = None,
    ) -> bool:
        return True


def _fresh_assembler():
    ctl.reset_for_run("quarantine-regression")
    reset_for_session("sess-quarantine-regression")
    host = _Host()
    reset_utterance_lifecycle(host, session_id="sess-quarantine-regression")
    return get_japanese_continuity_assembler(host)


def _expire_quarantine(assembler) -> None:
    """Age every quarantined entry past the drop threshold, then run the tick."""
    for entry in assembler._quarantine:
        entry["quarantined_mono"] = (
            time.monotonic() - (constants.JAPANESE_NOISE_QUARANTINE_DROP_S + 1)
        )
    with assembler._lock:
        assembler._drop_expired_quarantine_locked()


def _ledger_texts() -> list[str]:
    return [str(r.get("final_text") or "") for r in ctl.get_active_records()]


class ExpiredQuarantineIsRecoveredTest(unittest.TestCase):
    """The realistic path: quarantine expires mid-session, more speech follows."""

    def test_expired_real_speech_reaches_the_ledger(self):
        assembler = _fresh_assembler()
        with patch(
            "alpha.utils.transcript_evidence.log_stable_commit",
            side_effect=lambda **kwargs: "regression-stable-commit",
        ):
            assembler._quarantine_fragment(2, REAL_SPEECH, REAL_SPEECH)
            _expire_quarantine(assembler)
            assembler.ingest(2, FOLLOW_UP, {})
            assembler.flush("stop_listening")
        texts = _ledger_texts()
        self.assertTrue(
            any(REAL_SPEECH[:4] in t for t in texts),
            f"quarantined real speech was destroyed: {texts!r}",
        )

    def test_expiry_queues_for_recovery_rather_than_discarding(self):
        assembler = _fresh_assembler()
        assembler._quarantine_fragment(2, REAL_SPEECH_2, REAL_SPEECH_2)
        _expire_quarantine(assembler)
        self.assertEqual(
            0, len(assembler._quarantine), "the entry should have left quarantine"
        )
        self.assertEqual(
            1,
            len(assembler._quarantine_recovery_pending),
            "it must be queued for recovery, not dropped",
        )


class StopFlushRecoversTest(unittest.TestCase):
    """Stop is the last chance quarantined text ever gets."""

    def test_stop_flush_does_not_discard_real_speech(self):
        assembler = _fresh_assembler()
        with patch(
            "alpha.utils.transcript_evidence.log_stable_commit",
            side_effect=lambda **kwargs: "regression-stable-commit",
        ):
            assembler._quarantine_fragment(2, REAL_SPEECH_2, REAL_SPEECH_2)
            assembler.flush_quarantine_on_stop("stop_listening")
            # Text must have left quarantine into the commit path, not vanished.
            self.assertEqual(0, len(assembler._quarantine))
            self.assertEqual(0, len(assembler._quarantine_recovery_pending))
            buffered = (assembler._buffer or {}).get("text", "")
        self.assertIn(
            REAL_SPEECH_2[:3],
            str(buffered),
            "stop-flush dropped the fragment instead of committing it",
        )

    def test_stop_flush_drains_entries_queued_by_an_earlier_expiry(self):
        """A fragment that expired seconds before Stop must still land."""
        assembler = _fresh_assembler()
        with patch(
            "alpha.utils.transcript_evidence.log_stable_commit",
            side_effect=lambda **kwargs: "regression-stable-commit",
        ):
            assembler._quarantine_fragment(2, REAL_SPEECH_2, REAL_SPEECH_2)
            _expire_quarantine(assembler)
            self.assertEqual(1, len(assembler._quarantine_recovery_pending))
            assembler.flush_quarantine_on_stop("stop_listening")
        self.assertEqual(
            0,
            len(assembler._quarantine_recovery_pending),
            "the pending queue was not drained at stop -- that text is lost",
        )


class NoiseWithoutWordCharactersTest(unittest.TestCase):
    """The one class that must NOT be committed -- see the module docstring."""

    def test_punctuation_only_fragment_is_not_committed(self):
        assembler = _fresh_assembler()
        self.assertFalse(
            assembler._quarantine_entry_has_committable_content({"text": TRUE_NOISE})
        )

    def test_real_speech_is_committable(self):
        assembler = _fresh_assembler()
        self.assertTrue(
            assembler._quarantine_entry_has_committable_content({"text": REAL_SPEECH})
        )

    def test_alphanumeric_is_committable(self):
        assembler = _fresh_assembler()
        self.assertTrue(
            assembler._quarantine_entry_has_committable_content({"text": "N1"})
        )

    def test_punctuation_only_does_not_trip_the_session_killing_commit_gate(self):
        """Committing blank text fails accept_boundary_proposal, which sets
        `_assembler_commit_gate_failed` and silently drops every later commit."""
        assembler = _fresh_assembler()
        with patch(
            "alpha.utils.transcript_evidence.log_stable_commit",
            side_effect=lambda **kwargs: "regression-stable-commit",
        ):
            assembler._quarantine_fragment(2, TRUE_NOISE, TRUE_NOISE)
            _expire_quarantine(assembler)
            assembler._drain_quarantine_recovery()
        self.assertFalse(
            assembler._assembler_commit_gate_failed,
            "the punctuation-only fragment reached the commit path and killed "
            "the session's commit gate",
        )


if __name__ == "__main__":
    unittest.main()
