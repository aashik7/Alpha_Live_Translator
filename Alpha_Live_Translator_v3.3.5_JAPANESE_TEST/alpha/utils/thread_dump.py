"""Reliable thread dumps on Windows — real files + Python fallback."""

from __future__ import annotations

import faulthandler
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional

_thread_dump_failed_count = 0
_last_dump_paths: dict[str, str] = {}


def get_thread_dump_failed_count() -> int:
    return int(_thread_dump_failed_count)


def get_last_thread_dump_paths() -> dict[str, str]:
    return dict(_last_dump_paths)


def _python_thread_dump_text() -> str:
    lines = [f"# python_thread_dump at {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    frames = sys._current_frames()
    thread_map = {t.ident: t for t in threading.enumerate() if t.ident is not None}
    for thread_id, frame in frames.items():
        thread = thread_map.get(thread_id)
        name = thread.name if thread is not None else f"thread-{thread_id}"
        lines.append(f"--- Thread {name} (id={thread_id}) ---")
        lines.extend(traceback.format_stack(frame))
        lines.append("")
    return "\n".join(lines)


def write_thread_dumps(
    folder: Path,
    *,
    reason: str,
) -> dict[str, Any]:
    """Write faulthandler + Python dumps; update THREAD_DUMP_LAST.txt."""
    global _thread_dump_failed_count, _last_dump_paths
    folder.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "reason": reason,
        "faulthandler_ok": False,
        "python_ok": False,
        "last_path": "",
    }
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("THREAD_DUMP_REQUESTED", reason=reason)
        freeze_guard_log_sync("THREAD_DUMP_REQUESTED", reason=reason)
    except Exception:
        pass

    fh_path = folder / "THREAD_DUMP_FAULTHANDLER.txt"
    py_path = folder / "THREAD_DUMP_PYTHON.txt"
    last_path = folder / "THREAD_DUMP_LAST.txt"

    fh_text = ""
    try:
        with open(fh_path, "w", encoding="utf-8") as fh_file:
            faulthandler.dump_traceback(file=fh_file, all_threads=True)
        fh_text = fh_path.read_text(encoding="utf-8", errors="ignore")
        result["faulthandler_ok"] = bool(fh_text.strip())
        if result["faulthandler_ok"]:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "THREAD_DUMP_FAULTHANDLER_WRITTEN",
                    path=str(fh_path),
                    reason=reason,
                )
            except Exception:
                pass
    except Exception as exc:
        _thread_dump_failed_count += 1
        fh_text = f"# faulthandler failed: {exc}\n"
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "THREAD_DUMP_FAILED",
                method="faulthandler",
                reason=reason,
                error=str(exc),
            )
        except Exception:
            pass

    py_text = ""
    try:
        py_text = _python_thread_dump_text()
        py_path.write_text(py_text, encoding="utf-8")
        result["python_ok"] = bool(py_text.strip())
        if result["python_ok"]:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "THREAD_DUMP_PYTHON_WRITTEN",
                    path=str(py_path),
                    reason=reason,
                )
            except Exception:
                pass
    except Exception as exc:
        _thread_dump_failed_count += 1
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "THREAD_DUMP_FAILED",
                method="python",
                reason=reason,
                error=str(exc),
            )
        except Exception:
            pass

    combined = (
        f"# thread_dump reason={reason} at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"=== FAULTHANDLER ===\n{fh_text}\n\n=== PYTHON ===\n{py_text}\n"
    )
    last_path.write_text(combined, encoding="utf-8")
    result["last_path"] = str(last_path)
    _last_dump_paths = {
        "faulthandler": str(fh_path),
        "python": str(py_path),
        "last": str(last_path),
    }
    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "THREAD_DUMP_LAST_UPDATED",
            path=str(last_path),
            reason=reason,
            faulthandler_ok=result["faulthandler_ok"],
            python_ok=result["python_ok"],
        )
        freeze_guard_log_sync("THREAD_DUMP_LAST_UPDATED", path=str(last_path))
        freeze_guard_log_sync("ACTIVE_SESSION_THREAD_DUMP_WRITTEN", reason=reason)
    except Exception:
        pass
    return result


def run_thread_dump_selftest(folder: Path) -> dict[str, Any]:
    """Write THREAD_DUMP_SELFTEST.txt once at listen start; verify key threads."""
    folder.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"passed": False, "path": ""}
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("THREAD_DUMP_SELFTEST_STARTED")
    except Exception:
        pass
    path = folder / "THREAD_DUMP_SELFTEST.txt"
    fh_text = ""
    py_text = _python_thread_dump_text()
    try:
        with open(path, "w", encoding="utf-8") as fh_file:
            faulthandler.dump_traceback(file=fh_file, all_threads=True)
        fh_text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        fh_text = f"# faulthandler failed: {exc}\n"
    combined = (
        f"# thread_dump_selftest at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"=== FAULTHANDLER ===\n{fh_text}\n\n=== PYTHON ===\n{py_text}\n"
    )
    path.write_text(combined, encoding="utf-8")
    result["path"] = str(path)
    combined_lower = combined.lower()
    has_main = "mainthread" in combined_lower or "main thread" in combined_lower
    has_watchdog = "sessionhangwatchdog" in combined_lower or "watchdog" in combined_lower
    has_autosave = "partialartifactautosaveworker" in combined_lower or "autosave" in combined_lower
    result["passed"] = has_main and (has_watchdog or has_autosave)
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        if result["passed"]:
            jp_accuracy_log(
                "THREAD_DUMP_SELFTEST_PASSED",
                path=str(path),
                has_main=has_main,
                has_watchdog=has_watchdog,
                has_autosave=has_autosave,
            )
        else:
            jp_accuracy_log(
                "THREAD_DUMP_FAILED",
                method="selftest",
                path=str(path),
                has_main=has_main,
                has_watchdog=has_watchdog,
                has_autosave=has_autosave,
            )
    except Exception:
        pass
    return result


def write_thread_dump_stall(folder: Path, *, reason: str) -> dict[str, Any]:
    """Write stall-specific dump plus THREAD_DUMP_LAST.txt."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    stall_path = folder / f"THREAD_DUMP_UI_STALL_{ts}.txt"
    result = write_thread_dumps(folder, reason=reason)
    try:
        last = folder / "THREAD_DUMP_LAST.txt"
        if last.exists():
            stall_path.write_text(last.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            result["stall_path"] = str(stall_path)
    except Exception:
        pass
    return result
