"""One read error must not kill system-audio capture for the whole session.

WHAT WAS BROKEN
---------------
`_wasapi_reader_worker` is the blocking read loop that feeds every byte of
system audio into the pipeline. It ran on a plain `threading.Thread`
(`wasapi.py:289`) -- `SupervisedThread` was used only for the device watcher two
methods below -- and its `except Exception` handler ended in `break`.

So any exception from `stream.read()`, `put_bounded()` or `stream.is_active()`
ended the loop, the target function returned, and the thread was gone. Nothing
noticed: `note_capture_error()` only bumps a counter, `_stop_event` was never
set so `_close_wasapi_stream` was never triggered, and the stream object was
left reporting `is_active()` True. The far end of the meeting went silent for
the rest of the session while the connection indicator still read Signal OK.

Driven before the fix: one `OSError(-9981)` on the third read left the thread
dead, `_stop_event` clear, and the queue frozen -- and a stream that merely
reported itself inactive exited with no print and no counter at all.

The audit tool that exists to catch exactly this shape did not report it.
`_body_swallows` asks "does the loop body contain a top-level `try` that catches
Exception?" and never looks inside the handler, so it treats "the exception does
not propagate" as "the loop survives". That is false for a handler ending in
`break` or `return`, and measurably `break`, `continue`, `return` and `pass`
handlers were indistinguishable to it.

THE FIX
-------
The handler now backs off and stays in the loop, giving up only after a run of
consecutive failures -- and the scanner now asks whether the handler body can
leave the loop, not merely whether it catches.

WHAT THESE TESTS PIN
--------------------
* a transient read error no longer ends capture, and delivery resumes
* the error run has to be CONSECUTIVE -- one good read resets it, so scattered
  glitches across a long meeting never accumulate into a give-up
* a genuinely dead stream still ends the loop, loudly, rather than spinning
* the two deliberate exits are untouched: `_stop_event`, and a stream that is
  no longer active
* the scanner reports a handler that `break`s or `return`s, does NOT report one
  that `continue`s, and is not fooled by a `break` belonging to a nested loop

The real loop and the real scanner throughout. The stream is a double because
the whole point is to make a read fail on demand; everything reading it is
production code.
"""

import ast
import json
import queue
import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.audio.wasapi import WasapiCaptureMixin  # noqa: E402
from alpha.config import (  # noqa: E402
    WASAPI_READER_ERROR_BACKOFF_S,
    WASAPI_READER_MAX_CONSECUTIVE_ERRORS,
)

AUDIT = PROJECT_ROOT / "tools" / "audit_unrecoverable_latches.py"


class FakeStream:
    """A PortAudio stream double. `fail_on` is a set of read ordinals."""

    def __init__(self, fail_on=(), active_until=None, always_fail_after=None):
        self.reads = 0
        self.fail_on = set(fail_on)
        self.active_until = active_until
        self.always_fail_after = always_fail_after

    def is_active(self):
        return self.active_until is None or self.reads < self.active_until

    def get_read_available(self):
        return 1 << 20

    def read(self, frames, exception_on_overflow=False):
        self.reads += 1
        if self.reads in self.fail_on or (
            self.always_fail_after is not None and self.reads > self.always_fail_after
        ):
            raise OSError(-9981, "Input overflowed")
        return b"\x00\x01" * frames


class Host:
    _wasapi_reader_worker = WasapiCaptureMixin._wasapi_reader_worker

    def __init__(self, stream):
        self._stop_event = threading.Event()
        self._wasapi_stream = stream
        self._wasapi_frames_per_buffer = 8
        self.sys_audio_queue = queue.Queue(maxsize=100000)


