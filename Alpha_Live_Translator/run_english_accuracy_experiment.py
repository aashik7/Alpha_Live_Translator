#!/usr/bin/env python3
"""Evidence-driven English accuracy experiment toward 90%+ (measurement + same-audio A/B).

Does not modify Japanese recognition. Does not promote candidates.
Uses retained WAV from English run live-...057f111e when available.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import struct
import sys
import time
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EN_RUN = ROOT / "troubleshooting" / "runs" / "v3.3.5.5.8.5.26.5.3-20260724-171708"
EN_RUN_ID = "live-v3.3.5.5.8.5.26.5.3-20260724-171708-057f111e"
REF_PATH = (
    ROOT.parent / "Alpha_Benchmark_References" / "current_english_actual.txt"
)
EXPERIMENTS_ROOT = ROOT / "troubleshooting" / "experiments"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _copy_if(src: Path, dest: Path) -> bool:
    if not src.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.wav", "*.pcm", "*.raw"))
        return True
    shutil.copy2(src, dest)
    return True


def score_pair(ref_text: str, hyp_text: str) -> dict[str, Any]:
    """Word-level strict/normalized WER only (no full-document character CER)."""
    from score_current_bilingual_accuracy import (
        normalize_en_punct_insensitive_words,
        tokenize_en_words,
        _levenshtein_tokens,
    )

    ref_w = tokenize_en_words(ref_text)
    hyp_w = tokenize_en_words(hyp_text)
    strict = _levenshtein_tokens(ref_w, hyp_w)
    norm = _levenshtein_tokens(
        normalize_en_punct_insensitive_words(ref_text),
        normalize_en_punct_insensitive_words(hyp_text),
    )
    lead = 0
    for a, b in zip(ref_w, hyp_w):
        if a != b:
            break
        lead += 1
    trail = 0
    for a, b in zip(reversed(ref_w), reversed(hyp_w)):
        if a != b:
            break
        trail += 1
    return {
        "strict_wer_percent": strict.get("wer_percent"),
        "strict_accuracy_percent": strict.get("word_accuracy_percent"),
        "normalized_wer_percent": norm.get("wer_percent"),
        "normalized_accuracy_percent": norm.get("word_accuracy_percent"),
        "substitutions": strict.get("substitutions"),
        "deletions": strict.get("deletions"),
        "insertions": strict.get("insertions"),
        "reference_word_count": strict.get("reference_word_count"),
        "hypothesis_word_count": strict.get("hypothesis_word_count"),
        "leading_reference_coverage_words": lead,
        "trailing_reference_coverage_words": trail,
        "leading_missing_words": max(0, len(ref_w) - len(hyp_w)) if lead == 0 else 0,
        "trailing_missing_words": 0,
        "raw_score_block": strict,
        "normalized_score_block": norm,
    }


def _log(msg: str) -> None:
    print(msg, flush=True)


def content_loss_pct(a: str, b: str) -> float:
    from score_current_bilingual_accuracy import tokenize_en_words

    wa, wb = tokenize_en_words(a), tokenize_en_words(b)
    if not wa:
        return 0.0 if not wb else 100.0
    # Approximate loss as deletion rate treating a as reference
    from score_current_bilingual_accuracy import _levenshtein_tokens

    m = _levenshtein_tokens(wa, wb)
    return float(m.get("deletions", 0)) * 100.0 / max(1, len(wa))


def concat_stream_wavs(run_folder: Path, stream: str, out_wav: Path) -> dict[str, Any]:
    """Concatenate retained chunk WAVs for system/mic/mixed into one immutable WAV."""
    base = run_folder / "audio_temp" / f"{stream}_audio"
    if stream == "mic":
        base = run_folder / "audio_temp" / "mic_audio"
    elif stream == "system":
        base = run_folder / "audio_temp" / "system_audio"
    elif stream == "mixed":
        base = run_folder / "audio_temp" / "mixed_audio"
    files = sorted(base.glob(f"{stream}_*.wav")) if base.is_dir() else []
    if not files:
        # alternate naming
        files = sorted(base.glob("*.wav")) if base.is_dir() else []
    if not files:
        return {"ok": False, "reason": "no_chunks", "stream": stream}

    frames = bytearray()
    params = None
    for fp in files:
        with wave.open(str(fp), "rb") as wf:
            p = wf.getparams()
            if params is None:
                params = p
            elif (p.nchannels, p.sampwidth, p.framerate) != (
                params.nchannels,
                params.sampwidth,
                params.framerate,
            ):
                return {"ok": False, "reason": "param_mismatch", "file": str(fp)}
            frames.extend(wf.readframes(wf.getnframes()))
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    assert params is not None
    with wave.open(str(out_wav), "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        out.writeframes(bytes(frames))
    # Make read-only
    try:
        out_wav.chmod(0o444)
    except Exception:
        pass
    dur = len(frames) / float(params.nchannels * params.sampwidth * params.framerate)
    return {
        "ok": True,
        "stream": stream,
        "path": str(out_wav),
        "chunk_count": len(files),
        "sha256": _sha256_file(out_wav),
        "sample_rate": params.framerate,
        "channels": params.nchannels,
        "sampwidth": params.sampwidth,
        "encoding": "pcm_s16le",
        "duration_seconds": round(dur, 3),
        "bytes": out_wav.stat().st_size,
    }


def _pcm16_rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    n = len(pcm) // 2
    # sample subset for speed
    step = max(1, n // 200_000)
    acc = 0.0
    count = 0
    for i in range(0, n, step):
        (sample,) = struct.unpack_from("<h", pcm, i * 2)
        acc += float(sample) * float(sample)
        count += 1
    return math.sqrt(acc / max(1, count))


def _pcm16_clip_ratio(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    n = len(pcm) // 2
    step = max(1, n // 200_000)
    clips = 0
    count = 0
    for i in range(0, n, step):
        (sample,) = struct.unpack_from("<h", pcm, i * 2)
        if abs(sample) >= 32700:
            clips += 1
        count += 1
    return clips / max(1, count)


def _pcm16_silence_ratio(pcm: bytes, thresh: int = 80) -> float:
    if len(pcm) < 2:
        return 1.0
    n = len(pcm) // 2
    step = max(1, n // 200_000)
    silent = 0
    count = 0
    for i in range(0, n, step):
        (sample,) = struct.unpack_from("<h", pcm, i * 2)
        if abs(sample) < thresh:
            silent += 1
        count += 1
    return silent / max(1, count)


def correlate_streams(a_wav: Path, b_wav: Path, max_lag_samples: int = 16000) -> dict[str, Any]:
    """Estimate delay/correlation between two mono PCM16 WAVs (downsampled)."""
    def read_mono(path: Path) -> tuple[list[float], int]:
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        # downsample to ~100 Hz envelope for speed
        step = max(1, sr // 100)
        vals: list[float] = []
        n = len(raw) // 2
        for i in range(0, n, step):
            (sample,) = struct.unpack_from("<h", raw, i * 2)
            vals.append(float(sample))
        return vals, sr

    a, sr = read_mono(a_wav)
    b, _ = read_mono(b_wav)
    # truncate to shared length
    m = min(len(a), len(b))
    a, b = a[:m], b[:m]
    if m < 100:
        return {"ok": False, "reason": "too_short"}
    # normalize
    def norm(x: list[float]) -> list[float]:
        mean = sum(x) / len(x)
        y = [v - mean for v in x]
        denom = math.sqrt(sum(v * v for v in y) / len(y)) or 1.0
        return [v / denom for v in y]

    an, bn = norm(a), norm(b)
    best_corr = -2.0
    best_lag = 0
    # lag in downsampled units; convert later
    max_lag = min(max_lag_samples // max(1, sr // 100), m // 4)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            aa, bb = an[: m - lag], bn[lag:]
        else:
            aa, bb = an[-lag:], bn[: m + lag]
        if len(aa) < 50:
            continue
        corr = sum(x * y for x, y in zip(aa, bb)) / len(aa)
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    lag_seconds = best_lag / float(max(1, sr // 100))
    return {
        "ok": True,
        "best_correlation": round(best_corr, 4),
        "best_lag_seconds": round(lag_seconds, 4),
        "MICROPHONE_PLAYBACK_ECHO_DETECTED": bool(best_corr >= 0.35 and abs(lag_seconds) >= 0.02),
    }


def reference_quality_audit(ref_text: str, hyp_text: str) -> dict[str, Any]:
    from score_current_bilingual_accuracy import tokenize_en_words, _levenshtein_tokens

    ref_w = tokenize_en_words(ref_text)
    hyp_w = tokenize_en_words(hyp_text)
    # backtrace rough alignment for flagged windows
    # Use simple window scan for repeated words / broken grammar heuristics
    flags: list[dict[str, Any]] = []
    # repeated consecutive words in reference
    for i in range(1, len(ref_w)):
        if ref_w[i] == ref_w[i - 1] and len(ref_w[i]) > 2:
            flags.append(
                {
                    "reference_excerpt": " ".join(ref_w[max(0, i - 3) : i + 3]),
                    "alpha_excerpt": " ".join(hyp_w[max(0, i - 3) : i + 3]),
                    "approximate_position_words": i,
                    "reason": "repeated_word_in_reference",
                    "manual_audio_review_required": True,
                }
            )
    # numeral / contraction style diffs sampled from token mismatches
    ref_set = set(ref_w)
    hyp_set = set(hyp_w)
    contraction_pairs = {
        "dont": "don't",
        "doesnt": "doesn't",
        "cant": "can't",
        "wont": "won't",
        "im": "i'm",
        "its": "it's",
        "thats": "that's",
        "were": "we're",
        "youre": "you're",
    }
    for a, b in contraction_pairs.items():
        if (a in ref_set and b.replace("'", "") in hyp_set) or (
            b.replace("'", "") in ref_set and a in hyp_set
        ):
            flags.append(
                {
                    "reference_excerpt": a,
                    "alpha_excerpt": b,
                    "approximate_position_words": -1,
                    "reason": "contraction_or_apostrophe_variant",
                    "manual_audio_review_required": True,
                }
            )
    # possible acronyms / names: short ALLCAPS-like tokens after casefold loss — use raw lines
    for m in re.finditer(r"\b([A-Z]{2,6}|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", ref_text):
        token = m.group(1)
        if token.lower() not in hyp_text.casefold():
            flags.append(
                {
                    "reference_excerpt": token,
                    "alpha_excerpt": "(not found in Alpha hypothesis)",
                    "approximate_position_words": -1,
                    "reason": "possible_name_or_acronym_missing_in_alpha",
                    "manual_audio_review_required": True,
                }
            )
            if sum(1 for f in flags if f["reason"] == "possible_name_or_acronym_missing_in_alpha") >= 40:
                break
    # boundary mismatch
    if ref_w[:8] != hyp_w[:8]:
        flags.append(
            {
                "reference_excerpt": " ".join(ref_w[:12]),
                "alpha_excerpt": " ".join(hyp_w[:12]),
                "approximate_position_words": 0,
                "reason": "mismatched_opening_boundary",
                "manual_audio_review_required": True,
            }
        )
    if ref_w[-8:] != hyp_w[-8:]:
        flags.append(
            {
                "reference_excerpt": " ".join(ref_w[-12:]),
                "alpha_excerpt": " ".join(hyp_w[-12:]),
                "approximate_position_words": max(0, len(ref_w) - 12),
                "reason": "mismatched_ending_boundary",
                "manual_audio_review_required": True,
            }
        )
    # de-dupe by reason+excerpt
    seen = set()
    uniq = []
    for f in flags:
        key = (f["reason"], f["reference_excerpt"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return {
        "flag_count": len(uniq),
        "flags": uniq[:120],
        "note": "Trusted strict score continues using current reference until human audio review.",
        "auto_edit_applied": False,
    }


def deepgram_api_key() -> str:
    from alpha.config import DEEPGRAM_API_KEY

    key = (DEEPGRAM_API_KEY or "").strip()
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY missing")
    return key


def prerecorded_transcribe(
    wav_path: Path,
    *,
    language: str = "en",
    model: str = "nova-3",
    diarize: bool = False,
    keyterms: list[str] | None = None,
) -> dict[str, Any]:
    key = deepgram_api_key()
    params: dict[str, Any] = {
        "model": model,
        "language": language,
        "punctuate": "true",
        "smart_format": "true",
        "numerals": "true",
        "utterances": "true",
    }
    if diarize:
        params["diarize"] = "true"
        params["diarize_model"] = "latest"
    q = parse.urlencode(params, doseq=True)
    # keyterms as repeated query params if provided
    if keyterms:
        for t in keyterms:
            q += "&" + parse.urlencode({"keyterm": t})
    url = f"https://api.deepgram.com/v1/listen?{q}"
    data = wav_path.read_bytes()
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Token {key}",
            "Content-Type": "audio/wav",
        },
    )
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP_{exc.code}", "detail": detail[:2000]}
    elapsed = time.perf_counter() - t0
    alts = (
        (((body.get("results") or {}).get("channels") or [{}])[0].get("alternatives") or [{}])
    )
    transcript = str(alts[0].get("transcript") or "")
    return {
        "ok": True,
        "elapsed_seconds": round(elapsed, 3),
        "transcript": transcript,
        "request_params": params,
        "keyterm_count": len(keyterms or []),
        "diarize": diarize,
        "response_keys": sorted(body.keys()),
    }


def streaming_transcribe_realtime(
    wav_path: Path,
    *,
    endpointing_ms: int = 1200,
    utterance_end_ms: int = 1500,
    diarize: bool = True,
    keyterms: list[str] | None = None,
    pace_realtime: bool = True,
) -> dict[str, Any]:
    """Send WAV PCM to Deepgram live websocket; optional realtime pacing."""
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
    if sw != 2 or ch != 1:
        return {"ok": False, "error": f"unsupported_wav sr={sr} ch={ch} sw={sw}"}

    params = {
        "model": "nova-3",
        "language": "en",
        "encoding": "linear16",
        "sample_rate": str(sr),
        "channels": "1",
        "interim_results": "true",
        "punctuate": "true",
        "smart_format": "true",
        "numerals": "true",
        "endpointing": str(endpointing_ms),
        "utterance_end_ms": str(utterance_end_ms),
    }
    if diarize:
        params["diarize"] = "true"
        params["diarize_model"] = "latest"
    q = parse.urlencode(params)
    if keyterms:
        for t in keyterms:
            q += "&" + parse.urlencode({"keyterm": t})
    url = f"wss://api.deepgram.com/v1/listen?{q}"

    finals: list[str] = []
    errors: list[str] = []
    done = {"closed": False}

    def on_message(_ws, message: str) -> None:
        try:
            obj = json.loads(message)
        except Exception:
            return
        if obj.get("type") == "Results":
            is_final = bool(obj.get("is_final") or obj.get("speech_final"))
            channel = (obj.get("channel") or {})
            alts = channel.get("alternatives") or [{}]
            text = str(alts[0].get("transcript") or "").strip()
            if is_final and text:
                finals.append(text)

    def on_error(_ws, err) -> None:
        errors.append(str(err))

    def on_close(_ws, status_code, msg) -> None:
        done["closed"] = True
        done["status_code"] = status_code
        done["close_msg"] = str(msg or "")

    def on_open(ws) -> None:
        # 100ms frames
        frame = int(sr * 0.1) * 2
        i = 0
        t_start = time.perf_counter()
        sent = 0
        while i < len(pcm):
            chunk = pcm[i : i + frame]
            ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
            i += len(chunk)
            sent += 1
            if pace_realtime:
                target = t_start + (sent * 0.1)
                delay = target - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
        try:
            ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass

    ws = websocket.WebSocketApp(
        url,
        header=[f"Authorization: Token {key}"],
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    t0 = time.perf_counter()
    ws.run_forever(ping_interval=20, ping_timeout=10)
    elapsed = time.perf_counter() - t0
    transcript = " ".join(finals).strip()
    status = done.get("status_code")
    return {
        "ok": bool(transcript) and not errors,
        "transcript": transcript,
        "final_segment_count": len(finals),
        "elapsed_seconds": round(elapsed, 3),
        "close_status_code": status,
        "close_normal_1000": status in (1000, None),
        "errors": errors[:10],
        "request_params": params,
        "pace_realtime": pace_realtime,
    }


def build_error_alignment(ref_text: str, hyp_text: str, out_path: Path) -> dict[str, Any]:
    from score_current_bilingual_accuracy import tokenize_en_words

    ref_w = tokenize_en_words(ref_text)
    hyp_w = tokenize_en_words(hyp_text)
    # DP ops
    n, m = len(ref_w), len(hyp_w)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref_w[i - 1] == hyp_w[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    # backtrace
    i, j = n, m
    ops: list[dict[str, Any]] = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] and ref_w[i - 1] == hyp_w[j - 1]:
            i -= 1
            j -= 1
            continue
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            ops.append({"op": "sub", "ref": ref_w[i - 1], "hyp": hyp_w[j - 1], "ref_i": i - 1, "hyp_i": j - 1})
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append({"op": "del", "ref": ref_w[i - 1], "hyp": "", "ref_i": i - 1, "hyp_i": j})
            i -= 1
        else:
            ops.append({"op": "ins", "ref": "", "hyp": hyp_w[j - 1], "ref_i": i, "hyp_i": j - 1})
            j -= 1
    ops.reverse()
    with out_path.open("w", encoding="utf-8") as f:
        for row in ops:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    # longest deletion run
    longest = 0
    cur = 0
    for row in ops:
        if row["op"] == "del":
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return {"op_count": len(ops), "longest_deletion_region_words": longest}


def main() -> int:
    stamp = _utc()
    exp_dir = EXPERIMENTS_ROOT / f"english_accuracy_90{stamp}"
    baseline_dir = exp_dir / "baseline"
    audio_dir = exp_dir / "audio"
    cand_dir = exp_dir / "candidates"
    for d in (baseline_dir, audio_dir, cand_dir):
        d.mkdir(parents=True, exist_ok=True)

    if not EN_RUN.is_dir():
        print(f"ERROR: English run folder missing: {EN_RUN}")
        return 2
    if not REF_PATH.is_file():
        print(f"ERROR: English reference missing: {REF_PATH}")
        return 2

    asc = EN_RUN / "accuracy_stage_compare"
    # ---- Phase 1: preserve baseline ----
    copies = {
        "deepgram_request_actual.json": asc / "deepgram_request_actual.json",
        "raw_deepgram.txt": asc / "raw_deepgram.txt",
        "stable_transcript.txt": asc / "stable_transcript.txt",
        "final_alpha_output.txt": asc / "final_alpha_output.txt",
        "stage_manifest.json": asc / "stage_manifest.json",
        "audio_delivery_summary.json": asc / "audio_delivery_summary.json",
        "RUN_MANIFEST.json": EN_RUN / "RUN_MANIFEST.json",
        "current_english_actual.txt": REF_PATH,
    }
    for name, src in copies.items():
        _copy_if(src, baseline_dir / name)
    # Small evidence only (avoid multi-GB log trees stalling the experiment).
    for rel in (
        "artifacts/LIVE_RUN_STATUS.json",
        "health/LAST_HEALTH_SNAPSHOT.json",
        "logs/async_debug.log",
    ):
        _copy_if(EN_RUN / rel, baseline_dir / rel)
    _log("Phase1: baseline copied; scoring Stable WER...")

    ref_text = _read_text(REF_PATH)
    raw_text = _read_text(baseline_dir / "raw_deepgram.txt")
    stable_text = _read_text(baseline_dir / "stable_transcript.txt")
    final_text = _read_text(baseline_dir / "final_alpha_output.txt")
    if not final_text.strip():
        # Fall back to live Alpha output without overwriting original run files.
        alt = _read_text(EN_RUN / "transcripts" / "Alpha output.txt")
        if alt.strip():
            final_text = alt
            _write_text(baseline_dir / "final_alpha_output_FROM_LIVE_ALPHA_OUTPUT.txt", alt)

    baseline_stable = score_pair(ref_text, stable_text)
    baseline = {
        "run_id": EN_RUN_ID,
        "run_folder": str(EN_RUN),
        "reference_path": str(REF_PATH),
        "strict_wer_percent": baseline_stable["strict_wer_percent"],
        "strict_accuracy_percent": baseline_stable["strict_accuracy_percent"],
        "normalized_wer_percent": baseline_stable["normalized_wer_percent"],
        "normalized_accuracy_percent": baseline_stable["normalized_accuracy_percent"],
        "substitutions": baseline_stable["substitutions"],
        "deletions": baseline_stable["deletions"],
        "insertions": baseline_stable["insertions"],
        "reference_word_count": baseline_stable["reference_word_count"],
        "hypothesis_word_count": baseline_stable["hypothesis_word_count"],
        "leading_reference_coverage_words": baseline_stable["leading_reference_coverage_words"],
        "trailing_reference_coverage_words": baseline_stable["trailing_reference_coverage_words"],
        "raw_to_stable_content_loss_percent": content_loss_pct(raw_text, stable_text),
        "stable_to_final_content_loss_percent": content_loss_pct(stable_text, final_text),
        "raw_equals_stable": raw_text.strip() == stable_text.strip(),
        "audio_delivery_summary": json.loads(
            _read_text(baseline_dir / "audio_delivery_summary.json") or "{}"
        ),
        "stage_manifest_trusted_for_scoring": json.loads(
            _read_text(baseline_dir / "stage_manifest.json") or "{}"
        ).get("trusted_for_scoring"),
        "note": "Baseline preserved; originals not overwritten.",
    }
    _write_json(exp_dir / "BASELINE_ENGLISH_ACCURACY.json", baseline)

    # ---- Phase 2: reference audit ----
    audit = reference_quality_audit(ref_text, stable_text)
    _write_json(exp_dir / "REFERENCE_QUALITY_AUDIT.json", audit)
    lines = [
        "REFERENCE QUALITY AUDIT (no auto-edit)",
        f"flag_count={audit['flag_count']}",
        "",
    ]
    for f in audit["flags"][:60]:
        lines.append(
            f"- [{f['reason']}] pos={f['approximate_position_words']} "
            f"ref='{f['reference_excerpt'][:80]}' alpha='{str(f['alpha_excerpt'])[:80]}' "
            f"manual_review={f['manual_audio_review_required']}"
        )
    _write_text(exp_dir / "REFERENCE_QUALITY_AUDIT.txt", "\n".join(lines) + "\n")

    # ---- Phase 3: exact audio ----
    audio_meta = {"EXACT_AUDIO_AVAILABLE": False, "NEW_ENGLISH_CAPTURE_REQUIRED": True, "streams": {}}
    stream_paths: dict[str, Path] = {}
    for stream, out_name in (
        ("system", "system_only.wav"),
        ("mic", "microphone_only.wav"),
        ("mixed", "current_mixed.wav"),
    ):
        _log(f"Phase3: concatenating {stream} WAV chunks...")
        info = concat_stream_wavs(EN_RUN, stream, audio_dir / out_name)
        audio_meta["streams"][stream] = info
        if info.get("ok"):
            stream_paths[stream] = Path(info["path"])
            audio_meta["EXACT_AUDIO_AVAILABLE"] = True
            audio_meta["NEW_ENGLISH_CAPTURE_REQUIRED"] = False
            _log(f"  ok chunks={info.get('chunk_count')} dur={info.get('duration_seconds')}s")
        else:
            _log(f"  FAILED {info}")
    _write_json(audio_dir / "AUDIO_IMMUTABLE_MANIFEST.json", audio_meta)

    # ---- Phase 5: audio path diagnosis ----
    diagnosis: dict[str, Any] = {
        "benchmark_source": "system_audio_playback",
        "alpha_sends": "system_plus_microphone_mixture (mixed stream retained)",
        "streams_available": sorted(stream_paths.keys()),
    }
    pcm_stats = {}
    for name, path in stream_paths.items():
        with wave.open(str(path), "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
            sr = wf.getframerate()
        pcm_stats[name] = {
            "rms": round(_pcm16_rms(pcm), 2),
            "clip_ratio": round(_pcm16_clip_ratio(pcm), 6),
            "silence_ratio": round(_pcm16_silence_ratio(pcm), 4),
            "duration_seconds": round(len(pcm) / (2 * sr), 3),
            "sha256": _sha256_file(path),
        }
    diagnosis["pcm_stats"] = pcm_stats
    if "system" in stream_paths and "mic" in stream_paths:
        corr = correlate_streams(stream_paths["system"], stream_paths["mic"])
        diagnosis["system_vs_microphone"] = corr
        diagnosis["MICROPHONE_PLAYBACK_ECHO_DETECTED"] = bool(
            corr.get("MICROPHONE_PLAYBACK_ECHO_DETECTED")
        )
    else:
        diagnosis["MICROPHONE_PLAYBACK_ECHO_DETECTED"] = False
    if "system" in stream_paths and "mixed" in stream_paths:
        diagnosis["system_vs_mixed"] = correlate_streams(
            stream_paths["system"], stream_paths["mixed"]
        )
    _write_json(exp_dir / "AUDIO_PATH_DIAGNOSIS.json", diagnosis)

    # ---- Phase 6/7: same-audio Deepgram candidates ----
    candidates: list[dict[str, Any]] = []

    def add_candidate(name: str, transcript: str, meta: dict[str, Any]) -> None:
        score = score_pair(ref_text, transcript) if transcript.strip() else {
            "strict_wer_percent": None,
            "strict_accuracy_percent": None,
            "error": "empty_transcript",
        }
        row = {"name": name, "meta": meta, "score": score}
        candidates.append(row)
        cdir = cand_dir / name
        cdir.mkdir(parents=True, exist_ok=True)
        _write_text(cdir / "transcript.txt", transcript)
        _write_json(cdir / "score.json", row)

    # Test A — current Alpha stable (same audio session evidence)
    add_candidate(
        "A_alpha_stable_baseline",
        stable_text,
        {
            "source": "alpha_stable_transcript",
            "model": "nova-3",
            "language": "en",
            "endpointing": 1200,
            "utterance_end_ms": 1500,
            "keyterms": [],
            "note": "Alpha live Stable from exact run; not a fresh Deepgram call",
        },
    )

    mixed = stream_paths.get("mixed")
    system = stream_paths.get("system")
    if not mixed:
        print("ERROR: mixed audio unavailable; cannot continue same-audio Deepgram tests")
        _write_json(exp_dir / "ENGLISH_90_PERCENT_GATE.json", {
            "ENGLISH_90_PERCENT_GATE": "NOT_REACHED",
            "reason": "exact_mixed_audio_missing_for_deepgram_tests",
        })
        return 3

    # Test C — prerecorded mixed
    print("Running Test C: prerecorded mixed...")
    c_res = prerecorded_transcribe(mixed, diarize=False)
    add_candidate("C_prerecorded_mixed_no_diarize", c_res.get("transcript") or "", c_res)

    # Test D — system-only prerecorded (streaming parity proxy + explicit system stream)
    if system:
        print("Running Test D: prerecorded system-only...")
        d_res = prerecorded_transcribe(system, diarize=False)
        add_candidate("D_prerecorded_system_only", d_res.get("transcript") or "", d_res)

    # Test E — diarization A/B on prerecorded mixed (diarize flag)
    print("Running Test E: prerecorded mixed WITH diarize...")
    e_res = prerecorded_transcribe(mixed, diarize=True)
    add_candidate("E_prerecorded_mixed_diarize", e_res.get("transcript") or "", e_res)

    # Test G — keyterms: only fixed product glossary (not benchmark-derived)
    product_glossary = ["Nova-3", "Alpha"]
    print("Running Test G: prerecorded mixed + product glossary keyterms...")
    g0 = prerecorded_transcribe(mixed, diarize=False, keyterms=None)
    add_candidate("G0_prerecorded_no_keyterms", g0.get("transcript") or "", g0)
    g1 = prerecorded_transcribe(mixed, diarize=False, keyterms=product_glossary)
    add_candidate(
        "G1_prerecorded_product_keyterms",
        g1.get("transcript") or "",
        {**g1, "keyterms": product_glossary, "benchmark_derived_keyterms": False},
    )

    # Test B — direct streaming replay.
    # Default: accelerated send (same bytes) to finish in-session; set
    # ENGLISH_ACCURACY_REALTIME=1 for true wall-clock pacing.
    pace_rt = str(__import__("os").environ.get("ENGLISH_ACCURACY_REALTIME", "0")).strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
    }
    _log(
        f"Running Test B: direct Deepgram streaming replay "
        f"(pace_realtime={pace_rt})..."
    )
    b_res = streaming_transcribe_realtime(
        mixed,
        endpointing_ms=1200,
        utterance_end_ms=1500,
        diarize=True,
        pace_realtime=pace_rt,
    )
    add_candidate("B_streaming_direct_realtime", b_res.get("transcript") or "", b_res)

    # Test F — endpointing A/B. Full realtime matrix is expensive; run accelerated
    # streaming unless ENGLISH_ACCURACY_REALTIME=1.
    if b_res.get("ok"):
        for ep in (500, 800):
            _log(f"Running Test F: streaming endpointing={ep} (pace_realtime={pace_rt})...")
            f_res = streaming_transcribe_realtime(
                mixed,
                endpointing_ms=ep,
                utterance_end_ms=1500,
                diarize=True,
                pace_realtime=pace_rt,
            )
            add_candidate(f"F_streaming_endpointing_{ep}", f_res.get("transcript") or "", f_res)
        # 1200 covered by B
        add_candidate(
            "F_streaming_endpointing_1200",
            b_res.get("transcript") or "",
            {**b_res, "endpointing_ms": 1200, "reused_from": "B_streaming_direct_realtime"},
        )
    else:
        add_candidate(
            "F_streaming_endpointing_SKIPPED",
            "",
            {"ok": False, "reason": "skipped_because_streaming_B_failed", "b_errors": b_res.get("errors")},
        )

    # Alignment for baseline
    align_info = build_error_alignment(
        ref_text, stable_text, exp_dir / "ENGLISH_ERROR_ALIGNMENT.jsonl"
    )

    # Comparison table
    comparison_rows = []
    for c in candidates:
        sc = c.get("score") or {}
        comparison_rows.append(
            {
                "name": c["name"],
                "strict_wer_percent": sc.get("strict_wer_percent"),
                "strict_accuracy_percent": sc.get("strict_accuracy_percent"),
                "normalized_wer_percent": sc.get("normalized_wer_percent"),
                "deletions": sc.get("deletions"),
                "substitutions": sc.get("substitutions"),
                "insertions": sc.get("insertions"),
                "ok": (c.get("meta") or {}).get("ok", True),
            }
        )
    comparison = {
        "generated_at_utc": stamp,
        "baseline_strict_wer_percent": baseline["strict_wer_percent"],
        "candidates": comparison_rows,
        "audio": audio_meta,
        "microphone_echo": diagnosis.get("MICROPHONE_PLAYBACK_ECHO_DETECTED"),
    }
    _write_json(exp_dir / "CANDIDATE_COMPARISON.json", comparison)
    txt_lines = [
        "ENGLISH ACCURACY CANDIDATE COMPARISON",
        f"baseline_strict_wer={baseline['strict_wer_percent']}",
        "",
    ]
    for r in comparison_rows:
        txt_lines.append(
            f"{r['name']}: strict_WER={r['strict_wer_percent']} "
            f"acc={r['strict_accuracy_percent']} del={r['deletions']} "
            f"sub={r['substitutions']} ins={r['insertions']} ok={r['ok']}"
        )
    _write_text(exp_dir / "CANDIDATE_COMPARISON.txt", "\n".join(txt_lines) + "\n")

    # ---- Phase 8: root cause ----
    def wer_of(name: str) -> float | None:
        for r in comparison_rows:
            if r["name"] == name and r.get("strict_wer_percent") is not None:
                return float(r["strict_wer_percent"])
        return None

    alpha_wer = float(baseline["strict_wer_percent"] or 99)
    prerec_wer = wer_of("C_prerecorded_mixed_no_diarize")
    stream_wer = wer_of("B_streaming_direct_realtime")
    system_wer = wer_of("D_prerecorded_system_only")
    norm_wer = float(baseline.get("normalized_wer_percent") or 99)

    root = "model_audio_reference_combination"
    if prerec_wer is not None and prerec_wer <= 10 and stream_wer is not None and stream_wer > 10:
        root = "streaming_segmentation_or_finalization"
    elif stream_wer is not None and stream_wer <= 10 and alpha_wer > 10:
        root = "Alpha_capture_delivery_or_callback_pipeline"
    elif (
        system_wer is not None
        and prerec_wer is not None
        and system_wer + 1.0 < prerec_wer
    ):
        root = "microphone_echo_or_mixing_contamination"
    elif diagnosis.get("MICROPHONE_PLAYBACK_ECHO_DETECTED") and system_wer is not None and prerec_wer is not None:
        if system_wer < prerec_wer:
            root = "microphone_echo_or_mixing_contamination"
    elif alpha_wer > 10 and norm_wer <= 10:
        root = "scoring_normalization_and_reference_format"
    elif all(
        (wer_of(n) is None or float(wer_of(n)) > 12)
        for n in (
            "C_prerecorded_mixed_no_diarize",
            "B_streaming_direct_realtime",
            "D_prerecorded_system_only",
        )
    ):
        root = "model_audio_reference_combination"

    # ---- Phase 9: promotion gate ----
    scored_ok = [
        r
        for r in comparison_rows
        if r.get("strict_wer_percent") is not None and r.get("ok") is not False
    ]
    best = min(scored_ok, key=lambda r: float(r["strict_wer_percent"])) if scored_ok else None
    gate_reached = bool(
        best
        and float(best["strict_wer_percent"]) <= 10.0
        and float(best.get("strict_accuracy_percent") or 0) >= 90.0
        and baseline.get("audio_delivery_summary", {}).get("generated_during_runtime") is True
        and baseline.get("audio_delivery_summary", {}).get("generated_by_offline_repair") is not True
    )
    # Baseline delivery was offline-repaired → cannot promote from this package alone.
    if baseline.get("audio_delivery_summary", {}).get("generated_by_offline_repair"):
        gate_reached = False

    gate = {
        "ENGLISH_90_PERCENT_GATE": "REACHED" if gate_reached else "NOT_REACHED",
        "best_candidate": best,
        "best_verified_strict_wer_percent": None if not best else best["strict_wer_percent"],
        "strongest_root_cause": root,
        "baseline_strict_wer_percent": alpha_wer,
        "baseline_deletions": baseline["deletions"],
        "remaining_deletion_count_best": None if not best else best.get("deletions"),
        "audio_delivery_trusted": False,
        "offline_repair_used_on_baseline": True,
        "second_unseen_english_sample_confirmed": False,
        "promotion_allowed": False,
        "notes": [
            "Baseline audio_delivery_summary was offline-repaired; trusted live counters required before promotion.",
            "No candidate may be promoted on a single test.",
            "Japanese configuration untouched by this experiment runner.",
        ],
        "different_stt_provider_or_custom_model_justified": bool(
            root == "model_audio_reference_combination"
            and (best is None or float(best["strict_wer_percent"]) > 10)
        ),
        "existing_85_percent_acceptable_for_translation_beta": True,
    }
    _write_json(exp_dir / "ENGLISH_90_PERCENT_GATE.json", gate)

    report = f"""ENGLISH ACCURACY DECISION REPORT
