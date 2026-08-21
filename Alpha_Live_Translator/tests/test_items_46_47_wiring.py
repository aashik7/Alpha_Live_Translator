"""Items 46 and 47 are actually WIRED, not just present as library code.

WHY THIS EXISTS
---------------
`alpha/utils/service_status.py` shipped 2026-08-12 with `preflight_credentials`
(item 46) and `describe_connection` (item 47), both correct and both covered by
`tests/test_service_status.py`. They were reopened on 2026-08-16 because
grepping all six exported names across `alpha/` and `main.py` returned **zero
non-defining references**: the module had no production caller at all. So no
credential check ran at Start, and there was no status indicator -- the operator
saw neither.

That is the third time this project shipped correct-and-unreachable code (item
44's `commit_in_flight`, item 65's gated log, and these two), which is why the
ledger's own rule is: **before closing any item, grep for a production caller,
not just for the code.**

These tests therefore assert the WIRING, not the rules. The rules stay covered
by `test_service_status.py`; duplicating them here would create a second
authority over the same decision.

WHAT WAS REPLACED, NOT ADDED
----------------------------
`_start_listening` already decided the credential question itself, for Deepgram
only, with two hardcoded `key_status` branches. `signal_label` was written from
four places with two hardcoded strings. Both are now single-authority (§0 rule
2): the preflight decides Start, and `_sync_connection_indicator` is the only
writer of the indicator.

WHY THE INDICATOR TEST USES A RECORDER, NOT A REAL WIDGET
----------------------------------------------------------
`signal_label` is a `CTkLabel` and the call passes `text_color`, which a plain
`tk.Label` rejects. `_sync_connection_indicator` swallows widget errors on
purpose -- a status indicator must never break the 1s UI tick it rides on -- so
a `tk.Label` here would make every assertion pass vacuously. A recorder captures
exactly what the method decided to display.
"""

import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.service_status import CredentialProblem  # noqa: E402


class LabelRecorder:
    """Stands in for the `CTkLabel`; records what was displayed."""

    def __init__(self):
        self.calls = []

    def configure(self, **kw):
        self.calls.append(kw)

    @property
    def last(self):
        return self.calls[-1] if self.calls else {}


