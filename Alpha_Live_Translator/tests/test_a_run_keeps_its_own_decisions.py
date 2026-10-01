"""A run's boundary-stabilizer decisions are that run's, not every run's.

WHAT WAS BROKEN (item 68, found reading the live meeting of 2026-10-01)
-----------------------------------------------------------------------
`_decision_log_path` and `_summary_path` imported `get_run_folder` from
`alpha.utils.troubleshooting_paths`, which has no such function (it is in
`alpha.utils.run_identity`). The ImportError was swallowed, so every run
appended its decisions to one shared `troubleshooting/runs/_pending/...` file,
and at Stop `finalize_boundary_decisions` copied that whole file into the run
folder. In the main checkout it held 23 runs since 2026-08 (5,700 lines); the
2026-10-01 run's file had 155 lines of its own, and the first, silent session
of that day got the 2026-09-30 replay's decisions.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import japanese_boundary_stabilizer as jbs  # noqa: E402
from alpha.utils import boundary_evidence_finalize as bef  # noqa: E402
from alpha.utils import run_identity  # noqa: E402

PENDING = Path("troubleshooting/runs/_pending/accuracy/boundary_stabilizer_decisions.jsonl")
OTHER_RUNS = '{"run_id": "live-...-20260930-131614"}\n'


class _InATempProject(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="item68-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        self.run_folder = self.tmp / "troubleshooting" / "runs" / "v-20261001-140533"
        (self.run_folder / "accuracy").mkdir(parents=True)
        identity = run_identity.RunIdentity(
            run_id="live-v-20261001-140533",
            run_timestamp="20261001-140533",
            run_type="live",
            app_version="test",
            app_codename="test",
            selected_language="ja",
            run_folder=str(self.run_folder),
        )
        p = patch.object(run_identity, "_current", identity)
        p.start()
        self.addCleanup(p.stop)
        PENDING.parent.mkdir(parents=True, exist_ok=True)
        PENDING.write_text(OTHER_RUNS, encoding="utf-8")


class TheDecisionLogIsInTheRunFolderTest(_InATempProject):
    def test_paths(self):
        self.assertEqual(jbs._decision_log_path(), self.run_folder / "accuracy" / "boundary_stabilizer_decisions.jsonl")
        self.assertEqual(jbs._summary_path(), self.run_folder / "accuracy" / "boundary_stabilizer_summary.json")


class StopDoesNotCopyOtherRunsInTest(_InATempProject):
    def test_a_run_with_its_own_decisions_keeps_them(self):
        own = self.run_folder / "accuracy" / "boundary_stabilizer_decisions.jsonl"
        own.write_text('{"run_id": "live-v-20261001-140533"}\n', encoding="utf-8")
        bef.finalize_boundary_decisions(self.run_folder)
        self.assertEqual(own.read_text(encoding="utf-8"), '{"run_id": "live-v-20261001-140533"}\n')

    def test_a_run_without_decisions_gets_an_empty_file(self):
        bef.finalize_boundary_decisions(self.run_folder)
        own = self.run_folder / "accuracy" / "boundary_stabilizer_decisions.jsonl"
        self.assertEqual(own.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
