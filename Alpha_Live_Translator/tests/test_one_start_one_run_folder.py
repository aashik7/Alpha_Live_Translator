"""One Start makes one run folder, and the whole session writes to it.

WHAT WAS BROKEN (item 60, PENDING_TASKS open defect i, 2026-09-30)
------------------------------------------------------------------
Every Start left an empty run folder beside the real one
(`...26.5.46-20260930-131613`, 38 files, next to `...-131614`). The UI Start
path (`session_runtime`) creates the Start's run identity; the start-worker
wrapper installed by `install_japanese_stabilizer_hooks` then called
`init_live_run_from_host` again, which always rotates an existing identity --
a second folder, a second ledger reset, and the live session runtime left bound
to the first (that run's Stop logged `RUNTIME_AUDIO_COUNTERS_FROZEN` for
`...-131613` while its transcript was in `...-131614`).
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import alpha.utils.troubleshooting_paths as tp  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.japanese_final_chunk_stabilizer import (  # noqa: E402
    install_japanese_stabilizer_hooks,
)
from alpha.utils import run_identity as ri  # noqa: E402


class _App:
    _listen_language = "ja"

    def _start_listening_worker(self, dropdown_lang, deepgram_lang):
        self.worker_saw = getattr(self, "_run_identity", None)

    def _stop_listening_immediate(self, *a, **k):
        pass

    def _publish_final_transcript_segment(self, *a, **k):
        return True

    def copy_live_transcript_to_clipboard(self, *a, **k):
        pass


install_japanese_stabilizer_hooks(_App)


class OneStartOneRunFolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="item60-"))
        self._saved_root = tp._troubleshooting_root
        tp._troubleshooting_root = self.tmp / "troubleshooting"
        ri.reset_run_identity()
        tp.reset_troubleshooting_session()

    def tearDown(self):
        ri.reset_run_identity()
        tp.reset_troubleshooting_session()
        tp._troubleshooting_root = self._saved_root
        ctl.reset_for_run("teardown-item60")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_folders(self):
        runs = tp._troubleshooting_root / "runs"
        return sorted(p.name for p in runs.iterdir() if p.is_dir() and p.name != "_pending")

    def test_the_worker_keeps_the_identity_the_start_created(self):
        app = _App()
        app._run_identity = ri.init_live_run_from_host(app)  # the UI Start path
        first = app._run_identity
        app._start_listening_worker("Japanese", "ja")
        self.assertIs(ri.get_current_run_identity(), first, "the worker rotated the Start's identity")
        self.assertIs(app.worker_saw, first)
        self.assertEqual(len(self.run_folders()), 1, self.run_folders())

    def test_a_finished_session_still_gets_a_new_run(self):
        app = _App()
        app._run_identity = ri.init_live_run_from_host(app)
        old = app._run_identity
        old.stop_finalize_completed = True  # the previous meeting's Stop finished
        app._start_listening_worker("Japanese", "ja")
        self.assertIsNot(ri.get_current_run_identity(), old)
        self.assertIs(app._run_identity, ri.get_current_run_identity())


if __name__ == "__main__":
    unittest.main()
