"""An English revision of a committed line reaches the ledger, and so the export.

WHAT WAS BROKEN
---------------
Two gates, each written for another case, stopped every English lifecycle
revision short of the canonical ledger. The pane and the store took the
correction; the ledger -- which the export is built from -- kept the old text.

1. `_commit_locked` spreads the identity-registry entry into the commit
   metadata. For a SUPERSEDE its `canonical_record_id` is the record being
   REVISED, and duplicate protection reads that key as "the Japanese assembler
   already wrote the ledger" (`already_committed`) and skips the write.
2. With that fixed, the lifecycle's own observation of the commit makes duplicate
   protection's second observation an `idempotent_replay`, and a revision's
   record already exists, so the registry's first-commit exception
   ("awaiting_canonical_commit") does not apply: dropped as a duplicate.

Measured through the real app replaying the owner's messages: with only the
lifecycle half of item 35 the pane read "I'm sending you both." while the
sealed export read "I will send you a"; with gate 1 fixed alone the log showed
`DUPLICATE_IGNORE reason=idempotent_replay` for every revision. Item 66's
docstring had measured gate 1 on the extend path and routed around it, which is
also why the extend path's continuation never reached the export at all.

Driven here through the real lifecycle, the real `_publish_final_transcript_segment`,
the real `_display_transcript_item`, the real identity registry and the real
ledger. Only the Tk queue is a plain `queue.Queue` drained by the test.
"""

import queue
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription import utterance_lifecycle as ul  # noqa: E402
from alpha.transcription.canonical_identity_registry import reset_for_session  # noqa: E402
from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402
from alpha.transcription.duplicate_protection import DuplicateProtectionMixin  # noqa: E402

SESSION = "sess-item35-ledger"


class _Host(DuplicateProtectionMixin):
    """The real publish and display halves, joined by a plain queue."""

    _publish_final_transcript_segment = DeepgramClientMixin._publish_final_transcript_segment

    def __init__(self):
        self.transcript_store = TranscriptStore()
        self.transcript_queue = queue.Queue()
        self._live_session_id = SESSION
        self._listen_language = "en"
        self._frozen_ledger_error_count = 0

    def drain(self):
        while not self.transcript_queue.empty():
            self._display_transcript_item(self.transcript_queue.get())


class _Case(unittest.TestCase):
    def setUp(self):
        ctl.reset_for_run("run-item35-ledger")
        reset_for_session(SESSION)
        self.host = _Host()
        self.life = ul.UtteranceLifecycleOwner()
        self.life.bind_host(self.host)
        self.life.reset_for_session(SESSION)
        self.n = 0

    def tearDown(self):
        ctl.reset_for_run("teardown-item35-ledger")
        reset_for_session("teardown-item35-ledger")

    def interim(self, text, start, end):
        self.n += 1
        self.life.on_interim(
            text=text, speaker=1, channel=0, start=start, end=end,
            event_id=f"interim-{self.n}",
            metadata={"start_time": start, "end_time": end},
        )
        self.host.drain()

    def final(self, text, start, end, *, speech_final):
        self.n += 1
        self.life.on_final_chunk(
            text=text, speaker=1, channel=0, start=start, end=end,
            is_final=True, speech_final=speech_final, event_id=f"final-{self.n}",
            metadata={"start_time": start, "end_time": end},
        )
        self.host.drain()

    def utterance_end(self):
        self.n += 1
        self.life.on_utterance_end(channel=0, event_id=f"ue-{self.n}")
        self.host.drain()

    def ledger_texts(self):
        return [str(r.get("final_text") or "") for r in ctl.get_active_records()]

    def store_texts(self):
        return [s.text for s in self.host.transcript_store.get_all()]


class TheOwnersSentenceIsOneRecordTest(_Case):
    def test_the_ledger_holds_the_provider_s_last_word_once(self):
        self.interim("I will send you a", 13.28, 14.08)
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), ["I will send you a"], "fixture: the early commit")
        self.interim("I will send you both.", 13.28, 14.40)
        self.interim("I'm sending you both.", 13.28, 14.48)
        self.utterance_end()
        self.assertEqual(
            self.ledger_texts(),
            ["I'm sending you both."],
            "the export is built from the ledger; it must hold the revision, once",
        )
        self.assertEqual(self.store_texts(), ["I'm sending you both."])
        self.assertEqual(ctl.get_action_counts().get("revise"), 1)

    def test_different_speech_still_appends(self):
        self.interim("Good afternoon.", 2.72, 3.36)
        self.utterance_end()
        self.interim("Good afternoon. How are you?", 6.46, 7.58)
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), ["Good afternoon.", "Good afternoon. How are you?"])
        self.assertFalse(ctl.get_action_counts().get("revise"))


class AFinalCorrectionReachesTheLedgerTooTest(_Case):
    """The pre-existing path through `_supersede_committed_locked`.

    Deepgram's is_final for audio the app committed early from an interim is a
    correction of that record (same start, related text). The pane took it;
    gate 1 kept it out of the ledger, so the export held the early guess.
    """

    def test_the_final_revises_the_early_commit(self):
        self.interim("I will send you a", 13.28, 14.08)
        self.utterance_end()
        self.final("I will send you a lot.", 13.28, 14.60, speech_final=True)
        self.assertEqual(self.ledger_texts(), ["I will send you a lot."])
        self.assertEqual(self.store_texts(), ["I will send you a lot."])


if __name__ == "__main__":
    unittest.main()
