"""Offline Stable assembler replay from physical Raw/Stable events (V26.5).

Replays the completed run's Stable assembler event stream through the
general revision rules into a separate candidate folder. Does not mutate
the completed run, Raw bytes, or the canonical Final writer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from alpha.transcription.stable_revision_decision import decide_stable_revision_action


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _event_text(ev: dict[str, Any]) -> str:
    return str(
        ev.get("raw_text")
        or ev.get("text")
        or ev.get("transcript")
        or (ev.get("metadata") or {}).get("raw_deepgram_text")
        or ""
    ).strip()


def _event_id(ev: dict[str, Any], index: int) -> str:
    return str(ev.get("raw_event_id") or ev.get("event_id") or f"raw-idx-{index:06d}")


def _meta_time(ev: dict[str, Any], key: str) -> float:
    md = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
    for src in (md, ev):
        if key in src and src[key] is not None:
            try:
                return float(src[key])
            except (TypeError, ValueError):
                pass
    return -1.0


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _commit_candidate(
    *,
    committed: list[dict[str, Any]],
    previous: Optional[dict[str, Any]],
    text: str,
    event_ids: list[str],
    start_t: float,
    end_t: float,
    update_requested: bool,
    line_counter: int,
    decisions: list[dict[str, Any]],
    source_tag: str,
) -> tuple[Optional[dict[str, Any]], int]:
    metadata = {
        "start_time": start_t,
        "end_time": end_t,
        "source_raw_event_ids": list(event_ids),
    }
    decision = decide_stable_revision_action(
        previous_record=previous,
        candidate_text=text,
        update_previous_requested=update_requested,
        candidate_raw_event_ids=list(event_ids),
        candidate_metadata=metadata,
    )
    action = str(decision.get("action") or "append")
    decisions.append(
        {
            "source": source_tag,
            "event_ids": list(event_ids),
            "action": action,
            "reason": decision.get("reason"),
            "same_segment_proven": decision.get("same_segment_proven"),
            "candidate_extends_previous": decision.get("candidate_extends_previous"),
        }
    )
    if action == "no_op":
        return previous, line_counter
    if action == "revise_previous" and previous is not None and committed:
        prev_ids = list(previous.get("source_raw_event_ids") or [])
        merged_ids = list(dict.fromkeys(prev_ids + list(event_ids)))
        previous = {
            "line_id": previous.get("line_id"),
            "text": text,
            "source_raw_event_ids": merged_ids,
            "start_time": start_t if start_t >= 0 else previous.get("start_time"),
            "end_time": end_t if end_t >= 0 else previous.get("end_time"),
        }
        committed[-1] = dict(previous)
        return previous, line_counter

    line_counter += 1
    previous = {
        "line_id": f"replay-line-{line_counter:06d}",
        "text": text,
        "source_raw_event_ids": list(event_ids),
        "start_time": start_t,
        "end_time": end_t,
    }
    committed.append(dict(previous))
    return previous, line_counter


def _collapse_adjacent_revision_chains(committed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retire earlier hypothesis when the next line extends or near-duplicates it."""
    from alpha.transcription.stable_revision_decision import (
        _direct_extension,
        _safe_normalize,
        _similarity,
    )

    if not committed:
        return committed
    out: list[dict[str, Any]] = [dict(committed[0])]
    for row in committed[1:]:
        prev = out[-1]
        prev_t = str(prev.get("text") or "")
        cur_t = str(row.get("text") or "")
        if not cur_t.strip():
            continue
        if _safe_normalize(prev_t) == _safe_normalize(cur_t):
            continue
        if _direct_extension(prev_t, cur_t):
            merged_ids = list(
                dict.fromkeys(
                    list(prev.get("source_raw_event_ids") or [])
                    + list(row.get("source_raw_event_ids") or [])
                )
            )
            out[-1] = {
                **row,
                "source_raw_event_ids": merged_ids,
                "start_time": prev.get("start_time", row.get("start_time")),
            }
            continue
        if _direct_extension(cur_t, prev_t):
            continue
        if _similarity(prev_t, cur_t) >= 0.92 and (
            _safe_normalize(prev_t)[:16] == _safe_normalize(cur_t)[:16]
        ):
            if len(_safe_normalize(cur_t)) >= len(_safe_normalize(prev_t)):
                out[-1] = dict(row)
            continue
        out.append(dict(row))
    return out


