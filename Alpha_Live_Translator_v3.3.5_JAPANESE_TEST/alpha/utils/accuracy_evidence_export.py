"""Auto-export Alpha transcript and accuracy evidence index (V3.3.5.5.8.5.22)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    ACCURACY_EVIDENCE_MODE_ENABLED,
    ANTI_OVERFIT_MODE_ENABLED,
    APP_CODENAME,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    AUTO_EXPORT_ALPHA_TXT_ENABLED,
    AUTO_EXPORT_ALPHA_TXT_ON_STOP,
    BENCHMARK_BASELINE_LOCK_ENABLED,
    CER_REFERENCE_VALIDATION_ENABLED,
    CORRECTION_RULE_APPROVAL_REQUIRED,
    BUSINESS_TERM_RISK_REPORT_ENABLED,
    BUSINESS_CER_BENCHMARK_INTEGRITY_ENABLED,
    CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE,
    EVIDENCE_PROTECTION_85232_ENABLED,
    GLOSSARY_CANDIDATE_REPORT_ENABLED,
    JAPANESE_BOUNDARY_DIAGNOSIS_ENABLED,
    LATEST_ACCURACY_ZIP_FLUSH_FIX_ENABLED,
    LATEST_ANALYZER_REPORT_SYNC_ENABLED,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    REFERENCE_ALPHA_HASH_BINDING_ENABLED,
    REPORT_SYNCHRONIZATION_85234_ENABLED,
    REAL_LIVE_ALPHA_PROTECTION_ENABLED,
    REFERENCE_ALIGNMENT_DIAGNOSIS_ENABLED,
    REFERENCE_CLEANUP_SUGGESTIONS_ENABLED,
    REFERENCE_TRANSCRIPT_QUALITY_CHECK_ENABLED,
    SINGLE_SAMPLE_CORRECTION_BLOCKED,
    SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED,
    TEMP_AUDIO_RETENTION_HOURS,
    VISIBLE_ERROR_AUDIT_EXPANDED,
)


def _get_run_identity() -> tuple[str, str, Optional[Path]]:
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if ident is not None:
            folder = Path(ident.run_folder) if ident.run_folder else None
            return ident.run_id, ident.run_timestamp, folder
    except Exception:
        pass
    return "", "", None


def _read_alpha_text(host: Any = None) -> str:
    try:
        from alpha.constants import CLEAN_ALPHA_EXPORT_ENABLED
        from alpha.transcription.stable_line_revision import get_stable_line_revision_manager

        if CLEAN_ALPHA_EXPORT_ENABLED:
            clean = get_stable_line_revision_manager().format_clean_alpha_text()
            if clean.strip():
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log("CLEAN_ALPHA_EXPORT_WRITTEN", source="stable_line_revision")
                except Exception:
                    pass
                return clean
    except Exception:
        pass
    try:
        from alpha.utils.run_artifacts import get_transcript_text_from_snapshot

        text = get_transcript_text_from_snapshot()
        if text.strip():
            return text
    except Exception:
        pass
    try:
        from alpha.utils.transcript_snapshot_store import format_alpha_output_text

        text = format_alpha_output_text(active_only=True)
        if text.strip():
            return text
    except Exception:
        pass
    try:
        from alpha.utils.run_artifacts import get_transcript_text_from_host

        return get_transcript_text_from_host(host, allow_ui_export=False)
    except Exception:
        return ""


def _accuracy_header(*, run_id: str, run_timestamp: str, audio_ref: str = "") -> str:
    lines = [
        "# Alpha Live Translator Accuracy Export",
        f"# app_version: {APP_VERSION}",
        f"# run_id: {run_id or 'unknown'}",
        f"# run_timestamp: {run_timestamp or 'unknown'}",
        "# language: ja",
        "# source: stable_alpha_output",
        "# raw_deepgram_mutated: false",
        f"# audio_reference_folder: {audio_ref or 'none'}",
        "",
    ]
    return "\n".join(lines)


def _load_translation_summary(run_folder: Optional[Path]) -> dict[str, Any]:
    if not run_folder:
        return {}
    path = run_folder / "accuracy" / "translation_readiness_summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _latest_result_file(pattern: str) -> Path | None:
    out_dir = Path("troubleshooting/accuracy_benchmark/results")
    if not out_dir.exists():
        return None
    hits = sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def export_alpha_txt_on_stop(host: Any = None) -> dict[str, Any]:
    """Write Alpha.txt exports — must remain fast and non-blocking for Stop."""
    result: dict[str, Any] = {"ok": False}
    if not AUTO_EXPORT_ALPHA_TXT_ENABLED or not AUTO_EXPORT_ALPHA_TXT_ON_STOP:
        return result
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("ALPHA_TXT_AUTO_EXPORT_BEGIN")
    except Exception:
        pass

    from alpha.utils.alpha_output_protection import (
        get_alpha_export_run_type,
        is_live_alpha_write_allowed,
        is_placeholder_text,
        write_protected_live_alpha_outputs,
        write_smoke_test_alpha_outputs,
    )

    run_id, run_timestamp, run_folder = _get_run_identity()
    text = _read_alpha_text(host)
    run_type = get_alpha_export_run_type()

    try:
        from alpha.utils.troubleshooting_paths import (
            get_accuracy_path,
            get_latest_dir,
            get_troubleshooting_root,
            get_transcript_path,
        )

        per_run_alpha = get_transcript_path("alpha_output")
        troubleshooting_root = get_troubleshooting_root()
        latest_dir = get_latest_dir()
        accuracy_copy_path = get_accuracy_path("alpha_for_accuracy_check")
        audio_ref = str(run_folder / "audio_temp") if run_folder else "audio_temp"

        if not is_live_alpha_write_allowed():
            result = write_smoke_test_alpha_outputs(
                text,
                run_type=run_type,
                status={"run_id": run_id, "run_timestamp": run_timestamp},
            )
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("ALPHA_TXT_AUTO_EXPORT_WRITTEN", path=result.get("smoke_alpha_path", ""))
            except Exception:
                pass
            return result

        if not text.strip() or is_placeholder_text(text):
            # Do not rewrite sealed run transcript paths; evidence only.
            result.update(
                {
                    "ok": True,
                    "per_run_alpha_path": str(per_run_alpha),
                    "alpha_output_line_count": 0,
                    "live_paths_updated": False,
                    "run_type": run_type,
                    "authoritative_final_read_only": True,
                }
            )
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "ALPHA_PLACEHOLDER_SKIPPED_FOR_LIVE_ROOT_PATHS",
                    per_run_only=str(per_run_alpha),
                )
            except Exception:
                pass
            return result

        # Prefer sealed Final Alpha; never create/overwrite Alpha_output_FINAL.txt here.
        sealed_text = ""
        try:
            from alpha.utils.final_artifact_authority import (
                get_final_export_authority_state,
                verify_final_export_seal,
            )

            if run_folder is not None:
                verify_final_export_seal(run_folder, run_id=run_id)
                final_path = Path(run_folder) / "transcripts" / "Alpha_output_FINAL.txt"
                if final_path.exists():
                    sealed_text = final_path.read_text(encoding="utf-8")
                state = get_final_export_authority_state(run_folder)
                result["final_export_write_count"] = state.get("write_count")
                result["final_seal_verified"] = state.get("seal_verified")
        except Exception as exc:
            result["seal_read_warning"] = f"{type(exc).__name__}: {exc}"

        export_text = sealed_text if sealed_text.strip() else text
        # Approved aliases + accuracy copy only — not Alpha_output_FINAL.txt.
        protected = write_protected_live_alpha_outputs(
            export_text,
            run_id=run_id,
            run_timestamp=run_timestamp,
            per_run_alpha=per_run_alpha,
            troubleshooting_root=troubleshooting_root,
            latest_dir=latest_dir,
            accuracy_copy_path=accuracy_copy_path,
            audio_ref=audio_ref,
            header_fn=_accuracy_header,
            skip_per_run_alpha_if_sealed=True,
        )
        result.update(protected)
        result["authoritative_final_read_only"] = True

        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("ALPHA_TXT_AUTO_EXPORT_WRITTEN", path=result.get("alpha_txt_path", ""))
            jp_accuracy_log("ALPHA_TXT_LATEST_COPY_WRITTEN", path=result.get("latest_alpha_txt_path", ""))
            jp_accuracy_log(
                "ALPHA_TXT_ACCURACY_COPY_WRITTEN", path=result.get("accuracy_alpha_txt_path", "")
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "ALPHA_TXT_AUTO_EXPORT_FAILED_NON_BLOCKING",
                error=str(exc),
            )
        except Exception:
            pass
        result["error"] = str(exc)
    return result


def write_accuracy_evidence_index(
    host: Any = None,
    *,
    export_result: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    if not ACCURACY_EVIDENCE_MODE_ENABLED:
        return None
    run_id, run_timestamp, run_folder = _get_run_identity()
    if run_folder is None:
        return None

    summary = _load_translation_summary(run_folder)
    export_result = export_result or {}
    alpha_line_count = int(export_result.get("alpha_output_line_count", 0))

    audio_manifest = run_folder / "audio_temp" / "audio_manifest.json"
    audio_summary = run_folder / "audio_temp" / "audio_temp_summary.txt"
    audio_available = audio_manifest.exists()
    expires_at = ""
    if audio_available:
        try:
            manifest = json.loads(audio_manifest.read_text(encoding="utf-8"))
            expires_at = manifest.get("expires_at", "")
        except Exception:
            pass

    index: dict[str, Any] = {
        "app_version": APP_VERSION,
        "app_codename": APP_CODENAME,
        "anti_overfit_mode_enabled": ANTI_OVERFIT_MODE_ENABLED,
        "benchmark_baseline_lock_enabled": BENCHMARK_BASELINE_LOCK_ENABLED,
        "auto_business_correction_level": AUTO_BUSINESS_CORRECTION_LEVEL,
        "correction_rule_approval_required": CORRECTION_RULE_APPROVAL_REQUIRED,
        "single_sample_correction_blocked": SINGLE_SAMPLE_CORRECTION_BLOCKED,
        "lesson_specific_corrections_disabled": LESSON_SPECIFIC_CORRECTIONS_DISABLED,
        "run_id": run_id,
        "run_timestamp": run_timestamp,
        "alpha_txt_path": export_result.get("alpha_txt_path", ""),
        "latest_alpha_txt_path": export_result.get("latest_alpha_txt_path", ""),
        "accuracy_alpha_txt_path": export_result.get("accuracy_alpha_txt_path", ""),
        "raw_deepgram_finals_path": str(run_folder / "transcripts" / "raw_deepgram_finals.jsonl"),
        "stable_commits_path": str(run_folder / "transcripts" / "stable_commits.jsonl"),
        "ui_exported_segments_path": str(
            run_folder / "transcripts" / "ui_exported_segments.jsonl"
        ),
        "audio_manifest_path": str(audio_manifest) if audio_available else "",
        "audio_temp_summary_path": str(audio_summary) if audio_summary.exists() else "",
        "audio_available": audio_available,
        "audio_retention_hours": TEMP_AUDIO_RETENTION_HOURS,
        "audio_expires_at": expires_at,
        "raw_mutation_count": int(summary.get("raw_mutation_count", 0)),
        "stable_commit_count": int(summary.get("stable_commit_count", 0)),
        "alpha_output_line_count": alpha_line_count,
        "translation_ready_ratio": summary.get("translation_ready_ratio", 0.0),
        "punctuation_start_count": int(summary.get("punctuation_start_count", 0)),
        "short_fragment_count": int(summary.get("short_fragment_count", 0)),
        "incomplete_tail_count": int(summary.get("incomplete_tail_count", 0)),
        "business_correction_count": int(summary.get("business_correction_count", 0)),
        "business_accuracy_expansion_count": int(
            summary.get("business_accuracy_expansion_count", 0)
        ),
        "split_fragment_repair_count": int(summary.get("split_fragment_repair_count", 0)),
        "duplicate_phrase_dedupe_count": int(summary.get("duplicate_phrase_dedupe_count", 0)),
        "midline_punctuation_cleanup_count": int(
            summary.get("midline_punctuation_cleanup_count", 0)
        ),
        "name_correction_count": int(summary.get("name_correction_count", 0)),
        "name_correction_skipped_count": int(summary.get("name_correction_skipped_count", 0)),
        "latest_pointer_status_fixed": bool(summary.get("latest_pointer_status_fixed", False)),
        "latest_upload_zip_pointer_fixed": bool(
            summary.get("latest_upload_zip_pointer_fixed", False)
        ),
        "latest_accuracy_zip_entry_verified": bool(
            summary.get("latest_accuracy_zip_entry_verified", False)
        ),
        "stop_tail_suppressed_count": int(summary.get("stop_tail_suppressed_count", 0)),
        "accuracy_evidence_ready": bool(alpha_line_count > 0 or audio_available),
        "notes": "WAV audio excluded from upload package by default",
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    visible_audit_path = ""
    audit_summary: dict[str, Any] = {}
    try:
        alpha_for_audit = ""
        latest_live = export_result.get("latest_live_alpha_output_path", "")
        if latest_live and Path(latest_live).exists():
            alpha_for_audit = Path(latest_live).read_text(encoding="utf-8")
        else:
            latest_alpha = Path(export_result.get("latest_alpha_txt_path", ""))
            if latest_alpha.exists():
                alpha_for_audit = latest_alpha.read_text(encoding="utf-8")
            elif run_folder:
                per_run = run_folder / "transcripts" / "Alpha output.txt"
                if per_run.exists():
                    alpha_for_audit = per_run.read_text(encoding="utf-8")
        if alpha_for_audit.strip() and run_folder:
            from alpha.transcription.japanese_visible_error_audit import write_visible_error_audit

            audit_path = write_visible_error_audit(
                alpha_for_audit,
                run_folder=run_folder,
                run_id=run_id,
            )
            if audit_path:
                visible_audit_path = str(audit_path)
                audit_summary = json.loads(audit_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    index["evidence_protection_enabled"] = EVIDENCE_PROTECTION_85232_ENABLED
    index["real_live_alpha_protection_enabled"] = REAL_LIVE_ALPHA_PROTECTION_ENABLED
    index["smoke_test_alpha_overwrite_blocked"] = SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED
    index["latest_live_alpha_output_path"] = export_result.get("latest_live_alpha_output_path", "")
    index["latest_live_alpha_output_size_bytes"] = int(
        export_result.get("latest_live_alpha_output_size_bytes", 0)
    )
    index["latest_live_alpha_protected"] = bool(
        export_result.get("latest_live_alpha_protected", REAL_LIVE_ALPHA_PROTECTION_ENABLED)
    )
    index["latest_live_alpha_updated_by_run_id"] = export_result.get(
        "latest_live_alpha_updated_by_run_id", run_id
    )
    index["latest_live_alpha_updated_at"] = export_result.get("latest_live_alpha_updated_at", "")
    index["visible_error_audit_expanded"] = VISIBLE_ERROR_AUDIT_EXPANDED
    index["visible_error_audit_path"] = visible_audit_path
    index["visible_error_count"] = int(audit_summary.get("visible_error_count", 0))
    index["visible_error_high_count"] = int(audit_summary.get("visible_error_high_count", 0))
    index["visible_error_medium_count"] = int(audit_summary.get("visible_error_medium_count", 0))
    index["visible_error_low_count"] = int(audit_summary.get("visible_error_low_count", 0))
    index["name_risk_count"] = int(audit_summary.get("name_risk_count", 0))
    index["business_term_risk_count"] = int(audit_summary.get("business_term_risk_count", 0))
    index["punctuation_artifact_count"] = int(audit_summary.get("punctuation_artifact_count", 0))
    index["sentence_boundary_risk_count"] = int(
        audit_summary.get("sentence_boundary_risk_count", 0)
    )
    index["reference_transcript_quality_check_enabled"] = REFERENCE_TRANSCRIPT_QUALITY_CHECK_ENABLED
    index["reference_quality_report_path"] = ""
    index["reference_quality_verdict"] = ""
    index["trusted_cer_score"] = None
    index["score_should_be_used_for_decision"] = False
    index["benchmark_score_report_path"] = ""
    index["reference_transcript_used"] = False
    index["cer_score"] = None
    index["normalized_cer_score"] = None
    index["rough_accuracy_percent"] = None
    index["dangerous_correction_count"] = int(
        summary.get("business_correction_regression_count", 0)
    )
    alignment_report = _latest_result_file("*_alignment_report.json")
    boundary_report = _latest_result_file("*_boundary_error_report.json")
    business_risk_report = _latest_result_file("*_business_term_risk_report.json")
    glossary_candidates = _latest_result_file("*_glossary_candidates.json")
    cleanup_suggestions = _latest_result_file("*_reference_cleanup_suggestions.txt")
    index["reference_alignment_diagnosis_enabled"] = REFERENCE_ALIGNMENT_DIAGNOSIS_ENABLED
    index["japanese_boundary_diagnosis_enabled"] = JAPANESE_BOUNDARY_DIAGNOSIS_ENABLED
    index["business_term_risk_report_enabled"] = BUSINESS_TERM_RISK_REPORT_ENABLED
    index["glossary_candidate_report_enabled"] = GLOSSARY_CANDIDATE_REPORT_ENABLED
    index["reference_cleanup_suggestions_enabled"] = REFERENCE_CLEANUP_SUGGESTIONS_ENABLED
    index["latest_alignment_report_path"] = str(alignment_report) if alignment_report else ""
    index["latest_boundary_error_report_path"] = str(boundary_report) if boundary_report else ""
    index["latest_business_term_risk_report_path"] = (
        str(business_risk_report) if business_risk_report else ""
    )
    index["latest_glossary_candidates_path"] = str(glossary_candidates) if glossary_candidates else ""
    index["latest_reference_cleanup_suggestions_path"] = (
        str(cleanup_suggestions) if cleanup_suggestions else ""
    )
    index["alignment_mode"] = ""
    index["trusted_cer_allowed"] = False
    index["total_boundary_risks"] = 0
    index["assembler_candidate_count"] = 0
    if alignment_report and alignment_report.exists():
        try:
            alignment_data = json.loads(alignment_report.read_text(encoding="utf-8"))
            index["alignment_mode"] = str(alignment_data.get("alignment_mode", ""))
            index["reference_quality_verdict"] = str(
                alignment_data.get("reference_quality_verdict", index.get("reference_quality_verdict", ""))
            )
            index["trusted_cer_allowed"] = bool(alignment_data.get("trusted_cer_allowed", False))
        except Exception:
            pass
    if boundary_report and boundary_report.exists():
        try:
            boundary_data = json.loads(boundary_report.read_text(encoding="utf-8"))
            index["total_boundary_risks"] = int(boundary_data.get("total_boundary_risks", 0))
            index["assembler_candidate_count"] = int(boundary_data.get("assembler_candidate_count", 0))
        except Exception:
            pass
    if business_risk_report and business_risk_report.exists():
        try:
            risk_data = json.loads(business_risk_report.read_text(encoding="utf-8"))
            index["business_term_risk_count"] = int(
                risk_data.get("business_term_risk_count", index.get("business_term_risk_count", 0))
            )
        except Exception:
            pass
    index["glossary_candidate_count"] = 0
    if glossary_candidates and glossary_candidates.exists():
        try:
            glossary_data = json.loads(glossary_candidates.read_text(encoding="utf-8"))
            index["glossary_candidate_count"] = int(glossary_data.get("glossary_candidate_count", 0))
        except Exception:
            pass
    index["business_cer_benchmark_integrity_enabled"] = BUSINESS_CER_BENCHMARK_INTEGRITY_ENABLED
    index["report_synchronization_85234_enabled"] = REPORT_SYNCHRONIZATION_85234_ENABLED
    index["cer_trust_requires_alignment_coverage"] = CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE
    index["reference_alpha_hash_binding_enabled"] = REFERENCE_ALPHA_HASH_BINDING_ENABLED
    index["latest_report_sync_enabled"] = LATEST_ANALYZER_REPORT_SYNC_ENABLED
    index["business_18min_test_protocol_path"] = (
        "troubleshooting/accuracy_benchmark/BUSINESS_18MIN_CER_TEST_PROTOCOL.md"
    )
    index["benchmark_manifest_path"] = ""
    index["latest_report_sync_status"] = ""
    index["latest_report_set_consistent"] = False
    index["latest_report_set_timestamp"] = ""
    index["trusted_score_before_alignment"] = None
    index["trusted_score_after_alignment"] = None
    index["alignment_coverage_verdict"] = ""
    index["alignment_integrity_verdict"] = ""
    index["unaligned_alpha_ratio"] = None
    index["extra_alpha_sections_count"] = index.get("extra_alpha_sections_count", 0)
    index["average_section_overlap_score"] = None
    index["score_blockers"] = []
    index["latest_score_report_path"] = ""
    index["latest_reference_quality_report_path"] = ""
    index["latest_alignment_report_json_path"] = index.get("latest_alignment_report_path", "")
    index["latest_alignment_report_txt_path"] = ""
    index["latest_report_set_warnings"] = []
    try:
        if LATEST_ANALYZER_REPORT_SYNC_ENABLED:
            from alpha.utils.accuracy_report_sync import sync_latest_accuracy_reports

            sync_result = sync_latest_accuracy_reports(run_id=index.get("run_id"))
            if sync_result.get("ok"):
                index["latest_report_sync_status"] = "completed"
                index["latest_report_set_consistent"] = bool(sync_result.get("consistent"))
                index["latest_report_set_warnings"] = sync_result.get("warnings", [])
                index.update(sync_result.get("index_updates", {}))
    except Exception:
        index["latest_report_sync_status"] = "failed_non_blocking"

    try:
        from alpha.utils.troubleshooting_paths import (
            get_accuracy_path,
            get_latest_dir,
            get_troubleshooting_root,
        )

        per_run_path = get_accuracy_path("accuracy_evidence_index")
        per_run_path.parent.mkdir(parents=True, exist_ok=True)
        per_run_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        latest_path = get_latest_dir() / "latest_accuracy_evidence_index.json"
        latest_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (get_troubleshooting_root() / "latest_accuracy_evidence_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "ACCURACY_EVIDENCE_INDEX_WRITTEN", path=str(per_run_path)
            )
            jp_accuracy_log(
                "LATEST_ACCURACY_EVIDENCE_INDEX_WRITTEN", path=str(latest_path)
            )
            jp_accuracy_log("ACCURACY_EVIDENCE_INDEX_BASELINE_FIELDS_UPDATED")
            jp_accuracy_log("ACCURACY_EVIDENCE_INDEX_85232_FIELDS_UPDATED")
            jp_accuracy_log("ACCURACY_EVIDENCE_INDEX_85233_FIELDS_UPDATED")
            jp_accuracy_log("ACCURACY_EVIDENCE_INDEX_85234_FIELDS_UPDATED")
        except Exception:
            pass
        return per_run_path
    except Exception:
        return None


def write_stop_tail_debug_evidence(
    *,
    text: str,
    speaker: int,
    commit_reason: str,
    incomplete_reason: str = "",
    classification: str = "incomplete_suppressed",
    stable_commit_id: str = "",
    source_commit_id: str = "",
    canonical_line_id: str = "",
) -> None:
    try:
        from alpha.utils.evidence_jsonl import append_jsonl
        from alpha.utils.troubleshooting_paths import (
            get_accuracy_path,
            get_transcript_path,
        )

        debug_path = get_transcript_path("incomplete_stop_tail")
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if debug_path.exists():
            existing = debug_path.read_text(encoding="utf-8")
        line = f"[Speaker {speaker}] {text.strip()}\n"
        if line not in existing:
            with debug_path.open("a", encoding="utf-8") as fh:
                fh.write(line)

        append_jsonl(
            get_accuracy_path("stop_tail_decisions"),
            {
                "text": text,
                "speaker": speaker,
                "commit_reason": commit_reason,
                "incomplete_reason": incomplete_reason,
                "classification": classification,
                "suppressed_from_alpha": True,
                "suppression_reason": incomplete_reason or "no_sentence_boundary",
                "raw_deepgram_mutated": False,
                "raw_mutation": False,
                "stable_commit_id": stable_commit_id or source_commit_id,
                "source_commit_id": source_commit_id or stable_commit_id,
                "canonical_line_id": canonical_line_id,
                "source": "stop_tail",
                "timestamp": time.time(),
            },
        )
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "STOP_TAIL_WRITTEN_TO_DEBUG_FILE",
                text=text,
                path=str(debug_path),
            )
            jp_accuracy_log("STOP_TAIL_DECISION_LOG_WRITTEN")
        except Exception:
            pass
    except Exception:
        pass


def write_latest_accuracy_evidence_zip(
    *,
    export_result: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Create latest_accuracy_evidence_index.zip after files are flushed."""
    if not LATEST_ACCURACY_ZIP_FLUSH_FIX_ENABLED:
        return None
    export_result = export_result or {}
    line_count = int(export_result.get("alpha_output_line_count", 0))
    try:
        import zipfile

        from alpha.utils.troubleshooting_paths import get_latest_dir, get_troubleshooting_root

        latest_dir = get_latest_dir()
        root = get_troubleshooting_root()
        alpha_path = latest_dir / "latest_live_alpha_output.txt"
        if not alpha_path.exists() or alpha_path.stat().st_size == 0:
            alpha_path = root / "latest_alpha_output.txt"
        if not alpha_path.exists() or alpha_path.stat().st_size == 0:
            alpha_path = latest_dir / "latest_alpha_output.txt"
        json_path = latest_dir / "latest_accuracy_evidence_index.json"
        if not json_path.exists():
            json_path = root / "latest_accuracy_evidence_index.json"

        if alpha_path.exists():
            alpha_path.open("a", encoding="utf-8").close()
            alpha_size = alpha_path.stat().st_size
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "LATEST_ALPHA_OUTPUT_FLUSHED_BEFORE_ZIP",
                    path=str(alpha_path),
                    size_bytes=alpha_size,
                )
                if line_count > 0 and alpha_size > 0:
                    jp_accuracy_log(
                        "LATEST_ALPHA_OUTPUT_SIZE_VERIFIED",
                        size_bytes=alpha_size,
                    )
            except Exception:
                pass

        if json_path.exists():
            json_path.open("a", encoding="utf-8").close()
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "LATEST_ACCURACY_EVIDENCE_JSON_FLUSHED_BEFORE_ZIP",
                    path=str(json_path),
                )
            except Exception:
                pass

        zip_path = latest_dir / "latest_accuracy_evidence_index.zip"
        root_zip = root / "latest_accuracy_evidence_index.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if alpha_path.exists():
                zf.write(alpha_path, arcname="latest_alpha_output.txt")
            if json_path.exists():
                zf.write(json_path, arcname="latest_accuracy_evidence_index.json")
        root_zip.write_bytes(zip_path.read_bytes())

        entry_ok = False
        entry_size = 0
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "latest_alpha_output.txt" in zf.namelist():
                entry_size = zf.getinfo("latest_alpha_output.txt").file_size
                entry_ok = entry_size > 0 or line_count == 0

        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "LATEST_ACCURACY_EVIDENCE_ZIP_CREATED",
                path=str(zip_path),
                entry_size=entry_size,
            )
            if entry_ok:
                jp_accuracy_log(
                    "LATEST_ACCURACY_EVIDENCE_ZIP_ENTRY_VERIFIED",
                    entry_size=entry_size,
                )
            elif line_count > 0:
                jp_accuracy_log(
                    "LATEST_ACCURACY_EVIDENCE_ZIP_EMPTY_ENTRY_FIXED",
                    entry_size=entry_size,
                )
        except Exception:
            pass
        return zip_path
    except Exception as exc:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "LATEST_ACCURACY_EVIDENCE_ZIP_FAILED_NON_BLOCKING",
                error=str(exc),
            )
        except Exception:
            pass
        return None


