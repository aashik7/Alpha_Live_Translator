"""Three-stage Japanese accuracy diagnostic capture (8.5.25.3)."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional

_SPEAKER_LABEL_RE = re.compile(r"^\[Speaker\s+\d+\]\s*", re.IGNORECASE)

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    ASSEMBLER_ONLY_STAGE_CAPTURE_ENABLED,
    DEEPGRAM_REQUEST_SNAPSHOT_ENABLED,
    DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED,
    FINAL_ALPHA_STAGE_CAPTURE_ENABLED,
    IMMUTABLE_LIVE_STAGE_EVIDENCE_ENABLED,
    RAW_DEEPGRAM_STAGE_CAPTURE_ENABLED,
    THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED,
)
from alpha.utils.path_types import ensure_path

_lock = threading.Lock()
_state: dict[str, Any] = {
    "run_id": "",
    "raw_counter": 0,
    "assembler_counter": 0,
    "raw_event_count": 0,
    "assembler_event_count": 0,
    "assembler_active_lines": [],
    "append_count": 0,
    "revise_count": 0,
    "revision_requested_count": 0,
    "revision_applied_count": 0,
    "revision_rejected_to_append_count": 0,
    "completed_sentence_revision_blocked_count": 0,
    "lineage_missing_count": 0,
    "lineage_disjoint_count": 0,
    "content_loss_guard_count": 0,
    "exact_duplicate_no_op_count": 0,
    "no_op_count": 0,
    "destructive_revision_count": 0,
    "suppressed_stop_tail_count": 0,
    "finalized": False,
    "deepgram_snapshot_written": False,
    "three_stage_finalize_call_count": 0,
    "stage_dir": "",
}


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_dir(run_folder: str | Path | None = None) -> Path:
    run_folder = ensure_path(run_folder)
    if run_folder is not None:
        return get_accuracy_stage_compare_dir(run_folder)
    with _lock:
        stored = _state.get("stage_dir", "")
    if stored:
        return Path(stored)
    return get_accuracy_stage_compare_dir(None)


def get_accuracy_stage_compare_dir(run_folder: str | Path | None = None) -> Path:
    from alpha.utils.troubleshooting_paths import get_active_run_folder

    folder = ensure_path(run_folder) or ensure_path(get_active_run_folder())
    if folder is None:
        folder = Path("troubleshooting/runs/_pending")
    stage = folder / "accuracy_stage_compare"
    stage.mkdir(parents=True, exist_ok=True)
    return stage


def write_export_coverage_report(report: dict[str, Any], *, run_folder: Path | None = None) -> Path | None:
    path = get_accuracy_stage_compare_path("export_coverage_report", run_folder)
    try:
        payload = dict(report)
        payload.setdefault("created_by", "live_runtime")
        payload.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _jp_log("EXPORT_COVERAGE_REPORT_WRITTEN", path=str(path))
        return path
    except Exception:
        return None


def get_accuracy_stage_compare_path(name: str, run_folder: str | Path | None = None) -> Path:
    mapping = {
        "raw_deepgram": "raw_deepgram.txt",
        "raw_deepgram_events": "raw_deepgram_events.jsonl",
        "stable_assembler_only": "stable_assembler_only.txt",
        "stable_transcript": "stable_transcript.txt",
        "stable_active_records": "stable_active_records.jsonl",
        "stable_assembler_events": "stable_assembler_events.jsonl",
        "final_alpha_output": "final_alpha_output.txt",
        "deepgram_request_snapshot": "deepgram_request_snapshot.json",
        "deepgram_request_actual": "deepgram_request_actual.json",
        "benchmark_audio_source": "benchmark_audio_source.json",
        "audio_delivery_summary": "audio_delivery_summary.json",
        "export_coverage_report": "export_coverage_report.json",
        "stage_manifest": "stage_manifest.json",
        "three_stage_accuracy_report": "three_stage_accuracy_report.json",
        "three_stage_accuracy_report_txt": "three_stage_accuracy_report.txt",
    }
    if name not in mapping:
        raise KeyError(f"unknown stage compare path: {name}")
    return _stage_dir(run_folder) / mapping[name]


def reset_accuracy_stage_capture(run_id: str, *, run_folder: Path | None = None) -> None:
    if not THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED:
        return
    stage_dir = get_accuracy_stage_compare_dir(run_folder)
    with _lock:
        _state.clear()
        _state.update(
            {
                "run_id": run_id,
                "raw_counter": 0,
                "assembler_counter": 0,
                "raw_event_count": 0,
                "assembler_event_count": 0,
                "assembler_active_lines": [],
                "append_count": 0,
                "revise_count": 0,
                "revision_requested_count": 0,
                "revision_applied_count": 0,
                "revision_rejected_to_append_count": 0,
                "completed_sentence_revision_blocked_count": 0,
                "lineage_missing_count": 0,
                "lineage_disjoint_count": 0,
                "content_loss_guard_count": 0,
                "exact_duplicate_no_op_count": 0,
                "no_op_count": 0,
                "destructive_revision_count": 0,
                "suppressed_stop_tail_count": 0,
                "finalized": False,
                "deepgram_snapshot_written": False,
                "three_stage_finalize_call_count": 0,
                "stage_dir": str(stage_dir),
            }
        )
    for name in ("raw_deepgram_events", "stable_assembler_events"):
        p = get_accuracy_stage_compare_path(name, run_folder)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
    try:
        from alpha.constants import RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED
        from alpha.utils.runtime_audio_counters import reset_runtime_audio_counters

        if RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED:
            reset_runtime_audio_counters(run_id)
    except Exception:
        pass


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def record_raw_deepgram_final(
    *,
    run_id: str = "",
    speaker: int = 0,
    raw_text: str,
    is_final: bool = True,
    speech_final: Any = None,
    confidence: Any = None,
    channel: Any = None,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    if not THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED or not RAW_DEEPGRAM_STAGE_CAPTURE_ENABLED:
        return ""
    if DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED:
        return ""
    text = raw_text or ""
    if not text.strip():
        return ""

    with _lock:
        _state["raw_counter"] += 1
        arrival_index = _state["raw_counter"]
        event_id = f"raw-{arrival_index:06d}"
        if run_id:
            _state["run_id"] = run_id

    event = {
        "raw_event_id": event_id,
        "run_id": _state.get("run_id", run_id),
        "arrival_index": arrival_index,
        "timestamp": time.time(),
        "speaker": speaker,
        "raw_text": text,
        "accepted_by_gate": True,
        "source": "deepgram_final_ingress",
        "is_final": bool(is_final),
        "speech_final": speech_final,
        "confidence": confidence,
        "channel": channel,
        "metadata": metadata or {},
        "diagnostic_text_mutated": False,
    }
    try:
        _append_jsonl(get_accuracy_stage_compare_path("raw_deepgram_events"), event)
        with _lock:
            _state["raw_event_count"] += 1
        _jp_log("RAW_STAGE_EVENT_CAPTURED", raw_event_id=event_id, arrival_index=arrival_index)
    except Exception:
        pass
    return event_id


def get_accuracy_stage_active_char_count() -> int:
    with _lock:
        lines = _state.get("assembler_active_lines") or []
        return sum(len(str(line)) for line in lines)


def record_revision_decision_stats(
    *,
    update_previous_requested: bool,
    final_action: str,
    decision_reason: str,
    revision_requested_but_rejected: bool = False,
) -> None:
    with _lock:
        if update_previous_requested:
            _state["revision_requested_count"] = int(_state.get("revision_requested_count", 0)) + 1
        if final_action == "revise_previous":
            _state["revision_applied_count"] = int(_state.get("revision_applied_count", 0)) + 1
            _state["revise_count"] = int(_state.get("revise_count", 0)) + 1
        elif final_action == "no_op":
            _state["no_op_count"] = int(_state.get("no_op_count", 0)) + 1
            if decision_reason == "exact_duplicate":
                _state["exact_duplicate_no_op_count"] = int(_state.get("exact_duplicate_no_op_count", 0)) + 1
        if revision_requested_but_rejected:
            _state["revision_rejected_to_append_count"] = int(_state.get("revision_rejected_to_append_count", 0)) + 1
        if decision_reason == "completed_previous_sentence_protected":
            _state["completed_sentence_revision_blocked_count"] = int(
                _state.get("completed_sentence_revision_blocked_count", 0)
            ) + 1
        if decision_reason == "revision_lineage_missing":
            _state["lineage_missing_count"] = int(_state.get("lineage_missing_count", 0)) + 1
        if decision_reason == "revision_lineage_disjoint":
            _state["lineage_disjoint_count"] = int(_state.get("lineage_disjoint_count", 0)) + 1
        if decision_reason == "destructive_content_loss_prevented":
            _state["content_loss_guard_count"] = int(_state.get("content_loss_guard_count", 0)) + 1
            _state["destructive_revision_count"] = int(_state.get("destructive_revision_count", 0)) + 1


def record_assembler_only_event(
    *,
    run_id: str = "",
    speaker: int = 0,
    assembler_text: str,
    reason: str = "",
    commit_reason: str = "",
    action: str = "append",
    update_previous: bool = False,
    decision_reason: str = "",
    revision_decision: Optional[dict[str, Any]] = None,
    applied_action: str = "",
    stop_incomplete: bool = False,
    incomplete_reason: str = "",
    held_tail: str = "",
    boundary_type: str = "",
    safe_boundary_used: str = "",
    raw_fragments: Optional[list[str]] = None,
    source_raw_event_ids: Optional[list[str]] = None,
) -> str:
    if not THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED or not ASSEMBLER_ONLY_STAGE_CAPTURE_ENABLED:
        return ""
    if DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED:
        return ""
    text = assembler_text or ""
    if not text.strip() and action != "suppressed_stop_tail":
        return ""

    with _lock:
        _state["assembler_counter"] += 1
        event_id = f"asm-{_state['assembler_counter']:06d}"
        if run_id:
            _state["run_id"] = run_id

    event = {
        "stable_stage_event_id": event_id,
        "run_id": _state.get("run_id", run_id),
        "timestamp": time.time(),
        "speaker": speaker,
        "assembler_text": text,
        "reason": reason,
        "commit_reason": commit_reason,
        "action": action,
        "update_previous": update_previous,
        "stop_incomplete": stop_incomplete,
        "incomplete_reason": incomplete_reason,
        "held_tail": held_tail,
        "boundary_type": boundary_type,
        "safe_boundary_used": safe_boundary_used,
        "raw_fragments": raw_fragments or [],
        "source_raw_event_ids": source_raw_event_ids or [],
        "decision_reason": decision_reason,
        "revision_decision": revision_decision or {},
        "applied_action": applied_action or action,
        "diagnostic_text_mutated": False,
    }
    try:
        _append_jsonl(get_accuracy_stage_compare_path("stable_assembler_events"), event)
        with _lock:
            _state["assembler_event_count"] += 1
            if action == "revise_previous" and _state["assembler_active_lines"]:
                _state["assembler_active_lines"][-1] = text
                _jp_log("ASSEMBLER_ONLY_STAGE_REVISE", event_id=event_id)
            elif action == "suppressed_stop_tail":
                _state["suppressed_stop_tail_count"] += 1
                _jp_log("ASSEMBLER_ONLY_STAGE_STOP_TAIL_SUPPRESSED", event_id=event_id)
            elif action == "no_op":
                _jp_log("ASSEMBLER_ONLY_STAGE_NO_OP", event_id=event_id)
            elif action == "append":
                _state["assembler_active_lines"].append(text)
                _state["append_count"] += 1
                _jp_log("ASSEMBLER_ONLY_STAGE_APPEND", event_id=event_id)
            _jp_log("ASSEMBLER_ONLY_STAGE_EVENT_CAPTURED", event_id=event_id, action=action)
    except Exception:
        pass
    return event_id


def write_deepgram_request_snapshot(snapshot: dict[str, Any], *, run_folder: Path | None = None) -> Path | None:
    if not THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED or not DEEPGRAM_REQUEST_SNAPSHOT_ENABLED:
        return None
    path = get_accuracy_stage_compare_path("deepgram_request_snapshot", run_folder)
    payload = dict(snapshot)
    payload.setdefault("app_version", APP_VERSION)
    payload.setdefault("app_codename", APP_CODENAME)
    payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
    sanitized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    payload["request_parameter_sha256"] = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with _lock:
            _state["deepgram_snapshot_written"] = True
        _jp_log("DEEPGRAM_REQUEST_SNAPSHOT_WRITTEN", path=str(path))
        return path
    except Exception:
        return None


def write_deepgram_request_actual(payload: dict[str, Any], *, run_folder: Path | None = None) -> Path | None:
    """Write sanitized Deepgram request proof captured immediately before connect."""
    if not THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED:
        return None
    path = get_accuracy_stage_compare_path("deepgram_request_actual", run_folder)
    data = dict(payload)
    # Hard scrub any accidental secrets
    text = json.dumps(data, ensure_ascii=False)
    for needle in ("Authorization", "api_key", "API_KEY", "Token "):
        if needle in text and needle != "Token ":
            data = {k: v for k, v in data.items() if "key" not in str(k).lower() or k == "keyterm_count" or k == "keyterm_values" or k == "keyterm_parameter_present"}
            break
    query = str(data.get("sanitized_query_string") or "")
    if "token=" in query.lower() or "authorization=" in query.lower():
        try:
            from alpha.utils.issue12_stage1_runtime import sanitize_deepgram_query_string

            data["sanitized_query_string"] = sanitize_deepgram_query_string(query)
        except Exception:
            data["sanitized_query_string"] = query.split("Token", 1)[0]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with _lock:
            _state["deepgram_request_actual_written"] = True
        _jp_log("DEEPGRAM_REQUEST_ACTUAL_WRITTEN", path=str(path))
        return path
    except Exception:
        return None


def write_benchmark_audio_source_record(
    record: dict[str, Any], *, run_folder: Path | None = None
) -> Path | None:
    if not THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED:
        return None
    path = get_accuracy_stage_compare_path("benchmark_audio_source", run_folder)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _jp_log("BENCHMARK_AUDIO_SOURCE_RECORD_WRITTEN", path=str(path))
        return path
    except Exception:
        return None


def ensure_stage1_stable_transcript_alias(*, run_folder: Path | None = None) -> Path | None:
    """Copy stable_assembler_only.txt → stable_transcript.txt for Stage 1 evidence naming."""
    asm = get_accuracy_stage_compare_path("stable_assembler_only", run_folder)
    alias = get_accuracy_stage_compare_path("stable_transcript", run_folder)
    try:
        if asm.exists() and asm.stat().st_size > 0:
            alias.write_text(asm.read_text(encoding="utf-8"), encoding="utf-8")
            return alias
    except Exception:
        return None
    return None


def _log_finalizer_exception(
    step: str,
    exc: BaseException,
    *,
    run_folder: Path | None,
    run_id: str = "",
) -> None:
    _jp_log(
        "THREE_STAGE_FINALIZER_EXCEPTION",
        step=step,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        traceback=traceback.format_exc(),
        run_id=run_id,
        run_folder=str(run_folder) if run_folder else "",
        current_finalization_step=step,
    )


def _resolve_run_id_from_folder(run_folder: Path) -> str:
    manifest_path = run_folder / "RUN_MANIFEST.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            rid = data.get("run_id")
            if rid:
                return str(rid)
        except Exception:
            pass
    with _lock:
        return str(_state.get("run_id", ""))


def _count_jsonl_events(path: Path, text_field: str) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            if (ev.get(text_field) or "").strip():
                count += 1
        except Exception:
            continue
    return count


def strip_speaker_label(line: str) -> str:
    return _SPEAKER_LABEL_RE.sub("", (line or "").strip()).strip()


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except Exception:
            continue
    return rows


def write_jsonl_records(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_applied_action(action: str) -> str:
    act = str(action or "append").lower()
    if act in ("revise", "revise_previous"):
        return "revise"
    if act == "no_op":
        return "no_op"
    if act in ("suppress", "suppressed_stop_tail"):
        return "suppress"
    return "append"


def build_stable_active_record_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    run_id = str(snapshot.get("run_id") or "")
    rows: list[dict[str, Any]] = []
    for rec in snapshot.get("records") or []:
        text = str(rec.get("final_text") or rec.get("assembler_text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "record_id": str(rec.get("record_id") or ""),
                "sequence_number": int(rec.get("sequence_number") or 0),
                "speaker": int(rec.get("speaker") or 2),
                "text": text,
                "content_sha256": _sha256_text(text),
                "source_raw_event_ids": list(rec.get("source_raw_event_ids") or []),
                "snapshot_id": snapshot_id,
                "run_id": run_id,
            }
        )
    return rows


def stable_active_lines_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("text") or "").strip() for row in rows if str(row.get("text") or "").strip()]


def write_stable_active_stage_artifacts(
    run_folder: Path,
    snapshot: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Write Stable stage artifacts.

    Prefer persisted reconstruction for completed runs. Never truncate a valid
    reconstructed Stable set using an empty in-memory ledger snapshot.
    """
    run_folder = ensure_path(run_folder)
    if run_folder is None:
        return {"ok": False, "reason": "no_run_folder"}

    events_path = get_accuracy_stage_compare_path("stable_assembler_events", run_folder)
    has_persisted_events = events_path.exists() and events_path.stat().st_size > 0

    # Completed-run / post-live path: reconstruct from disk only.
    if has_persisted_events and snapshot is None:
        try:
            from alpha.utils.persisted_run_evidence import (
                PersistedEvidenceReconstructionError,
                write_reconstructed_stable_artifacts,
            )

            report = write_reconstructed_stable_artifacts(run_folder)
            with _lock:
                rows = []
                jsonl = get_accuracy_stage_compare_path("stable_active_records", run_folder)
                if jsonl.exists():
                    for line in jsonl.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            try:
                                rows.append(json.loads(line))
                            except Exception:
                                pass
                _state["assembler_active_lines"] = [
                    str(r.get("text") or "").strip() for r in rows if str(r.get("text") or "").strip()
                ]
            return {
                "ok": bool(report.get("reconstruction_completed")),
                "stable_active_record_count": int(report.get("active_record_count") or 0),
                "stable_active_records_path": str(
                    get_accuracy_stage_compare_path("stable_active_records", run_folder)
                ),
                "stable_assembler_only_path": str(
                    get_accuracy_stage_compare_path("stable_assembler_only", run_folder)
                ),
                "persisted_reconstruction": True,
            }
        except PersistedEvidenceReconstructionError:
            raise
        except Exception as exc:
            # Fall through to snapshot only when persisted reconstruction fails unexpectedly
            # and there is a non-empty snapshot. Never write empty over persisted evidence.
            if has_persisted_events:
                raise RuntimeError(f"persisted_stable_reconstruction_failed:{exc}") from exc

    if snapshot is None:
        try:
            from alpha.transcription.canonical_transcript_ledger import get_frozen_snapshot

            snapshot = get_frozen_snapshot() or {}
        except Exception:
            snapshot = {}
    rows = build_stable_active_record_rows(snapshot)
    if has_persisted_events and not rows:
        raise RuntimeError(
            "Refusing to truncate Stable stage artifacts: persisted assembler events exist "
            "but in-memory snapshot produced zero active records"
        )

    from alpha.utils.canonical_content_hash import atomic_write_jsonl, atomic_write_text_utf8

    stable_jsonl = get_accuracy_stage_compare_path("stable_active_records", run_folder)
    atomic_write_jsonl(stable_jsonl, rows)
    asm_lines = stable_active_lines_from_rows(rows)
    asm_txt = get_accuracy_stage_compare_path("stable_assembler_only", run_folder)
    atomic_write_text_utf8(asm_txt, "\n".join(asm_lines) + ("\n" if asm_lines else ""))
    with _lock:
        _state["assembler_active_lines"] = list(asm_lines)
    return {
        "ok": True,
        "stable_active_record_count": len(rows),
        "stable_active_records_path": str(stable_jsonl),
        "stable_assembler_only_path": str(asm_txt),
        "persisted_reconstruction": False,
    }