class TheStartPathAsksThePreflight(unittest.TestCase):
    """Item 46. Drives the real `AlphaApp._start_listening`."""

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        self.errors = []
        self.dialogs = []
        outer = self

        class Host:
            _start_listening = AlphaApp._start_listening

            def __init__(self):
                self._starting_listening = False
                self.published = []

            def publish_error_event(self, message, source=None, recoverable=True):
                self.published.append((message, source, recoverable))

            # Reached only if the preflight lets the Start through. Raising
            # here is how the test proves the preflight did NOT block.
            def _strip_language_flag(self, *a, **k):
                raise _GotPastPreflight()

            @property
            def source_language(self):
                raise _GotPastPreflight()

        self.Host = Host
        self.outer = outer

    def _run_start(self, problems):
        """Run `_start_listening` with the preflight forced to `problems`."""
        import alpha.utils.service_status as svc
        import alpha.ui.main_window as mw

        real_preflight = svc.preflight_credentials
        real_box = mw.messagebox
        shown = []

        class FakeBox:
            @staticmethod
            def showerror(title, message):
                shown.append((title, message))

        svc.preflight_credentials = lambda **kw: list(problems)
        mw.messagebox = FakeBox
        host = self.Host()
        got_past = False
        try:
            host._start_listening()
        except _GotPastPreflight:
            got_past = True
        except Exception:
            # Any other failure is downstream of the preflight, which still
            # means the preflight allowed the Start.
            got_past = True
        finally:
            svc.preflight_credentials = real_preflight
            mw.messagebox = real_box
        return host, shown, got_past

    def test_a_blocking_problem_stops_the_start(self):
        problems = [
            CredentialProblem(
                service="Deepgram",
                code="deepgram_key_missing",
                message="No Deepgram API key found.",
                blocks_start=True,
            )
        ]
        host, shown, got_past = self._run_start(problems)
        self.assertFalse(got_past, "Start continued past a blocking problem")
        self.assertEqual(len(shown), 1, "no error dialog was shown")
        self.assertIn("No Deepgram API key found.", shown[0][1])
        self.assertTrue(
            any("No Deepgram API key found." in m for m, _s, _r in host.published)
        )

    def test_a_non_blocking_problem_warns_without_stopping(self):
        """DeepL missing is degraded, not broken. It must not block Start, and
        it must not raise a modal that interrupts every transcript-only run."""
        problems = [
            CredentialProblem(
                service="DeepL",
                code="deepl_key_missing",
                message="No DeepL auth key found, so this session will transcribe but not translate.",
                blocks_start=False,
            )
        ]
        host, shown, got_past = self._run_start(problems)
        self.assertTrue(got_past, "a non-blocking problem stopped the Start")
        self.assertEqual(shown, [], "a non-blocking problem raised a modal dialog")
        self.assertTrue(
            any("not translate" in m for m, _s, _r in host.published),
            "the DeepL warning was never surfaced",
        )

    def test_a_clean_preflight_lets_the_start_through(self):
        _host, shown, got_past = self._run_start([])
        self.assertTrue(got_past)
        self.assertEqual(shown, [])

    def test_the_blocking_problem_wins_over_a_warning(self):
        problems = [
            CredentialProblem(
                service="DeepL",
                code="deepl_key_missing",
                message="No DeepL auth key found.",
                blocks_start=False,
            ),
            CredentialProblem(
                service="Deepgram",
                code="deepgram_key_missing",
                message="No Deepgram API key found.",
                blocks_start=True,
            ),
        ]
        _host, shown, got_past = self._run_start(problems)
        self.assertFalse(got_past)
        self.assertIn("Deepgram", shown[0][0])

    def test_a_previous_sessions_outage_does_not_leak_into_this_one(self):
        """`_dg_disconnected_at` is cleared ONLY by `_mark_deepgram_gap_if_any`,
        which runs on `_deepgram_on_open`. A session stopped while still
        disconnected leaves it set forever, and `_dg_auth_failed` survives the
        same way. Nothing read either across a session boundary until item 47's
        indicator did, so the next Start showed "Reconnecting" with a gap
        measured from the PREVIOUS session -- a number that grows without bound
        -- or "Key rejected" for a key already fixed.
        """
        import alpha.utils.service_status as svc

        real = svc.preflight_credentials
        svc.preflight_credentials = lambda **kw: []
        host = self.Host()
        host._dg_disconnected_at = 12345.0
        host._dg_auth_failed = True
        host._audio_device_changed = True
        host._connection_indicator_state = "failed"
        try:
            host._start_listening()
        except _GotPastPreflight:
            pass
        except Exception:
            pass
        finally:
            svc.preflight_credentials = real
        self.assertEqual(host._dg_disconnected_at, 0.0, "stale outage clock survived")
        self.assertFalse(host._dg_auth_failed, "stale auth rejection survived")
        self.assertFalse(
            host._audio_device_changed, "stale device-change warning survived"
        )
        self.assertIsNone(
            host._connection_indicator_state,
            "the indicator would not re-announce a problem in the new session",
        )

    def test_the_reset_happens_only_after_the_preflight_allows_the_start(self):
        """A Start refused for a missing key must not touch connection state:
        the operator has not started anything, so nothing has changed."""
        import alpha.utils.service_status as svc
        import alpha.ui.main_window as mw

        real = svc.preflight_credentials
        real_box = mw.messagebox

        class FakeBox:
            @staticmethod
            def showerror(title, message):
                pass

        svc.preflight_credentials = lambda **kw: [
            CredentialProblem(
                service="Deepgram",
                code="deepgram_key_missing",
                message="No Deepgram API key found.",
                blocks_start=True,
            )
        ]
        mw.messagebox = FakeBox
        host = self.Host()
        host._dg_disconnected_at = 12345.0
        try:
            host._start_listening()
        except Exception:
            pass
        finally:
            svc.preflight_credentials = real
            mw.messagebox = real_box
        self.assertEqual(host._dg_disconnected_at, 12345.0)

    def test_a_preflight_that_raises_never_blocks_the_start(self):
        """The guard must not become a gate on its own health."""
        import alpha.utils.service_status as svc

        real = svc.preflight_credentials

        def boom(**kw):
            raise RuntimeError("config exploded")

        svc.preflight_credentials = boom
        host = self.Host()
        got_past = False
        try:
            host._start_listening()
        except _GotPastPreflight:
            got_past = True
        except Exception:
            got_past = True
        finally:
            svc.preflight_credentials = real
        self.assertTrue(got_past, "a broken preflight prevented starting")


