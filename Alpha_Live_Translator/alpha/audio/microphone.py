"""Microphone capture via sounddevice."""

import os

from alpha.config import DEEPGRAM_SAMPLE_RATE, MIC_BLOCKSIZE
from alpha.utils.queues import put_bounded


def _import_sounddevice():
    """Lazy import — sounddevice init is expensive on Windows startup."""
    import sounddevice as sd

    return sd


# Baseline profiling only: force module-level import to restore pre-repair cost.
if os.environ.get("ALPHA_STARTUP_EAGER_SOUNDDEVICE", "").strip().lower() in (
    "1",
    "true",
    "yes",
):
    _import_sounddevice()


class MicrophoneCaptureMixin:
    """Mixin providing default microphone capture methods."""

    def _mic_callback(self, indata, _frames, _time, status):
            """sounddevice callback — push raw mic PCM bytes to mic_audio_queue."""
            if status:
                print(status)
                try:
                    from alpha.utils.runtime_audio_counters import note_capture_error

                    note_capture_error()
                except Exception:
                    pass
            if self._stop_event.is_set() or self.mic_audio_queue is None:
                return
            raw = indata.tobytes()
            if raw and not put_bounded(self.mic_audio_queue, raw):
                print("Microphone audio queue full — dropped oldest chunk")
                try:
                    from alpha.utils.runtime_audio_counters import note_audio_queue_drop

                    note_audio_queue_drop()
                except Exception:
                    pass

    def _start_microphone_capture(self):
            """Start capturing the default microphone via sounddevice (16 kHz mono)."""
            try:
                sd = _import_sounddevice()
                device = sd.default.device[0]
                device_name = sd.query_devices(device).get("name", "unknown")
                print(f"Capturing from microphone: {device_name}")

                self._mic_stream = sd.InputStream(
                    device=device,
                    channels=1,
                    samplerate=DEEPGRAM_SAMPLE_RATE,
                    dtype="int16",
                    blocksize=MIC_BLOCKSIZE,
                    callback=self._mic_callback,
                )
                self._mic_stream.start()
                print("Microphone stream started successfully")
            except Exception as exc:
                print(f"Microphone capture error: {exc}")
                try:
                    from alpha.utils.runtime_audio_counters import note_capture_error

                    note_capture_error()
                except Exception:
                    pass
                raise

    def _close_microphone_stream(self):
            """Stop and release the sounddevice microphone stream."""
            if self._mic_stream is not None:
                try:
                    self._mic_stream.stop()
                    self._mic_stream.close()
                except Exception as exc:
                    print(f"Error closing microphone stream: {exc}")
                self._mic_stream = None
