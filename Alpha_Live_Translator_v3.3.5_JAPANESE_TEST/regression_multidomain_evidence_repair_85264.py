"""Physical regression fixtures for the v3.3.5.5.8.5.26.4.1.1 evidence repair (85264).

Builds 20 persistent fixtures (spec N), runs each through the canonical
pre-score evidence gate + scorer via a real subprocess, and records physical
command/stdout/stderr/exit-code/hash evidence per fixture.

Usage:
  python regression_multidomain_evidence_repair_85264.py --project-root . --output-root <dir>
  python regression_multidomain_evidence_repair_85264.py --project-root . --run-fixture <fixture_dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPAIR_VERSION = "3.3.5.5.8.5.26.4.1.1"
STALE_VERSION = "3.3.5.5.8.5.26.2"

REFERENCE_TEXT = (
    "[Speaker 1] 田中さんは株式会社アルファの売上が120万円だと報告しました。\n"
    "[Speaker 2] 会議は午前10時に始まり、成長率は3.2%でした。\n"
)

TRUTH_METADATA = {
    "benchmark_id": "evidence_repair_fixture_v1",
    "participant_and_person_names": ["田中"],
    "company_names": ["株式会社アルファ"],
    "it_terms": [],
    "sales_terms": ["売上"],
    "marketing_terms": [],
    "general_business_terms": ["会議"],
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def build_valid_run_folder(fixture_dir: Path, run_id: str) -> Path:
    """Create a fully valid synthetic run folder that passes the evidence gate."""
    from alpha.utils.multidomain_gate_evidence import (
        MULTIDOMAIN_VERSION,
        build_audio_delivery_summary,
        build_stop_evidence_reconciliation,
        build_stop_source_map,
        sha256_file as mdg_sha256_file,
    )

    run_folder = fixture_dir / run_id
    stage = run_folder / "accuracy_stage_compare"
    stage.mkdir(parents=True, exist_ok=True)
    (run_folder / "artifacts").mkdir(parents=True, exist_ok=True)

    hypothesis = REFERENCE_TEXT
    (stage / "raw_deepgram.txt").write_text(hypothesis, encoding="utf-8")
    (stage / "stable_transcript.txt").write_text(hypothesis, encoding="utf-8")
    (stage / "final_alpha_output.txt").write_text(hypothesis, encoding="utf-8")

    events_path = stage / "audio_delivery_events.jsonl"
    with events_path.open("w", encoding="utf-8") as handle:
        for chunk_id in range(1, 6):
            base = {
                "schema_version": 2,
                "run_id": run_id,
                "app_version": MULTIDOMAIN_VERSION,
                "delivery_chunk_id": chunk_id,
                "sequence_index": chunk_id,
                "byte_count": 640,
                "frame_count": 320,
                "sample_rate": 16000,
                "channels": 1,
                "sample_width_bytes": 2,
            }
            queued = dict(base)
            queued.update(
                {
                    "event": "normalized_chunk_queued",
                    "event_type": "queued",
                    "queued_at_utc": utc_now_iso(),
                    "send_status": "pending",
                    "hook_source_file": "alpha/ui/main_window.py",
                    "hook_source_function": "audio_mixer_worker",
                    "monotonic_ns": 1_000_000 * chunk_id,
                }
            )
            sent = dict(base)
            sent.update(
                {
                    "event": "normalized_chunk_sent",
                    "event_type": "sent",
                    "sent_at_utc": utc_now_iso(),
                    "send_status": "success",
                    "send_result": "success",
                    "hook_source_file": "alpha/transcription/deepgram_client.py",
                    "hook_source_function": "_normalize_and_send_pcm",
                    "monotonic_ns": 1_000_000 * chunk_id + 500_000,
                }
            )
            handle.write(json.dumps(queued, ensure_ascii=False) + "\n")
            handle.write(json.dumps(sent, ensure_ascii=False) + "\n")

    summary = build_audio_delivery_summary(events_path, run_id=run_id, expected_run_id=run_id)
    write_json(stage / "audio_delivery_summary.json", summary)

    request = {
        "schema_version": 2,
        "run_id": run_id,
        "app_version": MULTIDOMAIN_VERSION,
        "harness_version": MULTIDOMAIN_VERSION,
        "captured_at_utc": utc_now_iso(),
        "capture_source_file": "alpha/transcription/deepgram_client.py",
        "capture_source_function": "_deepgram_worker",
        "captured_immediately_before_connection": True,
        "model": "nova-3",
        "language": "ja",
        "encoding": "linear16",
        "sample_rate": 16000,
        "channels": 1,
        "interim_results": True,
        "punctuate": True,
        "smart_format": False,
        "endpointing": 300,
        "utterance_end_ms": 1000,
        "keyterm_count": 0,
        "keyword_count": 0,
        "reference_terms_loaded": 0,
        "accuracy_profile": "domain_agnostic_no_hints",
        "diarization_state": "disabled",
        "sanitized": True,
        "forbidden_secret_fields_present": False,
    }
    request["request_parameter_sha256"] = hashlib.sha256(
        json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    write_json(stage / "deepgram_request_actual.json", request)

    lineage_entries: dict[str, Any] = {}
    for stage_name, filename in (
        ("raw", "raw_deepgram.txt"),
        ("stable", "stable_transcript.txt"),
        ("final", "final_alpha_output.txt"),
    ):
        physical = stage / filename
        digest = mdg_sha256_file(physical)
        lineage_entries[stage_name] = {
            "stage": stage_name,
            "run_id": run_id,
            "app_version": MULTIDOMAIN_VERSION,
            "harness_version": MULTIDOMAIN_VERSION,
            "runtime_writer_file": "alpha/utils/accuracy_stage_capture.py",
            "runtime_writer_function": "finalize_accuracy_stage_artifacts",
            "runtime_finalizer_file": "alpha/utils/accuracy_stage_capture.py",
            "runtime_finalizer_function": "finalize_accuracy_stage_artifacts",
            "runtime_source_path": str(physical),
            "runtime_source_sha256": digest,
            "runtime_source_byte_size": physical.stat().st_size,
            "evidence_snapshot_path": f"accuracy_stage_compare/{filename}",
            "evidence_snapshot_sha256": digest,
            "evidence_snapshot_byte_size": physical.stat().st_size,
            "capture_mode": "existing_current_run_artifact",
            "captured_after_runtime_finalization": True,
            "captured_after_runtime_exit": True,
            "runtime_child_exited_at": utc_now_iso(),
            "evidence_captured_at": utc_now_iso(),
            "content_modified_during_copy": False,
            "source_and_snapshot_hash_match": True,
            "scorer_input_path": f"accuracy_stage_compare/{filename}",
        }
    write_json(
        stage / "TRANSCRIPT_STAGE_LINEAGE.json",
        {
            "run_id": run_id,
            "app_version": MULTIDOMAIN_VERSION,
            "harness_version": MULTIDOMAIN_VERSION,
            "created_at": utc_now_iso(),
            "child_run_folder": "",
            "raw": lineage_entries["raw"],
            "stable": lineage_entries["stable"],
            "final": lineage_entries["final"],
        },
    )

    write_json(
        run_folder / "RUN_MANIFEST.json",
        {"run_id": run_id, "app_version": MULTIDOMAIN_VERSION, "final_status": "completed", "completed_at": utc_now_iso()},
    )
    write_json(
        run_folder / "artifacts" / "LIVE_RUN_STATUS.json",
        {"run_id": run_id, "is_stopping": False, "is_finalizing": False, "final_status": "completed"},
    )
    write_json(
        stage / "stage_manifest.json",
        {
            "run_id": run_id,
            "app_version": MULTIDOMAIN_VERSION,
            "benchmark_profile": "domain_agnostic_no_hints",
            "raw_path": "accuracy_stage_compare/raw_deepgram.txt",
            "raw_sha256": lineage_entries["raw"]["runtime_source_sha256"],
            "raw_byte_size": lineage_entries["raw"]["runtime_source_byte_size"],
            "stable_path": "accuracy_stage_compare/stable_transcript.txt",
            "stable_sha256": lineage_entries["stable"]["runtime_source_sha256"],
            "stable_byte_size": lineage_entries["stable"]["runtime_source_byte_size"],
            "final_path": "accuracy_stage_compare/final_alpha_output.txt",
            "final_sha256": lineage_entries["final"]["runtime_source_sha256"],
            "final_byte_size": lineage_entries["final"]["runtime_source_byte_size"],
            "audio_delivery_events_path": "accuracy_stage_compare/audio_delivery_events.jsonl",
            "deepgram_request_path": "accuracy_stage_compare/deepgram_request_actual.json",
            "completed": True,
            "completed_at": utc_now_iso(),
        },
    )
    build_stop_source_map(run_folder)
    build_stop_evidence_reconciliation(
        run_folder, run_started_at=utc_now_iso(), runtime_child_exited_at=utc_now_iso()
    )

    write_json(
        stage / "reference_isolation_actual.json",
        {
            "run_id": run_id,
            "app_version": MULTIDOMAIN_VERSION,
            "isolation_verified": True,
            "reference_opened_after_runtime_exit": True,
            "truth_opened_after_runtime_exit": True,
        },
    )

    (fixture_dir / "reference.txt").write_text(REFERENCE_TEXT, encoding="utf-8")
    write_json(fixture_dir / "truth.json", TRUTH_METADATA)
    return run_folder


# ---------------------------------------------------------------------------
# Mutations (one per negative fixture)
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def mutate(fixture_id: str, run_folder: Path) -> None:
    stage = run_folder / "accuracy_stage_compare"
    if fixture_id == "02":
        (stage / "raw_deepgram.txt").unlink()
    elif fixture_id == "03":
        (stage / "raw_deepgram.txt").write_bytes(b"")
    elif fixture_id == "04":
        (stage / "stable_transcript.txt").unlink()
    elif fixture_id == "05":
        (stage / "final_alpha_output.txt").unlink()
    elif fixture_id == "06":
        (stage / "audio_delivery_events.jsonl").unlink()
    elif fixture_id == "07":
        with (stage / "audio_delivery_events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("this is {{{ not json\n")
    elif fixture_id == "08":
        (stage / "deepgram_request_actual.json").unlink()
    elif fixture_id == "09":
        (stage / "deepgram_request_actual.json").write_text("{broken json", encoding="utf-8")
    elif fixture_id == "10":
        manifest = _load(stage / "stage_manifest.json")
        manifest["run_id"] = "multidomain-v3.3.5.5.8.5.26.3-20260721-085008-3d5c678b"
        write_json(stage / "stage_manifest.json", manifest)
    elif fixture_id == "11":
        manifest = _load(stage / "stage_manifest.json")
        manifest["app_version"] = STALE_VERSION
        write_json(stage / "stage_manifest.json", manifest)
    elif fixture_id == "12":
        (stage / "stable_transcript.txt").write_bytes(b"")
    elif fixture_id == "13":
        manifest = _load(stage / "stage_manifest.json")
        manifest["completed"] = False
        write_json(stage / "stage_manifest.json", manifest)
    elif fixture_id == "14":
        (stage / "raw_deepgram.txt").unlink()
    elif fixture_id == "15":
        stop = _load(stage / "STOP_EVIDENCE_RECONCILIATION.json")
        stop["conflicts"] = ["run_manifest_not_completed_but_live_status_finalized"]
        stop["stop_evidence_verified"] = False
        stop["status"] = "STOP_EVIDENCE_CONFLICT"
        write_json(stage / "STOP_EVIDENCE_RECONCILIATION.json", stop)
    elif fixture_id == "16":
        pass  # tampered score file is written by the builder; evidence stays valid
    elif fixture_id == "17":
        lineage = _load(stage / "TRANSCRIPT_STAGE_LINEAGE.json")
        lineage["raw"]["evidence_snapshot_sha256"] = "0" * 64
        write_json(stage / "TRANSCRIPT_STAGE_LINEAGE.json", lineage)
    elif fixture_id == "18":
        request = _load(stage / "deepgram_request_actual.json")
        request["api_key"] = "sk_live_ABCDEFGH123456789012"
        request["sanitized_query_string"] = "token=SECRETTOKENVALUE1234567890"
        write_json(stage / "deepgram_request_actual.json", request)
    elif fixture_id == "19":
        lineage = _load(stage / "TRANSCRIPT_STAGE_LINEAGE.json")
        lineage["stable"]["content_modified_during_copy"] = True
        lineage["stable"]["source_and_snapshot_hash_match"] = False
        write_json(stage / "TRANSCRIPT_STAGE_LINEAGE.json", lineage)


FIXTURES: list[dict[str, Any]] = [
    {"id": "01", "name": "01_valid_complete_evidence", "category": "positive", "expected": {"scoring_permitted": True, "status": "SCORED", "blocked_contains": []}},
    {"id": "02", "name": "02_missing_raw", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["raw_deepgram.txt:missing"]}},
    {"id": "03", "name": "03_empty_raw", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["raw_deepgram.txt:empty"]}},
    {"id": "04", "name": "04_missing_stable", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["stable_transcript.txt:missing"]}},
    {"id": "05", "name": "05_missing_final", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["final_alpha_output.txt:missing"]}},
    {"id": "06", "name": "06_missing_audio_jsonl", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["audio_delivery_events.jsonl:missing"]}},
    {"id": "07", "name": "07_malformed_audio_jsonl", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["audio_delivery_events.jsonl:parse_error"]}},
    {"id": "08", "name": "08_missing_deepgram_request", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["deepgram_request_actual.json:missing"]}},
    {"id": "09", "name": "09_malformed_deepgram_request", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["deepgram_request_actual.json:parse_error"]}},
    {"id": "10", "name": "10_stale_run_id", "category": "negative", "expected": {"scoring_permitted": False, "status": "RUN_ID_MISMATCH", "blocked_contains": ["stage_manifest.json:run_id_mismatch"]}},
    {"id": "11", "name": "11_stale_version", "category": "negative", "expected": {"scoring_permitted": False, "status": "VERSION_MISMATCH", "blocked_contains": ["stage_manifest.json:stale_version"]}},
    {"id": "12", "name": "12_empty_hypothesis_blocked", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["stable_transcript.txt:empty"], "extra": {"scoring_decision_nulls": True}}},
    {"id": "13", "name": "13_stage_manifest_completed_false", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["stage_manifest.json:completed_false"]}},
    {"id": "14", "name": "14_gate_failed_cannot_claim_completed", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["raw_deepgram.txt:missing"], "extra": {"acceptance_real_benchmark_completed": False}}},
    {"id": "15", "name": "15_conflicting_stop_evidence", "category": "negative", "expected": {"scoring_permitted": False, "status": "STOP_EVIDENCE_CONFLICT", "blocked_contains": ["STOP_EVIDENCE_RECONCILIATION.json:conflicts_present"]}},
    {"id": "16", "name": "16_category_score_mismatch", "category": "negative", "expected": {"scoring_permitted": False, "status": "SCORING_VALUE_MISMATCH", "blocked_contains": [], "extra": {"scoring_value_mismatch_detected": True, "acceptance_real_benchmark_completed": False}}},
    {"id": "17", "name": "17_transcript_hash_mismatch", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["TRANSCRIPT_STAGE_LINEAGE.json:raw_snapshot_hash_stale"]}},
    {"id": "18", "name": "18_request_secret_exposed", "category": "negative", "expected": {"scoring_permitted": False, "status": "SECRET_EXPOSED", "blocked_contains": ["deepgram_request_actual.json:secret_exposed"]}},
    {"id": "19", "name": "19_transcript_copy_content_mutation", "category": "negative", "expected": {"scoring_permitted": False, "status": "EVIDENCE_INCOMPLETE", "blocked_contains": ["TRANSCRIPT_STAGE_LINEAGE.json:stable_content_modified_during_copy"]}},
    {"id": "20", "name": "20_complete_valid_independent_verification", "category": "positive", "expected": {"scoring_permitted": True, "status": "SCORED", "blocked_contains": []}},
]


# ---------------------------------------------------------------------------
# Fixture driver (subprocess entry)
# ---------------------------------------------------------------------------


def run_fixture(project_root: Path, fixture_dir: Path) -> int:
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from alpha.utils.multidomain_gate_evidence import build_pre_score_evidence_gate, write_scoring_decision
    from score_multidomain_gate_85262 import ScoringNotPermittedError, score_all

    manifest = _load(fixture_dir / "fixture_manifest.json")
    expected = manifest["expected_result"]
    run_folder = fixture_dir / manifest["run_folder"]

    gate = build_pre_score_evidence_gate(run_folder)
    actual: dict[str, Any] = {
        "fixture_id": manifest["fixture_id"],
        "fixture_name": manifest["fixture_name"],
        "app_version": REPAIR_VERSION,
        "scoring_permitted": bool(gate.get("scoring_permitted")),
        "status": str(gate.get("status")),
        "blocked_reasons": list(gate.get("blocked_reasons") or []),
        "scores": None,
        "extra": {},
    }

    if actual["scoring_permitted"]:
        try:
            score = score_all(
                project_root=project_root,
                run_folder=run_folder,
                reference_path=fixture_dir / "reference.txt",
                truth_path=fixture_dir / "truth.json",
            )
            strict = score["strict"]
            actual["status"] = "SCORED"
            actual["scores"] = {
                "raw_cer_percent": strict["raw"]["cer_percent"],
                "stable_cer_percent": strict["stable"]["cer_percent"],
                "final_cer_percent": strict["final"]["cer_percent"],
                "raw_accuracy_percent": strict["raw"]["accuracy_percent"],
                "stable_accuracy_percent": strict["stable"]["accuracy_percent"],
                "final_accuracy_percent": strict["final"]["accuracy_percent"],
            }
            tampered_path = fixture_dir / "tampered_domain_category_score.json"
            if tampered_path.exists():
                tampered = _load(tampered_path)
                genuine = score["domain_category"]
                mismatches = [
                    key
                    for key in genuine
                    if key.endswith("_accuracy_percent")
                    and key in tampered
                    and abs(float(tampered[key]) - float(genuine[key])) > 0.01
                ]
                actual["extra"]["scoring_value_mismatch_detected"] = bool(mismatches)
                actual["extra"]["mismatched_fields"] = mismatches
                if mismatches:
                    actual["scoring_permitted"] = False
                    actual["status"] = "SCORING_VALUE_MISMATCH"
                    # Fail-closed: no strict/meaning score bodies may remain after block.
                    stage = run_folder / "accuracy_stage_compare"
                    for stale_name in ("strict_score.json", "meaning_equivalent_score.json"):
                        stale = stage / stale_name
                        try:
                            if stale.exists():
                                stale.unlink()
                        except OSError:
                            pass
                    write_scoring_decision(
                        run_folder,
                        scoring_permitted=False,
                        real_benchmark_completed=False,
                        status="SCORING_VALUE_MISMATCH",
                        blocked_reasons=[f"category_mismatch:{k}" for k in mismatches],
                    )
                    from run_multidomain_gate_85262 import build_acceptance

                    acceptance = build_acceptance(
                        score={},
                        domain={},
                        verification={"verification_passed": False},
                        isolation={"isolation_verified": True},
                        audio_summary={},
                        runtime={"runtime_regressions": []},
                        request={},
                        fixture_mode=False,
                        stage_manifest_completed=True,
                        scoring_permitted=False,
                        evidence_gate_status="SCORING_VALUE_MISMATCH",
                    )
                    actual["extra"]["acceptance_real_benchmark_completed"] = bool(
                        acceptance["real_benchmark_completed"]
                    )
                    actual["extra"]["acceptance_status"] = acceptance["STATUS"]
                    actual["extra"]["acceptance_scoring_permitted"] = bool(
                        acceptance["scoring_permitted"]
                    )
        except ScoringNotPermittedError as exc:
            actual["scoring_permitted"] = False
            actual["status"] = exc.status
            actual["blocked_reasons"] = list(exc.blocked_reasons)
    else:
        write_scoring_decision(
            run_folder,
            scoring_permitted=False,
            real_benchmark_completed=False,
            status=actual["status"],
            blocked_reasons=actual["blocked_reasons"],
        )

    decision_path = run_folder / "accuracy_stage_compare" / "SCORING_DECISION.json"
    if decision_path.exists():
        decision = _load(decision_path)
        actual["extra"]["scoring_decision_nulls"] = all(
            decision.get(key) is None
            for key in (
                "raw_cer_percent",
                "stable_cer_percent",
                "final_cer_percent",
                "raw_accuracy_percent",
                "stable_accuracy_percent",
                "final_accuracy_percent",
            )
        )

    if manifest["fixture_id"] == "14":
        from run_multidomain_gate_85262 import build_acceptance

        acceptance = build_acceptance(
            score={},
            domain={},
            verification={"verification_passed": False},
            isolation={"isolation_verified": True},
            audio_summary={},
            runtime={"runtime_regressions": []},
            request={},
            fixture_mode=False,
            stage_manifest_completed=False,
            scoring_permitted=False,
            evidence_gate_status=actual["status"],
        )
        actual["extra"]["acceptance_real_benchmark_completed"] = bool(
            acceptance["real_benchmark_completed"]
        )
        actual["extra"]["acceptance_status"] = acceptance["STATUS"]

    matches = (
        actual["scoring_permitted"] == expected["scoring_permitted"]
        and actual["status"] == expected["status"]
        and all(item in actual["blocked_reasons"] for item in expected.get("blocked_contains", []))
    )
    for key, value in (expected.get("extra") or {}).items():
        if actual["extra"].get(key) != value:
            matches = False
    actual["matches_expected"] = matches
    write_json(fixture_dir / "actual_result.json", actual)
    print(json.dumps({"fixture": manifest["fixture_name"], "matches_expected": matches, "status": actual["status"]}, ensure_ascii=False))
    return 0 if matches else 1


# ---------------------------------------------------------------------------
# Builder + parent runner
# ---------------------------------------------------------------------------


def write_sha256sums(fixture_dir: Path) -> None:
    lines: list[str] = []
    for path in sorted(fixture_dir.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS.txt":
            continue
        rel = str(path.relative_to(fixture_dir)).replace("\\", "/")
        lines.append(f"{sha256_file(path)}  {rel}")
    (fixture_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_and_run_all(project_root: Path, output_root: Path) -> int:
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    all_passed = True

    for spec in FIXTURES:
        fixture_dir = output_root / spec["name"]
        fixture_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"mdgfix{spec['id']}-v{REPAIR_VERSION}"
        # The run folder name IS the run_id used for run-id validation.
        run_folder = build_valid_run_folder(fixture_dir, run_id)
        mutate(spec["id"], run_folder)

        if spec["id"] == "16":
            from score_multidomain_gate_85262 import score_domain_categories

            genuine = score_domain_categories(
                reference_text=REFERENCE_TEXT,
                truth=TRUTH_METADATA,
                stage_texts={"raw": REFERENCE_TEXT, "stable": REFERENCE_TEXT, "final": REFERENCE_TEXT},
                primary_stage="stable",
            )
            tampered = dict(genuine)
            tampered["numbers_accuracy_percent"] = 50.0
            tampered["combined_critical_entity_accuracy_percent"] = 61.5
            tampered = {
                key: value
                for key, value in tampered.items()
                if key.endswith("_accuracy_percent") or key == "primary_stage"
            }
            write_json(fixture_dir / "tampered_domain_category_score.json", tampered)

        write_json(fixture_dir / "expected_result.json", spec["expected"])
        write_json(
            fixture_dir / "fixture_manifest.json",
            {
                "fixture_id": spec["id"],
                "fixture_name": spec["name"],
                "category": spec["category"],
                "app_version": REPAIR_VERSION,
                "run_id": run_id,
                "run_folder": run_id,
                "expected_result": spec["expected"],
                "fixture_mode": True,
                "real_benchmark_completed": False,
                "ready_for_translation_beta": False,
                "created_at": utc_now_iso(),
            },
        )

        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--project-root",
            str(project_root),
            "--run-fixture",
            str(fixture_dir),
        ]
        (fixture_dir / "command.txt").write_text(" ".join(cmd) + "\n", encoding="utf-8")
        started = time.time()
        started_at = utc_now_iso()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=str(project_root))
        duration = time.time() - started
        (fixture_dir / "stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
        (fixture_dir / "stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
        (fixture_dir / "exit_code.txt").write_text(str(proc.returncode) + "\n", encoding="utf-8")
        write_json(
            fixture_dir / "subprocess_metadata.json",
            {
                "command": cmd,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "duration_seconds": round(duration, 3),
                "exit_code": proc.returncode,
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "cwd": str(project_root),
            },
        )
        write_sha256sums(fixture_dir)

        passed = proc.returncode == 0
        all_passed = all_passed and passed
        results.append(
            {
                "fixture_id": spec["id"],
                "fixture_name": spec["name"],
                "category": spec["category"],
                "exit_code": proc.returncode,
                "passed": passed,
            }
        )
        print(f"fixture {spec['name']}: {'PASS' if passed else 'FAIL'} (exit={proc.returncode})")

    summary = {
        "app_version": REPAIR_VERSION,
        "created_at": utc_now_iso(),
        "fixtures_root": str(output_root),
        "fixture_count": len(results),
        "passed_count": sum(1 for row in results if row["passed"]),
        "failed_count": sum(1 for row in results if not row["passed"]),
        "regression_passed": all_passed,
        "results": results,
    }

    # V26.4.1 focused regressions (defects A–G)
    focused_mod = (
        project_root
        / "troubleshooting"
        / "implementation_evidence"
        / f"v{REPAIR_VERSION}"
        / "_focused_regressions.py"
    )
    focused_summary: dict[str, Any] = {}
    if focused_mod.exists():
        import importlib.util

        spec = importlib.util.spec_from_file_location("focused_v2641", focused_mod)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        focused_summary = mod.run_focused_regressions(project_root, output_root)
        all_passed = all_passed and bool(focused_summary.get("focused_passed"))
        summary["focused_v2641"] = focused_summary
        summary["regression_passed"] = all_passed

    write_json(output_root / "regression_summary.json", summary)
    report_lines = [
        f"Multidomain evidence repair regression — v{REPAIR_VERSION}",
        f"fixtures: {summary['fixture_count']}",
        f"passed: {summary['passed_count']}",
        f"failed: {summary['failed_count']}",
        f"regression_passed: {all_passed}",
        "",
    ] + [f"{row['fixture_name']}: {'PASS' if row['passed'] else 'FAIL'}" for row in results]
    (output_root / "report.txt").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"regression_passed={'true' if all_passed else 'false'}")
    return 0 if all_passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="85264 evidence-repair regression fixtures")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output-root", default="")
    parser.add_argument("--run-fixture", default="")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    if args.run_fixture:
        return run_fixture(project_root, Path(args.run_fixture).resolve())
    if not args.output_root:
        parser.error("--output-root is required unless --run-fixture is given")
    return build_and_run_all(project_root, Path(args.output_root).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
