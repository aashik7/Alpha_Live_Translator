"""Issue 12 Stage 1 trusted CER + critical-term scoring (85261).

Uses existing normalization / CER policy. Does not replace score_three_stage_accuracy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from alpha.utils.cer_backtracking import levenshtein_operation_counts
from alpha.utils.issue12_stage1_runtime import (
    MEETING_CONTEXT_GLOSSARY_REL,
    sha256_file,
)
from alpha.utils.prepared_reference_trust import load_prepared_reference_trust

NAME_CATEGORIES = {"participant_name", "company_name", "department_name", "job_title"}
NUMBER_CATEGORIES = {"number", "date"}
BUSINESS_CATEGORIES = {"financial_term", "business_term", "product_name"}


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


def _stage_block(ref_norm: str, hyp_text: str) -> dict[str, Any]:
    hyp_norm = _normalize_text(hyp_text)
    counts = levenshtein_operation_counts(ref_norm, hyp_norm)
    ref_len = max(int(counts["reference_character_count"]), 1)
    distance = int(counts["edit_distance"])
    cer_ratio = distance / ref_len
    cer_percent = cer_ratio * 100.0
    accuracy_percent = max(0.0, 100.0 - cer_percent)
    return {
        "reference_character_count": int(counts["reference_character_count"]),
        "hypothesis_character_count": int(counts["hypothesis_character_count"]),
        "substitutions": int(counts["substitutions"]),
        "deletions": int(counts["deletions"]),
        "insertions": int(counts["insertions"]),
        "total_edit_distance": distance,
        "cer": cer_ratio,
        "cer_percent": cer_percent,
        "accuracy_percent": accuracy_percent,
        "accuracy_percent_display": round(accuracy_percent, 2),
        "cer_percent_display": round(cer_percent, 2),
    }


def _load_glossary_terms(project_root: Path) -> list[dict[str, str]]:
    path = project_root / MEETING_CONTEXT_GLOSSARY_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    terms = data.get("terms") or []
    out: list[dict[str, str]] = []
    for item in terms:
        if isinstance(item, dict):
            term = str(item.get("term") or "").strip()
            cat = str(item.get("category") or "").strip()
            if term and cat:
                out.append({"term": term, "category": cat})
    return out


def _category_accuracy(hyp_norm: str, terms: list[dict[str, str]], categories: set[str]) -> dict[str, Any]:
    selected = [t for t in terms if t["category"] in categories]
    if not selected:
        return {
            "expected_count": 0,
            "found_count": 0,
            "accuracy_percent": 100.0,
            "missing_terms": [],
        }
    found = 0
    missing: list[str] = []
    for item in selected:
        term_norm = _normalize_text(item["term"])
        if term_norm and term_norm in hyp_norm:
            found += 1
        else:
            missing.append(item["term"])
    acc = (found / len(selected)) * 100.0
    return {
        "expected_count": len(selected),
        "found_count": found,
        "accuracy_percent": acc,
        "missing_terms": missing,
    }


def _top_edit_categories(
    ref_norm: str, hyp_norm: str, glossary_terms: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Heuristic high-impact categories for fail diagnostics (not used for pass)."""
    counts = levenshtein_operation_counts(ref_norm, hyp_norm)
    missing_by_cat: dict[str, int] = {}
    for item in glossary_terms:
        term_norm = _normalize_text(item["term"])
        if term_norm and term_norm not in hyp_norm:
            missing_by_cat[item["category"]] = missing_by_cat.get(item["category"], 0) + 1
    ranked = sorted(missing_by_cat.items(), key=lambda kv: (-kv[1], kv[0]))
    out = [{"category": c, "missing_term_count": n} for c, n in ranked[:5]]
    if len(out) < 5:
        out.append(
            {
                "category": "raw_substitutions",
                "missing_term_count": int(counts["substitutions"]),
            }
        )
    if len(out) < 5:
        out.append(
            {
                "category": "raw_deletions",
                "missing_term_count": int(counts["deletions"]),
            }
        )
    return out[:5]