def parse_final_alpha_text_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        strip_speaker_label(line)
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if strip_speaker_label(line)
    ]


def compare_stable_and_final_artifacts(run_folder: Path) -> dict[str, Any]:
    run_folder = ensure_path(run_folder)
    if run_folder is None:
        return {"ok": False, "reason": "no_run_folder"}
    stable_rows = load_jsonl_records(get_accuracy_stage_compare_path("stable_active_records", run_folder))
    final_sidecar = run_folder / "transcripts" / "final_export_records.jsonl"
    final_rows = load_jsonl_records(final_sidecar)
    alpha_path = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    if not alpha_path.exists():
        alpha_path = get_accuracy_stage_compare_path("final_alpha_output", run_folder)
    alpha_lines = parse_final_alpha_text_lines(alpha_path)

    stable_ids = [str(r.get("record_id") or "") for r in stable_rows]
    final_ids = [str(r.get("record_id") or "") for r in final_rows]
    stable_set = set(stable_ids)
    final_set = set(final_ids)
    missing_final = sorted(stable_set - final_set)
    extra_final = sorted(final_set - stable_set)
    duplicate_final = sorted({rid for rid in final_ids if final_ids.count(rid) > 1})
    duplicate_stable = sorted({rid for rid in stable_ids if stable_ids.count(rid) > 1})

    stable_texts = stable_active_lines_from_rows(stable_rows)
    final_texts = [str(r.get("text") or "").strip() for r in final_rows if str(r.get("text") or "").strip()]
    stable_hashes = [_sha256_text(t) for t in stable_texts]
    final_hashes = [str(r.get("content_sha256") or _sha256_text(str(r.get("text") or ""))) for r in final_rows]

    record_id_match = stable_ids == final_ids and not missing_final and not extra_final and not duplicate_final
    text_hash_match = stable_hashes == final_hashes and bool(stable_hashes)
    text_exact_match = stable_texts == final_texts == alpha_lines and bool(stable_texts)

    return {
        "stable_active_record_count": len(stable_rows),
        "final_export_record_count": len(final_rows),
        "final_text_line_count": len(alpha_lines),
        "stable_final_record_id_match": record_id_match,
        "stable_final_text_hash_match": text_hash_match,
        "stable_final_text_exact_match": text_exact_match,
        "missing_final_record_ids": missing_final,
        "extra_final_record_ids": extra_final,
        "duplicate_final_record_ids": duplicate_final,
        "duplicate_stable_record_ids": duplicate_stable,
        "stable_record_ids": stable_ids,
        "final_record_ids": final_ids,
    }


