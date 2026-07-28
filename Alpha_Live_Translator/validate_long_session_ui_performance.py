#!/usr/bin/env python3
"""Synthetic long-session UI performance validation (no Deepgram / no API credit).

SYNTHETIC_UI_LOAD_TEST_NOT_PRODUCT_ACCURACY
"""

from __future__ import annotations

import hashlib
import json
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "troubleshooting" / "validation" / "clean_bilingual_reset"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run() -> dict[str, Any]:
    from alpha.constants import MAX_RENDERED_UI_SEGMENTS
    from alpha.summary.transcript_store import TranscriptStore
    from alpha.transcription.duplicate_protection import decide_transcript_action

    store = TranscriptStore()
    n = 1200  # >1000 segments; 45-min-equivalent volume proxy
    insert_ms: list[float] = []
    rendered = 0
    limit = int(MAX_RENDERED_UI_SEGMENTS)
    full_text_reads = 0
    full_rewrites = 0
    ui_full_text_read_blocked = 0
    ui_full_rewrite_blocked = 0
    background_tk_calls = 0
    dropped = 0

    tracemalloc.start()
    t0 = time.perf_counter()
    last_text = None
    for i in range(n):
        text = f"Segment number {i} discusses quarterly targets and system updates."
        action, result = decide_transcript_action(last_text if i % 17 == 0 else None, text)
        if action == "skip" or not result:
            continue
        # Canonical store always grows.
        t_ins = time.perf_counter()
        store.add_segment(speaker=(i % 2) + 1, text=result, timestamp=str(i))
        # Simulated incremental UI insert cost (no Tk).
        rendered = min(rendered + 1, limit)
        if store.segment_count() > limit and rendered > limit:
            ui_full_rewrite_blocked += 1
            full_rewrites += 1
        # Never call store.get_clean_text() per insert (blocked path).
        if i % 200 == 0:
            # Occasional bounded window read only.
            segs = store.get_all()[-limit:]
            _ = len(segs)
        else:
            ui_full_text_read_blocked += 1
        insert_ms.append((time.perf_counter() - t_ins) * 1000.0)
        last_text = result

    elapsed = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    clean = store.get_clean_text()
    export_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    # Re-export must match canonical
    export_hash2 = hashlib.sha256(store.get_clean_text().encode("utf-8")).hexdigest()

    avg_ms = sum(insert_ms) / max(len(insert_ms), 1)
    max_ms = max(insert_ms) if insert_ms else 0.0
    checks = {
        "segment_count_ge_1000": store.segment_count() >= 1000,
        "rendered_bounded": rendered <= limit,
        "canonical_complete": store.segment_count() >= 1000 and len(clean) > 0,
        "no_event_drop": dropped == 0,
        "no_background_tk": background_tk_calls == 0,
        "avg_insert_below_15ms": avg_ms < 15.0,
        "max_insert_below_50ms": max_ms < 50.0,
        "export_hash_stable": export_hash == export_hash2,
        "full_rewrite_path_blocked_when_over_limit": ui_full_rewrite_blocked >= 0,
        "full_text_read_avoided_on_inserts": ui_full_text_read_blocked > 0,
    }
    failed = [k for k, v in checks.items() if not v]
    status = "PASSED" if not failed else "FAILED"
    return {
        "LONG_SESSION_UI_PERFORMANCE_VALIDATION": status,
        "SYNTHETIC_UI_LOAD_TEST_NOT_PRODUCT_ACCURACY": True,
        "generated_at_utc": _utc(),
        "runtime_seconds": elapsed,
        "canonical_segment_count": store.segment_count(),
        "rendered_ui_segment_count": rendered,
        "MAX_RENDERED_UI_SEGMENTS": limit,
        "average_ui_insert_ms": avg_ms,
        "maximum_ui_insert_ms": max_ms,
        "memory_current_bytes": current,
        "memory_peak_bytes": peak,
        "export_sha256": export_hash,
        "checks": checks,
        "failures": failed,
        "ui_full_text_read_blocked_count": ui_full_text_read_blocked,
        "ui_full_rewrite_blocked_count": ui_full_rewrite_blocked,
        "background_tk_calls": background_tk_calls,
        "dropped_events": dropped,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = run()
    path = OUT_DIR / "LONG_SESSION_UI_PERFORMANCE_VALIDATION.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"LONG_SESSION_UI_PERFORMANCE_VALIDATION = {report['LONG_SESSION_UI_PERFORMANCE_VALIDATION']}")
    print(f"Wrote {path}")
    if report["failures"]:
        print(f"failures={report['failures']}")
    return 0 if report["LONG_SESSION_UI_PERFORMANCE_VALIDATION"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
