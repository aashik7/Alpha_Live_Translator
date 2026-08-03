"""Task 2C — deterministic Phase 2 acceptance-gate tests.

No real audio, no live Deepgram/DeepL calls, no timing-based flakiness:
timeout scenarios use the assembler's synchronous
`try_execute_continuity_hold` entry point instead of real timers.
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
from alpha.transcription.japanese_sentence_assembler import (  # noqa: E402
    get_japanese_continuity_assembler,
)
from alpha.transcription.japanese_final_chunk_stabilizer import (  # noqa: E402
    get_japanese_final_stabilizer,
)


class EnglishTestHost(DuplicateProtectionMixin):
    """Minimal host duplicated from prior task test files for isolation."""

    def __init__(self, session_id: str = "sess-2c-en", run_id: str = "run-2c-en") -> None:
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

    def on_interim_transcript(self, speaker, text, metadata=None) -> None:
        self.published_items.append(
            {"kind": "interim", "speaker": speaker, "text": text, "metadata": dict(metadata or {})}
        )

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, commit_reason: str = ""
    ) -> None:
        item = {"speaker": speaker, "text": text, "is_final": True, "timestamp": "00:00"}
        item.update(dict(metadata or {}))
        item["commit_reason"] = commit_reason
        self.published_items.append({"kind": "final", "item": dict(item)})
        self._display_transcript_item(item)

    def _on_store_segment_added(self, *args, **kwargs) -> None:
        self.translation_added.append({"args": args, "kwargs": kwargs})

    def _on_store_segment_updated(self, *args, **kwargs) -> None:
        self.translation_updated.append({"args": args, "kwargs": kwargs})


class JapaneseTestHost:
    """Minimal host for JapaneseContinuityAssembler / stabilizer, isolated per test."""

    def __init__(self, session_id: str = "sess-2c-ja", run_id: str = "run-2c-ja") -> None:
        self._live_session_id = session_id
        self._listen_language = "ja"
        self._is_finalizing = False
        self._is_stopping = False
        self.is_listening = True
        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 1: the Japanese assembler
        # now proposes commits through utterance_lifecycle.py's canonical
        # controller (accept_boundary_proposal), which needs its session_id
        # set exactly like the real app's session-start code
        # (session_runtime.begin_live_session) does for every session,
        # English or Japanese -- previously unnecessary here since Japanese
        # never touched this module.
        reset_utterance_lifecycle(self, session_id=session_id)


class Task2CAcceptanceGateTests(unittest.TestCase):
    maxDiff = None

    def _active_records(self) -> list[dict]:
        return ctl.get_active_records()

    # ------------------------------------------------------------------
    # 1. Progressive-revision test (English)
    # ------------------------------------------------------------------
    def test_1_progressive_revision_single_record(self) -> None:
        host = EnglishTestHost()
        owner = get_utterance_lifecycle(host)
        events = [
            ("My", False, 0.0, 0.3),
            ("My name", False, 0.0, 0.7),
            ("My name is Tariqul", True, 0.0, 1.2),
        ]
        decisions = []
        for text, speech_final, start, end in events:
            decisions.append(
                owner.on_final_chunk(
                    text=text, speaker=1, channel=0, start=start, end=end,
                    is_final=True, speech_final=speech_final,
                    event_id=f"prog-{text}", metadata={"channel_index": 0},
                    deepgram_request_id=f"prog-{text}",
                )
            )
        self.assertEqual([d.utterance_id for d in decisions], ["U-1", "U-1", "U-1"])
        records = self._active_records()
        self.assertEqual(len(records), 1, f"expected exactly one record, got {records}")
        self.assertEqual(records[0]["final_text"], "My name is Tariqul")
        self.assertEqual(ctl.get_action_counts()["append"], 1)
        self.assertEqual(ctl.get_action_counts()["revise"], 0)

    # ------------------------------------------------------------------
    # 2. Sentence-boundary test (English)
    # ------------------------------------------------------------------
    def test_2_sentence_boundary_stays_two_records(self) -> None:
        host = EnglishTestHost()
        owner = get_utterance_lifecycle(host)
        owner.on_final_chunk(
            text="Sentence one.", speaker=1, channel=0, start=0.0, end=0.9,
            is_final=True, speech_final=True, event_id="sb-1",
            metadata={"channel_index": 0}, deepgram_request_id="sb-1",
        )
        owner.on_final_chunk(
            text="Sentence two.", speaker=1, channel=0, start=3.5, end=4.4,
            is_final=True, speech_final=True, event_id="sb-2",
            metadata={"channel_index": 0}, deepgram_request_id="sb-2",
        )
        records = self._active_records()
        self.assertEqual(len(records), 2, f"expected two separate records, got {records}")
        self.assertEqual(
            [r["final_text"] for r in records], ["Sentence one.", "Sentence two."]
        )

    # ------------------------------------------------------------------
    # 3. Japanese speaker-boundary test
    # ------------------------------------------------------------------
    def test_3_japanese_speaker_change_never_merges(self) -> None:
        host = JapaneseTestHost()
        stabilizer = get_japanese_final_stabilizer(host)
        stabilizer.set_accepting(True)
        assembler = get_japanese_continuity_assembler(host)
        text_a = "こんにちは、今日は天気がいいですね。"
        text_b = "はい、本当にそうですね。"
        stabilizer.ingest(1, text_a, metadata={})
        stabilizer.ingest(2, text_b, metadata={})
        # "stop_listening" also drains the boundary-stabilizer's own pending
        # buffer (text_b may be held there pending more context), unlike a
        # generic forced flush which only drains the assembler's buffer.
        assembler.flush("stop_listening")

        records = self._active_records()
        texts = [r.get("final_text") for r in records]
        speakers = [r.get("speaker") for r in records]
        self.assertEqual(len(records), 2, f"expected two separate records, got {records}")
        self.assertEqual(speakers, [1, 2])
        # Neither committed record may contain both speakers' text merged
        # into one canonical line.
        for text in texts:
            self.assertFalse(
                text_a in text and text_b in text,
                f"speaker turns merged into one record: {text!r}",
            )
        self.assertIn(text_a, texts[0])
        self.assertNotIn(text_b, texts[0])
        self.assertIn(text_b, texts[1])
        self.assertNotIn(text_a, texts[1])
        # Exact-equality check: this is the precise failing case from
        # TASK_2C_REPORT.md section 6 (a short leading-particle reply
        # "はい..." from a different speaker was merged via
        # stable_line_revision.py / japanese_boundary_stabilizer.py).
        self.assertEqual(records[0]["final_text"], text_a)
        self.assertEqual(records[1]["final_text"], text_b)

    # ------------------------------------------------------------------
    # 4. Synthetic re-entry test
    # ------------------------------------------------------------------
    def test_4_synthetic_output_never_reenters_raw_ingress(self) -> None:
        host = JapaneseTestHost()
        stabilizer = get_japanese_final_stabilizer(host)
        stabilizer.set_accepting(True)

        with patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.get_japanese_continuity_assembler"
        ) as mock_get_assembler, patch(
            "alpha.utils.accuracy_stage_capture.record_raw_deepgram_final"
        ) as mock_record_raw:
            result = stabilizer.ingest(
                1, "synthetic assembler-merged text",
                metadata={"synthetic_record": True, "synthetic_lineage": True},
            )

        self.assertTrue(result)
        mock_get_assembler.assert_not_called()
        mock_record_raw.assert_not_called()

    def test_4b_non_synthetic_input_still_reaches_assembler(self) -> None:
        # Control case: confirms the guard in 4 above is specific to
        # synthetic-flagged input, not a blanket block on all ingress.
        host = JapaneseTestHost()
        stabilizer = get_japanese_final_stabilizer(host)
        stabilizer.set_accepting(True)

        with patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.get_japanese_continuity_assembler"
        ) as mock_get_assembler:
            stabilizer.ingest(1, "普通の発話です。", metadata={})

        mock_get_assembler.assert_called_once()

    # ------------------------------------------------------------------
    # 5. Timeout-safety test
    # ------------------------------------------------------------------
    def test_5_timeout_does_not_join_new_speaker(self) -> None:
        host = JapaneseTestHost()
        stabilizer = get_japanese_final_stabilizer(host)
        stabilizer.set_accepting(True)
        assembler = get_japanese_continuity_assembler(host)
        text_a = "こんにちは、今日は天気がいいですね。"
        # Must be >= 8 meaningful characters -- japanese_boundary_stabilizer's
        # flush_pending() drops shorter stop-flush tails as noise regardless
        # of speaker, a pre-existing, unrelated threshold.
        text_b = "はい、本当にそうですね。"

        stabilizer.ingest(1, text_a, metadata={})
        # Force the fallback timeout synchronously -- no real timer/sleep.
        assembler.try_execute_continuity_hold(
            assembler._flush_generation, "test_forced_timeout"
        )
        stabilizer.ingest(2, text_b, metadata={})
        # "stop_listening" also drains the boundary-stabilizer's own pending
        # buffer, not just the assembler's.
        assembler.flush("stop_listening")

        records = self._active_records()
        texts = [r.get("final_text") for r in records]
        speakers = [r.get("speaker") for r in records]
        self.assertGreaterEqual(len(records), 1, f"expected at least one record, got {records}")
        # Whatever record the timeout produced must not contain speaker 2's
        # words, and vice versa -- no cross-speaker join at the timeout point.
        for text in texts:
            self.assertFalse(
                text_a in text and text_b in text,
                f"timeout-committed record absorbed the new speaker: {text!r}",
            )
        joined = " ".join(texts)
        self.assertIn(text_a, joined)
        self.assertIn(text_b, joined)
        self.assertEqual(sorted(speakers), [1, 2])

    # ------------------------------------------------------------------
    # Edge case: same speaker, two rapidly adjacent finals must still
    # correctly merge/extend as before (confirm no over-correction).
    # ------------------------------------------------------------------
    def test_6_same_speaker_rapid_adjacent_finals_still_merge(self) -> None:
        host = JapaneseTestHost()
        stabilizer = get_japanese_final_stabilizer(host)
        stabilizer.set_accepting(True)
        assembler = get_japanese_continuity_assembler(host)
        frag_a = "これはテストで"
        frag_b = "とても大事な会議です。"
        stabilizer.ingest(1, frag_a, metadata={})
        stabilizer.ingest(1, frag_b, metadata={})
        assembler.flush("stop_listening")

        records = self._active_records()
        self.assertEqual(
            len(records), 1,
            f"same-speaker adjacent finals must merge into one record, got {records}",
        )
        self.assertEqual(records[0]["final_text"], frag_a + frag_b)
        self.assertEqual(records[0]["speaker"], 1)


if __name__ == "__main__":
    unittest.main()
