"""One supervisor for long-lived worker loops.

WHY THIS EXISTS
---------------
Item 94 was one instance of a shape this repo keeps producing: a component
detects a failure, disables itself to protect integrity, and the path that would
re-enable it is never reachable in that session. The audit in `mitigation.md`
found six threads that end on one exception (A1-A6) and could never be
restarted.

Three of them (A1, A2, A4) share the exact same mistake: a module-level
`_started` boolean that is set once and never reset, so the second `start()` is a
silent no-op. That is why `start()` here gates on **liveness**, not on a flag,
and why it **clears the stop event** — `performance_timeline._heartbeat_stop`
was never cleared, which made stopping the heartbeat irreversible even
deliberately.

DESIGN NOTES THAT ARE NOT OBVIOUS
---------------------------------
* **A clean return is not a failure.** `stop_finalize_worker._watchdog_loop`
  breaks out of its own loop when `worker_done` is set. Restarting it there would
  resurrect a watchdog after finalize completed, which is worse than the bug this
  module fixes. A target that returns normally is finished; only an *exception*
  triggers a restart.
* **Restarting is bounded.** Spinning on a permanent failure is its own bug, so
  after `max_restarts` inside a rolling `restart_window_s` the supervisor stops
  and records `gave_up=True`. A component that gave up is reported, not hidden.
* **The supervisor must not log through `crash_guard_log`.** A1 *is* the crash
  guard's own writer thread; logging a crash-guard-writer failure through
  `crash_guard_log` re-enters the module that is currently broken. This logs
  through `jp_accuracy_log` (a different module, a different queue) behind a
  re-entrancy guard, and every logging call is itself guarded — a supervisor that
  can die while reporting a death is the same bug one level up.
* **The state is reported.** `restart_count` and `gave_up` reach
  `LAST_HEALTH_SNAPSHOT.json` through `all_supervisor_snapshots()`. Item 94's
  stall detector fired correctly and could not tell anyone; a supervisor whose
  state nothing reports would repeat exactly that.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Optional

DEFAULT_MAX_RESTARTS = 5
DEFAULT_RESTART_WINDOW_S = 60.0
DEFAULT_BACKOFF_INITIAL_S = 0.5
DEFAULT_BACKOFF_MAX_S = 5.0

_registry_lock = threading.Lock()
_registry: "dict[str, SupervisedThread]" = {}
_log_reentry = threading.local()


def _log(event: str, **data: Any) -> None:
    """Report a supervisor event without ever being able to raise.

    Deliberately not `crash_guard_log`: see the module docstring. The
    re-entrancy guard covers the case where the logging path itself is the
    supervised component and fails while reporting its own failure.
    """
    if getattr(_log_reentry, "active", False):
        return
    _log_reentry.active = True
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **data)
    except Exception:
        pass
    finally:
        _log_reentry.active = False


class SupervisedThread:
    """Runs `target` in a thread, restarting it on exception within a budget.

    `target` is called with no arguments and is expected to run until the stop
    event is set (it may read `supervisor.stop_event`). Returning normally means
    "finished"; raising means "restart me".
    """

    def __init__(
        self,
        target: Callable[[], Any],
        *,
        name: str,
        stop_event: Optional[threading.Event] = None,
        max_restarts: int = DEFAULT_MAX_RESTARTS,
        restart_window_s: float = DEFAULT_RESTART_WINDOW_S,
        backoff_initial_s: float = DEFAULT_BACKOFF_INITIAL_S,
        backoff_max_s: float = DEFAULT_BACKOFF_MAX_S,
        daemon: bool = True,
        register: bool = True,
    ) -> None:
        self._target = target
        self.name = str(name)
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self._max_restarts = max(0, int(max_restarts))
        self._restart_window_s = float(restart_window_s)
        self._backoff_initial_s = float(backoff_initial_s)
        self._backoff_max_s = float(backoff_max_s)
        self._daemon = bool(daemon)

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._restart_count = 0
        self._last_error = ""
        self._last_error_type = ""
        self._last_restart_ts = 0.0
        self._gave_up = False
        self._finished_cleanly = False
        self._restart_times: deque[float] = deque()

        if register:
            with _registry_lock:
                _registry[self.name] = self

    # ---- control ---------------------------------------------------------

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def start(self) -> bool:
        """Start, or restart after a stop. Returns False if already running.

        Gated on `thread is not None and thread.is_alive()` rather than on a
        boolean, because a dead thread with a `_started` flag still set is
        precisely the A1/A2/A4 defect.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            # Clearing the stop event is what makes stop() -> start() actually
            # restart. `performance_timeline` never cleared its own and could
            # not be restarted even on purpose.
            self._stop_event.clear()
            self._gave_up = False
            self._finished_cleanly = False
            self._restart_times.clear()
            thread = threading.Thread(
                target=self._supervise, name=self.name, daemon=self._daemon
            )
            self._thread = thread
        thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        """Signal a clean stop. Never counts as a failure and never restarts."""
        self._stop_event.set()
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout)

    def is_alive(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def join(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    # ---- supervision -----------------------------------------------------

    def _budget_exhausted(self, now: float) -> bool:
        window_start = now - self._restart_window_s
        while self._restart_times and self._restart_times[0] < window_start:
            self._restart_times.popleft()
        return len(self._restart_times) >= self._max_restarts

    def _supervise(self) -> None:
        backoff = self._backoff_initial_s
        while not self._stop_event.is_set():
            run_began = time.monotonic()
            try:
                self._target()
            except Exception as exc:
                now = time.monotonic()
                ran_for = now - run_began
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._last_error_type = type(exc).__name__
                if self._stop_event.is_set():
                    # Raised while shutting down. Not a fault to recover from.
                    _log(
                        "SUPERVISED_THREAD_ERROR_DURING_STOP",
                        thread_name=self.name,
                        error=self._last_error,
                    )
                    break
                with self._lock:
                    exhausted = self._budget_exhausted(now)
                if exhausted:
                    with self._lock:
                        self._gave_up = True
                    _log(
                        "SUPERVISED_THREAD_GAVE_UP",
                        thread_name=self.name,
                        error=self._last_error,
                        restart_count=self._restart_count,
                        max_restarts=self._max_restarts,
                        restart_window_s=self._restart_window_s,
                    )
                    break
                with self._lock:
                    self._restart_times.append(now)
                    self._restart_count += 1
                    self._last_restart_ts = time.time()
                    restart_count = self._restart_count
                _log(
                    "SUPERVISED_THREAD_RESTARTING",
                    thread_name=self.name,
                    error=self._last_error,
                    restart_count=restart_count,
                    ran_for_s=round(ran_for, 3),
                    backoff_s=backoff,
                )
                # A run that survived a while is not part of a tight failure
                # loop, so it should not inherit the previous backoff.
                if ran_for >= self._backoff_max_s:
                    backoff = self._backoff_initial_s
                if self._stop_event.wait(backoff):
                    break
                backoff = min(backoff * 2.0, self._backoff_max_s)
                continue
            else:
                # Returned without raising: the target decided it was finished.
                # Restarting here would resurrect A6's stop watchdog after
                # finalize completed.
                with self._lock:
                    self._finished_cleanly = True
                break

    # ---- reporting -------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "alive": self.is_alive(),
                "restart_count": self._restart_count,
                "last_error": self._last_error,
                "last_error_type": self._last_error_type,
                "last_restart_ts": self._last_restart_ts,
                "gave_up": self._gave_up,
                "finished_cleanly": self._finished_cleanly,
                "stop_requested": self._stop_event.is_set(),
            }

    @property
    def restart_count(self) -> int:
        with self._lock:
            return self._restart_count

    @property
    def gave_up(self) -> bool:
        with self._lock:
            return self._gave_up

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error


def supervise(
    target: Callable[[], Any],
    *,
    name: str,
    **kwargs: Any,
) -> SupervisedThread:
    """Create (or replace) a named supervisor and start it."""
    supervisor = SupervisedThread(target, name=name, **kwargs)
    supervisor.start()
    return supervisor


def get_supervisor(name: str) -> Optional[SupervisedThread]:
    with _registry_lock:
        return _registry.get(str(name))


def all_supervisor_snapshots() -> dict[str, dict[str, Any]]:
    """Every registered supervisor's state, for the health snapshot."""
    with _registry_lock:
        supervisors = list(_registry.values())
    out: dict[str, dict[str, Any]] = {}
    for supervisor in supervisors:
        try:
            out[supervisor.name] = supervisor.snapshot()
        except Exception:
            continue
    return out


def supervisor_health_summary() -> dict[str, Any]:
    """Compact roll-up: what a reader needs to see that something restarted."""
    snapshots = all_supervisor_snapshots()
    total_restarts = 0
    gave_up: list[str] = []
    dead: list[str] = []
    for name, snap in snapshots.items():
        try:
            total_restarts += int(snap.get("restart_count", 0))
            if snap.get("gave_up"):
                gave_up.append(name)
            elif not snap.get("alive") and not snap.get("finished_cleanly"):
                dead.append(name)
        except Exception:
            continue
    return {
        "supervised_thread_count": len(snapshots),
        "supervised_restart_count_total": total_restarts,
        "supervised_gave_up": sorted(gave_up),
        "supervised_dead_not_finished": sorted(dead),
        "supervised_threads": snapshots,
    }


def reset_registry_for_tests() -> None:
    with _registry_lock:
        _registry.clear()
