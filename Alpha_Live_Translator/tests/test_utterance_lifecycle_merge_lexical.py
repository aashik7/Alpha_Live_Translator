"""Regression tests for `_merge_lexical` — CLIENT_DELIVERY_SPRINT_v5.md
problem F / item 51.

Deepgram re-sends the same growing utterance with different formatting
("50 percent" -> "50%", "mister" -> "Mr.", punctuation settling as the
sentence resolves) and also slides its window forward over continuous
speech, repeating the previous chunk's tail. `_merge_lexical` recognised
neither, so it fell through to the concatenation branch meant for
genuinely disjoint chunks and glued each re-send onto the text it should
have replaced. The corruption compounded on every tick and reached the
canonical ledger, the translation input and the client-facing export
alike: on run `...20260811-182940`, 5 of 54 exported lines carried 85.9%
of the export's characters, the worst 5039 characters from ~112 glued
fragments.

`RealRecordedSequenceTest` is the anchor. Its fragments are not invented
for the test — they are the actual input sequence recovered from that
run, verified by re-merging them through the pre-fix function and getting
the recorded corrupted line back byte-for-byte.

Two directions have to hold at once, and the pair of them is the real
contract here:
  * duplication must stop (problem F), and
  * no chunk's new content may be dropped while stopping it — silent
    content loss is strictly worse than visible duplication, and an
    earlier iteration of this fix did exactly that (see
    `test_slid_window_with_new_tail_keeps_the_tail`).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    _compare_tokens,
    _merge_lexical,
    _overlap_join,
    _tail_resend_splice,
    should_use_utterance_lifecycle,
)


def _repeated_phrases(text: str, size: int = 4) -> list[str]:
    """N-grams that occur more than once, well apart -- the duplication signal."""
    words = text.lower().split()
    seen: dict[str, int] = {}
    repeats: list[str] = []
    for i in range(len(words) - size + 1):
        gram = " ".join(words[i : i + size])
        if gram in seen and i - seen[gram] > size - 1:
            repeats.append(gram)
        seen[gram] = i
    return repeats


def _fold(chunks: list[str]) -> str:
    """Apply chunks the way the lifecycle does: left-fold onto the accumulator."""
    acc = ""
    for chunk in chunks:
        acc = _merge_lexical(acc, chunk) if acc else chunk
    return acc


# The verified real input sequence for utterance U-1 of run
# v3.3.5.5.8.5.26.5.3-20260811-182940. Pre-fix, folding these produced the
# recorded 472-character corrupted export line exactly.
REAL_SEQUENCE = [
    "So I'm mister Olympia.",
    "So I'm Mr. Olympia,",
    "So I'm mister Olympia, the best bodybuilder in",
    "So I'm Mr. Olympia, the best",
    "bodybuilder in the world, and I didn't",
    "bodybuilder in the world, and I didn't spend more than 4",
    "bodybuilder in the world, and I didn't spend more than 4 hours a week",
    "bodybuilder in the world, and I didn't spend more than 4 hours a week lifting weights.",
    "bodybuilder in the world, and I didn't spend more than 4 hours a week lifting weights. Come on.",
]


class RealRecordedSequenceTest(unittest.TestCase):
    def test_real_sequence_does_not_self_concatenate(self):
        merged = _fold(REAL_SEQUENCE)
        # Pre-fix this was 472 characters. The spoken content is ~127.
        self.assertLess(
            len(merged),
            200,
            f"self-concatenation is back: {len(merged)} chars for one short "
            f"utterance -- {merged!r}",
        )

    def test_real_sequence_says_each_thing_once(self):
        merged = _fold(REAL_SEQUENCE).lower()
        for phrase in ("olympia", "bodybuilder", "lifting weights"):
            self.assertEqual(
                1,
                merged.count(phrase),
                f"{phrase!r} appears {merged.count(phrase)} times in {merged!r}",
            )

    def test_real_sequence_loses_no_spoken_content(self):
        """The other half of the contract: stopping duplication must not
        start dropping text."""
        merged = _fold(REAL_SEQUENCE).lower()
        for phrase in (
            "mister olympia",
            "the best bodybuilder",
            "in the world",
            "spend more than 4 hours a week",
            "lifting weights",
            "come on",
        ):
            self.assertIn(phrase, merged, f"{phrase!r} was dropped from {merged!r}")


class ReformattedResendTest(unittest.TestCase):
    """Same span, re-sent with different formatting -> replace, never glue."""

    def test_punctuation_variant_is_not_concatenated(self):
        merged = _merge_lexical("So I'm mister Olympia.", "So I'm Mr. Olympia,")
        self.assertNotIn("Olympia. So", merged, f"glued instead of replaced: {merged!r}")
        self.assertEqual(1, merged.lower().count("olympia"))

    def test_numeral_formatting_variant_is_not_concatenated(self):
        merged = _merge_lexical("Okay. Easy. 50 percent", "Easy. 50%")
        self.assertEqual(1, merged.lower().count("easy"), f"glued: {merged!r}")

    def test_growing_reformatted_utterance_takes_the_longer_side(self):
        """The asymmetric-length case that `SequenceMatcher.ratio()` used to
        reject at 0.50 despite 3 of 4 tokens matching in order."""
        merged = _merge_lexical(
            "So I'm Mr. Olympia,", "So I'm mister Olympia, the best bodybuilder in"
        )
        self.assertEqual("So I'm mister Olympia, the best bodybuilder in", merged)


class SlidingWindowOverlapTest(unittest.TestCase):
    """Deepgram slides its window over continuous speech; the next chunk
    repeats the previous one's tail."""

    def test_boundary_run_is_joined_not_repeated(self):
        merged = _merge_lexical(
            "So I'm mister Olympia, the best bodybuilder in",
            "bodybuilder in the world, and I didn't",
        )
        self.assertEqual(
            "So I'm mister Olympia, the best bodybuilder in the world, and I didn't",
            merged,
        )

    def test_slid_window_with_new_tail_keeps_the_tail(self):
        """Guards the content-loss regression this fix nearly introduced.

        With the boundary check ordered *after* the similarity gate, this
        pair scored as "same utterance re-said", the gate returned whichever
        side was longer -- prev, which still holds the earlier text -- and
        curr's new tail was silently discarded.
        """
        prev = (
            "So I'm mister Olympia, the best bodybuilder in the world, "
            "and I didn't spend more than 4 hours a week"
        )
        curr = (
            "bodybuilder in the world, and I didn't spend more than 4 hours "
            "a week lifting weights."
        )
        merged = _merge_lexical(prev, curr)
        self.assertIn("lifting weights", merged, f"new tail dropped: {merged!r}")
        self.assertEqual(1, merged.lower().count("bodybuilder"), f"duplicated: {merged!r}")

    def test_curr_fully_inside_prev_tail_adds_nothing(self):
        merged = _merge_lexical("I am very happy today", "happy today")
        self.assertEqual("I am very happy today", merged)


