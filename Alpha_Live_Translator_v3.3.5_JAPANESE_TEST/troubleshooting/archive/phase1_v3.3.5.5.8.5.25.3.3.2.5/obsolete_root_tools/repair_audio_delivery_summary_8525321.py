"""Offline repair for audio_delivery_summary.json (V25.3.2.1)."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from alpha.utils.repair_helpers import read_json, write_json


def repair_audio_delivery_summary(run_folder: Path) -> dict[str, Any]:
    run_folder = Path(run_folder)
    stage_dir = run_folder / "accuracy_stage_compare"
    stage_dir.mkdir(parents=True, exist_ok=True)
    out_path = stage_dir / "audio_delivery_summary.json"
    report_path = stage_dir / "audio_delivery_summary_repair_report.txt"

    manifest = read_json(run_folder / "RUN_MANIFEST.json")
    live = read_json(run_folder / "artifacts" / "LIVE_RUN_STATUS.json")
    audio_manifest = read_json(run_folder / "audio_temp" / "audio_manifest.json")
    dg_snap = read_json(stage_dir / "deepgram_request_snapshot.json")

    run_id = str(manifest.get("run_id", ""))
    app_version = str(manifest.get("app_version", ""))
    source_files: list[str] = []
    metrics_recovered: list[str] = []
    metrics_unavailable: list[str] = []

    summary: dict[str, Any] = {
        "run_id": run_id,
        "app_version": app_version,
        "created_by": "offline_repair",
        "generated_during_runtime": False,
        "generated_by_offline_repair": True,
        "wire_encoding": "linear16",
        "wire_sample_rate": dg_snap.get("sample_rate") or audio_manifest.get("sample_rate") or 16000,
        "wire_channels": 1,
        "sample_width_bytes": 2,
        "audio_chunks_sent": None,
        "audio_bytes_sent": None,
        "calculated_audio_seconds_sent": None,
        "run_elapsed_seconds": live.get("elapsed_seconds"),
        "audio_seconds_to_run_seconds_ratio": None,
        "audio_queue_overflow_count": live.get("audio_queue_overflow_count"),
        "audio_chunk_drop_count": live.get("audio_chunk_drop_count"),
        "deepgram_send_errors": None,
        "system_audio_chunks_received": None,
        "microphone_chunks_received": None,
        "mixed_audio_chunks_created": audio_manifest.get("total_chunks"),
        "capture_errors": None,
        "missing_metrics": [],
        "source_files_used": [],
    }

    if manifest:
        source_files.append("RUN_MANIFEST.json")
    if live:
        source_files.append("artifacts/LIVE_RUN_STATUS.json")
        if summary["run_elapsed_seconds"] is not None:
            metrics_recovered.append("run_elapsed_seconds")
    if audio_manifest:
        source_files.append("audio_temp/audio_manifest.json")
        if summary["mixed_audio_chunks_created"] is not None:
            metrics_recovered.append("mixed_audio_chunks_created")
    if dg_snap:
        source_files.append("accuracy_stage_compare/deepgram_request_snapshot.json")
        metrics_recovered.append("wire_sample_rate")

    for key in (
        "audio_chunks_sent",
        "audio_bytes_sent",
        "calculated_audio_seconds_sent",
        "deepgram_send_errors",
        "system_audio_chunks_received",
        "microphone_chunks_received",
        "capture_errors",
    ):
        if summary.get(key) is None:
            metrics_unavailable.append(key)

    if summary["audio_queue_overflow_count"] is None:
        metrics_unavailable.append("audio_queue_overflow_count")
    else:
        metrics_recovered.append("audio_queue_overflow_count")

    summary["missing_metrics"] = sorted(metrics_unavailable)
    summary["source_files_used"] = sorted(set(source_files))

    write_json(out_path, summary)

    repair_status = "REPAIRED_WITH_MISSING_METRICS" if metrics_unavailable else "REPAIRED"
    report_lines = [
        f"repair_status={repair_status}",
        f"metrics_recovered={metrics_recovered}",
        f"metrics_unavailable={metrics_unavailable}",
        f"source_files_used={summary['source_files_used']}",
        f"generated_by_offline_repair=true",
        f"repaired_at={time.strftime('%Y-%m-%dT%H:%M:%S')}",
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    return {
        "repair_status": repair_status,
        "output_path": str(out_path),
        "metrics_recovered": metrics_recovered,
        "metrics_unavailable": metrics_unavailable,
        "source_files_used": summary["source_files_used"],
        "generated_by_offline_repair": True,
        "audio_metrics_complete": not metrics_unavailable,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", required=True)
    args = parser.parse_args()
    result = repair_audio_delivery_summary(Path(args.run_folder))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["repair_status"].startswith("REPAIRED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
