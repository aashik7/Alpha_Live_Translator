"""Thread-safe transcript snapshot for background autosave — no Tkinter access."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from alpha.utils.lock_instrumentation import InstrumentedRLock

_lock = InstrumentedRLock("transcript_snapshot_store")
_segments: list[dict[str, Any]] = []
_segment_id_counter = 0
_started = False


def ensure_snapshot_store_started() -> None:
    global _started
    if _started:
        return
    _started = True
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("TRANSCRIPT_SNAPSHOT_STORE_STARTED")
    except Exception:
        pass


def reset_transcript_snapshot_store() -> None:
    global _segment_id_counter
    with _lock:
        _segments.clear()
        _segment_id_counter = 0


def append_transcript_snapshot(
    *,
    speaker: Any,
    stable_text: str,
    commit_reason: str = "",
    timestamp: Optional[float] = None,
) -> int:
    """Append one immutable stable segment record (safe from any thread)."""
    global _segment_id_counter
    ensure_snapshot_store_started()
    text = (stable_text or "").strip()
    if not text:
        return -1
    ts = float(timestamp if timestamp is not None else time.time())
    with _lock:
        _segment_id_counter += 1
        seg_id = _segment_id_counter
        _segments.append(
            {
                "segment_id": seg_id,
                "timestamp": ts,
                "speaker": speaker,
                "stable_text": text,
                "commit_reason": commit_reason or "",
                "status": "active",
                "revision_number": 1,
            }
        )
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "TRANSCRIPT_SNAPSHOT_SEGMENT_APPENDED",
            segment_id=seg_id,
            speaker=speaker,
            text_preview=text[:80],
        )
    except Exception:
        pass
    return seg_id


def revise_last_transcript_snapshot(
    *,
    stable_text: str,
    commit_reason: str = "",
    speaker: Any = None,
) -> int:
    """Revise the last active snapshot segment instead of appending a duplicate."""
    global _segment_id_counter
    ensure_snapshot_store_started()
    text = (stable_text or "").strip()
    if not text:
        return -1
    ts = time.time()
    with _lock:
        if not _segments:
            return append_transcript_snapshot(
                speaker=speaker,
                stable_text=text,
                commit_reason=commit_reason,
                timestamp=ts,
            )
        last = _segments[-1]
        if last.get("status") == "suppressed":
            _segment_id_counter += 1
            seg_id = _segment_id_counter
            _segments.append(
                {
                    "segment_id": seg_id,
                    "timestamp": ts,
                    "speaker": speaker if speaker is not None else last.get("speaker"),
                    "stable_text": text,
                    "commit_reason": commit_reason or "",
                    "status": "active",
                    "revision_number": 1,
                }
            )
        else:
            old_text = last.get("stable_text", "")
            last["status"] = "revised"
            _segment_id_counter += 1
            seg_id = _segment_id_counter
            rev_num = int(last.get("revision_number", 1)) + 1
            _segments.append(
                {
                    "segment_id": seg_id,
                    "timestamp": ts,
                    "speaker": speaker if speaker is not None else last.get("speaker"),
                    "stable_text": text,
                    "commit_reason": commit_reason or "",
                    "status": "active",
                    "revision_number": rev_num,
                    "revised_from_segment_id": last.get("segment_id"),
                }
            )
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "TRANSCRIPT_SNAPSHOT_SEGMENT_REVISED",
                    segment_id=seg_id,
                    revised_from=last.get("segment_id"),
                    old_preview=str(old_text)[:60],
                    new_preview=text[:60],
                )
            except Exception:
                pass
    return seg_id


def get_snapshot_copy() -> list[dict[str, Any]]:
    with _lock:
        return [dict(seg) for seg in _segments]


def snapshot_segment_count() -> int:
    with _lock:
        return len(_segments)


def format_alpha_output_text(*, active_only: bool = True) -> str:
    lines: list[str] = []
    for seg in get_snapshot_copy():
        if active_only and seg.get("status") not in (None, "active"):
            continue
        text = (seg.get("stable_text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker")
        prefix = f"[Speaker {speaker}] " if speaker is not None else ""
        lines.append(f"{prefix}{text}")
    return "\n".join(lines)
