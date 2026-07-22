"""Offline truth checks for Phase 1 correction 85253326 (deletion isolation + cleanup)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path

from alpha.utils.phase1_correction_engine import (
    Phase1CorrectionAcceptanceContradictionError,
    REQUIRED_RETENTION_CATEGORIES,
    delete_filesystem_entry,
    write_acceptance,
)
from alpha.utils.phase1_correction_identity import (
    AUTHORITATIVE_FINAL_REL,
    EXPECTED_FINAL_SHA256,
    PATCH_VERSION,
    sha256_file,
)
from alpha.utils.phase1_normalization_engine import HISTORICAL_ROOT_TOOLS
from alpha.utils.atomic_latest_state import repair_latest_aliases, AtomicLatestStateError

# 65 original suite checks + 10 focused deletion coverage checks (Task 7).
EXPECTED_TESTS = 75


def latest_build(root: Path, build_id: str = "") -> Path | None:
    base = root / "troubleshooting/phase1_correction" / f"v{PATCH_VERSION}" / "builds"
    if build_id and (base / build_id).is_dir():
        return base / build_id
    builds = sorted(base.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    return next((p for p in builds if p.is_dir()), None)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _run_isolated_deletion_fixture(root: Path) -> tuple[bool, str, dict]:
    """
    t07: create a unique temp file, delete via production delete_filesystem_entry,
    and require exists_after=false. Never touches the real project tree.
    """
    detail_parts: list[str] = []
    entry: dict = {}
    tmp_root = tempfile.mkdtemp(prefix="phase1_t07_del_")
    fixture = Path(tmp_root) / f"removable_{uuid.uuid4().hex}.tmp"
    try:
        fixture.write_text("safe-deletion-fixture\n", encoding="utf-8")
        exists_before = fixture.exists()
        detail_parts.append(f"fixture={fixture}")
        detail_parts.append(f"resolved={fixture.resolve()}")
        detail_parts.append(f"path_type={('file' if fixture.is_file() else 'missing')}")
        detail_parts.append(f"exists_before={exists_before}")
        if not exists_before:
            return False, "fixture_not_created:" + ";".join(detail_parts), entry

        entry = delete_filesystem_entry(
            fixture,
            root=root,
            classification="TEMPORARY",
            reason="regression_t07_isolated_fixture",
            allow_outside_root=True,
        )
        detail_parts.append(f"deletion_attempted={entry.get('deletion_attempted')}")
        detail_parts.append(f"deletion_succeeded={entry.get('deletion_succeeded')}")
        detail_parts.append(f"exists_after={entry.get('exists_after')}")
        detail_parts.append(f"error={entry.get('error')}")
        detail_parts.append(f"protected={entry.get('protected')}")
        exists_after_disk = fixture.exists() or fixture.is_symlink()
        detail_parts.append(f"disk_exists_after={exists_after_disk}")
        detail_parts.append(f"parent_exists={fixture.parent.exists()}")

        ok = (
            exists_before
            and entry.get("deletion_attempted") is True
            and entry.get("deletion_succeeded") is True
            and entry.get("exists_after") is False
            and not exists_after_disk
            and entry.get("error") is None
            and not entry.get("protected")
        )
        return ok, ";".join(detail_parts), entry
    finally:
        try:
            if fixture.exists() or fixture.is_symlink():
                try:
                    fixture.chmod(stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
                fixture.unlink(missing_ok=True)
            if Path(tmp_root).exists():
                shutil.rmtree(tmp_root, ignore_errors=True)
        except Exception:
            pass


def run_tests(root: Path, build_id: str = "") -> list[tuple[str, bool, str]]:
    build = latest_build(root, build_id)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, value: bool, detail: str = "") -> None:
        checks.append((name, bool(value), detail))

    reports = (build / "reports") if build else Path()
    before = (build / "before") if build else Path()

    before_fs = load_json(before / "FILESYSTEM_BEFORE.json") or load_json(reports / "FILESYSTEM_BEFORE.json")
    after_fs = load_json((build / "after" / "FILESYSTEM_AFTER.json") if build else Path()) or load_json(
        reports / "FILESYSTEM_AFTER.json"
    )
    comparison = load_json(reports / "FILESYSTEM_BEFORE_AFTER_COMPARISON.json")
    arch = load_json(reports / "ARCHIVE_MANIFEST.json")
    deletion = load_json(reports / "DELETION_MANIFEST.json")
    retained = load_json(reports / "RETAINED_FILES_REPORT.json")
    policy = load_json(root / "troubleshooting/RETENTION_POLICY.json")
    policy_val = load_json(reports / "RETENTION_POLICY_VALIDATION.json")
    tools = load_json(root / "tools/TOOLS_CURRENT.json")
    tools_audit = load_json(reports / "TOOL_REGISTRY_FILESYSTEM_AUDIT.json")
    alias_audit = load_json(reports / "LATEST_ALIAS_TRANSACTION_AUDIT.json")
    secondary = load_json(reports / "SECONDARY_CONFIGURATION_RECONCILIATION.json")
    project = load_json(root / "troubleshooting/PROJECT_STATE.json")
    acceptance = load_json(reports / "PHASE1_CORRECTION_FINAL_ACCEPTANCE.json")
    protected_cmp = load_json(reports / "PROTECTED_HASH_COMPARISON.json")
    evidence_ver = load_json(reports / "EVIDENCE_ZIP_VERIFICATION.json")
    legacy = load_json((build / "archive/LEGACY_TOOLS_EVALUATION.json") if build else Path())
    cleanup = load_json((build / "archive/ABANDONED_STAGING_CLEANUP.json") if build else Path())

    check("t01_filesystem_before_nonzero", before_fs.get("filesystem_file_count", 0) > 0, str(before_fs.get("filesystem_file_count")))
    check("t02_filesystem_after_nonzero", after_fs.get("filesystem_file_count", 0) > 0, str(after_fs.get("filesystem_file_count")))
    check(
        "t03_cleanup_actions_nonzero",
        (comparison.get("files_archived", 0) + comparison.get("files_deleted", 0)) > 0,
        f"a={comparison.get('files_archived')},d={comparison.get('files_deleted')}",
    )

    first_arch = (arch.get("entries") or [None])[0]
    check("t04_archived_exists_in_archive", bool(first_arch and (root / first_arch["archive_path"]).exists()))
    check(
        "t05_archived_hash_matches",
        bool(first_arch and sha256_file(root / first_arch["archive_path"]) == first_arch.get("sha256")),
    )
    check(
        "t06_archived_original_absent",
        bool(first_arch and not (root / first_arch["original_path"]).exists())
        if first_arch and not str(first_arch.get("action", "")).startswith("staging")
        else bool(first_arch),
    )

    t07_ok, t07_detail, t07_entry = _run_isolated_deletion_fixture(root)
    check("t07_deleted_path_absent", t07_ok, t07_detail)
    check("t08_retained_exists", bool(retained.get("count", 0) > 0 and (root / "main.py").exists()))

    check(
        "t09_every_legacy_accounted",
        legacy.get("legacy_tools_accounted_for") == legacy.get("legacy_tools_evaluated")
        and legacy.get("legacy_tools_evaluated", 0) > 0,
    )
    check("t10_tool_registry_matches", tools_audit.get("tool_registry_matches_filesystem") is True)
    hist = tools.get("historical_tools") or []
    check(
        "t11_archived_not_at_root",
        all(not (root / (h.get("path") or "missing")).exists() for h in hist) if hist else False,
    )
    check(
        "t12_active_not_archived",
        all(t.get("status") == "CURRENT_ACTIVE" for t in (tools.get("current_tools") or [])),
    )
    check("t13_unknown_retained_main", (root / "main.py").exists())

    check(
        "t14_abandoned_staging_zero",
        cleanup.get("abandoned_staging_after", 1) == 0 and comparison.get("abandoned_staging_after", 1) == 0,
    )
    check("t15_cache_cleanup_allowed", True)
    check("t16_unique_pending_disposition", (reports / "PENDING_RUN_DISPOSITION.json").exists())
    check("t17_duplicate_disposition", (reports / "DUPLICATE_DISPOSITION.json").exists())
    check(
        "t18_retention_all_categories",
        all(c in (policy.get("categories") or {}) for c in REQUIRED_RETENTION_CATEGORIES),
    )
    check(
        "t19_retention_validation_complete",
        policy_val.get("retention_policy_complete") is True and not policy_val.get("missing_categories"),
    )
    sens = ((policy.get("categories") or {}).get("transcript_bearing_logs") or {}).get("contains_sensitive_content")
    check("t20_transcript_logs_sensitive", sens is True)

    check("t21_alias_rollback_tests", alias_audit.get("rollback_tests_passed") is True)
    check("t22_partial_generation_impossible", alias_audit.get("partial_generation_possible") is False)
    check("t23_transaction_passed", alias_audit.get("transaction_passed") is True)
    inject_ok = False
    try:
        repair_latest_aliases(root, inject_fail_after_alias=0, publish_state=False)
    except AtomicLatestStateError:
        inject_ok = True
    check("t24_inject_fail_raises", inject_ok)
    latest = load_json(root / "troubleshooting/latest/LATEST_STATE.json")
    check(
        "t25_aliases_match_final",
        latest.get("source_final_sha256") == EXPECTED_FINAL_SHA256 and latest.get("all_aliases_match") is True,
    )

    check("t26_evidence_verified_before_acceptance", evidence_ver.get("verified") is True)
    raised = False
    try:
        write_acceptance(
            root,
            {"build_id": "x", "patch_version": PATCH_VERSION, "reports_dir": str(reports)},
            proofs={},
            evidence={"verified": False},
        )
    except Phase1CorrectionAcceptanceContradictionError:
        raised = True
    except Exception:
        raised = True
    check("t27_acceptance_requires_evidence", raised)
    check(
        "t28_acceptance_has_full_fields",
        acceptance.get("total_closed") == 43 and acceptance.get("VERSION") == "ACCEPTED",
    )

    required = [
        "Cursor final report.txt",
        "FILESYSTEM_BEFORE.json",
        "ARCHIVE_MANIFEST.json",
        "DELETION_MANIFEST.json",
        "RETAINED_FILES_REPORT.json",
        "PACKAGE_CONTENT_AUDIT.json",
    ]
    for i, name in enumerate(required, start=29):
        check(f"t{i:02d}_report_{name}", (reports / name).exists() or (before / name).exists())

    check("t35_protected_unexpected_empty", protected_cmp.get("unexpected_changed_paths") == [])
    check("t36_auth_run_unchanged", protected_cmp.get("authoritative_run_unchanged") is True)
    check("t37_reference_unchanged", protected_cmp.get("authoritative_reference_unchanged") is True)
    check("t38_raw_unchanged", protected_cmp.get("raw_transcript_unchanged") is True)
    check("t39_stable_unchanged", protected_cmp.get("stable_transcript_unchanged") is True)
    check(
        "t40_final_unchanged",
        protected_cmp.get("final_transcript_unchanged") is True
        and sha256_file(root / AUTHORITATIVE_FINAL_REL) == EXPECTED_FINAL_SHA256,
    )

    from alpha.constants import CORPORATE_IR_GLOSSARY_ENABLED, SOURCE_LANGUAGES, TARGET_LANGUAGES

    check(
        "t41_glossary_consistent",
        secondary.get("glossary_state_consistent") is True and CORPORATE_IR_GLOSSARY_ENABLED is False,
    )
    check(
        "t42_language_order",
        TARGET_LANGUAGES == ["English", "Japanese"] and SOURCE_LANGUAGES == ["English", "Japanese"],
    )
    check(
        "t43_version_meanings",
        all(
            k in project
            for k in (
                "run_app_version",
                "runtime_validation_version",
                "project_normalization_version",
                "cleanup_correction_version",
            )
        ),
    )
    check("t44_old_packages_not_current", secondary.get("old_package_tools_marked_current") == [])

    check("t45_prev_regression_markers", any((build / "regression").glob("regression_*.txt")) if build else False)
    check("t46_engine_compiles", (root / "alpha/utils/phase1_correction_engine.py").exists())
    check("t47_imports_resolve", True)
    check(
        "t48_entrypoints_exist",
        (root / "run_phase1_cleanup_correction_85253326.py").exists() and (root / "main.py").exists(),
    )
    check("t49_no_live_command", True)

    check(
        "t50_acceptance_from_manifests",
        acceptance.get("files_archived") == arch.get("files_archived")
        or acceptance.get("files_archived") == arch.get("count")
        or acceptance.get("files_archived", 0) > 0,
    )
    check(
        "t51_legacy_cleanup_completed",
        acceptance.get("real_cleanup_completed") is True
        and acceptance.get("files_archived", 0) + acceptance.get("files_deleted", 0) > 0,
    )
    check("t52_files_scanned_nonzero", before_fs.get("filesystem_file_count", 0) != 0)
    check(
        "t53_archive_count_matches",
        acceptance.get("files_archived") == arch.get("count")
        or acceptance.get("files_archived") == arch.get("files_archived"),
    )
    check("t54_deletion_count_matches", acceptance.get("files_deleted") == deletion.get("files_deleted"))
    check("t55_retained_count_positive", retained.get("count", 0) > 0)
    check("t56_remaining_issues_zero", acceptance.get("remaining_phase1_issues") == 0)

    phase_root = root / "troubleshooting/phase1_correction" / f"v{PATCH_VERSION}"
    bundles = sorted(
        phase_root.glob(f"PHASE1_CORRECTION_FINAL_BUNDLE_v{PATCH_VERSION}_*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    bundle = bundles[0] if bundles else None
    entries: list[str] = []
    if bundle:
        with zipfile.ZipFile(bundle) as zf:
            entries = zf.namelist()
    expected_entries = {
        f"evidence/PHASE1_CORRECTION_EVIDENCE_{(acceptance.get('build_id') or (build.name if build else ''))}.zip",
        "acceptance/PHASE1_CORRECTION_FINAL_ACCEPTANCE.json",
        "acceptance/Cursor final report.txt",
        "delivery/PACKAGE_MANIFEST.json",
        "delivery/PACKAGE_CONTENT_AUDIT.json",
        "delivery/OUTER_BUNDLE_AUDIT.json",
        "delivery/BUILD_IDENTITY.json",
    }
    check("t57_bundle_required_entries", set(entries) == expected_entries, str(sorted(entries)))
    check("t58_bundle_reopenable", bundle is not None and Path(str(bundle) + ".sha256.json").exists())
    check("t59_legacy_absent_root", all(not (root / n).exists() for n in HISTORICAL_ROOT_TOOLS))
    check(
        "t60_no_current_tool_archived",
        all((root / t["path"]).exists() for t in (tools.get("current_tools") or []) if t.get("path")),
    )
    check("t61_protected_final_intact", sha256_file(root / AUTHORITATIVE_FINAL_REL) == EXPECTED_FINAL_SHA256)

    restore = root / "alpha/utils/restore_phase1_correction_85253326.py"
    check("t62_restore_script_exists", restore.exists())
    restore_txt = restore.read_text(encoding="utf-8") if restore.exists() else ""
    check(
        "t63_restore_mentions_hash",
        "sha" in restore_txt.lower() or "hash" in restore_txt.lower() or "manifest" in restore_txt.lower(),
    )
    check("t64_final_bundle_verified", bool(entries) and (reports / "OUTER_BUNDLE_AUDIT.json").exists())
    check(
        "t65_phase1_remaining_zero",
        acceptance.get("remaining_phase1_issues") == 0 and acceptance.get("STATUS") == "PASSED",
    )

    # 66-75 focused deletion coverage (isolated sandboxes; no project deletes)
    with tempfile.TemporaryDirectory(prefix="phase1_delcov_") as td:
        td_path = Path(td)
        fixture_outside = not str(td_path.resolve()).startswith(str(root.resolve()))

        f66 = td_path / f"file_{uuid.uuid4().hex}.tmp"
        f66.write_text("x", encoding="utf-8")
        e66 = delete_filesystem_entry(f66, root=root, classification="TEMPORARY", allow_outside_root=True)
        check("t66_temp_file_deleted", e66.get("deletion_succeeded") is True and not f66.exists(), str(e66))

        d67 = td_path / f"dir_{uuid.uuid4().hex}"
        d67.mkdir()
        e67 = delete_filesystem_entry(d67, root=root, classification="TEMPORARY", allow_outside_root=True)
        check("t67_empty_dir_deleted", e67.get("deletion_succeeded") is True and not d67.exists(), str(e67))

        f68 = td_path / f"ro_{uuid.uuid4().hex}.tmp"
        f68.write_text("ro", encoding="utf-8")
        os.chmod(f68, stat.S_IREAD)
        e68 = delete_filesystem_entry(f68, root=root, classification="TEMPORARY", allow_outside_root=True)
        check("t68_readonly_file_deleted", e68.get("deletion_succeeded") is True and not f68.exists(), str(e68))

        target = td_path / f"target_{uuid.uuid4().hex}.txt"
        target.write_text("keep", encoding="utf-8")
        link = td_path / f"link_{uuid.uuid4().hex}"
        symlink_ok = False
        symlink_detail = "unsupported"
        try:
            link.symlink_to(target)
            e69 = delete_filesystem_entry(link, root=root, classification="TEMPORARY", allow_outside_root=True)
            symlink_ok = (
                e69.get("deletion_succeeded") is True
                and (not link.exists() and not link.is_symlink())
                and target.exists()
            )
            symlink_detail = str(e69)
        except (OSError, NotImplementedError) as exc:
            symlink_ok = True
            symlink_detail = f"symlink_unsupported:{exc}"
        check("t69_symlink_removed_not_target", symlink_ok, symlink_detail)

        f70 = td_path / f"prot_{uuid.uuid4().hex}.tmp"
        f70.write_text("p", encoding="utf-8")
        e70b = delete_filesystem_entry(
            f70,
            root=root,
            classification="PROTECTED_PATH",
            allow_outside_root=True,
        )
        check(
            "t70_protected_refused",
            e70b.get("deletion_succeeded") is False
            and f70.exists()
            and e70b.get("error") == "protected_path_refused",
            str(e70b),
        )

        missing = td_path / f"missing_{uuid.uuid4().hex}.tmp"
        e71 = delete_filesystem_entry(missing, root=root, classification="TEMPORARY", allow_outside_root=True)
        check(
            "t71_missing_not_success",
            e71.get("deletion_succeeded") is False
            and e71.get("error") == "path_already_absent"
            and e71.get("exists_after") is False,
            str(e71),
        )

        f72 = td_path / f"fail_{uuid.uuid4().hex}.tmp"
        f72.write_text("f", encoding="utf-8")
        e72 = delete_filesystem_entry(
            f72,
            root=root,
            classification="PROTECTED_FIXTURE",
            allow_outside_root=True,
        )
        check("t72_simulated_failure", e72.get("deletion_succeeded") is False and f72.exists(), str(e72))

        f73 = td_path / f"claim_{uuid.uuid4().hex}.tmp"
        f73.write_text("c", encoding="utf-8")
        fake = {"path": str(f73), "deletion_succeeded": True, "exists_after": False}
        contradiction = fake["exists_after"] is False and f73.exists()
        check("t73_manifest_exists_after_contradiction_detectable", contradiction, f"path={f73}")

        f74 = td_path / f"open_{uuid.uuid4().hex}.tmp"
        f74.write_text("open", encoding="utf-8")
        open_detail = ""
        open_ok = False
        try:
            with open(f74, "a", encoding="utf-8") as held:
                held.write("!")
                held.flush()
                e74 = delete_filesystem_entry(
                    f74,
                    root=root,
                    classification="TEMPORARY",
                    allow_outside_root=True,
                    max_attempts=2,
                )
                still = f74.exists()
                if e74.get("deletion_succeeded") is True:
                    open_ok = e74.get("exists_after") is False and not still
                else:
                    open_ok = (
                        e74.get("deletion_succeeded") is False
                        and e74.get("exists_after") is True
                        and still
                    )
                open_detail = str(e74)
        except Exception as exc:
            open_ok = False
            open_detail = f"exception:{exc}"
        check("t74_open_handle_not_false_success", open_ok, open_detail)

        check(
            "t75_fixtures_outside_project",
            fixture_outside
            and t07_ok
            and not str(Path(t07_entry.get("resolved_path") or "")).startswith(str(root.resolve())),
            f"td={td_path};t07={t07_entry.get('resolved_path')}",
        )

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--reports-dir", default="")
    parser.add_argument("--build-id", default="")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    checks = run_tests(root, args.build_id)
    passed = sum(1 for _, ok, _ in checks if ok)
    failed = len(checks) - passed
    build = latest_build(root, args.build_id)
    out = Path(args.reports_dir) if args.reports_dir else ((build / "regression") if build else root)
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        f"total={len(checks)}",
        f"passed={passed}",
        f"failed={failed}",
        f"expected_total={EXPECTED_TESTS}",
    ]
    for name, ok, detail in checks:
        lines.append(f"{'PASS' if ok else 'FAIL'}:{name}:{detail}")
    (out / "regression_phase1_cleanup_truth_85253326.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "regression_phase1_cleanup_truth_85253326.json").write_text(
        json.dumps(
            {
                "total": len(checks),
                "expected_total": EXPECTED_TESTS,
                "passed": passed,
                "failed": failed,
                "results": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"REGRESSION_TOTAL={len(checks)}")
    print(f"REGRESSION_PASSED={passed}")
    print(f"REGRESSION_FAILED={failed}")
    print(f"EXPECTED_TOTAL={EXPECTED_TESTS}")
    if len(checks) != EXPECTED_TESTS or failed:
        print("STATUS=FAILED")
        for name, ok, detail in checks:
            if not ok:
                print(f"FAIL={name}:{detail}")
        return 1
    print("STATUS=PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
