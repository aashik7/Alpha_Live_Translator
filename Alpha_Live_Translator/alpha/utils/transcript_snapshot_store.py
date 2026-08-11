"""Thread-safe transcript snapshot for background autosave — no Tkinter access."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from alpha.transcription.speaker_boundary_guard import speakers_confirmed_same
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


def _speaker_confirmed_same(active_speaker: Any, candidate_speaker: Any) -> bool:
    """Fail-closed speaker match for the snapshot store (v4 item 24).

    Normalises the unknown forms this codebase uses interchangeably (`None`,
    `0`, `""`) to None before delegating, because `speakers_confirmed_same`
    is fail-closed on None but would still read `0 == 0` as a confirmed
    match -- the same trap item 22 hit in `utterance_lifecycle`.
    """
    def _known(value: Any) -> Any:
        if value is None:
            return None
        try:
            speaker = int(value)
        except (TypeError, ValueError):
            return None
        return speaker or None

    return speakers_confirmed_same(_known(active_speaker), _known(candidate_speaker))


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
        # fixes CLIENT_DELIVERY_SPRINT_v5.md problem C (v4 item 24). This
        # function accepted `speaker` but never compared it -- the argument was
        # only a fallback value when the caller passed none -- so it revised
        # whatever the literal last row was, regardless of who spoke it. That
        # is strictly weaker than `transcript_store`'s equivalent, which has
        # filtered by speaker since TASK_2E_FINDINGS.md item 3.
        #
        # Same primitive and same fail-closed rule as
        # `TranscriptStore.update_last_segment_if_active`: an unknown speaker on
        # either side is never a confirmed match. A speaker change is a hard
        # boundary, so the correct response is to start a new segment rather
        # than overwrite another speaker's line.
        if speaker is not None and not _speaker_confirmed_same(
            last.get("speaker"), speaker
        ):
            return append_transcript_snapshot(
                speaker=speaker,
                stable_text=text,
                commit_reason=commit_reason,
                timestamp=ts,
            )
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
        from alpha.utils.ui_speaker_label import format_ui_speaker_line

        lines.append(format_ui_speaker_line(text))
    return "\n".join(lines)
