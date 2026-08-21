"""A final the provider is not confident about must not become transcript.

WHY THIS EXISTS
---------------
`LANGUAGE_CONFIDENCE_REJECT` has been in `constants.py` since the language work
and **nothing consumed it**. `main_window.py` only LOGGED the three thresholds
(`SAFE`, `UNSTABLE`, `REJECT`) into a diagnostics blob; a grep for the names
returned no other reference. That is the fourth time this project shipped a rule
with no caller — after item 44's `commit_in_flight`, item 65's gated log, and
items 46+47's whole module — and this one meant provider output at confidence
**0.384** was committed to the canonical transcript as if it were speech.

THE THRESHOLD IS MEASURED, NOT CHOSEN
--------------------------------------
Across the three genuine Japanese runs of 2026-08-20/21 — 344 finals — the
minimum provider confidence was **0.500** and **not one** fell below 0.45:

    run ...235820   n=236   min 0.971   below 0.45: 0
    run ...150452   n= 87   min 0.542   below 0.45: 0
    run ...151230   n= 21   min 0.500   below 0.45: 0

On the run where non-Japanese was spoken into a Japanese session, 10% fell
below it, including the invented `義姉。` at 0.384. So the gate drops nothing
real and does remove the worst of the invention.

WHAT THIS DOES NOT DO — DO NOT MISREAD IT
------------------------------------------
It does **not** fix wrong-language transcription. In that same run
`バングラデーション`, a phonetic guess at the English word "Bangladesh", arrived
at confidence **0.997**. A confidence gate cannot catch a confidently wrong
language; only separating the two audio sources into their own recognisers can.
That is item 85, and it stays open.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import LANGUAGE_CONFIDENCE_REJECT  # noqa: E402
from alpha.transcription import japanese_final_chunk_stabilizer as jfcs  # noqa: E402


class _Host:
    """Minimal host; the stabilizer only reads listening/stopping flags."""

    is_listening = True
    _is_stopping = False


class TheGateHasARealThreshold(unittest.TestCase):
    def test_the_reject_threshold_is_the_measured_one(self):
        self.assertEqual(LANGUAGE_CONFIDENCE_REJECT, 0.45)

    def test_the_stabilizer_actually_imports_it(self):
        """The defect was that nothing did."""
        source = (
            PROJECT_ROOT
            / "alpha"
            / "transcription"
            / "japanese_final_chunk_stabilizer.py"
        ).read_text(encoding="utf-8", errors="replace")
        self.assertIn("LANGUAGE_CONFIDENCE_REJECT", source)
        self.assertIn("LOW_CONFIDENCE_FINAL_REJECTED", source)


class LowConfidenceFinalsAreDropped(unittest.TestCase):
    """Drives the real `JapaneseFinalChunkStabilizer.ingest`."""

    def setUp(self):
        self.forwarded = []
        self.logged = []

        # `active()` gates the whole method on Japanese mode; force it on so the
        # confidence rule is what decides, not the environment.
        self._real_active = jfcs.JapaneseFinalChunkStabilizer.active
        jfcs.JapaneseFinalChunkStabilizer.active = lambda _self: True

        self._real_log = jfcs.jp_accuracy_log
        jfcs.jp_accuracy_log = lambda event, **kw: self.logged.append((event, kw))

        self.stabilizer = jfcs.JapaneseFinalChunkStabilizer(_Host())
        self.stabilizer.set_accepting(True)

    def tearDown(self):
        jfcs.JapaneseFinalChunkStabilizer.active = self._real_active
        jfcs.jp_accuracy_log = self._real_log

    def _ingest(self, text, confidence):
        meta = {"confidence": confidence} if confidence is not None else {}
        return self.stabilizer.ingest(1, text, meta)

    def _rejections(self):
        return [kw for name, kw in self.logged if name == "LOW_CONFIDENCE_FINAL_REJECTED"]

    def test_the_0_384_garbage_from_the_live_run_is_rejected(self):
        """`義姉。` — invented from non-Japanese speech."""
        self._ingest("義姉。", 0.384)
        rejected = self._rejections()
        self.assertEqual(len(rejected), 1, "the low-confidence final was not rejected")
        self.assertAlmostEqual(rejected[0]["confidence"], 0.384, places=3)

    def test_a_final_exactly_at_the_threshold_is_kept(self):
        """0.45 is a REJECT-below bound, not reject-at."""
        self._ingest("これは残る。", LANGUAGE_CONFIDENCE_REJECT)
        self.assertEqual(self._rejections(), [])

    def test_the_lowest_confidence_seen_in_a_real_japanese_run_is_kept(self):
        """0.500 was the minimum across 344 genuine finals. Dropping it would
        be exactly the content loss this project ranks worse than duplication."""
        self._ingest("こんにちは。", 0.500)
        self.assertEqual(self._rejections(), [])

    def test_the_0_553_konnichiwa_from_the_same_run_is_kept(self):
        """Real speech, low confidence. It must survive."""
        self._ingest("こんにちは。", 0.553)
        self.assertEqual(self._rejections(), [])

    def test_high_confidence_is_untouched(self):
        self._ingest("普通の文です。", 0.997)
        self.assertEqual(self._rejections(), [])

    def test_a_final_with_no_confidence_is_kept(self):
        """Deepgram omits it on some frames; absence is not low confidence."""
        self._ingest("信頼度なし。", None)
        self.assertEqual(self._rejections(), [])

    def test_transcript_confidence_is_preferred_when_both_are_present(self):
        self.stabilizer.ingest(
            1, "テスト。", {"transcript_confidence": 0.30, "confidence": 0.99}
        )
        self.assertEqual(len(self._rejections()), 1)

    def test_a_rejected_final_reports_the_threshold_it_failed(self):
        self._ingest("義姉。", 0.384)
        self.assertAlmostEqual(
            self._rejections()[0]["threshold"], LANGUAGE_CONFIDENCE_REJECT, places=3
        )


class TheGateDoesNotClaimToFixWrongLanguage(unittest.TestCase):
    def test_a_confident_wrong_language_guess_still_passes(self):
        """`バングラデーション` arrived at 0.997 in the live run. This is the
        limit of a confidence gate, recorded so nobody mistakes one for the
        other: only item 85's per-source recognisers can catch it."""
        logged = []
        real_active = jfcs.JapaneseFinalChunkStabilizer.active
        real_log = jfcs.jp_accuracy_log
        jfcs.JapaneseFinalChunkStabilizer.active = lambda _self: True
        jfcs.jp_accuracy_log = lambda event, **kw: logged.append((event, kw))
        try:
            s = jfcs.JapaneseFinalChunkStabilizer(_Host())
            s.set_accepting(True)
            s.ingest(1, "バングラデーションの写真です。", {"confidence": 0.997})
        finally:
            jfcs.JapaneseFinalChunkStabilizer.active = real_active
            jfcs.jp_accuracy_log = real_log
        self.assertEqual(
            [kw for name, kw in logged if name == "LOW_CONFIDENCE_FINAL_REJECTED"],
            [],
            "a confidence gate must not be mistaken for language separation",
        )


if __name__ == "__main__":
    unittest.main()
