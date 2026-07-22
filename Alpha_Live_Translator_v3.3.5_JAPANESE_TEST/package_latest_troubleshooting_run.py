"""Strict current-run-only evidence packaging for V25.3.3.1."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable

from alpha.constants import APP_VERSION
from alpha.utils.latest_completed_live_run import resolve_latest_completed_live_run
from alpha.utils.path_types import ensure_path

_RUN_REQUIRED = (
    "logs/async_debug.log",
    "logs/debug.log",
    "logs/deepgram_events.jsonl",
    "logs/diagnostic_test.log",
    "logs/freeze_guard.log",
    "logs/japanese_accuracy.log",
    "logs/queue_timeline.jsonl",
    "logs/stop_finalize_timeline.jsonl",
    "logs/ui_event_bus_timeline.jsonl",
    "transcripts/Alpha output.txt",
    "transcripts/Alpha_output_FINAL.txt",
    "transcripts/FINAL_EXPORT_SEAL.json",
    "transcripts/raw_deepgram_finals.jsonl",
    "transcripts/stable_commits.jsonl",
    "transcripts/canonical_transcript_ledger.jsonl",
    "transcripts/final_export_records.jsonl",
    "accuracy_stage_compare/raw_deepgram.txt",
    "accuracy_stage_compare/raw_deepgram_events.jsonl",
    "accuracy_stage_compare/stable_assembler_only.txt",
    "accuracy_stage_compare/stable_assembler_events.jsonl",
    "accuracy_stage_compare/stable_active_records.jsonl",
    "accuracy_stage_compare/final_alpha_output.txt",
    "accuracy_stage_compare/suppressed_stop_tail_candidates.jsonl",
    "accuracy_stage_compare/deepgram_request_snapshot.json",
    "accuracy_stage_compare/audio_delivery_summary.json",
    "accuracy_stage_compare/stage_manifest.json",
    "accuracy_stage_compare/export_coverage_report.json",
    "accuracy_stage_compare/three_stage_accuracy_report.json",
    "accuracy_stage_compare/three_stage_accuracy_report.txt",
    "artifacts/LIVE_RUN_STATUS.json",
    "artifacts/RUN_ARTIFACTS_INDEX.txt",
    "artifacts/POST_RUN_EXIT_SUMMARY.json",
    "artifacts/FLIGHT_RECORDER.log",
    "artifacts/ELEVEN_ISSUE_FINAL_CLOSURE.json",
    "RUN_MANIFEST.json",
    "health/PROCESS_HEALTH_TIMELINE.jsonl",
    "health/MEMORY_TREND_SUMMARY.json",
    "health/STALL_CLASSIFICATION_SUMMARY.json",
)

_REFERENCE_REQUIRED = (
    "reference.txt",
    "reference_snapshot.json",
    "reference_quality_report.json",
)

_VALIDATION_REQUIRED = (
    "FINAL_ALPHA_WRITER_AUDIT.json",
    "regression_final_writer_stop_tail_8525331.txt",
    "validate_final_writer_stop_tail_closure_8525331.txt",
    "PRE_LIVE_GATE_REPORT.json",
    "validate_eleven_issue_closure_852533.txt",
    "regression_eleven_issue_closure_852533.txt",
)

_AUDIO_SUFFIXES = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".pcm",
}
_SECRET_NAME_PARTS = (".env", "credentials", "apikey", "api_key", "secret", "token")
_SECRET_CONTENT_PATTERNS = (
    re.compile(r"\b(?:DEEPGRAM|DEEPL|GROQ|OPENAI)_API_KEY\s*=\s*\S+", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)
_FORBIDDEN_ARCHIVE_PARTS = (
    "/external/",
    "/smoke_tests/",
    "/preflight_",
    "/clean_export_repair/",
    "/boundary_simulation/",
    "/latest/",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _embedded_run_ids(path: Path) -> set[str]:
    if path.suffix.lower() not in {".json", ".jsonl"}:
        return set()
    found: set[str] = set()
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if path.suffix.lower() == ".json":
        lines = [path.read_text(encoding="utf-8", errors="ignore")]
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        values: list[Any] = [payload]
        while values:
            current = values.pop()
            if isinstance(current, dict):
                run_id = current.get("run_id")
                if isinstance(run_id, str) and run_id:
                    found.add(run_id)
                values.extend(current.values())
            elif isinstance(current, list):
                values.extend(current)
    return found


def _contains_secret(path: Path) -> bool:
    lower_name = path.name.lower()
    if any(part in lower_name for part in _SECRET_NAME_PARTS):
        return True
    if path.stat().st_size > 2_000_000:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return any(pattern.search(text) for pattern in _SECRET_CONTENT_PATTERNS)


def _arcname(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _write_index(path: Path, audit: dict[str, Any]) -> None:
    lines = [
        f"{key}={json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}"
        for key, value in audit.items()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _collect_required(
    project_root: Path, run_folder: Path
) -> tuple[list[tuple[Path, str]], list[str]]:
    troubleshooting = project_root / "troubleshooting"
    reference_dir = (
        troubleshooting / "accuracy_benchmark" / "prepared" / f"v{APP_VERSION}"
    )
    validation_dir = troubleshooting / "validation" / f"v{APP_VERSION}"
    selected: list[tuple[Path, str]] = []
    missing: list[str] = []

    groups: Iterable[tuple[Path, Iterable[str]]] = (
        (run_folder, _RUN_REQUIRED),
        (reference_dir, _REFERENCE_REQUIRED),
        (validation_dir, _VALIDATION_REQUIRED),
    )
    for base, relative_names in groups:
        for relative_name in relative_names:
            path = base / relative_name
            if not path.is_file() or path.stat().st_size <= 0:
                missing.append(_arcname(project_root, path))
                continue
            selected.append((path, _arcname(project_root, path)))
    return selected, missing


def build_package(
    *,
    run_folder_override: str | Path | None = None,
    troubleshooting_root: str | Path | None = None,
) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parent
    troubleshooting = ensure_path(troubleshooting_root) or (
        project_root / "troubleshooting"
    )
    override = ensure_path(run_folder_override)
    if override is not None and not override.is_absolute():
        override = project_root / override

    resolution = resolve_latest_completed_live_run(
        expected_version=APP_VERSION,
        explicit_run_folder=override,
        project_root=project_root,
    )
    run_folder = ensure_path(resolution.get("resolved_run_folder"))
    if not resolution.get("ok") or run_folder is None:
        return {
            "package_complete": False,
            "error": resolution.get("error") or "run_resolution_failed",
        }

    manifest = _read_json(run_folder / "RUN_MANIFEST.json")
    run_id = str(manifest.get("run_id") or resolution.get("resolved_run_id") or "")
    upload_dir = run_folder / "upload_package"
    upload_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    zip_path = upload_dir / f"UPLOAD_PACKAGE_v{APP_VERSION}_{timestamp}.zip"
    index_path = upload_dir / "UPLOAD_PACKAGE_INDEX.txt"
    audit_path = upload_dir / "PACKAGE_CONTENT_AUDIT.json"

    selected, missing = _collect_required(project_root, run_folder)
    archive_names = [arc for _, arc in selected]
    duplicates = sorted(
        {name for name in archive_names if archive_names.count(name) > 1}
    )
    unexpected = sorted(
        name
        for name in archive_names
        if any(part in f"/{name.lower()}/" for part in _FORBIDDEN_ARCHIVE_PARTS)
    )
    smoke_files = sorted(name for name in archive_names if "smoke" in name.lower())
    preflight_files = sorted(
        name for name in archive_names if "preflight" in name.lower()
    )
    old_version_files = sorted(
        name
        for name in archive_names
        if "/validation/v" in name
        and f"/validation/v{APP_VERSION}/" not in f"/{name}"
    )
    offline_repair_core = []
    for path, arc in selected:
        if "/accuracy_stage_compare/" not in f"/{arc}":
            continue
        payload = _read_json(path)
        if payload.get("generated_by_offline_repair") or payload.get(
            "repaired_offline"
        ):
            offline_repair_core.append(arc)

    run_id_mismatches: list[str] = []
    run_prefix = _arcname(project_root, run_folder).rstrip("/") + "/"
    for path, arc in selected:
        if not arc.startswith(run_prefix):
            continue
        embedded = _embedded_run_ids(path)
        if embedded and (embedded != {run_id}):
            run_id_mismatches.append(f"{arc}:{sorted(embedded)}")

    secret_files = sorted(arc for path, arc in selected if _contains_secret(path))
    audio_files = sorted(
        arc for path, arc in selected if path.suffix.lower() in _AUDIO_SUFFIXES
    )
    validation_version_match = all(
        f"/validation/v{APP_VERSION}/" in f"/{arc}"
        for _, arc in selected
        if "/validation/" in f"/{arc}"
    )
    current_run_only = (
        not unexpected
        and not old_version_files
        and not run_id_mismatches
        and all(
            arc.startswith(run_prefix)
            or f"/accuracy_benchmark/prepared/v{APP_VERSION}/" in f"/{arc}"
            or f"/validation/v{APP_VERSION}/" in f"/{arc}"
            for _, arc in selected
        )
    )

    audit: dict[str, Any] = {
        "run_id": run_id,
        "app_version": APP_VERSION,
        "zip_path": str(zip_path),
        "zip_sha256": "",
        "file_count": len(selected),
        "archive_paths_unique": not duplicates,
        "duplicate_archive_paths": duplicates,
        "current_run_only": current_run_only,
        "unexpected_external_paths": unexpected + run_id_mismatches,
        "old_version_files": old_version_files,
        "smoke_files": smoke_files,
        "preflight_files": preflight_files,
        "offline_repair_core_artifacts": sorted(offline_repair_core),
        "required_files_missing": sorted(missing),
        "secret_scan_passed": not secret_files,
        "secret_files": secret_files,
        "audio_exclusion_passed": not audio_files,
        "audio_files": audio_files,
        "validation_version_match": validation_version_match,
        "authoritative_writer_count": 1,
        "package_complete": False,
    }
    critical_ok = (
        audit["archive_paths_unique"]
        and audit["current_run_only"]
        and not audit["unexpected_external_paths"]
        and not audit["old_version_files"]
        and not audit["smoke_files"]
        and not audit["preflight_files"]
        and not audit["offline_repair_core_artifacts"]
        and not audit["required_files_missing"]
        and audit["secret_scan_passed"]
        and audit["audio_exclusion_passed"]
        and audit["validation_version_match"]
    )
    if not critical_ok:
        audit_path.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_index(index_path, audit)
        return audit

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path, arc in sorted(selected, key=lambda item: item[1]):
            archive.write(path, arc)

    audit["zip_sha256"] = _sha256(zip_path)
    audit["package_complete"] = True
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_index(index_path, audit)
    return audit


def main(
    *,
    run_folder_override: str | Path | None = None,
    troubleshooting_root: str | Path | None = None,
) -> int:
    audit = build_package(
        run_folder_override=run_folder_override,
        troubleshooting_root=troubleshooting_root,
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if audit.get("package_complete") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", default="")
    parser.add_argument("--troubleshooting-root", default="")
    args = parser.parse_args()
    raise SystemExit(
        main(
            run_folder_override=args.run_folder or None,
            troubleshooting_root=args.troubleshooting_root or None,
        )
    )
