"""Atomic pipeline commit transaction — ledger, stage evidence, runtime counters (V25.3.3)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from alpha.constants import SINGLE_REVISION_AUTHORITY_ENABLED, UNPROVEN_REVISION_DEFAULT_ACTION
from alpha.utils.pipeline_integrity import PipelineIntegrityError


@dataclass(frozen=True)
class PipelineCommitResult:
    transaction_id: str
    requested_action: str
    applied_action: str
    record_id: str
    revision_target_id: str
    source_raw_event_ids: tuple[str, ...]
    ledger_applied: bool
    stage_event_written: bool
    runtime_counter_updated: bool
    metadata_consistent: bool
    success: bool
    failure_reason: str
    metadata: dict[str, Any]


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _ordered_lineage_ids(ids: Optional[list[str]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in ids or []:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _failure_result(
    *,
    transaction_id: str,
    requested_action: str,
    applied_action: str,
    revision_target_id: str,
    source_raw_event_ids: list[str],
    metadata: dict[str, Any],
    failure_reason: str,
) -> PipelineCommitResult:
    return PipelineCommitResult(
        transaction_id=transaction_id,
        requested_action=requested_action,
        applied_action=applied_action,
        record_id="",
        revision_target_id=revision_target_id,
        source_raw_event_ids=tuple(source_raw_event_ids),
        ledger_applied=False,
        stage_event_written=False,
        runtime_counter_updated=False,
        metadata_consistent=False,
        success=False,
        failure_reason=failure_reason,
        metadata=dict(metadata),
    )


def _write_suppressed_stop_tail_candidate(
    *,
    speaker: int,
    text: str,
    suppression_reason: str,
    source_raw_event_ids: list[str],
    transaction_id: str,
    metadata: dict[str, Any],
) -> None:
    from alpha.utils.path_types import ensure_path
    from alpha.utils.troubleshooting_paths import get_active_run_folder

    folder = ensure_path(get_active_run_folder())
    if folder is None:
        try:
            from alpha.utils.run_identity import get_current_run_identity

            ident = get_current_run_identity()
            folder = ensure_path(getattr(ident, "run_folder", None))
        except Exception:
            folder = None
    if folder is None:
        return
    stage_dir = folder / "accuracy_stage_compare"
    stage_dir.mkdir(parents=True, exist_ok=True)
    path = stage_dir / "suppressed_stop_tail_candidates.jsonl"
    row = {
        "speaker": speaker,
        "candidate_text": text,
        "suppression_reason": suppression_reason,
        "source_raw_event_ids": list(source_raw_event_ids or []),
        "transaction_id": transaction_id,
        "stop_tail_candidate": True,
        "stop_tail_candidate_suppressed": True,
        "previous_active_record_preserved": True,
        "revision_target_id": None,
        "canonical_record_id": None,
        "synthetic_record": bool(metadata.get("synthetic_record", False)),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def execute_pipeline_commit(
    *,
    speaker: int,
    assembler_text: str,
    final_text: str,
    metadata: dict[str, Any],
    requested_action: str,
    applied_action: str,
    revision_target_id: str = "",
    revision_reason: str = "",
    source_raw_event_ids: Optional[list[str]] = None,
    commit_reason: str = "",
    stop_flush: bool = False,
    incomplete_tail: bool = False,
    suppression_reason: str = "",
    update_previous_requested: bool = False,
    rejected_to_append: bool = False,
    decision_reason: str = "",
    revision_decision: Optional[dict[str, Any]] = None,
    stage_reason: str = "",
    stage_commit_reason: str = "",
    stage_action: str = "",
    stop_incomplete: bool = False,
    incomplete_reason: str = "",
    held_tail: str = "",
    boundary_type: str = "",
    safe_boundary_used: str = "",
    raw_fragments: Optional[list[str]] = None,
    transaction_id: str = "",
) -> PipelineCommitResult:
    """Run one immutable commit: metadata → ledger → stage evidence → runtime counters."""
    txn_id = transaction_id or f"txn-{uuid.uuid4().hex[:12]}"
    meta = dict(metadata or {})
    requested = str(requested_action or "append")
    applied = str(applied_action or "append")
    lineage_ids = _ordered_lineage_ids(list(source_raw_event_ids or []))
    target_id = str(revision_target_id or meta.get("revision_target_id") or "")

    _jp_log(
        "PIPELINE_COMMIT_TRANSACTION_STARTED",
        transaction_id=txn_id,
        requested_action=requested,
        applied_action=applied,
        revision_target_id=target_id,
        source_raw_event_ids=lineage_ids,
    )

    if applied in ("suppress_candidate", "suppressed_stop_tail_candidate"):
        applied = "suppress_candidate"
        target_id = ""
        meta["stop_tail_candidate"] = True
        meta["stop_tail_candidate_text"] = str(final_text or assembler_text or "")
        meta["stop_tail_candidate_suppressed"] = True
        meta["previous_active_record_preserved"] = True
        meta["revision_target_id"] = None
        meta["canonical_record_id"] = None
        meta["suppression_reason"] = suppression_reason or incomplete_reason or "incomplete_stop_tail"
        if meta.get("synthetic_record") is None:
            meta["synthetic_record"] = False
        _jp_log(
            "STOP_TAIL_CANDIDATE_SUPPRESSED",
            transaction_id=txn_id,
            candidate_text=str(final_text or "")[:120],
            suppression_reason=meta.get("suppression_reason"),
        )
        _jp_log(
            "STOP_TAIL_PREVIOUS_RECORD_PRESERVED",
            transaction_id=txn_id,
            previous_active_record_preserved=True,
        )

    if applied == "suppress" and (
        stop_flush
        or incomplete_tail
        or stop_incomplete
        or meta.get("stop_tail_candidate")
        or str(commit_reason or stage_reason or "").lower()
        in (
            "stop_flush",
            "stop_flush_incomplete_tail",
            "stop_tail_candidate",
        )
    ):
        _jp_log(
            "STOP_TAIL_INVALID_RECORD_SUPPRESSION_BLOCKED",
            transaction_id=txn_id,
            revision_target_id=target_id,
        )
        raise PipelineIntegrityError(
            "Stop-tail candidate cannot suppress an existing canonical record"
        )

    if meta.get("lineage_assignment_failed") or meta.get("force_append_only"):
        if applied in ("revise", "revise_previous"):
            _jp_log(
                "PIPELINE_COMMIT_REVISION_DOWNGRADED_TO_APPEND",
                transaction_id=txn_id,
                reason="lineage_assignment_failed",
            )
            applied = "append"
            target_id = ""

    if (
        SINGLE_REVISION_AUTHORITY_ENABLED
        and applied in ("revise", "revise_previous")
        and UNPROVEN_REVISION_DEFAULT_ACTION == "append"
    ):
        if not lineage_ids and not stop_flush and not meta.get("synthetic_record"):
            _jp_log(
                "PIPELINE_COMMIT_REVISION_DOWNGRADED_TO_APPEND",
                transaction_id=txn_id,
                reason="missing_lineage",
            )
            applied = "append"
            target_id = ""
        elif not target_id:
            _jp_log(
                "PIPELINE_COMMIT_REVISION_DOWNGRADED_TO_APPEND",
                transaction_id=txn_id,
                reason="missing_revision_target",
            )
            applied = "append"

    try:
        from alpha.transcription.revision_metadata import normalize_applied_metadata

        meta = normalize_applied_metadata(
            meta,
            applied_action=applied,
            revision_target_id=target_id,
            requested_update_previous=update_previous_requested,
        )
        metadata_consistent = True
    except PipelineIntegrityError as exc:
        result = _failure_result(
            transaction_id=txn_id,
            requested_action=requested,
            applied_action=applied,
            revision_target_id=target_id,
            source_raw_event_ids=lineage_ids,
            metadata=meta,
            failure_reason=f"metadata_inconsistent:{exc}",
        )
        _jp_log(
            "PIPELINE_COMMIT_TRANSACTION_FAILED",
            transaction_id=txn_id,
            failure_reason=result.failure_reason,
        )
        return result

    ledger_applied = False
    record_id = ""
    ledger_applied_action = applied
    try:
        from alpha.transcription.canonical_transcript_ledger import apply_decision

        ledger_result = apply_decision(
            speaker=speaker,
            assembler_text=assembler_text,
            final_text=final_text,
            requested_action=requested,
            applied_action=applied,
            revision_target_id=str(meta.get("revision_target_id") or target_id or ""),
            revision_reason=revision_reason,
            source_raw_event_ids=lineage_ids,
            commit_reason=commit_reason,
            stop_flush=stop_flush,
            incomplete_tail=incomplete_tail,
            suppression_reason=suppression_reason,
            metadata=meta,
            transaction_id=txn_id,
        )
        if not ledger_result.get("ok"):
            reason = str(ledger_result.get("reason") or "ledger_apply_failed")
            result = _failure_result(
                transaction_id=txn_id,
                requested_action=requested,
                applied_action=applied,
                revision_target_id=target_id,
                source_raw_event_ids=lineage_ids,
                metadata=meta,
                failure_reason=reason,
            )
            _jp_log(
                "PIPELINE_COMMIT_TRANSACTION_FAILED",
                transaction_id=txn_id,
                failure_reason=reason,
            )
            return result
        ledger_applied = True
        ledger_applied_action = str(ledger_result.get("applied_action") or applied)
        record_id = str(ledger_result.get("record_id") or "")
        if ledger_applied_action == "suppress_candidate":
            record_id = ""
            target_id = ""
            meta["revision_target_id"] = None
            meta["canonical_record_id"] = None
            meta["previous_active_record_preserved"] = True
            try:
                _write_suppressed_stop_tail_candidate(
                    speaker=speaker,
                    text=final_text or assembler_text,
                    suppression_reason=suppression_reason or incomplete_reason,
                    source_raw_event_ids=lineage_ids,
                    transaction_id=txn_id,
                    metadata=meta,
                )
            except Exception as exc:
                _jp_log(
                    "SUPPRESSED_STOP_TAIL_CANDIDATE_WRITE_FAILED",
                    error=f"{type(exc).__name__}:{exc}",
                )
        elif record_id:
            meta["revision_target_id"] = record_id
            meta["canonical_record_id"] = record_id
        if ledger_result.get("revision_target_id") and ledger_applied_action != "suppress_candidate":
            meta["revision_target_id"] = str(ledger_result.get("revision_target_id"))
    except PipelineIntegrityError as exc:
        result = _failure_result(
            transaction_id=txn_id,
            requested_action=requested,
            applied_action=applied,
            revision_target_id=target_id,
            source_raw_event_ids=lineage_ids,
            metadata=meta,
            failure_reason=f"ledger_integrity:{exc}",
        )
        _jp_log(
            "PIPELINE_COMMIT_TRANSACTION_FAILED",
            transaction_id=txn_id,
            failure_reason=result.failure_reason,
        )
        return result
    except Exception as exc:
        result = _failure_result(
            transaction_id=txn_id,
            requested_action=requested,
            applied_action=applied,
            revision_target_id=target_id,
            source_raw_event_ids=lineage_ids,
            metadata=meta,
            failure_reason=f"ledger_exception:{type(exc).__name__}:{exc}",
        )
        _jp_log(
            "PIPELINE_COMMIT_TRANSACTION_FAILED",
            transaction_id=txn_id,
            failure_reason=result.failure_reason,
        )
        return result

    stage_event_written = False
    try:
        from alpha.utils.accuracy_stage_capture import record_assembler_only_event
        from alpha.utils.run_identity import get_run_id

        asm_action = stage_action or ledger_applied_action
        record_assembler_only_event(
            run_id=get_run_id(),
            speaker=speaker,
            assembler_text=final_text,
            reason=stage_reason,
            commit_reason=stage_commit_reason,
            action=asm_action,
            update_previous=update_previous_requested,
            stop_incomplete=stop_incomplete,
            incomplete_reason=incomplete_reason,
            held_tail=held_tail,
            boundary_type=boundary_type,
            safe_boundary_used=safe_boundary_used,
            raw_fragments=raw_fragments,
            source_raw_event_ids=lineage_ids,
            decision_reason=decision_reason,
            revision_decision=revision_decision,
            applied_action=ledger_applied_action,
        )
        stage_event_written = True
    except Exception as exc:
        result = _failure_result(
            transaction_id=txn_id,
            requested_action=requested,
            applied_action=ledger_applied_action,
            revision_target_id=str(meta.get("revision_target_id") or target_id),
            source_raw_event_ids=lineage_ids,
            metadata=meta,
            failure_reason=f"stage_event_failed:{type(exc).__name__}:{exc}",
        )
        _jp_log(
            "PIPELINE_COMMIT_TRANSACTION_FAILED",
            transaction_id=txn_id,
            failure_reason=result.failure_reason,
            ledger_applied=True,
        )
        return result

    runtime_counter_updated = False
    try:
        from alpha.utils.live_runtime_metrics import note_assembler_event

        note_assembler_event(
            ledger_applied_action,
            revision_requested=update_previous_requested,
            rejected_to_append=rejected_to_append,
        )
        runtime_counter_updated = True
    except Exception as exc:
        result = _failure_result(
            transaction_id=txn_id,
            requested_action=requested,
            applied_action=ledger_applied_action,
            revision_target_id=str(meta.get("revision_target_id") or target_id),
            source_raw_event_ids=lineage_ids,
            metadata=meta,
            failure_reason=f"runtime_counter_failed:{type(exc).__name__}:{exc}",
        )
        result = PipelineCommitResult(
            transaction_id=txn_id,
            requested_action=requested,
            applied_action=ledger_applied_action,
            record_id=record_id,
            revision_target_id=str(meta.get("revision_target_id") or target_id),
            source_raw_event_ids=tuple(lineage_ids),
            ledger_applied=True,
            stage_event_written=stage_event_written,
            runtime_counter_updated=False,
            metadata_consistent=True,
            success=False,
            failure_reason=result.failure_reason,
            metadata=meta,
        )
        _jp_log(
            "PIPELINE_COMMIT_TRANSACTION_FAILED",
            transaction_id=txn_id,
            failure_reason=result.failure_reason,
            ledger_applied=True,
            stage_event_written=stage_event_written,
        )
        return result

    result = PipelineCommitResult(
        transaction_id=txn_id,
        requested_action=requested,
        applied_action=ledger_applied_action,
        record_id="" if ledger_applied_action == "suppress_candidate" else record_id,
        revision_target_id=""
        if ledger_applied_action == "suppress_candidate"
        else str(meta.get("revision_target_id") or target_id or ""),
        source_raw_event_ids=tuple(lineage_ids),
        ledger_applied=ledger_applied,
        stage_event_written=stage_event_written,
        runtime_counter_updated=runtime_counter_updated,
        metadata_consistent=True,
        success=True,
        failure_reason="",
        metadata=meta,
    )
    _jp_log(
        "PIPELINE_COMMIT_TRANSACTION_COMPLETED",
        transaction_id=txn_id,
        applied_action=ledger_applied_action,
        record_id=record_id,
        revision_target_id=result.revision_target_id,
    )
    return result
