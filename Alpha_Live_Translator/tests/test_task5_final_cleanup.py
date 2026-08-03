"""Task 5 — deterministic tests for Fix 1 (Japanese single canonical
controller), Fix 2 (duplicate_protection.py identity verification), and
Fix 3 (main_window.py legacy manual-mode canonical_utterance_id routing).

No real audio, no live provider calls, no timers.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    reset_for_session,
    resolve_canonical_record_id,
)
from alpha.transcription.duplicate_protection import DuplicateProtectionMixin  # noqa: E402
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    get_utterance_lifecycle,
    reset_utterance_lifecycle,
)

from tests.test_task2g_acceptance_gate import ManualModeCommitHost  # noqa: E402


class Fix1CanonicalControllerTests(unittest.TestCase):
    """FIX 1: the Japanese assembler must propose to utterance_lifecycle.py
    (accept_boundary_proposal) instead of committing independently — the
    concrete, testable consequence is that the identity registry actually
    gets populated, which it never did before this fix."""

    def setUp(self) -> None:
        self.session_id = "sess-5-fix1"
        ctl.reset_for_run("run-5-fix1")
        reset_for_session(self.session_id)
        self.host = type("H", (), {})()
        reset_utterance_lifecycle(self.host, session_id=self.session_id)
        self.controller = get_utterance_lifecycle(self.host)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-5-fix1")
        reset_for_session("teardown-5-fix1")

    def test_1_accept_boundary_proposal_registers_identity_and_commits(self) -> None:
        result = self.controller.accept_boundary_proposal(
            action="commit_new",
            text="これはテストの発言です",
            speaker=1,
            channel=0,
            canonical_utterance_id="jp-utt-fix1-test",
            source_version=1,
            source_raw_event_ids=["raw-fix1-1"],
            commit_reason="test",
        )
        self.assertTrue(result["success"], result)
        self.assertTrue(result["record_id"])
        # This is the concrete proof Fix 1 closes the gap: before the fix,
        # the Japanese assembler called execute_pipeline_commit directly
        # and never registered identity, so this would always resolve to
        # empty for Japanese commits.
        resolved = resolve_canonical_record_id(
            session_id=self.session_id,
            channel_index=0,
            canonical_utterance_id="jp-utt-fix1-test",
        )
        self.assertEqual(str(resolved), result["record_id"])

    def test_2_missing_canonical_utterance_id_fails_closed(self) -> None:
        result = self.controller.accept_boundary_proposal(
            action="commit_new",
            text="identity-less text",
            speaker=1,
            channel=0,
            canonical_utterance_id="",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "missing_canonical_utterance_id")


class Fix2IdentityVerificationTests(unittest.TestCase):
    """FIX 2: duplicate_protection.py must verify an already_committed claim
    against the identity registry instead of trusting caller-supplied flags
    outright."""

    class _Host(DuplicateProtectionMixin):
        def __init__(self, session_id: str) -> None:
            self.transcript_store = TranscriptStore()
            self._live_session_id = session_id
            self._frozen_ledger_error_count = 0

    def setUp(self) -> None:
        self.session_id = "sess-5-fix2"
        ctl.reset_for_run("run-5-fix2")
        reset_for_session(self.session_id)
        self.host = self._Host(self.session_id)

    def tearDown(self) -> None:
        ctl.reset_for_run("teardown-5-fix2")
        reset_for_session("teardown-5-fix2")

    def test_1_unverified_already_committed_claim_is_not_trusted(self) -> None:
        # Claims _jp_continuity_assembler=True (the exact flag the old code
        # trusted outright) but the identity registry has no entry for this
        # canonical_utterance_id at all -- must fall through to the real
        # commit path (execute_pipeline_commit), not skip it.
        item = {
            "speaker": 1,
            "text": "unverified claim text",
            "is_final": True,
            "session_id": self.session_id,
            "channel_index": 0,
            "canonical_utterance_id": "jp-utt-unverified",
            "provider_utterance_id": "prov-unverified",
            "source_version": 1,
            "source_raw_event_ids": ["raw-unverified-1"],
            "_jp_continuity_assembler": True,
            "translation_eligible": True,
        }
        self.host._display_transcript_item(dict(item))
        segments = self.host.transcript_store.get_all()
        self.assertEqual(len(segments), 1, segments)
        self.assertEqual(segments[0].text, "unverified claim text")
        # The real commit path ran (not skipped) -- proven by the identity
        # registry now actually having an entry, since only
        # execute_pipeline_commit + assign_canonical_record_id populate it.
        resolved = resolve_canonical_record_id(
            session_id=self.session_id, channel_index=0,
            canonical_utterance_id="jp-utt-unverified",
        )
        self.assertTrue(resolved, "expected the fallback commit path to have registered identity")

    def test_2_verified_already_committed_claim_is_trusted_and_not_recommitted(self) -> None:
        # First commit for real (registers identity), then replay the same
        # item marked already_committed=True with a matching record id --
        # this time the claim IS verifiable and must be trusted (no second
        # ledger mutation attempt).
        item = {
            "speaker": 1,
            "text": "verified claim text",
            "is_final": True,
            "session_id": self.session_id,
            "channel_index": 0,
            "canonical_utterance_id": "jp-utt-verified",
            "provider_utterance_id": "prov-verified",
            "source_version": 1,
            "source_raw_event_ids": ["raw-verified-1"],
            "translation_eligible": True,
        }
        self.host._display_transcript_item(dict(item))
        record_id = resolve_canonical_record_id(
            session_id=self.session_id, channel_index=0,
            canonical_utterance_id="jp-utt-verified",
        )
        self.assertTrue(record_id)
        counts_before = ctl.get_action_counts()

        replay = dict(item)
        replay["canonical_record_id"] = str(record_id)
        replay["canonical_ledger_committed"] = True
        self.host._display_transcript_item(replay)

        # already_committed was trusted (verified) -- decide_transcript_action
        # sees the same text as previous and skips, so no new ledger action
        # and no duplicate segment.
        self.assertEqual(ctl.get_action_counts(), counts_before)
        segments = self.host.transcript_store.get_all()
        self.assertEqual(len(segments), 1, segments)


class Fix3ManualModeIdentityTests(unittest.TestCase):
    """FIX 3: the manual-mode legacy paths in main_window.py must assign a
    real canonical_utterance_id instead of leaving it unset."""

    def test_1_new_manual_mode_segment_gets_real_canonical_utterance_id(self) -> None:
        host = ManualModeCommitHost()
        item = host.commit(1, "これはテストの音声")
        self.assertTrue(
            item.get("canonical_utterance_id"),
            "Fix 3: a new manual-mode commit must carry a real canonical_utterance_id",
        )
        # ManualModeCommitHost.commit()'s item never sets channel_index (its
        # commit()-built item omits it entirely), so it resolves to None
        # through duplicate_protection.py's item.get("channel_index",
        # item.get("channel")) fallback -- match that exactly here.
        resolved = resolve_canonical_record_id(
            session_id=host._live_session_id,
            channel_index=None,
            canonical_utterance_id=item["canonical_utterance_id"],
        )
        self.assertTrue(resolved, "the assigned canonical_utterance_id must resolve in the identity registry")

    def test_2_merged_continuation_reuses_same_utterance_id_with_bumped_version(self) -> None:
        host = ManualModeCommitHost()
        first_item = host.commit(1, "これはテストの音声")
        first_id = first_item.get("canonical_utterance_id")
        self.assertTrue(first_id)
        second_item = host.commit(1, "認識をテストしています")
        # Same speaker, no interjection -> compound continuation merges into
        # the SAME utterance identity, not a fresh one.
        self.assertEqual(second_item.get("canonical_utterance_id"), first_id)
        self.assertGreater(int(second_item.get("source_version") or 0), 1)
        segments = host.transcript_store.get_all()
        self.assertEqual(len(segments), 1, segments)


if __name__ == "__main__":
    unittest.main()
