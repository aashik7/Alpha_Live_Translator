#!/usr/bin/env python3
"""Package the latest completed Japanese + English clean bilingual benchmark runs."""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "troubleshooting" / "accuracy_benchmark" / "clean_bilingual_test"
RUNS = ROOT / "troubleshooting" / "runs"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _request_language(run_folder: Path) -> str:
    req = run_folder / "accuracy_stage_compare" / "deepgram_request_actual.json"
    if not req.is_file():
        return ""
    try:
        data = _load_json(req)
        return str(data.get("language") or data.get("selected_language") or "").lower()
    except Exception:
        return ""


def _is_completed_run(run_folder: Path) -> bool:
    asc = run_folder / "accuracy_stage_compare"
    if not asc.is_dir():
        return False
    raw = asc / "raw_deepgram.txt"
    raw_alt = asc / "raw_provider.txt"
    stable = asc / "stable_transcript.txt"
    final = asc / "final_alpha_output.txt"
    manifest = asc / "stage_manifest.json"
    audio = asc / "audio_delivery_summary.json"
    raw_ok = (raw.is_file() and raw.stat().st_size > 0) or (raw_alt.is_file() and raw_alt.stat().st_size > 0)
    stable_ok = stable.is_file() and stable.stat().st_size > 0
    final_ok = final.is_file() and final.stat().st_size > 0
    if not (raw_ok and stable_ok and final_ok and manifest.is_file() and audio.is_file()):
        return False
    # Reject empty/smoke: require meaningful final text
    if len(final.read_text(encoding="utf-8", errors="replace").strip()) < 40:
        return False
    return True


def find_latest_completed(lang_prefix: str) -> Path | None:
    if not RUNS.is_dir():
        return None
    candidates: list[Path] = []
    for p in RUNS.iterdir():
        if not p.is_dir() or p.name.startswith("_"):
            continue
        lang = _request_language(p)
        if not lang.startswith(lang_prefix):
            continue
        if _is_completed_run(p):
            candidates.append(p)
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _copy_run(run_folder: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "RUN_MANIFEST.json",
        "accuracy_stage_compare",
        "accuracy",
        "transcripts",
        "health",
        "artifacts",
        "logs",
    ):
        src = run_folder / name
        if src.exists():
            target = dest / name
            if src.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--japanese-reference", required=True)
    ap.add_argument("--english-reference", required=True)
    args = ap.parse_args()

    ja_ref = Path(args.japanese_reference)
    en_ref = Path(args.english_reference)
    if not ja_ref.is_file() or ja_ref.stat().st_size <= 0:
        print(f"ERROR: Japanese reference missing/empty: {ja_ref}")
        return 2
    if not en_ref.is_file() or en_ref.stat().st_size <= 0:
        print(f"ERROR: English reference missing/empty: {en_ref}")
        return 2

    ja_run = find_latest_completed("ja")
    en_run = find_latest_completed("en")
    if ja_run is None or en_run is None:
        print("ERROR: completed Japanese and/or English runs not found")
        print(f"japanese_run={ja_run}")
        print(f"english_run={en_run}")
        return 2
    if ja_run.resolve() == en_run.resolve():
        print("ERROR: Japanese and English resolved to the same run folder")
        return 2

    stamp = _utc()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    staging = OUT_DIR / f"staging_{stamp}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    _copy_run(ja_run, staging / "japanese_run")
    _copy_run(en_run, staging / "english_run")
    refs = staging / "References"
    refs.mkdir(parents=True)
    shutil.copy2(ja_ref, refs / "current_japanese_actual.txt")
    shutil.copy2(en_ref, refs / "current_english_actual.txt")

    # Validation artifacts
    val = staging / "validation"
    val.mkdir(parents=True)
    for src in (
        ROOT / "troubleshooting" / "validation" / "language_routing" / "LANGUAGE_ROUTING_VALIDATION.json",
        ROOT / "troubleshooting" / "validation" / "clean_bilingual_reset" / "LONG_SESSION_UI_PERFORMANCE_VALIDATION.json",
        ROOT / "troubleshooting" / "validation" / "clean_bilingual_reset" / "CLEAN_BILINGUAL_ENVIRONMENT_VALIDATION.json",
    ):
        if src.is_file():
            shutil.copy2(src, val / src.name)
    hist = ROOT / "troubleshooting" / "cleanup_history"
    if hist.is_dir():
        for pattern in (
            "RESET_DELETION_MANIFEST_*.json",
            "POST_RESET_SOURCE_HASH_VERIFICATION_*.json",
        ):
            files = sorted(hist.glob(pattern))
            if files:
                shutil.copy2(files[-1], val / files[-1].name)

    zip_path = OUT_DIR / f"CLEAN_BILINGUAL_BENCHMARK_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in staging.rglob("*"):
            if p.is_file():
                # never include wav/env
                if p.suffix.lower() in {".wav", ".env"} or p.name == ".env":
                    continue
                zf.write(p, arcname=p.relative_to(staging).as_posix())

    index = {
        "japanese_run": str(ja_run),
        "english_run": str(en_run),
        "japanese_language": _request_language(ja_run),
        "english_language": _request_language(en_run),
        "zip_path": str(zip_path),
        "generated_at_utc": stamp,
    }
    (OUT_DIR / "CLEAN_BILINGUAL_BENCHMARK_INDEX.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(index, indent=2))
    print(f"ZIP={zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
