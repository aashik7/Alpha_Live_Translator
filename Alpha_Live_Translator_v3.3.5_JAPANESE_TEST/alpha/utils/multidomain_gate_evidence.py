"""Multidomain gate evidence helpers (85262).

Benchmark-only instrumentation and post-run analysis helpers.
Must not load reference/truth during live recognition.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ACCURACY_PROFILE_DOMAIN_AGNOSTIC = "domain_agnostic_no_hints"
ACCURACY_PROFILE_TEST01_MEETING = "target_85_meeting_context"
MULTIDOMAIN_VERSION = "3.3.5.5.8.5.26.5.1"
FROZEN_INFRASTRUCTURE = "3.3.5.5.8.5.25.3.3.2.8"
NORMALIZATION_RULES_VERSION = "mdg_general_norm_v265"
AUDIO_EVENT_SCHEMA_VERSION = 2
FORBIDDEN_STALE_VERSION = "3.3.5.5.8.5.26.2"

# Benchmark-only parent→child binding handshake (inactive unless env vars set).
BINDING_ENV_ID = "ALPHA_MULTIDOMAIN_BINDING_ID"
BINDING_ENV_PARENT_GATE_RUN_ID = "ALPHA_MULTIDOMAIN_PARENT_GATE_RUN_ID"
BINDING_RECORD_NAME = "BENCHMARK_CHILD_BINDING.json"
BINDING_SCHEMA_VERSION = 1

_lock = threading.Lock()
_next_delivery_chunk_id = 1
_pending_ids: deque[int] = deque()
_evidence_overflow_count = 0
_active = False
_events_path: Optional[Path] = None
_event_q: queue.SimpleQueue[Optional[dict[str, Any]]] = queue.SimpleQueue()
_writer_thread: Optional[threading.Thread] = None
_writer_started = False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_multidomain_benchmark_mode() -> bool:
    return _truthy("ALPHA_MULTIDOMAIN_BENCHMARK_MODE")


def get_active_japanese_accuracy_profile() -> str:
    env = os.environ.get("JAPANESE_ACCURACY_PROFILE", "").strip()
    if env:
        return env
    try:
        from alpha.constants import JAPANESE_ACCURACY_PROFILE

        return str(JAPANESE_ACCURACY_PROFILE or "").strip()
    except Exception:
        return ""


def is_domain_agnostic_no_hints_active() -> bool:
    if not is_multidomain_benchmark_mode():
        return False
    return get_active_japanese_accuracy_profile() == ACCURACY_PROFILE_DOMAIN_AGNOSTIC


def test01_profile_status() -> dict[str, Any]:
    return {
        "profile_name": ACCURACY_PROFILE_TEST01_MEETING,
        "status": "experimental_only",
        "production_enabled": False,
        "multidomain_benchmark_allowed": False,
    }


def _resolve_events_path(run_folder: Path | None = None) -> Path | None:
    try:
        if run_folder is None:
            from alpha.utils.troubleshooting_paths import get_active_run_folder

            run_folder = get_active_run_folder()
        if run_folder is None:
            return None
        stage = Path(run_folder) / "accuracy_stage_compare"
        stage.mkdir(parents=True, exist_ok=True)
        return stage / "audio_delivery_events.jsonl"
    except Exception:
        return None


def _writer_loop() -> None:
    global _evidence_overflow_count
    path = _events_path
    while True:
        try:
            item = _event_q.get(timeout=0.5)
        except Exception:
            continue
        if item is None:
            return
        if item.get("__shutdown__"):
            return
        try:
            target = Path(item.get("_path") or path or "")
            if not target:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: v for k, v in item.items() if k != "_path"}
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception:
            with _lock:
                _evidence_overflow_count += 1


def _ensure_writer(path: Path) -> None:
    global _writer_thread, _writer_started, _events_path
    with _lock:
        _events_path = path
        if _writer_started:
            return
        _writer_started = True
        _writer_thread = threading.Thread(
            target=_writer_loop, name="MultidomainAudioEvidenceWriter", daemon=True
        )
        _writer_thread.start()


def _post_event(payload: dict[str, Any], *, run_folder: Path | None = None) -> None:
    global _evidence_overflow_count
    if not _active and not is_multidomain_benchmark_mode():
        return
    path = _resolve_events_path(run_folder)
    if path is None:
        return
    try:
        _ensure_writer(path)
        row = dict(payload)
        row["_path"] = str(path)
        row.setdefault("monotonic_ns", time.monotonic_ns())
        _event_q.put_nowait(row)
    except Exception:
        with _lock:
            _evidence_overflow_count += 1


def activate_benchmark_evidence(*, run_id: str = "") -> None:
    global _active, _next_delivery_chunk_id, _pending_ids
    with _lock:
        _active = True
        _next_delivery_chunk_id = 1
        _pending_ids = deque()
    _post_event(
        {
            "event": "benchmark_mode_started",
            "run_id": run_id,
            "profile": ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
        }
    )
    # Benchmark-only handshake: write binding record when parent env is present.
    try:
        write_child_binding_record(run_id=run_id)
    except Exception:
        pass


def deactivate_benchmark_evidence(*, run_id: str = "") -> None:
    global _active
    _post_event({"event": "benchmark_mode_stopped", "run_id": run_id})
    with _lock:
        _active = False


def retained_per_chunk_metadata_count() -> int:
    """After V26.4.1, no unbounded per-chunk in-memory metadata is retained."""
    return 0


def module_level_collection_sizes() -> dict[str, int]:
    """Inspect module-level collections for memory-regression assertions."""
    with _lock:
        return {
            "_pending_ids": len(_pending_ids),
            "_queued_meta": 0,
            "_evidence_overflow_count": int(_evidence_overflow_count),
        }


def write_child_binding_record(
    *,
    run_folder: Path | None = None,
    run_id: str = "",
) -> dict[str, Any] | None:
    """Atomically write BENCHMARK_CHILD_BINDING.json when handshake env is set.

    Inactive during ordinary non-benchmark runs (missing env → no-op).
    """
    binding_id = os.environ.get(BINDING_ENV_ID, "").strip()
    parent_gate_run_id = os.environ.get(BINDING_ENV_PARENT_GATE_RUN_ID, "").strip()
    if not binding_id or not parent_gate_run_id:
        return None
    if not is_multidomain_benchmark_mode() and not _active:
        return None
    try:
        if run_folder is None:
            from alpha.utils.troubleshooting_paths import get_active_run_folder

            run_folder = get_active_run_folder()
    except Exception:
        run_folder = None
    if run_folder is None:
        return None
    run_folder = Path(run_folder)
    run_folder.mkdir(parents=True, exist_ok=True)
    child_run_id = run_id or _current_run_id() or run_folder.name
    payload = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "binding_id": binding_id,
        "parent_gate_run_id": parent_gate_run_id,
        "child_run_id": child_run_id,
        "child_run_path": str(run_folder.resolve()),
        "app_version": MULTIDOMAIN_VERSION,
        "process_id": os.getpid(),
        "created_at_utc": utc_now_iso(),
    }
    atomic_write_json(run_folder / BINDING_RECORD_NAME, payload)
    return payload


def load_child_binding_record(run_folder: Path) -> dict[str, Any] | None:
    path = Path(run_folder) / BINDING_RECORD_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def find_bindings_for_parent(
    project_root: Path,
    parent_gate_run_id: str,
    *,
    gate_run_folder: Path | None = None,
) -> list[tuple[Path, dict[str, Any]]]:
    """Return all (child_folder, binding) pairs claiming the given parent gate run."""
    parent_gate_run_id = str(parent_gate_run_id or "").strip()
    if not parent_gate_run_id:
        return []
    runs_root = Path(project_root) / "troubleshooting" / "runs"
    if not runs_root.exists():
        return []
    gate_resolved: Path | None = None
    if gate_run_folder is not None:
        try:
            gate_resolved = Path(gate_run_folder).resolve()
        except Exception:
            gate_resolved = None
    matches: list[tuple[Path, dict[str, Any]]] = []
    for folder in runs_root.iterdir():
        if not folder.is_dir():
            continue
        try:
            if gate_resolved is not None and folder.resolve() == gate_resolved:
                continue
        except Exception:
            pass
        record = load_child_binding_record(folder)
        if not record:
            continue
        if str(record.get("parent_gate_run_id") or "").strip() != parent_gate_run_id:
            continue
        matches.append((folder, record))
    return matches


def resolve_parent_child_binding(
    project_root: Path,
    *,
    gate_run_folder: Path,
    parent_gate_run_id: str = "",
) -> dict[str, Any]:
    """Deterministically resolve the bound child for a parent gate run.

    Fail-closed outcomes (status != OK):
      BINDING_ABSENT, BINDING_WRONG, BINDING_AMBIGUOUS
    """
    gate_run_folder = Path(gate_run_folder)
    parent_id = str(parent_gate_run_id or gate_run_folder.name or "").strip()
    result: dict[str, Any] = {
        "status": "BINDING_ABSENT",
        "parent_gate_run_id": parent_id,
        "child_run_id": "",
        "child_run_folder": "",
        "binding": None,
        "detail": "",
    }
    if not parent_id:
        result["detail"] = "parent_gate_run_id_missing"
        return result

    matches = find_bindings_for_parent(
        project_root, parent_id, gate_run_folder=gate_run_folder
    )
    lineage_child = ""
    lineage_path = gate_run_folder / "accuracy_stage_compare" / "TRANSCRIPT_STAGE_LINEAGE.json"
    if lineage_path.exists():
        try:
            lineage_doc = json.loads(lineage_path.read_text(encoding="utf-8"))
            lineage_child = str(lineage_doc.get("child_run_folder") or "").strip()
        except Exception:
            lineage_child = ""

    if not matches:
        result["status"] = "BINDING_ABSENT"
        result["detail"] = "no_binding_record_for_parent"
        return result
    if len(matches) > 1:
        result["status"] = "BINDING_AMBIGUOUS"
        result["detail"] = ",".join(str(m[0]) for m in matches)
        return result

    child_folder, binding = matches[0]
    child_run_id = str(binding.get("child_run_id") or "").strip()
    binding_parent = str(binding.get("parent_gate_run_id") or "").strip()
    if not child_run_id:
        result["status"] = "BINDING_WRONG"
        result["detail"] = "child_run_id_missing"
        result["binding"] = binding
        result["child_run_folder"] = str(child_folder)
        return result
    if binding_parent != parent_id:
        result["status"] = "BINDING_WRONG"
        result["detail"] = "parent_gate_run_id_mismatch"
        result["binding"] = binding
        result["child_run_folder"] = str(child_folder)
        result["child_run_id"] = child_run_id
        return result
    if lineage_child:
        try:
            if Path(lineage_child).resolve() != child_folder.resolve():
                result["status"] = "BINDING_AMBIGUOUS"
                result["detail"] = f"lineage_child_conflict:{lineage_child}"
                result["binding"] = binding
                result["child_run_folder"] = str(child_folder)
                result["child_run_id"] = child_run_id
                return result
        except Exception:
            result["status"] = "BINDING_WRONG"
            result["detail"] = "lineage_child_unresolvable"
            result["binding"] = binding
            result["child_run_folder"] = str(child_folder)
            result["child_run_id"] = child_run_id
            return result

    result.update(
        {
            "status": "OK",
            "child_run_id": child_run_id,
            "child_run_folder": str(child_folder.resolve()),
            "binding": binding,
            "detail": "",
        }
    )
    return result


def parse_evidence_timestamp(value: Any) -> datetime | None:
    """Parse ISO-8601 / Z timestamps as timezone-aware UTC.

    Naive values are treated as UTC. Never returns an offset-naive datetime.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _aware_utc_from_mtime(path: Path) -> datetime | None:
    """Physical file mtime as timezone-aware UTC (fail-closed on error)."""
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
    except Exception:
        return None


