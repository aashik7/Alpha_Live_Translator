"""Unique packaging build identity for V25.3.3.2.3 single-authority packaging."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGING_VERSION = "3.3.5.5.8.5.25.3.3.2.3"
PACKAGING_CODENAME = (
    "Single Acceptance Authority, Fresh-Build Packaging & Non-Circular Verification"
)

AUDIT_ROOT_REL = Path("troubleshooting") / "post_acceptance_audit" / f"v{PACKAGING_VERSION}"


class PackageBuildIdentityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def create_build_identity(
    *,
    project_root: Path,
    source_bundle: Path,
    run_folder: Path,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    source_bundle = source_bundle if source_bundle.is_absolute() else (project_root / source_bundle)
    run_folder = run_folder if run_folder.is_absolute() else (project_root / run_folder)
    source_bundle = source_bundle.resolve()
    run_folder = run_folder.resolve()
    if not source_bundle.exists():
        raise PackageBuildIdentityError(f"source_bundle_missing:{source_bundle}")
    if not run_folder.exists():
        raise PackageBuildIdentityError(f"run_folder_missing:{run_folder}")

    build_id = str(uuid.uuid4())
    build_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    audit_root = project_root / AUDIT_ROOT_REL
    build_dir = audit_root / "builds" / build_id
    if build_dir.exists():
        raise PackageBuildIdentityError(f"build_folder_already_exists:{build_dir}")
    build_dir.mkdir(parents=True, exist_ok=False)
    (build_dir / "staging" / "evidence").mkdir(parents=True)
    (build_dir / "staging" / "acceptance").mkdir(parents=True)
    (build_dir / "staging" / "delivery").mkdir(parents=True)
    (build_dir / "staging" / "regression").mkdir(parents=True)

    run_id = ""
    manifest = run_folder / "RUN_MANIFEST.json"
    if manifest.exists():
        try:
            run_id = str(json.loads(manifest.read_text(encoding="utf-8")).get("run_id") or "")
        except Exception:
            run_id = ""
    if not run_id:
        run_id = f"live-{run_folder.name}"

    identity = {
        "build_id": build_id,
        "build_timestamp": build_timestamp,
        "packaging_version": PACKAGING_VERSION,
        "packaging_codename": PACKAGING_CODENAME,
        "source_bundle_path": str(source_bundle),
        "source_bundle_sha256": sha256_file(source_bundle),
        "source_run_id": run_id,
        "source_run_folder": str(run_folder),
        "build_dir": str(build_dir),
        "staging_dir": str(build_dir / "staging"),
        "created_unix": time.time(),
    }
    out = build_dir / "BUILD_IDENTITY.json"
    out.write_text(json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Also mirror into staging/delivery for outer bundle
    (build_dir / "staging" / "delivery" / "BUILD_IDENTITY.json").write_text(
        json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return identity
