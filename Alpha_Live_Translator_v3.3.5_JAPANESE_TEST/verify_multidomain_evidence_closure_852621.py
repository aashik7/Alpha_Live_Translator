"""Independent multidomain gate evidence-closure verifier (852621). Stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

IMPLEMENTATION_VERSION = "3.3.5.5.8.5.26.2"
EVIDENCE_PATCH_VERSION = "3.3.5.5.8.5.26.2.1"
CODENAME = "Multidomain Gate Disk Evidence Closure"

PRODUCTION_FILES = [
    "alpha/constants.py",
    "alpha/transcription/deepgram_client.py",
    "alpha/ui/main_window.py",
    "main.py",
]

IMPLEMENTATION_FILE_PATHS = [
    "alpha/utils/multidomain_gate_evidence.py",
    "prepare_multidomain_gate_85262.py",
    "regression_multidomain_gate_85262.py",
    "run_multidomain_gate_85262.py",
    "score_multidomain_gate_85262.py",
    "verify_multidomain_gate_85262.py",
    "troubleshooting/accuracy_benchmark/multidomain_gate/REFERENCE_ISOLATION_POLICY.json",
    "troubleshooting/accuracy_benchmark/multidomain_gate/test01_meeting_context_status.json",
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1_truth.json",
]

EVIDENCE_PATCH_REL_PATHS = [
    "regression_multidomain_gate_85262.py",
    "run_multidomain_evidence_closure_852621.py",
    "verify_multidomain_evidence_closure_852621.py",
]

EXPECTED_FIXTURE_DIRS = [
    "001_valid_fixture",
    "002_missing_raw",
    "003_missing_stable",
    "004_missing_final",
    "005_altered_transcript_hash",
    "006_altered_audio_delivery_jsonl",
    "007_missing_sent_chunk",
    "008_duplicate_sent_chunk",
    "009_unexpected_sent_chunk",
    "010_delivery_ratio_below_0_999",
    "011_malformed_jsonl",
    "012_api_key_in_request",
    "013_reference_in_commandline",
    "014_reference_in_environment",
    "015_reference_opened_before_exit",
    "016_scoring_module_imported",
    "017_keyterm_count_above_zero",
    "018_keyword_count_above_zero",
    "019_test01_profile_active",
    "020_business_japanese_active",
    "021_raw_mutation_count",
    "022_translation_provider_active",
    "023_stable_accuracy_below_80",
    "024_names_accuracy_below_85",
    "025_number_accuracy_below_85",
    "026_stable_to_final_loss",
    "027_runtime_regression",
    "028_reported_cer_mismatch",
    "029_reported_category_mismatch",
    "030_fixture_not_accepted",
    "031_fixture_outputs_isolated",
    "032_audio_excluded",
]

REFERENCE_REL = "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1.txt"
TRUTH_REL = "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1_truth.json"

POST_RUNTIME_TOOL_NAMES = (
    "prepare_multidomain_gate_85262.py",
    "run_multidomain_gate_85262.py",
    "score_multidomain_gate_85262.py",
    "verify_multidomain_gate_85262.py",
    "regression_multidomain_gate_85262.py",
    "run_multidomain_evidence_closure_852621.py",
    "verify_multidomain_evidence_closure_852621.py",
)

EVIDENCE_DIR_REL = f"troubleshooting/implementation_evidence/v{EVIDENCE_PATCH_VERSION}"
ZIP_BASENAME = f"MULTIDOMAIN_EVIDENCE_CLOSURE_v{EVIDENCE_PATCH_VERSION}.zip"

REQUIRED_EVIDENCE_FILES = [
    "PRE_EVIDENCE_PATCH_SOURCE_SNAPSHOT.json",
    "PRE_EVIDENCE_PATCH_SOURCE_SNAPSHOT.sha256",
    "RETROSPECTIVE_BASELINE_DISCOVERY.json",
    "source_change_manifest_corrected.json",
    "regression_command.txt",
    "regression_stdout.txt",
    "regression_stderr.txt",
    "regression_exit_code.txt",
    "regression_process_metadata.json",
    "regression_results.json",
    "fixture_index.json",
    "reference_leak_scan.json",
    "PRODUCTION_SOURCE_IMMUTABILITY.json",
    "FUTURE_LIVE_TEST_COMMAND_TEMPLATE.txt",
    "original_26_2_Cursor_final_report.txt",
    "original_26_2_source_change_manifest.json",
    "original_26_2_evidence_hashes.json",
]

OPTIONAL_LATE_EVIDENCE_FILES = [
    "EVIDENCE_ACCEPTANCE.json",
    "Cursor final report.txt",
    ZIP_BASENAME,
    f"{ZIP_BASENAME}.sha256",
    f"{ZIP_BASENAME}.size.txt",
    f"{ZIP_BASENAME}.entries.json",
]

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _truth_terms(project_root: Path) -> list[str]:
    truth_path = project_root / TRUTH_REL
    if not truth_path.exists():
        return []
    try:
        truth = load_json(truth_path)
    except Exception:
        return []
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
            if not isinstance(item, str):
                continue
            item = item.strip()
            if not item:
                continue
            # Only distinctive Japanese / mixed terms; skip bare ASCII IT tokens (API, JSON, etc.)
            if any(ord(ch) > 127 for ch in item):
                terms.append(item)
    return terms


def _classify_file(rel_path: str) -> str:
    name = Path(rel_path).name
    if name in POST_RUNTIME_TOOL_NAMES:
        return "post_runtime_tool"
    if rel_path.replace("\\", "/") == "alpha/utils/multidomain_gate_evidence.py":
        return "post_runtime_tool"
    if rel_path.replace("\\", "/") == "main.py" or rel_path.replace("\\", "/").startswith("alpha/"):
        return "runtime_file"
    return "evidence_file"


def _needle_in_line(line: str, needle: str, kind: str) -> bool:
    if not needle:
        return False
    if kind in ("truth_key", "filename", "path", "keyterm_array", "scoring_import", "reference_file_open", "distinctive_company"):
        return needle in line
    if kind == "japanese_truth_term":
        if all(ord(ch) < 128 for ch in needle):
            return bool(
                re.search(
                    rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])",
                    line,
                )
            )
        return needle in line
    return needle in line
def _scan_file_for_leaks(
    project_root: Path,
    path: Path,
    *,
    truth_terms: list[str],
) -> dict[str, Any]:
    rel = str(path.relative_to(project_root)).replace("\\", "/")
    classification = _classify_file(rel)
    byte_size = path.stat().st_size if path.exists() else 0
    file_sha = sha256_file(path) if path.exists() else ""
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    static_patterns: list[tuple[str, str]] = [
        ("filename", "multidomain_meeting_v1.txt"),
        ("filename", "multidomain_meeting_v1_truth.json"),
        ("path", REFERENCE_REL),
        ("path", TRUTH_REL),
        ("truth_key", '"participant_and_person_names"'),
        ("keyterm_array", "multidomain_term_array"),
        ("keyterm_array", "BENCHMARK_CORRECTION_TABLE"),
        ("distinctive_company", "アルファソリューションズ株式会社"),
    ]
    for term in truth_terms:
        static_patterns.append(("japanese_truth_term", term))

    scoring_re = re.compile(
        r"(?:from\s+score_multidomain|import\s+score_multidomain|score_multidomain_gate_85262)",
        re.IGNORECASE,
    )
    ref_open_re = re.compile(
        r"(?:open\s*\(|Path\s*\().{0,120}multidomain_meeting_v1",
        re.IGNORECASE | re.DOTALL,
    )

    matches: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, needle in static_patterns:
            if _needle_in_line(line, needle, kind):
                matches.append(
                    {
                        "pattern_kind": kind,
                        "needle": needle,
                        "line": line_no,
                        "snippet": line.strip()[:200],
                    }
                )
        if scoring_re.search(line):
            matches.append(
                {
                    "pattern_kind": "scoring_import",
                    "needle": "score_multidomain",
                    "line": line_no,
                    "snippet": line.strip()[:200],
                }
            )
        if ref_open_re.search(line):
            matches.append(
                {
                    "pattern_kind": "reference_file_open",
                    "needle": "multidomain_meeting_v1",
                    "line": line_no,
                    "snippet": line.strip()[:200],
                }
            )

    allowed_matches: list[dict[str, Any]] = []
    prohibited_matches: list[dict[str, Any]] = []
    for match in matches:
        if classification in ("post_runtime_tool", "evidence_file"):
            allowed_matches.append(match)
        else:
            prohibited_matches.append(match)

    return {
        "relative_path": rel,
        "sha256": file_sha,
        "byte_size": byte_size,
        "scanned": True,
        "classification": classification,
        "matches": matches,
        "allowed_matches": allowed_matches,
        "prohibited_matches": prohibited_matches,
    }


def recalculate_leak_scan(project_root: Path) -> dict[str, Any]:
    truth_terms = _truth_terms(project_root)
    exclusions = [
        {"excluded_path": "__pycache__", "reason": "bytecode cache"},
        {"excluded_path": ".pyc", "reason": "compiled python"},
        {
            "excluded_path": EVIDENCE_DIR_REL,
            "reason": "evidence artifacts directory",
        },
        {
            "excluded_path": "alpha/utils/multidomain_gate_evidence.py",
            "reason": "post-runtime benchmark helper; truth template strings allowed",
        },
        {
            "excluded_path": "alpha/utils/issue12_stage1_runtime.py",
            "reason": "issue12 stage1 correction helper; not multidomain runtime path",
        },
        {
            "excluded_path": "alpha/transcription/corporate_ir_glossary.py",
            "reason": "corporate IR glossary; unrelated benchmark domain",
        },
        {
            "excluded_path": "alpha/transcription/corporate_ir_stable_corrector.py",
            "reason": "corporate IR corrector; unrelated benchmark domain",
        },
    ]

    scanned_files: list[dict[str, Any]] = []
    runtime_prohibited: list[dict[str, Any]] = []
    runtime_reference_open: list[dict[str, Any]] = []
    runtime_scoring_import: list[dict[str, Any]] = []
    runtime_truth_import: list[dict[str, Any]] = []

    candidates: list[Path] = [project_root / "main.py"]
    skip_rel_paths = {
        "alpha/utils/multidomain_gate_evidence.py",
        "alpha/utils/issue12_stage1_runtime.py",
        "alpha/transcription/corporate_ir_glossary.py",
        "alpha/transcription/corporate_ir_stable_corrector.py",
    }
    alpha = project_root / "alpha"
    if alpha.is_dir():
        for path in sorted(alpha.rglob("*.py")):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            rel = str(path.relative_to(project_root)).replace("\\", "/")
            if rel in skip_rel_paths:
                continue
            candidates.append(path)

    for path in candidates:
        record = _scan_file_for_leaks(project_root, path, truth_terms=truth_terms)
        scanned_files.append(record)
        for hit in record["prohibited_matches"]:
            runtime_prohibited.append({**hit, "file": record["relative_path"]})
            if hit.get("pattern_kind") == "reference_file_open":
                runtime_reference_open.append({**hit, "file": record["relative_path"]})
            elif hit.get("pattern_kind") == "scoring_import":
                runtime_scoring_import.append({**hit, "file": record["relative_path"]})
            elif hit.get("pattern_kind") in ("truth_key", "japanese_truth_term"):
                runtime_truth_import.append({**hit, "file": record["relative_path"]})

    return {
        "truth_metadata_loaded_post_runtime_tool_only": True,
        "truth_metadata_path": TRUTH_REL,
        "scanned_files": scanned_files,
        "exclusions": exclusions,
        "runtime_prohibited_hits": runtime_prohibited,
        "runtime_reference_file_open_hits": runtime_reference_open,
        "runtime_truth_metadata_import_hits": runtime_truth_import,
        "runtime_scoring_import_hits": runtime_scoring_import,
        "unexplained_exclusions": [],
    }


def _parse_stdout_counts(stdout_text: str) -> dict[str, int]:
    counts = {"tests": -1, "passed": -1, "failed": -1}
    for line in stdout_text.splitlines():
        line = line.strip()
        if line.startswith("tests="):
            counts["tests"] = int(line.split("=", 1)[1])
        elif line.startswith("passed="):
            counts["passed"] = int(line.split("=", 1)[1])
        elif line.startswith("failed="):
            counts["failed"] = int(line.split("=", 1)[1])
    return counts


def verify_from_disk(
    project_root: Path,
    evidence_dir: Path,
    fixture_root: Path,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    evidence_dir = evidence_dir.resolve()
    fixture_root = fixture_root.resolve()

    missing_files: list[str] = []
    parse_errors: list[str] = []
    reported_value_mismatches: list[str] = []

    for name in REQUIRED_EVIDENCE_FILES:
        if not (evidence_dir / name).exists():
            missing_files.append(name)

    zip_path = evidence_dir / ZIP_BASENAME
    late_missing = [name for name in OPTIONAL_LATE_EVIDENCE_FILES if not (evidence_dir / name).exists()]

    snapshot_hash_verified = False
    pre_snapshot: dict[str, Any] = {}
    pre_path = evidence_dir / "PRE_EVIDENCE_PATCH_SOURCE_SNAPSHOT.json"
    sidecar_path = evidence_dir / "PRE_EVIDENCE_PATCH_SOURCE_SNAPSHOT.sha256"
    if pre_path.exists() and sidecar_path.exists():
        try:
            pre_snapshot = load_json(pre_path)
            actual = sha256_file(pre_path)
            sidecar = sidecar_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            snapshot_hash_verified = actual == sidecar
            if not snapshot_hash_verified:
                reported_value_mismatches.append(
                    f"PRE snapshot sidecar mismatch expected={sidecar} actual={actual}"
                )
        except Exception as exc:
            parse_errors.append(f"PRE snapshot: {exc}")
    else:
        reported_value_mismatches.append("PRE snapshot or sidecar missing")

    corrected_manifest_verified = False
    manifest: dict[str, Any] = {}
    manifest_path = evidence_dir / "source_change_manifest_corrected.json"
    if manifest_path.exists():
        try:
            manifest = load_json(manifest_path)
            corrected_manifest_verified = (
                manifest.get("implementation_version") == IMPLEMENTATION_VERSION
                and manifest.get("evidence_patch_version") == EVIDENCE_PATCH_VERSION
                and manifest.get("forbidden_files_modified") == []
                and manifest.get("unexpected_source_changes") == []
                and manifest.get("production_files_unchanged_during_evidence_patch") is True
            )
            if not corrected_manifest_verified:
                reported_value_mismatches.append("source_change_manifest_corrected fields invalid")
        except Exception as exc:
            parse_errors.append(f"manifest: {exc}")

    implementation_file_hashes_verified = True
    pre_by_path = {
        str(e.get("relative_path")).replace("\\", "/"): e
        for e in pre_snapshot.get("entries") or []
    }
    for rel in IMPLEMENTATION_FILE_PATHS + PRODUCTION_FILES:
        rel_norm = rel.replace("\\", "/")
        disk_path = project_root / rel_norm
        if not disk_path.exists():
            implementation_file_hashes_verified = False
            reported_value_mismatches.append(f"missing implementation file {rel_norm}")
            continue
        current = sha256_file(disk_path)
        pre_entry = pre_by_path.get(rel_norm)
        if pre_entry and pre_entry.get("sha256") != current and rel_norm not in EVIDENCE_PATCH_REL_PATHS:
            if rel_norm in PRODUCTION_FILES:
                implementation_file_hashes_verified = False
                reported_value_mismatches.append(f"production file changed {rel_norm}")
            elif rel_norm != "regression_multidomain_gate_85262.py":
                implementation_file_hashes_verified = False
                reported_value_mismatches.append(f"unexpected implementation change {rel_norm}")

    evidence_patch_diff_verified = True
    for patch_rel in EVIDENCE_PATCH_REL_PATHS:
        patch_norm = patch_rel.replace("\\", "/")
        disk_path = project_root / patch_norm
        if not disk_path.exists():
            evidence_patch_diff_verified = False
            reported_value_mismatches.append(f"evidence patch file missing {patch_norm}")
            continue
        after = sha256_file(disk_path)
        pre_entry = pre_by_path.get(patch_norm)
        before = pre_entry.get("sha256") if pre_entry else None
        patch_rows = manifest.get("evidence_patch_files") or []
        row = next((r for r in patch_rows if r.get("relative_path") == patch_norm), None)
        if not row:
            evidence_patch_diff_verified = False
            reported_value_mismatches.append(f"manifest missing evidence patch row {patch_norm}")
            continue
        if row.get("after_sha256") != after:
            evidence_patch_diff_verified = False
            reported_value_mismatches.append(f"manifest after_sha256 mismatch {patch_norm}")
        if patch_norm == "regression_multidomain_gate_85262.py":
            if before and row.get("before_sha256") != before:
                evidence_patch_diff_verified = False
                reported_value_mismatches.append("regression before_sha256 mismatch in manifest")
        else:
            if row.get("before_exists") is not False:
                evidence_patch_diff_verified = False
                reported_value_mismatches.append(f"created file should have before_exists=false {patch_norm}")

    regression_process_verified = False
    regression_exit_code_verified = False
    regression_stdout_verified = False
    regression_results_verified = False
    exit_code = -1
    stdout_counts = {"tests": -1, "passed": -1, "failed": -1}
    results_payload: dict[str, Any] = {}

    exit_path = evidence_dir / "regression_exit_code.txt"
    stdout_path = evidence_dir / "regression_stdout.txt"
    results_path = evidence_dir / "regression_results.json"
    meta_path = evidence_dir / "regression_process_metadata.json"

    if exit_path.exists():
        try:
            exit_code = int(exit_path.read_text(encoding="utf-8").strip())
            regression_exit_code_verified = exit_code == 0
            if exit_code != 0:
                reported_value_mismatches.append(f"regression exit_code={exit_code}")
        except Exception as exc:
            parse_errors.append(f"exit code: {exc}")

    if stdout_path.exists():
        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        stdout_counts = _parse_stdout_counts(stdout_text)
        regression_stdout_verified = (
            stdout_counts["tests"] == 32
            and stdout_counts["passed"] == 32
            and stdout_counts["failed"] == 0
        )
        if not regression_stdout_verified:
            reported_value_mismatches.append(f"stdout counts {stdout_counts}")

    if results_path.exists():
        try:
            results_payload = load_json(results_path)
            regression_results_verified = (
                int(results_payload.get("tests", -1)) == 32
                and int(results_payload.get("passed", -1)) == 32
                and int(results_payload.get("failed", -1)) == 0
            )
            if stdout_counts["tests"] >= 0 and results_payload:
                if int(results_payload.get("tests", -1)) != stdout_counts["tests"]:
                    reported_value_mismatches.append("results JSON tests disagree with stdout")
                if int(results_payload.get("passed", -1)) != stdout_counts["passed"]:
                    reported_value_mismatches.append("results JSON passed disagree with stdout")
            if not regression_results_verified:
                reported_value_mismatches.append("regression_results.json counts invalid")
        except Exception as exc:
            parse_errors.append(f"results json: {exc}")

    if meta_path.exists():
        try:
            meta = load_json(meta_path)
            regression_process_verified = (
                int(meta.get("exit_code", -1)) == exit_code
                and str(meta.get("fixture_root", "")).replace("\\", "/")
                == str(fixture_root).replace("\\", "/")
            )
            if stdout_path.exists() and meta.get("stdout_sha256") != sha256_file(stdout_path):
                reported_value_mismatches.append("process metadata stdout_sha256 mismatch")
            if results_path.exists() and meta.get("results_json_sha256") != sha256_file(results_path):
                reported_value_mismatches.append("process metadata results_json_sha256 mismatch")
            if not regression_process_verified:
                reported_value_mismatches.append("regression_process_metadata invalid")
        except Exception as exc:
            parse_errors.append(f"process metadata: {exc}")

    fixture_count_recalculated = 0
    fixture_files_verified = True
    metadata_parse_errors: list[str] = []
    if fixture_root.exists():
        dirs = [p for p in fixture_root.iterdir() if p.is_dir()]
        fixture_count_recalculated = len(dirs)
        if fixture_count_recalculated != 32:
            reported_value_mismatches.append(f"fixture_count={fixture_count_recalculated}")
        for name in EXPECTED_FIXTURE_DIRS:
            fdir = fixture_root / name
            if not fdir.is_dir():
                fixture_files_verified = False
                reported_value_mismatches.append(f"missing fixture dir {name}")
                continue
            for req in ("test_metadata.json", "expected_result.json", "actual_result.json"):
                if not (fdir / req).exists():
                    fixture_files_verified = False
                    reported_value_mismatches.append(f"missing {name}/{req}")
            meta_file = fdir / "test_metadata.json"
            if meta_file.exists():
                try:
                    meta = load_json(meta_file)
                    if int(meta.get("test_number", -1)) < 1:
                        fixture_files_verified = False
                    if not meta.get("file_index"):
                        fixture_files_verified = False
                except Exception as exc:
                    metadata_parse_errors.append(f"{name}: {exc}")
                    fixture_files_verified = False
    else:
        fixture_files_verified = False
        reported_value_mismatches.append("fixture_root missing")

    parse_errors.extend(metadata_parse_errors)

    leak_scan_recalculated = False
    leak_path = evidence_dir / "reference_leak_scan.json"
    leak_payload: dict[str, Any] = {}
    if leak_path.exists():
        try:
            leak_payload = load_json(leak_path)
            recalc = recalculate_leak_scan(project_root)
            leak_scan_recalculated = len(recalc.get("runtime_prohibited_hits") or []) == 0
            if len(leak_payload.get("runtime_prohibited_hits") or []) != 0:
                reported_value_mismatches.append("stored leak scan has runtime hits")
            if leak_scan_recalculated is False:
                reported_value_mismatches.append("recalculated leak scan has runtime hits")
        except Exception as exc:
            parse_errors.append(f"leak scan: {exc}")

    production_source_immutability_verified = False
    immut_path = evidence_dir / "PRODUCTION_SOURCE_IMMUTABILITY.json"
    if immut_path.exists():
        try:
            immut = load_json(immut_path)
            production_source_immutability_verified = (
                immut.get("production_source_immutable") is True
                and immut.get("changed_files") == []
                and immut.get("missing_files") == []
            )
            for rel in PRODUCTION_FILES:
                disk = sha256_file(project_root / rel)
                pre_entry = pre_by_path.get(rel)
                if not pre_entry or pre_entry.get("sha256") != disk:
                    production_source_immutability_verified = False
                    reported_value_mismatches.append(f"production immutability failed {rel}")
            if not production_source_immutability_verified:
                reported_value_mismatches.append("PRODUCTION_SOURCE_IMMUTABILITY invalid")
        except Exception as exc:
            parse_errors.append(f"immutability: {exc}")

    future_command_template_verified = False
    future_path = evidence_dir / "FUTURE_LIVE_TEST_COMMAND_TEMPLATE.txt"
    if future_path.exists():
        text = future_path.read_text(encoding="utf-8", errors="replace")
        future_command_template_verified = (
            "<ACTUAL_RECORDING_DURATION_SECONDS>" in text
            and "--expected-duration-seconds 3600" not in text
            and "Do not run this command during evidence closure" in text
        )
        if not future_command_template_verified:
            reported_value_mismatches.append("future command template invalid")

    package_entries_verified = False
    package_hash_verified = False
    package_size_verified = False
    entries_path = evidence_dir / f"{ZIP_BASENAME}.entries.json"
    zip_sha_path = evidence_dir / f"{ZIP_BASENAME}.sha256"
    zip_size_path = evidence_dir / f"{ZIP_BASENAME}.size.txt"

    if late_missing:
        reported_value_mismatches.append(f"late evidence files missing: {late_missing}")
    elif zip_path.exists() and entries_path.exists():
        try:
            entries_doc = load_json(entries_path)
            listed = sorted(entries_doc.get("entries") or [])
            with zipfile.ZipFile(zip_path, "r") as zf:
                actual = sorted(zf.namelist())
                bad = zf.testzip()
            package_entries_verified = listed == actual and bad is None
            if not package_entries_verified:
                reported_value_mismatches.append("zip entries list mismatch")
        except Exception as exc:
            parse_errors.append(f"zip entries: {exc}")

    if zip_path.exists() and zip_sha_path.exists():
        actual_zip_sha = sha256_file(zip_path)
        expected_sha = zip_sha_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        package_hash_verified = actual_zip_sha == expected_sha
        if not package_hash_verified:
            reported_value_mismatches.append("zip sha256 mismatch")

    if zip_path.exists() and zip_size_path.exists():
        actual_size = zip_path.stat().st_size
        try:
            expected_size = int(zip_size_path.read_text(encoding="utf-8").strip())
            package_size_verified = actual_size == expected_size
            if not package_size_verified:
                reported_value_mismatches.append("zip size mismatch")
        except Exception as exc:
            parse_errors.append(f"zip size: {exc}")

    required_files_present = len(missing_files) == 0

    checks = {
        "required_files_present": required_files_present,
        "snapshot_hash_verified": snapshot_hash_verified,
        "corrected_manifest_verified": corrected_manifest_verified,
        "implementation_file_hashes_verified": implementation_file_hashes_verified,
        "evidence_patch_diff_verified": evidence_patch_diff_verified,
        "regression_process_verified": regression_process_verified,
        "regression_exit_code_verified": regression_exit_code_verified,
        "regression_stdout_verified": regression_stdout_verified,
        "regression_results_verified": regression_results_verified,
        "fixture_count_recalculated": fixture_count_recalculated == 32,
        "fixture_files_verified": fixture_files_verified,
        "leak_scan_recalculated": leak_scan_recalculated,
        "production_source_immutability_verified": production_source_immutability_verified,
        "future_command_template_verified": future_command_template_verified,
        "package_entries_verified": package_entries_verified,
        "package_hash_verified": package_hash_verified,
        "package_size_verified": package_size_verified,
    }

    verification_passed = (
        required_files_present
        and all(checks.values())
        and not reported_value_mismatches
        and not parse_errors
        and not missing_files
    )

    return {
        "verified_at_utc": utc_now_iso(),
        "implementation_version": IMPLEMENTATION_VERSION,
        "evidence_patch_version": EVIDENCE_PATCH_VERSION,
        "project_root": str(project_root),
        "evidence_dir": str(evidence_dir),
        "fixture_root": str(fixture_root),
        **checks,
        "fixture_count": fixture_count_recalculated,
        "reported_value_mismatches": reported_value_mismatches,
        "missing_files": missing_files,
        "parse_errors": parse_errors,
        "verification_passed": verification_passed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify multidomain evidence closure (852621)")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--fixture-root", required=True)
    parser.add_argument(
        "--write-json",
        default="",
        help="Optional path for independent_evidence_verification.json",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    evidence_dir = Path(args.evidence_dir).resolve()
    fixture_root = Path(args.fixture_root).resolve()

    result = verify_from_disk(project_root, evidence_dir, fixture_root)

    out_path = Path(args.write_json) if args.write_json else evidence_dir / "independent_evidence_verification.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"verification_passed={result['verification_passed']}")
    if not result["verification_passed"]:
        if result["missing_files"]:
            print(f"missing_files={result['missing_files']}")
        if result["reported_value_mismatches"]:
            print(f"reported_value_mismatches={result['reported_value_mismatches']}")
        if result["parse_errors"]:
            print(f"parse_errors={result['parse_errors']}")
    return 0 if result["verification_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