def derive_lineage_timing_flags(
    *,
    evidence_captured_at: str,
    runtime_child_exited_at: str,
    runtime_finalized_at: str = "",
    runtime_finalized_source_path: str = "",
    stage_manifest_path: str | Path = "",
) -> dict[str, Any]:
    """Derive captured_after_* flags from physical timestamps (fail-closed).

    Stage-manifest finalization time must come from the physical file mtime
    (UTC-aware), never from its timezone-less created_at string.
    """
    captured_ts = parse_evidence_timestamp(evidence_captured_at)
    exit_ts = parse_evidence_timestamp(runtime_child_exited_at)

    finalized_ts: datetime | None = None
    finalized_at_out = runtime_finalized_at
    finalized_source_out = runtime_finalized_source_path
    used_stage_manifest_mtime = False
    stage_manifest_completed = False
    stage_manifest_present = False

    manifest_raw = str(stage_manifest_path or "").strip()
    if manifest_raw:
        manifest_path = Path(manifest_raw)
        stage_manifest_present = manifest_path.exists()
        if stage_manifest_present:
            try:
                doc = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                doc = None
            stage_manifest_completed = bool(
                isinstance(doc, dict) and doc.get("completed") is True
            )
            if stage_manifest_completed:
                finalized_ts = _aware_utc_from_mtime(manifest_path)
                if finalized_ts is not None:
                    used_stage_manifest_mtime = True
                    finalized_source_out = str(manifest_path.resolve())
                    finalized_at_out = (
                        finalized_ts.isoformat().replace("+00:00", "Z")
                    )
            # completed=false or unreadable → leave finalized_ts None (fail closed)
    elif runtime_finalized_at:
        # Non-manifest sources only; still normalize to aware UTC.
        finalized_ts = parse_evidence_timestamp(runtime_finalized_at)

    after_exit = False
    after_finalization = False
    try:
        after_exit = bool(captured_ts and exit_ts and captured_ts >= exit_ts)
    except TypeError:
        after_exit = False
    try:
        if captured_ts and finalized_ts:
            # Stage-manifest mtime is sub-second precise; utc_now_iso() truncates
            # to whole seconds. Compare at second resolution when the finalization
            # source is the stage manifest so same-second write→lineage is not a
            # false not_after_finalization failure.
            if used_stage_manifest_mtime:
                after_finalization = (
                    captured_ts.replace(microsecond=0)
                    >= finalized_ts.replace(microsecond=0)
                )
            else:
                after_finalization = captured_ts >= finalized_ts
        else:
            after_finalization = False
    except TypeError:
        after_finalization = False

    return {
        "captured_after_runtime_exit": after_exit,
        "captured_after_runtime_finalization": after_finalization,
        "evidence_captured_at": evidence_captured_at,
        "runtime_child_exited_at": runtime_child_exited_at,
        "runtime_finalized_at": finalized_at_out,
        "runtime_finalized_source_path": finalized_source_out,
        "exit_timestamp_present": exit_ts is not None,
        "finalization_timestamp_present": finalized_ts is not None,
        "evidence_timestamp_present": captured_ts is not None,
        "lineage_timing_verified": after_exit and after_finalization,
        "used_stage_manifest_mtime": used_stage_manifest_mtime,
        "stage_manifest_present": stage_manifest_present,
        "stage_manifest_completed": stage_manifest_completed,
        "compared_datetimes_utc_aware": all(
            ts is None or (ts.tzinfo is not None and ts.utcoffset() is not None)
            for ts in (captured_ts, exit_ts, finalized_ts)
        ),
    }


