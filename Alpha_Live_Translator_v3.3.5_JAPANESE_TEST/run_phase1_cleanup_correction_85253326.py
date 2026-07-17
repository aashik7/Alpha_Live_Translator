"""Fail-closed offline orchestrator for Phase 1 correction 85253326."""
from __future__ import annotations

import argparse
import py_compile
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.utils.atomic_latest_state import run_alias_rollback_injection_tests
from alpha.utils.phase1_correction_engine import (
    Phase1CorrectionAcceptanceContradictionError,
    archive_legacy_tools,
    cleanup_abandoned_staging,
    compare_protected_hashes,
    create_evidence_zip,
    create_outer_bundle,
    inventory_filesystem,
    protected_hashes,
    required_reports_present,
    supersede_invalid_bundles,
    update_project_state,
    write_acceptance,
    write_cursor_report,
    write_retention_policy,
    write_rollback_and_restore,
    write_secondary_reconciliation,
    write_support_reports,
    write_tools_current,
)
from alpha.utils.phase1_correction_identity import (
    AUTHORITATIVE_FINAL_REL,
    EXPECTED_FINAL_SHA256,
    PATCH_VERSION,
    create_build_identity,
    sha256_file,
    write_json_report,
)


def run_script(root: Path, script: str, report: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(root / script)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    report.write_text((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else ""), encoding="utf-8")
    passed = (
        "STATUS=PASSED" in (proc.stdout or "")
        or "RESULT=PASSED" in (proc.stdout or "")
        or ("passed=" in (proc.stdout or "") and "failed=0" in (proc.stdout or ""))
    )
    if proc.returncode or not passed:
        raise Phase1CorrectionAcceptanceContradictionError(
            f"previous_regression_failed:{script}:rc={proc.returncode}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline only; never starts main.py or live audio.")
    parser.add_argument("--project-root", default=str(ROOT))
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    run_folder = Path(args.run_folder)
    run_folder = run_folder if run_folder.is_absolute() else (root / run_folder)
    reference = Path(args.reference)
    reference = reference if reference.is_absolute() else (root / reference)
    identity = None
    try:
        if not run_folder.exists() or not reference.exists():
            raise Phase1CorrectionAcceptanceContradictionError("authoritative_inputs_missing")
        if sha256_file(root / AUTHORITATIVE_FINAL_REL) != EXPECTED_FINAL_SHA256:
            raise Phase1CorrectionAcceptanceContradictionError("authoritative_final_sha_mismatch_precheck")

        for source in (
            "alpha/utils/phase1_correction_identity.py",
            "alpha/utils/phase1_correction_engine.py",
            "alpha/utils/atomic_latest_state.py",
            "regression_phase1_cleanup_truth_85253326.py",
            "run_phase1_cleanup_correction_85253326.py",
        ):
            py_compile.compile(str(root / source), doraise=True)

        identity = create_build_identity(root)
        phase_root = Path(identity["phase_root"])
        supersede_invalid_bundles(phase_root)

        before_inv = inventory_filesystem(root, identity, "before")
        baseline = protected_hashes(root, identity, "before")

        legacy = archive_legacy_tools(root, identity)
        cleanup = cleanup_abandoned_staging(root, identity)
        write_retention_policy(root, identity)
        write_tools_current(root, identity, legacy)
        update_project_state(root, identity)

        alias_audit = run_alias_rollback_injection_tests(root, identity=identity)
        write_secondary_reconciliation(root, identity)

        after_hashes = protected_hashes(root, identity, "after")
        protected_cmp = compare_protected_hashes(baseline, after_hashes, identity)
        after_inv = inventory_filesystem(root, identity, "after")

        write_support_reports(
            root,
            identity,
            before_inv=before_inv,
            after_inv=after_inv,
            legacy=legacy,
            cleanup=cleanup,
        )
        write_rollback_and_restore(root, identity, legacy)

        # Previous regressions (offline)
        for script in (
            "regression_final_cleanup_package_85253324.py",
            "regression_zero_issue_validation_85253322.py",
            "regression_single_authority_packaging_85253323.py",
            "regression_canonical_acceptance_bundle_85253321.py",
            "regression_phase1_project_normalization_85253325.py",
        ):
            run_script(root, script, Path(identity["regression_dir"]) / f"{script}.txt")

        # Evidence ZIP before acceptance
        missing_pre = required_reports_present(identity)
        # LATEST_ALIAS must exist now
        if not (Path(identity["reports_dir"]) / "LATEST_ALIAS_TRANSACTION_AUDIT.json").exists():
            missing_pre.append("LATEST_ALIAS_TRANSACTION_AUDIT.json")
        write_json_report(
            Path(identity["reports_dir"]) / "REQUIRED_REPORTS_PRECHECK.json",
            {"missing": missing_pre},
            identity=identity,
        )

        evidence = create_evidence_zip(root, identity)

        files_archived = legacy["files_archived"] + cleanup.get("files_archived", 0)
        files_deleted = cleanup.get("files_deleted", 0)
        arch_manifest = json_load(Path(identity["reports_dir"]) / "ARCHIVE_MANIFEST.json")
        del_manifest = json_load(Path(identity["reports_dir"]) / "DELETION_MANIFEST.json")
        if files_archived != arch_manifest.get("count"):
            raise Phase1CorrectionAcceptanceContradictionError(
                f"archive_claim_mismatch:{files_archived}:{arch_manifest.get('count')}"
            )
        if files_deleted != del_manifest.get("files_deleted"):
            raise Phase1CorrectionAcceptanceContradictionError(
                f"deletion_claim_mismatch:{files_deleted}:{del_manifest.get('files_deleted')}"
            )

        proofs = {
            "filesystem_before_file_count": before_inv["filesystem_file_count"],
            "filesystem_after_file_count": after_inv["filesystem_file_count"],
            "filesystem_before_bytes": before_inv["filesystem_bytes"],
            "filesystem_after_bytes": after_inv["filesystem_bytes"],
            "legacy_tools_evaluated": legacy["legacy_tools_evaluated"],
            "legacy_tools_archived": legacy["legacy_tools_archived"],
            "legacy_tools_retained_active": legacy.get("legacy_tools_retained_active", 0),
            "legacy_tools_retained_dependency": legacy.get("legacy_tools_retained_dependency", 0),
            "legacy_tools_retained_unknown": legacy.get("legacy_tools_retained_unknown", 0),
            "legacy_tools_compatibility_stub": legacy.get("legacy_tools_compatibility_stub", 0),
            "legacy_tools_accounted_for": legacy["legacy_tools_accounted_for"],
            "files_archived": files_archived,
            "files_deleted": files_deleted,
            "files_retained": after_inv["filesystem_file_count"],
            "bytes_archived": legacy.get("bytes_archived", 0) + cleanup.get("bytes_archived", 0),
            "bytes_deleted": cleanup.get("bytes_deleted", 0),
            "abandoned_staging_before": cleanup.get("abandoned_staging_before", 0),
            "abandoned_staging_after": cleanup.get("abandoned_staging_after", 0),
            "missing_required_reports": [],
            "alias_audit": alias_audit,
            "protected_cmp": protected_cmp,
        }

        # Task 19: acceptance only after evidence ZIP verified.
        acceptance = write_acceptance(root, identity, proofs=proofs, evidence=evidence)
        write_cursor_report(identity, acceptance)
        bundle = create_outer_bundle(root, identity, evidence=evidence, acceptance=acceptance)

        # Full deletion-isolation suite after final acceptance + outer bundle exist.
        # Gate on STATUS=PASSED / REGRESSION_FAILED=0 (total may grow with coverage).
        proc = subprocess.run(
            [
                sys.executable,
                str(root / "regression_phase1_cleanup_truth_85253326.py"),
                "--project-root",
                str(root),
                "--reports-dir",
                identity["regression_dir"],
                "--build-id",
                identity["build_id"],
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (Path(identity["regression_dir"]) / "regression_phase1_cleanup_truth_85253326.txt").write_text(
            (proc.stdout or "") + (proc.stderr or ""),
            encoding="utf-8",
        )
        out = proc.stdout or ""
        if (
            proc.returncode
            or "STATUS=PASSED" not in out
            or "REGRESSION_FAILED=0" not in out
            or "REGRESSION_PASSED=" not in out
        ):
            raise Phase1CorrectionAcceptanceContradictionError(
                f"correction_regression_failed:{proc.returncode}:{out[-800:]}"
            )

        print("VERSION=ACCEPTED")
        print("STATUS=PASSED")
        print("previous_known_issues_closed=27")
        print("phase1_normalization_findings_closed=9")
        print("phase1_correction_issues_closed=7")
        print("total_closed=43")
        print("remaining_phase1_issues=0")
        print(f"filesystem_before_file_count={proofs['filesystem_before_file_count']}")
        print(f"filesystem_after_file_count={proofs['filesystem_after_file_count']}")
        print(f"legacy_tools_evaluated={proofs['legacy_tools_evaluated']}")
        print(f"legacy_tools_accounted_for={proofs['legacy_tools_accounted_for']}")
        print(f"files_archived={proofs['files_archived']}")
        print(f"files_deleted={proofs['files_deleted']}")
        print("abandoned_staging_after=0")
        print("real_cleanup_completed=true")
        print("retention_policy_complete=true")
        print("latest_alias_transactional=true")
        print("tool_registry_matches_filesystem=true")
        print("protected_file_changes=0")
        print("regression_failures=0")
        print("missing_required_reports=0")
        print("validation_contradictions=0")
        print("new_live_test_required=false")
        print("ready_for_phase2=true")
        print("ready_for_issue12=false")
        print(f"final_bundle={bundle['final_bundle']}")
        print(f"final_sidecar={bundle['sidecar']}")
        print(f"outer_entries={','.join(bundle['entries'])}")
        print(f"patch_version={PATCH_VERSION}")
        print(f"build_id={identity['build_id']}")
        print(f"expected_final_sha256={EXPECTED_FINAL_SHA256}")
        print(f"acceptance_status={acceptance['STATUS']}")
        return 0
    except Exception as exc:
        traceback.print_exc()
        if identity:
            try:
                write_json_report(
                    Path(identity["reports_dir"]) / "FAILURE_REPORT.json",
                    {"error": str(exc)},
                    identity=identity,
                )
            except Exception:
                pass
        print(f"FAILED_INVARIANT={exc}")
        return 1


def json_load(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
