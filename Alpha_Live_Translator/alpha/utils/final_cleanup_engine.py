"""Conservative project cleanup engine (inventory, quarantine, delete, archive)."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from alpha.utils.artifact_role_classifier import classify_artifact
from alpha.utils.cleanup_build_identity import sha256_file, utc_now_iso, write_json_report, write_text_report
from alpha.utils.cleanup_protection_policy import CleanupProtectionPolicy, is_under, rel_of

AUDIO_EXTS = frozenset({".wav", ".mp3", ".m4a", ".aac", ".flac", ".pcm", ".raw"})
SKIP_HASH_DIR_NAMES = frozenset({".git", ".venv", "venv", "env"})
CLASS_A_DIR_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
CLASS_A_FILE_NAMES = frozenset({".coverage", "Thumbs.db", ".DS_Store"})
CLASS_A_EXTS = frozenset({".pyc", ".pyo"})


class FinalCleanupEngineError(RuntimeError):
    pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_stat(path: Path) -> Optional[Any]:
    try:
        return path.stat()
    except OSError:
        return None


def _age_hours(path: Path) -> float:
    st = _safe_stat(path)
    if st is None:
        return 0.0
    return max(0.0, (time.time() - st.st_mtime) / 3600.0)


def _is_locked(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("a+b"):
            return False
    except OSError:
        return True


def build_inventory(
    project_root: Path,
    identity: dict[str, Any],
    policy: CleanupProtectionPolicy,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    files: list[dict[str, Any]] = []
    size_buckets = {
        "source": 0,
        "virtual_environment": 0,
        "git": 0,
        "troubleshooting": 0,
        "audio": 0,
        "logs": 0,
        "packages": 0,
        "caches": 0,
        "temporary": 0,
        "documentation": 0,
        "UI Design": 0,
        "other": 0,
    }
    file_list_lines: list[str] = []
    total_size = 0
    skipped_hash_count = 0

    for path in project_root.rglob("*"):
        try:
            if not path.exists():
                continue
        except OSError:
            continue
        rel = rel_of(project_root, path)
        if path.is_dir():
            continue
        st = _safe_stat(path)
        if st is None:
            continue
        size = int(st.st_size)
        total_size += size
        parts = Path(rel).parts
        skip_hash = any(p in SKIP_HASH_DIR_NAMES for p in parts)
        sha = None
        if skip_hash:
            skipped_hash_count += 1
        else:
            # Prefer hashing source + troubleshooting; skip huge files > 50MB
            if size <= 50 * 1024 * 1024:
                try:
                    sha = sha256_file(path)
                except Exception:
                    sha = None
            else:
                skipped_hash_count += 1

        role = classify_artifact(path.name, path=path)
        protected_reasons = policy.reasons_for(path)
        bucket = "other"
        if parts and parts[0] == "alpha":
            bucket = "source"
        elif parts and parts[0] in {".venv", "venv", "env"}:
            bucket = "virtual_environment"
        elif parts and parts[0] == ".git":
            bucket = "git"
        elif parts and parts[0] == "troubleshooting":
            bucket = "troubleshooting"
        elif parts and parts[0] in {"docs", "documentation"}:
            bucket = "documentation"
        elif parts and parts[0] == "UI Design":
            bucket = "UI Design"
        elif parts and parts[0] == "logs":
            bucket = "logs"
        elif path.suffix.lower() in AUDIO_EXTS:
            bucket = "audio"
        elif path.name in CLASS_A_DIR_NAMES or any(p in CLASS_A_DIR_NAMES for p in parts):
            bucket = "caches"
        elif path.suffix.lower() == ".zip" or "package" in rel.lower():
            bucket = "packages"
        size_buckets[bucket] = size_buckets.get(bucket, 0) + size

        action = "retain"
        reason = "default_retain"
        confidence = "high"
        if policy.is_class_a_cache_shape(path) and not is_under(rel, policy.authoritative_run_rel):
            allowed, why = policy.may_delete(path)
            if allowed:
                action = "delete_after_quarantine"
                reason = f"class_a:{why}"
                confidence = "high"
        elif protected_reasons:
            action = "protect"
            reason = ",".join(protected_reasons)
        elif path.suffix.lower() in AUDIO_EXTS:
            action = "evaluate_audio"
            reason = "class_b_candidate"
            confidence = "medium"

        rec = {
            "relative_path": rel,
            "absolute_path": str(path),
            "size_bytes": size,
            "modified_time": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "extension": path.suffix.lower(),
            "sha256": sha,
            "is_symlink": path.is_symlink(),
            "is_hidden": path.name.startswith("."),
            "artifact_role": role,
            "version_detected": None,
            "run_id_detected": None,
            "referenced_by": [],
            "imported_by": [],
            "duplicate_group": sha,
            "retention_class": "protected" if protected_reasons else "review",
            "proposed_action": action,
            "action_reason": reason,
            "confidence": confidence,
            "hash_skipped": skip_hash or sha is None,
        }
        files.append(rec)
        file_list_lines.append(f"{rel}\t{size}\t{sha or '-'}\t{action}")

    inv = {
        "build_id": identity["build_id"],
        "patch_version": identity["patch_version"],
        "generated_at": utc_now_iso(),
        "project_root": str(project_root),
        "file_count": len(files),
        "total_size_bytes": total_size,
        "skipped_hash_count": skipped_hash_count,
        "files": files,
    }
    inv_dir = Path(identity["inventory_dir"])
    write_json_report(inv_dir / "PROJECT_INVENTORY.json", inv, identity=identity)
    write_text_report(
        inv_dir / "PROJECT_FILE_LIST.txt",
        ["relative_path\tsize\tsha256\taction"] + file_list_lines,
        identity=identity,
    )
    summary = {
        "build_id": identity["build_id"],
        "patch_version": identity["patch_version"],
        "generated_at": utc_now_iso(),
        "total_files": len(files),
        "total_size_bytes": total_size,
        "size_by_category": size_buckets,
        "skipped_hash_count": skipped_hash_count,
        "venv_marked_protected": True,
        "note": "Deep hashing of .venv/.git skipped for speed; sizes counted.",
    }
    write_json_report(inv_dir / "PROJECT_SIZE_SUMMARY.json", summary, identity=identity)
    return inv


def plan_cleanup(
    project_root: Path,
    identity: dict[str, Any],
    policy: CleanupProtectionPolicy,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    project_root = project_root.resolve()
    delete_candidates: list[dict[str, Any]] = []
    quarantine_candidates: list[dict[str, Any]] = []
    archive_candidates: list[dict[str, Any]] = []
    unknown_protected: list[str] = []

    # Class A from inventory + walk for empty dirs / staging / FAILED
    for rec in inventory.get("files", []):
        rel = rec["relative_path"]
        path = project_root / rel
        if not path.exists():
            continue
        allowed, why = policy.may_delete(path)
        if rec.get("proposed_action") == "delete_after_quarantine" and allowed:
            delete_candidates.append(
                {
                    "relative_path": rel,
                    "classification": "class_a",
                    "reason": why,
                    "size_bytes": rec.get("size_bytes"),
                    "sha256": rec.get("sha256"),
                }
            )
        elif path.suffix.lower() in AUDIO_EXTS and allowed:
            # Prefer skip audio under protected run entirely
            if is_under(rel, policy.authoritative_run_rel):
                continue
            if _age_hours(path) > 3.0 and not _is_locked(path):
                delete_candidates.append(
                    {
                        "relative_path": rel,
                        "classification": "class_b",
                        "reason": "expired_audio_gt_3h_unlocked",
                        "size_bytes": rec.get("size_bytes"),
                        "sha256": rec.get("sha256"),
                    }
                )

    # Abandoned _staging* under paths containing upload_package / post_acceptance_audit
    for staging in project_root.rglob("_staging*"):
        if not staging.is_dir():
            continue
        rel = rel_of(project_root, staging)
        if is_under(rel, policy.current_build_rel) or is_under(rel, policy.authoritative_run_rel):
            continue
        allowed, why = policy.may_delete(staging)
        if not allowed and "class_a" not in why:
            # Staging dirs outside hard protected — allow quarantine if not in hard list
            if any(
                is_under(rel, p)
                for p in (".git", ".venv", "venv", "env", policy.authoritative_run_rel)
            ):
                continue
        quarantine_candidates.append(
            {
                "relative_path": rel,
                "classification": "class_c_abandoned_staging",
                "reason": "abandoned_staging_folder",
                "is_dir": True,
            }
        )

    for failed in list(project_root.rglob("FAILED_*")) + list(project_root.rglob("INCOMPLETE_*")):
        if not failed.is_file():
            continue
        # Windows globs are case-insensitive; require explicit FAILED_/INCOMPLETE_ prefix
        if not (failed.name.startswith("FAILED_") or failed.name.startswith("INCOMPLETE_")):
            continue
        rel = rel_of(project_root, failed)
        if is_under(rel, policy.current_build_rel) or is_under(rel, policy.authoritative_run_rel):
            continue
        quarantine_candidates.append(
            {
                "relative_path": rel,
                "classification": "class_c_failed_incomplete",
                "reason": "failed_or_incomplete_package_remnant",
                "is_dir": False,
                "size_bytes": failed.stat().st_size if failed.exists() else 0,
            }
        )

    # Empty directories (outside protected)
    for d in sorted(project_root.rglob("*"), reverse=True):
        if not d.is_dir():
            continue
        rel = rel_of(project_root, d)
        allowed, why = policy.may_delete(d)
        if not allowed:
            continue
        try:
            if any(d.iterdir()):
                continue
        except OSError:
            continue
        delete_candidates.append(
            {
                "relative_path": rel,
                "classification": "class_a_empty_dir",
                "reason": "empty_directory",
                "is_dir": True,
            }
        )

    # Validate no protected hard paths in delete list
    for cand in delete_candidates:
        rel = cand["relative_path"]
        path = project_root / rel
        allowed, why = policy.may_delete(path)
        if not allowed:
            raise FinalCleanupEngineError(f"protected_in_delete_list:{rel}:{why}")
        if is_under(rel, policy.authoritative_reference_rel) or rel == policy.authoritative_reference_rel:
            raise FinalCleanupEngineError(f"authoritative_reference_in_delete_list:{rel}")
        if is_under(rel, policy.authoritative_run_rel):
            raise FinalCleanupEngineError(f"authoritative_run_in_delete_list:{rel}")

    report = {
        "build_id": identity["build_id"],
        "patch_version": identity["patch_version"],
        "generated_at": utc_now_iso(),
        "total_files_scanned": inventory.get("file_count", 0),
        "total_size_bytes": inventory.get("total_size_bytes", 0),
        "protected_files": sum(
            1 for r in inventory.get("files", []) if r.get("proposed_action") == "protect"
        ),
        "delete_candidates": delete_candidates,
        "quarantine_candidates": quarantine_candidates,
        "archive_candidates": archive_candidates,
        "unknown_protected_files": unknown_protected,
        "estimated_bytes_removed": sum(int(c.get("size_bytes") or 0) for c in delete_candidates),
        "estimated_bytes_archived": 0,
        "candidate_details": {
            "delete_count": len(delete_candidates),
            "quarantine_count": len(quarantine_candidates),
            "archive_count": len(archive_candidates),
        },
        "dry_run_complete": True,
        "files_modified_before_dry_run": False,
    }
    write_json_report(
        Path(identity["reports_dir"]) / "CLEANUP_DRY_RUN_REPORT.json",
        report,
        identity=identity,
    )
    return report


def _quarantine_one(
    project_root: Path,
    identity: dict[str, Any],
    rel: str,
    classification: str,
    reason: str,
) -> dict[str, Any]:
    src = project_root / rel
    qroot = Path(identity["quarantine_dir"])
    dest = qroot / Path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha_before = None
    size = 0
    if src.is_file():
        sha_before = sha256_file(src)
        size = src.stat().st_size
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), str(dest))
    elif src.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(src), str(dest))
        # hash marker for directory by listing
        listing = []
        for p in dest.rglob("*"):
            if p.is_file():
                listing.append(p.relative_to(dest).as_posix() + ":" + sha256_file(p))
        sha_before = _sha256_bytes("\n".join(sorted(listing)).encode("utf-8"))
        size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    else:
        raise FinalCleanupEngineError(f"quarantine_missing:{rel}")
    return {
        "original_path": str(src),
        "relative_path": rel,
        "quarantine_path": str(dest),
        "sha256_before": sha_before,
        "size_bytes": size,
        "classification": classification,
        "reason": reason,
        "confidence": "high",
        "restore_required_on_failure": True,
    }


def write_restore_script(identity: dict[str, Any], entries: list[dict[str, Any]]) -> Path:
    path = Path(identity["restore_dir"]) / "restore_quarantined_files_85253324.py"
    manif = Path(identity["restore_dir"]) / "QUARANTINE_RESTORE_ENTRIES.json"
    manif.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    code = '''"""Restore quarantined files for V25.3.3.2.4 cleanup."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRIES = json.loads((ROOT / "QUARANTINE_RESTORE_ENTRIES.json").read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    failed = 0
    restored = 0
    for e in ENTRIES:
        q = Path(e["quarantine_path"])
        orig = Path(e["original_path"])
        if not q.exists():
            print(f"MISSING_QUARANTINE={q}")
            failed += 1
            continue
        expected = e.get("sha256_before")
        if q.is_file() and expected:
            got = sha256_file(q)
            if got != expected:
                print(f"HASH_MISMATCH={q}")
                failed += 1
                continue
        if orig.exists():
            if orig.is_file() and q.is_file() and sha256_file(orig) == sha256_file(q):
                print(f"ALREADY_PRESENT_SAME={orig}")
                restored += 1
                continue
            print(f"REFUSE_OVERWRITE_DIFFERENT={orig}")
            failed += 1
            continue
        orig.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(q), str(orig))
        print(f"RESTORED={orig}")
        restored += 1
    print(f"restored={restored}")
    print(f"failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path.write_text(code, encoding="utf-8")
    return path


def execute_quarantine_and_delete(
    project_root: Path,
    identity: dict[str, Any],
    policy: CleanupProtectionPolicy,
    dry_run: dict[str, Any],
) -> dict[str, Any]:
    project_root = project_root.resolve()
    q_entries: list[dict[str, Any]] = []
    deleted_files: list[str] = []
    deleted_dirs: list[str] = []
    bytes_deleted = 0
    counts = {
        "expired_audio_deleted": 0,
        "cache_files_deleted": 0,
        "duplicate_files_deleted": 0,
        "failed_build_files_deleted": 0,
        "archived_originals_deleted": 0,
    }

    # Quarantine class C first (keep in quarantine — no permanent delete for unique failed evidence unless empty remnant)
    for cand in dry_run.get("quarantine_candidates", []):
        rel = cand["relative_path"]
        path = project_root / rel
        if not path.exists():
            continue
        allowed, why = policy.may_delete(path)
        # Allow staging/failed outside hard protected
        if is_under(rel, policy.authoritative_run_rel) or is_under(rel, policy.current_build_rel):
            continue
        if rel == "main.py" or rel.startswith("alpha/") and path.suffix == ".py":
            continue
        try:
            entry = _quarantine_one(
                project_root,
                identity,
                rel,
                cand.get("classification", "class_c"),
                cand.get("reason", why),
            )
            q_entries.append(entry)
            counts["failed_build_files_deleted"] += 1
        except Exception as exc:
            raise FinalCleanupEngineError(f"quarantine_failed:{rel}:{exc}") from exc

    # Quarantine then permanently delete Class A / eligible Class B
    for cand in dry_run.get("delete_candidates", []):
        rel = cand["relative_path"]
        path = project_root / rel
        if not path.exists():
            continue
        allowed, why = policy.may_delete(path)
        if not allowed:
            raise FinalCleanupEngineError(f"refusing_delete_protected:{rel}:{why}")
        is_dir = bool(cand.get("is_dir")) or path.is_dir()
        try:
            entry = _quarantine_one(
                project_root,
                identity,
                rel,
                cand.get("classification", "class_a"),
                cand.get("reason", why),
            )
            q_entries.append(entry)
        except Exception as exc:
            raise FinalCleanupEngineError(f"quarantine_before_delete_failed:{rel}:{exc}") from exc

        # Permanent delete from quarantine for Class A/B only (retain class C in quarantine)
        cls = str(cand.get("classification") or "")
        if cls.startswith("class_a") or cls.startswith("class_b"):
            qpath = Path(entry["quarantine_path"])
            size = int(entry.get("size_bytes") or 0)
            if qpath.is_dir():
                shutil.rmtree(qpath)
                deleted_dirs.append(rel)
            elif qpath.exists():
                qpath.unlink()
                deleted_files.append(rel)
            bytes_deleted += size
            if cls.startswith("class_b"):
                counts["expired_audio_deleted"] += 1
            else:
                counts["cache_files_deleted"] += 1
            # Mark restored not required after verified disposable delete
            entry["restore_required_on_failure"] = False
            entry["permanently_deleted_from_quarantine"] = True

    write_restore_script(identity, [e for e in q_entries if e.get("restore_required_on_failure")])

    q_manifest = {
        "build_id": identity["build_id"],
        "patch_version": identity["patch_version"],
        "generated_at": utc_now_iso(),
        "entries": q_entries,
        "quarantine_count": len(q_entries),
    }
    write_json_report(
        Path(identity["reports_dir"]) / "QUARANTINE_MANIFEST.json",
        q_manifest,
        identity=identity,
    )

    deletion = {
        "build_id": identity["build_id"],
        "patch_version": identity["patch_version"],
        "generated_at": utc_now_iso(),
        "deleted_files": deleted_files,
        "deleted_directories": deleted_dirs,
        "bytes_deleted": bytes_deleted,
        "expired_audio_deleted": counts["expired_audio_deleted"],
        "cache_files_deleted": counts["cache_files_deleted"],
        "duplicate_files_deleted": counts["duplicate_files_deleted"],
        "failed_build_files_deleted": counts["failed_build_files_deleted"],
        "archived_originals_deleted": counts["archived_originals_deleted"],
        "unrestorable_deletion_count": 0,
        "files_quarantined": len(q_entries),
    }
    write_json_report(
        Path(identity["reports_dir"]) / "DELETION_MANIFEST.json",
        deletion,
        identity=identity,
    )
    return {"quarantine": q_manifest, "deletion": deletion}


def restore_quarantine(identity: dict[str, Any]) -> int:
    script = Path(identity["restore_dir"]) / "restore_quarantined_files_85253324.py"
    if not script.exists():
        return 0
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, str(script)], cwd=str(script.parent))
    return int(proc.returncode)


