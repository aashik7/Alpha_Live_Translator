"""Deepgram is not asked for `numerals`: a quarter is not 0.25.

WHAT WAS BROKEN
---------------
Every live request carried `numerals=true` beside `smart_format=true`. On
English, `numerals` turns "quarter" into a number wherever it appears. Measured
live on 2026-09-25, one TTS clip streamed through Deepgram with the app's own
English parameters, then with `numerals` removed:

    numerals=true    Our 3rd 0.25 revenue grew by 12% ... About 0.25 of the team
                     joined the project in 2025. We will review the 4th 0.25 plan
                     at 09:30 tomorrow.
    without it       Our third quarter revenue grew by 12% ... About a quarter of
                     the team joined the project in 2025. We will review the
                     fourth quarter plan at 09:30 tomorrow.

`smart_format` alone still writes 12%, 2.5 million, March 3, 2025 and 09:30 --
nothing else in the clip changed. DeepL then carried the damage into the
translation: "第3四半期の売上高（0.25）". For quarterly results meetings that is
a wrong number on screen.

Japanese: the same measurement with a Japanese clip gave byte-identical output
with and without `numerals`, so dropping it for every language changes nothing
there.

WHAT THESE TESTS PIN
--------------------
* the URL the app actually opens, in English and in Japanese, asks for
  `smart_format` and not `numerals`
* the English parameter builder the replay tools use says the same
* the English request validator still accepts the production request
"""

import sys
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402
from alpha.utils.english_deepgram_request import (  # noqa: E402
    build_english_live_query_params,
    validate_english_query_string,
)


class _Host(DeepgramClientMixin):
    def __init__(self, language):
        self._listen_language = language
        self.is_listening = False
        self._starting_listening = True
        self._stop_event = threading.Event()


def _query(language):
    url = _Host(language)._build_deepgram_url()
    return url, parse_qs(urlparse(url).query)


class TheLiveRequestTest(unittest.TestCase):
    def test_english_asks_for_smart_format_and_not_numerals(self):
        _, query = _query("en")
        self.assertNotIn(
            "numerals", query,
            "numerals=true turns 'third quarter' into '3rd 0.25' on English",
        )
        self.assertEqual(query.get("smart_format"), ["true"], "12%, dates and times come from here")

    def test_japanese_the_same(self):
        _, query = _query("ja")
        self.assertNotIn("numerals", query)
        self.assertEqual(query.get("smart_format"), ["true"])

    def test_the_english_request_is_still_valid(self):
        url, _ = _query("en")
        validate_english_query_string(urlparse(url).query)


class TheReplayToolsMatchProductionTest(unittest.TestCase):
    def test_the_english_builder_does_not_ask_for_numerals(self):
        params = build_english_live_query_params()
        self.assertNotIn("numerals", params)
        self.assertEqual(params.get("smart_format"), "true")


if __name__ == "__main__":
    unittest.main()
