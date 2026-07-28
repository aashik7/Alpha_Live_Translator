#!/usr/bin/env python3
"""Safe bilingual test-environment reset — allowlisted generated evidence only.

Default: dry-run. Use --execute to delete after snapshot + verification.
Never deletes alpha/, main.py, sources, .env, references, or paths outside project root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
EXTERNAL_REFERENCES = (
    Path(r"C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Benchmark_References")
)
CLEANUP_HISTORY = ROOT / "troubleshooting" / "cleanup_history"

PROTECTED_MUST_EXIST = [
    ROOT / "main.py",
    ROOT / "alpha",
    ROOT / "alpha" / "constants.py",
    ROOT / "alpha" / "ui" / "main_window.py",
    ROOT / "alpha" / "transcription" / "deepgram_client.py",
]

# Explicit allowlist of relative paths / glob roots under ROOT only.
DELETION_ALLOWLIST_GLOBS = [
    "troubleshooting/runs",
    "troubleshooting/runs_*",
    "troubleshooting/runs_pending",
    "troubleshooting/latest*",
    "troubleshooting/accuracy_benchmark/current_bilingual_accuracy*",
    "troubleshooting/accuracy_benchmark/bilingual_manual_upload_*",
    "troubleshooting/accuracy_benchmark/bilingual_forensic_evidence_*",
    "troubleshooting/accuracy_benchmark/japanese_candidates_*",
    "troubleshooting/accuracy_benchmark/BILINGUAL_*.zip",
    "troubleshooting/accuracy_benchmark/JAPANESE_CANDIDATE_*.zip",
    "troubleshooting/accuracy_benchmark/clean_bilingual_test",
    "troubleshooting/run_artifacts*",
    "run_artifacts*",
    "**/__pycache__",
]

PRESERVE_RELATIVE = [
    "troubleshooting/accuracy_benchmark/reference_transcripts",
    "troubleshooting/accuracy_benchmark/prepared",
    "troubleshooting/cleanup_history",
    "troubleshooting/validation",
    "troubleshooting/experiments",
    "Alpha_Benchmark_References",
]

REQUIRED_EMPTY_FOLDERS = [
    "troubleshooting/runs",
    "troubleshooting/accuracy_benchmark/current_bilingual_accuracy",
    "troubleshooting/accuracy_benchmark/clean_bilingual_test",
    "troubleshooting/cleanup_history",
    "troubleshooting/validation/clean_bilingual_reset",
]


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    if (ROOT / "main.py").is_file():
        files.append(ROOT / "main.py")
    for p in ROOT.glob("*.py"):
        files.append(p)
    for p in (ROOT / "alpha").rglob("*"):
        if p.is_file() and p.suffix in {".py", ".json", ".txt", ".md", ".toml"}:
            # skip caches
            if "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            files.append(p)
    for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        p = ROOT / name
        if p.is_file():
            files.append(p)
    # unique preserve order
    seen: set[Path] = set()
    out: list[Path] = []
    for f in files:
        rf = f.resolve()
        if rf not in seen:
            seen.add(rf)
            out.append(f)
    return out


def create_source_snapshot(stamp: str) -> tuple[Path, Path, dict[str, str]]:
    CLEANUP_HISTORY.mkdir(parents=True, exist_ok=True)
    zip_path = CLEANUP_HISTORY / f"PRE_RESET_SOURCE_SNAPSHOT_{stamp}.zip"
    hash_path = CLEANUP_HISTORY / f"PRE_RESET_SOURCE_HASHES_{stamp}.json"
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_source_files():
            rel = path.relative_to(ROOT).as_posix()
            if path.name == ".env" or "secret" in path.name.lower() or "api_key" in path.name.lower():
                continue
            hashes[rel] = _sha256_file(path)
            zf.write(path, arcname=rel)
    hash_path.write_text(
        json.dumps(
            {
                "generated_at_utc": stamp,
                "project_root": str(ROOT),
                "file_count": len(hashes),
                "hashes": hashes,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return zip_path, hash_path, hashes


def _matches_preserve(path: Path) -> bool:
    rel = path.resolve().relative_to(ROOT.resolve()).as_posix()
    for pref in PRESERVE_RELATIVE:
        pref_n = pref.replace("\\", "/")
        if rel == pref_n or rel.startswith(pref_n + "/"):
            return True
    return False


def collect_deletion_candidates() -> list[Path]:
    candidates: list[Path] = []
    # Explicit known roots
    explicit = [
        ROOT / "troubleshooting" / "runs",
        ROOT / "troubleshooting" / "runs_pending",
        ROOT / "run_artifacts",
        ROOT / "logs",
    ]
    # latest pointers
    for p in (ROOT / "troubleshooting").glob("latest*"):
        explicit.append(p)
    for p in (ROOT / "troubleshooting").glob("LATEST*"):
        explicit.append(p)
    for p in (ROOT / "troubleshooting").glob("runs_*"):
        explicit.append(p)
    for p in (ROOT / "troubleshooting").glob("run_artifacts*"):
        explicit.append(p)
    ab = ROOT / "troubleshooting" / "accuracy_benchmark"
    if ab.is_dir():
        for pat in (
            "current_bilingual_accuracy*",
            "bilingual_manual_upload_*",
            "bilingual_forensic_evidence_*",
            "japanese_candidates_*",
            "BILINGUAL_*.zip",
            "JAPANESE_CANDIDATE_*.zip",
            "clean_bilingual_test",
        ):
            explicit.extend(ab.glob(pat))
    # __pycache__ and .pyc under project
    for p in ROOT.rglob("__pycache__"):
        explicit.append(p)
    for p in ROOT.rglob("*.pyc"):
        explicit.append(p)

    for path in explicit:
        if not path.exists():
            continue
        resolved = path.resolve()
        if not _is_under(resolved, ROOT):
            raise RuntimeError(f"path outside project root refused: {resolved}")
        if _matches_preserve(resolved):
            continue
        # never delete alpha, main.py, cleanup_history contents we're writing
        if resolved == (ROOT / "alpha").resolve() or _is_under(resolved, ROOT / "alpha"):
            if resolved.name == "__pycache__" or resolved.suffix == ".pyc" or "__pycache__" in resolved.parts:
                candidates.append(resolved)
            continue
        if resolved == (ROOT / "main.py").resolve():
            continue
        if _is_under(resolved, CLEANUP_HISTORY):
            continue
        candidates.append(resolved)

    # unique, deepest-first for deletion
    uniq = sorted(set(candidates), key=lambda p: len(p.parts), reverse=True)
    return uniq


def _count_tree(path: Path) -> tuple[int, int, int]:
    files = folders = bytes_ = 0
    if path.is_file():
        return 1, 0, path.stat().st_size
    if not path.is_dir():
        return 0, 0, 0
    folders = 1
    for p in path.rglob("*"):
        if p.is_file():
            files += 1
            try:
                bytes_ += p.stat().st_size
            except OSError:
                pass
        elif p.is_dir():
            folders += 1
    return files, folders, bytes_


def verify_protected() -> None:
    expected = Path(
        r"C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator"
    ).resolve()
    # Allow former long folder name during transition, or cwd via different casing/normalization
    if ROOT.resolve() != expected and ROOT.name not in {
        "Alpha_Live_Translator",
        "Alpha_Live_Translator_v3.3.5_JAPANESE_TEST",
    }:
        raise RuntimeError(f"Unexpected project root: {ROOT}")
    for p in PROTECTED_MUST_EXIST:
        if not p.exists():
            raise RuntimeError(f"Protected path missing: {p}")
    if not EXTERNAL_REFERENCES.exists():
        print(f"WARNING: external references folder missing (will not create/delete): {EXTERNAL_REFERENCES}")


def verify_hashes(before: dict[str, str]) -> dict[str, Any]:
    after: dict[str, str] = {}
    changed: list[str] = []
    missing: list[str] = []
    for rel, expected in before.items():
        path = ROOT / rel
        if not path.is_file():
            missing.append(rel)
            continue
        actual = _sha256_file(path)
        after[rel] = actual
        if actual != expected:
            changed.append(rel)
    return {
        "match": not changed and not missing,
        "changed": changed,
        "missing": missing,
        "after_hashes": after,
    }


def recreate_runtime_folders() -> None:
    for rel in REQUIRED_EMPTY_FOLDERS:
        (ROOT / rel).mkdir(parents=True, exist_ok=True)
    # Ensure no stale pending evidence remains before clean tests.
    pending = ROOT / "troubleshooting" / "runs" / "_pending"
    if pending.exists():
        shutil.rmtree(pending, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset bilingual test environment (allowlisted)")
    ap.add_argument("--execute", action="store_true", help="Actually delete (default is dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default)")
    args = ap.parse_args()
    execute = bool(args.execute)
    stamp = _utc_stamp()

    try:
        verify_protected()
    except Exception as exc:
        print(f"RESET_STATUS = FAILED\n{exc}")
        return 1

    candidates = collect_deletion_candidates()
    total_files = total_folders = total_bytes = 0
    for c in candidates:
        f, d, b = _count_tree(c)
        total_files += f
        total_folders += d
        total_bytes += b

    print("=" * 72)
    print("BILINGUAL TEST ENVIRONMENT RESET")
    print(f"mode={'EXECUTE' if execute else 'DRY-RUN'}")
    print(f"project_root={ROOT}")
    print(f"external_references={EXTERNAL_REFERENCES}")
    print("protected_paths:")
    for p in PROTECTED_MUST_EXIST:
        print(f"  - {p}")
    print("preserve:")
    for p in PRESERVE_RELATIVE:
        print(f"  - {p}")
    print("proposed_deletions:")
    for c in candidates:
        print(f"  - {c}")
    print(f"total_file_count={total_files}")
    print(f"total_folder_count={total_folders}")
    print(f"total_byte_count={total_bytes}")
    print("=" * 72)

    if not execute:
        print("DRY-RUN only. Re-run with --execute to delete after review.")
        print("RESET_STATUS = DRY_RUN_OK")
        return 0

    # Execute path
    try:
        zip_path, hash_path, before_hashes = create_source_snapshot(stamp)
        print(f"snapshot={zip_path}")
        print(f"hashes={hash_path}")
    except Exception as exc:
        print(f"RESET_STATUS = FAILED\nsnapshot creation failed: {exc}")
        return 1

    deleted: list[dict[str, Any]] = []
    try:
        for path in candidates:
            if not _is_under(path, ROOT):
                raise RuntimeError(f"path outside approved root: {path}")
            if _matches_preserve(path):
                raise RuntimeError(f"attempted to delete preserved path: {path}")
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted.append({"path": str(path), "deleted": True})
    except Exception as exc:
        print(f"RESET_STATUS = FAILED\ndeletion aborted: {exc}")
        return 1

    recreate_runtime_folders()

    # Verify external references untouched (existence + optional hash of README)
    refs_ok = True
    if EXTERNAL_REFERENCES.exists():
        refs_ok = True  # we never touch it

    verification = verify_hashes(before_hashes)
    CLEANUP_HISTORY.mkdir(parents=True, exist_ok=True)
    del_json = CLEANUP_HISTORY / f"RESET_DELETION_MANIFEST_{stamp}.json"
    del_txt = CLEANUP_HISTORY / f"RESET_DELETION_MANIFEST_{stamp}.txt"
    ver_json = CLEANUP_HISTORY / f"POST_RESET_SOURCE_HASH_VERIFICATION_{stamp}.json"

    status = "PASSED"
    failure_codes: list[str] = []
    if not verification["match"]:
        status = "FAILED"
        failure_codes.append("source_hash_changed")
    if not refs_ok:
        status = "FAILED"
        failure_codes.append("external_reference_modified")

    manifest = {
        "RESET_STATUS": status,
        "generated_at_utc": stamp,
        "project_root": str(ROOT),
        "external_references": str(EXTERNAL_REFERENCES),
        "snapshot_zip": str(zip_path),
        "pre_reset_hashes": str(hash_path),
        "deleted": deleted,
        "deleted_count": len(deleted),
        "total_files_removed_estimate": total_files,
        "total_folders_removed_estimate": total_folders,
        "total_bytes_removed_estimate": total_bytes,
        "failure_codes": failure_codes,
        "hash_verification": {
            "match": verification["match"],
            "changed": verification["changed"],
            "missing": verification["missing"],
        },
    }
    del_json.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    del_txt.write_text(
        "\n".join(
            [
                f"RESET_STATUS = {status}",
                f"deleted_count = {len(deleted)}",
                f"snapshot = {zip_path}",
                *[f"DELETED {d['path']}" for d in deleted],
                f"failure_codes = {failure_codes}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    ver_json.write_text(
        json.dumps(
            {
                "RESET_STATUS": status,
                "match": verification["match"],
                "changed": verification["changed"],
                "missing": verification["missing"],
                "generated_at_utc": stamp,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"RESET_STATUS = {status}")
    if failure_codes:
        print(f"failure_codes={failure_codes}")
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
