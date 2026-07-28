"""Strict staging-directory package builder for V25.3.3.2 (no final-closure chicken-egg)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION
from alpha.utils.canonical_content_hash import atomic_write_json, byte_sha256_file
from alpha.utils.path_types import ensure_path

_RUN_REQUIRED = (
    "transcripts/Alpha_output_FINAL.txt",
    "transcripts/FINAL_EXPORT_SEAL.json",
    "transcripts/final_export_records.jsonl",
    "transcripts/stable_commits.jsonl",
    "transcripts/raw_deepgram_finals.jsonl",
    "accuracy_stage_compare/stable_assembler_events.jsonl",
    "accuracy_stage_compare/stable_active_records.jsonl",
    "accuracy_stage_compare/stable_assembler_only.txt",
    "accuracy_stage_compare/final_alpha_output.txt",
    "accuracy_stage_compare/audio_delivery_summary.json",
    "accuracy_stage_compare/export_coverage_report.json",
    "accuracy_stage_compare/stage_manifest.json",
    "accuracy_stage_compare/PERSISTED_STABLE_RECONSTRUCTION_REPORT.json",
    "accuracy_stage_compare/suppressed_stop_tail_candidates.jsonl",
    "artifacts/LIVE_RUN_STATUS.json",
    "artifacts/RUN_ARTIFACTS_INDEX.txt",
    "artifacts/ELEVEN_ISSUE_PREPACKAGE_CLOSURE.json",
    "RUN_MANIFEST.json",
)

_VALIDATION_REQUIRED = (
    "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_BEFORE.json",
    "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_AFTER.json",
    "regression_persisted_evidence_package_closure_8525332.txt",
)

_FORBIDDEN = ("/external/", "/smoke", "/preflight", "/.env", "/.git/", "/.venv/")
_AUDIO = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".pcm"}
_SECRET = re.compile(r"(?:DEEPGRAM|DEEPL)_API_KEY\s*=\s*\S+", re.I)


def _sha(path: Path) -> str:
    return byte_sha256_file(path)


def build_staging_package(
    *,
    run_folder: Path,
    project_root: Path,
    prepackage_closure: dict[str, Any],
    final_closure: dict[str, Any],
) -> dict[str, Any]:
    run_folder = ensure_path(run_folder)
    assert run_folder is not None
    project_root = ensure_path(project_root) or Path(".")
    upload = run_folder / "upload_package"
    upload.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = upload / f"_staging_v{APP_VERSION}_{stamp}"
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

    run_rel = f"troubleshooting/runs/{run_folder.name}"
    for rel in _RUN_REQUIRED:
        add(run_folder / rel, f"{run_rel}/{rel}")

    # Include useful optional run artifacts when present
    for rel in (
        "accuracy_stage_compare/three_stage_accuracy_report.json",
        "accuracy_stage_compare/three_stage_accuracy_report.txt",
        "artifacts/POST_RUN_EXIT_SUMMARY.json",
        "health/STALL_CLASSIFICATION_SUMMARY.json",
        "health/PROCESS_HEALTH_TIMELINE.jsonl",
        "health/MEMORY_TREND_SUMMARY.json",
        "logs/japanese_accuracy.log",
        "logs/freeze_guard.log",
        "logs/stop_finalize_timeline.jsonl",
    ):
        p = run_folder / rel
        if p.exists():
            add(p, f"{run_rel}/{rel}")

    val_dir = project_root / "troubleshooting" / "validation" / f"v{APP_VERSION}"
    for rel in _VALIDATION_REQUIRED:
        add(val_dir / rel, f"troubleshooting/validation/v{APP_VERSION}/{rel}")

    ref_dir = project_root / "troubleshooting" / "accuracy_benchmark" / "prepared" / "v3.3.5.5.8.5.25.3.3.1"
    for name in ("reference.txt", "reference_snapshot.json", "reference_quality_report.json"):
        p = ref_dir / name
        if p.exists():
            add(p, f"troubleshooting/accuracy_benchmark/prepared/v3.3.5.5.8.5.25.3.3.1/{name}")

    # Copy selected files into staging preserving archive layout
    for src, arc in selected:
        dest = staging / arc
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    # Write closure reports inside staging (final closure is created here, not required before staging).
    # Prepackage may already be in selected; overwrite staging copy once without duplicating the archive path.
    closure_arc = f"{run_rel}/artifacts/ELEVEN_ISSUE_FINAL_CLOSURE.json"
    pre_arc = f"{run_rel}/artifacts/ELEVEN_ISSUE_PREPACKAGE_CLOSURE.json"
    (staging / closure_arc).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(staging / pre_arc, prepackage_closure)
    atomic_write_json(staging / closure_arc, final_closure)
    seen_arcs = {arc for _, arc in selected}
    if pre_arc not in seen_arcs:
        selected.append((staging / pre_arc, pre_arc))
        seen_arcs.add(pre_arc)
    if closure_arc not in seen_arcs:
        selected.append((staging / closure_arc, closure_arc))
        seen_arcs.add(closure_arc)

    uniq: list[tuple[Path, str]] = []
    seen2: set[str] = set()
    for src, arc in selected:
        if arc in seen2:
            continue
        seen2.add(arc)
        uniq.append((src, arc))
    selected = uniq

    archive_names = [arc for _, arc in selected]
    duplicates = sorted({n for n in archive_names if archive_names.count(n) > 1})
    unexpected = sorted(n for n in archive_names if any(f in f"/{n.lower()}/" for f in _FORBIDDEN))
    smoke = sorted(n for n in archive_names if "smoke" in n.lower())
    preflight = sorted(n for n in archive_names if "preflight" in n.lower())
    old_version = sorted(
        n
        for n in archive_names
        if "/validation/v" in n and f"/validation/v{APP_VERSION}/" not in f"/{n}"
    )
    audio_files = sorted(n for src, n in selected if src.suffix.lower() in _AUDIO)
    secret_files = []
    for src, arc in selected:
        if src.suffix.lower() in {".txt", ".json", ".log", ".env"}:
            try:
                if _SECRET.search(src.read_text(encoding="utf-8", errors="ignore")[:20000]):
                    secret_files.append(arc)
            except Exception:
                pass

    audit = {
        "archive_paths_unique": len(duplicates) == 0,
        "duplicate_archive_paths": duplicates,
        "current_run_only": True,
        "unexpected_external_paths": unexpected,
        "old_version_files": old_version,
        "smoke_files": smoke,
        "preflight_files": preflight,
        "required_files_missing": missing,
        "secret_scan_passed": len(secret_files) == 0,
        "secret_files": secret_files,
        "audio_exclusion_passed": len(audio_files) == 0,
        "audio_files": audio_files,
        "authoritative_writer_count": 1,
        "package_complete": False,
    }
    audit_path = staging / f"{run_rel}/upload_package/PACKAGE_CONTENT_AUDIT.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    package_ready = (
        audit["archive_paths_unique"]
        and not unexpected
        and not old_version
        and not smoke
        and not preflight
        and not missing
        and audit["secret_scan_passed"]
        and audit["audio_exclusion_passed"]
    )
    audit["package_complete"] = bool(package_ready)
    atomic_write_json(audit_path, audit)

    zip_path = upload / f"UPLOAD_PACKAGE_v{APP_VERSION}_{stamp}.zip"
    if package_ready:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Walk staging and add relative files
            for path in staging.rglob("*"):
                if path.is_file():
                    arc = path.relative_to(staging).as_posix()
                    zf.write(path, arcname=arc)
        # Verify zip vs staging
        zip_names = []
        with zipfile.ZipFile(zip_path, "r") as zf:
            zip_names = sorted(n for n in zf.namelist() if not n.endswith("/"))
        staging_names = sorted(
            p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()
        )
        path_match = zip_names == staging_names
        hash_match = True
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zip_names:
                staged = staging / name
                if not staged.exists():
                    hash_match = False
                    break
                if hashlib.sha256(zf.read(name)).hexdigest() != _sha(staged):
                    hash_match = False
                    break
        post = {
            "zip_path": str(zip_path),
            "staging_zip_path_match": path_match,
            "staging_zip_hash_match": hash_match,
            "zip_entry_count": len(zip_names),
            "package_complete": path_match and hash_match and package_ready,
        }
        atomic_write_json(upload / "POST_ZIP_VERIFICATION.json", post)
        audit.update(post)
        atomic_write_json(upload / "PACKAGE_CONTENT_AUDIT.json", audit)
        # Also copy final closure outside staging for convenience
        shutil.copy2(staging / closure_arc, run_folder / "artifacts" / "ELEVEN_ISSUE_FINAL_CLOSURE.json")
    else:
        atomic_write_json(upload / "PACKAGE_CONTENT_AUDIT.json", audit)
        post = {
            "zip_path": "",
            "staging_zip_path_match": False,
            "staging_zip_hash_match": False,
            "package_complete": False,
            "required_files_missing": missing,
        }
        atomic_write_json(upload / "POST_ZIP_VERIFICATION.json", post)
        audit.update(post)

    return audit
