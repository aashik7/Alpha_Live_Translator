"""Temporary WAV audio chunk capture for debugging — auto-deleted after retention.

V26.5.3: retain synchronized mixed / system / mic streams, including genuine
zero/near-zero silence. Retention is observational, async, bounded, and must
never stop transcription. Does not alter Deepgram-delivery PCM.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

from alpha.constants import (
    AUDIO_TEMP_CAPTURE_ENABLED,
    AUDIO_TEMP_CHUNK_SECONDS,
    AUDIO_TEMP_MAX_TOTAL_GB,
    AUDIO_TEMP_RETENTION_HOURS,
    TEMP_AUDIO_AUTO_DELETE_ENABLED,
    TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
    TEMP_AUDIO_RETENTION_ENABLED,
    TEMP_AUDIO_RETENTION_HOURS,
)

# Packet classifications (V26.5.3 silence definitions)
PACKET_SOURCE_SILENCE = "SOURCE_SILENCE"
PACKET_SOURCE_SILENT_PACKET = "SOURCE_SILENT_PACKET"
PACKET_CAPTURE_GAP = "CAPTURE_GAP"
PACKET_UNKNOWN = "UNKNOWN"
PACKET_ACTIVE = "ACTIVE"

_NEAR_ZERO_ABS = 50  # int16 near-zero threshold for SOURCE_SILENCE classification
_RETENTION_QUEUE_MAX = 256
_SAMPLE_RATE = 16000
_CHANNELS = 1
_SAMPLE_WIDTH = 2

_lock = threading.Lock()
_manifest: dict[str, Any] = {}
_chunk_buffers: dict[str, bytearray] = {
    "mixed": bytearray(),
    "system": bytearray(),
    "mic": bytearray(),
}
_stream_sequences: dict[str, int] = {"mixed": 0, "system": 0, "mic": 0}
_stream_mono_start: dict[str, float | None] = {"mixed": None, "system": None, "mic": None}
_started = False
_writer_thread: threading.Thread | None = None
_retention_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=_RETENTION_QUEUE_MAX)
_retention_drop_count = 0
_retention_error_count = 0
_stop_writer = False


def _retention_hours() -> int:
    return int(TEMP_AUDIO_RETENTION_HOURS or AUDIO_TEMP_RETENTION_HOURS)


def _mono_folder(stream_type: str) -> Path:
    from alpha.utils.troubleshooting_paths import get_audio_temp_path

    mapping = {
        "mixed": "mixed_audio_dir",
        "system": "system_audio_dir",
        "mic": "mic_audio_dir",
    }
    return get_audio_temp_path(mapping.get(stream_type, "mixed_audio_dir"))


def _run_metadata() -> dict[str, str]:
    try:
        from alpha.constants import APP_VERSION
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if ident is not None:
            return {
                "app_version": APP_VERSION,
                "run_id": ident.run_id,
                "run_timestamp": ident.run_timestamp,
            }
    except Exception:
        pass
    try:
        from alpha.constants import APP_VERSION

        return {"app_version": APP_VERSION, "run_id": "", "run_timestamp": ""}
    except Exception:
        return {"app_version": "", "run_id": "", "run_timestamp": ""}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify_pcm_bytes(pcm_bytes: bytes) -> str:
    """Classify retained PCM. Never converts UNKNOWN into source silence."""
    if not pcm_bytes:
        return PACKET_UNKNOWN
    if len(pcm_bytes) % _SAMPLE_WIDTH != 0:
        return PACKET_UNKNOWN
    import array

    samples = array.array("h")
    samples.frombytes(pcm_bytes)
    if not samples:
        return PACKET_UNKNOWN
    if all(abs(s) <= _NEAR_ZERO_ABS for s in samples):
        return PACKET_SOURCE_SILENCE
    return PACKET_ACTIVE


def retention_queue_stats() -> dict[str, Any]:
    with _lock:
        return {
            "queue_maxsize": _RETENTION_QUEUE_MAX,
            "queue_size": _retention_queue.qsize(),
            "drop_count": _retention_drop_count,
            "error_count": _retention_error_count,
            "buffer_bytes": {k: len(v) for k, v in _chunk_buffers.items()},
            "bounded": True,
        }


def start_audio_temp_capture() -> None:
    global _started, _manifest, _writer_thread, _stop_writer
    if not AUDIO_TEMP_CAPTURE_ENABLED or _started:
        return
    _started = True
    _stop_writer = False
    meta = _run_metadata()
    created = time.time()
    retention = _retention_hours()
    expires = created + retention * 3600
    _manifest = {
        "app_version": meta.get("app_version", ""),
        "run_id": meta.get("run_id", ""),
        "run_timestamp": meta.get("run_timestamp", ""),
        "capture_enabled": True,
        "retention_hours": retention,
        "auto_delete_enabled": TEMP_AUDIO_AUTO_DELETE_ENABLED,
        "include_in_upload_package": TEMP_AUDIO_INCLUDE_IN_UPLOAD_PACKAGE,
        "max_total_gb": AUDIO_TEMP_MAX_TOTAL_GB,
        "chunk_duration_seconds": AUDIO_TEMP_CHUNK_SECONDS,
        "sample_rate": _SAMPLE_RATE,
        "channels": _CHANNELS,
        "sample_width": _SAMPLE_WIDTH,
        "format": "wav_pcm16",
        "encoding": "pcm_s16le",
        "streams_saved": ["mixed", "system", "mic"],
        "streams_available": ["mixed", "system", "mic"],
        "silence_policy": {
            "retain_zero_and_near_zero": True,
            "never_skip_based_on_amplitude": True,
            "never_synthesize_elapsed_silence": True,
            "source_silent_packet_classification": PACKET_SOURCE_SILENT_PACKET,
            "capture_gap_classification": PACKET_CAPTURE_GAP,
        },
        "files": [],
        "chunks": [],
        "packets": [],
        "capture_gaps": [],
        "created_at": datetime.fromtimestamp(created).isoformat(timespec="seconds"),
        "expires_at": datetime.fromtimestamp(expires).isoformat(timespec="seconds"),
    }
    for key in _chunk_buffers:
        _chunk_buffers[key] = bytearray()
        _stream_sequences[key] = 0
        _stream_mono_start[key] = None
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("TEMP_AUDIO_CAPTURE_ENABLED")
        jp_accuracy_log("AUDIO_TEMP_CAPTURE_STARTED")
        jp_accuracy_log("TEMP_AUDIO_RETENTION_2H_ACTIVE", retention_hours=retention)
        jp_accuracy_log("TEMP_AUDIO_EXCLUDED_FROM_UPLOAD_PACKAGE")
        jp_accuracy_log(
            "TEMP_AUDIO_MULTI_STREAM_RETENTION_ACTIVE",
            streams="mixed,system,mic",
        )
    except Exception:
        pass
    _ensure_writer_thread()
    _write_manifest()


def _ensure_writer_thread() -> None:
    global _writer_thread, _stop_writer
    if _writer_thread is not None and _writer_thread.is_alive():
        return
    _stop_writer = False

    def _worker() -> None:
        while True:
            try:
                item = _retention_queue.get(timeout=0.25)
            except queue.Empty:
                if _stop_writer and _retention_queue.empty():
                    break
                continue
            if item is None:
                if _stop_writer:
                    break
                continue
            try:
                _flush_chunk_locked_from_item(item)
            except Exception:
                global _retention_error_count
                with _lock:
                    _retention_error_count += 1
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log("TEMP_AUDIO_RETENTION_WRITE_FAILED_NON_BLOCKING")
                except Exception:
                    pass

    _writer_thread = threading.Thread(
        target=_worker, name="AudioTempRetentionWriter", daemon=True
    )
    _writer_thread.start()


def _enqueue_flush(item: dict[str, Any]) -> None:
    """Non-blocking enqueue. On overflow drop the oldest retention item only."""
    global _retention_drop_count, _retention_error_count
    _ensure_writer_thread()
    try:
        _retention_queue.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        _retention_queue.get_nowait()
        _retention_drop_count += 1
    except queue.Empty:
        pass
    try:
        _retention_queue.put_nowait(item)
        return
    except queue.Full:
        _retention_drop_count += 1
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "TEMP_AUDIO_RETENTION_QUEUE_OVERFLOW_DROPPED",
                drop_count=_retention_drop_count,
            )
        except Exception:
            pass
    try:
        _flush_chunk_locked_from_item(item)
    except Exception:
        with _lock:
            _retention_error_count += 1


def ingest_audio_chunk(
    pcm_bytes: bytes | None,
    *,
    stream_type: str = "mixed",
    packet_classification: str | None = None,
    source_frame_count: int | None = None,
    explicit_silent_packet: bool = False,
) -> None:
    """Observe PCM for diagnostic retention. Never raises into the capture path.

    Silent / near-zero PCM is retained. Empty bytes are only expanded to zeros when
    an explicit silent packet with a known frame count is provided.
    """
    try:
        _ingest_audio_chunk_impl(
            pcm_bytes,
            stream_type=stream_type,
            packet_classification=packet_classification,
            source_frame_count=source_frame_count,
            explicit_silent_packet=explicit_silent_packet,
        )
    except Exception:
        global _retention_error_count
        with _lock:
            _retention_error_count += 1
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "TEMP_AUDIO_RETENTION_INGEST_FAILED_NON_BLOCKING",
                stream_type=stream_type,
            )
        except Exception:
            pass


def _ingest_audio_chunk_impl(
    pcm_bytes: bytes | None,
    *,
    stream_type: str,
    packet_classification: str | None,
    source_frame_count: int | None,
    explicit_silent_packet: bool,
) -> None:
    if not AUDIO_TEMP_CAPTURE_ENABLED:
        return
    if not _started:
        start_audio_temp_capture()

    key = stream_type if stream_type in _chunk_buffers else "mixed"
    data = bytes(pcm_bytes or b"")
    frames_from_bytes = len(data) // _SAMPLE_WIDTH if data else 0

    if explicit_silent_packet or packet_classification == PACKET_SOURCE_SILENT_PACKET:
        # Explicit API silent packet: retain exact zero-frame count; do not invent
        # silence from elapsed wall time alone.
        n_frames = int(source_frame_count or frames_from_bytes or 0)
        if n_frames <= 0:
            classification = PACKET_UNKNOWN
            data = b""
            retained_frames = 0
        else:
            data = b"\x00\x00" * n_frames
            classification = PACKET_SOURCE_SILENT_PACKET
            retained_frames = n_frames
            frames_from_bytes = n_frames
    elif not data:
        # Empty without explicit silent-packet frame count → UNKNOWN; do not synthesize.
        classification = PACKET_UNKNOWN
        retained_frames = 0
    else:
        classification = packet_classification or classify_pcm_bytes(data)
        retained_frames = frames_from_bytes

    mono_now = time.monotonic()
    wall_now = time.time()
    with _lock:
        seq = int(_stream_sequences.get(key, 0))
        _stream_sequences[key] = seq + 1
        if _stream_mono_start.get(key) is None:
            _stream_mono_start[key] = mono_now
        start_mono = float(_stream_mono_start[key] or mono_now)
        # Advance expected continuous timeline by retained frames (0 for UNKNOWN empty).
        frame_dur = retained_frames / float(_SAMPLE_RATE) if retained_frames else 0.0
        end_mono = start_mono + frame_dur
        if retained_frames:
            _stream_mono_start[key] = end_mono
        meta = _run_metadata()
        packet_entry = {
            "run_id": meta.get("run_id", ""),
            "stream_type": key,
            "sequence_number": seq,
            "monotonic_start": start_mono,
            "monotonic_end": end_mono if retained_frames else mono_now,
            "wall_start": wall_now,
            "wall_end": wall_now + frame_dur,
            "source_frame_count": int(source_frame_count if source_frame_count is not None else frames_from_bytes),
            "retained_frame_count": retained_frames,
            "byte_count": len(data),
            "sample_rate": _SAMPLE_RATE,
            "channels": _CHANNELS,
            "encoding": "pcm_s16le",
            "packet_classification": classification,
            "sha256": _sha256_bytes(data) if data else "",
        }
        _manifest.setdefault("packets", []).append(packet_entry)
        # Cap packet log growth (bounded evidence).
        if len(_manifest["packets"]) > 200000:
            _manifest["packets"] = _manifest["packets"][-100000:]

        if data:
            _chunk_buffers[key].extend(data)
            bytes_per_chunk = (
                _SAMPLE_RATE * _CHANNELS * _SAMPLE_WIDTH * AUDIO_TEMP_CHUNK_SECONDS
            )
            while len(_chunk_buffers[key]) >= bytes_per_chunk:
                chunk_data = bytes(_chunk_buffers[key][:bytes_per_chunk])
                del _chunk_buffers[key][:bytes_per_chunk]
                _enqueue_flush(
                    {
                        "stream_type": key,
                        "pcm_data": chunk_data,
                        "duration_seconds": float(AUDIO_TEMP_CHUNK_SECONDS),
                        "packet_classification": classification,
                    }
                )


def record_capture_gap(
    *,
    stream_type: str,
    reason: str,
    expected_sequence: int | None = None,
    observed_sequence: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record CAPTURE_GAP without synthesizing silence. Never raises."""
    try:
        if not _started:
            start_audio_temp_capture()
        meta = _run_metadata()
        entry = {
            "run_id": meta.get("run_id", ""),
            "stream_type": stream_type,
            "packet_classification": PACKET_CAPTURE_GAP,
            "reason": reason,
            "expected_sequence": expected_sequence,
            "observed_sequence": observed_sequence,
            "monotonic_time": time.monotonic(),
            "wall_time": time.time(),
            "detail": detail or {},
        }
        with _lock:
            _manifest.setdefault("capture_gaps", []).append(entry)
            _manifest.setdefault("packets", []).append(
                {
                    "run_id": meta.get("run_id", ""),
                    "stream_type": stream_type,
                    "sequence_number": observed_sequence
                    if observed_sequence is not None
                    else int(_stream_sequences.get(stream_type, -1)),
                    "monotonic_start": time.monotonic(),
                    "monotonic_end": time.monotonic(),
                    "source_frame_count": 0,
                    "retained_frame_count": 0,
                    "byte_count": 0,
                    "sample_rate": _SAMPLE_RATE,
                    "channels": _CHANNELS,
                    "encoding": "pcm_s16le",
                    "packet_classification": PACKET_CAPTURE_GAP,
                    "sha256": "",
                    "reason": reason,
                }
            )
        _write_manifest()
    except Exception:
        global _retention_error_count
        with _lock:
            _retention_error_count += 1


