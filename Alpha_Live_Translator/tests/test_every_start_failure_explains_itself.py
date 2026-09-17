"""Every way Deepgram can stop a meeting says what happened, in the operator's language.

Item 29 made a key Deepgram refuses at Start say so. The owner's follow-up:
"deepgram er o key trial expired hoye jai. Setar o error message lagbe", and
every popup in English or Japanese by the display-language setting.

What Deepgram actually answers, from its error reference
(developers.deepgram.com/docs/errors), verbatim:

    401  INVALID_AUTH              "Invalid credentials."
         -- a wrong, deleted OR EXPIRED key; the answer does not say which
    401  INSUFFICIENT_PERMISSIONS  "User does not have sufficient permissions."
    402  ASR_PAYMENT_REQUIRED      "Project does not have enough credits for an
                                    ASR request and does not have an overage
                                    agreement."  -- the free trial credit gone
    403  INSUFFICIENT_PERMISSIONS  "Project does not have access to the
                                    requested model."
    429  TOO_MANY_REQUESTS         "Too many requests. Please try again later"
    503  Service Unavailable

Deepgram's own forum confirms an already-open socket survives its key
expiring, so mid-meeting the refusal arrives on the next reconnect.

Before this file:

* a 402 at Start showed "Deepgram refused the connection" -- nothing about
  credit, and no way to enter another key on a keyless build;
* a 402 mid-meeting was not recognised at all, so the indicator said
  "Reconnecting" for the rest of the meeting;
* a network that never reached Deepgram waited the full 30 s and then said
  nothing, even when the socket had already failed in the first 50 ms;
* a second rejected query mid-meeting raised TWO dialogs for one error;
* the preflight's "no key" dialog and the mid-meeting "key rejected" dialog
  were English whatever the display language.
"""

import ast
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from websocket._exceptions import WebSocketBadStatusException  # noqa: E402

from alpha.transcription import deepgram_client  # noqa: E402
from alpha.transcription.deepgram_client import (  # noqa: E402
    DeepgramKeyRejected,
    DeepgramRefusedStart,
    DeepgramUnreachable,
    deepgram_start_refusal,
)
from alpha.ui import key_setup, main_window, strings  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import service_status as ss  # noqa: E402

MAIN_WINDOW_SOURCE = Path(main_window.__file__).read_text(encoding="utf-8")
CLIENT_SOURCE = Path(deepgram_client.__file__).read_text(encoding="utf-8")


def handshake(status, reason, err_code=None, err_msg=None, deepgram=True):
    """A failed handshake in websocket-client's own shape, with Deepgram's body."""
    headers = {"content-type": "application/json"}
    if deepgram:
        headers["dg-request-id"] = "01a0accc-307e-7171-b0e9-5eb486f4e252"
    body = b""
    if err_code:
        body = ('{"err_code":"%s","err_msg":"%s","request_id":"x"}' % (err_code, err_msg or "")).encode()
    return WebSocketBadStatusException(
        "Handshake status %d %s -+-+- %s -+-+- %s" % (status, reason, headers, body),
        status,
        reason,
        headers,
        body,
    )


INVALID_AUTH = handshake(401, "Unauthorized", "INVALID_AUTH", "Invalid credentials.")
NO_PERMISSION = handshake(
    401, "Unauthorized", "INSUFFICIENT_PERMISSIONS", "User does not have sufficient permissions."
)
OUT_OF_CREDIT = handshake(
    402,
    "Payment Required",
    "ASR_PAYMENT_REQUIRED",
    "Project does not have enough credits for an ASR request and does not have an overage agreement.",
)
NO_MODEL = handshake(
    403, "Forbidden", "INSUFFICIENT_PERMISSIONS", "Project does not have access to the requested model."
)
RATE_LIMITED = handshake(429, "Too Many Requests", "TOO_MANY_REQUESTS", "Too many requests. Please try again later")
SERVICE_DOWN = handshake(503, "Service Unavailable")
PROJECT_GONE = handshake(404, "Not Found", "PROJECT_NOT_FOUND", "Project not found.")
PROXY_403 = handshake(403, "Forbidden", deepgram=False)


