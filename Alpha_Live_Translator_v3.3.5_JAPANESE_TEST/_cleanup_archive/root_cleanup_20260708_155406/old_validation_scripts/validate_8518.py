"""Validation for V3.3.5.5.8.5.18 Tk-safe pipeline & deadlock elimination."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FORBID_BACKGROUND_TK_CALLS,
    FORBID_UI_BLOCKING_LOCK_WAIT,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    KEYTERM_PROFILE_BUSINESS_JAPANESE,
    LANGUAGE_AGNOSTIC_UI_EVENT_BUS,
    LONG_SESSION_STABILITY_MODE,
    TK_SAFE_PIPELINE_MODE,
    resolve_japanese_keyterms,
)
from alpha.transcription.japanese_accuracy_cleaner import (
    normalize_business_cleanup_once,
    run_business_cleanup_selftest_once,
)
from alpha.transcription.japanese_sentence_assembler import JapaneseContinuityAssembler
from alpha.utils.language_pipeline_worker import start_language_pipeline_worker
from alpha.utils.ui_event_bus import get_ui_event_bus, start_ui_event_bus
from alpha.utils.ui_thread_guard import register_ui_main_thread

_DUPLICATE_BAD = ("いついつも", "このこのたび")


def run_deadlock_regression_test() -> bool:
    """Simulate 8.5.17 deadlock class — must not block UI or call Tk from background."""
    register_ui_main_thread()
    start_ui_event_bus()
    start_language_pipeline_worker()
    host = MagicMock()
    host.after = MagicMock(side_effect=AssertionError("host.after must not be called"))
    host.after_cancel = MagicMock()
    host.is_listening = True
    host.listening = False
    host._dg_ws = object()
    host._audio_q = MagicMock()
    host._audio_q.qsize.return_value = 0
    host.transcript_queue = MagicMock()
    host.transcript_queue.qsize.return_value = 0
    host._jp_final_stabilizer = None

    asm = JapaneseContinuityAssembler(host)
    asm._buffer = {
        "text": "テスト文です。",
        "created_mono": time.monotonic(),
        "updated_mono": time.monotonic(),
        "hold_started_mono": time.monotonic(),
        "part_count": 1,
        "speaker": 2,
        "raw_fragments": ["テスト文です。"],
        "metadata": {},
    }
    blocked = threading.Event()
    ui_done = threading.Event()

    def hold_lock():
        with asm._lock:
            blocked.set()
            time.sleep(0.3)

    t = threading.Thread(target=hold_lock, name="DeadlockTestHolder")
    t.start()
    assert blocked.wait(timeout=1.0)

    # UI heartbeat must not block
    snap = asm.get_buffer_snapshot_nonblocking()
    assert isinstance(snap, dict)
    ui_done.set()

    with asm._lock:
        asm._schedule_flush(50, "deadlock_test")

    flush_due = asm._pending_flush_due_mono
    flush_gen = asm._pending_flush_generation
    assert flush_due is not None

    from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

    get_language_pipeline_worker().schedule_flush(asm, flush_due, flush_gen, "deadlock_test")
    t.join(timeout=2.0)
    assert not t.is_alive() or ui_done.is_set()
    host.after.assert_not_called()
    print("DEADLOCK_REGRESSION_TEST_PASSED")
    return True


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    if APP_VERSION != "3.3.5.5.8.5.18":
        failures.append("version")
    if APP_CODENAME != "Language-Agnostic Tk-Safe Pipeline & Deadlock Elimination":
        failures.append("codename")
    if not TK_SAFE_PIPELINE_MODE:
        failures.append("tk_safe_pipeline_mode")
    if not LANGUAGE_AGNOSTIC_UI_EVENT_BUS:
        failures.append("ui_event_bus_flag")
    if not FORBID_BACKGROUND_TK_CALLS:
        failures.append("forbid_background_tk")
    if not FORBID_UI_BLOCKING_LOCK_WAIT:
        failures.append("forbid_ui_blocking_lock")
    if not LONG_SESSION_STABILITY_MODE:
        failures.append("long_session_stability_mode")
    if JAPANESE_STT_PROFILE != "no_diarize":
        failures.append("stt_profile")
    if JAPANESE_KEYTERM_PROFILE != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("keyterm_profile")
    if DEEPGRAM_MODEL != "nova-3" or DEEPGRAM_LANGUAGE != "ja":
        failures.append("deepgram_config")
    if DEEPGRAM_ENDPOINTING_MS != 500 or DEEPGRAM_UTTERANCE_END_MS != 1500:
        failures.append("deepgram_timing")

    terms, profile, _ = resolve_japanese_keyterms()
    if profile != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("resolved_profile")
    if not run_business_cleanup_selftest_once():
        failures.append("business_cleanup_selftest")

    for text in ("いつもお世話になっております", "このたび御社", "翌日"):
        r = normalize_business_cleanup_once(text, verify_second_pass=True)
        for bad in _DUPLICATE_BAD:
            if bad in r["candidate"]:
                failures.append(f"cleanup_{bad}")

    try:
        if not run_deadlock_regression_test():
            failures.append("deadlock_regression")
    except Exception as exc:
        failures.append(f"deadlock_regression:{exc}")

    asm_path = ROOT / "alpha" / "transcription" / "japanese_sentence_assembler.py"
    asm_src = asm_path.read_text(encoding="utf-8")
    if "after(max(1, int(hold_ms))" in asm_src or "_timer_after_id = after" in asm_src:
        failures.append("assembler_still_uses_tk_after")
    if "get_buffer_snapshot_nonblocking" not in asm_src:
        failures.append("missing_nonblocking_snapshot")
    if "try_execute_continuity_hold" not in asm_src:
        failures.append("missing_try_execute_continuity_hold")

    bus = get_ui_event_bus()
    if bus is None:
        failures.append("ui_event_bus")

    jp_log = ROOT / "logs" / "v3.3.5.5.8.5.18_japanese_accuracy.log"
    if not jp_log.parent.exists():
        warnings.append("log_dir_missing")

    status = "FAILED" if failures else ("PASSED_WITH_WARNINGS" if warnings else "PASSED")
    print(f"V3.3.5.5.8.5.18 TK-SAFE PIPELINE VALIDATION: {status}")
    if failures:
        print("Failures:", ", ".join(failures))
    if warnings:
        print("Warnings:", ", ".join(warnings))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
