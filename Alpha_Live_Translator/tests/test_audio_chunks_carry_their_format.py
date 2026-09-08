"""Every system-audio chunk carries the format it was captured with.

WHAT WAS BROKEN
---------------
`push_system` resampled with `self._wasapi_channels` / `self._wasapi_rate` --
mixer state, set once by `configure_sources`. The queue between the WASAPI
reader and the mixer carried bare PCM bytes with no format attached, so a chunk
was resampled with whatever the mixer had been told last, not with what the
chunk was actually captured at.

That is safe today only because nothing writes those attributes after startup.
It is shared mutable state read on the mixer thread and written on another, and
it is the whole reason a device swap cannot be done safely: at a swap the queue
holds old-device bytes, and `ingest_queues` drains them after the format has
moved on.

WHY IT IS THE WORST FAILURE SHAPE IN THIS PROJECT
-------------------------------------------------
Measured on the real resampler, 440 Hz / 48 kHz stereo / 1.000 s:

    told 48 kHz, 2 ch (correct) -> 1.000 s, 440.0 Hz
    told 44.1 kHz, 2 ch         -> 1.088 s, 404.3 Hz   nothing raised
    told 48 kHz, 1 ch           -> 2.000 s, 220.0 Hz   nothing raised

Nothing raises, no counter moves, the transcript keeps flowing -- and Deepgram
transcribes half-speed audio fluently and wrongly. A wrong CHANNEL COUNT is
materially worse than a wrong rate.

THE FIX, AND WHY IT IS CONSTRUCTION RATHER THAN DISCIPLINE
----------------------------------------------------------
The reader stamps `(pcm, channels, rate)` onto every chunk; the mixer resamples
with the values that ARRIVED WITH THE CHUNK. Old-device chunks still in flight
then resample correctly, new-device chunks resample correctly, and there is no
ordering requirement between anything -- no drain step, no flush step, no lock.
Ordering is a convention; a stamp is a fact carried with the data.

`configure_sources` stays, demoted to supplying defaults for a chunk that
carries no stamp.

WHAT THESE TESTS PIN
--------------------
* two chunks captured at DIFFERENT formats, queued together, each resample by
  its own stamp -- sample for sample, not by duration
* an explicit stamp beats the configured default
* the mixer's attributes stop being load-bearing (R17): changing them cannot
  change how a stamped chunk is resampled
* bare bytes still work, so nothing that puts raw PCM on the queue breaks
* the reader stamps with the format the stream was actually opened with
* stamped chunks survive `put_bounded`'s drop-the-oldest path
* nothing but the mixer worker calls the mixer's three format-touching methods
"""

import ast
import queue
import sys
import threading
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio.processing import pcm_to_mono_16k_np  # noqa: E402
from alpha.audio.timeline_mixer import DeepgramTimelineMixer  # noqa: E402
from alpha.audio.wasapi import WasapiCaptureMixin  # noqa: E402
from alpha.utils.queues import put_bounded  # noqa: E402

OLD_RATE, OLD_CHANNELS = 48000, 2
NEW_RATE, NEW_CHANNELS = 44100, 1


def tone(seconds, rate, channels, hz=440.0, amplitude=8000):
    """Interleaved int16 PCM, the fixture the R1 table was measured on."""
    n = int(rate * seconds)
    t = np.arange(n, dtype=np.float64) / float(rate)
    mono = (np.sin(2.0 * np.pi * hz * t) * amplitude).astype(np.int16)
    if channels == 1:
        return mono.tobytes()
    return np.repeat(mono, channels).astype(np.int16).tobytes()


class TheHazardIsRealTest(unittest.TestCase):
    """The R1 fixture, re-measured here rather than quoted.

    Green before and after the fix on purpose -- it characterises the
    resampler, it does not test the stamp. It exists so the numbers the rest of
    this file relies on are checked rather than trusted.
    """

    def test_the_wrong_channel_count_doubles_the_length_and_halves_the_pitch(self):
        pcm = tone(1.0, OLD_RATE, OLD_CHANNELS)
        right = pcm_to_mono_16k_np(pcm, OLD_CHANNELS, OLD_RATE)
        wrong = pcm_to_mono_16k_np(pcm, 1, OLD_RATE)
        self.assertAlmostEqual(len(right) / 16000.0, 1.000, places=2)
        self.assertAlmostEqual(len(wrong) / 16000.0, 2.000, places=2)

    def test_the_wrong_rate_is_silent_and_wrong(self):
        pcm = tone(1.0, OLD_RATE, OLD_CHANNELS)
        wrong = pcm_to_mono_16k_np(pcm, OLD_CHANNELS, NEW_RATE)
        # No exception, no empty result -- a plausible-looking signal.
        self.assertGreater(len(wrong), 0)
        self.assertNotEqual(
            len(wrong), len(pcm_to_mono_16k_np(pcm, OLD_CHANNELS, OLD_RATE))
        )