def score_issue12_stage1(
    *,
    project_root: Path,
    run_folder: Path,
    reference_path: Path,
) -> dict[str, Any]:
    project_root = Path(project_root)
    run_folder = Path(run_folder)
    reference_path = Path(reference_path)
    if not reference_path.is_absolute():
        reference_path = project_root / reference_path

    stage_dir = run_folder / "accuracy_stage_compare"
    raw_path = stage_dir / "raw_deepgram.txt"
    stable_path = stage_dir / "stable_transcript.txt"
    if not stable_path.exists():
        stable_path = stage_dir / "stable_assembler_only.txt"
    final_path = stage_dir / "final_alpha_output.txt"

    trust = load_prepared_reference_trust(reference_path)
    trusted = bool(trust.get("trusted"))
    verdict = str(trust.get("verdict") or "invalid_for_cer")
    # Allow direct authoritative reference when prepared hash matches source
    if not trusted and reference_path.exists():
        if trust.get("reference_sha256") and trust.get("snapshot_sha256"):
            if trust["reference_sha256"] == trust["snapshot_sha256"] and verdict == "valid_for_cer":
                trusted = True

    ref_text = reference_path.read_text(encoding="utf-8")
    ref_norm = _normalize_text(ref_text)
    ref_sha = sha256_file(reference_path)

    failures: list[str] = []
    if verdict != "valid_for_cer":
        failures.append(f"reference_quality_verdict={verdict}")
    if not trusted:
        failures.append("trusted_score_false")

    for label, path in (
        ("raw", raw_path),
        ("stable", stable_path),
        ("final", final_path),
    ):
        if not path.exists() or path.stat().st_size <= 0:
            failures.append(f"missing_or_empty_{label}")

    raw = _stage_block(ref_norm, raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    stable = (
        _stage_block(ref_norm, stable_path.read_text(encoding="utf-8"))
        if stable_path.exists()
        else {}
    )
    final = (
        _stage_block(ref_norm, final_path.read_text(encoding="utf-8"))
        if final_path.exists()
        else {}
    )

    stable_acc = float(stable.get("accuracy_percent") or 0.0)
    final_acc = float(final.get("accuracy_percent") or 0.0)
    stable_to_final_loss = max(0.0, stable_acc - final_acc)

    glossary_terms = _load_glossary_terms(project_root)
    hyp_for_terms = _normalize_text(
        stable_path.read_text(encoding="utf-8") if stable_path.exists() else ""
    )
    name_s = _category_accuracy(hyp_for_terms, glossary_terms, NAME_CATEGORIES)
    number_s = _category_accuracy(hyp_for_terms, glossary_terms, NUMBER_CATEGORIES)
    business_s = _category_accuracy(hyp_for_terms, glossary_terms, BUSINESS_CATEGORIES)

    critical_terms = [
        t
        for t in glossary_terms
        if t["category"] in (NAME_CATEGORIES | NUMBER_CATEGORIES | BUSINESS_CATEGORIES)
    ]
    combined = _category_accuracy(
        hyp_for_terms,
        critical_terms,
        NAME_CATEGORIES | NUMBER_CATEGORIES | BUSINESS_CATEGORIES,
    )

    # Pass/fail without display rounding
    if stable_acc < 85.00:
        failures.append("stable_accuracy_below_85")
    if float(stable.get("cer_percent") or 100.0) > 15.00:
        failures.append("stable_cer_above_15")
    if float(combined["accuracy_percent"]) < 90.00:
        failures.append("combined_critical_term_below_90")
    if stable_to_final_loss > 0.0:
        failures.append("stable_to_final_loss_nonzero")

    target_85_passed = trusted and verdict == "valid_for_cer" and not failures

    payload: dict[str, Any] = {
        "reference_quality_verdict": verdict if trusted else verdict,
        "trusted_score": bool(trusted and verdict == "valid_for_cer"),
        "reference_path": str(reference_path),
        "reference_sha256": ref_sha,
        "raw": raw,
        "stable": stable,
        "final": final,
        "stable_to_final_loss_percent": stable_to_final_loss,
        "name_accuracy_percent": float(name_s["accuracy_percent"]),
        "number_accuracy_percent": float(number_s["accuracy_percent"]),
        "business_term_accuracy_percent": float(business_s["accuracy_percent"]),
        "combined_critical_term_accuracy_percent": float(combined["accuracy_percent"]),
        "category_details": {
            "names": name_s,
            "numbers": number_s,
            "business_terms": business_s,
            "combined": combined,
        },
        "target_85_passed": target_85_passed,
        "failures": failures,
        "gap_to_85_percent": max(0.0, 85.00 - stable_acc),
        "highest_impact_edit_categories": _top_edit_categories(
            ref_norm, hyp_for_terms, glossary_terms
        ),
    }

    stage_dir.mkdir(parents=True, exist_ok=True)
    json_path = stage_dir / "issue12_stage1_score.json"
    txt_path = stage_dir / "issue12_stage1_score.txt"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "ISSUE12_STAGE1_SCORE",
        f"trusted_score={payload['trusted_score']}",
        f"reference_quality_verdict={payload['reference_quality_verdict']}",
        f"raw_accuracy_percent={round(float(raw.get('accuracy_percent') or 0), 2)}",
        f"stable_accuracy_percent={round(stable_acc, 2)}",
        f"final_accuracy_percent={round(final_acc, 2)}",
        f"trusted_stable_cer_percent={round(float(stable.get('cer_percent') or 0), 2)}",
        f"combined_critical_term_accuracy_percent={round(float(combined['accuracy_percent']), 2)}",
        f"stable_to_final_loss_percent={round(stable_to_final_loss, 2)}",
        f"target_85_passed={target_85_passed}",
        f"failures={failures}",
        f"gap_to_85_percent={round(float(payload['gap_to_85_percent']), 2)}",
    ]
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload["score_json_path"] = str(json_path)
    payload["score_txt_path"] = str(txt_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score Issue 12 Stage 1 accuracy")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args(argv)
    result = score_issue12_stage1(
        project_root=Path(args.project_root),
        run_folder=Path(args.run_folder),
        reference_path=Path(args.reference),
    )
    print(json.dumps({"target_85_passed": result["target_85_passed"], "failures": result["failures"]}, ensure_ascii=False))
    return 0 if result["target_85_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
