"""One-command pre-live gate for V25.3.3.1."""

from __future__ import annotations

import compileall
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "troubleshooting" / "validation" / f"v{APP_VERSION}" / "PRE_LIVE_GATE_REPORT.json"

MODIFIED = [
    "alpha/constants.py",
    "alpha/utils/final_artifact_authority.py",
    "alpha/utils/run_artifacts.py",
    "alpha/utils/canonical_export_writer.py",
    "alpha/utils/accuracy_evidence_export.py",
    "alpha/utils/alpha_output_protection.py",
    "alpha/utils/stop_finalize_worker.py",
    "alpha/utils/accuracy_stage_capture.py",
    "alpha/transcription/canonical_transcript_ledger.py",
    "alpha/transcription/pipeline_commit_transaction.py",
    "alpha/transcription/japanese_sentence_assembler.py",
    "alpha/transcription/revision_metadata.py",
    "alpha/utils/live_runtime_metrics.py",
    "audit_final_alpha_writers_8525331.py",
    "regression_final_writer_stop_tail_8525331.py",
    "validate_final_writer_stop_tail_closure_8525331.py",
    "run_pre_live_gate_8525331.py",
    "run_post_live_closure_8525331.py",
    "package_latest_troubleshooting_run.py",
]


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
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "ok": proc.returncode == 0,
    }


def main() -> int:
    report: dict[str, Any] = {
        "app_version": APP_VERSION,
        "steps": {},
    }

    compile_ok = compileall.compile_dir(str(ROOT / "alpha"), quiet=1) and all(
        compileall.compile_file(str(ROOT / rel), quiet=1) for rel in MODIFIED if (ROOT / rel).exists()
    )
    report["steps"]["compile"] = {"ok": bool(compile_ok)}

    # Reference must exist before eleven-issue regressions that trust prepared snapshot.
    ref_dir = ROOT / "troubleshooting" / "accuracy_benchmark" / "prepared" / f"v{APP_VERSION}"
    if not ref_dir.exists():
        prior = ROOT / "troubleshooting" / "accuracy_benchmark" / "prepared" / "v3.3.5.5.8.5.25.3.3"
        if prior.exists():
            import shutil

            shutil.copytree(prior, ref_dir)
            # Retarget snapshot metadata to current version without changing content hashes.
            snap_path = ref_dir / "reference_snapshot.json"
            if snap_path.exists():
                snap = json.loads(snap_path.read_text(encoding="utf-8"))
                snap["app_version"] = APP_VERSION
                snap["snapshot_path"] = str(
                    Path("troubleshooting")
                    / "accuracy_benchmark"
                    / "prepared"
                    / f"v{APP_VERSION}"
                    / "reference.txt"
                ).replace("\\", "/")
                snap["copied_unchanged_from_version"] = snap.get("app_version") or "3.3.5.5.8.5.25.3.3"
                snap["app_version"] = APP_VERSION
                snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report["steps"]["reference_copy"] = {"ok": True, "from": str(prior)}
        else:
            report["steps"]["reference_copy"] = {"ok": False, "reason": "prior_reference_missing"}
    ref_ok = (ref_dir / "reference.txt").exists() and (ref_dir / "reference_snapshot.json").exists()
    report["steps"]["reference_verify"] = {"ok": ref_ok, "path": str(ref_dir)}

    report["steps"]["writer_audit"] = _run([sys.executable, "audit_final_alpha_writers_8525331.py"])
    report["steps"]["new_regressions"] = _run(
        [sys.executable, "regression_final_writer_stop_tail_8525331.py"]
    )
    report["steps"]["eleven_issue_regressions"] = _run(
        [sys.executable, "regression_eleven_issue_closure_852533.py"]
    )

    smoke_script = "runtime_smoke_eleven_issue_closure_852533.py"
    if (ROOT / smoke_script).exists():
        report["steps"]["smoke"] = _run([sys.executable, smoke_script])
    else:
        report["steps"]["smoke"] = {"ok": False, "reason": "smoke_script_missing"}

    # (reference already verified above)

    # Pre-live validation: writer audit acceptance + regressions
    audit_path = ROOT / "troubleshooting" / "validation" / f"v{APP_VERSION}" / "FINAL_ALPHA_WRITER_AUDIT.json"
    audit_pass = False
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit_pass = bool(audit.get("acceptance", {}).get("passed"))
    reg_path = (
        ROOT
        / "troubleshooting"
        / "validation"
        / f"v{APP_VERSION}"
        / "regression_final_writer_stop_tail_8525331.txt"
    )
    reg_pass = False
    if reg_path.exists():
        reg_pass = "STATUS=PASSED" in reg_path.read_text(encoding="utf-8")

    eleven_reg = ROOT / "troubleshooting" / "validation" / f"v{APP_VERSION}" / "regression_eleven_issue_closure_852533.txt"
    # eleven regressions write to APP_VERSION path — may need create via script
    eleven_pass = bool(report["steps"]["eleven_issue_regressions"].get("ok"))

    pre_live_validation = {
        "ok": audit_pass and reg_pass and eleven_pass and ref_ok and compile_ok,
        "audit_pass": audit_pass,
        "new_regression_pass": reg_pass,
        "eleven_regression_pass": eleven_pass,
        "reference_ok": ref_ok,
        "compile_ok": compile_ok,
        "smoke_ok": bool(report["steps"]["smoke"].get("ok")),
    }
    report["steps"]["pre_live_validation"] = pre_live_validation

    all_ok = (
        compile_ok
        and report["steps"]["writer_audit"]["ok"]
        and report["steps"]["new_regressions"]["ok"]
        and report["steps"]["eleven_issue_regressions"]["ok"]
        and report["steps"]["smoke"]["ok"]
        and ref_ok
        and pre_live_validation["ok"]
    )
    report["PRE_LIVE_STATUS"] = "PASSED" if all_ok else "FAILED"
    report["single_writer_tests_passed"] = audit_pass and reg_pass
    report["stop_tail_tests_passed"] = reg_pass
    report["late_overwrite_tests_passed"] = reg_pass
    report["all_regressions_passed"] = reg_pass and eleven_pass
    report["live_test_permitted"] = bool(all_ok)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "PRE_LIVE_STATUS",
        "single_writer_tests_passed",
        "stop_tail_tests_passed",
        "late_overwrite_tests_passed",
        "all_regressions_passed",
        "live_test_permitted",
    )}, indent=2))
    print(f"wrote {OUT}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
