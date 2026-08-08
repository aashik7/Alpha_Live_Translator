"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, items 18 and 19.

Item 18: TranscriptStore.add_translation matched a segment by exact text
equality only. If the segment's text was revised between when the
translation request went out and when the result came back, no segment's
text equals the original request text anymore, the match silently fails,
and the translation is dropped with no log. add_translation now accepts
canonical_utterance_id and matches on that first when supplied (threaded
through from main_window.py's _append_translation_result all the way to
TranscriptSegment); text-equality is kept as the fallback for callers
that don't have an id.

Item 19: duplicate_continuation_ratio treated `current` as a full
duplicate (ratio=1.0, which its callers suppress outright) whenever it
appeared ANYWHERE inside `previous`, including the middle. Narrowed to
prefix-or-suffix of previous -- the only shapes that actually evidence a
truncated re-send rather than a coincidental repeated short remark. No
live occurrence of either bug was found in the evidence scanned (0
DUPLICATE_CONTINUATION_SUPPRESSED events across troubleshooting/runs/
for item 19), so both are static/theoretical fixes.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.transcription.japanese_boundary_stabilizer import (  # noqa: E402
    duplicate_continuation_ratio,
)


class TestAddTranslationMatchesByIdWhenAvailable(unittest.TestCase):
    def test_text_revised_after_request_still_finds_translation_by_id(self):
        store = TranscriptStore()
        store.add_segment(
            speaker=1, text="original rough text", canonical_utterance_id="U-1"
        )
        # Simulate a correction landing before the translation result does.
        store.update_last_segment_if_active(speaker=1, text="corrected final text")

        store.add_translation(
            original_text="original rough text",
            translated_text="translated text",
            canonical_utterance_id="U-1",
        )

        segment = store.get_all()[0]
        self.assertEqual(
            segment.translated_text,
            "translated text",
            "id-based match must survive a text revision that happened "
            "after the translation request went out",
        )

    def test_no_id_supplied_falls_back_to_legacy_text_match(self):
        store = TranscriptStore()
        store.add_segment(speaker=1, text="hello there")

        store.add_translation(
            original_text="hello there", translated_text="translated"
        )

        self.assertEqual(store.get_all()[0].translated_text, "translated")

    def test_id_supplied_but_not_found_does_not_fall_back_to_text_match(self):
        # An id was supplied but no segment carries it -- must not
        # silently reuse the old text-match path and risk attaching the
        # translation to the wrong (coincidentally same-text) segment.
        store = TranscriptStore()
        store.add_segment(
            speaker=1, text="hello there", canonical_utterance_id="U-1"
        )

        store.add_translation(
            original_text="hello there",
            translated_text="translated",
            canonical_utterance_id="U-does-not-exist",
        )

        self.assertIsNone(store.get_all()[0].translated_text)


class TestDuplicateContinuationRatioIgnoresMidlineCoincidence(unittest.TestCase):
    def test_coincidental_middle_substring_is_not_full_duplicate(self):
        previous = "本当にありがとうございました、助かりました"
        current = "ありがとうございました"
        self.assertIn(current, previous)
        self.assertFalse(previous.startswith(current))
        self.assertFalse(previous.endswith(current))

        ratio = duplicate_continuation_ratio(previous, current)

        self.assertLess(
            ratio,
            0.95,
            "a short remark that coincidentally appears mid-line in the "
            "previous utterance must not be suppressed as a duplicate",
        )

    def test_genuine_prefix_truncation_is_still_full_duplicate(self):
        previous = "こんにちは、元気ですか、今日はいい天気ですね"
        current = "こんにちは、元気ですか"
        self.assertTrue(previous.startswith(current))

        ratio = duplicate_continuation_ratio(previous, current)

        self.assertEqual(ratio, 1.0)

    def test_genuine_suffix_truncation_is_still_full_duplicate(self):
        previous = "えーと、それでですね、ありがとうございました"
        current = "ありがとうございました"
        self.assertTrue(previous.endswith(current))

        ratio = duplicate_continuation_ratio(previous, current)

        self.assertEqual(ratio, 1.0)

    def test_exact_match_is_still_full_duplicate(self):
        text = "そうですね"
        self.assertEqual(duplicate_continuation_ratio(text, text), 1.0)


if __name__ == "__main__":
    unittest.main()
