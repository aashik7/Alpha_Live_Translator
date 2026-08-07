"""Regression test for BUG_FIX_ROADMAP.md Batch 1, item 1.

Confirmed defect: main_window.py::_begin_graceful_stop called
self._flush_pending_translation_submit() with zero arguments, but that
method's signature is (self, key) with no default for `key`
(main_window.py::_flush_pending_translation_submit). Every call raised
TypeError, caught by an immediately-surrounding bare
`except Exception: pass` -- this safety net never once executed.

It was also entirely redundant: flush_pending_translation_submissions()
(plural, no args, iterates every pending debounce key) already runs
later in the same Stop sequence via stop_finalize_worker.py, and is a
complete, correct replacement (see its own docstring / TASK_7_REPORT.md).
The fix removes the broken call rather than patching its arity.

This test binds the real AlphaApp._begin_graceful_stop onto a minimal
stub and proves the broken zero-arg call is gone: before the fix, a
MagicMock standing in for _flush_pending_translation_submit records a
call (with no arguments) every time _begin_graceful_stop runs; after the
fix, it is never called at all.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402


class _Stub:
    """Minimal host carrying only what _begin_graceful_stop's try/except
    blocks need to fail gracefully (or succeed) without a real Tk app."""

    def __init__(self):
        self.translation_worker = None
        self._live_session_id = "test-session"


_Stub._begin_graceful_stop = AlphaApp._begin_graceful_stop


class TestBeginGracefulStopDoesNotCallBrokenFlush(unittest.TestCase):
    def test_flush_pending_translation_submit_never_called(self):
        stub = _Stub()
        stub._flush_pending_translation_submit = MagicMock()
        with patch("alpha.utils.stop_finalize_worker.begin_stop_from_ui") as mock_begin:
            stub._begin_graceful_stop()

        stub._flush_pending_translation_submit.assert_not_called()
        mock_begin.assert_called_once_with(stub)


if __name__ == "__main__":
    unittest.main()
