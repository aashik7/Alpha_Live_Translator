"""Centralized safe stable-line revision decision (V25.3.1)."""

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
    return cand_n.startswith(prev_n) and len(cand_n) > len(prev_n)


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


def _content_loss_risk(previous: str, candidate: str, *, extends: bool, lineage_overlap: int) -> bool:
    if not previous or not candidate:
        return False
    if extends:
        return False
    prev_n = _safe_normalize(previous)
    cand_n = _safe_normalize(candidate)
    if not cand_n.startswith(prev_n[: min(len(prev_n), max(8, len(prev_n) // 4))]):
        if lineage_overlap <= 0:
            return True
    ratio = _length_ratio(previous, candidate)
    if ratio < 0.65 and lineage_overlap <= 0:
        return True
    if _ends_terminal(previous) and not extends:
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
    }


def decide_stable_revision_action(
    *,
    previous_record: Optional[dict[str, Any]],
    candidate_text: str,
    update_previous_requested: bool,
    candidate_raw_event_ids: Optional[list[str]] = None,
    candidate_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Deterministic revision gate used by assembler and offline replay."""
    decision = _base_decision()
    candidate = candidate_text or ""
    metadata = candidate_metadata or {}
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

    # RULE 1
    if not update_previous_requested:
        decision.update(action="append", allowed=True, reason="no_explicit_revision_request")
        return decision

    # RULE 2
    if not previous_record or not str(previous_record.get("text") or "").strip():
        decision.update(action="append", allowed=True, reason="no_active_previous_record")
        return decision

    previous_text = str(previous_record.get("text") or "")
    previous_ids = list(previous_record.get("source_raw_event_ids") or [])
    target_line_id = str(previous_record.get("line_id") or "")
    decision["target_line_id"] = target_line_id
    decision["previous_terminal"] = _ends_terminal(previous_text)

    requested_target = str(metadata.get("revision_target_line_id") or target_line_id)
    if requested_target and target_line_id and requested_target != target_line_id:
        decision.update(action="append", allowed=False, reason="revision_target_mismatch")
        return decision

    extends = _direct_extension(previous_text, candidate)
    decision["candidate_extends_previous"] = extends

    # RULE 3
    if _safe_normalize(candidate) == _safe_normalize(previous_text):
        decision.update(action="no_op", allowed=True, reason="exact_duplicate")
        return decision

    # RULE 4
    if extends:
        decision.update(
            action="revise_previous",
            allowed=True,
            reason="candidate_directly_extends_previous",
        )
        return decision

    # RULE 5
    if REVISION_TERMINAL_SENTENCE_GUARD_ENABLED and decision["previous_terminal"] and not extends:
        decision.update(
            action="append",
            allowed=False,
            reason="completed_previous_sentence_protected",
        )
        return decision

    overlap = _lineage_overlap(previous_ids, candidate_ids)
    decision["lineage_overlap_count"] = overlap

    # RULE 6
    if REVISION_LINEAGE_REQUIRED:
        lineage_proven = (
            bool(target_line_id)
            and bool(previous_ids)
            and bool(candidate_ids)
            and overlap > 0
        )
        if not lineage_proven:
            reason = "revision_lineage_not_proven"
            if not previous_ids or not candidate_ids:
                reason = "revision_lineage_missing"
            elif overlap <= 0:
                reason = "revision_lineage_disjoint"
            decision.update(action="append", allowed=False, reason=reason)
            return decision

    # RULE 7 — similarity fallback only with lineage
    sim = _similarity(previous_text, candidate)
    ratio = _length_ratio(previous_text, candidate)
    decision["similarity_score"] = round(sim, 4)
    if overlap > 0 and 0.65 <= ratio <= 1.75 and sim >= 0.85:
        decision["similarity_used"] = True
        if REVISION_CONTENT_LOSS_GUARD_ENABLED and _content_loss_risk(
            previous_text, candidate, extends=False, lineage_overlap=overlap
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

    # RULE 8
    if REVISION_CONTENT_LOSS_GUARD_ENABLED:
        loss_risk = _content_loss_risk(
            previous_text, candidate, extends=False, lineage_overlap=overlap
        )
        decision["content_loss_risk"] = loss_risk
        if loss_risk:
            decision.update(
                action="append",
                allowed=False,
                reason="destructive_content_loss_prevented",
            )
            return decision

    # RULE 9
    decision.update(action="append", allowed=False, reason="uncertain_default_append")
    return decision


# Alias required by spec
_decide_stable_revision_action = decide_stable_revision_action
