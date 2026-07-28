#!/usr/bin/env python3
"""Validate translation repair package report consistency."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(package_dir: str | None = None) -> int:
    if package_dir:
        pkg = Path(package_dir)
    else:
        cands = sorted(
            ROOT.glob("troubleshooting/translation_beta_repair*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        cands = [p for p in cands if p.is_dir()]
        if not cands:
            print("TRANSLATION_REPORT_CONSISTENCY = FAILED")
            print("no package dir")
            return 1
        pkg = cands[0]

    failures = []
    required = [
        "TRANSLATION_BETA_REPAIR_VALIDATION.json",
        "TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION.json",
        "TRANSLATION_TIMEOUT_HANDLING_VALIDATION.json",
        "TRANSLATION_SMOKE_TEST.json",
        "TRANSLATION_ORDER_VALIDATION.json",
        "TRANSLATION_REPORT_CONSISTENCY.json",
        "TRANSLATION_BETA_REPAIR_DECISION_REPORT.txt",
        "Cursor final report.txt",
        "implementation_manifest.json",
    ]
    for name in required:
        if not (pkg / name).exists():
            failures.append(f"missing:{name}")

    def load(name: str):
        return json.loads((pkg / name).read_text(encoding="utf-8"))

    if not failures:
        main_v = load("TRANSLATION_BETA_REPAIR_VALIDATION.json")
        graceful = load("TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION.json")
        timeout = load("TRANSLATION_TIMEOUT_HANDLING_VALIDATION.json")
        smoke = load("TRANSLATION_SMOKE_TEST.json")
        manifest = load("implementation_manifest.json")
        decision = (pkg / "TRANSLATION_BETA_REPAIR_DECISION_REPORT.txt").read_text(
            encoding="utf-8"
        )
        cursor = (pkg / "Cursor final report.txt").read_text(encoding="utf-8")

        overall = main_v.get("OVERALL_ACCEPTANCE")
        if decision.strip() != cursor.strip():
            failures.append("cursor_final_report_ne_decision_report")
        if f"OVERALL_ACCEPTANCE={overall}" not in decision:
            failures.append("decision_overall_mismatch")
        if bool(manifest.get("acceptance")) != (overall == "PASSED"):
            failures.append("manifest_acceptance_mismatch")
        if graceful.get("TRANSLATION_GRACEFUL_SHUTDOWN_VALIDATION") == "PASSED":
            gsum = graceful.get("summary") or {}
            if int(gsum.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", -1)) != 0:
                failures.append("graceful_pass_pending_nonzero")
            if gsum.get("UNFINISHED_TRANSLATION_SEGMENT_IDS"):
                failures.append("graceful_pass_unfinished")
        if timeout.get("TRANSLATION_TIMEOUT_HANDLING_VALIDATION") == "PASSED":
            tsum = timeout.get("summary") or {}
            if int(tsum.get("TRANSLATION_QUEUE_PENDING_AT_EXIT", 0)) == 0:
                failures.append("timeout_pass_claimed_zero_pending")
        if "readiness-preflight" in decision.lower() and "V26.5 readiness" in decision:
            failures.append("stale_readiness_report")
        if overall == "PASSED" and smoke.get("TRANSLATION_SMOKE_TEST") != "PASSED":
            failures.append("overall_pass_smoke_fail")

    payload = {
        "package": str(pkg),
        "TRANSLATION_REPORT_CONSISTENCY": "PASSED" if not failures else "FAILED",
        "failures": failures,
    }
    out = pkg / "TRANSLATION_REPORT_CONSISTENCY.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("TRANSLATION_REPORT_CONSISTENCY =", payload["TRANSLATION_REPORT_CONSISTENCY"])
    print("package=", pkg)
    return 0 if not failures else 1


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(main(arg))
