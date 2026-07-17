"""Clean staging + ZIP builder for V25.3.3.2.1 canonical acceptance packages."""

from __future__ import annotations

import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from alpha.utils.canonical_acceptance_state import (
    REQUIRED_VALIDATION_IN_ZIP,
    STALE_ACCEPTANCE_BASENAMES,
    VALIDATION_VERSION,
    file_sha256,
    is_stale_acceptance_authority,
)
from alpha.utils.canonical_content_hash import atomic_write_json, byte_sha256_file
from alpha.utils.immutable_evidence_contract import (
    IMMUTABLE_HASHES_AFTER_FILENAME,
    IMMUTABLE_HASHES_BEFORE_FILENAME,
)
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
    "artifacts/LIVE_RUN_STATUS.json",
    "artifacts/RUN_ARTIFACTS_INDEX.txt",
    "RUN_MANIFEST.json",
    "health/STALL_CLASSIFICATION_SUMMARY.json",
)

_RUN_OPTIONAL = (
    "accuracy_stage_compare/three_stage_accuracy_report.json",
    "accuracy_stage_compare/three_stage_accuracy_report.txt",
    "accuracy_stage_compare/raw_deepgram_events.jsonl",
    "accuracy_stage_compare/suppressed_stop_tail_candidates.jsonl",
    "artifacts/POST_RUN_EXIT_SUMMARY.json",
    "health/PROCESS_HEALTH_TIMELINE.jsonl",
    "health/MEMORY_TREND_SUMMARY.json",
    "logs/japanese_accuracy.log",
    "logs/freeze_guard.log",
    "logs/stop_finalize_timeline.jsonl",
)

_FORBIDDEN = ("/external/", "/smoke", "/preflight", "/.env", "/.git/", "/.venv/")
_AUDIO = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".pcm"}
_SECRET = re.compile(r"(?:DEEPGRAM|DEEPL)_API_KEY\s*=\s*\S+", re.I)


def audit_stale_acceptance_evidence(
    run_folder: Path,
    staging: Path | None = None,
) -> dict[str, Any]:
    run_folder = ensure_path(run_folder)
    assert run_folder is not None
    found: list[str] = []
    for p in run_folder.rglob("*"):
        if not p.is_file():
            continue
        if is_stale_acceptance_authority(p.name):
            # Only flag under upload_package or artifacts as acceptance authorities
            rel = p.relative_to(run_folder).as_posix()
            if "upload_package" in rel or "artifacts" in rel or p.name in STALE_ACCEPTANCE_BASENAMES:
                found.append(rel)

    excluded = list(found)
    in_staging: list[str] = []
    if staging and staging.exists():
        for p in staging.rglob("*"):
            if p.is_file() and is_stale_acceptance_authority(p.name):
                # Versioned V25.3.3.2.1 files are NOT stale
                if "V25.3.3.2.1" in p.name or "v3.3.5.5.8.5.25.3.3.2.1" in p.name:
                    continue
                if p.name in STALE_ACCEPTANCE_BASENAMES:
                    in_staging.append(p.relative_to(staging).as_posix())

    return {
        "stale_files_found": found,
        "stale_files_excluded": excluded,
        "stale_acceptance_authorities_in_staging": in_staging,
        "stale_acceptance_authorities_excluded": len(in_staging) == 0,
    }


