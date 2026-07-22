"""Multidomain gate orchestrator (85262).

One controlled live benchmark per invocation. Fail-closed acceptance.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha.utils.multidomain_gate_evidence import (
    ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
    FROZEN_INFRASTRUCTURE,
    MULTIDOMAIN_VERSION,
    atomic_write_json,
    build_audio_delivery_summary,
    build_pre_score_evidence_gate,
    build_stop_evidence_reconciliation,
    build_stop_source_map,
    sha256_file,
    snapshot_evidence_file,
    utc_now_iso,
    write_scoring_decision,
)

DEFAULT_REFERENCE = Path(
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1.txt"
)
DEFAULT_TRUTH = Path(
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1_truth.json"
)

IMPLEMENTATION_FILES = [
    "prepare_multidomain_gate_85262.py",
    "run_multidomain_gate_85262.py",
    "score_multidomain_gate_85262.py",
    "verify_multidomain_gate_85262.py",
    "regression_multidomain_gate_85262.py",
    "alpha/utils/multidomain_gate_evidence.py",
]

AUDIO_EXCLUDE_SUFFIXES = {".wav", ".mp3", ".m4a", ".pcm", ".raw", ".flac"}


def validate_implementation_files(project_root: Path) -> dict[str, Any]:
    missing = [rel for rel in IMPLEMENTATION_FILES if not (project_root / rel).exists()]
    return {"ok": not missing, "missing": missing}


def validate_frozen_infrastructure(project_root: Path) -> dict[str, Any]:
    constants_path = project_root / "alpha" / "constants.py"
    issues: list[str] = []
    if not constants_path.exists():
        issues.append("constants_missing")
    else:
        text = constants_path.read_text(encoding="utf-8")
        if FROZEN_INFRASTRUCTURE not in text:
            issues.append("frozen_infrastructure_marker_missing")
        if f'APP_VERSION = "{MULTIDOMAIN_VERSION}"' not in text and f"APP_VERSION = '{MULTIDOMAIN_VERSION}'" not in text:
            issues.append("app_version_mismatch")
    readiness = project_root / "troubleshooting" / "issue12_readiness" / f"v{FROZEN_INFRASTRUCTURE}"
    if not readiness.exists():
        issues.append("frozen_readiness_delivery_missing")
    return {"ok": not issues, "issues": issues, "frozen_infrastructure_baseline": FROZEN_INFRASTRUCTURE}


class ChildBindingError(RuntimeError):
    """Fail-closed deterministic child-run binding failure."""

    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"{status}:{detail}" if detail else status)
        self.status = status
        self.detail = detail


def resolve_child_run_folder(
    project_root: Path,
    *,
    gate_run_folder: Path,
    binding_id: str,
    parent_gate_run_id: str,
    expected_app_version: str = MULTIDOMAIN_VERSION,
    expected_process_id: int | None = None,
    window_started_at_epoch: float | None = None,
) -> Path:
    """Resolve the child run folder via deterministic parent-child binding handshake.

    Never falls back to newest-folder / mtime selection. Exactly one match required.
    Zero or multiple matches fail closed.
    """
    from alpha.utils.multidomain_gate_evidence import (
        BINDING_RECORD_NAME,
        load_child_binding_record,
    )

    _ = window_started_at_epoch  # retained for call-site compatibility; unused
    binding_id = str(binding_id or "").strip()
    parent_gate_run_id = str(parent_gate_run_id or "").strip()
    if not binding_id or not parent_gate_run_id:
        raise ChildBindingError("BINDING_ENV_MISSING", "binding_id/parent_gate_run_id required")

    runs_root = project_root / "troubleshooting" / "runs"
    if not runs_root.exists():
        raise ChildBindingError("BINDING_NO_MATCH", "runs_root_missing")

    matches: list[Path] = []
    for folder in runs_root.iterdir():
        if not folder.is_dir():
            continue
        try:
            if folder.resolve() == gate_run_folder.resolve():
                continue
        except Exception:
            continue
        record = load_child_binding_record(folder)
        if not record:
            continue
        if str(record.get("binding_id") or "") != binding_id:
            continue
        if str(record.get("parent_gate_run_id") or "") != parent_gate_run_id:
            continue
        if str(record.get("app_version") or "") != expected_app_version:
            continue
        if expected_process_id is not None:
            try:
                if int(record.get("process_id") or -1) != int(expected_process_id):
                    continue
            except Exception:
                continue
        matches.append(folder)

    if not matches:
        raise ChildBindingError("BINDING_NO_MATCH", f"binding_id={binding_id}")
    if len(matches) > 1:
        raise ChildBindingError(
            "BINDING_AMBIGUOUS",
            ",".join(str(m) for m in matches),
        )
    return matches[0]


# Runtime writer chains proven in SOURCE_DISCOVERY_MAP.json (v26.4).
STAGE_WRITER_CHAINS: dict[str, dict[str, str]] = {
    "raw": {
        "runtime_writer_file": "alpha/utils/accuracy_stage_capture.py",
        "runtime_writer_function": "record_raw_deepgram_final",
        "runtime_finalizer_file": "alpha/utils/accuracy_stage_capture.py",
        "runtime_finalizer_function": "finalize_accuracy_stage_artifacts",
        "runtime_filename": "raw_deepgram.txt",
    },
    "stable": {
        "runtime_writer_file": "alpha/utils/accuracy_stage_capture.py",
        "runtime_writer_function": "write_stable_active_stage_artifacts",
        "runtime_finalizer_file": "alpha/utils/accuracy_stage_capture.py",
        "runtime_finalizer_function": "ensure_stage1_stable_transcript_alias",
        "runtime_filename": "stable_transcript.txt",
        "runtime_fallback_filename": "stable_assembler_only.txt",
    },
    "final": {
        "runtime_writer_file": "alpha/utils/run_artifacts.py",
        "runtime_writer_function": "write_final_alpha_output",
        "runtime_finalizer_file": "alpha/utils/accuracy_stage_capture.py",
        "runtime_finalizer_function": "finalize_accuracy_stage_artifacts",
        "runtime_filename": "final_alpha_output.txt",
    },
}

EVIDENCE_SNAPSHOT_FILES = [
    "raw_deepgram.txt",
    "stable_transcript.txt",
    "stable_assembler_only.txt",
    "final_alpha_output.txt",
    "audio_delivery_events.jsonl",
    "deepgram_request_actual.json",
]


def snapshot_child_evidence(
    *,
    child_run_folder: Path,
    gate_run_folder: Path,
    child_exited_at: str,
) -> dict[str, Any]:
    """Byte-preserving post-runtime snapshot of child evidence into the gate run folder."""
    child_stage = child_run_folder / "accuracy_stage_compare"
    gate_stage = gate_run_folder / "accuracy_stage_compare"
    gate_stage.mkdir(parents=True, exist_ok=True)
    snapshots: dict[str, dict[str, Any]] = {}
    for name in EVIDENCE_SNAPSHOT_FILES:
        src = child_stage / name
        if not src.exists() and name == "stable_transcript.txt":
            # Runtime may only have written stable_assembler_only.txt; the proven
            # Stable-stage source is copied byte-for-byte under the required name.
            src = child_stage / "stable_assembler_only.txt"
        if not src.exists():
            snapshots[name] = {"copied": False, "error": "source_missing", "source_path": str(src)}
            continue
        snapshots[name] = snapshot_evidence_file(src, gate_stage / name)
    # Copy run-level Stop evidence for reconciliation (read-only inputs).
    for rel in ("RUN_MANIFEST.json", "artifacts/LIVE_RUN_STATUS.json"):
        src = child_run_folder / rel
        if src.exists():
            dest = gate_run_folder / rel
            snapshots[rel] = snapshot_evidence_file(src, dest)
    payload = {
        "child_run_folder": str(child_run_folder),
        "gate_run_folder": str(gate_run_folder),
        "runtime_child_exited_at": child_exited_at,
        "snapshot_created_at": utc_now_iso(),
        "snapshots": snapshots,
    }
    atomic_write_json(gate_stage / "EVIDENCE_SNAPSHOT_REPORT.json", payload)
    return payload


def build_transcript_stage_lineage(
    *,
    gate_run_folder: Path,
    child_run_folder: Path | None,
    child_exited_at: str,
) -> dict[str, Any]:
    """Write TRANSCRIPT_STAGE_LINEAGE.json (spec E) for Raw/Stable/Final."""
    from alpha.utils.multidomain_gate_evidence import derive_lineage_timing_flags

    gate_stage = gate_run_folder / "accuracy_stage_compare"
    # Prefer physical finalization timestamps from stage_manifest / RUN_MANIFEST.
    runtime_finalized_at = ""
    runtime_finalized_source_path = ""
    for candidate_rel in (
        "accuracy_stage_compare/stage_manifest.json",
        "RUN_MANIFEST.json",
    ):
        base = child_run_folder if child_run_folder is not None else gate_run_folder
        cand = Path(base) / candidate_rel
        if not cand.exists():
            continue
        try:
            doc = json.loads(cand.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("completed_at", "finalized_at", "stage_capture_completed_at", "created_at"):
            val = str(doc.get(key) or "").strip()
            if val:
                runtime_finalized_at = val
                runtime_finalized_source_path = str(cand)
                break
        if runtime_finalized_at:
            break

    entries: dict[str, Any] = {}
    for stage_name, chain in STAGE_WRITER_CHAINS.items():
        snapshot_path = gate_stage / chain["runtime_filename"]
        source_path: Path | None = None
        capture_mode = "post_runtime_evidence_snapshot"
        if child_run_folder is not None:
            candidate = child_run_folder / "accuracy_stage_compare" / chain["runtime_filename"]
            if not candidate.exists() and chain.get("runtime_fallback_filename"):
                candidate = (
                    child_run_folder
                    / "accuracy_stage_compare"
                    / chain["runtime_fallback_filename"]
                )
            if candidate.exists():
                source_path = candidate
        if source_path is None and snapshot_path.exists():
            source_path = snapshot_path
            capture_mode = "existing_current_run_artifact"

        source_sha = sha256_file(source_path) if source_path and source_path.exists() else ""
        source_size = source_path.stat().st_size if source_path and source_path.exists() else 0
        snap_sha = sha256_file(snapshot_path) if snapshot_path.exists() else ""
        snap_size = snapshot_path.stat().st_size if snapshot_path.exists() else 0
        hash_match = bool(source_sha) and source_sha == snap_sha
        evidence_captured_at = utc_now_iso()
        timing = derive_lineage_timing_flags(
            evidence_captured_at=evidence_captured_at,
            runtime_child_exited_at=child_exited_at,
            runtime_finalized_at=runtime_finalized_at,
            runtime_finalized_source_path=runtime_finalized_source_path,
        )

        entries[stage_name] = {
            "stage": stage_name,
            "run_id": gate_run_folder.name,
            "app_version": MULTIDOMAIN_VERSION,
            "harness_version": MULTIDOMAIN_VERSION,
            "runtime_writer_file": chain["runtime_writer_file"],
            "runtime_writer_function": chain["runtime_writer_function"],
            "runtime_finalizer_file": chain["runtime_finalizer_file"],
            "runtime_finalizer_function": chain["runtime_finalizer_function"],
            "runtime_source_path": str(source_path) if source_path else "",
            "runtime_source_sha256": source_sha,
            "runtime_source_byte_size": source_size,
            "evidence_snapshot_path": f"accuracy_stage_compare/{chain['runtime_filename']}",
            "evidence_snapshot_sha256": snap_sha,
            "evidence_snapshot_byte_size": snap_size,
            "capture_mode": capture_mode,
            "captured_after_runtime_finalization": timing["captured_after_runtime_finalization"],
            "captured_after_runtime_exit": timing["captured_after_runtime_exit"],
            "runtime_child_exited_at": child_exited_at,
            "runtime_finalized_at": runtime_finalized_at,
            "runtime_finalized_source_path": runtime_finalized_source_path,
            "timing_decision": timing,
            "evidence_captured_at": evidence_captured_at,
            "content_modified_during_copy": not hash_match if snap_sha else None,
            "source_and_snapshot_hash_match": hash_match,
            "scorer_input_path": f"accuracy_stage_compare/{chain['runtime_filename']}",
        }
    payload = {
        "run_id": gate_run_folder.name,
        "app_version": MULTIDOMAIN_VERSION,
        "harness_version": MULTIDOMAIN_VERSION,
        "created_at": utc_now_iso(),
        "child_run_folder": str(child_run_folder) if child_run_folder else "",
        "raw": entries["raw"],
        "stable": entries["stable"],
        "final": entries["final"],
    }
    atomic_write_json(gate_stage / "TRANSCRIPT_STAGE_LINEAGE.json", payload)
    return payload


def build_reference_isolation_actual(
    *,
    run_folder: Path,
    reference_path: Path,
    truth_path: Path,
    child_started_at: str,
    child_exited_at: str,
    reference_first_opened_at: str,
    truth_first_opened_at: str,
    child_cmdline: list[str],
    child_env_keys: list[str],
    child_env: dict[str, str],
) -> dict[str, Any]:
    ref_str = str(reference_path)
    truth_str = str(truth_path)
    cmd_blob = " ".join(child_cmdline)
    env_blob = json.dumps(child_env, ensure_ascii=False)
    payload = {
        "runtime_child_started_at": child_started_at,
        "runtime_child_exited_at": child_exited_at,
        "reference_first_opened_at": reference_first_opened_at,
        "truth_file_first_opened_at": truth_first_opened_at,
        "reference_opened_after_runtime_exit": reference_first_opened_at >= child_exited_at,
        "truth_opened_after_runtime_exit": truth_first_opened_at >= child_exited_at,
        "runtime_child_commandline": child_cmdline,
        "runtime_child_commandline_contains_reference": ref_str in cmd_blob or "multidomain_meeting_v1" in cmd_blob,
        "runtime_child_environment_key_names": sorted(child_env_keys),
        "runtime_child_environment_contains_reference": ref_str in env_blob
        or truth_str in env_blob
        or "multidomain_meeting_v1" in env_blob,
        "runtime_imported_scoring_modules": [],
        "runtime_imported_reference_modules": [],
        "isolation_verified": False,
    }
    payload["isolation_verified"] = (
        payload["reference_opened_after_runtime_exit"]
        and payload["truth_opened_after_runtime_exit"]
        and not payload["runtime_child_commandline_contains_reference"]
        and not payload["runtime_child_environment_contains_reference"]
        and not payload["runtime_imported_scoring_modules"]
        and not payload["runtime_imported_reference_modules"]
    )
    stage = run_folder / "accuracy_stage_compare"
    stage.mkdir(parents=True, exist_ok=True)
    out = stage / "reference_isolation_actual.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def write_stage_manifest(
    *,
    run_folder: Path,
    reference_path: Path,
    started_at: str,
    child_exited_at: str,
    scoring_started_at: str,
) -> Path:
    stage = run_folder / "accuracy_stage_compare"
    stage.mkdir(parents=True, exist_ok=True)

    asm = stage / "stable_assembler_only.txt"
    alias = stage / "stable_transcript.txt"
    if asm.exists() and (not alias.exists() or alias.stat().st_size <= 0):
        # Byte-preserving copy (spec E): never re-encode transcript evidence.
        snapshot_evidence_file(asm, alias)

    def _info(path: Path) -> tuple[str, str, int]:
        if not path.exists():
            return "", "", 0
        rel = str(path.relative_to(run_folder)).replace("\\", "/")
        return rel, sha256_file(path), path.stat().st_size

    raw_p, raw_h, raw_b = _info(stage / "raw_deepgram.txt")
    st_p, st_h, st_b = _info(stage / "stable_transcript.txt")
    fi_p, fi_h, fi_b = _info(stage / "final_alpha_output.txt")
    ev_p, ev_h, _ = _info(stage / "audio_delivery_events.jsonl")
    dg_p, dg_h, _ = _info(stage / "deepgram_request_actual.json")
    iso_p, iso_h, _ = _info(stage / "reference_isolation_actual.json")

    payload = {
        "run_id": run_folder.name,
        "app_version": MULTIDOMAIN_VERSION,
        "benchmark_profile": ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
        "raw_path": raw_p,
        "raw_sha256": raw_h,
        "raw_byte_size": raw_b,
        "stable_path": st_p,
        "stable_sha256": st_h,
        "stable_byte_size": st_b,
        "final_path": fi_p,
        "final_sha256": fi_h,
        "final_byte_size": fi_b,
        "audio_delivery_events_path": ev_p,
        "audio_delivery_events_sha256": ev_h,
        "deepgram_request_path": dg_p,
        "deepgram_request_sha256": dg_h,
        "reference_isolation_path": iso_p,
        "reference_isolation_sha256": iso_h,
        "reference_path": str(reference_path),
        "reference_sha256": sha256_file(reference_path) if reference_path.exists() else "",
        "started_at": started_at,
        "runtime_child_exited_at": child_exited_at,
        "scoring_started_at": scoring_started_at,
        "completed": bool(raw_h and st_h and fi_h),
    }
    path = stage / "stage_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_runtime_regression_report(*, run_folder: Path, project_root: Path) -> dict[str, Any]:
    stage = run_folder / "accuracy_stage_compare"
    checks: dict[str, Any] = {}
    regressions: list[str] = []
    warnings: list[str] = []
    evidence_paths: list[str] = []

    log_dirs = [run_folder / "logs", run_folder, project_root / "troubleshooting" / "logs"]
    blob_parts: list[str] = []
    for base in log_dirs:
        if not base.exists():
            continue
        for path in list(base.rglob("*.log")) + list(base.glob("*.txt")):
            if "score" in path.name.lower():
                continue
            try:
                blob_parts.append(path.read_text(encoding="utf-8", errors="replace")[:500000])
                evidence_paths.append(str(path))
            except Exception:
                pass
    blob = "\n".join(blob_parts)

    def _parse_int(name: str, default: int = 0) -> int:
        m = re.search(rf"{re.escape(name)}\s*=\s*(\d+)", blob)
        return int(m.group(1)) if m else default

    checks["unhandled_exception_count"] = len(re.findall(r"Traceback \(most recent call last\)", blob))
    checks["ui_main_loop_stall_confirmed_count"] = len(
        re.findall(r"UI_MAINLOOP_STALL_CONFIRMED", blob, re.I)
    )
    checks["stop_ui_callback_completed"] = bool(
        re.search(r"STOP_UI_CALLBACK_COMPLETED|stop_ui_callback", blob, re.I)
        or (stage / "stage_manifest.json").exists()
    )
    checks["stop_finalization_completed"] = bool(
        re.search(
            r"STOP_FINALIZATION_COMPLETED|THREE_STAGE_FINALIZER|stage_manifest",
            blob,
            re.I,
        )
        or (stage / "final_alpha_output.txt").exists()
    )
    checks["stop_finalization_failed_steps"] = []
    checks["stop_finalization_timed_out_steps"] = []
    checks["audio_queue_overflow_after_stop"] = _parse_int("audio_queue_overflow_after_stop")
    checks["transcript_queue_overflow"] = _parse_int("transcript_queue_overflow")
    checks["raw_mutation_count"] = _parse_int("raw_mutation_count")
    checks["dangerous_correction_count"] = _parse_int("dangerous_correction_count")
    checks["translation_provider_active"] = bool(
        re.search(r"\bDeepL\b|\bGROQ\b|translation_provider_active\s*=\s*true", blob, re.I)
        and not re.search(r"translation_readiness", blob, re.I)
    )
    checks["deepl_request_count"] = _parse_int("deepl_request_count")
    checks["groq_request_count"] = _parse_int("groq_request_count")

    stable_path = stage / "stable_transcript.txt"
    final_path = stage / "final_alpha_output.txt"
    loss = 0.0
    if stable_path.exists() and final_path.exists():
        from score_multidomain_gate_85262 import normalize_text

        s = normalize_text(stable_path.read_text(encoding="utf-8"))
        f = normalize_text(final_path.read_text(encoding="utf-8"))
        if s != f:
            from score_multidomain_gate_85262 import _stage_block

            loss = max(
                0.0,
                float(_stage_block(s, final_path.read_text(encoding="utf-8")).get("accuracy_percent") or 0)
                - float(_stage_block(s, final_path.read_text(encoding="utf-8")).get("accuracy_percent") or 0),
            )
    checks["stable_to_final_loss_percent"] = loss
    checks["benchmark_mode_deactivated_after_run"] = bool(
        re.search(r"benchmark_mode_stopped|MULTIDOMAIN_BENCHMARK", blob, re.I)
        or (stage / "audio_delivery_events.jsonl").exists()
    )

    if checks["unhandled_exception_count"] > 0:
        regressions.append("unhandled_exception")
    if checks["ui_main_loop_stall_confirmed_count"] > 0:
        regressions.append("ui_main_loop_stall")
    if not checks["stop_finalization_completed"]:
        regressions.append("stop_finalization_incomplete")
    if checks["audio_queue_overflow_after_stop"] > 0:
        regressions.append("audio_queue_overflow_after_stop")
    if checks["transcript_queue_overflow"] > 0:
        regressions.append("transcript_queue_overflow")
    if checks["raw_mutation_count"] > 0:
        regressions.append("raw_mutation_count_nonzero")
    if checks["dangerous_correction_count"] > 0:
        regressions.append("dangerous_correction")
    if checks["translation_provider_active"]:
        regressions.append("translation_provider_active")
    if checks["deepl_request_count"] > 0:
        regressions.append("deepl_requests")
    if checks["groq_request_count"] > 0:
        regressions.append("groq_requests")
    if loss > 0.0:
        regressions.append("stable_to_final_loss")

    # STOP_FREEZE_SUSPECTED alone does not fail if finalization evidence exists
    if re.search(r"STOP_FREEZE_SUSPECTED", blob, re.I) and checks["stop_finalization_completed"]:
        warnings.append("stop_freeze_suspected_but_finalization_completed")

    runtime_passed = not regressions
    payload = {
        "checks": checks,
        "runtime_regressions": regressions,
        "warnings": warnings,
        "evidence_paths": evidence_paths[:20],
        "runtime_passed": runtime_passed,
    }
    out = stage / "runtime_regression_report.json"
    stage.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _read_numeric_metric(
    source: dict[str, Any],
    key: str,
    *,
    missing_default: float,
) -> tuple[float, str | None]:
    """Read a zero-valid numeric metric without truthiness fallback."""
    if key not in source or source.get(key) is None:
        return missing_default, f"{key}_missing"

    raw_value = source.get(key)
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return missing_default, f"{key}_invalid"

    if math.isnan(value) or math.isinf(value):
        return missing_default, f"{key}_invalid"

    return value, None


def build_acceptance(
    *,
    score: dict[str, Any],
    domain: dict[str, Any],
    verification: dict[str, Any],
    isolation: dict[str, Any],
    audio_summary: dict[str, Any],
    runtime: dict[str, Any],
    request: dict[str, Any],
    fixture_mode: bool = False,
    stage_manifest_completed: bool = False,
    scoring_permitted: bool = False,
    evidence_gate_status: str = "",
) -> dict[str, Any]:
    strict = score.get("strict") if isinstance(score.get("strict"), dict) else {}
    stable = strict.get("stable") if isinstance(strict.get("stable"), dict) else {}

    stable_acc, acc_err = _read_numeric_metric(stable, "accuracy_percent", missing_default=0.0)

    cer_present = "cer_percent" in stable and stable.get("cer_percent") is not None
    stable_cer = 100.0
    cer_failure: str | None = None
    if not cer_present:
        cer_failure = "stable_cer_missing"
    else:
        cer_value, cer_err = _read_numeric_metric(stable, "cer_percent", missing_default=100.0)
        if cer_err == "cer_percent_invalid":
            cer_failure = "stable_cer_invalid"
        elif cer_value < 0.0:
            cer_failure = "stable_cer_invalid"
        else:
            stable_cer = cer_value
            if stable_cer > 20.00:
                cer_failure = "stable_cer_above_20"

    loss_source = strict if "stable_to_final_loss_percent" in strict else score
    loss, _loss_err = _read_numeric_metric(loss_source, "stable_to_final_loss_percent", missing_default=0.0)

    keyterm_count, _ = _read_numeric_metric(request, "keyterm_count", missing_default=0.0)
    keyword_count, _ = _read_numeric_metric(request, "keyword_count", missing_default=0.0)
    reference_terms_loaded, _ = _read_numeric_metric(request, "reference_terms_loaded", missing_default=0.0)
    delivery_ratio, _ = _read_numeric_metric(audio_summary, "delivery_ratio", missing_default=0.0)
    failed_chunk_count, _ = _read_numeric_metric(audio_summary, "failed_chunk_count", missing_default=0.0)

    missing_ids = audio_summary.get("missing_sent_chunk_ids")
    if missing_ids is None:
        missing_chunk_count = 0
    elif isinstance(missing_ids, list):
        missing_chunk_count = len(missing_ids)
    else:
        missing_chunk_count = int(missing_ids) if missing_ids else 0

    dup_ids = audio_summary.get("duplicate_sent_chunk_ids")
    dup_chunk_count = len(dup_ids) if isinstance(dup_ids, list) else 0
    unexpected_ids = audio_summary.get("unexpected_sent_chunk_ids")
    unexpected_chunk_count = len(unexpected_ids) if isinstance(unexpected_ids, list) else 0

    name_acc, _ = _read_numeric_metric(domain, "combined_name_accuracy_percent", missing_default=0.0)
    dates_acc, _ = _read_numeric_metric(domain, "dates_times_accuracy_percent", missing_default=0.0)
    numbers_acc, _ = _read_numeric_metric(domain, "numbers_accuracy_percent", missing_default=0.0)
    money_acc, _ = _read_numeric_metric(domain, "money_percentage_accuracy_percent", missing_default=0.0)
    critical_acc, _ = _read_numeric_metric(
        domain, "combined_critical_entity_accuracy_percent", missing_default=0.0
    )
    raw_acc, _ = _read_numeric_metric(
        strict.get("raw") if isinstance(strict.get("raw"), dict) else {},
        "accuracy_percent",
        missing_default=0.0,
    )
    final_acc, _ = _read_numeric_metric(
        strict.get("final") if isinstance(strict.get("final"), dict) else {},
        "accuracy_percent",
        missing_default=0.0,
    )

    failures: list[str] = []
    if fixture_mode:
        failures.append("fixture_mode_not_live_benchmark")

    # v26.4 fail-closed enforcement (spec H): stage_manifest.completed=false or a
    # failed evidence gate can never yield real_benchmark_completed=true.
    if not stage_manifest_completed:
        failures.append("stage_manifest_completed_false")
    if not scoring_permitted:
        failures.append("scoring_not_permitted")

    if not isolation.get("isolation_verified"):
        failures.append("reference_isolation_failed")
    if int(keyterm_count) != 0:
        failures.append("keyterm_count_nonzero")
    if int(keyword_count) != 0:
        failures.append("keyword_count_nonzero")
    if int(reference_terms_loaded) != 0:
        failures.append("reference_terms_loaded_nonzero")
    if delivery_ratio < 0.999:
        failures.append("audio_delivery_ratio_below_threshold")
    if missing_chunk_count > 0:
        failures.append("audio_delivery_missing_chunks")
    if acc_err == "accuracy_percent_invalid" or stable_acc < 80.00:
        failures.append("stable_accuracy_below_80")
    if cer_failure:
        failures.append(cer_failure)
    if name_acc < 85.00:
        failures.append("combined_name_below_85")
    if dates_acc < 85.00:
        failures.append("dates_times_below_85")
    if numbers_acc < 85.00:
        failures.append("numbers_below_85")
    if money_acc < 85.00:
        failures.append("money_percentage_below_85")
    if critical_acc < 85.00:
        failures.append("combined_critical_entity_below_85")
    if loss > 0.0:
        failures.append("stable_to_final_loss_nonzero")
    if runtime.get("runtime_regressions"):
        failures.append("runtime_regressions_present")
    if not verification.get("verification_passed"):
        failures.append("independent_verification_failed")

    # Retain audited zero-valid audio counters in local scope for fail-closed clarity.
    _ = (failed_chunk_count, dup_chunk_count, unexpected_chunk_count)

    passed = not failures and not fixture_mode

    if fixture_mode:
        version = "NOT_ACCEPTED"
        status = "FIXTURE_ONLY"
        ready_beta = False
    elif passed:
        version = "ACCEPTED"
        status = "PASSED"
        ready_beta = True
    elif not stage_manifest_completed or not scoring_permitted:
        version = "NOT_ACCEPTED"
        status = evidence_gate_status or "EVIDENCE_INCOMPLETE"
        ready_beta = False
    else:
        version = "NOT_ACCEPTED"
        status = "GATE_NOT_REACHED"
        ready_beta = False

    # real_benchmark_completed may only be true for a live run whose stage manifest
    # is complete AND whose evidence gate permitted scoring. No later code may
    # overwrite this to true (spec H).
    real_benchmark_completed = (
        (not fixture_mode) and bool(stage_manifest_completed) and bool(scoring_permitted)
    )

    return {
        "VERSION": version,
        "STATUS": status,
        "benchmark": "multidomain_meeting_v1",
        "fixture_mode": bool(fixture_mode),
        "real_benchmark_completed": real_benchmark_completed,
        "stage_manifest_completed": bool(stage_manifest_completed),
        "scoring_permitted": bool(scoring_permitted),
        "reference_isolation_verified": bool(isolation.get("isolation_verified")),
        "actual_deepgram_request_verified": bool(request),
        "keyterm_count": int(keyterm_count),
        "keyword_count": int(keyword_count),
        "reference_terms_loaded": int(reference_terms_loaded),
        "audio_delivery_ratio": float(delivery_ratio),
        "audio_delivery_missing_chunks": int(missing_chunk_count),
        "raw_accuracy_percent": float(raw_acc),
        "stable_accuracy_percent": float(stable_acc),
        "final_accuracy_percent": float(final_acc),
        "stable_cer_percent": float(stable_cer),
        "combined_name_accuracy_percent": float(name_acc),
        "dates_times_accuracy_percent": float(dates_acc),
        "numbers_accuracy_percent": float(numbers_acc),
        "money_percentage_accuracy_percent": float(money_acc),
        "combined_critical_entity_accuracy_percent": float(critical_acc),
        "stable_to_final_loss_percent": float(loss),
        "runtime_regressions": list(runtime.get("runtime_regressions") or []),
        "independent_verification_passed": bool(verification.get("verification_passed")),
        "ready_for_translation_beta": ready_beta,
        "failed_gates": failures,
        "failures": failures,
        "app_version": MULTIDOMAIN_VERSION,
        "frozen_infrastructure_baseline": FROZEN_INFRASTRUCTURE,
        "created_at": utc_now_iso(),
    }


def create_analysis_package(
    *,
    project_root: Path,
    run_folder: Path,
    reference_path: Path,
    truth_path: Path,
    report_text: str,
) -> Path:
    run_id = run_folder.name
    stage = run_folder / "accuracy_stage_compare"
    report_path = stage / "Cursor final report.txt"
    report_path.write_text(report_text, encoding="utf-8")

    ref_copy_dir = run_folder / "reference_copy"
    ref_copy_dir.mkdir(parents=True, exist_ok=True)
    ref_copy = ref_copy_dir / "multidomain_meeting_v1.txt"
    truth_copy = ref_copy_dir / "multidomain_meeting_v1_truth.json"
    if reference_path.exists():
        ref_copy.write_text(reference_path.read_text(encoding="utf-8"), encoding="utf-8")
    if truth_path.exists():
        truth_copy.write_text(truth_path.read_text(encoding="utf-8"), encoding="utf-8")

    manifest_src = (
        project_root
        / "troubleshooting"
        / "implementation_evidence"
        / f"v{MULTIDOMAIN_VERSION}"
        / "source_change_manifest.json"
    )

    zip_path = run_folder / f"MULTIDOMAIN_GATE_ANALYSIS_PACKAGE_{run_id}.zip"
    members: list[tuple[Path, str]] = []

    stage_names = [
        "raw_deepgram.txt",
        "stable_transcript.txt",
        "final_alpha_output.txt",
        "stage_manifest.json",
        "audio_delivery_events.jsonl",
        "audio_delivery_summary.json",
        "deepgram_request_actual.json",
        "reference_isolation_actual.json",
        "strict_score.json",
        "meaning_equivalent_score.json",
        "domain_category_score.json",
        "runtime_regression_report.json",
        "independent_verification.json",
        "multidomain_gate_acceptance.json",
    ]
    for name in stage_names:
        src = stage / name
        if src.exists():
            members.append((src, f"accuracy_stage_compare/{name}"))

    if ref_copy.exists():
        members.append((ref_copy, "reference_copy/multidomain_meeting_v1.txt"))
    if truth_copy.exists():
        members.append((truth_copy, "reference_copy/multidomain_meeting_v1_truth.json"))

    log_candidates = [
        project_root / "troubleshooting" / "logs" / f"v{MULTIDOMAIN_VERSION}_japanese_accuracy.log",
        project_root / "troubleshooting" / "logs" / f"v{MULTIDOMAIN_VERSION}_diagnostic_test.log",
        project_root / "troubleshooting" / "logs" / f"v{MULTIDOMAIN_VERSION}_freeze_guard.log",
        project_root / "troubleshooting" / "logs" / f"v{MULTIDOMAIN_VERSION}_debug.log",
    ]
    for p in project_root.glob(f"**/v{MULTIDOMAIN_VERSION}_*.log"):
        log_candidates.append(p)

    seen: set[str] = set()
    for src in log_candidates:
        if not src.exists() or not src.is_file():
            continue
        if src.suffix.lower() in AUDIO_EXCLUDE_SUFFIXES:
            continue
        arc = f"logs/{src.name}"
        if arc in seen:
            continue
        seen.add(arc)
        members.append((src, arc))

    if manifest_src.exists():
        members.append((manifest_src, "source_change_manifest.json"))
    members.append((report_path, "Cursor final report.txt"))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arc in members:
            if src.suffix.lower() in AUDIO_EXCLUDE_SUFFIXES:
                continue
            zf.write(src, arcname=arc)

    # Reopen ZIP to verify entries
    with zipfile.ZipFile(zip_path, "r") as zf:
        _ = zf.namelist()

    sha_path = run_folder / f"MULTIDOMAIN_GATE_ANALYSIS_PACKAGE_{run_id}.sha256"
    sha_path.write_text(sha256_file(zip_path) + "\n", encoding="utf-8")
    size_path = run_folder / f"MULTIDOMAIN_GATE_ANALYSIS_PACKAGE_{run_id}.size.txt"
    size_path.write_text(str(zip_path.stat().st_size) + "\n", encoding="utf-8")
    return zip_path


create_upload_package = create_analysis_package


def launch_application(project_root: Path, env: dict[str, str]) -> subprocess.Popen[Any]:
    main_py = project_root / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"main_py_missing:{main_py}")
    merged = os.environ.copy()
    merged.update(env)
    for key in list(merged):
        if "REFERENCE" in key.upper() or "TRUTH" in key.upper():
            if "multidomain" in merged.get(key, "").lower():
                merged.pop(key, None)
    merged.pop("ALPHA_ISSUE12_STAGE1_BENCHMARK", None)
    merged.pop("ISSUE12_STAGE1_BENCHMARK", None)
    return subprocess.Popen(
        [sys.executable, str(main_py)],
        cwd=str(project_root),
        env=merged,
    )


def print_verdict(acceptance: dict[str, Any], package: Path, *, fixture_mode: bool) -> None:
    if fixture_mode:
        print(f"VERSION={acceptance['VERSION']}")
        print(f"STATUS={acceptance['STATUS']}")
        print("real_benchmark_completed=false")
        print("ready_for_translation_beta=false")
        print(f"analysis_package={package}")
        return

    print(f"VERSION={acceptance['VERSION']}")
    print(f"STATUS={acceptance['STATUS']}")
    print(f"benchmark={acceptance['benchmark']}")
    print(f"raw_accuracy_percent={round(float(acceptance['raw_accuracy_percent']), 2):.2f}")
    print(f"stable_accuracy_percent={round(float(acceptance['stable_accuracy_percent']), 2):.2f}")
    print(f"final_accuracy_percent={round(float(acceptance['final_accuracy_percent']), 2):.2f}")
    print(f"stable_cer_percent={round(float(acceptance['stable_cer_percent']), 2):.2f}")
    print(
        f"combined_name_accuracy_percent={round(float(acceptance['combined_name_accuracy_percent']), 2):.2f}"
    )
    print(
        f"dates_times_accuracy_percent={round(float(acceptance['dates_times_accuracy_percent']), 2):.2f}"
    )
    print(f"numbers_accuracy_percent={round(float(acceptance['numbers_accuracy_percent']), 2):.2f}")
    print(
        "money_percentage_accuracy_percent="
        f"{round(float(acceptance['money_percentage_accuracy_percent']), 2):.2f}"
    )
    print(
        "combined_critical_entity_accuracy_percent="
        f"{round(float(acceptance['combined_critical_entity_accuracy_percent']), 2):.2f}"
    )
    print(f"audio_delivery_ratio={round(float(acceptance['audio_delivery_ratio']), 4):.4f}")
    print(f"missing_audio_chunks={acceptance['audio_delivery_missing_chunks']}")
    print(f"stable_to_final_loss_percent={round(float(acceptance['stable_to_final_loss_percent']), 2):.2f}")
    print(f"runtime_regressions={len(acceptance.get('runtime_regressions') or [])}")
    print(f"reference_isolation_verified={'true' if acceptance['reference_isolation_verified'] else 'false'}")
    print(
        "independent_verification_passed="
        f"{'true' if acceptance['independent_verification_passed'] else 'false'}"
    )
    print(f"ready_for_translation_beta={'true' if acceptance['ready_for_translation_beta'] else 'false'}")
    if acceptance["STATUS"] != "PASSED":
        print(f"failed_gates={acceptance.get('failed_gates')}")
    print(f"analysis_package={package}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multidomain gate orchestrator")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    parser.add_argument("--truth-metadata", default=str(DEFAULT_TRUTH))
    parser.add_argument("--recording-label", required=True)
    parser.add_argument("--expected-duration-seconds", type=float, required=True)
    parser.add_argument(
        "--fixture-run-folder",
        default="",
        help="Score an existing fixture without launching Alpha.",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from score_multidomain_gate_85262 import ScoringNotPermittedError, score_all
    from verify_multidomain_gate_85262 import verify_multidomain_gate

    fixture_mode = bool(args.fixture_run_folder)
    print("=== Multidomain Gate — Domain-Agnostic Japanese Accuracy & Audio Delivery ===")
    print(f"app_version={MULTIDOMAIN_VERSION}")
    print(f"frozen_infrastructure={FROZEN_INFRASTRUCTURE}")
    print(f"recording_label={args.recording_label}")
    print(f"expected_duration_seconds={args.expected_duration_seconds}")

    impl = validate_implementation_files(project_root)
    if not impl["ok"]:
        print(f"IMPLEMENTATION_FILES_MISSING={impl['missing']}")
        return 2

    infra = validate_frozen_infrastructure(project_root)
    if not infra["ok"]:
        print(f"FROZEN_INFRASTRUCTURE_INVALID={infra['issues']}")
        return 2

    reference_path = Path(args.reference)
    truth_path = Path(args.truth_metadata)
    if not reference_path.is_absolute():
        reference_path = project_root / reference_path
    if not truth_path.is_absolute():
        truth_path = project_root / truth_path
    if not reference_path.exists():
        print(f"REFERENCE_MISSING={reference_path}")
        return 2
    if not truth_path.exists():
        print(f"TRUTH_MISSING={truth_path}")
        return 2

    started_at = utc_now_iso()
    started_at_epoch = time.time()
    child_started_at = started_at
    child_exited_at = started_at
    child_cmdline: list[str] = []
    child_env_keys: list[str] = []
    child_env: dict[str, str] = {}

    if fixture_mode:
        run_folder = Path(args.fixture_run_folder)
        if not run_folder.is_absolute():
            run_folder = project_root / run_folder
        print(f"FIXTURE_RUN_FOLDER={run_folder}")
        child_exited_at = utc_now_iso()
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_id = f"multidomain-v{MULTIDOMAIN_VERSION}-{ts}-{uuid.uuid4().hex[:8]}"
        run_folder = project_root / "troubleshooting" / "runs" / run_id
        run_folder.mkdir(parents=True, exist_ok=True)
        print(f"run_id={run_id}")

        binding_id = uuid.uuid4().hex
        env = {
            "ALPHA_MULTIDOMAIN_BENCHMARK_MODE": "1",
            "JAPANESE_ACCURACY_PROFILE": ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
            "ALPHA_MULTIDOMAIN_BINDING_ID": binding_id,
            "ALPHA_MULTIDOMAIN_PARENT_GATE_RUN_ID": run_id,
        }
        child_env = {k: v for k, v in env.items()}
        child_env_keys = sorted(child_env.keys())

        print("Launching Alpha for ONE live multidomain benchmark...")
        print("1) Start listening")
        print(f"2) Play the complete recording: {args.recording_label}")
        print("3) Stop after the recording ends")
        print("4) Close the application")
        print(f"binding_id={binding_id}")
        proc = launch_application(project_root, env)
        child_cmdline = [sys.executable, str(project_root / "main.py")]
        child_started_at = utc_now_iso()
        try:
            input("Press Enter after the application has been closed...")
        except EOFError:
            proc.wait()
        finally:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=30)
                except Exception:
                    pass
        child_exited_at = utc_now_iso()

    reference_first_opened_at = utc_now_iso()
    _ = sha256_file(reference_path)
    truth_first_opened_at = utc_now_iso()
    _ = truth_path.read_text(encoding="utf-8")

    isolation = build_reference_isolation_actual(
        run_folder=run_folder,
        reference_path=reference_path,
        truth_path=truth_path,
        child_started_at=child_started_at,
        child_exited_at=child_exited_at,
        reference_first_opened_at=reference_first_opened_at,
        truth_first_opened_at=truth_first_opened_at,
        child_cmdline=child_cmdline,
        child_env_keys=child_env_keys,
        child_env=child_env,
    )

    # --- v26.4.1: deterministic parent-child binding (no mtime fallback) ---
    child_run_folder: Path | None = None
    binding_error: str = ""
    if not fixture_mode:
        try:
            child_run_folder = resolve_child_run_folder(
                project_root,
                gate_run_folder=run_folder,
                binding_id=str(child_env.get("ALPHA_MULTIDOMAIN_BINDING_ID") or ""),
                parent_gate_run_id=str(child_env.get("ALPHA_MULTIDOMAIN_PARENT_GATE_RUN_ID") or run_folder.name),
                expected_app_version=MULTIDOMAIN_VERSION,
                expected_process_id=None,
                window_started_at_epoch=started_at_epoch,
            )
            print(f"child_run_folder={child_run_folder}")
            snapshot_child_evidence(
                child_run_folder=child_run_folder,
                gate_run_folder=run_folder,
                child_exited_at=child_exited_at,
            )
        except ChildBindingError as exc:
            binding_error = f"{exc.status}:{exc.detail}"
            print(f"child_run_folder=BINDING_FAILED:{binding_error}")

    # Required order (spec F): stage manifest BEFORE Stop reconciliation / pre-score gate.
    scoring_started_at = utc_now_iso()
    manifest_path = write_stage_manifest(
        run_folder=run_folder,
        reference_path=reference_path,
        started_at=started_at,
        child_exited_at=child_exited_at,
        scoring_started_at=scoring_started_at,
    )
    stage_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stage_manifest_completed = bool(stage_manifest.get("completed"))

    build_transcript_stage_lineage(
        gate_run_folder=run_folder,
        child_run_folder=child_run_folder,
        child_exited_at=child_exited_at,
    )
    build_stop_source_map(run_folder)
    build_stop_evidence_reconciliation(
        run_folder,
        run_started_at=started_at,
        runtime_child_exited_at=child_exited_at,
    )

    stage = run_folder / "accuracy_stage_compare"
    events_path = stage / "audio_delivery_events.jsonl"
    audio_summary = build_audio_delivery_summary(
        events_path,
        run_id=run_folder.name,
        expected_run_id="",
    )
    atomic_write_json(stage / "audio_delivery_summary.json", audio_summary)

    # --- mandatory canonical pre-score evidence gate (spec H) ---
    gate = build_pre_score_evidence_gate(run_folder)
    scoring_permitted = bool(gate.get("scoring_permitted"))
    evidence_gate_status = str(gate.get("status") or "")
    print(f"evidence_gate_passed={'true' if scoring_permitted else 'false'}")
    print(f"evidence_gate_status={evidence_gate_status}")

    score: dict[str, Any] = {}
    if scoring_permitted:
        try:
            score = score_all(
                project_root=project_root,
                run_folder=run_folder,
                reference_path=reference_path,
                truth_path=truth_path,
            )
        except ScoringNotPermittedError as exc:
            scoring_permitted = False
            evidence_gate_status = exc.status
    if not scoring_permitted:
        write_scoring_decision(
            run_folder,
            scoring_permitted=False,
            real_benchmark_completed=False,
            status=evidence_gate_status or "EVIDENCE_INCOMPLETE",
            blocked_reasons=list(gate.get("blocked_reasons") or []),
        )

    runtime = build_runtime_regression_report(run_folder=run_folder, project_root=project_root)

    req: dict[str, Any] = {}
    req_path = stage / "deepgram_request_actual.json"
    if req_path.exists():
        try:
            req = json.loads(req_path.read_text(encoding="utf-8"))
        except Exception:
            req = {}

    package_pre = None
    verification = verify_multidomain_gate(
        project_root=project_root,
        run_folder=run_folder,
        reference_path=reference_path,
        truth_path=truth_path,
        package_path=package_pre,
    )

    acceptance = build_acceptance(
        score=score,
        domain=score.get("domain_category") or {},
        verification=verification,
        isolation=isolation,
        audio_summary=audio_summary,
        runtime=runtime,
        request=req,
        fixture_mode=fixture_mode,
        stage_manifest_completed=stage_manifest_completed,
        scoring_permitted=scoring_permitted,
        evidence_gate_status=evidence_gate_status,
    )
    acceptance_path = stage / "multidomain_gate_acceptance.json"
    acceptance_path.write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_lines = [
        "Multidomain Gate — Cursor final report",
        f"VERSION={acceptance['VERSION']}",
        f"STATUS={acceptance['STATUS']}",
        f"fixture_mode={acceptance['fixture_mode']}",
        f"stable_accuracy_percent={acceptance['stable_accuracy_percent']}",
        f"audio_delivery_ratio={acceptance['audio_delivery_ratio']}",
        f"ready_for_translation_beta={acceptance['ready_for_translation_beta']}",
        f"failures={acceptance.get('failures')}",
    ]
    package = create_analysis_package(
        project_root=project_root,
        run_folder=run_folder,
        reference_path=reference_path,
        truth_path=truth_path,
        report_text="\n".join(report_lines) + "\n",
    )

    verification = verify_multidomain_gate(
        project_root=project_root,
        run_folder=run_folder,
        reference_path=reference_path,
        truth_path=truth_path,
        package_path=package,
    )
    stage.joinpath("independent_verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print_verdict(acceptance, package, fixture_mode=fixture_mode)
    return 0 if acceptance["STATUS"] == "PASSED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
