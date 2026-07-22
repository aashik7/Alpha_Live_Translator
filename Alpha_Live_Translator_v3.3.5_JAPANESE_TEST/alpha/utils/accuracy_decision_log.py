"""Accuracy decision audit logs — assembler, quarantine, correction."""



from __future__ import annotations



import json

from typing import Any, Optional



from alpha.utils.evidence_jsonl import append_jsonl

from alpha.utils.troubleshooting_paths import get_accuracy_path



_correction_inactive_logged = False

_summary_written = False





def log_assembler_decision(

    *,

    raw_text: str = "",

    buffer_before: str = "",

    buffer_after: str = "",

    decision: str = "",

    reason: str = "",

    commit_reason: str = "",

    hold_reason: str = "",

    release_reason: str = "",

    merge_reason: str = "",

    incomplete_tail: bool = False,

    buffer_age_ms: float = 0.0,

    last_fragment_age_ms: float = 0.0,

    translation_ready: bool = False,

    raw_mutated: bool = False,

    exception: str = "",

) -> None:

    append_jsonl(

        get_accuracy_path("assembler_decisions"),

        {

            "raw_text": raw_text,

            "buffer_before": buffer_before,

            "buffer_after": buffer_after,

            "decision": decision,

            "reason": reason,

            "commit_reason": commit_reason,

            "hold_reason": hold_reason,

            "release_reason": release_reason,

            "merge_reason": merge_reason,

            "incomplete_tail": incomplete_tail,

            "punctuation_start": bool(

                (raw_text or buffer_after or "").strip()[:1] in {"、", "。", "！", "？", "，", "．"}

            ),

            "buffer_age_ms": buffer_age_ms,

            "last_fragment_age_ms": last_fragment_age_ms,

            "translation_ready": bool(translation_ready),

            "raw_mutated": bool(raw_mutated),

            "exception_type": exception if exception else "",

            "exception_message": exception if exception else "",

        },

    )





def log_quarantine_decision(

    *,

    raw_text: str = "",

    compact_length: int = 0,

    time_since_last_good_commit_ms: float = 0.0,

    rms: Optional[float] = None,

    quarantine: bool = False,

    released: bool = False,

    dropped: bool = False,

    reason: str = "",

    later_in_stable: bool = False,

    raw_mutated: bool = False,

) -> None:

    payload: dict[str, Any] = {

        "raw_text": raw_text,

        "compact_length": compact_length,

        "time_since_last_good_commit_ms": time_since_last_good_commit_ms,

        "source_activity": rms if rms is not None else "unknown",

        "quarantine_decision": (

            "released" if released else "dropped" if dropped else "quarantined" if quarantine else "not_needed"

        ),

        "reason": reason,

        "later_committed_to_stable": later_in_stable,

        "raw_mutated": bool(raw_mutated),

    }

    if rms is not None:

        payload["rms"] = rms

    append_jsonl(get_accuracy_path("quarantine_decisions"), payload)





def log_stable_merge_correction(

    *,

    raw_input_text: str,

    stable_output_text: str,

    transform_type: str,

    transform_reason: str,

    previous_text_before: str = "",

    previous_text_after: str = "",

    fragment_text: str = "",

) -> None:

    append_jsonl(

        get_accuracy_path("correction_decisions"),

        {

            "raw_input_text": raw_input_text,

            "stable_output_text": stable_output_text,

            "transform_type": transform_type,

            "transform_reason": transform_reason,

            "previous_text_before": previous_text_before,

            "previous_text_after": previous_text_after,

            "fragment_text": fragment_text,

            "raw_mutated": False,

        },

    )





def log_correction_inactive() -> None:

    global _correction_inactive_logged

    if _correction_inactive_logged:

        return

    _correction_inactive_logged = True

    append_jsonl(

        get_accuracy_path("correction_decisions"),

        {

            "event": "CORRECTION_LAYER_INACTIVE_EXCEPT_STABLE_MERGE_FOR_8_5_21",

            "correction_performed": False,

        },

    )

    try:

        from alpha.utils.japanese_accuracy_log import jp_accuracy_log



        jp_accuracy_log("CORRECTION_LAYER_INACTIVE_EXCEPT_STABLE_MERGE_FOR_8_5_21")

    except Exception:

        pass





def write_translation_readiness_summary(stats: dict[str, Any]) -> None:

    global _summary_written

    path = get_accuracy_path("translation_readiness_summary")

    try:

        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

        _summary_written = True

        from alpha.utils.japanese_accuracy_log import jp_accuracy_log



        jp_accuracy_log("TRANSLATION_READINESS_SUMMARY_WRITTEN", **stats)

    except Exception:

        pass





def write_japanese_accuracy_summary(stats: dict[str, Any]) -> None:

    path = get_accuracy_path("japanese_accuracy_summary")

    try:

        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    except Exception:

        pass





def log_accuracy_decision_logging_active() -> None:

    log_correction_inactive()

    try:

        from alpha.utils.japanese_accuracy_log import jp_accuracy_log



        jp_accuracy_log("ACCURACY_DECISION_LOGGING_ACTIVE")

        jp_accuracy_log("ASSEMBLER_DECISION_LOGGING_ACTIVE")

        jp_accuracy_log("QUARANTINE_DECISION_LOGGING_ACTIVE")

        jp_accuracy_log("STABLE_LAYER_ONLY_TRANSFORM_CONFIRMED")

    except Exception:

        pass


