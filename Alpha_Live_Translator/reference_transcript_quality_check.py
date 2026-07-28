"""Reference transcript quality check before trusted CER scoring."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION, REFERENCE_ALPHA_HASH_BINDING_ENABLED, REFERENCE_TRANSCRIPT_QUALITY_CHECK_ENABLED

_BUSINESS_TERMS = (
    "御社",
    "弊社",
    "お世話になっております",
    "よろしくお願いいたします",
    "名刺交換",
    "ご挨拶",
    "後任",
    "前任者",
    "担当交代",
    "永井",
    "江藤",
    "チン",
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
    return re.sub(r"\s+", "", body)


def _char_overlap_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    return round(len(set_a & set_b) / max(len(set_a | set_b), 1), 4)


def _business_term_overlap(alpha: str, reference: str) -> float:
    hits = 0
    total = 0
    for term in _BUSINESS_TERMS:
        in_a = term in alpha
        in_b = term in reference
        if in_a or in_b:
            total += 1
            if in_a and in_b:
                hits += 1
    if total == 0:
        return 0.0
    return round(hits / total, 4)


def _detect_formatting_risks(reference_text: str) -> list[str]:
    flags: list[str] = []
    lines = [ln.strip() for ln in reference_text.splitlines() if ln.strip()]
    bullet_count = sum(1 for ln in lines if re.match(r"^[-*•]\s+", ln))
    numbered_count = sum(1 for ln in lines if re.match(r"^\d+[\).、]\s*", ln))
    heading_count = sum(1 for ln in lines if re.match(r"^#+\s+", ln))
    timestamp_only = sum(1 for ln in lines if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", ln))
    _summary_patterns = (
        re.compile(r"まとめ"),
        re.compile(r"要点"),
        re.compile(r"概要"),
        re.compile(r"要約"),
        re.compile(r"(?<!経営)トピック"),
        re.compile(r"\bLesson\b", re.I),
        re.compile(r"\blesson\b"),
    )
    summary_markers = sum(
        1 for ln in lines if any(pat.search(ln) for pat in _summary_patterns)
    )
    if bullet_count >= 3:
        flags.append("bullet_points")
    if numbered_count >= 3:
        flags.append("numbered_outline")
    if heading_count >= 2:
        flags.append("markdown_headings")
    if timestamp_only >= max(2, len(lines) // 3):
        flags.append("timestamp_only_lines")
    if summary_markers >= 2:
        flags.append("lesson_notes_summary_style")
    if len(lines) <= 5 and len(_normalize_text(reference_text)) < 80:
        flags.append("very_short_reference")
    return flags


def check_prepared_reference_snapshot_quality(
    reference_text: str,
    *,
    reference_path: str = "",
) -> dict[str, Any]:
    """Structural reference-only validation for pre-live prepared snapshots (V25.3.2)."""
    ref_norm = _normalize_text(reference_text)
    ref_chars = len(ref_norm)
    lines = [ln.strip() for ln in reference_text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    formatting_flags = _detect_formatting_risks(reference_text)

    verdict = "valid_for_cer"
    recommendation = "Prepared reference snapshot is structurally valid for CER."
    should_run_cer = True
    failure_reasons: list[str] = []

    if not ref_norm:
        verdict = "invalid_for_cer"
        recommendation = "Reference is empty after normalization."
        should_run_cer = False
        failure_reasons.append("empty_after_normalization")
    elif ref_chars < 80:
        verdict = "invalid_for_cer"
        recommendation = "Reference is too short for trusted CER."
        should_run_cer = False
        failure_reasons.append("too_short")
    elif len(lines) < 3:
        verdict = "invalid_for_cer"
        recommendation = "Reference has too few lines for a verbatim transcript."
        should_run_cer = False
        failure_reasons.append("too_few_lines")
    elif any(
        flag in formatting_flags
        for flag in ("bullet_points", "numbered_outline", "markdown_headings", "timestamp_only_lines")
    ):
        verdict = "invalid_for_cer"
        recommendation = "Reference looks like outline/notes, not verbatim transcript."
        should_run_cer = False
        failure_reasons.append("structural_outline_format")
    elif "lesson_notes_summary_style" in formatting_flags:
        verdict = "questionable_for_cer"
        recommendation = "Reference may include summary-style markers; review before trusting CER."
        should_run_cer = True
        failure_reasons.append("summary_style_markers")

    return {
        "app_version": APP_VERSION,
        "reference_transcript_quality_check_enabled": REFERENCE_TRANSCRIPT_QUALITY_CHECK_ENABLED,
        "alpha_path": "",
        "reference_path": reference_path,
        "verdict": verdict,
        "alpha_char_count": 0,
        "reference_char_count": ref_chars,
        "char_count_ratio": 0.0,
        "shared_char_overlap_ratio": 0.0,
        "business_term_overlap_ratio": 0.0,
        "formatting_risk_flags": formatting_flags,
        "recommendation": recommendation,
        "should_run_cer": should_run_cer,
        "valid_for_cer": verdict == "valid_for_cer",
        "failure_reasons": failure_reasons,
        "prepared_reference_only": True,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "report_type": "reference_quality_report",
    }


def check_reference_transcript_quality(
    alpha_text: str,
    reference_text: str,
    *,
    alpha_path: str = "",
    reference_path: str = "",
) -> dict[str, Any]:
    alpha_norm = _normalize_text(alpha_text)
    ref_norm = _normalize_text(reference_text)
    alpha_chars = len(alpha_norm)
    ref_chars = len(ref_norm)
    ratio = round(ref_chars / max(alpha_chars, 1), 4) if alpha_chars else 0.0
    shared_overlap = _char_overlap_ratio(alpha_norm, ref_norm)
    business_overlap = _business_term_overlap(alpha_norm, ref_norm)
    formatting_flags = _detect_formatting_risks(reference_text)

    verdict = "valid_for_cer"
    recommendation = "Reference appears aligned enough for trusted CER scoring."
    should_run_cer = True

    if not alpha_norm or not ref_norm:
        verdict = "invalid_for_cer"
        recommendation = "Alpha or reference is empty after normalization."
        should_run_cer = False
    elif ratio < 0.5 or ratio > 2.0:
        verdict = "questionable_for_cer"
        recommendation = "Character count ratio outside 0.5–2.0; reference may not be verbatim."
        should_run_cer = True
    if shared_overlap < 0.15:
        verdict = "invalid_for_cer"
        recommendation = "Very low shared character overlap; reference likely not aligned with Alpha."
        should_run_cer = False
    elif shared_overlap < 0.30 and verdict != "invalid_for_cer":
        verdict = "questionable_for_cer"
        recommendation = "Low overlap; treat CER as low confidence."
    if len(formatting_flags) >= 2:
        if verdict == "valid_for_cer":
            verdict = "questionable_for_cer"
        recommendation = "Reference looks like lesson notes/outline, not verbatim transcript."
    if "lesson_notes_summary_style" in formatting_flags and shared_overlap < 0.45:
        verdict = "invalid_for_cer"
        recommendation = "Reference appears to be lesson notes/summary, not verbatim transcript."
        should_run_cer = False

    report = {
        "app_version": APP_VERSION,
        "reference_transcript_quality_check_enabled": REFERENCE_TRANSCRIPT_QUALITY_CHECK_ENABLED,
        "alpha_path": alpha_path,
        "reference_path": reference_path,
        "verdict": verdict,
        "alpha_char_count": alpha_chars,
        "reference_char_count": ref_chars,
        "char_count_ratio": ratio,
        "shared_char_overlap_ratio": shared_overlap,
        "business_term_overlap_ratio": business_overlap,
        "formatting_risk_flags": formatting_flags,
        "recommendation": recommendation,
        "should_run_cer": should_run_cer,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "report_type": "reference_quality_report",
    }
    if REFERENCE_ALPHA_HASH_BINDING_ENABLED and alpha_path:
        from alpha.utils.reference_alpha_hash import bind_report_hashes

        bind_report_hashes(
            report,
            alpha_path=alpha_path,
            reference_path=reference_path,
            report_type="reference_quality_report",
        )
    return report


def _write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = Path("troubleshooting/accuracy_benchmark/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"{stamp}_reference_quality_report.json"
    txt_path = out_dir / f"{stamp}_reference_quality_report.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_lines = [
        "Reference Transcript Quality Report",
        f"verdict: {report.get('verdict', '')}",
        f"char_count_ratio: {report.get('char_count_ratio', '')}",
        f"shared_char_overlap_ratio: {report.get('shared_char_overlap_ratio', '')}",
        f"business_term_overlap_ratio: {report.get('business_term_overlap_ratio', '')}",
        f"should_run_cer: {report.get('should_run_cer', '')}",
        f"recommendation: {report.get('recommendation', '')}",
    ]
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    return json_path, txt_path


def main() -> int:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("REFERENCE_TRANSCRIPT_QUALITY_CHECK_STARTED")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Check reference transcript quality for CER")
    parser.add_argument("--alpha", type=str, required=True)
    parser.add_argument("--reference", type=str, required=True)
    args = parser.parse_args()

    alpha_path = Path(args.alpha)
    reference_path = Path(args.reference)
    if not alpha_path.exists() or not reference_path.exists():
        print("FAILED alpha or reference path missing")
        return 1

    report = check_reference_transcript_quality(
        alpha_path.read_text(encoding="utf-8"),
        reference_path.read_text(encoding="utf-8"),
        alpha_path=str(alpha_path),
        reference_path=str(reference_path),
    )
    json_path, txt_path = _write_report(report)
    report["reference_quality_report_path"] = str(json_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from alpha.utils.accuracy_report_sync import sync_latest_accuracy_reports

        sync_latest_accuracy_reports(
            alpha_path=str(alpha_path),
            reference_path=str(reference_path),
        )
    except Exception:
        pass

    verdict = report["verdict"]
    if verdict == "valid_for_cer":
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("REFERENCE_TRANSCRIPT_VALID_FOR_CER", path=str(reference_path))
        except Exception:
            pass
    elif verdict == "questionable_for_cer":
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("REFERENCE_TRANSCRIPT_QUESTIONABLE_FOR_CER", path=str(reference_path))
        except Exception:
            pass
    else:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("REFERENCE_TRANSCRIPT_INVALID_FOR_CER", path=str(reference_path))
        except Exception:
            pass
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("REFERENCE_QUALITY_REPORT_WRITTEN", path=str(json_path))
    except Exception:
        pass

    print(f"verdict={verdict} should_run_cer={report['should_run_cer']}")
    print(f"report_json={json_path}")
    print(f"report_txt={txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
