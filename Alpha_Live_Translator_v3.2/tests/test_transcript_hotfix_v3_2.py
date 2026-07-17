"""Transcript hotfix tests for Alpha Live Translator v3.2.1."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.transcription.duplicate_protection import (  # noqa: E402
    apply_transcript_sequence,
    decide_transcript_action,
)

TEST_INPUTS = [
    "Traditional meeting assistance force and trade offs. They either compromise",
    (
        "Traditional meeting assistance force and trade offs. They either compromise "
        "security by sending your data out to the cloud"
    ),
    (
        "Traditional meeting assistance force and trade offs. They either compromise "
        "security by sending your data out to the cloud or they require bots to join your calls"
    ),
    "And before you're wondering, no. I am not paid",
    "And before you're wondering, no. I am not paid or sponsored to make this video.",
    "However, you do not need to go with a pro plan. This",
    (
        "However, you do not need to go with a pro plan. This is a 100% free forever, "
        "and you do not need to pay for the pro features."
    ),
    "It asks us if we want to use Quill for this meeting. Let's",
    "It asks us if we want to use Quill for this meeting. Let's select yes.",
    "This is a new separate sentence.",
]

EXPECTED_SEGMENTS = [
    (
        "Traditional meeting assistance force and trade offs. They either compromise "
        "security by sending your data out to the cloud or they require bots to join your calls"
    ),
    "And before you're wondering, no. I am not paid or sponsored to make this video.",
    (
        "However, you do not need to go with a pro plan. This is a 100% free forever, "
        "and you do not need to pay for the pro features."
    ),
    "It asks us if we want to use Quill for this meeting. Let's select yes.",
    "This is a new separate sentence.",
]

FORBIDDEN_GLUE = [
    "compromiseTraditional",
    "paidAnd before",
    "ThisHowever",
    "Let'sIt asks",
]


def apply_hotfix_to_store(store: TranscriptStore, texts, speaker: int = 1) -> list[str]:
    """Mirror production store updates using hotfix decision logic."""
    for text in texts:
        segment = store.get_last_segment(speaker)
        previous = segment.text if segment is not None else None
        action, result = decide_transcript_action(previous, text)
        if action == "skip" or not result:
            continue
        if action == "update":
            if not store.update_last_segment(speaker, result):
                store.add_segment(speaker, result)
        else:
            store.add_segment(speaker, result)
    return [segment.text for segment in store.get_all()]


def has_cumulative_duplicate(segment: str, partial_inputs: list[str]) -> bool:
    """True when a prior partial final appears twice (cumulative merge bug)."""
    for partial in partial_inputs:
        partial = partial.strip()
        if len(partial) < 15:
            continue
        if segment.count(partial) >= 2:
            return True
    return False


class TestTranscriptHotfixV32(unittest.TestCase):
    def test_apply_transcript_sequence_helper(self):
        output = apply_transcript_sequence(TEST_INPUTS, speaker=1)
        self.assertEqual(output, EXPECTED_SEGMENTS)

    def test_transcript_store_hotfix_sequence(self):
        store = TranscriptStore()
        output = apply_hotfix_to_store(store, TEST_INPUTS, speaker=1)
        self.assertEqual(output, EXPECTED_SEGMENTS)

    def test_no_glued_or_repeated_segments(self):
        store = TranscriptStore()
        output = apply_hotfix_to_store(store, TEST_INPUTS, speaker=1)
        combined = "\n".join(output)
        for fragment in FORBIDDEN_GLUE:
            self.assertNotIn(fragment, combined, f"Found glued fragment: {fragment}")
        for segment in output:
            self.assertFalse(
                has_cumulative_duplicate(segment, TEST_INPUTS),
                f"Cumulative duplicate detected in segment: {segment}",
            )


if __name__ == "__main__":
    unittest.main()
