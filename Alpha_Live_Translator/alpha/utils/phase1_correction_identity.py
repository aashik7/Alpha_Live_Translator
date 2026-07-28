"""Identity and reporting helpers for Phase 1 correction 85253326."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATCH_VERSION = "3.3.5.5.8.5.25.3.3.2.6"
AUTHORITATIVE_RUN_ID = "v3.3.5.5.8.5.25.3.3.1-20260714-111519"
AUTHORITATIVE_RUN_REL = Path("troubleshooting/runs") / AUTHORITATIVE_RUN_ID
AUTHORITATIVE_REFERENCE_REL = Path("troubleshooting/accuracy_benchmark/reference_transcripts/test01.txt")
AUTHORITATIVE_FINAL_REL = AUTHORITATIVE_RUN_REL / "transcripts/Alpha_output_FINAL.txt"
EXPECTED_FINAL_SHA256 = "6e70dd171862527da2f2de0305ab82154cf9c1591b73860e4fd75e06f570c178"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_build_identity(project_root: Path) -> dict[str, str]:
    build_id = str(uuid.uuid4())
    phase_root = project_root / "troubleshooting/phase1_correction" / f"v{PATCH_VERSION}"
    build_root = phase_root / "builds" / build_id
    names = ("before", "after", "analysis", "archive", "reports", "regression", "package", "restore", "temporary_verification")
    for name in names:
        (build_root / name).mkdir(parents=True, exist_ok=True)
    result = {"build_id": build_id, "patch_version": PATCH_VERSION, "generated_at": utc_now_iso(),
              "project_root": str(project_root.resolve()), "phase_root": str(phase_root), "build_root": str(build_root)}
    result.update({f"{name}_dir": str(build_root / name) for name in names})
    return result


def write_json_report(path: Path, payload: dict[str, Any], *, identity: dict[str, str] | None = None) -> None:
    data = dict(payload)
    if identity:
        data.setdefault("build_id", identity["build_id"])
        data.setdefault("patch_version", identity["patch_version"])
    data.setdefault("generated_at", utc_now_iso())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
