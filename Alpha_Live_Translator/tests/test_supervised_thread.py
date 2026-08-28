"""Acceptance tests for `alpha/utils/supervised_thread.py` (mitigation.md step 1).

Every test drives the real class. Nothing here reads live machine state -- the
counters and events are all created by the test, because a test that depends on
what else is running on the box passes alone and fails under load. That is not
hypothetical: `test_the_updater_does_not_report_its_own_interpreter` did exactly
that earlier in this project and reported as a build failure.

Backoff is set to milliseconds throughout so the suite does not sleep. The
supervisor's real default (0.5s -> 5s) is exercised only in the arithmetic, not
in wall-clock time.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.supervised_thread import (  # noqa: E402
    SupervisedThread,
    all_supervisor_snapshots,
    supervisor_health_summary,
)

FAST = {
    "backoff_initial_s": 0.001,
    "backoff_max_s": 0.004,
    "restart_window_s": 30.0,
}


def wait_until(predicate, timeout=3.0, interval=0.005):
    """Poll a condition instead of sleeping a fixed amount."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class Criterion1_AnExceptionDoesNotEndSupervision(unittest.TestCase):
    """An exception in the target does not end supervision; the target runs again."""

    def test_the_target_runs_again_after_it_raises(self):
        runs = []
        started = threading.Event()

        def target():
            runs.append(1)
            if len(runs) == 1:
                raise RuntimeError("first run explodes")
            started.set()
            supervisor.stop_event.wait(2.0)

        supervisor = SupervisedThread(
            target, name="t_criterion1", register=False, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()

        self.assertTrue(started.wait(3.0), "the target never ran a second time")
        self.assertGreaterEqual(len(runs), 2)
        self.assertTrue(supervisor.is_alive())

    def test_it_survives_several_consecutive_failures_within_budget(self):
        runs = []

        def target():
            runs.append(1)
            if len(runs) <= 3:
                raise ValueError(f"failure {len(runs)}")
            supervisor.stop_event.wait(2.0)

        supervisor = SupervisedThread(
            target, name="t_criterion1b", register=False, max_restarts=5, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()

        self.assertTrue(wait_until(lambda: len(runs) >= 4))
        self.assertFalse(supervisor.gave_up)
        self.assertTrue(supervisor.is_alive())


class Criterion2_RestartCountAndLastError(unittest.TestCase):
    """`restart_count` increments and `last_error` carries the exception."""

    def test_restart_count_increments_once_per_failure(self):
        runs = []

        def target():
            runs.append(1)
            if len(runs) <= 2:
                raise RuntimeError("boom")
            supervisor.stop_event.wait(2.0)

        supervisor = SupervisedThread(
            target, name="t_criterion2", register=False, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()

        self.assertTrue(wait_until(lambda: supervisor.restart_count >= 2))
        self.assertEqual(supervisor.restart_count, 2)

    def test_last_error_carries_the_exception_type_and_message(self):
        def target():
            raise KeyError("a-specific-key")

        supervisor = SupervisedThread(
            target, name="t_criterion2b", register=False, max_restarts=1, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()

        self.assertTrue(wait_until(lambda: supervisor.gave_up))
        self.assertIn("KeyError", supervisor.last_error)
        self.assertIn("a-specific-key", supervisor.last_error)

    def test_the_snapshot_reports_the_same_state(self):
        def target():
            raise RuntimeError("snapshot-me")

        supervisor = SupervisedThread(
            target, name="t_criterion2c", register=False, max_restarts=1, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()
        self.assertTrue(wait_until(lambda: supervisor.gave_up))

        snap = supervisor.snapshot()
        self.assertEqual(snap["name"], "t_criterion2c")
        self.assertTrue(snap["gave_up"])
        self.assertIn("snapshot-me", snap["last_error"])
        self.assertEqual(snap["last_error_type"], "RuntimeError")
        self.assertGreater(snap["last_restart_ts"], 0.0)


class Criterion3_ItStopsRestartingAfterTheCap(unittest.TestCase):
    """After the cap it stops restarting and reports `gave_up=True` rather than looping."""

    def test_it_gives_up_rather_than_spinning(self):
        runs = []

        def target():
            runs.append(1)
            raise RuntimeError("always fails")

        supervisor = SupervisedThread(
            target, name="t_criterion3", register=False, max_restarts=3, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()

        self.assertTrue(wait_until(lambda: supervisor.gave_up, timeout=5.0))
        self.assertTrue(wait_until(lambda: not supervisor.is_alive(), timeout=2.0))

        settled = len(runs)
        time.sleep(0.15)
        self.assertEqual(
            len(runs), settled, "it kept running the target after giving up"
        )

    def test_the_restart_count_stops_at_the_cap(self):
        def target():
            raise RuntimeError("always fails")

        supervisor = SupervisedThread(
            target, name="t_criterion3b", register=False, max_restarts=3, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()

        self.assertTrue(wait_until(lambda: supervisor.gave_up, timeout=5.0))
        self.assertEqual(supervisor.restart_count, 3)


class Criterion4_ACleanStopIsNotAFailure(unittest.TestCase):
    """A normal stop leaves `gave_up=False` and does not restart."""

    def test_stop_does_not_count_as_a_failure(self):
        runs = []

        def target():
            runs.append(1)
            supervisor.stop_event.wait(5.0)

        supervisor = SupervisedThread(
            target, name="t_criterion4", register=False, **FAST
        )
        supervisor.start()
        self.assertTrue(wait_until(lambda: len(runs) >= 1))

        supervisor.stop(2.0)
        self.assertFalse(supervisor.is_alive())
        self.assertFalse(supervisor.gave_up)
        self.assertEqual(supervisor.restart_count, 0)

        after = len(runs)
        time.sleep(0.1)
        self.assertEqual(len(runs), after, "it restarted after a clean stop")

    def test_a_target_that_returns_normally_is_not_restarted(self):
        """A6's watchdog breaks its own loop when finalize is done.

        Restarting there would resurrect a stop watchdog after the stop it was
        watching completed, so a clean return must end supervision.
        """
        runs = []

        def target():
            runs.append(1)
            return

        supervisor = SupervisedThread(
            target, name="t_criterion4b", register=False, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()

        self.assertTrue(wait_until(lambda: not supervisor.is_alive()))
        time.sleep(0.1)
        self.assertEqual(len(runs), 1, "a clean return was treated as a failure")
        self.assertFalse(supervisor.gave_up)
        self.assertTrue(supervisor.snapshot()["finished_cleanly"])

    def test_an_exception_raised_while_stopping_does_not_restart(self):
        runs = []
        entered = threading.Event()

        def target():
            runs.append(1)
            entered.set()
            supervisor.stop_event.wait(5.0)
            raise RuntimeError("raised on the way out")

        supervisor = SupervisedThread(
            target, name="t_criterion4c", register=False, **FAST
        )
        supervisor.start()
        self.assertTrue(entered.wait(3.0))

        supervisor.stop(2.0)
        time.sleep(0.1)
        self.assertEqual(len(runs), 1)
        self.assertEqual(supervisor.restart_count, 0)
        self.assertFalse(supervisor.gave_up)


class Criterion5_StopThenStartGenuinelyRestarts(unittest.TestCase):
    """`stop()` then `start()` restarts — the A4 defect.

    `performance_timeline.start_heartbeat` returns early when `_heartbeat is not
    None`, and `_heartbeat_stop` is never cleared, so its heartbeat could not be
    restarted even deliberately. Reproduced before the fix as:
        alive after start           : True
        alive after stop            : False
        alive after restart attempt : False
    """

    def test_the_thread_comes_back(self):
        runs = []

        def target():
            runs.append(1)
            supervisor.stop_event.wait(5.0)

        supervisor = SupervisedThread(
            target, name="t_criterion5", register=False, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)

        self.assertTrue(supervisor.start())
        self.assertTrue(wait_until(lambda: len(runs) == 1))
        self.assertTrue(supervisor.is_alive())

        supervisor.stop(2.0)
        self.assertFalse(supervisor.is_alive())

        self.assertTrue(supervisor.start(), "start() after stop() was a no-op")
        self.assertTrue(wait_until(lambda: len(runs) == 2))
        self.assertTrue(supervisor.is_alive())

    def test_the_stop_event_is_cleared_by_start(self):
        supervisor = SupervisedThread(
            lambda: None, name="t_criterion5b", register=False, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.stop_event.set()

        supervisor.start()
        self.assertTrue(
            wait_until(lambda: not supervisor.stop_event.is_set()),
            "start() left the stop event set, so the target could never run",
        )

    def test_start_is_refused_while_already_running(self):
        def target():
            supervisor.stop_event.wait(5.0)

        supervisor = SupervisedThread(
            target, name="t_criterion5c", register=False, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)

        self.assertTrue(supervisor.start())
        self.assertTrue(wait_until(supervisor.is_alive))
        self.assertFalse(supervisor.start(), "a second start() spawned a second thread")

    def test_a_supervisor_that_gave_up_can_be_started_again(self):
        """Giving up must be recoverable by an operator action, not another latch."""
        fail = {"yes": True}
        runs = []

        def target():
            runs.append(1)
            if fail["yes"]:
                raise RuntimeError("still broken")
            supervisor.stop_event.wait(5.0)

        supervisor = SupervisedThread(
            target, name="t_criterion5d", register=False, max_restarts=1, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()
        self.assertTrue(wait_until(lambda: supervisor.gave_up, timeout=5.0))

        fail["yes"] = False
        self.assertTrue(supervisor.start())
        self.assertTrue(wait_until(lambda: supervisor.is_alive()))
        self.assertFalse(supervisor.gave_up, "gave_up was not cleared by a restart")


class TheRegistryFeedsTheHealthSnapshot(unittest.TestCase):
    """Item 94's stall detector fired and could not tell anyone. Not again."""

    def test_a_registered_supervisor_appears_in_the_snapshot(self):
        supervisor = SupervisedThread(
            lambda: None, name="t_registry_probe", **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        snapshots = all_supervisor_snapshots()
        self.assertIn("t_registry_probe", snapshots)
        self.assertIn("restart_count", snapshots["t_registry_probe"])
        self.assertIn("gave_up", snapshots["t_registry_probe"])

    def test_the_summary_counts_restarts_and_names_who_gave_up(self):
        def target():
            raise RuntimeError("nope")

        supervisor = SupervisedThread(
            target, name="t_registry_gaveup", max_restarts=1, **FAST
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()
        self.assertTrue(wait_until(lambda: supervisor.gave_up, timeout=5.0))

        summary = supervisor_health_summary()
        self.assertIn("t_registry_gaveup", summary["supervised_gave_up"])
        self.assertGreaterEqual(summary["supervised_restart_count_total"], 1)
        self.assertGreaterEqual(summary["supervised_thread_count"], 1)


class ItDoesNotLogThroughTheModuleItSupervises(unittest.TestCase):
    """A1 is the crash guard's own writer. Logging its death through
    `crash_guard_log` would re-enter the broken module."""

    def test_the_supervisor_module_never_calls_crash_guard_log(self):
        source = (PROJECT_ROOT / "alpha" / "utils" / "supervised_thread.py").read_text(
            encoding="utf-8", errors="replace"
        )
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        body = code.split('"""', 2)[-1]
        self.assertNotIn("crash_guard_log(", body)


if __name__ == "__main__":
    unittest.main()
