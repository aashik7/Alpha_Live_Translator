"""Restore only files explicitly backed up by a correction rollback manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    root = Path(args.project_root).resolve()
    restored = 0
    errors: list[str] = []
    for item in manifest.get("restore_files", []):
        source = Path(item["backup_path"])
        destination = root / item["original_path"]
        expected = item.get("sha256")
        if not source.exists():
            errors.append(f"backup_missing:{source}")
            continue
        actual = sha256_file(source)
        if expected and actual != expected:
            errors.append(f"backup_hash_mismatch:{source}")
            continue
        if destination.exists() and sha256_file(destination) not in {actual, expected}:
            errors.append(f"refuse_conflicting_overwrite:{destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256_file(destination) != actual:
            errors.append(f"restore_verify_failed:{destination}")
            continue
        restored += 1
    for item in manifest.get("archived_tools", []):
        archive = root / item["archive_path"]
        original = root / item["original_path"]
        expected = item.get("sha256")
        if not archive.exists():
            errors.append(f"archive_missing:{archive}")
            continue
        if expected and sha256_file(archive) != expected:
            errors.append(f"archive_hash_mismatch:{archive}")
            continue
        if original.exists() and expected and sha256_file(original) != expected:
            errors.append(f"refuse_conflicting_overwrite:{original}")
            continue
        if not original.exists():
            shutil.copy2(archive, original)
            if expected and sha256_file(original) != expected:
                errors.append(f"restore_verify_failed:{original}")
                continue
            restored += 1
    print(f"RESTORED={restored}")
    if errors:
        print("RESTORE_ERRORS=" + ";".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
