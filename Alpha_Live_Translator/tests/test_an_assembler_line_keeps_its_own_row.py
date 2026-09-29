"""A line the Japanese assembler committed keeps its own row, id and translation.

WHAT WAS BROKEN (PENDING_TASKS.md section 0d, the owner's meeting `...140417`)
----------------------------------------------------------------------------
8 of 225 lines got no translation during the meeting; they were translated only
by the Stop-time reconciliation, 81 s to 32 minutes after they committed. Each
one is a `TRANSLATION_STORE_ID_MATCH_NOT_FOUND` about a second after its commit,
and all 15 of those misses in the run name the SAME id, `jpm-utt-9ee2ca2ccbc2`,
for different sentences.

The cause is a second boundary authority in the UI. The assembler (with its
boundary stabilizer) had already decided "new line" and written a new ledger
record -- `stable-218` "あら、どれを直さないと駄目だ。" -- when
`_commit_transcript_item_to_store` ran its own manual-mode "cross-segment
merge", glued the line onto the pane's previous row ("デザインドキュメントを")
and relabelled the item with a session-wide `jpm-utt` id that it never re-mints
while assembler items keep arriving. So:

* the pane showed ONE row where the export (built from the ledger) has TWO --
  in one case the glued row read "…さんにまるさんが…さんにとあとはバッチ?か";
* the row kept the previous line's id, the translation was keyed on the jpm
  id, `add_translation` found no row with it and dropped it.

The assembler owns Japanese boundaries (REPAIR_PLAN Phase 2: one authority);
its own merge (the stabilizer's `merge_with_previous`) revises the ledger AND
the pane together. The UI merge is for manual-mode items that never passed
through it, and stays for those.

WHAT THESE TESTS PIN
--------------------
Through the real `_commit_transcript_item_to_store` on the same method-borrowed
host `test_task2g_acceptance_gate.py` uses:

* an assembler line that "continues" the previous row is its own row, with its
  own canonical id -- the pane matches the export
* its translation lands on it
* a manual-mode line without the assembler still merges, as before
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_task2g_acceptance_gate import ManualModeCommitHost  # noqa: E402

PREVIOUS = "本日の会議の資料を"  # ends on a particle: the UI's continuation heuristic fires
CURRENT = "確認してください"


class AnAssemblerLineIsItsOwnRowTest(unittest.TestCase):
    def commit_assembler(self, host, text, uid):
        item = {
            "speaker": 1,
            "text": text,
            "is_final": True,
            "speech_final": True,
            "_jp_cleaned": True,
            "_jp_continuity_assembler": True,
            # What the assembler's queue item carries in production.
            "canonical_utterance_id": uid,
            "source_version": 1,
        }
        host._commit_transcript_item_to_store(item)
        return item

    def test_the_pane_keeps_the_ledgers_two_lines(self):
        host = ManualModeCommitHost()
        self.commit_assembler(host, PREVIOUS, "jp-utt-aaaaaaaaaaaa")
        self.commit_assembler(host, CURRENT, "jp-utt-bbbbbbbbbbbb")
        segments = host.transcript_store.get_all()
        self.assertEqual(
            [s.text for s in segments],
            [PREVIOUS, CURRENT],
            "the assembler wrote two ledger records; the pane must show the same two lines",
        )
        self.assertEqual(
            [s.canonical_utterance_id for s in segments],
            ["jp-utt-aaaaaaaaaaaa", "jp-utt-bbbbbbbbbbbb"],
        )

    def test_the_translation_lands_on_its_line(self):
        host = ManualModeCommitHost()
        self.commit_assembler(host, PREVIOUS, "jp-utt-aaaaaaaaaaaa")
        item = self.commit_assembler(host, CURRENT, "jp-utt-bbbbbbbbbbbb")
        host.transcript_store.add_translation(
            CURRENT,
            "Please check it.",
            speaker=1,
            canonical_utterance_id=str(item.get("canonical_utterance_id") or ""),
        )
        by_text = {s.text: s.translated_text for s in host.transcript_store.get_all()}
        self.assertEqual(
            by_text.get(CURRENT),
            "Please check it.",
            "keyed on the line's own id, the translation must find its row",
        )


class AManualModeLineStillMergesTest(unittest.TestCase):
    def test_without_the_assembler_the_ui_merge_still_works(self):
        host = ManualModeCommitHost()
        host.commit(1, PREVIOUS, jp_continuity_assembler=False)
        host.commit(1, CURRENT, jp_continuity_assembler=False)
        self.assertEqual([s.text for s in host.transcript_store.get_all()], [PREVIOUS + CURRENT])


if __name__ == "__main__":
    unittest.main()
