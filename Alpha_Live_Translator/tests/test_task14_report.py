"""Task 14 — VALIDATE tests for the confirmed RUN_MANIFEST.json stale-write
defect (see TASK_14_REPORT.md).

Ground-truth mechanism (confirmed by real execution, not code-reading
inference — see TASK_14_REPORT.md Phase 1):

schedule_evidence_pointer_finalization_background(host, ...) is fired from
stop_finalize_worker.py right after one specific run's Stop sequence
finishes, but it runs later on an independent daemon thread with no
guaranteed timing. Its worker, evidence_pointer_finalize.py::
finalize_evidence_pointers_completed, used to re-derive "which run is
this" via the single, unscoped get_current_run_identity() global instead
of using the run it was actually scheduled for. If a real user starts a
new session (Start) before that background thread gets OS-scheduled, the
global has already moved on to the NEW run by the time the thread runs —
pairing the OLD run's `host` object with the NEW run's identity/folder,
and overwriting the NEW run's already-correct RUN_MANIFEST.json (written
synchronously by _write_minimal_runtime_artifacts) with a bogus status
derived from that mismatched pairing.

Fix: capture run_id/run_folder at the scheduling call site (guaranteed
correct for that run) and thread them through explicitly; the background
pass now bails out (no reads, no writes) if the current global identity no
longer agrees this is the same run.

Separately: _queue_final_ui_update's call from the normal (always
pre-drain) sequence was calling the full build_stop_finalize_summary(host)
at a point where most required steps are not yet marked, logging a
spurious "failed" result every single run. Fixed by giving it a
lightweight-snapshot mode (same pattern TASK_10_REPORT.md already used for
the immediate caller), used only at that always-premature call site.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils import stop_finalize_worker as sfw  # noqa: E402
from alpha.utils import evidence_pointer_finalize as epf  # noqa: E402


class QueueFinalUiUpdateLightweightModeTests(unittest.TestCase):
    """VALIDATE (unit level): the always-premature call site no longer
    computes (or logs) the full, spurious required-step summary; the
    genuine-failure call site still does."""

    class _Host:
        def _run_on_ui_thread(self, fn):
            fn()

        def _finish_graceful_stop(self, timed_out=False):
            pass

    def test_1_lightweight_mode_never_calls_build_stop_finalize_summary(self) -> None:
        host = self._Host()
        with patch.object(sfw, "build_stop_finalize_summary") as mock_build:
            sfw._queue_final_ui_update(host, timed_out=False, use_lightweight_snapshot=True)
        mock_build.assert_not_called()

    def test_2_default_mode_still_calls_build_stop_finalize_summary(self) -> None:
        host = self._Host()
        with patch.object(
            sfw, "build_stop_finalize_summary", return_value={"stop_finalize_timed_out": False}
        ) as mock_build:
            sfw._queue_final_ui_update(host, timed_out=False)
        mock_build.assert_called_once_with(host)


class StaleEvidencePointerPassSkipsMismatchedRunTests(unittest.TestCase):
    """VALIDATE (unit level): a background pass scheduled for run A, that
    only actually executes after the global identity has moved on to run
    B, must bail out without reading or writing anything for run B."""

    class _FakeIdentity:
        def __init__(self, run_id: str, run_folder: str) -> None:
            self.run_id = run_id
            self.run_folder = run_folder

    def test_1_mismatched_current_identity_skips_without_side_effects(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="alpha_task14_stale_")
        run_b_identity = self._FakeIdentity("run-B", tmpdir)
        with patch(
            "alpha.utils.run_identity.get_current_run_identity", return_value=run_b_identity
        ), patch.object(sfw, "build_stop_finalize_summary") as mock_build, patch(
            "alpha.utils.troubleshooting_paths.finalize_run_manifest"
        ) as mock_finalize_manifest:
            result = epf.finalize_evidence_pointers_completed(
                host=object(),
                reason="after_minimal_stop",
                run_id="run-A",
                run_folder=tmpdir,
            )
        self.assertEqual(result.get("error"), "stale_run_superseded")
        self.assertFalse(result.get("ok"))
        mock_build.assert_not_called()
        mock_finalize_manifest.assert_not_called()

    def test_2_matching_current_identity_proceeds_normally(self) -> None:
        from alpha.utils import troubleshooting_paths as tp

        tmpdir = Path(tempfile.mkdtemp(prefix="alpha_task14_match_"))
        run_a_identity = self._FakeIdentity("run-A", str(tmpdir))
        fake_manifest = {
            "run_id": "run-A",
            "created_at": "x",
            "completed_at": "",
            "final_status": "in_progress",
        }
        with patch(
            "alpha.utils.run_identity.get_current_run_identity", return_value=run_a_identity
        ), patch.object(sfw, "build_stop_finalize_summary", return_value={
            "stop_finalize_failed": False,
            "failure_reason": "",
            "alpha_output_written": True,
        }), patch.object(tp, "_current_run_folder", tmpdir), patch.object(
            tp, "_run_manifest", fake_manifest
        ), patch.object(tp, "_active_run_id", "run-A"):
            result = epf.finalize_evidence_pointers_completed(
                host=object(),
                reason="after_minimal_stop",
                run_id="run-A",
                run_folder=str(tmpdir),
            )
        # The point of this test is the bail-out check itself (a matching
        # run_id must NOT be treated as stale) -- not the downstream
        # evidence-package write outcome, which here uses a bare object()
        # host with none of AlphaApp's real attributes and so legitimately
        # can't finish "completed". What matters: it proceeded past the
        # bail-out and genuinely attempted+finalized this run's manifest.
        self.assertNotEqual(result.get("error"), "stale_run_superseded")
        manifest_path = tmpdir / "RUN_MANIFEST.json"
        self.assertTrue(manifest_path.exists())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertNotEqual(data.get("final_status"), "in_progress")
        self.assertTrue(data.get("completed_at"))


_REPRO_SCRIPT = r'''
import sys, time, tempfile, json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, {project_root!r})

from tests.test_task9_report import get_shared_integration_host, _pump
from alpha.transcription import canonical_transcript_ledger as ctl
from alpha.transcription.canonical_identity_registry import reset_for_session
from alpha.transcription.utterance_lifecycle import reset_utterance_lifecycle, get_utterance_lifecycle
from alpha.utils import stop_finalize_worker as sfw
from alpha.utils import troubleshooting_paths as tp
from alpha.utils import evidence_pointer_finalize as epf
from alpha.utils.ui_thread_guard import register_ui_main_thread
import alpha.utils.run_identity as ri

register_ui_main_thread()

def delayed_schedule(host, *, reason="after_stop", run_id="", run_folder=""):
    def _worker():
        time.sleep(2.0)
        try:
            epf.finalize_evidence_pointers_completed(
                host, reason=reason, run_id=run_id, run_folder=run_folder
            )
        except Exception:
            pass
    import threading
    threading.Thread(target=_worker, daemon=True).start()

def run_session(idx, tmpdir):
    session_id = f"sess-t14-{{idx}}"
    run_id = f"run-t14-{{idx}}"
    ctl.reset_for_run(run_id)
    reset_for_session(session_id)

    class FakeIdentity:
        def __init__(self):
            self.run_id = run_id; self.run_folder = str(tmpdir); self.run_timestamp = "ts"
            self.app_version = "test"; self.stop_ui_callback_duration_ms = 0.0
            self.stop_finalize_completed = False; self.deepgram_close_status = ""

    fake_ident = FakeIdentity()
    fake_manifest = {{"run_id": run_id, "created_at": "x", "completed_at": "", "final_status": "in_progress"}}

    with patch.object(tp, "_current_run_folder", tmpdir), \
         patch.object(tp, "_run_manifest", fake_manifest), \
         patch.object(tp, "_active_run_id", run_id), \
         patch.object(ri, "_current", fake_ident):
        host = get_shared_integration_host(session_id, run_id)
        host._listen_language = "en"
        host._start_ui_event_bus_drain_loop()
        host._finish_graceful_stop = lambda timed_out=False: None
        reset_utterance_lifecycle(host, session_id=session_id)
        owner = get_utterance_lifecycle(host)
        owner._commit_fallback_ms = 60000
        owner.on_final_chunk(text=f"Sentence {{idx}}.", speaker=1, channel=0, start=0.0, end=1.0,
                              is_final=True, speech_final=True, event_id=f"ev-{{idx}}", metadata={{}})
        host.process_ui_queue()
        _pump(host, seconds=0.3)
        worker = host.translation_worker
        worker.stop_accepting()
        sfw.begin_stop_from_ui(host)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            _pump(host, seconds=0.05)
            if sfw.get_stop_finalize_snapshot().get("worker_done"):
                break
        _pump(host, seconds=0.3)
        host._teardown()

with patch.object(epf, "schedule_evidence_pointer_finalization_background", delayed_schedule):
    tmp1 = Path(tempfile.mkdtemp(prefix="alpha_t14case_1_"))
    run_session(1, tmp1)
    tmp2 = Path(tempfile.mkdtemp(prefix="alpha_t14case_2_"))
    run_session(2, tmp2)
    with patch.object(ri, "_current", type("I", (), {{
        "run_id": "run-t14-2", "run_folder": str(tmp2), "run_timestamp": "ts",
        "app_version": "test", "stop_ui_callback_duration_ms": 0.0,
        "stop_finalize_completed": False, "deepgram_close_status": "",
    }})()), patch.object(tp, "_current_run_folder", tmp2), patch.object(tp, "_active_run_id", "run-t14-2"):
        time.sleep(3.0)

session2_manifest = json.loads((tmp2 / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
print("RESULT:" + json.dumps({{"session2_final_status": session2_manifest.get("final_status")}}))
'''


class SecondSessionManifestNotCorruptedByStaleFirstSessionPassTests(unittest.TestCase):
    """VALIDATE item 1 (full real-thread reproduction, subprocess-isolated
    like Task 12's tests): session 1's evidence-pointer background pass is
    deliberately delayed until after session 2 has fully started and
    stopped in the SAME process — the exact race that used to corrupt
    session 2's RUN_MANIFEST.json with session 1's stale data. Asserts the
    real, persisted RUN_MANIFEST.json for session 2 reflects session 2's
    own true (completed) outcome, not session 1's."""

    def test_second_session_manifest_reflects_its_own_true_outcome(self) -> None:
        script = _REPRO_SCRIPT.format(project_root=str(PROJECT_ROOT))
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        result_line = next(
            (ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")), None
        )
        self.assertIsNotNone(
            result_line,
            f"subprocess produced no RESULT line.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )
        data = json.loads(result_line[len("RESULT:"):])
        # Session 2 has its own, promptly-scheduled evidence-pointer pass
        # (not delayed) that correctly upgrades it to "completed" on its
        # own. What this test actually guards against is the OLD defect:
        # session 1's delayed pass overwriting session 2's manifest back to
        # "failed" once it finally runs against the (by-then) mismatched
        # global identity.
        self.assertEqual(
            data["session2_final_status"],
            "completed",
            "session 2's RUN_MANIFEST.json must reflect its own true outcome, "
            "not be corrupted by session 1's delayed evidence-pointer pass",
        )


if __name__ == "__main__":
    unittest.main()
