"""Live pipeline monotonic profiling (Start / transcript / translation / Stop).

Always records in-memory; optional JSON export under a run or repair folder.
Uses time.perf_counter() for durations. Wall clock is stored only as metadata.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_lock = threading.RLock()
_session_id: str = ""
_t0: float = 0.0
_events: list[dict[str, Any]] = []
_marks: dict[str, float] = {}  # name -> perf_counter absolute
_meta: dict[str, Any] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def reset_session(session_id: str, *, meta: Optional[dict[str, Any]] = None) -> None:
    global _session_id, _t0, _events, _marks, _meta
    with _lock:
        _session_id = str(session_id or "")
        _t0 = time.perf_counter()
        _events = []
        _marks = {}
        _meta = dict(meta or {})
        _meta["session_id"] = _session_id
        _meta["reset_at_utc"] = _utc_now_iso()


def current_session_id() -> str:
    with _lock:
        return _session_id


def mark(name: str, *, session_id: Optional[str] = None, **extra: Any) -> float:
    """Record a named pipeline event. Returns ms since session reset."""
    global _t0
    now = time.perf_counter()
    with _lock:
        if session_id is not None and _session_id and str(session_id) != _session_id:
            return -1.0
        if _t0 <= 0.0:
            _t0 = now
        elapsed_ms = round((now - _t0) * 1000.0, 3)
        _marks[name] = now
        rec = {
            "event": name,
            "session_id": _session_id,
            "perf_counter": now,
            "elapsed_ms_from_session_start": elapsed_ms,
            "wall_utc": _utc_now_iso(),
        }
        if extra:
            rec["extra"] = extra
        _events.append(rec)
        return elapsed_ms


def duration_ms(start_name: str, end_name: str) -> Optional[float]:
    with _lock:
        a = _marks.get(start_name)
        b = _marks.get(end_name)
        if a is None or b is None:
            return None
        return round((b - a) * 1000.0, 3)


def snapshot() -> dict[str, Any]:
    with _lock:
        marks_elapsed = {
            k: round((v - _t0) * 1000.0, 3) for k, v in _marks.items() if _t0 > 0
        }
        return {
            "session_id": _session_id,
            "meta": dict(_meta),
            "marks_ms_from_session_start": marks_elapsed,
            "events": list(_events),
            "durations_ms": {
                "start_ack": duration_ms("start_button_clicked_at", "start_ui_acknowledged_at"),
                "start_to_listening": duration_ms(
                    "start_button_clicked_at", "listening_state_visible_at"
                ),
                "stop_ack": duration_ms("stop_button_clicked_at", "stop_ui_acknowledged_at"),
                "stop_total": duration_ms("stop_button_clicked_at", "stop_completed_at"),
            },
        }


def write_json(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def slowest_stage(pairs: list[tuple[str, str, str]]) -> dict[str, Any]:
    """pairs: (label, start_mark, end_mark)."""
    best: dict[str, Any] = {"label": None, "duration_ms": None}
    for label, a, b in pairs:
        d = duration_ms(a, b)
        if d is None:
            continue
        if best["duration_ms"] is None or d > float(best["duration_ms"]):
            best = {"label": label, "duration_ms": d, "start": a, "end": b}
    return best
