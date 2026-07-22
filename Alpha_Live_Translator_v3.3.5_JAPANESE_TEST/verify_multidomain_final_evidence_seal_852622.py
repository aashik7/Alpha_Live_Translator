"""Independent external verifier for the Multidomain Gate Final Evidence Seal (852622).

Python standard library only. Deliberately does NOT import
run_multidomain_final_evidence_seal_852622.py or
regression_multidomain_gate_evidence_852622.py, and does NOT trust
regression_results.json's summary counts, the Cursor final report, or the final
acceptance JSON. Every figure in the output below is independently recomputed
from primary artifacts on disk: the acceptance contract file itself, the actual
fixture directories/files, the actual alpha/*.py source tree, the actual pre-edit
source snapshot, and the actual sealed inner ZIP bytes.

Never modifies the inner ZIP. Never launches Alpha, never imports main.py, never
runs any gate script as a live orchestrator.
"""

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

EVIDENCE_VERSION = "3.3.5.5.8.5.26.2.2"
INNER_ZIP_NAME = f"MULTIDOMAIN_EVIDENCE_INNER_v{EVIDENCE_VERSION}.zip"

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
POLICY_FIXTURE_DIRS = set(EXPECTED_FIXTURE_DIRS[29:32])

EXPECTED_POLICY_RESULT = {
    "030_fixture_cannot_create_accepted_result": "FIXTURE_ACCEPTANCE_BLOCKED",
    "031_fixture_cannot_overwrite_latest_live_artifacts": "LATEST_LIVE_ARTIFACTS_UNCHANGED",
    "032_audio_files_excluded_from_package": "AUDIO_FILES_EXCLUDED",
}

ALLOWED_NEW_SCRIPTS = {
    "regression_multidomain_gate_evidence_852622.py",
    "run_multidomain_final_evidence_seal_852622.py",
    "verify_multidomain_final_evidence_seal_852622.py",
}

GENERIC_WORDS = ("keyterm", "keyword", "glossary", "reference")
FILENAME_PATTERNS = ["multidomain_meeting_v1.txt", "multidomain_meeting_v1_truth.json"]
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
IMPORT_PATTERNS = [
    "score_multidomain_gate_85262",
    "verify_multidomain_gate_85262",
    "run_multidomain_gate_85262",
]
_OPEN_NEAR_RE = re.compile(r"(?:open\s*\(|\.open\s*\(|read_text\s*\(|read_bytes\s*\()", re.IGNORECASE)
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+", re.MULTILINE)
_KEYTERM_ARRAY_NEARBY_RE = re.compile(r"keyterm|keyword|JAPANESE_KEYTERMS", re.IGNORECASE)


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


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Acceptance contract + hash
# ---------------------------------------------------------------------------


def verify_acceptance_contract(evidence_dir: Path, missing_files: list[str], parse_errors: list[str]) -> bool:
    contract_path = evidence_dir / "ACCEPTANCE_CONTRACT.json"
    sidecar_path = evidence_dir / "ACCEPTANCE_CONTRACT.json.sha256"
    if not contract_path.exists():
        missing_files.append(str(contract_path))
        return False
    if not sidecar_path.exists():
        missing_files.append(str(sidecar_path))
        return False
    try:
        actual_sha = sha256_file(contract_path)
        sidecar_sha = sidecar_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if actual_sha != sidecar_sha:
            return False
        contract = load_json(contract_path)
    except Exception as exc:  # noqa: BLE001
        parse_errors.append(f"ACCEPTANCE_CONTRACT.json: {exc}")
        return False

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
    return contract == expected


# ---------------------------------------------------------------------------
# 2. Alpha scan re-derivation (independent implementation; duplicated on purpose)
# ---------------------------------------------------------------------------


