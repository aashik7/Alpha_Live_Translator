"""The window shows every line the Japanese assembler committed, as its own row.

WHAT WAS BROKEN (item 71, the owner's live meeting of 2026-10-02 on 26.5.65)
---------------------------------------------------------------------------
The assembler decides whether a line is new or a revision of the one before,
and writes the ledger (and so the export) accordingly. The window then decided
again, from the text alone, in `DuplicateProtectionMixin._display_transcript_item`:

* a new line CONTAINING the previous row's text replaced that row
  (`decide_transcript_action` -> "update"): 「オッケー。」 was overwritten by
  「これオッケーですか。」, 「ディスクリーン。」 by 「ありきさんディスクリーンね。」;
* a new line the previous row ENDS (or starts) with was skipped:
  「なのかな。」 after 「これは何個はつぐみなのかな。」 and 「イニッシュ。」 after
  「イニッシュしてるか。」 -- said again, 1.5 s and 2.6 s later -- never showed.

Either way the new line's id never reached the store, so its translation found
no row (`TRANSLATION_STORE_ID_MATCH_NOT_FOUND`, 4 in that meeting) and its
English never showed. The export was right.

The previous row is only consulted when the identity registry knows the
line's id, which it always does in production (the assembler registers every
commit). Item 40's test host never registered ids, which is why it passed --
these tests register them as the assembler does.

WHAT THESE TESTS PIN
--------------------
Through the real `_commit_transcript_item_to_store` and
`DuplicateProtectionMixin._display_transcript_item`:

* a new assembler line containing the previous row's text is its own row
* a new assembler line the previous row ends with is its own row
* its translation lands on its row
* a revision (same id, `revision_target_id`) still replaces its row
* a manual-mode line that never passed through the assembler is unchanged
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    assign_canonical_record_id,
    observe_identity,
)
from alpha.ui.main_window import AlphaApp  # noqa: E402
from test_task2g_acceptance_gate import ManualModeCommitHost  # noqa: E402

CHANNEL = [0, 1]


class _Case(unittest.TestCase):
    def setUp(self):
        self.host = ManualModeCommitHost()
        self.n = 0
        self.records = {}

    def commit(self, text, uid, *, version=1, revises=""):
        """An assembler commit as it reaches the window: registered first."""
        self.n += 1
        # one ledger record per line, as the lifecycle keeps it
        record_id = self.records.setdefault(uid, revises or f"canon-{self.n:06d}")
        session = self.host._live_session_id
        observe_identity(
            session_id=session,
            channel_index=CHANNEL,
            canonical_utterance_id=uid,
            source_version=version,
            decision="SUPERSEDE" if revises else "CREATE_NEW",
            text=text,
            lifecycle_state="COMMITTED",
            translation_eligible=True,
        )
        assign_canonical_record_id(
            session_id=session,
            channel_index=CHANNEL,
            canonical_utterance_id=uid,
            canonical_record_id=record_id,
        )
        self.host._commit_transcript_item_to_store(
            {
                "speaker": 1,
                "text": text,
                "is_final": True,
                "speech_final": True,
                "_jp_cleaned": True,
                "_jp_continuity_assembler": True,
                "canonical_utterance_id": uid,
                "canonical_record_id": record_id,
                "revision_target_id": revises,
                "source_version": version,
                "channel_index": CHANNEL,
            }
        )

    def rows(self):
        return [(s.text, s.canonical_utterance_id) for s in self.host.transcript_store.get_all()]


class ANewLineIsItsOwnRowTest(_Case):
    def test_one_that_contains_the_row_before(self):
        self.commit("オッケー。", "jp-utt-aaaaaaaaaaa1")
        self.commit("これオッケーですか。", "jp-utt-bbbbbbbbbbb2")
        self.assertEqual(
            self.rows(),
            [("オッケー。", "jp-utt-aaaaaaaaaaa1"), ("これオッケーですか。", "jp-utt-bbbbbbbbbbb2")],
        )

    def test_one_the_row_before_ends_with(self):
        self.commit("これは何個はつぐみなのかな。", "jp-utt-aaaaaaaaaaa1")
        self.commit("なのかな。", "jp-utt-bbbbbbbbbbb2")
        self.assertEqual(
            self.rows(),
            [("これは何個はつぐみなのかな。", "jp-utt-aaaaaaaaaaa1"), ("なのかな。", "jp-utt-bbbbbbbbbbb2")],
        )

    def test_its_translation_lands_on_it(self):
        self.commit("ディスクリーン。", "jp-utt-aaaaaaaaaaa1")
        self.commit("ありきさんディスクリーンね。", "jp-utt-bbbbbbbbbbb2")
        self.host.transcript_store.add_translation(
            "ありきさんディスクリーンね。", "Mr. Ariki, the screen.", speaker=1,
            canonical_utterance_id="jp-utt-bbbbbbbbbbb2",
        )
        by_text = {s.text: s.translated_text for s in self.host.transcript_store.get_all()}
        self.assertEqual(by_text.get("ありきさんディスクリーンね。"), "Mr. Ariki, the screen.")


class ARevisionStillReplacesItsRowTest(_Case):
    def test_same_id_with_a_revision_target(self):
        self.commit("同じ。", "jp-utt-aaaaaaaaaaa1")
        self.commit("同じ。これ入ったチェック。", "jp-utt-aaaaaaaaaaa1", version=2, revises="canon-000001")
        self.assertEqual(self.rows(), [("同じ。これ入ったチェック。", "jp-utt-aaaaaaaaaaa1")])

    def test_same_id_without_a_revision_target(self):
        # the line's own id is proof enough that it is the same line
        self.commit("同じ。", "jp-utt-aaaaaaaaaaa1")
        self.commit("同じ。これ入ったチェック。", "jp-utt-aaaaaaaaaaa1", version=2)
        self.assertEqual(self.rows(), [("同じ。これ入ったチェック。", "jp-utt-aaaaaaaaaaa1")])


class _StopHost(ManualModeCommitHost):
    """Adds the real Stop-tail recovery; whatever else it reaches comes from
    AlphaApp, except the Tk pane, which is not built here."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        attr = getattr(AlphaApp, name)
        return attr.__get__(self) if callable(attr) else attr

    def _remove_interim_line_from_display(self, *a, **k):
        pass

    def _update_interim_line_only(self, *a, **k):
        pass

    def _on_store_segment_updated(self, *a, **k):
        pass


