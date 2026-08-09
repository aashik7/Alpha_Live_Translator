"""Regression tests for BUG_FIX_ROADMAP.md item 11c.

`main_window.py::_should_commit_interim_recovery` opened with a single inline
guard, `if len(norm_interim) < 20`, shared by both scripts. That length is
measured AFTER `_normalize_compare`, and for CJK that routes through
`compact_cjk_for_compare`, which strips all spacing and punctuation -- so a
"character" there is a far larger unit of meaning than a Latin one. One number
cannot be right for both.

Measured over all 2210 interims in every recorded run (27 run folders):

           n     <20     %<20   min  p50  p90  max
    en  1188     351    29.5%     1   30   65  108
    ja  1022     931    91.1%     0    9   19   33

20 sits below the English median (30) but at more than double the Japanese
median (9) and above its p90 (19). For Japanese the guard therefore meant
"never recover a tail".

This runs on the Stop-time last-chance path, so a refusal is permanent loss.
All three interims ever genuinely pending at Stop were Japanese:

    日曜日、寝たから                     -> 7 normalized   (run 20260808-134815)
    たのはどこですか。最近はもう友達と    -> 16 normalized  (run 20260808-155334)
    思って-何、何、何、                   -> 7 normalized   (run 20260809-033339)

Every one was discarded by the old floor -- after items 10, 11 and 11b had
already been fixed specifically to let such tails reach this decision.

These tests pin both directions: real Japanese speech at the measured lengths
must now be admitted, bare particles and punctuation must still be refused,
and the English floor -- for which there is no contrary evidence, since no
English interim has ever been pending at Stop -- must not move.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import (  # noqa: E402
    STOP_TAIL_MIN_CHARS_CJK,
    STOP_TAIL_MIN_CHARS_LATIN,
)
from alpha.ui.main_window import AlphaApp  # noqa: E402


class _Host:
    """Binds the real decision + the real normalizer, per script."""

    _should_commit_interim_recovery = AlphaApp._should_commit_interim_recovery
    _normalize_compare = AlphaApp._normalize_compare
    _compact_japanese_for_compare = AlphaApp._compact_japanese_for_compare
    _is_cjk_pipeline_active = AlphaApp._is_cjk_pipeline_active
    _cjk_language_code = AlphaApp._cjk_language_code

    def __init__(self, japanese):
        self._japanese = japanese
        self._listen_language = "ja" if japanese else "en"

    def _is_japanese_manual_mode(self):
        return self._japanese


class TestMeasuredPendingTailsAreAdmitted(unittest.TestCase):
    """The three tails that actually occurred live must survive."""

    def setUp(self):
        self.host = _Host(japanese=True)

    def _assert_admitted(self, text, run):
        norm = self.host._normalize_compare(text)
        should_commit, reason = self.host._should_commit_interim_recovery(text, "")
        self.assertTrue(
            should_commit,
            f"run {run}: {text!r} normalizes to {len(norm)} chars and was "
            f"refused as {reason!r} -- this is real speech lost at Stop",
        )

    def test_sunday_because_i_slept(self):
        self._assert_admitted("日曜日、寝たから", "20260808-134815")

    def test_where_was_it_recently_with_friends(self):
        self._assert_admitted("たのはどこですか。最近はもう友達と", "20260808-155334")

    def test_thinking_what_what_what(self):
        # A stutter, and arguably low value -- but the decision of whether it
        # is worth keeping belongs to the containment guards, not to a blunt
        # length cut that also discards the two meaningful tails above.
        self._assert_admitted("思って-何、何、何、", "20260809-033339")

    def test_standard_japanese_closing_remark_is_admitted(self):
        self._assert_admitted("ありがとうございます。", "typical closing")


class TestJapaneseNoiseIsStillRefused(unittest.TestCase):
    """Lowering the floor must not start committing particles and punctuation."""

    def setUp(self):
        self.host = _Host(japanese=True)

    def _assert_refused(self, text):
        should_commit, reason = self.host._should_commit_interim_recovery(text, "")
        self.assertFalse(should_commit, f"{text!r} should not be committed")
        self.assertEqual(reason, "too_short")

    def test_bare_punctuation_refused(self):
        self._assert_refused("。")

    def test_single_particle_refused(self):
        self._assert_refused("で")

    def test_two_char_fragment_refused(self):
        self._assert_refused("とか")

    def test_three_char_fragment_refused(self):
        # 3 normalized chars is the last rejected bucket; meaning starts at 4.
        self._assert_refused("いたく")


class TestEnglishFloorUnchanged(unittest.TestCase):
    """No English tail has ever been pending at Stop -- do not move this."""

    def setUp(self):
        self.host = _Host(japanese=False)

    def test_english_floor_is_still_twenty(self):
        self.assertEqual(STOP_TAIL_MIN_CHARS_LATIN, 20)
        # 19 normalized chars must still be refused on the Latin path.
        should_commit, reason = self.host._should_commit_interim_recovery(
            "a" * 19, ""
        )
        self.assertFalse(should_commit)
        self.assertEqual(reason, "too_short")

    def test_short_english_fragment_still_refused(self):
        should_commit, reason = self.host._should_commit_interim_recovery(
            "Okay, well", ""
        )
        self.assertFalse(should_commit)
        self.assertEqual(reason, "too_short")

    def test_long_english_tail_still_admitted(self):
        should_commit, reason = self.host._should_commit_interim_recovery(
            "and that is the last thing I wanted to say today", ""
        )
        self.assertTrue(should_commit)
        self.assertEqual(reason, "no_prior_final")


class TestFloorSelection(unittest.TestCase):
    """The per-script split itself."""

    def test_cjk_floor_is_lower_than_latin(self):
        self.assertLess(
            STOP_TAIL_MIN_CHARS_CJK,
            STOP_TAIL_MIN_CHARS_LATIN,
            "compacted CJK characters carry more meaning, so their floor "
            "must be lower, not equal",
        )

    def test_cjk_floor_admits_every_measured_pending_tail(self):
        # 7, 16 and 7 were the normalized lengths observed live.
        for measured in (7, 16, 7):
            self.assertLessEqual(STOP_TAIL_MIN_CHARS_CJK, measured)

    def test_the_two_scripts_get_different_floors(self):
        # Same normalized length, opposite verdicts -- the whole point of the
        # split. 5 chars: above the CJK floor, far below the Latin one.
        ja_commit, _ = _Host(japanese=True)._should_commit_interim_recovery(
            "寝れたみたい", ""
        )
        en_commit, en_reason = _Host(japanese=False)._should_commit_interim_recovery(
            "hello", ""
        )
        self.assertTrue(ja_commit)
        self.assertFalse(en_commit)
        self.assertEqual(en_reason, "too_short")


if __name__ == "__main__":
    unittest.main()
