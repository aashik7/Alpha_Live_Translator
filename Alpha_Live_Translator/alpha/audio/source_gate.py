"""Conservative Teams source gate — suppress mic echo / false overlap."""

import time

from alpha.constants import (
    MIC_ACTIVE_RMS_MIN,
    MIC_NOISE_MULTIPLIER,
    MIC_TO_SYSTEM_RATIO_MIN,
    OVERLAP_CONFIRM_FRAMES,
    SOURCE_HOLD_MS,
    SYSTEM_ACTIVE_RMS_MIN,
    SYSTEM_NOISE_MULTIPLIER,
)

_NOISE_EMA_ALPHA = 0.05

# How far above the current floor a frame may sit and still be believed to be
# background. Anything louder is speech the threshold misjudged, and learning
# from it is what let the floor run away (see `_learns_as_noise`). Chosen to sit
# below the activity multipliers themselves (system 3.0, mic 4.0) so a frame can
# never both fail the activity test and be treated as a clean noise sample.
_NOISE_LEARN_MAX_RATIO = 2.0


class TeamsSourceGate:
    """Per-frame system/mic activity with overlap confirmation and source hold."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._sys_noise_floor = 0.0
        self._mic_noise_floor = 0.0
        self._overlap_confirm_count = 0
        self._chosen_source = "none"
        self._previous_source = "none"
        self._source_hold_until = 0.0
        self._stats = {
            "system_count": 0,
            "mic_count": 0,
            "mixed_count": 0,
            "none_count": 0,
            "false_overlap_prevented_count": 0,
            "total_source_checks": 0,
        }

    def get_summary(self):
        return dict(self._stats)

    def _learns_as_noise(self, rms, floor, activity_min) -> bool:
        """True when this frame can be believed to be background, not speech.

        The floor is only ever updated from frames judged INACTIVE, and a frame
        is judged inactive by comparing it against a threshold derived from the
        floor itself. That is a positive feedback loop: once the threshold drifts
        above real speech, the speech it wrongly rejects is fed back in as
        "noise", which raises the floor, which rejects more speech.

        It ran away in production. Measured over the live run of 2026-08-20 the
        system floor climbed 23 -> 946 -> 1432 -> 1872 and the threshold reached
        5616, while the frame RMS distribution for that audio was p5 125,
        p50 1343, p90 2709. A threshold of 5616 gates off 99% of frames; the
        80 minimum gates off 3%, which is the true silence in that recording.

        Two ways a frame earns the right to teach the floor, and no third:

        1. It is quieter than `activity_min`. Nothing below the absolute
           activity minimum can be speech, so it is always a safe noise sample.
           This is also what lets a genuinely noisy room bootstrap from zero.
        2. It is within `_NOISE_LEARN_MAX_RATIO` of the floor already learned.
           Real background stays near its own level; a frame many times louder
           is speech the threshold misjudged, and that is exactly the sample
           that made the loop run away.
        """
        rms = float(rms or 0.0)
        if rms <= 0:
            return False
        if rms < float(activity_min):
            return True
        if floor <= 0:
            return True
        return rms <= float(floor) * _NOISE_LEARN_MAX_RATIO

    def _update_noise_floors(self, sys_rms, mic_rms, system_active, mic_active):
        if not system_active and self._learns_as_noise(
            sys_rms, self._sys_noise_floor, SYSTEM_ACTIVE_RMS_MIN
        ):
            if self._sys_noise_floor <= 0:
                self._sys_noise_floor = float(sys_rms)
            else:
                self._sys_noise_floor = (
                    (1.0 - _NOISE_EMA_ALPHA) * self._sys_noise_floor
                    + _NOISE_EMA_ALPHA * float(sys_rms)
                )
        if not mic_active and self._learns_as_noise(
            mic_rms, self._mic_noise_floor, MIC_ACTIVE_RMS_MIN
        ):
            if self._mic_noise_floor <= 0:
                self._mic_noise_floor = float(mic_rms)
            else:
                self._mic_noise_floor = (
                    (1.0 - _NOISE_EMA_ALPHA) * self._mic_noise_floor
                    + _NOISE_EMA_ALPHA * float(mic_rms)
                )

    def _apply_hold(self, raw_source, raw_reason, sys_rms, mic_rms, now):
        if raw_source == self._chosen_source:
            return raw_source, raw_reason
        if self._chosen_source == "none" or now >= self._source_hold_until:
            self._previous_source = self._chosen_source
            self._chosen_source = raw_source
            self._source_hold_until = now + (SOURCE_HOLD_MS / 1000.0)
            return raw_source, raw_reason

        clearly_stronger = False
        if raw_source == "system" and sys_rms >= mic_rms * 3.0:
            clearly_stronger = True
        elif raw_source == "mic" and mic_rms >= sys_rms * 3.0:
            clearly_stronger = True
        elif raw_source == "mixed":
            clearly_stronger = self._overlap_confirm_count >= OVERLAP_CONFIRM_FRAMES

        if clearly_stronger:
            self._previous_source = self._chosen_source
            self._chosen_source = raw_source
            self._source_hold_until = now + (SOURCE_HOLD_MS / 1000.0)
            return raw_source, raw_reason

        return self._chosen_source, "source_hold_previous"

    def evaluate(self, sys_rms: float, mic_rms: float, now=None):
        """Return source decision and mix metadata for one 20 ms frame."""
        now = time.monotonic() if now is None else now
        sys_rms = float(sys_rms or 0.0)
        mic_rms = float(mic_rms or 0.0)

        sys_threshold = max(
            SYSTEM_ACTIVE_RMS_MIN,
            self._sys_noise_floor * SYSTEM_NOISE_MULTIPLIER,
        )
        mic_threshold = max(
            MIC_ACTIVE_RMS_MIN,
            self._mic_noise_floor * MIC_NOISE_MULTIPLIER,
        )

        system_active = sys_rms >= sys_threshold
        mic_above_threshold = mic_rms >= mic_threshold
        mic_to_system_ratio = mic_rms / max(sys_rms, 1.0)

        if system_active:
            mic_active = mic_above_threshold and (
                mic_to_system_ratio >= MIC_TO_SYSTEM_RATIO_MIN
            )
        else:
            mic_active = mic_above_threshold

        overlap_candidate = system_active and mic_above_threshold

        if system_active and mic_active:
            self._overlap_confirm_count += 1
        else:
            self._overlap_confirm_count = 0

        self._update_noise_floors(sys_rms, mic_rms, system_active, mic_active)

        if (
            system_active
            and mic_active
            and self._overlap_confirm_count >= OVERLAP_CONFIRM_FRAMES
        ):
            raw_source = "mixed"
            raw_reason = "confirmed_overlap"
        elif system_active and not mic_active:
            raw_source = "system"
            if mic_above_threshold:
                raw_reason = "system_active_mic_ratio_too_low_echo"
            else:
                raw_reason = "system_active_mic_below_threshold"
        elif system_active and overlap_candidate:
            raw_source = "system"
            raw_reason = "system_active_mic_ratio_too_low_echo"
        elif mic_active:
            raw_source = "mic"
            raw_reason = "mic_active_system_inactive"
        else:
            raw_source = "none"
            raw_reason = "no_active_source"

        if overlap_candidate and raw_source != "mixed":
            self._stats["false_overlap_prevented_count"] += 1

        chosen_source, decision_reason = self._apply_hold(
            raw_source, raw_reason, sys_rms, mic_rms, now
        )

        overlap_detected = chosen_source == "mixed"

        self._stats["total_source_checks"] += 1
        count_key = f"{chosen_source}_count"
        if count_key in self._stats:
            self._stats[count_key] += 1

        method_map = {
            "system": "teams_gate_system_only",
            "mic": "teams_gate_mic_only",
            "mixed": "teams_gate_weighted_mix",
            "none": "teams_gate_silence",
        }

        return {
            "chosen_source": chosen_source,
            "speaker_label": chosen_source,
            "speaker_detection_method": method_map.get(chosen_source, "teams_gate"),
            "decision_reason": decision_reason,
            "system_rms": round(sys_rms, 2),
            "mic_rms": round(mic_rms, 2),
            "system_noise_floor": round(self._sys_noise_floor, 2),
            "mic_noise_floor": round(self._mic_noise_floor, 2),
            "system_threshold": round(sys_threshold, 2),
            "mic_threshold": round(mic_threshold, 2),
            "mic_to_system_ratio": round(mic_to_system_ratio, 4),
            "system_active": system_active,
            "mic_active": mic_active,
            "overlap_candidate": overlap_candidate,
            "overlap_confirm_count": self._overlap_confirm_count,
            "overlap_detected": overlap_detected,
            "previous_source": self._previous_source,
            "used_pre_mix_audio": True,
        }

    def mix_frame(self, sys_samples, mic_samples, decision: dict):
        """Mix one frame using gate decision; suppress inactive mic echo."""
        import numpy as np

        source = decision.get("chosen_source", "none")
        sys_rms = float(decision.get("system_rms") or 0.0)
        mic_rms = float(decision.get("mic_rms") or 0.0)

        if source == "mixed":
            total = sys_rms + mic_rms + 1e-6
            sys_w = max(0.4, min(0.8, sys_rms / total))
            mic_w = 1.0 - sys_w
            mixed = sys_samples.astype(np.float32) * sys_w + mic_samples.astype(
                np.float32
            ) * mic_w
            return np.clip(mixed, -32768, 32767).astype(np.int16)

        if source == "system":
            return sys_samples.astype(np.int16)

        if source == "mic":
            return mic_samples.astype(np.int16)

        # "none" means neither source cleared its threshold. It must NOT mean
        # "send silence to the transcriber".
        #
        # This gate exists to LABEL a frame (system / mic / overlap) for speaker
        # attribution. Returning zeros made it destroy audio instead, and a
        # wrong label then became permanent word loss. Measured on the live run
        # of 2026-08-20: the delivered stream was 52-82% silence from two
        # minutes in, Deepgram returned 43% of the reference words, and the app
        # kept 96.5% of what Deepgram gave it -- so essentially all of the loss
        # was created right here.
        #
        # Failing OPEN is strictly safer than failing closed:
        #   * when the frame really is silence, `sys_samples` IS silence, so the
        #     delivered bytes are unchanged;
        #   * when the gate is wrong, the speech survives.
        # There is no case where emitting zeros is better than emitting the
        # captured system audio, and Deepgram runs its own VAD on top.
        #
        # The louder source is chosen so a mic-only moment is not dropped when
        # system capture is idle.
        if float(mic_rms) > float(sys_rms):
            return mic_samples.astype(np.int16)
        return sys_samples.astype(np.int16)
