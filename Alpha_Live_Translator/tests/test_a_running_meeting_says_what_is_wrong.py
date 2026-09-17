"""A meeting that is running but not working says so, in the operator's language.

Item 30 covered everything that stops a meeting. This covers what leaves it
running while something is wrong -- the reports that read "Alpha is not
working" although nothing crashed:

1. **No sound at all.** The meeting's audio plays on a device this session does
   not capture. Item 73 measured that shape: EXACT digital silence, rms 0.0.
2. **Sound, but nothing transcribed.** The Listening language is wrong, or the
   pipeline has stopped committing. Measured on the retained runs, with sound
   counted generously (any sample above |50|): healthy sessions reached at most
   12.6 s of sound without a commit; the four runs of 2026-08-14 that stopped
   committing mid-meeting (36 finals, 15 commits in one) reached 53-135 s.
   Thirty seconds of speech-level sound sits between the two with margin.
3. **Microphone switched on but not working.** The stream failed to open, or
   opened and delivered nothing -- previously a console line only.
4. **No DeepL key.** The transcript ran with no translation and the only sign
   was an English placeholder naming an environment variable.
5. **DeepL quota used up, or DeepL rejecting the key, mid-meeting.** Both read
   "● Translation degraded", with no reason.

None of these interrupts the meeting with a modal. Each names itself in the
status strip, and clicking the indicator shows the whole sentence.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha import constants  # noqa: E402
from alpha.audio.microphone import MicrophoneCaptureMixin  # noqa: E402
from alpha.translation.deepl_client import DeepLClient, DeepLError  # noqa: E402
from alpha.translation.translation_worker import (  # noqa: E402
    StableTranslationJob,
    TranslationWorker,
)
from alpha.ui import main_window, strings  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import service_status as ss  # noqa: E402


def in_language(test, language):
    previous = strings.get_language()
    test.addCleanup(strings.set_language, previous)
    strings.set_language(language)


# ---------------------------------------------------------------------------
# The states and their words
# ---------------------------------------------------------------------------
class TheRulesForSoundAndSpeech(unittest.TestCase):
    def status(self, **kw):
        base = dict(listening=True, deepgram_connected=True)
        base.update(kw)
        return ss.describe_connection(**base)

    def test_the_thresholds_are_the_measured_ones(self):
        self.assertEqual(constants.NO_SOUND_HINT_AFTER_S, 60.0)
        self.assertEqual(constants.NO_SPEECH_HINT_AFTER_VOICED_S, 30.0)

    def test_a_minute_of_silence_is_named(self):
        self.assertEqual(self.status(seconds_without_sound=59.9).state, ss.CONNECTED)
        status = self.status(seconds_without_sound=60.0)
        self.assertEqual(status.state, ss.NO_SOUND)
        self.assertEqual(status.message, ss.NO_SOUND_TEXT)

    def test_thirty_seconds_of_sound_with_no_words_is_named(self):
        self.assertEqual(self.status(voiced_seconds_without_words=29.9).state, ss.CONNECTED)
        status = self.status(voiced_seconds_without_words=30.0)
        self.assertEqual(status.state, ss.NO_SPEECH)
        self.assertEqual(status.message, ss.NO_SPEECH_TEXT)

    def test_they_outrank_a_translation_problem(self):
        """No words at all is worse than words without a translation."""
        status = self.status(
            seconds_without_sound=90.0, translation_degraded=True, translation_degraded_reason="quota"
        )
        self.assertEqual(status.state, ss.NO_SOUND)

    def test_a_reconnect_explains_the_silence_better(self):
        status = self.status(deepgram_connected=False, voiced_seconds_without_words=90.0)
        self.assertEqual(status.state, ss.RECONNECTING)

    def test_a_device_change_explains_it_better_still(self):
        status = self.status(audio_device_changed=True, seconds_without_sound=90.0)
        self.assertEqual(status.state, ss.RECONNECTING)
        self.assertTrue(status.detail["audio_device_changed"])

    def test_a_failure_wins(self):
        status = self.status(deepgram_auth_failed=True, seconds_without_sound=90.0)
        self.assertEqual(status.state, ss.FAILED)


class TranslationProblemsNameTheirReason(unittest.TestCase):
    def test_used_up_quota(self):
        status = ss.describe_connection(
            listening=True, deepgram_connected=True, translation_degraded=True, translation_degraded_reason="quota"
        )
        self.assertEqual(status.state, ss.DEGRADED)
        self.assertEqual(status.message, ss.DEEPL_QUOTA_TEXT)

    def test_a_rejected_key(self):
        status = ss.describe_connection(
            listening=True, deepgram_connected=True, translation_degraded=True, translation_degraded_reason="auth"
        )
        self.assertEqual(status.message, ss.DEEPL_KEY_REJECTED_TEXT)

    def test_a_missing_key(self):
        status = ss.describe_connection(
            listening=True, deepgram_connected=True, translation_unavailable_reason="missing_key"
        )
        self.assertEqual(status.state, ss.DEGRADED)
        self.assertEqual(status.message, ss.DEEPL_KEY_MISSING_TEXT)
        self.assertTrue(status.detail["translation_unavailable_reason"])

    def test_a_provider_outage_keeps_its_own_words(self):
        status = ss.describe_connection(
            listening=True,
            deepgram_connected=True,
            translation_degraded=True,
            translation_status_message="Translation degraded (provider failing, retrying in 30s).",
        )
        self.assertIn("retrying", status.message)


class EveryNewSentenceIsTranslated(unittest.TestCase):
    TEXTS = (
        "NO_SOUND_TEXT",
        "NO_SPEECH_TEXT",
        "DEEPL_QUOTA_TEXT",
        "DEEPL_KEY_REJECTED_TEXT",
        "DEEPL_KEY_MISSING_TEXT",
        "CONNECTION_DETAILS_TITLE",
    )
    LABELS = (
        "● No sound",
        "● No speech recognised",
        "● No translation",
        "● DeepL quota used up",
        "● DeepL key rejected",
        "Mic unavailable",
    )

    def test_all_of_them(self):
        texts = [getattr(ss, name) for name in self.TEXTS] + list(self.LABELS)
        missing = [text for text in texts if text not in strings._JA]
        self.assertEqual(missing, [], "these would show in English on a Japanese screen")


# ---------------------------------------------------------------------------
# Where the numbers come from
# ---------------------------------------------------------------------------
class ActivityHost:
    _note_audio_activity = AlphaApp._note_audio_activity
    _audio_attention_inputs = AlphaApp._audio_attention_inputs

    def __init__(self):
        self._last_any_sound_mono = 0.0
        self._voiced_seconds_total = 0.0
        self._voiced_total_at_last_words = 0.0
        self._hint_ledger_sequence = 0
        self._listening_started_mono = 1000.0
        self._dg_disconnected_at = 0.0


class TheMixerCountsSoundAndSpeech(unittest.TestCase):
    def setUp(self):
        self.host = ActivityHost()

    def test_digital_silence_is_not_sound(self):
        self.host._note_audio_activity({"sys_rms": 0.0, "mic_rms": 0.0}, now_mono=1010.0)
        self.assertEqual(self.host._last_any_sound_mono, 0.0)

    def test_any_real_signal_is_sound(self):
        self.host._note_audio_activity({"sys_rms": 3.0, "mic_rms": 0.0}, now_mono=1010.0)
        self.assertEqual(self.host._last_any_sound_mono, 1010.0)

    def test_speech_level_frames_are_counted_at_twenty_milliseconds(self):
        for _ in range(50):
            self.host._note_audio_activity({"sys_rms": 900.0, "system_active": True}, now_mono=1010.0)
        self.host._note_audio_activity({"sys_rms": 40.0, "system_active": False}, now_mono=1010.0)
        self.assertAlmostEqual(self.host._voiced_seconds_total, 1.0, places=6)

    def test_a_malformed_frame_never_raises(self):
        self.host._note_audio_activity({"sys_rms": None, "mic_rms": "x"}, now_mono=1010.0)


class TheIndicatorsInputs(unittest.TestCase):
    def setUp(self):
        self.host = ActivityHost()
        self.sequence = 5
        patcher = mock.patch(
            "alpha.transcription.canonical_transcript_ledger.mutation_sequence",
            side_effect=lambda: self.sequence,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host._hint_ledger_sequence = 5

    def test_silence_is_counted_from_the_start_when_nothing_was_ever_heard(self):
        no_sound, _voiced = self.host._audio_attention_inputs(now_mono=1061.0)
        self.assertAlmostEqual(no_sound, 61.0)

    def test_silence_is_counted_from_the_last_sound(self):
        self.host._last_any_sound_mono = 1050.0
        no_sound, _voiced = self.host._audio_attention_inputs(now_mono=1061.0)
        self.assertAlmostEqual(no_sound, 11.0)

    def test_speech_without_words_accumulates(self):
        self.host._voiced_seconds_total = 31.0
        _no_sound, voiced = self.host._audio_attention_inputs(now_mono=1061.0)
        self.assertAlmostEqual(voiced, 31.0)

    def test_a_line_reaching_the_transcript_resets_it(self):
        self.host._voiced_seconds_total = 31.0
        self.sequence = 6
        _no_sound, voiced = self.host._audio_attention_inputs(now_mono=1061.0)
        self.assertEqual(voiced, 0.0)

    def test_an_outage_does_not_count_against_the_words(self):
        """No words arrive while the socket is down; that is the reconnect's story."""
        self.host._voiced_seconds_total = 31.0
        self.host._dg_disconnected_at = time.time()
        _no_sound, voiced = self.host._audio_attention_inputs(now_mono=1061.0)
        self.assertEqual(voiced, 0.0)
        self.host._dg_disconnected_at = 0.0
        _no_sound, voiced = self.host._audio_attention_inputs(now_mono=1062.0)
        self.assertEqual(voiced, 0.0, "the outage's speech came back as a false hint")


