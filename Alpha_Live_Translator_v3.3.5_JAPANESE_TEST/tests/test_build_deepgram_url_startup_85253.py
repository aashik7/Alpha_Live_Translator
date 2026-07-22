"""No-network startup regression for _build_deepgram_url() (8.5.25.3)."""

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.config import DEEPGRAM_API_KEY  # noqa: E402
from alpha.constants import (  # noqa: E402
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
)
from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402


class BuildUrlHost(DeepgramClientMixin):
    def __init__(self) -> None:
        self._listen_language = "ja"
        self._jp_keyterms_fallback_used = False


class TestBuildDeepgramUrlStartup85253(unittest.TestCase):
    def test_build_deepgram_url_no_network(self) -> None:
        url = BuildUrlHost()._build_deepgram_url()
        self.assertTrue(url.startswith("wss://api.deepgram.com/v1/listen?"))

        qs = parse_qs(urlparse(url).query, keep_blank_values=True)
        single = {k: (v[0] if v else "") for k, v in qs.items()}

        self.assertEqual(single.get("model"), "nova-3")
        self.assertEqual(DEEPGRAM_MODEL, "nova-3")
        self.assertEqual(single.get("language"), "ja")
        self.assertEqual(DEEPGRAM_LANGUAGE, "ja")
        self.assertEqual(single.get("endpointing"), str(DEEPGRAM_ENDPOINTING_MS))
        self.assertEqual(single.get("utterance_end_ms"), str(DEEPGRAM_UTTERANCE_END_MS))
        self.assertEqual(single.get("sample_rate"), "16000")
        self.assertEqual(single.get("channels"), "1")
        self.assertEqual(JAPANESE_STT_PROFILE, "no_diarize")
        self.assertNotIn("diarize_model", single)
        self.assertEqual(JAPANESE_KEYTERM_PROFILE, "business_japanese")
        self.assertNotIn("sk-", url)
        if DEEPGRAM_API_KEY:
            self.assertNotIn(DEEPGRAM_API_KEY, url)


if __name__ == "__main__":
    unittest.main()
