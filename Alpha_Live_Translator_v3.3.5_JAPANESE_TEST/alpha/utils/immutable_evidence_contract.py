"""Canonical immutable-runtime hash evidence filenames (V25.3.3.2.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from alpha.utils.validation_version import VALIDATION_PATCH_VERSION

IMMUTABLE_HASHES_BEFORE_FILENAME = "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_BEFORE.json"
IMMUTABLE_HASHES_AFTER_FILENAME = "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_AFTER.json"

# Historical short names — read-only compatibility
_LEGACY_BEFORE = "IMMUTABLE_HASHES_BEFORE.json"
_LEGACY_AFTER = "IMMUTABLE_HASHES_AFTER.json"


def validation_dir(
    project_root: Path | str,
    *,
    version: str | None = None,
) -> Path:
    ver = version or VALIDATION_PATCH_VERSION
    return Path(project_root) / "troubleshooting" / "validation" / f"v{ver}"


def before_path(validation_directory: Path | str) -> Path:
    return Path(validation_directory) / IMMUTABLE_HASHES_BEFORE_FILENAME


def after_path(validation_directory: Path | str) -> Path:
    return Path(validation_directory) / IMMUTABLE_HASHES_AFTER_FILENAME


def resolve_before_path(
    project_root: Path | str,
    *,
    version: str | None = None,
) -> Optional[Path]:
    """Resolve BEFORE hashes file: canonical name first, then legacy."""
    root = Path(project_root)
    candidates: list[Path] = []
    ver = version or VALIDATION_PATCH_VERSION
    for v in (ver, VALIDATION_PATCH_VERSION):
        d = validation_dir(root, version=v)
        candidates.append(d / IMMUTABLE_HASHES_BEFORE_FILENAME)
        candidates.append(d / _LEGACY_BEFORE)
    # Also scan other validation folders for the canonical name
    val_root = root / "troubleshooting" / "validation"
    if val_root.is_dir():
        for d in sorted(val_root.iterdir(), key=lambda p: p.name, reverse=True):
            if d.is_dir():
                candidates.append(d / IMMUTABLE_HASHES_BEFORE_FILENAME)
                candidates.append(d / _LEGACY_BEFORE)
    seen: set[str] = set()
    for p in candidates:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def resolve_after_path(
    project_root: Path | str,
    *,
    version: str | None = None,
) -> Optional[Path]:
    root = Path(project_root)
    candidates: list[Path] = []
    ver = version or VALIDATION_PATCH_VERSION
    for v in (ver, VALIDATION_PATCH_VERSION):
        d = validation_dir(root, version=v)
        candidates.append(d / IMMUTABLE_HASHES_AFTER_FILENAME)
        candidates.append(d / _LEGACY_AFTER)
    val_root = root / "troubleshooting" / "validation"
    if val_root.is_dir():
        for d in sorted(val_root.iterdir(), key=lambda p: p.name, reverse=True):
            if d.is_dir():
                candidates.append(d / IMMUTABLE_HASHES_AFTER_FILENAME)
                candidates.append(d / _LEGACY_AFTER)
    seen: set[str] = set()
    for p in candidates:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def load_hashes_json(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def is_canonical_immutable_filename(name: str) -> bool:
    return name in (IMMUTABLE_HASHES_BEFORE_FILENAME, IMMUTABLE_HASHES_AFTER_FILENAME)


def is_legacy_immutable_filename(name: str) -> bool:
    return name in (_LEGACY_BEFORE, _LEGACY_AFTER)
