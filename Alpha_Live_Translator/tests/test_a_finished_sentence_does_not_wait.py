"""A finished Japanese sentence the speaker stopped on commits at once.

WHAT WAS SLOW (PENDING_TASKS.md section 0e, the owner's meetings of 2026-09-28)
------------------------------------------------------------------------------
The continuity assembler commits a buffer at once when it ends on 。/？ and the
text before the punctuation does not "look incomplete". But with the
punctuation stripped, almost every sentence looks incomplete for want of a
boundary (`looks_incomplete_japanese_fragment` -> `no_sentence_boundary`), so
that branch rarely fired and a finished sentence waited out the whole sentence
hold -- ~3 s under `JAPANESE_ACCURACY_MODE` -- before anyone saw it.

Measured on the three meetings: 85 lines ended on 。/？ with the last final
marked `speech_final` (the speaker stopped) and the missing boundary as the
only doubt; they waited p50 3.0 s, and 3 of them (3.5%) were extended during
the wait. Committing them at once costs those 3 a line split -- never a word --
and saves ~3 s on the other 82.

WHAT THESE TESTS PIN
--------------------
Through the real assembler `ingest`:
* 。/？ + speech_final + only the missing boundary -> committed now
* the same sentence without speech_final -> still held (the speaker may go on)
* a real incomplete ending (…ので。) -> still held, even with speech_final
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription import japanese_boundary_stabilizer as jbs  # noqa: E402
from alpha.transcription import japanese_sentence_assembler as jsa  # noqa: E402
from alpha.transcription.canonical_identity_registry import reset_for_session  # noqa: E402
from alpha.transcription.utterance_lifecycle import reset_utterance_lifecycle  # noqa: E402
from alpha.utils import language_pipeline_worker as lpw  # noqa: E402

SESSION = "sess-section0e"


class _Host:
    _live_session_id = SESSION
    _listen_language = "ja"
    _is_finalizing = False
    _is_stopping = False
    is_listening = True

    def __init__(self):
        self.published = []

    def _publish_final_transcript_segment(
        self, speaker, text, metadata=None, queue_item=None, commit_reason=None
    ):
        self.published.append(text)
        return True


class _Worker:
    def __getattr__(self, name):
        return lambda *a, **k: None


class AFinishedSentenceTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(lpw, "get_language_pipeline_worker", lambda: _Worker())
        p.start()
        self.addCleanup(p.stop)
        ctl.reset_for_run("run-section0e")
        reset_for_session(SESSION)
        jbs.reset_boundary_stabilizer()
        self.addCleanup(jbs.reset_boundary_stabilizer)
        self.host = _Host()
        reset_utterance_lifecycle(self.host, SESSION)
        self.asm = jsa.JapaneseContinuityAssembler(self.host)
        self.asm.reset()
        self.n = 0

    def final(self, text, *, speech_final):
        self.n += 1
        self.asm.ingest(
            1,
            text,
            {"speech_final": speech_final, "source_raw_event_ids": [f"raw-{self.n}"]},
            "deepgram_final",
        )

    def test_speech_final_commits_at_once(self):
        self.final("今日はありがとうございました。", speech_final=True)
        self.assertEqual(self.host.published, ["今日はありがとうございました。"])

    def test_without_speech_final_it_still_waits(self):
        self.final("今日はありがとうございました。", speech_final=False)
        self.assertEqual(self.host.published, [], "the speaker may go on: keep the hold")

    def test_a_real_incomplete_ending_still_waits(self):
        self.final("資料を送りますので。", speech_final=True)
        self.assertEqual(self.host.published, [], "…ので。 usually continues")


if __name__ == "__main__":
    unittest.main()
