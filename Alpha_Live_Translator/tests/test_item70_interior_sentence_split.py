"""Item 70: split the accumulated English utterance at an INTERIOR terminator.

WHY THE EARLIER GATE WAS NOT ENOUGH
-----------------------------------
`_flush_sentence_boundary_locked` splits only at the seam between the previous
text and the incoming chunk, and only when the previous text already ends on a
terminator. Measured on the real recorded runs, **68% of Deepgram's finals do
not end on a terminator**, so the sentence boundary usually sits in the MIDDLE
of the accumulated text where that gate never looks.

Driven against the real lifecycle before this change, re-growing the 25 longest
real English records as cumulative windows: 25 records in, **25 out**, longest
**1982** characters, all 25 still over 400. The flush never fired once.

WHAT THE INTERIOR SPLIT REQUIRES, AND WHY EACH CONDITION EXISTS
---------------------------------------------------------------
A prefix of the accumulated text may be committed only when all hold:

1. it ends on `.!?` followed by a space, with non-empty text after it;
2. `prefix + " " + tail == merged` byte-for-byte -- a split, never a rewrite;
3. `curr.startswith(prefix)` -- the provider's OWN latest text still carries
   this completed sentence. This is what refuses the stale-terminator shape
   below, and it is the condition the previous attempt lacked;
4. `prev.startswith(prefix)` -- it survived a full merge cycle verbatim. This
   generalises the original gate's "verbatim survival proves the sentence is
   settled" guarantee from the seam to the interior.

THE STALE TERMINATOR (condition 3 is the only thing that catches it)
--------------------------------------------------------------------
`_merge_lexical` has a separate, pre-existing defect recorded in v5 §9:

    prev   'I am very happy.'
    curr   'I am very happy today.'   <- the provider WITHDREW the period
    merged 'I am very happy. today.'  <- the merge kept it anyway

`merged` contains an interior terminator that the provider has already
retracted. Conditions 1, 2 and 4 all pass on it. Only condition 3 fails, because
`curr` reads "happy today" where the prefix demands "happy.".

THE TWO TRAPS THAT KILLED THE PREVIOUS ATTEMPT
----------------------------------------------
Both are fixed here rather than avoided, and both are pinned below.

DUPLICATION -- after a commit the next cumulative window carries the committed
sentence back in. Item 66's `_strip_committed_tail_prefix` is fuzzy and bounded
at `min_run=3`, so 1-2 token sentences ("Yes.", "Okay.") slipped through and
committed twice. The split now records the exact committed prefix and removes it
from the next window by byte-exact string match, so no token threshold applies.

CONTENT LOSS -- the tail was handed to `_trim_resent_tail_locked` through
`force_new=True`. That trim exists for text arriving straight from the wire that
repeats an already-committed tail; a split tail is not that, it is text this
module already held and has just proven equal to `merged[len(prefix):]`. Running
it there deleted the head of an ordinary anadiplosis. The split now skips it, on
the trim's own stated contract.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    reset_utterance_lifecycle,
)


class _Host:
    _listen_language = "en"

    def __init__(self):
        self.published: list[str] = []

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        self.published.append(text)
        return True


def _drive(chunks, tag):
    """Every chunk shares one start time, which is what makes a cumulative
    window timing-compatible with the active utterance instead of a new one."""
    host = _Host()
    owner = reset_utterance_lifecycle(host, session_id=f"item70-split-{tag}")
    for i, chunk in enumerate(chunks):
        owner.on_final_chunk(
            text=chunk,
            speaker=1,
            channel=0,
            start=0.0,
            end=(i + 1) * 1.2,
            is_final=True,
            speech_final=False,
            event_id=f"e{i}",
            metadata={"channel_index": 0},
            deepgram_request_id=f"r{i}",
        )
    active = owner._active
    return host.published, (active.text if active else "")


def _cumulative(text, step=4):
    words = text.split()
    out = [" ".join(words[:i]) for i in range(step, len(words), step)]
    out.append(text)
    return out


def _words(s):
    return [w.strip(".,!?;:\"'").lower() for w in s.split() if w.strip(".,!?;:\"'")]


LONG_ACCUMULATION = (
    "I went to the store this morning. Then I bought some milk and bread. "
    "After that I walked home through the park. The weather was surprisingly "
    "good for this time of year. I think we should do this again next week."
)

SHORT_ACK_CUMULATIVE = [
    "Hello there.",
    "Hello there. How are you",
    "Hello there. How are you doing?",
    "Hello there. How are you doing? I am fine.",
    "Hello there. How are you doing? I am fine. Thanks for asking.",
]

ANADIPLOSIS_CUMULATIVE = [
    "And that is the bottom line.",
    "And that is the bottom line. The bottom line is simple.",
]

STALE_TERMINATOR = ["I am very happy.", "I am very happy today."]


class TestTheLongLineIsActuallyCut(unittest.TestCase):
    """The symptom item 70 is filed against. Pre-fix this produced ONE record."""

    def test_a_multi_sentence_accumulation_becomes_several_records(self):
        published, active = _drive(_cumulative(LONG_ACCUMULATION), "long")
        records = published + ([active] if active.strip() else [])
        self.assertGreater(
            len(records),
            1,
            f"the accumulation was never split: {records!r}",
        )

    def test_no_record_is_longer_than_the_whole_input_was(self):
        published, active = _drive(_cumulative(LONG_ACCUMULATION), "long2")
        records = published + ([active] if active.strip() else [])
        self.assertLess(
            max(len(r) for r in records),
            len(LONG_ACCUMULATION),
            "no record was shortened at all",
        )

    def test_every_record_ends_on_a_sentence_terminator_or_is_the_open_tail(self):
        published, _ = _drive(_cumulative(LONG_ACCUMULATION), "long3")
        for rec in published:
            self.assertIn(
                rec.rstrip()[-1],
                ".!?",
                f"a committed record does not end on a sentence boundary: {rec!r}",
            )


class TestContentIsNeverAlteredByTheSplit(unittest.TestCase):
    def test_no_word_is_lost(self):
        published, active = _drive(_cumulative(LONG_ACCUMULATION), "loss")
        got = _words(" ".join(published + [active]))
        want = _words(LONG_ACCUMULATION)
        self.assertEqual(
            sorted(got), sorted(want), "the split changed the words themselves"
        )

    def test_the_records_rejoin_into_the_original_text(self):
        published, active = _drive(_cumulative(LONG_ACCUMULATION), "join")
        rejoined = " ".join(r for r in published + [active] if r.strip())
        self.assertEqual(
            rejoined,
            LONG_ACCUMULATION,
            "the split is not byte-exact -- it rewrote the text",
        )


class TestTheTwoTrapsStayFixed(unittest.TestCase):
    """Both were reproduced against the real lifecycle in the withdrawn
    attempt. They are pinned on CONTENT, not on counts."""

    def test_a_two_token_sentence_is_committed_exactly_once(self):
        published, active = _drive(SHORT_ACK_CUMULATIVE, "ack")
        whole = " ".join(published + [active])
        self.assertEqual(
            whole.count("Hello there."),
            1,
            f"trap 1: the short sentence was committed twice: {published + [active]!r}",
        )

    def test_every_short_sentence_in_the_sequence_appears_once(self):
        published, active = _drive(SHORT_ACK_CUMULATIVE, "ack2")
        whole = " ".join(published + [active])
        for sentence in ("Hello there.", "How are you doing?", "I am fine."):
            self.assertEqual(
                whole.count(sentence), 1, f"{sentence!r} not committed exactly once"
            )

    def test_anadiplosis_keeps_the_repeated_phrase(self):
        published, active = _drive(ANADIPLOSIS_CUMULATIVE, "anad")
        whole = " ".join(published + [active])
        self.assertIn(
            "The bottom line is simple",
            whole,
            f"trap 2: the re-send trim ate the tail's head: {published + [active]!r}",
        )

    def test_neither_trap_sequence_loses_a_word(self):
        for seq, tag in ((SHORT_ACK_CUMULATIVE, "t1"), (ANADIPLOSIS_CUMULATIVE, "t2")):
            published, active = _drive(seq, tag)
            whole = " ".join(published + [active])
            for word in _words(seq[-1]):
                self.assertIn(word, whole.lower(), f"{word!r} lost from {seq[-1]!r}")


class TestARetractedTerminatorIsNeverSplitOn(unittest.TestCase):
    def test_a_withdrawn_period_does_not_become_a_boundary(self):
        published, active = _drive(STALE_TERMINATOR, "stale")
        self.assertEqual(
            published,
            [],
            "split at a terminator the provider had already withdrawn",
        )
        self.assertIn("today", active)

    def test_the_stale_merge_shape_still_exists(self):
        """If `_merge_lexical` is ever fixed this guard stops covering the trap,
        and the test should be re-pointed rather than silently passing."""
        from alpha.transcription.utterance_lifecycle import _merge_lexical

        merged = _merge_lexical("I am very happy.", "I am very happy today.")
        self.assertEqual(
            merged,
            "I am very happy. today.",
            "the pre-existing merge defect changed; re-check this trap",
        )


class TestTheTwoMechanismsThatMakeItWork(unittest.TestCase):
    """Both were found by tracing the real commit, not by reading, and both
    failed SILENTLY -- the split predicate fired every time and nothing was
    published. Pinned because neither is visible from the split logic itself."""

    def test_shortening_the_utterance_bumps_its_version(self):
        """`_observe_identity` records the text seen at each
        (utterance_id, version). Committing a shorter text at a version it has
        already seen is refused as `conflicting_same_version_text`, which
        rejected every interior split until the version was bumped with it.
        Measured over the 25 longest real records: without the bump, 25 records
        in produced 34 out with the longest still 1909 characters."""
        host = _Host()
        owner = reset_utterance_lifecycle(host, session_id="item70-version")
        chunks = _cumulative(
            "First one is done. Second one follows it. Third one ends here.", step=3
        )
        seen = []
        for i, chunk in enumerate(chunks):
            owner.on_final_chunk(
                text=chunk,
                speaker=1,
                channel=0,
                start=0.0,
                end=(i + 1) * 1.2,
                is_final=True,
                speech_final=False,
                event_id=f"e{i}",
                metadata={"channel_index": 0},
                deepgram_request_id=f"r{i}",
            )
            if owner._active is not None:
                seen.append((owner._active.utterance_id, owner._active.version))
        self.assertTrue(
            host.published,
            "no split committed at all -- the identity registry refused it",
        )
        self.assertEqual(
            len(seen), len(set(seen)), "a version was reused with different text"
        )

    def test_the_resend_trim_is_disarmed_after_a_split(self):
        """Item 66's trim is fuzzy and token-bounded, so it cannot tell a
        provider re-send from a speaker genuinely repeating themselves. A split
        commit ends on a terminator by construction, so there is no partial span
        to re-send and the trim has no work to do. Leaving it armed was measured
        deleting 9 words across the 25 longest real records."""
        from alpha.transcription.utterance_lifecycle import (
            _HARD_BOUNDARY_COMMIT_REASONS,
        )

        self.assertIn("sentence_boundary_flush", _HARD_BOUNDARY_COMMIT_REASONS)

    def test_a_genuine_immediate_repetition_survives_a_split(self):
        """The real record this was found on:
        "...Oh, yeah. So everybody agrees. So everybody agrees. Yeah..." lost one
        whole "So everybody agrees" to the fuzzy trim."""
        text = "Oh yeah. So everybody agrees. So everybody agrees. Yeah okay."
        published, active = _drive(_cumulative(text, step=3), "repeat")
        whole = " ".join(published + [active])
        self.assertEqual(
            whole.count("So everybody agrees"),
            2,
            f"a genuine repetition was collapsed: {published + [active]!r}",
        )


class TestExistingBehaviourIsUnchanged(unittest.TestCase):
    def test_pure_append_still_flushes_at_the_seam(self):
        published, active = _drive(
            [
                "First sentence is complete.",
                "Second sentence starts here.",
                "And a third one.",
            ],
            "pa",
        )
        self.assertEqual(
            published,
            ["First sentence is complete.", "Second sentence starts here."],
        )
        self.assertEqual(active, "And a third one.")

    def test_text_with_no_terminator_never_splits(self):
        published, active = _drive(["No terminator here", "and it continues"], "nt")
        self.assertEqual(published, [])
        self.assertIn("continues", active)

    def test_a_single_sentence_is_never_split(self):
        published, active = _drive(["One complete sentence."], "one")
        self.assertEqual(published, [])
        self.assertEqual(active, "One complete sentence.")

    def test_the_kill_switch_disables_the_interior_split_too(self):
        with patch("alpha.constants.ENGLISH_SENTENCE_FLUSH_ENABLED", False):
            published, active = _drive(_cumulative(LONG_ACCUMULATION), "off")
        self.assertEqual(
            published, [], "the flag must disable every split path, not just the seam"
        )


if __name__ == "__main__":
    unittest.main()
