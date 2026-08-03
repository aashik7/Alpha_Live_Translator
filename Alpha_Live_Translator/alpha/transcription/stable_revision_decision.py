"""Centralized safe stable-line revision decision (V26.5.1).

Rules (general — no phrase hardcoding):
1. Exact duplicate → no_op
2. Later hypothesis extending the same earlier hypothesis → revise/retire earlier
3. Revision of the same segment only when identity is proven (timing/utterance,
   or lineage plus textual relatedness) → update in place
4. Never delete unique committed content because a later unrelated hypothesis is shorter
5. Textual similarity alone is insufficient to retire a record
6. Adjacent unrelated utterances must never share revision identity
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Optional

from alpha.constants import (
    REVISION_CONTENT_LOSS_GUARD_ENABLED,
    REVISION_LINEAGE_REQUIRED,
    REVISION_TERMINAL_SENTENCE_GUARD_ENABLED,
    SAFE_STABLE_REVISION_ENABLED,
    UNPROVEN_REVISION_DEFAULT_ACTION,
)
from alpha.transcription.speaker_boundary_guard import speakers_confirmed_same

_TERMINAL_RE = re.compile(r"[。！？!?]\s*$")
_PUNCT_SPACE_RE = re.compile(r"\s*([、。！？!?])\s*")


def _safe_normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text or "")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = _PUNCT_SPACE_RE.sub(r"\1", normalized)
    return normalized


def _ends_terminal(text: str) -> bool:
    return bool(_TERMINAL_RE.search((text or "").strip()))


def _direct_extension(previous: str, candidate: str) -> bool:
    prev_n = _safe_normalize(previous)
    cand_n = _safe_normalize(candidate)
    if not prev_n or not cand_n:
        return False
    if cand_n.startswith(prev_n) and len(cand_n) > len(prev_n):
        return True
    # Near-extension: candidate continues the same hypothesis with minor mid-string edits.
    if len(prev_n) >= 12 and len(cand_n) > len(prev_n):
        prefix_len = max(12, int(len(prev_n) * 0.85))
        if cand_n.startswith(prev_n[:prefix_len]):
            return True
    return False


def _lineage_overlap(previous_ids: list[str], candidate_ids: list[str]) -> int:
    if not previous_ids or not candidate_ids:
        return 0
    prev_set = {str(x) for x in previous_ids if x}
    cand_set = {str(x) for x in candidate_ids if x}
    return len(prev_set & cand_set)


def _similarity(previous: str, candidate: str) -> float:
    prev_n = _safe_normalize(previous)
    cand_n = _safe_normalize(candidate)
    if not prev_n or not cand_n:
        return 0.0
    return SequenceMatcher(None, prev_n, cand_n).ratio()


def _length_ratio(previous: str, candidate: str) -> float:
    prev_len = max(len(_safe_normalize(previous)), 1)
    cand_len = len(_safe_normalize(candidate))
    return cand_len / prev_len


def _as_float(value: Any, default: float = -1.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _timing_same_segment(
    previous_record: dict[str, Any],
    candidate_metadata: dict[str, Any],
) -> bool:
    """Prove same Deepgram segment via start/end timing identity or overlap."""
    prev_start = _as_float(
        previous_record.get("start_time", previous_record.get("audio_start")),
        -1.0,
    )
    prev_end = _as_float(
        previous_record.get("end_time", previous_record.get("audio_end")),
        -1.0,
    )
    cand_start = _as_float(
        candidate_metadata.get("start_time", candidate_metadata.get("audio_start")),
        -1.0,
    )
    cand_end = _as_float(
        candidate_metadata.get("end_time", candidate_metadata.get("audio_end")),
        -1.0,
    )
    # Nested metadata from raw events
    if cand_start < 0 and isinstance(candidate_metadata.get("metadata"), dict):
        nested = candidate_metadata["metadata"]
        cand_start = _as_float(nested.get("start_time"), cand_start)
        cand_end = _as_float(nested.get("end_time"), cand_end)

    if prev_start >= 0 and cand_start >= 0 and abs(prev_start - cand_start) <= 0.08:
        return True
    if prev_start >= 0 and prev_end > prev_start and cand_start >= 0 and cand_end > cand_start:
        latest_start = max(prev_start, cand_start)
        earliest_end = min(prev_end, cand_end)
        if earliest_end - latest_start > 0.05:
            return True
    # utterance / segment identity fields when present
    for key in ("utterance_id", "segment_id", "deepgram_utterance_id"):
        pv = previous_record.get(key)
        cv = candidate_metadata.get(key)
        if pv is not None and cv is not None and str(pv) == str(cv) and str(pv):
            return True
    return False


def _textually_related_revision(previous: str, candidate: str) -> bool:
    """Require more than bare lineage sticky-overlap to treat texts as one segment."""
    if _direct_extension(previous, candidate):
        return True
    prev_n = _safe_normalize(previous)
    cand_n = _safe_normalize(candidate)
    if not prev_n or not cand_n:
        return False
    if prev_n in cand_n or cand_n in prev_n:
        return True
    sim = _similarity(previous, candidate)
    ratio = _length_ratio(previous, candidate)
    # Same-chain revise needs high textual relatedness; bare lineage is insufficient.
    return sim >= 0.72 and 0.55 <= ratio <= 1.85


def _unique_content_lost(previous: str, candidate: str) -> bool:
    """True when previous unique normalized content is absent from the candidate."""
    prev_n = _safe_normalize(previous)
    cand_n = _safe_normalize(candidate)
    if not prev_n:
        return False
    if not cand_n:
        return True
    if prev_n in cand_n:
        return False
    if _direct_extension(previous, candidate):
        return False
    # Substantial unique previous content not retained.
    if len(prev_n) >= 8 and _similarity(previous, candidate) < 0.72:
        return True
    if len(prev_n) >= 8 and not cand_n.startswith(prev_n[: max(8, len(prev_n) // 4)]):
        return True
    return False


def _same_revision_chain(
    *,
    previous_record: dict[str, Any],
    candidate_ids: list[str],
    candidate_metadata: dict[str, Any],
    previous_text: str,
    candidate_text: str,
) -> tuple[bool, int, bool]:
    previous_ids = list(previous_record.get("source_raw_event_ids") or [])
    overlap = _lineage_overlap(previous_ids, candidate_ids)
    timing = _timing_same_segment(previous_record, candidate_metadata)
    # Timing / utterance identity alone proves same segment.
    if timing:
        return True, overlap, timing
    # Lineage overlap alone is sticky and false-positive across adjacent utterances.
    # Require textual relatedness as well.
    if overlap > 0 and _textually_related_revision(previous_text, candidate_text):
        return True, overlap, timing
    return False, overlap, timing


def _content_loss_risk(
    previous: str,
    candidate: str,
    *,
    extends: bool,
    same_segment: bool,
    lineage_overlap: int,
) -> bool:
    """Block destructive replacement of *unique* content by an unrelated shorter hyp."""
    if not previous or not candidate:
        return False
    if extends:
        return False
    # V26.5.1: same_segment no longer auto-clears content-loss risk.
    if same_segment and not _unique_content_lost(previous, candidate):
        return False
    if same_segment and _unique_content_lost(previous, candidate):
        return True
    prev_n = _safe_normalize(previous)
    cand_n = _safe_normalize(candidate)
    if not cand_n.startswith(prev_n[: min(len(prev_n), max(8, len(prev_n) // 4))]):
        if lineage_overlap <= 0:
            return True
    ratio = _length_ratio(previous, candidate)
    if ratio < 0.65 and lineage_overlap <= 0:
        return True
    if prev_n and cand_n and prev_n not in cand_n and lineage_overlap <= 0:
        sim = _similarity(previous, candidate)
        if sim < 0.5:
            return True
    return False


def _base_decision() -> dict[str, Any]:
    return {
        "action": UNPROVEN_REVISION_DEFAULT_ACTION,
        "allowed": False,
        "reason": "",
        "target_line_id": "",
        "lineage_overlap_count": 0,
        "previous_terminal": False,
        "candidate_extends_previous": False,
        "similarity_used": False,
        "content_loss_risk": False,
        "similarity_score": None,
        "same_segment_proven": False,
        "timing_same_segment": False,
    }


def decide_stable_revision_action(
    *,
    previous_record: Optional[dict[str, Any]],
    candidate_text: str,
    update_previous_requested: bool,
    candidate_raw_event_ids: Optional[list[str]] = None,
    candidate_metadata: Optional[dict[str, Any]] = None,
    candidate_speaker: Any = None,
) -> dict[str, Any]:
    """Deterministic revision gate used by assembler and offline replay."""
    decision = _base_decision()
    candidate = candidate_text or ""
    metadata = dict(candidate_metadata or {})
    candidate_ids = list(candidate_raw_event_ids or metadata.get("source_raw_event_ids") or [])

    if not SAFE_STABLE_REVISION_ENABLED:
        if update_previous_requested:
            decision.update(
                action="revise_previous",
                allowed=True,
                reason="safe_revision_disabled_legacy_path",
            )
        else:
            decision.update(action="append", allowed=True, reason="no_revision_requested")
        return decision

    if not previous_record or not str(previous_record.get("text") or "").strip():
        decision.update(action="append", allowed=True, reason="no_active_previous_record")
        return decision

    # fixes TASK_2C_REPORT.md: speaker identity is checked BEFORE any
    # text-adjacency/candidate-extends-previous rule below (Rules A-F all
    # operate on text alone). A different, or unknown, speaker can never
    # revise/extend the previous record -- always append a new line.
    previous_speaker = previous_record.get("speaker")
    if not speakers_confirmed_same(previous_speaker, candidate_speaker):
        decision.update(
            action="append",
            allowed=True,
            reason="speaker_boundary_forced_new_line",
        )
        return decision

    previous_text = str(previous_record.get("text") or "")
    previous_ids = list(previous_record.get("source_raw_event_ids") or [])
    target_line_id = str(previous_record.get("line_id") or "")
    decision["target_line_id"] = target_line_id
    decision["previous_terminal"] = _ends_terminal(previous_text)

    requested_target = str(metadata.get("revision_target_line_id") or target_line_id)
    if (
        update_previous_requested
        and requested_target
        and target_line_id
        and requested_target != target_line_id
    ):
        decision.update(action="append", allowed=False, reason="revision_target_mismatch")
        return decision

    extends = _direct_extension(previous_text, candidate)
    decision["candidate_extends_previous"] = extends
    same_segment, overlap, timing = _same_revision_chain(
        previous_record=previous_record,
        candidate_ids=candidate_ids,
        candidate_metadata=metadata,
        previous_text=previous_text,
        candidate_text=candidate,
    )
    decision["lineage_overlap_count"] = overlap
    decision["same_segment_proven"] = same_segment
    decision["timing_same_segment"] = timing

    # RULE A — exact duplicate
    if _safe_normalize(candidate) == _safe_normalize(previous_text):
        decision.update(action="no_op", allowed=True, reason="exact_duplicate")
        return decision

    # RULE B — extension of the same hypothesis
    if extends:
        decision.update(
            action="revise_previous",
            allowed=True,
            reason="candidate_directly_extends_previous",
        )
        return decision

    # RULE C — proven same-segment revision; never delete unique prior content
    if same_segment:
        loss_risk = False
        if REVISION_CONTENT_LOSS_GUARD_ENABLED:
            loss_risk = _content_loss_risk(
                previous_text,
                candidate,
                extends=False,
                same_segment=True,
                lineage_overlap=overlap,
            )
        decision["content_loss_risk"] = loss_risk
        if loss_risk:
            decision.update(
                action="append",
                allowed=False,
                reason="destructive_content_loss_prevented",
            )
            return decision
        decision.update(
            action="revise_previous",
            allowed=True,
            reason="same_segment_revision",
        )
        return decision

    # RULE D — terminal guard only when same-segment is NOT proven
    if (
        REVISION_TERMINAL_SENTENCE_GUARD_ENABLED
        and decision["previous_terminal"]
        and not extends
        and not same_segment
        and update_previous_requested
    ):
        decision.update(
            action="append",
            allowed=False,
            reason="completed_previous_sentence_protected",
        )
        return decision

    # RULE E — explicit revision request with lineage (when required)
    if update_previous_requested:
        if REVISION_LINEAGE_REQUIRED:
            lineage_proven = bool(target_line_id) and bool(previous_ids) and bool(candidate_ids) and overlap > 0
            if not lineage_proven and not timing:
                reason = "revision_lineage_not_proven"
                if not previous_ids or not candidate_ids:
                    reason = "revision_lineage_missing"
                elif overlap <= 0:
                    reason = "revision_lineage_disjoint"
                decision.update(action="append", allowed=False, reason=reason)
                return decision

        sim = _similarity(previous_text, candidate)
        ratio = _length_ratio(previous_text, candidate)
        decision["similarity_score"] = round(sim, 4)
        # Textual similarity only when identity/timing already weakly associated
        if (overlap > 0 or timing) and 0.65 <= ratio <= 1.75 and sim >= 0.85:
            decision["similarity_used"] = True
            if REVISION_CONTENT_LOSS_GUARD_ENABLED and _content_loss_risk(
                previous_text,
                candidate,
                extends=False,
                same_segment=False,
                lineage_overlap=overlap,
            ):
                decision.update(
                    action="append",
                    allowed=False,
                    reason="destructive_content_loss_prevented",
                    content_loss_risk=True,
                )
                return decision
            decision.update(
                action="revise_previous",
                allowed=True,
                reason="lineage_proven_similarity_revision",
            )
            return decision

        if REVISION_CONTENT_LOSS_GUARD_ENABLED:
            loss_risk = _content_loss_risk(
                previous_text,
                candidate,
                extends=False,
                same_segment=False,
                lineage_overlap=overlap,
            )
            decision["content_loss_risk"] = loss_risk
            if loss_risk:
                decision.update(
                    action="append",
                    allowed=False,
                    reason="destructive_content_loss_prevented",
                )
                return decision

        decision.update(action="append", allowed=False, reason="uncertain_default_append")
        return decision

    # RULE F — no explicit request and no proven same-chain relation → append new
    decision.update(action="append", allowed=True, reason="no_explicit_revision_request")
    return decision


# Alias required by spec
_decide_stable_revision_action = decide_stable_revision_action
