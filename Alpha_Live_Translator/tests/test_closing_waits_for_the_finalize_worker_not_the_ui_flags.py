"""A close during a meeting must wait for the finalize worker, not the UI flags.

WHAT WAS BROKEN (CODE_REVIEW item 22)
------------------------------------
Closing the window mid-meeting starts a graceful stop and then waits until the
stop "has finished" before shutting the app down. It judged that from
`_is_stopping` / `_is_finalizing`. Those are UI state, and two things clear them
while the daemon `StopFinalizeWorker` is still working:

  * five seconds after Stop, `_stop_ui_watchdog_tick` force-restores the UI and
    `_restore_ui_after_stop_watchdog` clears both flags, whatever the worker is
    doing -- and the worker has up to 66 s of step budget before
    `write_final_alpha` writes the deliverable;
  * on the normal path the worker clears both flags itself before its alias
    sync and seal verification.

The close then shut down, `mainloop()` returned, `main.py` exited with nothing
joining the worker, and the worker died mid-finalize. In the field 208 of 2058
recorded stops took longer than 5 s.

Driven end to end before the fix -- real `_on_close`, real `_begin_graceful_stop`,
real `begin_stop_from_ui`, real stop UI watchdog, real close poll, real Tk
mainloop and thread guard; only the worker body faked, writing its deliverable
at 7 s:

      5.14 s  UI force-restored (flags cleared by the 5 s watchdog)
      5.29 s  SHUTDOWN (finalize worker alive: True)
    deliverable written before shutdown: False

The same mistake had already been found and fixed for Start, which is gated on
`finalize_in_progress()` for exactly this reason. The close had not been.

THE FIX
-------
The close poll also waits while `finalize_in_progress()` is True, and its cap
rises from 12 s to 30 s: the slowest stop in the field took 11.8 s, too close to
12. The poll still returns the moment the worker finishes, and a second close
click still force-closes at once.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import main_window  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import stop_finalize_worker as sfw  # noqa: E402

# The slowest `stop_finalize_duration_ms` across the evidence tree when this was
# written (26.5.3; 9.9 s at 26.5.18).
SLOWEST_STOP_SEEN_IN_THE_FIELD_S = 11.8


def _borrow(name):
    """Look a method up at CALL time -- see the close tests next door."""
    impl = getattr(AlphaApp, name, None)
    if impl is None:
        raise AttributeError("%s has not been written yet" % name)
    return impl


class _Host:
    """The real close and stop path over stubbed teardown and widget painting."""

    def __init__(self):
        self.is_listening = True
        self._is_stopping = False
        self._is_finalizing = False
        self._window_close_pending = False
        self._stop_finalize_started = False
        self.status_text_label = None
        self.translation_worker = None
        self.events = []
        self.shutdown_threads = []
        self.pending_after = []
        self.cancelled = []
        self.destroyed = False
        self._next_id = 0
        self._after_lock = threading.Lock()

    # real
    def _on_close(self):
        return _borrow("_on_close")(self)

    def _begin_graceful_stop(self):
        return _borrow("_begin_graceful_stop")(self)

    def _start_stop_ui_watchdog(self):
        return _borrow("_start_stop_ui_watchdog")(self)

    def _stop_ui_watchdog_tick(self):
        return _borrow("_stop_ui_watchdog_tick")(self)

    def _restore_ui_after_stop_watchdog(self, **kwargs):
        return _borrow("_restore_ui_after_stop_watchdog")(self, **kwargs)

    def _poll_window_close_ready(self, *args):
        return _borrow("_poll_window_close_ready")(self, *args)

    def _cancel_recurring_ui_jobs(self):
        return _borrow("_cancel_recurring_ui_jobs")(self)

    # stubs
    def _set_stopped_ui_state(self):
        self.events.append("ui_restored")

    def _set_stopping_ui_state(self):
        pass

    def _shutdown_and_destroy(self):
        self.shutdown_threads.append(threading.current_thread())
        self.events.append("shutdown")

    def after(self, ms, fn, *args):
        with self._after_lock:
            self._next_id += 1
            job = "after#%d" % self._next_id
            self.pending_after.append((job, fn, args))
        return job

    def after_cancel(self, job):
        with self._after_lock:
            self.cancelled.append(job)
            self.pending_after = [p for p in self.pending_after if p[0] != job]

    def destroy(self):
        self.destroyed = True

    def pump(self):
        """Run one round of scheduled `after` callbacks on the main thread."""
        with self._after_lock:
            jobs, self.pending_after = self.pending_after, []
        for _job, fn, args in jobs:
            fn(*args)

    def pump_until(self, predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not predicate():
            self.pump()
            time.sleep(0.02)
        return predicate()


class _FakeFinalizeWorker:
    """Stands in for `_run_finalize_worker`'s body, in the real worker's order."""

    def __init__(self):
        self.release = threading.Event()

    def run(self, host):
        with sfw._state_lock:
            sfw._stop_state["worker_started"] = True
        try:
            self.release.wait(15.0)
            host.events.append("deliverable_written")
            host._is_stopping = False  # what the worker itself does before its tail
            host._is_finalizing = False
            event = getattr(host, "stop_core_completed_event", None)
            if event is not None:
                event.set()
        finally:
            with sfw._state_lock:
                sfw._stop_state["worker_done"] = True


class _IsolatedStopState(unittest.TestCase):
    """Other tests run the real Stop worker; never inherit one of their threads."""

    def setUp(self):
        with sfw._state_lock:
            self._saved_thread = sfw._stop_state.get("finalize_thread")
            sfw._stop_state["finalize_thread"] = None
        self._saved_worker = sfw._run_finalize_worker
        self._releases = []
        self._threads = []

    def tearDown(self):
        for release in self._releases:
            release.set()
        for thread in self._threads:
            thread.join(timeout=5.0)
        with sfw._state_lock:
            current = sfw._stop_state.get("finalize_thread")
        if current is not None:
            current.join(timeout=5.0)
        sfw._run_finalize_worker = self._saved_worker
        with sfw._state_lock:
            sfw._stop_state["finalize_thread"] = self._saved_thread

    def _register_live_worker(self):
        release = threading.Event()
        started = threading.Event()
        thread = threading.Thread(
            target=lambda: (started.set(), release.wait(15.0)), name="StopFinalizeWorker", daemon=True
        )
        thread.start()
        started.wait(5.0)
        self._releases.append(release)
        self._threads.append(thread)
        with sfw._state_lock:
            sfw._stop_state["finalize_thread"] = thread
        return release


class TheCloseWaitsForTheWorkerTest(_IsolatedStopState):
    def test_flags_cleared_by_the_stop_watchdog_do_not_end_the_wait(self):
        """The 5 s force-restore has run: both flags are False, the worker is not done."""
        self._register_live_worker()
        host = _Host()  # both flags False -- exactly what the restore leaves
        host._poll_window_close_ready(time.monotonic() + 60.0)
        self.assertEqual(
            host.events,
            [],
            "the close shut down while the finalize worker was still running -- "
            "main.py exits after mainloop and the worker dies with it",
        )
        self.assertTrue(host.pending_after, "the poll did not keep waiting")

    def test_it_closes_on_the_ui_thread_once_the_worker_finishes(self):
        release = self._register_live_worker()
        host = _Host()
        host._poll_window_close_ready(time.monotonic() + 60.0)
        host.pump_until(lambda: False, 0.3)
        self.assertEqual(host.shutdown_threads, [], "the close did not wait for the worker")
        release.set()
        self._threads[-1].join(timeout=5.0)
        self.assertTrue(host.pump_until(lambda: host.shutdown_threads, 3.0), "never closed")
        self.assertEqual(host.shutdown_threads, [threading.main_thread()])

    def test_the_whole_chain_close_stop_watchdog_restore_then_worker(self):
        """Real `_on_close` -> `_begin_graceful_stop` -> `begin_stop_from_ui` ->
        real UI watchdog force-restore -> real close poll. Only the worker body is fake."""
        worker = _FakeFinalizeWorker()
        self._releases.append(worker.release)
        sfw._run_finalize_worker = worker.run
        host = _Host()

        host._on_close()
        self.assertTrue(sfw.finalize_in_progress(), "fixture: the real Stop did not start a worker")

        # Skip the real five seconds: the next real watchdog tick sees it has run.
        host.stop_started_at -= 6.0
        self.assertTrue(
            host.pump_until(lambda: "ui_restored" in host.events, 3.0),
            "fixture: the real stop UI watchdog never force-restored",
        )
        self.assertFalse(host._is_stopping or host._is_finalizing, "fixture: the restore left a flag set")

        host.pump_until(lambda: "shutdown" in host.events, 0.6)
        self.assertNotIn(
            "shutdown",
            host.events,
            "the close shut down right after the 5 s force-restore, with the "
            "worker still running: %r" % host.events,
        )

        worker.release.set()
        self.assertTrue(host.pump_until(lambda: "shutdown" in host.events, 5.0), "never closed")
        self.assertLess(host.events.index("deliverable_written"), host.events.index("shutdown"))
        self.assertEqual(host.shutdown_threads, [threading.main_thread()])

    def test_a_finished_worker_does_not_hold_the_close(self):
        thread = threading.Thread(target=lambda: None, daemon=True)
        thread.start()
        thread.join(timeout=5.0)
        with sfw._state_lock:
            sfw._stop_state["finalize_thread"] = thread
        host = _Host()
        host._poll_window_close_ready(time.monotonic() + 60.0)
        self.assertEqual(host.shutdown_threads, [threading.main_thread()])

    def test_a_worker_that_never_finishes_still_closes_at_the_deadline(self):
        self._register_live_worker()
        host = _Host()
        host._poll_window_close_ready(time.monotonic() - 1.0)
        self.assertTrue(host.pump_until(lambda: host.shutdown_threads, 8.0), "a hung worker held the window forever")
        self.assertEqual(host.shutdown_threads, [threading.main_thread()])

    def test_the_wait_is_long_enough_for_the_slowest_stop_seen_in_the_field(self):
        """A cap the slow stops actually hit kills the worker just the same."""
        self.assertGreaterEqual(
            main_window.WINDOW_CLOSE_WAIT_S,
            2 * SLOWEST_STOP_SEEN_IN_THE_FIELD_S,
            "the close gives up at %.0f s; the slowest recorded stop took %.1f s"
            % (main_window.WINDOW_CLOSE_WAIT_S, SLOWEST_STOP_SEEN_IN_THE_FIELD_S),
        )


class ClosingAfterStopTest(_IsolatedStopState):
    """The commonest close there is: click Stop, then close the window.

    `_on_close` only waited when `is_listening` was True. After Stop it is False,
    so the close shut down at once and killed the worker -- within the first five
    seconds too, when `_is_stopping` was still True: that path set
    `_window_close_pending`, wrote the partial snapshot, found `is_listening`
    False, and fell through to the idle close.
    """

    def _stopped_host(self, *, stopping):
        host = _Host()
        host.is_listening = False  # Stop has run
        host._is_stopping = stopping
        host._is_finalizing = stopping
        return host

    def test_closing_after_the_5s_restore_waits_for_the_worker(self):
        release = self._register_live_worker()
        host = self._stopped_host(stopping=False)
        host._on_close()
        self.assertEqual(
            host.shutdown_threads,
            [],
            "closing after Stop shut down while the Stop worker was still writing",
        )
        release.set()
        self._threads[-1].join(timeout=5.0)
        self.assertTrue(host.pump_until(lambda: host.shutdown_threads, 3.0), "never closed")
        self.assertEqual(host.shutdown_threads, [threading.main_thread()])

    def test_closing_within_5s_of_stop_waits_for_the_worker(self):
        self._register_live_worker()
        host = self._stopped_host(stopping=True)
        host._on_close()
        self.assertEqual(
            host.shutdown_threads,
            [],
            "with _is_stopping still True the close fell through to the idle "
            "shutdown and killed the worker",
        )

    def test_closing_after_stop_does_not_start_a_second_stop(self):
        self._register_live_worker()
        host = self._stopped_host(stopping=False)
        host._on_close()
        self.assertFalse(
            hasattr(host, "stop_started_at"),
            "the close ran _begin_graceful_stop again for a session already stopped",
        )

    def test_a_second_click_while_waiting_after_stop_still_force_closes(self):
        self._register_live_worker()
        host = self._stopped_host(stopping=False)
        host._on_close()
        host._on_close()
        self.assertTrue(host.destroyed, "a second click no longer force-closes")

    def test_closing_an_idle_app_still_closes_at_once(self):
        host = self._stopped_host(stopping=False)
        host._on_close()
        self.assertEqual(host.shutdown_threads, [threading.main_thread()])


if __name__ == "__main__":
    unittest.main()
