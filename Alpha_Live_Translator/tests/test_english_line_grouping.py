"""Tests for CLIENT_DELIVERY_SPRINT_v5.md item 65, third approach.

The user's complaint about the English transcript, verbatim: "like a
composition which is not suitable for reading". Live run
`...20260812-154956` exported one line of 2342 characters, 424 words, 27
sentences.

The requested shape, and the rule behind it, in the user's own words: break
after 2 sentences when they are long and after 3 when they are short, and since
a person cannot count letters while reading, decide it on a word budget. His
example is the specification, and `test_reproduces_the_users_example` asserts
this code reproduces his grouping exactly -- it is the only test here that
cannot be adjusted to fit the implementation.

The other load-bearing property is that this is *formatting*: item 65's second
attempt committed at sentence boundaries inside the lifecycle and 8 of 9
utterances never reached the export. Regrouping text cannot do that, and
`text_is_preserved` is asserted on every case, including the real 2342-char
line.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.utils.english_line_grouping import (  # noqa: E402
    ENGLISH_LINE_MAX_SENTENCES,
    collapse_stutters,
    group_sentences_into_lines,
    split_sentences,
    text_is_preserved,
)

USER_EXAMPLE = (
    "My name is Tariqul. I am from Bangladesh. I am a software developer. "
    "Currently I am working on Wicresoft Japan as a System Engineer. I have a "
    "dream to chase so I work so hard night and day. "
    "I live in Tokyo Japan right now. I use Bus and Train for come to office "
    "and it takes more than an hour to reach office."
)

USER_EXPECTED = [
    "My name is Tariqul. I am from Bangladesh. I am a software developer.",
    "Currently I am working on Wicresoft Japan as a System Engineer. I have a "
    "dream to chase so I work so hard night and day.",
    "I live in Tokyo Japan right now. I use Bus and Train for come to office "
    "and it takes more than an hour to reach office.",
]


class UserSpecificationTest(unittest.TestCase):
    def test_reproduces_the_users_example(self):
        """The specification. Three short sentences group; two long ones stop."""
        self.assertEqual(group_sentences_into_lines(USER_EXAMPLE), USER_EXPECTED)

    def test_short_sentences_take_a_third(self):
        lines = group_sentences_into_lines("One. Two. Three. Four. Five. Six.")
        self.assertEqual(lines, ["One. Two. Three.", "Four. Five. Six."])

    def test_long_sentences_stop_at_two(self):
        long_one = " ".join(["word"] * 15) + "."
        lines = group_sentences_into_lines(f"{long_one} {long_one} {long_one}")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].count("."), 2)

    def test_never_more_than_the_maximum(self):
        text = " ".join(f"S{i}." for i in range(30))
        for line in group_sentences_into_lines(text):
            self.assertLessEqual(line.count("."), ENGLISH_LINE_MAX_SENTENCES)


class NothingIsLostTest(unittest.TestCase):
    """Formatting only -- the property that separates this from attempt two."""

    def test_words_survive_the_users_example(self):
        self.assertTrue(
            text_is_preserved(USER_EXAMPLE, group_sentences_into_lines(USER_EXAMPLE))
        )

    def test_words_survive_the_real_2342_character_line(self):
        run = (
            PROJECT_ROOT
            / "troubleshooting/runs/v3.3.5.5.8.5.26.5.3-20260812-154956"
            / "transcripts/final_export_records.jsonl"
        )
        if not run.exists():
            self.skipTest("run evidence not present")
        rows = [
            json.loads(line)
            for line in run.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        worst = max(rows, key=lambda r: len(r["text"]))
        lines = group_sentences_into_lines(worst["text"])
        self.assertGreater(len(lines), 1, "the 27-sentence line was not split")
        self.assertTrue(text_is_preserved(worst["text"], lines))

    def test_split_sentences_is_lossless(self):
        text = "First one.  Second one!   Third?  "
        self.assertEqual("".join(split_sentences(text)), text)

    def test_empty_input(self):
        self.assertEqual(group_sentences_into_lines(""), [])
        self.assertEqual(group_sentences_into_lines("   "), [])

    def test_text_with_no_terminator_is_one_line(self):
        self.assertEqual(
            group_sentences_into_lines("no terminator here"), ["no terminator here"]
        )


class AbbreviationsAreNotBoundariesTest(unittest.TestCase):
    def test_titles_do_not_split(self):
        lines = group_sentences_into_lines("Dr. Smith arrived. He was late.")
        self.assertEqual(lines, ["Dr. Smith arrived. He was late."])

    def test_decimals_do_not_split(self):
        lines = group_sentences_into_lines("It grew 3.5 percent. That is good.")
        self.assertEqual(lines, ["It grew 3.5 percent. That is good."])


class SentenceSplitterCaseSensitivityTest(unittest.TestCase):
    """`re.IGNORECASE` on `\b[A-Z]` made it match ANY single letter.

    A sentence ending in a one-letter word -- "I take vitamin a." -- was read
    as an initial, so the boundary after it was never taken and two sentences
    merged into one line. Real initials and abbreviations must still be
    protected, which is why the two patterns are now separate rather than one
    case-insensitive alternation.
    """

    def test_a_lone_lowercase_letter_is_not_an_initial(self):
        from alpha.utils.english_line_grouping import _is_abbreviation_or_initial

        self.assertFalse(_is_abbreviation_or_initial("vitamin a"))

    def test_a_real_initial_is_still_protected(self):
        """The candidate ends just BEFORE the boundary period, so an initial
        looks like "...J", not "...J."."""
        from alpha.utils.english_line_grouping import _is_abbreviation_or_initial

        self.assertTrue(_is_abbreviation_or_initial("Tolkien, J"))

    def test_a_capital_with_its_own_period_is_not_an_initial(self):
        """Otherwise "A. B. C." stops splitting entirely."""
        from alpha.utils.english_line_grouping import _is_abbreviation_or_initial

        self.assertFalse(_is_abbreviation_or_initial("A."))

    def test_abbreviations_stay_case_insensitive(self):
        from alpha.utils.english_line_grouping import _is_abbreviation_or_initial

        for form in ("Dr", "dr", "PROF", "etc"):
            self.assertTrue(_is_abbreviation_or_initial(form), form)

    def test_titles_still_do_not_split(self):
        self.assertEqual(
            group_sentences_into_lines("Dr. Smith arrived. He was late."),
            ["Dr. Smith arrived. He was late."],
        )


class NumbersAreNotStuttersTest(unittest.TestCase):
    """Collapsing a repeated digit changes a NUMBER rather than removing a
    stumble. "1 1" and "8 8" appear in real transcripts as list items or as a
    decimal the provider split."""

    def test_repeated_digits_survive(self):
        for phrase in ("8 8 percent", "1 1 of them", "chapter 3 3 begins"):
            self.assertEqual(collapse_stutters(phrase), phrase)

    def test_words_are_still_collapsed(self):
        self.assertEqual(collapse_stutters("he he writes"), "he writes")

    def test_alphanumeric_tokens_survive(self):
        self.assertEqual(collapse_stutters("room 4b 4b now"), "room 4b 4b now")


class ReadableLinesAreMemoisedTest(unittest.TestCase):
    """`get_clean_text` runs on every UI render and re-derived every segment.

    Measured before: 14.9 ms at 20 segments, 29.0 at 160, 62.0 at 320, against
    `UI_QUEUE_TIME_BUDGET_MS = 10`. The 99-minute run logged 162
    `UI_EVENT_DRAIN_TIME_BUDGET_EXCEEDED` events.
    """

    def test_repeated_renders_reuse_the_cache(self):
        store = TranscriptStore()
        store.add_segment(speaker=1, text=USER_EXAMPLE, source_language="en")
        first = store.get_clean_text()
        segment = store.get_all()[0]
        self.assertIsNotNone(getattr(segment, "_readable_cache", None))
        self.assertEqual(store.get_clean_text(), first)

    def test_an_in_place_revision_invalidates_it(self):
        """`update_last_segment_if_active` rewrites text in place, so caching
        on identity alone would keep serving the pre-revision lines."""
        store = TranscriptStore()
        store.add_segment(speaker=1, text="First one. Second one.", source_language="en")
        before = store.get_clean_text()
        store.update_last_segment_if_active(1, "Totally different now. Second thing.")
        after = store.get_clean_text()
        self.assertNotEqual(before, after)
        self.assertIn("Totally different now.", after)

    def test_rendering_many_segments_stays_cheap(self):
        import time

        store = TranscriptStore()
        for _ in range(300):
            store.add_segment(speaker=1, text=USER_EXAMPLE, source_language="en")
        store.get_clean_text()
        started = time.perf_counter()
        store.get_clean_text()
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertLess(elapsed_ms, 25.0, f"{elapsed_ms:.1f} ms per render at 300 segments")


class StoreRendersGroupedLinesTest(unittest.TestCase):
    """Wired where the readable transcript is actually built."""

    def test_english_segment_becomes_several_speaker_lines(self):
        store = TranscriptStore()
        store.add_segment(speaker=1, text=USER_EXAMPLE, source_language="en")
        lines = [l for l in store.get_clean_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertTrue(line.startswith("Speaker"), line)

    def test_japanese_is_left_alone(self):
        store = TranscriptStore()
        store.add_segment(
            speaker=1, text="これは一つ目です。これは二つ目です。これは三つ目です。", source_language="ja"
        )
        lines = [l for l in store.get_clean_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, "Japanese must keep its own boundaries")

    def test_unknown_language_is_left_alone(self):
        store = TranscriptStore()
        store.add_segment(speaker=1, text=USER_EXAMPLE)
        lines = [l for l in store.get_clean_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)

    def test_no_word_is_lost_through_the_store(self):
        store = TranscriptStore()
        store.add_segment(speaker=1, text=USER_EXAMPLE, source_language="en")
        rendered = " ".join(
            l.split(":", 1)[1] for l in store.get_clean_text().splitlines() if ":" in l
        )
        self.assertEqual(rendered.split(), USER_EXAMPLE.split())


class StutterCollapseTest(unittest.TestCase):
    """The user's "repeated words". NOT item 64's seam duplicates -- run
    ...161651 has zero of those. These are the speaker stuttering, transcribed
    faithfully inside one payload, and they are removed from the READABLE copy
    only."""

    def test_repeated_pronoun_collapses(self):
        self.assertEqual(collapse_stutters("he he he writes openly"), "he writes openly")

    def test_repeated_preposition_collapses(self):
        self.assertEqual(collapse_stutters("While in in Duterte"), "While in Duterte")

    def test_legitimate_doubles_survive(self):
        for phrase in ("the food that that arrived", "he had had enough"):
            self.assertEqual(collapse_stutters(phrase), phrase)

    def test_punctuated_repetition_is_emphasis_not_stutter(self):
        self.assertEqual(collapse_stutters("Yes, yes indeed"), "Yes, yes indeed")

    def test_case_is_not_a_loophole(self):
        self.assertEqual(collapse_stutters("The the meeting"), "The meeting")

    def test_empty_input(self):
        self.assertEqual(collapse_stutters(""), "")


class ShortLinesAreCorrectTest(unittest.TestCase):
    """The user's "random short and long paragraph" is NOT a bug to fold away.

    Folding a short line into the previous one was tried and reverted: it turns
    "One. Two. Three." + "Four. Five. Six." into a six-sentence line, breaking
    the max-3 rule. His own first line is 13 words, so a short line is the
    correct output when the sentences are short.
    """

    def test_short_sentences_still_produce_short_lines(self):
        self.assertEqual(
            group_sentences_into_lines("Yes. Okay. Sure. Right. Fine. Good."),
            ["Yes. Okay. Sure.", "Right. Fine. Good."],
        )

    def test_a_long_line_is_never_produced_by_merging(self):
        for line in group_sentences_into_lines("A. B. C. D. E. F. G. H. I."):
            self.assertLessEqual(line.count("."), ENGLISH_LINE_MAX_SENTENCES)


if __name__ == "__main__":
    unittest.main()
