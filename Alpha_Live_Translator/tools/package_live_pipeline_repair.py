# -*- coding: utf-8 -*-
"""Package live-pipeline repair evidence into a fresh ZIP.

Usage (from Alpha_Live_Translator root):

    python .\\tools\\package_live_pipeline_repair.py

Rejects outdated evidence (older than latest production code mtime) and refuses
to claim CREATED if packaging fails. Does not include secrets or _archive.
"""

from __future__ import annotations

import zipfile
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRODUCTION_FILES = [
    ROOT / "alpha/ui/main_window.py",
    ROOT / "alpha/utils/live_pipeline_profile.py",
    ROOT / "alpha/translation/translation_worker.py",
    ROOT / "alpha/transcription/duplicate_protection.py",
    ROOT / "tools/validate_live_pipeline_repair.py",
    ROOT / "tools/finalise_live_pipeline_repair.py",
    ROOT / "tools/package_live_pipeline_repair.py",
]

REQUIRED_EVIDENCE = [
    "START_PIPELINE_PROFILE.json",
    "STOP_PIPELINE_PROFILE.json",
    "TRANSCRIPT_REVISION_LIFECYCLE_VALIDATION.json",
    "STABLE_ONLY_TRANSLATION_VALIDATION.json",
    "TRANSLATION_LATENCY_BREAKDOWN.json",
    "LOADING_STATE_VALIDATION.json",
    "STALE_SESSION_CALLBACK_VALIDATION.json",
    "SPARSE_ORDERING_REGRESSION.json",
    "SOURCE_IMMUTABILITY_VALIDATION.json",
    "GENERIC_SPEAKER_VALIDATION.json",
    "JAPANESE_FREEZE_VERIFICATION.json",
    "ENGLISH_FREEZE_VERIFICATION.json",
    "JA_TO_EN_LIVE_RESULT.json",
    "EN_TO_JA_LIVE_RESULT.json",
    "UI_EVENT_LOOP_RESPONSIVENESS.json",
    "LIVE_PIPELINE_REPAIR_DECISION.json",
    "LIVE_PIPELINE_REPAIR_REPORT.txt",
    "implementation_manifest.json",
    "translation_events.jsonl",
    "ui_lifecycle_events.jsonl",
    "Cursor final report.txt",
]


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _newest_evidence_dir() -> Path | None:
    base = ROOT / "troubleshooting"
    if not base.is_dir():
        return None
    dirs = sorted(
        [
            p
            for p in base.iterdir()
            if p.is_dir()
            and p.name.startswith("live_pipeline_repair")
            and p.name != "live_pipeline_repair"
        ],
        key=lambda p: p.name,
        reverse=True,
    )
    return dirs[0] if dirs else None


def _latest_code_mtime() -> float:
    times = [p.stat().st_mtime for p in PRODUCTION_FILES if p.is_file()]
    if not times:
        raise RuntimeError("No production files found to compare mtimes")
    return max(times)


def _evidence_newest_mtime(evidence: Path) -> float:
    files = [p for p in evidence.rglob("*") if p.is_file()]
    if not files:
        return 0.0
    return max(p.stat().st_mtime for p in files)


def main() -> int:
    evidence = _newest_evidence_dir()
    if evidence is None:
        print("ERROR: no evidence folder found. Run validate_live_pipeline_repair.py first.")
        return 2

    missing = [name for name in REQUIRED_EVIDENCE if not (evidence / name).is_file()]
    if missing:
        print("ERROR: evidence folder missing required files:")
        for name in missing:
            print(f"  - {name}")
        return 2

    code_mtime = _latest_code_mtime()
    evidence_mtime = _evidence_newest_mtime(evidence)
    if evidence_mtime + 1.0 < code_mtime:
        print("ERROR: evidence is older than latest production-code modification.")
        print(f"  evidence_mtime={evidence_mtime}")
        print(f"  code_mtime={code_mtime}")
        print("Re-run: python .\\tools\\validate_live_pipeline_repair.py")
        return 2

    # Optional: warn if live still NOT_RUN (allowed for pre-live package of deterministic evidence,
    # but label clearly). Final acceptance ZIP should be created after finalise.
    try:
        import json

        decision = json.loads((evidence / "LIVE_PIPELINE_REPAIR_DECISION.json").read_text(encoding="utf-8"))
    except Exception:
        decision = {}

    stamp = _utc()
    out_dir = ROOT / "troubleshooting" / "live_pipeline_repair"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"ALPHA_LIVE_PIPELINE_REPAIR_{stamp}.zip"

    exclude_names = {".env", ".DS_Store"}
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in evidence.rglob("*"):
                if not f.is_file():
                    continue
                if f.name in exclude_names or f.name.startswith(".env."):
                    continue
                if f.suffix.lower() in {".zip", ".wav", ".mp3", ".pyc"}:
                    continue
                if "__pycache__" in f.parts or "graphify-out" in f.parts:
                    continue
                arc = f"{evidence.name}/{f.relative_to(evidence).as_posix()}"
                zf.write(f, arcname=arc)
        # Validate open
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            names = zf.namelist()
        if bad is not None:
            print(f"ERROR: ZIP integrity failure at {bad}")
            return 2
        if not names:
            print("ERROR: ZIP is empty")
            return 2
    except Exception as exc:
        print(f"ERROR: packaging failed: {exc}")
        return 2

    print("CREATED")
    print(f"ZIP={zip_path}")
    print(f"EVIDENCE={evidence}")
    print(f"DECISION_STATUS={decision.get('STATUS')}")
    print(f"READY_FOR_LIVE_TEST={decision.get('READY_FOR_LIVE_TEST')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
