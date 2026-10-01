"""A line the boundary stabilizer holds leaves on time, in order, as itself.

WHAT WAS BROKEN (PENDING_TASKS.md section 0b, the owner's meetings of 2026-09-28)
------------------------------------------------------------------------------
`JapaneseBoundaryStabilizer` holds a line that starts with a particle or ends
mid-clause, for at most `BOUNDARY_STABILIZER_HOLD_MS_MAX` (4 s). Nothing enforced
that: the hold ended only when the NEXT final arrived. Measured on the retained
decision logs, held lines waited p50 8 s and up to 47 s (163 s once, until
Stop); in `...140417` 115 of 140 waited longer than the 4 s allowed. While a
line was held the window showed nothing -- the "seemed to disconnect" report.

Three more defects in the same hold, found measuring it:

* A young hold (same speaker, under 4 s) that the next text did not merge into
  stayed held while the next text went out AHEAD of it: the transcript showed
  the newer line first -- 4, 4 and 9 times in the three meetings.
* A speaker change and a timed-out hold released the held line but then held
  the NEW text unconditionally, so a complete sentence waited for yet another
  final.
* A held line was published by whichever later call released it, with that
  call's speaker, commit reason and raw-event lineage.

WHAT THESE TESTS PIN
--------------------
Driven through the real assembler, the real stabilizer, the real lifecycle
commit path and the real ledger; only the clock and the worker's heap are
stand-ins (so the test does not sleep 4 s, and no background thread races it).

* a held line is released by its timer at 4 s, not before, and not later
* the timer is its own task type: an assembler flush cannot cancel it
* an older held line goes out before a newer one, never after
* a speaker change or a timed-out hold does not hold the new sentence
* a released line keeps its own lineage and commit reason, also at Stop
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

SESSION = "sess-section0b"
HELD = "でやってみますか。"  # held: leading particle で (a real held line of ...101440)
LATER = "今日はありがとうございました。"


class _Host:
    _live_session_id = SESSION
    _listen_language = "ja"
    _is_finalizing = False
    _is_stopping = False
    is_listening = True

    def __init__(self):
        self.published = []

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        self.published.append(text)
        return True


class _Worker:
    """The worker's scheduling surface, recorded instead of run."""

    def __init__(self):
        self.boundary_releases = []

    def schedule_boundary_release(self, assembler, due_mono, generation):
        self.boundary_releases.append((due_mono, generation))

    def schedule_flush(self, *a, **k):
        pass

    def schedule_stable_hold_release(self, *a, **k):
        pass

    def cancel_flush(self, *a, **k):
        pass

    def schedule_quarantine_drop(self, *a, **k):
        pass


