"""Japanese text the assembler holds past its 8 s limit still leaves on a timer.

WHAT WAS BROKEN (item 62, the owner's live meeting of 2026-10-01)
-----------------------------------------------------------------
The continuity buffer holds a sentence that has not ended. Its timer calls
`_execute_continuity_hold_locked`, which re-arms itself while the hold is
under `JAPANESE_CONTINUITY_MAX_HOLD_MS` (8 s). Past 8 s it asked
`_check_emergency_commit` once and returned. That check declines a short
buffer until it is "very old" (12 s) and any buffer whose last fragment is
under 2.5 s old -- and nothing asked it again, so the text waited for the
next final, or for Stop.

Run `...20261001-140533`: 「で」 at 14:14:45, 「画面アイテムは」 at 14:14:52,
the timer at 14:14:55.9 found an 8-character buffer held 10.7 s, declined,
and set no timer. The buffer sat until a final at 14:15:09.8 and reached the
screen at 14:15:13.6, 28 s after it was spoken.

WHAT THESE TESTS PIN
--------------------
Through the real assembler, stabilizer, lifecycle and ledger; only the clock
and the worker's timer heap are stand-ins, run in time order.

* a tail the stable layer holds after a timeout commit leaves on its 2 s timer
* a short buffer past 8 s leaves once it is 12 s old, with no further speech
* a lone short fragment followed by silence leaves too
* a buffer that is still growing is not cut by the re-armed timer
* speech after a silence keeps its order and is not held as noise (item 64)
* a final of only 、/。 joins a sentence being built but is never a line (item 64)
"""

import sys
import time as _time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription import japanese_boundary_stabilizer as jbs  # noqa: E402
from alpha.transcription import japanese_sentence_assembler as jsa  # noqa: E402
from alpha.transcription.canonical_identity_registry import reset_for_session  # noqa: E402
from alpha.transcription.utterance_lifecycle import reset_utterance_lifecycle  # noqa: E402
from alpha.utils import language_pipeline_worker as lpw  # noqa: E402

SESSION = "sess-item62"


class _Host:
    _live_session_id = SESSION
    _listen_language = "ja"
    _is_finalizing = False
    _is_stopping = False
    is_listening = True

    def __init__(self, clock):
        self.clock = clock
        self.published = []

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        self.published.append((self.clock[0], text))
        return True


class _Worker:
    """The worker's timer heap, run in time order by `_Case.advance`."""

    def __init__(self, clock):
        self.clock = clock
        self.flushes = []
        self.releases = []

    def schedule_flush(self, assembler, due_mono, generation, reason, *, task_type="flush"):
        self.flushes.append((due_mono, generation, reason, task_type))

    def schedule_stable_hold_release(self, assembler, due_mono, generation, reason):
        self.schedule_flush(assembler, due_mono, generation, reason, task_type="stable_hold_release")

    def cancel_flush(self, assembler):
        # as the real worker: only "flush" tasks
        self.flushes = [t for t in self.flushes if t[3] != "flush"]

    def schedule_boundary_release(self, assembler, due_mono, generation):
        self.releases.append((due_mono, generation))

    def schedule_quarantine_drop(self, assembler, drop_ms, *, skip_valid_short=False):
        self.flushes.append((self.clock[0] + drop_ms / 1000.0, 0, skip_valid_short, "quarantine_drop"))


