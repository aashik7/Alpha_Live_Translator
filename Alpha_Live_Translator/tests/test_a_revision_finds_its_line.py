"""A revision of a Japanese line finds that line, whatever metadata it carries.

WHAT WAS BROKEN (item 65, the owner's live meeting of 2026-10-01)
-----------------------------------------------------------------
The identity registry keys a line on (session, channel, utterance id).
Deepgram's finals carry `channel_index` [0, 1]; a fragment that came back out
of noise quarantine carried only {"quarantine_recovered": True}. When text
built from it revised the line before, the assembler proposed the revision
under channel "" -- the registry found nothing (`missing_exact_revision_target`),
and the assembler committed the revision as a NEW line. Two of those in a row
put 「アグアイテムフィズ系なって。分かりました。…」 in the export three times,
each version longer, and left the translations of the copies unmatched
(`TRANSLATION_STORE_ID_MATCH_NOT_FOUND` x2).

WHAT THESE TESTS PIN
--------------------
Through the real assembler, stabilizer, lifecycle commit path, identity
registry and ledger:

* a revision without channel metadata revises the line in place
* a new line without channel metadata, and a revision of it that has some,
  also stay one line
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_a_held_line_leaves_on_time import _Case  # noqa: E402

CHANNEL = [0, 1]  # deepgram_client passes data.get("channel_index") through


class ARevisionFindsItsLineTest(_Case):
    def publish(self, text, metadata, reason="hold_timeout_sentence_end_punctuation"):
        with self.asm._lock:
            self.asm._route_stable_publish(1, text, metadata, reason, raw_fragments=[text])

    def lines(self):
        return [t for t, _i, _r in self.ledger()]

    def test_the_live_shape(self):
        self.publish("アグアイテムフィズ系なって。分かりました。", {"source_raw_event_ids": ["raw-1"], "channel_index": CHANNEL})
        self.tick(4.1)
        self.publish(
            "で画面アイテムはきれいですっ",
            {"source_raw_event_ids": ["raw-2"], "channel_index": CHANNEL},
            "safe_hold_timeout_incomplete_but_stable",
        )
        self.tick(4.1)
        # text built from a fragment that came back out of quarantine
        self.publish("これはこうですね。", {"quarantine_recovered": True}, "hold_timeout_safe_prefix")
        self.tick(4.1)
        self.publish(
            "でテーブル名はテーブルの論理名、分かりました?はい、まだ2の。",
            {"source_raw_event_ids": ["raw-4"], "channel_index": CHANNEL},
        )
        self.tick(4.1)
        self.assertEqual(
            self.lines(),
            ["アグアイテムフィズ系なって。分かりました。で画面アイテムはきれいですっこれはこうですね。"
             "でテーブル名はテーブルの論理名、分かりました?はい、まだ2の。"],
        )

    def test_a_line_committed_without_a_channel(self):
        self.publish("分かりました。", {"quarantine_recovered": True})
        self.tick(4.1)
        self.assertEqual(self.lines(), ["分かりました。"], "fixture: a line of its own")
        self.publish("でテーブル名は論理名です。", {"source_raw_event_ids": ["raw-2"], "channel_index": CHANNEL})
        self.tick(4.1)
        self.assertEqual(self.lines(), ["分かりました。でテーブル名は論理名です。"])


if __name__ == "__main__":
    unittest.main()
