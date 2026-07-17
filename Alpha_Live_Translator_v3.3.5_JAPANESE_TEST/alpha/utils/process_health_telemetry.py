"""Process memory/RSS telemetry with psutil and Windows ctypes fallback."""

from __future__ import annotations

import gc
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

_MEMORY_HIGH_MB = 2000.0
_MEMORY_GROWTH_WARNING_MB = 500.0
_THREAD_COUNT_HIGH = 80
_HANDLE_COUNT_HIGH = 2000

_baseline_rss_mb: Optional[float] = None
_max_rss_mb: float = 0.0
_final_rss_mb: Optional[float] = None
_samples_count: int = 0
_metrics_unavailable_logged = False
_telemetry_backend = "unavailable"
_lock = threading.Lock()


def _windows_memory_mb() -> tuple[float, float]:
    """Return (rss_mb, private_mb) via ctypes on Windows."""
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.WinDLL("psapi")
    handle = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    rss = counters.WorkingSetSize / (1024 * 1024)
    private = counters.PagefileUsage / (1024 * 1024)
    return round(rss, 1), round(private, 1)


def collect_process_metrics() -> dict[str, Any]:
    """Collect process metrics; never raises."""
    global _baseline_rss_mb, _max_rss_mb, _final_rss_mb, _samples_count
    global _metrics_unavailable_logged, _telemetry_backend

    payload: dict[str, Any] = {
        "timestamp": time.time(),
        "python_thread_count": threading.active_count(),
        "gc_counts": list(gc.get_count()),
        "uptime_seconds": round(time.monotonic(), 1),
        "telemetry_backend": _telemetry_backend,
    }

    rss_mb = -1.0
    private_mb = -1.0
    cpu_percent: Optional[float] = None
    thread_count: Optional[int] = None
    handle_count: Optional[int] = None
    backend = "unavailable"
    unavailable_reason = ""

    try:
        import psutil  # type: ignore

        proc = psutil.Process()
        mem = proc.memory_info()
        rss_mb = round(mem.rss / (1024 * 1024), 1)
        private_mb = round(getattr(mem, "private", mem.rss) / (1024 * 1024), 1)
        backend = "psutil"
        try:
            cpu_percent = round(proc.cpu_percent(interval=None), 2)
        except Exception:
            pass
        try:
            thread_count = int(proc.num_threads())
        except Exception:
            pass
        try:
            handle_count = int(proc.num_handles())  # type: ignore[attr-defined]
        except Exception:
            pass
    except ImportError:
        if sys.platform == "win32":
            try:
                rss_mb, private_mb = _windows_memory_mb()
                backend = "windows_ctypes"
            except Exception as exc:
                unavailable_reason = f"windows_ctypes_failed:{exc}"
        else:
            unavailable_reason = "psutil_missing_non_windows"
        if rss_mb < 0 and not _metrics_unavailable_logged:
            _metrics_unavailable_logged = True
            try:
                from alpha.utils.freeze_guard_log import freeze_guard_log_sync

                freeze_guard_log_sync(
                    "PROCESS_METRICS_UNAVAILABLE",
                    reason=unavailable_reason or "psutil_missing_and_fallback_failed",
                )
            except Exception:
                pass
    except Exception as exc:
        unavailable_reason = f"collection_failed:{exc}"
        if rss_mb < 0 and not _metrics_unavailable_logged:
            _metrics_unavailable_logged = True
            try:
                from alpha.utils.freeze_guard_log import freeze_guard_log_sync

                freeze_guard_log_sync(
                    "PROCESS_METRICS_UNAVAILABLE",
                    reason=unavailable_reason,
                )
            except Exception:
                pass

    _telemetry_backend = backend
    payload["telemetry_backend"] = backend
    if unavailable_reason:
        payload["telemetry_unavailable_reason"] = unavailable_reason

    if rss_mb >= 0:
        payload["process_memory_rss_mb"] = rss_mb
        payload["process_memory_private_mb"] = private_mb
        with _lock:
            if _baseline_rss_mb is None:
                _baseline_rss_mb = rss_mb
            _max_rss_mb = max(_max_rss_mb, rss_mb)
            _final_rss_mb = rss_mb
            _samples_count += 1
            baseline = _baseline_rss_mb or rss_mb
            payload["memory_baseline_mb"] = round(baseline, 1)
            payload["memory_delta_mb"] = round(rss_mb - baseline, 1)
            payload["memory_max_mb"] = round(_max_rss_mb, 1)
            payload["memory_samples_count"] = _samples_count
    else:
        payload["process_memory_rss_mb"] = -1
        payload["process_memory_mb"] = -1

    if cpu_percent is not None:
        payload["process_cpu_percent"] = cpu_percent
    if thread_count is not None:
        payload["process_thread_count"] = thread_count
    if handle_count is not None:
        payload["handle_count"] = handle_count

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("PROCESS_HEALTH_SNAPSHOT", **{k: payload[k] for k in (
            "process_memory_rss_mb", "telemetry_backend", "memory_delta_mb"
        ) if k in payload})
    except Exception:
        pass

    return payload


