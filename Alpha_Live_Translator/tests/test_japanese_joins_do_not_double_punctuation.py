"""A Japanese line joined to a fragment keeps one mark at the seam.

WHAT WAS BROKEN (the owner's meeting of 2026-09-29, replayed on 26.5.46)
-----------------------------------------------------------------------
Deepgram often starts a final with 、 or 。 that belongs to the pause before it
(「、はい。」「。ここでし」). The stable layer's `merge_punctuation_fragment`
(both of its branches returned f"{prev}{frag}") appended it as-is onto a line
that had already ended, and so did the assembler's `merge_japanese_fragments`
when its overlap search found nothing. The export read
「谷口さんでやろうか。、はい。」, 「石ブランチだよね。、おはようます。」 and
「ありましたよね。。こでしっけ」 (that one from the stable layer). Before the
change 10 of the 16 assertions below fail: 6 in the stable layer, 4 in the
assembler.

WHAT THESE TESTS PIN
--------------------
* after 。/．/？/、 a leading 、 or 。 is dropped
* the previous text is never changed: it is often a committed line, and
  rewriting its last 、 to 。 (as the first version did) made the join no
  longer start with that line, so it was committed a second time
* anything else is joined exactly as before
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.japanese_sentence_assembler import merge_japanese_fragments  # noqa: E402
from alpha.transcription.japanese_stable_accuracy import merge_punctuation_fragment  # noqa: E402

CASES = [
    ("谷口さんでやろうか。", "、はい。", "谷口さんでやろうか。はい。"),
    ("石ブランチだよね。", "、おはようます。", "石ブランチだよね。おはようます。"),
    ("ありましたよね。", "。ここでし", "ありましたよね。ここでし"),
    ("何ですか？", "、それで", "何ですか？それで"),
    ("もう一個あって、", "。", "もう一個あって、"),
    ("もう一個あって、", "、耳なの", "もう一個あって、耳なの"),
    ("話しましたけど、", "。はい、そうです。", "話しましたけど、はい、そうです。"),
    ("話しました．", "、それで", "話しました．それで"),
    ("これは", "、ここ", "これは、ここ"),  # no mark to double: kept
    ("資料を送ります", "。", "資料を送ります。"),
]


class TheJoinersTest(unittest.TestCase):
    def test_stable_layer(self):
        for prev, frag, expected in CASES:
            with self.subTest(prev=prev, frag=frag):
                self.assertEqual(merge_punctuation_fragment(prev, frag), expected)

    def test_assembler(self):
        for prev, frag, expected in CASES:
            with self.subTest(prev=prev, frag=frag):
                self.assertEqual(merge_japanese_fragments(prev, frag), expected)


if __name__ == "__main__":
    unittest.main()
