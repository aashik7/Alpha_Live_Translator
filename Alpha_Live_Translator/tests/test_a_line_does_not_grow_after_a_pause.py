"""Speech after a pause starts a new Japanese line instead of growing the last one.

WHAT WAS BROKEN (item 69, the owner's live meeting of 2026-10-01)
-----------------------------------------------------------------
The boundary stabilizer merges a text into the previous line when that line
ends mid-clause or the text starts with a particle (で, は, が ...). Nothing
limited how long ago the previous line was spoken. Before item 50 the fallback
speaker changed after a 4 s gap, so the speaker check blocked those merges by
accident; with one speaker, 「これはこうですね。」 spoken 21 s after
「…画面に表示する」 joined it, and one line grew for a minute. 8 of the 31 merges
in that meeting came after a pause over 4 s (Deepgram audio time), up to 42 s.

WHAT THESE TESTS PIN
--------------------
Through the real assembler, stabilizer, lifecycle and ledger, with Deepgram's
start/end times on the metadata:

* after a pause over 4 s the text is a new line
* after a short pause it still joins the line (the merge itself is unchanged)
* with no timing, or a clock that restarted (reconnect), behaviour is unchanged
* the pause is measured from where the text starts, not its newest fragment
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_a_held_buffer_keeps_its_timer import _Case as _TimerCase  # noqa: E402
from tests.test_a_held_line_leaves_on_time import _Case  # noqa: E402

FIRST = "この項目は画面に表示する"  # ends mid-clause: the stabilizer would merge into it
NEXT = "これはこうですね。"


class ALineDoesNotGrowAfterAPauseTest(_Case):
    def publish(self, text, start=None, end=None):
        self.n += 1
        metadata = {"source_raw_event_ids": [f"raw-{self.n}"], "channel_index": [0, 1]}
        if start is not None:
            metadata.update({"start_time": start, "end_time": end})
        with self.asm._lock:
            self.asm._route_stable_publish(1, text, metadata, "hold_timeout_sentence_end_punctuation", raw_fragments=[text])
        self.tick(4.1)

    def lines(self):
        return [t for t, _i, _r in self.ledger()]

    def test_after_a_long_pause_it_is_a_new_line(self):
        self.publish(FIRST, 10.0, 12.0)
        self.publish(NEXT, 33.0, 34.0)  # 21 s later, as live
        self.assertEqual(self.lines(), [FIRST, NEXT])

    def test_after_a_short_pause_it_still_joins(self):
        self.publish(FIRST, 10.0, 12.0)
        self.publish(NEXT, 13.5, 14.5)
        self.assertEqual(self.lines(), [FIRST + NEXT])

    def test_without_timing_nothing_changes(self):
        self.publish(FIRST)
        self.publish(NEXT)
        self.assertEqual(self.lines(), [FIRST + NEXT])

    def test_after_a_reconnect_reset_the_clock_nothing_changes(self):
        self.publish(FIRST, 900.0, 902.0)
        self.publish(NEXT, 1.0, 2.0)
        self.assertEqual(self.lines(), [FIRST + NEXT])


class ThePauseIsMeasuredFromWhereTheTextStartsTest(_TimerCase):
    """Through the assembler's buffer: the merge overwrites start_time with
    each newer fragment's, so the pause is taken from the buffer's first one."""

    def final_at(self, text, start, end, *, speech_final):
        self.n += 1
        self.asm.ingest(
            1,
            text,
            {"speech_final": speech_final, "start_time": start, "end_time": end,
             "source_raw_event_ids": [f"raw-{self.n}"], "channel_index": [0, 1]},
            "deepgram_final",
        )

    def test_a_sentence_resumed_after_three_seconds_still_joins(self):
        self.final_at("この項目は画面に表示する", 10.0, 12.0, speech_final=True)
        self.advance(15.0)
        self.final_at("これは", 15.0, 15.5, speech_final=False)  # 3 s after the line
        self.advance(1.5)
        self.final_at("こうですね。", 17.0, 18.0, speech_final=True)  # 5 s after it
        self.advance(15.0)
        self.assertEqual(self.shown()[-1][1], "この項目は画面に表示するこれはこうですね。", self.shown())


if __name__ == "__main__":
    unittest.main()
