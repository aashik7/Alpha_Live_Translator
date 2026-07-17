"""Canonical zero-issue acceptance object (V25.3.3.2.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ZeroIssueAcceptanceContradictionError(RuntimeError):
    """Raised when VERSION=ACCEPTED would contradict other fields."""


ISSUE_BOOLEANS = (
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
    "finalizer_event_reconciliation_passed",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_zero_issue_acceptance(payload: dict[str, Any]) -> dict[str, Any]:
    state = {
        "validation_patch_version": payload.get("validation_patch_version"),
        "selected_run_id": payload.get("selected_run_id"),
        "selected_run_folder": payload.get("selected_run_folder"),
        "selected_run_app_version": payload.get("selected_run_app_version"),
        "reference_path": payload.get("reference_path"),
        "reference_sha256": payload.get("reference_sha256"),
        "original_pipeline_issues_closed": int(
            payload.get("original_pipeline_issues_closed", 0)
        ),
        "original_pipeline_issues_total": int(
            payload.get("original_pipeline_issues_total", 11)
        ),
        "new_audit_issues_closed": int(payload.get("new_audit_issues_closed", 0)),
        "new_audit_issues_total": int(payload.get("new_audit_issues_total", 12)),
        "remaining_issues": int(payload.get("remaining_issues", -1)),
        "compile_failures": int(payload.get("compile_failures", -1)),
        "regression_suites_failed": int(payload.get("regression_suites_failed", -1)),
        "regression_tests_failed": int(payload.get("regression_tests_failed", -1)),
        "missing_required_evidence": int(payload.get("missing_required_evidence", -1)),
        "validation_contradictions": int(payload.get("validation_contradictions", -1)),
        "package_identity_mismatches": int(payload.get("package_identity_mismatches", -1)),
        "immutable_runtime_artifacts_unchanged": bool(
            payload.get("immutable_runtime_artifacts_unchanged")
        ),
        "package_verified": bool(payload.get("package_verified")),
        "outer_bundle_verified": bool(payload.get("outer_bundle_verified")),
        "new_live_test_required": bool(payload.get("new_live_test_required", True)),
        "failures": list(payload.get("failures") or []),
        "warnings": list(payload.get("warnings") or []),
        "generated_utc": _utc_now(),
        "required_regression_suites": payload.get("required_regression_suites"),
        "regression_suites_passed": payload.get("regression_suites_passed"),
        "regression_tests_total": payload.get("regression_tests_total"),
        "regression_tests_passed": payload.get("regression_tests_passed"),
        "final_audit_bundle": payload.get("final_audit_bundle"),
        "evidence_zip_path": payload.get("evidence_zip_path"),
    }
    for key in ISSUE_BOOLEANS:
        state[key] = bool(payload.get(key))

    # Derive remaining if not provided
    if "remaining_issues" not in payload or payload.get("remaining_issues") is None:
        closed = sum(1 for k in ISSUE_BOOLEANS if state.get(k) is True)
        # 12 new issues — finalizer counted in ISSUE_BOOLEANS which has 13 keys
        # Spec lists 12 issue booleans in acceptance fields section.
        pass

    version = str(payload.get("VERSION") or "NOT_ACCEPTED")
    status = str(payload.get("STATUS") or "FAILED")
    state["VERSION"] = version
    state["STATUS"] = status

    if version == "ACCEPTED":
        _assert_accepted_invariants(state)

    return state


def _assert_accepted_invariants(state: dict[str, Any]) -> None:
    problems: list[str] = []
    if state.get("original_pipeline_issues_closed") != 11:
        problems.append("original_pipeline_issues_closed!=11")
    if state.get("original_pipeline_issues_total") != 11:
        problems.append("original_pipeline_issues_total!=11")
    if state.get("new_audit_issues_closed") != 12:
        problems.append("new_audit_issues_closed!=12")
    if state.get("new_audit_issues_total") != 12:
        problems.append("new_audit_issues_total!=12")
    if state.get("remaining_issues") != 0:
        problems.append("remaining_issues!=0")
    for key in (
        "compile_failures",
        "regression_suites_failed",
        "regression_tests_failed",
        "missing_required_evidence",
        "validation_contradictions",
        "package_identity_mismatches",
    ):
        if state.get(key) != 0:
            problems.append(f"{key}!={state.get(key)}")
    # Exactly the 12 new audit issues from the task list
    twelve = (
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
    )
    # Note: finalizer_event is issue 12 in the "12 remaining" — map:
    # The 12 issues in FINAL OBJECTIVE map to the booleans; finalizer is separate field
    # Spec lists both stop_timeline and finalizer in the required fields.
    # Count closed among the 12 primary flags listed under "all 12 issue booleans".
    primary_12 = twelve
    for key in primary_12:
        if state.get(key) is not True:
            problems.append(f"{key}!=true")
    # Also require finalizer reconciliation (part of the 12 observability issues)
    if state.get("finalizer_event_reconciliation_passed") is not True:
        problems.append("finalizer_event_reconciliation_passed!=true")
    if state.get("immutable_runtime_artifacts_unchanged") is not True:
        problems.append("immutable_runtime_artifacts_unchanged!=true")
    if state.get("package_verified") is not True:
        problems.append("package_verified!=true")
    if state.get("outer_bundle_verified") is not True:
        problems.append("outer_bundle_verified!=true")
    if state.get("new_live_test_required") is not False:
        problems.append("new_live_test_required!=false")
    if list(state.get("failures") or []):
        problems.append("failures_nonempty")
    if state.get("STATUS") != "PASSED":
        problems.append("STATUS!=PASSED")
    if problems:
        raise ZeroIssueAcceptanceContradictionError(
            "ACCEPTED_contradiction:" + ",".join(problems)
        )


def render_zero_issue_cursor_report(acceptance: dict[str, Any]) -> str:
    """Generate Cursor report exclusively from acceptance JSON fields."""
    lines = [
        "Alpha Live Translator — Zero-Issue Final Acceptance Report",
        f"validation_patch_version={acceptance.get('validation_patch_version')}",
        f"selected_run_id={acceptance.get('selected_run_id')}",
        f"selected_run_folder={acceptance.get('selected_run_folder')}",
        f"selected_run_app_version={acceptance.get('selected_run_app_version')}",
        f"VERSION={acceptance.get('VERSION')}",
        f"STATUS={acceptance.get('STATUS')}",
        f"original_pipeline_issues_closed={acceptance.get('original_pipeline_issues_closed')}",
        f"new_audit_issues_closed={acceptance.get('new_audit_issues_closed')}",
        f"remaining_issues={acceptance.get('remaining_issues')}",
        f"regression_suites_failed={acceptance.get('regression_suites_failed')}",
        f"regression_tests_failed={acceptance.get('regression_tests_failed')}",
        f"missing_required_evidence={acceptance.get('missing_required_evidence')}",
        f"validation_contradictions={acceptance.get('validation_contradictions')}",
        f"package_identity_mismatches={acceptance.get('package_identity_mismatches')}",
        f"immutable_runtime_artifacts_unchanged={acceptance.get('immutable_runtime_artifacts_unchanged')}",
        f"new_live_test_required={acceptance.get('new_live_test_required')}",
        f"final_audit_bundle={acceptance.get('final_audit_bundle')}",
        f"generated_utc={acceptance.get('generated_utc')}",
    ]
    for key in ISSUE_BOOLEANS:
        lines.append(f"{key}={acceptance.get(key)}")
    fails = acceptance.get("failures") or []
    lines.append("failures=" + (",".join(fails) if fails else "[]"))
    return "\n".join(lines) + "\n"
