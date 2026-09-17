"""A keyterm rejection lasts one meeting, and "400" means HTTP status 400.

WHAT WAS BROKEN (audit 2026-09-14, bug #2)
-----------------------------------------
`_deepgram_on_error` decided three things from `"400" in str(err)`:

1. Whether to turn Japanese keyterms off (`_jp_keyterms_fallback_used = True`)
   and reconnect without them. Nothing in the app ever set that flag back to
   False -- not Start, not a successful connect -- so one rejection turned
   keyterms off for every later meeting until the app was restarted. The log
   line says "retrying once without keyterms".
2. Whether the error is recoverable.
3. Whether to STOP LISTENING: it posts a "Listening has been stopped" notice
   and returns without reconnecting.

A substring is not a status. websocket-client formats a failed handshake as
``Handshake status <code> <reason> -+-+- <response headers> -+-+- <body>``, so
the text of a 401 or a 503 carries Deepgram's `dg-request-id` -- a UUID whose
hex can contain "400" -- and any other error text can carry a number with 400
in it. Such an error turned keyterms off for the rest of the process, and once
they were off (or in every later meeting) the next one ended the meeting
instead of reconnecting.

Driven through the real `_deepgram_on_error`, the real `_start_listening` and
the real `_build_deepgram_url`.
"""

import sys
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from websocket import WebSocketBadStatusException  # noqa: E402

from alpha.transcription.deepgram_client import DeepgramClientMixin  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402

# A UUID in Deepgram's request-id shape whose hex happens to contain "400".
REQUEST_ID_WITH_400 = "7c2e9400-5b1d-4f3a-9e21-0d6c8b4a1f77"


def _handshake_error(status, reason, body=b"", request_id=REQUEST_ID_WITH_400):
    """Built with websocket-client's own message shape for a failed handshake."""
    headers = {"dg-request-id": request_id, "content-type": "application/json"}
    return WebSocketBadStatusException(
        "Handshake status %d %s -+-+- %s -+-+- %s" % (status, reason, headers, body),
        status,
        reason,
        headers,
        body,
    )


QUERY_REJECTED = _handshake_error(
    400, "Bad Request", b'{"err_code":"INVALID_QUERY_PARAMETER","err_msg":"bad keyterm"}'
)
KEY_REJECTED = _handshake_error(401, "Unauthorized", b'{"err_code":"INVALID_AUTH"}')
SERVICE_BUSY = _handshake_error(503, "Service Unavailable")
SERVICE_BUSY_ID_WITH_401 = _handshake_error(
    503, "Service Unavailable", request_id="5e0b2d4f-401a-4c9e-8d73-2a61f0c9b3e8"
)


class _GotPastPreflight(Exception):
    pass


class _Host(DeepgramClientMixin):
    """Real Deepgram error handling and URL building, and the real Start entry."""

    _start_listening = AlphaApp._start_listening

    def __init__(self):
        self._listen_language = "ja"
        self.is_listening = True
        self._stop_event = threading.Event()
        self._starting_listening = False
        self.reconnects = 0
        self.published = []

    def _schedule_reconnect(self):
        self.reconnects += 1

    def publish_error_event(self, message, source=None, recoverable=True):
        self.published.append((source, recoverable))

    # Reached only once the Start is past its preflight and session reset.
    def _strip_language_flag(self, *a, **k):
        raise _GotPastPreflight()

    @property
    def source_language(self):
        raise _GotPastPreflight()

    def keyterms_sent(self):
        query = parse_qs(urlparse(self._build_deepgram_url()).query)
        return bool(query.get("keyterm"))

    def start_next_meeting(self):
        import alpha.utils.service_status as svc

        real = svc.preflight_credentials
        svc.preflight_credentials = lambda **kw: []
        try:
            self._start_listening()
        except _GotPastPreflight:
            pass
        finally:
            svc.preflight_credentials = real
        self.is_listening = True


class _NoticeRecorder:
    def __init__(self):
        self.notices = []

    def post(self, event_type, payload=None, *a, **k):
        self.notices.append((event_type, dict(payload or {})))


