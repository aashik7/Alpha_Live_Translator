"""Regression test for BUG_FIX_ROADMAP.md Batch 2, item 6.

Confirmed defect: two swallowed-exception paths in
utterance_lifecycle.py logged nothing on failure:

1. UtteranceLifecycleOwner.reset_for_session -- if resetting the
   canonical identity registry raised, a new session could silently
   inherit stale identity entries from the previous one.
2. UtteranceLifecycleOwner._resolve_correction_target_locked -- on any
   exception, the function falls through to returning the raw,
   UNVERIFIED target_record_id/target_utterance_id instead of the
   registry-resolved exact match, with no trace that verification was
   skipped.

Fix adds logging only; both fallback behaviors are otherwise unchanged
(verified by asserting the return values below).
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


class TestResetForSessionLogsRegistryFailure(unittest.TestCase):
    def test_registry_reset_failure_is_logged(self):
        owner = UtteranceLifecycleOwner()
        with patch(
            "alpha.transcription.canonical_identity_registry.reset_for_session",
            side_effect=RuntimeError("boom"),
        ), patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log"
        ) as mock_log:
            owner.reset_for_session("sess-1")

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("IDENTITY_REGISTRY_RESET_FAILED", events)
        # Behavior unchanged: the session id is still set locally even
        # though the registry-side reset failed.
        self.assertEqual(owner.session_id, "sess-1")

    def test_registry_reset_success_logs_nothing(self):
        owner = UtteranceLifecycleOwner()
        with patch(
            "alpha.transcription.canonical_identity_registry.reset_for_session"
        ), patch("alpha.utils.japanese_accuracy_log.jp_accuracy_log") as mock_log:
            owner.reset_for_session("sess-2")

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertNotIn("IDENTITY_REGISTRY_RESET_FAILED", events)


class TestResolveCorrectionTargetLogsFailure(unittest.TestCase):
    def test_resolution_failure_is_logged_and_falls_back_to_raw_values(self):
        owner = UtteranceLifecycleOwner()
        owner.reset_for_session("sess-3")
        with patch(
            "alpha.transcription.canonical_identity_registry.resolve_canonical_record_id",
            side_effect=RuntimeError("boom"),
        ), patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log"
        ) as mock_log:
            with owner._lock:
                record_id, utterance_id = owner._resolve_correction_target_locked(
                    channel=0,
                    metadata={
                        "canonical_utterance_id": "U-9",
                        "revision_target_id": "REC-9",
                    },
                )

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("CORRECTION_TARGET_RESOLUTION_FAILED", events)
        # Behavior unchanged: falls back to the raw, unverified values.
        self.assertEqual((record_id, utterance_id), ("REC-9", "U-9"))

    def test_resolution_success_logs_nothing(self):
        owner = UtteranceLifecycleOwner()
        owner.reset_for_session("sess-4")
        with patch(
            "alpha.transcription.canonical_identity_registry.resolve_canonical_record_id",
            return_value="REC-9",
        ), patch("alpha.utils.japanese_accuracy_log.jp_accuracy_log") as mock_log:
            with owner._lock:
                owner._resolve_correction_target_locked(
                    channel=0,
                    metadata={
                        "canonical_utterance_id": "U-9",
                        "revision_target_id": "REC-9",
                    },
                )

        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertNotIn("CORRECTION_TARGET_RESOLUTION_FAILED", events)


if __name__ == "__main__":
    unittest.main()
