"""Regression tests for BUG_FIX_ROADMAP.md Batch 3, item 13.

Two separate changes are pinned here.

1. **The substitution gap.** A substitution-style correction
   ("...three million" -> "...four million") is neither a containment nor
   an extension of the previous text, so decide_transcript_action falls
   through to "add" and the transcript keeps BOTH the wrong line and the
   corrected one. The only thing that previously fixed that was the
   authoritative `lifecycle_decision` signal, which is absent on some
   paths.

   The fix is deliberately **identity-only, never text similarity**:
   converting "add" to "update" REPLACES the stored line, so a wrong
   guess destroys committed speech -- the opposite and worse failure
   direction from items 10/11/12/19, which were about not dropping
   content. Two genuinely distinct utterances can be textually
   near-identical ("the first quarter was strong" / "the second quarter
   was strong"), and no threshold on the text alone separates that from a
   real correction. Matching `canonical_utterance_id` does.

   This became possible only because Batch 3 item 18 (`0aa6a8f`) added
   `canonical_utterance_id` to TranscriptSegment -- the field whose
   absence the code comment at this call site explicitly cited as the
   reason it had to fall back to a weaker check.

2. **Two dead branches removed** from decide_transcript_action. Both were
   provably unreachable AND returned the same value as the check that
   subsumed them, so removal is a no-op. These tests pin the whole
   decision table so that stays true.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.transcription.duplicate_protection import (  # noqa: E402
    DuplicateProtectionMixin,
    decide_transcript_action,
)


class TestDecideTranscriptActionTableUnchanged(unittest.TestCase):
    """The two removed branches were redundant -- prove the table is intact."""

    def test_substitution_still_returns_add(self):
        # decide_transcript_action itself is unchanged: it has only two
        # strings and cannot safely call this a revision. The identity
        # check lives in the caller.
        self.assertEqual(
            decide_transcript_action(
                "the budget was three million", "the budget was four million"
            ),
            ("add", "the budget was four million"),
        )

    def test_current_extends_previous_is_update(self):
        self.assertEqual(
            decide_transcript_action("hello", "hello world"), ("update", "hello world")
        )

    def test_current_contained_in_previous_is_skip(self):
        # This branch is what subsumed the removed prev_n.startswith(curr_n).
        self.assertEqual(decide_transcript_action("hello world", "hello"), ("skip", None))

    def test_exact_duplicate_is_skip(self):
        self.assertEqual(decide_transcript_action("same text", "same text"), ("skip", None))

    def test_no_previous_is_add(self):
        self.assertEqual(decide_transcript_action(None, "first line"), ("add", "first line"))

    def test_empty_current_is_skip(self):
        self.assertEqual(decide_transcript_action("anything", "   "), ("skip", None))


class _Counters:
    def __init__(self):
        self.skipped = 0
        self.added = 0
        self.updated = 0


class _Host:
    """Minimal host exercising only the decision block under test."""

    _ensure_stability_state = DuplicateProtectionMixin._ensure_stability_state

    def __init__(self, previous_text, previous_utterance_id):
        self.transcript_store = TranscriptStore()
        if previous_text:
            self.transcript_store.add_segment(
                speaker=1,
                text=previous_text,
                canonical_utterance_id=previous_utterance_id,
            )
        self._transcript_stability_counters = _Counters()
        self._live_session_id = "sess-1"
        self.applied = []

    # Stop the real commit machinery after the decision we care about.
    def _apply_transcript_to_store(self, *args, **kwargs):
        self.applied.append((args, kwargs))
        raise _StopAfterDecision()


class _StopAfterDecision(Exception):
    pass


def _decide(previous_text, previous_id, current_text, current_id):
    """Run _display_transcript_item far enough to capture the decision.

    Returns (initial_decision, applied_calls, upgraded) where `upgraded`
    is whether the item-13 same-utterance upgrade fired. The upgrade emits
    SAME_UTTERANCE_SUBSTITUTION_UPDATED, which is asserted directly --
    unlike `applied`, that signal does not depend on how far the rest of
    the commit pipeline gets with this reduced test host (the no-id case,
    for one, returns earlier for reasons unrelated to this fix).
    """
    host = _Host(previous_text, previous_id)
    item = {
        "is_final": True,
        "speaker": 1,
        "text": current_text,
        "canonical_utterance_id": current_id,
        "session_id": "sess-1",
    }
    captured = {}
    events = []

    real_decide = decide_transcript_action

    def _spy(prev, curr):
        result = real_decide(prev, curr)
        captured["initial"] = result
        return result

    with patch(
        "alpha.transcription.duplicate_protection.decide_transcript_action",
        side_effect=_spy,
    ), patch(
        "alpha.transcription.canonical_identity_registry.resolve_canonical_record_id",
        return_value="canon-000001",
    ), patch(
        "alpha.utils.japanese_accuracy_log.jp_accuracy_log",
        side_effect=lambda event, **kw: events.append(event),
    ):
        try:
            DuplicateProtectionMixin._display_transcript_item(host, item)
        except _StopAfterDecision:
            pass
        except Exception:
            # Any later-stage failure is fine; the decision under test has
            # already been made and recorded by this point.
            pass

    upgraded = "SAME_UTTERANCE_SUBSTITUTION_UPDATED" in events
    return captured.get("initial"), host.applied, upgraded


class TestSameUtteranceSubstitutionBecomesUpdate(unittest.TestCase):
    def test_matching_utterance_id_turns_add_into_update(self):
        initial, applied, upgraded = _decide(
            "the budget was three million",
            "U-7",
            "the budget was four million",
            "U-7",
        )

        self.assertEqual(
            initial,
            ("add", "the budget was four million"),
            "decide_transcript_action alone must still say add",
        )
        self.assertTrue(
            upgraded,
            "a proven same-utterance substitution must replace, not duplicate",
        )
        # Intentionally not asserting that _apply_transcript_to_store was
        # reached: whether the canonical commit stage lets the item through
        # depends on module-level ledger/registry state that other tests in
        # the suite mutate, so that assertion passed alone and failed in the
        # full run. `upgraded` is the signal this fix actually owns.
        if applied:
            self.assertEqual(applied[0][1].get("action"), "update")

    def test_different_utterance_id_stays_add(self):
        # The dangerous case: two distinct utterances that look similar.
        # Without identity proof this must remain "add" -- a visible
        # duplicate is recoverable, an overwrite is not.
        initial, applied, upgraded = _decide(
            "the first quarter was strong",
            "U-7",
            "the second quarter was strong",
            "U-8",
        )

        self.assertEqual(initial[0], "add")
        self.assertFalse(upgraded, "no identity proof means no overwrite")
        if applied:
            self.assertEqual(applied[0][1].get("action"), "add")

    def test_missing_utterance_id_stays_add(self):
        initial, applied, upgraded = _decide(
            "the budget was three million",
            "",
            "the budget was four million",
            "",
        )

        self.assertEqual(initial[0], "add")
        self.assertFalse(upgraded, "an absent id is not identity proof")


if __name__ == "__main__":
    unittest.main()
