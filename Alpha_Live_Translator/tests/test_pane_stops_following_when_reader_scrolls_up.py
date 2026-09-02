"""A reading pane must not drag the reader back to the newest line.

WHAT WAS BROKEN
---------------
Every writer of the two reading panes ended with an unconditional

    box.see(tk.END)

so while a translation was arriving the pane scrolled itself to the bottom on
every update -- correct while the reader is watching the newest line, and
unusable the moment they scroll up to re-read something. The next arriving
segment yanked them straight back down, and a live session produces one every
few seconds, so scrolling up was effectively impossible.

THE FIX
-------
`alpha.ui.follow_tail.scroll_to_tail` replaces the bare `see(tk.END)` at all
eight writers. It scrolls only while the pane is following its tail, and a pane
stops following the moment the reader scrolls up in it. A floating arrow then
offers the way back; pressing it, or scrolling back to the bottom by hand,
re-arms following.

WHAT THESE TESTS PIN
--------------------
* the differential itself -- the same widget, in the same scrolled-up state,
  yanked by the old call and left alone by the new one
* the flag is moved only by the reader's own scrolling, never by a
  programmatic scroll (otherwise auto-scroll re-arms itself and the reader can
  never stay put -- the original bug, one level down)
* the arrow maps and unmaps with that state
* a pane whose content no longer overflows hands the tail back on its own, so
  Clear and a language switch need no knowledge of this feature
* every writer really goes through the gate -- checked by walking the AST of
  both modules, not by reading them

Real widgets and the real `AlphaApp` methods throughout: `winfo_ismapped()` and
`yview()` are meaningless on a stub, and a stub is exactly what would let this
pass with the feature gone.
"""

