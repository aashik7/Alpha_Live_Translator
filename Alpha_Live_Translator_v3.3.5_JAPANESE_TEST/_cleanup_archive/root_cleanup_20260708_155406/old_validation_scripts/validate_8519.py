"""Validation for V3.3.5.5.8.5.19 long-run evidence cleanup."""
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
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FORBID_BACKGROUND_TK_CALLS,
    FORBID_UI_BLOCKING_LOCK_WAIT,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    KEYTERM_PROFILE_BUSINESS_JAPANESE,
    LANGUAGE_AGNOSTIC_UI_EVENT_BUS,
    LONG_RUN_EVIDENCE_PACKAGE_MODE,
    LONG_SESSION_STABILITY_MODE,
    TK_SAFE_PIPELINE_MODE,
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
from alpha.utils.ui_event_bus import get_ui_event_bus, start_ui_event_bus
from alpha.utils.ui_thread_guard import register_ui_main_thread

_DUPLICATE_BAD = ("いついつも", "このこのたび")


def run_deadlock_regression_test() -> bool:
    register_ui_main_thread()
    start_ui_event_bus()
    start_language_pipeline_worker()
    host = MagicMock()
    host.after = MagicMock(side_effect=AssertionError("host.after must not be called"))
    host.after_cancel = MagicMock()
    host.is_listening = True
    host.listening = False
    host._dg_ws = object()
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


def _find_latest_live_artifact_folder() -> Path | None:
    live_root = ROOT / "run_artifacts"
    if not live_root.exists():
        return None
    folders = [
        p
        for p in live_root.iterdir()
        if p.is_dir() and f"v{APP_VERSION}" in p.name
    ]
    if not folders:
        return None
    return max(folders, key=lambda p: p.stat().st_mtime)


