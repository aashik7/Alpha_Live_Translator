"""Language-agnostic UI event bus — background threads post; Tk main thread drains."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from alpha.constants import (
    HIGH_FREQUENCY_UI_DRAIN_LOGGING_ENABLED,
    LANGUAGE_AGNOSTIC_UI_EVENT_BUS,
    UI_EVENT_DRAIN_SAMPLE_INTERVAL_SECONDS,
    UI_EVENT_DRAIN_VERBOSE_LOGGING,
)

_WARN_QUEUE = 1000
_DROP_QUEUE = 3000
_DEFAULT_MAX_EVENTS = 50
_DEFAULT_TIME_BUDGET_MS = 12.0
_POLL_MS = 75

_CRITICAL_EVENTS = frozenset(
    {
        "transcript_segment_ready",
        "transcript_flush_requested",
    }
)
_COALESCE_EVENTS = frozenset(
    {
        "ui_status_update",
        "diagnostics_notice",
        "partial_error_notice",
    }
)

_bus: Optional["UIEventBus"] = None
_bus_lock = threading.Lock()


@dataclass
class UIEvent:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    posted_mono: float = field(default_factory=time.monotonic)
    schedule_after_ms: int = 0
    callback: Optional[Callable[..., Any]] = None
    callback_args: tuple[Any, ...] = field(default_factory=tuple)
    callback_kwargs: dict[str, Any] = field(default_factory=dict)


class UIEventBus:
    """Thread-safe queue; only Tk main thread invokes handlers."""

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[UIEvent] = queue.SimpleQueue()
        self._stats_lock = threading.Lock()
        self._posted = 0
        self._drained = 0
        self._dropped = 0
        self._last_post_mono = 0.0
        self._last_drain_mono = 0.0
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._started = False
        self._drain_budget_exceeded = 0
        self._backpressure_warned = 0
        self._last_sampled_drain_log_mono = 0.0

    def register_handler(
        self, event_type: str, handler: Callable[[dict[str, Any]], None]
    ) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def post(self, event_type: str, payload: Optional[dict[str, Any]] = None) -> None:
        if not LANGUAGE_AGNOSTIC_UI_EVENT_BUS:
            return
        ev = UIEvent(event_type=event_type, payload=dict(payload or {}))
        self._enqueue(ev)

    def post_schedule_after(
        self,
        ms: int,
        callback: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: Optional[dict[str, Any]] = None,
    ) -> None:
        """Background-safe substitute for root.after — drained on UI thread only."""
        ev = UIEvent(
            event_type="__schedule_after__",
            schedule_after_ms=max(0, int(ms)),
            callback=callback,
            callback_args=args,
            callback_kwargs=dict(kwargs or {}),
        )
        self._enqueue(ev)

    def _enqueue(self, ev: UIEvent) -> None:
        qsize = self._queue.qsize()
        if qsize > _DROP_QUEUE and ev.event_type in _COALESCE_EVENTS:
            with self._stats_lock:
                self._dropped += 1
            self._log_once(
                "UI_EVENT_QUEUE_BACKPRESSURE",
                event_type=ev.event_type,
                queue_size=qsize,
                action="dropped_non_critical",
            )
            return
        if qsize > _WARN_QUEUE:
            self._log_once(
                "UI_EVENT_QUEUE_BACKPRESSURE",
                event_type=ev.event_type,
                queue_size=qsize,
                action="warn",
            )
        try:
            self._queue.put_nowait(ev)
            with self._stats_lock:
                self._posted += 1
                self._last_post_mono = time.monotonic()
            if ev.event_type != "__schedule_after__":
                self._log_throttled("UI_EVENT_POSTED", event_type=ev.event_type, queue_size=qsize + 1)
        except Exception:
            with self._stats_lock:
                self._dropped += 1

    def drain_until_empty(
        self,
        host: Any,
        *,
        max_rounds: int = 200,
        time_budget_ms: float = 500.0,
    ) -> dict[str, Any]:
        """Drain the UI event bus until empty or budget exhausted (Stop barrier)."""
        start = time.perf_counter()
        total_processed = 0
        rounds = 0
        while rounds < max_rounds:
            remaining = self._queue.qsize()
            if remaining == 0:
                break
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if elapsed_ms >= time_budget_ms:
                break
            result = self.drain(
                host,
                max_events=max(remaining, _DEFAULT_MAX_EVENTS),
                time_budget_ms=max(1.0, time_budget_ms - elapsed_ms),
            )
            total_processed += int(result.get("processed", 0) or 0)
            rounds += 1
            if int(result.get("remaining", 0) or 0) == 0:
                break
        stats = self.stats()
        return {
            "processed": total_processed,
            "rounds": rounds,
            "ui_bus_events_posted": stats.get("ui_bus_events_posted", 0),
            "ui_bus_events_drained": stats.get("ui_bus_events_drained", 0),
            "ui_bus_events_dropped": stats.get("ui_bus_events_dropped", 0),
            "ui_bus_queue_remaining": stats.get("ui_bus_queue_remaining", 0),
        }

    def drain(
        self,
        host: Any,
        *,
        max_events: int = _DEFAULT_MAX_EVENTS,
        time_budget_ms: float = _DEFAULT_TIME_BUDGET_MS,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        processed = 0
        remaining = self._queue.qsize()
        budget_exceeded = False
        self._log_throttled("UI_EVENT_DRAIN_STARTED", queue_size=remaining)
        while processed < max_events:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if elapsed_ms >= time_budget_ms:
                budget_exceeded = True
                break
            try:
                ev = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._dispatch(host, ev)
            except Exception as exc:
                self._log_throttled(
                    "UI_EVENT_HANDLER_EXCEPTION",
                    event_type=ev.event_type,
                    error=str(exc),
                )
            processed += 1
        remaining = self._queue.qsize()
        with self._stats_lock:
            self._drained += processed
            self._last_drain_mono = time.monotonic()
            if budget_exceeded:
                self._drain_budget_exceeded += 1
        if budget_exceeded:
            self._log_throttled(
                "UI_EVENT_DRAIN_TIME_BUDGET_EXCEEDED",
                processed=processed,
                remaining=remaining,
            )
        self._log_throttled(
            "UI_EVENT_DRAIN_COMPLETED",
            processed=processed,
            remaining=remaining,
        )
        return {
            "processed": processed,
            "remaining": remaining,
            "budget_exceeded": budget_exceeded,
        }

    def _dispatch(self, host: Any, ev: UIEvent) -> None:
        if ev.event_type == "__schedule_after__":
            if ev.callback is None:
                return
            # Drain already runs on the Tk main thread. Execute immediately when
            # delay is 0 so translation UI updates are not lost if later after()
            # jobs are cancelled during Stop. Non-zero delays still use after().
            if int(ev.schedule_after_ms or 0) <= 0:
                ev.callback(*ev.callback_args, **ev.callback_kwargs)
                return
            after = getattr(host, "after", None)
            if callable(after):
                after(ev.schedule_after_ms, ev.callback, *ev.callback_args)
            return
        for handler in self._handlers.get(ev.event_type, []):
            handler(ev.payload)
        for handler in self._handlers.get("*", []):
            handler({"event_type": ev.event_type, **ev.payload})

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._log_once("UI_EVENT_BUS_STARTED")

    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                "ui_event_bus_queue_size": self._queue.qsize(),
                "ui_bus_queue_remaining": self._queue.qsize(),
                "ui_event_posted_count": self._posted,
                "ui_bus_events_posted": self._posted,
                "ui_event_drained_count": self._drained,
                "ui_bus_events_drained": self._drained,
                "ui_event_dropped_count": self._dropped,
                "ui_bus_events_dropped": self._dropped,
                "last_ui_event_post_ts": self._last_post_mono,
                "last_ui_event_drain_ts": self._last_drain_mono,
                "ui_event_drain_budget_exceeded_count": self._drain_budget_exceeded,
            }

    def _log_once(self, event: str, **data: Any) -> None:
        try:
            from alpha.utils.evidence_jsonl import append_jsonl_named
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(event, **data)
            if event in (
                "UI_EVENT_DRAIN_TIME_BUDGET_EXCEEDED",
                "UI_EVENT_QUEUE_BACKPRESSURE",
                "UI_EVENT_HANDLER_EXCEPTION",
            ):
                append_jsonl_named(
                    "log",
                    "ui_event_bus_timeline",
                    {
                        "event": event,
                        **data,
                    },
                )
        except Exception:
            pass

    def _log_throttled(self, event: str, **data: Any) -> None:
        try:
            from alpha.utils.evidence_jsonl import append_jsonl_named
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            if event in ("UI_EVENT_DRAIN_STARTED", "UI_EVENT_DRAIN_COMPLETED", "UI_EVENT_POSTED"):
                if not HIGH_FREQUENCY_UI_DRAIN_LOGGING_ENABLED and not UI_EVENT_DRAIN_VERBOSE_LOGGING:
                    now = time.monotonic()
                    if (
                        now - self._last_sampled_drain_log_mono
                    ) >= float(UI_EVENT_DRAIN_SAMPLE_INTERVAL_SECONDS):
                        self._last_sampled_drain_log_mono = now
                        append_jsonl_named(
                            "log",
                            "ui_event_bus_timeline",
                            {
                                "event": "UI_EVENT_BUS_SAMPLE",
                                "sampled_event": event,
                                **data,
                            },
                        )
                    return
            jp_accuracy_log(event, **data)
        except Exception:
            pass


def get_ui_event_bus() -> UIEventBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = UIEventBus()
        return _bus


def start_ui_event_bus() -> UIEventBus:
    bus = get_ui_event_bus()
    bus.start()
    return bus


def get_ui_event_bus_poll_ms() -> int:
    return _POLL_MS
