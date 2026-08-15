"""Per-run artifact folders with live vs test separation and trustworthy index."""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    AUDIO_TEMP_INCLUDE_IN_UPLOAD_ZIP,
    CENTRALIZED_TROUBLESHOOTING_DIR,
    JAPANESE_ACCURACY_MODE,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LONG_RUN_EVIDENCE_PACKAGE_MODE,
    LONG_SESSION_STABILITY_MODE,
    TROUBLESHOOTING_MODE,
    UI_PERFORMANCE_MODE,
)
from alpha.utils.run_identity import (
    RUN_TYPE_LIVE,
    RunIdentity,
    get_current_run_identity,
    sanitize_selected_language,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LIVE_ROOT = _PROJECT_ROOT / "run_artifacts"  # legacy pointer only
_TEST_ROOT = _PROJECT_ROOT / "test_artifacts"
_current_folder: Optional[Path] = None
_index_path: Optional[Path] = None


def _artifact_subfolder() -> Path:
    """Return troubleshooting/runs/<run_id>/artifacts for current run."""
    from alpha.utils.troubleshooting_paths import get_current_run_folder

    run_folder = get_current_run_folder()
    if run_folder is not None:
        d = run_folder / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return _LIVE_ROOT / "_pending"


def _run_folder() -> Path:
    from alpha.utils.troubleshooting_paths import get_current_run_folder, get_runs_root

    folder = get_current_run_folder()
    if folder is not None:
        return folder
    pending = get_runs_root() / "_pending"
    pending.mkdir(parents=True, exist_ok=True)
    return pending


def get_run_artifacts_root() -> Path:
    if CENTRALIZED_TROUBLESHOOTING_DIR:
        from alpha.utils.troubleshooting_paths import get_runs_root

        return get_runs_root()
    return _LIVE_ROOT


def get_test_artifacts_root() -> Path:
    return _TEST_ROOT


def get_current_run_artifacts_folder() -> Optional[Path]:
    return _current_folder


def get_current_index_path() -> Optional[Path]:
    return _index_path


def reset_run_artifacts_session() -> None:
    global _current_folder, _index_path
    _current_folder = None
    _index_path = None


def _artifact_root_for_run_type(run_type: str) -> Path:
    if run_type == RUN_TYPE_LIVE:
        return _LIVE_ROOT
    return _TEST_ROOT


def _folder_name(identity: RunIdentity) -> str:
    if identity.run_type == RUN_TYPE_LIVE:
        return f"v{APP_VERSION}-{identity.run_timestamp}"
    return f"v{APP_VERSION}-{identity.run_type}-{identity.run_timestamp}"


def ensure_run_artifacts_folder(
    identity: Optional[RunIdentity] = None,
) -> Path:
    global _current_folder, _index_path
    identity = identity or get_current_run_identity()
    if identity is None:
        identity = _make_fallback_identity()

    if CENTRALIZED_TROUBLESHOOTING_DIR and TROUBLESHOOTING_MODE:
        from alpha.utils.troubleshooting_paths import (
            create_run_folder,
            get_current_run_folder,
            get_artifact_path,
        )

        folder = get_current_run_folder()
        if folder is None:
            folder = create_run_folder(
                app_version=APP_VERSION,
                run_timestamp=identity.run_timestamp,
                run_type=identity.run_type,
                run_id=identity.run_id,
                selected_language=identity.selected_language,
            )
        _current_folder = folder
        _index_path = get_artifact_path("run_artifacts_index")
        try:
            from alpha.utils.async_debug_log import log_runtime_debug_event
            from alpha.utils.freeze_guard_log import freeze_guard_log

            payload = {
                "run_type": identity.run_type,
                "artifact_root": str(folder),
                "artifact_folder": str(folder),
                "reason": "troubleshooting_centralized",
            }
            freeze_guard_log("ARTIFACT_ROOT_SELECTED", **payload)
            freeze_guard_log("LEGACY_LOG_PATH_REDIRECTED", target=str(folder))
            freeze_guard_log("NO_RUNTIME_LOGS_OUTSIDE_TROUBLESHOOTING_CONFIRMED")
            log_runtime_debug_event("ARTIFACT_ROOT_SELECTED", **payload)
        except Exception:
            pass
        return folder

    if _current_folder is not None and identity.run_type == RUN_TYPE_LIVE:
        return _current_folder
    if _current_folder is not None and identity.run_type != RUN_TYPE_LIVE:
        # Non-live runs always get their own folder.
        pass

    root = _artifact_root_for_run_type(identity.run_type)
    root.mkdir(parents=True, exist_ok=True)
    folder = root / _folder_name(identity)
    os.makedirs(folder, exist_ok=True)
    _current_folder = folder
    _index_path = folder / "RUN_ARTIFACTS_INDEX.txt"

    try:
        from alpha.utils.async_debug_log import log_runtime_debug_event
        from alpha.utils.freeze_guard_log import freeze_guard_log

        payload = {
            "run_type": identity.run_type,
            "artifact_root": str(root),
            "artifact_folder": str(folder),
            "reason": "live" if identity.run_type == RUN_TYPE_LIVE else "non_live_run",
        }
        freeze_guard_log("ARTIFACT_ROOT_SELECTED", **payload)
        log_runtime_debug_event("ARTIFACT_ROOT_SELECTED", **payload)
    except Exception:
        pass

    return folder


def _make_fallback_identity() -> RunIdentity:
    from alpha.utils.run_identity import create_run_identity_once

    return create_run_identity_once(run_type=RUN_TYPE_LIVE, selected_language="ja")


def _log_paths() -> dict[str, str]:
    paths: dict[str, str] = {}
    try:
        from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_log_path

        paths["japanese_accuracy_log"] = str(get_japanese_accuracy_log_path())
    except Exception:
        paths["japanese_accuracy_log"] = "(not available)"
    try:
        from alpha.utils.async_debug_log import get_async_debug_log_path

        paths["debug_log"] = str(get_async_debug_log_path())
    except Exception:
        paths["debug_log"] = "(not available)"
    try:
        from alpha.utils.diagnostic_test_log import get_log_file_path

        paths["diagnostic_log"] = str(get_log_file_path())
    except Exception:
        paths["diagnostic_log"] = "(not available)"
    try:
        from alpha.utils.freeze_guard_log import get_freeze_guard_log_path

        paths["freeze_guard_log"] = str(get_freeze_guard_log_path())
    except Exception:
        paths["freeze_guard_log"] = "(not available)"
    return paths


def _build_index_lines(
    identity: RunIdentity,
    *,
    status: str,
    host: Any = None,
    extra: Optional[dict[str, Any]] = None,
) -> list[str]:
    paths = _log_paths()
    lang, _ = sanitize_selected_language(identity.selected_language)
    lines = [
        f"status={status}",
        f"RUN_TYPE={identity.run_type}",
        f"run_id={identity.run_id}",
        f"run_timestamp={identity.run_timestamp}",
        f"app_version={APP_VERSION}",
        f"app_codename={APP_CODENAME}",
        f"selected_language={lang}",
        f"JAPANESE_STT_PROFILE={JAPANESE_STT_PROFILE}",
        f"JAPANESE_KEYTERM_PROFILE={JAPANESE_KEYTERM_PROFILE}",
        f"japanese_accuracy_mode={JAPANESE_ACCURACY_MODE}",
        f"ui_performance_mode={UI_PERFORMANCE_MODE}",
        f"japanese_accuracy_log={paths['japanese_accuracy_log']}",
        f"debug_log={paths['debug_log']}",
        f"diagnostic_log={paths['diagnostic_log']}",
        f"freeze_guard_log={paths['freeze_guard_log']}",
    ]
    folder = _current_folder or ensure_run_artifacts_folder(identity)
    lines.append(f"artifact_folder={folder}")

    exported = 0
    internal_stable = 0
    if host is not None:
        exported = int(getattr(host, "_exported_ui_segment_count", 0) or 0)
    lines.append(f"exported_ui_segment_count={exported}")
    lines.append(f"alpha_output_export={(extra or {}).get('alpha_output_export', '(not exported)')}")

    for key, value in (extra or {}).items():
        if key in ("alpha_output_export",):
            continue
        if isinstance(value, (dict, list)):
            lines.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
        else:
            lines.append(f"{key}={value}")

    return lines


def _write_index(lines: list[str], *, identity: RunIdentity) -> Path:
    folder = ensure_run_artifacts_folder(identity)
    if CENTRALIZED_TROUBLESHOOTING_DIR:
        from alpha.utils.troubleshooting_paths import get_artifact_path

        index_path = get_artifact_path("run_artifacts_index")
    else:
        index_path = folder / "RUN_ARTIFACTS_INDEX.txt"
    try:
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        os.makedirs(folder, exist_ok=True)
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    global _index_path
    _index_path = index_path
    return index_path


def create_initial_run_artifacts_index(
    *,
    identity: Optional[RunIdentity] = None,
    host: Any = None,
) -> Optional[Path]:
    identity = identity or get_current_run_identity()
    if identity is None:
        return None

    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log

        freeze_guard_log("RUN_ARTIFACTS_INDEX_CREATE_BEGIN", run_id=identity.run_id)
        index_path = _write_index(
            _build_index_lines(identity, status="started", host=host),
            identity=identity,
        )
        identity.index_created = True

        from alpha.utils.async_debug_log import log_runtime_debug_event

        log_runtime_debug_event(
            "RUN_ARTIFACTS_INDEX_CREATED",
            run_id=identity.run_id,
            run_type=identity.run_type,
            index_path=str(index_path),
            status="started",
        )
        freeze_guard_log(
            "RUN_ARTIFACTS_INDEX_CREATED",
            run_id=identity.run_id,
            index_path=str(index_path),
            status="started",
        )
        if identity.run_type == RUN_TYPE_LIVE and LONG_SESSION_STABILITY_MODE:
            try:
                from alpha.utils.troubleshooting_paths import get_artifact_path
                from alpha.utils.flight_recorder import (
                    record_flight_event,
                    start_flight_recorder,
                )

                start_flight_recorder(get_artifact_path("flight_recorder").parent)
                record_flight_event("run_created", host=host, force=True)
            except Exception:
                pass
        return index_path
    except Exception as exc:
        try:
            from alpha.utils.freeze_guard_log import freeze_guard_log

            freeze_guard_log(
                "RUN_ARTIFACTS_INDEX_FAILED",
                phase="create",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
        except Exception:
            pass
        return None


def update_run_artifacts_index_at_stop(
    *,
    identity: Optional[RunIdentity] = None,
    host: Any = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    identity = identity or get_current_run_identity()
    if identity is None:
        return None

    try:
        from alpha.utils.freeze_guard_log import freeze_guard_log

        freeze_guard_log("RUN_ARTIFACTS_INDEX_UPDATE_BEGIN", run_id=identity.run_id)

        warnings = list((extra or {}).get("warning_reasons") or [])
        status = "completed"
        if warnings or (extra or {}).get("timed_out_steps"):
            status = "completed_with_warnings"

        merged_extra = dict(extra or {})
        merged_extra.setdefault(
            "stop_finalize_completed",
            str(identity.stop_finalize_completed).lower(),
        )
        merged_extra.setdefault(
            "stop_ui_callback_duration_ms",
            identity.stop_ui_callback_duration_ms,
        )
        merged_extra.setdefault("deepgram_close_status", identity.deepgram_close_status)

        index_path = _write_index(
            _build_index_lines(
                identity,
                status=status,
                host=host,
                extra=merged_extra,
            ),
            identity=identity,
        )
        identity.index_updated = True

        if identity.run_type == RUN_TYPE_LIVE:
            write_latest_live_artifacts_pointer(
                identity=identity,
                index_path=index_path,
                host=host,
                extra={**merged_extra, "status": status},
            )

        from alpha.utils.async_debug_log import log_runtime_debug_event

        log_runtime_debug_event(
            "RUN_ARTIFACTS_INDEX_UPDATED",
            run_id=identity.run_id,
            run_type=identity.run_type,
            index_path=str(index_path),
            status=status,
        )
        freeze_guard_log(
            "RUN_ARTIFACTS_INDEX_UPDATED",
            run_id=identity.run_id,
            index_path=str(index_path),
            status=status,
        )
        return index_path
    except Exception as exc:
        try:
            from alpha.utils.freeze_guard_log import freeze_guard_log

            freeze_guard_log(
                "RUN_ARTIFACTS_INDEX_FAILED",
                phase="update",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
        except Exception:
            pass
        return None


def _project_root() -> Path:
    return _PROJECT_ROOT


def write_latest_live_artifacts_pointer(
    *,
    identity: RunIdentity,
    index_path: Path,
    host: Any = None,
    extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Update root pointer files so uploads never reference a stale live run."""
    if identity.run_type != RUN_TYPE_LIVE:
        return index_path

    paths = _log_paths()
    root = _PROJECT_ROOT
    latest_path = root / "LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt"
    upload_path = root / "UPLOAD_PACKAGE_INDEX.txt"
    warnings = list((extra or {}).get("warning_reasons") or [])
    blocking = list((extra or {}).get("blocking_reasons") or [])
    alpha_output = str(root / "Alpha output.txt")

    latest_lines = [
        f"status={(extra or {}).get('status', 'completed')}",
        f"latest_live_run_id={identity.run_id}",
        f"latest_live_run_timestamp={identity.run_timestamp}",
        f"latest_live_app_version={APP_VERSION}",
        f"latest_live_index_path={index_path}",
        f"RUN_TYPE={identity.run_type}",
        f"artifact_folder={index_path.parent}",
        f"alpha_output_path={alpha_output}",
        f"debug_log_path={paths['debug_log']}",
        f"japanese_accuracy_log_path={paths['japanese_accuracy_log']}",
        f"diagnostic_log_path={paths['diagnostic_log']}",
        f"freeze_guard_log_path={paths['freeze_guard_log']}",
        f"warning_reasons={json.dumps(warnings, ensure_ascii=False)}",
        f"blocking_reasons={json.dumps(blocking, ensure_ascii=False)}",
    ]
    latest_path.write_text("\n".join(latest_lines) + "\n", encoding="utf-8")

    upload_lines = [
        "# Upload package for latest live run",
        f"run_id={identity.run_id}",
        f"run_timestamp={identity.run_timestamp}",
        "1. Alpha output.txt",
        "2. Cursor final report.txt",
        f"3. logs/v{APP_VERSION}_japanese_accuracy.log",
        f"4. logs/v{APP_VERSION}_diagnostic_test.log",
        f"5. logs/v{APP_VERSION}_freeze_guard.log",
        f"6. {paths['debug_log']}",
        f"7. {index_path}",
        "8. LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt",
        "9. UPLOAD_PACKAGE_INDEX.txt",
    ]
    upload_path.write_text("\n".join(upload_lines) + "\n", encoding="utf-8")

    legacy_root_index = root / "RUN_ARTIFACTS_INDEX.txt"
    if legacy_root_index.exists():
        try:
            legacy_text = legacy_root_index.read_text(encoding="utf-8", errors="ignore")
            if identity.run_id not in legacy_text:
                legacy_root_index.write_text(
                    "# DEPRECATED — use LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt\n"
                    + latest_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
        except Exception:
            pass

    try:
        from alpha.utils.async_debug_log import log_runtime_debug_event
        from alpha.utils.freeze_guard_log import freeze_guard_log

        freeze_guard_log(
            "LATEST_LIVE_INDEX_POINTER_UPDATED",
            latest_live_index_path=str(latest_path),
            run_id=identity.run_id,
        )
        freeze_guard_log("UPLOAD_PACKAGE_HINT_WRITTEN", upload_package_path=str(upload_path))
        if legacy_root_index.exists():
            freeze_guard_log("ROOT_INDEX_STALE_PREVENTED", run_id=identity.run_id)
        log_runtime_debug_event(
            "LATEST_LIVE_INDEX_POINTER_UPDATED",
            run_id=identity.run_id,
            latest_live_index_path=str(latest_path),
        )
    except Exception:
        pass
    return latest_path


# Legacy shim — redirects to safe update for live runs only.
def write_run_artifacts_index(
    *,
    accuracy_log_path: str,
    debug_log_path: str,
    diagnostic_log_path: str,
    export_path: str = "",
    selected_language: str = "ja",
    host: Any = None,
) -> Path:
    identity = get_current_run_identity()
    if identity is None:
        from alpha.utils.run_identity import create_run_identity_once

        identity = create_run_identity_once(
            run_type=RUN_TYPE_LIVE,
            selected_language=selected_language,
            host=host,
        )
    extra = {
        "alpha_output_export": export_path or "(not exported)",
        "japanese_accuracy_log_override": accuracy_log_path,
        "debug_log_override": debug_log_path,
        "diagnostic_log_override": diagnostic_log_path,
    }
    path = update_run_artifacts_index_at_stop(identity=identity, host=host, extra=extra)
    if path is None:
        folder = ensure_run_artifacts_folder(identity)
        return folder / "RUN_ARTIFACTS_INDEX.txt"
    return path


def get_transcript_text_from_snapshot() -> str:
    from alpha.utils.transcript_snapshot_store import format_alpha_output_text

    return format_alpha_output_text(active_only=True)


def get_transcript_text_from_host(host: Any, *, allow_ui_export: bool = False) -> str:
    """Prefer snapshot store; UI export path is blocked unless explicitly allowed."""
    from alpha.utils.transcript_snapshot_store import format_alpha_output_text
    from alpha.utils.ui_thread_guard import guard_ui_thread_blocking_call, is_ui_main_thread

    snap_text = format_alpha_output_text(active_only=True)
    if snap_text.strip():
        return snap_text
    if is_ui_main_thread() and not allow_ui_export:
        guard_ui_thread_blocking_call("get_transcript_text_from_host")
        return ""
    if host is None:
        return ""
    if hasattr(host, "_get_clean_transcript_for_copy_export"):
        try:
            return str(host._get_clean_transcript_for_copy_export() or "")
        except Exception:
            pass
    store = getattr(host, "transcript_store", None)
    if store is None or not hasattr(store, "get_all"):
        return ""
    from alpha.utils.ui_speaker_label import format_ui_speaker_line

    lines = []
    for segment in store.get_all():
        text = (getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        lines.append(format_ui_speaker_line(text))
    return "\n".join(lines)


def write_partial_alpha_output_from_snapshot(
    *, reason: str = "autosave", host: Any = None
) -> Optional[Path]:
    identity = get_current_run_identity()
    if identity is None:
        return None
    folder = ensure_run_artifacts_folder(identity)
    from alpha.utils.troubleshooting_paths import get_transcript_path

    partial_path = get_transcript_path("alpha_output_partial")
    text = get_transcript_text_from_snapshot()
    if not text.strip():
        return None
    try:
        start = time.perf_counter()
        partial_path.write_text(text, encoding="utf-8")
        duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log
        from alpha.utils.session_progress import increment_counter, touch_progress

        increment_counter("partial_autosave_count")
        touch_progress("last_artifact_autosave")
        jp_accuracy_log(
            "PARTIAL_AUTOSAVE_WRITE_DURATION_MS",
            duration_ms=duration_ms,
            reason=reason,
        )
        if duration_ms > 1000:
            jp_accuracy_log("PARTIAL_AUTOSAVE_VERY_SLOW", duration_ms=duration_ms, reason=reason)
        elif duration_ms > 200:
            jp_accuracy_log("PARTIAL_AUTOSAVE_SLOW", duration_ms=duration_ms, reason=reason)
        jp_accuracy_log(
            "PARTIAL_ALPHA_OUTPUT_AUTOSAVED_BACKGROUND",
            reason=reason,
            path=str(partial_path),
            segment_lines=len([ln for ln in text.splitlines() if ln.strip()]),
            duration_ms=duration_ms,
        )
        freeze_guard_log_sync(
            "PARTIAL_ALPHA_OUTPUT_AUTOSAVED_BACKGROUND",
            reason=reason,
            path=str(partial_path),
        )
        freeze_guard_log_sync("ACTIVE_SESSION_PARTIAL_ARTIFACT_WRITTEN", reason=reason)
        if reason in ("crash", "ui_mainloop_stall_confirmed", "window_close"):
            freeze_guard_log_sync("PARTIAL_ALPHA_OUTPUT_WRITTEN_ON_CRASH", reason=reason)
        try:
            from alpha.utils.flight_recorder import record_flight_event

            record_flight_event("autosave_success", host=host, force=True, layer="alpha")
        except Exception:
            pass
        return partial_path
    except Exception:
        return None


def write_partial_alpha_output(host: Any, *, reason: str = "autosave") -> Optional[Path]:
    """Legacy entry — routes to snapshot background path."""
    return write_partial_alpha_output_from_snapshot(reason=reason, host=host)



def _committed_final_source_text(host: Any = None) -> tuple[str, str]:
    """Best committed source transcript for Final export (export metadata ok)."""
    from alpha.utils.ui_speaker_label import format_ui_speaker_line, strip_speaker_prefix

    candidates: list[tuple[str, str]] = []
    try:
        snap_text = get_transcript_text_from_snapshot()
        if snap_text.strip():
            candidates.append(("transcript_snapshot", snap_text))
    except Exception:
        pass
    if host is not None:
        try:
            host_text = get_transcript_text_from_host(host, allow_ui_export=True)
            if host_text.strip():
                candidates.append(("host_committed", host_text))
        except Exception:
            pass
        try:
            store = getattr(host, "transcript_store", None)
            if store is not None and hasattr(store, "get_all"):
                lines = []
                for segment in store.get_all():
                    body = strip_speaker_prefix(getattr(segment, "text", "") or "")
                    if body:
                        lines.append(format_ui_speaker_line(body))
                joined = "\n".join(lines)
                if joined.strip():
                    candidates.append(("transcript_store", joined))
        except Exception:
            pass
    identity = get_current_run_identity()
    if identity is not None:
        try:
            mirror = Path(identity.run_folder) / "transcripts" / "Alpha output.txt"
            if mirror.exists():
                mirror_text = mirror.read_text(encoding="utf-8")
                if mirror_text.strip():
                    candidates.append(("alpha_output_mirror", mirror_text))
        except Exception:
            pass
    if not candidates:
        return "", "none"
    # Prefer longest non-empty committed text.
    source, text = max(candidates, key=lambda item: len(item[1].strip()))
    if text and not text.endswith("\n"):
        text = text + "\n"
    return text, source


def write_final_alpha_output_from_snapshot(host: Any = None) -> Optional[Path]:
    """Write Final Alpha once via final_artifact_authority, then seal (V25.3.3.1)."""
    from alpha.constants import FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY
    from alpha.transcription.canonical_transcript_ledger import (
        get_frozen_snapshot,
        serialize_export_payload,
        validate_internal_consistency,
    )
    from alpha.utils.canonical_export_writer import (
        build_final_export_record_rows,
        transcript_sha256,
    )
    from alpha.utils.final_artifact_authority import (
        begin_final_export,
        seal_final_export,
        verify_final_export_seal,
        write_final_once,
    )
    from alpha.utils.path_types import ensure_path
    from alpha.utils.troubleshooting_paths import get_transcript_path

    identity = get_current_run_identity()
    if identity is None:
        return None

    run_folder = ensure_path(identity.run_folder)
    emergency_path = None
    if run_folder is not None:
        emergency_path = run_folder / "artifacts" / "EMERGENCY_UNVERIFIED_TRANSCRIPT.txt"
    else:
        emergency_path = ensure_run_artifacts_folder(identity) / "EMERGENCY_UNVERIFIED_TRANSCRIPT.txt"

    result_flags: dict[str, Any] = {
        "alpha_output_written": False,
        "final_export_records_written": False,
        "finalization_failed": False,
    }

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("STOP_FINALIZE_USED_TRANSCRIPT_SNAPSHOT")
        jp_accuracy_log("STOP_FINALIZE_AVOIDED_UI_EXPORT_PATH")
    except Exception:
        pass

    if not FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY:
        result_flags["finalization_failed"] = True
        return None

    snap = get_frozen_snapshot()
    text = ""
    rows: list = []
    snapshot_id = ""
    source_name = "frozen_ledger"
    unattached_gap_line = ""
    if snap:
        try:
            payload = serialize_export_payload(snap)
            text = str(payload.get("text") or "")
            # Item 72: a connection outage with no record to attach it to. Held
            # aside rather than merged into `text` here, because the
            # `if not text.strip()` fallback below is real content recovery --
            # letting the marker satisfy it would export the marker instead of
            # speech the store still had. Prepended after that fallback runs.
            unattached_gap_line = str(payload.get("unattached_gap_line") or "")
            snapshot_id = str(snap.get("snapshot_id") or "")
            rows = build_final_export_record_rows(snap, run_id=identity.run_id)
            consistency = validate_internal_consistency()
            if not consistency.get("ok", True):
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "FINAL_EXPORT_LEDGER_CONSISTENCY_FAILED",
                        issues=consistency.get("issues"),
                    )
                except Exception:
                    pass
        except Exception as exc:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "FINAL_EXPORT_SERIALIZE_FAILED",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )
            except Exception:
                pass
            text = ""

    if not text.strip():
        text, source_name = _committed_final_source_text(host)
        rows = []
        if not snapshot_id:
            snapshot_id = f"fallback-{source_name}"

    # Item 72: now that recovery has had its turn, say why the export looks the
    # way it does. Prepended whether or not recovery found anything -- an outage
    # is equally worth stating above recovered text as above nothing at all --
    # and it is the ONLY thing that distinguishes "the network died" from "the
    # session recorded nothing", which is otherwise invisible to whoever opens
    # the file.
    if unattached_gap_line:
        text = unattached_gap_line + "\n" + text if text.strip() else unattached_gap_line + "\n"

    final_source_segment_count = len([ln for ln in (text or "").splitlines() if ln.strip()])
    final_source_character_count = len((text or "").strip())
    result_flags["final_source_segment_count"] = final_source_segment_count
    result_flags["final_source_character_count"] = final_source_character_count
    result_flags["final_export_source"] = source_name

    if not text.strip():
        try:
            emergency_path.parent.mkdir(parents=True, exist_ok=True)
            emergency_path.write_text("", encoding="utf-8")
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "EMERGENCY_UNVERIFIED_TRANSCRIPT_WRITTEN",
                path=str(emergency_path),
                reason="no_committed_final_source",
            )
        except Exception:
            pass
        identity.stop_finalize_completed = False
        result_flags["finalization_failed"] = True
        return None

    if run_folder is None:
        identity.stop_finalize_completed = False
        result_flags["finalization_failed"] = True
        return None

    begin_final_export(
        run_folder,
        run_id=identity.run_id,
        snapshot_id=snapshot_id,
        expected_record_count=len(rows) or final_source_segment_count,
    )
    write_result = write_final_once(
        run_folder,
        run_id=identity.run_id,
        snapshot_id=snapshot_id,
        text=text,
        records=rows,
    )
    result_flags["empty_overwrite_prevented"] = bool(
        write_result.get("empty_overwrite_prevented")
    )
    result_flags["final_export_character_count"] = int(
        write_result.get("final_export_character_count")
        or len((text or "").strip())
    )
    if not write_result.get("ok"):
        identity.stop_finalize_completed = False
        result_flags["finalization_failed"] = True
        return None

    seal_final_export(
        run_folder,
        run_id=identity.run_id,
        snapshot_id=snapshot_id,
    )
    verify_final_export_seal(
        run_folder,
        run_id=identity.run_id,
        snapshot_id=snapshot_id,
    )

    final_copy = get_transcript_path("alpha_output_final")
    result_flags["alpha_output_written"] = True
    result_flags["final_export_records_written"] = True
    identity.stop_finalize_completed = True

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "FINAL_ALPHA_FROZEN_LEDGER_EXPORT_COMPLETED",
            path=str(final_copy),
            snapshot_id=snapshot_id,
            authoritative_alpha_output_sha256=transcript_sha256(text),
            record_count=len(rows),
            final_export_records_path=str(run_folder / "transcripts" / "final_export_records.jsonl"),
            write_count=1,
            sealed=True,
        )
    except Exception:
        pass
    return final_copy


def write_final_alpha_output(host: Any) -> Optional[Path]:
    return write_final_alpha_output_from_snapshot(host)


def write_live_run_status(host: Any, *, status: str, reason: str = "") -> Optional[Path]:
    identity = get_current_run_identity()
    if identity is None:
        return None
    folder = ensure_run_artifacts_folder(identity)
    from alpha.utils.troubleshooting_paths import get_artifact_path

    path = get_artifact_path("live_run_status")
    try:
        from alpha.utils.session_progress import build_progress_payload, set_run_status

        payload = {
            "status": status,
            "current_run_status": status,
            "reason": reason,
            "run_id": identity.run_id,
            "run_timestamp": identity.run_timestamp,
            "app_version": APP_VERSION,
            **build_progress_payload(host),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("LIVE_RUN_STATUS_UPDATED", status=status, reason=reason, path=str(path))
        return path
    except Exception:
        return None


def finalize_live_run_status_completed(
    host: Any,
    *,
    segment_counts: Optional[dict[str, Any]] = None,
    stop_summary: Optional[dict[str, Any]] = None,
    evidence_flags: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Write final LIVE_RUN_STATUS.json synchronously at normal stop completion."""
    import datetime

    identity = get_current_run_identity()
    if identity is None:
        return None
    from alpha.utils.troubleshooting_paths import get_artifact_path

    path = get_artifact_path("live_run_status")
    flags = dict(evidence_flags or {})
    try:
        from alpha.utils.session_progress import build_progress_payload, set_run_status

        completed_at = datetime.datetime.now().isoformat(timespec="seconds")
        # fixes TASK_4A_FINDINGS.md items 1/3: prefer the authoritative,
        # fail-closed value computed by
        # stop_finalize_worker.compute_core_final_status() (threaded through
        # via stop_summary["final_status"]/["stop_finalize_failed"]) instead
        # of deriving status from stop_finalize_timed_out alone. Falls back
        # to the old timeout-only rule only if a caller passes a stop_summary
        # that predates this field (defensive, not the expected live path).
        summary = stop_summary or {}
        if "final_status" in summary:
            final_status = str(summary.get("final_status") or "failed")
            stop_finalize_failed = bool(summary.get("stop_finalize_failed", True))
            failure_reason = str(summary.get("failure_reason") or "")
        else:
            final_status = "completed"
            if summary.get("stop_finalize_timed_out"):
                final_status = "completed_with_warnings"
            stop_finalize_failed = bool(summary.get("stop_finalize_failed", False))
            failure_reason = ""

        # Clear stop flags before writing completed status.
        if host is not None:
            try:
                host._is_stopping = False
                host._is_finalizing = False
                host.is_listening = False
            except Exception:
                pass

        payload = {
            "status": final_status,
            "current_run_status": final_status,
            "reason": "stop_finalize_completed",
            "run_id": identity.run_id,
            "run_timestamp": identity.run_timestamp,
            "app_version": APP_VERSION,
            "completed_at": completed_at,
            "stop_finalize_completed": True,
            # fixes TASK_4A_FINDINGS.md items 1/3: real value, not hardcoded.
            "stop_finalize_failed": stop_finalize_failed,
            "failure_reason": failure_reason,
            "stop_finalize_timed_out": bool(
                (stop_summary or {}).get("stop_finalize_timed_out", False)
            ),
            "timed_out_steps": list((stop_summary or {}).get("timed_out_steps") or []),
            "failed_steps": list((stop_summary or {}).get("failed_steps") or []),
            "alpha_output_written": bool(flags.get("alpha_output_written", False)),
            "run_artifacts_index_written": bool(
                flags.get("run_artifacts_index_written", False)
            ),
            "live_run_status_written": True,
            "upload_package_index_written": bool(
                flags.get("upload_package_index_written", False)
            ),
            "upload_package_zip_created": bool(flags.get("upload_package_zip_created", False)),
            "upload_package_zip_failed_non_blocking": bool(
                flags.get("upload_package_zip_failed_non_blocking", False)
            ),
            "process_health_timeline_written": bool(
                flags.get("process_health_timeline_written", False)
            ),
            "memory_trend_summary_written": bool(
                flags.get("memory_trend_summary_written", False)
            ),
            "validation_output_written": bool(flags.get("validation_output_written", False)),
            "final_alpha_output_written": bool(flags.get("alpha_output_written", False)),
            "final_run_index_written": bool(flags.get("run_artifacts_index_written", False)),
            **build_progress_payload(host),
        }
        if segment_counts:
            payload.update(segment_counts)
        if stop_summary:
            for key, value in stop_summary.items():
                if key not in payload:
                    payload[key] = value

        if final_status.startswith("completed"):
            if payload.get("is_stopping") or payload.get("is_finalizing"):
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "FINAL_STATE_COMPLETION_BLOCKED",
                        is_stopping=payload.get("is_stopping"),
                        is_finalizing=payload.get("is_finalizing"),
                    )
                except Exception:
                    pass
                final_status = "stopped_with_errors"
            payload["is_stopping"] = False
            payload["is_finalizing"] = False
            payload["language_pipeline_worker_alive"] = False
            payload["status"] = final_status
            payload["current_run_status"] = final_status
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("STOP_FLAGS_CLEARED_BEFORE_COMPLETION")
                jp_accuracy_log("FINALIZATION_STATE_RECONCILED")
            except Exception:
                pass

        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        set_run_status(final_status)
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "LIVE_RUN_STATUS_FINAL_ARTIFACT_FLAGS_UPDATED",
                path=str(path),
                **{k: payload[k] for k in (
                    "alpha_output_written",
                    "run_artifacts_index_written",
                    "upload_package_index_written",
                    "upload_package_zip_created",
                    "process_health_timeline_written",
                    "memory_trend_summary_written",
                ) if k in payload},
            )
            jp_accuracy_log(
                "LIVE_RUN_STATUS_FINALIZED_COMPLETED",
                path=str(path),
                completed_at=completed_at,
            )
            jp_accuracy_log("LIVE_RUN_STATUS_ARTIFACT_FLAGS_CONSISTENT")
        except Exception:
            pass
        return path
    except Exception:
        return None


def finalize_live_run_status_incomplete(
    host: Any,
    *,
    status: str,
    reason: str,
) -> Optional[Path]:
    """Write incomplete/hang/crash LIVE_RUN_STATUS.json."""
    identity = get_current_run_identity()
    if identity is None:
        return None
    folder = ensure_run_artifacts_folder(identity)
    from alpha.utils.troubleshooting_paths import get_artifact_path

    path = get_artifact_path("live_run_status")
    try:
        from alpha.utils.session_progress import build_progress_payload, set_run_status

        payload = {
            "status": status,
            "current_run_status": status,
            "incomplete_reason": reason,
            "crash_reason": reason if "crash" in status else "",
            "partial_artifacts_available": True,
            "run_id": identity.run_id,
            "run_timestamp": identity.run_timestamp,
            "app_version": APP_VERSION,
            **build_progress_payload(host),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        set_run_status(status)
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "LIVE_RUN_STATUS_FINALIZED_INCOMPLETE",
                status=status,
                reason=reason,
                path=str(path),
            )
        except Exception:
            pass
        create_upload_evidence_package(host, status=status)
        return path
    except Exception:
        return None


def write_last_health_snapshot(payload: dict[str, Any]) -> Optional[Path]:
    identity = get_current_run_identity()
    if identity is None:
        return None
    from alpha.utils.troubleshooting_paths import get_health_path

    path = get_health_path("last_health_snapshot")
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:
        return None


def write_thread_dump_file(text: str, *, reason: str) -> Path:
    from alpha.utils.troubleshooting_paths import get_thread_dump_path

    folder = get_thread_dump_path("THREAD_DUMP_LAST.txt").parent
    from alpha.utils.thread_dump import write_thread_dumps

    result = write_thread_dumps(folder, reason=reason)
    last = result.get("last_path")
    if last:
        return Path(str(last))
    path = get_thread_dump_path("THREAD_DUMP_LAST.txt")
    path.write_text(text or "", encoding="utf-8")
    return path


def _write_partial_index(host: Any, *, status: str, reason: str = "") -> Optional[Path]:
    identity = get_current_run_identity()
    if identity is None:
        return None
    folder = ensure_run_artifacts_folder(identity)
    partial_path = folder / "RUN_ARTIFACTS_INDEX.partial.txt"
    extra = {"partial_reason": reason, "autosave": True}
    try:
        from alpha.utils.session_progress import build_progress_payload

        extra.update(build_progress_payload(host))
    except Exception:
        pass
    lines = _build_index_lines(identity, status=status, host=host, extra=extra)
    partial_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

    jp_accuracy_log("PARTIAL_RUN_INDEX_AUTOSAVED", status=status, path=str(partial_path))
    return partial_path


def write_latest_live_partial_pointer(
    *,
    identity: RunIdentity,
    index_path: Path,
    host: Any = None,
    status: str = "in_progress",
    reason: str = "",
) -> Path:
    paths = _log_paths()
    root = _PROJECT_ROOT
    latest_path = root / "LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt"
    folder = index_path.parent
    partial_alpha = folder / "Alpha_output_PARTIAL.txt"
    lines = [
        f"status={status}",
        f"latest_live_run_id={identity.run_id}",
        f"latest_live_run_timestamp={identity.run_timestamp}",
        f"latest_live_app_version={APP_VERSION}",
        f"latest_live_index_path={index_path}",
        f"partial_index_path={folder / 'RUN_ARTIFACTS_INDEX.partial.txt'}",
        f"RUN_TYPE={identity.run_type}",
        f"artifact_folder={folder}",
        f"alpha_output_partial_path={partial_alpha}",
        f"alpha_output_path={root / 'Alpha output.txt'}",
        f"live_run_status_path={folder / 'LIVE_RUN_STATUS.json'}",
        f"health_snapshot_path={folder / 'LAST_HEALTH_SNAPSHOT.json'}",
        f"thread_dump_path={folder / 'THREAD_DUMP_LAST.txt'}",
        f"partial_reason={reason}",
        f"debug_log_path={paths['debug_log']}",
        f"japanese_accuracy_log_path={paths['japanese_accuracy_log']}",
        f"diagnostic_log_path={paths['diagnostic_log']}",
        f"freeze_guard_log_path={paths['freeze_guard_log']}",
    ]
    latest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

    jp_accuracy_log(
        "LATEST_LIVE_INDEX_POINTER_UPDATED_PARTIAL",
        status=status,
        path=str(latest_path),
    )
    jp_accuracy_log("STALE_ROOT_INDEX_PREVENTED", run_id=identity.run_id)
    return latest_path


def write_health_timeline_line(payload: dict[str, Any]) -> Optional[Path]:
    identity = get_current_run_identity()
    if identity is None:
        return None
    from alpha.utils.troubleshooting_paths import get_health_path

    path = get_health_path("health_timeline")
    try:
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        return path
    except Exception:
        return None


_partial_autosave_started = False


def autosave_partial_alpha_background(
    *, reason: str = "autosave", host: Any = None
) -> None:
    """Background-only partial Alpha output from snapshot."""
    from alpha.utils.ui_thread_guard import guard_ui_thread_blocking_call

    if not guard_ui_thread_blocking_call("autosave_partial_alpha_background"):
        return
    identity = get_current_run_identity()
    if identity is None or identity.run_type != RUN_TYPE_LIVE:
        return
    if host is not None and not bool(getattr(host, "is_listening", False)):
        if reason not in (
            "crash",
            "window_close",
            "ui_mainloop_stall_confirmed",
            "ui_mainloop_stall_suspected",
        ):
            return
    write_partial_alpha_output_from_snapshot(reason=reason, host=host)


def autosave_partial_index_background(
    *, reason: str = "autosave", host: Any = None
) -> None:
    """Background partial index + live status (less frequent than alpha)."""
    from alpha.utils.ui_thread_guard import guard_ui_thread_blocking_call

    if not guard_ui_thread_blocking_call("autosave_partial_index_background"):
        return
    identity = get_current_run_identity()
    if identity is None or identity.run_type != RUN_TYPE_LIVE:
        return
    if host is not None and not bool(getattr(host, "is_listening", False)):
        if reason not in (
            "crash",
            "window_close",
            "ui_mainloop_stall_confirmed",
            "ui_mainloop_stall_suspected",
        ):
            return
    partial_index = _write_partial_index(host, status="in_progress", reason=reason)
    write_live_run_status(host, status="in_progress", reason=reason)
    if partial_index is not None and identity is not None:
        write_latest_live_partial_pointer(
            identity=identity,
            index_path=partial_index,
            host=host,
            status="in_progress",
            reason=reason,
        )
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "PARTIAL_RUN_INDEX_AUTOSAVED_BACKGROUND",
                status="in_progress",
                path=str(partial_index),
            )
        except Exception:
            pass


def autosave_partial_artifacts_background(
    *, reason: str = "autosave", host: Any = None
) -> None:
    """Full partial autosave — alpha + index (crash/stall/window_close)."""
    autosave_partial_alpha_background(reason=reason, host=host)
    autosave_partial_index_background(reason=reason, host=host)
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("CRASH_RECOVERY_ARTIFACTS_AVAILABLE", reason=reason)
    except Exception:
        pass


def autosave_partial_artifacts(host: Any, *, reason: str = "autosave") -> None:
    """Legacy shim — always routes to background snapshot path."""
    autosave_partial_artifacts_background(reason=reason, host=host)


def write_crash_safe_index(*, status: str, reason: str) -> Optional[Path]:
    identity = get_current_run_identity()
    if identity is None:
        return None
    folder = ensure_run_artifacts_folder(identity)
    path = folder / "RUN_ARTIFACTS_INDEX.txt"
    host = None
    try:
        from alpha.utils.session_watchdog import get_watchdog_host

        host = get_watchdog_host()
    except Exception:
        pass
    lines = _build_index_lines(
        identity,
        status=status,
        host=host,
        extra={"crash_reason": reason, "crash_safe": True},
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

    jp_accuracy_log("CRASH_SAFE_ARTIFACT_INDEX_WRITTEN", status=status, reason=reason)
    return path


def recover_previous_incomplete_runs_on_startup() -> list[dict[str, Any]]:
    """Detect prior live runs that never completed stop/finalize."""
    recovered: list[dict[str, Any]] = []
    if not _LIVE_ROOT.exists():
        return recovered
    for folder in sorted(_LIVE_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not folder.is_dir():
            continue
        final_index = folder / "RUN_ARTIFACTS_INDEX.txt"
        partial_index = folder / "RUN_ARTIFACTS_INDEX.partial.txt"
        live_status = folder / "LIVE_RUN_STATUS.json"
        status = ""
        if final_index.exists():
            text = final_index.read_text(encoding="utf-8", errors="ignore")
            if "status=completed" in text or "status=completed_with_warnings" in text:
                continue
            if "status=started" in text or "status=in_progress" in text:
                status = "started_no_finalize"
            else:
                status = "incomplete_final_index"
        elif partial_index.exists() or live_status.exists():
            status = "incomplete_partial_only"
        else:
            continue
        entry = {
            "artifact_folder": str(folder),
            "status": status,
            "partial_index": str(partial_index) if partial_index.exists() else "",
            "partial_alpha": str(folder / "Alpha_output_PARTIAL.txt"),
        }
        recovered.append(entry)
    if not recovered:
        return recovered
    forensic_analyses: list[dict[str, Any]] = []
    try:
        from alpha.utils.session_forensics import (
            analyze_incomplete_run,
            write_previous_run_forensic_summary,
        )

        for item in recovered[:5]:
            folder = Path(item.get("artifact_folder", ""))
            if folder.is_dir():
                forensic_analyses.append(analyze_incomplete_run(folder))
        if forensic_analyses:
            write_previous_run_forensic_summary(forensic_analyses)
    except Exception:
        pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log
        from alpha.utils.session_progress import increment_counter

        increment_counter("previous_incomplete_run_detected_count")
        jp_accuracy_log("PREVIOUS_INCOMPLETE_RUN_DETECTED", runs=recovered[:5])
        summary_path = _PROJECT_ROOT / "RECOVERED_INCOMPLETE_RUN_INDEX.txt"
        lines = ["# Recovered incomplete live runs — do not overwrite", ""]
        for item in recovered[:10]:
            lines.append(f"folder={item['artifact_folder']}")
            lines.append(f"status={item['status']}")
            lines.append("")
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        jp_accuracy_log(
            "PREVIOUS_RUN_RECOVERY_SUMMARY_WRITTEN",
            path=str(summary_path),
            count=len(recovered),
        )
        jp_accuracy_log("RECOVERED_INCOMPLETE_RUN_INDEX_WRITTEN", path=str(summary_path))
    except Exception:
        pass
    return recovered


def create_upload_evidence_package(
    host: Any = None,
    *,
    status: str = "completed",
) -> Optional[Path]:
    """Build upload package under troubleshooting/runs/<run_id>/upload_package/."""
    if not LONG_RUN_EVIDENCE_PACKAGE_MODE:
        return None

    identity = get_current_run_identity()
    if identity is None:
        return None
    folder = ensure_run_artifacts_folder(identity)
    from alpha.utils.troubleshooting_paths import (
        get_accuracy_path,
        get_artifact_path,
        get_audio_temp_path,
        get_health_path,
        get_log_path,
        get_run_manifest_path,
        get_thread_dump_path,
        get_transcript_path,
        get_upload_package_path,
        get_validation_path,
        preflight_upload_evidence,
    )

    preflight_upload_evidence(folder)

    ts = identity.run_timestamp
    upload_dir = get_upload_package_path("upload_package_index").parent
    upload_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[tuple[str, Path]] = [
        ("RUN_MANIFEST.json", get_run_manifest_path()),
        ("japanese_accuracy.log", get_log_path("japanese_accuracy")),
        ("diagnostic_test.log", get_log_path("diagnostic_test")),
        ("freeze_guard.log", get_log_path("freeze_guard")),
        ("debug.log", get_log_path("debug")),
        ("async_debug.log", get_log_path("async_debug")),
        ("deepgram_events.jsonl", get_log_path("deepgram_events")),
        ("stop_finalize_timeline.jsonl", get_log_path("stop_finalize_timeline")),
        ("ui_event_bus_timeline.jsonl", get_log_path("ui_event_bus_timeline")),
        ("queue_timeline.jsonl", get_log_path("queue_timeline")),
        ("thread_safety.jsonl", get_log_path("thread_safety")),
        ("background_tk_guard.jsonl", get_log_path("background_tk_guard")),
        ("Alpha output.txt", get_transcript_path("alpha_output")),
        ("Alpha_output_PARTIAL.txt", get_transcript_path("alpha_output_partial")),
        ("Alpha_output_FINAL.txt", get_transcript_path("alpha_output_final")),
        ("raw_deepgram_finals.jsonl", get_transcript_path("raw_deepgram_finals")),
        ("raw_deepgram_interims_sampled.jsonl", get_transcript_path("raw_deepgram_interims_sampled")),
        ("stable_commits.jsonl", get_transcript_path("stable_commits")),
        ("ui_exported_segments.jsonl", get_transcript_path("ui_exported_segments")),
        ("assembler_decisions.jsonl", get_accuracy_path("assembler_decisions")),
        ("quarantine_decisions.jsonl", get_accuracy_path("quarantine_decisions")),
        ("correction_decisions.jsonl", get_accuracy_path("correction_decisions")),
        ("japanese_accuracy_summary.json", get_accuracy_path("japanese_accuracy_summary")),
        ("translation_readiness_summary.json", get_accuracy_path("translation_readiness_summary")),
        ("LAST_HEALTH_SNAPSHOT.json", get_health_path("last_health_snapshot")),
        ("HEALTH_TIMELINE.jsonl", get_health_path("health_timeline")),
        ("PROCESS_HEALTH_TIMELINE.jsonl", get_health_path("process_health_timeline")),
        ("QUEUE_HEALTH_TIMELINE.jsonl", get_health_path("queue_health_timeline")),
        ("MEMORY_TREND_SUMMARY.json", get_health_path("memory_trend_summary")),
        ("RUN_ARTIFACTS_INDEX.txt", get_artifact_path("run_artifacts_index")),
        ("RUN_ARTIFACTS_INDEX.partial.txt", get_artifact_path("run_artifacts_index_partial")),
        ("LIVE_RUN_STATUS.json", get_artifact_path("live_run_status")),
        ("FLIGHT_RECORDER.log", get_artifact_path("flight_recorder")),
        ("PREVIOUS_RUN_FORENSIC_SUMMARY.txt", get_artifact_path("previous_run_forensic_summary")),
        ("THREAD_DUMP_SELFTEST.txt", get_thread_dump_path("THREAD_DUMP_SELFTEST.txt")),
        ("THREAD_DUMP_LAST.txt", get_thread_dump_path("THREAD_DUMP_LAST.txt")),
        ("audio_manifest.json", get_audio_temp_path("audio_manifest")),
        ("audio_temp_summary.txt", get_audio_temp_path("audio_temp_summary")),
        ("validate_8520_1_output.txt", get_validation_path("validate_8520_1_output")),
        ("validate_8520_2_output.txt", get_validation_path("validate_8520_2_output")),
    ]
    stage_names = (
        "raw_deepgram.txt",
        "raw_deepgram_events.jsonl",
        "stable_assembler_only.txt",
        "stable_assembler_events.jsonl",
        "final_alpha_output.txt",
        "deepgram_request_snapshot.json",
        "audio_delivery_summary.json",
        "stage_manifest.json",
        "three_stage_accuracy_report.json",
        "three_stage_accuracy_report.txt",
    )
    stage_base = folder / "accuracy_stage_compare"
    for sn in stage_names:
        sp = stage_base / sn
        if sp.exists():
            candidates.append((sn, sp))

    for stall in (get_thread_dump_path("THREAD_DUMP_LAST.txt").parent).glob(
        "THREAD_DUMP_UI_STALL_*.txt"
    ):
        candidates.append((stall.name, stall))

    index_path = get_upload_package_path("upload_package_index")
    lines = [
        f"# Upload package index — v{APP_VERSION} — status={status}",
        f"run_id={identity.run_id}",
        f"run_timestamp={identity.run_timestamp}",
        f"run_folder={folder}",
        "",
    ]
    lines.append("")
    lines.append("# Audio WAV files excluded by policy")
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("UPLOAD_PACKAGE_AUDIO_EXCLUDED")
        jp_accuracy_log("UPLOAD_PACKAGE_AUDIO_EXCLUDED_BY_POLICY")
        jp_accuracy_log("AUDIO_TEMP_WAV_EXCLUDED_FROM_UPLOAD_PACKAGE")
        jp_accuracy_log("AUDIO_TEMP_EXCLUDED_FROM_UPLOAD_PACKAGE_BY_DEFAULT")
    except Exception:
        pass

    existing_files: list[tuple[str, Path]] = []
    optional_files = {
        "RUN_ARTIFACTS_INDEX.partial.txt",
        "PREVIOUS_RUN_FORENSIC_SUMMARY.txt",
        "THREAD_DUMP_LAST.txt",
    }
    required_files = {
        "validate_8520_2_output.txt",
        "PROCESS_HEALTH_TIMELINE.jsonl",
        "MEMORY_TREND_SUMMARY.json",
    }
    for label, path in candidates:
        exists = path.exists() and str(path) not in ("", "(not available)")
        if exists and path.stat().st_size == 0:
            flag = "CREATED_EMPTY_MARKER"
        elif exists:
            flag = "EXISTS"
        else:
            if label in optional_files:
                flag = "OPTIONAL_NOT_CREATED"
            elif label in required_files:
                flag = "MISSING_ERROR"
            else:
                flag = "MISSING_ERROR"
        lines.append(f"{label}\t{path}\t{flag}")
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            if exists:
                jp_accuracy_log("UPLOAD_PACKAGE_FILE_EXISTS", file=label, path=str(path))
                existing_files.append((label, path))
            else:
                jp_accuracy_log("UPLOAD_PACKAGE_FILE_MISSING", file=label, path=str(path))
        except Exception:
            if exists:
                existing_files.append((label, path))

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "UPLOAD_PACKAGE_INDEX_CREATED",
            path=str(index_path),
            file_count=len(existing_files),
        )
    except Exception:
        pass

    zip_path = upload_dir / f"UPLOAD_PACKAGE_v{APP_VERSION}_{ts}.zip"
    try:
        import zipfile

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for label, path in existing_files:
                try:
                    arcname = f"{path.parent.name}/{path.name}" if path.parent != folder else path.name
                    zf.write(path, arcname=arcname)
                except Exception:
                    pass
            zf.write(index_path, arcname="UPLOAD_PACKAGE_INDEX.txt")
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "UPLOAD_PACKAGE_ZIP_CREATED",
                path=str(zip_path),
                file_count=len(existing_files),
            )
            jp_accuracy_log("UPLOAD_PACKAGE_COMPLETE", path=str(zip_path))
        except Exception:
            pass
        return zip_path
    except Exception:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("UPLOAD_PACKAGE_ZIP_FAILED_NON_BLOCKING")
        except Exception:
            pass
        return index_path