import ast
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.follow_tail import scroll_to_tail  # noqa: E402

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless CI without a display
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "Tk cannot start in this environment")
class FollowTailTest(unittest.TestCase):
    """The pane is built the way `_create_styled_text` builds it, then driven
    with the real `AlphaApp` methods."""

    def setUp(self):
        import customtkinter as ctk

        from alpha.ui.main_window import AlphaApp

        self.root = ctk.CTk()
        self.root.geometry("500x300")
        self.root.update()

        self.frame = ctk.CTkFrame(master=self.root)
        self.frame.pack(fill="both", expand=True)
        self.scrollbar = ctk.CTkScrollbar(master=self.frame, orientation="vertical")
        self.text = tk.Text(master=self.frame, wrap="word")
        self.text.pack(side="left", fill="both", expand=True)
        self.scrollbar.configure(command=self.text.yview)

        class Host:
            check_scrollbar_visibility = AlphaApp.check_scrollbar_visibility
            _bind_scroll_autohide = AlphaApp._bind_scroll_autohide
            _bind_follow_tail = AlphaApp._bind_follow_tail
            _sync_follow_tail_button = AlphaApp._sync_follow_tail_button
            _note_reader_scrolled = AlphaApp._note_reader_scrolled
            _jump_to_latest = AlphaApp._jump_to_latest
            _maybe_scroll_transcript_box = AlphaApp._maybe_scroll_transcript_box
            _transcript_ui_scroll_last_mono = 0.0

        self.host = Host()
        self.host._bind_scroll_autohide(self.text, self.scrollbar)

        self.text._follow_button = ctk.CTkButton(
            master=self.frame, text="↓", width=30, height=30, command=lambda: None
        )
        self.host._bind_follow_tail(self.text, self.scrollbar)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    # -- helpers ---------------------------------------------------------

    def _fill(self, lines=300, start=0):
        self.text.configure(state="normal")
        for i in range(start, start + lines):
            self.text.insert("end", "line %d\n" % i)
        self.text.configure(state="disabled")
        self.root.update()

    def _reader_scrolls_to(self, fraction):
        """A reader-driven scroll: move the view, then run the real handler
        that the `<MouseWheel>` binding and the scrollbar command both call."""
        self.text.yview_moveto(fraction)
        self.root.update()
        self.host._note_reader_scrolled(self.text)
        self.root.update()

    # -- the differential ------------------------------------------------

    def test_the_old_unconditional_see_is_what_yanked_the_reader_back(self):
        """The pre-fix call and the post-fix call, same widget, same state.

        Without this the suite could pass with the feature deleted and nothing
        would ever have exercised the behaviour it was written for.
        """
        self._fill()
        self._reader_scrolls_to(0.0)
        self.assertEqual(self.text.yview()[0], 0.0)

        self.text.see(tk.END)  # exactly what all eight writers used to do
        self.root.update()
        self.assertGreater(
            self.text.yview()[0],
            0.0,
            "the old call did not move the view -- the rest of this file "
            "would then be proving nothing",
        )

        self._reader_scrolls_to(0.0)
        scroll_to_tail(self.text)
        self.root.update()
        self.assertEqual(
            self.text.yview()[0],
            0.0,
            "the reader was dragged back to the bottom",
        )

    # -- the flag --------------------------------------------------------

    def test_a_pane_follows_its_tail_until_the_reader_says_otherwise(self):
        self._fill()
        self.assertTrue(self.text._follow_tail)
        scroll_to_tail(self.text)
        self.root.update()
        self.assertGreaterEqual(self.text.yview()[1], 0.999)

    def test_new_text_does_not_move_a_reader_who_scrolled_up(self):
        self._fill()
        self._reader_scrolls_to(0.0)
        for _ in range(5):
            self._fill(lines=20, start=1000)
            scroll_to_tail(self.text)
            self.root.update()
        self.assertEqual(
            self.text.yview()[0], 0.0, "arriving text scrolled the pane anyway"
        )

    def test_a_programmatic_scroll_never_re_arms_following(self):
        """The bug one level down: if `see()` could set the flag, the pane's
        own auto-scroll would keep switching following back on."""
        self._fill()
        self._reader_scrolls_to(0.0)
        self.text.see(tk.END)
        self.root.update()
        self.assertFalse(
            self.text._follow_tail,
            "a programmatic scroll re-armed following",
        )

    def test_scrolling_back_to_the_bottom_by_hand_resumes_following(self):
        self._fill()
        self._reader_scrolls_to(0.0)
        self.assertFalse(self.text._follow_tail)
        self._reader_scrolls_to(1.0)
        self.assertTrue(self.text._follow_tail, "following never came back")
        self._fill(lines=20, start=2000)
        scroll_to_tail(self.text)
        self.root.update()
        self.assertGreaterEqual(self.text.yview()[1], 0.999)

    def test_the_scrollbar_command_is_a_reader_scroll_too(self):
        """CTkScrollbar routes the thumb drag, the trough click and its own
        wheel through this one command, so the wrapper has to be on it."""
        self._fill()
        command = self.scrollbar.cget("command")
        command("moveto", 0.0)
        self.root.update()
        self.assertFalse(
            self.text._follow_tail,
            "dragging the scrollbar up did not stop the pane following",
        )

    def test_a_real_mouse_wheel_event_stops_it_following(self):
        """End to end through Tk's own binding, not a helper call."""
        self._fill()
        self.text.event_generate("<MouseWheel>", delta=120, x=10, y=10)
        self.root.update()
        self.root.update_idletasks()
        if self.text.yview()[1] >= 0.999:
            self.skipTest("this Tk did not scroll on a synthetic wheel event")
        self.assertFalse(
            self.text._follow_tail,
            "a real wheel scroll left the pane following its tail",
        )

    # -- the arrow -------------------------------------------------------

    def test_the_arrow_appears_only_while_the_reader_is_scrolled_up(self):
        self._fill()
        self.host._sync_follow_tail_button(self.text)
        self.root.update()
        self.assertFalse(
            self.text._follow_button.winfo_ismapped(),
            "the arrow is showing while the pane is at its tail",
        )

        self._reader_scrolls_to(0.0)
        self.assertTrue(
            self.text._follow_button.winfo_ismapped(), "the arrow never appeared"
        )

        self._reader_scrolls_to(1.0)
        self.assertFalse(
            self.text._follow_button.winfo_ismapped(), "the arrow never went away"
        )

    def test_the_arrow_goes_to_the_last_line_and_re_arms_following(self):
        self._fill()
        self._reader_scrolls_to(0.0)
        self.host._jump_to_latest(self.text)
        self.root.update()
        self.assertGreaterEqual(self.text.yview()[1], 0.999)
        self.assertTrue(self.text._follow_tail)
        self.assertFalse(self.text._follow_button.winfo_ismapped())

    def test_the_arrow_is_on_top_of_the_text_not_behind_it(self):
        """It floats over the pane, so a stacking mistake would leave a button
        that is mapped, sized and completely unclickable -- which every other
        assertion here would still pass."""
        self._fill()
        self._reader_scrolls_to(0.0)
        button = self.text._follow_button
        self.root.update()
        x = button.winfo_rootx() + button.winfo_width() // 2
        y = button.winfo_rooty() + button.winfo_height() // 2
        hit = self.root.winfo_containing(x, y)
        if hit is None:
            self.skipTest("the window is not on screen; hit testing is vacuous")
        self.assertTrue(
            str(hit).startswith(str(button)),
            "the point at the arrow's centre hits %r, not the arrow" % (str(hit),),
        )

    def test_content_that_no_longer_overflows_hands_the_tail_back(self):
        """Clear, a language switch and the placeholder all shrink the pane to
        content that fits. None of them should have to know this feature
        exists, so `yview() == (0.0, 1.0)` re-arms following on its own."""
        self._fill()
        self._reader_scrolls_to(0.0)
        self.assertFalse(self.text._follow_tail)

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "one short line\n")
        self.text.configure(state="disabled")
        self.root.update()
        self.host._sync_follow_tail_button(self.text)

        self.assertTrue(
            self.text._follow_tail,
            "the pane stayed stuck after its content was cleared",
        )
        self.assertFalse(self.text._follow_button.winfo_ismapped())

    # -- a real writer ---------------------------------------------------

    def test_the_real_transcript_writer_honours_it(self):
        """`_maybe_scroll_transcript_box` is the throttled auto-scroll every
        committed transcript line goes through. Borrowed unmodified."""
        self._fill()
        self._reader_scrolls_to(0.0)
        self.host._transcript_ui_scroll_last_mono = 0.0
        self.host._maybe_scroll_transcript_box(self.text)
        self.root.update()
        self.assertEqual(self.text.yview()[0], 0.0)

        self.host._jump_to_latest(self.text)
        self._fill(lines=20, start=3000)
        self.host._transcript_ui_scroll_last_mono = 0.0
        self.host._maybe_scroll_transcript_box(self.text)
        self.root.update()
        self.assertGreaterEqual(
            self.text.yview()[1], 0.999, "the writer stopped auto-scrolling entirely"
        )