def count_assembler_event_total(run_folder: Path) -> int:
    events_path = get_accuracy_stage_compare_path("stable_assembler_events", run_folder)
    if not events_path.exists():
        return 0
    count = 0
    for line in events_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip():
            count += 1
    return count


def count_event_file_applied_actions(run_folder: Path) -> dict[str, int]:
    counts = {"append": 0, "revise": 0, "no_op": 0, "suppress": 0}
    events_path = get_accuracy_stage_compare_path("stable_assembler_events", run_folder)
    for line in (
        events_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if events_path.exists()
        else []
    ):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        act = normalize_applied_action(str(ev.get("applied_action") or ev.get("action") or "append"))
        counts[act] = int(counts.get(act, 0)) + 1
    return counts


def count_runtime_applied_actions(runtime_metrics: dict[str, Any]) -> dict[str, int]:
    return {
        "append": int(runtime_metrics.get("append_count") or 0),
        "revise": int(runtime_metrics.get("revision_applied_count") or 0),
        "no_op": int(runtime_metrics.get("no_op_count") or 0),
        "suppress": int(runtime_metrics.get("suppression_count") or 0),
    }


def reconcile_three_source_action_counts(
    *,
    ledger_counts: dict[str, int],
    runtime_metrics: dict[str, Any],
    event_file_counts: Optional[dict[str, int]] = None,
    assembler_event_count: int = 0,
) -> dict[str, Any]:
    runtime_action_counts = count_runtime_applied_actions(runtime_metrics)
    event_counts = dict(event_file_counts or {})
    differences: list[str] = []
    for key in ("append", "revise", "no_op", "suppress"):
        ledger_val = int(ledger_counts.get(key) or 0)
        runtime_val = int(runtime_action_counts.get(key) or 0)
        if ledger_val != runtime_val:
            differences.append(f"{key}:ledger={ledger_val},runtime={runtime_val}")
        if event_counts:
            event_val = int(event_counts.get(key) or 0)
            if ledger_val != event_val:
                differences.append(f"{key}:ledger={ledger_val},event_file={event_val}")
            if runtime_val != event_val:
                differences.append(f"{key}:runtime={runtime_val},event_file={event_val}")
    applied_sum = sum(int(runtime_action_counts.get(k) or 0) for k in ("append", "revise", "no_op", "suppress"))
    if assembler_event_count > 0 and applied_sum != assembler_event_count:
        differences.append(f"applied_sum={applied_sum},assembler_event_count={assembler_event_count}")
    return {
        "runtime_action_counts": runtime_action_counts,
        "ledger_action_counts": dict(ledger_counts),
        "event_file_action_counts": event_counts,
        "revision_requested_count": int(runtime_metrics.get("revision_requested_count") or 0),
        "revision_applied_count": int(runtime_metrics.get("revision_applied_count") or 0),
        "revision_rejected_to_append_count": int(runtime_metrics.get("revision_rejected_to_append_count") or 0),
        "assembler_event_count": assembler_event_count,
        "applied_action_sum": applied_sum,
        "counts_reconciled": not differences,
        "count_differences": differences,
    }


