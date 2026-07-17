"""Repair V24 cumulative Alpha export from stable commits (8.5.24.1 + 8.5.25.1)."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path

from alpha.constants import APP_VERSION

from alpha.transcription.final_output_cleanup import (
    count_punctuation_artifacts,
    detect_cumulative_alpha_lines_v2,
    sweep_residual_duplicates,
)
from alpha.transcription.stable_line_revision import (
    detect_cumulative_alpha_lines,
    detect_cumulative_duplicate,
    get_stable_line_revision_manager,
    reset_stable_line_revision_manager,
)


def _strip_speaker(text: str) -> str:
    return re.sub(r"^\[Speaker\s+\d+\]\s*", "", (text or "").strip())


def _load_stable_commits(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _load_alpha_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def repair_from_stable_commits(commits: list[dict]) -> tuple[list[str], dict]:
    reset_stable_line_revision_manager()
    mgr = get_stable_line_revision_manager()
    previous = ""
    for row in commits:
        text = (row.get("stable_text") or "").strip()
        if not text:
            continue
        meta = row.get("assembler_metadata") or {}
        action = meta.get("boundary_action", "")
        should_revise = bool(
            meta.get("boundary_should_revise")
            or action in ("merge_with_previous", "merge_pending_and_current", "revise_previous_line")
            or meta.get("replaces_previous_stable_line")
        )
        if detect_cumulative_duplicate(previous, text):
            should_revise = True
        stab = {
            "output_text": text,
            "output_action": "revise_previous_line" if should_revise else "append_new_line",
            "should_revise": should_revise,
            "should_append": not should_revise,
            "emit_now": True,
            "reason": meta.get("boundary_reason", "repair_replay"),
            "action": action,
        }
        mgr.apply_boundary_output(stab, previous_text=previous)
        previous = mgr.get_active_lines()[-1]["text"] if mgr.get_active_lines() else text
    lines = [ln["text"] for ln in mgr.get_active_lines()]
    return lines, mgr.get_metrics()


def repair_from_alpha_lines(lines: list[str]) -> tuple[list[str], dict]:
    reset_stable_line_revision_manager()
    mgr = get_stable_line_revision_manager()
    previous = ""
    for raw in lines:
        text = _strip_speaker(raw)
        if not text:
            continue
        should_revise = detect_cumulative_duplicate(previous, text)
        stab = {
            "output_text": text,
            "should_revise": should_revise,
            "should_append": not should_revise,
            "emit_now": True,
            "output_action": "revise_previous_line" if should_revise else "append_new_line",
            "reason": "cumulative_repair",
        }
        mgr.apply_boundary_output(stab, previous_text=previous)
        previous = mgr.get_active_lines()[-1]["text"] if mgr.get_active_lines() else text
    return [ln["text"] for ln in mgr.get_active_lines()], mgr.get_metrics()


def main() -> int:
    print("CLEAN_ALPHA_REPAIR_STARTED")
    parser = argparse.ArgumentParser(description="Repair clean Alpha export from V24 cumulative output")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--input", type=str, default="")
    parser.add_argument("--stable-commits", type=str, default="")
    parser.add_argument("--reference", type=str, default="")
    parser.add_argument("--promote", action="store_true", help="Overwrite latest_live_alpha_output (dangerous)")
    parser.add_argument("--lossless", action="store_true", help="Lossless export repair with coverage gate")
    parser.add_argument("--canonical-lineage", action="store_true", help="Canonical lineage export repair")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else Path("troubleshooting/latest/latest_live_alpha_output.txt")
    if args.latest:
        input_path = Path("troubleshooting/latest/latest_live_alpha_output.txt")

    stable_path = Path(args.stable_commits) if args.stable_commits else None
    if stable_path is None:
        pointer = Path("troubleshooting/latest/LATEST_LIVE_RUN_POINTER.json")
        if pointer.exists():
            try:
                pdata = json.loads(pointer.read_text(encoding="utf-8"))
                run_name = pdata.get("run_id") or pdata.get("run_folder", "").split("/")[-1].split("\\")[-1]
                if run_name:
                    candidate = Path("troubleshooting/runs") / run_name / "transcripts" / "stable_commits.jsonl"
                    if candidate.exists():
                        stable_path = candidate
            except Exception:
                pass
        if stable_path is None:
            runs = sorted(
                [p for p in Path("troubleshooting/runs").iterdir() if p.is_dir() and p.name != "_pending"],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ) if Path("troubleshooting/runs").exists() else []
            for run in runs:
                candidate = run / "transcripts" / "stable_commits.jsonl"
                if candidate.exists() and sum(1 for ln in candidate.read_text(encoding="utf-8").splitlines() if ln.strip()) > 5:
                    stable_path = candidate
                    break

    before_lines = _load_alpha_lines(input_path)
    source = "alpha_lines"
    if stable_path and stable_path.exists():
        clean_lines, metrics = repair_from_stable_commits(_load_stable_commits(stable_path))
        source = "stable_commits"
        if not clean_lines and before_lines:
            clean_lines, metrics = repair_from_alpha_lines(before_lines)
            source = "alpha_lines_fallback"
    else:
        clean_lines, metrics = repair_from_alpha_lines(before_lines)

    print("CLEAN_ALPHA_REPAIR_85242_STARTED")
    sweep_metrics: dict = {}
    coverage_report: dict = {}
    pre_report: dict = {}
    glossary_decisions: list = []

    run_folder = stable_path.parent.parent if stable_path else None
    if run_folder:
        gpath = run_folder / "accuracy" / "glossary_correction_decisions.jsonl"
        if gpath.exists():
            for line in gpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        glossary_decisions.append(json.loads(line))
                    except Exception:
                        pass

    clean_lines, sweep_metrics = sweep_residual_duplicates(clean_lines)
    metrics.update(sweep_metrics)

    try:
        from alpha.transcription.corporate_ir_stable_corrector import apply_corporate_ir_stable_corrections

        gloss = apply_corporate_ir_stable_corrections(clean_lines, run_folder=run_folder)
        clean_lines = gloss.get("lines", clean_lines)
        glossary_decisions = gloss.get("decisions", glossary_decisions)
        metrics.update(gloss.get("metrics", {}))
    except Exception:
        pass

    if args.canonical_lineage:
        print("CANONICAL_REPAIR_STARTED")
        from alpha.transcription.transcript_lineage import finalize_canonical_export

        canon = finalize_canonical_export(
            clean_lines,
            run_folder=run_folder,
            glossary_decisions=glossary_decisions,
        )
        clean_lines = canon.get("export_lines", clean_lines)
        coverage_report = canon.get("coverage_report", {})
        pre_report = canon.get("pre_correction_report", {})
        metrics.update(coverage_report)
        metrics.update(pre_report)
        print("CANONICAL_REPAIR_LINEAGE_COVERAGE_CALCULATED")
        if pre_report.get("pre_correction_blocked_count", 0) > 0:
            print("CANONICAL_REPAIR_PRE_CORRECTION_BLOCKED")
        if coverage_report.get("valid_segment_loss_count", 0) > 0:
            print("LOSSLESS_REPAIR_VALID_SEGMENT_LOSS_DETECTED")
        print("CANONICAL_REPAIR_COMPLETED")
    elif args.lossless:
        print("LOSSLESS_REPAIR_STARTED")
        from alpha.utils.clean_export_coverage import finalize_lossless_clean_export

        clean_lines, coverage_report, _ = finalize_lossless_clean_export(
            clean_lines,
            run_folder=run_folder,
            stable_commits_path=stable_path,
        )
        sweep_metrics = {k: v for k, v in coverage_report.items() if isinstance(v, (int, float, bool, str))}
        metrics.update(sweep_metrics)
        print("LOSSLESS_REPAIR_EXPORT_COVERAGE_CALCULATED")
        if coverage_report.get("valid_segment_loss_count", 0) > 0:
            print("LOSSLESS_REPAIR_VALID_SEGMENT_LOSS_DETECTED")
    if sweep_metrics.get("residual_duplicate_suppressed_count", 0) > 0:
        print("CLEAN_ALPHA_REPAIR_RESIDUAL_DUPLICATES_REMOVED")
    if sweep_metrics.get("punctuation_artifact_cleaned_count", 0) > 0:
        print("CLEAN_ALPHA_REPAIR_PUNCTUATION_CLEANED")

    out_dir = Path("troubleshooting/accuracy_benchmark/clean_export_repair")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    prefix = "canonical_" if args.canonical_lineage else ("lossless_" if args.lossless else "")
    out_alpha = out_dir / f"{stamp}_{prefix}clean_alpha_output.txt"
    out_json = out_dir / f"{stamp}_{prefix}lineage_repair_report.json" if args.canonical_lineage else out_dir / f"{stamp}_{prefix}repair_report.json"
    out_txt = out_dir / f"{stamp}_{prefix}repair_report.txt"
    out_cov = out_dir / f"{stamp}_{prefix}export_coverage_report.json"
    out_pre = out_dir / f"{stamp}_pre_correction_reentry_report.json"

    out_alpha.write_text("\n".join(clean_lines) + ("\n" if clean_lines else ""), encoding="utf-8")

    before_cum = detect_cumulative_alpha_lines_v2(before_lines)
    after_cum = detect_cumulative_alpha_lines_v2(clean_lines)
    report = {
        "app_version": APP_VERSION,
        "repair_mode": "canonical_lineage" if args.canonical_lineage else ("lossless" if args.lossless else "standard"),
        "input_path": str(input_path),
        "stable_commits_path": str(stable_path) if stable_path else "",
        "repair_source": source,
        "input_line_count": len(before_lines),
        "output_line_count": len(clean_lines),
        "lines_removed": len(before_lines) - len(clean_lines),
        "residual_duplicate_before_count": sweep_metrics.get("residual_duplicate_before_count", 0),
        "residual_duplicate_after_count": sweep_metrics.get("residual_duplicate_after_count", 0),
        "residual_duplicate_suppressed_count": sweep_metrics.get("residual_duplicate_suppressed_count", 0),
        "punctuation_artifact_before_count": sweep_metrics.get("punctuation_artifact_before_count", 0),
        "punctuation_artifact_after_count": sweep_metrics.get("punctuation_artifact_after_count", 0),
        "punctuation_artifact_cleaned_count": sweep_metrics.get("punctuation_artifact_cleaned_count", 0),
        "clean_export_ready_for_scoring": (
            after_cum.get("cumulative_duplicate_count", 0) == 0
            and after_cum.get("punctuation_artifact_count", 0) == 0
            and (
                (args.canonical_lineage and metrics.get("source_commit_coverage_ratio", 0) >= 0.98
                 and metrics.get("valid_segment_loss_count", 0) == 0
                 and not metrics.get("final_export_contains_pre_correction_lines", False))
                or (args.lossless and metrics.get("valid_segment_loss_count", 0) == 0)
                or (not args.lossless and not args.canonical_lineage)
            )
        ),
        "final_export_contains_pre_correction_lines": metrics.get(
            "final_export_contains_pre_correction_lines", False
        ),
        "source_commit_coverage_ratio": metrics.get("source_commit_coverage_ratio", 0),
        "promoted_to_latest": False,
        "cumulative_before": before_cum,
        "cumulative_after": after_cum,
        "metrics": metrics,
        "repaired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "promoted": False,
        "output_path": str(out_alpha),
    }

    if (args.lossless or args.canonical_lineage) and args.promote:
        gate_ok = report.get("clean_export_ready_for_scoring", False)
        if gate_ok:
            promote_path = Path("troubleshooting/latest/latest_live_alpha_output.txt")
            backup = promote_path.with_suffix(".txt.bak")
            if promote_path.exists():
                shutil.copy2(promote_path, backup)
            promote_path.write_text(out_alpha.read_text(encoding="utf-8"), encoding="utf-8")
            report["promoted"] = True
            report["promoted_to_latest"] = True
            report["backup_path"] = str(backup)
            if args.canonical_lineage:
                print("CANONICAL_REPAIR_PROMOTED_WITH_BACKUP")
            else:
                print("LOSSLESS_REPAIR_PROMOTED_WITH_BACKUP")
        else:
            if args.canonical_lineage:
                print("CANONICAL_REPAIR_NOT_PROMOTED")
            else:
                print("LOSSLESS_REPAIR_NOT_PROMOTED")
    elif args.promote:
        promote_path = Path("troubleshooting/latest/latest_live_alpha_output.txt")
        promote_path.write_text(out_alpha.read_text(encoding="utf-8"), encoding="utf-8")
        report["promoted"] = True
        report["promoted_to_latest"] = True
    else:
        print("CLEAN_ALPHA_REPAIR_NOT_PROMOTED")
        if args.canonical_lineage:
            print("CANONICAL_REPAIR_NOT_PROMOTED")
        elif args.lossless:
            print("LOSSLESS_REPAIR_NOT_PROMOTED")

    if args.canonical_lineage or args.lossless:
        cov_keys = (
            "valid_segment_loss_count", "export_coverage_ratio", "export_lossless",
            "clean_export_ready_for_scoring", "source_commit_coverage_ratio",
            "lineage_coverage_ratio", "final_export_contains_pre_correction_lines",
            "source_commit_total_count", "source_commit_represented_count",
        )
        cov_payload = {k: v for k, v in metrics.items() if k in cov_keys}
        out_cov.write_text(json.dumps(cov_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        report["export_coverage_report_path"] = str(out_cov)
        if args.canonical_lineage and pre_report:
            out_pre.write_text(json.dumps(pre_report, ensure_ascii=False, indent=2), encoding="utf-8")
            report["pre_correction_reentry_report_path"] = str(out_pre)
        if args.lossless:
            print("LOSSLESS_REPAIR_COMPLETED")

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_txt.write_text(
        "\n".join(
            [
                "CLEAN ALPHA EXPORT REPAIR REPORT",
                f"input_lines={report['input_line_count']}",
                f"output_lines={report['output_line_count']}",
                f"lines_removed={report['lines_removed']}",
                f"cumulative_before={before_cum.get('cumulative_duplicate_count', 0)}",
                f"residual_duplicate_after={report.get('residual_duplicate_after_count', 0)}",
                f"punctuation_artifact_after={report.get('punctuation_artifact_after_count', 0)}",
                f"clean_export_ready={report.get('clean_export_ready_for_scoring', False)}",
                f"output={out_alpha}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("CLEAN_ALPHA_REPAIR_85242_COMPLETED")
    print("CLEAN_ALPHA_REPAIR_REPORT_WRITTEN")
    print(f"clean_alpha={out_alpha}")
    print(f"report_json={out_json}")

    if args.reference and Path(args.reference).exists():
        print("reference_provided_run_score_latest_accuracy_separately")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
