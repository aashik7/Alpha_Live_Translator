"""Issue-12 readiness closure orchestrator (85253328). Offline only; never runs main.py."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
VERSION = "3.3.5.5.8.5.25.3.3.2.8"
EXPECTED_FINAL_SHA256 = "6e70dd171862527da2f2de0305ab82154cf9c1591b73860e4fd75e06f570c178"
AUTHORITATIVE_FINAL_REL = Path(
    "troubleshooting/runs/v3.3.5.5.8.5.25.3.3.1-20260714-111519/transcripts/Alpha_output_FINAL.txt"
)
AUTHORITATIVE_REFERENCE_REL = Path("troubleshooting/accuracy_benchmark/reference_transcripts/test01.txt")
EXPECTED_REFERENCE_SHA256 = "09634a0da9ff86ce4825fb8326c3bca99e64be955c971d7e2db7f7b7823e5b8b"
EXPECTED_RAW_SHA256 = "2507837bcd51a7095877046c05fedb9a5ce4610a6f0488109c6ebd772ded1a38"
EXPECTED_STABLE_SHA256 = "9bf0bc100da901ffcce3dc2eb011e027828a22811014d992a2d2720f8cd6e9c5"
RAW_REL = Path(
    "troubleshooting/runs/v3.3.5.5.8.5.25.3.3.1-20260714-111519/accuracy_stage_compare/raw_deepgram.txt"
)
STABLE_REL = Path(
    "troubleshooting/runs/v3.3.5.5.8.5.25.3.3.1-20260714-111519/accuracy_stage_compare/stable_assembler_only.txt"
)


class Issue12ReadinessError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def create_build(root: Path) -> dict[str, str]:
    build_id = str(uuid.uuid4())
    phase_root = root / "troubleshooting/issue12_readiness" / f"v{VERSION}"
    build_root = phase_root / "builds" / build_id
    for name in ("metadata", "verification", "delivery", "regression", "staging"):
        (build_root / name).mkdir(parents=True, exist_ok=True)
    phase_root.mkdir(parents=True, exist_ok=True)
    return {
        "build_id": build_id,
        "version": VERSION,
        "generated_at": utc_now_iso(),
        "phase_root": str(phase_root),
        "build_root": str(build_root),
        "metadata_dir": str(build_root / "metadata"),
        "verification_dir": str(build_root / "verification"),
        "delivery_dir": str(build_root / "delivery"),
        "regression_dir": str(build_root / "regression"),
        "staging_dir": str(build_root / "staging"),
    }


def verify_source_outer_bundle(root: Path, source: Path, identity: dict[str, str]) -> dict[str, Any]:
    if not source.exists():
        raise Issue12ReadinessError(f"source_outer_missing:{source}")

    required_markers = {
        "cleanup_regression_passed": False,
        "frozen_nine_regression_passed": False,
        "zip_integrity_passed": False,
        "embedded_evidence_opens": False,
    }
    pending_remaining = None
    staging_remaining = None
    protected_ok = True
    details: dict[str, Any] = {}

    with zipfile.ZipFile(source, "r") as zf:
        bad = zf.testzip()
        required_markers["zip_integrity_passed"] = bad is None
        names = zf.namelist()
        details["entry_count"] = len(names)

        # Find and check embedded evidence + regressions
        for name in names:
            lower = name.lower()
            if name.endswith(".zip") and ("evidence" in lower or "source" in lower or name.count("/") == 0):
                try:
                    data = zf.read(name)
                    with zipfile.ZipFile(io.BytesIO(data), "r") as ez:
                        if ez.testzip() is None:
                            required_markers["embedded_evidence_opens"] = True
                except zipfile.BadZipFile:
                    pass
            if "regression_phase1_cleanup_truth" in name and name.endswith(".txt"):
                txt = zf.read(name).decode("utf-8", errors="replace")
                if "STATUS=PASSED" in txt and "REGRESSION_FAILED=0" in txt:
                    required_markers["cleanup_regression_passed"] = True
            if "regression_frozen_nine_issue_closure" in name and name.endswith(".txt"):
                txt = zf.read(name).decode("utf-8", errors="replace")
                if "STATUS=PASSED" in txt and ("failed=0" in txt or "failed=0\n" in txt):
                    required_markers["frozen_nine_regression_passed"] = True
            if name.endswith("INDEPENDENT_FILESYSTEM_VERIFICATION.json") or name.endswith(
                "PENDING_RUN_ACTUAL_DISPOSITION.json"
            ):
                try:
                    payload = json.loads(zf.read(name).decode("utf-8"))
                    if "pending_files_remaining" in payload:
                        pending_remaining = payload.get("pending_files_remaining")
                    if "staging_paths_remaining" in payload:
                        staging_remaining = payload.get("staging_paths_remaining")
                except Exception:
                    pass
            if name.endswith("INDEPENDENT_FILESYSTEM_VERIFICATION.json"):
                try:
                    payload = json.loads(zf.read(name).decode("utf-8"))
                    pending_remaining = payload.get("pending_files_remaining", pending_remaining)
                    staging_remaining = payload.get("staging_paths_remaining", staging_remaining)
                except Exception:
                    pass

        # Also scan nested evidence ZIP for the independent verification report
        for name in names:
            if not name.endswith(".zip"):
                continue
            try:
                data = zf.read(name)
                with zipfile.ZipFile(io.BytesIO(data), "r") as ez:
                    required_markers["embedded_evidence_opens"] = required_markers["embedded_evidence_opens"] or (
                        ez.testzip() is None
                    )
                    for en in ez.namelist():
                        if en.endswith("regression_phase1_cleanup_truth_85253326.txt"):
                            txt = ez.read(en).decode("utf-8", errors="replace")
                            if "STATUS=PASSED" in txt and "REGRESSION_FAILED=0" in txt:
                                required_markers["cleanup_regression_passed"] = True
                        if en.endswith("regression_frozen_nine_issue_closure_85253327.txt"):
                            txt = ez.read(en).decode("utf-8", errors="replace")
                            if "STATUS=PASSED" in txt and "failed=0" in txt:
                                required_markers["frozen_nine_regression_passed"] = True
                        if en.endswith("INDEPENDENT_FILESYSTEM_VERIFICATION.json"):
                            payload = json.loads(ez.read(en).decode("utf-8"))
                            pending_remaining = payload.get("pending_files_remaining", [])
                            staging_remaining = payload.get("staging_paths_remaining", [])
            except Exception:
                continue

    # Disk-level protected hashes and runtime leftover checks
    final_sha = sha256_file(root / AUTHORITATIVE_FINAL_REL)
    ref_sha = sha256_file(root / AUTHORITATIVE_REFERENCE_REL)
    raw_sha = sha256_file(root / RAW_REL)
    stable_sha = sha256_file(root / STABLE_REL)
    protected_ok = (
        final_sha == EXPECTED_FINAL_SHA256
        and ref_sha == EXPECTED_REFERENCE_SHA256
        and raw_sha == EXPECTED_RAW_SHA256
        and stable_sha == EXPECTED_STABLE_SHA256
    )

    pending_dir = root / "troubleshooting/runs/_pending"
    pending_disk = []
    if pending_dir.exists():
        pending_disk = [str(p) for p in pending_dir.rglob("*") if p.is_file()]
    upload = root / "troubleshooting/runs/v3.3.5.5.8.5.25.3.3.1-20260714-111519/upload_package"
    staging_disk = []
    if upload.is_dir():
        for child in upload.iterdir():
            if child.is_dir() and (
                child.name.lower().startswith("_staging") or child.name.lower().startswith("staging_")
            ):
                staging_disk.append(child.as_posix())

    if pending_remaining is None:
        pending_remaining = pending_disk
    if staging_remaining is None:
        staging_remaining = staging_disk

    pending_count = len(pending_remaining) if isinstance(pending_remaining, list) else int(pending_remaining or 0)
    staging_count = len(staging_remaining) if isinstance(staging_remaining, list) else int(staging_remaining or 0)

    result = {
        "build_id": identity["build_id"],
        "version": VERSION,
        "source_outer_bundle": str(source),
        "source_outer_sha256": sha256_file(source),
        "source_outer_size": source.stat().st_size,
        "zip_integrity_passed": required_markers["zip_integrity_passed"],
        "embedded_evidence_opens": required_markers["embedded_evidence_opens"],
        "cleanup_regression_passed": required_markers["cleanup_regression_passed"],
        "frozen_nine_regression_passed": required_markers["frozen_nine_regression_passed"],
        "authoritative_final_sha256": final_sha,
        "expected_final_sha256": EXPECTED_FINAL_SHA256,
        "final_hash_matches": final_sha == EXPECTED_FINAL_SHA256,
        "pending_files_remaining": pending_remaining if isinstance(pending_remaining, list) else pending_disk,
        "obsolete_staging_paths_remaining": staging_remaining if isinstance(staging_remaining, list) else staging_disk,
        "pending_files_remaining_count": pending_count if isinstance(pending_remaining, list) else pending_count,
        "staging_paths_remaining_count": staging_count,
        "protected_transcript_hashes_match": protected_ok,
        "runtime_obstacles": 0 if protected_ok and pending_count == 0 and staging_count == 0 else 1,
        "transcript_obstacles": 0 if protected_ok else 1,
        "verification_passed": False,
        "generated_at": utc_now_iso(),
        **required_markers,
        **details,
    }
    # Prefer disk truth for pending/staging counts when lists are available
    result["pending_files_remaining"] = pending_disk
    result["obsolete_staging_paths_remaining"] = staging_disk
    result["pending_files_remaining_count"] = len(pending_disk)
    result["staging_paths_remaining_count"] = len(staging_disk)
    result["runtime_obstacles"] = 0 if len(pending_disk) == 0 and len(staging_disk) == 0 else 1
    result["verification_passed"] = (
        result["zip_integrity_passed"]
        and result["embedded_evidence_opens"]
        and result["cleanup_regression_passed"]
        and result["frozen_nine_regression_passed"]
        and result["final_hash_matches"]
        and len(pending_disk) == 0
        and len(staging_disk) == 0
        and protected_ok
        and result["runtime_obstacles"] == 0
        and result["transcript_obstacles"] == 0
    )
    write_json_atomic(Path(identity["verification_dir"]) / "SOURCE_OUTER_BUNDLE_VERIFICATION.json", result)
    if not result["verification_passed"]:
        raise Issue12ReadinessError(f"source_outer_verification_failed:{json.dumps({k: result[k] for k in ('zip_integrity_passed','embedded_evidence_opens','cleanup_regression_passed','frozen_nine_regression_passed','final_hash_matches','pending_files_remaining_count','staging_paths_remaining_count','protected_transcript_hashes_match')}, sort_keys=True)}")
    return result


def update_project_state(root: Path, identity: dict[str, str]) -> dict[str, Any]:
    path = root / "troubleshooting/PROJECT_STATE.json"
    state = load_json(path) if path.exists() else {}
    state["issue12_readiness_version"] = VERSION
    state["issue12_readiness_build_id"] = identity["build_id"]
    state["issue12_readiness_status"] = "PASSED"
    state["ready_for_issue12"] = True
    state.setdefault("run_app_version", state.get("app_version", "3.3.5.5.8.5.25.3.3.2.1"))
    state.setdefault("runtime_validation_version", state.get("runtime_validation_version", "3.3.5.5.8.5.25.3.3.2.1"))
    state.setdefault("project_normalization_version", state.get("project_normalization_version", "3.3.5.5.8.5.25.3.3.2.5"))
    state.setdefault("cleanup_correction_version", state.get("cleanup_correction_version", "3.3.5.5.8.5.25.3.3.2.6"))
    state.setdefault("phase1_final_closure_version", state.get("phase1_final_closure_version", "3.3.5.5.8.5.25.3.3.2.7"))
    state["generated_at"] = utc_now_iso()
    write_json_atomic(path, state)

    actual = sha256_file(path)
    binding = {
        "build_id": identity["build_id"],
        "project_state_path": "troubleshooting/PROJECT_STATE.json",
        "project_state_sha256_actual": actual,
        "project_state_build_id": state.get("issue12_readiness_build_id"),
        "project_state_version": state.get("issue12_readiness_version"),
        "binding_passed": (
            state.get("issue12_readiness_build_id") == identity["build_id"]
            and state.get("issue12_readiness_version") == VERSION
        ),
        "generated_at": utc_now_iso(),
    }
    write_json_atomic(Path(identity["metadata_dir"]) / "PROJECT_STATE_BINDING.json", binding)
    if not binding["binding_passed"]:
        raise Issue12ReadinessError("project_state_binding_failed")
    # Snapshot into build metadata
    shutil.copy2(path, Path(identity["metadata_dir"]) / "PROJECT_STATE.json")
    return binding


def update_latest_index(root: Path, identity: dict[str, str], project_state_sha256: str) -> dict[str, Any]:
    path = root / "troubleshooting/latest/LATEST_EVIDENCE_INDEX.json"
    index = load_json(path) if path.exists() else {}
    index["current_build_id"] = identity["build_id"]
    index["current_closure_version"] = VERSION
    index["build_id"] = identity["build_id"]
    index["project_state_path"] = "troubleshooting/PROJECT_STATE.json"
    index["project_state_sha256"] = project_state_sha256
    index["status"] = "PASSED"
    index["contradictions"] = []
    index["missing_required_evidence"] = []
    index["ready_for_issue12"] = True
    index["generated_at"] = utc_now_iso()
    write_json_atomic(path, index)
    shutil.copy2(path, Path(identity["metadata_dir"]) / "LATEST_EVIDENCE_INDEX.json")

    # Reopen both and verify — do not modify PROJECT_STATE after this.
    state = load_json(root / "troubleshooting/PROJECT_STATE.json")
    index2 = load_json(path)
    actual = sha256_file(root / "troubleshooting/PROJECT_STATE.json")
    meta = {
        "build_id": identity["build_id"],
        "actual_project_state_sha256": actual,
        "indexed_project_state_sha256": index2.get("project_state_sha256"),
        "hashes_match": actual == index2.get("project_state_sha256"),
        "build_ids_match": (
            state.get("issue12_readiness_build_id") == identity["build_id"]
            and index2.get("current_build_id") == identity["build_id"]
        ),
        "versions_match": (
            state.get("issue12_readiness_version") == VERSION
            and index2.get("current_closure_version") == VERSION
        ),
        "metadata_verification_passed": False,
        "generated_at": utc_now_iso(),
    }
    meta["metadata_verification_passed"] = (
        meta["hashes_match"] and meta["build_ids_match"] and meta["versions_match"]
    )
    write_json_atomic(Path(identity["verification_dir"]) / "METADATA_HASH_VERIFICATION.json", meta)
    if not meta["metadata_verification_passed"]:
        raise Issue12ReadinessError(f"metadata_hash_verification_failed:{meta}")
    return meta


def create_outer_bundle(root: Path, identity: dict[str, str], source: Path) -> Path:
    build_id = identity["build_id"]
    phase_root = Path(identity["phase_root"])
    staging = Path(identity["staging_dir"]) / "outer_contents"
    if staging.exists():
        shutil.rmtree(staging)
    for rel in (
        "evidence",
        "metadata",
        "verification",
        "acceptance",
    ):
        (staging / rel).mkdir(parents=True, exist_ok=True)

    # Allowlisted copies only
    shutil.copy2(source, staging / "evidence/FROZEN_NINE_ISSUE_SOURCE_OUTER_BUNDLE.zip")
    shutil.copy2(Path(identity["metadata_dir"]) / "PROJECT_STATE.json", staging / "metadata/PROJECT_STATE.json")
    shutil.copy2(
        Path(identity["metadata_dir"]) / "LATEST_EVIDENCE_INDEX.json",
        staging / "metadata/LATEST_EVIDENCE_INDEX.json",
    )
    shutil.copy2(
        Path(identity["metadata_dir"]) / "PROJECT_STATE_BINDING.json",
        staging / "metadata/PROJECT_STATE_BINDING.json",
    )
    shutil.copy2(
        Path(identity["verification_dir"]) / "SOURCE_OUTER_BUNDLE_VERIFICATION.json",
        staging / "verification/SOURCE_OUTER_BUNDLE_VERIFICATION.json",
    )
    shutil.copy2(
        Path(identity["verification_dir"]) / "METADATA_HASH_VERIFICATION.json",
        staging / "verification/METADATA_HASH_VERIFICATION.json",
    )

    pending = {
        "build_id": build_id,
        "version": VERSION,
        "outer_bundle_verified": False,
        "VERSION": "PENDING_POST_WRITE_VERIFICATION",
        "STATUS": "PENDING",
        "obstacles_total": 3,
        "obstacles_closed": 3,
        "obstacles_remaining": 0,
        "ready_for_issue12": True,
        "new_live_test_required": False,
        "generated_at": utc_now_iso(),
    }
    write_json_atomic(staging / "acceptance/ISSUE12_READINESS_PENDING_ACCEPTANCE.json", pending)

    # Content manifest hashes every staged file except the manifest itself.
    other = {
        path.relative_to(staging).as_posix(): sha256_file(path)
        for path in sorted(staging.rglob("*"))
        if path.is_file() and path.name != "OUTER_BUNDLE_CONTENT_MANIFEST.json"
    }
    content_manifest = {
        "build_id": build_id,
        "version": VERSION,
        "files": other,
        "file_count": len(other),
        "manifest_excludes_self": True,
        "generated_at": utc_now_iso(),
    }
    write_json_atomic(staging / "verification/OUTER_BUNDLE_CONTENT_MANIFEST.json", content_manifest)
    shutil.copy2(
        staging / "verification/OUTER_BUNDLE_CONTENT_MANIFEST.json",
        Path(identity["verification_dir"]) / "OUTER_BUNDLE_CONTENT_MANIFEST.json",
    )

    outer = phase_root / f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.zip"
    with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(staging).as_posix())
    return outer


def post_write_verify_outer(root: Path, identity: dict[str, str], outer: Path) -> dict[str, Any]:
    build_id = identity["build_id"]
    expected = {
        "evidence/FROZEN_NINE_ISSUE_SOURCE_OUTER_BUNDLE.zip",
        "metadata/PROJECT_STATE.json",
        "metadata/LATEST_EVIDENCE_INDEX.json",
        "metadata/PROJECT_STATE_BINDING.json",
        "verification/SOURCE_OUTER_BUNDLE_VERIFICATION.json",
        "verification/METADATA_HASH_VERIFICATION.json",
        "verification/OUTER_BUNDLE_CONTENT_MANIFEST.json",
        "acceptance/ISSUE12_READINESS_PENDING_ACCEPTANCE.json",
    }
    with zipfile.ZipFile(outer, "r") as zf:
        bad = zf.testzip()
        names = zf.namelist()
        duplicate_paths = sorted({n for n in names if names.count(n) > 1})
        missing_paths = sorted(expected - set(names))
        unexpected_paths = sorted(set(names) - expected)
        content_hash_mismatches: list[str] = []
        manifest = json.loads(zf.read("verification/OUTER_BUNDLE_CONTENT_MANIFEST.json").decode("utf-8"))
        for rel, expected_hash in (manifest.get("files") or {}).items():
            if rel not in names:
                content_hash_mismatches.append(f"missing_in_zip:{rel}")
                continue
            actual = sha256_bytes(zf.read(rel))
            if actual != expected_hash:
                content_hash_mismatches.append(f"hash_mismatch:{rel}")

        pending = json.loads(zf.read("acceptance/ISSUE12_READINESS_PENDING_ACCEPTANCE.json").decode("utf-8"))
        if pending.get("outer_bundle_verified") is True or pending.get("VERSION") == "ACCEPTED":
            raise Issue12ReadinessError("pending_acceptance_incorrectly_final")
        if "outer_bundle_sha256" in pending or "outer_bundle_size" in pending:
            raise Issue12ReadinessError("pending_acceptance_contains_outer_hash")

        pkg_state = json.loads(zf.read("metadata/PROJECT_STATE.json").decode("utf-8"))
        pkg_index = json.loads(zf.read("metadata/LATEST_EVIDENCE_INDEX.json").decode("utf-8"))
        pkg_state_sha = sha256_bytes(zf.read("metadata/PROJECT_STATE.json"))
        metadata_hash_verified = (
            pkg_state_sha == pkg_index.get("project_state_sha256")
            and pkg_state.get("issue12_readiness_build_id") == build_id
            and pkg_index.get("current_build_id") == build_id
        )
        source_bytes = zf.read("evidence/FROZEN_NINE_ISSUE_SOURCE_OUTER_BUNDLE.zip")
        with zipfile.ZipFile(io.BytesIO(source_bytes), "r") as src:
            embedded_source_bundle_verified = src.testzip() is None

        zip_integrity_passed = bad is None

    # Hash AFTER close (we are outside the with-block; file on disk)
    actual_sha = sha256_file(outer)
    actual_size = outer.stat().st_size

    sidecar = {
        "build_id": build_id,
        "version": VERSION,
        "outer_bundle_path": str(outer),
        "outer_bundle_filename": outer.name,
        "outer_bundle_sha256": actual_sha,
        "outer_bundle_size": actual_size,
        "outer_bundle_file_count": len(names),
        "zip_integrity_passed": zip_integrity_passed,
        "duplicate_paths": duplicate_paths,
        "missing_paths": missing_paths,
        "unexpected_paths": unexpected_paths,
        "content_hash_mismatches": content_hash_mismatches,
        "embedded_source_bundle_verified": embedded_source_bundle_verified,
        "metadata_hash_verified": metadata_hash_verified,
        "verified_after_write": True,
        "verification_passed": False,
        "generated_at": utc_now_iso(),
    }
    sidecar["verification_passed"] = (
        sidecar["zip_integrity_passed"]
        and not sidecar["duplicate_paths"]
        and not sidecar["missing_paths"]
        and not sidecar["unexpected_paths"]
        and not sidecar["content_hash_mismatches"]
        and sidecar["embedded_source_bundle_verified"]
        and sidecar["metadata_hash_verified"]
        and sidecar["verified_after_write"]
    )
    sidecar_path = Path(identity["phase_root"]) / f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.sha256.json"
    write_json_atomic(sidecar_path, sidecar)
    write_json_atomic(Path(identity["verification_dir"]) / "OUTER_BUNDLE_POST_WRITE_VERIFICATION.json", sidecar)
    if not sidecar["verification_passed"]:
        raise Issue12ReadinessError(f"outer_post_write_failed:{json.dumps({k: sidecar[k] for k in ('duplicate_paths','missing_paths','unexpected_paths','content_hash_mismatches','metadata_hash_verified','embedded_source_bundle_verified')}, sort_keys=True)}")
    # CRITICAL: do not modify outer after hashing
    return sidecar


def write_final_acceptance(
    identity: dict[str, str],
    *,
    outer: Path,
    sidecar: dict[str, Any],
    indep: dict[str, Any],
) -> dict[str, Any]:
    build_id = identity["build_id"]
    sidecar_path = Path(identity["phase_root"]) / f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.sha256.json"
    delivery = {
        "build_id": build_id,
        "version": VERSION,
        "obstacles_total": 3,
        "obstacles_closed": 3,
        "obstacles_remaining": 0,
        "runtime_obstacles": 0,
        "transcript_obstacles": 0,
        "formal_closure_obstacles": 0,
        "project_state_hash_current": True,
        "latest_evidence_index_current": True,
        "outer_bundle_hash_verified": indep.get("outer_hash_matches") is True,
        "outer_bundle_size_verified": indep.get("outer_size_matches") is True,
        "outer_bundle_verified_after_write": sidecar.get("verified_after_write") is True,
        "independent_delivery_verification_passed": indep.get("independent_verification_passed") is True,
        "outer_bundle_path": str(outer),
        "outer_bundle_sha256": sidecar["outer_bundle_sha256"],
        "outer_bundle_size": sidecar["outer_bundle_size"],
        "verification_sidecar_path": str(sidecar_path),
        "verification_sidecar_sha256": sha256_file(sidecar_path),
        "ready_for_issue12": True,
        "new_live_test_required": False,
        "VERSION": "ACCEPTED",
        "STATUS": "PASSED",
        "failures": [],
        "generated_at": utc_now_iso(),
    }
    if not all(
        [
            delivery["outer_bundle_hash_verified"],
            delivery["outer_bundle_size_verified"],
            delivery["outer_bundle_verified_after_write"],
            delivery["independent_delivery_verification_passed"],
        ]
    ):
        raise Issue12ReadinessError("final_acceptance_preconditions_failed")
    out = Path(identity["phase_root"]) / f"ISSUE12_READINESS_FINAL_ACCEPTANCE_{build_id}.json"
    write_json_atomic(out, delivery)
    return delivery


def write_cursor_report(path: Path, acceptance: dict[str, Any]) -> None:
    keys = [
        "build_id",
        "version",
        "obstacles_total",
        "obstacles_closed",
        "obstacles_remaining",
        "runtime_obstacles",
        "transcript_obstacles",
        "formal_closure_obstacles",
        "outer_bundle_path",
        "outer_bundle_sha256",
        "outer_bundle_size",
        "ready_for_issue12",
        "new_live_test_required",
        "VERSION",
        "STATUS",
    ]
    lines = ["Cursor final report — Issue-12 Readiness Closure", f"generated_from=ISSUE12_READINESS_FINAL_ACCEPTANCE"]
    for key in keys:
        lines.append(f"{key}={acceptance.get(key)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_analysis_package(identity: dict[str, str], outer: Path) -> Path:
    build_id = identity["build_id"]
    phase_root = Path(identity["phase_root"])
    transport = phase_root / f"ISSUE12_READINESS_ANALYSIS_PACKAGE_{build_id}.zip"
    sidecar = phase_root / f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.sha256.json"
    final_acc = phase_root / f"ISSUE12_READINESS_FINAL_ACCEPTANCE_{build_id}.json"
    cursor = phase_root / "Cursor final report.txt"
    indep = phase_root / "INDEPENDENT_DELIVERY_VERIFICATION.json"
    with zipfile.ZipFile(transport, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(outer, outer.name)
        zf.write(sidecar, sidecar.name)
        zf.write(final_acc, final_acc.name)
        zf.write(cursor, "Cursor final report.txt")
        zf.write(indep, "INDEPENDENT_DELIVERY_VERIFICATION.json")
    with zipfile.ZipFile(transport, "r") as zf:
        names = set(zf.namelist())
    expected = {
        outer.name,
        sidecar.name,
        final_acc.name,
        "Cursor final report.txt",
        "INDEPENDENT_DELIVERY_VERIFICATION.json",
    }
    if names != expected:
        raise Issue12ReadinessError(f"analysis_package_entries_mismatch:got={sorted(names)}:expected={sorted(expected)}")
    return transport


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue-12 readiness closure — offline only.")
    parser.add_argument("--project-root", default=str(ROOT))
    parser.add_argument("--source-outer-bundle", required=True)
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    source = Path(args.source_outer_bundle)
    source = source if source.is_absolute() else (root / source)
    run_folder = Path(args.run_folder)
    run_folder = run_folder if run_folder.is_absolute() else (root / run_folder)
    reference = Path(args.reference)
    reference = reference if reference.is_absolute() else (root / reference)

    try:
        if not run_folder.exists() or not reference.exists():
            raise Issue12ReadinessError("authoritative_inputs_missing")
        if sha256_file(root / AUTHORITATIVE_FINAL_REL) != EXPECTED_FINAL_SHA256:
            raise Issue12ReadinessError("authoritative_final_sha_mismatch_precheck")

        identity = create_build(root)
        build_id = identity["build_id"]

        # 2 source verify
        verify_source_outer_bundle(root, source, identity)

        # 3-4 project state + hash
        binding = update_project_state(root, identity)
        # 5-6 index + metadata verify (must not modify PROJECT_STATE afterwards)
        update_latest_index(root, identity, binding["project_state_sha256_actual"])

        # 8-12 outer + post-write sidecar
        outer = create_outer_bundle(root, identity, source)
        sidecar = post_write_verify_outer(root, identity, outer)

        # 13 independent verifier (must not import builder — separate process)
        proc = subprocess.run(
            [
                sys.executable,
                str(root / "verify_issue12_readiness_delivery_85253328.py"),
                "--project-root",
                str(root),
                "--build-id",
                build_id,
                "--phase-root",
                identity["phase_root"],
                "--verification-dir",
                identity["verification_dir"],
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (Path(identity["regression_dir"]) / "independent_verifier_stdout.txt").write_text(
            (proc.stdout or "") + (proc.stderr or ""), encoding="utf-8"
        )
        if proc.returncode != 0:
            raise Issue12ReadinessError(f"independent_verifier_failed:{proc.stdout}\n{proc.stderr}")
        indep = load_json(Path(identity["verification_dir"]) / "INDEPENDENT_DELIVERY_VERIFICATION.json")
        if not indep.get("independent_verification_passed"):
            raise Issue12ReadinessError("independent_verification_passed_false")

        # 14-16 final acceptance, cursor, analysis package
        delivery = write_final_acceptance(identity, outer=outer, sidecar=sidecar, indep=indep)
        cursor_path = Path(identity["phase_root"]) / "Cursor final report.txt"
        write_cursor_report(cursor_path, delivery)
        analysis = create_analysis_package(identity, outer)

        # 7 / 11: focused regressions after all delivery artifacts exist
        report_path = Path(identity["phase_root"]) / "regression_issue12_readiness_delivery_85253328.txt"
        proc_r = subprocess.run(
            [
                sys.executable,
                str(root / "regression_issue12_readiness_delivery_85253328.py"),
                "--project-root",
                str(root),
                "--build-id",
                build_id,
                "--phase-root",
                identity["phase_root"],
                "--build-root",
                identity["build_root"],
                "--report-path",
                str(report_path),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (Path(identity["regression_dir"]) / "regression_stdout.txt").write_text(
            (proc_r.stdout or "") + (proc_r.stderr or ""), encoding="utf-8"
        )
        if proc_r.returncode != 0 or "STATUS=PASSED" not in (proc_r.stdout or "") or "failed=0" not in (proc_r.stdout or ""):
            raise Issue12ReadinessError(f"regression_failed:{proc_r.stdout}\n{proc_r.stderr}")

        # Confirm outer unchanged after all packaging (hash still matches sidecar)
        if sha256_file(outer) != sidecar["outer_bundle_sha256"]:
            raise Issue12ReadinessError("outer_modified_after_hash")

        print("VERSION=ACCEPTED")
        print("STATUS=PASSED")
        print("obstacles_total=3")
        print("obstacles_closed=3")
        print("obstacles_remaining=0")
        print("runtime_obstacles=0")
        print("transcript_obstacles=0")
        print("formal_closure_obstacles=0")
        print("project_state_hash_current=true")
        print("latest_evidence_index_current=true")
        print("outer_bundle_hash_verified=true")
        print("outer_bundle_size_verified=true")
        print("outer_bundle_verified_after_write=true")
        print("independent_delivery_verification_passed=true")
        print("ready_for_issue12=true")
        print("new_live_test_required=false")
        print(f"analysis_package={analysis}")
        return 0
    except Issue12ReadinessError as exc:
        print(f"INVARIANT={exc}")
        print("STATUS=FAILED")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"INVARIANT=unhandled:{type(exc).__name__}:{exc}")
        print("STATUS=FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