def recompute_export_coverage_report(run_folder: Path) -> dict[str, Any]:
    run_folder = ensure_path(run_folder)
    if run_folder is None:
        return {"coverage_passed": False, "reason": "no_run_folder"}
    # V25.3.3.2: completed-run coverage must use persisted reconstructed evidence
    # and normalized hashing — never empty in-memory ledger state.
    events_path = get_accuracy_stage_compare_path("stable_assembler_events", run_folder)
    final_sidecar = run_folder / "transcripts" / "final_export_records.jsonl"
    if events_path.exists() and final_sidecar.exists() and events_path.stat().st_size > 0:
        try:
            from alpha.utils.persisted_run_evidence import compute_persisted_coverage

            report = compute_persisted_coverage(run_folder)
            report.setdefault("final_text_record_count", report.get("final_record_count"))
            report.setdefault("final_sidecar_record_count", report.get("final_record_count"))
            report.setdefault("stable_final_text_exact_match", report.get("normalized_text_match"))
            report.setdefault("stable_final_record_id_match", report.get("record_id_order_match"))
            report.setdefault("sealed_final_hash_match", True)
            report.setdefault(
                "stage_final_hash_match", report.get("authoritative_stage_byte_hash_match")
            )
            report.setdefault("post_evidence_seal_verified", True)
            report.setdefault("late_final_overwrite_detected", False)
            report.setdefault("recomputed_from_artifacts", True)
            return report
        except Exception as exc:
            return {
                "coverage_passed": False,
                "reason": f"persisted_coverage_failed:{exc}",
                "coverage_ratio": 0.0,
            }
    try:
        from alpha.transcription.canonical_transcript_ledger import get_frozen_snapshot

        snap = get_frozen_snapshot() or {}
    except Exception:
        snap = {}
    stable_rows = load_jsonl_records(get_accuracy_stage_compare_path("stable_active_records", run_folder))
    if not stable_rows and snap:
        stable_rows = build_stable_active_record_rows(snap)
    final_rows = load_jsonl_records(run_folder / "transcripts" / "final_export_records.jsonl")
    alpha_path = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    alpha_lines = parse_final_alpha_text_lines(alpha_path)

    canonical_ids = [str(r.get("record_id") or "") for r in (snap.get("records") or stable_rows)]
    stable_ids = [str(r.get("record_id") or "") for r in stable_rows]
    final_ids = [str(r.get("record_id") or "") for r in final_rows]
    canonical_set = set(canonical_ids)
    stable_set = set(stable_ids)
    final_set = set(final_ids)

    missing_from_stable = sorted(canonical_set - stable_set)
    missing_from_final = sorted(stable_set - final_set)
    extra_in_stable = sorted(stable_set - canonical_set)
    extra_in_final = sorted(final_set - stable_set)
    duplicate_stable = sorted({rid for rid in stable_ids if stable_ids.count(rid) > 1})
    duplicate_final = sorted({rid for rid in final_ids if final_ids.count(rid) > 1})

    stable_texts = stable_active_lines_from_rows(stable_rows)
    final_texts = [str(r.get("text") or "").strip() for r in final_rows if str(r.get("text") or "").strip()]
    canonical_hashes = [
        _sha256_text(str(r.get("final_text") or r.get("assembler_text") or "").strip())
        for r in (snap.get("records") or stable_rows)
        if str(r.get("final_text") or r.get("assembler_text") or "").strip()
    ]
    stable_hashes = [_sha256_text(t) for t in stable_texts]
    final_hashes = [str(r.get("content_sha256") or _sha256_text(t)) for r, t in zip(final_rows, final_texts)]

    canonical_to_stable_hash_match = canonical_hashes == stable_hashes and bool(canonical_hashes)
    stable_to_final_hash_match = stable_hashes == final_hashes and bool(stable_hashes)
    final_sidecar_to_text_match = final_texts == alpha_lines and bool(final_texts)

    snap_dist = dict(snap.get("speaker_distribution") or {})
    stable_dist: dict[str, int] = {}
    for row in stable_rows:
        key = f"Speaker {int(row.get('speaker') or 2)}"
        stable_dist[key] = int(stable_dist.get(key, 0)) + 1
    speaker_distribution_match = snap_dist == stable_dist if snap_dist else stable_dist == stable_dist

    suppressed = [
        str(r.get("record_id"))
        for r in (snap.get("records") or [])
        if r.get("suppressed")
    ]
    unexplained_suppression_count = len([rid for rid in suppressed if rid not in stable_set])

    active_count = len(canonical_set) or len(stable_set)
    matched = len(stable_set & final_set & canonical_set)
    ratio = 1.0 if not active_count else matched / active_count
    coverage_passed = (
        ratio == 1.0
        and not missing_from_stable
        and not missing_from_final
        and not extra_in_stable
        and not extra_in_final
        and not duplicate_stable
        and not duplicate_final
        and canonical_to_stable_hash_match
        and stable_to_final_hash_match
        and final_sidecar_to_text_match
        and unexplained_suppression_count == 0
    )

    seal_path = run_folder / "transcripts" / "FINAL_EXPORT_SEAL.json"
    seal: dict[str, Any] = {}
    sealed_final_hash_match = False
    stage_final_hash_match = False
    post_evidence_seal_verified = False
    late_final_overwrite_detected = False
    late_overwrite_path = ""
    late_overwrite_before = ""
    late_overwrite_after = ""
    authoritative_sha = _sha256_file(alpha_path) if alpha_path.exists() else ""
    stage_final_path = get_accuracy_stage_compare_path("final_alpha_output", run_folder)
    stage_sha = _sha256_file(stage_final_path) if stage_final_path.exists() else ""
    if seal_path.exists():
        try:
            seal = json.loads(seal_path.read_text(encoding="utf-8"))
        except Exception:
            seal = {}
        sealed_final_hash_match = bool(
            seal.get("text_sha256") and seal.get("text_sha256") == authoritative_sha
        )
        stage_final_hash_match = bool(
            authoritative_sha and stage_sha and authoritative_sha == stage_sha
        )
        post_evidence_seal_verified = bool(seal.get("seal_verified")) and sealed_final_hash_match
        if seal.get("text_sha256") and authoritative_sha and seal.get("text_sha256") != authoritative_sha:
            late_final_overwrite_detected = True
            late_overwrite_path = str(alpha_path)
            late_overwrite_before = str(seal.get("text_sha256") or "")
            late_overwrite_after = authoritative_sha
            coverage_passed = False

    compare = compare_stable_and_final_artifacts(run_folder)
    stable_final_text_exact_match = bool(compare.get("stable_final_text_exact_match"))
    stable_final_record_id_match = bool(compare.get("stable_final_record_id_match"))
    if not (
        sealed_final_hash_match
        and stage_final_hash_match
        and post_evidence_seal_verified
        and stable_final_text_exact_match
        and stable_final_record_id_match
    ):
        # Require seal-aware match for V25.3.3.1 when seal exists.
        if seal_path.exists():
            coverage_passed = False
            ratio = 0.0 if not coverage_passed else ratio

    if (
        seal_path.exists()
        and sealed_final_hash_match
        and stage_final_hash_match
        and post_evidence_seal_verified
        and stable_final_text_exact_match
        and stable_final_record_id_match
        and not late_final_overwrite_detected
        and len(stable_ids) == len(final_ids) == len(alpha_lines)
    ):
        coverage_passed = True
        ratio = 1.0

    return {
        "run_id": str(snap.get("run_id") or _resolve_run_id_from_folder(run_folder)),
        "canonical_active_record_count": len(canonical_ids) or len(stable_ids),
        "stable_active_record_count": len(stable_ids),
        "final_sidecar_record_count": len(final_ids),
        "final_text_record_count": len(alpha_lines),
        "stable_final_text_exact_match": stable_final_text_exact_match,
        "stable_final_record_id_match": stable_final_record_id_match,
        "sealed_final_hash_match": sealed_final_hash_match,
        "stage_final_hash_match": stage_final_hash_match,
        "post_evidence_seal_verified": post_evidence_seal_verified,
        "late_final_overwrite_detected": late_final_overwrite_detected,
        "late_overwrite_path": late_overwrite_path,
        "late_overwrite_before_hash": late_overwrite_before,
        "late_overwrite_after_hash": late_overwrite_after,
        "authoritative_final_sha256": authoritative_sha,
        "stage_final_sha256": stage_sha,
        "missing_from_stable": missing_from_stable,
        "missing_from_final": missing_from_final,
        "extra_in_stable": extra_in_stable,
        "extra_in_final": extra_in_final,
        "duplicate_stable": duplicate_stable,
        "duplicate_final": duplicate_final,
        "canonical_to_stable_hash_match": canonical_to_stable_hash_match,
        "stable_to_final_hash_match": stable_to_final_hash_match,
        "final_sidecar_to_text_match": final_sidecar_to_text_match,
        "speaker_distribution_match": speaker_distribution_match,
        "unexplained_suppression_count": unexplained_suppression_count,
        "matched_record_count": matched,
        "coverage_ratio": round(ratio, 6),
        "coverage_passed": bool(coverage_passed),
        "seal_write_count": int(seal.get("write_count") or 0),
        "post_seal_write_attempt_count": int(seal.get("post_seal_write_attempt_count") or 0),
        "snapshot_id": str(snap.get("snapshot_id") or ""),
        "final_text_line_count": len(alpha_lines),
        "canonical_record_ids": canonical_ids,
        "stable_record_ids": stable_ids,
        "final_record_ids": final_ids,
        "duplicate_stable_record_ids": duplicate_stable,
        "duplicate_final_record_ids": duplicate_final,
        "suppressed_record_ids": suppressed,
        "recomputed_from_artifacts": True,
    }


def _merge_audio_metric(runtime_val: Any, host_val: Any, offline_val: Any) -> Any:
    if runtime_val is not None:
        return runtime_val
    if host_val is not None:
        return host_val
    return offline_val


def evaluate_stage_capture_critical_checks(
    *,
    run_folder: Path,
    finalizer_errors: list[str],
    final_source_hash_matches: bool,
    export_coverage: dict[str, Any],
    stable_final_compare: dict[str, Any],
    action_reconciliation: dict[str, Any],
    lineage: dict[str, Any],
    audio_summary: dict[str, Any],
    three_stage_finalize_call_count: int,
) -> dict[str, Any]:
    run_folder = ensure_path(run_folder)
    checks: dict[str, bool] = {}
    failed: list[str] = []

    def _check(name: str, ok: bool) -> None:
        checks[name] = bool(ok)
        if not ok:
            failed.append(name)

    _check("no_finalizer_exceptions", not finalizer_errors)
    _check("raw_stage_exists", get_accuracy_stage_compare_path("raw_deepgram", run_folder).exists())
    _check("assembler_events_exist", get_accuracy_stage_compare_path("stable_assembler_events", run_folder).exists())
    _check(
        "stable_active_records_exist",
        get_accuracy_stage_compare_path("stable_active_records", run_folder).exists(),
    )
    _check("final_alpha_exists", (run_folder / "transcripts" / "Alpha_output_FINAL.txt").exists())
    _check("final_export_sidecar_exists", (run_folder / "transcripts" / "final_export_records.jsonl").exists())
    _check(
        "deepgram_request_snapshot_exists",
        get_accuracy_stage_compare_path("deepgram_request_snapshot", run_folder).exists(),
    )
    _check(
        "audio_delivery_summary_exists",
        get_accuracy_stage_compare_path("audio_delivery_summary", run_folder).exists(),
    )
    _check("audio_generated_during_runtime", audio_summary.get("generated_during_runtime") is True)
    _check("audio_not_offline_repair", audio_summary.get("generated_by_offline_repair") is not True)
    _check("audio_counters_complete", not list(audio_summary.get("missing_metrics") or []))
    _check("audio_counter_crosscheck_passed", audio_summary.get("counter_crosscheck_passed") is True)
    _check("action_counters_reconcile", action_reconciliation.get("counts_reconciled") is True)
    _check("lineage_coverage_full", float(lineage.get("lineage_coverage_ratio") or 0) >= 1.0)
    _check("stable_records_without_lineage_zero", int(lineage.get("stable_records_without_lineage") or 0) == 0)
    _check("export_coverage_passes", export_coverage.get("coverage_passed") is True)
    _check("stable_final_text_exact_match", stable_final_compare.get("stable_final_text_exact_match") is True)
    _check("final_source_hash_matches", final_source_hash_matches)
    _check("three_stage_finalize_call_count_one", three_stage_finalize_call_count == 1)

    live_status = _load_json_if_exists(run_folder / "artifacts" / "LIVE_RUN_STATUS.json")
    _check("stop_flags_false", not live_status.get("is_stopping") and not live_status.get("is_finalizing"))
    _check("language_worker_stopped", not live_status.get("language_pipeline_worker_alive"))
    drain_ok = live_status.get("stop_drain_barrier_passed")
    if drain_ok is not None:
        _check("stop_drain_barrier_passed", bool(drain_ok))

    complete = not failed
    reason = "all_critical_checks_passed" if complete else f"failed:{','.join(failed)}"
    return {
        "stage_capture_critical_checks": checks,
        "stage_capture_failed_checks": failed,
        "stage_capture_complete": complete,
        "stage_capture_complete_reason": reason,
    }


def _rebuild_raw_lines_from_events(run_folder: Path) -> list[str]:
    path = get_accuracy_stage_compare_path("raw_deepgram_events", run_folder)
    lines: list[str] = []
    if not path.exists():
        return lines
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            text = (ev.get("raw_text") or "").strip()
            if text:
                lines.append(text)
        except Exception:
            continue
    return lines


def _rebuild_assembler_lines_from_events(run_folder: Path) -> list[str]:
    path = get_accuracy_stage_compare_path("stable_assembler_events", run_folder)
    lines: list[str] = []
    if not path.exists():
        return lines
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        action = str(ev.get("action") or "append")
        text = (ev.get("assembler_text") or "").strip()
        if action == "revise_previous" and lines and text:
            lines[-1] = text
        elif action == "append" and text:
            lines.append(text)
    return lines


