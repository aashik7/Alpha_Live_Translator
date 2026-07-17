"""Validation for V3.3.5.5.8.5.20 troubleshooting evidence unification."""
from __future__ import annotations

import json
import sys
import threading
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    AUDIO_TEMP_CAPTURE_ENABLED,
    AUDIO_TEMP_RETENTION_HOURS,
    CENTRALIZED_TROUBLESHOOTING_DIR,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FORBID_BACKGROUND_TK_CALLS,
    FULL_DIAGNOSTIC_LOGGING_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    KEYTERM_PROFILE_BUSINESS_JAPANESE,
    TK_SAFE_PIPELINE_MODE,
    TROUBLESHOOTING_MODE,
    resolve_japanese_keyterms,
)
from alpha.transcription.japanese_accuracy_cleaner import (
    normalize_business_cleanup_once,
    run_business_cleanup_selftest_once,
)
from alpha.transcription.japanese_sentence_assembler import JapaneseContinuityAssembler
from alpha.utils.language_pipeline_worker import start_language_pipeline_worker
from alpha.utils.process_health_telemetry import collect_process_metrics, write_memory_trend_summary
from alpha.utils.stop_finalize_worker import build_stop_finalize_summary
from alpha.utils.troubleshooting_paths import (
    get_troubleshooting_root,
    get_validation_path,
    validate_no_runtime_files_outside_troubleshooting,
)
from alpha.utils.ui_event_bus import get_ui_event_bus, start_ui_event_bus
from alpha.utils.ui_thread_guard import register_ui_main_thread

_DUPLICATE_BAD = ("いついつも", "このこのたび")


def _find_latest_run_folder() -> Path | None:
    runs = get_troubleshooting_root() / "runs"
    if not runs.exists():
        return None
    folders = [
        p for p in runs.iterdir() if p.is_dir() and f"v{APP_VERSION}" in p.name
    ]
    if not folders:
        pending = runs / "_pending"
        return pending if pending.exists() else None
    return max(folders, key=lambda p: p.stat().st_mtime)


