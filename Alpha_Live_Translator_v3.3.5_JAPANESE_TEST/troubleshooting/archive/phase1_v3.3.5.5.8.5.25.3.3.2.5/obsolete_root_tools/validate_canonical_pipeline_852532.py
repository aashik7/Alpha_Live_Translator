"""Fail-closed canonical pipeline validation (V25.3.2 / 25.3.2.1)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    ATOMIC_STOP_FINALIZATION_BARRIER_ENABLED,
    CANONICAL_TRANSCRIPT_LEDGER_ENABLED,
    CRITICAL_GATE_PARTIAL_PASS_DISABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FAIL_CLOSED_PIPELINE_VALIDATION,
    FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY,
    LIVE_RUNTIME_METRICS_REGISTRY_ENABLED,
    RAW_EVENT_LINEAGE_REQUIRED,
    SINGLE_REVISION_AUTHORITY_ENABLED,
)
from alpha.utils.cer_backtracking import levenshtein_operation_counts
from alpha.utils.latest_completed_live_run import normalize_app_version, resolve_latest_completed_live_run, versions_match
from alpha.utils.prepared_reference_trust import load_prepared_reference_trust

OUT = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3.2.1/validate_canonical_pipeline_852532.txt")
PREPARED_SNAP = Path("troubleshooting/accuracy_benchmark/prepared/v3.3.5.5.8.5.25.3.2/reference_snapshot.json")
RUN_BASE_VERSION = "3.3.5.5.8.5.25.3.2"


def _fail(msg: str, failures: list[str]) -> None:
    failures.append(msg)


def pre_live(failures: list[str], warnings: list[str]) -> None:
    if "Run Resolution" not in APP_CODENAME and "Canonical Transcript" not in APP_CODENAME:
        _fail("codename_mismatch", failures)
    for flag, name in [
        (CANONICAL_TRANSCRIPT_LEDGER_ENABLED, "ledger"),
        (SINGLE_REVISION_AUTHORITY_ENABLED, "revision_authority"),
        (ATOMIC_STOP_FINALIZATION_BARRIER_ENABLED, "stop_barrier"),
        (FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY, "frozen_export"),
        (RAW_EVENT_LINEAGE_REQUIRED, "lineage"),
        (FAIL_CLOSED_PIPELINE_VALIDATION, "fail_closed"),
        (LIVE_RUNTIME_METRICS_REGISTRY_ENABLED, "metrics_registry"),
        (CRITICAL_GATE_PARTIAL_PASS_DISABLED, "no_partial_pass"),
    ]:
        if not flag:
            _fail(f"flag_disabled_{name}", failures)
    if DEEPGRAM_MODEL != "nova-3" or DEEPGRAM_LANGUAGE != "ja":
        _fail("deepgram_config_changed", failures)
    if not Path("alpha/utils/latest_completed_live_run.py").exists():
        _fail("run_resolver_missing", failures)
    if not Path("alpha/utils/prepared_reference_trust.py").exists():
        _fail("prepared_reference_trust_missing", failures)
    if not PREPARED_SNAP.exists():
        _fail("prepared_reference_missing", failures)
    else:
        snap = json.loads(PREPARED_SNAP.read_text(encoding="utf-8"))
        if not snap.get("valid_for_cer"):
            _fail("reference_not_valid_for_cer", failures)
    counts = levenshtein_operation_counts("abc", "abd")
    if counts["edit_distance"] != counts["substitutions"] + counts["deletions"] + counts["insertions"]:
        _fail("cer_accounting_broken", failures)


def post_live(
    run_folder: Path,
    failures: list[str],
    warnings: list[str],
    *,
    reference_path: Path | None = None,
) -> dict:
    diag: dict = {}
    resolved = resolve_latest_completed_live_run(
        expected_version=RUN_BASE_VERSION,
        explicit_run_folder=run_folder,
    )
    diag.update(
        {
            "resolved_run_folder": resolved.get("resolved_run_folder"),
            "resolved_run_id": resolved.get("resolved_run_id"),
            "resolved_version": resolved.get("resolved_app_version"),
            "expected_version": RUN_BASE_VERSION,
            "version_match": resolved.get("version_match"),
            "version_values": resolved.get("version_values"),
        }
    )
    if not resolved.get("ok"):
        _fail(f"run_resolve_failed:{resolved.get('error')}", failures)
        return diag

    run_version = normalize_app_version(resolved.get("resolved_app_version", ""))
    if not versions_match(run_version, RUN_BASE_VERSION):
        _fail("run_version_mismatch", failures)

    stage = run_folder / "accuracy_stage_compare"
    manifest_path = stage / "stage_manifest.json"
    if not manifest_path.exists():
        _fail("stage_manifest_missing", failures)
        return diag

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stage_ver = normalize_app_version(str(manifest.get("app_version", "")))
    if stage_ver and not versions_match(stage_ver, run_version):
        _fail("stage_manifest_version_mismatch", failures)

    cov_path = stage / "export_coverage_report.json"
    if cov_path.exists():
        cov = json.loads(cov_path.read_text(encoding="utf-8"))
        if not cov.get("coverage_passed"):
            _fail("export_coverage_failed", failures)
    else:
        _fail("export_coverage_report_missing", failures)

    audio_path = stage / "audio_delivery_summary.json"
    diag["audio_summary_exists"] = audio_path.exists()
    if not audio_path.exists():
        _fail("audio_delivery_summary_missing", failures)
    else:
        audio = json.loads(audio_path.read_text(encoding="utf-8"))
        diag["audio_summary_runtime_generated"] = audio.get("generated_during_runtime") is True
        diag["audio_summary_offline_repaired"] = audio.get("generated_by_offline_repair") is True
        missing = list(audio.get("missing_metrics") or [])
        diag["audio_metrics_missing"] = missing
        diag["audio_metrics_complete"] = not missing
        if audio.get("generated_during_runtime") and audio.get("generated_by_offline_repair"):
            _fail("audio_provenance_contradictory", failures)
        if audio.get("generated_during_runtime") is True:
            if int(audio.get("audio_chunks_sent") or 0) <= 0:
                _fail("audio_chunks_sent_zero", failures)
        elif audio.get("generated_by_offline_repair") is True:
            if missing:
                warnings.append(f"audio_metrics_incomplete:{','.join(missing)}")
        else:
            _fail("audio_provenance_unknown", failures)

    live_status = run_folder / "artifacts" / "LIVE_RUN_STATUS.json"
    if live_status.exists():
        st = json.loads(live_status.read_text(encoding="utf-8"))
        if st.get("is_stopping") or st.get("is_finalizing"):
            _fail("stop_flags_still_true", failures)
        if st.get("language_pipeline_worker_alive"):
            _fail("language_worker_still_alive", failures)

    score_path = stage / "three_stage_accuracy_report.json"
    if not score_path.exists():
        _fail("score_report_missing", failures)
    else:
        report = json.loads(score_path.read_text(encoding="utf-8"))
        if report.get("reference_quality_verdict") == "valid_for_cer" and report.get("likely_bottleneck") == "reference_not_trusted":
            _fail("contradictory_scorer_trust_report", failures)
        if report.get("reference_trusted") and not report.get("scoring_completed"):
            _fail("trusted_reference_scores_none", failures)
        if report.get("scoring_completed"):
            for stage_name in ("raw_deepgram", "stable_assembler", "final_alpha"):
                acc = report.get(f"{stage_name.replace('stable_assembler', 'stable_assembler')}_accuracy_percent")
            for key in (
                "raw_deepgram_accuracy_percent",
                "stable_assembler_accuracy_percent",
                "final_alpha_accuracy_percent",
            ):
                if report.get(key) is None:
                    _fail(f"score_null_{key}", failures)
        if manifest.get("reference_not_yet_scored") is True and report.get("scoring_completed"):
            _fail("stale_reference_not_yet_scored", failures)

    if reference_path:
        trust = load_prepared_reference_trust(reference_path)
        if not trust.get("trusted"):
            _fail(f"prepared_reference_not_trusted:{trust.get('trust_reason')}", failures)

    return diag


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-live", action="store_true")
    parser.add_argument("--post-live", action="store_true")
    parser.add_argument("--run-folder", default="")
    parser.add_argument("--reference", default="")
    args = parser.parse_args()
    failures: list[str] = []
    warnings: list[str] = []
    project = Path(__file__).resolve().parent
    diag: dict = {}

    if args.pre_live or not args.post_live:
        pre_live(failures, warnings)
    if args.post_live:
        if args.run_folder:
            run_folder = Path(args.run_folder)
            if not run_folder.is_absolute():
                run_folder = project / run_folder
        else:
            resolved = resolve_latest_completed_live_run(expected_version=RUN_BASE_VERSION)
            if not resolved.get("ok"):
                _fail("no_completed_live_run", failures)
                run_folder = None
            else:
                run_folder = Path(resolved["resolved_run_folder"])
        if run_folder is not None:
            ref = Path(args.reference) if args.reference else None
            if ref and not ref.is_absolute():
                ref = project / ref
            diag = post_live(run_folder, failures, warnings, reference_path=ref)

    status = "FAILED" if failures else ("PASSED_WITH_WARNINGS" if warnings and not CRITICAL_GATE_PARTIAL_PASS_DISABLED else "PASSED")
    if failures and CRITICAL_GATE_PARTIAL_PASS_DISABLED:
        status = "FAILED"

    lines = [
        f"validate_canonical_pipeline_852532 — {APP_VERSION}",
        f"Mode: {'post-live' if args.post_live else 'pre-live'}",
        f"Status: {status}",
        f"resolved_run_folder: {diag.get('resolved_run_folder', 'n/a')}",
        f"resolved_run_id: {diag.get('resolved_run_id', 'n/a')}",
        f"resolved_version: {diag.get('resolved_version', 'n/a')}",
        f"expected_version: {diag.get('expected_version', RUN_BASE_VERSION)}",
        f"version_match: {diag.get('version_match', 'n/a')}",
        f"audio_summary_exists: {diag.get('audio_summary_exists', 'n/a')}",
        f"audio_summary_offline_repaired: {diag.get('audio_summary_offline_repaired', 'n/a')}",
        f"Failures: {failures or 'none'}",
        f"Warnings: {warnings or 'none'}",
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(status)
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