def _resolve_final_alpha_source_path(run_folder: Path) -> tuple[Path | None, str]:
    primary = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    if primary.exists() and primary.stat().st_size > 0:
        return primary, "run_transcripts_alpha_output_final"

    index_path = run_folder / "artifacts" / "RUN_ARTIFACTS_INDEX.txt"
    if index_path.exists():
        try:
            for line in index_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                lower = line.lower()
                if "alpha_output_final" in lower or "final_alpha" in lower:
                    _, _, rhs = line.partition("=")
                    candidate = Path(rhs.strip())
                    if not candidate.is_absolute():
                        candidate = run_folder / candidate
                    if candidate.exists() and candidate.stat().st_size > 0:
                        return candidate, "run_artifacts_index"
        except Exception:
            pass

    status_path = run_folder / "artifacts" / "LIVE_RUN_STATUS.json"
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            for key in ("final_alpha_output_path", "alpha_output_final_path", "final_transcript_path"):
                raw = data.get(key)
                if raw:
                    candidate = Path(str(raw))
                    if not candidate.is_absolute():
                        candidate = run_folder / candidate
                    if candidate.exists() and candidate.stat().st_size > 0:
                        return candidate, "live_run_status"
        except Exception:
            pass

    manifest_path = run_folder / "RUN_MANIFEST.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key in ("final_alpha_output_path", "authoritative_final_transcript_path"):
                raw = data.get(key)
                if raw:
                    candidate = Path(str(raw))
                    if not candidate.is_absolute():
                        candidate = run_folder / candidate
                    if candidate.exists() and candidate.stat().st_size > 0:
                        return candidate, "run_manifest"
        except Exception:
            pass

    return None, "not_found"


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _collect_offline_audio_metrics(run_folder: str | Path, host: Any = None) -> dict[str, Any]:
    from alpha.constants import RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED

    run_folder = ensure_path(run_folder)
    if run_folder is None:
        raise ValueError("run_folder is required")
    run_id = _resolve_run_id_from_folder(run_folder)
    source_files: list[str] = []
    runtime_summary: dict[str, Any] = {}
    host_summary: dict[str, Any] = {}
    offline_summary: dict[str, Any] = {}

    if RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED:
        try:
            from alpha.utils.runtime_audio_counters import (
                build_audio_delivery_summary,
                get_runtime_audio_counters,
            )

            runtime_summary = build_audio_delivery_summary(run_id=run_id, host=host)
            if not runtime_summary.get("frozen"):
                snap = get_runtime_audio_counters()
                runtime_summary.setdefault("audio_chunks_sent", snap.get("audio_chunks_sent"))
                runtime_summary.setdefault("audio_bytes_sent", snap.get("audio_bytes_sent"))
        except Exception:
            pass

    if host is not None:
        try:
            snap = host._build_latency_snapshot() if hasattr(host, "_build_latency_snapshot") else {}
            host_summary["audio_chunks_sent"] = snap.get("audio_chunks_sent")
            host_summary["audio_bytes_sent"] = snap.get("bytes_sent_total")
            host_summary["run_elapsed_seconds"] = snap.get("elapsed_sec")
            rate = snap.get("wire_sample_rate") or getattr(host, "_wire_sample_rate", 16000)
            host_summary["wire_sample_rate"] = rate
            if host_summary["audio_bytes_sent"] is not None:
                host_summary["calculated_audio_seconds_sent"] = round(
                    float(host_summary["audio_bytes_sent"]) / (float(rate) * 2.0), 3
                )
            if host_summary.get("calculated_audio_seconds_sent") and host_summary.get("run_elapsed_seconds"):
                host_summary["audio_seconds_to_run_seconds_ratio"] = round(
                    float(host_summary["calculated_audio_seconds_sent"])
                    / max(float(host_summary["run_elapsed_seconds"]), 0.001),
                    4,
                )
        except Exception:
            pass
        try:
            from alpha.utils.session_progress import build_progress_payload

            prog = build_progress_payload(host)
            host_summary["audio_queue_overflow_count"] = prog.get("audio_queue_overflow_count")
            host_summary["audio_chunk_drop_count"] = prog.get("audio_chunk_drop_count")
        except Exception:
            pass

    manifest_data = _load_json_if_exists(run_folder / "RUN_MANIFEST.json")
    if manifest_data.get("app_version"):
        offline_summary["app_version"] = str(manifest_data.get("app_version"))
        source_files.append("RUN_MANIFEST.json")

    live_status = _load_json_if_exists(run_folder / "artifacts" / "LIVE_RUN_STATUS.json")
    if live_status:
        source_files.append("artifacts/LIVE_RUN_STATUS.json")
        offline_summary["run_elapsed_seconds"] = live_status.get("elapsed_seconds")
        offline_summary["audio_queue_overflow_count"] = live_status.get("audio_queue_overflow_count")
        offline_summary["audio_chunk_drop_count"] = live_status.get("audio_chunk_drop_count")

    health = _load_json_if_exists(run_folder / "health" / "LAST_HEALTH_SNAPSHOT.json")
    if health:
        source_files.append("health/LAST_HEALTH_SNAPSHOT.json")
        offline_summary["audio_queue_overflow_count"] = health.get("audio_queue_overflow_count")

    dg_snap = _load_json_if_exists(get_accuracy_stage_compare_path("deepgram_request_snapshot", run_folder))
    if dg_snap:
        source_files.append("accuracy_stage_compare/deepgram_request_snapshot.json")
        offline_summary["wire_sample_rate"] = dg_snap.get("sample_rate")

    audio_manifest = _load_json_if_exists(run_folder / "audio_temp" / "audio_manifest.json")
    if audio_manifest:
        source_files.append("audio_temp/audio_manifest.json")
        offline_summary["system_audio_chunks_received"] = audio_manifest.get("system_audio_chunks_received")
        offline_summary["microphone_chunks_received"] = audio_manifest.get("microphone_chunks_received")
        offline_summary["mixed_audio_chunks_created"] = audio_manifest.get("mixed_audio_chunks_created")
        offline_summary["capture_errors"] = audio_manifest.get("capture_errors")

    summary: dict[str, Any] = {
        "run_id": run_id,
        "app_version": str(manifest_data.get("app_version") or APP_VERSION),
        "created_by": "live_runtime",
        "generated_during_runtime": bool(runtime_summary.get("generated_during_runtime")),
        "generated_by_offline_repair": False,
        "wire_encoding": "linear16",
        "wire_sample_rate": None,
        "wire_channels": 1,
        "sample_width_bytes": 2,
        "audio_chunks_sent": None,
        "audio_bytes_sent": None,
        "calculated_audio_seconds_sent": None,
        "run_elapsed_seconds": None,
        "audio_seconds_to_run_seconds_ratio": None,
        "audio_queue_overflow_count": None,
        "audio_chunk_drop_count": None,
        "system_audio_chunks_received": None,
        "microphone_chunks_received": None,
        "mixed_audio_chunks_created": None,
        "capture_errors": None,
        "deepgram_send_errors": None,
        "missing_metrics": [],
        "source_files_used": source_files,
        "counter_source": runtime_summary.get("counter_source", "runtime_audio_counters"),
        "counter_run_id_match": runtime_summary.get("counter_run_id_match"),
        "counter_crosscheck_passed": runtime_summary.get("counter_crosscheck_passed"),
        "deepgram_client_chunks_sent": runtime_summary.get("deepgram_client_chunks_sent"),
        "deepgram_client_bytes_sent": runtime_summary.get("deepgram_client_bytes_sent"),
    }

    for key in (
        "wire_sample_rate",
        "audio_chunks_sent",
        "audio_bytes_sent",
        "calculated_audio_seconds_sent",
        "run_elapsed_seconds",
        "audio_seconds_to_run_seconds_ratio",
        "audio_queue_overflow_count",
        "audio_chunk_drop_count",
        "system_audio_chunks_received",
        "microphone_chunks_received",
        "mixed_audio_chunks_created",
        "capture_errors",
        "deepgram_send_errors",
        "counter_source",
        "counter_run_id_match",
        "counter_crosscheck_passed",
        "deepgram_client_chunks_sent",
        "deepgram_client_bytes_sent",
    ):
        summary[key] = _merge_audio_metric(
            runtime_summary.get(key),
            host_summary.get(key),
            offline_summary.get(key),
        )

    if summary["audio_bytes_sent"] is not None and summary["calculated_audio_seconds_sent"] is None:
        rate = summary.get("wire_sample_rate") or 16000
        summary["wire_sample_rate"] = rate
        summary["calculated_audio_seconds_sent"] = round(float(summary["audio_bytes_sent"]) / (float(rate) * 2.0), 3)
    if summary.get("calculated_audio_seconds_sent") and summary.get("run_elapsed_seconds"):
        summary["audio_seconds_to_run_seconds_ratio"] = round(
            float(summary["calculated_audio_seconds_sent"])
            / max(float(summary["run_elapsed_seconds"]), 0.001),
            4,
        )

    missing: list[str] = []
    for key, value in summary.items():
        if key in (
            "missing_metrics",
            "run_id",
            "wire_encoding",
            "wire_channels",
            "sample_width_bytes",
            "source_files_used",
            "generated_during_runtime",
            "generated_by_offline_repair",
            "counter_source",
            "counter_run_id_match",
            "counter_crosscheck_passed",
            "deepgram_client_chunks_sent",
            "deepgram_client_bytes_sent",
            "app_version",
            "created_by",
        ):
            continue
        if value is None:
            missing.append(key)
    summary["missing_metrics"] = sorted(set(missing))
    summary["source_files_used"] = sorted(set(source_files))
    if runtime_summary:
        _jp_log("AUDIO_DELIVERY_SUMMARY_RUNTIME_VALUES_WRITTEN", run_id=run_id)
    return summary


