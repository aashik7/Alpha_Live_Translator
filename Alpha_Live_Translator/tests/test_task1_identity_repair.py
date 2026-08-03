"""Deterministic offline validation for Task 1 canonical identity repair."""

from __future__ import annotations

import json
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
    get_identity_entry,
    observe_identity,
    reset_for_session,
)
from alpha.transcription.duplicate_protection import DuplicateProtectionMixin  # noqa: E402
from alpha.transcription.pipeline_commit_transaction import execute_pipeline_commit  # noqa: E402
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    get_utterance_lifecycle,
    reset_utterance_lifecycle,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
TASK1_FIXTURE = FIXTURES_DIR / "task1_identity_sequences.json"
REVISION_FIXTURE = FIXTURES_DIR / "v25_3_revision_events.json"


class IdentityTestHost(DuplicateProtectionMixin):
    def __init__(self, session_id: str = "sess-test", run_id: str = "run-test") -> None:
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
            {
                "kind": "interim",
                "speaker": speaker,
                "text": text,
                "metadata": dict(metadata or {}),
            }
        )

    def _publish_final_transcript_segment(
        self,
        speaker: int,
        text: str,
        metadata: dict | None = None,
        commit_reason: str = "",
    ) -> None:
        item = {
            "speaker": speaker,
            "text": text,
            "is_final": True,
            "timestamp": "00:00",
        }
        item.update(dict(metadata or {}))
        item["commit_reason"] = commit_reason
        self.published_items.append({"kind": "final", "item": dict(item)})
        self._display_transcript_item(item)

    def _on_store_segment_added(
        self,
        speaker: int,
        text: str,
        canonical_utterance_id: str = "",
        source_version: int = 0,
        source_record_id: str = "",
    ) -> None:
        self.translation_added.append(
            {
                "speaker": speaker,
                "text": text,
                "canonical_utterance_id": canonical_utterance_id,
                "source_version": source_version,
                "source_record_id": source_record_id,
            }
        )

    def _on_store_segment_updated(
        self,
        speaker: int,
        text: str,
        canonical_utterance_id: str = "",
        source_version: int = 0,
        source_record_id: str = "",
    ) -> None:
        self.translation_updated.append(
            {
                "speaker": speaker,
                "text": text,
                "canonical_utterance_id": canonical_utterance_id,
                "source_version": source_version,
                "source_record_id": source_record_id,
            }
        )


