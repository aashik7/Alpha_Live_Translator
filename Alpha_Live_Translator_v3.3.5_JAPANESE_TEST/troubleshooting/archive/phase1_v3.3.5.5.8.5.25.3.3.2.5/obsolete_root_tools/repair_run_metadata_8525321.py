"""Repair run version metadata and stale Stop flags (V25.3.2.1)."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from alpha.utils.latest_completed_live_run import normalize_app_version, resolve_latest_completed_live_run
from alpha.utils.repair_helpers import backup_file, read_json, sha256_file, transcript_hashes, write_json

RUN_VERSION = "3.3.5.5.8.5.25.3.2"


def _parse_index_fields(index_path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not index_path.exists():
        return fields
    for line in index_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip()] = v.strip()
    return fields


def _collect_status_values(run_folder: Path) -> dict[str, Any]:
    manifest = read_json(run_folder / "RUN_MANIFEST.json")
    live = read_json(run_folder / "artifacts" / "LIVE_RUN_STATUS.json")
    index = _parse_index_fields(run_folder / "artifacts" / "RUN_ARTIFACTS_INDEX.txt")
    return {
        "RUN_MANIFEST.final_status": manifest.get("final_status"),
        "LIVE_RUN_STATUS.status": live.get("status"),
        "LIVE_RUN_STATUS.is_stopping": live.get("is_stopping"),
        "LIVE_RUN_STATUS.is_finalizing": live.get("is_finalizing"),
        "LIVE_RUN_STATUS.language_pipeline_worker_alive": live.get("language_pipeline_worker_alive"),
        "RUN_ARTIFACTS_INDEX.status": index.get("status"),
    }


def _collect_version_values(run_folder: Path) -> dict[str, str]:
    manifest = read_json(run_folder / "RUN_MANIFEST.json")
    live = read_json(run_folder / "artifacts" / "LIVE_RUN_STATUS.json")
    stage = read_json(run_folder / "accuracy_stage_compare" / "stage_manifest.json")
    index = _parse_index_fields(run_folder / "artifacts" / "RUN_ARTIFACTS_INDEX.txt")
    m = re.match(r"^(v)?([\d.]+)-", run_folder.name, re.I)
    folder_ver = normalize_app_version(m.group(2)) if m else ""
    return {
        "RUN_MANIFEST.json": normalize_app_version(str(manifest.get("app_version", ""))),
        "LIVE_RUN_STATUS.json": normalize_app_version(str(live.get("app_version", ""))),
        "stage_manifest.json": normalize_app_version(str(stage.get("app_version", ""))),
        "RUN_ARTIFACTS_INDEX.txt": normalize_app_version(index.get("app_version", "")),
        "run_folder_name": folder_ver,
    }


def repair_run_metadata(run_folder: Path) -> dict[str, Any]:
    run_folder = Path(run_folder)
    resolved = resolve_latest_completed_live_run(
        expected_version=RUN_VERSION,
        explicit_run_folder=run_folder,
    )
    if not resolved.get("ok"):
        return {"repair_status": "FAILED", "error": resolved.get("error")}

    hashes_before = transcript_hashes(run_folder)
    version_before = _collect_version_values(run_folder)
    status_before = _collect_status_values(run_folder)
    files_changed: list[str] = []

    authoritative_version = resolved["resolved_app_version"] or RUN_VERSION
    manifest_path = run_folder / "RUN_MANIFEST.json"
    live_path = run_folder / "artifacts" / "LIVE_RUN_STATUS.json"
    stage_path = run_folder / "accuracy_stage_compare" / "stage_manifest.json"
    index_path = run_folder / "artifacts" / "RUN_ARTIFACTS_INDEX.txt"

    # Repair stage_manifest missing app_version (root cause of run_version_mismatch)
    if stage_path.exists():
        backup_file(stage_path)
        stage = read_json(stage_path)
        if not stage.get("app_version"):
            stage["app_version"] = authoritative_version
            stage["repaired_offline"] = True
            stage["repair_timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            write_json(stage_path, stage)
            files_changed.append(str(stage_path.relative_to(run_folder)))

    # Repair stale stop flags on historical completed run
    stale_stop_flags_repaired = False
    safe_to_repair = False
    repair_reason = ""
    final_export = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    if final_export.exists() and resolved["resolved_run_status"].startswith("completed"):
        safe_to_repair = True
        live = read_json(live_path)
        if live.get("is_stopping") or live.get("is_finalizing") or live.get("language_pipeline_worker_alive"):
            backup_file(live_path)
            live["is_stopping"] = False
            live["is_finalizing"] = False
            live["language_pipeline_worker_alive"] = False
            live["status"] = "completed"
            live["current_run_status"] = "completed"
            live["stop_finalize_completed"] = True
            live["offline_stop_flags_repaired"] = True
            live["offline_stop_flags_repaired_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if not live.get("app_version"):
                live["app_version"] = authoritative_version
            write_json(live_path, live)
            files_changed.append(str(live_path.relative_to(run_folder)))
            stale_stop_flags_repaired = True
            repair_reason = "historical_completed_run_stale_host_snapshot"

    if manifest_path.exists():
        manifest = read_json(manifest_path)
        if not manifest.get("app_version"):
            backup_file(manifest_path)
            manifest["app_version"] = authoritative_version
            write_json(manifest_path, manifest)
            files_changed.append(str(manifest_path.relative_to(run_folder)))

    if index_path.exists():
        text = index_path.read_text(encoding="utf-8")
        if "app_version=" not in text:
            backup_file(index_path)
            index_path.write_text(text.rstrip() + f"\napp_version={authoritative_version}\n", encoding="utf-8")
            files_changed.append(str(index_path.relative_to(run_folder)))

    hashes_after = transcript_hashes(run_folder)
    version_after = _collect_version_values(run_folder)
    status_after = _collect_status_values(run_folder)

    mismatch_field = ""
    if not version_before.get("stage_manifest.json"):
        mismatch_field = "stage_manifest.json:app_version_missing"

    report = {
        "run_id": resolved.get("resolved_run_id"),
        "run_folder": str(run_folder),
        "expected_version": RUN_VERSION,
        "version_values_before": version_before,
        "version_values_after": version_after,
        "status_values_before": status_before,
        "status_values_after": status_after,
        "mismatch_field_repaired": mismatch_field,
        "files_changed": files_changed,
        "repair_status": "REPAIRED",
        "stale_stop_flags_repaired": stale_stop_flags_repaired,
        "repair_reason": repair_reason,
        "safe_to_repair": safe_to_repair,
        "transcript_files_unchanged": hashes_before == hashes_after,
        "transcript_hashes_before": hashes_before,
        "transcript_hashes_after": hashes_after,
        "repaired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_json(run_folder / "artifacts" / "run_metadata_repair_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-folder", required=True)
    args = parser.parse_args()
    report = repair_run_metadata(Path(args.run_folder))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("repair_status") == "REPAIRED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
