"""Regression tests for v4 roadmap items 24 and 26.

Both are leftovers from `BUG_FIX_ROADMAP.md` that survived into the v5 sprint,
audited 2026-08-12 and judged worth fixing before delivery.

* **24** — `revise_last_transcript_snapshot` accepted a `speaker` argument but
  never compared it, so it revised whatever the literal last row was. v4's own
  note: *"strictly weaker than `transcript_store`'s equivalent, which at least
  filters by speaker."* Third sibling of items 22/23.
* **26** — the canonical ledger overwrote `final_text` in place with no
  comparison against `source_version`. Item 42 fixed that class of loss at the
  *caller*; this is the authority defending itself, so a different caller
  cannot repeat problem A.

Item 26's guard is deliberately about **ordering**, not content. Item 42 owns
"is this revision non-destructive?"; putting that rule here too would be two
authorities on one decision (sprint §0 rule 2).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.utils import transcript_snapshot_store as snap  # noqa: E402


class SnapshotStoreSpeakerGuardTest(unittest.TestCase):
    """v4 item 24."""

    def setUp(self) -> None:
        snap.reset_transcript_snapshot_store()

    def _active(self):
        return [s for s in snap.get_snapshot_copy() if s.get("status") == "active"]

    def test_a_different_speaker_does_not_overwrite_the_last_line(self):
        snap.append_transcript_snapshot(speaker=1, stable_text="speaker one line")
        snap.revise_last_transcript_snapshot(speaker=2, stable_text="speaker two line")
        active = self._active()
        self.assertEqual(2, len(active), f"one speaker overwrote another: {active!r}")
        self.assertTrue(any("one line" in s["stable_text"] for s in active))
        self.assertTrue(any("two line" in s["stable_text"] for s in active))

    def test_the_same_speaker_still_revises_in_place(self):
        """The counterweight -- over-tightening would append duplicates."""
        snap.append_transcript_snapshot(speaker=1, stable_text="partial text")
        snap.revise_last_transcript_snapshot(speaker=1, stable_text="partial text extended")
        active = self._active()
        self.assertEqual(1, len(active), f"a genuine revision was appended: {active!r}")
        self.assertIn("extended", active[0]["stable_text"])

    def test_an_unknown_speaker_never_confirms_a_match(self):
        """Fail-closed, matching `transcript_store` and item 22."""
        snap.append_transcript_snapshot(speaker=0, stable_text="unidentified one")
        snap.revise_last_transcript_snapshot(speaker=0, stable_text="unidentified two")
        self.assertEqual(2, len(self._active()))

    def test_no_speaker_supplied_keeps_the_old_behaviour(self):
        """Callers that pass nothing must not start being refused."""
        snap.append_transcript_snapshot(speaker=1, stable_text="original")
        snap.revise_last_transcript_snapshot(stable_text="revised")
        self.assertEqual(1, len(self._active()))


class LedgerStaleVersionGuardTest(unittest.TestCase):
    """v4 item 26."""

    def setUp(self) -> None:
        ctl.reset_for_run("v4-item-26")

    def _append(self, text: str, version: int) -> str:
        result = ctl.apply_decision(
            speaker=1,
            assembler_text=text,
            final_text=text,
            requested_action="append",
            applied_action="append",
            metadata={"canonical_utterance_id": "u-1", "source_version": version},
            source_raw_event_ids=["raw-1"],
            transaction_id="txn-append",
        )
        return str(result.get("record_id") or "")

    def _revise(self, record_id: str, text: str, version: int) -> dict:
        return ctl.apply_decision(
            speaker=1,
            assembler_text=text,
            final_text=text,
            requested_action="revise",
            applied_action="revise",
            revision_target_id=record_id,
            metadata={"canonical_utterance_id": "u-1", "source_version": version},
            source_raw_event_ids=["raw-2"],
            transaction_id="txn-revise",
        )

    def test_a_newer_revision_is_accepted(self):
        record_id = self._append("first version", 1)
        self.assertTrue(record_id)
        result = self._revise(record_id, "first version, now longer", 2)
        self.assertTrue(result.get("ok"), f"a legitimate revision was refused: {result!r}")
        texts = [str(r.get("final_text") or "") for r in ctl.get_active_records()]
        self.assertTrue(any("now longer" in t for t in texts))

    def test_a_stale_revision_is_refused_and_the_text_survives(self):
        record_id = self._append("the good text", 5)
        result = self._revise(record_id, "an out of order overwrite", 2)
        self.assertFalse(result.get("ok"), "a stale write was allowed to overwrite")
        self.assertEqual("stale_source_version", result.get("reason"))
        texts = [str(r.get("final_text") or "") for r in ctl.get_active_records()]
        self.assertTrue(
            any("the good text" in t for t in texts),
            f"the newer text was destroyed by an older write: {texts!r}",
        )

    def test_an_equal_version_is_still_allowed(self):
        """Only strictly-older writes are stale; equal versions are ordinary
        idempotent retries and must not start failing."""
        record_id = self._append("same version text", 3)
        result = self._revise(record_id, "same version revised", 3)
        self.assertTrue(result.get("ok"), f"an equal-version revise was refused: {result!r}")

    def test_a_missing_version_does_not_start_rejecting_writes(self):
        """Callers that never supplied a version must be unaffected."""
        record_id = self._append("no version text", 1)
        result = ctl.apply_decision(
            speaker=1,
            assembler_text="revised without a version",
            final_text="revised without a version",
            requested_action="revise",
            applied_action="revise",
            revision_target_id=record_id,
            metadata={"canonical_utterance_id": "u-1"},
            source_raw_event_ids=["raw-3"],
            transaction_id="txn-noversion",
        )
        self.assertTrue(result.get("ok"), f"a version-less caller was refused: {result!r}")


if __name__ == "__main__":
    unittest.main()
