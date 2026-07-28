"""Strict stop evidence evaluation using live + reconciliation (fail closed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alpha.utils.final_status_reconciliation import (
    load_reconciled_status,
    reconciled_value,
)
from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import _read_json
from alpha.utils.strict_evidence_values import (
    StrictEvidenceError,
    require_false,
    require_true,
    require_zero,
)


def evaluate_strict_stop_evidence(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    live = _read_json(folder / "artifacts" / "LIVE_RUN_STATUS.json")
    recon = load_reconciled_status(folder)
    failures: list[str] = []
    values: dict[str, Any] = {}

    def pick(field: str) -> Any:
        rv = reconciled_value(recon, field) if recon else None
        if rv is not None:
            return rv, "reconciliation"
        return live.get(field), "live"

    checks = [
        ("stop_drain_barrier_passed", require_true, True),
        ("transcript_queue_remaining", require_zero, 0),
        ("ui_bus_queue_remaining", require_zero, 0),
        ("language_pipeline_queue_size", require_zero, 0),
        ("audio_queue_size", require_zero, 0),
        ("language_pipeline_worker_alive", require_false, False),
        ("is_stopping", require_false, False),
        ("is_finalizing", require_false, False),
        ("stop_finalize_completed", require_true, True),
    ]
    # stop_finalize_failed must be false
    extra = ("stop_finalize_failed", require_false)

    for field, checker, _expected in checks:
        val, src = pick(field)
        # Fallbacks for queue field aliases in live
        if val is None and field == "transcript_queue_remaining":
            val = live.get("transcript_ui_queue_size", live.get("ui_queue_size"))
            src = "live_alias"
        if val is None and field == "ui_bus_queue_remaining":
            val = live.get("ui_event_bus_queue_size")
            src = "live_alias"
        values[field] = {"value": val, "source": src}
        try:
            checker(val, field)
        except StrictEvidenceError as exc:
            failures.append(str(exc))

    val, src = pick("stop_finalize_failed")
    if val is None:
        val = live.get("stop_finalize_failed")
        src = "live"
    values["stop_finalize_failed"] = {"value": val, "source": src}
    try:
        require_false(val, "stop_finalize_failed")
    except StrictEvidenceError as exc:
        failures.append(str(exc))

    if not recon:
        failures.append("FINAL_STATUS_RECONCILIATION.json missing")

    return {
        "strict_stop_evidence_passed": len(failures) == 0,
        "failures": failures,
        "values": values,
        "reconciliation_present": bool(recon),
    }
