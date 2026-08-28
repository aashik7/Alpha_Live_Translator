"""The in-place updater, driven end to end against a simulated client install.

These are not unit tests of helpers. Each one builds a throwaway install that has
the same shape as a real one -- `app\\` with an `.env`, a `user_settings.json` and
a populated `troubleshooting\\runs\\` beside the code -- runs the real
`apply_update.py` as a subprocess exactly as `Update_Alpha.bat` runs it, and then
looks at the resulting tree.

The one that matters most is `test_the_delivery_keys_survive`. `app\\.env` holds
the DEEPGRAM_API_KEY and DEEPL_AUTH_KEY, it is written only by the Inno installer
(installer/alpha.iss `WriteEnvFile`), and it exists nowhere in the source tree. An
updater that mirrors `app\\` naively deletes it, and the client is left with an
install that starts and cannot transcribe, with nothing to restore from. Same
shape, less permanently, for `troubleshooting\\`: deleting it destroys the
evidence that makes a failure on a machine we cannot reach diagnosable at all.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPDATER = PROJECT_ROOT / "tools" / "update_payload" / "apply_update.py"

if str(PROJECT_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class UpdaterHarness(unittest.TestCase):
    """A fake install and a fake payload, both on disk, driving the real script."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.install = self.root / "Alpha Live Translator"
        self.app = self.install / "app"
        self.payload = self.root / "update" / "app"

        # An install has to look like one or the updater refuses it.
        write(self.install / "python" / "pythonw.exe", "not really an exe")
        write(self.install / "python" / "python.exe", "not really an exe")

        # --- the shipped app, at the OLD version -------------------------
        write(self.app / "main.py", "print('old main')\n")
        write(self.app / "alpha" / "constants.py", 'APP_VERSION = "1.0.0"\n')
        write(self.app / "alpha" / "transcription" / "assembler.py", "OLD = True\n")
        write(self.app / "alpha" / "unchanged.py", "STABLE = 1\n")
        # A module that was dropped between builds. This is the "jegulo extra
        # ase chole jabe" half of the request.
        write(self.app / "alpha" / "removed_in_new_build.py", "DEAD = True\n")
        # Stale bytecode beside a module that is about to change.
        write(self.app / "alpha" / "__pycache__" / "assembler.cpython-314.pyc", "stale")

        # --- runtime state that must survive -----------------------------
        write(self.app / ".env", "DEEPGRAM_API_KEY=secret-dg\nDEEPL_AUTH_KEY=secret-dl\n")
        write(self.app / "user_settings.json", '{"ui_language": "ja"}\n')
        write(self.app / "troubleshooting" / "runs" / "run-001" / "logs" / "debug.log", "evidence\n")
        write(self.app / "troubleshooting" / "latest" / "pointer.txt", "run-001\n")
        write(self.app / "logs" / "console-20260826.log", "console\n")
        write(self.app / "debug" / "MIGRATION_NOTICE.txt", "notice\n")

        # --- the new build ------------------------------------------------
        write(self.payload / "main.py", "print('new main')\n")
        write(self.payload / "alpha" / "constants.py", 'APP_VERSION = "2.0.0"\n')
        write(self.payload / "alpha" / "transcription" / "assembler.py", "OLD = False\nFIXED = True\n")
        write(self.payload / "alpha" / "unchanged.py", "STABLE = 1\n")
        write(self.payload / "alpha" / "added_in_new_build.py", "NEW = True\n")

        self.addCleanup(self._tmp.cleanup)

    def run_updater(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(UPDATER), str(self.install),
             "--payload", str(self.payload), "--force", *extra],
            capture_output=True, text=True, timeout=180,
        )

    def read(self, rel: str) -> str:
        return (self.app / rel).read_text(encoding="utf-8")


