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

LEFT BEHIND BY ITEM 36 (open defect l, 2026-09-29)
-------------------------------------------------
`run_english_accuracy_experiment.py` still sent `numerals=true` in both its
pre-recorded and its streaming request, so the experiment measured a request
production no longer makes; and the validator's allowlist still accepted
`numerals`, so a request that re-added it passed. Pinned below: both
experiment requests, captured at the network call, and the validator refusing
`numerals` while `validate_deepgram_english_request.py` still passes.
"""

import io
import sys
import tempfile
import threading
import types
import unittest
import wave
from pathlib import Path
from unittest import mock
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


def _tiny_wav(folder):
    path = Path(folder) / "clip.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return path


class TheAccuracyExperimentMatchesProductionTest(unittest.TestCase):
    """The request each experiment path really sends, captured at the call."""

    def setUp(self):
        import run_english_accuracy_experiment as experiment

        self.experiment = experiment
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wav = _tiny_wav(tmp.name)
        patcher = mock.patch.object(experiment, "deepgram_api_key", lambda: "test-key")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_prerecorded_request_does_not_ask_for_numerals(self):
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            raise self.experiment.error.HTTPError(
                req.full_url, 400, "stub", {}, io.BytesIO(b"")
            )

        with mock.patch.object(self.experiment.request, "urlopen", fake_urlopen):
            result = self.experiment.prerecorded_transcribe(self.wav)
        self.assertEqual(len(seen), 1, "the request never reached the network call")
        query = parse_qs(urlparse(seen[0]).query)
        self.assertNotIn("numerals", query, "numerals=true turns 'third quarter' into '3rd 0.25'")
        self.assertEqual(query.get("smart_format"), ["true"])
        self.assertFalse(result["ok"])

    def test_the_streaming_request_does_not_ask_for_numerals(self):
        seen = []

        class _FakeApp:
            def __init__(self, url, **_kwargs):
                seen.append(url)

            def run_forever(self, **_kwargs):
                return None

        fake = types.ModuleType("websocket")
        fake.WebSocketApp = _FakeApp
        with mock.patch.dict(sys.modules, {"websocket": fake}):
            result = self.experiment.streaming_transcribe_realtime(
                self.wav, pace_realtime=False
            )
        self.assertEqual(len(seen), 1, "the socket was never opened")
        query = parse_qs(urlparse(seen[0]).query)
        self.assertNotIn("numerals", query, "numerals=true turns 'third quarter' into '3rd 0.25'")
        self.assertEqual(query.get("smart_format"), ["true"])
        self.assertNotIn("numerals", result["request_params"])


class TheValidatorRefusesNumeralsTest(unittest.TestCase):
    def test_a_request_that_re_adds_numerals_is_refused(self):
        url, _ = _query("en")
        with self.assertRaises(ValueError) as caught:
            validate_english_query_string(urlparse(url).query + "&numerals=true")
        self.assertIn("numerals", str(caught.exception))

    def test_the_validation_script_still_passes(self):
        import validate_deepgram_english_request as script

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(script, "ROOT", Path(tmp.name)), mock.patch(
            "builtins.print"
        ):
            code = script.main()
        report = (
            Path(tmp.name)
            / "troubleshooting"
            / "validation"
            / "english_only_improvement"
            / "ENGLISH_DEEPGRAM_REQUEST_VALIDATION.json"
        ).read_text(encoding="utf-8")
        self.assertEqual(code, 0, report)
        self.assertIn("live_run_057f111e_mirror_numerals_rejected", report)


if __name__ == "__main__":
    unittest.main()
