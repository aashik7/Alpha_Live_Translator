"""Item 49 -- the diagnostic bundle has to say which language the window was in.

`collect_logs.py` reports the active UI language into `summary.txt`, and it
works that out by hand rather than importing `alpha.ui.strings`: the collector
has to keep working when the app itself cannot start, which is exactly when
somebody reaches for it.

That duplication drifted. The function stopped after the saved choice and
reported "never changed from the shipped default", which was true until the app
learned to follow the Windows display language and quietly wrong afterwards. On
a Japanese Windows with nothing chosen it would have claimed the window was
English while it was Japanese -- the single question a bundle exists to answer.

These tests pin the order against `strings._resolve_language`, so the next time
one moves the other cannot silently stay behind.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import collect_logs  # noqa: E402
from alpha.ui import strings  # noqa: E402


class TestTheReportedLanguage(unittest.TestCase):
    def setUp(self):
        # Every case decides its own environment; inheriting one makes the
        # result depend on the machine the suite happens to run on.
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        os.environ.pop(collect_logs.__dict__.get("ENV_VAR", "ALPHA_UI_LANGUAGE"), None)

    def _report(self, *, saved=None, windows="en", override=None):
        if override is None:
            os.environ.pop("ALPHA_UI_LANGUAGE", None)
        else:
            os.environ["ALPHA_UI_LANGUAGE"] = override
        payload = "{}" if saved is None else '{"ui_language": "%s"}' % saved

        class _Fake:
            def read_text(self, **kwargs):
                if saved is None:
                    raise FileNotFoundError
                return payload

        with mock.patch.object(collect_logs, "windows_ui_language", return_value=windows), \
                mock.patch.object(collect_logs, "APP_ROOT") as root:
            root.__truediv__ = lambda self_, name: _Fake()
            return collect_logs.ui_language()

    def test_the_environment_variable_wins(self):
        self.assertIn("ja", self._report(override="ja", saved="en", windows="en"))
        self.assertIn("ALPHA_UI_LANGUAGE", self._report(override="ja"))

    def test_a_saved_choice_beats_windows(self):
        report = self._report(saved="en", windows="ja")
        self.assertTrue(report.startswith("en"), report)
        self.assertIn("chosen in the app", report)

    def test_windows_decides_when_nothing_was_chosen(self):
        """The case that used to be reported wrongly."""
        report = self._report(saved=None, windows="ja")
        self.assertTrue(report.startswith("ja"), report)
        self.assertIn("Windows", report)
        self.assertNotIn("shipped default", report)

    def test_it_says_so_when_windows_cannot_be_read(self):
        report = self._report(saved=None, windows="")
        self.assertTrue(report.startswith("en"), report)
        self.assertIn("could not be read", report)


class TestTheDuplicatedDetectionMatches(unittest.TestCase):
    """The collector copies this logic on purpose; the copy has to agree."""

    def test_both_read_the_same_windows_language(self):
        self.assertEqual(
            collect_logs.windows_ui_language(),
            strings._windows_ui_language(),
            "collect_logs and alpha.ui.strings disagree about the OS language",
        )

    def test_both_treat_the_same_lcids_as_japanese(self):
        for lcid, expected in ((0x0411, "ja"), (0x0409, "en"), (0x0407, "en"),
                               (0x0812, "en"), (0x1011, "ja")):
            with self.subTest(lcid=hex(lcid)):
                with mock.patch("ctypes.windll.kernel32.GetUserDefaultUILanguage",
                                return_value=lcid, create=True):
                    self.assertEqual(collect_logs.windows_ui_language(), expected)
                    self.assertEqual(strings._windows_ui_language(), expected)


class TestTheSummaryCarriesIt(unittest.TestCase):
    def test_the_language_line_is_in_the_summary(self):
        text = collect_logs.summary(["runs/x  (1 files)"], True)
        self.assertIn("language", text.lower())


if __name__ == "__main__":
    unittest.main()