class AChunkIsResampledByItsOwnStampTest(unittest.TestCase):
    """The regression test. Two devices' chunks share one queue."""

    def setUp(self):
        self.mixer = DeepgramTimelineMixer()
        # The mixer is configured for the OLD device, as it is at Start.
        self.mixer.configure_sources(OLD_CHANNELS, OLD_RATE, mic_available=False)
        self.old_pcm = tone(0.2, OLD_RATE, OLD_CHANNELS)
        self.new_pcm = tone(0.2, NEW_RATE, NEW_CHANNELS, hz=660.0)

    def test_both_devices_chunks_resample_correctly_from_one_queue(self):
        sys_queue = queue.Queue(maxsize=100)
        sys_queue.put((self.old_pcm, OLD_CHANNELS, OLD_RATE))
        sys_queue.put((self.new_pcm, NEW_CHANNELS, NEW_RATE))

        self.mixer.ingest_queues(sys_queue, None)

        expected = np.concatenate(
            (
                pcm_to_mono_16k_np(self.old_pcm, OLD_CHANNELS, OLD_RATE),
                pcm_to_mono_16k_np(self.new_pcm, NEW_CHANNELS, NEW_RATE),
            )
        )
        # Sample for sample. Duration alone would pass the wrong-channel case
        # at some rate pairs, which is exactly how this class of bug hides.
        np.testing.assert_array_equal(
            self.mixer._sys_buffer,
            expected,
            "a chunk was resampled with the other device's format",
        )

    def test_the_stamp_beats_the_configured_default(self):
        self.mixer.push_system(self.new_pcm, NEW_CHANNELS, NEW_RATE)
        np.testing.assert_array_equal(
            self.mixer._sys_buffer,
            pcm_to_mono_16k_np(self.new_pcm, NEW_CHANNELS, NEW_RATE),
            "push_system ignored the stamp and used configure_sources' values",
        )

    def test_an_unstamped_chunk_still_uses_the_configured_default(self):
        """Bare bytes must keep working -- the fallback is the contract."""
        self.mixer.push_system(self.old_pcm)
        np.testing.assert_array_equal(
            self.mixer._sys_buffer,
            pcm_to_mono_16k_np(self.old_pcm, OLD_CHANNELS, OLD_RATE),
        )

    def test_a_bare_bytes_queue_item_still_works(self):
        sys_queue = queue.Queue(maxsize=100)
        sys_queue.put(self.old_pcm)
        self.mixer.ingest_queues(sys_queue, None)
        np.testing.assert_array_equal(
            self.mixer._sys_buffer,
            pcm_to_mono_16k_np(self.old_pcm, OLD_CHANNELS, OLD_RATE),
        )

    def test_the_mixers_own_attributes_stop_being_load_bearing(self):
        """R17. `main_window` hoists these into locals before the mixer loop,
        so assigning them mid-session already changed nothing. With the stamp
        they cannot change a stamped chunk's result even if something does."""
        self.mixer.push_system(self.new_pcm, NEW_CHANNELS, NEW_RATE)
        first = self.mixer._sys_buffer.copy()

        self.mixer.reset()
        self.mixer._wasapi_channels = 1
        self.mixer._wasapi_rate = 8000
        self.mixer.push_system(self.new_pcm, NEW_CHANNELS, NEW_RATE)

        np.testing.assert_array_equal(
            self.mixer._sys_buffer,
            first,
            "the mixer's mutable format state still decides a stamped chunk",
        )

    def test_an_empty_chunk_with_a_stamp_is_ignored(self):
        self.mixer.push_system(b"", NEW_CHANNELS, NEW_RATE)
        self.assertEqual(self.mixer._sys_buffer.size, 0)


