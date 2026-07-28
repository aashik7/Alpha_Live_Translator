"""Lightweight startup timeline / blocking / heartbeat instrumentation.

Enabled when ALPHA_STARTUP_PROFILE=1 (or always-record markers if force=True).
Never logs API keys or environment variable values.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

_t0: Optional[float] = None
_marks: dict[str, float] = {}
_blocking: list[dict[str, Any]] = []
_heartbeat_delays_ms: list[float] = []
_heartbeat_after_id = None
_heartbeat_expected = 0.0
_first_paint_ms: Optional[float] = None
_interactive_ms: Optional[float] = None
_enabled = False
_lock = threading.Lock()
_out_dir: Optional[Path] = None
_fs_scans: list[dict[str, Any]] = []
_thread_samples: list[dict[str, Any]] = []
_memory_samples: list[dict[str, Any]] = []
_BLOCK_THRESHOLD_MS = 50.0


def profiling_enabled() -> bool:
    return os.environ.get("ALPHA_STARTUP_PROFILE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def ensure_started(*, force: bool = False) -> None:
    global _t0, _enabled
    if _t0 is not None:
        return
    if not force and not profiling_enabled():
        return
    _enabled = True
    _t0 = time.perf_counter()
    mark("process_entry")


def mark(name: str, *, force: bool = False) -> float:
    global _first_paint_ms, _interactive_ms
    ensure_started()
    if _t0 is None:
        return 0.0
    elapsed_ms = round((time.perf_counter() - _t0) * 1000.0, 1)
    with _lock:
        # Real-Alpha markers may overwrite provisional values; splash marks are banned.
        sticky = (
            "first_visible_paint",
            "first_tk_idle_callback",
            "application_interactive_ready",
            "real_alpha_first_paint",
            "real_alpha_interactive_ready",
        )
        if (not force) and name in sticky and name in _marks:
            # Allow real_alpha_* / first_visible_paint to be set once from real UI path
            if name.startswith("real_alpha_") or name in (
                "first_visible_paint",
                "application_interactive_ready",
                "first_tk_idle_callback",
            ):
                # Keep first write (should be real Alpha only after splash removal)
                return _marks[name]
        _marks[name] = elapsed_ms
        if name in ("first_visible_paint", "first_tk_idle_callback", "real_alpha_first_paint"):
            if _first_paint_ms is None or name == "real_alpha_first_paint":
                _first_paint_ms = elapsed_ms
        if name in ("application_interactive_ready", "real_alpha_interactive_ready"):
            _interactive_ms = elapsed_ms
    return elapsed_ms


def force_mark(name: str) -> float:
    """Overwrite a sticky marker (used when correcting splash → real Alpha)."""
    ensure_started()
    if _t0 is None:
        return 0.0
    elapsed_ms = round((time.perf_counter() - _t0) * 1000.0, 1)
    global _first_paint_ms, _interactive_ms
    with _lock:
        _marks[name] = elapsed_ms
        if name in ("first_visible_paint", "real_alpha_first_paint", "first_tk_idle_callback"):
            _first_paint_ms = elapsed_ms
        if name in ("application_interactive_ready", "real_alpha_interactive_ready"):
            _interactive_ms = elapsed_ms
    return elapsed_ms


def get_marks() -> dict[str, float]:
    with _lock:
        return dict(_marks)


def elapsed_ms() -> float:
    if _t0 is None:
        return 0.0
    return round((time.perf_counter() - _t0) * 1000.0, 1)


def record_blocking(
    operation: str,
    duration_ms: float,
    *,
    location: str = "",
    blocked_ui: bool = True,
) -> None:
    if _t0 is None or duration_ms < _BLOCK_THRESHOLD_MS:
        return
    before_paint = _first_paint_ms is None or elapsed_ms() < (_first_paint_ms or 0)
    with _lock:
        _blocking.append(
            {
                "operation": operation,
                "start_ms_since_process": round(elapsed_ms() - duration_ms, 1),
                "duration_ms": round(duration_ms, 1),
                "thread_name": threading.current_thread().name,
                "call_location": location,
                "before_first_paint": before_paint,
                "blocked_ui_event_loop": bool(
                    blocked_ui and threading.current_thread() is threading.main_thread()
                ),
            }
        )


def timed(operation: str, location: str = "") -> Callable:
    def deco(fn: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            ensure_started()
            if _t0 is None:
                return fn(*args, **kwargs)
            t1 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                record_blocking(
                    operation,
                    (time.perf_counter() - t1) * 1000.0,
                    location=location or operation,
                )

        return wrapper

    return deco


def measure_call(operation: str, fn: Callable, *args, location: str = "", **kwargs):
    ensure_started()
    t1 = time.perf_counter()
    try:
        return fn(*args, **kwargs)
    finally:
        record_blocking(
            operation,
            (time.perf_counter() - t1) * 1000.0,
            location=location or operation,
        )


def record_fs_scan(
    path: str,
    *,
    files_inspected: int = 0,
    bytes_inspected: int = 0,
    duration_ms: float = 0.0,
    reason: str = "",
) -> None:
    with _lock:
        _fs_scans.append(
            {
                "path": path,
                "files_inspected": files_inspected,
                "bytes_inspected": bytes_inspected,
                "duration_ms": round(duration_ms, 1),
                "reason": reason,
                "at_ms": elapsed_ms(),
            }
        )


def sample_threads(label: str) -> None:
    with _lock:
        _thread_samples.append(
            {
                "label": label,
                "at_ms": elapsed_ms(),
                "thread_count": threading.active_count(),
                "thread_names": sorted(t.name for t in threading.enumerate()),
            }
        )


def sample_memory(label: str) -> None:
    rss = None
    try:
        import psutil  # type: ignore

        rss = int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        try:
            import resource  # type: ignore

            rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        except Exception:
            rss = None
    with _lock:
        _memory_samples.append({"label": label, "at_ms": elapsed_ms(), "rss_bytes": rss})


def start_ui_heartbeat(root, interval_ms: int = 100) -> None:
    """Schedule temporary 100ms heartbeat; disable via stop_ui_heartbeat.

    The first callback only arms the clock after the real Alpha event loop is
    pumping. Recording that arming gap would falsely count mainloop startup as
    post-paint UI lag.
    """
    global _heartbeat_after_id, _heartbeat_expected
    if _t0 is None or not profiling_enabled():
        return
    if getattr(root, "_startup_perf_heartbeat_started", False):
        return
    root._startup_perf_heartbeat_started = True
    root._startup_perf_heartbeat_armed = False
    _heartbeat_expected = time.perf_counter() + (interval_ms / 1000.0)

    def _tick():
        global _heartbeat_after_id, _heartbeat_expected
        now = time.perf_counter()
        if not getattr(root, "_startup_perf_heartbeat_armed", False):
            root._startup_perf_heartbeat_armed = True
            root._startup_perf_heartbeat_warmup = 0
            _heartbeat_expected = now + (interval_ms / 1000.0)
        else:
            warmup = int(getattr(root, "_startup_perf_heartbeat_warmup", 0) or 0)
            delay_ms = max(0.0, (now - _heartbeat_expected) * 1000.0)
            # Skip one warm-up sample after arming (scheduler / first composition).
            if warmup < 1:
                root._startup_perf_heartbeat_warmup = warmup + 1
            else:
                with _lock:
                    _heartbeat_delays_ms.append(round(delay_ms, 1))
            _heartbeat_expected = now + (interval_ms / 1000.0)
        if getattr(root, "winfo_exists", lambda: False)() and getattr(
            root, "_startup_perf_heartbeat_started", False
        ):
            _heartbeat_after_id = root.after(interval_ms, _tick)

    _heartbeat_after_id = root.after(interval_ms, _tick)


def stop_ui_heartbeat(root) -> None:
    global _heartbeat_after_id
    root._startup_perf_heartbeat_started = False
    job = _heartbeat_after_id
    _heartbeat_after_id = None
    if job is not None:
        try:
            root.after_cancel(job)
        except Exception:
            pass


def _pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def durations_from_marks() -> dict[str, Optional[float]]:
    m = get_marks()

    def delta(a: str, b: str) -> Optional[float]:
        if a in m and b in m:
            return round(m[b] - m[a], 1)
        return None

    real_paint = m.get("real_alpha_first_paint") or m.get("first_visible_paint")
    real_interactive = m.get("real_alpha_interactive_ready") or m.get(
        "application_interactive_ready"
    )
    return {
        "total_import_duration_ms": delta(
            "main_module_import_started", "main_module_import_completed"
        ),
        "configuration_duration_ms": delta(
            "configuration_load_started", "configuration_load_completed"
        ),
        "logging_duration_ms": delta(
            "logging_initialization_started", "logging_initialization_completed"
        ),
        "app_construction_duration_ms": delta(
            "app_construction_started", "tkinter_root_created"
        ),
        "widget_construction_duration_ms": delta(
            "UI_widgets_construction_started", "UI_widgets_construction_completed"
        ),
        "time_to_first_window_ms": m.get("window_show_requested"),
        "time_to_first_paint_ms": real_paint,
        "time_to_interactive_ready_ms": real_interactive,
        "real_alpha_first_paint_ms": real_paint,
        "real_alpha_interactive_ready_ms": real_interactive,
        "background_initialization_duration_ms": delta(
            "background_initialization_started", "background_initialization_completed"
        ),
        "splash_excluded": True,
    }


def set_output_dir(path: Path) -> None:
    global _out_dir
    _out_dir = Path(path)
    _out_dir.mkdir(parents=True, exist_ok=True)


def default_output_dir() -> Path:
    override = os.environ.get("ALPHA_STARTUP_OUT", "").strip()
    if override:
        out = Path(override)
        out.mkdir(parents=True, exist_ok=True)
        return out
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    root = Path(__file__).resolve().parents[2]
    out = root / "troubleshooting" / f"startup_performance{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_artifacts(out_dir: Optional[Path] = None) -> Path:
    dest = Path(out_dir) if out_dir else (_out_dir or default_output_dir())
    dest.mkdir(parents=True, exist_ok=True)
    marks = get_marks()
    delays = list(_heartbeat_delays_ms)
    timeline = {
        "markers_ms": marks,
        "durations_ms": durations_from_marks(),
        "notes": "Sanitized; no env vars or API keys.",
    }
    (dest / "STARTUP_TIMELINE.json").write_text(
        json.dumps(timeline, indent=2), encoding="utf-8"
    )
    (dest / "MAIN_THREAD_BLOCKING_OPERATIONS.json").write_text(
        json.dumps({"threshold_ms": _BLOCK_THRESHOLD_MS, "operations": list(_blocking)}, indent=2),
        encoding="utf-8",
    )
    resp = {
        "sample_count": len(delays),
        "p50_event_loop_delay_ms": _pct(delays, 50),
        "p95_event_loop_delay_ms": _pct(delays, 95),
        "max_event_loop_delay_ms": max(delays) if delays else None,
        "delays_above_200_ms": sum(1 for d in delays if d > 200),
        "delays_above_500_ms": sum(1 for d in delays if d > 500),
        "longest_blocked_interval_ms": max(delays) if delays else None,
        "delays_ms": delays[-200:],
    }
    (dest / "UI_EVENT_LOOP_RESPONSIVENESS.json").write_text(
        json.dumps(resp, indent=2), encoding="utf-8"
    )
    (dest / "FILESYSTEM_STARTUP_ANALYSIS.json").write_text(
        json.dumps({"scans": list(_fs_scans)}, indent=2), encoding="utf-8"
    )
    (dest / "STARTUP_THREAD_ANALYSIS.json").write_text(
        json.dumps({"samples": list(_thread_samples)}, indent=2), encoding="utf-8"
    )
    (dest / "STARTUP_MEMORY_ANALYSIS.json").write_text(
        json.dumps({"samples": list(_memory_samples)}, indent=2), encoding="utf-8"
    )
    return dest


def install_autoquit(root, dump_dir: Optional[Path] = None) -> None:
    """When ALPHA_STARTUP_AUTOQUIT_MS is set, quit after real Alpha interactive + delay.

    Splash timing is never used. Quitting waits until real_alpha_interactive_ready
    (or application_interactive_ready) is marked, then keeps the heartbeat running
    for the configured delay so UI lag samples reflect the real Alpha window.
    """
    raw = os.environ.get("ALPHA_STARTUP_AUTOQUIT_MS", "").strip()
    if not raw:
        return
    try:
        delay_ms = max(50, int(raw))
    except ValueError:
        delay_ms = 800
    out = Path(dump_dir) if dump_dir else default_output_dir()
    deadline = time.perf_counter() + max(8.0, delay_ms / 1000.0 + 6.0)
    started_settle = {"v": False}

    def _finish():
        sample_threads("interactive_ready")
        sample_memory("interactive_ready")
        stop_ui_heartbeat(root)
        try:
            write_artifacts(out)
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    def _poll():
        marks = get_marks()
        painted = (
            "real_alpha_first_paint" in marks or "first_visible_paint" in marks
        )
        interactive = (
            "real_alpha_interactive_ready" in marks
            or "application_interactive_ready" in marks
        )
        if painted and interactive and not started_settle["v"]:
            started_settle["v"] = True
            sample_threads("first_paint")
            sample_memory("first_paint")
            root.after(delay_ms, _finish)
            return
        if time.perf_counter() >= deadline:
            # Honest timeout: dump whatever markers exist; do not invent ready.
            mark("autoquit_deadline_reached")
            _finish()
            return
        root.after(50, _poll)

    root.after_idle(_poll)


def safe_exc_summary(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:200]
