"""Three-stage Japanese accuracy scorer (8.5.25.3 / 25.3.2.1)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION, CER_OPERATION_ACCOUNTING_STRICT, THREE_STAGE_CER_SCORING_ENABLED
from alpha.utils.cer_backtracking import stage_metrics_from_normalized
from alpha.utils.prepared_reference_trust import load_prepared_reference_trust


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


def _stage_metrics(alpha_norm: str, ref_norm: str) -> dict[str, Any]:
    if not alpha_norm:
        return {}
    metrics = stage_metrics_from_normalized(alpha_norm, ref_norm)
    if CER_OPERATION_ACCOUNTING_STRICT and metrics.get("cer") is not None:
        s = int(metrics.get("substitution_count") or 0)
        d = int(metrics.get("deletion_count") or 0)
        i = int(metrics.get("insertion_count") or 0)
        dist = int(metrics.get("edit_distance") or 0)
        if dist != s + d + i:
            raise ValueError(f"CER operation accounting failed: {dist} != {s}+{d}+{i}")
        cer = float(metrics.get("cer") or 0)
        if cer > 0 and s == 0 and d == 0 and i == 0:
            raise ValueError("nonzero CER with zero S/D/I")
    return metrics


def _infer_bottleneck(
    *,
    trusted: bool,
    raw_acc: Any,
    asm_acc: Any,
    fin_acc: Any,
    stages_present: dict[str, bool],
) -> tuple[str, str]:
    if not trusted:
        return "reference_not_trusted", "reference_not_trusted"
    if not all(stages_present.values()):
        missing = [k for k, ok in stages_present.items() if not ok]
        return "missing_stage_files", f"missing_stage_files:{','.join(missing)}"
    stable_minus_raw = (
        round(asm_acc - raw_acc, 2) if asm_acc is not None and raw_acc is not None else None
    )
    final_minus_stable = (
        round(fin_acc - asm_acc, 2) if fin_acc is not None and asm_acc is not None else None
    )
    assembler_degradation = stable_minus_raw is not None and stable_minus_raw < -3.0
    final_degradation = final_minus_stable is not None and final_minus_stable < -3.0
    if raw_acc is not None and raw_acc < 90.0 and asm_acc is not None and abs(asm_acc - raw_acc) <= 3.0:
        return "audio_or_deepgram_stage", "audio_or_deepgram_stage"
    if assembler_degradation and final_degradation:
        return "multiple_stage_loss", "multiple_stage_loss"
    if assembler_degradation:
        return "assembler_stage", "assembler_stage"
    if final_degradation:
        return "final_post_processing_stage", "final_post_processing_stage"
    if raw_acc is not None and asm_acc is not None and fin_acc is not None:
        deltas = {
            "audio_or_deepgram_stage": max(0.0, (raw_acc or 0) - (asm_acc or 0)),
            "assembler_stage": max(0.0, (raw_acc or 0) - (asm_acc or 0)),
            "final_post_processing_stage": max(0.0, (asm_acc or 0) - (fin_acc or 0)),
        }
        likely = max(deltas, key=deltas.get)
        if max(deltas.values()) <= 3.0:
            return "no_major_stage_degradation", "no_major_stage_degradation"
        return likely, likely
    return "scoring_incomplete", "scoring_incomplete"


def score_three_stages(
    *,
    run_folder: Path,
    reference_path: Path,
    trust_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stage_dir = run_folder / "accuracy_stage_compare"
    raw_path = stage_dir / "raw_deepgram.txt"
    asm_path = stage_dir / "stable_assembler_only.txt"
    final_path = stage_dir / "final_alpha_output.txt"

    trust = trust_info or load_prepared_reference_trust(reference_path)
    trusted = bool(trust.get("trusted"))
    verdict = str(trust.get("verdict", "invalid_for_cer"))

    ref_text = reference_path.read_text(encoding="utf-8")
    ref_norm = _normalize_text(ref_text)

    stages_present = {
        "raw_deepgram": raw_path.exists() and raw_path.stat().st_size > 0,
        "stable_assembler": asm_path.exists() and asm_path.stat().st_size > 0,
        "final_alpha": final_path.exists() and final_path.stat().st_size > 0,
    }

    stages: dict[str, dict[str, Any]] = {}
    scoring_attempted = trusted and any(stages_present.values())
    scoring_failure_reason = ""

    if trusted:
        try:
            if stages_present["raw_deepgram"]:
                stages["raw_deepgram"] = _stage_metrics(
                    _normalize_text(raw_path.read_text(encoding="utf-8")), ref_norm
                )
            if stages_present["stable_assembler"]:
                stages["stable_assembler"] = _stage_metrics(
                    _normalize_text(asm_path.read_text(encoding="utf-8")), ref_norm
                )
            if stages_present["final_alpha"]:
                stages["final_alpha"] = _stage_metrics(
                    _normalize_text(final_path.read_text(encoding="utf-8")), ref_norm
                )
        except Exception as exc:
            scoring_failure_reason = f"{type(exc).__name__}: {exc}"
    else:
        scoring_failure_reason = str(trust.get("trust_reason", "reference_not_trusted"))

    raw_stage = stages.get("raw_deepgram", {})
    asm_stage = stages.get("stable_assembler", {})
    fin_stage = stages.get("final_alpha", {})

    raw_acc = raw_stage.get("accuracy_percent")
    asm_acc = asm_stage.get("accuracy_percent")
    fin_acc = fin_stage.get("accuracy_percent")

    likely_bottleneck, largest_loss_stage = _infer_bottleneck(
        trusted=trusted,
        raw_acc=raw_acc,
        asm_acc=asm_acc,
        fin_acc=fin_acc,
        stages_present=stages_present,
    )

    if trusted and likely_bottleneck == "reference_not_trusted":
        likely_bottleneck = "scoring_incomplete"
        largest_loss_stage = "scoring_incomplete"

    scoring_completed = (
        trusted
        and not scoring_failure_reason
        and all(stages_present.values())
        and all(stages[k].get("accuracy_percent") is not None for k in stages)
    )

    if trusted and not scoring_completed and not scoring_failure_reason:
        scoring_failure_reason = "scores_none_while_reference_trusted"

    manifest_path = run_folder / "accuracy_stage_compare" / "stage_manifest.json"
    destructive_revision_count = 0
    revision_rejected_to_append_count = 0
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            destructive_revision_count = int(manifest.get("destructive_revision_count") or 0)
            revision_rejected_to_append_count = int(manifest.get("revision_rejected_to_append_count") or 0)
        except Exception:
            pass

    stable_minus_raw = (
        round(asm_acc - raw_acc, 2) if asm_acc is not None and raw_acc is not None else None
    )
    final_minus_stable = (
        round(fin_acc - asm_acc, 2) if fin_acc is not None and asm_acc is not None else None
    )
    final_minus_raw = (
        round(fin_acc - raw_acc, 2) if fin_acc is not None and raw_acc is not None else None
    )

    final_not_worse_than_raw = (
        fin_acc is not None and raw_acc is not None and fin_acc >= raw_acc - 1.0
    )
    assembler_accuracy_gate_passed = bool(
        final_not_worse_than_raw and destructive_revision_count == 0 and scoring_completed
    )

    warning = ""
    if not trusted:
        warning = f"Reference not trusted: {trust.get('trust_reason', '')}"
    elif not scoring_completed:
        warning = f"Scoring incomplete: {scoring_failure_reason}"

    report = {
        "app_version": APP_VERSION,
        "run_folder": str(run_folder),
        "reference_path": str(reference_path),
        "reference_snapshot_sha256": trust.get("snapshot_sha256"),
        "reference_quality_verdict": verdict,
        "reference_trusted": trusted,
        "reference_trust_reason": trust.get("trust_reason", ""),
        "reference_sha256": trust.get("reference_sha256"),
        "reference_hash_match": trust.get("hash_match"),
        "trusted_score": trusted,
        "score_should_be_used_for_decision": scoring_completed,
        "scoring_attempted": scoring_attempted,
        "scoring_completed": scoring_completed,
        "scoring_failure_reason": scoring_failure_reason,
        "diagnostic_score_available": scoring_completed,
        "warning": warning,
        "raw_deepgram_cer": raw_stage.get("cer"),
        "raw_deepgram_accuracy_percent": raw_acc,
        "raw_substitutions": raw_stage.get("substitution_count"),
        "raw_deletions": raw_stage.get("deletion_count"),
        "raw_insertions": raw_stage.get("insertion_count"),
        "stable_assembler_cer": asm_stage.get("cer"),
        "stable_assembler_accuracy_percent": asm_acc,
        "stable_substitutions": asm_stage.get("substitution_count"),
        "stable_deletions": asm_stage.get("deletion_count"),
        "stable_insertions": asm_stage.get("insertion_count"),
        "final_alpha_cer": fin_stage.get("cer"),
        "final_alpha_accuracy_percent": fin_acc,
        "final_substitutions": fin_stage.get("substitution_count"),
        "final_deletions": fin_stage.get("deletion_count"),
        "final_insertions": fin_stage.get("insertion_count"),
        "stable_minus_raw_accuracy_points": stable_minus_raw,
        "final_minus_stable_accuracy_points": final_minus_stable,
        "final_minus_raw_accuracy_points": final_minus_raw,
        "destructive_revision_detected": destructive_revision_count > 0,
        "destructive_revision_count": destructive_revision_count,
        "revision_rejected_to_append_count": revision_rejected_to_append_count,
        "final_not_worse_than_raw": final_not_worse_than_raw,
        "assembler_accuracy_gate_passed": assembler_accuracy_gate_passed,
        "largest_accuracy_loss_stage": largest_loss_stage,
        "likely_bottleneck": likely_bottleneck,
        "stages": stages,
        "stages_present": stages_present,
    }
    return report


def _write_text_report(report: dict[str, Any], path: Path) -> None:
    lines = [
        "THREE-STAGE JAPANESE ACCURACY VERDICT",
        "",
        f"Reference trusted: {report.get('reference_trusted')}",
        f"Reference trust reason: {report.get('reference_trust_reason')}",
        f"Scoring completed: {report.get('scoring_completed')}",
        f"Raw Deepgram accuracy: {report.get('raw_deepgram_accuracy_percent')}%",
        f"Assembler-only accuracy: {report.get('stable_assembler_accuracy_percent')}%",
        f"Final Alpha accuracy: {report.get('final_alpha_accuracy_percent')}%",
        f"Final minus raw: {report.get('final_minus_raw_accuracy_points')} pts",
        f"Likely bottleneck: {report.get('likely_bottleneck')}",
        f"Reference trust status: {report.get('reference_quality_verdict')}",
        "",
        f"Warning: {report.get('warning', '')}",
    ]
    if report.get("scoring_failure_reason"):
        lines.append(f"Scoring failure: {report.get('scoring_failure_reason')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not THREE_STAGE_CER_SCORING_ENABLED:
        print("THREE_STAGE_CER_SCORING_ENABLED is false")
        return 1
    parser = argparse.ArgumentParser(description="Score three-stage Japanese accuracy")
    parser.add_argument("--latest-live-run", action="store_true", help="DEPRECATED: silent latest fallback removed")
    parser.add_argument("--run-folder", type=str, default="")
    parser.add_argument("--raw", type=str, default="")
    parser.add_argument("--stable", type=str, default="")
    parser.add_argument("--final", type=str, default="")
    parser.add_argument("--reference", type=str, default="")
    parser.add_argument("--project-state", type=str, default="")
    args = parser.parse_args()

    if not args.reference:
        print("FAILED: --reference is required (silent latest_* fallback removed)")
        return 2
    if not args.run_folder and not (args.raw and args.stable and args.final):
        print(
            "FAILED: require --run-folder+--reference or --raw/--stable/--final/--reference; "
            "silent latest_* fallback removed"
        )
        return 2

    project = Path(__file__).resolve().parent
    if args.run_folder:
        run_folder = Path(args.run_folder)
        if not run_folder.is_absolute():
            run_folder = project / run_folder
    else:
        # Explicit stage paths mode: synthesize a ephemeral run context under cwd is not allowed;
        # stages must already live under a run folder — require --run-folder for three-stage.
        print("FAILED: --run-folder is required for three-stage scoring")
        return 2

    if not run_folder.exists():
        print("No run folder found.")
        return 1

    reference = Path(args.reference)
    if not reference.is_absolute():
        reference = project / reference
    if not reference.exists():
        print(f"Reference not found: {reference}")
        return 1

    trust = load_prepared_reference_trust(reference)
    report = score_three_stages(run_folder=run_folder, reference_path=reference, trust_info=trust)

    stage_dir = run_folder / "accuracy_stage_compare"
    stage_dir.mkdir(parents=True, exist_ok=True)
    json_path = stage_dir / "three_stage_accuracy_report.json"
    txt_path = stage_dir / "three_stage_accuracy_report.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_text_report(report, txt_path)

    manifest_path = stage_dir / "stage_manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest.update(
        {
            "reference_not_yet_scored": not report.get("scoring_completed"),
            "reference_sha256": trust.get("snapshot_sha256") or trust.get("reference_sha256"),
            "score_generated": bool(report.get("scoring_completed")),
            "score_report_path": str(json_path),
            "reference_quality_verdict": report.get("reference_quality_verdict"),
            "reference_trusted": report.get("reference_trusted"),
        }
    )
    manifest["score_generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {txt_path}")
    print(f"Reference trusted: {report.get('reference_trusted')}")
    print(f"Scoring completed: {report.get('scoring_completed')}")
    print(f"Likely bottleneck: {report.get('likely_bottleneck')}")

    if report.get("reference_trusted") and not report.get("scoring_completed"):
        print(f"FAILED: {report.get('scoring_failure_reason')}")
        return 1
    if (
        report.get("reference_quality_verdict") == "valid_for_cer"
        and report.get("reference_trusted")
        and report.get("likely_bottleneck") == "reference_not_trusted"
    ):
        print("FAILED: contradictory trust report")
        return 1
    return 0 if report.get("scoring_completed") or not report.get("reference_trusted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
