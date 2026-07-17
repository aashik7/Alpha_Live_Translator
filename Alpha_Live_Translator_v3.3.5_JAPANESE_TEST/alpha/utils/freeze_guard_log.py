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
            with open(log_file, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        pass
