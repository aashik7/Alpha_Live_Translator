"""Unique Phase 1 project-normalization build identity (V3.3.5.5.8.5.25.3.3.2.5)."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATCH_VERSION = "3.3.5.5.8.5.25.3.3.2.5"
PATCH_CODENAME = "Phase 1 Project Normalization & Offline Hardening"

PHASE1_ROOT_REL = Path("troubleshooting") / "phase1_normalization" / f"v{PATCH_VERSION}"

BUILD_SUBDIRS = (
    "baseline",
    "inventory",
    "analysis",
    "archive",
    "quarantine",
    "reports",
    "regression",
    "package",
    "restore",
)

EXPECTED_FINAL_SHA256 = "6e70dd171862527da2f2de0305ab82154cf9c1591b73860e4fd75e06f570c178"

AUTHORITATIVE_RUN_REL = Path("troubleshooting") / "runs" / "v3.3.5.5.8.5.25.3.3.1-20260714-111519"
AUTHORITATIVE_REFERENCE_REL = (
    Path("troubleshooting") / "accuracy_benchmark" / "reference_transcripts" / "test01.txt"
)
AUTHORITATIVE_FINAL_REL = AUTHORITATIVE_RUN_REL / "transcripts" / "Alpha_output_FINAL.txt"


class Phase1BuildIdentityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_report(
    path: Path, data: dict[str, Any], *, identity: dict[str, Any] | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    if identity is not None:
        payload.setdefault("build_id", identity["build_id"])
        payload.setdefault("patch_version", identity["patch_version"])
        payload.setdefault("generated_at", utc_now_iso())
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text_report(path: Path, body_lines: list[str], *, identity: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        f"build_id={identity['build_id']}",
        f"patch_version={identity['patch_version']}",
        f"generated_at={utc_now_iso()}",
    ]
    path.write_text("\n".join(header + body_lines) + "\n", encoding="utf-8")


def create_phase1_build_identity(*, project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    build_id = str(uuid.uuid4())
    build_timestamp = utc_now_iso()
    phase1_root = project_root / PHASE1_ROOT_REL
    build_dir = phase1_root / "builds" / build_id
    if build_dir.exists():
        raise Phase1BuildIdentityError(f"build_folder_already_exists:{build_dir}")
    build_dir.mkdir(parents=True, exist_ok=False)
    for name in BUILD_SUBDIRS:
        (build_dir / name).mkdir(parents=True, exist_ok=False)

    identity = {
        "build_id": build_id,
        "build_timestamp": build_timestamp,
        "patch_version": PATCH_VERSION,
        "patch_codename": PATCH_CODENAME,
        "project_root": str(project_root),
        "phase1_root": str(phase1_root),
        "build_dir": str(build_dir),
        "baseline_dir": str(build_dir / "baseline"),
        "inventory_dir": str(build_dir / "inventory"),
        "analysis_dir": str(build_dir / "analysis"),
        "archive_dir": str(build_dir / "archive"),
        "quarantine_dir": str(build_dir / "quarantine"),
        "reports_dir": str(build_dir / "reports"),
        "regression_dir": str(build_dir / "regression"),
        "package_dir": str(build_dir / "package"),
        "restore_dir": str(build_dir / "restore"),
        "created_unix": time.time(),
        "expected_final_sha256": EXPECTED_FINAL_SHA256,
    }
    write_json_report(build_dir / "BUILD_IDENTITY.json", identity, identity=identity)
    return identity
