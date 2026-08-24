"""The transcript pane starts visible, and one function owns its state.

WHAT WAS REPORTED
-----------------
On a large desktop screen, pressing "Show Transcript" swapped the button but the
transcript pane never appeared; pressing "Hide" removed the pane but the
translation did not expand into the freed space. One screenshot showed BOTH the
"Show Transcript" and "Hide" buttons mapped at the same time. Correct on a
laptop screen.

WHAT WAS FOUND, AND WHAT WAS NOT
--------------------------------
The geometry function is not the defect. Driven on a real CTk root at 900, 1400
and 1920 design px, the real `toggle_initial_verse` produced the correct result
every time: transcript at 30% when shown, primary back to 99% of the wrapper
when hidden, exactly one button mapped, no exception. **The reported failure was
not reproducible headlessly and its cause is NOT claimed here.**

What IS fixable without reproducing it is the shape that allows it. The button
state and the grid state were written in two places:

  * `_create_verse_section` gridded "Show Transcript" at construction, while
  * `_initial_verse_visible` was set separately, and
  * `toggle_initial_verse` wrote both, inside a `try/except` that only printed.

Any raise between the two halves leaves them disagreeing, and the two reported
symptoms are exactly what disagreement looks like in each direction. A
both-buttons-mapped screenshot is only reachable if one half ran without the
other.

`_sync_transcript_visibility` is now the single writer: it derives the button
AND asks `_apply_content_layout`, both from one flag. The toggle restores the
previous flag if it raises, so a failed press is not silently a no-op, and logs
`TRANSCRIPT_PANE_TOGGLE_FAILED` rather than leaving a lone print.

DEFAULT
-------
The pane now starts visible. It used to start hidden, so a fresh launch showed
only the translation and the user had to find a button before seeing what was
being transcribed.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless CI without a display
    TK_AVAILABLE = False

if TK_AVAILABLE:
    import customtkinter as ctk

    import alpha.ui.main_window as MW
    from alpha.ui.main_window import AlphaApp


def _host(width=1400):
    """A real widget host carrying the real reading-grid methods."""

    class Host(ctk.CTk):
        _apply_content_layout = AlphaApp._apply_content_layout
        _build_content_column = AlphaApp._build_content_column
        _design_width = AlphaApp._design_width
        _design_px = AlphaApp._design_px
        _place_toggle_button = AlphaApp._place_toggle_button
        _sync_transcript_visibility = AlphaApp._sync_transcript_visibility
        # `_sync_transcript_visibility` ends by enforcing its own
        # postcondition, and `toggle_initial_verse` writes a trace. A host
        # missing either turns this file's every test into an AttributeError.
        _ensure_transcript_pane_matches_flag = (
            AlphaApp._ensure_transcript_pane_matches_flag
        )
        _trace_transcript_toggle = AlphaApp._trace_transcript_toggle
        _get_layout_mode = AlphaApp._get_layout_mode
        _record_layout_snapshot = lambda self, mode: None
        toggle_initial_verse = AlphaApp.toggle_initial_verse
        summary_panel_visible = False

        def build(self):
            self.grid_rowconfigure(0, weight=1)
            self.grid_columnconfigure(0, weight=1)
            self.content_wrapper = ctk.CTkFrame(self, fg_color="transparent")
            self.content_wrapper.grid(row=0, column=0, sticky="nsew", padx=20)
            self.content_wrapper.grid_columnconfigure(
                0, weight=MW.CONTENT_PRIMARY_WEIGHT
            )
            self.content_wrapper.grid_columnconfigure(1, weight=0)
            self.content_wrapper.grid_columnconfigure(2, weight=0)
            self.content_wrapper.grid_rowconfigure(0, weight=1)
            self.left_column = self._build_content_column(0, padx=(0, 8))
            self.transcript_column = self._build_content_column(1, padx=(8, 0))
            self.right_column = self._build_content_column(2, padx=(8, 0))
            self.translated_title_row = ctk.CTkFrame(self.left_column)
            self.translated_title_row.grid(row=0, column=0, sticky="ew")
            self.translated_title_row.grid_columnconfigure(0, weight=1)
            self.initial_title_row = ctk.CTkFrame(self.transcript_column)
            self.initial_title_row.grid(row=0, column=0, sticky="ew")
            self.initial_title_row.grid_columnconfigure(0, weight=1)
            self.hide_initial_button = ctk.CTkButton(
                self.initial_title_row, text="Hide", width=64
            )
            self.show_initial_button = ctk.CTkButton(
                self.translated_title_row, text="Show Transcript", width=128
            )
            self.hide_initial_button.grid_remove()
            self.show_initial_button.grid_remove()
            self.transcript_column.grid_remove()
            self.right_column.grid_remove()

    app = Host()
    app.geometry(f"{width}x760")
    app.update()
    app.build()
    app.update()
    return app


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class TheTranscriptPaneStartsVisible(unittest.TestCase):
    def test_the_default_flag_is_visible(self):
        src = Path(MW.__file__).read_text(encoding="utf-8", errors="replace")
        self.assertIn(
            "self._initial_verse_visible = True",
            src,
            "the transcript pane no longer defaults to visible",
        )

    def test_the_first_sync_shows_the_pane_and_the_hide_button(self):
        app = _host()
        try:
            app._initial_verse_visible = True
            app._sync_transcript_visibility()
            app.update()
            self.assertTrue(app.transcript_column.winfo_ismapped())
            self.assertTrue(app.hide_initial_button.winfo_ismapped())
            self.assertFalse(app.show_initial_button.winfo_ismapped())
        finally:
            app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class ExactlyOneButtonIsEverMapped(unittest.TestCase):
    def _both(self, app):
        return (
            bool(app.show_initial_button.winfo_ismapped()),
            bool(app.hide_initial_button.winfo_ismapped()),
        )

    def test_never_both_across_repeated_toggles(self):
        app = _host()
        try:
            app._initial_verse_visible = True
            app._sync_transcript_visibility()
            app.update()
            for _ in range(6):
                app.toggle_initial_verse()
                app.update()
                show, hide = self._both(app)
                self.assertNotEqual(
                    show, hide, f"both buttons in the same state: {(show, hide)}"
                )
        finally:
            app.destroy()

    def test_the_button_always_matches_the_pane(self):
        app = _host()
        try:
            for want in (True, False, True, False):
                app._initial_verse_visible = want
                app._sync_transcript_visibility()
                app.update()
                self.assertEqual(bool(app.transcript_column.winfo_ismapped()), want)
                self.assertEqual(bool(app.hide_initial_button.winfo_ismapped()), want)
                self.assertEqual(
                    bool(app.show_initial_button.winfo_ismapped()), not want
                )
        finally:
            app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class HidingReturnsTheSpaceToTheTranslation(unittest.TestCase):
    def test_the_primary_pane_fills_the_wrapper_when_the_pane_is_hidden(self):
        """The second reported symptom: the transcript went away and the
        translation did not grow into the gap."""
        for width in (900, 1400, 1920):
            app = _host(width)
            try:
                app._initial_verse_visible = False
                app._sync_transcript_visibility()
                app.update()
                wrapper = app.content_wrapper.winfo_width()
                primary = app.left_column.winfo_width()
                self.assertGreater(
                    primary / max(1, wrapper),
                    0.95,
                    f"at {width}px the translation used only "
                    f"{100 * primary // max(1, wrapper)}% of the width",
                )
            finally:
                app.destroy()

    def test_showing_gives_the_reference_pane_its_share(self):
        for width in (900, 1400, 1920):
            app = _host(width)
            try:
                app._initial_verse_visible = True
                app._sync_transcript_visibility()
                app.update()
                wrapper = app.content_wrapper.winfo_width()
                reference = app.transcript_column.winfo_width()
                share = reference / max(1, wrapper)
                self.assertGreater(share, 0.2, f"reference pane too narrow at {width}")
                self.assertLess(share, 0.45, f"reference pane too wide at {width}")
            finally:
                app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class AFailedToggleIsNotSilent(unittest.TestCase):
    def test_the_flag_is_restored_so_the_next_press_still_works(self):
        app = _host()
        try:
            app._initial_verse_visible = True
            app._sync_transcript_visibility()
            app.update()
            with patch.object(
                type(app),
                "_sync_transcript_visibility",
                side_effect=RuntimeError("boom"),
            ):
                app.toggle_initial_verse()
            self.assertTrue(
                app._initial_verse_visible,
                "a failed toggle left the flag flipped, so the next press is a no-op",
            )
        finally:
            app.destroy()

    def test_a_failure_is_logged(self):
        app = _host()
        try:
            logged = []
            with patch.object(
                type(app),
                "_sync_transcript_visibility",
                side_effect=RuntimeError("boom"),
            ), patch(
                "alpha.utils.japanese_accuracy_log.jp_accuracy_log",
                side_effect=lambda event, **kw: logged.append(event),
            ):
                app.toggle_initial_verse()
            self.assertIn("TRANSCRIPT_PANE_TOGGLE_FAILED", logged)
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
