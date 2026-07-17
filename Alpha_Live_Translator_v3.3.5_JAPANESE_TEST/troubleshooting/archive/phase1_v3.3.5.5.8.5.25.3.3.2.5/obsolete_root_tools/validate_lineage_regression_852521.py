"""Regression: suppression-aware lineage coverage (8.5.25.2.1)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from alpha.transcription.transcript_lineage import (
    TranscriptLineageRegistry,
    analyze_lineage_export_coverage,
    classify_source_commits,
)


def _fixture_a() -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        stable = root / "stable_commits.jsonl"
        stop_tail = root / "stop_tail_decisions.jsonl"
        rows = []
        for i in range(1, 10):
            row = {
                "stable_commit_id": f"stable-{i}",
                "stable_text": f"line {i}",
                "commit_reason": "stop_flush_incomplete_tail" if i == 9 else "normal",
                "export_eligibility": "intentionally_suppressed" if i == 9 else "export_required",
                "debug_history_only": i == 9,
                "suppression_classification": "incomplete_suppressed" if i == 9 else "",
                "suppression_reason": "no_sentence_boundary" if i == 9 else "",
            }
            rows.append(row)
        stable.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        stop_tail.write_text(
            json.dumps(
                {
                    "stable_commit_id": "stable-9",
                    "source_commit_id": "stable-9",
                    "text": "保育所の新規改正",
                    "classification": "incomplete_suppressed",
                    "suppressed_from_alpha": True,
                    "suppression_reason": "no_sentence_boundary",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        cls = classify_source_commits(stable_commits_path=stable, stop_tail_path=stop_tail)
        reg = TranscriptLineageRegistry()
        export_lines = []
        for i in range(1, 9):
            reg.create_canonical_line(f"line {i}", source_commit_ids=[f"stable-{i}"])
        cov = analyze_lineage_export_coverage(
            reg, export_lines, stable_commits_path=stable, stop_tail_path=stop_tail
        )
        return {**cls, **cov}


def _fixture_b() -> dict:
    reg = TranscriptLineageRegistry()
    reg.create_canonical_line("only one", source_commit_ids=["stable-1"])
    with tempfile.TemporaryDirectory() as td:
        stable = Path(td) / "stable.jsonl"
        stable.write_text(
            "\n".join(
                json.dumps({"stable_commit_id": f"stable-{i}", "stable_text": f"t{i}", "export_eligibility": "export_required"}, ensure_ascii=False)
                for i in range(1, 3)
            )
            + "\n",
            encoding="utf-8",
        )
        cov = analyze_lineage_export_coverage(reg, ["only one"], stable_commits_path=stable)
        return cov


def main() -> int:
    a = _fixture_a()
    b = _fixture_b()
    checks = {
        "fixture_a_observed_9": a.get("source_commit_observed_count") == 9,
        "fixture_a_suppressed_1": a.get("source_commit_intentionally_suppressed_count") == 1,
        "fixture_a_required_8": a.get("source_commit_required_count") == 8,
        "fixture_a_represented_8": a.get("source_commit_represented_required_count") == 8,
        "fixture_a_missing_0": a.get("source_commit_missing_required_count") == 0,
        "fixture_a_coverage_1": a.get("source_commit_coverage_ratio") == 1.0,
        "fixture_a_valid_loss_0": a.get("valid_segment_loss_count") == 0,
        "fixture_b_valid_loss_1": b.get("valid_segment_loss_count") == 1,
        "fixture_b_clean_false": b.get("clean_export_ready_for_scoring") is False,
        "algorithm_v2": a.get("coverage_algorithm_version") == "lineage_v2_suppression_aware",
    }
    failed = [k for k, ok in checks.items() if not ok]
    lines = [
        "VALIDATE_LINEAGE_REGRESSION_852521",
        f"Result: {'PASSED' if not failed else 'FAILED'}",
        f"fixture_a: observed={a.get('source_commit_observed_count')} required={a.get('source_commit_required_count')} coverage={a.get('source_commit_coverage_ratio')}",
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_lineage_regression_852521_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
