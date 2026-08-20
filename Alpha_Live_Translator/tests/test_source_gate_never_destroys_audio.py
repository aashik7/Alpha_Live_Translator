"""The source gate must label a frame, never destroy it.

WHAT WAS BROKEN
---------------
Reported from a live test: the app misses many words and gets worse the longer
it runs. Measured against the reference transcript of the same video:

    reference                     2186 words
    RAW Deepgram returned         1106 words   43.1% recall
    Alpha exported                1067 words   42.6% recall
    exported vs what Deepgram gave              96.5%

So the pipeline kept nearly everything it received; the loss happened before
Deepgram saw the audio. The delivered stream (`main_query` writes it as the
"exact mixed Deepgram-delivery PCM") was 52-82% silence from two minutes in,
while the pre-mix system capture was a steady 2-15% silence at -23.7 to -27.1
dBFS with no drift.

TWO DEFECTS, ONE OUTCOME
------------------------
1. `mix_frame` returned `np.zeros` whenever `chosen_source == "none"`. This gate
   exists to LABEL a frame for speaker attribution; returning silence made a
   wrong label into permanent word loss.

2. `_update_noise_floors` learned the floor from frames judged INACTIVE, and
   inactivity is judged against a threshold derived from that same floor. Once
   the threshold drifted above real speech, the rejected speech was fed back in
   as "noise", raising the floor further — a positive feedback loop. Replaying
   the real captured audio through the real gate reproduced the live result
   almost exactly (47.7% vs 52.1%, 65.3% vs 75.1%, 75.9% vs 82.3%), with the
   floor climbing 23 -> 946 -> 1432 -> 1872 and the threshold reaching 5616.

   For that recording the frame RMS distribution was p5 125, p50 1343, p90 2709.
   A threshold of 5616 gates off 99% of frames; the 80 minimum gates off 3%,
   which is the real silence in the recording.

WHY THE FIX IS SAFE IN ONE DIRECTION ONLY
-----------------------------------------
Both changes can only ever pass MORE audio: `mix_frame` no longer emits silence,
and the learning guard only ever reduces how far the floor rises, which lowers
the threshold and marks more frames active. There is no input for which this
loses audio that the previous code kept.

A note on reproducing it: an earlier replay of this same gate showed only
0-9.5% silence and appeared to exonerate it. That replay was wrong — it passed
the real `time.monotonic()` as `now`, so thousands of frames elapsed inside one
500 ms `SOURCE_HOLD_MS` window and `_apply_hold` never let the source change.
Any test here must advance a SIMULATED clock by one frame duration per frame.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from alpha.audio.source_gate import TeamsSourceGate  # noqa: E402
from alpha.constants import SYSTEM_ACTIVE_RMS_MIN  # noqa: E402

FRAME = 320  # 20 ms at 16 kHz
FRAME_SECONDS = 0.02


def _frame(rms, rng):
    if rms <= 0:
        return np.zeros(FRAME, dtype=np.int16)
    return np.clip(rng.standard_normal(FRAME) * rms, -32768, 32767).astype(np.int16)


def _rms(samples):
    return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))


def _drive(levels, seed=11):
    """Run the real gate over a list of system-RMS levels, one frame each.

    Returns (silent_frame_count, total, final_floor, gate).
    """
    rng = np.random.default_rng(seed)
    gate = TeamsSourceGate()
    now = 0.0
    silent = 0
    mic = np.zeros(FRAME, dtype=np.int16)
    for level in levels:
        sys_s = _frame(level, rng)
        decision = gate.evaluate(_rms(sys_s), 0.0, now=now)
        now += FRAME_SECONDS
        out = gate.mix_frame(sys_s, mic, decision)
        if not np.any(out):
            silent += 1
    return silent, len(levels), gate._sys_noise_floor, gate


class TheGateNeverEmitsSilenceForAudibleInput(unittest.TestCase):
    def test_a_frame_carrying_signal_is_never_zeroed(self):
        rng = np.random.default_rng(3)
        gate = TeamsSourceGate()
        sys_s = _frame(1500, rng)
        mic = np.zeros(FRAME, dtype=np.int16)
        # Force the "none" branch directly rather than waiting for a drift.
        decision = {"chosen_source": "none", "system_rms": 1500.0, "mic_rms": 0.0}
        out = gate.mix_frame(sys_s, mic, decision)
        self.assertTrue(
            np.any(out), "an audible frame was replaced with silence by the gate"
        )
        np.testing.assert_array_equal(out, sys_s)

    def test_none_prefers_whichever_source_is_louder(self):
        rng = np.random.default_rng(4)
        gate = TeamsSourceGate()
        sys_s = _frame(10, rng)
        mic_s = _frame(900, rng)
        decision = {"chosen_source": "none", "system_rms": 10.0, "mic_rms": 900.0}
        np.testing.assert_array_equal(gate.mix_frame(sys_s, mic_s, decision), mic_s)

    def test_genuine_digital_silence_stays_silent(self):
        """Failing open must not invent signal where there is none."""
        gate = TeamsSourceGate()
        z = np.zeros(FRAME, dtype=np.int16)
        decision = {"chosen_source": "none", "system_rms": 0.0, "mic_rms": 0.0}
        self.assertFalse(np.any(gate.mix_frame(z, z, decision)))


class TheNoiseFloorDoesNotRunAway(unittest.TestCase):
    def test_continuous_speech_does_not_drive_the_floor_into_the_speech_band(self):
        """The live failure: 10 minutes of speech pushed the floor to 1872 and
        the threshold to 5616, above the p90 of the audio itself."""
        levels = [1400] * (60 * 50)  # 60 s of speech-level frames
        silent, total, floor, _ = _drive(levels)
        self.assertLess(
            floor,
            700.0,
            f"the floor climbed into the speech band ({floor:.0f})",
        )
        self.assertEqual(
            silent, 0, f"{silent}/{total} speech frames were gated to silence"
        )

    def test_a_long_session_does_not_degrade(self):
        """Progressive degradation was the user-visible symptom: fine for two
        minutes, then worse and worse."""
        levels = [1400] * (60 * 50 * 10)  # 10 minutes
        rng = np.random.default_rng(5)
        gate = TeamsSourceGate()
        now = 0.0
        mic = np.zeros(FRAME, dtype=np.int16)
        first_min = last_min = 0
        for i, level in enumerate(levels):
            sys_s = _frame(level, rng)
            d = gate.evaluate(_rms(sys_s), 0.0, now=now)
            now += FRAME_SECONDS
            if not np.any(gate.mix_frame(sys_s, mic, d)):
                if i < 60 * 50:
                    first_min += 1
                elif i >= len(levels) - 60 * 50:
                    last_min += 1
        self.assertEqual(first_min, 0)
        self.assertEqual(
            last_min, 0, "the last minute lost frames the first minute kept"
        )

    def test_quiet_background_is_still_learned(self):
        """The floor must keep doing its job: anything below the activity
        minimum is a safe noise sample."""
        quiet = float(SYSTEM_ACTIVE_RMS_MIN) / 2.0
        _, _, floor, _ = _drive([quiet] * 2000)
        self.assertGreater(floor, 0.0, "the floor never learned a quiet background")
        self.assertLess(floor, float(SYSTEM_ACTIVE_RMS_MIN))

    def test_speech_after_quiet_background_still_passes(self):
        levels = [40.0] * 1000 + [1400.0] * 1000
        silent, total, _, _ = _drive(levels)
        self.assertEqual(
            silent, 0, f"{silent}/{total} frames silenced after a quiet passage"
        )

    def test_alternating_speech_and_silence_keeps_all_the_speech(self):
        levels = []
        for _ in range(60):
            levels += [1400.0] * 50   # 1 s speech
            levels += [0.0] * 25      # 0.5 s silence
        rng = np.random.default_rng(9)
        gate = TeamsSourceGate()
        now = 0.0
        mic = np.zeros(FRAME, dtype=np.int16)
        lost = 0
        for level in levels:
            sys_s = _frame(level, rng)
            d = gate.evaluate(_rms(sys_s), 0.0, now=now)
            now += FRAME_SECONDS
            out = gate.mix_frame(sys_s, mic, d)
            if level > 0 and not np.any(out):
                lost += 1
        self.assertEqual(lost, 0, f"{lost} speech frames were silenced")


class TheLearningGuardItself(unittest.TestCase):
    def test_a_loud_frame_far_above_the_floor_is_refused_as_a_noise_sample(self):
        gate = TeamsSourceGate()
        self.assertFalse(gate._learns_as_noise(1800.0, 100.0, SYSTEM_ACTIVE_RMS_MIN))

    def test_a_frame_near_the_floor_is_accepted(self):
        gate = TeamsSourceGate()
        self.assertTrue(gate._learns_as_noise(150.0, 100.0, SYSTEM_ACTIVE_RMS_MIN))

    def test_anything_below_the_activity_minimum_is_always_a_noise_sample(self):
        """This is what lets a fresh gate bootstrap, and what keeps a genuinely
        quiet room tracking correctly."""
        gate = TeamsSourceGate()
        self.assertTrue(
            gate._learns_as_noise(
                float(SYSTEM_ACTIVE_RMS_MIN) - 1.0, 1000.0, SYSTEM_ACTIVE_RMS_MIN
            )
        )

    def test_an_empty_frame_teaches_nothing(self):
        gate = TeamsSourceGate()
        self.assertFalse(gate._learns_as_noise(0.0, 100.0, SYSTEM_ACTIVE_RMS_MIN))


if __name__ == "__main__":
    unittest.main()
