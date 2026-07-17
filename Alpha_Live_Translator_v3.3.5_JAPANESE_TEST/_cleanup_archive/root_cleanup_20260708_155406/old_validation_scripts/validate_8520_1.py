"""Validation for V3.3.5.5.8.5.20.1 troubleshooting evidence routing hotfix."""
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
    CENTRALIZED_TROUBLESHOOTING_DIR,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    KEYTERM_PROFILE_BUSINESS_JAPANESE,
    PENDING_RUN_REBINDING_ENABLED,
    TROUBLESHOOTING_MODE,
    resolve_japanese_keyterms,
)
from alpha.transcription.japanese_accuracy_cleaner import (
    normalize_business_cleanup_once,
    run_business_cleanup_selftest_once,
)
from alpha.transcription.japanese_sentence_assembler import JapaneseContinuityAssembler
from alpha.utils.language_pipeline_worker import start_language_pipeline_worker
from alpha.utils.process_health_telemetry import collect_process_metrics
from alpha.utils.stop_finalize_worker import build_stop_finalize_summary
from alpha.utils.troubleshooting_paths import (
    assert_active_run_folder_is_not_pending,
    get_troubleshooting_root,
    get_validation_path,
    scan_active_files_in_pending,
    scan_runtime_files_outside_troubleshooting,
)
from alpha.utils.ui_event_bus import get_ui_event_bus, start_ui_event_bus
from alpha.utils.ui_thread_guard import register_ui_main_thread

_DUPLICATE_BAD = ("いついつも", "このこのたび")

_REQUIRED_LOGS = [
    "logs/japanese_accuracy.log",
    "logs/diagnostic_test.log",
    "logs/freeze_guard.log",
    "logs/debug.log",
    "logs/async_debug.log",
    "logs/deepgram_events.jsonl",
    "logs/stop_finalize_timeline.jsonl",
    "logs/ui_event_bus_timeline.jsonl",
    "logs/queue_timeline.jsonl",
    "logs/thread_safety.jsonl",
    "logs/background_tk_guard.jsonl",
]

_REQUIRED_TRANSCRIPTS = [
    "transcripts/Alpha output.txt",
    "transcripts/raw_deepgram_finals.jsonl",
    "transcripts/stable_commits.jsonl",
    "transcripts/ui_exported_segments.jsonl",
    "transcripts/raw_deepgram_interims_sampled.jsonl",
]

_REQUIRED_ACCURACY = [
    "accuracy/assembler_decisions.jsonl",
    "accuracy/quarantine_decisions.jsonl",
    "accuracy/correction_decisions.jsonl",
    "accuracy/translation_readiness_summary.json",
]

_REQUIRED_HEALTH = [
    "health/HEALTH_TIMELINE.jsonl",
    "health/PROCESS_HEALTH_TIMELINE.jsonl",
    "health/MEMORY_TREND_SUMMARY.json",
]


