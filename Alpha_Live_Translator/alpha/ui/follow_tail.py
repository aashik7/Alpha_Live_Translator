"""Follow-the-tail scrolling for the live reading panes.

A pane scrolls itself to the newest line only while it is already sitting at
the bottom. Once the reader scrolls up, it stops following until they either
scroll back down or press the floating jump-to-latest arrow.

This lives in its own module because both writers of the transcript pane need
it: `alpha.ui.main_window` and the `DuplicateProtectionMixin` in
`alpha.transcription.duplicate_protection`. Importing one from the other would
make a cycle; this module imports nothing but tkinter.
"""

import tkinter as tk

# `yview()` returns (first, last) as fractions of the total content. `last` is
# 1.0 with the final line resting on the bottom edge; the epsilon absorbs the
# rounding Tk does when a display line is only partly visible.
FOLLOW_TAIL_BOTTOM_EPS = 0.999


def capture_reader_position(box):
    """Remember where a scrolled-up reader is, for restoring after a rebuild.

    Returns a zero-argument restore callable, or `None` when there is nothing
    worth restoring.

    `_render_transcript_from_store_now` rebuilds the pane through
    `_insert_formatted_text`, which begins with `delete("1.0", "end")`. That
    wipe loses the reader's position: `scroll_to_tail` then correctly declines
    to jump them to the bottom, but nothing puts them back where they were, so
    they land at the TOP of a pane they had scrolled into the middle of.

    Returns `None` when the reader is following the tail -- `scroll_to_tail`
    already does the right thing for them, and restoring a remembered index
    would fight it -- and when the widget cannot answer, which keeps a rebuild
    working on a test fake or a half-torn-down window.

    A plain function for the same reason as `scroll_to_tail`: its caller is a
    method on `AlphaApp`, and tests borrow those onto bare hosts.
    """
    if box is None:
        return None
    try:
        if getattr(box, "_follow_tail", True):
            return None
        # The line currently at the top of the viewport, not a fraction: the
        # rebuild can change the content length, and a fraction would then map
        # to a different line.
        top = box.index("@0,0")
    except Exception:
        return None

    def _restore():
        try:
            box.see(top)
        except Exception:
            pass

    return _restore


def scroll_to_tail(box):
    """Scroll `box` to its last line unless the reader has scrolled up in it.

    Deliberately a plain function rather than a method. Its call sites live on
    `AlphaApp` and on `DuplicateProtectionMixin`, and a dozen tests borrow
    those methods onto bare host classes with no UI helpers at all, so a
    `self.` call would raise `AttributeError` on every one of them.

    Reading the flag off the widget with a `True` default has the same effect
    in the other direction: any widget that never opted in -- a test fake, the
    summary pane, a pane added later -- keeps exactly the unconditional
    `see(tk.END)` behaviour it has today.
    """
    if box is None:
        return
    try:
        if not getattr(box, "_follow_tail", True):
            return
        box.see(tk.END)
    except Exception:
        pass