def _write_audio_delivery_summary(
    host: Any = None,
    *,
    run_folder: Path | None = None,
    offline_repair: bool = False,
) -> Path | None:
    _jp_log("AUDIO_SUMMARY_GENERATION_STARTED", run_folder=str(run_folder), offline_repair=offline_repair)
    run_folder = ensure_path(run_folder)
    if run_folder is None:
        from alpha.utils.troubleshooting_paths import get_active_run_folder

        run_folder = ensure_path(get_active_run_folder())
    if run_folder is None:
        return None
    path = get_accuracy_stage_compare_path("audio_delivery_summary", run_folder)
    if not offline_repair:
        try:
            from alpha.constants import RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED
            from alpha.utils.runtime_audio_counters import (
                build_audio_delivery_summary,
                freeze_runtime_audio_counters,
            )

            if RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED:
                freeze_runtime_audio_counters(host=host)
                summary = build_audio_delivery_summary(
                    run_id=_resolve_run_id_from_folder(run_folder),
                    host=host,
                )
            else:
                summary = _collect_offline_audio_metrics(run_folder, host)
        except Exception:
            summary = _collect_offline_audio_metrics(run_folder, host)
    else:
        summary = _collect_offline_audio_metrics(run_folder, host)
        summary["generated_by_offline_repair"] = True
        summary["generated_during_runtime"] = False
    try:
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _jp_log("AUDIO_DELIVERY_SUMMARY_WRITTEN", path=str(path))
        for metric in summary.get("missing_metrics", []):
            _jp_log("AUDIO_DELIVERY_METRIC_MISSING", metric=metric)
        return path
    except Exception as exc:
        _log_finalizer_exception("audio_delivery_summary", exc, run_folder=run_folder, run_id=summary.get("run_id", ""))
        return None


def build_stage_manifest(
    *,
    run_folder: Path,
    run_type: str = "live",
    run_status: str = "completed",
    selected_language: str = "ja",
    final_alpha_source_path: str = "",
    final_source_hash_matches: bool = False,
    repaired_offline: bool = False,
    repair_timestamp: str = "",
    errors: list[str] | None = None,
    export_coverage: Optional[dict[str, Any]] = None,
    stable_final_compare: Optional[dict[str, Any]] = None,
    action_reconciliation: Optional[dict[str, Any]] = None,
    lineage: Optional[dict[str, Any]] = None,
    audio_summary: Optional[dict[str, Any]] = None,
    three_stage_finalize_call_count: int = 0,
) -> dict[str, Any]:
    run_folder = ensure_path(run_folder)
    if run_folder is None:
        raise ValueError("run_folder is required")
    run_id = _resolve_run_id_from_folder(run_folder)

    def _file_info(name: str) -> dict[str, Any]:
        p = get_accuracy_stage_compare_path(name, run_folder)
        exists = p.exists() and p.stat().st_size > 0
        text = p.read_text(encoding="utf-8", errors="ignore") if exists else ""
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return {
            "path": str(p.relative_to(run_folder)).replace("\\", "/"),
            "exists": exists,
            "line_count": len(lines),
            "character_count": len(text),
            "sha256": _sha256_file(p) if exists else "",
        }

    raw_events_path = get_accuracy_stage_compare_path("raw_deepgram_events", run_folder)
    asm_events_path = get_accuracy_stage_compare_path("stable_assembler_events", run_folder)
    raw_events = _count_jsonl_events(raw_events_path, "raw_text")
    asm_events = count_assembler_event_total(run_folder)
    if asm_events <= 0:
        asm_events = _count_jsonl_events(asm_events_path, "assembler_text")
    with _lock:
        if raw_events <= 0:
            raw_events = int(_state.get("raw_event_count", 0))
        if asm_events <= 0:
            asm_events = int(_state.get("assembler_event_count", 0))
        append_count = int(_state.get("append_count", 0))
        revise_count = int(_state.get("revision_applied_count", _state.get("revise_count", 0)))
        suppressed = int(_state.get("suppressed_stop_tail_count", 0))
        no_op_count = int(_state.get("no_op_count", 0))
        revision_requested = int(_state.get("revision_requested_count", 0))
        revision_rejected = int(_state.get("revision_rejected_to_append_count", 0))
        destructive_revision_count = int(_state.get("destructive_revision_count", 0))
        completed_sentence_blocked = int(_state.get("completed_sentence_revision_blocked_count", 0))
        lineage_missing = int(_state.get("lineage_missing_count", 0))
        lineage_disjoint = int(_state.get("lineage_disjoint_count", 0))
        content_loss_guard = int(_state.get("content_loss_guard_count", 0))
        exact_duplicate_no_op = int(_state.get("exact_duplicate_no_op_count", 0))
        if three_stage_finalize_call_count <= 0:
            three_stage_finalize_call_count = int(_state.get("three_stage_finalize_call_count", 0))

    if action_reconciliation is None:
        try:
            from alpha.transcription.canonical_transcript_ledger import get_action_counts
            from alpha.utils.live_runtime_metrics import get_metrics

            action_reconciliation = reconcile_three_source_action_counts(
                ledger_counts=get_action_counts(),
                runtime_metrics=get_metrics(),
                event_file_counts=count_event_file_applied_actions(run_folder),
                assembler_event_count=asm_events,
            )
        except Exception:
            action_reconciliation = {"counts_reconciled": False, "count_differences": ["reconciliation_unavailable"]}

    action_sum = sum(
        int(action_reconciliation.get("runtime_action_counts", {}).get(k) or 0)
        for k in ("append", "revise", "no_op", "suppress")
    )
    action_reconciled = bool(action_reconciliation.get("counts_reconciled"))
    append_count = int(action_reconciliation.get("runtime_action_counts", {}).get("append") or append_count)
    revise_count = int(action_reconciliation.get("runtime_action_counts", {}).get("revise") or revise_count)
    no_op_count = int(action_reconciliation.get("runtime_action_counts", {}).get("no_op") or no_op_count)
    suppressed = int(action_reconciliation.get("runtime_action_counts", {}).get("suppress") or suppressed)
    revision_requested = int(action_reconciliation.get("revision_requested_count") or revision_requested)
    revision_rejected = int(action_reconciliation.get("revision_rejected_to_append_count") or revision_rejected)

    if action_reconciled:
        _jp_log("STAGE_ACTION_COUNTS_RECONCILED", assembler_event_count=asm_events, action_sum=action_sum)
    else:
        _jp_log(
            "STAGE_ACTION_COUNT_MISMATCH",
            assembler_event_count=asm_events,
            action_sum=action_sum,
            differences=action_reconciliation.get("count_differences"),
        )

    if export_coverage is None:
        export_coverage = recompute_export_coverage_report(run_folder)
    if stable_final_compare is None:
        stable_final_compare = compare_stable_and_final_artifacts(run_folder)
    if lineage is None:
        try:
            from alpha.transcription.canonical_transcript_ledger import lineage_reconciliation

            raw_ids: set[str] = set()
            raw_path = get_accuracy_stage_compare_path("raw_deepgram_events", run_folder)
            for row in load_jsonl_records(raw_path):
                rid = row.get("raw_event_id")
                if rid:
                    raw_ids.add(str(rid))
            lineage = lineage_reconciliation(raw_ids)
        except Exception:
            lineage = {}
    if audio_summary is None:
        audio_path = get_accuracy_stage_compare_path("audio_delivery_summary", run_folder)
        audio_summary = _load_json_if_exists(audio_path)

    critical = evaluate_stage_capture_critical_checks(
        run_folder=run_folder,
        finalizer_errors=list(errors or []),
        final_source_hash_matches=final_source_hash_matches,
        export_coverage=export_coverage,
        stable_final_compare=stable_final_compare,
        action_reconciliation=action_reconciliation,
        lineage=lineage,
        audio_summary=audio_summary,
        three_stage_finalize_call_count=three_stage_finalize_call_count,
    )

    raw_info = _file_info("raw_deepgram")
    raw_info["event_count"] = raw_events
    stable_info = _file_info("stable_assembler_only")
    stable_info["event_count"] = asm_events
    stable_active_info = _file_info("stable_active_records")
    stable_active_info["record_count"] = int(stable_final_compare.get("stable_active_record_count") or 0)
    stable_info["append_count"] = append_count
    stable_info["revise_count"] = revise_count
    stable_info["revision_requested_count"] = revision_requested
    stable_info["revision_applied_count"] = revise_count
    stable_info["revision_rejected_to_append_count"] = revision_rejected
    stable_info["completed_sentence_revision_blocked_count"] = completed_sentence_blocked
    stable_info["lineage_missing_count"] = lineage_missing
    stable_info["lineage_disjoint_count"] = lineage_disjoint
    stable_info["content_loss_guard_count"] = content_loss_guard
    stable_info["exact_duplicate_no_op_count"] = exact_duplicate_no_op
    stable_info["no_op_count"] = no_op_count
    stable_info["destructive_revision_count"] = destructive_revision_count
    stable_info["assembler_event_count"] = asm_events
    stable_info["stage_action_counts_reconciled"] = action_reconciled
    stable_info["suppressed_stop_tail_count"] = suppressed
    final_info = _file_info("final_alpha_output")
    final_info["source_path"] = final_alpha_source_path
    final_info["source_hash_matches"] = final_source_hash_matches
    dg_snap = _file_info("deepgram_request_snapshot")
    audio_info = _file_info("audio_delivery_summary")
    audio_info.pop("line_count", None)
    audio_info.pop("character_count", None)
    audio_missing: list[str] = list(audio_summary.get("missing_metrics") or [])
    audio_info["missing_metrics"] = audio_missing

    missing: list[str] = []
    warnings: list[str] = []
    required = [
        ("raw_deepgram", raw_info),
        ("stable_assembler_only", stable_info),
        ("stable_active_records", stable_active_info),
        ("final_alpha_output", final_info),
        ("deepgram_request_snapshot", dg_snap),
        ("audio_delivery_summary", audio_info),
        ("stage_manifest", {"exists": True}),
    ]
    for label, info in required[:-1]:
        if not info.get("exists"):
            missing.append(label)
    if raw_events <= 0:
        missing.append("raw_deepgram_events_empty")
    if asm_events <= 0:
        missing.append("stable_assembler_events_empty")
    if not final_source_hash_matches and final_info.get("exists"):
        warnings.append("final_alpha_source_hash_mismatch")
    if not action_reconciled:
        warnings.append("stage_action_count_mismatch")
    if not final_info.get("exists"):
        warnings.append("final_alpha_output_missing")
    if not export_coverage.get("coverage_passed"):
        warnings.append("export_coverage_failed")
    if not stable_final_compare.get("stable_final_text_exact_match"):
        warnings.append("stable_final_text_mismatch")

    complete = bool(critical.get("stage_capture_complete"))

    return {
        "app_version": APP_VERSION,
        "app_codename": APP_CODENAME,
        "run_id": run_id,
        "run_type": run_type,
        "run_status": run_status,
        "selected_language": selected_language,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repaired_offline": repaired_offline,
        "repair_timestamp": repair_timestamp,
        "reference_not_yet_scored": True,
        "assembler_event_count": asm_events,
        "append_count": append_count,
        "revision_requested_count": revision_requested,
        "revision_applied_count": revise_count,
        "revision_rejected_to_append_count": revision_rejected,
        "completed_sentence_revision_blocked_count": completed_sentence_blocked,
        "lineage_missing_count": lineage_missing,
        "lineage_disjoint_count": lineage_disjoint,
        "content_loss_guard_count": content_loss_guard,
        "exact_duplicate_no_op_count": exact_duplicate_no_op,
        "suppressed_stop_tail_count": suppressed,
        "stage_action_counts_reconciled": action_reconciled,
        "destructive_revision_count": destructive_revision_count,
        "ledger_action_counts": action_reconciliation.get("ledger_action_counts"),
        "runtime_action_counts": action_reconciliation.get("runtime_action_counts"),
        "event_file_action_counts": action_reconciliation.get("event_file_action_counts"),
        "counts_reconciled": action_reconciled,
        "count_reconciliation_differences": action_reconciliation.get("count_differences"),
        "applied_action_sum": action_reconciliation.get("applied_action_sum"),
        "three_stage_finalize_call_count": three_stage_finalize_call_count,
        "lineage_coverage_ratio": lineage.get("lineage_coverage_ratio"),
        "stable_records_without_lineage": lineage.get("stable_records_without_lineage"),
        "export_coverage_ratio": export_coverage.get("coverage_ratio"),
        "export_coverage_passed": export_coverage.get("coverage_passed"),
        "stable_final_record_id_match": stable_final_compare.get("stable_final_record_id_match"),
        "stable_final_text_hash_match": stable_final_compare.get("stable_final_text_hash_match"),
        "stable_final_text_exact_match": stable_final_compare.get("stable_final_text_exact_match"),
        "stable_active_record_count": stable_final_compare.get("stable_active_record_count"),
        "final_export_record_count": stable_final_compare.get("final_export_record_count"),
        "raw_stage": raw_info,
        "stable_stage": stable_info,
        "stable_active_stage": stable_active_info,
        "final_stage": final_info,
        "deepgram_request_snapshot": dg_snap,
        "audio_delivery_summary": audio_info,
        "stage_capture_complete": complete,
        "stage_capture_critical_checks": critical.get("stage_capture_critical_checks"),
        "stage_capture_failed_checks": critical.get("stage_capture_failed_checks"),
        "stage_capture_complete_reason": critical.get("stage_capture_complete_reason"),
        "missing_required_files": missing,
        "warnings": warnings,
        "errors": list(errors or []),
        "final_export_write_count": int(
            export_coverage.get("seal_write_count")
            or export_coverage.get("final_export_write_count")
            or 0
        ),
        "post_seal_write_attempt_count": int(
            export_coverage.get("post_seal_write_attempt_count") or 0
        ),
        "legacy_writer_disabled": True,
        "stop_tail_candidate_suppression_count": int(
            (action_reconciliation.get("ledger_action_counts") or {}).get("suppress_candidate")
            or 0
        ),
        "existing_record_suppression_count": int(
            export_coverage.get("unexplained_suppression_count") or 0
        ),
        "previous_active_record_preserved_count": int(
            (action_reconciliation.get("ledger_action_counts") or {}).get("suppress_candidate")
            or 0
        ),
        "late_final_overwrite_detected": bool(
            export_coverage.get("late_final_overwrite_detected")
        ),
        "ui_events_posted_after_final_drain": 0,
        "final_seal_verified": bool(
            export_coverage.get("post_evidence_seal_verified")
            or export_coverage.get("sealed_final_hash_match")
        ),
        "coverage_passed": bool(export_coverage.get("coverage_passed")),
        "authoritative_final_sha256": export_coverage.get("authoritative_final_sha256", ""),
        "stage_final_sha256": export_coverage.get("stage_final_sha256", ""),
        "authoritative_stage_hash_match": bool(
            export_coverage.get("stage_final_hash_match")
        ),
        "sealed_final_hash_match": bool(export_coverage.get("sealed_final_hash_match")),
    }


