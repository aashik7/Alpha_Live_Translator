"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, items 14, 15, 16.

Item 14: merge_japanese_fragments's `prev.endswith(curr): return prev`
fast path silently discarded a fragment over 32 chars whenever it was a
literal suffix of the already-buffered text -- no live occurrence was
found in real run data (114 merge events scanned across
troubleshooting/runs/, 0 no-growth), so this pins the theoretical case
directly: a long, brand-new fragment that happens to duplicate the
buffer's tail must not vanish without a trace.

Item 15: `_looks_like_speaker_continuation_tail`'s
`_SPEAKER_LOCK_CONTINUATION_PREFIXES` list used to include ordinary
Japanese connectives (nandakedo/sorega/dakara/demo/kedo) that any speaker
can open a sentence with. A genuinely new speaker's turn starting with
one of them was misclassified as "this is the same speaker continuing",
contributing to a wrong speaker-lock decision upstream.

Item 16: teams_commit_decision_from_dup_action was renamed to
..._diagnostic_only after confirming by full control-flow trace that its
return value only ever reaches logging calls, never a commit branch. This
pins the mapping table so the rename didn't also change behavior.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.japanese_sentence_assembler import (  # noqa: E402
    JapaneseContinuityAssembler,
    merge_japanese_fragments,
)
from alpha.transcription.deepgram_client import (  # noqa: E402
    teams_commit_decision_from_dup_action_diagnostic_only,
)


class TestMergeJapaneseFragmentsLongSuffixNotDiscarded(unittest.TestCase):
    def test_long_fragment_matching_buffer_tail_is_not_silently_dropped(self):
        # curr is 33 chars, an exact literal match of prev's own tail, more
        # than the loop's 32-char search window -- the case the old fast
        # path handled by discarding curr in full.
        curr = "これはテストのためのとても長い文章ですちょうど三十三文字くらいになるように書きました"
        # Pad prev so it genuinely ends with curr (prev = filler + curr).
        prev = "冒頭のフィラー音声がここに入ります。" + curr
        self.assertTrue(prev.endswith(curr))
        self.assertGreater(len(curr), 32)

        merged = merge_japanese_fragments(prev, curr)

        # Note: assertIn(curr, merged) alone would not distinguish the two
        # behaviors here, since prev already contains curr's exact text as
        # its own tail by construction. The real signal of "curr was
        # silently discarded" is merged == prev / no growth -- the old fast
        # path returned prev completely unchanged, discarding the fact
        # that curr (new STT output) was ever processed at all.
        self.assertNotEqual(
            merged,
            prev,
            "a 33+-char fragment must not vanish without a trace even if "
            "its text duplicates the buffer's existing tail",
        )
        self.assertGreater(len(merged), len(prev))

    def test_short_exact_duplicate_still_collapses_unchanged(self):
        # Documents that the fix is intentionally narrow: for curr <= 32
        # chars the overlap search below reaches the same conclusion the
        # old fast path did, so this behavior is deliberately unchanged.
        prev = "そうですね、それでいいと思います"
        curr = "それでいいと思います"
        self.assertTrue(prev.endswith(curr))

        merged = merge_japanese_fragments(prev, curr)

        self.assertEqual(merged, prev)

    def test_curr_fully_containing_prev_still_replaces(self):
        prev = "こんにちは"
        curr = "こんにちは、元気ですか"
        merged = merge_japanese_fragments(prev, curr)
        self.assertEqual(merged, curr)


class TestSpeakerContinuationTailIgnoresOrdinaryConnectives(unittest.TestCase):
    def _check(self, text, reason="normal"):
        return JapaneseContinuityAssembler._looks_like_speaker_continuation_tail(
            None, text, reason
        )

    def test_ordinary_connective_alone_is_not_a_continuation_signal(self):
        for connective in ("でも", "だから", "けど", "それが", "なんだけど"):
            text = connective + "、これは新しい話者の発言です。"
            with self.subTest(connective=connective):
                self.assertFalse(
                    self._check(text),
                    f"'{connective}' is an ordinary connective, not proof "
                    "of same-speaker continuation",
                )

    def test_specific_retained_phrase_prefix_still_matches(self):
        text = "理由までちゃんと説明していませんでした"
        self.assertTrue(self._check(text))

    def test_full_retained_phrase_still_matches(self):
        text = "それがあんまりないのかもしれないっ思いました"
        self.assertTrue(self._check(text))


class TestTeamsCommitDecisionRenameKeepsMapping(unittest.TestCase):
    def test_add_action_maps_to_commit_new(self):
        decision, reason = teams_commit_decision_from_dup_action_diagnostic_only(
            "add", "", "new text"
        )
        self.assertEqual(decision, "commit_new")
        self.assertEqual(reason, "new_segment")

    def test_skip_duplicate_normalized_equal(self):
        decision, reason = teams_commit_decision_from_dup_action_diagnostic_only(
            "skip", "hello there", "hello there"
        )
        self.assertEqual(decision, "skip_duplicate")
        self.assertEqual(reason, "normalized_equal")

    def test_update_action_extends_previous(self):
        decision, reason = teams_commit_decision_from_dup_action_diagnostic_only(
            "update", "hello", "hello there"
        )
        self.assertEqual(decision, "merge_with_previous")
        self.assertEqual(reason, "current_extends_previous")


if __name__ == "__main__":
    unittest.main()
