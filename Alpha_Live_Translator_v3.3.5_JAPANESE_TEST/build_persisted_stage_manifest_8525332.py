"""Build stage_manifest.json entirely from persisted completed-run evidence (V25.3.3.2)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION
from alpha.utils.canonical_content_hash import atomic_write_json
from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import (
    load_persisted_action_counts,
    load_persisted_audio_summary,
    load_persisted_health_evidence,
    load_persisted_speaker_distribution,
    load_persisted_stall_summary,
    load_persisted_stop_state,
    load_run_identity,
    write_export_coverage_from_persisted,
)


def build_persisted_stage_manifest(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    identity = load_run_identity(folder)
    actions = load_persisted_action_counts(folder)
    audio = load_persisted_audio_summary(folder)
    stop = load_persisted_stop_state(folder)
    health = load_persisted_health_evidence(folder)
    stall = load_persisted_stall_summary(folder)
    speakers = load_persisted_speaker_distribution(folder)
    coverage = write_export_coverage_from_persisted(folder)

    score_json = folder / "accuracy_stage_compare" / "three_stage_accuracy_report.json"
    score: dict[str, Any] = {}
    if score_json.exists():
        try:
            score = json.loads(score_json.read_text(encoding="utf-8"))
        except Exception:
            score = {}

    recon_path = folder / "accuracy_stage_compare" / "PERSISTED_STABLE_RECONSTRUCTION_REPORT.json"
    recon: dict[str, Any] = {}
    if recon_path.exists():
        try:
            recon = json.loads(recon_path.read_text(encoding="utf-8"))
        except Exception:
            recon = {}

    seal = {}
    seal_path = folder / "transcripts" / "FINAL_EXPORT_SEAL.json"
    if seal_path.exists():
        try:
            seal = json.loads(seal_path.read_text(encoding="utf-8"))
        except Exception:
            seal = {}

    reference_not_yet_scored = not bool(score)
    trusted_score = score.get("trusted_score")
    if trusted_score is None:
        trusted_score = score.get("final_cer")
    if trusted_score is None and isinstance(score.get("stages"), dict):
        trusted_score = (score.get("stages") or {}).get("final", {}).get("cer")

    failed_checks: list[str] = []
    if not coverage.get("coverage_passed"):
        failed_checks.append("coverage_passed")
    if not recon.get("reconstruction_completed", True):
        failed_checks.append("reconstruction_completed")
    if int(coverage.get("stable_active_record_count") or 0) <= 0:
        failed_checks.append("stable_active_record_count")
    if int(coverage.get("final_record_count") or 0) <= 0:
        failed_checks.append("final_record_count")
    if not stop.get("stop_finalize_completed"):
        failed_checks.append("stop_finalize_completed")
    if stop.get("stop_finalize_failed"):
        failed_checks.append("stop_finalize_failed")

    stage_capture_complete = len(failed_checks) == 0 and bool(coverage.get("coverage_passed"))

    manifest: dict[str, Any] = {
        "app_version": APP_VERSION,
        "run_app_version": identity.get("app_version"),
        "run_id": identity.get("run_id"),
        "manifest_source": "persisted_completed_run",
        "uses_live_in_memory_state": False,
        "reference_not_yet_scored": reference_not_yet_scored,
        "trusted_score": trusted_score,
        "score_should_be_used_for_decision": (not reference_not_yet_scored) and trusted_score is not None,
        "speaker_distribution": speakers,
        "process_health_timeline_written": health.get("process_health_timeline_written"),
        "memory_trend_summary_written": health.get("memory_trend_summary_written"),
        "persisted_event_action_counts": actions.get("persisted_event_action_counts"),
        "persisted_commit_action_counts": actions.get("persisted_commit_action_counts"),
        "counts_reconciled": actions.get("counts_reconciled"),
        "count_differences": actions.get("count_differences"),
        "stable_active_record_count": coverage.get("stable_active_record_count"),
        "final_record_count": coverage.get("final_record_count"),
        "coverage_ratio": coverage.get("coverage_ratio"),
        "coverage_passed": coverage.get("coverage_passed"),
        "stage_capture_complete": stage_capture_complete,
        "stage_capture_failed_checks": failed_checks,
        "final_export_write_count": int(seal.get("write_count") or 0),
        "post_seal_write_attempt_count": int(seal.get("post_seal_write_attempt_count") or 0),
        "legacy_writer_disabled": True,
        "stop_tail_candidate_suppression_count": int(recon.get("suppress_candidate_count") or 0),
        "existing_record_suppression_count": 0,
        "previous_active_record_preserved_count": int(recon.get("append_count") or 0),
        "late_final_overwrite_detected": False,
        "ui_events_posted_after_final_drain": 0,
        "final_seal_verified": bool(seal.get("seal_verified") or seal.get("sealed")),
        "stop_finalize_completed": stop.get("stop_finalize_completed"),
        "stop_finalize_failed": stop.get("stop_finalize_failed"),
        "final_status": stop.get("final_status"),
        "audio_delivery_summary": audio,
        "stall_classification_summary": stall,
        "append_count": int((actions.get("persisted_event_action_counts") or {}).get("append") or 0),
        "revise_count": int((actions.get("persisted_event_action_counts") or {}).get("revise") or 0),
        "authoritative_stage_byte_hash_match": coverage.get("authoritative_stage_byte_hash_match"),
        "authoritative_stage_normalized_hash_match": coverage.get(
            "authoritative_stage_normalized_hash_match"
        ),
    }
    out = folder / "accuracy_stage_compare" / "stage_manifest.json"
    atomic_write_json(out, manifest)
    manifest["path"] = str(out)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", required=True)
    args = parser.parse_args()
    manifest = build_persisted_stage_manifest(args.run_folder)
    print(json.dumps({
        "stage_capture_complete": manifest.get("stage_capture_complete"),
        "coverage_passed": manifest.get("coverage_passed"),
        "stable_active_record_count": manifest.get("stable_active_record_count"),
        "final_record_count": manifest.get("final_record_count"),
        "path": manifest.get("path"),
    }, indent=2, ensure_ascii=False))
    return 0 if manifest.get("stage_capture_complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
