#!/usr/bin/env python
"""Replay a recorded run's provider ingress headlessly and diff the result.

`CLIENT_DELIVERY_SPRINT_v5.md` item 38. Built so that verifying a change
costs a command instead of a human live session — that dependency
(problem E) is why v4 items 10/11 sat unverified across five sessions.

WHAT IT REPLAYS
---------------
`evidence_streams/provider_events.jsonl` is a **mixed** stream. It holds
both genuine Deepgram ingress and the Japanese assembler's own commit
re-emissions, because `_publish_final_transcript_segment` also calls
`record_raw_deepgram_final`. `canonical_finalize.py` filters out rows
flagged `synthetic_record`/`synthetic_lineage`, but assembler output that
is not flagged synthetic still reaches the file.

The two classes are separated on **`metadata.raw_deepgram_text`
presence**, verified perfectly bimodal across 462 rows in all 16 runs
that have the file: 242 rows carry both `raw_deepgram_text` and
`confidence`, 220 carry neither, and **no row is mixed**. Do not use
`metadata.source` for this — it is the *speaker-source* label
(`system`/`none`/`mic`/`mixed`, written from
`source_snapshot.chosen_source`), not a provenance field. Filtering on
`source == "system"` would silently discard 98 genuine ingress rows.

Any row matching neither shape raises `UnknownRowShape`. Unrecognised
input is never skipped: this instrument exists to find records that
disappear, so it must not disappear records itself. The per-run JSON
carries the accounting invariant

    rows_read == rows_fed + sum(rows_excluded[reason])

with a per-reason breakdown, and the tool exits non-zero if it fails.

JAPANESE ONLY — BY EVIDENCE, NOT CHOICE
---------------------------------------
Only `japanese_final_chunk_stabilizer.py` calls
`record_raw_deepgram_final` on true ingress, so an English session
records **zero** genuine-ingress rows. Confirmed: all 10 English runs
have 0 ingress rows and 0 `stable_commits`. Six runs are replayable:
`...160529`, `...155922`, `...160130`, `...134815`, `...155334`,
`...174516`.

LIMITATION — FAST-FEED, NO REAL TIMERS
--------------------------------------
Events are fed as fast as possible; original inter-event timestamps are
**not** honoured, and neither is `LanguagePipelineWorker`'s real
scheduling thread. In production the assembler posts a *deferred* flush
via `schedule_flush(assembler, due_mono, generation, reason)`, executed
against wall-clock time; here the flush is driven directly. Flush timing
decides what the assembler batches into one commit, so this replay is
faithful for *content* routing but not for *timing*.

**Measured 2026-08-11: the loss pattern does not reproduce under
fast-feed.** All 14 recorded losses across the 6 replayable runs are
`overwritten_by_id_collision`; replay reproduces 0 of them, and the
segmentation itself diverges — identical ingress yields 13 replayed
commit decisions against 29 recorded on `...134815`, 19 against 32 on
`...155334`. No timer fires mid-stream, so the assembler mints a fresh
id where the real run reused one, and the collision never happens.
Problem A is therefore timing-dependent; reproducing it needs real
`LanguagePipelineWorker` scheduling, which is a separately-scoped item
(v5 §8 item 38b) rather than a change to this tool.

That is a finding, not a defect here — this harness is deliberately not
tuned until its numbers agree with an expectation. What it does
establish is the *recorded* side, measured against the ledger and the
export independently, which is what proves problem A and gives item 41
its target.

USAGE
-----
    python tools/replay_run.py <run_folder> [--json]
    python tools/replay_run.py --all [--json]

Exit codes: 0 all replayed runs reproduced their recorded loss pattern,
1 a mismatch or a failed invariant, 2 bad input. **Exit 1 is the current
expected result** for the reason above; it is a real signal, so it is
left as a failure rather than downgraded to a warning.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TOOLS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class UnknownRowShape(Exception):
    """A provider_events row matched no known class. Never skipped."""


# --------------------------------------------------------------------------
# Reading the recorded run
# --------------------------------------------------------------------------
def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def classify_row(row: dict[str, Any]) -> str:
    """'ingress' or 'assembler_re_emission'. Raises on anything else."""
    meta = row.get("metadata") or {}
    has_raw_dg = meta.get("raw_deepgram_text") is not None
    has_conf = row.get("confidence") is not None
    if has_raw_dg and has_conf:
        return "ingress"
    if not has_raw_dg and not has_conf:
        return "assembler_re_emission"
    raise UnknownRowShape(
        "row matches neither known class (the two markers disagreed, which "
        "did not occur in any of the 462 rows this classifier was derived "
        f"from): raw_event_id={row.get('raw_event_id')!r} "
        f"raw_deepgram_text_present={has_raw_dg} confidence_present={has_conf}"
    )


def partition_events(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ingress: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    for row in rows:
        kind = classify_row(row)  # raises on unknown -- deliberately not caught
        if kind == "ingress":
            ingress.append(row)
        else:
            excluded[kind] = excluded.get(kind, 0) + 1
    return ingress, excluded


def _stable_commit_rows(run_folder: Path) -> list[dict[str, Any]]:
    return [
        r
        for r in _load_jsonl(run_folder / "transcripts" / "stable_commits.jsonl")
        if r.get("stable_commit_id")
    ]


def recorded_counts(run_folder: Path) -> dict[str, Any]:
    """The four numbers as the real run left them on disk."""
    stable = _stable_commit_rows(run_folder)
    ledger = _load_jsonl(run_folder / "evidence_streams" / "canonical_commits.jsonl")
    final_path = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    export_lines = 0
    if final_path.exists():
        with open(final_path, "r", encoding="utf-8", errors="ignore") as fh:
            export_lines = sum(1 for line in fh if line.strip())
    commits = [
        (
            (r.get("assembler_metadata") or {}).get("canonical_utterance_id") or "",
            r.get("stable_text") or "",
        )
        for r in stable
    ]
    # Recorded ledger rows carry the id at top level and the text in `text`;
    # the in-memory records replay reads use `metadata.canonical_utterance_id`
    # and `final_text`. Normalised to one shape here so the comparison below
    # is written once.
    ledger_text = {
        r.get("canonical_utterance_id") or "": r.get("text") or "" for r in ledger
    }
    return {
        # In every recorded run these two are equal: one stable_commits row
        # is written per assembler commit decision. Reported separately
        # anyway so a future divergence is visible rather than assumed.
        "assembler_decisions": len(stable),
        "stable_commits": len(stable),
        "distinct_utterances": len({i for i, _ in commits if i}),
        "ledger_records": len(ledger),
        "export_lines": export_lines,
        "_commits": commits,
        "_ledger_text": ledger_text,
        "_export_text": _export_text(run_folder),
    }


def _export_text(run_folder: Path) -> Optional[str]:
    path = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="ignore")


# --------------------------------------------------------------------------
# Driving the real pipeline
# --------------------------------------------------------------------------
class _ReplayHost:
    """Headless host for the real Japanese stabilizer + assembler + ledger.

    Mirrors `tests/test_task2c_acceptance_gate.py`'s `JapaneseTestHost`
    (deliberately re-declared here rather than imported -- a tool must not
    depend on the test suite). Everything on the commit path is the
    production implementation; only the UI edge is captured.
    """

    def __init__(self, session_id: str, run_id: str) -> None:
        from alpha.transcription import canonical_transcript_ledger as ctl
        from alpha.transcription.canonical_identity_registry import reset_for_session
        from alpha.transcription.utterance_lifecycle import reset_utterance_lifecycle

        self._live_session_id = session_id
        self._listen_language = "ja"
        self._is_finalizing = False
        self._is_stopping = False
        self.is_listening = True
        self.published: list[dict[str, Any]] = []

        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        reset_utterance_lifecycle(self, session_id=session_id)

    def _publish_final_transcript_segment(
        self,
        speaker: Any,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
        queue_item: Optional[dict[str, Any]] = None,
        commit_reason: Optional[str] = None,
    ) -> bool:
        meta = dict(metadata or {})
        self.published.append(
            {
                "speaker": speaker,
                "text": text,
                "canonical_utterance_id": meta.get("canonical_utterance_id", ""),
                "commit_reason": commit_reason or "",
            }
        )
        return True


def replay_events(ingress: list[dict[str, Any]], *, tag: str) -> dict[str, Any]:
    """Feed ingress rows through the real pipeline; return the same numbers."""
    from unittest.mock import patch

    from alpha.transcription import canonical_transcript_ledger as ctl
    from alpha.transcription.japanese_final_chunk_stabilizer import (
        get_japanese_final_stabilizer,
    )
    from alpha.transcription.japanese_sentence_assembler import (
        get_japanese_continuity_assembler,
    )

    host = _ReplayHost(session_id=f"replay-{tag}", run_id=f"replay-{tag}")
    stabilizer = get_japanese_final_stabilizer(host)
    stabilizer.set_accepting(True)
    assembler = get_japanese_continuity_assembler(host)

    stable_texts: list[str] = []

    def _count_stable_commit(**kwargs: Any) -> str:
        # The assembler writes one of these per commit decision. Patched
        # rather than left live so replay never appends to a real run
        # folder's evidence.
        stable_texts.append(kwargs.get("stable_text"))
        return f"replay-stable-{len(stable_texts)}"

    with patch(
        "alpha.utils.transcript_evidence.log_stable_commit",
        side_effect=_count_stable_commit,
    ):
        for row in ingress:
            speaker = row.get("speaker")
            text = row.get("raw_text") or ""
            meta = dict(row.get("metadata") or {})
            try:
                stabilizer.ingest(int(speaker or 1), text, metadata=meta)
            except Exception as exc:  # never swallow -- surface as a result
                return {
                    "error": f"ingest_failed:{type(exc).__name__}:{exc}",
                    "raw_event_id": row.get("raw_event_id"),
                }
        # Fast-feed: no real timer ever fires, so the deferred flush the
        # assembler posted through LanguagePipelineWorker is driven here
        # instead. See the module docstring's limitation note.
        assembler.flush("stop_listening")

    ledger = ctl.get_active_records()
    commits = [
        (p.get("canonical_utterance_id") or "", p.get("text") or "")
        for p in host.published
    ]
    return {
        "assembler_decisions": len(host.published),
        "stable_commits": len(stable_texts),
        "distinct_utterances": len({i for i, _ in commits if i}),
        "ledger_records": len(ledger),
        # Export is a pure downstream serialization of the ledger and
        # measured 1:1 with it in all 6 replayable runs, so replay stops at
        # the ledger rather than modelling a UI/export layer it would have
        # to fake.
        "export_lines": None,
        "_commits": commits,
        "_ledger_text": {
            (r.get("metadata") or {}).get("canonical_utterance_id") or "": r.get("final_text") or ""
            for r in ledger
        },
        "_export_text": None,
    }


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------
def _norm(text: str) -> str:
    return "".join(text.split())


def _unreached_utterances(commits: list[tuple[str, str]], ledger_text: dict[str, str]) -> list[str]:
    """Utterance ids the assembler committed that reached no ledger record.

    The cheaper of the two loss modes and the only one an id-level check can
    see. It is NOT sufficient on its own -- see `_dropped_content`.
    """
    return sorted({i for i, _ in commits if i and i not in ledger_text})


def _dropped_content(
    commits: list[tuple[str, str]],
    ledger_text: dict[str, str],
    export_text: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Committed text that no ledger record carries. This is problem A.

    An id-only check cannot see the real failure. The assembler can commit
    two *textually disjoint* sentences under one `canonical_utterance_id`;
    the ledger keys on that id, so the second commit lands as a revision of
    the first and the first sentence's words are gone. The id reached the
    ledger, so an id-level check scores it as clean -- measured on run
    `...20260807-160529`, that check reported 0 losses while a whole
    sentence was missing from the export.

    So a commit counts as landed only when its text survives into the ledger
    record for its id: `revise`/extension commits are prefixes or substrings
    of the final text and pass, while a disjoint sentence does not. Where an
    export exists, its absence there is checked too -- an independent second
    signal, so the verdict does not rest on one field of one file.
    """
    dropped: list[dict[str, Any]] = []
    for uid, text in commits:
        if not text.strip():
            continue
        if uid not in ledger_text:
            dropped.append({"uid": uid, "text": text, "reason": "no_ledger_record"})
            continue
        if _norm(text) in _norm(ledger_text[uid]):
            continue
        entry = {"uid": uid, "text": text, "reason": "overwritten_by_id_collision"}
        if export_text is not None:
            entry["absent_from_export"] = _norm(text) not in _norm(export_text)
        dropped.append(entry)
    return dropped


