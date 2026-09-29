"""A meeting playing somewhere Alpha is not listening is named, with the device.

WHAT WAS BROKEN (PENDING_TASKS.md section 0c, the owner's meetings of 2026-09-28)
------------------------------------------------------------------------------
`...101440` captured the laptop microphone for 15 minutes and the meeting for
none of them: the Realtek loopback Alpha records was exact digital zero from
start to Stop, because the meeting app played on another device. In
`...100031` the Windows default output moved from a Bluetooth headset to
Realtek at 10:11:26; Alpha followed it (rebind OK, 0.26 s) -- and nothing was
playing there either.

Item 31's "● No sound" could not say so: it fires only when BOTH tracks are
silent, and the microphone was hearing the room. And following a device change
cleared item 73's warning back to "● Signal OK" without a word about which
device Alpha now records.

WHAT THESE TESTS PIN
--------------------
* the meeting track silent for `MEETING_AUDIO_SILENT_HINT_AFTER_S` while the
  microphone hears sound -> "● Meeting audio silent", naming the device Alpha
  records, with the sentence translatable (a template, not an f-string)
* not while the microphone is silent too (item 31's no-sound owns that), not
  before the threshold, and sound on the meeting track resets it
* a followed device change is announced for `AUDIO_DEVICE_FOLLOWED_NOTICE_S`,
  naming the new device, and restarts the silence clock
* the severities: a reconnect or a failure outranks the hint, the hint
  outranks a degraded translation, and the notice outranks nothing
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import (  # noqa: E402
    AUDIO_DEVICE_FOLLOWED_NOTICE_S,
    MEETING_AUDIO_NEVER_HEARD_HINT_AFTER_S,
    MEETING_AUDIO_SILENT_HINT_AFTER_S,
)
from alpha.ui import strings  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import service_status as ss  # noqa: E402

DEVICE = "Speakers (Realtek(R) Audio)"


def status(**kw):
    kw.setdefault("listening", True)
    kw.setdefault("deepgram_connected", True)
    return ss.describe_connection(**kw)


class TheStateAndItsWordsTest(unittest.TestCase):
    def test_a_silent_meeting_track_names_the_device(self):
        s = status(meeting_audio_silent_seconds=50.0, audio_capture_device=DEVICE)
        self.assertEqual(s.state, ss.NO_MEETING_AUDIO)
        self.assertIn(DEVICE, s.message)
        self.assertIn("50", s.message)
        self.assertEqual(s.detail["message_template"], ss.MEETING_AUDIO_SILENT_TEXT)
        self.assertEqual(s.detail["message_args"]["device"], DEVICE)

    def test_without_a_device_name_it_still_says_what_to_do(self):
        s = status(meeting_audio_silent_seconds=50.0)
        self.assertEqual(s.state, ss.NO_MEETING_AUDIO)
        self.assertEqual(s.detail["message_template"], ss.MEETING_AUDIO_SILENT_NO_DEVICE_TEXT)

    def test_not_before_the_threshold(self):
        s = status(meeting_audio_silent_seconds=MEETING_AUDIO_SILENT_HINT_AFTER_S - 1)
        self.assertEqual(s.state, ss.CONNECTED)

    def test_a_reconnect_outranks_it(self):
        s = status(meeting_audio_silent_seconds=50.0, deepgram_reconnecting=True)
        self.assertEqual(s.state, ss.RECONNECTING)

    def test_it_outranks_a_degraded_translation(self):
        s = status(meeting_audio_silent_seconds=50.0, translation_degraded=True)
        self.assertEqual(s.state, ss.NO_MEETING_AUDIO)

    def test_a_followed_device_is_announced(self):
        s = status(audio_device_followed=DEVICE)
        self.assertEqual(s.state, ss.DEVICE_FOLLOWED)
        self.assertIn(DEVICE, s.message)
        self.assertEqual(s.detail["message_template"], ss.AUDIO_DEVICE_FOLLOWED_TEXT)

    def test_the_notice_outranks_nothing(self):
        s = status(audio_device_followed=DEVICE, translation_degraded=True)
        self.assertEqual(s.state, ss.DEGRADED)
        s = status(audio_device_followed=DEVICE, meeting_audio_silent_seconds=50.0)
        self.assertEqual(s.state, ss.NO_MEETING_AUDIO)

    def test_the_sentences_are_translated(self):
        ja = strings._TABLES["ja"]
        for key in (
            ss.MEETING_AUDIO_SILENT_TEXT,
            ss.MEETING_AUDIO_SILENT_NO_DEVICE_TEXT,
            ss.AUDIO_DEVICE_FOLLOWED_TEXT,
            "● Meeting audio silent",
            "● Audio device switched",
        ):
            self.assertIn(key, ja, f"no Japanese for: {key[:40]}")
        # The template keeps its placeholders in Japanese, so it can be filled.
        self.assertIn("{device}", ja[ss.MEETING_AUDIO_SILENT_TEXT])
        self.assertIn("{seconds}", ja[ss.MEETING_AUDIO_SILENT_TEXT])
        self.assertIn("{device}", ja[ss.AUDIO_DEVICE_FOLLOWED_TEXT])


class _Host:
    _note_audio_activity = AlphaApp._note_audio_activity
    _meeting_audio_inputs = AlphaApp._meeting_audio_inputs

    def __init__(self):
        self._listening_started_mono = 1000.0
        self._last_any_sound_mono = 0.0
        self._voiced_seconds_total = 0.0
        self._diag_wasapi_device_name = DEVICE

    def frames(self, start, end, *, sys_rms, mic_rms):
        t = start
        while t < end:
            self._note_audio_activity(
                {"sys_rms": sys_rms, "mic_rms": mic_rms, "system_active": False, "mic_active": mic_rms > 80},
                now_mono=1000.0 + t,
            )
            t += 0.5

    def inputs(self, at):
        return self._meeting_audio_inputs(now_mono=1000.0 + at)


class TheSignalsFromTheMixerTest(unittest.TestCase):
    def test_meeting_track_zero_while_the_mic_hears_the_room(self):
        """`...101440`: loopback exact zero for the whole meeting, laptop mic live.
        Never heard since Start, so the longer start-up grace applies."""
        host = _Host()
        host.frames(0, 50, sys_rms=0.0, mic_rms=600.0)
        self.assertEqual(host.inputs(50)[0], 0.0, "remote people may not have spoken yet")
        host.frames(50, MEETING_AUDIO_NEVER_HEARD_HINT_AFTER_S + 1, sys_rms=0.0, mic_rms=600.0)
        silent, followed = host.inputs(MEETING_AUDIO_NEVER_HEARD_HINT_AFTER_S + 1)
        self.assertGreaterEqual(silent, MEETING_AUDIO_NEVER_HEARD_HINT_AFTER_S)
        self.assertEqual(followed, "")

    def test_a_dropout_is_named_sooner(self):
        """`...100031` after 10:11:41: the meeting WAS heard, then went silent."""
        host = _Host()
        host.frames(0, 5, sys_rms=700.0, mic_rms=600.0)
        host.frames(5, 5 + MEETING_AUDIO_SILENT_HINT_AFTER_S + 1, sys_rms=0.0, mic_rms=600.0)
        self.assertGreaterEqual(
            host.inputs(5 + MEETING_AUDIO_SILENT_HINT_AFTER_S + 1)[0], MEETING_AUDIO_SILENT_HINT_AFTER_S
        )

    def test_once_raised_a_pause_in_the_room_does_not_clear_it(self):
        """Replaying `...101440` the first version flickered off at every 10 s
        gap in the room's talk -- the meeting had not come back."""
        host = _Host()
        host.frames(0, 5, sys_rms=700.0, mic_rms=600.0)
        host.frames(5, 60, sys_rms=0.0, mic_rms=600.0)
        self.assertGreater(host.inputs(60)[0], 0.0)
        host.frames(60, 90, sys_rms=0.0, mic_rms=0.0)  # the room pauses for 30 s
        self.assertGreater(host.inputs(90)[0], 0.0)
        host.frames(90, 91, sys_rms=700.0, mic_rms=0.0)  # the meeting is heard again
        self.assertEqual(host.inputs(91)[0], 0.0)

    def test_both_silent_is_not_this_hint(self):
        host = _Host()
        host.frames(0, 5, sys_rms=700.0, mic_rms=0.0)
        host.frames(5, 5 + MEETING_AUDIO_NEVER_HEARD_HINT_AFTER_S + 5, sys_rms=0.0, mic_rms=0.0)
        self.assertEqual(
            host.inputs(5 + MEETING_AUDIO_NEVER_HEARD_HINT_AFTER_S + 5)[0],
            0.0,
            "item 31's no-sound owns total silence",
        )

    def test_meeting_sound_resets_the_clock(self):
        host = _Host()
        host.frames(0, 30, sys_rms=0.0, mic_rms=600.0)
        host.frames(30, 30.5, sys_rms=900.0, mic_rms=600.0)
        host.frames(30.5, 50, sys_rms=0.0, mic_rms=600.0)
        self.assertLess(host.inputs(50)[0], MEETING_AUDIO_SILENT_HINT_AFTER_S)

    def test_a_followed_change_is_announced_then_ends_and_restarts_the_clock(self):
        """`...100031`: 10:11:26, Bluetooth headset -> Realtek, rebind OK."""
        host = _Host()
        host.frames(0, 5, sys_rms=700.0, mic_rms=600.0)  # the old device was heard
        host.frames(5, 40, sys_rms=0.0, mic_rms=600.0)
        host._audio_device_followed_mono = 1000.0 + 40
        host._audio_device_followed_name = "Realtek HD Audio"
        host.frames(40, 60, sys_rms=0.0, mic_rms=600.0)
        silent, followed = host.inputs(60)
        self.assertEqual(followed, "Realtek HD Audio")
        self.assertEqual(
            silent,
            0.0,
            "silence counts from the NEW device (55 s since the old one was "
            "heard, but only 20 s on this one, which gets the start-up grace)",
        )
        host.frames(60, 40 + AUDIO_DEVICE_FOLLOWED_NOTICE_S + 1, sys_rms=0.0, mic_rms=600.0)
        self.assertEqual(host.inputs(40 + AUDIO_DEVICE_FOLLOWED_NOTICE_S + 1)[1], "")