def _flush_chunk_locked_from_item(item: dict[str, Any]) -> None:
    stream_type = str(item.get("stream_type") or "mixed")
    pcm_data = bytes(item.get("pcm_data") or b"")
    duration_seconds = float(item.get("duration_seconds") or 0.0)
    packet_classification = str(item.get("packet_classification") or PACKET_UNKNOWN)
    if not pcm_data:
        return
    folder = _mono_folder(stream_type)
    folder.mkdir(parents=True, exist_ok=True)
    meta = _run_metadata()
    run_id = meta.get("run_id", "run") or "run"
    ts = time.strftime("%Y%m%d-%H%M%S")
    with _lock:
        idx = len([c for c in _manifest.get("chunks", []) if c.get("stream_type") == stream_type])
    filename = f"{stream_type}_{run_id}_{idx:04d}.wav"
    if len(filename) > 120:
        filename = f"{stream_type}_{ts}_{idx:04d}.wav"
    path = folder / filename
    frame_count = len(pcm_data) // _SAMPLE_WIDTH
    if duration_seconds <= 0:
        duration_seconds = frame_count / float(_SAMPLE_RATE)
    end_ts = time.time()
    start_ts = end_ts - duration_seconds
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(_CHANNELS)
        wf.setsampwidth(_SAMPLE_WIDTH)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm_data)
    size_bytes = path.stat().st_size
    try:
        rel_path = str(path.relative_to(path.parent.parent.parent))
    except ValueError:
        rel_path = str(path)
    entry = {
        "filename": filename,
        "relative_path": rel_path,
        "stream_type": stream_type,
        "chunk_index": idx,
        "sequence_number": idx,
        "path": str(path),
        "start_time": datetime.fromtimestamp(start_ts).isoformat(timespec="seconds"),
        "end_time": datetime.fromtimestamp(end_ts).isoformat(timespec="seconds"),
        "start_timestamp": start_ts,
        "end_timestamp": end_ts,
        "duration_seconds": duration_seconds,
        "source_frame_count": frame_count,
        "retained_frame_count": frame_count,
        "byte_count": len(pcm_data),
        "size_bytes": size_bytes,
        "bytes": size_bytes,
        "sample_rate": _SAMPLE_RATE,
        "channels": _CHANNELS,
        "encoding": "pcm_s16le",
        "packet_classification": packet_classification,
        "sha256": _sha256_bytes(pcm_data),
        "run_id": meta.get("run_id", ""),
        "deleted": False,
    }
    with _lock:
        _manifest.setdefault("chunks", []).append(entry)
        _manifest.setdefault("files", []).append(entry)
        streams = set(_manifest.get("streams_saved") or [])
        streams.add(stream_type)
        _manifest["streams_saved"] = sorted(streams)
        _manifest["streams_available"] = sorted(streams)
        _enforce_size_limit_locked()
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "TEMP_AUDIO_CHUNK_WRITTEN",
            stream_type=stream_type,
            path=str(path),
            size_bytes=size_bytes,
            packet_classification=packet_classification,
        )
    except Exception:
        pass
    _write_manifest()


