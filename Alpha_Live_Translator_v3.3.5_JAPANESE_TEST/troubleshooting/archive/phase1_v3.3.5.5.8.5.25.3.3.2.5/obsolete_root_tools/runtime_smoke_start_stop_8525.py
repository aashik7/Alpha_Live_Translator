"""Runtime smoke harness for Corporate IR Glossary 8.5.25."""

from __future__ import annotations

import hashlib
import json
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
        CLEAN_ALPHA_EXPORT_ENABLED,
        CORPORATE_IR_GLOSSARY_ENABLED,
        GLOSSARY_KEYTERM_BOOST_ENABLED,
        JAPANESE_BOUNDARY_STABILIZER_ENABLED,
        REAL_LIVE_ALPHA_PROTECTION_ENABLED,
        RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
        RUNTIME_EVIDENCE_PACKAGE_DISABLED,
        SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED,
        STABLE_GLOSSARY_CORRECTION_ENABLED,
        STOP_PATH_MINIMAL_MODE,
    )
    from alpha.transcription.corporate_ir_stable_corrector import apply_corporate_ir_stable_corrections
    from alpha.transcription.japanese_business_accuracy import (
        run_business_correction_guard_selftest,
        run_minimal_correction_selftest,
    )
    from alpha.utils.alpha_output_protection import (
        RUN_TYPE_SMOKE_TEST,
        reset_alpha_export_run_type,
        set_alpha_export_run_type,
        write_smoke_test_alpha_outputs,
    )
    from alpha.utils.stop_finalize_worker import begin_stop_from_ui

    if APP_VERSION != "3.3.5.5.8.5.25":
        print(f"FAILED wrong version {APP_VERSION}")
        return 1
    if not all(
        (
            STOP_PATH_MINIMAL_MODE,
            RUNTIME_EVIDENCE_PACKAGE_DISABLED,
            REAL_LIVE_ALPHA_PROTECTION_ENABLED,
            SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED,
            ANTI_OVERFIT_MODE_ENABLED,
            AUTO_BUSINESS_CORRECTION_LEVEL == "minimal_plus_user_glossary",
            CORPORATE_IR_GLOSSARY_ENABLED,
            GLOSSARY_KEYTERM_BOOST_ENABLED,
            STABLE_GLOSSARY_CORRECTION_ENABLED,
            CLEAN_ALPHA_EXPORT_ENABLED,
            RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
            JAPANESE_BOUNDARY_STABILIZER_ENABLED,
        )
    ):
        print("FAILED baseline flags missing")
        return 1

    guard = run_business_correction_guard_selftest()
    minimal = run_minimal_correction_selftest()
    if not guard.get("ok") or not minimal.get("ok"):
        print(f"FAILED selftests guard={guard} minimal={minimal}")
        return 1

    out = apply_corporate_ir_stable_corrections(["工程価格の既存円について"])
    if not out.get("lines"):
        print("FAILED glossary corrector returned empty")
        return 1

    live_pointer_before = _file_hash(Path("troubleshooting/latest/LATEST_LIVE_RUN_POINTER.json"))
    before = {str(p): _file_hash(p) for p in _protected_paths()}
    set_alpha_export_run_type(RUN_TYPE_SMOKE_TEST)
    write_smoke_test_alpha_outputs("smoke glossary test", run_type=RUN_TYPE_SMOKE_TEST)
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
    smoke_pointer = Path("troubleshooting/latest/LATEST_SMOKE_RUN_POINTER.json")
    smoke_dir = Path("troubleshooting/smoke_tests")
    smoke_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    smoke_out = smoke_dir / f"runtime_smoke_start_stop_8525_{ts}.txt"
    smoke_out.write_text(
        json.dumps(
            {
                "result": "PASSED",
                "elapsed_seconds": round(elapsed, 2),
                "live_alpha_protected": unchanged,
                "glossary_layer_ok": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if not unchanged:
        print("FAILED latest live Alpha overwritten")
        return 1
    if not smoke_pointer.exists():
        print("FAILED LATEST_SMOKE_RUN_POINTER not written")
        return 1

    print(f"PASSED elapsed={elapsed:.2f}s glossary_layer_not_blocking_stop=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
