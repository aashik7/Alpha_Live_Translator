"""Speech the pipeline is still holding stays on screen as a pending line.

WHAT WAS BROKEN (PENDING_TASKS.md section 0g, the owner's meetings of 2026-09-28)
------------------------------------------------------------------------------
The grey "⏳" line is Deepgram's interim. When a speaker pauses, interims stop,
and the ghost watchdog (`_check_interim_ghost_watchdog`) removes any interim not
refreshed for `INTERIM_GHOST_TTL_MS` (6 s). But the Japanese pipeline was still
HOLDING those words -- the assembler's buffer, the stable-layer hold, the
boundary stabilizer -- for up to 8 s more, so the window showed nothing until
the line committed. `...140417` at 14:09:40-14:10:14: the interim was wiped at
14:09:49, the line held, wiped again at 14:10:01.9, shown at 14:10:14.4. Four
such 30-34 s blank stretches in that meeting; 23 watchdog wipes. The owner's
report: "seemed to disconnect mid-way".

The watchdog is right that an orphaned interim must go (a permanent ghost line
is impossible by design, and `test_interim_ghost_line.py` pins that). It was
wrong that nothing was pending: the words were pending, one layer down.

WHAT THESE TESTS PIN
--------------------
* the assembler reports every word it holds uncommitted, oldest first:
  stabilizer, stable-layer hold, buffer
* while anything is held, a stale interim is REPLACED by the held text, not
  wiped; it follows the held text as it shrinks and goes the moment nothing is
  held any more
* live speech takes the line back at once
* with nothing held, the watchdog still removes a stale interim, unchanged
"""

import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import INTERIM_GHOST_TTL_MS  # noqa: E402
from alpha.transcription import japanese_boundary_stabilizer as jbs  # noqa: E402
from alpha.transcription import japanese_sentence_assembler as jsa  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402

_BOUND = (
    "_apply_final_interim_comparison",
    "_clear_interim_tail",
    "_check_interim_ghost_watchdog",
    "_handle_interim_transcript_ui",
)


class _Assembler:
    """The one method the UI reads, over a text the test controls."""

    def __init__(self):
        self.held = ""

    def get_held_text_nonblocking(self):
        return self.held


class _Host:
    def __init__(self):
        self._latest_interim_text = ""
        self._latest_interim_speaker = 1
        self._latest_interim_utterance_id = ""
        self._latest_interim_committed = False
        self._last_interim_ui_at = 0.0
        self.renders = []
        self.logs = []
        self._jp_continuity_assembler = _Assembler()

    def _remove_interim_line_from_display(self):
        pass

    def _update_interim_line_only(self):
        self.renders.append(self._latest_interim_text)

    def _interim_log(self, message, data):
        self.logs.append((message, data))

    def _normalize_compare(self, text):
        return " ".join((text or "").strip().lower().split())

    def _is_japanese_manual_mode(self):
        return True

    def make_stale(self):
        self._last_interim_ui_at = time.perf_counter() - (INTERIM_GHOST_TTL_MS + 500) / 1000.0


for _name in _BOUND:
    setattr(_Host, _name, getattr(AlphaApp, _name))
for _name in ("_pipeline_held_text",):
    if hasattr(AlphaApp, _name):
        setattr(_Host, _name, getattr(AlphaApp, _name))


class HeldSpeechIsShownNotWipedTest(unittest.TestCase):
    def test_a_stale_interim_becomes_the_held_text(self):
        host = _Host()
        host._latest_interim_text = "なので、デザインドキュメントを"
        host._jp_continuity_assembler.held = "なので、デザインドキュメントを直さないと"
        host.make_stale()
        host._check_interim_ghost_watchdog()
        self.assertEqual(
            host._latest_interim_text,
            "なので、デザインドキュメントを直さないと",
            "the words the pipeline still holds must stay on screen as pending",
        )
        self.assertEqual(host.renders[-1], "なので、デザインドキュメントを直さないと")

    def test_it_follows_the_held_text_and_goes_when_nothing_is_held(self):
        host = _Host()
        host._latest_interim_text = "一つ目。二つ目"
        host._jp_continuity_assembler.held = "一つ目。二つ目"
        host.make_stale()
        host._check_interim_ghost_watchdog()
        host._jp_continuity_assembler.held = "二つ目"  # the first sentence committed
        host._check_interim_ghost_watchdog()
        self.assertEqual(host._latest_interim_text, "二つ目")
        host._jp_continuity_assembler.held = ""  # everything committed
        host._check_interim_ghost_watchdog()
        self.assertEqual(host._latest_interim_text, "", "no ghost once nothing is held")
        self.assertEqual(
            getattr(host, "_watchdog_orphaned_interim_text", ""),
            "",
            "words that COMMITTED are not an orphan: stashing them would hand "
            "already-exported text to the Stop-time recovery path",
        )
        self.assertFalse(
            [m for m, _ in host.logs if m == "[INTERIM] ghost watchdog cleared"],
            "and it is not a ghost either",
        )

    def test_live_speech_takes_the_line_back(self):
        host = _Host()
        host._latest_interim_text = "保留中の文"
        host._jp_continuity_assembler.held = "保留中の文"
        host.make_stale()
        host._check_interim_ghost_watchdog()
        host._handle_interim_transcript_ui(1, "新しい発言が始まった", metadata={})
        host._jp_continuity_assembler.held = "まだ保留中"
        host._check_interim_ghost_watchdog()  # fresh interim: the watchdog must leave it
        self.assertEqual(host._latest_interim_text, "新しい発言が始まった")

    def test_nothing_held_still_clears_a_ghost(self):
        host = _Host()
        host._latest_interim_text = "orphaned ghost line"
        host.make_stale()
        host._check_interim_ghost_watchdog()
        self.assertEqual(host._latest_interim_text, "")


class TheAssemblerReportsWhatItHoldsTest(unittest.TestCase):
    def setUp(self):
        jbs.reset_boundary_stabilizer()
        self.addCleanup(jbs.reset_boundary_stabilizer)

        class _H:
            _listen_language = "ja"
            _is_finalizing = False
            _is_stopping = False
            is_listening = True

            def _publish_final_transcript_segment(self, *a, **k):
                return True

        self.asm = jsa.JapaneseContinuityAssembler(_H())

    def test_oldest_first_across_all_three_holds(self):
        stab = jbs.get_boundary_stabilizer()
        stab.process("でやってみますか。", commit_reason="t", speaker=1)  # held by the stabilizer
        self.assertTrue(stab.pending_text, "fixture: the stabilizer holds a line")
        self.asm._stable_hold_pending = {"text": "アラブネームはね"}
        self.asm._buffer = {"text": "モディファイしたり"}
        self.assertEqual(
            self.asm.get_held_text_nonblocking(),
            "でやってみますか。アラブネームはねモディファイしたり",
        )

    def test_nothing_held_is_empty(self):
        self.assertEqual(self.asm.get_held_text_nonblocking(), "")


if __name__ == "__main__":
    unittest.main()
