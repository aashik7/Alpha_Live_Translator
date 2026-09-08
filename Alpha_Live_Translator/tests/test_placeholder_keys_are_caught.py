"""Every placeholder key the project ships must be caught before Start.

WHAT WAS BROKEN
---------------
`get_deepgram_key_status()` compared the key against `PLACEHOLDER_API_KEYS` and
a placeholder blocked Start with a sentence the operator could act on.
`has_deepl_api_key()` was `bool(DEEPL_AUTH_KEY or DEEPL_API_KEY)` -- a bare
truthiness test that never consulted that set. For DeepL the set was dead code.

Measured against the real `preflight_credentials`, every placeholder string the
project ships passed, including `your_deepl_api_key_here`, which exists for no
other purpose:

    DEEPL=your_deepl_api_key_here      has_deepl=True  preflight=[]
    DEEPL=your_deepl_auth_key_here     has_deepl=True  preflight=[]   (.env.example)

Worse, `PLACEHOLDER_API_KEYS` contained `your_deepl_api_key_here` while
`.env.example` ships `your_deepl_auth_key_here`. The DeepL entry was dead twice
over: never consulted, and the wrong spelling anyway.

And the build template `installer/keys.local.ini.example` ships hyphenated
values -- `your-deepgram-api-key`, `your-deepl-auth-key`. `read_keys()` rejects
only an EMPTY value, so a placeholder compiles into a real installer, and the
runtime check does not know that spelling either. Of the four placeholder
strings the project ships across two template files, exactly one was caught.

WHAT THESE TESTS PIN
--------------------
* every placeholder the project actually ships is recognised, in both spellings
* a placeholder DeepL key produces a Start-time problem, and one that does NOT
  block Start -- the Deepgram/DeepL asymmetry is deliberate, and a session with
  no translation is degraded rather than broken
* a real key still produces no problem at all
* the build refuses a placeholder instead of shipping it to a client
"""

import importlib
import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REAL_KEY = "a" * 40

# The strings this project actually ships, from .env.example and
# installer/keys.local.ini.example.
SHIPPED_DEEPL_PLACEHOLDERS = (
    "your_deepl_auth_key_here",
    "your_deepl_api_key_here",
    "your-deepl-auth-key",
)
SHIPPED_DEEPGRAM_PLACEHOLDERS = (
    "your_deepgram_api_key_here",
    "your-deepgram-api-key",
)


class _Env:
    """Reload `alpha.config` and `service_status` under a chosen environment."""

    def __init__(self, **env):
        self.env = env
        self.saved = {}

    def __enter__(self):
        for k, v in self.env.items():
            self.saved[k] = os.environ.get(k)
            # An "absent" key is set to "" rather than removed. The repo has a
            # real `.env`, and `load_dotenv` fills in any name that is NOT
            # already in os.environ -- so popping the variable hands the test
            # the developer's own key and the assertion measures nothing.
            os.environ[k] = "" if v is None else v
        import alpha.config as cfg
        from alpha.utils import service_status as ss

        importlib.reload(cfg)
        importlib.reload(ss)
        return cfg, ss

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import alpha.config as cfg
        from alpha.utils import service_status as ss

        importlib.reload(cfg)
        importlib.reload(ss)
        return False


