"""Pre/post-live validation for V25.3.1 revision safety."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    CER_OPERATION_ACCOUNTING_STRICT,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    REVISION_CONTENT_LOSS_GUARD_ENABLED,
    REVISION_LINEAGE_REQUIRED,
    REVISION_TERMINAL_SENTENCE_GUARD_ENABLED,
    RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED,
    SAFE_STABLE_REVISION_ENABLED,
    UNPROVEN_REVISION_DEFAULT_ACTION,
)
from alpha.transcription.stable_revision_decision import decide_stable_revision_action
from alpha.utils.cer_backtracking import levenshtein_operation_counts

VALIDATION_DIR = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3.1")
OUTPUT_PATH = VALIDATION_DIR / "validate_revision_safety_852531.txt"


def _check(name: str, ok: bool, failures: list[str], warnings: list[str], *, warn_only: bool = False) -> None:
    if ok:
        return
    if warn_only:
        warnings.append(name)
    else:
        failures.append(name)


def _latest_live_run(project: Path) -> Path | None:
    runs = project / "troubleshooting" / "runs"
    if not runs.exists():
        return None
    candidates = [p for p in runs.iterdir() if p.is_dir() and (p / "RUN_MANIFEST.json").exists()]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in candidates:
        try:
            manifest = json.loads((folder / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
            if manifest.get("run_type") == "live" and str(manifest.get("final_status", "")).startswith("completed"):
                return folder
        except Exception:
            continue
    return candidates[0] if candidates else None


def _scan_for_api_keys(folder: Path) -> bool:
    patterns = [re.compile(r"sk-[a-zA-Z0-9]{10,}"), re.compile(r"DEEPGRAM_API_KEY\s*=\s*\S+")]
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".wav", ".mp3"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in patterns:
            if pat.search(text):
                return True
    return False


def main() -> int:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent
    failures: list[str] = []
    warnings: list[str] = []
    lines = [f"validate_revision_safety_852531 — {APP_VERSION}", ""]

    _check("version_is_25_3_1", APP_VERSION == "3.3.5.5.8.5.25.3.1", failures, warnings)
    _check("codename_matches", "Safe Revision Semantics" in APP_CODENAME, failures, warnings)
    _check(
        "deepgram_model_unchanged",
        DEEPGRAM_MODEL == "nova-3" and DEEPGRAM_LANGUAGE == "ja",
        failures,
        warnings,
    )
    _check("deepgram_endpointing_500", DEEPGRAM_ENDPOINTING_MS == 500, failures, warnings)
    _check("deepgram_utterance_end_1500", DEEPGRAM_UTTERANCE_END_MS == 1500, failures, warnings)
    _check("safe_stable_revision_enabled", SAFE_STABLE_REVISION_ENABLED, failures, warnings)
    _check("revision_lineage_required", REVISION_LINEAGE_REQUIRED, failures, warnings)
    _check("terminal_sentence_guard", REVISION_TERMINAL_SENTENCE_GUARD_ENABLED, failures, warnings)
    _check("content_loss_guard", REVISION_CONTENT_LOSS_GUARD_ENABLED, failures, warnings)
    _check("default_uncertain_append", UNPROVEN_REVISION_DEFAULT_ACTION == "append", failures, warnings)
    _check("cer_accounting_strict", CER_OPERATION_ACCOUNTING_STRICT, failures, warnings)
    _check("runtime_audio_counters_enabled", RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED, failures, warnings)

    spec = importlib.util.spec_from_file_location(
        "assembler", project / "alpha" / "transcription" / "japanese_sentence_assembler.py"
    )
    assembler_src = (project / "alpha" / "transcription" / "japanese_sentence_assembler.py").read_text(encoding="utf-8")
    _check("central_revision_function_used", "decide_stable_revision_action" in assembler_src, failures, warnings)
    _check("stable_revision_module_exists", (project / "alpha" / "transcription" / "stable_revision_decision.py").exists(), failures, warnings)

    counts = levenshtein_operation_counts("abc", "abd")
    _check("cer_backtracking_valid", counts["edit_distance"] == counts["substitutions"] + counts["deletions"] + counts["insertions"], failures, warnings)

    fixture_path = project / "tests" / "fixtures" / "v25_3_revision_events.json"
    _check("revision_fixture_exists", fixture_path.exists(), failures, warnings)
    if fixture_path.exists():
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        for case in fixture.get("cases", []):
            expected = case.get("expected_safe_action")
            if expected is None:
                continue
            decision = decide_stable_revision_action(
                previous_record=case.get("previous_record"),
                candidate_text=case.get("candidate_text", ""),
                update_previous_requested=bool(case.get("update_previous_requested")),
                candidate_raw_event_ids=case.get("candidate_raw_event_ids"),
                candidate_metadata=case.get("candidate_metadata") or {},
            )
            expected = case.get("expected_safe_action")
            _check(
                f"fixture_{case.get('case_id')}",
                decision.get("action") == expected,
                failures,
                warnings,
            )

    counters_path = project / "alpha" / "utils" / "runtime_audio_counters.py"
    _check("runtime_audio_counters_module", counters_path.exists(), failures, warnings)
    _check("package_script_exists", (project / "package_latest_troubleshooting_run.py").exists(), failures, warnings)

    replay_report = VALIDATION_DIR / "revision_replay_report.json"
    if replay_report.exists():
        replay = json.loads(replay_report.read_text(encoding="utf-8"))
        _check(
            "four_destructive_revisions_rejected",
            int(replay.get("destructive_safe_rejected_count") or 0) >= 4,
            failures,
            warnings,
        )
        if replay.get("scoring"):
            recon_acc = replay["scoring"]["reconstructed"].get("accuracy_percent")
            raw_acc = replay["scoring"].get("raw_deepgram", {}).get("accuracy_percent")
            if recon_acc is not None and raw_acc is not None:
                _check("reconstructed_near_raw", recon_acc >= raw_acc - 2.0, failures, warnings, warn_only=True)

    run_folder = _latest_live_run(project)
    if run_folder and APP_VERSION.endswith(".1"):
        manifest_path = run_folder / "accuracy_stage_compare" / "stage_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("app_version") == APP_VERSION:
                _check("post_live_run_completed", manifest.get("run_status") == "completed", failures, warnings, warn_only=True)
                _check(
                    "post_live_destructive_revision_zero",
                    int(manifest.get("destructive_revision_count") or 0) == 0,
                    failures,
                    warnings,
                    warn_only=True,
                )
                audio_path = run_folder / "accuracy_stage_compare" / "audio_delivery_summary.json"
                if audio_path.exists():
                    audio = json.loads(audio_path.read_text(encoding="utf-8"))
                    for field in (
                        "audio_chunks_sent",
                        "audio_bytes_sent",
                        "calculated_audio_seconds_sent",
                        "audio_queue_overflow_count",
                        "audio_chunk_drop_count",
                        "deepgram_send_errors",
                    ):
                        _check(
                            f"post_live_audio_{field}",
                            audio.get(field) is not None,
                            failures,
                            warnings,
                            warn_only=True,
                        )
                _check("post_live_no_api_keys", not _scan_for_api_keys(run_folder), failures, warnings, warn_only=True)

    status = "PASSED"
    if failures:
        status = "FAILED"
    elif warnings:
        status = "PASSED_WITH_WARNINGS"

    lines.append(f"Status: {status}")
    lines.append(f"Failures: {failures or 'none'}")
    lines.append(f"Warnings: {warnings or 'none'}")
    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    print(status)
    return 0 if status != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
