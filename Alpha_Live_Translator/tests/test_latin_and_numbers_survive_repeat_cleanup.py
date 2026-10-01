"""Japanese repeat cleanup leaves Latin words and numbers whole.

WHAT WAS BROKEN (item 67, the owner's live meeting of 2026-10-01)
-----------------------------------------------------------------
`_fix_adjacent_repeat_with_suffix` collapses any unit repeated back to back
when more text follows. Deepgram writes Latin text inside Japanese without
spaces, so a repeat made of letters is inside a word, and one made of digits
is inside a number:

    Thisiswithtakeね。          -> Thiswithtakeね。      (this is with take)
    バージョンwiththis。         -> バージョンwithis。
    これですね。Thisis。isClub。 -> これですね。ThisClub。
    2020年に始めました。         -> 20年に始めました。     (direct call)

Five of the nine collapses logged in the retained runs were of this kind; the
number case was found by calling the function, not seen in a run yet.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.cjk_text import (  # noqa: E402
    merge_boundary_prefix_overlap,
    remove_cjk_local_repeats,
    remove_cjk_prefix_overlap,
)

KEPT = [
    "Thisiswithtakeね。",
    "バージョンwiththis。",
    "これですね。Thisis。isClub。",
    "ok。isisこれは体育さんも一緒で。",
    "Mississippiです。",
    "2020年に始めました。",
    "1010円です。",
    "２０２０年です。",
    "ByeByeです。",
]


class LatinAndNumbersAreKeptTest(unittest.TestCase):
    def test_prefix_overlap(self):
        for text in KEPT:
            with self.subTest(text=text):
                self.assertEqual(remove_cjk_prefix_overlap(text, "ja", None), text)

    def test_local_repeats(self):
        for text in KEPT:
            with self.subTest(text=text):
                self.assertEqual(remove_cjk_local_repeats(text, "ja", None), text)

    def test_a_fragment_join(self):
        self.assertEqual(merge_boundary_prefix_overlap("売上は2020", "2020年より"), "売上は20202020年より")


class JapaneseRestartsAreStillCollapsedTest(unittest.TestCase):
    def test_the_collapses_the_retained_runs_logged(self):
        self.assertEqual(remove_cjk_prefix_overlap("他は他は言えるか。", "ja", None), "他は言えるか。")
        self.assertEqual(
            remove_cjk_prefix_overlap("いや久しぶりですね久しぶりですね。元気でしたか。", "ja", None),
            "いや久しぶりですね。元気でしたか。",
        )
        self.assertEqual(
            remove_cjk_prefix_overlap(
                "なんですけど、スクリーンからthisなんですけど、スクリーンからthis。これだと", "ja", None
            ),
            "なんですけど、スクリーンからthis。これだと",
        )


if __name__ == "__main__":
    unittest.main()
