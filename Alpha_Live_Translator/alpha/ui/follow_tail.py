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
