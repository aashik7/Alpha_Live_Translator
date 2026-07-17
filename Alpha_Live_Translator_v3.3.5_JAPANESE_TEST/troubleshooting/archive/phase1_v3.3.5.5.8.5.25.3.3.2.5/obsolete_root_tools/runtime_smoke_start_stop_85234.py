"""Runtime smoke harness for business CER benchmark integrity 8.5.23.4."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path


class _DummyHost:
    def __init__(self) -> None:
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
        self._last_graceful_stop_result = {"finalized": True, "closed": True}

    def _ensure_graceful_stop_state(self): ...

    def stop_gracefully(self, timeout_seconds=2.0, stop_capture_fn=None):
        if callable(stop_capture_fn):
            stop_capture_fn()
        return {"timed_out": False, "finalized": True, "closed": True}

    def _close_wasapi_stream(self): ...

    def _close_microphone_stream(self): ...

    def _stop_health_monitor_ui_safe(self): ...

    def _clear_audio_pipeline_queues(self): ...

    def _run_on_ui_thread(self, fn):
        fn()

    def _finish_graceful_stop(self, timed_out=False):
        self.stop_ui_restored_event.set()


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    return [
        Path("troubleshooting/Alpha.txt"),
        Path("troubleshooting/latest_alpha_output.txt"),
        Path("troubleshooting/latest/latest_alpha_output.txt"),
        Path("troubleshooting/latest/latest_live_alpha_output.txt"),
    ]


def main() -> int:
    from alpha.constants import (
        ANTI_OVERFIT_MODE_ENABLED,
        APP_VERSION,
        AUTO_BUSINESS_CORRECTION_LEVEL,
        EVIDENCE_PROTECTION_85232_ENABLED,
        LATEST_ANALYZER_REPORT_SYNC_ENABLED,
        LESSON_SPECIFIC_CORRECTIONS_DISABLED,
        REAL_LIVE_ALPHA_PROTECTION_ENABLED,
        RUNTIME_EVIDENCE_PACKAGE_DISABLED,
        SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED,
        STOP_PATH_MINIMAL_MODE,
    )
    from alpha.transcription.japanese_business_accuracy import (
        run_business_correction_guard_selftest,
        run_minimal_correction_selftest,
    )
    from alpha.utils.alpha_output_protection import (
        RUN_TYPE_SMOKE_TEST,
        reset_alpha_export_run_type,
        set_alpha_export_run_type,
    )
    from alpha.utils.stop_finalize_worker import begin_stop_from_ui

    if APP_VERSION != "3.3.5.5.8.5.23.4":
        print(f"FAILED wrong version {APP_VERSION}")
        return 1
    if not all(
        (
            STOP_PATH_MINIMAL_MODE,
            RUNTIME_EVIDENCE_PACKAGE_DISABLED,
            EVIDENCE_PROTECTION_85232_ENABLED,
            REAL_LIVE_ALPHA_PROTECTION_ENABLED,
            SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED,
            ANTI_OVERFIT_MODE_ENABLED,
            AUTO_BUSINESS_CORRECTION_LEVEL == "minimal",
            LESSON_SPECIFIC_CORRECTIONS_DISABLED,
            LATEST_ANALYZER_REPORT_SYNC_ENABLED,
        )
    ):
        print("FAILED baseline flags missing")
        return 1

    guard = run_business_correction_guard_selftest()
    minimal = run_minimal_correction_selftest()
    if not guard.get("ok") or not minimal.get("ok"):
        print(f"FAILED selftests guard={guard} minimal={minimal}")
        return 1

    before = {str(p): _file_hash(p) for p in _protected_paths()}
    set_alpha_export_run_type(RUN_TYPE_SMOKE_TEST)
    host = _DummyHost()
    t0 = time.monotonic()
    begin_stop_from_ui(host)
    ok_core = host.stop_core_completed_event.wait(timeout=5.0)
    ok_ui = host.stop_ui_restored_event.wait(timeout=5.0)
    elapsed = time.monotonic() - t0
    reset_alpha_export_run_type()

    if not ok_core or not ok_ui or elapsed > 5.0:
        print(f"FAILED core={ok_core} ui={ok_ui} elapsed={elapsed:.2f}s")
        return 1

    after = {str(p): _file_hash(p) for p in _protected_paths()}
    unchanged = all(before.get(k) == after.get(k) for k in before)

    smoke_dirs = sorted(Path("troubleshooting/smoke_tests").glob("*"))
    smoke_written = bool(smoke_dirs) and (smoke_dirs[-1] / "Alpha_smoke_test.txt").exists()
    if not unchanged:
        print("FAILED latest live Alpha overwritten")
        return 1
    if not smoke_written:
        print("FAILED smoke output missing")
        return 1

    print(
        f"PASSED elapsed={elapsed:.2f}s smoke_written={smoke_written} "
        f"live_alpha_protected={unchanged} report_sync_not_blocking_stop=True"
    )
    print("NOTE: GUI runtime Start/Stop was not executed; user must verify manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
