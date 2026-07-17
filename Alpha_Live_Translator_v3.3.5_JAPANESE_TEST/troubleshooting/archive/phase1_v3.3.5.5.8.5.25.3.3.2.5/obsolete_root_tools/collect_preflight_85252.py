"""Preflight evidence collection before 8.5.25.2 changes."""

from __future__ import annotations

import hashlib
import json
import re
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


def _detect_pre_correction_coexistence(alpha_path: Path, glossary_path: Path) -> tuple[bool, bool]:
    pre_in_alpha = False
    corrected_and_uncorrected = False
    if not alpha_path.exists():
        return pre_in_alpha, corrected_and_uncorrected
    alpha_text = alpha_path.read_text(encoding="utf-8")
    pairs = [("既存円", "既存園"), ("工程価格", "公定価格"), ("さくら作", "さくらさく")]
    for before, after in pairs:
        if before in alpha_text and after in alpha_text:
            corrected_and_uncorrected = True
        if before in alpha_text:
            pre_in_alpha = True
    return pre_in_alpha, corrected_and_uncorrected


def main() -> int:
    root = Path("troubleshooting")
    preflight = root / "preflight_85252"
    preflight.mkdir(parents=True, exist_ok=True)
    print("PREFLIGHT_85252_COLLECTION_STARTED")
    warnings: list[str] = []

    files: list[tuple[Path, str]] = [
        (root / "latest" / "latest_accuracy_evidence_index.json", "latest_accuracy_evidence_index.json"),
        (root / "latest" / "latest_live_alpha_output.txt", "latest_live_alpha_output.txt"),
        (root / "latest" / "clean_active_transcript.jsonl", "clean_active_transcript.jsonl"),
        (root / "latest" / "stable_revision_history.jsonl", "stable_revision_history.jsonl"),
        (root / "latest" / "export_coverage_report.json", "export_coverage_report.json"),
        (root / "latest" / "export_suppression_decisions.jsonl", "export_suppression_decisions.jsonl"),
        (root / "latest" / "glossary_correction_summary.json", "glossary_correction_summary.json"),
        (root / "latest" / "corporate_term_accuracy_report.json", "corporate_term_accuracy_report.json"),
        (root / "latest" / "financial_number_accuracy_report.json", "financial_number_accuracy_report.json"),
        (root / "latest" / "boundary_stabilizer_summary.json", "boundary_stabilizer_summary.json"),
        (root / "latest" / "boundary_stabilizer_decisions.jsonl", "boundary_stabilizer_decisions.jsonl"),
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
        (root / "validation" / "validate_accuracy_85251_output.txt", "validate_accuracy_85251_output.txt"),
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
            print(f"PREFLIGHT_85252_FILE_COLLECTED: {src}")
        else:
            print(f"PREFLIGHT_85252_FILE_MISSING: {src}")
            if dst in critical:
                warnings.append(f"critical_missing:{dst}")

    alpha_path = root / "latest" / "latest_live_alpha_output.txt"
    cov_path = root / "latest" / "export_coverage_report.json"
    cov: dict = {}
    if cov_path.exists():
        try:
            cov = json.loads(cov_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    pre_in_alpha, both_versions = _detect_pre_correction_coexistence(
        alpha_path, root / "accuracy_benchmark" / "glossaries" / "test01_corporate_ir_glossary.json"
    )
    glossary_count = idx.get("glossary_correction_count", 0)
    bs = root / "latest" / "boundary_stabilizer_summary.json"
    if bs.exists():
        try:
            glossary_count = json.loads(bs.read_text(encoding="utf-8")).get(
                "glossary_corrections_count", glossary_count
            )
        except Exception:
            pass

    manifest = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version_before_change": "3.3.5.5.8.5.25.1",
        "latest_run_id": idx.get("run_id", ""),
        "latest_alpha_path": str(alpha_path),
        "latest_alpha_size_bytes": alpha_path.stat().st_size if alpha_path.exists() else 0,
        "latest_alpha_sha256": _sha256(alpha_path),
        "latest_alpha_line_count": _line_count(alpha_path),
        "pre_correction_lines_in_alpha": pre_in_alpha,
        "corrected_and_uncorrected_coexist": both_versions,
        "valid_segment_loss_count_if_available": cov.get("valid_segment_loss_count", -1),
        "glossary_correction_count_if_available": glossary_count,
        "glossary_count_synced": glossary_count == idx.get("glossary_correction_count", glossary_count),
        "warnings": warnings,
    }
    (preflight / "PREFLIGHT_85252_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (preflight / "PREFLIGHT_85252_SUMMARY.txt").write_text(
        "\n".join(
            [
                "PREFLIGHT 85252 SUMMARY",
                f"collected_at={manifest['collected_at']}",
                f"app_version_before_change={manifest['app_version_before_change']}",
                f"pre_correction_lines_in_alpha={pre_in_alpha}",
                f"corrected_and_uncorrected_coexist={both_versions}",
                f"valid_segment_loss_count={cov.get('valid_segment_loss_count', 'n/a')}",
                f"glossary_corrections={glossary_count}",
                f"glossary_count_synced={manifest['glossary_count_synced']}",
                f"warnings={warnings}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("PREFLIGHT_85252_MANIFEST_WRITTEN")
    print("PREFLIGHT_85252_COLLECTION_COMPLETED")
    return 1 if any("critical_missing" in w for w in warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