def japanese():
    """Switch the display language for one test and put it back."""
    previous = strings.get_language()
    strings.set_language("ja")
    return previous


class DeepgramsDocumentedRefusalsCanBeToldApart(unittest.TestCase):
    CASES = (
        (INVALID_AUTH, ss.REFUSAL_KEY_INVALID, True),
        (NO_PERMISSION, ss.REFUSAL_KEY_NO_PERMISSION, True),
        (OUT_OF_CREDIT, ss.REFUSAL_CREDIT_EXHAUSTED, False),
        (NO_MODEL, ss.REFUSAL_NO_MODEL_ACCESS, False),
        (RATE_LIMITED, ss.REFUSAL_RATE_LIMITED, False),
        (SERVICE_DOWN, ss.REFUSAL_SERVICE_UNAVAILABLE, False),
        (PROJECT_GONE, ss.REFUSAL_OTHER, False),
        (PROXY_403, ss.REFUSAL_OTHER, False),
    )

    def test_each_answer_gets_its_own_reason(self):
        for err, reason, is_key in self.CASES:
            with self.subTest(reason=reason, text=str(err)[:40]):
                refusal = deepgram_start_refusal(err, str(err))
                self.assertIsInstance(refusal, DeepgramRefusedStart)
                self.assertEqual(refusal.reason, reason)
                self.assertEqual(isinstance(refusal, DeepgramKeyRejected), is_key)

    def test_the_error_code_is_kept_for_the_popup(self):
        refusal = deepgram_start_refusal(OUT_OF_CREDIT, str(OUT_OF_CREDIT))
        self.assertEqual(refusal.status, 402)
        self.assertEqual(refusal.err_code, "ASR_PAYMENT_REQUIRED")
        self.assertIn("enough credits", str(refusal), "the log lost Deepgram's own words")

    def test_only_a_text_copy_of_the_error_is_classified_the_same(self):
        """Some paths hand over `str(err)` only; the answer must not change."""
        refusal = deepgram_start_refusal(None, str(OUT_OF_CREDIT))
        self.assertEqual(refusal.reason, ss.REFUSAL_CREDIT_EXHAUSTED)


class EveryReasonHasWordsInBothLanguages(unittest.TestCase):
    REASONS = (
        ss.REFUSAL_KEY_INVALID,
        ss.REFUSAL_KEY_NO_PERMISSION,
        ss.REFUSAL_CREDIT_EXHAUSTED,
        ss.REFUSAL_NO_MODEL_ACCESS,
        ss.REFUSAL_RATE_LIMITED,
        ss.REFUSAL_SERVICE_UNAVAILABLE,
        ss.REFUSAL_OTHER,
        ss.UNREACHABLE,
    )

    def test_each_reason_has_a_title_and_an_explanation(self):
        for reason in self.REASONS:
            title, lines = ss.start_failure_text(reason)
            self.assertTrue(title.strip(), reason)
            self.assertTrue(lines, reason)

    def test_each_of_them_is_translated(self):
        texts = set()
        for reason in self.REASONS:
            title, lines = ss.start_failure_text(reason)
            texts.add(title)
            texts.update(lines)
            for hint in (ss.key_dialog_hint(reason), ss.env_file_hint(reason)):
                if hint:
                    texts.add(hint)
        texts.update(
            (
                ss.NO_DEEPGRAM_RESPONSE_TEXT,
                ss.MID_SESSION_KEY_REJECTED_TEXT,
                ss.MID_SESSION_CREDIT_EXHAUSTED_TEXT,
            )
        )
        missing = sorted(text for text in texts if text not in strings._JA)
        self.assertEqual(missing, [], "these would show in English on a Japanese screen")

    def test_the_trial_is_named_where_credit_runs_out(self):
        _title, lines = ss.start_failure_text(ss.REFUSAL_CREDIT_EXHAUSTED)
        self.assertIn("trial", " ".join(lines).lower())

    def test_an_expired_key_is_named_as_a_cause(self):
        """Deepgram answers a wrong key and an expired one identically (401)."""
        _title, lines = ss.start_failure_text(ss.REFUSAL_KEY_INVALID)
        self.assertIn("expired", " ".join(lines).lower())
        self.assertIn("expired", ss.MID_SESSION_KEY_REJECTED_TEXT.lower())