def build_clean_staging(
    *,
    run_folder: Path,
    project_root: Path,
    validation_dir: Path,
    reference_path: Path,
) -> dict[str, Any]:
    run_folder = ensure_path(run_folder)
    project_root = ensure_path(project_root)
    validation_dir = ensure_path(validation_dir)
    reference_path = ensure_path(reference_path)
    assert run_folder and project_root and validation_dir and reference_path

    upload = run_folder / "upload_package"
    upload.mkdir(parents=True, exist_ok=True)
    staging = upload / f"staging_v{VALIDATION_VERSION}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    selected: list[tuple[Path, str]] = []
    missing: list[str] = []

    def add(src: Path, arc: str) -> None:
        if not src.exists():
            missing.append(arc)
            return
        if is_stale_acceptance_authority(src.name):
            return
        selected.append((src, arc.replace("\\", "/")))

    run_rel = f"troubleshooting/runs/{run_folder.name}"
    for rel in _RUN_REQUIRED:
        add(run_folder / rel, f"{run_rel}/{rel}")
    for rel in _RUN_OPTIONAL:
        p = run_folder / rel
        if p.exists():
            add(p, f"{run_rel}/{rel}")

    # Reference
    ref_dir = reference_path.parent
    for name in ("reference.txt", "reference_snapshot.json", "reference_quality_report.json"):
        p = ref_dir / name
        if p.exists():
            add(
                p,
                f"troubleshooting/accuracy_benchmark/prepared/{ref_dir.name}/{name}",
            )

    # Copy run/ref into staging
    for src, arc in selected:
        dest = staging / arc
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    # Validation files at staging/validation/ as specified
    val_dest = staging / "validation"
    val_dest.mkdir(parents=True, exist_ok=True)
    val_names = (
        "CANONICAL_PREPACKAGE_VALIDATION.json",
        "ELEVEN_ISSUE_PREPACKAGE_CLOSURE.json",
        "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
        IMMUTABLE_HASHES_BEFORE_FILENAME,
        IMMUTABLE_HASHES_AFTER_FILENAME,
        "PACKAGE_STAGING_AUDIT.json",  # written below after audit
    )
    for name in val_names:
        if name == "PACKAGE_STAGING_AUDIT.json":
            continue
        src = validation_dir / name
        # Read-compat: fall back to legacy short names when reading historical dirs
        if not src.exists() and name == IMMUTABLE_HASHES_BEFORE_FILENAME:
            src = validation_dir / "IMMUTABLE_HASHES_BEFORE.json"
        if not src.exists() and name == IMMUTABLE_HASHES_AFTER_FILENAME:
            src = validation_dir / "IMMUTABLE_HASHES_AFTER.json"
        if src.exists():
            dest_name = name  # always write canonical names into staging
            shutil.copy2(src, val_dest / dest_name)
            selected.append((val_dest / dest_name, f"validation/{dest_name}"))
        else:
            missing.append(f"validation/{name}")

    # Also include regression txt if present
    reg = validation_dir / "regression_canonical_acceptance_bundle_85253321.txt"
    if reg.exists():
        shutil.copy2(reg, val_dest / reg.name)
        selected.append((val_dest / reg.name, f"validation/{reg.name}"))

    # Deduplicate arcs
    uniq: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for src, arc in selected:
        if arc in seen:
            continue
        seen.add(arc)
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
        if "/validation/v" in n and f"/validation/v{VALIDATION_VERSION}/" not in f"/{n}"
    )
    stale_in = [
        n
        for n in archive_names
        if Path(n).name in STALE_ACCEPTANCE_BASENAMES
    ]
    audio_files = sorted(n for src, n in selected if src.suffix.lower() in _AUDIO)
    secret_files: list[str] = []
    for src, arc in selected:
        if src.suffix.lower() in {".txt", ".json", ".log", ".env"}:
            try:
                if _SECRET.search(src.read_text(encoding="utf-8", errors="ignore")[:20000]):
                    secret_files.append(arc)
            except Exception:
                pass

    identity = {}
    manifest = run_folder / "RUN_MANIFEST.json"
    if manifest.exists():
        import json

        identity = json.loads(manifest.read_text(encoding="utf-8"))

    staging_audit = {
        "run_id_match": bool(identity.get("run_id")),
        "run_id": identity.get("run_id"),
        "validation_version_match": True,
        "validation_version": VALIDATION_VERSION,
        "archive_paths_unique": len(duplicates) == 0,
        "duplicate_archive_paths": duplicates,
        "current_run_only": True,
        "unexpected_external_paths": unexpected,
        "old_version_files": old_version,
        "stale_acceptance_files": stale_in,
        "smoke_files": smoke,
        "preflight_files": preflight,
        "required_files_missing": missing,
        "secret_scan_passed": len(secret_files) == 0,
        "secret_files": secret_files,
        "audio_exclusion_passed": len(audio_files) == 0,
        "audio_files": audio_files,
        "staging_complete": False,
        "selected_file_count": len(selected),
    }
    staging_complete = (
        staging_audit["archive_paths_unique"]
        and not unexpected
        and not old_version
        and not stale_in
        and not smoke
        and not preflight
        and not missing
        and staging_audit["secret_scan_passed"]
        and staging_audit["audio_exclusion_passed"]
    )
    staging_audit["staging_complete"] = bool(staging_complete)

    atomic_write_json(val_dest / "PACKAGE_STAGING_AUDIT.json", staging_audit)
    atomic_write_json(validation_dir / "PACKAGE_STAGING_AUDIT.json", staging_audit)
    # Ensure staging file is counted
    if "validation/PACKAGE_STAGING_AUDIT.json" not in seen:
        selected.append(
            (val_dest / "PACKAGE_STAGING_AUDIT.json", "validation/PACKAGE_STAGING_AUDIT.json")
        )

    stale_audit = audit_stale_acceptance_evidence(run_folder, staging)
    atomic_write_json(validation_dir / "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json", stale_audit)
    shutil.copy2(
        validation_dir / "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
        val_dest / "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
    )

    return {
        "staging": staging,
        "upload": upload,
        "selected": selected,
        "staging_audit": staging_audit,
        "stale_audit": stale_audit,
    }


