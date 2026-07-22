"""Normalize nested stable-commit lineage into derived top-level fields."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha.utils.canonical_content_hash import atomic_write_json, atomic_write_jsonl
from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import _extract_lineage, _read_jsonl


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_sha256(row: dict[str, Any]) -> str:
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_stable_commits(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    src = folder / "transcripts" / "stable_commits.jsonl"
    rows = _read_jsonl(src)
    # Only actual commit rows (stable_text present) are lineage subjects
    commit_rows = [r for r in rows if str(r.get("stable_text") or "").strip()]
    normalized: list[dict[str, Any]] = []
    top_level = 0
    nested_recovered = 0
    without: list[str] = []

    for row in commit_rows:
        top = row.get("source_raw_event_ids")
        lineage_source = "none"
        lineage: list[str] = []
        if isinstance(top, list) and any(str(x).strip() for x in top):
            lineage = [str(x) for x in top if str(x).strip()]
            lineage_source = "top_level"
            top_level += 1
        else:
            recovered = _extract_lineage(row)
            if recovered:
                lineage = recovered
                lineage_source = "nested_assembler_metadata"
                nested_recovered += 1
            else:
                cid = str(row.get("stable_commit_id") or row.get("commit_id") or "")
                without.append(cid or f"row_sha_{_row_sha256(row)[:12]}")

        out = dict(row)
        out["source_raw_event_ids"] = lineage
        out["lineage_source"] = lineage_source
        out["lineage_normalized"] = True
        out["source_row_sha256"] = _row_sha256(row)
        normalized.append(out)

    total = len(commit_rows)
    coverage = (float(total - len(without)) / float(total)) if total else 0.0
    report = {
        "generated_utc": _utc_now(),
        "run_folder": str(folder),
        "source_path": str(src),
        "source_commit_count": total,
        "normalized_commit_count": len(normalized),
        "top_level_lineage_present_count": top_level,
        "nested_lineage_recovered_count": nested_recovered,
        "commits_without_lineage": without,
        "lineage_coverage_ratio": coverage,
        "original_stable_commits_mutated": False,
        "lineage_reconciliation_passed": len(without) == 0 and coverage == 1.0 and total > 0,
    }
    return {"rows": normalized, "report": report}


def write_stable_commit_lineage_reconciliation(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    result = normalize_stable_commits(folder)
    out_jsonl = folder / "transcripts" / "stable_commits_normalized.jsonl"
    out_report = folder / "transcripts" / "STABLE_COMMIT_LINEAGE_RECONCILIATION.json"
    atomic_write_jsonl(out_jsonl, result["rows"])
    report = dict(result["report"])
    report["normalized_path"] = str(out_jsonl)
    atomic_write_json(out_report, report)
    report["report_path"] = str(out_report)
    return report
