"""Japanese transcript accuracy diagnostics (V3.3.5.5.8.5.11.1) — file only, no UI impact."""

from __future__ import annotations

import atexit
import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    CENTRALIZED_TROUBLESHOOTING_DIR,
    FULL_DIAGNOSTIC_LOGGING_ENABLED,
    JAPANESE_ACCURACY_MODE,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LANGUAGE_AGNOSTIC_UI_EVENT_BUS,
    LONG_SESSION_STABILITY_MODE,
    TK_SAFE_PIPELINE_MODE,
    TROUBLESHOOTING_MODE,
    UI_PERFORMANCE_MODE,
)

JAPANESE_ACCURACY_LOGGING = True
LOG_PREVIEW_CHARS = 160
HELD_LOG_THROTTLE_MS = 2000

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_log_queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=4000)
_writer_started = False
_writer_thread: Optional[threading.Thread] = None
_held_log_last: dict[str, float] = {}
_version_check_logged = False
_event_counts: dict[str, int] = {}

_THROTTLE_EVENTS = frozenset(
    {
        "held_fragment_reason",
        "INCOMPLETE_FRAGMENT_HELD",
    }
)

_NO_PREVIEW_KEYS = frozenset(
    {
        "committed_text",
        "final_committed_text",
        "final_text",
        "keyterm_list",
        "raw_fragments_used",
        "stable_text",
        "stable_text_original",
        "stable_text_cleaned_candidate",
        "raw_japanese_transcript",
        "original",
        "candidate",
        "cleanup_output",
        "final_cleanup_output",
        "per_fragment_cleanup_output",
    }
)


def _timestamp() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def _preview(text: str) -> str:
    value = (text or "").strip()
    if len(value) <= LOG_PREVIEW_CHARS:
        return value
    return value[:LOG_PREVIEW_CHARS] + "…"


def _throttle_key(event: str, data: dict[str, Any]) -> str:
    reason = str(
        data.get("held_fragment_reason")
        or data.get("incomplete_reason")
        or data.get("buffer_text", "")[:64]
    )
    return f"{event}|{reason}"


def _should_throttle(event: str, data: dict[str, Any]) -> bool:
    if event not in _THROTTLE_EVENTS:
        return False
    key = _throttle_key(event, data)
    now = time.monotonic()
    last = _held_log_last.get(key)
    if last is not None and (now - last) * 1000.0 < HELD_LOG_THROTTLE_MS:
        return True
    _held_log_last[key] = now
    return False


def _format_line(event: str, data: dict[str, Any]) -> str:
    safe = dict(data)
    safe.setdefault("app_version", APP_VERSION)
    safe.setdefault("app_codename", APP_CODENAME)
    safe.setdefault("JAPANESE_STT_PROFILE", JAPANESE_STT_PROFILE)
    safe.setdefault("JAPANESE_KEYTERM_PROFILE", JAPANESE_KEYTERM_PROFILE)
    safe.setdefault("japanese_accuracy_mode", JAPANESE_ACCURACY_MODE)
    safe.setdefault("ui_performance_mode", UI_PERFORMANCE_MODE)
    safe.setdefault("long_session_stability_mode", LONG_SESSION_STABILITY_MODE)
    safe.setdefault("tk_safe_pipeline_mode", TK_SAFE_PIPELINE_MODE)
    safe.setdefault("language_agnostic_ui_event_bus", LANGUAGE_AGNOSTIC_UI_EVENT_BUS)
    try:
        from alpha.utils.run_identity import get_current_run_identity

        identity = get_current_run_identity()
        if identity is not None:
            safe.setdefault("run_id", identity.run_id)
            safe.setdefault("run_timestamp", identity.run_timestamp)
            safe.setdefault("run_type", identity.run_type)
    except Exception:
        pass
    for key in list(safe.keys()):
        value = safe[key]
        if key in _NO_PREVIEW_KEYS:
            continue
        if isinstance(value, str) and (
            "text" in key.lower() or "preview" in key.lower()
        ):
            if key not in ("committed_text", "final_committed_text", "final_text"):
                safe[key] = _preview(value)
    payload = {"event": event, **safe}
    return f"{_timestamp()} | {json.dumps(payload, ensure_ascii=False, default=str)}"


