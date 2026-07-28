# -*- coding: utf-8 -*-
"""Session-scoped utterance revision lifecycle for production Deepgram finals.

English / non-Japanese path: buffer incomplete finals (is_final=true,
speech_final=false), replace one active UI record, and commit once on
speech_final / UtteranceEnd / bounded inactivity timeout.

Japanese finals continue through japanese_final_chunk_stabilizer and are
not owned by this module.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# States
IDLE = "IDLE"
ACTIVE_INTERIM = "ACTIVE_INTERIM"
ACTIVE_FINAL_CHUNK = "ACTIVE_FINAL_CHUNK"
READY_TO_COMMIT = "READY_TO_COMMIT"
COMMITTED = "COMMITTED"
SUPERSEDED = "SUPERSEDED"
CANCELLED = "CANCELLED"

# Decisions
CREATE_ACTIVE = "CREATE_ACTIVE"
REPLACE_ACTIVE = "REPLACE_ACTIVE"
EXTEND_ACTIVE = "EXTEND_ACTIVE"
HOLD_FINAL_CHUNK = "HOLD_FINAL_CHUNK"
COMMIT_ACTIVE = "COMMIT_ACTIVE"
CREATE_NEW_UTTERANCE = "CREATE_NEW_UTTERANCE"
SUPERSEDE_PREVIOUS = "SUPERSEDE_PREVIOUS"
IGNORE_DUPLICATE = "IGNORE_DUPLICATE"
CANCEL_ACTIVE = "CANCEL_ACTIVE"

# Timing proximity for same-utterance merge (seconds). Short — not a minute wait.
_TIMING_GAP_MAX_S = 2.5
_TIMING_START_MATCH_S = 0.35
_TIMING_OVERLAP_MIN_S = 0.05

# Fallback inactivity commit (ms). Uses existing meeting-buffer scale; does not
# change Deepgram endpointing configuration.
DEFAULT_COMMIT_FALLBACK_MS = 2000


def _as_float(value: Any, default: float = -1.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _norm_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _merge_lexical(previous: str, current: str) -> str:
    """Merge two lexical chunks of the same utterance without hardcoding phrases."""
    prev = (previous or "").strip()
    curr = (current or "").strip()
    if not prev:
        return curr
    if not curr:
        return prev
    prev_n = _norm_text(prev)
    curr_n = _norm_text(curr)
    if prev_n == curr_n:
        return curr if len(curr) >= len(prev) else prev
    if prev_n and curr_n.startswith(prev_n):
        return curr
    if curr_n and prev_n.startswith(curr_n):
        return prev
    if prev_n and prev_n in curr_n:
        return curr
    if curr_n and curr_n in prev_n:
        return prev
    # Adjacent finalised chunks of one utterance (non-overlapping lexical spans).
    if prev.endswith((",", ";", ":")):
        return f"{prev} {curr}"
    if prev[-1:] in ".!?":
        return f"{prev} {curr}"
    return f"{prev}, {curr}"


def _channels_compatible(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return True
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _timing_compatible(
    prev_start: float,
    prev_end: float,
    cand_start: float,
    cand_end: float,
) -> bool:
    if cand_start < 0 and prev_start < 0:
        return True  # no timing → rely on other gates
    if prev_start >= 0 and cand_start >= 0 and abs(prev_start - cand_start) <= _TIMING_START_MATCH_S:
        return True
    if prev_start >= 0 and prev_end > prev_start and cand_start >= 0 and cand_end > cand_start:
        latest_start = max(prev_start, cand_start)
        earliest_end = min(prev_end, cand_end)
        if earliest_end - latest_start >= _TIMING_OVERLAP_MIN_S:
            return True
    if prev_end >= 0 and cand_start >= 0:
        gap = cand_start - prev_end
        if -0.25 <= gap <= _TIMING_GAP_MAX_S:
            return True
    if prev_start >= 0 and cand_start >= 0:
        gap = abs(cand_start - prev_start)
        if gap <= _TIMING_GAP_MAX_S:
            return True
    return False


def _text_related(previous: str, current: str) -> bool:
    prev_n = _norm_text(previous)
    curr_n = _norm_text(current)
    if not prev_n or not curr_n:
        return False
    if prev_n == curr_n:
        return True
    if prev_n in curr_n or curr_n in prev_n:
        return True
    if curr_n.startswith(prev_n) or prev_n.startswith(curr_n):
        return True
    # High prefix overlap for near-revisions (e.g. Terry → Tariqul).
    prefix = min(len(prev_n), len(curr_n), max(8, int(0.5 * min(len(prev_n), len(curr_n)))))
    if prefix >= 8 and prev_n[:prefix] == curr_n[:prefix]:
        return True
    return False


@dataclass
class ActiveUtterance:
    utterance_id: str
    session_id: str
    state: str = IDLE
    speaker: int = 1
    channel: Any = None
    text: str = ""
    version: int = 0
    start_time: float = -1.0
    end_time: float = -1.0
    deepgram_request_id: str = ""
    lineage_ids: list[str] = field(default_factory=list)
    committed_record_id: str = ""
    committed: bool = False
    last_event_mono: float = field(default_factory=time.monotonic)
    created_mono: float = field(default_factory=time.monotonic)


@dataclass
class LifecycleDecision:
    decision: str
    reason: str
    utterance_id: str = ""
    text: str = ""
    previous_text: str = ""
    should_update_interim: bool = False
    should_commit: bool = False
    should_supersede_committed: bool = False
    superseded_record_id: str = ""
    version: int = 0
    session_id: str = ""
    event_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class UtteranceLifecycleOwner:
    """Owns one active utterance per session for the English/generic final path."""

    def __init__(
        self,
        *,
        host: Any = None,
        commit_fallback_ms: int = DEFAULT_COMMIT_FALLBACK_MS,
        event_log_path: Optional[Path] = None,
        on_commit: Optional[Callable[[LifecycleDecision], None]] = None,
        on_interim_update: Optional[Callable[[LifecycleDecision], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._host = host
        self._lock = threading.RLock()
        self._commit_fallback_ms = max(250, int(commit_fallback_ms))
        self._event_log_path = event_log_path
        self._on_commit = on_commit
        self._on_interim_update = on_interim_update
        self._clock = clock or time.monotonic
        self._session_id = ""
        self._seq = 0
        self._active: Optional[ActiveUtterance] = None
        self._last_committed: Optional[ActiveUtterance] = None
        self._timeout_token = 0
        self._timeout_after_id: Any = None
        self._events: list[dict[str, Any]] = []
        self._committed_utterance_ids: set[str] = set()
        self._stats = {
            "canonical_commits": 0,
            "translation_jobs_hint": 0,
            "hold_final_chunks": 0,
            "replace_active": 0,
            "extend_active": 0,
            "utterance_end_dedup": 0,
            "timeout_commits": 0,
            "supersessions": 0,
        }

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    def reset_for_session(self, session_id: str) -> None:
        with self._lock:
            self._cancel_timeout_locked()
            self._session_id = str(session_id or "")
            self._seq = 0
            self._active = None
            self._last_committed = None
            self._committed_utterance_ids.clear()
            self._timeout_token = 0
            self._stats = {k: 0 for k in self._stats}
            self._log_event(
                {
                    "decision": CANCEL_ACTIVE,
                    "decision_reason": "session_reset",
                    "session_id": self._session_id,
                }
            )

    def bind_host(self, host: Any) -> None:
        self._host = host

    def set_event_log_path(self, path: Optional[Path]) -> None:
        self._event_log_path = path

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def active(self) -> Optional[ActiveUtterance]:
        return self._active

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._stats)

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def on_interim(
        self,
        *,
        text: str,
        speaker: int = 1,
        channel: Any = None,
        start: Any = None,
        end: Any = None,
        event_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> LifecycleDecision:
        return self._ingest(
            text=text,
            speaker=speaker,
            channel=channel,
            start=start,
            end=end,
            is_final=False,
            speech_final=False,
            event_id=event_id or f"interim-{time.time_ns()}",
            metadata=metadata or {},
            source="interim",
        )

    def on_final_chunk(
        self,
        *,
        text: str,
        speaker: int = 1,
        channel: Any = None,
        start: Any = None,
        end: Any = None,
        is_final: bool = True,
        speech_final: Any = None,
        event_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
        deepgram_request_id: str = "",
    ) -> LifecycleDecision:
        return self._ingest(
            text=text,
            speaker=speaker,
            channel=channel,
            start=start,
            end=end,
            is_final=bool(is_final),
            speech_final=speech_final,
            event_id=event_id or f"final-{time.time_ns()}",
            metadata=metadata or {},
            source="final",
            deepgram_request_id=deepgram_request_id,
        )

    def on_utterance_end(
        self,
        *,
        channel: Any = None,
        event_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> LifecycleDecision:
        with self._lock:
            active = self._active
            if active is None or not (active.text or "").strip():
                d = LifecycleDecision(
                    decision=IGNORE_DUPLICATE,
                    reason="utterance_end_no_active",
                    session_id=self._session_id,
                    event_id=event_id or f"ue-{time.time_ns()}",
                )
                self._record_decision(d, is_final=True, speech_final=None, channel=channel)
                return d
            if active.committed or active.utterance_id in self._committed_utterance_ids:
                self._stats["utterance_end_dedup"] += 1
                d = LifecycleDecision(
                    decision=IGNORE_DUPLICATE,
                    reason="utterance_end_already_committed",
                    utterance_id=active.utterance_id,
                    text=active.text,
                    session_id=self._session_id,
                    event_id=event_id or f"ue-{time.time_ns()}",
                    version=active.version,
                )
                self._record_decision(d, is_final=True, speech_final=True, channel=channel)
                return d
            return self._commit_locked(
                reason="utterance_end",
                event_id=event_id or f"ue-{time.time_ns()}",
                metadata=dict(metadata or {}),
                decision_name=COMMIT_ACTIVE,
            )

    def on_timeout(self, *, token: int) -> Optional[LifecycleDecision]:
        with self._lock:
            if token != self._timeout_token:
                return None
            active = self._active
            if active is None or not (active.text or "").strip():
                return None
            if active.committed or active.utterance_id in self._committed_utterance_ids:
                return None
            if active.state not in (ACTIVE_FINAL_CHUNK, ACTIVE_INTERIM, READY_TO_COMMIT):
                return None
            self._stats["timeout_commits"] += 1
            return self._commit_locked(
                reason="inactivity_timeout_fallback",
                event_id=f"timeout-{time.time_ns()}",
                metadata={"timeout_ms": self._commit_fallback_ms},
                decision_name=COMMIT_ACTIVE,
            )

    def force_cancel_active(self, reason: str = "cancelled") -> LifecycleDecision:
        with self._lock:
            self._cancel_timeout_locked()
            active = self._active
            text = active.text if active else ""
            uid = active.utterance_id if active else ""
            if active:
                active.state = CANCELLED
            self._active = None
            d = LifecycleDecision(
                decision=CANCEL_ACTIVE,
                reason=reason,
                utterance_id=uid,
                text=text,
                session_id=self._session_id,
            )
            self._record_decision(d, is_final=False, speech_final=None, channel=None)
            return d

    # ------------------------------------------------------------------
    # Core ingest
    # ------------------------------------------------------------------
    def _ingest(
        self,
        *,
        text: str,
        speaker: int,
        channel: Any,
        start: Any,
        end: Any,
        is_final: bool,
        speech_final: Any,
        event_id: str,
        metadata: dict[str, Any],
        source: str,
        deepgram_request_id: str = "",
    ) -> LifecycleDecision:
        lexical = (text or "").strip()
        if not lexical:
            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="empty_text",
                session_id=self._session_id,
                event_id=event_id,
            )
            self._record_decision(
                d, is_final=is_final, speech_final=speech_final, channel=channel
            )
            return d

        cand_start = _as_float(start, _as_float(metadata.get("start_time"), -1.0))
        cand_end = _as_float(end, _as_float(metadata.get("end_time"), -1.0))
        sf = speech_final
        if isinstance(sf, str):
            sf = sf.strip().lower() in ("1", "true", "yes")
        elif sf is not None:
            sf = bool(sf)

        with self._lock:
            session_id = self._session_id or str(
                getattr(self._host, "_live_session_id", "") or ""
            )
            if session_id and self._session_id and session_id != self._session_id:
                d = LifecycleDecision(
                    decision=IGNORE_DUPLICATE,
                    reason="session_mismatch",
                    session_id=self._session_id,
                    event_id=event_id,
                    text=lexical,
                )
                self._record_decision(
                    d, is_final=is_final, speech_final=sf, channel=channel
                )
                return d
            if not self._session_id:
                self._session_id = session_id or f"sess-local-{uuid.uuid4().hex[:8]}"

            active = self._active
            previous_text = active.text if active else ""

            # Case A — interim only
            if not is_final:
                return self._apply_active_update_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    state=ACTIVE_INTERIM,
                    hold=False,
                    speech_final=False,
                    source=source,
                )

            # Duplicate of already-committed active
            if (
                active
                and active.committed
                and _norm_text(active.text) == _norm_text(lexical)
            ):
                d = LifecycleDecision(
                    decision=IGNORE_DUPLICATE,
                    reason="duplicate_of_committed",
                    utterance_id=active.utterance_id,
                    text=lexical,
                    previous_text=previous_text,
                    session_id=self._session_id,
                    event_id=event_id,
                    version=active.version,
                )
                self._record_decision(d, is_final=True, speech_final=sf, channel=channel)
                return d

            same_active = self._compatible_with_active_locked(
                speaker=speaker,
                channel=channel,
                cand_start=cand_start,
                cand_end=cand_end,
                lexical=lexical,
            )

            # Authoritative correction of last committed (same timing/lineage).
            # Must run even when active is None after a prior COMMIT.
            if (
                self._last_committed is not None
                and (active is None or not same_active)
                and self._is_correction_of_committed_locked(
                    lexical=lexical,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    metadata=metadata,
                )
            ):
                return self._supersede_committed_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    speech_final=sf if sf is not None else True,
                )

            # Case B — final chunk, utterance incomplete
            if is_final and sf is False:
                d = self._apply_active_update_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    state=ACTIVE_FINAL_CHUNK,
                    hold=True,
                    speech_final=False,
                    source=source,
                    force_new=not same_active and active is not None and active.committed,
                )
                self._stats["hold_final_chunks"] += 1
                self._arm_timeout_locked()
                return d

            # Case C — speech_final true (or unknown final treated carefully)
            if is_final and (sf is True or sf is None):
                # If sf is None on English finals, prefer hold when active incomplete
                # chunk exists and candidate is compatible; otherwise commit.
                if sf is None and active and active.state == ACTIVE_FINAL_CHUNK and same_active:
                    d = self._apply_active_update_locked(
                        lexical=lexical,
                        speaker=speaker,
                        channel=channel,
                        cand_start=cand_start,
                        cand_end=cand_end,
                        event_id=event_id,
                        metadata=metadata,
                        deepgram_request_id=deepgram_request_id,
                        state=ACTIVE_FINAL_CHUNK,
                        hold=True,
                        speech_final=None,
                        source=source,
                    )
                    self._arm_timeout_locked()
                    return d

                if same_active or active is None or not (active.text or "").strip():
                    self._apply_active_update_locked(
                        lexical=lexical,
                        speaker=speaker,
                        channel=channel,
                        cand_start=cand_start,
                        cand_end=cand_end,
                        event_id=event_id,
                        metadata=metadata,
                        deepgram_request_id=deepgram_request_id,
                        state=READY_TO_COMMIT,
                        hold=False,
                        speech_final=True,
                        source=source,
                        force_new=active is None,
                    )
                    return self._commit_locked(
                        reason="speech_final",
                        event_id=event_id,
                        metadata=metadata,
                        decision_name=COMMIT_ACTIVE,
                    )

                # Incompatible with active → commit previous if held, then new
                if active and not active.committed and (active.text or "").strip():
                    self._commit_locked(
                        reason="boundary_before_new_utterance",
                        event_id=f"{event_id}-flush",
                        metadata=metadata,
                        decision_name=COMMIT_ACTIVE,
                    )
                self._apply_active_update_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    state=READY_TO_COMMIT,
                    hold=False,
                    speech_final=True,
                    source=source,
                    force_new=True,
                )
                return self._commit_locked(
                    reason="speech_final_new_utterance",
                    event_id=event_id,
                    metadata=metadata,
                    decision_name=CREATE_NEW_UTTERANCE,
                )

            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="unhandled_flags",
                text=lexical,
                session_id=self._session_id,
                event_id=event_id,
            )
            self._record_decision(d, is_final=is_final, speech_final=sf, channel=channel)
            return d

    def _compatible_with_active_locked(
        self,
        *,
        speaker: int,
        channel: Any,
        cand_start: float,
        cand_end: float,
        lexical: str,
    ) -> bool:
        active = self._active
        if active is None or not (active.text or "").strip():
            return True
        if active.committed:
            return False
        if active.session_id and self._session_id and active.session_id != self._session_id:
            return False
        if int(active.speaker or 1) != int(speaker or 1):
            return False
        if not _channels_compatible(active.channel, channel):
            return False
        timing_ok = _timing_compatible(
            active.start_time, active.end_time, cand_start, cand_end
        )
        text_ok = _text_related(active.text, lexical)
        # Prefer lineage/timing; text overlap only as bounded fallback.
        if timing_ok:
            return True
        if text_ok and active.state in (ACTIVE_INTERIM, ACTIVE_FINAL_CHUNK, READY_TO_COMMIT):
            # Fallback requires non-terminal + session already matched + channel ok.
            return True
        # Adjacent non-overlapping chunks while holding final: allow extend when
        # previous was ACTIVE_FINAL_CHUNK and gap is small / unknown.
        if active.state == ACTIVE_FINAL_CHUNK:
            if cand_start < 0 or active.end_time < 0:
                return True
            if _timing_compatible(
                active.start_time, active.end_time, cand_start, cand_end
            ):
                return True
        return False

    def _is_correction_of_committed_locked(
        self,
        *,
        lexical: str,
        channel: Any,
        cand_start: float,
        cand_end: float,
        metadata: dict[str, Any],
    ) -> bool:
        prev = self._last_committed
        if prev is None or not prev.committed:
            return False
        if not _channels_compatible(prev.channel, channel):
            return False
        # Explicit lineage / revision target
        target = str(
            metadata.get("revision_target_id")
            or metadata.get("revision_target_line_id")
            or metadata.get("canonical_utterance_id")
            or ""
        )
        if target and target in (prev.utterance_id, prev.committed_record_id):
            return _text_related(prev.text, lexical) or True
        if not _timing_compatible(prev.start_time, prev.end_time, cand_start, cand_end):
            return False
        # Same start window + related text → authoritative same-utterance correction
        return _text_related(prev.text, lexical)

    def _apply_active_update_locked(
        self,
        *,
        lexical: str,
        speaker: int,
        channel: Any,
        cand_start: float,
        cand_end: float,
        event_id: str,
        metadata: dict[str, Any],
        deepgram_request_id: str,
        state: str,
        hold: bool,
        speech_final: Any,
        source: str,
        force_new: bool = False,
    ) -> LifecycleDecision:
        active = self._active
        previous_text = active.text if active else ""

        if force_new or active is None or active.committed:
            self._seq += 1
            uid = f"U-{self._seq}"
            active = ActiveUtterance(
                utterance_id=uid,
                session_id=self._session_id,
                state=state,
                speaker=int(speaker or 1),
                channel=channel,
                text=lexical,
                version=1,
                start_time=cand_start,
                end_time=cand_end,
                deepgram_request_id=str(deepgram_request_id or ""),
                lineage_ids=[event_id] if event_id else [],
            )
            self._active = active
            decision = CREATE_ACTIVE if not force_new else CREATE_NEW_UTTERANCE
            reason = "create_active_utterance" if not force_new else "create_new_utterance"
            if hold:
                decision = HOLD_FINAL_CHUNK
                reason = "hold_incomplete_final_chunk"
        else:
            prev_n = _norm_text(previous_text)
            curr_n = _norm_text(lexical)
            if prev_n == curr_n:
                decision = IGNORE_DUPLICATE
                reason = "exact_duplicate_active"
                active.last_event_mono = self._clock()
                d = LifecycleDecision(
                    decision=decision,
                    reason=reason,
                    utterance_id=active.utterance_id,
                    text=active.text,
                    previous_text=previous_text,
                    session_id=self._session_id,
                    event_id=event_id,
                    version=active.version,
                    should_update_interim=False,
                )
                self._record_decision(
                    d, is_final=source == "final", speech_final=speech_final, channel=channel
                )
                return d

            merged = _merge_lexical(previous_text, lexical)
            if _norm_text(merged) != curr_n and _norm_text(merged) != prev_n:
                decision = EXTEND_ACTIVE
                reason = "extend_active_adjacent_chunk"
                self._stats["extend_active"] += 1
            elif curr_n.startswith(prev_n) or prev_n in curr_n:
                decision = REPLACE_ACTIVE
                reason = "replace_active_cumulative_revision"
                self._stats["replace_active"] += 1
            else:
                decision = REPLACE_ACTIVE
                reason = "replace_active_same_utterance_revision"
                self._stats["replace_active"] += 1
            if hold:
                # Preserve HOLD as the logged decision while still replace/extend.
                decision = HOLD_FINAL_CHUNK if decision != EXTEND_ACTIVE else EXTEND_ACTIVE
                if decision == HOLD_FINAL_CHUNK:
                    reason = "hold_incomplete_final_chunk"
                else:
                    reason = "extend_active_held_final_chunk"

            active.text = merged
            active.version += 1
            active.state = state
            active.channel = channel if channel is not None else active.channel
            active.speaker = int(speaker or active.speaker or 1)
            if cand_start >= 0:
                if active.start_time < 0 or cand_start < active.start_time:
                    active.start_time = cand_start
            if cand_end >= 0:
                active.end_time = max(active.end_time, cand_end)
            if event_id:
                active.lineage_ids.append(event_id)
            if deepgram_request_id:
                active.deepgram_request_id = str(deepgram_request_id)
            active.last_event_mono = self._clock()

        d = LifecycleDecision(
            decision=decision,
            reason=reason,
            utterance_id=active.utterance_id,
            text=active.text,
            previous_text=previous_text,
            should_update_interim=True,
            should_commit=False,
            version=active.version,
            session_id=self._session_id,
            event_id=event_id,
            metadata={
                "source": source,
                "start_time": active.start_time,
                "end_time": active.end_time,
                "channel": active.channel,
                "speaker": active.speaker,
                "canonical_utterance_id": active.utterance_id,
                "source_version": active.version,
                "lineage_ids": list(active.lineage_ids),
                **{k: v for k, v in metadata.items() if k not in ("text",)},
            },
        )
        self._record_decision(
            d, is_final=source == "final", speech_final=speech_final, channel=channel
        )
        self._emit_interim(d)
        return d

    def _commit_locked(
        self,
        *,
        reason: str,
        event_id: str,
        metadata: dict[str, Any],
        decision_name: str,
    ) -> LifecycleDecision:
        active = self._active
        if active is None or not (active.text or "").strip():
            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="commit_without_active",
                session_id=self._session_id,
                event_id=event_id,
            )
            self._record_decision(d, is_final=True, speech_final=True, channel=None)
            return d
        if active.committed or active.utterance_id in self._committed_utterance_ids:
            self._stats["utterance_end_dedup"] += 1
            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="already_committed",
                utterance_id=active.utterance_id,
                text=active.text,
                session_id=self._session_id,
                event_id=event_id,
                version=active.version,
            )
            self._record_decision(d, is_final=True, speech_final=True, channel=active.channel)
            return d

        self._cancel_timeout_locked()
        active.state = COMMITTED
        active.committed = True
        self._committed_utterance_ids.add(active.utterance_id)
        self._stats["canonical_commits"] += 1
        self._stats["translation_jobs_hint"] += 1
        self._last_committed = active
        self._active = None

        d = LifecycleDecision(
            decision=decision_name,
            reason=reason,
            utterance_id=active.utterance_id,
            text=active.text,
            previous_text="",
            should_update_interim=False,
            should_commit=True,
            version=active.version,
            session_id=self._session_id,
            event_id=event_id,
            metadata={
                "speech_final": True,
                "start_time": active.start_time,
                "end_time": active.end_time,
                "channel": active.channel,
                "speaker": active.speaker,
                "canonical_utterance_id": active.utterance_id,
                "source_version": active.version,
                "source_raw_event_ids": list(active.lineage_ids),
                "deepgram_request_id": active.deepgram_request_id,
                "lifecycle_commit_reason": reason,
                **{k: v for k, v in metadata.items() if k not in ("text",)},
            },
        )
        self._record_decision(
            d, is_final=True, speech_final=True, channel=active.channel, commit=True
        )
        self._emit_commit(d)
        return d

    def _supersede_committed_locked(
        self,
        *,
        lexical: str,
        speaker: int,
        channel: Any,
        cand_start: float,
        cand_end: float,
        event_id: str,
        metadata: dict[str, Any],
        deepgram_request_id: str,
        speech_final: Any,
    ) -> LifecycleDecision:
        prev = self._last_committed
        assert prev is not None
        original_id = prev.committed_record_id or prev.utterance_id
        self._seq += 1
        uid = prev.utterance_id  # keep same canonical utterance identity
        active = ActiveUtterance(
            utterance_id=uid,
            session_id=self._session_id,
            state=READY_TO_COMMIT,
            speaker=int(speaker or prev.speaker or 1),
            channel=channel if channel is not None else prev.channel,
            text=lexical,
            version=int(prev.version) + 1,
            start_time=cand_start if cand_start >= 0 else prev.start_time,
            end_time=cand_end if cand_end >= 0 else prev.end_time,
            deepgram_request_id=str(deepgram_request_id or prev.deepgram_request_id),
            lineage_ids=list(prev.lineage_ids) + ([event_id] if event_id else []),
        )
        # Mark previous committed snapshot superseded in audit trail.
        prev.state = SUPERSEDED
        self._active = active
        self._committed_utterance_ids.discard(uid)
        self._stats["supersessions"] += 1

        d_super = LifecycleDecision(
            decision=SUPERSEDE_PREVIOUS,
            reason="authoritative_same_utterance_correction",
            utterance_id=uid,
            text=lexical,
            previous_text=prev.text,
            should_supersede_committed=True,
            superseded_record_id=original_id,
            version=active.version,
            session_id=self._session_id,
            event_id=event_id,
            metadata={
                "original_record_id": original_id,
                "replacement_utterance_id": uid,
                "session_id": self._session_id,
                "start_time": active.start_time,
                "end_time": active.end_time,
            },
        )
        self._record_decision(
            d_super, is_final=True, speech_final=speech_final, channel=channel
        )

        if speech_final is False:
            active.state = ACTIVE_FINAL_CHUNK
            self._arm_timeout_locked()
            d_super.should_update_interim = True
            d_super.decision = HOLD_FINAL_CHUNK
            self._emit_interim(d_super)
            return d_super

        commit = self._commit_locked(
            reason="supersede_then_commit",
            event_id=event_id,
            metadata={
                **metadata,
                "superseded_record_id": original_id,
                "original_record_id": original_id,
            },
            decision_name=SUPERSEDE_PREVIOUS,
        )
        commit.should_supersede_committed = True
        commit.superseded_record_id = original_id
        commit.previous_text = prev.text
        return commit

    # ------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------
    def _arm_timeout_locked(self) -> None:
        self._timeout_token += 1
        token = self._timeout_token
        self._cancel_timeout_job_only_locked()
        host = self._host
        ms = self._commit_fallback_ms

        def _fire() -> None:
            decision = self.on_timeout(token=token)
            if decision is None:
                return
            # Commit callback already emitted via _emit_commit when should_commit.

        if host is not None and callable(getattr(host, "after", None)):
            try:
                self._timeout_after_id = host.after(ms, _fire)
                return
            except Exception:
                pass
        timer = threading.Timer(ms / 1000.0, _fire)
        timer.daemon = True
        timer.start()
        self._timeout_after_id = timer

    def _cancel_timeout_job_only_locked(self) -> None:
        job = self._timeout_after_id
        self._timeout_after_id = None
        if job is None:
            return
        host = self._host
        if host is not None and callable(getattr(host, "after_cancel", None)):
            try:
                if not isinstance(job, threading.Timer):
                    host.after_cancel(job)
                    return
            except Exception:
                pass
        if isinstance(job, threading.Timer):
            try:
                job.cancel()
            except Exception:
                pass

    def _cancel_timeout_locked(self) -> None:
        self._timeout_token += 1
        self._cancel_timeout_job_only_locked()

    # ------------------------------------------------------------------
    # Emit / log
    # ------------------------------------------------------------------
    def _emit_interim(self, decision: LifecycleDecision) -> None:
        cb = self._on_interim_update
        if cb is None and self._host is not None:
            handler = getattr(self._host, "on_interim_transcript", None)
            if callable(handler):

                def _default(dec: LifecycleDecision) -> None:
                    meta = dict(dec.metadata or {})
                    meta["lifecycle_decision"] = dec.decision
                    meta["canonical_utterance_id"] = dec.utterance_id
                    meta["source_version"] = dec.version
                    meta["is_final"] = False
                    handler(
                        int(meta.get("speaker") or 1),
                        dec.text,
                        metadata=meta,
                    )

                cb = _default
        if cb and decision.should_update_interim:
            try:
                cb(decision)
            except Exception:
                pass

    def _emit_commit(self, decision: LifecycleDecision) -> None:
        cb = self._on_commit
        if cb is None and self._host is not None:
            publisher = getattr(self._host, "_publish_final_transcript_segment", None)
            if callable(publisher):

                def _default(dec: LifecycleDecision) -> None:
                    meta = dict(dec.metadata or {})
                    meta["speech_final"] = True
                    meta["canonical_utterance_id"] = dec.utterance_id
                    meta["source_version"] = dec.version
                    meta["lifecycle_decision"] = dec.decision
                    meta["source_raw_event_ids"] = list(
                        meta.get("source_raw_event_ids") or []
                    )
                    if dec.superseded_record_id:
                        meta["superseded_record_id"] = dec.superseded_record_id
                    publisher(
                        int(meta.get("speaker") or 1),
                        dec.text,
                        metadata=meta,
                        commit_reason=str(
                            meta.get("lifecycle_commit_reason") or "utterance_lifecycle"
                        ),
                    )

                cb = _default
        if cb and decision.should_commit:
            try:
                cb(decision)
            except Exception:
                pass

    def _record_decision(
        self,
        decision: LifecycleDecision,
        *,
        is_final: bool,
        speech_final: Any,
        channel: Any,
        commit: bool = False,
    ) -> None:
        row = {
            "ts": time.time(),
            "session_id": decision.session_id or self._session_id,
            "event_id": decision.event_id,
            "channel": channel,
            "start": (decision.metadata or {}).get("start_time"),
            "duration": None,
            "is_final": bool(is_final),
            "speech_final": speech_final,
            "utterance_id": decision.utterance_id,
            "lexical_text": decision.text,
            "active_utterance_id": decision.utterance_id,
            "previous_active_text": decision.previous_text,
            "decision": decision.decision,
            "decision_reason": decision.reason,
            "canonical_commit_created": bool(commit or decision.should_commit),
            "canonical_record_id": "",
            "superseded_record_id": decision.superseded_record_id,
            "translation_job_created": bool(commit or decision.should_commit),
            "source_version": decision.version,
        }
        start = _as_float((decision.metadata or {}).get("start_time"), -1.0)
        end = _as_float((decision.metadata or {}).get("end_time"), -1.0)
        if start >= 0 and end >= start:
            row["duration"] = round(end - start, 3)
        self._events.append(row)
        self._log_event(row)

    def _log_event(self, row: dict[str, Any]) -> None:
        path = self._event_log_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass


# Module-level owner bound to the live host (session-scoped via reset_for_session).
_owner_lock = threading.RLock()
_owner: Optional[UtteranceLifecycleOwner] = None


def get_utterance_lifecycle(host: Any = None) -> UtteranceLifecycleOwner:
    global _owner
    with _owner_lock:
        if _owner is None:
            try:
                from alpha.constants import UTTERANCE_COMMIT_FALLBACK_MS

                ms = int(UTTERANCE_COMMIT_FALLBACK_MS)
            except Exception:
                ms = DEFAULT_COMMIT_FALLBACK_MS
            _owner = UtteranceLifecycleOwner(host=host, commit_fallback_ms=ms)
        elif host is not None:
            _owner.bind_host(host)
        return _owner


def reset_utterance_lifecycle(host: Any = None, session_id: str = "") -> UtteranceLifecycleOwner:
    owner = get_utterance_lifecycle(host)
    sid = session_id or str(getattr(host, "_live_session_id", "") or "")
    owner.reset_for_session(sid)
    return owner


def should_use_utterance_lifecycle(host: Any) -> bool:
    """English / generic finals only — Japanese keeps its stabilizer path."""
    try:
        from alpha.transcription.japanese_final_chunk_stabilizer import (
            should_use_japanese_final_stabilizer,
        )

        if should_use_japanese_final_stabilizer(host):
            return False
    except Exception:
        pass
    lang = str(getattr(host, "_listen_language", "") or "").lower()
    if lang.startswith("ja"):
        return False
    return True