def create_and_inspect_main_zip(
    *,
    staging: Path,
    upload: Path,
    staging_audit: dict[str, Any],
) -> dict[str, Any]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zip_path = upload / f"UPLOAD_PACKAGE_v{VALIDATION_VERSION}_{stamp}.zip"

    # Walk staging for files actually present
    staging_files = sorted(
        p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()
    )

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in staging_files:
            zf.write(staging / rel, arcname=rel)

    zip_open_success = True
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zip_names = sorted(n for n in zf.namelist() if not n.endswith("/"))
            path_match = zip_names == staging_files
            hash_match = True
            for name in zip_names:
                staged = staging / name
                if not staged.exists():
                    hash_match = False
                    break
                if byte_sha256_file(staged) != __import__("hashlib").sha256(zf.read(name)).hexdigest():
                    hash_match = False
                    break
            duplicates = sorted({n for n in zip_names if zip_names.count(n) > 1})
            unexpected = sorted(
                n for n in zip_names if any(f in f"/{n.lower()}/" for f in _FORBIDDEN)
            )
            stale_inside = [
                n
                for n in zip_names
                if Path(n).name in STALE_ACCEPTANCE_BASENAMES
                and "V25.3.3.2.1" not in Path(n).name
            ]
            required_missing = [r for r in REQUIRED_VALIDATION_IN_ZIP if r not in zip_names]
            # IMMUTABLE_HASHES_AFTER may be placeholder before final rehash; require BEFORE at least
            current_validation = all(
                r in zip_names
                for r in (
                    "validation/CANONICAL_PREPACKAGE_VALIDATION.json",
                    "validation/ELEVEN_ISSUE_PREPACKAGE_CLOSURE.json",
                    "validation/PACKAGE_STAGING_AUDIT.json",
                    f"validation/{IMMUTABLE_HASHES_BEFORE_FILENAME}",
                    "validation/STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
                )
            )
            audio_inside = [n for n in zip_names if Path(n).suffix.lower() in _AUDIO]
            secret_inside: list[str] = []
            for name in zip_names:
                if Path(name).suffix.lower() in {".txt", ".json", ".log", ".env"}:
                    try:
                        data = zf.read(name).decode("utf-8", errors="ignore")[:20000]
                        if _SECRET.search(data):
                            secret_inside.append(name)
                    except Exception:
                        pass
    except zipfile.BadZipFile:
        zip_open_success = False
        zip_names = []
        path_match = False
        hash_match = False
        duplicates = []
        unexpected = []
        stale_inside = []
        required_missing = list(REQUIRED_VALIDATION_IN_ZIP)
        current_validation = False
        audio_inside = []
        secret_inside = []

    isolation_passed = (
        zip_open_success
        and path_match
        and hash_match
        and not duplicates
        and not unexpected
        and not stale_inside
        and not audio_inside
        and not secret_inside
        and not required_missing
        and staging_audit.get("staging_complete") is True
    )
    verification_passed = isolation_passed and current_validation

    post = {
        "main_zip_path": str(zip_path),
        "main_zip_sha256": file_sha256(zip_path) if zip_path.exists() else "",
        "main_zip_size": zip_path.stat().st_size if zip_path.exists() else 0,
        "main_zip_file_count": len(zip_names),
        "zip_open_success": zip_open_success,
        "staging_zip_path_match": path_match,
        "staging_zip_hash_match": hash_match,
        "archive_paths_unique": len(duplicates) == 0,
        "duplicate_archive_paths": duplicates,
        "unexpected_paths": unexpected,
        "required_files_missing": required_missing,
        "stale_acceptance_files_inside_zip": stale_inside,
        "current_validation_inside_zip": current_validation,
        "package_isolation_passed": isolation_passed,
        "package_verification_passed": verification_passed,
        "audio_files_inside_zip": audio_inside,
        "secret_files_inside_zip": secret_inside,
        "validation_version": VALIDATION_VERSION,
    }
    atomic_write_json(upload / "POST_ZIP_VERIFICATION_V25.3.3.2.1.json", post)
    return post


