"""Per-run live runtime metrics registry (V25.3.2)."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from alpha.constants import LIVE_RUNTIME_METRICS_REGISTRY_ENABLED

_lock = threading.Lock()
_metrics: dict[str, Any] = {
    "run_id": "",
    "frozen": False,
    "run_start_monotonic": None,
    "audio_chunks_sent": 0,
    "audio_bytes_sent": 0,
    "audio_queue_overflow_count": 0,
    "audio_chunk_drop_count": 0,
    "deepgram_send_errors": 0,
    "system_audio_chunks_received": 0,
    "microphone_chunks_received": 0,
    "mixed_audio_chunks_created": 0,
    "capture_errors": 0,
    "raw_deepgram_final_count": 0,
    "assembler_event_count": 0,
    "revision_requested_count": 0,
    "revision_applied_count": 0,
    "revision_rejected_to_append_count": 0,
    "append_count": 0,
    "no_op_count": 0,
    "suppression_count": 0,
    "suppression_candidate_count": 0,
    "ui_event_posted_count": 0,
    "ui_event_drained_count": 0,
    "speaker_distribution": {},
}


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def reset_for_run(run_id: str = "") -> None:
    if not LIVE_RUNTIME_METRICS_REGISTRY_ENABLED:
        return
    with _lock:
        _metrics.clear()
        _metrics.update(
            {
                "run_id": run_id,
                "frozen": False,
                "run_start_monotonic": time.monotonic(),
                "audio_chunks_sent": 0,
                "audio_bytes_sent": 0,
                "audio_queue_overflow_count": 0,
                "audio_chunk_drop_count": 0,
                "deepgram_send_errors": 0,
                "system_audio_chunks_received": 0,
                "microphone_chunks_received": 0,
                "mixed_audio_chunks_created": 0,
                "capture_errors": 0,
                "raw_deepgram_final_count": 0,
                "assembler_event_count": 0,
                "revision_requested_count": 0,
                "revision_applied_count": 0,
                "revision_rejected_to_append_count": 0,
                "append_count": 0,
                "no_op_count": 0,
                "suppression_count": 0,
                "suppression_candidate_count": 0,
                "ui_event_posted_count": 0,
                "ui_event_drained_count": 0,
                "speaker_distribution": {},
            }
        )
    _jp_log("LIVE_RUNTIME_METRICS_RESET", run_id=run_id)


def _inc(field: str, amount: int = 1) -> None:
    if not LIVE_RUNTIME_METRICS_REGISTRY_ENABLED:
        return
    with _lock:
        if _metrics.get("frozen"):
            return
        _metrics[field] = int(_metrics.get(field, 0)) + amount


def note_audio_chunk_sent(payload_len: int) -> None:
    _inc("audio_chunks_sent")
    with _lock:
        if _metrics.get("frozen"):
            return
        _metrics["audio_bytes_sent"] = int(_metrics.get("audio_bytes_sent", 0)) + max(0, int(payload_len))


def note_audio_queue_drop() -> None:
    _inc("audio_queue_overflow_count")
    _inc("audio_chunk_drop_count")


def note_deepgram_send_error() -> None:
    _inc("deepgram_send_errors")


def note_system_audio_chunk_received() -> None:
    _inc("system_audio_chunks_received")


def note_microphone_chunk_received() -> None:
    _inc("microphone_chunks_received")


def note_mixed_audio_chunk_created() -> None:
    _inc("mixed_audio_chunks_created")


def note_capture_error() -> None:
    _inc("capture_errors")


def note_raw_deepgram_final() -> None:
    _inc("raw_deepgram_final_count")


def note_assembler_event(applied_action: str, *, revision_requested: bool = False, rejected_to_append: bool = False) -> None:
    _inc("assembler_event_count")
    act = str(applied_action or "append")
    if act in ("append", "revise", "revise_previous"):
        _inc("append_count" if act == "append" else "revision_applied_count")
    elif act == "no_op":
        _inc("no_op_count")
    elif act in ("suppress", "suppressed_stop_tail"):
        _inc("suppression_count")
    elif act in ("suppress_candidate", "suppressed_stop_tail_candidate"):
        _inc("suppression_candidate_count")
    if revision_requested:
        _inc("revision_requested_count")
    if rejected_to_append:
        _inc("revision_rejected_to_append_count")


def note_ui_event_posted() -> None:
    _inc("ui_event_posted_count")


def note_ui_event_drained() -> None:
    _inc("ui_event_drained_count")


def set_speaker_distribution(dist: dict[str, int]) -> None:
    if not LIVE_RUNTIME_METRICS_REGISTRY_ENABLED:
        return
    with _lock:
        if _metrics.get("frozen"):
            return
        _metrics["speaker_distribution"] = dict(dist)


def freeze() -> dict[str, Any]:
    with _lock:
        _metrics["frozen"] = True
        snap = dict(_metrics)
    _jp_log("LIVE_RUNTIME_METRICS_FROZEN", run_id=snap.get("run_id"))
    return snap


def get_metrics() -> dict[str, Any]:
    with _lock:
        return dict(_metrics)


def get_applied_action_counts() -> dict[str, int]:
    with _lock:
        return {
            "append": int(_metrics.get("append_count") or 0),
            "revise": int(_metrics.get("revision_applied_count") or 0),
            "no_op": int(_metrics.get("no_op_count") or 0),
            "suppress": int(_metrics.get("suppression_count") or 0),
            "suppress_candidate": int(_metrics.get("suppression_candidate_count") or 0),
        }


def build_audio_delivery_summary(*, run_elapsed_seconds: Optional[float] = None) -> dict[str, Any]:
    snap = get_metrics()
    bytes_sent = int(snap.get("audio_bytes_sent") or 0)
    rate = 16000
    calc_seconds = round(bytes_sent / (rate * 2.0), 3) if bytes_sent > 0 else 0.0
    elapsed = run_elapsed_seconds
    if elapsed is None and snap.get("run_start_monotonic") is not None:
        elapsed = round(time.monotonic() - float(snap["run_start_monotonic"]), 3)
    ratio = None
    if calc_seconds and elapsed:
        ratio = round(calc_seconds / max(float(elapsed), 0.001), 4)
    return {
        "run_id": snap.get("run_id", ""),
        "wire_encoding": "linear16",
        "wire_sample_rate": rate,
        "wire_channels": 1,
        "sample_width_bytes": 2,
        "audio_chunks_sent": int(snap.get("audio_chunks_sent") or 0),
        "audio_bytes_sent": bytes_sent,
        "calculated_audio_seconds_sent": calc_seconds or None,
        "run_elapsed_seconds": elapsed,
        "audio_seconds_to_run_seconds_ratio": ratio,
        "audio_queue_overflow_count": int(snap.get("audio_queue_overflow_count") or 0),
        "audio_chunk_drop_count": int(snap.get("audio_chunk_drop_count") or 0),
        "system_audio_chunks_received": int(snap.get("system_audio_chunks_received") or 0),
        "microphone_chunks_received": int(snap.get("microphone_chunks_received") or 0),
        "mixed_audio_chunks_created": int(snap.get("mixed_audio_chunks_created") or 0),
        "capture_errors": int(snap.get("capture_errors") or 0),
        "deepgram_send_errors": int(snap.get("deepgram_send_errors") or 0),
        "raw_deepgram_final_count": int(snap.get("raw_deepgram_final_count") or 0),
        "missing_metrics": [],
        "generated_during_runtime": True,
        "generated_by_offline_repair": False,
    }