def _find_latest_real_run_folder() -> Path | None:
    runs = get_troubleshooting_root() / "runs"
    if not runs.exists():
        return None
    folders = [
        p
        for p in runs.iterdir()
        if p.is_dir() and p.name != "_pending" and f"v{APP_VERSION}" in p.name
    ]
    if not folders:
        return None
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

    if APP_VERSION != "3.3.5.5.8.5.20.1":
        failures.append("version")
    if APP_CODENAME != "Troubleshooting Evidence Routing Hotfix":
        failures.append("codename")
    if not TROUBLESHOOTING_MODE or not CENTRALIZED_TROUBLESHOOTING_DIR:
        failures.append("troubleshooting_flags")
    if not PENDING_RUN_REBINDING_ENABLED:
        failures.append("pending_rebinding_disabled")
    if JAPANESE_STT_PROFILE != "no_diarize":
        failures.append("stt_profile")
    if DEEPGRAM_MODEL != "nova-3" or DEEPGRAM_LANGUAGE != "ja":
        failures.append("deepgram_config")

    ts_root = get_troubleshooting_root()
    if not ts_root.exists():
        failures.append("troubleshooting_root_missing")

    run_folder = _find_latest_real_run_folder()
    if run_folder is None:
        warnings.append("no_real_run_folder_yet")
    else:
        if run_folder.name == "_pending":
            failures.append("active_run_is_pending")
        if not assert_active_run_folder_is_not_pending():
            warnings.append("active_folder_not_asserted")

        manifest_path = run_folder / "RUN_MANIFEST.json"
        if not manifest_path.exists():
            failures.append("run_manifest_missing")
        else:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("final_status") == "in_progress":
                    warnings.append("run_manifest_still_in_progress")
                if manifest.get("app_version") != APP_VERSION:
                    warnings.append("manifest_version_mismatch")
            except Exception:
                failures.append("run_manifest_parse")

        latest_ptr = ts_root / "latest" / "LATEST_RUN_POINTER.json"
        if latest_ptr.exists():
            try:
                ptr = json.loads(latest_ptr.read_text(encoding="utf-8"))
                if ptr.get("status") == "in_progress":
                    warnings.append("latest_pointer_still_in_progress")
            except Exception:
                warnings.append("latest_pointer_parse")
        else:
            warnings.append("latest_pointer_missing")

        live_status = run_folder / "artifacts" / "LIVE_RUN_STATUS.json"
        if live_status.exists():
            try:
                status = json.loads(live_status.read_text(encoding="utf-8"))
                if status.get("stop_finalize_completed") and not status.get(
                    "run_artifacts_index_written"
                ):
                    warnings.append("live_status_artifact_flags_inconsistent")
                if status.get("stop_finalize_completed") and status.get("timed_out_steps"):
                    warnings.append("stop_timed_out_steps_non_empty")
            except Exception:
                warnings.append("live_status_parse")

        for rel in _REQUIRED_LOGS + _REQUIRED_TRANSCRIPTS + _REQUIRED_ACCURACY + _REQUIRED_HEALTH:
            p = run_folder / rel
            if not p.exists():
                failures.append(f"missing:{rel}")

        validation_out = run_folder / "validation" / "validate_8520_1_output.txt"
        if not validation_out.exists():
            warnings.append("validate_output_not_in_run_folder")

        upload_idx = run_folder / "upload_package" / "UPLOAD_PACKAGE_INDEX.txt"
        if not upload_idx.exists():
            warnings.append("upload_package_index_missing")
        else:
            idx_text = upload_idx.read_text(encoding="utf-8", errors="ignore")
            if ".wav" in idx_text and "EXCLUDED_BY_POLICY" not in idx_text:
                warnings.append("wav_not_marked_excluded_in_index")

        zips = list(run_folder.glob("upload_package/UPLOAD_PACKAGE_v*.zip"))
        if not zips:
            warnings.append("upload_zip_missing")

    pending_active = scan_active_files_in_pending()
    if pending_active:
        warnings.append(f"pending_active_files:{len(pending_active)}")

    outside = scan_runtime_files_outside_troubleshooting()
    if outside:
        warnings.append(f"legacy_runtime_files:{len(outside)}")

    terms, profile, _ = resolve_japanese_keyterms()
    if profile != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("keyterm_profile")
    if not run_business_cleanup_selftest_once():
        failures.append("business_cleanup_selftest")

    for text in ("いつもお世話になっております", "このたび御社"):
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

    stop_src = (ROOT / "alpha" / "utils" / "stop_finalize_worker.py").read_text(encoding="utf-8")
    if "timed_out_steps=timed_out_steps" in stop_src and "**stop_summary" in stop_src:
        failures.append("duplicate_kwargs_stop_finalize")

    host = MagicMock()
    host._dg_close_status = "normal"
    summary = build_stop_finalize_summary(host, dg_result={"timed_out": True})
    if summary.get("stop_finalize_timed_out") and not summary.get("timed_out_steps"):
        failures.append("stop_timed_out_without_steps")

    try:
        from alpha.utils.tk_thread_guard import get_tk_guard_stats

        tk_stats = get_tk_guard_stats()
        active_blocked = int(tk_stats.get("background_tk_call_blocked_count_active_session", 0))
        if active_blocked > 0:
            warnings.append(f"active_session_tk_blocked:{active_blocked}")
    except Exception:
        warnings.append("tk_guard_stats_unavailable")

    metrics = collect_process_metrics()
    if metrics.get("process_memory_rss_mb", -1) < 0:
        warnings.append("process_metrics_unavailable")

    bus = get_ui_event_bus()
    if bus is None:
        failures.append("ui_event_bus")
    else:
        stats = bus.stats()
        if int(stats.get("dropped", 0)) > 0:
            warnings.append(f"ui_event_dropped:{stats.get('dropped')}")

    status = "FAILED" if failures else ("PASSED_WITH_WARNINGS" if warnings else "PASSED")
    log(f"V3.3.5.5.8.5.20.1 TROUBLESHOOTING EVIDENCE ROUTING VALIDATION: {status}")
    if failures:
        log("Failures: " + ", ".join(failures))
    if warnings:
        log("Warnings: " + ", ".join(warnings))

    out_text = output.getvalue()
    out_paths = [ROOT / "validate_8520_1_output.txt"]
    try:
        out_paths.append(get_validation_path("validate_8520_1_output"))
    except Exception:
        pass
    if run_folder:
        out_paths.append(run_folder / "validation" / "validate_8520_1_output.txt")
    for out_path in out_paths:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(out_text, encoding="utf-8")
        except Exception:
            pass

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
