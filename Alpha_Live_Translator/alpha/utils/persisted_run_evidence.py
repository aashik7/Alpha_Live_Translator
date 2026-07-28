"""Persisted completed-run evidence loader (V25.3.3.2).

Loads evidence ONLY from disk. Must never import or query:
- global canonical ledger instance
- runtime audio-counter singletons
- live UI / TranscriptStore / assembler process state
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from alpha.utils.canonical_content_hash import (
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text_utf8,
    canonicalize_record,
    canonical_record_list_sha256,
    normalize_text_content,
)
from alpha.utils.path_types import ensure_path


class PersistedEvidenceReconstructionError(RuntimeError):
    """Raised when reconstructed derived evidence would truncate valid Stable output."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _normalize_action(action: str) -> str:
    act = str(action or "").strip().lower()
    if act in ("revise", "revise_previous"):
        return "revise"
    if act == "no_op":
        return "no_op"
    if act == "suppress_candidate":
        return "suppress_candidate"
    if act in ("suppress", "suppressed_stop_tail"):
        return "suppress"
    if act == "append":
        return "append"
    return act or "append"


def _extract_lineage(row: dict[str, Any]) -> list[str]:
    top = row.get("source_raw_event_ids")
    if isinstance(top, list) and top:
        return [str(x) for x in top if str(x).strip()]
    meta = row.get("assembler_metadata")
    if isinstance(meta, dict):
        nested = meta.get("source_raw_event_ids")
        if isinstance(nested, list) and nested:
            return [str(x) for x in nested if str(x).strip()]
        raw_id = meta.get("raw_event_id")
        if raw_id:
            return [str(raw_id)]
    return []


