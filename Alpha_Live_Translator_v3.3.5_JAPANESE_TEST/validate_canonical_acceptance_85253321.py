"""Validate issues 1–8 and 11 from actual run artifacts (V25.3.3.2.1)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from alpha.utils.canonical_acceptance_state import (
    build_canonical_acceptance_state,
    hash_immutable_artifacts,
    write_prepackage_closure,
)
from alpha.utils.canonical_content_hash import atomic_write_json
from alpha.utils.immutable_evidence_contract import IMMUTABLE_HASHES_BEFORE_FILENAME
from alpha.utils.path_types import ensure_path

ROOT = Path(__file__).resolve().parent
VALIDATION_VERSION = "3.3.5.5.8.5.25.3.3.2.1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()

    run_folder = ensure_path(args.run_folder)
    assert run_folder is not None
    if not run_folder.is_absolute():
        run_folder = ROOT / run_folder
    ref = ensure_path(args.reference)
    assert ref is not None
    if not ref.is_absolute():
        ref = ROOT / ref

    val_dir = ROOT / "troubleshooting" / "validation" / f"v{VALIDATION_VERSION}"
    val_dir.mkdir(parents=True, exist_ok=True)

    before_path = val_dir / IMMUTABLE_HASHES_BEFORE_FILENAME
    legacy = val_dir / "IMMUTABLE_HASHES_BEFORE.json"
    if before_path.exists():
        before = json.loads(before_path.read_text(encoding="utf-8"))
    elif legacy.exists():
        before = json.loads(legacy.read_text(encoding="utf-8"))
        atomic_write_json(before_path, before)
    else:
        before = hash_immutable_artifacts(run_folder)
        atomic_write_json(before_path, before)

    state = build_canonical_acceptance_state(
        run_folder=run_folder,
        reference_path=ref,
        immutable_before=before,
        immutable_after=before,
        pending_package=True,
    )

    atomic_write_json(val_dir / "CANONICAL_PREPACKAGE_VALIDATION.json", state)
    write_prepackage_closure(val_dir / "ELEVEN_ISSUE_PREPACKAGE_CLOSURE.json", state)

    ic = state["issue_closure"]
    ok_core = all(
        ic["issue_results"].get(k) is True
        for k in (
            "final_content_loss_closed",
            "raw_lineage_closed",
            "action_counter_mismatch_closed",
            "finalizer_crash_closed",
            "runtime_audio_counters_closed",
            "stop_drain_closed",
            "false_coverage_closed",
            "stage_completion_truthful",
            "stall_classification_closed",
        )
    )
    pending_ok = (
        ic["issue_results"].get("package_isolation_closed") == "pending_package_verification"
        and ic["issue_results"].get("current_validation_packaged")
        == "pending_package_verification"
    )
    print(
        json.dumps(
            {
                "VERSION": state["VERSION"],
                "issues_closed": ic["issues_closed"],
                "issues_total": ic["issues_total"],
                "closure_ratio": ic["closure_ratio"],
                "package_pending_issues": ic.get("package_pending_issues"),
                "core_issues_passed": ok_core,
                "package_issues_pending": pending_ok,
                "failures": state["final_verdict"]["failures"],
            },
            indent=2,
        )
    )
    return 0 if ok_core and pending_ok and ic["issues_closed"] == 9 else 1


if __name__ == "__main__":
    sys.exit(main())
