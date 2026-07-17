"""Unique cleanup build identity for V25.3.3.2.4 final cleanup packaging."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATCH_VERSION = "3.3.5.5.8.5.25.3.3.2.4"
PATCH_CODENAME = "Final Package Closure, Safe Project Cleanup & Zero-Regret Retention"

CLEANUP_ROOT_REL = Path("troubleshooting") / "project_cleanup" / f"v{PATCH_VERSION}"

BUILD_SUBDIRS = (
    "inventory",
    "analysis",
    "quarantine",
    "reports",
    "regression",
    "package",
    "restore",
)


class CleanupBuildIdentityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_report(path: Path, data: dict[str, Any], *, identity: dict[str, Any] | None = None) -> None:
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


def create_cleanup_build_identity(*, project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    build_id = str(uuid.uuid4())
    build_timestamp = utc_now_iso()
    cleanup_root = project_root / CLEANUP_ROOT_REL
    build_dir = cleanup_root / "builds" / build_id
    if build_dir.exists():
        raise CleanupBuildIdentityError(f"build_folder_already_exists:{build_dir}")
    build_dir.mkdir(parents=True, exist_ok=False)
    for name in BUILD_SUBDIRS:
        (build_dir / name).mkdir(parents=True, exist_ok=False)

    identity = {
        "build_id": build_id,
        "build_timestamp": build_timestamp,
        "patch_version": PATCH_VERSION,
        "patch_codename": PATCH_CODENAME,
        "project_root": str(project_root),
        "cleanup_root": str(cleanup_root),
        "build_dir": str(build_dir),
        "inventory_dir": str(build_dir / "inventory"),
        "analysis_dir": str(build_dir / "analysis"),
        "quarantine_dir": str(build_dir / "quarantine"),
        "reports_dir": str(build_dir / "reports"),
        "regression_dir": str(build_dir / "regression"),
        "package_dir": str(build_dir / "package"),
        "restore_dir": str(build_dir / "restore"),
        "created_unix": time.time(),
    }
    write_json_report(build_dir / "BUILD_IDENTITY.json", identity, identity=identity)
    return identity
