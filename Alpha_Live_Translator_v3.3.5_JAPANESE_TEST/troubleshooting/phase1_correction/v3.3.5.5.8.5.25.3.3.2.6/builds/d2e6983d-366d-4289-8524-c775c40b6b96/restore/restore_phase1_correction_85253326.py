"""Restore only files explicitly backed up by a correction rollback manifest."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    root = Path(args.project_root).resolve()
    restored = 0
    for item in manifest.get("restore_files", []):
        source = Path(item["backup_path"])
        destination = root / item["original_path"]
        if not source.exists():
            raise RuntimeError(f"backup_missing:{source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        restored += 1
    print(f"RESTORED={restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
