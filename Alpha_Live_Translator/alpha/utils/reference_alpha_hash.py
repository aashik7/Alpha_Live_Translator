"""Alpha/reference hash binding for benchmark reports (8.5.23.4)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: str | Path) -> str:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def file_size_bytes(path: str | Path) -> int:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return 0
    return int(p.stat().st_size)


def normalize_path(path: str | Path) -> str:
    try:
        return str(Path(path).resolve()).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def paths_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    pa = Path(a)
    pb = Path(b)
    if pa == pb:
        return True
    if pa.name and pa.name == pb.name:
        return True
    return normalize_path(a).endswith(normalize_path(b)) or normalize_path(b).endswith(normalize_path(a))


def latest_live_run_id() -> str:
    for candidate in (
        Path("troubleshooting/latest/LATEST_RUN_POINTER.json"),
        Path("troubleshooting/latest/latest_accuracy_evidence_index.json"),
    ):
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            rid = str(data.get("run_id") or data.get("latest_run_id") or "").strip()
            if rid:
                return rid
        except Exception:
            pass
    return ""


def bind_report_hashes(
    report: dict[str, Any],
    *,
    alpha_path: str,
    reference_path: str = "",
    report_type: str = "",
) -> dict[str, Any]:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("REFERENCE_ALPHA_HASH_BINDING_STARTED", report_type=report_type)
    except Exception:
        pass
    report["alpha_path"] = alpha_path
    report["alpha_sha256"] = file_sha256(alpha_path)
    report["alpha_size_bytes"] = file_size_bytes(alpha_path)
    if reference_path:
        report["reference_path"] = reference_path
        report["reference_sha256"] = file_sha256(reference_path)
        report["reference_size_bytes"] = file_size_bytes(reference_path)
    run_id = latest_live_run_id()
    if run_id:
        report["latest_live_run_id"] = run_id
    if report_type:
        report["report_type"] = report_type
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("ALPHA_HASH_WRITTEN_TO_REPORT", alpha_sha256=report.get("alpha_sha256", "")[:16])
        if reference_path:
            jp_accuracy_log(
                "REFERENCE_HASH_WRITTEN_TO_REPORT",
                reference_sha256=report.get("reference_sha256", "")[:16],
            )
        jp_accuracy_log("REFERENCE_ALPHA_HASH_BINDING_COMPLETED", report_type=report_type)
    except Exception:
        pass
    return report