class TheUpdateAppliesTest(UpdaterHarness):
    def test_it_succeeds(self):
        result = self.run_updater()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("UPDATE COMPLETE", result.stdout)

    def test_changed_files_are_replaced(self):
        self.run_updater()
        self.assertEqual("print('new main')\n", self.read("main.py"))
        self.assertIn("FIXED = True", self.read("alpha/transcription/assembler.py"))
        self.assertIn('APP_VERSION = "2.0.0"', self.read("alpha/constants.py"))

    def test_new_files_are_added(self):
        self.run_updater()
        self.assertTrue((self.app / "alpha" / "added_in_new_build.py").is_file())

    def test_files_no_longer_in_the_build_are_removed(self):
        self.run_updater()
        self.assertFalse(
            (self.app / "alpha" / "removed_in_new_build.py").exists(),
            "a module dropped between builds is still on the client machine",
        )

    def test_stale_bytecode_is_dropped(self):
        self.run_updater()
        self.assertFalse(
            (self.app / "alpha" / "__pycache__").exists(),
            "stale .pyc left beside a replaced module is a way to half-apply an update",
        )

    def test_a_second_run_is_a_no_op(self):
        r"""Caught a real bug: the record of what was applied was written INSIDE
        app\, so the next run classified it as a file no longer part of the app
        and deleted it. Every run reported changes, and the record never survived
        long enough to be read."""
        self.run_updater()
        result = self.run_updater()
        self.assertEqual(0, result.returncode)
        self.assertIn("Already up to date", result.stdout)

    def test_the_update_record_survives_the_next_update(self):
        self.run_updater()
        self.run_updater()
        self.assertTrue((self.install / "UPDATE_APPLIED.json").is_file())


class RuntimeStateSurvivesTest(UpdaterHarness):
    def test_the_delivery_keys_survive(self):
        """`.env` is written only by the installer and exists in no other copy."""
        self.run_updater()
        self.assertTrue(
            (self.app / ".env").is_file(),
            "the update deleted app\\.env -- the client's Deepgram and DeepL keys "
            "are written only by the installer and exist nowhere else, so the "
            "install is now permanently unable to transcribe",
        )
        self.assertIn("secret-dg", self.read(".env"))
        self.assertIn("secret-dl", self.read(".env"))

    def test_the_operator_settings_survive(self):
        self.run_updater()
        self.assertEqual('{"ui_language": "ja"}\n', self.read("user_settings.json"))

    def test_every_saved_run_survives(self):
        self.run_updater()
        self.assertEqual(
            "evidence\n",
            self.read("troubleshooting/runs/run-001/logs/debug.log"),
            "the update destroyed the run evidence, which is the only thing that "
            "makes a failure on a machine we cannot reach diagnosable",
        )
        self.assertTrue((self.app / "troubleshooting" / "latest" / "pointer.txt").is_file())

    def test_the_other_two_runtime_folders_survive(self):
        """collect_logs.py collects ("logs", "troubleshooting/latest", "debug")."""
        self.run_updater()
        self.assertEqual("console\n", self.read("logs/console-20260826.log"))
        self.assertEqual("notice\n", self.read("debug/MIGRATION_NOTICE.txt"))

    def test_an_empty_runtime_folder_is_not_swept_away(self):
        """An empty troubleshooting\\ is still where the app writes."""
        shutil.rmtree(self.app / "troubleshooting")
        (self.app / "troubleshooting").mkdir()
        self.run_updater()
        self.assertTrue((self.app / "troubleshooting").is_dir())


