"""Real-time mono 16 kHz timeline mixer for Deepgram (mix, do not concatenate)."""

import time

import numpy as np

from alpha.audio.processing import pcm_to_mono_16k_np
from alpha.audio.source_gate import TeamsSourceGate
from alpha.config import DEEPGRAM_SAMPLE_RATE

# 20 ms frames at 16 kHz → 320 samples → 640 bytes (256 kbps when paced to real time)
FRAME_SAMPLES = int(DEEPGRAM_SAMPLE_RATE * 0.02)
MAX_BUFFER_SAMPLES = DEEPGRAM_SAMPLE_RATE * 3  # cap at 3 s to avoid runaway backlog


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
        self._source_gate = TeamsSourceGate()

    def reset(self):
        self._sys_buffer = np.array([], dtype=np.int16)
        self._mic_buffer = np.array([], dtype=np.int16)
        self._next_frame_time = None
        self._sys_source_available = False
        self._mic_source_available = False
        self._source_gate.reset()

    def get_source_gate_summary(self):
        return self._source_gate.get_summary()

    def configure_sources(self, wasapi_channels, wasapi_rate, mic_available=True):
        self._wasapi_channels = max(1, int(wasapi_channels or 1))
        self._wasapi_rate = max(1, int(wasapi_rate or DEEPGRAM_SAMPLE_RATE))
        self._mic_source_available = bool(mic_available)

    def set_mic_gate(self, gate_fn):
        """Legacy hook retained; Teams source gate owns mix decisions."""

    def push_system(self, chunk_bytes):
        if not chunk_bytes:
            return
        mono = pcm_to_mono_16k_np(
            chunk_bytes, self._wasapi_channels, self._wasapi_rate
        )
        if mono.size == 0:
            return
        self._sys_source_available = True
        self._sys_buffer = np.concatenate((self._sys_buffer, mono))
        self._trim_buffer("_sys_buffer")

    def push_mic(self, chunk_bytes):
        if not chunk_bytes:
            return
        mic = np.frombuffer(chunk_bytes, dtype=np.int16).copy()
        if mic.size == 0:
            return
        self._mic_source_available = True
        self._mic_buffer = np.concatenate((self._mic_buffer, mic))
        self._trim_buffer("_mic_buffer")

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

        meta = {
            "system_source_available": bool(self._sys_source_available),
            "mic_source_available": bool(self._mic_source_available),
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
        """Yield at most one mono 16 kHz PCM frame if due by wall clock (real-time paced)."""
        now = time.monotonic()
        if self._next_frame_time is None:
            self._next_frame_time = now

        if now < self._next_frame_time:
            return []

        pcm, meta = self._build_frame()
        self._next_frame_time += FRAME_SAMPLES / float(DEEPGRAM_SAMPLE_RATE)
        try:
            from alpha.utils.runtime_audio_counters import note_mixed_audio_chunk_created

            note_mixed_audio_chunk_created()
        except Exception:
            pass
        return [(pcm, meta)]

    def sleep_until_next_frame(self):
        if self._next_frame_time is None:
            return 0.005
        delay = self._next_frame_time - time.monotonic()
        return max(0.001, min(delay, 0.05))
