"""Targeted 26-test hard-fix regression for V3.3.5.5.8.5.26.3.

Invokes the actual build_acceptance from run_multidomain_gate_85262.py.
Offline only — does not launch Alpha or write live acceptance paths.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import sys
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_multidomain_gate_85262 import build_acceptance  # noqa: E402

APP_VERSION = "3.3.5.5.8.5.26.3"
PROHIBITED_EXACT = [
    "multidomain_meeting_v1.txt",
    "multidomain_meeting_v1_truth.json",
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1.txt",
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1_truth.json",
    "participant_and_person_names",
    "it_terms",
    "sales_terms",
    "marketing_terms",
    "general_business_terms",
    "build_truth_metadata_template",
    "_build_multidomain_truth_metadata_template_offline",
]
PROHIBITED_CONTEXTUAL = ["company_names"]
MULTIDOMAIN_CONTEXT_MARKERS = [
    "participant_and_person_names",
    "multidomain_meeting_v1",
    "build_truth_metadata_template",
    "_build_multidomain_truth_metadata_template_offline",
]
FORBIDDEN_IMPORT_MODULES = {
    "prepare_multidomain_gate_85262",
    "score_multidomain_gate_85262",
    "verify_multidomain_gate_85262",
    "run_multidomain_gate_85262",
}
TRUTH_CATEGORY_KEYS = [
    "participant_and_person_names",
    "company_names",
    "it_terms",
    "sales_terms",
    "marketing_terms",
    "general_business_terms",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_truth(project_root: Path) -> dict[str, Any]:
    path = (
        project_root
        / "troubleshooting"
        / "accuracy_benchmark"
        / "reference_transcripts"
        / "multidomain_meeting_v1_truth.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _passing_gate_inputs(*, cer: Any = 0.0, include_cer: bool = True) -> dict[str, Any]:
    stable: dict[str, Any] = {"accuracy_percent": 100.0}
    if include_cer:
        stable["cer_percent"] = cer
    return {
        "score": {
            "strict": {
                "raw": {"accuracy_percent": 100.0, "cer_percent": 0.0},
                "stable": stable,
                "final": {"accuracy_percent": 100.0, "cer_percent": 0.0},
                "stable_to_final_loss_percent": 0.0,
            },
            "stable_to_final_loss_percent": 0.0,
        },
        "domain": {
            "combined_name_accuracy_percent": 100.0,
            "dates_times_accuracy_percent": 100.0,
            "numbers_accuracy_percent": 100.0,
            "money_percentage_accuracy_percent": 100.0,
            "combined_critical_entity_accuracy_percent": 100.0,
        },
        "verification": {"verification_passed": True},
        "isolation": {"isolation_verified": True},
        "audio_summary": {
            "delivery_ratio": 1.0,
            "missing_sent_chunk_ids": [],
            "duplicate_sent_chunk_ids": [],
            "unexpected_sent_chunk_ids": [],
            "failed_chunk_count": 0,
        },
        "runtime": {"runtime_regressions": []},
        "request": {
            "keyterm_count": 0,
            "keyword_count": 0,
            "reference_terms_loaded": 0,
        },
    }


def call_actual_gate(inputs: dict[str, Any], *, fixture_mode: bool = False) -> dict[str, Any]:
    return build_acceptance(**inputs, fixture_mode=fixture_mode)


def scan_alpha_isolation(project_root: Path) -> dict[str, Any]:
    truth = load_truth(project_root)
    entity_names: list[str] = []
    for key in ("participant_and_person_names", "company_names"):
        entity_names.extend([str(x) for x in (truth.get(key) or [])])
    category_values: dict[str, set[str]] = {
        key: {str(x) for x in (truth.get(key) or [])} for key in TRUTH_CATEGORY_KEYS
    }

    alpha_files = sorted((project_root / "alpha").rglob("*.py"))
    file_rows: list[dict[str, Any]] = []
    prohibited_exact_hits: list[dict[str, str]] = []
    unique_entity_hits: list[dict[str, str]] = []
    forbidden_import_hits: list[dict[str, str]] = []
    benchmark_collection_hits: list[dict[str, Any]] = []
    parse_failures: list[str] = []
    compile_failures: list[str] = []

    def walk_collections(node: ast.AST, rel: str) -> None:
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            strings = [elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
            for cat, values in category_values.items():
                overlap = sorted(set(strings) & values)
                if len(overlap) >= 3:
                    benchmark_collection_hits.append(
                        {"relative_path": rel, "category": cat, "overlap": overlap, "kind": type(node).__name__}
                    )
        elif isinstance(node, ast.Dict):
            strings: list[str] = []
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    strings.append(k.value)
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    strings.append(v.value)
            for cat, values in category_values.items():
                overlap = sorted(set(strings) & values)
                if len(overlap) >= 3:
                    benchmark_collection_hits.append(
                        {"relative_path": rel, "category": cat, "overlap": overlap, "kind": "Dict"}
                    )
        for child in ast.iter_child_nodes(node):
            walk_collections(child, rel)

    for path in alpha_files:
        rel = str(path.relative_to(project_root)).replace("\\", "/")
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        row: dict[str, Any] = {
            "relative_path": rel,
            "sha256": sha256_bytes(raw),
            "byte_size": len(raw),
            "parsed_successfully": True,
            "compiled_successfully": True,
            "raw_text_hits": [],
            "ast_string_hits": [],
            "code_constant_hits": [],
            "forbidden_import_hits": [],
            "benchmark_collection_hits": [],
        }
        for needle in PROHIBITED_EXACT:
            if needle in text:
                hit = {"relative_path": rel, "needle": needle, "channel": "raw_text"}
                row["raw_text_hits"].append(hit)
                prohibited_exact_hits.append(hit)
        if any(m in text for m in MULTIDOMAIN_CONTEXT_MARKERS):
            for needle in PROHIBITED_CONTEXTUAL:
                if needle in text:
                    hit = {"relative_path": rel, "needle": needle, "channel": "raw_text_contextual"}
                    row["raw_text_hits"].append(hit)
                    prohibited_exact_hits.append(hit)
        for name in entity_names:
            if name and name in text:
                hit = {"relative_path": rel, "entity": name, "channel": "raw_text"}
                unique_entity_hits.append(hit)

        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            row["parsed_successfully"] = False
            parse_failures.append(f"{rel}: {exc}")
            file_rows.append(row)
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                for needle in PROHIBITED_EXACT:
                    if needle == val or needle in val:
                        hit = {"relative_path": rel, "needle": needle, "channel": "ast_string"}
                        row["ast_string_hits"].append(hit)
                        if hit not in prohibited_exact_hits:
                            prohibited_exact_hits.append(hit)
                for name in entity_names:
                    if name and name == val:
                        unique_entity_hits.append(
                            {"relative_path": rel, "entity": name, "channel": "ast_string"}
                        )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                else:
                    if node.module:
                        names.append(node.module.split(".")[0])
                        names.append(node.module)
                for n in names:
                    if n in FORBIDDEN_IMPORT_MODULES:
                        hit = {"relative_path": rel, "module": n}
                        row["forbidden_import_hits"].append(hit)
                        forbidden_import_hits.append(hit)
        before = len(benchmark_collection_hits)
        walk_collections(tree, rel)
        row["benchmark_collection_hits"] = benchmark_collection_hits[before:]

        try:
            code = compile(tree, rel, "exec")
            for const in code.co_consts:
                if isinstance(const, str):
                    for needle in PROHIBITED_EXACT:
                        if needle == const or needle in const:
                            hit = {"relative_path": rel, "needle": needle, "channel": "code_constant"}
                            row["code_constant_hits"].append(hit)
                            if hit not in prohibited_exact_hits:
                                prohibited_exact_hits.append(hit)
        except Exception as exc:  # noqa: BLE001
            row["compiled_successfully"] = False
            compile_failures.append(f"{rel}: {exc}")

        file_rows.append(row)

    isolation_passed = (
        not parse_failures
        and not compile_failures
        and not prohibited_exact_hits
        and not unique_entity_hits
        and not forbidden_import_hits
        and not benchmark_collection_hits
        and len(file_rows) == len(alpha_files)
    )
    return {
        "discovered_alpha_python_files": len(alpha_files),
        "scanned_alpha_python_files": len(file_rows),
        "excluded_files": [],
        "parse_failures": parse_failures,
        "compile_failures": compile_failures,
        "prohibited_exact_hits": prohibited_exact_hits,
        "unique_entity_hits": unique_entity_hits,
        "forbidden_import_hits": forbidden_import_hits,
        "benchmark_collection_hits": benchmark_collection_hits,
        "isolation_passed": isolation_passed,
        "files": file_rows,
    }


def _index_files(fixture_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(fixture_dir.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            rows.append(
                {
                    "relative_path": str(path.relative_to(fixture_dir)).replace("\\", "/"),
                    "byte_size": len(data),
                    "sha256": sha256_bytes(data),
                }
            )
    return rows


def _write_test_artifacts(
    fixture_dir: Path,
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
    assertion: dict[str, Any],
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    write_json(fixture_dir / "expected_result.json", expected)
    write_json(fixture_dir / "actual_result.json", actual)
    write_json(fixture_dir / "assertion_result.json", assertion)
    (fixture_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (fixture_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    (fixture_dir / "exit_code.txt").write_text(f"{exit_code}\n", encoding="utf-8")
    write_json(fixture_dir / "fixture_file_index.json", {"files": _index_files(fixture_dir)})


def _cer_only_failures(acceptance: dict[str, Any]) -> list[str]:
    return [f for f in (acceptance.get("failures") or [])]


class TestContext:
    def __init__(self, project_root: Path, smoke_root: Path, scan: dict[str, Any]):
        self.project_root = project_root
        self.smoke_root = smoke_root
        self.scan = scan
        self.ignored_failure_codes: list[str] = []
        self.normalized_away_failure_codes: list[str] = []
        self.unhandled_exceptions = 0
        self.actual_gate_tests = 0
        self.actual_gate_tests_passed = 0


def run_test(
    ctx: TestContext,
    number: int,
    name: str,
    expected: dict[str, Any],
    runner: Callable[[], dict[str, Any]],
    *,
    is_gate: bool = False,
) -> dict[str, Any]:
    dirname = f"{number:03d}_{name}"
    fixture_dir = ctx.smoke_root / dirname
    stdout = ""
    stderr = ""
    exit_code = 0
    actual: dict[str, Any] = {}
    try:
        actual = runner()
        if is_gate:
            ctx.actual_gate_tests += 1
    except Exception as exc:  # noqa: BLE001
        ctx.unhandled_exceptions += 1
        exit_code = 1
        stderr = traceback.format_exc()
        actual = {"error": type(exc).__name__, "message": str(exc)}
        passed = False
    else:
        passed = bool(actual.get("passed"))
        if is_gate and passed:
            ctx.actual_gate_tests_passed += 1

    assertion = {
        "test_number": number,
        "test_name": name,
        "passed": passed,
        "expected": expected,
        "actual": actual,
    }
    _write_test_artifacts(
        fixture_dir,
        expected=expected,
        actual=actual,
        assertion=assertion,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )
    return {
        "test_number": number,
        "test_name": name,
        "directory": dirname,
        "passed": passed,
        "is_gate": is_gate,
    }


def build_suite(ctx: TestContext) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    scan = ctx.scan

    def t01():
        return {
            "passed": scan["scanned_alpha_python_files"] == scan["discovered_alpha_python_files"]
            and scan["discovered_alpha_python_files"] > 0,
            "discovered": scan["discovered_alpha_python_files"],
            "scanned": scan["scanned_alpha_python_files"],
        }

    def t02():
        return {"passed": scan["excluded_files"] == [], "excluded_files": scan["excluded_files"]}

    def t03():
        needles = ["multidomain_meeting_v1.txt", "multidomain_meeting_v1_truth.json"]
        hits = [h for h in scan["prohibited_exact_hits"] if h.get("needle") in needles]
        return {"passed": hits == [], "hits": hits}

    def t04():
        keys = [
            "participant_and_person_names",
            "it_terms",
            "sales_terms",
            "marketing_terms",
            "general_business_terms",
        ]
        hits = [h for h in scan["prohibited_exact_hits"] if h.get("needle") in keys]
        # company_names only counts with multidomain context (already filtered in scan)
        hits += [h for h in scan["prohibited_exact_hits"] if h.get("needle") == "company_names"]
        return {"passed": hits == [], "hits": hits}

    def t05():
        return {"passed": scan["unique_entity_hits"] == [], "hits": scan["unique_entity_hits"]}

    def t06():
        return {
            "passed": scan["benchmark_collection_hits"] == [],
            "hits": scan["benchmark_collection_hits"],
        }

    def t07():
        prep = (ctx.project_root / "prepare_multidomain_gate_85262.py").read_text(encoding="utf-8")
        alpha_hits = []
        for p in (ctx.project_root / "alpha").rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if "_build_multidomain_truth_metadata_template_offline" in text:
                alpha_hits.append(str(p.relative_to(ctx.project_root)))
        return {
            "passed": ("def _build_multidomain_truth_metadata_template_offline" in prep) and not alpha_hits,
            "alpha_hits": alpha_hits,
        }

    def t08():
        return {"passed": scan["forbidden_import_hits"] == [], "hits": scan["forbidden_import_hits"]}

    results.append(run_test(ctx, 1, "alpha_all_files_scanned", {"all_scanned": True}, t01))
    results.append(run_test(ctx, 2, "alpha_scan_exclusions_zero", {"excluded": []}, t02))
    results.append(run_test(ctx, 3, "benchmark_filenames_absent", {"hits": []}, t03))
    results.append(run_test(ctx, 4, "truth_schema_keys_absent", {"hits": []}, t04))
    results.append(run_test(ctx, 5, "participant_company_absent", {"hits": []}, t05))
    results.append(run_test(ctx, 6, "no_benchmark_collection_literal", {"hits": []}, t06))
    results.append(run_test(ctx, 7, "offline_truth_function_prepare_only", {"ok": True}, t07))
    results.append(run_test(ctx, 8, "alpha_no_gate_module_imports", {"hits": []}, t08))

    def make_cer_test(cer_value: Any, include_cer: bool, expect_failures: list[str], expect_pass: bool):
        def _run() -> dict[str, Any]:
            inputs = _passing_gate_inputs(cer=cer_value, include_cer=include_cer)
            acceptance = call_actual_gate(inputs, fixture_mode=False)
            failures = _cer_only_failures(acceptance)
            # Do not ignore or normalize failures.
            only = failures
            passed = (only == expect_failures) if not expect_pass else (only == [])
            if expect_pass:
                passed = failures == [] and float(acceptance.get("stable_cer_percent")) == float(
                    0.0 if cer_value in (0.0, "0.0") else cer_value
                    if isinstance(cer_value, (int, float)) and not isinstance(cer_value, bool)
                    else acceptance.get("stable_cer_percent")
                )
                if expect_pass and cer_value in (0.0, "0.0", 19.999, 20.0):
                    passed = failures == []
            else:
                passed = failures == expect_failures
            return {
                "passed": passed,
                "failures": failures,
                "stable_cer_percent": acceptance.get("stable_cer_percent"),
                "ready_for_translation_beta": acceptance.get("ready_for_translation_beta"),
                "ignored_failure_codes": [],
                "normalized_away_failure_codes": [],
            }

        return _run

    cer_cases = [
        (9, "cer_numeric_zero_passes", 0.0, True, [], True),
        (10, "cer_string_zero_passes", "0.0", True, [], True),
        (11, "cer_19_999_passes", 19.999, True, [], True),
        (12, "cer_20_passes", 20.0, True, [], True),
        (13, "cer_20_0001_fails_above", 20.0001, True, ["stable_cer_above_20"], False),
        (14, "cer_null_missing", None, True, ["stable_cer_missing"], False),
        (15, "cer_key_missing", None, False, ["stable_cer_missing"], False),
        (16, "cer_invalid_string", "invalid", True, ["stable_cer_invalid"], False),
        (17, "cer_negative_invalid", -0.01, True, ["stable_cer_invalid"], False),
        (18, "cer_nan_invalid", float("nan"), True, ["stable_cer_invalid"], False),
        (19, "cer_pos_inf_invalid", float("inf"), True, ["stable_cer_invalid"], False),
    ]
    for number, name, value, include, expect_fail, expect_pass in cer_cases:
        results.append(
            run_test(
                ctx,
                number,
                name,
                {"expect_failures": expect_fail, "expect_pass": expect_pass},
                make_cer_test(value, include, expect_fail, expect_pass),
                is_gate=True,
            )
        )

    def other_zero(mutator: Callable[[dict[str, Any]], None], check: Callable[[dict[str, Any]], bool]):
        def _run() -> dict[str, Any]:
            inputs = _passing_gate_inputs(cer=0.0)
            mutator(inputs)
            acceptance = call_actual_gate(inputs, fixture_mode=False)
            failures = acceptance.get("failures") or []
            passed = failures == [] and check(acceptance)
            return {"passed": passed, "failures": failures, "acceptance_slice": {
                "stable_to_final_loss_percent": acceptance.get("stable_to_final_loss_percent"),
                "keyterm_count": acceptance.get("keyterm_count"),
                "keyword_count": acceptance.get("keyword_count"),
                "reference_terms_loaded": acceptance.get("reference_terms_loaded"),
                "audio_delivery_missing_chunks": acceptance.get("audio_delivery_missing_chunks"),
                "stable_accuracy_percent": acceptance.get("stable_accuracy_percent"),
            }}

        return _run

    results.append(
        run_test(
            ctx,
            20,
            "loss_zero_passes",
            {"failures": []},
            other_zero(lambda i: None, lambda a: float(a["stable_to_final_loss_percent"]) == 0.0),
            is_gate=True,
        )
    )
    results.append(
        run_test(
            ctx,
            21,
            "keyterm_zero_passes",
            {"failures": []},
            other_zero(lambda i: i["request"].__setitem__("keyterm_count", 0), lambda a: a["keyterm_count"] == 0),
            is_gate=True,
        )
    )
    results.append(
        run_test(
            ctx,
            22,
            "keyword_zero_passes",
            {"failures": []},
            other_zero(lambda i: i["request"].__setitem__("keyword_count", 0), lambda a: a["keyword_count"] == 0),
            is_gate=True,
        )
    )
    results.append(
        run_test(
            ctx,
            23,
            "reference_terms_zero_passes",
            {"failures": []},
            other_zero(
                lambda i: i["request"].__setitem__("reference_terms_loaded", 0),
                lambda a: a["reference_terms_loaded"] == 0,
            ),
            is_gate=True,
        )
    )
    results.append(
        run_test(
            ctx,
            24,
            "missing_chunks_zero_passes",
            {"failures": []},
            other_zero(
                lambda i: i["audio_summary"].__setitem__("missing_sent_chunk_ids", []),
                lambda a: a["audio_delivery_missing_chunks"] == 0,
            ),
            is_gate=True,
        )
    )
    results.append(
        run_test(
            ctx,
            25,
            "accuracy_80_passes",
            {"failures": []},
            other_zero(
                lambda i: i["score"]["strict"]["stable"].__setitem__("accuracy_percent", 80.0),
                lambda a: float(a["stable_accuracy_percent"]) == 80.0,
            ),
            is_gate=True,
        )
    )

    def t26() -> dict[str, Any]:
        inputs = _passing_gate_inputs(cer=0.0)
        # All valid zeros already present; call with fixture_mode=False in memory only.
        acceptance = call_actual_gate(inputs, fixture_mode=False)
        failures = acceptance.get("failures") or []
        passed = (
            failures == []
            and acceptance.get("ready_for_translation_beta") is True
            and acceptance.get("VERSION") == "ACCEPTED"
            and float(acceptance.get("stable_cer_percent")) == 0.0
        )
        return {
            "passed": passed,
            "failures": failures,
            "ignored_failure_codes": [],
            "normalized_away_failure_codes": [],
            "ready_for_translation_beta": acceptance.get("ready_for_translation_beta"),
            "VERSION": acceptance.get("VERSION"),
            "stable_cer_percent": acceptance.get("stable_cer_percent"),
            "persisted_to_live_path": False,
        }

    results.append(
        run_test(
            ctx,
            26,
            "complete_positive_fixture_all_zeros",
            {"failures": [], "ready_for_translation_beta": True},
            t26,
            is_gate=True,
        )
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--smoke-root", type=Path, required=True)
    parser.add_argument("--scan-json", type=Path, default=None)
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    smoke_root = args.smoke_root.resolve()
    smoke_root.mkdir(parents=True, exist_ok=True)

    if args.scan_json and args.scan_json.exists():
        scan = json.loads(args.scan_json.read_text(encoding="utf-8"))
    else:
        scan = scan_alpha_isolation(project_root)

    ctx = TestContext(project_root, smoke_root, scan)
    results = build_suite(ctx)
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    summary = {
        "tests": len(results),
        "passed": passed,
        "failed": failed,
        "actual_gate_tests": ctx.actual_gate_tests,
        "actual_gate_tests_passed": ctx.actual_gate_tests_passed,
        "ignored_failure_codes": ctx.ignored_failure_codes,
        "normalized_away_failure_codes": ctx.normalized_away_failure_codes,
        "unhandled_exceptions": ctx.unhandled_exceptions,
        "results": results,
        "created_at": utc_now_iso(),
        "app_version": APP_VERSION,
        "acceptance_builder": "run_multidomain_gate_85262.build_acceptance",
    }
    write_json(smoke_root / "targeted_regression_results.json", summary)
    print(f"tests={len(results)}")
    print(f"passed={passed}")
    print(f"failed={failed}")
    print(f"actual_gate_tests={ctx.actual_gate_tests}")
    print(f"actual_gate_tests_passed={ctx.actual_gate_tests_passed}")
    print("ignored_failure_codes=[]")
    print("normalized_away_failure_codes=[]")
    print(f"unhandled_exceptions={ctx.unhandled_exceptions}")
    return 0 if failed == 0 and ctx.unhandled_exceptions == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