class ItReportsBeforeItWritesTest(UpdaterHarness):
    def test_dry_run_changes_nothing(self):
        result = self.run_updater("--dry-run")
        self.assertEqual(0, result.returncode)
        self.assertIn("nothing was written", result.stdout)
        self.assertEqual("print('old main')\n", self.read("main.py"))
        self.assertTrue((self.app / "alpha" / "removed_in_new_build.py").is_file())

    def test_the_plan_names_every_change(self):
        result = self.run_updater("--dry-run")
        self.assertIn("update  main.py", result.stdout.replace("\\", "/").replace("/", "\\"))
        self.assertIn("removed_in_new_build.py", result.stdout)
        self.assertIn("added_in_new_build.py", result.stdout)

    def test_a_backup_is_left_behind(self):
        self.run_updater()
        backups = list(self.install.glob("app_backup_*"))
        self.assertEqual(1, len(backups), f"expected one backup, got {backups}")
        self.assertEqual(
            "print('old main')\n",
            (backups[0] / "main.py").read_text(encoding="utf-8"),
        )

    def test_the_backup_does_not_copy_the_evidence(self):
        """troubleshooting\\ can be gigabytes; copying it aside is not free."""
        self.run_updater()
        backup = next(iter(self.install.glob("app_backup_*")))
        self.assertFalse((backup / "troubleshooting").exists())
        self.assertFalse((backup / ".env").exists())

    def test_it_records_what_it_did(self):
        self.run_updater()
        record = json.loads(
            (self.install / "UPDATE_APPLIED.json").read_text(encoding="utf-8")
        )
        self.assertEqual("2.0.0", record["app_version"])
        self.assertEqual(1, record["files_removed"])