def export_alpha_evidence_on_stop(host: Any = None) -> None:
    """Combined export invoked from minimal Stop path — read-only for Final Alpha."""
    try:
        from alpha.utils.boundary_evidence_finalize import finalize_boundary_evidence_on_stop

        finalize_boundary_evidence_on_stop()
    except Exception:
        pass
    export_result = export_alpha_txt_on_stop(host)
    try:
        from alpha.utils.canonical_export_writer import (
            sync_non_authoritative_aliases_from_sealed_final,
        )

        run_id, _run_timestamp, run_folder = _get_run_identity()
        if run_folder is not None:
            alias_result = sync_non_authoritative_aliases_from_sealed_final(
                run_folder=run_folder,
                run_id=run_id,
            )
            export_result.update(alias_result)
            export_result["legacy_authoritative_writer_disabled"] = True
            export_result["authoritative_final_read_only"] = True
    except Exception as exc:
        export_result["alias_sync_error"] = f"{type(exc).__name__}: {exc}"
    write_accuracy_evidence_index(host, export_result=export_result)
    write_latest_accuracy_evidence_zip(export_result=export_result)
    try:
        from alpha.utils.atomic_evidence_finalize import finalize_atomic_evidence

        run_id, _, run_folder = _get_run_identity()
        finalize_atomic_evidence(run_folder=run_folder, export_result=export_result)
    except Exception:
        pass


def schedule_alpha_evidence_export_background(host: Any = None) -> None:
    """Fire-and-forget export after Stop core completes."""

    def _worker() -> None:
        try:
            export_alpha_evidence_on_stop(host)
        except Exception:
            pass

    threading.Thread(
        target=_worker, name="AlphaEvidenceExport", daemon=True
    ).start()