class PlaceholderKeysTest(unittest.TestCase):
    def test_every_shipped_deepl_placeholder_is_recognised(self):
        for value in SHIPPED_DEEPL_PLACEHOLDERS:
            with self.subTest(value=value):
                with _Env(DEEPGRAM_API_KEY=REAL_KEY, DEEPL_AUTH_KEY=value,
                          DEEPL_API_KEY=None) as (cfg, ss):
                    self.assertEqual(
                        cfg.get_deepl_key_status(), "placeholder",
                        "%r is a string this project ships and nothing else "
                        "uses it" % (value,),
                    )
                    codes = [p.code for p in ss.preflight_credentials()]
                    self.assertIn(
                        "deepl_key_placeholder", codes,
                        "Start gave no signal for a placeholder DeepL key; it "
                        "will surface mid-session as auth_failed, and the "
                        "indicator will blame the provider",
                    )

    def test_every_shipped_deepgram_placeholder_is_recognised(self):
        for value in SHIPPED_DEEPGRAM_PLACEHOLDERS:
            with self.subTest(value=value):
                with _Env(DEEPGRAM_API_KEY=value, DEEPL_AUTH_KEY=REAL_KEY,
                          DEEPL_API_KEY=None) as (cfg, ss):
                    self.assertEqual(cfg.get_deepgram_key_status(), "placeholder")

    def test_a_placeholder_deepl_key_does_not_block_start(self):
        """The asymmetry is deliberate: no translation is degraded, not broken."""
        with _Env(DEEPGRAM_API_KEY=REAL_KEY,
                  DEEPL_AUTH_KEY="your_deepl_auth_key_here",
                  DEEPL_API_KEY=None) as (cfg, ss):
            problems = ss.preflight_credentials()
            blocking = [p.code for p in problems if p.blocks_start]
            self.assertEqual(
                blocking, [], "a missing translation key must not refuse Start"
            )

    def test_a_real_key_pair_produces_no_problem(self):
        with _Env(DEEPGRAM_API_KEY=REAL_KEY, DEEPL_AUTH_KEY="b" * 36,
                  DEEPL_API_KEY=None) as (cfg, ss):
            self.assertEqual(cfg.get_deepl_key_status(), "configured")
            self.assertEqual(ss.preflight_credentials(), [])

    def test_an_absent_deepl_key_still_reports_missing_not_placeholder(self):
        with _Env(DEEPGRAM_API_KEY=REAL_KEY, DEEPL_AUTH_KEY=None,
                  DEEPL_API_KEY=None) as (cfg, ss):
            self.assertEqual(cfg.get_deepl_key_status(), "missing")
            codes = [p.code for p in ss.preflight_credentials()]
            self.assertIn("deepl_key_missing", codes)
            self.assertNotIn("deepl_key_placeholder", codes)

    def test_has_deepl_api_key_still_answers_the_old_question(self):
        """Three production call sites read it; it must keep its meaning."""
        with _Env(DEEPGRAM_API_KEY=REAL_KEY, DEEPL_AUTH_KEY="b" * 36,
                  DEEPL_API_KEY=None) as (cfg, ss):
            self.assertTrue(cfg.has_deepl_api_key())
        with _Env(DEEPGRAM_API_KEY=REAL_KEY, DEEPL_AUTH_KEY=None,
                  DEEPL_API_KEY=None) as (cfg, ss):
            self.assertFalse(cfg.has_deepl_api_key())


class TheBuildRefusesAPlaceholderTest(unittest.TestCase):
    """A build made from an unedited template must not reach a client."""

    def _read_keys(self, deepgram, deepl):
        sys.path.insert(0, str(PROJECT_ROOT / "installer"))
        try:
            import build_installer

            importlib.reload(build_installer)
            saved = build_installer.KEYS_FILE
            build_installer.KEYS_FILE = Path("does-not-exist.ini")
            os.environ["ALPHA_DEEPGRAM_KEY"] = deepgram
            os.environ["ALPHA_DEEPL_KEY"] = deepl
            try:
                return build_installer.read_keys()
            finally:
                build_installer.KEYS_FILE = saved
                os.environ.pop("ALPHA_DEEPGRAM_KEY", None)
                os.environ.pop("ALPHA_DEEPL_KEY", None)
        finally:
            sys.path.remove(str(PROJECT_ROOT / "installer"))

    def test_a_placeholder_key_stops_the_build(self):
        with self.assertRaises(SystemExit) as caught:
            self._read_keys("your-deepgram-api-key", "b" * 36)
        self.assertIn("placeholder", str(caught.exception).lower())

    def test_real_keys_still_build(self):
        deepgram, deepl = self._read_keys(REAL_KEY, "b" * 36)
        self.assertEqual(deepgram, REAL_KEY)

    def test_an_empty_key_still_stops_the_build(self):
        """The check that already worked must keep working."""
        with self.assertRaises(SystemExit):
            self._read_keys("", "b" * 36)


if __name__ == "__main__":
    unittest.main()