def _load_truth_terms_independently(project_root: Path) -> list[str]:
    truth_path = project_root / "alpha" / "utils" / "multidomain_gate_evidence.py"
    if not truth_path.exists():
        return []
    text = truth_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"def\s+build_truth_metadata_template\s*\([^)]*\)[^\{]*\{", text)
    # Simpler and fully independent: extract each list literal that follows a known key,
    # without importing the module (per the "stdlib only, no orchestrator/module import" rule).
    terms: list[str] = []
    for key in (
        "participant_and_person_names",
        "company_names",
        "it_terms",
        "sales_terms",
        "marketing_terms",
        "general_business_terms",
    ):
        m = re.search(rf'"{key}"\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if not m:
            continue
        block = m.group(1)
        for item_match in re.finditer(r'"((?:[^"\\]|\\.)*)"', block):
            raw = item_match.group(1)
            try:
                value = json.loads(f'"{raw}"')
            except Exception:  # noqa: BLE001
                value = raw
            if value.strip() and any(ord(ch) > 127 for ch in value):
                terms.append(value.strip())
    return terms


def _template_function_line_range(text: str) -> tuple[int, int]:
    lines = text.splitlines()
    start = end = -1
    for idx, line in enumerate(lines, start=1):
        if re.match(r"^def\s+build_truth_metadata_template\s*\(", line):
            start = idx
            continue
        if start != -1 and end == -1 and re.match(r"^def\s+\w+\s*\(", line) and idx != start:
            end = idx - 1
            break
    if start != -1 and end == -1:
        end = len(lines)
    return start, end


def independent_alpha_scan(project_root: Path) -> dict[str, Any]:
    alpha_dir = project_root / "alpha"
    truth_terms = _load_truth_terms_independently(project_root)
    discovered = sorted(p for p in alpha_dir.rglob("*.py") if "__pycache__" not in p.parts) if alpha_dir.is_dir() else []

    scanned = 0
    unreadable: list[str] = []
    prohibited: list[dict[str, Any]] = []
    informational: list[dict[str, Any]] = []

    for path in discovered:
        rel = str(path.relative_to(project_root)).replace("\\", "/")
        try:
            data = path.read_bytes()
            text = data.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            unreadable.append(f"{rel}: {exc}")
            continue
        scanned += 1
        lines = text.splitlines()
        tmpl_start, tmpl_end = (
            _template_function_line_range(text) if rel.endswith("alpha/utils/multidomain_gate_evidence.py") else (-1, -1)
        )

        for line_no, line in enumerate(lines, start=1):
            in_template_fn = tmpl_start != -1 and tmpl_start <= line_no <= tmpl_end

            for needle in FILENAME_PATTERNS + [REFERENCE_REL_PATH, TRUTH_REL_PATH]:
                if needle in line:
                    is_open = bool(_OPEN_NEAR_RE.search(line))
                    target = prohibited if is_open else informational
                    target.append({"relative_path": rel, "line": line_no, "needle": needle, "pattern_kind": "path_or_filename"})

            for needle in TRUTH_KEY_PATTERNS + TERM_ARRAY_MARKERS:
                if needle in line:
                    informational.append({"relative_path": rel, "line": line_no, "needle": needle, "pattern_kind": "schema_or_marker"})

            if _IMPORT_RE.match(line):
                for needle in IMPORT_PATTERNS:
                    if needle in line:
                        prohibited.append({"relative_path": rel, "line": line_no, "needle": needle, "pattern_kind": "scoring_import"})

            for word in GENERIC_WORDS:
                if word in line.lower():
                    informational.append({"relative_path": rel, "line": line_no, "needle": word, "pattern_kind": "generic_word"})

            for term in truth_terms:
                if term and term in line:
                    if in_template_fn:
                        informational.append({"relative_path": rel, "line": line_no, "needle": term, "pattern_kind": "japanese_truth_term"})
                    elif _KEYTERM_ARRAY_NEARBY_RE.search(line):
                        prohibited.append({"relative_path": rel, "line": line_no, "needle": term, "pattern_kind": "japanese_truth_term"})
                    else:
                        informational.append({"relative_path": rel, "line": line_no, "needle": term, "pattern_kind": "japanese_truth_term"})

    return {
        "discovered_python_file_count": len(discovered),
        "scanned_python_file_count": scanned,
        "excluded_python_file_count": 0,
        "unreadable_python_files": unreadable,
        "prohibited_hits": prohibited,
        "informational_hits": informational,
        "scan_complete": scanned == len(discovered) and not unreadable,
    }


# ---------------------------------------------------------------------------
# 3. Fixture re-verification (reads primary per-fixture JSON, never the regression
#    summary JSON's aggregate counts)
# ---------------------------------------------------------------------------


def independent_fixture_verification(fixture_root: Path) -> dict[str, Any]:
    present_dirs = sorted(p.name for p in fixture_root.iterdir() if p.is_dir()) if fixture_root.exists() else []
    physical_fixture_count = sum(1 for n in EXPECTED_FIXTURE_DIRS if n in present_dirs)

    negative_gate_failures = 0
    exact_matches = 0
    unhandled_exceptions = 0
    negative_checked = 0
    policy_results: dict[str, Any] = {}
    parse_errors: list[str] = []

    for name in EXPECTED_FIXTURE_DIRS:
        fdir = fixture_root / name
        if not fdir.is_dir():
            continue
        try:
            expected = load_json(fdir / "expected_gate_result.json")
            actual = load_json(fdir / "actual_gate_result.json")
            assertion = load_json(fdir / "regression_assertion.json")
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{name}: {exc}")
            continue

        if name in NEGATIVE_FIXTURE_DIRS:
            negative_checked += 1
            expected_codes = sorted(expected.get("expected_failure_codes") or [])
            actual_codes = sorted(actual.get("actual_failure_codes") or [])
            gate_failed = actual.get("actual_gate_status") == "FAILED"
            if gate_failed:
                negative_gate_failures += 1
            single_code_each = len(expected_codes) == 1 and len(actual_codes) == 1
            codes_match = single_code_each and expected_codes == actual_codes
            no_unhandled = actual.get("unhandled_exception") is False and not assertion.get("unrelated_exception_detected")
            regression_passed = assertion.get("regression_test_status") == "PASSED"
            acceptance_not_accepted = actual.get("actual_acceptance_version") != "ACCEPTED"
            if not no_unhandled:
                unhandled_exceptions += 1
            if gate_failed and codes_match and no_unhandled and regression_passed and acceptance_not_accepted:
                exact_matches += 1

        elif name in POLICY_FIXTURE_DIRS:
            policy_results[name] = {
                "actual_policy_result": actual.get("policy_result"),
                "expected_policy_result": EXPECTED_POLICY_RESULT.get(name),
                "matches": actual.get("policy_result") == EXPECTED_POLICY_RESULT.get(name),
                "actual_gate_status": actual.get("actual_gate_status"),
            }

    return {
        "physical_fixture_count": physical_fixture_count,
        "negative_fixture_count_checked": negative_checked,
        "negative_gate_failures_observed": negative_gate_failures,
        "exact_failure_code_matches": exact_matches,
        "negative_unhandled_exception_count": unhandled_exceptions,
        "policy_fixture_results": policy_results,
        "policy_fixtures_all_match": all(v["matches"] for v in policy_results.values()) if policy_results else False,
        "parse_errors": parse_errors,
    }


# ---------------------------------------------------------------------------
# 4. Source immutability re-verification
# ---------------------------------------------------------------------------


def independent_source_immutability(project_root: Path, evidence_dir: Path) -> dict[str, Any]:
    snap_path = evidence_dir / "PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json"
    sidecar_path = evidence_dir / "PRE_FINAL_EVIDENCE_SOURCE_SNAPSHOT.json.sha256"
    if not snap_path.exists() or not sidecar_path.exists():
        return {"source_immutable": False, "reason": "pre-snapshot or sidecar missing"}

    actual_sha = sha256_file(snap_path)
    sidecar_sha = sidecar_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if actual_sha != sidecar_sha:
        return {"source_immutable": False, "reason": "pre-snapshot sidecar mismatch"}

    snapshot = load_json(snap_path)
    entries = snapshot.get("entries") or []
    changed: list[str] = []
    missing: list[str] = []

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
        if pre_existed:
            if entry.get("sha256") != current_sha:
                changed.append(rel)
        else:
            changed.append(rel)

    return {
        "snapshot_file_count": len(entries),
        "changed_files": changed,
        "missing_files": missing,
        "source_immutable": not changed and not missing,
    }


# ---------------------------------------------------------------------------
# 5. Sealed inner zip re-verification
# ---------------------------------------------------------------------------


def independent_inner_zip_verification(evidence_dir: Path) -> dict[str, Any]:
    zip_path = evidence_dir / "sealed" / INNER_ZIP_NAME
    seal_path = evidence_dir / "sealed" / f"{INNER_ZIP_NAME}.seal.json"
    sha_sidecar_path = evidence_dir / "sealed" / f"{INNER_ZIP_NAME}.sha256"
    size_sidecar_path = evidence_dir / "sealed" / f"{INNER_ZIP_NAME}.size.txt"
    entries_sidecar_path = evidence_dir / "sealed" / f"{INNER_ZIP_NAME}.entries.json"

    result: dict[str, Any] = {
        "inner_zip_opened": False,
        "inner_zip_corrupt_entry_count": -1,
        "inner_zip_duplicate_entry_count": -1,
        "inner_zip_actual_sha256": None,
        "inner_zip_sidecar_sha256": None,
        "inner_zip_sha256_match": False,
        "inner_zip_actual_size": None,
        "inner_zip_sidecar_size": None,
        "inner_zip_size_match": False,
        "inner_zip_actual_entry_count": None,
        "inner_zip_manifest_entry_count": None,
        "inner_zip_entries_match": False,
        "inner_zip_modified_after_seal": True,
    }

    if not zip_path.exists() or not seal_path.exists():
        return result

    seal = load_json(seal_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            raw_names = zf.namelist()
            bad = zf.testzip()
        result["inner_zip_opened"] = True
        result["inner_zip_corrupt_entry_count"] = 0 if bad is None else 1
        result["inner_zip_duplicate_entry_count"] = len(raw_names) - len(set(raw_names))
    except Exception:  # noqa: BLE001
        return result

    actual_sha = sha256_file(zip_path)
    actual_size = zip_path.stat().st_size
    result["inner_zip_actual_sha256"] = actual_sha
    result["inner_zip_actual_size"] = actual_size
    result["inner_zip_actual_entry_count"] = len(raw_names)

    if sha_sidecar_path.exists():
        sidecar_sha = sha_sidecar_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        result["inner_zip_sidecar_sha256"] = sidecar_sha
        result["inner_zip_sha256_match"] = sidecar_sha == actual_sha

    if size_sidecar_path.exists():
        sidecar_size = int(size_sidecar_path.read_text(encoding="utf-8").strip().splitlines()[0].strip())
        result["inner_zip_sidecar_size"] = sidecar_size
        result["inner_zip_size_match"] = sidecar_size == actual_size

    if entries_sidecar_path.exists():
        manifest = load_json(entries_sidecar_path)
        manifest_entries = sorted(manifest.get("entries") or [])
        result["inner_zip_manifest_entry_count"] = len(manifest_entries)
        result["inner_zip_entries_match"] = manifest_entries == sorted(raw_names)

    seal_sha = seal.get("sha256")
    sealed_at_utc = seal.get("sealed_at_utc")
    mtime = datetime.fromtimestamp(zip_path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    try:
        sealed_dt = datetime.fromisoformat(str(sealed_at_utc).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        sealed_dt = None
    modified_after_seal = True
    if sealed_dt is not None and mtime <= sealed_dt and seal_sha == actual_sha:
        modified_after_seal = False
    result["inner_zip_modified_after_seal"] = modified_after_seal

    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent verifier for multidomain final evidence seal (852622)")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--fixture-root", required=True)
    parser.add_argument("--write-json", required=True)
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    evidence_dir = Path(args.evidence_dir)
    fixture_root = Path(args.fixture_root)
    out_path = Path(args.write_json)

    missing_files: list[str] = []
    parse_errors: list[str] = []

    acceptance_contract_verified = verify_acceptance_contract(evidence_dir, missing_files, parse_errors)
    alpha_scan = independent_alpha_scan(project_root)
    fixtures = independent_fixture_verification(fixture_root)
    immutability = independent_source_immutability(project_root, evidence_dir)
    inner_zip = independent_inner_zip_verification(evidence_dir)

    reported_value_mismatches: list[str] = []
    if fixtures["negative_fixture_count_checked"] != 28:
        reported_value_mismatches.append(
            f"negative_fixture_count_checked={fixtures['negative_fixture_count_checked']} expected=28"
        )
    if fixtures["exact_failure_code_matches"] != 28:
        reported_value_mismatches.append(
            f"exact_failure_code_matches={fixtures['exact_failure_code_matches']} expected=28"
        )
    parse_errors.extend(fixtures.get("parse_errors") or [])

    verification_checks = [
        acceptance_contract_verified,
        alpha_scan["scan_complete"],
        alpha_scan["discovered_python_file_count"] == alpha_scan["scanned_python_file_count"],
        not alpha_scan["prohibited_hits"],
        fixtures["physical_fixture_count"] == 32,
        fixtures["negative_fixture_count_checked"] == 28,
        fixtures["negative_gate_failures_observed"] == 28,
        fixtures["exact_failure_code_matches"] == 28,
        fixtures["negative_unhandled_exception_count"] == 0,
        fixtures["policy_fixtures_all_match"],
        immutability.get("source_immutable") is True,
        inner_zip["inner_zip_opened"],
        inner_zip["inner_zip_corrupt_entry_count"] == 0,
        inner_zip["inner_zip_duplicate_entry_count"] == 0,
        inner_zip["inner_zip_sha256_match"],
        inner_zip["inner_zip_size_match"],
        inner_zip["inner_zip_entries_match"],
        inner_zip["inner_zip_modified_after_seal"] is False,
        not missing_files,
        not parse_errors,
    ]
    verification_passed = all(verification_checks)

    result = {
        "generated_at_utc": utc_now_iso(),
        "evidence_version": EVIDENCE_VERSION,
        "acceptance_contract_verified": acceptance_contract_verified,
        "alpha_scan_complete": alpha_scan["scan_complete"],
        "alpha_scan_exclusion_count": alpha_scan["excluded_python_file_count"],
        "prohibited_reference_leak_count": len(alpha_scan["prohibited_hits"]),
        "physical_fixture_count_recalculated": fixtures["physical_fixture_count"],
        "negative_fixture_count_recalculated": fixtures["negative_fixture_count_checked"],
        "negative_gate_failures_recalculated": fixtures["negative_gate_failures_observed"],
        "exact_failure_code_matches_recalculated": fixtures["exact_failure_code_matches"],
        "negative_unhandled_exception_count": fixtures["negative_unhandled_exception_count"],
        "policy_fixture_results_recalculated": fixtures["policy_fixture_results"],
        "source_immutability_recalculated": immutability,
        "inner_zip_opened": inner_zip["inner_zip_opened"],
        "inner_zip_corrupt_entry_count": inner_zip["inner_zip_corrupt_entry_count"],
        "inner_zip_duplicate_entry_count": inner_zip["inner_zip_duplicate_entry_count"],
        "inner_zip_actual_sha256": inner_zip["inner_zip_actual_sha256"],
        "inner_zip_sidecar_sha256": inner_zip["inner_zip_sidecar_sha256"],
        "inner_zip_sha256_match": inner_zip["inner_zip_sha256_match"],
        "inner_zip_actual_size": inner_zip["inner_zip_actual_size"],
        "inner_zip_sidecar_size": inner_zip["inner_zip_sidecar_size"],
        "inner_zip_size_match": inner_zip["inner_zip_size_match"],
        "inner_zip_actual_entry_count": inner_zip["inner_zip_actual_entry_count"],
        "inner_zip_manifest_entry_count": inner_zip["inner_zip_manifest_entry_count"],
        "inner_zip_entries_match": inner_zip["inner_zip_entries_match"],
        "inner_zip_modified_after_seal": inner_zip["inner_zip_modified_after_seal"],
        "reported_value_mismatches": reported_value_mismatches,
        "missing_files": missing_files,
        "parse_errors": parse_errors,
        "verification_passed": verification_passed,
    }
    write_json(out_path, result)

    print(f"verification_passed={str(verification_passed).lower()}")
    return 0 if verification_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
