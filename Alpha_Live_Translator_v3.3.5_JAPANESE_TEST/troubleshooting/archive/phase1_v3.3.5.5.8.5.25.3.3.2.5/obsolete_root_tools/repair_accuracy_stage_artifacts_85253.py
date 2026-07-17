"""Offline repair for missing three-stage accuracy artifacts (8.5.25.3)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alpha.utils.accuracy_stage_capture import (
    _rebuild_assembler_lines_from_events,
    _rebuild_raw_lines_from_events,
    _resolve_final_alpha_source_path,
    _sha256_file,
    get_accuracy_stage_compare_path,
    repair_accuracy_stage_artifacts,
)

OUT_NAME = "repair_accuracy_stage_artifacts_report.txt"


def _latest_completed_live_run(runs_root: Path) -> Path | None:
    if not runs_root.exists():
        return None
    candidates: list[Path] = []
    for folder in runs_root.iterdir():
        if not folder.is_dir() or folder.name == "_pending":
            continue
        manifest_path = folder / "RUN_MANIFEST.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if manifest.get("run_type") != "live":
            continue
        status = str(manifest.get("final_status", ""))
        if not status.startswith("completed"):
            continue
        candidates.append(folder)
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _hash_stage_text_files(run_folder: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in (
        "accuracy_stage_compare/raw_deepgram_events.jsonl",
        "accuracy_stage_compare/stable_assembler_events.jsonl",
    ):
        path = run_folder / rel
        hashes[rel] = _sha256_file(path) if path.exists() else ""
    return hashes


def _write_report(run_folder: Path, lines: list[str]) -> Path:
    out = run_folder / "accuracy_stage_compare" / OUT_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def repair_run(run_folder: Path) -> tuple[str, Path]:
    manifest_path = run_folder / "RUN_MANIFEST.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if manifest.get("run_type") not in (None, "live"):
        lines = [
            "REPAIR_ACCURACY_STAGE_ARTIFACTS_85253",
            f"run_folder={run_folder}",
            "status=FAILED",
            "reason=not_a_live_run",
        ]
        return "FAILED", _write_report(run_folder, lines)

    before_hashes = _hash_stage_text_files(run_folder)
    source_before, reason_before = _resolve_final_alpha_source_path(run_folder)

    result = repair_accuracy_stage_artifacts(
        run_folder,
        run_type=str(manifest.get("run_type", "live")),
        run_status=str(manifest.get("final_status", "completed")),
        selected_language=str(manifest.get("selected_language", "ja")),
        offline_repair=True,
    )
    after_hashes = _hash_stage_text_files(run_folder)

    created: list[str] = []
    for name in ("final_alpha_output", "audio_delivery_summary", "stage_manifest", "raw_deepgram", "stable_assembler_only"):
        path = get_accuracy_stage_compare_path(name, run_folder)
        if path.exists() and path.stat().st_size > 0:
            created.append(path.name)

    still_missing = [
        name
        for name in ("final_alpha_output.txt", "audio_delivery_summary.json", "stage_manifest.json")
        if not (run_folder / "accuracy_stage_compare" / name).exists()
    ]

    audio_path = get_accuracy_stage_compare_path("audio_delivery_summary", run_folder)
    audio_payload: dict = {}
    if audio_path.exists():
        try:
            audio_payload = json.loads(audio_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    manifest_payload = result.get("manifest") or {}
    status = "REPAIRED"
    if still_missing or result.get("errors"):
        status = "FAILED" if still_missing else "REPAIRED_WITH_WARNINGS"
    elif manifest_payload.get("warnings"):
        status = "REPAIRED_WITH_WARNINGS"

    lines = [
        "REPAIR_ACCURACY_STAGE_ARTIFACTS_85253",
        f"run_folder={run_folder}",
        f"status={status}",
        f"files_found_raw_events={ (run_folder / 'accuracy_stage_compare/raw_deepgram_events.jsonl').exists() }",
        f"files_found_stable_events={ (run_folder / 'accuracy_stage_compare/stable_assembler_events.jsonl').exists() }",
        f"files_created_or_present={json.dumps(created, ensure_ascii=False)}",
        f"files_still_missing={json.dumps(still_missing, ensure_ascii=False)}",
        f"final_alpha_source={result.get('final_alpha_source', str(source_before or ''))}",
        f"final_alpha_source_reason={result.get('final_alpha_source_reason', reason_before)}",
        f"final_source_hash_matches={result.get('final_source_hash_matches', False)}",
        f"audio_metrics_recovered={json.dumps({k: audio_payload.get(k) for k in audio_payload if audio_payload.get(k) is not None and k != 'missing_metrics'}, ensure_ascii=False)}",
        f"audio_metrics_unavailable={json.dumps(audio_payload.get('missing_metrics', []), ensure_ascii=False)}",
        f"stage_capture_complete={manifest_payload.get('stage_capture_complete', False)}",
        f"raw_stable_hashes_before={json.dumps(before_hashes, ensure_ascii=False)}",
        f"raw_stable_hashes_after={json.dumps(after_hashes, ensure_ascii=False)}",
        f"errors={json.dumps(result.get('errors', []), ensure_ascii=False)}",
        f"warnings={json.dumps(manifest_payload.get('warnings', []), ensure_ascii=False)}",
    ]
    report_path = _write_report(run_folder, lines)
    print("\n".join(lines))
    return status, report_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-live-run", action="store_true")
    parser.add_argument("--run-folder", type=str, default="")
    args = parser.parse_args()

    if args.run_folder:
        run_folder = Path(args.run_folder)
    elif args.latest_live_run:
        run_folder = _latest_completed_live_run(Path("troubleshooting/runs"))
        if run_folder is None:
            print("No completed live run found.")
            return 1
    else:
        parser.error("Specify --latest-live-run or --run-folder")

    if not run_folder.exists():
        print(f"Run folder not found: {run_folder}")
        return 1

    status, _ = repair_run(run_folder)
    return 0 if status in ("REPAIRED", "REPAIRED_WITH_WARNINGS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
