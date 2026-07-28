"""Independent delivery verifier for Issue-12 readiness (85253328).

Uses only the Python standard library. Must not import the package builder.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

VERSION = "3.3.5.5.8.5.25.3.3.2.8"
EXPECTED_FINAL_SHA256 = "6e70dd171862527da2f2de0305ab82154cf9c1591b73860e4fd75e06f570c178"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def verify(
    *,
    root: Path,
    build_id: str,
    phase_root: Path,
    verification_dir: Path,
) -> dict[str, Any]:
    outer = phase_root / f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.zip"
    sidecar = phase_root / f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.sha256.json"
    final_acc = phase_root / f"ISSUE12_READINESS_FINAL_ACCEPTANCE_{build_id}.json"

    if not outer.exists():
        raise RuntimeError("outer_bundle_missing")
    if not sidecar.exists():
        raise RuntimeError("sidecar_missing")

    actual_sha = sha256_file(outer)
    actual_size = outer.stat().st_size
    side = load_json(sidecar)

    outer_hash_matches = actual_sha == side.get("outer_bundle_sha256")
    outer_size_matches = actual_size == side.get("outer_bundle_size")

    metadata_hash_matches = False
    source_bundle_integrity_passed = False
    pending_acceptance_correctly_classified = False

    with zipfile.ZipFile(outer, "r") as zf:
        if zf.testzip() is not None:
            raise RuntimeError("outer_zip_integrity_failed")
        names = zf.namelist()
        pending = json.loads(zf.read("acceptance/ISSUE12_READINESS_PENDING_ACCEPTANCE.json").decode("utf-8"))
        pending_acceptance_correctly_classified = (
            pending.get("outer_bundle_verified") is False
            and pending.get("VERSION") == "PENDING_POST_WRITE_VERIFICATION"
            and pending.get("STATUS") == "PENDING"
            and "outer_bundle_sha256" not in pending
            and "outer_bundle_size" not in pending
            and pending.get("VERSION") != "ACCEPTED"
        )

        pkg_state = json.loads(zf.read("metadata/PROJECT_STATE.json").decode("utf-8"))
        pkg_index = json.loads(zf.read("metadata/LATEST_EVIDENCE_INDEX.json").decode("utf-8"))
        pkg_state_bytes = zf.read("metadata/PROJECT_STATE.json")
        pkg_state_sha = hashlib.sha256(pkg_state_bytes).hexdigest()
        metadata_hash_matches = (
            pkg_state_sha == pkg_index.get("project_state_sha256")
            and pkg_state.get("issue12_readiness_build_id") == build_id
            and pkg_index.get("current_build_id") == build_id
            and pkg_index.get("current_closure_version") == VERSION
        )

        source_bytes = zf.read("evidence/FROZEN_NINE_ISSUE_SOURCE_OUTER_BUNDLE.zip")
        with zipfile.ZipFile(io.BytesIO(source_bytes), "r") as src:
            source_bundle_integrity_passed = src.testzip() is None and len(src.namelist()) > 0

    final_exists_outside = final_acc.exists()
    # Final acceptance is created after this verifier in some flows; require outside for final check flag.
    # When called before final acceptance exists, still record that pending is not final.
    independent_verification_passed = (
        outer_hash_matches
        and outer_size_matches
        and metadata_hash_matches
        and source_bundle_integrity_passed
        and pending_acceptance_correctly_classified
        # final acceptance may be written after this step; checker below records existence
    )

    result = {
        "build_id": build_id,
        "version": VERSION,
        "actual_outer_bundle_sha256": actual_sha,
        "sidecar_outer_bundle_sha256": side.get("outer_bundle_sha256"),
        "outer_hash_matches": outer_hash_matches,
        "actual_outer_bundle_size": actual_size,
        "sidecar_outer_bundle_size": side.get("outer_bundle_size"),
        "outer_size_matches": outer_size_matches,
        "metadata_hash_matches": metadata_hash_matches,
        "source_bundle_integrity_passed": source_bundle_integrity_passed,
        "pending_acceptance_correctly_classified": pending_acceptance_correctly_classified,
        "final_delivery_acceptance_exists_outside": final_exists_outside,
        "independent_verification_passed": independent_verification_passed,
        "outer_entry_count": len(names) if "names" in locals() else None,
        "expected_final_sha256": EXPECTED_FINAL_SHA256,
    }
    write_json(verification_dir / "INDEPENDENT_DELIVERY_VERIFICATION.json", result)
    # Also copy to phase root for analysis package packaging convenience
    write_json(phase_root / "INDEPENDENT_DELIVERY_VERIFICATION.json", result)
    if not independent_verification_passed:
        raise RuntimeError(f"independent_verification_failed:{json.dumps(result, sort_keys=True)}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent Issue-12 delivery verifier.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--phase-root", required=True)
    parser.add_argument("--verification-dir", required=True)
    args = parser.parse_args()
    try:
        result = verify(
            root=Path(args.project_root).resolve(),
            build_id=args.build_id,
            phase_root=Path(args.phase_root),
            verification_dir=Path(args.verification_dir),
        )
        print(f"independent_verification_passed={result['independent_verification_passed']}")
        print(f"outer_hash_matches={result['outer_hash_matches']}")
        print(f"outer_size_matches={result['outer_size_matches']}")
        print(f"metadata_hash_matches={result['metadata_hash_matches']}")
        print("STATUS=PASSED")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"INVARIANT={exc}")
        print("STATUS=FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
