"""Regression tests for BUG_FIX_ROADMAP.md Batch 4, item 20c.

Confirmed defect (§5's 2026-08-09 note, from item 13's investigation):
decide_transcript_action's `if curr_n in prev_n: return ("skip", None)`
counted `current` as "nothing new" whenever it was a literal substring
ANYWHERE inside `previous`, including the middle -- the same any-position
containment anti-pattern items 10/11/12/19 already fixed elsewhere.

Confirmed severity by reading the caller directly
(duplicate_protection.py::_display_transcript_item):

    action, result_text = decide_transcript_action(previous_text, text)
    ...
    if action == "skip" or not result_text:
        self._transcript_stability_counters.skipped += 1
        return

There is no fallback path on "skip" -- the incoming final is dropped
outright: never written to TranscriptStore, never displayed, never
submitted for translation. This is real content loss, not a missed-merge
class defect like item 12.

This defect became MORE reachable for the Japanese assembler path
specifically once item 20b (`2367285`) stopped `revision_target_id` from
being self-referentially truthy on every commit. Before that fix,
_display_transcript_item's has_authoritative_revision_signal check forced
action to "update" for nearly every Japanese assembler commit regardless
of what decide_transcript_action returned, masking this function's actual
output. Two tests below exercise this exact interaction, through the
real _display_transcript_item, not just the pure function.

Fix: narrowed to prefix-or-suffix of previous, the only shapes that
actually evidence current is a truncated partial repeat (e.g. a provider
re-send) rather than a coincidental substring match.
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


class TestDecideTranscriptActionCoincidentalMiddleNoLongerDrops(unittest.TestCase):
    def test_coincidental_middle_substring_is_not_skip(self):
        previous = "本当にありがとうございました、助かりました"
        current = "ありがとうございました"
        self.assertIn(current, previous)
        self.assertFalse(previous.startswith(current))
        self.assertFalse(previous.endswith(current))

        action, result = decide_transcript_action(previous, current)

        self.assertNotEqual(
            action,
            "skip",
            "a short remark that coincidentally appears mid-line in the "
            "previous utterance must not be dropped",
        )
        self.assertEqual(action, "add")
        self.assertEqual(result, current)

    def test_english_coincidental_middle_substring_is_not_skip(self):
        previous = "so anyway I think the budget number was around three"
        current = "budget number"
        action, result = decide_transcript_action(previous, current)
        self.assertNotEqual(action, "skip")


class TestDecideTranscriptActionGenuineContainmentStillDrops(unittest.TestCase):
    def test_current_is_a_prefix_of_previous_still_skip(self):
        previous = "こんにちは、元気ですか、今日はいい天気ですね"
        current = "こんにちは、元気ですか"
        self.assertTrue(previous.startswith(current))
        self.assertEqual(decide_transcript_action(previous, current), ("skip", None))

    def test_current_is_a_suffix_of_previous_still_skip(self):
        previous = "えーと、それでですね、ありがとうございました"
        current = "ありがとうございました"
        self.assertTrue(previous.endswith(current))
        self.assertEqual(decide_transcript_action(previous, current), ("skip", None))

    def test_exact_duplicate_still_skip(self):
        text = "そうですね"
        self.assertEqual(decide_transcript_action(text, text), ("skip", None))

    def test_current_extends_previous_still_update(self):
        self.assertEqual(
            decide_transcript_action("hello", "hello world"),
            ("update", "hello world"),
        )

    def test_no_previous_still_add(self):
        self.assertEqual(decide_transcript_action(None, "first line"), ("add", "first line"))

    def test_empty_current_still_skip(self):
        self.assertEqual(decide_transcript_action("anything", "   "), ("skip", None))


class _Counters:
    def __init__(self):
        self.skipped = 0
        self.added = 0
        self.updated = 0


class _Host:
    """Real _display_transcript_item host shaped like a Japanese assembler
    commit (canonical_utterance_id + _jp_continuity_assembler set, matching
    item 20b's now-fixed path) so `already_committed` resolves True and the
    call reaches decide_transcript_action's own "skip"/"add" outcome
    directly, without the separate execute_pipeline_commit re-verification
    branch (module-level ledger state, exercised by other tests in this
    file's neighbors, not this item's concern) getting in the way.
    """

    _ensure_stability_state = DuplicateProtectionMixin._ensure_stability_state
    _render_transcript_from_store = DuplicateProtectionMixin._render_transcript_from_store

    def __init__(self, previous_text, previous_id="jp-utt-previous"):
        self.transcript_store = TranscriptStore()
        if previous_text:
            self.transcript_store.add_segment(
                speaker=1, text=previous_text, canonical_utterance_id=previous_id
            )
        self._transcript_stability_counters = _Counters()
        self._live_session_id = "sess-20c"
        self.initial_verse_box = None
        self.applied = []

    def _apply_transcript_to_store(self, *args, **kwargs):
        self.applied.append((args, kwargs))


def _run_as_japanese_assembler_commit(host, current_text, *, assembler=True):
    item = {
        "is_final": True,
        "speaker": 1,
        "text": current_text,
        "canonical_utterance_id": "jp-utt-current",
        "session_id": "sess-20c",
    }
    if assembler:
        item["_jp_continuity_assembler"] = True
    else:
        # A line the window decides itself; the record id makes it a
        # committed claim, as the assembler flag does above.
        item["canonical_record_id"] = "canon-000099"
    with patch(
        "alpha.transcription.canonical_identity_registry.resolve_canonical_record_id",
        return_value="canon-000099",
    ):
        DuplicateProtectionMixin._display_transcript_item(host, item)


class TestRealDisplayTranscriptItemDropsOnSkip(unittest.TestCase):
    """Confirms the caller-side severity claim directly, not by reading code."""

    def test_coincidental_middle_substring_reaches_the_store_via_display_item(self):
        previous = "so anyway I think the budget number was around three"
        current = "budget number"
        host = _Host(previous)

        _run_as_japanese_assembler_commit(host, current)

        self.assertTrue(
            host.applied,
            "a coincidentally-contained short remark must still reach the "
            "store instead of being silently dropped",
        )
        self.assertEqual(host._transcript_stability_counters.skipped, 0)

    def test_genuine_prefix_containment_is_still_dropped_with_zero_trace(self):
        # Control: the narrowing must not turn genuine containment into a
        # spurious commit either -- for a line the window decides itself.
        # Until item 71 this test sent an assembler line and expected it
        # dropped; see the next test for why that expectation was wrong.
        previous = "so anyway I think the budget"
        current = "so anyway I think"
        host = _Host(previous)

        _run_as_japanese_assembler_commit(host, current, assembler=False)

        self.assertFalse(host.applied)
        self.assertEqual(host._transcript_stability_counters.skipped, 1)

    def test_an_assembler_line_the_previous_row_starts_with_reaches_the_store(self):
        # Item 71 (2026-10-02) retracts this file's earlier expectation that an
        # assembler line under a NEW id, which the previous row starts or ends
        # with, is a provider re-send to drop. By the time an assembler line
        # reaches the window, the boundary stabilizer's re-send guard (item 55:
        # a repeat within 1 s) has passed it and the ledger holds it as its
        # own line; dropping it here hid real speech -- 「なのかな。」 and
        # 「イニッシュ。」, said again 1.5 s and 2.6 s later in the owner's
        # meeting -- and left its translation without a row.
        previous = "so anyway I think the budget"
        current = "so anyway I think"
        host = _Host(previous)

        _run_as_japanese_assembler_commit(host, current)

        self.assertTrue(host.applied)
        self.assertEqual(host._transcript_stability_counters.skipped, 0)


if __name__ == "__main__":
    unittest.main()
