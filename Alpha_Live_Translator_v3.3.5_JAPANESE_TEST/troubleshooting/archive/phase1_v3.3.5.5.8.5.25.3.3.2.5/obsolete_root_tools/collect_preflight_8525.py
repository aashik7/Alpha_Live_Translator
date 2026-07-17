"""Preflight evidence collection before 8.5.25 changes."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path


def _sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    root = Path("troubleshooting")
    preflight = root / "preflight_8525"
    preflight.mkdir(parents=True, exist_ok=True)
    print("PREFLIGHT_8525_COLLECTION_STARTED")
    warnings: list[str] = []

    files: list[tuple[Path, str]] = [
        (root / "latest" / "latest_accuracy_evidence_index.json", "latest_accuracy_evidence_index.json"),
        (root / "latest" / "latest_live_alpha_output.txt", "latest_live_alpha_output.txt"),
        (root / "latest" / "boundary_stabilizer_summary.json", "boundary_stabilizer_summary.json"),
        (root / "latest" / "boundary_stabilizer_decisions.jsonl", "boundary_stabilizer_decisions.jsonl"),
        (root / "latest" / "clean_active_transcript.jsonl", "clean_active_transcript.jsonl"),
        (root / "latest" / "stable_revision_history.jsonl", "stable_revision_history.jsonl"),
        (root / "accuracy_benchmark" / "latest_reports" / "LATEST_REPORT_SET_INDEX.json", "LATEST_REPORT_SET_INDEX.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_accuracy_score_report.json", "latest_accuracy_score_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_reference_quality_report.json", "latest_reference_quality_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_alignment_report.json", "latest_alignment_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_boundary_error_report.json", "latest_boundary_error_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_business_term_risk_report.json", "latest_business_term_risk_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_glossary_candidates.json", "latest_glossary_candidates.json"),
        (root / "accuracy_benchmark" / "reference_transcripts" / "test01.txt", "test01.txt"),
        (root / "Cursor final report.txt", "Cursor_final_report.txt"),
        (root / "validation" / "validate_accuracy_85242_output.txt", "validate_accuracy_85242_output.txt"),
    ]
    for smoke in sorted((root / "smoke_tests").glob("*852*")) if (root / "smoke_tests").exists() else []:
        files.append((smoke, f"smoke_{smoke.name}"))

    idx: dict = {}
    idx_path = root / "latest" / "latest_accuracy_evidence_index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text(encoding="utf-8"))

    critical = {"latest_accuracy_evidence_index.json", "latest_live_alpha_output.txt"}
    for src, dst in files:
        if src.exists():
            shutil.copy2(src, preflight / dst)
            print(f"PREFLIGHT_8525_FILE_COLLECTED: {src}")
        else:
            print(f"PREFLIGHT_8525_FILE_MISSING: {src}")
            if dst in critical:
                warnings.append(f"critical_missing:{dst}")

    alpha_path = root / "latest" / "latest_live_alpha_output.txt"
    manifest = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version_before_change": "3.3.5.5.8.5.24.2",
        "latest_run_id": idx.get("run_id", ""),
        "latest_alpha_path": str(alpha_path),
        "latest_alpha_size_bytes": alpha_path.stat().st_size if alpha_path.exists() else 0,
        "latest_alpha_sha256": _sha256(alpha_path),
        "latest_alpha_line_count": idx.get("alpha_output_line_count", 0),
        "residual_duplicate_after_count": 0,
        "punctuation_artifact_after_count": idx.get("punctuation_artifact_count", 0),
        "warnings": warnings,
    }
    bs = root / "latest" / "boundary_stabilizer_summary.json"
    if bs.exists():
        try:
            sm = json.loads(bs.read_text(encoding="utf-8"))
            manifest["residual_duplicate_after_count"] = sm.get("residual_duplicate_after_count", 0)
            manifest["punctuation_artifact_after_count"] = sm.get("punctuation_artifact_after_count", 0)
        except Exception:
            pass

    (preflight / "PREFLIGHT_8525_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (preflight / "PREFLIGHT_8525_SUMMARY.txt").write_text(
        "\n".join(
            [
                "PREFLIGHT 8525 SUMMARY",
                f"collected_at={manifest['collected_at']}",
                f"app_version_before_change={manifest['app_version_before_change']}",
                f"latest_run_id={manifest['latest_run_id']}",
                f"latest_alpha_lines={manifest['latest_alpha_line_count']}",
                f"residual_duplicate_after={manifest['residual_duplicate_after_count']}",
                f"punctuation_artifact_after={manifest['punctuation_artifact_after_count']}",
                f"warnings={warnings}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("PREFLIGHT_8525_MANIFEST_WRITTEN")
    print("PREFLIGHT_8525_COLLECTION_COMPLETED")
    return 1 if any("critical_missing" in w for w in warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
