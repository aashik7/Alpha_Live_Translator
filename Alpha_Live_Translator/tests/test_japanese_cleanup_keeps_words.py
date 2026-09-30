"""Japanese text cleanup never rewrites a word or a number that was said.

WHAT WAS BROKEN (the owner's meeting of 2026-09-29, found reviewing it)
----------------------------------------------------------------------
`collapse_exact_duplicate_phrase` ran on every Deepgram final, again on every
merged assembler buffer, and again at commit, and removed ANY adjacent repeat
of a 1-6 character unit. Japanese is full of legitimate repeats, so it rewrote
what people said. Every collapse the retained meetings logged (56) was read;
the harmful ones included:

    ここに / ここ怪しい / ここでし   -> こに / こ怪しい / こでし   (x8)
    課題ナンバー二二七              -> 課題ナンバー二七          (227 -> 27)
    スリーラインスリーセブンセブン   -> スリーラインスリーセブン   (377 -> 37)
    QLサーバーバージョン            -> QLサーバージョン
    これでももったいないな。        -> これでももったいな       (and the 。)
    いい街ですか。 / ややね / てても -> い街ですか。 / やね / ても
    とりあえずThisis、             -> とりあえずThis、
    町田市。はいはい。ありがとう    -> 町田市はい。ありがとう    (a sentence end lost)

Its guard list of natural repeats (はいはい, うんうん, そうそう, まあまあ) never
applied: it compared the unit (はい), not the doubled form.

The collapses that were right were a speaker repeating a word with a pause
written between the copies: サンプル、サンプル / Java、Javaの.

WHAT THESE TESTS PIN
--------------------
* an adjacent repeat with nothing between the copies is left alone
* a repeat the speaker separated (、 。 space) at a phrase start is collapsed
  to one copy, keeping the punctuation after it
* never a number (「二十、二十一」), and never across a sentence end when the
  second copy goes on (「声の高さです。声の高さですか。」 is a statement and its
  echo) -- both found by the review of 2026-09-30
* the natural-repeat list protects its entries
* through the real per-fragment cleanup, precision cleanup and assembler
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription import japanese_boundary_stabilizer as jbs  # noqa: E402
from alpha.transcription import japanese_sentence_assembler as jsa  # noqa: E402
from alpha.transcription.canonical_identity_registry import reset_for_session  # noqa: E402
from alpha.transcription.utterance_lifecycle import reset_utterance_lifecycle  # noqa: E402
from alpha.utils import cjk_text as cj  # noqa: E402
from alpha.utils import language_pipeline_worker as lpw  # noqa: E402

KEPT = [
    "ここに入れてもらうか。",
    "ここ怪しいですね。デリートの。",
    "。ここでし",
    "課題ナンバー二二七。",
    "スリーラインスリーセブンセブンの",
    "QLサーバーバージョン",
    "これでももったいないな。",
    "いい街ですか。",
    "ややね、これ。",
    "てても。",
    "とりあえずThisis、この二つ",
    "もしもしも。",
    "一シーズンね。はいはい。",
    "町田市。はいはい。ありがとうございます。",
    "確認します。ますます良くなる。",
    "一、一、二です。",
    "田中さん、さんまを焼きます。",  # a word's tail + the next word's head
    "はい、はい。分かりました。",  # a natural repeat, protected
    # Review, 2026-09-30: numbers, and a sentence the next one echoes.
    "二十、二十一、二十二。",
    "桁は10、100、1000と増えます。",
    "10,100円です。",
    "ナンバースリー、ナンバースリーオンリーですね。",  # a real final
    "トネーション、声の高さです。声の高さですか。はい。",  # a real lesson: statement, then echo
]

COLLAPSED = [
    ("サンプル、サンプルデザインドキュメントねこれ。", "サンプルデザインドキュメントねこれ。"),
    ("Java、Javaの", "Javaの"),
    ("、大丈夫です。大丈夫です。", "、大丈夫です。"),
    ("ショート、ショートね、", "ショートね、"),
]


class TheCollapseTest(unittest.TestCase):
    def test_adjacent_repeats_are_words(self):
        for text in KEPT:
            with self.subTest(text=text):
                self.assertEqual(cj.collapse_exact_duplicate_phrase(text)[0], text)

    def test_a_separated_repeat_is_one_copy(self):
        for text, expected in COLLAPSED:
            with self.subTest(text=text):
                self.assertEqual(cj.collapse_exact_duplicate_phrase(text), (expected, True))

    def test_the_natural_repeats_are_protected(self):
        for unit in ("はい", "うん", "そう", "まあ"):
            with self.subTest(unit=unit):
                self.assertTrue(cj._should_protect_duplicate_unit(unit))


PREFIX_KEPT = [
    "たんですよ、新しく。もちろんもちろん。そう、",  # natural, not a restart
    "スリーセブンスリーセブンの",  # 3737
    "ナンバースリー、ナンバースリーオンリーですね。",
    "めちゃくちゃ面白いです。面白いですよね。",  # a real agreement echo
    "ルーパル。ルーパルイズ。",  # 。 between: kept as said (was collapsed)
]

PREFIX_COLLAPSED = [
    ("がんばってください。ありがとうございありがとうございます。",
     "がんばってください。ありがとうございます。"),  # keeps the 。 it dropped
    ("inTokyo、inTokyoの", "inTokyoの"),
    ("ご確認くださいくださいませ。", "ご確認くださいませ。"),  # a restart mid-phrase
]


class ThePrefixExtensionTest(unittest.TestCase):
    """The same flaw in the sibling rule: a restart 「A + A…」 was rebuilt from a
    punctuation-free copy (the 。 before it went), and number words collapsed."""

    def test_kept(self):
        for text in PREFIX_KEPT:
            with self.subTest(text=text):
                self.assertEqual(cj.collapse_prefix_extension_duplicate(text)[0], text)

    def test_collapsed(self):
        for text, expected in PREFIX_COLLAPSED:
            with self.subTest(text=text):
                self.assertEqual(cj.collapse_prefix_extension_duplicate(text), (expected, True))


class TheRealCleanupsTest(unittest.TestCase):
    def test_per_fragment_and_precision_cleanup(self):
        # Both lists: the production cleanups run both rules, and the first
        # version kept ナンバースリー… in one rule and cut it in the other.
        for text in KEPT + PREFIX_KEPT:
            with self.subTest(text=text):
                self.assertEqual(cj.cleanup_japanese_per_fragment(text)[0], text)
                self.assertEqual(cj.cleanup_japanese_transcript_precision(text)[0], text)


class _Host:
    _live_session_id = "sess-item49"
    _listen_language = "ja"
    _is_finalizing = False
    _is_stopping = False
    is_listening = True

    def __init__(self):
        self.published = []

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        self.published.append(text)
        return True


class _Worker:
    def __getattr__(self, name):
        return lambda *a, **k: None


class ThroughTheAssemblerTest(unittest.TestCase):
    """The owner's meeting: 「これは」 + 「ここ」 + 「を直してください。」 exported
    「これはこを直して…」 -- the lone 「ここ」 lost a こ at ingest."""

    def setUp(self):
        p = patch.object(lpw, "get_language_pipeline_worker", lambda: _Worker())
        p.start()
        self.addCleanup(p.stop)
        ctl.reset_for_run("run-item49")
        reset_for_session(_Host._live_session_id)
        jbs.reset_boundary_stabilizer()
        self.addCleanup(jbs.reset_boundary_stabilizer)
        self.host = _Host()
        reset_utterance_lifecycle(self.host, _Host._live_session_id)
        self.asm = jsa.JapaneseContinuityAssembler(self.host)
        self.asm.reset()
        self.n = 0

    def final(self, text, speech_final):
        self.n += 1
        self.asm.ingest(
            1, text, {"speech_final": speech_final, "source_raw_event_ids": [f"raw-{self.n}"]},
            "deepgram_final",
        )

    def test_a_lone_koko_keeps_both_characters(self):
        self.final("これは", False)
        self.final("ここ", False)
        self.final("を直してください。", True)
        self.assertEqual(self.host.published, ["これはここを直してください。"])

    def test_a_number_keeps_its_digits(self):
        self.final("課題ナンバー二二七です。", True)
        self.assertEqual(self.host.published, ["課題ナンバー二二七です。"])


if __name__ == "__main__":
    unittest.main()
