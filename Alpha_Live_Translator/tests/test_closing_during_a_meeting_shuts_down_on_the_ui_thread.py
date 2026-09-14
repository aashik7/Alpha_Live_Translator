"""Closing the window during a meeting must shut down on the UI thread.

WHAT WAS BROKEN
---------------
`_on_close` during a session calls `_begin_graceful_stop()` and then used to
start a background thread, `WindowCloseWait`, which polled for the stop to
finish (or 12 s to pass) and then called `_shutdown_and_destroy()` ON THAT
THREAD -- on the normal success path as well as on the timeout.

`_shutdown_and_destroy` cancels the session loops and, since item 16, the four
app-lifetime `after` jobs. From a non-UI thread every one of those cancels goes
through `tk_thread_guard.guarded_cancel`, which counted the call, logged it and
returned -- it never cancelled anything. So on the most ordinary close there is,
item 16's cancellation silently did nothing and the recurring jobs kept firing
until teardown. Item 16 was only ever fixed for a close with no session running.

The thread was also left stuck: the final `destroy()` is marshalled by tkinter's
threaded Tcl into the running mainloop, the loop exits, and the call's reply never
comes back. Driven in production shape -- real guard, UI thread registered, main
thread inside mainloop():

    mainloop returned after      : 0.81 s
    job ticks AFTER bg cancel    : 8        (the cancel was swallowed)
    WindowCloseWait alive 3 s after mainloop returned: True

A CORRECTION, recorded here because it was published: the 2026-09-14 audit said
this path left the window open, on the strength of a probe whose main thread was
blocked in `join()` rather than inside `mainloop()`. With the main thread in its
loop, tkinter marshals the cross-thread `destroy()` and the window does close.
That claim was wrong; the cancel swallow and the stuck thread are real.

THE FIX, AND THE ONE THAT WAS REJECTED
------------------------------------
The thread only existed to wait without blocking the UI. A main-thread `after`
poll does that natively, with no thread and no marshalling, and when the stop is
done -- or the deadline passes -- `_shutdown_and_destroy()` runs on the UI thread
where every cancel is real.

Rejected: keeping the thread and marshalling the shutdown with
`_run_on_ui_thread`. From a background thread that posts to the UI event bus,
and if graceful stop has already stopped the bus pump the close is never
delivered -- a real hang, which is the very failure the audit wrongly claimed.

The poll must never sleep on the UI thread: the Stop worker's
`request_stop_ui_drain` posts work to the UI thread and waits for an ack.

`guarded_cancel` is also fixed at the class level: it now reroutes like
`guarded_after` instead of dropping the cancel.
"""

import sys
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402


def _borrow(name):
    """Look a method up at CALL time. Binding a not-yet-written method in a class
    body makes the whole module fail at collection -- hit twice in this repo."""
    impl = getattr(AlphaApp, name, None)
    if impl is None:
        raise AttributeError("%s has not been written yet" % name)
    return impl


class _CloseHost:
    """Drives the REAL `_on_close` over stubbed stop and teardown."""

    def __init__(self, *, stop_finishes=True):
        self.is_listening = True
        self._is_stopping = False
        self._is_finalizing = False
        self._window_close_pending = False
        self._stop_finishes = stop_finishes
        self.shutdown_threads = []
        self.pending_after = []
        self.cancelled = []
        self.destroyed = False
        self._next_id = 0

    # the real methods under test
    def _on_close(self):
        return _borrow("_on_close")(self)

    def _poll_window_close_ready(self, deadline_mono):
        return _borrow("_poll_window_close_ready")(self, deadline_mono)

    def _cancel_recurring_ui_jobs(self):
        return _borrow("_cancel_recurring_ui_jobs")(self)

    # stubs
    def _begin_graceful_stop(self):
        self._is_stopping = not self._stop_finishes
        self._is_finalizing = not self._stop_finishes

    def _shutdown_and_destroy(self):
        self.shutdown_threads.append(threading.current_thread())

    def after(self, ms, fn, *args):
        self._next_id += 1
        job = "after#%d" % self._next_id
        self.pending_after.append((job, fn, args))
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)
        self.pending_after = [p for p in self.pending_after if p[0] != job]

    def destroy(self):
        self.destroyed = True

    def pump(self, rounds=5):
        """Run scheduled `after` callbacks on THIS (the main) thread."""
        for _ in range(rounds):
            jobs, self.pending_after = self.pending_after, []
            for _job, fn, args in jobs:
                fn(*args)


def _live_close_threads():
    return [t for t in threading.enumerate() if t.name == "WindowCloseWait" and t.is_alive()]