class _Case(unittest.TestCase):
    def setUp(self):
        import alpha.utils.ui_event_bus as bus_module

        self._bus_module = bus_module
        self._real_get_bus = bus_module.get_ui_event_bus
        self.bus = _NoticeRecorder()
        bus_module.get_ui_event_bus = lambda: self.bus
        self.host = _Host()
        self.assertTrue(
            self.host.keyterms_sent(),
            "fixture: this configuration sends no keyterms, so nothing here would be measured",
        )

    def tearDown(self):
        self._bus_module.get_ui_event_bus = self._real_get_bus

    def stop_notices(self):
        return [p for t, p in self.bus.notices if p.get("action") == "stop_listening"]


class AKeytermRejectionLastsOneMeetingTest(_Case):
    def test_the_next_meeting_sends_keyterms_again(self):
        self.host._deepgram_on_error(None, QUERY_REJECTED)
        self.assertFalse(self.host.keyterms_sent(), "fixture: the rejection did not fall back")
        self.assertEqual(self.host.reconnects, 1, "fixture: the fallback did not reconnect")

        self.host.start_next_meeting()
        self.assertTrue(
            self.host.keyterms_sent(),
            "one keyterm rejection turned keyterms off for every later meeting "
            "until the app was restarted",
        )

    def test_within_the_same_meeting_the_fallback_still_holds(self):
        self.host._deepgram_on_error(None, QUERY_REJECTED)
        self.assertFalse(self.host.keyterms_sent())
        self.assertEqual(self.stop_notices(), [], "the first rejection ended the meeting")


class FourHundredMeansStatus400Test(_Case):
    def test_an_auth_error_whose_request_id_contains_400_leaves_keyterms_on(self):
        self.host._deepgram_on_error(None, KEY_REJECTED)
        self.assertTrue(self.host._dg_auth_failed, "fixture: the 401 was not recognised")
        self.assertTrue(
            self.host.keyterms_sent(),
            "a 401 turned keyterms off because its request id contains '400'",
        )

    def test_a_transient_error_whose_text_contains_400_reconnects_instead_of_ending_the_meeting(self):
        self.host._jp_keyterms_fallback_used = True  # keyterms already off this meeting
        self.host._deepgram_on_error(None, SERVICE_BUSY)
        self.assertEqual(
            self.stop_notices(),
            [],
            "a 503 ended the meeting ('Listening has been stopped') because its "
            "request id contains '400'",
        )
        self.assertEqual(self.host.reconnects, 1, "a transient 503 was not reconnected")
        self.assertEqual(self.host.published[-1], ("deepgram", True), "a 503 was reported unrecoverable")

    def test_a_plain_error_with_400_in_its_text_is_not_a_rejection(self):
        self.host._deepgram_on_error(None, ConnectionError("read timed out after 4000 ms"))
        self.assertTrue(self.host.keyterms_sent(), "a timeout message turned keyterms off")
        self.assertEqual(self.stop_notices(), [])
        self.assertEqual(self.host.reconnects, 1)

    def test_a_503_whose_request_id_contains_401_does_not_report_the_key_rejected(self):
        """The item 47 auth flag had the same substring test, for "401"/"403".

        It drives the status indicator: the operator is told the key was
        rejected, for a key that is fine, until the socket next opens.
        """
        self.host._deepgram_on_error(None, SERVICE_BUSY_ID_WITH_401)
        self.assertFalse(
            bool(getattr(self.host, "_dg_auth_failed", False)),
            "a 503 was reported as a rejected key because its request id contains '401'",
        )

    def test_a_real_400_still_falls_back_once_then_stops(self):
        """Guard: the behaviour for a genuine rejection is unchanged."""
        self.host._deepgram_on_error(None, QUERY_REJECTED)
        self.assertFalse(self.host.keyterms_sent())
        self.assertEqual(self.host.reconnects, 1)
        self.assertEqual(self.stop_notices(), [])

        self.host._deepgram_on_error(None, QUERY_REJECTED)
        self.assertEqual(len(self.stop_notices()), 1, "a second genuine 400 no longer stops")
        # Item 30: the stop notice owns the dialog. This event used to be
        # published unrecoverable too, which `_on_error_occurred` turns into a
        # second modal for the same error.
        self.assertEqual(self.host.published[-1], ("deepgram", True))

    def test_a_400_without_a_status_object_is_still_recognised(self):
        """Guard: an error that reached us as text only, in websocket-client's shape."""
        self.host._deepgram_on_error(None, Exception(str(QUERY_REJECTED)))
        self.assertFalse(self.host.keyterms_sent(), "a textual 'Handshake status 400' was missed")


if __name__ == "__main__":
    unittest.main()