def flush_audio_temp_on_stop() -> None:
    """Flush remaining buffered audio at Stop (including trailing silence)."""
    global _retention_error_count
    if not AUDIO_TEMP_CAPTURE_ENABLED:
        return
    try:
        pending: list[dict[str, Any]] = []
        with _lock:
            for stream_type, buf in list(_chunk_buffers.items()):
                # Retain any remaining frames including short silent tails
                # (even below 1 second — do not discard silence).
                if len(buf) > 0:
                    pcm = bytes(buf)
                    buf.clear()
                    frames = len(pcm) // _SAMPLE_WIDTH
                    pending.append(
                        {
                            "stream_type": stream_type,
                            "pcm_data": pcm,
                            "duration_seconds": frames / float(_SAMPLE_RATE),
                            "packet_classification": classify_pcm_bytes(pcm),
                        }
                    )
        # Synchronous final flush so silence is durable before teardown.
        for item in pending:
            try:
                _flush_chunk_locked_from_item(item)
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "TEMP_AUDIO_FINAL_CHUNK_FLUSHED",
                        stream_type=item["stream_type"],
                        size_bytes=len(item["pcm_data"]),
                    )
                except Exception:
                    pass
            except Exception:
                with _lock:
                    _retention_error_count += 1
        while True:
            try:
                item = _retention_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                continue
            try:
                _flush_chunk_locked_from_item(item)
            except Exception:
                with _lock:
                    _retention_error_count += 1
        _write_manifest()
    except Exception:
        with _lock:
            _retention_error_count += 1


