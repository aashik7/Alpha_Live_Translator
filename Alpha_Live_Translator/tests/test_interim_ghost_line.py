"""Regression tests for the permanent interim ("⏳") ghost-line defect.

Confirmed defect: after a final committed, `_apply_final_interim_comparison`
decided whether to clear the on-screen interim preview purely by text
containment. When the committed final and the displayed interim were
genuinely unrelated (neither contains the other) no branch matched, the
action stayed at its "keep_interim" default, and a stale interim line from
an earlier utterance stayed on screen -- in a live run, permanently.

Two layers are covered here:

1. Identity gate -- `_apply_final_interim_comparison` decides the unrelated
   case from the utterance identity the interim now carries, instead of
   guessing from text. Same utterance -> clear; a genuinely different, still
   live utterance -> keep; identity unavailable -> clear (the observed
   ghost pattern).
2. Liveness watchdog -- `_check_interim_ghost_watchdog` removes any interim
   that has stopped being refreshed, whatever produced it. This is a
   liveness invariant rather than a heuristic, so a permanent ghost stays
   impossible even if layer 1 is wrong or a future code path forgets to
   clear. `test_watchdog_reaps_ghost_even_when_comparison_keeps_it` is the
   test that pins that guarantee.

The tests bind the real AlphaApp methods onto a stub host so the decision
logic is exercised directly, without constructing Tk widgets.
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

_BOUND_METHODS = (
    "_apply_final_interim_comparison",
    "_clear_interim_tail",
    "_check_interim_ghost_watchdog",
    "_handle_interim_transcript_ui",
    "on_interim_transcript",
)


class InterimHost:
    """Minimal host carrying the real methods under test, with a fake display."""

    def __init__(self):
        self._latest_interim_text = ""
        self._latest_interim_speaker = 1
        self._latest_interim_utterance_id = ""
        self._latest_interim_committed = False
        self._last_final_text = ""
        self._last_interim_ui_at = 0.0
        self._pending_interim = None
        self._interim_flush_posted = False
        self.display_removed_count = 0
        self.logs = []

    def _remove_interim_line_from_display(self):
        self.display_removed_count += 1

    def _interim_log(self, message, data):
        self.logs.append((message, data))

    def _normalize_compare(self, text):
        return " ".join((text or "").strip().lower().split())

    def _schedule_interim_flush_main_thread(self):
        pass

    def _update_interim_line_only(self):
        pass

    def _is_japanese_manual_mode(self):
        return False

    # --- helpers -----------------------------------------------------
    def last_comparison_action(self):
        for message, data in reversed(self.logs):
            if message == "[INTERIM] final comparison":
                return data["action"]
        return None

    def make_interim_stale(self):
        self._last_interim_ui_at = time.perf_counter() - (
            (INTERIM_GHOST_TTL_MS + 500) / 1000.0
        )


for _name in _BOUND_METHODS:
    setattr(InterimHost, _name, getattr(AlphaApp, _name))


class TestFinalInterimComparison(unittest.TestCase):
    """Layer 1: which interim lines survive a final commit."""

    def test_equal_text_clears_interim(self):
        # The most common case: `in` matches both ways, so branch order
        # decides. Guards the earlier BUG-B fix against regression.
        host = InterimHost()
        host._latest_interim_text = "hello world"
        host._apply_final_interim_comparison("hello world")
        self.assertEqual(host.last_comparison_action(), "clear_interim")
        self.assertEqual(host._latest_interim_text, "")

    def test_interim_covered_by_final_clears(self):
        host = InterimHost()
        host._latest_interim_text = "hello"
        host._apply_final_interim_comparison("hello world")
        self.assertEqual(host.last_comparison_action(), "clear_interim")
        self.assertEqual(host._latest_interim_text, "")

    def test_final_covered_by_longer_interim_keeps(self):
        host = InterimHost()
        host._latest_interim_text = "hello world today"
        host._apply_final_interim_comparison("hello world")
        self.assertEqual(host.last_comparison_action(), "keep_interim")
        self.assertEqual(host._latest_interim_text, "hello world today")

    def test_no_interim_is_a_noop(self):
        host = InterimHost()
        host._apply_final_interim_comparison("anything")
        self.assertEqual(host.last_comparison_action(), "no_interim")

    def test_unrelated_without_identity_clears_ghost(self):
        # The confirmed defect, using text shaped like the real captured
        # case (a short new final committing while a long stale interim
        # from a previous utterance is still displayed).
        host = InterimHost()
        host._latest_interim_text = (
            "So this conversation is a casual everyday chat between two friends"
        )
        host._apply_final_interim_comparison("The complete")
        self.assertEqual(host.last_comparison_action(), "clear_interim_unrelated")
        self.assertEqual(host._latest_interim_text, "")

    def test_unrelated_same_utterance_clears(self):
        # Text diverged through a merge/correction, but it is still the
        # same utterance -- the final supersedes the preview.
        host = InterimHost()
        host._latest_interim_text = "terry is here"
        host._latest_interim_utterance_id = "U-7"
        host._apply_final_interim_comparison("tariqul was there", utterance_id="U-7")
        self.assertEqual(host.last_comparison_action(), "clear_interim_same_utterance")
        self.assertEqual(host._latest_interim_text, "")

    def test_unrelated_different_utterance_keeps_live_interim(self):
        # A delayed final for an older utterance must not wipe the preview
        # of a newer one that is still in progress.
        host = InterimHost()
        host._latest_interim_text = "and then I went to the office"
        host._latest_interim_utterance_id = "U-9"
        host._apply_final_interim_comparison("The complete", utterance_id="U-8")
        self.assertEqual(
            host.last_comparison_action(), "keep_interim_other_utterance"
        )
        self.assertEqual(host._latest_interim_text, "and then I went to the office")

    def test_unrelated_with_identity_on_one_side_only_clears(self):
        host = InterimHost()
        host._latest_interim_text = "stale ghost text here"
        host._apply_final_interim_comparison("brand new final", utterance_id="U-3")
        self.assertEqual(host.last_comparison_action(), "clear_interim_unrelated")
        self.assertEqual(host._latest_interim_text, "")


class TestInterimGhostWatchdog(unittest.TestCase):
    """Layer 2: the liveness backstop that makes a permanent ghost impossible."""

    def test_stale_interim_is_cleared(self):
        host = InterimHost()
        host._latest_interim_text = "orphaned ghost line"
        host.make_interim_stale()
        host._check_interim_ghost_watchdog()
        self.assertEqual(host._latest_interim_text, "")

    def test_fresh_interim_is_kept(self):
        # Normal streaming must never be interrupted by the watchdog.
        host = InterimHost()
        host._latest_interim_text = "live streaming interim"
        host._last_interim_ui_at = time.perf_counter()
        host._check_interim_ghost_watchdog()
        self.assertEqual(host._latest_interim_text, "live streaming interim")

    def test_no_interim_causes_no_display_churn(self):
        host = InterimHost()
        host._last_interim_ui_at = time.perf_counter() - 60.0
        host._check_interim_ghost_watchdog()
        self.assertEqual(host.display_removed_count, 0)

    def test_never_rendered_interim_is_not_cleared_prematurely(self):
        host = InterimHost()
        host._latest_interim_text = "never rendered"
        host._last_interim_ui_at = 0.0
        host._check_interim_ghost_watchdog()
        self.assertEqual(host._latest_interim_text, "never rendered")

    def test_watchdog_reaps_ghost_even_when_comparison_keeps_it(self):
        # The guarantee: whatever layer 1 decides, an interim that stops
        # being refreshed is removed. Do not weaken this test -- it is what
        # makes a permanent ghost line structurally impossible.
        host = InterimHost()
        host._latest_interim_text = "and then I went to the office"
        host._latest_interim_utterance_id = "U-9"
        host._apply_final_interim_comparison("The complete", utterance_id="U-8")
        self.assertTrue(host._latest_interim_text, "precondition: comparison kept it")
        host.make_interim_stale()
        host._check_interim_ghost_watchdog()
        self.assertEqual(host._latest_interim_text, "")


class TestInterimIdentityPlumbing(unittest.TestCase):
    """Identity must survive deepgram_client's double delivery of each interim."""

    def test_raw_delivery_does_not_wipe_lifecycle_identity(self):
        # Every interim arrives twice: once from utterance_lifecycle (with
        # canonical_utterance_id) and once raw (without). The raw one lands
        # last, so a plain overwrite would discard the only identity there is.
        host = InterimHost()
        host.on_interim_transcript(
            1, "my name is", metadata={"canonical_utterance_id": "U-42"}
        )
        host.on_interim_transcript(
            1, "my name is", metadata={"is_final": False, "channel_index": [0, 1]}
        )
        self.assertEqual(
            host._pending_interim[2].get("canonical_utterance_id"), "U-42"
        )

    def test_new_identity_replaces_previous(self):
        host = InterimHost()
        host.on_interim_transcript(
            1, "first", metadata={"canonical_utterance_id": "U-42"}
        )
        host.on_interim_transcript(
            1, "second", metadata={"canonical_utterance_id": "U-43"}
        )
        self.assertEqual(
            host._pending_interim[2].get("canonical_utterance_id"), "U-43"
        )

    def test_different_text_does_not_inherit_stale_identity(self):
        # Carry-forward is keyed on identical text, so a new utterance's
        # id-less interim never adopts the previous utterance's id.
        host = InterimHost()
        host.on_interim_transcript(
            1, "first utterance", metadata={"canonical_utterance_id": "U-50"}
        )
        host.on_interim_transcript(1, "totally different second utterance", metadata={})
        self.assertIsNone(host._pending_interim[2].get("canonical_utterance_id"))

    def test_handler_stores_identity(self):
        host = InterimHost()
        host._handle_interim_transcript_ui(
            2, "hello there", metadata={"canonical_utterance_id": "U-77"}
        )
        self.assertEqual(host._latest_interim_utterance_id, "U-77")

    def test_identity_survives_later_id_less_update(self):
        host = InterimHost()
        host._handle_interim_transcript_ui(
            2, "hello there", metadata={"canonical_utterance_id": "U-77"}
        )
        host._handle_interim_transcript_ui(2, "hello there friend", metadata={})
        self.assertEqual(host._latest_interim_utterance_id, "U-77")

    def test_clear_resets_identity(self):
        host = InterimHost()
        host._handle_interim_transcript_ui(
            2, "hello there", metadata={"canonical_utterance_id": "U-77"}
        )
        host._clear_interim_tail()
        self.assertEqual(host._latest_interim_utterance_id, "")


if __name__ == "__main__":
    unittest.main()
