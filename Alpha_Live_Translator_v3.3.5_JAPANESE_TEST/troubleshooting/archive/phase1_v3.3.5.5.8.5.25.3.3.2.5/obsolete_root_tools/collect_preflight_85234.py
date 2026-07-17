"""One-time preflight evidence collection before 8.5.23.4 changes."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from alpha.constants import APP_VERSION


def _sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    root = Path("troubleshooting")
    preflight = root / "preflight_85234"
    preflight.mkdir(parents=True, exist_ok=True)
    print("PREFLIGHT_85234_COLLECTION_STARTED")
    warnings: list[str] = []
    files_to_collect: list[tuple[Path, str]] = [
        (root / "latest" / "latest_accuracy_evidence_index.json", "latest_accuracy_evidence_index.json"),
        (root / "latest" / "latest_live_alpha_output.txt", "latest_live_alpha_output.txt"),
        (root / "latest_alpha_output.txt", "latest_alpha_output.txt"),
        (root / "latest" / "LATEST_RUN_POINTER.json", "LATEST_RUN_POINTER.json"),
        (root / "Cursor final report.txt", "Cursor_final_report.txt"),
    ]
    idx: dict = {}
    index_path = root / "latest" / "latest_accuracy_evidence_index.json"
    if index_path.exists():
        idx = json.loads(index_path.read_text(encoding="utf-8"))
    for key, dst in (
        ("latest_alignment_report_path", "alignment_report.json"),
        ("latest_boundary_error_report_path", "boundary_error_report.json"),
        ("latest_business_term_risk_report_path", "business_term_risk_report.json"),
        ("latest_glossary_candidates_path", "glossary_candidates.json"),
        ("latest_reference_cleanup_suggestions_path", "reference_cleanup_suggestions.txt"),
        ("benchmark_score_report_path", "accuracy_score_report.json"),
        ("reference_quality_report_path", "reference_quality_report.json"),
    ):
        p = str(idx.get(key, "")).strip()
        if p:
            files_to_collect.append((Path(p), dst))
    results = root / "accuracy_benchmark" / "results"
    if results.exists():
        for pattern, dst in (
            ("*_accuracy_score_report.json", "latest_results_accuracy_score_report.json"),
            ("*_alignment_report.json", "latest_results_alignment_report.json"),
            ("*_boundary_error_report.json", "latest_results_boundary_error_report.json"),
            ("*_reference_quality_report.json", "latest_results_reference_quality_report.json"),
        ):
            cands = sorted(results.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                files_to_collect.append((cands[0], dst))
    runs_dir = root / "runs"
    if runs_dir.exists():
        runs = sorted(
            [p for p in runs_dir.iterdir() if p.is_dir() and p.name != "_pending"],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if runs:
            files_to_collect.append(
                (runs[0] / "upload_package" / "UPLOAD_PACKAGE_INDEX.txt", "UPLOAD_PACKAGE_INDEX.txt")
            )
    seen: set[str] = set()
    for src, dst in files_to_collect:
        key = f"{src}|{dst}"
        if key in seen:
            continue
        seen.add(key)
        out = preflight / dst
        if src.exists():
            shutil.copy2(src, out)
            print(f"PREFLIGHT_85234_FILE_COLLECTED: {src}")
        else:
            print(f"PREFLIGHT_85234_FILE_MISSING: {src}")
            if dst in ("latest_live_alpha_output.txt", "latest_accuracy_evidence_index.json"):
                warnings.append(f"critical_missing:{dst}")
    alpha_path = root / "latest" / "latest_live_alpha_output.txt"
    ref_path = Path("troubleshooting/accuracy_benchmark/reference_transcripts/test01.txt")
    manifest = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version_before_change": APP_VERSION,
        "latest_run_id": idx.get("run_id", ""),
        "latest_run_timestamp": idx.get("run_timestamp", ""),
        "latest_alpha_path": str(alpha_path),
        "latest_alpha_size_bytes": alpha_path.stat().st_size if alpha_path.exists() else 0,
        "latest_alpha_sha256": _sha256(alpha_path),
        "latest_reference_path_if_known": str(ref_path),
        "latest_reference_sha256_if_known": _sha256(ref_path),
        "latest_score_report_path": idx.get("benchmark_score_report_path", ""),
        "latest_alignment_report_path": idx.get("latest_alignment_report_path", ""),
        "latest_boundary_report_path": idx.get("latest_boundary_error_report_path", ""),
        "latest_business_report_path": idx.get("latest_business_term_risk_report_path", ""),
        "latest_glossary_report_path": idx.get("latest_glossary_candidates_path", ""),
        "warnings": warnings,
    }
    (preflight / "PREFLIGHT_85234_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (preflight / "PREFLIGHT_85234_SUMMARY.txt").write_text(
        "\n".join(
            [
                "PREFLIGHT 85234 SUMMARY",
                f"collected_at={manifest['collected_at']}",
                f"app_version_before_change={manifest['app_version_before_change']}",
                f"latest_run_id={manifest['latest_run_id']}",
                f"latest_alpha_sha256={manifest['latest_alpha_sha256'][:16]}...",
                f"warnings={warnings}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("PREFLIGHT_85234_MANIFEST_WRITTEN")
    print("PREFLIGHT_85234_COLLECTION_COMPLETED")
    return 1 if any("critical_missing" in w for w in warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