class TheWordsHeldAtStopShowOnceTest(_Case):
    """The Stop-tail recovery wrote pending words onto the last row (window
    only); the assembler's Stop flush right after commits the same words as a
    line of their own. With the window keeping every assembler line, they
    showed twice -- found by the review of item 71."""

    def setUp(self):
        self.host = _StopHost()
        self.n = 0
        self.records = {}

    def stop_with_pending(self, pending, held):
        self.host._latest_interim_committed = False
        self.host._latest_interim_text = pending
        self.host._latest_interim_speaker = 1
        self.host._pipeline_held_text = lambda: held
        self.host._recover_interim_tail_on_stop()

    def test_held_words_are_left_to_the_assemblers_flush(self):
        self.commit("はい。", "jp-utt-aaaaaaaaaaa1")
        self.stop_with_pending("はい、承知しました", held="はい、承知しました")
        self.commit("はい、承知しました。", "jp-utt-bbbbbbbbbbb2")  # the Stop flush
        self.assertEqual(
            [t for t, _i in self.rows()],
            ["はい。", "はい、承知しました。"],
        )

    def test_words_the_assembler_does_not_hold_still_reach_the_window(self):
        self.commit("はい。", "jp-utt-aaaaaaaaaaa1")
        self.stop_with_pending("はい、承知しました", held="")
        self.assertIn("承知しました", "".join(t for t, _i in self.rows()))


class TheEvidenceSaysWhatHappenedTest(_Case):
    def test_a_revision_is_not_logged_as_kept_as_its_own_row(self):
        from alpha.utils import japanese_accuracy_log as jal

        events = []
        original = jal.jp_accuracy_log

        def spy(event, **fields):
            events.append(event)
            return original(event, **fields)

        self.commit("違う話です。", "jp-utt-bbbbbbbbbbb2")
        with patch.object(jal, "jp_accuracy_log", spy):
            # a revision whose id is not the last row's, and whose text holds
            # that row's: the text says "update", the revision signal wins
            self.commit("違う話です。同じ。", "jp-utt-aaaaaaaaaaa1", revises="canon-000077")
        self.assertNotIn("ASSEMBLER_LINE_KEPT_AS_ITS_OWN_ROW", events)

    def test_the_window_logs_a_kept_line_as_new(self):
        from alpha.ui import main_window

        decisions = []
        original = main_window._session_ndjson_log

        def spy(*args, **kwargs):
            data = kwargs.get("data") or {}
            if kwargs.get("message") == "[JAPANESE] commit decision":
                decisions.append((data.get("decision"), data.get("text_preview")))
            return original(*args, **kwargs)

        self.commit("これは何個はつぐみなのかな。", "jp-utt-aaaaaaaaaaa1")
        with patch.object(main_window, "_session_ndjson_log", spy):
            self.commit("なのかな。", "jp-utt-bbbbbbbbbbb2")
        self.assertEqual([d for d, _t in decisions], ["commit_new"], decisions)


class ALineFromOutsideTheAssemblerIsUnchangedTest(_Case):
    """Only the assembler's own lines skip the text decision: a registered line
    without `_jp_continuity_assembler` (English) is still judged by its text."""

    def setUp(self):
        self.host = ManualModeCommitHost(listen_language="en")
        self.n = 0
        self.records = {}

    def test_a_shorter_resend_is_still_skipped(self):
        self.commit_english("I will send you the file today.", "en-utt-aaaaaaaaaaa1")
        self.commit_english("I will send you the file", "en-utt-bbbbbbbbbbb2")
        self.assertEqual([t for t, _i in self.rows()], ["I will send you the file today."])

    def commit_english(self, text, uid):
        self.n += 1
        record_id = self.records.setdefault(uid, f"canon-{self.n:06d}")
        session = self.host._live_session_id
        observe_identity(
            session_id=session, channel_index=CHANNEL, canonical_utterance_id=uid,
            source_version=1, decision="CREATE_NEW", text=text,
            lifecycle_state="COMMITTED", translation_eligible=True,
        )
        assign_canonical_record_id(
            session_id=session, channel_index=CHANNEL, canonical_utterance_id=uid,
            canonical_record_id=record_id,
        )
        self.host._commit_transcript_item_to_store(
            {
                "speaker": 1,
                "text": text,
                "is_final": True,
                "speech_final": True,
                "canonical_utterance_id": uid,
                "canonical_record_id": record_id,
                "source_version": 1,
                "channel_index": CHANNEL,
            }
        )


class AManualModeLineIsUnchangedTest(unittest.TestCase):
    def test_without_the_assembler_the_window_still_decides(self):
        host = ManualModeCommitHost()
        host.commit(1, "本日の会議の資料を", jp_continuity_assembler=False)
        host.commit(1, "確認してください", jp_continuity_assembler=False)
        self.assertEqual([s.text for s in host.transcript_store.get_all()], ["本日の会議の資料を確認してください"])


if __name__ == "__main__":
    unittest.main()
