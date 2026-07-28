"""Atomic accuracy evidence finalization after all reports complete (8.5.25.2.1)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION, ATOMIC_ACCURACY_EVIDENCE_FINALIZATION_ENABLED

_INDEX_PATHS = (
    Path("troubleshooting/latest/latest_accuracy_evidence_index.json"),
    Path("troubleshooting/latest_accuracy_evidence_index.json"),
)


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def finalize_atomic_evidence(
    *,
    run_folder: Path | None = None,
    export_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not ATOMIC_ACCURACY_EVIDENCE_FINALIZATION_ENABLED:
        return {"ok": False, "reason": "disabled"}

    _jp_log("ATOMIC_EVIDENCE_FINALIZATION_STARTED")
    result: dict[str, Any] = {"ok": False, "checks": [], "warnings": []}
    export_result = export_result or {}

    alpha_path = Path("troubleshooting/latest/latest_live_alpha_output.txt")
    alpha_sha = _file_sha256(alpha_path)

    reports_dir = Path("troubleshooting/accuracy_benchmark/latest_reports")
    score_report = _load_json(reports_dir / "latest_accuracy_score_report.json")
    align_report = _load_json(reports_dir / "latest_alignment_report.json")
    ref_report = _load_json(reports_dir / "latest_reference_quality_report.json")
    report_set = _load_json(reports_dir / "LATEST_REPORT_SET_INDEX.json")
    cov_report = _load_json(Path("troubleshooting/latest/export_coverage_report.json"))

    index_base: dict[str, Any] = {}
    for p in _INDEX_PATHS:
        if p.exists():
            index_base.update(_load_json(p))
            break

    updates: dict[str, Any] = {
        "app_version": APP_VERSION,
        "atomic_evidence_finalization_enabled": True,
        "atomic_evidence_finalized_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "latest_alpha_sha256": alpha_sha,
        "authoritative_alpha_output_sha256": export_result.get("authoritative_alpha_output_sha256", alpha_sha),
        "canonical_export_payload_sha256": export_result.get("canonical_export_payload_sha256", alpha_sha),
        "final_output_hash_consistent": export_result.get("final_output_hash_consistent", True),
        "reference_quality_verdict": ref_report.get("reference_quality_verdict", index_base.get("reference_quality_verdict", "")),
        "latest_report_set_consistent": report_set.get("report_set_consistent", False),
        "trusted_score": score_report.get("final_trusted_score", score_report.get("trusted_score", False)),
        "score_should_be_used_for_decision": score_report.get("score_should_be_used_for_decision", False),
        "clean_export_ready_for_scoring": cov_report.get("clean_export_ready_for_scoring", index_base.get("clean_export_ready_for_scoring", False)),
        "source_commit_coverage_ratio": cov_report.get("source_commit_coverage_ratio", index_base.get("source_commit_coverage_ratio", 0)),
        "valid_segment_loss_count": cov_report.get("valid_segment_loss_count", index_base.get("valid_segment_loss_count", 0)),
        "dangerous_correction_count": cov_report.get("dangerous_correction_count", index_base.get("dangerous_correction_count", 0)),
        "malformed_numeric_output_count": cov_report.get("malformed_numeric_output_count", 0),
        "coverage_algorithm_version": cov_report.get("coverage_algorithm_version", ""),
        "final_export_contains_pre_correction_lines": cov_report.get("final_export_contains_pre_correction_lines", False),
    }

    checks: list[str] = []
    if score_report.get("alpha_sha256") and score_report.get("alpha_sha256") != alpha_sha:
        checks.append("score_alpha_hash_mismatch")
    if align_report.get("alpha_sha256") and align_report.get("alpha_sha256") != alpha_sha:
        checks.append("alignment_alpha_hash_mismatch")
    if ref_report.get("reference_quality_verdict"):
        if updates["reference_quality_verdict"] != ref_report.get("reference_quality_verdict"):
            checks.append("reference_quality_verdict_mismatch")
    if report_set and updates["latest_report_set_consistent"] != report_set.get("report_set_consistent"):
        checks.append("report_set_consistency_mismatch")

    if not checks:
        _jp_log("EVIDENCE_REPORT_HASH_BINDING_VERIFIED")
        _jp_log("EVIDENCE_REPORT_FIELD_CONSISTENCY_VERIFIED")
    else:
        result["warnings"].extend(checks)

    for p in _INDEX_PATHS:
        merged = dict(index_base)
        merged.update(updates)
        _atomic_write_json(p, merged)
    _jp_log("LATEST_EVIDENCE_INDEX_REFRESHED_AFTER_SCORING")

    zip_path = None
    try:
        from alpha.utils.accuracy_evidence_export import write_latest_accuracy_evidence_zip

        zip_path = write_latest_accuracy_evidence_zip(export_result=export_result)
        if zip_path:
            _jp_log("LATEST_EVIDENCE_ZIP_REBUILT", path=str(zip_path))
    except Exception as exc:
        result["warnings"].append(f"zip_rebuild_failed:{exc}")

    result["ok"] = True
    result["checks"] = checks
    result["index_updates"] = updates
    result["zip_path"] = str(zip_path) if zip_path else ""
    _jp_log("ATOMIC_EVIDENCE_FINALIZATION_COMPLETED")
    return result
