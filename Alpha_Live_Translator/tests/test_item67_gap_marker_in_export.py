"""Item 67: the connection-gap marker never reached the exported transcript.

`[connection lost - approximately Ns of audio not captured]` was emitted
correctly and reached the live UI store, but `Alpha output.txt` is built from
the frozen canonical ledger and the marker cannot become a ledger record: it is
synthetic, carries no `source_raw_event_ids`, and `RAW_EVENT_LINEAGE_REQUIRED`
means every canonical record must trace to a real provider event. Measured on
run `...20260814-101813`, which emitted `raw-000003` with the marker text and
still exported 6 records with no mention of the 31-second hole.

So a client reading the exported transcript saw continuous speech across a gap.
That is precisely what item 44's "mark the gap visibly" exists to prevent, and
the half that was still missing.

The rule is NOT weakened. The gap is recorded beside the ledger and rendered at
export time, prepended into the entry of the record that follows it -- which
keeps `lines` 1:1 with `record_ids`, because every downstream coverage gate
pairs those two lists by index and a standalone line would shift them apart.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import DG_GAP_MARKER_TEMPLATE  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ledger  # noqa: E402


class GapMarkerReachesTheExportTest(unittest.TestCase):
    def setUp(self):
        ledger.reset_for_run("item-67")

    def _add(self, text, created_at, uid):
        ledger.apply_decision(
            speaker=1,
            assembler_text=text,
            final_text=text,
            requested_action="append",
            applied_action="append",
            source_raw_event_ids=[f"raw-{uid}"],
            commit_reason="utterance_end",
            metadata={
                "session_id": "s",
                "channel_index": 0,
                "canonical_utterance_id": uid,
                "source_version": 1,
            },
        )
        ledger._records[-1]["created_at"] = created_at

    def _export(self):
        return ledger.serialize_export_payload({"records": ledger._records})

    def test_the_marker_appears_in_the_exported_text(self):
        self._add("Before the drop.", 100.0, "U1")
        ledger.record_connection_gap(seconds=31.0, at=115.0)
        self._add("After the reconnect.", 150.0, "U2")
        self.assertIn("connection lost", self._export()["text"])

    def test_it_lands_between_the_records_it_separates(self):
        self._add("First before.", 100.0, "U1")
        self._add("Second before.", 110.0, "U2")
        ledger.record_connection_gap(seconds=31.0, at=115.0)
        self._add("After the hole.", 150.0, "U3")
        text = self._export()["text"]
        self.assertLess(text.index("Second before."), text.index("connection lost"))
        self.assertLess(text.index("connection lost"), text.index("After the hole."))

    def test_lines_stay_one_to_one_with_record_ids(self):
        """Coverage gates pair these lists by index; a standalone marker line
        would shift them apart."""
        self._add("One.", 100.0, "U1")
        ledger.record_connection_gap(seconds=12.0, at=105.0)
        self._add("Two.", 110.0, "U2")
        ledger.record_connection_gap(seconds=8.0, at=115.0)
        self._add("Three.", 120.0, "U3")
        payload = self._export()
        self.assertEqual(len(payload["lines"]), len(payload["record_ids"]))
        self.assertEqual(len(payload["record_ids"]), 3)

    def test_the_wording_matches_what_the_live_ui_shows(self):
        self._add("Before.", 100.0, "U1")
        ledger.record_connection_gap(seconds=31.0, at=105.0)
        self._add("After.", 110.0, "U2")
        expected = DG_GAP_MARKER_TEMPLATE.format(seconds=31)
        self.assertIn(expected, self._export()["text"])

    def test_a_gap_after_the_last_commit_is_still_shown(self):
        """A drop the session never recovered speech from still has to be
        visible rather than dropped for having no record after it."""
        self._add("Only line.", 100.0, "U1")
        ledger.record_connection_gap(seconds=44.0, at=200.0)
        text = self._export()["text"]
        self.assertIn("connection lost", text)
        self.assertLess(text.index("Only line."), text.index("connection lost"))

    def test_several_gaps_all_appear(self):
        self._add("One.", 100.0, "U1")
        ledger.record_connection_gap(seconds=10.0, at=105.0)
        self._add("Two.", 110.0, "U2")
        ledger.record_connection_gap(seconds=20.0, at=115.0)
        self._add("Three.", 120.0, "U3")
        text = self._export()["text"]
        self.assertEqual(text.count("connection lost"), 2)
        self.assertIn("approximately 10s", text)
        self.assertIn("approximately 20s", text)

    def test_no_gap_leaves_the_export_untouched(self):
        """Behaviour must be byte-identical for a session with no outage."""
        self._add("Only line here.", 100.0, "U1")
        self.assertEqual(self._export()["text"], "Speaker: Only line here.\n")

    def test_a_zero_or_negative_gap_is_ignored(self):
        self._add("Only line.", 100.0, "U1")
        ledger.record_connection_gap(seconds=0.0, at=105.0)
        ledger.record_connection_gap(seconds=-5.0, at=106.0)
        self.assertNotIn("connection lost", self._export()["text"])


class GapRegistryLifecycleTest(unittest.TestCase):
    def test_gaps_are_cleared_between_runs(self):
        """Otherwise one session's outage annotates the next session's export."""
        ledger.reset_for_run("run-a")
        ledger.record_connection_gap(seconds=31.0, at=100.0)
        self.assertEqual(len(ledger.get_connection_gaps()), 1)
        ledger.reset_for_run("run-b")
        self.assertEqual(ledger.get_connection_gaps(), [])

    def test_recording_never_raises(self):
        """Annotating must never disturb the reconnect that just succeeded."""
        ledger.reset_for_run("run-c")
        ledger.record_connection_gap(seconds="not a number", at=None)  # type: ignore[arg-type]
        self.assertEqual(ledger.get_connection_gaps(), [])

    def test_the_marker_is_not_a_ledger_record(self):
        """RAW_EVENT_LINEAGE_REQUIRED stays intact -- no synthetic record."""
        ledger.reset_for_run("run-d")
        ledger.record_connection_gap(seconds=31.0, at=100.0)
        self.assertEqual(len(ledger._records), 0)


if __name__ == "__main__":
    unittest.main()
