"""Regression test for BUG_FIX_ROADMAP.md Batch 2, item 8.

Confirmed defect: UtteranceLifecycleOwner._observe_identity fails OPEN
-- on any exception from the canonical identity registry, it silently
returns (True, "unavailable", {}), i.e. "accepted", in a file whose
whole design is fail-closed. This is the one gate meant to prevent
duplicate/cross-utterance mutation, bypassed with zero trace.

This batch item is logging only -- the fail-open behavior itself is
deliberately NOT changed here (that's item 27, gated on evidence of how
often this actually fires). This test asserts both: the failure is now
logged, AND the return value is unchanged (still fails open).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    UtteranceLifecycleOwner,
)


class TestObserveIdentityLogsFailOpen(unittest.TestCase):
    def _call(self):
        owner = UtteranceLifecycleOwner()
        owner.reset_for_session("sess-1")
        with patch(
            "alpha.transcription.canonical_identity_registry.observe_identity",
            side_effect=RuntimeError("boom"),
        ), patch("alpha.utils.japanese_accuracy_log.jp_accuracy_log") as mock_log:
            result = owner._observe_identity(
                utterance_id="U-1",
                channel=0,
                version=1,
                decision="COMMIT",
                text="hello",
                lifecycle_state="COMMITTED",
                translation_eligible=True,
                metadata={},
            )
        return result, mock_log

    def test_failure_is_logged(self):
        _, mock_log = self._call()
        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("OBSERVE_IDENTITY_FAILED_OPEN", events)

    def test_fail_open_behavior_is_unchanged(self):
        result, _ = self._call()
        accepted, reason, entry = result
        self.assertTrue(accepted, "must still fail open (accepted=True), not fail closed")
        self.assertEqual(reason, "unavailable")
        self.assertEqual(entry, {})

    def test_success_logs_nothing(self):
        owner = UtteranceLifecycleOwner()
        owner.reset_for_session("sess-2")

        class _Result:
            accepted = True
            reason = "ok"
            entry = {}

        with patch(
            "alpha.transcription.canonical_identity_registry.observe_identity",
            return_value=_Result(),
        ), patch("alpha.utils.japanese_accuracy_log.jp_accuracy_log") as mock_log:
            owner._observe_identity(
                utterance_id="U-2",
                channel=0,
                version=1,
                decision="COMMIT",
                text="hello",
                lifecycle_state="COMMITTED",
                translation_eligible=True,
                metadata={},
            )

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertNotIn("OBSERVE_IDENTITY_FAILED_OPEN", events)


if __name__ == "__main__":
    unittest.main()
