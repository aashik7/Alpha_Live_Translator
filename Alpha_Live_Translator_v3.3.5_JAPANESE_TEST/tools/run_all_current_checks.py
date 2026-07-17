"""Run current offline checks only (no live/main)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    tools = json.loads((ROOT / "tools" / "TOOLS_CURRENT.json").read_text(encoding="utf-8"))
    checks = [
        [sys.executable, str(ROOT / "validate_runtime_environment.py")],
        [
            sys.executable,
            str(ROOT / "regression_phase1_project_normalization_85253325.py"),
            "--offline-only",
        ],
    ]
    # Scorer hard-require args: expect exit 2 without paths
    score_checks = [
        [sys.executable, str(ROOT / "score_latest_accuracy.py")],
        [sys.executable, str(ROOT / "analyze_alpha_vs_reference.py")],
        [sys.executable, str(ROOT / "score_three_stage_accuracy.py")],
    ]
    failures = 0
    for cmd in checks:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            failures += 1
            print(f"FAILED:{cmd[-1]}")
            print(proc.stdout)
            print(proc.stderr)
        else:
            print(f"PASSED:{Path(cmd[-1]).name}")

    for cmd in score_checks:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 2:
            failures += 1
            print(f"FAILED_EXPECT_EXIT_2:{Path(cmd[-1]).name}:got={proc.returncode}")
        else:
            print(f"PASSED_EXIT_2:{Path(cmd[-1]).name}")

    print(f"tools_registry_patch={tools.get('patch_version')}")
    if failures:
        print("STATUS=FAILED")
        return 1
    print("STATUS=PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
