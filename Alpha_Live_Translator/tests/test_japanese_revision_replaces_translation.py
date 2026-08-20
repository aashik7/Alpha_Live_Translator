"""A revised Japanese utterance must not leave its old translation on screen.

WHY THIS EXISTS
---------------
Reported as "EN->JA reads fine but JA->EN does not". Measured on the JA source
run `v3.3.5.5.8.5.26.5.3-20260820-140328`, the transcript pane was clean (119
lines, median 37 characters, zero near-duplicate pairs) while the English
TRANSLATION pane held 6 adjacent near-duplicate pairs -- about 4% of the pane
was the same sentence shown twice with small wording differences, e.g.

    "...it's tiring to say long sentences, isn't it?"
    "...it's tiring to say long words in advance, isn't it?"

WHY ONLY THIS DIRECTION
-----------------------
`duplicate_protection.py` promotes a commit from "add" to "update" -- and so
routes it to `_on_store_segment_updated`, which removes the superseded
translation -- only on an authoritative revision signal:

    life_decision in ("SUPERSEDE_PREVIOUS", "REPLACE_ACTIVE", "EXTEND_ACTIVE")
    or item.get("superseded_record_id") or item.get("revision_target_id")

That vocabulary belongs to the ENGLISH `utterance_lifecycle.py`. The Japanese
assembler signals a revision as `applied_action == "revise"`, which appears in
none of those fields, and it deliberately leaves `revision_target_id` unset
(see `japanese_sentence_assembler.py`, and the item 20b retraction in
`CANONICAL_KEY_FIELDS_AUDIT.md` section 5b -- a self-referential id there was
itself a bug). The same run's evidence shows exactly that split:

    canonical_commits.jsonl   applied_action: append 109, revise 11
    utterance_decisions.jsonl decision: CREATE_NEW 120, revision_target_id set 0/120
    translation_jobs.jsonl    133 jobs for 120 ids -- 11 ids translated 2-3 times

So every Japanese revision reaches the UI through `_on_store_segment_added`,
which submits a second translation but never removes the first.

THE COMPOUNDING PART
--------------------
When the revision's translation completes, `_clear_translation_loading_item`
looks up the LOADING mark `tr_load_<segment_id>` -- not the completed mark
`tr_done_<utterance_id>_<version>` -- so it deletes nothing, then OVERWRITES
the registry entry with the new mark. The old line's mark is lost, so no later
revision can reclaim it either. It stays for the rest of the session and
reaches the client's file through `_get_translated_transcript_for_copy_export`'s
widget-read fallback.

Worse, that same call handed the deleter the COMPLETED entry's line count while
pointing it at the LOADING mark, so on a revision it would delete N rows
starting at the pending row -- eating whatever translations happened to follow.

WHERE THE REMOVAL GOES, AND WHY NOT AT COMMIT TIME
---------------------------------------------------
In `_clear_translation_loading_item`, at the moment the replacement text is
written -- not in `_on_store_segment_added` when the revision commits. At
commit time the pane would be blank for that utterance for the whole
translation round-trip, and if the resubmitted job then failed the utterance
would be lost from the pane entirely. Removing at write time means the
superseded line survives until its replacement actually exists. Both properties
are pinned below.

WHAT IS DELIBERATELY NOT CHANGED
--------------------------------
The add/update decision in `duplicate_protection.py`. Promoting the Japanese
"revise" signal to `action = "update"` there would also REPLACE the stored
transcript line, and that file's own comment records why that direction is the
dangerous one: "a wrong guess destroys committed speech ... the opposite and
worse failure direction". The transcript pane is already correct. The fix stays
in the translation pane, and keys on identity exactly as that file requires.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover
    TK_AVAILABLE = False

# The real pair from the run, translation jobs #17 and #18.
EN_V1 = "It's a way of speaking with low energy. I see."
EN_V2 = "It's a way of speaking with low energy. I see. I'm actually a bit against that."
JA_V1 = "元気のない話し方ですね。"
JA_V2 = (
    "元気のない話し方ですね。"
    "実は反対です。"
)

LONG_V1 = (
    "If you speak without opening your mouth very wide, it's tiring to say "
    "long sentences, isn't it? Yes, it's hard. That is the difficulty."
)
LONG_V2 = (
    "If you speak without opening your mouth very wide, it's tiring to say "
    "long words in advance, isn't it? Yes, it's hard. That is the difficulty."
)


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class ARevisionDeliveredAsAnAddReplacesItsTranslation(unittest.TestCase):
    """Drives the real `_on_store_segment_added` + `_clear_translation_loading_item`."""

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.root = tk.Tk()
        self.root.withdraw()
        self.box = tk.Text(self.root)

        class Host:
            _on_store_segment_added = AlphaApp._on_store_segment_added
            _clear_translation_loading_item = AlphaApp._clear_translation_loading_item
            _remove_translation_item_for_utterance = (
                AlphaApp._remove_translation_item_for_utterance
            )
            _delete_translation_entry = AlphaApp._delete_translation_entry
            _readable_translation_parts = AlphaApp._readable_translation_parts
            _ui_speaker_label_text = AlphaApp._ui_speaker_label_text

            def __init__(self, box):
                self.translated_verse_box = box
                self._translation_loading_items = {}
                self._translation_items_by_utterance = {}
                self.submitted = []
                self.skips = []

            # The transcript box is genuinely absent here: this test is about
            # the translation pane, and `_on_store_segment_added` returns right
            # after the translation submit when there is no transcript widget.
            def _transcript_box(self):
                return None

            def _remove_interim_line_from_display(self):
                pass

            def _clear_text_placeholder(self, *a, **k):
                pass

            def submit_text_for_translation(self, text, **kw):
                self.submitted.append((text, kw))

            def _log_translation_display_skip(self, **kw):
                self.skips.append(kw)

        self.host = Host(self.box)

    def tearDown(self):
        self.root.destroy()

    def _commit(self, text, key, version=1):
        """A canonical commit reaching the UI as an ADD, which is what the
        Japanese revision path actually does (decision: CREATE_NEW)."""
        self.host._on_store_segment_added(
            1,
            text,
            canonical_utterance_id=key,
            source_version=version,
            source_record_id="rec-" + key,
        )

    def _translated(self, segment_id, key, text, version=1):
        self.host._clear_translation_loading_item(
            segment_id=segment_id,
            terminal_state="completed",
            replace_with_text=text,
            canonical_utterance_id=key,
            source_version=version,
        )

    def _pane(self):
        return self.box.get("1.0", "end")

    def test_the_superseded_translation_is_gone(self):
        self._commit(JA_V1, "jp-utt-A")
        self._translated(1, "jp-utt-A", EN_V1)
        self._commit(JA_V2, "jp-utt-A")
        self._translated(2, "jp-utt-A", EN_V2)
        self.assertIn("I'm actually a bit against that", self._pane())
        self.assertEqual(
            self._pane().count("It's a way of speaking with low energy"),
            1,
            "the superseded translation is still on screen beside the new one",
        )

    def test_a_multi_line_revision_leaves_no_orphan_lines(self):
        """Item 83 groups a translation into 2-3 lines; all of them must go."""
        self._commit(JA_V1, "jp-utt-B")
        self._translated(1, "jp-utt-B", LONG_V1)
        self._commit(JA_V2, "jp-utt-B")
        self._translated(2, "jp-utt-B", LONG_V2)
        self.assertIn("long words in advance", self._pane())
        self.assertNotIn("long sentences", self._pane())

    def test_a_third_version_also_replaces(self):
        """Two ids in the run were translated three times."""
        self._commit(JA_V1, "jp-utt-C")
        self._translated(1, "jp-utt-C", "First rendering of it.")
        self._commit(JA_V2, "jp-utt-C")
        self._translated(2, "jp-utt-C", "Second rendering of it.")
        self._commit(JA_V2, "jp-utt-C")
        self._translated(3, "jp-utt-C", "Third rendering of it.")
        pane = self._pane()
        self.assertIn("Third rendering", pane)
        self.assertNotIn("First rendering", pane)
        self.assertNotIn("Second rendering", pane)

    def test_a_genuinely_new_utterance_is_never_removed(self):
        """The whole safety of the fix: identity, never position or similarity.

        Two distinct utterances can be textually near-identical. Only a
        matching canonical_utterance_id may cause a removal.
        """
        self._commit("A", "jp-utt-D")
        self._translated(1, "jp-utt-D", "The first quarter was strong.")
        self._commit("B", "jp-utt-E")
        self._translated(2, "jp-utt-E", "The second quarter was strong.")
        pane = self._pane()
        self.assertIn("first quarter", pane)
        self.assertIn("second quarter", pane)

    def test_an_append_run_keeps_every_line(self):
        """109 of the run's 120 commits were genuine appends; none may be lost."""
        for i in range(6):
            key = "jp-utt-seq-%d" % i
            self._commit("t%d" % i, key)
            self._translated(i + 1, key, "Rendering number %d here." % i)
        for i in range(6):
            self.assertIn("number %d here" % i, self._pane())

    def test_the_translation_is_still_submitted_for_a_revision(self):
        """Removal must not short-circuit the resubmit."""
        self._commit(JA_V1, "jp-utt-F")
        self._translated(1, "jp-utt-F", EN_V1)
        self._commit(JA_V2, "jp-utt-F")
        self.assertEqual(len(self.host.submitted), 2)
        self.assertEqual(self.host.submitted[-1][0], JA_V2)

    def test_a_commit_with_no_canonical_id_removes_nothing(self):
        self._commit(JA_V1, "jp-utt-G")
        self._translated(1, "jp-utt-G", EN_V1)
        self._commit(JA_V2, "")
        self.assertIn("low energy", self._pane())

    def test_the_old_line_survives_until_the_replacement_actually_arrives(self):
        """Why the removal is at write time and not at commit time.

        Between the revision committing and its translation returning there is
        a full provider round-trip. The superseded line is stale, but it is the
        only rendering of that utterance in the pane during that window.
        """
        self._commit(JA_V1, "jp-utt-H")
        self._translated(1, "jp-utt-H", EN_V1)
        self._commit(JA_V2, "jp-utt-H")
        self.assertIn("low energy", self._pane())

    def test_a_failed_replacement_does_not_lose_the_utterance(self):
        """The failure this placement exists to prevent.

        If the resubmitted job dies, removing at commit time would have left
        the pane with no rendering of this utterance at all.
        """
        self._commit(JA_V1, "jp-utt-I")
        self._translated(1, "jp-utt-I", EN_V1)
        self._commit(JA_V2, "jp-utt-I")
        self.host._clear_translation_loading_item(
            segment_id=2,
            terminal_state="failed",
            replace_with_text=None,
            canonical_utterance_id="jp-utt-I",
            source_version=1,
        )
        self.assertIn("low energy", self._pane())

    def test_a_pending_row_removal_does_not_eat_the_lines_after_it(self):
        """The loading mark is one row, whatever the finished entry occupies.

        This call used to be handed the COMPLETED entry's line count while
        pointing at the LOADING mark, so a revision of a three-line entry
        deleted three rows starting at the pending row.
        """
        self._commit(JA_V1, "jp-utt-J")
        self._translated(1, "jp-utt-J", LONG_V1)
        self._commit("other", "jp-utt-K")
        self._translated(2, "jp-utt-K", "A neighbour that must survive.")
        # A pending row for the revision, then its result.
        self.host._translation_loading_items[3] = {"mark": "tr_load_3"}
        self._commit(JA_V2, "jp-utt-J")
        self._translated(3, "jp-utt-J", LONG_V2)
        pane = self._pane()
        self.assertIn("A neighbour that must survive", pane)
        self.assertIn("long words in advance", pane)
        self.assertNotIn("long sentences", pane)


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class AHyphenatedMarkNameStillDeletes(unittest.TestCase):
    """The deeper defect, pinned on its own.

    Every canonical utterance id is `jp-utt-<hex>`, so the completed mark is
    `tr_done_jp-utt-e0dcbd1255fc_1`. Tk reads a `-`/`+` run inside a text index
    as a modifier operator, so the composed end expression was rejected while
    the guard in front of it passed:

        index("tr_done_jp-utt-A_1")                   -> "2.0"     ok
        compare("tr_done_jp-utt-A_1", ">=", "1.0")    -> True      ok
        index("tr_done_jp-utt-A_1 lineend + 1 chars") -> TclError

    Both callers swallow the exception, so the delete failed silently. The
    earlier tests for this removal used `"u1"` as the id -- a shape production
    never produces -- which is exactly why they passed while the Japanese path
    was broken in the field.
    """

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.root = tk.Tk()
        self.root.withdraw()
        self.box = tk.Text(self.root)

        class Host:
            _delete_translation_entry = AlphaApp._delete_translation_entry

        self.host = Host()

    def tearDown(self):
        self.root.destroy()

    def test_tk_still_rejects_the_composed_expression(self):
        """Pins WHY the fix is needed. If Tk ever accepts this, revisit."""
        self.box.insert("end", "only line\n")
        self.box.mark_set("tr_done_jp-utt-A_1", "1.0")
        with self.assertRaises(tk.TclError):
            self.box.index("tr_done_jp-utt-A_1 lineend + 1 chars")

    def test_a_one_line_entry_with_a_hyphenated_mark_is_deleted(self):
        self.box.insert("end", "first line\n")
        self.box.mark_set("tr_done_jp-utt-e0dcbd1255fc_1", "1.0")
        self.box.insert("end", "second line\n")
        self.host._delete_translation_entry(
            self.box, "tr_done_jp-utt-e0dcbd1255fc_1", {"entry_lines": 1}
        )
        remaining = self.box.get("1.0", "end")
        self.assertNotIn("first line", remaining)
        self.assertIn("second line", remaining)

    def test_a_three_line_entry_with_a_hyphenated_mark_leaves_no_orphan(self):
        self.box.insert("end", "a one\na two\na three\n")
        self.box.mark_set("tr_done_jp-utt-2d6b8f58a921_2", "1.0")
        self.box.insert("end", "keep me\n")
        self.host._delete_translation_entry(
            self.box, "tr_done_jp-utt-2d6b8f58a921_2", {"entry_lines": 3}
        )
        remaining = self.box.get("1.0", "end")
        for gone in ("a one", "a two", "a three"):
            self.assertNotIn(gone, remaining)
        self.assertIn("keep me", remaining)

    def test_an_unhyphenated_mark_still_behaves(self):
        """The old shape must not regress."""
        self.box.insert("end", "first line\n")
        self.box.mark_set("tr_done_u1_1", "1.0")
        self.box.insert("end", "second line\n")
        self.host._delete_translation_entry(self.box, "tr_done_u1_1", {"entry_lines": 1})
        remaining = self.box.get("1.0", "end")
        self.assertNotIn("first line", remaining)
        self.assertIn("second line", remaining)


if __name__ == "__main__":
    unittest.main()
