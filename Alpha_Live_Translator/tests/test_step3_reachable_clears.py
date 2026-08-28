"""mitigation.md step 3 — the two flag clears, and the repair that only needed scheduling.

Three defects, one shape: state goes one way and the path back is either absent
or wired somewhere it can never run.

* **A5** `async_debug_log` already HAS a correct repair,
  `ensure_async_logger_healthy_non_blocking()`. It resets the started flag, drops
  the dead thread handle and respawns the writer. It was called from exactly one
  place -- `main.py:59`, at startup, before the writer can have died. Meanwhile
  `_check_queue_health()`, which runs on every enqueue, already DETECTED the dead
  writer and emitted `ASYNC_LOG_WRITER_STALLED` -- then stopped, sixty lines above
  the repair. Recovery that is reachable, correct, and wired to the one moment it
  is guaranteed to be unnecessary.

* **B1** `set_degraded_logging_mode(True)` had three call sites.
  `set_degraded_logging_mode(False)` had zero. One queue spike degraded verbose
  logging for the rest of the session, long after the queue drained.

* **B2** `_quota_disabled` and `_accepting` were cleared only in `__init__` /
  `reset_session` / `start`, and no public re-arm existed. For genuinely
  exhausted quota that is right; the gap is that a transient `quota_exceeded`, or
  a top-up mid-meeting, also required restarting the session.

The throttling assertions below are not decoration. `_check_queue_health()` runs
on EVERY enqueue and `emergency_sync_write()` is a synchronous disk write. An
earlier draft of the step 3 plan proposed calling the repair from the session
watchdog tick instead -- 2.0 s intervals, an unconditional sync write inside the
repair, ~1100 needless synchronous writes on a 37-minute session. A reliability
fix that ships a performance regression is not a fix, so the cost of the recovery
path is pinned here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _FakeClock:
    """Monotonic time under the test's control.

    Every window in this feature is measured in wall time. Sleeping through them
    would make the suite slow and, worse, timing-dependent -- the failure mode
    that already produced one machine-dependent test in this repo.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class A5TheRepairIsCalledWhereTheProblemIsDetectedTest(unittest.TestCase):
    def setUp(self) -> None:
        from alpha.utils import async_debug_log as adl

        self.adl = adl
        self._saved = {
            "degraded": adl._degraded_mode,
            "snapshot": adl._last_health_snapshot_mono,
        }
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        self.adl._degraded_mode = self._saved["degraded"]
        self.adl._last_health_snapshot_mono = self._saved["snapshot"]
        self.adl._reset_writer_recovery_state()

    def _dead_writer(self):
        """The exact condition `_check_queue_health` already tests for."""

        class _Dead:
            @staticmethod
            def is_alive() -> bool:
                return False

        return patch.multiple(
            self.adl, _writer_started=True, _writer_thread=_Dead(), create=False
        )

    def test_a_dead_writer_triggers_the_existing_repair(self):
        adl = self.adl
        adl._reset_writer_recovery_state()
        with self._dead_writer(), \
                patch.object(adl, "ensure_async_logger_healthy_non_blocking") as repair, \
                patch.object(adl, "emergency_sync_write"):
            adl._check_queue_health()

        self.assertEqual(
            1, repair.call_count,
            "the dead writer was detected and reported, and the repair that "
            "would fix it -- already written, already correct -- was not called",
        )

    def test_a_live_writer_triggers_nothing(self):
        adl = self.adl
        adl._reset_writer_recovery_state()

        class _Alive:
            @staticmethod
            def is_alive() -> bool:
                return True

        with patch.multiple(adl, _writer_started=True, _writer_thread=_Alive()), \
                patch.object(adl, "ensure_async_logger_healthy_non_blocking") as repair, \
                patch.object(adl, "emergency_sync_write"):
            for _ in range(50):
                adl._check_queue_health()

        self.assertEqual(0, repair.call_count, "the repair ran while the writer was healthy")

    def test_the_repair_is_throttled_on_the_enqueue_hot_path(self):
        """`_check_queue_health` runs on every enqueue; the repair opens with a
        synchronous disk write. Once per call would be a performance regression
        shipped as a reliability fix."""
        adl = self.adl
        adl._reset_writer_recovery_state()
        clock = _FakeClock()
        with self._dead_writer(), \
                patch.object(adl.time, "monotonic", clock), \
                patch.object(adl, "ensure_async_logger_healthy_non_blocking") as repair, \
                patch.object(adl, "emergency_sync_write"):
            for _ in range(500):
                adl._check_queue_health()

            self.assertEqual(
                1, repair.call_count,
                f"the repair ran {repair.call_count} times across 500 enqueues "
                "with no time passing -- that is a synchronous disk write per "
                "log line",
            )

            clock.advance(adl._WRITER_REPAIR_INTERVAL_S + 1.0)
            adl._check_queue_health()
            self.assertEqual(
                2, repair.call_count,
                "the throttle never opens again, so the writer can never be "
                "repaired after the first attempt",
            )

    def test_it_stops_retrying_a_writer_that_will_not_come_back(self):
        """Bounded, like the supervisor. Spinning on a permanent failure is its
        own bug, and mitigation.md names it as one."""
        adl = self.adl
        adl._reset_writer_recovery_state()
        clock = _FakeClock()
        with self._dead_writer(), \
                patch.object(adl.time, "monotonic", clock), \
                patch.object(adl, "ensure_async_logger_healthy_non_blocking") as repair, \
                patch.object(adl, "emergency_sync_write"):
            for _ in range(adl._WRITER_REPAIR_MAX_ATTEMPTS + 5):
                adl._check_queue_health()
                clock.advance(adl._WRITER_REPAIR_INTERVAL_S + 1.0)

        self.assertEqual(
            adl._WRITER_REPAIR_MAX_ATTEMPTS, repair.call_count,
            "the repair keeps being retried forever on a writer that never "
            "recovers",
        )

    def test_giving_up_is_announced_once_and_not_on_every_call(self):
        adl = self.adl
        adl._reset_writer_recovery_state()
        clock = _FakeClock()
        with self._dead_writer(), \
                patch.object(adl.time, "monotonic", clock), \
                patch.object(adl, "ensure_async_logger_healthy_non_blocking"), \
                patch.object(adl, "emergency_sync_write") as sync:
            for _ in range(adl._WRITER_REPAIR_MAX_ATTEMPTS + 20):
                adl._check_queue_health()
                clock.advance(adl._WRITER_REPAIR_INTERVAL_S + 1.0)

        gave_up = [c for c in sync.call_args_list
                   if c.args and c.args[0] == "ASYNC_LOG_WRITER_UNRECOVERABLE"]
        self.assertEqual(1, len(gave_up), "the give-up notice repeats on every call")

    def test_a_recovered_writer_re_arms_the_budget(self):
        """A writer that came back must not carry the old attempt count, or the
        second outage of a long session gets fewer attempts than the first."""
        adl = self.adl
        adl._reset_writer_recovery_state()
        clock = _FakeClock()

        class _Alive:
            @staticmethod
            def is_alive() -> bool:
                return True

        with self._dead_writer(), \
                patch.object(adl.time, "monotonic", clock), \
                patch.object(adl, "ensure_async_logger_healthy_non_blocking"), \
                patch.object(adl, "emergency_sync_write"):
            adl._check_queue_health()
            clock.advance(adl._WRITER_REPAIR_INTERVAL_S + 1.0)
            adl._check_queue_health()

        with patch.multiple(adl, _writer_started=True, _writer_thread=_Alive()), \
                patch.object(adl.time, "monotonic", clock), \
                patch.object(adl, "emergency_sync_write"):
            adl._check_queue_health()

        with self._dead_writer(), \
                patch.object(adl.time, "monotonic", clock), \
                patch.object(adl, "ensure_async_logger_healthy_non_blocking") as repair, \
                patch.object(adl, "emergency_sync_write"):
            clock.advance(adl._WRITER_REPAIR_INTERVAL_S + 1.0)
            for _ in range(adl._WRITER_REPAIR_MAX_ATTEMPTS):
                adl._check_queue_health()
                clock.advance(adl._WRITER_REPAIR_INTERVAL_S + 1.0)

        self.assertEqual(
            adl._WRITER_REPAIR_MAX_ATTEMPTS, repair.call_count,
            "the attempt budget was not re-armed after the writer recovered",
        )