def record_lifecycle_event(event: str, **extra: Any) -> None:
    payload = {"event": event, **extra}
    try:
        from alpha.utils.run_identity import get_run_id

        payload.setdefault("run_id", str(get_run_id() or ""))
    except Exception:
        payload.setdefault("run_id", "")
    _post_event(payload)


def _current_run_id() -> str:
    try:
        from alpha.utils.run_identity import get_run_id

        return str(get_run_id() or "")
    except Exception:
        return ""


def note_normalized_chunk_queued(
    chunk: Any,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> int:
    """Assign delivery_chunk_id immediately before queue put. Does not copy/alter bytes."""
    global _next_delivery_chunk_id
    if not (_active or is_multidomain_benchmark_mode()):
        return 0
    try:
        if isinstance(chunk, (bytes, bytearray, memoryview)):
            byte_count = len(chunk)
        else:
            byte_count = int(getattr(chunk, "nbytes", 0) or len(chunk))
    except Exception:
        byte_count = 0
    frame_count = byte_count // max(2 * max(channels, 1), 1)
    with _lock:
        chunk_id = _next_delivery_chunk_id
        _next_delivery_chunk_id += 1
        _pending_ids.append(chunk_id)
    run_id = _current_run_id()
    _post_event(
        {
            "schema_version": AUDIO_EVENT_SCHEMA_VERSION,
            "event": "normalized_chunk_queued",
            "event_type": "queued",
            "delivery_chunk_id": chunk_id,
            "sequence_index": chunk_id,
            "run_id": run_id,
            "app_version": MULTIDOMAIN_VERSION,
            "frame_count": frame_count,
            "byte_count": byte_count,
            "sample_rate": sample_rate,
            "channels": channels,
            "sample_width_bytes": 2,
            "queued_at_utc": utc_now_iso(),
            "send_status": "pending",
            "hook_source_file": "alpha/ui/main_window.py",
            "hook_source_function": "audio_mixer_worker",
        }
    )
    return chunk_id


def note_queue_drop_discard_pending() -> None:
    """Keep pending IDs aligned when oldest queue item is discarded."""
    if not (_active or is_multidomain_benchmark_mode()):
        return
    with _lock:
        if _pending_ids:
            _pending_ids.popleft()


def take_pending_delivery_id() -> Optional[int]:
    with _lock:
        if not _pending_ids:
            return None
        return _pending_ids.popleft()


def note_normalized_chunk_sent(
    delivery_chunk_id: Optional[int],
    *,
    frame_count: int,
    byte_count: int,
    sample_rate: int = 16000,
    channels: int = 1,
    send_result: str = "success",
) -> None:
    if delivery_chunk_id is None:
        return
    run_id = _current_run_id()
    _post_event(
        {
            "schema_version": AUDIO_EVENT_SCHEMA_VERSION,
            "event": "normalized_chunk_sent",
            "event_type": "sent",
            "delivery_chunk_id": int(delivery_chunk_id),
            "sequence_index": int(delivery_chunk_id),
            "run_id": run_id,
            "app_version": MULTIDOMAIN_VERSION,
            "frame_count": int(frame_count),
            "byte_count": int(byte_count),
            "sample_rate": int(sample_rate),
            "channels": int(channels),
            "sample_width_bytes": 2,
            "sent_at_utc": utc_now_iso(),
            "send_result": send_result,
            "send_status": send_result,
            "hook_source_file": "alpha/transcription/deepgram_client.py",
            "hook_source_function": "_normalize_and_send_pcm",
        }
    )


def note_normalized_chunk_send_failed(
    delivery_chunk_id: Optional[int],
    *,
    error_class: str,
    error_message_sanitized: str,
) -> None:
    run_id = _current_run_id()
    _post_event(
        {
            "schema_version": AUDIO_EVENT_SCHEMA_VERSION,
            "event": "normalized_chunk_send_failed",
            "event_type": "failed",
            "delivery_chunk_id": delivery_chunk_id,
            "sequence_index": int(delivery_chunk_id or 0),
            "run_id": run_id,
            "app_version": MULTIDOMAIN_VERSION,
            "sample_width_bytes": 2,
            "sent_at_utc": utc_now_iso(),
            "send_status": "failed",
            "error_class": error_class,
            "error_message_sanitized": str(error_message_sanitized)[:200],
            "hook_source_file": "alpha/transcription/deepgram_client.py",
            "hook_source_function": "_normalize_and_send_pcm",
        }
    )


def flush_evidence_events(*, timeout_s: float = 180.0) -> bool:
    """Wait until the evidence writer queue drains (best-effort)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            empty = _event_q.empty()
        except Exception:
            empty = True
        if empty:
            time.sleep(0.15)
            try:
                if _event_q.empty():
                    return True
            except Exception:
                return True
        else:
            time.sleep(0.05)
    return False


def get_evidence_queue_overflow_count() -> int:
    with _lock:
        return int(_evidence_overflow_count)


def recalculate_audio_delivery_summary(events_path: Path) -> dict[str, Any]:
    """Independently reopen JSONL and compute delivery statistics."""
    parse_errors = 0
    queued: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if not events_path.exists():
        return {
            "queued_chunk_count": 0,
            "sent_chunk_count": 0,
            "failed_chunk_count": 0,
            "unique_queued_chunk_count": 0,
            "unique_sent_chunk_count": 0,
            "duplicate_queued_chunk_ids": [],
            "duplicate_sent_chunk_ids": [],
            "missing_sent_chunk_ids": [],
            "unexpected_sent_chunk_ids": [],
            "queued_frame_count": 0,
            "sent_frame_count": 0,
            "queued_byte_count": 0,
            "sent_byte_count": 0,
            "queued_duration_seconds": 0.0,
            "sent_duration_seconds": 0.0,
            "delivery_ratio": 0.0,
            "sequence_gap_count": 0,
            "maximum_send_delay_ms": 0.0,
            "p50_send_delay_ms": 0.0,
            "p95_send_delay_ms": 0.0,
            "p99_send_delay_ms": 0.0,
            "send_gap_over_250ms_count": 0,
            "evidence_record_parse_errors": 0,
            "evidence_queue_overflow_count": 0,
            "events_path": str(events_path),
            "events_missing": True,
        }

    queued_times: dict[int, int] = {}
    sent_times: dict[int, int] = {}
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            parse_errors += 1
            continue
        event = str(row.get("event") or "")
        if event == "normalized_chunk_queued":
            queued.append(row)
            cid = int(row.get("delivery_chunk_id") or 0)
            queued_times[cid] = int(row.get("monotonic_ns") or 0)
        elif event == "normalized_chunk_sent":
            sent.append(row)
            cid = int(row.get("delivery_chunk_id") or 0)
            sent_times[cid] = int(row.get("monotonic_ns") or 0)
        elif event == "normalized_chunk_send_failed":
            failed.append(row)

    q_ids = [int(r.get("delivery_chunk_id") or 0) for r in queued]
    s_ids = [int(r.get("delivery_chunk_id") or 0) for r in sent]
    q_counts = Counter(q_ids)
    s_counts = Counter(s_ids)
    dup_q = sorted([i for i, c in q_counts.items() if c > 1 and i > 0])
    dup_s = sorted([i for i, c in s_counts.items() if c > 1 and i > 0])
    q_set = set(q_ids)
    s_set = set(s_ids)
    missing = sorted(q_set - s_set)
    unexpected = sorted(s_set - q_set)

    q_frames = sum(int(r.get("frame_count") or 0) for r in queued)
    s_frames = sum(int(r.get("frame_count") or 0) for r in sent)
    q_bytes = sum(int(r.get("byte_count") or 0) for r in queued)
    s_bytes = sum(int(r.get("byte_count") or 0) for r in sent)

    def _duration(frames: int, rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0
        sr = int(rows[0].get("sample_rate") or 16000) or 16000
        return float(frames) / float(sr)

    delays_ms: list[float] = []
    for cid, q_ns in queued_times.items():
        if cid in sent_times and q_ns and sent_times[cid]:
            delays_ms.append(max(0.0, (sent_times[cid] - q_ns) / 1_000_000.0))
    delays_ms.sort()

    def _pct(vals: list[float], p: float) -> float:
        if not vals:
            return 0.0
        idx = min(len(vals) - 1, max(0, int(round((p / 100.0) * (len(vals) - 1)))))
        return float(vals[idx])

    # Sequence gaps among unique queued IDs
    uniq_q = sorted(i for i in q_set if i > 0)
    gaps = 0
    for a, b in zip(uniq_q, uniq_q[1:]):
        if b != a + 1:
            gaps += 1

    sent_sorted = sorted(sent_times.items(), key=lambda kv: kv[1])
    send_gap_over = 0
    for (_a, t0), (_b, t1) in zip(sent_sorted, sent_sorted[1:]):
        if t0 and t1 and (t1 - t0) / 1_000_000.0 > 250.0:
            send_gap_over += 1

    q_count = len(queued)
    s_count = len(sent)
    ratio = (float(s_count) / float(q_count)) if q_count else 1.0

    return {
        "queued_chunk_count": q_count,
        "sent_chunk_count": s_count,
        "failed_chunk_count": len(failed),
        "unique_queued_chunk_count": len(q_set),
        "unique_sent_chunk_count": len(s_set),
        "duplicate_queued_chunk_ids": dup_q,
        "duplicate_sent_chunk_ids": dup_s,
        "missing_sent_chunk_ids": missing,
        "unexpected_sent_chunk_ids": unexpected,
        "queued_frame_count": q_frames,
        "sent_frame_count": s_frames,
        "queued_byte_count": q_bytes,
        "sent_byte_count": s_bytes,
        "queued_duration_seconds": _duration(q_frames, queued),
        "sent_duration_seconds": _duration(s_frames, sent),
        "delivery_ratio": ratio,
        "sequence_gap_count": gaps,
        "maximum_send_delay_ms": float(delays_ms[-1]) if delays_ms else 0.0,
        "p50_send_delay_ms": _pct(delays_ms, 50),
        "p95_send_delay_ms": _pct(delays_ms, 95),
        "p99_send_delay_ms": _pct(delays_ms, 99),
        "send_gap_over_250ms_count": send_gap_over,
        "evidence_record_parse_errors": parse_errors,
        "evidence_queue_overflow_count": get_evidence_queue_overflow_count(),
        "events_path": str(events_path),
        "events_missing": False,
    }


def normalize_transcript_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
        if m:
            line = m.group(1).strip()
        lines.append(line)
    return re.sub(r"\s+", "", "".join(lines))


# V26.5: benchmark-specific meaning pairs removed. General normalization only.
_MEANING_PAIRS: list[tuple[str, str]] = []


def apply_meaning_equivalent(text: str) -> tuple[str, list[dict[str, str]]]:
    """General meaning normalization for supplementary scoring only (analysis-time)."""
    from alpha.utils.general_meaning_normalization import apply_general_meaning_normalization

    return apply_general_meaning_normalization(text)


# =====================================================================
# v3.3.5.5.8.5.26.4 — canonical fail-closed evidence helpers
# =====================================================================

GATE_SCHEMA_VERSION = 1

REQUIRED_EVIDENCE_FILES = [
    "raw_deepgram.txt",
    "stable_transcript.txt",
    "final_alpha_output.txt",
    "audio_delivery_events.jsonl",
    "audio_delivery_summary.json",
    "deepgram_request_actual.json",
    "TRANSCRIPT_STAGE_LINEAGE.json",
    "STOP_EVIDENCE_RECONCILIATION.json",
    "stage_manifest.json",
    "reference_isolation_actual.json",
]

_SECRET_KEY_SUBSTRINGS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "auth_token",
    "secret",
    "password",
    "bearer",
)
_SECRET_KEY_ALLOWED = (
    "forbidden_secret_fields_present",
    "secret_scan",
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:token|bearer|authorization)\s*[=:]\s*[A-Za-z0-9_\-\.]{12,}"
)


def scan_payload_for_secrets(payload: Any) -> list[str]:
    """Scan keys and string values of a parsed JSON payload for secret material."""
    findings: list[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_l = str(key).lower()
                if any(sub in key_l for sub in _SECRET_KEY_SUBSTRINGS) and not any(
                    key_l.startswith(a) or key_l == a for a in _SECRET_KEY_ALLOWED
                ):
                    if isinstance(value, str) and value.strip():
                        findings.append(f"secret_bearing_key:{path}.{key}")
                _walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                _walk(value, f"{path}[{idx}]")
        elif isinstance(node, str):
            if _SECRET_VALUE_RE.search(node):
                findings.append(f"secret_token_pattern:{path}")

    _walk(payload, "$")
    return findings


def atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def snapshot_evidence_file(source: Path, dest: Path) -> dict[str, Any]:
    """Byte-preserving copy with atomic publication; hashes prove no mutation."""
    source = Path(source)
    dest = Path(dest)
    info: dict[str, Any] = {
        "source_path": str(source),
        "dest_path": str(dest),
        "copied": False,
        "source_sha256": "",
        "dest_sha256": "",
        "byte_size": 0,
        "content_modified_during_copy": None,
        "source_and_snapshot_hash_match": False,
    }
    if not source.exists():
        info["error"] = "source_missing"
        return info
    data = source.read_bytes()
    src_sha = hashlib.sha256(data).hexdigest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    dst_sha = sha256_file(dest)
    info.update(
        {
            "copied": True,
            "source_sha256": src_sha,
            "dest_sha256": dst_sha,
            "byte_size": len(data),
            "content_modified_during_copy": src_sha != dst_sha,
            "source_and_snapshot_hash_match": src_sha == dst_sha,
        }
    )
    return info


def build_audio_delivery_summary(
    events_path: Path,
    *,
    run_id: str = "",
    expected_run_id: str = "",
    parent_gate_run_id: str = "",
    child_run_id: str = "",
) -> dict[str, Any]:
    """Spec-F summary derived solely from the physical JSONL (fail-closed).

    Physical event run_id is validated against ``expected_run_id`` (the child
    live-app ID when a binding exists). Both parent and child IDs are recorded.
    """
    events_path = Path(events_path)
    base = recalculate_audio_delivery_summary(events_path)
    exists = events_path.exists()
    events_sha = sha256_file(events_path) if exists else ""
    events_size = events_path.stat().st_size if exists else 0

    parent_id = str(parent_gate_run_id or "").strip()
    child_id = str(child_run_id or "").strip()
    # Prefer explicit child for foreign-ID checks; fall back to expected_run_id.
    event_expected_id = child_id or str(expected_run_id or "").strip()

    foreign_run_ids: list[str] = []
    if exists and event_expected_id:
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            rid = str(row.get("run_id") or "")
            if rid and rid != event_expected_id and rid not in foreign_run_ids:
                foreign_run_ids.append(rid)

    verified = (
        exists
        and int(base.get("queued_chunk_count") or 0) > 0
        and int(base.get("sent_chunk_count") or 0) > 0
        and int(base.get("evidence_record_parse_errors") or 0) == 0
        and int(base.get("failed_chunk_count") or 0) == 0
        and not base.get("missing_sent_chunk_ids")
        and not base.get("duplicate_queued_chunk_ids")
        and not base.get("duplicate_sent_chunk_ids")
        and not base.get("unexpected_sent_chunk_ids")
        and float(base.get("delivery_ratio") or 0.0) >= 0.999
        and not foreign_run_ids
    )

    return {
        "run_id": run_id or parent_id or expected_run_id,
        "parent_gate_run_id": parent_id,
        "child_run_id": child_id or event_expected_id,
        "app_version": MULTIDOMAIN_VERSION,
        "harness_version": MULTIDOMAIN_VERSION,
        "events_path": str(events_path),
        "events_sha256": events_sha,
        "events_byte_size": events_size,
        "parse_error_count": int(base.get("evidence_record_parse_errors") or 0),
        "queued_chunk_count": int(base.get("queued_chunk_count") or 0),
        "sent_chunk_count": int(base.get("sent_chunk_count") or 0),
        "failed_chunk_count": int(base.get("failed_chunk_count") or 0),
        "unique_queued_chunk_count": int(base.get("unique_queued_chunk_count") or 0),
        "unique_sent_chunk_count": int(base.get("unique_sent_chunk_count") or 0),
        "queued_byte_count": int(base.get("queued_byte_count") or 0),
        "sent_byte_count": int(base.get("sent_byte_count") or 0),
        "queued_frame_count": int(base.get("queued_frame_count") or 0),
        "sent_frame_count": int(base.get("sent_frame_count") or 0),
        "missing_sent_chunk_ids": list(base.get("missing_sent_chunk_ids") or []),
        "duplicate_queued_chunk_ids": list(base.get("duplicate_queued_chunk_ids") or []),
        "duplicate_sent_chunk_ids": list(base.get("duplicate_sent_chunk_ids") or []),
        "unexpected_sent_chunk_ids": list(base.get("unexpected_sent_chunk_ids") or []),
        "sequence_gap_count": int(base.get("sequence_gap_count") or 0),
        "delivery_ratio": float(base.get("delivery_ratio") or 0.0),
        "foreign_run_ids": foreign_run_ids,
        "audio_delivery_verified": bool(verified),
        "derived_from_physical_jsonl": bool(exists),
        "events_missing": not exists,
    }


def _file_check(
    path: Path,
    run_folder: Path,
    *,
    accepted_run_ids: set[str],
    json_kind: str = "",
    must_be_nonempty: bool = False,
) -> dict[str, Any]:
    """Physically reopen one required evidence file and validate it."""
    failures: list[str] = []
    exists = path.exists() and path.is_file()
    readable = False
    byte_size = 0
    sha = ""
    parsed: Any = None
    run_id_match = True
    version_match = True
    current_run_path_match = False
    try:
        current_run_path_match = str(path.resolve()).lower().startswith(
            str(Path(run_folder).resolve()).lower()
        )
    except Exception:
        current_run_path_match = False

    if not exists:
        failures.append("missing")
    else:
        try:
            data = path.read_bytes()
            readable = True
            byte_size = len(data)
            sha = hashlib.sha256(data).hexdigest()
        except Exception:
            failures.append("unreadable")
        if readable and must_be_nonempty and byte_size == 0:
            failures.append("empty")
        if readable and json_kind:
            try:
                if json_kind == "jsonl":
                    rows = []
                    for line in data.decode("utf-8", errors="strict").splitlines():
                        if line.strip():
                            rows.append(json.loads(line))
                    parsed = rows
                    if not rows:
                        failures.append("jsonl_empty")
                else:
                    parsed = json.loads(data.decode("utf-8", errors="strict"))
            except Exception:
                failures.append("parse_error")
                parsed = None
        if parsed is not None and accepted_run_ids:
            candidates: list[Any] = parsed if isinstance(parsed, list) else [parsed]
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("run_id") or "")
                if rid and rid not in accepted_run_ids:
                    run_id_match = False
                    failures.append("run_id_mismatch")
                    break
        if parsed is not None:
            candidates = parsed if isinstance(parsed, list) else [parsed]
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                for field in ("app_version", "harness_version", "gate_version"):
                    ver = str(row.get(field) or "")
                    if ver and ver != MULTIDOMAIN_VERSION:
                        version_match = False
                        failures.append("stale_version")
                        break
                if not version_match:
                    break
    if not current_run_path_match:
        failures.append("outside_current_run_folder")

    return {
        "path": str(path),
        "exists": exists,
        "readable": readable,
        "byte_size": byte_size,
        "sha256": sha,
        "parsed": parsed is not None if json_kind else None,
        "run_id_match": run_id_match,
        "version_match": version_match,
        "current_run_path_match": current_run_path_match,
        "validation_failures": sorted(set(failures)),
        "_parsed_payload": parsed,
    }


def build_pre_score_evidence_gate(
    run_folder: Path,
    *,
    expected_run_id: str = "",
    accepted_run_ids: set[str] | None = None,
    harness_version: str = MULTIDOMAIN_VERSION,
    write: bool = True,
    project_root: Path | None = None,
    binding: dict[str, Any] | None = None,
    require_binding: bool = False,
) -> dict[str, Any]:
    """Canonical pre-score gate (spec H). The scorer must not bypass this.

    Parent benchmark ID and child live-app ID may differ. When a deterministic
    binding record is present (or required), stage_manifest / lineage / stop /
    summary validate against the parent ID; Deepgram request and physical audio
    events validate against the child ID.
    """
    run_folder = Path(run_folder)
    stage = run_folder / "accuracy_stage_compare"
    expected_run_id = expected_run_id or run_folder.name
    parent_id = expected_run_id
    child_id = ""
    binding_status = ""
    binding_detail = ""
    binding_payload: dict[str, Any] | None = None

    if project_root is not None:
        root = Path(project_root)
    else:
        root = run_folder
        try:
            resolved = run_folder.resolve()
            parts = resolved.parts
            for i, part in enumerate(parts):
                if (
                    part == "troubleshooting"
                    and i + 1 < len(parts)
                    and parts[i + 1] == "runs"
                ):
                    root = Path(*parts[:i]) if i > 0 else resolved
                    break
        except Exception:
            root = run_folder
    # Prefer an explicit binding; otherwise resolve from disk.
    if isinstance(binding, dict) and binding.get("status") == "OK":
        binding_payload = binding
        binding_status = "OK"
        child_id = str(binding.get("child_run_id") or "").strip()
        parent_from_binding = str(binding.get("parent_gate_run_id") or "").strip()
        if parent_from_binding and parent_from_binding != parent_id:
            binding_status = "BINDING_WRONG"
            binding_detail = "parent_gate_run_id_mismatch"
            child_id = ""
    else:
        try:
            resolved = resolve_parent_child_binding(
                root,
                gate_run_folder=run_folder,
                parent_gate_run_id=parent_id,
            )
        except Exception as exc:
            resolved = {
                "status": "BINDING_ABSENT",
                "detail": f"resolve_error:{exc}",
                "child_run_id": "",
                "parent_gate_run_id": parent_id,
                "binding": None,
            }
        binding_payload = resolved
        binding_status = str(resolved.get("status") or "BINDING_ABSENT")
        binding_detail = str(resolved.get("detail") or "")
        if binding_status == "OK":
            child_id = str(resolved.get("child_run_id") or "").strip()

    parent_run_ids: set[str] = set(accepted_run_ids or set())
    parent_run_ids.add(parent_id)
    child_run_ids: set[str] = set()
    if child_id:
        child_run_ids.add(child_id)
    elif not require_binding:
        # Unbound / synthetic fixtures: all artifacts share the parent ID.
        child_run_ids = set(parent_run_ids)

    # Artifacts authored by the parent gate harness.
    PARENT_ID_FILES = {
        "stage_manifest.json",
        "audio_delivery_summary.json",
        "TRANSCRIPT_STAGE_LINEAGE.json",
        "STOP_EVIDENCE_RECONCILIATION.json",
    }
    # Artifacts captured from the live child process.
    CHILD_ID_FILES = {
        "audio_delivery_events.jsonl",
        "deepgram_request_actual.json",
    }

    checks: dict[str, dict[str, Any]] = {}
    blocked_reasons: list[str] = []
    status = "EVIDENCE_VERIFIED"

    if require_binding or binding_status not in ("", "OK", "BINDING_ABSENT"):
        # Explicit requirement, or a wrong/ambiguous binding always fails closed.
        if binding_status == "BINDING_ABSENT" and require_binding:
            blocked_reasons.append("binding:absent")
        elif binding_status == "BINDING_WRONG":
            blocked_reasons.append(f"binding:wrong:{binding_detail or 'invalid'}")
        elif binding_status == "BINDING_AMBIGUOUS":
            blocked_reasons.append(f"binding:ambiguous:{binding_detail or 'multiple'}")
    elif child_id == "" and binding_status == "BINDING_ABSENT":
        # Detect child-tagged evidence without a binding → fail closed.
        probe_paths = [
            stage / "deepgram_request_actual.json",
            stage / "audio_delivery_events.jsonl",
        ]
        childish = False
        for probe in probe_paths:
            if not probe.exists():
                continue
            try:
                if probe.suffix == ".jsonl":
                    for line in probe.read_text(encoding="utf-8", errors="replace").splitlines():
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        rid = str(row.get("run_id") or "")
                        if rid and rid != parent_id:
                            childish = True
                            break
                else:
                    doc = json.loads(probe.read_text(encoding="utf-8"))
                    rid = str(doc.get("run_id") or "") if isinstance(doc, dict) else ""
                    if rid and rid != parent_id:
                        childish = True
            except Exception:
                continue
            if childish:
                break
        if childish:
            blocked_reasons.append("binding:absent")
            binding_status = "BINDING_ABSENT"

    spec: list[tuple[str, str, bool]] = [
        ("raw_deepgram.txt", "", True),
        ("stable_transcript.txt", "", True),
        ("final_alpha_output.txt", "", True),
        ("audio_delivery_events.jsonl", "jsonl", True),
        ("audio_delivery_summary.json", "json", True),
        ("deepgram_request_actual.json", "json", True),
        ("TRANSCRIPT_STAGE_LINEAGE.json", "json", True),
        ("STOP_EVIDENCE_RECONCILIATION.json", "json", True),
        ("stage_manifest.json", "json", True),
        ("reference_isolation_actual.json", "json", True),
    ]
    for name, kind, nonempty in spec:
        if name in CHILD_ID_FILES:
            ids_for_file = child_run_ids if child_run_ids else parent_run_ids
        elif name in PARENT_ID_FILES:
            ids_for_file = parent_run_ids
        else:
            ids_for_file = parent_run_ids | child_run_ids
        checks[name] = _file_check(
            stage / name,
            run_folder,
            accepted_run_ids=ids_for_file,
            json_kind=kind,
            must_be_nonempty=nonempty,
        )
        for failure in checks[name]["validation_failures"]:
            blocked_reasons.append(f"{name}:{failure}")

    # --- content-level validations on parsed payloads ---
    manifest = checks["stage_manifest.json"].get("_parsed_payload")
    stage_manifest_completed = bool(isinstance(manifest, dict) and manifest.get("completed") is True)
    if isinstance(manifest, dict):
        if manifest.get("completed") is not True:
            blocked_reasons.append("stage_manifest.json:completed_false")
        rid = str(manifest.get("run_id") or "")
        if rid and rid != parent_id:
            blocked_reasons.append("stage_manifest.json:run_id_mismatch")
    else:
        stage_manifest_completed = False

    summary = checks["audio_delivery_summary.json"].get("_parsed_payload")
    if isinstance(summary, dict):
        if summary.get("audio_delivery_verified") is not True:
            blocked_reasons.append("audio_delivery_summary.json:audio_delivery_verified_false")
        if summary.get("derived_from_physical_jsonl") is not True:
            blocked_reasons.append("audio_delivery_summary.json:not_derived_from_physical_jsonl")
        if int(summary.get("parse_error_count") or 0) != 0:
            blocked_reasons.append("audio_delivery_summary.json:parse_errors_nonzero")
        if child_id:
            if str(summary.get("parent_gate_run_id") or "") != parent_id:
                blocked_reasons.append("audio_delivery_summary.json:parent_gate_run_id_mismatch")
            if str(summary.get("child_run_id") or "") != child_id:
                blocked_reasons.append("audio_delivery_summary.json:child_run_id_mismatch")
            if summary.get("foreign_run_ids"):
                blocked_reasons.append("audio_delivery_summary.json:foreign_run_ids_present")

    events = checks["audio_delivery_events.jsonl"].get("_parsed_payload")
    if isinstance(events, list) and events:
        queued = [r for r in events if isinstance(r, dict) and r.get("event") == "normalized_chunk_queued"]
        sent = [r for r in events if isinstance(r, dict) and r.get("event") == "normalized_chunk_sent"]
        if not queued:
            blocked_reasons.append("audio_delivery_events.jsonl:no_queued_events")
        if not sent:
            blocked_reasons.append("audio_delivery_events.jsonl:no_sent_events")

    request = checks["deepgram_request_actual.json"].get("_parsed_payload")
    secret_findings: list[str] = []
    if isinstance(request, dict):
        secret_findings = scan_payload_for_secrets(request)
        if secret_findings:
            blocked_reasons.append("deepgram_request_actual.json:secret_exposed")
        if request.get("sanitized") is not True:
            blocked_reasons.append("deepgram_request_actual.json:not_sanitized")
        if request.get("forbidden_secret_fields_present") is True:
            blocked_reasons.append("deepgram_request_actual.json:forbidden_secret_fields_present")
        if str(request.get("language") or "") != "ja":
            blocked_reasons.append("deepgram_request_actual.json:language_not_ja")
        for field in ("keyterm_count", "keyword_count", "reference_terms_loaded"):
            if int(request.get(field) or 0) != 0:
                blocked_reasons.append(f"deepgram_request_actual.json:{field}_nonzero")
        if child_id:
            rid = str(request.get("run_id") or "")
            if rid and rid != child_id:
                blocked_reasons.append("deepgram_request_actual.json:run_id_mismatch")

    lineage = checks["TRANSCRIPT_STAGE_LINEAGE.json"].get("_parsed_payload")
    if isinstance(lineage, dict):
        for stage_name in ("raw", "stable", "final"):
            entry = lineage.get(stage_name)
            if not isinstance(entry, dict):
                blocked_reasons.append(f"TRANSCRIPT_STAGE_LINEAGE.json:{stage_name}_missing")
                continue
            if entry.get("content_modified_during_copy") is not False:
                blocked_reasons.append(
                    f"TRANSCRIPT_STAGE_LINEAGE.json:{stage_name}_content_modified_during_copy"
                )
            if entry.get("source_and_snapshot_hash_match") is not True:
                blocked_reasons.append(
                    f"TRANSCRIPT_STAGE_LINEAGE.json:{stage_name}_hash_mismatch"
                )
            if entry.get("captured_after_runtime_finalization") is not True:
                blocked_reasons.append(
                    f"TRANSCRIPT_STAGE_LINEAGE.json:{stage_name}_not_after_finalization"
                )
            if entry.get("captured_after_runtime_exit") is not True:
                blocked_reasons.append(
                    f"TRANSCRIPT_STAGE_LINEAGE.json:{stage_name}_not_after_exit"
                )
            snap_path = str(entry.get("evidence_snapshot_path") or "")
            snap_sha = str(entry.get("evidence_snapshot_sha256") or "")
            if snap_path and snap_sha:
                physical = Path(snap_path)
                if not physical.is_absolute():
                    physical = run_folder / snap_path
                actual_sha = sha256_file(physical) if physical.exists() else ""
                if actual_sha != snap_sha:
                    blocked_reasons.append(
                        f"TRANSCRIPT_STAGE_LINEAGE.json:{stage_name}_snapshot_hash_stale"
                    )

    stop = checks["STOP_EVIDENCE_RECONCILIATION.json"].get("_parsed_payload")
    if isinstance(stop, dict):
        if stop.get("stop_evidence_verified") is not True:
            blocked_reasons.append("STOP_EVIDENCE_RECONCILIATION.json:stop_evidence_not_verified")
        if stop.get("conflicts"):
            blocked_reasons.append("STOP_EVIDENCE_RECONCILIATION.json:conflicts_present")

    isolation = checks["reference_isolation_actual.json"].get("_parsed_payload")
    if isinstance(isolation, dict):
        if isolation.get("isolation_verified") is not True:
            blocked_reasons.append("reference_isolation_actual.json:isolation_not_verified")

    blocked_reasons = sorted(set(blocked_reasons))
    if any(b.startswith("binding:absent") for b in blocked_reasons):
        status = "BINDING_ABSENT"
    elif any(b.startswith("binding:wrong") for b in blocked_reasons):
        status = "BINDING_WRONG"
    elif any(b.startswith("binding:ambiguous") for b in blocked_reasons):
        status = "BINDING_AMBIGUOUS"
    elif any(":secret_exposed" in b or "forbidden_secret_fields_present" in b for b in blocked_reasons):
        status = "SECRET_EXPOSED"
    elif any(b.endswith(":run_id_mismatch") for b in blocked_reasons):
        status = "RUN_ID_MISMATCH"
    elif any(b.endswith(":stale_version") for b in blocked_reasons):
        status = "VERSION_MISMATCH"
    elif any(b.startswith("STOP_EVIDENCE_RECONCILIATION.json:conflicts") for b in blocked_reasons):
        status = "STOP_EVIDENCE_CONFLICT"
    elif blocked_reasons:
        status = "EVIDENCE_INCOMPLETE"

    passed = not blocked_reasons
    payload = {
        "schema_version": GATE_SCHEMA_VERSION,
        "run_id": expected_run_id,
        "parent_gate_run_id": parent_id,
        "child_run_id": child_id,
        "binding_status": binding_status,
        "binding_detail": binding_detail,
        "app_version": MULTIDOMAIN_VERSION,
        "harness_version": harness_version,
        "created_at": utc_now_iso(),
        "run_folder": str(run_folder),
        "required_files": {
            name: {k: v for k, v in check.items() if k != "_parsed_payload"}
            for name, check in checks.items()
        },
        "stage_manifest_completed": stage_manifest_completed,
        "secret_findings": secret_findings,
        "blocked_reasons": blocked_reasons,
        "status": status,
        "evidence_gate_passed": passed,
        "scoring_permitted": passed,
    }
    if write:
        atomic_write_json(stage / "PRE_SCORE_EVIDENCE_GATE.json", payload)
    return payload


def write_scoring_decision(
    run_folder: Path,
    *,
    scoring_permitted: bool,
    real_benchmark_completed: bool,
    status: str,
    blocked_reasons: list[str],
    scores: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write SCORING_DECISION.json. Nulls (never 0/100 substitutes) when blocked."""
    run_folder = Path(run_folder)
    stage = run_folder / "accuracy_stage_compare"
    payload: dict[str, Any] = {
        "schema_version": GATE_SCHEMA_VERSION,
        "run_id": run_folder.name,
        "app_version": MULTIDOMAIN_VERSION,
        "harness_version": MULTIDOMAIN_VERSION,
        "created_at": utc_now_iso(),
        "scoring_permitted": bool(scoring_permitted),
        "real_benchmark_completed": bool(real_benchmark_completed),
        "status": status,
        "blocked_reasons": list(blocked_reasons),
        "raw_cer_percent": None,
        "stable_cer_percent": None,
        "final_cer_percent": None,
        "raw_accuracy_percent": None,
        "stable_accuracy_percent": None,
        "final_accuracy_percent": None,
    }
    if scoring_permitted and scores:
        for key in (
            "raw_cer_percent",
            "stable_cer_percent",
            "final_cer_percent",
            "raw_accuracy_percent",
            "stable_accuracy_percent",
            "final_accuracy_percent",
        ):
            if key in scores and scores[key] is not None:
                payload[key] = float(scores[key])
    atomic_write_json(stage / "SCORING_DECISION.json", payload)
    return payload


def build_stop_source_map(run_folder: Path) -> dict[str, Any]:
    """Map every Stop-related evidence source for the current run (spec I)."""
    run_folder = Path(run_folder)
    sources = [
        {
            "source": "stop_finalize_worker",
            "source_file": "alpha/utils/stop_finalize_worker.py",
            "evidence_path": "artifacts/LIVE_RUN_STATUS.json",
            "semantics": "stop_finalize_completed flag written by the Stop finalization worker",
        },
        {
            "source": "LIVE_RUN_STATUS",
            "source_file": "alpha/utils/atomic_latest_state.py",
            "evidence_path": "artifacts/LIVE_RUN_STATUS.json",
            "semantics": "authoritative live run status; is_stopping/is_finalizing flags",
        },
        {
            "source": "RUN_MANIFEST",
            "source_file": "alpha/utils/troubleshooting_paths.py",
            "evidence_path": "RUN_MANIFEST.json",
            "semantics": "final_status and completed_at set on normal app exit",
        },
        {
            "source": "crash_guard_final_exit",
            "source_file": "alpha/utils/crash_guard_log.py",
            "evidence_path": "logs/",
            "semantics": (
                "POST_RUN_NORMAL_APP_EXIT marks a normal exit after a run; "
                "CLOSED_BEFORE_START_CLASSIFIED marks an app closed before any run started. "
                "They are distinct event types and must not be treated as equivalent."
            ),
        },
        {
            "source": "stage_manifest",
            "source_file": "alpha/utils/accuracy_stage_capture.py",
            "evidence_path": "accuracy_stage_compare/stage_manifest.json",
            "semantics": "stage_capture_complete implies three-stage finalization ran after Stop",
        },
    ]
    payload = {
        "schema_version": GATE_SCHEMA_VERSION,
        "run_id": run_folder.name,
        "app_version": MULTIDOMAIN_VERSION,
        "created_at": utc_now_iso(),
        "sources": sources,
    }
    atomic_write_json(run_folder / "accuracy_stage_compare" / "STOP_SOURCE_MAP.json", payload)
    return payload


def build_stop_evidence_reconciliation(
    run_folder: Path,
    *,
    run_started_at: str = "",
    runtime_child_exited_at: str = "",
) -> dict[str, Any]:
    """Reconcile Stop evidence from current-run physical records only (spec I/F).

    Requires stage_manifest.json to exist before reconciliation. Only sources
    physically opened and inspected are listed in source_records. Crash-guard
    records are not claimed unless physically opened.
    """
    run_folder = Path(run_folder)
    stage = run_folder / "accuracy_stage_compare"
    source_records: list[dict[str, Any]] = []
    conflicts: list[str] = []
    ignored_out_of_run: list[str] = []
    stage_manifest_path = stage / "stage_manifest.json"

    if not stage_manifest_path.exists():
        payload = {
            "schema_version": GATE_SCHEMA_VERSION,
            "run_id": run_folder.name,
            "app_version": MULTIDOMAIN_VERSION,
            "run_started_at": run_started_at,
            "runtime_child_exited_at": runtime_child_exited_at,
            "authoritative_sources": [],
            "corroborating_sources": [],
            "ignored_out_of_run_records": [],
            "source_records": [],
            "stop_ui_callback_completed": None,
            "stop_finalize_completed": None,
            "final_output_completed": None,
            "process_exit_normal": None,
            "conflicts": ["stage_manifest_missing_before_stop_reconciliation"],
            "stop_evidence_verified": False,
            "status": "STOP_EVIDENCE_INCOMPLETE",
            "stage_manifest_required_before_reconciliation": True,
            "stage_manifest_present": False,
            "crash_guard_inspected": False,
            "created_at": utc_now_iso(),
        }
        stage.mkdir(parents=True, exist_ok=True)
        atomic_write_json(stage / "STOP_EVIDENCE_RECONCILIATION.json", payload)
        return payload

    def _load(path: Path) -> dict[str, Any]:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    live_status_path = run_folder / "artifacts" / "LIVE_RUN_STATUS.json"
    run_manifest_path = run_folder / "RUN_MANIFEST.json"
    live_status = _load(live_status_path)
    run_manifest = _load(run_manifest_path)
    stage_manifest = _load(stage_manifest_path)

    stop_ui_callback_completed = None
    stop_finalize_completed = None
    final_output_completed = None
    process_exit_normal = None

    if live_status:
        source_records.append(
            {
                "source": "LIVE_RUN_STATUS",
                "path": "artifacts/LIVE_RUN_STATUS.json",
                "physical_source_path": str(live_status_path.resolve()),
                "inspected": True,
                "is_stopping": live_status.get("is_stopping"),
                "is_finalizing": live_status.get("is_finalizing"),
                "final_status": live_status.get("final_status"),
            }
        )
        stop_ui_callback_completed = (
            live_status.get("is_stopping") is False and live_status.get("is_finalizing") is False
        )
        stop_finalize_completed = stop_ui_callback_completed

    if run_manifest:
        final_status = str(run_manifest.get("final_status") or "")
        source_records.append(
            {
                "source": "RUN_MANIFEST",
                "path": "RUN_MANIFEST.json",
                "physical_source_path": str(run_manifest_path.resolve()),
                "inspected": True,
                "final_status": final_status,
                "completed_at": run_manifest.get("completed_at"),
            }
        )
        process_exit_normal = final_status in ("completed", "completed_normal", "normal_exit")

    if stage_manifest:
        source_records.append(
            {
                "source": "stage_manifest",
                "path": "accuracy_stage_compare/stage_manifest.json",
                "physical_source_path": str(stage_manifest_path.resolve()),
                "inspected": True,
                "stage_capture_complete": stage_manifest.get("stage_capture_complete"),
                "completed": stage_manifest.get("completed"),
            }
        )
        final_output_completed = bool(
            stage_manifest.get("stage_capture_complete") or stage_manifest.get("completed")
        )
    final_txt = stage / "final_alpha_output.txt"
    if final_output_completed is None:
        final_output_completed = final_txt.exists() and final_txt.stat().st_size > 0
    elif not final_output_completed and final_txt.exists() and final_txt.stat().st_size > 0:
        final_output_completed = True

    authoritative_sources = [r["source"] for r in source_records]
    if live_status and run_manifest:
        if process_exit_normal is False and stop_finalize_completed is True:
            conflicts.append("run_manifest_not_completed_but_live_status_finalized")
        if process_exit_normal is True and stop_finalize_completed is False:
            conflicts.append("run_manifest_completed_but_live_status_still_finalizing")

    if not source_records:
        status = "STOP_EVIDENCE_INCOMPLETE"
        verified = False
    elif conflicts:
        status = "STOP_EVIDENCE_CONFLICT"
        verified = False
    else:
        verified = bool(final_output_completed) and (process_exit_normal is not False)
        status = "STOP_EVIDENCE_VERIFIED" if verified else "STOP_EVIDENCE_INCOMPLETE"

    payload = {
        "schema_version": GATE_SCHEMA_VERSION,
        "run_id": run_folder.name,
        "app_version": MULTIDOMAIN_VERSION,
        "run_started_at": run_started_at,
        "runtime_child_exited_at": runtime_child_exited_at,
        "authoritative_sources": authoritative_sources,
        "corroborating_sources": ["final_alpha_output.txt"] if final_txt.exists() else [],
        "ignored_out_of_run_records": ignored_out_of_run,
        "source_records": source_records,
        "stop_ui_callback_completed": stop_ui_callback_completed,
        "stop_finalize_completed": stop_finalize_completed,
        "final_output_completed": final_output_completed,
        "process_exit_normal": process_exit_normal,
        "conflicts": conflicts,
        "stop_evidence_verified": verified,
        "status": status,
        "stage_manifest_required_before_reconciliation": True,
        "stage_manifest_present": True,
        "crash_guard_inspected": False,
        "created_at": utc_now_iso(),
    }
    atomic_write_json(stage / "STOP_EVIDENCE_RECONCILIATION.json", payload)
    return payload


def extract_numeric_entities(reference_text: str) -> dict[str, list[str]]:
    """Extract numbers, dates/times, money/percentages from reference after runtime."""
    text = reference_text
    money = re.findall(
        r"\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:円|万円|億円)|[一二三四五六七八九十百千万億兆]+円",
        text,
    )
    percents = re.findall(r"\d+(?:\.\d+)?\s*%|パーセント", text)
    # Also capture forms like 3.2%
    percents += re.findall(r"\d+\.\d+%", text)
    dates = re.findall(
        r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|午前\d{1,2}時(?:\d{1,2}分)?|午後\d{1,2}時(?:\d{1,2}分)?",
        text,
    )
    numbers = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:件|名|社|回|人|台)?", text)
    # Deduplicate preserving order
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            item = item.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    return {
        "numeric_entities": _uniq(numbers),
        "dates_times": _uniq(dates),
        "money_percentages": _uniq(money + percents),
    }
