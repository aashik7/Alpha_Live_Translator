"""Offline Alpha/reference diagnostics (8.5.23.3).

This tool is analysis-only:
- no runtime mutation
- no auto-correction
- no raw Deepgram mutation
"""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from alpha.constants import (
    APP_VERSION,
    ALIGNMENT_COVERAGE_REPAIR_852341_ENABLED,
    BUSINESS_TERM_RISK_REPORT_ENABLED,
    GLOSSARY_CANDIDATE_REPORT_ENABLED,
    JAPANESE_BOUNDARY_DIAGNOSIS_ENABLED,
    REFERENCE_ALIGNMENT_DIAGNOSIS_ENABLED,
    REFERENCE_ALPHA_HASH_BINDING_ENABLED,
    REFERENCE_CLEANUP_SUGGESTIONS_ENABLED,
)
from reference_transcript_quality_check import (
    _write_report as write_reference_quality_report,
    check_reference_transcript_quality,
)

_JP_CHAR_RE = re.compile(r"[一-龯ぁ-んァ-ヶー]")
_SPEAKER_RE = re.compile(r"^\[Speaker\s+\d+\]\s*")
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#+\s*")
_BULLET_RE = re.compile(r"^\s*[-*•]\s+")
_NUMBERED_RE = re.compile(r"^\s*\d+[\).、．]\s*")
_TIMESTAMP_RE = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?\s*$")
_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")

