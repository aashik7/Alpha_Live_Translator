"""Item 75 — per-entry meta row and current-entry highlight.

WHY THIS SHAPE
--------------
The ledger's own warning for this item is that it "is the first change that adds
a **logical line**, which is the single thing that breaks the arithmetic above —
font, wrap and spacing do not." Two mechanisms carry that risk and both are
pinned below:

* the render cap (item 74(a)) trims by the line count each segment reports, so
  the meta row must be counted or the pane grows unbounded and half-entries are
  stranded at the top;
* `_get_translated_transcript_for_copy_export` falls back to
  `box.get("1.0", "end")`, so any decorative row in the TRANSLATION pane is
  written verbatim into the client's delivered file. The transcript pane has no
  such fallback — `_get_clean_transcript_for_copy_export` builds from
  `transcript_store.get_all()` — which is why the meta row lives there and only
  there.

WHY TAG RANGES AND NOT WIDGETS
------------------------------
Measured before choosing, per `UI_REDESIGN_PROMPT.md` §"Phase 4": 430 entries as
CTk cards cost 15.33 s, 5,161 widgets and +49.3 MB RSS, against 38.46 ms for a
full `tk.Text` rewrite — 35.6 ms per card against 0.042 ms incremental. One card
is 3.6x the entire 10 ms per-tick budget.
"""

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

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

LONG_EN = (
    "This is the first sentence of the entry. This is the second one. "
    "A third sentence closes the first line off. Then a fourth begins. "
    "A fifth keeps it going a while longer so the grouping has work to do."
)


class _EntryFixture:
    """Shared host. NOT a TestCase, so the cases below do not re-run each
    other's tests just by sharing a setUp."""

    def setUp(self):
        from alpha.summary.transcript_store import TranscriptStore
        from alpha.ui.main_window import AlphaApp

        self.root = tk.Tk()
        self.root.withdraw()
        self.box = tk.Text(self.root)

        class Host:
            _insert_speaker_segment_line = AlphaApp._insert_speaker_segment_line
            _ensure_entry_tags = AlphaApp._ensure_entry_tags
            _entry_meta_text = AlphaApp._entry_meta_text
            _entry_meta_time_text = AlphaApp._entry_meta_time_text
            _highlight_current_entry = AlphaApp._highlight_current_entry
            _readable_segment_parts = AlphaApp._readable_segment_parts
            _ui_speaker_label_text = AlphaApp._ui_speaker_label_text
            _speaker_tag = AlphaApp._speaker_tag
            _segment_language = AlphaApp._segment_language
            _get_clean_transcript_for_copy_export = (
                AlphaApp._get_clean_transcript_for_copy_export
            )

            def __init__(self, store):
                self.transcript_store = store
                self._listen_language = "en"

            def _is_japanese_manual_mode(self):
                return False

        self.store = TranscriptStore()
        self.host = Host(self.store)

    def tearDown(self):
        self.root.destroy()

    def _commit(self, text, speaker=1, stamp=None):
        self.store.add_segment(
            speaker=speaker,
            text=text,
            timestamp=stamp if stamp is not None else time.time(),
            source_language="en",
        )
        return self.host._insert_speaker_segment_line(self.box, speaker, text)

    def _lines(self):
        return [l for l in self.box.get("1.0", "end").splitlines() if l.strip()]

@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheEntryCarriesAMetaRow(_EntryFixture, unittest.TestCase):
    """Drives the real `_insert_speaker_segment_line`."""

    def test_a_meta_row_is_rendered(self):
        self._commit("A short entry.")
        first = self._lines()[0]
        self.assertNotIn("A short entry", first, "the meta row is not above the body")
        self.assertRegex(first, r"\d{2}:\d{2}:\d{2}")

    def test_the_meta_row_uses_the_stored_timestamp(self):
        stamp = time.mktime((2026, 8, 21, 14, 32, 5, 0, 0, -1))
        self._commit("Timed entry.", stamp=stamp)
        self.assertIn("14:32:05", self._lines()[0])

    def test_the_meta_row_names_the_speaker_when_there_is_one(self):
        self._commit("Numbered speaker.", speaker=2)
        self.assertIn("2", self._lines()[0])

    def test_the_meta_row_is_tagged_and_the_body_is_not(self):
        self._commit("Tagged entry.")
        meta_ranges = self.box.tag_ranges("entry_meta")
        self.assertTrue(meta_ranges, "entry_meta tag was never applied")
        self.assertIn("entry_meta", self.box.tag_names("1.0"))
        self.assertNotIn("entry_meta", self.box.tag_names("2.0"))

    def test_the_returned_line_count_includes_the_meta_row(self):
        """The render cap trims by this number. Item 74(a)."""
        reported = self._commit("One short body line.")
        self.assertEqual(reported, len(self._lines()))
        self.assertGreaterEqual(reported, 2)

    def test_the_count_is_still_exact_for_a_grouped_multi_line_entry(self):
        reported = self._commit(LONG_EN)
        self.assertEqual(reported, len(self._lines()))
        self.assertGreater(reported, 2, "grouping produced no extra body lines")

    def test_the_body_is_still_grouped(self):
        """C8: every transcript render path must group."""
        self._commit(LONG_EN)
        body = [l for l in self._lines() if "Speaker" in l and ":" in l]
        self.assertGreater(len(body), 1, "the body was written as one raw line")

    def test_no_text_is_lost_to_the_meta_row(self):
        self._commit(LONG_EN)
        rendered = " ".join(self._lines()[1:])
        for word in LONG_EN.replace(".", " ").split():
            self.assertIn(word, rendered)