class ClosingDuringAMeetingTest(unittest.TestCase):
    def test_the_shutdown_runs_on_the_ui_thread(self):
        """The whole point: every cancel in the shutdown must be a real one."""
        host = _CloseHost()
        host._on_close()
        host.pump()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not host.shutdown_threads:
            time.sleep(0.02)
            host.pump()
        self.assertEqual(len(host.shutdown_threads), 1, "the shutdown did not run exactly once")
        self.assertIs(
            host.shutdown_threads[0],
            threading.main_thread(),
            "the shutdown ran on %r -- off the UI thread every after_cancel in it "
            "is swallowed by the guard" % host.shutdown_threads[0].name,
        )

    def test_no_background_close_thread_is_started(self):
        before = set(_live_close_threads())
        host = _CloseHost(stop_finishes=False)
        host._on_close()
        time.sleep(0.1)
        started = [t for t in _live_close_threads() if t not in before]
        self.assertEqual(started, [], "a WindowCloseWait thread is still started")

    def test_while_stopping_it_waits_without_shutting_down(self):
        host = _CloseHost(stop_finishes=False)
        host._is_stopping = True  # genuinely mid-stop; the host starts idle
        host._poll_window_close_ready(time.monotonic() + 60.0)
        self.assertEqual(host.shutdown_threads, [])
        self.assertTrue(host.pending_after, "the poll did not reschedule itself")

    def test_the_deadline_still_closes_on_the_ui_thread(self):
        """A stop that never finishes must not leave the window up forever."""
        host = _CloseHost(stop_finishes=False)
        host._is_stopping = True
        host._poll_window_close_ready(time.monotonic() - 1.0)
        self.assertEqual(host.shutdown_threads, [threading.main_thread()])

    def test_the_poll_never_sleeps_on_the_ui_thread(self):
        """The Stop worker posts drain work to the UI thread and waits for an
        ack; a poll that slept there would stall it."""
        host = _CloseHost(stop_finishes=False)
        # Mid-stop, so this reaches the RESCHEDULE branch. Without it the host is
        # idle, the poll shuts down at once, and the timing passes for the wrong
        # reason -- this test did exactly that until the fixture was corrected.
        host._is_stopping = True
        started = time.monotonic()
        host._poll_window_close_ready(time.monotonic() + 60.0)
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(host.shutdown_threads, [], "fixture: this must be the waiting path")
        self.assertTrue(host.pending_after, "fixture: the poll must have rescheduled")

    def test_a_second_close_click_still_force_closes_and_stops_the_poll(self):
        host = _CloseHost(stop_finishes=False)
        host._on_close()
        poll_jobs = [job for job, _fn, _a in host.pending_after]
        self.assertTrue(poll_jobs, "no poll job was scheduled to cancel")
        host._on_close()
        self.assertTrue(host.destroyed, "a second click no longer force-closes")
        self.assertTrue(
            set(poll_jobs) & set(host.cancelled),
            "the close poll was left scheduled into a destroyed window",
        )


class GuardedCancelReallyCancelsTest(unittest.TestCase):
    """The class-level half: a background after_cancel must not be dropped."""

    def setUp(self):
        from alpha.utils import tk_thread_guard, ui_thread_guard

        self.tg, self.ug = tk_thread_guard, ui_thread_guard
        self._saved = (tk_thread_guard._guard_installed, ui_thread_guard.UI_MAIN_THREAD_ID)
        tk_thread_guard._guard_installed = False
        ui_thread_guard.register_ui_main_thread()

        class GuardedTk(tk.Tk):
            pass

        tk_thread_guard.install_tk_thread_guard(GuardedTk)
        self.root = GuardedTk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.tg._guard_installed, self.ug.UI_MAIN_THREAD_ID = self._saved

    def _drain_and_run(self, seconds=0.6):
        from alpha.utils.ui_event_bus import get_ui_event_bus

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            get_ui_event_bus().drain(self.root)
            self.root.update()
            time.sleep(0.02)

    def test_a_background_cancel_stops_the_job(self):
        fired = []
        job = self.root.after(300, lambda: fired.append(True))
        self.assertTrue(job, "harness: a main-thread after() was rerouted")
        t = threading.Thread(target=lambda: self.root.after_cancel(job), name="WindowCloseWait")
        t.start()
        t.join()
        self._drain_and_run()
        self.assertEqual(fired, [], "the background cancel was swallowed; the job fired anyway")

    def test_a_main_thread_cancel_is_unchanged(self):
        fired = []
        job = self.root.after(200, lambda: fired.append(True))
        self.root.after_cancel(job)
        self._drain_and_run(0.4)
        self.assertEqual(fired, [])

    def test_an_empty_id_from_a_background_thread_does_not_raise_on_the_ui_thread(self):
        """`guarded_after` returns "" for a job it rerouted, so a caller can hold
        an empty id. Rerouting a cancel of "" would make tkinter raise
        ValueError on the UI thread when the bus drains it."""
        t = threading.Thread(target=lambda: self.root.after_cancel(""), name="BgCancel")
        t.start()
        t.join()
        self._drain_and_run(0.2)


if __name__ == "__main__":
    unittest.main()
