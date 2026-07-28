"""Synchronize latest analyzer reports into evidence index (8.5.23.4 / 8.5.23.4.1)."""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from alpha.constants import (
    APP_VERSION,
    EVIDENCE_INDEX_SYNC_AFTER_SCORING_ENABLED,
    LATEST_ANALYZER_REPORT_SYNC_ENABLED,
    LATEST_REPORT_SYNC_STRICT_MATCH_ENABLED,
    REPORT_SYNCHRONIZATION_85234_ENABLED,
)
from alpha.utils.reference_alpha_hash import file_sha256, normalize_path, paths_match

RESULTS_DIR = Path("troubleshooting/accuracy_benchmark/results")
LATEST_REPORTS_DIR = Path("troubleshooting/accuracy_benchmark/latest_reports")
LATEST_INDEX_PATHS = (
    Path("troubleshooting/latest/latest_accuracy_evidence_index.json"),
    Path("troubleshooting/latest_accuracy_evidence_index.json"),
)

REPORT_SPECS: dict[str, tuple[str, str]] = {
    "score": ("*_accuracy_score_report.json", "latest_accuracy_score_report.json"),
    "reference_quality": ("*_reference_quality_report.json", "latest_reference_quality_report.json"),
    "alignment_json": ("*_alignment_report.json", "latest_alignment_report.json"),
    "alignment_txt": ("*_alignment_report.txt", "latest_alignment_report.txt"),
    "boundary": ("*_boundary_error_report.json", "latest_boundary_error_report.json"),
    "business": ("*_business_term_risk_report.json", "latest_business_term_risk_report.json"),
    "glossary": ("*_glossary_candidates.json", "latest_glossary_candidates.json"),
    "cleanup": ("*_reference_cleanup_suggestions.txt", "latest_reference_cleanup_suggestions.txt"),
}

INDEX_FIELD_MAP = {
    "score": "latest_score_report_path",
    "reference_quality": "latest_reference_quality_report_path",
    "alignment_json": "latest_alignment_report_json_path",
    "alignment_txt": "latest_alignment_report_txt_path",
    "boundary": "latest_boundary_error_report_path",
    "business": "latest_business_term_risk_report_path",
    "glossary": "latest_glossary_candidates_path",
    "cleanup": "latest_reference_cleanup_suggestions_path",
}


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _parse_stamp(path: Path) -> datetime | None:
    m = re.match(r"^(\d{8})_(\d{6})_", path.name)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)}{m.group(2)}", "%Y%m%d%H%M%S")
    except Exception:
        return None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _latest_run_completed_at() -> datetime | None:
    for candidate in (
        Path("troubleshooting/latest/LATEST_RUN_POINTER.json"),
        Path("troubleshooting/latest/latest_accuracy_evidence_index.json"),
    ):
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            ts = str(data.get("completed_at") or data.get("written_at") or "").strip()
            if ts:
                return datetime.fromisoformat(ts)
        except Exception:
            pass
    return None


def _report_matches(
    report: dict[str, Any],
    *,
    alpha_path: str,
    reference_path: str,
    run_completed_at: datetime | None,
) -> tuple[bool, str]:
    if alpha_path:
        rep_alpha = str(report.get("alpha_path", ""))
        if rep_alpha and not paths_match(rep_alpha, alpha_path):
            return False, "alpha_path_mismatch"
    if reference_path:
        rep_ref = str(report.get("reference_path", ""))
        if rep_ref and not paths_match(rep_ref, reference_path):
            return False, "reference_path_mismatch"
    stamp = report.get("generated_at") or report.get("scored_at") or report.get("checked_at")
    if run_completed_at and stamp:
        try:
            rep_dt = datetime.fromisoformat(str(stamp).replace("Z", ""))
            if rep_dt < run_completed_at:
                return False, "before_run_completion"
        except Exception:
            pass
    return True, ""


