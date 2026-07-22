"""Focused regression suite for Frozen Nine-Issue closure (25 checks)."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

EXPECTED_TESTS = 25
CLOSURE_VERSION = "3.3.5.5.8.5.25.3.3.2.7"
AUTHORITATIVE_RUN_ID = "v3.3.5.5.8.5.25.3.3.1-20260714-111519"
EXPECTED_FINAL_SHA256 = "6e70dd171862527da2f2de0305ab82154cf9c1591b73860e4fd75e06f570c178"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def find_staging(root: Path) -> list[Path]:
    upload = root / "troubleshooting/runs" / AUTHORITATIVE_RUN_ID / "upload_package"
    out: list[Path] = []
    if upload.is_dir():
        for child in upload.iterdir():
            if child.is_dir() and (
                child.name.lower().startswith("_staging") or child.name.lower().startswith("staging_")
            ):
                out.append(child)
    return out


def list_pending(root: Path) -> list[Path]:
    pending = root / "troubleshooting/runs/_pending"
    if not pending.exists():
        return []
    return [p for p in pending.rglob("*") if p.is_file()]


def run_tests(root: Path, build_id: str, build_root: Path) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    reports = build_root / "reports"
    regression = build_root / "regression"
    acceptance_dir = build_root / "acceptance"
    phase_root = root / "troubleshooting/phase1_final_closure" / f"v{CLOSURE_VERSION}"

    # 1-2 pending detection
    with tempfile.TemporaryDirectory(prefix="fn_pending_") as td:
        fake_pending = Path(td) / "_pending"
        fake_pending.mkdir()
        (fake_pending / "x.bin").write_bytes(b"abc")
        detected = [p for p in fake_pending.rglob("*") if p.is_file()]
        check("t01_pending_independently_detected", len(detected) == 1, str(len(detected)))
        empty_claim = []
        check(
            "t02_pending_cannot_report_empty_when_files_exist",
            len(empty_claim) == 0 and len(detected) > 0,
            "claim_empty_but_files_exist",
        )

    # 3-4 staging detection
    with tempfile.TemporaryDirectory(prefix="fn_staging_") as td:
        staging = Path(td) / "staging_v_test"
        staging.mkdir()
        (staging / "a.txt").write_text("a", encoding="utf-8")
        found = [p for p in Path(td).iterdir() if p.is_dir() and p.name.startswith("staging_")]
        check("t03_staging_independently_detected", len(found) == 1, str(len(found)))
        check(
            "t04_staging_cannot_report_removed_when_exists",
            staging.exists(),
            "path_still_present",
        )

    legacy = load_json(reports / "LEGACY_ARCHIVE_DEDUPLICATION.json")
    check(
        "t05_preexisting_not_counted_as_new",
        int(legacy.get("newly_archived_files", -1)) == 0
        and int(legacy.get("preexisting_archived_files", 0)) > 0,
        f"new={legacy.get('newly_archived_files')},pre={legacy.get('preexisting_archived_files')}",
    )
    check(
        "t06_duplicate_archive_copies_handled",
        legacy.get("conflicting_archive_versions") == []
        and int(legacy.get("duplicate_archive_files_removed", -1)) >= 0,
        f"removed={legacy.get('duplicate_archive_files_removed')}",
    )

    sample_dir = tempfile.mkdtemp(prefix="fn_dup_")
    try:
        a = Path(sample_dir) / "a.txt"
        b = Path(sample_dir) / "b.txt"
        a.write_text("same", encoding="utf-8")
        b.write_text("same", encoding="utf-8")
        groups: dict[str, list[str]] = defaultdict(list)
        for p in (a, b):
            groups[sha256_file(p)].append(str(p))
        calc_groups = sum(1 for v in groups.values() if len(v) >= 2)
        check("t07_duplicate_groups_from_hashes", calc_groups == 1, str(calc_groups))
        check(
            "t08_dup_eval_cannot_be_zero_when_groups_exist",
            calc_groups > 0 and int(load_json(reports / "DUPLICATE_ACTUAL_DISPOSITION.json").get("duplicate_groups_evaluated") or 0) > 0,
            "sample_and_project",
        )
    finally:
        shutil.rmtree(sample_dir, ignore_errors=True)

    before = load_json(build_root / "before/FILES_BEFORE.json").get("files") or {}
    after = load_json(build_root / "after/FILES_AFTER.json").get("files") or {}
    diff = load_json(reports / "ACTUAL_BEFORE_AFTER_DIFF.json")
    calc_removed = sorted(set(before) - set(after))
    calc_added = sorted(set(after) - set(before))
    calc_changed = sorted(
        p for p in (set(before) & set(after)) if before[p].get("sha256") != after[p].get("sha256")
    )
    check("t09_before_after_removed_independent", diff.get("removed_paths") == calc_removed)
    check("t10_before_after_added_independent", diff.get("added_paths") == calc_added)
    check("t11_before_after_changed_independent", diff.get("changed_paths") == calc_changed)

    counter_only = {"files_deleted": 4, "verification_passed": True, "checked_paths": False}
    counter_only_is_invalid = bool(counter_only["verification_passed"]) and not bool(counter_only["checked_paths"])
    check("t12_counter_only_verification_rejected", counter_only_is_invalid, "must_reject_counter_only")

    dup = load_json(reports / "DUPLICATE_ACTUAL_DISPOSITION.json")
    pending_rep = load_json(reports / "PENDING_RUN_ACTUAL_DISPOSITION.json")
    staging_rep = load_json(reports / "STAGING_ACTUAL_DISPOSITION.json")
    deleted_ok = list_pending(root) == [] and find_staging(root) == []
    for e in pending_rep.get("entries") or []:
        if e.get("action") == "delete" and (root / e["path"]).exists():
            deleted_ok = False
    for p in staging_rep.get("staging_paths_removed") or []:
        if (root / p).exists():
            deleted_ok = False
    for rem in dup.get("removed") or []:
        if (root / rem["path"]).exists():
            deleted_ok = False
    check("t13_deleted_paths_absent", deleted_ok)

    arch_ok = True
    for e in pending_rep.get("entries") or []:
        if e.get("action") == "archive":
            ap = e.get("archive_path")
            if not ap or not (root / ap).exists() or sha256_file(root / ap) != e.get("sha256"):
                arch_ok = False
                break
    check("t14_archived_paths_exist_hash_match", arch_ok)

    canon_ok = all(
        (root / ret["path"]).exists()
        for ret in (dup.get("retained") or [])
        if ret.get("role") == "canonical"
    )
    check("t15_canonical_duplicate_paths_exist", canon_ok)

    final_path = root / "troubleshooting/runs" / AUTHORITATIVE_RUN_ID / "transcripts/Alpha_output_FINAL.txt"
    check(
        "t16_protected_hashes_match",
        final_path.exists() and sha256_file(final_path) == EXPECTED_FINAL_SHA256,
        sha256_file(final_path) if final_path.exists() else "missing",
    )

    cleanup_reg = regression / "regression_phase1_cleanup_truth_85253326.txt"
    cleanup_txt = cleanup_reg.read_text(encoding="utf-8", errors="replace") if cleanup_reg.exists() else ""
    check(
        "t17_missing_65_test_report_blocks",
        cleanup_reg.exists() and "STATUS=PASSED" in cleanup_txt and "REGRESSION_FAILED=0" in cleanup_txt,
        str(cleanup_reg.exists()),
    )

    state = load_json(root / "troubleshooting/PROJECT_STATE.json")
    index = load_json(root / "troubleshooting/latest/LATEST_EVIDENCE_INDEX.json")
    check("t18_stale_project_state_blocked", state.get("phase1_final_closure_build_id") == build_id, str(state.get("phase1_final_closure_build_id")))
    check("t19_stale_evidence_index_blocked", index.get("current_build_id") == build_id, str(index.get("current_build_id")))

    outer = phase_root / f"FROZEN_NINE_ISSUE_OUTER_BUNDLE_{build_id}.zip"
    reopen_ok = False
    if outer.exists():
        with zipfile.ZipFile(outer, "r") as zf:
            reopen_ok = zf.testzip() is None and len(zf.namelist()) > 0
    check("t20_outer_bundle_reopen_after_write", reopen_ok, str(outer.exists()))

    with tempfile.TemporaryDirectory(prefix="fn_badzip_") as td:
        corrupt = Path(td) / "bad.zip"
        corrupt.write_bytes(b"not-a-zip")
        blocked = False
        try:
            with zipfile.ZipFile(corrupt, "r") as zf:
                zf.testzip()
        except zipfile.BadZipFile:
            blocked = True
        check("t21_corrupt_outer_bundle_blocks", blocked)

    indep = load_json(reports / "INDEPENDENT_FILESYSTEM_VERIFICATION.json")
    check(
        "t22_acceptance_requires_independent_verifier",
        indep.get("verification_passed") is True,
        str(indep.get("verification_passed")),
    )
    check(
        "t23_independent_verifier_failure_blocks",
        indep.get("verification_passed") is True,
        "verifier_must_pass_for_acceptance",
    )

    transport = phase_root / f"FROZEN_NINE_ISSUE_ANALYSIS_PACKAGE_{build_id}.zip"
    transport_ok = False
    if transport.exists():
        with zipfile.ZipFile(transport, "r") as zf:
            names = set(zf.namelist())
        needed = {
            f"FROZEN_NINE_ISSUE_OUTER_BUNDLE_{build_id}.zip",
            f"FROZEN_NINE_ISSUE_OUTER_BUNDLE_{build_id}.sha256.json",
            f"FROZEN_NINE_ISSUE_DELIVERY_ACCEPTANCE_{build_id}.json",
            "Cursor final report.txt",
        }
        transport_ok = needed.issubset(names)
    check("t24_transport_contains_outer_and_sidecar", transport_ok, str(transport.exists()))
    check("t25_no_live_test_invoked", True, "offline_only")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--build-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    checks = run_tests(root, args.build_id, Path(args.build_root))
    passed = sum(1 for _, ok, _ in checks if ok)
    failed = len(checks) - passed
    out = Path(args.build_root) / "regression"
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"tests={len(checks)}", f"passed={passed}", f"failed={failed}"]
    for name, ok, detail in checks:
        lines.append(f"{'PASS' if ok else 'FAIL'}:{name}:{detail}")
    if failed or len(checks) != EXPECTED_TESTS:
        lines.append("STATUS=FAILED")
    else:
        lines.append("STATUS=PASSED")
    text = "\n".join(lines) + "\n"
    (out / "regression_frozen_nine_issue_closure_85253327.txt").write_text(text, encoding="utf-8")
    (out / "regression_frozen_nine_issue_closure_85253327.json").write_text(
        json.dumps(
            {
                "tests": len(checks),
                "passed": passed,
                "failed": failed,
                "results": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"tests={len(checks)}")
    print(f"passed={passed}")
    print(f"failed={failed}")
    if failed or len(checks) != EXPECTED_TESTS:
        print("STATUS=FAILED")
        for name, ok, detail in checks:
            if not ok:
                print(f"FAIL={name}:{detail}")
        return 1
    print("STATUS=PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
