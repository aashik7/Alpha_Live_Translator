"""Item 6: `_enqueue_flush` must not call back into the lock it may be under.

REFUTED as a live bug, and the retraction matters. An earlier pass reported a
reproduced deadlock; the probe had held `_lock` by hand and called the inner
function directly, which proves the EDGE exists and says nothing about whether
production takes it. Driving the real entry point refuted it: two executed
stress runs -- 43 real overflows single-threaded, 1901 across three concurrent
callback threads -- reached the fallback ZERO times, because the path needs a
competing producer and `_enqueue_flush` has exactly one call site, under `_lock`.

So this is not a bug fix. It removes a latent edge at zero behavioural cost:
by the time control reached the fallback the item was already counted in
`_retention_drop_count` and logged, so returning is what the function's own
docstring already promises -- "Non-blocking enqueue. On overflow drop the oldest
retention item only".

The residual risk it removes is real though: wiring the already-written
`reset_audio_temp_session()` into a Start/Stop path -- which is what that
function exists for -- introduces the second producer, and a lost-slot race
would then wedge the audio callback thread forever while holding `_lock`.

`_lock` here is a plain `threading.Lock`, NOT reentrant. Do not "fix" this class
of hazard with an `RLock`: it would legitimise re-entry at all 16 `with _lock`
sites and mask the pattern rather than remove it.
"""

import ast
import queue
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TheOverflowPathNeverReEntersTheLock(unittest.TestCase):
    """Driven through the real `_enqueue_flush` with a genuinely full queue."""

    def setUp(self):
        from alpha.utils import audio_temp_capture as atc

        self.atc = atc
        self._saved_queue = atc._retention_queue
        self._saved_drop = atc._retention_drop_count
        self._saved_ensure = atc._ensure_writer_thread
        self._saved_flush = atc._flush_chunk_locked_from_item

        # A real, genuinely full queue -- no mocking of the overflow itself.
        atc._retention_queue = queue.Queue(maxsize=1)
        atc._retention_queue.put_nowait({"filler": True})
        # The writer thread would drain it and defeat the test.
        atc._ensure_writer_thread = lambda: None

        self.flush_calls = []
        atc._flush_chunk_locked_from_item = lambda item: self.flush_calls.append(item)

        def _restore():
            atc._retention_queue = self._saved_queue
            atc._retention_drop_count = self._saved_drop
            atc._ensure_writer_thread = self._saved_ensure
            atc._flush_chunk_locked_from_item = self._saved_flush

        self.addCleanup(_restore)

    def _force_double_overflow(self):
        """Make the retry at :258 fail too, which is what reached the fallback.

        The first `put_nowait` fails, `get_nowait` frees a slot, and a competing
        producer refills it before the retry. Production has no second producer;
        this test supplies one so the branch is actually exercised.
        """
        atc = self.atc
        real_get = atc._retention_queue.get_nowait

        def _steal_the_slot():
            item = real_get()
            atc._retention_queue.put_nowait({"stolen": True})
            return item

        atc._retention_queue.get_nowait = _steal_the_slot
        atc._enqueue_flush({"payload": "audio"})

    def test_the_synchronous_flush_is_never_called(self):
        self._force_double_overflow()
        self.assertEqual(
            self.flush_calls,
            [],
            "the overflow path still calls back into the lock it may be under",
        )

    def test_it_returns_instead_of_raising(self):
        self._force_double_overflow()  # must simply return

    def test_the_drop_is_still_counted(self):
        before = self.atc._retention_drop_count
        self._force_double_overflow()
        self.assertGreater(
            self.atc._retention_drop_count,
            before,
            "the drop stopped being counted; the loss would be invisible again",
        )


class TheEdgeIsGoneFromTheSource(unittest.TestCase):
    def _enqueue_flush_body(self):
        source = (
            PROJECT_ROOT / "alpha" / "utils" / "audio_temp_capture.py"
        ).read_text(encoding="utf-8", errors="replace")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == "_enqueue_flush":
                return ast.unparse(node)
        self.fail("_enqueue_flush not found")

    def test_it_does_not_call_the_locked_flusher(self):
        self.assertNotIn("_flush_chunk_locked_from_item", self._enqueue_flush_body())

    def test_the_lock_stays_non_reentrant(self):
        """An RLock here would mask this class of bug at 16 sites."""
        from alpha.utils import audio_temp_capture as atc

        self.assertNotIsInstance(
            atc._lock,
            type(__import__("threading").RLock()),
            "_lock became reentrant; that legitimises re-entry everywhere",
        )


if __name__ == "__main__":
    unittest.main()