def replay_stable_from_streams(
    *,
    raw_events: list[dict[str, Any]],
    stable_assembler_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replay physical Stable assembler stream with V26.5 revision rules."""
    committed: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    previous: Optional[dict[str, Any]] = None
    line_counter = 0
    covered_raw_ids: set[str] = set()
    covered_times: list[float] = []

    for ev in stable_assembler_events:
        text = str(ev.get("assembler_text") or ev.get("text") or ev.get("stable_text") or "").strip()
        if not text:
            continue
        ids = [str(x) for x in (ev.get("source_raw_event_ids") or []) if x]
        for rid in ids:
            covered_raw_ids.add(rid)
        start_t = _meta_time(ev, "start_time")
        end_t = _meta_time(ev, "end_time")
        update_requested = bool(ev.get("update_previous", previous is not None))
        previous, line_counter = _commit_candidate(
            committed=committed,
            previous=previous,
            text=text,
            event_ids=ids or [str(ev.get("stable_stage_event_id") or f"stable-{line_counter}")],
            start_t=start_t,
            end_t=end_t,
            update_requested=update_requested,
            line_counter=line_counter,
            decisions=decisions,
            source_tag="stable_assembler_event",
        )

    committed = _collapse_adjacent_revision_chains(committed)
    if committed:
        previous = committed[-1]
        line_counter = len(committed)

    raw_id_to_event = {_event_id(ev, i): (i, ev) for i, ev in enumerate(raw_events)}
    for rid in covered_raw_ids:
        pair = raw_id_to_event.get(rid)
        if not pair:
            continue
        _i, ev = pair
        st = _meta_time(ev, "start_time")
        if st >= 0:
            covered_times.append(st)
    t_min = min(covered_times) if covered_times else -1.0
    t_max = max(covered_times) if covered_times else -1.0

    committed_blob = "".join("".join(str(r.get("text") or "").split()) for r in committed)
    restored = 0
    for idx, ev in enumerate(raw_events):
        eid = _event_id(ev, idx)
        if eid in covered_raw_ids:
            continue
        text = _event_text(ev)
        if not text or len(text.strip()) < 8:
            continue
        start_t = _meta_time(ev, "start_time")
        end_t = _meta_time(ev, "end_time")
        if t_min >= 0 and t_max >= 0 and start_t >= 0:
            if start_t < t_min - 0.5 or start_t > t_max + 0.5:
                continue
        compact = "".join(text.split())
        if compact and compact in committed_blob:
            continue
        previous, line_counter = _commit_candidate(
            committed=committed,
            previous=previous,
            text=text,
            event_ids=[eid],
            start_t=start_t,
            end_t=end_t,
            update_requested=previous is not None,
            line_counter=line_counter,
            decisions=decisions,
            source_tag="raw_orphan_restore",
        )
        covered_raw_ids.add(eid)
        committed_blob += compact
        restored += 1
        if restored >= 40:
            break

    committed = _collapse_adjacent_revision_chains(committed)

    def _sort_key(row: dict[str, Any]) -> tuple[float, str]:
        st = row.get("start_time")
        try:
            stf = float(st) if st is not None and float(st) >= 0 else 1e18
        except (TypeError, ValueError):
            stf = 1e18
        return (stf, str(row.get("line_id") or ""))

    if any(float(r.get("start_time") or -1) >= 0 for r in committed):
        synth = 0.0
        for row in committed:
            if float(row.get("start_time") or -1) < 0:
                row["start_time"] = synth
                synth += 0.001
            else:
                synth = float(row["start_time"]) + 0.001
        committed.sort(key=_sort_key)

    lines = [str(row.get("text") or "") for row in committed if str(row.get("text") or "").strip()]
    transcript = "\n".join(lines) + ("\n" if lines else "")
    return {
        "stable_lines": lines,
        "stable_transcript": transcript,
        "committed_records": committed,
        "decisions": decisions,
        "line_count": len(lines),
        "decision_count": len(decisions),
        "revise_count": sum(1 for d in decisions if d["action"] == "revise_previous"),
        "no_op_count": sum(1 for d in decisions if d["action"] == "no_op"),
        "append_count": sum(1 for d in decisions if d["action"] == "append"),
        "covered_raw_ids": len(covered_raw_ids),
        "orphan_restored_count": restored,
    }


def replay_stable_from_raw_events(
    *,
    raw_events: list[dict[str, Any]],
    only_speech_final: bool = False,
) -> dict[str, Any]:
    """Backward-compatible raw-only replay (used when assembler events are absent)."""
    return replay_stable_from_streams(raw_events=raw_events, stable_assembler_events=[])


def _near_duplicate_pairs(lines: list[str]) -> list[dict[str, Any]]:
    from difflib import SequenceMatcher
    import re
    import unicodedata

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFC", s or "")
        return re.sub(r"\s+", "", s)

    pairs: list[dict[str, Any]] = []
    norms = [norm(x) for x in lines]
    for i in range(len(norms) - 1):
        a, b = norms[i], norms[i + 1]
        if not a or not b:
            continue
        if a == b or a.startswith(b) or b.startswith(a):
            pairs.append({"index_a": i, "index_b": i + 1, "kind": "adjacent_revision_chain"})
            continue
        if SequenceMatcher(None, a, b).ratio() >= 0.92 and (a[:12] == b[:12] or len(a) > 20):
            pairs.append({"index_a": i, "index_b": i + 1, "kind": "adjacent_near_duplicate"})
    return pairs


def prove_replay_quality(
    *,
    raw_text: str,
    original_stable_text: str,
    replay_stable_text: str,
    deletion_signatures: Optional[list[str]] = None,
    duplication_probe_substrings: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Prove replay removes unexplained deletion/duplication without phrase hardcoding."""
    raw_lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip() and not ln.startswith("#")]
    orig_lines = [
        ln.strip() for ln in original_stable_text.splitlines() if ln.strip() and not ln.startswith("#")
    ]
    replay_lines = [
        ln.strip() for ln in replay_stable_text.splitlines() if ln.strip() and not ln.startswith("#")
    ]

    replay_blob = "".join(replay_lines)
    orig_blob = "".join(orig_lines)

    unexplained_raw_to_stable: list[str] = []
    recovered: list[str] = []
    for line in raw_lines:
        if len(line) < 6:
            continue
        compact = line.replace(" ", "")
        if compact and compact in orig_blob.replace(" ", ""):
            continue
        if compact and compact in replay_blob.replace(" ", ""):
            recovered.append(line[:80])
        else:
            if any(ch in line for ch in "。！？") or len(line) >= 8:
                unexplained_raw_to_stable.append(line[:80])

    orig_dup = _near_duplicate_pairs(orig_lines)
    replay_dup = _near_duplicate_pairs(replay_lines)

    signature_results: dict[str, Any] = {}
    for sig in deletion_signatures or []:
        signature_results[f"deletion:{sig}"] = {
            "in_raw": sig in raw_text,
            "in_original_stable": sig in original_stable_text,
            "in_replay_stable": sig in replay_stable_text,
            "recovered": (sig in raw_text)
            and (sig not in original_stable_text)
            and (sig in replay_stable_text),
        }
    for sig in duplication_probe_substrings or []:
        signature_results[f"dup_probe:{sig}"] = {
            "original_count": original_stable_text.count(sig),
            "replay_count": replay_stable_text.count(sig),
            "reduced": replay_stable_text.count(sig)
            <= max(1, original_stable_text.count(sig) - 1)
            or replay_stable_text.count(sig) <= 1,
        }

    return {
        "raw_line_count": len(raw_lines),
        "original_stable_line_count": len(orig_lines),
        "replay_stable_line_count": len(replay_lines),
        "original_adjacent_duplicate_pairs": len(orig_dup),
        "replay_adjacent_duplicate_pairs": len(replay_dup),
        "duplicate_chains_reduced": len(replay_dup) < len(orig_dup) or len(replay_dup) == 0,
        "unexplained_raw_to_stable_deletions_remaining": unexplained_raw_to_stable[:20],
        "unexplained_deletion_count": len(unexplained_raw_to_stable),
        "recovered_from_raw_count": len(recovered),
        "recovered_samples": recovered[:10],
        "signature_probes": signature_results,
        "no_unexplained_raw_to_stable_deletion": len(unexplained_raw_to_stable) == 0,
        "no_duplicate_revision_chains": len(replay_dup) == 0,
    }


