"""Item 44's third requirement: commit the utterance that was in flight.

The item is "backoff, buffer, commit in-flight, mark the gap visibly". Three of
those were built and verified live. **Commit in-flight was never implemented**,
and nothing in any run would have shown it, because the loss is silent.

Driving the real `UtteranceLifecycleOwner` shows the failure directly. An
utterance still open when the socket dies is:

  * not merged into the post-reconnect text -- correct, `_timing_compatible`
    rejects the next final because the provider restarts its clock near 0; and
  * not committed either -- `_apply_active_update_locked`'s `force_new` branch
    simply replaces `self._active`.

So every reconnect silently dropped whatever had been spoken but not yet
committed before the drop. Measured with no fix: one commit
("and now a completely different topic"); the pre-drop sentence is gone.

Committing on the unexpected close also fixes the ordering the item's own title
asks for -- pre-drop speech, then the gap marker, then post-reconnect speech --
because `_deepgram_on_close` runs before `_deepgram_on_open` emits the marker.
"""

import inspect
import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import deepgram_client as dc  # noqa: E402
from alpha.transcription import utterance_lifecycle as ul  # noqa: E402


class _Recorder:
    def __init__(self):
        self.commits = []

    def __call__(self, decision):
        self.commits.append(decision.text)


def _lifecycle():
    rec = _Recorder()
    life = ul.UtteranceLifecycleOwner(on_commit=rec)
    life.reset_for_session("sess-item-44")
    return life, rec


def _speak(life, text, *, start, end, event_id):
    life.on_final_chunk(
        text=text,
        speaker=1,
        channel=0,
        start=start,
        end=end,
        is_final=True,
        speech_final=False,
        event_id=event_id,
        metadata={"start_time": start, "end_time": end},
    )


class InFlightSpeechSurvivesADisconnectTest(unittest.TestCase):
    PRE = "I was in the middle of saying something"
    POST = "and now a completely different topic"

    def test_pre_drop_speech_is_committed(self):
        """The defect: without this, the pre-drop sentence vanishes."""
        life, rec = _lifecycle()
        _speak(life, self.PRE, start=10.0, end=12.0, event_id="a1")
        life.commit_in_flight(reason="provider_disconnected")
        _speak(life, self.POST, start=0.4, end=2.0, event_id="b1")
        life.on_utterance_end(event_id="end", channel=0)
        self.assertEqual(rec.commits, [self.PRE, self.POST])

    def test_pre_drop_speech_is_lost_without_the_call(self):
        """Pins that the call is what saves it, not something incidental."""
        life, rec = _lifecycle()
        _speak(life, self.PRE, start=10.0, end=12.0, event_id="a1")
        _speak(life, self.POST, start=0.4, end=2.0, event_id="b1")
        life.on_utterance_end(event_id="end", channel=0)
        self.assertNotIn(self.PRE, rec.commits)

    def test_the_two_sides_of_the_hole_are_never_one_line(self):
        life, rec = _lifecycle()
        _speak(life, self.PRE, start=10.0, end=12.0, event_id="a1")
        life.commit_in_flight(reason="provider_disconnected")
        _speak(life, self.POST, start=0.4, end=2.0, event_id="b1")
        life.on_utterance_end(event_id="end", channel=0)
        for line in rec.commits:
            self.assertFalse(
                self.PRE in line and self.POST in line,
                "speech from both sides of a 30s outage was glued into one line",
            )

    def test_nothing_in_flight_is_a_quiet_no_op(self):
        life, rec = _lifecycle()
        self.assertIsNone(life.commit_in_flight())
        self.assertEqual(rec.commits, [])

    def test_committing_twice_does_not_duplicate(self):
        life, rec = _lifecycle()
        _speak(life, self.PRE, start=10.0, end=12.0, event_id="a1")
        life.commit_in_flight()
        life.commit_in_flight()
        self.assertEqual(rec.commits, [self.PRE])

    def test_it_is_counted(self):
        life, _ = _lifecycle()
        _speak(life, self.PRE, start=10.0, end=12.0, event_id="a1")
        life.commit_in_flight()
        self.assertEqual(life.stats().get("in_flight_commits"), 1)

    def test_a_disconnect_is_not_a_premature_commit(self):
        """If it were, the first post-reconnect chunk could extend the record
        and glue speech across the hole."""
        self.assertNotIn("provider_disconnected", ul._PREMATURE_COMMIT_REASONS)


class WiredToTheUnexpectedCloseTest(unittest.TestCase):
    """Placement matters: on close, so it lands before the gap marker."""

    def test_close_handler_commits_in_flight(self):
        src = inspect.getsource(dc.DeepgramClientMixin._deepgram_on_close)
        self.assertIn("commit_in_flight", src)

    def test_it_is_guarded_by_the_unexpected_close_branch(self):
        """A normal Stop must not trigger it -- stop already commits."""
        src = inspect.getsource(dc.DeepgramClientMixin._deepgram_on_close)
        guard = src.index("if not stop_requested and not getattr(self, \"_dg_disconnected_at\", 0.0):")
        call = src.index("commit_in_flight")
        self.assertGreater(call, guard, "commit_in_flight is outside the unexpected-close guard")

    def test_it_runs_before_the_gap_marker_is_emitted(self):
        """Ordering from item 44's own title: commit in-flight, THEN mark."""
        on_close = inspect.getsource(dc.DeepgramClientMixin._deepgram_on_close)
        on_open = inspect.getsource(dc.DeepgramClientMixin._deepgram_on_open)
        self.assertIn("commit_in_flight", on_close)
        self.assertIn("_mark_deepgram_gap_if_any", on_open)
        self.assertNotIn("_mark_deepgram_gap_if_any", on_close)


class TheOtherThreeRequirementsAreStillWiredTest(unittest.TestCase):
    """Backoff, buffer and the marker -- all verified on live runs; pinned so a
    later change cannot quietly remove one."""

    def test_backoff_retries_rather_than_giving_up(self):
        src = inspect.getsource(dc.DeepgramClientMixin._reconnect_deepgram)
        self.assertIn("while self.is_listening", src)
        self.assertIn("DG_RECONNECT_BACKOFF_MAX_S", src)

    def test_buffered_audio_is_replayed_on_the_new_socket(self):
        src = inspect.getsource(dc.DeepgramClientMixin._deepgram_on_open)
        self.assertIn("_dg_replay_buffer", src)
        self.assertIn("replay_chunks", src)

    def test_the_gap_is_marked(self):
        src = inspect.getsource(dc.DeepgramClientMixin._mark_deepgram_gap_if_any)
        self.assertIn("DG_GAP_MARKER_TEMPLATE", src)


if __name__ == "__main__":
    unittest.main()