class ItRefusesRatherThanGuessTest(UpdaterHarness):
    def test_it_refuses_a_folder_that_is_not_an_install(self):
        empty = self.root / "somewhere else"
        empty.mkdir()
        result = subprocess.run(
            [sys.executable, str(UPDATER), str(empty), "--payload", str(self.payload), "--force"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("does not look like an Alpha install", result.stdout)

    def test_it_refuses_a_missing_payload(self):
        result = subprocess.run(
            [sys.executable, str(UPDATER), str(self.install),
             "--payload", str(self.root / "nope"), "--force"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("payload is missing", result.stdout)

    def test_it_refuses_an_incomplete_payload(self):
        (self.payload / "main.py").unlink()
        result = self.run_updater()
        self.assertEqual(2, result.returncode)
        self.assertIn("incomplete", result.stdout)

    def test_it_refuses_an_install_with_no_embedded_python(self):
        shutil.rmtree(self.install / "python")
        result = self.run_updater()
        self.assertEqual(2, result.returncode)
        self.assertIn("no embedded Python", result.stdout)


class TheRunningCheckDoesNotSeeItselfTest(unittest.TestCase):
    r"""Reproduced by rehearsing the real .bat against a real copy of the build.

    `Update_Alpha.bat` runs the updater with the install's OWN
    `python\python.exe` -- on purpose, so nothing has to be installed on the
    client machine first. The first version of the running-instance check
    matched on path prefix alone, so it found that interpreter, concluded Alpha
    was running, and refused. Not an edge case: it would have failed 100% of
    runs on 100% of machines, and the package would have been useless on
    arrival.

    The FIRST version of this test was itself defective, and it is worth saying
    why rather than quietly replacing it. It called `running_instances()` for
    real and asserted that `sys.executable` was absent from the result. That
    holds only while no other process happens to be running the same
    interpreter -- so it passed alone and failed the moment a second pytest run
    shared it. A test that depends on what else is running on the machine is not
    a regression test; it is a coin flip that reports as a build failure.

    It now feeds the check a synthetic process table containing our own PID and
    a foreign one at the same path, which is exactly the condition the bug was
    about and is independent of the machine.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("apply_update", UPDATER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _with_process_table(self, module, rows):
        """Drive running_instances() against a fixed process list."""
        from unittest.mock import patch

        class _Result:
            stdout = "\n".join(rows)

        return patch.object(module.subprocess, "run", return_value=_Result())

    def test_the_updater_does_not_report_its_own_interpreter(self):
        module = self._module()
        install = Path(sys.executable).resolve().parent
        own = str(Path(sys.executable).resolve())

        with self._with_process_table(module, [f"{os.getpid()}|{own}"]):
            live = module.running_instances(install)

        self.assertEqual(
            [], live,
            "the updater sees the interpreter it is running on as a live Alpha "
            "session and will refuse every update on every machine",
        )

    def test_a_genuinely_running_instance_is_still_reported(self):
        """The PID filter must not blind the check to a real live session."""
        module = self._module()
        install = Path(sys.executable).resolve().parent
        own = str(Path(sys.executable).resolve())
        other = str(install / "pythonw.exe")
        foreign_pid = os.getpid() + 100000          # cannot collide with ours

        with self._with_process_table(
            module, [f"{os.getpid()}|{own}", f"{foreign_pid}|{other}"]
        ):
            live = module.running_instances(install)

        self.assertEqual(
            [other], live,
            "a real running Alpha was filtered out along with the updater's own "
            "process -- the guard would pass while the app is live",
        )

    def test_a_check_that_could_not_run_is_not_a_clean_result(self):
        from unittest.mock import patch

        module = self._module()
        with patch.object(module.subprocess, "run", side_effect=OSError("no powershell")):
            self.assertIsNone(
                module.running_instances(Path(sys.executable).resolve().parent),
                "a check that could not run reported as 'nothing is running'",
            )


class ItRollsBackTest(UpdaterHarness):
    def test_a_failure_mid_update_restores_the_previous_tree(self):
        """Injected at the verify step: a half-applied app is not a survivable state."""
        broken = self.root / "broken_apply_update.py"
        source = UPDATER.read_text(encoding="utf-8")
        source = source.replace(
            "def verify(payload: Path, app_dir: Path) -> list[str]:",
            "def verify(payload: Path, app_dir: Path) -> list[str]:\n"
            "    raise RuntimeError('injected failure')",
            1,
        )
        broken.write_text(source, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(broken), str(self.install),
             "--payload", str(self.payload), "--force"],
            capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(3, result.returncode, result.stdout)
        self.assertIn("restored", result.stdout)
        self.assertEqual(
            "print('old main')\n", self.read("main.py"),
            "the app was left half-updated after a failure",
        )
        self.assertTrue(
            (self.app / "alpha" / "removed_in_new_build.py").is_file(),
            "a file removed before the failure was not put back",
        )
        self.assertIn("secret-dg", self.read(".env"))
        self.assertEqual("evidence\n", self.read("troubleshooting/runs/run-001/logs/debug.log"))


class ThePackagedUpdateIsCompleteTest(unittest.TestCase):
    """What the builder emits has to be runnable as-is by someone with no repo."""

    def test_the_builder_ships_everything_the_bat_needs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "tools" / "build_update_package.py"),
                 "--out", tmp],
                capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            package = next(iter(Path(tmp).glob("AlphaLiveTranslator_Update_*")))
            for required in ("Update_Alpha.bat", "apply_update.py",
                             "README_FIRST.txt", "UPDATE_MANIFEST.json",
                             "app/main.py", "app/alpha/constants.py"):
                self.assertTrue(
                    (package / required).is_file(),
                    f"the delivered package has no {required}",
                )

    def test_the_payload_carries_no_runtime_state(self):
        """Shipping a stale .env or someone else's logs to a client machine."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "tools" / "build_update_package.py"),
                 "--out", tmp],
                capture_output=True, text=True, timeout=300, check=True,
            )
            package = next(iter(Path(tmp).glob("AlphaLiveTranslator_Update_*")))
            for forbidden in (".env", "user_settings.json", "troubleshooting", "logs"):
                self.assertFalse(
                    (package / "app" / forbidden).exists(),
                    f"the update package ships app/{forbidden} to the client",
                )
            self.assertEqual(
                [], list((package / "app").rglob("__pycache__")),
                "the update package ships compiled bytecode",
            )

    def test_the_zip_is_named_for_the_real_version(self):
        """`with_suffix` on a dotted version silently renames the build."""
        import tempfile

        from build_update_package import app_version  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "tools" / "build_update_package.py"),
                 "--out", tmp, "--zip"],
                capture_output=True, text=True, timeout=300, check=True,
            )
            archives = list(Path(tmp).glob("*.zip"))
            self.assertEqual(1, len(archives))
            self.assertEqual(
                f"AlphaLiveTranslator_Update_{app_version()}.zip", archives[0].name
            )


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    unittest.main()
