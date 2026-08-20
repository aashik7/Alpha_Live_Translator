"""The mixer must repay its backlog, not one frame per call.

WHAT WAS BROKEN
---------------
`emit_due_frames` returned at most ONE 20 ms frame per call while advancing
`_next_frame_time` by exactly one frame. A caller running slower than 50 Hz
could therefore never catch up: measured against the real mixer, a caller
ticking every 100 ms emitted 10 frames in one second against 50 due, leaving
800 ms of audio per second in the buffer. `_trim_buffer` keeps only the newest
3 s, so that backlog was eventually discarded outright — silent, permanent word
loss whenever the mixer loop was delayed.

The lag was also permanent rather than transient, because each call only ever
repaid a single frame of it.

This was NOT the main cause of the 2026-08-20 live run's word loss — that was
the source gate emitting silence, fixed separately — and the deficit measured on
that run's own artifacts was about 2.3% (675 s of audio for 691 s of wall time).
It is fixed here because it is a real, silent audio-loss path in the same
pipeline.

WHY THE RESYNC EXISTS
---------------------
Past the buffer's own depth the missing audio has already been trimmed away, so
counting those frames would only emit zero-padded silence for speech that no
longer exists. Beyond `MAX_BUFFER_SAMPLES` the clock resyncs instead.
"""

import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from alpha.audio.timeline_mixer import (  # noqa: E402
    FRAME_SAMPLES,
    MAX_BUFFER_SAMPLES,
    DeepgramTimelineMixer,
)
from alpha.config import DEEPGRAM_SAMPLE_RATE  # noqa: E402

FRAME_SECONDS = FRAME_SAMPLES / float(DEEPGRAM_SAMPLE_RATE)


def _speech(seconds, rms=1200, seed=2):
    rng = np.random.default_rng(seed)
    n = int(DEEPGRAM_SAMPLE_RATE * seconds)
    return (rng.standard_normal(n) * rms).astype(np.int16).tobytes()


def _mixer():
    m = DeepgramTimelineMixer()
    m.configure_sources(1, DEEPGRAM_SAMPLE_RATE, mic_available=False)
    return m


class ABacklogIsRepaid(unittest.TestCase):
    def test_a_slow_caller_still_gets_every_due_frame(self):
        m = _mixer()
        m.push_system(_speech(2.0))
        m.emit_due_frames()  # start the clock
        m._next_frame_time = time.monotonic() - 0.5  # half a second behind
        emitted = m.emit_due_frames()
        self.assertGreaterEqual(
            len(emitted),
            int(0.5 / FRAME_SECONDS) - 1,
            f"only {len(emitted)} frames returned for 0.5 s of backlog",
        )

    def test_one_call_no_longer_repays_only_one_frame(self):
        m = _mixer()
        m.push_system(_speech(2.0))
        m.emit_due_frames()
        m._next_frame_time = time.monotonic() - 0.2
        self.assertGreater(len(m.emit_due_frames()), 1)

    def test_nothing_is_emitted_before_it_is_due(self):
        m = _mixer()
        m.push_system(_speech(1.0))
        m.emit_due_frames()
        self.assertEqual(m.emit_due_frames(), [], "emitted a frame that was not due")

    def test_the_burst_is_bounded_by_the_buffer_depth(self):
        m = _mixer()
        m.push_system(_speech(1.0))
        m.emit_due_frames()
        m._next_frame_time = time.monotonic() - 3600.0  # an hour behind
        emitted = m.emit_due_frames()
        self.assertLessEqual(
            len(emitted),
            int(MAX_BUFFER_SAMPLES / FRAME_SAMPLES),
            "an unbounded burst was emitted",
        )

    def test_an_extreme_lag_resyncs_instead_of_emitting_dead_silence(self):
        """Past the buffer depth the audio is already gone; emitting frames for
        it would only produce zero padding."""
        m = _mixer()
        m.push_system(_speech(1.0))
        m.emit_due_frames()
        m._next_frame_time = time.monotonic() - 3600.0
        m.emit_due_frames()
        lag = time.monotonic() - m._next_frame_time
        self.assertLess(
            lag,
            MAX_BUFFER_SAMPLES / float(DEEPGRAM_SAMPLE_RATE) + 1.0,
            f"the clock is still {lag:.0f}s behind after a resync",
        )

    def test_frames_carry_real_audio_not_padding(self):
        m = _mixer()
        m.push_system(_speech(1.0))
        m.emit_due_frames()
        m._next_frame_time = time.monotonic() - 0.4
        emitted = m.emit_due_frames()
        self.assertTrue(emitted)
        audible = 0
        for pcm, _meta in emitted:
            if np.any(np.frombuffer(pcm, dtype=np.int16)):
                audible += 1
        self.assertGreater(
            audible, 0, "every repaid frame was silence — the backlog was lost"
        )

    def test_each_frame_is_the_expected_size(self):
        m = _mixer()
        m.push_system(_speech(1.0))
        m.emit_due_frames()
        m._next_frame_time = time.monotonic() - 0.1
        for pcm, _meta in m.emit_due_frames():
            self.assertEqual(len(pcm), FRAME_SAMPLES * 2, "frame is not 20 ms of int16")


if __name__ == "__main__":
    unittest.main()
