"""A build made with --no-keys ships no API keys and asks for them once.

WHY
---
The installer and the portable zip normally carry the delivery keys. A build for
sharing must carry none: whoever runs it pastes their own Deepgram and DeepL
keys on first start, and from then on the app behaves exactly as a keyed build.

The two halves tested here:

* `alpha/ui/key_setup.py` -- when to ask, and what the saved `.env` looks like.
  The dialog itself is not driven here; `write_env` is what it calls once the
  operator presses Save, and the gate is what decides whether it ever appears.
* `installer/build_installer.py --no-keys` -- no keys read, a `.needs-api-keys`
  marker in the bundle, and a portable zip with no `.env` in it at all.

THE GATE MATTERS MORE THAN THE DIALOG. `alpha/config.py` calls it while it is
still being imported, so a gate that said yes on a developer machine or in this
suite would block every run on a modal window. It is keyed on a marker file that
only a --no-keys build ships.
"""

import os
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import key_setup  # noqa: E402

REAL_DEEPGRAM = "a" * 40
REAL_DEEPL = "b" * 36 + ":fx"


class _NoPromptEnv(unittest.TestCase):
    """Never leave ALPHA_NO_KEY_PROMPT or the key variables changed behind."""

    def setUp(self):
        self._saved = {
            name: os.environ.get(name)
            for name in ("ALPHA_NO_KEY_PROMPT", "DEEPGRAM_API_KEY", "DEEPL_AUTH_KEY")
        }
        os.environ.pop("ALPHA_NO_KEY_PROMPT", None)
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TheGateDecidesWhenToAskTest(_NoPromptEnv):
    def setUp(self):
        super().setUp()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def mark(self):
        (self.root / key_setup.MARKER_NAME).write_text("keyless", encoding="utf-8")

    def test_a_developer_tree_is_never_asked(self):
        """No marker, no prompt -- this suite imports alpha.config constantly."""
        self.assertFalse(key_setup.should_prompt(self.root, "", ""))
        self.assertFalse(key_setup.should_prompt(self.root, REAL_DEEPGRAM, REAL_DEEPL))

    def test_a_keyless_build_with_no_keys_is_asked(self):
        self.mark()
        self.assertTrue(key_setup.should_prompt(self.root, "", ""))
        self.assertTrue(key_setup.should_prompt(self.root, REAL_DEEPGRAM, ""))
        self.assertTrue(key_setup.should_prompt(self.root, "", REAL_DEEPL))

    def test_once_the_keys_are_saved_it_stops_asking(self):
        self.mark()
        self.assertFalse(key_setup.should_prompt(self.root, REAL_DEEPGRAM, REAL_DEEPL))

    def test_a_placeholder_key_counts_as_missing(self):
        self.mark()
        self.assertTrue(key_setup.should_prompt(self.root, "your_deepgram_api_key_here", REAL_DEEPL))
        self.assertTrue(key_setup.should_prompt(self.root, REAL_DEEPGRAM, "your-deepl-auth-key"))

    def test_an_automated_run_can_turn_the_prompt_off(self):
        """The build's own smoke check imports the app with no display."""
        self.mark()
        os.environ["ALPHA_NO_KEY_PROMPT"] = "1"
        self.assertFalse(key_setup.should_prompt(self.root, "", ""))


class TheSavedEnvFileTest(_NoPromptEnv):
    def setUp(self):
        super().setUp()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / ".env"

    def read(self):
        return dict(
            line.split("=", 1)
            for line in self.env.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.startswith("#")
        )

    def test_it_writes_the_two_names_the_app_reads(self):
        key_setup.write_env(self.env, REAL_DEEPGRAM, REAL_DEEPL)
        saved = self.read()
        self.assertEqual(saved["DEEPGRAM_API_KEY"], REAL_DEEPGRAM)
        self.assertEqual(saved["DEEPL_AUTH_KEY"], REAL_DEEPL)

    def test_the_keys_are_live_in_this_process_too(self):
        """config.py re-reads os.environ straight after the dialog returns."""
        key_setup.write_env(self.env, REAL_DEEPGRAM, REAL_DEEPL)
        self.assertEqual(os.environ["DEEPGRAM_API_KEY"], REAL_DEEPGRAM)
        self.assertEqual(os.environ["DEEPL_AUTH_KEY"], REAL_DEEPL)

    def test_surrounding_settings_survive_a_rewrite(self):
        self.env.write_text(
            "DEEPL_API_PLAN=free\nDEEPGRAM_API_KEY=old\nDEEPL_API_KEY=legacy\n", encoding="utf-8"
        )
        key_setup.write_env(self.env, REAL_DEEPGRAM, REAL_DEEPL)
        saved = self.read()
        self.assertEqual(saved["DEEPL_API_PLAN"], "free", "an unrelated setting was dropped")
        self.assertEqual(saved["DEEPGRAM_API_KEY"], REAL_DEEPGRAM)
        self.assertNotIn(
            "DEEPL_API_KEY", saved, "the legacy alias would outrank the key just entered"
        )

    def test_whitespace_around_a_pasted_key_is_trimmed(self):
        key_setup.write_env(self.env, "  " + REAL_DEEPGRAM + "\t", " " + REAL_DEEPL + " ")
        self.assertEqual(self.read()["DEEPGRAM_API_KEY"], REAL_DEEPGRAM)


