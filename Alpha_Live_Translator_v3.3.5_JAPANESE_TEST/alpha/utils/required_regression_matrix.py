"""Mandatory regression matrix for zero-issue acceptance (V25.3.3.2.2)."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

REQUIRED_REGRESSION_SCRIPTS = (
    "regression_eleven_issue_closure_852533.py",
    "regression_final_writer_stop_tail_8525331.py",
    "regression_persisted_evidence_package_closure_8525332.py",
    "regression_canonical_acceptance_bundle_85253321.py",
    "regression_zero_issue_validation_85253322.py",
    "runtime_smoke_eleven_issue_closure_852533.py",
)


@dataclass
class SuiteResult:
    name: str
    path: str
    found: bool
    exit_code: Optional[int]
    tests_total: Optional[int]
    tests_passed: Optional[int]
    tests_failed: Optional[int]
    status: str
    output_path: str
    output_sha256: Optional[str]


def _parse_counts(text: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    total = passed = failed = None
    # JSON smoke payload with checks dict
    try:
        data = json.loads(text.strip().split("\n\n")[0] if "\n\n" in text else text)
        if isinstance(data, dict) and isinstance(data.get("checks"), dict):
            checks = data["checks"]
            total = len(checks)
            passed = sum(1 for v in checks.values() if v)
            failed = total - passed
            return total, passed, failed
    except Exception:
        pass
    # Partial JSON from printed payload
    m_checks = re.search(r'"checks"\s*:\s*\{([^}]*)\}', text, re.S)
    if m_checks and '"result"' in text:
        # Count true/false in checks via crude parse of full JSON blobs
        try:
            # Find outermost JSON object
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(text[start : end + 1])
                if isinstance(data.get("checks"), dict):
                    checks = data["checks"]
                    total = len(checks)
                    passed = sum(1 for v in checks.values() if v)
                    failed = total - passed
                    return total, passed, failed
        except Exception:
            pass

    m = re.search(r"(?im)^tests\s*=\s*(\d+)", text)
    if m:
        total = int(m.group(1))
    m = re.search(r"(?im)(?:passed|tests_passed)\s*[:=]\s*(\d+)", text)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(?im)(?:failed|tests_failed)\s*[:=]\s*(\d+)", text)
    if m:
        failed = int(m.group(1))
    if passed is None:
        m = re.search(r"(?im)passed:\s*(\d+)", text)
        if m:
            passed = int(m.group(1))
    if failed is None:
        m = re.search(r"(?im)failed:\s*(\d+)", text)
        if m:
            failed = int(m.group(1))
    if total is None and passed is not None and failed is not None:
        total = passed + failed
    return total, passed, failed


def _status_from(exit_code: Optional[int], failed: Optional[int], text: str) -> str:
    if exit_code is None:
        return "MISSING"
    if exit_code != 0:
        return "FAILED"
    if failed is not None and failed != 0:
        return "FAILED"
    if re.search(r"(?im)\bFAILED\b", text) and not re.search(r"(?im)STATUS\s*=\s*PASSED|RESULT=PASSED|\bPASSED\b\s*$", text):
        # Prefer explicit PASSED markers
        if "STATUS=PASSED" in text or "RESULT=PASSED" in text or text.strip().endswith("PASSED"):
            return "PASSED"
        if failed == 0:
            return "PASSED"
        return "FAILED"
    if exit_code == 0 and (failed is None or failed == 0):
        return "PASSED"
    return "FAILED"


def run_required_regressions(
    project_root: Path | str,
    *,
    output_dir: Path | str,
    python_exe: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = python_exe or sys.executable
    suites: list[SuiteResult] = []

    for name in REQUIRED_REGRESSION_SCRIPTS:
        script = root / name
        out_path = out_dir / (Path(name).stem + ".txt")
        if not script.is_file():
            suites.append(
                SuiteResult(
                    name=name,
                    path=str(script),
                    found=False,
                    exit_code=None,
                    tests_total=None,
                    tests_passed=None,
                    tests_failed=None,
                    status="MISSING",
                    output_path=str(out_path),
                    output_sha256=None,
                )
            )
            continue
        proc = subprocess.run(
            [exe, str(script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        out_path.write_text(combined, encoding="utf-8")
        total, passed, failed = _parse_counts(combined)
        sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
        suites.append(
            SuiteResult(
                name=name,
                path=str(script),
                found=True,
                exit_code=proc.returncode,
                tests_total=total,
                tests_passed=passed,
                tests_failed=failed,
                status=_status_from(proc.returncode, failed, combined),
                output_path=str(out_path),
                output_sha256=sha,
            )
        )

    suite_dicts = [asdict(s) for s in suites]
    suites_failed = sum(1 for s in suites if s.status != "PASSED")
    suites_passed = sum(1 for s in suites if s.status == "PASSED")
    tests_total = sum(int(s.tests_total or 0) for s in suites)
    tests_passed = sum(int(s.tests_passed or 0) for s in suites)
    tests_failed = sum(int(s.tests_failed or 0) for s in suites)
    # Unparsable results fail acceptance
    unparsable = [
        s.name
        for s in suites
        if s.found
        and (
            s.tests_total is None
            or s.tests_passed is None
            or s.tests_failed is None
            or s.exit_code is None
        )
    ]
    if unparsable:
        suites_failed = max(suites_failed, len(unparsable))

    return {
        "required_regression_suites": suite_dicts,
        "regression_suites_passed": suites_passed,
        "regression_suites_failed": suites_failed,
        "regression_tests_total": tests_total,
        "regression_tests_passed": tests_passed,
        "regression_tests_failed": tests_failed,
        "unparsable_suites": unparsable,
        "acceptance_regression_gate_passed": (
            suites_failed == 0
            and tests_failed == 0
            and not unparsable
            and len(suites) == len(REQUIRED_REGRESSION_SCRIPTS)
            and all(s.found for s in suites)
            and all(s.exit_code == 0 for s in suites)
            and all(s.status == "PASSED" for s in suites)
        ),
    }
