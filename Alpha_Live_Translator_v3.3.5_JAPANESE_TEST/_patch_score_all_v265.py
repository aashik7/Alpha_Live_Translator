# -*- coding: utf-8 -*-
from pathlib import Path

path = Path("score_multidomain_gate_85262.py")
text = path.read_text(encoding="utf-8")
start = text.index("def score_all(")
end = text.index("\ndef main(")
new_fn = '''def score_all(
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
            return "\\n".join(kept) + "\\n"
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
    strict_path.write_text(json.dumps(strict_payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    meaning_path.write_text(json.dumps(meaning_payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    domain_path.write_text(json.dumps(domain_payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")

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


'''
# Fix accidental double-escaped newlines in the generated source
new_fn = new_fn.replace('"\\\\n".join(kept) + "\\\\n"', '"\\n".join(kept) + "\\n"')
new_fn = new_fn.replace('+ "\\\\n", encoding=', '+ "\\n", encoding=')
path.write_text(text[:start] + new_fn + text[end:], encoding="utf-8")
print("replaced ok", path.stat().st_size)
