"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, item 11b.

Confirmed live defect (run `v3.3.5.5.8.5.26.5.3-20260809-033339`, ja):

    +261.15s  [INTERIM] received                text_len=10
    +267.19s  [INTERIM] ghost watchdog cleared  text_len=10 stale_ms=6039.2
    +268.25s  [INTERIM] stop tail ...           latest_interim_len=0
              [INTERIM] stop tail skipped       reason=empty_interim

The interim ghost watchdog enforces a *display* liveness invariant, but it
cleared `_latest_interim_text` -- the only source
`_recover_interim_tail_on_stop` reads. So a tail orphaned shortly before
Stop was destroyed by the display layer 1.06s before the content-recovery
path ran, and the speech (`思って-何、何、何、`) is absent from that run's
final export. `Bug Report.md` §4.3 predicted exactly this interaction.

The consequence is that items 10 and 11 -- both of which fixed real
containment defects in that recovery path -- were unreachable in precisely
the scenario they exist for. Fixing their comparison logic without this is
fixing a filter on a pipe that has already been emptied.

The fix has the watchdog stash the orphan before clearing, and has the
Stop path fall back to it. Whether the orphan is *safe* to commit stays
the job of `_check_stop_tail_duplicate` (item 10) and
`_should_commit_interim_recovery` (item 11) -- deliberately, so an orphan
that did later get committed is filtered there rather than pre-emptively
lost here.
"""

import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import INTERIM_GHOST_TTL_MS  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402


class _Host:
    """Minimal host exercising the real watchdog + real recovery decision."""

    _check_interim_ghost_watchdog = AlphaApp._check_interim_ghost_watchdog
    _clear_interim_tail = AlphaApp._clear_interim_tail
    _discard_watchdog_orphaned_interim = AlphaApp._discard_watchdog_orphaned_interim

    def __init__(self, interim_text, speaker=2, utterance_id="u-42"):
        self._latest_interim_text = interim_text
        self._latest_interim_speaker = speaker
        self._latest_interim_utterance_id = utterance_id
        self._latest_interim_committed = False
        self._watchdog_orphaned_interim_text = ""
        self._watchdog_orphaned_interim_speaker = 1
        self._watchdog_orphaned_interim_utterance_id = ""
        self.removed_from_display = 0
        self.logged = []
        # Make the interim look stale enough for the watchdog to fire.
        self._last_interim_ui_at = time.perf_counter() - (
            (INTERIM_GHOST_TTL_MS + 500) / 1000.0
        )

    def _remove_interim_line_from_display(self):
        self.removed_from_display += 1

    def _interim_log(self, message, data=None):
        self.logged.append((message, data or {}))

    # --- stubs needed only by _handle_interim_transcript_ui ---
    def _update_interim_line_only(self):
        pass


class TestWatchdogPreservesOrphanForRecovery(unittest.TestCase):
    def test_watchdog_stashes_the_text_it_clears(self):
        host = _Host("思って-何、何、何、")

        host._check_interim_ghost_watchdog()

        # Display side of the ghost fix is unchanged: line removed, live
        # interim state cleared.
        self.assertEqual(host._latest_interim_text, "")
        self.assertEqual(
            host.removed_from_display,
            1,
            "the visible ghost line must still be removed immediately",
        )
        # Content side: the orphan survives for Stop-time recovery.
        self.assertEqual(host._watchdog_orphaned_interim_text, "思って-何、何、何、")
        self.assertEqual(host._watchdog_orphaned_interim_speaker, 2)
        self.assertEqual(host._watchdog_orphaned_interim_utterance_id, "u-42")

    def test_watchdog_logs_that_it_preserved_the_orphan(self):
        host = _Host("some pending tail text")
        host._check_interim_ghost_watchdog()

        cleared = [d for m, d in host.logged if "ghost watchdog cleared" in m]
        self.assertEqual(len(cleared), 1)
        self.assertTrue(cleared[0].get("orphan_preserved_for_stop_recovery"))

    def test_watchdog_does_not_fire_on_a_fresh_interim(self):
        # Guard: the fix must not make the watchdog fire more eagerly.
        host = _Host("still being spoken")
        host._last_interim_ui_at = time.perf_counter()

        host._check_interim_ghost_watchdog()

        self.assertEqual(host._latest_interim_text, "still being spoken")
        self.assertEqual(host._watchdog_orphaned_interim_text, "")
        self.assertEqual(host.removed_from_display, 0)

    def test_watchdog_stamps_when_the_orphan_was_taken(self):
        # The stamp is what lets the Stop path distinguish "nothing happened
        # since" from "the speaker carried on" -- see
        # TestStaleOrphanIsNotResurrected.
        host = _Host("orphaned mid-sentence tail")
        before = host._last_interim_ui_at

        host._check_interim_ghost_watchdog()

        self.assertEqual(host._watchdog_orphaned_interim_at, before)

    def test_discard_clears_the_stash(self):
        host = _Host("orphaned tail")
        host._check_interim_ghost_watchdog()
        self.assertTrue(host._watchdog_orphaned_interim_text)

        host._discard_watchdog_orphaned_interim()

        self.assertEqual(host._watchdog_orphaned_interim_text, "")
        self.assertEqual(host._watchdog_orphaned_interim_speaker, 1)
        self.assertEqual(host._watchdog_orphaned_interim_utterance_id, "")


class _RecoveryHost:
    """Drives the real Stop-time recovery decision over a stashed orphan."""

    _recover_interim_tail_on_stop = AlphaApp._recover_interim_tail_on_stop
    _check_stop_tail_duplicate = AlphaApp._check_stop_tail_duplicate
    _should_commit_interim_recovery = AlphaApp._should_commit_interim_recovery
    _get_last_final_text_for_recovery = AlphaApp._get_last_final_text_for_recovery
    _normalize_compare = AlphaApp._normalize_compare
    _merge_text_with_overlap_info = AlphaApp._merge_text_with_overlap_info
    _clear_interim_tail = AlphaApp._clear_interim_tail
    _discard_watchdog_orphaned_interim = AlphaApp._discard_watchdog_orphaned_interim
    _reset_segment_repair_state = AlphaApp._reset_segment_repair_state

    def __init__(self, orphan, orphan_at=100.0, last_interim_at=100.0):
        self._latest_interim_text = ""
        self._latest_interim_speaker = 1
        self._latest_interim_utterance_id = ""
        self._latest_interim_committed = False
        self._watchdog_orphaned_interim_text = orphan
        self._watchdog_orphaned_interim_speaker = 2
        self._watchdog_orphaned_interim_utterance_id = "u-9"
        self._watchdog_orphaned_interim_at = orphan_at
        self._last_interim_ui_at = last_interim_at
        self._last_final_text = ""
        self.transcript_store = None
        self.committed = []
        self.logged = []
        self._teams_pending_commit_override = None

    def _is_japanese_manual_mode(self):
        return False

    def _remove_interim_line_from_display(self):
        pass

    def _interim_log(self, message, data=None):
        self.logged.append((message, data or {}))

    def _display_transcript_item(self, item):
        self.committed.append(item)

    def _track_committed_segment_meta(self, item, text):
        pass

    def reasons(self):
        return [d.get("reason") for m, d in self.logged if "skipped" in m]


class TestOrphanReachesTheRecoveryPath(unittest.TestCase):
    """The whole point of 11b: items 10/11 must actually get to judge it."""

    def test_orphan_is_committed_when_nothing_happened_since(self):
        # The exact live case: watchdog cleared at +267.19s, Stop at +268.25s,
        # no interim in between. Pre-11b this logged reason=empty_interim and
        # the speech was lost.
        host = _RecoveryHost(
            "and that is the last thing I wanted to say",
            orphan_at=100.0,
            last_interim_at=100.0,
        )

        host._recover_interim_tail_on_stop()

        self.assertEqual(len(host.committed), 1, host.logged)
        self.assertEqual(
            host.committed[0]["text"], "and that is the last thing I wanted to say"
        )
        self.assertEqual(
            host.committed[0]["speaker"], 2, "the orphan's speaker must be carried over"
        )
        self.assertEqual(host._watchdog_orphaned_interim_text, "")

    def test_empty_orphan_still_reports_empty_interim(self):
        host = _RecoveryHost("")
        host._recover_interim_tail_on_stop()
        self.assertEqual(host.committed, [])
        self.assertIn("empty_interim", host.reasons())


class TestStaleOrphanIsNotResurrected(unittest.TestCase):
    def test_orphan_superseded_by_a_later_interim_is_dropped(self):
        # Speaker carried on after the watchdog fired: the orphan is stale and
        # committing it would append old text at the END of the transcript.
        host = _RecoveryHost(
            "an old fragment from minutes ago",
            orphan_at=100.0,
            last_interim_at=250.0,
        )

        host._recover_interim_tail_on_stop()

        self.assertEqual(host.committed, [], "a superseded orphan must not commit")
        self.assertTrue(
            any("orphan superseded" in m for m, _ in host.logged),
            "the supersession must be logged, not silent",
        )
        self.assertIn("empty_interim", host.reasons())


class TestSessionResetDropsTheOrphan(unittest.TestCase):
    """A stale orphan must never reach a *different* session's transcript.

    The reset lives inside `begin_live_session`, which also does run-folder
    creation, evidence bootstrap and ledger identity work -- calling it here
    would test the filesystem, not this fix. So this asserts the wiring
    directly, the same approach (and for the same reason) as Batch 2 item
    7's two wiring tests.
    """

    def test_begin_live_session_resets_the_orphan_fields(self):
        import inspect

        from alpha.utils import session_runtime

        src = inspect.getsource(session_runtime.begin_live_session)
        for field in (
            "_watchdog_orphaned_interim_text",
            "_watchdog_orphaned_interim_speaker",
            "_watchdog_orphaned_interim_utterance_id",
            "_watchdog_orphaned_interim_at",
        ):
            self.assertIn(
                f"host.{field} =",
                src,
                f"{field} must be reset per session, next to _latest_interim_text",
            )


if __name__ == "__main__":
    unittest.main()
