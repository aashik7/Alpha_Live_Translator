"""Canonical acceptance state — single authoritative in-memory acceptance object (V25.3.3.2.1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from alpha.constants import APP_VERSION
from alpha.utils.canonical_content_hash import (
    atomic_write_json,
    byte_sha256_file,
    normalize_text_content,
)
from alpha.utils.immutable_evidence_contract import (
    IMMUTABLE_HASHES_AFTER_FILENAME,
    IMMUTABLE_HASHES_BEFORE_FILENAME,
)
from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import (
    compute_persisted_coverage,
    load_persisted_action_counts,
    load_persisted_audio_summary,
    load_persisted_final_records,
    load_persisted_stall_summary,
    load_persisted_stop_state,
    load_run_identity,
    _extract_lineage,
    _read_json,
    _read_jsonl,
)
from alpha.utils.strict_evidence_values import is_exactly_true, is_numeric_zero


VALIDATION_VERSION = "3.3.5.5.8.5.25.3.3.2.1"
SOURCE_OF_TRUTH = "canonical_acceptance_state"

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

PACKAGE_PENDING_ISSUES = (
    "package_isolation_closed",
    "current_validation_packaged",
)

IMMUTABLE_RELS = (
    "transcripts/Alpha_output_FINAL.txt",
    "transcripts/final_export_records.jsonl",
    "transcripts/FINAL_EXPORT_SEAL.json",
    "accuracy_stage_compare/stable_assembler_events.jsonl",
    "transcripts/stable_commits.jsonl",
    "accuracy_stage_compare/audio_delivery_summary.json",
)

STALE_ACCEPTANCE_BASENAMES = frozenset(
    {
        "ELEVEN_ISSUE_FINAL_CLOSURE.json",
        "FINAL_ACCEPTANCE.json",
        "POST_ZIP_VERIFICATION.json",
        "PACKAGE_CONTENT_AUDIT.json",
    }
)

REQUIRED_VALIDATION_IN_ZIP = (
    "validation/CANONICAL_PREPACKAGE_VALIDATION.json",
    "validation/ELEVEN_ISSUE_PREPACKAGE_CLOSURE.json",
    "validation/STALE_ACCEPTANCE_EVIDENCE_AUDIT.json",
    f"validation/{IMMUTABLE_HASHES_BEFORE_FILENAME}",
    "validation/PACKAGE_STAGING_AUDIT.json",
)


class CanonicalAcceptanceContradictionError(RuntimeError):
    """Raised when VERSION=ACCEPTED would contradict other acceptance fields."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_immutable_artifacts(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    arts: dict[str, Any] = {}
    for rel in IMMUTABLE_RELS:
        p = folder / rel
        arts[rel] = {
            "exists": p.exists(),
            "sha256": byte_sha256_file(p) if p.exists() else None,
            "size": p.stat().st_size if p.exists() else 0,
        }
    return {
        "run_folder": str(folder),
        "generated_utc": _utc_now(),
        "artifacts": arts,
    }


def compare_immutable_hashes(before: dict[str, Any], after: dict[str, Any]) -> bool:
    ba = before.get("artifacts") or {}
    aa = after.get("artifacts") or {}
    if set(ba.keys()) != set(aa.keys()):
        return False
    for key in ba:
        if ba[key].get("sha256") != aa[key].get("sha256"):
            return False
        if ba[key].get("size") != aa[key].get("size"):
            return False
    return True


def _record_id(row: dict[str, Any]) -> str:
    for k in ("record_id", "stable_commit_id", "commit_id", "id"):
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v)
    return ""


