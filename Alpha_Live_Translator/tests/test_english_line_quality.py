"""Regression tests for CLIENT_DELIVERY_SPRINT_v5.md items 64 and 65.

Both defects come from the same live run (`...20260812-095935`) and the human
reference transcript the user supplied for it, and both are ours -- each one
reproduces by calling the real production code, not by reading a log.

**Item 64 -- a one-token re-send is emitted twice.** `_overlap_join` requires
`k >= 2` and `_tail_resend_splice` requires `min_run=3`, so a Deepgram window
that slides back by exactly one token matches neither and reaches
`_merge_lexical`'s concatenation branch, which joins with `f"{prev}, {curr}"`.
The comma is the fingerprint. Against the reference transcript:

    reference: "and other schools of Muslim or Muhammadan law"
    exported:  "and other schools, schools of Muslim or Muhammadan law"

    reference: "other heretics have attributed to Jesus Christ"
    exported:  "other heretics have, have attributed to Jesus Christ"

    reference: "not analyzing objectively claiming the falsehood"
    exported:  "not analyzing objectively. Claiming, Claiming the falsehood"

The k=1 case is not simply a looser k>=2 and must not be treated as one: real
English doubles words across a clause boundary, and collapsing those deletes
speech -- worse, by this module's own stated ordering, than the duplication
being fixed. So the collapse is gated on the two spans overlapping on the audio
clock, which a re-send does and a continuation does not. The same run's speaker
genuinely stutters ("What What is", "I I I"); those arrive inside one provider
payload, never at a seam, so nothing here can reach them.

**Item 65 -- one exported line held 25 sentences.** English has no boundary
stabilizer; it relies on Deepgram `speech_final`, which continuous speech never
produces. Deepgram was configured correctly for that run (endpointing=1200,
utterance_end_ms=1500) -- the speaker simply never paused 1.2s. One utterance
absorbed 45 seconds across 187 revisions and exported as a single 2445-char
line; 16 of the run's 41 lines were over 400 chars. The fix commits at a
finished sentence, but only where the merge was a pure append, so no revision
path can be split mid-correction.

Item 65 is tested through the real `UtteranceLifecycleOwner` rather than the helper
alone: the claim is about caller behaviour (does the finished text get
published, or abandoned?), which a pure-function test cannot settle.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha import constants  # noqa: E402
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    _PREMATURE_COMMIT_REASONS,
    _audio_spans_overlap,
    _merge_lexical,
    UtteranceLifecycleOwner,
)


class BoundaryTokenCollapseTest(unittest.TestCase):
    """Item 64 -- the three mismatches measured against the reference."""

    def test_schools_schools_matches_the_reference(self):
        merged = _merge_lexical(
            "And he says, the interpreters of the Quran and other schools",
            "schools of Muslim or Muhammadan law have attributed",
            audio_overlaps=True,
        )
        self.assertNotIn("schools, schools", merged)
        self.assertIn("other schools of Muslim or Muhammadan law", merged)

    def test_have_have_matches_the_reference(self):
        merged = _merge_lexical(
            "all the praises that Aryans, politicians, or colonists and other heretics have",
            "have attributed to Jesus Christ",
            audio_overlaps=True,
        )
        self.assertNotIn("have, have", merged)
        self.assertIn("heretics have attributed to Jesus Christ", merged)

    def test_claiming_claiming_matches_the_reference(self):
        merged = _merge_lexical(
            "1st point is already not analyzing objectively. Claiming",
            "Claiming the falsehood of this doctrine.",
            audio_overlaps=True,
        )
        self.assertNotIn("Claiming, Claiming", merged)
        self.assertIn("Claiming the falsehood", merged)

    def test_a_genuine_english_double_survives_a_continuation(self):
        """The reason this is gated instead of unconditional.

        "He said that" + "that was fine" is two spoken words. Collapsing it
        deletes speech, which is the failure direction this module ranks worst.
        """
        merged = _merge_lexical("He said that", "that was fine.")
        self.assertIn("that, that", merged)

    def test_default_is_no_collapse(self):
        """Any caller without timing keeps the pre-item-64 behaviour."""
        self.assertEqual(
            _merge_lexical("we reached the summit", "summit was cold"),
            "we reached the summit, summit was cold",
        )

    def test_multi_token_overlap_still_goes_through_overlap_join(self):
        """k>=2 returns before the k=1 path; this must not change it."""
        merged = _merge_lexical(
            "the best bodybuilder in",
            "bodybuilder in the world, and I didn't",
            audio_overlaps=True,
        )
        self.assertEqual(merged, "the best bodybuilder in the world, and I didn't")

    def test_curr_adding_nothing_returns_prev(self):
        self.assertEqual(
            _merge_lexical("we reached the summit", "summit", audio_overlaps=True),
            "we reached the summit",
        )


class AudioSpanOverlapTest(unittest.TestCase):
    """The evidence the collapse rests on -- fail closed without it."""

    def test_resend_overlaps(self):
        # Real values from run ...095935 raw-000002 / raw-000003.
        self.assertTrue(_audio_spans_overlap(53.31, 56.75, 53.390003, 61.879997))

    def test_adjacent_spans_do_not_overlap(self):
        self.assertFalse(_audio_spans_overlap(10.0, 12.0, 12.0, 14.0))

    def test_missing_timing_is_not_an_overlap(self):
        self.assertFalse(_audio_spans_overlap(-1.0, -1.0, -1.0, -1.0))
        self.assertFalse(_audio_spans_overlap(10.0, 12.0, -1.0, 14.0))
        self.assertFalse(_audio_spans_overlap(-1.0, -1.0, 11.0, 14.0))

    def test_float_noise_is_not_an_overlap(self):
        """Two spans that merely abut must not read as a re-send."""
        self.assertFalse(_audio_spans_overlap(10.0, 12.0, 11.999, 14.0))


class _Recorder:
    def __init__(self) -> None:
        self.commits: list[str] = []

    def __call__(self, decision) -> None:
        self.commits.append(decision.text)


class SentenceBoundaryFlushTest(unittest.TestCase):
    """Item 65 -- driven through the real lifecycle, not the helper.

    REOPENED 2026-08-12. The flush is gated OFF by
    `ENGLISH_SENTENCE_FLUSH_ENABLED`: on live run `...142447` it fired 8 times
    and only 1 of the 9 committed utterances reached the export -- the one
    commit that did NOT come from the flush. These tests force the flag on so
    the mechanism stays pinned while it is disabled in production; the class
    below pins that production default.
    """

    def setUp(self):
        patcher = patch.object(constants, "ENGLISH_SENTENCE_FLUSH_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _lifecycle(self):
        recorder = _Recorder()
        life = UtteranceLifecycleOwner(on_commit=recorder)
        life.reset_for_session("session-item-65")
        return life, recorder

    def _final(self, life, text, *, start, end, event_id):
        return life.on_final_chunk(
            text=text,
            speaker=1,
            channel=0,
            start=start,
            end=end,
            is_final=True,
            speech_final=False,
            event_id=event_id,
            metadata={"start_time": start, "end_time": end},
        )

    def test_a_finished_sentence_is_published_instead_of_accumulating(self):
        life, recorder = self._lifecycle()
        self._final(life, "First sentence is complete.", start=0.0, end=2.0, event_id="e1")
        self._final(life, "Second sentence starts here.", start=2.1, end=4.0, event_id="e2")
        self.assertEqual(recorder.commits, ["First sentence is complete."])

    def test_the_new_speech_is_not_lost_by_the_flush(self):
        life, recorder = self._lifecycle()
        self._final(life, "First sentence is complete.", start=0.0, end=2.0, event_id="e1")
        self._final(life, "Second sentence starts here.", start=2.1, end=4.0, event_id="e2")
        life.on_utterance_end(event_id="e3", channel=0)
        self.assertEqual(
            recorder.commits,
            ["First sentence is complete.", "Second sentence starts here."],
        )

    def test_three_sentences_produce_three_lines_not_one_paragraph(self):
        life, recorder = self._lifecycle()
        self._final(life, "The budget was approved.", start=0.0, end=1.0, event_id="e1")
        self._final(life, "Nobody objected to it.", start=1.1, end=2.0, event_id="e2")
        self._final(life, "We move on now.", start=2.1, end=3.0, event_id="e3")
        life.on_utterance_end(event_id="e4", channel=0)
        self.assertEqual(
            recorder.commits,
            ["The budget was approved.", "Nobody objected to it.", "We move on now."],
        )
        self.assertTrue(all(len(t) < 400 for t in recorder.commits))

    def test_a_revision_is_never_split(self):
        """The narrow trigger: only a pure append across a terminator."""
        life, recorder = self._lifecycle()
        self._final(life, "I am very happy.", start=0.0, end=2.0, event_id="e1")
        self._final(life, "I am very happy today.", start=0.0, end=2.4, event_id="e2")
        self.assertEqual(recorder.commits, [])

    def test_a_mid_sentence_chunk_is_never_split(self):
        life, recorder = self._lifecycle()
        self._final(life, "I went to the shop and", start=0.0, end=2.0, event_id="e1")
        self._final(life, "bought some bread.", start=2.1, end=4.0, event_id="e2")
        self.assertEqual(recorder.commits, [])

    def test_flush_reason_is_not_treated_as_a_premature_commit(self):
        """If it were, the next chunk would extend the flushed record and glue
        the long line straight back together."""
        self.assertNotIn("sentence_boundary_flush", _PREMATURE_COMMIT_REASONS)

    def test_flush_is_counted(self):
        life, _ = self._lifecycle()
        self._final(life, "First sentence is complete.", start=0.0, end=2.0, event_id="e1")
        self._final(life, "Second sentence starts here.", start=2.1, end=4.0, event_id="e2")
        self.assertEqual(life.stats().get("sentence_boundary_flushes"), 1)


class FlushIsEnabledInProductionTest(unittest.TestCase):
    """Item 65's flush is ON again as of 2026-08-12.

    It was disabled after run ...142447 lost 8 of 9 committed utterances. The
    cause turned out to be outside the flush: every flush commit carried
    `is_final: False` inherited from its triggering event, and
    `_display_transcript_item` drops those silently. The survivor was the one
    commit whose metadata had no `is_final` key at all. See
    tests/test_committed_segment_is_final.py.
    """

    def test_flag_defaults_on(self):
        self.assertTrue(constants.ENGLISH_SENTENCE_FLUSH_ENABLED)

    def test_a_finished_sentence_commits_with_the_production_default(self):
        recorder = _Recorder()
        life = UtteranceLifecycleOwner(on_commit=recorder)
        life.reset_for_session("session-default")
        for i, (text, start, end) in enumerate(
            [("First sentence is complete.", 0.0, 2.0),
             ("Second sentence starts here.", 2.1, 4.0)], 1
        ):
            life.on_final_chunk(
                text=text, speaker=1, channel=0, start=start, end=end,
                is_final=True, speech_final=False, event_id=f"d{i}",
                metadata={"start_time": start, "end_time": end},
            )
        self.assertEqual(recorder.commits, ["First sentence is complete."])
        self.assertEqual(life.stats().get("sentence_boundary_flushes"), 1)


if __name__ == "__main__":
    unittest.main()
