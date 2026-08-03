"""Canonical transcript ledger — single authoritative source for active transcript (V25.3.3)."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Any, Optional

from alpha.constants import (
    CANONICAL_TRANSCRIPT_LEDGER_ENABLED,
    FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY,
    RAW_EVENT_LINEAGE_REQUIRED,
    SINGLE_REVISION_AUTHORITY_ENABLED,
)
from alpha.utils.pipeline_integrity import PipelineIntegrityError

_lock = threading.RLock()
_run_id = ""
_sequence = 0
_ledger_generation = 0
_mutation_sequence = 0
_records: list[dict[str, Any]] = []
_history: list[dict[str, Any]] = []
_idempotency_index: dict[str, dict[str, Any]] = {}
_frozen_snapshot: Optional[dict[str, Any]] = None
_frozen = False


def _now() -> float:
    return time.time()


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def reset_for_run(run_id: str) -> None:
    global _run_id, _sequence, _ledger_generation, _mutation_sequence, _records, _history, _idempotency_index, _frozen_snapshot, _frozen
    with _lock:
        _run_id = run_id or ""
        _sequence = 0
        _ledger_generation += 1
        _mutation_sequence = 0
        _records = []
        _history = []
        _idempotency_index = {}
        _frozen_snapshot = None
        _frozen = False
    _jp_log("CANONICAL_LEDGER_RESET", run_id=run_id, ledger_generation=_ledger_generation)


def _next_record_id_unlocked() -> str:
    global _sequence
    _sequence += 1
    return f"canon-{_sequence:06d}"


def _next_mutation_sequence_unlocked() -> int:
    global _mutation_sequence
    _mutation_sequence += 1
    return _mutation_sequence


def _active_records_unlocked() -> list[dict[str, Any]]:
    return [r for r in _records if r.get("active") and not r.get("suppressed")]


def _find_record_unlocked(record_id: str) -> Optional[dict[str, Any]]:
    for rec in _records:
        if rec.get("record_id") == record_id:
            return rec
    return None


def _speaker_distribution(records: list[dict[str, Any]]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for rec in records:
        spk = rec.get("speaker", 0)
        key = f"Speaker {int(spk) if str(spk).isdigit() else spk}"
        dist[key] = int(dist.get(key, 0)) + 1
    return dist


def _assert_not_frozen_unlocked() -> None:
    if _frozen:
        raise PipelineIntegrityError("canonical ledger is frozen")


def _append_history_unlocked(
    *,
    action: str,
    transaction_id: str = "",
    record_id: str = "",
    text_preview: str = "",
    previous_text: str = "",
    previous_text_sha256: str = "",
    revision_target_id: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "action": action,
        "at": _now(),
        "ledger_generation": _ledger_generation,
        "mutation_sequence": _next_mutation_sequence_unlocked(),
        "transaction_id": transaction_id or f"txn-{uuid.uuid4().hex[:12]}",
    }
    if record_id:
        entry["record_id"] = record_id
    if text_preview:
        entry["text_preview"] = text_preview
    if previous_text:
        entry["previous_text"] = previous_text
    if previous_text_sha256:
        entry["previous_text_sha256"] = previous_text_sha256
    if revision_target_id:
        entry["revision_target_id"] = revision_target_id
    if extra:
        entry.update(extra)
    _history.append(entry)
    return entry


def _normalize_applied_action(action: str) -> str:
    action = str(action or "append").lower()
    if action in ("revise_previous", "revise"):
        return "revise"
    if action in ("no_op", "noop", "no-op"):
        return "no_op"
    if action in ("suppress_candidate", "suppressed_stop_tail_candidate"):
        return "suppress_candidate"
    if action in ("suppress", "suppressed_stop_tail"):
        return "suppress"
    return "append"


def _merge_lineage_unlocked(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in list(existing or []) + list(incoming or []):
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def _build_idempotency_key(metadata: dict[str, Any], applied_action: str) -> str:
    meta = dict(metadata or {})
    session_id = str(meta.get("session_id") or "").strip()
    channel_index = str(meta.get("channel_index", meta.get("channel")) or "").strip()
    canonical_utterance_id = str(meta.get("canonical_utterance_id") or "").strip()
    decision = str(
        meta.get("idempotency_decision")
        or meta.get("canonical_decision")
        or applied_action
        or ""
    ).strip()
    source_version = str(meta.get("source_version") or "").strip()
    if not session_id or not canonical_utterance_id or not source_version or not decision:
        return ""
    return "|".join(
        (
            session_id,
            channel_index,
            canonical_utterance_id,
            source_version,
            decision,
        )
    )


def _remember_idempotency_unlocked(
    *,
    key: str,
    applied_action: str,
    record_id: str,
    revision_target_id: str,
) -> None:
    if not key:
        return
    _idempotency_index[key] = {
        "applied_action": str(applied_action or ""),
        "record_id": str(record_id or ""),
        "revision_target_id": str(revision_target_id or ""),
    }


def apply_decision(
    *,
    speaker: int,
    assembler_text: str,
    final_text: str,
    requested_action: str = "append",
    applied_action: str = "append",
    revision_target_id: str = "",
    revision_reason: str = "",
    source_raw_event_ids: Optional[list[str]] = None,
    commit_reason: str = "",
    stop_flush: bool = False,
    incomplete_tail: bool = False,
    suppression_reason: str = "",
    source: str = "assembler",
    metadata: Optional[dict[str, Any]] = None,
    transaction_id: str = "",
) -> dict[str, Any]:
    """Apply one authoritative ledger mutation from a finalized revision decision."""
    if not CANONICAL_TRANSCRIPT_LEDGER_ENABLED:
        return {"ok": True, "skipped": True, "applied_action": applied_action}

    txn_id = transaction_id or f"txn-{uuid.uuid4().hex[:12]}"
    meta = dict(metadata or {})
    synthetic = bool(meta.get("synthetic_record") or meta.get("synthetic_lineage"))

    with _lock:
        _assert_not_frozen_unlocked()
        applied = _normalize_applied_action(applied_action)
        requested = _normalize_applied_action(requested_action)
        ids = _merge_lineage_unlocked([], list(source_raw_event_ids or []))
        final = (final_text or assembler_text or "").strip()
        asm = (assembler_text or final).strip()
        idempotency_key = _build_idempotency_key(meta, applied)

        if idempotency_key and idempotency_key in _idempotency_index:
            remembered = dict(_idempotency_index[idempotency_key])
            _jp_log(
                "DUPLICATE_IGNORE",
                reason="ledger_idempotent_replay",
                idempotency_key=idempotency_key,
                record_id=remembered.get("record_id"),
                applied_action=remembered.get("applied_action"),
            )
            return {
                "ok": True,
                "applied_action": remembered.get("applied_action") or applied,
                "record_id": remembered.get("record_id") or "",
                "revision_target_id": remembered.get("revision_target_id") or "",
                "transaction_id": txn_id,
                "idempotent": True,
            }

        if RAW_EVENT_LINEAGE_REQUIRED and applied in ("append", "revise") and not stop_flush and not synthetic:
            if not ids:
                _jp_log("RAW_EVENT_LINEAGE_MISSING", applied_action=applied, transaction_id=txn_id)
                # fixes TASK_1A_FINDINGS.md Pattern 5: "append" silently bypassed
                # RAW_EVENT_LINEAGE_REQUIRED (only "revise" was ever blocked here).
                if SINGLE_REVISION_AUTHORITY_ENABLED and applied in ("append", "revise"):
                    return {"ok": False, "reason": f"missing_{applied}_lineage"}

        if applied == "no_op":
            _append_history_unlocked(
                action="no_op",
                transaction_id=txn_id,
                text_preview=final[:80],
            )
            return {
                "ok": True,
                "applied_action": "no_op",
                "record_id": "",
                "revision_target_id": revision_target_id,
                "transaction_id": txn_id,
            }

        if applied == "suppress_candidate":
            return _suppress_candidate_unlocked(
                candidate_text=final or asm,
                suppression_reason=suppression_reason or "incomplete_stop_tail",
                commit_reason=commit_reason,
                source_raw_event_ids=ids,
                revision_target_id=str(revision_target_id or ""),
                metadata=meta,
                transaction_id=txn_id,
            )

        if applied == "suppress":
            origin = str(commit_reason or meta.get("stage_reason") or source or "").lower()
            stop_tail_origins = (
                "stop_flush",
                "stop_flush_incomplete_tail",
                "stop_tail_candidate",
                "incomplete_stop_tail",
            )
            if stop_flush or incomplete_tail or any(token in origin for token in stop_tail_origins):
                raise PipelineIntegrityError(
                    "Stop-tail candidate cannot suppress an existing canonical record"
                )
            if meta.get("stop_tail_candidate"):
                raise PipelineIntegrityError(
                    "Stop-tail candidate cannot suppress an existing canonical record"
                )
            return _suppress_record_unlocked(
                record_id=revision_target_id,
                suppression_reason=suppression_reason or "explicit_suppression",
                commit_reason=commit_reason,
                transaction_id=txn_id,
            )

        if applied == "revise":
            target_id = str(revision_target_id or "").strip()
            if not target_id:
                _jp_log(
                    "IDENTITY_REJECTION",
                    reason="revision_target_required",
                    transaction_id=txn_id,
                )
                return {"ok": False, "reason": "revision_target_required"}
            existing_record_id = str(meta.get("canonical_record_id") or "").strip()
            if existing_record_id and existing_record_id != target_id:
                _jp_log(
                    "IDENTITY_REJECTION",
                    reason="canonical_record_id_immutable",
                    transaction_id=txn_id,
                    canonical_record_id=existing_record_id,
                    revision_target_id=target_id,
                )
                return {"ok": False, "reason": "canonical_record_id_immutable"}
            result = _revise_record_unlocked(
                target_record_id=target_id,
                speaker=speaker,
                assembler_text=asm,
                final_text=final,
                source_raw_event_ids=ids,
                revision_reason=revision_reason,
                commit_reason=commit_reason,
                stop_flush=stop_flush,
                incomplete_tail=incomplete_tail,
                requested_action=requested,
                transaction_id=txn_id,
            )
            if result.get("ok"):
                _remember_idempotency_unlocked(
                    key=idempotency_key,
                    applied_action="revise",
                    record_id=str(result.get("record_id") or ""),
                    revision_target_id=str(result.get("revision_target_id") or target_id),
                )
            return result

        existing_record_id = str(meta.get("canonical_record_id") or "").strip()
        if existing_record_id:
            _jp_log(
                "IDENTITY_REJECTION",
                reason="append_with_existing_canonical_record_id",
                transaction_id=txn_id,
                canonical_record_id=existing_record_id,
            )
            return {"ok": False, "reason": "append_with_existing_canonical_record_id"}

        result = _append_record_unlocked(
            speaker=speaker,
            assembler_text=asm,
            final_text=final,
            source_raw_event_ids=ids,
            commit_reason=commit_reason,
            stop_flush=stop_flush,
            incomplete_tail=incomplete_tail,
            requested_action=requested,
            revision_reason=revision_reason,
            source=source,
            metadata=meta,
            transaction_id=txn_id,
            synthetic_record=synthetic,
        )
        if result.get("ok"):
            _remember_idempotency_unlocked(
                key=idempotency_key,
                applied_action="append",
                record_id=str(result.get("record_id") or ""),
                revision_target_id="",
            )
        return result


def _append_record_unlocked(
    *,
    speaker: int,
    assembler_text: str,
    final_text: str,
    source_raw_event_ids: Optional[list[str]] = None,
    commit_reason: str = "",
    stop_flush: bool = False,
    incomplete_tail: bool = False,
    requested_action: str = "append",
    revision_reason: str = "",
    source: str = "assembler",
    metadata: Optional[dict[str, Any]] = None,
    transaction_id: str = "",
    synthetic_record: bool = False,
) -> dict[str, Any]:
    text = (final_text or assembler_text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty_text"}

    record_id = _next_record_id_unlocked()
    now = _now()
    rec = {
        "record_id": record_id,
        "run_id": _run_id,
        "sequence_number": _sequence,
        "speaker": speaker,
        "source": source,
        "assembler_text": assembler_text,
        "final_text": text,
        "source_raw_event_ids": list(source_raw_event_ids or []),
        "created_at": now,
        "updated_at": now,
        "requested_action": requested_action,
        "applied_action": "append",
        "revision_target_id": "",
        "revision_reason": revision_reason,
        "active": True,
        "suppressed": False,
        "suppression_reason": "",
        "stop_flush": bool(stop_flush),
        "incomplete_tail": bool(incomplete_tail),
        "synthetic_record": bool(synthetic_record or stop_flush),
        "commit_reason": commit_reason,
        "content_sha256": _sha256_text(text),
        "metadata": dict(metadata or {}),
    }
    _records.append(rec)
    _append_history_unlocked(
        action="append",
        transaction_id=transaction_id,
        record_id=record_id,
        text_preview=text[:80],
    )
    _jp_log("CANONICAL_LEDGER_APPEND", record_id=record_id, speaker=speaker, transaction_id=transaction_id)
    return {
        "ok": True,
        "applied_action": "append",
        "record_id": record_id,
        "transaction_id": transaction_id,
    }


def append_record(
    *,
    speaker: int,
    assembler_text: str,
    final_text: str,
    source_raw_event_ids: Optional[list[str]] = None,
    commit_reason: str = "",
    stop_flush: bool = False,
    incomplete_tail: bool = False,
    requested_action: str = "append",
    revision_reason: str = "",
    source: str = "assembler",
    metadata: Optional[dict[str, Any]] = None,
    transaction_id: str = "",
) -> dict[str, Any]:
    with _lock:
        _assert_not_frozen_unlocked()
        return _append_record_unlocked(
            speaker=speaker,
            assembler_text=assembler_text,
            final_text=final_text,
            source_raw_event_ids=source_raw_event_ids,
            commit_reason=commit_reason,
            stop_flush=stop_flush,
            incomplete_tail=incomplete_tail,
            requested_action=requested_action,
            revision_reason=revision_reason,
            source=source,
            metadata=metadata,
            transaction_id=transaction_id,
        )


def _revise_record_unlocked(
    *,
    target_record_id: str,
    speaker: int,
    assembler_text: str,
    final_text: str,
    source_raw_event_ids: Optional[list[str]] = None,
    revision_reason: str = "",
    commit_reason: str = "",
    stop_flush: bool = False,
    incomplete_tail: bool = False,
    requested_action: str = "revise",
    transaction_id: str = "",
) -> dict[str, Any]:
    text = (final_text or assembler_text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty_text"}
    if not target_record_id:
        raise PipelineIntegrityError("revise requires revision_target_id")

    target = _find_record_unlocked(target_record_id)
    if target is None or not target.get("active") or target.get("suppressed"):
        raise PipelineIntegrityError(f"revision target not active: {target_record_id}")

    prev_text = str(target.get("final_text") or "")
    prev_hash = str(target.get("content_sha256") or _sha256_text(prev_text))
    _append_history_unlocked(
        action="revise",
        transaction_id=transaction_id,
        record_id=target_record_id,
        previous_text=prev_text,
        previous_text_sha256=prev_hash,
        revision_target_id=target_record_id,
        text_preview=text[:80],
    )
    merged_ids = _merge_lineage_unlocked(
        list(target.get("source_raw_event_ids") or []),
        list(source_raw_event_ids or []),
    )
    target["assembler_text"] = assembler_text
    target["final_text"] = text
    target["updated_at"] = _now()
    target["applied_action"] = "revise"
    target["revision_reason"] = revision_reason
    target["commit_reason"] = commit_reason
    target["source_raw_event_ids"] = merged_ids
    target["stop_flush"] = bool(stop_flush)
    target["incomplete_tail"] = bool(incomplete_tail)
    target["requested_action"] = requested_action
    target["content_sha256"] = _sha256_text(text)
    if speaker is not None:
        target["speaker"] = speaker

    _jp_log("CANONICAL_LEDGER_REVISE", record_id=target_record_id, transaction_id=transaction_id)
    return {
        "ok": True,
        "applied_action": "revise",
        "record_id": target_record_id,
        "revision_target_id": target_record_id,
        "transaction_id": transaction_id,
    }


def revise_record(
    *,
    target_record_id: str,
    speaker: int,
    assembler_text: str,
    final_text: str,
    source_raw_event_ids: Optional[list[str]] = None,
    revision_reason: str = "",
    commit_reason: str = "",
    stop_flush: bool = False,
    incomplete_tail: bool = False,
    requested_action: str = "revise",
    transaction_id: str = "",
) -> dict[str, Any]:
    with _lock:
        _assert_not_frozen_unlocked()
        return _revise_record_unlocked(
            target_record_id=target_record_id,
            speaker=speaker,
            assembler_text=assembler_text,
            final_text=final_text,
            source_raw_event_ids=source_raw_event_ids,
            revision_reason=revision_reason,
            commit_reason=commit_reason,
            stop_flush=stop_flush,
            incomplete_tail=incomplete_tail,
            requested_action=requested_action,
            transaction_id=transaction_id,
        )


def _suppress_candidate_unlocked(
    *,
    candidate_text: str = "",
    suppression_reason: str = "",
    commit_reason: str = "",
    source_raw_event_ids: Optional[list[str]] = None,
    revision_target_id: str = "",
    metadata: Optional[dict[str, Any]] = None,
    transaction_id: str = "",
) -> dict[str, Any]:
    """History-only incomplete Stop-tail candidate — never mutates active records."""
    if str(revision_target_id or "").strip():
        raise PipelineIntegrityError(
            "suppress_candidate must not receive revision_target_id"
        )
    reason = str(suppression_reason or "").strip() or "incomplete_stop_tail"
    text = str(candidate_text or "").strip()
    _append_history_unlocked(
        action="suppress_candidate",
        transaction_id=transaction_id,
        text_preview=text[:80],
        extra={
            "suppression_reason": reason,
            "commit_reason": commit_reason,
            "candidate_text": text,
            "source_raw_event_ids": list(source_raw_event_ids or []),
            "previous_active_record_preserved": True,
            "stop_tail_candidate": True,
            "metadata": dict(metadata or {}),
        },
    )
    _jp_log(
        "CANONICAL_LEDGER_SUPPRESS_CANDIDATE",
        reason=reason,
        transaction_id=transaction_id,
        previous_active_record_preserved=True,
    )
    return {
        "ok": True,
        "applied_action": "suppress_candidate",
        "record_id": "",
        "revision_target_id": "",
        "previous_active_record_preserved": True,
        "transaction_id": transaction_id,
    }


def _suppress_record_unlocked(
    *,
    record_id: str = "",
    suppression_reason: str = "",
    commit_reason: str = "",
    transaction_id: str = "",
) -> dict[str, Any]:
    reason = str(suppression_reason or "").strip()
    if not reason:
        return {"ok": False, "reason": "suppression_reason_required"}

    # fixes TASK_1A_FINDINGS.md Pattern 1: positional "last active record" was
    # previously used as an implicit suppression target with no identity
    # verification. An explicit record_id is now required; no exact match ->
    # reject + log identity mismatch instead of guessing.
    if not record_id:
        _jp_log("IDENTITY_REJECTION", reason="suppress_missing_exact_target", transaction_id=transaction_id)
        return {"ok": False, "reason": "suppress_record_id_required"}
    target = _find_record_unlocked(record_id)
    if target is None:
        _jp_log(
            "IDENTITY_REJECTION",
            reason="suppress_target_not_found",
            transaction_id=transaction_id,
            record_id=record_id,
        )
        return {"ok": False, "reason": "no_target"}

    target["suppressed"] = True
    target["active"] = False
    target["applied_action"] = "suppress"
    target["suppression_reason"] = reason
    target["commit_reason"] = commit_reason
    target["updated_at"] = _now()
    rid = str(target.get("record_id"))
    _append_history_unlocked(
        action="suppress",
        transaction_id=transaction_id,
        record_id=rid,
        text_preview=str(target.get("final_text") or "")[:80],
        extra={"suppression_reason": reason},
    )
    _jp_log("CANONICAL_LEDGER_SUPPRESS", record_id=rid, reason=reason, transaction_id=transaction_id)
    return {
        "ok": True,
        "applied_action": "suppress",
        "record_id": rid,
        "transaction_id": transaction_id,
    }


def suppress_record(
    *,
    record_id: str = "",
    suppression_reason: str = "",
    commit_reason: str = "",
    transaction_id: str = "",
) -> dict[str, Any]:
    with _lock:
        _assert_not_frozen_unlocked()
        return _suppress_record_unlocked(
            record_id=record_id,
            suppression_reason=suppression_reason,
            commit_reason=commit_reason,
            transaction_id=transaction_id,
        )


def get_active_records() -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _active_records_unlocked()]


def get_record_history() -> list[dict[str, Any]]:
    with _lock:
        return list(_history)


def _action_counts_unlocked() -> dict[str, int]:
    counts = {"append": 0, "revise": 0, "no_op": 0, "suppress": 0, "suppress_candidate": 0}
    for ev in _history:
        act = str(ev.get("action") or "")
        if act in counts:
            counts[act] += 1
    return counts


def get_action_counts() -> dict[str, int]:
    with _lock:
        return _action_counts_unlocked()


def validate_internal_consistency() -> dict[str, Any]:
    with _lock:
        active = _active_records_unlocked()
        issues: list[str] = []
        without_lineage = [
            r["record_id"]
            for r in active
            if not r.get("stop_flush")
            and not r.get("synthetic_record")
            and not list(r.get("source_raw_event_ids") or [])
        ]
        if RAW_EVENT_LINEAGE_REQUIRED and without_lineage:
            issues.append(f"stable_records_without_lineage:{len(without_lineage)}")
        duplicate_active = len(active) != len({r.get("record_id") for r in active})
        if duplicate_active:
            issues.append("duplicate_active_record_ids")
        return {
            "ok": not issues,
            "active_record_count": len(active),
            "total_record_count": len(_records),
            "stable_records_without_lineage": len(without_lineage),
            "issues": issues,
            "frozen": _frozen,
            "ledger_generation": _ledger_generation,
            "mutation_sequence": _mutation_sequence,
        }


def freeze_snapshot() -> dict[str, Any]:
    global _frozen_snapshot, _frozen
    with _lock:
        if _frozen and _frozen_snapshot is not None:
            return dict(_frozen_snapshot)
        active = [dict(r) for r in _active_records_unlocked()]
        payload = {
            "snapshot_id": f"snap-{uuid.uuid4().hex[:12]}",
            "run_id": _run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "record_count": len(_records),
            "active_record_count": len(active),
            "records": active,
            "speaker_distribution": _speaker_distribution(active),
            "action_counts": _action_counts_unlocked(),
            "ledger_generation": _ledger_generation,
            "mutation_sequence": _mutation_sequence,
        }
        serialized = json.dumps(
            {k: v for k, v in payload.items() if k != "snapshot_sha256"},
            ensure_ascii=False,
            sort_keys=True,
        )
        payload["snapshot_sha256"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        _frozen_snapshot = payload
        _frozen = True
    _jp_log(
        "CANONICAL_LEDGER_FROZEN",
        snapshot_id=payload["snapshot_id"],
        active_record_count=payload["active_record_count"],
        ledger_generation=_ledger_generation,
    )
    return dict(payload)


def get_ledger_identity() -> dict[str, Any]:
    """Return generation / object identity / frozen flag for session evidence."""
    with _lock:
        return {
            "ledger_generation": int(_ledger_generation),
            "ledger_object_id": f"ledger-gen-{_ledger_generation}:{_run_id or 'none'}",
            "run_id": str(_run_id or ""),
            "frozen": bool(_frozen),
            "active_record_count": len(_active_records_unlocked()),
            "module_id": id(_records),
        }


def get_frozen_snapshot() -> Optional[dict[str, Any]]:
    with _lock:
        return dict(_frozen_snapshot) if _frozen_snapshot else None


def is_frozen() -> bool:
    with _lock:
        return _frozen


def serialize_export_payload(snapshot: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    snap = snapshot or get_frozen_snapshot()
    if not snap:
        raise PipelineIntegrityError("no frozen ledger snapshot for export")
    lines = []
    record_ids = []
    for rec in snap.get("records") or []:
        text = str(rec.get("final_text") or rec.get("assembler_text") or "").strip()
        if not text:
            continue
        from alpha.utils.ui_speaker_label import format_ui_speaker_line

        lines.append(format_ui_speaker_line(text))
        record_ids.append(str(rec.get("record_id")))
    body = "\n".join(lines)
    if body and not body.endswith("\n"):
        body += "\n"
    return {
        "lines": lines,
        "text": body,
        "record_ids": record_ids,
        "speaker_distribution": dict(snap.get("speaker_distribution") or {}),
        "snapshot_id": snap.get("snapshot_id"),
        "snapshot_sha256": snap.get("snapshot_sha256"),
        "active_record_count": int(snap.get("active_record_count") or len(record_ids)),
    }


def build_export_coverage_report(exported_record_ids: list[str]) -> dict[str, Any]:
    snap = get_frozen_snapshot()
    if not snap:
        return {"coverage_passed": False, "reason": "no_frozen_snapshot"}
    active_ids = [str(r.get("record_id")) for r in (snap.get("records") or [])]
    exported_set = set(exported_record_ids)
    active_set = set(active_ids)
    missing = sorted(active_set - exported_set)
    duplicate = sorted({x for x in exported_record_ids if exported_record_ids.count(x) > 1})
    with _lock:
        suppressed = [
            str(r.get("record_id"))
            for r in _records
            if r.get("suppressed")
        ]
        run_id = _run_id
    ratio = 1.0 if not active_set else len(exported_set & active_set) / len(active_set)
    return {
        "run_id": run_id,
        "snapshot_id": snap.get("snapshot_id"),
        "active_canonical_record_count": len(active_set),
        "final_export_line_count": len(exported_record_ids),
        "exported_record_ids": list(exported_record_ids),
        "missing_record_ids": missing,
        "duplicate_record_ids": duplicate,
        "suppressed_record_ids": suppressed,
        "unexplained_missing_record_count": len(missing),
        "coverage_ratio": round(ratio, 6),
        "coverage_passed": ratio == 1.0 and not duplicate and not missing,
    }


def lineage_reconciliation(raw_event_ids_from_file: set[str]) -> dict[str, Any]:
    referenced: set[str] = set()
    without_lineage = 0
    with _lock:
        for rec in _active_records_unlocked():
            ids = list(rec.get("source_raw_event_ids") or [])
            if not ids and not rec.get("stop_flush") and not rec.get("synthetic_record"):
                without_lineage += 1
            referenced.update(ids)
    unresolved = sorted(referenced - raw_event_ids_from_file)
    ratio = 1.0
    if referenced:
        ratio = len(referenced - set(unresolved)) / len(referenced)
    return {
        "lineage_coverage_ratio": round(ratio, 6),
        "unresolved_raw_event_ids": unresolved,
        "stable_records_without_lineage": without_lineage,
        "referenced_raw_event_count": len(referenced),
    }
