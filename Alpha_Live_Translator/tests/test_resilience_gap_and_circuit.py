"""Regression tests for problem D — `CLIENT_DELIVERY_SPRINT_v5.md` items 44/45.

Both paths already had working machinery before these items: Deepgram
reconnect had backoff, an audio replay buffer and single-flight locking; DeepL
had error classification and retry-with-backoff. These tests cover only the
pieces that were genuinely missing.

* **44** — audio captured *while the socket is down* is gone. Replay cannot
  bring it back, so a transcript that stitches silently across the hole reads
  as continuous speech and a client cannot tell a clean recording from one
  with a hole in it. `_mark_deepgram_gap_if_any` makes it visible.
* **45** — a transient provider outage made every segment spend the full retry
  ladder (~7s) before failing. The breaker fails fast after N *consecutive*
  failures, and reopens with a longer cooldown, without ever refusing to
  recover and without blocking the transcript.

**Not covered here, and it cannot be:** item 44's sprint gate is a 60-minute
live session with a deliberate network drop. That needs a human at the machine.
"""

import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import (  # noqa: E402
    DG_GAP_MARKER_MIN_S,
    TRANSLATION_CIRCUIT_BREAK_AFTER,
    TRANSLATION_CIRCUIT_COOLDOWN_MAX_S,
)
from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402
from alpha.translation.deepl_client import DeepLError  # noqa: E402
from alpha.translation.translation_worker import TranslationWorker  # noqa: E402


class _GapHost(DeepgramClientMixin):
    """Only the publish edge is captured; the gap logic itself is real."""

    def __init__(self) -> None:
        self._dg_disconnected_at = 0.0
        self._last_committed_speaker = 1
        self.published: list[tuple[str, dict]] = []

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        self.published.append((text, dict(metadata or {})))
        return True


class DeepgramGapMarkerTest(unittest.TestCase):
    """Item 44."""

    def test_a_real_outage_is_marked_in_the_transcript(self):
        host = _GapHost()
        host._dg_disconnected_at = time.time() - 12.0
        gap = host._mark_deepgram_gap_if_any()
        self.assertIsNotNone(gap)
        self.assertEqual(1, len(host.published), "the gap was not made visible")
        text, metadata = host.published[0]
        self.assertIn("connection lost", text.lower())
        self.assertIn("12", text, f"the real outage length is missing: {text!r}")
        self.assertTrue(metadata.get("connection_gap_marker"))

    def test_the_marker_is_not_translated_and_is_flagged_synthetic(self):
        """It is an annotation, not speech -- it must not be sent to DeepL or
        counted as a real utterance."""
        host = _GapHost()
        host._dg_disconnected_at = time.time() - 20.0
        host._mark_deepgram_gap_if_any()
        _, metadata = host.published[0]
        self.assertFalse(metadata.get("translation_eligible", True))
        self.assertTrue(metadata.get("synthetic_record"))

    def test_a_blip_shorter_than_the_floor_is_not_marked(self):
        """A sub-threshold reconnect marker would be noisier than the hole."""
        host = _GapHost()
        host._dg_disconnected_at = time.time() - (float(DG_GAP_MARKER_MIN_S) / 2.0)
        self.assertIsNone(host._mark_deepgram_gap_if_any())
        self.assertEqual([], host.published)

    def test_no_disconnect_means_no_marker(self):
        host = _GapHost()
        self.assertIsNone(host._mark_deepgram_gap_if_any())
        self.assertEqual([], host.published)

    def test_the_clock_resets_so_one_outage_is_marked_once(self):
        host = _GapHost()
        host._dg_disconnected_at = time.time() - 15.0
        host._mark_deepgram_gap_if_any()
        self.assertIsNone(host._mark_deepgram_gap_if_any())
        self.assertEqual(1, len(host.published))

    def test_a_publish_failure_never_breaks_the_reconnect(self):
        """Annotating is best-effort -- losing the marker must not stop the
        reconnect that restores transcription."""

        class _Exploding(_GapHost):
            def _publish_final_transcript_segment(self, *a, **k):
                raise RuntimeError("UI is gone")

        host = _Exploding()
        host._dg_disconnected_at = time.time() - 30.0
        host._mark_deepgram_gap_if_any()  # must not raise