def evaluate_process_thresholds(payload: dict[str, Any]) -> None:
    """Log threshold warnings; never raises."""
    rss = float(payload.get("process_memory_rss_mb", -1))
    if rss < 0:
        return
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        delta = float(payload.get("memory_delta_mb", 0))
        if delta > _MEMORY_GROWTH_WARNING_MB:
            jp_accuracy_log(
                "PROCESS_MEMORY_GROWTH_WARNING",
                process_memory_rss_mb=rss,
                memory_delta_mb=delta,
            )
            freeze_guard_log_sync(
                "PROCESS_MEMORY_GROWTH_WARNING",
                process_memory_rss_mb=rss,
                memory_delta_mb=delta,
            )
        if rss > _MEMORY_HIGH_MB:
            jp_accuracy_log("PROCESS_MEMORY_HIGH", process_memory_rss_mb=rss)
            freeze_guard_log_sync("PROCESS_MEMORY_HIGH", process_memory_rss_mb=rss)
        threads = int(payload.get("process_thread_count", payload.get("python_thread_count", 0)))
        if threads > _THREAD_COUNT_HIGH:
            freeze_guard_log_sync("PROCESS_THREAD_COUNT_HIGH", thread_count=threads)
        handles = payload.get("handle_count")
        if handles is not None and int(handles) > _HANDLE_COUNT_HIGH:
            freeze_guard_log_sync("PROCESS_HANDLE_COUNT_HIGH", handle_count=handles)
    except Exception:
        pass


def write_process_health_timeline(payload: dict[str, Any]) -> Optional[Path]:
    """Append one line to PROCESS_HEALTH_TIMELINE.jsonl."""
    try:
        from alpha.utils.troubleshooting_paths import get_health_path

        path = get_health_path("process_health_timeline")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("PROCESS_HEALTH_SNAPSHOT_WRITTEN", path=str(path))
            jp_accuracy_log("PROCESS_HEALTH_TIMELINE_CREATED", path=str(path))
        except Exception:
            pass
        return path
    except Exception:
        return None


def write_memory_trend_summary() -> Optional[Path]:
    summary = get_memory_trend_summary()
    try:
        from alpha.utils.troubleshooting_paths import get_health_path

        path = get_health_path("memory_trend_summary")
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:
        return None


def get_memory_trend_summary() -> dict[str, Any]:
    with _lock:
        baseline = _baseline_rss_mb
        final = _final_rss_mb
        delta = round((final or 0) - (baseline or 0), 1) if final is not None and baseline is not None else 0.0
        return {
            "memory_baseline_mb": baseline,
            "memory_final_mb": final,
            "memory_max_mb": round(_max_rss_mb, 1) if _max_rss_mb else -1,
            "memory_delta_mb": delta,
            "memory_samples_count": _samples_count,
            "telemetry_backend": _telemetry_backend,
            "memory_growth_warning": delta > _MEMORY_GROWTH_WARNING_MB,
            "process_thread_growth_warning": False,
        }


def reset_process_health_telemetry() -> None:
    global _baseline_rss_mb, _max_rss_mb, _final_rss_mb, _samples_count
    global _metrics_unavailable_logged, _telemetry_backend
    with _lock:
        _baseline_rss_mb = None
        _max_rss_mb = 0.0
        _final_rss_mb = None
        _samples_count = 0
        _metrics_unavailable_logged = False
        _telemetry_backend = "unavailable"
