"""mitigation.md step 2: the five converted loops, driven by real exceptions.

Every test here injects a fault into the REAL loop and asserts the loop is still
running afterwards with a non-zero `restart_count`. None of them assert on the
supervisor in isolation -- that is what `test_supervised_thread.py` is for, and
the verification protocol in mitigation.md §3 asks for both.

Against the pre-fix code every test in this file fails, because pre-fix there was
no supervisor at all: the thread ended on the first exception and the
`_writer_started` / `_heartbeat is not None` latches made restart impossible.

Nothing here reads live machine state. Faults are injected by pointing a real
writer at a real unusable path, or by making a real collaborator raise.
"""

import pathlib
import sys
import tempfile
import threading
import time
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.supervised_thread import SupervisedThread  # noqa: E402

FAST = {
    "backoff_initial_s": 0.001,
    "backoff_max_s": 0.004,
    "restart_window_s": 30.0,
}


def wait_until(predicate, timeout=5.0, interval=0.005):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def unusable_path(tmp: pathlib.Path) -> pathlib.Path:
    """A path whose parent is a regular file, so mkdir/open raises for real."""
    blocker = tmp / "iam_a_file"
    blocker.write_text("x", encoding="utf-8")
    return blocker / "sub" / "written.log"


class A1_CrashGuardWriterRestarts(unittest.TestCase):
    """`crash_guard_log._writer_loop` — was: one OSError ended it forever."""

    def setUp(self):
        from alpha.utils import crash_guard_log as cg

        self.cg = cg
        self._saved = (cg._writer_supervisor, cg._shutdown_requested, cg._LOG_DIR, cg._LOG_FILE)
        self.addCleanup(self._restore)

    def _restore(self):
        cg = self.cg
        supervisor = cg._writer_supervisor
        if supervisor is not None and supervisor is not self._saved[0]:
            try:
                supervisor.stop(1.0)
            except Exception:
                pass
        (
            cg._writer_supervisor,
            cg._shutdown_requested,
            cg._LOG_DIR,
            cg._LOG_FILE,
        ) = self._saved

    def test_it_restarts_and_reopens_after_a_real_write_failure(self):
        cg = self.cg
        tmp = pathlib.Path(tempfile.mkdtemp())
        bad = unusable_path(tmp)

        # A fresh supervisor over the REAL _writer_loop, pointed at a path the
        # OS refuses. The handle is opened inside the target, so each restart
        # genuinely reopens -- that is the A1 fix.
        cg._writer_supervisor = None
        cg._shutdown_requested = False
        cg._LOG_DIR = bad.parent
        cg._LOG_FILE = bad

        supervisor = SupervisedThread(
            cg._writer_loop, name="A1TestWriter", register=False, max_restarts=50, **FAST
        )
        cg._writer_supervisor = supervisor
        supervisor.start()

        self.assertTrue(
            wait_until(lambda: supervisor.restart_count >= 2),
            f"the writer did not restart (restarts={supervisor.restart_count})",
        )
        self.assertTrue(supervisor.is_alive(), "the writer thread died permanently")

        # Repair the path: the next restart must reopen successfully and stay up.
        good = tmp / "good"
        cg._LOG_DIR = good
        cg._LOG_FILE = good / "crash_guard.log"
        self.assertTrue(
            wait_until(lambda: (good / "crash_guard.log").exists() or supervisor.is_alive())
        )
        cg.crash_guard_log("A1_AFTER_RECOVERY", probe=True)
        self.assertTrue(wait_until(lambda: (good / "crash_guard.log").exists(), timeout=6.0))
        self.assertTrue(supervisor.is_alive())

    def test_the_start_gate_is_liveness_not_a_latch(self):
        """`_writer_started = True` was never reset, so restart was impossible."""
        source = (PROJECT_ROOT / "alpha" / "utils" / "crash_guard_log.py").read_text(
            encoding="utf-8", errors="replace"
        )
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        self.assertNotIn("_writer_started = True", code)
        self.assertIn("supervisor.is_alive()", code)

    def test_a_dropped_line_is_counted_rather_than_silent(self):
        stats = self.cg.get_crash_guard_writer_stats()
        self.assertIn("crash_guard_dropped_line_count", stats)
        self.assertIn("crash_guard_writer_restart_count", stats)
        self.assertIn("crash_guard_writer_gave_up", stats)


