"""Focused validation for V3.3.5.5.8.5.11."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    DEBUG_AFTER_LOOP_VERBOSE,
    DEBUG_UI_LOOP_VERBOSE,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    JAPANESE_ACCURACY_MODE,
    JAPANESE_STT_PROFILE,
    UI_PERFORMANCE_MODE,
)
from alpha.transcription.deepgram_client import DeepgramClientMixin
from alpha.transcription.japanese_accuracy_cleaner import (
    build_japanese_cleanup_candidate,
    detect_keyterm_overbias_candidates,
)
from alpha.utils.async_debug_log import (
    flush_async_debug_logging,
    get_async_debug_log_path,
    log_runtime_debug_event,
)
from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_log_path
from alpha.utils.diagnostic_test_log import get_log_file_path
from alpha.utils.run_artifacts import ensure_run_artifacts_folder, write_run_artifacts_index
from alpha.utils.runtime_evidence import (
    categorize_japanese_accuracy_issues,
    finalize_run_evidence,
    get_ui_performance_counters,
    mirror_runtime_event,
    reset_runtime_evidence_session,
)


def _assert(cond: bool, label: str, failures: list[str]) -> None:
    if not cond:
        failures.append(label)


def test_version_and_config(failures: list[str]) -> None:
    _assert(APP_VERSION == "3.3.5.5.8.5.11", "version", failures)
    _assert(
        APP_CODENAME == "Runtime Evidence Logging & Long Japanese Accuracy Finalizer",
        "codename",
        failures,
    )
    _assert(DEEPGRAM_MODEL == "nova-3", "model", failures)
    _assert(DEEPGRAM_LANGUAGE == "ja", "language", failures)
    _assert(JAPANESE_STT_PROFILE == "no_diarize", "stt_profile", failures)
    _assert(DEEPGRAM_ENDPOINTING_MS == 500, "endpointing", failures)
    _assert(DEEPGRAM_UTTERANCE_END_MS == 1500, "utterance_end_ms", failures)
    _assert(JAPANESE_ACCURACY_MODE is True, "japanese_accuracy_mode", failures)
    _assert(UI_PERFORMANCE_MODE is True, "ui_performance_mode", failures)
    _assert(DEBUG_UI_LOOP_VERBOSE is False, "debug_ui_loop_verbose", failures)
    _assert(DEBUG_AFTER_LOOP_VERBOSE is False, "debug_after_loop_verbose", failures)


def test_cleanup_rules(failures: list[str]) -> None:
    sonkei = build_japanese_cleanup_candidate("僕が尊敬してい人は誰々です")
    _assert(
        sonkei["applied_to_ui"] and "尊敬してる人" in sonkei["candidate"],
        "sonkei_teiru_fix",
        failures,
    )
    sara = build_japanese_cleanup_candidate("さらて普通に答える")
    _assert(
        sara["applied_to_ui"] and sara["candidate"] == "さらっと普通に答える",
        "sarasara_answer_fix",
        failures,
    )
    kantoku = build_japanese_cleanup_candidate("なんとか監督だからです")
    _assert(
        kantoku["candidate"] == "なんとか監督だからです",
        "kantoku_not_forced",
        failures,
    )
    _assert(len(detect_keyterm_overbias_candidates("なんとか監督だからです")) > 0, "kantoku_risky", failures)


def test_runtime_logging(failures: list[str]) -> None:
    reset_runtime_evidence_session()
    log_runtime_debug_event("RUN_STARTED", smoke=True)
    log_runtime_debug_event("FINAL_LIVE_SESSION_SUMMARY", smoke=True, EMERGENCY_COMMIT_count=0)
    ok = flush_async_debug_logging(timeout_ms=500.0)
    _assert(ok, "async_flush", failures)
    debug_path = get_async_debug_log_path()
    _assert(debug_path.exists(), "debug_log_exists", failures)
    text = debug_path.read_text(encoding="utf-8")
    _assert("STOP_LISTENING_DONE" in text or "RUN_STARTED" in text, "debug_runtime_events", failures)
    _assert("FINAL_LIVE_SESSION_SUMMARY" in text, "debug_final_summary", failures)


def test_run_artifacts(failures: list[str]) -> None:
    folder = ensure_run_artifacts_folder()
    index = write_run_artifacts_index(
        accuracy_log_path=str(get_japanese_accuracy_log_path()),
        debug_log_path=str(get_async_debug_log_path()),
        diagnostic_log_path=str(get_log_file_path()),
    )
    _assert(folder.exists(), "artifacts_folder", failures)
    _assert(index.exists(), "artifacts_index", failures)
    body = index.read_text(encoding="utf-8")
    _assert("japanese_accuracy_log=" in body, "index_accuracy_path", failures)
    _assert("debug_log=" in body, "index_debug_path", failures)


def test_deepgram_close_classification(failures: list[str]) -> None:
    class Host(DeepgramClientMixin):
        def __init__(self):
            import threading

            self._stop_event = threading.Event()
            self.is_listening = False
            self._is_stopping = True
            self._dg_stop_sending_audio = True
            self._dg_reconnect_lock = threading.Lock()
            self._dg_reconnecting = False

    host = Host()
    host._deepgram_on_error(None, "Connection closed")
    host._deepgram_on_close(None, 1000, "normal closure")
    ok = flush_async_debug_logging(timeout_ms=500.0)
    _assert(ok, "close_classify_flush", failures)
    text = get_async_debug_log_path().read_text(encoding="utf-8")
    _assert("DEEPGRAM_CLOSE_NORMAL" in text, "deepgram_close_normal", failures)
    _assert("DEEPGRAM_CLOSE_ERROR" not in text or text.count("DEEPGRAM_CLOSE_ERROR") == 0, "no_scary_close_on_stop", failures)


def test_issue_categorization(failures: list[str]) -> None:
    findings = categorize_japanese_accuracy_issues(
        raw_text="僕が尊敬してい人は誰々です",
        stable_text="僕が尊敬してい人は誰々です",
        candidate_text="僕が尊敬してる人は誰々です",
        cleanup_candidate={"applied_to_ui": True, "confidence": 0.96},
        readiness_reasons=["keyterm_overbias"],
    )
    cats = {f["category"] for f in findings}
    _assert("missing_word" in cats or "cleanup_high_confidence_applied" in cats, "issue_categories", failures)


def main() -> int:
    failures: list[str] = []
    test_version_and_config(failures)
    test_cleanup_rules(failures)
    test_runtime_logging(failures)
    test_run_artifacts(failures)
    test_deepgram_close_classification(failures)
    test_issue_categorization(failures)

    accuracy_log = get_japanese_accuracy_log_path()
    _assert(accuracy_log.name == "v3.3.5.5.8.5.11_japanese_accuracy.log", "accuracy_log_name", failures)
    _assert(accuracy_log.parent.name == "logs", "accuracy_log_dir", failures)
    debug_parent = get_async_debug_log_path().parent.name
    _assert(debug_parent == "debug", "debug_log_dir", failures)

    if failures:
        print("VALIDATION FAILED:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("VALIDATION PASSED: V3.3.5.5.8.5.11 automated checks")
    print(f"  accuracy_log={accuracy_log}")
    print(f"  debug_log={get_async_debug_log_path()}")
    print(f"  diagnostic_log={get_log_file_path()}")
    print(f"  artifacts_folder={ensure_run_artifacts_folder()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
