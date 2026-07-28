#!/usr/bin/env python3
"""Build final live bilingual translation test report package + ZIP."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = {
    "JA_TO_EN": ROOT / "troubleshooting/runs/v3.3.5.5.8.5.26.5.3-20260727-150631",
    "EN_TO_JA": ROOT / "troubleshooting/runs/v3.3.5.5.8.5.26.5.3-20260727-151415",
}


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def events(run: Path) -> list[dict]:
    p = run / "translation" / "translation_events.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def summarize(label: str, run: Path) -> dict:
    summary = load_json(run / "translation" / "translation_summary.json")
    dg = load_json(run / "accuracy_stage_compare" / "deepgram_request_actual.json")
    status = load_json(run / "artifacts" / "LIVE_RUN_STATUS.json")
    manifest = load_json(run / "RUN_MANIFEST.json")
    ev = events(run)
    provider_done = [e for e in ev if e.get("phase") == "provider_done"]
    commits = [e for e in ev if e.get("phase") == "ordered_commit"]
    accepted_ids = sorted(
        {int(e["segment_id"]) for e in provider_done if e.get("segment_id") is not None}
    )
    committed_ids = sorted(
        {int(e["segment_id"]) for e in commits if e.get("segment_id") is not None}
    )

    transcript = ""
    transcript_src = "missing"
    for rel in [
        "transcripts/Alpha_output_FINAL.txt",
        "transcripts/Alpha output.txt",
        "accuracy/Alpha_for_accuracy_check.txt",
        "artifacts/EMERGENCY_UNVERIFIED_TRANSCRIPT.txt",
    ]:
        t = load_text(run / rel).strip()
        if t:
            transcript = t
            transcript_src = rel
            break

    stables: list[dict] = []
    sc = run / "transcripts" / "stable_commits.jsonl"
    if sc.exists():
        for line in sc.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            txt = o.get("text") or o.get("stable_text") or o.get("committed_text") or ""
            if txt:
                stables.append(
                    {
                        "id": o.get("segment_id") or o.get("stable_commit_id") or o.get("id"),
                        "text": txt[:200],
                    }
                )

    lang = dg.get("language")
    if lang == "ja":
        direction = "JA -> EN-US"
    elif lang == "en":
        direction = "EN -> JA"
    else:
        direction = f"{lang or '?'} -> ?"

    pending = int(summary.get("TRANSLATION_QUEUE_PENDING_AT_EXIT") or 0)
    ordering = int(summary.get("ORDERING_BUFFER_PENDING_AT_EXIT") or 0)
    missing = (
        summary.get("MISSING_TRANSLATION_SEGMENT_IDS")
        or summary.get("UNFINISHED_TRANSLATION_SEGMENT_IDS")
        or []
    )
    if isinstance(missing, int):
        missing_count = missing
        missing_ids: list = []
    else:
        missing_ids = list(missing)
        missing_count = len(missing_ids)

    commits_completed = int(summary.get("TRANSLATION_COMMITS_COMPLETED") or 0)
    sent = int(summary.get("TRANSLATION_REQUESTS_SENT") or 0)
    gate_ok = (
        int(summary.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM") or 0) == 0
        and int(summary.get("DUPLICATE_TRANSLATION_REQUESTS_SENT") or 0) == 0
        and int(summary.get("OUT_OF_ORDER_TRANSLATION_COMMITS") or 0) == 0
        and pending == 0
        and ordering == 0
        and missing_count == 0
        and commits_completed == sent
        and sent > 0
        and not dg.get("diarize_present")
        and not dg.get("diarize_model_present")
    )

    return {
        "label": label,
        "run_folder": str(run),
        "run_id": summary.get("run_id") or status.get("run_id"),
        "direction": direction,
        "stt_language": lang,
        "stt_model": dg.get("model"),
        "endpointing": dg.get("endpointing"),
        "utterance_end_ms": dg.get("utterance_end_ms"),
        "diarize_absent": (not dg.get("diarize_present")) and (not dg.get("diarize_model_present")),
        "elapsed_seconds": status.get("elapsed_seconds") or manifest.get("elapsed_seconds"),
        "stop_finalize_completed": bool(status.get("stop_finalize_completed")),
        "run_status": status.get("status") or manifest.get("final_status"),
        "stable_commit_count": status.get("internal_stable_commit_count"),
        "translation": {
            "requests_sent": sent,
            "commits_completed": commits_completed,
            "successful_provider_results": int(summary.get("successful_translations") or 0),
            "interim_sent": int(summary.get("TRANSLATION_REQUESTS_SENT_FROM_INTERIM") or 0),
            "duplicate_sent": int(summary.get("DUPLICATE_TRANSLATION_REQUESTS_SENT") or 0),
            "duplicate_submissions_rejected": int(summary.get("DUPLICATE_SUBMISSIONS_REJECTED") or 0),
            "pending_at_exit": pending,
            "ordering_buffer_at_exit": ordering,
            "inflight_at_exit": int(summary.get("TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT") or 0),
            "missing_ids": missing_ids,
            "missing_count": missing_count,
            "accepted_segment_ids": accepted_ids,
            "committed_segment_ids": committed_ids,
            "provider_latency_ms": summary.get("provider_latency_ms"),
            "e2e_ms": summary.get("translation_end_to_end_ms"),
            "source_characters_sent": summary.get("source_characters_sent"),
            "worker_stopped": bool(summary.get("TRANSLATION_WORKER_STOPPED")),
        },
        "transcript_source": transcript_src,
        "transcript_preview": transcript[:1200],
        "transcript_line_count": len([ln for ln in transcript.splitlines() if ln.strip()]),
        "stable_preview": stables[:8],
        "speaker_numbered_in_export": ("Speaker 2" in transcript) or ("Speaker 1" in transcript),
        "gate_ok": gate_ok,
        "blocker": None
        if gate_ok
        else ("ordering_gap_uncommitted_segments" if missing_count else "translation_incomplete"),
    }


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "troubleshooting" / f"live_bilingual_test_report{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = [summarize(k, v) for k, v in RUNS.items()]
    overall = "FAILED" if any(not r["gate_ok"] for r in reports) else "ACCEPTED"
    decision = {
        "generated_at_utc": stamp,
        "LIVE_BILINGUAL_TEST_RESULT": overall,
        "tests": reports,
        "proven_blocker": (
            "Ordered translation commits stalled because segment IDs advanced by 2 "
            "(1,3,5,...) so even IDs never arrived; ordering buffer waited forever and "
            "Stop left pending/missing IDs."
        ),
        "notes": [
            "DeepL provider succeeded for almost all accepted jobs (provider_done count high).",
            "Interim provider requests = 0 on both runs.",
            "Duplicate provider requests sent = 0 on both runs.",
            "English diarization remained absent.",
            "Japanese run used language=ja endpointing=500; English run used language=en endpointing=1200.",
            "Alpha export still shows [Speaker 2] in Japanese transcript output "
            "(UI label regression vs required generic Speaker:).",
        ],
    }
    (out_dir / "LIVE_BILINGUAL_TEST_DECISION.json").write_text(
        json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        "ALPHA LIVE BILINGUAL TRANSLATION TEST REPORT",
        f"generated_at_utc={stamp}",
        f"RESULT={overall}",
        "",
    ]
    for r in reports:
        t = r["translation"]
        pl = t["provider_latency_ms"] or {}
        lines += [
            f"=== {r['label']} ({r['direction']}) ===",
            f"run={r['run_folder']}",
            f"run_id={r['run_id']}",
            (
                f"stt={r['stt_language']} model={r['stt_model']} "
                f"endpointing={r['endpointing']} utterance_end_ms={r['utterance_end_ms']} "
                f"diarize_absent={r['diarize_absent']}"
            ),
            (
                f"elapsed_s={r['elapsed_seconds']} stop_ok={r['stop_finalize_completed']} "
                f"status={r['run_status']}"
            ),
            (
                f"stable_commits={r['stable_commit_count']} translation_sent={t['requests_sent']} "
                f"commits_completed={t['commits_completed']} "
                f"provider_success={t['successful_provider_results']}"
            ),
            (
                f"interim_sent={t['interim_sent']} dup_sent={t['duplicate_sent']} "
                f"pending={t['pending_at_exit']} ordering_buffer={t['ordering_buffer_at_exit']} "
                f"missing={t['missing_count']}"
            ),
            f"accepted_ids={t['accepted_segment_ids']}",
            f"committed_ids={t['committed_segment_ids']}",
            f"provider_latency_p50_p95_max={pl.get('p50')}/{pl.get('p95')}/{pl.get('max')}",
            f"gate_ok={r['gate_ok']} blocker={r['blocker']}",
            f"speaker_numbered_in_export={r['speaker_numbered_in_export']}",
            "transcript_preview:",
            r["transcript_preview"],
            "",
        ]
    lines += ["PROVEN BLOCKER:", decision["proven_blocker"], "", "NOTES:"]
    lines += [f"- {n}" for n in decision["notes"]]
    report_text = "\n".join(lines)
    (out_dir / "LIVE_BILINGUAL_TEST_REPORT.txt").write_text(report_text, encoding="utf-8")
    (out_dir / "Cursor final report.txt").write_text(report_text, encoding="utf-8")

    for label, run in RUNS.items():
        dest = out_dir / label
        dest.mkdir(exist_ok=True)
        for rel in [
            "translation/translation_summary.json",
            "translation/translation_validation.json",
            "translation/translation_events.jsonl",
            "translation/sanitized_deepl_configuration.json",
            "accuracy_stage_compare/deepgram_request_actual.json",
            "artifacts/LIVE_RUN_STATUS.json",
            "RUN_MANIFEST.json",
            "transcripts/Alpha_output_FINAL.txt",
            "transcripts/Alpha output.txt",
            "artifacts/EMERGENCY_UNVERIFIED_TRANSCRIPT.txt",
            "accuracy/Alpha_for_accuracy_check.txt",
        ]:
            src = run / rel
            if src.exists():
                (dest / Path(rel).name).write_bytes(src.read_bytes())

    zip_dir = ROOT / "troubleshooting" / "live_bilingual_test_report"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"ALPHA_LIVE_BILINGUAL_TEST_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in out_dir.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(out_dir.parent)))
    (out_dir / "ZIP_PATH.txt").write_text(str(zip_path.resolve()), encoding="utf-8")

    print(
        json.dumps(
            {
                "result": overall,
                "package": str(out_dir.resolve()),
                "zip": str(zip_path.resolve()),
                "tests": [
                    {
                        "label": r["label"],
                        "direction": r["direction"],
                        "gate_ok": r["gate_ok"],
                        "sent": r["translation"]["requests_sent"],
                        "commits": r["translation"]["commits_completed"],
                        "missing": r["translation"]["missing_count"],
                    }
                    for r in reports
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
