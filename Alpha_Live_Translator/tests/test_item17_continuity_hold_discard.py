"""Item 17: a raise inside the continuity-hold tick must not silently eat the sentence.

The defect, driven on a real `JapaneseContinuityAssembler` in the review:

    returned              : True   <- reports success
    buffer after          : None
    spoken text survived? : False
    events emitted        : ['ASYNC_LOG_EMERGENCY_WRITE']

The only event was the crash logger's own plumbing. Nothing named the content
loss, and the buffered Japanese sentence -- spoken, captured, buffered -- was
gone.

The sibling handler in the same class, `_handle_assembler_exception` (:1193),
faces the same situation and does it correctly: it emits a named event AND hands
the fragment to `_quarantine_recovery_pending`, which item 43's drain
(`:1127-1128` -> `_drain_quarantine_recovery`) replays on the next ingest, at the
first point where the assembler lock is not held.

These tests assert the two behaviours that were missing, and deliberately pin the
two that were already correct so the fix cannot change them by accident.
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SPOKEN = "本日はお時間をいただきありがとうございます"


def _host():
    class _Host:
        _live_session_id = "sess-item17"
        _listen_language = "ja"
        _is_finalizing = False
        _is_stopping = False
        is_listening = True

        def __init__(self):
            self.published: list[str] = []

        def _publish_final_transcript_segment(
            self, speaker, text, metadata=None, queue_item=None, commit_reason=None
        ):
            self.published.append(text)
            return True

    return _Host()


def _assembler(host):
    from alpha.transcription.japanese_sentence_assembler import (
        get_japanese_continuity_assembler,
    )

    return get_japanese_continuity_assembler(host)


def _buffer_a_real_sentence(assembler, text=SPOKEN, speaker=2):
    """The buffer shape production builds at `japanese_sentence_assembler.py:3284`."""
    now = time.monotonic()
    assembler._buffer = {
        "speaker": speaker,
        "text": text,
        "metadata": {"channel_index": 0},
        "created_mono": now,
        "updated_mono": now,
        "hold_started_mono": now,
        "part_count": 1,
        "upstream_reason": "item17_test",
        "raw_fragments": [text],
        "source_raw_event_ids": [],
    }
    # Reach the execute call rather than the reschedule branch at :3163.
    assembler._pending_flush_due_mono = None
    assembler._quarantine_recovery_pending = []


def _drive_a_raising_hold_tick(assembler):
    """Force `_execute_continuity_hold_locked` to raise, the way the review did."""
    from alpha.transcription import japanese_sentence_assembler as jsa

    def _boom(reason):
        raise RuntimeError("hold tick exploded")

    assembler._execute_continuity_hold_locked = _boom

    events: list[str] = []
    real_log = jsa.jp_accuracy_log

    def _spy(event, *args, **kwargs):
        events.append(str(event))
        return real_log(event, *args, **kwargs)

    with patch.object(jsa, "jp_accuracy_log", _spy):
        returned = assembler.try_execute_continuity_hold(
            assembler._flush_generation, "item17_test"
        )
    return returned, events


class TheDiscardIsNamedAndTheSentenceSurvives(unittest.TestCase):
    def setUp(self):
        self.host = _host()
        self.assembler = _assembler(self.host)
        _buffer_a_real_sentence(self.assembler)
        self.returned, self.events = _drive_a_raising_hold_tick(self.assembler)

    def test_the_loss_is_named(self):
        """Nothing named it before -- the only event was the crash logger's."""
        self.assertIn(
            "CONTINUITY_HOLD_TICK_DISCARDED_BUFFER",
            self.events,
            f"the discard was not named; events were {self.events}",
        )

    def test_the_spoken_sentence_is_queued_for_recovery(self):
        """The same queue `_handle_assembler_exception` uses, drained by item 43."""
        pending = list(getattr(self.assembler, "_quarantine_recovery_pending", []))
        texts = [str(entry.get("text", "")) for entry in pending]
        self.assertIn(
            SPOKEN,
            texts,
            f"the buffered sentence was dropped, not queued; pending={pending}",
        )

    def test_the_queued_entry_keeps_its_speaker(self):
        pending = list(getattr(self.assembler, "_quarantine_recovery_pending", []))
        entry = next((e for e in pending if e.get("text") == SPOKEN), None)
        self.assertIsNotNone(entry, "nothing was queued")
        self.assertEqual(entry.get("speaker"), 2)

    def test_a_counter_records_it(self):
        self.assertGreaterEqual(
            int(getattr(self.assembler, "_continuity_hold_discard_count", 0)),
            1,
            "no counter recorded the discard",
        )


class TheBehaviourThatWasAlreadyCorrectIsUnchanged(unittest.TestCase):
    """Pinned so the fix cannot alter them by accident."""

    def setUp(self):
        self.host = _host()
        self.assembler = _assembler(self.host)
        _buffer_a_real_sentence(self.assembler)
        self.returned, self.events = _drive_a_raising_hold_tick(self.assembler)

    def test_the_tick_still_returns_true(self):
        """The worker treats False as "could not acquire"; True must survive."""
        self.assertTrue(self.returned)

    def test_the_buffer_is_still_cleared(self):
        """Safe mode drops the buffer on purpose; recovery is a copy, not a keep."""
        self.assertIsNone(self.assembler._buffer)

    def test_the_lock_is_released(self):
        acquired = self.assembler._lock.try_acquire(timeout=0.0)
        self.assertTrue(acquired, "the handler left the assembler lock held")
        if acquired:
            self.assembler._lock.release()


class AnEmptyBufferQueuesNothing(unittest.TestCase):
    """A phantom recovery entry would be its own bug."""

    def test_no_entry_and_no_counter_bump_for_an_empty_buffer(self):
        host = _host()
        assembler = _assembler(host)
        _buffer_a_real_sentence(assembler, text="   ")
        before = int(getattr(assembler, "_continuity_hold_discard_count", 0))

        _drive_a_raising_hold_tick(assembler)

        pending = list(getattr(assembler, "_quarantine_recovery_pending", []))
        self.assertEqual(pending, [], f"queued a blank entry: {pending}")
        self.assertEqual(
            int(getattr(assembler, "_continuity_hold_discard_count", 0)), before
        )

    def test_no_entry_when_there_was_no_buffer_at_all(self):
        host = _host()
        assembler = _assembler(host)
        _buffer_a_real_sentence(assembler)
        assembler._buffer = None

        _drive_a_raising_hold_tick(assembler)

        self.assertEqual(list(assembler._quarantine_recovery_pending), [])


if __name__ == "__main__":
    unittest.main()