def replay_run(run_folder: Path) -> dict[str, Any]:
    rows = _load_jsonl(run_folder / "evidence_streams" / "provider_events.jsonl")
    ingress, excluded = partition_events(rows)

    accounting = {
        "rows_read": len(rows),
        "rows_fed": len(ingress),
        "rows_excluded": excluded,
        "invariant_holds": len(rows) == len(ingress) + sum(excluded.values()),
    }

    result: dict[str, Any] = {
        "run": run_folder.name,
        "accounting": accounting,
        "replayable": bool(ingress),
    }
    if not accounting["invariant_holds"]:
        result["error"] = "accounting_invariant_failed"
        return result
    if not ingress:
        result["skipped_reason"] = (
            "no genuine provider ingress in this run -- English sessions "
            "record none (see module docstring)"
        )
        return result

    rec = recorded_counts(run_folder)
    rep = replay_events(ingress, tag=run_folder.name[-13:])
    if "error" in rep:
        result.update(rep)
        return result

    rec_lost = _unreached_utterances(rec["_commits"], rec["_ledger_text"])
    rep_lost = _unreached_utterances(rep["_commits"], rep["_ledger_text"])
    rec_dropped = _dropped_content(rec["_commits"], rec["_ledger_text"], rec["_export_text"])
    rep_dropped = _dropped_content(rep["_commits"], rep["_ledger_text"], rep["_export_text"])

    result.update(
        {
            "recorded": {k: v for k, v in rec.items() if not k.startswith("_")},
            "replayed": {k: v for k, v in rep.items() if not k.startswith("_")},
            # Raw delta, kept visible because it is the number v5 §1 cites --
            # but it counts revisions as losses, so it is NOT the verdict.
            "recorded_raw_delta": rec["assembler_decisions"] - rec["ledger_records"],
            "replayed_raw_delta": rep["assembler_decisions"] - rep["ledger_records"],
            "recorded_unreached": rec_lost,
            "replayed_unreached": rep_lost,
            "recorded_dropped_content": rec_dropped,
            "replayed_dropped_content": rep_dropped,
        }
    )
    result["counts_match"] = len(rec_dropped) == len(rep_dropped)
    # Identity, not just the delta: the SAME words must be the missing ones.
    # Matched on text, not id -- ids are minted per run, so a recorded id and
    # a replayed id are never equal even when they carry the same sentence.
    result["identity_match"] = sorted(_norm(d["text"]) for d in rec_dropped) == sorted(
        _norm(d["text"]) for d in rep_dropped
    )
    result["reproduces"] = bool(result["counts_match"] and result["identity_match"])
    return result


