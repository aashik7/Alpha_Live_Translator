"""The operator can add their own names, places and product words.

WHY THIS EXISTS
---------------
A proper noun that is not a Japanese word is the one thing the recogniser cannot
guess, and it does not fail quietly: it substitutes something that sounds
similar and is confident about it. Measured on run `...20260821-171012`, where
the speaker said their own name inside an otherwise Japanese sentence
(`私は … と申します`, so the LANGUAGE was right):

    spoken   タリクールイスラム   ->  returned  誰くりすらむ         confidence 0.989
    spoken   バングラデシュ       ->  returned  バングラデーション   confidence 0.997

Nothing downstream can catch that. The text is valid Japanese script and the
provider is certain, so `LANGUAGE_CONFIDENCE_REJECT` (item 86) cannot see it and
neither can a script check -- `バングラデーション` is ordinary katakana. The
provider's own mechanism is keyterms, which this app already sends; there was
simply no way for the operator to add their own.

MEASURED EFFECT, replaying that same audio through Deepgram at language=ja
--------------------------------------------------------------------------
    without user keyterms:  "と申します"                   (the name vanished)
    with user keyterms:     "タリクリスラムと申します"      (the name is back)

Honest limit, recorded so nobody overclaims it: `バングラデーション` did NOT
change in that test. Keyterms improve recognition of a term; they do not
guarantee it.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha import constants  # noqa: E402
from alpha.constants import (  # noqa: E402
    JAPANESE_KEYTERM_MAX,
    USER_KEYTERMS_PATH,
    resolve_japanese_keyterms,
)


class TheUserFileIsReadable(unittest.TestCase):
    def test_the_file_ships_with_the_app(self):
        self.assertTrue(
            USER_KEYTERMS_PATH.is_file(),
            "the operator has nowhere to put their own terms",
        )

    def test_it_is_valid_json_with_a_keyterms_list(self):
        data = json.loads(USER_KEYTERMS_PATH.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data.get("keyterms"), list)

    def test_it_explains_itself_to_whoever_opens_it(self):
        data = json.loads(USER_KEYTERMS_PATH.read_text(encoding="utf-8"))
        self.assertIn("_readme", data)

    def test_the_terms_reach_the_resolved_list(self):
        terms, _profile, _removed = resolve_japanese_keyterms()
        for expected in json.loads(USER_KEYTERMS_PATH.read_text(encoding="utf-8"))[
            "keyterms"
        ]:
            self.assertIn(expected, terms)


class TheMergeKeepsTheOperatorsTermsFirst(unittest.TestCase):
    """The cap truncates, and the operator's own names are the terms the
    recogniser is least able to guess. They must not be the ones dropped."""

    def test_user_terms_lead(self):
        terms, _p, _r = resolve_japanese_keyterms()
        user = json.loads(USER_KEYTERMS_PATH.read_text(encoding="utf-8"))["keyterms"]
        self.assertEqual(terms[: len(user)], user)

    def test_the_built_in_business_terms_survive(self):
        terms, _p, _r = resolve_japanese_keyterms()
        self.assertIn("お世話になっております", terms)

    def test_the_cap_is_respected(self):
        terms, _p, _r = resolve_japanese_keyterms()
        self.assertLessEqual(len(terms), JAPANESE_KEYTERM_MAX)

    def test_a_user_term_wins_the_cap_over_a_built_in(self):
        original = constants._load_user_keyterms
        many = [f"ユーザー用語{i}" for i in range(JAPANESE_KEYTERM_MAX + 20)]
        constants._load_user_keyterms = lambda: many
        try:
            merged = constants._merge_user_keyterms(["お世話になっております"])
        finally:
            constants._load_user_keyterms = original
        self.assertEqual(len(merged), JAPANESE_KEYTERM_MAX)
        self.assertEqual(merged[0], "ユーザー用語0")
        self.assertNotIn("お世話になっております", merged)


class TheLoaderNeverBreaksAStart(unittest.TestCase):
    """A hand-edited file WILL be malformed one day. That must mean 'no extra
    terms', never a session that refuses to start."""

    def setUp(self):
        self.original = constants.USER_KEYTERMS_PATH

    def tearDown(self):
        constants.USER_KEYTERMS_PATH = self.original

    def _load_with(self, text, tmpname):
        import tempfile

        d = Path(tempfile.mkdtemp())
        f = d / tmpname
        if text is not None:
            f.write_text(text, encoding="utf-8")
        constants.USER_KEYTERMS_PATH = f
        return constants._load_user_keyterms()

    def test_a_missing_file_is_no_terms(self):
        self.assertEqual(self._load_with(None, "absent.json"), [])

    def test_malformed_json_is_no_terms(self):
        self.assertEqual(self._load_with("{ not json", "bad.json"), [])

    def test_a_bare_list_is_accepted(self):
        self.assertEqual(self._load_with('["会社名"]', "list.json"), ["会社名"])

    def test_a_keyterms_key_that_is_not_a_list_is_no_terms(self):
        self.assertEqual(self._load_with('{"keyterms": "会社名"}', "wrong.json"), [])

    def test_blank_and_comment_entries_are_skipped(self):
        got = self._load_with(
            '{"keyterms": ["  ", "#note", "会社名", ""]}', "mixed.json"
        )
        self.assertEqual(got, ["会社名"])

    def test_duplicates_are_collapsed(self):
        got = self._load_with('{"keyterms": ["会社名", "会社名"]}', "dupe.json")
        self.assertEqual(got, ["会社名"])

    def test_entries_are_stripped(self):
        got = self._load_with('{"keyterms": ["  会社名  "]}', "pad.json")
        self.assertEqual(got, ["会社名"])


if __name__ == "__main__":
    unittest.main()
