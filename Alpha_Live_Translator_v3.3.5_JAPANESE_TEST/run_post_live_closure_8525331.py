"""Post-live closure orchestrator for V25.3.3.1 — fail-closed without live run."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION
from alpha.utils.latest_completed_live_run import resolve_latest_completed_live_run
from alpha.utils.path_types import ensure_path

ROOT = Path(__file__).resolve().parent


def _run(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-3000:],
        "stderr_tail": (proc.stderr or "")[-1500:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-live-run", action="store_true")
    parser.add_argument("--reference", default="")
    parser.add_argument(
        "--run-folder",
        default="",
        help="Validate exactly this completed live run; do not search for another.",
    )
    args = parser.parse_args()

    ref = args.reference or str(
        ROOT / "troubleshooting" / "accuracy_benchmark" / "prepared" / f"v{APP_VERSION}"
    )

    resolution: dict[str, Any] = {}
    run_folder: Path | None = None

    if args.run_folder:
        # Exact-folder mode: validate only the supplied run; never search.
        resolution = resolve_latest_completed_live_run(
            expected_version=APP_VERSION,
            explicit_run_folder=args.run_folder,
            project_root=ROOT,
        )
        if resolution.get("ok"):
            run_folder = ensure_path(
                resolution.get("resolved_run_folder") or resolution.get("run_folder")
            )
        else:
            run_folder = None
    elif args.latest_live_run:
        resolution = resolve_latest_completed_live_run(
            expected_version=APP_VERSION,
            project_root=ROOT,
        )
        if resolution.get("ok") and resolution.get("version_match"):
            run_folder = ensure_path(
                resolution.get("resolved_run_folder") or resolution.get("run_folder")
            )
        else:
            run_folder = None
    else:
        parser.error("Specify --run-folder or --latest-live-run")

    closure: dict[str, Any] = {
        "app_version": APP_VERSION,
        "reference": ref,
        "resolution": resolution,
        "steps": {},
    }

    if run_folder is None or not Path(run_folder).exists():
        reason = str(resolution.get("error") or "no_completed_live_run_for_version")
        closure.update(
            {
                "VERSION": "NOT_ACCEPTED",
                "POST_LIVE_STATUS": "FAILED",
                "reason": reason,
                "NOTE": "PENDING_LIVE_TEST — do not accept from offline repair",
                "issues_closed": 0,
                "issues_total": 11,
                "closure_ratio": 0.0,
                "single_final_writer_passed": False,
                "safe_stop_tail_passed": False,
                "sealed_export_passed": False,
                "package_complete": False,
            }
        )
        out = (
            ROOT
            / "troubleshooting"
            / "validation"
            / f"v{APP_VERSION}"
            / "ELEVEN_ISSUE_FINAL_CLOSURE_PENDING.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(closure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: closure[k] for k in (
            "VERSION", "POST_LIVE_STATUS", "reason", "NOTE"
        )}, indent=2))
        print(f"wrote {out}")
        return 1

    run_folder = Path(run_folder)
    closure["run_folder"] = str(run_folder)
    artifacts = run_folder / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    # 2. Verify seal
    from alpha.utils.final_artifact_authority import verify_final_export_seal

    try:
        seal = verify_final_export_seal(run_folder)
        closure["steps"]["verify_seal"] = {"ok": True, "seal": seal}
    except Exception as exc:
        closure["steps"]["verify_seal"] = {"ok": False, "error": str(exc)}
        closure["VERSION"] = "NOT_ACCEPTED"
        closure["POST_LIVE_STATUS"] = "FAILED"
        closure["reason"] = "seal_verification_failed"
        (artifacts / "ELEVEN_ISSUE_FINAL_CLOSURE.json").write_text(
            json.dumps(closure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 1

    # 3. Recompute coverage
    from alpha.utils.accuracy_stage_capture import (
        recompute_export_coverage_report,
        write_export_coverage_report,
        finalize_accuracy_stage_artifacts,
    )

    coverage = recompute_export_coverage_report(run_folder)
    write_export_coverage_report(coverage, run_folder=run_folder)
    closure["steps"]["coverage"] = {"ok": bool(coverage.get("coverage_passed")), "coverage": coverage}

    # 4. CER scores if scorer exists
    scorer = ROOT / "score_three_stage_accuracy.py"
    if scorer.exists():
        closure["steps"]["cer"] = _run(
            [sys.executable, str(scorer), "--run-folder", str(run_folder), "--reference", ref]
        )
    else:
        closure["steps"]["cer"] = {"ok": True, "skipped": "no_scorer"}

    # 5. Final stage manifest via finalize (idempotent)
    closure["steps"]["manifest"] = finalize_accuracy_stage_artifacts(
        run_folder=run_folder,
        final_alpha_source_path=run_folder / "transcripts" / "Alpha_output_FINAL.txt",
        offline_repair=False,
        allow_idempotent_repair=True,
    )

    # 6-7 validators
    closure["steps"]["new_validator"] = _run(
        [
            sys.executable,
            "validate_final_writer_stop_tail_closure_8525331.py",
            "--run-folder",
            str(run_folder),
            "--reference",
            ref,
        ]
    )
    closure["steps"]["eleven_validator"] = _run(
        [
            sys.executable,
            "validate_eleven_issue_closure_852533.py",
            "--post-live",
            "--run-folder",
            str(run_folder),
            "--reference",
            ref,
        ]
    )

    # 8-9 package + zip inspect
    closure["steps"]["package"] = _run(
        [sys.executable, "package_latest_troubleshooting_run.py", "--run-folder", str(run_folder)]
    )

    # 10 re-validate
    closure["steps"]["post_package_validate"] = _run(
        [
            sys.executable,
            "validate_final_writer_stop_tail_closure_8525331.py",
            "--run-folder",
            str(run_folder),
            "--reference",
            ref,
        ]
    )

    passed = all(
        bool(closure["steps"].get(k, {}).get("ok"))
        for k in ("verify_seal", "coverage", "new_validator", "eleven_validator", "package")
    )
    package_path = ""
    pkg_step = closure["steps"].get("package") or {}
    pkg_stdout = str(pkg_step.get("stdout_tail") or "")
    for line in pkg_stdout.splitlines():
        stripped = line.strip()
        if stripped.lower().endswith(".zip") and ("UPLOAD_PACKAGE" in stripped or "upload_package" in stripped.lower()):
            package_path = stripped
            break
    # Prefer auditing JSON if packaging wrote one
    for candidate in sorted((run_folder / "upload_package").glob(f"UPLOAD_PACKAGE_v{APP_VERSION}_*.zip"), reverse=True):
        package_path = str(candidate)
        break

    closure.update(
        {
            "issues_closed": 11 if passed else 0,
            "issues_total": 11,
            "closure_ratio": 1.0 if passed else 0.0,
            "single_final_writer_passed": passed,
            "safe_stop_tail_passed": passed,
            "sealed_export_passed": bool(closure["steps"]["verify_seal"].get("ok")),
            "package_complete": bool(closure["steps"]["package"].get("ok")),
            "package_path": package_path,
            "VERSION": "ACCEPTED" if passed else "NOT_ACCEPTED",
            "POST_LIVE_STATUS": "PASSED" if passed else "FAILED",
        }
    )
    out = artifacts / "ELEVEN_ISSUE_FINAL_CLOSURE.json"
    out.write_text(json.dumps(closure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: closure[k] for k in (
        "VERSION",
        "POST_LIVE_STATUS",
        "issues_closed",
        "issues_total",
        "closure_ratio",
        "package_complete",
        "package_path",
        "run_folder",
    )}, indent=2))
    print(f"wrote {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
