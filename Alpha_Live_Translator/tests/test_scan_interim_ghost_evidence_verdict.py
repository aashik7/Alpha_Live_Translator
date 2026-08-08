"""Regression test for BUG_FIX_ROADMAP.md Batch 1, item 3.

Confirmed defect: tools/scan_interim_ghost_evidence.py::build_verdict()
returned PASS whenever `anomalies` was empty, regardless of how many
decisions needed the watchdog to clean up after them. A real run with 10
watchdog firings out of 12 decisions (83%) reported PASS -- but the
watchdog is meant to be a rare backstop for genuine orphans; a ratio
that high means the identity gate (Layer 1) is barely functioning.

This test imports the tool by file path (it lives under tools/, not a
package importable via alpha.*) and calls build_verdict() directly with
constructed result dicts matching scan()'s real return shape, at ratios
on both sides of WATCHDOG_FIRING_RATIO_REVIEW_THRESHOLD.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = PROJECT_ROOT / "tools" / "scan_interim_ghost_evidence.py"

_spec = importlib.util.spec_from_file_location(
    "scan_interim_ghost_evidence", TOOL_PATH
)
scan_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_tool)


def _result(total, watchdog_n, anomalies_n=0, action_counts=None):
    """Build a result dict matching scan()'s real return shape."""
    return {
        "run_dir": Path("fake_run"),
        "manifest": {},
        "action_counts": action_counts or {},
        "comparisons": [],
        "watchdog_fires": [{"ts": i} for i in range(watchdog_n)],
        "anomalies": [{"ts": i} for i in range(anomalies_n)],
        "session_start_ts": 0,
        "session_end_ts": 1000,
        "total_comparisons": total,
    }


class TestWatchdogRatioVerdict(unittest.TestCase):
    def test_high_watchdog_ratio_is_review_not_pass(self):
        # The real regression case: 10 of 12 decisions (83%) needed the
        # watchdog. This used to report PASS.
        result = _result(total=12, watchdog_n=10)
        level, headline, _ = scan_tool.build_verdict(result)
        self.assertEqual(level, "warn")
        self.assertIn("83%", headline)

    def test_low_watchdog_ratio_is_still_pass(self):
        # A single watchdog firing in a long, otherwise-clean session is
        # the watchdog doing exactly its designed job -- must stay PASS.
        result = _result(total=50, watchdog_n=1)
        level, _, _ = scan_tool.build_verdict(result)
        self.assertEqual(level, "pass")

    def test_ratio_exactly_at_threshold_is_review(self):
        threshold = scan_tool.WATCHDOG_FIRING_RATIO_REVIEW_THRESHOLD
        total = 100
        watchdog_n = int(total * threshold)
        result = _result(total=total, watchdog_n=watchdog_n)
        level, _, _ = scan_tool.build_verdict(result)
        self.assertEqual(level, "warn")

    def test_zero_watchdog_firings_is_pass(self):
        result = _result(total=10, watchdog_n=0)
        level, _, _ = scan_tool.build_verdict(result)
        self.assertEqual(level, "pass")

    def test_anomalies_still_take_priority_over_ratio(self):
        # An explicit anomaly (keep_interim decided past-TTL with no
        # watchdog rescue) is worse than a high watchdog ratio and must
        # still win the verdict.
        result = _result(total=12, watchdog_n=10, anomalies_n=1)
        level, headline, _ = scan_tool.build_verdict(result)
        self.assertEqual(level, "warn")
        self.assertIn("stale", headline.lower())


if __name__ == "__main__":
    unittest.main()
