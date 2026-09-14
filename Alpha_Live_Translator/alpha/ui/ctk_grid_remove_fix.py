"""Make `grid_remove()` survive a monitor DPI change in CustomTkinter.

CustomTkinter remembers each widget's last geometry-manager call and replays it
whenever the monitor's DPI changes (`CTkBaseClass._set_scaling`), so paddings
are re-scaled. It forgets that call on `grid_forget`, `pack_forget` and
`place_forget` -- but it does not override `grid_remove`, which it inherits
straight from tkinter. A widget hidden with `grid_remove()` therefore keeps its
last `grid(...)` call, and moving the window to a monitor at another scale
replays it: the widget comes back on screen while the app believes it is hidden.

That is the external-monitor report. After the move the closed hamburger menu
opened by itself with all nine of its controls, and "Show Transcript" appeared
beside "Hide" -- and no layout pass re-hides either, because each owner already
believes its widget is hidden.

The fix gives `grid_remove` exactly the treatment CustomTkinter already gives
`grid_forget`. Tk itself still remembers the grid options of a removed widget,
so `grid()` with no arguments restores its position as before.

Measured against CustomTkinter 5.2.2 (the version pinned in
requirements-lock.txt). `install()` does nothing if a later CustomTkinter defines
`grid_remove` on the base class itself.
"""

from __future__ import annotations


def install() -> bool:
    """Patch `CTkBaseClass.grid_remove` once. Returns True only if this call patched it."""
    try:
        from customtkinter.windows.widgets.core_widget_classes.ctk_base_class import CTkBaseClass
    except Exception:
        return False
    if "grid_remove" in CTkBaseClass.__dict__:
        return False  # already installed, or fixed in CustomTkinter itself

    def grid_remove(self):
        """Unmap this widget, remembering its grid options -- and forget the replay.

        Mirrors `CTkBaseClass.grid_forget`. Without this, a DPI change replays the
        widget's last `grid(...)` call and puts it back on screen.
        """
        self._last_geometry_manager_call = None
        return super(CTkBaseClass, self).grid_remove()

    grid_remove._alpha_grid_remove_fix = True
    CTkBaseClass.grid_remove = grid_remove
    return True
