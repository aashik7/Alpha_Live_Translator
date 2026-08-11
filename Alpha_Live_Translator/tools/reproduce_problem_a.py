#!/usr/bin/env python
"""Deterministic headless reproduction of problem A — item 41 Phase 5 fixture.

`CLIENT_DELIVERY_SPRINT_v5.md` item 41. Full proof: `PROBLEM_A_ROOT_CAUSE.md`.

WHAT THIS PROVES
----------------
A Japanese sentence that has already been committed to the canonical ledger
is **destroyed** by the next commit when that next commit is textually
unrelated to it, because the assembler proposes `revise_previous` — reusing
the previous `canonical_utterance_id` — on the strength of a flag it computed
*before* the revision-decision engine ran, and never recomputed afterwards.

The engine itself says `append` every single time (measured: 105 of 111
decisions across the whole recorded corpus carry
`decision_reason: "speaker_boundary_forced_new_line"`, and not one carries a
`revise_previous` verdict). Its verdict is then overwritten by the proposal.

WHY THIS FILE IS NOT UNDER tests/
---------------------------------
It is designed to **FAIL while the bug exists**, so it must not join the
auto-discovered suite and move the 410 / 5F + 2E + 2S baseline. Item 42 uses
it for sprint §4 step 5 ("prove the test catches the bug"): it must fail now,
for the proven reason, and pass once the fix lands.

    Exit 0 = both sentences survived  -> problem A is FIXED
    Exit 1 = the first sentence was destroyed -> problem A is PRESENT

CONTROL CASE
------------
The run is done twice with exactly **one** variable changed, so the result
cannot be blamed on the harness: with `boundary_should_revise` absent the
same two sentences produce two independent ledger records and both survive;
with it set to True they collapse to one record and the first sentence is
gone. `boundary_should_revise` is not a synthetic switch — it is one of the
four real inputs to `update_previous_requested`, and the boundary stabilizer
sets it in production (`BOUNDARY_OUTPUT_REVISE_PREVIOUS_LINE` fired 12 times
across the recorded corpus).

ENTRY POINT
-----------
`_publish_sentence` is driven directly rather than through
`_route_stable_publish`, because the layers above it merge a short follow-up
into the held buffer first, which makes the revise non-destructive (the new
text then *contains* the old). That merge is not always available in a live
session — when the two commits come from different buffer generations the
texts stay disjoint, which is the recorded production case. Driving
`_publish_sentence` isolates the defect without simulating it: every function
below this call is the real production implementation, including the real
canonical ledger.

USAGE
-----
    cd Alpha_Live_Translator
    "<repo_root>/.venv/Scripts/python.exe" tools/reproduce_problem_a.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TOOLS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Two textually disjoint Japanese sentences, both taken from real recorded
# runs. SENTENCE_A is the shape of a completed prior commit; SENTENCE_B is the
# shape of the next one. Neither contains the other, which is the whole point:
# a revise whose new text contains the old is harmless, and three of the
# recorded revises were exactly that and lost nothing.
SENTENCE_A = "日本から持ってきたんですか。持ってきたんですって。"
SENTENCE_B = "ですよ。違いますねでやっぱりこっちにいると日本の行事を味わうことが難しいのです。"


class _FixtureHost:
    """Minimal host. Only the UI edge is captured; the commit path is real."""

    def __init__(self) -> None:
        self._live_session_id = "sess-problem-a-fixture"
        self._listen_language = "ja"
        self._is_finalizing = False
        self._is_stopping = False
        self.is_listening = True

    def _publish_final_transcript_segment(
        self,
        speaker: Any,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
        queue_item: Optional[dict[str, Any]] = None,
        commit_reason: Optional[str] = None,
    ) -> bool:
        return True


def _commit_two_sentences(*, request_revise: bool) -> list[dict[str, Any]]:
    """Commit SENTENCE_A then SENTENCE_B and return the live ledger records."""
    from alpha.transcription import canonical_transcript_ledger as ctl
    from alpha.transcription.canonical_identity_registry import reset_for_session
    from alpha.transcription.japanese_sentence_assembler import (
        get_japanese_continuity_assembler,
    )
    from alpha.transcription.utterance_lifecycle import reset_utterance_lifecycle

    ctl.reset_for_run("problem-a-fixture")
    reset_for_session("sess-problem-a-fixture")
    host = _FixtureHost()
    reset_utterance_lifecycle(host, session_id="sess-problem-a-fixture")
    assembler = get_japanese_continuity_assembler(host)

    # Patched so the fixture never appends to a real run folder's evidence.
    with patch(
        "alpha.utils.transcript_evidence.log_stable_commit",
        side_effect=lambda **kwargs: "fixture-stable-commit",
    ):
        assembler._publish_sentence(
            1, SENTENCE_A, {"source_raw_event_ids": ["raw-fixture-1"]}, "fixture_first"
        )
        metadata: dict[str, Any] = {"source_raw_event_ids": ["raw-fixture-2"]}
        if request_revise:
            # The single independent variable. One of the four real inputs to
            # update_previous_requested; set by the boundary stabilizer live.
            metadata["boundary_should_revise"] = True
        assembler._publish_sentence(1, SENTENCE_B, metadata, "fixture_second")

    return list(ctl.get_active_records())


def _describe(records: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [str(r.get("final_text") or "") for r in records]
    return {
        "record_count": len(records),
        "a_survived": any(SENTENCE_A[:10] in t for t in texts),
        "b_survived": any(SENTENCE_B[:10] in t for t in texts),
        "actions": [str(r.get("applied_action") or "") for r in records],
        "utterance_ids": [
            str((r.get("metadata") or {}).get("canonical_utterance_id") or "")
            for r in records
        ],
        "texts": texts,
    }


def main() -> int:
    control = _describe(_commit_two_sentences(request_revise=False))
    subject = _describe(_commit_two_sentences(request_revise=True))

    for name, res in (("CONTROL  (no revise flag)", control), ("SUBJECT  (revise flag)", subject)):
        print(f"\n{name}")
        print(f"  ledger records : {res['record_count']}")
        print(f"  applied_action : {res['actions']}")
        print(f"  distinct ids   : {len({i for i in res['utterance_ids'] if i})}")
        print(f"  sentence A kept: {res['a_survived']}")
        print(f"  sentence B kept: {res['b_survived']}")
        for t in res["texts"]:
            print(f"    -> {t[:60]}")

    print("\n" + "=" * 68)
    if not control["a_survived"] or not control["b_survived"]:
        print("FIXTURE INVALID: the control case lost text too. The harness, not")
        print("the bug, is at fault -- do not draw conclusions from the subject.")
        return 2

    if subject["a_survived"] and subject["b_survived"]:
        print("PASS - both sentences survived. Problem A is FIXED.")
        return 0

    print("FAIL - problem A is PRESENT.")
    print(f"  The control kept {control['record_count']} records and lost nothing.")
    print("  Setting boundary_should_revise -- and changing nothing else --")
    print(f"  collapsed that to {subject['record_count']} record(s) and destroyed:")
    print(f"    {SENTENCE_A}")
    print("  The assembler proposed revise_previous from a flag computed before")
    print("  the revision-decision engine ran; the engine's own verdict was")
    print("  'append'. The ledger revise then overwrote final_text in place.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
