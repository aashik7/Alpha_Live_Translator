"""Package staging for zero-issue audit (V25.3.3.2.2)."""

from __future__ import annotations

import hashlib
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from alpha.utils.canonical_content_hash import atomic_write_json, byte_sha256_file
from alpha.utils.immutable_evidence_contract import (
    IMMUTABLE_HASHES_AFTER_FILENAME,
    IMMUTABLE_HASHES_BEFORE_FILENAME,
)
from alpha.utils.path_types import ensure_path
from alpha.utils.validation_version import VALIDATION_PATCH_VERSION

_RUN_REQUIRED = (
    "transcripts/Alpha_output_FINAL.txt",
    "transcripts/FINAL_EXPORT_SEAL.json",
    "transcripts/final_export_records.jsonl",
    "transcripts/stable_commits.jsonl",
    "transcripts/stable_commits_normalized.jsonl",
    "transcripts/STABLE_COMMIT_LINEAGE_RECONCILIATION.json",
    "transcripts/raw_deepgram_finals.jsonl",
    "accuracy_stage_compare/stable_assembler_events.jsonl",
    "accuracy_stage_compare/stable_active_records.jsonl",
    "accuracy_stage_compare/audio_delivery_summary.json",
    "artifacts/LIVE_RUN_STATUS.json",
    "artifacts/FINAL_STATUS_RECONCILIATION.json",
    "artifacts/POST_RUN_EXIT_SUMMARY.json",
    "RUN_MANIFEST.json",
    "health/STALL_CLASSIFICATION_SUMMARY.json",
    "health/PROCESS_HEALTH_TIMELINE.jsonl",
    "health/MEMORY_TREND_SUMMARY.json",
    "logs/stop_finalize_timeline.jsonl",
    "logs/stop_finalize_timeline_reconciled.jsonl",
    "logs/STOP_TIMELINE_RECONCILIATION_REPORT.json",
    "logs/FINALIZER_EVENT_RECONCILIATION.json",
    "logs/japanese_accuracy.log",
)

_FORBIDDEN = ("/external/", "/smoke", "/preflight", "/.env", "/.git/", "/.venv/")


def build_zero_issue_staging(
    *,
    run_folder: Path,
    project_root: Path,
    validation_dir: Path,
    prepared_reference_dir: Path,
) -> dict[str, Any]:
    run_folder = ensure_path(run_folder)
    project_root = ensure_path(project_root)
    validation_dir = ensure_path(validation_dir)
    prepared_reference_dir = ensure_path(prepared_reference_dir)
    assert run_folder and project_root and validation_dir and prepared_reference_dir

    audit_root = (
        project_root
        / "troubleshooting"
        / "post_acceptance_audit"
        / f"v{VALIDATION_PATCH_VERSION}"
    )
    audit_root.mkdir(parents=True, exist_ok=True)
    staging = audit_root / f"_staging_{VALIDATION_PATCH_VERSION}_{time.strftime('%Y%m%d-%H%M%S')}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    selected: list[tuple[Path, str]] = []
    missing: list[str] = []

    def add(src: Path, arc: str) -> None:
        if not src.exists():
            missing.append(arc)
            return
        selected.append((src, arc.replace("\\", "/")))

    run_rel = f"run/{run_folder.name}"
    for rel in _RUN_REQUIRED:
        add(run_folder / rel, f"{run_rel}/{rel}")

    for name in ("reference.txt", "reference_snapshot.json", "reference_quality_report.json"):
        add(prepared_reference_dir / name, f"reference/{name}")

    for src in validation_dir.glob("*"):
        if src.is_file():
            add(src, f"validation/{src.name}")

    for src, arc in selected:
        dest = staging / arc
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    archive_names = [a for _, a in selected]
    duplicates = sorted({n for n in archive_names if archive_names.count(n) > 1})
    unexpected = sorted(
        n for n in archive_names if any(f in f"/{n.lower()}/" for f in _FORBIDDEN)
    )
    foreign_runs = sorted(
        n
        for n in archive_names
        if n.startswith("run/") and not n.startswith(f"run/{run_folder.name}/")
    )

    audit = {
        "staging_complete": len(missing) == 0 and not duplicates and not unexpected and not foreign_runs,
        "missing_required": missing,
        "duplicate_paths": duplicates,
        "unexpected_paths": unexpected,
        "foreign_run_paths": foreign_runs,
        "entry_count": len(archive_names),
        "staging_path": str(staging),
        "validation_patch_version": VALIDATION_PATCH_VERSION,
        "immutable_before_present": (staging / "validation" / IMMUTABLE_HASHES_BEFORE_FILENAME).exists(),
        "immutable_after_present": (staging / "validation" / IMMUTABLE_HASHES_AFTER_FILENAME).exists(),
    }
    atomic_write_json(staging / "validation" / "PACKAGE_STAGING_AUDIT.json", audit)
    atomic_write_json(validation_dir / "PACKAGE_STAGING_AUDIT.json", audit)
    return {
        "staging": staging,
        "audit_root": audit_root,
        "staging_audit": audit,
        "selected": selected,
    }


def create_evidence_zip(staging: Path, dest_zip: Path) -> dict[str, Any]:
    staging = Path(staging)
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in staging.rglob("*"):
            if path.is_file():
                arc = path.relative_to(staging).as_posix()
                zf.write(path, arc)
    # reopen verify
    with zipfile.ZipFile(dest_zip, "r") as zf:
        names = zf.namelist()
        bad = zf.testzip()
    return {
        "zip_path": str(dest_zip),
        "sha256": byte_sha256_file(dest_zip),
        "entry_count": len(names),
        "testzip_ok": bad is None,
        "names": names,
    }


def create_outer_audit_bundle(
    *,
    audit_root: Path,
    files: dict[str, Path],
) -> dict[str, Any]:
    audit_root = Path(audit_root)
    audit_root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    bundle = (
        audit_root
        / f"FINAL_ZERO_ISSUE_AUDIT_BUNDLE_v{VALIDATION_PATCH_VERSION}_{ts}.zip"
    )
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arc, path in files.items():
            p = Path(path)
            if p.exists():
                zf.write(p, arc.replace("\\", "/"))
    with zipfile.ZipFile(bundle, "r") as zf:
        names = set(zf.namelist())
        bad = zf.testzip()
    required = {
        "ZERO_ISSUE_FINAL_ACCEPTANCE.json",
        "Cursor final report.txt",
    }
    missing = sorted(required - names)
    return {
        "bundle_path": str(bundle),
        "sha256": byte_sha256_file(bundle),
        "entry_count": len(names),
        "testzip_ok": bad is None,
        "missing_required": missing,
        "bundle_complete": bad is None and not missing,
        "names": sorted(names),
    }
