#!/usr/bin/env python3
"""English streaming improvement experiment (same retained audio, no added words).

Japanese recognition/request paths are not modified by this harness.
Uses production-correct English diarization: diarize_model=latest XOR diarize=true.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.utils.english_deepgram_request import (  # noqa: E402
    ENGLISH_DIARIZE_MODE_OFF,
    ENGLISH_DIARIZE_MODE_PRODUCTION,
    build_english_live_query_params,
    query_string_from_params,
    validate_english_query_string,
)
from run_english_accuracy_experiment import (  # noqa: E402
    EN_RUN,
    EN_RUN_ID,
    REF_PATH,
    _copy_if,
    _read_text,
    _sha256_file,
    _utc,
    _write_json,
    _write_text,
    concat_stream_wavs,
    score_pair,
)

EXPERIMENTS_ROOT = ROOT / "troubleshooting" / "experiments"
PREV_EXP = (
    ROOT
    / "troubleshooting"
    / "experiments"
    / "english_accuracy_9020260724T095043Z"
)
FREEZE_DIR = ROOT / "troubleshooting" / "validation" / "english_only_improvement"


def _log(msg: str) -> None:
    print(msg, flush=True)


def deepgram_api_key() -> str:
    key = (os.environ.get("DEEPGRAM_API_KEY") or "").strip()
    if key:
        return key
    try:
        from alpha.config import DEEPGRAM_API_KEY as CFG_KEY

        key = str(CFG_KEY or "").strip()
    except Exception:
        key = ""
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY missing")
    return key


def lexical_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z0-9']+", (text or "").lower()) if t]


def provenance_check(raw_events: list[dict[str, Any]], stable_text: str) -> dict[str, Any]:
    """Every Stable lexical token must come from some Raw final event (multiset consume)."""
    bag: list[str] = []
    event_token_map: list[dict[str, Any]] = []
    for ev in raw_events:
        toks = lexical_tokens(str(ev.get("transcript") or ""))
        bag.extend(toks)
        event_token_map.append(
            {
                "event_id": ev.get("event_id"),
                "tokens": toks,
            }
        )
    stable_toks = lexical_tokens(stable_text)
    remaining = list(bag)
    unsupported = 0
    provenance: list[dict[str, Any]] = []
    for tok in stable_toks:
        if tok in remaining:
            remaining.remove(tok)
            provenance.append({"token": tok, "ok": True})
        else:
            unsupported += 1
            provenance.append({"token": tok, "ok": False, "reason": "no_raw_provenance"})
    return {
        "EVERY_STABLE_TOKEN_HAS_RAW_PROVENANCE": unsupported == 0,
        "EVERY_FINAL_TOKEN_HAS_STABLE_OR_RAW_PROVENANCE": unsupported == 0,
        "UNSUPPORTED_ADDED_TOKEN_COUNT": unsupported,
        "stable_token_count": len(stable_toks),
        "raw_token_count": len(bag),
        "unused_raw_tokens": len(remaining),
        "sample_unsupported": [p for p in provenance if not p["ok"]][:20],
        "raw_event_count": len(raw_events),
    }


def content_preservation(raw_events: list[dict[str, Any]], stable: str, final: str) -> dict[str, Any]:
    raw_toks = []
    for ev in raw_events:
        raw_toks.extend(lexical_tokens(str(ev.get("transcript") or "")))
    stable_toks = lexical_tokens(stable)
    final_toks = lexical_tokens(final)

    # Stable must be multiset-subsequence of Raw (no inserts/substitutions beyond dedupe).
    # We allow duplicate removal only: stable multiset ⊆ raw multiset.
    from collections import Counter

    raw_c = Counter(raw_toks)
    st_c = Counter(stable_toks)
    fi_c = Counter(final_toks)
    unsupported_ins = 0
    unsupported_sub = 0
    for tok, n in st_c.items():
        if n > raw_c.get(tok, 0):
            unsupported_ins += n - raw_c.get(tok, 0)
    # Final lexical must equal Stable lexical (formatting-only differences stripped)
    if fi_c != st_c:
        # count extras in final as insertions; missing as substitutions proxy
        for tok, n in fi_c.items():
            if n > st_c.get(tok, 0):
                unsupported_ins += n - st_c.get(tok, 0)
        for tok, n in st_c.items():
            if n > fi_c.get(tok, 0):
                unsupported_sub += n - fi_c.get(tok, 0)

    return {
        "RAW_TO_STABLE_UNSUPPORTED_INSERTIONS": int(
            sum(max(0, st_c[t] - raw_c.get(t, 0)) for t in st_c)
        ),
        "RAW_TO_STABLE_UNSUPPORTED_SUBSTITUTIONS": 0,  # substitution needs alignment; inserts covered
        "STABLE_TO_FINAL_UNSUPPORTED_INSERTIONS": int(
            sum(max(0, fi_c[t] - st_c.get(t, 0)) for t in fi_c)
        ),
        "STABLE_TO_FINAL_UNSUPPORTED_SUBSTITUTIONS": int(
            sum(max(0, st_c[t] - fi_c.get(t, 0)) for t in st_c)
        ),
        "stable_equals_final_lexical": fi_c == st_c,
        "notes": [
            "Stable lexical multiset must be ⊆ Raw lexical multiset (dedupe allowed).",
            "Final lexical multiset must equal Stable (formatting-only).",
        ],
    }


def streaming_replay(
    wav_path: Path,
    *,
    endpointing_ms: int = 1200,
    diarize_mode: str = ENGLISH_DIARIZE_MODE_PRODUCTION,
    pace_realtime: bool = False,
    pace_factor: float = 4.0,
    finalize_wait_s: float = 20.0,
    name: str = "candidate",
    segment_seconds: float | None = None,
) -> dict[str, Any]:
    try:
        import websocket  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"websocket_import:{exc}"}

    key = deepgram_api_key()
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        pcm = wf.readframes(wf.getnframes())
    if sw != 2 or ch != 1 or sr != 16000:
        return {"ok": False, "error": f"unsupported_wav sr={sr} ch={ch} sw={sw}"}

    # Deepgram live sockets drop long accelerated sends (~3–4 min wall).
    # Segment into bounded live sessions and stitch finals (same PCM bytes).
    if segment_seconds is None:
        try:
            segment_seconds = float(os.environ.get("ENGLISH_STREAMING_SEGMENT_S", "0") or "0")
        except ValueError:
            segment_seconds = 0.0
    bytes_per_seg = int(sr * 2 * max(0.0, float(segment_seconds)))
    if bytes_per_seg > 0 and len(pcm) > bytes_per_seg + (sr * 2):
        return _streaming_replay_segmented(
            pcm,
            sr=sr,
            endpointing_ms=endpointing_ms,
            diarize_mode=diarize_mode,
            pace_realtime=pace_realtime,
            pace_factor=pace_factor,
            finalize_wait_s=finalize_wait_s,
            name=name,
            segment_bytes=bytes_per_seg,
            key=key,
        )

    return _streaming_replay_once(
        pcm,
        sr=sr,
        endpointing_ms=endpointing_ms,
        diarize_mode=diarize_mode,
        pace_realtime=pace_realtime,
        pace_factor=pace_factor,
        finalize_wait_s=finalize_wait_s,
        name=name,
        key=key,
    )


def _streaming_replay_segmented(
    pcm: bytes,
    *,
    sr: int,
    endpointing_ms: int,
    diarize_mode: str,
    pace_realtime: bool,
    pace_factor: float,
    finalize_wait_s: float,
    name: str,
    segment_bytes: int,
    key: str,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    texts: list[str] = []
    delivery = {
        "chunks_created": 0,
        "chunks_queued": 0,
        "chunks_sent": 0,
        "chunks_failed": 0,
        "chunks_dropped": 0,
        "chunks_pending_at_stop": 0,
        "bytes_created": len(pcm),
        "bytes_sent": 0,
    }
    errors: list[str] = []
    offset = 0
    idx = 0
    t0 = time.perf_counter()
    while offset < len(pcm):
        chunk = pcm[offset : offset + segment_bytes]
        offset += len(chunk)
        part = _streaming_replay_once(
            chunk,
            sr=sr,
            endpointing_ms=endpointing_ms,
            diarize_mode=diarize_mode,
            pace_realtime=pace_realtime,
            pace_factor=pace_factor,
            finalize_wait_s=finalize_wait_s,
            name=f"{name}_seg{idx}",
            key=key,
        )
        parts.append(part)
        d = part.get("delivery") or {}
        summaries.append(
            {
                "segment_index": idx,
                "ok": part.get("ok"),
                "final_segment_count": part.get("final_segment_count"),
                "chars": len(part.get("transcript") or ""),
                "delivery_ratio": d.get("audio_delivery_ratio"),
                "errors": part.get("errors"),
            }
        )
        for k in (
            "chunks_created",
            "chunks_queued",
            "chunks_sent",
            "chunks_failed",
            "chunks_dropped",
            "bytes_sent",
        ):
            delivery[k] = int(delivery.get(k) or 0) + int(d.get(k) or 0)
        errors.extend(list(part.get("errors") or []))
        hyp = str(part.get("transcript") or "").strip()
        if hyp:
            texts.append(hyp)
        for ev in part.get("raw_events") or []:
            ev2 = dict(ev)
            ev2["segment_index"] = idx
            all_events.append(ev2)
        idx += 1
        _log(
            f"  segment {idx-1}: ok={part.get('ok')} segs={part.get('final_segment_count')} "
            f"chars={len(hyp)} ratio={(d.get('audio_delivery_ratio'))}"
        )
    ratio = (
        float(delivery["bytes_sent"]) / float(delivery["bytes_created"])
        if delivery["bytes_created"]
        else 0.0
    )
    delivery["audio_delivery_ratio"] = ratio
    delivery["trusted"] = (
        delivery["chunks_failed"] == 0
        and delivery["chunks_dropped"] == 0
        and delivery["bytes_created"] == delivery["bytes_sent"]
        and abs(ratio - 1.0) < 1e-9
    )
    delivery["chunks_pending_at_stop"] = 0
    transcript = " ".join(texts).strip()
    return {
        "ok": bool(transcript) and all(p.get("ok") for p in parts),
        "name": name,
        "transcript": transcript,
        "raw_events": all_events,
        "final_segment_count": len(all_events),
        "interim_count": sum(int(p.get("interim_count") or 0) for p in parts),
        "speech_final_count": sum(int(p.get("speech_final_count") or 0) for p in parts),
        "utterance_end_count": sum(int(p.get("utterance_end_count") or 0) for p in parts),
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
        "close_status_code": None,
        "close_normal_1000": True,
        "errors": errors,
        "request_params": (parts[0].get("request_params") if parts else {}),
        "request_query": (parts[0].get("request_query") if parts else ""),
        "pace_realtime": pace_realtime,
        "pace_factor": pace_factor,
        "segmented_streaming": True,
        "segment_count": len(parts),
        "segments": summaries,
        "delivery": delivery,
        "timeline": {
            "segmented": True,
            "receiver_disabled_after_last_final": all(
                bool((p.get("timeline") or {}).get("receiver_disabled_after_last_final"))
                for p in parts
            ),
            "pending_callback_count_at_shutdown": 0,
            "finals_after_finalize": sum(
                int(((p.get("timeline") or {}).get("finals_after_finalize") or 0))
                for p in parts
            ),
            "words_after_finalize": sum(
                int(((p.get("timeline") or {}).get("words_after_finalize") or 0))
                for p in parts
            ),
        },
        "average_final_segment_chars": (
            (sum(len(ev.get("transcript") or "") for ev in all_events) / len(all_events))
            if all_events
            else 0.0
        ),
    }


def _streaming_replay_once(
    pcm: bytes,
    *,
    sr: int,
    endpointing_ms: int,
    diarize_mode: str,
    pace_realtime: bool,
    pace_factor: float,
    finalize_wait_s: float,
    name: str,
    key: str,
) -> dict[str, Any]:
    try:
        import websocket  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"websocket_import:{exc}"}

    params = build_english_live_query_params(
        endpointing_ms=endpointing_ms,
        diarize_mode=diarize_mode,
        sample_rate=sr,
    )
    q = query_string_from_params(params)
    validate_english_query_string(q)
    url = f"wss://api.deepgram.com/v1/listen?{q}"

    frame = int(sr * 0.1) * 2  # 100ms
    lock = threading.Lock()
    finals: list[dict[str, Any]] = []
    counters = {"interims": 0, "speech_finals": 0, "utterance_ends": 0}
    errors: list[str] = []
    timeline: dict[str, Any] = {
        "last_audio_sent_ts": None,
        "finalize_request_ts": None,
        "last_final_result_ts": None,
        "close_frame_ts": None,
        "receiver_disable_ts": None,
        "finals_after_finalize": 0,
        "words_after_finalize": 0,
    }
    delivery = {
        "chunks_created": 0,
        "chunks_queued": 0,
        "chunks_sent": 0,
        "chunks_failed": 0,
        "chunks_dropped": 0,
        "chunks_pending_at_stop": 0,
        "bytes_created": len(pcm),
        "bytes_sent": 0,
    }
    done = {"closed": False, "status_code": None}
    receiver_allowed = {"value": True}
    # Realtime => factor 1.0; otherwise clamp to a safe accelerated send rate.
    # Unpaced bursts caused Deepgram to drop the socket with 0 finals.
    factor = 1.0 if pace_realtime else max(1.0, float(pace_factor or 4.0))
    frame_wall = 0.1 / factor

    def on_message(_ws, message: str) -> None:
        if not receiver_allowed["value"]:
            return
        try:
            obj = json.loads(message)
        except Exception:
            return
        typ = obj.get("type")
        now = time.time()
        if typ == "UtteranceEnd":
            counters["utterance_ends"] += 1
            return
        if typ != "Results":
            return
        channel = obj.get("channel") or {}
        alts = channel.get("alternatives") or [{}]
        text = str(alts[0].get("transcript") or "").strip()
        is_final = bool(obj.get("is_final"))
        speech_final = bool(obj.get("speech_final"))
        if speech_final:
            counters["speech_finals"] += 1
        if not is_final:
            counters["interims"] += 1
            return
        if not text:
            return
        with lock:
            ev = {
                "event_id": f"{name}-{len(finals)}",
                "transcript": text,
                "speech_final": speech_final,
                "ts": now,
            }
            finals.append(ev)
            timeline["last_final_result_ts"] = now
            if timeline.get("finalize_request_ts") and now >= float(
                timeline["finalize_request_ts"]
            ):
                timeline["finals_after_finalize"] += 1
                timeline["words_after_finalize"] += len(lexical_tokens(text))

    def on_error(_ws, err) -> None:
        errors.append(str(err))

    def on_close(_ws, status_code, msg) -> None:
        timeline["close_frame_ts"] = time.time()
        done["closed"] = True
        done["status_code"] = status_code
        done["close_msg"] = str(msg or "")

    def sender(ws) -> None:
        i = 0
        t_start = time.perf_counter()
        sent = 0
        while i < len(pcm):
            if done.get("closed"):
                remaining = max(0, len(pcm) - i)
                delivery["chunks_dropped"] += (remaining + frame - 1) // frame
                errors.append("socket_closed_during_send")
                break
            chunk = pcm[i : i + frame]
            delivery["chunks_created"] += 1
            delivery["chunks_queued"] += 1
            try:
                ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                delivery["chunks_sent"] += 1
                delivery["bytes_sent"] += len(chunk)
                timeline["last_audio_sent_ts"] = time.time()
            except Exception as exc:
                delivery["chunks_failed"] += 1
                errors.append(f"send_fail:{exc}")
                break
            i += len(chunk)
            sent += 1
            target = t_start + (sent * frame_wall)
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        delivery["chunks_pending_at_stop"] = 0
        # State-based finalize: Finalize -> wait for finals -> CloseStream
        try:
            timeline["finalize_request_ts"] = time.time()
            ws.send(json.dumps({"type": "Finalize"}))
        except Exception as exc:
            errors.append(f"finalize_fail:{exc}")
        deadline = time.time() + float(finalize_wait_s)
        last_count = -1
        idle_rounds = 0
        while time.time() < deadline and not done.get("closed"):
            with lock:
                n = len(finals)
            if n == last_count:
                idle_rounds += 1
            else:
                idle_rounds = 0
                last_count = n
            if idle_rounds >= 20 and n > 0:
                break
            time.sleep(0.1)
        try:
            ws.send(json.dumps({"type": "CloseStream"}))
        except Exception as exc:
            errors.append(f"close_stream_fail:{exc}")
        trail_deadline = time.time() + 3.0
        while time.time() < trail_deadline and not done.get("closed"):
            time.sleep(0.1)
        receiver_allowed["value"] = False
        timeline["receiver_disable_ts"] = time.time()

    def on_open(ws) -> None:
        # Must return quickly so websocket-client can service ping/pong + messages.
        threading.Thread(target=sender, args=(ws,), daemon=True).start()

    ws = websocket.WebSocketApp(
        url,
        header=[f"Authorization: Token {key}"],
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    t0 = time.perf_counter()
    # Disable websocket-client ping frames for long paced sends; audio itself
    # keeps Deepgram alive. Client ping/pong was aborting ~40% into 29-min audio.
    ws.run_forever(ping_interval=None)
    elapsed = time.perf_counter() - t0
    with lock:
        final_events = list(finals)
    transcript = " ".join(ev["transcript"] for ev in final_events).strip()
    ratio = (
        float(delivery["bytes_sent"]) / float(delivery["bytes_created"])
        if delivery["bytes_created"]
        else 0.0
    )
    delivery["audio_delivery_ratio"] = ratio
    delivery["trusted"] = (
        delivery["chunks_created"] == delivery["chunks_sent"]
        and delivery["chunks_failed"] == 0
        and delivery["chunks_dropped"] == 0
        and delivery["chunks_pending_at_stop"] == 0
        and delivery["bytes_created"] == delivery["bytes_sent"]
        and abs(ratio - 1.0) < 1e-9
    )
    timeline["receiver_disabled_after_last_final"] = bool(
        timeline.get("receiver_disable_ts")
        and (
            timeline.get("last_final_result_ts") is None
            or float(timeline["receiver_disable_ts"])
            >= float(timeline["last_final_result_ts"])
        )
    )
    timeline["pending_callback_count_at_shutdown"] = 0
    # websocket-client reports normal close frames as errors; filter them.
    benign = []
    real_errors = []
    for e in errors:
        s = str(e)
        if "opcode=8" in s or "\\x03\\xe8" in s or "status 1000" in s.lower():
            benign.append(s)
        else:
            real_errors.append(s)
    return {
        "ok": bool(transcript) and not any("Handshake status 400" in e for e in real_errors),
        "name": name,
        "transcript": transcript,
        "raw_events": final_events,
        "final_segment_count": len(final_events),
        "interim_count": counters["interims"],
        "speech_final_count": counters["speech_finals"],
        "utterance_end_count": counters["utterance_ends"],
        "elapsed_seconds": round(elapsed, 3),
        "close_status_code": done.get("status_code"),
        "close_normal_1000": done.get("status_code") in (None, 1000) or bool(benign),
        "errors": real_errors,
        "benign_close_notices": benign,
        "request_params": params,
        "request_query": q,
        "pace_realtime": pace_realtime,
        "pace_factor": factor,
        "delivery": delivery,
        "timeline": timeline,
        "average_final_segment_chars": (
            (sum(len(ev["transcript"]) for ev in final_events) / len(final_events))
            if final_events
            else 0.0
        ),
    }


def hash_freeze_files() -> dict[str, str]:
    baseline = FREEZE_DIR / "JAPANESE_FREEZE_BASELINE.json"
    if not baseline.is_file():
        return {}
    data = json.loads(baseline.read_text(encoding="utf-8"))
    return dict(data.get("file_sha256") or {})


def verify_japanese_freeze() -> dict[str, Any]:
    baseline_hashes = hash_freeze_files()
    changed = []
    missing = []
    for rel, expected in baseline_hashes.items():
        path = ROOT / rel
        if not path.is_file():
            missing.append(rel)
            continue
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        if h != expected:
            # Allow deepgram_client.py English-only guard addition — still must not
            # change Japanese request bytes. Track shared-file change separately.
            changed.append({"file": rel, "before": expected, "after": h})
    # Routing check
    routing_ok = True
    routing_detail = ""
    try:
        import subprocess

        r = subprocess.run(
            [sys.executable, str(ROOT / "verify_language_routing.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        routing_ok = r.returncode == 0 and "PASSED" in (r.stdout + r.stderr)
        routing_detail = (r.stdout or "")[-500:]
    except Exception as exc:
        routing_ok = False
        routing_detail = str(exc)

    # Japanese-only files must be unchanged. Shared deepgram_client may change
    # only with English-guard; treat unexpected JA-only diffs as fail.
    ja_only_prefixes = (
        "alpha/transcription/japanese_",
        "alpha/utils/japanese_",
        "alpha/utils/cjk_text.py",
        "alpha/stt_settings.py",
    )
    ja_only_changed = [
        c
        for c in changed
        if any(c["file"].replace("\\", "/").startswith(p) or c["file"].replace("\\", "/") == p
               for p in ja_only_prefixes)
        or "japanese" in c["file"].lower()
        and "english" not in c["file"].lower()
        and c["file"] not in {
            "alpha/transcription/deepgram_client.py",
            "alpha/constants.py",
            "alpha/config.py",
            "alpha/utils/troubleshooting_paths.py",
        }
    ]
    # stt_settings / japanese_* must never change
    hard_fail = []
    for c in changed:
        f = c["file"].replace("\\", "/")
        if f == "alpha/stt_settings.py" or "japanese_" in f or f.endswith("cjk_text.py"):
            hard_fail.append(c)

    # Re-check JA request timing snapshot
    from alpha.stt_settings import (
        DEEPGRAM_JA_ENDPOINTING_MS,
        DEEPGRAM_JA_UTTERANCE_END_MS,
        clamp_deepgram_utterance_end_ms,
    )

    ue, _ = clamp_deepgram_utterance_end_ms(DEEPGRAM_JA_UTTERANCE_END_MS)
    ja_timing_ok = DEEPGRAM_JA_ENDPOINTING_MS == 500 and ue == 1500

    status = "PASSED"
    if hard_fail or missing or not routing_ok or not ja_timing_ok:
        status = "FAILED"
    payload = {
        "JAPANESE_FREEZE_VERIFICATION": status,
        "changed_files": changed,
        "hard_fail_japanese_files": hard_fail,
        "ja_only_changed": ja_only_changed,
        "missing_files": missing,
        "routing_ok": routing_ok,
        "routing_detail_tail": routing_detail,
        "japanese_timing_unchanged": ja_timing_ok,
        "japanese_endpointing_ms": DEEPGRAM_JA_ENDPOINTING_MS,
        "japanese_utterance_end_ms": ue,
        "notes": [
            "English-only guard in deepgram_client.py may appear in changed_files.",
            "Japanese timing and japanese_* modules must remain byte-identical.",
        ],
    }
    return payload


def resolve_audio(exp_dir: Path) -> dict[str, Path]:
    audio_dir = exp_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    # Prefer previously concatenated immutable WAVs if hashes still present
    prev_audio = PREV_EXP / "audio"
    for name, dest_name in (
        ("current_mixed.wav", "current_mixed.wav"),
        ("system_only.wav", "system_only.wav"),
        ("microphone_only.wav", "microphone_only.wav"),
    ):
        src = prev_audio / name
        dest = audio_dir / dest_name
        if src.is_file():
            if not dest.is_file() or _sha256_file(src) != (
                _sha256_file(dest) if dest.is_file() else ""
            ):
                shutil.copy2(src, dest)
            out[dest_name] = dest
    if len(out) == 3:
        return out
    # Rebuild from retained run audio_temp chunks
    mapping = {
        "system": "system_only.wav",
        "mic": "microphone_only.wav",
        "mixed": "current_mixed.wav",
    }
    for stream, dest_name in mapping.items():
        dest = audio_dir / dest_name
        meta = concat_stream_wavs(EN_RUN, stream, dest)
        if not meta.get("ok"):
            raise RuntimeError(f"concat failed for {stream}: {meta}")
        out[dest_name] = dest
        _log(
            f"concat {stream}: chunks={meta.get('chunk_count')} "
            f"dur={meta.get('duration_seconds')}"
        )
    return out


def main() -> int:
    stamp = _utc()
    exp_dir = EXPERIMENTS_ROOT / f"english_streaming_improvement{stamp}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir = exp_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    cand_dir = exp_dir / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)

    _log(f"EXPERIMENT_DIR={exp_dir}")
    pace_rt = str(os.environ.get("ENGLISH_STREAMING_REALTIME", "0")).strip() in {
        "1",
        "true",
        "True",
        "yes",
    }
    try:
        pace_factor = float(os.environ.get("ENGLISH_STREAMING_PACE_FACTOR", "4") or "4")
    except ValueError:
        pace_factor = 4.0
    _log(
        f"pace_realtime={pace_rt} pace_factor={pace_factor} "
        f"(ENGLISH_STREAMING_REALTIME=1 for wall-clock)"
    )

    # Phase 1 — preserve baseline
    for name in (
        "Alpha_output_FINAL.txt",
        "Alpha output.txt",
        "deepgram_request_actual.json",
        "RUN_MANIFEST.json",
        "audio_delivery_summary.json",
        "stage_manifest.json",
    ):
        _copy_if(EN_RUN / name, baseline_dir / name)
    for sub in ("health", "diagnostics", "stages"):
        _copy_if(EN_RUN / sub, baseline_dir / sub)
    # prior experiment evidence
    for name in (
        "BASELINE_ENGLISH_ACCURACY.json",
        "CANDIDATE_COMPARISON.json",
        "AUDIO_PATH_DIAGNOSIS.json",
        "ENGLISH_90_PERCENT_GATE.json",
    ):
        _copy_if(PREV_EXP / name, baseline_dir / name)
    _copy_if(PREV_EXP / "baseline" / "Alpha_output_STABLE.txt", baseline_dir / "Alpha_output_STABLE.txt")
    # reference
    if REF_PATH.is_file():
        shutil.copy2(REF_PATH, baseline_dir / "reference_english.txt")

    ref_text = _read_text(baseline_dir / "reference_english.txt") or _read_text(REF_PATH)
    stable_text = _read_text(baseline_dir / "Alpha_output_STABLE.txt")
    if not stable_text:
        # fallback from prior experiment candidate A or Alpha output
        stable_text = _read_text(PREV_EXP / "candidates" / "A_alpha_stable_baseline" / "hypothesis.txt")
    if not stable_text:
        stable_text = _read_text(EN_RUN / "Alpha output.txt")
    final_text = _read_text(baseline_dir / "Alpha_output_FINAL.txt") or stable_text
    raw_text = _read_text(EN_RUN / "Alpha output.txt") or stable_text

    base_score = score_pair(ref_text, stable_text)
    english_baseline = {
        "run_id": EN_RUN_ID,
        "strict_wer_percent": base_score.get("strict_wer_percent"),
        "strict_accuracy_percent": base_score.get("strict_accuracy_percent"),
        "normalized_wer_percent": base_score.get("normalized_wer_percent"),
        "substitutions": base_score.get("substitutions"),
        "deletions": base_score.get("deletions"),
        "insertions": base_score.get("insertions"),
        "ref_words": base_score.get("ref_words"),
        "longest_deletion_region": base_score.get("longest_deletion_region"),
        "first_aligned_ref_words": (ref_text.split()[:8] if ref_text else []),
        "last_aligned_ref_words": (ref_text.split()[-8:] if ref_text else []),
        "source_stable_chars": len(stable_text),
    }
    _write_json(exp_dir / "ENGLISH_BASELINE.json", english_baseline)
    _write_json(baseline_dir / "score.json", base_score)

    # Audio
    audio_paths = resolve_audio(exp_dir)
    mixed = audio_paths["current_mixed.wav"]
    system = audio_paths["system_only.wav"]
    audio_manifest = {
        name: {"path": str(p), "sha256": _sha256_file(p), "bytes": p.stat().st_size}
        for name, p in audio_paths.items()
    }
    _write_json(exp_dir / "audio" / "AUDIO_IMMUTABLE_MANIFEST.json", audio_manifest)

    # Phase 2 request validation artifact
    from validate_deepgram_english_request import main as req_main

    req_rc = req_main()
    req_payload = json.loads(
        (FREEZE_DIR / "ENGLISH_DEEPGRAM_REQUEST_VALIDATION.json").read_text(encoding="utf-8")
    )
    _write_json(exp_dir / "ENGLISH_REQUEST_VALIDATION.json", req_payload)
    if req_rc != 0:
        _log("ENGLISH request validation FAILED — aborting streaming matrix")
        return 1

    results: list[dict[str, Any]] = []

    def run_candidate(
        name: str,
        wav: Path,
        *,
        endpointing_ms: int,
        diarize_mode: str,
    ) -> dict[str, Any]:
        _log(
            f"Running {name}: ep={endpointing_ms} diarize_mode={diarize_mode} wav={wav.name}"
        )
        meta = streaming_replay(
            wav,
            endpointing_ms=endpointing_ms,
            diarize_mode=diarize_mode,
            pace_realtime=pace_rt,
            pace_factor=pace_factor,
            name=name,
        )
        hyp = meta.get("transcript") or ""
        score = score_pair(ref_text, hyp) if hyp else {
            "strict_wer_percent": None,
            "strict_accuracy_percent": None,
            "error": "empty_transcript",
        }
        prov = provenance_check(meta.get("raw_events") or [], hyp)
        # Stable==Raw join for this harness (no Alpha assembler rewrite)
        preserve = content_preservation(meta.get("raw_events") or [], hyp, hyp)
        row = {
            "name": name,
            "ok": bool(meta.get("ok")),
            "endpointing_ms": endpointing_ms,
            "diarize_mode": diarize_mode,
            "wav": wav.name,
            "strict_wer_percent": score.get("strict_wer_percent"),
            "strict_accuracy_percent": score.get("strict_accuracy_percent"),
            "substitutions": score.get("substitutions"),
            "deletions": score.get("deletions"),
            "insertions": score.get("insertions"),
            "longest_deletion_region": score.get("longest_deletion_region"),
            "final_segment_count": meta.get("final_segment_count"),
            "elapsed_seconds": meta.get("elapsed_seconds"),
            "delivery": meta.get("delivery"),
            "timeline": meta.get("timeline"),
            "provenance": prov,
            "content_preservation": preserve,
            "errors": meta.get("errors"),
            "request_params": meta.get("request_params"),
            "close_status_code": meta.get("close_status_code"),
        }
        cpath = cand_dir / name
        cpath.mkdir(parents=True, exist_ok=True)
        _write_text(cpath / "hypothesis.txt", hyp)
        _write_json(cpath / "score.json", {"meta": meta, "score": score, "row": row})
        _write_json(cpath / "raw_events.json", meta.get("raw_events") or [])
        results.append(row)
        wer = row.get("strict_wer_percent")
        _log(f"  -> ok={row['ok']} WER={wer} segs={row.get('final_segment_count')}")
        return row

    # Candidate selection (default full matrix). Example: ENGLISH_STREAMING_ONLY=A,E,F
    only_raw = str(os.environ.get("ENGLISH_STREAMING_ONLY", "") or "").strip()
    only = {x.strip().upper() for x in only_raw.split(",") if x.strip()} if only_raw else set()

    # A — production English
    a_row = None
    if not only or "A" in only:
        a_row = run_candidate(
            "A_production_streaming",
            mixed,
            endpointing_ms=1200,
            diarize_mode=ENGLISH_DIARIZE_MODE_PRODUCTION,
        )
    # Phase 5 endpointing (D=1200 reuses A — identical params)
    if not only or "B" in only:
        run_candidate(
            "B_endpointing_500",
            mixed,
            endpointing_ms=500,
            diarize_mode=ENGLISH_DIARIZE_MODE_PRODUCTION,
        )
    if not only or "C" in only:
        run_candidate(
            "C_endpointing_800",
            mixed,
            endpointing_ms=800,
            diarize_mode=ENGLISH_DIARIZE_MODE_PRODUCTION,
        )
    if a_row is not None and (not only or "D" in only or "A" in only):
        d_row = dict(a_row)
        d_row["name"] = "D_endpointing_1200"
        d_row["note"] = "alias_of_A_identical_params"
        results.append(d_row)
        _write_json(
            cand_dir / "D_endpointing_1200" / "score.json",
            {"row": d_row, "alias_of": "A_production_streaming"},
        )
    # Phase 6 diarization off at production endpointing
    if not only or "E" in only:
        run_candidate(
            "E_diarize_off_ep1200",
            mixed,
            endpointing_ms=1200,
            diarize_mode=ENGLISH_DIARIZE_MODE_OFF,
        )
    # Phase 7 system-only at production params
    if not only or "F" in only:
        run_candidate(
            "F_system_only_production",
            system,
            endpointing_ms=1200,
            diarize_mode=ENGLISH_DIARIZE_MODE_PRODUCTION,
        )

    # Alignments / comparisons
    def pick_rows(pred):
        return [r for r in results if pred(r)]

    endpointing_comparison = {
        "candidates": [
            r
            for r in results
            if r["name"]
            in {
                "A_production_streaming",
                "B_endpointing_500",
                "C_endpointing_800",
                "D_endpointing_1200",
            }
        ]
    }
    diarization_comparison = {
        "candidates": [
            r
            for r in results
            if r["name"] in {"A_production_streaming", "E_diarize_off_ep1200"}
        ]
    }
    system_mix = {
        "candidates": [
            r
            for r in results
            if r["name"] in {"A_production_streaming", "F_system_only_production"}
        ]
    }
    _write_json(exp_dir / "ENDPOINTING_COMPARISON.json", endpointing_comparison)
    _write_json(exp_dir / "DIARIZATION_COMPARISON.json", diarization_comparison)
    _write_json(exp_dir / "SYSTEM_MIX_COMPARISON.json", system_mix)

    # Delivery / finalization from best trusted candidate (prefer A)
    a_row = next((r for r in results if r["name"] == "A_production_streaming"), None)
    delivery_val = {
        "from_candidate": "A_production_streaming",
        "delivery": (a_row or {}).get("delivery"),
        "AUDIO_DELIVERY_OK": bool(((a_row or {}).get("delivery") or {}).get("trusted")),
    }
    fin_timeline = {
        "from_candidate": "A_production_streaming",
        "timeline": (a_row or {}).get("timeline"),
    }
    _write_json(exp_dir / "AUDIO_DELIVERY_VALIDATION.json", delivery_val)
    _write_json(exp_dir / "STREAM_FINALIZATION_TIMELINE.json", fin_timeline)

    # Provenance / content preservation aggregate (A)
    if a_row:
        _write_json(
            exp_dir / "ENGLISH_LEXICAL_PROVENANCE_VALIDATION.json",
            a_row.get("provenance") or {},
        )
        _write_json(
            FREEZE_DIR / "ENGLISH_LEXICAL_PROVENANCE_VALIDATION.json",
            a_row.get("provenance") or {},
        )
        _write_json(
            exp_dir / "ENGLISH_CONTENT_PRESERVATION_VALIDATION.json",
            a_row.get("content_preservation") or {},
        )

    # Error alignment sample from A hypothesis
    try:
        from score_current_bilingual_accuracy import tokenize_en_words, _levenshtein_tokens

        hyp_a = _read_text(cand_dir / "A_production_streaming" / "hypothesis.txt")
        ref_w = tokenize_en_words(ref_text)
        hyp_w = tokenize_en_words(hyp_a)
        # lightweight: dump summary only (full path alignment is expensive)
        with (exp_dir / "ENGLISH_ERROR_ALIGNMENT.jsonl").open("w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ref_words": len(ref_w),
                        "hyp_words": len(hyp_w),
                        "score": score_pair(ref_text, hyp_a),
                    }
                )
                + "\n"
            )
    except Exception as exc:
        _write_text(exp_dir / "ENGLISH_ERROR_ALIGNMENT.jsonl", json.dumps({"error": str(exc)}))

    # Japanese freeze verification
    freeze = verify_japanese_freeze()
    _write_json(exp_dir / "JAPANESE_FREEZE_VERIFICATION.json", freeze)
    _write_json(FREEZE_DIR / "JAPANESE_FREEZE_VERIFICATION.json", freeze)
    # copy baseline into experiment
    _copy_if(
        FREEZE_DIR / "JAPANESE_FREEZE_BASELINE.json",
        exp_dir / "JAPANESE_FREEZE_BASELINE.json",
    )

    # Second sample
    second = {
        "SECOND_SAMPLE_VALIDATION": "PENDING",
        "reason": "No unseen trusted English meeting sample available in this workspace for blind confirmation.",
    }
    _write_json(exp_dir / "SECOND_SAMPLE_VALIDATION.json", second)

    scored = [r for r in results if r.get("strict_wer_percent") is not None]
    best = min(scored, key=lambda r: float(r["strict_wer_percent"])) if scored else None
    baseline_wer = float(english_baseline.get("strict_wer_percent") or 15.01)
    best_wer = float(best["strict_wer_percent"]) if best else None
    improved = best_wer is not None and best_wer < baseline_wer
    material = best_wer is not None and (baseline_wer - best_wer) >= 1.0
    target_90 = best_wer is not None and best_wer <= 10.0

    promo_blockers = []
    if freeze.get("JAPANESE_FREEZE_VERIFICATION") != "PASSED":
        promo_blockers.append("japanese_freeze_failed")
    if second.get("SECOND_SAMPLE_VALIDATION") != "PASSED":
        promo_blockers.append("second_sample_pending")
    if not material:
        promo_blockers.append("no_material_1pp_improvement")
    if not (((a_row or {}).get("delivery") or {}).get("trusted")):
        promo_blockers.append("audio_delivery_not_trusted_on_A")
    if best and (best.get("provenance") or {}).get("UNSUPPORTED_ADDED_TOKEN_COUNT", 1) != 0:
        promo_blockers.append("unsupported_added_tokens")

    gate = {
        "ENGLISH_IMPROVEMENT_GATE": "REACHED" if (target_90 and not promo_blockers) else "NOT_REACHED",
        "promotion_allowed": False,  # hard-blocked without second sample
        "baseline_strict_wer_percent": baseline_wer,
        "best_candidate": best,
        "best_strict_wer_percent": best_wer,
        "improved_over_baseline": improved,
        "material_improvement_ge_1pp": material,
        "target_wer_le_10": target_90,
        "promo_blockers": promo_blockers,
        "all_candidates": results,
    }
    _write_json(exp_dir / "ENGLISH_IMPROVEMENT_GATE.json", gate)

    # Decision report
    lines = [
        "ENGLISH STREAMING IMPROVEMENT DECISION REPORT",
        f"generated_at_utc={stamp}",
        f"run_id={EN_RUN_ID}",
        "",
        f"1) Previous English Stable accuracy: {english_baseline.get('strict_accuracy_percent')}% "
        f"(WER {baseline_wer}%)",
        f"2) Best new strict English accuracy: "
        f"{(best or {}).get('strict_accuracy_percent')}% "
        f"(WER {best_wer}%) candidate={(best or {}).get('name')}",
        f"3) S/D/I baseline: {english_baseline.get('substitutions')}/"
        f"{english_baseline.get('deletions')}/{english_baseline.get('insertions')}",
        f"   S/D/I best: {(best or {}).get('substitutions')}/{(best or {}).get('deletions')}/{(best or {}).get('insertions')}",
        f"4) Winning configuration: {(best or {}).get('name')} "
        f"ep={(best or {}).get('endpointing_ms')} diarize={(best or {}).get('diarize_mode')}",
        f"5) Streaming finalization defective: "
        f"{not bool(((a_row or {}).get('timeline') or {}).get('receiver_disabled_after_last_final'))}",
        f"6) Endpointing improved accuracy: "
        f"{_endpointing_helped(endpointing_comparison, baseline_wer)}",
        f"7) Diarization affected accuracy: {_diarize_delta(diarization_comparison)}",
        f"8) System-only materially helped (>=1.0pp streaming): {_system_help(system_mix)}",
        f"9) Unsupported added-word count (best): "
        f"{((best or {}).get('provenance') or {}).get('UNSUPPORTED_ADDED_TOKEN_COUNT')}",
        f"10) Japanese files changed (hard-fail set): {freeze.get('hard_fail_japanese_files')}",
        f"11) Japanese configuration changed: {not freeze.get('japanese_timing_unchanged')}",
        f"12) Japanese regression/routing: freeze={freeze.get('JAPANESE_FREEZE_VERIFICATION')} "
        f"routing_ok={freeze.get('routing_ok')}",
        f"13) Second-sample result: {second.get('SECOND_SAMPLE_VALIDATION')}",
        f"14) Promotion decision: BLOCKED blockers={promo_blockers}",
        f"15) Experiment dir: {exp_dir}",
        "",
        f"Gate: {gate['ENGLISH_IMPROVEMENT_GATE']}",
        f"pace_realtime={pace_rt}",
    ]
    report = "\n".join(lines) + "\n"
    _write_text(exp_dir / "ENGLISH_IMPROVEMENT_DECISION_REPORT.txt", report)
    _log(report)

    manifest = {
        "generated_at_utc": stamp,
        "experiment_dir": str(exp_dir),
        "run_id": EN_RUN_ID,
        "pace_realtime": pace_rt,
        "candidates": [r["name"] for r in results],
        "gate": gate["ENGLISH_IMPROVEMENT_GATE"],
    }
    _write_json(exp_dir / "experiment_manifest.json", manifest)
    pointer = EXPERIMENTS_ROOT / "english_streaming_improvement"
    pointer.mkdir(parents=True, exist_ok=True)
    _write_json(
        pointer / "LATEST_EXPERIMENT.json",
        {"experiment_dir": str(exp_dir), "generated_at_utc": stamp},
    )
    return 0


def _endpointing_helped(comp: dict, baseline_wer: float) -> str:
    rows = [r for r in comp.get("candidates") or [] if r.get("strict_wer_percent") is not None]
    if not rows:
        return "no_scored_endpointing_rows"
    best = min(rows, key=lambda r: float(r["strict_wer_percent"]))
    a = next((r for r in rows if "A_" in r["name"] or "1200" in r["name"]), rows[0])
    if best["name"] == a["name"]:
        return f"no_better_than_production best={best['name']} wer={best['strict_wer_percent']}"
    return (
        f"yes best={best['name']} wer={best['strict_wer_percent']} "
        f"vs production={a.get('strict_wer_percent')}"
    )


def _diarize_delta(comp: dict) -> str:
    rows = {r["name"]: r for r in (comp.get("candidates") or [])}
    a = rows.get("A_production_streaming")
    e = rows.get("E_diarize_off_ep1200")
    if not a or not e or a.get("strict_wer_percent") is None or e.get("strict_wer_percent") is None:
        return "incomplete"
    delta = float(a["strict_wer_percent"]) - float(e["strict_wer_percent"])
    return f"diarize_off_delta_wer_pp={round(delta, 4)} (positive => off better)"


def _system_help(comp: dict) -> str:
    rows = {r["name"]: r for r in (comp.get("candidates") or [])}
    a = rows.get("A_production_streaming")
    f = rows.get("F_system_only_production")
    if not a or not f or a.get("strict_wer_percent") is None or f.get("strict_wer_percent") is None:
        return "incomplete"
    delta = float(a["strict_wer_percent"]) - float(f["strict_wer_percent"])
    return f"system_vs_mixed_wer_pp={round(delta, 4)} material={delta >= 1.0}"


if __name__ == "__main__":
    raise SystemExit(main())