class A2_DiagnosticWriterRestarts(unittest.TestCase):
    """`diagnostic_test_log._writer_loop` — same latch, same death."""

    def setUp(self):
        from alpha.utils import diagnostic_test_log as dt

        self.dt = dt
        self._saved_resolve = dt._resolve_log_file
        self.addCleanup(setattr, dt, "_resolve_log_file", self._saved_resolve)

    def test_it_restarts_after_a_real_open_failure_and_recovers(self):
        dt = self.dt
        dt.diag_init()
        self.assertTrue(wait_until(lambda: dt._writer_supervisor is not None))
        supervisor = dt._writer_supervisor
        before = supervisor.restart_count

        tmp = pathlib.Path(tempfile.mkdtemp())
        bad = unusable_path(tmp)
        dt._resolve_log_file = lambda: bad
        for index in range(3):
            dt.diag_log("test", f"A2_BREAK_{index}", {})
            time.sleep(0.15)

        self.assertTrue(
            wait_until(lambda: supervisor.restart_count > before, timeout=6.0),
            "the diagnostic writer did not restart",
        )
        self.assertTrue(supervisor.is_alive(), "the diagnostic writer died permanently")

        dt._resolve_log_file = self._saved_resolve
        dt.diag_log("test", "A2_RECOVERED", {})
        time.sleep(0.2)
        self.assertTrue(supervisor.is_alive())
        self.assertFalse(supervisor.gave_up)

    def test_the_oldest_line_drop_is_counted(self):
        """The drop is deliberate; its invisibility was the defect."""
        stats = self.dt.get_diagnostic_writer_stats()
        self.assertIn("diagnostic_dropped_line_count", stats)
        self.assertIn("diagnostic_writer_restart_count", stats)

    def test_the_start_gate_is_liveness_not_a_latch(self):
        source = (PROJECT_ROOT / "alpha" / "utils" / "diagnostic_test_log.py").read_text(
            encoding="utf-8", errors="replace"
        )
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        self.assertNotIn("_writer_started = True", code)
        self.assertIn("supervisor.is_alive()", code)


