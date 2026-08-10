"""Regression test for BUG_FIX_ROADMAP.md Batch 3, item 20b.

Confirmed root cause (not the earlier misdiagnosis in the original item
20b write-up, which measured the wrong evidence stream --
clean_active_transcript.jsonl never carried canonical_utterance_id by
design; corrected in CANONICAL_KEY_FIELDS_AUDIT.md):

japanese_sentence_assembler.py set metadata["revision_target_id"] to the
just-created record's OWN id unconditionally, whenever a record_id came
back from the commit -- for BOTH a genuine revision AND a brand-new
append. Confirmed against live evidence: all 36 canonical records in run
...20260809-174516 with applied_action == "append" had
revision_target_id == canonical_record_id (self-referential, meaningless
for a commit with nothing to revise).

duplicate_protection.py's _display_transcript_item treats any truthy
revision_target_id as an authoritative revision signal and forces
action="update" (BUG-G2's fix, working as designed -- the defect is
upstream, not there). For a same-speaker Japanese session this routes
every commit after the first through TranscriptStore.update_last_segment_
if_active, which overwrites the store's one-and-only row's TEXT while
never touching its canonical_utterance_id -- so every utterance after the
first stayed attached to the FIRST utterance's id forever. Measured live:
35 of 36 TRANSLATION_STORE_ID_MATCH_NOT_FOUND events in the same run.

The fix: only set revision_target_id when the commit is genuinely
revising a prior record (update_previous_requested /
final_revision_action == "revise_previous" in the two branches that set
it), not whenever a record_id merely exists.

This test drives the REAL JapaneseContinuityAssembler, the real
canonical ledger, the real utterance_lifecycle controller, and the real
duplicate_protection.py commit path -- through TranscriptStore -- for two
same-speaker, unrelated (non-continuation) Japanese sentences, and checks
what actually lands in the store.
"""

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
)
from alpha.transcription.duplicate_protection import DuplicateProtectionMixin  # noqa: E402
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    reset_utterance_lifecycle,
)
from alpha.transcription.japanese_sentence_assembler import (  # noqa: E402
    get_japanese_continuity_assembler,
)
from alpha.transcription.japanese_final_chunk_stabilizer import (  # noqa: E402
    get_japanese_final_stabilizer,
)


class _JapaneseStoreHost(DuplicateProtectionMixin):
    """Real assembler -> real duplicate_protection.py -> real TranscriptStore.

    Mirrors test_task2c_acceptance_gate.py's EnglishTestHost /
    JapaneseTestHost patterns (both already-established, reviewed harness
    shapes in this suite), combined: JapaneseTestHost's assembler/session
    setup plus EnglishTestHost's _publish_final_transcript_segment ->
    _display_transcript_item wiring and _on_store_segment_added/_updated
    tracking, since no existing host combines both.
    """

    def __init__(self, session_id: str = "sess-20b", run_id: str = "run-20b") -> None:
        self._live_session_id = session_id
        self._listen_language = "ja"
        self._is_finalizing = False
        self._is_stopping = False
        self.is_listening = True
        self.transcript_store = TranscriptStore()
        self.initial_verse_box = None
        self._frozen_ledger_error_count = 0
        self.added_calls = []
        self.updated_calls = []
        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        reset_utterance_lifecycle(self, session_id=session_id)

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        item = dict(queue_item or {})
        item.setdefault("speaker", speaker)
        item.setdefault("text", text)
        item["is_final"] = True
        if commit_reason:
            item["stabilizer_reason"] = commit_reason
        self._display_transcript_item(item)

    def _on_store_segment_added(self, speaker, text, **kwargs):
        self.added_calls.append({"speaker": speaker, "text": text, **kwargs})

    def _on_store_segment_updated(self, speaker, text, **kwargs):
        self.updated_calls.append({"speaker": speaker, "text": text, **kwargs})


def _commit_two_unrelated_sentences(host):
    stabilizer = get_japanese_final_stabilizer(host)
    stabilizer.set_accepting(True)
    assembler = get_japanese_continuity_assembler(host)

    text_a = "こんにちは、今日は天気がいいですね。"
    text_b = "会議の資料をもう一度確認しておきましょう。"

    stabilizer.ingest(1, text_a, metadata={})
    assembler.flush("test_boundary")
    stabilizer.ingest(1, text_b, metadata={})
    assembler.flush("stop_listening")

    return text_a, text_b


class TestAssemblerAppendNotTreatedAsRevision(unittest.TestCase):
    def test_two_unrelated_same_speaker_appends_become_two_store_rows(self):
        host = _JapaneseStoreHost()
        text_a, text_b = _commit_two_unrelated_sentences(host)

        # Sanity: the canonical ledger itself must show two independent
        # append records -- if this fails, the test setup (not the fix)
        # is wrong and everything below is moot.
        records = ctl.get_active_records()
        applied_actions = [r.get("applied_action") for r in records]
        self.assertEqual(
            applied_actions,
            ["append", "append"],
            f"test setup must produce two independent appends, got: {records}",
        )

        segments = host.transcript_store.get_all()
        self.assertEqual(
            len(segments),
            2,
            "two unrelated appends must become two TranscriptStore rows, "
            f"not overwrite one another -- got: {[s.text for s in segments]}",
        )
        self.assertEqual(segments[0].text, text_a)
        self.assertEqual(segments[1].text, text_b)
        self.assertTrue(segments[0].canonical_utterance_id)
        self.assertTrue(segments[1].canonical_utterance_id)
        self.assertNotEqual(
            segments[0].canonical_utterance_id,
            segments[1].canonical_utterance_id,
            "two distinct utterances must never share a canonical_utterance_id",
        )

    def test_translation_lookup_finds_the_second_utterance_by_id(self):
        # The exact live symptom: TRANSLATION_STORE_ID_MATCH_NOT_FOUND for
        # every utterance after the first, because it stayed permanently
        # attached to the first utterance's id.
        host = _JapaneseStoreHost()
        text_a, text_b = _commit_two_unrelated_sentences(host)

        second_id = host.transcript_store.get_all()[1].canonical_utterance_id
        host.transcript_store.add_translation(
            original_text=text_b,
            translated_text="Let's double-check the meeting materials.",
            canonical_utterance_id=second_id,
        )

        segments = host.transcript_store.get_all()
        self.assertEqual(
            segments[1].translated_text,
            "Let's double-check the meeting materials.",
            "translation submitted with the second utterance's real id "
            "must land on the second utterance's row",
        )
        self.assertIsNone(
            segments[0].translated_text,
            "the first utterance's row must not be the one that absorbed it",
        )

    def test_second_append_does_not_route_through_the_update_hook(self):
        host = _JapaneseStoreHost()
        _commit_two_unrelated_sentences(host)

        self.assertEqual(
            len(host.added_calls),
            2,
            "two independent appends must both reach _on_store_segment_added, "
            f"not _on_store_segment_updated -- added={len(host.added_calls)}, "
            f"updated={len(host.updated_calls)}",
        )
        self.assertEqual(len(host.updated_calls), 0)


if __name__ == "__main__":
    unittest.main()
