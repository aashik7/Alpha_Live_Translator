"""Item 66: a mid-sentence commit stores its tail twice.

`utterance_end` and `speech_final` mean *speech* paused, not that a *sentence*
finished, so a speaker who pauses mid-clause gets committed there. The provider
then re-sends that span in its next window -- but the previous utterance is
already committed, and `_merge_lexical`'s overlap machinery (`_overlap_join`,
`_tail_resend_splice`) only ever runs WITHIN one active utterance. Nothing
dedupes across the boundary, so the tail lands in two records.

Measured on run `...20260812-161651`, 5 of its 7 exported records:

    [0] "...the only 1 I'm familiar with, by Goethe. central work of"
    [1] "central work of the European enlightenment, ..."

    [5] "...in Duterte, he writes openly, I never considered"
    [6] "he writes openly, I never considered him an impostor at all"

The fix trims the new utterance's head, never the committed record. That
matters: the alternative -- routing these through `_extend_committed_locked` so
the committed record absorbs the continuation -- was implemented and measured,
and it produces the correct merged text in the lifecycle AND the store while
the canonical write is skipped as `already_committed`, leaving the LEDGER
holding the truncated record. The export reads the ledger, so that approach
truncates the transcript. Trimming the new utterance revises nothing and cannot
reach an existing ledger record.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import utterance_lifecycle as ul  # noqa: E402
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    _ends_a_sentence,
    _strip_committed_tail_prefix,
)


class StripCommittedTailPrefixTest(unittest.TestCase):
    """The pure function. Real pairs from run ...161651."""

    def test_real_pair_5_6(self):
        self.assertEqual(
            _strip_committed_tail_prefix(
                "1, While in Duterte, he writes openly, I never considered",
                "he writes openly, I never considered him an impostor at all",
            ),
            "him an impostor at all",
        )

    def test_real_pair_0_1(self):
        self.assertEqual(
            _strip_committed_tail_prefix(
                "the only 1 I'm familiar with, by Goethe. central work of",
                "central work of the European enlightenment, one of them",
            ),
            "the European enlightenment, one of them",
        )

    def test_a_two_word_echo_is_not_enough(self):
        """min_run=3: a short coincidental echo across a real boundary must
        not delete speech."""
        self.assertIsNone(
            _strip_committed_tail_prefix("we went to the shop", "the shop was closed")
        )

    def test_unrelated_text_is_untouched(self):
        self.assertIsNone(
            _strip_committed_tail_prefix(
                "the budget was approved today", "nobody objected to it at all"
            )
        )

    def test_a_full_repeat_leaves_nothing_and_returns_none(self):
        """Returning None means "keep the caller's text" -- an empty utterance
        would be worse than a duplicate."""
        self.assertIsNone(
            _strip_committed_tail_prefix("one two three four", "one two three four")
        )

    def test_short_inputs_are_ignored(self):
        self.assertIsNone(_strip_committed_tail_prefix("a b", "a b c"))

    def test_empty_inputs(self):
        self.assertIsNone(_strip_committed_tail_prefix("", "anything at all here"))
        self.assertIsNone(_strip_committed_tail_prefix("anything at all here", ""))


class EndsASentenceTest(unittest.TestCase):
    def test_terminators(self):
        for text in ("done.", "really?", "stop!", 'he said "yes."'):
            self.assertTrue(_ends_a_sentence(text), text)

    def test_mid_sentence(self):
        for text in ("central work of", "abandonment, So an", "I never considered"):
            self.assertFalse(_ends_a_sentence(text), text)


class _Recorder:
    def __init__(self):
        self.commits = []

    def __call__(self, decision):
        self.commits.append(decision.text)


class ThroughTheLifecycleTest(unittest.TestCase):
    PRE = "1, While in Duterte, he writes openly, I never considered"
    POST = "he writes openly, I never considered him an impostor at all"

    def _run(self, *, prev_reason_speech_final=True, speaker2=1):
        rec = _Recorder()
        life = ul.UtteranceLifecycleOwner(on_commit=rec)
        life.reset_for_session("sess-66")
        life.on_final_chunk(
            text=self.PRE, speaker=1, channel=0, start=10.0, end=14.0,
            is_final=True, speech_final=prev_reason_speech_final, event_id="a1",
            metadata={"start_time": 10.0, "end_time": 14.0},
        )
        life.on_final_chunk(
            text=self.POST, speaker=speaker2, channel=0, start=13.5, end=17.0,
            is_final=True, speech_final=False, event_id="b1",
            metadata={"start_time": 13.5, "end_time": 17.0},
        )
        life.on_utterance_end(event_id="end", channel=0)
        return life, rec

    def test_the_tail_is_not_committed_twice(self):
        _, rec = self._run()
        joined = " ".join(rec.commits)
        self.assertEqual(
            joined.count("I never considered"),
            1,
            f"committed tail appears twice: {rec.commits}",
        )

    def test_no_words_are_lost(self):
        _, rec = self._run()
        joined = " ".join(rec.commits)
        for tail_word in ("Duterte", "impostor", "openly"):
            self.assertIn(tail_word, joined)

    def test_it_is_counted(self):
        life, _ = self._run()
        self.assertEqual(life.stats().get("resent_tails_trimmed"), 1)

    def test_a_different_speaker_is_never_trimmed(self):
        """A speaker change is a hard boundary; an overlap there is coincidence."""
        life, rec = self._run(speaker2=2)
        self.assertEqual(life.stats().get("resent_tails_trimmed"), 0)
        self.assertIn(self.POST, rec.commits)


class DisconnectIsNeverTrimmedTest(unittest.TestCase):
    """Words either side of a network hole are unrelated, so an apparent
    overlap there must not delete anything."""

    def test_provider_disconnected_is_a_hard_boundary(self):
        self.assertIn("provider_disconnected", ul._HARD_BOUNDARY_COMMIT_REASONS)

    def test_nothing_is_trimmed_after_an_in_flight_commit(self):
        rec = _Recorder()
        life = ul.UtteranceLifecycleOwner(on_commit=rec)
        life.reset_for_session("sess-66-drop")
        life.on_final_chunk(
            text="he writes openly, I never considered", speaker=1, channel=0,
            start=10.0, end=14.0, is_final=True, speech_final=False, event_id="a1",
            metadata={"start_time": 10.0, "end_time": 14.0},
        )
        life.commit_in_flight(reason="provider_disconnected")
        life.on_final_chunk(
            text="he writes openly, I never considered him an impostor", speaker=1,
            channel=0, start=0.4, end=3.0, is_final=True, speech_final=False,
            event_id="b1", metadata={"start_time": 0.4, "end_time": 3.0},
        )
        life.on_utterance_end(event_id="end", channel=0)
        self.assertEqual(life.stats().get("resent_tails_trimmed"), 0)


if __name__ == "__main__":
    unittest.main()
