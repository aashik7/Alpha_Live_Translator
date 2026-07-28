"""Strict per-run identity for live vs test/mock artifact separation."""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    RUN_ID_MISMATCH_AUTO_REPAIR_ENABLED,
    SINGLE_RUN_IDENTITY_LOCK_ENABLED,
)

RUN_TYPE_LIVE = "live"
RUN_TYPE_SMOKE_TEST = "smoke_test"
RUN_TYPE_AUTOMATED_VALIDATION = "automated_validation"
RUN_TYPE_MOCK_TEST = "mock_test"

_VALID_RUN_TYPES = frozenset(
    {
        RUN_TYPE_LIVE,
        RUN_TYPE_SMOKE_TEST,
        RUN_TYPE_AUTOMATED_VALIDATION,
        RUN_TYPE_MOCK_TEST,
    }
)

_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Za-z0-9]+)?$")

_current: Optional["RunIdentity"] = None
_identity_lock = threading.Lock()


@dataclass
class RunIdentity:
    run_id: str
    run_timestamp: str
    run_type: str
    app_version: str
    app_codename: str
    selected_language: str
    index_created: bool = False
    index_updated: bool = False
    stop_ui_callback_duration_ms: float = 0.0
    stop_finalize_completed: bool = False
    deepgram_close_status: str = "pending"
    deepgram_graceful_stop_duration_ms: float = 0.0
    run_folder: str = ""
    created_at: str = ""
    identity_locked: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _is_real_language(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or "MagicMock" in text or "<" in text:
        return False
    return bool(_LANG_RE.match(text))


def sanitize_selected_language(value: Any, *, default: str = "ja") -> tuple[str, bool]:
    """Return (language, is_real)."""
    if _is_real_language(value):
        return str(value).strip(), True
    return default, False


def infer_run_type_from_language(
    selected_language: Any,
    *,
    requested_run_type: str = RUN_TYPE_LIVE,
) -> str:
    """Downgrade to mock_test if language is not a real string."""
    _, is_real = sanitize_selected_language(selected_language)
    if not is_real and requested_run_type == RUN_TYPE_LIVE:
        return RUN_TYPE_MOCK_TEST
    if requested_run_type not in _VALID_RUN_TYPES:
        return RUN_TYPE_LIVE
    return requested_run_type


def create_run_identity_once(
    *,
    run_type: str = RUN_TYPE_LIVE,
    selected_language: Any = "ja",
    host: Any = None,
) -> RunIdentity:
    global _current
    with _identity_lock:
        if SINGLE_RUN_IDENTITY_LOCK_ENABLED and _current is not None:
            try:
                from alpha.utils.async_debug_log import log_runtime_debug_event
                from alpha.utils.freeze_guard_log import freeze_guard_log
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("RUN_IDENTITY_REUSED_EXISTING", run_id=_current.run_id)
                jp_accuracy_log("RUN_IDENTITY_SECOND_CREATION_PREVENTED", run_id=_current.run_id)
                freeze_guard_log("RUN_IDENTITY_SECOND_CREATION_PREVENTED", run_id=_current.run_id)
                log_runtime_debug_event("RUN_IDENTITY_SECOND_CREATION_PREVENTED", run_id=_current.run_id)
            except Exception:
                pass
            return _current

    lang, is_real = sanitize_selected_language(
        selected_language or getattr(host, "_listen_language", None) or "ja"
    )
    effective_type = infer_run_type_from_language(lang, requested_run_type=run_type)
    if not is_real and effective_type == RUN_TYPE_LIVE:
        effective_type = RUN_TYPE_MOCK_TEST

    ts = time.strftime("%Y%m%d-%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    run_id = f"{effective_type}-v{APP_VERSION}-{ts}-{short_uuid}"

    identity = RunIdentity(
        run_id=run_id,
        run_timestamp=ts,
        run_type=effective_type,
        app_version=APP_VERSION,
        app_codename=APP_CODENAME,
        selected_language=lang,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        identity_locked=True,
    )

    try:
        from alpha.utils.troubleshooting_paths import (
            create_run_folder,
            write_latest_pointer,
        )

        run_folder = create_run_folder(
            app_version=APP_VERSION,
            run_timestamp=ts,
            run_type=effective_type,
            run_id=run_id,
            selected_language=lang,
        )
        identity.run_folder = str(run_folder)
        write_latest_pointer(run_id=run_id, run_folder=run_folder, status="in_progress")
        try:
            from alpha.utils.accuracy_stage_capture import reset_accuracy_stage_capture

            reset_accuracy_stage_capture(run_id, run_folder=run_folder)
        except Exception:
            pass
        try:
            from alpha.transcription.canonical_transcript_ledger import reset_for_run as reset_canonical_ledger

            reset_canonical_ledger(run_id)
        except Exception:
            pass
        try:
            from alpha.utils.live_runtime_metrics import reset_for_run as reset_live_metrics

            reset_live_metrics(run_id)
        except Exception:
            pass
    except Exception:
        pass
    with _identity_lock:
        _current = identity

    try:
        from alpha.utils.async_debug_log import log_runtime_debug_event
        from alpha.utils.freeze_guard_log import freeze_guard_log
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        payload = {
            "run_id": run_id,
            "run_timestamp": ts,
            "run_type": effective_type,
            "app_version": APP_VERSION,
            "app_codename": APP_CODENAME,
            "selected_language": lang,
        }
        jp_accuracy_log("RUN_IDENTITY_CREATED", **payload)
        jp_accuracy_log("RUN_IDENTITY_LOCKED", run_id=run_id, identity_locked=True)
        freeze_guard_log("RUN_IDENTITY_CREATED", **payload)
        freeze_guard_log("RUN_IDENTITY_LOCKED", run_id=run_id, identity_locked=True)
        log_runtime_debug_event("RUN_IDENTITY_CREATED", **payload)
        log_runtime_debug_event("RUN_IDENTITY_LOCKED", run_id=run_id, identity_locked=True)
    except Exception:
        pass

    return identity


def create_run_identity(
    *,
    run_type: str = RUN_TYPE_LIVE,
    selected_language: Any = "ja",
    host: Any = None,
) -> RunIdentity:
    return create_run_identity_once(
        run_type=run_type,
        selected_language=selected_language,
        host=host,
    )


def get_current_run_identity() -> Optional[RunIdentity]:
    return _current


def assert_run_identity_locked() -> bool:
    ident = get_current_run_identity()
    return bool(ident is not None and ident.identity_locked)


def get_run_id() -> str:
    ident = get_current_run_identity()
    return str(ident.run_id) if ident is not None else ""


def get_run_folder() -> str:
    ident = get_current_run_identity()
    return str(ident.run_folder) if ident is not None else ""


def validate_all_artifacts_use_same_run_id() -> dict[str, Any]:
    ident = get_current_run_identity()
    if ident is None:
        return {"ok": False, "reason": "no_identity"}
    from alpha.utils.run_artifacts import get_current_index_path
    from alpha.utils.troubleshooting_paths import get_run_manifest_path, get_artifact_path

    out = {"ok": True, "run_id": ident.run_id, "mismatches": []}
    index_path = get_current_index_path()
    manifest_path = get_run_manifest_path()
    live_path = get_artifact_path("live_run_status")
    for label, p in (("index", index_path), ("manifest", manifest_path), ("live", live_path)):
        try:
            if p is None or not Path(p).exists():
                continue
            txt = Path(p).read_text(encoding="utf-8", errors="ignore")
            if ident.run_id not in txt:
                out["ok"] = False
                out["mismatches"].append(label)
        except Exception:
            out["ok"] = False
            out["mismatches"].append(f"{label}_read_error")
    return out


def repair_artifact_run_id_if_safe() -> bool:
    if not RUN_ID_MISMATCH_AUTO_REPAIR_ENABLED:
        return False
    ident = get_current_run_identity()
    if ident is None:
        return False
    try:
        from alpha.utils.run_artifacts import get_current_index_path
        from alpha.utils.freeze_guard_log import freeze_guard_log
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        idx = get_current_index_path()
        if idx is None or not idx.exists():
            return False
        txt = idx.read_text(encoding="utf-8", errors="ignore")
        if f"run_id={ident.run_id}" in txt:
            return True
        lines = txt.splitlines()
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith("run_id="):
                lines[i] = f"run_id={ident.run_id}"
                replaced = True
        if replaced:
            idx.write_text("\n".join(lines) + "\n", encoding="utf-8")
            jp_accuracy_log("RUN_ID_MISMATCH_AUTO_REPAIRED", run_id=ident.run_id, index_path=str(idx))
            freeze_guard_log("RUN_ID_MISMATCH_AUTO_REPAIRED", run_id=ident.run_id, index_path=str(idx))
            return True
    except Exception:
        return False
    return False


def reset_run_identity() -> None:
    global _current
    with _identity_lock:
        _current = None


def init_automated_validation_run(*, selected_language: str = "ja") -> RunIdentity:
    return create_run_identity_once(
        run_type=RUN_TYPE_AUTOMATED_VALIDATION,
        selected_language=selected_language,
    )


def init_live_run_from_host(host: Any) -> RunIdentity:
    """Create exactly one live run identity/folder for a Start Listening session.

    Every new Listen Start must receive a fresh run folder and writable ledger.
    Prior completed/frozen sessions must never be reused (Start→Stop→Start).
    Previous session export files remain preserved under their own folders.
    """
    lang = getattr(host, "_listen_language", None)
    if not lang:
        raise ValueError(
            "Deepgram language not finalized before run identity creation "
            "(refusing silent ja/en fallback)"
        )
    current = get_current_run_identity()
    ledger_frozen = False
    try:
        from alpha.transcription.canonical_transcript_ledger import is_frozen

        ledger_frozen = bool(is_frozen())
    except Exception:
        ledger_frozen = False
    # Always rotate identity when a prior session exists. Relying only on
    # stop_finalize_completed left short Stops with a frozen ledger that the
    # next Start reused (canonical Stable commits = 0).
    if current is not None:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "RUN_IDENTITY_RESET_FOR_NEW_SESSION",
                previous_run_id=current.run_id,
                selected_language=str(lang),
                previous_stop_finalize_completed=bool(
                    getattr(current, "stop_finalize_completed", False)
                ),
                ledger_was_frozen=ledger_frozen,
            )
        except Exception:
            pass
        reset_run_identity()
        try:
            from alpha.utils.troubleshooting_paths import reset_troubleshooting_session

            reset_troubleshooting_session()
        except Exception:
            pass
    identity = create_run_identity_once(
        run_type=RUN_TYPE_LIVE, selected_language=lang, host=host
    )
    # Guarantee writable ledger even if create path was skipped/reused.
    try:
        from alpha.transcription.canonical_transcript_ledger import reset_for_run

        reset_for_run(identity.run_id)
    except Exception:
        pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "RUN_ID_CREATED",
            run_id=identity.run_id,
            selected_language=identity.selected_language,
            run_folder=identity.run_folder,
        )
        jp_accuracy_log(
            "RUN_FOLDER_BOUND",
            run_id=identity.run_id,
            run_folder=identity.run_folder,
        )
    except Exception:
        pass
    return identity
