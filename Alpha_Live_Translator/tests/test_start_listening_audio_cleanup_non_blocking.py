"""Regression test for BUG_FIX_ROADMAP.md Batch 2, item 9b.

Confirmed defect: the class-level monkey-patch
install_japanese_stabilizer_hooks() installs onto
AlphaApp._start_listening_worker (applied once at app startup, main.py,
so it wraps every subsequent Start click for the app's lifetime) used
to call audio_temp_capture.cleanup_old_audio_temp(reason=
"start_listening") directly and synchronously. That function iterates
EVERY historical run folder under troubleshooting/runs/ checking audio
retention -- a cost that grows with total run count, not just the
current session. Measured live: an unaccounted 8.8s (English) / 12.5s
(Japanese) gap between "TEMP_AUDIO_RETENTION_CLEANUP_STARTED" and the
real audio/Deepgram init actually beginning, with dozens of run folders
accumulated (Bug Report.md §4.5).

The exact same cleanup is already correctly scheduled non-blocking at
Stop (stop_finalize_worker.py, reason="after_stop") via
audio_temp_capture.schedule_audio_cleanup_non_blocking. Fix: use that
same, already-tested wrapper at Start too, instead of the blocking call.
Neither call site ever used the deleted-file-count return value, so
this is a behavior-preserving swap.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.japanese_final_chunk_stabilizer import (  # noqa: E402
    install_japanese_stabilizer_hooks,
)


class _FakeApp:
    """Stand-in class for AlphaApp, just enough surface for the patched
    _start_listening_worker to run without touching real app/UI state."""

    def _start_listening_worker(self, *args, **kwargs):
        return "orig-worker-ran"


class TestStartListeningUsesNonBlockingAudioCleanup(unittest.TestCase):
    def _install_and_call(self, app_cls):
        install_japanese_stabilizer_hooks(app_cls)
        instance = app_cls()
        instance._listen_language = "en"
        with patch(
            "alpha.transcription.japanese_final_chunk_stabilizer.reset_japanese_final_stabilizer"
        ), patch(
            "alpha.utils.japanese_accuracy_log.log_japanese_accuracy_run_started"
        ), patch(
            "alpha.utils.run_artifacts.reset_run_artifacts_session"
        ), patch(
            "alpha.utils.run_artifacts.create_initial_run_artifacts_index"
        ), patch(
            "alpha.utils.runtime_evidence.reset_runtime_evidence_session"
        ), patch(
            "alpha.utils.run_identity.init_live_run_from_host", return_value=None
        ), patch(
            "alpha.utils.audio_temp_capture.cleanup_old_audio_temp"
        ) as mock_blocking, patch(
            "alpha.utils.audio_temp_capture.schedule_audio_cleanup_non_blocking"
        ) as mock_nonblocking:
            result = instance._start_listening_worker()
        return result, mock_blocking, mock_nonblocking

    def test_uses_non_blocking_scheduler_not_the_blocking_call(self):
        # Fresh class per test -- the hook installer mutates the class
        # object itself.
        class AppUnderTest(_FakeApp):
            pass

        result, mock_blocking, mock_nonblocking = self._install_and_call(
            AppUnderTest
        )

        mock_nonblocking.assert_called_once_with(reason="start_listening")
        mock_blocking.assert_not_called()
        # The original worker (real audio/Deepgram init) must still run.
        self.assertEqual(result, "orig-worker-ran")


if __name__ == "__main__":
    unittest.main()