class B1DegradedLoggingCanRecoverTest(unittest.TestCase):
    def setUp(self) -> None:
        from alpha.utils import async_debug_log as adl

        self.adl = adl
        self._degraded = adl._degraded_mode
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        self.adl._degraded_mode = self._degraded
        self.adl._reset_writer_recovery_state()

    def _drive(self, qsize: int, clock: _FakeClock):
        adl = self.adl

        class _Q:
            @staticmethod
            def qsize() -> int:
                return qsize

        with patch.object(adl, "_log_queue", _Q()), \
                patch.object(adl.time, "monotonic", clock), \
                patch.object(adl, "emergency_sync_write"), \
                patch.multiple(adl, _writer_started=False, _writer_thread=None):
            adl._check_queue_health()

    def test_a_queue_spike_still_degrades(self):
        """The existing behaviour must survive the fix."""
        adl = self.adl
        adl._degraded_mode = False
        adl._reset_writer_recovery_state()
        self._drive(adl._QUEUE_CRITICAL + 1, _FakeClock())
        self.assertTrue(adl._degraded_mode, "the queue spike no longer degrades logging")

    def test_a_drained_queue_clears_degraded_mode(self):
        adl = self.adl
        clock = _FakeClock()
        adl._degraded_mode = False
        adl._reset_writer_recovery_state()
        self._drive(adl._QUEUE_CRITICAL + 1, clock)
        self.assertTrue(adl._degraded_mode)

        clock.advance(1.0)
        self._drive(0, clock)
        clock.advance(adl._DEGRADED_RECOVERY_S + 1.0)
        self._drive(0, clock)

        self.assertFalse(
            adl._degraded_mode,
            "the queue drained and stayed drained, and verbose logging is still "
            "degraded -- for the rest of the session, as it was before this fix",
        )

    def test_one_low_reading_is_not_enough(self):
        """A queue oscillating around the threshold must not flap degraded mode
        on and off, because each transition writes synchronously."""
        adl = self.adl
        clock = _FakeClock()
        adl._degraded_mode = False
        adl._reset_writer_recovery_state()
        self._drive(adl._QUEUE_CRITICAL + 1, clock)

        clock.advance(1.0)
        self._drive(0, clock)
        self.assertTrue(
            adl._degraded_mode,
            "degraded mode cleared on a single low sample -- one dip below the "
            "threshold is not recovery",
        )

    def test_a_new_spike_restarts_the_recovery_window(self):
        adl = self.adl
        clock = _FakeClock()
        adl._degraded_mode = False
        adl._reset_writer_recovery_state()
        self._drive(adl._QUEUE_CRITICAL + 1, clock)

        clock.advance(adl._DEGRADED_RECOVERY_S - 1.0)
        self._drive(0, clock)
        clock.advance(1.0)
        self._drive(adl._QUEUE_DEGRADED + 1, clock)      # spike again
        clock.advance(2.0)
        self._drive(0, clock)

        self.assertTrue(
            adl._degraded_mode,
            "the recovery window carried over across a fresh spike, so a queue "
            "that is still misbehaving looks recovered",
        )

    def test_the_recovery_is_announced(self):
        adl = self.adl
        clock = _FakeClock()
        adl._degraded_mode = False
        adl._reset_writer_recovery_state()
        self._drive(adl._QUEUE_CRITICAL + 1, clock)

        class _Q:
            @staticmethod
            def qsize() -> int:
                return 0

        clock.advance(1.0)
        self._drive(0, clock)
        clock.advance(adl._DEGRADED_RECOVERY_S + 1.0)
        with patch.object(adl, "_log_queue", _Q()), \
                patch.object(adl.time, "monotonic", clock), \
                patch.object(adl, "emergency_sync_write") as sync, \
                patch.multiple(adl, _writer_started=False, _writer_thread=None):
            adl._check_queue_health()

        events = [c.args[0] for c in sync.call_args_list if c.args]
        self.assertIn(
            "DEGRADED_LOGGING_MODE_CLEARED", events,
            "entering degraded mode is announced but leaving it is silent, so "
            "the log cannot be read back to tell which lines were dropped",
        )


