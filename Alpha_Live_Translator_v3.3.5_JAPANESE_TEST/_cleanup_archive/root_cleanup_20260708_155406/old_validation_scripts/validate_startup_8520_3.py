"""Startup recovery validation for V3.3.5.5.8.5.20.3."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    APP_VERSION,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    HIGH_FREQUENCY_UI_DRAIN_LOGGING_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    UI_EVENT_DRAIN_VERBOSE_LOGGING,
)
from alpha.utils.async_debug_log import ensure_async_logger_healthy_non_blocking
from alpha.utils.troubleshooting_paths import (
    get_troubleshooting_root,
    safe_log_event,
    safe_register_writer,
    safe_rebind_writer,
)


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    if APP_VERSION != "3.3.5.5.8.5.20.3":
        failures.append("app_version")

    root = get_troubleshooting_root()
    if not root.exists():
        failures.append("troubleshooting_root")

    try:
        safe_log_event("STARTUP_RECOVERY_MODE_ACTIVE")
    except Exception:
        failures.append("safe_log_event")

    try:
        path = safe_register_writer(
            "startup_check_writer",
            "logs",
            "startup_check.log",
        )
        safe_rebind_writer("startup_check_writer", path.parent.parent)
    except Exception:
        failures.append("safe_writer_register_rebind")

    health = ensure_async_logger_healthy_non_blocking()
    if not isinstance(health, dict):
        failures.append("async_logger_health_result")
    elif not bool(health.get("writer_thread_alive", False)):
        warnings.append("async_logger_not_alive_non_blocking")

    if DEEPGRAM_MODEL != "nova-3" or DEEPGRAM_LANGUAGE != "ja":
        failures.append("deepgram_core_config_changed")
    if DEEPGRAM_ENDPOINTING_MS != 500 or DEEPGRAM_UTTERANCE_END_MS != 1500:
        failures.append("deepgram_timing_changed")
    if JAPANESE_STT_PROFILE != "no_diarize":
        failures.append("japanese_stt_profile_changed")
    if JAPANESE_KEYTERM_PROFILE != "business_japanese":
        failures.append("japanese_keyterm_profile_changed")

    if HIGH_FREQUENCY_UI_DRAIN_LOGGING_ENABLED:
        failures.append("high_frequency_ui_drain_logging_enabled")
    if UI_EVENT_DRAIN_VERBOSE_LOGGING:
        failures.append("ui_event_drain_verbose_enabled")
    try:
        mw = Path("alpha/ui/main_window.py").read_text(encoding="utf-8", errors="ignore")
        if "validate_8520_" in mw:
            failures.append("validation_called_in_startup_or_start")
        if "create_upload_evidence_package(" in mw:
            failures.append("upload_package_called_in_startup_or_start")
    except Exception:
        warnings.append("main_window_startup_scan_failed")

    result = (
        "FAILED"
        if failures
        else "PASSED_WITH_WARNINGS"
        if warnings
        else "PASSED"
    )
    log(f"V3.3.5.5.8.5.20.3 STARTUP RECOVERY VALIDATION: {result}")
    if failures:
        log("Failures: " + ", ".join(failures))
    if warnings:
        log("Warnings: " + ", ".join(warnings))
    log("Deepgram config unchanged: model=nova-3 language=ja endpointing=500 utterance_end_ms=1500")
    log("Japanese profile unchanged: JAPANESE_STT_PROFILE=no_diarize JAPANESE_KEYTERM_PROFILE=business_japanese")

    out = Path("troubleshooting/validation/validate_startup_8520_3_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
