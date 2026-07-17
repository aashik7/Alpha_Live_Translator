"""One offline persisted-evidence closure command for V25.3.3.2."""

from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION, APP_CODENAME
from alpha.utils.canonical_content_hash import atomic_write_json
from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import (
    copy_stage_final_byte_identical,
    load_persisted_action_counts,
    load_persisted_speaker_distribution,
    supersede_partial_index,
    write_export_coverage_from_persisted,
    write_reconstructed_stable_artifacts,
)
from alpha.utils.package_persisted_staging import build_staging_package
from build_persisted_stage_manifest_8525332 import build_persisted_stage_manifest

ROOT = Path(__file__).resolve().parent
IMMUTABLE_RELS = (
    "transcripts/Alpha_output_FINAL.txt",
    "transcripts/final_export_records.jsonl",
    "transcripts/FINAL_EXPORT_SEAL.json",
    "accuracy_stage_compare/stable_assembler_events.jsonl",
    "transcripts/stable_commits.jsonl",
    "accuracy_stage_compare/audio_delivery_summary.json",
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_immutable(run_folder: Path) -> dict[str, Any]:
    arts = {}
    for rel in IMMUTABLE_RELS:
        p = run_folder / rel
        arts[rel] = {
            "exists": p.exists(),
            "sha256": _sha_file(p) if p.exists() else None,
            "size": p.stat().st_size if p.exists() else 0,
        }
    return {
        "run_folder": str(run_folder),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": arts,
    }


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
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()

    run_folder = ensure_path(args.run_folder)
    assert run_folder is not None
    if not run_folder.is_absolute():
        run_folder = ROOT / run_folder
    ref = str(args.reference)

    val_dir = ROOT / "troubleshooting" / "validation" / f"v{APP_VERSION}"
    val_dir.mkdir(parents=True, exist_ok=True)

    # 1 compile modified files
    compile_targets = [
        "alpha/utils/canonical_content_hash.py",
        "alpha/utils/persisted_run_evidence.py",
        "alpha/utils/package_persisted_staging.py",
        "alpha/utils/accuracy_stage_capture.py",
        "build_persisted_stage_manifest_8525332.py",
        "regression_persisted_evidence_package_closure_8525332.py",
        "run_persisted_closure_8525332.py",
    ]
    for rel in compile_targets:
        py_compile.compile(str(ROOT / rel), doraise=True)

    # 2 regressions
    reg = _run([sys.executable, "regression_persisted_evidence_package_closure_8525332.py"])
    if not reg["ok"]:
        print(json.dumps({"VERSION": "NOT_ACCEPTED", "reason": "regression_failed", "reg": reg}, indent=2))
        return 1

    # 3 immutable before (already written at patch start; refresh if missing)
    before_path = val_dir / "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_BEFORE.json"
    if not before_path.exists():
        atomic_write_json(before_path, _hash_immutable(run_folder))
    before = json.loads(before_path.read_text(encoding="utf-8"))

    # 4 reconstruct stable
    recon = write_reconstructed_stable_artifacts(run_folder)

    # 5 byte copy stage final
    stage_copy = copy_stage_final_byte_identical(run_folder)

    # 6 scores — generate/reuse
    score_json = run_folder / "accuracy_stage_compare" / "three_stage_accuracy_report.json"
    score_step: dict[str, Any]
    if score_json.exists() and score_json.stat().st_size > 0:
        score_step = {"ok": True, "reused": True, "path": str(score_json)}
    else:
        scorer = ROOT / "score_three_stage_accuracy.py"
        if scorer.exists():
            score_step = _run(
                [sys.executable, str(scorer), "--run-folder", str(run_folder), "--reference", ref]
            )
        else:
            score_step = {"ok": False, "reason": "no_scorer_and_no_existing_score"}

    # 7 coverage
    coverage = write_export_coverage_from_persisted(run_folder)

    # 8 manifest
    manifest = build_persisted_stage_manifest(run_folder)

    # 9 supersede partial index
    partial = supersede_partial_index(run_folder)

    # 10 eleven-issue validator (pre-package; package_isolation may still be open)
    eleven = _run(
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

    # Also run new writer validator if present
    writer_val = {"ok": True, "skipped": True}
    writer_script = ROOT / "validate_final_writer_stop_tail_closure_8525331.py"
    if writer_script.exists():
        writer_val = _run(
            [
                sys.executable,
                str(writer_script),
                "--run-folder",
                str(run_folder),
                "--reference",
                ref,
            ]
        )

    speakers = load_persisted_speaker_distribution(run_folder)
    actions = load_persisted_action_counts(run_folder)

    persisted_ok = bool(recon.get("reconstruction_completed")) and int(recon.get("active_record_count") or 0) == 22
    coverage_ok = bool(coverage.get("coverage_passed")) and float(coverage.get("coverage_ratio") or 0) == 1.0
    manifest_ok = bool(manifest.get("stage_capture_complete"))
    partial_ok = bool(partial.get("ok"))

    # 11 prepackage closure (before package)
    prepackage = {
        "app_version": APP_VERSION,
        "app_codename": APP_CODENAME,
        "run_folder": str(run_folder),
        "persisted_reconstruction_passed": persisted_ok,
        "normalized_coverage_passed": coverage_ok,
        "stage_manifest_complete": manifest_ok,
        "stable_active_record_count": recon.get("active_record_count"),
        "append_count": recon.get("append_count"),
        "revise_count": recon.get("revise_count"),
        "final_record_count": coverage.get("final_record_count"),
        "coverage_ratio": coverage.get("coverage_ratio"),
        "speaker_distribution": speakers,
        "persisted_event_action_counts": actions.get("persisted_event_action_counts"),
        "counts_reconciled": actions.get("counts_reconciled"),
        "stage_byte_identical": stage_copy.get("ok"),
        "partial_index": partial,
        "score_step": {"ok": score_step.get("ok"), "reused": score_step.get("reused", False)},
        "eleven_validator_ok": eleven.get("ok"),
        "writer_validator_ok": writer_val.get("ok"),
        "new_live_test_required": False,
    }
    pre_path = run_folder / "artifacts" / "ELEVEN_ISSUE_PREPACKAGE_CLOSURE.json"
    atomic_write_json(pre_path, prepackage)

    # Write AFTER hashes before packaging (immutable artifacts must already be unchanged).
    after = _hash_immutable(run_folder)
    after_path = val_dir / "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_AFTER.json"
    atomic_write_json(after_path, after)
    unchanged = before.get("artifacts") == after.get("artifacts")

    # Preliminary final closure for staging (package will also write accepted/rejected)
    # 12-16 package staging + zip
    package_allowed = (
        persisted_ok
        and coverage_ok
        and manifest_ok
        and reg["ok"]
        and unchanged
        and partial_ok
    )

    final_closure: dict[str, Any] = {
        **prepackage,
        "VERSION": "ACCEPTED" if package_allowed else "NOT_ACCEPTED",
        "POST_LIVE_STATUS": "PASSED" if package_allowed else "FAILED",
        "issues_closed": 0,
        "issues_total": 11,
        "closure_ratio": 0.0,
        "package_complete": False,
        "immutable_runtime_artifacts_unchanged": unchanged,
        "zip_path": "",
    }

    package_audit: dict[str, Any] = {"package_complete": False}
    if package_allowed:
        package_audit = build_staging_package(
            run_folder=run_folder,
            project_root=ROOT,
            prepackage_closure=prepackage,
            final_closure=final_closure,
        )
        final_closure["package_complete"] = bool(package_audit.get("package_complete"))
        final_closure["zip_path"] = str(package_audit.get("zip_path") or "")
        final_closure["package_audit"] = {
            k: package_audit.get(k)
            for k in (
                "archive_paths_unique",
                "duplicate_archive_paths",
                "current_run_only",
                "unexpected_external_paths",
                "old_version_files",
                "smoke_files",
                "preflight_files",
                "required_files_missing",
                "secret_scan_passed",
                "audio_exclusion_passed",
                "staging_zip_path_match",
                "staging_zip_hash_match",
                "package_complete",
            )
        }
        if not final_closure["package_complete"]:
            final_closure["VERSION"] = "NOT_ACCEPTED"
            final_closure["POST_LIVE_STATUS"] = "FAILED"

    # Re-verify immutable hashes after packaging
    after2 = _hash_immutable(run_folder)
    atomic_write_json(after_path, after2)
    unchanged2 = before.get("artifacts") == after2.get("artifacts")
    final_closure["immutable_runtime_artifacts_unchanged"] = unchanged2
    if not unchanged2:
        final_closure["VERSION"] = "NOT_ACCEPTED"
        final_closure["POST_LIVE_STATUS"] = "FAILED"
        final_closure["immutable_hash_diff"] = {
            k: {"before": before["artifacts"].get(k), "after": after2["artifacts"].get(k)}
            for k in IMMUTABLE_RELS
            if before["artifacts"].get(k) != after2["artifacts"].get(k)
        }

    # 18 re-run eleven-issue validator against packaged artifacts
    eleven_final = _run(
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
    # Parse issues_closed from validator output text when available
    eleven_txt = val_dir / "validate_eleven_issue_closure_852533.txt"
    issues_closed = 0
    if eleven_txt.exists():
        for line in eleven_txt.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("issues_closed:"):
                try:
                    issues_closed = int(line.split(":", 1)[1].strip())
                except ValueError:
                    issues_closed = 0
                break
    final_closure["eleven_validator_ok"] = bool(eleven_final.get("ok"))
    final_closure["issues_closed"] = issues_closed
    final_closure["closure_ratio"] = round(issues_closed / 11.0, 4) if issues_closed else 0.0
    if (
        final_closure.get("package_complete")
        and unchanged2
        and issues_closed == 11
        and persisted_ok
        and coverage_ok
        and manifest_ok
    ):
        final_closure["VERSION"] = "ACCEPTED"
        final_closure["POST_LIVE_STATUS"] = "PASSED"
    else:
        final_closure["VERSION"] = "NOT_ACCEPTED"
        final_closure["POST_LIVE_STATUS"] = "FAILED"

    # Re-write final closure outside zip path as well
    atomic_write_json(run_folder / "artifacts" / "ELEVEN_ISSUE_FINAL_CLOSURE.json", final_closure)
    atomic_write_json(val_dir / "ELEVEN_ISSUE_FINAL_CLOSURE.json", final_closure)

    # Final validation summary
    summary = {
        "VERSION": final_closure["VERSION"],
        "POST_LIVE_STATUS": final_closure["POST_LIVE_STATUS"],
        "issues_closed": final_closure["issues_closed"],
        "issues_total": 11,
        "closure_ratio": final_closure["closure_ratio"],
        "persisted_reconstruction_passed": persisted_ok,
        "normalized_coverage_passed": coverage_ok,
        "stage_manifest_complete": manifest_ok,
        "package_complete": final_closure["package_complete"],
        "immutable_runtime_artifacts_unchanged": unchanged2,
        "new_live_test_required": False,
        "zip_path": final_closure.get("zip_path") or "",
        "run_folder": str(run_folder),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary["zip_path"]:
        print(f"FINAL_ZIP={summary['zip_path']}")
    return 0 if summary["VERSION"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
