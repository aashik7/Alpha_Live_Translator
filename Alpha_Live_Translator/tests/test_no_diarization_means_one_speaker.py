"""Without diarization the app does not invent speaker changes.

WHAT WAS BROKEN (the owner's meeting of 2026-09-29, found reviewing it)
----------------------------------------------------------------------
Japanese (profile `no_diarize`) and English (`ENGLISH_DIARIZATION_ENABLED =
False`) ask Deepgram for no diarization, so no word carries a speaker and
every speaker came from `_fallback_speaker_detection`: it rotated the speaker
1 -> 2 -> 3 -> 4 whenever 4 s passed without a Results message. The Japanese
assembler treats a speaker change as a hard boundary, so a pause split a
sentence and committed half of it: in that meeting 45 speaker flips, 20 of them
mid-sentence -- 「メインで」 / 「はこれです。」 translated "As the main one," /
"This is my go-to.", 「MRさんはデザインドキュメント」 / 「今どれぐらい」.

Replayed through the real app on the meeting's own recorded finals, the same
code with one speaker: export 96 -> 68 lines, lines committed more than 10 s
after their last words 13 -> 7, and the lineage report passed.

WHAT THESE TESTS PIN
--------------------
* no speaker on any word -> speaker 1, whatever the gap
* a response that does carry speakers still splits by them
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import speaker_detection as sd  # noqa: E402


class _Host(sd.SpeakerDetectionMixin):
    pass


def _results(text, speaker=None):
    word = {"word": text, "punctuated_word": text, "start": 0.0, "end": 1.0}
    if speaker is not None:
        word["speaker"] = speaker
    return {"channel": {"alternatives": [{"transcript": text, "words": [word]}]}}


class NoDiarizationTest(unittest.TestCase):
    def test_a_long_pause_is_not_a_new_speaker(self):
        host = _Host()
        clock = [1000.0]
        with mock.patch("time.time", lambda: clock[0]):
            first = host.extract_speaker_from_nova3(_results("メインで"))
            clock[0] += 10.0  # 10 s with no Results at all
            second = host.extract_speaker_from_nova3(_results("はこれです。"))
            clock[0] += 30.0
            third = host.extract_speaker_from_nova3(_results("今どれぐらい、"))
        self.assertEqual(
            [s["speaker"] for s in first + second + third], [1, 1, 1],
            "a pause was turned into a speaker change",
        )

    def test_a_response_with_no_words_is_speaker_one_too(self):
        host = _Host()
        data = {"channel": {"alternatives": [{"transcript": "はい。", "words": []}]}}
        clock = [1000.0]
        with mock.patch("time.time", lambda: clock[0]):
            host.extract_speaker_from_nova3(data)
            clock[0] += 10.0
            self.assertEqual(host.extract_speaker_from_nova3(data)[0]["speaker"], 1)


class RealDiarizationTest(unittest.TestCase):
    def test_words_with_speakers_still_split(self):
        host = _Host()
        data = {"channel": {"alternatives": [{"transcript": "a b", "words": [
            {"word": "a", "punctuated_word": "A", "speaker": 0},
            {"word": "b", "punctuated_word": "B", "speaker": 1},
        ]}]}}
        self.assertEqual(
            [(s["speaker"], s["text"]) for s in host.extract_speaker_from_nova3(data)],
            [(1, "A"), (2, "B")],
        )


if __name__ == "__main__":
    unittest.main()
