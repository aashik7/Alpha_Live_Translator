"""Orchestrate offline repair for existing live run (V25.3.2.1)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from alpha.utils.latest_completed_live_run import resolve_latest_completed_live_run
from alpha.utils.prepared_reference_trust import load_prepared_reference_trust
from alpha.utils.repair_helpers import transcript_hashes, write_json

RUN_VERSION = "3.3.5.5.8.5.25.3.2"


def repair_existing_run(run_folder: Path, reference_path: Path) -> dict:
    run_folder = Path(run_folder)
    project = Path(__file__).resolve().parent
    hashes_before = transcript_hashes(run_folder)

    resolved = resolve_latest_completed_live_run(
        expected_version=RUN_VERSION,
        explicit_run_folder=run_folder,
    )
    if not resolved.get("ok"):
        return {"repair_status": "FAILED", "error": resolved.get("error")}

    # 1. Metadata repair
    from repair_run_metadata_8525321 import repair_run_metadata

    meta_report = repair_run_metadata(run_folder)

    # 2. Stage artifact backfill (from existing JSONL — no transcript mutation)
    from alpha.utils.accuracy_stage_capture import repair_accuracy_stage_artifacts

    final_source = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    stage_result = repair_accuracy_stage_artifacts(
        run_folder,
        final_alpha_source_path=final_source if final_source.exists() else None,
        offline_repair=True,
    )

    # 3. Audio summary repair
    from repair_audio_delivery_summary_8525321 import repair_audio_delivery_summary

    audio_result = repair_audio_delivery_summary(run_folder)

    # 4. Reference trust
    trust = load_prepared_reference_trust(reference_path)

    # 5. Scoring
    score_proc = subprocess.run(
        [
            sys.executable,
            str(project / "score_three_stage_accuracy.py"),
            "--run-folder",
            str(run_folder),
            "--reference",
            str(reference_path),
        ],
        capture_output=True,
        text=True,
        cwd=str(project),
    )
    score_report_path = run_folder / "accuracy_stage_compare" / "three_stage_accuracy_report.json"
    scores = {}
    scoring_completed = False
    if score_report_path.exists():
        scores = json.loads(score_report_path.read_text(encoding="utf-8"))
        scoring_completed = bool(scores.get("scoring_completed"))

    # 6. Post-live validation
    val_proc = subprocess.run(
        [
            sys.executable,
            str(project / "validate_canonical_pipeline_852532.py"),
            "--post-live",
            "--run-folder",
            str(run_folder),
            "--reference",
            str(reference_path),
        ],
        capture_output=True,
        text=True,
        cwd=str(project),
    )
    validation_status = "PASSED" if val_proc.returncode == 0 else "FAILED"

    hashes_after = transcript_hashes(run_folder)
    files_changed = list(meta_report.get("files_changed", []))
    if stage_result.get("stage_dir"):
        files_changed.append("accuracy_stage_compare/stage_files")
    files_changed.append("accuracy_stage_compare/audio_delivery_summary.json")

    warnings = []
    if not audio_result.get("audio_metrics_complete"):
        warnings.append("audio_metrics_incomplete")
    if not scoring_completed:
        warnings.append("scoring_incomplete")

    repair_status = "REPAIRED"
    if warnings:
        repair_status = "REPAIRED_WITH_EVIDENCE_WARNINGS"
    if validation_status == "FAILED" or not hashes_before == hashes_after:
        repair_status = "FAILED"

    report = {
        "run_folder": str(run_folder),
        "run_id": resolved.get("resolved_run_id"),
        "version_repaired": meta_report.get("mismatch_field_repaired"),
        "stop_flags_repaired": meta_report.get("stale_stop_flags_repaired"),
        "audio_summary_created": True,
        "audio_metrics_complete": audio_result.get("audio_metrics_complete"),
        "reference_trusted": trust.get("trusted"),
        "scoring_completed": scoring_completed,
        "scores": {
            "raw_deepgram_accuracy_percent": scores.get("raw_deepgram_accuracy_percent"),
            "stable_assembler_accuracy_percent": scores.get("stable_assembler_accuracy_percent"),
            "final_alpha_accuracy_percent": scores.get("final_alpha_accuracy_percent"),
            "likely_bottleneck": scores.get("likely_bottleneck"),
        },
        "validation_status": validation_status,
        "validation_stdout": val_proc.stdout[-2000:] if val_proc.stdout else "",
        "score_stdout": score_proc.stdout[-2000:] if score_proc.stdout else "",
        "files_changed": files_changed,
        "transcript_hashes_unchanged": hashes_before == hashes_after,
        "transcript_hashes_before": hashes_before,
        "transcript_hashes_after": hashes_after,
        "repair_status": repair_status,
        "repaired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_json(run_folder / "artifacts" / "repair_existing_run_8525321_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parent
    run_folder = Path(args.run_folder)
    reference = Path(args.reference)
    if not reference.is_absolute():
        reference = project / reference
    report = repair_existing_run(run_folder, reference)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("repair_status", "").startswith("REPAIRED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
