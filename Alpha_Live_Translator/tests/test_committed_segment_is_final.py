"""Regression test for item 65-flush's root cause.

Live run `...20260812-142447`: 9 committed utterances, 10 queue publishes,
`PIPELINE_COMMIT_TRANSACTION_STARTED: 1`. 8 of 9 utterances -- 4218 characters
of real speech -- were published and never exported, with nothing logged.

The whole difference between the survivor and the lost, from that run's own
evidence:

    raw-000001..8  is_final=False   reason='sentence_boundary_flush'
    raw-000009     is_final=None    reason='inactivity_timeout_fallback'

`_display_transcript_item` opens with `if item.get("is_final") is False:
return`. `None` passes that identity check; `False` does not. So the survivor
survived only because its metadata happened to have no `is_final` key at all.

The chain: `_commit_locked` builds its metadata by spreading the TRIGGERING
event's metadata last, so a commit raised while handling a chunk whose metadata
said `is_final: False` inherits that False. `_publish_final_transcript_segment`
then does a blanket `queue_item.update(metadata)`, overwriting the `is_final:
True` it had set moments earlier. The display gate drops it before any
canonical commit, silently.

None of this was specific to the sentence-boundary flush. **Any** commit raised
while handling an `is_final: False` event was discarded the same way -- the
flush merely made it frequent enough to be visible. Fixed at all three layers,
and this file pins each one.
"""

import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha import constants  # noqa: E402
from alpha.transcription import deepgram_client as dc  # noqa: E402
from alpha.transcription import utterance_lifecycle as ul  # noqa: E402
from alpha.transcription.duplicate_protection import (  # noqa: E402
    DuplicateProtectionMixin,
)


class _Counters:
    def __init__(self):
        self.skipped = self.added = self.updated = 0

    def as_dict(self):
        return {"skipped": self.skipped, "added": self.added, "updated": self.updated}


class _Host:
    """Captures what the lifecycle publishes, without the UI or the ledger."""

    def __init__(self):
        self.published = []

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, commit_reason=None
    ):
        self.published.append(dict(metadata or {}))
        return True


class CommitMetadataSaysFinalTest(unittest.TestCase):
    """Layer 1 -- the commit's own metadata."""

    def _commit_with_incoming(self, incoming_metadata):
        life = ul.UtteranceLifecycleOwner()
        life.reset_for_session("sess-is-final")
        host = _Host()
        life.bind_host(host)
        life.on_final_chunk(
            text="A complete sentence here.",
            speaker=1,
            channel=0,
            start=0.0,
            end=2.0,
            is_final=True,
            speech_final=True,
            event_id="e1",
            metadata=incoming_metadata,
        )
        return host.published

    def test_commit_is_final_even_when_the_trigger_said_false(self):
        """The exact shape of the run ...142447 loss."""
        published = self._commit_with_incoming(
            {"is_final": False, "start_time": 0.0, "end_time": 2.0}
        )
        self.assertTrue(published, "nothing was published at all")
        for meta in published:
            self.assertIsNot(
                meta.get("is_final"),
                False,
                "a committed segment still claims is_final=False -- the display "
                "gate will drop it silently before any canonical commit",
            )

    def test_commit_is_final_when_the_trigger_said_nothing(self):
        published = self._commit_with_incoming({"start_time": 0.0, "end_time": 2.0})
        self.assertTrue(published)
        for meta in published:
            self.assertIsNot(meta.get("is_final"), False)

    def test_dispatch_sets_it_explicitly(self):
        src = inspect.getsource(ul.UtteranceLifecycleOwner._dispatch_commit)
        self.assertIn('meta["is_final"] = True', src)


class PublishPathReassertsFinalTest(unittest.TestCase):
    """Layer 2 -- the blanket metadata update that clobbered it."""

    def test_queue_item_is_final_survives_the_metadata_update(self):
        src = inspect.getsource(dc.DeepgramClientMixin._publish_final_transcript_segment)
        update_at = src.index("queue_item.update(metadata)")
        reassert_at = src.find('queue_item["is_final"] = True', update_at)
        self.assertNotEqual(
            reassert_at,
            -1,
            "queue_item.update(metadata) can still overwrite is_final with a "
            "stale False from the triggering event",
        )


class DroppedCommitIsLoggedTest(unittest.TestCase):
    """Layer 3 -- the silence that hid this for three sessions."""

    def test_a_commit_dropped_as_interim_is_logged(self):
        logged = []

        class Host:
            _ensure_stability_state = DuplicateProtectionMixin._ensure_stability_state
            _display_transcript_item = DuplicateProtectionMixin._display_transcript_item

            def __init__(self):
                self._transcript_stability_counters = _Counters()
                self._live_session_id = "sess-log"

        import alpha.utils.japanese_accuracy_log as jal

        original = jal.jp_accuracy_log
        jal.jp_accuracy_log = lambda event, **kw: logged.append(event)
        try:
            Host()._display_transcript_item(
                {
                    "is_final": False,
                    "speaker": 1,
                    "text": "a committed sentence",
                    "lifecycle_commit_reason": "sentence_boundary_flush",
                }
            )
        finally:
            jal.jp_accuracy_log = original
        self.assertIn("COMMITTED_SEGMENT_DROPPED_AS_INTERIM", logged)

    def test_a_genuine_interim_is_still_dropped_quietly(self):
        """Interims must not reach the store, and must not spam the log."""
        logged = []

        class Host:
            _ensure_stability_state = DuplicateProtectionMixin._ensure_stability_state
            _display_transcript_item = DuplicateProtectionMixin._display_transcript_item

            def __init__(self):
                self._transcript_stability_counters = _Counters()
                self._live_session_id = "sess-log"

        import alpha.utils.japanese_accuracy_log as jal

        original = jal.jp_accuracy_log
        jal.jp_accuracy_log = lambda event, **kw: logged.append(event)
        try:
            Host()._display_transcript_item(
                {"is_final": False, "speaker": 1, "text": "partial words"}
            )
        finally:
            jal.jp_accuracy_log = original
        self.assertEqual(logged, [])


class FlushIsEnabledAgainTest(unittest.TestCase):
    def test_flush_is_on(self):
        self.assertTrue(
            constants.ENGLISH_SENTENCE_FLUSH_ENABLED,
            "item 65's flush was re-enabled once its loss was traced to the "
            "is_final clobber; turning it off again needs a new reason",
        )


if __name__ == "__main__":
    unittest.main()
