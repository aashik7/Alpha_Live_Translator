"""Regression tests for problem A — `CLIENT_DELIVERY_SPRINT_v5.md` item 42.

Proof of the defect: `PROBLEM_A_ROOT_CAUSE.md` (repo root, item 41).

The defect: `_publish_sentence` computed `update_previous_requested` from
boundary-stabilizer signals **before** `decide_stable_revision_action` ran and
never recomputed it. When the authority returned `append`, that branch cleared
the flag's four *inputs* but not the variable, so the id-mint gate still reused
the previous `canonical_utterance_id` — and a ledger revise replaces
`final_text` in place. 10 real Japanese sentences were destroyed that way
across the recorded corpus.

**Two properties have to hold at once, and the pair of them is the contract:**

* `DestructiveRevisePreventedTest` — a textually disjoint follow-up must never
  destroy a committed sentence.
* `GenuineRevisionStillWorksTest` — an actual extension must still revise in
  place. This is the half a naive fix breaks: pointing the gate at
  `final_revision_action` alone makes it *always* `commit_new` (the authority
  is fail-closed to `append` because `previous_record` carries no `speaker`
  key), which converts every genuine revision into a near-duplicate second
  line — trading the data loss for visible duplication.

`tools/reproduce_problem_a.py` is the standalone fixture for the same defect;
it lives outside `tests/` because it is designed to fail while the bug exists.
"""

import sys
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    reset_for_session,
)
from alpha.transcription.japanese_sentence_assembler import (  # noqa: E402
    _revision_is_non_destructive,
    get_japanese_continuity_assembler,
)
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    reset_utterance_lifecycle,
)


class _Host:
    """Only the UI edge is stubbed; the whole commit path stays real."""

    def __init__(self) -> None:
        self._live_session_id = "sess-problem-a-regression"
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


def _commit_pair(first: str, second: str) -> list[str]:
    """Commit two sentences with a revise requested on the second.

    `boundary_should_revise` is one of the four real inputs to
    `update_previous_requested`; the boundary stabilizer sets it in production
    (`BOUNDARY_OUTPUT_REVISE_PREVIOUS_LINE` fired 12x across the corpus).
    """
    ctl.reset_for_run("problem-a-regression")
    reset_for_session("sess-problem-a-regression")
    host = _Host()
    reset_utterance_lifecycle(host, session_id="sess-problem-a-regression")
    assembler = get_japanese_continuity_assembler(host)
    with patch(
        "alpha.utils.transcript_evidence.log_stable_commit",
        side_effect=lambda **kwargs: "regression-stable-commit",
    ):
        assembler._publish_sentence(
            1, first, {"source_raw_event_ids": ["raw-1"]}, "regression_first"
        )
        assembler._publish_sentence(
            1,
            second,
            {"source_raw_event_ids": ["raw-2"], "boundary_should_revise": True},
            "regression_second",
        )
    return [str(r.get("final_text") or "") for r in ctl.get_active_records()]


# Real disjoint pair, shaped after the recorded loss on run ...20260807-160529.
DISJOINT_FIRST = "日本から持ってきたんですか。持ってきたんですって。"
DISJOINT_SECOND = "ですよ。違いますねでやっぱりこっちにいると日本の行事を味わうことが難しいのです。"

# Real extension pair, shaped after one of the 3 harmless revises in the corpus.
EXTENSION_FIRST = "日本だと忘れてしまうんですけど、外国で日本の普通の食べ物を食べると"
EXTENSION_SECOND = "日本だと忘れてしまうんですけど、外国で日本の普通の食べ物を食べるとおいしく感じます。"


class DestructiveRevisePreventedTest(unittest.TestCase):
    def test_disjoint_follow_up_does_not_destroy_the_committed_sentence(self):
        texts = _commit_pair(DISJOINT_FIRST, DISJOINT_SECOND)
        self.assertTrue(
            any(DISJOINT_FIRST[:10] in t for t in texts),
            f"the first sentence was destroyed by the follow-up: {texts!r}",
        )
        self.assertTrue(
            any(DISJOINT_SECOND[:10] in t for t in texts),
            f"the second sentence never landed: {texts!r}",
        )

    def test_disjoint_follow_up_gets_its_own_ledger_record(self):
        texts = _commit_pair(DISJOINT_FIRST, DISJOINT_SECOND)
        self.assertEqual(
            2, len(texts), f"expected two independent records, got: {texts!r}"
        )


class GenuineRevisionStillWorksTest(unittest.TestCase):
    """The half a naive fix breaks. Without this, item 42 trades problem A's
    data loss for duplication."""

    def test_extension_revises_in_place_instead_of_appending_a_near_copy(self):
        texts = _commit_pair(EXTENSION_FIRST, EXTENSION_SECOND)
        self.assertEqual(
            1,
            len(texts),
            f"a genuine extension appended a near-duplicate line: {texts!r}",
        )
        self.assertTrue(
            any(EXTENSION_SECOND[:12] in t for t in texts),
            f"the extended text did not survive: {texts!r}",
        )


class RevisionSafetyPredicateTest(unittest.TestCase):
    """`_revision_is_non_destructive` is the discriminator item 41 measured as
    exceptionless across the corpus."""

    def test_extension_is_safe(self):
        self.assertTrue(_revision_is_non_destructive("あいうえお", "あいうえおかきくけこ"))

    def test_identical_text_is_safe(self):
        self.assertTrue(_revision_is_non_destructive("あいうえお", "あいうえお"))

    def test_disjoint_text_is_not_safe(self):
        self.assertFalse(_revision_is_non_destructive("あいうえお", "かきくけこ"))

    def test_truncation_is_not_safe(self):
        """Losing the tail is still losing words."""
        self.assertFalse(_revision_is_non_destructive("あいうえおかきくけこ", "あいうえお"))

    def test_whitespace_only_difference_is_safe(self):
        """Cleanup passes re-space text; that must not read as a rewrite."""
        self.assertTrue(_revision_is_non_destructive("あい うえお", "あいうえお です"))

    def test_empty_previous_is_safe_to_overwrite(self):
        self.assertTrue(_revision_is_non_destructive("", "あいうえお"))

    def test_empty_candidate_is_never_safe(self):
        self.assertFalse(_revision_is_non_destructive("あいうえお", ""))

    def test_sentence_final_punctuation_is_not_ignored(self):
        """`。` and `、` carry boundary meaning -- a continuing form is not the
        same sentence as a completed one, so this must not pass as extension."""
        self.assertFalse(_revision_is_non_destructive("食べると。", "食べると、おいしい。"))


if __name__ == "__main__":
    unittest.main()