def run_deadlock_regression_test() -> bool:
    register_ui_main_thread()
    start_ui_event_bus()
    start_language_pipeline_worker()
    host = MagicMock()
    host.after = MagicMock(side_effect=AssertionError("host.after must not be called"))
    host.is_listening = True
    host._audio_q = MagicMock()
    host._audio_q.qsize.return_value = 0
    host.transcript_queue = MagicMock()
    host.transcript_queue.qsize.return_value = 0
    host._jp_final_stabilizer = None

    asm = JapaneseContinuityAssembler(host)
    asm._buffer = {
        "text": "テスト文です。",
        "created_mono": time.monotonic(),
        "updated_mono": time.monotonic(),
        "hold_started_mono": time.monotonic(),
        "part_count": 1,
        "speaker": 2,
        "raw_fragments": ["テスト文です。"],
        "metadata": {},
    }
    blocked = threading.Event()

    def hold_lock():
        with asm._lock:
            blocked.set()
            time.sleep(0.3)

    t = threading.Thread(target=hold_lock, name="DeadlockTestHolder")
    t.start()
    assert blocked.wait(timeout=1.0)
    snap = asm.get_buffer_snapshot_nonblocking()
    assert isinstance(snap, dict)
    with asm._lock:
        asm._schedule_flush(50, "deadlock_test")
    flush_due = asm._pending_flush_due_mono
    flush_gen = asm._pending_flush_generation
    assert flush_due is not None
    from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

    get_language_pipeline_worker().schedule_flush(asm, flush_due, flush_gen, "deadlock_test")
    t.join(timeout=2.0)
    host.after.assert_not_called()
    print("DEADLOCK_REGRESSION_TEST_PASSED")
    return True


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    output = StringIO()

    def log(line: str = "") -> None:
        print(line)
        output.write(line + "\n")

    if APP_VERSION != "3.3.5.5.8.5.20":
        failures.append("version")
    expected_codename = (
        "Troubleshooting Evidence Unification, Clean Stop Evidence & Temporary Audio Buffer"
    )
    if APP_CODENAME != expected_codename:
        failures.append("codename")
    if not TROUBLESHOOTING_MODE:
        failures.append("troubleshooting_mode")
    if not CENTRALIZED_TROUBLESHOOTING_DIR:
        failures.append("centralized_troubleshooting_dir")
    if not FULL_DIAGNOSTIC_LOGGING_ENABLED:
        failures.append("full_diagnostic_logging")
    if not TK_SAFE_PIPELINE_MODE or not FORBID_BACKGROUND_TK_CALLS:
        failures.append("tk_safe_pipeline")
    if JAPANESE_STT_PROFILE != "no_diarize":
        failures.append("stt_profile")
    if JAPANESE_KEYTERM_PROFILE != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("keyterm_profile")
    if DEEPGRAM_MODEL != "nova-3" or DEEPGRAM_LANGUAGE != "ja":
        failures.append("deepgram_config")
    if DEEPGRAM_ENDPOINTING_MS != 500 or DEEPGRAM_UTTERANCE_END_MS != 1500:
        failures.append("deepgram_timing")

    ts_root = get_troubleshooting_root()
    if not ts_root.exists():
        failures.append("troubleshooting_root_missing")

    run_folder = _find_latest_run_folder()
    if run_folder is None:
        warnings.append("no_run_folder_yet")
    else:
        manifest = run_folder / "RUN_MANIFEST.json"
        if not manifest.exists():
            warnings.append("run_manifest_missing")
        else:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("app_version") != APP_VERSION:
                    failures.append("manifest_version")
                if data.get("selected_language") != "ja":
                    failures.append("manifest_language")
            except Exception:
                failures.append("manifest_parse")

    required_rel = [
        "logs/japanese_accuracy.log",
        "logs/freeze_guard.log",
        "transcripts/raw_deepgram_finals.jsonl",
        "transcripts/stable_commits.jsonl",
        "transcripts/ui_exported_segments.jsonl",
        "accuracy/assembler_decisions.jsonl",
        "accuracy/quarantine_decisions.jsonl",
        "accuracy/correction_decisions.jsonl",
        "health/HEALTH_TIMELINE.jsonl",
        "health/PROCESS_HEALTH_TIMELINE.jsonl",
    ]
    if run_folder:
        for rel in required_rel:
            if not (run_folder / rel).exists():
                warnings.append(f"missing:{rel}")

    violations = validate_no_runtime_files_outside_troubleshooting()
    if violations:
        warnings.append(f"legacy_runtime_files:{len(violations)}")

    terms, profile, _ = resolve_japanese_keyterms()
    if profile != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("resolved_profile")
    if not run_business_cleanup_selftest_once():
        failures.append("business_cleanup_selftest")

    for text in ("いつもお世話になっております", "このたび御社", "翌日"):
        r = normalize_business_cleanup_once(text, verify_second_pass=True)
        for bad in _DUPLICATE_BAD:
            if bad in r["candidate"]:
                failures.append(f"cleanup_{bad}")

    try:
        if not run_deadlock_regression_test():
            failures.append("deadlock_regression")
    except Exception as exc:
        failures.append(f"deadlock_regression:{exc}")

    if not (ROOT / "alpha" / "utils" / "troubleshooting_paths.py").exists():
        failures.append("troubleshooting_paths_missing")
    if not (ROOT / "alpha" / "utils" / "audio_temp_capture.py").exists():
        failures.append("audio_temp_capture_missing")

    metrics = collect_process_metrics()
    write_memory_trend_summary()
    if metrics.get("process_memory_rss_mb", -1) < 0:
        warnings.append("process_metrics_unavailable")
    elif metrics.get("telemetry_backend") not in ("psutil", "windows_ctypes"):
        warnings.append(f"telemetry_backend:{metrics.get('telemetry_backend')}")

    host = MagicMock()
    host._dg_close_status = "normal"
    summary = build_stop_finalize_summary(host, dg_result={"timed_out": True})
    if summary.get("stop_finalize_timed_out") and not summary.get("timed_out_steps"):
        failures.append("stop_finalize_timed_out_without_steps")

    stop_src = (ROOT / "alpha" / "utils" / "stop_finalize_worker.py").read_text(encoding="utf-8")
    if "timed_out_steps=timed_out_steps" in stop_src and "**stop_summary" in stop_src:
        failures.append("duplicate_kwargs_stop_finalize")
    if "STOP_FINALIZE_SUMMARY_NORMALIZED" not in stop_src:
        failures.append("stop_finalize_summary_normalized_missing")

    mw_src = (ROOT / "alpha" / "ui" / "main_window.py").read_text(encoding="utf-8")
    for token in (
        "stop_ui_flush_requested",
        "stop_ui_recover_requested",
        "deepgram_close_ui_cleanup_requested",
        "_stop_health_monitor_ui_safe",
    ):
        if token not in mw_src:
            warnings.append(f"missing_ui_reroute:{token}")

    if AUDIO_TEMP_CAPTURE_ENABLED and AUDIO_TEMP_RETENTION_HOURS < 2:
        warnings.append("audio_retention_hours_low")

    if run_folder:
        upload_dir = run_folder / "upload_package"
        if upload_dir.exists():
            zips = list(upload_dir.glob(f"UPLOAD_PACKAGE_v{APP_VERSION}_*.zip"))
            index = upload_dir / "UPLOAD_PACKAGE_INDEX.txt"
            if not index.exists():
                warnings.append("upload_package_index_missing")
            if not zips:
                warnings.append("upload_package_zip_missing")
            else:
                import zipfile

                with zipfile.ZipFile(zips[0], "r") as zf:
                    names = zf.namelist()
                    if any(n.endswith(".wav") for n in names):
                        failures.append("audio_wav_in_upload_zip")
        if AUDIO_TEMP_CAPTURE_ENABLED:
            if not (run_folder / "audio_temp" / "audio_manifest.json").exists():
                warnings.append("audio_manifest_missing")
            if not (run_folder / "audio_temp" / "audio_temp_summary.txt").exists():
                warnings.append("audio_temp_summary_missing")

    latest_ptr = ts_root / "latest" / "LATEST_RUN_POINTER.json"
    if not latest_ptr.exists():
        warnings.append("latest_pointer_missing")

    bus = get_ui_event_bus()
    if bus is None:
        failures.append("ui_event_bus")
    else:
        stats = bus.stats()
        posted = int(stats.get("posted", 0))
        drained = int(stats.get("drained", 0))
        dropped = int(stats.get("dropped", 0))
        if posted != drained and posted > 0:
            warnings.append(f"ui_event_posted_drained_mismatch:{posted}!={drained}")
        if dropped > 0:
            warnings.append(f"ui_event_dropped:{dropped}")

    status = "FAILED" if failures else ("PASSED_WITH_WARNINGS" if warnings else "PASSED")
    log(
        f"V3.3.5.5.8.5.20 TROUBLESHOOTING EVIDENCE VALIDATION: {status}"
    )
    if failures:
        log("Failures: " + ", ".join(failures))
    if warnings:
        log("Warnings: " + ", ".join(warnings))

    out_text = output.getvalue()
    out_paths: list[Path] = [ROOT / "validate_8520_output.txt"]
    try:
        out_paths.append(get_validation_path("validate_8520_output"))
    except Exception:
        pass
    if run_folder:
        out_paths.append(run_folder / "validation" / "validate_8520_output.txt")
    for out_path in out_paths:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(out_text, encoding="utf-8")
        except Exception:
            pass

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
