"""Prepare deterministic reference snapshot for accuracy benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from alpha.constants import APP_VERSION, DETERMINISTIC_REFERENCE_SNAPSHOT_ENABLED
from alpha.utils.canonical_content_hash import normalize_text_content, normalized_file_sha256
from alpha.utils.validation_version import VALIDATION_PATCH_VERSION
from reference_transcript_quality_check import check_prepared_reference_snapshot_quality

NORMALIZATION_VERSION = "v25.3.3.2.2_faithful_copy_normalized_identity"


def normalize_reference(text: str) -> str:
    """Legacy helper retained for CER tooling — not used for prepared reference.txt identity."""
    import unicodedata

    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
        if m:
            line = m.group(1).strip()
        lines.append(unicodedata.normalize("NFC", line))
    return "\n".join(lines) + ("\n" if lines else "")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if not DETERMINISTIC_REFERENCE_SNAPSHOT_ENABLED:
        print("DETERMINISTIC_REFERENCE_SNAPSHOT_ENABLED is false")
        return 1
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument(
        "--output-version",
        default=VALIDATION_PATCH_VERSION,
        help="Prepared folder version (default: VALIDATION_PATCH_VERSION)",
    )
    parser.add_argument(
        "--output-root",
        default="troubleshooting/accuracy_benchmark/prepared",
        help="Root directory for prepared reference folders",
    )
    args = parser.parse_args()
    source = Path(args.reference)
    if not source.exists() or source.stat().st_size <= 0:
        print(f"Reference missing or empty: {source}")
        return 1

    prepared_dir = Path(args.output_root) / f"v{args.output_version}"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = prepared_dir / "reference.txt"
    # Faithful copy — do not speaker-strip or mutate transcript for identity evidence
    snapshot_path.write_bytes(source.read_bytes())

    source_norm = normalized_file_sha256(source)
    snap_norm = normalized_file_sha256(snapshot_path)
    source_text = source.read_text(encoding="utf-8")
    quality = check_prepared_reference_snapshot_quality(
        source_text, reference_path=str(source)
    )
    verdict = str(quality.get("verdict", "invalid_for_cer"))
    valid = bool(quality.get("valid_for_cer"))
    hash_match = source_norm == snap_norm

    ref_snapshot = {
        "app_version": APP_VERSION,
        "validation_patch_version": VALIDATION_PATCH_VERSION,
        "preparation_version": str(args.output_version),
        "output_version": str(args.output_version),
        "source_path": str(source),
        "snapshot_path": str(snapshot_path),
        "source_sha256": sha256_file(source),
        "snapshot_sha256": sha256_file(snapshot_path),
        "source_normalized_sha256": source_norm,
        "snapshot_normalized_sha256": snap_norm,
        "normalized_sha256_match": hash_match,
        "normalization_version": NORMALIZATION_VERSION,
        "reference_quality_verdict": verdict,
        "valid_for_cer": valid and hash_match,
        "failure_reasons": list(quality.get("failure_reasons") or [])
        + ([] if hash_match else ["normalized_sha256_mismatch"]),
        "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "created_by": "validation",
        "faithful_copy": True,
    }
    (prepared_dir / "reference_snapshot.json").write_text(
        json.dumps(ref_snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (prepared_dir / "reference_quality_report.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Prepared reference: {snapshot_path}")
    print(f"valid_for_cer={valid and hash_match}")
    print(f"normalized_sha256_match={hash_match}")
    return 0 if valid and hash_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