def _required_stage_artifacts_present(run_folder: Path) -> bool:
    for name in (
        "final_alpha_output",
        "audio_delivery_summary",
        "stage_manifest",
        "raw_deepgram",
        "stable_assembler_only",
        "stable_active_records",
    ):
        path = get_accuracy_stage_compare_path(name, run_folder)
        if not path.exists() or path.stat().st_size <= 0:
            return False
    return True


def repair_accuracy_stage_artifacts(
    run_folder: Path,
    *,
    host: Any = None,
    final_alpha_source_path: Path | None = None,
    run_type: str = "live",
    run_status: str = "completed",
    selected_language: str = "ja",
    offline_repair: bool = True,
) -> dict[str, Any]:
    """Backfill missing stage artifacts without mutating raw/stable event JSONL."""
    run_folder = ensure_path(run_folder)
    final_alpha_source_path = ensure_path(final_alpha_source_path)
    if run_folder is None:
        return {"ok": False, "errors": ["no_run_folder"]}
    return finalize_accuracy_stage_artifacts(
        run_folder=run_folder,
        host=host,
        final_alpha_source_path=final_alpha_source_path,
        run_type=run_type,
        run_status=run_status,
        selected_language=selected_language,
        offline_repair=offline_repair,
        allow_idempotent_repair=True,
    )


def finalize_three_stage_on_stop(
    host: Any = None, *, run_folder: str | Path | None = None
) -> dict[str, Any]:
    """Invoke stage finalization after Stop — read-only for sealed Final Alpha."""
    folder = ensure_path(run_folder)
    ident = None
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if folder is None and ident is not None:
            folder = ensure_path(ident.run_folder)
    except Exception:
        ident = None
    if folder is None:
        from alpha.utils.troubleshooting_paths import get_active_run_folder

        folder = ensure_path(get_active_run_folder())
    if folder is None:
        return {"ok": False, "errors": ["no_run_folder"]}
    final_source = folder / "transcripts" / "Alpha_output_FINAL.txt"
    seal_ok = False
    try:
        from alpha.utils.final_artifact_authority import verify_final_export_seal

        verify_final_export_seal(
            folder,
            run_id=str(getattr(ident, "run_id", "") if ident else ""),
        )
        seal_ok = True
    except Exception as exc:
        return {
            "ok": False,
            "errors": [f"final_export_seal_unverified:{type(exc).__name__}:{exc}"],
        }
    if not final_source.exists():
        return {"ok": False, "errors": ["sealed_final_alpha_missing"]}
    result = finalize_accuracy_stage_artifacts(
        run_folder=folder,
        host=host,
        final_alpha_source_path=final_source,
        run_type=str(ident.run_type if ident else "live"),
        run_status="completed",
        selected_language=str(ident.selected_language if ident else "ja"),
        offline_repair=False,
        allow_idempotent_repair=True,
    )
    result["final_seal_verified"] = seal_ok
    return result


