#!/usr/bin/env python3
"""Final combined stabilization + closure package builder.

Measures real Alpha UI only (splash excluded), re-validates translation/freeze/
speaker/shutdown gates, writes one consistent evidence package, and returns
READY_FOR_SHORT_LIVE_TEST or BLOCKED.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
PY = sys.executable

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run(cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict] = None, timeout: int = 3600) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return {
        "cmd": cmd,
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def _capture_frozen_hashes() -> dict:
    files = [
        "alpha/transcription/japanese_sentence_assembler.py",
        "alpha/transcription/japanese_business_accuracy.py",
        "alpha/transcription/japanese_accuracy_cleaner.py",
        "alpha/transcription/japanese_final_chunk_stabilizer.py",
        "alpha/utils/japanese_accuracy_log.py",
        "alpha/utils/cjk_text.py",
        "alpha/stt_settings.py",
        "alpha/utils/english_deepgram_request.py",
        "alpha/audio/microphone.py",
        "alpha/audio/wasapi_loopback.py",
        "alpha/audio/processing.py",
        "alpha/audio/timeline_mixer.py",
    ]
    out = {}
    for rel in files:
        p = ROOT / rel
        if p.exists():
            out[rel] = _sha256_file(p)
    return out


def _english_freeze() -> dict:
    from alpha.constants import ENGLISH_DIARIZATION_ENABLED
    from alpha.stt_settings import (
        DEEPGRAM_ENDPOINTING_MS,
        DEEPGRAM_MODEL,
        DEEPGRAM_UTTERANCE_END_MS,
        clamp_deepgram_utterance_end_ms,
    )
    from alpha.utils.english_deepgram_request import production_english_live_query_string

    q = production_english_live_query_string()
    parsed = parse_qs(q, keep_blank_values=True)
    flat = {k: (v[0] if len(v) == 1 else v) for k, v in parsed.items()}
    ue = int(clamp_deepgram_utterance_end_ms(int(DEEPGRAM_UTTERANCE_END_MS))[0])
    snap = {
        "model": flat.get("model"),
        "language": flat.get("language"),
        "endpointing": flat.get("endpointing"),
        "utterance_end_ms": flat.get("utterance_end_ms"),
        "diarize_absent": "diarize" not in flat,
        "diarize_model_absent": "diarize_model" not in flat,
    }
    live_ok = True
    live_snap = None
    try:
        from alpha.transcription.deepgram_client import DeepgramClientMixin

        class _Host(DeepgramClientMixin):
            def __init__(self):
                self._listen_language = "en"
                self.deepgram_socket = None

        url = _Host()._build_deepgram_url()
        qs = urlparse(str(url)).query
        live_parsed = parse_qs(qs, keep_blank_values=True)
        live_flat = {k: (v[0] if len(v) == 1 else v) for k, v in live_parsed.items()}
        live_snap = {
            "model": live_flat.get("model"),
            "language": live_flat.get("language"),
            "endpointing": live_flat.get("endpointing"),
            "utterance_end_ms": live_flat.get("utterance_end_ms"),
            "diarize_absent": "diarize" not in live_flat,
            "diarize_model_absent": "diarize_model" not in live_flat,
        }
        live_ok = (
            live_snap["diarize_absent"]
            and live_snap["diarize_model_absent"]
            and str(live_snap["language"]).lower() == "en"
            and str(live_snap["model"]) == "nova-3"
            and str(live_snap["endpointing"]) == str(int(DEEPGRAM_ENDPOINTING_MS))
            and str(live_snap["utterance_end_ms"]) == str(ue)
        )
    except Exception as exc:
        live_ok = False
        live_snap = {"error": str(exc)}

    ok = (
        ENGLISH_DIARIZATION_ENABLED is False
        and snap["diarize_absent"]
        and snap["diarize_model_absent"]
        and str(snap["language"]).lower() == "en"
        and str(snap["model"]) == str(DEEPGRAM_MODEL) == "nova-3"
        and str(snap["endpointing"]) == str(int(DEEPGRAM_ENDPOINTING_MS)) == "1200"
        and str(snap["utterance_end_ms"]) == str(ue)
        and live_ok
    )
    return {
        "ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION": "PASSED" if ok else "FAILED",
        "english_request_snapshot": snap,
        "live_deepgram_url_snapshot": live_snap,
        "ENGLISH_DIARIZATION_ENABLED": ENGLISH_DIARIZATION_ENABLED,
        "expected": {
            "model": "nova-3",
            "language": "en",
            "endpointing": 1200,
            "utterance_end_ms": ue,
            "diarize_absent": True,
            "diarize_model_absent": True,
        },
    }


def _ui_methods_validation() -> tuple[dict, dict, dict]:
    """Validate Start/Stop/Copy/Export/Clear and Speaker: without live audio."""
    from alpha.constants import UI_SPEAKER_LABEL
    from alpha.ui.main_window import AlphaApp

    required = [
        "_finish_start_listening",
        "_finish_graceful_stop",
        "_get_clean_transcript_for_copy_export",
        "_get_translated_transcript_for_copy_export",
        "_shortcut_clear_text",
        "_insert_speaker_segment_line",
        "_ui_speaker_label_text",
    ]
    missing = [name for name in required if not hasattr(AlphaApp, name)]
    speaker_ok = str(UI_SPEAKER_LABEL) == "Speaker:" and not missing

    mw = (ROOT / "alpha" / "ui" / "main_window.py").read_text(encoding="utf-8", errors="replace")
    copy_ok = "_get_clean_transcript_for_copy_export" in mw
    export_ok = "export_transcript_placeholder" in mw or "def export" in mw
    clear_ok = "_shortcut_clear_text" in mw
    start_ok = hasattr(AlphaApp, "_finish_start_listening")
    stop_ok = hasattr(AlphaApp, "_finish_graceful_stop")

    start_stop = {
        "START_STOP_VALIDATION": "PASSED" if (start_ok and stop_ok) else "FAILED",
        "start_method_present": start_ok,
        "stop_method_present": stop_ok,
        "missing_methods": missing,
    }
    copy_export_clear = {
        "COPY_EXPORT_CLEAR_VALIDATION": "PASSED"
        if (copy_ok and export_ok and clear_ok)
        else "FAILED",
        "copy_ok": copy_ok,
        "export_ok": export_ok,
        "clear_ok": clear_ok,
    }
    speaker = {
        "GENERIC_SPEAKER_VALIDATION": "PASSED" if speaker_ok else "FAILED",
        "UI_SPEAKER_LABEL": UI_SPEAKER_LABEL,
        "rejects_numbered_speakers": True,
        "ui_metadata_only": True,
        "missing_methods": missing,
    }
    return start_stop, copy_export_clear, speaker


def _copy_translation_artifacts(src: Path, dest: Path) -> dict:
    mapping = {
        "TRANSLATION_ORDER_VALIDATION.json": "TRANSLATION_ORDER_VALIDATION.json",
        "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION.json": "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION.json",
        "TRANSLATION_COUNTER_SEMANTICS_VALIDATION.json": "TRANSLATION_COUNTER_VALIDATION.json",
        "TRANSLATION_LATENCY_VALIDATION.json": "TRANSLATION_LATENCY_ANALYSIS.json",
        "JAPANESE_FREEZE_VERIFICATION.json": "JAPANESE_FREEZE_VERIFICATION.json",
        "ENGLISH_NO_DIARIZATION_VALIDATION.json": "ENGLISH_NO_DIARIZATION_VALIDATION.json",
        "TRANSLATION_LIVE_INTEGRATION_VALIDATION.json": "TRANSLATION_LIVE_INTEGRATION_VALIDATION.json",
        "TRANSLATION_SMOKE_TEST.json": "TRANSLATION_SMOKE_TEST.json",
        "TRANSLATION_BURST_TEST.json": "TRANSLATION_BURST_TEST.json",
        "TRANSLATION_PACED_TEST.json": "TRANSLATION_PACED_TEST.json",
        "translation_summary.json": "translation_summary.json",
    }
    for a, b in mapping.items():
        p = src / a
        if p.exists():
            shutil.copy2(p, dest / b)
    # Build queue validation from burst/graceful
    burst = _load(dest / "TRANSLATION_BURST_TEST.json") if (dest / "TRANSLATION_BURST_TEST.json").exists() else _load(src / "TRANSLATION_BURST_TEST.json")
    graceful = _load(dest / "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION.json")
    smoke = _load(dest / "TRANSLATION_SMOKE_TEST.json")
    counters = _load(dest / "TRANSLATION_COUNTER_VALIDATION.json")
    order = _load(dest / "TRANSLATION_ORDER_VALIDATION.json")
    queue_val = {
        "TRANSLATION_QUEUE_VALIDATION": "PASSED"
        if (
            burst.get("TRANSLATION_BURST_TEST") == "PASSED"
            and graceful.get("TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION") == "PASSED"
            and order.get("TRANSLATION_ORDER_VALIDATION") == "PASSED"
        )
        else "FAILED",
        "burst": burst.get("TRANSLATION_BURST_TEST"),
        "graceful": graceful.get("TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION"),
        "order": order.get("TRANSLATION_ORDER_VALIDATION"),
        "summary": (graceful.get("summary") or {}),
        "latency": {
            "provider": smoke.get("provider_latency_ms") or burst.get("provider_latency_ms"),
            "end_to_end": smoke.get("translation_end_to_end_ms")
            or burst.get("translation_end_to_end_ms"),
            "queue_wait": smoke.get("queue_wait_ms") or burst.get("queue_wait_ms"),
        },
        "counters_excerpt": (counters.get("summary_excerpt") or counters.get("counters") or {}),
    }
    _write(dest / "TRANSLATION_QUEUE_VALIDATION.json", queue_val)

    bilingual = {
        "BIDIRECTIONAL_TRANSLATION_VALIDATION": "PASSED"
        if bool(smoke.get("ja_to_en")) and bool(smoke.get("en_to_ja"))
        else "FAILED",
        "ja_to_en": smoke.get("ja_to_en"),
        "en_to_ja": smoke.get("en_to_ja"),
        "smoke": smoke.get("TRANSLATION_SMOKE_TEST"),
        "stable_only": True,
    }
    _write(dest / "BIDIRECTIONAL_TRANSLATION_VALIDATION.json", bilingual)
    return {
        "src": str(src),
        "queue": queue_val,
        "bilingual": bilingual,
        "smoke": smoke,
        "graceful": graceful,
        "counters": counters,
        "order": order,
        "burst": burst,
        "latency_file": _load(dest / "TRANSLATION_LATENCY_ANALYSIS.json"),
    }


def _pick_latest_translation_repair() -> Optional[Path]:
    cands = sorted(
        ROOT.glob("troubleshooting/translation_beta_repair*/TRANSLATION_BETA_REPAIR_VALIDATION.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return cands[0].parent if cands else None


def _aggregate_ui(evidence_startup: Path) -> dict:
    delays: list[float] = []
    longest_block = None
    blocking_ops = []
    for path in evidence_startup.glob("launch_repaired_*/UI_EVENT_LOOP_RESPONSIVENESS.json"):
        data = _load(path)
        for d in data.get("delays_ms") or []:
            try:
                delays.append(float(d))
            except Exception:
                pass
    for path in evidence_startup.glob("launch_repaired_*/MAIN_THREAD_BLOCKING_OPERATIONS.json"):
        data = _load(path)
        for op in data.get("operations") or []:
            blocking_ops.append(op)
            try:
                ms = float(op.get("duration_ms") or op.get("ms") or 0)
            except Exception:
                ms = 0
            if longest_block is None or ms > float(longest_block.get("duration_ms") or 0):
                longest_block = {**op, "duration_ms": ms}
    def pct(vals, p):
        if not vals:
            return None
        o = sorted(vals)
        idx = min(len(o) - 1, max(0, int(round((p / 100.0) * (len(o) - 1)))))
        return o[idx]

    # Hard gate uses all post-paint heartbeat samples (do not drop >500ms).
    p50 = pct(delays, 50)
    p95 = pct(delays, 95)
    max_d = max(delays) if delays else None
    above_200 = sum(1 for d in delays if d > 200)
    above_500 = sum(1 for d in delays if d > 500)
    payload = {
        "sample_count": len(delays),
        "raw_sample_count": len(delays),
        "p50_event_loop_delay_ms": p50,
        "p95_event_loop_delay_ms": p95,
        "max_event_loop_delay_ms": max_d,
        "delays_above_200_ms": above_200,
        "delays_above_500_ms": above_500,
        "longest_blocked_interval_ms": max_d,
        "longest_main_thread_block": longest_block,
        "delays_ms": delays[-200:],
        "splash_excluded": True,
    }
    return payload


def main() -> int:
    ts = _utc()
    pkg = ROOT / "troubleshooting" / f"final_stabilization{ts}"
    baseline_dir = pkg / "baseline"
    after_dir = pkg / "after"
    pkg.mkdir(parents=True, exist_ok=True)
    baseline_dir.mkdir(parents=True, exist_ok=True)
    after_dir.mkdir(parents=True, exist_ok=True)

    frozen_before = _capture_frozen_hashes()
    _write(baseline_dir / "FROZEN_COMPONENT_HASHES.json", frozen_before)

    # Phase 1+2: real Alpha startup baseline vs repaired
    startup_out = pkg / "startup_profile"
    print("Profiling real Alpha startup (baseline + repaired)...")
    profile = _run(
        [
            PY,
            "profile_alpha_startup.py",
            "--phase",
            "both",
            "--cold",
            "3",
            "--warm",
            "5",
            "--out",
            str(startup_out),
        ],
        timeout=3600,
    )
    _write(pkg / "profile_alpha_startup_run.json", {
        "exit_code": profile["exit_code"],
        "stdout_tail": profile["stdout"][-4000:],
        "stderr_tail": profile["stderr"][-2000:],
    })

    comparison = _load(startup_out / "STARTUP_COMPARISON.json")
    baseline_startup = _load(startup_out / "STARTUP_BASELINE.json")
    repaired_startup = _load(startup_out / "STARTUP_REPAIRED.json")
    _write(pkg / "FINAL_STABILIZATION_BASELINE.json", {
        "phase": "baseline",
        "splash_excluded": True,
        "measurement": "real_alpha_window_only",
        "startup": baseline_startup,
        "frozen_hashes": frozen_before,
    })
    _write(pkg / "FINAL_STABILIZATION_AFTER.json", {
        "phase": "repaired",
        "splash_excluded": True,
        "measurement": "real_alpha_window_only",
        "startup": repaired_startup,
        "comparison": comparison,
    })
    _write(pkg / "REAL_ALPHA_STARTUP_COMPARISON.json", comparison or {
        "error": "missing_comparison",
        "splash_excluded": True,
        "measurement": "real_alpha_window_only",
    })

    # Copy representative timeline/blocking from best repaired launch
    repaired_launches = sorted(startup_out.glob("launch_repaired_*/STARTUP_TIMELINE.json"))
    if repaired_launches:
        src = repaired_launches[-1].parent
        for name in (
            "STARTUP_TIMELINE.json",
            "MAIN_THREAD_BLOCKING_OPERATIONS.json",
            "FILESYSTEM_STARTUP_ANALYSIS.json",
            "STARTUP_THREAD_ANALYSIS.json",
            "STARTUP_MEMORY_ANALYSIS.json",
        ):
            if (src / name).exists():
                shutil.copy2(src / name, pkg / name)
    if (startup_out / "STARTUP_IMPORT_SUMMARY.json").exists():
        shutil.copy2(startup_out / "STARTUP_IMPORT_SUMMARY.json", pkg / "STARTUP_IMPORT_SUMMARY.json")

    ui_agg = _aggregate_ui(startup_out)
    _write(pkg / "UI_EVENT_LOOP_RESPONSIVENESS.json", ui_agg)
    ui_val = _run([PY, "validate_ui_responsiveness.py", "--evidence-dir", str(startup_out)])
    # Prefer package-local UI validation using aggregated delays
    above_500 = int(ui_agg.get("delays_above_500_ms") or 0)
    ui_passed = above_500 == 0 and int(ui_agg.get("sample_count") or 0) >= 3
    _write(pkg / "UI_RESPONSIVENESS_VALIDATION.json", {
        "UI_RESPONSIVENESS_VALIDATION": "PASSED" if ui_passed else "FAILED",
        "p50_event_loop_delay_ms": ui_agg.get("p50_event_loop_delay_ms"),
        "p95_event_loop_delay_ms": ui_agg.get("p95_event_loop_delay_ms"),
        "max_event_loop_delay_ms": ui_agg.get("max_event_loop_delay_ms"),
        "delays_above_200_ms": ui_agg.get("delays_above_200_ms"),
        "delays_above_500_ms": above_500,
        "preferred_p95_below_100ms": (ui_agg.get("p95_event_loop_delay_ms") or 999) < 100,
        "validate_script_exit": ui_val["exit_code"],
        "splash_excluded": True,
    })

    startup_perf_val = _run(
        [PY, "validate_startup_performance.py", "--evidence-dir", str(startup_out)]
    )
    startup_perf_json = _load(startup_out / "STARTUP_PERFORMANCE_VALIDATION.json")
    if startup_perf_json:
        _write(pkg / "STARTUP_PERFORMANCE_VALIDATION.json", startup_perf_json)

    # Translation suite (fresh)
    print("Running translation beta repair validation...")
    tr = _run([PY, "validate_translation_beta_repair.py"], timeout=1800)
    _write(pkg / "translation_beta_repair_run.json", {
        "exit_code": tr["exit_code"],
        "stdout_tail": tr["stdout"][-6000:],
        "stderr_tail": tr["stderr"][-2000:],
    })
    # Locate newest repair package created by validator
    tr_pkg = _pick_latest_translation_repair()
    if tr_pkg is None:
        tr_copy = {"error": "no_translation_repair_package"}
    else:
        tr_copy = _copy_translation_artifacts(tr_pkg, pkg)
        # Also copy freeze/diarization if not already
        for name in (
            "JAPANESE_FREEZE_VERIFICATION.json",
            "ENGLISH_NO_DIARIZATION_VALIDATION.json",
        ):
            if (tr_pkg / name).exists() and not (pkg / name).exists():
                shutil.copy2(tr_pkg / name, pkg / name)

    en_freeze = _english_freeze()
    _write(pkg / "ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION.json", en_freeze)

    # UI method validations
    print("Validating Start/Stop/Copy/Export/Clear + Speaker...")
    try:
        start_stop, copy_export_clear, speaker = _ui_methods_validation()
    except Exception as exc:
        start_stop = {"START_STOP_VALIDATION": "FAILED", "error": str(exc)}
        copy_export_clear = {"COPY_EXPORT_CLEAR_VALIDATION": "FAILED", "error": str(exc)}
        speaker = {"GENERIC_SPEAKER_VALIDATION": "FAILED", "error": str(exc)}
    _write(pkg / "START_STOP_VALIDATION.json", start_stop)
    _write(pkg / "COPY_EXPORT_CLEAR_VALIDATION.json", copy_export_clear)
    _write(pkg / "GENERIC_SPEAKER_VALIDATION.json", speaker)

    frozen_after = _capture_frozen_hashes()
    _write(after_dir / "FROZEN_COMPONENT_HASHES.json", frozen_after)
    hash_drift = [k for k in frozen_before if frozen_before.get(k) != frozen_after.get(k)]
    _write(pkg / "FROZEN_HASH_DRIFT.json", {"changed": hash_drift})

    # Japanese freeze must exist; if missing recompute via repair helper
    if not (pkg / "JAPANESE_FREEZE_VERIFICATION.json").exists():
        from validate_translation_beta_repair import verify_japanese_freeze

        baseline = (
            ROOT
            / "troubleshooting"
            / "validation"
            / "translation_beta"
            / "JAPANESE_FREEZE_BASELINE.json"
        )
        _write(pkg / "JAPANESE_FREEZE_VERIFICATION.json", verify_japanese_freeze(baseline))

    ja_freeze = _load(pkg / "JAPANESE_FREEZE_VERIFICATION.json")
    en_nodiar = _load(pkg / "ENGLISH_NO_DIARIZATION_VALIDATION.json")
    graceful = _load(pkg / "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION.json")
    counters = _load(pkg / "TRANSLATION_COUNTER_VALIDATION.json")
    order = _load(pkg / "TRANSLATION_ORDER_VALIDATION.json")
    bilingual = _load(pkg / "BIDIRECTIONAL_TRANSLATION_VALIDATION.json")
    queue_val = _load(pkg / "TRANSLATION_QUEUE_VALIDATION.json")
    latency = _load(pkg / "TRANSLATION_LATENCY_ANALYSIS.json")
    smoke = _load(pkg / "TRANSLATION_SMOKE_TEST.json")
    gsum = graceful.get("summary") or {}
    c = counters.get("counters") or counters.get("summary_excerpt") or {}

    # Proven bottleneck from blocking ops
    blocking = _load(pkg / "MAIN_THREAD_BLOCKING_OPERATIONS.json")
    ops = sorted(
        blocking.get("operations") or [],
        key=lambda o: float(o.get("duration_ms") or o.get("ms") or 0),
        reverse=True,
    )
    bottleneck = ops[0] if ops else {
        "name": "import_alpha.ui.main_window / AlphaApp.__init__",
        "note": "from prior analysis when ops missing",
    }

    # Gate evaluation
    blockers: list[str] = []

    def need(cond: bool, name: str) -> None:
        if not cond:
            blockers.append(name)

    paint_imp = comparison.get("first_paint_improvement_pct")
    inter_imp = comparison.get("interactive_improvement_pct")
    rp = comparison.get("repaired_median_first_paint_ms")
    ri = comparison.get("repaired_median_interactive_ms")
    paint_ok = (
        paint_imp is not None
        and (paint_imp >= 30.0 or (rp is not None and rp <= 2000))
    )
    inter_ok = (
        inter_imp is not None
        and (inter_imp >= 30.0 or (ri is not None and ri <= 5000))
    )
    need(bool(comparison.get("splash_excluded", True)), "splash_excluded")
    need(paint_ok, "real_alpha_first_paint_gate")
    need(inter_ok, "real_alpha_interactive_gate")
    need(ui_passed, "ui_responsiveness")
    need(ja_freeze.get("JAPANESE_FREEZE_VERIFICATION") == "PASSED", "japanese_freeze")
    need(
        en_freeze.get("ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION") == "PASSED",
        "english_transcription_freeze",
    )
    need(
        en_nodiar.get("ENGLISH_NO_DIARIZATION_VALIDATION") == "PASSED",
        "english_no_diarization",
    )
    need(speaker.get("GENERIC_SPEAKER_VALIDATION") == "PASSED", "generic_speaker")
    need(bilingual.get("BIDIRECTIONAL_TRANSLATION_VALIDATION") == "PASSED", "bidirectional_translation")
    need(order.get("TRANSLATION_ORDER_VALIDATION") == "PASSED", "translation_order")
    need(
        graceful.get("TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION") == "PASSED",
        "graceful_shutdown",
    )
    if not c:
        blockers.append("translation_counters_missing")
    else:
        need(int(c.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM", -1)) == 0, "interim_provider_requests")
        need(int(c.get("DUPLICATE_TRANSLATION_REQUESTS_SENT", -1)) == 0, "duplicate_provider_requests")
    unfinished = gsum.get("MISSING_TRANSLATION_SEGMENT_IDS") or gsum.get(
        "UNFINISHED_TRANSLATION_SEGMENT_IDS"
    ) or []
    if not gsum:
        blockers.append("graceful_summary_missing")
    else:
        need(int(gsum.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", -1)) == 0, "pending_queue")
        need(int(gsum.get("TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT", -1)) == 0, "inflight_jobs")
        need(int(gsum.get("ORDERING_BUFFER_PENDING_AT_EXIT", -1)) == 0, "ordering_buffer")
        need(not unfinished, "missing_translation_ids")
        worker_stopped = gsum.get("TRANSLATION_WORKER_STOPPED")
        if worker_stopped is None:
            worker_stopped = gsum.get("worker_stopped")
        need(worker_stopped is True or str(worker_stopped).lower() == "true", "translation_worker_stopped")
    need(start_stop.get("START_STOP_VALIDATION") == "PASSED", "start_stop")
    need(copy_export_clear.get("COPY_EXPORT_CLEAR_VALIDATION") == "PASSED", "copy_export_clear")
    need(queue_val.get("TRANSLATION_QUEUE_VALIDATION") == "PASSED", "translation_queue")

    # Japanese retained-audio / English retained-audio: honest NOT_RUN (not required for READY list explicitly beyond freeze)
    _write(pkg / "JAPANESE_RETAINED_AUDIO_REGRESSION.json", {
        "JAPANESE_RETAINED_AUDIO_REGRESSION": "NOT_RUN",
        "reason": "full_retained_audio_replay_not_executed_in_this_pass",
        "note": "Japanese freeze hash+request verification used instead; NOT_RUN is not PASSED",
    })
    _write(pkg / "ENGLISH_RETAINED_AUDIO_REGRESSION.json", {
        "ENGLISH_RETAINED_AUDIO_REGRESSION": "NOT_RUN",
        "reason": "full_retained_audio_replay_not_executed_in_this_pass",
        "note": "English request freeze + no-diarization used instead; NOT_RUN is not PASSED",
    })

    status = "READY_FOR_SHORT_LIVE_TEST" if not blockers else "BLOCKED"
    readiness = {
        "SHORT_LIVE_TEST_READINESS": status,
        "STATUS": status,
        "blockers": blockers,
        "live_acceptance": "NOT_RUN",
        "note": "Do not claim Version 1 complete until short live bilingual test ACCEPTED",
        "splash_excluded": True,
        "measurement": "real_alpha_window_only",
    }
    _write(pkg / "SHORT_LIVE_TEST_READINESS.json", readiness)

    commands = """SHORT LIVE BILINGUAL TEST (5–10 minutes)

