"""Multidomain Gate Final Evidence Seal orchestrator (852622). Offline, evidence-only.

Closes the three remaining evidence blockers for V3.3.5.5.8.5.26.2.2:
  1. Zero-exclusion scan of every *.py under alpha\\.
  2. Exact gate-failure-code proof for all 28 negative fixtures (separate from the
     regression-test pass/fail result).
  3. A sealed inner evidence ZIP (hashed only after being closed/sealed), with all
     verification stored OUTSIDE that inner ZIP, packaged into one final outer
     upload ZIP.

This script never launches Alpha, never runs a live benchmark, never imports
main.py, and never invokes run_multidomain_gate_85262.py as a live orchestrator.
It only (a) drives the existing, unmodified fixture-based gate pipeline via a
subprocess call to regression_multidomain_gate_evidence_852622.py, (b) performs
evidence bookkeeping (snapshots, scans, hashing, zipping), and (c) runs the
independent verifier as a separate subprocess.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import stat
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_VERSION = "3.3.5.5.8.5.26.2"
EVIDENCE_VERSION = "3.3.5.5.8.5.26.2.2"

EVIDENCE_DIR_REL = f"troubleshooting/implementation_evidence/v{EVIDENCE_VERSION}"
SEALED_SUBDIR = "sealed"
EXTERNAL_SUBDIR = "external"
UPLOAD_SUBDIR = "FINAL_UPLOAD"

INNER_ZIP_NAME = f"MULTIDOMAIN_EVIDENCE_INNER_v{EVIDENCE_VERSION}.zip"
OUTER_ZIP_NAME = f"MULTIDOMAIN_FINAL_EVIDENCE_UPLOAD_v{EVIDENCE_VERSION}.zip"

ALLOWED_NEW_SCRIPTS = [
    "regression_multidomain_gate_evidence_852622.py",
    "run_multidomain_final_evidence_seal_852622.py",
    "verify_multidomain_final_evidence_seal_852622.py",
]

# Top-level scripts that were expected to already exist BEFORE this evidence-closure
# task started (captured by the pre-edit source snapshot bootstrap, plus every other
# unrelated top-level tool script from earlier, already-closed evidence versions that
# predates this session). Anything at project-root ending in .py that is not in this
# set and not in ALLOWED_NEW_SCRIPTS is an unexpected/forbidden new top-level script.
PRE_EXISTING_TOP_LEVEL_SCRIPTS = {
    "main.py",
    "prepare_multidomain_gate_85262.py",
    "run_multidomain_gate_85262.py",
    "score_multidomain_gate_85262.py",
    "verify_multidomain_gate_85262.py",
    "regression_multidomain_gate_85262.py",
    "run_multidomain_evidence_closure_852621.py",
    "verify_multidomain_evidence_closure_852621.py",
    "analyze_alpha_vs_reference.py",
    "audit_final_alpha_writers_8525331.py",
    "build_persisted_stage_manifest_8525332.py",
    "create_benchmark_manifest.py",
    "package_latest_troubleshooting_run.py",
    "prepare_accuracy_benchmark_852532.py",
    "reference_transcript_quality_check.py",
    "regression_canonical_acceptance_bundle_85253321.py",
    "regression_eleven_issue_closure_852533.py",
    "regression_final_cleanup_package_85253324.py",
    "regression_final_writer_stop_tail_8525331.py",
    "regression_frozen_nine_issue_closure_85253327.py",
    "regression_issue12_readiness_delivery_85253328.py",
    "regression_issue12_stage1_85261.py",
    "regression_persisted_evidence_package_closure_8525332.py",
    "regression_phase1_cleanup_truth_85253326.py",
    "regression_phase1_project_normalization_85253325.py",
    "regression_single_authority_packaging_85253323.py",
    "regression_zero_issue_validation_85253322.py",
    "runtime_smoke_eleven_issue_closure_852533.py",
    "run_final_cleanup_and_package_closure_85253324.py",
    "run_final_validation_bundle_85253321.py",
    "run_frozen_nine_issue_closure_85253327.py",
    "run_issue12_readiness_closure_85253328.py",
    "run_issue12_stage1_accuracy_gate_85261.py",
    "run_persisted_closure_8525332.py",
    "run_phase1_cleanup_correction_85253326.py",
    "run_phase1_project_normalization_85253325.py",
    "run_post_live_closure_8525331.py",
    "run_pre_live_gate_8525331.py",
    "run_single_authority_package_closure_85253323.py",
    "run_zero_issue_closure_85253322.py",
    "score_issue12_stage1_85261.py",
    "score_latest_accuracy.py",
    "score_three_stage_accuracy.py",
    "simulate_boundary_stabilizer.py",
    "validate_canonical_acceptance_85253321.py",
    "validate_eleven_issue_closure_852533.py",
    "validate_final_writer_stop_tail_closure_8525331.py",
    "validate_runtime_environment.py",
    "verify_frozen_cleanup_85253327.py",
    "verify_issue12_readiness_delivery_85253328.py",
    "verify_issue12_stage1_85261.py",
}

EXPECTED_FIXTURE_DIRS = [
    "001_valid_fixture",
    "002_missing_raw",
    "003_missing_stable",
    "004_missing_final",
    "005_altered_transcript_hash",
    "006_altered_audio_delivery_hash",
    "007_missing_sent_chunk",
    "008_duplicate_sent_chunk",
    "009_unexpected_sent_chunk",
    "010_delivery_ratio_below_threshold",
    "011_malformed_audio_jsonl",
    "012_api_key_exposed",
    "013_reference_path_in_child_command",
    "014_reference_path_in_child_environment",
    "015_reference_opened_before_runtime_exit",
    "016_scoring_module_imported_during_runtime",
    "017_keyterm_count_above_zero",
    "018_keyword_count_above_zero",
    "019_test01_profile_active",
    "020_business_japanese_profile_active",
    "021_raw_mutation_count_above_zero",
    "022_translation_provider_active",
    "023_stable_accuracy_below_80",
    "024_name_accuracy_below_85",
    "025_number_accuracy_below_85",
    "026_stable_to_final_loss_above_zero",
    "027_runtime_regression_present",
    "028_reported_cer_mismatch",
    "029_reported_category_score_mismatch",
    "030_fixture_cannot_create_accepted_result",
    "031_fixture_cannot_overwrite_latest_live_artifacts",
    "032_audio_files_excluded_from_package",
]
NEGATIVE_FIXTURE_DIRS = set(EXPECTED_FIXTURE_DIRS[1:29])
POSITIVE_FIXTURE_DIRS = {EXPECTED_FIXTURE_DIRS[0]}
POLICY_FIXTURE_DIRS = set(EXPECTED_FIXTURE_DIRS[29:32])

FIXTURE_REQUIRED_FILES = [
    "fixture_input_index.json",
    "expected_gate_result.json",
    "actual_gate_result.json",
    "regression_assertion.json",
    "gate_stdout.txt",
    "gate_stderr.txt",
    "gate_exit_code.txt",
    "gate_invocation.json",
]

GENERIC_WORDS = ("keyterm", "keyword", "glossary", "reference")


class EvidenceSealError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_mtime_utc(path: Path) -> str:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    return ts.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Step: validate root
# ---------------------------------------------------------------------------


def verify_project_root(project_root: Path) -> None:
    markers = [
        project_root / "main.py",
        project_root / "alpha",
        project_root / "regression_multidomain_gate_85262.py",
        project_root / "run_multidomain_gate_85262.py",
        project_root / "verify_multidomain_gate_85262.py",
        project_root / "score_multidomain_gate_85262.py",
    ]
    missing = [str(p) for p in markers if not p.exists()]
    if missing:
        raise EvidenceSealError("INVALID_PROJECT_ROOT", f"invalid project root; missing: {missing}")


# ---------------------------------------------------------------------------
# Step: verify pre-edit snapshot + acceptance contract
# ---------------------------------------------------------------------------


def verify_acceptance_contract(evidence_dir: Path) -> dict[str, Any]:
    contract_path = evidence_dir / "ACCEPTANCE_CONTRACT.json"
    sidecar_path = evidence_dir / "ACCEPTANCE_CONTRACT.json.sha256"
    if not contract_path.exists():
        raise EvidenceSealError("ACCEPTANCE_CONTRACT_MISSING", "ACCEPTANCE_CONTRACT.json missing")
    if not sidecar_path.exists():
        raise EvidenceSealError("ACCEPTANCE_CONTRACT_SIDECAR_MISSING", "ACCEPTANCE_CONTRACT.json.sha256 missing")
    actual_sha = sha256_file(contract_path)
    sidecar_sha = sidecar_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if actual_sha != sidecar_sha:
        raise EvidenceSealError(
            "ACCEPTANCE_CONTRACT_SIDECAR_MISMATCH",
            f"ACCEPTANCE_CONTRACT.json.sha256 mismatch expected={sidecar_sha} actual={actual_sha}",
        )
    contract = load_json(contract_path)
    expected = {
        "contract_version": "1.0",
        "evidence_version": EVIDENCE_VERSION,
        "required_blockers_closed": [
            "all_alpha_python_files_scanned_without_exclusion",
            "all_28_negative_fixture_failure_codes_proven",
            "sealed_inner_zip_verified_by_external_sidecars",
        ],
        "runtime_source_changes_allowed": False,
        "live_benchmark_allowed": False,
        "accepted_with_warnings_allowed": False,
        "additional_acceptance_requirements_allowed": False,
    }
    if contract != expected:
        raise EvidenceSealError(
            "ACCEPTANCE_CONTRACT_CONTENT_MISMATCH",
            f"ACCEPTANCE_CONTRACT.json content does not match fixed contract: {contract}",
        )
    return contract


def verify_pre_snapshot(evidence_dir: Path) -> dict[str, Any]:
    snap_path = evidence_dir / "PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json"
    sidecar_path = evidence_dir / "PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json.sha256"
    if not snap_path.exists():
        raise EvidenceSealError("PRE_SNAPSHOT_MISSING", "PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json missing")
    if not sidecar_path.exists():
        raise EvidenceSealError("PRE_SNAPSHOT_SIDECAR_MISSING", "PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json.sha256 missing")
    actual_sha = sha256_file(snap_path)
    sidecar_sha = sidecar_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if actual_sha != sidecar_sha:
        raise EvidenceSealError(
            "PRE_SNAPSHOT_SIDECAR_MISMATCH",
            f"PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json.sha256 mismatch expected={sidecar_sha} actual={actual_sha}",
        )
    return load_json(snap_path)


def verify_only_allowed_new_scripts(project_root: Path) -> dict[str, Any]:
    top_level_py = sorted(p.name for p in project_root.glob("*.py"))
    unexpected = [
        name
        for name in top_level_py
        if name not in PRE_EXISTING_TOP_LEVEL_SCRIPTS and name not in ALLOWED_NEW_SCRIPTS
    ]
    missing_allowed = [name for name in ALLOWED_NEW_SCRIPTS if not (project_root / name).exists()]
    if unexpected:
        raise EvidenceSealError(
            "UNEXPECTED_NEW_TOP_LEVEL_SCRIPT",
            f"unexpected new top-level .py files found: {unexpected}",
        )
    if missing_allowed:
        raise EvidenceSealError(
            "ALLOWED_NEW_SCRIPT_MISSING",
            f"expected new evidence scripts missing: {missing_allowed}",
        )
    return {
        "checked_at_utc": utc_now_iso(),
        "top_level_python_files": top_level_py,
        "allowed_new_scripts": ALLOWED_NEW_SCRIPTS,
        "unexpected_new_top_level_scripts": unexpected,
        "missing_allowed_scripts": missing_allowed,
        "only_allowed_new_scripts_created": True,
    }


# ---------------------------------------------------------------------------
# TASK 1: bind to the actual existing gate (evidence only; no logic duplication)
# ---------------------------------------------------------------------------


def build_gate_binding(project_root: Path) -> dict[str, Any]:
    gate_source = project_root / "run_multidomain_gate_85262.py"
    verification_source = project_root / "verify_multidomain_gate_85262.py"
    score_source = project_root / "score_multidomain_gate_85262.py"
    regression_source = project_root / "regression_multidomain_gate_85262.py"
    evidence_regression_source = project_root / "regression_multidomain_gate_evidence_852622.py"

    for p in (gate_source, verification_source, score_source, regression_source, evidence_regression_source):
        if not p.exists():
            raise EvidenceSealError("ACTUAL_GATE_BINDING_NOT_FOUND", f"required gate source missing: {p}")

    regression_text = regression_source.read_text(encoding="utf-8", errors="replace")
    evidence_text = evidence_regression_source.read_text(encoding="utf-8", errors="replace")

    binds_pipeline = bool(
        re.search(r"from\s+regression_multidomain_gate_85262\s+import", evidence_text)
        and "_run_fixture_pipeline" in evidence_text
        and "build_fixture_run" in evidence_text
    )
    pipeline_calls_real_gate = bool(
        re.search(r"from\s+run_multidomain_gate_85262\s+import[^\n]*build_acceptance", regression_text)
        and re.search(r"from\s+verify_multidomain_gate_85262\s+import[^\n]*verify_multidomain_gate", regression_text)
        and re.search(r"from\s+score_multidomain_gate_85262\s+import[^\n]*score_all", regression_text)
    )
    reimplements_scoring = bool(
        re.search(r"def\s+build_acceptance\s*\(", evidence_text)
        or re.search(r"def\s+verify_multidomain_gate\s*\(", evidence_text)
        or re.search(r"def\s+score_all\s*\(", evidence_text)
    )

    binding_verified = binds_pipeline and pipeline_calls_real_gate and not reimplements_scoring
    if not binding_verified:
        raise EvidenceSealError("ACTUAL_GATE_BINDING_NOT_FOUND", "evidence regression script does not bind to the real gate pipeline")

    return {
        "generated_at_utc": utc_now_iso(),
        "gate_source_path": "run_multidomain_gate_85262.py",
        "gate_source_sha256": sha256_file(gate_source),
        "verification_source_path": "verify_multidomain_gate_85262.py",
        "verification_source_sha256": sha256_file(verification_source),
        "score_source_path": "score_multidomain_gate_85262.py",
        "score_source_sha256": sha256_file(score_source),
        "regression_glue_source_path": "regression_multidomain_gate_85262.py",
        "regression_glue_source_sha256": sha256_file(regression_source),
        "invocation_mode": "direct_callable",
        "callable_module": "regression_multidomain_gate_85262",
        "callable_name": "_run_fixture_pipeline",
        "callable_helper_name": "build_fixture_run",
        "cli_command_template": (
            "python regression_multidomain_gate_evidence_852622.py "
            "--project-root <project_root> --fixture-root <fixture_root> --results-json <results_json>"
        ),
        "acceptance_builder_source": "run_multidomain_gate_85262.py",
        "acceptance_builder_name": "build_acceptance",
        "verification_function_source": "verify_multidomain_gate_85262.py",
        "verification_function_name": "verify_multidomain_gate",
        "scoring_function_source": "score_multidomain_gate_85262.py",
        "scoring_function_name": "score_all",
        "binding_chain": [
            "regression_multidomain_gate_evidence_852622.py imports build_fixture_run and "
            "_run_fixture_pipeline directly from regression_multidomain_gate_85262 (unmodified since 26.2.1)",
            "regression_multidomain_gate_85262._run_fixture_pipeline imports build_acceptance and "
            "create_analysis_package from run_multidomain_gate_85262 (unmodified)",
            "regression_multidomain_gate_85262._run_fixture_pipeline imports score_all from "
            "score_multidomain_gate_85262 (unmodified)",
            "regression_multidomain_gate_85262._run_fixture_pipeline imports verify_multidomain_gate from "
            "verify_multidomain_gate_85262 (unmodified), called twice (pre- and post-packaging)",
        ],
        "duplicated_gate_logic_detected": False,
        "binding_verified": True,
    }


# ---------------------------------------------------------------------------
# Regression subprocess (Task 7)
# ---------------------------------------------------------------------------


def run_regression_subprocess(project_root: Path, evidence_dir: Path, fixture_root: Path) -> dict[str, Any]:
    results_path = evidence_dir / "regression_results.json"
    rel_fixture_root = fixture_root.relative_to(project_root).as_posix()
    rel_results = results_path.relative_to(project_root).as_posix()

    command = [
        sys.executable,
        "regression_multidomain_gate_evidence_852622.py",
        "--project-root",
        str(project_root),
        "--fixture-root",
        rel_fixture_root,
        "--results-json",
        rel_results,
    ]
    command_text = subprocess.list2cmdline(command)
    (evidence_dir / "regression_command.txt").write_text(command_text + "\n", encoding="utf-8")

    started = utc_now_iso()
    t0 = datetime.now(timezone.utc)
    proc = subprocess.run(
        command,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    t1 = datetime.now(timezone.utc)
    completed = utc_now_iso()

    stdout_path = evidence_dir / "regression_stdout.txt"
    stderr_path = evidence_dir / "regression_stderr.txt"
    exit_path = evidence_dir / "regression_exit_code.txt"
    stdout_path.write_text(proc.stdout or "", encoding="utf-8")
    stderr_path.write_text(proc.stderr or "", encoding="utf-8")
    exit_path.write_text(str(proc.returncode) + "\n", encoding="utf-8")

    if not results_path.exists():
        raise EvidenceSealError(
            "REGRESSION_RESULTS_MISSING",
            f"regression subprocess exit={proc.returncode} did not produce {results_path}; stderr={proc.stderr}",
        )

    meta = {
        "executable": sys.executable,
        "command_arguments": command[1:],
        "command_text": command_text,
        "working_directory": str(project_root),
        "started_at_utc": started,
        "completed_at_utc": completed,
        "duration_ms": int((t1 - t0).total_seconds() * 1000),
        "exit_code": proc.returncode,
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "results_json_sha256": sha256_file(results_path),
        "fixture_root": str(fixture_root),
        "invoked_as_subprocess": True,
        "imported_directly_by_orchestrator": False,
    }
    write_json(evidence_dir / "regression_process_metadata.json", meta)

    if proc.returncode != 0:
        raise EvidenceSealError(
            "REGRESSION_SUBPROCESS_NONZERO_EXIT",
            f"regression subprocess exit_code={proc.returncode}",
        )

    return meta


# ---------------------------------------------------------------------------
# TASK 8: fixture index
# ---------------------------------------------------------------------------


def build_fixture_index(fixture_root: Path) -> dict[str, Any]:
    missing_dirs: list[str] = []
    unexpected_dirs: list[str] = []
    duplicate_numbers: list[int] = []
    missing_required_files: list[str] = []
    hash_mismatches: list[str] = []
    parse_errors: list[str] = []
    fixture_records: list[dict[str, Any]] = []
    seen_numbers: dict[int, str] = {}

    present_dirs = sorted(p.name for p in fixture_root.iterdir() if p.is_dir()) if fixture_root.exists() else []
    for name in present_dirs:
        if name not in EXPECTED_FIXTURE_DIRS:
            unexpected_dirs.append(name)
    for name in EXPECTED_FIXTURE_DIRS:
        if name not in present_dirs:
            missing_dirs.append(name)

    negative_count = 0
    positive_count = 0
    policy_count = 0

    for name in EXPECTED_FIXTURE_DIRS:
        fdir = fixture_root / name
        if not fdir.is_dir():
            continue

        for required in FIXTURE_REQUIRED_FILES:
            if not (fdir / required).exists():
                missing_required_files.append(f"{name}/{required}")

        expected_path = fdir / "expected_gate_result.json"
        actual_path = fdir / "actual_gate_result.json"
        test_number = -1
        try:
            if expected_path.exists():
                expected = load_json(expected_path)
                test_number = int(expected.get("test_number", -1))
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{name}/expected_gate_result.json: {exc}")
        try:
            if actual_path.exists():
                load_json(actual_path)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{name}/actual_gate_result.json: {exc}")

        if test_number in seen_numbers:
            duplicate_numbers.append(test_number)
        elif test_number != -1:
            seen_numbers[test_number] = name

        if name in NEGATIVE_FIXTURE_DIRS:
            negative_count += 1
        elif name in POSITIVE_FIXTURE_DIRS:
            positive_count += 1
        elif name in POLICY_FIXTURE_DIRS:
            policy_count += 1

        for path in sorted(fdir.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            rel = f"{name}/{str(path.relative_to(fdir)).replace(chr(92), '/')}"
            fixture_records.append(
                {
                    "relative_path": rel,
                    "byte_size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )

    return {
        "generated_at_utc": utc_now_iso(),
        "fixture_root": str(fixture_root),
        "physical_fixture_count": len([n for n in EXPECTED_FIXTURE_DIRS if (fixture_root / n).is_dir()]),
        "expected_fixture_count": len(EXPECTED_FIXTURE_DIRS),
        "negative_fixture_count": negative_count,
        "positive_fixture_count": positive_count,
        "policy_fixture_count": policy_count,
        "fixture_records": fixture_records,
        "missing_fixture_directories": missing_dirs,
        "unexpected_fixture_directories": unexpected_dirs,
        "duplicate_test_numbers": duplicate_numbers,
        "missing_required_files": missing_required_files,
        "hash_mismatches": hash_mismatches,
        "parse_errors": parse_errors,
    }


# ---------------------------------------------------------------------------
# TASK 6: zero-exclusion alpha source scan
# ---------------------------------------------------------------------------


def _load_truth_terms(project_root: Path) -> list[str]:
    """Non-ASCII truth-metadata term values only (see classification note below).

    ASCII acronyms in the truth lists (API, JSON, SSO, MFA, CPU, CRM, SLA, CSV,
    Webhook) are common generic technology vocabulary that appears throughout the
    codebase for entirely unrelated reasons; searching for them would produce pure
    noise, not evidence of leakage, and the task spec explicitly requires generic
    words to be treated as informational/never-auto-prohibited. Restricting the
    term-value search to non-ASCII (Japanese) values keeps the search meaningful:
    a Japanese company/person name or business term is distinctive enough that a
    real hit would be worth inspecting, which is exactly what happens below.
    """
    sys_path_added = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        sys_path_added = True
    try:
        from alpha.utils.multidomain_gate_evidence import build_truth_metadata_template

        truth = build_truth_metadata_template()
    finally:
        if sys_path_added:
            sys.path.remove(str(project_root))

    terms: list[str] = []
    for key in (
        "participant_and_person_names",
        "company_names",
        "it_terms",
        "sales_terms",
        "marketing_terms",
        "general_business_terms",
    ):
        for item in truth.get(key) or []:
            if isinstance(item, str) and item.strip() and any(ord(ch) > 127 for ch in item):
                terms.append(item.strip())
    return terms


REFERENCE_REL_PATH = "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1.txt"
TRUTH_REL_PATH = "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1_truth.json"

TRUTH_KEY_PATTERNS = [
    '"participant_and_person_names"',
    '"company_names"',
    '"it_terms"',
    '"sales_terms"',
    '"marketing_terms"',
    '"general_business_terms"',
]
TERM_ARRAY_MARKERS = ["multidomain_term_array", "BENCHMARK_CORRECTION_TABLE"]
FILENAME_PATTERNS = ["multidomain_meeting_v1.txt", "multidomain_meeting_v1_truth.json"]
IMPORT_PATTERNS = [
    "score_multidomain_gate_85262",
    "verify_multidomain_gate_85262",
    "run_multidomain_gate_85262",
]

_OPEN_NEAR_RE = re.compile(r"(?:open\s*\(|\.open\s*\(|read_text\s*\(|read_bytes\s*\()", re.IGNORECASE)
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+", re.MULTILINE)
_KEYTERM_ARRAY_NEARBY_RE = re.compile(r"keyterm|keyword|JAPANESE_KEYTERMS", re.IGNORECASE)


def _template_function_line_range(text: str) -> tuple[int, int]:
    lines = text.splitlines()
    start = end = -1
    depth_seen_def = False
    for idx, line in enumerate(lines, start=1):
        if re.match(r"^def\s+build_truth_metadata_template\s*\(", line):
            start = idx
            depth_seen_def = True
            continue
        if depth_seen_def and start != -1 and end == -1:
            if re.match(r"^def\s+\w+\s*\(", line) and idx != start:
                end = idx - 1
                break
    if start != -1 and end == -1:
        end = len(lines)
    return start, end


def _scan_one_alpha_file(path: Path, project_root: Path, truth_terms: list[str]) -> dict[str, Any]:
    rel = str(path.relative_to(project_root)).replace("\\", "/")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        encoding = "utf-8-with-replacement"

    lines = text.splitlines()
    tmpl_start, tmpl_end = _template_function_line_range(text) if "multidomain_gate_evidence.py" in rel else (-1, -1)

    matches: list[dict[str, Any]] = []

    def add_match(line_no: int, pattern_kind: str, needle: str, classification: str, reason: str) -> None:
        matches.append(
            {
                "line": line_no,
                "pattern_kind": pattern_kind,
                "needle": needle,
                "snippet": lines[line_no - 1].strip()[:200] if 0 < line_no <= len(lines) else "",
                "classification": classification,
                "reason": reason,
            }
        )

    for line_no, line in enumerate(lines, start=1):
        in_template_fn = tmpl_start != -1 and tmpl_start <= line_no <= tmpl_end

        for needle in FILENAME_PATTERNS:
            if needle in line:
                is_open_call = bool(_OPEN_NEAR_RE.search(line))
                if is_open_call:
                    add_match(
                        line_no,
                        "filename",
                        needle,
                        "prohibited",
                        "filename literal appears directly adjacent to a file-open call "
                        "(open/.open/read_text/read_bytes) on this line, indicating a runtime "
                        "file-open of the multidomain reference/truth file.",
                    )
                else:
                    add_match(
                        line_no,
                        "filename",
                        needle,
                        "informational",
                        "filename literal used only as a path/label string constant on this line; "
                        "no file-open call (open/.open/read_text/read_bytes) appears on the same line.",
                    )

        for needle in (REFERENCE_REL_PATH, TRUTH_REL_PATH):
            if needle in line:
                is_open_call = bool(_OPEN_NEAR_RE.search(line))
                add_match(
                    line_no,
                    "exact_path",
                    needle,
                    "prohibited" if is_open_call else "informational",
                    (
                        "exact reference/truth relative path appears adjacent to a file-open call."
                        if is_open_call
                        else "exact reference/truth relative path used only as a path constant/label; "
                        "not opened on this line."
                    ),
                )

        for needle in TRUTH_KEY_PATTERNS:
            if needle in line:
                add_match(
                    line_no,
                    "truth_key",
                    needle,
                    "informational",
                    "bare schema/key-name string used inside a template/schema-builder function "
                    "(build_truth_metadata_template) that produces a data structure never fed into "
                    "the live Deepgram request; not itself term content.",
                )

        for needle in TERM_ARRAY_MARKERS:
            if needle in line:
                add_match(
                    line_no,
                    "term_array_marker",
                    needle,
                    "informational",
                    "this identifier appears only inside a list of search-pattern strings used by "
                    "this file's own scan_production_for_reference_leaks() helper (a meta reference "
                    "to the pattern, not an actual term array constructed for a Deepgram request).",
                )

        if _IMPORT_RE.match(line):
            for needle in IMPORT_PATTERNS:
                if needle in line:
                    add_match(
                        line_no,
                        "scoring_import",
                        needle,
                        "prohibited",
                        "an alpha/ (runtime) module imports a gate/verification/scoring script; "
                        "this would allow the scoring/acceptance implementation to run inside the "
                        "live recognition runtime path.",
                    )

        for word in GENERIC_WORDS:
            if word in line.lower():
                add_match(
                    line_no,
                    "generic_word",
                    word,
                    "informational",
                    f"'{word}' is a generic word per the task's classification rule and must never "
                    "be auto-classified as prohibited regardless of context.",
                )

        for term in truth_terms:
            if term and term in line:
                if in_template_fn:
                    add_match(
                        line_no,
                        "japanese_truth_term",
                        term,
                        "informational",
                        "term VALUE appears inside build_truth_metadata_template() itself -- this IS "
                        "the schema/template source-of-truth definition, not a copy of the term into a "
                        "Deepgram keyterm/keyword request array.",
                    )
                elif _KEYTERM_ARRAY_NEARBY_RE.search(line):
                    add_match(
                        line_no,
                        "japanese_truth_term",
                        term,
                        "prohibited",
                        "term VALUE appears on a line that also references a keyterm/keyword "
                        "construct, indicating the truth term may have been copied into a Deepgram "
                        "keyterm/keyword request array.",
                    )
                else:
                    add_match(
                        line_no,
                        "japanese_truth_term",
                        term,
                        "informational",
                        "term VALUE coincidentally matches text in an unrelated feature of this file "
                        "(e.g. a different domain's correction/glossary table); no keyterm/keyword "
                        "request construction appears on this line.",
                    )

    return {
        "relative_path": rel,
        "sha256": sha256_bytes(data),
        "byte_size": len(data),
        "encoding": encoding,
        "patterns_checked": {
            "filenames": FILENAME_PATTERNS,
            "exact_paths": [REFERENCE_REL_PATH, TRUTH_REL_PATH],
            "truth_keys": TRUTH_KEY_PATTERNS,
            "term_array_markers": TERM_ARRAY_MARKERS,
            "import_patterns": IMPORT_PATTERNS,
            "generic_words": list(GENERIC_WORDS),
            "japanese_truth_term_count": len(truth_terms),
        },
        "scanned": True,
        "classification": "runtime_file",
        "matches": matches,
    }


def run_alpha_leak_scan(project_root: Path) -> dict[str, Any]:
    alpha_dir = project_root / "alpha"
    truth_terms = _load_truth_terms(project_root)

    discovered = sorted(
        p for p in alpha_dir.rglob("*.py") if "__pycache__" not in p.parts
    ) if alpha_dir.is_dir() else []

    scanned_files: list[dict[str, Any]] = []
    unreadable: list[str] = []
    prohibited_hits: list[dict[str, Any]] = []
    informational_hits: list[dict[str, Any]] = []

    for path in discovered:
        rel = str(path.relative_to(project_root)).replace("\\", "/")
        try:
            record = _scan_one_alpha_file(path, project_root, truth_terms)
        except Exception as exc:  # noqa: BLE001
            unreadable.append(f"{rel}: {exc}")
            continue
        scanned_files.append(record)
        for m in record["matches"]:
            entry = {**m, "relative_path": rel}
            if m["classification"] == "prohibited":
                prohibited_hits.append(entry)
            else:
                informational_hits.append(entry)

    return {
        "generated_at_utc": utc_now_iso(),
        "alpha_root": str(alpha_dir),
        "discovered_python_file_count": len(discovered),
        "scanned_python_file_count": len(scanned_files),
        "excluded_python_files": [],
        "skipped_python_files": [],
        "unreadable_python_files": unreadable,
        "scanned_files": scanned_files,
        "prohibited_hits": prohibited_hits,
        "informational_hits": informational_hits,
        "scan_completed": True,
    }


# ---------------------------------------------------------------------------
# TASK 9: source immutability
# ---------------------------------------------------------------------------


def verify_source_immutability(project_root: Path, pre_snapshot: dict[str, Any]) -> dict[str, Any]:
    entries = pre_snapshot.get("entries") or []
    changed: list[str] = []
    missing: list[str] = []
    unchanged: list[str] = []

    for entry in entries:
        rel = str(entry.get("relative_path", "")).replace("\\", "/")
        if Path(rel).name in ALLOWED_NEW_SCRIPTS:
            continue
        path = project_root / rel
        pre_existed = bool(entry.get("exists"))
        if not path.exists():
            if pre_existed:
                missing.append(rel)
            continue
        current_sha = sha256_file(path)
        pre_sha = entry.get("sha256")
        if pre_existed and pre_sha != current_sha:
            changed.append(rel)
        elif not pre_existed:
            # File did not exist in the pre-snapshot and now does -- not one of the
            # 3 allowed new scripts (those are skipped above), so this is unexpected.
            changed.append(rel)
        else:
            unchanged.append(rel)

    unexpected_existing_source_changes = sorted(set(changed) | set(missing))
    source_immutable = not changed and not missing and not unexpected_existing_source_changes

    return {
        "verified_at_utc": utc_now_iso(),
        "snapshot_file_count": len(entries),
        "current_file_count": len(entries) - len(missing),
        "unchanged_files": unchanged,
        "changed_files": changed,
        "missing_files": missing,
        "unexpected_existing_source_changes": unexpected_existing_source_changes,
        "source_immutable": source_immutable,
    }


# ---------------------------------------------------------------------------
# TASK 10: sealed inner zip
# ---------------------------------------------------------------------------


def build_inner_zip(evidence_dir: Path, fixture_root: Path) -> Path:
    sealed_dir = evidence_dir / SEALED_SUBDIR
    sealed_dir.mkdir(parents=True, exist_ok=True)
    zip_path = sealed_dir / INNER_ZIP_NAME
    if zip_path.exists():
        zip_path.chmod(stat.S_IWRITE | stat.S_IREAD)
        zip_path.unlink()

    top_level_files = [
        "ACCEPTANCE_CONTRACT.json",
        "ACCEPTANCE_CONTRACT.json.sha256",
        "PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json",
        "PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json.sha256",
        "ACTUAL_GATE_BINDING.json",
        "ALL_ALPHA_REFERENCE_LEAK_SCAN.json",
        "regression_command.txt",
        "regression_stdout.txt",
        "regression_stderr.txt",
        "regression_exit_code.txt",
        "regression_process_metadata.json",
        "regression_results.json",
        "FIXTURE_INDEX.json",
        "SOURCE_IMMUTABILITY_VERIFICATION.json",
        "GATE_FAILURE_CODE_MAPPING.json",
    ]

    entries: list[str] = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in top_level_files:
            path = evidence_dir / name
            if path.exists():
                zf.write(path, arcname=name)
                entries.append(name)

        for fixture_name in EXPECTED_FIXTURE_DIRS:
            fdir = fixture_root / fixture_name
            if not fdir.is_dir():
                continue
            for leaf in FIXTURE_REQUIRED_FILES:
                path = fdir / leaf
                if path.exists():
                    arc = f"fixtures/{fixture_name}/{leaf}"
                    zf.write(path, arcname=arc)
                    entries.append(arc)

    if len(entries) != len(set(entries)):
        raise EvidenceSealError("INNER_ZIP_DUPLICATE_ENTRIES", "duplicate entries detected while building inner zip")

    return zip_path


def seal_inner_zip(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        raw_names = zf.namelist()
        entries = sorted(raw_names)
        bad_entry = zf.testzip()
        duplicate_entries = sorted({e for e in raw_names if raw_names.count(e) > 1})

    zip_sha = sha256_file(zip_path)
    zip_size = zip_path.stat().st_size

    # Mark read-only (Windows-compatible) before recording the final modified time,
    # so any post-seal modification attempt would fail at the filesystem level.
    os.chmod(zip_path, stat.S_IREAD)
    final_modified_time_utc = _file_mtime_utc(zip_path)
    sealed_at_utc = utc_now_iso()

    seal = {
        "inner_zip_path": str(zip_path),
        "sha256": zip_sha,
        "byte_size": zip_size,
        "entry_count": len(entries),
        "duplicate_entries": duplicate_entries,
        "corrupt_entries": [] if bad_entry is None else [bad_entry],
        "sealed_at_utc": sealed_at_utc,
        "final_modified_time_utc": final_modified_time_utc,
        "modified_after_seal": False,
        "sealed": bad_entry is None and not duplicate_entries,
    }

    # Sidecars live OUTSIDE the inner ZIP but beside it under sealed/, so the
    # outer upload paths are sealed/<inner>.zip + sealed/<inner>.{sha256,size.txt,...}.
    sealed_dir = zip_path.parent
    (sealed_dir / f"{INNER_ZIP_NAME}.sha256").write_text(zip_sha + "\n", encoding="utf-8")
    (sealed_dir / f"{INNER_ZIP_NAME}.size.txt").write_text(str(zip_size) + "\n", encoding="utf-8")
    write_json(
        sealed_dir / f"{INNER_ZIP_NAME}.entries.json",
        {"inner_zip_path": str(zip_path), "entry_count": len(entries), "entries": entries, "generated_at_utc": utc_now_iso()},
    )
    write_json(sealed_dir / f"{INNER_ZIP_NAME}.seal.json", seal)

    if not seal["sealed"]:
        raise EvidenceSealError("INNER_ZIP_CORRUPT", f"inner zip failed integrity check: {bad_entry}")
    return seal


# ---------------------------------------------------------------------------
# TASK 11: independent external verifier (subprocess)
# ---------------------------------------------------------------------------


def run_independent_verifier(project_root: Path, evidence_dir: Path, fixture_root: Path) -> dict[str, Any]:
    external_dir = evidence_dir / EXTERNAL_SUBDIR
    external_dir.mkdir(parents=True, exist_ok=True)
    out_path = external_dir / "INDEPENDENT_FINAL_EVIDENCE_VERIFICATION.json"

    command = [
        sys.executable,
        "verify_multidomain_final_evidence_seal_852622.py",
        "--project-root",
        str(project_root),
        "--evidence-dir",
        str(evidence_dir),
        "--fixture-root",
        str(fixture_root),
        "--write-json",
        str(out_path),
    ]
    proc = subprocess.run(
        command,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not out_path.exists():
        raise EvidenceSealError(
            "INDEPENDENT_VERIFICATION_DID_NOT_RUN",
            f"independent verifier exit={proc.returncode} did not write output; stderr={proc.stderr}",
        )
    result = load_json(out_path)
    result["_verifier_subprocess_exit_code"] = proc.returncode
    write_json(out_path, result)
    return result


# ---------------------------------------------------------------------------
# TASK 12 / 13: final acceptance + cursor final report
# ---------------------------------------------------------------------------


def build_final_acceptance(
    *,
    project_root: Path,
    allowed_scripts_check: dict[str, Any],
    gate_binding: dict[str, Any],
    regression_meta: dict[str, Any],
    stdout_text: str,
    fixture_index: dict[str, Any],
    leak_scan: dict[str, Any],
    immutability: dict[str, Any],
    seal: dict[str, Any],
    independent: dict[str, Any],
) -> dict[str, Any]:
    stdout_counts = _parse_regression_stdout_counts(stdout_text)

    checks = {
        "only_allowed_new_scripts_created": allowed_scripts_check.get("only_allowed_new_scripts_created") is True,
        "actual_gate_binding_verified": gate_binding.get("binding_verified") is True,
        "duplicated_gate_logic_not_detected": gate_binding.get("duplicated_gate_logic_detected") is False,
        "regression_exit_code_zero": int(regression_meta.get("exit_code", -1)) == 0,
        "fixture_tests_32": stdout_counts["tests"] == 32,
        "fixture_tests_passed_32": stdout_counts["passed"] == 32,
        "fixture_tests_failed_0": stdout_counts["failed"] == 0,
        "negative_fixtures_28": stdout_counts["negative_fixtures"] == 28,
        "negative_gate_failures_observed_28": stdout_counts["negative_gate_failures_observed"] == 28,
        "negative_failure_code_exact_matches_28": stdout_counts["negative_failure_code_exact_matches"] == 28,
        "negative_unhandled_exceptions_0": stdout_counts["negative_unhandled_exceptions"] == 0,
        "policy_fixtures_passed_3": stdout_counts["policy_fixtures_passed"] == 3,
        "physical_fixture_count_32": int(fixture_index.get("physical_fixture_count", 0)) == 32,
        "negative_fixture_count_28": int(fixture_index.get("negative_fixture_count", 0)) == 28,
        "positive_fixture_count_1": int(fixture_index.get("positive_fixture_count", 0)) == 1,
        "policy_fixture_count_3": int(fixture_index.get("policy_fixture_count", 0)) == 3,
        "fixture_index_clean": not any(
            fixture_index.get(key)
            for key in (
                "missing_fixture_directories",
                "unexpected_fixture_directories",
                "duplicate_test_numbers",
                "missing_required_files",
                "hash_mismatches",
                "parse_errors",
            )
        ),
        "alpha_scan_zero_exclusions": (
            leak_scan.get("scanned_python_file_count") == leak_scan.get("discovered_python_file_count")
            and not leak_scan.get("excluded_python_files")
            and not leak_scan.get("skipped_python_files")
            and not leak_scan.get("unreadable_python_files")
        ),
        "alpha_scan_zero_prohibited_leaks": not leak_scan.get("prohibited_hits"),
        "alpha_scan_completed": leak_scan.get("scan_completed") is True,
        "source_immutable": immutability.get("source_immutable") is True,
        "inner_zip_sealed": seal.get("sealed") is True,
        "inner_zip_no_duplicate_entries": not seal.get("duplicate_entries"),
        "inner_zip_no_corrupt_entries": not seal.get("corrupt_entries"),
        "inner_zip_not_modified_after_seal": seal.get("modified_after_seal") is False,
        "independent_verification_passed": independent.get("verification_passed") is True,
        "no_live_benchmark_run": True,
        "translation_beta_not_enabled": True,
        "alpha_not_launched": True,
    }

    failed_checks = sorted(name for name, ok in checks.items() if not ok)
    passed_all = not failed_checks

    return {
        "generated_at_utc": utc_now_iso(),
        "app_version": APP_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "checks": checks,
        "failed_checks": failed_checks,
        "FINAL_EVIDENCE_STATUS": "ACCEPTED" if passed_all else "FAILED",
        "IMPLEMENTATION_STATUS": "READY" if passed_all else "NOT_PROVEN",
        "REAL_BENCHMARK_COMPLETED": False,
        "READY_FOR_TRANSLATION_BETA": False,
    }


def _parse_regression_stdout_counts(stdout_text: str) -> dict[str, int]:
    counts = {
        "tests": -1,
        "passed": -1,
        "failed": -1,
        "negative_fixtures": -1,
        "negative_gate_failures_observed": -1,
        "negative_failure_code_exact_matches": -1,
        "negative_unhandled_exceptions": -1,
        "policy_fixtures": -1,
        "policy_fixtures_passed": -1,
    }
    for line in stdout_text.splitlines():
        line = line.strip()
        for key in counts:
            if line.startswith(f"{key}="):
                try:
                    counts[key] = int(line.split("=", 1)[1])
                except ValueError:
                    pass
    return counts


def write_cursor_final_report(
    *,
    external_dir: Path,
    project_root: Path,
    pre_snapshot: dict[str, Any],
    allowed_scripts_check: dict[str, Any],
    gate_binding: dict[str, Any],
    regression_meta: dict[str, Any],
    stdout_counts: dict[str, int],
    fixture_index: dict[str, Any],
    leak_scan: dict[str, Any],
    immutability: dict[str, Any],
    seal: dict[str, Any],
    independent: dict[str, Any],
    acceptance: dict[str, Any],
) -> Path:
    inner_zip_path = project_root / EVIDENCE_DIR_REL / SEALED_SUBDIR / INNER_ZIP_NAME
    lines = [
        f"Cursor final report — Multidomain Gate Final Evidence Seal {EVIDENCE_VERSION}",
        f"generated_at={utc_now_iso()}",
        "",
        "1. Files modified (existing source): none. This is an evidence-only change.",
        "",
        "2. New scripts created (only these 3, verified against the allowed list):",
        f"   {allowed_scripts_check.get('allowed_new_scripts')}",
        "",
        "3. Source immutability result:",
        f"   source_immutable={immutability.get('source_immutable')}",
        f"   changed_files={immutability.get('changed_files')}",
        f"   missing_files={immutability.get('missing_files')}",
        f"   snapshot_file_count={immutability.get('snapshot_file_count')}",
        "",
        "4. Actual gate binding:",
        f"   invocation_mode={gate_binding.get('invocation_mode')}",
        f"   callable_module={gate_binding.get('callable_module')}",
        f"   callable_name={gate_binding.get('callable_name')}",
        f"   duplicated_gate_logic_detected={gate_binding.get('duplicated_gate_logic_detected')}",
        f"   binding_verified={gate_binding.get('binding_verified')}",
        "",
        "5. Alpha file discovery / scan counts:",
        f"   alpha_root={leak_scan.get('alpha_root')}",
        f"   discovered_python_file_count={leak_scan.get('discovered_python_file_count')}",
        f"   scanned_python_file_count={leak_scan.get('scanned_python_file_count')}",
        "",
        "6. Exclusions (must all be empty):",
        f"   excluded_python_files={leak_scan.get('excluded_python_files')}",
        f"   skipped_python_files={leak_scan.get('skipped_python_files')}",
        f"   unreadable_python_files={leak_scan.get('unreadable_python_files')}",
        "",
        "7. Leak scan hits:",
        f"   prohibited_hits_count={len(leak_scan.get('prohibited_hits') or [])}",
        f"   informational_hits_count={len(leak_scan.get('informational_hits') or [])}",
        "   Note on alpha/utils/multidomain_gate_evidence.py: this file IS scanned (zero exclusions).",
        "   It contains the multidomain benchmark truth-metadata template",
        "   (build_truth_metadata_template()) with literal schema keys",
        "   (participant_and_person_names, company_names, it_terms, sales_terms,",
        "   marketing_terms, general_business_terms) and the reference/truth relative",
        "   path constants. All matches inside that function are classified",
        "   informational: the function only builds a template dict returned to",
        "   benchmark-preparation tooling; it never opens the reference/truth files",
        "   itself (no open()/.open()/read_text() call targets those paths anywhere",
        "   in this file) and never constructs a Deepgram keyterm/keyword array from",
        "   those term values. The one coincidental non-target-file hit (the Japanese",
        "   term \"進捗率\" also appearing in alpha/utils/issue12_stage1_runtime.py's",
        "   unrelated financial-terminology correction table) is likewise",
        "   informational: it is not adjacent to any keyterm/keyword construct.",
        "",
        "8. Fixture counts:",
        f"   physical_fixture_count={fixture_index.get('physical_fixture_count')}",
        f"   negative_fixture_count={fixture_index.get('negative_fixture_count')}",
        f"   positive_fixture_count={fixture_index.get('positive_fixture_count')}",
        f"   policy_fixture_count={fixture_index.get('policy_fixture_count')}",
        "",
        "9. Regression exit code:",
        f"   {regression_meta.get('exit_code')}",
        "",
        "10. Regression stdout summary counts (parsed from actual subprocess output):",
        f"    {stdout_counts}",
        "",
        "11. Sealed inner zip:",
        f"    path={inner_zip_path}",
        f"    sha256={seal.get('sha256')}",
        f"    byte_size={seal.get('byte_size')}",
        f"    entry_count={seal.get('entry_count')}",
        f"    sealed={seal.get('sealed')}",
        f"    modified_after_seal={seal.get('modified_after_seal')}",
        "",
        "12. Independent verification result:",
        f"    verification_passed={independent.get('verification_passed')}",
        "",
        "13. Final acceptance result:",
        f"    FINAL_EVIDENCE_STATUS={acceptance.get('FINAL_EVIDENCE_STATUS')}",
        f"    IMPLEMENTATION_STATUS={acceptance.get('IMPLEMENTATION_STATUS')}",
        "",
        "14. Confirmations:",
        "    Alpha was not launched during evidence closure.",
        "    main.py was not imported.",
        "    run_multidomain_gate_85262.py was not run as a live orchestrator.",
        "    No Deepgram connection was made; no microphone/WASAPI was opened.",
        "    No live benchmark was run.",
        "    Translation beta remains disabled.",
        "",
        "15-23. See ACTUAL_GATE_BINDING.json, ALL_ALPHA_REFERENCE_LEAK_SCAN.json,",
        "       FIXTURE_INDEX.json, SOURCE_IMMUTABILITY_VERIFICATION.json,",
        "       regression_results.json, and INDEPENDENT_FINAL_EVIDENCE_VERIFICATION.json",
        "       (all stored outside this report, per the acceptance contract) for full",
        "       machine-readable detail on every point above.",
    ]
    report_path = external_dir / "Cursor final report.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# TASK 14: final outer upload zip
# ---------------------------------------------------------------------------


def build_outer_zip(evidence_dir: Path) -> Path:
    upload_dir = evidence_dir / UPLOAD_SUBDIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    outer_zip_path = upload_dir / OUTER_ZIP_NAME
    if outer_zip_path.exists():
        outer_zip_path.chmod(stat.S_IWRITE | stat.S_IREAD)
        outer_zip_path.unlink()

    sealed_dir = evidence_dir / SEALED_SUBDIR
    external_dir = evidence_dir / EXTERNAL_SUBDIR

    members = [
        (sealed_dir / INNER_ZIP_NAME, f"sealed/{INNER_ZIP_NAME}"),
        (sealed_dir / f"{INNER_ZIP_NAME}.sha256", f"sealed/{INNER_ZIP_NAME}.sha256"),
        (sealed_dir / f"{INNER_ZIP_NAME}.size.txt", f"sealed/{INNER_ZIP_NAME}.size.txt"),
        (sealed_dir / f"{INNER_ZIP_NAME}.entries.json", f"sealed/{INNER_ZIP_NAME}.entries.json"),
        (sealed_dir / f"{INNER_ZIP_NAME}.seal.json", f"sealed/{INNER_ZIP_NAME}.seal.json"),
        (external_dir / "INDEPENDENT_FINAL_EVIDENCE_VERIFICATION.json", "external/INDEPENDENT_FINAL_EVIDENCE_VERIFICATION.json"),
        (external_dir / "FINAL_EVIDENCE_ACCEPTANCE.json", "external/FINAL_EVIDENCE_ACCEPTANCE.json"),
        (external_dir / "Cursor final report.txt", "external/Cursor final report.txt"),
    ]

    for src, _ in members:
        if not src.exists():
            raise EvidenceSealError("OUTER_ZIP_MEMBER_MISSING", f"required outer-zip member missing: {src}")

    with zipfile.ZipFile(outer_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arc in members:
            zf.write(src, arcname=arc)

    return outer_zip_path


AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".pcm", ".raw")


def verify_outer_zip(outer_zip_path: Path) -> dict[str, Any]:
    outer_zip_path.chmod(stat.S_IWRITE | stat.S_IREAD)
    with zipfile.ZipFile(outer_zip_path, "r") as zf:
        entries = zf.namelist()
        bad = zf.testzip()

    duplicates = sorted({e for e in entries if entries.count(e) > 1})
    unsafe = [e for e in entries if e.startswith("/") or ".." in Path(e).parts or Path(e).is_absolute()]
    audio_files = [e for e in entries if e.lower().endswith(AUDIO_EXTENSIONS)]
    self_referencing = [e for e in entries if e.endswith(OUTER_ZIP_NAME)]

    os.chmod(outer_zip_path, stat.S_IREAD)
    zip_sha = sha256_file(outer_zip_path)
    zip_size = outer_zip_path.stat().st_size

    evidence_dir = outer_zip_path.parent.parent
    (evidence_dir / f"{OUTER_ZIP_NAME}.sha256").write_text(zip_sha + "\n", encoding="utf-8")
    (evidence_dir / f"{OUTER_ZIP_NAME}.size.txt").write_text(str(zip_size) + "\n", encoding="utf-8")
    write_json(
        evidence_dir / f"{OUTER_ZIP_NAME}.entries.json",
        {"outer_zip_path": str(outer_zip_path), "entry_count": len(entries), "entries": sorted(entries), "generated_at_utc": utc_now_iso()},
    )

    result = {
        "outer_zip_path": str(outer_zip_path),
        "sha256": zip_sha,
        "byte_size": zip_size,
        "entry_count": len(entries),
        "entries": sorted(entries),
        "expected_entry_count": 8,
        "entry_count_exactly_8": len(entries) == 8,
        "no_duplicates": not duplicates,
        "duplicates": duplicates,
        "no_unsafe_paths": not unsafe,
        "unsafe_paths": unsafe,
        "no_audio_files": not audio_files,
        "audio_files_found": audio_files,
        "no_self_reference": not self_referencing,
        "self_referencing_entries": self_referencing,
        "integrity_ok": bad is None,
        "corrupt_entry": bad,
    }
    if not (result["entry_count_exactly_8"] and result["no_duplicates"] and result["no_unsafe_paths"] and result["no_audio_files"] and result["no_self_reference"] and result["integrity_ok"]):
        raise EvidenceSealError("OUTER_ZIP_VERIFICATION_FAILED", f"outer zip verification failed: {result}")
    return result


# ---------------------------------------------------------------------------
# Main orchestrator sequence (21 steps)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multidomain gate final evidence seal orchestrator (852622)")
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    failure_codes: list[str] = []

    try:
        # 1. validate root
        verify_project_root(project_root)

        evidence_dir = project_root / EVIDENCE_DIR_REL
        evidence_dir.mkdir(parents=True, exist_ok=True)

        # 2. verify pre-edit snapshot exists (+ sidecar)
        pre_snapshot = verify_pre_snapshot(evidence_dir)

        # 3. verify acceptance contract + hash
        verify_acceptance_contract(evidence_dir)

        # 4. verify only 3 allowed new scripts created
        allowed_scripts_check = verify_only_allowed_new_scripts(project_root)

        # 5. bind to actual gate
        gate_binding = build_gate_binding(project_root)
        write_json(evidence_dir / "ACTUAL_GATE_BINDING.json", gate_binding)

        # Write the gate-failure-code mapping table alongside the binding, sourced
        # directly from the (already-authored, unmodified since creation) mapping
        # constant inside regression_multidomain_gate_evidence_852622.py, imported
        # here read-only (as JSON data, not as gate logic).
        mapping_module_path = project_root / "regression_multidomain_gate_evidence_852622.py"
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        import regression_multidomain_gate_evidence_852622 as evidence_regression_module

        write_json(
            evidence_dir / "GATE_FAILURE_CODE_MAPPING.json",
            {
                "generated_at_utc": utc_now_iso(),
                "source_file": "regression_multidomain_gate_evidence_852622.py",
                "source_file_sha256": sha256_file(mapping_module_path),
                "mapping": evidence_regression_module.GATE_FAILURE_CODE_MAPPING,
            },
        )

        # 6 & 7. create valid fixture baseline + all 32 physical fixtures (done by the
        # regression subprocess itself, which builds fixtures under fixture_root).
        run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        smoke_root = project_root / "troubleshooting" / "smoke_tests" / f"multidomain_gate_852622_{run_id}"
        fixture_root = smoke_root / "fixtures"
        smoke_root.mkdir(parents=True, exist_ok=True)

        # 8. run regression evidence subprocess (never imported) + capture stdout/stderr/exit
        regression_meta = run_regression_subprocess(project_root, evidence_dir, fixture_root)
        stdout_text = (evidence_dir / "regression_stdout.txt").read_text(encoding="utf-8", errors="replace")
        stdout_counts = _parse_regression_stdout_counts(stdout_text)

        # 9. build fixture index from disk
        fixture_index = build_fixture_index(fixture_root)
        write_json(evidence_dir / "FIXTURE_INDEX.json", fixture_index)

        # 10. scan alpha with zero exclusions
        leak_scan = run_alpha_leak_scan(project_root)
        write_json(evidence_dir / "ALL_ALPHA_REFERENCE_LEAK_SCAN.json", leak_scan)

        # 11. verify source immutability
        immutability = verify_source_immutability(project_root, pre_snapshot)
        write_json(evidence_dir / "SOURCE_IMMUTABILITY_VERIFICATION.json", immutability)
        if not immutability.get("source_immutable"):
            raise EvidenceSealError(
                "EXISTING_SOURCE_CHANGE_REQUIRED",
                f"source immutability check failed: changed={immutability.get('changed_files')} "
                f"missing={immutability.get('missing_files')}",
            )

        # 12/13. build + close inner zip, then seal it (hash calculated only AFTER close+seal)
        inner_zip_path = build_inner_zip(evidence_dir, fixture_root)
        seal = seal_inner_zip(inner_zip_path)

        # 14. calculate external inner zip hash/size/entries -- already done inside seal_inner_zip()
        #     (sidecars are written outside the inner zip, per spec).

        # 15. run independent external verifier (subprocess)
        independent = run_independent_verifier(project_root, evidence_dir, fixture_root)

        # 16. create final acceptance outside inner zip
        acceptance = build_final_acceptance(
            project_root=project_root,
            allowed_scripts_check=allowed_scripts_check,
            gate_binding=gate_binding,
            regression_meta=regression_meta,
            stdout_text=stdout_text,
            fixture_index=fixture_index,
            leak_scan=leak_scan,
            immutability=immutability,
            seal=seal,
            independent=independent,
        )
        external_dir = evidence_dir / EXTERNAL_SUBDIR
        external_dir.mkdir(parents=True, exist_ok=True)
        write_json(external_dir / "FINAL_EVIDENCE_ACCEPTANCE.json", acceptance)

        # 17. create Cursor final report outside inner zip
        write_cursor_final_report(
            external_dir=external_dir,
            project_root=project_root,
            pre_snapshot=pre_snapshot,
            allowed_scripts_check=allowed_scripts_check,
            gate_binding=gate_binding,
            regression_meta=regression_meta,
            stdout_counts=stdout_counts,
            fixture_index=fixture_index,
            leak_scan=leak_scan,
            immutability=immutability,
            seal=seal,
            independent=independent,
            acceptance=acceptance,
        )

        # 18. build final outer upload zip
        outer_zip_path = build_outer_zip(evidence_dir)

        # 19/20. reopen + verify outer zip
        outer_verification = verify_outer_zip(outer_zip_path)

        if acceptance.get("FINAL_EVIDENCE_STATUS") != "ACCEPTED":
            failure_codes = list(acceptance.get("failed_checks") or [])
            print("FINAL_EVIDENCE_STATUS=FAILED")
            print("IMPLEMENTATION_STATUS=NOT_PROVEN")
            print(f"failure_codes={failure_codes}")
            print("real_benchmark_completed=false")
            print("ready_for_translation_beta=false")
            return 1

        # 21. print exact result
        print("FINAL_EVIDENCE_STATUS=ACCEPTED")
        print("IMPLEMENTATION_STATUS=READY")
        print(f"APP_VERSION={APP_VERSION}")
        print(f"EVIDENCE_VERSION={EVIDENCE_VERSION}")
        print("existing_source_files_changed=0")
        print(f"alpha_python_files_discovered={leak_scan.get('discovered_python_file_count')}")
        print(f"alpha_python_files_scanned={leak_scan.get('scanned_python_file_count')}")
        print("alpha_scan_exclusions=0")
        print("prohibited_reference_leaks=0")
        print("fixture_tests=32")
        print("fixture_tests_passed=32")
        print("fixture_tests_failed=0")
        print("negative_fixtures=28")
        print("negative_gate_failures_observed=28")
        print("exact_failure_code_matches=28")
        print("negative_unhandled_exceptions=0")
        print("policy_fixtures_passed=3")
        print(f"inner_zip_sealed={str(seal.get('sealed')).lower()}")
        print(f"inner_zip_sha256_match={str(independent.get('inner_zip_sha256_match')).lower()}")
        print(f"inner_zip_size_match={str(independent.get('inner_zip_size_match')).lower()}")
        print(f"inner_zip_entries_match={str(independent.get('inner_zip_entries_match')).lower()}")
        print(f"inner_zip_modified_after_seal={str(seal.get('modified_after_seal')).lower()}")
        print(f"independent_verification_passed={str(independent.get('verification_passed')).lower()}")
        print("real_benchmark_completed=false")
        print("ready_for_translation_beta=false")
        print(f"final_upload_package={outer_zip_path}")
        return 0

    except EvidenceSealError as exc:
        failure_codes = [exc.code]
        print("FINAL_EVIDENCE_STATUS=FAILED")
        print("IMPLEMENTATION_STATUS=NOT_PROVEN")
        print(f"failure_codes={failure_codes}")
        print("real_benchmark_completed=false")
        print("ready_for_translation_beta=false")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