class TheLedgerCounterIsReal(unittest.TestCase):
    def test_it_moves_when_a_line_is_committed(self):
        from alpha.transcription import canonical_transcript_ledger as ledger

        self.assertIsInstance(ledger.mutation_sequence(), int)


class IndicatorHost:
    _sync_connection_indicator = AlphaApp._sync_connection_indicator
    _CONNECTION_INDICATOR_TEXT = AlphaApp._CONNECTION_INDICATOR_TEXT
    _explain_connection_state = AlphaApp._explain_connection_state

    def __init__(self, **inputs):
        self.signal_label = object()
        self.is_listening = True
        self.translation_worker = None
        self._dg_disconnected_at = 0.0
        self._dg_reconnecting = False
        self._dg_auth_failed = False
        self._dg_credit_exhausted = False
        self._audio_device_changed = False
        self._diag_wasapi_device_name = ""
        self._translation_unavailable_reason = inputs.get("unavailable", "")
        self._inputs = (inputs.get("no_sound", 0.0), inputs.get("voiced", 0.0))
        self.painted = []
        self.published = []

    def deepgram_gap_seconds(self):
        return 0.0

    def _audio_attention_inputs(self, now_mono=None):
        return self._inputs

    def _set_dynamic_text(self, label, text, **kwargs):
        self.painted.append(text)

    def publish_error_event(self, message, source=None, recoverable=True):
        self.published.append((message, recoverable))


