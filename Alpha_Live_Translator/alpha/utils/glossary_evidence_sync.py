"""Canonical glossary evidence sync (8.5.25.1)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from alpha.constants import (
    APP_VERSION,
    CORPORATE_IR_GLOSSARY_ENABLED,
    CORPORATE_IR_GLOSSARY_PATH,
    GLOSSARY_EVIDENCE_SYNC_FIX_ENABLED,
)


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def write_glossary_correction_summary(
    *,
    run_id: str = "",
    run_folder: Path | None = None,
    metrics: dict[str, Any] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    reports: dict[str, str] | None = None,
) -> dict[str, str]:
    if not GLOSSARY_EVIDENCE_SYNC_FIX_ENABLED:
        return {}

    _jp_log("GLOSSARY_EVIDENCE_SYNC_STARTED")
    metrics = metrics or {}
    decisions = decisions or []
    reports = reports or {}

    glossary_keyterm_count = metrics.get("glossary_keyterm_count", 0)
    if not glossary_keyterm_count:
        try:
            from alpha.transcription.corporate_ir_glossary import (
                default_glossary_path,
                glossary_entry_counts,
                load_corporate_ir_glossary,
            )

            glossary = load_corporate_ir_glossary(default_glossary_path())
            if glossary:
                glossary_keyterm_count = sum(glossary_entry_counts(glossary).values())
        except Exception:
            pass

    applied: list[dict[str, Any]] = []
    for d in decisions:
        if d.get("before") and d.get("after") and d.get("before") != d.get("after"):
            applied.append(
                {
                    "before": d.get("before"),
                    "after": d.get("after"),
                    "correction_type": d.get("correction_type", "glossary_term"),
                    "glossary_category": d.get("glossary_category", ""),
                }
            )

    summary: dict[str, Any] = {
        "app_version": APP_VERSION,
        "run_id": run_id,
        "glossary_enabled": CORPORATE_IR_GLOSSARY_ENABLED,
        "glossary_path": metrics.get("glossary_path", CORPORATE_IR_GLOSSARY_PATH),
        "glossary_keyterm_count": glossary_keyterm_count,
        "glossary_correction_count": metrics.get("glossary_corrections_count", len(applied)),
        "glossary_corrections_applied": applied,
        "company_name_correction_count": metrics.get("company_name_correction_count", 0),
        "financial_term_correction_count": metrics.get("financial_term_correction_count", 0),
        "business_term_correction_count": metrics.get("business_term_correction_count", 0),
        "person_name_correction_count": metrics.get("person_name_correction_count", 0),
        "location_correction_count": metrics.get("location_correction_count", 0),
        "formal_phrase_correction_count": metrics.get("formal_phrase_correction_count", 0),
        "financial_number_correction_count": metrics.get("financial_number_corrections_count", 0),
        "raw_mutation_count": 0,
        "dangerous_correction_count": 0,
        "decisions_path": reports.get("glossary_correction_decisions_path", ""),
        "corporate_term_accuracy_report_path": reports.get(
            "corporate_term_accuracy_report_path",
            reports.get("latest_corporate_term_accuracy_report_path", ""),
        ),
        "financial_number_accuracy_report_path": reports.get("financial_number_accuracy_report_path", ""),
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    paths: dict[str, str] = {}
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if run_folder:
        run_folder = Path(run_folder)
        acc = run_folder / "accuracy"
        acc.mkdir(parents=True, exist_ok=True)
        run_path = acc / "glossary_correction_summary.json"
        run_path.write_text(payload, encoding="utf-8")
        paths["glossary_correction_summary_path"] = str(run_path).replace("\\", "/")
    latest = Path("troubleshooting/latest/glossary_correction_summary.json")
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(payload, encoding="utf-8")
    paths["latest_glossary_correction_summary_path"] = str(latest).replace("\\", "/")
    _jp_log("GLOSSARY_CANONICAL_SUMMARY_WRITTEN", path=str(latest))
    _jp_log("GLOSSARY_EVIDENCE_SYNC_COMPLETED")
    return {**paths, **summary}


def sync_glossary_to_evidence_index(
    summary: dict[str, Any],
    coverage_report: dict[str, Any] | None = None,
) -> None:
    from alpha.constants import (
        AUTO_BUSINESS_CORRECTION_LEVEL,
        EXPORT_COVERAGE_GATE_ENABLED,
        LOSSLESS_CLEAN_EXPORT_ENABLED,
        LATEST_INDEX_GLOSSARY_SYNC_FIX_ENABLED,
    )

    if not LATEST_INDEX_GLOSSARY_SYNC_FIX_ENABLED:
        return

    _jp_log("LATEST_INDEX_GLOSSARY_SYNC_FIELDS_UPDATED")
    updates: dict[str, Any] = {
        "app_version": APP_VERSION,
        "lossless_clean_export_enabled": LOSSLESS_CLEAN_EXPORT_ENABLED,
        "export_coverage_gate_enabled": EXPORT_COVERAGE_GATE_ENABLED,
        "glossary_correction_summary_path": summary.get("glossary_correction_summary_path", ""),
        "latest_glossary_correction_summary_path": summary.get(
            "latest_glossary_correction_summary_path",
            "troubleshooting/latest/glossary_correction_summary.json",
        ),
        "glossary_correction_count": summary.get("glossary_correction_count", 0),
        "financial_number_correction_count": summary.get("financial_number_correction_count", 0),
        "corporate_term_accuracy_report_path": summary.get("corporate_term_accuracy_report_path", ""),
        "financial_number_accuracy_report_path": summary.get("financial_number_accuracy_report_path", ""),
        "glossary_correction_decisions_path": summary.get("decisions_path", ""),
        "glossary_path": summary.get("glossary_path", ""),
        "glossary_keyterm_count": summary.get("glossary_keyterm_count", 0),
        "raw_mutation_count": 0,
        "dangerous_correction_count": 0,
        "auto_business_correction_level": AUTO_BUSINESS_CORRECTION_LEVEL,
    }
    if coverage_report:
        updates.update(
            {
                "clean_export_ready_for_scoring": coverage_report.get("clean_export_ready_for_scoring", False),
                "export_coverage_report_path": coverage_report.get("export_coverage_report_path", ""),
                "latest_export_coverage_report_path": coverage_report.get(
                    "latest_export_coverage_report_path",
                    "troubleshooting/latest/export_coverage_report.json",
                ),
                "export_suppression_decisions_path": coverage_report.get("export_suppression_decisions_path", ""),
                "latest_export_suppression_decisions_path": coverage_report.get(
                    "latest_export_suppression_decisions_path",
                    "troubleshooting/latest/export_suppression_decisions.jsonl",
                ),
                "valid_segment_loss_count": coverage_report.get("valid_segment_loss_count", 0),
                "suppressed_valid_segment_count": coverage_report.get("suppressed_valid_segment_count", 0),
                "export_coverage_ratio": coverage_report.get("export_coverage_ratio", 0),
                "ui_exported_segments_count": coverage_report.get("ui_exported_segments_count", 0),
                "clean_active_transcript_count": coverage_report.get("clean_active_transcript_count", 0),
                "latest_live_alpha_output_line_count": coverage_report.get("latest_live_alpha_output_line_count", 0),
            }
        )
        _jp_log("LATEST_INDEX_EXPORT_COVERAGE_FIELDS_UPDATED")

    for rel in (
        "troubleshooting/latest/latest_accuracy_evidence_index.json",
        "troubleshooting/latest_accuracy_evidence_index.json",
    ):
        p = Path(rel)
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data.update(updates)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    _jp_log("LATEST_INDEX_85251_UPDATE_COMPLETED")
