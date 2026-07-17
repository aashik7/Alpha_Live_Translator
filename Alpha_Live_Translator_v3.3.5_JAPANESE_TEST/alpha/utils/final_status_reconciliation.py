"""Derived FINAL_STATUS_RECONCILIATION — does not rewrite LIVE_RUN_STATUS."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from alpha.utils.canonical_content_hash import atomic_write_json
from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import _read_json, _read_jsonl
from alpha.utils.strict_evidence_values import (
    is_exactly_false,
    is_exactly_true,
    is_numeric_zero,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _derived(
    value: Any,
    *,
    source_files: list[str],
    derivation_rule: str,
    confidence: str,
    conflicts_with_live_status: bool,
) -> dict[str, Any]:
    return {
        "value": value,
        "source_files": source_files,
        "derivation_rule": derivation_rule,
        "confidence": confidence,
        "conflicts_with_live_status": conflicts_with_live_status,
    }


def _parse_jp_log_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "{" not in line:
            continue
        blob = line[line.find("{") :]
        try:
            row = json.loads(blob)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("event"):
            events.append(row)
    return events


def _speaker_distribution(run_folder: Path) -> dict[str, int]:
    rows = _read_jsonl(run_folder / "transcripts" / "final_export_records.jsonl")
    counts: Counter[str] = Counter()
    for row in rows:
        sp = row.get("speaker")
        if sp is None:
            continue
        counts[str(sp)] += 1
    return dict(sorted(counts.items(), key=lambda kv: kv[0]))


def _find_barrier_ack(events: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for ev in reversed(events):
        if ev.get("event") == "UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED":
            return ev
    return None


def build_final_status_reconciliation(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    live_path = folder / "artifacts" / "LIVE_RUN_STATUS.json"
    post_path = folder / "artifacts" / "POST_RUN_EXIT_SUMMARY.json"
    jap_path = folder / "logs" / "japanese_accuracy.log"
    health = folder / "health"
    live = _read_json(live_path)
    post = _read_json(post_path)
    events = _parse_jp_log_events(jap_path)
    barrier = _find_barrier_ack(events)

    live_spk = live.get("speaker_distribution")
    spk = _speaker_distribution(folder)
    health_tl = health / "PROCESS_HEALTH_TIMELINE.jsonl"
    mem = health / "MEMORY_TREND_SUMMARY.json"
    health_exists = health_tl.exists() and health_tl.stat().st_size > 0
    mem_exists = mem.exists() and mem.stat().st_size > 0

    barrier_live = live.get("stop_drain_barrier_passed")
    barrier_value = None
    barrier_sources: list[str] = []
    barrier_rule = ""
    barrier_conf = "low"
    if is_exactly_true(barrier_live) or is_exactly_false(barrier_live):
        barrier_value = barrier_live
        barrier_sources = ["artifacts/LIVE_RUN_STATUS.json"]
        barrier_rule = "live_explicit_boolean"
        barrier_conf = "high"
    elif barrier is not None and is_exactly_true(barrier.get("passed")):
        barrier_value = True
        barrier_sources = [
            "logs/japanese_accuracy.log#UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED",
        ]
        barrier_rule = "drain_barrier_ack_passed_true"
        barrier_conf = "high"
    conflicts_barrier = (
        barrier_value is not None
        and barrier_live is not None
        and barrier_value != barrier_live
    ) or (barrier_value is True and barrier_live is None)

    def queue_from_barrier(key: str, live_keys: tuple[str, ...]) -> dict[str, Any]:
        live_val = None
        for lk in live_keys:
            if lk in live:
                live_val = live.get(lk)
                break
        if is_numeric_zero(live_val):
            return _derived(
                0,
                source_files=["artifacts/LIVE_RUN_STATUS.json"],
                derivation_rule=f"live_{live_keys[0]}_zero",
                confidence="high",
                conflicts_with_live_status=False,
            )
        if barrier is not None and is_numeric_zero(barrier.get(key)):
            return _derived(
                0,
                source_files=[
                    "logs/japanese_accuracy.log#UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED",
                ],
                derivation_rule=f"barrier_ack_{key}_zero",
                confidence="high",
                conflicts_with_live_status=live_val not in (None, 0),
            )
        if live_val is not None:
            return _derived(
                live_val,
                source_files=["artifacts/LIVE_RUN_STATUS.json"],
                derivation_rule="live_passthrough",
                confidence="medium",
                conflicts_with_live_status=False,
            )
        return _derived(
            None,
            source_files=[],
            derivation_rule="unresolved",
            confidence="none",
            conflicts_with_live_status=False,
        )

    tq = queue_from_barrier(
        "transcript_queue_remaining",
        ("transcript_queue_remaining", "transcript_ui_queue_size", "ui_queue_size"),
    )
    # Prefer explicit barrier field name
    if barrier is not None and "transcript_queue_remaining" in barrier:
        tq = _derived(
            int(barrier.get("transcript_queue_remaining")),
            source_files=[
                "logs/japanese_accuracy.log#UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED",
            ],
            derivation_rule="barrier_ack_transcript_queue_remaining",
            confidence="high",
            conflicts_with_live_status=False,
        )

    ui_bus = queue_from_barrier(
        "ui_bus_queue_remaining",
        ("ui_bus_queue_remaining", "ui_event_bus_queue_size"),
    )
    if barrier is not None and "ui_bus_queue_remaining" in barrier:
        ui_bus = _derived(
            int(barrier.get("ui_bus_queue_remaining")),
            source_files=[
                "logs/japanese_accuracy.log#UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED",
            ],
            derivation_rule="barrier_ack_ui_bus_queue_remaining",
            confidence="high",
            conflicts_with_live_status=False,
        )

    lp_live = live.get("language_pipeline_queue_size")
    if is_numeric_zero(lp_live):
        lp_q = _derived(
            0,
            source_files=["artifacts/LIVE_RUN_STATUS.json"],
            derivation_rule="live_language_pipeline_queue_size_zero",
            confidence="high",
            conflicts_with_live_status=False,
        )
    elif is_exactly_false(live.get("language_pipeline_worker_alive")) and (
        is_exactly_true(live.get("stop_finalize_completed"))
        or is_exactly_true(post.get("stop_finalize_completed"))
    ):
        lp_q = _derived(
            0,
            source_files=[
                "artifacts/LIVE_RUN_STATUS.json",
                "artifacts/POST_RUN_EXIT_SUMMARY.json",
            ],
            derivation_rule="worker_dead_and_stop_finalize_completed_implies_queue_drained",
            confidence="medium",
            conflicts_with_live_status=lp_live not in (None, 0),
        )
    else:
        lp_q = _derived(
            lp_live,
            source_files=["artifacts/LIVE_RUN_STATUS.json"],
            derivation_rule="live_passthrough_or_unresolved",
            confidence="low" if lp_live is None else "medium",
            conflicts_with_live_status=False,
        )

    audio_live = live.get("audio_queue_size")
    drain_proven = (
        is_exactly_true(barrier_value)
        and is_numeric_zero(tq.get("value"))
        and is_numeric_zero(ui_bus.get("value"))
        and is_exactly_false(live.get("language_pipeline_worker_alive"))
        and (
            is_exactly_true(live.get("stop_finalize_completed"))
            or is_exactly_true(post.get("stop_finalize_completed"))
        )
    )
    if is_numeric_zero(audio_live):
        audio_q = _derived(
            0,
            source_files=["artifacts/LIVE_RUN_STATUS.json"],
            derivation_rule="live_audio_queue_size_zero",
            confidence="high",
            conflicts_with_live_status=False,
        )
    elif drain_proven and (audio_live is None or audio_live == -1):
        audio_q = _derived(
            0,
            source_files=[
                "logs/japanese_accuracy.log#UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED",
                "artifacts/LIVE_RUN_STATUS.json",
                "artifacts/POST_RUN_EXIT_SUMMARY.json",
            ],
            derivation_rule="drain_completed_evidence_reconciles_sentinel_audio_queue_size",
            confidence="high",
            conflicts_with_live_status=audio_live == -1,
        )
    else:
        audio_q = _derived(
            audio_live,
            source_files=["artifacts/LIVE_RUN_STATUS.json"],
            derivation_rule="live_passthrough_or_unresolved",
            confidence="low",
            conflicts_with_live_status=False,
        )

    worker_live = live.get("language_pipeline_worker_alive")
    stop_live = live.get("is_stopping")
    fin_live = live.get("is_finalizing")
    sfc_live = live.get("stop_finalize_completed")
    sff_live = live.get("stop_finalize_failed")
    sfc_post = post.get("stop_finalize_completed")

    fields = {
        "speaker_distribution": _derived(
            spk,
            source_files=["transcripts/final_export_records.jsonl"],
            derivation_rule="count_speaker_from_final_export_records",
            confidence="high",
            conflicts_with_live_status=bool(live_spk is not None and live_spk != spk),
        ),
        "process_health_timeline_exists": _derived(
            health_exists,
            source_files=["health/PROCESS_HEALTH_TIMELINE.jsonl"],
            derivation_rule="file_exists_nonempty",
            confidence="high",
            conflicts_with_live_status=bool(
                live.get("process_health_timeline_written") is False and health_exists
            ),
        ),
        "memory_trend_summary_exists": _derived(
            mem_exists,
            source_files=["health/MEMORY_TREND_SUMMARY.json"],
            derivation_rule="file_exists_nonempty",
            confidence="high",
            conflicts_with_live_status=bool(
                live.get("memory_trend_summary_written") is False and mem_exists
            ),
        ),
        "stop_drain_barrier_passed": _derived(
            barrier_value,
            source_files=barrier_sources,
            derivation_rule=barrier_rule or "unresolved",
            confidence=barrier_conf,
            conflicts_with_live_status=conflicts_barrier,
        ),
        "transcript_queue_remaining": tq,
        "ui_bus_queue_remaining": ui_bus,
        "language_pipeline_queue_size": lp_q,
        "audio_queue_size": audio_q,
        "language_pipeline_worker_alive": _derived(
            worker_live if isinstance(worker_live, bool) else None,
            source_files=["artifacts/LIVE_RUN_STATUS.json"],
            derivation_rule="live_explicit_boolean",
            confidence="high" if isinstance(worker_live, bool) else "none",
            conflicts_with_live_status=False,
        ),
        "is_stopping": _derived(
            stop_live if isinstance(stop_live, bool) else None,
            source_files=["artifacts/LIVE_RUN_STATUS.json"],
            derivation_rule="live_explicit_boolean",
            confidence="high" if isinstance(stop_live, bool) else "none",
            conflicts_with_live_status=False,
        ),
        "is_finalizing": _derived(
            fin_live if isinstance(fin_live, bool) else None,
            source_files=["artifacts/LIVE_RUN_STATUS.json"],
            derivation_rule="live_explicit_boolean",
            confidence="high" if isinstance(fin_live, bool) else "none",
            conflicts_with_live_status=False,
        ),
        "stop_finalize_completed": _derived(
            True
            if is_exactly_true(sfc_live) or is_exactly_true(sfc_post)
            else (False if is_exactly_false(sfc_live) and is_exactly_false(sfc_post) else None),
            source_files=[
                "artifacts/LIVE_RUN_STATUS.json",
                "artifacts/POST_RUN_EXIT_SUMMARY.json",
            ],
            derivation_rule="live_or_post_run_exit_summary",
            confidence="high",
            conflicts_with_live_status=False,
        ),
        "stop_finalize_failed": _derived(
            sff_live if isinstance(sff_live, bool) else False,
            source_files=["artifacts/LIVE_RUN_STATUS.json"],
            derivation_rule="live_explicit_boolean_default_false_only_if_bool",
            confidence="high" if isinstance(sff_live, bool) else "medium",
            conflicts_with_live_status=False,
        ),
    }

    disagreements = [
        k
        for k, v in fields.items()
        if isinstance(v, dict) and v.get("conflicts_with_live_status")
    ]
    required_ok = all(
        [
            is_exactly_true(fields["stop_drain_barrier_passed"]["value"]),
            is_numeric_zero(fields["transcript_queue_remaining"]["value"]),
            is_numeric_zero(fields["ui_bus_queue_remaining"]["value"]),
            is_numeric_zero(fields["language_pipeline_queue_size"]["value"]),
            is_numeric_zero(fields["audio_queue_size"]["value"]),
            is_exactly_false(fields["language_pipeline_worker_alive"]["value"]),
            is_exactly_false(fields["is_stopping"]["value"]),
            is_exactly_false(fields["is_finalizing"]["value"]),
            is_exactly_true(fields["stop_finalize_completed"]["value"]),
            is_exactly_false(fields["stop_finalize_failed"]["value"]),
            is_exactly_true(fields["process_health_timeline_exists"]["value"]),
            is_exactly_true(fields["memory_trend_summary_exists"]["value"]),
            isinstance(fields["speaker_distribution"]["value"], dict)
            and len(fields["speaker_distribution"]["value"]) > 0,
        ]
    )
    return {
        "generated_utc": _utc_now(),
        "run_folder": str(folder),
        "live_status_path": str(live_path),
        "live_status_mutated": False,
        "fields": fields,
        "disagreements_with_live_status": disagreements,
        "status_reconciliation_passed": required_ok,
    }


def write_final_status_reconciliation(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    payload = build_final_status_reconciliation(folder)
    out = folder / "artifacts" / "FINAL_STATUS_RECONCILIATION.json"
    atomic_write_json(out, payload)
    payload["output_path"] = str(out)
    return payload


def load_reconciled_status(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    path = folder / "artifacts" / "FINAL_STATUS_RECONCILIATION.json"
    if path.exists():
        return _read_json(path)
    return {}


def reconciled_value(recon: dict[str, Any], field: str) -> Any:
    fields = recon.get("fields") or {}
    entry = fields.get(field) or {}
    return entry.get("value")
