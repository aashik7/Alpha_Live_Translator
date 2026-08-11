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

TWO REPLAY MODES
----------------
`replay_events` (default) feeds every row back to back; original
inter-event timestamps are not honoured, and neither is
`LanguagePipelineWorker`'s real scheduling thread. `replay_events_real_timer`
(`--real-timer`, item 38b) instead starts the real worker and sleeps the
real recorded gap between rows, so the deferred flush production posts
via `schedule_flush(assembler, due_mono, generation, reason)` executes
against real wall-clock time exactly as it would live.

**Measured 2026-08-11, fast-feed: the loss pattern does not reproduce.**
All 14 recorded losses across the 6 replayable runs are
`overwritten_by_id_collision`; fast-feed reproduces 0 of them, and the
segmentation itself diverges — identical ingress yields 13 replayed
commit decisions against 29 recorded on `...134815`, 19 against 32 on
`...155334`. That divergence was consistent with "no timer fires
mid-stream", which read as timing-dependent.

**Measured 2026-08-11, real-timer (item 38b): that reading was wrong.**
Real timing fixes the segmentation divergence -- decision counts now
match or come within 1 on every run (`...134815` 29 vs 29, `...155334`
33 vs 32, full table in `CLIENT_DELIVERY_SPRINT_v5.md` §9) -- but content
loss still reproduces **0 of 14** even so. Timing was necessary to
explain the decision-*count* mismatch and is not sufficient to explain
the *content-loss* mechanism; those are two separate things this tool
originally conflated. See `_dropped_content`'s docstring for the
mechanism and §9 for the code-level lead this handed to item 41 -- a
disagreement between `update_previous_requested` (set before
`decide_stable_revision_action` runs) and that function's own
`final_revision_action` verdict, cross-validated against
`japanese_accuracy.log`'s `STABLE_REVISION_DECISION` events on 12 of 13
observed id-reuse cases across all 6 runs. Why real-timer replay still
does not reproduce it is unresolved -- two structural explanations
(interim-stream dependency, a bypassed boundary-stabilizer) were checked
and ruled out; this is as far as item 38b's scope goes, and it is item
41's starting point, not its answer.

Both findings stand on their own: fast-feed is deliberately not tuned to
agree with the recorded run, and neither is real-timer -- this harness
reports what the real pipeline does, not what would make a number match.

USAGE
-----
    python tools/replay_run.py <run_folder> [--json]
    python tools/replay_run.py --all [--json]
    python tools/replay_run.py <run_folder> --real-timer [--json]  # item 38b, slow

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


def _replay_result(host: "_ReplayHost", stable_texts: list[str], ctl: Any) -> dict[str, Any]:
    """Shared tail for both replay modes: read back what the real pipeline did."""
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


def replay_events(ingress: list[dict[str, Any]], *, tag: str) -> dict[str, Any]:
    """Feed ingress rows through the real pipeline as fast as Python allows.

    No timer fires mid-stream -- see the module docstring's limitation note
    and `replay_events_real_timer` below, which exists because this mode
    measurably does not reproduce problem A.
    """
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

    return _replay_result(host, stable_texts, ctl)


def _recorded_gaps_s(ingress: list[dict[str, Any]]) -> list[float]:
    """Real inter-arrival gaps in seconds, from each row's recorded `timestamp`.

    Clamped to >= 0 -- recorded timestamps are expected monotonic, but a
    replay tool must not raise or hang on a row that violates that rather
    than surfacing it as a wrong result.
    """
    gaps: list[float] = []
    for a, b in zip(ingress, ingress[1:]):
        ta, tb = a.get("timestamp"), b.get("timestamp")
        gaps.append(max(0.0, float(tb) - float(ta)) if ta is not None and tb is not None else 0.0)
    return gaps