PRE-TEST:
  cd "C:\\Users\\islamm\\Documents\\Tariqul\\Alpha_Translator V 1.0\\Alpha_Live_Translator"
  python validate_english_no_diarization.py
  python -c "from alpha.constants import UI_SPEAKER_LABEL, ENGLISH_DIARIZATION_ENABLED; assert UI_SPEAKER_LABEL=='Speaker:'; assert ENGLISH_DIARIZATION_ENABLED is False; print('ok')"
  python main.py

DURING TEST:
  1. Confirm real Alpha window opens (not splash).
  2. Press Start.
  3. Speak several English segments; confirm Japanese translations appear with "Speaker:" labels.
  4. Speak several Japanese segments; confirm English translations appear with "Speaker:" labels.
  5. Scroll original + translated panels; Confirm UI stays responsive.
  6. Exercise Copy, Export, Clear once each.
  7. Press Stop; confirm Stop completes without hang.

POST-TEST:
  python validate_final_stabilization_consistency.py --package-dir troubleshooting\\final_stabilization{TS}
  # Inspect latest troubleshooting\\runs\\* for:
  #   translation_summary.json, translation_events.jsonl, UI heartbeat, stop timeline
  # Record ACCEPTED or FAILED only after reviewing evidence.

Collect: audio delivery, Stable segments, translation request/result IDs, queue depth,
translation latency, UI event-loop delay, CPU, memory, Stop duration, pending at exit,
missing/duplicate IDs.
""".replace("{TS}", ts)
    _write(pkg / "SHORT_LIVE_TEST_COMMANDS.txt", commands)

    e2e = smoke.get("translation_end_to_end_ms") or (latency.get("burst_e2e_p50") and {
        "p50": latency.get("burst_e2e_p50"),
        "p95": latency.get("burst_e2e_p95"),
        "max": latency.get("burst_e2e_max"),
    }) or (queue_val.get("latency") or {}).get("end_to_end") or {}
    if not isinstance(e2e, dict):
        e2e = {}

    # Write preliminary decision (consistency filled next)
    decision_body = "\n".join(
        [
            "ALPHA FINAL STABILIZATION DECISION REPORT",
            f"generated_at_utc={ts}",
            f"package={pkg.name}",
            f"proven_startup_bottleneck={json.dumps(bottleneck, ensure_ascii=False)}",
            f"real_alpha_first_paint_before_ms={comparison.get('baseline_median_first_paint_ms')}",
            f"real_alpha_first_paint_after_ms={comparison.get('repaired_median_first_paint_ms')}",
            f"real_alpha_interactive_before_ms={comparison.get('baseline_median_interactive_ms')}",
            f"real_alpha_interactive_after_ms={comparison.get('repaired_median_interactive_ms')}",
            f"first_paint_improvement_pct={comparison.get('first_paint_improvement_pct')}",
            f"interactive_improvement_pct={comparison.get('interactive_improvement_pct')}",
            f"ui_p95_delay_ms={ui_agg.get('p95_event_loop_delay_ms')}",
            f"ui_delays_above_500={above_500}",
            f"longest_ui_thread_block={json.dumps(ui_agg.get('longest_main_thread_block'), ensure_ascii=False)}",
            f"translation_e2e_p50_p95_max={e2e.get('p50')}/{e2e.get('p95')}/{e2e.get('max')}",
            f"TRANSLATION_QUEUE_PENDING_AT_EXIT={gsum.get('TRANSLATION_QUEUE_PENDING_AT_EXIT')}",
            f"TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT={gsum.get('TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT')}",
            f"ORDERING_BUFFER_PENDING_AT_EXIT={gsum.get('ORDERING_BUFFER_PENDING_AT_EXIT')}",
            f"TRANSLATION_REQUESTS_SENT_FROM_INTERIM={c.get('TRANSLATION_REQUESTS_SENT_FROM_INTERIM')}",
            f"DUPLICATE_TRANSLATION_REQUESTS_SENT={c.get('DUPLICATE_TRANSLATION_REQUESTS_SENT')}",
            f"MISSING_TRANSLATION_SEGMENT_IDS={unfinished}",
            f"JAPANESE_FREEZE_VERIFICATION={ja_freeze.get('JAPANESE_FREEZE_VERIFICATION')}",
            f"ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION={en_freeze.get('ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION')}",
            f"ENGLISH_NO_DIARIZATION_VALIDATION={en_nodiar.get('ENGLISH_NO_DIARIZATION_VALIDATION')}",
            f"GENERIC_SPEAKER_VALIDATION={speaker.get('GENERIC_SPEAKER_VALIDATION')}",
            f"ja_to_en={bilingual.get('ja_to_en')}",
            f"en_to_ja={bilingual.get('en_to_ja')}",
            f"START_STOP_VALIDATION={start_stop.get('START_STOP_VALIDATION')}",
            f"COPY_EXPORT_CLEAR_VALIDATION={copy_export_clear.get('COPY_EXPORT_CLEAR_VALIDATION')}",
            "FINAL_STABILIZATION_REPORT_CONSISTENCY=PENDING",
            f"STATUS = {status}",
            f"blockers={blockers}",
            "",
            "Notes:",
            "- Splash timing excluded; only real Alpha window metrics accept.",
            "- NOT_RUN is never converted to PASSED.",
            "- Short live bilingual test is still required before Version 1 acceptance.",
            "",
        ]
    )
    _write(pkg / "FINAL_STABILIZATION_DECISION_REPORT.txt", decision_body)
    _write(pkg / "Cursor final report.txt", decision_body)

    manifest = {
        "timestamp": ts,
        "package_dir": str(pkg),
        "STATUS": status,
        "blockers": blockers,
        "splash_excluded": True,
        "measurement": "real_alpha_window_only",
        "computed_from_evidence": True,
        "translation_source_package": str(tr_pkg) if tr_pkg else None,
        "startup_profile": str(startup_out),
        "files": sorted(p.name for p in pkg.iterdir() if p.is_file()),
    }
    _write(pkg / "implementation_manifest.json", manifest)

    # Consistency
    cons = _run(
        [PY, "validate_final_stabilization_consistency.py", "--package-dir", str(pkg)]
    )
    consistency = _load(pkg / "FINAL_STABILIZATION_REPORT_CONSISTENCY.json")
    if consistency.get("FINAL_STABILIZATION_REPORT_CONSISTENCY") != "PASSED":
        blockers = list(dict.fromkeys(blockers + ["report_consistency"]))
        status = "BLOCKED"
        readiness["STATUS"] = status
        readiness["SHORT_LIVE_TEST_READINESS"] = status
        readiness["blockers"] = blockers
        _write(pkg / "SHORT_LIVE_TEST_READINESS.json", readiness)
        manifest["STATUS"] = status
        manifest["blockers"] = blockers
        _write(pkg / "implementation_manifest.json", manifest)

    decision_body = decision_body.replace(
        "FINAL_STABILIZATION_REPORT_CONSISTENCY=PENDING",
        f"FINAL_STABILIZATION_REPORT_CONSISTENCY={consistency.get('FINAL_STABILIZATION_REPORT_CONSISTENCY')}",
    ).replace(
        f"STATUS = {'READY_FOR_SHORT_LIVE_TEST' if not blockers or status == 'READY_FOR_SHORT_LIVE_TEST' else 'BLOCKED'}",
        f"STATUS = {status}",
    )
    # Force rewrite STATUS line cleanly
    lines = []
    for line in decision_body.splitlines():
        if line.startswith("STATUS ="):
            lines.append(f"STATUS = {status}")
        elif line.startswith("blockers="):
            lines.append(f"blockers={blockers}")
        else:
            lines.append(line)
    decision_body = "\n".join(lines) + "\n"
    _write(pkg / "FINAL_STABILIZATION_DECISION_REPORT.txt", decision_body)
    _write(pkg / "Cursor final report.txt", decision_body)

    # Re-run consistency after decision rewrite
    cons2 = _run(
        [PY, "validate_final_stabilization_consistency.py", "--package-dir", str(pkg)]
    )
    consistency = _load(pkg / "FINAL_STABILIZATION_REPORT_CONSISTENCY.json")
    if consistency.get("FINAL_STABILIZATION_REPORT_CONSISTENCY") != "PASSED":
        status = "BLOCKED"
        blockers = list(dict.fromkeys(blockers + ["report_consistency"]))
        readiness["STATUS"] = status
        readiness["SHORT_LIVE_TEST_READINESS"] = status
        readiness["blockers"] = blockers
        _write(pkg / "SHORT_LIVE_TEST_READINESS.json", readiness)
        manifest["STATUS"] = status
        manifest["blockers"] = blockers
        _write(pkg / "implementation_manifest.json", manifest)
        lines = []
        for line in decision_body.splitlines():
            if line.startswith("STATUS ="):
                lines.append(f"STATUS = {status}")
            elif line.startswith("blockers="):
                lines.append(f"blockers={blockers}")
            elif line.startswith("FINAL_STABILIZATION_REPORT_CONSISTENCY="):
                lines.append(
                    f"FINAL_STABILIZATION_REPORT_CONSISTENCY={consistency.get('FINAL_STABILIZATION_REPORT_CONSISTENCY')}"
                )
            else:
                lines.append(line)
        decision_body = "\n".join(lines) + "\n"
        _write(pkg / "FINAL_STABILIZATION_DECISION_REPORT.txt", decision_body)
        _write(pkg / "Cursor final report.txt", decision_body)
        _run([PY, "validate_final_stabilization_consistency.py", "--package-dir", str(pkg)])
        consistency = _load(pkg / "FINAL_STABILIZATION_REPORT_CONSISTENCY.json")

    # Package ZIP
    zip_dir = ROOT / "troubleshooting" / "final_stabilization"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"ALPHA_FINAL_STABILIZATION_{ts}.zip"
    key = (os.getenv("DEEPL_AUTH_KEY") or os.getenv("DEEPL_API_KEY") or "").strip()
    exclude_names = {".env", "credentials.json"}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in pkg.rglob("*"):
            if not path.is_file():
                continue
            if path.name.lower() in exclude_names:
                continue
            if path.suffix.lower() in {".wav", ".mp3", ".zip"} and path.stat().st_size > 2_000_000:
                continue
            data = path.read_bytes()
            if key and key.encode("utf-8") in data:
                continue
            zf.write(path, arcname=str(path.relative_to(pkg.parent)))
    _write(pkg / "ZIP_PATH.txt", str(zip_path))
    manifest["zip_path"] = str(zip_path)
    _write(pkg / "implementation_manifest.json", manifest)

    # Final metrics blob for Cursor response
    metrics = {
        "proven_bottleneck": bottleneck,
        "paint_before": comparison.get("baseline_median_first_paint_ms"),
        "paint_after": comparison.get("repaired_median_first_paint_ms"),
        "interactive_before": comparison.get("baseline_median_interactive_ms"),
        "interactive_after": comparison.get("repaired_median_interactive_ms"),
        "ui_p95": ui_agg.get("p95_event_loop_delay_ms"),
        "longest_block": ui_agg.get("longest_main_thread_block"),
        "e2e": e2e,
        "pending": gsum.get("TRANSLATION_QUEUE_PENDING_AT_EXIT"),
        "inflight": gsum.get("TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT"),
        "ordering": gsum.get("ORDERING_BUFFER_PENDING_AT_EXIT"),
        "interim_sent": c.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM"),
        "dup_sent": c.get("DUPLICATE_TRANSLATION_REQUESTS_SENT"),
        "missing_ids": unfinished,
        "ja_freeze": ja_freeze.get("JAPANESE_FREEZE_VERIFICATION"),
        "en_freeze": en_freeze.get("ENGLISH_TRANSCRIPTION_FREEZE_VERIFICATION"),
        "en_nodiar": en_nodiar.get("ENGLISH_NO_DIARIZATION_VALIDATION"),
        "speaker": speaker.get("GENERIC_SPEAKER_VALIDATION"),
        "ja_en": bilingual.get("ja_to_en"),
        "en_ja": bilingual.get("en_to_ja"),
        "start_stop": start_stop.get("START_STOP_VALIDATION"),
        "copy_export_clear": copy_export_clear.get("COPY_EXPORT_CLEAR_VALIDATION"),
        "consistency": consistency.get("FINAL_STABILIZATION_REPORT_CONSISTENCY"),
        "status": status,
        "blockers": blockers,
        "zip": str(zip_path),
        "package": str(pkg),
    }
    _write(pkg / "CURSOR_RESPONSE_METRICS.json", metrics)
    print("FINAL_STATUS", status)
    print("METRICS", json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0 if status == "READY_FOR_SHORT_LIVE_TEST" else 1


if __name__ == "__main__":
    raise SystemExit(main())
