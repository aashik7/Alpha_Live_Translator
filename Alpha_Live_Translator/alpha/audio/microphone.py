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


class _NullLock:
    """Used only by a host that lacks the shared single-flight lock."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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
            if raw:
                # Counted where the DEVICE delivers, not where the mixer
                # drains: a rebind has to distinguish "the microphone is
                # producing" from "the mixer is still running".
                self._mic_chunks_captured = (
                    int(getattr(self, "_mic_chunks_captured", 0) or 0) + 1
                )
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

        Called from the device-watch thread, which must not do the work
        itself: a headset plug moves BOTH defaults, so this and the WASAPI
        rebind fire together, and that one is expensive.

        Note it is NOT a correctness requirement that the two serialise.
        pyaudiowpatch and sounddevice are separate libraries with separate
        PortAudio instances, so their re-inits do not contend. They share a
        dispatch path because one path is easier to reason about, not because
        an earlier comment here claimed they must.
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
        # A worker thread, never the Tk mainloop. The mic rebind is cheap
        # (22.2 ms measured) so the mainloop would survive it, but a headset
        # plug fires this and the WASAPI rebind together and that one is not
        # cheap -- routing both the same way leaves one path to reason about.
        schedule = getattr(self, "_schedule_audio_rebind", None)
        if callable(schedule):
            try:
                schedule(self._rebind_microphone_to_default_device)
                return
            except Exception:
                pass
        # A host without the WASAPI mixin still gets a correct rebind; inline
        # is acceptable here precisely because this one is cheap.
        try:
            self._rebind_microphone_to_default_device()
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
        """Reopen the mic on whatever is now the default input.

        Runs on the shared rebind worker -- never the mainloop, and never the
        device-watch thread.

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
        # Runs on its own thread now, so it can outlive the decision to stop.
        # Reopening the microphone after Stop would leave a live stream behind
        # a session everything else believes has ended.
        try:
            if self._stop_event.is_set():
                return False
        except Exception:
            pass
        # A microphone that is not running is not a microphone to follow. Both
        # cases are supported and deliberate: the operator turned it off with
        # the UI switch, or the system-audio-only benchmark is active. A device
        # change must never switch capture of someone's voice back on.
        if not getattr(self, "_microphone_capture_enabled", True):
            return False
        if getattr(self, "_mic_stream", None) is None:
            return False
        now = time.monotonic()
        # Claimed under a lock: read-then-set was safe only while the caller was
        # the single-threaded mainloop.
        lock = getattr(self, "_rebind_single_flight_lock", None)
        guard = lock() if callable(lock) else _NullLock()
        with guard:
            if getattr(self, "_mic_rebind_in_progress", False):
                return False
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
        captured_before = int(getattr(self, "_mic_chunks_captured", 0) or 0)
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
            # Opening a stream is not the same as a microphone that works.
            confirm = getattr(self, "_await_capture_confirmation", None)
            if callable(confirm) and not confirm(
                "_mic_chunks_captured", captured_before
            ):
                print(
                    "[MIC] Rebind opened an input device but no audio arrived; "
                    "the microphone may be silent."
                )
                _log("AUDIO_INPUT_REBIND_NO_AUDIO")
                return False
            # Re-baseline onto the device now captured from, or the watcher
            # would compare against the old microphone and report a change on
            # every poll from here.
            self._mic_default_endpoint_baseline = (
                self._read_default_capture_endpoint_id()
            )
            self._mic_device_change_reported = False
            # A microphone swap is the same acoustic discontinuity as a speaker
            # swap -- it is the OPERATOR's own voice that changes device -- so
            # the next stable line must not be merged into one captured on the
            # old microphone. Shared helper; a host without it simply skips.
            mark = getattr(self, "_mark_device_swap_boundary", None)
            if callable(mark):
                mark()
            self._mic_swap_count = int(getattr(self, "_mic_swap_count", 0) or 0) + 1
            _log(
                "AUDIO_INPUT_REBIND_COMPLETED",
                swap_index=int(getattr(self, "_mic_swap_count", 0) or 0),
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