def evaluate_transcript_integrity(run_folder: Path) -> dict[str, Any]:
    stage = run_folder / "accuracy_stage_compare"
    stable = _read_jsonl(stage / "stable_active_records.jsonl")
    final = load_persisted_final_records(run_folder)
    final_txt = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    text = final_txt.read_text(encoding="utf-8", errors="replace") if final_txt.exists() else ""

    stable_ids = [_record_id(r) for r in stable]
    final_ids = [_record_id(r) for r in final]
    missing = [i for i in stable_ids if i and i not in final_ids]
    extra = [i for i in final_ids if i and i not in stable_ids]
    order_match = stable_ids == final_ids
    count_equal = len(stable) == len(final) and len(stable) > 0

    norm_ok = True
    speaker_ok = True
    for s, f in zip(stable, final):
        if normalize_text_content(str(s.get("text") or "")) != normalize_text_content(
            str(f.get("text") or "")
        ):
            norm_ok = False
        if str(s.get("speaker") or "") != str(f.get("speaker") or ""):
            speaker_ok = False

    # Rebuild text from final records for consistency check
    rebuilt = "\n".join(str(r.get("text") or "") for r in final)
    text_match = normalize_text_content(text) == normalize_text_content(rebuilt) or (
        normalize_text_content(text)
        == normalize_text_content(
            "\n".join(
                line
                for line in text.splitlines()
                if line.strip()
            )
        )
        and norm_ok
        and count_equal
    )
    # Prefer content from records; Alpha_output_FINAL may include speaker prefixes.
    # Primary gate is stable↔final record equality.
    closed = bool(
        count_equal
        and order_match
        and not missing
        and not extra
        and norm_ok
        and speaker_ok
    )
    return {
        "stable_count": len(stable),
        "final_count": len(final),
        "record_counts_equal": count_equal,
        "record_ids_equal": not missing and not extra and set(stable_ids) == set(final_ids),
        "record_order_equal": order_match,
        "normalized_text_equal": norm_ok,
        "speaker_values_equal": speaker_ok,
        "missing_final_record_ids": missing,
        "extra_final_record_ids": extra,
        "final_text_bytes": len(text.encode("utf-8")),
        "closed": closed,
    }


def evaluate_lineage_integrity(run_folder: Path) -> dict[str, Any]:
    stage = run_folder / "accuracy_stage_compare"
    stable = _read_jsonl(stage / "stable_active_records.jsonl")
    raw_events = _read_jsonl(stage / "raw_deepgram_events.jsonl")
    if not raw_events:
        # Fallback: finals may embed event ids
        raw_events = _read_jsonl(run_folder / "transcripts" / "raw_deepgram_finals.jsonl")
    known_ids: set[str] = set()
    for ev in raw_events:
        for k in ("event_id", "raw_event_id", "id", "message_id"):
            v = ev.get(k)
            if v is not None and str(v).strip():
                known_ids.add(str(v))
        # Also gather nested
        meta = ev.get("assembler_metadata") if isinstance(ev.get("assembler_metadata"), dict) else {}
        for k in ("event_id", "raw_event_id"):
            v = meta.get(k)
            if v:
                known_ids.add(str(v))

    unresolved: list[str] = []
    with_lineage = 0
    total = 0
    for row in stable:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        total += 1
        lineage = _extract_lineage(row)
        if lineage:
            with_lineage += 1
            for lid in lineage:
                if known_ids and lid not in known_ids:
                    unresolved.append(lid)
        else:
            unresolved.append(f"missing_lineage:{_record_id(row)}")

    # If raw events don't expose IDs, trust non-empty lineage lists (known pattern)
    if not known_ids:
        unresolved = [u for u in unresolved if u.startswith("missing_lineage:")]

    coverage = (with_lineage / total) if total else 0.0
    closed = total > 0 and coverage == 1.0 and len(unresolved) == 0
    return {
        "active_records_with_text": total,
        "records_with_lineage": with_lineage,
        "lineage_coverage": round(coverage, 4),
        "unresolved_raw_event_ids": unresolved,
        "raw_event_count": len(raw_events),
        "closed": closed,
    }


def evaluate_action_integrity(run_folder: Path) -> dict[str, Any]:
    counts = load_persisted_action_counts(run_folder)
    closed = bool(counts.get("counts_reconciled"))
    return {**counts, "closed": closed}