class _Case(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.worker = _Worker()
        fake_time = types.SimpleNamespace(
            monotonic=lambda: self.clock[0], time=_time.time, strftime=_time.strftime
        )
        for p in (
            patch.object(jbs, "time", fake_time),
            patch.object(lpw, "get_language_pipeline_worker", lambda: self.worker),
        ):
            p.start()
            self.addCleanup(p.stop)
        ctl.reset_for_run("run-section0b")
        reset_for_session(SESSION)
        jbs.reset_boundary_stabilizer()
        self.host = _Host()
        reset_utterance_lifecycle(self.host, SESSION)
        self.asm = jsa.JapaneseContinuityAssembler(self.host)
        self.asm.reset()
        self.n = 0

    def tearDown(self):
        jbs.reset_boundary_stabilizer()
        ctl.reset_for_run("teardown-section0b")
        reset_for_session("teardown-section0b")

    def commit(self, text, *, speaker=1, reason="hold_timeout_sentence_end_punctuation", **kw):
        """One sentence leaving the assembler's buffer, as `_flush_locked` hands it on."""
        self.n += 1
        with self.asm._lock:
            self.asm._route_stable_publish(
                speaker,
                text,
                {"source_raw_event_ids": [f"raw-{self.n}"]},
                reason,
                raw_fragments=[text],
                **kw,
            )

    def tick(self, seconds):
        self.clock[0] += seconds
        return self.asm.try_release_boundary_pending(self.asm._boundary_release_generation)

    def ledger(self):
        return [
            (str(r.get("final_text") or ""), list(r.get("source_raw_event_ids") or []), r.get("commit_reason"))
            for r in ctl.get_active_records()
        ]


class AHeldLineLeavesOnItsTimerTest(_Case):
    def test_released_at_four_seconds_with_no_further_speech(self):
        self.commit(HELD)
        self.assertEqual(self.host.published, [], "fixture: the stabilizer holds this line")
        self.assertEqual(
            self.worker.boundary_releases[-1][0],
            1004.0,
            "the release is scheduled for the hold's own deadline",
        )
        self.tick(3.9)
        self.assertEqual(self.host.published, [], "not before the 4 s hold is over")
        self.tick(0.2)
        self.assertEqual(self.host.published, [HELD], "released by the timer, no next final needed")

    def test_the_released_line_keeps_its_own_lineage_and_reason(self):
        self.commit(HELD, reason="safe_chunk_boundary_commit")
        self.tick(4.1)
        self.assertEqual(self.ledger(), [(HELD, ["raw-1"], "safe_chunk_boundary_commit")])

    def test_a_stale_timer_does_nothing(self):
        self.commit(HELD)
        stale = self.asm._boundary_release_generation
        self.commit(LATER)  # releases HELD first, re-arms (nothing held now)
        self.clock[0] += 10
        self.asm.try_release_boundary_pending(stale)
        self.assertEqual(self.host.published, [HELD, LATER], "each line exactly once")


class AHeldRevisionStaysARevisionTest(_Case):
    """Item 54 (2026-09-29), found replaying the owner's meeting of that day.

    「。ここでしっけ…」 starts with punctuation, so the stable layer merges it
    into the line before and marks the result a revision of that line. The
    merged text starts with the particle か, so the boundary stabilizer holds
    it -- and the release (section 0b) republished it with
    `stable_layer_update_previous=False`: the export kept BOTH lines, 「…よね。」
    and 「…よね。ここでしっけ…」, and the revised line's translation found no
    row (`TRANSLATION_STORE_ID_MATCH_NOT_FOUND`)."""

    FIRST = "かリザ、前、タレントデートの不具合なんかありましたよね。"
    REST = "。ここでしっけ価格登録かどっかで。"

    def test_released_by_the_timer(self):
        self.commit(self.FIRST)
        self.tick(4.1)
        self.assertEqual([t for t, _i, _r in self.ledger()], [self.FIRST], "fixture")
        self.commit(self.REST)
        self.tick(4.1)
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [self.FIRST + "ここでしっけ価格登録かどっかで。"],
            "the held revision became a second line",
        )

    def test_released_by_a_newer_line(self):
        self.commit(self.FIRST)
        self.tick(4.1)
        self.commit(self.REST)
        self.commit(LATER)  # releases the held revision first
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [self.FIRST + "ここでしっけ価格登録かどっかで。", LATER],
        )

    # Added 2026-09-30 by the review of item 54: the timer and a newer line
    # were fixed, three more ways out of the hold were not.

    def test_released_at_stop(self):
        self.commit(self.FIRST)
        self.tick(4.1)
        self.commit(self.REST)
        self.asm.flush("stop_listening")
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [self.FIRST + "ここでしっけ価格登録かどっかで。"],
        )

    def test_merged_with_the_next_final(self):
        nxt = "で登録してから確認する流れになっていたと思います。"
        self.commit(self.FIRST)
        self.tick(4.1)
        self.commit(self.REST)
        self.clock[0] += 1.0
        self.commit(nxt)  # the stabilizer merges it into the held revision
        self.tick(4.1)
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [self.FIRST + "ここでしっけ価格登録かどっかで。" + nxt],
        )

    def test_a_second_punctuation_start_final(self):
        self.commit(self.FIRST)
        self.tick(4.1)
        self.commit(self.REST)
        self.clock[0] += 1.0
        self.commit("。それで登録の画面を確認しました。")
        self.tick(4.1)
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [self.FIRST + "ここでしっけ価格登録かどっかで。それで登録の画面を確認しました。"],
        )


class APunctuationStartFinalJoinsTheNewestLineTest(_Case):
    """Item 54's review, 2026-09-30.

    The stable layer joins a final that starts with 、/。 onto
    `_last_stable_commit`. While the boundary stabilizer held a newer line,
    that was the wrong line: the fragment's words went in front of the held
    line's (fails on the code before item 49 too). And a line that had ended
    mid-clause was merged in again by the stabilizer, which did not know the
    text already held it: 「資料の件については資料の件については。…」. Before
    item 49 the old prefix collapse cut that repeat at commit, so it never
    showed; item 49 stopped that collapse cutting real words, so now it would."""

    A = "今日はよろしくお願いします。"
    MID = "資料の件については"  # ends mid-clause: the stabilizer holds it

    def setUp(self):
        super().setUp()
        self.commit(self.A)
        self.tick(4.1)
        self.commit(self.MID, force_release=True)  # past the stable layer's own hold
        self.assertEqual([t for t, _i, _r in self.ledger()], [self.A], "fixture: held")

    def test_while_a_newer_line_is_held(self):
        self.clock[0] += 1.0
        self.commit("。はい、分かりました。")
        self.tick(4.1)
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [self.A, self.MID + "。はい、分かりました。"],
        )

    def test_after_a_line_that_ended_mid_clause(self):
        self.tick(4.1)  # the held line goes out on its own
        self.clock[0] += 1.0
        self.commit("。はい、分かりました。")
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [self.A, self.MID + "。はい、分かりました。"],
            "the line before was merged in a second time",
        )


