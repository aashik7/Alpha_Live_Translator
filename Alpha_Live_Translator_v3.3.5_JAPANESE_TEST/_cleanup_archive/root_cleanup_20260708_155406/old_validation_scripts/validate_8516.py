"""Validation for V3.3.5.5.8.5.16 UI thread isolation and thread dump fix."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    KEYTERM_PROFILE_BUSINESS_JAPANESE,
    resolve_japanese_keyterms,
)
from alpha.transcription.japanese_accuracy_cleaner import (
    detect_duplicate_damage,
    normalize_business_cleanup_once,
    run_business_cleanup_selftest_once,
)

_DUPLICATE_BAD = ("いついつも", "このこのたび")


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _load_ndjson_events(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if '"event":' in line:
                m = re.search(r'"event":\s*"([^"]+)"', line)
                if m:
                    events.append({"event": m.group(1)})
    return events


def _event_names(events: list[dict]) -> list[str]:
    names: list[str] = []
    for ev in events:
        if "event" in ev:
            names.append(str(ev["event"]))
    return names


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    if APP_VERSION != "3.3.5.5.8.5.16":
        failures.append("version")
    if APP_CODENAME != "UI Thread Isolation & Watchdog Thread Dump Fix":
        failures.append("codename")
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
        failures.append("resolved_profile_not_business")
    esl_markers = ("ESL", "トワイス", "ジヒョ", "英語のレベル")
    if any(m in terms for m in esl_markers):
        failures.append("esl_twice_terms_in_active_profile")

    if not run_business_cleanup_selftest_once():
        failures.append("business_cleanup_selftest")

    for text in ("いつもお世話になっております", "このたび御社"):
        r = normalize_business_cleanup_once(text, verify_second_pass=True)
        for bad in _DUPLICATE_BAD:
            if bad in r["candidate"]:
                failures.append(f"cleanup_produces_{bad}")

    jp_log = ROOT / "logs" / "v3.3.5.5.8.5.16_japanese_accuracy.log"
    fg_log = ROOT / "logs" / "v3.3.5.5.8.5.16_freeze_guard.log"
    jp_events = _event_names(_load_ndjson_events(jp_log))
    all_events = jp_events + _event_names(_load_ndjson_events(fg_log))

    for required in (
        "UI_HEARTBEAT_MINIMAL_MODE_ACTIVE",
        "TRANSCRIPT_SNAPSHOT_STORE_STARTED",
    ):
        if jp_log.exists() and required not in all_events:
            warnings.append(f"missing_{required}")

    if jp_log.exists():
        if "PARTIAL_ALPHA_OUTPUT_AUTOSAVED" in jp_events and (
            "PARTIAL_ALPHA_OUTPUT_AUTOSAVED_BACKGROUND" not in jp_events
        ):
            warnings.append("legacy_ui_path_autosave_still_used")
        if jp_events.count("UI_THREAD_BLOCKING_CALL_BLOCKED") > 0:
            warnings.append("ui_thread_blocking_calls_detected")
        if jp_events.count("THREAD_DUMP_FAILED") > 0:
            failures.append("thread_dump_failed_in_log")
        if jp_events.count("UI_MAINLOOP_STALL_CONFIRMED") > 0:
            warnings.append("ui_mainloop_stall_confirmed_in_run")
        very_slow = jp_events.count("UI_AFTER_CALLBACK_VERY_SLOW")
        if very_slow > 0:
            warnings.append(f"ui_after_callback_very_slow_count={very_slow}")

    live_root = ROOT / "run_artifacts"
    dump_files = (
        list(live_root.glob("v3.3.5.5.8.5.16-*/THREAD_DUMP_LAST.txt"))
        if live_root.exists()
        else []
    )
    for dump_path in dump_files:
        text = _read_text(dump_path)
        if "UnsupportedOperation" in text and "fileno" in text:
            failures.append("thread_dump_stringio_error_still_present")
        if text and "Thread" not in text and "thread" not in text.lower():
            warnings.append(f"thread_dump_maybe_empty:{dump_path.name}")

    partial_8516 = list(live_root.glob("v3.3.5.5.8.5.16-*/Alpha_output_PARTIAL.txt")) if live_root.exists() else []
    if jp_log.exists() and not partial_8516:
        warnings.append("no_8516_partial_output_yet")

    status = "FAILED" if failures else ("PASSED_WITH_WARNINGS" if warnings else "PASSED")
    print(f"V3.3.5.5.8.5.16 UI THREAD ISOLATION VALIDATION: {status}")
    if failures:
        print("Failures:", ", ".join(failures))
    if warnings:
        print("Warnings:", ", ".join(warnings))
    print(f"app_version={APP_VERSION}")
    return 0 if status != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
