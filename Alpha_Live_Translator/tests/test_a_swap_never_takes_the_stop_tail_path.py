"""Two bugs found by the 2026-09-14 full-code audit.

BUG 1 -- A DEVICE SWAP MID-SENTENCE DELETED THE OPERATOR'S WORDS
---------------------------------------------------------------
Phase 7 (e288c9f, package 26.5.16) made a device swap a deliberate utterance
boundary by calling `flush(DEVICE_SWAP_BOUNDARY_REASON)`. It correctly kept the
swap away from the latched `_stop_boundary_active` flag. But the rest of
`flush()` was not read, and further down it said:

    incomplete, inc_reason = looks_incomplete_japanese_fragment(text)
    if incomplete or reason == "stop_listening":
        self._flush_locked("stop_flush_incomplete_tail", force=True,
                           stop_incomplete=incomplete, ...)

`incomplete` ALONE sends the buffered fragment down the stop-tail path, whatever
the reason. From there `suppress_stop_tail_early` is true (both
STOP_TAIL_CLEANUP_ENABLED and SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA are True),
so the fragment is classified `intentionally_suppressed` and never written. It
was also marked synthetic, because `_is_synthetic_stop_only_ingress` treats
`stop_flush_incomplete_tail` as stop-only ingress.

So on the owner's exact scenario -- headphones plugged in while someone is
mid-sentence -- the words that had already arrived were removed from the
delivered transcript. Driven against the real assembler before this fix, a
device swap and a Stop were indistinguishable: both emitted
STOP_TAIL_CANDIDATE_SUPPRESSED, CANONICAL_LEDGER_SUPPRESS_CANDIDATE and
SUPPRESSED_STOP_TAIL_CANDIDATE_WRITE_SKIPPED, with `is_listening` still True.

Why stop-tail suppression exists, and why it is wrong here: at Stop a dangling
incomplete fragment is noise at the edge of a finished session. Mid-session it
is real speech that a device change interrupted. Suppressing it trades a slightly
ragged line for silent content loss.

Why Phase 7's own tests missed it: none of them put anything in the buffer. They
pinned the flag and the merge gate, which were right, and never asked what
happens to a sentence that is in flight at the moment of the swap -- the one
case that matters. A test looser than the claim, again.

WHAT THIS HARNESS CAN AND CANNOT PROVE
-------------------------------------
Calibrated before writing: with the minimal host these tests use, a normal
non-stop commit reaches the canonical commit authority and is rejected with
IDENTITY_REJECTION, because the host carries no utterance identity and the
authority fails closed. That is a harness limit, not a production path. So these
tests do NOT claim the fragment reaches the transcript end to end. They pin the
precise defect instead: the swap must be routed as a normal commit, and must
never reach stop-tail suppression.

BUG 2 -- CRASH FORENSICS SAID THE APP WAS NEVER LISTENING
--------------------------------------------------------
`crash_guard_log._host_context` recorded
`ctx["listening"] = getattr(host, "listening", False)`. Nothing anywhere assigns
`listening`; the real attribute is `is_listening`. So every crash context the app
ever wrote reported the session as not listening -- including every crash that
happened mid-meeting, which is exactly when that field is read.
"""

import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import alpha.utils.japanese_accuracy_log as jal  # noqa: E402
from alpha.transcription import japanese_sentence_assembler as jsa  # noqa: E402

# No sentence-final punctuation: a sentence cut off by the swap.
IN_FLIGHT = "本日はお時間をいただきありがとうございま"

SUPPRESSION_EVENTS = {
    "STOP_TAIL_CANDIDATE_SUPPRESSED",
    "CANONICAL_LEDGER_SUPPRESS_CANDIDATE",
    "SUPPRESSED_STOP_TAIL_CANDIDATE_WRITE_SKIPPED",
}


class _Host:
    """The minimal host shape the suite's other assembler tests use."""

    _listen_language = "ja"
    _is_finalizing = False
    _is_stopping = False
    is_listening = True

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        return True


class _Captured:
    def __init__(self):
        self.events = []
        self.flush_calls = []


