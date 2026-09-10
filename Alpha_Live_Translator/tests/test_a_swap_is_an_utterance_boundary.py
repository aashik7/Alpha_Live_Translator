"""Phase 7: a device swap is a deliberate utterance boundary (R11), and the
mixer buffer that straddles it is measured rather than guessed (R3).

R11 -- WHY A BOUNDARY AT ALL
---------------------------
Deepgram may be mid-utterance when the audio stops for a rebind. The utterance
completes with a truncated tail and the next one starts from a different device,
so the assembler's continuity logic would try to merge across a seam that is
acoustically discontinuous -- gluing the end of one room's audio to the start of
another's. The guideline's instruction is explicit: do NOT try to make the seam
invisible to the assembler, because that is the "hold and hope" shape that
produced item 94. Make it a deliberate boundary instead.

THE TRAP THIS AVOIDS
--------------------
The obvious implementation is `flush("device_swap")`, and it is wrong. `flush()`
sets `_stop_boundary_active = True` for ANY reason, and that flag is cleared
only in `reset()` -- once per session. So a swap would latch it for the rest of
the meeting and silently disable punctuation merging from that point on: an
accuracy regression with no error, no counter and no event. The flag is also
allowlisted as safe precisely BECAUSE only stop paths reach it, so a swap
reaching it would falsify the allowlist entry too.

The second tempting shortcut is to clear `_last_stable_commit`, since the merge
gate already refuses when there is no previous stable line. That is worse: the
same field feeds the revision lineage record and the item 41 non-destructive
revise check, so clearing it disarms a correctness gate to win a merge
decision.

What is implemented instead is a ONE-SHOT boundary. `flush()` keeps setting
`_stop_boundary_active` for every existing caller -- all of which are stop paths
-- and only the new swap reason takes the one-shot branch. The one-shot is
consumed where the next stable line is committed, not inside the merge
predicate, because a predicate with a side effect is its own bug class.

R3 -- THE BUFFER THAT STRADDLES THE SWAP
----------------------------------------
`_sys_buffer` holds up to 3 s of already-resampled 16 kHz audio. Since the
format stamp shipped it is not a correctness risk -- those samples are
format-safe. It is a latency-versus-content choice, and the guideline's
instruction is to decide explicitly and write the decision down, because an
implicit choice here is either silent content loss or a silent 3 s lag and
neither is diagnosable afterwards.

The decision is PLAY IT OUT: keep draining normally, lose nothing, accept the
latency. That is also what the code already did, so what these tests pin is the
part that was missing -- the occupancy is measured into the evidence event, so a
reader can see how much old-device audio crossed the seam.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio.timeline_mixer import DeepgramTimelineMixer  # noqa: E402
from alpha.transcription import japanese_sentence_assembler as jsa  # noqa: E402


class TheSwapReasonDoesNotLatchTheStopFlagTest(unittest.TestCase):
    """The whole point: a mid-session boundary must not behave like Stop."""

    def _assembler(self):
        """Built through the real accessor, over the minimal host shape the
        other assembler tests in this suite use."""

        class _Host:
            _listen_language = "ja"
            _is_finalizing = False
            _is_stopping = False
            is_listening = True

            def _publish_final_transcript_segment(
                self, speaker, text, metadata=None, queue_item=None, commit_reason=None
            ):
                return True

        return jsa.get_japanese_continuity_assembler(_Host())

    def test_a_stop_flush_still_latches_the_stop_boundary(self):
        """Unchanged behaviour for every existing caller -- all stop paths."""
        a = self._assembler()
        a.flush("stop_listening")
        self.assertTrue(a._stop_boundary_active)

    def test_an_unrecognised_reason_is_still_treated_as_a_stop(self):
        """`reason` is threaded through from callers, so the safe default is
        that anything that is not explicitly the swap keeps the old behaviour."""
        a = self._assembler()
        a.flush("some_future_stop_variant")
        self.assertTrue(a._stop_boundary_active)

    def test_a_swap_flush_does_not_latch_the_stop_boundary(self):
        a = self._assembler()
        a.flush(jsa.DEVICE_SWAP_BOUNDARY_REASON)
        self.assertFalse(
            a._stop_boundary_active,
            "a device swap latched the stop-boundary flag, which is cleared "
            "only by reset() -- punctuation merging would stay disabled for "
            "the rest of the meeting, silently",
        )

    def test_a_swap_flush_raises_a_one_shot_boundary(self):
        a = self._assembler()
        a.flush(jsa.DEVICE_SWAP_BOUNDARY_REASON)
        self.assertTrue(a._merge_boundary_pending)

    def test_the_boundary_blocks_a_merge_across_the_seam(self):
        """Effect, not just state: the next fragment must not be glued to a
        stable line that came from the previous device."""
        from alpha.transcription.japanese_stable_accuracy import (
            can_merge_punctuation_with_previous,
        )

        previous = {"text": "こんにちは", "speaker": 1, "mono": 0.0}
        merged_ok, _ = can_merge_punctuation_with_previous(
            "、そうですね",
            previous,
            current_speaker=1,
            stop_boundary_active=False,
            now_mono=0.1,
        )
        blocked, reason = can_merge_punctuation_with_previous(
            "、そうですね",
            previous,
            current_speaker=1,
            stop_boundary_active=True,
            now_mono=0.1,
        )
        self.assertTrue(
            merged_ok, "fixture wrong: this fragment should merge with no boundary"
        )
        self.assertFalse(blocked)
        self.assertEqual(reason, "stop_boundary")

    def test_the_flag_the_allowlist_depends_on_is_untouched_by_a_swap(self):
        """`_stop_boundary_active` is recorded as safe BECAUSE only stop paths
        reach it. A swap reaching it would falsify that entry."""
        a = self._assembler()
        before = a._stop_boundary_active
        a.flush(jsa.DEVICE_SWAP_BOUNDARY_REASON)
        self.assertEqual(a._stop_boundary_active, before)


class TheBufferAcrossTheSeamIsMeasuredTest(unittest.TestCase):
    """R3. The decision is play-it-out; what was missing is the measurement."""

    def test_the_mixer_reports_its_buffered_seconds(self):
        mixer = DeepgramTimelineMixer()
        mixer.configure_sources(2, 48000, mic_available=False)
        self.assertEqual(mixer.buffered_system_seconds(), 0.0)
        # 0.5 s at 16 kHz mono, pushed as 48 kHz stereo so the mixer resamples.
        mixer.push_system(np.zeros(48000, dtype=np.int16).tobytes(), 2, 48000)
        self.assertAlmostEqual(mixer.buffered_system_seconds(), 0.5, places=2)

    def test_the_reported_value_is_bounded_by_the_buffer_cap(self):
        """A number that could exceed the cap would be a measurement bug, and
        this value goes into evidence a human reads."""
        from alpha.audio.timeline_mixer import MAX_BUFFER_SAMPLES

        mixer = DeepgramTimelineMixer()
        mixer.configure_sources(1, 16000, mic_available=False)
        mixer.push_system(np.zeros(16000 * 10, dtype=np.int16).tobytes(), 1, 16000)
        cap_seconds = MAX_BUFFER_SAMPLES / 16000.0
        self.assertLessEqual(mixer.buffered_system_seconds(), cap_seconds + 0.01)


class TheRebindRaisesTheBoundaryAndRecordsTheSeamTest(unittest.TestCase):
    """The wiring: a completed rebind must mark the boundary and say what
    crossed it. R11 + R3 + R13's reported counter, driven through the real
    rebind."""

    def _host(self):
        import threading

        from alpha.audio.wasapi import WasapiCaptureMixin

        class _Host:
            _refresh_connection_indicator = (
                WasapiCaptureMixin._refresh_connection_indicator
            )
            _wasapi_capture_stop_event = WasapiCaptureMixin._wasapi_capture_stop_event
            _wasapi_stop_requested = WasapiCaptureMixin._wasapi_stop_requested
            _rebind_single_flight_lock = WasapiCaptureMixin._rebind_single_flight_lock
            _await_capture_confirmation = WasapiCaptureMixin._await_capture_confirmation

            def __init__(self):
                self._stop_event = threading.Event()
                self._audio_device_changed = True
                self._wasapi_device_change_reported = True
                self._wasapi_default_endpoint_baseline = ""
                self._wasapi_device_watch_thread = None
                self._diag_wasapi_device_name = "Speakers"
                self._wasapi_chunks_captured = 0
                self.boundaries = []

            def _borrow(self, name):
                impl = getattr(WasapiCaptureMixin, name, None)
                if impl is None:
                    raise AttributeError("%s has not been written yet" % name)
                return impl

            def _rebind_wasapi_to_default_device(self):
                return self._borrow("_rebind_wasapi_to_default_device")(self)

            def _mark_device_swap_boundary(self):
                return self._borrow("_mark_device_swap_boundary")(self)

            def _start_device_watch(self):
                return False

            def _read_default_endpoint_id(self):
                return "NEW"

            def _run_on_ui_thread(self, fn):
                pass

            def _close_wasapi_stream(self):
                pass

            def _start_wasapi_loopback(self, show_error_dialog=True):
                self._wasapi_chunks_captured += 1

        return _Host()

    def test_a_completed_rebind_marks_the_utterance_boundary(self):
        host = self._host()
        marked = []
        import alpha.audio.wasapi as w

        real = w.WasapiCaptureMixin._mark_device_swap_boundary
        w.WasapiCaptureMixin._mark_device_swap_boundary = (
            lambda self: marked.append(True)
        )
        try:
            self.assertTrue(host._rebind_wasapi_to_default_device())
        finally:
            w.WasapiCaptureMixin._mark_device_swap_boundary = real
        self.assertEqual(
            len(marked),
            1,
            "the swap did not become an utterance boundary, so the assembler "
            "would merge across a seam between two different devices",
        )

    def test_marking_the_boundary_never_raises(self):
        """It runs on the rebind worker. A boundary that throws would fail a
        rebind that had already succeeded."""
        host = self._host()
        host._mark_device_swap_boundary()

    def test_the_worker_publishes_the_mixer_so_the_seam_can_be_measured(self):
        """The measurement reads the mixer off the host. The mixer used to be a
        LOCAL in `audio_mixer_worker`, so this read found nothing and the
        recorded seam would have been 0.0 for the life of the app -- a number
        in the evidence that looks like an answer and is not."""
        import ast

        src = (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8", errors="replace"
        )
        tree = ast.parse(src)
        worker = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "audio_mixer_worker"
        )
        published = any(
            isinstance(t, ast.Attribute) and t.attr == "_timeline_mixer"
            for node in ast.walk(worker)
            if isinstance(node, ast.Assign)
            for t in node.targets
        )
        self.assertTrue(
            published, "audio_mixer_worker never publishes the mixer on the host"
        )

    def test_the_measured_seam_is_the_mixers_own_number(self):
        host = self._host()
        host._timeline_mixer = DeepgramTimelineMixer()
        host._timeline_mixer.configure_sources(1, 16000, mic_available=False)
        host._timeline_mixer.push_system(
            np.zeros(16000, dtype=np.int16).tobytes(), 1, 16000
        )
        expected = host._timeline_mixer.buffered_system_seconds()
        self.assertGreater(expected, 0.9)
        logged = []
        import alpha.utils.japanese_accuracy_log as jal

        real = jal.jp_accuracy_log
        jal.jp_accuracy_log = lambda event, **kw: logged.append((event, kw))
        try:
            host._rebind_wasapi_to_default_device()
        finally:
            jal.jp_accuracy_log = real
        completed = [kw for e, kw in logged if e == "AUDIO_DEVICE_REBIND_COMPLETED"]
        self.assertTrue(completed, "no completion event was logged")
        self.assertAlmostEqual(
            completed[0].get("buffered_system_seconds"), expected, places=2
        )

    def test_the_swap_counter_is_reported(self):
        host = self._host()
        host._rebind_wasapi_to_default_device()
        self.assertEqual(getattr(host, "_wasapi_swap_count", 0), 1)


if __name__ == "__main__":
    unittest.main()
