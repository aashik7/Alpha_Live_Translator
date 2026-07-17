"""Prepared reference trust evaluation for offline scoring (V25.3.2.1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SUPPORTED_NORMALIZATION_VERSIONS = frozenset({"v25.3.2_nfkc_strip_speaker"})

PREPARED_DIR = Path("troubleshooting/accuracy_benchmark/prepared/v3.3.5.5.8.5.25.3.2")
PREPARED_SNAPSHOT = PREPARED_DIR / "reference_snapshot.json"
PREPARED_REFERENCE = PREPARED_DIR / "reference.txt"
PREPARED_QUALITY = PREPARED_DIR / "reference_quality_report.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_prepared_reference_trust(reference_path: str | Path | None = None) -> dict[str, Any]:
    ref_path = Path(reference_path) if reference_path else PREPARED_REFERENCE
    result: dict[str, Any] = {
        "trusted": False,
        "verdict": "invalid_for_cer",
        "should_run_cer": False,
        "reference_sha256": "",
        "snapshot_sha256": "",
        "hash_match": False,
        "failure_reasons": ["prepared_snapshot_missing"],
        "trust_reason": "prepared_snapshot_missing",
        "prepared_reference_only": False,
        "normalization_version": "",
        "reference_path": str(ref_path),
        "snapshot_path": str(PREPARED_REFERENCE),
    }

    if not PREPARED_SNAPSHOT.exists():
        return result

    try:
        snap = json.loads(PREPARED_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception as exc:
        result["failure_reasons"] = [f"snapshot_parse_error:{exc}"]
        result["trust_reason"] = "snapshot_parse_error"
        return result

    quality: dict[str, Any] = {}
    if PREPARED_QUALITY.exists():
        try:
            quality = json.loads(PREPARED_QUALITY.read_text(encoding="utf-8"))
        except Exception:
            quality = {}

    snapshot_path = Path(str(snap.get("snapshot_path", PREPARED_REFERENCE)))
    if not snapshot_path.exists():
        snapshot_path = PREPARED_REFERENCE

    actual_ref = Path(ref_path) if ref_path.exists() else snapshot_path
    ref_sha = _sha256_file(actual_ref) if actual_ref.exists() else ""
    snap_sha = str(snap.get("snapshot_sha256", ""))
    hash_match = bool(ref_sha and snap_sha and ref_sha == snap_sha)

    verdict = str(snap.get("reference_quality_verdict") or quality.get("verdict") or "")
    valid_for_cer = bool(snap.get("valid_for_cer"))
    should_run_cer = bool(snap.get("should_run_cer", quality.get("should_run_cer", valid_for_cer)))
    failure_reasons = list(snap.get("failure_reasons") or quality.get("failure_reasons") or [])
    prepared_only = bool(snap.get("prepared_reference_only") or quality.get("prepared_reference_only"))
    norm_version = str(snap.get("normalization_version", ""))

    result.update(
        {
            "verdict": verdict,
            "should_run_cer": should_run_cer,
            "reference_sha256": ref_sha,
            "snapshot_sha256": snap_sha,
            "hash_match": hash_match,
            "failure_reasons": failure_reasons,
            "prepared_reference_only": prepared_only,
            "normalization_version": norm_version,
            "snapshot_path": str(snapshot_path),
        }
    )

    if not valid_for_cer:
        result["trust_reason"] = "valid_for_cer_false"
        return result
    if verdict != "valid_for_cer":
        result["trust_reason"] = f"verdict_not_valid:{verdict}"
        return result
    if not should_run_cer:
        result["trust_reason"] = "should_run_cer_false"
        return result
    if failure_reasons:
        result["trust_reason"] = "failure_reasons_present"
        return result
    if not hash_match:
        result["trust_reason"] = "reference_hash_mismatch"
        return result
    if norm_version and norm_version not in SUPPORTED_NORMALIZATION_VERSIONS:
        result["trust_reason"] = f"unsupported_normalization:{norm_version}"
        return result

    result["trusted"] = True
    result["trust_reason"] = "prepared_reference_valid"
    result["failure_reasons"] = []
    return result
