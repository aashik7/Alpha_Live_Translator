"""Regression: atomic evidence index refresh (8.5.25.2.1)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        trouble = root / "troubleshooting"
        latest = trouble / "latest"
        latest.mkdir(parents=True)
        reports = trouble / "accuracy_benchmark" / "latest_reports"
        reports.mkdir(parents=True)
        alpha = latest / "latest_live_alpha_output.txt"
        alpha.write_text("test alpha\n", encoding="utf-8")
        cov = latest / "export_coverage_report.json"
        cov.write_text(
            json.dumps(
                {
                    "coverage_algorithm_version": "lineage_v2_suppression_aware",
                    "source_commit_coverage_ratio": 1.0,
                    "valid_segment_loss_count": 0,
                    "clean_export_ready_for_scoring": True,
                    "dangerous_correction_count": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (reports / "latest_reference_quality_report.json").write_text(
            json.dumps({"reference_quality_verdict": "invalid_for_cer"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (reports / "LATEST_REPORT_SET_INDEX.json").write_text(
            json.dumps({"report_set_consistent": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        (reports / "latest_accuracy_score_report.json").write_text(
            json.dumps({"final_trusted_score": False, "trusted_score": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        (latest / "latest_accuracy_evidence_index.json").write_text(
            json.dumps({"reference_quality_verdict": "valid_for_cer", "latest_report_set_consistent": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        (trouble / "latest_accuracy_evidence_index.json").write_text(
            json.dumps({"reference_quality_verdict": "valid_for_cer"}, ensure_ascii=False),
            encoding="utf-8",
        )

        import os

        cwd = os.getcwd()
        os.chdir(root)
        try:
            from alpha.utils.atomic_evidence_finalize import finalize_atomic_evidence

            result = finalize_atomic_evidence(export_result={"final_output_hash_consistent": True})
            idx = json.loads((latest / "latest_accuracy_evidence_index.json").read_text(encoding="utf-8"))
        finally:
            os.chdir(cwd)

    checks = {
        "finalize_ok": result.get("ok") is True,
        "verdict_refreshed": idx.get("reference_quality_verdict") == "invalid_for_cer",
        "report_set_consistent": idx.get("latest_report_set_consistent") is True,
        "trusted_false": idx.get("trusted_score") is False,
        "coverage_ratio": idx.get("source_commit_coverage_ratio") == 1.0,
    }
    failed = [k for k, ok in checks.items() if not ok]
    lines = [
        "VALIDATE_ATOMIC_EVIDENCE_SYNC_852521",
        f"Result: {'PASSED' if not failed else 'FAILED'}",
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_atomic_evidence_sync_852521_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
