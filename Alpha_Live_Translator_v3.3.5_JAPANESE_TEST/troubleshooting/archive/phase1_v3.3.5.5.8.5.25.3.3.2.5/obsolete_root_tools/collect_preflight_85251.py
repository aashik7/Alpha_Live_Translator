"""Preflight evidence collection before 8.5.25.1 changes."""

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


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())


def _ui_segment_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("ui_text"):
            count += 1
    return count


def main() -> int:
    root = Path("troubleshooting")
    preflight = root / "preflight_85251"
    preflight.mkdir(parents=True, exist_ok=True)
    print("PREFLIGHT_85251_COLLECTION_STARTED")
    warnings: list[str] = []

    files: list[tuple[Path, str]] = [
        (root / "latest" / "latest_accuracy_evidence_index.json", "latest_accuracy_evidence_index.json"),
        (root / "latest" / "latest_live_alpha_output.txt", "latest_live_alpha_output.txt"),
        (root / "latest" / "clean_active_transcript.jsonl", "clean_active_transcript.jsonl"),
        (root / "latest" / "stable_revision_history.jsonl", "stable_revision_history.jsonl"),
        (root / "latest" / "boundary_stabilizer_summary.json", "boundary_stabilizer_summary.json"),
        (root / "latest" / "boundary_stabilizer_decisions.jsonl", "boundary_stabilizer_decisions.jsonl"),
        (root / "latest" / "corporate_term_accuracy_report.json", "corporate_term_accuracy_report.json"),
        (root / "latest" / "financial_number_accuracy_report.json", "financial_number_accuracy_report.json"),
        (root / "accuracy_benchmark" / "glossaries" / "test01_corporate_ir_glossary.json", "test01_corporate_ir_glossary.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "LATEST_REPORT_SET_INDEX.json", "LATEST_REPORT_SET_INDEX.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_accuracy_score_report.json", "latest_accuracy_score_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_reference_quality_report.json", "latest_reference_quality_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_alignment_report.json", "latest_alignment_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_alignment_report.txt", "latest_alignment_report.txt"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_boundary_error_report.json", "latest_boundary_error_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_business_term_risk_report.json", "latest_business_term_risk_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_glossary_candidates.json", "latest_glossary_candidates.json"),
        (root / "accuracy_benchmark" / "reference_transcripts" / "test01.txt", "test01.txt"),
        (root / "Cursor final report.txt", "Cursor_final_report.txt"),
        (root / "validation" / "validate_accuracy_8525_output.txt", "validate_accuracy_8525_output.txt"),
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
            print(f"PREFLIGHT_85251_FILE_COLLECTED: {src}")
        else:
            print(f"PREFLIGHT_85251_FILE_MISSING: {src}")
            if dst in critical:
                warnings.append(f"critical_missing:{dst}")

    alpha_path = root / "latest" / "latest_live_alpha_output.txt"
    clean_path = root / "latest" / "clean_active_transcript.jsonl"
    ui_path_str = idx.get("ui_exported_segments_path", "")
    ui_path = Path(ui_path_str) if ui_path_str else None
    ui_count = _ui_segment_count(ui_path) if ui_path and ui_path.exists() else 0
    alpha_lines = _line_count(alpha_path)
    clean_count = _line_count(clean_path)
    glossary_count = idx.get("glossary_correction_count", 0)
    bs = root / "latest" / "boundary_stabilizer_summary.json"
    if bs.exists():
        try:
            sm = json.loads(bs.read_text(encoding="utf-8"))
            glossary_count = sm.get("glossary_corrections_count", glossary_count)
        except Exception:
            pass

    missing_warning = ""
    if ui_count > 0 and alpha_lines > 0 and ui_count > alpha_lines:
        missing_warning = f"ui_segments({ui_count}) > alpha_lines({alpha_lines})"
        warnings.append(missing_warning)

    manifest = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version_before_change": "3.3.5.5.8.5.25",
        "latest_run_id": idx.get("run_id", ""),
        "latest_alpha_path": str(alpha_path),
        "latest_alpha_size_bytes": alpha_path.stat().st_size if alpha_path.exists() else 0,
        "latest_alpha_sha256": _sha256(alpha_path),
        "ui_exported_segments_count_if_available": ui_count,
        "clean_active_transcript_count_if_available": clean_count,
        "latest_live_alpha_output_line_count": alpha_lines,
        "glossary_correction_count_if_available": glossary_count,
        "missing_valid_segment_warning_if_detected": missing_warning,
        "warnings": warnings,
    }
    (preflight / "PREFLIGHT_85251_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (preflight / "PREFLIGHT_85251_SUMMARY.txt").write_text(
        "\n".join(
            [
                "PREFLIGHT 85251 SUMMARY",
                f"collected_at={manifest['collected_at']}",
                f"app_version_before_change={manifest['app_version_before_change']}",
                f"latest_run_id={manifest['latest_run_id']}",
                f"ui_exported_segments={ui_count}",
                f"alpha_lines={alpha_lines}",
                f"clean_active_lines={clean_count}",
                f"glossary_corrections={glossary_count}",
                f"missing_valid_segment_warning={missing_warning}",
                f"warnings={warnings}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("PREFLIGHT_85251_MANIFEST_WRITTEN")
    print("PREFLIGHT_85251_COLLECTION_COMPLETED")
    return 1 if any("critical_missing" in w for w in warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
