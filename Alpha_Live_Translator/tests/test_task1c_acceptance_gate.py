"""Task 1C — deterministic Phase 1 acceptance-gate tests.

No real audio, no live Deepgram/DeepL calls, no timing-based flakiness:
every scenario is driven by explicit synthetic events / crafted item dicts,
mirroring the harness already proven in test_task1_identity_repair.py.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    reset_for_session,
)
from alpha.transcription.duplicate_protection import DuplicateProtectionMixin  # noqa: E402
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    get_utterance_lifecycle,
    reset_utterance_lifecycle,
)


class IdentityTestHost(DuplicateProtectionMixin):
    """Minimal host duplicated from test_task1_identity_repair.py for isolation."""

    def __init__(self, session_id: str = "sess-1c", run_id: str = "run-1c") -> None:
        self._live_session_id = session_id
        self.transcript_store = TranscriptStore()
        self.translation_added: list[dict[str, object]] = []
        self.translation_updated: list[dict[str, object]] = []
        self.published_items: list[dict[str, object]] = []
        self.initial_verse_box = None
        self._frozen_ledger_error_count = 0
        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        reset_utterance_lifecycle(self, session_id=session_id)

    def on_interim_transcript(self, speaker: int, text: str, metadata: dict | None = None) -> None:
        self.published_items.append(
            {"kind": "interim", "speaker": speaker, "text": text, "metadata": dict(metadata or {})}
        )

    def _publish_final_transcript_segment(
        self, speaker: int, text: str, metadata: dict | None = None, commit_reason: str = ""
    ) -> None:
        item = {"speaker": speaker, "text": text, "is_final": True, "timestamp": "00:00"}
        item.update(dict(metadata or {}))
        item["commit_reason"] = commit_reason
        self.published_items.append({"kind": "final", "item": dict(item)})
        self._display_transcript_item(item)

    def _on_store_segment_added(
        self, speaker: int, text: str, canonical_utterance_id: str = "",
        source_version: int = 0, source_record_id: str = "",
    ) -> None:
        self.translation_added.append(
            {
                "speaker": speaker, "text": text,
                "canonical_utterance_id": canonical_utterance_id,
                "source_version": source_version, "source_record_id": source_record_id,
            }
        )

    def _on_store_segment_updated(
        self, speaker: int, text: str, canonical_utterance_id: str = "",
        source_version: int = 0, source_record_id: str = "",
    ) -> None:
        self.translation_updated.append(
            {
                "speaker": speaker, "text": text,
                "canonical_utterance_id": canonical_utterance_id,
                "source_version": source_version, "source_record_id": source_record_id,
            }
        )


class Task1CAcceptanceGateTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.host = IdentityTestHost()
        self.owner = get_utterance_lifecycle(self.host)

    def tearDown(self) -> None:
        ctl.reset_for_run("tear-down-1c")
        reset_for_session("tear-down-1c")
        reset_utterance_lifecycle(self.host, session_id="tear-down-1c")

    def _active_texts(self) -> list[str]:
        return [str(r.get("final_text") or "") for r in ctl.get_active_records()]

    def _active_record_ids(self) -> list[str]:
        return [str(r.get("record_id") or "") for r in ctl.get_active_records()]

    def _history_actions(self) -> list[str]:
        return [str(ev.get("action") or "") for ev in ctl.get_record_history()]

    def _record_text(self, record_id: str) -> str | None:
        for rec in ctl.get_active_records():
            if rec.get("record_id") == record_id:
                return rec.get("final_text")
        return None

    # ------------------------------------------------------------------
    # 1. Wrong-utterance revision test
    # ------------------------------------------------------------------
    def test_1_wrong_utterance_revision_rejected_not_remapped_to_last_committed(self) -> None:
        self.owner.on_final_chunk(
            text="Alpha sentence.", speaker=1, channel=0, start=0.0, end=0.5,
            is_final=True, speech_final=True, event_id="wu-1",
            metadata={"channel_index": 0}, deepgram_request_id="wu-1",
        )
        self.owner.on_final_chunk(
            text="Bravo sentence.", speaker=1, channel=0, start=5.0, end=5.5,
            is_final=True, speech_final=True, event_id="wu-2",
            metadata={"channel_index": 0}, deepgram_request_id="wu-2",
        )

        record_ids_before = self._active_record_ids()
        texts_before = self._active_texts()
        self.assertEqual(len(record_ids_before), 2)
        self.assertEqual(texts_before, ["Alpha sentence.", "Bravo sentence."])

        # _last_committed is now U-2 (Bravo). This "correction" is
        # timing-adjacent to U-2 and explicitly claims utterance id "U-1"
        # (a different, already-committed utterance). A global
        # `_last_committed`-as-authority remap would have applied this as a
        # correction of U-2's committed record; it must be rejected instead.
        decision = self.owner.on_final_chunk(
            text="Bravo sentence CORRECTED illegally.", speaker=1, channel=0,
            start=5.1, end=5.6, is_final=True, speech_final=True, event_id="wu-3",
            metadata={"channel_index": 0, "canonical_utterance_id": "U-1"},
            deepgram_request_id="wu-3",
        )

        self.assertNotEqual(decision.decision, "SUPERSEDE_PREVIOUS")
        self.assertEqual(self._active_record_ids()[:2], record_ids_before)
        self.assertEqual(self._active_texts()[:2], texts_before)
        self.assertEqual(ctl.get_action_counts()["revise"], 0)

    # ------------------------------------------------------------------
    # 2. Out-of-order / reordered revision test
    # ------------------------------------------------------------------
    def test_2_out_of_order_revision_never_corrupts_another_utterance(self) -> None:
        self.host._display_transcript_item(
            {
                "speaker": 1, "text": "U1 version one.", "is_final": True,
                "session_id": self.host._live_session_id, "channel_index": 0,
                "canonical_utterance_id": "U-1", "provider_utterance_id": "oo-u1-1",
                "source_version": 1, "source_raw_event_ids": ["oo-raw-u1-1"],
                "translation_eligible": True, "lifecycle_state": "COMMITTED",
                "canonical_decision": "TERMINAL_COMMIT",
            }
        )
        self.host._display_transcript_item(
            {
                "speaker": 1, "text": "U2 version one.", "is_final": True,
                "session_id": self.host._live_session_id, "channel_index": 0,
                "canonical_utterance_id": "U-2", "provider_utterance_id": "oo-u2-1",
                "source_version": 1, "source_raw_event_ids": ["oo-raw-u2-1"],
                "translation_eligible": True, "lifecycle_state": "COMMITTED",
                "canonical_decision": "TERMINAL_COMMIT",
            }
        )
        record_ids = self._active_record_ids()
        self.assertEqual(len(record_ids), 2)
        u1_record_id, u2_record_id = record_ids

        # Legitimate, in-order revision of U-1 to version two.
        self.host._display_transcript_item(
            {
                "speaker": 1, "text": "U1 version two.", "is_final": True,
                "session_id": self.host._live_session_id, "channel_index": 0,
                "canonical_utterance_id": "U-1", "provider_utterance_id": "oo-u1-2",
                "source_version": 2, "source_raw_event_ids": ["oo-raw-u1-2"],
                "translation_eligible": True, "lifecycle_state": "COMMITTED",
                "canonical_decision": "SUPERSEDE", "revision_target_id": u1_record_id,
            }
        )
        self.assertEqual(self._record_text(u2_record_id), "U2 version one.")
        counts_before = ctl.get_action_counts()

        # A delayed / reordered version-one revision for U-1 arrives after
        # version two already committed. Must resolve as stale/rejected, and
        # must not touch U-2's record even though both share session+channel.
        self.host._display_transcript_item(
            {
                "speaker": 1, "text": "U1 version one late replay.", "is_final": True,
                "session_id": self.host._live_session_id, "channel_index": 0,
                "canonical_utterance_id": "U-1", "provider_utterance_id": "oo-u1-1-late",
                "source_version": 1, "source_raw_event_ids": ["oo-raw-u1-1-late"],
                "translation_eligible": True, "lifecycle_state": "COMMITTED",
                "canonical_decision": "SUPERSEDE", "revision_target_id": u1_record_id,
            }
        )

        self.assertEqual(ctl.get_action_counts(), counts_before)
        self.assertEqual(self._record_text(u1_record_id), "U1 version two.")
        self.assertEqual(self._record_text(u2_record_id), "U2 version one.")

    # ------------------------------------------------------------------
    # 3. Cross-channel UtteranceEnd test
    # ------------------------------------------------------------------
    def test_3_cross_channel_utterance_end_does_not_commit_active_utterance(self) -> None:
        decision = self.owner.on_final_chunk(
            text="Held on channel zero", speaker=1, channel=0, start=0.0, end=0.3,
            is_final=True, speech_final=False, event_id="ce-1",
            metadata={"channel_index": 0}, deepgram_request_id="ce-1",
        )
        self.assertEqual(decision.utterance_id, "U-1")
        self.assertIsNotNone(self.owner.active)

        end_decision = self.owner.on_utterance_end(
            channel=1, event_id="ce-end", metadata={"channel_index": 1}
        )

        self.assertEqual(end_decision.reason, "cross_channel_utterance_end_ignored")
        self.assertIsNotNone(self.owner.active)
        self.assertEqual(self.owner.active.text, "Held on channel zero")
        self.assertEqual(self._active_texts(), [])
        self.assertEqual(ctl.get_action_counts()["append"], 0)

    # ------------------------------------------------------------------
    # 4. Exact-duplicate-final test
    # ------------------------------------------------------------------
    def test_4a_exact_duplicate_held_chunk_ignored_by_lifecycle(self) -> None:
        first = self.owner.on_final_chunk(
            text="Building up the sentence", speaker=1, channel=0, start=0.0, end=0.4,
            is_final=True, speech_final=False, event_id="hold-1",
            metadata={"channel_index": 0}, deepgram_request_id="hold-1",
        )
        self.assertEqual(first.decision, "HOLD_FINAL_CHUNK")
        self.assertEqual(first.version, 1)

        # Deepgram redelivers the identical (non-terminal) chunk.
        second = self.owner.on_final_chunk(
            text="Building up the sentence", speaker=1, channel=0, start=0.0, end=0.4,
            is_final=True, speech_final=False, event_id="hold-2",
            metadata={"channel_index": 0}, deepgram_request_id="hold-2",
        )

        self.assertEqual(second.decision, "IGNORE_DUPLICATE")
        self.assertEqual(second.version, 1)
        self.assertEqual(ctl.get_action_counts()["append"], 0)
        self.assertEqual(ctl.get_action_counts()["revise"], 0)

    def test_4b_exact_duplicate_committed_final_replay_causes_zero_new_commits(self) -> None:
        item = {
            "speaker": 1, "text": "Exact committed final replay.", "is_final": True,
            "session_id": self.host._live_session_id, "channel_index": 0,
            "canonical_utterance_id": "U-1", "provider_utterance_id": "dup-replay-1",
            "source_version": 1, "source_raw_event_ids": ["dup-replay-raw-1"],
            "translation_eligible": True, "lifecycle_state": "COMMITTED",
            "canonical_decision": "TERMINAL_COMMIT",
        }
        self.host._display_transcript_item(dict(item))
        counts_before = ctl.get_action_counts()
        records_before = ctl.get_active_records()

        # Identical (session, channel, canonical_utterance_id, version,
        # decision) resubmitted -- a network-retry-style exact replay.
        self.host._display_transcript_item(dict(item))

        self.assertEqual(ctl.get_action_counts(), counts_before)
        self.assertEqual(ctl.get_active_records(), records_before)
        self.assertEqual(self.host._transcript_stability_counters.skipped, 1)

    # ------------------------------------------------------------------
    # 5. Single-mutation test (evidence-write failure, no fallback append)
    # ------------------------------------------------------------------
    def test_5_evidence_write_failure_causes_no_fallback_append(self) -> None:
        with patch(
            "alpha.utils.accuracy_stage_capture.record_assembler_only_event",
            side_effect=RuntimeError("stage boom"),
        ):
            self.host._display_transcript_item(
                {
                    "speaker": 1, "text": "Evidence-failure full chain sentence.",
                    "is_final": True, "session_id": self.host._live_session_id,
                    "channel_index": 0, "canonical_utterance_id": "U-1",
                    "provider_utterance_id": "ev-full-1", "source_version": 1,
                    "source_raw_event_ids": ["ev-full-raw-1"],
                    "translation_eligible": True, "lifecycle_state": "COMMITTED",
                    "canonical_decision": "TERMINAL_COMMIT",
                }
            )

        # Ledger mutation applied exactly once -- no second/fallback append
        # triggered by the evidence-write failure.
        self.assertEqual(ctl.get_action_counts()["append"], 1)
        self.assertEqual(self._history_actions().count("append"), 1)
        self.assertEqual(len(self._active_record_ids()), 1)
        # UI/translation path still completed (evidence failure is non-fatal).
        self.assertEqual(len(self.host.translation_added), 1)

    # ------------------------------------------------------------------
    # Bonus: interim Case A channel-safety fix (this session)
    # ------------------------------------------------------------------
    def test_6_incompatible_interim_is_ignored_without_corrupting_held_utterance(self) -> None:
        self.owner.on_final_chunk(
            text="Speaker zero opening.", speaker=1, channel=0, start=0.0, end=0.4,
            is_final=True, speech_final=False, event_id="ia-1",
            metadata={"channel_index": 0}, deepgram_request_id="ia-1",
        )
        self.assertEqual(self.owner.active.channel, 0)
        self.assertEqual(self.owner.active.text, "Speaker zero opening.")

        interim = self.owner.on_interim(
            text="Speaker one interrupting", speaker=2, channel=1,
            start=10.0, end=10.3, event_id="ia-interim-1",
            metadata={"channel_index": 1},
        )

        self.assertEqual(interim.decision, "IGNORE_DUPLICATE")
        self.assertEqual(interim.reason, "interim_incompatible_with_active_utterance")
        # Held channel-0 utterance must be completely untouched.
        self.assertEqual(self.owner.active.channel, 0)
        self.assertEqual(self.owner.active.text, "Speaker zero opening.")


if __name__ == "__main__":
    unittest.main()