class ExplainHarness(unittest.TestCase):
    """`_explain_start_failure` with only the dialogs replaced."""

    def setUp(self):
        self.calls = []
        box = mock.patch.object(main_window, "messagebox")
        self.messagebox = box.start()
        self.addCleanup(box.stop)
        self.messagebox.showerror.side_effect = lambda *a, **k: self.calls.append(("error", a))
        offer = mock.patch.object(
            main_window,
            "_offer_key_setup",
            side_effect=lambda **k: self.calls.append(("offer", k)) or False,
        )
        offer.start()
        self.addCleanup(offer.stop)
        previous = strings.get_language()
        self.addCleanup(strings.set_language, previous)
        strings.set_language("en")

    def explain(self, error, *, keyless):
        with mock.patch.object(key_setup, "should_prompt", return_value=keyless):
            main_window._explain_start_failure(error)
        return [kind for kind, _ in self.calls]

    def shown(self):
        title, message = self.calls[0][1][:2]
        return title, message


class TheOperatorIsToldWhatHappened(ExplainHarness):
    def test_used_up_trial_credit_says_so_and_names_the_file(self):
        from alpha.config import PROJECT_ROOT as APP_ROOT

        kinds = self.explain(deepgram_start_refusal(OUT_OF_CREDIT, str(OUT_OF_CREDIT)), keyless=False)
        self.assertEqual(kinds, ["error"])
        title, message = self.shown()
        self.assertEqual(title, "Deepgram credit used up")
        self.assertIn("trial", message.lower())
        self.assertIn("HTTP 402 ASR_PAYMENT_REQUIRED", message)
        self.assertIn(str(APP_ROOT / ".env"), message)
        self.assertNotIn("internet", message.lower())

    def test_used_up_credit_on_a_keyless_build_offers_another_key(self):
        kinds = self.explain(deepgram_start_refusal(OUT_OF_CREDIT, str(OUT_OF_CREDIT)), keyless=True)
        self.assertEqual(kinds, ["error", "offer"])

    def test_a_key_without_permission_is_a_key_problem(self):
        kinds = self.explain(deepgram_start_refusal(NO_PERMISSION, str(NO_PERMISSION)), keyless=True)
        self.assertEqual(kinds, ["error", "offer"])
        self.assertIn("permission", self.shown()[1].lower())

    def test_no_model_access_is_not_blamed_on_the_key(self):
        kinds = self.explain(deepgram_start_refusal(NO_MODEL, str(NO_MODEL)), keyless=True)
        self.assertEqual(kinds, ["error"], "a working key was offered for replacement")
        self.assertIn("model", self.shown()[1].lower())

    def test_a_busy_deepgram_says_wait(self):
        kinds = self.explain(deepgram_start_refusal(RATE_LIMITED, str(RATE_LIMITED)), keyless=True)
        self.assertEqual(kinds, ["error"])
        self.assertIn("wait", self.shown()[1].lower())

    def test_a_deepgram_outage_says_try_later(self):
        kinds = self.explain(deepgram_start_refusal(SERVICE_DOWN, str(SERVICE_DOWN)), keyless=True)
        self.assertEqual(kinds, ["error"])
        self.assertIn("HTTP 503", self.shown()[1])

    def test_an_unreachable_deepgram_names_the_network_and_the_real_error(self):
        error = DeepgramUnreachable("[Errno 11001] getaddrinfo failed")
        kinds = self.explain(error, keyless=True)
        self.assertEqual(kinds, ["error"])
        title, message = self.shown()
        self.assertEqual(title, "Cannot reach Deepgram")
        self.assertIn("internet", message.lower())
        self.assertIn("getaddrinfo failed", message)

    def test_a_silent_timeout_still_explains_itself(self):
        kinds = self.explain(DeepgramUnreachable(""), keyless=False)
        self.assertEqual(kinds, ["error"])
        self.assertIn(ss.NO_DEEPGRAM_RESPONSE_TEXT, self.shown()[1])