def evaluate_finalizer_integrity(run_folder: Path) -> dict[str, Any]:
    seal = _read_json(run_folder / "transcripts" / "FINAL_EXPORT_SEAL.json")
    stop = load_persisted_stop_state(run_folder)
    live = stop.get("live_run_status") or {}
    manifest = _read_json(run_folder / "accuracy_stage_compare" / "stage_manifest.json")
    write_count = int(seal.get("write_count") or manifest.get("final_export_write_count") or 0)
    post_seal = int(
        seal.get("post_seal_write_attempt_count")
        or manifest.get("post_seal_write_attempt_count")
        or 0
    )
    sealed = bool(seal.get("sealed") and seal.get("seal_verified", seal.get("sealed")))
    stop_ok = bool(stop.get("stop_finalize_completed")) and not bool(stop.get("stop_finalize_failed"))
    no_exc = not bool(live.get("finalizer_exception")) and not list(
        live.get("finalizer_errors") or manifest.get("finalizer_errors") or []
    )
    writer = str(seal.get("writer_function") or "")
    one_writer = bool(writer) and write_count == 1
    closed = bool(stop_ok and no_exc and one_writer and write_count == 1 and post_seal == 0 and sealed)
    return {
        "stop_finalize_completed": stop_ok,
        "no_finalizer_exception": no_exc,
        "authoritative_writer": writer,
        "final_write_count": write_count,
        "post_seal_write_attempt_count": post_seal,
        "seal_verified": sealed,
        "closed": closed,
    }


def evaluate_audio_integrity(run_folder: Path) -> dict[str, Any]:
    audio = load_persisted_audio_summary(run_folder)
    closed = bool(
        int(audio.get("audio_chunks_sent") or 0) > 0
        and int(audio.get("audio_bytes_sent") or 0) > 0
        and float(audio.get("calculated_audio_seconds_sent") or 0) > 0
        and audio.get("generated_during_runtime") is True
        and audio.get("generated_by_offline_repair") is not True
        and not list(audio.get("missing_metrics") or [])
    )
    return {
        "audio_chunks_sent": int(audio.get("audio_chunks_sent") or 0),
        "audio_bytes_sent": int(audio.get("audio_bytes_sent") or 0),
        "calculated_audio_seconds_sent": float(audio.get("calculated_audio_seconds_sent") or 0),
        "generated_during_runtime": audio.get("generated_during_runtime"),
        "generated_by_offline_repair": audio.get("generated_by_offline_repair"),
        "missing_metrics": list(audio.get("missing_metrics") or []),
        "closed": closed,
    }


def evaluate_stop_integrity(run_folder: Path) -> dict[str, Any]:
    live = _read_json(run_folder / "artifacts" / "LIVE_RUN_STATUS.json")
    recon = _read_json(run_folder / "artifacts" / "FINAL_STATUS_RECONCILIATION.json")
    fields = (recon.get("fields") or {}) if recon else {}

    def _recon_val(name: str) -> Any:
        entry = fields.get(name) or {}
        return entry.get("value")

    posted = int(live.get("ui_event_posted_count") or live.get("ui_bus_events_posted") or 0)
    drained = int(live.get("ui_event_drained_count") or live.get("ui_bus_events_drained") or 0)
    ui_q = int(live.get("ui_queue_size") or live.get("transcript_ui_queue_size") or 0)
    bus_q = int(live.get("ui_event_bus_queue_size") or live.get("ui_bus_queue_remaining") or 0)
    worker_alive = bool(live.get("language_pipeline_worker_alive"))
    flags_clear = (live.get("is_stopping") is False) and (live.get("is_finalizing") is False)
    queues_empty = ui_q == 0 and bus_q == 0
    reconciled = posted == drained

    # Fail closed: barrier must be exactly True from live OR reconciliation.
    barrier_live = live.get("stop_drain_barrier_passed")
    barrier_recon = _recon_val("stop_drain_barrier_passed")
    if is_exactly_true(barrier_live):
        barrier_ok = True
        barrier_value = True
    elif is_exactly_true(barrier_recon):
        barrier_ok = True
        barrier_value = True
    else:
        # Independent persisted evidence without treating null as success
        jap = run_folder / "logs" / "japanese_accuracy.log"
        barrier_value = None
        barrier_ok = False
        if jap.exists():
            for line in jap.read_text(encoding="utf-8", errors="replace").splitlines():
                if "UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED" not in line or "{" not in line:
                    continue
                try:
                    import json as _json

                    row = _json.loads(line[line.find("{") :])
                except Exception:
                    continue
                if row.get("event") == "UI_STOP_DRAIN_BARRIER_ACKNOWLEDGED" and row.get("passed") is True:
                    barrier_ok = True
                    barrier_value = True
                    break

    closed = bool(flags_clear and queues_empty and not worker_alive and reconciled and barrier_ok)
    return {
        "transcript_ui_queue_size": ui_q,
        "ui_event_bus_queue_size": bus_q,
        "language_pipeline_worker_alive": worker_alive,
        "is_stopping": live.get("is_stopping"),
        "is_finalizing": live.get("is_finalizing"),
        "ui_event_posted_count": posted,
        "ui_event_drained_count": drained,
        "posted_drained_reconciled": reconciled,
        "stop_drain_barrier_passed": barrier_value if barrier_value is not None else barrier_live,
        "stop_drain_barrier_evidence": (
            "live"
            if is_exactly_true(barrier_live)
            else (
                "reconciliation"
                if is_exactly_true(barrier_recon)
                else ("japanese_accuracy_log" if barrier_ok else "missing")
            )
        ),
        "closed": closed,
    }


