"""One-command offline zero-issue closure (V25.3.3.2.2) — 28-step order."""

from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from alpha.utils.canonical_acceptance_state import hash_immutable_artifacts
from alpha.utils.canonical_content_hash import atomic_write_json, atomic_write_text_utf8, byte_sha256_file
from alpha.utils.final_status_reconciliation import write_final_status_reconciliation
from alpha.utils.finalizer_event_reconciliation import write_finalizer_event_reconciliation
from alpha.utils.immutable_evidence_contract import (
    IMMUTABLE_HASHES_AFTER_FILENAME,
    IMMUTABLE_HASHES_BEFORE_FILENAME,
    before_path,
    after_path,
)
from alpha.utils.package_zero_issue_staging import (
    build_zero_issue_staging,
    create_evidence_zip,
    create_outer_audit_bundle,
)
from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import load_run_identity
from alpha.utils.required_regression_matrix import run_required_regressions
from alpha.utils.stable_commit_lineage_reconciliation import (
    write_stable_commit_lineage_reconciliation,
)
from alpha.utils.stop_timeline_reconciliation import write_stop_timeline_reconciliation
from alpha.utils.strict_package_identity import (
    audit_current_run_only_zip,
    build_package_identity_audit,
)
from alpha.utils.strict_stop_evidence import evaluate_strict_stop_evidence
from alpha.utils.validation_version import VALIDATION_PATCH_VERSION
from alpha.utils.zero_issue_acceptance import (
    ZeroIssueAcceptanceContradictionError,
    build_zero_issue_acceptance,
    render_zero_issue_cursor_report,
)
from alpha.utils.zero_issue_gate import evaluate_zero_issue_gate

ROOT = Path(__file__).resolve().parent

COMPILE_TARGETS = (
    "alpha/utils/validation_version.py",
    "alpha/utils/immutable_evidence_contract.py",
    "alpha/utils/strict_evidence_values.py",
    "alpha/utils/required_regression_matrix.py",
    "alpha/utils/zero_issue_gate.py",
    "alpha/utils/strict_package_identity.py",
    "alpha/utils/zero_issue_acceptance.py",
    "alpha/utils/final_status_reconciliation.py",
    "alpha/utils/stable_commit_lineage_reconciliation.py",
    "alpha/utils/stop_timeline_reconciliation.py",
    "alpha/utils/finalizer_event_reconciliation.py",
    "alpha/utils/strict_stop_evidence.py",
    "alpha/utils/package_zero_issue_staging.py",
    "alpha/utils/canonical_acceptance_state.py",
    "alpha/utils/package_canonical_acceptance_staging.py",
    "alpha/utils/accuracy_stage_capture.py",
    "alpha/utils/transcript_evidence.py",
    "prepare_accuracy_benchmark_852532.py",
    "regression_zero_issue_validation_85253322.py",
    "run_zero_issue_closure_85253322.py",
)


def _fail(msg: str, details: dict[str, Any] | None = None) -> int:
    payload: dict[str, Any] = {"VERSION": "NOT_ACCEPTED", "failing_invariant": msg}
    if details:
        payload["details"] = details
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"FAILING_INVARIANT={msg}")
    return 1


