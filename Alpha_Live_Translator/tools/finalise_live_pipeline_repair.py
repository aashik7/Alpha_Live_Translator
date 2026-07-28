# -*- coding: utf-8 -*-
"""Finalise live-pipeline repair evidence after real JA/EN live tests.

Usage (from Alpha_Live_Translator root):

    python .\\tools\\finalise_live_pipeline_repair.py

Locates newest completed non-_pending runs, scores profiling/lifecycle evidence,
and writes/updates LIVE_PIPELINE_REPAIR_DECISION.json in the newest evidence folder.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return round(xs[0], 3)
    k = (len(xs) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return round(xs[f], 3)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 3)


def _detect_language(run_dir: Path) -> str:
    candidates = [
        run_dir / "artifacts" / "RUN_METADATA.json",
        run_dir / "RUN_METADATA.json",
        run_dir / "artifacts" / "language_routing.json",
        run_dir / "translation" / "summary.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = _read_json(path)
        except Exception:
            continue
        blob = json.dumps(data).lower()
        if '"ja"' in blob or "japanese" in blob or "lang=ja" in blob:
            if "english" in blob and "japanese" not in blob:
                pass
            return "ja"
        if '"en"' in blob or "english" in blob:
            return "en"
    # Fallback: inspect transcript for Japanese characters
    for name in ("Alpha output.txt", "Alpha_output_FINAL.txt", "latest_alpha_output.txt"):
        for p in run_dir.rglob(name):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
                return "ja"
            if text.strip():
                return "en"
    return "unknown"


def _run_complete(run_dir: Path) -> bool:
    if run_dir.name.endswith("_pending"):
        return False
    markers = [
        run_dir / "artifacts" / "RUN_ARTIFACTS_INDEX.txt",
        run_dir / "transcripts" / "Alpha output.txt",
        run_dir / "upload_package" / "UPLOAD_PACKAGE_INDEX.txt",
    ]
    return any(m.is_file() for m in markers)


def _list_completed_runs() -> list[Path]:
    runs_root = ROOT / "troubleshooting" / "runs"
    if not runs_root.is_dir():
        return []
    runs = [p for p in runs_root.iterdir() if p.is_dir() and _run_complete(p)]
    return sorted(runs, key=lambda p: p.name, reverse=True)


def _newest_evidence_dir() -> Optional[Path]:
    base = ROOT / "troubleshooting"
    if not base.is_dir():
        return None
    dirs = sorted(
        [p for p in base.iterdir() if p.is_dir() and p.name.startswith("live_pipeline_repair") and p.name != "live_pipeline_repair"],
        key=lambda p: p.name,
        reverse=True,
    )
    return dirs[0] if dirs else None


def _analyze_run(run_dir: Path) -> dict[str, Any]:
    lang = _detect_language(run_dir)
    summary: dict[str, Any] = {
        "run_folder": str(run_dir),
        "language": lang,
        "complete": True,
        "interim_event_count": None,
        "permanent_source_line_count": None,
        "translation_jobs_accepted": None,
        "provider_requests": None,
        "translation_commits": None,
        "loading_indicators_pending_at_exit": None,
        "queue_pending_at_exit": None,
        "in_flight_at_exit": None,
        "ordering_buffer_pending_at_exit": None,
        "unresolved_sequences": None,
        "latencies_ms": {},
        "status": "INCOMPLETE_LIVE_EVIDENCE",
    }
    # Translation worker summary if present
    for rel in (
        "translation/summary.json",
        "translation/TRANSLATION_SUMMARY.json",
        "artifacts/translation_summary.json",
    ):
        path = run_dir / rel
        if path.is_file():
            try:
                data = _read_json(path)
                summary["translation_jobs_accepted"] = data.get("STABLE_TRANSLATION_JOBS_ACCEPTED") or data.get("jobs_accepted")
                summary["provider_requests"] = data.get("TRANSLATION_REQUESTS_SENT") or data.get("requests_sent")
                summary["translation_commits"] = data.get("TRANSLATION_COMMITS_COMPLETED") or data.get("commits")
                summary["queue_pending_at_exit"] = data.get("TRANSLATION_QUEUE_PENDING_AT_EXIT") or data.get("queue_pending")
                summary["in_flight_at_exit"] = data.get("TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT") or data.get("in_flight")
                summary["ordering_buffer_pending_at_exit"] = data.get("ORDERING_BUFFER_PENDING_AT_EXIT") or data.get("ordering_pending")
                summary["unresolved_sequences"] = data.get("UNRESOLVED_TRANSLATION_SEQUENCES") or data.get("unresolved") or []
                summary["loading_indicators_pending_at_exit"] = data.get("LOADING_INDICATORS_PENDING_AT_EXIT", 0)
            except Exception:
                pass
            break
    # Source line count from transcript
    for rel in (
        "transcripts/Alpha output.txt",
        "transcripts/Alpha_output_FINAL.txt",
        "Alpha output.txt",
    ):
        path = run_dir / rel
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            lines = [ln for ln in text.splitlines() if ln.strip()]
            summary["permanent_source_line_count"] = len(lines)
            summary["source_transcript_character_count"] = len(text)
            break
    # Profiling snapshot if present
    for rel in ("artifacts/live_pipeline_profile.json", "live_pipeline_profile.json"):
        path = run_dir / rel
        if path.is_file():
            try:
                prof = _read_json(path)
                d = prof.get("durations_ms") or {}
                summary["latencies_ms"] = {
                    "start_ack": d.get("start_ack"),
                    "start_to_listening": d.get("start_to_listening"),
                    "stop_ack": d.get("stop_ack"),
                    "stop_total": d.get("stop_total"),
                }
            except Exception:
                pass
            break

    # Gate completeness for this run
    needed = [
        summary.get("permanent_source_line_count"),
        summary.get("translation_commits"),
    ]
    if all(v is not None for v in needed):
        summary["status"] = "ANALYZED"
    return summary


def main() -> int:
    evidence = _newest_evidence_dir()
    if evidence is None:
        print("STATUS = INCOMPLETE_LIVE_EVIDENCE")
        print("No live_pipeline_repair<timestamp> evidence folder found.")
        print("Run tools/validate_live_pipeline_repair.py first.")
        return 2

    runs = _list_completed_runs()
    ja_run = next((r for r in runs if _detect_language(r) == "ja"), None)
    en_run = next((r for r in runs if _detect_language(r) == "en"), None)

    ja_result = _analyze_run(ja_run) if ja_run else {"status": "NOT_RUN"}
    en_result = _analyze_run(en_run) if en_run else {"status": "NOT_RUN"}

    _write_json(evidence / "JA_TO_EN_LIVE_RESULT.json", ja_result)
    _write_json(evidence / "EN_TO_JA_LIVE_RESULT.json", en_result)

    latency = {
        "ja": (ja_result.get("latencies_ms") if isinstance(ja_result, dict) else {}),
        "en": (en_result.get("latencies_ms") if isinstance(en_result, dict) else {}),
        "note": "Populate from live_pipeline_profile.json inside each completed run when available.",
    }
    _write_json(evidence / "TRANSLATION_LATENCY_BREAKDOWN.json", latency)

    if not ja_run or not en_run:
        status = "INCOMPLETE_LIVE_EVIDENCE"
    else:
        # Without rich UI-render evidence, stay blocked rather than ACCEPTED.
        has_ui_render = False
        for run in (ja_run, en_run):
            if (run / "artifacts" / "ui_lifecycle_events.jsonl").is_file():
                has_ui_render = True
            if (run / "ui_lifecycle_events.jsonl").is_file():
                has_ui_render = True
        if has_ui_render:
            status = "BLOCKED"  # require human/metric gate before ACCEPTED
        else:
            status = "INCOMPLETE_LIVE_EVIDENCE"

    decision = {
        "STATUS": status,
        "timestamp_utc": _utc(),
        "evidence_dir": str(evidence),
        "ja_run": str(ja_run) if ja_run else None,
        "en_run": str(en_run) if en_run else None,
        "ja_result_status": ja_result.get("status") if isinstance(ja_result, dict) else "NOT_RUN",
        "en_result_status": en_result.get("status") if isinstance(en_result, dict) else "NOT_RUN",
        "note": "Do not set ACCEPTED without verified Start/Stop/UI-render metrics from both directions.",
    }
    _write_json(evidence / "LIVE_PIPELINE_REPAIR_DECISION.json", decision)
    report = [
        "Live pipeline repair finalisation",
        f"STATUS = {status}",
        f"evidence = {evidence}",
        f"ja_run = {ja_run}",
        f"en_run = {en_run}",
        "",
        "Next: if both live runs exist with profiling, review decision then run:",
        "python .\\tools\\package_live_pipeline_repair.py",
    ]
    text = "\n".join(report) + "\n"
    (evidence / "LIVE_PIPELINE_REPAIR_REPORT.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0 if status != "INCOMPLETE_LIVE_EVIDENCE" or (ja_run or en_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
