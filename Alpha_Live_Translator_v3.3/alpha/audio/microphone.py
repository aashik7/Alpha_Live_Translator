"""Microphone capture via sounddevice."""

import sounddevice as sd

from alpha.config import DEEPGRAM_SAMPLE_RATE, MIC_BLOCKSIZE
from alpha.utils.queues import put_bounded


class MicrophoneCaptureMixin:
    """Mixin providing default microphone capture methods."""

    def _mic_callback(self, indata, _frames, _time, status):
            """sounddevice callback — push raw mic PCM bytes to mic_audio_queue."""
            if status:
                print(status)
            if self._stop_event.is_set() or self.mic_audio_queue is None:
                return
            raw = indata.tobytes()
            if raw and not put_bounded(self.mic_audio_queue, raw):
                print("Microphone audio queue full — dropped oldest chunk")

    def _start_microphone_capture(self):
            """Start capturing the default microphone via sounddevice (16 kHz mono)."""
            try:
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