def _read_log_events(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    output = StringIO()

    def log(line: str = "") -> None:
        print(line)
        output.write(line + "\n")

    if APP_VERSION != "3.3.5.5.8.5.19":
        failures.append("version")
    if APP_CODENAME != "Long-Run Guard Cleanup & Complete Evidence Package":
        failures.append("codename")
    if not LONG_RUN_EVIDENCE_PACKAGE_MODE:
        failures.append("long_run_evidence_package_mode")
    if not TK_SAFE_PIPELINE_MODE:
        failures.append("tk_safe_pipeline_mode")
    if not LANGUAGE_AGNOSTIC_UI_EVENT_BUS:
        failures.append("ui_event_bus_flag")
    if not FORBID_BACKGROUND_TK_CALLS:
        failures.append("forbid_background_tk")
    if not FORBID_UI_BLOCKING_LOCK_WAIT:
        failures.append("forbid_ui_blocking_lock")
    if not LONG_SESSION_STABILITY_MODE:
        failures.append("long_session_stability_mode")
    if JAPANESE_STT_PROFILE != "no_diarize":
        failures.append("stt_profile")
    if JAPANESE_KEYTERM_PROFILE != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("keyterm_profile")
    if DEEPGRAM_MODEL != "nova-3" or DEEPGRAM_LANGUAGE != "ja":
        failures.append("deepgram_config")
    if DEEPGRAM_ENDPOINTING_MS != 500 or DEEPGRAM_UTTERANCE_END_MS != 1500:
        failures.append("deepgram_timing")

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

    mw_src = (ROOT / "alpha" / "ui" / "main_window.py").read_text(encoding="utf-8")
    if "interim_flush_requested" not in mw_src:
        failures.append("interim_flush_event_bus_missing")
    if "DEEPGRAM_INTERIM_FLUSH_REROUTED_TO_UI_EVENT_BUS" not in mw_src:
        failures.append("interim_flush_reroute_log_missing")

    classifier_src = (
        ROOT / "alpha" / "utils" / "component_stall_classifier.py"
    ).read_text(encoding="utf-8")
    if "COMPONENT_STALL_TRANSIENT_SPIKE" not in classifier_src:
        failures.append("stall_classifier_tuning_missing")

    metrics = collect_process_metrics()
    if metrics.get("process_memory_rss_mb", -1) < 0:
        fg = _read_log_events(ROOT / "logs" / f"v{APP_VERSION}_freeze_guard.log")
        if "PROCESS_METRICS_UNAVAILABLE" not in fg:
            warnings.append("process_metrics_unavailable_not_logged")

    host = MagicMock()
    host._dg_close_status = "normal"
    summary = build_stop_finalize_summary(host, dg_result={"timed_out": True})
    if summary.get("stop_finalize_timed_out"):
        failures.append("stop_finalize_timed_out_without_steps")
    if not summary.get("deepgram_close_late_normal"):
        warnings.append("deepgram_close_late_normal_not_set_in_test")

    artifacts = _find_latest_live_artifact_folder()
    jp_log = _read_log_events(ROOT / "logs" / f"v{APP_VERSION}_japanese_accuracy.log")
    fg_log = _read_log_events(ROOT / "logs" / f"v{APP_VERSION}_freeze_guard.log")
    combined = jp_log + fg_log

    for token in (
        "TK_SAFE_PIPELINE_MODE_ACTIVE",
        "LANGUAGE_AGNOSTIC_UI_EVENT_BUS_ACTIVE",
        "LONG_RUN_EVIDENCE_PACKAGE_MODE_ACTIVE",
    ):
        if token not in combined:
            warnings.append(f"missing_startup_log:{token}")

    if artifacts:
        upload_index = artifacts / "UPLOAD_PACKAGE_INDEX.txt"
        if not upload_index.exists():
            warnings.append("upload_package_index_missing_in_latest_run")
        else:
            log(f"UPLOAD_PACKAGE_INDEX found: {upload_index}")
        live_status_path = artifacts / "LIVE_RUN_STATUS.json"
        index_path = artifacts / "RUN_ARTIFACTS_INDEX.txt"
        if live_status_path.exists() and index_path.exists():
            try:
                live_status = json.loads(live_status_path.read_text(encoding="utf-8"))
                index_text = index_path.read_text(encoding="utf-8", errors="ignore")
                index_status = ""
                for line in index_text.splitlines():
                    if line.startswith("status="):
                        index_status = line.split("=", 1)[1].strip()
                        break
                if live_status.get("status") != index_status and index_status:
                    failures.append("live_run_status_index_mismatch")
                if live_status.get("stop_finalize_timed_out") and not live_status.get(
                    "timed_out_steps"
                ):
                    failures.append("stop_finalize_timed_out_without_steps_in_artifact")
            except Exception:
                warnings.append("artifact_status_parse_failed")

        proc_health = artifacts / "PROCESS_HEALTH_TIMELINE.jsonl"
        if not proc_health.exists():
            warnings.append("process_health_timeline_missing_in_latest_run")

    bus = get_ui_event_bus()
    if bus is None:
        failures.append("ui_event_bus")
    else:
        stats = bus.stats()
        posted = int(stats.get("posted", 0))
        drained = int(stats.get("drained", 0))
        if posted != drained and posted > 0:
            warnings.append(f"ui_event_posted_drained_mismatch:{posted}!={drained}")

    try:
        from alpha.utils.tk_thread_guard import get_tk_guard_stats

        tk_stats = get_tk_guard_stats()
        blocked = int(tk_stats.get("background_tk_call_blocked_count", 0))
        if blocked > 0:
            warnings.append(f"background_tk_call_blocked_count={blocked}")
        else:
            log("BACKGROUND_TK_CALL_GUARD_REMAINED_ZERO")
    except Exception:
        warnings.append("tk_guard_stats_unavailable")

    if "STOP_FINALIZE_SUMMARY_NORMALIZED" not in (
        ROOT / "alpha" / "utils" / "stop_finalize_worker.py"
    ).read_text(encoding="utf-8"):
        failures.append("stop_finalize_summary_normalized_missing")

    if not (ROOT / "alpha" / "utils" / "segment_count_reconciliation.py").exists():
        failures.append("segment_count_reconciliation_missing")

    status = "FAILED" if failures else ("PASSED_WITH_WARNINGS" if warnings else "PASSED")
    log(f"V3.3.5.5.8.5.19 LONG-RUN EVIDENCE CLEANUP VALIDATION: {status}")
    if failures:
        log("Failures: " + ", ".join(failures))
    if warnings:
        log("Warnings: " + ", ".join(warnings))

    out_text = output.getvalue()
    out_paths: list[Path] = [ROOT / "validate_8519_output.txt"]
    folder = artifacts or (ROOT / "run_artifacts")
    out_paths.append(folder / "validate_8519_output.txt")
    for out_path in out_paths:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(out_text, encoding="utf-8")
        except Exception:
            pass

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
