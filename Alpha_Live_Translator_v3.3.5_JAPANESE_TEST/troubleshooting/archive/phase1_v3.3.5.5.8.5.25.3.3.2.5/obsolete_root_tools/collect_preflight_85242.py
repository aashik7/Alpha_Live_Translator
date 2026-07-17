"""Preflight evidence collection before 8.5.24.2 changes."""

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
    preflight = root / "preflight_85242"
    preflight.mkdir(parents=True, exist_ok=True)
    print("PREFLIGHT_85242_COLLECTION_STARTED")
    warnings: list[str] = []

    files: list[tuple[Path, str]] = [
        (root / "latest" / "latest_accuracy_evidence_index.json", "latest_accuracy_evidence_index.json"),
        (root / "latest" / "latest_live_alpha_output.txt", "latest_live_alpha_output.txt"),
        (root / "latest" / "boundary_stabilizer_summary.json", "boundary_stabilizer_summary.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "LATEST_REPORT_SET_INDEX.json", "LATEST_REPORT_SET_INDEX.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_accuracy_score_report.json", "latest_accuracy_score_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_reference_quality_report.json", "latest_reference_quality_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_alignment_report.json", "latest_alignment_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_alignment_report.txt", "latest_alignment_report.txt"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_boundary_error_report.json", "latest_boundary_error_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_business_term_risk_report.json", "latest_business_term_risk_report.json"),
        (root / "accuracy_benchmark" / "latest_reports" / "latest_glossary_candidates.json", "latest_glossary_candidates.json"),
        (root / "latest" / "clean_active_transcript.jsonl", "clean_active_transcript.jsonl"),
        (root / "latest" / "stable_revision_history.jsonl", "stable_revision_history.jsonl"),
        (root / "latest" / "boundary_stabilizer_decisions.jsonl", "boundary_stabilizer_decisions.jsonl"),
        (root / "Cursor final report.txt", "Cursor_final_report.txt"),
        (root / "validation" / "validate_accuracy_85241_output.txt", "validate_accuracy_85241_output.txt"),
    ]
    for smoke in sorted((root / "smoke_tests").glob("*852*")) if (root / "smoke_tests").exists() else []:
        files.append((smoke, f"smoke_{smoke.name}"))

    runs = sorted(
        [p for p in (root / "runs").iterdir() if p.is_dir() and p.name != "_pending"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if (root / "runs").exists() else []
    boundary_summary = ""
    boundary_decisions = ""
    clean_active = ""
    stable_revision = ""
    if runs:
        acc = runs[0] / "accuracy"
        tr = runs[0] / "transcripts"
        for name, holder in (
            ("boundary_stabilizer_summary.json", "summary"),
            ("boundary_stabilizer_decisions.jsonl", "decisions"),
        ):
            p = acc / name
            if p.exists():
                files.append((p, f"run_{name}"))
                if holder == "summary":
                    boundary_summary = str(p)
                else:
                    boundary_decisions = str(p)
        for name, var in (
            ("clean_active_transcript.jsonl", "clean"),
            ("stable_revision_history.jsonl", "revision"),
        ):
            p = tr / name
            if p.exists():
                files.append((p, f"run_{name}"))
                if var == "clean":
                    clean_active = str(p)
                else:
                    stable_revision = str(p)

    pending_decisions = root / "runs" / "_pending" / "accuracy" / "boundary_stabilizer_decisions.jsonl"
    if pending_decisions.exists():
        files.append((pending_decisions, "boundary_stabilizer_decisions_pending.jsonl"))
        if not boundary_decisions:
            boundary_decisions = str(pending_decisions)

    idx: dict = {}
    idx_path = root / "latest" / "latest_accuracy_evidence_index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text(encoding="utf-8"))

    critical = {"latest_accuracy_evidence_index.json", "latest_live_alpha_output.txt"}
    for src, dst in files:
        if src.exists():
            shutil.copy2(src, preflight / dst)
            print(f"PREFLIGHT_85242_FILE_COLLECTED: {src}")
        else:
            print(f"PREFLIGHT_85242_FILE_MISSING: {src}")
            if dst in critical:
                warnings.append(f"critical_missing:{dst}")
            if dst in ("clean_active_transcript.jsonl", "stable_revision_history.jsonl"):
                warnings.append(f"missing_before_85242:{dst}")

    alpha_path = root / "latest" / "latest_live_alpha_output.txt"
    score_path = root / "accuracy_benchmark" / "latest_reports" / "latest_accuracy_score_report.json"
    align_path = root / "accuracy_benchmark" / "latest_reports" / "latest_alignment_report.json"
    boundary_path = root / "accuracy_benchmark" / "latest_reports" / "latest_boundary_error_report.json"

    manifest = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version_before_change": "3.3.5.5.8.5.24.1",
        "latest_run_id": idx.get("run_id", runs[0].name if runs else ""),
        "latest_alpha_path": str(alpha_path),
        "latest_alpha_size_bytes": alpha_path.stat().st_size if alpha_path.exists() else 0,
        "latest_alpha_sha256": _sha256(alpha_path),
        "latest_boundary_summary_path": boundary_summary or str(root / "latest" / "boundary_stabilizer_summary.json"),
        "latest_boundary_decisions_path": boundary_decisions,
        "latest_clean_active_transcript_path": clean_active or str(root / "latest" / "clean_active_transcript.jsonl"),
        "latest_stable_revision_history_path": stable_revision or str(root / "latest" / "stable_revision_history.jsonl"),
        "latest_score_report_path": str(score_path) if score_path.exists() else "",
        "latest_boundary_report_path": str(boundary_path) if boundary_path.exists() else "",
        "latest_alignment_report_path": str(align_path) if align_path.exists() else "",
        "warnings": warnings,
    }
    (preflight / "PREFLIGHT_85242_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (preflight / "PREFLIGHT_85242_SUMMARY.txt").write_text(
        "\n".join(
            [
                "PREFLIGHT 85242 SUMMARY",
                f"collected_at={manifest['collected_at']}",
                f"app_version_before_change={manifest['app_version_before_change']}",
                f"latest_run_id={manifest['latest_run_id']}",
                f"latest_alpha_sha256={manifest['latest_alpha_sha256'][:16]}...",
                f"boundary_decisions_path={boundary_decisions or 'none'}",
                f"clean_active_transcript_path={clean_active or 'missing'}",
                f"stable_revision_history_path={stable_revision or 'missing'}",
                f"warnings={warnings}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("PREFLIGHT_85242_MANIFEST_WRITTEN")
    print("PREFLIGHT_85242_COLLECTION_COMPLETED")
    return 1 if any("critical_missing" in w for w in warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
