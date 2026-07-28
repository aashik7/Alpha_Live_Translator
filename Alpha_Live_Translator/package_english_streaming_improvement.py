#!/usr/bin/env python3
"""Package English streaming improvement experiment (no WAV/API secrets)."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POINTER = (
    ROOT
    / "troubleshooting"
    / "experiments"
    / "english_streaming_improvement"
    / "LATEST_EXPERIMENT.json"
)
OUT_ROOT = ROOT / "troubleshooting" / "experiments" / "english_streaming_improvement"

SKIP_SUFFIXES = {".wav", ".pcm", ".raw", ".env"}
SKIP_NAMES = {".env", "credentials.json"}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    if not POINTER.is_file():
        print("LATEST_EXPERIMENT.json missing")
        return 1
    exp_dir = Path(json.loads(POINTER.read_text(encoding="utf-8"))["experiment_dir"])
    if not exp_dir.is_dir():
        print(f"experiment_dir missing: {exp_dir}")
        return 1
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = _utc()
    zip_path = OUT_ROOT / f"ENGLISH_STREAMING_IMPROVEMENT_{stamp}.zip"
    count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in exp_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES:
                continue
            if ".env" in path.parts:
                continue
            arc = path.relative_to(exp_dir).as_posix()
            zf.write(path, arcname=f"{exp_dir.name}/{arc}")
            count += 1
        # include freeze validation copies
        freeze = ROOT / "troubleshooting" / "validation" / "english_only_improvement"
        for name in (
            "JAPANESE_FREEZE_BASELINE.json",
            "JAPANESE_FREEZE_VERIFICATION.json",
            "ENGLISH_DEEPGRAM_REQUEST_VALIDATION.json",
            "ENGLISH_LEXICAL_PROVENANCE_VALIDATION.json",
        ):
            p = freeze / name
            if p.is_file():
                zf.write(p, arcname=f"validation/{name}")
                count += 1
    index = {
        "zip_path": str(zip_path),
        "experiment_dir": str(exp_dir),
        "generated_at_utc": stamp,
        "file_count": count,
        "include_wav": False,
    }
    (OUT_ROOT / "ENGLISH_STREAMING_IMPROVEMENT_PACKAGE_INDEX.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(index, indent=2))
    print(f"ZIP={zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
