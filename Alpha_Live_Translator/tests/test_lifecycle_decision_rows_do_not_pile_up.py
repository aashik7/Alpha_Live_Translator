"""The lifecycle owner's in-memory decision rows must not pile up.

WHAT WAS BROKEN (audit 2026-09-14, bug #3)
-----------------------------------------
`UtteranceLifecycleOwner._record_decision` appends one dict per decision to
`self._events`, and nothing ever removed one. The owner is a process singleton
(`get_utterance_lifecycle`), and `reset_for_session` -- which runs at every
Start -- zeroed the stats but left the list alone. So every decision of every
meeting since the app opened stayed in memory, and nothing in the app reads the
list: `events()` has no caller, and the rows that matter are already written to
the event log file.

Measured before the fix through the real `on_interim`: 300 decisions gave 300
rows, and after `reset_for_session` all 300 rows of the previous meeting were
still there. The audit measured 627 bytes a row with tracemalloc.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import utterance_lifecycle as ul  # noqa: E402


def _speak(owner, count, offset=0):
    """Real interims through the real public entry, one decision row each."""
    for i in range(offset, offset + count):
        owner.on_interim(
            text="Thank you all for joining today, let us review quarter %d" % (i // 10),
            start=i * 0.1,
            end=i * 0.1 + 0.5,
            channel=0,
            event_id="evt-%d" % i,
        )


class DecisionRowsTest(unittest.TestCase):
    def setUp(self):
        self.owner = ul.UtteranceLifecycleOwner()
        self.owner.reset_for_session("meeting-1")

    def tearDown(self):
        self.owner.force_cancel_active("test_teardown")

    def test_a_new_meeting_keeps_no_rows_from_the_last_one(self):
        _speak(self.owner, 300)
        self.assertTrue(self.owner.events(), "fixture: no decision rows were recorded")

        self.owner.reset_for_session("meeting-2")
        leftover = [e for e in self.owner.events() if e.get("session_id") == "meeting-1"]
        self.assertEqual(
            len(leftover),
            0,
            "%d decision rows from the previous meeting survived the session reset"
            % len(leftover),
        )

    def test_a_long_meeting_keeps_a_bounded_number_of_rows(self):
        kept = int(getattr(ul, "LIFECYCLE_EVENTS_KEPT", 1000))
        _speak(self.owner, kept + 500)
        self.assertLessEqual(
            len(self.owner.events()),
            kept,
            "a single meeting's decision rows grow without bound",
        )
        rows = self.owner.events()
        self.assertEqual(rows[-1].get("event_id"), "evt-%d" % (kept + 499), "the newest row was dropped")
        self.assertEqual(rows[0].get("event_id"), "evt-500", "the bound did not drop the oldest rows")

    def test_the_rows_kept_are_the_newest_in_order(self):
        """Guard: bounding must drop the oldest rows, not the newest."""
        _speak(self.owner, 50)
        rows = self.owner.events()
        self.assertEqual(len(rows), 50)
        order = [int(str(r.get("event_id")).rsplit("-", 1)[1]) for r in rows]
        self.assertEqual(order, sorted(order), "rows are no longer in decision order")
        self.assertEqual(order[-1], 49, "the newest decision is not the last row")


if __name__ == "__main__":
    unittest.main()
