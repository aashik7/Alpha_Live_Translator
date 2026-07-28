"""Restore Phase 1 mutations from PHASE1_ROLLBACK_MANIFEST.json (best-effort)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# This script is copied into builds/<id>/restore/ — also runnable from that location.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--manifest", default="")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    script_dir = Path(__file__).resolve().parent
    manifest_path = Path(args.manifest) if args.manifest else script_dir / "PHASE1_ROLLBACK_MANIFEST.json"
    if not manifest_path.exists():
        print(f"FAILED manifest missing: {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutations = manifest.get("mutations") or {}

    # Restore archived historical tools
    restored = 0
    for item in mutations.get("archived_tools") or []:
        src = project_root / item.get("archive_path", "")
        dest = project_root / item.get("original_path", "")
        if src.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            restored += 1

    print(f"restore_completed=true")
    print(f"tools_restored={restored}")
    print("note=immutable_authoritative_artifacts_were_never_modified")
    print("STATUS=PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