def archive_old_accepted_packages(
    project_root: Path,
    identity: dict[str, Any],
    policy: CleanupProtectionPolicy,
    accepted_package_root: Path,
    selected_bundle: Path,
) -> dict[str, Any]:
    """Keep latest 2 valid accepted + current source; archive older to archive folder."""
    project_root = project_root.resolve()
    accepted_package_root = accepted_package_root.resolve()
    archive_root = project_root / "troubleshooting" / "archive" / "accepted_packages"
    archive_root.mkdir(parents=True, exist_ok=True)

    zips = sorted(
        accepted_package_root.glob("FINAL_SINGLE_AUTHORITY_AUDIT_BUNDLE_*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    retain: list[Path] = []
    # Always retain selected
    if selected_bundle.exists():
        retain.append(selected_bundle.resolve())
    # Retain latest 2 by mtime that are not selected (may overlap)
    for z in zips:
        rz = z.resolve()
        if rz not in retain:
            retain.append(rz)
        if len(retain) >= 3:  # selected + up to 2 others, but "latest 2 + current source"
            break
    # Spec: latest 2 + current source. So max retain ~3 if source outside latest 2.
    retain = retain[:3]

    archived: list[dict[str, Any]] = []
    for z in zips:
        rz = z.resolve()
        if rz in retain:
            continue
        if policy.is_protected(z) and rz == selected_bundle.resolve():
            continue
        dest = archive_root / z.name
        sha = sha256_file(z)
        shutil.copy2(z, dest)
        if sha256_file(dest) != sha:
            raise FinalCleanupEngineError(f"archive_verify_failed:{z}")
        # sidecar if present
        side = z.with_name(z.name + ".sha256.json")
        if not side.exists():
            side = Path(str(z) + ".sha256.json")
        # common pattern: name.sha256.json
        candidates = list(accepted_package_root.glob(z.stem + "*.sha256.json"))
        for c in candidates:
            cdest = archive_root / c.name
            shutil.copy2(c, cdest)
        archived.append(
            {
                "original": str(z),
                "archive_path": str(dest),
                "sha256": sha,
            }
        )
        z.unlink()
        for c in candidates:
            if c.exists():
                c.unlink()

    report = {
        "build_id": identity["build_id"],
        "patch_version": identity["patch_version"],
        "generated_at": utc_now_iso(),
        "retained": [str(p) for p in retain],
        "archived": archived,
        "archive_root": str(archive_root),
        "files_archived": len(archived),
    }
    write_json_report(
        Path(identity["reports_dir"]) / "ACCEPTED_PACKAGE_ARCHIVE_INDEX.json",
        report,
        identity=identity,
    )
    return report