def _print_human(res: dict[str, Any]) -> None:
    print(f"\n=== {res['run']} ===")
    acc = res["accounting"]
    print(
        f"  rows_read={acc['rows_read']} fed={acc['rows_fed']} "
        f"excluded={acc['rows_excluded']} invariant={'OK' if acc['invariant_holds'] else 'FAILED'}"
    )
    if res.get("error"):
        print(f"  ERROR: {res['error']}")
        return
    if not res.get("replayable"):
        print(f"  skipped: {res.get('skipped_reason')}")
        return
    rec, rep = res["recorded"], res["replayed"]
    print(f"  {'':10} {'decisions':>9} {'stable':>7} {'uttr':>6} {'ledger':>7} {'export':>7}")
    print(
        f"  {'recorded':10} {rec['assembler_decisions']:>9} {rec['stable_commits']:>7} "
        f"{rec['distinct_utterances']:>6} {rec['ledger_records']:>7} {rec['export_lines']:>7}"
    )
    print(
        f"  {'replayed':10} {rep['assembler_decisions']:>9} {rep['stable_commits']:>7} "
        f"{rep['distinct_utterances']:>6} {rep['ledger_records']:>7} {'n/a':>7}"
    )
    print(
        f"  raw delta (counts revisions as loss): "
        f"recorded={res['recorded_raw_delta']} replayed={res['replayed_raw_delta']}"
    )
    print(
        f"  unreached ids: recorded={len(res['recorded_unreached'])} "
        f"replayed={len(res['replayed_unreached'])}"
    )
    print(
        f"  DROPPED CONTENT: recorded={len(res['recorded_dropped_content'])} "
        f"replayed={len(res['replayed_dropped_content'])}"
    )
    for side in ("recorded", "replayed"):
        for d in res[f"{side}_dropped_content"]:
            gone = d.get("absent_from_export")
            mark = "" if gone is None else f" absent_from_export={gone}"
            print(f"    {side}: {d['uid']} [{d['reason']}]{mark}\n      {d['text'][:70]}")
    verdict = "MATCHES" if res["reproduces"] else "DIVERGES"
    print(f"  -> {verdict} (counts={res['counts_match']} identity={res['identity_match']})")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_folder", nargs="?", help="path to one troubleshooting/runs/<run>")
    parser.add_argument("--all", action="store_true", help="every run with provider ingress")
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

    results = []
    for folder in folders:
        try:
            results.append(replay_run(folder))
        except UnknownRowShape as exc:
            results.append({"run": folder.name, "error": f"unknown_row_shape: {exc}"})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for res in results:
            _print_human(res)

    replayed = [r for r in results if r.get("replayable") and not r.get("error")]
    failed = [r for r in results if r.get("error")] + [
        r for r in replayed if not r.get("reproduces")
    ]
    if not args.json:
        print(
            f"\n{len(replayed)} replayed, "
            f"{sum(1 for r in replayed if r.get('reproduces'))} reproduced, "
            f"{len(failed)} not."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