class TheReaderStampsWhatItCapturedTest(unittest.TestCase):
    """Driven through the real reader loop over a stub stream."""

    class _Stream:
        def __init__(self, payload, chunks=2):
            self._payload = payload
            self._left = chunks

        def is_active(self):
            return True

        def get_read_available(self):
            return 10 ** 6 if self._left else 0

        def read(self, frames, exception_on_overflow=False):
            if not self._left:
                return b""
            self._left -= 1
            return self._payload

    def _host(self, stream, channels, rate):
        class Host:
            _wasapi_reader_worker = WasapiCaptureMixin._wasapi_reader_worker
            _wasapi_stop_requested = WasapiCaptureMixin._wasapi_stop_requested
            _wasapi_capture_stop_event = (
                WasapiCaptureMixin._wasapi_capture_stop_event
            )

            def __init__(self):
                self._stop_event = threading.Event()
                self._wasapi_stream = stream
                self._wasapi_frames_per_buffer = 480
                self._wasapi_channels = channels
                self._wasapi_rate = rate
                self.sys_audio_queue = queue.Queue(maxsize=100)

        return Host()

    def _drain(self, host):
        thread = threading.Thread(target=host._wasapi_reader_worker, daemon=True)
        thread.start()
        items = []
        try:
            while len(items) < 2:
                items.append(host.sys_audio_queue.get(timeout=2.0))
        finally:
            host._stop_event.set()
            thread.join(timeout=2.0)
        return items

    def test_every_queued_chunk_carries_its_channels_and_rate(self):
        payload = tone(0.02, OLD_RATE, OLD_CHANNELS)
        host = self._host(self._Stream(payload), OLD_CHANNELS, OLD_RATE)
        for item in self._drain(host):
            self.assertIsInstance(
                item, tuple, "the reader still queues bare bytes; got %r" % (type(item),)
            )
            self.assertEqual(len(item), 3)
            data, channels, rate = item
            self.assertEqual(data, payload)
            self.assertEqual(channels, OLD_CHANNELS)
            self.assertEqual(rate, OLD_RATE)

    def test_a_second_device_stamps_its_own_format(self):
        """The reader is restarted per device, so re-reading the format at
        reader start is what makes the stamp per-device."""
        payload = tone(0.02, NEW_RATE, NEW_CHANNELS)
        host = self._host(self._Stream(payload), NEW_CHANNELS, NEW_RATE)
        for _, channels, rate in self._drain(host):
            self.assertEqual((channels, rate), (NEW_CHANNELS, NEW_RATE))


class StampedChunksSurviveTheQueueTest(unittest.TestCase):
    def test_put_bounded_drops_the_oldest_stamped_chunk_not_the_newest(self):
        """`MAX_AUDIO_QUEUE_SIZE` is 100 and the drop is silent, so the tuple
        has to survive the same path bare bytes did."""
        q = queue.Queue(maxsize=2)
        for i in range(4):
            put_bounded(q, (bytes([i]), OLD_CHANNELS, OLD_RATE))
        drained = []
        while not q.empty():
            drained.append(q.get_nowait())
        self.assertEqual([d[0] for d in drained], [b"\x02", b"\x03"])
        self.assertTrue(all(d[1:] == (OLD_CHANNELS, OLD_RATE) for d in drained))


class OnlyTheMixerWorkerTouchesTheMixerTest(unittest.TestCase):
    """R16, and the reason it is a risk rather than a silent fix: draining the
    queue or calling `configure_sources` from another thread races
    unsynchronised numpy buffer mutation, and the corruption it produces is
    exactly R1. The mixer has no lock and `emit_due_frames` runs at 50 Hz.

    Checked statically over app code: the shipped tree must contain no call
    site outside the mixer worker.
    """

    OWNED = ("push_system", "ingest_queues", "configure_sources")

    def _app_sources(self):
        for path in (PROJECT_ROOT / "alpha").rglob("*.py"):
            yield path

    def test_no_module_outside_the_mixer_worker_calls_them(self):
        offenders = []
        for path in self._app_sources():
            if path.name == "timeline_mixer.py":
                continue  # the mixer's own internals
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            enclosing = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for child in ast.walk(node):
                        enclosing[id(child)] = node.name
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                attr = getattr(node.func, "attr", "")
                if attr not in self.OWNED:
                    continue
                owner = enclosing.get(id(node), "<module>")
                if owner == "audio_mixer_worker":
                    continue
                offenders.append(
                    "%s:%d in %s() calls %s"
                    % (path.relative_to(PROJECT_ROOT), node.lineno, owner, attr)
                )
        self.assertEqual(
            offenders,
            [],
            "the mixer has no lock; only its worker may touch these:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
