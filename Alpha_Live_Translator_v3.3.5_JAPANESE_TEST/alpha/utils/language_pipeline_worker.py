"""Off-UI language pipeline worker — flush/quarantine timers without Tkinter."""

from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

_worker: Optional["LanguagePipelineWorker"] = None
_worker_lock = threading.Lock()


@dataclass(order=True)
class _ScheduledTask:
    due_mono: float
    seq: int
    task_type: str = field(compare=False)
    assembler: Any = field(compare=False, default=None)
    generation: int = field(compare=False, default=0)
    reason: str = field(compare=False, default="")
    drop_ms: int = field(compare=False, default=0)
    skip_valid_short: bool = field(compare=False, default=False)


class LanguagePipelineWorker:
    """Executes assembler flush/quarantine work off the UI thread — no Tk calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._heap: list[_ScheduledTask] = []
        self._seq = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._last_flush_request_mono = 0.0
        self._last_flush_execute_mono = 0.0

    def start(self) -> None:
        if self._started and self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._started = True
        self._thread = threading.Thread(
            target=self._loop, name="LanguagePipelineWorker", daemon=True
        )
        self._thread.start()
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("LANGUAGE_PIPELINE_WORKER_STARTED")
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def pending_task_count(self) -> int:
        with self._cond:
            return len(self._heap)

    def cancel_all_tasks(self) -> int:
        with self._cond:
            cancelled = len(self._heap)
            self._heap.clear()
            return cancelled

    def stop_and_join(self, timeout_seconds: float = 2.0) -> dict[str, Any]:
        """Stop worker thread and wait for join; clear pending scheduled tasks."""
        begin = time.monotonic()
        pending_before = self.cancel_all_tasks()
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        thread = self._thread
        thread_alive = False
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.05, float(timeout_seconds)))
            thread_alive = thread.is_alive()
        duration_ms = round((time.monotonic() - begin) * 1000.0, 2)
        pending_after = self.pending_task_count()
        result = {
            "stopped": not thread_alive,
            "thread_alive": thread_alive,
            "pending_task_count": pending_after,
            "pending_cancelled": pending_before,
            "duration_ms": duration_ms,
        }
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("LANGUAGE_WORKER_STOPPED", **result)
        except Exception:
            pass
        return result

    def reset_for_new_run(self) -> None:
        """Restart-safe reset before the next Start — fresh thread and empty heap."""
        self.stop_and_join(timeout_seconds=1.0)
        with self._cond:
            self._heap.clear()
            self._seq = 0
            self._stop.clear()
            self._started = False
            self._thread = None
            self._last_flush_request_mono = 0.0
            self._last_flush_execute_mono = 0.0
        self.start()
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("LANGUAGE_PIPELINE_WORKER_RESET_FOR_NEW_RUN")
        except Exception:
            pass

    def schedule_flush(
        self, assembler: Any, due_mono: float, generation: int, reason: str
    ) -> None:
        if self._stop.is_set():
            return
        self._last_flush_request_mono = time.monotonic()
        with self._cond:
            self._seq += 1
            heapq.heappush(
                self._heap,
                _ScheduledTask(
                    due_mono=due_mono,
                    seq=self._seq,
                    task_type="flush",
                    assembler=assembler,
                    generation=generation,
                    reason=reason,
                ),
            )
            self._cond.notify()

    def cancel_flush(self, assembler: Any) -> None:
        with self._cond:
            self._heap = [
                t
                for t in self._heap
                if not (t.task_type == "flush" and t.assembler is assembler)
            ]
            heapq.heapify(self._heap)

    def schedule_quarantine_drop(
        self, assembler: Any, drop_ms: int, *, skip_valid_short: bool = False
    ) -> None:
        if self._stop.is_set():
            return
        due = time.monotonic() + drop_ms / 1000.0
        with self._cond:
            self._seq += 1
            heapq.heappush(
                self._heap,
                _ScheduledTask(
                    due_mono=due,
                    seq=self._seq,
                    task_type="quarantine_drop",
                    assembler=assembler,
                    drop_ms=drop_ms,
                    skip_valid_short=skip_valid_short,
                ),
            )
            self._cond.notify()

    def stats(self) -> dict[str, Any]:
        return {
            "last_flush_request_post_ts": self._last_flush_request_mono,
            "last_flush_execute_ts": self._last_flush_execute_mono,
            "language_pipeline_worker_alive": bool(
                self._thread is not None and self._thread.is_alive()
            ),
            "language_pipeline_pending_task_count": self.pending_task_count(),
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            task: Optional[_ScheduledTask] = None
            wait_s = 0.5
            with self._cond:
                now = time.monotonic()
                while self._heap and self._heap[0].due_mono <= now:
                    task = heapq.heappop(self._heap)
                    break
                if task is None and self._heap:
                    wait_s = max(0.05, self._heap[0].due_mono - now)
            if task is None:
                with self._cond:
                    self._cond.wait(timeout=wait_s)
                continue
            try:
                if task.task_type == "flush":
                    self._run_flush(task)
                elif task.task_type == "quarantine_drop":
                    self._run_quarantine_drop(task)
            except Exception as exc:
                try:
                    from alpha.utils.crash_guard_log import log_exception

                    log_exception(exc, source="language_pipeline_worker")
                except Exception:
                    pass

    def _run_flush(self, task: _ScheduledTask) -> None:
        assembler = task.assembler
        if assembler is None:
            return
        self._last_flush_execute_mono = time.monotonic()
        executed = assembler.try_execute_continuity_hold(
            task.generation, task.reason
        )
        if executed:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "ASSEMBLER_FLUSH_EXECUTED_OFF_UI_THREAD",
                    generation=task.generation,
                    reason=task.reason,
                )
            except Exception:
                pass
        else:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "ASSEMBLER_FLUSH_LOCK_BUSY_RETRY",
                    generation=task.generation,
                    reason=task.reason,
                )
            except Exception:
                pass
            if not self._stop.is_set():
                self.schedule_flush(
                    assembler,
                    time.monotonic() + 0.05,
                    task.generation,
                    task.reason,
                )

    def _run_quarantine_drop(self, task: _ScheduledTask) -> None:
        assembler = task.assembler
        if assembler is None:
            return
        assembler._quarantine_drop_scheduled = False
        drop_fn = getattr(assembler, "_drop_expired_quarantine_locked", None)
        if callable(drop_fn):
            acquired = assembler._lock.try_acquire(timeout=0.0)
            if not acquired:
                if not self._stop.is_set():
                    self.schedule_quarantine_drop(
                        assembler, task.drop_ms, skip_valid_short=task.skip_valid_short
                    )
                return
            try:
                drop_fn(skip_valid_short=task.skip_valid_short)
            finally:
                assembler._lock.release()


def get_language_pipeline_worker() -> LanguagePipelineWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = LanguagePipelineWorker()
        return _worker


def start_language_pipeline_worker() -> LanguagePipelineWorker:
    worker = get_language_pipeline_worker()
    worker.reset_for_new_run()
    return worker
