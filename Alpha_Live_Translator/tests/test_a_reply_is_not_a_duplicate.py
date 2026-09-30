"""A reply that repeats the end or start of the line before it is kept.

WHAT WAS BROKEN (item 55, found reviewing item 50 on 2026-09-30)
-----------------------------------------------------------------
The boundary stabilizer drops a line that equals the start or the end of the
previous one ("duplicate continuation"), meant for a re-send of that line. It
was gated only on the speaker being the same. Without diarization the speaker
was a guess that changed after any 4 s pause, which by accident let a later
reply through; item 50 made every line speaker 1, so every such reply was
dropped however late it came:

    今日は本当にありがとうございました。  ->  (6 s later) ありがとうございました。  dropped
    はい、分かりました。では次に進みます。 ->  はい。                             dropped

The retained Japanese runs hold 9 pairs of consecutive finals of that shape,
3.8-10.4 s apart -- all speech. Deepgram's finals never cover the same audio
twice, so only a repeat arriving with the line (within
`DUPLICATE_RESEND_WINDOW_S`) is a re-send.

Driven through the real assembler, stabilizer, lifecycle and ledger (the
harness of test_a_held_line_leaves_on_time).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_a_held_line_leaves_on_time import _Case  # noqa: E402

REPLIES = [
    ("今日は本当にありがとうございました。", "ありがとうございました。"),
    ("はい、分かりました。では次に進みます。", "はい。"),
    ("資料は明日までに送ります。", "送ります。"),
]


class AReplyIsKeptTest(_Case):
    def test_a_reply_after_a_pause(self):
        for first, reply in REPLIES:
            with self.subTest(first=first):
                self.setUp()
                self.commit(first)
                self.tick(4.1)
                self.clock[0] += 6.0
                self.commit(reply)
                self.tick(4.1)
                # A short reply may join the line before (a separate rule);
                # what matters is that its words are in the export, once.
                self.assertEqual(
                    "".join(t for t, _i, _r in self.ledger()),
                    first + reply,
                    "the reply was dropped as a duplicate of the line before",
                )

    def test_a_resend_with_the_line_is_still_dropped(self):
        line = "今日は本当にありがとうございました。"
        self.commit(line)
        self.commit(line)  # the same moment: a re-send, not speech
        self.assertEqual([t for t, _i, _r in self.ledger()], [line])


if __name__ == "__main__":
    unittest.main()
