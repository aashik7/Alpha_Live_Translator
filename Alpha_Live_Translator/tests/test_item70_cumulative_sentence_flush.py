"""Item 70: the detection half, and guards against the widening that failed.

WHAT SHIPPED
------------
Only the detection half, plus a refactor of the flush that is
behaviour-neutral on the shape it still accepts.

`duplicate_protection._display_transcript_item` already logged
`COMMITTED_SEGMENT_DROPPED_AS_INTERIM` for item 65's 8-of-9 loss. That copy
was UNREACHABLE for the case it was written for: `AlphaApp._display_transcript_item`
is the entry point every UI batch goes through, and its first gate

    if item.get("is_final") is False:
        return

fired first, with no log and no counter. The existing regression test could
not see it because it binds `DuplicateProtectionMixin._display_transcript_item`
onto a bare host, so it never executes the app method at all -- a green test
over a dead production log. The test below drives the REAL app method.

WHAT DID NOT SHIP, AND WHY THESE GUARDS EXIST
---------------------------------------------
Item 70 proposed widening the flush from a pure append to the cumulative
re-send shape as well (`prev in curr`). Adversarial review before shipping
reproduced three findings against the real lifecycle:

1. DUPLICATION. After a flush, the next cumulative window carries the
   committed sentence back in, and the only defence -- item 66's
   `_strip_committed_tail_prefix(min_run=3)` -- fails closed below 3
   comparison tokens. `"Hello there."` was committed, then committed AGAIN
   inside `"Hello there. How are you doing?"`. The threshold is exact: 1-2
   token sentences duplicate, 3+ do not. Short acknowledgements ("Yes.",
   "Okay.", "Thank you.") are the most common sentence type in meeting
   speech, so this is not a corner case.

2. CONTENT LOSS. The flush tail reaches `_trim_resent_tail_locked` through
   `force_new=True`. That trim is written for a window the provider RE-SENT;
   the flush tail provably is not one -- the flush has already established
   `prev + " " + tail == merged` -- so an ordinary anadiplosis
   ("...the bottom line. The bottom line is simple.") had "The bottom line"
   deleted outright, present in no record.

3. IT DID NOT FIX THE SYMPTOM. On a replay of run `...20260814-114309` the
   longest record was unchanged at 2084 characters and records over 400 went
   59 -> 60. Averages improved only because the denominator grew.

The reason (3) is structural, and it is what a future attempt needs: **68% of
Deepgram's finals do not end on a terminator**, so the sentence boundary
usually sits in the MIDDLE of the accumulated text while this gate tests
`prev[-1]`. Cutting the long lines needs a split at an INTERIOR terminator,
which is a different change.

The two failing sequences are pinned below so a re-widening cannot
reintroduce them silently.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    _merge_lexical,
    reset_utterance_lifecycle,
)


class _Host:
    _listen_language = "en"

    def __init__(self):
        self.published: list[str] = []

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        self.published.append(text)
        return True


def _drive(chunks, tag, *, cumulative_timing=False):
    host = _Host()
    owner = reset_utterance_lifecycle(host, session_id=f"item70-{tag}")
    for i, chunk in enumerate(chunks):
        owner.on_final_chunk(
            text=chunk,
            speaker=1,
            channel=0,
            # A cumulative window keeps re-sending from the same start, which
            # is what makes the chunks timing-compatible with the active
            # utterance rather than a new one.
            start=0.0 if cumulative_timing else i * 1.0,
            end=(i + 1) * 1.2 if cumulative_timing else i * 1.0 + 0.9,
            is_final=True,
            speech_final=False,
            event_id=f"e{i}",
            metadata={"channel_index": 0},
            deepgram_request_id=f"r{i}",
        )
    active = owner._active
    return host.published, (active.text if active else "")


PURE_APPEND = [
    "First sentence is complete.",
    "Second sentence starts here.",
    "And a third one.",
]

# The two sequences that broke the withdrawn widening.
SHORT_ACK_CUMULATIVE = [
    "Hello there.",
    "Hello there. How are you",
    "Hello there. How are you doing?",
    "Hello there. How are you doing? I am fine.",
    "Hello there. How are you doing? I am fine. Thanks for asking.",
]
ANADIPLOSIS_CUMULATIVE = [
    "And that is the bottom line.",
    "And that is the bottom line. The bottom line is simple.",
]


class TestTheWidenedShapeStaysRefused(unittest.TestCase):
    """Both sequences below were reproduced losing/duplicating text under the
    widened predicate. They are pinned on the CONTENT, not on the flush count,
    so a future widening that genuinely handles them is free to pass."""

    def test_a_short_sentence_is_never_committed_twice(self):
        published, active = _drive(SHORT_ACK_CUMULATIVE, "ack", cumulative_timing=True)
        whole = " ".join(published + [active])
        self.assertEqual(
            whole.count("Hello there."),
            1,
            "a 2-token sentence re-entered the active utterance and was "
            f"committed twice: {published + [active]!r}",
        )

    def test_anadiplosis_does_not_lose_the_repeated_phrase(self):
        published, active = _drive(
            ANADIPLOSIS_CUMULATIVE, "anad", cumulative_timing=True
        )
        whole = " ".join(published + [active])
        self.assertIn(
            "The bottom line is simple",
            whole,
            "the re-send trim deleted the tail's head; it is in no record: "
            f"{published + [active]!r}",
        )

    def test_no_word_is_lost_in_either_sequence(self):
        for seq, tag in ((SHORT_ACK_CUMULATIVE, "w1"), (ANADIPLOSIS_CUMULATIVE, "w2")):
            published, active = _drive(seq, tag, cumulative_timing=True)
            whole = " ".join(published + [active])
            for word in seq[-1].replace(".", " ").replace("?", " ").split():
                self.assertIn(word, whole, f"{word!r} lost from {seq[-1]!r}")


class TestPureAppendBehaviourIsUnchanged(unittest.TestCase):
    def test_pure_append_still_flushes_each_boundary(self):
        published, active = _drive(PURE_APPEND, "pa")
        self.assertEqual(
            published,
            ["First sentence is complete.", "Second sentence starts here."],
        )
        self.assertEqual(active, "And a third one.")

    def test_no_terminator_never_flushes(self):
        published, active = _drive(["No terminator here", "and it continues"], "nt")
        self.assertEqual(published, [])
        self.assertIn("continues", active)

    def test_a_single_chunk_never_flushes(self):
        published, _ = _drive(["One."], "one")
        self.assertEqual(published, [])

    def test_a_revision_is_never_split_at_a_withdrawn_terminator(self):
        """Also covered by test_english_line_quality, pinned here for the WHY.

            prev   'I am very happy.'
            curr   'I am very happy today.'    <- the sentence was REVISED
            merged 'I am very happy. today.'   <- _merge_lexical kept the stale
                                                  period (a separate,
                                                  pre-existing defect)

        `merged` starts with `prev`, so any startswith-based eligibility test
        splits at a terminator the provider had already withdrawn.
        """
        published, _ = _drive(["I am very happy.", "I am very happy today."], "rev")
        self.assertEqual(published, [])
        merged = _merge_lexical("I am very happy.", "I am very happy today.")
        self.assertTrue(
            merged.startswith("I am very happy."),
            "if this stops holding, the test no longer covers the trap",
        )

    def test_the_flush_is_a_byte_exact_split_never_a_rewrite(self):
        prev, curr = "First sentence is complete.", "Second sentence starts here."
        merged = _merge_lexical(prev, curr)
        tail = merged[len(prev):].strip()
        self.assertEqual(
            f"{prev} {tail}",
            merged,
            "flush must reconstruct merged exactly, or it is rewriting text",
        )

    def test_flag_off_disables_the_flush_entirely(self):
        with patch("alpha.constants.ENGLISH_SENTENCE_FLUSH_ENABLED", False):
            published, _ = _drive(PURE_APPEND, "off")
        self.assertEqual(published, [], "the kill switch must still work")


class TestSilentDropGateIsNowLogged(unittest.TestCase):
    """The detection half -- drives the REAL AlphaApp method, not the mixin.

    Binding the mixin (as the older test does) skips the app-level gate
    entirely, which is exactly why this hole survived.
    """

    def _run_app_gate(self, item):
        from alpha.ui.main_window import AlphaApp

        logged = []

        class Host:
            _display_transcript_item = AlphaApp._display_transcript_item

            def _diag_transcript_item_fields(self, it):
                return it.get("speaker"), it.get("text") or "", ""

            def _diag_store_segment_count(self):
                return 0

            is_listening = True

        with patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log",
            side_effect=lambda event, **kw: logged.append((event, kw)),
        ):
            Host()._display_transcript_item(item)
        return logged

    def test_a_committed_segment_dropped_as_interim_is_logged_by_the_app_gate(self):
        logged = self._run_app_gate(
            {
                "is_final": False,
                "speaker": 1,
                "text": "a committed sentence",
                "lifecycle_commit_reason": "sentence_boundary_flush",
                "canonical_utterance_id": "u-1",
            }
        )
        events = [e for e, _ in logged]
        self.assertIn(
            "COMMITTED_SEGMENT_DROPPED_AS_INTERIM",
            events,
            "the gate that actually runs in production must not be silent",
        )
        payload = dict(logged[events.index("COMMITTED_SEGMENT_DROPPED_AS_INTERIM")][1])
        self.assertEqual(payload.get("gate"), "main_window._display_transcript_item")
        self.assertEqual(payload.get("commit_reason"), "sentence_boundary_flush")

    def test_a_genuine_interim_stays_quiet(self):
        logged = self._run_app_gate(
            {"is_final": False, "speaker": 1, "text": "half a sen"}
        )
        self.assertNotIn(
            "COMMITTED_SEGMENT_DROPPED_AS_INTERIM", [e for e, _ in logged]
        )


if __name__ == "__main__":
    unittest.main()