class TheBuildShipsNoKeysTest(unittest.TestCase):
    """installer/build_installer.py, driven directly."""

    def setUp(self):
        sys.path.insert(0, str(PROJECT_ROOT / "installer"))
        self.addCleanup(lambda: sys.path.remove(str(PROJECT_ROOT / "installer")))
        import build_installer

        self.build = build_installer
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = Path(self.tmp.name) / "bundle"
        for rel in ("python/pythonw.exe", "python/python314._pth", "app/main.py", "app/alpha/config.py", "Alpha.bat"):
            path = self.bundle / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stub", encoding="utf-8")

    @property
    def marker(self):
        return self.bundle / "app" / self.build.KEY_SETUP_MARKER

    def test_no_keys_reads_no_keys_at_all(self):
        """Without this it would fail the build on a machine with no keys file."""
        self.assertEqual(self.build.read_keys(no_keys=True), ("", ""))

    def test_no_keys_leaves_the_marker_the_app_looks_for(self):
        self.build.verify_bundle(self.bundle, no_keys=True)
        self.assertTrue(self.marker.is_file())
        self.assertEqual(self.build.KEY_SETUP_MARKER, key_setup.MARKER_NAME)

    def test_a_keyed_build_removes_a_marker_left_behind(self):
        """Same bundle directory is reused; a stale marker would prompt for keys it has."""
        self.marker.write_text("stale", encoding="utf-8")
        self.build.verify_bundle(self.bundle, no_keys=False)
        self.assertFalse(self.marker.is_file())

    def test_the_keyless_portable_zip_carries_no_env_file(self):
        self.build.verify_bundle(self.bundle, no_keys=True)
        output = Path(self.tmp.name) / "out"
        zip_path = self.build.write_portable_zip(self.bundle, output, "9.9.9", "", "")
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
        self.assertFalse([n for n in names if n.endswith("/app/.env")], "a keyless build shipped an .env")
        self.assertTrue([n for n in names if n.endswith("/app/" + key_setup.MARKER_NAME)])
        self.assertIn("ships WITHOUT API keys", (output / "README-PORTABLE.txt").read_text(encoding="utf-8"))

    def test_a_keyed_portable_zip_still_carries_the_keys(self):
        """Guard: the normal delivery path is unchanged."""
        output = Path(self.tmp.name) / "out2"
        zip_path = self.build.write_portable_zip(self.bundle, output, "9.9.9", REAL_DEEPGRAM, REAL_DEEPL)
        with zipfile.ZipFile(zip_path) as archive:
            env = archive.read("Alpha Live Translator/app/.env").decode("utf-8")
        self.assertIn("DEEPGRAM_API_KEY=" + REAL_DEEPGRAM, env)
        self.assertIn("DEEPL_AUTH_KEY=" + REAL_DEEPL, env)
        self.assertNotIn("ships WITHOUT API keys", (output / "README-PORTABLE.txt").read_text(encoding="utf-8"))


class TheInstallerScriptSkipsTheEnvWithoutKeysTest(unittest.TestCase):
    def test_write_env_file_returns_early_when_the_key_define_is_empty(self):
        """Inno would otherwise write an .env of empty values, which reads as a broken build."""
        iss = (PROJECT_ROOT / "installer" / "alpha.iss").read_text(encoding="utf-8-sig")
        body = iss.split("procedure WriteEnvFile();", 1)[1].split("end;", 1)[0]
        guard = body.index("if '{#DeepgramKey}' = '' then")
        self.assertLess(guard, body.index("EnvPath :="), "the guard must run before the file is written")
        self.assertIn("exit;", body[guard:guard + 120])


if __name__ == "__main__":
    unittest.main()
