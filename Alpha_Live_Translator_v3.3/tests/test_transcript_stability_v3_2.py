"""Transcript stability tests for Alpha Live Translator v3.2 (obsolete)."""

import sys
import unittest
from pathlib import Path

raise unittest.SkipTest(
    "Obsolete pre-hotfix V3.2 transcript stability test. "
    "Replaced by test_transcript_hotfix_v3_2.py and V3.3.4 Meeting Segment Buffer behavior."
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.duplicate_protection import (  # noqa: E402
    apply_transcript_sequence,
    compact_for_compare,
    current_contains_previous,
    decide_transcript_action,
    is_contained_duplicate,
    is_exact_duplicate,
    is_progressive_extension,
    merge_with_safe_space,
    normalize_for_compare,
    previous_contains_current,
    remove_overlap_and_merge,
)

TEST_INPUTS = [
    "Without compromising security. Let me",
    "Without compromising security. Let me explain.",
    "Traditional meeting assistants force uncomfortable trade offs.",
    "Traditional meeting assistants force uncomfortable trade offs.",
    "We can either start it by just clicking on the button",
    "We can either start it by just clicking on the button or selecting the shortcut.",
    "Hand side, and it will automatically",
    "summarize the notes as the meeting goes on.",
    "And in just a 2nd, it's",
    "And in just a 2nd, it's pulled the whole transcript of the entire 11 minute meeting",
]

EXPECTED_OUTPUT = [
    "Without compromising security. Let me explain.",
    "Traditional meeting assistants force uncomfortable trade offs.",
    "We can either start it by just clicking on the button or selecting the shortcut.",
    "Hand side, and it will automatically summarize the notes as the meeting goes on.",
    "And in just a 2nd, it's pulled the whole transcript of the entire 11 minute meeting",
]

FORBIDDEN_FRAGMENTS = [
    "Let meWithout",
    "it'sAnd",
    "automaticallyHand",
]


class TestTranscriptStabilityHelpers(unittest.TestCase):
    def test_normalize_for_compare(self):
        self.assertEqual(
            normalize_for_compare("  Hello, World!  "),
            "hello world",
        )

    def test_compact_for_compare(self):
        self.assertEqual(
            compact_for_compare("Let me Without"),
            "letmewithout",
        )

    def test_is_exact_duplicate(self):
        self.assertTrue(
            is_exact_duplicate(
                "Traditional meeting assistants force uncomfortable trade offs.",
                "traditional meeting assistants force uncomfortable trade offs.",
            )
        )

    def test_is_contained_duplicate(self):
        self.assertTrue(
            is_contained_duplicate(
                "Without compromising security. Let me explain.",
                "Without compromising security. Let me",
            )
        )

    def test_is_progressive_extension(self):
        self.assertTrue(
            is_progressive_extension(
                "We can either start it by just clicking on the button",
                "We can either start it by just clicking on the button or selecting the shortcut.",
            )
        )

    def test_merge_with_safe_space(self):
        self.assertEqual(
            merge_with_safe_space("Hello", "world"),
            "Hello world",
        )
        self.assertEqual(
            merge_with_safe_space("Hello.", "world"),
            "Hello. world",
        )

    def test_remove_overlap_and_merge_hand_side_case(self):
        merged = remove_overlap_and_merge(
            "Hand side, and it will automatically",
            "summarize the notes as the meeting goes on.",
        )
        self.assertEqual(
            merged,
            "Hand side, and it will automatically summarize the notes as the meeting goes on.",
        )

    def test_decide_progressive_extension(self):
        action, text = decide_transcript_action(
            "Without compromising security. Let me",
            "Without compromising security. Let me explain.",
        )
        self.assertEqual(action, "replace_last")
        self.assertEqual(text, "Without compromising security. Let me explain.")

    def test_decide_skip_exact_duplicate(self):
        action, text = decide_transcript_action(
            "Traditional meeting assistants force uncomfortable trade offs.",
            "Traditional meeting assistants force uncomfortable trade offs.",
        )
        self.assertEqual(action, "skip")
        self.assertIsNone(text)

    def test_decide_skip_previous_contains_current(self):
        action, _ = decide_transcript_action(
            "Without compromising security. Let me explain.",
            "Without compromising security. Let me",
        )
        self.assertEqual(action, "skip")

    def test_decide_merge_last_fragment(self):
        action, text = decide_transcript_action(
            "Hand side, and it will automatically",
            "summarize the notes as the meeting goes on.",
        )
        self.assertEqual(action, "merge_last")
        self.assertEqual(
            text,
            "Hand side, and it will automatically summarize the notes as the meeting goes on.",
        )

    def test_current_contains_previous(self):
        self.assertTrue(
            current_contains_previous(
                "And in just a 2nd, it's",
                "And in just a 2nd, it's pulled the whole transcript of the entire 11 minute meeting",
            )
        )


class TestTranscriptStabilitySequence(unittest.TestCase):
    def test_full_sequence_produces_clean_output(self):
        result = apply_transcript_sequence(TEST_INPUTS, speaker=1)
        self.assertEqual(result, EXPECTED_OUTPUT)

    def test_no_glued_or_cumulative_fragments(self):
        result = apply_transcript_sequence(TEST_INPUTS, speaker=1)
        combined = "\n".join(result)
        for forbidden in FORBIDDEN_FRAGMENTS:
            self.assertNotIn(forbidden, combined, msg=f"Found forbidden fragment: {forbidden}")

        normalized_blob = compact_for_compare(combined)
        for line in result:
            compact_line = compact_for_compare(line)
            count = normalized_blob.count(compact_line)
            self.assertEqual(
                count,
                1,
                msg=f"Repeated cumulative phrase detected for line: {line}",
            )


if __name__ == "__main__":
    unittest.main()
