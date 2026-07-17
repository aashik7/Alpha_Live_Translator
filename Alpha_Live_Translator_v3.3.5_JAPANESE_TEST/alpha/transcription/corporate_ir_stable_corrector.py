"""Stable-layer corporate IR glossary and financial number corrector (8.5.25)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from alpha.constants import (
    CORPORATE_IR_GLOSSARY_ENABLED,
    CORPORATE_TERM_AUDIT_ENABLED,
    FINANCIAL_NUMBER_AUDIT_ENABLED,
    FINANCIAL_NUMBER_CORRECTION_ENABLED,
    GLOSSARY_CORRECTION_DECISION_LOG_ENABLED,
    STABLE_GLOSSARY_CORRECTION_ENABLED,
)
from alpha.transcription.corporate_ir_glossary import (
    build_expected_number_rules,
    build_stable_correction_candidates,
    default_glossary_path,
    glossary_entry_counts,
    load_corporate_ir_glossary,
)


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _strip_speaker(text: str) -> tuple[str, str]:
    m = re.match(r"^(\[Speaker\s+\d+\]\s*)", text or "")
    if m:
        return m.group(1), text[m.end() :]
    return "", text or ""


def _context_ok(text: str, context_any: list[str]) -> bool:
    if not context_any:
        return True
    return any(c in text for c in context_any)


def _do_not_block(text: str, do_not: set[str]) -> bool:
    return any(d in text for d in do_not)


def _apply_term_rules(
    text: str,
    rules: list[dict[str, Any]],
    do_not: set[str],
    *,
    prev_line: str = "",
    next_line: str = "",
) -> tuple[str, list[dict[str, Any]], list[str]]:
    corrections: list[dict[str, Any]] = []
    audit_flags: list[str] = []
    window = f"{prev_line}\n{text}\n{next_line}"
    out = text
    for rule in rules:
        before = rule.get("before", "")
        after = rule.get("after", "")
        if not before or before == after or rule.get("confidence") == "audit":
            if before and before in out and rule.get("confidence") == "audit":
                audit_flags.append(f"detected:{before}")
            continue
        if before not in out:
            continue
        if _do_not_block(out, do_not):
            _jp_log("STABLE_GLOSSARY_CORRECTION_SKIPPED_DO_NOT_CORRECT", before=before)
            continue
        ctx = rule.get("context_any") or []
        if ctx and not _context_ok(window, ctx):
            _jp_log("STABLE_GLOSSARY_CORRECTION_SKIPPED_LOW_CONFIDENCE", before=before, reason="context")
            continue
        new_out = out.replace(before, after)
        if new_out != out:
            corrections.append(
                {
                    "correction_type": "glossary_term",
                    "before": before,
                    "after": after,
                    "glossary_category": rule.get("category", ""),
                    "context_terms": ctx,
                    "confidence": rule.get("confidence", "high"),
                    "risk_level": "low",
                }
            )
            out = new_out
            _jp_log("STABLE_GLOSSARY_CORRECTION_APPLIED", before=before, after=after)
    return out, corrections, audit_flags


def _number_pattern_variants(expected: str) -> list[str]:
    variants = [expected]
    variants.append(expected.replace(",", "").replace("，", ""))
    variants.append(expected.replace("%", "パーセント"))
    variants.append(expected.replace("%", "％"))
    return list(dict.fromkeys(v for v in variants if v))


def _apply_number_rules(
    text: str,
    number_rules: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    from alpha.transcription.financial_number_safety import (
        apply_safe_financial_number_correction,
        audit_financial_text,
    )

    corrections: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    metrics = {
        "financial_number_correction_attempt_count": 0,
        "financial_number_correction_applied_count": 0,
        "financial_number_correction_blocked_count": 0,
        "dangerous_correction_blocked_count": 0,
    }
    out = text
    if not FINANCIAL_NUMBER_CORRECTION_ENABLED:
        return out, corrections, audits, metrics

    _jp_log("FINANCIAL_NUMBER_AUDIT_STARTED")
    for rule in number_rules:
        label = rule.get("label", "")
        expected = rule.get("expected", "")
        context_terms = rule.get("context_terms") or []
        if not expected:
            continue
        if context_terms and not any(c in out for c in context_terms):
            continue
        _jp_log("FINANCIAL_NUMBER_CONTEXT_MATCHED", label=label)

        search_patterns = [expected] + list(rule.get("aliases") or [])
        applied = False
        for alias in search_patterns:
            if not alias or alias == expected or alias not in out:
                continue
            metrics["financial_number_correction_attempt_count"] += 1
            new_out, decision = apply_safe_financial_number_correction(
                out,
                alias=alias,
                expected=expected,
                label=label,
                context_terms=context_terms,
            )
            if decision and decision.get("blocked"):
                metrics["financial_number_correction_blocked_count"] += 1
                metrics["dangerous_correction_blocked_count"] += 1
                _jp_log("DANGEROUS_FINANCIAL_CORRECTION_BLOCKED", label=label, alias=alias)
                audits.append({**decision, "label": label})
                continue
            if decision and new_out != out:
                corrections.append(
                    {
                        "correction_type": "financial_number",
                        "before": decision.get("before", alias),
                        "after": decision.get("after", expected),
                        "candidate_text": alias,
                        "final_text": decision.get("after", expected),
                        "glossary_category": "expected_numbers",
                        "context_terms": context_terms,
                        "confidence": "high",
                        "risk_level": "low",
                        "validation_status": decision.get("validation_status", "safe"),
                        "label": label,
                    }
                )
                out = new_out
                metrics["financial_number_correction_applied_count"] += 1
                applied = True
                break
        if not applied:
            import re

            num_like = re.findall(r"[0-9０-９一二三四五六七八九十百千万億兆％%\.]+", out)
            if num_like and any(c in out for c in context_terms):
                audits.append({"label": label, "expected": expected, "detected_fragments": num_like[:3]})
            else:
                _jp_log("FINANCIAL_NUMBER_CORRECTION_SKIPPED_UNCERTAIN", label=label)

    audit = audit_financial_text(out)
    if audit.get("malformed_numeric_output_count", 0) > 0:
        metrics["dangerous_correction_blocked_count"] += audit["malformed_numeric_output_count"]
        _jp_log("MALFORMED_NUMERIC_OUTPUT_DETECTED", count=audit["malformed_numeric_output_count"])

    _jp_log("FINANCIAL_NUMBER_AUDIT_COMPLETED", corrected=len(corrections), audited=len(audits))
    return out, corrections, audits, metrics


def correct_stable_line(
    text: str,
    *,
    glossary: dict[str, Any],
    term_rules: list[dict[str, Any]],
    number_rules: list[dict[str, Any]],
    prev_line: str = "",
    next_line: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    prefix, body = _strip_speaker(text)
    do_not = set(glossary.get("do_not_correct") or [])
    all_corrections: list[dict[str, Any]] = []
    audit_flags: list[str] = []

    if STABLE_GLOSSARY_CORRECTION_ENABLED and glossary.get("auto_apply", True):
        body, term_corr, term_audit = _apply_term_rules(
            body, term_rules, do_not, prev_line=prev_line, next_line=next_line
        )
        all_corrections.extend(term_corr)
        audit_flags.extend(term_audit)

    number_audits: list[dict[str, Any]] = []
    fin_metrics: dict[str, int] = {}
    if FINANCIAL_NUMBER_AUDIT_ENABLED:
        body, num_corr, number_audits, fin_metrics = _apply_number_rules(body, number_rules)
        all_corrections.extend(num_corr)

    output = f"{prefix}{body}" if prefix else body
    return {
        "input_text": text,
        "output_text": output,
        "corrections": all_corrections,
        "audit_flags": audit_flags,
        "number_audits": number_audits,
        "financial_metrics": fin_metrics,
        "correction_allowed": bool(all_corrections),
        "risk_level": "low" if all_corrections else "none",
        "run_id": run_id,
    }


def apply_corporate_ir_stable_corrections(
    lines: list[str],
    *,
    run_folder: Path | None = None,
    glossary_path: Path | None = None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "glossary_enabled": CORPORATE_IR_GLOSSARY_ENABLED,
        "glossary_corrections_count": 0,
        "financial_number_corrections_count": 0,
        "corporate_term_audit_flags_count": 0,
        "company_name_correction_count": 0,
        "financial_term_correction_count": 0,
        "business_term_correction_count": 0,
        "person_name_correction_count": 0,
        "location_correction_count": 0,
        "formal_phrase_correction_count": 0,
        "financial_number_correction_attempt_count": 0,
        "financial_number_correction_applied_count": 0,
        "financial_number_correction_blocked_count": 0,
        "malformed_numeric_output_count": 0,
        "dangerous_correction_blocked_count": 0,
        "dangerous_correction_count": 0,
    }
    if not CORPORATE_IR_GLOSSARY_ENABLED:
        return {"lines": lines, "metrics": metrics, "decisions": [], "reports": {}}

    _jp_log("STABLE_GLOSSARY_CORRECTION_STARTED", line_count=len(lines))
    _jp_log("CORPORATE_IR_LAYER_INTEGRATED_AFTER_BOUNDARY_CLEANUP")

    glossary = load_corporate_ir_glossary(glossary_path or default_glossary_path())
    if not glossary:
        _jp_log("DEEPGRAM_GLOSSARY_KEYTERMS_SKIPPED_NO_GLOSSARY")
        return {"lines": lines, "metrics": metrics, "decisions": [], "reports": {}}

    term_rules = build_stable_correction_candidates(glossary)
    number_rules = build_expected_number_rules(glossary)
    run_id = ""
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if ident:
            run_id = ident.run_id or ""
    except Exception:
        pass

    corrected_lines: list[str] = []
    decisions: list[dict[str, Any]] = []
    category_hits: dict[str, int] = {}
    category_corrections: dict[str, int] = {}
    financial_report = {
        "expected_number_count": len(number_rules),
        "detected_number_count": 0,
        "corrected_number_count": 0,
        "audited_number_count": 0,
        "skipped_uncertain_count": 0,
        "corrections": [],
        "warnings": [],
    }
    corporate_report: dict[str, Any] = {
        "glossary_terms_total": sum(glossary_entry_counts(glossary).values()),
        "glossary_terms_detected": 0,
        "glossary_terms_missing": [],
        "glossary_terms_corrected": 0,
        "company_name_hits": 0,
        "company_name_corrections": 0,
        "financial_term_hits": 0,
        "financial_term_corrections": 0,
        "business_term_hits": 0,
        "business_term_corrections": 0,
        "person_name_hits": 0,
        "person_name_corrections": 0,
        "location_hits": 0,
        "location_corrections": 0,
        "formal_phrase_hits": 0,
        "formal_phrase_corrections": 0,
        "low_confidence_candidates": [],
        "audit_only_candidates": [],
    }

    for i, raw in enumerate(lines):
        prev_ln = lines[i - 1] if i > 0 else ""
        next_ln = lines[i + 1] if i + 1 < len(lines) else ""
        result = correct_stable_line(
            raw,
            glossary=glossary,
            term_rules=term_rules,
            number_rules=number_rules,
            prev_line=prev_ln,
            next_line=next_ln,
            run_id=run_id,
        )
        corrected_lines.append(result["output_text"])
        fm = result.get("financial_metrics") or {}
        for k in (
            "financial_number_correction_attempt_count",
            "financial_number_correction_applied_count",
            "financial_number_correction_blocked_count",
            "dangerous_correction_blocked_count",
        ):
            metrics[k] = metrics.get(k, 0) + fm.get(k, 0)
        for corr in result["corrections"]:
            cat = corr.get("glossary_category", "")
            category_corrections[cat] = category_corrections.get(cat, 0) + 1
            if corr.get("correction_type") == "financial_number":
                metrics["financial_number_corrections_count"] += 1
                financial_report["corrected_number_count"] += 1
                financial_report["corrections"].append(corr)
            else:
                metrics["glossary_corrections_count"] += 1
            decisions.append(
                {
                    "timestamp": time.time(),
                    "run_id": run_id,
                    "input_text": result["input_text"],
                    "output_text": result["output_text"],
                    **corr,
                    "raw_mutation": False,
                    "canonical_line_id": "",
                    "source_commit_ids": [],
                    "represented_source_ids": [],
                    "old_text": corr.get("before", ""),
                    "corrected_text": corr.get("after", ""),
                    "glossary_term": corr.get("before", ""),
                }
            )
        for flag in result.get("audit_flags", []):
            corporate_report["audit_only_candidates"].append(flag)
            metrics["corporate_term_audit_flags_count"] += 1
        for na in result.get("number_audits", []):
            financial_report["audited_number_count"] += 1
            financial_report["warnings"].append(na)

    for field, key in (
        ("company_names", "company_name"),
        ("financial_terms", "financial_term"),
        ("business_terms", "business_term"),
        ("person_names", "person_name"),
        ("locations", "location"),
        ("formal_phrases", "formal_phrase"),
    ):
        for term in glossary.get(field) or []:
            joined = "\n".join(corrected_lines)
            if term in joined:
                corporate_report[f"{key}_hits"] += 1
                category_hits[field] = category_hits.get(field, 0) + 1
            else:
                corporate_report["glossary_terms_missing"].append(term)

    corporate_report["glossary_terms_detected"] = sum(category_hits.values())
    corporate_report["glossary_terms_corrected"] = metrics["glossary_corrections_count"]
    corporate_report["company_name_corrections"] = category_corrections.get("company_names", 0)
    corporate_report["financial_term_corrections"] = category_corrections.get("financial_terms", 0)
    corporate_report["business_term_corrections"] = category_corrections.get("business_terms", 0)
    corporate_report["person_name_corrections"] = category_corrections.get("person_names", 0)
    corporate_report["location_corrections"] = category_corrections.get("locations", 0)
    corporate_report["formal_phrase_corrections"] = category_corrections.get("formal_phrases", 0)

    metrics.update(
        {
            "company_name_correction_count": corporate_report["company_name_corrections"],
            "financial_term_correction_count": corporate_report["financial_term_corrections"],
            "business_term_correction_count": corporate_report["business_term_corrections"],
            "person_name_correction_count": corporate_report["person_name_corrections"],
            "location_correction_count": corporate_report["location_corrections"],
            "formal_phrase_correction_count": corporate_report["formal_phrase_corrections"],
            "glossary_path": str(glossary_path or default_glossary_path()).replace("\\", "/"),
            "dangerous_correction_count": metrics.get("dangerous_correction_blocked_count", 0),
        }
    )

    reports: dict[str, str] = {}
    if run_folder:
        run_folder = Path(run_folder)
        acc = run_folder / "accuracy"
        acc.mkdir(parents=True, exist_ok=True)
        if GLOSSARY_CORRECTION_DECISION_LOG_ENABLED:
            dec_path = acc / "glossary_correction_decisions.jsonl"
            dec_path.write_text(
                "\n".join(json.dumps(d, ensure_ascii=False) for d in decisions) + ("\n" if decisions else ""),
                encoding="utf-8",
            )
            reports["glossary_correction_decisions_path"] = str(dec_path).replace("\\", "/")
        if FINANCIAL_NUMBER_AUDIT_ENABLED:
            fin_path = acc / "financial_number_accuracy_report.json"
            fin_path.write_text(json.dumps(financial_report, ensure_ascii=False, indent=2), encoding="utf-8")
            reports["financial_number_accuracy_report_path"] = str(fin_path).replace("\\", "/")
        if CORPORATE_TERM_AUDIT_ENABLED:
            _jp_log("CORPORATE_TERM_AUDIT_STARTED")
            corp_path = acc / "corporate_term_accuracy_report.json"
            corp_path.write_text(json.dumps(corporate_report, ensure_ascii=False, indent=2), encoding="utf-8")
            latest = Path("troubleshooting/latest/corporate_term_accuracy_report.json")
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest.write_text(json.dumps(corporate_report, ensure_ascii=False, indent=2), encoding="utf-8")
            reports["corporate_term_accuracy_report_path"] = str(corp_path).replace("\\", "/")
            reports["latest_corporate_term_accuracy_report_path"] = str(latest).replace("\\", "/")
            _jp_log("CORPORATE_TERM_REPORT_WRITTEN", path=str(corp_path))
            _jp_log("CORPORATE_TERM_AUDIT_COMPLETED")

    _jp_log("STABLE_GLOSSARY_CORRECTION_COMPLETED", corrections=metrics["glossary_corrections_count"])
    _jp_log("LATEST_LIVE_ALPHA_OUTPUT_GLOSSARY_LAYER_WRITTEN")
    return {"lines": corrected_lines, "metrics": metrics, "decisions": decisions, "reports": reports}
