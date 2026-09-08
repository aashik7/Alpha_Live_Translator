"""Rebinding the runtime writers must not deadlock on its own lock.

WHAT WAS BROKEN
---------------
    # rebind_all_runtime_writers
    with _lock:
        _writers_rebound = True
        for writer_name in list(_writer_registry.keys()):
            rebind_runtime_writer(writer_name, run_folder)

    # rebind_runtime_writer
    def rebind_runtime_writer(writer_name, run_folder):
        with _lock:                     # the same non-reentrant threading.Lock

`_lock` is `threading.Lock()`, confirmed non-reentrant at runtime, and the
registry it iterates is populated by ordinary use -- `get_log_path` registers a
writer on every call. So the loop body blocked forever the moment a single
writer existed, and the `JapaneseAccuracyLogWriter` thread blocked with it,
waiting on the same lock.

WHY THE APP STILL STARTED, AND WHY THAT MATTERS
-----------------------------------------------
`create_run_folder` reaches the rebind only in an `else` branch, and
`STARTUP_RECOVERY_MODE` and `EVIDENCE_SAFE_MODE` are both True, so Start takes
the deferring branch and logs PENDING_WRITER_REBIND_DEFERRED_NON_BLOCKING. The
deadlock was skipped, not fixed.

That deferral is why `troubleshooting/runs/_pending/logs/*` grows without bound
across sessions: the rebind that would move the writers OUT of `_pending` is
the deadlocking one, so every session keeps appending to the shared pending
file. It reached 358 MB on the development machine.

It also stayed reachable through `preflight_upload_evidence`, which calls the
same function with only a UI-thread guard -- the evidence-packaging path a
client is asked to run when something has gone wrong.

THE FIX
-------
Take the lock once. Snapshot the registry keys under it, release, then call
`rebind_runtime_writer` for each -- that function takes the lock itself and is
written to be called unlocked.

Deliberately NOT an `RLock`: this module has 35 `with _lock` sites, and
switching the primitive would legitimise re-entry at all of them rather than
remove one bug.

WHY THIS RUNS IN A SUBPROCESS
-----------------------------
A deadlock here wedges `_lock` for the whole interpreter, so an in-process
version of this test hangs the entire suite instead of failing it -- teardown
and every later test that touches the module block on the same lock. A
subprocess with a timeout turns that into an ordinary, readable failure.
"""

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import alpha.utils.troubleshooting_paths as tp  # noqa: E402

TIMEOUT_S = 25.0

# The `_pending` roots are pointed at an empty tree so a hang can only be the
# lock and never the migration, which on a real machine copies hundreds of
# megabytes and would make this measure the wrong thing.
DRIVER = '''
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, {repo!r})
import alpha.utils.troubleshooting_paths as tp

tmp = Path(tempfile.mkdtemp(prefix="alpha_rebind_test_"))
tp._troubleshooting_root = tmp / "troubleshooting"
(tp._troubleshooting_root / "runs" / "_pending" / "logs").mkdir(parents=True, exist_ok=True)
folder = tmp / "runs" / "run-test"
(folder / "logs").mkdir(parents=True, exist_ok=True)

{populate}

names_before = sorted(tp._writer_registry)
tp.rebind_all_runtime_writers(folder)

# The writer this driver registered itself, not an arbitrary one: the
# function registers others of its own along the way, and some of those are
# created AFTER the active folder moves, so they read "bound" rather than
# "rebound" and say nothing about whether the loop ran.
target = "log:japanese_accuracy"
entry = tp._writer_registry.get(target)
print("RESULT " + json.dumps({{
    "registered": names_before,
    "rebind_status": (entry or {{}}).get("rebind_status"),
    "current_path": str((entry or {{}}).get("current_path") or ""),
    "run_folder": str(folder),
    "rebound_count": sum(
        1 for e in tp._writer_registry.values()
        if str(folder) in str(e.get("current_path") or "")
    ),
}}))
'''


def run_driver(populate):
    script = DRIVER.format(repo=str(PROJECT_ROOT), populate=textwrap.dedent(populate).strip())
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=TIMEOUT_S + 10.0,
    )


class RebindDoesNotDeadlockTest(unittest.TestCase):
    def test_the_lock_is_not_reentrant(self):
        """The premise. If this ever becomes an RLock the rest is moot."""
        self.assertNotIn(
            "RLock",
            type(tp._lock).__name__,
            "the fix must not be 'make it an RLock' -- 35 sites in this module "
            "take this lock, and that would legitimise re-entry at all of them",
        )

    def _result(self, proc):
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT "):
                import json

                return json.loads(line[len("RESULT "):])
        return None

    def test_rebinding_returns_with_a_populated_registry(self):
        try:
            proc = run_driver('tp.get_log_path("japanese_accuracy")')
        except subprocess.TimeoutExpired:
            self.fail(
                "rebind_all_runtime_writers did not return: it holds _lock "
                "across a loop whose body takes _lock again, and the registry "
                "is populated by any ordinary get_log_path() call"
            )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        result = self._result(proc)
        self.assertIsNotNone(result, proc.stdout[-2000:])
        self.assertTrue(result["registered"], "no writer registered; test is vacuous")

    def test_the_writers_are_actually_rebound(self):
        """Not deadlocking is not enough; the rebind has to do its job."""
        try:
            proc = run_driver('tp.get_log_path("japanese_accuracy")')
        except subprocess.TimeoutExpired:
            self.fail("rebind_all_runtime_writers did not return")
        result = self._result(proc)
        self.assertIsNotNone(result, proc.stdout[-2000:])
        # The path is the invariant, not the status word. The writer's own
        # thread re-registers it moments later through `get_log_path`, which
        # stamps "bound" rather than "rebound" -- with the correct new path.
        # Asserting on the word would fail on a rebind that worked.
        self.assertIn(
            result["run_folder"],
            result["current_path"],
            "the writer is still pointed outside the run folder",
        )
        self.assertIn(result["rebind_status"], ("rebound", "bound"))
        self.assertGreater(
            result["rebound_count"], 1,
            "only one writer moved; the loop did not get through the registry",
        )

    def test_an_empty_registry_is_still_fine(self):
        try:
            proc = run_driver("pass")
        except subprocess.TimeoutExpired:
            self.fail("rebind_all_runtime_writers did not return on an empty registry")
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