def load_run_identity(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    manifest = _read_json(folder / "RUN_MANIFEST.json")
    live = _read_json(folder / "artifacts" / "LIVE_RUN_STATUS.json")
    return {
        "run_folder": str(folder),
        "run_id": str(manifest.get("run_id") or live.get("run_id") or ""),
        "app_version": str(manifest.get("app_version") or live.get("app_version") or ""),
        "run_type": str(manifest.get("run_type") or live.get("run_type") or ""),
        "run_timestamp": str(manifest.get("run_timestamp") or ""),
        "final_status": str(manifest.get("final_status") or manifest.get("status") or live.get("status") or ""),
        "completed_at": str(manifest.get("completed_at") or live.get("completed_at") or ""),
    }


def load_persisted_final_records(run_folder: Path | str) -> list[dict[str, Any]]:
    folder = ensure_path(run_folder)
    assert folder is not None
    path = folder / "transcripts" / "final_export_records.jsonl"
    rows = _read_jsonl(path)
    # Never reconstruct Final from UI/text — only this sidecar.
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        text = str(row.get("text") or row.get("final_text") or "").strip()
        out.append(
            {
                "record_id": str(row.get("record_id") or ""),
                "sequence_number": int(row.get("sequence_number") or row.get("sequence") or i),
                "speaker": int(row.get("speaker") or 2),
                "text": text,
                "source_raw_event_ids": list(row.get("source_raw_event_ids") or []),
                "content_sha256": str(row.get("content_sha256") or ""),
                "snapshot_id": str(row.get("snapshot_id") or ""),
                "run_id": str(row.get("run_id") or ""),
            }
        )
    return out


def load_persisted_assembler_events(run_folder: Path | str) -> list[dict[str, Any]]:
    folder = ensure_path(run_folder)
    assert folder is not None
    path = folder / "accuracy_stage_compare" / "stable_assembler_events.jsonl"
    rows = _read_jsonl(path)
    rows.sort(
        key=lambda r: (
            float(r.get("timestamp") or 0.0),
            str(r.get("stable_stage_event_id") or ""),
        )
    )
    return rows


def load_persisted_stable_commits(run_folder: Path | str) -> list[dict[str, Any]]:
    folder = ensure_path(run_folder)
    assert folder is not None
    path = folder / "transcripts" / "stable_commits.jsonl"
    rows = [r for r in _read_jsonl(path) if str(r.get("stable_text") or "").strip()]
    rows.sort(key=lambda r: float(r.get("timestamp") or 0.0))
    return rows


def load_persisted_action_counts(run_folder: Path | str) -> dict[str, Any]:
    events = load_persisted_assembler_events(run_folder)
    commits = load_persisted_stable_commits(run_folder)
    event_counts = {"append": 0, "revise": 0, "no_op": 0, "suppress_candidate": 0, "suppress": 0}
    for ev in events:
        act = _normalize_action(ev.get("applied_action") or ev.get("action"))
        if act in event_counts:
            event_counts[act] += 1
    commit_counts = {"append": 0, "revise": 0, "no_op": 0, "suppress_candidate": 0, "suppress": 0}
    for commit in commits:
        meta = commit.get("assembler_metadata") if isinstance(commit.get("assembler_metadata"), dict) else {}
        act = _normalize_action(meta.get("applied_action") or commit.get("applied_action"))
        if act in commit_counts:
            commit_counts[act] += 1
    differences = {
        k: int(event_counts.get(k, 0)) - int(commit_counts.get(k, 0))
        for k in sorted(set(event_counts) | set(commit_counts))
    }
    reconciled = all(v == 0 for v in differences.values())
    return {
        "persisted_event_action_counts": event_counts,
        "persisted_commit_action_counts": commit_counts,
        "counts_reconciled": reconciled,
        "count_differences": differences,
    }


def load_persisted_audio_summary(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    return _read_json(folder / "accuracy_stage_compare" / "audio_delivery_summary.json")


def load_persisted_stop_state(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    live = _read_json(folder / "artifacts" / "LIVE_RUN_STATUS.json")
    manifest = _read_json(folder / "RUN_MANIFEST.json")
    post = _read_json(folder / "artifacts" / "POST_RUN_EXIT_SUMMARY.json")
    status = str(
        manifest.get("status")
        or manifest.get("final_status")
        or live.get("status")
        or ""
    )
    return {
        "status": status,
        "final_status": str(manifest.get("final_status") or status),
        "stop_finalize_completed": bool(
            manifest.get("stop_finalize_completed", live.get("stop_finalize_completed", False))
        ),
        "stop_finalize_failed": bool(
            manifest.get("stop_finalize_failed", live.get("stop_finalize_failed", False))
        ),
        "completed_at": str(manifest.get("completed_at") or live.get("completed_at") or ""),
        "live_run_status": live,
        "run_manifest": {
            k: manifest.get(k)
            for k in (
                "run_id",
                "app_version",
                "run_type",
                "final_status",
                "status",
                "completed_at",
                "stop_finalize_completed",
                "stop_finalize_failed",
            )
        },
        "post_run_exit_summary": post,
    }


def load_persisted_stall_summary(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    return _read_json(folder / "health" / "STALL_CLASSIFICATION_SUMMARY.json")


def load_persisted_speaker_distribution(run_folder: Path | str) -> dict[str, int]:
    records = load_persisted_final_records(run_folder)
    dist: dict[str, int] = {}
    for row in records:
        key = f"Speaker {int(row.get('speaker') or 2)}"
        dist[key] = int(dist.get(key, 0)) + 1
    return dist


def load_persisted_health_evidence(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    health_timeline = folder / "health" / "PROCESS_HEALTH_TIMELINE.jsonl"
    memory = folder / "health" / "MEMORY_TREND_SUMMARY.json"
    stall = folder / "health" / "STALL_CLASSIFICATION_SUMMARY.json"
    return {
        "process_health_timeline_written": health_timeline.exists() and health_timeline.stat().st_size > 0,
        "memory_trend_summary_written": memory.exists() and memory.stat().st_size > 0,
        "stall_classification_summary_written": stall.exists() and stall.stat().st_size > 0,
        "process_health_timeline_path": str(health_timeline),
        "memory_trend_summary_path": str(memory),
        "stall_classification_summary_path": str(stall),
    }


def reconstruct_active_stable_records(run_folder: Path | str) -> dict[str, Any]:
    """Apply persisted assembler events; cross-check IDs/lineage via stable_commits."""
    folder = ensure_path(run_folder)
    assert folder is not None
    events = load_persisted_assembler_events(folder)
    commits = load_persisted_stable_commits(folder)
    if events and not events:
        pass

    active: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    unresolved: list[str] = []
    append_count = revise_count = no_op_count = suppress_candidate_count = 0

    # Pair by chronological index when lengths match; else map by ascending order.
    commit_by_index = commits

    for i, ev in enumerate(events):
        act = _normalize_action(ev.get("applied_action") or ev.get("action"))
        text = str(ev.get("assembler_text") or "").strip()
        lineage = _extract_lineage(ev)
        speaker = int(ev.get("speaker") or 2)
        commit = commit_by_index[i] if i < len(commit_by_index) else {}
        meta = commit.get("assembler_metadata") if isinstance(commit.get("assembler_metadata"), dict) else {}
        commit_rid = str(meta.get("revision_target_id") or meta.get("canonical_record_id") or "")
        if not lineage:
            lineage = _extract_lineage(commit)

        if act == "no_op":
            no_op_count += 1
            continue
        if act == "suppress_candidate":
            suppress_candidate_count += 1
            continue
        if act == "suppress":
            # Explicit record suppress is not used for incomplete tails in V25.3.3.1+
            # Do not deactivate via stop-tail candidate paths.
            continue
        if act == "revise":
            revise_count += 1
            target = str(
                (ev.get("revision_decision") or {}).get("target_line_id")
                or commit_rid
                or ""
            )
            if not target or target not in active:
                unresolved.append(target or f"event_index_{i}")
                continue
            active[target]["text"] = text
            prev = list(active[target].get("source_raw_event_ids") or [])
            # Merge lineage like the ledger revise path (ordered unique).
            merged: list[str] = []
            seen: set[str] = set()
            for item in prev + lineage:
                sid = str(item or "").strip()
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                merged.append(sid)
            active[target]["source_raw_event_ids"] = merged
            continue

        # append
        append_count += 1
        rid = commit_rid
        if not rid:
            reason = str(ev.get("reason") or "")
            commit_reason = str(ev.get("commit_reason") or "")
            # English Stable evidence has no Japanese ledger commit IDs.
            if reason == "english_accepted_final" or commit_reason.startswith("english"):
                rid = f"en-stable-{i:06d}"
            else:
                unresolved.append(f"missing_record_id_event_index_{i}")
                continue
        if rid not in active:
            order.append(rid)
        active[rid] = {
            "record_id": rid,
            "sequence_number": 0,
            "speaker": speaker,
            "text": text,
            "source_raw_event_ids": lineage,
        }

    if events and append_count + revise_count > 0 and not order:
        raise PersistedEvidenceReconstructionError(
            "Non-empty persisted assembler events produced empty Stable reconstruction"
        )

    rows: list[dict[str, Any]] = []
    for seq, rid in enumerate(order, start=1):
        rec = dict(active[rid])
        rec["sequence_number"] = seq
        rows.append(rec)

    records_without_lineage = [
        r["record_id"] for r in rows if not list(r.get("source_raw_event_ids") or [])
    ]
    reconstruction_sha256 = canonical_record_list_sha256(rows)

    report = {
        "source_assembler_event_count": len(events),
        "source_stable_commit_count": len(commits),
        "append_count": append_count,
        "revise_count": revise_count,
        "no_op_count": no_op_count,
        "suppress_candidate_count": suppress_candidate_count,
        "active_record_count": len(rows),
        "records_without_lineage": len(records_without_lineage),
        "records_without_lineage_ids": records_without_lineage,
        "unresolved_revision_targets": unresolved,
        "reconstruction_completed": not unresolved,
        "reconstruction_sha256": reconstruction_sha256,
        "active_records": rows,
    }
    if unresolved:
        report["reconstruction_completed"] = False
    return report


def write_reconstructed_stable_artifacts(run_folder: Path | str) -> dict[str, Any]:
    """Atomically write derived Stable artifacts; never truncate valid evidence."""
    folder = ensure_path(run_folder)
    assert folder is not None
    compare = folder / "accuracy_stage_compare"
    compare.mkdir(parents=True, exist_ok=True)
    jsonl_path = compare / "stable_active_records.jsonl"
    txt_path = compare / "stable_assembler_only.txt"
    report_path = compare / "PERSISTED_STABLE_RECONSTRUCTION_REPORT.json"

    events = load_persisted_assembler_events(folder)
    result = reconstruct_active_stable_records(folder)
    rows = list(result.get("active_records") or [])

    if events and result.get("append_count", 0) + result.get("revise_count", 0) > 0:
        if not rows:
            raise PersistedEvidenceReconstructionError(
                "Refusing to write empty Stable artifacts over non-empty persisted events"
            )

    if result.get("unresolved_revision_targets"):
        raise PersistedEvidenceReconstructionError(
            f"Unresolved revision targets: {result['unresolved_revision_targets']}"
        )

    # Build complete payloads in memory first
    report = {k: v for k, v in result.items() if k != "active_records"}
    report["stable_active_records_path"] = str(jsonl_path)
    report["stable_assembler_only_path"] = str(txt_path)

    # Validate temp content before replace
    atomic_write_jsonl(jsonl_path, rows)
    asm_lines = [str(r.get("text") or "").strip() for r in rows if str(r.get("text") or "").strip()]
    atomic_write_text_utf8(txt_path, "\n".join(asm_lines) + ("\n" if asm_lines else ""))
    atomic_write_json(report_path, report)

    # Read-back hash verify
    written = _read_jsonl(jsonl_path)
    if len(written) != len(rows):
        raise PersistedEvidenceReconstructionError("stable_active_records read-back count mismatch")
    read_hash = canonical_record_list_sha256(written)
    if read_hash != result["reconstruction_sha256"]:
        raise PersistedEvidenceReconstructionError("stable_active_records reconstruction hash mismatch")

    report["write_ok"] = True
    report["read_back_sha256"] = read_hash
    atomic_write_json(report_path, report)
    return report


def compute_persisted_coverage(run_folder: Path | str) -> dict[str, Any]:
    """Coverage from reconstructed Stable + Final sidecar + sealed Final text."""
    from alpha.utils.canonical_content_hash import (
        byte_sha256_file,
        canonicalize_record,
        compare_normalized_text_files,
        normalized_file_sha256,
        normalized_text_sha256,
    )

    folder = ensure_path(run_folder)
    assert folder is not None
    stable_path = folder / "accuracy_stage_compare" / "stable_active_records.jsonl"
    if not stable_path.exists() or stable_path.stat().st_size == 0:
        write_reconstructed_stable_artifacts(folder)
    stable_rows = _read_jsonl(stable_path)
    final_rows = load_persisted_final_records(folder)
    alpha_path = folder / "transcripts" / "Alpha_output_FINAL.txt"
    stage_path = folder / "accuracy_stage_compare" / "final_alpha_output.txt"

    stable_ids = [str(r.get("record_id") or "") for r in stable_rows]
    final_ids = [str(r.get("record_id") or "") for r in final_rows]
    stable_map = {str(r.get("record_id")): r for r in stable_rows}
    final_map = {str(r.get("record_id")): r for r in final_rows}

    missing_final = [rid for rid in stable_ids if rid not in final_map]
    extra_final = [rid for rid in final_ids if rid not in stable_map]
    order_match = stable_ids == final_ids

    matched = 0
    speaker_match = True
    text_match = True
    lineage_match = True
    hash_match = True
    for rid in stable_ids:
        if rid not in final_map:
            continue
        s = canonicalize_record(stable_map[rid])
        f = canonicalize_record(final_map[rid])
        if s["speaker"] != f["speaker"]:
            speaker_match = False
        if s["text"] != f["text"]:
            text_match = False
        if s["source_raw_event_ids"] != f["source_raw_event_ids"]:
            lineage_match = False
        if s != f:
            hash_match = False
            continue
        matched += 1

    # Whole transcript normalized hashes
    stable_text = "\n".join(str(r.get("text") or "") for r in stable_rows)
    final_text = "\n".join(str(r.get("text") or "") for r in final_rows)
    stable_transcript_norm = normalized_text_sha256(stable_text)
    final_transcript_norm = normalized_text_sha256(final_text)

    auth_byte = byte_sha256_file(alpha_path) if alpha_path.exists() else ""
    stage_byte = byte_sha256_file(stage_path) if stage_path.exists() else ""
    auth_norm = normalized_file_sha256(alpha_path) if alpha_path.exists() else ""
    stage_norm = normalized_file_sha256(stage_path) if stage_path.exists() else ""

    denom = len(stable_ids) or len(final_ids)
    ratio = (matched / denom) if denom else 0.0
    coverage_passed = (
        denom > 0
        and matched == denom
        and not missing_final
        and not extra_final
        and order_match
        and speaker_match
        and text_match
        and lineage_match
        and hash_match
        and stable_transcript_norm == final_transcript_norm
        and bool(auth_byte)
        and auth_byte == stage_byte
        and auth_norm == stage_norm
    )

    return {
        "comparison_version": "v25.3.3.2_canonical_normalized",
        "canonical_active_record_count": len(stable_ids),
        "stable_active_record_count": len(stable_ids),
        "final_record_count": len(final_ids),
        "matched_record_count": matched,
        "missing_final_record_ids": missing_final,
        "extra_final_record_ids": extra_final,
        "record_id_order_match": order_match,
        "speaker_match": speaker_match,
        "normalized_text_match": text_match,
        "lineage_match": lineage_match,
        "canonical_record_hash_match": hash_match,
        "authoritative_stage_byte_hash_match": bool(auth_byte) and auth_byte == stage_byte,
        "authoritative_stage_normalized_hash_match": bool(auth_norm) and auth_norm == stage_norm,
        "authoritative_final_byte_sha256": auth_byte,
        "stage_final_byte_sha256": stage_byte,
        "authoritative_final_normalized_sha256": auth_norm,
        "stage_final_normalized_sha256": stage_norm,
        "stable_transcript_normalized_sha256": stable_transcript_norm,
        "final_transcript_normalized_sha256": final_transcript_norm,
        "coverage_ratio": ratio,
        "coverage_passed": coverage_passed,
    }


def write_export_coverage_from_persisted(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    report = compute_persisted_coverage(folder)
    path = folder / "accuracy_stage_compare" / "export_coverage_report.json"
    atomic_write_json(path, report)
    report["path"] = str(path)
    return report


def copy_stage_final_byte_identical(run_folder: Path | str) -> dict[str, Any]:
    from alpha.utils.canonical_content_hash import atomic_copy_bytes

    folder = ensure_path(run_folder)
    assert folder is not None
    src = folder / "transcripts" / "Alpha_output_FINAL.txt"
    dest = folder / "accuracy_stage_compare" / "final_alpha_output.txt"
    if not src.exists():
        raise FileNotFoundError(str(src))
    hashes = atomic_copy_bytes(src, dest)
    return {
        "ok": True,
        "source": str(src),
        "dest": str(dest),
        **hashes,
        "authoritative_final_byte_sha256": hashes["byte_sha256"],
        "stage_final_byte_sha256": hashes["byte_sha256"],
        "authoritative_final_normalized_sha256": hashes["normalized_content_sha256"],
        "stage_final_normalized_sha256": hashes["normalized_content_sha256"],
    }


def supersede_partial_index(run_folder: Path | str) -> dict[str, Any]:
    from datetime import datetime, timezone

    folder = ensure_path(run_folder)
    assert folder is not None
    final_index = folder / "artifacts" / "RUN_ARTIFACTS_INDEX.txt"
    # Runtime writes partial at run root; older paths used artifacts/.
    candidates = [
        folder / "artifacts" / "RUN_ARTIFACTS_INDEX.partial.txt",
        folder / "RUN_ARTIFACTS_INDEX.partial.txt",
    ]
    partial = next((p for p in candidates if p.exists()), None)
    if partial is None:
        return {"ok": True, "skipped": True, "reason": "no_partial"}
    if not final_index.exists():
        return {"ok": False, "reason": "final_index_missing"}
    # Do not re-supersede an already superseded partial.
    head = partial.read_text(encoding="utf-8", errors="replace")[:200]
    if "status=superseded" in head:
        return {"ok": True, "skipped": True, "reason": "already_superseded", "path": str(partial)}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = (
        f"status=superseded\n"
        f"superseded_by={final_index.as_posix()}\n"
        f"superseded_at={ts}\n"
    )
    # Preserve prior content as comments for historical evidence
    prior = partial.read_text(encoding="utf-8", errors="replace")
    text = body + "\n# prior_partial_content_begin\n" + prior
    if not prior.endswith("\n"):
        text += "\n"
    text += "# prior_partial_content_end\n"
    atomic_write_text_utf8(partial, text)
    return {"ok": True, "path": str(partial), "superseded_at": ts, "superseded_by": str(final_index)}
