"""Offline repair helpers shared by V25.3.2.1 repair scripts."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

TRANSCRIPT_PATHS = (
    "transcripts/Alpha_output_FINAL.txt",
    "transcripts/Alpha output.txt",
    "accuracy/Alpha_for_accuracy_check.txt",
)


def sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transcript_hashes(run_folder: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in TRANSCRIPT_PATHS:
        p = run_folder / rel
        if p.exists():
            out[rel.replace("/", "_")] = sha256_file(p)
    return out


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + ".before_repair")
    shutil.copy2(path, backup)
    return backup


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
