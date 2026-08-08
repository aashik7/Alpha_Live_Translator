"""Regression test for BUG_FIX_ROADMAP.md Batch 2, item 5.

Confirmed defect: main_window.py::_on_store_segment_updated's 3
except-blocks (interim-line removal, stale-translation removal,
translation resubmit) swallowed exceptions with zero logging. The store
mutation this function reacts to has already committed by the time
these run, so a failure here previously left committed text with, e.g.,
its old translation UI item gone and no new one ever requested -- with
no trace anywhere.

Fix adds a jp_accuracy_log call inside each except block; the swallow
itself is intentionally unchanged (still non-fatal to the store-update
path). This test binds the real method onto a stub whose 3 relevant
calls each raise, and asserts each failure is now logged under a
distinct event name.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402


class _Stub:
    def __init__(self):
        self.raise_on_interim_removal = False
        self.raise_on_translation_removal = False
        self.raise_on_translation_resubmit = False

    def _remove_interim_line_from_display(self):
        if self.raise_on_interim_removal:
            raise RuntimeError("boom: interim removal")

    def _remove_translation_item_for_utterance(self, **kwargs):
        if self.raise_on_translation_removal:
            raise RuntimeError("boom: translation removal")

    def submit_text_for_translation(self, *args, **kwargs):
        if self.raise_on_translation_resubmit:
            raise RuntimeError("boom: translation resubmit")

    def _transcript_box(self):
        return None  # short-circuits the rest of the function harmlessly


_Stub._on_store_segment_updated = AlphaApp._on_store_segment_updated


class TestOnStoreSegmentUpdatedLogsFailures(unittest.TestCase):
    def _call(self, stub):
        with patch("alpha.utils.japanese_accuracy_log.jp_accuracy_log") as mock_log:
            stub._on_store_segment_updated(
                1, "hello", canonical_utterance_id="U-1", source_version=2
            )
        return mock_log

    def test_interim_removal_failure_is_logged(self):
        stub = _Stub()
        stub.raise_on_interim_removal = True
        mock_log = self._call(stub)
        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("STORE_SEGMENT_UPDATE_INTERIM_REMOVAL_FAILED", events)

    def test_translation_removal_failure_is_logged(self):
        stub = _Stub()
        stub.raise_on_translation_removal = True
        mock_log = self._call(stub)
        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("STORE_SEGMENT_UPDATE_TRANSLATION_REMOVAL_FAILED", events)

    def test_translation_resubmit_failure_is_logged(self):
        stub = _Stub()
        stub.raise_on_translation_resubmit = True
        mock_log = self._call(stub)
        events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("STORE_SEGMENT_UPDATE_TRANSLATION_RESUBMIT_FAILED", events)

    def test_no_failures_logs_nothing(self):
        stub = _Stub()
        mock_log = self._call(stub)
        mock_log.assert_not_called()

    def test_function_never_raises_even_when_all_three_fail(self):
        # Swallow behavior itself must be unchanged -- this must not raise.
        stub = _Stub()
        stub.raise_on_interim_removal = True
        stub.raise_on_translation_removal = True
        stub.raise_on_translation_resubmit = True
        self._call(stub)  # no exception = pass


if __name__ == "__main__":
    unittest.main()