class Worker:
    def __init__(self, reason):
        self.degraded = True
        self.degraded_reason = reason
        self.status_message = "Translation paused (quota exceeded)."


class TheStripNamesTheProblem(unittest.TestCase):
    CASES = (
        (dict(no_sound=61.0), None, "● No sound"),
        (dict(voiced=31.0), None, "● No speech recognised"),
        (dict(unavailable="missing_key"), None, "● No translation"),
        ({}, "quota", "● DeepL quota used up"),
        ({}, "auth", "● DeepL key rejected"),
    )

    def test_each_state_has_its_own_label(self):
        for inputs, reason, label in self.CASES:
            with self.subTest(label=label):
                host = IndicatorHost(**inputs)
                if reason:
                    host.translation_worker = Worker(reason)
                host._sync_connection_indicator()
                self.assertEqual(host.painted[-1], label)

    def test_none_of_them_raises_a_modal(self):
        for inputs, reason, label in self.CASES:
            host = IndicatorHost(**inputs)
            if reason:
                host.translation_worker = Worker(reason)
            host._sync_connection_indicator()
            self.assertTrue(all(recoverable for _m, recoverable in host.published), label)


class ClickingTheIndicatorExplains(unittest.TestCase):
    def test_the_whole_sentence_in_the_display_language(self):
        in_language(self, "ja")
        host = IndicatorHost(no_sound=61.0)
        host._sync_connection_indicator()
        with mock.patch.object(main_window, "messagebox") as box:
            host._explain_connection_state()
        title, message = box.showinfo.call_args[0][:2]
        self.assertEqual(title, strings._JA[ss.CONNECTION_DETAILS_TITLE])
        self.assertEqual(message, strings._JA[ss.NO_SOUND_TEXT])

    def test_a_healthy_session_explains_nothing(self):
        host = IndicatorHost()
        host._sync_connection_indicator()
        with mock.patch.object(main_window, "messagebox") as box:
            host._explain_connection_state()
        box.showinfo.assert_not_called()