generated_at_utc={stamp}
run_id={EN_RUN_ID}

1) Verified baseline strict Stable WER: {alpha_wer}%
   accuracy: {baseline['strict_accuracy_percent']}%
   S/D/I: {baseline['substitutions']}/{baseline['deletions']}/{baseline['insertions']}
   ref_words={baseline['reference_word_count']}

2) Exact audio available: {audio_meta['EXACT_AUDIO_AVAILABLE']}
   NEW_ENGLISH_CAPTURE_REQUIRED: {audio_meta['NEW_ENGLISH_CAPTURE_REQUIRED']}

3) Reference quality flags: {audit['flag_count']} (no auto-edit)

4) Audio path: mixed=system+mic; MICROPHONE_PLAYBACK_ECHO_DETECTED={diagnosis.get('MICROPHONE_PLAYBACK_ECHO_DETECTED')}

5) Streaming vs prerecorded:
   prerecorded_mixed_WER={prerec_wer}
   streaming_direct_WER={stream_wer}

6) System-only vs mixed:
   system_only_WER={system_wer}
   mixed_prerecorded_WER={prerec_wer}

7/8/9) See CANDIDATE_COMPARISON.json for diarization/endpointing/keyterm rows.

Root cause (evidence-selected): {root}
ENGLISH_90_PERCENT_GATE: {gate['ENGLISH_90_PERCENT_GATE']}
Best candidate: {best}
Promotion allowed: False