class _DeadClient:
    def translate_text(self, text, source_lang=None, target_lang=None):
        raise DeepLError("down", code="temporary_server", retryable=True)


class TranslationCircuitBreakerTest(unittest.TestCase):
    """Item 45."""

    def _worker(self) -> TranslationWorker:
        return TranslationWorker(run_id="t", client=_DeadClient(), enabled=True)

    def test_circuit_starts_closed(self):
        worker = self._worker()
        self.assertFalse(worker.circuit_is_open())
        self.assertFalse(worker.degraded)

    def test_opens_only_after_n_consecutive_failures(self):
        worker = self._worker()
        for _ in range(int(TRANSLATION_CIRCUIT_BREAK_AFTER) - 1):
            worker._record_translation_failure("temporary_server")
        self.assertFalse(worker.circuit_is_open(), "opened too early")
        worker._record_translation_failure("temporary_server")
        self.assertTrue(worker.circuit_is_open())

    def test_a_success_resets_the_streak(self):
        """Consecutive, not cumulative -- an intermittent failure must not
        creep the circuit open over a long healthy session."""
        worker = self._worker()
        for _ in range(int(TRANSLATION_CIRCUIT_BREAK_AFTER) - 1):
            worker._record_translation_failure("temporary_server")
        worker._record_translation_success()
        worker._record_translation_failure("temporary_server")
        self.assertFalse(worker.circuit_is_open())

    def test_success_closes_an_open_circuit_and_says_so(self):
        worker = self._worker()
        for _ in range(int(TRANSLATION_CIRCUIT_BREAK_AFTER)):
            worker._record_translation_failure("temporary_server")
        self.assertTrue(worker.circuit_is_open())
        worker._record_translation_success()
        self.assertFalse(worker.circuit_is_open())
        self.assertIn("recovered", worker.status_message.lower())

    def test_degradation_is_visible(self):
        """Item 47's indicator renders this."""
        worker = self._worker()
        for _ in range(int(TRANSLATION_CIRCUIT_BREAK_AFTER)):
            worker._record_translation_failure("temporary_server")
        self.assertTrue(worker.degraded)
        self.assertIn("degraded", worker.status_message.lower())

    def test_cooldown_escalates_but_stays_capped(self):
        worker = self._worker()
        seen = []
        for _ in range(8):
            for _ in range(int(TRANSLATION_CIRCUIT_BREAK_AFTER)):
                worker._record_translation_failure("temporary_server")
            seen.append(worker._circuit_cooldown_s)
            worker._circuit_open_until = 0.0  # simulate the cooldown expiring
        self.assertGreater(seen[-1], seen[0], "cooldown never backed off")
        self.assertLessEqual(
            seen[-1],
            float(TRANSLATION_CIRCUIT_COOLDOWN_MAX_S),
            "cooldown grew past its cap -- recovery would be refused",
        )

    def test_the_circuit_always_expires_so_recovery_is_never_refused(self):
        worker = self._worker()
        for _ in range(int(TRANSLATION_CIRCUIT_BREAK_AFTER)):
            worker._record_translation_failure("temporary_server")
        self.assertTrue(worker.circuit_is_open())
        self.assertFalse(
            worker.circuit_is_open(now=worker._circuit_open_until + 1.0),
            "the circuit never reopens -- translation would be dead for the session",
        )

    def test_quota_is_not_counted_by_the_breaker(self):
        """Quota already disables the worker permanently; double-counting it
        would open a circuit nobody consults."""
        worker = self._worker()
        worker._quota_disabled = True
        self.assertTrue(worker.degraded, "quota must still read as degraded")
        self.assertFalse(worker.circuit_is_open())


if __name__ == "__main__":
    unittest.main()