# ---------------------------------------------------------------------------
# DeepL: the reason, told apart by type rather than by a digit in the text
# ---------------------------------------------------------------------------
class RaisingTranslator:
    def __init__(self, exc):
        self.exc = exc

    def translate_text(self, *args, **kwargs):
        raise self.exc


def classify(exc):
    client = DeepLClient(api_key="k")
    client._translator = RaisingTranslator(exc)
    try:
        client.translate_text("テスト", source_lang="JA", target_lang="EN-US")
    except DeepLError as err:
        return err
    raise AssertionError("no error raised")


class DeepLFailuresAreToldApartByType(unittest.TestCase):
    def test_the_sdks_quota_exception(self):
        import deepl

        err = classify(deepl.QuotaExceededException("Quota for this billing period has been exceeded", http_status_code=456))
        self.assertEqual((err.code, err.retryable), ("quota_exceeded", False))

    def test_the_sdks_authorization_exception(self):
        import deepl

        err = classify(deepl.AuthorizationException("Authorization failure, check auth_key", http_status_code=403))
        self.assertEqual((err.code, err.retryable), ("auth_failed", False))

    def test_a_status_code_without_the_subclass(self):
        import deepl

        err = classify(deepl.DeepLException("request failed", http_status_code=456))
        self.assertEqual(err.code, "quota_exceeded")

    def test_a_digit_in_a_message_is_not_a_status(self):
        """The Deepgram audit's bug class: "403" inside an id is not HTTP 403."""
        import deepl

        err = classify(deepl.DeepLException("upstream id 7f403a91-4560-4000 failed"))
        self.assertNotIn(err.code, ("auth_failed", "quota_exceeded", "invalid_request"))


class Rejecting:
    available = True

    def __init__(self, code):
        self.code = code

    def translate_text(self, *args, **kwargs):
        raise DeepLError("provider said no", code=self.code, retryable=False)


def job():
    return StableTranslationJob(
        run_id="t",
        segment_id=1,
        source_language="ja",
        source_text="テスト",
        source_text_hash="",
        stable_committed_at=time.time(),
    )


