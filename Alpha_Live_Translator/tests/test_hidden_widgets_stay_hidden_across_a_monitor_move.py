"""A widget hidden with `grid_remove()` must stay hidden when the window changes monitor.

THE REPORT
----------
After dragging the window from the laptop (150 %) to an external monitor (100 %):
the compact ("mobile") layout was "completely broken", and the transcript toggle
misbehaved -- a live report on a large screen had BOTH the Hide and the Show
Transcript buttons on screen at once (recorded as item 81).

THE CAUSE
---------
CustomTkinter 5.2.2 remembers each widget's last geometry call and, when the
monitor's DPI changes, replays it on every widget (`CTkBaseClass._set_scaling`)
so paddings are re-scaled. It forgets that call on `grid_forget`, `pack_forget`
and `place_forget` -- but it does not override `grid_remove`. So a widget hidden
with `grid_remove()` keeps its old `grid(...)` call, and the monitor move replays
it: the widget comes back on screen while the app believes it is hidden, and
nothing in the app's layout re-hides it.

Reproduced on the real AlphaApp through CustomTkinter's own DPI-change path (the
monitor reports 1.0, `check_dpi_scaling` runs every widget's rescale), before the
fix:

    compact, after opening and closing the hamburger menu once:
        menu_dropdown_frame 0->1 and all nine menu controls with it
    every width, transcript shown, after one Hide/Show:
        show_initial_button 0->1   (Hide and Show Transcript both on screen)

and the layout pass a resize runs does not undo either.

WHY THE EARLIER TESTS COULD NOT SEE IT
--------------------------------------
`ctk.set_widget_scaling()` compounds with the display's own factor, so the 100 %
state was judged unreachable on a 150 % laptop and the failure was injected
instead. Driving the tracker's DPI value reaches it directly.

THE FIX
-------
`alpha/ui/ctk_grid_remove_fix.py` gives `grid_remove` the same treatment
CustomTkinter already gives `grid_forget`: forget the replay record. Tk still
remembers the grid options, so `grid()` with no arguments restores the position
exactly as before.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _close(root):
    try:
        for job in root.tk.call("after", "info"):
            try:
                root.after_cancel(job)
            except Exception:
                pass
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


try:
    import customtkinter as ctk
    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

    _close(ctk.CTk())
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    TK_AVAILABLE = False


class _MonitorMoveTestCase(unittest.TestCase):
    """Moves a window to a monitor at another DPI the way CustomTkinter sees it."""

    def setUp(self):
        import alpha.ui.main_window  # noqa: F401  -- the app installs its fixes on import

        self._real_dpi = ScalingTracker.__dict__["get_window_dpi_scaling"]
        self.addCleanup(self._restore_dpi)

    def _restore_dpi(self):
        ScalingTracker.get_window_dpi_scaling = self._real_dpi

    def move_to_monitor(self, window, scale):
        # Patched rather than set once, so CustomTkinter's own polling loop sees
        # the same monitor and does not scale the window straight back.
        ScalingTracker.get_window_dpi_scaling = classmethod(lambda cls, w: scale)
        ScalingTracker.window_dpi_scaling_dict[window] = scale
        window.block_update_dimensions_event()
        ScalingTracker.update_scaling_callbacks_for_window(window)
        window.unblock_update_dimensions_event()

    def other_monitor_scale(self, window):
        current = ScalingTracker.window_dpi_scaling_dict.get(window, 1.0)
        return 1.0 if current != 1.0 else 1.5

    def settle(self, window, rounds=8):
        for _ in range(rounds):
            window.update_idletasks()
            window.update()


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class GridRemoveSurvivesADpiChangeTest(_MonitorMoveTestCase):
    """The mechanism, on plain CustomTkinter widgets."""

    def setUp(self):
        super().setUp()
        self.root = ctk.CTk()
        self.addCleanup(_close, self.root)
        self.root.geometry("400x300")
        self.settle(self.root)

    def test_a_removed_frame_is_not_put_back_by_a_monitor_move(self):
        frame = ctk.CTkFrame(self.root, width=50, height=50)
        frame.grid(row=0, column=0, padx=8)
        self.settle(self.root)
        frame.grid_remove()
        self.settle(self.root)
        self.assertFalse(frame.winfo_ismapped(), "fixture: grid_remove did not hide it")

        self.move_to_monitor(self.root, self.other_monitor_scale(self.root))
        self.settle(self.root)
        self.assertFalse(
            frame.winfo_ismapped(),
            "a widget hidden with grid_remove() came back on screen after the monitor move",
        )

    def test_a_removed_button_is_not_put_back_by_a_monitor_move(self):
        button = ctk.CTkButton(self.root, text="Show Transcript")
        button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        button.grid_remove()
        self.move_to_monitor(self.root, self.other_monitor_scale(self.root))
        self.settle(self.root)
        self.assertFalse(button.winfo_ismapped())

    def test_a_visible_widget_is_still_rescaled_by_a_monitor_move(self):
        """Guard: the replay exists to re-scale padding, and must keep doing it."""
        frame = ctk.CTkFrame(self.root, width=50, height=50)
        frame.grid(row=0, column=0, padx=8)
        scale = self.other_monitor_scale(self.root)
        self.move_to_monitor(self.root, scale)
        self.settle(self.root)
        self.assertTrue(frame.winfo_ismapped())
        self.assertEqual(int(frame.grid_info()["padx"]), round(8 * scale))

    def test_grid_with_no_arguments_still_restores_the_position(self):
        """Guard: items 91d/91e re-show panes with grid() and no arguments."""
        frame = ctk.CTkFrame(self.root, width=50, height=50)
        frame.grid(row=2, column=1, sticky="e")
        frame.grid_remove()
        frame.grid()
        info = frame.grid_info()
        self.assertEqual((int(info["row"]), int(info["column"]), info["sticky"]), (2, 1, "e"))

    def test_a_removed_scrollable_frame_is_not_put_back_either(self):
        scrollable = ctk.CTkScrollableFrame(self.root, width=100, height=60)
        scrollable.grid(row=0, column=0)
        scrollable.grid_remove()
        self.move_to_monitor(self.root, self.other_monitor_scale(self.root))
        self.settle(self.root)
        self.assertFalse(scrollable._parent_frame.winfo_ismapped())


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TheReportedSymptomsTest(_MonitorMoveTestCase):
    """The two things the reporter saw, on the real AlphaApp."""

    def setUp(self):
        super().setUp()
        from alpha.ui.main_window import AlphaApp

        self.app = AlphaApp()
        self.addCleanup(_close, self.app)
        self.app.deiconify()
        self.settle(self.app)

    def _layout_at(self, geometry):
        self.app.geometry(geometry)  # design units
        self.settle(self.app)
        self.app._apply_responsive_layout()
        self.settle(self.app)

    def test_the_hamburger_menu_stays_closed_after_a_monitor_move(self):
        self._layout_at("640x800")
        self.assertTrue(self.app._compact_mode, "fixture: 640 design px is not the compact layout")
        self.app.toggle_hamburger_menu()
        self.settle(self.app)
        self.app._hide_hamburger_menu()
        self.settle(self.app)
        self.assertFalse(self.app.menu_dropdown_frame.winfo_ismapped(), "fixture: the menu did not close")

        self.move_to_monitor(self.app, self.other_monitor_scale(self.app))
        self.settle(self.app)
        self.assertFalse(
            self.app.menu_dropdown_frame.winfo_ismapped(),
            "the closed hamburger menu opened by itself after the monitor move",
        )

    def test_only_one_transcript_button_is_on_screen_after_a_monitor_move(self):
        self._layout_at("1121x650")
        self.app._initial_verse_visible = True
        self.app._sync_transcript_visibility()
        self.settle(self.app)
        self.app.toggle_initial_verse()  # Hide
        self.settle(self.app)
        self.app.toggle_initial_verse()  # Show
        self.settle(self.app)
        self.assertTrue(self.app.hide_initial_button.winfo_ismapped(), "fixture: the transcript is not shown")

        self.move_to_monitor(self.app, self.other_monitor_scale(self.app))
        self.settle(self.app)
        self.assertFalse(
            self.app.show_initial_button.winfo_ismapped(),
            "Show Transcript came back on screen beside Hide after the monitor move",
        )
        self.assertTrue(self.app.hide_initial_button.winfo_ismapped())

    def test_the_toggle_still_works_after_a_monitor_move(self):
        """Guard. No update between a click and its assertion (see item 91)."""
        self._layout_at("1121x650")
        self.app._initial_verse_visible = True
        self.app._sync_transcript_visibility()
        self.settle(self.app)
        self.move_to_monitor(self.app, self.other_monitor_scale(self.app))
        self.settle(self.app)

        self.app.toggle_initial_verse()  # Hide
        self.assertFalse(self.app.transcript_column.winfo_ismapped())
        self.assertTrue(self.app.show_initial_button.winfo_ismapped())
        self.app.toggle_initial_verse()  # Show
        self.assertTrue(self.app.transcript_column.winfo_ismapped())
        self.assertTrue(self.app.hide_initial_button.winfo_ismapped())
        self.assertFalse(self.app.show_initial_button.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
