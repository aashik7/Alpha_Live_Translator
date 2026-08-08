"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, item 11 (audit §3.4 row 3).

Confirmed defect: main_window.py::_should_commit_interim_recovery refused
to commit the leftover interim whenever it appeared ANYWHERE inside the
last committed final (`norm_interim in norm_final` -> False,
"interim_in_final").

This is the second of two sequential filters on the Stop-time last-chance
recovery path -- `_check_stop_tail_duplicate` (item 10) runs first, and
both must pass for uncommitted speech to survive Stop. So a coincidental
interior match here loses the text permanently even after item 10's fix,
which is why the two had to be fixed separately.

An interim is the in-progress hypothesis building toward a final, so the
evidence that it is already covered by that final is that the final
*equals* it or *starts with* it. An interior or suffix-only match is
coincidence -- a speaker repeating an earlier phrase as a fresh closing
remark had that remark discarded.

The fix narrows the match to equality-or-prefix, mirroring item 10, and
removes the unreachable trailing `no_match` drop-path. These tests pin
both sides: coincidental interior/suffix matches must now survive, and
every genuine already-covered shape must still be refused.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402


class _Host:
    # Bind the real implementations, including the real _normalize_compare
    # (which strips punctuation via regex) -- this fix's behavior depends on
    # exactly that normalization, so stubbing a simpler one would test a
    # different function than the one that runs in production.
    _should_commit_interim_recovery = AlphaApp._should_commit_interim_recovery
    _normalize_compare = AlphaApp._normalize_compare

    def _is_japanese_manual_mode(self):
        return False


class TestCoincidentalContainmentIsNotDropped(unittest.TestCase):
    """The interim is NOT evidence-of-already-committed: it must commit."""

    def setUp(self):
        self.host = _Host()

    def test_interior_match_is_committed(self):
        # The speaker used the phrase mid-sentence, then repeated it alone
        # as a closing remark that never got committed. Pre-fix:
        # "interim_in_final" -> dropped at Stop, permanently.
        last_final = (
            "Okay so let us review the quarterly revenue numbers one more time."
        )
        interim = "review the quarterly revenue numbers"

        # Guard: this must really be an interior match, otherwise the test
        # would pass against the pre-fix code for the wrong reason.
        norm_i = self.host._normalize_compare(interim)
        norm_f = self.host._normalize_compare(last_final)
        self.assertIn(norm_i, norm_f)
        self.assertFalse(norm_f.startswith(norm_i))

        should_commit, reason = self.host._should_commit_interim_recovery(
            interim, last_final
        )
        self.assertTrue(
            should_commit,
            "a coincidental interior match must not discard the tail",
        )
        self.assertEqual(reason, "new_missing_tail")

    def test_suffix_match_is_committed(self):
        # Suffix-only containment is equally weak evidence: an interim that
        # is the TAIL of the previous final was never the prefix-shaped
        # in-progress hypothesis for it.
        last_final = "I really do not know what we should do about the schedule"
        interim = "what we should do about the schedule"

        norm_i = self.host._normalize_compare(interim)
        norm_f = self.host._normalize_compare(last_final)
        self.assertTrue(norm_f.endswith(norm_i))
        self.assertFalse(norm_f.startswith(norm_i))

        should_commit, reason = self.host._should_commit_interim_recovery(
            interim, last_final
        )
        self.assertTrue(should_commit)
        self.assertEqual(reason, "new_missing_tail")


class TestGenuinelyCoveredTailsAreStillRefused(unittest.TestCase):
    """The narrowed branch must still catch the shapes it was meant to."""

    def setUp(self):
        self.host = _Host()

    def test_exact_match_is_still_refused(self):
        text = "This is the closing sentence of the session."
        should_commit, reason = self.host._should_commit_interim_recovery(
            text, text
        )
        self.assertFalse(should_commit)
        # Equality is caught by the mirror-image branch above (final is
        # contained in interim with a zero-length margin), which is the
        # correct refusal for the same reason.
        self.assertEqual(reason, "not_meaningfully_longer")

    def test_interim_prefix_of_final_is_still_refused(self):
        # The normal shape: the interim was mid-utterance when the
        # completed final committed.
        last_final = "This is the closing sentence of the whole session today."
        interim = "This is the closing sentence"

        should_commit, reason = self.host._should_commit_interim_recovery(
            interim, last_final
        )
        self.assertFalse(should_commit)
        self.assertEqual(reason, "interim_in_final")


class TestUnrelatedBranchesUnchanged(unittest.TestCase):
    """Guards that the surrounding decision table did not shift."""

    def setUp(self):
        self.host = _Host()

    def test_too_short_still_refused(self):
        should_commit, reason = self.host._should_commit_interim_recovery(
            "thanks", "Some earlier committed line."
        )
        self.assertFalse(should_commit)
        self.assertEqual(reason, "too_short")

    def test_interim_extending_final_still_commits(self):
        last_final = "I think we should start"
        interim = "I think we should start the review meeting now please"
        should_commit, reason = self.host._should_commit_interim_recovery(
            interim, last_final
        )
        self.assertTrue(should_commit)
        self.assertEqual(reason, "interim_extends_final")

    def test_marginal_extension_still_refused(self):
        last_final = "I think we should start the review"
        interim = "I think we should start the review now"
        should_commit, reason = self.host._should_commit_interim_recovery(
            interim, last_final
        )
        self.assertFalse(should_commit)
        self.assertEqual(reason, "not_meaningfully_longer")

    def test_unrelated_text_still_commits(self):
        should_commit, reason = self.host._should_commit_interim_recovery(
            "And here is a brand new closing thought entirely.",
            "Completely unrelated earlier line.",
        )
        self.assertTrue(should_commit)
        self.assertEqual(reason, "new_missing_tail")

    def test_no_prior_final_still_commits(self):
        should_commit, reason = self.host._should_commit_interim_recovery(
            "A closing sentence with no prior final at all.", ""
        )
        self.assertTrue(should_commit)
        self.assertEqual(reason, "no_prior_final")


if __name__ == "__main__":
    unittest.main()