class B2QuotaPauseHasAWayBackTest(unittest.TestCase):
    def _worker(self):
        from alpha.translation.translation_worker import TranslationWorker

        worker = TranslationWorker.__new__(TranslationWorker)
        import threading

        worker._lock = threading.RLock()
        worker._quota_disabled = True
        worker._accepting = False
        worker._status_message = "Translation paused (quota exceeded)."
        worker._enabled = True
        return worker

    def test_a_public_resume_exists(self):
        from alpha.translation.translation_worker import TranslationWorker

        self.assertTrue(
            hasattr(TranslationWorker, "resume_after_quota"),
            "a quota pause can only be lifted by restarting the session -- a "
            "transient quota_exceeded, or a top-up mid-meeting, strands the "
            "operator with no way back",
        )

    def test_resuming_clears_both_flags(self):
        worker = self._worker()
        worker.resume_after_quota()
        self.assertFalse(worker._quota_disabled, "_quota_disabled survived the resume")
        self.assertTrue(
            worker._accepting,
            "_accepting stayed False, so the worker still refuses every segment "
            "-- clearing only one of the two flags is not a resume",
        )

    def test_resuming_reports_what_it_did(self):
        worker = self._worker()
        self.assertTrue(worker.resume_after_quota(), "resume reported failure on a paused worker")
        self.assertNotIn(
            "paused", worker.status_message.lower(),
            "the status still reports a pause the worker is no longer in, so the "
            "operator cannot tell the resume took effect",
        )

    def test_resuming_a_worker_that_was_not_paused_is_a_no_op(self):
        worker = self._worker()
        worker._quota_disabled = False
        worker._accepting = True
        worker._status_message = "running"
        self.assertFalse(
            worker.resume_after_quota(),
            "resume claimed to have done something to a worker that was not paused",
        )
        self.assertEqual("running", worker.status_message)

    def test_it_does_not_resume_a_worker_that_was_shut_down(self):
        """`_accepting` is also cleared by `stop_accepting()` and `shutdown()`.
        Re-arming it there would restart a worker the session deliberately
        stopped."""
        worker = self._worker()
        worker._quota_disabled = False       # stopped, not quota-paused
        worker._accepting = False
        self.assertFalse(worker.resume_after_quota())
        self.assertFalse(
            worker._accepting,
            "resume_after_quota re-armed a worker that was stopped for an "
            "unrelated reason",
        )


