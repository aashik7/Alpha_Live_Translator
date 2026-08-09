"""Regression tests for BUG_FIX_ROADMAP.md item 21b.

Confirmed by live run `v3.3.5.5.8.5.26.5.3-20260809-142601` (English, Stop
pressed mid-sentence).

**Intra-line hypothesis chains were invisible.** Every duplicate detector
in `final_output_cleanup.py` compares line i against line i-1, so none can
see repetition that arrives *inside one line*. That run spoke for 13 seconds
with no pause long enough to endpoint, Stop sent `Finalize`, and Deepgram
returned a SINGLE 460-character final containing its own growing hypotheses
joined together:

    Okay. So what I did is now I'm actually, talking and, talking and I will
    stop the, talking and I will stop the button middle, talking and I will
    stop the button middle of the sentence, talking and I will stop the
    button middle of the sentence. And let me check, And let me check how
    actually, And let me check how actually alpha working. And let me check
    how actually alpha working. So now I'm, So now I'm going to tell a, So
    now I'm going to tell a long sente

The whole session produced only 2 provider finals; that line went into the
export verbatim while the run's own gate reported `cumulative_duplicate_count:
0` and `export_lossless: true`.

A second suspected defect turned out not to exist: the cross-line guards read
`_jp_char_len(prev) >= 12`, which looked like a CJK-only count that would be 0
for English (the same shape as item 11c). It is not —
`count_japanese_chars` returns the compacted length for any script (24 for
"And let me check how actually"), so that guard was already live. The
attempted "fix" was a no-op and was reverted; `TestCrossLineDetectorStillCannotSeeThis`
records the real reason those detectors miss this bug.

The collapse is deliberately conservative: it only removes fragments that are
strict prefix-extensions of a neighbour, which is the signature of a
hypothesis chain. Ordinary lists and ordinary Japanese must pass through
untouched — that is what the second test class pins.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.final_output_cleanup import (  # noqa: E402
    _FRAGMENT_SPLIT_RE,
    collapse_intra_line_progressive_repeats,
    detect_cumulative_alpha_lines_v2,
)

# Verbatim from the live run's provider_events.jsonl (raw-000002).
LIVE_ACCUMULATED = (
    "Okay. So what I did is now I'm actually, talking and, talking and I will "
    "stop the, talking and I will stop the button middle, talking and I will "
    "stop the button middle of the sentence, talking and I will stop the button "
    "middle of the sentence. And let me check, And let me check how actually, "
    "And let me check how actually alpha working. And let me check how actually "
    "alpha working. So now I'm, So now I'm going to tell a, So now I'm going to "
    "tell a long sente"
)


class TestLiveAccumulatedFinalIsCollapsed(unittest.TestCase):
    def test_the_live_460_char_final_collapses(self):
        out, dropped = collapse_intra_line_progressive_repeats(LIVE_ACCUMULATED)

        self.assertGreater(dropped, 0, "the hypothesis chain must be detected")
        self.assertLess(
            len(out),
            len(LIVE_ACCUMULATED) // 2,
            "collapsing should roughly halve this line, not trim its edges",
        )

    def test_each_repeated_phrase_survives_exactly_once(self):
        out, _ = collapse_intra_line_progressive_repeats(LIVE_ACCUMULATED)

        # These are what the speaker actually said; each must appear once.
        for phrase in (
            "talking and I will stop the button middle of the sentence",
            "And let me check how actually alpha working",
            "So now I'm going to tell a long sente",
        ):
            self.assertEqual(
                out.count(phrase), 1, f"{phrase!r} should appear exactly once in {out!r}"
            )

    def test_no_speech_is_invented(self):
        # Every kept fragment must have come from the input verbatim -- the
        # collapse may only DROP fragments, never rewrite or merge their words.
        # Split the output on the same boundaries the collapse uses, otherwise
        # a rejoined "A. B" chunk is compared against an input that only ever
        # contained "A" and "B" separately.
        out, _ = collapse_intra_line_progressive_repeats(LIVE_ACCUMULATED)
        for frag in [f.strip() for f in _FRAGMENT_SPLIT_RE.split(out) if f and f.strip()]:
            self.assertIn(
                frag,
                LIVE_ACCUMULATED,
                f"{frag!r} is not verbatim in the input -- the collapse invented text",
            )


class TestOrdinarySpeechIsUntouched(unittest.TestCase):
    """The collapse must never damage text that merely uses commas."""

    def _assert_unchanged(self, text):
        out, dropped = collapse_intra_line_progressive_repeats(text)
        self.assertEqual(out, text)
        self.assertEqual(dropped, 0)

    def test_short_line_untouched(self):
        self._assert_unchanged("Hello, world.")

    def test_two_plain_sentences_untouched(self):
        self._assert_unchanged("My name is Tarikul. I'm from Bangladesh.")

    def test_ordinary_list_untouched(self):
        self._assert_unchanged(
            "I bought apples, oranges, and pears at the market today."
        )

    def test_sequential_sentences_untouched(self):
        self._assert_unchanged("First, we talk. Then, we listen. Finally, we agree.")

    def test_japanese_closing_untouched(self):
        self._assert_unchanged("はい、ありがとうございます。")

    def test_japanese_list_untouched(self):
        self._assert_unchanged("そうですね、確かに、その通りです。")


class TestCrossLineDetectorStillCannotSeeThis(unittest.TestCase):
    """Why the intra-line pass is needed at all, pinned as a fact.

    An earlier draft of this fix also changed `_jp_char_len(prev) >= 12` in the
    cross-line guards, believing that count returned 0 for English and left the
    branch dead. **That was wrong** -- `count_japanese_chars` returns the
    compacted character count for any script (24 for "And let me check how
    actually"), so the guard was already live and the change was a no-op. It
    was reverted. These tests record the real reason the cross-line detectors
    miss this defect: the repetition never spans two lines.
    """

    def test_the_accumulated_final_is_a_single_line(self):
        # One line in, so there is no i-1 to compare against -- every
        # line-vs-line detector is structurally blind here.
        result = detect_cumulative_alpha_lines_v2([LIVE_ACCUMULATED])
        self.assertEqual(
            result["cumulative_duplicate_count"],
            0,
            "confirms the cross-line detector cannot see intra-line repeats; "
            "this is why collapse_intra_line_progressive_repeats exists",
        )

    def test_prefix_overlap_ratio_would_not_have_caught_the_fragments(self):
        # Even split into lines, the observed chains score below the 0.7 gate.
        from alpha.transcription.stable_line_revision import prefix_overlap_ratio

        for prev, cur in (
            ("talking and", "talking and I will stop the button middle of the sentence"),
            ("So now I'm", "So now I'm going to tell a long sente"),
        ):
            self.assertLess(prefix_overlap_ratio(prev, cur), 0.7)


if __name__ == "__main__":
    unittest.main()
