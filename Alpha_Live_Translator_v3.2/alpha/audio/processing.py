"""PCM conversion, resampling, and mixing helpers."""

import numpy as np

from alpha.config import DEEPGRAM_SAMPLE_RATE, MIC_NOISE_GATE_INITIAL_RMS


def pcm_to_mono_16k_np(audio_chunk_bytes, channels=2, sample_rate=48000):
    """Convert PCM bytes to a 16 kHz mono int16 numpy array (numpy-only, no audioop)."""
    if not audio_chunk_bytes:
        return np.array([], dtype=np.int16)

    audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16)

    if channels > 1:
        frame_count = len(audio_np) // channels
        if frame_count == 0:
            return np.array([], dtype=np.int16)
        audio_np = audio_np[: frame_count * channels].reshape(-1, channels)
        audio_np = audio_np.mean(axis=1).astype(np.int16)

    if sample_rate != DEEPGRAM_SAMPLE_RATE and len(audio_np) > 0:
        target_len = int(len(audio_np) * DEEPGRAM_SAMPLE_RATE / sample_rate)  # CHANGED: linear interp target (fix 2)
        if target_len < 1:  # CHANGED: guard empty output (fix 2)
            return np.array([], dtype=np.int16)  # CHANGED: (fix 2)
        indices = np.linspace(0, len(audio_np) - 1, target_len)  # CHANGED: linear interpolation (fix 2)
        audio_np = np.interp(  # CHANGED: replace integer stride resampling (fix 2)
            indices,  # CHANGED: (fix 2)
            np.arange(len(audio_np)),  # CHANGED: (fix 2)
            audio_np.astype(np.float32),  # CHANGED: (fix 2)
        ).astype(np.int16)  # CHANGED: (fix 2)

    return audio_np.astype(np.int16)


def apply_noise_gate(audio_np, threshold=MIC_NOISE_GATE_INITIAL_RMS):
    """Silence microphone chunks below RMS threshold to prevent speaker echo."""
    if audio_np.size == 0:
        return audio_np
    rms = float(np.sqrt(np.mean(audio_np.astype(np.float64) ** 2)))
    if rms < threshold:
        return np.zeros_like(audio_np)
    return audio_np


def mix_audio_chunks(system_np, mic_np):
    """Sum system + mic arrays (same length) with 16-bit clipping."""
    if system_np.size == 0 and mic_np.size == 0:
        return np.array([], dtype=np.int16)
    if system_np.size == 0:
        return mic_np.astype(np.int16)
    if mic_np.size == 0:
        return system_np.astype(np.int16)
    target_len = max(system_np.size, mic_np.size)
    if system_np.size < target_len:
        system_np = np.pad(system_np, (0, target_len - system_np.size))
    if mic_np.size < target_len:
        mic_np = np.pad(mic_np, (0, target_len - mic_np.size))
    mixed = system_np.astype(np.int32) + mic_np.astype(np.int32)
    return np.clip(mixed, -32768, 32767).astype(np.int16)


def process_audio_chunk(audio_chunk_bytes, channels=2, sample_rate=48000):
    """Convert PCM bytes to 16 kHz mono int16 bytes for Deepgram."""
    return pcm_to_mono_16k_np(audio_chunk_bytes, channels, sample_rate).tobytes()


def ensure_deepgram_pcm_bytes(chunk_bytes):
    """Final-boundary guard: signed int16 LE mono payload for Deepgram."""
    if not chunk_bytes:
        return b"", 0
    nbytes = len(chunk_bytes)
    if nbytes % 2 != 0:
        chunk_bytes = chunk_bytes[: nbytes - 1]
    samples = len(chunk_bytes) // 2
    return chunk_bytes, samples


def pcm_duration_ms(sample_count, sample_rate=16000):
    if sample_rate <= 0 or sample_count <= 0:
        return 0.0
    return round((sample_count / float(sample_rate)) * 1000.0, 2)
