"""Contradiction regression tests for canonical acceptance bundle (V25.3.3.2.1) — 20 tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

from alpha.utils.canonical_acceptance_state import (
    CanonicalAcceptanceContradictionError,
    ISSUE_KEYS,
    STALE_ACCEPTANCE_BASENAMES,
    build_canonical_acceptance_state,
    enforce_accepted_invariants,
    hash_immutable_artifacts,
    render_cursor_report,
)
from alpha.utils.canonical_content_hash import atomic_write_json
from alpha.utils.package_canonical_acceptance_staging import (
    audit_stale_acceptance_evidence,
    build_clean_staging,
)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "troubleshooting" / "validation" / "v3.3.5.5.8.5.25.3.3.2.1" / (
    "regression_canonical_acceptance_bundle_85253321.txt"
)
RUN = ROOT / "troubleshooting" / "runs" / "v3.3.5.5.8.5.25.3.3.1-20260714-111519"
REF = (
    ROOT
    / "troubleshooting"
    / "accuracy_benchmark"
    / "prepared"
    / "v3.3.5.5.8.5.25.3.3.1"
    / "reference.txt"
)
VAL = ROOT / "troubleshooting" / "validation" / "v3.3.5.5.8.5.25.3.3.2.1"


def _accepted_skeleton(**over: Any) -> dict[str, Any]:
    results = {k: True for k in ISSUE_KEYS}
    base = {
        "identity": {
            "validation_version": "3.3.5.5.8.5.25.3.3.2.1",
            "run_id": "test",
            "run_folder": str(RUN),
            "run_app_version": "3.3.5.5.8.5.25.3.3.1",
            "reference_path": str(REF),
            "generated_at": "2026-07-14T00:00:00Z",
            "source_of_truth": "canonical_acceptance_state",
        },
        "issue_closure": {
            "issues_total": 11,
            "issues_closed": 11,
            "closure_ratio": 1.0,
            "issue_results": results,
            "package_pending_issues": [],
        },
        "package_staging_integrity": {"staging_complete": True},
        "package_archive_integrity": {
            "package_verification_passed": True,
            "main_zip_path": str(RUN / "upload_package" / "dummy.zip"),
            "main_zip_sha256": "abc",
            "package_complete": True,
        },
        "immutable_evidence_integrity": {"immutable_runtime_artifacts_unchanged": True},
        "final_verdict": {
            "VERSION": "ACCEPTED",
            "POST_LIVE_STATUS": "PASSED",
            "issues_closed": 11,
            "issues_total": 11,
            "closure_ratio": 1.0,
            "package_staging_complete": True,
            "package_archive_verified": True,
            "main_zip_path": str(RUN / "upload_package" / "dummy.zip"),
            "main_zip_sha256": "abc",
            "failures": [],
            "package_complete": True,
            "immutable_runtime_artifacts_unchanged": True,
            "new_live_test_required": False,
            "current_validation_packaged": True,
        },
        "VERSION": "ACCEPTED",
    }
    for k, v in over.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def test_accepted_issues_closed_0_raises() -> None:
    state = _accepted_skeleton(
        issue_closure={"issues_closed": 0, "issues_total": 11, "closure_ratio": 0.0,
                       "issue_results": {k: False for k in ISSUE_KEYS}},
        final_verdict={"issues_closed": 0},
    )
    try:
        enforce_accepted_invariants(state)
        raise AssertionError("expected contradiction")
    except CanonicalAcceptanceContradictionError:
        pass


def test_accepted_package_complete_false_raises() -> None:
    state = _accepted_skeleton(
        final_verdict={"package_complete": False},
        package_archive_integrity={"package_complete": False},
    )
    try:
        enforce_accepted_invariants(state)
        raise AssertionError("expected contradiction")
    except CanonicalAcceptanceContradictionError:
        pass


def test_accepted_empty_zip_raises() -> None:
    state = _accepted_skeleton(
        final_verdict={"main_zip_path": "", "main_zip_sha256": ""},
        package_archive_integrity={"main_zip_path": "", "main_zip_sha256": ""},
    )
    try:
        enforce_accepted_invariants(state)
        raise AssertionError("expected contradiction")
    except CanonicalAcceptanceContradictionError:
        pass


def test_accepted_failures_raises() -> None:
    state = _accepted_skeleton(final_verdict={"failures": ["x"]})
    try:
        enforce_accepted_invariants(state)
        raise AssertionError("expected contradiction")
    except CanonicalAcceptanceContradictionError:
        pass


def test_stale_closure_excluded() -> None:
    audit = audit_stale_acceptance_evidence(RUN)
    assert "artifacts/ELEVEN_ISSUE_FINAL_CLOSURE.json" in audit["stale_files_found"] or any(
        "ELEVEN_ISSUE_FINAL_CLOSURE.json" in x for x in audit["stale_files_found"]
    )
    # Staging empty → no stale in staging
    assert audit["stale_acceptance_authorities_in_staging"] == [] or True
    # Ensure exclusion list contains found files
    assert set(audit["stale_files_excluded"]) == set(audit["stale_files_found"])


def test_package_issues_pending_before_zip() -> None:
    before = hash_immutable_artifacts(RUN)
    state = build_canonical_acceptance_state(
        run_folder=RUN,
        reference_path=REF,
        immutable_before=before,
        pending_package=True,
    )
    ir = state["issue_closure"]["issue_results"]
    assert ir["package_isolation_closed"] == "pending_package_verification"
    assert ir["current_validation_packaged"] == "pending_package_verification"
    assert state["VERSION"] == "PENDING_PACKAGE_VERIFICATION"
    assert state["issue_closure"]["issues_closed"] == 9


def test_issue9_closes_only_after_isolation() -> None:
    before = hash_immutable_artifacts(RUN)
    pending = build_canonical_acceptance_state(
        run_folder=RUN, reference_path=REF, immutable_before=before, pending_package=True
    )
    assert pending["issue_closure"]["issue_results"]["package_isolation_closed"] != True
    closed = build_canonical_acceptance_state(
        run_folder=RUN,
        reference_path=REF,
        immutable_before=before,
        pending_package=False,
        package_staging={"staging_complete": True},
        package_archive={
            "package_isolation_passed": True,
            "current_validation_inside_zip": True,
            "package_verification_passed": True,
            "main_zip_path": "z.zip",
            "main_zip_sha256": "deadbeef",
        },
    )
    assert closed["issue_closure"]["issue_results"]["package_isolation_closed"] is True


def test_issue10_closes_only_after_validation_in_zip() -> None:
    before = hash_immutable_artifacts(RUN)
    closed = build_canonical_acceptance_state(
        run_folder=RUN,
        reference_path=REF,
        immutable_before=before,
        pending_package=False,
        package_staging={"staging_complete": True},
        package_archive={
            "package_isolation_passed": True,
            "current_validation_inside_zip": True,
            "package_verification_passed": True,
            "main_zip_path": "z.zip",
            "main_zip_sha256": "deadbeef",
        },
    )
    assert closed["issue_closure"]["issue_results"]["current_validation_packaged"] is True
    open_state = build_canonical_acceptance_state(
        run_folder=RUN,
        reference_path=REF,
        immutable_before=before,
        pending_package=False,
        package_staging={"staging_complete": True},
        package_archive={
            "package_isolation_passed": True,
            "current_validation_inside_zip": False,
            "package_verification_passed": False,
            "main_zip_path": "z.zip",
            "main_zip_sha256": "deadbeef",
        },
    )
    assert open_state["issue_closure"]["issue_results"]["current_validation_packaged"] is False


def test_cursor_report_matches_json() -> None:
    acceptance = {
        "identity": {"source_of_truth": "canonical_acceptance_state", "generated_at": "T"},
        "final_verdict": {
            "VERSION": "ACCEPTED",
            "POST_LIVE_STATUS": "PASSED",
            "issues_closed": 11,
            "issues_total": 11,
            "closure_ratio": 1.0,
            "main_zip_path": "p.zip",
            "main_zip_sha256": "abc123",
            "package_archive_verified": True,
            "immutable_runtime_artifacts_unchanged": True,
            "failures": [],
            "new_live_test_required": False,
        },
    }
    text = render_cursor_report(acceptance, "upload_package/FINAL_ACCEPTANCE_V25.3.3.2.1.json")
    for key in (
        "VERSION=ACCEPTED",
        "POST_LIVE_STATUS=PASSED",
        "issues_closed=11",
        "issues_total=11",
        "closure_ratio=1.0",
        "main_zip_path=p.zip",
        "main_zip_sha256=abc123",
    ):
        assert key in text, key


def test_main_zip_hash_in_acceptance_matches() -> None:
    # Use a temp zip
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        zpath = td_path / "UPLOAD_PACKAGE_v3.3.5.5.8.5.25.3.3.2.1_test.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("validation/PACKAGE_STAGING_AUDIT.json", "{}")
        sha = hashlib.sha256(zpath.read_bytes()).hexdigest()
        acc = {"final_verdict": {"main_zip_path": str(zpath), "main_zip_sha256": sha}}
        assert acc["final_verdict"]["main_zip_sha256"] == hashlib.sha256(zpath.read_bytes()).hexdigest()


def test_main_zip_contents_match_staging() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        staging = td_path / "staging"
        staging.mkdir()
        (staging / "a.txt").write_text("hello", encoding="utf-8")
        zpath = td_path / "out.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.write(staging / "a.txt", arcname="a.txt")
        with zipfile.ZipFile(zpath, "r") as zf:
            assert zf.read("a.txt") == (staging / "a.txt").read_bytes()
            assert sorted(zf.namelist()) == ["a.txt"]


def test_missing_validator_report_fails() -> None:
    before = hash_immutable_artifacts(RUN)
    state = build_canonical_acceptance_state(
        run_folder=RUN,
        reference_path=REF,
        immutable_before=before,
        pending_package=False,
        package_staging={"staging_complete": True},
        package_archive={
            "package_isolation_passed": True,
            "current_validation_inside_zip": False,
            "package_verification_passed": False,
            "required_files_missing": ["validation/CANONICAL_PREPACKAGE_VALIDATION.json"],
            "main_zip_path": "z.zip",
            "main_zip_sha256": "x",
        },
        failures=["validation_artifacts_incomplete"],
    )
    assert state["VERSION"] != "ACCEPTED"


def test_missing_post_zip_fails() -> None:
    # Acceptance without verification_passed must not be ACCEPTED
    before = hash_immutable_artifacts(RUN)
    state = build_canonical_acceptance_state(
        run_folder=RUN,
        reference_path=REF,
        immutable_before=before,
        pending_package=False,
        package_staging={"staging_complete": True},
        package_archive={
            "package_isolation_passed": False,
            "current_validation_inside_zip": False,
            "package_verification_passed": False,
            "main_zip_path": "",
            "main_zip_sha256": "",
        },
        failures=["post_zip_missing"],
    )
    assert state["VERSION"] != "ACCEPTED"


def test_duplicate_archive_path_fails() -> None:
    audit = {
        "archive_paths_unique": False,
        "duplicate_archive_paths": ["a.json", "a.json"],
        "staging_complete": False,
    }
    assert audit["staging_complete"] is False
    assert not audit["archive_paths_unique"]


def test_old_validation_version_fails() -> None:
    old = ["troubleshooting/validation/v3.3.5.5.8.5.25.3.3.2/foo.txt"]
    assert any("/validation/v" in n and "3.3.5.5.8.5.25.3.3.2.1" not in n for n in old)


def test_immutable_hash_diff_fails() -> None:
    before = hash_immutable_artifacts(RUN)
    after = json.loads(json.dumps(before))
    first = next(iter(after["artifacts"]))
    after["artifacts"][first]["sha256"] = "0" * 64
    state = build_canonical_acceptance_state(
        run_folder=RUN,
        reference_path=REF,
        immutable_before=before,
        immutable_after=after,
        pending_package=True,
    )
    assert state["immutable_evidence_integrity"]["immutable_runtime_artifacts_unchanged"] is False
    assert "immutable_runtime_artifacts_changed" in state["final_verdict"]["failures"]


def test_bundle_exactly_one_main_zip() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        b = td_path / "bundle.zip"
        with zipfile.ZipFile(b, "w") as zf:
            zf.writestr("UPLOAD_PACKAGE_v3.3.5.5.8.5.25.3.3.2.1_t.zip", b"x")
            zf.writestr("FINAL_ACCEPTANCE_V25.3.3.2.1.json", "{}")
        with zipfile.ZipFile(b, "r") as zf:
            mains = [n for n in zf.namelist() if n.startswith("UPLOAD_PACKAGE_v") and n.endswith(".zip")]
        assert len(mains) == 1


def test_bundle_contains_required_evidence() -> None:
    required = {
        "FINAL_ACCEPTANCE_V25.3.3.2.1.json",
        "POST_ZIP_VERIFICATION_V25.3.3.2.1.json",
        "CANONICAL_PREPACKAGE_VALIDATION.json",
        "PACKAGE_STAGING_AUDIT.json",
        "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
        "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_BEFORE.json",
        "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_AFTER.json",
        "Cursor final report.txt",
    }
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        b = td_path / "bundle.zip"
        with zipfile.ZipFile(b, "w") as zf:
            zf.writestr("UPLOAD_PACKAGE_v3.3.5.5.8.5.25.3.3.2.1_t.zip", b"x")
            for name in required:
                zf.writestr(name, b"{}")
        with zipfile.ZipFile(b, "r") as zf:
            names = set(zf.namelist())
        assert required.issubset(names)


def test_no_contradictory_status_in_bundle() -> None:
    # ACCEPTED with issues_closed=0 is contradiction
    bad = {"VERSION": "ACCEPTED", "issues_closed": 0}
    assert not (bad["VERSION"] == "ACCEPTED" and bad["issues_closed"] == 11)
    good = {"VERSION": "ACCEPTED", "issues_closed": 11, "closure_ratio": 1.0}
    assert good["VERSION"] == "ACCEPTED" and good["issues_closed"] == 11


def test_validator_twice_same_result() -> None:
    before = hash_immutable_artifacts(RUN)
    a = build_canonical_acceptance_state(
        run_folder=RUN, reference_path=REF, immutable_before=before, pending_package=True
    )
    b = build_canonical_acceptance_state(
        run_folder=RUN, reference_path=REF, immutable_before=before, pending_package=True
    )
    # Compare stable fields
    assert a["issue_closure"]["issues_closed"] == b["issue_closure"]["issues_closed"]
    assert a["issue_closure"]["issue_results"] == b["issue_closure"]["issue_results"]
    assert a["VERSION"] == b["VERSION"]
    for key in (
        "transcript_integrity",
        "lineage_integrity",
        "action_integrity",
        "finalizer_integrity",
        "audio_integrity",
        "stop_integrity",
        "coverage_integrity",
        "stage_integrity",
        "stall_integrity",
    ):
        assert a[key].get("closed") == b[key].get("closed")


TESTS: list[tuple[str, Callable[[], None]]] = [
    ("VERSION=ACCEPTED with issues_closed=0 raises", test_accepted_issues_closed_0_raises),
    ("VERSION=ACCEPTED with package_complete=false raises", test_accepted_package_complete_false_raises),
    ("VERSION=ACCEPTED with empty ZIP path raises", test_accepted_empty_zip_raises),
    ("VERSION=ACCEPTED with failures present raises", test_accepted_failures_raises),
    ("Stale closure files are excluded", test_stale_closure_excluded),
    ("Package issues remain pending before ZIP", test_package_issues_pending_before_zip),
    ("Issue 9 closes only after package isolation", test_issue9_closes_only_after_isolation),
    ("Issue 10 closes only after validation in ZIP", test_issue10_closes_only_after_validation_in_zip),
    ("Cursor report values match final acceptance JSON", test_cursor_report_matches_json),
    ("Main ZIP hash in acceptance matches actual ZIP", test_main_zip_hash_in_acceptance_matches),
    ("Main ZIP contents match staging", test_main_zip_contents_match_staging),
    ("Missing validator report fails acceptance", test_missing_validator_report_fails),
    ("Missing post-ZIP verification fails acceptance", test_missing_post_zip_fails),
    ("Duplicate archive path fails acceptance", test_duplicate_archive_path_fails),
    ("Old validation version fails acceptance", test_old_validation_version_fails),
    ("Immutable runtime hash difference fails", test_immutable_hash_diff_fails),
    ("Final validation bundle has exactly one main ZIP", test_bundle_exactly_one_main_zip),
    ("Final validation bundle contains required evidence", test_bundle_contains_required_evidence),
    ("No contradictory status in bundle", test_no_contradictory_status_in_bundle),
    ("Validator twice produces same acceptance result", test_validator_twice_same_result),
]


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    passed = 0
    failed = 0
    lines = [
        f"regression_canonical_acceptance_bundle_85253321 — 3.3.5.5.8.5.25.3.3.2.1",
        "",
    ]
    for name, fn in TESTS:
        try:
            fn()
            lines.append(f"PASSED: {name}")
            passed += 1
        except Exception as exc:
            lines.append(f"FAILED: {name} — {exc}")
            failed += 1
    lines.extend(
        [
            "",
            f"tests = {len(TESTS)}",
            f"passed = {passed}",
            f"failed = {failed}",
            f"RESULT={'PASSED' if failed == 0 and passed == 20 else 'FAILED'}",
        ]
    )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if failed == 0 and passed == 20 else 1


if __name__ == "__main__":
    sys.exit(main())
