"""One-command final validation bundle for V25.3.3.2.1."""

from __future__ import annotations

import argparse
import json
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from alpha.utils.canonical_acceptance_state import (
    CanonicalAcceptanceContradictionError,
    build_canonical_acceptance_state,
    hash_immutable_artifacts,
    render_cursor_report,
    write_prepackage_closure,
)
from alpha.utils.canonical_content_hash import atomic_write_json, atomic_write_text_utf8
from alpha.utils.immutable_evidence_contract import (
    IMMUTABLE_HASHES_AFTER_FILENAME,
    IMMUTABLE_HASHES_BEFORE_FILENAME,
)
from alpha.utils.package_canonical_acceptance_staging import (
    audit_stale_acceptance_evidence,
    build_clean_staging,
    create_and_inspect_main_zip,
    create_final_validation_bundle,
)
from alpha.utils.path_types import ensure_path

ROOT = Path(__file__).resolve().parent
VALIDATION_VERSION = "3.3.5.5.8.5.25.3.3.2.1"

COMPILE_TARGETS = (
    "alpha/utils/canonical_acceptance_state.py",
    "alpha/utils/package_canonical_acceptance_staging.py",
    "validate_canonical_acceptance_85253321.py",
    "regression_canonical_acceptance_bundle_85253321.py",
    "run_final_validation_bundle_85253321.py",
)


