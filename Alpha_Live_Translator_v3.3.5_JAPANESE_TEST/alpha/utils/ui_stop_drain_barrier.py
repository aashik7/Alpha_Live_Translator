"""UI-main-thread drain barrier for Stop — one after post, worker waits for ack."""

from __future__ import annotations

import threading
import time
from typing import Any

_DEFAULT_TIMEOUT_S = 2.0


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _safe_qsize(q: Any) -> int:
    if q is None:
        return 0
    try:
        return max(0, int(q.qsize()))
    except Exception:
        return 0


def drain_stop_queues_on_main_thread(host: Any) -> dict[str, Any]:
    """Drain transcript queue, UI batch buffer, and UI event bus on the Tk thread."""
    transcript_drained = 0
    transcript_remaining = 0
    ui_bus_result: dict[str, Any] = {}

    if hasattr(host, "_flush_pending_transcript_queue"):
        try:
            before = _safe_qsize(getattr(host, "transcript_queue", None))
            host._flush_pending_transcript_queue()
            after = _safe_qsize(getattr(host, "transcript_queue", None))
            transcript_drained = max(0, before - after) + after
            transcript_remaining = after
        except Exception as exc:
            _jp_log(
                "UI_STOP_DRAIN_TRANSCRIPT_FLUSH_FAILED",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )

    if hasattr(host, "drain_transcript_queue_for_stop"):
        try:
            extra = host.drain_transcript_queue_for_stop()
            if isinstance(extra, dict):
                transcript_drained += int(extra.get("drained", 0) or 0)
                transcript_remaining = int(
                    extra.get("remaining", transcript_remaining) or transcript_remaining
                )
        except Exception:
            pass

    try:
        from alpha.utils.ui_event_bus import get_ui_event_bus

        bus = get_ui_event_bus()
        ui_bus_result = bus.drain_until_empty(host)
    except Exception as exc:
        ui_bus_result = {"error": str(exc)}

    batch_remaining = len(getattr(host, "_transcript_ui_batch_buffer", []) or [])
    transcript_remaining = max(
        transcript_remaining,
        _safe_qsize(getattr(host, "transcript_queue", None)),
        batch_remaining,
    )

    posted = int(getattr(host, "_transcript_events_posted", 0) or 0)
    drained = int(getattr(host, "_transcript_events_drained", 0) or 0)

    result = {
        "transcript_events_posted": posted,
        "transcript_events_drained": drained,
        "transcript_queue_remaining": transcript_remaining,
        "ui_bus_events_posted": ui_bus_result.get("ui_bus_events_posted", 0),
        "ui_bus_events_drained": ui_bus_result.get("ui_bus_events_drained", 0),
        "ui_bus_events_dropped": ui_bus_result.get("ui_bus_events_dropped", 0),
        "ui_bus_queue_remaining": ui_bus_result.get("ui_bus_queue_remaining", 0),
        "ui_bus_processed": ui_bus_result.get("processed", 0),
        "passed": transcript_remaining == 0
        and int(ui_bus_result.get("ui_bus_queue_remaining", 0) or 0) == 0,
    }

    if result["passed"]:
        _jp_log("UI_TRANSCRIPT_DRAIN_COMPLETED", **result)
    else:
        _jp_log("UI_TRANSCRIPT_DRAIN_INCOMPLETE", **result)
    return result


def request_stop_ui_drain(
    host: Any, *, timeout_seconds: float = _DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    """
    Stop worker posts exactly one UI drain request and waits for main-thread ack.

    Never calls Tk directly from the Stop worker thread.
    """
    ack = threading.Event()
    result_holder: dict[str, Any] = {"posted": False, "timed_out": False}

    def _on_main() -> None:
        try:
            result_holder["drain"] = drain_stop_queues_on_main_thread(host)
            result_holder["ok"] = bool(result_holder["drain"].get("passed", False))
        except Exception as exc:
            result_holder["ok"] = False
            result_holder["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            ack.set()

    runner = getattr(host, "_run_on_ui_thread", None)
    if not callable(runner):
        _jp_log("UI_STOP_DRAIN_BARRIER_SKIPPED", reason="no_ui_runner")
        return {"ok": False, "reason": "no_ui_runner"}

    result_holder["begin_mono"] = time.monotonic()
    _jp_log("UI_STOP_DRAIN_BARRIER_REQUESTED", timeout_seconds=timeout_seconds)
    try:
        runner(_on_main)
        result_holder["posted"] = True
    except Exception as exc:
        _jp_log(
            "UI_STOP_DRAIN_BARRIER_POST_FAILED",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )
        return {"ok": False, "reason": "post_failed", "error": str(exc)}

    if not ack.wait(timeout=max(0.1, float(timeout_seconds))):
        result_holder["timed_out"] = True
        _jp_log("UI_STOP_DRAIN_BARRIER_TIMEOUT", timeout_seconds=timeout_seconds)
        return {
            "ok": False,
            "timed_out": True,
            "posted": True,
            "transcript_queue_remaining": _safe_qsize(
                getattr(host, "transcript_queue", None)
            ),
        }

    drain = dict(result_holder.get("drain") or {})
    drain["ok"] = bool(result_holder.get("ok", False))
    drain["timed_out"] = False
    drain["duration_ms"] = round(
        (time.monotonic() - float(result_holder.get("begin_mono", time.monotonic())))
        * 1000.0,
        2,
    )
    _jp_log("UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED", **drain)
    return drain