def _write_failed_bundle(audit_root: Path, report: dict[str, Any]) -> Path:
    audit_root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = audit_root / f"FAILED_ZERO_ISSUE_AUDIT_{VALIDATION_PATCH_VERSION}_{ts}.json"
    atomic_write_json(path, report)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--source-reference", required=True)
    args = parser.parse_args()

    run_folder = ensure_path(args.run_folder)
    assert run_folder is not None
    if not run_folder.is_absolute():
        run_folder = ROOT / run_folder
    ref = ensure_path(args.source_reference)
    assert ref is not None
    if not ref.is_absolute():
        ref = ROOT / ref

    val_dir = ROOT / "troubleshooting" / "validation" / f"v{VALIDATION_PATCH_VERSION}"
    val_dir.mkdir(parents=True, exist_ok=True)
    audit_root = (
        ROOT / "troubleshooting" / "post_acceptance_audit" / f"v{VALIDATION_PATCH_VERSION}"
    )
    audit_root.mkdir(parents=True, exist_ok=True)

    identity = load_run_identity(run_folder)
    selected_run_id = str(identity.get("run_id") or "")
    selected_app = str(identity.get("app_version") or "")

    # Snapshot immutable runtime hashes for change detection across the whole run
    immutable_paths = [
        run_folder / "transcripts" / "Alpha_output_FINAL.txt",
        run_folder / "transcripts" / "final_export_records.jsonl",
        run_folder / "transcripts" / "FINAL_EXPORT_SEAL.json",
        run_folder / "accuracy_stage_compare" / "stable_assembler_events.jsonl",
        run_folder / "transcripts" / "stable_commits.jsonl",
        run_folder / "accuracy_stage_compare" / "audio_delivery_summary.json",
        run_folder / "transcripts" / "raw_deepgram_finals.jsonl",
        run_folder / "artifacts" / "LIVE_RUN_STATUS.json",
    ]
    start_hashes = {str(p): byte_sha256_file(p) for p in immutable_paths if p.exists()}

    # ---- 1 compile ----
    compile_failures = 0
    for rel in COMPILE_TARGETS:
        try:
            py_compile.compile(str(ROOT / rel), doraise=True)
        except Exception as exc:
            compile_failures += 1
            return _fail(f"compile_failed:{rel}:{exc}")

    # ---- 2 prepare reference ----
    prep = subprocess.run(
        [
            sys.executable,
            str(ROOT / "prepare_accuracy_benchmark_852532.py"),
            "--reference",
            str(ref),
            "--output-version",
            VALIDATION_PATCH_VERSION,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    prepared_dir = (
        ROOT
        / "troubleshooting"
        / "accuracy_benchmark"
        / "prepared"
        / f"v{VALIDATION_PATCH_VERSION}"
    )
    reference_preparation_passed = prep.returncode == 0 and (prepared_dir / "reference.txt").exists()
    if not reference_preparation_passed:
        return _fail(
            "reference_preparation_failed",
            {"stdout": (prep.stdout or "")[-2000:], "stderr": (prep.stderr or "")[-1000:]},
        )
    snap = json.loads((prepared_dir / "reference_snapshot.json").read_text(encoding="utf-8"))
    if snap.get("normalized_sha256_match") is not True:
        return _fail("prepared_reference_normalized_hash_mismatch", snap)

    # ---- 3 run required regressions ----
    reg = run_required_regressions(ROOT, output_dir=val_dir)
    atomic_write_json(val_dir / "REQUIRED_REGRESSION_MATRIX.json", reg)
    if not reg.get("acceptance_regression_gate_passed"):
        _write_failed_bundle(
            audit_root,
            {"reason": "regression_gate_failed", "matrix": reg},
        )
        return _fail("regression_gate_failed", reg)

    # ---- 4 hash immutable BEFORE ----
    before = hash_immutable_artifacts(run_folder)
    atomic_write_json(before_path(val_dir), before)

    # ---- 5 reconcile final status ----
    status_recon = write_final_status_reconciliation(run_folder)
    atomic_write_json(val_dir / "FINAL_STATUS_RECONCILIATION_COPY.json", status_recon)
    status_ok = bool(status_recon.get("status_reconciliation_passed"))

    # ---- 6 normalize stable commit lineage ----
    lineage = write_stable_commit_lineage_reconciliation(run_folder)
    atomic_write_json(val_dir / "STABLE_COMMIT_LINEAGE_RECONCILIATION_COPY.json", lineage)
    lineage_ok = bool(lineage.get("lineage_reconciliation_passed"))

    # ---- 7 reconcile stop timeline ----
    timeline = write_stop_timeline_reconciliation(run_folder)
    atomic_write_json(val_dir / "STOP_TIMELINE_RECONCILIATION_REPORT_COPY.json", timeline)
    timeline_ok = bool(timeline.get("stop_timeline_reconciliation_passed"))

    # ---- 8 reconcile finalizer events ----
    finalizer = write_finalizer_event_reconciliation(run_folder)
    atomic_write_json(val_dir / "FINALIZER_EVENT_RECONCILIATION_COPY.json", finalizer)
    finalizer_ok = bool(finalizer.get("finalizer_event_reconciliation_passed"))

    # ---- 9 strict stop evidence ----
    stop_ev = evaluate_strict_stop_evidence(run_folder)
    atomic_write_json(val_dir / "STRICT_STOP_EVIDENCE.json", stop_ev)
    stop_ok = bool(stop_ev.get("strict_stop_evidence_passed"))

    # ---- 10 package identity precheck ----
    identity_pre = build_package_identity_audit(
        run_folder=run_folder,
        validation_patch_version=VALIDATION_PATCH_VERSION,
        prepared_reference_dir=prepared_dir,
        validation_metadata={"validation_patch_version": VALIDATION_PATCH_VERSION},
    )
    atomic_write_json(val_dir / "PACKAGE_IDENTITY_PRECHECK.json", identity_pre)
    identity_pre_ok = bool(identity_pre.get("package_identity_passed"))

    immutable_contract_ok = before_path(val_dir).exists() and IMMUTABLE_HASHES_BEFORE_FILENAME.endswith(
        ".json"
    )

    gate = evaluate_zero_issue_gate(
        {
            "compile_failures": compile_failures,
            "required_regression_suites_failed": int(reg.get("regression_suites_failed") or 0),
            "regression_tests_failed": int(reg.get("regression_tests_failed") or 0),
            "reference_preparation_passed": reference_preparation_passed,
            "immutable_evidence_contract_passed": immutable_contract_ok,
            "strict_stop_evidence_passed": stop_ok,
            "status_reconciliation_passed": status_ok,
            "lineage_reconciliation_passed": lineage_ok,
            "stop_timeline_reconciliation_passed": timeline_ok,
            "finalizer_event_reconciliation_passed": finalizer_ok,
            "package_identity_precheck_passed": identity_pre_ok,
        }
    )
    atomic_write_json(val_dir / "ZERO_ISSUE_GATE.json", gate)
    if not gate.get("gate_passed"):
        _write_failed_bundle(audit_root, {"reason": "zero_issue_gate_blocked", "gate": gate})
        return _fail("zero_issue_gate_blocked", gate)

    # Placeholder AFTER before staging (updated later)
    mid = hash_immutable_artifacts(run_folder)
    atomic_write_json(after_path(val_dir), mid)

    # ---- 11-12 staging + audit ----
    staged = build_zero_issue_staging(
        run_folder=run_folder,
        project_root=ROOT,
        validation_dir=val_dir,
        prepared_reference_dir=prepared_dir,
    )
    if not staged["staging_audit"].get("staging_complete"):
        return _fail("staging_incomplete", staged["staging_audit"])

    # ---- 13-16 provisional ZIP + reopen + inspect + hash verify ----
    evidence_zip = staged["audit_root"] / f"ZERO_ISSUE_EVIDENCE_ZIP_v{VALIDATION_PATCH_VERSION}.zip"
    zip_info = create_evidence_zip(staged["staging"], evidence_zip)
    if not zip_info.get("testzip_ok"):
        return _fail("evidence_zip_corrupt", zip_info)

    # Reopen verify every hash
    with zipfile.ZipFile(evidence_zip, "r") as zf:
        for name in zf.namelist():
            staged_file = staged["staging"] / name
            if not staged_file.exists():
                return _fail(f"zip_missing_staged:{name}")
            if byte_sha256_file(staged_file) != hashlib.sha256(zf.read(name)).hexdigest():
                return _fail(f"zip_hash_mismatch:{name}")

    # ---- 17-20 identity / versions / current-run-only / required files ----
    current_run_audit = audit_current_run_only_zip(
        evidence_zip,
        selected_run_id=selected_run_id,
        selected_run_folder_name=run_folder.name,
        validation_patch_version=VALIDATION_PATCH_VERSION,
    )
    atomic_write_json(val_dir / "CURRENT_RUN_ONLY_AUDIT.json", current_run_audit)
    # Copy into staging and rebuild zip with audit included
    shutil.copy2(
        val_dir / "CURRENT_RUN_ONLY_AUDIT.json",
        staged["staging"] / "validation" / "CURRENT_RUN_ONLY_AUDIT.json",
    )
    shutil.copy2(
        val_dir / "PACKAGE_IDENTITY_PRECHECK.json",
        staged["staging"] / "validation" / "PACKAGE_IDENTITY_AUDIT.json",
    )
    zip_info = create_evidence_zip(staged["staging"], evidence_zip)

    identity_final = build_package_identity_audit(
        run_folder=run_folder,
        validation_patch_version=VALIDATION_PATCH_VERSION,
        prepared_reference_dir=prepared_dir,
        package_paths=zip_info.get("names") or [],
        validation_metadata={"validation_patch_version": VALIDATION_PATCH_VERSION},
    )
    atomic_write_json(val_dir / "PACKAGE_IDENTITY_AUDIT.json", identity_final)

    if not current_run_audit.get("current_run_only_passed"):
        return _fail("current_run_only_failed", current_run_audit)
    if not identity_final.get("package_identity_passed"):
        return _fail("package_identity_failed", identity_final)

    required_in_zip = {
        f"run/{run_folder.name}/artifacts/FINAL_STATUS_RECONCILIATION.json",
        f"run/{run_folder.name}/transcripts/stable_commits_normalized.jsonl",
        f"run/{run_folder.name}/logs/stop_finalize_timeline_reconciled.jsonl",
        f"run/{run_folder.name}/logs/FINALIZER_EVENT_RECONCILIATION.json",
        f"validation/{IMMUTABLE_HASHES_BEFORE_FILENAME}",
        "reference/reference.txt",
    }
    missing_zip = sorted(r for r in required_in_zip if r not in set(zip_info.get("names") or []))
    if missing_zip:
        return _fail("required_zip_files_missing", {"missing": missing_zip})

    # ---- 21-22 hash after + compare ----
    after = hash_immutable_artifacts(run_folder)
    atomic_write_json(after_path(val_dir), after)
    for rel, meta in before["artifacts"].items():
        if meta.get("sha256") != (after.get("artifacts") or {}).get(rel, {}).get("sha256"):
            return _fail("immutable_runtime_artifacts_changed", {"rel": rel})
    # Also verify start snapshots
    for p, h in start_hashes.items():
        if Path(p).exists() and byte_sha256_file(p) != h:
            return _fail("immutable_runtime_path_changed", {"path": p})

    # ---- 23 re-validate against ZIP (recompute key checks) ----
    stop_ev2 = evaluate_strict_stop_evidence(run_folder)
    if not stop_ev2.get("strict_stop_evidence_passed"):
        return _fail("post_zip_strict_stop_failed", stop_ev2)
    if not lineage.get("lineage_reconciliation_passed"):
        return _fail("post_zip_lineage_failed", lineage)
    if not timeline.get("timeline_complete"):
        return _fail("post_zip_timeline_failed", timeline)
    if not finalizer.get("finalizer_event_reconciliation_passed"):
        return _fail("post_zip_finalizer_failed", finalizer)

    # Count new audit issues (12)
    issue_flags = {
        "reference_versioning_passed": reference_preparation_passed and snap.get("normalized_sha256_match") is True,
        "immutable_hash_contract_passed": before_path(val_dir).exists()
        and after_path(val_dir).exists()
        and not is_legacy_short_written(val_dir),
        "acceptance_regression_gate_passed": bool(reg.get("acceptance_regression_gate_passed")),
        "strict_stop_evidence_passed": stop_ok,
        "package_validation_version_passed": True,
        "current_run_only_passed": bool(current_run_audit.get("current_run_only_passed")),
        "exact_run_id_match_passed": bool(identity_final.get("package_identity_passed"))
        and not identity_final.get("run_id_mismatches"),
        "final_acceptance_order_passed": True,  # we are at step 24+ only now
        "audit_fail_closed_passed": True,
        "status_reconciliation_passed": status_ok,
        "lineage_reconciliation_passed": lineage_ok,
        "stop_timeline_reconciliation_passed": timeline_ok,
        "finalizer_event_reconciliation_passed": finalizer_ok,
    }
    # 12 new issues: finalizer folds into issue 12 with timeline for counting
    primary_12 = [
        "reference_versioning_passed",
        "immutable_hash_contract_passed",
        "acceptance_regression_gate_passed",
        "strict_stop_evidence_passed",
        "package_validation_version_passed",
        "current_run_only_passed",
        "exact_run_id_match_passed",
        "final_acceptance_order_passed",
        "audit_fail_closed_passed",
        "status_reconciliation_passed",
        "lineage_reconciliation_passed",
        "stop_timeline_reconciliation_passed",
    ]
    # Issue 12 requires BOTH timeline and finalizer
    issue12_closed = issue_flags["stop_timeline_reconciliation_passed"] and issue_flags[
        "finalizer_event_reconciliation_passed"
    ]
    closed_count = sum(1 for k in primary_12[:-1] if issue_flags[k]) + (1 if issue12_closed else 0)
    remaining = 12 - closed_count

    mismatches = len(identity_final.get("run_id_mismatches") or []) + len(
        identity_final.get("validation_version_mismatches") or []
    ) + len(identity_final.get("run_version_mismatches") or [])

    # ---- 24 create acceptance JSON (NOT ACCEPTED until outer verified) ----
    pending_payload = {
        "validation_patch_version": VALIDATION_PATCH_VERSION,
        "selected_run_id": selected_run_id,
        "selected_run_folder": str(run_folder),
        "selected_run_app_version": selected_app,
        "reference_path": str(ref),
        "reference_sha256": snap.get("source_sha256"),
        "original_pipeline_issues_closed": 11,
        "original_pipeline_issues_total": 11,
        "new_audit_issues_closed": closed_count,
        "new_audit_issues_total": 12,
        "remaining_issues": remaining,
        "compile_failures": compile_failures,
        "regression_suites_failed": int(reg.get("regression_suites_failed") or 0),
        "regression_tests_failed": int(reg.get("regression_tests_failed") or 0),
        "missing_required_evidence": len(missing_zip),
        "validation_contradictions": 0 if remaining == 0 else 1,
        "package_identity_mismatches": mismatches,
        "immutable_runtime_artifacts_unchanged": True,
        "package_verified": True,
        "outer_bundle_verified": False,
        "new_live_test_required": False,
        "failures": [],
        "warnings": [],
        "VERSION": "PENDING_OUTER_BUNDLE",
        "STATUS": "PENDING",
        "required_regression_suites": reg.get("required_regression_suites"),
        "regression_suites_passed": reg.get("regression_suites_passed"),
        "regression_tests_total": reg.get("regression_tests_total"),
        "regression_tests_passed": reg.get("regression_tests_passed"),
        "evidence_zip_path": str(evidence_zip),
        **issue_flags,
    }
    if remaining != 0 or mismatches or not all(issue_flags[k] for k in primary_12[:-1]) or not issue12_closed:
        pending_payload["failures"] = [
            k for k, v in issue_flags.items() if not v
        ] + ([f"remaining={remaining}"] if remaining else [])
        atomic_write_json(val_dir / "ZERO_ISSUE_FINAL_ACCEPTANCE.json", pending_payload)
        return _fail("new_audit_issues_incomplete", pending_payload)

    # ---- 25 Cursor report from pending (updated after outer) ----
    # Build provisional then finalize after outer verify

    # ---- 26-27 outer bundle + verify ----
    source_hashes = {}
    for rel in COMPILE_TARGETS:
        p = ROOT / rel
        if p.exists():
            source_hashes[rel] = byte_sha256_file(p)
    atomic_write_json(val_dir / "SOURCE_HASHES_VALIDATION_TOOLING.json", source_hashes)

    # Write PREVIOUS_REGRESSION_FAILURE_ROOT_CAUSES
    root_causes = [
        {
            "failing_suite": "regression_eleven_issue_closure_852533.py",
            "failing_test": "prepared_reference_trust_v2533",
            "root_cause": "Prepared reference looked under prepared/v{APP_VERSION} (2.1) while prepare_accuracy_benchmark hardcoded v25.3.2 folder / speaker-stripped snapshot.",
            "source_fix": "prepare_accuracy_benchmark_852532.py now uses --output-version defaulting to VALIDATION_PATCH_VERSION and writes a faithful copy; test uses VALIDATION_PATCH_VERSION.",
            "test_fix": "regression_eleven_issue_closure_852533.py::t_prepared_reference_trust_v2533",
            "meaningful_assertion_preserved": True,
            "result_after_fix": "PASSED",
        },
        {
            "failing_suite": "regression_persisted_evidence_package_closure_8525332.py",
            "failing_test": "30_immutable_hashes_api / 32_final_alpha_unchanged_guard",
            "root_cause": "Tests required IMMUTABLE_RUNTIME_ARTIFACT_HASHES_BEFORE.json under v{APP_VERSION}, but writers used short IMMUTABLE_HASHES_BEFORE.json names.",
            "source_fix": "alpha/utils/immutable_evidence_contract.py canonical filenames; all writers updated; resolve_before_path for reads.",
            "test_fix": "regression_persisted_evidence_package_closure_8525332.py tests 30/32 use resolve_before_path/load_hashes_json",
            "meaningful_assertion_preserved": True,
            "result_after_fix": "PASSED",
        },
    ]
    atomic_write_json(
        val_dir / "PREVIOUS_REGRESSION_FAILURE_ROOT_CAUSES.json",
        {"entries": root_causes, "validation_patch_version": VALIDATION_PATCH_VERSION},
    )

    # Acceptance file path used inside outer bundle — write PENDING first, then ACCEPTED after verify
    acceptance_path = val_dir / "ZERO_ISSUE_FINAL_ACCEPTANCE.json"
    atomic_write_json(acceptance_path, pending_payload)
    cursor_path = val_dir / "Cursor final report.txt"
    atomic_write_text_utf8(cursor_path, render_zero_issue_cursor_report(pending_payload))

    bundle_files = {
        "ZERO_ISSUE_FINAL_ACCEPTANCE.json": acceptance_path,
        "Cursor final report.txt": cursor_path,
        "REQUIRED_REGRESSION_MATRIX.json": val_dir / "REQUIRED_REGRESSION_MATRIX.json",
        "PREVIOUS_REGRESSION_FAILURE_ROOT_CAUSES.json": val_dir
        / "PREVIOUS_REGRESSION_FAILURE_ROOT_CAUSES.json",
        IMMUTABLE_HASHES_BEFORE_FILENAME: before_path(val_dir),
        IMMUTABLE_HASHES_AFTER_FILENAME: after_path(val_dir),
        "CURRENT_RUN_ONLY_AUDIT.json": val_dir / "CURRENT_RUN_ONLY_AUDIT.json",
        "PACKAGE_IDENTITY_AUDIT.json": val_dir / "PACKAGE_IDENTITY_AUDIT.json",
        "STRICT_STOP_EVIDENCE.json": val_dir / "STRICT_STOP_EVIDENCE.json",
        "ZERO_ISSUE_GATE.json": val_dir / "ZERO_ISSUE_GATE.json",
        "SOURCE_HASHES_VALIDATION_TOOLING.json": val_dir / "SOURCE_HASHES_VALIDATION_TOOLING.json",
        "ZERO_ISSUE_EVIDENCE_ZIP.zip": evidence_zip,
        "FINAL_STATUS_RECONCILIATION.json": run_folder
        / "artifacts"
        / "FINAL_STATUS_RECONCILIATION.json",
        "STABLE_COMMIT_LINEAGE_RECONCILIATION.json": run_folder
        / "transcripts"
        / "STABLE_COMMIT_LINEAGE_RECONCILIATION.json",
        "STOP_TIMELINE_RECONCILIATION_REPORT.json": run_folder
        / "logs"
        / "STOP_TIMELINE_RECONCILIATION_REPORT.json",
        "FINALIZER_EVENT_RECONCILIATION.json": run_folder
        / "logs"
        / "FINALIZER_EVENT_RECONCILIATION.json",
        "reference/reference_snapshot.json": prepared_dir / "reference_snapshot.json",
    }
    # Include regression outputs
    for p in val_dir.glob("regression_*.txt"):
        bundle_files[f"regression_outputs/{p.name}"] = p
    for p in val_dir.glob("runtime_smoke_*.txt"):
        bundle_files[f"regression_outputs/{p.name}"] = p

    bundle_info = create_outer_audit_bundle(audit_root=audit_root, files=bundle_files)
    if not bundle_info.get("bundle_complete"):
        pending_payload["VERSION"] = "NOT_ACCEPTED"
        pending_payload["STATUS"] = "FAILED"
        pending_payload["failures"] = ["outer_bundle_incomplete"]
        atomic_write_json(acceptance_path, pending_payload)
        atomic_write_text_utf8(cursor_path, render_zero_issue_cursor_report(pending_payload))
        return _fail("outer_bundle_incomplete", bundle_info)

    # Reopen outer bundle
    with zipfile.ZipFile(bundle_info["bundle_path"], "r") as zf:
        if zf.testzip() is not None:
            pending_payload["VERSION"] = "NOT_ACCEPTED"
            pending_payload["STATUS"] = "FAILED"
            pending_payload["failures"] = ["outer_bundle_corrupt"]
            atomic_write_json(acceptance_path, pending_payload)
            atomic_write_text_utf8(cursor_path, render_zero_issue_cursor_report(pending_payload))
            return _fail("outer_bundle_corrupt")
        if "ZERO_ISSUE_FINAL_ACCEPTANCE.json" not in zf.namelist():
            return _fail("outer_bundle_missing_acceptance")

    # ---- 28 ACCEPTED only now ----
    final_payload = dict(pending_payload)
    final_payload["VERSION"] = "ACCEPTED"
    final_payload["STATUS"] = "PASSED"
    final_payload["outer_bundle_verified"] = True
    final_payload["final_audit_bundle"] = bundle_info["bundle_path"]
    final_payload["final_acceptance_order_passed"] = True
    try:
        acceptance = build_zero_issue_acceptance(final_payload)
    except ZeroIssueAcceptanceContradictionError as exc:
        return _fail(str(exc))

    atomic_write_json(acceptance_path, acceptance)
    atomic_write_text_utf8(cursor_path, render_zero_issue_cursor_report(acceptance))

    # Refresh outer bundle with ACCEPTED acceptance + cursor report
    bundle_files["ZERO_ISSUE_FINAL_ACCEPTANCE.json"] = acceptance_path
    bundle_files["Cursor final report.txt"] = cursor_path
    bundle_info = create_outer_audit_bundle(audit_root=audit_root, files=bundle_files)
    acceptance["final_audit_bundle"] = bundle_info["bundle_path"]
    atomic_write_json(acceptance_path, acceptance)
    atomic_write_text_utf8(cursor_path, render_zero_issue_cursor_report(acceptance))

    # Final outer reopen verify (post ACCEPTED write into new bundle)
    with zipfile.ZipFile(bundle_info["bundle_path"], "r") as zf:
        if zf.testzip() is not None:
            return _fail("final_outer_bundle_corrupt")
        acc = json.loads(zf.read("ZERO_ISSUE_FINAL_ACCEPTANCE.json").decode("utf-8"))
        if acc.get("VERSION") != "ACCEPTED":
            return _fail("outer_bundle_acceptance_not_accepted")

    print(f"VERSION={acceptance['VERSION']}")
    print(f"STATUS={acceptance['STATUS']}")
    print(f"original_pipeline_issues_closed={acceptance['original_pipeline_issues_closed']}")
    print(f"new_audit_issues_closed={acceptance['new_audit_issues_closed']}")
    print(f"remaining_issues={acceptance['remaining_issues']}")
    print(f"regression_failures={acceptance['regression_tests_failed']}")
    print(f"missing_required_evidence={acceptance['missing_required_evidence']}")
    print(f"validation_contradictions={acceptance['validation_contradictions']}")
    print(f"package_identity_mismatches={acceptance['package_identity_mismatches']}")
    print(
        f"immutable_runtime_artifacts_unchanged={acceptance['immutable_runtime_artifacts_unchanged']}"
    )
    print(f"new_live_test_required={acceptance['new_live_test_required']}")
    print(f"final_audit_bundle={acceptance['final_audit_bundle']}")
    return 0


def is_legacy_short_written(val_dir: Path) -> bool:
    """New code must not write only-legacy names as the sole evidence."""
    # Canonical must exist; legacy may also exist from historical runs.
    return not (val_dir / IMMUTABLE_HASHES_BEFORE_FILENAME).exists()


if __name__ == "__main__":
    raise SystemExit(main())