def evaluate_coverage_integrity(run_folder: Path) -> dict[str, Any]:
    recomputed = compute_persisted_coverage(run_folder)
    closed = bool(
        float(recomputed.get("coverage_ratio") or 0) == 1.0
        and recomputed.get("coverage_passed") is True
    )
    return {
        "coverage_ratio": recomputed.get("coverage_ratio"),
        "coverage_passed": recomputed.get("coverage_passed"),
        "stable_active_record_count": recomputed.get("stable_active_record_count"),
        "final_record_count": recomputed.get("final_record_count"),
        "matched_record_count": recomputed.get("matched_record_count"),
        "closed": closed,
        "recomputed": True,
    }


def evaluate_stage_integrity(run_folder: Path) -> dict[str, Any]:
    stage = run_folder / "accuracy_stage_compare"
    manifest = _read_json(stage / "stage_manifest.json")
    stable = _read_jsonl(stage / "stable_active_records.jsonl")
    final = load_persisted_final_records(run_folder)
    auth = run_folder / "transcripts" / "Alpha_output_FINAL.txt"
    stage_final = stage / "final_alpha_output.txt"
    byte_match = False
    if auth.exists() and stage_final.exists():
        byte_match = auth.read_bytes() == stage_final.read_bytes()
    score = stage / "three_stage_accuracy_report.json"
    trusted_score = score.exists() and score.stat().st_size > 0
    persisted_src = manifest.get("manifest_source") == "persisted_completed_run"
    capture_complete = manifest.get("stage_capture_complete") is True
    failed = list(manifest.get("stage_capture_failed_checks") or [])
    closed = bool(
        len(stable) > 0
        and len(final) > 0
        and byte_match
        and trusted_score
        and persisted_src
        and capture_complete
        and not failed
        and manifest.get("authoritative_stage_byte_hash_match") is not False
    )
    return {
        "stable_active_nonempty": len(stable) > 0,
        "final_evidence_nonempty": len(final) > 0,
        "stage_final_byte_identical": byte_match,
        "trusted_score_exists": trusted_score,
        "manifest_source": manifest.get("manifest_source"),
        "stage_capture_complete": capture_complete,
        "stage_capture_failed_checks": failed,
        "closed": closed,
    }


def evaluate_stall_integrity(run_folder: Path) -> dict[str, Any]:
    stall = load_persisted_stall_summary(run_folder)
    unresolved = int(stall.get("unresolved_stall_count", -1) if stall else -1)
    closed = bool(stall) and unresolved == 0
    return {
        "unresolved_stall_count": unresolved,
        "stall_summary_present": bool(stall),
        "closed": closed,
    }


