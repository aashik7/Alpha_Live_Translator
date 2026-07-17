"""Validation for V3.3.5.5.8.5.11.1 stop non-blocking freeze guard."""
from __future__ import annotations

import sys
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
    JAPANESE_STT_PROFILE,
)
from alpha.utils.freeze_guard_log import get_freeze_guard_log_path
from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_log_path
from alpha.utils.stop_finalize_worker import begin_stop_from_ui


def main() -> int:
    failures: list[str] = []

    if APP_VERSION != "3.3.5.5.8.5.11.1":
        failures.append("version")
    if APP_CODENAME != "Stop Button Non-Blocking Finalization & Freeze Guard":
        failures.append("codename")
    if DEEPGRAM_MODEL != "nova-3":
        failures.append("model")
    if DEEPGRAM_LANGUAGE != "ja":
        failures.append("language")
    if JAPANESE_STT_PROFILE != "no_diarize":
        failures.append("stt_profile")
    if DEEPGRAM_ENDPOINTING_MS != 500:
        failures.append("endpointing")
    if DEEPGRAM_UTTERANCE_END_MS != 1500:
        failures.append("utterance_end_ms")

    acc = get_japanese_accuracy_log_path()
    if acc.name != "v3.3.5.5.8.5.11.1_japanese_accuracy.log":
        failures.append("accuracy_log_name")
    fg = get_freeze_guard_log_path()
    if fg.name != "v3.3.5.5.8.5.11.1_freeze_guard.log":
        failures.append("freeze_guard_log_name")

    host = MagicMock()
    host.is_listening = True
    host._is_finalizing = False
    host._stop_finalize_started = False
    host._stop_event = MagicMock()
    host._stop_event.set = MagicMock()
    host._set_stopping_ui_state = MagicMock()

    t0 = time.perf_counter()
    begin_stop_from_ui(host)
    duration_ms = (time.perf_counter() - t0) * 1000.0

    if duration_ms > 100.0:
        failures.append(f"ui_callback_too_slow_{duration_ms:.1f}ms")
    if not host._set_stopping_ui_state.called:
        failures.append("stopping_ui_state_not_set")

    time.sleep(0.3)
    if not fg.exists():
        failures.append("freeze_guard_log_missing")
    else:
        text = fg.read_text(encoding="utf-8")
        for event in (
            "STOP_BUTTON_CLICKED",
            "STOP_UI_CALLBACK_BEGIN",
            "STOP_UI_CALLBACK_RETURNED",
            "STOP_FINALIZE_WORKER_STARTED",
        ):
            if event not in text:
                failures.append(f"missing_{event}")

    if failures:
        print("VALIDATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("VALIDATION PASSED: V3.3.5.5.8.5.11.1")
    print(f"  stop_ui_callback_duration_ms={duration_ms:.2f}")
    print(f"  freeze_guard_log={fg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
