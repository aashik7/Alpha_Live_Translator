"""Microphone capture via sounddevice."""

import os
import time

from alpha.config import DEEPGRAM_SAMPLE_RATE, MIC_BLOCKSIZE, MIC_REBIND_COOLDOWN_S
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
                # Baseline the endpoint we are now BOUND to, so the device
                # watcher can tell "the default moved away from our mic" from
                # "the default is still ours". Taken from the OS rather than
                # from `device`, because a PortAudio index is neither stable nor
                # unique -- the same reasoning as the render side.
                self._mic_default_endpoint_baseline = (
                    self._read_default_capture_endpoint_id()
                )
                self._mic_device_change_reported = False
                print("Microphone stream started successfully")
            except Exception as exc:
                print(f"Microphone capture error: {exc}")
                try:
                    from alpha.utils.runtime_audio_counters import note_capture_error

                    note_capture_error()
                except Exception:
                    pass
                raise

    def _read_default_capture_endpoint_id(self):
        """Current default INPUT endpoint, or "" if it cannot be read."""
        try:
            from alpha.audio.default_endpoint import read_default_capture_endpoint_id

            return read_default_capture_endpoint_id()
        except Exception:
            return ""

    def _report_default_input_device_changed(self, baseline, current):
        """The default microphone moved. Schedule a rebind; never run it here.

        This is called from the device-watch thread. The rebind re-initialises
        PortAudio process-wide, so it is marshalled to the UI thread -- the same
        thread the WASAPI rebind runs on -- so two PortAudio inits can never run
        concurrently from different threads. A headset plug moves BOTH defaults,
        so both rebinds are routinely in flight at once.
        """
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "AUDIO_INPUT_DEVICE_CHANGED",
                baseline_endpoint_id=str(baseline or ""),
                current_endpoint_id=str(current or ""),
            )
        except Exception:
            pass
        runner = getattr(self, "_run_on_ui_thread", None)
        if not callable(runner):
            return
        try:
            runner(self._rebind_microphone_to_default_device)
        except Exception:
            pass

    def _report_default_input_device_restored(self):
        """The default came back to the microphone we capture from."""
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("AUDIO_INPUT_DEVICE_RESTORED")
        except Exception:
            pass

    def _rebind_microphone_to_default_device(self):
        """Reopen the mic on whatever is now the default input. UI thread only.

        `sd.default.device[0]` is read at open time, but PortAudio snapshots the
        device list at initialisation -- the same reason the WASAPI side has to
        terminate and re-create PyAudio. So closing and reopening alone would
        rebind to the OLD device and look like a success. `_terminate()` +
        `_initialize()` is what makes the new default visible; measured at
        22.2 ms, cheap enough to run here.

        That last step is the one thing not proven without hardware: it is the
        same inference item 73 already rests on for the render side, where a
        second PyAudio while the first was alive was measured returning in
        0.048 ms with an identical index.
        """
        if getattr(self, "_mic_rebind_in_progress", False):
            return False
        # A microphone that is not running is not a microphone to follow. Both
        # cases are supported and deliberate: the operator turned it off with
        # the UI switch, or the system-audio-only benchmark is active. A device
        # change must never switch capture of someone's voice back on.
        if not getattr(self, "_microphone_capture_enabled", True):
            return False
        if getattr(self, "_mic_stream", None) is None:
            return False
        now = time.monotonic()
        last = float(getattr(self, "_mic_last_rebind_mono", 0.0) or 0.0)
        if last and (now - last) < MIC_REBIND_COOLDOWN_S:
            return False
        self._mic_rebind_in_progress = True
        self._mic_last_rebind_mono = now

        def _log(event, **fields):
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(event, **fields)
            except Exception:
                pass

        started = time.monotonic()
        try:
            _log("AUDIO_INPUT_REBIND_STARTED")
            self._close_microphone_stream()
            sd = _import_sounddevice()
            sd._terminate()
            sd._initialize()
            self._start_microphone_capture()
        except Exception as exc:  # noqa: BLE001
            # Never fatal. The session keeps running on system audio, exactly as
            # it does when the mic fails to open at Start -- losing the operator
            #'s own voice is bad, killing the meeting is worse.
            print(f"[MIC] Microphone rebind failed: {exc}")
            _log(
                "AUDIO_INPUT_REBIND_FAILED",
                error=f"{type(exc).__name__}: {exc}",
                seconds_until_failure=round(time.monotonic() - started, 3),
            )
            return False
        else:
            # Re-baseline onto the device now captured from, or the watcher
            # would compare against the old microphone and report a change on
            # every poll from here.
            self._mic_default_endpoint_baseline = (
                self._read_default_capture_endpoint_id()
            )
            self._mic_device_change_reported = False
            _log(
                "AUDIO_INPUT_REBIND_COMPLETED",
                capture_gap_seconds=round(time.monotonic() - started, 3),
            )
            return True
        finally:
            self._mic_rebind_in_progress = False

    def _close_microphone_stream(self):
            """Stop and release the sounddevice microphone stream."""
            if self._mic_stream is not None:
                try:
                    self._mic_stream.stop()
                    self._mic_stream.close()
                except Exception as exc:
                    print(f"Error closing microphone stream: {exc}")
                self._mic_stream = None
            # Cleared where the session ends, the same way the render side
            # clears its baseline in `_close_wasapi_stream`: leaving it set
            # would make a second session whose mic fails to open compare
            # against the FIRST session's microphone. Guarded so a rebind's own
            # teardown does not wipe the baseline it is about to replace.
            if not getattr(self, "_mic_rebind_in_progress", False):
                self._mic_default_endpoint_baseline = ""
                self._mic_device_change_reported = False
                self._mic_last_rebind_mono = 0.0