class TheWorkerKnowsWhyItIsDegraded(unittest.TestCase):
    def test_a_rejected_key(self):
        worker = TranslationWorker(run_id="t", client=Rejecting("auth_failed"), enabled=True)
        worker._translate_job(job(), "EN-US")
        self.assertTrue(worker.degraded, "a rejected DeepL key left the indicator green")
        self.assertEqual(worker.degraded_reason, "auth")

    def test_a_rejected_key_has_a_way_back(self):
        """The item 94 latch audit: a flag that disables must have a reachable reset."""
        worker = TranslationWorker(run_id="t", client=Rejecting("auth_failed"), enabled=True)
        worker._translate_job(job(), "EN-US")
        worker._record_translation_success()
        self.assertEqual(worker.degraded_reason, "")
        self.assertFalse(worker.degraded)

    def test_used_up_quota(self):
        worker = TranslationWorker(run_id="t", client=Rejecting("quota_exceeded"), enabled=True)
        worker._translate_job(job(), "EN-US")
        self.assertEqual(worker.degraded_reason, "quota")

    def test_a_provider_outage(self):
        worker = TranslationWorker(run_id="t", client=Rejecting("temporary_server"), enabled=True)
        worker._circuit_open_until = time.time() + 60
        self.assertEqual(worker.degraded_reason, "provider")


# ---------------------------------------------------------------------------
# No DeepL key: the reason is recorded, and the pane speaks the language
# ---------------------------------------------------------------------------
class SessionHost:
    _start_translation_session = AlphaApp._start_translation_session
    _set_translation_status = AlphaApp._set_translation_status

    def __init__(self):
        self.translation_worker = None
        self.translated_verse_box = None
        self._translation_unavailable_reason = ""
        self.statuses = []


class NoDeepLKeyIsNamed(unittest.TestCase):
    def test_the_reason_is_recorded(self):
        host = SessionHost()
        with mock.patch.object(main_window, "has_deepl_api_key", return_value=False), mock.patch(
            "alpha.utils.run_identity.get_run_id", return_value="r"
        ), mock.patch("alpha.utils.run_identity.get_run_folder", return_value=None):
            host._start_translation_session()
        self.assertEqual(host._translation_unavailable_reason, "missing_key")
        self.assertEqual(host._translation_status_message, ss.DEEPL_KEY_MISSING_TEXT)

    def test_the_pane_text_is_translated(self):
        in_language(self, "ja")

        class Box:
            _placeholder_text = ""

        host = SessionHost()
        host.translated_verse_box = Box()
        host._show_text_placeholder = lambda box: None
        host._set_translation_status(ss.DEEPL_KEY_MISSING_TEXT)
        self.assertEqual(host.translated_verse_box._placeholder_text, strings._JA[ss.DEEPL_KEY_MISSING_TEXT])


# ---------------------------------------------------------------------------
# Microphone switched on, not working
# ---------------------------------------------------------------------------
class MicHost:
    _sync_mic_switches = AlphaApp._sync_mic_switches
    _apply_microphone_capture_live = MicrophoneCaptureMixin._apply_microphone_capture_live
    _carry_microphone_switch = MicrophoneCaptureMixin._carry_microphone_switch
    _note_microphone_unavailable = MicrophoneCaptureMixin._note_microphone_unavailable

    def __init__(self):
        self._microphone_capture_enabled = True
        self._mic_unavailable = False
        self.is_listening = True
        self._stop_event = threading.Event()
        self._mic_stream = None
        self._mic_chunks_captured = 0
        self.mic_switch = None
        self.mic_switch_menu = None
        self.open_raises = None
        self.confirm = True
        self.synced = 0

    def _start_microphone_capture(self):
        if self.open_raises:
            raise self.open_raises
        self._mic_stream = object()

    def _close_microphone_stream(self):
        self._mic_stream = None

    def _await_capture_confirmation(self, attr, before):
        return self.confirm

    def _mark_device_swap_boundary(self):
        return True

    def _run_on_ui_thread(self, callback):
        self.synced += 1
        callback()


class Switch:
    def __init__(self):
        self.text = ""

    def select(self):
        pass

    def deselect(self):
        pass

    def configure(self, **kw):
        self.text = kw.get("text", self.text)


