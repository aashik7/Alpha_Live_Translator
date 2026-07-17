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
MULTIDOMAIN_VERSION = "3.3.5.5.8.5.26.2"
FROZEN_INFRASTRUCTURE = "3.3.5.5.8.5.25.3.3.2.8"
NORMALIZATION_RULES_VERSION = "mdg_meaning_equiv_v1"

REFERENCE_REL = Path(
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1.txt"
)
TRUTH_REL = Path(
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1_truth.json"
)
ISOLATION_POLICY_REL = Path(
    "troubleshooting/accuracy_benchmark/multidomain_gate/REFERENCE_ISOLATION_POLICY.json"
)
TEST01_PROFILE_STATUS_REL = Path(
    "troubleshooting/accuracy_benchmark/multidomain_gate/test01_meeting_context_status.json"
)

_lock = threading.Lock()
_next_delivery_chunk_id = 1
_pending_ids: deque[int] = deque()
_evidence_overflow_count = 0
_queued_meta: dict[int, dict[str, Any]] = {}
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
    global _active, _next_delivery_chunk_id, _pending_ids, _queued_meta
    with _lock:
        _active = True
        _next_delivery_chunk_id = 1
        _pending_ids = deque()
        _queued_meta = {}
    _post_event(
        {
            "event": "benchmark_mode_started",
            "run_id": run_id,
            "profile": ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
        }
    )


def deactivate_benchmark_evidence(*, run_id: str = "") -> None:
    global _active
    _post_event({"event": "benchmark_mode_stopped", "run_id": run_id})
    with _lock:
        _active = False


def record_lifecycle_event(event: str, **extra: Any) -> None:
    payload = {"event": event, **extra}
    try:
        from alpha.utils.run_identity import get_run_id

        payload.setdefault("run_id", str(get_run_id() or ""))
    except Exception:
        payload.setdefault("run_id", "")
    _post_event(payload)


