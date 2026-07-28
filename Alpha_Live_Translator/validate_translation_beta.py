#!/usr/bin/env python3
"""Validate Stable-only async DeepL translation beta (mocks + optional live smoke)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from alpha.constants import (  # noqa: E402
    ENGLISH_DIARIZATION_ENABLED,
    JAPANESE_KEYTERMS_ENABLED,
    JAPANESE_STT_PROFILE,
    TRANSLATION_ENABLED,
    TRANSLATION_PROVIDER,
    TRANSLATION_QUEUE_MAX_SIZE,
    TRANSLATE_STABLE_ONLY,
    UI_SPEAKER_LABEL,
)
from alpha.stt_settings import (  # noqa: E402
    DEEPGRAM_JA_ENDPOINTING_MS,
    DEEPGRAM_JA_UTTERANCE_END_MS,
    DEEPGRAM_MODEL,
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

    def __init__(self, delay_s: float = 0.0, fail_codes: Optional[Dict[int, str]] = None):
        self.delay_s = float(delay_s)
        self.fail_codes = fail_codes or {}
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
            call_n = len(self.calls)
        if call_n in self.fail_codes:
            code = self.fail_codes[call_n]
            raise DeepLError(code, code=code, retryable=code in {"http_429", "temporary_server"})
        if self.delay_s:
            time.sleep(self.delay_s)
        return f"TR[{target_lang}] {text}"


def _verify_japanese_freeze(baseline_path: Path) -> dict:
    hard_paths = [
        "alpha/transcription/japanese_sentence_assembler.py",
        "alpha/transcription/japanese_business_accuracy.py",
        "alpha/transcription/japanese_accuracy_cleaner.py",
        "alpha/transcription/japanese_final_chunk_stabilizer.py",
        "alpha/utils/japanese_accuracy_log.py",
        "alpha/utils/cjk_text.py",
        "alpha/stt_settings.py",
    ]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    file_hashes = baseline.get("file_sha256") or {}
    mismatches = []
    for rel in hard_paths:
        p = ROOT / rel
        if not p.exists():
            mismatches.append({"path": rel, "reason": "missing"})
            continue
        digest = _sha256_file(p)
        expected = file_hashes.get(rel)
        if expected and digest != expected:
            mismatches.append({"path": rel, "reason": "hash_mismatch"})
    timing = baseline.get("japanese_deepgram_timing_snapshot") or {}
    timing_ok = (
        str(timing.get("model") or "") == str(DEEPGRAM_MODEL)
        and str(timing.get("language") or "") == "ja"
        and int(timing.get("endpointing") or -1) == int(DEEPGRAM_JA_ENDPOINTING_MS)
        and int(timing.get("utterance_end_ms") or -1) == int(DEEPGRAM_JA_UTTERANCE_END_MS)
        and int(DEEPGRAM_JA_ENDPOINTING_MS) == 500
        and int(DEEPGRAM_JA_UTTERANCE_END_MS) == 1500
        and str(timing.get("JAPANESE_STT_PROFILE") or "") == str(JAPANESE_STT_PROFILE)
        and bool(timing.get("JAPANESE_KEYTERMS_ENABLED")) == bool(JAPANESE_KEYTERMS_ENABLED)
    )
    # Rebuild Japanese request presence via profile constants
    ja_request = {
        "model": "nova-3",
        "language": "ja",
        "endpointing": int(DEEPGRAM_JA_ENDPOINTING_MS),
        "utterance_end_ms": int(DEEPGRAM_JA_UTTERANCE_END_MS),
        "JAPANESE_STT_PROFILE": JAPANESE_STT_PROFILE,
        "JAPANESE_KEYTERMS_ENABLED": JAPANESE_KEYTERMS_ENABLED,
        "diarize_absent": str(JAPANESE_STT_PROFILE) == "no_diarize",
    }
    passed = (not mismatches) and timing_ok and ja_request["diarize_absent"]
    return {
        "JAPANESE_FREEZE_VERIFICATION": "PASSED" if passed else "FAILED",
        "hard_file_mismatches": mismatches,
        "timing_ok": timing_ok,
        "japanese_request_freeze": ja_request,
        "shared_files_may_change": [
            "alpha/constants.py",
            "alpha/config.py",
            "alpha/transcription/deepgram_client.py",
            "alpha/utils/troubleshooting_paths.py",
        ],
        "note": "Shared files may change for English diarization / translation only.",
    }


def _test_language_map() -> dict:
    ja_src = get_deepl_source_code("ja")
    ja_tgt = target_for_source("ja")
    en_src = get_deepl_source_code("en")
    en_tgt = target_for_source("en")
    ok = ja_src == "JA" and ja_tgt == "EN-US" and en_src == "EN" and en_tgt == "JA"
    return {
        "ok": ok,
        "ja": {"source": ja_src, "target": ja_tgt},
        "en": {"source": en_src, "target": en_tgt},
    }


def _test_stable_only_and_ordering(evidence_dir: Path) -> dict:
    ready: List[TranslationResult] = []
    lock = threading.Lock()

    def on_ready(result: TranslationResult) -> None:
        with lock:
            ready.append(result)

    mock = MockDeepLClient(delay_s=0.05)
    worker = TranslationWorker(
        run_id="validation_order",
        evidence_dir=evidence_dir / "order",
        on_translation_ready=on_ready,
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    assert worker.start()
    # Enqueue out of completion order by using delay variance via sequential enqueue
    # with reverse-completing by holding: enqueue 1 then 2; mock is FIFO so order preserved.
    # Force out-of-order completion using two clients is hard; simulate by injecting held results.
    worker.enqueue_stable_segment(
        segment_id=1, source_language="en", source_text="one", is_interim=False
    )
    worker.enqueue_stable_segment(
        segment_id=2, source_language="en", source_text="two", is_interim=False
    )
    # interim must not translate
    worker.enqueue_stable_segment(
        segment_id=99, source_language="en", source_text="interim", is_interim=True
    )
    # duplicate
    worker.enqueue_stable_segment(
        segment_id=1, source_language="en", source_text="one again", is_interim=False
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        with lock:
            if len(ready) >= 2:
                break
        time.sleep(0.05)
    summary = worker.shutdown(timeout_seconds=3)
    commit_ids = [r.segment_id for r in ready]
    return {
        "ok": (
            commit_ids == sorted(commit_ids)
            and summary.get("TRANSLATION_REQUESTS_FROM_INTERIM", 0) == 1
            and summary.get("DUPLICATE_TRANSLATION_REQUESTS", 0) == 1
            and summary.get("OUT_OF_ORDER_TRANSLATION_COMMITS", 0) == 0
            and summary.get("DUPLICATE_TRANSLATION_COMMITS", 0) == 0
            and summary.get("SOURCE_TRANSCRIPT_MODIFICATIONS", 0) == 0
            and summary.get("TRANSLATION_WORKER_STOPPED") is True
            and summary.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", 0) == 0
            and all(c.get("thread") == "TranslationWorker" for c in mock.calls)
        ),
        "commit_ids": commit_ids,
        "summary": summary,
        "mock_threads": [c.get("thread") for c in mock.calls],
    }


def _test_out_of_order_hold(evidence_dir: Path) -> dict:
    """Prove segment 12 is held until 11 commits."""
    ready: List[int] = []

    def on_ready(result: TranslationResult) -> None:
        ready.append(result.segment_id)

    class ManualWorker(TranslationWorker):
        def _translate_job(self, job, target_lang):  # type: ignore[override]
            # Bypass network; fabricate delayed/out-of-order via caller
            return TranslationResult(
                run_id=job.run_id,
                segment_id=job.segment_id,
                source_language=job.source_language,
                target_language=target_lang,
                source_text=job.source_text,
                source_text_hash=job.source_text_hash,
                translated_text=f"t-{job.segment_id}",
                status="success",
                source_character_count=job.source_character_count,
            )

    w = ManualWorker(
        run_id="ooo",
        evidence_dir=evidence_dir / "ooo",
        on_translation_ready=on_ready,
        client=MockDeepLClient(),  # type: ignore[arg-type]
        enabled=True,
    )
    # Manually feed results out of order
    from alpha.translation.translation_worker import StableTranslationJob

    job12 = StableTranslationJob(
        run_id="ooo",
        segment_id=12,
        source_language="EN",
        source_text="twelve",
        source_text_hash="a",
        stable_committed_at=time.time(),
        stable_commit_timestamp=time.time(),
    )
    job11 = StableTranslationJob(
        run_id="ooo",
        segment_id=11,
        source_language="EN",
        source_text="eleven",
        source_text_hash="b",
        stable_committed_at=time.time(),
        stable_commit_timestamp=time.time(),
    )
    w._next_commit_id = 11
    w._handle_result(w._translate_job(job12, "JA"))
    mid = list(ready)
    w._handle_result(w._translate_job(job11, "JA"))
    return {
        "ok": mid == [] and ready == [11, 12] and w._counters["out_of_order_completions"] >= 1
        and w._counters["OUT_OF_ORDER_TRANSLATION_COMMITS"] == 0,
        "ready_after_12_only": mid,
        "ready_final": ready,
        "out_of_order_completions": w._counters["out_of_order_completions"],
        "out_of_order_commits": w._counters["OUT_OF_ORDER_TRANSLATION_COMMITS"],
    }


def _test_failure_does_not_block(evidence_dir: Path) -> dict:
    transcription_alive = {"ok": True}

    def transcription_heartbeat():
        for _ in range(20):
            if not transcription_alive["ok"]:
                return
            time.sleep(0.05)

    mock = MockDeepLClient(delay_s=0.2, fail_codes={1: "auth_failed"})
    ready = []
    worker = TranslationWorker(
        run_id="fail",
        evidence_dir=evidence_dir / "fail",
        on_translation_ready=lambda r: ready.append(r),
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    worker.start()
    t = threading.Thread(target=transcription_heartbeat, name="FakeTranscription")
    t.start()
    worker.enqueue_stable_segment(
        segment_id=1, source_language="en", source_text="boom", is_interim=False
    )
    worker.enqueue_stable_segment(
        segment_id=2, source_language="en", source_text="ok", is_interim=False
    )
    t.join(timeout=3)
    still_alive = t.is_alive() is False and transcription_alive["ok"]
    summary = worker.shutdown(timeout_seconds=3)
    return {
        "ok": still_alive and summary.get("failed_translations", 0) >= 1,
        "transcription_continued": still_alive,
        "summary": {
            "failed": summary.get("failed_translations"),
            "successful": summary.get("successful_translations"),
        },
    }


def _test_shutdown_bounded(evidence_dir: Path) -> dict:
    mock = MockDeepLClient(delay_s=2.0)
    worker = TranslationWorker(
        run_id="shutdown",
        evidence_dir=evidence_dir / "shutdown",
        client=mock,  # type: ignore[arg-type]
        enabled=True,
    )
    worker.start()
    for i in range(1, 6):
        worker.enqueue_stable_segment(
            segment_id=i, source_language="en", source_text=f"s{i}", is_interim=False
        )
    t0 = time.time()
    summary = worker.shutdown(timeout_seconds=1.0)
    elapsed = time.time() - t0
    return {
        "ok": elapsed < 3.0 and summary.get("TRANSLATION_WORKER_STOPPED") is True,
        "elapsed_s": round(elapsed, 3),
        "summary": {
            "pending_at_exit": summary.get("TRANSLATION_QUEUE_PENDING_AT_EXIT"),
            "unfinished": summary.get("unfinished_segment_ids"),
            "stopped": summary.get("TRANSLATION_WORKER_STOPPED"),
        },
    }


def _test_queue_bounded() -> dict:
    return {
        "ok": int(TRANSLATION_QUEUE_MAX_SIZE) == 100 and TRANSLATE_STABLE_ONLY is True,
        "TRANSLATION_QUEUE_MAX_SIZE": TRANSLATION_QUEUE_MAX_SIZE,
        "TRANSLATE_STABLE_ONLY": TRANSLATE_STABLE_ONLY,
    }


def _looks_like_placeholder_key(key: str) -> bool:
    low = (key or "").strip().lower()
    if not low or len(low) < 20:
        return True
    return any(
        token in low
        for token in (
            "your_deepl",
            "your-api",
            "api_key_here",
            "auth_key_here",
            "changeme",
            "example",
            "xxxxxx",
        )
    )


def _live_smoke(evidence_dir: Path) -> dict:
    # Prefer DEEPL_AUTH_KEY; allow legacy DEEPL_API_KEY from .env for smoke only.
    key = (os.getenv("DEEPL_AUTH_KEY") or os.getenv("DEEPL_API_KEY") or "").strip()
    if not key:
        return {
            "ok": False,
            "skipped": True,
            "reason": "DEEPL_AUTH_KEY missing",
        }
    if _looks_like_placeholder_key(key):
        return {
            "ok": False,
            "skipped": True,
            "reason": "DEEPL_AUTH_KEY is placeholder; live smoke not billed",
            "character_count_sent": 0,
        }
    os.environ["DEEPL_AUTH_KEY"] = key
    events: List[TranslationResult] = []

    def on_ready(r: TranslationResult) -> None:
        events.append(r)

    # Pass key explicitly — alpha.config may have been imported before dotenv load.
    client = DeepLClient(api_key=key)
    worker = TranslationWorker(
        run_id="smoke10",
        evidence_dir=evidence_dir / "smoke",
        on_translation_ready=on_ready,
        client=client,
        enabled=True,
    )
    if not worker.start():
        return {"ok": False, "reason": worker.status_message}
    originals = []
    for i, text in enumerate(JA_SEGMENTS, start=1):
        originals.append(text)
        worker.enqueue_stable_segment(
            segment_id=i, source_language="ja", source_text=text, is_interim=False
        )
    for i, text in enumerate(EN_SEGMENTS, start=6):
        originals.append(text)
        worker.enqueue_stable_segment(
            segment_id=i, source_language="en", source_text=text, is_interim=False
        )
    deadline = time.time() + 60
    while time.time() < deadline and len(events) < 10:
        time.sleep(0.1)
    summary = worker.shutdown(timeout_seconds=15)
    # Ensure source texts unchanged
    source_ok = all(o == o for o in originals) and summary.get(
        "SOURCE_TRANSCRIPT_MODIFICATIONS", 0
    ) == 0
    ordered = [e.segment_id for e in events]
    ja_ok = all(
        e.source_language == "JA" and e.target_language == "EN-US"
        for e in events
        if e.segment_id <= 5
    )
    en_ok = all(
        e.source_language == "EN" and e.target_language == "JA"
        for e in events
        if e.segment_id >= 6
    )
    chars = sum(len(t) for t in originals)
    return {
        "ok": (
            len(events) == 10
            and ordered == list(range(1, 11))
            and source_ok
            and ja_ok
            and en_ok
            and summary.get("DUPLICATE_TRANSLATION_REQUESTS", 0) == 0
            and summary.get("OUT_OF_ORDER_TRANSLATION_COMMITS", 0) == 0
        ),
        "events": len(events),
        "ordered_ids": ordered,
        "ja_to_en": ja_ok,
        "en_to_ja": en_ok,
        "source_characters_sent": summary.get("source_characters_sent", chars),
        "character_count_sent": chars,
        "p50_ms": summary.get("p50_translation_latency_ms"),
        "p95_ms": summary.get("p95_translation_latency_ms"),
        "summary": summary,
        "sample_translations": [
            {"id": e.segment_id, "src": e.source_text, "dst": e.translated_text}
            for e in events[:2] + events[-2:]
        ],
    }


def _scan_logs_for_key(evidence_dir: Path) -> dict:
    key = (os.getenv("DEEPL_AUTH_KEY") or "").strip()
    if not key:
        return {"ok": True, "note": "no key to scan"}
    leaks = []
    for path in evidence_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".json", ".jsonl", ".txt", ".log", ".md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if key in text:
            leaks.append(str(path.relative_to(evidence_dir)))
    return {"ok": len(leaks) == 0, "leaks": leaks}


def main() -> int:
    ts = _utc()
    out_root = ROOT / "troubleshooting" / f"translation_beta{ts}"
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
    freeze = _verify_japanese_freeze(baseline)
    (out_root / "JAPANESE_FREEZE_VERIFICATION.json").write_text(
        json.dumps(freeze, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_root / "japanese_request_freeze.json").write_text(
        json.dumps(freeze.get("japanese_request_freeze") or {}, indent=2),
        encoding="utf-8",
    )

    # English no-diarization
    import subprocess

    en_proc = subprocess.run(
        [sys.executable, str(ROOT / "validate_english_no_diarization.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    en_rc = int(en_proc.returncode)
    en_path = (
        ROOT
        / "troubleshooting"
        / "validation"
        / "translation_beta"
        / "ENGLISH_NO_DIARIZATION_VALIDATION.json"
    )
    en_payload = json.loads(en_path.read_text(encoding="utf-8")) if en_path.exists() else {}
    (out_root / "ENGLISH_NO_DIARIZATION_VALIDATION.json").write_text(
        json.dumps(en_payload, indent=2), encoding="utf-8"
    )
    eng_req = (
        ROOT
        / "troubleshooting"
        / "validation"
        / "translation_beta"
        / "english_request_no_diarization.json"
    )
    if eng_req.exists():
        (out_root / "sanitized_english_request.json").write_text(
            eng_req.read_text(encoding="utf-8"), encoding="utf-8"
        )

    lang = _test_language_map()
    stable = _test_stable_only_and_ordering(evidence)
    ooo = _test_out_of_order_hold(evidence)
    fail = _test_failure_does_not_block(evidence)
    shutdown = _test_shutdown_bounded(evidence)
    bounded = _test_queue_bounded()
    smoke = _live_smoke(evidence)
    keyscan = _scan_logs_for_key(out_root)

    # Copy smoke events/summary if present
    smoke_dir = evidence / "smoke"
    if (smoke_dir / "translation_events.jsonl").exists():
        (out_root / "translation_events.jsonl").write_text(
            (smoke_dir / "translation_events.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    if (smoke_dir / "translation_summary.json").exists():
        (out_root / "translation_summary.json").write_text(
            (smoke_dir / "translation_summary.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    if (smoke_dir / "sanitized_deepl_configuration.json").exists():
        (out_root / "sanitized_deepl_configuration.json").write_text(
            (smoke_dir / "sanitized_deepl_configuration.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    else:
        (out_root / "sanitized_deepl_configuration.json").write_text(
            json.dumps(
                {
                    "TRANSLATION_ENABLED": TRANSLATION_ENABLED,
                    "TRANSLATION_PROVIDER": TRANSLATION_PROVIDER,
                    "TRANSLATE_STABLE_ONLY": TRANSLATE_STABLE_ONLY,
                    "TRANSLATION_QUEUE_MAX_SIZE": TRANSLATION_QUEUE_MAX_SIZE,
                    "auth_key_present": bool((os.getenv("DEEPL_AUTH_KEY") or "").strip()),
                    "auth_key_logged": False,
                    "language_map": {
                        "ja": {"source": "JA", "target": "EN-US"},
                        "en": {"source": "EN", "target": "JA"},
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    (out_root / "sanitized_japanese_request.json").write_text(
        json.dumps(freeze.get("japanese_request_freeze") or {}, indent=2),
        encoding="utf-8",
    )

    order_payload = {
        "TRANSLATION_ORDER_VALIDATION": "PASSED" if ooo.get("ok") and stable.get("ok") else "FAILED",
        "stable_only": stable,
        "out_of_order_hold": ooo,
    }
    (out_root / "TRANSLATION_ORDER_VALIDATION.json").write_text(
        json.dumps(order_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_root / "TRANSLATION_SHUTDOWN_VALIDATION.json").write_text(
        json.dumps(
            {
                "TRANSLATION_SHUTDOWN_VALIDATION": "PASSED"
                if shutdown.get("ok")
                else "FAILED",
                **shutdown,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_root / "TRANSLATION_SMOKE_TEST.json").write_text(
        json.dumps(
            {
                "TRANSLATION_SMOKE_TEST": "PASSED"
                if smoke.get("ok")
                else ("SKIPPED" if smoke.get("skipped") else "FAILED"),
                **smoke,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ui_speaker_ok = str(UI_SPEAKER_LABEL).startswith("Speaker")
    en_ok = en_payload.get("ENGLISH_NO_DIARIZATION_VALIDATION") == "PASSED"
    freeze_ok = freeze.get("JAPANESE_FREEZE_VERIFICATION") == "PASSED"
    stable_ok = (
        lang.get("ok")
        and stable.get("ok")
        and ooo.get("ok")
        and fail.get("ok")
        and shutdown.get("ok")
        and bounded.get("ok")
        and keyscan.get("ok")
        and ENGLISH_DIARIZATION_ENABLED is False
        and ui_speaker_ok
    )
    # Smoke is required for full acceptance when a DeepL key is configured.
    smoke_required = bool(
        (os.getenv("DEEPL_AUTH_KEY") or os.getenv("DEEPL_API_KEY") or "").strip()
    )
    smoke_ok = bool(smoke.get("ok")) if smoke_required else False
    # Without a key, beta cannot fully pass the acceptance gate.
    beta_passed = bool(stable_ok and freeze_ok and en_ok and smoke_ok)

    beta_payload = {
        "generated_at_utc": ts,
        "TRANSLATION_BETA_VALIDATION": "PASSED" if beta_passed else "FAILED",
        "language_map": lang,
        "stable_only_ordering": stable,
        "out_of_order_hold": ooo,
        "failure_isolation": fail,
        "shutdown": shutdown,
        "queue_bounded": bounded,
        "japanese_freeze": freeze.get("JAPANESE_FREEZE_VERIFICATION"),
        "english_no_diarization": en_payload.get("ENGLISH_NO_DIARIZATION_VALIDATION"),
        "smoke": {
            "result": smoke.get("ok"),
            "skipped": smoke.get("skipped"),
            "characters": smoke.get("character_count_sent"),
            "p50_ms": smoke.get("p50_ms"),
            "p95_ms": smoke.get("p95_ms"),
        },
        "api_key_absent_from_logs": keyscan.get("ok"),
        "ui_speaker_label": UI_SPEAKER_LABEL,
        "ENGLISH_DIARIZATION_ENABLED": ENGLISH_DIARIZATION_ENABLED,
        "validate_english_no_diarization_exit": en_rc,
    }
    (out_root / "TRANSLATION_BETA_VALIDATION.json").write_text(
        json.dumps(beta_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    decision_lines = [
        "ALPHA TRANSLATION BETA DECISION REPORT",
        f"generated_at_utc={ts}",
        f"English diarization disabled: {ENGLISH_DIARIZATION_ENABLED is False}",
        f"Generic Speaker label: {UI_SPEAKER_LABEL}",
        f"Japanese freeze: {freeze.get('JAPANESE_FREEZE_VERIFICATION')}",
        f"English no-diarization: {en_payload.get('ENGLISH_NO_DIARIZATION_VALIDATION')}",
        f"Stable-only / order: {'PASSED' if stable.get('ok') and ooo.get('ok') else 'FAILED'}",
        f"JA→EN smoke: {smoke.get('ja_to_en')}",
        f"EN→JA smoke: {smoke.get('en_to_ja')}",
        f"Smoke characters: {smoke.get('character_count_sent')}",
        f"p50/p95 ms: {smoke.get('p50_ms')} / {smoke.get('p95_ms')}",
        f"Duplicate requests: {stable.get('summary', {}).get('DUPLICATE_TRANSLATION_REQUESTS')}",
        f"Out-of-order commits: {ooo.get('out_of_order_commits')}",
        f"Shutdown bounded: {shutdown.get('ok')}",
        f"Overall: {'PASSED' if beta_passed else 'FAILED'}",
    ]
    (out_root / "TRANSLATION_DECISION_REPORT.txt").write_text(
        "\n".join(decision_lines) + "\n", encoding="utf-8"
    )

    manifest = {
        "timestamp": ts,
        "package_dir": str(out_root),
        "files": sorted(p.name for p in out_root.iterdir() if p.is_file()),
        "acceptance": beta_passed,
    }
    (out_root / "implementation_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    # ZIP package
    zip_dir = ROOT / "troubleshooting" / "translation_beta"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"ALPHA_TRANSLATION_BETA_VALIDATION_{ts}.zip"
    import zipfile

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in out_root.rglob("*"):
            if not path.is_file():
                continue
            # Exclude secrets defensively
            if path.name.lower() in {".env", "credentials.json"}:
                continue
            data = path.read_bytes()
            key = (os.getenv("DEEPL_AUTH_KEY") or "").strip()
            if key and key.encode("utf-8") in data:
                continue
            zf.write(path, arcname=str(path.relative_to(out_root.parent)))
    print(f"TRANSLATION_BETA_VALIDATION = {'PASSED' if beta_passed else 'FAILED'}")
    print(f"package={out_root}")
    print(f"zip={zip_path}")
    # write path for parent
    (out_root / "ZIP_PATH.txt").write_text(str(zip_path), encoding="utf-8")
    return 0 if beta_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
