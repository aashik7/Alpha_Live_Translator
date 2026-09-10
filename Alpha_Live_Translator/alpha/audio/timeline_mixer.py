"""Real-time mono 16 kHz timeline mixer for Deepgram (mix, do not concatenate)."""

import time

import numpy as np

from alpha.audio.processing import pcm_to_mono_16k_np
from alpha.audio.source_gate import TeamsSourceGate
from alpha.config import DEEPGRAM_SAMPLE_RATE, SOURCE_LIVENESS_WINDOW_S

# 20 ms frames at 16 kHz → 320 samples → 640 bytes (256 kbps when paced to real time)
FRAME_SAMPLES = int(DEEPGRAM_SAMPLE_RATE * 0.02)
# Cap at 3 s to avoid runaway backlog. This is ALSO the most old-device audio
# that can straddle a device swap, and the decision there is to play it out
# rather than discard it (see `buffered_system_seconds`). Shortening it would
# cut the worst-case swap latency, but it is not a free knob: `emit_due_frames`
# uses the same depth as its maximum catch-up burst, so a smaller value also
# reduces how much backlog one slow mixer tick can repay -- which is the
# regression this constant was raised to fix. Measure before changing it.
MAX_BUFFER_SAMPLES = DEEPGRAM_SAMPLE_RATE * 3
# One call may repay at most this much backlog. 3 s matches the buffer depth, so
# a single call can always drain everything the buffers still hold, while a
# stalled caller cannot burst without bound.
_MAX_FRAMES_PER_EMIT = int(MAX_BUFFER_SAMPLES / FRAME_SAMPLES)