def note_normalized_chunk_queued(
    chunk: Any,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> int:
    """Assign delivery_chunk_id immediately before queue put. Does not copy/alter bytes."""
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
        _queued_meta[chunk_id] = {
            "frame_count": frame_count,
            "byte_count": byte_count,
            "sample_rate": sample_rate,
            "channels": channels,
            "queued_monotonic_ns": time.monotonic_ns(),
        }
    run_id = ""
    try:
        from alpha.utils.run_identity import get_run_id

        run_id = str(get_run_id() or "")
    except Exception:
        pass
    _post_event(
        {
            "event": "normalized_chunk_queued",
            "delivery_chunk_id": chunk_id,
            "run_id": run_id,
            "frame_count": frame_count,
            "byte_count": byte_count,
            "sample_rate": sample_rate,
            "channels": channels,
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
    run_id = ""
    try:
        from alpha.utils.run_identity import get_run_id

        run_id = str(get_run_id() or "")
    except Exception:
        pass
    _post_event(
        {
            "event": "normalized_chunk_sent",
            "delivery_chunk_id": int(delivery_chunk_id),
            "run_id": run_id,
            "frame_count": int(frame_count),
            "byte_count": int(byte_count),
            "sample_rate": int(sample_rate),
            "channels": int(channels),
            "send_result": send_result,
        }
    )


def note_normalized_chunk_send_failed(
    delivery_chunk_id: Optional[int],
    *,
    error_class: str,
    error_message_sanitized: str,
) -> None:
    run_id = ""
    try:
        from alpha.utils.run_identity import get_run_id

        run_id = str(get_run_id() or "")
    except Exception:
        pass
    _post_event(
        {
            "event": "normalized_chunk_send_failed",
            "delivery_chunk_id": delivery_chunk_id,
            "run_id": run_id,
            "error_class": error_class,
            "error_message_sanitized": str(error_message_sanitized)[:200],
        }
    )


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


def build_reference_isolation_policy() -> dict[str, Any]:
    return {
        "policy_version": "multidomain_gate_85262",
        "rules": [
            "The application child process must not receive the reference path.",
            "The application child process must not receive the truth metadata path.",
            "The application child environment must not contain reference text.",
            "The application child environment must not contain a reference SHA.",
            "The reference must not be opened until the application process exits.",
            "Benchmark scoring modules must not be imported by the live application.",
            "Benchmark truth metadata must be used only after runtime exit.",
            "Deepgram request construction must not import benchmark files.",
        ],
        "reference_path": str(REFERENCE_REL).replace("\\", "/"),
        "truth_metadata_path": str(TRUTH_REL).replace("\\", "/"),
        "orchestrator_may_receive_paths": True,
        "orchestrator_may_open_before_exit": False,
    }


def build_truth_metadata_template() -> dict[str, Any]:
    return {
        "benchmark_id": "multidomain_meeting_v1",
        "runtime_usage_allowed": False,
        "deepgram_usage_allowed": False,
        "correction_usage_allowed": False,
        "participant_and_person_names": [
            "田中健",
            "佐藤美咲",
            "鈴木大輔",
            "高橋彩",
            "山本部長",
            "斉藤課長",
            "佐藤主任",
            "小林さん",
            "中村恵子",
        ],
        "company_names": [
            "アルファソリューションズ株式会社",
            "東都物流株式会社",
            "青葉商事株式会社",
            "北星テクノロジー株式会社",
            "株式会社ネクストワークス",
        ],
        "it_terms": [
            "API",
            "CSV",
            "JSON",
            "SSO",
            "MFA",
            "Webhook",
            "CPU",
            "CRM",
            "SLA",
            "シングルサインオン",
            "多要素認証",
            "バックグラウンド処理",
            "タイムアウト",
            "回帰テスト",
            "外部ライブラリ",
            "クラウド環境",
        ],
        "sales_terms": [
            "初回相談",
            "提案書",
            "価格交渉",
            "社内承認",
            "契約手続き",
            "年間契約金額",
            "初期費用",
            "値引き",
            "契約期間",
            "月額利用料",
            "見積書",
            "個別見積もり",
        ],
        "marketing_terms": [
            "検索広告",
            "SNS広告",
            "オンラインセミナー",
            "表示回数",
            "クリック数",
            "クリック率",
            "問い合わせ件数",
            "見込み客",
            "転換率",
            "A/Bテスト",
            "CPA",
            "ランディングページ",
        ],
        "general_business_terms": [
            "進捗率",
            "負荷テスト",
            "情報システム部",
            "営業企画部",
            "購買部",
            "プロジェクト管理ツール",
            "重要度",
            "一次回答",
            "経営会議",
        ],
        "numeric_entities": "extract_from_reference_after_runtime",
        "dates_times": "extract_from_reference_after_runtime",
        "money_percentages": "extract_from_reference_after_runtime",
    }


def scan_production_for_reference_leaks(project_root: Path) -> dict[str, Any]:
    """Fail if production runtime files embed multidomain reference/truth content."""
    roots = [
        project_root / "alpha",
        project_root / "main.py",
    ]
    forbidden_needles = [
        "multidomain_meeting_v1.txt",
        "multidomain_meeting_v1_truth.json",
    ]
    # Term arrays / correction tables that would prove memorization wiring
    forbidden_snippets = [
        '"participant_and_person_names"',
        "アルファソリューションズ株式会社",
        "BENCHMARK_CORRECTION_TABLE",
        "multidomain_term_array",
    ]
    allow_paths = {
        str((project_root / "alpha" / "utils" / "multidomain_gate_evidence.py").resolve()),
    }
    hits: list[dict[str, str]] = []
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(root.rglob("*.py"))
    for path in files:
        if str(path.resolve()) in allow_paths:
            continue
        # Scoring scripts live at project root — not under alpha; skip gate scripts
        name = path.name
        if name.startswith(("score_multidomain", "verify_multidomain", "run_multidomain", "prepare_multidomain", "regression_multidomain")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for needle in forbidden_needles + forbidden_snippets:
            if needle in text:
                hits.append({"path": str(path.relative_to(project_root)), "needle": needle})
    return {"ok": not hits, "hits": hits}


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


# Meaning-equivalent pairs (analysis-only; never mutates transcript files)
_MEANING_PAIRS: list[tuple[str, str]] = [
    ("120万円", "百二十万円"),
    ("3.2%", "三点二パーセント"),
    ("午前10時", "午前十時"),
    ("5,000件", "五千件"),
    ("API", "エーピーアイ"),
    ("CSV", "シーエスブイ"),
    ("SSO", "エスエスオー"),
    ("JSON", "ジェイソン"),
    ("MFA", "エムエフエー"),
]


def apply_meaning_equivalent(text: str) -> tuple[str, list[dict[str, str]]]:
    """Canonicalize known equivalent spellings for supplementary scoring only."""
    out = text
    applied: list[dict[str, str]] = []
    for a, b in _MEANING_PAIRS:
        if a in out and b not in out:
            out = out.replace(a, b)
            applied.append({"from": a, "to": b})
        elif b in out and a not in out:
            # Normalize both directions to the same canonical (right side)
            pass
        # Also map left->right if both present as variants across hyp/ref separately
    # Second pass: map either form to canonical right
    for a, b in _MEANING_PAIRS:
        if a in out:
            out = out.replace(a, b)
            if not any(p["from"] == a and p["to"] == b for p in applied):
                applied.append({"from": a, "to": b})
    return out, applied


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
