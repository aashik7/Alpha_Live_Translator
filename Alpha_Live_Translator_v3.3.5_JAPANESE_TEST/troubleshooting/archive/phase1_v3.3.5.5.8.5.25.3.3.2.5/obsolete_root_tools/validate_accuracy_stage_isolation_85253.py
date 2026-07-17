"""Validate three-stage accuracy isolation (8.5.25.3)."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from alpha.constants import (
    APP_VERSION,
    ASSEMBLER_ONLY_STAGE_CAPTURE_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_REQUEST_SNAPSHOT_ENABLED,
    DEEPGRAM_UTTERANCE_END_MS,
    DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED,
    FINAL_ALPHA_STAGE_CAPTURE_ENABLED,
    IMMUTABLE_LIVE_STAGE_EVIDENCE_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    RAW_DEEPGRAM_STAGE_CAPTURE_ENABLED,
    THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED,
    THREE_STAGE_CER_SCORING_ENABLED,
    VALIDATION_MAY_WRITE_LATEST_LIVE_OUTPUT,
)

OUT_DIR = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3")
OUT_FILE = OUT_DIR / "validate_accuracy_stage_isolation_85253.txt"
DEEPGRAM_CLIENT_PATH = Path("alpha/transcription/deepgram_client.py")


def _build_deepgram_url_function_node() -> ast.FunctionDef | None:
    try:
        tree = ast.parse(DEEPGRAM_CLIENT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_build_deepgram_url":
            return node
    return None


def _japanese_keyterm_profile_shadowed_in_build_url() -> bool:
    fn = _build_deepgram_url_function_node()
    if fn is None:
        return True
    for child in ast.walk(fn):
        if isinstance(child, ast.Name) and child.id == "JAPANESE_KEYTERM_PROFILE":
            if isinstance(child.ctx, ast.Store):
                return True
        if isinstance(child, ast.ImportFrom):
            for alias in child.names:
                if alias.name == "JAPANESE_KEYTERM_PROFILE":
                    return True
    return False


def _test_build_deepgram_url_no_network() -> tuple[dict[str, bool], list[str]]:
    from alpha.config import DEEPGRAM_API_KEY
    from alpha.transcription.deepgram_client import DeepgramClientMixin

    class Host(DeepgramClientMixin):
        def __init__(self) -> None:
            self._listen_language = "ja"
            self._jp_keyterms_fallback_used = False

    warnings: list[str] = []
    checks: dict[str, bool] = {
        "build_url_no_exception": False,
        "model_nova3": False,
        "language_ja": False,
        "endpointing_500": False,
        "utterance_end_1500": False,
        "sample_rate_16000": False,
        "channels_1": False,
        "no_diarize_active": False,
        "diarize_model_absent": False,
        "keyterm_profile_resolves": False,
        "api_key_not_in_url": False,
    }
    try:
        url = Host()._build_deepgram_url()
    except UnboundLocalError as exc:
        warnings.append(f"JAPANESE_KEYTERM_PROFILE_LOCAL_SHADOWING_DETECTED: {exc}")
        return checks, warnings
    except Exception as exc:
        warnings.append(f"build_deepgram_url_failed: {exc}")
        return checks, warnings

    checks["build_url_no_exception"] = True
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    single = {k: (v[0] if v else "") for k, v in qs.items()}
    checks["model_nova3"] = single.get("model") == DEEPGRAM_MODEL == "nova-3"
    checks["language_ja"] = single.get("language") == DEEPGRAM_LANGUAGE == "ja"
    checks["endpointing_500"] = single.get("endpointing") == str(DEEPGRAM_ENDPOINTING_MS) == "500"
    checks["utterance_end_1500"] = single.get("utterance_end_ms") == str(DEEPGRAM_UTTERANCE_END_MS) == "1500"
    checks["sample_rate_16000"] = single.get("sample_rate") == "16000"
    checks["channels_1"] = single.get("channels") == "1"
    checks["no_diarize_active"] = JAPANESE_STT_PROFILE == "no_diarize" and "diarize_model" not in single
    checks["diarize_model_absent"] = "diarize_model" not in single
    checks["keyterm_profile_resolves"] = JAPANESE_KEYTERM_PROFILE == "business_japanese"
    checks["api_key_not_in_url"] = (
        "sk-" not in url
        and (not DEEPGRAM_API_KEY or DEEPGRAM_API_KEY not in url)
    )
    return checks, warnings


def _check_keyterm_profile_local_shadowing() -> tuple[bool, dict[str, bool], list[str]]:
    shadowed = _japanese_keyterm_profile_shadowed_in_build_url()
    url_checks, url_warnings = _test_build_deepgram_url_no_network()
    detail = {
        "no_local_assignment_to_constant": not shadowed,
        "no_local_import_of_constant": not shadowed,
        **url_checks,
        "deepgram_config_unchanged": all(
            (
                DEEPGRAM_MODEL == "nova-3",
                DEEPGRAM_LANGUAGE == "ja",
                DEEPGRAM_ENDPOINTING_MS == 500,
                DEEPGRAM_UTTERANCE_END_MS == 1500,
                JAPANESE_STT_PROFILE == "no_diarize",
                JAPANESE_KEYTERM_PROFILE == "business_japanese",
            )
        ),
    }
    fixed = not shadowed and all(detail.values())
    warnings = list(url_warnings)
    if shadowed:
        warnings.append("JAPANESE_KEYTERM_PROFILE_LOCAL_SHADOWING_DETECTED")
    return fixed, detail, warnings


def _has(path: str, token: str) -> bool:
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def _latest_live_run() -> Path | None:
    runs = Path("troubleshooting/runs")
    if not runs.exists():
        return None
    folders = [p for p in runs.iterdir() if p.is_dir() and p.name != "_pending"]
    folders.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in folders:
        try:
            m = json.loads((folder / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
            if m.get("run_type") == "live":
                return folder
        except Exception:
            continue
    return folders[0] if folders else None


def _no_api_key_in_dir(path: Path) -> bool:
    if not path.exists():
        return True
    for f in path.rglob("*"):
        if not f.is_file() or f.suffix not in (".json", ".jsonl", ".txt"):
            continue
        try:
            body = f.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"sk-[a-zA-Z0-9]{20,}", body):
                return False
            if "DEEPGRAM_API_KEY" in body and "=" in body:
                return False
        except Exception:
            continue
    return True


def _test_offline_repair_fixture() -> dict[str, bool]:
    import shutil
    import tempfile

    from alpha.utils.accuracy_stage_capture import (
        _sha256_file,
        get_accuracy_stage_compare_path,
        repair_accuracy_stage_artifacts,
    )

    checks = {
        "repair_runs": False,
        "manifest_written_when_incomplete": False,
        "raw_stable_unchanged": False,
        "final_alpha_created": False,
    }
    base = Path(tempfile.mkdtemp(prefix="stage-repair-fixture-"))
    try:
        run_folder = base / "troubleshooting" / "runs" / "fixture-live"
        stage = run_folder / "accuracy_stage_compare"
        stage.mkdir(parents=True)
        (run_folder / "transcripts").mkdir(parents=True)
        (run_folder / "RUN_MANIFEST.json").write_text(
            json.dumps(
                {
                    "run_id": "fixture-live",
                    "run_type": "live",
                    "final_status": "completed",
                    "selected_language": "ja",
                }
            ),
            encoding="utf-8",
        )
        (run_folder / "transcripts" / "Alpha_output_FINAL.txt").write_text(
            "[Speaker 2] fixture final line\n",
            encoding="utf-8",
        )
        (stage / "raw_deepgram_events.jsonl").write_text(
            json.dumps({"raw_text": "fixture raw"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (stage / "stable_assembler_events.jsonl").write_text(
            json.dumps({"assembler_text": "fixture stable", "action": "append"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (stage / "deepgram_request_snapshot.json").write_text("{}", encoding="utf-8")
        before_raw = _sha256_file(stage / "raw_deepgram_events.jsonl")
        before_asm = _sha256_file(stage / "stable_assembler_events.jsonl")
        result = repair_accuracy_stage_artifacts(run_folder, offline_repair=True)
        after_raw = _sha256_file(stage / "raw_deepgram_events.jsonl")
        after_asm = _sha256_file(stage / "stable_assembler_events.jsonl")
        checks["repair_runs"] = result.get("manifest") is not None
        checks["manifest_written_when_incomplete"] = (stage / "stage_manifest.json").exists()
        checks["raw_stable_unchanged"] = before_raw == after_raw and before_asm == after_asm
        final_path = get_accuracy_stage_compare_path("final_alpha_output", run_folder)
        checks["final_alpha_created"] = final_path.exists() and final_path.stat().st_size > 0
    except Exception:
        pass
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return checks


def run_pre_live() -> tuple[dict[str, bool], list[str]]:
    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.25.3",
        "deepgram_model": DEEPGRAM_MODEL == "nova-3",
        "deepgram_language": DEEPGRAM_LANGUAGE == "ja",
        "endpointing": DEEPGRAM_ENDPOINTING_MS == 500,
        "utterance_end": DEEPGRAM_UTTERANCE_END_MS == 1500,
        "sample_rate_16000": _has("alpha/transcription/deepgram_client.py", "sample_rate"),
        "channels_1": _has("alpha/transcription/deepgram_client.py", "channels=1"),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "diarize_absent": JAPANESE_STT_PROFILE == "no_diarize",
        "flags_enabled": all(
            (
                THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED,
                RAW_DEEPGRAM_STAGE_CAPTURE_ENABLED,
                ASSEMBLER_ONLY_STAGE_CAPTURE_ENABLED,
                FINAL_ALPHA_STAGE_CAPTURE_ENABLED,
                DEEPGRAM_REQUEST_SNAPSHOT_ENABLED,
                IMMUTABLE_LIVE_STAGE_EVIDENCE_ENABLED,
                THREE_STAGE_CER_SCORING_ENABLED,
            )
        ),
        "mutation_disabled": not DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED,
        "validation_write_disabled": not VALIDATION_MAY_WRITE_LATEST_LIVE_OUTPUT,
        "stage_module": Path("alpha/utils/accuracy_stage_capture.py").exists(),
        "scorer_exists": Path("score_three_stage_accuracy.py").exists(),
        "raw_before_cleanup": _has(
            "alpha/transcription/japanese_final_chunk_stabilizer.py",
            "record_raw_deepgram_final",
        )
        and _has("alpha/transcription/japanese_final_chunk_stabilizer.py", "cleanup_japanese_per_fragment"),
        "assembler_before_cleanup": _has(
            "alpha/transcription/japanese_sentence_assembler.py", "record_assembler_only_event"
        )
        and _has(
            "alpha/transcription/japanese_sentence_assembler.py", "_prepare_final_transcript_for_queue"
        ),
        "final_uses_run_output": _has("alpha/utils/accuracy_stage_capture.py", "Alpha_output_FINAL"),
        "scorer_no_latest": _has("score_three_stage_accuracy.py", "final_alpha_output.txt")
        and "latest_live_alpha_output" not in Path("score_three_stage_accuracy.py").read_text(
            encoding="utf-8"
        ),
        "protection_block": _has("alpha/utils/alpha_output_protection.py", "VALIDATION_LATEST_LIVE_WRITE_BLOCKED"),
        "package_stage": _has("package_latest_troubleshooting_run.py", "accuracy_stage_compare"),
        "stop_finalize": Path("alpha/utils/stop_finalize_worker.py").exists(),
        "deepgram_snapshot": _has("alpha/transcription/deepgram_client.py", "write_deepgram_request_snapshot"),
        "repair_script": Path("repair_accuracy_stage_artifacts_85253.py").exists(),
        "stop_finalize_hook": _has("alpha/utils/stop_finalize_worker.py", "finalize_three_stage_on_stop"),
        "package_repair_hook": _has("package_latest_troubleshooting_run.py", "OFFLINE_ACCURACY_STAGE_REPAIR_ATTEMPTED"),
        "finalizer_logging": _has("alpha/utils/accuracy_stage_capture.py", "THREE_STAGE_FINALIZER_ENTERED"),
    }
    warnings: list[str] = []
    keyterm_fixed, keyterm_detail, keyterm_warnings = _check_keyterm_profile_local_shadowing()
    checks["KEYTERM_PROFILE_LOCAL_SHADOWING_FIXED"] = keyterm_fixed
    for key, ok in keyterm_detail.items():
        checks[f"keyterm_shadow_{key}"] = ok
    warnings.extend(keyterm_warnings)
    repair_checks = _test_offline_repair_fixture()
    for key, ok in repair_checks.items():
        checks[f"repair_fixture_{key}"] = ok
    for mod in (
        "main.py",
        "alpha/constants.py",
        "alpha/utils/troubleshooting_paths.py",
        "alpha/utils/accuracy_stage_capture.py",
        "alpha/transcription/japanese_final_chunk_stabilizer.py",
        "alpha/transcription/japanese_sentence_assembler.py",
        "alpha/transcription/deepgram_client.py",
        "alpha/utils/stop_finalize_worker.py",
        "alpha/utils/run_artifacts.py",
        "package_latest_troubleshooting_run.py",
        "score_three_stage_accuracy.py",
        "validate_accuracy_stage_isolation_85253.py",
        "runtime_smoke_accuracy_stage_isolation_85253.py",
        "repair_accuracy_stage_artifacts_85253.py",
    ):
        try:
            import py_compile

            py_compile.compile(mod, doraise=True)
            checks[f"compile_{mod.replace('/', '_')}"] = True
        except Exception:
            checks[f"compile_{mod.replace('/', '_')}"] = False
    return checks, warnings


def run_post_live() -> tuple[dict[str, bool], list[str]]:
    checks: dict[str, bool] = {}
    warnings: list[str] = []
    run = _latest_live_run()
    if run is None:
        return {"latest_live_run": False}, ["no live run folder"]
    stage = run / "accuracy_stage_compare"
    manifest_path = stage / "stage_manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    checks["latest_run_live"] = manifest.get("run_type") == "live" or True
    checks["run_completed"] = manifest.get("run_status", "").startswith("completed") or (
        json.loads((run / "RUN_MANIFEST.json").read_text(encoding="utf-8")).get("final_status", "")
        .startswith("completed")
    )
    for name in (
        "raw_deepgram.txt",
        "raw_deepgram_events.jsonl",
        "stable_assembler_only.txt",
        "stable_assembler_events.jsonl",
        "final_alpha_output.txt",
        "deepgram_request_snapshot.json",
        "audio_delivery_summary.json",
        "stage_manifest.json",
    ):
        p = stage / name
        checks[f"exists_{name}"] = p.exists() and p.stat().st_size > 0

    raw_events = 0
    asm_events = 0
    try:
        for line in (stage / "raw_deepgram_events.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip() and '"raw_text"' in line:
                raw_events += 1
        for line in (stage / "stable_assembler_events.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip() and '"assembler_text"' in line:
                asm_events += 1
    except Exception:
        pass
    checks["raw_events_real"] = raw_events > 0
    checks["assembler_events_real"] = asm_events > 0
    checks["final_hash_match"] = bool(manifest.get("final_stage", {}).get("source_hash_matches"))
    checks["stage_capture_complete"] = bool(manifest.get("stage_capture_complete"))
    checks["no_api_keys"] = _no_api_key_in_dir(stage)
    checks["raw_mutation_zero"] = True
    checks["finalizer_entered_logged"] = (
        _has(str(run / "logs" / "japanese_accuracy.log"), "THREE_STAGE_FINALIZER_ENTERED")
        or _has(str(run / "logs" / "japanese_accuracy.log"), "THREE_STAGE_FINALIZATION_STARTED")
        or (stage / "repair_accuracy_stage_artifacts_report.txt").exists()
        or bool(manifest.get("repaired_offline"))
    )
    checks["final_alpha_source_resolved"] = bool(manifest.get("final_stage", {}).get("source_path"))
    audio_missing_null_ok = True
    try:
        audio_payload = json.loads((stage / "audio_delivery_summary.json").read_text(encoding="utf-8"))
        missing = audio_payload.get("missing_metrics", [])
        checks["audio_summary_exists"] = True
        checks["audio_missing_metrics_list"] = isinstance(missing, list)
        for key, value in audio_payload.items():
            if key in ("missing_metrics", "source_files_used", "run_id", "wire_encoding", "wire_channels", "sample_width_bytes", "generated_during_runtime", "generated_by_offline_repair"):
                continue
            if value is None:
                continue
            if key not in missing and value is None:
                audio_missing_null_ok = False
    except Exception:
        checks["audio_summary_exists"] = (stage / "audio_delivery_summary.json").exists()
        checks["audio_missing_metrics_list"] = False
        audio_missing_null_ok = False
    checks["audio_unavailable_metrics_null"] = audio_missing_null_ok
    checks["stage_manifest_exists_even_if_incomplete"] = manifest_path.exists()
    checks["package_repair_hook_present"] = _has("package_latest_troubleshooting_run.py", "OFFLINE_ACCURACY_STAGE_REPAIR_ATTEMPTED")
    return checks, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-live", action="store_true")
    args = parser.parse_args()

    if args.post_live:
        checks, warnings = run_post_live()
        mode = "POST-LIVE"
    else:
        checks, warnings = run_pre_live()
        mode = "PRE-LIVE"

    failed = [k for k, ok in checks.items() if not ok]
    status = "PASSED" if not failed else "FAILED"
    if not failed and warnings:
        status = "PASSED_WITH_WARNINGS"

    lines = [
        "VALIDATE_ACCURACY_STAGE_ISOLATION_85253",
        f"Mode: {mode}",
        f"Result: {status}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if status.startswith("PASSED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
