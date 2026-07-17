"""Multidomain gate strict / meaning-equivalent / domain-category scoring (85262)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from alpha.utils.cer_backtracking import levenshtein_operation_counts
from alpha.utils.multidomain_gate_evidence import (
    NORMALIZATION_RULES_VERSION,
    apply_meaning_equivalent,
    extract_numeric_entities,
    normalize_transcript_text,
    sha256_file,
)

GATE_VERSION = "3.3.5.5.8.5.26.2"

TRUTH_CATEGORY_KEYS = {
    "participant_and_person_names": "participant_name",
    "company_names": "company_name",
    "it_terms": "it_term",
    "sales_terms": "sales_term",
    "marketing_terms": "marketing_term",
    "general_business_terms": "general_business_term",
}

NUMERIC_CATEGORY_KEYS = {
    "numeric_entities": "number",
    "dates_times": "date_time",
    "money_percentages": "money_percentage",
}


def normalize_text(text: str) -> str:
    return normalize_transcript_text(text)


def _stage_block(ref_norm: str, hyp_text: str) -> dict[str, Any]:
    hyp_norm = normalize_text(hyp_text)
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


def _meaning_stage(ref_norm: str, hyp_text: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    hyp_norm = normalize_text(hyp_text)
    ref_canon, ref_pairs = apply_meaning_equivalent(ref_norm)
    hyp_canon, hyp_pairs = apply_meaning_equivalent(hyp_norm)
    pairs = ref_pairs + [p for p in hyp_pairs if p not in ref_pairs]
    block = _stage_block(ref_canon, hyp_canon)
    block["normalization_pairs_applied"] = pairs
    return block, pairs


def _find_position(text: str, needle: str) -> int:
    if not needle:
        return -1
    idx = text.find(needle)
    return idx if idx >= 0 else -1


def _term_in_hyp(term: str, hyp_norm: str) -> bool:
    term_norm = normalize_text(term)
    if not term_norm:
        return False
    if term_norm in hyp_norm:
        return True
    canon, _ = apply_meaning_equivalent(term_norm)
    hyp_canon, _ = apply_meaning_equivalent(hyp_norm)
    return canon in hyp_canon


def _category_accuracy(
    *,
    terms: list[str],
    category: str,
    stage: str,
    ref_text: str,
    hyp_norm: str,
    hyp_text: str,
) -> dict[str, Any]:
    if not terms:
        return {
            "expected_count": 0,
            "found_count": 0,
            "accuracy_percent": 100.0,
            "missed_entities": [],
        }
    found = 0
    missed: list[dict[str, Any]] = []
    ref_norm = normalize_text(ref_text)
    for term in terms:
        if _term_in_hyp(term, hyp_norm):
            found += 1
            continue
        ref_pos = _find_position(ref_norm, normalize_text(term))
        hyp_pos = _find_position(normalize_text(hyp_text), normalize_text(term))
        missed.append(
            {
                "expected": term,
                "observed_candidate": "",
                "stage": stage,
                "category": category,
                "reference_position": ref_pos,
                "hypothesis_position": hyp_pos,
                "match_type": "exact",
                "error_type": "missing",
            }
        )
    acc = (found / len(terms)) * 100.0
    return {
        "expected_count": len(terms),
        "found_count": found,
        "accuracy_percent": acc,
        "missed_entities": missed,
    }


def score_domain_categories(
    *,
    reference_text: str,
    truth: dict[str, Any],
    stage_texts: dict[str, str],
    primary_stage: str = "stable",
) -> dict[str, Any]:
    hyp_text = stage_texts.get(primary_stage) or ""
    hyp_norm = normalize_text(hyp_text)
    extracted = extract_numeric_entities(reference_text)
    all_missed: list[dict[str, Any]] = []

    def _score_key(truth_key: str, cat: str) -> dict[str, Any]:
        terms = list(truth.get(truth_key) or [])
        result = _category_accuracy(
            terms=terms,
            category=cat,
            stage=primary_stage,
            ref_text=reference_text,
            hyp_norm=hyp_norm,
            hyp_text=hyp_text,
        )
        all_missed.extend(result["missed_entities"])
        return result

    participant = _score_key("participant_and_person_names", "participant_name")
    company = _score_key("company_names", "company_name")
    it_s = _score_key("it_terms", "it_term")
    sales_s = _score_key("sales_terms", "sales_term")
    marketing_s = _score_key("marketing_terms", "marketing_term")
    general_s = _score_key("general_business_terms", "general_business_term")

    numbers_s = _category_accuracy(
        terms=list(extracted.get("numeric_entities") or []),
        category="number",
        stage=primary_stage,
        ref_text=reference_text,
        hyp_norm=hyp_norm,
        hyp_text=hyp_text,
    )
    dates_s = _category_accuracy(
        terms=list(extracted.get("dates_times") or []),
        category="date_time",
        stage=primary_stage,
        ref_text=reference_text,
        hyp_norm=hyp_norm,
        hyp_text=hyp_text,
    )
    money_s = _category_accuracy(
        terms=list(extracted.get("money_percentages") or []),
        category="money_percentage",
        stage=primary_stage,
        ref_text=reference_text,
        hyp_norm=hyp_norm,
        hyp_text=hyp_text,
    )
    all_missed.extend(numbers_s["missed_entities"])
    all_missed.extend(dates_s["missed_entities"])
    all_missed.extend(money_s["missed_entities"])

    combined_name = (
        (participant["found_count"] + company["found_count"])
        / max(participant["expected_count"] + company["expected_count"], 1)
        * 100.0
    )

    critical_expected = (
        participant["expected_count"]
        + company["expected_count"]
        + numbers_s["expected_count"]
        + dates_s["expected_count"]
        + money_s["expected_count"]
        + it_s["expected_count"]
        + sales_s["expected_count"]
        + marketing_s["expected_count"]
        + general_s["expected_count"]
    )
    critical_found = (
        participant["found_count"]
        + company["found_count"]
        + numbers_s["found_count"]
        + dates_s["found_count"]
        + money_s["found_count"]
        + it_s["found_count"]
        + sales_s["found_count"]
        + marketing_s["found_count"]
        + general_s["found_count"]
    )
    combined_critical = (
        (critical_found / max(critical_expected, 1)) * 100.0 if critical_expected else 100.0
    )

    return {
        "primary_stage": primary_stage,
        "participant_name_accuracy_percent": float(participant["accuracy_percent"]),
        "company_name_accuracy_percent": float(company["accuracy_percent"]),
        "combined_name_accuracy_percent": float(combined_name),
        "dates_times_accuracy_percent": float(dates_s["accuracy_percent"]),
        "numbers_accuracy_percent": float(numbers_s["accuracy_percent"]),
        "money_percentage_accuracy_percent": float(money_s["accuracy_percent"]),
        "it_term_accuracy_percent": float(it_s["accuracy_percent"]),
        "sales_term_accuracy_percent": float(sales_s["accuracy_percent"]),
        "marketing_term_accuracy_percent": float(marketing_s["accuracy_percent"]),
        "general_business_term_accuracy_percent": float(general_s["accuracy_percent"]),
        "combined_critical_entity_accuracy_percent": float(combined_critical),
        "category_details": {
            "participant_names": participant,
            "company_names": company,
            "it_terms": it_s,
            "sales_terms": sales_s,
            "marketing_terms": marketing_s,
            "general_business_terms": general_s,
            "numbers": numbers_s,
            "dates_times": dates_s,
            "money_percentages": money_s,
        },
        "extracted_numeric_entities": extracted,
        "missed_entities": all_missed,
    }


def score_all(
    project_root: Path,
    run_folder: Path,
    reference_path: Path,
    truth_path: Path,
) -> dict[str, Any]:
    project_root = Path(project_root)
    run_folder = Path(run_folder)
    reference_path = Path(reference_path)
    truth_path = Path(truth_path)
    if not reference_path.is_absolute():
        reference_path = project_root / reference_path
    if not truth_path.is_absolute():
        truth_path = project_root / truth_path

    stage_dir = run_folder / "accuracy_stage_compare"
    raw_path = stage_dir / "raw_deepgram.txt"
    stable_path = stage_dir / "stable_transcript.txt"
    if not stable_path.exists():
        stable_path = stage_dir / "stable_assembler_only.txt"
    final_path = stage_dir / "final_alpha_output.txt"

    ref_text = reference_path.read_text(encoding="utf-8")
    ref_norm = normalize_text(ref_text)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))

    raw_text = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
    stable_text = stable_path.read_text(encoding="utf-8") if stable_path.exists() else ""
    final_text = final_path.read_text(encoding="utf-8") if final_path.exists() else ""

    raw = _stage_block(ref_norm, raw_text)
    stable = _stage_block(ref_norm, stable_text)
    final = _stage_block(ref_norm, final_text)

    stable_acc = float(stable.get("accuracy_percent") or 0.0)
    final_acc = float(final.get("accuracy_percent") or 0.0)
    stable_to_final_loss = max(0.0, stable_acc - final_acc)

    raw_me, raw_pairs = _meaning_stage(ref_norm, raw_text)
    stable_me, stable_pairs = _meaning_stage(ref_norm, stable_text)
    final_me, final_pairs = _meaning_stage(ref_norm, final_text)
    all_pairs: list[dict[str, str]] = []
    for p in raw_pairs + stable_pairs + final_pairs:
        if p not in all_pairs:
            all_pairs.append(p)

    domain = score_domain_categories(
        reference_text=ref_text,
        truth=truth,
        stage_texts={"raw": raw_text, "stable": stable_text, "final": final_text},
        primary_stage="stable",
    )

    strict_payload = {
        "app_version": GATE_VERSION,
        "reference_path": str(reference_path),
        "reference_sha256": sha256_file(reference_path),
        "truth_path": str(truth_path),
        "raw": raw,
        "stable": stable,
        "final": final,
        "stable_to_final_loss_percent": stable_to_final_loss,
    }

    meaning_payload = {
        "normalization_rules_version": NORMALIZATION_RULES_VERSION,
        "normalization_pairs_applied": all_pairs,
        "raw": raw_me,
        "stable": stable_me,
        "final": final_me,
        "meaning_equivalent_score_trusted": True,
        "warnings": [],
    }

    domain_payload = {
        "benchmark_id": truth.get("benchmark_id") or "multidomain_meeting_v1",
        **domain,
    }

    stage_dir.mkdir(parents=True, exist_ok=True)
    strict_path = stage_dir / "strict_score.json"
    meaning_path = stage_dir / "meaning_equivalent_score.json"
    domain_path = stage_dir / "domain_category_score.json"
    strict_path.write_text(json.dumps(strict_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meaning_path.write_text(json.dumps(meaning_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    domain_path.write_text(json.dumps(domain_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "strict": strict_payload,
        "meaning_equivalent": meaning_payload,
        "domain_category": domain_payload,
        "strict_score_path": str(strict_path),
        "meaning_equivalent_score_path": str(meaning_path),
        "domain_category_score_path": str(domain_path),
        "stable_accuracy_percent": stable_acc,
        "stable_cer_percent": float(stable.get("cer_percent") or 100.0),
        "stable_to_final_loss_percent": stable_to_final_loss,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score multidomain gate")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--truth-metadata", required=True)
    args = parser.parse_args(argv)
    result = score_all(
        project_root=Path(args.project_root),
        run_folder=Path(args.run_folder),
        reference_path=Path(args.reference),
        truth_path=Path(args.truth_metadata),
    )
    print(
        json.dumps(
            {
                "stable_accuracy_percent": result["stable_accuracy_percent"],
                "stable_cer_percent": result["stable_cer_percent"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
