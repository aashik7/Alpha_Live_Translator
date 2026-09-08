"""A full translation queue must cost one line, not the rest of the session.

WHAT WAS BROKEN
---------------
`enqueue_stable_segment` allocates a dense ordering sequence under `_lock`
(:480) and then calls `put_nowait` outside it. On a full FIFO `put_nowait`
raises without evicting, so the **newest** line -- the one just spoken -- is the
one dropped. The handler tidies up after itself: it discards the sequence from
`_accepted_sequences`, `_sequence_to_source` and `_seen_request_ids`, and
decrements three counters. What it does not do is roll back
`_next_translation_sequence`.

That leaves a permanent hole in the ordering key, and the commit gate advances
only by finding `_next_translation_sequence_to_commit` in `_held`. So it parks
on the hole forever: every later translation completes at DeepL, is buffered in
`_held`, and is never handed to `on_translation_ready`. The translation pane
simply stops updating while transcription keeps scrolling. Export reads the
pane, so the delivered translated transcript ends at the drop.

Measured before the fix: 100 queued, one dropped, the consumer then catches up
on all 100 and **200 further successful translations reach nothing** -- they
park in `_held` while the gate stays on the dropped sequence. `degraded` was
`False` and `status_message` empty throughout, so the existing indicator never
spoke.

No error is required to reach this. A hundred lines backing up behind the
single translation thread is enough, which is also the case where the circuit
breaker never opens.

THE FIX
-------
Record the discarded sequence in `_dropped_sequences` and let the gate step over
it. Rolling back `_next_translation_sequence` instead would not be safe: it is
allocated under `_lock` while `put_nowait` runs outside it, so a concurrent
submit could re-issue the number and two jobs would share one ordering key.

WHAT THESE TESTS PIN
--------------------
* the gate steps over a dropped sequence, so the loss is one line
* it does NOT step over a sequence that is merely still in flight -- that
  distinction is the whole safety of the change, and skipping it would turn a
  stall into silent misordering, which is worse
* the drop is counted, named in `status_message`, and visible through
  `degraded`
* `degraded` returns to False on its own -- a window, not a latch, so one drop
  cannot leave the indicator red for the rest of the meeting
* `_next_translation_sequence` is deliberately NOT rolled back

The real `TranslationWorker` throughout. No consumer thread is started; the
tests pop the FIFO and hand back a result themselves, which is exactly what the
consumer does and lets the backlog be held at an exact depth.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import TRANSLATION_QUEUE_MAX_SIZE  # noqa: E402
from alpha.translation.translation_worker import (  # noqa: E402
    TranslationResult,
    TranslationWorker,
)


class QueueFullTest(unittest.TestCase):
    def setUp(self):
        self.delivered = []
        self.worker = TranslationWorker(
            run_id="test",
            evidence_dir=Path(tempfile.mkdtemp(prefix="alpha_qfull_")),
            on_translation_ready=lambda r: self.delivered.append(
                int(r.translation_sequence)
            ),
            client=None,
            enabled=True,
        )

    # -- helpers ---------------------------------------------------------

    def _submit(self, segment_id):
        return self.worker.enqueue_stable_segment(
            segment_id=segment_id,
            source_language="ja",
            source_text="line %d" % segment_id,
            canonical_utterance_id="u%d" % segment_id,
            session_id="s",
        )

    def _consume(self, seq):
        """What the consumer thread does: take the job, hand back a result."""
        if not self.worker._queue.empty():
            self.worker._queue.get_nowait()
        self.worker._handle_result(
            TranslationResult(
                run_id="test",
                segment_id=seq,
                source_language="ja",
                target_language="en",
                source_text="line %d" % seq,
                source_text_hash="h%d" % seq,
                translated_text="TRANSLATED %d" % seq,
                status="success",
                translation_sequence=seq,
                source_segment_id=seq,
                canonical_utterance_id="u%d" % seq,
                session_id="s",
            )
        )

    def _fill_the_queue(self):
        """Back the FIFO up to its bound, the way a slow consumer does."""
        accepted = [
            i for i in range(1, TRANSLATION_QUEUE_MAX_SIZE + 1) if self._submit(i)
        ]
        self.assertEqual(len(accepted), TRANSLATION_QUEUE_MAX_SIZE)
        self.assertTrue(self.worker._queue.full())
        return accepted

    def _drop_one(self):
        """Submit into the full queue. Returns the sequence that was discarded."""
        seq = self.worker._next_translation_sequence + 1
        self.assertFalse(self._submit(9000 + seq), "the queue was not actually full")
        return seq

    # -- the defect ------------------------------------------------------

    def test_later_translations_still_reach_the_pane_after_a_drop(self):
        backlog = self._fill_the_queue()
        dropped = self._drop_one()

        for seq in backlog:  # the consumer catches up
            self._consume(seq)
        self.assertEqual(len(self.delivered), TRANSLATION_QUEUE_MAX_SIZE)

        for seq in range(dropped + 1, dropped + 201):
            self._consume(seq)

        self.assertEqual(
            len(self.delivered),
            TRANSLATION_QUEUE_MAX_SIZE + 200,
            "the commit gate parked on the dropped sequence: %d results are "
            "stranded in _held and the translation pane has stopped for the "
            "rest of the session" % len(self.worker._held),
        )
        self.assertEqual(self.worker._held, {}, "results were left stranded")
        self.assertNotIn(dropped, self.delivered, "a dropped line was delivered")

    def test_several_consecutive_drops_are_all_stepped_over(self):
        backlog = self._fill_the_queue()
        first = self._drop_one()
        self._drop_one()
        self._drop_one()

        for seq in backlog:
            self._consume(seq)
        for seq in range(first + 3, first + 13):
            self._consume(seq)

        self.assertEqual(len(self.delivered), TRANSLATION_QUEUE_MAX_SIZE + 10)
        self.assertEqual(self.worker._held, {})

    # -- the safety half: what must NOT be skipped ------------------------

    def test_an_in_flight_sequence_holds_the_gate_but_a_dropped_one_does_not(self):
        """The distinction the whole change rests on.

        A dropped sequence will never produce a result, so stepping over it is
        safe. A sequence still in flight WILL produce one, and skipping it
        would let a later translation reach the pane ahead of an earlier one.
        """
        backlog = self._fill_the_queue()
        dropped = self._drop_one()
        in_flight = backlog[-1]

        for seq in backlog[:-1]:
            self._consume(seq)
        self.assertEqual(len(self.delivered), TRANSLATION_QUEUE_MAX_SIZE - 1)
        self.assertEqual(self.worker._next_translation_sequence_to_commit, in_flight)

        # Results that arrive AFTER the still-in-flight one must wait for it,
        # even though the sequence between them was dropped.
        self._consume(dropped + 1)
        self._consume(dropped + 2)
        self.assertEqual(
            len(self.delivered),
            TRANSLATION_QUEUE_MAX_SIZE - 1,
            "a translation was delivered ahead of one still in flight",
        )

        self._consume(in_flight)
        self.assertEqual(
            self.delivered[-3:],
            [in_flight, dropped + 1, dropped + 2],
            "the gate did not drain in order once the in-flight job landed",
        )

    # -- the operator can see it -----------------------------------------

    def test_the_drop_is_counted_and_named(self):
        self._fill_the_queue()
        self._drop_one()
        self.assertEqual(self.worker._counters.get("TRANSLATION_QUEUE_FULL_DROPS"), 1)
        self.assertIn(
            "translation",
            self.worker.status_message.lower(),
            "status_message does not name what happened: %r"
            % (self.worker.status_message,),
        )
        self.assertTrue(
            self.worker.degraded,
            "the indicator reads healthy immediately after dropping a line",
        )

    def test_degraded_is_a_window_and_not_a_latch(self):
        """One drop must not leave the indicator red for the whole meeting.

        Both existing contributors to `degraded` -- the circuit breaker and the
        quota pause -- are states that clear. A drop is an event, so it needs a
        window, and this pins that the window really expires.
        """
        from alpha.constants import TRANSLATION_QUEUE_FULL_DEGRADED_S

        self._fill_the_queue()
        self._drop_one()
        self.assertTrue(self.worker.degraded)

        self.worker._last_queue_full_drop_at -= TRANSLATION_QUEUE_FULL_DEGRADED_S + 1.0
        self.assertFalse(
            self.worker.degraded,
            "degraded never expires, so one drop paints the indicator red for "
            "the rest of the session",
        )

    # -- the deliberate non-change ---------------------------------------

    def test_the_sequence_counter_is_not_rolled_back(self):
        """Pinned because rolling it back is the obvious wrong fix.

        `_next_translation_sequence` is allocated under `_lock` while
        `put_nowait` runs outside it, so a concurrent submit could re-issue a
        rolled-back number and two jobs would share one ordering key.
        """
        self._fill_the_queue()
        dropped = self._drop_one()
        self.assertEqual(self.worker._next_translation_sequence, dropped)
        self.assertNotIn(dropped, self.worker._accepted_sequences)

    def test_reset_session_clears_the_drop_state(self):
        self._fill_the_queue()
        self._drop_one()
        self.assertTrue(self.worker._dropped_sequences)
        self.worker.reset_session(run_id="next")
        self.assertEqual(self.worker._dropped_sequences, set())
        self.assertEqual(self.worker._counters["TRANSLATION_QUEUE_FULL_DROPS"], 0)
        self.assertFalse(self.worker.degraded)


if __name__ == "__main__":
    unittest.main()
