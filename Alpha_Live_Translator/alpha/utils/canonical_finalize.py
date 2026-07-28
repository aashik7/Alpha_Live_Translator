"""Atomic canonical pipeline finalization before export (V25.3.2 / V25.3.3)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    ATOMIC_STOP_FINALIZATION_BARRIER_ENABLED,
    FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY,
)
from alpha.utils.path_types import ensure_path


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def reconcile_action_counts(
    *,
    ledger_counts: dict[str, int],
    runtime_metrics: dict[str, Any],
    event_file_counts: Optional[dict[str, int]] = None,
    assembler_event_count: int = 0,
) -> dict[str, Any]:
    from alpha.utils.accuracy_stage_capture import reconcile_three_source_action_counts

    return reconcile_three_source_action_counts(
        ledger_counts=ledger_counts,
        runtime_metrics=runtime_metrics,
        event_file_counts=event_file_counts,
        assembler_event_count=assembler_event_count,
    )


def _count_event_file_actions(run_folder: Path) -> dict[str, int]:
    from alpha.utils.accuracy_stage_capture import count_event_file_applied_actions

    return count_event_file_applied_actions(run_folder)


def finalize_canonical_pipeline(
    host: Any = None, *, run_folder: Optional[str | Path] = None
) -> dict[str, Any]:
    """Freeze ledger, build export payload, reconcile counters, write coverage report."""
    result: dict[str, Any] = {"ok": False}
    run_folder = ensure_path(run_folder)
    if not ATOMIC_STOP_FINALIZATION_BARRIER_ENABLED:
        result["skipped"] = True
        return result

    try:
        from alpha.transcription.canonical_transcript_ledger import (
            freeze_snapshot,
            get_action_counts,
            lineage_reconciliation,
            serialize_export_payload,
            validate_internal_consistency,
        )
        from alpha.utils.accuracy_stage_capture import (
            compare_stable_and_final_artifacts,
            count_assembler_event_total,
            count_event_file_applied_actions,
            get_accuracy_stage_compare_dir,
            load_jsonl_records,
            recompute_export_coverage_report,
            write_export_coverage_report,
            write_stable_active_stage_artifacts,
        )
        from alpha.utils.canonical_export_writer import set_canonical_export_payload
        from alpha.utils.live_runtime_metrics import freeze as freeze_metrics, set_speaker_distribution

        snap = freeze_snapshot()
        payload = serialize_export_payload(snap)
        set_speaker_distribution(dict(payload.get("speaker_distribution") or {}))
        set_canonical_export_payload(
            payload.get("lines") or [],
            canonical_records=snap.get("records") or [],
            coverage_report={},
        )
        metrics = freeze_metrics()
        coverage: dict[str, Any] = {}

        folder = ensure_path(run_folder)
        if folder is None:
            try:
                from alpha.utils.run_identity import get_current_run_identity

                ident = get_current_run_identity()
                if ident and ident.run_folder:
                    folder = ensure_path(ident.run_folder)
            except Exception:
                folder = None
        if folder is None:
            from alpha.utils.troubleshooting_paths import get_active_run_folder

            folder = ensure_path(get_active_run_folder())

        if folder:
            write_stable_active_stage_artifacts(folder, snapshot=snap)
            coverage = recompute_export_coverage_report(folder)
            set_canonical_export_payload(
                payload.get("lines") or [],
                canonical_records=snap.get("records") or [],
                coverage_report=coverage,
            )
            write_export_coverage_report(coverage, run_folder=folder)
            stable_final = compare_stable_and_final_artifacts(folder)

            raw_ids: set[str] = set()
            raw_path = get_accuracy_stage_compare_dir(folder) / "raw_deepgram_events.jsonl"
            for row in load_jsonl_records(raw_path):
                rid = row.get("raw_event_id")
                if rid:
                    raw_ids.add(str(rid))
            lineage = lineage_reconciliation(raw_ids)
            asm_events = count_assembler_event_total(folder)
            reconciliation = reconcile_action_counts(
                ledger_counts=get_action_counts(),
                runtime_metrics=metrics,
                event_file_counts=count_event_file_applied_actions(folder),
                assembler_event_count=asm_events,
            )
            stage_manifest_path = get_accuracy_stage_compare_dir(folder) / "stage_manifest.json"
            if stage_manifest_path.exists():
                manifest = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
            else:
                manifest = {}
            manifest.update(
                {
                    "ledger_action_counts": get_action_counts(),
                    "runtime_action_counts": reconciliation.get("runtime_action_counts"),
                    "event_file_action_counts": reconciliation.get("event_file_action_counts"),
                    "counts_reconciled": reconciliation.get("counts_reconciled"),
                    "count_reconciliation_differences": reconciliation.get("count_differences"),
                    "applied_action_sum": reconciliation.get("applied_action_sum"),
                    "lineage_coverage_ratio": lineage.get("lineage_coverage_ratio"),
                    "stable_records_without_lineage": lineage.get("stable_records_without_lineage"),
                    "export_coverage_ratio": coverage.get("coverage_ratio"),
                    "export_coverage_passed": coverage.get("coverage_passed"),
                    "stable_final_record_id_match": stable_final.get("stable_final_record_id_match"),
                    "stable_final_text_hash_match": stable_final.get("stable_final_text_hash_match"),
                    "stable_final_text_exact_match": stable_final.get("stable_final_text_exact_match"),
                    "stable_active_record_count": stable_final.get("stable_active_record_count"),
                    "final_export_record_count": stable_final.get("final_export_record_count"),
                    "ledger_consistency": validate_internal_consistency(),
                    "final_export_from_frozen_ledger": FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY,
                    "counts_reconciled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
            stage_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

            try:
                from alpha.utils.component_stall_classifier import finalize_stall_classifications

                finalize_stall_classifications(metrics, run_folder=folder, host=host)
            except Exception:
                pass

        try:
            from alpha.utils.flight_recorder import record_flight_event

            record_flight_event("stop_finalize_completed", force=True)
        except Exception:
            pass

        _jp_log(
            "CANONICAL_PIPELINE_FINALIZED",
            snapshot_id=snap.get("snapshot_id"),
            active_records=snap.get("active_record_count"),
            coverage_passed=coverage.get("coverage_passed") if folder else None,
        )
        result.update(
            {
                "ok": True,
                "snapshot_id": snap.get("snapshot_id"),
                "coverage": coverage if folder else {},
                "metrics": metrics,
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        _jp_log("CANONICAL_PIPELINE_FINALIZE_FAILED", error=str(exc))
    return result