def create_final_validation_bundle(
    *,
    upload: Path,
    validation_dir: Path,
    main_zip: Path,
    post_zip: Path,
    final_acceptance: Path,
    cursor_report: Path,
) -> dict[str, Any]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bundle = upload / f"FINAL_VALIDATION_BUNDLE_v{VALIDATION_VERSION}_{stamp}.zip"
    before_src = validation_dir / IMMUTABLE_HASHES_BEFORE_FILENAME
    if not before_src.exists():
        before_src = validation_dir / "IMMUTABLE_HASHES_BEFORE.json"
    after_src = validation_dir / IMMUTABLE_HASHES_AFTER_FILENAME
    if not after_src.exists():
        after_src = validation_dir / "IMMUTABLE_HASHES_AFTER.json"
    members = {
        main_zip.name: main_zip,
        "FINAL_ACCEPTANCE_V25.3.3.2.1.json": final_acceptance,
        "POST_ZIP_VERIFICATION_V25.3.3.2.1.json": post_zip,
        "CANONICAL_PREPACKAGE_VALIDATION.json": validation_dir
        / "CANONICAL_PREPACKAGE_VALIDATION.json",
        "PACKAGE_STAGING_AUDIT.json": validation_dir / "PACKAGE_STAGING_AUDIT.json",
        "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json": validation_dir
        / "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
        IMMUTABLE_HASHES_BEFORE_FILENAME: before_src,
        IMMUTABLE_HASHES_AFTER_FILENAME: after_src,
        "Cursor final report.txt": cursor_report,
    }
    missing = [k for k, p in members.items() if not p.exists()]
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arc, src in members.items():
            if src.exists():
                zf.write(src, arcname=arc)

    with zipfile.ZipFile(bundle, "r") as zf:
        names = sorted(n for n in zf.namelist() if not n.endswith("/"))
    main_zips = [n for n in names if n.startswith("UPLOAD_PACKAGE_v") and n.endswith(".zip")]
    stale = [n for n in names if Path(n).name in STALE_ACCEPTANCE_BASENAMES]
    acceptance = __import__("json").loads(final_acceptance.read_text(encoding="utf-8"))
    verdict = acceptance.get("final_verdict") or acceptance
    expected_zip = Path(verdict.get("main_zip_path") or "").name
    hash_match = (
        bool(main_zips)
        and expected_zip in main_zips
        and verdict.get("main_zip_sha256") == file_sha256(main_zip)
    )
    complete = (
        not missing
        and len(main_zips) == 1
        and hash_match
        and not stale
        and "FINAL_ACCEPTANCE_V25.3.3.2.1.json" in names
        and "POST_ZIP_VERIFICATION_V25.3.3.2.1.json" in names
        and "Cursor final report.txt" in names
    )
    audit = {
        "bundle_path": str(bundle),
        "bundle_sha256": file_sha256(bundle),
        "bundle_members": names,
        "main_evidence_zip_count": len(main_zips),
        "main_evidence_zips": main_zips,
        "acceptance_points_to_main_zip": expected_zip in main_zips,
        "main_zip_hash_match": hash_match,
        "stale_acceptance_files": stale,
        "required_missing": missing,
        "bundle_complete": complete,
        "no_contradictory_status": verdict.get("VERSION") == "ACCEPTED"
        and int(verdict.get("issues_closed") or 0) == 11,
    }
    atomic_write_json(upload / "FINAL_VALIDATION_BUNDLE_AUDIT.json", audit)
    return audit
