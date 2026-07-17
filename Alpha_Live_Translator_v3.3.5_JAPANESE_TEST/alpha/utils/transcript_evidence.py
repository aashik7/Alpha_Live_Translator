"""Transcript evidence layers — raw Deepgram, stable commits, UI export."""

from __future__ import annotations

import random
import time
from typing import Any, Optional

from alpha.constants import (
    DEBUG_VERBOSE_INTERIMS,
    RAW_INTERIM_LOG_SAMPLE_RATE,
    RAW_INTERIM_LOG_SAMPLING_ENABLED,
)
from alpha.utils.evidence_jsonl import append_jsonl
from alpha.utils.troubleshooting_paths import get_transcript_path

_interim_counter = 0


def _run_id() -> str:
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if ident is not None:
            return ident.run_id
    except Exception:
        pass
    return ""


def log_raw_deepgram_final(
    *,
    raw_text: str,
    is_final: bool = True,
    source: str = "deepgram",
    confidence: Optional[float] = None,
    channel: Optional[str] = None,
    accepted_by_gate: bool = True,
    drop_reason: str = "",
) -> None:
    payload: dict[str, Any] = {
        "run_id": _run_id(),
        "source": source,
        "is_final": is_final,
        "raw_text": raw_text,
        "accepted_by_gate": accepted_by_gate,
    }
    if confidence is not None:
        payload["confidence"] = confidence
    if channel:
        payload["channel"] = channel
    if drop_reason:
        payload["drop_reason"] = drop_reason
    append_jsonl(get_transcript_path("raw_deepgram_finals"), payload)
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("RAW_DEEPGRAM_FINAL_LOGGED", source=source)
    except Exception:
        pass


def log_raw_deepgram_interim_sampled(
    *,
    raw_interim_text: str,
    source: str = "deepgram",
    sampling_reason: str = "sampled",
) -> None:
    global _interim_counter
    _interim_counter += 1
    if not DEBUG_VERBOSE_INTERIMS and RAW_INTERIM_LOG_SAMPLING_ENABLED:
        if _interim_counter % max(1, RAW_INTERIM_LOG_SAMPLE_RATE) != 0:
            if random.randint(1, RAW_INTERIM_LOG_SAMPLE_RATE) != 1:
                return
            sampling_reason = "random_sample"
    append_jsonl(
        get_transcript_path("raw_deepgram_interims_sampled"),
        {
            "run_id": _run_id(),
            "raw_interim_text": raw_interim_text,
            "sampling_reason": sampling_reason,
            "source": source,
        },
    )


_interim_counter = 0
_stable_commit_counter = 0


def get_stable_commit_count() -> int:
    return _stable_commit_counter


def reset_stable_commit_counter() -> None:
    global _stable_commit_counter
    _stable_commit_counter = 0


def log_stable_commit(
    *,
    stable_text: str,
    commit_reason: str,
    assembler_metadata: Optional[dict[str, Any]] = None,
    translation_ready: bool = False,
    source_raw_event_ids: Optional[list[str]] = None,
    export_eligibility: str = "export_required",
    suppression_classification: str = "",
    suppression_reason: str = "",
    debug_history_only: bool = False,
) -> str:
    global _stable_commit_counter
    _stable_commit_counter += 1
    stable_commit_id = f"stable-{_stable_commit_counter}"
    meta = dict(assembler_metadata or {})
    if export_eligibility:
        meta["export_eligibility"] = export_eligibility
    if suppression_classification:
        meta["suppression_classification"] = suppression_classification
    if suppression_reason:
        meta["suppression_reason"] = suppression_reason
    if debug_history_only:
        meta["debug_history_only"] = True
    # Forward-looking: copy nested assembler lineage to top-level when missing
    top_lineage = list(source_raw_event_ids or [])
    if not top_lineage:
        nested = meta.get("source_raw_event_ids")
        if isinstance(nested, list) and nested:
            top_lineage = [str(x) for x in nested if str(x).strip()]
        elif meta.get("raw_event_id"):
            top_lineage = [str(meta.get("raw_event_id"))]
    append_jsonl(
        get_transcript_path("stable_commits"),
        {
            "run_id": _run_id(),
            "stable_commit_id": stable_commit_id,
            "stable_text": stable_text,
            "commit_reason": commit_reason,
            "assembler_metadata": meta,
            "translation_ready": translation_ready,
            "source_raw_event_ids": top_lineage,
            "export_eligibility": export_eligibility,
            "suppression_classification": suppression_classification,
            "suppression_reason": suppression_reason,
            "debug_history_only": debug_history_only,
        },
    )
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("STABLE_COMMIT_LOGGED", commit_reason=commit_reason, stable_commit_id=stable_commit_id)
    except Exception:
        pass
    return stable_commit_id


def log_ui_exported_segment(
    *,
    ui_text: str,
    speaker_label: str = "",
    ui_segment_id: str = "",
    source_stable_commit_id: str = "",
) -> None:
    append_jsonl(
        get_transcript_path("ui_exported_segments"),
        {
            "run_id": _run_id(),
            "ui_text": ui_text,
            "speaker_label": speaker_label,
            "ui_segment_id": ui_segment_id,
            "source_stable_commit_id": source_stable_commit_id,
        },
    )
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("UI_EXPORTED_SEGMENT_LOGGED")
    except Exception:
        pass


def log_traceability_active_once() -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("RAW_STABLE_UI_TRACEABILITY_ACTIVE")
    except Exception:
        pass
