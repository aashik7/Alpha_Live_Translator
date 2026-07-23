# -*- coding: utf-8 -*-
from pathlib import Path

p = Path("alpha/utils/stable_assembler_offline_replay_v265.py")
t = p.read_text(encoding="utf-8")
start = t.index("def replay_stable_from_streams(")
end = t.index("\ndef replay_stable_from_raw_events(")
# Insert collapse helper before replay_stable_from_streams if missing
helper = '''
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


'''
if "_collapse_adjacent_revision_chains" not in t:
    # place helper just before replay_stable_from_streams
    t = t[:start] + helper + t[start:]
    start = t.index("def replay_stable_from_streams(")
    end = t.index("\ndef replay_stable_from_raw_events(")

new_fn = Path("_replay_fn_body.py").read_text(encoding="utf-8") if False else None
