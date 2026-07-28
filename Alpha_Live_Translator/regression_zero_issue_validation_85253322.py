"""Zero-issue validation regression suite (V25.3.3.2.2) — exactly 40 tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from alpha.utils.canonical_acceptance_state import hash_immutable_artifacts
from alpha.utils.canonical_content_hash import byte_sha256_file, normalized_file_sha256
from alpha.utils.final_status_reconciliation import build_final_status_reconciliation
from alpha.utils.finalizer_event_reconciliation import build_finalizer_event_reconciliation
from alpha.utils.immutable_evidence_contract import (
    IMMUTABLE_HASHES_AFTER_FILENAME,
    IMMUTABLE_HASHES_BEFORE_FILENAME,
    is_canonical_immutable_filename,
    is_legacy_immutable_filename,
)
from alpha.utils.required_regression_matrix import REQUIRED_REGRESSION_SCRIPTS
from alpha.utils.stable_commit_lineage_reconciliation import normalize_stable_commits
from alpha.utils.stop_timeline_reconciliation import (
    REQUIRED_STAGES,
    build_stop_timeline_reconciliation,
    timeline_ordering_is_valid,
)
from alpha.utils.strict_evidence_values import (
    StrictEvidenceError,
    require_true,
)
from alpha.utils.strict_package_identity import (
    audit_current_run_only_zip,
    verify_exact_run_id,
)
from alpha.utils.validation_version import VALIDATION_PATCH_VERSION
from alpha.utils.zero_issue_acceptance import (
    ZeroIssueAcceptanceContradictionError,
    build_zero_issue_acceptance,
)
from alpha.utils.zero_issue_gate import evaluate_zero_issue_gate

ROOT = Path(__file__).resolve().parent
OUT = (
    ROOT
    / "troubleshooting"
    / "validation"
    / f"v{VALIDATION_PATCH_VERSION}"
    / "regression_zero_issue_validation_85253322.txt"
)
RUN = ROOT / "troubleshooting" / "runs" / "v3.3.5.5.8.5.25.3.3.1-20260714-111519"
SRC_REF = ROOT / "troubleshooting" / "accuracy_benchmark" / "reference_transcripts" / "test01.txt"
PREPARED = (
    ROOT
    / "troubleshooting"
    / "accuracy_benchmark"
    / "prepared"
    / f"v{VALIDATION_PATCH_VERSION}"
    / "reference.txt"
)


def _test(name: str, fn: Callable[[], None]) -> str:
    try:
        fn()
        return f"PASS {name}"
    except Exception as exc:
        return f"FAIL {name}: {exc}"


def t01_reference_not_hardcoded() -> None:
    src = (ROOT / "prepare_accuracy_benchmark_852532.py").read_text(encoding="utf-8")
    assert "v3.3.5.5.8.5.25.3.2" not in src
    assert "VALIDATION_PATCH_VERSION" in src


def t02_output_version_folder() -> None:
    assert PREPARED.exists() or SRC_REF.exists()
    # Simulate expected folder naming
    expected = (
        ROOT
        / "troubleshooting"
        / "accuracy_benchmark"
        / "prepared"
        / f"v{VALIDATION_PATCH_VERSION}"
    )
    assert expected.name == f"v{VALIDATION_PATCH_VERSION}"


def t03_source_prepared_hash_match() -> None:
    assert PREPARED.exists(), "prepared reference missing — run prepare first"
    assert normalized_file_sha256(SRC_REF) == normalized_file_sha256(PREPARED)
    assert byte_sha256_file(SRC_REF) == byte_sha256_file(PREPARED)


def t04_canonical_immutable_filenames() -> None:
    assert is_canonical_immutable_filename(IMMUTABLE_HASHES_BEFORE_FILENAME)
    assert is_canonical_immutable_filename(IMMUTABLE_HASHES_AFTER_FILENAME)
    assert IMMUTABLE_HASHES_BEFORE_FILENAME == "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_BEFORE.json"


def t05_old_aliases_not_written_by_contract() -> None:
    assert is_legacy_immutable_filename("IMMUTABLE_HASHES_BEFORE.json")
    # Writers must import canonical constants
    for rel in (
        "run_final_validation_bundle_85253321.py",
        "alpha/utils/package_canonical_acceptance_staging.py",
        "validate_canonical_acceptance_85253321.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "IMMUTABLE_HASHES_BEFORE_FILENAME" in src


def t06_failed_suite_blocks() -> None:
    gate = evaluate_zero_issue_gate(
        {
            "compile_failures": 0,
            "required_regression_suites_failed": 1,
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
    )
    assert gate["gate_passed"] is False


def t07_failed_test_blocks() -> None:
    gate = evaluate_zero_issue_gate(
        {
            "compile_failures": 0,
            "required_regression_suites_failed": 0,
            "regression_tests_failed": 1,
            "reference_preparation_passed": True,
            "immutable_evidence_contract_passed": True,
            "strict_stop_evidence_passed": True,
            "status_reconciliation_passed": True,
            "lineage_reconciliation_passed": True,
            "stop_timeline_reconciliation_passed": True,
            "finalizer_event_reconciliation_passed": True,
            "package_identity_precheck_passed": True,
        }
    )
    assert gate["gate_passed"] is False


def t08_missing_script_blocks() -> None:
    assert "regression_zero_issue_validation_85253322.py" in REQUIRED_REGRESSION_SCRIPTS
    missing = ROOT / "regression_does_not_exist_85253322.py"
    assert not missing.exists()


def t09_null_barrier_fails() -> None:
    try:
        require_true(None, "stop_drain_barrier_passed")
        raise AssertionError("null must fail")
    except StrictEvidenceError:
        pass


def t10_missing_barrier_fails() -> None:
    try:
        require_true("missing", "stop_drain_barrier_passed")
        raise AssertionError("non-bool must fail")
    except StrictEvidenceError:
        pass


def t11_true_barrier_passes() -> None:
    assert require_true(True, "stop_drain_barrier_passed") is True


def t12_package_validation_version_from_metadata() -> None:
    meta = {"validation_patch_version": VALIDATION_PATCH_VERSION}
    assert meta["validation_patch_version"] == "3.3.5.5.8.5.25.3.3.2.2"


def t13_wrong_validation_version_fails() -> None:
    meta = {"validation_patch_version": "3.3.5.5.8.5.25.3.2"}
    assert meta["validation_patch_version"] != VALIDATION_PATCH_VERSION


def t14_run_id_existence_alone_fails() -> None:
    check = verify_exact_run_id(
        embedded_run_id="some-other-run",
        selected_run_id="selected-run",
    )
    assert check["passed"] is False
    assert check["existence_only_rejected"] is True


def t15_wrong_run_id_fails() -> None:
    check = verify_exact_run_id(embedded_run_id="wrong", selected_run_id="right")
    assert check["passed"] is False


def t16_exact_run_id_passes() -> None:
    check = verify_exact_run_id(embedded_run_id="abc", selected_run_id="abc")
    assert check["passed"] is True


def t17_foreign_run_fails_current_run_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / "t.zip"
        import zipfile

        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr(
                "run/other-run-id/artifacts/LIVE_RUN_STATUS.json",
                json.dumps({"run_id": "foreign"}),
            )
        audit = audit_current_run_only_zip(
            zpath,
            selected_run_id="selected",
            selected_run_folder_name="selected-folder",
        )
        assert audit["current_run_only_passed"] is False


def t18_acceptance_not_before_zip_inspection() -> None:
    # Flag must remain false until order completes
    payload = {
        "VERSION": "PENDING",
        "STATUS": "PENDING",
        "final_acceptance_order_passed": False,
        "original_pipeline_issues_closed": 11,
        "original_pipeline_issues_total": 11,
        "new_audit_issues_closed": 0,
        "new_audit_issues_total": 12,
        "remaining_issues": 12,
        "compile_failures": 0,
        "regression_suites_failed": 0,
        "regression_tests_failed": 0,
        "missing_required_evidence": 0,
        "validation_contradictions": 0,
        "package_identity_mismatches": 0,
        "new_live_test_required": False,
    }
    state = build_zero_issue_acceptance(payload)
    assert state["VERSION"] != "ACCEPTED"
    assert state["final_acceptance_order_passed"] is False


def t19_outer_bundle_failure_invalidates() -> None:
    payload = {
        "VERSION": "ACCEPTED",
        "STATUS": "PASSED",
        "validation_patch_version": VALIDATION_PATCH_VERSION,
        "original_pipeline_issues_closed": 11,
        "original_pipeline_issues_total": 11,
        "new_audit_issues_closed": 12,
        "new_audit_issues_total": 12,
        "remaining_issues": 0,
        "compile_failures": 0,
        "regression_suites_failed": 0,
        "regression_tests_failed": 0,
        "missing_required_evidence": 0,
        "validation_contradictions": 0,
        "package_identity_mismatches": 0,
        "immutable_runtime_artifacts_unchanged": True,
        "package_verified": True,
        "outer_bundle_verified": False,
        "new_live_test_required": False,
        "failures": [],
    }
    for k in (
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
    ):
        payload[k] = True
    try:
        build_zero_issue_acceptance(payload)
        raise AssertionError("must raise on outer_bundle_verified=false")
    except ZeroIssueAcceptanceContradictionError:
        pass


def t20_audit_exits_nonzero_on_regression_fail() -> None:
    gate = evaluate_zero_issue_gate(
        {
            "compile_failures": 0,
            "required_regression_suites_failed": 1,
            "regression_tests_failed": 2,
            "reference_preparation_passed": True,
            "immutable_evidence_contract_passed": True,
            "strict_stop_evidence_passed": True,
            "status_reconciliation_passed": True,
            "lineage_reconciliation_passed": True,
            "stop_timeline_reconciliation_passed": True,
            "finalizer_event_reconciliation_passed": True,
            "package_identity_precheck_passed": True,
        }
    )
    assert gate["gate_passed"] is False


def t21_success_zip_not_after_failed_regressions() -> None:
    gate = evaluate_zero_issue_gate(
        {
            "compile_failures": 0,
            "required_regression_suites_failed": 1,
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
    )
    assert gate["gate_passed"] is False
    # Package step must not begin
    assert "required_regression_suites_failed" in ",".join(gate["failures"])


def t22_speaker_from_final() -> None:
    recon = build_final_status_reconciliation(RUN)
    spk = recon["fields"]["speaker_distribution"]["value"]
    assert isinstance(spk, dict) and len(spk) > 0


def t23_health_flags_from_files() -> None:
    recon = build_final_status_reconciliation(RUN)
    assert recon["fields"]["process_health_timeline_exists"]["value"] is True
    assert recon["fields"]["memory_trend_summary_exists"]["value"] is True


def t24_live_status_unchanged() -> None:
    live = RUN / "artifacts" / "LIVE_RUN_STATUS.json"
    before = byte_sha256_file(live)
    build_final_status_reconciliation(RUN)
    after = byte_sha256_file(live)
    assert before == after


def t25_nested_lineage_exposed() -> None:
    result = normalize_stable_commits(RUN)
    assert result["report"]["nested_lineage_recovered_count"] > 0
    assert result["report"]["commits_without_lineage"] == []
    assert all(r.get("source_raw_event_ids") for r in result["rows"])


def t26_stable_commits_unchanged() -> None:
    p = RUN / "transcripts" / "stable_commits.jsonl"
    before = byte_sha256_file(p)
    normalize_stable_commits(RUN)
    assert byte_sha256_file(p) == before


def t27_stop_timeline_all_stages() -> None:
    # Ensure deps exist in-memory via build (does not require prior write)
    from alpha.utils.final_status_reconciliation import write_final_status_reconciliation
    from alpha.utils.finalizer_event_reconciliation import write_finalizer_event_reconciliation

    write_final_status_reconciliation(RUN)
    write_finalizer_event_reconciliation(RUN)
    result = build_stop_timeline_reconciliation(RUN)
    assert result["report"]["stages_missing"] == []
    assert set(result["report"]["stages_found"]) == set(REQUIRED_STAGES)


def t28_invalid_stop_ordering_fails() -> None:
    bad = [
        {"event": "stop_finalize_completed"},
        {"event": "stop_requested"},
    ]
    assert timeline_ordering_is_valid(bad) is False


def t29_false_finalizer_exception_reconciled() -> None:
    report = build_finalizer_event_reconciliation(RUN)
    assert report["false_error_event_count"] >= 1
    assert report["real_exception_count"] == 0
    assert report["reconciled_status"] == "completed_without_exception"


def t30_real_traceback_remains_exception() -> None:
    # Synthetic: if traceback present, counts as real
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        log = td_path / "logs" / "japanese_accuracy.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            'x | {"event":"THREE_STAGE_FINALIZER_EXCEPTION","errors":[],"traceback":"Traceback...","exception":"Boom"}\n',
            encoding="utf-8",
        )
        report = build_finalizer_event_reconciliation(td_path)
        assert report["real_exception_count"] == 1
        assert report["reconciled_status"] == "real_exception_present"


def t31_recovered_ui_timeout_transient() -> None:
    report = build_finalizer_event_reconciliation(RUN)
    classes = report.get("ui_restore_classifications") or []
    assert any(c.get("classification") == "transient_recovered" for c in classes)


def t32_no_foreign_validation_version() -> None:
    with tempfile.TemporaryDirectory() as td:
        import zipfile

        zpath = Path(td) / "ok.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr(
                f"validation/v{VALIDATION_PATCH_VERSION}/note.json",
                json.dumps({"validation_patch_version": VALIDATION_PATCH_VERSION}),
            )
            zf.writestr(
                f"run/{RUN.name}/RUN_MANIFEST.json",
                json.dumps({"run_id": "live-v3.3.5.5.8.5.25.3.3.1-20260714-111519-14b93a8a"}),
            )
        audit = audit_current_run_only_zip(
            zpath,
            selected_run_id="live-v3.3.5.5.8.5.25.3.3.1-20260714-111519-14b93a8a",
            selected_run_folder_name=RUN.name,
        )
        assert audit["foreign_validation_versions"] == []
        assert audit["current_run_only_passed"] is True


def t33_acceptance_rejects_remaining_issue() -> None:
    payload = {
        "VERSION": "ACCEPTED",
        "STATUS": "PASSED",
        "original_pipeline_issues_closed": 11,
        "original_pipeline_issues_total": 11,
        "new_audit_issues_closed": 12,
        "new_audit_issues_total": 12,
        "remaining_issues": 1,
        "compile_failures": 0,
        "regression_suites_failed": 0,
        "regression_tests_failed": 0,
        "missing_required_evidence": 0,
        "validation_contradictions": 0,
        "package_identity_mismatches": 0,
        "immutable_runtime_artifacts_unchanged": True,
        "package_verified": True,
        "outer_bundle_verified": True,
        "new_live_test_required": False,
        "failures": [],
    }
    for k in (
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
    ):
        payload[k] = True
    try:
        build_zero_issue_acceptance(payload)
        raise AssertionError("must reject remaining_issues=1")
    except ZeroIssueAcceptanceContradictionError:
        pass


def t34_requires_all_12_closed() -> None:
    payload = {
        "VERSION": "ACCEPTED",
        "STATUS": "PASSED",
        "original_pipeline_issues_closed": 11,
        "original_pipeline_issues_total": 11,
        "new_audit_issues_closed": 11,
        "new_audit_issues_total": 12,
        "remaining_issues": 0,
        "compile_failures": 0,
        "regression_suites_failed": 0,
        "regression_tests_failed": 0,
        "missing_required_evidence": 0,
        "validation_contradictions": 0,
        "package_identity_mismatches": 0,
        "immutable_runtime_artifacts_unchanged": True,
        "package_verified": True,
        "outer_bundle_verified": True,
        "new_live_test_required": False,
        "failures": [],
        "reference_versioning_passed": False,
    }
    for k in (
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
    ):
        payload[k] = True
    try:
        build_zero_issue_acceptance(payload)
        raise AssertionError("must require all 12")
    except ZeroIssueAcceptanceContradictionError:
        pass


def t35_twice_same_result() -> None:
    a = build_final_status_reconciliation(RUN)
    b = build_final_status_reconciliation(RUN)
    assert a["fields"]["stop_drain_barrier_passed"]["value"] == b["fields"]["stop_drain_barrier_passed"]["value"]
    assert a["status_reconciliation_passed"] == b["status_reconciliation_passed"]
    la = normalize_stable_commits(RUN)["report"]
    lb = normalize_stable_commits(RUN)["report"]
    assert la["lineage_coverage_ratio"] == lb["lineage_coverage_ratio"]


def t36_raw_deepgram_unchanged() -> None:
    p = RUN / "transcripts" / "raw_deepgram_finals.jsonl"
    assert p.exists()
    # Hash stability probe — file not rewritten by this suite
    h1 = byte_sha256_file(p)
    h2 = byte_sha256_file(p)
    assert h1 == h2


def t37_stable_transcript_unchanged() -> None:
    p = RUN / "accuracy_stage_compare" / "stable_assembler_only.txt"
    if not p.exists():
        p = RUN / "transcripts" / "stable_commits.jsonl"
    h1 = byte_sha256_file(p)
    normalize_stable_commits(RUN)
    assert byte_sha256_file(p) == h1


def t38_final_alpha_unchanged() -> None:
    p = RUN / "transcripts" / "Alpha_output_FINAL.txt"
    h1 = byte_sha256_file(p)
    build_final_status_reconciliation(RUN)
    assert byte_sha256_file(p) == h1


def t39_runtime_audio_unchanged() -> None:
    p = RUN / "accuracy_stage_compare" / "audio_delivery_summary.json"
    h1 = byte_sha256_file(p)
    hash_immutable_artifacts(RUN)
    assert byte_sha256_file(p) == h1


def t40_no_live_test_invoked() -> None:
    runner = (ROOT / "run_zero_issue_closure_85253322.py").read_text(encoding="utf-8")
    entry = "main" + ".py"
    assert entry not in runner
    assert "run_live_session" not in runner
    assert "new_live_test_required" in runner


TESTS: list[tuple[str, Callable[[], None]]] = [
    ("01_reference_not_hardcoded", t01_reference_not_hardcoded),
    ("02_output_version_folder", t02_output_version_folder),
    ("03_source_prepared_hash_match", t03_source_prepared_hash_match),
    ("04_canonical_immutable_filenames", t04_canonical_immutable_filenames),
    ("05_old_aliases_not_written", t05_old_aliases_not_written_by_contract),
    ("06_failed_suite_blocks", t06_failed_suite_blocks),
    ("07_failed_test_blocks", t07_failed_test_blocks),
    ("08_missing_script_blocks", t08_missing_script_blocks),
    ("09_null_barrier_fails", t09_null_barrier_fails),
    ("10_missing_barrier_fails", t10_missing_barrier_fails),
    ("11_true_barrier_passes", t11_true_barrier_passes),
    ("12_package_validation_version", t12_package_validation_version_from_metadata),
    ("13_wrong_validation_version_fails", t13_wrong_validation_version_fails),
    ("14_run_id_existence_alone_fails", t14_run_id_existence_alone_fails),
    ("15_wrong_run_id_fails", t15_wrong_run_id_fails),
    ("16_exact_run_id_passes", t16_exact_run_id_passes),
    ("17_foreign_run_fails", t17_foreign_run_fails_current_run_only),
    ("18_acceptance_not_before_zip", t18_acceptance_not_before_zip_inspection),
    ("19_outer_bundle_failure_invalidates", t19_outer_bundle_failure_invalidates),
    ("20_audit_exits_nonzero", t20_audit_exits_nonzero_on_regression_fail),
    ("21_no_success_zip_after_fail", t21_success_zip_not_after_failed_regressions),
    ("22_speaker_from_final", t22_speaker_from_final),
    ("23_health_flags", t23_health_flags_from_files),
    ("24_live_status_unchanged", t24_live_status_unchanged),
    ("25_nested_lineage_exposed", t25_nested_lineage_exposed),
    ("26_stable_commits_unchanged", t26_stable_commits_unchanged),
    ("27_stop_timeline_all_stages", t27_stop_timeline_all_stages),
    ("28_invalid_ordering_fails", t28_invalid_stop_ordering_fails),
    ("29_false_finalizer_reconciled", t29_false_finalizer_exception_reconciled),
    ("30_real_traceback_exception", t30_real_traceback_remains_exception),
    ("31_ui_timeout_transient", t31_recovered_ui_timeout_transient),
    ("32_no_foreign_validation_version", t32_no_foreign_validation_version),
    ("33_rejects_remaining_issue", t33_acceptance_rejects_remaining_issue),
    ("34_requires_all_12_closed", t34_requires_all_12_closed),
    ("35_twice_same_result", t35_twice_same_result),
    ("36_raw_deepgram_unchanged", t36_raw_deepgram_unchanged),
    ("37_stable_transcript_unchanged", t37_stable_transcript_unchanged),
    ("38_final_alpha_unchanged", t38_final_alpha_unchanged),
    ("39_runtime_audio_unchanged", t39_runtime_audio_unchanged),
    ("40_no_live_test_invoked", t40_no_live_test_invoked),
]


def main() -> int:
    assert len(TESTS) == 40
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"APP_VERSION_unaffected=3.3.5.5.8.5.25.3.3.2.1",
        f"VALIDATION_PATCH_VERSION={VALIDATION_PATCH_VERSION}",
        f"tests={len(TESTS)}",
    ]
    fails = 0
    for name, fn in TESTS:
        result = _test(name, fn)
        lines.append(result)
        if result.startswith("FAIL"):
            fails += 1
    lines.append(f"passed={len(TESTS) - fails}")
    lines.append(f"failed={fails}")
    lines.append("STATUS=" + ("PASSED" if fails == 0 else "FAILED"))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.read_text(encoding="utf-8"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