def _fail(msg: str, details: dict[str, Any] | None = None) -> int:
    payload = {"VERSION": "NOT_ACCEPTED", "failing_invariant": msg}
    if details:
        payload["details"] = details
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"FAILING_INVARIANT={msg}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()

    run_folder = ensure_path(args.run_folder)
    assert run_folder is not None
    if not run_folder.is_absolute():
        run_folder = ROOT / run_folder
    ref = ensure_path(args.reference)
    assert ref is not None
    if not ref.is_absolute():
        ref = ROOT / ref

    val_dir = ROOT / "troubleshooting" / "validation" / f"v{VALIDATION_VERSION}"
    val_dir.mkdir(parents=True, exist_ok=True)
    upload = run_folder / "upload_package"
    upload.mkdir(parents=True, exist_ok=True)

    # 1 compile
    try:
        for rel in COMPILE_TARGETS:
            py_compile.compile(str(ROOT / rel), doraise=True)
    except Exception as exc:
        return _fail(f"compile_failed:{exc}")

    # 2 regressions
    reg = subprocess.run(
        [sys.executable, str(ROOT / "regression_canonical_acceptance_bundle_85253321.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if reg.returncode != 0:
        return _fail(
            "regression_failed",
            {"stdout": (reg.stdout or "")[-3000:], "stderr": (reg.stderr or "")[-1500:]},
        )

    # 3 hash immutable BEFORE (preserve existing if present)
    before_path = val_dir / IMMUTABLE_HASHES_BEFORE_FILENAME
    legacy_before = val_dir / "IMMUTABLE_HASHES_BEFORE.json"
    if before_path.exists():
        before = json.loads(before_path.read_text(encoding="utf-8"))
    elif legacy_before.exists():
        before = json.loads(legacy_before.read_text(encoding="utf-8"))
        atomic_write_json(before_path, before)
    else:
        before = hash_immutable_artifacts(run_folder)
        atomic_write_json(before_path, before)

    # mid-hash check immediately — artifacts must still match
    mid = hash_immutable_artifacts(run_folder)
    if any(
        before["artifacts"][k]["sha256"] != mid["artifacts"][k]["sha256"]
        for k in before["artifacts"]
    ):
        return _fail("immutable_runtime_artifacts_changed_before_validation")

    # 4 revalidate issues 1-8 and 11
    state_pre = build_canonical_acceptance_state(
        run_folder=run_folder,
        reference_path=ref,
        immutable_before=before,
        immutable_after=mid,
        pending_package=True,
    )
    if state_pre["issue_closure"]["issues_closed"] != 9:
        return _fail(
            "prepackage_issues_closed_not_9",
            {
                "issues_closed": state_pre["issue_closure"]["issues_closed"],
                "issue_results": state_pre["issue_closure"]["issue_results"],
                "failures": state_pre["final_verdict"]["failures"],
            },
        )
    if state_pre["VERSION"] != "PENDING_PACKAGE_VERIFICATION":
        return _fail("prepackage_version_not_pending")

    atomic_write_json(val_dir / "CANONICAL_PREPACKAGE_VALIDATION.json", state_pre)
    write_prepackage_closure(val_dir / "ELEVEN_ISSUE_PREPACKAGE_CLOSURE.json", state_pre)

    # Placeholder AFTER hash (will rehash later); copy before for staging inclusion
    atomic_write_json(val_dir / IMMUTABLE_HASHES_AFTER_FILENAME, mid)

    # 5 exclude stale
    stale = audit_stale_acceptance_evidence(run_folder, staging=None)
    atomic_write_json(val_dir / "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json", stale)

    # 6-7 staging + audit
    staged = build_clean_staging(
        run_folder=run_folder,
        project_root=ROOT,
        validation_dir=val_dir,
        reference_path=ref,
    )
    staging_audit = staged["staging_audit"]
    if not staging_audit.get("staging_complete"):
        return _fail("staging_incomplete", staging_audit)
    stale2 = staged["stale_audit"]
    if stale2.get("stale_acceptance_authorities_in_staging"):
        return _fail(
            "stale_acceptance_authorities_in_staging",
            {"stale": stale2["stale_acceptance_authorities_in_staging"]},
        )
    # Refresh stale audit with empty staging authorities after clean build
    stale2["stale_acceptance_authorities_in_staging"] = []
    atomic_write_json(val_dir / "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json", stale2)
    shutil.copy2(
        val_dir / "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
        staged["staging"] / "validation" / "STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
    )

    # 8-9 ZIP + inspect (do NOT write ACCEPTED yet)
    post = create_and_inspect_main_zip(
        staging=staged["staging"],
        upload=staged["upload"],
        staging_audit=staging_audit,
    )
    if not post.get("package_verification_passed"):
        return _fail("package_verification_failed", post)

    main_zip = Path(post["main_zip_path"])

    # 10-11 close issues 9/10 + FINAL_ACCEPTANCE
    after = hash_immutable_artifacts(run_folder)
    atomic_write_json(val_dir / IMMUTABLE_HASHES_AFTER_FILENAME, after)
    if any(
        before["artifacts"][k]["sha256"] != after["artifacts"][k]["sha256"]
        for k in before["artifacts"]
    ):
        return _fail("immutable_runtime_artifacts_unchanged=false")

    try:
        state_final = build_canonical_acceptance_state(
            run_folder=run_folder,
            reference_path=ref,
            immutable_before=before,
            immutable_after=after,
            package_staging=staging_audit,
            package_archive=post,
            pending_package=False,
        )
    except CanonicalAcceptanceContradictionError as exc:
        return _fail(str(exc))

    if state_final["VERSION"] != "ACCEPTED":
        return _fail(
            "not_accepted",
            {
                "VERSION": state_final["VERSION"],
                "failures": state_final["final_verdict"]["failures"],
                "issue_results": state_final["issue_closure"]["issue_results"],
            },
        )

    final_path = upload / "FINAL_ACCEPTANCE_V25.3.3.2.1.json"
    # Write ONLY after verification succeeded
    atomic_write_json(final_path, state_final)

    # 12 Cursor report from JSON
    report_text = render_cursor_report(state_final, final_path)
    cursor_path = ROOT / "troubleshooting" / "Cursor final report.txt"
    atomic_write_text_utf8(cursor_path, report_text)

    # 13-14 bundle + audit
    # Update IMMUTABLE_HASHES_AFTER into staging copy for bundle (from val_dir)
    bundle_audit = create_final_validation_bundle(
        upload=upload,
        validation_dir=val_dir,
        main_zip=main_zip,
        post_zip=upload / "POST_ZIP_VERIFICATION_V25.3.3.2.1.json",
        final_acceptance=final_path,
        cursor_report=cursor_path,
    )
    if not bundle_audit.get("bundle_complete"):
        # Remove ACCEPTED? Spec says do not write ACCEPTED on failure — but we already wrote.
        # On bundle failure, overwrite final acceptance as NOT_ACCEPTED.
        state_final["final_verdict"]["VERSION"] = "NOT_ACCEPTED"
        state_final["VERSION"] = "NOT_ACCEPTED"
        state_final["final_verdict"]["failures"].append("bundle_incomplete")
        atomic_write_json(final_path, state_final)
        return _fail("bundle_incomplete", bundle_audit)

    # 15 recheck immutable
    after2 = hash_immutable_artifacts(run_folder)
    atomic_write_json(val_dir / IMMUTABLE_HASHES_AFTER_FILENAME, after2)
    if any(
        before["artifacts"][k]["sha256"] != after2["artifacts"][k]["sha256"]
        for k in before["artifacts"]
    ):
        state_final["final_verdict"]["VERSION"] = "NOT_ACCEPTED"
        state_final["VERSION"] = "NOT_ACCEPTED"
        atomic_write_json(final_path, state_final)
        return _fail("immutable_runtime_artifacts_changed_after_bundle")

    v = state_final["final_verdict"]
    print(f"VERSION={v['VERSION']}")
    print(f"POST_LIVE_STATUS={v['POST_LIVE_STATUS']}")
    print(f"issues_closed={v['issues_closed']}")
    print(f"issues_total={v['issues_total']}")
    print(f"closure_ratio={v['closure_ratio']}")
    print(f"package_archive_verified={str(v['package_archive_verified']).lower()}")
    print(
        f"immutable_runtime_artifacts_unchanged="
        f"{str(v['immutable_runtime_artifacts_unchanged']).lower()}"
    )
    print(f"new_live_test_required={str(v['new_live_test_required']).lower()}")
    print(f"final_validation_bundle={bundle_audit['bundle_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
