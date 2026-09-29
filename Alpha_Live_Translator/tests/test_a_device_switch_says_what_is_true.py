"""A device switch says what is true, and a good outcome is not logged as an error.

WHAT WAS BROKEN (PENDING_TASKS.md open defects m and n, found reviewing 0c)
--------------------------------------------------------------------------
(m) Section 0c added "● Audio device switched" for the moment capture has
    followed Windows to a new default output -- information, the one good
    outcome of item 73's rebind. But `_sync_connection_indicator` published
    every state other than "connected" through `publish_error_event`, and
    `_on_error_occurred` logs each of those at ERROR level. So a successful
    follow wrote `[connection] Windows changed the default audio output...` to
    the log as an error.

(n) Item 73's sentence for the minute before the rebind is confirmed still
    said the session "captures “X” and cannot follow the change, so nothing is
    being recorded now. Stop and start the session to capture the new device,
    or make “X” the default again." Capture follows by itself now
    (`_rebind_wasapi_to_default_device`), and "make X the default again" undoes
    the switch Alpha is making. Being an f-string, it could not be translated
    either: a Japanese screen showed it in English.

WHAT THESE TESTS PIN
--------------------
* a followed device reaches the log as INFO through the real event bus and
  `_on_error_occurred`, never as ERROR; a problem after it is still published
* item 73's sentence says Alpha is switching, names no device to switch back
  to, and is shown in Japanese on a Japanese screen
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.core.event_bus import EventBus  # noqa: E402
from alpha.core.events import EventType  # noqa: E402
from alpha.ui import main_window, strings  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import service_status as ss  # noqa: E402

DEVICE = "Speakers (Realtek(R) Audio)"


class _Host:
    """Real indicator, real publish, real error handler, real bus."""

    _sync_connection_indicator = AlphaApp._sync_connection_indicator
    _CONNECTION_INDICATOR_TEXT = AlphaApp._CONNECTION_INDICATOR_TEXT
    _explain_connection_state = AlphaApp._explain_connection_state
    publish_error_event = AlphaApp.publish_error_event
    _on_error_occurred = AlphaApp._on_error_occurred

    def __init__(self):
        self.signal_label = object()
        self.is_listening = True
        self.translation_worker = None
        self._dg_disconnected_at = 0.0
        self._dg_reconnecting = False
        self._dg_auth_failed = False
        self._dg_credit_exhausted = False
        self._audio_device_changed = False
        self._diag_wasapi_device_name = DEVICE
        self._translation_unavailable_reason = ""
        self.meeting = (0.0, "")
        self.painted = []
        self.modals = []
        self.event_bus = EventBus()
        self.event_bus.subscribe(EventType.ERROR_OCCURRED, self._on_error_occurred)

    def deepgram_gap_seconds(self):
        return 0.0

    def _audio_attention_inputs(self, now_mono=None):
        return (0.0, 0.0)

    def _meeting_audio_inputs(self, now_mono=None):
        return self.meeting

    def _set_dynamic_text(self, label, text, **kwargs):
        self.painted.append(text)

    def _run_on_ui_thread(self, fn):
        self.modals.append(fn)


def _messages(log_method):
    """Each call as the log line it would write (`logger.x(fmt, *args)`)."""
    lines = []
    for call in log_method.call_args_list:
        fmt, *args = call.args
        lines.append(str(fmt) % tuple(args) if args else str(fmt))
    return lines


class AFollowedDeviceIsNotAnErrorTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(main_window, "logger")
        self.logger = patcher.start()
        self.addCleanup(patcher.stop)
        self.host = _Host()

    def test_the_follow_is_logged_as_info(self):
        self.host.meeting = (0.0, DEVICE)
        self.host._sync_connection_indicator()
        self.assertEqual(self.host.painted[-1], "● Audio device switched")
        self.assertEqual(
            _messages(self.logger.error), [], "a successful follow was logged as an error"
        )
        infos = [m for m in _messages(self.logger.info) if DEVICE in m]
        self.assertEqual(len(infos), 1, "the follow left no trace in the log at all")
        self.assertEqual(self.host.modals, [])

    def test_once_per_transition_not_per_tick(self):
        self.host.meeting = (0.0, DEVICE)
        for _ in range(5):
            self.host._sync_connection_indicator()
        infos = [m for m in _messages(self.logger.info) if DEVICE in m]
        self.assertEqual(len(infos), 1)

    def test_a_problem_after_the_follow_is_still_published(self):
        """The new device is silent while the room talks: section 0c's hint."""
        self.host.meeting = (0.0, DEVICE)
        self.host._sync_connection_indicator()
        self.host.meeting = (95.0, DEVICE)
        self.host._sync_connection_indicator()
        self.assertEqual(self.host.painted[-1], "● Meeting audio silent")
        errors = [m for m in _messages(self.logger.error) if "[connection]" in m]
        self.assertEqual(len(errors), 1, "a real problem stopped reaching the error surface")
        self.assertIn(DEVICE, errors[0])

    def test_a_device_change_is_still_published(self):
        self.host._audio_device_changed = True
        self.host._sync_connection_indicator()
        errors = [m for m in _messages(self.logger.error) if "[connection]" in m]
        self.assertEqual(len(errors), 1)
        self.assertIn("default audio output", errors[0])


class TheSwitchingSentenceTest(unittest.TestCase):
    def _status(self, **kw):
        kw.setdefault("listening", True)
        kw.setdefault("deepgram_connected", True)
        kw.setdefault("audio_device_changed", True)
        return ss.describe_connection(**kw)

    def test_it_says_alpha_is_switching(self):
        for device in (DEVICE, ""):
            with self.subTest(device=device):
                s = self._status(audio_capture_device=device)
                self.assertEqual(s.state, ss.RECONNECTING)
                self.assertEqual(s.message, ss.AUDIO_DEVICE_CHANGING_TEXT)
                self.assertNotIn("cannot follow", s.message)
                self.assertNotIn("default again", s.message)
                self.assertNotIn("“”", s.message)
                self.assertIn("stop and start the session", s.message)

    def test_it_names_no_device_to_go_back_to(self):
        s = self._status(audio_capture_device=DEVICE)
        self.assertNotIn(DEVICE, s.message)

    def test_it_is_one_translatable_literal(self):
        self.assertIn(ss.AUDIO_DEVICE_CHANGING_TEXT, strings._TABLES["ja"])

    def test_a_japanese_screen_shows_it_in_japanese(self):
        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)
        strings.set_language("ja")
        with mock.patch.object(main_window, "logger"):
            host = _Host()
            host._audio_device_changed = True
            host._sync_connection_indicator()
            with mock.patch.object(main_window, "messagebox") as box:
                host._explain_connection_state()
        message = box.showinfo.call_args[0][1]
        self.assertEqual(message, strings._JA[ss.AUDIO_DEVICE_CHANGING_TEXT])


if __name__ == "__main__":
    unittest.main()
