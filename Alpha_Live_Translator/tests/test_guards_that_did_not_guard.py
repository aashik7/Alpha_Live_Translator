"""Two guards that could not do the thing their name claims.

Both were found by the phase 3 audit and share one shape: a check whose result
was fixed before it ran, and a label that told a reader it had run.

ITEM 13 -- the lifecycle's stale-session guard
----------------------------------------------
    session_id = self._session_id or str(
        getattr(self._host, "_live_session_id", "") or ""
    )
    if session_id and self._session_id and session_id != self._session_id:
        ... reject with reason "session_mismatch"

`session_id` is assigned `self._session_id` whenever that is truthy, so by the
time the comparison runs the two are the same value; and when `self._session_id`
is falsy the `and self._session_id` term has already made the guard False.
Evaluated over every input shape, it never fires. The host's `_live_session_id`
-- the only value that could disagree -- is consulted ONLY when the lifecycle
has no session of its own, which is exactly when there is nothing to disagree
with.

The commit authority still fails closed downstream: `canonical_identity_registry`
compares two genuinely independent values and does reject. What the dead guard
costs is that the stale event reaches the lifecycle's OWN state first, so a
final belonging to a previous session can extend or replace the CURRENT
session's active utterance before anything refuses the write. That matters more
alongside the Stop/Start window, which is when a stale event is in flight.

ITEM 15 -- `TK_CALL_SITE_SAFE`
------------------------------
`scan_tk_call_sites` counts the substring `.after(` in two files -- comments and
docstrings included -- applies no test of any kind, and then writes an evidence
event named `TK_CALL_SITE_SAFE`. A reader working through a client's log
concludes the Tk call sites were audited and cleared. They were counted.

The cost is not a crash. It is that the question gets retired without being
asked, which is how the WASAPI reader sat unreported behind a scan that could
not see it.
"""

import ast
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    IGNORE_DUPLICATE,
    UtteranceLifecycleOwner,
)


class _Host:
    def __init__(self, live_session_id=""):
        self._live_session_id = live_session_id


class TheStaleSessionGuardCanFireTest(unittest.TestCase):
    """Driven through the real `_ingest`, not by reading the condition."""

    def _owner(self, *, lifecycle_session, host_session):
        owner = UtteranceLifecycleOwner(host=_Host(host_session))
        owner._session_id = lifecycle_session
        return owner

    def _ingest(self, owner, text="これはテストです"):
        return owner._ingest(
            text=text,
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=True,
            event_id="ev-1",
            metadata={},
            source="test",
        )

    def test_a_final_from_a_previous_session_is_rejected(self):
        owner = self._owner(lifecycle_session="SESSION-A", host_session="SESSION-B")
        decision = self._ingest(owner)
        self.assertEqual(
            decision.decision,
            IGNORE_DUPLICATE,
            "a final belonging to a previous session was accepted; it can now "
            "extend or replace the current session's active utterance",
        )
        self.assertEqual(decision.reason, "session_mismatch")

    def test_a_stale_event_does_not_extend_the_live_active_utterance(self):
        """The actual harm the guard exists to prevent.

        An interim is used because that is what BUILDS the active utterance; a
        final commits and leaves it empty, so asserting on it after one
        would pass whether or not the guard worked.
        """
        owner = self._owner(lifecycle_session="SESSION-A", host_session="SESSION-A")
        owner._ingest(
            text="本日はよろしくお願いします", speaker=1, channel=0, start=0.0, end=1.0,
            is_final=False, speech_final=False, event_id="ev-live",
            metadata={}, source="test",
        )
        live = owner._active
        self.assertIsNotNone(live, "no active utterance to protect; test is vacuous")
        live_text = live.text

        owner._host._live_session_id = "SESSION-B"      # a new session began
        owner._ingest(
            text="まったく別のセッションの文章", speaker=1, channel=0, start=1.0, end=2.0,
            is_final=False, speech_final=False, event_id="ev-stale",
            metadata={}, source="test",
        )
        self.assertEqual(
            owner._active.text, live_text,
            "an event from a different session extended the live utterance",
        )

    def test_a_matching_session_is_not_rejected(self):
        """The guard must not start refusing ordinary traffic."""
        owner = self._owner(lifecycle_session="SESSION-A", host_session="SESSION-A")
        decision = self._ingest(owner)
        self.assertNotEqual(decision.reason, "session_mismatch")

    def test_a_host_with_no_session_is_not_rejected(self):
        """Startup: the host has not published a session id yet."""
        owner = self._owner(lifecycle_session="SESSION-A", host_session="")
        decision = self._ingest(owner)
        self.assertNotEqual(decision.reason, "session_mismatch")

    def test_a_lifecycle_with_no_session_adopts_rather_than_rejects(self):
        owner = self._owner(lifecycle_session="", host_session="SESSION-B")
        decision = self._ingest(owner)
        self.assertNotEqual(decision.reason, "session_mismatch")
        self.assertEqual(owner._session_id, "SESSION-B")


class TheTkScanDoesNotClaimSafetyTest(unittest.TestCase):
    """Item 15. Walked with the AST -- this file quotes the old name in prose."""

    SRC = PROJECT_ROOT / "alpha" / "utils" / "tk_thread_guard.py"

    def _events(self):
        tree = ast.parse(self.SRC.read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "scan_tk_call_sites"
        )
        names = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "jp_accuracy_log":
                if n.args and isinstance(n.args[0], ast.Constant):
                    names.append(n.args[0].value)
        return names

    def test_no_emitted_event_calls_a_bare_count_safe(self):
        offenders = [e for e in self._events() if "SAFE" in e.upper()]
        self.assertEqual(
            offenders,
            [],
            "these events assert safety after counting the substring "
            "'.after(' in two files, with no check of any kind: %r" % (offenders,),
        )

    def test_it_still_reports_the_count(self):
        """The number is useful; only the claim attached to it was wrong."""
        self.assertTrue(
            self._events(),
            "the scan stopped reporting entirely -- the count is worth keeping",
        )

    def test_the_function_still_runs(self):
        from alpha.utils.tk_thread_guard import scan_tk_call_sites

        result = scan_tk_call_sites()
        self.assertIn("safe", result)
        self.assertGreater(result["safe"], 0)


if __name__ == "__main__":
    unittest.main()
