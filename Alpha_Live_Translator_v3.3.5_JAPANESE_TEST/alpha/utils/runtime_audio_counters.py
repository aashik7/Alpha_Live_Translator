"""Thread-safe per-run runtime audio delivery counters (V25.3.1 / V25.3.3)."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

_lock = threading.Lock()
_reset_count = 0
_freeze_count = 0
_counters: dict[str, Any] = {
    "run_id": "",
    "run_start_monotonic": None,
    "frozen": False,
    "audio_chunks_sent": 0,
    "audio_bytes_sent": 0,
    "deepgram_send_errors": 0,
    "audio_queue_overflow_count": 0,
    "audio_chunk_drop_count": 0,
    "system_audio_chunks_received": 0,
    "microphone_chunks_received": 0,
    "mixed_audio_chunks_created": 0,
    "capture_errors": 0,
}


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def merge_audio_metric(runtime_val: Any, host_val: Any, offline_val: Any) -> Any:
    if runtime_val is not None:
        return runtime_val
    if host_val is not None:
        return host_val
    return offline_val


def get_deepgram_client_send_totals(host: Any = None) -> dict[str, int]:
    if host is None:
        return {"audio_chunks_sent": 0, "audio_bytes_sent": 0}
    try:
        if hasattr(host, "get_authoritative_send_accounting"):
            return dict(host.get_authoritative_send_accounting())
    except Exception:
        pass
    return {
        "audio_chunks_sent": int(getattr(host, "_latency_audio_chunks_sent", 0) or 0),
        "audio_bytes_sent": int(getattr(host, "_latency_bytes_sent_total", 0) or 0),
    }


def verify_counter_crosscheck(host: Any = None) -> dict[str, Any]:
    with _lock:
        runtime_chunks = int(_counters.get("audio_chunks_sent") or 0)
        runtime_bytes = int(_counters.get("audio_bytes_sent") or 0)
        run_id = str(_counters.get("run_id") or "")
    client = get_deepgram_client_send_totals(host)
    client_chunks = int(client.get("audio_chunks_sent") or 0)
    client_bytes = int(client.get("audio_bytes_sent") or 0)
    chunks_match = runtime_chunks == client_chunks
    bytes_match = runtime_bytes == client_bytes
    return {
        "counter_crosscheck_passed": chunks_match and bytes_match,
        "audio_chunks_sent": runtime_chunks,
        "audio_bytes_sent": runtime_bytes,
        "deepgram_client_chunks_sent": client_chunks,
        "deepgram_client_bytes_sent": client_bytes,
        "counter_source": "runtime_audio_counters",
        "counter_run_id_match": bool(run_id),
        "chunks_match": chunks_match,
        "bytes_match": bytes_match,
    }


def reset_runtime_audio_counters(run_id: str = "") -> None:
    global _reset_count, _freeze_count
    try:
        from alpha.utils.live_runtime_metrics import reset_for_run

        reset_for_run(run_id)
    except Exception:
        pass
    with _lock:
        if _reset_count >= 1 and not _counters.get("frozen"):
            _jp_log("RUNTIME_AUDIO_COUNTERS_RESET_SKIPPED", run_id=run_id, reset_count=_reset_count)
            return
        _reset_count += 1
        _freeze_count = 0
        _counters.clear()
        _counters.update(
            {
                "run_id": run_id,
                "run_start_monotonic": time.monotonic(),
                "frozen": False,
                "audio_chunks_sent": 0,
                "audio_bytes_sent": 0,
                "deepgram_send_errors": 0,
                "audio_queue_overflow_count": 0,
                "audio_chunk_drop_count": 0,
                "system_audio_chunks_received": 0,
                "microphone_chunks_received": 0,
                "mixed_audio_chunks_created": 0,
                "capture_errors": 0,
            }
        )
    _jp_log("RUNTIME_AUDIO_COUNTERS_RESET", run_id=run_id, reset_count=_reset_count)


def note_system_audio_chunk_received() -> None:
    with _lock:
        if _counters.get("frozen"):
            return
        _counters["system_audio_chunks_received"] = int(_counters.get("system_audio_chunks_received", 0)) + 1


def note_microphone_chunk_received() -> None:
    with _lock:
        if _counters.get("frozen"):
            return
        _counters["microphone_chunks_received"] = int(_counters.get("microphone_chunks_received", 0)) + 1


def note_mixed_audio_chunk_created() -> None:
    with _lock:
        if _counters.get("frozen"):
            return
        _counters["mixed_audio_chunks_created"] = int(_counters.get("mixed_audio_chunks_created", 0)) + 1


def note_audio_chunk_sent(payload_len: int) -> None:
    try:
        from alpha.utils.live_runtime_metrics import note_audio_chunk_sent as _note

        _note(payload_len)
    except Exception:
        pass
    with _lock:
        if _counters.get("frozen"):
            return
        _counters["audio_chunks_sent"] = int(_counters.get("audio_chunks_sent", 0)) + 1
        _counters["audio_bytes_sent"] = int(_counters.get("audio_bytes_sent", 0)) + max(0, int(payload_len))


def note_deepgram_send_error() -> None:
    try:
        from alpha.utils.live_runtime_metrics import note_deepgram_send_error as _note

        _note()
    except Exception:
        pass
    with _lock:
        if _counters.get("frozen"):
            return
        _counters["deepgram_send_errors"] = int(_counters.get("deepgram_send_errors", 0)) + 1


def note_audio_queue_drop() -> None:
    try:
        from alpha.utils.live_runtime_metrics import note_audio_queue_drop as _note

        _note()
    except Exception:
        pass
    with _lock:
        if _counters.get("frozen"):
            return
        _counters["audio_queue_overflow_count"] = int(_counters.get("audio_queue_overflow_count", 0)) + 1
        _counters["audio_chunk_drop_count"] = int(_counters.get("audio_chunk_drop_count", 0)) + 1
    _jp_log("AUDIO_CHUNK_DROP_COUNT_INCREMENTED")


def note_capture_error() -> None:
    try:
        from alpha.utils.live_runtime_metrics import note_capture_error as _note

        _note()
    except Exception:
        pass
    with _lock:
        if _counters.get("frozen"):
            return
        _counters["capture_errors"] = int(_counters.get("capture_errors", 0)) + 1


def freeze_runtime_audio_counters(*, host: Any = None) -> dict[str, Any]:
    global _freeze_count
    with _lock:
        if _counters.get("frozen"):
            return dict(_counters)
        _freeze_count += 1
        _counters["frozen"] = True
        snapshot = dict(_counters)
    crosscheck = verify_counter_crosscheck(host)
    snapshot.update(crosscheck)
    _jp_log(
        "RUNTIME_AUDIO_COUNTERS_FROZEN",
        freeze_count=_freeze_count,
        **{k: snapshot.get(k) for k in snapshot if k not in ("run_start_monotonic",)},
    )
    return snapshot


def get_runtime_audio_counters(*, include_frozen_only: bool = False) -> dict[str, Any]:
    with _lock:
        if include_frozen_only and not _counters.get("frozen"):
            return {}
        return dict(_counters)


def get_reset_and_freeze_counts() -> dict[str, int]:
    with _lock:
        return {"reset_count": _reset_count, "freeze_count": _freeze_count}


def build_audio_delivery_summary(
    *,
    run_id: str = "",
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width_bytes: int = 2,
    run_elapsed_seconds: Optional[float] = None,
    host: Any = None,
) -> dict[str, Any]:
    snap = get_runtime_audio_counters()
    if run_id:
        snap["run_id"] = run_id
    start = snap.get("run_start_monotonic")
    elapsed = run_elapsed_seconds
    if elapsed is None and start is not None:
        elapsed = round(time.monotonic() - float(start), 3)

    bytes_sent = int(snap.get("audio_bytes_sent") or 0)
    calc_seconds = None
    ratio = None
    if bytes_sent > 0:
        calc_seconds = round(bytes_sent / (float(sample_rate) * float(channels) * float(sample_width_bytes)), 3)
    if calc_seconds and elapsed:
        ratio = round(calc_seconds / max(float(elapsed), 0.001), 4)

    crosscheck = verify_counter_crosscheck(host)
    missing: list[str] = []
    payload = {
        "run_id": snap.get("run_id") or run_id,
        "wire_encoding": "linear16",
        "wire_sample_rate": sample_rate,
        "wire_channels": channels,
        "sample_width_bytes": sample_width_bytes,
        "audio_chunks_sent": int(snap.get("audio_chunks_sent") or 0),
        "audio_bytes_sent": bytes_sent,
        "calculated_audio_seconds_sent": calc_seconds,
        "run_elapsed_seconds": elapsed,
        "audio_seconds_to_run_seconds_ratio": ratio,
        "audio_queue_overflow_count": int(snap.get("audio_queue_overflow_count") or 0),
        "audio_chunk_drop_count": int(snap.get("audio_chunk_drop_count") or 0),
        "system_audio_chunks_received": int(snap.get("system_audio_chunks_received") or 0),
        "microphone_chunks_received": int(snap.get("microphone_chunks_received") or 0),
        "mixed_audio_chunks_created": int(snap.get("mixed_audio_chunks_created") or 0),
        "capture_errors": int(snap.get("capture_errors") or 0),
        "deepgram_send_errors": int(snap.get("deepgram_send_errors") or 0),
        "counter_source": crosscheck.get("counter_source", "runtime_audio_counters"),
        "counter_run_id_match": crosscheck.get("counter_run_id_match"),
        "counter_crosscheck_passed": crosscheck.get("counter_crosscheck_passed"),
        "deepgram_client_chunks_sent": crosscheck.get("deepgram_client_chunks_sent"),
        "deepgram_client_bytes_sent": crosscheck.get("deepgram_client_bytes_sent"),
        "missing_metrics": missing,
        "generated_during_runtime": True,
        "generated_by_offline_repair": False,
        "frozen": bool(snap.get("frozen")),
    }
    for key in (
        "audio_chunks_sent",
        "audio_bytes_sent",
        "calculated_audio_seconds_sent",
        "run_elapsed_seconds",
        "system_audio_chunks_received",
        "microphone_chunks_received",
        "mixed_audio_chunks_created",
    ):
        if payload.get(key) is None:
            missing.append(key)
    payload["missing_metrics"] = sorted(set(missing))
    return payload
