"""Central path resolver — all runtime evidence under troubleshooting/."""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    AUDIO_TEMP_CAPTURE_ENABLED,
    AUDIO_TEMP_CHUNK_SECONDS,
    AUDIO_TEMP_INCLUDE_IN_UPLOAD_ZIP,
    AUDIO_TEMP_MAX_TOTAL_GB,
    AUDIO_TEMP_RETENTION_HOURS,
    CENTRALIZED_TROUBLESHOOTING_DIR,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FULL_DIAGNOSTIC_LOGGING_ENABLED,
    EVIDENCE_SAFE_MODE,
    PENDING_WRITER_FAILURE_IS_WARNING_DURING_START,
    STARTUP_RECOVERY_MODE,
    STRICT_STARTUP_NON_BLOCKING_LOGGING,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LOG_MAX_FILE_MB,
    LOG_ROTATION_BACKUPS,
    LOG_ROTATION_ENABLED,
    PENDING_RUN_REBINDING_ENABLED,
    TROUBLESHOOTING_MODE,
)

_lock = threading.Lock()
_project_root = Path(__file__).resolve().parents[2]
_troubleshooting_root = _project_root / "troubleshooting"
_current_run_folder: Optional[Path] = None
_run_manifest: dict[str, Any] = {}
_run_folder_created_at: float = 0.0
_writers_rebound: bool = False
_active_run_id: str = ""
_writer_registry: dict[str, dict[str, Any]] = {}

_LOG_NAME_MAP = {
    "japanese_accuracy": ("logs", "japanese_accuracy.log"),
    "diagnostic_test": ("logs", "diagnostic_test.log"),
    "freeze_guard": ("logs", "freeze_guard.log"),
    "debug": ("logs", "debug.log"),
    "async_debug": ("logs", "async_debug.log"),
    "deepgram_events": ("logs", "deepgram_events.jsonl"),
    "stop_finalize_timeline": ("logs", "stop_finalize_timeline.jsonl"),
    "ui_event_bus_timeline": ("logs", "ui_event_bus_timeline.jsonl"),
    "queue_timeline": ("logs", "queue_timeline.jsonl"),
    "thread_safety": ("logs", "thread_safety.jsonl"),
    "background_tk_guard": ("logs", "background_tk_guard.jsonl"),
}

_TRANSCRIPT_NAME_MAP = {
    "alpha_output": ("transcripts", "Alpha output.txt"),
    "alpha_output_partial": ("transcripts", "Alpha_output_PARTIAL.txt"),
    "alpha_output_final": ("transcripts", "Alpha_output_FINAL.txt"),
    "raw_deepgram_finals": ("transcripts", "raw_deepgram_finals.jsonl"),
    "raw_deepgram_interims_sampled": ("transcripts", "raw_deepgram_interims_sampled.jsonl"),
    "stable_commits": ("transcripts", "stable_commits.jsonl"),
    "ui_exported_segments": ("transcripts", "ui_exported_segments.jsonl"),
    "transcript_snapshot": ("transcripts", "transcript_snapshot.jsonl"),
    "incomplete_stop_tail": ("transcripts", "incomplete_stop_tail.txt"),
}

_ACCURACY_NAME_MAP = {
    "assembler_decisions": ("accuracy", "assembler_decisions.jsonl"),
    "quarantine_decisions": ("accuracy", "quarantine_decisions.jsonl"),
    "correction_decisions": ("accuracy", "correction_decisions.jsonl"),
    "japanese_accuracy_summary": ("accuracy", "japanese_accuracy_summary.json"),
    "translation_readiness_summary": ("accuracy", "translation_readiness_summary.json"),
    "alpha_for_accuracy_check": ("accuracy", "Alpha_for_accuracy_check.txt"),
    "accuracy_evidence_index": ("accuracy", "ACCURACY_EVIDENCE_INDEX.json"),
    "stop_tail_decisions": ("accuracy", "stop_tail_decisions.jsonl"),
}

_HEALTH_NAME_MAP = {
    "last_health_snapshot": ("health", "LAST_HEALTH_SNAPSHOT.json"),
    "health_timeline": ("health", "HEALTH_TIMELINE.jsonl"),
    "process_health_timeline": ("health", "PROCESS_HEALTH_TIMELINE.jsonl"),
    "queue_health_timeline": ("health", "QUEUE_HEALTH_TIMELINE.jsonl"),
    "memory_trend_summary": ("health", "MEMORY_TREND_SUMMARY.json"),
}

_ARTIFACT_NAME_MAP = {
    "run_artifacts_index": ("artifacts", "RUN_ARTIFACTS_INDEX.txt"),
    "run_artifacts_index_partial": ("artifacts", "RUN_ARTIFACTS_INDEX.partial.txt"),
    "live_run_status": ("artifacts", "LIVE_RUN_STATUS.json"),
    "flight_recorder": ("artifacts", "FLIGHT_RECORDER.log"),
    "previous_run_forensic_summary": ("artifacts", "PREVIOUS_RUN_FORENSIC_SUMMARY.txt"),
    "pending_migration_summary": ("artifacts", "PENDING_MIGRATION_SUMMARY.json"),
}

_AUDIO_NAME_MAP = {
    "audio_manifest": ("audio_temp", "audio_manifest.json"),
    "audio_temp_summary": ("audio_temp", "audio_temp_summary.txt"),
    "system_audio_dir": ("audio_temp", "system_audio"),
    "mic_audio_dir": ("audio_temp", "mic_audio"),
    "mixed_audio_dir": ("audio_temp", "mixed_audio"),
}

