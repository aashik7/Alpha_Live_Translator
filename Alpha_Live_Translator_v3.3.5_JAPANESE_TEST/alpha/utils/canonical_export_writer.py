"""Unified canonical export payload writer (8.5.25.2.1)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from alpha.constants import CANONICAL_OUTPUT_ARTIFACT_UNIFICATION_ENABLED

_payload_cache: dict[str, Any] | None = None


class LegacyAuthoritativeWriterDisabled(RuntimeError):
    """Live Stop path must not rewrite authoritative Final Alpha via legacy writer."""


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def normalize_transcript_for_hash(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines)


def transcript_sha256(text: str) -> str:
    normalized = normalize_transcript_for_hash(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def record_content_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def atomic_write_text_with_hash_verification(
    target: Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Write text atomically: temp → fsync → replace → readback hash verify."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_hash = record_content_sha256(text)
    temp_path = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    result: dict[str, Any] = {
        "ok": False,
        "path": str(target),
        "expected_sha256": expected_hash,
    }
    try:
        with open(temp_path, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        readback = target.read_text(encoding=encoding)
        readback_hash = record_content_sha256(readback)
        if readback_hash != expected_hash:
            result["readback_sha256"] = readback_hash
            result["reason"] = "readback_hash_mismatch"
            _jp_log(
                "FINAL_ALPHA_ATOMIC_READBACK_HASH_MISMATCH",
                path=str(target),
                expected=expected_hash,
                readback=readback_hash,
            )
            return result
        result["ok"] = True
        result["readback_sha256"] = readback_hash
        _jp_log(
            "FINAL_ALPHA_ATOMIC_WRITE_COMPLETED",
            path=str(target),
            sha256=readback_hash,
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        _jp_log(
            "FINAL_ALPHA_ATOMIC_WRITE_FAILED",
            path=str(target),
            error=str(exc),
        )
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
    return result


def build_final_export_record_rows(
    snap: dict[str, Any], *, run_id: str = ""
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seq, rec in enumerate(snap.get("records") or [], start=1):
        if rec.get("suppressed"):
            continue
        text = str(rec.get("final_text") or rec.get("assembler_text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "record_id": str(rec.get("record_id") or ""),
                "sequence_number": int(rec.get("sequence_number") or seq),
                "speaker": int(rec.get("speaker") or 2),
                "text": text,
                "content_sha256": record_content_sha256(text),
                "source_raw_event_ids": list(
                    rec.get("source_raw_event_ids") or rec.get("raw_event_ids") or []
                ),
                "snapshot_id": str(snap.get("snapshot_id") or ""),
                "run_id": str(run_id or snap.get("run_id") or ""),
            }
        )
    return rows


def write_final_export_records_jsonl(
    rows: list[dict[str, Any]],
    *,
    target: Path,
) -> dict[str, Any]:
    """Write final_export_records.jsonl atomically from frozen snapshot rows."""
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    body = "\n".join(lines)
    if body:
        body += "\n"
    atomic = atomic_write_text_with_hash_verification(target, body)
    atomic["record_count"] = len(rows)
    if atomic.get("ok"):
        _jp_log(
            "FINAL_EXPORT_RECORDS_JSONL_WRITTEN",
            path=str(target),
            record_count=len(rows),
        )
    return atomic


def set_canonical_export_payload(
    lines: list[str],
    *,
    canonical_records: list[dict[str, Any]] | None = None,
    coverage_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _payload_cache
    body = "\n".join(ln.strip() for ln in lines if ln and ln.strip())
    if body and not body.endswith("\n"):
        body += "\n"
    sha = transcript_sha256(body)
    _payload_cache = {
        "canonical_final_export_lines": lines,
        "text": body,
        "canonical_export_payload_sha256": sha,
        "authoritative_alpha_output_sha256": sha,
        "canonical_records": canonical_records or [],
        "coverage_report": coverage_report or {},
        "created_at": time.time(),
    }
    _jp_log("CANONICAL_EXPORT_PAYLOAD_CREATED", sha256=sha, lines=len(lines))
    return _payload_cache


def get_canonical_export_payload() -> dict[str, Any] | None:
    return _payload_cache


def clear_canonical_export_payload() -> None:
    global _payload_cache
    _payload_cache = None


def _move_partial_to_debug_history(run_folder: Path | None) -> list[str]:
    moved: list[str] = []
    if not run_folder:
        return moved
    try:
        from alpha.utils.troubleshooting_paths import get_transcript_path

        partial = get_transcript_path("alpha_output_partial")
        if partial.exists() and partial.stat().st_size > 0:
            debug_dir = run_folder / "debug_history"
            debug_dir.mkdir(parents=True, exist_ok=True)
            dest = debug_dir / "Alpha_output_PARTIAL_precanonical.txt"
            shutil.copy2(partial, dest)
            partial.write_text("", encoding="utf-8")
            moved.append(str(dest))
            _jp_log("PRECANONICAL_PARTIAL_MOVED_TO_DEBUG_HISTORY", path=str(dest))
    except Exception:
        pass
    return moved


def write_authoritative_outputs_from_payload(
    *,
    run_folder: Path | None = None,
    run_id: str = "",
    run_timestamp: str = "",
    accuracy_header_fn: Any = None,
    audio_ref: str = "",
    allow_offline_repair: bool = False,
) -> dict[str, Any]:
    """Legacy multi-writer path — disabled for live Stop (V25.3.3.1)."""
    del run_timestamp, accuracy_header_fn, audio_ref
    if allow_offline_repair and not CANONICAL_OUTPUT_ARTIFACT_UNIFICATION_ENABLED:
        return {"ok": False, "reason": "unification_disabled"}
    if not allow_offline_repair:
        if run_folder is not None:
            try:
                from alpha.utils.final_artifact_authority import record_post_seal_write_attempt

                record_post_seal_write_attempt(
                    run_folder,
                    function_name="write_authoritative_outputs_from_payload",
                    reason="legacy_writer_blocked",
                )
            except Exception:
                pass
        _jp_log("LEGACY_AUTHORITATIVE_WRITER_DISABLED", run_id=run_id)
        raise LegacyAuthoritativeWriterDisabled(
            "write_authoritative_outputs_from_payload is disabled for live runs; "
            "use final_artifact_authority.write_final_once / "
            "sync_non_authoritative_aliases_from_sealed_final"
        )
    return {"ok": False, "reason": "legacy_writer_offline_only_and_disabled"}


def sync_non_authoritative_aliases_from_sealed_final(
    run_folder: Path | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Delegate alias sync to sealed Final Alpha authority (read sealed file only)."""
    from alpha.utils.final_artifact_authority import (
        sync_non_authoritative_aliases_from_sealed_final as _sync,
    )

    if run_folder is None:
        return {"ok": False, "reason": "no_run_folder"}
    return _sync(run_folder, run_id=run_id)
