"""Item 88e -- a language switch must not disturb anything but text.

The review that produced these found the language control reaching into live
session state. Each test below is a measured failure, not a hypothetical:

1. Switching language mid-meeting **reset the session clock to 00:00**.
   `_retranslate_ui` repainted the primary button through
   `_set_listen_button_state`, which calls `_update_status_bar`, which sets
   `_listen_start_time = time.time()`. Measured at 600 s elapsed before the
   switch and 0 s after.

2. The same call forced `state="normal"`, so switching language while the
   button was deliberately disabled during "Starting…" re-enabled it and
   replaced the transitional wording -- offering a second Start while one was
   already in flight.

3. Every open of the header language menu built a fresh `Menu(self)` that was
   never destroyed, leaving one Tk widget on the root per click.

4. Menu labels came from `LANGUAGE_NAMES[code]` while the codes came from
   `_TABLES`. A language in one and not the other raised inside the build loop,
   and the surrounding guard turned that into "the menu silently never opens".

5. `save_language` applies nothing when its write fails, but the dialog said
   the language had changed. On a read-only install the click did nothing at
   all while insisting otherwise.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import strings  # noqa: E402


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


@unittest.skipUnless(TK_AVAILABLE, "Tk display unavailable in this environment")
class LanguageSwitchTestCase(unittest.TestCase):
    """Drives the real AlphaApp.

    A host built from borrowed methods cannot show any of this: the bugs live
    in what the repaint path calls, not in what it says.
    """

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)
        strings.set_language("en")
        self.app = AlphaApp()
        self.addCleanup(self._destroy)
        self.app.deiconify()
        self.app.update()

    def _destroy(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def _switch_to_japanese(self):
        strings.set_language("ja")
        self.app._retranslate_ui()
        self.app.update()


class TestTheSessionSurvivesASwitch(LanguageSwitchTestCase):
    def test_the_meeting_clock_is_not_reset(self):
        """The one that would have been noticed in a real meeting."""
        self.app.is_listening = True
        self.app._listen_start_time = time.time() - 600
        self.app.update()

        self._switch_to_japanese()

        elapsed = time.time() - self.app._listen_start_time
        self.assertGreater(
            elapsed, 500,
            f"the session clock restarted: {elapsed:.0f}s elapsed, expected ~600",
        )

    def test_a_disabled_primary_button_stays_disabled(self):
        self.app._set_dynamic_text(
            self.app.listen_button, "Starting…", state="disabled"
        )
        self.app.update()
        self.assertEqual(str(self.app.listen_button.cget("state")), "disabled")

        self._switch_to_japanese()

        self.assertEqual(
            str(self.app.listen_button.cget("state")), "disabled",
            "a language switch re-enabled a button that was deliberately off",
        )

    def test_the_transitional_wording_is_translated_not_replaced(self):
        """"Starting…" has to become the Japanese "Starting…", not revert to
        "Start Listening" -- which is what reading the button back and guessing
        would have produced."""
        self.app._set_dynamic_text(
            self.app.listen_button, "Starting…", state="disabled"
        )
        self.app.update()

        self._switch_to_japanese()

        self.assertEqual(self.app.listen_button.cget("text"), strings.t("Starting…"))
        self.assertNotEqual(self.app.listen_button.cget("text"), strings.t("Start Listening"))


class TestDynamicLabelsCarryTheirSource(LanguageSwitchTestCase):
    def test_every_dynamic_widget_records_where_its_text_came_from(self):
        """Runtime text cannot be re-translated from what is on screen, so the
        English source has to travel with the widget."""
        for attr in self.app._DYNAMIC_TEXT_WIDGETS:
            widget = getattr(self.app, attr, None)
            if widget is None:
                continue
            with self.subTest(widget=attr):
                self.assertTrue(
                    getattr(widget, "_alpha_text_source", None),
                    f"{attr} carries no source, so it can never be retranslated",
                )

    def test_a_widget_with_no_source_is_left_alone(self):
        label = ctk.CTkLabel(self.app, text="untouched")
        self.app.spare_label = label
        self.addCleanup(lambda: setattr(self.app, "spare_label", None))
        try:
            self.app._DYNAMIC_TEXT_WIDGETS = tuple(
                self.app._DYNAMIC_TEXT_WIDGETS
            ) + ("spare_label",)
            strings.set_language("ja")
            self.app._retranslate_dynamic_labels()
            self.assertEqual(label.cget("text"), "untouched")
        finally:
            del self.app._DYNAMIC_TEXT_WIDGETS


class TestTheLanguageMenu(LanguageSwitchTestCase):
    def _open(self):
        """Open without posting. `tk_popup` blocks on a real grab."""
        with mock.patch.object(type(self.app).__mro__[0], "_noop", create=True):
            pass
        import tkinter

        with mock.patch.object(tkinter.Menu, "tk_popup", lambda *a, **k: None):
            self.app._open_ui_language_menu()
        self.app.update()
        return self.app.ui_language_menu

    def test_the_menu_widget_is_reused(self):
        first = self._open()
        second = self._open()
        self.assertIs(first, second, "a new Tk menu was built for the second open")

    def _entry_labels(self, menu):
        """Labels only. `entrycget` raises on the separator, which has none."""
        out = []
        for index in range(menu.index("end") + 1):
            try:
                out.append(menu.entrycget(index, "label"))
            except Exception:
                continue
        return out

    def test_reopening_does_not_duplicate_entries(self):
        first = self._entry_labels(self._open())
        second = self._entry_labels(self._open())
        self.assertEqual(second, first, "entries accumulated across opens")
        # "System default" plus one per language.
        self.assertEqual(len(second), len(strings.available_languages()) + 1)

    def test_exactly_one_entry_is_ever_marked(self):
        labels = self._entry_labels(self._open())
        marked = [label for label in labels if label.startswith("•")]
        self.assertEqual(len(marked), 1, f"expected exactly one marked entry in {labels}")

    def test_a_language_without_a_display_name_still_opens(self):
        """The codes and the names come from two places; they can disagree."""
        with mock.patch.dict(strings._TABLES, {"zz": {}}, clear=False):
            labels = self._entry_labels(self._open())
        self.assertTrue(
            any("zz" in label for label in labels),
            f"the nameless language was dropped instead of shown: {labels}",
        )


class TestAFailedSaveStillSwitches(LanguageSwitchTestCase):
    def test_the_session_language_changes_even_if_it_cannot_be_written(self):
        """Otherwise the click does nothing while the dialog says it did."""
        import alpha.ui.main_window as mw

        with mock.patch.object(mw, "save_language", return_value=False), \
                mock.patch.object(mw.messagebox, "showwarning") as warned:
            self.app._apply_ui_language("ja")

        self.assertEqual(strings.get_language(), "ja")
        self.assertTrue(warned.called, "the failure to save was not reported")

    def test_a_successful_save_says_nothing(self):
        import alpha.ui.main_window as mw

        with mock.patch.object(mw, "save_language", return_value=True), \
                mock.patch.object(mw.messagebox, "showwarning") as warned:
            self.app._apply_ui_language("ja")

        self.assertFalse(warned.called, "a successful save should be silent")


class TestSystemDefaultIsReachable(LanguageSwitchTestCase):
    """There has to be a way back to "whatever Windows says".

    A saved choice outranks the operating system on purpose -- without that,
    picking a language would be undone on the next launch. The cost is that
    anyone who tries the control once is stuck with it: the user hit exactly
    this, an English Windows showing a Japanese app, because they had clicked
    日本語 to see whether the switch worked. Deleting a JSON file by hand is not
    an answer, so the control now offers "System default".
    """

    def setUp(self):
        super().setUp()
        # Redirected before anything writes: `clear_saved_language` and
        # `save_language` both touch APP_ROOT, and a test has no business
        # rewriting the developer's own settings.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        real_root = strings.APP_ROOT
        strings.APP_ROOT = self._tmp.name
        self.addCleanup(setattr, strings, "APP_ROOT", real_root)

    def _open(self):
        import tkinter

        with mock.patch.object(tkinter.Menu, "tk_popup", lambda *a, **k: None):
            self.app._open_ui_language_menu()
        self.app.update()
        return self.app.ui_language_menu

    def _labels(self, menu):
        out = []
        for index in range(menu.index("end") + 1):
            try:
                out.append(menu.entrycget(index, "label"))
            except Exception:
                out.append("<separator>")
        return out

    def test_choosing_it_forgets_the_saved_language(self):
        strings.save_language("ja")
        self.assertTrue(strings.has_saved_language())

        self.app._use_system_language()

        self.assertFalse(
            strings.has_saved_language(),
            "the saved choice survived, so Windows still does not decide",
        )
        self.assertEqual(
            strings.get_language(), strings._windows_ui_language() or "en",
            "after clearing, the language has to come from the system",
        )

    def test_clearing_keeps_other_settings_in_the_file(self):
        """The file is shared, so dropping one key must not drop the rest."""
        strings.save_language("ja")
        path = Path(strings.settings_path())
        data = json.loads(path.read_text(encoding="utf-8"))
        data["something_else"] = 42
        path.write_text(json.dumps(data), encoding="utf-8")

        strings.clear_saved_language()

        left = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(left.get("something_else"), 42)
        self.assertNotIn(strings.SETTINGS_KEY, left)

    def test_the_menu_marks_system_default_when_nothing_is_chosen(self):
        strings.clear_saved_language()
        labels = self._labels(self._open())
        self.assertTrue(
            labels[0].startswith("•"),
            f"System default is not marked as active: {labels}",
        )
        self.assertIn(strings.t("System default"), labels[0])
        for label in labels[1:]:
            self.assertFalse(
                label.startswith("•"),
                f"a language is marked while the system decides: {labels}",
            )

    def test_the_menu_marks_the_language_once_one_is_chosen(self):
        strings.save_language("ja")
        labels = self._labels(self._open())
        self.assertFalse(labels[0].startswith("•"), f"{labels}")
        marked = [label for label in labels[1:] if label.startswith("•")]
        self.assertEqual(len(marked), 1, f"{labels}")
        self.assertIn(strings.LANGUAGE_NAMES["ja"], marked[0])

    def test_the_combo_shows_system_default_when_nothing_is_chosen(self):
        strings.clear_saved_language()
        self.app._sync_ui_language_controls()
        self.assertEqual(
            self.app.ui_language_combo_menu.get(), strings.t("System default")
        )

    def test_the_combo_entry_is_translated_with_everything_else(self):
        strings.save_language("ja")
        self.app._sync_ui_language_controls()
        self.assertIn(
            strings.t("System default"),
            self.app.ui_language_combo_menu.cget("values"),
        )
        self.assertNotIn("System default", self.app.ui_language_combo_menu.cget("values"))

    def test_picking_it_from_the_combo_works_too(self):
        strings.save_language("ja")
        self.app._on_ui_language_combo(strings.t("System default"))
        self.assertFalse(strings.has_saved_language())


if __name__ == "__main__":
    unittest.main()