class Task1IdentityRepairTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.fixture = json.loads(TASK1_FIXTURE.read_text(encoding="utf-8"))
        self.revision_fixture = json.loads(REVISION_FIXTURE.read_text(encoding="utf-8"))
        self.host = IdentityTestHost()
        self.owner = get_utterance_lifecycle(self.host)

    def tearDown(self) -> None:
        ctl.reset_for_run("tear-down")
        reset_for_session("tear-down")
        reset_utterance_lifecycle(self.host, session_id="tear-down")

    def _active_texts(self) -> list[str]:
        return [str(r.get("final_text") or "") for r in ctl.get_active_records()]

    def _active_record_ids(self) -> list[str]:
        return [str(r.get("record_id") or "") for r in ctl.get_active_records()]

    def _history_actions(self) -> list[str]:
        return [str(ev.get("action") or "") for ev in ctl.get_record_history()]

    def _ingest_progressive(self, events: list[dict[str, object]]) -> list[object]:
        decisions = []
        for event in events:
            decisions.append(
                self.owner.on_final_chunk(
                    text=str(event["text"]),
                    speaker=1,
                    channel=event["channel"],
                    start=event["start"],
                    end=event["end"],
                    is_final=True,
                    speech_final=event["speech_final"],
                    event_id=str(event["event_id"]),
                    metadata={
                        "channel_index": event["channel"],
                        "provider_utterance_id": str(event["event_id"]),
                    },
                    deepgram_request_id=str(event["event_id"]),
                )
            )
        return decisions

    def test_progressive_identity_single_terminal_record(self) -> None:
        decisions = self._ingest_progressive(self.fixture["english_progressive"])

        self.assertEqual([d.utterance_id for d in decisions], ["U-1", "U-1", "U-1"])
        self.assertEqual([d.version for d in decisions], [1, 2, 3])
        self.assertEqual(
            [bool(d.metadata.get("translation_eligible")) for d in decisions],
            [False, False, True],
        )
        self.assertEqual(self._active_texts(), ["My name is Tariqul"])
        self.assertEqual(len(self._active_record_ids()), 1)
        self.assertEqual(ctl.get_action_counts()["append"], 1)
        self.assertEqual(ctl.get_action_counts()["revise"], 0)
        self.assertEqual(len(self.host.translation_added), 1)
        self.assertEqual(self.host.translation_added[0]["source_version"], 3)

        entry = get_identity_entry(
            session_id=self.host._live_session_id,
            channel_index=0,
            canonical_utterance_id="U-1",
        )
        self.assertEqual(entry["source_version"], 3)
        self.assertTrue(entry["translation_eligible"])
        self.assertEqual(entry["canonical_record_id"], self._active_record_ids()[0])

    def test_exact_duplicate_final_causes_zero_mutations(self) -> None:
        item = {
            "speaker": 1,
            "text": "Exact final sentence.",
            "is_final": True,
            "session_id": self.host._live_session_id,
            "channel_index": 0,
            "canonical_utterance_id": "U-1",
            "provider_utterance_id": "dup-1",
            "source_version": 1,
            "source_raw_event_ids": ["dup-raw-1"],
            "translation_eligible": True,
            "lifecycle_state": "COMMITTED",
            "canonical_decision": "TERMINAL_COMMIT",
        }
        self.host._display_transcript_item(dict(item))
        counts_before = ctl.get_action_counts()
        records_before = ctl.get_active_records()

        self.host._display_transcript_item(dict(item))

        self.assertEqual(ctl.get_action_counts(), counts_before)
        self.assertEqual(ctl.get_active_records(), records_before)
        self.assertEqual(len(self.host.translation_added), 1)
        self.assertEqual(self.host._transcript_stability_counters.skipped, 1)

    def test_wrong_utterance_revision_is_rejected(self) -> None:
        case = self.fixture["english_wrong_revision"]
        for index, base in enumerate(case["base_records"], start=1):
            self.host._display_transcript_item(
                {
                    "speaker": 1,
                    "text": base["text"],
                    "is_final": True,
                    "session_id": self.host._live_session_id,
                    "channel_index": 0,
                    "canonical_utterance_id": base["canonical_utterance_id"],
                    "provider_utterance_id": f"base-{index}",
                    "source_version": base["source_version"],
                    "source_raw_event_ids": list(base["source_raw_event_ids"]),
                    "translation_eligible": True,
                    "lifecycle_state": "COMMITTED",
                    "canonical_decision": "TERMINAL_COMMIT",
                }
            )
        record_ids = self._active_record_ids()
        self.assertEqual(len(record_ids), 2)

        self.host._display_transcript_item(
            {
                "speaker": 1,
                "text": case["bad_revision"]["text"],
                "is_final": True,
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": case["bad_revision"]["canonical_utterance_id"],
                "provider_utterance_id": "bad-revision",
                "source_version": case["bad_revision"]["source_version"],
                "source_raw_event_ids": list(case["bad_revision"]["source_raw_event_ids"]),
                "translation_eligible": True,
                "lifecycle_state": "COMMITTED",
                "canonical_decision": "SUPERSEDE",
                "revision_target_id": record_ids[0],
            }
        )

        self.assertEqual(self._active_record_ids(), record_ids)
        self.assertEqual(self._active_texts(), ["Alpha sentence.", "Bravo sentence."])
        self.assertEqual(ctl.get_action_counts()["revise"], 0)

    def test_out_of_order_version_is_rejected_as_stale(self) -> None:
        self.host._display_transcript_item(
            {
                "speaker": 1,
                "text": "Version one.",
                "is_final": True,
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-1",
                "provider_utterance_id": "stale-1",
                "source_version": 1,
                "source_raw_event_ids": ["raw-stale-1"],
                "translation_eligible": True,
                "lifecycle_state": "COMMITTED",
                "canonical_decision": "TERMINAL_COMMIT",
            }
        )
        self.host._display_transcript_item(
            {
                "speaker": 1,
                "text": "Version two.",
                "is_final": True,
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-1",
                "provider_utterance_id": "stale-2",
                "source_version": 2,
                "source_raw_event_ids": ["raw-stale-2"],
                "translation_eligible": True,
                "lifecycle_state": "COMMITTED",
                "canonical_decision": "SUPERSEDE",
                "revision_target_id": self._active_record_ids()[0],
            }
        )
        counts_before = ctl.get_action_counts()
        texts_before = self._active_texts()

        self.host._display_transcript_item(
            {
                "speaker": 1,
                "text": "Version one stale replay.",
                "is_final": True,
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-1",
                "provider_utterance_id": "stale-3",
                "source_version": 1,
                "source_raw_event_ids": ["raw-stale-3"],
                "translation_eligible": True,
                "lifecycle_state": "COMMITTED",
                "canonical_decision": "SUPERSEDE",
                "revision_target_id": self._active_record_ids()[0],
            }
        )

        self.assertEqual(ctl.get_action_counts(), counts_before)
        self.assertEqual(self._active_texts(), texts_before)

    def test_same_version_different_text_is_rejected(self) -> None:
        self.host._display_transcript_item(
            {
                "speaker": 1,
                "text": "Original text.",
                "is_final": True,
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-1",
                "provider_utterance_id": "same-1",
                "source_version": 1,
                "source_raw_event_ids": ["raw-same-1"],
                "translation_eligible": True,
                "lifecycle_state": "COMMITTED",
                "canonical_decision": "TERMINAL_COMMIT",
            }
        )
        counts_before = ctl.get_action_counts()
        texts_before = self._active_texts()

        self.host._display_transcript_item(
            {
                "speaker": 1,
                "text": "Different text.",
                "is_final": True,
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-1",
                "provider_utterance_id": "same-2",
                "source_version": 1,
                "source_raw_event_ids": ["raw-same-2"],
                "translation_eligible": True,
                "lifecycle_state": "COMMITTED",
                "canonical_decision": "TERMINAL_COMMIT",
            }
        )

        self.assertEqual(ctl.get_action_counts(), counts_before)
        self.assertEqual(self._active_texts(), texts_before)

    def test_cross_channel_utterance_end_is_ignored(self) -> None:
        first = self.fixture["english_progressive"][0]
        decision = self.owner.on_final_chunk(
            text=str(first["text"]),
            speaker=1,
            channel=0,
            start=0.0,
            end=0.3,
            is_final=True,
            speech_final=False,
            event_id="cross-1",
            metadata={"channel_index": 0, "provider_utterance_id": "cross-1"},
            deepgram_request_id="cross-1",
        )
        self.assertEqual(decision.utterance_id, "U-1")

        end_decision = self.owner.on_utterance_end(
            channel=1,
            event_id="cross-end",
            metadata={"channel_index": 1},
        )

        self.assertEqual(end_decision.reason, "cross_channel_utterance_end_ignored")
        self.assertEqual(self._active_texts(), [])
        self.assertEqual(ctl.get_action_counts()["append"], 0)
        self.assertEqual(len([x for x in self.host.published_items if x["kind"] == "final"]), 0)

    def test_canonical_commit_applied_when_evidence_write_fails(self) -> None:
        with patch(
            "alpha.utils.accuracy_stage_capture.record_assembler_only_event",
            side_effect=RuntimeError("stage boom"),
        ):
            result = execute_pipeline_commit(
                speaker=1,
                assembler_text="Evidence failure sentence.",
                final_text="Evidence failure sentence.",
                metadata={
                    "session_id": self.host._live_session_id,
                    "channel_index": 0,
                    "canonical_utterance_id": "U-1",
                    "source_version": 1,
                    "canonical_decision": "TERMINAL_COMMIT",
                },
                requested_action="append",
                applied_action="append",
                source_raw_event_ids=["raw-evidence-1"],
                commit_reason="unit_test",
            )

        self.assertTrue(result.canonical_commit_applied)
        self.assertTrue(result.evidence_write_failed)
        self.assertFalse(result.metrics_write_failed)
        self.assertEqual(len(self._active_record_ids()), 1)
        self.assertEqual(ctl.get_action_counts()["append"], 1)

    def test_canonical_commit_applied_when_metrics_write_fails(self) -> None:
        with patch(
            "alpha.utils.live_runtime_metrics.note_assembler_event",
            side_effect=RuntimeError("metrics boom"),
        ):
            result = execute_pipeline_commit(
                speaker=1,
                assembler_text="Metrics failure sentence.",
                final_text="Metrics failure sentence.",
                metadata={
                    "session_id": self.host._live_session_id,
                    "channel_index": 0,
                    "canonical_utterance_id": "U-1",
                    "source_version": 1,
                    "canonical_decision": "TERMINAL_COMMIT",
                },
                requested_action="append",
                applied_action="append",
                source_raw_event_ids=["raw-metrics-1"],
                commit_reason="unit_test",
            )

        self.assertTrue(result.canonical_commit_applied)
        self.assertFalse(result.evidence_write_failed)
        self.assertTrue(result.metrics_write_failed)
        self.assertEqual(len(self._active_record_ids()), 1)
        self.assertEqual(ctl.get_action_counts()["append"], 1)

    def test_same_mutation_submitted_twice_is_idempotent(self) -> None:
        metadata = {
            "session_id": self.host._live_session_id,
            "channel_index": 0,
            "canonical_utterance_id": "U-1",
            "source_version": 1,
            "canonical_decision": "TERMINAL_COMMIT",
        }
        first = execute_pipeline_commit(
            speaker=1,
            assembler_text="Idempotent sentence.",
            final_text="Idempotent sentence.",
            metadata=dict(metadata),
            requested_action="append",
            applied_action="append",
            source_raw_event_ids=["raw-idem-1"],
            commit_reason="unit_test",
        )
        second = execute_pipeline_commit(
            speaker=1,
            assembler_text="Idempotent sentence.",
            final_text="Idempotent sentence.",
            metadata=dict(metadata),
            requested_action="append",
            applied_action="append",
            source_raw_event_ids=["raw-idem-1"],
            commit_reason="unit_test",
        )

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(first.record_id, second.record_id)
        self.assertEqual(len(self._active_record_ids()), 1)
        self.assertEqual(self._history_actions().count("append"), 1)

    def test_session_isolation_allows_provider_id_reuse(self) -> None:
        reset_for_session("sess-a")
        obs1 = observe_identity(
            session_id="sess-a",
            channel_index=0,
            canonical_utterance_id="U-1",
            provider_utterance_id="provider-constant",
            source_version=1,
            decision="TERMINAL_COMMIT",
            text="Session A",
            lifecycle_state="COMMITTED",
            translation_eligible=True,
        )
        self.assertTrue(obs1.accepted)

        reset_for_session("sess-b")
        obs2 = observe_identity(
            session_id="sess-b",
            channel_index=0,
            canonical_utterance_id="U-1",
            provider_utterance_id="provider-constant",
            source_version=1,
            decision="TERMINAL_COMMIT",
            text="Session B",
            lifecycle_state="COMMITTED",
            translation_eligible=True,
        )
        self.assertTrue(obs2.accepted)
        self.assertEqual(obs2.entry["provider_utterance_id"], "provider-constant")
        self.assertEqual(obs2.entry["session_id"], "sess-b")

    def test_two_completed_sentences_remain_two_canonical_utterances(self) -> None:
        decisions = self._ingest_progressive(self.fixture["english_two_sentences"])

        self.assertEqual([d.utterance_id for d in decisions], ["U-1", "U-2"])
        self.assertEqual(self._active_texts(), ["This is sentence one.", "This is sentence two."])
        self.assertEqual(ctl.get_action_counts()["append"], 2)
        self.assertEqual(len(self.host.translation_added), 2)

    def test_replay_fixture_cumulative_revision_cases(self) -> None:
        cases = {case["case_id"]: case for case in self.revision_fixture["cases"]}
        self.assertIn("synthetic_direct_extension", cases)
        self.assertIn("synthetic_missing_lineage", cases)

        direct = cases["synthetic_direct_extension"]
        base = execute_pipeline_commit(
            speaker=1,
            assembler_text=direct["previous_record"]["text"],
            final_text=direct["previous_record"]["text"],
            metadata={
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-1",
                "source_version": 1,
                "canonical_decision": "TERMINAL_COMMIT",
            },
            requested_action="append",
            applied_action="append",
            source_raw_event_ids=list(direct["previous_record"]["source_raw_event_ids"]),
            commit_reason="fixture_base",
        )
        revise = execute_pipeline_commit(
            speaker=1,
            assembler_text=direct["candidate_text"],
            final_text=direct["candidate_text"],
            metadata={
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-1",
                "source_version": 2,
                "canonical_decision": "SUPERSEDE",
            },
            requested_action="revise",
            applied_action="revise",
            revision_target_id=base.record_id,
            source_raw_event_ids=list(direct["candidate_raw_event_ids"]),
            commit_reason="fixture_revise",
        )
        self.assertTrue(revise.success)
        self.assertEqual(self._active_texts(), [direct["candidate_text"]])

        ctl.reset_for_run("fixture-missing-lineage")
        reset_for_session(self.host._live_session_id)
        missing = cases["synthetic_missing_lineage"]
        missing_base = execute_pipeline_commit(
            speaker=1,
            assembler_text=missing["previous_record"]["text"],
            final_text=missing["previous_record"]["text"],
            metadata={
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-2",
                "source_version": 1,
                "canonical_decision": "TERMINAL_COMMIT",
            },
            requested_action="append",
            applied_action="append",
            source_raw_event_ids=["raw-seeded"],
            commit_reason="fixture_seed",
        )
        failed = execute_pipeline_commit(
            speaker=1,
            assembler_text=missing["candidate_text"],
            final_text=missing["candidate_text"],
            metadata={
                "session_id": self.host._live_session_id,
                "channel_index": 0,
                "canonical_utterance_id": "U-2",
                "source_version": 2,
                "canonical_decision": "SUPERSEDE",
            },
            requested_action="revise",
            applied_action="revise",
            revision_target_id=missing_base.record_id,
            source_raw_event_ids=list(missing["candidate_raw_event_ids"]),
            commit_reason="fixture_missing_lineage",
        )
        self.assertFalse(failed.success)
        self.assertEqual(failed.failure_reason, "missing_lineage")
        self.assertEqual(len(self._active_record_ids()), 1)


if __name__ == "__main__":
    unittest.main()
