"""V3.3.5.5.8.5.20.4 STOP FINALIZE RECOVERY VALIDATION."""

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
    SINGLE_RUN_IDENTITY_LOCK_ENABLED,
    STOP_FINALIZE_TWO_PHASE_MODE,
)


def _has(path: str, token: str) -> bool:
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def main() -> int:
    checks: list[tuple[str, bool]] = []
    checks.append(("app_version", APP_VERSION == "3.3.5.5.8.5.20.4"))
    checks.append(("deepgram_unchanged", DEEPGRAM_MODEL == "nova-3" and DEEPGRAM_LANGUAGE == "ja" and DEEPGRAM_ENDPOINTING_MS == 500 and DEEPGRAM_UTTERANCE_END_MS == 1500))
    checks.append(("japanese_profile_unchanged", JAPANESE_STT_PROFILE == "no_diarize" and JAPANESE_KEYTERM_PROFILE == "business_japanese"))
    checks.append(("start_checkpoints", _has("alpha/ui/main_window.py", "START_LISTENING_STATE_TRUE")))
    checks.append(("stop_core_phase", _has("alpha/utils/stop_finalize_worker.py", "STOP_CORE_PHASE_BEGIN")))
    checks.append(("evidence_worker", _has("alpha/utils/stop_finalize_worker.py", "EVIDENCE_PACKAGE_WORKER_STARTED")))
    checks.append(("consistency_not_core", _has("alpha/utils/stop_finalize_worker.py", "RUN_CONSISTENCY_CHECK_DEFERRED_TO_EVIDENCE_WORKER")))
    checks.append(("index_non_blocking", _has("alpha/utils/stop_finalize_worker.py", "RUN_ARTIFACTS_INDEX_DEFERRED_AFTER_TIMEOUT")))
    checks.append(("hard_timeout_wrapper", _has("alpha/utils/stop_finalize_worker.py", "run_timed_step(")))
    checks.append(("single_identity_lock", SINGLE_RUN_IDENTITY_LOCK_ENABLED and STOP_FINALIZE_TWO_PHASE_MODE))
    checks.append(("create_once_exists", _has("alpha/utils/run_identity.py", "create_run_identity_once")))
    checks.append(("artifact_uses_locked_id", _has("alpha/utils/run_artifacts.py", "create_run_identity_once(")))
    checks.append(("run_id_validation_exists", _has("alpha/utils/run_identity.py", "validate_all_artifacts_use_same_run_id")))
    checks.append(("stop_ui_restore_event", _has("alpha/utils/stop_finalize_worker.py", "STOP_UI_STATE_RESTORED")))
    checks.append(("evidence_fail_warning", _has("alpha/utils/stop_finalize_worker.py", "EVIDENCE_PACKAGE_FAILED_NON_BLOCKING")))
    checks.append(("stop_worker_no_tk", _has("alpha/utils/stop_finalize_worker.py", "STOP_WORKER_NO_TK_CALL_CONFIRMED")))
    checks.append(("evidence_worker_no_tk", _has("alpha/utils/stop_finalize_worker.py", "EVIDENCE_WORKER_NO_TK_CALL_CONFIRMED")))
    checks.append(("app_close_non_blocking", _has("alpha/ui/main_window.py", "APP_CLOSE_DURING_EVIDENCE_PACKAGE")))
    checks.append(("raw_deepgram_preserved", _has("alpha/constants.py", "DEEPGRAM_LANGUAGE = \"ja\"")))
    checks.append(("deepl_groq_meetingbaas_not_active", not _has("alpha/constants.py", "DeepL") and not _has("alpha/constants.py", "Groq") and not _has("alpha/constants.py", "MeetingBaaS")))

    failed = [k for k, ok in checks if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = ["V3.3.5.5.8.5.20.4 STOP FINALIZE RECOVERY VALIDATION", f"Result: {result}"]
    if failed:
        lines.append("Failed checks: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_8520_4_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