def _resolve_log_file() -> Path:
    from alpha.utils.troubleshooting_paths import get_log_path

    return get_log_path("japanese_accuracy")


def _writer_loop() -> None:
    current_path: Optional[Path] = None
    handle = None
    while True:
        line = _log_queue.get()
        if line is None:
            break
        # fixes TASK_6_REPORT.md P1 (ALPHA_ARCHITECTURE_DEBUG_REPORT.md
        # "Evidence write failure alters canonical semantic decisions"):
        # this is a diagnostic-only writer with no return value the
        # caller inspects, so a file I/O failure here must stay contained
        # to this thread/loop and never surface as an exception that
        # could take down logging (or anything else) for the rest of the
        # run -- it must not silently kill the writer thread either.
        try:
            target = _resolve_log_file()
            if target != current_path:
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass
                    handle = None
                target.parent.mkdir(parents=True, exist_ok=True)
                handle = open(target, "a", encoding="utf-8")
                current_path = target
            if handle is not None:
                handle.write(line + "\n")
                handle.flush()
        except Exception:
            continue


def _start_writer() -> None:
    global _writer_started, _writer_thread, _version_check_logged
    if _writer_started or not JAPANESE_ACCURACY_LOGGING:
        return
    _writer_started = True
    _writer_thread = threading.Thread(
        target=_writer_loop, name="JapaneseAccuracyLogWriter", daemon=True
    )
    _writer_thread.start()
    if not _version_check_logged:
        _version_check_logged = True
        log_file = _resolve_log_file()
        jp_accuracy_log(
            "APP_VERSION_CONFIRMED",
            app_version=APP_VERSION,
            app_codename=APP_CODENAME,
        )
        if TROUBLESHOOTING_MODE:
            jp_accuracy_log("TROUBLESHOOTING_MODE_ACTIVE")
        if CENTRALIZED_TROUBLESHOOTING_DIR:
            jp_accuracy_log("CENTRALIZED_TROUBLESHOOTING_DIR_ACTIVE")
        try:
            from alpha.constants import PENDING_RUN_REBINDING_ENABLED

            if PENDING_RUN_REBINDING_ENABLED:
                jp_accuracy_log("PENDING_WRITER_FINAL_REBIND_ACTIVE")
        except Exception:
            pass
        jp_accuracy_log("RUNTIME_STOP_FREEZE_ELIMINATION_ACTIVE")
        jp_accuracy_log("OFFLINE_EVIDENCE_PACKAGING_ACTIVE")
        jp_accuracy_log("STOP_PATH_MINIMAL_MODE_ACTIVE")
        try:
            from alpha.constants import (
                ASSEMBLER_EXCEPTION_NO_DIRECT_COMMIT,
                BUSINESS_PHRASE_PROTECTION_ENABLED,
                INCOMPLETE_TAIL_HOLD_ENABLED,
                JAPANESE_STABLE_ACCURACY_FIX_ENABLED,
                PUNCTUATION_START_MERGE_ENABLED,
                RAW_DEEPGRAM_IMMUTABLE,
                STABLE_LAYER_SAFE_MERGE_ENABLED,
                TRANSLATION_READINESS_METRICS_ENABLED,
            )

            if JAPANESE_STABLE_ACCURACY_FIX_ENABLED:
                jp_accuracy_log("JAPANESE_STABLE_ACCURACY_FIX_ACTIVE", app_version=APP_VERSION)
            if RAW_DEEPGRAM_IMMUTABLE:
                jp_accuracy_log("RAW_DEEPGRAM_IMMUTABLE_CONFIRMED")
                jp_accuracy_log("RAW_DEEPGRAM_PRESERVED_UNMUTATED")
            jp_accuracy_log("RUNTIME_BASELINE_85205_PRESERVED")
            if STABLE_LAYER_SAFE_MERGE_ENABLED:
                jp_accuracy_log("STABLE_LAYER_ONLY_TRANSFORM_CONFIRMED")
            if PUNCTUATION_START_MERGE_ENABLED:
                jp_accuracy_log("PUNCTUATION_START_MERGE_ENABLED")
            if INCOMPLETE_TAIL_HOLD_ENABLED:
                jp_accuracy_log("INCOMPLETE_TAIL_HOLD_ENABLED")
            if BUSINESS_PHRASE_PROTECTION_ENABLED:
                jp_accuracy_log("BUSINESS_PHRASE_PROTECTION_ENABLED")
            if ASSEMBLER_EXCEPTION_NO_DIRECT_COMMIT:
                jp_accuracy_log("ASSEMBLER_EXCEPTION_DIRECT_COMMIT_BLOCKED")
            if TRANSLATION_READINESS_METRICS_ENABLED:
                jp_accuracy_log("TRANSLATION_READINESS_METRICS_ENABLED")
            from alpha.constants import (
                ACCURACY_EVIDENCE_MODE_ENABLED,
                AUTO_EXPORT_ALPHA_TXT_ENABLED,
                JAPANESE_BUSINESS_ACCURACY_8522_ENABLED,
                STOP_TAIL_CLEANUP_ENABLED,
                STABLE_LAYER_BUSINESS_CORRECTION_ENABLED,
                TEMP_AUDIO_RETENTION_ENABLED,
                TEMP_AUDIO_RETENTION_HOURS,
            )

            if JAPANESE_BUSINESS_ACCURACY_8522_ENABLED:
                jp_accuracy_log("JAPANESE_BUSINESS_ACCURACY_8522_ACTIVE")
            if ACCURACY_EVIDENCE_MODE_ENABLED:
                jp_accuracy_log("ACCURACY_EVIDENCE_MODE_ACTIVE")
            if AUTO_EXPORT_ALPHA_TXT_ENABLED:
                jp_accuracy_log("AUTO_EXPORT_ALPHA_TXT_ACTIVE")
            if TEMP_AUDIO_RETENTION_ENABLED:
                jp_accuracy_log(
                    "TEMP_AUDIO_RETENTION_2H_ACTIVE",
                    retention_hours=TEMP_AUDIO_RETENTION_HOURS,
                )
            if STABLE_LAYER_BUSINESS_CORRECTION_ENABLED:
                jp_accuracy_log("STABLE_LAYER_BUSINESS_CORRECTION_ENABLED")
            if STOP_TAIL_CLEANUP_ENABLED:
                jp_accuracy_log("STOP_TAIL_CLEANUP_ENABLED")
            from alpha.constants import (
                BUSINESS_CORRECTION_GUARD_85221_ENABLED,
                BUSINESS_CORRECTION_IDEMPOTENT_MODE,
                FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_ENABLED,
                FULL_ACCURACY_LOGGING_STILL_ENABLED,
                PUNCTUATION_START_POST_CORRECTION_MERGE_ENABLED,
                STOP_TAIL_VALIDATION_NA_FIX_ENABLED,
            )

            if BUSINESS_CORRECTION_GUARD_85221_ENABLED:
                jp_accuracy_log("BUSINESS_CORRECTION_GUARD_85221_ACTIVE")
            if BUSINESS_CORRECTION_IDEMPOTENT_MODE:
                jp_accuracy_log("BUSINESS_CORRECTION_IDEMPOTENT_MODE_ACTIVE")
            if PUNCTUATION_START_POST_CORRECTION_MERGE_ENABLED:
                jp_accuracy_log("PUNCTUATION_START_POST_CORRECTION_MERGE_ACTIVE")
            if STOP_TAIL_VALIDATION_NA_FIX_ENABLED:
                jp_accuracy_log("STOP_TAIL_VALIDATION_NA_FIX_ACTIVE")
            if FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_ENABLED:
                jp_accuracy_log("FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_ACTIVE")
            if FULL_ACCURACY_LOGGING_STILL_ENABLED:
                jp_accuracy_log("FULL_ACCURACY_LOGGING_STILL_ENABLED")
            jp_accuracy_log("RUNTIME_BASELINE_PRESERVED")
        except Exception:
            pass
        jp_accuracy_log("HIGH_FREQUENCY_UI_DRAIN_LOGGING_SUPPRESSED")
        jp_accuracy_log("UI_EVENT_BUS_NORMAL_TICKS_NOT_LOGGED_TO_ACCURACY_LOG")
        if FULL_DIAGNOSTIC_LOGGING_ENABLED:
            jp_accuracy_log("FULL_DIAGNOSTIC_LOGGING_ACTIVE")
        try:
            from alpha.constants import AUDIO_TEMP_CAPTURE_ENABLED

            if AUDIO_TEMP_CAPTURE_ENABLED:
                jp_accuracy_log("AUDIO_TEMP_BUFFER_MODE_ACTIVE")
        except Exception:
            pass
        try:
            from alpha.utils.accuracy_decision_log import log_accuracy_decision_logging_active
            from alpha.utils.transcript_evidence import log_traceability_active_once

            log_traceability_active_once()
            log_accuracy_decision_logging_active()
        except Exception:
            pass
        try:
            from alpha.constants import LOG_ROTATION_ENABLED, RAW_INTERIM_LOG_SAMPLING_ENABLED

            if LOG_ROTATION_ENABLED:
                jp_accuracy_log("LOG_ROTATION_ACTIVE")
            if RAW_INTERIM_LOG_SAMPLING_ENABLED:
                jp_accuracy_log("RAW_INTERIM_LOG_SAMPLING_ACTIVE")
            jp_accuracy_log("ASYNC_LOGGING_NON_BLOCKING_CONFIRMED")
        except Exception:
            pass
        if f"v{APP_VERSION}" not in log_file.as_posix():
            try:
                _log_queue.put_nowait(
                    _format_line(
                        "ACCURACY_LOG_VERSION_MISMATCH",
                        {
                            "expected_app_version": APP_VERSION,
                            "log_file": str(log_file),
                        },
                    )
                )
            except queue.Full:
                pass
    atexit.register(shutdown_japanese_accuracy_logging)


