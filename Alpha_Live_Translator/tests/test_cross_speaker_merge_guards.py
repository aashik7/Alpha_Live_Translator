"""Regression tests for problem C — `CLIENT_DELIVERY_SPRINT_v5.md` items 22,
23 and 33s: two speakers' turns merging into one line.

* **22** — `_compatible_with_active_locked` compared
  `int(active.speaker or 1) != int(speaker or 1)`, so every *unknown* speaker
  coerced to `1` and two unidentified speakers compared equal. Fail-open.
* **23** — Case B forced a new utterance only when the active one was already
  committed, so an incompatible candidate (different speaker or channel)
  merged into a held, uncommitted utterance. Case C had always gated correctly.
* **33s** (scoped) — `_resolve_output_speaker` is a *display* stabiliser that
  relabels toward the previous speaker when evidence is weak, and one of its
  own lock reasons is "this text reads like a continuation". Feeding its output
  into the boundary decision closes a loop where the relabel manufactures the
  same-speaker agreement the guard exists to test for.

`SameSpeakerStillMergesTest` is the counterweight: these guards make merging
*stricter*, and over-tightening would fragment one speaker's continuous speech
into many short lines — trading a visible-on-stage defect for a different one.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    UtteranceLifecycleOwner,
    _known_speaker,
)


def _owner() -> UtteranceLifecycleOwner:
    owner = UtteranceLifecycleOwner(host=None, commit_fallback_ms=99999)
    owner.reset_for_session("sess-cross-speaker")
    return owner


def _final(owner, text, speaker, event_id, speech_final=False, channel=0):
    return owner.on_final_chunk(
        text=text,
        speaker=speaker,
        channel=channel,
        start=None,
        end=None,
        is_final=True,
        speech_final=speech_final,
        event_id=event_id,
        metadata={},
    )


def _active_text(owner) -> str:
    return str(owner._active.text if owner._active else "")


class UnknownSpeakersDoNotMergeTest(unittest.TestCase):
    """Item 22."""

    def test_two_unknown_speakers_do_not_merge(self):
        owner = _owner()
        _final(owner, "the first speaker said this", None, "e1")
        _final(owner, "a totally different remark", None, "e2")
        text = _active_text(owner)
        self.assertIn("totally different", text)
        self.assertNotIn(
            "first speaker",
            text,
            f"two unidentified speakers were merged into one line: {text!r}",
        )

    def test_speaker_zero_is_treated_as_unknown_not_as_a_match(self):
        """`speakers_confirmed_same` alone would read `0 == 0` as confirmed;
        the unknown forms have to collapse to None first."""
        owner = _owner()
        _final(owner, "one person speaking now", 0, "e1")
        _final(owner, "somebody else entirely", 0, "e2")
        self.assertNotIn("one person", _active_text(owner))

    def test_known_speaker_never_matches_an_unknown_one(self):
        owner = _owner()
        _final(owner, "identified speaker talking", 1, "e1")
        _final(owner, "unidentified voice here", None, "e2")
        self.assertNotIn("identified speaker talking", _active_text(owner))


class CaseBHonoursSpeakerBoundaryTest(unittest.TestCase):
    """Item 23 — the held-final-chunk path."""

    def test_speaker_change_on_an_uncommitted_utterance_does_not_merge(self):
        owner = _owner()
        _final(owner, "speaker one is talking here", 1, "e1")
        _final(owner, "speaker two interrupts now", 2, "e2")
        text = _active_text(owner)
        self.assertIn("speaker two", text)
        self.assertNotIn(
            "speaker one",
            text,
            f"Case B merged across a speaker boundary: {text!r}",
        )

    def test_channel_change_on_an_uncommitted_utterance_does_not_merge(self):
        owner = _owner()
        _final(owner, "left channel content", 1, "e1", channel=0)
        _final(owner, "right channel content", 1, "e2", channel=1)
        self.assertNotIn("left channel", _active_text(owner))


class SameSpeakerStillMergesTest(unittest.TestCase):
    """The counterweight. Without this, items 22/23 would simply trade a
    cross-speaker merge for transcript fragmentation."""

    def test_one_speaker_continuous_speech_still_merges(self):
        owner = _owner()
        _final(owner, "although it was raining heavily", 1, "e1")
        _final(owner, "we decided to stay inside", 1, "e2")
        text = _active_text(owner)
        self.assertIn("raining heavily", text)
        self.assertIn("stay inside", text)

    def test_same_speaker_merge_survives_three_chunks(self):
        owner = _owner()
        _final(owner, "first part of the sentence", 2, "e1")
        _final(owner, "second part continues", 2, "e2")
        _final(owner, "and the third part ends it", 2, "e3")
        text = _active_text(owner)
        for fragment in ("first part", "second part", "third part"):
            self.assertIn(fragment, text, f"fragmented at {fragment!r}: {text!r}")


class KnownSpeakerNormalisationTest(unittest.TestCase):
    def test_none_is_unknown(self):
        self.assertIsNone(_known_speaker(None))

    def test_zero_is_unknown(self):
        self.assertIsNone(_known_speaker(0))

    def test_empty_string_is_unknown(self):
        self.assertIsNone(_known_speaker(""))

    def test_non_numeric_is_unknown(self):
        self.assertIsNone(_known_speaker("nobody"))

    def test_real_speaker_survives(self):
        self.assertEqual(2, _known_speaker(2))
        self.assertEqual(1, _known_speaker("1"))


class BoundarySpeakerIsRawNotRelabelledTest(unittest.TestCase):
    """Item 33s. The Japanese assembler must hand the revision authority the
    raw provider label, not the display-stabilised one."""

    def test_publish_sentence_passes_the_raw_speaker_to_the_decision(self):
        import inspect

        from alpha.transcription import japanese_sentence_assembler as jsa

        source = inspect.getsource(jsa.JapaneseContinuityAssembler._publish_sentence)
        self.assertIn(
            "candidate_speaker=boundary_speaker",
            source,
            "the revision decision is being given the relabelled speaker again",
        )
        self.assertLess(
            source.index("boundary_speaker = "),
            source.index("self._resolve_output_speaker("),
            "boundary_speaker must be captured BEFORE the display relabel",
        )


if __name__ == "__main__":
    unittest.main()
