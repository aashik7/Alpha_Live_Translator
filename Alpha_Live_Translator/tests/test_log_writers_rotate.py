"""Every evidence writer must bound its own file.

WHAT WAS BROKEN
---------------
Rotation lived in exactly one place: `_JsonlWriter._rotate_if_needed` in
`evidence_jsonl.py`, which stats the file before every append and shifts
`.1`…`.5` backups when it crosses `LOG_MAX_FILE_MB`. The other four writers --
`japanese_accuracy_log`, `async_debug_log`, `freeze_guard_log` and
`diagnostic_test_log` -- each opened their file in append mode and never
consulted size or age. Measured at runtime: zero `stat()` calls on their paths
across 5,000 real events each, against 875 for `evidence_jsonl`.

`japanese_accuracy_log`'s single reference to `LOG_ROTATION_ENABLED` only emits
the string "LOG_ROTATION_ACTIVE", which is why reading this area by grep is
misleading.

Per-session growth is bounded only by session length -- about 34 MB for a
two-hour meeting, 136 MB for an eight-hour day. The file that is genuinely
unbounded is `troubleshooting/runs/_pending/logs/*`, which every session appends
to during the bootstrap window and which nothing ever truncates, rotates or
prunes. It reached 358 MB on the development machine, and because
`rebind_all_runtime_writers` migrates the whole pile on every rebind, that is
latency on a path the user waits on at Start, not merely disk.

Two of the writers hold one file handle open for the whole session and reopen
only when `get_log_path()` returns a different path, so a rotate-on-open hook
would fire at most once per session and would not fix them; they need a running
byte count.

WHAT THESE TESTS PIN
--------------------
* each of the four writers rotates once its file crosses the cap, and the live
  file comes back under it
* the rotation is the one that already exists and is already proven, not a
  second implementation
* the shared `_pending` pile is bounded at startup, since that is the only file
  unbounded ACROSS sessions rather than merely within one
* nothing rotates when the file is small -- a writer that churned backups on
  every line would be worse than the leak
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.evidence_jsonl import rotate_if_needed  # noqa: E402


class TheSharedRotationIsReusableTest(unittest.TestCase):
    """The rotation that already works, lifted so the others can call it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.tmp.name) / "evidence.log"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_file_over_the_cap_is_rotated(self):
        from alpha.constants import LOG_MAX_FILE_MB

        self.path.write_bytes(b"x" * (LOG_MAX_FILE_MB * 1024 * 1024 + 16))
        self.assertTrue(rotate_if_needed(self.path))
        self.assertFalse(self.path.exists(), "the live file was not moved aside")
        self.assertTrue(
            self.path.with_suffix(self.path.suffix + ".1").exists(),
            "no .1 backup was produced",
        )

    def test_a_small_file_is_left_alone(self):
        """A writer that churned backups every line would be worse."""
        self.path.write_text("one line\n", encoding="utf-8")
        self.assertFalse(rotate_if_needed(self.path))
        self.assertTrue(self.path.exists())

    def test_a_missing_file_is_not_an_error(self):
        self.assertFalse(rotate_if_needed(self.path))


class EachWriterBoundsItsOwnFileTest(unittest.TestCase):
    """Driven through each writer's real public entry point."""

    WRITERS = (
        ("alpha.utils.async_debug_log", "log_runtime_debug_event", "async_debug"),
        ("alpha.utils.freeze_guard_log", "freeze_guard_log", "freeze_guard"),
        ("alpha.utils.japanese_accuracy_log", "jp_accuracy_log", "japanese_accuracy"),
        ("alpha.utils.diagnostic_test_log", "diagnostic_test_log", "diagnostic_test"),
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        import alpha.utils.troubleshooting_paths as tp

        self.tp = tp
        self._saved_root = tp._troubleshooting_root
        tp._troubleshooting_root = Path(self.tmp.name) / "troubleshooting"
        (tp._troubleshooting_root / "runs" / "_pending" / "logs").mkdir(
            parents=True, exist_ok=True
        )

    def tearDown(self):
        self.tp._troubleshooting_root = self._saved_root
        self.tmp.cleanup()

    def test_every_writer_consults_the_shared_rotation(self):
        """Reuse, not a second implementation.

        Checked on the module rather than by behaviour because forcing four
        different writers past a 50 MB cap in a unit test would write 200 MB.
        """
        import ast

        for module_name, _, _ in self.WRITERS:
            with self.subTest(module=module_name):
                path = PROJECT_ROOT / (module_name.replace(".", "/") + ".py")
                tree = ast.parse(path.read_text(encoding="utf-8"))
                names = {
                    (n.func.attr if isinstance(n.func, ast.Attribute)
                     else getattr(n.func, "id", ""))
                    for n in ast.walk(tree) if isinstance(n, ast.Call)
                }
                self.assertIn(
                    "rotate_if_needed",
                    names,
                    "%s still appends without ever consulting size; measured "
                    "zero stat() calls across 5,000 events" % module_name,
                )

    def test_a_writer_rotates_a_file_that_is_already_over_the_cap(self):
        """One writer, driven for real, with the cap patched small."""
        from alpha.utils import evidence_jsonl as ev
        from alpha.utils import freeze_guard_log as fgl

        # Patched on `evidence_jsonl`, which is where the value is actually
        # read. Patching `alpha.constants` and reloading the writer does
        # nothing: the rotation binds the cap at ITS own import time.
        saved = ev.LOG_MAX_FILE_MB
        try:
            path = self.tp.get_log_path("freeze_guard")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * (2 * 1024 * 1024))
            ev.LOG_MAX_FILE_MB = 1
            fgl.freeze_guard_log("ROTATION_PROBE")
            self.assertTrue(
                path.with_suffix(path.suffix + ".1").exists(),
                "the writer appended to an oversized file without rotating it",
            )
            self.assertLess(path.stat().st_size, 1024 * 1024)
        finally:
            ev.LOG_MAX_FILE_MB = saved


class ThePendingPileIsBoundedTest(unittest.TestCase):
    """`runs/_pending/logs/*` is the only file unbounded ACROSS sessions."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        import alpha.utils.troubleshooting_paths as tp

        self.tp = tp
        self._saved_root = tp._troubleshooting_root
        tp._troubleshooting_root = Path(self.tmp.name) / "troubleshooting"
        self.pending = tp._troubleshooting_root / "runs" / "_pending" / "logs"
        self.pending.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tp._troubleshooting_root = self._saved_root
        self.tmp.cleanup()

    def test_an_oversized_pending_log_is_rotated_at_startup(self):
        from alpha.constants import LOG_MAX_FILE_MB

        fat = self.pending / "japanese_accuracy.log"
        fat.write_bytes(b"x" * (LOG_MAX_FILE_MB * 1024 * 1024 + 16))
        self.tp.bound_pending_logs()
        self.assertLess(
            fat.stat().st_size if fat.exists() else 0,
            LOG_MAX_FILE_MB * 1024 * 1024,
            "the shared bootstrap log is still unbounded across sessions; it "
            "reached 358 MB on the development machine and is copied in full "
            "on every rebind",
        )

    def test_a_small_pending_log_is_left_alone(self):
        small = self.pending / "async_debug.log"
        small.write_text("one line\n", encoding="utf-8")
        self.tp.bound_pending_logs()
        self.assertTrue(small.exists())
        self.assertEqual(small.read_text(encoding="utf-8"), "one line\n")

    def test_no_pending_folder_is_not_an_error(self):
        import shutil

        shutil.rmtree(self.pending)
        self.tp.bound_pending_logs()


if __name__ == "__main__":
    unittest.main()
