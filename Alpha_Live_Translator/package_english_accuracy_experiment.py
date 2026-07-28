#!/usr/bin/env python3
"""Package English accuracy 90 experiment ZIP (no secrets, no wav blobs by default)."""

from __future__ import annotations

import argparse
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPERIMENTS = ROOT / "troubleshooting" / "experiments"
POINTER = EXPERIMENTS / "english_accuracy_90" / "LATEST_EXPERIMENT.json"
OUT_DIR = EXPERIMENTS / "english_accuracy_90"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_exp_dir() -> Path:
    if POINTER.is_file():
        data = json.loads(POINTER.read_text(encoding="utf-8"))
        p = Path(str(data.get("experiment_dir") or ""))
        if p.is_dir():
            return p
    cands = sorted(
        [p for p in EXPERIMENTS.glob("english_accuracy_90*") if p.is_dir() and p.name != "english_accuracy_90"],
        key=lambda p: p.stat().st_mtime,
    )
    if not cands:
        raise SystemExit("No experiment directory found")
    return cands[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-wav", action="store_true", help="Include concatenated WAV files (large)")
    args = ap.parse_args()

    exp = resolve_exp_dir()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _utc()
    zip_path = OUT_DIR / f"ENGLISH_ACCURACY_90_EXPERIMENT_{stamp}.zip"

    skip_suffixes = {".env"}
    skip_names = {".env"}
    if not args.include_wav:
        skip_suffixes |= {".wav", ".pcm", ".raw"}

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in exp.rglob("*"):
            if not p.is_file():
                continue
            if p.name in skip_names or p.suffix.lower() in skip_suffixes:
                continue
            if "api_key" in p.name.lower() or "secret" in p.name.lower():
                continue
            arc = p.relative_to(exp).as_posix()
            zf.write(p, arcname=arc)
        # include validation if present
        val = OUT_DIR / "VALIDATION" / "ENGLISH_ACCURACY_EXPERIMENT_VALIDATION.json"
        if val.is_file():
            zf.write(val, arcname=f"validation/{val.name}")

    index = {
        "experiment_dir": str(exp),
        "zip_path": str(zip_path),
        "generated_at_utc": stamp,
        "include_wav": bool(args.include_wav),
    }
    (OUT_DIR / "ENGLISH_ACCURACY_90_PACKAGE_INDEX.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(index, indent=2))
    print(f"ZIP={zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
