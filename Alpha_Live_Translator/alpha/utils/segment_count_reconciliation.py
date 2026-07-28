"""Canonical final segment counts at stop — reconciles health vs final sources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def _line_count(text: str) -> int:
    return len([ln for ln in (text or "").splitlines() if ln.strip()])


def _read_last_health_counts(folder: Path) -> dict[str, Any]:
    path = folder / "LAST_HEALTH_SNAPSHOT.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "internal_stable_commit_count": int(
                data.get("internal_stable_commit_count", -1)
            ),
            "exported_ui_segment_count": int(
                data.get("exported_ui_segment_count", -1)
            ),
            "transcript_snapshot_count": int(
                data.get("transcript_snapshot_count", -1)
            ),
        }
    except Exception:
        return {}


def reconcile_final_segment_counts(host: Any = None) -> dict[str, Any]:
    """Recompute final counts from authoritative sources after stop flush."""
    from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_event_counts, jp_accuracy_log
    from alpha.utils.run_artifacts import (
        ensure_run_artifacts_folder,
        get_transcript_text_from_snapshot,
    )
    from alpha.utils.run_identity import get_current_run_identity
    from alpha.utils.transcript_snapshot_store import snapshot_segment_count

    event_counts = get_japanese_accuracy_event_counts()
    internal_stable = int(event_counts.get("STABLE_JAPANESE_COMMIT", 0))
    exported_ui = int(getattr(host, "_exported_ui_segment_count", 0) or 0) if host else 0
    snapshot_count = int(snapshot_segment_count())
    alpha_text = get_transcript_text_from_snapshot()
    alpha_lines = _line_count(alpha_text)

    identity = get_current_run_identity()
    partial_lines = 0
    folder = ensure_run_artifacts_folder(identity) if identity else None
    if folder is not None:
        partial_path = folder / "Alpha_output_PARTIAL.txt"
        if partial_path.exists():
            try:
                partial_lines = _line_count(partial_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    ui_display = exported_ui
    if host is not None:
        store = getattr(host, "transcript_store", None)
        if store is not None and hasattr(store, "get_all"):
            try:
                ui_display = len(
                    [
                        s
                        for s in store.get_all()
                        if (getattr(s, "text", "") or "").strip()
                    ]
                )
            except Exception:
                pass

    last_health = _read_last_health_counts(folder) if folder else {}

    result: dict[str, Any] = {
        "final_internal_stable_commit_count": internal_stable,
        "final_exported_ui_segment_count": exported_ui,
        "final_transcript_snapshot_count": snapshot_count,
        "final_alpha_output_line_count": alpha_lines,
        "final_partial_alpha_output_line_count": partial_lines,
        "final_ui_display_segment_count": ui_display,
        "last_health_snapshot_counts": last_health,
        "final_after_stop_counts": {
            "internal_stable_commit_count": internal_stable,
            "exported_ui_segment_count": exported_ui,
            "transcript_snapshot_count": snapshot_count,
            "alpha_output_line_count": alpha_lines,
        },
    }

    mismatches: list[str] = []
    if last_health:
        for key in (
            "internal_stable_commit_count",
            "exported_ui_segment_count",
        ):
            before = last_health.get(key, -1)
            after_key = key
            after = result["final_after_stop_counts"].get(after_key, -1)
            if before >= 0 and after >= 0 and before != after:
                mismatches.append(
                    f"{key}: health={before} final={after} (explainable: stop_flush)"
                )

    if mismatches:
        result["segment_count_mismatch_warnings"] = mismatches
        result["mismatch_source"] = "health_snapshot_before_final_flush_vs_final_after_stop_flush"
        jp_accuracy_log("FINAL_SEGMENT_COUNT_MISMATCH_WARNING", mismatches=mismatches)
    else:
        jp_accuracy_log("FINAL_SEGMENT_COUNTS_RECONCILED", **result)

    return result
