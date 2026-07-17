"""Validation for V3.3.5.5.8.5.20.2 final evidence cleanup."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from alpha.constants import APP_VERSION
from alpha.utils.troubleshooting_paths import get_troubleshooting_root


def _latest_run() -> Path | None:
    runs = get_troubleshooting_root() / "runs"
    if not runs.exists():
        return None
    folders = [p for p in runs.iterdir() if p.is_dir() and p.name != "_pending"]
    return max(folders, key=lambda p: p.stat().st_mtime) if folders else None


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    run = _latest_run()
    if APP_VERSION != "3.3.5.5.8.5.20.2":
        failures.append("app_version")
    if run is None:
        failures.append("run_folder_missing")
    else:
        manifest = run / "RUN_MANIFEST.json"
        live = run / "artifacts" / "LIVE_RUN_STATUS.json"
        idx = run / "artifacts" / "RUN_ARTIFACTS_INDEX.txt"
        writer_registry = run / "artifacts" / "WRITER_REGISTRY_FINAL.json"
        validation_out = run / "validation" / "validate_8520_2_output.txt"
        upload_zip = next(iter((run / "upload_package").glob("UPLOAD_PACKAGE_v*.zip")), None)
        for path in (manifest, live, idx, writer_registry):
            if not path.exists():
                failures.append(f"missing:{path.name}")
        if live.exists():
            payload = json.loads(live.read_text(encoding="utf-8"))
            if payload.get("status") not in ("completed", "completed_with_warnings"):
                failures.append("live_status_not_completed")
            if payload.get("timed_out_steps"):
                warnings.append("timed_out_steps_non_empty")
            if payload.get("failed_steps"):
                warnings.append("failed_steps_non_empty")
        if writer_registry.exists():
            reg = json.loads(writer_registry.read_text(encoding="utf-8"))
            pending = reg.get("pending_writers") or []
            if pending:
                failures.append("pending_writers_after_rebind")
        if upload_zip is None:
            failures.append("upload_zip_missing")
        else:
            with zipfile.ZipFile(upload_zip, "r") as zf:
                names = set(zf.namelist())
                if "validation/validate_8520_2_output.txt" not in names and "validate_8520_2_output.txt" not in names:
                    warnings.append("validation_output_not_in_zip")
                if any(name.lower().endswith(".wav") for name in names):
                    failures.append("wav_found_in_zip")
        if not (run / "health" / "PROCESS_HEALTH_TIMELINE.jsonl").exists():
            failures.append("process_health_timeline_missing")
        if not (run / "health" / "MEMORY_TREND_SUMMARY.json").exists():
            failures.append("memory_summary_missing")

        result = (
            "FAILED"
            if failures
            else "PASSED_WITH_WARNINGS"
            if warnings
            else "PASSED"
        )
        log(f"V3.3.5.5.8.5.20.2 FINAL EVIDENCE CLEANUP VALIDATION: {result}")
        if failures:
            log("Failures: " + ", ".join(failures))
        if warnings:
            log("Warnings: " + ", ".join(warnings))
        validation_out.parent.mkdir(parents=True, exist_ok=True)
        validation_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1 if failures else 0

    result = "FAILED"
    log(f"V3.3.5.5.8.5.20.2 FINAL EVIDENCE CLEANUP VALIDATION: {result}")
    if failures:
        log("Failures: " + ", ".join(failures))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