def _select_best_report(
    pattern: str,
    *,
    alpha_path: str,
    reference_path: str,
    run_completed_at: datetime | None,
) -> tuple[Path | None, str]:
    if not RESULTS_DIR.exists():
        return None, "results_dir_missing"
    candidates = sorted(RESULTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        _jp_log("LATEST_ANALYZER_REPORT_CANDIDATE_FOUND", path=str(path))
        if path.suffix == ".json":
            report = _load_json(path)
            ok, reason = _report_matches(
                report,
                alpha_path=alpha_path,
                reference_path=reference_path,
                run_completed_at=run_completed_at,
            )
            if not ok:
                _jp_log("LATEST_ANALYZER_REPORT_REJECTED_PATH_MISMATCH", path=str(path), reason=reason)
                continue
        else:
            stamp = _parse_stamp(path)
            if run_completed_at and stamp and stamp < run_completed_at:
                _jp_log("LATEST_ANALYZER_REPORT_REJECTED_STALE", path=str(path))
                continue
        _jp_log("LATEST_ANALYZER_REPORT_SELECTED", path=str(path))
        return path, ""
    return None, "no_candidate"


def _score_index_fields(score_path: Path | None) -> dict[str, Any]:
    if not score_path or not score_path.exists():
        return {}
    report = _load_json(score_path)
    fields = {
        "final_trusted_score": report.get("final_trusted_score", report.get("trusted_score")),
        "score_should_be_used_for_decision": report.get("score_should_be_used_for_decision"),
        "cer_score": report.get("cer_score"),
        "normalized_cer_score": report.get("normalized_cer_score"),
        "score_blockers": report.get("score_blockers", []),
        "score_trust_reason": report.get("score_trust_reason", ""),
        "raw_mutation_count": report.get("raw_mutation_count", 0),
        "dangerous_correction_count": report.get("dangerous_correction_count", 0),
        "auto_business_correction_level": report.get("auto_business_correction_level", ""),
        "alignment_algorithm_version": report.get("alignment_algorithm_version", ""),
        "unaligned_alpha_char_ratio": report.get("unaligned_alpha_char_ratio"),
        "unaligned_reference_char_ratio": report.get("unaligned_reference_char_ratio"),
        "average_window_overlap_score": report.get("average_window_overlap_score"),
        "alignment_integrity_verdict_v2": report.get("alignment_integrity_verdict_v2", ""),
        "alignment_coverage_verdict_v2": report.get("alignment_coverage_verdict_v2", ""),
        "line_count_mismatch_tolerated": report.get("line_count_mismatch_tolerated"),
        "char_coverage_used_for_trust": report.get("char_coverage_used_for_trust"),
        "glossary_correction_count": report.get("glossary_correction_count", 0),
        "financial_number_correction_count": report.get("financial_number_correction_count", 0),
        "glossary_correction_summary_path": report.get("glossary_correction_summary_path", ""),
        "clean_export_ready_for_scoring": report.get("clean_export_ready_for_scoring"),
        "valid_segment_loss_count": report.get("valid_segment_loss_count", 0),
        "export_coverage_ratio": report.get("export_coverage_ratio"),
        "export_coverage_report_path": report.get("export_coverage_report_path", ""),
    }
    _jp_log("EVIDENCE_INDEX_SCORE_FIELDS_UPDATED")
    return fields


def _alignment_v2_index_fields(align_path: Path | None) -> dict[str, Any]:
    if not align_path or not align_path.exists():
        return {}
    report = _load_json(align_path)
    if not report.get("alignment_algorithm_version", "").startswith("v2"):
        return {}
    fields = {
        "alignment_integrity_verdict_v2": report.get("alignment_integrity_verdict_v2", ""),
        "alignment_coverage_verdict_v2": report.get("alignment_coverage_verdict_v2", ""),
        "unaligned_alpha_char_ratio": report.get("unaligned_alpha_char_ratio"),
        "unaligned_reference_char_ratio": report.get("unaligned_reference_char_ratio"),
        "average_window_overlap_score": report.get("average_window_overlap_score"),
        "matched_window_count": report.get("matched_window_count"),
        "alignment_order_violations": report.get("alignment_order_violations"),
        "line_count_mismatch_tolerated": report.get("line_count_mismatch_tolerated"),
        "paragraph_reference_detected": report.get("paragraph_reference_detected"),
        "short_alpha_line_fragmentation_detected": report.get("short_alpha_line_fragmentation_detected"),
    }
    _jp_log("EVIDENCE_INDEX_ALIGNMENT_V2_FIELDS_UPDATED")
    return fields


def _copy_latest_reports(selected: dict[str, Path], meta: dict[str, Any]) -> None:
    _jp_log("LATEST_REPORTS_FOLDER_UPDATE_STARTED")
    LATEST_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_paths: dict[str, str] = {}
    report_hashes: dict[str, str] = {}
    missing: list[str] = []
    for key, (_, dst_name) in REPORT_SPECS.items():
        src = selected.get(key)
        dst = LATEST_REPORTS_DIR / dst_name
        if src is not None and src.exists():
            shutil.copy2(src, dst)
            report_paths[key] = str(dst).replace("\\", "/")
            report_hashes[key] = file_sha256(str(dst))
            _jp_log("LATEST_REPORT_FILE_COPIED", key=key, path=str(dst))
        else:
            missing.append(dst_name)
            _jp_log("LATEST_REPORT_FILE_MISSING", key=key, name=dst_name)
    index = {
        "app_version": APP_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **meta,
        "report_paths": report_paths,
        "report_hashes": report_hashes,
        "missing_reports": missing,
    }
    (LATEST_REPORTS_DIR / "LATEST_REPORT_SET_INDEX.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _jp_log("LATEST_REPORT_SET_INDEX_WRITTEN", path=str(LATEST_REPORTS_DIR / "LATEST_REPORT_SET_INDEX.json"))
    _jp_log("LATEST_REPORTS_FOLDER_UPDATE_COMPLETED", missing=missing)


def _timestamp_warnings(selected: dict[str, Path]) -> list[str]:
    warnings: list[str] = []
    stamps: list[datetime] = []
    for path in selected.values():
        dt = _parse_stamp(path)
        if dt:
            stamps.append(dt)
    if len(stamps) >= 2:
        span = max(stamps) - min(stamps)
        if span.total_seconds() > 300:
            warnings.append("report_timestamps_differ_by_more_than_5_minutes")
    refs = set()
    for path in selected.values():
        if path.suffix != ".json":
            continue
        ref = str(_load_json(path).get("reference_path", ""))
        if ref:
            refs.add(normalize_path(ref))
    if len(refs) > 1:
        warnings.append("mixed_reference_paths_in_report_set")
    return warnings


def sync_latest_accuracy_reports(
    run_id: str | None = None,
    alpha_path: str | None = None,
    reference_path: str | None = None,
) -> dict[str, Any]:
    if not (REPORT_SYNCHRONIZATION_85234_ENABLED and LATEST_ANALYZER_REPORT_SYNC_ENABLED):
        return {"ok": False, "reason": "sync_disabled"}
    if EVIDENCE_INDEX_SYNC_AFTER_SCORING_ENABLED:
        _jp_log("EVIDENCE_INDEX_SYNC_AFTER_SCORING_STARTED")
    _jp_log("LATEST_ANALYZER_REPORT_SYNC_STARTED", run_id=run_id or "")
    alpha = alpha_path or str(Path("troubleshooting/latest/latest_live_alpha_output.txt"))
    reference = reference_path or ""
    run_completed_at = _latest_run_completed_at()
    selected: dict[str, Path] = {}
    rejections: list[str] = []
    for key, (pattern, _) in REPORT_SPECS.items():
        path, reason = _select_best_report(
            pattern,
            alpha_path=alpha,
            reference_path=reference,
            run_completed_at=run_completed_at,
        )
        if path is not None:
            selected[key] = path
        elif reason:
            rejections.append(f"{key}:{reason}")
    warnings = _timestamp_warnings(selected)
    missing_keys = [k for k in REPORT_SPECS if k not in selected]
    if missing_keys:
        warnings.append(f"missing_reports:{','.join(missing_keys)}")
    refs = set()
    for path in selected.values():
        if path.suffix == ".json":
            ref = str(_load_json(path).get("reference_path", ""))
            if ref:
                refs.add(ref)
    if reference:
        refs.add(reference)
    report_set_reference = next(iter(refs), "") if len(refs) == 1 else (reference or "")
    consistent = len(missing_keys) == 0 and "mixed_reference_paths_in_report_set" not in warnings
    if LATEST_REPORT_SYNC_STRICT_MATCH_ENABLED and not selected:
        consistent = False
        warnings.append("no_matching_reports_found")
    if consistent:
        _jp_log("LATEST_ANALYZER_REPORT_SET_CONSISTENT")
    else:
        _jp_log("LATEST_ANALYZER_REPORT_SET_INCONSISTENT_WARNING", warnings=warnings, missing=missing_keys)
    stamps = [_parse_stamp(p) for p in selected.values()]
    stamps = [s for s in stamps if s]
    report_set_timestamp = max(stamps).strftime("%Y%m%d_%H%M%S") if stamps else ""
    score_report = _load_json(selected["score"]) if "score" in selected else {}
    align_report = _load_json(selected["alignment_json"]) if "alignment_json" in selected else {}
    meta = {
        "alpha_path": alpha,
        "alpha_sha256": file_sha256(alpha),
        "reference_path": report_set_reference,
        "reference_sha256": file_sha256(report_set_reference) if report_set_reference else "",
        "report_set_timestamp": report_set_timestamp,
        "report_set_consistent": consistent,
        "warnings": warnings,
        "alignment_algorithm_version": align_report.get("alignment_algorithm_version", ""),
        "alignment_integrity_verdict_v2": align_report.get("alignment_integrity_verdict_v2", ""),
        "final_trusted_score": score_report.get("final_trusted_score", score_report.get("trusted_score")),
        "score_should_be_used_for_decision": score_report.get("score_should_be_used_for_decision"),
    }
    if selected:
        _copy_latest_reports(selected, meta)
    index_updates: dict[str, Any] = {
        "app_version": APP_VERSION,
        "latest_report_sync_enabled": True,
        "latest_report_sync_status": "completed" if selected else "no_reports",
        "latest_report_sync_completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "latest_report_set_timestamp": report_set_timestamp,
        "latest_report_set_reference_path": report_set_reference,
        "latest_report_set_alpha_path": alpha,
        "latest_report_set_consistent": consistent,
        "latest_report_set_warnings": warnings,
        "latest_alpha_sha256": file_sha256(alpha),
        "latest_reference_sha256": file_sha256(report_set_reference) if report_set_reference else "",
        "business_cer_benchmark_integrity_enabled": True,
        "report_synchronization_85234_enabled": True,
        "cer_trust_requires_alignment_coverage": True,
        "reference_alpha_hash_binding_enabled": True,
        "alignment_coverage_repair_852341_enabled": True,
        "evidence_index_sync_after_scoring_enabled": True,
        "business_18min_test_protocol_path": "troubleshooting/accuracy_benchmark/BUSINESS_18MIN_CER_TEST_PROTOCOL.md",
    }
    for key, field in INDEX_FIELD_MAP.items():
        path = selected.get(key)
        index_updates[field] = str(path).replace("\\", "/") if path else ""
    index_updates.update(_score_index_fields(selected.get("score")))
    align_fields = _alignment_v2_index_fields(selected.get("alignment_json"))
    for k, v in align_fields.items():
        if v is not None and v != "":
            index_updates[k] = v
    for index_path in LATEST_INDEX_PATHS:
        if not index_path.exists():
            continue
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index.update(index_updates)
            if run_id:
                index["run_id"] = run_id
            index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    _jp_log("EVIDENCE_INDEX_REPORT_PATHS_REFRESHED")
    _jp_log("LATEST_ACCURACY_INDEX_REPORT_PATHS_UPDATED")
    _jp_log("LATEST_ANALYZER_REPORT_SYNC_COMPLETED", consistent=consistent)
    if EVIDENCE_INDEX_SYNC_AFTER_SCORING_ENABLED:
        _jp_log("EVIDENCE_INDEX_SYNC_AFTER_SCORING_COMPLETED", consistent=consistent)
    return {
        "ok": True,
        "consistent": consistent,
        "selected": {k: str(v) for k, v in selected.items()},
        "warnings": warnings,
        "rejections": rejections,
        "missing_keys": missing_keys,
        "index_updates": index_updates,
    }