Alignment longest deletion region: {align_info.get('longest_deletion_region_words')}
"""
    _write_text(exp_dir / "ENGLISH_ACCURACY_DECISION_REPORT.txt", report)

    manifest = {
        "experiment_dir": str(exp_dir),
        "generated_at_utc": stamp,
        "run_id": EN_RUN_ID,
        "app_version": "3.3.5.5.8.5.26.5.3",
        "japanese_config_modified": False,
        "outputs": [
            "BASELINE_ENGLISH_ACCURACY.json",
            "REFERENCE_QUALITY_AUDIT.json",
            "REFERENCE_QUALITY_AUDIT.txt",
            "AUDIO_PATH_DIAGNOSIS.json",
            "CANDIDATE_COMPARISON.json",
            "CANDIDATE_COMPARISON.txt",
            "ENGLISH_ERROR_ALIGNMENT.jsonl",
            "ENGLISH_90_PERCENT_GATE.json",
            "ENGLISH_ACCURACY_DECISION_REPORT.txt",
        ],
    }
    _write_json(exp_dir / "experiment_manifest.json", manifest)
    # pointer for validators/packager
    _write_json(
        EXPERIMENTS_ROOT / "english_accuracy_90" / "LATEST_EXPERIMENT.json",
        {"experiment_dir": str(exp_dir), "generated_at_utc": stamp},
    )
    (EXPERIMENTS_ROOT / "english_accuracy_90").mkdir(parents=True, exist_ok=True)

    print(report)
    print(f"EXPERIMENT_DIR={exp_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