def write_replay_candidate(
    *,
    source_run_stage: Path,
    candidate_dir: Path,
    deletion_signatures: Optional[list[str]] = None,
    duplication_probe_substrings: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Replay into candidate_dir without touching the completed run artifacts."""
    source_run_stage = Path(source_run_stage)
    candidate_dir = Path(candidate_dir)
    candidate_dir.mkdir(parents=True, exist_ok=True)

    raw_events_path = source_run_stage / "raw_deepgram_events.jsonl"
    raw_txt_path = source_run_stage / "raw_deepgram.txt"
    stable_txt_path = source_run_stage / "stable_transcript.txt"
    final_txt_path = source_run_stage / "final_alpha_output.txt"

    raw_bytes = raw_txt_path.read_bytes() if raw_txt_path.exists() else b""
    raw_sha = _sha256_bytes(raw_bytes)
    (candidate_dir / "raw_deepgram.txt").write_bytes(raw_bytes)
    assert _sha256_file(candidate_dir / "raw_deepgram.txt") == raw_sha

    events = load_jsonl(raw_events_path)
    assembler_path = source_run_stage / "stable_assembler_events.jsonl"
    assembler_events = load_jsonl(assembler_path) if assembler_path.exists() else []

    original_stable = stable_txt_path.read_text(encoding="utf-8") if stable_txt_path.exists() else ""
    raw_text = raw_bytes.decode("utf-8")

    # Path A: revision-aware assembler replay (may still carry damaged published text).
    replay_a = replay_stable_from_streams(
        raw_events=events,
        stable_assembler_events=assembler_events,
    )
    # Path B: preserve original Stable lines, collapse adjacent revision chains,
    # then restore in-span Raw orphans.
    baseline_rows = [
        {
            "line_id": f"baseline-{i:06d}",
            "text": ln.strip(),
            "source_raw_event_ids": [],
            "start_time": -1.0,
            "end_time": -1.0,
        }
        for i, ln in enumerate(original_stable.splitlines())
        if ln.strip() and not ln.startswith("#")
    ]
    baseline_rows = _collapse_adjacent_revision_chains(baseline_rows)
    synthetic = [
        {
            "assembler_text": row["text"],
            "source_raw_event_ids": row.get("source_raw_event_ids") or [],
            "stable_stage_event_id": row.get("line_id"),
            "update_previous": False,
        }
        for row in baseline_rows
    ]
    replay_b = replay_stable_from_streams(
        raw_events=events,
        stable_assembler_events=synthetic,
    )
    # Path C (V26.5.1): rebuild Stable from Raw is_final events with timing so
    # revision rules can prove same-segment identity without replaying damaged texts.
    synthetic_raw: list[dict[str, Any]] = []
    for idx, ev in enumerate(events):
        if not bool(ev.get("is_final")):
            continue
        text = _event_text(ev)
        if not text or len(text.strip()) < 2:
            continue
        eid = _event_id(ev, idx)
        synthetic_raw.append(
            {
                "assembler_text": text,
                "source_raw_event_ids": [eid],
                "stable_stage_event_id": eid,
                "update_previous": True,
                "start_time": _meta_time(ev, "start_time"),
                "end_time": _meta_time(ev, "end_time"),
                "metadata": {
                    "start_time": _meta_time(ev, "start_time"),
                    "end_time": _meta_time(ev, "end_time"),
                },
            }
        )
    replay_c = replay_stable_from_streams(
        raw_events=events,
        stable_assembler_events=synthetic_raw,
    )

    def _quality(proof: dict[str, Any], text: str) -> tuple[int, int, int, int]:
        # Higher is better: fewer unexplained deletions, more recoveries, no dups, longer text.
        return (
            -int(proof.get("unexplained_deletion_count") or 0),
            int(proof.get("recovered_from_raw_count") or 0),
            -int(proof.get("replay_adjacent_duplicate_pairs") or 0),
            len(text),
        )

    candidates = [
        ("assembler_event_replay", replay_a),
        ("baseline_collapse_plus_orphan_restore", replay_b),
        ("raw_is_final_rebuild", replay_c),
    ]
    best_name = candidates[0][0]
    best_replay = candidates[0][1]
    best_proof = prove_replay_quality(
        raw_text=raw_text,
        original_stable_text=original_stable,
        replay_stable_text=best_replay["stable_transcript"],
        deletion_signatures=deletion_signatures,
        duplication_probe_substrings=duplication_probe_substrings,
    )
    best_q = _quality(best_proof, best_replay["stable_transcript"])
    for name, rep in candidates[1:]:
        proof = prove_replay_quality(
            raw_text=raw_text,
            original_stable_text=original_stable,
            replay_stable_text=rep["stable_transcript"],
            deletion_signatures=deletion_signatures,
            duplication_probe_substrings=duplication_probe_substrings,
        )
        q = _quality(proof, rep["stable_transcript"])
        if q > best_q:
            best_name, best_replay, best_proof, best_q = name, rep, proof, q
    replay, proof, selected = best_replay, best_proof, best_name

    (candidate_dir / "stable_transcript.txt").write_text(replay["stable_transcript"], encoding="utf-8")

    if final_txt_path.exists():
        (candidate_dir / "final_alpha_output_original.txt").write_bytes(final_txt_path.read_bytes())
        (candidate_dir / "final_alpha_output.txt").write_text(
            replay["stable_transcript"], encoding="utf-8"
        )

    payload = {
        "source_stage": str(source_run_stage),
        "candidate_dir": str(candidate_dir),
        "raw_sha256": raw_sha,
        "raw_bytes_preserved": True,
        "raw_event_count": len(events),
        "selected_replay_path": selected,
        "replay_stats": {
            "line_count": replay["line_count"],
            "revise_count": replay["revise_count"],
            "no_op_count": replay["no_op_count"],
            "append_count": replay["append_count"],
            "orphan_restored_count": replay.get("orphan_restored_count", 0),
        },
        "proof": proof,
        "decisions_path": str(candidate_dir / "replay_decisions.jsonl"),
    }
    with (candidate_dir / "replay_decisions.jsonl").open("w", encoding="utf-8") as fh:
        for row in replay["decisions"]:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (candidate_dir / "REPLAY_SUMMARY.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload
