"""Item 90 -- picking one language dropdown decides the other.

Two languages, and a session never translates one into itself, so source and
target cannot both be the same. The complement lives at the top of
`on_language_change`, which is the single funnel both dropdowns route through.

The one thing worth a test is the termination: setting the other StringVar
fires its own write trace, which calls `on_language_change` again. That
recursion ends because the complement runs ONLY when the two already match,
and the write it performs makes them differ.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import SOURCE_LANGUAGES  # noqa: E402


def _close(root):
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
class TheDropdownsStayOpposite(unittest.TestCase):
    """Drives the real `on_language_change`, with the real write traces.

    The traces are the point: without them the recursion this test exists to
    bound never happens, and the test would pass on code that loops forever in
    the app.
    """

    def setUp(self):
        root = ctk.CTk()
        self.addCleanup(_close, root)
        self.root = root
        self.calls = []

        real = AlphaApp.on_language_change

        def counted(host, changed="both"):
            self.calls.append(changed)
            if len(self.calls) > 20:
                raise RecursionError("on_language_change did not settle")
            return real(host, changed)

        root.on_language_change = types.MethodType(counted, root)
        for name in ("_strip_language_flag", "_sync_language_combo_displays",
                     "_build_language_profile", "_resolve_deepgram_language"):
            setattr(root, name, types.MethodType(getattr(AlphaApp, name), root))
        # Everything past the complement is logging and Deepgram plumbing this
        # test does not exercise; the combos themselves are not built here.
        root.source_combo = None
        root.target_combo = None
        root.source_combo_menu = None
        root.target_combo_menu = None
        root._listen_language = None
        root.translated_title_label = None
        root._update_translation_title = lambda *a, **k: None

        root.source_language = ctk.StringVar(value="Japanese")
        root.target_language = ctk.StringVar(value="English")
        root.source_language.trace_add(
            "write", lambda *a: root.on_language_change("source")
        )
        root.target_language.trace_add(
            "write", lambda *a: root.on_language_change("target")
        )

    def _pair(self):
        return (self.root.source_language.get(), self.root.target_language.get())

    def test_the_two_languages_are_the_only_ones(self):
        """The complement picks 'the other one', which needs there to be one."""
        self.assertEqual(len(SOURCE_LANGUAGES), 2, SOURCE_LANGUAGES)

    def test_setting_source_to_the_target_flips_the_target(self):
        self.root.source_language.set("English")
        self.assertEqual(self._pair(), ("English", "Japanese"))

    def test_setting_target_to_the_source_flips_the_source(self):
        self.root.target_language.set("Japanese")
        self.assertEqual(self._pair(), ("English", "Japanese"))

    def test_it_settles_instead_of_recursing(self):
        """The write fired by the complement must find nothing left to do."""
        self.root.source_language.set("English")
        self.assertLessEqual(
            len(self.calls), 3,
            f"on_language_change ran {len(self.calls)} times: {self.calls}",
        )

    def test_an_already_opposite_pair_is_left_alone(self):
        before = self._pair()
        self.root.on_language_change("source")
        self.assertEqual(self._pair(), before)

    def test_both_is_left_alone(self):
        """`swap_languages` passes 'both' and has already set the pair itself."""
        self.root.source_language.set("English")
        self.calls.clear()
        self.root.on_language_change("both")
        self.assertEqual(self._pair(), ("English", "Japanese"))


if __name__ == "__main__":
    unittest.main()
