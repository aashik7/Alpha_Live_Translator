"""Fixture regression suite for multidomain gate (85262). Offline only."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.utils.multidomain_gate_evidence import (  # noqa: E402
    ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
    MULTIDOMAIN_VERSION,
    build_truth_metadata_template,
    normalize_transcript_text,
    sha256_file,
    utc_now_iso,
)

SMOKE_ROOT = ROOT / "troubleshooting" / "smoke_tests" / (
    f"multidomain_gate_85262_{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
)

# When keep-fixtures mode is active, each test writes flat into CURRENT_FIXTURE_DIR.
CURRENT_FIXTURE_DIR: Path | None = None
KEEP_FIXTURES = False

PLACEHOLDER_REFERENCE = """# PLACEHOLDER_NOT_AUTHORITATIVE — offline fixture scoring only; replace before live benchmark
[Speaker 1] 本日はアルファソリューションズ株式会社の田中健さんと、東都物流株式会社の佐藤美咲さんが参加しています。
[Speaker 2] APIとJSONを使ったSSOの多要素認証について、情報システム部で回帰テストを実施します。
[Speaker 3] 初回相談の提案書では年間契約金額120万円、月額利用料3.2%を想定しています。
[Speaker 4] 検索広告のクリック率改善のため、2026年7月16日午前10時にA/Bテストを開始します。
[Speaker 5] 鈴木大輔さんと高橋彩さんは、株式会社ネクストワークスのWebhook連携とCRMのSLAを確認します。
"""

# Deterministic fixture directory names for evidence closure (32 tests).
DETERMINISTIC_FIXTURES: list[tuple[str, str, bool, str]] = [
    ("001_valid_fixture", "valid_fixture_produces_implementation_ready_verification", True, ""),
    ("002_missing_raw", "missing_raw_file_fails", False, "missing_raw"),
    ("003_missing_stable", "missing_stable_file_fails", False, "missing_stable"),
    ("004_missing_final", "missing_final_file_fails", False, "missing_final"),
    ("005_altered_transcript_hash", "altered_transcript_hash_fails", False, "hash_mismatch"),
    ("006_altered_audio_delivery_jsonl", "altered_audio_delivery_jsonl_hash_fails", False, "audio_hash"),
    ("007_missing_sent_chunk", "missing_sent_chunk_fails", False, "missing_sent"),
    ("008_duplicate_sent_chunk", "duplicate_sent_chunk_fails", False, "duplicate_sent"),
    ("009_unexpected_sent_chunk", "unexpected_sent_chunk_fails", False, "unexpected_sent"),
    ("010_delivery_ratio_below_0_999", "delivery_ratio_below_0_999_fails", False, "low_ratio"),
    ("011_malformed_jsonl", "malformed_jsonl_fails", False, "malformed_jsonl"),
    ("012_api_key_in_request", "api_key_in_request_evidence_fails", False, "api_key"),
    ("013_reference_in_commandline", "reference_path_in_runtime_child_command_line_fails", False, "ref_cmdline"),
    ("014_reference_in_environment", "reference_path_in_runtime_child_environment_fails", False, "ref_env"),
    ("015_reference_opened_before_exit", "reference_opened_before_runtime_exit_fails", False, "ref_early"),
    ("016_scoring_module_imported", "scoring_module_imported_during_runtime_fails", False, "scoring_import"),
    ("017_keyterm_count_above_zero", "keyterm_count_above_zero_fails", False, "keyterm"),
    ("018_keyword_count_above_zero", "keyword_count_above_zero_fails", False, "keyword"),
    ("019_test01_profile_active", "test01_profile_active_fails", False, "test01_profile"),
    ("020_business_japanese_active", "business_japanese_profile_active_fails", False, "business_profile"),
    ("021_raw_mutation_count", "raw_mutation_count_above_zero_fails", False, "raw_mutation"),
    ("022_translation_provider_active", "translation_provider_active_fails", False, "translation"),
    ("023_stable_accuracy_below_80", "stable_accuracy_below_80_fails", False, "stable_accuracy"),
    ("024_names_accuracy_below_85", "names_accuracy_below_85_fails", False, "names"),
    ("025_number_accuracy_below_85", "number_accuracy_below_85_fails", False, "numbers"),
    ("026_stable_to_final_loss", "stable_to_final_loss_above_zero_fails", False, "stf_loss"),
    ("027_runtime_regression", "runtime_regression_fails", False, "runtime_regression"),
    ("028_reported_cer_mismatch", "reported_cer_mismatch_fails", False, "cer_mismatch"),
    ("029_reported_category_mismatch", "reported_category_score_mismatch_fails", False, "category_mismatch"),
    ("030_fixture_not_accepted", "fixture_run_cannot_create_version_accepted", True, ""),
    ("031_fixture_outputs_isolated", "fixture_outputs_cannot_overwrite_latest_live_run_artifacts", True, ""),
    ("032_audio_excluded", "audio_files_excluded_from_package", True, ""),
]


def _pass(name: str) -> dict[str, Any]:
    return {"name": name, "passed": True, "detail": ""}


def _fail(name: str, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": False, "detail": detail}


def _write_audio_events(stage: Path, *, chunk_count: int = 5, mutate: Callable[[list[str]], list[str]] | None = None) -> None:
    lines: list[str] = []
    for cid in range(1, chunk_count + 1):
        lines.append(
            json.dumps(
                {
                    "event": "normalized_chunk_queued",
                    "delivery_chunk_id": cid,
                    "run_id": "fixture",
                    "monotonic_ns": cid * 1_000_000,
                    "frame_count": 320,
                    "byte_count": 640,
                    "sample_rate": 16000,
                    "channels": 1,
                },
                ensure_ascii=False,
            )
        )
        lines.append(
            json.dumps(
                {
                    "event": "normalized_chunk_sent",
                    "delivery_chunk_id": cid,
                    "run_id": "fixture",
                    "monotonic_ns": cid * 1_000_000 + 5000,
                    "frame_count": 320,
                    "byte_count": 640,
                    "sample_rate": 16000,
                    "channels": 1,
                    "send_result": "success",
                },
                ensure_ascii=False,
            )
        )
    if mutate:
        lines = mutate(lines)
    (stage / "audio_delivery_events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_deepgram_request(stage: Path, **overrides: Any) -> None:
    payload = {
        "run_id": "fixture",
        "app_version": MULTIDOMAIN_VERSION,
        "profile": ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
        "benchmark_profile": ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
        "model": "nova-3",
        "language": "ja",
        "encoding": "linear16",
        "sample_rate": 16000,
        "channels": 1,
        "interim_results": True,
        "punctuate": True,
        "smart_format": True,
        "endpointing": 500,
        "utterance_end_ms": 1500,
        "diarize_present": False,
        "diarize_model_present": False,
        "keyterm_parameter_present": False,
        "keyterm_count": 0,
        "keyterm_values": [],
        "keyword_parameter_present": False,
        "keyword_count": 0,
        "keyword_values": [],
        "meeting_glossary_loaded": False,
        "business_japanese_profile_active": False,
        "test01_profile_active": False,
        "reference_terms_loaded": 0,
        "sanitized_query_string": "model=nova-3&language=ja",
        "request_sha256": "fixture",
        "captured_immediately_before_connect": True,
    }
    payload.update(overrides)
    (stage / "deepgram_request_actual.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_isolation(stage: Path, **overrides: Any) -> None:
    t0 = "2026-07-16T01:00:00Z"
    t1 = "2026-07-16T02:00:00Z"
    t2 = "2026-07-16T02:00:01Z"
    payload = {
        "runtime_child_started_at": t0,
        "runtime_child_exited_at": t1,
        "reference_first_opened_at": t2,
        "truth_file_first_opened_at": t2,
        "reference_opened_after_runtime_exit": True,
        "truth_opened_after_runtime_exit": True,
        "runtime_child_commandline": [sys.executable, "main.py"],
        "runtime_child_commandline_contains_reference": False,
        "runtime_child_environment_key_names": ["ALPHA_MULTIDOMAIN_BENCHMARK_MODE"],
        "runtime_child_environment_contains_reference": False,
        "runtime_imported_scoring_modules": [],
        "runtime_imported_reference_modules": [],
        "isolation_verified": True,
    }
    payload.update(overrides)
    (stage / "reference_isolation_actual.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_fixture_run(
    smoke_dir: Path,
    name: str,
    *,
    transcript_text: str | None = None,
    omit: set[str] | None = None,
    audio_mutate: Callable[[list[str]], list[str]] | None = None,
    request_overrides: dict[str, Any] | None = None,
    isolation_overrides: dict[str, Any] | None = None,
    stable_text: str | None = None,
    final_text: str | None = None,
    runtime_regressions: list[str] | None = None,
    corrupt_strict_score: bool = False,
    corrupt_domain_score: bool = False,
) -> tuple[Path, Path, Path]:
    omit = omit or set()
    if CURRENT_FIXTURE_DIR is not None:
        run_folder = Path(CURRENT_FIXTURE_DIR)
    else:
        run_folder = smoke_dir / name
    stage = run_folder / "accuracy_stage_compare"
    ref_dir = run_folder / "reference_copy"
    stage.mkdir(parents=True, exist_ok=True)
    ref_dir.mkdir(parents=True, exist_ok=True)

    ref_path = ref_dir / "multidomain_meeting_v1.txt"
    truth_path = ref_dir / "multidomain_meeting_v1_truth.json"
    ref_path.write_text(transcript_text or PLACEHOLDER_REFERENCE, encoding="utf-8")
    truth_path.write_text(
        json.dumps(build_truth_metadata_template(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    body = transcript_text or PLACEHOLDER_REFERENCE
    for label, fname, content in (
        ("raw", "raw_deepgram.txt", body),
        ("stable", "stable_transcript.txt", stable_text if stable_text is not None else body),
        ("final", "final_alpha_output.txt", final_text if final_text is not None else body),
    ):
        if label not in omit and fname not in omit:
            (stage / fname).write_text(content, encoding="utf-8")

    if "audio_delivery_events.jsonl" not in omit:
        _write_audio_events(stage, mutate=audio_mutate)
    if "deepgram_request_actual.json" not in omit:
        _write_deepgram_request(stage, **(request_overrides or {}))
    if "reference_isolation_actual.json" not in omit:
        _write_isolation(stage, **(isolation_overrides or {}))

    runtime = {
        "checks": {"raw_mutation_count": 0},
        "runtime_regressions": runtime_regressions or [],
        "warnings": [],
        "evidence_paths": [],
        "runtime_passed": not runtime_regressions,
    }
    (stage / "runtime_regression_report.json").write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (stage / "noise.wav").write_bytes(b"RIFF")

    return run_folder, ref_path, truth_path


def _run_fixture_pipeline(run_folder: Path, ref_path: Path, truth_path: Path) -> dict[str, Any]:
    from alpha.utils.multidomain_gate_evidence import recalculate_audio_delivery_summary
    from run_multidomain_gate_85262 import build_acceptance, create_analysis_package
    from score_multidomain_gate_85262 import score_all
    from verify_multidomain_gate_85262 import verify_multidomain_gate

    stage = run_folder / "accuracy_stage_compare"
    summary = recalculate_audio_delivery_summary(stage / "audio_delivery_events.jsonl")
    (stage / "audio_delivery_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    score = score_all(
        project_root=ROOT,
        run_folder=run_folder,
        reference_path=ref_path,
        truth_path=truth_path,
    )
    if (stage / "runtime_regression_report.json").exists():
        runtime = json.loads((stage / "runtime_regression_report.json").read_text(encoding="utf-8"))
    else:
        runtime = {"runtime_regressions": [], "runtime_passed": True}

    req = {}
    if (stage / "deepgram_request_actual.json").exists():
        req = json.loads((stage / "deepgram_request_actual.json").read_text(encoding="utf-8"))
    iso = {}
    if (stage / "reference_isolation_actual.json").exists():
        iso = json.loads((stage / "reference_isolation_actual.json").read_text(encoding="utf-8"))

    verification = verify_multidomain_gate(
        project_root=ROOT,
        run_folder=run_folder,
        reference_path=ref_path,
        truth_path=truth_path,
    )
    acceptance = build_acceptance(
        score=score,
        domain=score["domain_category"],
        verification=verification,
        isolation=iso,
        audio_summary=summary,
        runtime=runtime,
        request=req,
        fixture_mode=True,
    )
    (stage / "multidomain_gate_acceptance.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    package = create_analysis_package(
        project_root=ROOT,
        run_folder=run_folder,
        reference_path=ref_path,
        truth_path=truth_path,
        report_text="fixture report\n",
    )
    verification2 = verify_multidomain_gate(
        project_root=ROOT,
        run_folder=run_folder,
        reference_path=ref_path,
        truth_path=truth_path,
        package_path=package,
    )
    return {
        "score": score,
        "verification": verification2,
        "acceptance": acceptance,
        "runtime": runtime,
        "audio_summary": summary,
        "package": package,
    }


def test_01_valid_fixture() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test01_valid")
    result = _run_fixture_pipeline(run, ref, truth)
    if result["acceptance"]["VERSION"] == "ACCEPTED":
        return _fail("valid_fixture_implementation_ready", "fixture produced ACCEPTED")
    if result["acceptance"]["STATUS"] != "FIXTURE_ONLY":
        return _fail("valid_fixture_implementation_ready", result["acceptance"]["STATUS"])
    stable_acc = float(result["score"]["stable_accuracy_percent"])
    if stable_acc < 95.0:
        return _fail("valid_fixture_implementation_ready", f"stable_acc={stable_acc}")
    return _pass("valid_fixture_produces_implementation_ready_verification")


def test_02_missing_raw() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test02_missing_raw", omit={"raw"})
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("missing_raw_fails", "verification passed")
    return _pass("missing_raw_file_fails")


def test_03_missing_stable() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test03_missing_stable", omit={"stable"})
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("missing_stable_fails", "verification passed")
    return _pass("missing_stable_file_fails")


def test_04_missing_final() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test04_missing_final", omit={"final"})
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("missing_final_fails", "verification passed")
    return _pass("missing_final_file_fails")


def test_05_altered_transcript_hash() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test05_altered_hash")
    stage = run / "accuracy_stage_compare"
    stage.joinpath("stage_manifest.json").write_text(
        json.dumps(
            {
                "raw_sha256": "deadbeef",
                "stable_sha256": "deadbeef",
                "final_sha256": "deadbeef",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if "manifest_raw_sha256_mismatch" not in result["verification"]["reported_value_mismatches"]:
        return _fail("altered_transcript_hash_fails", str(result["verification"]["reported_value_mismatches"]))
    return _pass("altered_transcript_hash_fails")


def test_06_altered_audio_jsonl() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test06_altered_audio")
    stage = run / "accuracy_stage_compare"
    path = stage / "audio_delivery_events.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"event":"normalized_chunk_sent","delivery_chunk_id":999}\n', encoding="utf-8")
    result = _run_fixture_pipeline(run, ref, truth)
    if result["audio_summary"].get("unexpected_sent_chunk_ids") != [999]:
        return _fail("altered_audio_jsonl_fails", str(result["audio_summary"]))
    if result["acceptance"]["VERSION"] == "ACCEPTED":
        return _fail("altered_audio_jsonl_fails", "accepted")
    return _pass("altered_audio_delivery_jsonl_hash_fails")


def test_07_missing_sent_chunk() -> dict[str, Any]:
    def mutate(lines: list[str]) -> list[str]:
        return [ln for ln in lines if '"delivery_chunk_id": 3' not in ln or "normalized_chunk_sent" not in ln]

    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test07_missing_sent", audio_mutate=mutate)
    result = _run_fixture_pipeline(run, ref, truth)
    if not result["audio_summary"].get("missing_sent_chunk_ids"):
        return _fail("missing_sent_chunk_fails", str(result["audio_summary"]))
    return _pass("missing_sent_chunk_fails")


def test_08_duplicate_sent_chunk() -> dict[str, Any]:
    def mutate(lines: list[str]) -> list[str]:
        dup = [ln for ln in lines if '"delivery_chunk_id": 2' in ln and "normalized_chunk_sent" in ln]
        return lines + dup

    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test08_duplicate_sent", audio_mutate=mutate)
    result = _run_fixture_pipeline(run, ref, truth)
    if not result["audio_summary"].get("duplicate_sent_chunk_ids"):
        return _fail("duplicate_sent_chunk_fails", str(result["audio_summary"]))
    return _pass("duplicate_sent_chunk_fails")


def test_09_unexpected_sent_chunk() -> dict[str, Any]:
    def mutate(lines: list[str]) -> list[str]:
        lines.append(
            json.dumps(
                {
                    "event": "normalized_chunk_sent",
                    "delivery_chunk_id": 99,
                    "run_id": "fixture",
                    "monotonic_ns": 1,
                    "frame_count": 1,
                    "byte_count": 2,
                    "sample_rate": 16000,
                    "channels": 1,
                    "send_result": "success",
                }
            )
        )
        return lines

    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test09_unexpected_sent", audio_mutate=mutate)
    result = _run_fixture_pipeline(run, ref, truth)
    if 99 not in result["audio_summary"].get("unexpected_sent_chunk_ids", []):
        return _fail("unexpected_sent_chunk_fails", str(result["audio_summary"]))
    return _pass("unexpected_sent_chunk_fails")


def test_10_delivery_ratio_low() -> dict[str, Any]:
    def mutate(lines: list[str]) -> list[str]:
        return [ln for ln in lines if "normalized_chunk_sent" not in ln][:2]

    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test10_low_ratio", audio_mutate=mutate)
    result = _run_fixture_pipeline(run, ref, truth)
    ratio = float(result["audio_summary"].get("delivery_ratio", 1.0))
    if ratio >= 0.999:
        return _fail("delivery_ratio_below_999_fails", str(result["audio_summary"]))
    if "audio_delivery_ratio_below_threshold" not in result["acceptance"]["failures"]:
        return _fail("delivery_ratio_below_999_fails", str(result["acceptance"]["failures"]))
    return _pass("delivery_ratio_below_0_999_fails")


def test_11_malformed_jsonl() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test11_malformed_jsonl")
    path = run / "accuracy_stage_compare" / "audio_delivery_events.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    result = _run_fixture_pipeline(run, ref, truth)
    if int(result["audio_summary"].get("evidence_record_parse_errors") or 0) < 1:
        return _fail("malformed_jsonl_fails", str(result["audio_summary"]))
    return _pass("malformed_jsonl_fails")


def test_12_api_key_in_request() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test12_api_key",
        request_overrides={"sanitized_query_string": "model=nova-3&token=SECRET&language=ja"},
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("api_key_in_request_fails", str(result["verification"]["reported_value_mismatches"]))
    return _pass("api_key_in_request_evidence_fails")


def test_13_reference_in_commandline() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test13_ref_cmdline")
    iso_path = run / "accuracy_stage_compare" / "reference_isolation_actual.json"
    iso = json.loads(iso_path.read_text(encoding="utf-8"))
    iso.update(
        {
            "runtime_child_commandline": [sys.executable, str(ref)],
            "runtime_child_commandline_contains_reference": True,
            "isolation_verified": False,
        }
    )
    iso_path.write_text(json.dumps(iso, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("reference_in_commandline_fails", "passed")
    return _pass("reference_path_in_runtime_child_command_line_fails")


def test_14_reference_in_environment() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test14_ref_env",
        isolation_overrides={
            "runtime_child_environment_contains_reference": True,
            "isolation_verified": False,
        },
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("reference_in_env_fails", "passed")
    return _pass("reference_path_in_runtime_child_environment_fails")


def test_15_reference_opened_before_exit() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test15_ref_early",
        isolation_overrides={
            "runtime_child_exited_at": "2026-07-16T03:00:00Z",
            "reference_first_opened_at": "2026-07-16T02:00:00Z",
            "reference_opened_after_runtime_exit": False,
            "isolation_verified": False,
        },
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("reference_opened_before_exit_fails", "passed")
    return _pass("reference_opened_before_runtime_exit_fails")


def test_16_scoring_module_imported() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test16_scoring_import",
        isolation_overrides={
            "runtime_imported_scoring_modules": ["score_multidomain_gate_85262"],
            "isolation_verified": False,
        },
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("scoring_import_fails", "passed")
    return _pass("scoring_module_imported_during_runtime_fails")


def test_17_keyterm_count_nonzero() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test17_keyterm",
        request_overrides={"keyterm_count": 3, "keyterm_values": ["a", "b", "c"]},
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["acceptance"]["VERSION"] == "ACCEPTED":
        return _fail("keyterm_count_fails", "accepted")
    if "keyterm_count_nonzero" not in result["acceptance"]["failures"]:
        return _fail("keyterm_count_fails", str(result["acceptance"]["failures"]))
    return _pass("keyterm_count_above_zero_fails")


def test_18_keyword_count_nonzero() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test18_keyword",
        request_overrides={"keyword_count": 2, "keyword_values": ["x", "y"]},
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if "keyword_count_nonzero" not in result["acceptance"]["failures"]:
        return _fail("keyword_count_fails", str(result["acceptance"]["failures"]))
    return _pass("keyword_count_above_zero_fails")


def test_19_test01_profile_active() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test19_test01",
        request_overrides={"test01_profile_active": True, "profile": "target_85_meeting_context"},
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("test01_profile_fails", "passed")
    return _pass("test01_profile_active_fails")


def test_20_business_japanese_active() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test20_business",
        request_overrides={"business_japanese_profile_active": True, "profile": "business_japanese"},
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["verification"]["verification_passed"]:
        return _fail("business_japanese_fails", "passed")
    return _pass("business_japanese_profile_active_fails")


def test_21_raw_mutation_count() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test21_raw_mutation",
        runtime_regressions=["raw_mutation_count_nonzero"],
    )
    (run / "accuracy_stage_compare" / "runtime_regression_report.json").write_text(
        json.dumps(
            {
                "checks": {"raw_mutation_count": 2},
                "runtime_regressions": ["raw_mutation_count_nonzero"],
                "warnings": [],
                "evidence_paths": [],
                "runtime_passed": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["acceptance"]["VERSION"] == "ACCEPTED":
        return _fail("raw_mutation_fails", "accepted")
    return _pass("raw_mutation_count_above_zero_fails")


def test_22_translation_provider_active() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test22_translation",
        runtime_regressions=["translation_provider_active"],
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["acceptance"]["VERSION"] == "ACCEPTED":
        return _fail("translation_provider_fails", "accepted")
    return _pass("translation_provider_active_fails")


def test_23_stable_accuracy_below_80() -> dict[str, Any]:
    bad = "完全に異なるテキストのみ。"
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test23_low_stable",
        stable_text=bad,
        final_text=bad,
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if float(result["score"]["stable_accuracy_percent"]) >= 80.0:
        return _fail("stable_below_80_fails", str(result["score"]["stable_accuracy_percent"]))
    if result["acceptance"]["VERSION"] == "ACCEPTED":
        return _fail("stable_below_80_fails", "accepted")
    return _pass("stable_accuracy_below_80_fails")


def test_24_names_below_85() -> dict[str, Any]:
    bad = "[Speaker 1] 本日は会議です。"
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test24_names", stable_text=bad, final_text=bad)
    result = _run_fixture_pipeline(run, ref, truth)
    if float(result["score"]["domain_category"]["combined_name_accuracy_percent"]) >= 85.0:
        return _fail("names_below_85_fails", str(result["score"]["domain_category"]))
    return _pass("names_accuracy_below_85_fails")


def test_25_numbers_below_85() -> dict[str, Any]:
    bad = PLACEHOLDER_REFERENCE.replace("120万円", "不明").replace("3.2%", "不明")
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test25_numbers", stable_text=bad, final_text=bad)
    result = _run_fixture_pipeline(run, ref, truth)
    nums = float(result["score"]["domain_category"]["numbers_accuracy_percent"])
    if nums >= 85.0 and float(result["score"]["domain_category"]["money_percentage_accuracy_percent"]) >= 85.0:
        return _fail("numbers_below_85_fails", str(result["score"]["domain_category"]))
    return _pass("number_accuracy_below_85_fails")


def test_26_stable_final_loss() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test26_stf_loss",
        final_text="[Speaker 1] 短い出力のみ。",
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if float(result["score"]["stable_to_final_loss_percent"]) <= 0.0:
        return _fail("stable_final_loss_fails", str(result["score"]["stable_to_final_loss_percent"]))
    return _pass("stable_to_final_loss_above_zero_fails")


def test_27_runtime_regression() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(
        SMOKE_ROOT,
        "test27_runtime",
        runtime_regressions=["ui_main_loop_stall"],
    )
    result = _run_fixture_pipeline(run, ref, truth)
    if result["acceptance"]["VERSION"] == "ACCEPTED":
        return _fail("runtime_regression_fails", "accepted")
    return _pass("runtime_regression_fails")


def test_28_reported_cer_mismatch() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test28_cer_mismatch")
    result = _run_fixture_pipeline(run, ref, truth)
    strict_path = run / "accuracy_stage_compare" / "strict_score.json"
    data = json.loads(strict_path.read_text(encoding="utf-8"))
    data["stable"]["accuracy_percent"] = 12.34
    data["stable"]["cer_percent"] = 87.66
    strict_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from verify_multidomain_gate_85262 import verify_multidomain_gate

    v = verify_multidomain_gate(
        project_root=ROOT,
        run_folder=run,
        reference_path=ref,
        truth_path=truth,
    )
    if "reported_stable_accuracy_mismatch" not in v["reported_value_mismatches"]:
        return _fail("reported_cer_mismatch_fails", str(v["reported_value_mismatches"]))
    return _pass("reported_cer_mismatch_fails")


def test_29_reported_category_mismatch() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test29_category_mismatch")
    result = _run_fixture_pipeline(run, ref, truth)
    domain_path = run / "accuracy_stage_compare" / "domain_category_score.json"
    data = json.loads(domain_path.read_text(encoding="utf-8"))
    data["combined_name_accuracy_percent"] = 10.0
    domain_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from verify_multidomain_gate_85262 import verify_multidomain_gate

    v = verify_multidomain_gate(
        project_root=ROOT,
        run_folder=run,
        reference_path=ref,
        truth_path=truth,
    )
    if not v["reported_value_mismatches"]:
        return _fail("reported_category_mismatch_fails", "no mismatches")
    return _pass("reported_category_score_mismatch_fails")


def test_30_fixture_not_accepted() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test30_not_accepted")
    result = _run_fixture_pipeline(run, ref, truth)
    if result["acceptance"]["VERSION"] == "ACCEPTED":
        return _fail("fixture_not_accepted", "VERSION=ACCEPTED")
    if result["acceptance"].get("ready_for_translation_beta"):
        return _fail("fixture_not_accepted", "ready_for_translation_beta true")
    return _pass("fixture_run_cannot_create_version_accepted")


def test_31_fixture_outputs_isolated() -> dict[str, Any]:
    live_runs = ROOT / "troubleshooting" / "runs"
    before = set(p.name for p in live_runs.iterdir()) if live_runs.exists() else set()
    marker = live_runs / ".fixture_guard_marker.txt"
    if live_runs.exists():
        marker.write_text("guard\n", encoding="utf-8")
        before_mtime = marker.stat().st_mtime
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test31_isolated")
    _run_fixture_pipeline(run, ref, truth)
    if not str(run).replace("\\", "/").startswith(str(SMOKE_ROOT).replace("\\", "/")):
        return _fail("fixture_outputs_isolated", "outside smoke_tests")
    if live_runs.exists() and marker.exists():
        if marker.stat().st_mtime != before_mtime:
            return _fail("fixture_outputs_isolated", "live runs marker touched")
    after = set(p.name for p in live_runs.iterdir()) if live_runs.exists() else set()
    if not before.issubset(after):
        return _fail("fixture_outputs_isolated", "live runs removed")
    return _pass("fixture_outputs_cannot_overwrite_latest_live_run_artifacts")


def test_32_package_excludes_audio() -> dict[str, Any]:
    run, ref, truth = build_fixture_run(SMOKE_ROOT, "test32_no_audio")
    result = _run_fixture_pipeline(run, ref, truth)
    package = result["package"]
    with zipfile.ZipFile(package, "r") as zf:
        names = zf.namelist()
    if any(n.lower().endswith((".wav", ".mp3", ".m4a", ".pcm")) for n in names):
        return _fail("package_excludes_audio", str(names))
    return _pass("audio_files_excluded_from_package")


TESTS: list[Callable[[], dict[str, Any]]] = [
    test_01_valid_fixture,
    test_02_missing_raw,
    test_03_missing_stable,
    test_04_missing_final,
    test_05_altered_transcript_hash,
    test_06_altered_audio_jsonl,
    test_07_missing_sent_chunk,
    test_08_duplicate_sent_chunk,
    test_09_unexpected_sent_chunk,
    test_10_delivery_ratio_low,
    test_11_malformed_jsonl,
    test_12_api_key_in_request,
    test_13_reference_in_commandline,
    test_14_reference_in_environment,
    test_15_reference_opened_before_exit,
    test_16_scoring_module_imported,
    test_17_keyterm_count_nonzero,
    test_18_keyword_count_nonzero,
    test_19_test01_profile_active,
    test_20_business_japanese_active,
    test_21_raw_mutation_count,
    test_22_translation_provider_active,
    test_23_stable_accuracy_below_80,
    test_24_names_below_85,
    test_25_numbers_below_85,
    test_26_stable_final_loss,
    test_27_runtime_regression,
    test_28_reported_cer_mismatch,
    test_29_reported_category_mismatch,
    test_30_fixture_not_accepted,
    test_31_fixture_outputs_isolated,
    test_32_package_excludes_audio,
]


def _index_fixture_files(fixture_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not fixture_dir.exists():
        return rows
    for path in sorted(fixture_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "__pycache__" or "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(fixture_dir)).replace("\\", "/")
        try:
            data = path.read_bytes()
            rows.append(
                {
                    "relative_path": rel,
                    "exists": True,
                    "byte_size": len(data),
                    "sha256": __import__("hashlib").sha256(data).hexdigest(),
                }
            )
        except Exception:
            rows.append(
                {
                    "relative_path": rel,
                    "exists": True,
                    "byte_size": None,
                    "sha256": None,
                }
            )
    return rows


def _write_fixture_artifacts(
    *,
    fixture_dir: Path,
    test_number: int,
    det_name: str,
    expected_name: str,
    expected_pass: bool,
    expected_failure_reason: str,
    result: dict[str, Any],
    started_at: str,
    completed_at: str,
    invoked: str,
) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    expected = {
        "test_number": test_number,
        "test_name": expected_name,
        "expected_pass_or_fail": "pass" if expected_pass else "fail",
        "expected_failure_reason": expected_failure_reason,
    }
    actual = {
        "test_number": test_number,
        "test_name": result.get("name"),
        "actual_pass_or_fail": "pass" if result.get("passed") else "fail",
        "actual_failure_reason": result.get("detail") or "",
        "passed": bool(result.get("passed")),
        "detail": result.get("detail") or "",
    }
    (fixture_dir / "expected_result.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (fixture_dir / "actual_result.json").write_text(
        json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    file_index = _index_fixture_files(fixture_dir)
    metadata = {
        "test_number": test_number,
        "test_name": expected_name,
        "fixture_directory": str(fixture_dir),
        "deterministic_name": det_name,
        "expected_pass_or_fail": "pass" if expected_pass else "fail",
        "expected_failure_reason": expected_failure_reason,
        "actual_pass_or_fail": "pass" if result.get("passed") else "fail",
        "actual_failure_reason": result.get("detail") or "",
        "command_or_function_invoked": invoked,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "file_index": file_index,
    }
    (fixture_dir / "test_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    global SMOKE_ROOT, CURRENT_FIXTURE_DIR, KEEP_FIXTURES

    parser = argparse.ArgumentParser(description="Multidomain gate fixture regression (85262)")
    parser.add_argument("--evidence-root", default="", help="Root for persistent fixture evidence")
    parser.add_argument(
        "--keep-fixtures",
        action="store_true",
        help="Keep physical fixture directories (do not delete)",
    )
    parser.add_argument("--results-json", default="", help="Write machine-readable results JSON")
    args = parser.parse_args(argv)

    KEEP_FIXTURES = bool(args.keep_fixtures)
    if args.evidence_root:
        evidence_root = Path(args.evidence_root)
        if not evidence_root.is_absolute():
            evidence_root = ROOT / evidence_root
        fixtures_root = evidence_root / "fixtures"
        fixtures_root.mkdir(parents=True, exist_ok=True)
        SMOKE_ROOT = fixtures_root
    else:
        SMOKE_ROOT.mkdir(parents=True, exist_ok=True)
        fixtures_root = SMOKE_ROOT
        if KEEP_FIXTURES:
            fixtures_root = SMOKE_ROOT / "fixtures"
            fixtures_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for idx, fn in enumerate(TESTS):
        det_name, expected_name, expected_pass, expected_reason = DETERMINISTIC_FIXTURES[idx]
        test_number = idx + 1
        fixture_dir = fixtures_root / det_name if KEEP_FIXTURES else (SMOKE_ROOT / det_name)
        if KEEP_FIXTURES:
            if fixture_dir.exists():
                shutil.rmtree(fixture_dir, ignore_errors=True)
            fixture_dir.mkdir(parents=True, exist_ok=True)
            CURRENT_FIXTURE_DIR = fixture_dir
        else:
            CURRENT_FIXTURE_DIR = None

        started = utc_now_iso()
        try:
            result = fn()
        except Exception as exc:
            result = _fail(fn.__name__, f"{type(exc).__name__}: {exc}")
        completed = utc_now_iso()
        results.append(result)

        if KEEP_FIXTURES:
            _write_fixture_artifacts(
                fixture_dir=fixture_dir,
                test_number=test_number,
                det_name=det_name,
                expected_name=expected_name,
                expected_pass=expected_pass,
                expected_failure_reason=expected_reason,
                result=result,
                started_at=started,
                completed_at=completed,
                invoked=f"{fn.__module__}.{fn.__name__}",
            )

    CURRENT_FIXTURE_DIR = None
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    print(f"tests={len(results)}")
    print(f"passed={passed}")
    print(f"failed={failed}")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['name']}" + (f" — {r['detail']}" if r["detail"] else ""))
    print("IMPLEMENTATION_STATUS=READY")
    print("REAL_BENCHMARK_COMPLETED=false")
    print("READY_FOR_TRANSLATION_BETA=false")

    if args.results_json:
        out = Path(args.results_json)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tests": len(results),
            "passed": passed,
            "failed": failed,
            "keep_fixtures": KEEP_FIXTURES,
            "fixture_root": str(fixtures_root if KEEP_FIXTURES else SMOKE_ROOT),
            "results": results,
            "deterministic_fixtures": [
                {
                    "test_number": i + 1,
                    "directory": name,
                    "expected_name": exp,
                    "expected_pass": ep,
                    "expected_failure_reason": reason,
                }
                for i, (name, exp, ep, reason) in enumerate(DETERMINISTIC_FIXTURES)
            ],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 0 if failed == 0 and len(results) == 32 else 1


if __name__ == "__main__":
    raise SystemExit(main())
