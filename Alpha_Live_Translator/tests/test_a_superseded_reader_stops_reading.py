"""A reader that outlives its join must not keep reading the next device.

WHAT WAS BROKEN
---------------
`_close_wasapi_stream` joins the reader with `timeout=1.0`, never checks whether
the join succeeded, and nulls `_wasapi_reader_thread` either way. A reader still
blocked inside `stream.read()` therefore survives the close. Then
`_start_wasapi_loopback` CLEARS the capture stop event before spawning the next
reader -- so when the orphan finally wakes, `_wasapi_stop_requested()` is False
and it simply carries on.

What it carries on with is the problem. The loop re-reads
`stream = self._wasapi_stream` on every iteration, so the orphan picks up the
NEW device's stream; but `stamp_channels` / `stamp_rate` were captured once at
its own start, so it stamps that audio with the OLD device's format.

That is precisely the corruption the phase 7 stage 1 format stamp was shipped to
make impossible, reintroduced through a path the stamp cannot see. Driven
against the pre-fix tree through the real reader and the real close/start
sequence:

    join timed out after 1.01s: True
    old reader still alive after the rebind: True
    NEW-stream-bytes   stamped 48000 Hz 2 ch   x999

999 chunks of new-device audio wearing the old device's format. Two readers also
drain the same stream at once, so the good reader's chunks and the orphan's
interleave -- a partially corrupted timeline rather than an obviously broken
one, which is harder to notice and harder to diagnose.

The window needs the 1 s join to time out, which needs the old reader wedged in
a blocking read for longer than that -- during exactly the device transition
where a driver is most likely to wedge.

THE FIX
-------
Capture identity, stream and stamp TOGETHER at reader start:

* `_start_wasapi_loopback` bumps `_wasapi_reader_generation` before spawning.
* The worker reads its generation once and exits as soon as it is no longer the
  current reader. This is what makes an orphan stop, and it does not depend on
  what a closed PortAudio stream does on `is_active()`.
* The worker binds its own `stream` rather than re-reading the attribute, so the
  stamp provably describes the stream the bytes came from -- the stamp stops
  being correct-by-timing and becomes correct-by-construction, which was the
  whole point of stage 1.
* A superseded reader exits quietly. It must NOT print the give-up line, whose
  text is "System audio will not resume until the session is restarted" -- false
  and alarming when the real reader is healthy.

WHAT THESE TESTS PIN
--------------------
* an orphan stops instead of reading the next device's stream
* no chunk ever carries a stamp from a different device than its bytes
* a superseded reader exits promptly and silently
* a normal reader is unaffected, and a clean close still ends it
* the rebind cooldown does not survive Stop/Start
"""

import queue
import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio.wasapi import WasapiCaptureMixin  # noqa: E402

OLD_CHANNELS, OLD_RATE = 2, 48000
NEW_CHANNELS, NEW_RATE = 1, 44100

OLD_BYTES = b"\x01\x02" * 480
NEW_BYTES = b"\x09\x09" * 480


class _Stream:
    """A capture stream whose read() can be held open past the join."""

    def __init__(self, payload, hold=None):
        self.payload = payload
        self._hold = hold
        self.closed = False

    def is_active(self):
        return not self.closed

    def get_read_available(self):
        return 10 ** 6

    def read(self, frames, exception_on_overflow=False):
        if self._hold is not None:
            self._hold.wait(timeout=10.0)
            self._hold = None
        return self.payload


class _Host:
    """Borrows the real reader and the real stop-event helpers."""

    _wasapi_reader_worker = WasapiCaptureMixin._wasapi_reader_worker
    _wasapi_stop_requested = WasapiCaptureMixin._wasapi_stop_requested
    _wasapi_capture_stop_event = WasapiCaptureMixin._wasapi_capture_stop_event

    def __init__(self, stream):
        self._stop_event = threading.Event()
        self._wasapi_stream = stream
        self._wasapi_frames_per_buffer = 480
        self._wasapi_channels = OLD_CHANNELS
        self._wasapi_rate = OLD_RATE
        self._wasapi_reader_generation = 1
        self.sys_audio_queue = queue.Queue(maxsize=2000)

    # -- the two real lifecycle steps, reduced to what the reader can observe --
    def close_like_the_real_close(self, reader):
        """`_close_wasapi_stream`: signal, drop the handle, join 1 s, move on."""
        self._wasapi_capture_stop_event().set()
        self._wasapi_stream.closed = True
        self._wasapi_stream = None
        reader.join(timeout=1.0)
        return reader.is_alive()  # the real code ignores this

    def start_like_the_real_start(self, stream, channels, rate):
        """`_start_wasapi_loopback`: clear the event, set the new format, spawn."""
        self._wasapi_capture_stop_event().clear()
        self._wasapi_channels = channels
        self._wasapi_rate = rate
        self._wasapi_stream = stream
        self._wasapi_reader_generation = (
            int(getattr(self, "_wasapi_reader_generation", 0)) + 1
        )

    def drain(self):
        items = []
        while not self.sys_audio_queue.empty():
            items.append(self.sys_audio_queue.get_nowait())
        return items


