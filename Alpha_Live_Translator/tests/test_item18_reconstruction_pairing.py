"""Item 18: the Stable reconstruction paired two differently-sized streams by index.

Found by the live run `v3.3.5.5.8.5.26.5.16-20260914-095713`, which ended at
`completed_pending_evidence_package` with:

    THREE_STAGE_FINALIZER_EXCEPTION  step=assembler_stage
    PersistedEvidenceReconstructionError: Unresolved revision targets:
        ['missing_record_id_event_index_27', 'missing_record_id_event_index_28']

`reconstruct_active_stable_records` did:

    commit = commit_by_index[i] if i < len(commit_by_index) else {}
    meta   = commit.get("assembler_metadata") ...
    commit_rid = meta.get("revision_target_id") or meta.get("canonical_record_id")

`i` indexes the *assembler events*; `commit_by_index` is the *stable commits*.
Those are different lengths and different memberships -- `no_op` and
`suppress_candidate` events never become commits, and they are skipped AFTER
this pairing is computed. That measured run had 30 events (4 of them
`suppress_candidate`) against 26 usable commits, so:

* every event after the first `suppress_candidate` paired with the WRONG commit,
  silently; and
* the trailing events indexed past the end, got `{}`, and were reported as
  having no record id at all.

Both were false. Every one of those 26 events carries its own record id, in its
own `commit_reason`: `"|canonical_record_id=canon-000022|transaction_id=..."`.
Zero of them carry it as a top-level field, which is why reading the *commit*
was load-bearing in the first place.

The consequence is diagnostics, not transcript: that run's `Alpha_output_FINAL.txt`
was written and its 23 canonical records are intact, but validation, the health
timeline, the memory trend, the artifacts index and the upload package were all
skipped when the finalizer raised. A client sending a support bundle would send
an incomplete one.

These fixtures rebuild that shape synthetically rather than pointing at the run
folder: `troubleshooting/runs/` is gitignored and retention-pruned, and a test
whose guarantee disappears with the evidence is a pattern this project has
already been bitten by.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _event(index, action, text, record_id="", *, raw=None, target=""):
    """An assembler event shaped like the ones production writes.

    Note where the record id lives: inside `commit_reason`, never top level.
    A suppressed tail carries an EMPTY `canonical_record_id=`, exactly as the
    real stream does.
    """
    return {
        "stable_stage_event_id": f"asm-{index:06d}",
        "timestamp": 1789347600.0 + index,
        "speaker": 2,
        "assembler_text": text,
        "applied_action": action,
        "action": action,
        "commit_reason": f"|canonical_record_id={record_id}|transaction_id=txn-{index:012d}",
        "source_raw_event_ids": list(raw or [f"raw-{index:06d}"]),
        "revision_decision": {"target_line_id": target} if target else {},
    }


def _commit(record_id, text):
    return {
        "stable_commit_id": f"sc-{record_id}",
        "stable_text": text,
        "timestamp": 1789347600.0,
        "assembler_metadata": {"canonical_record_id": record_id},
        "source_raw_event_ids": [],
    }


def _run_folder(events, commits):
    root = Path(tempfile.mkdtemp())
    (root / "accuracy_stage_compare").mkdir(parents=True, exist_ok=True)
    (root / "transcripts").mkdir(parents=True, exist_ok=True)
    (root / "accuracy_stage_compare" / "stable_assembler_events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )
    (root / "transcripts" / "stable_commits.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in commits) + "\n",
        encoding="utf-8",
    )
    return root


def _reconstruct(folder):
    from alpha.utils.persisted_run_evidence import reconstruct_active_stable_records

    return reconstruct_active_stable_records(folder)


class TheMeasuredRunShapeReconstructs(unittest.TestCase):
    """A suppress_candidate mid-stream, and events past the commit count."""

    def setUp(self):
        # 4 appends, one suppressed tail in the middle, 2 trailing appends.
        # 7 events, 6 real commits -- the real run's shape, scaled down.
        self.events = [
            _event(1, "append", "いち", "canon-000001"),
            _event(2, "append", "に", "canon-000002"),
            _event(3, "suppress_candidate", "きれた", ""),
            _event(4, "append", "さん", "canon-000003"),
            _event(5, "append", "よん", "canon-000004"),
            _event(6, "append", "ご", "canon-000005"),
            _event(7, "append", "ろく", "canon-000006"),
        ]
        self.commits = [
            _commit(f"canon-{n:06d}", t)
            for n, t in ((1, "いち"), (2, "に"), (3, "さん"), (4, "よん"), (5, "ご"), (6, "ろく"))
        ]
        self.result = _reconstruct(_run_folder(self.events, self.commits))

    def test_nothing_is_unresolved(self):
        self.assertEqual(
            self.result["unresolved_revision_targets"],
            [],
            "events still fail to resolve their own record id",
        )

    def test_the_reconstruction_completes(self):
        self.assertTrue(self.result["reconstruction_completed"])

    def test_every_append_produced_a_record(self):
        self.assertEqual(self.result["append_count"], 6)
        self.assertEqual(len(self.result["active_records"]), 6)

    def test_the_suppressed_tail_is_counted_not_committed(self):
        self.assertEqual(self.result["suppress_candidate_count"], 1)

    def test_each_record_keeps_its_own_text(self):
        """The mispairing's silent half: right count, wrong content."""
        got = {r["record_id"]: r["text"] for r in self.result["active_records"]}
        self.assertEqual(got.get("canon-000003"), "さん")
        self.assertEqual(got.get("canon-000006"), "ろく")