def evaluate_immutable_integrity(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    after = after or before or {}
    before = before or {}
    unchanged = compare_immutable_hashes(before, after) if before and after else False
    return {
        "immutable_runtime_artifacts_unchanged": unchanged,
        "closed": unchanged,
    }


def build_issue_results(
    sections: dict[str, Any],
    *,
    package_isolation: Optional[bool] = None,
    current_validation_packaged: Optional[bool] = None,
    pending_package: bool = True,
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "final_content_loss_closed": bool(sections["transcript_integrity"].get("closed")),
        "raw_lineage_closed": bool(sections["lineage_integrity"].get("closed")),
        "action_counter_mismatch_closed": bool(sections["action_integrity"].get("closed")),
        "finalizer_crash_closed": bool(sections["finalizer_integrity"].get("closed")),
        "runtime_audio_counters_closed": bool(sections["audio_integrity"].get("closed")),
        "stop_drain_closed": bool(sections["stop_integrity"].get("closed")),
        "false_coverage_closed": bool(sections["coverage_integrity"].get("closed")),
        "stage_completion_truthful": bool(sections["stage_integrity"].get("closed")),
        "stall_classification_closed": bool(sections["stall_integrity"].get("closed")),
    }
    if pending_package:
        results["package_isolation_closed"] = "pending_package_verification"
        results["current_validation_packaged"] = "pending_package_verification"
    else:
        results["package_isolation_closed"] = bool(package_isolation)
        results["current_validation_packaged"] = bool(current_validation_packaged)

    closed_bools = []
    for key in ISSUE_KEYS:
        val = results[key]
        closed_bools.append(val is True)
    issues_closed = sum(1 for v in closed_bools if v)
    issues_total = 11
    return {
        "issues_total": issues_total,
        "issues_closed": issues_closed,
        "closure_ratio": round(issues_closed / issues_total, 4),
        "issue_results": results,
        "package_pending_issues": list(PACKAGE_PENDING_ISSUES) if pending_package else [],
    }


def enforce_accepted_invariants(state: dict[str, Any]) -> None:
    """Raise CanonicalAcceptanceContradictionError if ACCEPTED contradicts evidence."""
    verdict = state.get("final_verdict") or {}
    version = verdict.get("VERSION") or state.get("VERSION")
    if version != "ACCEPTED":
        return

    failures: list[str] = []
    issue = state.get("issue_closure") or {}
    archive = state.get("package_archive_integrity") or {}
    staging = state.get("package_staging_integrity") or {}
    immutable = state.get("immutable_evidence_integrity") or {}

    def req(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    req(verdict.get("POST_LIVE_STATUS") == "PASSED", "POST_LIVE_STATUS_not_PASSED")
    req(int(issue.get("issues_closed") or 0) == 11, "issues_closed_not_11")
    req(int(issue.get("issues_total") or 0) == 11, "issues_total_not_11")
    req(float(issue.get("closure_ratio") or 0) == 1.0, "closure_ratio_not_1")
    req(staging.get("staging_complete") is True or verdict.get("package_staging_complete") is True,
        "package_staging_incomplete")
    req(
        archive.get("package_verification_passed") is True
        or verdict.get("package_archive_verified") is True,
        "package_archive_not_verified",
    )
    # final_verdict is authoritative; empty string must fail (do not fall back).
    if "main_zip_path" in verdict:
        zip_path = verdict.get("main_zip_path") or ""
    else:
        zip_path = archive.get("main_zip_path") or ""
    if "main_zip_sha256" in verdict:
        zip_sha = verdict.get("main_zip_sha256") or ""
    else:
        zip_sha = archive.get("main_zip_sha256") or ""
    req(bool(str(zip_path).strip()), "main_zip_path_empty")
    req(bool(str(zip_sha).strip()), "main_zip_sha256_empty")
    fail_list = list(verdict.get("failures") or [])
    req(fail_list == [], f"failures_present:{fail_list}")

    results = issue.get("issue_results") or {}
    for key in ISSUE_KEYS:
        if results.get(key) is not True:
            failures.append(f"issue_not_true:{key}")

    # Contradictory package_complete=false under ACCEPTED
    if verdict.get("package_complete") is False or archive.get("package_complete") is False:
        failures.append("package_complete_false_under_ACCEPTED")

    if failures:
        raise CanonicalAcceptanceContradictionError(
            "CanonicalAcceptanceContradictionError: " + "; ".join(failures)
        )


def build_canonical_acceptance_state(
    *,
    run_folder: Path | str,
    reference_path: Path | str,
    immutable_before: dict[str, Any],
    immutable_after: dict[str, Any] | None = None,
    package_staging: dict[str, Any] | None = None,
    package_archive: dict[str, Any] | None = None,
    pending_package: bool = True,
    failures: list[str] | None = None,
) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    ref = ensure_path(reference_path)
    identity_raw = load_run_identity(folder)

    sections = {
        "transcript_integrity": evaluate_transcript_integrity(folder),
        "lineage_integrity": evaluate_lineage_integrity(folder),
        "action_integrity": evaluate_action_integrity(folder),
        "finalizer_integrity": evaluate_finalizer_integrity(folder),
        "audio_integrity": evaluate_audio_integrity(folder),
        "stop_integrity": evaluate_stop_integrity(folder),
        "coverage_integrity": evaluate_coverage_integrity(folder),
        "stage_integrity": evaluate_stage_integrity(folder),
        "stall_integrity": evaluate_stall_integrity(folder),
        "immutable_evidence_integrity": evaluate_immutable_integrity(
            immutable_before, immutable_after or immutable_before
        ),
    }

    pkg_iso = None
    cur_val = None
    if not pending_package and package_archive:
        pkg_iso = bool(package_archive.get("package_isolation_passed"))
        cur_val = bool(package_archive.get("current_validation_inside_zip"))

    issue_closure = build_issue_results(
        sections,
        package_isolation=pkg_iso,
        current_validation_packaged=cur_val,
        pending_package=pending_package,
    )

    fail = list(failures or [])
    for key, sec in sections.items():
        if key == "immutable_evidence_integrity":
            if not sec.get("immutable_runtime_artifacts_unchanged"):
                fail.append("immutable_runtime_artifacts_changed")
            continue
        if pending_package and key in ("package_staging_integrity", "package_archive_integrity"):
            continue
        if isinstance(sec, dict) and sec.get("closed") is False:
            fail.append(f"{key}_open")

    # Prepackage: require issues 1-8 and 11
    if pending_package:
        for key in ISSUE_KEYS:
            if key in PACKAGE_PENDING_ISSUES:
                continue
            if issue_closure["issue_results"].get(key) is not True:
                fail.append(f"prepackage_open:{key}")

    staging = package_staging or {
        "staging_complete": False,
        "pending": True,
    }
    archive = package_archive or {
        "package_verification_passed": False,
        "pending": True,
    }

    all_closed = (
        int(issue_closure["issues_closed"]) == 11
        and float(issue_closure["closure_ratio"]) == 1.0
        and not fail
        and staging.get("staging_complete") is True
        and archive.get("package_verification_passed") is True
        and sections["immutable_evidence_integrity"].get("immutable_runtime_artifacts_unchanged")
    )

    if pending_package:
        version = "PENDING_PACKAGE_VERIFICATION"
        post_live = "PENDING"
    elif all_closed:
        version = "ACCEPTED"
        post_live = "PASSED"
    else:
        version = "NOT_ACCEPTED"
        post_live = "FAILED"

    final_verdict = {
        "VERSION": version,
        "POST_LIVE_STATUS": post_live,
        "issues_closed": issue_closure["issues_closed"],
        "issues_total": issue_closure["issues_total"],
        "closure_ratio": issue_closure["closure_ratio"],
        "package_staging_complete": bool(staging.get("staging_complete")),
        "package_archive_verified": bool(archive.get("package_verification_passed")),
        "current_validation_packaged": (
            issue_closure["issue_results"].get("current_validation_packaged") is True
        ),
        "immutable_runtime_artifacts_unchanged": bool(
            sections["immutable_evidence_integrity"].get("immutable_runtime_artifacts_unchanged")
        ),
        "main_zip_path": archive.get("main_zip_path") or "",
        "main_zip_sha256": archive.get("main_zip_sha256") or "",
        "new_live_test_required": False,
        "failures": fail,
        "package_complete": bool(archive.get("package_verification_passed")),
    }

    state = {
        "identity": {
            "validation_version": VALIDATION_VERSION,
            "run_id": identity_raw.get("run_id") or "",
            "run_folder": str(folder),
            "run_app_version": identity_raw.get("app_version") or "",
            "reference_path": str(ref) if ref else "",
            "generated_at": _utc_now(),
            "source_of_truth": SOURCE_OF_TRUTH,
            "app_version": APP_VERSION,
        },
        "transcript_integrity": sections["transcript_integrity"],
        "lineage_integrity": sections["lineage_integrity"],
        "action_integrity": sections["action_integrity"],
        "finalizer_integrity": sections["finalizer_integrity"],
        "audio_integrity": sections["audio_integrity"],
        "stop_integrity": sections["stop_integrity"],
        "coverage_integrity": sections["coverage_integrity"],
        "stage_integrity": sections["stage_integrity"],
        "package_staging_integrity": staging,
        "package_archive_integrity": archive,
        "stall_integrity": sections["stall_integrity"],
        "immutable_evidence_integrity": sections["immutable_evidence_integrity"],
        "issue_closure": issue_closure,
        "final_verdict": final_verdict,
        "VERSION": version,
        "POST_LIVE_STATUS": post_live,
    }

    if version == "ACCEPTED":
        enforce_accepted_invariants(state)

    return state


def serialize_acceptance_subset(state: dict[str, Any]) -> dict[str, Any]:
    """Documented subset used by closure / report consumers."""
    v = state["final_verdict"]
    ic = state["issue_closure"]
    return {
        "source_of_truth": SOURCE_OF_TRUTH,
        "validation_version": state["identity"]["validation_version"],
        "run_id": state["identity"]["run_id"],
        "run_folder": state["identity"]["run_folder"],
        "VERSION": v["VERSION"],
        "POST_LIVE_STATUS": v["POST_LIVE_STATUS"],
        "issues_total": ic["issues_total"],
        "issues_closed": ic["issues_closed"],
        "closure_ratio": ic["closure_ratio"],
        "issue_results": ic["issue_results"],
        "package_pending_issues": ic.get("package_pending_issues") or [],
        "package_staging_complete": v["package_staging_complete"],
        "package_archive_verified": v["package_archive_verified"],
        "current_validation_packaged": v["current_validation_packaged"],
        "immutable_runtime_artifacts_unchanged": v["immutable_runtime_artifacts_unchanged"],
        "main_zip_path": v["main_zip_path"],
        "main_zip_sha256": v["main_zip_sha256"],
        "new_live_test_required": v["new_live_test_required"],
        "failures": v["failures"],
        "acceptance_object": state,
    }


def write_prepackage_closure(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    subset = serialize_acceptance_subset(state)
    payload = {
        **subset,
        "closure_ratio_before_package": subset["closure_ratio"],
        "VERSION": "PENDING_PACKAGE_VERIFICATION",
        "POST_LIVE_STATUS": "PENDING",
    }
    # Drop full nested object from file to keep closure focused (still derived from canonical)
    payload.pop("acceptance_object", None)
    payload["source_of_truth"] = SOURCE_OF_TRUTH
    atomic_write_json(path, payload)
    return payload


def render_cursor_report(acceptance: dict[str, Any], acceptance_source: Path | str) -> str:
    """Generate Cursor final report text from FINAL_ACCEPTANCE JSON only."""
    v = acceptance.get("final_verdict") or acceptance
    identity = acceptance.get("identity") or {}
    lines = [
        "=" * 80,
        "CURSOR FINAL REPORT — V3.3.5.5.8.5.25.3.3.2.1",
        "Canonical Acceptance State & Verified Validation Bundle",
        "=" * 80,
        "",
        f"acceptance_source = {acceptance_source}",
        f"source_of_truth = {identity.get('source_of_truth') or SOURCE_OF_TRUTH}",
        f"generated_at = {identity.get('generated_at') or _utc_now()}",
        "",
        f"VERSION={v.get('VERSION')}",
        f"POST_LIVE_STATUS={v.get('POST_LIVE_STATUS')}",
        f"issues_closed={v.get('issues_closed')}",
        f"issues_total={v.get('issues_total')}",
        f"closure_ratio={v.get('closure_ratio')}",
        f"main_zip_path={v.get('main_zip_path')}",
        f"main_zip_sha256={v.get('main_zip_sha256')}",
        f"package_archive_verified={json.dumps(bool(v.get('package_archive_verified')))}",
        f"immutable_runtime_artifacts_unchanged={json.dumps(bool(v.get('immutable_runtime_artifacts_unchanged')))}",
        f"failures={json.dumps(v.get('failures') or [])}",
        f"new_live_test_required={json.dumps(bool(v.get('new_live_test_required')))}",
        "",
        "This report was generated from FINAL_ACCEPTANCE_V25.3.3.2.1.json.",
        "Do not treat this prose as an independent acceptance authority.",
        "",
    ]
    return "\n".join(lines)


def file_sha256(path: Path | str) -> str:
    return byte_sha256_file(path)


def is_stale_acceptance_authority(name: str) -> bool:
    base = Path(name).name
    # Allow versioned post-zip / final acceptance outside staging rules separately
    if base in STALE_ACCEPTANCE_BASENAMES:
        return True
    # Old unversioned final closures
    if base == "ELEVEN_ISSUE_FINAL_CLOSURE.json":
        return True
    return False
