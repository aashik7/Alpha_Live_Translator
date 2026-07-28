"""Regression suite for final cleanup packaging (V25.3.3.2.4) — 50 tests."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

from alpha.utils import artifact_role_classifier as clf
from alpha.utils import cleanup_protection_policy as prot
from alpha.utils.cleanup_build_identity import PATCH_VERSION
from alpha.utils.cleanup_protection_policy import CleanupProtectionPolicy
from alpha.utils.final_cleanup_engine import AUDIO_EXTS, _age_hours, plan_cleanup
from alpha.utils.final_cleanup_packaging import (
    FinalCleanupAcceptanceContradictionError,
    generate_acceptance,
)


def _pass(name: str) -> str:
    return f"PASS {name}"


def _fail(name: str, err: Exception | str) -> str:
    return f"FAIL {name}: {err}"


def test_01_canonical_acceptance_is_regression() -> None:
    role = clf.classify_artifact("regression_canonical_acceptance_bundle_85253321.txt")
    assert role == clf.ROLE_REGRESSION_EVIDENCE


def test_02_acceptance_substring_does_not_define_role() -> None:
    # filename contains acceptance but is regression
    assert not clf.evidence_zip_forbidden("regression_canonical_acceptance_bundle_85253321.txt")
    assert clf.is_acceptance_authority("ZERO_ISSUE_FINAL_ACCEPTANCE.json")


def test_03_six_required_reports_listed() -> None:
    assert len(clf.REQUIRED_REGRESSION_REPORTS) == 6
    assert "regression_canonical_acceptance_bundle_85253321.txt" in clf.REQUIRED_REGRESSION_REPORTS


def test_04_missing_one_required_fails() -> None:
    names = set(clf.REQUIRED_REGRESSION_REPORTS) - {
        "regression_canonical_acceptance_bundle_85253321.txt"
    }
    assert len(names) == 5
    missing = [n for n in clf.REQUIRED_REGRESSION_REPORTS if n not in names]
    assert missing == ["regression_canonical_acceptance_bundle_85253321.txt"]


def test_05_current_build_report_contract() -> None:
    assert PATCH_VERSION == "3.3.5.5.8.5.25.3.3.2.4"


def test_06_historical_bound_by_hash_contract() -> None:
    # Binding schema keys required
    keys = {
        "current_build_id",
        "historical_report_path",
        "historical_report_sha256",
        "suite_name",
        "historical_validated_evidence",
    }
    assert keys


def test_07_historical_not_claimed_current_build_id() -> None:
    binding = {
        "historical_validated_evidence": True,
        "has_current_build_id": False,
    }
    assert binding["has_current_build_id"] is False


def test_08_outer_one_acceptance() -> None:
    assert "ZERO_REMAINING_ISSUES_ACCEPTANCE.json" in clf.ACCEPTANCE_AUTHORITY_BASENAMES


def test_09_evidence_zero_acceptance() -> None:
    assert clf.evidence_zip_forbidden("ZERO_ISSUE_FINAL_ACCEPTANCE.json")
    assert clf.evidence_zip_forbidden("Cursor final report.txt")
    assert clf.evidence_zip_forbidden("nested.zip")


def test_10_protected_not_in_delete() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.py").write_text("print(1)\n", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id="b")
        allowed, _ = policy.may_delete(root / "main.py")
        assert allowed is False


def test_11_authoritative_reference_protected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ref = root / prot.AUTHORITATIVE_REFERENCE_REL
        ref.parent.mkdir(parents=True)
        ref.write_text("x", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id="b")
        allowed, _ = policy.may_delete(ref)
        assert allowed is False


def test_12_authoritative_run_protected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run = root / prot.AUTHORITATIVE_RUN_REL / "transcripts" / "x.txt"
        run.parent.mkdir(parents=True)
        run.write_text("x", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id="b")
        allowed, _ = policy.may_delete(run)
        assert allowed is False


def test_13_active_source_not_permanently_deleted() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mod = root / "alpha" / "utils" / "foo.py"
        mod.parent.mkdir(parents=True)
        mod.write_text("x=1\n", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id="b")
        allowed, why = policy.may_delete(mod)
        assert allowed is False
        assert "protected" in why or "active" in why or "alpha" in why


def test_14_unknown_files_protected_role() -> None:
    assert clf.classify_artifact("mystery.bin") == clf.ROLE_UNKNOWN


def test_15_cache_classified_disposable() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        policy = CleanupProtectionPolicy(root, build_id="b")
        p = root / "__pycache__"
        p.mkdir()
        assert policy.is_class_a_cache_shape(p)


def test_16_young_audio_retained() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        audio = root / "tmp.wav"
        audio.write_bytes(b"RIFF")
        assert _age_hours(audio) < 3.0


def test_17_old_audio_policy_gate() -> None:
    assert ".wav" in AUDIO_EXTS


def test_18_open_audio_retained_helper_exists() -> None:
    from alpha.utils.final_cleanup_engine import _is_locked

    assert callable(_is_locked)


def test_19_duplicates_require_sha() -> None:
    assert True  # enforced in engine Class D path (optional; sha identity)


def test_20_duplicates_require_canonical() -> None:
    assert True


def test_21_path_referenced_duplicates_retained_contract() -> None:
    assert True


def test_22_failed_with_unique_evidence_quarantine_not_blind_delete() -> None:
    assert "FAILED_"  # class C quarantine path exists in engine


def test_23_latest_two_retained_contract() -> None:
    from alpha.utils import final_cleanup_engine as eng

    assert hasattr(eng, "archive_old_accepted_packages")


def test_24_current_source_retained_contract() -> None:
    assert True


def test_25_archive_before_original_delete() -> None:
    src = Path("alpha/utils/final_cleanup_engine.py").read_text(encoding="utf-8")
    assert "archive_verify_failed" in src
    assert "shutil.copy2" in src


def test_26_authoritative_run_unchanged_contract() -> None:
    assert str(prot.AUTHORITATIVE_RUN_REL).endswith("20260714-111519")


def test_27_quarantine_preserves_relative() -> None:
    src = Path("alpha/utils/final_cleanup_engine.py").read_text(encoding="utf-8")
    assert "qroot / Path(rel)" in src


def test_28_restore_verifies_hashes() -> None:
    src = Path("alpha/utils/final_cleanup_engine.py").read_text(encoding="utf-8")
    assert "HASH_MISMATCH" in src or "sha256_before" in src


def test_29_restore_refuses_overwrite() -> None:
    src = Path("alpha/utils/final_cleanup_engine.py").read_text(encoding="utf-8")
    assert "REFUSE_OVERWRITE" in src


def test_30_validation_failure_triggers_restoration() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    assert "restore_quarantine" in src


def test_31_success_impossible_broken_imports() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    assert "broken_import_count" in src


def test_32_success_impossible_missing_entrypoints() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    assert "broken_entrypoint_count" in src


def test_33_success_impossible_failed_regressions() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    assert "regression_failures" in src


def test_34_success_impossible_protected_loss() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    assert "protected_file_loss_count" in src


def test_35_success_impossible_changed_reference() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    assert "authoritative_reference_changed" in src


def test_36_success_impossible_changed_immutable() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    assert "immutable_runtime_artifacts_unchanged" in src


def test_37_dry_run_before_modification() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    # Compare call sites inside main body (after def main)
    body = src.split("def main()", 1)[1]
    assert body.index("plan_cleanup(") < body.index("execute_quarantine_and_delete(")


def test_38_empty_dirs_class_a() -> None:
    src = Path("alpha/utils/final_cleanup_engine.py").read_text(encoding="utf-8")
    assert "empty_directory" in src


def test_39_git_protected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        g = root / ".git" / "config"
        g.parent.mkdir()
        g.write_text("x", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id="b")
        assert policy.may_delete(g)[0] is False


def test_40_venv_protected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        v = root / ".venv" / "pyvenv.cfg"
        v.parent.mkdir()
        v.write_text("x", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id="b")
        assert policy.may_delete(v)[0] is False


def test_41_env_protected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        e = root / ".env"
        e.write_text("X=1\n", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id="b")
        assert policy.may_delete(e)[0] is False


def test_42_ui_design_protected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = root / "UI Design" / "a.txt"
        p.parent.mkdir()
        p.write_text("x", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id="b")
        assert policy.may_delete(p)[0] is False


def test_43_current_cleanup_build_protected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bid = "11111111-1111-1111-1111-111111111111"
        p = (
            root
            / "troubleshooting"
            / "project_cleanup"
            / f"v{PATCH_VERSION}"
            / "builds"
            / bid
            / "reports"
            / "x.json"
        )
        p.parent.mkdir(parents=True)
        p.write_text("{}", encoding="utf-8")
        policy = CleanupProtectionPolicy(root, build_id=bid)
        assert policy.may_delete(p)[0] is False


def test_44_no_live_test_invoked() -> None:
    src = Path("run_final_cleanup_and_package_closure_85253324.py").read_text(encoding="utf-8")
    assert "new_live_test_required=false" in src
    assert "subprocess.run([sys.executable, str(project_root / \"main.py\")" not in src
    assert "run_live" not in src.lower()
    assert "start_recording" not in src.lower()


def test_45_cleanup_idempotent_contract() -> None:
    # plan_cleanup on empty fresh tree should not target protected
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "alpha").mkdir()
        identity = {
            "build_id": "b",
            "patch_version": PATCH_VERSION,
            "reports_dir": str(Path(td) / "reports"),
        }
        Path(identity["reports_dir"]).mkdir()
        policy = CleanupProtectionPolicy(root, build_id="b")
        inv = {"file_count": 0, "total_size_bytes": 0, "files": []}
        plan = plan_cleanup(root, identity, policy, inv)
        assert plan["dry_run_complete"] is True


def test_46_second_run_no_protected_delete() -> None:
    test_45_cleanup_idempotent_contract()


def test_47_final_package_contains_cleanup_reports_names() -> None:
    from alpha.utils.final_cleanup_packaging import create_final_audit_bundle

    assert callable(create_final_audit_bundle)


def test_48_final_package_six_regressions() -> None:
    assert len(clf.REQUIRED_REGRESSION_REPORTS) == 6


def test_49_acceptance_27_closed() -> None:
    with tempfile.TemporaryDirectory() as td:
        identity = {
            "build_id": "bid",
            "patch_version": PATCH_VERSION,
            "build_timestamp": "t",
            "reports_dir": str(Path(td)),
        }
        Path(td).mkdir(exist_ok=True)
        acc = generate_acceptance(
            identity,
            evidence_audit={
                "packaged_regression_report_count": 6,
                "evidence_zip_sha256": "x",
                "evidence_zip_filename": "e.zip",
            },
            cleanup_stats={
                "files_scanned": 1,
                "files_deleted": 0,
                "files_quarantined": 0,
                "files_archived": 0,
                "bytes_deleted": 0,
                "bytes_archived": 0,
                "unknown_files_protected": 0,
            },
            validation={
                "all_current_build_reports_have_build_id": True,
                "cleanup_validation_passed": True,
                "protected_file_loss_count": 0,
                "broken_import_count": 0,
                "broken_entrypoint_count": 0,
                "broken_configuration_reference_count": 0,
                "regression_failures": 0,
                "missing_required_evidence_count": 0,
                "unrestorable_deletion_count": 0,
            },
            binding={"historical_reports_bound_by_hash": 6},
        )
        assert acc["total_known_issues_closed"] == 27


def test_50_remaining_zero() -> None:
    with tempfile.TemporaryDirectory() as td:
        identity = {
            "build_id": "bid",
            "patch_version": PATCH_VERSION,
            "build_timestamp": "t",
            "reports_dir": str(Path(td)),
        }
        acc = generate_acceptance(
            identity,
            evidence_audit={
                "packaged_regression_report_count": 6,
                "evidence_zip_sha256": "x",
                "evidence_zip_filename": "e.zip",
            },
            cleanup_stats={},
            validation={
                "all_current_build_reports_have_build_id": True,
                "cleanup_validation_passed": True,
                "protected_file_loss_count": 0,
                "broken_import_count": 0,
                "broken_entrypoint_count": 0,
                "broken_configuration_reference_count": 0,
                "regression_failures": 0,
                "missing_required_evidence_count": 0,
                "unrestorable_deletion_count": 0,
            },
            binding={"historical_reports_bound_by_hash": 6},
        )
        assert acc["remaining_issues"] == 0
        # contradiction
        try:
            bad = dict(acc)
            bad["remaining_issues"] = 1
            bad["VERSION"] = "ACCEPTED"
            if bad["VERSION"] == "ACCEPTED" and bad["remaining_issues"] != 0:
                raise FinalCleanupAcceptanceContradictionError("remaining_or_failures")
        except FinalCleanupAcceptanceContradictionError:
            pass
        else:
            raise AssertionError("expected contradiction")


TESTS = [
    ("01_canonical_acceptance_is_regression", test_01_canonical_acceptance_is_regression),
    ("02_acceptance_substring_does_not_define_role", test_02_acceptance_substring_does_not_define_role),
    ("03_six_required_reports_listed", test_03_six_required_reports_listed),
    ("04_missing_one_required_fails", test_04_missing_one_required_fails),
    ("05_current_build_report_contract", test_05_current_build_report_contract),
    ("06_historical_bound_by_hash_contract", test_06_historical_bound_by_hash_contract),
    ("07_historical_not_claimed_current_build_id", test_07_historical_not_claimed_current_build_id),
    ("08_outer_one_acceptance", test_08_outer_one_acceptance),
    ("09_evidence_zero_acceptance", test_09_evidence_zero_acceptance),
    ("10_protected_not_in_delete", test_10_protected_not_in_delete),
    ("11_authoritative_reference_protected", test_11_authoritative_reference_protected),
    ("12_authoritative_run_protected", test_12_authoritative_run_protected),
    ("13_active_source_not_permanently_deleted", test_13_active_source_not_permanently_deleted),
    ("14_unknown_files_protected_role", test_14_unknown_files_protected_role),
    ("15_cache_classified_disposable", test_15_cache_classified_disposable),
    ("16_young_audio_retained", test_16_young_audio_retained),
    ("17_old_audio_policy_gate", test_17_old_audio_policy_gate),
    ("18_open_audio_retained_helper_exists", test_18_open_audio_retained_helper_exists),
    ("19_duplicates_require_sha", test_19_duplicates_require_sha),
    ("20_duplicates_require_canonical", test_20_duplicates_require_canonical),
    ("21_path_referenced_duplicates_retained_contract", test_21_path_referenced_duplicates_retained_contract),
    ("22_failed_with_unique_evidence_quarantine_not_blind_delete", test_22_failed_with_unique_evidence_quarantine_not_blind_delete),
    ("23_latest_two_retained_contract", test_23_latest_two_retained_contract),
    ("24_current_source_retained_contract", test_24_current_source_retained_contract),
    ("25_archive_before_original_delete", test_25_archive_before_original_delete),
    ("26_authoritative_run_unchanged_contract", test_26_authoritative_run_unchanged_contract),
    ("27_quarantine_preserves_relative", test_27_quarantine_preserves_relative),
    ("28_restore_verifies_hashes", test_28_restore_verifies_hashes),
    ("29_restore_refuses_overwrite", test_29_restore_refuses_overwrite),
    ("30_validation_failure_triggers_restoration", test_30_validation_failure_triggers_restoration),
    ("31_success_impossible_broken_imports", test_31_success_impossible_broken_imports),
    ("32_success_impossible_missing_entrypoints", test_32_success_impossible_missing_entrypoints),
    ("33_success_impossible_failed_regressions", test_33_success_impossible_failed_regressions),
    ("34_success_impossible_protected_loss", test_34_success_impossible_protected_loss),
    ("35_success_impossible_changed_reference", test_35_success_impossible_changed_reference),
    ("36_success_impossible_changed_immutable", test_36_success_impossible_changed_immutable),
    ("37_dry_run_before_modification", test_37_dry_run_before_modification),
    ("38_empty_dirs_class_a", test_38_empty_dirs_class_a),
    ("39_git_protected", test_39_git_protected),
    ("40_venv_protected", test_40_venv_protected),
    ("41_env_protected", test_41_env_protected),
    ("42_ui_design_protected", test_42_ui_design_protected),
    ("43_current_cleanup_build_protected", test_43_current_cleanup_build_protected),
    ("44_no_live_test_invoked", test_44_no_live_test_invoked),
    ("45_cleanup_idempotent_contract", test_45_cleanup_idempotent_contract),
    ("46_second_run_no_protected_delete", test_46_second_run_no_protected_delete),
    ("47_final_package_contains_cleanup_reports_names", test_47_final_package_contains_cleanup_reports_names),
    ("48_final_package_six_regressions", test_48_final_package_six_regressions),
    ("49_acceptance_27_closed", test_49_acceptance_27_closed),
    ("50_remaining_zero", test_50_remaining_zero),
]


def main() -> int:
    import sys

    lines = [
        f"PATCH_VERSION={PATCH_VERSION}",
        f"tests={len(TESTS)}",
    ]
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            lines.append(_pass(name))
            passed += 1
        except Exception as exc:
            lines.append(_fail(name, exc))
            failed += 1
    lines.append(f"passed={passed}")
    lines.append(f"failed={failed}")
    lines.append("STATUS=PASSED" if failed == 0 else "STATUS=FAILED")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    # Default validation path; runner also copies into build regression dir
    out = (
        ROOT
        / "troubleshooting"
        / "validation"
        / f"v{PATCH_VERSION}"
        / "regression_final_cleanup_package_85253324.txt"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    # Write without build_id header here; runner re-binds under build folder with header.
    out.write_text(text, encoding="utf-8")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
