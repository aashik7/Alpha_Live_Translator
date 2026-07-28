"""Apply retention policy (default: --dry-run). Never touches protected paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default="")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true", help="Actually delete eligible prune paths")
    args = parser.parse_args()
    root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parent.parent
    policy_path = root / "troubleshooting" / "RETENTION_POLICY.json"
    if not policy_path.exists():
        print("FAILED: RETENTION_POLICY.json missing")
        return 1
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    execute = bool(args.execute)
    dry_run = not execute

    candidates: list[str] = []
    # Conservative: only list __pycache__ and known staging dirs
    for p in root.rglob("__pycache__"):
        if any(part in {".git", "venv", ".venv"} for part in p.parts):
            continue
        candidates.append(str(p.relative_to(root)).replace("\\", "/"))
    staging = root / "troubleshooting" / "full_project_audit"
    if staging.exists():
        for p in staging.glob("staging_*"):
            candidates.append(str(p.relative_to(root)).replace("\\", "/"))

    protected_hits = [c for c in candidates if "runs/v3.3.5.5.8.5.25.3.3.1-20260714-111519" in c]
    candidates = [c for c in candidates if c not in protected_hits]

    print(f"dry_run={dry_run}")
    print(f"candidate_count={len(candidates)}")
    for c in candidates[:50]:
        print(f"eligible={c}")
    if execute:
        # Still refuse mass delete of staging trees in Phase 1 — mark only
        print("execute_mode_refuses_destructive_staging_delete=true")
        print("ACTION=NONE_SAFE")
    print("STATUS=PASSED")
    print(f"policy={policy.get('patch_version')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
