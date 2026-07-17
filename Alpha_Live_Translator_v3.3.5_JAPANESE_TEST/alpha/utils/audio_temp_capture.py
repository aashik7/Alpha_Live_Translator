"""Temporary WAV audio chunk capture for debugging — auto-deleted after retention."""



from __future__ import annotations



import json

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



_lock = threading.Lock()

_manifest: dict[str, Any] = {}

_chunk_buffers: dict[str, bytearray] = {

    "mixed": bytearray(),

    "system": bytearray(),

    "mic": bytearray(),

}

_sample_rate = 16000

_channels = 1

_sample_width = 2

_started = False





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





def start_audio_temp_capture() -> None:

    global _started, _manifest

    if not AUDIO_TEMP_CAPTURE_ENABLED or _started:

        return

    _started = True

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

        "sample_rate": _sample_rate,

        "channels": _channels,

        "sample_width": _sample_width,

        "format": "wav_pcm16",

        "streams_saved": ["mixed"],

        "streams_available": ["mixed"],

        "files": [],

        "chunks": [],

        "created_at": datetime.fromtimestamp(created).isoformat(timespec="seconds"),

        "expires_at": datetime.fromtimestamp(expires).isoformat(timespec="seconds"),

    }

    try:

        from alpha.utils.japanese_accuracy_log import jp_accuracy_log



        jp_accuracy_log("TEMP_AUDIO_CAPTURE_ENABLED")

        jp_accuracy_log("AUDIO_TEMP_CAPTURE_STARTED")

        jp_accuracy_log("TEMP_AUDIO_RETENTION_2H_ACTIVE", retention_hours=retention)

        jp_accuracy_log("TEMP_AUDIO_EXCLUDED_FROM_UPLOAD_PACKAGE")

    except Exception:

        pass

    _write_manifest()





def ingest_audio_chunk(pcm_bytes: bytes, *, stream_type: str = "mixed") -> None:

    if not AUDIO_TEMP_CAPTURE_ENABLED or not pcm_bytes:

        return

    if not _started:

        start_audio_temp_capture()

    key = stream_type if stream_type in _chunk_buffers else "mixed"

    with _lock:

        _chunk_buffers[key].extend(pcm_bytes)

        bytes_per_chunk = _sample_rate * _channels * _sample_width * AUDIO_TEMP_CHUNK_SECONDS

        while len(_chunk_buffers[key]) >= bytes_per_chunk:

            chunk_data = bytes(_chunk_buffers[key][:bytes_per_chunk])

            del _chunk_buffers[key][:bytes_per_chunk]

            _flush_chunk_locked(key, chunk_data)





def _flush_chunk_locked(stream_type: str, pcm_data: bytes) -> None:

    folder = _mono_folder(stream_type)

    meta = _run_metadata()

    run_id = meta.get("run_id", "run")

    ts = time.strftime("%Y%m%d-%H%M%S")

    idx = len([c for c in _manifest.get("chunks", []) if c.get("stream_type") == stream_type])

    filename = f"{stream_type}_{run_id}_{idx:04d}.wav"

    if len(filename) > 120:

        filename = f"{stream_type}_{ts}_{idx:04d}.wav"

    path = folder / filename

    start_ts = time.time() - AUDIO_TEMP_CHUNK_SECONDS

    end_ts = time.time()

    try:

        with wave.open(str(path), "wb") as wf:

            wf.setnchannels(_channels)

            wf.setsampwidth(_sample_width)

            wf.setframerate(_sample_rate)

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

            "path": str(path),

            "start_time": datetime.fromtimestamp(start_ts).isoformat(timespec="seconds"),

            "end_time": datetime.fromtimestamp(end_ts).isoformat(timespec="seconds"),

            "start_timestamp": start_ts,

            "end_timestamp": end_ts,

            "duration_seconds": AUDIO_TEMP_CHUNK_SECONDS,

            "size_bytes": size_bytes,

            "bytes": size_bytes,

            "deleted": False,

        }

        _manifest.setdefault("chunks", []).append(entry)

        _manifest.setdefault("files", []).append(entry)

        try:

            from alpha.utils.japanese_accuracy_log import jp_accuracy_log



            jp_accuracy_log(

                "TEMP_AUDIO_CHUNK_WRITTEN",

                stream_type=stream_type,

                path=str(path),

                size_bytes=size_bytes,

            )

        except Exception:

            pass

        _enforce_size_limit_locked()

        _write_manifest()

    except Exception:

        pass





def flush_audio_temp_on_stop() -> None:

    """Flush remaining buffered audio at Stop."""

    if not AUDIO_TEMP_CAPTURE_ENABLED:

        return

    with _lock:

        for stream_type, buf in list(_chunk_buffers.items()):

            if len(buf) >= _sample_rate * _sample_width:

                pcm = bytes(buf)

                buf.clear()

                _flush_chunk_locked(stream_type, pcm)

                try:

                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log



                    jp_accuracy_log(

                        "TEMP_AUDIO_FINAL_CHUNK_FLUSHED",

                        stream_type=stream_type,

                        size_bytes=len(pcm),

                    )

                except Exception:

                    pass

    _write_manifest()





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

    active = [c for c in _manifest.get("chunks", []) if not c.get("deleted")]

    total_size = sum(int(c.get("size_bytes", 0)) for c in active)

    total_duration = sum(float(c.get("duration_seconds", 0)) for c in active)

    _manifest["total_chunks"] = len(active)

    _manifest["total_bytes"] = total_size

    _manifest["total_duration_seconds_estimate"] = round(total_duration, 1)

    try:

        manifest_path.write_text(

            json.dumps(_manifest, ensure_ascii=False, indent=2), encoding="utf-8"

        )

        retention = _retention_hours()

        expires = _manifest.get("expires_at", "")

        lines = [

            "Temporary audio retention summary",

            f"retention_hours={retention}",

            f"expires_at={expires}",

            f"streams_saved={','.join(_manifest.get('streams_saved', []))}",

            f"total_chunks={len(active)}",

            f"total_size_bytes={total_size}",

            f"sample_rate={_manifest.get('sample_rate', _sample_rate)}",

            f"channels={_manifest.get('channels', _channels)}",

            "included_in_upload_zip=false",

            "note=WAV audio excluded from upload package by default",

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

                        try:

                            from alpha.utils.japanese_accuracy_log import jp_accuracy_log



                            jp_accuracy_log(

                                "TEMP_AUDIO_EXPIRED_FILE_DELETED",

                                path=str(wav),

                                reason=reason,

                            )

                        except Exception:

                            pass

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

                        try:

                            from alpha.utils.japanese_accuracy_log import jp_accuracy_log



                            jp_accuracy_log(

                                "TEMP_AUDIO_EXPIRED_FILE_DELETED",

                                path=str(p),

                                reason=reason,

                            )

                        except Exception:

                            pass

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

    global _started, _manifest, _chunk_buffers

    with _lock:

        _started = False

        _manifest = {}

        _chunk_buffers = {"mixed": bytearray(), "system": bytearray(), "mic": bytearray()}