def replay_events_real_timer(ingress: list[dict[str, Any]], *, tag: str) -> dict[str, Any]:
    """Item 38b. Same pipeline as `replay_events`, driven by the real
    `LanguagePipelineWorker` thread across real recorded wall-clock gaps
    instead of feeding every row back-to-back.

    Why this exists: `replay_events` measured 0/14 losses reproduced and the
    commit counts it produced diverged outright from the recorded run (13 vs
    29 decisions on one run). Item 38's docstring traced that to no timer
    ever firing mid-stream. Whether a hold fires before the next fragment
    arrives is what problem A's id-reuse depends on (see
    `japanese_sentence_assembler.py`'s `_schedule_flush`/
    `try_execute_continuity_hold`), and that is a real-elapsed-time
    question -- SENTENCE_HOLD_MIN_MS/MAX_MS are 2000-3500ms, and the median
    recorded gap between ingress rows is several seconds, so scaling the
    waits down would silently change which side of the threshold most gaps
    fall on. This mode is therefore real-time, on purpose: it takes roughly
    as long as the original recording did (see `--all --real-timer`'s
    printed total before it starts).

    Uses the process-global `LanguagePipelineWorker` singleton via
    `start_language_pipeline_worker()`/`stop_and_join()` -- the same pair
    production calls at Start/Stop -- rather than constructing a private
    instance, because the assembler resolves the worker through that same
    singleton internally and cannot be handed a different one without
    patching module state that production does not patch.
    """
    import time
    from unittest.mock import patch

    from alpha.transcription import canonical_transcript_ledger as ctl
    from alpha.transcription.japanese_final_chunk_stabilizer import (
        get_japanese_final_stabilizer,
    )
    from alpha.transcription.japanese_sentence_assembler import (
        get_japanese_continuity_assembler,
    )
    from alpha.utils.language_pipeline_worker import (
        get_language_pipeline_worker,
        start_language_pipeline_worker,
    )

    host = _ReplayHost(session_id=f"replay-rt-{tag}", run_id=f"replay-rt-{tag}")
    stabilizer = get_japanese_final_stabilizer(host)
    stabilizer.set_accepting(True)
    assembler = get_japanese_continuity_assembler(host)
    gaps = _recorded_gaps_s(ingress)

    stable_texts: list[str] = []

    def _count_stable_commit(**kwargs: Any) -> str:
        stable_texts.append(kwargs.get("stable_text"))
        return f"replay-rt-stable-{len(stable_texts)}"

    start_language_pipeline_worker()
    try:
        with patch(
            "alpha.utils.transcript_evidence.log_stable_commit",
            side_effect=_count_stable_commit,
        ):
            for i, row in enumerate(ingress):
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
                if i < len(gaps):
                    time.sleep(gaps[i])
            # Same call production's Stop button makes; only what happened
            # DURING ingestion (mid-stream real timer fires) differs from
            # replay_events. See the class docstring's rationale.
            assembler.flush("stop_listening")
            # try_execute_continuity_hold's non-blocking try_acquire can lose
            # a race to flush() above and reschedule itself 50ms out (see
            # language_pipeline_worker.py); give it one window to land before
            # reading back state, same margin _run_flush's own retry uses.
            time.sleep(0.1)
    finally:
        get_language_pipeline_worker().stop_and_join(timeout_seconds=2.0)

    return _replay_result(host, stable_texts, ctl)


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


def replay_run(run_folder: Path, *, real_timer: bool = False) -> dict[str, Any]:
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
        "mode": "real_timer" if real_timer else "fast_feed",
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
    import time as _time

    started = _time.monotonic()
    if real_timer:
        rep = replay_events_real_timer(ingress, tag=run_folder.name[-13:])
        result["recorded_real_gap_total_s"] = round(sum(_recorded_gaps_s(ingress)), 1)
    else:
        rep = replay_events(ingress, tag=run_folder.name[-13:])
    result["replay_wall_s"] = round(_time.monotonic() - started, 1)
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
    print(f"\n=== {res['run']} [{res.get('mode', 'fast_feed')}] ===")
    acc = res["accounting"]
    print(
        f"  rows_read={acc['rows_read']} fed={acc['rows_fed']} "
        f"excluded={acc['rows_excluded']} invariant={'OK' if acc['invariant_holds'] else 'FAILED'}"
    )
    if "replay_wall_s" in res:
        print(f"  replay_wall_s={res['replay_wall_s']}")
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
    parser.add_argument(
        "--real-timer",
        action="store_true",
        help=(
            "item 38b: drive the real LanguagePipelineWorker across real "
            "recorded wall-clock gaps instead of fast-feeding every row. "
            "Real-time, on purpose (see replay_events_real_timer's "
            "docstring) -- a single run can take several minutes."
        ),
    )
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

    if args.real_timer and not args.json:
        print(
            "--real-timer: replaying at real recorded speed, this will take "
            "a while per run (see each run's provider_events.jsonl span).",
            file=sys.stderr,
        )

    results = []
    for folder in folders:
        try:
            results.append(replay_run(folder, real_timer=args.real_timer))
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