class _Case(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.worker = _Worker(self.clock)
        fake_time = types.SimpleNamespace(
            monotonic=lambda: self.clock[0], time=_time.time, strftime=_time.strftime, sleep=lambda s: None
        )
        for p in (
            patch.object(jsa, "time", fake_time),
            patch.object(jbs, "time", fake_time),
            patch.object(lpw, "get_language_pipeline_worker", lambda: self.worker),
        ):
            p.start()
            self.addCleanup(p.stop)
        ctl.reset_for_run("run-item62")
        reset_for_session(SESSION)
        jbs.reset_boundary_stabilizer()
        self.host = _Host(self.clock)
        reset_utterance_lifecycle(self.host, SESSION)
        self.asm = jsa.JapaneseContinuityAssembler(self.host)
        self.asm.reset()
        self.t0 = self.clock[0]
        self.n = 0

    def tearDown(self):
        jbs.reset_boundary_stabilizer()
        ctl.reset_for_run("teardown-item62")
        reset_for_session("teardown-item62")

    def final(self, text, *, speech_final=True):
        self.n += 1
        self.asm.ingest(
            1,
            text,
            {"speech_final": speech_final, "source_raw_event_ids": [f"raw-{self.n}"], "channel_index": [0, 1]},
            "deepgram_final",
        )

    def advance(self, seconds):
        """Advance the clock, firing every timer that falls due, in order."""
        end = self.clock[0] + seconds
        while True:
            due = [t for t in self.worker.flushes if t[0] <= end] + [
                (d, g, "release", "release") for d, g in self.worker.releases if d <= end
            ]
            if not due:
                break
            task = min(due, key=lambda t: t[0])
            self.clock[0] = max(self.clock[0], task[0])
            if task[3] == "release":
                self.worker.releases.remove((task[0], task[1]))
                self.asm.try_release_boundary_pending(task[1])
            elif task[3] == "quarantine_drop":  # as the worker's _run_quarantine_drop
                self.worker.flushes.remove(task)
                self.asm._quarantine_drop_scheduled = False
                with self.asm._lock:
                    self.asm._drop_expired_quarantine_locked(skip_valid_short=task[2])
            else:
                self.worker.flushes.remove(task)
                self.asm.try_execute_continuity_hold(task[1], task[2])
        self.clock[0] = end

    def shown(self):
        return [(round(t - self.t0, 1), text) for t, text in self.host.published]


class ATailHeldAfterATimeoutCommitLeavesTest(_Case):
    """Item 63. The stable layer holds a committed text that ends mid-clause for
    2 s on its own timer. That timer was queued as a "flush", and the timeout
    commit that produced the text runs `cancel_flush` right after publishing,
    so the tail waited for the next final: 14:05:59 in the live meeting,
    「…ワンレコードオンリーが」 shown 7.5 s late."""

    def test_the_live_shape(self):
        self.final("ワンレコード運営になるはずなんで、", speech_final=False)
        self.advance(4.0)
        self.final("ワンレコードオンリーが", speech_final=False)
        self.advance(30.0)  # nobody speaks again
        shown = self.shown()
        self.assertTrue(
            any("ワンレコードオンリーが" in text for _t, text in shown),
            f"the held tail never left without another final: {shown}",
        )
        # its timers, in turn: the assembler's timeout commit at 8.4 s, the
        # stable layer's 2 s, the boundary stabilizer's 4 s
        when = min(t for t, text in shown if "ワンレコードオンリーが" in text)
        self.assertLessEqual(when, 14.5, f"left at {when} s: {shown}")


class TheRealWorkerKeepsTheTailTimerTest(unittest.TestCase):
    def test_cancel_flush_leaves_the_stable_hold_release_alone(self):
        worker = lpw.LanguagePipelineWorker()  # not started: inspect its heap
        owner = object()
        worker.schedule_flush(owner, 5.0, 1, "no_sentence_boundary")
        worker.schedule_stable_hold_release(owner, 6.0, 1, "stable_layer_hold_release:incomplete_tail")
        worker.cancel_flush(owner)
        self.assertEqual(
            [(t.task_type, t.reason) for t in worker._heap],
            [("stable_hold_release", "stable_layer_hold_release:incomplete_tail")],
        )


class AShortBufferPastItsHoldLeavesTest(_Case):
    def test_the_live_shape(self):
        self.final("で")
        self.advance(6.9)
        self.final("画面アイテムは")
        self.advance(30.0)  # nobody speaks again
        shown = self.shown()
        self.assertTrue(
            any("画面アイテムは" in text for _t, text in shown),
            f"the held text never left without another final: {shown}",
        )
        # very old at 12 s, then the stable layer's 3.5 s and the stabilizer's 4 s
        when = min(t for t, text in shown if "画面アイテムは" in text)
        self.assertLessEqual(when, 20.1, f"left at {when} s: {shown}")

    def test_a_lone_short_fragment(self):
        self.final("画面の")  # 3 characters, then silence
        self.advance(30.0)
        shown = self.shown()
        self.assertTrue(
            any("画面の" in text for _t, text in shown),
            f"the held text never left without another final: {shown}",
        )


class SpeechAfterASilenceIsNotQuarantinedTest(_Case):
    """Item 64. Live meeting of 2026-10-01: after 18 s without a commit,
    「きれいですっ」, 「画面の」 and 「画面に表示する」 were held as noise for 8 s,
    came back only with the next final, and reached the export after the
    later 「ラベル名は」. In 11 retained live runs the quarantine held 73
    fragments: 72 were speech, one was a bare 「、」, and since item 43 every
    one is committed anyway -- it only delayed and reordered them."""

    def test_the_words_keep_their_order_and_leave(self):
        self.final("分かりました。")
        self.advance(20.0)  # longer than JAPANESE_NOISE_QUARANTINE_SILENCE_S
        for gap, piece in ((0.0, "きれいですっ"), (1.6, "画面の"), (8.3, "画面に表示する"), (4.6, "ラベル名は")):
            self.advance(gap)
            self.final(piece)
        self.advance(40.0)  # nobody speaks again
        latest = self.shown()[-1][1] if self.shown() else ""
        self.assertIn("きれいですっ画面の画面に表示するラベル名は", latest, self.shown())


class PunctuationAloneIsNotALineTest(_Case):
    """Item 64. Without the quarantine a bare 「、」 would reach the screen as a
    line -- as it already did after a short pause: 4 export lines 「。」 in the
    retained runs."""

    def test_after_a_silence_and_after_a_short_pause(self):
        for silence in (20.0, 5.0):
            with self.subTest(silence=silence):
                self.setUp()
                self.final("分かりました。")
                self.advance(silence)
                self.final("、")
                self.advance(20.0)
                self.final("今日はありがとうございました。")
                self.advance(10.0)
                self.assertEqual(
                    [text for _t, text in self.shown()],
                    ["分かりました。", "今日はありがとうございました。"],
                )
                self.assertFalse(self.asm._assembler_commit_gate_failed)
                self.tearDown()

    def test_it_still_joins_a_sentence_being_built(self):
        self.final("今日は", speech_final=False)
        self.advance(0.5)
        self.final("、", speech_final=False)
        self.advance(0.5)
        self.final("ありがとうございました。")
        self.advance(10.0)
        self.assertEqual([text for _t, text in self.shown()], ["今日は、ありがとうございました。"])


class AGrowingBufferIsNotCutTest(_Case):
    def test_speech_still_arriving_keeps_its_sentence(self):
        self.final("で")
        for piece in ("画面の", "アイテムは", "きれいに", "表示されて"):
            self.advance(2.0)
            self.final(piece, speech_final=False)
        self.advance(1.0)
        self.final("いますね。")
        self.advance(10.0)
        texts = [text for _t, text in self.shown()]
        self.assertIn("で画面のアイテムはきれいに表示されていますね。", "".join(texts), texts)


if __name__ == "__main__":
    unittest.main()