def _enforce_size_limit_locked() -> None:
    max_bytes = int(AUDIO_TEMP_MAX_TOTAL_GB * 1024 * 1024 * 1024)
    total = sum(
        int(c.get("size_bytes", 0))
        for c in _manifest.get("chunks", [])
        if not c.get("deleted")
    )
    chunks = sorted(
        [c for c in _manifest.get("chunks", []) if not c.get("deleted")],
        key=lambda c: c.get("start_timestamp", 0),
    )
    while total > max_bytes and chunks:
        old = chunks.pop(0)
        p = Path(old.get("path", ""))
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
        old["deleted"] = True
        old["delete_reason"] = "max_total_gb_exceeded"
        total -= int(old.get("size_bytes", 0))


def _write_manifest() -> None:
    from alpha.utils.troubleshooting_paths import get_audio_temp_path

    manifest_path = get_audio_temp_path("audio_manifest")
    summary_path = get_audio_temp_path("audio_temp_summary")
    with _lock:
        active = [c for c in _manifest.get("chunks", []) if not c.get("deleted")]
        total_size = sum(int(c.get("size_bytes", 0)) for c in active)
        total_duration = sum(float(c.get("duration_seconds", 0)) for c in active)
        _manifest["total_chunks"] = len(active)
        _manifest["total_bytes"] = total_size
        _manifest["total_duration_seconds_estimate"] = round(total_duration, 1)
        _manifest["retention_queue_stats"] = {
            "queue_maxsize": _RETENTION_QUEUE_MAX,
            "queue_size": _retention_queue.qsize(),
            "drop_count": _retention_drop_count,
            "error_count": _retention_error_count,
        }
        payload = json.dumps(_manifest, ensure_ascii=False, indent=2)
        streams = ",".join(_manifest.get("streams_saved", []))
        retention = _retention_hours()
        expires = _manifest.get("expires_at", "")
    try:
        manifest_path.write_text(payload, encoding="utf-8")
        lines = [
            "Temporary audio retention summary",
            f"retention_hours={retention}",
            f"expires_at={expires}",
            f"streams_saved={streams}",
            f"total_chunks={len(active)}",
            f"total_size_bytes={total_size}",
            f"sample_rate={_SAMPLE_RATE}",
            f"channels={_CHANNELS}",
            "included_in_upload_zip=false",
            "note=WAV audio excluded from upload package by default; silence retained",
            f"folder={manifest_path.parent}",
        ]
        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("TEMP_AUDIO_MANIFEST_WRITTEN", path=str(manifest_path))
            jp_accuracy_log("TEMP_AUDIO_SUMMARY_WRITTEN", path=str(summary_path))
        except Exception:
            pass
    except Exception:
        pass