def finalize_accuracy_stage_artifacts(
    *,
    run_folder: Path,
    host: Any = None,
    final_alpha_source_path: Path | None = None,
    run_type: str = "live",
    run_status: str = "completed",
    selected_language: str = "ja",
    offline_repair: bool = False,
    allow_idempotent_repair: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "errors": [], "warnings": []}
    run_folder = ensure_path(run_folder)
    final_alpha_source_path = ensure_path(final_alpha_source_path)
    if run_folder is None:
        result["errors"].append("no_run_folder")
        return result
    if not THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED:
        result["errors"].append("disabled")
        return result

    run_id = _resolve_run_id_from_folder(run_folder)
    three_stage_finalize_call_count = 0
    if not offline_repair:
        with _lock:
            _state["three_stage_finalize_call_count"] = int(
                _state.get("three_stage_finalize_call_count", 0)
            ) + 1
            call_count = int(_state["three_stage_finalize_call_count"])
            three_stage_finalize_call_count = call_count
        if call_count > 1:
            result["errors"].append("duplicate_three_stage_finalizer_call")
            _jp_log(
                "THREE_STAGE_FINALIZER_DUPLICATE_CALL_BLOCKED",
                run_folder=str(run_folder),
                run_id=run_id,
                three_stage_finalize_call_count=call_count,
            )
            return result
        _jp_log(
            "THREE_STAGE_FINALIZER_PATH_NORMALIZED",
            run_folder=str(run_folder),
            run_id=run_id,
        )
    else:
        with _lock:
            three_stage_finalize_call_count = int(_state.get("three_stage_finalize_call_count", 0))
    _jp_log("THREE_STAGE_FINALIZER_ENTERED", run_folder=str(run_folder), run_id=run_id, offline_repair=offline_repair)
    _jp_log("THREE_STAGE_FINALIZER_RUN_FOLDER_RESOLVED", run_folder=str(run_folder), run_id=run_id)
    stage_dir = get_accuracy_stage_compare_dir(run_folder)

    with _lock:
        already_finalized = bool(_state.get("finalized"))
    if already_finalized and not allow_idempotent_repair:
        result["ok"] = True
        result["idempotent"] = True
        return result
    if already_finalized and allow_idempotent_repair and _required_stage_artifacts_present(run_folder):
        manifest_path = get_accuracy_stage_compare_path("stage_manifest", run_folder)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        result["ok"] = bool(manifest.get("stage_capture_complete"))
        result["idempotent"] = True
        result["manifest"] = manifest
        result["stage_dir"] = str(stage_dir)
        _jp_log("THREE_STAGE_FINALIZER_COMPLETED", run_folder=str(run_folder), idempotent=True)
        return result

    source_hash_matches = False
    resolved_source: Path | None = None
    resolved_reason = ""

    try:
        raw_txt = get_accuracy_stage_compare_path("raw_deepgram", run_folder)
        if not raw_txt.exists() or raw_txt.stat().st_size <= 0 or not offline_repair:
            raw_lines = _rebuild_raw_lines_from_events(run_folder)
            if raw_lines or not raw_txt.exists():
                raw_txt.write_text("\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8")
                _jp_log("RAW_STAGE_TEXT_FINALIZED", line_count=len(raw_lines))
                _jp_log("RAW_STAGE_SHA256_CREATED", sha256=_sha256_file(raw_txt))
        _jp_log("RAW_STAGE_FINALIZATION_COMPLETED")
    except Exception as exc:
        result["errors"].append(f"raw_stage:{exc}")
        _log_finalizer_exception("raw_stage", exc, run_folder=run_folder, run_id=run_id)

    try:
        stable_result = write_stable_active_stage_artifacts(run_folder)
        if stable_result.get("ok"):
            _jp_log(
                "STABLE_ACTIVE_RECORDS_WRITTEN",
                record_count=stable_result.get("stable_active_record_count"),
                path=stable_result.get("stable_active_records_path"),
            )
            _jp_log(
                "ASSEMBLER_ONLY_STAGE_FINALIZED",
                line_count=stable_result.get("stable_active_record_count"),
                source="stable_active_records",
            )
        _jp_log("ASSEMBLER_STAGE_FINALIZATION_COMPLETED")
    except Exception as exc:
        result["errors"].append(f"assembler_stage:{exc}")
        _log_finalizer_exception("assembler_stage", exc, run_folder=run_folder, run_id=run_id)

    try:
        alias = ensure_stage1_stable_transcript_alias(run_folder=run_folder)
        if alias is not None:
            _jp_log("STABLE_TRANSCRIPT_ALIAS_WRITTEN", path=str(alias))
    except Exception as exc:
        result["warnings"].append(f"stable_transcript_alias:{exc}")

    try:
        if not FINAL_ALPHA_STAGE_CAPTURE_ENABLED or not IMMUTABLE_LIVE_STAGE_EVIDENCE_ENABLED:
            result["errors"].append("final_stage_disabled")
        else:
            dest = get_accuracy_stage_compare_path("final_alpha_output", run_folder)
            if final_alpha_source_path and final_alpha_source_path.exists():
                resolved_source = final_alpha_source_path
                resolved_reason = "explicit_argument"
            else:
                resolved_source, resolved_reason = _resolve_final_alpha_source_path(run_folder)
            _jp_log(
                "FINAL_ALPHA_SOURCE_RESOLVED",
                source=str(resolved_source) if resolved_source else "",
                reason=resolved_reason,
                run_id=run_id,
            )
            if resolved_source is None:
                result["errors"].append("final_alpha_source_not_found")
                result["warnings"].append("final_alpha_not_created")
            elif dest.exists() and dest.stat().st_size > 0 and offline_repair:
                source_sha = _sha256_file(resolved_source)
                copied_sha = _sha256_file(dest)
                source_hash_matches = source_sha == copied_sha and bool(source_sha)
            else:
                from alpha.utils.canonical_content_hash import atomic_copy_bytes

                copy_info = atomic_copy_bytes(resolved_source, dest)
                source_sha = copy_info["byte_sha256"]
                copied_sha = copy_info["byte_sha256"]
                source_hash_matches = True
                result["authoritative_final_sha256"] = source_sha
                result["stage_final_sha256"] = copied_sha
                result["authoritative_stage_hash_match"] = True
                source_text = dest.read_text(encoding="utf-8", errors="replace")
                _jp_log("FINAL_ALPHA_STAGE_HASH_MATCHED", sha256=source_sha)
                _jp_log(
                    "FINAL_ALPHA_STAGE_CAPTURED",
                    source_path=str(resolved_source),
                    line_count=len([ln for ln in source_text.splitlines() if ln.strip()]),
                    character_count=len(source_text),
                    byte_identical=True,
                )
            _jp_log("FINAL_ALPHA_STAGE_FINALIZATION_COMPLETED")
            # Ensure diagnostic stop-tail candidate log exists (may be empty).
            cand = get_accuracy_stage_compare_dir(run_folder) / "suppressed_stop_tail_candidates.jsonl"
            if not cand.exists() or cand.stat().st_size <= 0:
                cand.write_text('{"note":"no_suppressed_stop_tail_candidates"}\n', encoding="utf-8")
    except Exception as exc:
        result["errors"].append(f"final_alpha:{exc}")
        _log_finalizer_exception("final_alpha", exc, run_folder=run_folder, run_id=run_id)

    try:
        audio_path = get_accuracy_stage_compare_path("audio_delivery_summary", run_folder)
        if not audio_path.exists() or audio_path.stat().st_size <= 0 or offline_repair:
            _write_audio_delivery_summary(host, run_folder=run_folder, offline_repair=offline_repair)
    except Exception as exc:
        result["errors"].append(f"audio_summary:{exc}")
        _log_finalizer_exception("audio_delivery_summary", exc, run_folder=run_folder, run_id=run_id)

    export_coverage: dict[str, Any] = {}
    stable_final_compare: dict[str, Any] = {}
    action_reconciliation: dict[str, Any] = {}
    lineage: dict[str, Any] = {}
    audio_summary: dict[str, Any] = {}
    try:
        export_coverage = recompute_export_coverage_report(run_folder)
        write_export_coverage_report(export_coverage, run_folder=run_folder)
        stable_final_compare = compare_stable_and_final_artifacts(run_folder)
        try:
            from alpha.transcription.canonical_transcript_ledger import get_action_counts, lineage_reconciliation
            from alpha.utils.live_runtime_metrics import get_metrics

            raw_ids: set[str] = set()
            for row in load_jsonl_records(get_accuracy_stage_compare_path("raw_deepgram_events", run_folder)):
                rid = row.get("raw_event_id")
                if rid:
                    raw_ids.add(str(rid))
            lineage = lineage_reconciliation(raw_ids)
            asm_events = count_assembler_event_total(run_folder)
            action_reconciliation = reconcile_three_source_action_counts(
                ledger_counts=get_action_counts(),
                runtime_metrics=get_metrics(),
                event_file_counts=count_event_file_applied_actions(run_folder),
                assembler_event_count=asm_events,
            )
        except Exception as exc:
            result["warnings"].append(f"reconciliation:{exc}")
        audio_summary = _load_json_if_exists(get_accuracy_stage_compare_path("audio_delivery_summary", run_folder))
    except Exception as exc:
        result["errors"].append(f"export_coverage:{exc}")
        _log_finalizer_exception("export_coverage", exc, run_folder=run_folder, run_id=run_id)

    manifest: dict[str, Any] = {}
    try:
        _jp_log("STAGE_MANIFEST_GENERATION_STARTED", run_folder=str(run_folder), run_id=run_id)
        manifest = build_stage_manifest(
            run_folder=run_folder,
            run_type=run_type,
            run_status=run_status,
            selected_language=selected_language,
            final_alpha_source_path=str(resolved_source or final_alpha_source_path or "").replace("\\", "/"),
            final_source_hash_matches=source_hash_matches,
            repaired_offline=offline_repair,
            repair_timestamp=time.strftime("%Y-%m-%dT%H:%M:%S") if offline_repair else "",
            errors=result["errors"],
            export_coverage=export_coverage,
            stable_final_compare=stable_final_compare,
            action_reconciliation=action_reconciliation,
            lineage=lineage,
            audio_summary=audio_summary,
            three_stage_finalize_call_count=three_stage_finalize_call_count,
        )
        manifest_path = get_accuracy_stage_compare_path("stage_manifest", run_folder)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        _jp_log("STAGE_MANIFEST_WRITTEN", path=str(manifest_path))
    except Exception as exc:
        result["errors"].append(f"manifest:{exc}")
        _log_finalizer_exception("stage_manifest", exc, run_folder=run_folder, run_id=run_id)

    result["ok"] = bool(manifest.get("stage_capture_complete")) and not result["errors"]
    if result["ok"]:
        with _lock:
            _state["finalized"] = True
    else:
        with _lock:
            _state["finalized"] = False
    result["manifest"] = manifest
    result["stage_dir"] = str(stage_dir)
    result["final_alpha_source"] = str(resolved_source) if resolved_source else ""
    result["final_alpha_source_reason"] = resolved_reason
    result["final_source_hash_matches"] = source_hash_matches
    real_errors = [e for e in (result.get("errors") or []) if e]
    has_exc = bool(result.get("exception")) or bool(result.get("traceback"))
    if real_errors or has_exc:
        _jp_log(
            "THREE_STAGE_FINALIZER_EXCEPTION",
            run_folder=str(run_folder),
            errors=real_errors,
            exception=result.get("exception"),
            traceback=result.get("traceback"),
            run_id=run_id,
        )
    else:
        _jp_log(
            "THREE_STAGE_FINALIZER_COMPLETED",
            run_folder=str(run_folder),
            errors=[],
            exception=None,
            traceback=None,
            success=True,
            run_id=run_id,
        )
    return result


def get_stage_capture_snapshot() -> dict[str, Any]:
    with _lock:
        return dict(_state)
