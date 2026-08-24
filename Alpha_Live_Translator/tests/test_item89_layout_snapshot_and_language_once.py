"""Item 89 -- two things the 2nd-PC log bundle exposed.

1. **A layout complaint could not be handed over.** The bundle carried no
   geometry at all: grep it for a window size, a screen size, a scaling factor
   or a layout mode and there is nothing. So a user looking straight at a
   broken header had no way to show it, and this end could only re-measure
   widths it already believed were fine. `_record_layout_snapshot` writes the
   numbers into the run's evidence instead.

2. **Every language change ran its handler twice.** `_make_language_combo`'s
   `on_select` called `on_language_change` explicitly AND wrote to the
   StringVar, whose write trace calls it too -- and a Tk trace fires
   synchronously inside `set`. Visible in that same console log as paired
   `LANGUAGE_DROPDOWN_CHANGED` lines, one user action apiece.
"""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# The header host lives beside this file. Discovery imports these as
# `tests.<name>`, which does not put this directory on the path, so borrowing a
# sibling's fixture needs it added explicitly.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _close(root):
    """Destroy a CTk root without leaving its own `after` jobs armed."""
    try:
        for job in root.tk.call("after", "info"):
            try:
                root.after_cancel(job)
            except Exception:
                pass
    except Exception:
        pass
    root.destroy()


try:
    import customtkinter as ctk

    _close(ctk.CTk())
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    TK_AVAILABLE = False

if TK_AVAILABLE:
    from alpha.ui.main_window import AlphaApp


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TheLanguageHandlerRunsOncePerChange(unittest.TestCase):
    """Both routes into it, each asserted separately.

    The bug lived in the branch where the value actually changes, so a test
    that only re-picked the current value would have passed against the broken
    code. The host also has to register the production write traces -- they are
    set up in `AlphaApp.__init__`, which a header host does not run, and
    without them the double-call is invisible.
    """

    def _host(self):
        header = __import__("test_item71_header_responsive")
        root = header._build_header_host(1200)
        self.addCleanup(_close, root)
        self.calls = []
        root.on_language_change = lambda changed="both": self.calls.append(changed)
        root.source_language.trace_add(
            "write", lambda *_: root.on_language_change("source")
        )
        root.target_language.trace_add(
            "write", lambda *_: root.on_language_change("target")
        )
        for _ in range(3):
            root.update_idletasks()
            root.update()
        return root

    def test_picking_a_different_language_fires_once(self):
        root = self._host()
        self.assertEqual(root.source_language.get(), "Japanese")
        self.calls.clear()
        root.source_combo.cget("command")("English")
        self.assertEqual(
            self.calls, ["source"],
            "one selection must run the handler exactly once; it used to run "
            "twice because the write trace and on_select both called it",
        )
        self.assertEqual(root.source_language.get(), "English")

    def test_repicking_the_same_language_still_fires_once(self):
        """Writing an unchanged value fires no trace, so on_select has to."""
        root = self._host()
        self.calls.clear()
        root.source_combo.cget("command")("Japanese")
        self.assertEqual(self.calls, ["source"])


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class TheLayoutSnapshotRecordsWhatIsOnScreen(unittest.TestCase):
    SNAPSHOT_METHODS = ("_record_layout_snapshot", "_design_width")

    def _host(self, design_width=1200):
        header = __import__("test_item71_header_responsive")
        root = header._build_header_host(design_width)
        self.addCleanup(_close, root)
        for name in self.SNAPSHOT_METHODS:
            setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
        root._SNAPSHOT_CONTROLS = AlphaApp._SNAPSHOT_CONTROLS
        root._font_cache = {}
        for _ in range(4):
            root.update_idletasks()
            root.update()
        return root

    def _capture(self, root, mode="wide"):
        written = []
        with mock.patch(
            "alpha.utils.evidence_jsonl.append_jsonl_named",
            side_effect=lambda category, name, payload: written.append(
                (category, name, payload)
            ),
        ):
            root._record_layout_snapshot(mode)
        return written

    def test_it_writes_one_row_under_a_registered_name(self):
        """The name has to be a key in `_HEALTH_NAME_MAP`, not a filename.

        `get_health_path` raises KeyError for an unregistered name, and the
        method's own guard swallows it -- which is exactly how the first
        version of this silently wrote nothing at all.
        """
        from alpha.utils.troubleshooting_paths import _HEALTH_NAME_MAP

        written = self._capture(self._host())
        self.assertEqual(len(written), 1)
        category, name, _ = written[0]
        self.assertEqual(category, "health")
        self.assertIn(name, _HEALTH_NAME_MAP)

    def test_it_records_what_a_layout_complaint_is_ever_about(self):
        written = self._capture(self._host(), mode="wide")
        payload = written[0][2]
        self.assertEqual(payload["mode"], "wide")
        for key in ("design_width", "scaling", "ui_language", "screen", "window"):
            self.assertIn(key, payload)
        for key in ("width", "height"):
            self.assertIn(key, payload["screen"])
        for key in ("device_width", "device_height", "x", "y", "state"):
            self.assertIn(key, payload["window"])

    def test_every_control_reports_whether_it_fits(self):
        payload = self._capture(self._host())[0][2]
        controls = payload["controls"]
        self.assertIn("summary_button", controls)
        self.assertIn("ui_language_button", controls)
        for name, control in controls.items():
            self.assertIn("mapped", control, name)
            self.assertIn("w", control, name)
            self.assertIn("req", control, name)
            if control["mapped"]:
                self.assertIn(
                    "past_edge", control,
                    f"{name} is on screen, so how far past its container it "
                    "sits is the whole point of recording it",
                )

    def test_the_payload_survives_json(self):
        """It is written as JSONL; a value json cannot encode writes nothing."""
        payload = self._capture(self._host())[0][2]
        self.assertEqual(json.loads(json.dumps(payload))["mode"], "wide")

    def test_it_never_raises(self):
        """A diagnostic that can break the UI it diagnoses is worse than none."""
        root = self._host()
        with mock.patch(
            "alpha.utils.evidence_jsonl.append_jsonl_named",
            side_effect=RuntimeError("disk full"),
        ):
            root._record_layout_snapshot("wide")  # must not propagate


if __name__ == "__main__":
    unittest.main()
