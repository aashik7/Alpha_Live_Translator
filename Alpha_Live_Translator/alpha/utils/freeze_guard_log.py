"""Synchronous freeze-guard log — writes directly to disk, never blocks on UI thread."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    LANGUAGE_AGNOSTIC_UI_EVENT_BUS,
    LONG_SESSION_STABILITY_MODE,
    TK_SAFE_PIPELINE_MODE,
)


def _rotate_log_if_needed(path) -> bool:
    """Bound this writer's file, reusing the one rotation that already exists.

    Deferred import so this module keeps no import-time dependency on the
    evidence layer, and wrapped because a diagnostic writer must never raise
    into the path it is observing.
    """
    try:
        from alpha.utils.evidence_jsonl import rotate_if_needed

        return bool(rotate_if_needed(path))
    except Exception:
        return False


_ROTATION_CHECK_EVERY = 500
_rotation_check_counter = 0


def _should_rotate(path) -> bool:
    """Size check for a writer that holds one handle for the whole session.

    Sampled rather than run on every line. Measured at 12.9 us per call when it
    did the size check and its imports each time -- at a thousand lines a second
    that is ~1.3% of a core spent asking a question whose answer changes once
    per 50 MB. Checking every 500th line bounds the overshoot to a few hundred
    lines, which is nothing against the cap.
    """
    global _rotation_check_counter
    try:
        from alpha.constants import LOG_MAX_FILE_MB, LOG_ROTATION_ENABLED

        if not LOG_ROTATION_ENABLED or path is None:
            return False
        _rotation_check_counter += 1
        if _rotation_check_counter % _ROTATION_CHECK_EVERY:
            return False
        return path.exists() and path.stat().st_size >= LOG_MAX_FILE_MB * 1024 * 1024
    except Exception:
        return False


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_lock = threading.Lock()


def _resolve_log_file() -> Path:
    from alpha.utils.troubleshooting_paths import get_log_path

    return get_log_path("freeze_guard")


def get_freeze_guard_log_path() -> Path:
    return _resolve_log_file()


def _timestamp() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def freeze_guard_log(event: str, **data: Any) -> None:
    """Append one NDJSON line synchronously (safe from any thread)."""
    freeze_guard_log_sync(event, **data)


def freeze_guard_log_sync(event: str, **data: Any) -> None:
    payload = {
        "timestamp": _timestamp(),
        "event": event,
        "app_version": APP_VERSION,
        "app_codename": APP_CODENAME,
        "long_session_stability_mode": LONG_SESSION_STABILITY_MODE,
        "tk_safe_pipeline_mode": TK_SAFE_PIPELINE_MODE,
        "language_agnostic_ui_event_bus": LANGUAGE_AGNOSTIC_UI_EVENT_BUS,
        **data,
    }
    try:
        from alpha.utils.run_identity import get_current_run_identity

        identity = get_current_run_identity()
        if identity is not None:
            payload["run_id"] = identity.run_id
            payload["run_timestamp"] = identity.run_timestamp
            payload["run_type"] = identity.run_type
    except Exception:
        pass
    line = json.dumps(payload, ensure_ascii=True)
    try:
        log_file = _resolve_log_file()
        with _lock:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            _rotate_log_if_needed(log_file)
            with open(log_file, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        pass