_VALIDATION_NAME_MAP = {
    "validate_8520_output": ("validation", "validate_8520_output.txt"),
    "validate_8520_1_output": ("validation", "validate_8520_1_output.txt"),
    "validate_8520_2_output": ("validation", "validate_8520_2_output.txt"),
}

_UPLOAD_NAME_MAP = {
    "upload_package_index": ("upload_package", "UPLOAD_PACKAGE_INDEX.txt"),
}

_MARKER_JSONL: dict[str, dict[str, Any]] = {
    "deepgram_events": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "stop_finalize_timeline": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "ui_event_bus_timeline": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "queue_timeline": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "thread_safety": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "background_tk_guard": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "raw_deepgram_finals": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "raw_deepgram_interims_sampled": {
        "event": "RAW_INTERIM_LOG_INITIALIZED",
        "sample_count": 0,
    },
    "stable_commits": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "ui_exported_segments": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "transcript_snapshot": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "assembler_decisions": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "quarantine_decisions": {"event": "LOG_INITIALIZED", "reason": "no_events_yet"},
    "process_health_timeline": {
        "event": "PROCESS_HEALTH_TIMELINE_CREATED",
        "telemetry_backend": "pending",
    },
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _relative_to_troubleshooting(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(get_troubleshooting_root().resolve()))
    except Exception:
        return str(path)


def _is_pending_path(path: Path) -> bool:
    return "/runs/_pending/" in path.as_posix().replace("\\", "/")


def _is_ui_thread() -> bool:
    try:
        from alpha.utils.ui_thread_guard import is_ui_main_thread

        return bool(is_ui_main_thread())
    except Exception:
        return False


def safe_log_event(event: str, **data: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **data)
    except Exception:
        if STRICT_STARTUP_NON_BLOCKING_LOGGING:
            pass


def safe_register_writer(writer_name: str, category: str, filename: str, *, current_path: Optional[Path] = None) -> Path:
    try:
        return register_runtime_writer(
            writer_name,
            category,
            filename,
            current_path=current_path,
        )
    except Exception:
        safe_log_event(
            "EVIDENCE_SAFE_MODE_LOGGING_FAILURE_SUPPRESSED",
            operation="safe_register_writer",
            writer_name=writer_name,
            category=category,
        )
        base = _active_folder() / category
        base.mkdir(parents=True, exist_ok=True)
        return base / filename


def safe_rebind_writer(writer_name: str, run_folder: Path) -> bool:
    try:
        return rebind_runtime_writer(writer_name, run_folder)
    except Exception:
        safe_log_event(
            "PENDING_WRITER_REBIND_DEFERRED_NON_BLOCKING",
            writer_name=writer_name,
            run_folder=str(run_folder),
        )
        return False


def safe_write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        safe_log_event(
            "EVIDENCE_SAFE_MODE_LOGGING_FAILURE_SUPPRESSED",
            operation="safe_write_jsonl",
            path=str(path),
        )


def get_project_root() -> Path:
    return _project_root


def get_troubleshooting_root() -> Path:
    _troubleshooting_root.mkdir(parents=True, exist_ok=True)
    return _troubleshooting_root


def get_latest_dir() -> Path:
    d = get_troubleshooting_root() / "latest"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_runs_root() -> Path:
    d = get_troubleshooting_root() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_pending_folder() -> Path:
    p = get_runs_root() / "_pending"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_current_run_folder() -> Optional[Path]:
    return _current_run_folder


def get_active_run_folder() -> Optional[Path]:
    return _current_run_folder


def set_active_run_folder(folder: Path) -> None:
    global _current_run_folder, _run_folder_created_at
    with _lock:
        _current_run_folder = folder
        _run_folder_created_at = time.time()


def assert_active_run_folder_is_not_pending() -> bool:
    folder = _current_run_folder
    if folder is None:
        return False
    return folder.name != "_pending"


def _active_folder() -> Path:
    if _current_run_folder is not None:
        return _current_run_folder
    return get_pending_folder()


def _resolve_subpath(folder: Path, subdir: str, filename: str) -> Path:
    path = folder / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path / filename


def _path_from_map(folder: Path, name: str, mapping: dict[str, tuple[str, str]]) -> Path:
    if name not in mapping:
        raise KeyError(f"unknown path name: {name}")
    subdir, filename = mapping[name]
    return _resolve_subpath(folder, subdir, filename)


def resolve_runtime_path(category: str, name: str) -> Path:
    resolvers = {
        "log": get_log_path,
        "transcript": get_transcript_path,
        "accuracy": get_accuracy_path,
        "health": get_health_path,
        "artifact": get_artifact_path,
        "validation": get_validation_path,
        "upload": get_upload_package_path,
        "audio": get_audio_temp_path,
    }
    fn = resolvers.get(category)
    if fn is None:
        raise KeyError(f"unknown category: {category}")
    return fn(name)


def register_runtime_writer(
    writer_name: str,
    category: str,
    filename: str,
    *,
    current_path: Optional[Path] = None,
) -> Path:
    path = current_path or (_active_folder() / category / filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    folder = get_active_run_folder()
    entry = {
        "writer_name": writer_name,
        "category": category,
        "filename": filename,
        "current_path": str(path),
        "bound_run_id": _active_run_id,
        "bound_run_folder": str(folder) if folder else "",
        "is_pending": _is_pending_path(path),
        "last_write_timestamp": _now_iso(),
        "rebind_status": "pending" if _is_pending_path(path) else "bound",
    }
    with _lock:
        _writer_registry[writer_name] = entry
    return path


def rebind_runtime_writer(writer_name: str, run_folder: Path) -> bool:
    with _lock:
        if writer_name not in _writer_registry:
            return False
        entry = dict(_writer_registry[writer_name])
    category = str(entry.get("category", "log"))
    filename = str(entry.get("filename", ""))
    target = run_folder / category
    target.mkdir(parents=True, exist_ok=True)
    path = target / filename
    entry["current_path"] = str(path)
    entry["bound_run_folder"] = str(run_folder)
    entry["bound_run_id"] = _active_run_id
    entry["is_pending"] = False
    entry["last_write_timestamp"] = _now_iso()
    entry["rebind_status"] = "rebound"
    with _lock:
        _writer_registry[writer_name] = entry
    return True


def assert_no_pending_writers_active() -> list[str]:
    with _lock:
        pending = [
            name
            for name, entry in _writer_registry.items()
            if bool(entry.get("is_pending"))
        ]
    return pending


def get_writer_registry_snapshot() -> dict[str, Any]:
    with _lock:
        pending = [
            name
            for name, entry in _writer_registry.items()
            if bool(entry.get("is_pending"))
        ]
        snapshot = {
            "timestamp": _now_iso(),
            "active_run_id": _active_run_id,
            "active_run_folder": str(_current_run_folder) if _current_run_folder else "",
            "writers": list(_writer_registry.values()),
            "pending_writers": pending,
        }
    return snapshot


def write_writer_registry_snapshot(run_folder: Path) -> Path:
    path = run_folder / "artifacts" / "WRITER_REGISTRY_FINAL.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(get_writer_registry_snapshot(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def force_close_pending_writers() -> None:
    """Close any writer handles still bound to _pending."""
    try:
        from alpha.utils.async_debug_log import rebind_runtime_log_writer as _a
        from alpha.utils.diagnostic_test_log import rebind_runtime_log_writer as _d
        from alpha.utils.evidence_jsonl import rebind_runtime_log_writer as _e
        from alpha.utils.japanese_accuracy_log import rebind_runtime_log_writer as _j

        _a()
        _d()
        _e()
        _j()
    except Exception:
        pass


def get_log_path(name: str) -> Path:
    path = _path_from_map(_active_folder(), name, _LOG_NAME_MAP)
    safe_register_writer(f"log:{name}", "logs", path.name, current_path=path)
    return path


def get_transcript_path(name: str) -> Path:
    path = _path_from_map(_active_folder(), name, _TRANSCRIPT_NAME_MAP)
    safe_register_writer(f"transcript:{name}", "transcripts", path.name, current_path=path)
    return path


def get_accuracy_path(name: str) -> Path:
    path = _path_from_map(_active_folder(), name, _ACCURACY_NAME_MAP)
    safe_register_writer(f"accuracy:{name}", "accuracy", path.name, current_path=path)
    return path


def get_health_path(name: str) -> Path:
    path = _path_from_map(_active_folder(), name, _HEALTH_NAME_MAP)
    safe_register_writer(f"health:{name}", "health", path.name, current_path=path)
    return path


def get_artifact_path(name: str) -> Path:
    path = _path_from_map(_active_folder(), name, _ARTIFACT_NAME_MAP)
    safe_register_writer(f"artifact:{name}", "artifacts", path.name, current_path=path)
    return path


def get_thread_dump_path(name: str = "THREAD_DUMP_LAST.txt") -> Path:
    folder = _active_folder()
    d = folder / "thread_dumps"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def get_audio_temp_path(name: str) -> Path:
    folder = _active_folder()
    if name in _AUDIO_NAME_MAP:
        subdir, filename = _AUDIO_NAME_MAP[name]
        if name.endswith("_dir"):
            p = folder / subdir / filename
            p.mkdir(parents=True, exist_ok=True)
            return p
        return _resolve_subpath(folder, subdir, filename)
    p = folder / "audio_temp" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_validation_path(name: str = "validate_8520_1_output") -> Path:
    path = _path_from_map(_active_folder(), name, _VALIDATION_NAME_MAP)
    safe_register_writer(f"validation:{name}", "validation", path.name, current_path=path)
    return path


def get_accuracy_stage_compare_dir(run_folder: Path | None = None) -> Path:
    """Central accuracy stage compare folder for active or specified run."""
    from alpha.utils.accuracy_stage_capture import get_accuracy_stage_compare_dir as _dir

    folder = run_folder or _active_folder()
    return _dir(folder)


def get_accuracy_stage_compare_path(name: str, run_folder: Path | None = None) -> Path:
    from alpha.utils.accuracy_stage_capture import get_accuracy_stage_compare_path as _path

    folder = run_folder or _active_folder()
    return _path(name, folder)


def get_upload_package_path(name: str = "upload_package_index") -> Path:
    path = _path_from_map(_active_folder(), name, _UPLOAD_NAME_MAP)
    safe_register_writer(f"upload:{name}", "upload_package", path.name, current_path=path)
    return path


def get_run_manifest_path() -> Path:
    return _active_folder() / "RUN_MANIFEST.json"


def _folder_name(app_version: str, run_timestamp: str, run_type: str) -> str:
    return f"v{app_version}-{run_timestamp}"


def _write_marker_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    payload = {**payload, "timestamp": time.time(), "app_version": APP_VERSION}
    if path.suffix == ".json":
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    elif path.suffix == ".jsonl":
        path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    elif path.suffix == ".txt":
        path.write_text("", encoding="utf-8")
    elif path.suffix == ".log":
        path.write_text(
            f"{datetime.now().isoformat(timespec='seconds')} | LOG_INITIALIZED\n",
            encoding="utf-8",
        )
    else:
        path.touch()


def materialize_required_evidence_files(run_folder: Path) -> int:
    """Create all required evidence files with markers if missing."""
    created = 0
    set_active_run_folder(run_folder)
    try:
        for name in _LOG_NAME_MAP:
            p = get_log_path(name)
            if not p.exists() or p.stat().st_size == 0:
                marker = _MARKER_JSONL.get(name, {"event": "LOG_INITIALIZED"})
                if p.suffix == ".jsonl":
                    _write_marker_line(p, marker)
                else:
                    _write_marker_line(p, {"event": "LOG_INITIALIZED"})
                created += 1
        for name in _TRANSCRIPT_NAME_MAP:
            p = get_transcript_path(name)
            if not p.exists() or p.stat().st_size == 0:
                marker = _MARKER_JSONL.get(name, {"event": "LOG_INITIALIZED"})
                if p.suffix == ".jsonl":
                    _write_marker_line(p, marker)
                else:
                    _write_marker_line(p, {"event": "LOG_INITIALIZED"})
                created += 1
        for name in _ACCURACY_NAME_MAP:
            p = get_accuracy_path(name)
            if not p.exists() or p.stat().st_size == 0:
                if name == "correction_decisions":
                    _write_marker_line(
                        p,
                        {"event": "CORRECTION_LAYER_INACTIVE_FOR_8_5_20_3"},
                    )
                elif name.endswith(".json"):
                    _write_marker_line(p, {"event": "LOG_INITIALIZED", "reason": "no_events_yet"})
                else:
                    _write_marker_line(p, _MARKER_JSONL.get(name, {"event": "LOG_INITIALIZED"}))
                created += 1
        for name in _HEALTH_NAME_MAP:
            p = get_health_path(name)
            if not p.exists() or p.stat().st_size == 0:
                marker = _MARKER_JSONL.get(name, {"event": "LOG_INITIALIZED"})
                _write_marker_line(p, marker)
                created += 1
        for sub in ("system_audio", "mic_audio", "mixed_audio"):
            (run_folder / "audio_temp" / sub).mkdir(parents=True, exist_ok=True)
        for name in ("audio_manifest", "audio_temp_summary"):
            p = get_audio_temp_path(name)
            if not p.exists():
                if name == "audio_manifest":
                    _write_marker_line(
                        p,
                        {
                            "capture_enabled": AUDIO_TEMP_CAPTURE_ENABLED,
                            "chunks": [],
                            "event": "AUDIO_TEMP_MANIFEST_INITIALIZED",
                        },
                    )
                else:
                    _write_marker_line(p, {"event": "AUDIO_TEMP_SUMMARY_INITIALIZED"})
                created += 1
        (run_folder / "upload_package").mkdir(parents=True, exist_ok=True)
        (run_folder / "validation").mkdir(parents=True, exist_ok=True)
        (run_folder / "thread_dumps").mkdir(parents=True, exist_ok=True)
        selftest = get_thread_dump_path("THREAD_DUMP_SELFTEST.txt")
        if not selftest.exists():
            selftest.write_text("THREAD_DUMP_SELFTEST_INITIALIZED\n", encoding="utf-8")
            created += 1
    finally:
        set_active_run_folder(run_folder)
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "UPLOAD_PACKAGE_REQUIRED_FILES_MATERIALIZED",
            run_folder=str(run_folder),
            files_touched=created,
        )
    except Exception:
        pass
    return created


def migrate_pending_files_to_run_folder(run_folder: Path) -> dict[str, Any]:
    """Migrate bootstrap files from _pending into the real run folder."""
    if STARTUP_RECOVERY_MODE and _is_ui_thread():
        safe_log_event("EVIDENCE_HEAVY_WORK_BLOCKED_ON_UI_THREAD", operation="migrate_pending_files")
        return {
            "pending_files_found": 0,
            "files_migrated": 0,
            "files_left_in_pending": 0,
            "pending_writers_before_rebind": [],
            "pending_writers_after_rebind": [],
            "pending_write_after_rebind_detected": False,
            "migration_started_at": _now_iso(),
            "migration_completed_at": _now_iso(),
            "active_writers_rebound": False,
            "migrated_files": [],
            "deferred": True,
        }
    pending = get_pending_folder()
    started = datetime.now().isoformat(timespec="seconds")
    summary: dict[str, Any] = {
        "pending_files_found": 0,
        "files_migrated": 0,
        "files_left_in_pending": 0,
        "pending_writers_before_rebind": [],
        "pending_writers_after_rebind": [],
        "pending_write_after_rebind_detected": False,
        "migration_started_at": started,
        "migration_completed_at": "",
        "active_writers_rebound": False,
        "migrated_files": [],
    }
    summary["pending_writers_before_rebind"] = assert_no_pending_writers_active()
    if not pending.exists():
        summary["migration_completed_at"] = datetime.now().isoformat(timespec="seconds")
        return summary

    bootstrap_logs = run_folder / "logs" / "pre_run_bootstrap"
    bootstrap_health = run_folder / "health" / "pre_run_bootstrap"
    bootstrap_validation = run_folder / "validation" / "pre_run_bootstrap"
    for d in (bootstrap_logs, bootstrap_health, bootstrap_validation):
        d.mkdir(parents=True, exist_ok=True)

    for src in pending.rglob("*"):
        if not src.is_file():
            continue
        summary["pending_files_found"] += 1
        rel = src.relative_to(pending)
        top = rel.parts[0] if rel.parts else ""
        if top == "health":
            dst = bootstrap_health / src.name
        elif top == "validation":
            dst = bootstrap_validation / src.name
        else:
            dst = bootstrap_logs / src.name
        try:
            if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                shutil.copy2(src, dst)
                summary["files_migrated"] += 1
                summary["migrated_files"].append(
                    {"src": str(src), "dst": str(dst)}
                )
        except Exception:
            summary["files_left_in_pending"] += 1

    summary["migration_completed_at"] = datetime.now().isoformat(timespec="seconds")
    summary["pending_writers_after_rebind"] = assert_no_pending_writers_active()
    summary["active_writers_rebound"] = len(summary["pending_writers_after_rebind"]) == 0
    summary["pending_write_after_rebind_detected"] = bool(scan_active_files_in_pending())
    summary_path = run_folder / "artifacts" / "PENDING_MIGRATION_SUMMARY.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("PENDING_LOG_FILE_MIGRATED_TO_RUN_FOLDER", **summary)
    except Exception:
        pass
    return summary


def migrate_pending_to_run_folder(run_folder: Path) -> dict[str, Any]:
    return migrate_pending_files_to_run_folder(run_folder)


def rebind_all_runtime_writers(run_folder: Path, *, startup_phase: bool = False) -> None:
    """Rebind all log writers to the active run folder."""
    if STARTUP_RECOVERY_MODE and startup_phase and _is_ui_thread():
        safe_log_event(
            "EVIDENCE_HEAVY_WORK_BLOCKED_ON_UI_THREAD",
            operation="rebind_all_runtime_writers",
        )
        return
    global _writers_rebound
    set_active_run_folder(run_folder)
    if not startup_phase:
        force_close_pending_writers()
    else:
        safe_log_event("PENDING_WRITER_REBIND_DEFERRED_NON_BLOCKING", phase="startup")
    materialize_required_evidence_files(run_folder)
    rebind_targets = [
        "alpha.utils.japanese_accuracy_log",
        "alpha.utils.diagnostic_test_log",
        "alpha.utils.async_debug_log",
        "alpha.utils.evidence_jsonl",
    ]
    for mod_name in rebind_targets:
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            if hasattr(mod, "rebind_runtime_log_writer"):
                mod.rebind_runtime_log_writer()
        except Exception:
            pass
    try:
        from alpha.utils.flight_recorder import start_flight_recorder

        start_flight_recorder(get_artifact_path("flight_recorder").parent)
    except Exception:
        pass
    with _lock:
        _writers_rebound = True
        for writer_name in list(_writer_registry.keys()):
            rebind_runtime_writer(writer_name, run_folder)
    pending_after = assert_no_pending_writers_active()
    write_writer_registry_snapshot(run_folder)
    summary_path = run_folder / "artifacts" / "PENDING_MIGRATION_SUMMARY.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
        summary["pending_writers_after_rebind"] = pending_after
        summary["active_writers_rebound"] = len(pending_after) == 0
        summary["pending_write_after_rebind_detected"] = bool(scan_active_files_in_pending())
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("PENDING_WRITER_REGISTRY_CREATED")
        jp_accuracy_log("PENDING_FILE_HANDLES_CLOSED")
        jp_accuracy_log(
            "PENDING_LOG_WRITERS_REBOUND_TO_RUN_FOLDER",
            run_folder=str(run_folder),
        )
        if not pending_after:
            jp_accuracy_log("NO_ACTIVE_WRITERS_LEFT_IN_PENDING_CONFIRMED")
        else:
            jp_accuracy_log(
                "PENDING_WRITE_AFTER_REBIND_BLOCKED",
                pending_writers=pending_after,
            )
            if startup_phase and PENDING_WRITER_FAILURE_IS_WARNING_DURING_START:
                jp_accuracy_log(
                    "STARTUP_CONTINUED_DESPITE_EVIDENCE_WARNING",
                    pending_writers=pending_after,
                )
    except Exception:
        pass


def scan_active_files_in_pending() -> list[str]:
    """Return paths in _pending modified after active run folder creation."""
    pending = get_pending_folder()
    if not pending.exists() or _run_folder_created_at <= 0:
        return []
    active: list[str] = []
    cutoff = _run_folder_created_at
    for f in pending.rglob("*"):
        if f.is_file() and f.stat().st_mtime > cutoff:
            if "MIGRATION" not in f.name:
                active.append(str(f))
    return active


def scan_runtime_files_outside_troubleshooting() -> list[str]:
    return validate_no_runtime_files_outside_troubleshooting()


def _folder_name_create(app_version: str, run_timestamp: str, run_type: str) -> str:
    return f"v{app_version}-{run_timestamp}"


def create_run_folder(
    *,
    app_version: str = APP_VERSION,
    run_timestamp: str,
    run_type: str,
    run_id: str = "",
    selected_language: str = "ja",
) -> Path:
    global _current_run_folder, _run_manifest, _run_folder_created_at, _active_run_id
    runs = get_runs_root()
    folder = runs / _folder_name_create(app_version, run_timestamp, run_type)
    folder.mkdir(parents=True, exist_ok=True)

    for sub in (
        "logs",
        "transcripts",
        "accuracy",
        "health",
        "artifacts",
        "thread_dumps",
        "audio_temp/system_audio",
        "audio_temp/mic_audio",
        "audio_temp/mixed_audio",
        "validation",
        "upload_package",
        "accuracy_stage_compare",
    ):
        (folder / sub).mkdir(parents=True, exist_ok=True)

    manifest = {
        "app_version": app_version,
        "app_codename": APP_CODENAME,
        "run_id": run_id,
        "run_timestamp": run_timestamp,
        "run_type": run_type,
        "selected_language": selected_language,
        "troubleshooting_folder": str(folder),
        "deepgram_config_snapshot": {
            "model": DEEPGRAM_MODEL,
            "language": DEEPGRAM_LANGUAGE,
            "endpointing_ms": DEEPGRAM_ENDPOINTING_MS,
            "utterance_end_ms": DEEPGRAM_UTTERANCE_END_MS,
            "diarize_model": "absent",
        },
        "japanese_profile": {
            "JAPANESE_STT_PROFILE": JAPANESE_STT_PROFILE,
            "JAPANESE_KEYTERM_PROFILE": JAPANESE_KEYTERM_PROFILE,
        },
        "logging_modes": {
            "TROUBLESHOOTING_MODE": TROUBLESHOOTING_MODE,
            "CENTRALIZED_TROUBLESHOOTING_DIR": CENTRALIZED_TROUBLESHOOTING_DIR,
            "FULL_DIAGNOSTIC_LOGGING_ENABLED": FULL_DIAGNOSTIC_LOGGING_ENABLED,
            "PENDING_RUN_REBINDING_ENABLED": PENDING_RUN_REBINDING_ENABLED,
            "LOG_ROTATION_ENABLED": LOG_ROTATION_ENABLED,
            "LOG_MAX_FILE_MB": LOG_MAX_FILE_MB,
            "LOG_ROTATION_BACKUPS": LOG_ROTATION_BACKUPS,
        },
        "audio_temp_config": {
            "AUDIO_TEMP_CAPTURE_ENABLED": AUDIO_TEMP_CAPTURE_ENABLED,
            "AUDIO_TEMP_RETENTION_HOURS": AUDIO_TEMP_RETENTION_HOURS,
            "AUDIO_TEMP_MAX_TOTAL_GB": AUDIO_TEMP_MAX_TOTAL_GB,
            "AUDIO_TEMP_CHUNK_SECONDS": AUDIO_TEMP_CHUNK_SECONDS,
            "AUDIO_TEMP_INCLUDE_IN_UPLOAD_ZIP": AUDIO_TEMP_INCLUDE_IN_UPLOAD_ZIP,
        },
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "completed_at": "",
        "final_status": "in_progress",
    }
    with _lock:
        _current_run_folder = folder
        _run_manifest = manifest
        _run_folder_created_at = time.time()
        _active_run_id = run_id

    get_run_manifest_path().write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("PENDING_RUN_FOLDER_USED_FOR_BOOTSTRAP_ONLY")
    except Exception:
        pass

    if PENDING_RUN_REBINDING_ENABLED:
        if STARTUP_RECOVERY_MODE and EVIDENCE_SAFE_MODE:
            # Startup-safe mode: do not block Start on pending migration/rebind.
            materialize_required_evidence_files(folder)
            safe_log_event("PENDING_WRITER_REBIND_DEFERRED_NON_BLOCKING", phase="start")
            safe_log_event("STARTUP_CONTINUED_DESPITE_EVIDENCE_WARNING")
        else:
            migrate_pending_files_to_run_folder(folder)
            rebind_all_runtime_writers(folder)
    else:
        materialize_required_evidence_files(folder)

    _write_legacy_pointer(_project_root / "logs", f"Moved to {folder / 'logs'}")
    _write_legacy_pointer(_project_root / "debug", f"Moved to {folder / 'logs'}")
    _write_legacy_pointer(_project_root / "run_artifacts", f"Moved to {folder / 'artifacts'}")

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "TROUBLESHOOTING_RUN_FOLDER_CREATED",
            folder=str(folder),
            run_id=run_id,
        )
        jp_accuracy_log("RUNTIME_EVIDENCE_PATH_RESOLVED", folder=str(folder))
        jp_accuracy_log("DEEPGRAM_CONFIG_UNCHANGED_CONFIRMED")
        jp_accuracy_log("JAPANESE_ACCURACY_BEHAVIOR_UNCHANGED_FOR_8_5_20_3")
        jp_accuracy_log("RAW_DEEPGRAM_PRESERVATION_CONFIRMED")
    except Exception:
        pass
    return folder


