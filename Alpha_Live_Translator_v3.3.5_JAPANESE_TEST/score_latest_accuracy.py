"""Benchmark scoring for Alpha output against a reference transcript."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from alpha.constants import (
    APP_VERSION,
    CER_REFERENCE_VALIDATION_ENABLED,
    CER_TRUST_ALIGNMENT_V2_ENABLED,
    CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE,
    LINE_COUNT_MISMATCH_TOLERANCE_ENABLED,
    REFERENCE_ALPHA_HASH_BINDING_ENABLED,
)

_BUSINESS_TERMS = (
    "御社",
    "弊社",
    "お世話になっております",
    "よろしくお願いいたします",
    "恐れ入ります",
    "承知いたしました",
    "ご挨拶に参りました",
    "後任",
    "前任者",
    "担当交代",
)


def _normalize_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
        if m:
            line = m.group(1).strip()
        lines.append(line)
    body = "".join(lines)
    body = re.sub(r"\s+", "", body)
    return body


def _levenshtein_counts(a: str, b: str) -> tuple[int, int, int, int]:
    if not a and not b:
        return 0, 0, 0, 0
    rows = len(a) + 1
    cols = len(b) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    i, j = len(a), len(b)
    ins = del_ = sub = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] and a[i - 1] != b[j - 1]:
            sub += 1
            i -= 1
            j -= 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + 1:
            ins += 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            del_ += 1
            i -= 1
        else:
            if i > 0:
                i -= 1
            if j > 0:
                j -= 1
    return ins, del_, sub, dp[len(a)][len(b)]


def _business_term_stats(text: str) -> tuple[int, int]:
    hits = sum(text.count(term) for term in _BUSINESS_TERMS)
    misses = 0
    for term in ("御社", "お世話になっております", "よろしくお願いいたします"):
        if term not in text:
            misses += 1
    return hits, misses


def _load_summary_metrics(run_folder: Path | None) -> dict[str, Any]:
    if not run_folder:
        return {}
    path = run_folder / "accuracy" / "translation_readiness_summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_alpha_path(args: argparse.Namespace) -> Path | None:
    """Require explicit --alpha OR --run-folder; never silent latest_* fallback."""
    if getattr(args, "alpha", None):
        return Path(args.alpha)
    if getattr(args, "final", None):
        return Path(args.final)
    if getattr(args, "run_folder", None):
        return Path(args.run_folder) / "transcripts" / "Alpha_output_FINAL.txt"
    return None


def _load_alignment_report(path: str) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _apply_alignment_coverage_trust(
    report: dict[str, Any],
    alignment: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = list(report.get("score_blockers", []))
    trusted_before = bool(report.get("trusted_score"))
    report["trusted_score_before_alignment"] = trusted_before
    report["alignment_report_path"] = str(alignment.get("_source_path", report.get("alignment_report_path", "")))
    report["alignment_report_used_for_trust"] = bool(alignment)
    report["alignment_algorithm_version"] = alignment.get("alignment_algorithm_version", "v1_line_section")

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("CER_TRUST_V2_STARTED")
    except Exception:
        pass

    if not alignment:
        if CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE:
            report["alignment_coverage_verdict"] = "missing"
            report["score_warning"] = (
                str(report.get("score_warning", "")) + " alignment_report_missing_for_trust"
            ).strip()
            if trusted_before and report.get("line_count_alpha", 0) > 20:
                blockers.append("alignment_report_missing_for_trust")
        report["score_blockers"] = blockers
        report["trusted_score_after_alignment_v1"] = False
        report["trusted_score_after_alignment_v2"] = False
        report["final_trusted_score"] = False
        report["trusted_score"] = False
        report["score_should_be_used_for_decision"] = False
        report["trusted_cer_score"] = None
        report["rough_accuracy_percent"] = None
        report["score_trust_reason"] = "alignment_report_missing"
        return report

    # --- V1 line-section fields (recorded, not sole trust gate when V2 present) ---
    report["alignment_mode"] = alignment.get("alignment_mode", "")
    report["aligned_alpha_line_count"] = alignment.get("aligned_alpha_line_count", 0)
    report["total_alpha_line_count"] = alignment.get("total_alpha_line_count", report.get("line_count_alpha", 0))
    report["unaligned_alpha_line_count"] = alignment.get("unaligned_alpha_line_count", 0)
    report["unaligned_alpha_ratio"] = alignment.get("unaligned_alpha_ratio", 0)
    report["average_section_overlap_score"] = alignment.get("average_section_overlap_score", 0)
    report["extra_alpha_sections_count"] = alignment.get("extra_alpha_sections_count", 0)
    report["alignment_integrity_verdict_v1"] = alignment.get(
        "alignment_integrity_verdict_v1", alignment.get("alignment_integrity_verdict", "")
    )
    report["alignment_coverage_verdict_v1"] = alignment.get(
        "alignment_coverage_verdict_v1", alignment.get("alignment_coverage_verdict", "")
    )

    trusted_v1 = trusted_before
    v1_blockers: list[str] = []
    if alignment.get("alignment_mode") == "qualitative_only":
        trusted_v1 = False
        v1_blockers.append("alignment_qualitative_only")
    if float(alignment.get("unaligned_alpha_ratio", 1.0)) > 0.25:
        trusted_v1 = False
        v1_blockers.append("v1_unaligned_alpha_line_ratio_too_high")
    extra = int(alignment.get("extra_alpha_sections_count", 0))
    total_alpha = int(alignment.get("total_alpha_line_count", 0) or report.get("line_count_alpha", 0))
    if extra > max(10, total_alpha // 2):
        trusted_v1 = False
        v1_blockers.append("v1_extra_alpha_sections_too_high")
    if float(alignment.get("average_section_overlap_score", 0)) < 0.50:
        trusted_v1 = False
        v1_blockers.append("v1_average_section_overlap_too_low")
    if alignment.get("alignment_integrity_verdict_v1", alignment.get("alignment_integrity_verdict")) in (
        "weak",
        "invalid",
    ):
        trusted_v1 = False
        v1_blockers.append("v1_alignment_integrity_weak_or_invalid")
    report["trusted_score_after_alignment_v1"] = trusted_v1

    # --- V2 char/window trust ---
    has_v2 = bool(alignment.get("alignment_algorithm_version", "").startswith("v2"))
    report["char_coverage_used_for_trust"] = has_v2 and CER_TRUST_ALIGNMENT_V2_ENABLED
    trusted_v2 = trusted_before
    v2_blockers: list[str] = []

    if has_v2 and CER_TRUST_ALIGNMENT_V2_ENABLED:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("CER_TRUST_V2_ALIGNMENT_CHECK_STARTED")
        except Exception:
            pass
        report["unaligned_alpha_char_ratio"] = alignment.get("unaligned_alpha_char_ratio", 0)
        report["unaligned_reference_char_ratio"] = alignment.get("unaligned_reference_char_ratio", 0)
        report["average_window_overlap_score"] = alignment.get("average_window_overlap_score", 0)
        report["matched_window_count"] = alignment.get("matched_window_count", 0)
        report["alignment_integrity_verdict_v2"] = alignment.get("alignment_integrity_verdict_v2", "")
        report["alignment_coverage_verdict_v2"] = alignment.get("alignment_coverage_verdict_v2", "")
        report["line_count_mismatch_tolerated"] = bool(alignment.get("line_count_mismatch_tolerated", False))

        if not report.get("alpha_sha256") or not report.get("reference_sha256"):
            trusted_v2 = False
            v2_blockers.append("hash_missing")
        else:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("CER_TRUST_V2_HASH_CHECK_PASSED")
            except Exception:
                pass

        u_alpha = float(alignment.get("unaligned_alpha_char_ratio", 1.0))
        u_ref = float(alignment.get("unaligned_reference_char_ratio", 1.0))
        avg_win = float(alignment.get("average_window_overlap_score", 0))
        order_v = int(alignment.get("alignment_order_violations", 0))
        integrity_v2 = str(alignment.get("alignment_integrity_verdict_v2", ""))

        if u_alpha > 0.25:
            trusted_v2 = False
            v2_blockers.append("unaligned_alpha_char_ratio_too_high")
        if u_ref > 0.25:
            trusted_v2 = False
            v2_blockers.append("unaligned_reference_char_ratio_too_high")
        if avg_win < 0.50:
            trusted_v2 = False
            v2_blockers.append("average_window_overlap_too_low")
        if order_v > 5:
            trusted_v2 = False
            v2_blockers.append("alignment_order_violations_high")
        if integrity_v2 not in ("strong", "acceptable"):
            trusted_v2 = False
            v2_blockers.append("alignment_integrity_v2_not_acceptable")

        if LINE_COUNT_MISMATCH_TOLERANCE_ENABLED and alignment.get("line_count_mismatch_tolerated"):
            v2_blockers = [b for b in v2_blockers if not b.startswith("v1_")]
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("CER_TRUST_V2_LINE_MISMATCH_TOLERATED")
            except Exception:
                pass

        if trusted_v2:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("CER_TRUST_V2_ALIGNMENT_CHECK_PASSED")
                jp_accuracy_log("CER_TRUST_V2_SCORE_TRUSTED")
            except Exception:
                pass
            report["score_trust_reason"] = "v2_char_coverage_passed"
        else:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("CER_TRUST_V2_ALIGNMENT_CHECK_FAILED", blockers=v2_blockers)
                jp_accuracy_log("CER_TRUST_V2_SCORE_DOWNGRADED", blockers=v2_blockers)
            except Exception:
                pass
            report["score_trust_reason"] = "v2_alignment_coverage_failed"
        report["trusted_score_after_alignment_v2"] = trusted_v2
        final_trusted = trusted_v2
        blockers.extend(v2_blockers)
    else:
        final_trusted = trusted_v1
        blockers.extend(v1_blockers)
        report["trusted_score_after_alignment_v2"] = None
        report["score_trust_reason"] = "v1_line_section_only"

    report["final_trusted_score"] = final_trusted
    report["trusted_score"] = final_trusted
    report["score_should_be_used_for_decision"] = final_trusted
    report["trusted_cer_score"] = report.get("cer_score") if final_trusted else None
    report["rough_accuracy_percent"] = (
        round(max(0.0, 1.0 - float(report.get("cer_score", 0))) * 100.0, 2)
        if final_trusted and report.get("cer_score") is not None
        else None
    )
    if not final_trusted:
        report["score_warning"] = (
            str(report.get("score_warning", "")) + " CER trust downgraded after alignment check."
        ).strip()
    report["score_blockers"] = blockers
    report["trusted_score_after_alignment"] = final_trusted
    report["alignment_coverage_verdict"] = (
        alignment.get("alignment_coverage_verdict_v2")
        if has_v2
        else alignment.get("alignment_coverage_verdict", "")
    )
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("CER_TRUST_V2_COMPLETED", trusted=final_trusted)
    except Exception:
        pass
    return report


def score_alpha_reference(
    alpha_text: str,
    reference_text: str,
    *,
    alpha_path: str = "",
    reference_path: str = "",
    run_folder: Path | None = None,
    quality_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from reference_transcript_quality_check import check_reference_transcript_quality

    quality = quality_report or check_reference_transcript_quality(
        alpha_text,
        reference_text,
        alpha_path=alpha_path,
        reference_path=reference_path,
    )
    verdict = str(quality.get("verdict", "invalid_for_cer"))
    trusted = verdict == "valid_for_cer"
    low_confidence = verdict == "questionable_for_cer"
    score_warning = str(quality.get("recommendation", ""))
    score_usable = trusted
    score_blockers: list[str] = []

    if not quality.get("alpha_sha256") and REFERENCE_ALPHA_HASH_BINDING_ENABLED:
        trusted = False
        score_usable = False
        score_blockers.append("alpha_hash_missing")

    alpha_norm = _normalize_text(alpha_text)
    ref_norm = _normalize_text(reference_text)
    ins = del_ = sub = distance = 0
    cer = None
    rough_accuracy = None
    if verdict != "invalid_for_cer":
        ins, del_, sub, distance = _levenshtein_counts(alpha_norm, ref_norm)
        ref_len = max(len(ref_norm), 1)
        cer = round(distance / ref_len, 6)
        rough_accuracy = round(max(0.0, 1.0 - cer) * 100.0, 2)
        if low_confidence:
            trusted = False
            score_usable = False
    else:
        score_warning = quality.get("recommendation", "Invalid reference for CER")

    alpha_lines = [ln for ln in alpha_text.splitlines() if ln.strip() and not ln.startswith("#")]
    ref_lines = [ln for ln in reference_text.splitlines() if ln.strip() and not ln.startswith("#")]
    biz_hits, biz_misses = _business_term_stats(alpha_norm)
    summary = _load_summary_metrics(run_folder)

    try:
        from alpha.transcription.japanese_visible_error_audit import audit_visible_errors

        visible_count = int(audit_visible_errors(alpha_text).get("visible_error_count", 0))
    except Exception:
        visible_count = 0

    report = {
        "app_version": APP_VERSION,
        "cer_reference_validation_enabled": CER_REFERENCE_VALIDATION_ENABLED,
        "cer_trust_requires_alignment_coverage": CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE,
        "alpha_path": alpha_path,
        "reference_path": reference_path,
        "reference_transcript_used": bool(reference_text.strip()),
        "reference_quality_verdict": verdict,
        "reference_quality_report_path": quality.get("reference_quality_report_path", ""),
        "trusted_score": trusted,
        "low_confidence_score": low_confidence,
        "score_warning": score_warning,
        "score_should_be_used_for_decision": score_usable,
        "score_blockers": score_blockers,
        "cer_score": cer,
        "trusted_cer_score": cer if trusted else None,
        "normalized_cer_score": cer,
        "rough_accuracy_percent": rough_accuracy if trusted else None,
        "line_count_alpha": len(alpha_lines),
        "line_count_reference": len(ref_lines),
        "alpha_char_count": len(alpha_norm),
        "reference_char_count": len(ref_norm),
        "insertion_count": ins,
        "deletion_count": del_,
        "substitution_count": sub,
        "business_term_hits": biz_hits,
        "business_term_misses": biz_misses,
        "visible_error_count": visible_count,
        "dangerous_correction_count": int(summary.get("business_correction_regression_count", 0)),
        "raw_mutation_count": int(summary.get("raw_mutation_count", 0)),
        "punctuation_start_count": int(summary.get("punctuation_start_count", 0)),
        "short_fragment_count": int(summary.get("short_fragment_count", 0)),
        "incomplete_tail_count": int(summary.get("incomplete_tail_count", 0)),
        "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "report_type": "accuracy_score_report",
        "alpha_sha256": quality.get("alpha_sha256", ""),
        "alpha_size_bytes": quality.get("alpha_size_bytes", 0),
        "reference_sha256": quality.get("reference_sha256", ""),
        "reference_size_bytes": quality.get("reference_size_bytes", 0),
    }
    if REFERENCE_ALPHA_HASH_BINDING_ENABLED and alpha_path:
        from alpha.utils.reference_alpha_hash import bind_report_hashes

        bind_report_hashes(
            report,
            alpha_path=alpha_path,
            reference_path=reference_path,
            report_type="accuracy_score_report",
        )
    try:
        from alpha.constants import (
            CLEAN_ALPHA_EXPORT_ENABLED,
            CLEAN_ALPHA_EXPORT_REQUIRED_FOR_SCORING,
            CUMULATIVE_ALPHA_REJECTION_ENABLED,
            STABLE_LINE_REVISION_MODEL_ENABLED,
        )
        from alpha.transcription.final_output_cleanup import detect_cumulative_alpha_lines_v2

        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("SCORING_RESIDUAL_DUPLICATE_SCAN_STARTED")
        except Exception:
            pass
        cum = detect_cumulative_alpha_lines_v2(alpha_lines)
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("SCORING_RESIDUAL_DUPLICATE_SCAN_COMPLETED", count=cum.get("cumulative_duplicate_count", 0))
            jp_accuracy_log("SCORING_PUNCTUATION_ARTIFACT_SCAN_STARTED")
            jp_accuracy_log(
                "SCORING_PUNCTUATION_ARTIFACT_SCAN_COMPLETED",
                count=cum.get("punctuation_artifact_count", 0),
            )
        except Exception:
            pass
        report["clean_alpha_export_enabled"] = CLEAN_ALPHA_EXPORT_ENABLED
        report["stable_revision_model_enabled"] = STABLE_LINE_REVISION_MODEL_ENABLED
        report["alpha_export_source"] = "latest_live_alpha_output"
        report["cumulative_duplicate_count"] = cum.get("cumulative_duplicate_count", 0)
        report["punctuation_artifact_count"] = cum.get("punctuation_artifact_count", 0)
        report["alpha_output_cumulative_duplicate_suspected"] = cum.get(
            "alpha_output_cumulative_duplicate_suspected", False
        )
        report["alpha_output_punctuation_artifact_suspected"] = cum.get(
            "alpha_output_punctuation_artifact_suspected", False
        )
        report["clean_alpha_export_required"] = CLEAN_ALPHA_EXPORT_REQUIRED_FOR_SCORING
        report["clean_alpha_export_confirmed"] = not cum.get("alpha_output_cumulative_duplicate_suspected", False)
        try:
            from alpha.constants import (
                CORPORATE_IR_GLOSSARY_ENABLED,
                CORPORATE_IR_GLOSSARY_PATH,
            )

            report["corporate_ir_glossary_enabled"] = CORPORATE_IR_GLOSSARY_ENABLED
            report["glossary_path"] = CORPORATE_IR_GLOSSARY_PATH
            summary_path = Path("troubleshooting/latest/glossary_correction_summary.json")
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                report["glossary_keyterm_count"] = summary.get("glossary_keyterm_count", 0)
                report["glossary_correction_count"] = summary.get("glossary_correction_count", 0)
                report["financial_number_correction_count"] = summary.get("financial_number_correction_count", 0)
                report["glossary_correction_summary_path"] = str(summary_path).replace("\\", "/")
                report["glossary_correction_decisions_path"] = summary.get("decisions_path", "")
                report["financial_number_accuracy_report_path"] = summary.get("financial_number_accuracy_report_path", "")
                report["corporate_term_accuracy_report_path"] = summary.get("corporate_term_accuracy_report_path", "")
            idx_path = Path("troubleshooting/latest/latest_accuracy_evidence_index.json")
            if idx_path.exists():
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
                if "glossary_correction_count" not in report:
                    report["glossary_correction_count"] = idx.get("glossary_correction_count", 0)
                if "financial_number_correction_count" not in report:
                    report["financial_number_correction_count"] = idx.get("financial_number_correction_count", 0)
                report["glossary_correction_decisions_path"] = report.get(
                    "glossary_correction_decisions_path", idx.get("glossary_correction_decisions_path", "")
                )
                report["financial_number_accuracy_report_path"] = report.get(
                    "financial_number_accuracy_report_path", idx.get("financial_number_accuracy_report_path", "")
                )
                report["corporate_term_accuracy_report_path"] = report.get(
                    "corporate_term_accuracy_report_path", idx.get("corporate_term_accuracy_report_path", "")
                )
            cov_path = Path("troubleshooting/latest/export_coverage_report.json")
            if cov_path.exists():
                cov = json.loads(cov_path.read_text(encoding="utf-8"))
                report["clean_export_ready_for_scoring"] = cov.get("clean_export_ready_for_scoring", False)
                report["export_coverage_report_path"] = str(cov_path).replace("\\", "/")
                report["valid_segment_loss_count"] = cov.get("valid_segment_loss_count", 0)
                report["export_coverage_ratio"] = cov.get("export_coverage_ratio", 0)
                report["source_commit_coverage_ratio"] = cov.get("source_commit_coverage_ratio", 0)
                report["lineage_coverage_ratio"] = cov.get("lineage_coverage_ratio", 0)
                report["final_export_contains_pre_correction_lines"] = cov.get(
                    "final_export_contains_pre_correction_lines", False
                )
                report["canonical_export_line_count"] = cov.get("canonical_export_line_count", 0)
            ledger_path = Path("troubleshooting/latest/canonical_transcript_ledger.jsonl")
            if ledger_path.exists():
                report["canonical_transcript_ledger_path"] = str(ledger_path).replace("\\", "/")
            pre_path = Path("troubleshooting/latest/pre_correction_reentry_report.json")
            if pre_path.exists():
                pre = json.loads(pre_path.read_text(encoding="utf-8"))
                report["pre_correction_reentry_blocked_count"] = pre.get("pre_correction_blocked_count", 0)
                report["pre_correction_reentry_report_path"] = str(pre_path).replace("\\", "/")
            report["canonical_transcript_lineage_enabled"] = True
            report["final_export_lock_enabled"] = True
            if run_folder:
                for name, key in (
                    ("glossary_correction_decisions.jsonl", "glossary_correction_decisions_path"),
                    ("financial_number_accuracy_report.json", "financial_number_accuracy_report_path"),
                    ("corporate_term_accuracy_report.json", "corporate_term_accuracy_report_path"),
                ):
                    p = run_folder / "accuracy" / name
                    if p.exists():
                        report[key] = str(p).replace("\\", "/")
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("SCORING_GLOSSARY_LAYER_METADATA_INCLUDED")
            jp_accuracy_log("SCORING_GLOSSARY_CANONICAL_METADATA_INCLUDED")
            jp_accuracy_log("SCORING_EXPORT_COVERAGE_METADATA_INCLUDED")
            jp_accuracy_log("SCORING_CANONICAL_LINEAGE_METADATA_INCLUDED")
            jp_accuracy_log("SCORING_FINAL_EXPORT_LOCK_CONFIRMED")
        except Exception:
            pass
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log
        except Exception:
            jp_accuracy_log = lambda *a, **k: None
        if report.get("final_export_contains_pre_correction_lines"):
            report["final_trusted_score"] = False
            report["score_should_be_used_for_decision"] = False
            if "pre_correction_lines_in_final_export" not in score_blockers:
                score_blockers.append("pre_correction_lines_in_final_export")
            jp_accuracy_log("SCORING_PRE_CORRECTION_REENTRY_ABSENT", passed=False)
        else:
            jp_accuracy_log("SCORING_PRE_CORRECTION_REENTRY_ABSENT")
        if report.get("source_commit_coverage_ratio", 1.0) < 0.98:
            report["final_trusted_score"] = False
            report["score_should_be_used_for_decision"] = False
            if "source_commit_coverage_below_threshold" not in score_blockers:
                score_blockers.append("source_commit_coverage_below_threshold")
            jp_accuracy_log("SCORING_EXPORT_LINEAGE_GATE_FAILED")
        elif report.get("clean_export_ready_for_scoring"):
            jp_accuracy_log("SCORING_EXPORT_LINEAGE_GATE_PASSED")
        if report.get("valid_segment_loss_count", 0) > 0:
            report["final_trusted_score"] = False
            report["score_should_be_used_for_decision"] = False
            if "valid_segment_loss_detected" not in score_blockers:
                score_blockers.append("valid_segment_loss_detected")
            report["score_blockers"] = score_blockers
        if not report.get("clean_export_ready_for_scoring"):
            report["final_trusted_score"] = False
            report["score_should_be_used_for_decision"] = False
            if "clean_export_not_ready_for_scoring" not in score_blockers:
                score_blockers.append("clean_export_not_ready_for_scoring")
        clean_latest = Path("troubleshooting/latest/clean_active_transcript.jsonl")
        if clean_latest.exists():
            report["clean_active_transcript_line_count"] = sum(
                1 for ln in clean_latest.read_text(encoding="utf-8").splitlines() if ln.strip()
            )
        if run_folder:
            hist = run_folder / "transcripts" / "stable_commits.jsonl"
            if hist.exists():
                report["stable_commit_history_count"] = sum(
                    1 for ln in hist.read_text(encoding="utf-8").splitlines() if ln.strip()
                )
            clean = run_folder / "transcripts" / "clean_active_transcript.jsonl"
            if clean.exists():
                report["clean_active_line_count"] = sum(
                    1 for ln in clean.read_text(encoding="utf-8").splitlines() if ln.strip()
                )
        if cum.get("alpha_output_cumulative_duplicate_suspected") and CUMULATIVE_ALPHA_REJECTION_ENABLED:
            report["final_trusted_score"] = False
            report["score_should_be_used_for_decision"] = False
            if "alpha_output_cumulative_duplicate_suspected" not in score_blockers:
                score_blockers.append("alpha_output_cumulative_duplicate_suspected")
            report["score_blockers"] = score_blockers
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("SCORING_CLEAN_ALPHA_EXPORT_REJECTED", count=cum.get("cumulative_duplicate_count", 0))
                jp_accuracy_log("SCORING_CUMULATIVE_ALPHA_WARNING", count=cum.get("cumulative_duplicate_count", 0))
            except Exception:
                pass
        elif cum.get("punctuation_artifact_count", 0) > 5:
            if "alpha_output_punctuation_artifact_suspected" not in score_blockers:
                score_blockers.append("alpha_output_punctuation_artifact_suspected")
            report["score_blockers"] = score_blockers
        else:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("SCORING_CLEAN_ALPHA_EXPORT_CONFIRMED", alpha_path=alpha_path)
            except Exception:
                pass
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("SCORING_ALPHA_EXPORT_SOURCE_RECORDED", source=report.get("alpha_export_source", ""))
        except Exception:
            pass
    except Exception:
        pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        if verdict == "valid_for_cer":
            jp_accuracy_log("CER_REFERENCE_QUALITY_CHECK_PASSED")
    except Exception:
        pass
    return report


def _write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = Path("troubleshooting/accuracy_benchmark/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"{stamp}_accuracy_score_report.json"
    txt_path = out_dir / f"{stamp}_accuracy_score_report.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_lines = [
        "Alpha Live Translator — Accuracy Score Report",
        f"app_version: {report.get('app_version', '')}",
        f"trusted_score: {report.get('trusted_score', '')}",
        f"reference_quality_verdict: {report.get('reference_quality_verdict', '')}",
        f"CER: {report.get('cer_score', '')}",
        f"trusted_CER: {report.get('trusted_cer_score', '')}",
        f"rough_accuracy_percent: {report.get('rough_accuracy_percent', '')}",
        f"score_warning: {report.get('score_warning', '')}",
        f"final_trusted_score: {report.get('final_trusted_score', '')}",
        f"score_trust_reason: {report.get('score_trust_reason', '')}",
        f"score_should_be_used_for_decision: {report.get('score_should_be_used_for_decision', '')}",
    ]
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    return json_path, txt_path


def _latest_run_folder() -> Path | None:
    runs = Path("troubleshooting/runs")
    if not runs.exists():
        return None
    candidates = [p for p in runs.iterdir() if p.is_dir() and p.name != "_pending"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _latest_alignment_report_path(alpha_path: str = "", reference_path: str = "") -> str:
    out_dir = Path("troubleshooting/accuracy_benchmark/results")
    if not out_dir.exists():
        return ""
    candidates = sorted(out_dir.glob("*_alignment_report.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if alpha_path and data.get("alpha_path"):
            from alpha.utils.reference_alpha_hash import paths_match

            if not paths_match(str(data.get("alpha_path")), alpha_path):
                continue
        if reference_path and data.get("reference_path"):
            from alpha.utils.reference_alpha_hash import paths_match

            if not paths_match(str(data.get("reference_path")), reference_path):
                continue
        return str(path)
    return str(candidates[0]) if candidates else ""


def main() -> int:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("SCORE_LATEST_ACCURACY_SCRIPT_READY")
        jp_accuracy_log("REFERENCE_TRANSCRIPT_SCORING_READY")
        jp_accuracy_log("CER_REFERENCE_VALIDATION_STARTED")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Score Alpha output against reference transcript")
    parser.add_argument("--latest", action="store_true", help="DEPRECATED: silent latest fallback removed")
    parser.add_argument("--alpha", type=str, default="", help="Path to Alpha transcript")
    parser.add_argument("--final", type=str, default="", help="Path to Final Alpha transcript")
    parser.add_argument("--raw", type=str, default="", help="Optional raw stage path (explicit mode)")
    parser.add_argument("--stable", type=str, default="", help="Optional stable stage path (explicit mode)")
    parser.add_argument("--run-folder", type=str, default="", help="Run folder containing transcripts/Alpha_output_FINAL.txt")
    parser.add_argument("--reference", type=str, default="", help="Path to reference transcript")
    parser.add_argument("--project-state", type=str, default="", help="Optional PROJECT_STATE.json path")
    args = parser.parse_args()

    if not args.reference:
        print("FAILED: --reference is required (silent latest_* fallback removed)")
        return 2
    if not (args.alpha or args.final or args.run_folder):
        print(
            "FAILED: require --run-folder or --alpha/--final "
            "(or --raw/--stable/--final with --reference); silent latest_* fallback removed"
        )
        return 2

    alpha_path = _resolve_alpha_path(args)
    if alpha_path is None:
        print("FAILED: could not resolve alpha path from arguments")
        return 2
    reference_path = Path(args.reference)
    if not alpha_path.exists():
        print(f"FAILED alpha not found: {alpha_path}")
        return 1
    if not reference_path.exists():
        print(f"FAILED reference not found: {reference_path}")
        return 1

    alpha_text = alpha_path.read_text(encoding="utf-8")
    reference_text = reference_path.read_text(encoding="utf-8")

    from reference_transcript_quality_check import check_reference_transcript_quality, _write_report as write_quality

    quality = check_reference_transcript_quality(
        alpha_text,
        reference_text,
        alpha_path=str(alpha_path),
        reference_path=str(reference_path),
    )
    q_json, q_txt = write_quality(quality)
    quality["reference_quality_report_path"] = str(q_json)
    q_json.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")

    run_folder = _latest_run_folder()
    report = score_alpha_reference(
        alpha_text,
        reference_text,
        alpha_path=str(alpha_path),
        reference_path=str(reference_path),
        run_folder=run_folder,
        quality_report=quality,
    )
    report["reference_quality_report_path"] = str(q_json)
    align_path = _latest_alignment_report_path(str(alpha_path), str(reference_path))
    alignment = _load_alignment_report(align_path)
    if alignment:
        alignment["_source_path"] = align_path
    report["alignment_report_path"] = align_path
    if CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE:
        report = _apply_alignment_coverage_trust(report, alignment)
    json_path, txt_path = _write_report(report)
    report["benchmark_score_report_path"] = str(json_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from alpha.utils.accuracy_report_sync import sync_latest_accuracy_reports

        sync_latest_accuracy_reports(
            alpha_path=str(alpha_path),
            reference_path=str(reference_path),
        )
    except Exception:
        pass
    try:
        latest = Path("troubleshooting/latest")
        latest.mkdir(parents=True, exist_ok=True)
        bench_pointer = {
            "alpha_path": str(alpha_path),
            "reference_path": str(reference_path),
            "score_report_path": str(json_path),
            "scored_at": report.get("scored_at", ""),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        (latest / "LATEST_BENCHMARK_RUN_POINTER.json").write_text(
            json.dumps(bench_pointer, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("LATEST_BENCHMARK_RUN_POINTER_UPDATED", alpha_path=str(alpha_path))
    except Exception:
        pass

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        if quality.get("verdict") == "invalid_for_cer":
            jp_accuracy_log("CER_SCORING_SKIPPED_INVALID_REFERENCE")
        elif quality.get("verdict") == "questionable_for_cer":
            jp_accuracy_log("CER_SCORING_LOW_CONFIDENCE_REFERENCE")
        else:
            jp_accuracy_log("CER_SCORING_TRUSTED_REFERENCE")
        jp_accuracy_log("CER_SCORE_REPORT_WRITTEN", path=str(json_path))
    except Exception:
        pass

    print(
        f"verdict={report.get('reference_quality_verdict')} "
        f"trusted={report.get('trusted_score')} cer={report.get('cer_score')}"
    )
    print(f"report_json={json_path}")
    print(f"quality_json={q_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
