"""Validation for V3.3.5.5.8.5.17 long session flight recorder & crash forensics."""
from __future__ import annotations

import json
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
    LONG_SESSION_STABILITY_MODE,
    PARTIAL_ALPHA_AUTOSAVE_COMMIT_INTERVAL,
    PARTIAL_ALPHA_AUTOSAVE_INTERVAL_S,
    PARTIAL_INDEX_AUTOSAVE_COMMIT_INTERVAL,
    PARTIAL_INDEX_AUTOSAVE_INTERVAL_S,
    resolve_japanese_keyterms,
)
from alpha.transcription.japanese_accuracy_cleaner import (
    normalize_business_cleanup_once,
    run_business_cleanup_selftest_once,
)

_DUPLICATE_BAD = ("いついつも", "このこのたび")


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    if APP_VERSION != "3.3.5.5.8.5.17":
        failures.append("version")
    if APP_CODENAME != "Long Session Stability Flight Recorder & Crash Forensics":
        failures.append("codename")
    if not LONG_SESSION_STABILITY_MODE:
        failures.append("long_session_stability_mode")
    if JAPANESE_STT_PROFILE != "no_diarize":
        failures.append("stt_profile")
    if JAPANESE_KEYTERM_PROFILE != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("keyterm_profile")
    if DEEPGRAM_MODEL != "nova-3" or DEEPGRAM_LANGUAGE != "ja":
        failures.append("deepgram_config")
    if PARTIAL_ALPHA_AUTOSAVE_COMMIT_INTERVAL != 25:
        failures.append("alpha_commit_cadence")
    if PARTIAL_INDEX_AUTOSAVE_COMMIT_INTERVAL != 50:
        failures.append("index_commit_cadence")
    if PARTIAL_ALPHA_AUTOSAVE_INTERVAL_S != 30.0:
        failures.append("alpha_interval_cadence")
    if PARTIAL_INDEX_AUTOSAVE_INTERVAL_S != 60.0:
        failures.append("index_interval_cadence")

    terms, profile, _ = resolve_japanese_keyterms()
    if profile != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("resolved_profile")
    if not run_business_cleanup_selftest_once():
        failures.append("business_cleanup_selftest")

    for text in ("いつもお世話になっております", "このたび御社"):
        r = normalize_business_cleanup_once(text, verify_second_pass=True)
        for bad in _DUPLICATE_BAD:
            if bad in r["candidate"]:
                failures.append(f"cleanup_{bad}")

    jp_log = ROOT / "logs" / "v3.3.5.5.8.5.17_japanese_accuracy.log"
    if jp_log.exists():
        text = jp_log.read_text(encoding="utf-8", errors="ignore")
        for ev in (
            "LONG_SESSION_STABILITY_MODE_ACTIVE",
            "FLIGHT_RECORDER_STARTED",
            "PARTIAL_AUTOSAVE_CADENCE_CONFIGURED",
        ):
            if ev not in text:
                warnings.append(f"missing_{ev}")
        if "THREAD_DUMP_FAILED" in text and "selftest" in text.lower():
            failures.append("thread_dump_selftest_failed")

    live = ROOT / "run_artifacts"
    for folder in sorted(live.glob("v3.3.5.5.8.5.17-*"))[-2:]:
        fr = folder / "FLIGHT_RECORDER.log"
        if fr.exists():
            if "MainThread" not in fr.read_text(encoding="utf-8", errors="ignore"):
                warnings.append("flight_recorder_missing_mainthread")
        selftest = folder / "THREAD_DUMP_SELFTEST.txt"
        if selftest.exists():
            st = selftest.read_text(encoding="utf-8", errors="ignore")
            if "MainThread" not in st and "main thread" not in st.lower():
                warnings.append("selftest_missing_mainthread")

    status = "FAILED" if failures else ("PASSED_WITH_WARNINGS" if warnings else "PASSED")
    print(f"V3.3.5.5.8.5.17 LONG SESSION FLIGHT RECORDER VALIDATION: {status}")
    if failures:
        print("Failures:", ", ".join(failures))
    if warnings:
        print("Warnings:", ", ".join(warnings))
    print(f"app_version={APP_VERSION}")
    return 0 if status != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