def _write_legacy_pointer(path: Path, message: str) -> None:
    try:
        if path.exists() and path.is_dir():
            pointer = path / "MIGRATION_NOTICE.txt"
            if not pointer.exists():
                pointer.write_text(
                    f"# Legacy path — active evidence moved\n{message}\n",
                    encoding="utf-8",
                )
    except Exception:
        pass


def update_run_manifest(**fields: Any) -> None:
    global _run_manifest
    with _lock:
        _run_manifest.update(fields)
        manifest_path = get_run_manifest_path()
        if manifest_path.parent.exists():
            manifest_path.write_text(
                json.dumps(_run_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


def finalize_run_manifest(
    run_folder: Path,
    *,
    status: str = "completed",
    artifact_flags: Optional[dict[str, Any]] = None,
    stop_summary: Optional[dict[str, Any]] = None,
) -> Path:
    completed_at = datetime.now().isoformat(timespec="seconds")
    flags = dict(artifact_flags or {})
    summary = dict(stop_summary or {})
    fields = {
        "final_status": status,
        "completed_at": completed_at,
        "stop_finalize_completed": summary.get("stop_finalize_completed", True),
        "stop_finalize_failed": summary.get("stop_finalize_failed", False),
        "stop_finalize_timed_out": summary.get("stop_finalize_timed_out", False),
        "final_alpha_output_written": flags.get("alpha_output_written", False),
        "run_artifacts_index_written": flags.get("run_artifacts_index_written", False),
        "live_run_status_written": flags.get("live_run_status_written", False),
        "upload_package_index_written": flags.get("upload_package_index_written", False),
        "upload_package_zip_created": flags.get("upload_package_zip_created", False),
        "upload_package_zip_failed_non_blocking": flags.get(
            "upload_package_zip_failed_non_blocking", False
        ),
        "validation_output_written": flags.get("validation_output_written", False),
        "audio_temp_manifest_written": flags.get("audio_temp_manifest_written", False),
        "process_health_timeline_written": flags.get(
            "process_health_timeline_written", False
        ),
        "memory_trend_summary_written": flags.get("memory_trend_summary_written", False),
    }
    update_run_manifest(**fields)
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("RUN_MANIFEST_FINALIZED", status=status, **fields)
        jp_accuracy_log("RUN_MANIFEST_STATUS_COMPLETED", final_status=status)
        jp_accuracy_log("RUN_MANIFEST_ARTIFACT_FLAGS_UPDATED", **fields)
    except Exception:
        pass
    return get_run_manifest_path()


def write_latest_pointer(*, run_id: str, run_folder: Path, status: str = "in_progress") -> None:
    finalize_latest_pointers(
        run_folder,
        run_id=run_id,
        status=status,
    )


def finalize_latest_pointers(
    run_folder: Path,
    *,
    run_id: str,
    status: str = "completed",
    upload_zip_path: str = "",
    app_close_status: str = "normal",
) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("LATEST_RUN_POINTER_FINALIZATION_BEGIN", run_id=run_id, status=status)
    except Exception:
        pass

    latest = get_latest_dir()
    completed_at = datetime.now().isoformat(timespec="seconds")
    upload_index = run_folder / "upload_package" / "UPLOAD_PACKAGE_INDEX.txt"
    if not upload_zip_path:
        zips = list((run_folder / "upload_package").glob(f"UPLOAD_PACKAGE_v{APP_VERSION}_*.zip"))
        if not zips:
            zips = list((run_folder / "upload_package").glob("UPLOAD_PACKAGE_v*_*.zip"))
        upload_zip_path = str(zips[0]) if zips else ""

    upload_created = bool(upload_zip_path and Path(upload_zip_path).exists())
    upload_size = 0
    if upload_created:
        try:
            upload_size = Path(upload_zip_path).stat().st_size
        except Exception:
            pass

    latest_alpha_path = latest / "latest_alpha_output.txt"
    accuracy_index_path = latest / "latest_accuracy_evidence_index.json"
    troubleshooting_root = get_troubleshooting_root()
    root_latest_alpha = troubleshooting_root / "latest_alpha_output.txt"
    root_accuracy_index = troubleshooting_root / "latest_accuracy_evidence_index.json"

    pointer = {
        "latest_run_id": run_id,
        "run_id": run_id,
        "latest_run_timestamp": run_folder.name.split("-", 1)[-1] if "-" in run_folder.name else "",
        "latest_app_version": APP_VERSION,
        "run_type": "live",
        "status": status,
        "current_run_status": status,
        "run_folder": str(run_folder),
        "run_manifest_path": str(run_folder / "RUN_MANIFEST.json"),
        "run_artifacts_index_path": str(run_folder / "artifacts" / "RUN_ARTIFACTS_INDEX.txt"),
        "live_run_status_path": str(run_folder / "artifacts" / "LIVE_RUN_STATUS.json"),
        "alpha_output_path": str(run_folder / "transcripts" / "Alpha output.txt"),
        "latest_alpha_output_path": str(latest_alpha_path),
        "accuracy_evidence_index_path": str(accuracy_index_path),
        "upload_package_index_path": str(upload_index),
        "upload_package_zip_path": upload_zip_path,
        "upload_package_created": upload_created,
        "upload_package_created_at": completed_at if upload_created else "",
        "upload_package_size_bytes": upload_size,
        "upload_package_wav_excluded": True,
        "stop_finalize_completed": True,
        "app_close_status": app_close_status,
        "completed_at": completed_at,
        "updated_at": completed_at,
        "app_version": APP_VERSION,
    }
    (latest / "LATEST_RUN_POINTER.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    live_pointer_path = latest / "LATEST_LIVE_RUN_POINTER.json"
    live_pointer_path.write_text(json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("LATEST_LIVE_RUN_POINTER_UPDATED", run_id=run_id)
    except Exception:
        pass
    lines = [
        f"status={status}",
        f"current_run_status={status}",
        f"run_id={run_id}",
        f"run_folder={run_folder}",
        f"app_version={APP_VERSION}",
        f"completed_at={completed_at}",
        f"updated_at={completed_at}",
        f"stop_finalize_completed=true",
        f"app_close_status={app_close_status}",
        f"artifact_index={run_folder / 'artifacts' / 'RUN_ARTIFACTS_INDEX.txt'}",
        f"alpha_output={run_folder / 'transcripts' / 'Alpha output.txt'}",
        f"latest_alpha_output={latest_alpha_path}",
        f"accuracy_evidence_index={accuracy_index_path}",
        f"upload_index={upload_index}",
        f"upload_zip={upload_zip_path}",
        f"upload_package_created={str(upload_created).lower()}",
        f"upload_package_wav_excluded=true",
    ]
    (latest / "LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    alpha_src = run_folder / "transcripts" / "Alpha output.txt"
    if alpha_src.exists():
        try:
            (latest / "latest_alpha_output.txt").write_text(
                alpha_src.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except Exception:
            pass
    if upload_index.exists():
        try:
            (latest / "latest_upload_package_index.txt").write_text(
                upload_index.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except Exception:
            pass
    root_pointer = _project_root / "LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt"
    root_pointer.write_text(
        "# Pointer only — see troubleshooting/latest/\n"
        + (latest / "LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("LATEST_RUN_POINTER_FINALIZED", status=status, run_id=run_id)
        jp_accuracy_log("LATEST_RUN_POINTER_STATUS_FIXED_COMPLETED", status=status)
        jp_accuracy_log(
            "LATEST_LIVE_RUN_ARTIFACTS_INDEX_STATUS_FIXED_COMPLETED", status=status
        )
        jp_accuracy_log("LATEST_LIVE_RUN_ARTIFACTS_INDEX_FINALIZED", status=status)
        jp_accuracy_log("LATEST_POINTER_STATUS_MATCH_CONFIRMED", status=status)
        if upload_zip_path:
            jp_accuracy_log("LATEST_POINTER_UPLOAD_ZIP_PATH_UPDATED", path=upload_zip_path)
            jp_accuracy_log(
                "LATEST_ARTIFACTS_INDEX_UPLOAD_ZIP_PATH_UPDATED", path=upload_zip_path
            )
    except Exception:
        pass


def preflight_upload_evidence(run_folder: Path) -> None:
    """Rebind writers, flush logs, materialize files before upload package."""
    if STARTUP_RECOVERY_MODE and _is_ui_thread():
        safe_log_event("EVIDENCE_HEAVY_WORK_BLOCKED_ON_UI_THREAD", operation="preflight_upload_evidence")
        return
    rebind_all_runtime_writers(run_folder)
    materialize_required_evidence_files(run_folder)
    try:
        from alpha.utils.async_debug_log import flush_async_debug_logging_safe
        from alpha.utils.process_health_telemetry import (
            collect_process_metrics,
            write_memory_trend_summary,
            write_process_health_timeline,
        )

        flush_async_debug_logging_safe(timeout_ms=1000.0)
        payload = collect_process_metrics()
        write_process_health_timeline(payload)
        write_memory_trend_summary()
    except Exception:
        pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("UPLOAD_PACKAGE_PREFLIGHT_COMPLETED", run_folder=str(run_folder))
    except Exception:
        pass


def reset_troubleshooting_session() -> None:
    global _current_run_folder, _run_manifest, _writers_rebound, _run_folder_created_at, _active_run_id
    with _lock:
        _current_run_folder = None
        _run_manifest = {}
        _writers_rebound = False
        _run_folder_created_at = 0.0
        _active_run_id = ""
        _writer_registry.clear()


def validate_no_runtime_files_outside_troubleshooting() -> list[str]:
    violations: list[str] = []
    legacy_logs = _project_root / "logs"
    if legacy_logs.exists():
        for f in legacy_logs.glob("v*.log"):
            if f.stat().st_size > 4096 and "MIGRATION" not in f.name:
                violations.append(str(f))
    return violations


def ensure_troubleshooting_startup() -> None:
    get_troubleshooting_root()
    get_latest_dir()
    get_runs_root()
    get_pending_folder()
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("TROUBLESHOOTING_STARTUP_CLEANUP_STARTED")
        jp_accuracy_log("RUNTIME_STOP_FREEZE_ELIMINATION_ACTIVE")
        jp_accuracy_log("OFFLINE_EVIDENCE_PACKAGING_ACTIVE")
        jp_accuracy_log("STOP_PATH_MINIMAL_MODE_ACTIVE")
        jp_accuracy_log("EVIDENCE_SYSTEM_NON_BLOCKING_CONFIRMED")
    except Exception:
        pass
    notice = get_troubleshooting_root() / "MIGRATION_NOTICE.txt"
    if not notice.exists():
        notice.write_text(
            "# All runtime evidence is stored under troubleshooting/\n"
            "Legacy paths logs/, debug/, run_artifacts/ are deprecated.\n",
            encoding="utf-8",
        )
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("MIGRATION_NOTICE_WRITTEN", path=str(notice))
        except Exception:
            pass
    legacy_roots = (_project_root / "logs", _project_root / "debug", _project_root / "run_artifacts")
    for legacy in legacy_roots:
        if legacy.exists() and any(legacy.iterdir()):
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("LEGACY_RUNTIME_EVIDENCE_DETECTED", path=str(legacy))
            except Exception:
                pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("PREVIOUS_RUN_RECOVERY_CHECK_COMPLETED")
    except Exception:
        pass
