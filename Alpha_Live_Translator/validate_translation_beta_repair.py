#!/usr/bin/env python3
"""Alpha Translation Beta repair validation — hard-fail, honest shutdown, true latency."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass

from alpha.constants import (  # noqa: E402
    ENGLISH_DIARIZATION_ENABLED,
    JAPANESE_KEYTERMS_ENABLED,
    JAPANESE_STT_PROFILE,
    UI_SPEAKER_LABEL,
)
from alpha.stt_settings import (  # noqa: E402
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_JA_ENDPOINTING_MS,
    DEEPGRAM_JA_UTTERANCE_END_MS,
    DEEPGRAM_MODEL,
    clamp_deepgram_utterance_end_ms,
)
from alpha.translation.acceptance import (  # noqa: E402
    evaluate_graceful_shutdown_gate,
    evaluate_overall_acceptance,
)
from alpha.translation.deepl_client import DeepLClient, DeepLError  # noqa: E402
from alpha.translation.language_map import (  # noqa: E402
    get_deepl_source_code,
    target_for_source,
)
from alpha.translation.translation_worker import (  # noqa: E402
    TranslationResult,
    TranslationWorker,
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


JA_SEGMENTS = [
    "本日の会議を開始します。",
    "予算について確認します。",
    "次のステップを決めましょう。",
    "資料を共有してください。",
    "以上です。ありがとうございます。",
]
EN_SEGMENTS = [
    "Let's start today's meeting.",
    "Please confirm the budget.",
    "We should decide next steps.",
    "Please share the document.",
    "That is all. Thank you.",
]


class MockDeepLClient:
    available = True

    def __init__(self, delay_s: float = 0.0, stall_forever: bool = False):
        self.delay_s = float(delay_s)
        self.stall_forever = bool(stall_forever)
        self.calls: List[dict] = []
        self.lock = threading.Lock()

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        with self.lock:
            self.calls.append(
                {
                    "text": text,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "thread": threading.current_thread().name,
                }
            )
        if self.stall_forever:
            while True:
                time.sleep(0.2)
        if self.delay_s:
            time.sleep(self.delay_s)
        return f"TR[{target_lang}] {text}"


def _looks_like_placeholder_key(key: str) -> bool:
    low = (key or "").strip().lower()
    if not low or len(low) < 20:
        return True
    return any(
        t in low
        for t in (
            "your_deepl",
            "your-api",
            "api_key_here",
            "auth_key_here",
            "changeme",
            "example",
            "xxxxxx",
        )
    )


def verify_japanese_freeze(baseline: Path) -> dict:
    hard = [
        "alpha/transcription/japanese_sentence_assembler.py",
        "alpha/transcription/japanese_business_accuracy.py",
        "alpha/transcription/japanese_accuracy_cleaner.py",
        "alpha/transcription/japanese_final_chunk_stabilizer.py",
        "alpha/utils/japanese_accuracy_log.py",
        "alpha/utils/cjk_text.py",
        "alpha/stt_settings.py",
    ]
    japanese_request_freeze = {
        "model": "nova-3",
        "language": "ja",
        "endpointing": int(DEEPGRAM_JA_ENDPOINTING_MS),
        "utterance_end_ms": int(DEEPGRAM_JA_UTTERANCE_END_MS),
        "JAPANESE_STT_PROFILE": JAPANESE_STT_PROFILE,
        "JAPANESE_KEYTERMS_ENABLED": JAPANESE_KEYTERMS_ENABLED,
        "diarize_absent": True,
    }
    if not baseline.exists():
        return {
            "JAPANESE_FREEZE_VERIFICATION": "NOT_RUN",
            "reason": "baseline_missing",
            "baseline_path": str(baseline),
            "hard_file_mismatches": [],
            "timing_ok": False,
            "japanese_request_freeze": japanese_request_freeze,
        }
    base = json.loads(baseline.read_text(encoding="utf-8"))
    hashes = base.get("file_sha256") or {}
    if not hashes:
        return {
            "JAPANESE_FREEZE_VERIFICATION": "NOT_RUN",
            "reason": "baseline_hashes_missing",
            "baseline_path": str(baseline),
            "hard_file_mismatches": [],
            "timing_ok": False,
            "japanese_request_freeze": japanese_request_freeze,
        }
    mismatches = []
    for rel in hard:
        p = ROOT / rel
        digest = _sha256_file(p)
        if hashes.get(rel) and digest != hashes[rel]:
            mismatches.append(rel)
        elif not hashes.get(rel):
            mismatches.append(f"{rel}:missing_baseline_hash")
    timing = base.get("japanese_deepgram_timing_snapshot") or {}
    timing_ok = (
        str(timing.get("model")) == str(DEEPGRAM_MODEL)
        and str(timing.get("language")) == "ja"
        and int(timing.get("endpointing")) == int(DEEPGRAM_JA_ENDPOINTING_MS) == 500
        and int(timing.get("utterance_end_ms")) == int(DEEPGRAM_JA_UTTERANCE_END_MS) == 1500
        and str(timing.get("JAPANESE_STT_PROFILE")) == str(JAPANESE_STT_PROFILE)
        and bool(timing.get("JAPANESE_KEYTERMS_ENABLED")) == bool(JAPANESE_KEYTERMS_ENABLED)
    )
    live_timing_ok = (
        str(DEEPGRAM_MODEL) == "nova-3"
        and int(DEEPGRAM_JA_ENDPOINTING_MS) == 500
        and int(DEEPGRAM_JA_UTTERANCE_END_MS) == 1500
        and str(JAPANESE_STT_PROFILE) == "no_diarize"
    )
    passed = (not mismatches) and timing_ok and live_timing_ok
    return {
        "JAPANESE_FREEZE_VERIFICATION": "PASSED" if passed else "FAILED",
        "hard_file_mismatches": mismatches,
        "timing_ok": timing_ok and live_timing_ok,
        "japanese_request_freeze": japanese_request_freeze,
    }


def run_english_no_diarization(out_root: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "validate_english_no_diarization.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    src = (
        ROOT
        / "troubleshooting"
        / "validation"
        / "translation_beta"
        / "ENGLISH_NO_DIARIZATION_VALIDATION.json"
    )
    payload = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}
    # Enforce English timing expectations from this repair
    ue = int(clamp_deepgram_utterance_end_ms(5000)[0])
    checks = payload.get("checks") or {}
    prod = (checks.get("production_builder") or {})
    live = (checks.get("live_deepgram_url") or {})
    en_ok = (
        payload.get("ENGLISH_NO_DIARIZATION_VALIDATION") == "PASSED"
        and ENGLISH_DIARIZATION_ENABLED is False
        and str(prod.get("endpointing")) == str(int(DEEPGRAM_ENDPOINTING_MS))
        and str(prod.get("utterance_end_ms")) == str(ue)
        and prod.get("diarize_absent") is True
        and prod.get("diarize_model_absent") is True
    )
    if live:
        en_ok = en_ok and live.get("diarize_absent") and live.get("diarize_model_absent")
    payload["ENGLISH_NO_DIARIZATION_VALIDATION"] = "PASSED" if en_ok else "FAILED"
    payload["validate_exit"] = proc.returncode
    _write(out_root / "ENGLISH_NO_DIARIZATION_VALIDATION.json", payload)
    return payload


def test_counter_semantics(evidence: Path) -> dict:
    ready: List[TranslationResult] = []
    mock = MockDeepLClient(delay_s=0.02)
    w = TranslationWorker(
        run_id="counters",
        evidence_dir=evidence / "counters",
        on_translation_ready=lambda r: ready.append(r),
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    assert w.start()
    w.enqueue_stable_segment(segment_id=1, source_language="en", source_text="one")
    w.enqueue_stable_segment(segment_id=2, source_language="en", source_text="two")
    w.enqueue_stable_segment(
        segment_id=99, source_language="en", source_text="interim", is_interim=True
    )
    w.enqueue_stable_segment(
        segment_id=1, source_language="en", source_text="one again", is_interim=False
    )
    deadline = time.time() + 5
    while time.time() < deadline and len(ready) < 2:
        time.sleep(0.05)
    summary = w.shutdown(timeout_seconds=3)
    c = w.get_counters()
    ok = (
        c["INTERIM_SUBMISSIONS_REJECTED"] == 1
        and c["DUPLICATE_SUBMISSIONS_REJECTED"] == 1
        and c["TRANSLATION_REQUESTS_SENT_FROM_INTERIM"] == 0
        and c["DUPLICATE_TRANSLATION_REQUESTS_SENT"] == 0
        and c["STABLE_TRANSLATION_JOBS_ACCEPTED"] == 2
        and c["TRANSLATION_JOBS_QUEUED"] == 2
        and c["TRANSLATION_REQUESTS_SENT"] == 2
        and c["TRANSLATION_COMMITS_COMPLETED"] == 2
        and all(call["thread"] == "TranslationWorker" for call in mock.calls)
    )
    payload = {
        "TRANSLATION_COUNTER_SEMANTICS_VALIDATION": "PASSED" if ok else "FAILED",
        "counters": c,
        "summary_excerpt": {
            "TRANSLATION_REQUESTS_SENT_FROM_INTERIM": summary[
                "TRANSLATION_REQUESTS_SENT_FROM_INTERIM"
            ],
            "DUPLICATE_TRANSLATION_REQUESTS_SENT": summary[
                "DUPLICATE_TRANSLATION_REQUESTS_SENT"
            ],
            "INTERIM_SUBMISSIONS_REJECTED": summary["INTERIM_SUBMISSIONS_REJECTED"],
            "DUPLICATE_SUBMISSIONS_REJECTED": summary["DUPLICATE_SUBMISSIONS_REJECTED"],
            "TRANSLATION_REQUESTS_SENT": summary["TRANSLATION_REQUESTS_SENT"],
        },
        "mock_threads": [x["thread"] for x in mock.calls],
    }
    return payload


def test_order(evidence: Path) -> dict:
    ready: List[int] = []
    w = TranslationWorker(
        run_id="order",
        evidence_dir=evidence / "order",
        on_translation_ready=lambda r: ready.append(r.segment_id),
        client=MockDeepLClient(),  # type: ignore[arg-type]
        enabled=True,
    )
    from alpha.translation.translation_worker import StableTranslationJob

    w._next_commit_id = 11
    job12 = StableTranslationJob(
        run_id="order",
        segment_id=12,
        source_language="EN",
        source_text="twelve",
        source_text_hash="a",
        stable_committed_at=time.time(),
    )
    job11 = StableTranslationJob(
        run_id="order",
        segment_id=11,
        source_language="EN",
        source_text="eleven",
        source_text_hash="b",
        stable_committed_at=time.time(),
    )
    mid: List[int] = []
    w._handle_result(w._translate_job(job12, "JA"))
    mid = list(ready)
    w._handle_result(w._translate_job(job11, "JA"))
    ok = mid == [] and ready == [11, 12] and w.get_counters()["OUT_OF_ORDER_TRANSLATION_COMMITS"] == 0
    return {
        "TRANSLATION_ORDER_VALIDATION": "PASSED" if ok else "FAILED",
        "ready_after_12_only": mid,
        "ready_final": ready,
        "OUT_OF_ORDER_TRANSLATION_COMMITS": w.get_counters()[
            "OUT_OF_ORDER_TRANSLATION_COMMITS"
        ],
    }


def test_graceful_shutdown(evidence: Path) -> dict:
    ready: List[int] = []
    mock = MockDeepLClient(delay_s=0.05)
    w = TranslationWorker(
        run_id="graceful",
        evidence_dir=evidence / "graceful",
        on_translation_ready=lambda r: ready.append(r.segment_id),
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    assert w.start()
    for i in range(1, 6):
        w.enqueue_stable_segment(
            segment_id=i, source_language="en", source_text=f"seg-{i}"
        )
    summary = w.shutdown(timeout_seconds=10.0)
    gate_ok, gate_failures = evaluate_graceful_shutdown_gate(summary)
    ok = (
        gate_ok
        and summary.get("STABLE_TRANSLATION_JOBS_ACCEPTED") == 5
        and summary.get("TRANSLATION_REQUESTS_SENT") == 5
        and summary.get("TRANSLATION_COMMITS_COMPLETED") == 5
        and len(ready) == 5
    )
    return {
        "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION": "PASSED" if ok else "FAILED",
        "gate_failures": gate_failures,
        "summary": summary,
        "committed_ui": ready,
    }


def test_timeout_handling(evidence: Path) -> dict:
    # Long provider delay + short deadline: bounded exit with unfinished IDs.
    mock = MockDeepLClient(delay_s=30.0)
    w = TranslationWorker(
        run_id="timeout",
        evidence_dir=evidence / "timeout",
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    assert w.start()
    for i in range(1, 4):
        w.enqueue_stable_segment(
            segment_id=i, source_language="en", source_text=f"stall-{i}"
        )
    t0 = time.time()
    summary = w.shutdown(timeout_seconds=1.0)
    elapsed = time.time() - t0
    unfinished = summary.get("UNFINISHED_TRANSLATION_SEGMENT_IDS") or []
    ok = (
        elapsed < 3.0
        and len(unfinished) > 0
        # Must NOT falsely claim clean drain
        and int(summary.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", 0)) > 0
        and (
            bool(summary.get("TRANSLATION_WORKER_STOPPED"))
            or bool(summary.get("TRANSLATION_WORKER_STOP_TIMED_OUT"))
        )
    )
    return {
        "TRANSLATION_TIMEOUT_HANDLING_VALIDATION": "PASSED" if ok else "FAILED",
        "elapsed_s": round(elapsed, 3),
        "summary": {
            "TRANSLATION_WORKER_STOPPED": summary.get("TRANSLATION_WORKER_STOPPED"),
            "TRANSLATION_QUEUE_PENDING_AT_EXIT": summary.get(
                "TRANSLATION_QUEUE_PENDING_AT_EXIT"
            ),
            "UNFINISHED_TRANSLATION_SEGMENT_IDS": unfinished,
            "TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT": summary.get(
                "TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT"
            ),
        },
        "note": "Forced-timeout verifies bounded shutdown only; not a clean drain.",
    }


def test_paced(evidence: Path) -> dict:
    results: List[TranslationResult] = []
    mock = MockDeepLClient(delay_s=0.03)
    w = TranslationWorker(
        run_id="paced",
        evidence_dir=evidence / "paced",
        on_translation_ready=lambda r: results.append(r),
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    assert w.start()
    for i in range(1, 11):
        w.enqueue_stable_segment(
            segment_id=i,
            source_language="ja" if i <= 5 else "en",
            source_text=(JA_SEGMENTS[i - 1] if i <= 5 else EN_SEGMENTS[i - 6]),
            stable_commit_timestamp=time.time(),
        )
        time.sleep(0.12)
    summary = w.shutdown(timeout_seconds=15)
    ok, fails = evaluate_graceful_shutdown_gate(summary)
    ok = ok and len(results) == 10 and summary.get("TRANSLATION_REQUESTS_SENT") == 10
    return {
        "TRANSLATION_PACED_TEST": "PASSED" if ok else "FAILED",
        "gate_failures": fails,
        "maximum_queue_depth": summary.get("maximum_queue_depth"),
        "queue_wait_ms": summary.get("queue_wait_ms"),
        "provider_latency_ms": summary.get("provider_latency_ms"),
        "translation_end_to_end_ms": summary.get("translation_end_to_end_ms"),
        "summary": summary,
    }


def test_burst(evidence: Path) -> dict:
    results: List[TranslationResult] = []
    mock = MockDeepLClient(delay_s=0.08)
    w = TranslationWorker(
        run_id="burst",
        evidence_dir=evidence / "burst",
        on_translation_ready=lambda r: results.append(r),
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    assert w.start()
    t_burst = time.time()
    for i in range(1, 11):
        w.enqueue_stable_segment(
            segment_id=i,
            source_language="en",
            source_text=f"burst-{i}-{t_burst}",
            stable_commit_timestamp=t_burst,
        )
    summary = w.shutdown(timeout_seconds=20)
    ok, fails = evaluate_graceful_shutdown_gate(summary)
    e2e = summary.get("translation_end_to_end_ms") or {}
    # Burst must expose accumulated queue delay (e2e >> provider for later segments)
    ok = (
        ok
        and len(results) == 10
        and [r.segment_id for r in results] == list(range(1, 11))
        and float(e2e.get("max") or 0) > float((summary.get("provider_latency_ms") or {}).get("p50") or 0)
    )
    return {
        "TRANSLATION_BURST_TEST": "PASSED" if ok else "FAILED",
        "gate_failures": fails,
        "maximum_queue_depth": summary.get("maximum_queue_depth"),
        "queue_wait_ms": summary.get("queue_wait_ms"),
        "provider_latency_ms": summary.get("provider_latency_ms"),
        "translation_end_to_end_ms": summary.get("translation_end_to_end_ms"),
        "note": "Burst e2e includes queue delay; provider latency is not total latency.",
        "summary": summary,
    }


def test_latency_fields(burst: dict, paced: dict) -> dict:
    b = burst.get("summary") or {}
    p = paced.get("summary") or {}
    required_keys = [
        "queue_wait_ms",
        "provider_latency_ms",
        "ordering_wait_ms",
        "translation_end_to_end_ms",
    ]
    ok = all(k in b and k in p for k in required_keys)
    ok = ok and b.get("provider_latency_ms", {}).get("p50") is not None
    ok = ok and b.get("translation_end_to_end_ms", {}).get("max") is not None
    # Must not treat provider as only latency
    ok = ok and (
        float(b.get("translation_end_to_end_ms", {}).get("max") or 0)
        >= float(b.get("provider_latency_ms", {}).get("p50") or 0)
    )
    return {
        "TRANSLATION_LATENCY_VALIDATION": "PASSED" if ok else "FAILED",
        "burst_provider_p50": (b.get("provider_latency_ms") or {}).get("p50"),
        "burst_provider_p95": (b.get("provider_latency_ms") or {}).get("p95"),
        "burst_e2e_p50": (b.get("translation_end_to_end_ms") or {}).get("p50"),
        "burst_e2e_p95": (b.get("translation_end_to_end_ms") or {}).get("p95"),
        "burst_e2e_max": (b.get("translation_end_to_end_ms") or {}).get("max"),
        "paced_e2e": p.get("translation_end_to_end_ms"),
        "true_e2e_latency_reported": ok,
    }


def test_live_integration(evidence: Path) -> dict:
    """Prove Stable-only path, Speaker label, UI-safe callback, no UI-thread network."""
    from alpha.constants import UI_SPEAKER_LABEL as LABEL

    ui_thread_name = "FakeUIThread"
    network_threads: List[str] = []
    panel_lines: List[str] = []
    transcript_source = ["Stable one", "Stable two"]
    original_copy = list(transcript_source)

    class ThreadAwareMock(MockDeepLClient):
        def translate_text(self, text, source_lang, target_lang):
            network_threads.append(threading.current_thread().name)
            time.sleep(0.15)
            return super().translate_text(text, source_lang, target_lang)

    mock = ThreadAwareMock(delay_s=0.0)
    ui_scheduled = threading.Event()

    def on_ready(result: TranslationResult) -> None:
        # Simulate UI-safe marshal: never call network here
        def _ui():
            line = f"{LABEL} {result.translated_text}".strip()
            panel_lines.append(line)
            ui_scheduled.set()

        t = threading.Thread(target=_ui, name=ui_thread_name)
        t.start()
        t.join(timeout=1)

    w = TranslationWorker(
        run_id="integration",
        evidence_dir=evidence / "integration",
        on_translation_ready=on_ready,
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    assert w.start()

    # interim rejected
    w.enqueue_stable_segment(
        segment_id=1, source_language="en", source_text="partial", is_interim=True
    )
    # stable accepted while "transcription" continues
    transcription_ticks = {"n": 0}

    def transcription():
        for _ in range(10):
            transcription_ticks["n"] += 1
            time.sleep(0.05)

    th = threading.Thread(target=transcription, name="FakeDeepgramReceiver")
    th.start()
    for i, text in enumerate(transcript_source, start=1):
        w.enqueue_stable_segment(segment_id=i, source_language="en", source_text=text)
    th.join(timeout=3)
    summary = w.shutdown(timeout_seconds=5)
    stop_responsive = True  # shutdown returned
    ok = (
        w.get_counters()["INTERIM_SUBMISSIONS_REJECTED"] == 1
        and w.get_counters()["TRANSLATION_REQUESTS_SENT_FROM_INTERIM"] == 0
        and w.get_counters()["TRANSLATION_REQUESTS_SENT"] == 2
        and transcript_source == original_copy
        and all(str(LABEL).rstrip(":") in line or line.startswith(str(LABEL)) for line in panel_lines)
        or all(line.startswith("Speaker:") or "Speaker:" in line for line in panel_lines)
    )
    # fix speaker check cleanly
    speaker_ok = all("Speaker:" in line for line in panel_lines) and len(panel_lines) == 2
    network_ok = all(n == "TranslationWorker" for n in network_threads) and ui_thread_name not in network_threads
    transcription_ok = transcription_ticks["n"] >= 5
    ok = (
        speaker_ok
        and network_ok
        and transcription_ok
        and stop_responsive
        and summary.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM") == 0
        and transcript_source == original_copy
        and w.get_counters()["INTERIM_SUBMISSIONS_REJECTED"] == 1
    )
    return {
        "TRANSLATION_LIVE_INTEGRATION_VALIDATION": "PASSED" if ok else "FAILED",
        "interim_rejected": w.get_counters()["INTERIM_SUBMISSIONS_REJECTED"],
        "provider_threads": network_threads,
        "panel_lines": panel_lines,
        "source_unchanged": transcript_source == original_copy,
        "transcription_ticks": transcription_ticks["n"],
        "generic_speaker_label": str(LABEL),
        "ui_update_scheduled": ui_scheduled.is_set(),
    }


def test_smoke(evidence: Path) -> dict:
    key = (os.getenv("DEEPL_AUTH_KEY") or os.getenv("DEEPL_API_KEY") or "").strip()
    if not key or _looks_like_placeholder_key(key):
        return {
            "TRANSLATION_SMOKE_TEST": "FAILED",
            "ok": False,
            "reason": "DEEPL_AUTH_KEY missing or placeholder",
        }
    results: List[TranslationResult] = []
    client = DeepLClient(api_key=key)
    w = TranslationWorker(
        run_id="smoke10",
        evidence_dir=evidence / "smoke",
        on_translation_ready=lambda r: results.append(r),
        client=client,
        enabled=True,
    )
    if not w.start():
        return {"TRANSLATION_SMOKE_TEST": "FAILED", "reason": w.status_message}
    originals = []
    for i, text in enumerate(JA_SEGMENTS, start=1):
        originals.append(text)
        w.enqueue_stable_segment(
            segment_id=i,
            source_language="ja",
            source_text=text,
            stable_commit_timestamp=time.time(),
        )
    for i, text in enumerate(EN_SEGMENTS, start=6):
        originals.append(text)
        w.enqueue_stable_segment(
            segment_id=i,
            source_language="en",
            source_text=text,
            stable_commit_timestamp=time.time(),
        )
    deadline = time.time() + 90
    while time.time() < deadline and len(results) < 10:
        time.sleep(0.1)
    summary = w.shutdown(timeout_seconds=20)
    gate_ok, gate_failures = evaluate_graceful_shutdown_gate(summary)
    ja_ok = all(
        r.source_language == "JA" and r.target_language == "EN-US"
        for r in results
        if r.segment_id <= 5
    )
    en_ok = all(
        r.source_language == "EN" and r.target_language == "JA"
        for r in results
        if r.segment_id >= 6
    )
    ordered = [r.segment_id for r in results]
    ok = (
        gate_ok
        and len(results) == 10
        and ordered == list(range(1, 11))
        and ja_ok
        and en_ok
        and summary.get("TRANSLATION_REQUESTS_SENT") == 10
        and summary.get("STABLE_TRANSLATION_JOBS_ACCEPTED") == 10
        and summary.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM") == 0
        and summary.get("DUPLICATE_TRANSLATION_REQUESTS_SENT") == 0
        and summary.get("SOURCE_TRANSCRIPT_MODIFICATIONS") == 0
        and originals == JA_SEGMENTS + EN_SEGMENTS
    )
    return {
        "TRANSLATION_SMOKE_TEST": "PASSED" if ok else "FAILED",
        "ok": ok,
        "gate_failures": gate_failures,
        "events": len(results),
        "ordered_ids": ordered,
        "ja_to_en": ja_ok,
        "en_to_ja": en_ok,
        "character_count_sent": summary.get("source_characters_sent"),
        "provider_latency_ms": summary.get("provider_latency_ms"),
        "translation_end_to_end_ms": summary.get("translation_end_to_end_ms"),
        "queue_wait_ms": summary.get("queue_wait_ms"),
        "summary": summary,
        "sample": [
            {"id": r.segment_id, "src": r.source_text, "dst": r.translated_text}
            for r in results[:2] + results[-2:]
        ],
    }


def scan_key(out_root: Path) -> dict:
    key = (os.getenv("DEEPL_AUTH_KEY") or os.getenv("DEEPL_API_KEY") or "").strip()
    if not key:
        return {"ok": True, "leaks": []}
    leaks = []
    for path in out_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".json", ".jsonl", ".txt", ".md", ".log"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if key in text:
            leaks.append(str(path.relative_to(out_root)))
    return {"ok": len(leaks) == 0, "leaks": leaks}


def main() -> int:
    ts = _utc()
    out_root = ROOT / "troubleshooting" / f"translation_beta_repair{ts}"
    out_root.mkdir(parents=True, exist_ok=True)
    evidence = out_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)

    baseline = (
        ROOT
        / "troubleshooting"
        / "validation"
        / "translation_beta"
        / "JAPANESE_FREEZE_BASELINE.json"
    )
    freeze = verify_japanese_freeze(baseline)
    _write(out_root / "JAPANESE_FREEZE_VERIFICATION.json", freeze)

    en = run_english_no_diarization(out_root)
    counters = test_counter_semantics(evidence)
    _write(out_root / "TRANSLATION_COUNTER_SEMANTICS_VALIDATION.json", counters)

    order = test_order(evidence)
    _write(out_root / "TRANSLATION_ORDER_VALIDATION.json", order)

    graceful = test_graceful_shutdown(evidence)
    _write(out_root / "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION.json", graceful)

    timeout = test_timeout_handling(evidence)
    _write(out_root / "TRANSLATION_TIMEOUT_HANDLING_VALIDATION.json", timeout)

    paced = test_paced(evidence)
    _write(out_root / "TRANSLATION_PACED_TEST.json", paced)

    burst = test_burst(evidence)
    _write(out_root / "TRANSLATION_BURST_TEST.json", burst)

    latency = test_latency_fields(burst, paced)
    _write(out_root / "TRANSLATION_LATENCY_VALIDATION.json", latency)

    integration = test_live_integration(evidence)
    _write(out_root / "TRANSLATION_LIVE_INTEGRATION_VALIDATION.json", integration)

    # Real API only once after deterministic tests
    det_ok = all(
        x.get(k) == "PASSED"
        for x, k in (
            (counters, "TRANSLATION_COUNTER_SEMANTICS_VALIDATION"),
            (order, "TRANSLATION_ORDER_VALIDATION"),
            (graceful, "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION"),
            (timeout, "TRANSLATION_TIMEOUT_HANDLING_VALIDATION"),
            (paced, "TRANSLATION_PACED_TEST"),
            (burst, "TRANSLATION_BURST_TEST"),
            (latency, "TRANSLATION_LATENCY_VALIDATION"),
            (integration, "TRANSLATION_LIVE_INTEGRATION_VALIDATION"),
        )
    )
    if det_ok:
        smoke = test_smoke(evidence)
    else:
        smoke = {
            "TRANSLATION_SMOKE_TEST": "FAILED",
            "ok": False,
            "reason": "deterministic_tests_failed_smoke_skipped",
        }
    _write(out_root / "TRANSLATION_SMOKE_TEST.json", smoke)

    # Copy smoke events/summary
    smoke_dir = evidence / "smoke"
    if (smoke_dir / "translation_events.jsonl").exists():
        (out_root / "translation_events.jsonl").write_text(
            (smoke_dir / "translation_events.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    else:
        # fallback burst events
        burst_events = evidence / "burst" / "translation_events.jsonl"
        if burst_events.exists():
            (out_root / "translation_events.jsonl").write_text(
                burst_events.read_text(encoding="utf-8"), encoding="utf-8"
            )
    summary_src = smoke_dir / "translation_summary.json"
    if not summary_src.exists():
        summary_src = evidence / "burst" / "translation_summary.json"
    if summary_src.exists():
        (out_root / "translation_summary.json").write_text(
            summary_src.read_text(encoding="utf-8"), encoding="utf-8"
        )

    keyscan = scan_key(out_root)
    lang_ok = (
        get_deepl_source_code("ja") == "JA"
        and target_for_source("ja") == "EN-US"
        and get_deepl_source_code("en") == "EN"
        and target_for_source("en") == "JA"
    )
    speaker_ok = str(UI_SPEAKER_LABEL).startswith("Speaker")

    # Consistency placeholder then finalize after overall
    raw_counters = dict(counters.get("counters") or {})
    raw_counters["SOURCE_TRANSCRIPT_MODIFICATIONS"] = int(
        raw_counters.get(
            "SOURCE_TRANSCRIPT_MODIFICATIONS",
            raw_counters.get("source_transcript_modifications", 0),
        )
        or 0
    )
    # Prefer smoke summary for source-mod invariant when available
    smoke_summary = smoke.get("summary") or {}
    if "SOURCE_TRANSCRIPT_MODIFICATIONS" in smoke_summary:
        raw_counters["SOURCE_TRANSCRIPT_MODIFICATIONS"] = int(
            smoke_summary.get("SOURCE_TRANSCRIPT_MODIFICATIONS") or 0
        )

    evidence_pack = {
        "JAPANESE_FREEZE_VERIFICATION": freeze.get("JAPANESE_FREEZE_VERIFICATION"),
        "ENGLISH_NO_DIARIZATION_VALIDATION": en.get(
            "ENGLISH_NO_DIARIZATION_VALIDATION"
        ),
        "generic_speaker_label_enabled": speaker_ok,
        "ja_to_en_ok": bool(smoke.get("ja_to_en")),
        "en_to_ja_ok": bool(smoke.get("en_to_ja")),
        "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION": graceful.get(
            "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION"
        ),
        "TRANSLATION_TIMEOUT_HANDLING_VALIDATION": timeout.get(
            "TRANSLATION_TIMEOUT_HANDLING_VALIDATION"
        ),
        "TRANSLATION_LIVE_INTEGRATION_VALIDATION": integration.get(
            "TRANSLATION_LIVE_INTEGRATION_VALIDATION"
        ),
        "TRANSLATION_SMOKE_TEST": smoke.get("TRANSLATION_SMOKE_TEST"),
        "api_key_absent_from_logs": keyscan.get("ok"),
        "language_map_ok": lang_ok,
        "true_e2e_latency_reported": bool(latency.get("true_e2e_latency_reported")),
        "counters": raw_counters,
        "graceful_shutdown_summary": graceful.get("summary") or {},
    }

    # Write preliminary files for consistency checker
    decision_lines = []
    overall_pre = evaluate_overall_acceptance(
        {**evidence_pack, "TRANSLATION_REPORT_CONSISTENCY": "PENDING"}
    )

    # Build consistency across written artifacts
    consistency_failures = []
    gsum = graceful.get("summary") or {}
    if graceful.get("TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION") == "PASSED":
        if int(gsum.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", -1)) != 0:
            consistency_failures.append("graceful_passed_but_pending_nonzero")
        if gsum.get("UNFINISHED_TRANSLATION_SEGMENT_IDS"):
            consistency_failures.append("graceful_passed_but_unfinished")
    if timeout.get("TRANSLATION_TIMEOUT_HANDLING_VALIDATION") == "PASSED":
        tsum = timeout.get("summary") or {}
        if int(tsum.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", 0)) == 0 and not (
            tsum.get("UNFINISHED_TRANSLATION_SEGMENT_IDS")
        ):
            consistency_failures.append("timeout_passed_but_claimed_clean_drain")
    csum = counters.get("summary_excerpt") or {}
    if int(csum.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM", -1)) != 0:
        consistency_failures.append("interim_provider_requests_nonzero")
    if int(csum.get("DUPLICATE_TRANSLATION_REQUESTS_SENT", -1)) != 0:
        consistency_failures.append("duplicate_provider_requests_nonzero")

    consistency = {
        "TRANSLATION_REPORT_CONSISTENCY": "PASSED"
        if not consistency_failures
        else "FAILED",
        "failures": consistency_failures,
    }
    _write(out_root / "TRANSLATION_REPORT_CONSISTENCY.json", consistency)

    evidence_pack["TRANSLATION_REPORT_CONSISTENCY"] = consistency[
        "TRANSLATION_REPORT_CONSISTENCY"
    ]
    overall = evaluate_overall_acceptance(evidence_pack)

    # Also fail overall if deterministic/counter/order/latency/burst/paced failed
    for label, val in (
        ("counters", counters.get("TRANSLATION_COUNTER_SEMANTICS_VALIDATION")),
        ("order", order.get("TRANSLATION_ORDER_VALIDATION")),
        ("paced", paced.get("TRANSLATION_PACED_TEST")),
        ("burst", burst.get("TRANSLATION_BURST_TEST")),
        ("latency", latency.get("TRANSLATION_LATENCY_VALIDATION")),
    ):
        if val != "PASSED":
            overall["OVERALL_ACCEPTANCE"] = "FAILED"
            overall["failures"] = list(overall.get("failures") or []) + [label]

    repair_payload = {
        "generated_at_utc": ts,
        "TRANSLATION_BETA_REPAIR_VALIDATION": overall["OVERALL_ACCEPTANCE"],
        "OVERALL_ACCEPTANCE": overall["OVERALL_ACCEPTANCE"],
        "failures": overall.get("failures"),
        "computed_from_evidence": True,
        "japanese_freeze": freeze.get("JAPANESE_FREEZE_VERIFICATION"),
        "english_no_diarization": en.get("ENGLISH_NO_DIARIZATION_VALIDATION"),
        "counters": counters,
        "graceful_shutdown": graceful.get("TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION"),
        "timeout_handling": timeout.get("TRANSLATION_TIMEOUT_HANDLING_VALIDATION"),
        "latency": latency,
        "paced": paced.get("TRANSLATION_PACED_TEST"),
        "burst": burst.get("TRANSLATION_BURST_TEST"),
        "integration": integration.get("TRANSLATION_LIVE_INTEGRATION_VALIDATION"),
        "smoke": smoke.get("TRANSLATION_SMOKE_TEST"),
        "order": order.get("TRANSLATION_ORDER_VALIDATION"),
        "consistency": consistency.get("TRANSLATION_REPORT_CONSISTENCY"),
        "generic_speaker_label": UI_SPEAKER_LABEL,
        "ENGLISH_DIARIZATION_ENABLED": ENGLISH_DIARIZATION_ENABLED,
    }
    _write(out_root / "TRANSLATION_BETA_REPAIR_VALIDATION.json", repair_payload)

    # Decision + Cursor final report (bound to this package only)
    e2e = (smoke.get("translation_end_to_end_ms") or latency.get("burst_e2e_p50") and {
        "p50": latency.get("burst_e2e_p50"),
        "p95": latency.get("burst_e2e_p95"),
        "max": latency.get("burst_e2e_max"),
    } or {})
    if smoke.get("translation_end_to_end_ms"):
        e2e = smoke["translation_end_to_end_ms"]
    prov = smoke.get("provider_latency_ms") or {
        "p50": latency.get("burst_provider_p50"),
        "p95": latency.get("burst_provider_p95"),
    }
    gsum = graceful.get("summary") or {}
    c = counters.get("counters") or {}

    decision = "\n".join(
        [
            "ALPHA TRANSLATION BETA REPAIR DECISION REPORT",
            f"generated_at_utc={ts}",
            f"package={out_root.name}",
            f"INTERIM_SUBMISSIONS_REJECTED={c.get('INTERIM_SUBMISSIONS_REJECTED')}",
            f"TRANSLATION_REQUESTS_SENT_FROM_INTERIM={c.get('TRANSLATION_REQUESTS_SENT_FROM_INTERIM')}",
            f"DUPLICATE_SUBMISSIONS_REJECTED={c.get('DUPLICATE_SUBMISSIONS_REJECTED')}",
            f"DUPLICATE_TRANSLATION_REQUESTS_SENT={c.get('DUPLICATE_TRANSLATION_REQUESTS_SENT')}",
            f"graceful_pending_queue={gsum.get('TRANSLATION_QUEUE_PENDING_AT_EXIT')}",
            f"graceful_in_flight={gsum.get('TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT')}",
            f"graceful_ordering_buffer={gsum.get('ORDERING_BUFFER_PENDING_AT_EXIT')}",
            f"graceful_unfinished={gsum.get('UNFINISHED_TRANSLATION_SEGMENT_IDS')}",
            f"timeout_handling={timeout.get('TRANSLATION_TIMEOUT_HANDLING_VALIDATION')}",
            f"provider_latency_p50_p95={prov.get('p50')}/{prov.get('p95')}",
            f"e2e_latency_p50_p95_max={e2e.get('p50')}/{e2e.get('p95')}/{e2e.get('max')}",
            f"ja_to_en={smoke.get('ja_to_en')}",
            f"en_to_ja={smoke.get('en_to_ja')}",
            f"japanese_freeze={freeze.get('JAPANESE_FREEZE_VERIFICATION')}",
            f"english_no_diarization={en.get('ENGLISH_NO_DIARIZATION_VALIDATION')}",
            f"speaker_label={UI_SPEAKER_LABEL}",
            f"report_consistency={consistency.get('TRANSLATION_REPORT_CONSISTENCY')}",
            f"OVERALL_ACCEPTANCE={overall['OVERALL_ACCEPTANCE']}",
            f"failures={overall.get('failures')}",
            "",
            "Notes:",
            "- Rejected interim/duplicate submissions are NOT provider requests.",
            "- Graceful shutdown PASS requires pending/in-flight/ordering/unfinished all zero.",
            "- Timeout handling PASS only proves bounded shutdown with unfinished IDs reported.",
            "- Provider latency and end-to-end latency are reported separately.",
            "- This report is bound to translation_beta_repair only (not readiness-preflight).",
            "",
        ]
    )
    (out_root / "TRANSLATION_BETA_REPAIR_DECISION_REPORT.txt").write_text(
        decision, encoding="utf-8"
    )
    (out_root / "Cursor final report.txt").write_text(decision, encoding="utf-8")

    zip_dir = ROOT / "troubleshooting" / "translation_beta_repair"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"ALPHA_TRANSLATION_BETA_REPAIR_{ts}.zip"

    manifest = {
        "timestamp": ts,
        "package_dir": str(out_root),
        "zip_path": str(zip_path),
        "acceptance": overall["OVERALL_ACCEPTANCE"] == "PASSED",
        "OVERALL_ACCEPTANCE": overall["OVERALL_ACCEPTANCE"],
        "files": sorted(p.name for p in out_root.iterdir() if p.is_file()),
        "computed_from_evidence": True,
    }
    # Hard rule: manifest acceptance must match overall
    if overall["OVERALL_ACCEPTANCE"] != "PASSED":
        manifest["acceptance"] = False
    _write(out_root / "implementation_manifest.json", manifest)

    # Re-run consistency including decision/manifest agreement
    cons2_fail = list(consistency_failures)
    if manifest["acceptance"] and overall["OVERALL_ACCEPTANCE"] != "PASSED":
        cons2_fail.append("manifest_acceptance_true_but_overall_failed")
    if (not manifest["acceptance"]) and overall["OVERALL_ACCEPTANCE"] == "PASSED":
        cons2_fail.append("manifest_acceptance_false_but_overall_passed")
    # Decision report must contain same overall
    if f"OVERALL_ACCEPTANCE={overall['OVERALL_ACCEPTANCE']}" not in decision:
        cons2_fail.append("decision_report_mismatch")
    consistency = {
        "TRANSLATION_REPORT_CONSISTENCY": "PASSED" if not cons2_fail else "FAILED",
        "failures": cons2_fail,
    }
    _write(out_root / "TRANSLATION_REPORT_CONSISTENCY.json", consistency)
    if consistency["TRANSLATION_REPORT_CONSISTENCY"] != "PASSED":
        overall = evaluate_overall_acceptance(
            {**evidence_pack, "TRANSLATION_REPORT_CONSISTENCY": "FAILED"}
        )
        repair_payload["OVERALL_ACCEPTANCE"] = overall["OVERALL_ACCEPTANCE"]
        repair_payload["TRANSLATION_BETA_REPAIR_VALIDATION"] = overall[
            "OVERALL_ACCEPTANCE"
        ]
        repair_payload["failures"] = overall.get("failures")
        repair_payload["consistency"] = "FAILED"
        _write(out_root / "TRANSLATION_BETA_REPAIR_VALIDATION.json", repair_payload)
        manifest["acceptance"] = False
        manifest["OVERALL_ACCEPTANCE"] = overall["OVERALL_ACCEPTANCE"]
        _write(out_root / "implementation_manifest.json", manifest)

    key = (os.getenv("DEEPL_AUTH_KEY") or "").strip()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in out_root.rglob("*"):
            if not path.is_file():
                continue
            if path.name.lower() in {".env", "credentials.json"}:
                continue
            data = path.read_bytes()
            if key and key.encode("utf-8") in data:
                continue
            # Exclude old readiness naming
            if "readiness" in path.name.lower():
                continue
            zf.write(path, arcname=str(path.relative_to(out_root.parent)))

    (out_root / "ZIP_PATH.txt").write_text(str(zip_path), encoding="utf-8")

    # Final console metrics for Cursor response assembly
    print(
        "TRANSLATION_BETA_REPAIR_VALIDATION =",
        overall["OVERALL_ACCEPTANCE"],
    )
    print("package=", out_root)
    print("zip=", zip_path)
    print(
        "METRICS",
        json.dumps(
            {
                "interim_rejected": c.get("INTERIM_SUBMISSIONS_REJECTED"),
                "interim_sent": c.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM"),
                "dup_rejected": c.get("DUPLICATE_SUBMISSIONS_REJECTED"),
                "dup_sent": c.get("DUPLICATE_TRANSLATION_REQUESTS_SENT"),
                "graceful_pending": gsum.get("TRANSLATION_QUEUE_PENDING_AT_EXIT"),
                "graceful_inflight": gsum.get("TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT"),
                "graceful_ordering": gsum.get("ORDERING_BUFFER_PENDING_AT_EXIT"),
                "graceful_unfinished": gsum.get("UNFINISHED_TRANSLATION_SEGMENT_IDS"),
                "timeout": timeout.get("TRANSLATION_TIMEOUT_HANDLING_VALIDATION"),
                "provider_p50": prov.get("p50"),
                "provider_p95": prov.get("p95"),
                "e2e_p50": e2e.get("p50"),
                "e2e_p95": e2e.get("p95"),
                "e2e_max": e2e.get("max"),
                "ja_en": smoke.get("ja_to_en"),
                "en_ja": smoke.get("en_to_ja"),
                "freeze": freeze.get("JAPANESE_FREEZE_VERIFICATION"),
                "en_nodiar": en.get("ENGLISH_NO_DIARIZATION_VALIDATION"),
                "speaker": UI_SPEAKER_LABEL,
                "consistency": consistency.get("TRANSLATION_REPORT_CONSISTENCY"),
                "overall": overall["OVERALL_ACCEPTANCE"],
                "zip": str(zip_path),
            },
            ensure_ascii=False,
        ),
    )
    return 0 if overall["OVERALL_ACCEPTANCE"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