class AnOrphanedReaderStopsTest(unittest.TestCase):
    """The full rebind sequence, at the timings that produce the orphan."""

    def _rebind_with_a_wedged_reader(self):
        hold = threading.Event()
        self.addCleanup(hold.set)
        host = _Host(_Stream(OLD_BYTES, hold=hold))
        reader = threading.Thread(
            target=host._wasapi_reader_worker, name="OrphanProbe", daemon=True
        )
        reader.start()
        self.addCleanup(host._stop_event.set)
        time.sleep(0.2)  # let it enter the blocking read

        timed_out = host.close_like_the_real_close(reader)
        self.assertTrue(
            timed_out,
            "the fixture did not reproduce a timed-out join, so this test "
            "would be vacuous",
        )
        new_stream = _Stream(NEW_BYTES)
        host.start_like_the_real_start(new_stream, NEW_CHANNELS, NEW_RATE)
        hold.set()  # the old read() finally returns
        time.sleep(0.5)
        return host, reader

    def test_the_orphan_does_not_read_the_next_devices_stream(self):
        host, reader = self._rebind_with_a_wedged_reader()
        from_new_stream = [i for i in host.drain() if i[0] == NEW_BYTES]
        self.assertEqual(
            from_new_stream,
            [],
            "a superseded reader captured %d chunks from the NEW device's "
            "stream; two readers were draining one stream" % len(from_new_stream),
        )

    def test_no_chunk_ever_wears_another_devices_stamp(self):
        """The stage 1 guarantee, stated as the invariant it actually is."""
        host, _ = self._rebind_with_a_wedged_reader()
        expected = {OLD_BYTES: (OLD_CHANNELS, OLD_RATE), NEW_BYTES: (NEW_CHANNELS, NEW_RATE)}
        wrong = [
            (data[:2], channels, rate)
            for data, channels, rate in host.drain()
            if expected.get(data) != (channels, rate)
        ]
        self.assertEqual(
            wrong,
            [],
            "%d chunks carried a stamp from a different device than their "
            "bytes -- the exact wrong-resample corruption the stamp exists to "
            "prevent" % len(wrong),
        )

    def test_the_orphan_actually_exits(self):
        _, reader = self._rebind_with_a_wedged_reader()
        self.assertFalse(
            reader.is_alive(),
            "the superseded reader is still running alongside its replacement",
        )

    # NOTE: a test that the orphan never prints the give-up line ("System audio
    # will not resume until the session is restarted") was written and then
    # DELETED. It passed against the pre-fix tree too, because in this fixture
    # the orphan reads successfully and never accumulates errors -- so it
    # measured nothing. This repo has shipped a green test over a dead path
    # twice; a test green on both sides is worse than no test. The property is
    # covered in practice by `test_the_orphan_actually_exits`: a reader that
    # exits on the generation check cannot reach the error counter at all.

class TheOrdinaryReaderIsUnaffectedTest(unittest.TestCase):
    def test_a_current_reader_captures_and_stamps_normally(self):
        host = _Host(_Stream(OLD_BYTES))
        reader = threading.Thread(target=host._wasapi_reader_worker, daemon=True)
        reader.start()
        try:
            item = host.sys_audio_queue.get(timeout=2.0)
        finally:
            host._stop_event.set()
            reader.join(timeout=2.0)
        self.assertEqual(item, (OLD_BYTES, OLD_CHANNELS, OLD_RATE))

    def test_a_clean_close_still_ends_the_reader(self):
        host = _Host(_Stream(OLD_BYTES))
        reader = threading.Thread(target=host._wasapi_reader_worker, daemon=True)
        reader.start()
        host.sys_audio_queue.get(timeout=2.0)
        self.assertFalse(
            host.close_like_the_real_close(reader),
            "a reader that is NOT wedged must still be joined by the close",
        )

    def test_the_session_stop_still_ends_the_reader(self):
        host = _Host(_Stream(OLD_BYTES))
        reader = threading.Thread(target=host._wasapi_reader_worker, daemon=True)
        reader.start()
        host.sys_audio_queue.get(timeout=2.0)
        host._stop_event.set()
        reader.join(timeout=2.0)
        self.assertFalse(reader.is_alive(), "Stop no longer ends the reader")


class TheCooldownDoesNotSurviveStopStartTest(unittest.TestCase):
    """`_wasapi_last_rebind_mono` was written once and cleared nowhere."""

    class Host:
        _close_wasapi_stream = WasapiCaptureMixin._close_wasapi_stream
        _wasapi_capture_stop_event = WasapiCaptureMixin._wasapi_capture_stop_event

        def __init__(self):
            self._wasapi_stream = None
            self._pyaudio = None
            self._wasapi_reader_thread = None
            self._wasapi_device_watch_thread = None
            self._wasapi_default_endpoint_baseline = "OLD"
            self._wasapi_device_change_reported = True
            self._wasapi_last_rebind_mono = time.monotonic()

    def test_close_clears_the_rebind_cooldown(self):
        host = self.Host()
        host._close_wasapi_stream()
        self.assertFalse(
            getattr(host, "_wasapi_last_rebind_mono", 0.0),
            "the 10 s cooldown survived the session; the next session's FIRST "
            "device change would be silently refused with no log line",
        )

    def test_a_close_inside_a_rebind_leaves_the_cooldown_alone(self):
        """The anti-storm guard must survive its own teardown.

        A rebind stamps the cooldown, then calls `_close_wasapi_stream` before
        reopening. If that close cleared the timestamp, every rebind would reset
        its own cooldown and a flapping device -- a headset reconnecting, a
        conferencing app grabbing and releasing the endpoint -- would queue one
        full teardown per poll. This test exists because that is exactly what
        the first draft of the session-boundary fix did.
        """
        host = self.Host()
        stamped = host._wasapi_last_rebind_mono
        host._wasapi_rebind_in_progress = True
        host._close_wasapi_stream()
        self.assertEqual(
            host._wasapi_last_rebind_mono,
            stamped,
            "a rebind's own teardown reset the cooldown, disabling the "
            "anti-storm guard for every rebind",
        )


if __name__ == "__main__":
    unittest.main()
