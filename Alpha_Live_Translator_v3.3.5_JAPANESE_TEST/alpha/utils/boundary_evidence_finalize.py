"""Finalize boundary stabilizer evidence files on Stop (8.5.24.2 + 8.5.25.1)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    APP_VERSION,
    BOUNDARY_DECISIONS_FINALIZATION_ENABLED,
    BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ENABLED,
    CANONICAL_TRANSCRIPT_LINEAGE_ENABLED,
    FINAL_CLEAN_TRANSCRIPT_PERSISTENCE_ENABLED,
    LINEAGE_BASED_EXPORT_COVERAGE_ENABLED,
    LOSSLESS_CLEAN_EXPORT_ENABLED,
    STABLE_REVISION_HISTORY_PERSISTENCE_ENABLED,
    TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY,
)


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _resolve_run_folder(run_folder: Path | None = None) -> Path | None:
    if run_folder and run_folder.exists():
        return run_folder
    try:
        from alpha.utils.troubleshooting_paths import get_run_folder

        run = get_run_folder()
        if run:
            return Path(run)
    except Exception:
        pass
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if ident and ident.run_folder:
            return Path(ident.run_folder)
    except Exception:
        pass
    return None


def _mirror_latest(src: Path, name: str) -> Path:
    latest = Path("troubleshooting/latest") / name
    latest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, latest)
    return latest


def finalize_boundary_decisions(run_folder: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    if not BOUNDARY_DECISIONS_FINALIZATION_ENABLED:
        return paths
    _jp_log("BOUNDARY_DECISIONS_FINALIZATION_STARTED")
    final_path = run_folder / "accuracy" / "boundary_stabilizer_decisions.jsonl"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    pending = Path("troubleshooting/runs/_pending/accuracy/boundary_stabilizer_decisions.jsonl")
    if pending.exists():
        shutil.copy2(pending, final_path)
        _jp_log("BOUNDARY_DECISIONS_COPIED_FROM_PENDING", src=str(pending))
    elif not final_path.exists():
        final_path.write_text("", encoding="utf-8")
    paths["boundary_decisions_path"] = str(final_path).replace("\\", "/")
    latest = _mirror_latest(final_path, "boundary_stabilizer_decisions.jsonl")
    paths["latest_boundary_stabilizer_decisions_path"] = str(latest).replace("\\", "/")
    _jp_log("BOUNDARY_DECISIONS_FINAL_PATH_WRITTEN", path=str(final_path))
    _jp_log("BOUNDARY_DECISIONS_LATEST_COPY_WRITTEN", path=str(latest))
    _jp_log("BOUNDARY_DECISIONS_FINALIZATION_COMPLETED")
    return paths


def finalize_boundary_evidence_on_stop(run_folder: Path | None = None) -> dict[str, Any]:
    """Sweep clean export, persist evidence files, update summary."""
    result: dict[str, Any] = {"ok": False}
    if not BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ENABLED:
        return result

    run = _resolve_run_folder(run_folder)
    if not run:
        _jp_log("BOUNDARY_EVIDENCE_FINALIZE_SKIPPED_NO_RUN_FOLDER")
        return result

    run_id = ""
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if ident:
            run_id = ident.run_id or ""
    except Exception:
        pass

    cleanup_metrics: dict[str, Any] = {}
    glossary_result: dict[str, Any] = {"lines": [], "metrics": {}, "reports": {}, "decisions": []}
    coverage_report: dict[str, Any] = {}
    try:
        from alpha.transcription.stable_line_revision import get_stable_line_revision_manager

        mgr = get_stable_line_revision_manager()
        raw_lines = [
            f"{('[Speaker ' + str(r.get('speaker')) + '] ' if r.get('speaker') is not None else '')}{r.get('text', '')}"
            for r in mgr.get_active_lines()
        ]
        if not raw_lines:
            from alpha.utils.transcript_snapshot_store import format_alpha_output_text

            snap = format_alpha_output_text(active_only=True)
            raw_lines = [ln.strip() for ln in snap.splitlines() if ln.strip()]

        if LOSSLESS_CLEAN_EXPORT_ENABLED:
            from alpha.utils.clean_export_coverage import (
                analyze_export_coverage,
                build_lossless_export_lines,
                collect_candidate_segments,
                conservative_sweep_residual_duplicates,
                write_export_coverage_report,
                write_suppression_decisions,
            )

            cleaned_lines, sweep_metrics, sweep_decisions = conservative_sweep_residual_duplicates(
                raw_lines, run_id=run_id
            )
            cleanup_metrics.update(sweep_metrics)
        else:
            from alpha.transcription.final_output_cleanup import sweep_residual_duplicates

            cleaned_lines, cleanup_metrics = sweep_residual_duplicates(raw_lines)

        glossary_result = {"lines": cleaned_lines, "metrics": {}, "reports": {}, "decisions": []}
        try:
            from alpha.transcription.corporate_ir_stable_corrector import apply_corporate_ir_stable_corrections

            glossary_result = apply_corporate_ir_stable_corrections(cleaned_lines, run_folder=run)
            cleaned_lines = glossary_result.get("lines", cleaned_lines)
            cleanup_metrics.update(glossary_result.get("metrics", {}))
        except Exception as exc:
            cleanup_metrics["glossary_finalize_error"] = str(exc)

        canonical_records: list[dict[str, Any]] = []
        if CANONICAL_TRANSCRIPT_LINEAGE_ENABLED:
            from alpha.transcription.transcript_lineage import finalize_canonical_export
            from alpha.utils.clean_export_coverage import write_export_coverage_report, write_suppression_decisions

            canon = finalize_canonical_export(
                cleaned_lines,
                run_id=run_id,
                run_folder=run,
                glossary_decisions=glossary_result.get("decisions", []),
                financial_safety_metrics=glossary_result.get("metrics", {}),
            )
            cleaned_lines = canon.get("export_lines", cleaned_lines)
            canonical_records = canon.get("canonical_records", [])
            coverage_report = canon.get("coverage_report", {})
            enriched_decisions = canon.get("enriched_decisions", [])
            if enriched_decisions and run:
                dec_path = run / "accuracy" / "glossary_correction_decisions.jsonl"
                dec_path.parent.mkdir(parents=True, exist_ok=True)
                dec_path.write_text(
                    "\n".join(json.dumps(d, ensure_ascii=False) for d in enriched_decisions)
                    + ("\n" if enriched_decisions else ""),
                    encoding="utf-8",
                )
                latest_dec = Path("troubleshooting/latest/glossary_correction_decisions.jsonl")
                latest_dec.parent.mkdir(parents=True, exist_ok=True)
                latest_dec.write_text(dec_path.read_text(encoding="utf-8"), encoding="utf-8")
            pre_report = canon.get("pre_correction_report", {})
            cleanup_metrics.update(coverage_report)
            cleanup_metrics.update(pre_report)
            cleanup_metrics.update(canon.get("ledger_paths", {}))
            cleanup_metrics["canonical_export_line_count"] = len(cleaned_lines)
            coverage_report.update(write_export_coverage_report(coverage_report, run_folder=run))
            coverage_report.update(write_suppression_decisions(sweep_decisions, run_folder=run))
            cleanup_metrics["canonical_records"] = canonical_records
            _jp_log("LOSSLESS_ALPHA_EXPORT_WRITTEN", lines=len(cleaned_lines))
        elif LOSSLESS_CLEAN_EXPORT_ENABLED:
            from alpha.utils.clean_export_coverage import (
                analyze_export_coverage,
                build_lossless_export_lines,
                collect_candidate_segments,
                write_export_coverage_report,
                write_suppression_decisions,
            )

            candidates = collect_candidate_segments(
                ui_exported_path=run / "transcripts" / "ui_exported_segments.jsonl",
                stable_commits_path=run / "transcripts" / "stable_commits.jsonl",
            )
            if not TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY:
                cleaned_lines, recovery_decisions = build_lossless_export_lines(cleaned_lines, candidates)
            else:
                recovery_decisions = []
            coverage_report = analyze_export_coverage(
                run_id=run_id,
                run_folder=run,
                export_lines=cleaned_lines,
                ui_exported_path=run / "transcripts" / "ui_exported_segments.jsonl",
                stable_commits_path=run / "transcripts" / "stable_commits.jsonl",
                glossary_decisions_path=run / "accuracy" / "glossary_correction_decisions.jsonl",
            )
            coverage_report.update(write_export_coverage_report(coverage_report, run_folder=run))
            coverage_report.update(
                write_suppression_decisions(sweep_decisions + recovery_decisions, run_folder=run)
            )
            cleanup_metrics.update(coverage_report)
            _jp_log("LOSSLESS_ALPHA_EXPORT_WRITTEN", lines=len(cleaned_lines))

        mgr.apply_final_clean_lines(
            cleaned_lines,
            cleanup_metrics=cleanup_metrics,
            canonical_records=canonical_records if CANONICAL_TRANSCRIPT_LINEAGE_ENABLED else None,
        )
    except Exception as exc:
        cleanup_metrics["finalize_error"] = str(exc)

    path_info: dict[str, str] = {}
    try:
        from alpha.transcription.stable_line_revision import get_stable_line_revision_manager

        path_info = get_stable_line_revision_manager().finalize_on_stop(run)
    except Exception:
        pass

    path_info.update(finalize_boundary_decisions(run))
    path_info.update(glossary_result.get("reports", {}))

    glossary_summary: dict[str, Any] = {}
    try:
        from alpha.utils.glossary_evidence_sync import (
            sync_glossary_to_evidence_index,
            write_glossary_correction_summary,
        )

        glossary_summary = write_glossary_correction_summary(
            run_id=run_id,
            run_folder=run,
            metrics={**cleanup_metrics, **glossary_result.get("metrics", {})},
            decisions=glossary_result.get("decisions", []),
            reports=glossary_result.get("reports", {}),
        )
        sync_glossary_to_evidence_index(glossary_summary, coverage_report)
        cleanup_metrics["glossary_correction_count"] = glossary_summary.get("glossary_correction_count", 0)
        _jp_log("GLOSSARY_EVIDENCE_SYNC_INDEX_UPDATED")
    except Exception as exc:
        cleanup_metrics["glossary_sync_error"] = str(exc)

    try:
        from alpha.transcription.japanese_boundary_stabilizer import get_boundary_stabilizer

        summary_path = get_boundary_stabilizer().write_summary(
            extra_fields={
                **cleanup_metrics,
                **path_info,
                **glossary_result.get("metrics", {}),
                **glossary_summary,
            }
        )
        path_info["boundary_stabilizer_summary_path"] = str(summary_path).replace("\\", "/")
    except Exception:
        pass

    _update_evidence_index_85252(
        {
            **cleanup_metrics,
            **path_info,
            **glossary_result.get("metrics", {}),
            **glossary_summary,
            "run_folder": str(run),
        }
    )
    result.update({"ok": True, **cleanup_metrics, **path_info, **coverage_report})
    return result


def _update_evidence_index_85252(fields: dict[str, Any]) -> None:
    from alpha.constants import (
        AUTO_BUSINESS_CORRECTION_LEVEL,
        CANONICAL_TRANSCRIPT_LINEAGE_ENABLED,
        CORPORATE_IR_GLOSSARY_ENABLED,
        CORPORATE_IR_GLOSSARY_PATH,
        EXPORT_COVERAGE_GATE_ENABLED,
        FINAL_EXPORT_LOCK_ENABLED,
        GLOSSARY_KEYTERM_BOOST_ENABLED,
        FINANCIAL_NUMBER_AUDIT_ENABLED,
        LOSSLESS_CLEAN_EXPORT_ENABLED,
        STABLE_GLOSSARY_CORRECTION_ENABLED,
    )

    _jp_log("LATEST_ACCURACY_INDEX_85242_UPDATE_STARTED")
    updates = {
        "app_version": APP_VERSION,
        "canonical_transcript_lineage_enabled": CANONICAL_TRANSCRIPT_LINEAGE_ENABLED,
        "final_export_lock_enabled": FINAL_EXPORT_LOCK_ENABLED,
        "lossless_clean_export_enabled": LOSSLESS_CLEAN_EXPORT_ENABLED,
        "export_coverage_gate_enabled": EXPORT_COVERAGE_GATE_ENABLED,
        "residual_duplicate_cleanup_enabled": True,
        "punctuation_artifact_cleanup_enabled": True,
        "cumulative_alpha_rejection_enabled": True,
        "residual_duplicate_before_count": fields.get("residual_duplicate_before_count", 0),
        "residual_duplicate_after_count": fields.get("residual_duplicate_after_count", 0),
        "residual_duplicate_suppressed_count": fields.get("residual_duplicate_suppressed_count", 0),
        "punctuation_artifact_before_count": fields.get("punctuation_artifact_before_count", 0),
        "punctuation_artifact_after_count": fields.get("punctuation_artifact_after_count", 0),
        "punctuation_artifact_cleaned_count": fields.get("punctuation_artifact_cleaned_count", 0),
        "clean_alpha_export_line_count": fields.get("output_clean_line_count", fields.get("clean_active_line_count", 0)),
        "alpha_output_cumulative_duplicate_suspected": fields.get("residual_duplicate_after_count", 0) > 0,
        "clean_active_transcript_path": fields.get("clean_active_transcript_path", ""),
        "latest_clean_active_transcript_path": fields.get("latest_clean_active_transcript_path", ""),
        "stable_revision_history_path": fields.get("stable_revision_history_path", ""),
        "latest_stable_revision_history_path": fields.get("latest_stable_revision_history_path", ""),
        "boundary_stabilizer_decisions_path": fields.get("boundary_decisions_path", ""),
        "latest_boundary_stabilizer_decisions_path": fields.get("latest_boundary_stabilizer_decisions_path", ""),
        "boundary_stabilizer_summary_path": fields.get("boundary_stabilizer_summary_path", ""),
        "latest_boundary_stabilizer_summary_path": "troubleshooting/latest/boundary_stabilizer_summary.json",
        "corporate_ir_glossary_enabled": CORPORATE_IR_GLOSSARY_ENABLED,
        "glossary_path": fields.get("glossary_path", CORPORATE_IR_GLOSSARY_PATH),
        "glossary_keyterm_boost_enabled": GLOSSARY_KEYTERM_BOOST_ENABLED,
        "glossary_keyterm_count": fields.get("glossary_keyterm_count", 0),
        "stable_glossary_correction_enabled": STABLE_GLOSSARY_CORRECTION_ENABLED,
        "glossary_correction_count": fields.get(
            "glossary_correction_count", fields.get("glossary_corrections_count", 0)
        ),
        "glossary_correction_summary_path": fields.get("glossary_correction_summary_path", ""),
        "latest_glossary_correction_summary_path": fields.get(
            "latest_glossary_correction_summary_path",
            "troubleshooting/latest/glossary_correction_summary.json",
        ),
        "glossary_correction_decisions_path": fields.get("glossary_correction_decisions_path", ""),
        "financial_number_audit_enabled": FINANCIAL_NUMBER_AUDIT_ENABLED,
        "financial_number_correction_count": fields.get(
            "financial_number_correction_count", fields.get("financial_number_corrections_count", 0)
        ),
        "financial_number_accuracy_report_path": fields.get("financial_number_accuracy_report_path", ""),
        "corporate_term_accuracy_report_path": fields.get(
            "corporate_term_accuracy_report_path",
            fields.get("latest_corporate_term_accuracy_report_path", ""),
        ),
        "company_name_correction_count": fields.get("company_name_correction_count", 0),
        "financial_term_correction_count": fields.get("financial_term_correction_count", 0),
        "business_term_correction_count": fields.get("business_term_correction_count", 0),
        "person_name_correction_count": fields.get("person_name_correction_count", 0),
        "location_correction_count": fields.get("location_correction_count", 0),
        "formal_phrase_correction_count": fields.get("formal_phrase_correction_count", 0),
        "clean_export_ready_for_scoring": fields.get("clean_export_ready_for_scoring", False),
        "export_coverage_report_path": fields.get("export_coverage_report_path", ""),
        "latest_export_coverage_report_path": fields.get(
            "latest_export_coverage_report_path",
            "troubleshooting/latest/export_coverage_report.json",
        ),
        "export_suppression_decisions_path": fields.get("export_suppression_decisions_path", ""),
        "latest_export_suppression_decisions_path": fields.get(
            "latest_export_suppression_decisions_path",
            "troubleshooting/latest/export_suppression_decisions.jsonl",
        ),
        "valid_segment_loss_count": fields.get("valid_segment_loss_count", 0),
        "suppressed_valid_segment_count": fields.get("suppressed_valid_segment_count", 0),
        "export_coverage_ratio": fields.get("export_coverage_ratio", 0),
        "ui_exported_segments_count": fields.get("ui_exported_segments_count", 0),
        "clean_active_transcript_count": fields.get("clean_active_transcript_count", 0),
        "latest_live_alpha_output_line_count": fields.get("latest_live_alpha_output_line_count", 0),
        "canonical_transcript_ledger_path": fields.get("canonical_transcript_ledger_path", ""),
        "latest_canonical_transcript_ledger_path": fields.get(
            "latest_canonical_transcript_ledger_path",
            "troubleshooting/latest/canonical_transcript_ledger.jsonl",
        ),
        "pre_correction_reentry_report_path": fields.get("pre_correction_reentry_report_path", ""),
        "latest_pre_correction_reentry_report_path": fields.get(
            "latest_pre_correction_reentry_report_path",
            "troubleshooting/latest/pre_correction_reentry_report.json",
        ),
        "source_commit_total_count": fields.get("source_commit_total_count", 0),
        "source_commit_represented_count": fields.get("source_commit_represented_count", 0),
        "source_commit_missing_count": fields.get("source_commit_missing_count", 0),
        "source_commit_coverage_ratio": fields.get("source_commit_coverage_ratio", 0),
        "source_commit_observed_count": fields.get("source_commit_observed_count", 0),
        "source_commit_intentionally_suppressed_count": fields.get(
            "source_commit_intentionally_suppressed_count", 0
        ),
        "source_commit_required_count": fields.get("source_commit_required_count", 0),
        "source_commit_represented_required_count": fields.get(
            "source_commit_represented_required_count", 0
        ),
        "source_commit_missing_required_count": fields.get(
            "source_commit_missing_required_count", 0
        ),
        "coverage_algorithm_version": fields.get("coverage_algorithm_version", ""),
        "financial_number_correction_attempt_count": fields.get(
            "financial_number_correction_attempt_count", 0
        ),
        "financial_number_correction_applied_count": fields.get(
            "financial_number_correction_applied_count", 0
        ),
        "financial_number_correction_blocked_count": fields.get(
            "financial_number_correction_blocked_count", 0
        ),
        "malformed_numeric_output_count": fields.get("malformed_numeric_output_count", 0),
        "dangerous_correction_blocked_count": fields.get("dangerous_correction_blocked_count", 0),
        "canonical_export_payload_sha256": fields.get("canonical_export_payload_sha256", ""),
        "authoritative_alpha_output_sha256": fields.get("authoritative_alpha_output_sha256", ""),
        "final_output_hash_consistent": fields.get("final_output_hash_consistent", False),
        "suppression_aware_lineage_coverage_enabled": True,
        "lineage_coverage_ratio": fields.get("lineage_coverage_ratio", 0),
        "represented_by_corrected_line_count": fields.get("represented_by_corrected_line_count", 0),
        "pre_correction_reentry_blocked_count": fields.get("pre_correction_reentry_blocked_count", 0),
        "final_export_contains_pre_correction_lines": fields.get(
            "final_export_contains_pre_correction_lines", False
        ),
        "canonical_export_line_count": fields.get("canonical_export_line_count", 0),
        "cumulative_duplicate_count": fields.get("cumulative_duplicate_count", 0),
        "punctuation_artifact_count": fields.get("punctuation_artifact_count", 0),
        "auto_business_correction_level": AUTO_BUSINESS_CORRECTION_LEVEL,
        "raw_mutation_count": 0,
        "dangerous_correction_count": fields.get("dangerous_correction_count", 0),
    }
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
    _jp_log("LATEST_INDEX_CANONICAL_LINEAGE_FIELDS_UPDATED")
    _jp_log("LATEST_INDEX_FINAL_EXPORT_LOCK_FIELDS_UPDATED")
    _jp_log("LATEST_INDEX_PRE_CORRECTION_REENTRY_FIELDS_UPDATED")
    _jp_log("LATEST_INDEX_85252_UPDATE_COMPLETED")


_update_evidence_index_85251 = _update_evidence_index_85252
_update_evidence_index_8525 = _update_evidence_index_85252