class AMicrophoneThatDoesNotWorkSaysSo(unittest.TestCase):
    def test_a_failed_open_is_shown_on_the_switch(self):
        host = MicHost()
        host.mic_switch = Switch()
        host.open_raises = OSError("no input device")
        host._apply_microphone_capture_live()
        self.assertTrue(host._mic_unavailable)
        self.assertEqual(host.mic_switch.text, "Mic unavailable")

    def test_a_silent_stream_is_shown_too(self):
        host = MicHost()
        host.mic_switch = Switch()
        host.confirm = False
        host._apply_microphone_capture_live()
        self.assertTrue(host._mic_unavailable)

    def test_a_working_microphone_clears_it(self):
        host = MicHost()
        host.mic_switch = Switch()
        host._mic_unavailable = True
        host._apply_microphone_capture_live()
        self.assertFalse(host._mic_unavailable)
        self.assertEqual(host.mic_switch.text, "Mic on")

    def test_turning_it_off_clears_it(self):
        host = MicHost()
        host.mic_switch = Switch()
        host._mic_unavailable = True
        host._mic_stream = object()
        host._microphone_capture_enabled = False
        host._apply_microphone_capture_live()
        self.assertFalse(host._mic_unavailable)
        self.assertEqual(host.mic_switch.text, "Mic off")

    def test_outside_a_session_the_switch_shows_the_setting(self):
        host = MicHost()
        host.mic_switch = Switch()
        host._mic_unavailable = True
        host.is_listening = False
        host._sync_mic_switches()
        self.assertEqual(host.mic_switch.text, "Mic on")

    def test_in_japanese(self):
        in_language(self, "ja")
        host = MicHost()
        host.mic_switch = Switch()
        host._mic_unavailable = True
        host._sync_mic_switches()
        self.assertEqual(host.mic_switch.text, strings._JA["Mic unavailable"])

    def test_a_failure_at_start_is_recorded_too(self):
        source = Path(main_window.__file__).read_text(encoding="utf-8")
        start = source.index("Microphone capture unavailable, continuing with system audio only")
        window = source[start - 600 : start + 400]
        self.assertIn("_note_microphone_unavailable(True)", window)


def _tk_available():
    try:
        import tkinter

        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_tk_available(), "Tk display unavailable in this environment")
class TheNewLabelsFitTheStrip(unittest.TestCase):
    def setUp(self):
        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)
        self.app = AlphaApp()
        self.app.deiconify()
        self.app.update()
        self.addCleanup(self._destroy)

    def _destroy(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def test_clicking_the_real_indicator_explains(self):
        """A real click on the real label, not a grep for the binding."""
        self.app.is_listening = True
        self.addCleanup(setattr, self.app, "is_listening", False)
        self.app._audio_attention_inputs = lambda now_mono=None: (61.0, 0.0)
        self.app._sync_connection_indicator()
        with mock.patch.object(main_window, "messagebox") as box:
            inner = getattr(self.app.signal_label, "_label", self.app.signal_label)
            inner.event_generate("<Button-1>", x=2, y=2)
            self.app.update()
        box.showinfo.assert_called_once()
        self.assertEqual(box.showinfo.call_args[0][1], ss.NO_SOUND_TEXT)

    def test_every_label_in_both_languages(self):
        labels = EveryNewSentenceIsTranslated.LABELS[:-1]
        for language in ("en", "ja"):
            strings.set_language(language)
            for width in (900, 1400):
                self.app.geometry(f"{width}x800")
                self.app.update()
                self.app._apply_responsive_layout()
                for label in labels:
                    self.app._set_dynamic_text(self.app.signal_label, label)
                    self.app.mic_switch.configure(text=strings.t("Mic unavailable"))
                    self.app.update()
                    strip_right = (
                        self.app.status_bar_frame.winfo_rootx() + self.app.status_bar_frame.winfo_width()
                    )
                    for name in ("mic_switch", "signal_label", "timer_label"):
                        widget = getattr(self.app, name, None)
                        if widget is None or not widget.winfo_ismapped():
                            continue
                        overflow = (widget.winfo_rootx() + widget.winfo_width()) - strip_right
                        self.assertLessEqual(
                            overflow, 0, f"{name} {overflow}px past the strip ({language}, {width}, {label})"
                        )


if __name__ == "__main__":
    unittest.main()
