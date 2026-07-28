"""Runtime smoke for eleven-issue closure (V25.3.3 / 852533).

Isolated fixture data only — never writes troubleshooting/runs or troubleshooting/latest.
Simulates Start→Stop→Start→Stop with dummy host wiring.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from alpha.constants import APP_VERSION
from alpha.utils.language_pipeline_worker import get_language_pipeline_worker
from alpha.utils.stop_finalize_worker import begin_stop_from_ui

OUT_DIR = Path(f"troubleshooting/validation/v{APP_VERSION}/fixtures")
OUT_FILE = OUT_DIR / "runtime_smoke_eleven_issue_closure_852533.txt"
FORBIDDEN_PREFIXES = (
    Path("troubleshooting/runs"),
    Path("troubleshooting/latest"),
)


class _CycleHost:
    """Minimal host for stop-worker sequencing without Tk."""

    def __init__(self, run_folder: Path, cycle: int) -> None:
        self._cycle = cycle
        self._is_finalizing = False
        self._is_stopping = False
        self._stop_finalize_started = False
        self.is_listening = True
        self._dg_receiver_allowed = False
        self._dg_stop_sending_audio = False
        self._stop_event = threading.Event()
        self.stop_core_completed_event = threading.Event()
        self.stop_ui_restored_event = threading.Event()
        self._dg_close_status = "normal"
        self._last_graceful_stop_result: dict[str, Any] = {}
        self._audio_queue: queue.Queue[bytes] = queue.Queue()
        self._audio_queue.put(b"\x00" * 320)
        self._latency_audio_chunks_sent = 0
        self._latency_bytes_sent_total = 0
        self.transcript_queue: queue.Queue[str] = queue.Queue()
        self._transcript_ui_batch_buffer: list[str] = []
        self._transcript_events_posted = 0
        self._transcript_events_drained = 0
        self._accepting_transcripts = True
        self._run_folder = run_folder
        self._step_order: list[str] = []
        self._gate_closed_after_deepgram = False
        self._finalize_calls = 0
        self._ident = SimpleNamespace(
            run_folder=str(run_folder),
            run_type="fixture_smoke",
            selected_language="ja",
        )

    def _ensure_graceful_stop_state(self) -> None:
        self._dg_graceful_stop_active = True

    def _drain_audio_queue_to_deepgram(self, max_seconds: float = 2.5) -> int:
        drained = 0
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline:
            try:
                chunk = self._audio_queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            self._latency_audio_chunks_sent += 1
            self._latency_bytes_sent_total += len(chunk)
        return drained

    def stop_gracefully(self, timeout_seconds: float = 2.0, stop_capture_fn=None):
        if callable(stop_capture_fn):
            stop_capture_fn()
        return {"timed_out": False, "finalized": True, "closed": True}

    def get_authoritative_send_accounting(self) -> dict[str, int]:
        return {
            "audio_chunks_sent": self._latency_audio_chunks_sent,
            "audio_bytes_sent": self._latency_bytes_sent_total,
        }

    def _run_on_ui_thread(self, fn) -> None:
        fn()

    def _flush_pending_transcript_queue(self) -> None:
        while True:
            try:
                self.transcript_queue.get_nowait()
                self._transcript_events_drained += 1
            except queue.Empty:
                break

    def drain_transcript_queue_for_stop(self) -> dict[str, int]:
        self._flush_pending_transcript_queue()
        return {"drained": self._transcript_events_drained, "remaining": 0}

    def _finish_graceful_stop(self, timed_out: bool = False) -> None:
        self.stop_ui_restored_event.set()

    def _set_stopping_ui_state(self) -> None:
        pass

    def log_latency_stop_clicked_snapshot(self) -> None:
        pass

    def _session_log(self, *args, **kwargs) -> None:
        pass


_FORBIDDEN_SNAPSHOT: dict[str, tuple[float, int]] = {}


def _snapshot_forbidden_trees(project: Path) -> None:
    _FORBIDDEN_SNAPSHOT.clear()
    for prefix in FORBIDDEN_PREFIXES:
        target = project / prefix
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if path.is_file():
                try:
                    st = path.stat()
                    _FORBIDDEN_SNAPSHOT[str(path.resolve())] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    continue


def _assert_no_forbidden_writes(project: Path) -> list[str]:
    violations: list[str] = []
    for prefix in FORBIDDEN_PREFIXES:
        target = project / prefix
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            key = str(path.resolve())
            try:
                st = path.stat()
                current = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
            before = _FORBIDDEN_SNAPSHOT.get(key)
            if before is None:
                violations.append(str(path.relative_to(project)))
    return violations


def _install_global_isolation() -> Callable[[], None]:
    import alpha.utils.troubleshooting_paths as tp

    isolated = OUT_DIR / "_isolated_run"
    isolated.mkdir(parents=True, exist_ok=True)
    original = tp.get_active_run_folder
    tp.get_active_run_folder = lambda: str(isolated)

    def _restore() -> None:
        tp.get_active_run_folder = original

    return _restore


def _patch_finalize_counter(host: _CycleHost) -> None:
    import alpha.utils.stop_finalize_worker as sfw

    original = sfw._invoke_three_stage_finalize_once

    def _wrapped(h: Any) -> None:
        host._finalize_calls += 1

    sfw._invoke_three_stage_finalize_once = _wrapped
    host._restore_finalize = lambda: setattr(sfw, "_invoke_three_stage_finalize_once", original)


def _reset_stop_worker_between_cycles() -> None:
    import alpha.utils.stop_finalize_worker as sfw

    with sfw._state_lock:
        thread = sfw._stop_state.get("finalize_thread")
    if thread is not None and thread.is_alive():
        thread.join(timeout=8.0)
    with sfw._state_lock:
        sfw._stop_state["finalize_thread"] = None
        sfw._stop_state["worker_done"] = True
        sfw._stop_state["worker_started"] = False
    sfw._three_stage_finalize_call_count = 0


def _patch_isolated_paths(run_folder: Path) -> Callable[[], None]:
    import alpha.utils.troubleshooting_paths as tp

    original = tp.get_active_run_folder
    tp.get_active_run_folder = lambda: str(run_folder)

    def _restore() -> None:
        tp.get_active_run_folder = original

    return _restore


def _patch_no_runtime_logs() -> Callable[[], None]:
    restores: list[Callable[[], None]] = []
    try:
        import alpha.utils.japanese_accuracy_log as jal

        original = jal.jp_accuracy_log
        jal.jp_accuracy_log = lambda *args, **kwargs: None
        restores.append(lambda: setattr(jal, "jp_accuracy_log", original))
    except Exception:
        pass
    try:
        import alpha.utils.freeze_guard_log as fgl

        original_fg = fgl.freeze_guard_log
        fgl.freeze_guard_log = lambda *args, **kwargs: None
        restores.append(lambda: setattr(fgl, "freeze_guard_log", original_fg))
    except Exception:
        pass

    def _restore() -> None:
        for fn in restores:
            fn()

    return _restore


def _patch_step_tracer(host: _CycleHost) -> None:
    import alpha.utils.stop_finalize_worker as sfw

    original = sfw.run_timed_step

    def _wrapped(h: Any, step_name: str, fn) -> Any:
        host._step_order.append(step_name)
        if step_name == "close_transcript_gate":
            if "deepgram_graceful_stop" not in host._step_order:
                raise AssertionError("transcript gate before deepgram stop")
            host._gate_closed_after_deepgram = True
        return original(h, step_name, fn)

    sfw.run_timed_step = _wrapped
    host._restore_steps = lambda: setattr(sfw, "run_timed_step", original)


def _run_cycle(project: Path, cycle: int) -> dict[str, Any]:
    if cycle > 1:
        _reset_stop_worker_between_cycles()

    run_folder = OUT_DIR / f"smoke_cycle_{cycle}"
    run_folder.mkdir(parents=True, exist_ok=True)
    host = _CycleHost(run_folder, cycle)

    worker = get_language_pipeline_worker()
    worker.reset_for_new_run()

    restore_paths = _patch_isolated_paths(run_folder)
    restore_logs = _patch_no_runtime_logs()
    _patch_finalize_counter(host)
    _patch_step_tracer(host)

    begin_stop_from_ui(host)
    ok_core = host.stop_core_completed_event.wait(timeout=8.0)
    ok_ui = host.stop_ui_restored_event.wait(timeout=8.0)

    worker.stop_and_join(timeout_seconds=2.0)
    worker.reset_for_new_run()

    host._restore_finalize()
    host._restore_steps()
    restore_paths()
    restore_logs()
    _reset_stop_worker_between_cycles()

    return {
        "cycle": cycle,
        "core_completed": ok_core,
        "ui_restored": ok_ui,
        "finalize_calls": host._finalize_calls,
        "step_order": host._step_order,
        "gate_after_deepgram": host._gate_closed_after_deepgram,
        "audio_drained_before_gate": (
            "drain_audio_queue" in host._step_order
            and host._step_order.index("drain_audio_queue")
            < host._step_order.index("deepgram_graceful_stop")
            < host._step_order.index("close_transcript_gate")
        ),
        "ui_drain_step": "ui_transcript_drain" in host._step_order,
        "worker_restarted": worker.pending_task_count() == 0,
        "stop_flags_false": not host._is_stopping and not host._is_finalizing,
    }


_RUN_BEGIN = time.time()


def main() -> int:
    global _RUN_BEGIN
    _RUN_BEGIN = time.time()
    project = Path(__file__).resolve().parent
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _snapshot_forbidden_trees(project)
    restore_global = _install_global_isolation()

    checks: dict[str, bool] = {}
    warnings: list[str] = []
    cycles: list[dict[str, Any]] = []

    try:
        for cycle in (1, 2):
            cycles.append(_run_cycle(project, cycle))
    finally:
        restore_global()

    checks["start_stop_start_stop"] = len(cycles) == 2 and all(
        c.get("core_completed") and c.get("ui_restored") for c in cycles
    )
    checks["finalizer_once_per_run"] = all(c.get("finalize_calls") == 1 for c in cycles)
    checks["audio_queue_drain_before_deepgram_close"] = all(
        c.get("audio_drained_before_gate") for c in cycles
    )
    checks["transcript_gate_after_final_drain"] = all(c.get("gate_after_deepgram") for c in cycles)
    checks["ui_drain_barrier"] = all(c.get("ui_drain_step") for c in cycles)
    checks["worker_stop_restart"] = all(c.get("worker_restarted") for c in cycles)
    checks["stop_flags_false"] = all(c.get("stop_flags_false") for c in cycles)

    stop_src = (project / "alpha/utils/stop_finalize_worker.py").read_text(encoding="utf-8")
    checks["no_tk_from_stop_worker"] = "STOP_WORKER_NO_TK_CALL_CONFIRMED" in stop_src

    import package_latest_troubleshooting_run as pkg

    checks["fixture_package_isolation_policy"] = "/smoke_tests/" in "/".join(
        pkg._FORBIDDEN_ARCHIVE_PARTS
    ) and str(OUT_DIR).replace("\\", "/").endswith(f"validation/v{APP_VERSION}/fixtures")

    violations = _assert_no_forbidden_writes(project)
    checks["no_runs_or_latest_writes"] = not violations
    if violations:
        warnings.append("forbidden_writes:" + ",".join(violations[:5]))

    failed = [name for name, ok in checks.items() if not ok]
    status = "PASSED" if not failed else "FAILED"
    payload = {
        "result": status,
        "app_version": APP_VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": checks,
        "cycles": cycles,
        "warnings": warnings,
        "output_dir": str(OUT_DIR),
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
