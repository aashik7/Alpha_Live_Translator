"""Monotonic phase timing for multidomain gate / Alpha lifecycle (V26.5.1).

Writes run-specific ``performance_timeline.json`` with flushed console progress.
Never recursively scans troubleshooting trees.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class PerformanceTimeline:
    """Thread-safe phase timer that emits flushed progress at least every 10s."""

    def __init__(self, *, run_id: str, output_path: Path):
        self.run_id = str(run_id or "")
        self.output_path = Path(output_path)
        self._lock = threading.Lock()
        self._phases: list[dict[str, Any]] = []
        self._open: dict[str, dict[str, Any]] = {}
        self._t0 = time.perf_counter()
        self._last_progress = self._t0
        self._heartbeat_stop = threading.Event()
        self._heartbeat: Any = None

    def start_heartbeat(self, interval_s: float = 10.0) -> None:
        """Start, or restart, the progress heartbeat.

        Two defects fixed here (mitigation.md A4), and they compounded:

        * this returned early on ``_heartbeat is not None``, so once the thread
          existed a second call was a silent no-op even if the thread was dead;
        * ``stop_heartbeat`` set ``_heartbeat_stop`` and nothing ever cleared it,
          so stopping the heartbeat was irreversible *on purpose* as well as by
          accident. Measured before the fix: alive after start True, after stop
          False, after a restart attempt False.

        The supervisor clears the stop event on ``start()``, and the liveness
        gate replaces the flag. ``self.progress("heartbeat")`` was also
        unguarded, so one exception from it ended the thread; it is now
        restarted within a bounded budget.
        """
        supervisor = self._heartbeat
        if supervisor is not None and supervisor.is_alive():
            return

        def _loop() -> None:
            while not self._heartbeat_stop.wait(interval_s):
                self.progress("heartbeat")

        try:
            from alpha.utils.supervised_thread import SupervisedThread

            if supervisor is None:
                supervisor = SupervisedThread(
                    _loop,
                    name=f"PerformanceTimelineHeartbeat:{self.run_id or id(self)}",
                    stop_event=self._heartbeat_stop,
                )
                self._heartbeat = supervisor
            supervisor.start()
        except Exception:
            # Telemetry must never stop the timeline it is measuring.
            pass

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()

    def heartbeat_snapshot(self) -> dict[str, Any]:
        supervisor = self._heartbeat
        if supervisor is None:
            return {"alive": False, "restart_count": 0, "gave_up": False}
        try:
            return supervisor.snapshot()
        except Exception:
            return {"alive": False, "restart_count": 0, "gave_up": False}

    def begin(
        self,
        phase: str,
        *,
        blocking_operation: str | None = None,
        status: str = "running",
    ) -> None:
        now = time.perf_counter()
        wall = _utc_now_iso()
        with self._lock:
            self._open[phase] = {
                "phase": phase,
                "start_time": wall,
                "start_perf": now,
                "status": status,
                "run_id": self.run_id,
                "blocking_operation": blocking_operation,
            }
        self._print(f"[progress] phase={phase} status=start elapsed_s={now - self._t0:.1f}")

    def end(
        self,
        phase: str,
        *,
        status: str = "ok",
        blocking_operation: str | None = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        now = time.perf_counter()
        wall = _utc_now_iso()
        with self._lock:
            opened = self._open.pop(phase, None)
            start_perf = float((opened or {}).get("start_perf") or now)
            record = {
                "phase": phase,
                "start_time": (opened or {}).get("start_time") or wall,
                "end_time": wall,
                "elapsed_ms": round((now - start_perf) * 1000.0, 3),
                "status": status,
                "run_id": self.run_id,
                "blocking_operation": blocking_operation
                if blocking_operation is not None
                else (opened or {}).get("blocking_operation"),
            }
            if extra:
                record["extra"] = extra
            self._phases.append(record)
        self._print(
            f"[progress] phase={phase} status={status} "
            f"elapsed_ms={record['elapsed_ms']:.1f} total_s={now - self._t0:.1f}"
        )
        self.flush()
        return record

    def progress(self, note: str = "") -> None:
        now = time.perf_counter()
        with self._lock:
            open_phases = sorted(self._open.keys())
            if now - self._last_progress < 9.5 and note == "heartbeat":
                return
            self._last_progress = now
        label = ",".join(open_phases) if open_phases else "idle"
        suffix = f" note={note}" if note and note != "heartbeat" else ""
        self._print(
            f"[progress] phase={label} status=running "
            f"elapsed_s={now - self._t0:.1f}{suffix}"
        )

    def flush(self) -> Path:
        payload = {
            "kind": "performance_timeline",
            "run_id": self.run_id,
            "generated_at": _utc_now_iso(),
            "phases": list(self._phases),
            "open_phases": sorted(self._open.keys()),
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.output_path)
        return self.output_path

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "kind": "performance_timeline",
                "run_id": self.run_id,
                "phases": list(self._phases),
            }

    @staticmethod
    def _print(message: str) -> None:
        print(message, flush=True)
        try:
            sys.stdout.flush()
        except Exception:
            pass


def write_offline_performance_timeline(
    *,
    run_id: str,
    output_path: Path,
    phases: list[dict[str, Any]],
) -> Path:
    """Persist a completed offline / synthetic timeline with required fields."""
    normalized: list[dict[str, Any]] = []
    for row in phases:
        normalized.append(
            {
                "phase": row.get("phase"),
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
                "elapsed_ms": row.get("elapsed_ms"),
                "status": row.get("status") or "ok",
                "run_id": run_id,
                "blocking_operation": row.get("blocking_operation"),
            }
        )
    payload = {
        "kind": "performance_timeline",
        "run_id": run_id,
        "generated_at": _utc_now_iso(),
        "phases": normalized,
        "synthetic": any(bool(p.get("synthetic")) for p in phases),
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path