class EventsBeyondTheCommitCountStillResolve(unittest.TestCase):
    """The visible half: trailing events indexed past the end and got {}."""

    def test_trailing_events_resolve_from_their_own_commit_reason(self):
        events = [_event(i, "append", f"t{i}", f"canon-{i:06d}") for i in range(1, 6)]
        commits = [_commit("canon-000001", "t1")]  # deliberately far too few
        result = _reconstruct(_run_folder(events, commits))
        self.assertEqual(result["unresolved_revision_targets"], [])
        self.assertEqual(len(result["active_records"]), 5)


class ARealMissingRecordIdIsStillReported(unittest.TestCase):
    """The fix must not paper over a genuine gap."""

    def test_an_append_with_no_record_id_anywhere_is_flagged(self):
        events = [
            _event(1, "append", "ok", "canon-000001"),
            _event(2, "append", "no id at all", ""),
        ]
        result = _reconstruct(_run_folder(events, [_commit("canon-000001", "ok")]))
        self.assertTrue(
            result["unresolved_revision_targets"],
            "a genuinely id-less append was silently accepted",
        )
        self.assertFalse(result["reconstruction_completed"])

    def test_english_stable_events_keep_their_synthetic_ids(self):
        events = [_event(1, "append", "english line", "")]
        events[0]["reason"] = "english_accepted_final"
        result = _reconstruct(_run_folder(events, []))
        self.assertEqual(result["unresolved_revision_targets"], [])
        self.assertEqual(len(result["active_records"]), 1)


class ReviseStillResolvesToItsTarget(unittest.TestCase):
    def test_a_revise_updates_the_named_record(self):
        events = [
            _event(1, "append", "first", "canon-000001"),
            _event(2, "suppress_candidate", "noise", ""),
            _event(3, "revise", "first, corrected", "canon-000001", target="canon-000001"),
        ]
        commits = [_commit("canon-000001", "first")]
        result = _reconstruct(_run_folder(events, commits))
        self.assertEqual(result["unresolved_revision_targets"], [])
        self.assertEqual(result["revise_count"], 1)
        texts = {r["record_id"]: r["text"] for r in result["active_records"]}
        self.assertEqual(texts["canon-000001"], "first, corrected")

    def test_a_revise_whose_target_never_existed_still_fails_closed(self):
        """It raises rather than returning, and that is the correct contract.

        A revise with no surviving target leaves the reconstruction empty, and
        the guard at `:337-340` refuses to write empty Stable artifacts over
        non-empty persisted events. Pinned so this fix cannot quietly downgrade
        it into a silent empty result.
        """
        from alpha.utils.persisted_run_evidence import (
            PersistedEvidenceReconstructionError,
        )

        events = [_event(1, "revise", "orphan", "canon-999999", target="canon-999999")]
        with self.assertRaises(PersistedEvidenceReconstructionError):
            _reconstruct(_run_folder(events, []))


if __name__ == "__main__":
    unittest.main()
