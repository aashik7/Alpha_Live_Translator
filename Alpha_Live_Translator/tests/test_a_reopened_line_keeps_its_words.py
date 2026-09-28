"""A new version of a committed line keeps every word the old one had.

WHAT WAS BROKEN
---------------
Item 35 made a later guess at already-committed audio re-open that record and
commit a SUPERSEDE. Found in review on 2026-09-28 by driving the real code:

1. **The re-opened line lost its own head.** Every interim after the re-open
   went through item 66's `_trim_resent_tail_locked`, which compares the text
   with `_last_committed` -- and after a re-open that is the OLD VERSION OF THE
   SAME LINE. Deepgram's interims grow cumulatively, so the new text starts with
   the old one, the trim cut it off, and the SUPERSEDE overwrote the record:

       "I will send you" / UtteranceEnd / "I will send you both" /
       "I will send you both today" / UtteranceEnd

   exported (and showed) only "both today". Before item 35 the same messages
   exported "I will send you" / "both today". Any early commit of 3 or more
   words that the provider then extends does this.

2. **A correction or extend held open never reached the ledger.** Item 35 fixed
   the final-correction and extend paths only when they commit at once. When
   the final that triggers them has `speech_final=False` they hold the line --
   same utterance id, next version -- and it commits later as an ordinary
   COMMIT_ACTIVE, which carries the registry's record id, which duplicate
   protection reads as "already written". The export kept the early guess; the
   same self-trim as (1) cut the head off the pane's copy:

       "We should review" / inactivity timeout / final "the numbers" (held) /
       final "first."  ->  export "We should review", pane "the numbers, first."

WHAT THESE TESTS PIN
--------------------
Driven through the real lifecycle, the real `_publish_final_transcript_segment`,
the real `_display_transcript_item`, the real identity registry and the real
ledger; only the Tk queue is a plain `queue.Queue` drained by the test.

* a re-opened line that grows keeps its first words, after an UtteranceEnd and
  after the inactivity timeout
* a held extend and a held correction reach the ledger as one revised record,
  and the pane shows the same record
* item 66's trim still removes a re-sent tail from a genuinely NEW line
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

SESSION = "sess-item37-words"


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
        ctl.reset_for_run("run-item37-words")
        reset_for_session(SESSION)
        self.host = _Host()
        self.life = ul.UtteranceLifecycleOwner()
        self.life.bind_host(self.host)
        self.life.reset_for_session(SESSION)
        self.n = 0

    def tearDown(self):
        ctl.reset_for_run("teardown-item37-words")
        reset_for_session("teardown-item37-words")

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

    def timeout(self):
        self.life.on_timeout(token=self.life._timeout_token)
        self.host.drain()

    def ledger_texts(self):
        return [str(r.get("final_text") or "") for r in ctl.get_active_records()]

    def store_texts(self):
        return [s.text for s in self.host.transcript_store.get_all()]

    def assert_one_record_holding(self, *spoken):
        ledger = self.ledger_texts()
        self.assertEqual(len(ledger), 1, "one line of speech, one record: %r" % (ledger,))
        for words in spoken:
            self.assertIn(words, ledger[0], "the export lost words that were spoken")
        self.assertEqual(self.store_texts(), ledger, "the pane and the export disagree")


class AReopenedLineThatGrowsKeepsItsHeadTest(_Case):
    def test_after_an_utterance_end(self):
        self.interim("I will send you", 13.28, 14.08)
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), ["I will send you"], "fixture: the early commit")
        self.interim("I will send you both", 13.28, 14.40)
        self.interim("I will send you both today", 13.28, 14.90)
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), ["I will send you both today"])
        self.assertEqual(self.store_texts(), ["I will send you both today"])
        self.assertEqual(ctl.get_action_counts().get("revise"), 1)

    def test_after_the_inactivity_timeout(self):
        self.interim("We should review the", 30.0, 31.0)
        self.timeout()
        self.assertEqual(self.ledger_texts(), ["We should review the"], "fixture: the early commit")
        self.interim("We should review the numbers", 30.0, 31.6)
        self.interim("We should review the numbers first.", 30.0, 32.1)
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), ["We should review the numbers first."])
        self.assertEqual(self.store_texts(), ["We should review the numbers first."])


class AHeldRevisionReachesTheLedgerTest(_Case):
    def test_a_held_extend(self):
        """`_extend_committed_locked` with speech_final=False, committed later."""
        self.interim("We should review", 30.0, 31.0)
        self.timeout()
        self.final("the numbers", 31.1, 31.8, speech_final=False)
        self.final("first.", 31.9, 32.3, speech_final=True)
        self.assert_one_record_holding("We should review", "the numbers", "first.")
        self.assertEqual(ctl.get_action_counts().get("revise"), 1)

    def test_a_held_correction(self):
        """`_supersede_committed_locked` with speech_final=False, committed later."""
        self.interim("I will send you a", 13.28, 14.08)
        self.utterance_end()
        self.final("I will send you a lot", 13.28, 14.60, speech_final=False)
        self.final("today.", 14.70, 15.00, speech_final=True)
        self.assert_one_record_holding("I will send you a lot", "today.")
        self.assertEqual(ctl.get_action_counts().get("revise"), 1)


class ANewLineIsStillTrimmedTest(_Case):
    def test_a_resent_tail_is_removed_from_the_next_line(self):
        """Item 66 untouched: a new line that repeats the committed tail, on
        overlapping audio, loses the repeat -- those words are in the record
        before it."""
        self.interim("in Duterte, he writes openly, I never considered", 100.0, 104.0)
        self.utterance_end()
        self.interim("he writes openly, I never considered him an impostor", 102.0, 105.5)
        self.utterance_end()
        self.assertEqual(
            self.ledger_texts(),
            ["in Duterte, he writes openly, I never considered", "him an impostor"],
        )


if __name__ == "__main__":
    unittest.main()