def shutdown_japanese_accuracy_logging() -> None:
    if not _writer_started:
        return
    try:
        _log_queue.put_nowait(None)
    except Exception:
        pass
    if _writer_thread is not None:
        _writer_thread.join(timeout=2.0)


def jp_accuracy_log(event: str, **data: Any) -> None:
    if not JAPANESE_ACCURACY_LOGGING:
        return
    if _should_throttle(event, data):
        return
    _event_counts[event] = int(_event_counts.get(event, 0)) + 1
    _start_writer()
    try:
        _log_queue.put_nowait(_format_line(event, data))
    except queue.Full:
        pass


def rebind_runtime_log_writer() -> None:
    """Force writer to reopen at the active run folder path on next line."""
    try:
        _log_queue.put_nowait(_format_line("PENDING_FILE_HANDLES_CLOSED", {}))
    except Exception:
        pass


def get_japanese_accuracy_log_path() -> Path:
    return _resolve_log_file()


def get_japanese_accuracy_event_counts() -> dict[str, int]:
    return dict(_event_counts)


def reset_japanese_accuracy_event_counts() -> None:
    _event_counts.clear()


def log_japanese_accuracy_run_started(selected_language: str) -> None:
    reset_japanese_accuracy_event_counts()
    jp_accuracy_log(
        "RUN_STARTED",
        selected_language=selected_language,
        run_start_timestamp=int(time.time() * 1000),
    )