def _flush_with_a_sentence_in_flight(reason, text=IN_FLIGHT):
    """Buffer a fragment in the shape production builds, then flush."""
    captured = _Captured()
    real_log = jal.jp_accuracy_log
    real_mod_log = getattr(jsa, "jp_accuracy_log", None)
    jal.jp_accuracy_log = lambda e, **kw: captured.events.append(e)
    jsa.jp_accuracy_log = jal.jp_accuracy_log
    assembler = jsa.get_japanese_continuity_assembler(_Host())
    assembler.reset()
    real_flush_locked = assembler._flush_locked

    def spy(flush_reason, *args, **kwargs):
        captured.flush_calls.append((flush_reason, dict(kwargs)))
        return real_flush_locked(flush_reason, *args, **kwargs)

    assembler._flush_locked = spy
    try:
        now = time.monotonic()
        assembler._buffer = {
            "speaker": 2,
            "text": text,
            "metadata": {"channel_index": 0},
            "created_mono": now,
            "updated_mono": now,
            "hold_started_mono": now,
            "part_count": 1,
            "upstream_reason": "swap_tail_test",
            "raw_fragments": [text],
            "source_raw_event_ids": ["swap-tail-raw-1"],
        }
        assembler._pending_flush_due_mono = None
        assembler._quarantine_recovery_pending = []
        assembler.flush(reason)
    finally:
        jal.jp_accuracy_log = real_log
        if real_mod_log is not None:
            jsa.jp_accuracy_log = real_mod_log
    return captured


class ASwapNeverTakesTheStopTailPathTest(unittest.TestCase):
    def test_the_fragment_is_routed_as_the_swap_not_as_a_stop_tail(self):
        captured = _flush_with_a_sentence_in_flight(jsa.DEVICE_SWAP_BOUNDARY_REASON)
        reasons = [r for r, _ in captured.flush_calls]
        self.assertNotIn(
            "stop_flush_incomplete_tail",
            reasons,
            "a mid-session device swap was routed as a STOP tail; routed as %r"
            % (reasons,),
        )
        self.assertIn(jsa.DEVICE_SWAP_BOUNDARY_REASON, reasons)

    def test_the_fragment_is_not_flagged_as_a_stop_incomplete_tail(self):
        captured = _flush_with_a_sentence_in_flight(jsa.DEVICE_SWAP_BOUNDARY_REASON)
        flagged = [kw for _, kw in captured.flush_calls if kw.get("stop_incomplete")]
        self.assertEqual(
            flagged,
            [],
            "stop_incomplete=True reached the swap's commit, which is the switch "
            "that turns on stop-tail suppression",
        )

    def test_the_operators_words_are_not_suppressed(self):
        """The effect, not only the routing."""
        captured = _flush_with_a_sentence_in_flight(jsa.DEVICE_SWAP_BOUNDARY_REASON)
        fired = sorted(SUPPRESSION_EVENTS & set(captured.events))
        self.assertEqual(
            fired,
            [],
            "a device swap mid-sentence suppressed the words already spoken: %r"
            % (fired,),
        )

    def test_a_stop_still_suppresses_its_incomplete_tail(self):
        """Guard. Stop-tail suppression is correct at Stop and must not have
        been weakened to fix the swap."""
        captured = _flush_with_a_sentence_in_flight("stop_listening")
        self.assertIn("stop_flush_incomplete_tail", [r for r, _ in captured.flush_calls])
        self.assertTrue(
            SUPPRESSION_EVENTS & set(captured.events),
            "the fix disabled stop-tail suppression for Stop too",
        )

    def test_a_swap_with_nothing_buffered_is_still_a_no_op(self):
        """The one-shot boundary is raised; nothing is committed."""
        assembler = jsa.get_japanese_continuity_assembler(_Host())
        assembler.reset()
        calls = []
        assembler._flush_locked = lambda *a, **k: calls.append(a)
        assembler.flush(jsa.DEVICE_SWAP_BOUNDARY_REASON)
        self.assertEqual(calls, [])
        self.assertTrue(assembler._merge_boundary_pending)


class CrashForensicsReportListeningTest(unittest.TestCase):
    def _ctx(self, listening):
        from alpha.utils.crash_guard_log import _host_context

        class Host:
            is_listening = listening
            _audio_q = None
            transcript_queue = None

        return _host_context(Host())

    def test_a_crash_mid_session_is_recorded_as_listening(self):
        self.assertTrue(
            self._ctx(True).get("listening"),
            "crash forensics recorded a listening session as not listening -- "
            "it read `listening`, which nothing assigns",
        )

    def test_a_crash_while_idle_is_recorded_as_not_listening(self):
        self.assertFalse(self._ctx(False).get("listening"))


if __name__ == "__main__":
    unittest.main()
