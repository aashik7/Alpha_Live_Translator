"""Item 7: when the English utterance lifecycle raises, say so in the evidence.

REFUTED as first framed. The claim was that the fall-through at
`deepgram_client.py:1744` "bypasses the single commit authority", because
`_publish_final_transcript_segment` calls none of `observe_identity` /
`execute_pipeline_commit` / `apply_decision`. That reads the architecture inside
out: that function is the shared publish SINK upstream of the authority, and the
healthy lifecycle path reaches it too (`utterance_lifecycle.py:3112` calls the
same publisher). Measurement showed the fail-closed identity gate WORKING -- the
utterance is refused downstream, not smuggled past.

What survives is an observability defect. The handler prints and nothing else,
so the downstream `IDENTITY_REJECTION` has no attributable cause. Its two
Japanese siblings in the same function -- `:1601-1611` and `:1652-1662` -- each
write a structured event naming the fallback. The English one wrote none.

The fix is the event, and ONLY the event. Do not mint a synthetic
`canonical_utterance_id` to let the fall-through commit: that would defeat the
gate the measurement showed working.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SPOKEN = "so everybody agrees on the revised schedule"


def _english_host():
    from alpha.transcription.deepgram_client import DeepgramClientMixin

    class Host(DeepgramClientMixin):
        _live_session_id = "sess-item7"
        _listen_language = "en"
        _is_finalizing = False
        _is_stopping = False
        is_listening = True

        def __init__(self):
            self.published: list[tuple] = []

        def _allow_final_transcript_commit(self, *a, **k):
            return True

        def _publish_final_transcript_segment(
            self, speaker_num, segment_text, metadata=None, **kwargs
        ):
            self.published.append((speaker_num, segment_text, dict(metadata or {})))
            return True

    return Host()


def _drive_with_a_raising_lifecycle(host):
    """Make the English lifecycle block raise, and capture the evidence events."""
    from alpha.transcription import utterance_lifecycle as ul
    from alpha.utils import japanese_accuracy_log as jal

    events: list[tuple] = []
    real_log = jal.jp_accuracy_log

    def _spy(event, *args, **kwargs):
        events.append((str(event), kwargs))
        return real_log(event, *args, **kwargs)

    def _boom(*args, **kwargs):
        raise RuntimeError("lifecycle exploded")

    with patch.object(ul, "should_use_utterance_lifecycle", _boom), patch.object(
        jal, "jp_accuracy_log", _spy
    ):
        returned = host._commit_final_transcript_segment(
            2, SPOKEN, {"channel_index": 0, "speech_final": True}
        )
    return returned, events


class TheEnglishFailureIsNamedLikeItsJapaneseSiblings(unittest.TestCase):
    def setUp(self):
        self.host = _english_host()
        self.returned, self.events = _drive_with_a_raising_lifecycle(self.host)
        self.names = [name for name, _ in self.events]

    def test_the_event_is_emitted(self):
        self.assertIn(
            "ENGLISH_LIFECYCLE_INGEST_FAILED",
            self.names,
            f"the English fall-through is still unattributable; events were {self.names}",
        )

    def test_it_carries_the_exception_and_the_fallback(self):
        payload = next(
            (kw for name, kw in self.events if name == "ENGLISH_LIFECYCLE_INGEST_FAILED"),
            None,
        )
        self.assertIsNotNone(payload, "event not emitted")
        self.assertIn("RuntimeError", str(payload.get("reason", "")))
        self.assertIn("lifecycle exploded", str(payload.get("reason", "")))
        self.assertTrue(
            str(payload.get("fallback", "")),
            "the event does not say what happened instead",
        )

    def test_it_carries_a_text_preview_like_the_japanese_siblings(self):
        payload = next(
            (kw for name, kw in self.events if name == "ENGLISH_LIFECYCLE_INGEST_FAILED"),
            None,
        )
        self.assertIsNotNone(payload)
        self.assertIn(SPOKEN[:20], str(payload.get("text_preview", "")))


class TheBehaviourAroundItIsUnchanged(unittest.TestCase):
    """Pinned: the fix is the event only."""

    def setUp(self):
        self.host = _english_host()
        self.returned, self.events = _drive_with_a_raising_lifecycle(self.host)

    def test_the_final_is_still_published(self):
        """Publishing preserves the spoken text; that part was already right."""
        self.assertEqual(len(self.host.published), 1)
        self.assertEqual(self.host.published[0][1], SPOKEN)

    def test_it_still_reports_success(self):
        self.assertTrue(self.returned)

    def test_no_synthetic_canonical_utterance_id_is_minted(self):
        """Minting one would defeat the gate measurement showed working."""
        _speaker, _text, metadata = self.host.published[0]
        self.assertFalse(
            str(metadata.get("canonical_utterance_id") or ""),
            "a synthetic canonical_utterance_id was invented for the fall-through",
        )


if __name__ == "__main__":
    unittest.main()