class TailResendSpliceTest(unittest.TestCase):
    """Once an utterance has accumulated, Deepgram re-sends only its most
    recent span, revised and extended. Whole-against-whole comparison cannot
    see that, and the shared run is not a clean suffix of the accumulator
    either, so both earlier mechanisms miss it."""

    def test_tail_resend_replaces_the_tail_instead_of_repeating_it(self):
        prev = (
            "Mhmm. Okay. Lower it. I'll lift it to the top. You lower it. "
            "So even though you failed, positively, we're"
        )
        curr = "So even though you failed positively, we can do a couple of extra reps"
        merged = _merge_lexical(prev, curr)
        self.assertEqual([], _repeated_phrases(merged), f"still duplicating: {merged!r}")
        self.assertIn("extra reps", merged, "new tail content was lost")
        self.assertTrue(merged.startswith("Mhmm. Okay. Lower it."), "earlier text was lost")

    def test_tail_resend_against_a_long_accumulator(self):
        prev = (
            "So I am mister Olympia, the best bodybuilder in the world, and I "
            "didn't spend more than 4 hours a week lifting weights. Come on. "
            "I spent more in a gym. Yeah."
        )
        curr = "I spent more in a gym lifting Yeah. But you didn't train with me."
        merged = _merge_lexical(prev, curr)
        self.assertEqual([], _repeated_phrases(merged), f"still duplicating: {merged!r}")
        self.assertIn("train with me", merged)
        self.assertIn("mister Olympia", merged)

    def test_splice_can_never_discard_more_than_the_orphan_bound(self):
        """The safety property the whole mechanism rests on: however long the
        accumulator gets, a splice drops at most `max_orphan` tokens."""
        prev = "one two three four five six seven eight nine ten"
        # curr re-sends from "four" but prev has 5 tokens after that run's end
        curr = "four five six completely different now"
        self.assertIsNone(
            _tail_resend_splice(
                prev, curr, _compare_tokens(prev), _compare_tokens(curr), max_orphan=2
            ),
            "a run sitting too early in prev must not splice -- that would "
            "discard unbounded text",
        )

    def test_short_coincidental_echo_does_not_splice(self):
        """`min_run` is stricter here than for `_overlap_join` because this
        step can drop text."""
        prev = "we talked about the budget"
        curr = "the budget was approved"
        self.assertIsNone(
            _tail_resend_splice(prev, curr, _compare_tokens(prev), _compare_tokens(curr))
        )

    def test_prefers_the_latest_occurrence_of_the_run(self):
        prev = "go left then go right"
        curr = "go right and stop"
        merged = _merge_lexical(prev, curr)
        self.assertEqual("go left then go right and stop", merged)