class DeepgramTimelineMixer:
    """Align system + mic on one mono 16 kHz timeline; one frame per wall-clock tick."""

    def __init__(self):
        self._wasapi_channels = 2
        self._wasapi_rate = 48000
        self._sys_buffer = np.array([], dtype=np.int16)
        self._mic_buffer = np.array([], dtype=np.int16)
        self._next_frame_time = None
        self._sys_source_available = False
        self._mic_source_available = False
        # WHEN each source last delivered. The `_available` flags above latch
        # True for the life of the session -- they are consumed by the source
        # gate and the evidence writers, so their meaning must not change --
        # and that makes them useless for telling a live source from one that
        # stopped minutes ago. These carry that second, different fact.
        self._sys_last_chunk_mono = 0.0
        self._mic_last_chunk_mono = 0.0
        self._source_gate = TeamsSourceGate()

    def reset(self):
        self._sys_buffer = np.array([], dtype=np.int16)
        self._mic_buffer = np.array([], dtype=np.int16)
        self._next_frame_time = None
        self._sys_source_available = False
        self._mic_source_available = False
        self._sys_last_chunk_mono = 0.0
        self._mic_last_chunk_mono = 0.0
        self._source_gate.reset()

    def buffered_system_seconds(self):
        """How much old-device audio is still queued to be played out.

        R3: at a device swap `_sys_buffer` holds up to MAX_BUFFER_SAMPLES of
        audio captured on the PREVIOUS device. Since the per-chunk format stamp
        shipped those samples are format-safe, so this is a latency-versus-
        content choice rather than a correctness one, and the decision is to
        PLAY IT OUT -- keep draining normally and lose nothing. This method
        exists so that choice is visible in the evidence instead of implicit:
        an unrecorded choice here is either silent content loss or a silent
        multi-second lag, and neither is diagnosable after the fact.
        """
        return round(float(self._sys_buffer.size) / float(DEEPGRAM_SAMPLE_RATE), 3)

    def get_source_gate_summary(self):
        return self._source_gate.get_summary()

    def configure_sources(self, wasapi_channels, wasapi_rate, mic_available=True):
        """Supply the DEFAULT format, used only by a chunk that carries no stamp.

        This used to be the only source of truth, which made it shared mutable
        state: written on one thread, read on the mixer thread, and safe only
        because nothing writes it after startup. A chunk that arrives with its
        own format ignores these values entirely -- see `push_system`.
        """
        self._wasapi_channels = max(1, int(wasapi_channels or 1))
        self._wasapi_rate = max(1, int(wasapi_rate or DEEPGRAM_SAMPLE_RATE))
        self._mic_source_available = bool(mic_available)

    def set_mic_gate(self, gate_fn):
        """Legacy hook retained; Teams source gate owns mix decisions."""

    def push_system(self, chunk_bytes, channels=None, rate=None):
        """Resample one system-audio chunk with the format IT was captured at.

        `channels` / `rate` are the stamp the reader put on the chunk. They win
        over the configured defaults, which is what makes a device change safe
        by construction: a chunk captured on the old device and still in flight
        when the format moves on resamples correctly anyway, so there is no
        ordering requirement between the capture side and this one -- no drain,
        no flush, no lock.

        Resampling with the wrong format is this project's worst failure shape:
        measured on a 1.000 s 440 Hz tone at 48 kHz stereo, being told 1 channel
        yields 2.000 s at 220 Hz, nothing raises, no counter moves, and Deepgram
        transcribes the half-speed audio fluently and wrongly.
        """
        if not chunk_bytes:
            return
        mono = pcm_to_mono_16k_np(
            chunk_bytes,
            self._wasapi_channels if channels is None else max(1, int(channels)),
            self._wasapi_rate if rate is None else max(1, int(rate)),
        )
        if mono.size == 0:
            return
        self._sys_source_available = True
        self._sys_last_chunk_mono = time.monotonic()
        self._sys_buffer = np.concatenate((self._sys_buffer, mono))
        self._trim_buffer("_sys_buffer")

    def push_mic(self, chunk_bytes):
        if not chunk_bytes:
            return
        mic = np.frombuffer(chunk_bytes, dtype=np.int16).copy()
        if mic.size == 0:
            return
        self._mic_source_available = True
        self._mic_last_chunk_mono = time.monotonic()
        self._mic_buffer = np.concatenate((self._mic_buffer, mic))
        self._trim_buffer("_mic_buffer")

    @staticmethod
    def _source_is_live(last_chunk_mono):
        if not last_chunk_mono:
            return False
        return (time.monotonic() - last_chunk_mono) <= SOURCE_LIVENESS_WINDOW_S

    def _trim_buffer(self, attr):
        buf = getattr(self, attr)
        if buf.size > MAX_BUFFER_SAMPLES:
            setattr(self, attr, buf[-MAX_BUFFER_SAMPLES:])

    @staticmethod
    def _take_samples(buf, n):
        if buf.size >= n:
            return buf[:n].copy(), buf[n:]
        out = np.zeros(n, dtype=np.int16)
        if buf.size > 0:
            out[: buf.size] = buf
        return out, np.array([], dtype=np.int16)

    def _build_frame(self):
        sys_s, self._sys_buffer = self._take_samples(self._sys_buffer, FRAME_SAMPLES)
        mic_s, self._mic_buffer = self._take_samples(self._mic_buffer, FRAME_SAMPLES)

        sys_rms = float(np.sqrt(np.mean(sys_s.astype(np.float32) ** 2)))
        mic_rms = float(np.sqrt(np.mean(mic_s.astype(np.float32) ** 2)))

        decision = self._source_gate.evaluate(sys_rms, mic_rms)
        mixed = self._source_gate.mix_frame(sys_s, mic_s, decision)

        # Observational retention copies only — not attached to speaker meta
        # (avoids logging/snapshot bloat). Mix bytes unchanged.
        try:
            from alpha.utils.audio_temp_capture import ingest_audio_chunk

            ingest_audio_chunk(sys_s.tobytes(), stream_type="system")
            ingest_audio_chunk(mic_s.tobytes(), stream_type="mic")
        except Exception:
            pass
        meta = {
            "system_source_available": bool(self._sys_source_available),
            "mic_source_available": bool(self._mic_source_available),
            # Availability LATCHES; liveness does not. A source that stopped
            # delivering still reads available, which is how a dead microphone
            # or a rebind gap looks identical to a working one in the evidence.
            # Not a silence detector: a quiet room still delivers chunks of
            # zeros, so nothing arriving means the DEVICE stopped.
            "system_source_live": self._source_is_live(self._sys_last_chunk_mono),
            "mic_source_live": self._source_is_live(self._mic_last_chunk_mono),
            "speaker_detection_method": decision.get("speaker_detection_method"),
            "speaker_label": decision.get("speaker_label"),
            "chosen_source": decision.get("chosen_source"),
            "decision_reason": decision.get("decision_reason"),
            "used_pre_mix_audio": True,
            "sys_rms": decision.get("system_rms"),
            "mic_rms": decision.get("mic_rms"),
            "system_noise_floor": decision.get("system_noise_floor"),
            "mic_noise_floor": decision.get("mic_noise_floor"),
            "system_threshold": decision.get("system_threshold"),
            "mic_threshold": decision.get("mic_threshold"),
            "mic_to_system_ratio": decision.get("mic_to_system_ratio"),
            "system_active": decision.get("system_active"),
            "mic_active": decision.get("mic_active"),
            "overlap_candidate": decision.get("overlap_candidate"),
            "overlap_confirm_count": decision.get("overlap_confirm_count"),
            "overlap_detected": decision.get("overlap_detected"),
            "previous_source": decision.get("previous_source"),
        }
        return mixed.tobytes(), meta

    def ingest_queues(self, sys_queue, mic_queue):
        """Non-blocking drain of capture queues into timeline buffers."""
        if sys_queue is not None:
            while True:
                try:
                    chunk = sys_queue.get_nowait()
                except Exception:
                    break
                # A stamped chunk is `(pcm, channels, rate)`; bare bytes are
                # still accepted and fall back to the configured defaults, so
                # anything that enqueues raw PCM keeps working.
                if isinstance(chunk, tuple) and len(chunk) == 3:
                    self.push_system(chunk[0], chunk[1], chunk[2])
                else:
                    self.push_system(chunk)
                try:
                    from alpha.utils.runtime_audio_counters import note_system_audio_chunk_received

                    note_system_audio_chunk_received()
                except Exception:
                    pass
        if mic_queue is not None:
            while True:
                try:
                    chunk = mic_queue.get_nowait()
                except Exception:
                    break
                self.push_mic(chunk)
                try:
                    from alpha.utils.runtime_audio_counters import note_microphone_chunk_received

                    note_microphone_chunk_received()
                except Exception:
                    pass

    def emit_due_frames(self):
        """Yield every mono 16 kHz PCM frame now due by wall clock.

        This used to return at most ONE frame per call while still advancing the
        clock by exactly one frame, so a caller running slower than 50 Hz could
        never catch up: measured, a caller ticking every 100 ms emitted 10 frames
        per second against 50 due, and the other 800 ms of audio per second was
        left in the buffer until `_trim_buffer` discarded it. The lag was
        permanent, because each call only ever repaid one frame of it.

        Emitting all due frames repays the backlog immediately. The per-call cap
        bounds the burst so one slow tick cannot monopolise the loop.
        """
        now = time.monotonic()
        if self._next_frame_time is None:
            self._next_frame_time = now

        frame_seconds = FRAME_SAMPLES / float(DEEPGRAM_SAMPLE_RATE)
        max_lag_seconds = MAX_BUFFER_SAMPLES / float(DEEPGRAM_SAMPLE_RATE)

        # Beyond the buffer's own depth the missing audio has already been
        # trimmed away, so continuing to count those frames would only emit
        # zero-padded silence for speech that no longer exists. Resync instead,
        # and let the buffer contents describe the timeline from here.
        if now - self._next_frame_time > max_lag_seconds:
            self._next_frame_time = now - max_lag_seconds

        emitted = []
        while now >= self._next_frame_time and len(emitted) < _MAX_FRAMES_PER_EMIT:
            pcm, meta = self._build_frame()
            self._next_frame_time += frame_seconds
            emitted.append((pcm, meta))
            try:
                from alpha.utils.runtime_audio_counters import (
                    note_mixed_audio_chunk_created,
                )

                note_mixed_audio_chunk_created()
            except Exception:
                pass
        return emitted

    def sleep_until_next_frame(self):
        if self._next_frame_time is None:
            return 0.005
        delay = self._next_frame_time - time.monotonic()
        return max(0.001, min(delay, 0.05))
