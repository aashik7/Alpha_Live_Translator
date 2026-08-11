#!/usr/bin/env python
"""Score a recorded run against `CLIENT_DELIVERY_SPRINT_v5.md` §7's gates.

`CLIENT_DELIVERY_SPRINT_v5.md` item 39. Companion to item 38's
`replay_run.py` -- this tool does not replay anything, it reads back what
a run actually left on disk and checks it against the 8 definition-of-done
gates, plus commit-latency percentiles.

HONESTY OVER COVERAGE
----------------------
Not all 8 gates are measurable from a static run folder, and this tool
says so rather than guessing:

- **Gates 1, 2, 3** are fully measurable from any run folder and are real
  pass/fail verdicts.
- **Gates 4 and 5** (cross-speaker merges, quarantine losing real speech)
  need a ground-truth reference to check against -- this run's own
  `RUN_MANIFEST.json` records `JAPANESE_STT_PROFILE: no_diarize`, so
  speaker labels here are a heuristic, not a fact (see
  `CLIENT_DELIVERY_SPRINT_v5.md` §6 on dual-channel capture, deferred
  post-delivery specifically because it turns speaker identity from a
  guess into one). Pass `--reference <hand-written transcript>` (item 40)
  to get a real verdict; without it these report `NOT_MEASURABLE`, never
  a fabricated PASS. `channel_index` was checked as a cheaper proxy and
  rejected -- it reads `[0, 1]` on 34 of 35 records across two runs
  regardless of anything, i.e. it is an artifact of the capture profile,
  not a speaker signal.
- **Gates 6, 7, 8** (network drop, Start/Stop cycles, clean-machine
  install) are properties of a live session or an installer, not of a
  recorded run folder, and always report `NOT_MEASURABLE` here. Items
  44-49 do not exist yet, so there is nothing in current evidence for a
  detector to key on even opportunistically.

USAGE
-----
    python tools/score_run.py <run_folder> [--reference FILE] [--json]
    python tools/score_run.py --all [--reference FILE] [--json]

Exit codes: 0 every measurable gate passed, 1 a measurable gate failed,
2 bad input. `NOT_MEASURABLE` never affects the exit code -- it is
neither a pass nor a failure.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TOOLS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.replay_run import (  # noqa: E402
    UnknownRowShape,
    _dropped_content,
    _load_jsonl,
    _norm,
    classify_row,
    recorded_counts,
)

PASS, FAIL, NOT_MEASURABLE, HEURISTIC = "PASS", "FAIL", "NOT_MEASURABLE", "HEURISTIC"


def _gate(number: int, name: str, status: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {"gate": number, "name": name, "status": status, **detail}


# --------------------------------------------------------------------------
# Gate 1 -- committed sentences dropped before the export
# --------------------------------------------------------------------------
def gate_1_dropped_content(run_folder: Path) -> dict[str, Any]:
    """Recorded-side only -- this tool scores what shipped, not a replay.

    Reuses `replay_run.py`'s own verdict function so the two tools cannot
    silently drift into disagreeing about what a loss is.
    """
    rec = recorded_counts(run_folder)
    dropped = _dropped_content(rec["_commits"], rec["_ledger_text"], rec["_export_text"])
    status = PASS if not dropped else FAIL
    return _gate(
        1,
        "committed sentences dropped before export",
        status,
        {
            "dropped_count": len(dropped),
            "dropped": [{"uid": d["uid"], "text": d["text"], "reason": d["reason"]} for d in dropped],
        },
    )


# --------------------------------------------------------------------------
# Gate 2 -- exact-duplicate lines in export
# --------------------------------------------------------------------------
def gate_2_duplicate_export_lines(run_folder: Path) -> dict[str, Any]:
    path = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    if not path.exists():
        return _gate(2, "exact-duplicate export lines", NOT_MEASURABLE, {"reason": "no export file"})
    lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
    counts: dict[str, int] = {}
    for line in lines:
        counts[line] = counts.get(line, 0) + 1
    dupes = {line: n for line, n in counts.items() if n > 1}
    status = PASS if not dupes else FAIL
    return _gate(
        2,
        "exact-duplicate export lines",
        status,
        {"total_lines": len(lines), "duplicate_lines": dupes},
    )


# --------------------------------------------------------------------------
# Gate 3 -- canonical records without a translation
# --------------------------------------------------------------------------
def gate_3_untranslated_records(run_folder: Path) -> dict[str, Any]:
    ledger = _load_jsonl(run_folder / "evidence_streams" / "canonical_commits.jsonl")
    jobs = _load_jsonl(run_folder / "evidence_streams" / "translation_jobs.jsonl")
    if not ledger:
        return _gate(3, "canonical records without a translation", NOT_MEASURABLE, {"reason": "no ledger records"})
    active_ids = {r.get("canonical_utterance_id") for r in ledger if r.get("canonical_utterance_id") and not r.get("synthetic_record")}
    translated_ids = {r.get("canonical_utterance_id") for r in jobs if r.get("accepted") and r.get("canonical_utterance_id")}
    missing = sorted(active_ids - translated_ids)
    id_to_text = {r.get("canonical_utterance_id"): r.get("text") or "" for r in ledger}
    status = PASS if not missing else FAIL
    return _gate(
        3,
        "canonical records without a translation",
        status,
        {
            "ledger_records": len(active_ids),
            "missing_count": len(missing),
            "missing": [{"uid": uid, "text": id_to_text.get(uid, "")} for uid in missing],
        },
    )


# --------------------------------------------------------------------------
# Gates 4 & 5 -- need a reference transcript to be more than a guess
# --------------------------------------------------------------------------
def _load_reference(reference_path: Optional[Path]) -> Optional[str]:
    if reference_path is None:
        return None
    if not reference_path.exists():
        return None
    return reference_path.read_text(encoding="utf-8", errors="ignore")


def gate_4_cross_speaker_lines(run_folder: Path, reference_text: Optional[str]) -> dict[str, Any]:
    if reference_text is None:
        return _gate(
            4,
            "lines containing two speakers' turns",
            NOT_MEASURABLE,
            {
                "reason": (
                    "no --reference transcript supplied, and this run's speaker "
                    "labels are not diarized (JAPANESE_STT_PROFILE: no_diarize) "
                    "so they cannot self-certify. See item 40."
                )
            },
        )
    # Real check, once a reference exists: every exported line's text must
    # be a substring run of ONE reference speaker turn, not a splice of two.
    # Left unimplemented until item 40 produces a real transcript to test
    # this against -- writing the matching logic against a reference this
    # tool invented itself would be exactly the kind of untested claim
    # CLAUDE.md's verification rule exists to prevent.
    return _gate(
        4,
        "lines containing two speakers' turns",
        NOT_MEASURABLE,
        {"reason": "reference-based check not yet implemented, see item 40 follow-up"},
    )


def gate_5_quarantine_review(run_folder: Path, reference_text: Optional[str]) -> dict[str, Any]:
    log_path = run_folder / "logs" / "japanese_accuracy.log"
    if not log_path.exists():
        return _gate(5, "quarantine events losing real speech", NOT_MEASURABLE, {"reason": "no japanese_accuracy.log"})
    events = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if " | " not in line:
            continue
        _, _, rest = line.partition(" | ")
        rest = rest.strip()
        if not rest.startswith("{"):
            continue
        try:
            d = json.loads(rest)
        except Exception:
            continue
        if d.get("event") == "NOISE_FRAGMENT_QUARANTINED":
            events.append(d)
    if not events:
        return _gate(5, "quarantine events losing real speech", PASS, {"quarantine_count": 0})
    if reference_text is None:
        return _gate(
            5,
            "quarantine events losing real speech",
            HEURISTIC,
            {
                "reason": "no --reference transcript; cannot judge real-speech vs genuine noise automatically",
                "quarantine_count": len(events),
                "quarantined_text": [e.get("raw_text", "") for e in events],
            },
        )
    ref_norm = _norm(reference_text)
    confirmed_real = [e for e in events if _norm(e.get("raw_text", "")) and _norm(e.get("raw_text", "")) in ref_norm]
    status = FAIL if confirmed_real else HEURISTIC
    return _gate(
        5,
        "quarantine events losing real speech",
        status,
        {
            "quarantine_count": len(events),
            "confirmed_real_speech_lost": [e.get("raw_text", "") for e in confirmed_real],
            "unconfirmed": [e.get("raw_text", "") for e in events if e not in confirmed_real],
        },
    )


# --------------------------------------------------------------------------
# Gates 6, 7, 8 -- properties of a live session or an installer
# --------------------------------------------------------------------------
def gate_6_network_drop(run_folder: Path) -> dict[str, Any]:
    return _gate(
        6,
        "60-min session with forced network drop ends completed, zero loss",
        NOT_MEASURABLE,
        {"reason": "requires a live session with a deliberate drop; items 44/45 not built yet"},
    )


def gate_7_start_stop_cycles(run_folder: Path) -> dict[str, Any]:
    return _gate(
        7,
        "Start->Stop->Start, 5 cycles, no degradation",
        NOT_MEASURABLE,
        {"reason": "requires a live multi-cycle session; one run folder is one session"},
    )


def gate_8_clean_install(run_folder: Path) -> dict[str, Any]:
    return _gate(
        8,
        "clean-machine install runs the full scenario",
        NOT_MEASURABLE,
        {"reason": "requires an installer verification pass; item 49 not started"},
    )


# --------------------------------------------------------------------------
# Latency percentiles
# --------------------------------------------------------------------------
def commit_latency_percentiles(run_folder: Path) -> dict[str, Any]:
    """Seconds from the last genuine ingress row a commit draws on, to the
    commit landing in the ledger. Computed from real recorded timestamps
    present in every replayable run -- `stop_finalize_timeline.jsonl` was
    checked as a Stop-latency source and rejected: it holds only a
    `LOG_INITIALIZED` marker on every run sampled, no real timing data.
    """
    rows = _load_jsonl(run_folder / "evidence_streams" / "provider_events.jsonl")
    by_id = {r.get("raw_event_id"): r for r in rows if r.get("raw_event_id")}

    def is_ingress(r: dict[str, Any]) -> bool:
        meta = r.get("metadata") or {}
        return meta.get("raw_deepgram_text") is not None and r.get("confidence") is not None

    ledger = _load_jsonl(run_folder / "evidence_streams" / "canonical_commits.jsonl")
    latencies: list[float] = []
    negative: list[dict[str, Any]] = []
    for rec in ledger:
        committed_at = rec.get("committed_at")
        if committed_at is None:
            continue
        ts = [
            by_id[rid]["timestamp"]
            for rid in (rec.get("source_raw_event_ids") or [])
            if rid in by_id and is_ingress(by_id[rid]) and by_id[rid].get("timestamp") is not None
        ]
        if not ts:
            continue
        latency = float(committed_at) - max(ts)
        if latency < 0:
            # Not filtered out -- reported as an anomaly for a human to
            # look at rather than silently excluded from the numbers.
            negative.append({"uid": rec.get("canonical_utterance_id"), "latency_s": round(latency, 2)})
        latencies.append(latency)
    if not latencies:
        return {"count": 0, "reason": "no records with matched ingress timestamps"}
    latencies.sort()
    return {
        "count": len(latencies),
        "p50_s": round(statistics.median(latencies), 2),
        "p90_s": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.9))], 2),
        "p99_s": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))], 2),
        "max_s": round(latencies[-1], 2),
        "negative_latency_records": negative,
    }


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def score_run(run_folder: Path, *, reference_text: Optional[str] = None) -> dict[str, Any]:
    rows = _load_jsonl(run_folder / "evidence_streams" / "provider_events.jsonl")
    ingress = [r for r in rows if classify_row(r) == "ingress"]
    result: dict[str, Any] = {"run": run_folder.name, "replayable": bool(ingress)}

    gates = [
        gate_1_dropped_content(run_folder) if ingress else _gate(
            1, "committed sentences dropped before export", NOT_MEASURABLE,
            {"reason": "no genuine provider ingress in this run (English sessions record none)"},
        ),
        gate_2_duplicate_export_lines(run_folder),
        gate_3_untranslated_records(run_folder),
        gate_4_cross_speaker_lines(run_folder, reference_text),
        gate_5_quarantine_review(run_folder, reference_text),
        gate_6_network_drop(run_folder),
        gate_7_start_stop_cycles(run_folder),
        gate_8_clean_install(run_folder),
    ]
    result["gates"] = gates
    result["latency"] = commit_latency_percentiles(run_folder)
    result["overall"] = FAIL if any(g["status"] == FAIL for g in gates) else PASS
    return result


def _print_human(res: dict[str, Any]) -> None:
    print(f"\n=== {res['run']} ===")
    for g in res["gates"]:
        marker = {"PASS": "PASS", "FAIL": "FAIL", "NOT_MEASURABLE": "n/a ", "HEURISTIC": "heur"}[g["status"]]
        print(f"  [{marker}] gate {g['gate']}: {g['name']}")
        if g["status"] == FAIL:
            for k, v in g.items():
                if k not in ("gate", "name", "status"):
                    print(f"         {k}: {v}")
    lat = res["latency"]
    if lat.get("count"):
        print(
            f"  latency: n={lat['count']} p50={lat['p50_s']}s p90={lat['p90_s']}s "
            f"p99={lat['p99_s']}s max={lat['max_s']}s"
        )
        if lat.get("negative_latency_records"):
            print(f"  ANOMALY: {len(lat['negative_latency_records'])} record(s) with negative latency (see --json)")
    else:
        print(f"  latency: {lat.get('reason', 'no data')}")
    print(f"  -> OVERALL: {res['overall']}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_folder", nargs="?", help="path to one troubleshooting/runs/<run>")
    parser.add_argument("--all", action="store_true", help="every run under troubleshooting/runs")
    parser.add_argument("--reference", help="hand-written reference transcript (item 40), enables gates 4/5")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.all:
        base = _PROJECT_ROOT / "troubleshooting" / "runs"
        folders = sorted(p for p in base.iterdir() if p.is_dir())
    elif args.run_folder:
        folder = Path(args.run_folder)
        if not folder.is_dir():
            print(f"not a directory: {folder}", file=sys.stderr)
            return 2
        folders = [folder]
    else:
        parser.print_usage(sys.stderr)
        return 2

    reference_text = _load_reference(Path(args.reference)) if args.reference else None
    if args.reference and reference_text is None:
        print(f"--reference file not found: {args.reference}", file=sys.stderr)
        return 2

    results = []
    for folder in folders:
        try:
            results.append(score_run(folder, reference_text=reference_text))
        except UnknownRowShape as exc:
            results.append({"run": folder.name, "error": f"unknown_row_shape: {exc}"})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for res in results:
            if "error" in res:
                print(f"\n=== {res['run']} ===\n  ERROR: {res['error']}")
            else:
                _print_human(res)

    failed = [r for r in results if r.get("overall") == FAIL or "error" in r]
    if not args.json:
        print(f"\n{len(results)} scored, {len(results) - len(failed)} passed, {len(failed)} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
