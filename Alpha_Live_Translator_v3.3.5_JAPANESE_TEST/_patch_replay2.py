# -*- coding: utf-8 -*-
from pathlib import Path

NEW = r'''
def _collapse_adjacent_revision_chains(committed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retire earlier hypothesis when the next line extends or near-duplicates it."""
    from alpha.transcription.stable_revision_decision import _direct_extension, _safe_normalize, _similarity

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
        previous, line_counter = _commit_candidate(
            committed=committed,
            previous=previous,
            text=text,
            event_ids=ids or [str(ev.get("stable_stage_event_id") or f"stable-{line_counter}")],
            start_t=-1.0,
            end_t=-1.0,
            update_requested=previous is not None,
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

'''

p = Path("alpha/utils/stable_assembler_offline_replay_v265.py")
t = p.read_text(encoding="utf-8")
start = t.index("def replay_stable_from_streams(")
end = t.index("\ndef replay_stable_from_raw_events(")
# Drop any prior helper if present immediately before
marker = "\ndef _collapse_adjacent_revision_chains("
if marker in t[:start]:
    start = t.index("def _collapse_adjacent_revision_chains(")
p.write_text(t[:start] + NEW.lstrip("\n") + t[end:], encoding="utf-8")
print("ok", p.stat().st_size)
