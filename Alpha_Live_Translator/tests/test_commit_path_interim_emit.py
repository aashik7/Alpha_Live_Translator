"""Regression tests: a speech_final commit must not also publish its own
final text through the live interim-preview channel.

Confirmed defect (run v3.3.5.5.8.5.26.5.3-20260807-132429): every
speech_final commit went through `_ingest` Case C, which calls
`_apply_active_update_locked(...)` to fold the last fragment into
`active.text` and then immediately calls `_commit_locked(...)`. That
first call unconditionally ended in `_emit_interim(...)`, so the
utterance's *final* text was queued to the interim-preview channel
microseconds before the same text was committed permanently.

Because the preview and the commit reach the UI on two independent
timers (`_pending_interim` + INTERIM_UI_THROTTLE_MS vs `transcript_queue`
+ TRANSCRIPT_UI_BATCH_FLUSH_MS), the preview could land *after* the
commit had already run its final/interim comparison — repainting the
just-committed sentence as a stale "in progress" line that survived
until the ghost watchdog reaped it ~1.5s later. Observed on 3 of 5
sentences in that run (U-2, U-3, U-5).

These tests pin the fix at its source: the commit path emits a commit
and nothing else, while every genuine in-progress path still emits its
interim exactly as before.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    UtteranceLifecycleOwner,
)


class _Recorder:
    """Captures which channel each lifecycle decision was published on."""

    def __init__(self):
        self.commits = []
        self.interims = []

    def on_commit(self, decision):
        self.commits.append(decision)

    def on_interim(self, decision):
        self.interims.append(decision)


def _make_owner():
    rec = _Recorder()
    owner = UtteranceLifecycleOwner(
        on_commit=rec.on_commit,
        on_interim_update=rec.on_interim,
    )
    owner.reset_for_session("sess-test")
    return owner, rec


class TestCommitPathDoesNotEmitInterim(unittest.TestCase):
    def test_speech_final_commit_emits_no_interim(self):
        # Case C, same-active path: one interim builds the utterance, then a
        # speech_final chunk completes it. Only the interim should reach the
        # preview channel; the completing chunk must commit and nothing more.
        owner, rec = _make_owner()
        owner.on_interim(text="My name is", speaker=1, channel=0, start=0.0, end=1.0)
        self.assertEqual(len(rec.interims), 1, "the in-progress interim must still publish")
        interim_count_before_final = len(rec.interims)

        owner.on_final_chunk(
            text="My name is Tariko.",
            speaker=1,
            channel=0,
            start=0.0,
            end=2.0,
            is_final=True,
            speech_final=True,
        )
        self.assertEqual(len(rec.commits), 1, "the utterance must still commit")
        self.assertEqual(
            len(rec.interims),
            interim_count_before_final,
            "the commit path must not publish the final text as an interim preview",
        )

    def test_speech_final_commit_with_no_prior_interim_emits_no_interim(self):
        # Same path with active is None -- a single speech_final chunk that
        # both creates and commits the utterance.
        owner, rec = _make_owner()
        owner.on_final_chunk(
            text="Hi.",
            speaker=1,
            channel=0,
            start=0.0,
            end=0.5,
            is_final=True,
            speech_final=True,
        )
        self.assertEqual(len(rec.commits), 1)
        self.assertEqual(rec.interims, [])

    def test_new_utterance_commit_path_emits_no_interim(self):
        # Case C incompatible-with-active path: a held final chunk is flushed
        # and a brand-new utterance is created and committed
        # (reason="speech_final_new_utterance"). Neither commit may leak an
        # interim for its own final text.
        owner, rec = _make_owner()
        owner.on_final_chunk(
            text="First part",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=False,
        )
        interims_after_hold = len(rec.interims)
        self.assertGreaterEqual(
            interims_after_hold, 1, "a held final chunk must still preview"
        )

        owner.on_final_chunk(
            text="Totally different second utterance.",
            speaker=2,
            channel=1,
            start=30.0,
            end=32.0,
            is_final=True,
            speech_final=True,
        )
        self.assertGreaterEqual(len(rec.commits), 1)
        self.assertEqual(
            len(rec.interims),
            interims_after_hold,
            "neither the flush-commit nor the new-utterance commit may emit an interim",
        )


class TestInProgressPathsStillEmitInterim(unittest.TestCase):
    """The fix must be surgical -- genuine previews are unaffected."""

    def test_pure_interim_still_emits(self):
        owner, rec = _make_owner()
        owner.on_interim(text="I", speaker=1, channel=0, start=0.0, end=0.3)
        owner.on_interim(text="I am", speaker=1, channel=0, start=0.0, end=0.6)
        self.assertEqual(len(rec.interims), 2)
        self.assertEqual(rec.commits, [])

    def test_held_final_chunk_still_emits(self):
        # is_final=True, speech_final=False stays buffered and must keep
        # showing progress to the user.
        owner, rec = _make_owner()
        owner.on_final_chunk(
            text="Thank you.",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=False,
        )
        self.assertEqual(len(rec.interims), 1)
        self.assertEqual(rec.commits, [])

    def test_interim_text_is_the_fragment_not_the_committed_text(self):
        # Guards the specific symptom: the text that reached the preview
        # channel used to be active.text (the merged final), which is how the
        # already-committed sentence reappeared on screen.
        owner, rec = _make_owner()
        owner.on_interim(text="Thank you.", speaker=1, channel=0, start=0.0, end=1.0)
        owner.on_final_chunk(
            text="Thank you for your time.",
            speaker=1,
            channel=0,
            start=0.0,
            end=2.0,
            is_final=True,
            speech_final=True,
        )
        committed = rec.commits[-1].text
        self.assertNotIn(
            committed,
            [d.text for d in rec.interims],
            "the committed text must never have been published as a preview",
        )


if __name__ == "__main__":
    unittest.main()
