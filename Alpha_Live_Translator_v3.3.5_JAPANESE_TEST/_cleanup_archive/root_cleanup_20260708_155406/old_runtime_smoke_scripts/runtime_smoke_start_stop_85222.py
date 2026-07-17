"""Runtime smoke harness for business accuracy expansion 8.5.22.2."""

from __future__ import annotations

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


def main() -> int:
    from alpha.constants import (
        APP_VERSION,
        AUTO_EXPORT_ALPHA_TXT_ENABLED,
        BUSINESS_ACCURACY_EXPANSION_85222_ENABLED,
        EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED,
        RUNTIME_EVIDENCE_PACKAGE_DISABLED,
        STOP_PATH_MINIMAL_MODE,
        TEMP_AUDIO_RETENTION_HOURS,
    )
    from alpha.transcription.japanese_business_accuracy import (
        run_business_correction_guard_selftest,
        run_business_expansion_selftest,
    )
    from alpha.utils.stop_finalize_worker import begin_stop_from_ui

    if APP_VERSION != "3.3.5.5.8.5.22.2":
        print(f"FAILED wrong version {APP_VERSION}")
        return 1
    if not STOP_PATH_MINIMAL_MODE or not RUNTIME_EVIDENCE_PACKAGE_DISABLED:
        print("FAILED runtime baseline flags missing")
        return 1
    if not AUTO_EXPORT_ALPHA_TXT_ENABLED:
        print("FAILED alpha export disabled")
        return 1
    if TEMP_AUDIO_RETENTION_HOURS != 2:
        print(f"FAILED retention hours {TEMP_AUDIO_RETENTION_HOURS}")
        return 1
    if not BUSINESS_ACCURACY_EXPANSION_85222_ENABLED:
        print("FAILED expansion flag missing")
        return 1
    if not EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED:
        print("FAILED pointer finalization flag missing")
        return 1

    guard = run_business_correction_guard_selftest()
    expansion = run_business_expansion_selftest()
    if not guard.get("ok") or not expansion.get("ok"):
        print(f"FAILED selftests guard={guard} expansion={expansion}")
        return 1

    host = _DummyHost()
    t0 = time.monotonic()
    begin_stop_from_ui(host)
    ok_core = host.stop_core_completed_event.wait(timeout=5.0)
    ok_ui = host.stop_ui_restored_event.wait(timeout=5.0)
    elapsed = time.monotonic() - t0
    if not ok_core or not ok_ui or elapsed > 5.0:
        print(f"FAILED core={ok_core} ui={ok_ui} elapsed={elapsed:.2f}s")
        return 1

    alpha_txt = Path("troubleshooting/Alpha.txt")
    if not alpha_txt.exists():
        print("FAILED Alpha.txt not exported after stop smoke")
        return 1

    pointer_module = Path("alpha/utils/evidence_pointer_finalize.py").exists()
    print(
        f"PASSED elapsed={elapsed:.2f}s alpha_txt={alpha_txt.exists()} "
        f"pointer_module={pointer_module}"
    )
    print("NOTE: Full GUI Start/Stop requires manual verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