_BUSINESS_TERMS = (
    "他社",
    "他者",
    "自社",
    "弊社",
    "御社",
    "名刺",
    "名詞",
    "名称",
    "紹介",
    "営業部長",
    "開発部長",
    "部長",
    "担当",
    "名刺交換",
    "頂戴いたします",
    "よろしくお願いいたします",
    "お世話になっております",
    "申し訳ございません",
    "あいにく",
    "本日",
    "切らしておりまして",
    "ございます",
    "いらっしゃいます",
)
_SUSPICIOUS_TERM_VARIANTS = {
    "他社": ("他者",),
    "他者": ("他社",),
    "名刺": ("名詞", "名称"),
    "名詞": ("名刺", "名称"),
    "名称": ("名刺", "名詞"),
}
_LEADING_PARTICLES = (
    "が",
    "は",
    "て",
    "で",
    "に",
    "の",
    "から",
    "と",
    "か",
    "し",
    "では",
    "そして",
    "つまり",
    "ですから",
    "なので",
)
_CONTINUATION_ENDINGS = (
    "ます",
    "ですが",
    "ので",
    "から",
    "て",
    "で",
    "と",
    "けど",
    "たり",
    "つつ",
)
_INTENTIONAL_SHORT = {"はい", "いいえ", "ええ", "そうです", "そうですね", "はい。", "いいえ。"}


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _normalize_spaces(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u3000", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def _strip_timestamp_prefix(line: str) -> str:
    return re.sub(r"^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*", "", line)


def _alpha_lines_with_meta(alpha_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, raw in enumerate(alpha_text.splitlines(), start=1):
        if not raw.strip():
            continue
        line = _normalize_spaces(_strip_timestamp_prefix(raw))
        line = _SPEAKER_RE.sub("", line).strip()
        if not line:
            continue
        rows.append({"line_number": idx, "original": raw, "normalized": line})
    return rows


def _reference_lines_with_hints(reference_text: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    rows: list[dict[str, Any]] = []
    headings: list[str] = []
    formatting_flags: list[str] = []
    for idx, raw in enumerate(reference_text.splitlines(), start=1):
        if not raw.strip():
            continue
        line = _normalize_spaces(raw)
        if _MARKDOWN_HEADING_RE.match(line):
            headings.append(_MARKDOWN_HEADING_RE.sub("", line).strip() or f"section_{idx}")
            formatting_flags.append("markdown_headings")
            line = _MARKDOWN_HEADING_RE.sub("", line).strip()
        if _BULLET_RE.match(line):
            formatting_flags.append("bullet_points")
            line = _BULLET_RE.sub("", line).strip()
        if _NUMBERED_RE.match(line):
            formatting_flags.append("numbered_outline")
            line = _NUMBERED_RE.sub("", line).strip()
        if _TIMESTAMP_RE.match(line):
            formatting_flags.append("timestamp_only_lines")
            continue
        line = _BOLD_RE.sub(r"\1", line)
        line = _normalize_spaces(line)
        if not line:
            continue
        rows.append({"line_number": idx, "original": raw, "normalized": line})
    return rows, headings, sorted(set(formatting_flags))


def _chars_only_japanese(text: str) -> str:
    return "".join(ch for ch in text if _JP_CHAR_RE.match(ch))


def _line_shared_terms(alpha_line: str, ref_line: str) -> list[str]:
    terms = []
    for term in _BUSINESS_TERMS:
        if term in alpha_line and term in ref_line:
            terms.append(term)
    return terms


def _line_overlap_score(alpha_line: str, ref_line: str) -> float:
    a = set(_chars_only_japanese(alpha_line))
    b = set(_chars_only_japanese(ref_line))
    if not a or not b:
        return 0.0
    return round(len(a & b) / max(len(a | b), 1), 4)


def _confidence_from_overlap(overlap: float, shared_terms: list[str]) -> str:
    if overlap > 0.70 and shared_terms:
        return "high"
    if overlap >= 0.50:
        return "medium"
    return "low"


def _compute_coverage(
    alpha_rows: list[dict[str, Any]],
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    aligned_line_numbers: set[int] = set()
    for section in sections:
        start = section.get("alpha_line_start")
        end = section.get("alpha_line_end")
        if start is None or end is None:
            continue
        if section.get("confidence") == "low" and float(section.get("overlap_score", 0)) < 0.08:
            continue
        for ln in range(int(start), int(end) + 1):
            aligned_line_numbers.add(ln)
    total = len(alpha_rows)
    aligned = sum(1 for r in alpha_rows if r["line_number"] in aligned_line_numbers)
    unaligned = max(0, total - aligned)
    ratio = round(unaligned / max(total, 1), 4)
    overlaps = [float(s.get("overlap_score", 0)) for s in sections if s.get("alpha_line_start") is not None]
    avg_overlap = round(sum(overlaps) / max(len(overlaps), 1), 4) if overlaps else 0.0
    min_overlap = round(min(overlaps), 4) if overlaps else 0.0
    conf_dist = Counter(str(s.get("confidence", "low")) for s in sections)
    return {
        "aligned_alpha_line_count": aligned,
        "unaligned_alpha_line_count": unaligned,
        "total_alpha_line_count": total,
        "unaligned_alpha_ratio": ratio,
        "average_section_overlap_score": avg_overlap,
        "min_section_overlap_score": min_overlap,
        "alignment_confidence_distribution": dict(conf_dist),
    }


def _integrity_verdict(
    coverage: dict[str, Any],
    *,
    alignment_mode: str,
    order_violations: int,
    missing_sections: int,
) -> tuple[str, str, str]:
    ratio = float(coverage.get("unaligned_alpha_ratio", 1.0))
    avg = float(coverage.get("average_section_overlap_score", 0.0))
    if alignment_mode == "qualitative_only" or ratio > 0.75 or avg < 0.20:
        return "invalid", "weak", "Do not use CER for product decisions."
    if ratio > 0.25 or order_violations > 0 or avg < 0.50 or missing_sections > 0:
        return "weak", "weak", "Alignment coverage insufficient for trusted CER."
    if ratio > 0.10 or avg < 0.70:
        return "acceptable", "acceptable", "Usable with caution; verify section coverage."
    return "strong", "strong", "Alignment coverage acceptable for benchmark scoring."


def _build_alignment(
    alpha_rows: list[dict[str, Any]],
    ref_rows: list[dict[str, Any]],
    headings: list[str],
    verdict: str,
) -> dict[str, Any]:
    _jp_log("MONOTONIC_ALIGNMENT_STARTED")
    sections: list[dict[str, Any]] = []
    alpha_used: set[int] = set()
    missing_sections = 0
    order_violations = 0
    last_alpha_index = -1

    if verdict == "valid_for_cer":
        mode = "verbatim"
        search_start = 0
        for idx, ref in enumerate(ref_rows, start=1):
            best_i = -1
            best_score = 0.0
            for i in range(search_start, len(alpha_rows)):
                if i in alpha_used:
                    continue
                alpha = alpha_rows[i]
                score = _line_overlap_score(alpha["normalized"], ref["normalized"])
                shared = _line_shared_terms(alpha["normalized"], ref["normalized"])
                if shared:
                    score = round(min(1.0, score + 0.15), 4)
                if score > best_score:
                    best_score = score
                    best_i = i
            if best_i < 0 or best_score < 0.08:
                missing_sections += 1
                sections.append(
                    {
                        "section_id": f"section_{idx}",
                        "reference_heading_or_hint": headings[min(idx - 1, len(headings) - 1)] if headings else "",
                        "alpha_line_start": None,
                        "alpha_line_end": None,
                        "reference_excerpt": ref["normalized"][:120],
                        "alpha_excerpt": "",
                        "shared_terms": [],
                        "overlap_score": best_score,
                        "confidence": "low",
                        "notes": "No reliable alpha alignment found",
                    }
                )
                continue
            if best_i < last_alpha_index:
                order_violations += 1
                _jp_log(
                    "MONOTONIC_ALIGNMENT_ORDER_VIOLATION",
                    section=idx,
                    alpha_index=best_i,
                    previous=last_alpha_index,
                )
            prev_index = last_alpha_index
            alpha_used.add(best_i)
            last_alpha_index = max(last_alpha_index, best_i)
            search_start = best_i + 1
            alpha = alpha_rows[best_i]
            shared = _line_shared_terms(alpha["normalized"], ref["normalized"])
            confidence = _confidence_from_overlap(best_score, shared)
            if not shared and confidence == "high":
                confidence = "medium"
                _jp_log("ALIGNMENT_CONFIDENCE_DOWNGRADED", section=idx, reason="no_shared_terms")
            if prev_index >= 0 and best_i - prev_index > 8:
                confidence = "low"
                _jp_log("ALIGNMENT_CONFIDENCE_DOWNGRADED", section=idx, reason="mapping_jump")
            sections.append(
                {
                    "section_id": f"section_{idx}",
                    "reference_heading_or_hint": headings[min(idx - 1, len(headings) - 1)] if headings else "",
                    "alpha_line_start": alpha["line_number"],
                    "alpha_line_end": alpha["line_number"],
                    "reference_excerpt": ref["normalized"][:120],
                    "alpha_excerpt": alpha["normalized"][:120],
                    "shared_terms": shared,
                    "overlap_score": best_score,
                    "confidence": confidence,
                    "notes": "Monotonic sequential alignment",
                }
            )
    else:
        if verdict == "questionable_for_cer":
            mode = "section_level"
        else:
            mode = "qualitative_only"
        for idx, ref in enumerate(ref_rows, start=1):
            best_i = -1
            best_score = 0.0
            for i, alpha in enumerate(alpha_rows):
                if i in alpha_used:
                    continue
                score = _line_overlap_score(alpha["normalized"], ref["normalized"])
                if _line_shared_terms(alpha["normalized"], ref["normalized"]):
                    score = round(min(1.0, score + 0.15), 4)
                if score > best_score:
                    best_score = score
                    best_i = i
            if best_i < 0 or best_score < 0.08:
                missing_sections += 1
                sections.append(
                    {
                        "section_id": f"section_{idx}",
                        "reference_heading_or_hint": headings[min(idx - 1, len(headings) - 1)] if headings else "",
                        "alpha_line_start": None,
                        "alpha_line_end": None,
                        "reference_excerpt": ref["normalized"][:120],
                        "alpha_excerpt": "",
                        "shared_terms": [],
                        "overlap_score": best_score,
                        "confidence": "low",
                        "notes": "No reliable alpha alignment found",
                    }
                )
                continue
            if best_i < last_alpha_index:
                order_violations += 1
            alpha_used.add(best_i)
            last_alpha_index = max(last_alpha_index, best_i)
            alpha = alpha_rows[best_i]
            shared = _line_shared_terms(alpha["normalized"], ref["normalized"])
            confidence = _confidence_from_overlap(best_score, shared)
            sections.append(
                {
                    "section_id": f"section_{idx}",
                    "reference_heading_or_hint": headings[min(idx - 1, len(headings) - 1)] if headings else "",
                    "alpha_line_start": alpha["line_number"],
                    "alpha_line_end": alpha["line_number"],
                    "reference_excerpt": ref["normalized"][:120],
                    "alpha_excerpt": alpha["normalized"][:120],
                    "shared_terms": shared,
                    "overlap_score": best_score,
                    "confidence": confidence,
                    "notes": "Soft anchor alignment",
                }
            )

    extra_alpha_sections = max(0, len(alpha_rows) - len(alpha_used))
    coverage = _compute_coverage(alpha_rows, sections)
    _jp_log("ALIGNMENT_COVERAGE_CALCULATED", **coverage)
    integrity, coverage_verdict, recommendation = _integrity_verdict(
        coverage,
        alignment_mode=mode,
        order_violations=order_violations,
        missing_sections=missing_sections,
    )
    coverage_warning = ""
    if extra_alpha_sections > max(3, len(ref_rows)):
        coverage_warning = "Alpha contains substantial content outside reference sections."
    if len(ref_rows) > len(alpha_rows) * 2:
        coverage_warning = (coverage_warning + " Reference may cover more audio than Alpha captured.").strip()
    if integrity == "strong":
        _jp_log("ALIGNMENT_INTEGRITY_STRONG")
    elif integrity == "invalid":
        _jp_log("ALIGNMENT_INTEGRITY_INVALID")
    else:
        _jp_log("ALIGNMENT_INTEGRITY_WEAK")
    return {
        "alignment_mode": mode,
        "sections": sections,
        "missing_sections_count": missing_sections,
        "extra_alpha_sections_count": extra_alpha_sections,
        "alignment_order_violations": order_violations,
        "alignment_integrity_verdict": integrity,
        "alignment_coverage_verdict": coverage_verdict,
        "coverage_warning": coverage_warning,
        "decision_recommendation": recommendation,
        **coverage,
    }


def _boundary_diagnosis(alpha_rows: list[dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for i, row in enumerate(alpha_rows):
        cur = row["normalized"]
        prev = alpha_rows[i - 1]["normalized"] if i > 0 else ""
        nxt = alpha_rows[i + 1]["normalized"] if i + 1 < len(alpha_rows) else ""

        def add(pattern_id: str, severity: str, cause: str, recommendation: str) -> None:
            findings.append(
                {
                    "line_number": row["line_number"],
                    "previous_line": prev,
                    "current_line": cur,
                    "next_line": nxt,
                    "pattern_id": pattern_id,
                    "severity": severity,
                    "likely_cause": cause,
                    "recommendation": recommendation,
                }
            )

        if any(cur.startswith(p) for p in _LEADING_PARTICLES):
            add("leading_fragment_particle", "medium", "assembler_boundary", "assembler_candidate")
        if prev and any(prev.endswith(e) for e in _CONTINUATION_ENDINGS):
            add("clause_continuation_risk", "low", "assembler_boundary", "needs_reference")
        if ("ございます" in cur and "こちら" in cur) or ("ます" in cur and "では" in cur) or ("ください" in cur and " " in cur):
            if "。" not in cur:
                add("glue_missing_boundary", "medium", "assembler_boundary", "assembler_candidate")
        if any(tok in cur for tok in ("。、", "、。", "。。", "、、")):
            add("midline_punctuation_artifact", "medium", "punctuation_merge", "audit_only")
        if i > 0:
            prev_cur = alpha_rows[i - 1]["normalized"]
            if prev_cur and (cur.startswith(prev_cur[: min(15, len(prev_cur))]) or prev_cur.startswith(cur[: min(15, len(cur))])):
                if cur != prev_cur:
                    add("duplicate_continuation", "medium", "assembler_boundary", "assembler_candidate")
        if len(_chars_only_japanese(cur)) < 8 and cur not in _INTENTIONAL_SHORT:
            add("too_short_unstable_line", "low", "raw_stt", "needs_reference")
        if cur.endswith(("て", "で", "と", "が", "は", "の")):
            add("stop_tail_incomplete_risk", "low", "raw_stt", "needs_reference")

    summary = {
        "total_boundary_risks": len(findings),
        "leading_fragment_count": sum(1 for f in findings if f["pattern_id"] == "leading_fragment_particle"),
        "glue_risk_count": sum(1 for f in findings if f["pattern_id"] == "glue_missing_boundary"),
        "punctuation_artifact_count": sum(1 for f in findings if f["pattern_id"] == "midline_punctuation_artifact"),
        "duplicate_continuation_count": sum(1 for f in findings if f["pattern_id"] == "duplicate_continuation"),
        "assembler_candidate_count": sum(1 for f in findings if f["recommendation"] == "assembler_candidate"),
        "raw_stt_likely_count": sum(1 for f in findings if f["likely_cause"] == "raw_stt"),
        "needs_reference_count": sum(1 for f in findings if f["recommendation"] == "needs_reference"),
    }
    return {"findings": findings, **summary}


def _term_line_numbers(rows: list[dict[str, Any]], term: str) -> list[int]:
    return [r["line_number"] for r in rows if term in r["normalized"]]


def _business_term_report(alpha_rows: list[dict[str, Any]], ref_rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    alpha_text = "\n".join(r["normalized"] for r in alpha_rows)
    ref_text = "\n".join(r["normalized"] for r in ref_rows)
    for term in _BUSINESS_TERMS:
        expected = [term] if term in ref_text else []
        observed = [term] if term in alpha_text else []
        suspicious = [v for v in _SUSPICIOUS_TERM_VARIANTS.get(term, ()) if v in alpha_text]
        lines = _term_line_numbers(alpha_rows, term)
        confidence = "high" if observed and expected else ("medium" if observed or expected else "low")
        needs_review = bool(suspicious) or (expected and not observed)
        items.append(
            {
                "term": term,
                "expected_terms_from_reference": expected,
                "observed_terms_in_alpha": observed,
                "suspicious_variants": suspicious,
                "line_numbers": lines,
                "confidence": confidence,
                "correction_allowed": False,
                "glossary_candidate": bool(expected or observed),
                "needs_human_review": needs_review,
            }
        )
    return {
        "app_version": APP_VERSION,
        "business_term_risk_report_enabled": BUSINESS_TERM_RISK_REPORT_ENABLED,
        "items": items,
        "business_term_risk_count": sum(1 for i in items if i["needs_human_review"]),
    }


def _extract_candidates(alpha_rows: list[dict[str, Any]], ref_rows: list[dict[str, Any]]) -> dict[str, Any]:
    alpha_text = "\n".join(r["normalized"] for r in alpha_rows)
    ref_text = "\n".join(r["normalized"] for r in ref_rows)

    def freq(text: str, term: str) -> int:
        return text.count(term)

    fixed_terms = [
        ("チン", "participant_names"),
        ("永井", "participant_names"),
        ("江藤", "participant_names"),
        ("営業部長", "roles/titles"),
        ("開発部長", "roles/titles"),
        ("他社", "business_terms"),
        ("自社", "business_terms"),
        ("弊社", "business_terms"),
        ("名刺交換", "business_terms"),
        ("頂戴いたします", "recurring_phrases"),
        ("あいにく", "recurring_phrases"),
        ("切らしておりまして", "recurring_phrases"),
    ]
    items = []
    for text, category in fixed_terms:
        fa = freq(alpha_text, text)
        fr = freq(ref_text, text)
        if fa == 0 and fr == 0:
            continue
        source = "both" if fa and fr else ("alpha" if fa else "reference")
        items.append(
            {
                "text": text,
                "category": category,
                "source": source,
                "frequency_alpha": fa,
                "frequency_reference": fr,
                "confidence": "high" if source == "both" else "medium",
                "risk_notes": "candidate only",
                "suggested_glossary_entry": text,
                "auto_apply": False,
            }
        )

    counts_by_cat = Counter(i["category"] for i in items)
    return {
        "app_version": APP_VERSION,
        "glossary_candidate_report_enabled": GLOSSARY_CANDIDATE_REPORT_ENABLED,
        "candidates": items,
        "glossary_candidate_count": len(items),
        "category_counts": dict(counts_by_cat),
    }


def _suggested_glossary(candidates: dict[str, Any]) -> dict[str, Any]:
    out = {
        "participants": [],
        "companies": [],
        "departments": [],
        "business_terms": [],
        "custom_phrases": [],
        "do_not_correct": [],
        "source": "generated_by_analyze_alpha_vs_reference",
        "auto_apply": False,
    }
    for item in candidates.get("candidates", []):
        txt = item["text"]
        cat = item["category"]
        if cat == "participant_names":
            out["participants"].append(txt)
            out["do_not_correct"].append(txt)
        elif cat in ("roles/titles", "departments"):
            out["departments"].append(txt)
        elif cat == "business_terms":
            out["business_terms"].append(txt)
        else:
            out["custom_phrases"].append(txt)
    for key in out:
        if isinstance(out[key], list):
            out[key] = sorted(set(out[key]))
    return out


def _cleanup_suggestions_text(quality: dict[str, Any]) -> str:
    verdict = quality.get("verdict", "invalid_for_cer")
    lines = [
        "Reference Cleanup Suggestions",
        f"verdict: {verdict}",
        f"formatting_risk_flags: {', '.join(quality.get('formatting_risk_flags', [])) or 'none'}",
        "",
    ]
    if verdict == "valid_for_cer":
        lines.append("Reference appears usable for trusted CER.")
    else:
        lines.extend(
            [
                "Reference is still useful for qualitative/section alignment.",
                "Reference is not ideal for trusted CER in current form.",
                "",
                "How to convert to verbatim transcript:",
                "- Use exact spoken wording from audio",
                "- Keep original speech order",
                "- Remove markdown headings",
                "- Remove bullets and summary notes",
                "- Keep full spoken sentences",
                "",
                "Recommended format example:",
                "みなさんこんにちは。第3回目のビジネス会話クラス、お疲れさまでした。",
                "今回も第3回目の授業の内容をこちらのビデオで復習していきたいと思います。",
            ]
        )
    return "\n".join(lines) + "\n"


def _resolve_alpha_path(args: argparse.Namespace) -> Path | None:
    """Require explicit --alpha/--final/--run-folder; never silent latest_* fallback."""
    if getattr(args, "alpha", None):
        return Path(args.alpha)
    if getattr(args, "final", None):
        return Path(args.final)
    if getattr(args, "run_folder", None):
        return Path(args.run_folder) / "transcripts" / "Alpha_output_FINAL.txt"
    return None


def analyze(
    alpha_path: Path,
    reference_path: Path,
    out_dir: Path,
    *,
    visible_audit_path: Path | None = None,
) -> dict[str, str]:
    _jp_log("ALPHA_REFERENCE_ANALYSIS_STARTED", alpha=str(alpha_path), reference=str(reference_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    alpha_text = alpha_path.read_text(encoding="utf-8")
    ref_text = reference_path.read_text(encoding="utf-8")
    alpha_rows = _alpha_lines_with_meta(alpha_text)
    alpha_line_texts = [r.get("text", "") for r in alpha_rows if r.get("text")]
    try:
        from alpha.transcription.final_output_cleanup import detect_cumulative_alpha_lines_v2

        _jp_log("SCORING_RESIDUAL_DUPLICATE_SCAN_STARTED")
        cum_scan = detect_cumulative_alpha_lines_v2(alpha_line_texts)
        _jp_log("SCORING_RESIDUAL_DUPLICATE_SCAN_COMPLETED", count=cum_scan.get("cumulative_duplicate_count", 0))
        _jp_log("SCORING_PUNCTUATION_ARTIFACT_SCAN_STARTED")
        _jp_log(
            "SCORING_PUNCTUATION_ARTIFACT_SCAN_COMPLETED",
            count=cum_scan.get("punctuation_artifact_count", 0),
        )
    except Exception:
        cum_scan = {}
    ref_rows, headings, ref_flags = _reference_lines_with_hints(ref_text)

    quality = check_reference_transcript_quality(
        alpha_text, ref_text, alpha_path=str(alpha_path), reference_path=str(reference_path)
    )
    q_json, _ = write_reference_quality_report(quality)
    quality["reference_quality_report_path"] = str(q_json)
    q_json.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")

    alignment = _build_alignment(alpha_rows, ref_rows, headings, str(quality.get("verdict", "")))
    alignment_v2: dict[str, Any] = {}
    if ALIGNMENT_COVERAGE_REPAIR_852341_ENABLED:
        try:
            from alpha.utils.alignment_v2 import run_alignment_v2

            alignment_v2 = run_alignment_v2(alpha_rows, ref_rows)
        except Exception:
            pass
    alignment_report = {
        "app_version": APP_VERSION,
        "reference_alignment_diagnosis_enabled": REFERENCE_ALIGNMENT_DIAGNOSIS_ENABLED,
        "alpha_path": str(alpha_path),
        "reference_path": str(reference_path),
        "reference_quality_verdict": quality["verdict"],
        "trusted_cer_allowed": quality["verdict"] == "valid_for_cer",
        "qualitative_alignment_allowed": True,
        "warning": quality["recommendation"],
        "alignment_mode": alignment["alignment_mode"],
        "sections": alignment["sections"],
        "missing_sections_count": alignment["missing_sections_count"],
        "extra_alpha_sections_count": alignment["extra_alpha_sections_count"],
        "reference_formatting_flags_seen_in_alignment": ref_flags,
        "cumulative_duplicate_count": cum_scan.get("cumulative_duplicate_count", 0),
        "punctuation_artifact_count": cum_scan.get("punctuation_artifact_count", 0),
        "alpha_output_cumulative_duplicate_suspected": cum_scan.get(
            "alpha_output_cumulative_duplicate_suspected", False
        ),
        "alpha_output_punctuation_artifact_suspected": cum_scan.get(
            "alpha_output_punctuation_artifact_suspected", False
        ),
        "clean_alpha_export_required": True,
        "clean_alpha_export_confirmed": not cum_scan.get("alpha_output_cumulative_duplicate_suspected", False),
        "corporate_ir_glossary_enabled": True,
        "glossary_path": "troubleshooting/accuracy_benchmark/glossaries/test01_corporate_ir_glossary.json",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "report_type": "alignment_report",
        "alignment_order_violations": alignment.get("alignment_order_violations", 0),
        "alignment_integrity_verdict": alignment.get("alignment_integrity_verdict", ""),
        "alignment_coverage_verdict": alignment.get("alignment_coverage_verdict", ""),
        "alignment_integrity_verdict_v1": alignment.get("alignment_integrity_verdict", ""),
        "alignment_coverage_verdict_v1": alignment.get("alignment_coverage_verdict", ""),
        "unaligned_alpha_ratio": alignment.get("unaligned_alpha_ratio", 0),
        "aligned_alpha_line_count": alignment.get("aligned_alpha_line_count", 0),
        "unaligned_alpha_line_count": alignment.get("unaligned_alpha_line_count", 0),
        "total_alpha_line_count": alignment.get("total_alpha_line_count", 0),
        "average_section_overlap_score": alignment.get("average_section_overlap_score", 0),
        "min_section_overlap_score": alignment.get("min_section_overlap_score", 0),
        "alignment_confidence_distribution": alignment.get("alignment_confidence_distribution", {}),
        "coverage_warning": alignment.get("coverage_warning", ""),
        "decision_recommendation": alignment.get("decision_recommendation", ""),
    }
    if alignment_v2:
        windows = alignment_v2.pop("matched_windows", [])
        alignment_report.update(alignment_v2)
        alignment_report["matched_windows_sample"] = windows[:15]
        _jp_log("ALIGNMENT_V2_REPORT_WRITTEN")
    if REFERENCE_ALPHA_HASH_BINDING_ENABLED:
        from alpha.utils.reference_alpha_hash import bind_report_hashes

        bind_report_hashes(
            alignment_report,
            alpha_path=str(alpha_path),
            reference_path=str(reference_path),
            report_type="alignment_report",
        )
    if visible_audit_path and visible_audit_path.exists():
        try:
            alignment_report["visible_audit_path"] = str(visible_audit_path)
            alignment_report["visible_audit_summary"] = json.loads(
                visible_audit_path.read_text(encoding="utf-8")
            ).get("visible_error_count", 0)
        except Exception:
            pass

    try:
        summary_path = Path("troubleshooting/latest/glossary_correction_summary.json")
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            alignment_report["glossary_keyterm_count"] = summary.get("glossary_keyterm_count", 0)
            alignment_report["glossary_correction_count"] = summary.get("glossary_correction_count", 0)
            alignment_report["glossary_correction_summary_path"] = str(summary_path).replace("\\", "/")
            alignment_report["glossary_correction_decisions_path"] = summary.get("decisions_path", "")
            alignment_report["corporate_term_accuracy_report_path"] = summary.get(
                "corporate_term_accuracy_report_path", ""
            )
            alignment_report["financial_number_accuracy_report_path"] = summary.get(
                "financial_number_accuracy_report_path", ""
            )
            alignment_report["financial_number_correction_count"] = summary.get(
                "financial_number_correction_count", 0
            )
        cov_path = Path("troubleshooting/latest/export_coverage_report.json")
        if cov_path.exists():
            cov = json.loads(cov_path.read_text(encoding="utf-8"))
            alignment_report["clean_export_ready_for_scoring"] = cov.get("clean_export_ready_for_scoring", False)
            alignment_report["export_coverage_report_path"] = str(cov_path).replace("\\", "/")
            alignment_report["valid_segment_loss_count"] = cov.get("valid_segment_loss_count", 0)
            alignment_report["export_coverage_ratio"] = cov.get("export_coverage_ratio", 0)
            alignment_report["source_commit_coverage_ratio"] = cov.get("source_commit_coverage_ratio", 0)
            alignment_report["lineage_coverage_ratio"] = cov.get("lineage_coverage_ratio", 0)
            alignment_report["final_export_contains_pre_correction_lines"] = cov.get(
                "final_export_contains_pre_correction_lines", False
            )
            alignment_report["canonical_export_line_count"] = cov.get("canonical_export_line_count", 0)
        ledger_path = Path("troubleshooting/latest/canonical_transcript_ledger.jsonl")
        if ledger_path.exists():
            alignment_report["canonical_transcript_ledger_path"] = str(ledger_path).replace("\\", "/")
        pre_path = Path("troubleshooting/latest/pre_correction_reentry_report.json")
        if pre_path.exists():
            pre = json.loads(pre_path.read_text(encoding="utf-8"))
            alignment_report["pre_correction_reentry_blocked_count"] = pre.get("pre_correction_blocked_count", 0)
            alignment_report["pre_correction_reentry_report_path"] = str(pre_path).replace("\\", "/")
        clean_path = Path("troubleshooting/latest/clean_active_transcript.jsonl")
        if clean_path.exists():
            alignment_report["clean_active_transcript_line_count"] = sum(
                1 for ln in clean_path.read_text(encoding="utf-8").splitlines() if ln.strip()
            )
        alignment_report["canonical_transcript_lineage_enabled"] = True
        alignment_report["final_export_lock_enabled"] = True
        _jp_log("ALIGNMENT_GLOSSARY_CANONICAL_METADATA_INCLUDED")
        _jp_log("ALIGNMENT_EXPORT_COVERAGE_METADATA_INCLUDED")
    except Exception:
        pass

    boundary = _boundary_diagnosis(alpha_rows)
    boundary_report = {
        "app_version": APP_VERSION,
        "japanese_boundary_diagnosis_enabled": JAPANESE_BOUNDARY_DIAGNOSIS_ENABLED,
        "alpha_path": str(alpha_path),
        "reference_path": str(reference_path),
        "findings": boundary["findings"],
        "total_boundary_risks": boundary["total_boundary_risks"],
        "leading_fragment_count": boundary["leading_fragment_count"],
        "glue_risk_count": boundary["glue_risk_count"],
        "punctuation_artifact_count": boundary["punctuation_artifact_count"],
        "duplicate_continuation_count": boundary["duplicate_continuation_count"],
        "assembler_candidate_count": boundary["assembler_candidate_count"],
        "raw_stt_likely_count": boundary["raw_stt_likely_count"],
        "needs_reference_count": boundary["needs_reference_count"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "report_type": "boundary_error_report",
    }
    if REFERENCE_ALPHA_HASH_BINDING_ENABLED:
        from alpha.utils.reference_alpha_hash import bind_report_hashes

        bind_report_hashes(
            boundary_report,
            alpha_path=str(alpha_path),
            reference_path=str(reference_path),
            report_type="boundary_error_report",
        )

    business = _business_term_report(alpha_rows, ref_rows)
    glossary = _extract_candidates(alpha_rows, ref_rows)
    suggested_glossary = _suggested_glossary(glossary)
    cleanup_suggestions = _cleanup_suggestions_text(quality)

    alignment_json = out_dir / f"{stamp}_alignment_report.json"
    alignment_txt = out_dir / f"{stamp}_alignment_report.txt"
    boundary_json = out_dir / f"{stamp}_boundary_error_report.json"
    business_json = out_dir / f"{stamp}_business_term_risk_report.json"
    glossary_json = out_dir / f"{stamp}_glossary_candidates.json"
    cleanup_txt = out_dir / f"{stamp}_reference_cleanup_suggestions.txt"
    suggested_glossary_path = (
        Path("troubleshooting/accuracy_benchmark") / f"suggested_meeting_glossary_{stamp}.json"
    )
    suggested_glossary_path.parent.mkdir(parents=True, exist_ok=True)

    alignment_json.write_text(json.dumps(alignment_report, ensure_ascii=False, indent=2), encoding="utf-8")
    alignment_txt.write_text(
        "\n".join(
            [
                "Alpha/Reference Alignment Summary",
                f"reference_quality_verdict: {alignment_report['reference_quality_verdict']}",
                f"alignment_mode: {alignment_report['alignment_mode']}",
                f"missing_sections: {alignment_report['missing_sections_count']}",
                f"extra_alpha_sections: {alignment_report['extra_alpha_sections_count']}",
                "",
                "Sections:",
            ]
            + [
                (
                    f"- {s['section_id']}: alpha_lines={s['alpha_line_start']}..{s['alpha_line_end']} "
                    f"confidence={s['confidence']} overlap={s['overlap_score']} "
                    f"hint={s['reference_heading_or_hint']} note={s['notes']}"
                )
                for s in alignment_report["sections"]
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    boundary_json.write_text(json.dumps(boundary_report, ensure_ascii=False, indent=2), encoding="utf-8")
    business_json.write_text(json.dumps(business, ensure_ascii=False, indent=2), encoding="utf-8")
    glossary_json.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")
    cleanup_txt.write_text(cleanup_suggestions, encoding="utf-8")
    suggested_glossary_path.write_text(
        json.dumps(suggested_glossary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _jp_log("ALPHA_REFERENCE_ALIGNMENT_WRITTEN", path=str(alignment_json))
    _jp_log("BOUNDARY_ERROR_REPORT_WRITTEN", path=str(boundary_json))
    _jp_log("BUSINESS_TERM_RISK_REPORT_WRITTEN", path=str(business_json))
    _jp_log("GLOSSARY_CANDIDATES_WRITTEN", path=str(glossary_json))
    _jp_log("REFERENCE_CLEANUP_SUGGESTIONS_WRITTEN", path=str(cleanup_txt))
    _jp_log("ALIGNMENT_GLOSSARY_LAYER_METADATA_INCLUDED")
    _jp_log("ALPHA_REFERENCE_ANALYSIS_COMPLETED")
    try:
        from alpha.utils.accuracy_report_sync import sync_latest_accuracy_reports

        sync_latest_accuracy_reports(
            alpha_path=str(alpha_path),
            reference_path=str(reference_path),
        )
    except Exception:
        pass

    return {
        "alignment_report_json": str(alignment_json),
        "alignment_report_txt": str(alignment_txt),
        "boundary_error_report_json": str(boundary_json),
        "business_term_risk_report_json": str(business_json),
        "glossary_candidates_json": str(glossary_json),
        "reference_cleanup_suggestions_txt": str(cleanup_txt),
        "suggested_meeting_glossary_json": str(suggested_glossary_path),
        "reference_quality_report_json": str(q_json),
        "reference_quality_verdict": str(quality.get("verdict", "")),
        "alignment_mode": alignment_report["alignment_mode"],
        "trusted_cer_allowed": str(quality.get("verdict", "")) == "valid_for_cer",
        "total_boundary_risks": str(boundary_report["total_boundary_risks"]),
        "assembler_candidate_count": str(boundary_report["assembler_candidate_count"]),
        "business_term_risk_count": str(business.get("business_term_risk_count", 0)),
        "glossary_candidate_count": str(glossary.get("glossary_candidate_count", 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Alpha output vs reference transcript")
    parser.add_argument("--latest", action="store_true", help="DEPRECATED: silent latest fallback removed")
    parser.add_argument("--alpha", type=str, default="", help="Explicit alpha txt path")
    parser.add_argument("--final", type=str, default="", help="Explicit Final alpha txt path")
    parser.add_argument("--raw", type=str, default="", help="Optional raw stage path")
    parser.add_argument("--stable", type=str, default="", help="Optional stable stage path")
    parser.add_argument("--run-folder", type=str, default="", help="Run folder with Alpha_output_FINAL.txt")
    parser.add_argument("--reference", type=str, default="", help="Reference transcript path")
    parser.add_argument("--project-state", type=str, default="", help="Optional PROJECT_STATE.json")
    parser.add_argument("--visible-audit", type=str, default="", help="Optional visible_error_audit.json path")
    parser.add_argument(
        "--out",
        type=str,
        default="troubleshooting/accuracy_benchmark/results/",
        help="Output directory",
    )
    args = parser.parse_args()

    if not args.reference:
        print("FAILED: --reference is required (silent latest_* fallback removed)")
        return 2
    if not (args.alpha or args.final or args.run_folder):
        print(
            "FAILED: require --run-folder+--reference or --alpha/--final/--raw/--stable "
            "with --reference; silent latest_* fallback removed"
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
    visible_audit = Path(args.visible_audit) if args.visible_audit else None
    outputs = analyze(alpha_path, reference_path, Path(args.out), visible_audit_path=visible_audit)
    print(f"alignment_report={outputs['alignment_report_json']}")
    print(f"boundary_report={outputs['boundary_error_report_json']}")
    print(f"business_report={outputs['business_term_risk_report_json']}")
    print(f"glossary_report={outputs['glossary_candidates_json']}")
    print(f"cleanup_suggestions={outputs['reference_cleanup_suggestions_txt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
