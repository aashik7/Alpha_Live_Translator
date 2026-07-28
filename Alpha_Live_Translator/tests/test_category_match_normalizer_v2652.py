"""Unit tests for truthful category-match normalization (v26.5.2).

Uses terminology deliberately absent from the multidomain_meeting_v1 benchmark.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from score_multidomain_gate_85262 import (  # noqa: E402
    _term_in_hyp,
    normalize_category_match_text,
    normalize_text,
)


class TestCategoryMatchNormalizerV2652(unittest.TestCase):
    def test_nfkc_fullwidth_latin(self) -> None:
        # Fullwidth Latin letters → ASCII via NFKC, then casefold.
        self.assertEqual(
            normalize_category_match_text("Ｋｐｉダッシュボード"),
            normalize_category_match_text("KPIダッシュボード"),
        )

    def test_ascii_casefold(self) -> None:
        self.assertEqual(
            normalize_category_match_text("OkRレビュー"),
            normalize_category_match_text("okrレビュー"),
        )

    def test_whitespace_between_split_latin_letters(self) -> None:
        self.assertEqual(
            normalize_category_match_text("e t l パイプライン"),
            normalize_category_match_text("ETLパイプライン"),
        )
        self.assertEqual(
            normalize_category_match_text("q a ゲート"),
            normalize_category_match_text("QAゲート"),
        )

    def test_punctuation_equivalent_ab_forms(self) -> None:
        self.assertEqual(
            normalize_category_match_text("X/Y分析"),
            normalize_category_match_text("XY分析"),
        )
        self.assertEqual(
            normalize_category_match_text("X／Y分析"),
            normalize_category_match_text("XY分析"),
        )

    def test_term_in_hyp_recovers_spaced_lowercase_acronym(self) -> None:
        hyp = normalize_text("本日の e t l パイプライン障害について")
        self.assertTrue(_term_in_hyp("ETLパイプライン", hyp))

    def test_term_in_hyp_recovers_slash_acronym(self) -> None:
        hyp = normalize_text("今週の x y 分析結果を共有します")
        self.assertTrue(_term_in_hyp("X/Y分析", hyp))

    def test_does_not_equate_different_kanji(self) -> None:
        hyp = normalize_text("本日は契約書の確認を行います")
        self.assertFalse(_term_in_hyp("契約者", hyp))

    def test_does_not_equate_different_names(self) -> None:
        hyp = normalize_text("山田太郎が説明します")
        self.assertFalse(_term_in_hyp("山田次郎", hyp))

    def test_does_not_equate_different_numeric_values(self) -> None:
        hyp = normalize_text("予算は120万円です")
        self.assertFalse(_term_in_hyp("210万円", hyp))

    def test_strict_cer_normalizer_unchanged_preserves_case_and_slash(self) -> None:
        # Strict CER path must NOT casefold or strip A/B punctuation.
        strict = normalize_text("A/BテストとCRM")
        self.assertIn("/", strict)
        self.assertIn("A/B", strict)
        self.assertNotEqual(strict, strict.casefold()) if any(c.isupper() for c in strict) else self.assertTrue(True)
        self.assertEqual(normalize_text("Hello CRM"), "HelloCRM")


if __name__ == "__main__":
    unittest.main()
