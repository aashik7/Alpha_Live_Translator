"""Validation for V3.3.5.5.8.5.15 hang watchdog and crash-safe artifact recovery."""
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
    VALID_SHORT_JAPANESE_LIST_TERMS,
    resolve_japanese_keyterms,
)
from alpha.transcription.japanese_accuracy_cleaner import (
    detect_duplicate_damage,
    normalize_business_cleanup_once,
    run_business_cleanup_selftest_once,
)

_DUPLICATE_BAD = (
    "いついつも",
    "このこのたび",
    "おお世話",
    "後後任",
    "担当担当交代",
    "使役使役形",
    "参参りました",
    "くださいください",
)
_ESL_MARKERS = ("ESL", "トワイス", "ジヒョ", "英語のレベル", "自分の意見")


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
        elif "message" in ev:
            names.append(str(ev["message"]))
    return names


def _parse_latest_live_index(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text = _read_text(root / "LATEST_LIVE_RUN_ARTIFACTS_INDEX.txt")
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _find_latest_run_folder(root: Path) -> Path | None:
    live_root = root / "run_artifacts"
    if not live_root.exists():
        return None
    folders = sorted(
        [p for p in live_root.iterdir() if p.is_dir() and "8.5.15" in p.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return folders[0] if folders else None


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    if APP_VERSION != "3.3.5.5.8.5.15":
        failures.append("version")
    if APP_CODENAME != "Mid-Session Hang Watchdog & Crash-Safe Artifact Recovery":
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
    if any(m in terms for m in _ESL_MARKERS):
        failures.append("esl_twice_terms_in_active_profile")
    if profile != KEYTERM_PROFILE_BUSINESS_JAPANESE:
        failures.append("resolved_profile_not_business")

    if not run_business_cleanup_selftest_once():
        failures.append("business_cleanup_selftest")

    for text in ("いつもお世話になっております", "このたび御社"):
        r = normalize_business_cleanup_once(text, verify_second_pass=True)
        for bad in _DUPLICATE_BAD:
            if bad in r["candidate"]:
                failures.append(f"cleanup_produces_{bad}")

    jp_log = ROOT / "logs" / "v3.3.5.5.8.5.15_japanese_accuracy.log"
    fg_log = ROOT / "logs" / "v3.3.5.5.8.5.15_freeze_guard.log"
    diag_log = ROOT / "logs" / "v3.3.5.5.8.5.15_diagnostic_test.log"
    debug_logs = sorted((ROOT / "debug").glob("debug-46ae0c-v3.3.5.5.8.5.15-*.log"))

    jp_events = _event_names(_load_ndjson_events(jp_log))
    fg_events = _event_names(_load_ndjson_events(fg_log))
    all_events = jp_events + fg_events

    for required in (
        "CRASH_HOOKS_INSTALLED",
        "WATCHDOG_THREAD_STARTED",
        "UI_HEARTBEAT_STARTED",
        "BUSINESS_CLEANUP_SELFTEST_PASSED",
        "BUSINESS_CLEANUP_LIVE_OVERHEAD_REDUCED",
    ):
        if required not in all_events and required not in jp_events:
            if jp_log.exists() or fg_log.exists():
                warnings.append(f"missing_event_{required}")
            else:
                warnings.append(f"no_logs_yet_{required}")

    latest = _parse_latest_live_index(ROOT)
    run_folder = _find_latest_run_folder(ROOT)
    if run_folder is None and latest.get("artifact_folder"):
        run_folder = Path(latest["artifact_folder"])

    if latest.get("latest_live_app_version") and "8.5.15" not in latest.get(
        "latest_live_app_version", ""
    ):
        warnings.append("latest_index_not_8515")
    if latest.get("latest_live_app_version", "").startswith("3.3.5.5.8.5.13"):
        warnings.append("stale_8513_index_pointer")
    if latest.get("latest_live_app_version", "").startswith("3.3.5.5.8.5.14"):
        warnings.append("latest_index_still_8514")

    partial_count = jp_events.count("PARTIAL_ALPHA_OUTPUT_AUTOSAVED")
    partial_index_count = jp_events.count("PARTIAL_RUN_INDEX_AUTOSAVED")
    progress_count = jp_events.count("RUN_PROGRESS_HEARTBEAT")

    if jp_log.exists() and progress_count == 0:
        warnings.append("no_run_progress_heartbeat_in_log")
    if jp_log.exists() and partial_count == 0:
        warnings.append("no_partial_alpha_autosave_in_log")
    if jp_log.exists() and partial_index_count == 0:
        warnings.append("no_partial_index_autosave_in_log")

    run_completed = "FINAL_LIVE_SESSION_SUMMARY" in jp_events or any(
        "status=completed" in _read_text(run_folder / "RUN_ARTIFACTS_INDEX.txt")
        for _ in [0]
        if run_folder and (run_folder / "RUN_ARTIFACTS_INDEX.txt").exists()
    )
    run_crashed = any(
        e in all_events
        for e in (
            "UI_MAINLOOP_STALL_CONFIRMED",
            "CRASH_HOOK_TRIGGERED",
            "WINDOW_CLOSE_FORCED_AFTER_TIMEOUT",
        )
    )

    if run_completed:
        if "RUN_CONSISTENCY_CHECK_PASSED" not in jp_events:
            warnings.append("missing_run_consistency_check_passed")
        if run_folder and not (run_folder / "RUN_ARTIFACTS_INDEX.txt").exists():
            failures.append("missing_final_run_index")
    elif run_crashed or (run_folder and (run_folder / "RUN_ARTIFACTS_INDEX.partial.txt").exists()):
        if run_folder:
            if not (run_folder / "Alpha_output_PARTIAL.txt").exists():
                failures.append("missing_partial_alpha_on_incomplete_run")
            if not (run_folder / "RUN_ARTIFACTS_INDEX.partial.txt").exists():
                failures.append("missing_partial_index_on_incomplete_run")
            if not (run_folder / "LIVE_RUN_STATUS.json").exists():
                warnings.append("missing_live_run_status_json")
            if not (run_folder / "LAST_HEALTH_SNAPSHOT.json").exists():
                warnings.append("missing_health_snapshot_json")

    transcript_blob = ""
    if run_folder and "8.5.15" in run_folder.name:
        for name in ("Alpha_output_FINAL.txt", "Alpha_output_PARTIAL.txt"):
            p = run_folder / name
            if p.exists():
                transcript_blob += _read_text(p)
    elif latest.get("latest_live_app_version", "").startswith("3.3.5.5.8.5.15"):
        alpha_partial = latest.get("alpha_output_partial_path", "")
        if alpha_partial:
            transcript_blob += _read_text(Path(alpha_partial))
    if latest.get("latest_live_app_version", "").startswith("3.3.5.5.8.5.15"):
        transcript_blob += _read_text(ROOT / "Alpha output.txt")
    if transcript_blob:
        if "いついつも" in transcript_blob:
            failures.append("transcript_contains_itsuitsumo")
        if "このこのたび" in transcript_blob:
            failures.append("transcript_contains_konokonotabi")
    if "翌日" in transcript_blob or "翌日" in _read_text(jp_log):
        pass  # preserved if emitted — informational only

    if debug_logs:
        dbg_text = _read_text(debug_logs[-1])
        if "diarize" in dbg_text.lower() and "no_diarize" not in dbg_text:
            warnings.append("possible_diarize_in_debug")
    else:
        warnings.append("no_debug_log_yet")

    if not diag_log.exists():
        warnings.append("diagnostic_log_not_created_yet")

    # DeepL must not be active
    if "deepl" in _read_text(jp_log).lower() and "deepl_not_active" not in _read_text(jp_log):
        warnings.append("deepl_mention_in_log")

    status = "FAILED"
    if not failures:
        status = "PASSED_WITH_WARNINGS" if warnings else "PASSED"

    print(f"V3.3.5.5.8.5.15 HANG WATCHDOG VALIDATION: {status}")
    if failures:
        print("Failures:", ", ".join(failures))
    if warnings:
        print("Warnings:", ", ".join(warnings))
    print(f"app_version={APP_VERSION}")
    print(f"partial_autosave_count={partial_count}")
    print(f"progress_heartbeat_count={progress_count}")
    if run_folder:
        print(f"run_folder={run_folder}")
    return 0 if status != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
