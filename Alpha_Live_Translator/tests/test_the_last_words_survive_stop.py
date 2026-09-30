"""The words in progress when the owner presses Stop reach the export.

WHAT WAS BROKEN (item 58, 2026-09-30)
-------------------------------------
Two rules threw away the end of a meeting:

* `SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA`: the assembler's buffer at Stop,
  if it had no sentence end, was committed as a "suppress candidate" -- kept
  in an evidence file, left out of the ledger, the export and the translation.
* the boundary stabilizer's Stop flush dropped a held line under 8 Japanese
  characters.

Measured on the retained runs: of the 12 texts they dropped, 10 are nowhere in
the export -- 「こそ二、三十年間デフレだった状態からなんでここ突然五年で…」
(104 characters), 「争力が高まるし何なら…凄くデータと-」, 「さようなら。」,
「今の日本で一番大切なのは、」. The earlier reason, "a dangling fragment is
noise at the edge of a finished session", did not survive the measurement.

Driven through the real assembler, stabilizer, lifecycle and ledger.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_a_held_line_leaves_on_time import _Case  # noqa: E402


class TheLastWordsSurviveStopTest(_Case):
    def texts(self):
        return [t for t, _i, _r in self.ledger()]

    def test_the_sentence_in_progress(self):
        self.asm.ingest(
            1,
            "今の日本で一番大切なのは、",
            {"speech_final": False, "source_raw_event_ids": ["raw-a"]},
            "deepgram_final",
        )
        self.assertEqual(self.texts(), [], "fixture: still in the buffer")
        self.asm.flush("stop_listening")
        self.assertEqual(
            "".join(self.texts()).rstrip("、。"),
            "今の日本で一番大切なのは",
            "Stop left the sentence in progress out of the export",
        )

    def test_a_short_held_line(self):
        self.commit("でどうぞ。")  # held: leading particle, 4 characters
        self.assertEqual(self.texts(), [], "fixture: held by the stabilizer")
        self.asm.flush("stop_listening")
        self.assertEqual(self.texts(), ["でどうぞ。"], "Stop dropped a short held line")


if __name__ == "__main__":
    unittest.main()