def cleanup_old_audio_temp(*, reason: str = "retention") -> int:
    if not TEMP_AUDIO_RETENTION_ENABLED and not TEMP_AUDIO_AUTO_DELETE_ENABLED:
        return 0
    from alpha.utils.troubleshooting_paths import get_runs_root

    deleted = 0
    cutoff = time.time() - (_retention_hours() * 3600)
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("TEMP_AUDIO_RETENTION_CLEANUP_STARTED", reason=reason)
    except Exception:
        pass
    runs = get_runs_root()
    if not runs.exists():
        return 0
    for run_folder in runs.iterdir():
        if not run_folder.is_dir():
            continue
        audio_root = run_folder / "audio_temp"
        if not audio_root.exists():
            continue
        manifest_path = audio_root / "audio_manifest.json"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
        for sub in ("system_audio", "mic_audio", "mixed_audio"):
            subdir = audio_root / sub
            if not subdir.exists():
                continue
            for wav in subdir.glob("*.wav"):
                try:
                    if wav.stat().st_mtime < cutoff:
                        wav.unlink()
                        deleted += 1
                except Exception:
                    pass
        for chunk in manifest.get("chunks", []):
            if chunk.get("deleted"):
                continue
            end_ts = float(chunk.get("end_timestamp", 0))
            if end_ts and end_ts < cutoff:
                p = Path(chunk.get("path", ""))
                if p.exists():
                    try:
                        p.unlink()
                        deleted += 1
                    except Exception:
                        pass
                chunk["deleted"] = True
                chunk["delete_reason"] = reason
        if manifest:
            try:
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "TEMP_AUDIO_RETENTION_CLEANUP_COMPLETED",
            deleted_count=deleted,
            reason=reason,
        )
        jp_accuracy_log("TEMP_AUDIO_CLEANUP_NON_BLOCKING_CONFIRMED", reason=reason)
    except Exception:
        pass
    return deleted


