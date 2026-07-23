"""Multidomain gate strict / meaning-equivalent / domain-category scoring (85262)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from alpha.utils.cer_backtracking import levenshtein_operation_counts
from alpha.utils.multidomain_gate_evidence import (
    MULTIDOMAIN_VERSION,
    NORMALIZATION_RULES_VERSION,
    apply_meaning_equivalent,
    build_pre_score_evidence_gate,
    extract_numeric_entities,
    normalize_transcript_text,
    sha256_file,
    write_scoring_decision,
)
from alpha.utils.scoring_window_v265 import (
    filter_truth_entities_to_reference_window,
    load_explicit_scoring_markers,
    load_raw_events,
    resolve_scoring_window,
    slice_events_to_text,
    write_scoring_window_record,
)

# Single version source: alpha/utils/multidomain_gate_evidence.py (which mirrors alpha/constants.py)
GATE_VERSION = MULTIDOMAIN_VERSION


class ScoringNotPermittedError(RuntimeError):
    """Raised when the canonical pre-score evidence gate has not passed."""

    def __init__(self, status: str, blocked_reasons: list[str]):
        super().__init__(f"SCORING_NOT_PERMITTED:{status}:{blocked_reasons}")
        self.status = status
        self.blocked_reasons = blocked_reasons


def enforce_pre_score_gate(run_folder: Path) -> dict[str, Any]:
    """Fail closed: scoring may only run after PRE_SCORE_EVIDENCE_GATE.json passes."""
    stage = Path(run_folder) / "accuracy_stage_compare"
    gate_path = stage / "PRE_SCORE_EVIDENCE_GATE.json"
    if not gate_path.exists():
        gate = build_pre_score_evidence_gate(Path(run_folder))
    else:
        try:
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
        except Exception:
            gate = {
                "evidence_gate_passed": False,
                "scoring_permitted": False,
                "status": "EVIDENCE_INCOMPLETE",
                "blocked_reasons": ["PRE_SCORE_EVIDENCE_GATE.json:parse_error"],
            }
    if not (gate.get("evidence_gate_passed") is True and gate.get("scoring_permitted") is True):
        status = str(gate.get("status") or "EVIDENCE_INCOMPLETE")
        blocked = list(gate.get("blocked_reasons") or ["evidence_gate_not_passed"])
        write_scoring_decision(
            Path(run_folder),
            scoring_permitted=False,
            real_benchmark_completed=False,
            status=status,
            blocked_reasons=blocked,
        )
        raise ScoringNotPermittedError(status, blocked)
    return gate

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
    in_window_truth: dict[str, list[str]] | None = None,
    out_of_window_truth: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    hyp_text = stage_texts.get(primary_stage) or ""
    hyp_norm = normalize_text(hyp_text)
    extracted = extract_numeric_entities(reference_text)
    all_missed: list[dict[str, Any]] = []
    truth_for_score = dict(truth)
    if in_window_truth is not None:
        for key, terms in in_window_truth.items():
            truth_for_score[key] = list(terms)

    def _score_key(truth_key: str, cat: str) -> dict[str, Any]:
        terms = list(truth_for_score.get(truth_key) or [])
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
    business_expected = (
        it_s["expected_count"]
        + sales_s["expected_count"]
        + marketing_s["expected_count"]
        + general_s["expected_count"]
    )
    business_found = (
        it_s["found_count"]
        + sales_s["found_count"]
        + marketing_s["found_count"]
        + general_s["found_count"]
    )
    combined_business = (
        (business_found / max(business_expected, 1)) * 100.0 if business_expected else 100.0
    )

    critical_expected = (
        participant["expected_count"]
        + company["expected_count"]
        + numbers_s["expected_count"]
        + dates_s["expected_count"]
        + money_s["expected_count"]
        + business_expected
    )
    critical_found = (
        participant["found_count"]
        + company["found_count"]
        + numbers_s["found_count"]
        + dates_s["found_count"]
        + money_s["found_count"]
        + business_found
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
        "combined_business_term_accuracy_percent": float(combined_business),
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
        "out_of_window_truth": out_of_window_truth or {},
        "out_of_window_truth_excluded_from_denominator": True,
    }


def score_all(
    project_root: Path,
    run_folder: Path,
    reference_path: Path,
    truth_path: Path,
    *,
    output_stage_dir: Path | None = None,
    hypothesis_stage_dir: Path | None = None,
    skip_pre_score_gate: bool = False,
) -> dict[str, Any]:
    project_root = Path(project_root)
    run_folder = Path(run_folder)
    reference_path = Path(reference_path)
    truth_path = Path(truth_path)
    if not reference_path.is_absolute():
        reference_path = project_root / reference_path
    if not truth_path.is_absolute():
        truth_path = project_root / truth_path

    if skip_pre_score_gate:
        gate = {
            "evidence_gate_passed": True,
            "scoring_permitted": True,
            "status": "SKIPPED_FOR_OFFLINE_CANDIDATE",
        }
    else:
        gate = enforce_pre_score_gate(run_folder)

    source_stage = Path(hypothesis_stage_dir) if hypothesis_stage_dir else (run_folder / "accuracy_stage_compare")
    stage_dir = Path(output_stage_dir) if output_stage_dir else (run_folder / "accuracy_stage_compare")
    raw_path = source_stage / "raw_deepgram.txt"
    stable_path = source_stage / "stable_transcript.txt"
    final_path = source_stage / "final_alpha_output.txt"
    events_path = source_stage / "raw_deepgram_events.jsonl"
    if not events_path.exists():
        events_path = run_folder / "accuracy_stage_compare" / "raw_deepgram_events.jsonl"

    ref_text = reference_path.read_text(encoding="utf-8")
    ref_norm = normalize_text(ref_text)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))

    raw_text_full = raw_path.read_text(encoding="utf-8")
    stable_text_full = stable_path.read_text(encoding="utf-8")
    final_text_full = final_path.read_text(encoding="utf-8")
    if not raw_text_full.strip() or not stable_text_full.strip() or not final_text_full.strip():
        blocked = ["empty_hypothesis_blocked"]
        write_scoring_decision(
            run_folder,
            scoring_permitted=False,
            real_benchmark_completed=False,
            status="EVIDENCE_INCOMPLETE",
            blocked_reasons=blocked,
        )
        raise ScoringNotPermittedError("EVIDENCE_INCOMPLETE", blocked)

    events = load_raw_events(events_path)
    explicit = load_explicit_scoring_markers(source_stage) or load_explicit_scoring_markers(
        run_folder / "accuracy_stage_compare"
    )
    audio_duration = None
    summary_path = run_folder / "accuracy_stage_compare" / "audio_delivery_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for key in ("sent_duration_seconds", "queued_duration_seconds", "audio_duration_seconds"):
                if summary.get(key) is not None:
                    audio_duration = float(summary[key])
                    break
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            audio_duration = None

    window = resolve_scoring_window(
        reference_text=ref_text,
        events=events,
        explicit_markers=explicit,
        audio_duration_seconds=audio_duration,
    )
    if not window.get("window_resolved"):
        blocked = [f"scoring_window_unresolved:{window.get('status')}"]
        write_scoring_decision(
            run_folder,
            scoring_permitted=False,
            real_benchmark_completed=False,
            status="SCORING_WINDOW_UNRESOLVED",
            blocked_reasons=blocked,
        )
        raise ScoringNotPermittedError("SCORING_WINDOW_UNRESOLVED", blocked)

    raw_text = slice_events_to_text(events, window)

    def _filter_lines_by_raw_alignment(text: str) -> str:
        window_raw_norm = normalize_text(raw_text)
        kept: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            ln = normalize_text(stripped)
            if not ln:
                continue
            probe = ln[: max(8, min(24, len(ln)))]
            if probe and probe in window_raw_norm:
                kept.append(stripped)
                continue
            if ln in window_raw_norm:
                kept.append(stripped)
        if kept:
            return "\n".join(kept) + "\n"
        return text

    stable_text = _filter_lines_by_raw_alignment(stable_text_full)
    final_text = _filter_lines_by_raw_alignment(final_text_full)
    if not stable_text.strip():
        stable_text = stable_text_full
    if not final_text.strip():
        final_text = final_text_full

    truth_keys = list(TRUTH_CATEGORY_KEYS.keys())
    truth_window = filter_truth_entities_to_reference_window(
        truth=truth,
        reference_text=ref_text,
        category_keys=truth_keys,
    )

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
        in_window_truth=truth_window["in_window_truth"],
        out_of_window_truth=truth_window["out_of_window_truth"],
    )

    strict_payload = {
        "app_version": GATE_VERSION,
        "harness_version": GATE_VERSION,
        "run_id": run_folder.name,
        "pre_score_gate_status": str(gate.get("status") or ""),
        "reference_path": str(reference_path),
        "reference_sha256": sha256_file(reference_path),
        "truth_path": str(truth_path),
        "scoring_window": {
            "start_event_id": window.get("start_event_id"),
            "end_event_id": window.get("end_event_id"),
            "start_time_seconds": window.get("start_time_seconds"),
            "end_time_seconds": window.get("end_time_seconds"),
            "excluded_prefix_seconds": window.get("excluded_prefix_seconds"),
            "excluded_suffix_seconds": window.get("excluded_suffix_seconds"),
            "excluded_prefix_reason": window.get("excluded_prefix_reason"),
            "excluded_suffix_reason": window.get("excluded_suffix_reason"),
            "method": window.get("method"),
            "lowest_cer_window_search": False,
        },
        "raw": raw,
        "stable": stable,
        "final": final,
        "stable_to_final_loss_percent": stable_to_final_loss,
    }

    meaning_payload = {
        "normalization_rules_version": NORMALIZATION_RULES_VERSION,
        "normalization_pairs_applied": all_pairs,
        "benchmark_specific_meaning_pairs": False,
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
    write_scoring_window_record(stage_dir, window)
    (stage_dir / "windowed_raw_deepgram.txt").write_text(raw_text, encoding="utf-8")
    (stage_dir / "windowed_stable_transcript.txt").write_text(stable_text, encoding="utf-8")
    (stage_dir / "windowed_final_alpha_output.txt").write_text(final_text, encoding="utf-8")

    strict_path = stage_dir / "strict_score.json"
    meaning_path = stage_dir / "meaning_equivalent_score.json"
    domain_path = stage_dir / "domain_category_score.json"
    strict_path.write_text(json.dumps(strict_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meaning_path.write_text(json.dumps(meaning_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    domain_path.write_text(json.dumps(domain_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    recomputed = score_domain_categories(
        reference_text=ref_text,
        truth=truth,
        stage_texts={"raw": raw_text, "stable": stable_text, "final": final_text},
        primary_stage="stable",
        in_window_truth=truth_window["in_window_truth"],
        out_of_window_truth=truth_window["out_of_window_truth"],
    )
    category_mismatches = [
        key
        for key in recomputed
        if key.endswith("_accuracy_percent")
        and abs(float(recomputed[key]) - float(domain_payload.get(key) or 0.0)) > 0.01
    ]
    if category_mismatches:
        for stale in (strict_path, meaning_path):
            try:
                if stale.exists():
                    stale.unlink()
            except OSError:
                pass
        write_scoring_decision(
            run_folder,
            scoring_permitted=False,
            real_benchmark_completed=False,
            status="SCORING_VALUE_MISMATCH",
            blocked_reasons=[f"category_mismatch:{k}" for k in category_mismatches],
        )
        raise ScoringNotPermittedError(
            "SCORING_VALUE_MISMATCH",
            [f"category_mismatch:{k}" for k in category_mismatches],
        )

    decision_target = run_folder if output_stage_dir is None else Path(output_stage_dir).parent
    write_scoring_decision(
        decision_target,
        scoring_permitted=True,
        real_benchmark_completed=False,
        status="SCORED",
        blocked_reasons=[],
        scores={
            "raw_cer_percent": float(raw.get("cer_percent") or 0.0),
            "stable_cer_percent": float(stable.get("cer_percent") or 0.0),
            "final_cer_percent": float(final.get("cer_percent") or 0.0),
            "raw_accuracy_percent": float(raw.get("accuracy_percent") or 0.0),
            "stable_accuracy_percent": float(stable.get("accuracy_percent") or 0.0),
            "final_accuracy_percent": float(final.get("accuracy_percent") or 0.0),
            "stable_to_final_loss_percent": stable_to_final_loss,
            "combined_name_accuracy_percent": float(domain.get("combined_name_accuracy_percent") or 0.0),
            "numbers_accuracy_percent": float(domain.get("numbers_accuracy_percent") or 0.0),
            "combined_business_term_accuracy_percent": float(
                domain.get("combined_business_term_accuracy_percent") or 0.0
            ),
        },
    )

    return {
        "strict": strict_payload,
        "meaning_equivalent": meaning_payload,
        "domain_category": domain_payload,
        "domain": domain_payload,
        "strict_score_path": str(strict_path),
        "meaning_equivalent_score_path": str(meaning_path),
        "domain_category_score_path": str(domain_path),
        "stable_accuracy_percent": stable_acc,
        "stable_cer_percent": float(stable.get("cer_percent") or 100.0),
        "stable_to_final_loss_percent": stable_to_final_loss,
        "scoring_window": window,
        "truth_window": truth_window,
        "stage_dir": str(stage_dir),
    }



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score multidomain gate")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--truth-metadata", required=True)
    args = parser.parse_args(argv)
    try:
        result = score_all(
            project_root=Path(args.project_root),
            run_folder=Path(args.run_folder),
            reference_path=Path(args.reference),
            truth_path=Path(args.truth_metadata),
        )
    except ScoringNotPermittedError as exc:
        print(
            json.dumps(
                {
                    "scoring_permitted": False,
                    "status": exc.status,
                    "blocked_reasons": exc.blocked_reasons,
                },
                ensure_ascii=False,
            )
        )
        return 4
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
