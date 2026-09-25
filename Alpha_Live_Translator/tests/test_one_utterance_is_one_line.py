"""One utterance is one line, however many times the provider revises it.

WHAT WAS BROKEN
---------------
The owner's run of 2026-09-25 16:40:43 exported one 1.2-second sentence as
three lines, all starting on the same audio:

    U-4  [13.28-14.08]  utterance_end            "I will send you a"
    U-5  [13.28-14.40]  sentence_boundary_flush  "I will send you both."
    U-6  [13.28-14.48]  utterance_end            "I'm sending you both."

and "Would I would" [69.76] / "I would I would, Why would those who are?"
[69.84] the same way. Replayed through the real app against a local Deepgram
stand-in with those exact messages, the sealed export reproduced all five lines.

Two mechanisms, one cause -- a later guess at the SAME audio treated as new
speech:

1. `UtteranceEnd` committed the held interim. Deepgram had not finalised the
   segment and went on revising it; the next interim became a new utterance,
   and item 66's trim needs the committed tail verbatim, which a revised last
   word ("a" -> "both") defeats.
2. Inside one utterance, "I will send you both." + "I'm sending you both." share
   too few words for `_merge_lexical`'s similarity gate (0.5 < 0.6), so it glued
   them, and the sentence flush split the glue into two records.

Deepgram's interims are cumulative per segment: two interims whose first words
start together are two guesses at the same audio. That -- the audio clock, not
the words -- is the identity used here.

WHAT THESE TESTS PIN
--------------------
* the owner's sequences each end as ONE line, committed as a supersede of the
  record the early commit wrote, so the ledger revises instead of appending
* genuinely different speech is untouched: a different start (two people saying
  "Good afternoon"), a sentence-flush tail, an utterance that absorbed a final,
  another channel
* an older, shorter guess at committed audio neither re-opens nor shrinks it
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import utterance_lifecycle as ul  # noqa: E402


class _Recorder:
    def __init__(self):
        self.commits = []

    def __call__(self, decision):
        self.commits.append(decision)

    @property
    def texts(self):
        return [d.text for d in self.commits]

    @property
    def utterances(self):
        return [d.utterance_id for d in self.commits]


class _Case(unittest.TestCase):
    def setUp(self):
        self.rec = _Recorder()
        self.life = ul.UtteranceLifecycleOwner(on_commit=self.rec)
        self.life.reset_for_session("sess-item35")
        self.n = 0

    def interim(self, text, start, end, *, channel=0, speaker=1):
        self.n += 1
        return self.life.on_interim(
            text=text, speaker=speaker, channel=channel, start=start, end=end,
            event_id=f"interim-{self.n}",
            metadata={"start_time": start, "end_time": end},
        )

    def final(self, text, start, end, *, speech_final=False, channel=0):
        self.n += 1
        return self.life.on_final_chunk(
            text=text, speaker=1, channel=channel, start=start, end=end,
            is_final=True, speech_final=speech_final, event_id=f"final-{self.n}",
            metadata={"start_time": start, "end_time": end},
        )

    def utterance_end(self, *, channel=0):
        self.n += 1
        return self.life.on_utterance_end(channel=channel, event_id=f"ue-{self.n}")

    def assert_one_line(self, expected_text):
        self.assertEqual(
            len(set(self.rec.utterances)), 1,
            "one utterance became several lines: %r" % (self.rec.texts,),
        )
        self.assertEqual(self.rec.texts[-1], expected_text)
        for later in self.rec.commits[1:]:
            self.assertEqual(
                later.decision, ul.SUPERSEDE_PREVIOUS,
                "a later commit of the same audio must supersede the record, "
                "or duplicate protection appends it as a new line",
            )
            self.assertTrue(later.should_supersede_committed)


class TheOwnersSequencesAreOneLineEachTest(_Case):
    def test_i_will_send_you_both(self):
        """16:40:59.545 .. 16:41:02.08, as recorded."""
        self.interim("I will send you a", 13.28, 14.08)
        self.utterance_end()
        self.interim("I will send you both.", 13.28, 14.40)
        self.interim("I will send you both.", 13.28, 14.40)
        self.interim("I'm sending you both.", 13.28, 14.48)
        self.utterance_end()
        self.assert_one_line("I'm sending you both.")
        self.assertEqual(self.life.stats()["committed_segments_reopened"], 1)
        self.assertEqual(self.life.stats()["same_segment_hypotheses_replaced"], 1)

    def test_would_i_would(self):
        """The first word re-timed by 0.08 s is still the same audio."""
        self.interim("Would I would", 69.76, 70.48)
        self.utterance_end()
        self.interim("I would I would,", 69.84, 71.20)
        self.interim("I would I would, Why would those who are?", 69.84, 74.01)
        self.utterance_end()
        self.assert_one_line("I would I would, Why would those who are?")

    def test_the_inactivity_timeout_is_an_early_commit_too(self):
        self.interim("Why? Why would", 66.95, 67.9)
        self.life.on_timeout(token=self.life._timeout_token)
        self.interim("Why? Why would I work?", 66.95, 68.63)
        self.utterance_end()
        self.assert_one_line("Why? Why would I work?")


class DifferentSpeechIsNeverMergedTest(_Case):
    def test_two_people_saying_good_afternoon(self):
        """Owner's run U-1/U-2: different speakers, 3.1 s apart. Two lines."""
        self.interim("Good afternoon.", 2.72, 3.36, speaker=2)
        self.utterance_end()
        self.interim("Good afternoon. How are you?", 6.46, 7.58, speaker=1)
        self.utterance_end()
        self.assertEqual(self.rec.texts, ["Good afternoon.", "Good afternoon. How are you?"])
        self.assertEqual(len(set(self.rec.utterances)), 2)
        self.assertEqual(self.life.stats()["committed_segments_reopened"], 0)

    def test_a_sentence_flush_tail_is_not_a_revision_of_its_head(self):
        """The tail starts where the window starts -- it is the rest of it."""
        head = (
            "Alpha is ready for the client meeting this morning. "
            "We checked every single report twice and fixed all of the numbers."
        )
        self.interim(head, 50.0, 55.0)
        self.interim(head + " Now we can", 50.0, 56.0)
        self.interim(head + " Now we can start.", 50.0, 56.5)
        self.utterance_end()
        self.assertEqual(self.rec.texts, [head, "Now we can start."])
        self.assertEqual(len(set(self.rec.utterances)), 2)
        self.assertNotEqual(self.rec.commits[1].decision, ul.SUPERSEDE_PREVIOUS)

    def test_a_committed_flush_tail_is_never_reopened_with_its_head(self):
        """After the tail commits, the window it came from still starts at the
        same instant -- and still carries the head. Re-opening the tail with
        it would write the head into a second record."""
        head = (
            "Alpha is ready for the client meeting this morning. "
            "We checked every single report twice and fixed all of the numbers."
        )
        self.interim(head, 50.0, 55.0)
        self.interim(head + " Now we can", 50.0, 56.0)
        self.interim(head + " Now we can start.", 50.0, 56.5)
        self.utterance_end()
        self.interim(head + " Now we can start today.", 50.0, 57.0)
        self.utterance_end()
        self.assertEqual(self.life.stats()["committed_segments_reopened"], 0)
        self.assertEqual(self.rec.texts[:2], [head, "Now we can start."])

    def test_an_utterance_that_absorbed_a_final_is_never_reopened(self):
        """A finalised span is never re-sent; its start matching is chance."""
        self.final("Okay.", 20.0, 20.2)
        self.interim("Okay. so we", 20.0, 20.9)
        self.utterance_end()
        self.interim("so we can go.", 20.2, 21.4)
        self.utterance_end()
        self.assertEqual(self.life.stats()["committed_segments_reopened"], 0)
        self.assertIn("Okay.", self.rec.texts[0])

    def test_another_channel_is_other_audio(self):
        self.interim("I will send you a", 13.28, 14.08, channel=0)
        self.utterance_end(channel=0)
        self.interim("I will send you both.", 13.28, 14.40, channel=1)
        self.utterance_end(channel=1)
        self.assertEqual(len(set(self.rec.utterances)), 2)
        self.assertEqual(self.life.stats()["committed_segments_reopened"], 0)


class AnOlderGuessNeverShrinksTheRecordTest(_Case):
    def test_a_shorter_resend_is_ignored(self):
        self.interim("I will send you both.", 13.28, 14.40)
        self.utterance_end()
        decision = self.interim("I will send", 13.28, 13.9)
        self.utterance_end()
        self.assertEqual(decision.decision, ul.IGNORE_DUPLICATE)
        self.assertEqual(self.rec.texts, ["I will send you both."])


if __name__ == "__main__":
    unittest.main()