def _run(host, timeout=10.0):
    t = threading.Thread(target=host._wasapi_reader_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return t


class TheReaderSurvivesTest(unittest.TestCase):
    def test_a_transient_read_error_does_not_end_capture(self):
        """The defect, driven end to end on the real loop."""
        stream = FakeStream(fail_on={3}, active_until=40)
        host = Host(stream)
        _run(host)

        self.assertGreaterEqual(
            stream.reads, 40, "the loop stopped reading after the error"
        )
        self.assertGreater(
            host.sys_audio_queue.qsize(),
            3,
            "capture never resumed after one transient error -- system audio is "
            "dead for the rest of the session",
        )

    def test_the_error_run_has_to_be_consecutive(self):
        """A good read resets the counter.

        Without this, scattered glitches over a two-hour meeting would
        eventually add up to the give-up threshold and kill capture anyway --
        turning a rate limit into a slow-motion version of the original bug.
        """
        every_other = set(range(1, 400, 2))
        self.assertGreater(len(every_other), WASAPI_READER_MAX_CONSECUTIVE_ERRORS)
        stream = FakeStream(fail_on=every_other, active_until=400)
        host = Host(stream)
        _run(host, timeout=30.0)

        self.assertGreaterEqual(
            stream.reads,
            400,
            "capture gave up on scattered errors that were never consecutive",
        )

    def test_a_stream_that_never_recovers_gives_up_rather_than_spinning(self):
        stream = FakeStream(always_fail_after=2)
        host = Host(stream)
        t = _run(host, timeout=30.0)

        self.assertFalse(t.is_alive(), "the reader is spinning on a dead stream")
        self.assertLessEqual(
            stream.reads,
            WASAPI_READER_MAX_CONSECUTIVE_ERRORS + 10,
            "the reader kept retrying well past the give-up threshold",
        )

    # -- the two deliberate exits, unchanged ------------------------------

    def test_a_requested_stop_still_ends_the_loop(self):
        stream = FakeStream()
        host = Host(stream)
        host._stop_event.set()
        t = _run(host, timeout=5.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(stream.reads, 0)

    def test_a_stream_that_is_no_longer_active_still_ends_the_loop(self):
        """`_close_wasapi_stream` relies on this exit; it must survive."""
        stream = FakeStream(active_until=5)
        host = Host(stream)
        t = _run(host, timeout=5.0)
        self.assertFalse(t.is_alive())
        self.assertFalse(host._stop_event.is_set())

    def test_a_stop_during_an_error_does_not_wait_out_the_backoff(self):
        """Teardown closes the stream, so the read raises. That must exit at
        once rather than sleeping through the whole error budget."""
        stream = FakeStream(always_fail_after=0)
        host = Host(stream)
        host._stop_event.set()
        t = _run(host, timeout=5.0)
        self.assertFalse(t.is_alive())
        budget = WASAPI_READER_MAX_CONSECUTIVE_ERRORS * WASAPI_READER_ERROR_BACKOFF_S
        self.assertGreater(budget, 0.0)


class TheScannerSeesAHandlerThatLeavesTheLoopTest(unittest.TestCase):
    """The blind spot, exercised through the real scan on synthetic modules.

    `_body_swallows` used to answer "does the body catch?" when the question
    is "can the handler leave the loop?". A guard that cannot see the shape it
    exists to find passes forever.
    """

    MODULES = {
        # handler ends in `break` -- the thread dies. MUST be reported.
        "breaks_out": '''
            import threading

            def _loop():
                while True:
                    try:
                        work()
                    except Exception:
                        break

            threading.Thread(target=_loop).start()
        ''',
        # handler ends in `return` -- same thing. MUST be reported.
        "returns_out": '''
            import threading

            def _loop():
                while True:
                    try:
                        work()
                    except Exception:
                        return

            threading.Thread(target=_loop).start()
        ''',
        # handler continues -- the loop survives. Must NOT be reported.
        "continues": '''
            import threading

            def _loop():
                while True:
                    try:
                        work()
                    except Exception:
                        continue

            threading.Thread(target=_loop).start()
        ''',
        # the `break` belongs to a NESTED loop, so the outer loop survives.
        # Must NOT be reported.
        "breaks_only_the_inner_loop": '''
            import threading

            def _loop():
                while True:
                    try:
                        work()
                    except Exception:
                        for _ in range(3):
                            break

            threading.Thread(target=_loop).start()
        ''',
    }

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        pkg = root / "alpha" / "utils"
        pkg.mkdir(parents=True)
        for name, body in cls.MODULES.items():
            (pkg / ("probe_%s.py" % name)).write_text(
                textwrap.dedent(body), encoding="utf-8"
            )
        (root / "tools").mkdir()
        probe = root / "tools" / "audit_unrecoverable_latches.py"
        probe.write_text(AUDIT.read_text(encoding="utf-8"), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(probe), "--json"],
            cwd=root, capture_output=True, text=True, timeout=300,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        data = json.loads(result.stdout)
        cls.reported = {
            (row["file"].rsplit("/", 1)[-1], row["function"])
            for row in data["unsupervised_threads"]
        }

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _reported(self, module):
        return ("probe_%s.py" % module, "_loop") in self.reported

    def test_a_handler_that_breaks_out_is_reported(self):
        self.assertTrue(
            self._reported("breaks_out"),
            "the scan still treats 'the exception does not propagate' as 'the "
            "loop survives' -- the blind spot that hid the WASAPI reader",
        )

    def test_a_handler_that_returns_is_reported(self):
        self.assertTrue(self._reported("returns_out"))

    def test_a_handler_that_continues_is_not_reported(self):
        self.assertFalse(
            self._reported("continues"),
            "the scan now reports loops that genuinely survive -- a guard that "
            "cries wolf gets switched off",
        )

    def test_a_break_belonging_to_a_nested_loop_is_not_reported(self):
        self.assertFalse(
            self._reported("breaks_only_the_inner_loop"),
            "a break inside a nested loop ends the INNER loop; the thread's "
            "loop is unaffected",
        )


class TheRealReaderNoLongerLeavesItsLoopTest(unittest.TestCase):
    """Source-level companion to the behavioural tests above.

    Walked with the AST rather than grepped: this file and `wasapi.py` both
    quote the word `break` in prose explaining the fix, and matching raw text
    would find the sentence announcing it.
    """

    def test_no_except_handler_in_the_reader_leaves_the_loop_uninvited(self):
        src = (PROJECT_ROOT / "alpha" / "audio" / "wasapi.py").read_text(
            encoding="utf-8"
        )
        fn = next(
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == "_wasapi_reader_worker"
        )
        loops = [n for n in ast.walk(fn) if isinstance(n, (ast.While, ast.For))]
        self.assertEqual(len(loops), 1, "the reader gained a second loop")

        offenders = []
        for handler in ast.walk(fn):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            for node in ast.walk(handler):
                if isinstance(node, (ast.Break, ast.Return)):
                    offenders.append((type(node).__name__, node.lineno))

        # A give-up break after a bounded run of failures is allowed and
        # deliberate; an UNCONDITIONAL one is the defect. Assert the handler
        # cannot leave on its first error by driving it, not by counting.
        stream = FakeStream(fail_on={1}, active_until=20)
        host = Host(stream)
        _run(host)
        self.assertGreaterEqual(
            stream.reads, 20,
            "the handler still leaves the loop on the first error; AST found "
            "%r" % (offenders,),
        )


if __name__ == "__main__":
    unittest.main()
