"""Validate runtime stop freeze elimination patch 8.5.20.5."""

from __future__ import annotations

from pathlib import Path

from alpha.constants import (
    APP_VERSION,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    OFFLINE_EVIDENCE_PACKAGING_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STOP_PATH_MINIMAL_MODE,
)


def has(path: str, token: str) -> bool:
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def main() -> int:
    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.20.5",
        "runtime_package_disabled": RUNTIME_EVIDENCE_PACKAGE_DISABLED,
        "offline_package_enabled": OFFLINE_EVIDENCE_PACKAGING_ENABLED and Path("package_latest_troubleshooting_run.py").exists(),
        "stop_minimal_mode": STOP_PATH_MINIMAL_MODE and has("alpha/utils/stop_finalize_worker.py", "STOP_MINIMAL_BEGIN"),
        "evidence_worker_disabled": has("alpha/utils/stop_finalize_worker.py", "EVIDENCE_PACKAGE_WORKER_DISABLED_DURING_RUNTIME"),
        "no_runtime_index_rewrite": has("alpha/utils/stop_finalize_worker.py", "_write_minimal_runtime_artifacts"),
        "no_runtime_upload": has("alpha/constants.py", "NO_UPLOAD_ZIP_DURING_RUNTIME = True"),
        "no_runtime_validation": has("alpha/constants.py", "NO_VALIDATION_DURING_RUNTIME_STOP = True"),
        "ui_watchdog": has("alpha/ui/main_window.py", "STOP_UI_WATCHDOG_STARTED"),
        "ui_restore_on_ui_thread": has("alpha/ui/main_window.py", "STOP_UI_RESTORE_EXECUTED_ON_UI_THREAD"),
        "app_close_non_blocking": has("alpha/ui/main_window.py", "APP_CLOSE_NON_BLOCKING"),
        "deepgram_unchanged": DEEPGRAM_MODEL == "nova-3" and DEEPGRAM_LANGUAGE == "ja" and DEEPGRAM_ENDPOINTING_MS == 500 and DEEPGRAM_UTTERANCE_END_MS == 1500,
        "japanese_unchanged": JAPANESE_STT_PROFILE == "no_diarize" and JAPANESE_KEYTERM_PROFILE == "business_japanese",
    }
    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.20.5 RUNTIME STOP FREEZE ELIMINATION VALIDATION",
        f"Result: {result}",
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_runtime_8520_5_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
