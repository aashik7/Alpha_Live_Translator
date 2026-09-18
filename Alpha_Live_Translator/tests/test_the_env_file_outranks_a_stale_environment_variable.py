"""The `.env` beside the app outranks a stale variable in the machine's environment.

WHAT WAS BROKEN
---------------
`alpha/config.py` read the file with `load_dotenv(PROJECT_ROOT / ".env")`.
python-dotenv skips every name that is already in `os.environ` -- its
`DotEnv.set_as_environment_variables` is literally
`if k in os.environ and not self.override: continue` -- so a `DEEPGRAM_API_KEY`
left behind in the Windows user environment, set once and long forgotten, beat
the key in the file and nothing said so.

Measured on the real module, with the repo's own `.env` in place:

    DEEPGRAM_API_KEY=<junk>  ->  config.DEEPGRAM_API_KEY is the OS variable: True
    DEEPL_AUTH_KEY=<junk>    ->  config.DEEPL_AUTH_KEY    is the OS variable: True
                                 config.get_deepgram_key_status(): "configured"

That contradicts everything the app tells the operator. Items 29 and 30 print
this file's path and say to put a valid key on its `DEEPGRAM_API_KEY` line and
start Alpha again; the first-run dialog writes that same file; the installer
writes it too. On a machine with the stale variable every one of those is a
lie -- the file is edited, the app is restarted, and the rejected key comes
straight back, with no hint anywhere that the file is being ignored.

WHAT THESE TESTS PIN
--------------------
* the file's value is the one the app runs with, whatever the environment holds
* a name the file does NOT define still comes from the environment, so the
  documented fallback ("set DEEPGRAM_API_KEY in your environment or .env file")
  and a developer exporting a key in a shell both keep working
"""

import importlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv  # noqa: E402

ENV_FILE = PROJECT_ROOT / ".env"
STALE = "stale-environment-variable-that-must-not-win"
# Every name `alpha/config.py` reads out of the environment at import time.
KEY_NAMES = ("DEEPGRAM_API_KEY", "DEEPL_AUTH_KEY", "DEEPL_API_KEY")


def file_defines(name):
    """True when this tree's own `.env` carries a non-empty value for `name`."""
    if not ENV_FILE.is_file():
        return False
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        head, separator, value = line.partition("=")
        if separator and head.strip() == name and value.strip():
            return True
    return False


class TheFileIsTheAuthorityTest(unittest.TestCase):
    """Reloads the real `alpha.config`, and puts it back afterwards."""

    def setUp(self):
        saved = {name: os.environ.get(name) for name in KEY_NAMES}
        self.addCleanup(self._restore, saved)

    def _restore(self, saved):
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        # The keys are bound at import time, so the reloads in these tests
        # rebound them. Put the module back the way the rest of the suite
        # expects to find it.
        import alpha.config

        importlib.reload(alpha.config)

    def test_the_file_wins_over_a_variable_already_in_the_environment(self):
        defined = [name for name in ("DEEPGRAM_API_KEY", "DEEPL_AUTH_KEY") if file_defines(name)]
        if not defined:
            self.skipTest("this tree has no .env with a key in it for a variable to outrank")
        for name in defined:
            os.environ[name] = STALE

        import alpha.config as cfg

        importlib.reload(cfg)

        for name in defined:
            with self.subTest(name=name):
                self.assertNotEqual(
                    getattr(cfg, name),
                    STALE,
                    "a stale %s in the machine's environment beat the value in "
                    ".env -- the file the app tells the operator to edit, and "
                    "the one its own key dialog writes" % (name,),
                )

    def test_a_variable_still_supplies_a_name_the_file_does_not_define(self):
        """Replays the kwargs `alpha.config` really passes, against real dotenv.

        Two things at once, on purpose: that the module hands python-dotenv the
        file as the authority, and that doing so does not cost the documented
        environment fallback. Both are read off the production call rather than
        restated here, so a change to that call fails this test.
        """
        recorded = {}
        real_load_dotenv = dotenv.load_dotenv

        def record(path=None, *args, **kwargs):
            recorded["path"] = path
            recorded["kwargs"] = kwargs
            return real_load_dotenv(path, *args, **kwargs)

        with mock.patch.object(dotenv, "load_dotenv", record):
            import alpha.config as cfg

            importlib.reload(cfg)

        self.assertEqual(
            Path(recorded["path"]),
            ENV_FILE,
            "the module no longer reads the .env beside the app",
        )

        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("DEEPGRAM_API_KEY=the-value-in-the-file\n", encoding="utf-8")

            os.environ["DEEPGRAM_API_KEY"] = STALE
            real_load_dotenv(env_file, **recorded["kwargs"])
            self.assertEqual(
                os.environ["DEEPGRAM_API_KEY"],
                "the-value-in-the-file",
                "the file does define this name, so its value must be the one "
                "that ends up in the environment",
            )

            os.environ["DEEPL_AUTH_KEY"] = STALE
            real_load_dotenv(env_file, **recorded["kwargs"])
            self.assertEqual(
                os.environ["DEEPL_AUTH_KEY"],
                STALE,
                "the file does not define this name, so the environment must "
                "still supply it -- a developer exporting a key in a shell, and "
                'the app\'s own "set it in your environment or .env file"',
            )


if __name__ == "__main__":
    unittest.main()
