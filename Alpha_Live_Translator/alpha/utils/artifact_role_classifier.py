"""Explicit artifact role classification for packaging (V25.3.3.2.4).

Replaces naive filename-substring rules such as ``*ACCEPTANCE*`` that wrongly
exclude regression reports like ``regression_canonical_acceptance_bundle_*.txt``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

ROLE_ACCEPTANCE_AUTHORITY = "acceptance_authority"
ROLE_CURSOR_REPORT = "cursor_report"
ROLE_REGRESSION_EVIDENCE = "regression_evidence"
ROLE_VALIDATION_EVIDENCE = "validation_evidence"
ROLE_RUNTIME_EVIDENCE = "runtime_evidence"
ROLE_REFERENCE_EVIDENCE = "reference_evidence"
ROLE_PACKAGE_AUDIT = "package_audit"
ROLE_SOURCE_SNAPSHOT = "source_snapshot"
ROLE_UNKNOWN = "unknown"
ROLE_NESTED_ZIP = "nested_zip"
ROLE_CLOSURE_BUNDLE = "closure_bundle"

ACCEPTANCE_AUTHORITY_BASENAMES = frozenset(
    {
        "ZERO_ISSUE_FINAL_ACCEPTANCE.json",
        "ZERO_REMAINING_ISSUES_ACCEPTANCE.json",
        "ELEVEN_ISSUE_FINAL_ACCEPTANCE.json",
        "CANONICAL_FINAL_ACCEPTANCE.json",
        "FINAL_ACCEPTANCE.json",
    }
)

CURSOR_REPORT_BASENAMES = frozenset(
    {
        "Cursor final report.txt",
        "cursor final report.txt",
    }
)

REQUIRED_REGRESSION_REPORTS = (
    "regression_eleven_issue_closure_852533.txt",
    "regression_final_writer_stop_tail_8525331.txt",
    "regression_persisted_evidence_package_closure_8525332.txt",
    "regression_canonical_acceptance_bundle_85253321.txt",
    "regression_zero_issue_validation_85253322.txt",
    "regression_single_authority_packaging_85253323.txt",
)

REQUIRED_REGRESSION_REPORTS_SET = frozenset(REQUIRED_REGRESSION_REPORTS)

_ACCEPTANCE_SCHEMA_MARKERS = (
    "total_known_issues_closed",
    "acceptance_authority_count",
    "issues_closed",
    "original_pipeline_issues_closed",
    "packaging_issues_closed",
    "new_packaging_issues_closed",
    "previous_packaging_issues_closed",
)

_EVIDENCE_FORBIDDEN_BASENAMES = frozenset(
    {
        "PREPACKAGE_CLOSURE.json",
        "FINAL_CLOSURE.json",
        "POST_ZIP_VERIFICATION.json",
        "OUTER_BUNDLE_MANIFEST.json",
        "OUTER_BUNDLE_CONTENT_AUDIT.json",
        "OUTER_BUNDLE_INTERNAL_VERIFICATION.json",
        "OUTER_BUNDLE_AUDIT.json",
        "FINAL_VALIDATION_BUNDLE.zip",
    }
)


def _basename(name: str | Path) -> str:
    return Path(name).name


def _load_json_if_possible(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if path is None or not path.is_file():
        return None
    if path.suffix.lower() != ".json":
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def matches_acceptance_authority_schema(data: dict[str, Any]) -> bool:
    if str(data.get("VERSION") or "") != "ACCEPTED":
        return False
    if str(data.get("artifact_role") or "") == ROLE_ACCEPTANCE_AUTHORITY:
        return True
    return any(k in data for k in _ACCEPTANCE_SCHEMA_MARKERS)


def is_acceptance_authority(
    name: str | Path,
    *,
    path: Optional[Path] = None,
    payload: Optional[dict[str, Any]] = None,
) -> bool:
    base = _basename(name)
    if base in ACCEPTANCE_AUTHORITY_BASENAMES:
        return True
    data = payload if payload is not None else _load_json_if_possible(path)
    if data is None:
        return False
    if str(data.get("artifact_role") or "") == ROLE_ACCEPTANCE_AUTHORITY:
        return True
    return matches_acceptance_authority_schema(data)


def is_cursor_report(name: str | Path) -> bool:
    base = _basename(name)
    if base in CURSOR_REPORT_BASENAMES:
        return True
    return base.lower() == "cursor final report.txt"


def is_required_regression_report(name: str | Path) -> bool:
    return _basename(name) in REQUIRED_REGRESSION_REPORTS_SET


def is_nested_zip(name: str | Path) -> bool:
    return _basename(name).lower().endswith(".zip")


def is_closure_or_outer_audit(name: str | Path) -> bool:
    base = _basename(name)
    if base in _EVIDENCE_FORBIDDEN_BASENAMES:
        return True
    upper = base.upper()
    if upper.startswith("FINAL_") and upper.endswith("_BUNDLE.ZIP"):
        return True
    if "OUTER_BUNDLE" in upper and base.lower().endswith((".json", ".zip")):
        return True
    if "PREPACKAGE_CLOSURE" in upper or "FINAL_CLOSURE" in upper:
        return True
    if "POST_ZIP_VERIFICATION" in upper:
        return True
    return False


def classify_artifact(
    name: str | Path,
    *,
    path: Optional[Path] = None,
    payload: Optional[dict[str, Any]] = None,
) -> str:
    """Return explicit artifact role. Never uses bare 'acceptance' substring."""
    base = _basename(name)
    if is_required_regression_report(base):
        return ROLE_REGRESSION_EVIDENCE
    if is_cursor_report(base):
        return ROLE_CURSOR_REPORT
    if is_nested_zip(base):
        # Acceptance/audit outer bundles vs evidence zips distinguished by basename.
        if base.upper().startswith("ZERO_ISSUE_EVIDENCE_"):
            return ROLE_PACKAGE_AUDIT
        if "AUDIT_BUNDLE" in base.upper() or "FINAL_" in base.upper():
            return ROLE_CLOSURE_BUNDLE
        return ROLE_NESTED_ZIP
    if is_closure_or_outer_audit(base):
        return ROLE_PACKAGE_AUDIT
    if is_acceptance_authority(base, path=path, payload=payload):
        return ROLE_ACCEPTANCE_AUTHORITY
    low = base.lower()
    if low.startswith("regression_") and low.endswith(".txt"):
        return ROLE_REGRESSION_EVIDENCE
    if low.startswith("runtime_smoke_") and low.endswith(".txt"):
        return ROLE_VALIDATION_EVIDENCE
    if "reference" in low and low.endswith((".txt", ".json")):
        return ROLE_REFERENCE_EVIDENCE
    if any(
        low.startswith(p)
        for p in (
            "immmutable",
            "immutable_",
            "package_identity",
            "package_staging",
            "zero_issue_gate",
            "strict_stop",
            "current_run_only",
            "required_regression",
            "source_hashes",
        )
    ):
        return ROLE_VALIDATION_EVIDENCE
    if path is not None:
        try:
            parts = {p.lower() for p in Path(path).parts}
        except Exception:
            parts = set()
        if "transcripts" in parts or "accuracy_stage_compare" in parts:
            return ROLE_RUNTIME_EVIDENCE
        if "runs" in parts:
            return ROLE_RUNTIME_EVIDENCE
    return ROLE_UNKNOWN


def evidence_zip_forbidden(
    name: str | Path,
    *,
    path: Optional[Path] = None,
    payload: Optional[dict[str, Any]] = None,
) -> bool:
    """True when a path must not enter the corrected evidence ZIP."""
    role = classify_artifact(name, path=path, payload=payload)
    if role in {
        ROLE_ACCEPTANCE_AUTHORITY,
        ROLE_CURSOR_REPORT,
        ROLE_NESTED_ZIP,
        ROLE_CLOSURE_BUNDLE,
    }:
        return True
    # Explicit allow for required regressions even if name contains 'acceptance'
    if is_required_regression_report(name):
        return False
    if is_closure_or_outer_audit(name):
        return True
    return False


def filename_forbidden_for_evidence(name: str) -> bool:
    """Drop-in replacement for the old ``*ACCEPTANCE*`` filename_forbidden filter."""
    return evidence_zip_forbidden(name)
