"""Japanese translations are grouped into readable lines, losing nothing.

WHY
---
After item 82 the English pane reads as 2-3 sentence lines while the Japanese
translation was still one line per record. Measured on a real run's output: 36
records, median 139 characters, p90 392, max 769, holding 4.9 sentences each.

WHY THIS IS SAFE, MEASURED BEFORE IT WAS WRITTEN
------------------------------------------------
Japanese splitting is easier than English, not harder. `。！？` are unambiguous
sentence terminators with no abbreviation case ("Mr.", "Dr.") and no decimal
point to confuse -- exactly what `english_line_grouping`'s `_ABBREVIATION` and
`_INITIAL_LETTER` regexes exist to work around. On the real translation output:

  * a naive split reconstructed the original **36 of 36 records byte-exactly**;
  * only **2** terminators in the whole file sat inside `「」`, both in one
    record where a URL is read aloud, and those must not become breaks;
  * grouping 2-3 sentences at a ~60 character target gave **84 lines, median 72
    characters, p90 106, max 157**, against a previous max of 769.

C9 IS NOT IN SCOPE
------------------
C9 says the Japanese TRANSCRIPT is never regrouped by a display rule, because
its boundaries come from `japanese_sentence_assembler.py`. This is the Japanese
TRANSLATION -- DeepL output of English input, reaching the pane by a different
path with no assembler behind it. The transcript path is unchanged, and a test
below pins that.

ITEM 74(b) SHIPPED WITH IT, BECAUSE GROUPING MAKES IT LIVE
-----------------------------------------------------------
Both removal sites deleted `mark -> mark lineend + 1 chars`, exactly ONE logical
line. Correct only while a translation was one line. A three-line entry would
leave two orphans that no later revision can reclaim, and they reach the
client's file through `_get_translated_transcript_for_copy_export`'s widget-read
fallback. The line count now travels on the registry entry and the removal
replays it.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.japanese_line_grouping import (  # noqa: E402
    group_japanese_lines,
    japanese_text_is_preserved,
    looks_japanese,
    split_japanese_sentences,
)

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover
    TK_AVAILABLE = False

LONG_JP = (
    "ライアン、本当にありがとう。2019年に初めてライアンにインタビューした際、"
    "私が特に感銘を受けたことの一つが、彼の記憶力でした。"
    "彼は、長年にわたって読んできた本から、引用やエピソードを難なく引き出していました。"
    "序文には、ブレーズ・パスカルからの引用があります。"
    "そう、ブレーズ・パスカルです。私は本気で、彼の秘密を知りたかったのです。"
)

# Real canonical id shape. These tests used to key on `JP_ID`, which production
# never produces, and that is precisely why they passed while the removal was
# broken in the field: the mark built from a real id
# (`tr_done_jp-utt-e0dcbd1255fc_1`) contains hyphens, and Tk reads a `-` run
# inside a text index as a modifier operator, so the composed delete expression
# raised and was swallowed. See
# `test_japanese_revision_replaces_translation.py`.
JP_ID = "jp-utt-e0dcbd1255fc"
JP_ID_2 = "jp-utt-53a73ab4b335"
EN_ID = "en-utt-482a61b3c7d1"


class TheGroupingNeverChangesTheText(unittest.TestCase):
    def test_lines_rejoin_into_the_original(self):
        parts = group_japanese_lines(LONG_JP)
        self.assertTrue(japanese_text_is_preserved(LONG_JP, parts))

    def test_it_actually_splits_a_long_record(self):
        self.assertGreater(len(group_japanese_lines(LONG_JP)), 1)

    def test_no_line_is_a_wall_of_text(self):
        for line in group_japanese_lines(LONG_JP):
            self.assertLess(len(line), 200, line)

    def test_a_single_sentence_stays_one_line(self):
        one = "これは一つの文です。"
        self.assertEqual(group_japanese_lines(one), [one])

    def test_empty_text_yields_nothing(self):
        self.assertEqual(group_japanese_lines("   "), [])

    def test_text_with_no_terminator_is_returned_whole(self):
        raw = "終止符のない日本語のテキスト"
        self.assertEqual(group_japanese_lines(raw), [raw])


class QuotedTerminatorsAreNotBreaks(unittest.TestCase):
    def test_a_terminator_inside_brackets_does_not_split(self):
        quoted = "テンプレートを閲覧します。「新しいドメイン名。example dot com。」次に進みます。"
        parts = group_japanese_lines(quoted)
        self.assertTrue(japanese_text_is_preserved(quoted, parts))
        for line in parts:
            self.assertEqual(
                line.count("「"), line.count("」"), f"a quote was split: {line!r}"
            )

    def test_split_keeps_terminators_on_their_sentence(self):
        parts = split_japanese_sentences("一です。二です。三です。")
        self.assertEqual(parts, ["一です。", "二です。", "三です。"])

    def test_a_run_of_terminators_stays_together(self):
        self.assertEqual(split_japanese_sentences("本当に？！次へ。"), ["本当に？！", "次へ。"])


class LanguageDetection(unittest.TestCase):
    def test_japanese_is_detected(self):
        self.assertTrue(looks_japanese("これはテストです。"))

    def test_english_is_not(self):
        self.assertFalse(looks_japanese("This is a test."))

    def test_a_latin_only_string_is_not(self):
        self.assertFalse(looks_japanese("Courage Is Calling"))


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheTranslationPaneUsesIt(unittest.TestCase):
    """Drives the real `_clear_translation_loading_item`."""

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.root = tk.Tk()
        self.root.withdraw()
        self.box = tk.Text(self.root)

        class Host:
            _clear_translation_loading_item = (
                AlphaApp._clear_translation_loading_item
            )
            _remove_translation_item_for_utterance = (
                AlphaApp._remove_translation_item_for_utterance
            )
            _delete_translation_entry = AlphaApp._delete_translation_entry
            _readable_translation_parts = AlphaApp._readable_translation_parts
            _ui_speaker_label_text = AlphaApp._ui_speaker_label_text

            def __init__(self, box):
                self.translated_verse_box = box
                self._translation_loading_items = {}
                self._translation_items_by_utterance = {}

            def _clear_text_placeholder(self, *a, **k):
                pass

            def _log_translation_display_skip(self, **kw):
                pass

        self.host = Host(self.box)

    def tearDown(self):
        self.root.destroy()

    def _complete(self, key, text, version=1, segment_id=1):
        self.host._clear_translation_loading_item(
            segment_id=segment_id,
            terminal_state="completed",
            replace_with_text=text,
            canonical_utterance_id=key,
            source_version=version,
        )

    def _lines(self):
        return [l for l in self.box.get("1.0", "end").splitlines() if l.strip()]

    def test_a_long_japanese_translation_is_rendered_as_several_lines(self):
        self._complete(JP_ID, LONG_JP)
        self.assertGreater(len(self._lines()), 1)

    def test_the_entry_line_count_is_recorded(self):
        self._complete(JP_ID, LONG_JP)
        entry = self.host._translation_items_by_utterance[JP_ID]
        self.assertEqual(entry["entry_lines"], len(self._lines()))

    def test_a_revision_removes_every_line_of_the_entry(self):
        """Item 74(b). Deleting one line of a three-line entry stranded two."""
        self._complete(JP_ID, LONG_JP)
        self._complete(JP_ID_2, "次の翻訳です。これは二番目の文です。", segment_id=2)
        self.host._remove_translation_item_for_utterance(
            canonical_utterance_id=JP_ID, source_version=1
        )
        remaining = self.box.get("1.0", "end")
        self.assertNotIn("ライアン、本当に", remaining)
        self.assertIn("次の翻訳です", remaining)

    def test_no_japanese_text_is_lost_on_the_way_to_the_pane(self):
        self._complete(JP_ID, LONG_JP)
        rendered = "".join(
            l.replace("Speaker:", "").strip() for l in self._lines()
        )
        self.assertEqual(
            "".join(rendered.split()), "".join(LONG_JP.split())
        )

    def test_an_english_translation_still_uses_the_english_rule(self):
        en = (
            "This is the first sentence here. This is the second sentence. "
            "And a third one closes it off completely. A fourth begins again. "
            "A fifth keeps it going for a while longer."
        )
        self._complete(EN_ID, en)
        joined = " ".join(
            l.replace("Speaker:", "").strip() for l in self._lines()
        )
        for word in en.replace(".", " ").split():
            self.assertIn(word, joined)


class TheJapaneseTranscriptPathIsUntouched(unittest.TestCase):
    """C9 regression guard: the assembler owns transcript boundaries."""

    def test_readable_parts_still_refuses_to_regroup_japanese(self):
        from alpha.summary.transcript_store import TranscriptStore

        store = TranscriptStore()
        jp = "これは一です。これは二です。これは三です。これは四です。"
        store.add_segment(speaker=1, text=jp, source_language="ja")
        lines = [l for l in store.get_clean_text().splitlines() if l.strip()]
        self.assertEqual(
            len(lines), 1, "the Japanese TRANSCRIPT was regrouped; C9 broken"
        )


if __name__ == "__main__":
    unittest.main()