class _GotPastPreflight(Exception):
    """Raised by the stub that sits immediately after the preflight."""


class TheIndicatorReflectsTheConnection(unittest.TestCase):
    """Item 47. Drives the real `AlphaApp._sync_connection_indicator`."""

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        class Host:
            _sync_connection_indicator = AlphaApp._sync_connection_indicator
            _CONNECTION_INDICATOR_TEXT = AlphaApp._CONNECTION_INDICATOR_TEXT

            def __init__(self):
                self.signal_label = LabelRecorder()
                self.is_listening = True
                self._dg_disconnected_at = 0.0
                self._dg_reconnecting = False
                self._dg_auth_failed = False
                self.translation_worker = None
                # What `_sync_connection_indicator` reads to name the
                # captured device in the device-change message.
                self._diag_wasapi_device_name = "Speakers (Realtek Audio) [Loopback]"
                self.published = []

            def deepgram_gap_seconds(self):
                started = float(getattr(self, "_dg_disconnected_at", 0.0) or 0.0)
                return 12.0 if started else 0.0

            def publish_error_event(self, message, source=None, recoverable=True):
                self.published.append((message, source, recoverable))

        self.host = Host()

    def _text(self):
        return self.host.signal_label.last.get("text", "")

    def test_a_healthy_session_shows_signal_ok(self):
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Signal OK")
        self.assertEqual(self.host.published, [])

    def test_not_listening_shows_standby(self):
        self.host.is_listening = False
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Standby")

    def test_force_idle_shows_standby_even_while_listening(self):
        """Stop and finalise keep `is_listening` True for a while."""
        self.host._sync_connection_indicator(force_idle=True)
        self.assertEqual(self._text(), "● Standby")

    def test_a_dropped_socket_shows_reconnecting(self):
        self.host._dg_disconnected_at = 1.0
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Reconnecting")

    def test_a_rejected_key_shows_failed_and_outranks_a_reconnect(self):
        self.host._dg_disconnected_at = 1.0
        self.host._dg_reconnecting = True
        self.host._dg_auth_failed = True
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Key rejected")

    def test_translation_trouble_shows_degraded(self):
        class Worker:
            degraded = True
            status_message = "DeepL circuit open."

        self.host.translation_worker = Worker()
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Translation degraded")

    def test_a_reconnect_outranks_a_degraded_translation(self):
        """Losing words beats losing their translation. The ordering lives in
        `describe_connection`; this pins that the wiring does not undo it."""

        class Worker:
            degraded = True
            status_message = "DeepL circuit open."

        self.host.translation_worker = Worker()
        self.host._dg_disconnected_at = 1.0
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Reconnecting")

    def test_the_message_is_published_once_per_transition_not_per_tick(self):
        self.host._dg_disconnected_at = 1.0
        for _ in range(5):
            self.host._sync_connection_indicator()
        self.assertEqual(
            len(self.host.published), 1, "the indicator spammed the error surface"
        )

    def test_recovering_publishes_again_on_the_next_problem(self):
        self.host._dg_disconnected_at = 1.0
        self.host._sync_connection_indicator()
        self.host._dg_disconnected_at = 0.0
        self.host._sync_connection_indicator()
        self.host._dg_disconnected_at = 1.0
        self.host._sync_connection_indicator()
        self.assertEqual(len(self.host.published), 2)

    def test_the_device_message_names_the_device_this_session_captures(self):
        """Measured on the live runs of 2026-08-21: capture binds at Start and
        never follows, so "switch back" is only right if the operator switches
        back to the device THIS session bound. The old wording never said
        which, and in the second run the user switched to the device the FIRST
        session had used -- exactly the wrong move for the one running."""
        self.host._audio_device_changed = True
        self.host._sync_connection_indicator()
        msg = " ".join(m for m, _s, _r in self.host.published)
        self.assertIn("Realtek Audio", msg, f"the device was not named: {msg}")

    def test_the_device_message_leads_with_stop_and_start(self):
        """Stop/start is the reliable recovery; switching back is conditional.
        The reliable one must come first."""
        self.host._audio_device_changed = True
        self.host._sync_connection_indicator()
        msg = " ".join(m for m, _s, _r in self.host.published).lower()
        self.assertIn("stop and start", msg)
        self.assertLess(
            msg.index("stop and start"),
            msg.index("default again"),
            "the conditional advice was offered before the reliable one",
        )

    def test_the_device_message_survives_an_unknown_device_name(self):
        self.host._diag_wasapi_device_name = ""
        self.host._audio_device_changed = True
        self.host._sync_connection_indicator()
        msg = " ".join(m for m, _s, _r in self.host.published)
        self.assertIn("Stop and start", msg)
        self.assertNotIn("“”", msg, "an empty device name left empty quotes")

    def test_an_audio_device_change_reaches_the_indicator(self):
        """Item 73's detector, folded in. Windows moving the default output
        leaves every connection signal healthy, so without its own signal the
        indicator reports "Signal OK" over a device nothing is routed to."""
        self.host._audio_device_changed = True
        self.host._sync_connection_indicator()
        # NOT "● Reconnecting": the severity is the same but nothing is
        # reconnecting, and naming the state after its severity would tell the
        # operator to wait for a recovery that will never come.
        self.assertEqual(self._text(), "● Audio device changed")
        self.assertTrue(
            any("default audio output" in m for m, _s, _r in self.host.published),
            f"the operator was never told what to do: {self.host.published}",
        )

    def test_a_device_change_outranks_a_degraded_translation(self):
        class Worker:
            degraded = True
            status_message = "DeepL circuit open."

        self.host.translation_worker = Worker()
        self.host._audio_device_changed = True
        self.host._sync_connection_indicator()
        self.assertTrue(
            any("default audio output" in m for m, _s, _r in self.host.published)
        )

    def test_a_rejected_key_still_outranks_a_device_change(self):
        self.host._audio_device_changed = True
        self.host._dg_auth_failed = True
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Key rejected")

    def test_restoring_the_device_clears_the_warning(self):
        self.host._audio_device_changed = True
        self.host._sync_connection_indicator()
        self.host._audio_device_changed = False
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Signal OK")

    def test_a_socket_reconnect_still_says_reconnecting(self):
        """The device wording must not leak onto an ordinary reconnect."""
        self.host._dg_disconnected_at = 1.0
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Reconnecting")

    def test_a_device_change_during_a_reconnect_names_the_device(self):
        """Both true: `describe_connection` gives the device message, so the
        label must agree with the sentence the operator is shown."""
        self.host._dg_disconnected_at = 1.0
        self.host._dg_reconnecting = True
        self.host._audio_device_changed = True
        self.host._sync_connection_indicator()
        self.assertEqual(self._text(), "● Audio device changed")
        self.assertTrue(
            any("default audio output" in m for m, _s, _r in self.host.published)
        )

    def test_a_missing_label_is_not_an_error(self):
        self.host.signal_label = None
        self.host._sync_connection_indicator()

    def test_the_gap_length_reaches_the_message(self):
        self.host._dg_disconnected_at = 1.0
        self.host._sync_connection_indicator()
        self.assertTrue(
            any("12s" in m for m, _s, _r in self.host.published),
            f"gap seconds never reached the operator: {self.host.published}",
        )


