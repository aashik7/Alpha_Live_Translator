"""WASAPI loopback system audio capture via pyaudiowpatch."""

import threading
import time

import pyaudiowpatch as pyaudio
from tkinter import messagebox

from alpha.config import WASAPI_FRAMES_PER_BUFFER
from alpha.utils.queues import put_bounded


class WasapiCaptureMixin:
    """Mixin providing WASAPI loopback capture methods."""

    def _get_wasapi_loopback_device(self):
        """Get the default WASAPI loopback device via pyaudiowpatch."""
        pa = pyaudio.PyAudio()
        try:
            loopback = pa.get_default_wasapi_loopback()
            if loopback is None:
                raise RuntimeError("No default WASAPI loopback device available.")
            print(f"Found WASAPI loopback device: {loopback.get('name', 'unknown')}")
            return pa, loopback
        except Exception:
            pa.terminate()
            raise

    def _wasapi_reader_worker(self):
        """Blocking read loop — more reliable than callbacks on some Python builds."""
        idle_polls = 0
        while not self._stop_event.is_set():
            try:
                stream = self._wasapi_stream
                if stream is None or not stream.is_active():
                    break

                frames = self._wasapi_frames_per_buffer
                available = stream.get_read_available()
                if available >= frames:
                    data = stream.read(frames, exception_on_overflow=False)
                    if data and self.sys_audio_queue is not None:
                        put_bounded(self.sys_audio_queue, data)
                    idle_polls = 0
                else:
                    idle_polls += 1
                    if idle_polls == 500:
                        print(
                            "[WASAPI] No loopback audio yet — play system audio "
                            "or speak into the microphone."
                        )
                        idle_polls = 0
                    time.sleep(0.01)
            except Exception as exc:
                if not self._stop_event.is_set():
                    print(f"[WASAPI] Reader error: {exc}")
                break

    def _start_wasapi_loopback(self):
        """Start capturing all system audio via WASAPI loopback (zero user setup)."""
        try:
            self._pyaudio, loopback = self._get_wasapi_loopback_device()
            self._wasapi_channels = int(loopback["maxInputChannels"])
            self._wasapi_rate = int(loopback["defaultSampleRate"])
            self._wasapi_frames_per_buffer = WASAPI_FRAMES_PER_BUFFER

            print("Starting WASAPI loopback audio capture...")
            print(
                f"Capturing from device: {loopback.get('name', 'unknown')} "
                f"({self._wasapi_rate} Hz, {self._wasapi_channels} ch)"
            )

            self._wasapi_stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=self._wasapi_channels,
                rate=self._wasapi_rate,
                input=True,
                frames_per_buffer=self._wasapi_frames_per_buffer,
                input_device_index=loopback["index"],
            )
            self._wasapi_stream.start_stream()
            self._wasapi_reader_thread = threading.Thread(
                target=self._wasapi_reader_worker,
                name="WasapiReader",
                daemon=True,
            )
            self._wasapi_reader_thread.start()
            print("WASAPI loopback stream started successfully")
        except Exception as exc:
            print(f"WASAPI loopback error: {exc}")
            self._close_wasapi_stream()
            messagebox.showerror(
                "WASAPI Loopback Error",
                "Could not capture system audio automatically.\n\n"
                "Please check Windows Sound settings and ensure your default "
                "playback/speaker device is working.\n\n"
                f"Details: {exc}",
            )
            raise

    def _close_wasapi_stream(self):
        """Stop and release WASAPI loopback resources."""
        if self._wasapi_stream is not None:
            try:
                if self._wasapi_stream.is_active():
                    self._wasapi_stream.stop_stream()
                self._wasapi_stream.close()
            except Exception as exc:
                print(f"Error closing WASAPI stream: {exc}")
            self._wasapi_stream = None

        reader = getattr(self, "_wasapi_reader_thread", None)
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.0)
        self._wasapi_reader_thread = None

        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception as exc:
                print(f"Error terminating PyAudio: {exc}")
            self._pyaudio = None
