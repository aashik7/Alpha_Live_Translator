"""Real-time mono 16 kHz timeline mixer for Deepgram (mix, do not concatenate)."""

import time

import numpy as np

from alpha.audio.processing import pcm_to_mono_16k_np
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
        self._mic_gate_fn = None
        self._sys_source_available = False
        self._mic_source_available = False

    def reset(self):
        self._sys_buffer = np.array([], dtype=np.int16)
        self._mic_buffer = np.array([], dtype=np.int16)
        self._next_frame_time = None
        self._sys_source_available = False
        self._mic_source_available = False

    def configure_sources(self, wasapi_channels, wasapi_rate, mic_available=True):
        self._wasapi_channels = max(1, int(wasapi_channels or 1))
        self._wasapi_rate = max(1, int(wasapi_rate or DEEPGRAM_SAMPLE_RATE))
        self._mic_source_available = bool(mic_available)

    def set_mic_gate(self, gate_fn):
        """gate_fn(mic_np: np.ndarray) -> bool"""
        self._mic_gate_fn = gate_fn

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

    @staticmethod
    def _mix_weighted(sys_s, mic_s, sys_w, mic_w):
        mixed = sys_s.astype(np.float32) * sys_w + mic_s.astype(np.float32) * mic_w
        return np.clip(mixed, -32768, 32767).astype(np.int16)

    def _build_frame(self):
        sys_s, self._sys_buffer = self._take_samples(self._sys_buffer, FRAME_SAMPLES)
        mic_s, self._mic_buffer = self._take_samples(self._mic_buffer, FRAME_SAMPLES)

        mic_passes = bool(self._mic_gate_fn(mic_s)) if self._mic_gate_fn else False
        sys_rms = float(np.sqrt(np.mean(sys_s.astype(np.float32) ** 2)))
        mic_rms = float(np.sqrt(np.mean(mic_s.astype(np.float32) ** 2))) if mic_passes else 0.0

        has_sys = sys_rms > 1.0
        has_mic = mic_passes and mic_rms > 1.0

        if has_sys and has_mic:
            total = sys_rms + mic_rms + 1e-6
            sys_w = max(0.4, min(0.8, sys_rms / total))
            mic_w = 1.0 - sys_w
            mixed = self._mix_weighted(sys_s, mic_s, sys_w, mic_w)
            method = "pre_mix_weighted_mix"
            label = "mixed"
        elif has_sys:
            mixed = sys_s
            method = "pre_mix_system_only"
            label = "system"
        elif has_mic:
            mixed = mic_s
            method = "pre_mix_mic_only"
            label = "mic"
        else:
            mixed = np.zeros(FRAME_SAMPLES, dtype=np.int16)
            method = "pre_mix_silence"
            label = "none"

        meta = {
            "system_source_available": bool(self._sys_source_available),
            "mic_source_available": bool(self._mic_source_available),
            "speaker_detection_method": method,
            "speaker_label": label,
            "used_pre_mix_audio": True,
            "sys_rms": round(sys_rms, 2),
            "mic_rms": round(mic_rms, 2),
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
        if mic_queue is not None:
            while True:
                try:
                    chunk = mic_queue.get_nowait()
                except Exception:
                    break
                self.push_mic(chunk)

    def emit_due_frames(self):
        """Yield at most one mono 16 kHz PCM frame if due by wall clock (real-time paced)."""
        now = time.monotonic()
        if self._next_frame_time is None:
            self._next_frame_time = now

        if now < self._next_frame_time:
            return []

        pcm, meta = self._build_frame()
        self._next_frame_time += FRAME_SAMPLES / float(DEEPGRAM_SAMPLE_RATE)
        return [(pcm, meta)]

    def sleep_until_next_frame(self):
        if self._next_frame_time is None:
            return 0.005
        delay = self._next_frame_time - time.monotonic()
        return max(0.001, min(delay, 0.05))
