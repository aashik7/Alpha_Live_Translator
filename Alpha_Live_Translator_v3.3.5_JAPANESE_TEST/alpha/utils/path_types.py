"""Shared path normalization for public pipeline entry points."""

from __future__ import annotations

from pathlib import Path


def ensure_path(value: str | Path | None) -> Path | None:
    """Return ``value`` as a Path while preserving ``None``."""
    if value is None:
        return None
    return value if isinstance(value, Path) else Path(value)
