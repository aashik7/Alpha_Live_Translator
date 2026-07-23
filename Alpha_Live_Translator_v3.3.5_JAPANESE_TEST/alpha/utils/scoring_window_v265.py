"""Deterministic reference-covered scoring window (V26.5).

Anchors are the first and last trusted reference sentences only.
Requires unique, ordered matches against the physical event stream.
Never searches for a lowest-CER window.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from alpha.utils.general_meaning_normalization import apply_general_meaning_normalization

_MIN_NEEDLE = 8


def _strip_transcript_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
        if m:
            line = m.group(1).strip()
        lines.append(line)
    return lines


def reference_boundary_sentences(reference_text: str) -> tuple[str, str]:
    lines = _strip_transcript_lines(reference_text)
    if not lines:
        raise ValueError("reference_text_empty")
    return lines[0], lines[-1]


def _norm_compact(text: str) -> str:
    n, _ = apply_general_meaning_normalization(text or "")
    return n


def _event_text(ev: dict[str, Any]) -> str:
    return str(
        ev.get("raw_text")
        or ev.get("text")
        or ev.get("transcript")
        or (ev.get("metadata") or {}).get("raw_deepgram_text")
        or ""
    )


def _event_start(ev: dict[str, Any]) -> float:
    md = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
    for key in ("start_time", "start", "audio_start"):
        if key in md and md[key] is not None:
            try:
                return float(md[key])
            except (TypeError, ValueError):
                pass
        if key in ev and ev[key] is not None:
            try:
                return float(ev[key])
            except (TypeError, ValueError):
                pass
    return -1.0


def _event_end(ev: dict[str, Any]) -> float:
    md = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
    for key in ("end_time", "end", "audio_end"):
        if key in md and md[key] is not None:
            try:
                return float(md[key])
            except (TypeError, ValueError):
                pass
        if key in ev and ev[key] is not None:
            try:
                return float(ev[key])
            except (TypeError, ValueError):
                pass
    return _event_start(ev)


def _event_id(ev: dict[str, Any], index: int) -> str:
    return str(ev.get("raw_event_id") or ev.get("event_id") or ev.get("id") or f"idx-{index:06d}")


def load_raw_events(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _candidate_needles(sentence: str) -> list[str]:
    """Longest-first distinctive needles derived only from the reference sentence."""
    full = _norm_compact(sentence)
    if not full:
        return []
    needles: list[str] = [full]
    clauses = [c.strip() for c in re.split(r"[。！？!?]", sentence) if c.strip()]
    parts: list[str] = []
    for clause in clauses:
        parts.append(clause)
        parts.extend([p.strip() for p in re.split(r"[、,]", clause) if p.strip()])
    for part in parts:
        n = _norm_compact(part)
        if n and len(n) >= _MIN_NEEDLE and n not in needles:
            needles.append(n)
    for length in (min(24, len(full)), min(16, len(full)), min(12, len(full)), _MIN_NEEDLE):
        if length < _MIN_NEEDLE:
            continue
        prefix = full[:length]
        suffix = full[-length:]
        if prefix not in needles:
            needles.append(prefix)
        if suffix not in needles:
            needles.append(suffix)
    # Leftmost sliding unique windows (deterministic, sentence-local, not CER search).
    for length in range(len(full), _MIN_NEEDLE - 1, -1):
        for start in range(0, len(full) - length + 1):
            needle = full[start : start + length]
            if needle not in needles:
                needles.append(needle)
            break  # leftmost only per length
    return needles


def _completion_hits(cum_by_idx: list[tuple[int, str]], needle: str) -> list[int]:
    """Event indices where needle occurrence count increases (deterministic)."""
    hits: list[int] = []
    prev_count = 0
    for idx, cum_text in cum_by_idx:
        count = cum_text.count(needle)
        if count > prev_count:
            hits.extend([idx] * (count - prev_count))
            prev_count = count
    return hits


def _longest_unique_needles(sentence: str, stream: str) -> list[str]:
    """Deterministic unique substrings using a small fixed set of windows."""
    full = _norm_compact(sentence)
    if not full or not stream:
        return []
    found: list[str] = []
    lengths = sorted({len(full), min(20, len(full)), min(14, len(full)), _MIN_NEEDLE}, reverse=True)
    for length in lengths:
        if length < _MIN_NEEDLE or length > len(full):
            continue
        mid = max(0, (len(full) - length) // 2)
        for needle in (full[:length], full[-length:], full[mid : mid + length]):
            if stream.count(needle) == 1 and needle not in found:
                found.append(needle)
                if len(found) >= 3:
                    return found
    return found


def _find_unique_completion(
    events: list[dict[str, Any]],
    sentence: str,
    *,
    after_index: int = -1,
    prefer: str = "earliest",
) -> dict[str, Any]:
    """Find a unique ordered anchor event for a reference sentence.

    prefer='earliest' — start boundary: earliest unique needle completion
    prefer='latest' — end boundary: latest unique needle completion
    Never searches for a lowest-CER window.
    """
    start_from = after_index + 1
    cum_by_idx: list[tuple[int, str]] = []
    cum = ""
    for i in range(start_from, len(events)):
        cum += _norm_compact(_event_text(events[i]))
        cum_by_idx.append((i, cum))
    stream = cum_by_idx[-1][1] if cum_by_idx else ""

    needles = _candidate_needles(sentence) + _longest_unique_needles(sentence, stream)
    ordered: list[str] = []
    seen_n: set[str] = set()
    for n in needles:
        if n and n not in seen_n:
            seen_n.add(n)
            ordered.append(n)

    unique_hits: list[tuple[int, str, int]] = []
    ambiguous = 0
    for needle in ordered:
        if len(needle) < _MIN_NEEDLE and needle != _norm_compact(sentence):
            continue
        hits = _completion_hits(cum_by_idx, needle)
        if len(hits) == 1:
            unique_hits.append((hits[0], needle, len(needle)))
        elif len(hits) > 1:
            ambiguous += 1

    if not unique_hits:
        return {
            "status": "ANCHOR_NOT_UNIQUE",
            "event_index": -1,
            "event_id": "",
            "start_time": -1.0,
            "end_time": -1.0,
            "needle_used": "",
            "needle_length": 0,
            "match_count": 0,
            "sentence": sentence,
            "detail": "no_unique_ordered_match",
            "ambiguous_needle_count": ambiguous,
        }

    if prefer == "latest":
        unique_hits.sort(key=lambda row: (row[0], row[2]))
        hit_idx, needle, _nlen = unique_hits[-1]
    else:
        # Earliest event index; prefer longer needle on ties.
        unique_hits.sort(key=lambda row: (row[0], -row[2]))
        hit_idx, needle, _nlen = unique_hits[0]

    return {
        "status": "OK",
        "event_index": hit_idx,
        "event_id": _event_id(events[hit_idx], hit_idx),
        "start_time": _event_start(events[hit_idx]),
        "end_time": _event_end(events[hit_idx]),
        "needle_used": needle,
        "needle_length": len(needle),
        "match_count": 1,
        "sentence": sentence,
        "prefer": prefer,
        "unique_needle_candidates": len(unique_hits),
    }


def resolve_scoring_window(
    *,
    reference_text: str,
    events: list[dict[str, Any]],
    explicit_markers: Optional[dict[str, Any]] = None,
    audio_duration_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """Resolve one physical scoring window for Raw/Stable/Final."""
    first_sent, last_sent = reference_boundary_sentences(reference_text)

    if explicit_markers and explicit_markers.get("start_event_id") and explicit_markers.get("end_event_id"):
        start_id = str(explicit_markers["start_event_id"])
        end_id = str(explicit_markers["end_event_id"])
        start_idx = next((i for i, e in enumerate(events) if _event_id(e, i) == start_id), -1)
        end_idx = next((i for i, e in enumerate(events) if _event_id(e, i) == end_id), -1)
        if start_idx < 0 or end_idx < 0 or end_idx < start_idx:
            return {
                "status": "EXPLICIT_MARKERS_INVALID",
                "window_resolved": False,
                "detail": f"start_idx={start_idx} end_idx={end_idx}",
            }
        start_anchor = {
            "status": "OK",
            "event_index": start_idx,
            "event_id": start_id,
            "start_time": float(explicit_markers.get("start_time") or _event_start(events[start_idx])),
            "end_time": _event_end(events[start_idx]),
            "needle_used": "explicit_marker",
            "source": "explicit_scoring_markers",
            "sentence": first_sent,
        }
        end_anchor = {
            "status": "OK",
            "event_index": end_idx,
            "event_id": end_id,
            "start_time": _event_start(events[end_idx]),
            "end_time": float(explicit_markers.get("end_time") or _event_end(events[end_idx])),
            "needle_used": "explicit_marker",
            "source": "explicit_scoring_markers",
            "sentence": last_sent,
        }
    else:
        start_anchor = _find_unique_completion(events, first_sent, after_index=-1, prefer="earliest")
        if start_anchor.get("status") != "OK":
            return {
                "status": "START_ANCHOR_FAILED",
                "window_resolved": False,
                "start_anchor": start_anchor,
                "end_anchor": {},
                "first_reference_sentence": first_sent,
                "last_reference_sentence": last_sent,
            }
        end_anchor = _find_unique_completion(
            events, last_sent, after_index=int(start_anchor["event_index"]), prefer="latest"
        )
        if end_anchor.get("status") != "OK":
            return {
                "status": "END_ANCHOR_FAILED",
                "window_resolved": False,
                "start_anchor": start_anchor,
                "end_anchor": end_anchor,
                "first_reference_sentence": first_sent,
                "last_reference_sentence": last_sent,
            }
        start_anchor["source"] = "first_reference_sentence"
        end_anchor["source"] = "last_reference_sentence"

    start_idx = int(start_anchor["event_index"])
    end_idx = int(end_anchor["event_index"])
    start_t = float(start_anchor["start_time"])
    end_t = float(end_anchor["end_time"])
    if end_t < start_t:
        return {
            "status": "WINDOW_ORDER_INVALID",
            "window_resolved": False,
            "start_anchor": start_anchor,
            "end_anchor": end_anchor,
        }

    first_event_t = _event_start(events[0]) if events else 0.0
    last_event_t = _event_end(events[-1]) if events else end_t
    audio_dur = float(audio_duration_seconds) if audio_duration_seconds is not None else last_event_t
    if audio_dur <= 0:
        audio_dur = last_event_t

    prefix_excluded = max(0.0, start_t - max(0.0, first_event_t))
    # Prefer wall-clock audio duration for suffix when provided.
    suffix_excluded = max(0.0, audio_dur - end_t)
    if suffix_excluded <= 0 and last_event_t > end_t:
        suffix_excluded = last_event_t - end_t

    window_events = events[start_idx : end_idx + 1]
    return {
        "status": "OK",
        "window_resolved": True,
        "method": "first_last_reference_sentence_unique_ordered",
        "lowest_cer_window_search": False,
        "first_reference_sentence": first_sent,
        "last_reference_sentence": last_sent,
        "start_anchor": start_anchor,
        "end_anchor": end_anchor,
        "start_event_id": start_anchor["event_id"],
        "end_event_id": end_anchor["event_id"],
        "start_event_index": start_idx,
        "end_event_index": end_idx,
        "start_time_seconds": start_t,
        "end_time_seconds": end_t,
        "window_duration_seconds": max(0.0, end_t - start_t),
        "audio_duration_seconds": audio_dur,
        "excluded_prefix_seconds": prefix_excluded,
        "excluded_suffix_seconds": suffix_excluded,
        "excluded_prefix_reason": "spoken_material_before_first_reference_sentence",
        "excluded_suffix_reason": "microphone_or_material_after_last_reference_sentence",
        "event_count_in_window": len(window_events),
        "event_count_total": len(events),
    }


def slice_events_to_text(events: list[dict[str, Any]], window: dict[str, Any]) -> str:
    if not window.get("window_resolved"):
        return ""
    start_idx = int(window["start_event_index"])
    end_idx = int(window["end_event_index"])
    lines: list[str] = []
    for ev in events[start_idx : end_idx + 1]:
        t = _event_text(ev).strip()
        if t:
            lines.append(t)
    return "\n".join(lines) + ("\n" if lines else "")


def slice_transcript_by_time(
    transcript_text: str,
    events: list[dict[str, Any]],
    window: dict[str, Any],
) -> str:
    """Prefer event-stream slice; fall back to full text only if window unresolved."""
    if window.get("window_resolved") and events:
        sliced = slice_events_to_text(events, window)
        if sliced.strip():
            return sliced
    return transcript_text


def filter_truth_entities_to_reference_window(
    *,
    truth: dict[str, Any],
    reference_text: str,
    category_keys: list[str],
) -> dict[str, Any]:
    """Score only entities physically present in the trusted reference text.

    Out-of-window truth entries are reported separately and never counted as misses.
    """
    ref_norm = _norm_compact(reference_text)
    in_window: dict[str, list[str]] = {}
    out_of_window: dict[str, list[str]] = {}
    for key in category_keys:
        terms = list(truth.get(key) or [])
        kept: list[str] = []
        skipped: list[str] = []
        for term in terms:
            if not str(term).strip():
                continue
            # Placeholder directives in truth are not entities.
            if str(term).startswith("extract_from_reference"):
                continue
            t_norm = _norm_compact(str(term))
            if t_norm and t_norm in ref_norm:
                kept.append(str(term))
            else:
                skipped.append(str(term))
        in_window[key] = kept
        out_of_window[key] = skipped
    return {
        "in_window_truth": in_window,
        "out_of_window_truth": out_of_window,
        "out_of_window_total": sum(len(v) for v in out_of_window.values()),
    }


def load_explicit_scoring_markers(stage_dir: Path) -> Optional[dict[str, Any]]:
    path = Path(stage_dir) / "scoring_window_markers.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_scoring_window_record(stage_dir: Path, window: dict[str, Any]) -> Path:
    path = Path(stage_dir) / "scoring_window.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(window, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