class ThePopupFollowsTheDisplayLanguage(ExplainHarness):
    def test_used_up_credit_in_japanese(self):
        strings.set_language("ja")
        self.explain(deepgram_start_refusal(OUT_OF_CREDIT, str(OUT_OF_CREDIT)), keyless=False)
        title, message = self.shown()
        self.assertEqual(title, strings._JA["Deepgram credit used up"])
        _t, lines = ss.start_failure_text(ss.REFUSAL_CREDIT_EXHAUSTED)
        for line in lines:
            self.assertIn(strings._JA[line], message)
            self.assertNotIn(line, message, "an English sentence leaked into the Japanese popup")
        # Identifiers and paths are the same in both languages.
        self.assertIn("HTTP 402 ASR_PAYMENT_REQUIRED", message)

    def test_unreachable_in_japanese(self):
        strings.set_language("ja")
        self.explain(DeepgramUnreachable(""), keyless=False)
        title, message = self.shown()
        self.assertEqual(title, strings._JA["Cannot reach Deepgram"])
        self.assertIn(strings._JA[ss.NO_DEEPGRAM_RESPONSE_TEXT], message)


class WaitHost:
    _wait_for_deepgram_sender = AlphaApp._wait_for_deepgram_sender

    def __init__(self):
        self._stop_event = threading.Event()
        self._latency_sender_loop_alive = False
        self._dg_start_refusal = None
        self._dg_start_socket_error = None
        self._dg_thread = None


class AnUnreachableDeepgramFailsFast(unittest.TestCase):
    def test_a_dead_connection_does_not_wait_out_the_timeout(self):
        """websocket-client returns from run_forever once the socket fails."""
        host = WaitHost()
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        host._dg_thread = dead
        host._dg_start_socket_error = "[Errno 11001] getaddrinfo failed"
        started = time.perf_counter()
        with self.assertRaises(DeepgramUnreachable) as caught:
            host._wait_for_deepgram_sender(timeout_s=30.0)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(caught.exception.detail, "[Errno 11001] getaddrinfo failed")

    def test_a_refusal_still_wins_over_the_dead_connection(self):
        host = WaitHost()
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        host._dg_thread = dead
        host._dg_start_refusal = deepgram_start_refusal(OUT_OF_CREDIT, str(OUT_OF_CREDIT))
        with self.assertRaises(DeepgramRefusedStart):
            host._wait_for_deepgram_sender(timeout_s=30.0)

    def test_a_connection_still_trying_is_given_the_full_timeout(self):
        host = WaitHost()
        release = threading.Event()
        alive = threading.Thread(target=release.wait, daemon=True)
        alive.start()
        self.addCleanup(release.set)
        host._dg_thread = alive
        started = time.perf_counter()
        with self.assertRaises(DeepgramUnreachable):
            host._wait_for_deepgram_sender(timeout_s=0.4)
        self.assertGreaterEqual(time.perf_counter() - started, 0.35)

    def test_the_timeout_keeps_its_old_wording_for_the_logs(self):
        host = WaitHost()
        with self.assertRaisesRegex(DeepgramUnreachable, "sender not ready within"):
            host._wait_for_deepgram_sender(timeout_s=0.2)


class ErrorHost:
    _deepgram_on_error = AlphaApp._deepgram_on_error

    def __init__(self, *, starting=False, listening=True):
        self._stop_event = threading.Event()
        self.is_listening = listening
        self._starting_listening = starting
        self._is_stopping = False
        self._dg_stop_sending_audio = False
        self._dg_auth_failed = False
        self._dg_credit_exhausted = False
        self._dg_start_refusal = None
        self._dg_start_socket_error = None
        self._listen_language = "ja"
        self._jp_keyterms_fallback_used = True
        self.reconnects = 0
        self.published = []

    def _schedule_reconnect(self):
        self.reconnects += 1

    def publish_error_event(self, message, source=None, recoverable=True):
        self.published.append((source, recoverable))

    def error(self, err):
        self._deepgram_on_error(None, err)
        return self


