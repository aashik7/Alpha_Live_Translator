"""A line item 66 trimmed stays trimmed in every later version of it.

WHAT WAS BROKEN (PENDING_TASKS.md open defect k)
------------------------------------------------
Item 66 removes the head of a new line when the record before it already ends
with those words (a commit landed mid-sentence and the provider re-sent the
span). Item 35 lets a later guess at the same audio re-open that line, and item
37 rightly refuses to trim a new version against the record it replaces. But
nothing trimmed it against the record item 66 had trimmed it against -- the
lifecycle keeps only the last committed record -- so the cut words came back:

    "in Duterte, he writes openly, I never considered"   [100.0-104.0]  UtteranceEnd
    "he writes openly, I never considered him an impostor"  [102.0-105.5]  UtteranceEnd
        -> committed as "him an impostor" (item 66)
    "he writes openly, I never considered him an impostor at all"  [102.0-106.0]  UtteranceEnd
        -> the export repeats "he writes openly, I never considered"

Driving the real code found the same repeat on more paths -- a second re-open,
growing interims while re-opened, the provider's final arriving as a
correction, a cumulative final after the inactivity timeout, an extend whose
merge took the whole re-send, and a line whose trim fired on a merge rather
than at creation -- and one worse: an older, SHORTER guess at the trimmed
line's audio did not read as one
(it was compared with the trimmed text only), re-opened the line, and replaced
"him an impostor" with "he writes openly, I never considered him", losing
"an impostor" from the export.

WHAT THESE TESTS PIN
--------------------
Through the real lifecycle, `_publish_final_transcript_segment`,
`_display_transcript_item`, identity registry and ledger (the host from
`test_a_reopened_line_keeps_its_words.py`):

* every later version of a trimmed line -- re-opened once or twice, grown while
  open, corrected by the final, re-sent after the timeout, extended -- stays
  trimmed, as one revised record, with the pane agreeing; also when the trim
  fired on a merge
* an older, shorter guess changes nothing
* a revision whose head no longer matches is left whole (the trim only ever
  removes words proven to be in the record before)
"""

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_a_reopened_line_keeps_its_words import _Case  # noqa: E402

from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402

FIRST = "in Duterte, he writes openly, I never considered"
REPEAT = "he writes openly, I never considered"


class _Trimmed(_Case):
    def setUp(self):
        super().setUp()
        self.interim(FIRST, 100.0, 104.0)
        self.utterance_end()
        self.interim(f"{REPEAT} him an impostor", 102.0, 105.5)

    def commit_trimmed_line(self):
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), [FIRST, "him an impostor"], "fixture: item 66's trim")

    def assert_export(self, second_line):
        ledger = self.ledger_texts()
        self.assertEqual(ledger, [FIRST, second_line])
        self.assertEqual(
            sum(line.count(REPEAT) for line in ledger), 1, "the cut half-sentence came back"
        )
        self.assertEqual(self.store_texts(), ledger, "the pane and the export disagree")


class AReopenedTrimmedLineStaysTrimmedTest(_Trimmed):
    def test_the_owner_shape(self):
        self.commit_trimmed_line()
        self.interim(f"{REPEAT} him an impostor at all", 102.0, 106.0)
        self.utterance_end()
        self.assert_export("him an impostor at all")
        self.assertEqual(ctl.get_action_counts().get("revise"), 1)

    def test_reopened_twice(self):
        self.commit_trimmed_line()
        self.interim(f"{REPEAT} him an impostor at all", 102.0, 106.0)
        self.utterance_end()
        self.interim(f"{REPEAT} him an impostor at all, really", 102.0, 106.5)
        self.utterance_end()
        self.assert_export("him an impostor at all, really")

    def test_growing_while_reopened(self):
        self.commit_trimmed_line()
        self.interim(f"{REPEAT} him an", 102.0, 105.2)
        self.interim(f"{REPEAT} him an impostor at all", 102.0, 106.0)
        self.interim(f"{REPEAT} him an impostor at all today", 102.0, 106.4)
        self.utterance_end()
        self.assert_export("him an impostor at all today")

    def test_an_older_shorter_guess_changes_nothing(self):
        self.commit_trimmed_line()
        self.interim(f"{REPEAT} him", 102.0, 105.0)
        self.utterance_end()
        self.assert_export("him an impostor")
        self.assertEqual(self.life.stats()["committed_segments_reopened"], 0)


class AnyLaterVersionStaysTrimmedTest(_Trimmed):
    def test_the_final_as_a_correction(self):
        self.commit_trimmed_line()
        self.final(f"{REPEAT} him an impostor.", 102.0, 105.5, speech_final=True)
        self.assert_export("him an impostor.")

    def test_a_cumulative_final_after_the_timeout(self):
        self.timeout()
        self.assertEqual(self.ledger_texts(), [FIRST, "him an impostor"], "fixture")
        self.final(f"{REPEAT} him an impostor at all.", 102.0, 106.0, speech_final=True)
        self.assert_export("him an impostor at all.")

    def test_an_extend_that_re_sends_the_head(self):
        """A changed word makes it no correction ("imposter"), so the timeout's
        early commit is EXTENDED -- and the merge takes the whole re-send."""
        self.timeout()
        self.final(f"{REPEAT} him an imposter at all.", 102.0, 106.0, speech_final=True)
        self.assert_export("him an imposter at all.")


class ATrimOnAMergeIsKeptTooTest(_Case):
    def test_the_trim_fires_on_the_second_interim(self):
        """Item 66's second half: the first interim was too short to trim, the
        cumulative one after it was trimmed on the merge."""
        self.interim(FIRST, 100.0, 104.0)
        self.utterance_end()
        self.interim("he writes", 102.0, 102.5)
        self.interim(f"{REPEAT} him an impostor", 102.0, 105.5)
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), [FIRST, "him an impostor"], "fixture")
        self.interim(f"{REPEAT} him an impostor at all", 102.0, 106.0)
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), [FIRST, "him an impostor at all"])
        self.assertEqual(self.store_texts(), self.ledger_texts())


class OnlyProvenWordsAreCutTest(_Trimmed):
    def test_a_revised_head_is_left_whole(self):
        """No longer a proven repeat of the line before, so nothing is cut --
        a possible repeat, never a loss."""
        self.commit_trimmed_line()
        revised = "he wrote openly and never considered him an impostor at all"
        self.interim(revised, 102.0, 106.0)
        self.utterance_end()
        self.assertEqual(self.ledger_texts(), [FIRST, revised])


if __name__ == "__main__":
    unittest.main()
