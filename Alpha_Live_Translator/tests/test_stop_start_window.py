"""A new session must not begin while the previous one is still finalizing.

WHAT WAS BROKEN
---------------
`_stop_ui_watchdog_tick` force-restores the UI once five seconds have passed,
and `_restore_ui_after_stop_watchdog` clears BOTH guards that keep a second
session out -- `_is_finalizing` and `_stop_finalize_started`. `toggle_listening`
has no other guard.

Meanwhile `_run_finalize_worker` still has work to do. Computed from
`_STEP_TIMEOUTS_MS` and the order the steps are invoked in: 20 steps, 66.0 s of
budget before `write_final_alpha` writes the deliverable, 71.0 s in total. The
two large budgets are real -- `drain_audio_queue` is 25 s and spends it when
audio is still queued, and `translation_worker_shutdown` is 16 s, longest under
exactly the slow-provider condition that produced the queue-full defect.

So for up to 66 seconds the operator sees an enabled Start button while the
previous session has not written its transcript. And what makes that harmful
rather than untidy is the second half: `rebind_all_runtime_writers` logs
`SECOND_RUN_FOLDER_CREATION_BLOCKED` and then rebinds anyway -- the guard body
only logs, and `set_active_run_folder` sits outside it. A second Start in the
window repoints every runtime writer at the new run's folder while the old
worker is still writing into it.

THE FIX
-------
Two changes that are one fix. `toggle_listening` gates on the finalize worker
rather than on a UI flag, and the misleading event is renamed to say what
actually happened.

Blocking the second rebind is NOT the fix: every session after the first binds
its own run folder, so a `return` there would break normal use. The rename is
the honest half; the gate is the protective half.

WHAT THESE TESTS PIN
--------------------
* Start is refused while the finalize worker is alive, and allowed once it is
  not -- including when the worker DIED without completing, so a hung finalize
  cannot lock the operator out for the rest of the day
* the first Start of a process, before any Stop has happened, is unaffected
* the rename: no event claims a block that does not happen, and the payload
  carries the folder being replaced
"""

import ast
import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import stop_finalize_worker as sfw  # noqa: E402


class Host:
    """Only what `toggle_listening` reads."""

    toggle_listening = AlphaApp.toggle_listening

    def __init__(self):
        self._is_finalizing = False
        self._starting_listening = False
        self.is_listening = False
        self.started = 0
        self.stopped = 0

    def _start_listening(self):
        self.started += 1

    def _begin_graceful_stop(self):
        self.stopped += 1


class StartIsGatedOnTheWorkerNotTheUiFlagTest(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self._saved = sfw._stop_state.get("finalize_thread")

    def tearDown(self):
        with sfw._state_lock:
            sfw._stop_state["finalize_thread"] = self._saved

    def _set_worker(self, thread):
        with sfw._state_lock:
            sfw._stop_state["finalize_thread"] = thread

    def test_start_is_refused_while_the_finalize_worker_is_alive(self):
        """The window itself: the UI has already cleared `_is_finalizing`."""
        running = threading.Event()
        done = threading.Event()
        t = threading.Thread(target=lambda: (running.set(), done.wait(10.0)), daemon=True)
        t.start()
        running.wait(5.0)
        self._set_worker(t)

        self.host._is_finalizing = False        # what the 5 s restore does
        self.host._stop_finalize_started = False
        self.host.toggle_listening()

        try:
            self.assertEqual(
                self.host.started,
                0,
                "a second session began while the previous one was still "
                "finalizing -- up to 66 s before it writes its transcript",
            )
        finally:
            done.set()
            t.join(timeout=5.0)

    def test_start_is_allowed_once_the_worker_has_finished(self):
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        t.join(timeout=5.0)
        self._set_worker(t)

        self.host.toggle_listening()
        self.assertEqual(self.host.started, 1, "Start stayed blocked after the worker finished")

    def test_a_worker_that_died_does_not_lock_the_operator_out(self):
        """The regression this gate could easily introduce.

        Gating on `stop_core_completed_event` alone would block Start forever
        if the worker hangs or dies without setting it. A dead thread must
        release Start -- it can no longer corrupt anything.
        """
        t = threading.Thread(target=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                             daemon=True)
        t.start()
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive())
        self._set_worker(t)

        self.host.toggle_listening()
        self.assertEqual(
            self.host.started, 1, "a crashed finalize worker locked Start out"
        )

    def test_the_first_start_of_the_process_is_unaffected(self):
        """No Stop has happened yet, so there is no worker at all."""
        self._set_worker(None)
        self.host.toggle_listening()
        self.assertEqual(self.host.started, 1)

    def test_stop_is_never_blocked_by_the_gate(self):
        """The gate guards Start. Refusing Stop would strand a live session."""
        running = threading.Event()
        done = threading.Event()
        t = threading.Thread(target=lambda: (running.set(), done.wait(10.0)), daemon=True)
        t.start()
        running.wait(5.0)
        self._set_worker(t)

        self.host.is_listening = True
        self.host.toggle_listening()
        try:
            self.assertEqual(self.host.stopped, 1, "Stop was blocked by the Start gate")
        finally:
            done.set()
            t.join(timeout=5.0)


class TheRebindEventSaysWhatHappensTest(unittest.TestCase):
    """Item 11. The guard body only logs; `set_active_run_folder` is outside it.

    Walked with the AST rather than grepped -- the module and this file both
    quote the old event name in prose explaining the change.
    """

    SRC = PROJECT_ROOT / "alpha" / "utils" / "troubleshooting_paths.py"

    def _fn(self):
        tree = ast.parse(self.SRC.read_text(encoding="utf-8"))
        return next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "rebind_all_runtime_writers"
        )

    def test_no_event_claims_a_block_that_does_not_happen(self):
        fn = self._fn()
        guard = next(
            n for n in ast.walk(fn)
            if isinstance(n, ast.If) and "rebind_count" in ast.unparse(n.test)
        )
        body = ast.unparse(guard)
        leaves = [x for x in ast.walk(guard) if isinstance(x, (ast.Return, ast.Raise))]
        self.assertFalse(
            "SECOND_RUN_FOLDER_CREATION_BLOCKED" in body and not leaves,
            "the event says the rebind was BLOCKED, but the guard body has no "
            "return or raise and `set_active_run_folder` runs anyway -- a "
            "reader of a client's log concludes the rebind was prevented",
        )

    def test_the_rebind_is_still_allowed(self):
        """Blocking it would break every session after the first."""
        fn = self._fn()
        calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "set_active_run_folder"
        ]
        self.assertTrue(calls, "the rebind must still happen; only the name was wrong")

    def test_the_event_carries_the_folder_being_replaced(self):
        fn = self._fn()
        guard = next(
            n for n in ast.walk(fn)
            if isinstance(n, ast.If) and "rebind_count" in ast.unparse(n.test)
        )
        body = ast.unparse(guard)
        self.assertIn(
            "previous_run_folder", body,
            "a rebind event that does not name the folder it is replacing "
            "cannot be used to reconstruct what happened",
        )


if __name__ == "__main__":
    unittest.main()