class TheStartRecordsWhatTheSocketSaid(unittest.TestCase):
    def test_a_network_error_at_start_is_kept_for_the_popup(self):
        host = ErrorHost(starting=True, listening=False).error(
            ConnectionRefusedError("[WinError 10061] No connection could be made")
        )
        self.assertIn("10061", host._dg_start_socket_error)
        self.assertIsNone(host._dg_start_refusal)

    def test_a_refusal_at_start_is_not_mistaken_for_a_network_error(self):
        host = ErrorHost(starting=True, listening=False).error(OUT_OF_CREDIT)
        self.assertIsNone(host._dg_start_socket_error)
        self.assertEqual(host._dg_start_refusal.reason, ss.REFUSAL_CREDIT_EXHAUSTED)


class CreditRunningOutMidMeeting(unittest.TestCase):
    def test_a_402_on_reconnect_is_recognised(self):
        host = ErrorHost().error(OUT_OF_CREDIT)
        self.assertTrue(host._dg_credit_exhausted)
        self.assertEqual(host.reconnects, 1, "a top-up must still be able to recover the meeting")

    def test_a_503_is_not_mistaken_for_it(self):
        self.assertFalse(ErrorHost().error(SERVICE_DOWN)._dg_credit_exhausted)

    def test_it_is_a_failure_with_its_own_advice(self):
        status = ss.describe_connection(
            listening=True, deepgram_connected=False, deepgram_credit_exhausted=True
        )
        self.assertEqual(status.state, ss.FAILED)
        self.assertEqual(status.message, ss.MID_SESSION_CREDIT_EXHAUSTED_TEXT)
        self.assertTrue(status.detail["deepgram_credit_exhausted"])

    def test_a_rejected_key_still_names_the_key(self):
        status = ss.describe_connection(
            listening=True,
            deepgram_connected=False,
            deepgram_auth_failed=True,
            deepgram_credit_exhausted=True,
        )
        self.assertEqual(status.message, ss.MID_SESSION_KEY_REJECTED_TEXT)

    def test_the_socket_opening_clears_it(self):
        tree = ast.parse(CLIENT_SOURCE)
        on_open = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_deepgram_on_open"
        )
        cleared = [
            n
            for n in ast.walk(on_open)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "_dg_credit_exhausted" for t in n.targets)
        ]
        self.assertTrue(cleared, "a topped-up account would keep showing 'credit used up'")

    def test_every_start_forgets_the_previous_sessions_answers(self):
        start = MAIN_WINDOW_SOURCE[MAIN_WINDOW_SOURCE.index("    def _start_listening(self):"):]
        start = start[: start.index("\n    def ", 10)]
        for name in ("_dg_credit_exhausted = False", "_dg_start_socket_error = None"):
            self.assertIn(f"self.{name}", start)


class IndicatorHost:
    _sync_connection_indicator = AlphaApp._sync_connection_indicator
    _CONNECTION_INDICATOR_TEXT = AlphaApp._CONNECTION_INDICATOR_TEXT

    def __init__(self):
        self.signal_label = object()
        self.is_listening = True
        self.translation_worker = None
        self._dg_disconnected_at = time.time()
        self._dg_reconnecting = True
        self._dg_auth_failed = False
        self._dg_credit_exhausted = True
        self._audio_device_changed = False
        self._diag_wasapi_device_name = ""
        self.painted = []
        self.published = []

    def deepgram_gap_seconds(self):
        return 30.0

    def _set_dynamic_text(self, label, text, **kwargs):
        self.painted.append(text)

    def publish_error_event(self, message, source=None, recoverable=True):
        self.published.append((message, recoverable))


class TheIndicatorNamesTheCredit(unittest.TestCase):
    def test_the_strip_says_credit_not_reconnecting(self):
        host = IndicatorHost()
        host._sync_connection_indicator()
        self.assertEqual(host.painted[-1], "● Deepgram credit used up")

    def test_the_operator_is_told_once(self):
        host = IndicatorHost()
        for _ in range(3):
            host._sync_connection_indicator()
        self.assertEqual(
            host.published, [(ss.MID_SESSION_CREDIT_EXHAUSTED_TEXT, False)]
        )

    def test_the_indicator_text_is_translated(self):
        self.assertIn("● Deepgram credit used up", strings._JA)


