#!/usr/bin/env python3
"""Validate startup performance against STARTUP_COMPARISON.json evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args()
    root = Path(args.evidence_dir)
    comparison_path = root / "STARTUP_COMPARISON.json"
    baseline_path = root / "STARTUP_BASELINE.json"
    repaired_path = root / "STARTUP_REPAIRED.json"
    missing = [p.name for p in (comparison_path, baseline_path, repaired_path) if not p.exists()]
    result = {
        "STARTUP_PERFORMANCE_VALIDATION": "FAILED",
        "missing_evidence": missing,
    }
    if missing:
        (root / "STARTUP_PERFORMANCE_VALIDATION.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        return 1
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    paint_imp = comparison.get("first_paint_improvement_pct")
    inter_imp = comparison.get("interactive_improvement_pct")
    required = float(comparison.get("required_min_improvement_pct") or 40.0)
    paint_ok = paint_imp is not None and (
        paint_imp >= required
        or (comparison.get("repaired_median_first_paint_ms") or 99999) <= float(
            comparison.get("or_first_paint_below_ms") or 2000
        )
    )
    inter_ok = inter_imp is not None and (
        inter_imp >= required
        or (comparison.get("repaired_median_interactive_ms") or 99999) <= float(
            comparison.get("or_interactive_below_ms") or 5000
        )
    )
    # Soft preferred targets
    preferred = {
        "first_window_within_2s": (comparison.get("repaired_median_first_paint_ms") or 99999) <= 2000,
        "interactive_within_5s": (comparison.get("repaired_median_interactive_ms") or 99999) <= 5000,
        "splash_excluded": bool(comparison.get("splash_excluded", True)),
    }
    passed = paint_ok and inter_ok and preferred["splash_excluded"]
    result = {
        "STARTUP_PERFORMANCE_VALIDATION": "PASSED" if passed else "FAILED",
        "first_paint_improvement_pct": paint_imp,
        "interactive_improvement_pct": inter_imp,
        "required_min_improvement_pct": required,
        "paint_gate_passed": paint_ok,
        "interactive_gate_passed": inter_ok,
        "preferred_targets": preferred,
        "measurement": comparison.get("measurement") or "real_alpha_window_only",
        "baseline_median_first_paint_ms": comparison.get("baseline_median_first_paint_ms"),
        "repaired_median_first_paint_ms": comparison.get("repaired_median_first_paint_ms"),
        "baseline_median_interactive_ms": comparison.get("baseline_median_interactive_ms"),
        "repaired_median_interactive_ms": comparison.get("repaired_median_interactive_ms"),
    }
    (root / "STARTUP_PERFORMANCE_VALIDATION.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