class B2TheHostCanLiftThePauseTest(unittest.TestCase):
    """Through the real `AlphaApp` method, not a re-implementation of it.

    mitigation.md §3 criterion 2: the conversions must be driven through the
    production entry point. A test that calls `worker.resume_after_quota()`
    directly proves the worker works and says nothing about whether anything
    can reach it.
    """

    def _app(self, worker):
        from alpha.ui.main_window import AlphaApp

        app = AlphaApp.__new__(AlphaApp)
        app.translation_worker = worker
        app._translation_status_message = ""
        app.translated_verse_box = None
        return app

    def _paused_worker(self):
        import threading

        from alpha.translation.translation_worker import TranslationWorker

        worker = TranslationWorker.__new__(TranslationWorker)
        worker._lock = threading.RLock()
        worker._quota_disabled = True
        worker._accepting = False
        worker._status_message = "Translation paused (quota exceeded)."
        return worker

    def test_the_host_exposes_the_action(self):
        from alpha.ui.main_window import AlphaApp

        self.assertTrue(
            hasattr(AlphaApp, "resume_translation_after_quota"),
            "the worker can be resumed but nothing in the app can reach it",
        )

    def test_it_lifts_the_pause_and_updates_the_status(self):
        worker = self._paused_worker()
        app = self._app(worker)
        self.assertTrue(app.resume_translation_after_quota())
        self.assertFalse(worker._quota_disabled)
        self.assertTrue(worker._accepting)
        self.assertNotIn("paused", app._translation_status_message.lower())

    def test_it_is_safe_with_no_worker(self):
        """Called before a session starts, or after shutdown."""
        app = self._app(None)
        self.assertFalse(app.resume_translation_after_quota())

    def test_a_worker_that_raises_does_not_take_the_ui_down(self):
        class _Angry:
            status_message = ""

            @staticmethod
            def resume_after_quota():
                raise RuntimeError("worker exploded")

        app = self._app(_Angry())
        self.assertFalse(app.resume_translation_after_quota())

    def test_the_paused_message_names_the_way_out(self):
        """The old text described a state with no exit, which was accurate then
        and is misleading now."""
        source = (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8"
        )
        start = source.index('if status == "quota_exceeded":')
        block = source[start:start + 600]
        self.assertIn(
            "resume", block.lower(),
            "the quota-paused status still offers the operator no way forward",
        )


if __name__ == "__main__":
    unittest.main()
