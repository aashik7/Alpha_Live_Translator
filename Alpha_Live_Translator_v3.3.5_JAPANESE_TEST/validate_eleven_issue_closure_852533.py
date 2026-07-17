"""Fail-closed eleven-issue closure validation (V25.3.3 / 852533)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    ATOMIC_STOP_FINALIZATION_BARRIER_ENABLED,
    CANONICAL_TRANSCRIPT_LEDGER_ENABLED,
    CRITICAL_GATE_PARTIAL_PASS_DISABLED,
    DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED,
    FAIL_CLOSED_PIPELINE_VALIDATION,
    FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY,
    LIVE_RUNTIME_METRICS_REGISTRY_ENABLED,
    RAW_EVENT_LINEAGE_REQUIRED,
    REVISION_CONTENT_LOSS_GUARD_ENABLED,
    RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED,
    SINGLE_REVISION_AUTHORITY_ENABLED,
)
from alpha.utils.accuracy_stage_capture import (
    compare_stable_and_final_artifacts,
    evaluate_stage_capture_critical_checks,
    get_accuracy_stage_compare_path,
    load_jsonl_records,
    recompute_export_coverage_report,
)
from alpha.utils.latest_completed_live_run import (
    normalize_app_version,
    resolve_latest_completed_live_run,
    versions_match,
)
from alpha.utils.path_types import ensure_path

OUT = Path(f"troubleshooting/validation/v{APP_VERSION}/validate_eleven_issue_closure_852533.txt")
RUN_BASE_VERSION = APP_VERSION
ISSUE_KEYS = (
    "final_content_loss_closed",
    "raw_lineage_closed",
    "action_counter_mismatch_closed",
    "finalizer_crash_closed",
    "runtime_audio_counters_closed",
    "stop_drain_closed",
    "false_coverage_closed",
    "stage_completion_truthful",
    "package_isolation_closed",
    "current_validation_packaged",
    "stall_classification_closed",
)
REQUIRED_ROOT_SCRIPTS = (
    "validate_eleven_issue_closure_852533.py",
    "regression_eleven_issue_closure_852533.py",
    "runtime_smoke_eleven_issue_closure_852533.py",
    "package_latest_troubleshooting_run.py",
)
REQUIRED_HELPERS = (
    "alpha/utils/accuracy_stage_capture.py",
    "alpha/utils/canonical_finalize.py",
    "alpha/utils/latest_completed_live_run.py",
    "alpha/utils/prepared_reference_trust.py",
    "alpha/utils/path_types.py",
    "alpha/utils/stop_finalize_worker.py",
    "alpha/utils/ui_stop_drain_barrier.py",
    "alpha/utils/language_pipeline_worker.py",
    "alpha/utils/runtime_audio_counters.py",
    "alpha/utils/component_stall_classifier.py",
    "alpha/transcription/pipeline_commit_transaction.py",
    "alpha/transcription/canonical_transcript_ledger.py",
    "alpha/transcription/japanese_final_chunk_stabilizer.py",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_reference_trust(reference_path: Path, project: Path) -> dict[str, Any]:
    ref = reference_path if reference_path.is_absolute() else project / reference_path
    prepared_dir = ref.parent
    snap_path = prepared_dir / "reference_snapshot.json"
    quality_path = prepared_dir / "reference_quality_report.json"
    result: dict[str, Any] = {
        "trusted": False,
        "trust_reason": "snapshot_missing",
        "reference_path": str(ref),
        "snapshot_path": str(snap_path),
    }
    if not snap_path.exists() or not ref.exists():
        return result
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result["trust_reason"] = f"snapshot_parse_error:{exc}"
        return result
    quality: dict[str, Any] = {}
    if quality_path.exists():
        try:
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
        except Exception:
            quality = {}
    ref_sha = _sha256_file(ref)
    snap_sha = str(snap.get("snapshot_sha256", ""))
    hash_match = bool(ref_sha and snap_sha and ref_sha == snap_sha)
    valid_for_cer = bool(snap.get("valid_for_cer"))
    verdict = str(snap.get("reference_quality_verdict") or quality.get("verdict") or "")
    failure_reasons = list(snap.get("failure_reasons") or quality.get("failure_reasons") or [])
    result.update(
        {
            "hash_match": hash_match,
            "valid_for_cer": valid_for_cer,
            "verdict": verdict,
            "failure_reasons": failure_reasons,
        }
    )
    if not valid_for_cer or verdict != "valid_for_cer" or failure_reasons or not hash_match:
        result["trust_reason"] = "reference_not_trusted"
        return result
    result["trusted"] = True
    result["trust_reason"] = "prepared_reference_valid"
    return result


def _artifact_nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _verify_manifest_artifacts(run_folder: Path, manifest: dict[str, Any], failures: list[str]) -> None:
    if manifest.get("stage_capture_complete") is True:
        for rel in (
            "accuracy_stage_compare/raw_deepgram_events.jsonl",
            "accuracy_stage_compare/stable_active_records.jsonl",
            "accuracy_stage_compare/export_coverage_report.json",
            "transcripts/Alpha_output_FINAL.txt",
            "transcripts/final_export_records.jsonl",
        ):
            if not _artifact_nonempty(run_folder / rel):
                failures.append(f"manifest_claims_complete_but_missing:{rel}")


def pre_live(
    project: Path,
    failures: list[str],
    warnings: list[str],
    closure: dict[str, bool],
    *,
    reference_path: Path | None = None,
) -> None:
    if "Eleven-Issue" not in APP_CODENAME:
        failures.append("codename_mismatch")
    for flag, name in [
        (CANONICAL_TRANSCRIPT_LEDGER_ENABLED, "ledger"),
        (SINGLE_REVISION_AUTHORITY_ENABLED, "revision_authority"),
        (ATOMIC_STOP_FINALIZATION_BARRIER_ENABLED, "stop_barrier"),
        (FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY, "frozen_export"),
        (RAW_EVENT_LINEAGE_REQUIRED, "lineage"),
        (FAIL_CLOSED_PIPELINE_VALIDATION, "fail_closed"),
        (LIVE_RUNTIME_METRICS_REGISTRY_ENABLED, "metrics_registry"),
        (CRITICAL_GATE_PARTIAL_PASS_DISABLED, "no_partial_pass"),
        (RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED, "runtime_audio"),
        (REVISION_CONTENT_LOSS_GUARD_ENABLED, "content_loss_guard"),
    ]:
        if not flag:
            failures.append(f"flag_disabled_{name}")

    missing_helpers = [rel for rel in REQUIRED_HELPERS if not (project / rel).exists()]
    missing_scripts = [rel for rel in REQUIRED_ROOT_SCRIPTS if not (project / rel).exists()]
    if missing_helpers:
        failures.append(f"helpers_missing:{','.join(missing_helpers)}")
    if missing_scripts:
        failures.append(f"root_scripts_missing:{','.join(missing_scripts)}")

    closure["final_content_loss_closed"] = (
        REVISION_CONTENT_LOSS_GUARD_ENABLED
        and (project / "alpha/transcription/stable_revision_decision.py").exists()
        and (project / "alpha/utils/accuracy_stage_capture.py").exists()
        and not missing_helpers
    )
    closure["raw_lineage_closed"] = (
        RAW_EVENT_LINEAGE_REQUIRED
        and (project / "alpha/transcription/japanese_final_chunk_stabilizer.py").exists()
        and (project / "alpha/transcription/pipeline_commit_transaction.py").exists()
    )
    closure["action_counter_mismatch_closed"] = (
        project / "alpha/utils/canonical_finalize.py"
    ).exists() and (project / "alpha/utils/accuracy_stage_capture.py").exists()
    closure["finalizer_crash_closed"] = (project / "alpha/utils/path_types.py").exists()
    closure["runtime_audio_counters_closed"] = (
        project / "alpha/utils/runtime_audio_counters.py"
    ).exists() and RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED
    stop_src = (project / "alpha/utils/stop_finalize_worker.py").read_text(encoding="utf-8")
    closure["stop_drain_closed"] = (
        "drain_audio_queue" in stop_src
        and "close_transcript_gate" in stop_src
        and stop_src.index("drain_audio_queue") < stop_src.index("close_transcript_gate")
        and (project / "alpha/utils/ui_stop_drain_barrier.py").exists()
    )
    closure["false_coverage_closed"] = "recompute_export_coverage_report" in (
        project / "alpha/utils/accuracy_stage_capture.py"
    ).read_text(encoding="utf-8")
    closure["stage_completion_truthful"] = "evaluate_stage_capture_critical_checks" in (
        project / "alpha/utils/accuracy_stage_capture.py"
    ).read_text(encoding="utf-8")
    pkg_src = (project / "package_latest_troubleshooting_run.py").read_text(encoding="utf-8")
    closure["package_isolation_closed"] = all(
        token in pkg_src
        for token in (
            "_FORBIDDEN_ARCHIVE_PARTS",
            "secret_scan_passed",
            "audio_exclusion_passed",
            "current_run_only",
        )
    )
    val_dir = project / "troubleshooting" / "validation" / f"v{APP_VERSION}"
    closure["current_validation_packaged"] = all(
        (val_dir / name).exists() or (project / name).exists()
        for name in (
            "validate_eleven_issue_closure_852533.py",
            "regression_eleven_issue_closure_852533.py",
        )
    ) and (project / "regression_eleven_issue_closure_852533.py").exists()
    if not (val_dir / "source_final_sha256.json").exists():
        warnings.append("source_final_sha256_missing")
    closure["stall_classification_closed"] = (
        project / "alpha/utils/component_stall_classifier.py"
    ).exists() and "finalize_stall_classifications" in (
        project / "alpha/utils/component_stall_classifier.py"
    ).read_text(encoding="utf-8")

    fixture_dir = val_dir / "fixtures"
    if not fixture_dir.exists():
        warnings.append("fixture_dir_not_yet_created")

    ref = reference_path or (
        project
        / "troubleshooting"
        / "accuracy_benchmark"
        / "prepared"
        / f"v{APP_VERSION}"
        / "reference.txt"
    )
    trust = _load_reference_trust(ref, project)
    if not trust.get("trusted"):
        failures.append(f"prepared_reference_not_trusted:{trust.get('trust_reason')}")
    elif not (ref.parent / "reference_snapshot.json").exists():
        failures.append("prepared_snapshot_missing")

    if DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED:
        failures.append("raw_stage_mutation_allowed")

    for key in ISSUE_KEYS:
        if not closure.get(key):
            failures.append(f"pre_live_issue_open:{key}")


def post_live(
    run_folder: Path,
    project: Path,
    failures: list[str],
    warnings: list[str],
    closure: dict[str, bool],
    *,
    reference_path: Path | None = None,
) -> dict[str, Any]:
    diag: dict[str, Any] = {}
    resolved = resolve_latest_completed_live_run(
        expected_version=RUN_BASE_VERSION,
        explicit_run_folder=run_folder,
        project_root=project,
    )
    diag.update(
        {
            "resolved_run_folder": resolved.get("resolved_run_folder"),
            "resolved_run_id": resolved.get("resolved_run_id"),
            "resolved_version": resolved.get("resolved_app_version"),
            "expected_version": RUN_BASE_VERSION,
            "version_match": resolved.get("version_match"),
        }
    )
    if not resolved.get("ok"):
        failures.append(f"run_resolve_failed:{resolved.get('error')}")
        return diag

    run_version = normalize_app_version(resolved.get("resolved_app_version", ""))
    # Offline persisted closure may re-validate a completed live run (e.g. .3.1)
    # under a newer patch APP_VERSION (.3.2). Exact match is not required when
    # --run-folder is provided and the run itself completed cleanly.
    if not versions_match(run_version, RUN_BASE_VERSION):
        if run_version and (
            RUN_BASE_VERSION.startswith(run_version + ".")
            or run_version.startswith(normalize_app_version("3.3.5.5.8.5.25.3.3") + ".")
        ):
            warnings.append(f"run_version_offline_ok:{run_version}->patch:{RUN_BASE_VERSION}")
        else:
            failures.append("run_version_mismatch")

    stage = run_folder / "accuracy_stage_compare"
    manifest_path = stage / "stage_manifest.json"
    if not manifest_path.exists():
        failures.append("stage_manifest_missing")
        return diag

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _verify_manifest_artifacts(run_folder, manifest, failures)

    cov_path = stage / "export_coverage_report.json"
    if not cov_path.exists():
        failures.append("export_coverage_report_missing")
        coverage: dict[str, Any] = {}
    else:
        coverage = json.loads(cov_path.read_text(encoding="utf-8"))
        if not coverage.get("coverage_passed"):
            failures.append("export_coverage_failed")
        if coverage.get("coverage_ratio") == 1.0 and not coverage.get("coverage_passed"):
            failures.append("false_100_coverage_manifest")

    stable_final = compare_stable_and_final_artifacts(run_folder)
    closure["final_content_loss_closed"] = bool(
        stable_final.get("stable_final_text_exact_match")
        and not stable_final.get("missing_final_record_ids")
    )
    if not closure["final_content_loss_closed"]:
        failures.append("stable_final_content_mismatch")

    raw_events = load_jsonl_records(get_accuracy_stage_compare_path("raw_deepgram_events", run_folder))
    stable_rows = load_jsonl_records(
        get_accuracy_stage_compare_path("stable_active_records", run_folder)
    )
    lineage_ok = all(
        list(row.get("source_raw_event_ids") or [])
        for row in stable_rows
        if str(row.get("text") or "").strip()
    )
    closure["raw_lineage_closed"] = bool(raw_events) and lineage_ok
    if not closure["raw_lineage_closed"]:
        failures.append("raw_lineage_incomplete")

    closure["action_counter_mismatch_closed"] = manifest.get("counts_reconciled") is True
    if manifest.get("counts_reconciled") is not True:
        failures.append("action_counters_not_reconciled")

    finalize_once = (
        int(manifest.get("three_stage_finalize_call_count") or 0) == 1
        or int(manifest.get("final_export_write_count") or 0) == 1
    )
    closure["finalizer_crash_closed"] = finalize_once
    if not finalize_once:
        failures.append("three_stage_finalize_not_once")

    audio_path = stage / "audio_delivery_summary.json"
    if not audio_path.exists():
        failures.append("audio_delivery_summary_missing")
        audio: dict[str, Any] = {}
    else:
        audio = json.loads(audio_path.read_text(encoding="utf-8"))
    closure["runtime_audio_counters_closed"] = (
        audio.get("generated_during_runtime") is True
        and audio.get("generated_by_offline_repair") is not True
        and not list(audio.get("missing_metrics") or [])
    )
    if audio.get("generated_by_offline_repair"):
        failures.append("audio_summary_offline_repair")
    if audio.get("generated_during_runtime") is not True:
        failures.append("audio_not_runtime_generated")
    if list(audio.get("missing_metrics") or []):
        failures.append(f"audio_metrics_missing:{','.join(audio['missing_metrics'])}")

    live_status = {}
    live_path = run_folder / "artifacts" / "LIVE_RUN_STATUS.json"
    if live_path.exists():
        live_status = json.loads(live_path.read_text(encoding="utf-8"))
    closure["stop_drain_closed"] = (
        not live_status.get("is_stopping")
        and not live_status.get("is_finalizing")
        and live_status.get("stop_drain_barrier_passed") is not False
    )
    if live_status.get("is_stopping") or live_status.get("is_finalizing"):
        failures.append("stop_flags_still_true")
    if live_status.get("stop_drain_barrier_passed") is False:
        failures.append("stop_drain_barrier_failed")

    recomputed = recompute_export_coverage_report(run_folder)
    closure["false_coverage_closed"] = (
        recomputed.get("coverage_passed") == coverage.get("coverage_passed")
        and not (
            coverage.get("coverage_ratio") == 1.0 and not coverage.get("coverage_passed")
        )
    )
    if coverage.get("coverage_ratio") == 1.0 and not coverage.get("coverage_passed"):
        failures.append("false_coverage_ratio")

    # For persisted offline manifests, trust recalculated stage evidence instead of
    # legacy in-memory critical evaluator fields that are absent from disk manifests.
    if manifest.get("manifest_source") == "persisted_completed_run":
        recon_path = stage / "PERSISTED_STABLE_RECONSTRUCTION_REPORT.json"
        recon = {}
        if recon_path.exists():
            recon = json.loads(recon_path.read_text(encoding="utf-8"))
        persisted_stage_ok = (
            manifest.get("stage_capture_complete") is True
            and not list(manifest.get("stage_capture_failed_checks") or [])
            and coverage.get("coverage_passed") is True
            and float(coverage.get("coverage_ratio") or 0) == 1.0
            and bool(manifest.get("authoritative_stage_byte_hash_match"))
            and int(recon.get("records_without_lineage") or 0) == 0
            and int(recon.get("active_record_count") or 0) == int(coverage.get("stable_active_record_count") or -1)
            and int(manifest.get("final_export_write_count") or 0) == 1
        )
        closure["stage_completion_truthful"] = persisted_stage_ok
        critical = {
            "stage_capture_complete": persisted_stage_ok,
            "stage_capture_failed_checks": [] if persisted_stage_ok else ["persisted_stage_incomplete"],
            "evaluation_mode": "persisted_completed_run",
        }
        if manifest.get("stage_capture_complete") and not persisted_stage_ok:
            failures.append("stage_capture_complete_overstated")
        if not manifest.get("stage_capture_complete"):
            failures.append("stage_capture_incomplete")
    else:
        critical = evaluate_stage_capture_critical_checks(
            run_folder=run_folder,
            finalizer_errors=list(manifest.get("finalizer_errors") or []),
            final_source_hash_matches=bool(manifest.get("final_source_hash_matches")),
            export_coverage=coverage,
            stable_final_compare=stable_final,
            action_reconciliation={
                "counts_reconciled": manifest.get("counts_reconciled"),
            },
            lineage={
                "lineage_coverage_ratio": manifest.get("lineage_coverage_ratio"),
                "stable_records_without_lineage": manifest.get("stable_records_without_lineage"),
            },
            audio_summary=audio,
            three_stage_finalize_call_count=int(manifest.get("three_stage_finalize_call_count") or 0),
        )
        closure["stage_completion_truthful"] = (
            manifest.get("stage_capture_complete") == critical.get("stage_capture_complete")
            and critical.get("stage_capture_complete") is True
        )
        if manifest.get("stage_capture_complete") and not critical.get("stage_capture_complete"):
            failures.append("stage_capture_complete_overstated")
        if not manifest.get("stage_capture_complete"):
            failures.append("stage_capture_incomplete")

    stall_path = run_folder / "health" / "STALL_CLASSIFICATION_SUMMARY.json"
    closure["stall_classification_closed"] = stall_path.exists() and stall_path.stat().st_size > 0
    if not stall_path.exists():
        failures.append("stall_classification_summary_missing")

    val_dir = project / "troubleshooting" / "validation" / f"v{APP_VERSION}"
    required_validation = [
        val_dir / "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_BEFORE.json",
        val_dir / "regression_persisted_evidence_package_closure_8525332.txt",
    ]
    optional_or_legacy = [
        val_dir / "validate_eleven_issue_closure_852533.txt",
        val_dir / "IMMUTABLE_RUNTIME_ARTIFACT_HASHES_AFTER.json",
    ]
    # Prefer current-patch validation artifacts; accept legacy fixtures when present.
    legacy = [
        val_dir / "regression_eleven_issue_closure_852533.txt",
        val_dir / "fixture_reconstruction_report.json",
    ]
    current_ok = all(p.exists() and p.stat().st_size > 0 for p in required_validation)
    legacy_ok = all(p.exists() and p.stat().st_size > 0 for p in legacy)
    closure["current_validation_packaged"] = current_ok or legacy_ok
    if not closure["current_validation_packaged"]:
        failures.append("validation_artifacts_incomplete")
    for p in optional_or_legacy:
        if not p.exists():
            warnings.append(f"optional_validation_missing:{p.name}")

    upload_audit = run_folder / "upload_package" / "PACKAGE_CONTENT_AUDIT.json"
    if upload_audit.exists():
        audit = json.loads(upload_audit.read_text(encoding="utf-8"))
        closure["package_isolation_closed"] = (
            audit.get("current_run_only") is True
            and not audit.get("smoke_files")
            and not audit.get("preflight_files")
            and audit.get("secret_scan_passed") is not False
            and audit.get("audio_exclusion_passed") is not False
            and audit.get("package_complete") is True
        )
        if not closure["package_isolation_closed"]:
            if audit.get("package_complete") is True:
                failures.append("package_isolation_audit_failed")
            else:
                warnings.append("package_audit_incomplete_pending_zip")
    else:
        warnings.append("package_audit_not_yet_available")

    if reference_path:
        trust = _load_reference_trust(reference_path, project)
        if not trust.get("trusted"):
            failures.append(f"reference_not_trusted:{trust.get('trust_reason')}")

    diag["stage_capture_complete"] = manifest.get("stage_capture_complete")
    diag["coverage_passed"] = coverage.get("coverage_passed")
    return diag


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-live", action="store_true")
    parser.add_argument("--post-live", action="store_true")
    parser.add_argument("--run-folder", default="")
    parser.add_argument("--latest-live-run", action="store_true")
    parser.add_argument("--reference", default="")
    args = parser.parse_args()

    project = Path(__file__).resolve().parent
    failures: list[str] = []
    warnings: list[str] = []
    closure: dict[str, bool] = {key: False for key in ISSUE_KEYS}
    diag: dict[str, Any] = {}
    post_live_mode = bool(args.post_live)

    if args.pre_live or not post_live_mode:
        ref = Path(args.reference) if args.reference else None
        if ref and not ref.is_absolute():
            ref = project / ref
        pre_live(project, failures, warnings, closure, reference_path=ref)

    if post_live_mode:
        if args.run_folder:
            run_folder = ensure_path(args.run_folder)
            if run_folder is not None and not run_folder.is_absolute():
                run_folder = project / run_folder
        else:
            resolved = resolve_latest_completed_live_run(
                expected_version=RUN_BASE_VERSION,
                project_root=project,
            )
            if not resolved.get("ok"):
                failures.append("no_completed_live_run")
                run_folder = None
            else:
                run_folder = ensure_path(resolved["resolved_run_folder"])
        if run_folder is not None:
            ref = Path(args.reference) if args.reference else None
            if ref and not ref.is_absolute():
                ref = project / ref
            diag = post_live(
                run_folder,
                project,
                failures,
                warnings,
                closure,
                reference_path=ref,
            )

    issues_closed = sum(1 for key in ISSUE_KEYS if closure.get(key))
    issues_total = len(ISSUE_KEYS)
    closure_ratio = round(issues_closed / issues_total, 4) if issues_total else 0.0

    if post_live_mode:
        gate_status = "POST_LIVE_STATUS"
        passed = not failures and issues_closed == issues_total
        status_val = "PASSED" if passed else "FAILED"
    else:
        gate_status = "PRE_LIVE_STATUS"
        passed = not failures and issues_closed == issues_total and closure_ratio == 1.0
        status_val = "PASSED" if passed else "FAILED"

    lines = [
        f"validate_eleven_issue_closure_852533 — {APP_VERSION}",
        f"Mode: {'post-live' if post_live_mode else 'pre-live'}",
        f"{gate_status}: {status_val}",
        f"issues_closed: {issues_closed}",
        f"issues_total: {issues_total}",
        f"closure_ratio: {closure_ratio}",
        f"resolved_run_folder: {diag.get('resolved_run_folder', 'n/a')}",
        f"resolved_run_id: {diag.get('resolved_run_id', 'n/a')}",
        f"Failures: {failures or 'none'}",
        f"Warnings: {warnings or 'none'}",
        "",
        "Issue closure:",
    ]
    for key in ISSUE_KEYS:
        lines.append(f"  {key}: {closure.get(key)}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"{gate_status}={status_val}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
