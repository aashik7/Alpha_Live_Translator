"""Independent read-only verifier for Frozen Nine-Issue closure (85253327).

Must not import the cleanup executor. Verdict is calculated from disk/ZIP only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLOSURE_VERSION = "3.3.5.5.8.5.25.3.3.2.7"
AUTHORITATIVE_RUN_ID = "v3.3.5.5.8.5.25.3.3.1-20260714-111519"
AUTHORITATIVE_RUN_REL = Path("troubleshooting/runs") / AUTHORITATIVE_RUN_ID
AUTHORITATIVE_REFERENCE_REL = Path("troubleshooting/accuracy_benchmark/reference_transcripts/test01.txt")
AUTHORITATIVE_RAW_REL = AUTHORITATIVE_RUN_REL / "accuracy_stage_compare/raw_deepgram.txt"
AUTHORITATIVE_STABLE_REL = AUTHORITATIVE_RUN_REL / "accuracy_stage_compare/stable_assembler_only.txt"
AUTHORITATIVE_FINAL_REL = AUTHORITATIVE_RUN_REL / "transcripts/Alpha_output_FINAL.txt"
EXPECTED_FINAL_SHA256 = "6e70dd171862527da2f2de0305ab82154cf9c1591b73860e4fd75e06f570c178"
EXPECTED_REFERENCE_SHA256 = "09634a0da9ff86ce4825fb8326c3bca99e64be955c971d7e2db7f7b7823e5b8b"
EXPECTED_RAW_SHA256 = "2507837bcd51a7095877046c05fedb9a5ce4610a6f0488109c6ebd772ded1a38"
EXPECTED_STABLE_SHA256 = "9bf0bc100da901ffcce3dc2eb011e027828a22811014d992a2d2720f8cd6e9c5"
SKIP_DIR_NAMES = {".git", ".venv", "node_modules", ".mypy_cache", ".pytest_cache", ".cursor"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def rel_str(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def find_staging_folders(root: Path) -> list[str]:
    upload = root / AUTHORITATIVE_RUN_REL / "upload_package"
    found: list[str] = []
    if not upload.is_dir():
        return found
    for child in sorted(upload.iterdir()):
        if child.is_dir() and (child.name.lower().startswith("_staging") or child.name.lower().startswith("staging_")):
            found.append(rel_str(child, root))
    return found


def list_pending_files(root: Path) -> list[str]:
    pending = root / "troubleshooting/runs/_pending"
    if not pending.exists():
        return []
    return sorted(rel_str(f, root) for f in pending.rglob("*") if f.is_file())


def scan_file_map(root: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        base = Path(dirpath)
        for name in filenames:
            path = base / name
            try:
                if not path.is_file():
                    continue
                st = path.stat()
                key = rel_str(path, root)
                files[key] = {"sha256": sha256_file(path), "size_bytes": int(st.st_size)}
            except OSError:
                continue
    return files


def legacy_tool_dir(root: Path, version_suffix: str) -> Path | None:
    base = root / f"troubleshooting/archive/phase1_v{version_suffix}"
    for name in ("legacy_tools", "obsolete_root_tools"):
        cand = base / name
        if cand.is_dir():
            return cand
    return None


def verify(
    root: Path,
    *,
    build_id: str,
    reports_dir: Path,
    before_dir: Path,
    after_dir: Path,
) -> dict[str, Any]:
    pending_remaining = list_pending_files(root)
    staging_remaining = find_staging_folders(root)

    archive_claim_mismatches: list[str] = []
    deletion_claim_mismatches: list[str] = []
    duplicate_claim_mismatches: list[str] = []
    before_after_diff_mismatches: list[str] = []
    protected_hash_mismatches: list[str] = []

    legacy = load_json(reports_dir / "LEGACY_ARCHIVE_DEDUPLICATION.json")
    # Independently check no exact duplicate basenames remain across both archive gens with same hash.
    early = legacy_tool_dir(root, "3.3.5.5.8.5.25.3.3.2.5")
    late = legacy_tool_dir(root, "3.3.5.5.8.5.25.3.3.2.6")
    early_map = {p.name: sha256_file(p) for p in early.iterdir() if p.is_file()} if early else {}
    late_map = {p.name: sha256_file(p) for p in late.iterdir() if p.is_file()} if late else {}
    for name in set(early_map) & set(late_map):
        if early_map[name] == late_map[name]:
            archive_claim_mismatches.append(f"duplicate_archive_copy_remains:{name}")
    if int(legacy.get("newly_archived_files", -1)) != 0 and set(early_map) & set(late_map):
        # Truthful: if both gens had same files and claim says newly archived > 0, that's a mismatch.
        # Only flag when claim contradicts preexisting presence.
        if legacy.get("newly_archived_files", 0) > 0 and legacy.get("preexisting_archived_files", 0) == 0:
            archive_claim_mismatches.append("already_archived_counted_as_new")
    # Retained hashes must exist
    for detail in legacy.get("details") or []:
        canonical = detail.get("canonical")
        if canonical:
            cpath = root / canonical
            if not cpath.exists():
                archive_claim_mismatches.append(f"canonical_missing:{canonical}")
            elif detail.get("sha256") and sha256_file(cpath) != detail["sha256"]:
                archive_claim_mismatches.append(f"canonical_hash_mismatch:{canonical}")
    if legacy.get("conflicting_archive_versions"):
        archive_claim_mismatches.append("conflicting_archive_versions_present")

    pending_rep = load_json(reports_dir / "PENDING_RUN_ACTUAL_DISPOSITION.json")
    for entry in pending_rep.get("entries") or []:
        if entry.get("action") == "delete":
            p = root / entry["path"]
            if p.exists():
                deletion_claim_mismatches.append(f"pending_deleted_still_exists:{entry['path']}")
        if entry.get("action") == "archive":
            ap = entry.get("archive_path")
            if not ap or not (root / ap).exists():
                deletion_claim_mismatches.append(f"pending_archive_missing:{ap}")
            elif sha256_file(root / ap) != entry.get("sha256"):
                deletion_claim_mismatches.append(f"pending_archive_hash_mismatch:{ap}")

    staging_rep = load_json(reports_dir / "STAGING_ACTUAL_DISPOSITION.json")
    for path in staging_rep.get("staging_paths_removed") or []:
        if (root / path).exists():
            deletion_claim_mismatches.append(f"staging_removed_still_exists:{path}")

    dup_rep = load_json(reports_dir / "DUPLICATE_ACTUAL_DISPOSITION.json")
    # Recalculate duplicate groups from disk (independent).
    disk_files = scan_file_map(root)
    by_hash: dict[tuple[str, int], list[str]] = defaultdict(list)
    for path, meta in disk_files.items():
        by_hash[(meta["sha256"], int(meta["size_bytes"]))].append(path)
    independent_groups = sum(1 for paths in by_hash.values() if len(paths) >= 2)
    claimed_groups = int(dup_rep.get("duplicate_groups_evaluated") or 0)
    if claimed_groups == 0 and independent_groups > 0:
        duplicate_claim_mismatches.append("duplicate_evaluation_reported_zero_but_groups_exist")
    if claimed_groups > 0 and independent_groups == 0:
        # Possible if all duplicates were deleted; allow if removals listed.
        if not dup_rep.get("removed"):
            duplicate_claim_mismatches.append("claimed_groups_but_none_on_disk_and_no_removals")
    for rem in dup_rep.get("removed") or []:
        if (root / rem["path"]).exists():
            deletion_claim_mismatches.append(f"duplicate_deleted_still_exists:{rem['path']}")
    for ret in dup_rep.get("retained") or []:
        if ret.get("role") == "canonical":
            p = root / ret["path"]
            if not p.exists():
                duplicate_claim_mismatches.append(f"canonical_duplicate_missing:{ret['path']}")
            elif sha256_file(p) != ret.get("sha256"):
                duplicate_claim_mismatches.append(f"canonical_duplicate_hash_mismatch:{ret['path']}")
    if dup_rep.get("unclassified_duplicate_groups"):
        duplicate_claim_mismatches.append("unclassified_duplicate_groups_present")

    before_payload = load_json(before_dir / "FILES_BEFORE.json")
    after_payload = load_json(after_dir / "FILES_AFTER.json")
    before_files = before_payload.get("files") or {}
    after_files = after_payload.get("files") or {}
    # Independent recalculation from inventory maps (not counters).
    before_keys = set(before_files)
    after_keys = set(after_files)
    calc_removed = sorted(before_keys - after_keys)
    calc_added = sorted(after_keys - before_keys)
    calc_changed = sorted(
        p
        for p in (before_keys & after_keys)
        if before_files[p].get("sha256") != after_files[p].get("sha256")
    )
    claimed_diff = load_json(reports_dir / "ACTUAL_BEFORE_AFTER_DIFF.json")
    if claimed_diff.get("removed_paths") != calc_removed:
        before_after_diff_mismatches.append("removed_paths_mismatch")
    if claimed_diff.get("added_paths") != calc_added:
        before_after_diff_mismatches.append("added_paths_mismatch")
    if claimed_diff.get("changed_paths") != calc_changed:
        before_after_diff_mismatches.append("changed_paths_mismatch")
    if claimed_diff.get("files_before") != len(before_files):
        before_after_diff_mismatches.append("files_before_count_mismatch")
    if claimed_diff.get("files_after") != len(after_files):
        before_after_diff_mismatches.append("files_after_count_mismatch")

    # Protected hashes
    checks = [
        ("authoritative_reference_unchanged", AUTHORITATIVE_REFERENCE_REL, EXPECTED_REFERENCE_SHA256),
        ("raw_transcript_unchanged", AUTHORITATIVE_RAW_REL, EXPECTED_RAW_SHA256),
        ("stable_transcript_unchanged", AUTHORITATIVE_STABLE_REL, EXPECTED_STABLE_SHA256),
        ("final_transcript_unchanged", AUTHORITATIVE_FINAL_REL, EXPECTED_FINAL_SHA256),
    ]
    protected_flags: dict[str, bool] = {}
    for flag, rel, expected in checks:
        path = root / rel
        if not path.exists():
            protected_hash_mismatches.append(f"missing:{rel.as_posix()}")
            protected_flags[flag] = False
            continue
        actual = sha256_file(path)
        ok = actual == expected
        protected_flags[flag] = ok
        if not ok:
            protected_hash_mismatches.append(f"{flag}:{actual}")

    state = load_json(root / "troubleshooting/PROJECT_STATE.json")
    index = load_json(root / "troubleshooting/latest/LATEST_EVIDENCE_INDEX.json")
    metadata_current = (
        state.get("phase1_final_closure_build_id") == build_id
        and state.get("phase1_final_closure_version") == CLOSURE_VERSION
        and state.get("phase1_final_closure_status") == "PASSED"
        and index.get("current_build_id") == build_id
        and index.get("current_closure_version") == CLOSURE_VERSION
        and index.get("status") == "PASSED"
        and index.get("contradictions") == []
        and index.get("missing_required_evidence") == []
    )
    if not metadata_current:
        protected_hash_mismatches.append("metadata_build_id_stale_or_mismatch")

    verification_passed = (
        not pending_remaining
        and not staging_remaining
        and not archive_claim_mismatches
        and not deletion_claim_mismatches
        and not duplicate_claim_mismatches
        and not before_after_diff_mismatches
        and not protected_hash_mismatches
        and all(protected_flags.values())
        and metadata_current
    )

    result = {
        "build_id": build_id,
        "closure_version": CLOSURE_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pending_files_remaining": pending_remaining,
        "staging_paths_remaining": staging_remaining,
        "archive_claim_mismatches": archive_claim_mismatches,
        "deletion_claim_mismatches": deletion_claim_mismatches,
        "duplicate_claim_mismatches": duplicate_claim_mismatches,
        "before_after_diff_mismatches": before_after_diff_mismatches,
        "protected_hash_mismatches": protected_hash_mismatches,
        "authoritative_reference_unchanged": protected_flags.get("authoritative_reference_unchanged", False),
        "raw_transcript_unchanged": protected_flags.get("raw_transcript_unchanged", False),
        "stable_transcript_unchanged": protected_flags.get("stable_transcript_unchanged", False),
        "final_transcript_unchanged": protected_flags.get("final_transcript_unchanged", False),
        "final_sha256": sha256_file(root / AUTHORITATIVE_FINAL_REL) if (root / AUTHORITATIVE_FINAL_REL).exists() else None,
        "expected_final_sha256": EXPECTED_FINAL_SHA256,
        "metadata_current": metadata_current,
        "independent_duplicate_groups_on_disk": independent_groups,
        "verification_passed": verification_passed,
    }
    write_json(reports_dir / "INDEPENDENT_FILESYSTEM_VERIFICATION.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent Frozen Nine-Issue verifier (no executor import).")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--reports-dir", required=True)
    parser.add_argument("--before-dir", required=True)
    parser.add_argument("--after-dir", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    result = verify(
        root,
        build_id=args.build_id,
        reports_dir=Path(args.reports_dir),
        before_dir=Path(args.before_dir),
        after_dir=Path(args.after_dir),
    )
    print(f"verification_passed={result['verification_passed']}")
    print(f"pending_files_remaining={len(result['pending_files_remaining'])}")
    print(f"staging_paths_remaining={len(result['staging_paths_remaining'])}")
    print(f"archive_claim_mismatches={len(result['archive_claim_mismatches'])}")
    print(f"deletion_claim_mismatches={len(result['deletion_claim_mismatches'])}")
    print(f"duplicate_claim_mismatches={len(result['duplicate_claim_mismatches'])}")
    print(f"before_after_diff_mismatches={len(result['before_after_diff_mismatches'])}")
    print(f"protected_hash_mismatches={len(result['protected_hash_mismatches'])}")
    print(f"metadata_current={result['metadata_current']}")
    if not result["verification_passed"]:
        for key in (
            "pending_files_remaining",
            "staging_paths_remaining",
            "archive_claim_mismatches",
            "deletion_claim_mismatches",
            "duplicate_claim_mismatches",
            "before_after_diff_mismatches",
            "protected_hash_mismatches",
        ):
            for item in result[key]:
                print(f"FAIL:{key}:{item}")
        return 1
    print("STATUS=PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
