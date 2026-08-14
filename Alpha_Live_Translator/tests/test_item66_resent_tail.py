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

    def _run(self, *, prev_reason_speech_final=True, speaker2=1, cand_start=13.5):
        rec = _Recorder()
        life = ul.UtteranceLifecycleOwner(on_commit=rec)
        life.reset_for_session("sess-66")
        life.on_final_chunk(
            text=self.PRE, speaker=1, channel=0, start=10.0, end=14.0,
            is_final=True, speech_final=prev_reason_speech_final, event_id="a1",
            metadata={"start_time": 10.0, "end_time": 14.0},
        )
        life.on_final_chunk(
            text=self.POST, speaker=speaker2, channel=0, start=cand_start, end=17.0,
            is_final=True, speech_final=False, event_id="b1",
            metadata={"start_time": cand_start, "end_time": 17.0},
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

    def test_a_different_speaker_with_no_audio_overlap_is_never_trimmed(self):
        """Problem C's guard. A genuine speaker change does not produce
        overlapping audio, so with the spans disjoint the speaker gate is the
        only rule and must refuse.

        `cand_start=14.5` matters: the original version of this test used 13.5,
        which OVERLAPS the previous span, so once overlapping audio became an
        accepted alternative to the speaker match it was asserting the opposite
        of what it describes."""
        life, rec = self._run(speaker2=2, cand_start=14.5)
        self.assertEqual(life.stats().get("resent_tails_trimmed"), 0)
        self.assertIn(self.POST, rec.commits)


class DiarizationArtifactIsStillTrimmedTest(unittest.TestCase):
    """Live run ...20260814-101813 records [4]/[5].

    They share the re-sent run "and the number 1 thing", but the provider
    labelled them speaker 2 and speaker 1, so the same-speaker gate refused and
    the duplicate reached the export. Their audio spans were 131.56-138.24 and
    136.32-160.48 -- overlapping by ~1.9s, i.e. the same audio arriving twice,
    which makes the label disagreement a diarization artifact rather than two
    people. Overlapping audio is therefore accepted as an alternative to the
    speaker match, exactly as item 64 already does.
    """

    PRE = "many, many things there. Bone strength, and the number 1 thing"
    POST = "And the number 1 thing that I hear from people is, I don't really have time"

    def _run(self, *, cand_start):
        rec = _Recorder()
        life = ul.UtteranceLifecycleOwner(on_commit=rec)
        life.reset_for_session("sess-66-diar")
        life.on_final_chunk(
            text=self.PRE, speaker=2, channel=0, start=131.56, end=138.24,
            is_final=True, speech_final=True, event_id="a1",
            metadata={"start_time": 131.56, "end_time": 138.24},
        )
        life.on_final_chunk(
            text=self.POST, speaker=1, channel=0, start=cand_start, end=160.48,
            is_final=True, speech_final=False, event_id="b1",
            metadata={"start_time": cand_start, "end_time": 160.48},
        )
        life.on_utterance_end(event_id="end", channel=0)
        return life, rec

    def test_overlapping_audio_trims_despite_the_speaker_label(self):
        life, rec = self._run(cand_start=136.32)   # the real value
        self.assertEqual(life.stats().get("resent_tails_trimmed"), 1)
        joined = " ".join(rec.commits)
        self.assertEqual(joined.lower().count("the number 1 thing"), 1)

    def test_a_real_speaker_change_is_still_protected(self):
        """No audio overlap means the speaker gate is the only rule, and it
        must still refuse -- this is problem C's guard."""
        life, rec = self._run(cand_start=139.00)   # starts after PRE ended
        self.assertEqual(life.stats().get("resent_tails_trimmed"), 0)
        self.assertTrue(any(c.startswith("And the number 1 thing") for c in rec.commits))


class TrimSurvivesCumulativeResendsTest(unittest.TestCase):
    """Run ...20260814-112633: the trim fired and the duplicate came back.

    `RESENT_TAIL_TRIMMED` logged `removed_preview: "The 3rd 1"`, yet the
    exported record still began with it. Deepgram re-sends its window
    CUMULATIVELY, so the next chunk of that utterance carried the head again,
    `_merge_lexical` took it wholesale, and the trim was undone -- that
    utterance reached source_version 8.

    Trimming after every merge is idempotent: `_strip_committed_tail_prefix`
    matches only at the head, so once removed it returns None, while a
    cumulative re-send that reintroduces it is trimmed again.
    """

    def _run(self):
        rec = _Recorder()
        life = ul.UtteranceLifecycleOwner(on_commit=rec)
        life.reset_for_session("sess-66-cumulative")
        life.on_final_chunk(
            text="The 3rd 1", speaker=2, channel=0, start=70.2, end=70.84,
            is_final=True, speech_final=True, event_id="a",
            metadata={"start_time": 70.2, "end_time": 70.84},
        )
        for i, txt in enumerate([
            "The 3rd 1 is I've never met",
            "The 3rd 1 is I've never met anybody that wants to do 1",
            "The 3rd 1 is I've never met anybody that wants to do 1 after the 3rd 1.",
        ], 1):
            life.on_final_chunk(
                text=txt, speaker=1, channel=0, start=69.88, end=76.0 + i,
                is_final=True, speech_final=False, event_id=f"b{i}",
                metadata={"start_time": 69.88, "end_time": 76.0 + i},
            )
        life.on_utterance_end(event_id="e", channel=0)
        return life, rec

    def test_the_head_does_not_come_back(self):
        _, rec = self._run()
        self.assertFalse(
            any(c.startswith("The 3rd 1 is") for c in rec.commits),
            f"cumulative re-send restored the trimmed head: {rec.commits}",
        )

    def test_the_committed_short_record_is_still_there(self):
        _, rec = self._run()
        self.assertIn("The 3rd 1", rec.commits)

    def test_a_later_mid_text_recurrence_is_kept(self):
        """Only the HEAD is trimmed -- the same words later are real speech."""
        _, rec = self._run()
        self.assertTrue(any("after the 3rd 1" in c for c in rec.commits))

    def test_trimming_is_idempotent_not_runaway(self):
        life, rec = self._run()
        self.assertGreaterEqual(life.stats().get("resent_tails_trimmed"), 1)
        joined = " ".join(rec.commits)
        self.assertIn("never met anybody", joined)


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
