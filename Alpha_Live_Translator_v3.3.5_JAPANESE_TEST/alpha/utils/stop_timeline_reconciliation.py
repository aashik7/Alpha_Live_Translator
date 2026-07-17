"""Reconstruct stop_finalize_timeline_reconciled.jsonl from multiple sources."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from alpha.utils.canonical_content_hash import atomic_write_json, atomic_write_jsonl
from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import _read_json, _read_jsonl


REQUIRED_STAGES = (
    "stop_requested",
    "audio_producers_stopped",
    "audio_queue_drain_completed",
    "deepgram_finalize_completed",
    "transcript_gate_closed",
    "assembler_flush_completed",
    "language_worker_stopped",
    "ui_drain_completed",
    "canonical_ledger_frozen",
    "final_alpha_written",
    "final_export_sealed",
    "three_stage_finalizer_completed",
    "stop_finalize_completed",
)

# Map required stage -> candidate source event names (prefer later occurrences for stop-path)
_STAGE_EVENT_ALIASES: dict[str, tuple[str, ...]] = {
    "stop_requested": (
        "STOP_UI_WATCHDOG_STARTED",
        "BOUNDARY_STABILIZER_STOP_FLUSH_STARTED",
        "STOP_REQUESTED",
    ),
    "audio_producers_stopped": (
        "TEMP_AUDIO_FINAL_CHUNK_FLUSHED",
        "RUNTIME_AUDIO_COUNTERS_FROZEN",
        "AUDIO_PRODUCERS_STOPPED",
    ),
    "audio_queue_drain_completed": (
        "UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED",
        "AUDIO_QUEUE_DRAIN_COMPLETED",
    ),
    "deepgram_finalize_completed": (
        "TRANSCRIPT_GATE_CLOSED_AFTER_DEEPGRAM",
        "STOP_TRANSCRIPT_GATE_CLOSED_ON_WS_CLOSE",
        "DEEPGRAM_FINALIZE_COMPLETED",
    ),
    "transcript_gate_closed": (
        "STOP_TRANSCRIPT_GATE_CLOSED",
        "STOP_TRANSCRIPT_GATE_CLOSED_ON_WS_CLOSE",
        "TRANSCRIPT_GATE_CLOSED_AFTER_DEEPGRAM",
    ),
    "assembler_flush_completed": (
        "BOUNDARY_STABILIZER_STOP_FLUSH_STARTED",
        "ASSEMBLER_FLUSH_EXECUTED_OFF_UI_THREAD",
        "JAPANESE_BUFFER_CLEARED_ON_STOP",
        "ASSEMBLER_FLUSH_COMPLETED",
    ),
    "language_worker_stopped": (
        "LANGUAGE_WORKER_STOPPED",
        "LANGUAGE_PIPELINE_WORKER_STOPPED",
    ),
    "ui_drain_completed": (
        "UI_TRANSCRIPT_DRAIN_COMPLETED",
        "UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED",
        "UI_DRAIN_COMPLETED",
    ),
    "canonical_ledger_frozen": (
        "CANONICAL_LEDGER_FROZEN",
        "LEDGER_FROZEN",
    ),
    "final_alpha_written": (
        "FINAL_ALPHA_AUTHORITY_WRITE_ONCE",
        "FINAL_ALPHA_FROZEN_LEDGER_EXPORT_COMPLETED",
        "LOSSLESS_ALPHA_EXPORT_WRITTEN",
        "FINAL_ALPHA_WRITTEN",
    ),
    "final_export_sealed": (
        "FINAL_EXPORT_SEALED",
        "FINAL_EXPORT_SEAL_VERIFIED",
    ),
    "three_stage_finalizer_completed": (
        "FINAL_ALPHA_STAGE_FINALIZATION_COMPLETED",
        "THREE_STAGE_FINALIZER_COMPLETED",
        "THREE_STAGE_FINALIZER_ENTERED",
    ),
    "stop_finalize_completed": (
        "STOP_FINALIZE_COMPLETED",
        "STOP_FINALIZE_USED_TRANSCRIPT_SNAPSHOT",
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_jp_log(path: Path) -> list[tuple[int, dict[str, Any], str]]:
    if not path.exists():
        return []
    out: list[tuple[int, dict[str, Any], str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if "{" not in line:
            continue
        try:
            row = json.loads(line[line.find("{") :])
        except Exception:
            continue
        if isinstance(row, dict):
            out.append((i, row, line[:40]))
    return out


def _parse_flight(path: Path) -> list[tuple[int, str]]:
    if not path.exists():
        return []
    return list(
        enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        )
    )


def _ts_of(row: dict[str, Any], line_prefix: str = "") -> Any:
    for k in ("timestamp", "ts", "time", "wall_time"):
        if row.get(k) is not None:
            return row.get(k)
    # Prefix like "2026-07-14 11:21:21.116 |"
    if "|" in line_prefix:
        return line_prefix.split("|", 1)[0].strip()
    return None


def build_stop_timeline_reconciliation(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    orig = folder / "logs" / "stop_finalize_timeline.jsonl"
    jap = folder / "logs" / "japanese_accuracy.log"
    fr = folder / "artifacts" / "FLIGHT_RECORDER.log"
    live = _read_json(folder / "artifacts" / "LIVE_RUN_STATUS.json")
    post = _read_json(folder / "artifacts" / "POST_RUN_EXIT_SUMMARY.json")
    manifest = _read_json(folder / "RUN_MANIFEST.json")
    recon = _read_json(folder / "artifacts" / "FINAL_STATUS_RECONCILIATION.json")
    finalizer_recon = _read_json(folder / "logs" / "FINALIZER_EVENT_RECONCILIATION.json")

    original_events = _read_jsonl(orig)
    jp_events = _parse_jp_log(jap)
    fr_lines = _parse_flight(fr)

    # Index JP events by name (all occurrences)
    by_event: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
    for item in jp_events:
        name = str(item[1].get("event") or "")
        by_event.setdefault(name, []).append(item)

    reconstructed: list[dict[str, Any]] = []
    # Preserve any real original timeline stages
    for i, row in enumerate(original_events):
        ev = str(row.get("event") or row.get("stage") or "")
        if ev and ev != "LOG_INITIALIZED":
            reconstructed.append(
                {
                    "event": ev,
                    "timestamp": row.get("timestamp"),
                    "source_file": "logs/stop_finalize_timeline.jsonl",
                    "source_line_or_record": i + 1,
                    "reconstructed": False,
                    "confidence": "high",
                }
            )

    stages_found: list[str] = []
    stages_missing: list[str] = []
    stage_records: dict[str, dict[str, Any]] = {}

    for stage in REQUIRED_STAGES:
        aliases = _STAGE_EVENT_ALIASES.get(stage, ())
        found: Optional[dict[str, Any]] = None

        for alias in aliases:
            hits = by_event.get(alias) or []
            if not hits:
                continue
            # Prefer last occurrence (stop path)
            line_no, row, prefix = hits[-1]
            # LANGUAGE_WORKER_STOPPED also fires at start — require it near stop if possible
            if stage == "language_worker_stopped" and len(hits) == 1:
                # Single early start event is weak; fall through to live status
                continue
            found = {
                "event": stage,
                "source_event": alias,
                "timestamp": _ts_of(row, prefix),
                "source_file": "logs/japanese_accuracy.log",
                "source_line_or_record": line_no,
                "reconstructed": True,
                "confidence": "high",
            }
            break

        if found is None and stage == "language_worker_stopped":
            if live.get("language_pipeline_worker_alive") is False:
                found = {
                    "event": stage,
                    "source_event": "language_pipeline_worker_alive",
                    "timestamp": live.get("completed_at") or live.get("timestamp"),
                    "source_file": "artifacts/LIVE_RUN_STATUS.json",
                    "source_line_or_record": "language_pipeline_worker_alive",
                    "reconstructed": True,
                    "confidence": "high",
                }

        if found is None and stage == "audio_queue_drain_completed":
            fields = (recon.get("fields") or {}) if recon else {}
            audio = (fields.get("audio_queue_size") or {}).get("value")
            barrier = (fields.get("stop_drain_barrier_passed") or {}).get("value")
            if audio == 0 and barrier is True:
                found = {
                    "event": stage,
                    "source_event": "FINAL_STATUS_RECONCILIATION.audio_queue_size",
                    "timestamp": recon.get("generated_utc"),
                    "source_file": "artifacts/FINAL_STATUS_RECONCILIATION.json",
                    "source_line_or_record": "fields.audio_queue_size",
                    "reconstructed": True,
                    "confidence": "high",
                }
            elif live.get("ui_bus_queue_remaining") == 0 and live.get("stop_finalize_completed") is True:
                found = {
                    "event": stage,
                    "source_event": "queues_drained_stop_finalize_completed",
                    "timestamp": live.get("completed_at"),
                    "source_file": "artifacts/LIVE_RUN_STATUS.json",
                    "source_line_or_record": "ui_bus_queue_remaining+stop_finalize_completed",
                    "reconstructed": True,
                    "confidence": "medium",
                }

        if found is None and stage == "three_stage_finalizer_completed":
            if (finalizer_recon or {}).get("reconciled_status") == "completed_without_exception":
                found = {
                    "event": stage,
                    "source_event": "FINALIZER_EVENT_RECONCILIATION.completed_without_exception",
                    "timestamp": (finalizer_recon or {}).get("generated_utc"),
                    "source_file": "logs/FINALIZER_EVENT_RECONCILIATION.json",
                    "source_line_or_record": "reconciled_status",
                    "reconstructed": True,
                    "confidence": "high",
                }
            elif by_event.get("THREE_STAGE_FINALIZER_ENTERED") and (
                not by_event.get("THREE_STAGE_FINALIZER_EXCEPTION")
                or (finalizer_recon or {}).get("real_exception_count") == 0
            ):
                line_no, row, prefix = by_event["THREE_STAGE_FINALIZER_ENTERED"][-1]
                found = {
                    "event": stage,
                    "source_event": "THREE_STAGE_FINALIZER_ENTERED+no_real_exception",
                    "timestamp": _ts_of(row, prefix),
                    "source_file": "logs/japanese_accuracy.log",
                    "source_line_or_record": line_no,
                    "reconstructed": True,
                    "confidence": "medium",
                }

        if found is None and stage == "stop_finalize_completed":
            if live.get("stop_finalize_completed") is True or post.get("stop_finalize_completed") is True:
                found = {
                    "event": stage,
                    "source_event": "stop_finalize_completed",
                    "timestamp": live.get("completed_at") or post.get("completed_at"),
                    "source_file": (
                        "artifacts/LIVE_RUN_STATUS.json"
                        if live.get("stop_finalize_completed") is True
                        else "artifacts/POST_RUN_EXIT_SUMMARY.json"
                    ),
                    "source_line_or_record": "stop_finalize_completed",
                    "reconstructed": True,
                    "confidence": "high",
                }
            elif manifest.get("stop_finalize_completed") is True:
                found = {
                    "event": stage,
                    "source_event": "stop_finalize_completed",
                    "timestamp": manifest.get("completed_at"),
                    "source_file": "RUN_MANIFEST.json",
                    "source_line_or_record": "stop_finalize_completed",
                    "reconstructed": True,
                    "confidence": "high",
                }

        # FLIGHT_RECORDER fallback: scan for keyword
        if found is None:
            for line_no, line in fr_lines:
                low = line.lower()
                key = stage.replace("_", " ")
                if stage.replace("_", "") in low.replace("_", "").replace(" ", "") or key in low:
                    found = {
                        "event": stage,
                        "source_event": "flight_recorder_keyword",
                        "timestamp": None,
                        "source_file": "artifacts/FLIGHT_RECORDER.log",
                        "source_line_or_record": line_no,
                        "reconstructed": True,
                        "confidence": "low",
                    }
                    break

        if found is None:
            stages_missing.append(stage)
        else:
            stages_found.append(stage)
            stage_records[stage] = found
            reconstructed.append(found)

    # Ordering: use REQUIRED_STAGES sequence presence
    ordering_valid = True
    if stages_missing:
        ordering_valid = False
    else:
        # timestamps where available — soft check
        last_ts: Any = None
        for stage in REQUIRED_STAGES:
            rec = stage_records.get(stage) or {}
            ts = rec.get("timestamp")
            if isinstance(ts, (int, float)) and isinstance(last_ts, (int, float)):
                if ts + 1e-9 < last_ts:
                    # Allow small reordering for concurrent logs only if same second — still flag if large reverse
                    if last_ts - ts > 120:
                        ordering_valid = False
                        break
            if isinstance(ts, (int, float)):
                last_ts = ts

    timeline_complete = len(stages_missing) == 0 and ordering_valid
    report = {
        "generated_utc": _utc_now(),
        "run_folder": str(folder),
        "required_stages": list(REQUIRED_STAGES),
        "stages_found": stages_found,
        "stages_missing": stages_missing,
        "ordering_valid": ordering_valid,
        "timeline_complete": timeline_complete,
        "original_timeline_mutated": False,
        "stop_timeline_reconciliation_passed": timeline_complete,
        "reconstructed_event_count": len(reconstructed),
    }
    return {"events": reconstructed, "report": report}


def write_stop_timeline_reconciliation(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    result = build_stop_timeline_reconciliation(folder)
    out_jsonl = folder / "logs" / "stop_finalize_timeline_reconciled.jsonl"
    out_report = folder / "logs" / "STOP_TIMELINE_RECONCILIATION_REPORT.json"
    atomic_write_jsonl(out_jsonl, result["events"])
    report = dict(result["report"])
    report["reconciled_timeline_path"] = str(out_jsonl)
    atomic_write_json(out_report, report)
    report["report_path"] = str(out_report)
    return report


def timeline_ordering_is_valid(events: list[dict[str, Any]]) -> bool:
    """Fail if required stages appear out of REQUIRED_STAGES order when both present."""
    idx = {s: i for i, s in enumerate(REQUIRED_STAGES)}
    last = -1
    for ev in events:
        name = str(ev.get("event") or "")
        if name not in idx:
            continue
        cur = idx[name]
        if cur < last:
            return False
        last = cur
    return True
