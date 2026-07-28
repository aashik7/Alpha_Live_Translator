#!/usr/bin/env python3
"""Validate UI event-loop responsiveness evidence from startup profiling."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def _collect_delays(evidence_dir: Path) -> list[float]:
    delays: list[float] = []
    paths = sorted(evidence_dir.glob("launch_repaired_*/UI_EVENT_LOOP_RESPONSIVENESS.json"))
    direct = evidence_dir / "UI_EVENT_LOOP_RESPONSIVENESS.json"
    if direct.exists():
        paths.append(direct)
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for d in data.get("delays_ms") or []:
            try:
                delays.append(float(d))
            except Exception:
                pass
    return delays


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args()
    root = Path(args.evidence_dir)
    delays = _collect_delays(root)
    result = {"UI_RESPONSIVENESS_VALIDATION": "FAILED", "missing_evidence": not delays}
    if not delays:
        (root / "UI_RESPONSIVENESS_VALIDATION.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        return 1
    p50 = _pct(delays, 50)
    p95 = _pct(delays, 95)
    max_d = max(delays) if delays else None
    above_200 = sum(1 for d in delays if d > 200)
    above_500 = sum(1 for d in delays if d > 500)
    # Hard gate: post-paint heartbeat must not show any >500ms delay.
    hard_ok = above_500 == 0 and len(delays) >= 3
    preferred_ok = p95 is not None and p95 < 100
    passed = hard_ok
    payload = {
        "UI_RESPONSIVENESS_VALIDATION": "PASSED" if passed else "FAILED",
        "p50_event_loop_delay_ms": p50,
        "p95_event_loop_delay_ms": p95,
        "max_event_loop_delay_ms": max_d,
        "delays_above_200_ms": above_200,
        "delays_above_500_ms": above_500,
        "preferred_p95_below_100ms": preferred_ok,
        "sample_count": len(delays),
        "raw_sample_count": len(delays),
        "median_ms": float(statistics.median(delays)) if delays else None,
    }
    (root / "UI_EVENT_LOOP_RESPONSIVENESS.json").write_text(
        json.dumps(
            {
                "sample_count": len(delays),
                "p50_event_loop_delay_ms": p50,
                "p95_event_loop_delay_ms": p95,
                "max_event_loop_delay_ms": max_d,
                "delays_above_200_ms": above_200,
                "delays_above_500_ms": above_500,
                "longest_blocked_interval_ms": max_d,
                "delays_ms": delays[-200:],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "UI_RESPONSIVENESS_VALIDATION.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