def schedule_audio_cleanup_non_blocking(*, reason: str = "after_stop") -> None:
    def _worker() -> None:
        try:
            cleanup_old_audio_temp(reason=reason)
        except Exception:
            pass

    threading.Thread(
        target=_worker, name="AudioTempRetentionCleanup", daemon=True
    ).start()


def reset_audio_temp_session() -> None:
    global _started, _manifest, _chunk_buffers, _stop_writer, _retention_drop_count
    global _retention_error_count, _stream_sequences, _stream_mono_start, _writer_thread
    with _lock:
        _started = False
        _stop_writer = True
        _manifest = {}
        _chunk_buffers = {
            "mixed": bytearray(),
            "system": bytearray(),
            "mic": bytearray(),
        }
        _stream_sequences = {"mixed": 0, "system": 0, "mic": 0}
        _stream_mono_start = {"mixed": None, "system": None, "mic": None}
        _retention_drop_count = 0
        _retention_error_count = 0
    # Drain queue (including any prior poison pill) so a subsequent start does
    # not immediately exit the writer thread.
    while True:
        try:
            _retention_queue.get_nowait()
        except queue.Empty:
            break
    if _writer_thread is not None and _writer_thread.is_alive():
        try:
            _retention_queue.put_nowait(None)
        except Exception:
            pass
        _writer_thread.join(timeout=1.0)
    _writer_thread = None
    _stop_writer = False