class DisjointChunksStillConcatenateTest(unittest.TestCase):
    """The behaviour that must NOT change. Over-correcting here turns
    problem F into silent content loss."""

    def test_unrelated_adjacent_sentences_concatenate(self):
        """Same pair `test_bugfix_spec_regression.py` asserts on."""
        merged = _merge_lexical(
            "Although it was raining heavily outside",
            "we decided to stay in the cozy living room, drink hot tea",
        )
        self.assertEqual(
            "Although it was raining heavily outside, we decided to stay in "
            "the cozy living room, drink hot tea",
            merged,
        )

    def test_single_shared_boundary_word_does_not_trigger_a_join(self):
        """`k >= 2` safety margin. One shared word at the boundary happens
        constantly between unrelated sentences; joining on it would drop a
        real word."""
        merged = _merge_lexical("I put it on the table", "the dog barked loudly")
        self.assertIn("the dog barked loudly", merged)
        self.assertTrue(merged.startswith("I put it on the table"))

    def test_terminal_punctuation_join_style_is_unchanged(self):
        self.assertEqual(
            "First thing. Second thing", _merge_lexical("First thing.", "Second thing")
        )
        self.assertEqual(
            "First thing, Second thing", _merge_lexical("First thing,", "Second thing")
        )


class CompareTokensTest(unittest.TestCase):
    def test_edge_punctuation_stripped_but_internal_apostrophes_kept(self):
        self.assertEqual(["didn't", "i'm", "olympia"], _compare_tokens("didn't, I'm; Olympia."))

    def test_same_word_with_different_trailing_punctuation_compares_equal(self):
        self.assertEqual(_compare_tokens("Olympia."), _compare_tokens("Olympia,"))

    def test_tokens_are_never_used_to_build_output(self):
        """Comparison form is lowercased; the merged text must keep the
        caller's original casing and punctuation."""
        merged = _merge_lexical("The Best Bodybuilder In", "Bodybuilder In The World.")
        self.assertIn("The Best Bodybuilder In The World.", merged)


class OverlapJoinUnitTest(unittest.TestCase):
    def test_returns_none_when_there_is_no_boundary_run(self):
        prev, curr = "alpha beta", "gamma delta"
        self.assertIsNone(
            _overlap_join(prev, curr, _compare_tokens(prev), _compare_tokens(curr))
        )

    def test_returns_none_for_a_one_token_boundary_run(self):
        prev, curr = "alpha beta", "beta gamma"
        self.assertIsNone(
            _overlap_join(prev, curr, _compare_tokens(prev), _compare_tokens(curr))
        )

    def test_prefers_the_longest_boundary_run(self):
        prev, curr = "x a b c", "a b c y"
        self.assertEqual(
            "x a b c y",
            _overlap_join(prev, curr, _compare_tokens(prev), _compare_tokens(curr)),
        )


class JapaneseIsNotAffectedTest(unittest.TestCase):
    """Problem F is English-only because Japanese never reaches this
    function -- it routes to japanese_sentence_assembler.py instead."""

    class _Host:
        def __init__(self, lang):
            self._listen_language = lang

    def test_japanese_does_not_use_this_lifecycle_path(self):
        self.assertFalse(should_use_utterance_lifecycle(self._Host("ja")))

    def test_english_does_use_this_lifecycle_path(self):
        self.assertTrue(should_use_utterance_lifecycle(self._Host("en")))


if __name__ == "__main__":
    unittest.main()