class ARejectedKeyIsRecorded(unittest.TestCase):
    """The runtime half of item 46, read by item 47's indicator."""

    def setUp(self):
        from alpha.ui.main_window import AlphaApp

        class Host:
            _deepgram_on_error = AlphaApp._deepgram_on_error

            def __init__(self):
                self._stop_event = threading.Event()
                self.is_listening = True
                self._dg_auth_failed = False

        self.host = Host()

    def _err(self, text):
        try:
            self.host._deepgram_on_error(None, Exception(text))
        except Exception:
            pass
        return self.host._dg_auth_failed

    def test_a_401_is_recorded_as_an_auth_failure(self):
        self.assertTrue(self._err("Handshake status 401 Unauthorized"))

    def test_a_403_is_recorded_as_an_auth_failure(self):
        self.assertTrue(self._err("server returned 403 Forbidden"))

    def test_invalid_credentials_is_recorded(self):
        self.assertTrue(self._err("Invalid credentials supplied"))

    def test_an_ordinary_network_error_is_not_an_auth_failure(self):
        self.assertFalse(self._err("Connection reset by peer"))

    def test_a_timeout_is_not_an_auth_failure(self):
        self.assertFalse(self._err("timed out waiting for handshake"))


class TheModuleHasProductionCallers(unittest.TestCase):
    """The check the ledger says to run before closing either item.

    Both functions were correct, tested, and referenced by nothing. A grep for
    the code would have passed; only a grep for a CALLER catches it.
    """

    def _source(self):
        return (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8", errors="replace"
        )

    def test_preflight_credentials_is_called(self):
        self.assertIn("preflight_credentials", self._source())

    def test_describe_connection_is_called(self):
        self.assertIn("describe_connection", self._source())

    def test_the_indicator_has_exactly_one_writer(self):
        """Four places used to write `signal_label` directly.

        This scans the WHOLE `alpha/` tree, not just `main_window.py`. The
        narrower version missed `alpha/audio/wasapi.py`, where item 73's
        device-change notice painted the label directly -- and once item 47
        started repainting it every second from `_update_timer`, that notice
        survived about one second before being overwritten with "Signal OK".

        The invariant is deliberately "no other module MENTIONS `signal_label`",
        not "no other module contains the string `signal_label.configure`". The
        offending code read `label = getattr(self, "signal_label", None)` and
        then called `label.configure(...)`, so a search for the composed
        attribute access would have sailed straight past it. Owning the widget
        in one module is the property that actually holds.
        """
        offenders = []
        for path in (PROJECT_ROOT / "alpha").rglob("*.py"):
            if path.name == "main_window.py":
                continue
            # Comments may name it -- explaining WHY a module must not touch
            # the label is the opposite of touching it.
            code = "\n".join(
                line.split("#", 1)[0]
                for line in path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            )
            if "signal_label" in code:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual(
            offenders,
            [],
            "signal_label is touched outside main_window.py; feed "
            "_sync_connection_indicator a signal instead of painting the label",
        )

    def test_the_indicator_owns_the_label_inside_main_window_too(self):
        """Within `main_window.py`, only the single owner may write it."""
        source = self._source()
        self.assertEqual(
            source.count("signal_label.configure"),
            0,
            "a direct signal_label writer came back; route it through "
            "_sync_connection_indicator instead",
        )


if __name__ == "__main__":
    unittest.main()
