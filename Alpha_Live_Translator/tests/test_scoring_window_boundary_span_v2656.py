"""Generic test: boundary-spanning records retain in-window text (v26.5.6)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.scoring_window_v265 import (  # noqa: E402
    clip_record_text_to_semantic_window,
    clip_text_to_semantic_window,
)


class TestScoringWindowBoundarySpanV2656(unittest.TestCase):
    def test_boundary_spanning_record_retains_in_window_text(self) -> None:
        # Synthetic meeting text: pre-boundary header + in-window body + post-boundary tail.
        # Japanese punctuation mirrors production transcripts; spaces are not significant.
        first = "開始の合図です。"
        last = "終了の合図です。"
        window = {
            "window_resolved": True,
            "first_reference_sentence": first,
            "last_reference_sentence": last,
            "start_anchor": {"needle_used": "開始の合図です"},
            "end_anchor": {"needle_used": "終了の合図です"},
        }
        spanning_record = (
            "前置きノイズ。"
            + first
            + "本文は残す。"
            + last
            + "後置きノイズ。"
        )
        clipped = clip_record_text_to_semantic_window(spanning_record, window)
        self.assertTrue(clipped.startswith("開始の合図です"), msg=repr(clipped[:80]))
        self.assertIn("終了の合図です", clipped)
        self.assertTrue(clipped.endswith("終了の合図です") or clipped.endswith("終了の合図です。"), msg=repr(clipped[-80:]))
        self.assertNotIn("前置きノイズ", clipped)
        self.assertNotIn("後置きノイズ", clipped)
        self.assertIn("本文は残す", clipped)

        full = "前置きノイズ\n" + spanning_record + "\n後置きノイズ\n"
        full_clipped = clip_text_to_semantic_window(full, window)
        self.assertEqual(full_clipped, clipped)


if __name__ == "__main__":
    unittest.main()
