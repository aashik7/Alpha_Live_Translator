"""Regression test for BUG_FIX_ROADMAP.md Batch 2, item 4.

Confirmed defect: main_window.py::_remove_interim_line_from_display
called box.compare("interim_anchor", ">=", "1.0") unconditionally. Tk
raises TclError ("bad text index") when a mark doesn't exist, which is
the *normal* case whenever there's no interim currently on screen --
every one of those was caught and logged as [INTERIM] remove_exception,
drowning any genuinely unexpected exception in noise (14 occurrences in
one short real run).

Fix: guard on "interim_anchor" in box.mark_names() before touching the
mark at all. Zero behavior change -- "mark absent" already meant
"nothing to remove" either way.

Uses a fake Tk Text-widget stub (not a real Tk widget, to keep this test
headless) whose .compare() raises exactly like real Tk does when the
mark is absent, so the test exercises the real control-flow decision
rather than just checking source text.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402


class _FakeTclError(Exception):
    pass


class _FakeTextBox:
    """Minimal stand-in for a Tk Text widget's mark-related behavior."""

    def __init__(self, marks=()):
        self._marks = set(marks)
        self.compare_call_count = 0
        self.delete_called = False

    def mark_names(self):
        return tuple(self._marks)

    def compare(self, mark, op, idx):
        self.compare_call_count += 1
        if mark not in self._marks:
            raise _FakeTclError(f'bad text index "{mark}"')
        return True

    def configure(self, **kwargs):
        pass

    def delete(self, start, end):
        self.delete_called = True

    def mark_unset(self, name):
        self._marks.discard(name)

    def see(self, *_args, **_kwargs):
        pass


class _Host:
    def __init__(self, box):
        self._box = box
        self.logs = []

    def _transcript_box(self):
        return self._box

    def _interim_log(self, message, data):
        self.logs.append((message, data))


_Host._remove_interim_line_from_display = AlphaApp._remove_interim_line_from_display


class TestRemoveInterimLineNoExceptionNoise(unittest.TestCase):
    def test_no_mark_present_logs_no_exception(self):
        box = _FakeTextBox(marks=())
        host = _Host(box)
        host._remove_interim_line_from_display()

        messages = [m for m, _ in host.logs]
        self.assertNotIn(
            "[INTERIM] remove_exception",
            messages,
            "removing a non-existent interim line must not go through "
            "the exception path",
        )
        self.assertIn(("[INTERIM] remove_attempt", {"has_mark": False}), host.logs)
        self.assertEqual(
            box.compare_call_count,
            0,
            "the fix must not call box.compare() at all when the mark is absent",
        )

    def test_mark_present_still_removes_successfully(self):
        box = _FakeTextBox(marks=("interim_anchor",))
        host = _Host(box)
        host._remove_interim_line_from_display()

        messages = [m for m, _ in host.logs]
        self.assertIn("[INTERIM] remove_success", messages)
        self.assertNotIn("[INTERIM] remove_exception", messages)
        self.assertTrue(box.delete_called)
        self.assertNotIn("interim_anchor", box.mark_names())

    def test_no_box_is_a_noop(self):
        host = _Host(box=None)
        host._remove_interim_line_from_display()
        self.assertEqual(host.logs, [])


if __name__ == "__main__":
    unittest.main()
