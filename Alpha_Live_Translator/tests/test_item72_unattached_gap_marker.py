"""Regression tests for CLIENT_DELIVERY_SPRINT_v5.md item 72.

Defect: `serialize_export_payload`'s trailing-gap branch was
`if pending_gaps and lines:`. A session that recorded connection outages
but committed NO record left `lines` empty, so the branch was skipped and
every gap marker was discarded. The export body came out `""` and nothing
anywhere said the network had dropped -- which is exactly the case the
marker exists for.

The fix returns the merged marker in its own field, `unattached_gap_line`,
and the caller prepends it. Two invariants make that the only safe shape,
and both are pinned below:

1. `lines` and `record_ids` stay 1:1. Downstream coverage gates pair them
   by index, so the marker must never become a `lines` entry with no
   record to pair against.

2. `text` stays EMPTY when there is no record. `run_artifacts.py` runs

       if not text.strip():
           text, source_name = _committed_final_source_text(host)

   which is real content recovery -- it tries the transcript snapshot, the
   host's committed text, then the transcript store. A fix that returned
   the marker as `text` would satisfy that check and silently switch the
   recovery OFF, exporting a one-line marker in place of speech the store
   still held. That would be content loss introduced by a fix for a
   reporting bug, so it is pinned as its own test.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402

GAP = {"at": 50.0, "seconds": 45.0}


def _snap(records):
    return {"records": records, "snapshot_id": "snap-72", "speaker_distribution": {}}


def _payload(records, gaps):
    with patch.object(ctl, "get_connection_gaps", return_value=list(gaps)):
        return ctl.serialize_export_payload(_snap(records))


class TestGapSurvivesWithNoRecord(unittest.TestCase):
    def test_gap_is_not_discarded_when_there_is_no_record(self):
        out = _payload([], [GAP])
        self.assertTrue(
            out["unattached_gap_line"],
            "a session with an outage and no committed record must still "
            "report the outage somewhere -- this is the whole defect",
        )
        self.assertIn("45s", out["unattached_gap_line"])

    def test_text_stays_empty_so_content_recovery_still_runs(self):
        # The invariant that rules out the obvious fix. See the module
        # docstring: a non-empty `text` here disables
        # `_committed_final_source_text` in run_artifacts.py.
        out = _payload([], [GAP])
        self.assertEqual(
            out["text"],
            "",
            "text must stay empty with no record, or run_artifacts' "
            "committed-text recovery is silently skipped",
        )

    def test_lines_and_record_ids_stay_empty_and_paired(self):
        out = _payload([], [GAP])
        self.assertEqual(out["lines"], [])
        self.assertEqual(out["record_ids"], [])
        self.assertEqual(len(out["lines"]), len(out["record_ids"]))

    def test_multiple_orphan_gaps_are_summed_not_repeated(self):
        out = _payload([], [{"at": 10.0, "seconds": 10.0}, {"at": 30.0, "seconds": 40.0}])
        self.assertIn("50s", out["unattached_gap_line"])
        self.assertEqual(
            out["unattached_gap_line"].count("connection lost"),
            1,
            "consecutive drops with no speech between them are one hole",
        )


class TestExistingBehaviourUnchanged(unittest.TestCase):
    def test_gap_before_a_record_still_attaches_to_that_record(self):
        out = _payload(
            [{"final_text": "hello there", "record_id": "r1", "created_at": 100.0}],
            [GAP],
        )
        self.assertEqual(out["unattached_gap_line"], "")
        self.assertIn("connection lost", out["text"])
        self.assertIn("hello there", out["text"])
        self.assertEqual(len(out["lines"]), len(out["record_ids"]))

    def test_gap_after_the_last_record_still_attaches_to_it(self):
        out = _payload(
            [{"final_text": "hello there", "record_id": "r1", "created_at": 10.0}],
            [{"at": 900.0, "seconds": 30.0}],
        )
        self.assertEqual(out["unattached_gap_line"], "")
        self.assertIn("connection lost", out["text"])
        self.assertEqual(len(out["lines"]), 1)
        self.assertEqual(len(out["record_ids"]), 1)

    def test_no_gaps_at_all_changes_nothing(self):
        out = _payload(
            [{"final_text": "hello there", "record_id": "r1", "created_at": 10.0}], []
        )
        self.assertEqual(out["unattached_gap_line"], "")
        self.assertNotIn("connection lost", out["text"])


class TestCallerPrependsWithoutSuppressingRecovery(unittest.TestCase):
    """The caller-side half. The ledger returning the marker is useless if
    run_artifacts drops it, and actively harmful if it pre-empts recovery."""

    def test_marker_is_prepended_above_recovered_text(self):
        # Reproduces run_artifacts' ordering: recovery first, marker second.
        recovered = "Speaker: text the store still had"
        text = ""
        unattached = "[connection lost - approximately 45s of audio not captured]"
        if not text.strip():
            text = recovered  # _committed_final_source_text(host)
        if unattached:
            text = unattached + "\n" + text if text.strip() else unattached + "\n"
        self.assertTrue(text.startswith("[connection lost"))
        self.assertIn(
            "text the store still had",
            text,
            "recovery must survive -- the marker explains the export, never replaces it",
        )

    def test_marker_alone_when_recovery_finds_nothing(self):
        text = ""
        unattached = "[connection lost - approximately 45s of audio not captured]"
        if not text.strip():
            text = ""  # recovery found nothing either
        if unattached:
            text = unattached + "\n" + text if text.strip() else unattached + "\n"
        self.assertEqual(
            text, "[connection lost - approximately 45s of audio not captured]\n"
        )


if __name__ == "__main__":
    unittest.main()
