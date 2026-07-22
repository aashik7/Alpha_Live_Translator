"""Previous-run forensic analysis on startup after hard crash or kill."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_index_status(folder: Path) -> str:
    final_index = folder / "RUN_ARTIFACTS_INDEX.txt"
    if final_index.exists():
        text = final_index.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.startswith("status="):
                return line.split("=", 1)[1].strip()
    partial = folder / "RUN_ARTIFACTS_INDEX.partial.txt"
    if partial.exists():
        return "in_progress"
    return "unknown"


def _read_health_snapshot(folder: Path) -> dict[str, Any]:
    path = folder / "LAST_HEALTH_SNAPSHOT.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def analyze_incomplete_run(folder: Path) -> dict[str, Any]:
    from alpha.utils.flight_recorder import read_last_flight_event

    status = _read_index_status(folder)
    last_flight = read_last_flight_event(folder)
    health = _read_health_snapshot(folder)
    partial_alpha = folder / "Alpha_output_PARTIAL.txt"
    thread_dump = folder / "THREAD_DUMP_LAST.txt"
    live_status_path = folder / "LIVE_RUN_STATUS.json"
    previous_version = ""
    if live_status_path.exists():
        try:
            live_data = json.loads(live_status_path.read_text(encoding="utf-8"))
            previous_version = str(live_data.get("app_version", ""))
        except Exception:
            pass
    if not previous_version:
        index_path = folder / "RUN_ARTIFACTS_INDEX.txt"
        if index_path.exists():
            for line in index_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("app_version="):
                    previous_version = line.split("=", 1)[1].strip()
                    break

    exit_marker_missing = True
    if last_flight and last_flight.get("event_name") in (
        "process_exit_marker",
        "run_completed",
        "stop_finalize_completed",
    ):
        exit_marker_missing = False

    likely_cause = "previous_artifact_incomplete"
    if status in ("in_progress", "started", "started_no_finalize"):
        if last_flight and last_flight.get("event_name") in (
            "ui_stall_confirmed",
            "ui_stall_suspected",
        ):
            likely_cause = "previous_ui_stall"
        elif exit_marker_missing:
            likely_cause = "previous_hard_crash_or_kill"
        else:
            likely_cause = "previous_stop_incomplete"

    recovery_completed = bool(
        (folder / "RUN_ARTIFACTS_INDEX.txt").exists()
        and "status=completed" in (folder / "RUN_ARTIFACTS_INDEX.txt").read_text(
            encoding="utf-8", errors="ignore"
        )
    )

    return {
        "artifact_folder": str(folder),
        "previous_run_version": previous_version,
        "index_status": status,
        "previous_status": status,
        "likely_cause": likely_cause,
        "last_flight_event": last_flight,
        "last_health_snapshot": health,
        "partial_alpha_exists": partial_alpha.exists(),
        "thread_dump_exists": thread_dump.exists(),
        "process_exit_marker_missing": exit_marker_missing,
        "exit_marker_present": not exit_marker_missing,
        "recovery_completed": recovery_completed,
        "last_stable_commit_count": health.get("internal_stable_commit_count", -1),
        "last_ui_heartbeat_age_ms": health.get("ui_heartbeat_age_ms", -1),
    }


def write_previous_run_forensic_summary(analyses: list[dict[str, Any]]) -> Optional[Path]:
    if not analyses:
        return None
    path = _PROJECT_ROOT / "PREVIOUS_RUN_FORENSIC_SUMMARY.txt"
    lines = ["# Previous run forensic summary", ""]
    for item in analyses[:5]:
        lines.append(f"folder={item.get('artifact_folder', '')}")
        lines.append(f"previous_run_version={item.get('previous_run_version', '')}")
        lines.append(f"previous_status={item.get('previous_status', item.get('index_status', ''))}")
        lines.append(f"likely_cause={item.get('likely_cause', '')}")
        lines.append(f"index_status={item.get('index_status', '')}")
        lines.append(f"exit_marker_present={item.get('exit_marker_present', False)}")
        lines.append(f"exit_marker_missing={item.get('process_exit_marker_missing', True)}")
        lines.append(f"recovery_completed={item.get('recovery_completed', False)}")
        last = item.get("last_flight_event") or {}
        lines.append(f"last_flight_event={last.get('event_name', 'none')}")
        lines.append(f"last_flight_seq={last.get('seq', -1)}")
        health = item.get("last_health_snapshot") or {}
        lines.append(
            f"last_health_snapshot_ui_hb_ms={health.get('ui_heartbeat_age_ms', -1)}"
        )
        lines.append(
            f"last_health_snapshot_stable_count={health.get('internal_stable_commit_count', -1)}"
        )
        lines.append(f"last_stable_commit_count={item.get('last_stable_commit_count', -1)}")
        lines.append(f"last_ui_heartbeat_age_ms={item.get('last_ui_heartbeat_age_ms', -1)}")
        lines.append(f"partial_alpha_exists={item.get('partial_alpha_exists', False)}")
        lines.append(f"thread_dump_exists={item.get('thread_dump_exists', False)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        for item in analyses[:3]:
            cause = item.get("likely_cause", "")
            if cause == "previous_hard_crash_or_kill":
                jp_accuracy_log("PREVIOUS_RUN_LIKELY_HARD_KILL", **item)
            elif cause == "previous_ui_stall":
                jp_accuracy_log("PREVIOUS_RUN_LIKELY_UI_STALL", **item)
        jp_accuracy_log(
            "PREVIOUS_RUN_FORENSIC_SUMMARY_WRITTEN",
            path=str(path),
            count=len(analyses),
        )
        jp_accuracy_log("PREVIOUS_RUN_RECOVERY_INDEX_WRITTEN", path=str(path))
    except Exception:
        pass
    return path