class A3_WasapiDeviceWatchRestarts(unittest.TestCase):
    """`wasapi._wasapi_device_watch_worker` — item 73's detector.

    Blast radius is the largest of the six: once dead, the app stops noticing
    the default endpoint moved, which is the item 80 audio-loss class.
    """

    def _host(self, reads):
        from alpha.audio.wasapi import WasapiCaptureMixin

        class Host:
            _wasapi_device_watch_worker = WasapiCaptureMixin._wasapi_device_watch_worker

            def __init__(self):
                self._stop_event = threading.Event()
                self._wasapi_default_endpoint_baseline = "BASE"
                self._wasapi_device_change_reported = False
                self.changed = []
                self.restored = []

            def _read_default_endpoint_id(self):
                return reads()

            def _report_default_device_changed(self, baseline, current):
                self.changed.append((baseline, current))

            def _report_default_device_restored(self):
                self.restored.append(True)

        return Host()

    def setUp(self):
        from alpha.audio import default_endpoint

        self.default_endpoint = default_endpoint
        self._saved = (default_endpoint.com_initialize_mta, default_endpoint.com_uninitialize)
        default_endpoint.com_initialize_mta = lambda: True
        default_endpoint.com_uninitialize = lambda: None

        def restore():
            (
                self.default_endpoint.com_initialize_mta,
                self.default_endpoint.com_uninitialize,
            ) = self._saved

        self.addCleanup(restore)

    def test_a_com_failure_no_longer_ends_device_detection(self):
        calls = {"n": 0}

        def reads():
            calls["n"] += 1
            if calls["n"] <= 3:
                raise OSError("COM hiccup")
            return "BASE"

        host = self._host(reads)
        supervisor = SupervisedThread(
            lambda: host._wasapi_device_watch_worker(poll_seconds=0.01),
            name="A3TestWatch",
            register=False,
            max_restarts=50,
            **FAST,
        )
        self.addCleanup(lambda: (host._stop_event.set(), supervisor.stop(1.0)))
        supervisor.start()

        self.assertTrue(
            wait_until(lambda: supervisor.restart_count >= 3),
            f"the watch thread did not restart (restarts={supervisor.restart_count})",
        )
        self.assertTrue(supervisor.is_alive(), "the device watch died permanently")
        self.assertTrue(
            wait_until(lambda: calls["n"] > 4),
            "it restarted but stopped polling the endpoint",
        )

    def test_a_clean_stop_ends_it_without_a_restart(self):
        host = self._host(lambda: "BASE")
        supervisor = SupervisedThread(
            lambda: host._wasapi_device_watch_worker(poll_seconds=0.01),
            name="A3TestWatchStop",
            register=False,
            **FAST,
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()
        self.assertTrue(wait_until(supervisor.is_alive))

        host._stop_event.set()
        self.assertTrue(wait_until(lambda: not supervisor.is_alive()))
        self.assertEqual(supervisor.restart_count, 0)
        self.assertFalse(supervisor.gave_up)

    def test_the_debounce_and_the_unlatch_still_work(self):
        """A restart must not cost the two-reads debounce or the un-latch."""
        sequence = ["OTHER", "OTHER", "BASE"]
        index = {"i": 0}

        def reads():
            i = index["i"]
            index["i"] += 1
            return sequence[i] if i < len(sequence) else "BASE"

        host = self._host(reads)
        supervisor = SupervisedThread(
            lambda: host._wasapi_device_watch_worker(poll_seconds=0.01),
            name="A3TestDebounce",
            register=False,
            **FAST,
        )
        self.addCleanup(lambda: (host._stop_event.set(), supervisor.stop(1.0)))
        supervisor.start()

        # Two consecutive OTHER reads report exactly once, then BASE un-latches.
        self.assertTrue(wait_until(lambda: host.changed and host.restored))
        self.assertEqual(len(host.changed), 1, "reported more than once per device")
        self.assertFalse(host._wasapi_device_change_reported, "the un-latch was lost")

    def test_the_swallowing_except_is_gone(self):
        source = (PROJECT_ROOT / "alpha" / "audio" / "wasapi.py").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertNotIn("Device watch stopped", source)


class A4_PerformanceTimelineHeartbeatRestarts(unittest.TestCase):
    """The pre-fix measurement was: start True, stop False, restart False."""

    def _timeline(self):
        from alpha.utils.performance_timeline import PerformanceTimeline

        tmp = pathlib.Path(tempfile.mkdtemp())
        return PerformanceTimeline(run_id="a4test", output_path=tmp / "pt.json")

    def test_stop_then_start_restarts_the_heartbeat(self):
        timeline = self._timeline()
        self.addCleanup(timeline.stop_heartbeat)

        timeline.start_heartbeat(interval_s=0.01)
        self.assertTrue(wait_until(lambda: timeline._heartbeat.is_alive()))

        timeline.stop_heartbeat()
        self.assertTrue(wait_until(lambda: not timeline._heartbeat.is_alive()))

        timeline.start_heartbeat(interval_s=0.01)
        self.assertTrue(
            wait_until(lambda: timeline._heartbeat.is_alive()),
            "restart was a no-op -- the A4 defect is still present",
        )

    def test_an_exception_from_progress_does_not_end_the_heartbeat(self):
        """Drives the PRODUCTION `start_heartbeat`, not a supervisor the test built.

        An earlier draft of this test wrapped the loop in its own
        `SupervisedThread` and passed against the pre-fix code, which proves
        nothing -- exactly the failure mitigation.md §3 criterion 1 describes.
        """
        timeline = self._timeline()
        self.addCleanup(timeline.stop_heartbeat)
        failures = {"n": 0}
        real = timeline.progress

        def flaky(*args, **kwargs):
            failures["n"] += 1
            if failures["n"] <= 2:
                raise RuntimeError("progress exploded")
            return real(*args, **kwargs)

        timeline.progress = flaky
        timeline.start_heartbeat(interval_s=0.01)

        self.assertTrue(
            wait_until(lambda: getattr(timeline._heartbeat, "restart_count", 0) >= 2),
            "the heartbeat did not restart after progress() raised",
        )
        self.assertTrue(timeline._heartbeat.is_alive())
        self.assertTrue(
            wait_until(lambda: failures["n"] > 3),
            "it restarted but stopped calling progress()",
        )

    def test_the_early_return_gate_is_liveness_now(self):
        source = (PROJECT_ROOT / "alpha" / "utils" / "performance_timeline.py").read_text(
            encoding="utf-8", errors="replace"
        )
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        self.assertNotIn("if self._heartbeat is not None:\n            return", code)
        self.assertIn("supervisor.is_alive()", code)


class A6_StopFreezeWatchdogRestarts(unittest.TestCase):
    """Runs during stop, which is exactly when a freeze needs reporting."""

    def setUp(self):
        from alpha.utils import stop_finalize_worker as sfw

        self.sfw = sfw
        self._saved_snapshot = sfw._host_snapshot
        self.addCleanup(setattr, sfw, "_host_snapshot", self._saved_snapshot)
        with sfw._state_lock:
            self._saved_state = dict(sfw._stop_state)
        self.addCleanup(self._restore_state)

    def _restore_state(self):
        with self.sfw._state_lock:
            self.sfw._stop_state.update(self._saved_state)

    def test_an_exception_in_the_loop_no_longer_ends_the_watchdog(self):
        sfw = self.sfw
        calls = {"n": 0}

        def flaky_snapshot(host):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("snapshot exploded during stop")
            return {}

        sfw._host_snapshot = flaky_snapshot
        with sfw._state_lock:
            sfw._stop_state["worker_done"] = False
            sfw._stop_state["start_mono"] = time.monotonic()
            sfw._stop_state["last_step_begin_mono"] = time.monotonic()
            sfw._stop_state["last_step_name"] = "test"
            sfw._stop_state["ui_callback_returned"] = True
            sfw._stop_state["worker_started"] = True

        # The PRODUCTION spawn path, not a supervisor this test built: an
        # earlier draft did the latter and passed pre-fix, proving nothing.
        with sfw._state_lock:
            sfw._stop_state["watchdog_thread"] = None
        sfw._start_watchdog(None)
        with sfw._state_lock:
            supervisor = sfw._stop_state["watchdog_thread"]
        self.addCleanup(lambda: getattr(supervisor, "stop", lambda *_: None)(1.0))

        self.assertTrue(
            hasattr(supervisor, "restart_count"),
            "_start_watchdog did not create a supervised thread",
        )
        self.assertTrue(
            wait_until(lambda: supervisor.restart_count >= 2, timeout=8.0),
            f"the stop watchdog did not restart (restarts={supervisor.restart_count})",
        )
        self.assertTrue(supervisor.is_alive())

        with sfw._state_lock:
            sfw._stop_state["worker_done"] = True
        self.assertTrue(wait_until(lambda: not supervisor.is_alive(), timeout=6.0))

    def test_worker_done_is_a_clean_exit_that_is_never_resurrected(self):
        """A supervised restart must not bring the watchdog back after finalize."""
        sfw = self.sfw
        sfw._host_snapshot = lambda host: {}
        with sfw._state_lock:
            sfw._stop_state["worker_done"] = True

        supervisor = SupervisedThread(
            lambda: sfw._watchdog_loop(None),
            name="A6TestWatchdogDone",
            register=False,
            **FAST,
        )
        self.addCleanup(supervisor.stop, 1.0)
        supervisor.start()

        self.assertTrue(wait_until(lambda: not supervisor.is_alive()))
        time.sleep(0.15)
        self.assertEqual(supervisor.restart_count, 0)
        self.assertFalse(supervisor.gave_up)
        self.assertTrue(supervisor.snapshot()["finished_cleanly"])


class TheSupervisorStateReachesTheHealthSnapshot(unittest.TestCase):
    """mitigation.md §3 criterion 4 — required for A1-A4."""

    def test_restart_count_and_gave_up_are_in_the_health_payload(self):
        from alpha.utils.crash_guard_log import crash_guard_log
        from alpha.utils.session_progress import build_long_session_health_payload

        crash_guard_log("HEALTH_PAYLOAD_PROBE")
        payload = build_long_session_health_payload(None)

        for key in (
            "supervised_thread_count",
            "supervised_restart_count_total",
            "supervised_gave_up",
            "crash_guard_writer_restart_count",
            "crash_guard_writer_gave_up",
        ):
            self.assertIn(key, payload, f"{key} never reaches LAST_HEALTH_SNAPSHOT.json")

        self.assertIn("supervised_threads", payload)
        self.assertIsInstance(payload["supervised_threads"], dict)


if __name__ == "__main__":
    unittest.main()