class AMidMeetingRejectedQueryShowsOneDialog(unittest.TestCase):
    """A second genuine 400 raised two modals: 'Error' and 'Deepgram Connection Error'."""

    def setUp(self):
        self.host = ErrorHost()
        self.notices = []
        bus = mock.Mock()
        bus.post.side_effect = lambda name, payload: self.notices.append((name, payload))
        patcher = mock.patch("alpha.utils.ui_event_bus.get_ui_event_bus", return_value=bus)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_stop_notice_and_no_unrecoverable_event(self):
        query_rejected = handshake(400, "Bad Request", "INVALID_QUERY_PARAMETER", "bad keyterm")
        self.host.error(query_rejected)
        stops = [p for n, p in self.notices if p.get("action") == "stop_listening"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(
            [r for _s, r in self.host.published],
            [True],
            "the error event raised a second modal beside the stop notice",
        )

    def test_the_notice_is_rendered_in_the_display_language(self):
        payload = {
            "title": "Deepgram connection error",
            "message": "Deepgram rejected the connection settings, so listening has been stopped.",
            "detail": "Handshake status 400 Bad Request",
            "action": "stop_listening",
        }
        previous = japanese()
        self.addCleanup(strings.set_language, previous)
        with mock.patch.object(main_window, "messagebox") as box:
            main_window._show_partial_error(payload)
        title, message = box.showerror.call_args[0][:2]
        self.assertEqual(title, strings._JA["Deepgram connection error"])
        self.assertIn(strings._JA[payload["message"]], message)
        self.assertIn("Handshake status 400", message)


class TheExistingPopupsSpeakJapaneseToo(unittest.TestCase):
    def test_the_preflight_messages_are_translated(self):
        for status in ("missing", "placeholder"):
            problems = ss.preflight_credentials(
                deepgram_status=status, deepl_status="configured"
            )
            with self.subTest(status=status):
                self.assertIn(problems[0].message, strings._JA)

    def test_the_preflight_popup_is_shown_in_japanese(self):
        """Drives the real `_start_listening`, as items 46/47's wiring test does."""
        import alpha.utils.service_status as svc

        problems = ss.preflight_credentials(deepgram_status="missing", deepl_status="configured")

        class Stop(Exception):
            pass

        class Host:
            _start_listening = AlphaApp._start_listening

            def __init__(self):
                self._starting_listening = False

            def publish_error_event(self, *a, **k):
                pass

            def _strip_language_flag(self, *a, **k):
                raise Stop()

        previous = japanese()
        self.addCleanup(strings.set_language, previous)
        with mock.patch.object(svc, "preflight_credentials", return_value=problems), mock.patch.object(
            main_window, "_offer_key_setup", return_value=False
        ), mock.patch.object(main_window, "messagebox") as box:
            try:
                Host()._start_listening()
            except Stop:
                self.fail("the Start went past a missing key")
        title, message = box.showerror.call_args[0][:2]
        self.assertEqual(title, strings._JA["Deepgram API Key"])
        self.assertEqual(message, strings._JA[problems[0].message])


def _tk_available():
    try:
        import tkinter

        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_tk_available(), "Tk display unavailable in this environment")
class TheNewIndicatorTextFitsTheStrip(unittest.TestCase):
    """The strip test in test_microphone_toggle only ever measures "● Standby"."""

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

    def test_it_fits_in_both_languages(self):
        for language in ("en", "ja"):
            strings.set_language(language)
            for width in (900, 1400):
                self.app.geometry(f"{width}x800")
                self.app.update()
                self.app._apply_responsive_layout()
                self.app._set_dynamic_text(self.app.signal_label, "● Deepgram credit used up")
                self.app.update()
                label = self.app.signal_label
                if not label.winfo_ismapped():
                    continue
                strip_right = (
                    self.app.status_bar_frame.winfo_rootx() + self.app.status_bar_frame.winfo_width()
                )
                for name in ("mic_switch", "signal_label", "timer_label"):
                    widget = getattr(self.app, name, None)
                    if widget is None or not widget.winfo_ismapped():
                        continue
                    overflow = (widget.winfo_rootx() + widget.winfo_width()) - strip_right
                    self.assertLessEqual(overflow, 0, f"{name} {overflow}px past the strip ({language}, {width})")


if __name__ == "__main__":
    unittest.main()