class TheRealRebindRecordsTheDeviceTest(unittest.TestCase):
    """Through the real `_rebind_wasapi_to_default_device`, on the host
    `test_a_rebind_proves_audio_resumed.py` uses (close/start stubbed)."""

    def _host(self, **kw):
        tests_dir = str(Path(__file__).resolve().parent)
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        from test_a_rebind_proves_audio_resumed import _RebindHost

        return _RebindHost(**kw)

    def test_a_confirmed_rebind_names_the_new_device(self):
        host = self._host(produces_audio=True)
        self.assertTrue(host._rebind_wasapi_to_default_device())
        self.assertEqual(host._audio_device_followed_name, "Speakers")
        self.assertGreater(host._audio_device_followed_mono, 0.0)

    def test_a_silent_rebind_announces_nothing(self):
        host = self._host(produces_audio=False)
        self.assertFalse(host._rebind_wasapi_to_default_device())
        self.assertFalse(getattr(host, "_audio_device_followed_mono", 0.0))


class TheIndicatorHasALabelForBothTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(AlphaApp._CONNECTION_INDICATOR_TEXT[ss.NO_MEETING_AUDIO][0], "● Meeting audio silent")
        self.assertEqual(AlphaApp._CONNECTION_INDICATOR_TEXT[ss.DEVICE_FOLLOWED][0], "● Audio device switched")


if __name__ == "__main__":
    unittest.main()
