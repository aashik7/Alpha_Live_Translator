"""Validation for V3.3.5.5.8.5.12 run identity, artifact separation, and stop worker."""
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
    LONG_TEST_BASELINE_MODE,
)
from alpha.utils.diagnostic_test_log import get_log_file_path
from alpha.utils.freeze_guard_log import get_freeze_guard_log_path
from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_log_path
from alpha.utils.run_artifacts import (
    create_initial_run_artifacts_index,
    get_current_index_path,
    get_test_artifacts_root,
    reset_run_artifacts_session,
)
from alpha.utils.run_consistency import validate_run_consistency
from alpha.utils.run_identity import (
    RUN_TYPE_AUTOMATED_VALIDATION,
    init_automated_validation_run,
    reset_run_identity,
)
from alpha.utils.stop_finalize_worker import begin_stop_from_ui


def main() -> int:
    failures: list[str] = []

    if APP_VERSION != "3.3.5.5.8.5.12":
        failures.append("version")
    if APP_CODENAME != "Long Japanese Test Readiness & Accuracy Evidence Cleanup":
        failures.append("codename")
    if not LONG_TEST_BASELINE_MODE:
        failures.append("long_test_baseline_mode")
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
    if acc.name != "v3.3.5.5.8.5.12_japanese_accuracy.log":
        failures.append("accuracy_log_name")
    fg = get_freeze_guard_log_path()
    if fg.name != "v3.3.5.5.8.5.12_freeze_guard.log":
        failures.append("freeze_guard_log_name")
    diag = get_log_file_path()
    if diag.name != "v3.3.5.5.8.5.12_diagnostic_test.log":
        failures.append("diagnostic_log_name")

    reset_run_identity()
    reset_run_artifacts_session()
    identity = init_automated_validation_run(selected_language="ja")
    if identity.run_type != RUN_TYPE_AUTOMATED_VALIDATION:
        failures.append("run_type_not_automated_validation")

    host = MagicMock()
    host._listen_language = "ja"
    index_path = create_initial_run_artifacts_index(identity=identity, host=host)
    if index_path is None:
        failures.append("index_not_created")
    else:
        test_root = get_test_artifacts_root()
        if test_root.name not in str(index_path):
            failures.append("index_not_under_test_artifacts")
        text = index_path.read_text(encoding="utf-8")
        if "RUN_TYPE=automated_validation" not in text:
            failures.append("index_missing_run_type")
        if "MagicMock" in text:
            failures.append("magicmock_in_test_index")
        if "run_id=" not in text:
            failures.append("index_missing_run_id")

    consistency = validate_run_consistency(identity=identity, host=host)
    if consistency.get("passed") is not True:
        failures.append(f"consistency_failed:{consistency.get('blocking_reasons')}")

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

    time.sleep(0.5)
    if not fg.exists():
        failures.append("freeze_guard_log_missing")
    else:
        text = fg.read_text(encoding="utf-8")
        for event in (
            "RUN_ID_CREATED",
            "ARTIFACT_ROOT_SELECTED",
            "RUN_ARTIFACTS_INDEX_CREATED",
            "STOP_BUTTON_CLICKED",
            "STOP_UI_CALLBACK_RETURNED",
            "STOP_FINALIZE_WORKER_STARTED",
        ):
            if event not in text:
                failures.append(f"missing_{event}")

    live_polluted = list((ROOT / "run_artifacts").glob("v3.3.5.5.8.5.12-*automated*"))
    if live_polluted:
        failures.append("automated_artifacts_in_run_artifacts")

    if failures:
        print("VALIDATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("VALIDATION PASSED: V3.3.5.5.8.5.12")
    print(f"  run_type={identity.run_type}")
    print(f"  run_id={identity.run_id}")
    print(f"  test_artifacts_index={get_current_index_path()}")
    print(f"  stop_ui_callback_duration_ms={duration_ms:.2f}")
    print(f"  consistency_passed={consistency.get('passed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