class AShortAnswerAfterALongLineIsKeptTest(_Case):
    """Review of item 54, also on the code before it: the text a
    punctuation-start final makes by joining the line before was scored as a
    duplicate of that line -- 38 characters of it against a 2-character はい
    scored 0.95 -- and dropped."""

    def test_hai_after_a_long_line(self):
        long_line = "本日の定例会議では来月のリリース計画と品質保証の進め方について詳しく確認しました。"
        self.commit(long_line)
        self.tick(4.1)
        self.clock[0] += 1.0
        self.commit("。はい。")
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [long_line + "はい。"],
            "the はい was dropped as a duplicate of the line it joined",
        )

    def test_only_punctuation_adds_nothing(self):
        self.commit(LATER)
        self.tick(4.1)
        self.commit("。")
        self.assertEqual([t for t, _i, _r in self.ledger()], [LATER])


class ACommittedLineIsNotRewrittenTest(_Case):
    """Review of item 51, 2026-09-30, also on the code before it: joining
    「。はい、そうです。」 onto a committed 「…けど、」 changed that line's 、 to
    。, the result no longer started with the committed line, and it was
    committed a second time: the export held 「…けど、」 AND 「…けど。はい…」."""

    def test_a_line_ending_in_a_comma(self):
        line = "今日の会議では予算について話しましたけど、"
        self.commit(line)
        self.tick(4.1)
        self.clock[0] += 1.0
        self.commit("。はい、そうです。")
        self.tick(4.1)
        self.assertEqual(
            [t for t, _i, _r in self.ledger()],
            [line + "はい、そうです。"],
        )


class TheTimerSurvivesAnAssemblerFlushTest(unittest.TestCase):
    def test_cancel_flush_leaves_the_boundary_release_alone(self):
        worker = lpw.LanguagePipelineWorker()
        owner = object()
        worker.schedule_flush(owner, _time.monotonic() + 60, 1, "hold_tick")
        worker.schedule_boundary_release(owner, _time.monotonic() + 60, 7)
        worker.cancel_flush(owner)
        self.assertEqual(
            [(t.task_type, t.generation) for t in worker._heap],
            [("boundary_release", 7)],
            "`_cancel_timer` runs on every assembler flush; it must not take the release with it",
        )


class TheRealWorkerThreadRunsTheReleaseTest(unittest.TestCase):
    """Added 2026-09-29 (review of 0b): every test above records the schedule
    instead of running it, so nothing proved the worker's own thread dispatches
    the task, or retries it when the assembler lock is busy."""

    def test_dispatched_on_the_worker_thread_and_retried_while_busy(self):
        import threading

        worker = lpw.LanguagePipelineWorker()
        self.addCleanup(worker.stop_and_join, 1.0)
        calls = []
        done = threading.Event()

        class _Owner:
            def try_release_boundary_pending(self, generation):
                calls.append((generation, threading.current_thread().name))
                if len(calls) == 1:
                    return False  # the assembler lock was busy
                done.set()
                return True

        worker.start()
        worker.schedule_boundary_release(_Owner(), _time.monotonic(), 7)
        self.assertTrue(done.wait(3.0), f"the release never ran again after a busy lock: {calls}")
        self.assertEqual(
            calls,
            [(7, "LanguagePipelineWorker"), (7, "LanguagePipelineWorker")],
        )


class TheOlderLineGoesOutFirstTest(_Case):
    def test_a_newer_line_never_overtakes_a_held_one(self):
        self.commit(HELD)
        self.clock[0] += 1.0
        self.commit(LATER)
        self.assertEqual(self.host.published, [HELD, LATER])
        self.assertEqual([t for t, _, _ in self.ledger()], [HELD, LATER])
        self.assertEqual(
            [ids for _, ids, _ in self.ledger()],
            [["raw-1"], ["raw-2"]],
            "each line carries the raw events it came from",
        )


class TheNewSentenceIsNotHeldTest(_Case):
    def test_after_a_speaker_change(self):
        self.commit(HELD, speaker=1)
        self.clock[0] += 1.0
        self.commit(LATER, speaker=2)
        self.assertEqual(
            self.host.published,
            [HELD, LATER],
            "the new speaker's complete sentence goes out now, after the held line",
        )

    def test_after_a_timed_out_hold(self):
        self.commit(HELD)
        self.clock[0] += 5.0  # the timer has not run: the next final finds it due
        self.commit(LATER)
        self.assertEqual(self.host.published, [HELD, LATER])


class StopUsesTheHeldLinesOwnContextTest(_Case):
    def test_stop_flush_keeps_the_lineage(self):
        self.commit(HELD)
        self.assertEqual(self.host.published, [], "fixture: held")
        self.asm.flush("stop_listening")
        records = self.ledger()
        self.assertEqual([t for t, _, _ in records], [HELD])
        self.assertEqual(records[0][1], ["raw-1"], "the stop flush names the held line's own raw event")


if __name__ == "__main__":
    unittest.main()