@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheCurrentEntryIsHighlighted(_EntryFixture, unittest.TestCase):
    def test_the_newest_entry_carries_the_highlight(self):
        self._commit("First entry.")
        self._commit("Second entry.")
        ranges = self.box.tag_ranges("current_entry")
        self.assertTrue(ranges, "current_entry tag was never applied")
        highlighted = self.box.get(str(ranges[0]), str(ranges[1]))
        self.assertIn("Second entry", highlighted)
        self.assertNotIn("First entry", highlighted)

    def test_only_one_entry_is_ever_highlighted(self):
        for i in range(4):
            self._commit(f"Entry number {i}.")
        ranges = self.box.tag_ranges("current_entry")
        self.assertEqual(len(ranges), 2, "more than one highlighted range")

    def test_the_highlight_covers_the_meta_row_too(self):
        self._commit("An entry.")
        ranges = self.box.tag_ranges("current_entry")
        highlighted = self.box.get(str(ranges[0]), str(ranges[1]))
        self.assertRegex(highlighted, r"\d{2}:\d{2}:\d{2}")

    def test_the_highlight_never_sets_a_foreground(self):
        """It must not fight the speaker/body colours."""
        self._commit("An entry.")
        self.assertEqual(self.box.tag_cget("current_entry", "foreground"), "")


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheClientsFileIsUnaffected(_EntryFixture, unittest.TestCase):
    """The decoration must never reach an exported transcript."""

    def test_the_exported_transcript_has_no_meta_row(self):
        self._commit(LONG_EN)
        exported = self.host._get_clean_transcript_for_copy_export()
        self.assertIn("first sentence of the entry", exported)
        self.assertNotRegex(exported, r"\d{2}:\d{2}:\d{2}")

    def test_the_export_line_count_matches_the_store_not_the_pane(self):
        self._commit("First entry.")
        self._commit("Second entry.")
        exported = [l for l in
                    self.host._get_clean_transcript_for_copy_export().splitlines()
                    if l.strip()]
        self.assertEqual(len(exported), 2)
        self.assertGreater(len(self._lines()), len(exported))


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheMetaRowFailsSoft(_EntryFixture, unittest.TestCase):
    def test_an_unreadable_timestamp_still_renders_the_entry(self):
        self.store.add_segment(
            speaker=1, text="Bad stamp.", timestamp="not-a-time", source_language="en"
        )
        reported = self.host._insert_speaker_segment_line(self.box, 1, "Bad stamp.")
        self.assertEqual(reported, len(self._lines()))
        self.assertIn("Bad stamp", self.box.get("1.0", "end"))

    def test_an_absent_store_still_renders_the_entry(self):
        self.host.transcript_store = None
        reported = self.host._insert_speaker_segment_line(self.box, 1, "No store.")
        self.assertEqual(reported, len(self._lines()))
        self.assertIn("No store", self.box.get("1.0", "end"))


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheTranslationPaneStaysUndecorated(unittest.TestCase):
    """`_get_translated_transcript_for_copy_export` reads the widget as a
    fallback, so nothing decorative may ever be written into that box."""

    def test_the_translation_renderer_writes_no_meta_row(self):
        from alpha.ui.main_window import AlphaApp

        root = tk.Tk()
        root.withdraw()
        box = tk.Text(root)
        try:

            class Host:
                _clear_translation_loading_item = (
                    AlphaApp._clear_translation_loading_item
                )
                _delete_translation_entry = AlphaApp._delete_translation_entry
                _readable_translation_parts = AlphaApp._readable_translation_parts
                _ui_speaker_label_text = AlphaApp._ui_speaker_label_text

                def __init__(self, box):
                    self.translated_verse_box = box
                    self._translation_loading_items = {}
                    self._translation_items_by_utterance = {}

                def _clear_text_placeholder(self, *a, **k):
                    pass

                def _log_translation_display_skip(self, **kw):
                    pass

            host = Host(box)
            host._clear_translation_loading_item(
                segment_id=1,
                terminal_state="completed",
                replace_with_text="A finished translation.",
                canonical_utterance_id="jp-utt-abc123def456",
                source_version=1,
            )
            rendered = box.get("1.0", "end")
            self.assertIn("A finished translation", rendered)
            self.assertNotRegex(rendered, r"\d{2}:\d{2}:\d{2}")
            self.assertNotIn("entry_meta", box.tag_names())
            self.assertNotIn("current_entry", box.tag_names())
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
