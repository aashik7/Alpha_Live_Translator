"""Regression tests for canonical pipeline (V25.3.2)."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import APP_VERSION
from alpha.transcription.canonical_transcript_ledger import (
    apply_decision,
    freeze_snapshot,
    get_active_records,
    reset_for_run,
    serialize_export_payload,
)
from alpha.transcription.revision_metadata import normalize_applied_metadata
from alpha.utils.pipeline_integrity import PipelineIntegrityError

OUT = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3.2/regression_canonical_pipeline_852532.txt")


def _test(name: str, fn) -> str:
    try:
        fn()
        return f"PASS {name}"
    except Exception as exc:
        return f"FAIL {name}: {exc}"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"regression_canonical_pipeline {APP_VERSION}", ""]

    def t_append_downstream():
        reset_for_run("reg-test")
        meta = normalize_applied_metadata({}, applied_action="append", requested_update_previous=True)
        assert meta["applied_action"] == "append"
        assert not meta["update_previous"]

    def t_revise_target():
        reset_for_run("reg-test")
        apply_decision(speaker=2, assembler_text="A", final_text="A", applied_action="append", source_raw_event_ids=["raw-000001"])
        r = apply_decision(
            speaker=2,
            assembler_text="AB",
            final_text="AB",
            applied_action="revise",
            revision_target_id=get_active_records()[-1]["record_id"],
            source_raw_event_ids=["raw-000001"],
        )
        assert r["applied_action"] == "revise"

    def t_noop_duplicate():
        reset_for_run("reg-test")
        apply_decision(speaker=2, assembler_text="dup", final_text="dup", applied_action="append", source_raw_event_ids=["raw-000001"])
        before = len(get_active_records())
        apply_decision(speaker=2, assembler_text="dup", final_text="dup", applied_action="no_op")
        assert len(get_active_records()) == before

    def t_missing_lineage_forces_append():
        reset_for_run("reg-test")
        apply_decision(speaker=2, assembler_text="first", final_text="first", applied_action="append", source_raw_event_ids=["raw-000001"])
        r = apply_decision(
            speaker=2,
            assembler_text="unrelated",
            final_text="unrelated",
            applied_action="revise",
            revision_target_id=get_active_records()[-1]["record_id"],
            source_raw_event_ids=[],
        )
        assert r["applied_action"] == "append"

    def t_legacy_flags_blocked_on_append():
        meta = normalize_applied_metadata({"force_update_previous": True}, applied_action="append", requested_update_previous=True)
        assert not meta["force_update_previous"]

    def t_export_coverage():
        reset_for_run("reg-test")
        apply_decision(speaker=2, assembler_text="L1", final_text="L1", applied_action="append", source_raw_event_ids=["raw-000001"])
        apply_decision(speaker=2, assembler_text="L2", final_text="L2", applied_action="append", source_raw_event_ids=["raw-000002"])
        snap = freeze_snapshot()
        payload = serialize_export_payload(snap)
        assert payload["active_record_count"] == 2
        assert len(payload["lines"]) == 2

    def t_frozen_immutable():
        reset_for_run("reg-test")
        apply_decision(speaker=2, assembler_text="X", final_text="X", applied_action="append", source_raw_event_ids=["raw-000001"])
        freeze_snapshot()
        try:
            apply_decision(speaker=2, assembler_text="Y", final_text="Y", applied_action="append", source_raw_event_ids=["raw-000002"])
            raise AssertionError("expected frozen error")
        except PipelineIntegrityError:
            pass

    for name, fn in [
        ("append_remains_append_downstream", t_append_downstream),
        ("revise_targets_record", t_revise_target),
        ("no_op_duplicate", t_noop_duplicate),
        ("missing_lineage_forces_append", t_missing_lineage_forces_append),
        ("legacy_flags_blocked", t_legacy_flags_blocked_on_append),
        ("export_coverage_100", t_export_coverage),
        ("frozen_snapshot_immutable", t_frozen_immutable),
    ]:
        lines.append(_test(name, fn))

    failed = [ln for ln in lines if ln.startswith("FAIL")]
    lines.append("")
    lines.append("PASSED" if not failed else "FAILED")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print("PASSED" if not failed else "FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
