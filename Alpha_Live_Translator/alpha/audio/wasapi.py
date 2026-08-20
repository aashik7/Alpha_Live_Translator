"""WASAPI loopback system audio capture via pyaudiowpatch."""

import threading
import time

from tkinter import messagebox

from alpha.config import WASAPI_FRAMES_PER_BUFFER
from alpha.utils.queues import put_bounded


def _import_pyaudio():
    """Lazy import — pyaudiowpatch init can be slow on Windows."""
    import pyaudiowpatch as pyaudio

    return pyaudio


class WasapiCaptureMixin:
    """Mixin providing WASAPI loopback capture methods."""

    def _show_wasapi_error(self, title: str, message: str) -> None:
        if threading.current_thread() is threading.main_thread():
            messagebox.showerror(title, message)
        else:
            self.after(0, lambda: messagebox.showerror(title, message))

    def _get_wasapi_loopback_device(self):
        """Get the default WASAPI loopback device via pyaudiowpatch."""
        pyaudio = _import_pyaudio()
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

    # ------------------------------------------------------------------
    # Item 73: notice when Windows' default output device changes mid-session
    # ------------------------------------------------------------------
    def _read_default_endpoint_id(self) -> str:
        """Seam for tests. Returns "" when the endpoint cannot be read."""
        from alpha.audio.default_endpoint import read_default_render_endpoint_id

        return read_default_render_endpoint_id()

    def _report_default_device_changed(self, baseline: str, current: str) -> None:
        """Two sinks, deliberately, and deliberately not a third.

        A modal is wrong here: `_show_wasapi_error` blocks the Tk mainloop,
        and the only other mid-session modal in this app stops the session
        immediately afterwards. A device change is recoverable -- the user can
        switch back -- so it must not seize the UI mid-meeting.

        The transcript publish path is wrong too, and that is measured rather
        than assumed: item 67's marker takes exactly that route and is
        REJECTED before it reaches the store. Run `...20260814-101813` logs
        `DEEPGRAM_AUDIO_GAP_MARKED` at 10:21:15.842 and
        `IDENTITY_REJECTION reason="missing_identity_key"` at 10:21:15.938,
        and the string "connection lost" appears nowhere in that run's
        transcripts. Publishing here would inherit that dead end.
        """
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "AUDIO_OUTPUT_DEVICE_CHANGED",
                baseline_endpoint_id=baseline,
                current_endpoint_id=current,
                captured_device_name=str(
                    getattr(self, "_diag_wasapi_device_name", "") or ""
                ),
                note="capture stays on the original device; audio routed to "
                "the new default is not captured",
            )
        except Exception:
            pass

        # Item 47 made `_sync_connection_indicator` the SINGLE writer of
        # `signal_label`, and it repaints once a second from `_update_timer`.
        # Painting the label directly here therefore held for about one second
        # before being overwritten with "● Signal OK" -- a green light over a
        # device nothing is being routed to, which is the exact failure this
        # detector exists to make visible.
        #
        # The change is now a SIGNAL the indicator consumes, ranked in
        # `describe_connection` alongside the connection ones, so it holds
        # until the device is restored instead of flickering past.
        self._audio_device_changed = True
        self._refresh_connection_indicator()

    def _report_default_device_restored(self) -> None:
        """The default came back to the device we capture from."""
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "AUDIO_OUTPUT_DEVICE_RESTORED",
                note="default output is the capture device again",
            )
        except Exception:
            pass

        # Clearing the SIGNAL, not repainting the label: the indicator decides
        # what to show next, which matters because "restored" does not mean
        # healthy -- a reconnect or a rejected key may still be in progress,
        # and hardcoding "● Signal OK" here used to paint over both.
        self._audio_device_changed = False
        self._refresh_connection_indicator()

    def _refresh_connection_indicator(self) -> None:
        """Ask the UI to repaint the status indicator now, on its own thread.

        Without this the change would still appear, but only on the next 1 s
        tick of `_update_timer`. This watcher runs on its own 2 s daemon
        thread, so it must never touch a widget itself.
        """
        runner = getattr(self, "_run_on_ui_thread", None)
        if not callable(runner):
            return

        # The indicator is looked up INSIDE the marshalled callback, not before
        # marshalling. Deciding out here would make the hand-off conditional on
        # a widget that may not exist yet when the watcher first fires, and the
        # invariant this must keep is "the UI update is marshalled", full stop.
        def _paint() -> None:
            sync = getattr(self, "_sync_connection_indicator", None)
            if callable(sync):
                sync()

        try:
            runner(_paint)
        except Exception:
            pass

    def _wasapi_device_watch_worker(self, poll_seconds: float = 2.0) -> None:
        """Watch the default render endpoint for the life of the stream.

        Polled rather than event-driven, and polled OUTSIDE PortAudio, because
        PortAudio cannot answer this question at all: `Pa_Initialize()`
        snapshots the device list, so `get_default_wasapi_loopback()` returns
        the start-of-session default forever (measured: a second `PyAudio()`
        while the first is alive returns in 0.048 ms with an identical index).

        Its own thread rather than the reader loop: a poll costs ~2.6 ms, and
        the reader loop wakes every ~10 ms to move audio. Spending a quarter
        of that budget on a COM call risks dropping frames, and
        `_wasapi_reader_worker` discards overflow silently
        (`exception_on_overflow=False`), so a drop would leave no trace.

        Requires TWO consecutive disagreeing reads before reporting: a
        sleep/resume or a driver re-enumeration can momentarily report a
        different endpoint. Reports once per distinct new device.
        """
        from alpha.audio.default_endpoint import com_initialize_mta, com_uninitialize

        com_ready = com_initialize_mta()
        try:
            if not com_ready:
                return
            pending = ""
            while not self._stop_event.wait(poll_seconds):
                # The baseline is the endpoint the STREAM IS BOUND TO, and it
                # never moves for the life of the session. PortAudio opened a
                # specific device index and has no follow-the-default
                # behaviour, so "is the default still the device we capture?"
                # is the whole question. Re-baselining onto each new default
                # was the first draft and was wrong: switching BACK to the
                # capture device is a recovery, and it reported that as a
                # second fault.
                baseline = getattr(self, "_wasapi_default_endpoint_baseline", "")
                if not baseline:
                    continue
                current = self._read_default_endpoint_id()
                # "" is UNKNOWN, never "changed". A COM hiccup is not evidence
                # that the device moved, and warning on it would teach the
                # operator to ignore the warning.
                if not current:
                    pending = ""
                    continue
                if current == baseline:
                    # Back on the capture device. Clear the latch so a later
                    # switch is reported again, and take the warning down --
                    # a sticky warning that outlives the problem is noise.
                    if getattr(self, "_wasapi_device_change_reported", False):
                        self._wasapi_device_change_reported = False
                        self._report_default_device_restored()
                    pending = ""
                    continue
                if pending != current:
                    pending = current
                    continue
                if not getattr(self, "_wasapi_device_change_reported", False):
                    self._wasapi_device_change_reported = True
                    self._report_default_device_changed(baseline, current)
                pending = ""
        except Exception as exc:
            print(f"[WASAPI] Device watch stopped: {exc}")
        finally:
            if com_ready:
                com_uninitialize()

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
                        if getattr(self, "_dg_stop_sending_audio", False):
                            continue
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
                try:
                    from alpha.utils.runtime_audio_counters import note_capture_error

                    note_capture_error()
                except Exception:
                    pass
                break

    def _start_wasapi_loopback(self):
        """Start capturing all system audio via WASAPI loopback (zero user setup)."""
        pyaudio = _import_pyaudio()
        try:
            self._pyaudio, loopback = self._get_wasapi_loopback_device()
            # Item 73: baseline the default render endpoint at the moment we
            # bind to it. Inside this try, so a failure to read it falls into
            # the existing `except` and can never block Start -- and it is
            # taken from the OS, not from `loopback`, because PortAudio's
            # index and name are neither stable nor unique (see
            # alpha/audio/default_endpoint.py).
            self._wasapi_default_endpoint_baseline = self._read_default_endpoint_id()
            self._wasapi_device_change_reported = False
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
            # Item 73. Started only after the reader is up, and only when a
            # baseline was actually obtained -- with no baseline there is
            # nothing to compare against and the thread would just burn COM
            # calls.
            if self._wasapi_default_endpoint_baseline:
                self._wasapi_device_watch_thread = threading.Thread(
                    target=self._wasapi_device_watch_worker,
                    name="WasapiDeviceWatch",
                    daemon=True,
                )
                self._wasapi_device_watch_thread.start()
            print("WASAPI loopback stream started successfully")
        except Exception as exc:
            print(f"WASAPI loopback error: {exc}")
            try:
                from alpha.utils.runtime_audio_counters import note_capture_error

                note_capture_error()
            except Exception:
                pass
            self._close_wasapi_stream()
            self._show_wasapi_error(
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

        # Item 73. Cleared HERE, where the session ends, not only in
        # __init__ -- this function is the one place every stop path passes
        # through, and it runs more than once per session, so the reset has to
        # be idempotent. Leaving the baseline set would make a second Start
        # whose device acquisition fails compare against the FIRST session's
        # device, which is the same latent bug `_wasapi_rate` and
        # `_diag_wasapi_device_name` already carry.
        watch = getattr(self, "_wasapi_device_watch_thread", None)
        if (
            watch is not None
            and watch.is_alive()
            and watch is not threading.current_thread()
        ):
            watch.join(timeout=1.0)
        self._wasapi_device_watch_thread = None
        self._wasapi_default_endpoint_baseline = ""
        self._wasapi_device_change_reported = False

        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception as exc:
                print(f"Error terminating PyAudio: {exc}")
            self._pyaudio = None