class NoWriterBypassesTheGateTest(unittest.TestCase):
    """Walks the AST rather than grepping.

    A grep would also match the sentence in a comment that explains the fix --
    a mistake this repo has already made four times.
    """

    FILES = (
        PROJECT_ROOT / "alpha" / "ui" / "main_window.py",
        PROJECT_ROOT / "alpha" / "transcription" / "duplicate_protection.py",
    )

    # Pressing the arrow IS the request to go to the bottom, so this one is
    # allowed to scroll unconditionally.
    ALLOWED_IN = {"_jump_to_latest"}

    def _see_end_calls(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        enclosing = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    enclosing.setdefault(id(child), node.name)
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "see"):
                continue
            arg = node.args[0] if node.args else None
            is_end = (
                isinstance(arg, ast.Attribute) and arg.attr == "END"
            ) or (isinstance(arg, ast.Constant) and arg.value == "end")
            if is_end:
                found.append((enclosing.get(id(node), "<module>"), node.lineno))
        return found

    def test_every_pane_writer_goes_through_scroll_to_tail(self):
        offenders = []
        for path in self.FILES:
            for func_name, lineno in self._see_end_calls(path):
                if func_name not in self.ALLOWED_IN:
                    offenders.append("%s:%d in %s" % (path.name, lineno, func_name))
        self.assertEqual(
            offenders,
            [],
            "these still scroll unconditionally and will yank the reader "
            "back: " + ", ".join(offenders),
        )

    def test_the_scan_can_still_see_such_a_call(self):
        """The check above passes trivially if the scan is broken. Plant one."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            planted = Path(tmp) / "planted.py"
            planted.write_text(
                "import tkinter as tk\n\n\ndef writer(box):\n    box.see(tk.END)\n",
                encoding="utf-8",
            )
            self.assertEqual(
                [name for name, _ in self._see_end_calls(planted)],
                ["writer"],
                "the AST scan cannot find a bare see(tk.END) any more",
            )


if __name__ == "__main__":
    unittest.main()
