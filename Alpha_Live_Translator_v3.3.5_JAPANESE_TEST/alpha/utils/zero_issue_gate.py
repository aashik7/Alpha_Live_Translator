"""Fail-closed gate before zero-issue package creation."""

from __future__ import annotations

from typing import Any


def evaluate_zero_issue_gate(conditions: dict[str, Any]) -> dict[str, Any]:
    required = {
        "compile_failures": 0,
        "required_regression_suites_failed": 0,
        "regression_tests_failed": 0,
        "reference_preparation_passed": True,
        "immutable_evidence_contract_passed": True,
        "strict_stop_evidence_passed": True,
        "status_reconciliation_passed": True,
        "lineage_reconciliation_passed": True,
        "stop_timeline_reconciliation_passed": True,
        "finalizer_event_reconciliation_passed": True,
        "package_identity_precheck_passed": True,
    }
    failures: list[str] = []
    for key, expected in required.items():
        actual = conditions.get(key)
        if actual != expected:
            failures.append(f"{key}={actual!r} expected={expected!r}")
    return {
        "gate_passed": len(failures) == 0,
        "failures": failures,
        "conditions": {k: conditions.get(k) for k in required},
    }


def assert_package_allowed(gate: dict[str, Any]) -> None:
    if not gate.get("gate_passed"):
        raise RuntimeError(
            "zero_issue_gate_blocked:" + ",".join(gate.get("failures") or [])
        )
