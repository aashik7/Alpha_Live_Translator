"""Deepgram refusing a Start must say so, at once, with the real reason.

Field report, 26.5.25, a shared build: "Start does nothing." The bundle the
operator sent showed six Start presses in half an hour, every one of them the
same shape:

    09:37:37.051  start_listening_clicked
    09:37:38.141  websocket: Handshake status 401 Unauthorized
                  {"err_code":"INVALID_AUTH","err_msg":"Invalid credentials."}
    09:38:07.642  Error starting listening: Deepgram sender not ready within 30s
                  -> status "Stopped", no dialog

Deepgram answered in about one second. The app then waited the rest of thirty,
and told the operator nothing at all. Three defects line up to produce that:

1. `_deepgram_on_error` decides "the operator asked to stop" from, among other
   things, `not is_listening`. `is_listening` is False for the whole of Start,
   so the 401 took the stop branch and returned before item 47's auth check
   ever ran. In the operator's own evidence: `DEEPGRAM_CLOSE_NORMAL
   reason=stop_requested` carrying the 401 text, and zero
   `DEEPGRAM_AUTH_REJECTED` records across all six attempts. The same branch
   swallows every other refusal at Start too -- out of credits, rate limited.
2. The Start worker's wait for the sender watched only `_stop_event`, so even a
   recognised refusal would have been sat out for the full 30 s.
3. `_finish_start_listening` printed the error and set "Stopped" -- no dialog,
   and nothing offered the key dialog a keyless build has for exactly this.
   The Start preflight does offer it, but only for a key that is MISSING or a
   placeholder; a well-formed key that Deepgram refuses sails past it.

The review of the first version of this fix found a fourth, older defect, in
the key dialog itself: it closed its window with `destroy()` alone, so the
nested `mainloop()` it runs never returned while the main window existed. Opened
from inside the UI event bus drain, that stopped the bus for the rest of the
process. `TheKeyDialogReturnsWithAMainWindowOpen` pins that down.
"""

import ast
import sys
import tempfile
import threading
import time
import tkinter
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import deepgram_client  # noqa: E402
from alpha.transcription.deepgram_client import (  # noqa: E402
    DeepgramKeyRejected,
    DeepgramRefusedStart,
    deepgram_start_refusal,
)
from alpha.ui import key_setup, main_window  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402

# Verbatim from the operator's console log, request id and all.
FIELD_401 = (
    "Handshake status 401 Unauthorized -+-+- {'content-type': 'application/json', "
    "'dg-error': 'Invalid credentials.', 'vary': 'accept-encoding', "
    "'dg-request-id': '01a0accc-307e-7171-b0e9-5eb486f4e252', 'content-length': '112', "
    "'date': 'Thu, 17 Sep 2026 00:37:38 GMT'} -+-+- "
    "b'{\"err_code\":\"INVALID_AUTH\",\"err_msg\":\"Invalid credentials.\","
    "\"request_id\":\"01a0accc-307e-7171-b0e9-5eb486f4e252\"}' - goodbye"
)
# Same shape, other refusals a shared-build operator can plausibly hit.
OUT_OF_CREDITS_402 = (
    "Handshake status 402 Payment Required -+-+- {'dg-request-id': 'abc'} -+-+- "
    "b'{\"err_code\":\"ASR_PAYMENT_REQUIRED\",\"err_msg\":\"Project does not have enough credits.\"}'"
    " - goodbye"
)
# A corporate proxy's 403: no Deepgram header, no Deepgram body.
PROXY_403 = "Handshake status 403 Forbidden -+-+- {'server': 'Zscaler/6.2'} -+-+- b'' - goodbye"


class ClassifyingARefusal(unittest.TestCase):
    def test_the_field_401_is_a_rejected_key(self):
        refusal = deepgram_start_refusal(None, FIELD_401)
        self.assertIsInstance(refusal, DeepgramKeyRejected)
        self.assertEqual(refusal.status, 401)
        self.assertIn("Invalid credentials.", str(refusal))

    def test_out_of_credits_is_a_refusal_but_not_a_key_problem(self):
        refusal = deepgram_start_refusal(None, OUT_OF_CREDITS_402)
        self.assertIsInstance(refusal, DeepgramRefusedStart)
        self.assertNotIsInstance(refusal, DeepgramKeyRejected)
        self.assertEqual(refusal.status, 402)
        self.assertIn("enough credits", str(refusal))

    def test_a_proxy_403_is_not_blamed_on_the_key(self):
        """No Deepgram marker means Deepgram never saw the key."""
        refusal = deepgram_start_refusal(None, PROXY_403)
        self.assertIsInstance(refusal, DeepgramRefusedStart)
        self.assertNotIsInstance(refusal, DeepgramKeyRejected)

    def test_a_network_error_is_not_a_refusal(self):
        self.assertIsNone(deepgram_start_refusal(None, "Connection reset by peer"))

    def test_a_refusal_is_still_a_runtime_error(self):
        """Anything that caught the old generic start failure keeps catching it."""
        self.assertTrue(issubclass(DeepgramRefusedStart, RuntimeError))


class ErrorHost:
    """The real error handler, in the state the window is in during Start."""

    _deepgram_on_error = AlphaApp._deepgram_on_error

    def __init__(self, *, starting=True, listening=False):
        self._stop_event = threading.Event()
        self.is_listening = listening
        self._starting_listening = starting
        self._is_stopping = False
        self._dg_stop_sending_audio = False
        self._dg_auth_failed = False
        self._dg_start_refusal = None
        self._listen_language = "ja"
        self._jp_keyterms_fallback_used = False
        self.reconnects = 0

    def _schedule_reconnect(self):
        self.reconnects += 1

    def publish_error_event(self, *args, **kwargs):
        pass

    def error(self, text):
        self._deepgram_on_error(None, Exception(text))
        return self


class TheRefusalIsSeenDuringStart(unittest.TestCase):
    def test_the_field_401_is_recorded_while_starting(self):
        host = ErrorHost().error(FIELD_401)
        self.assertTrue(host._dg_auth_failed, "the 401 was treated as a stop request")
        self.assertIsInstance(host._dg_start_refusal, DeepgramKeyRejected)

    def test_out_of_credits_is_recorded_while_starting(self):
        host = ErrorHost().error(OUT_OF_CREDITS_402)
        self.assertIsInstance(host._dg_start_refusal, DeepgramRefusedStart)
        self.assertFalse(host._dg_auth_failed)

    def test_a_network_error_while_starting_records_nothing(self):
        host = ErrorHost().error("Connection reset by peer")
        self.assertIsNone(host._dg_start_refusal)
        self.assertFalse(host._dg_auth_failed)

    def test_a_live_session_does_not_record_a_start_refusal(self):
        """Mid-session, the reconnect loop owns a refused handshake, as before."""
        host = ErrorHost(starting=False, listening=True).error(FIELD_401)
        self.assertTrue(host._dg_auth_failed)
        self.assertIsNone(host._dg_start_refusal)

    def test_an_operator_stop_still_takes_the_quiet_path(self):
        host = ErrorHost(starting=False, listening=True)
        host._stop_event.set()
        host.error("Connection to remote host was lost.")
        self.assertEqual(host.reconnects, 0)
        self.assertFalse(host._dg_auth_failed)
        self.assertIsNone(host._dg_start_refusal)


class WaitHost:
    _wait_for_deepgram_sender = AlphaApp._wait_for_deepgram_sender

    def __init__(self):
        self._stop_event = threading.Event()
        self._latency_sender_loop_alive = False
        self._dg_start_refusal = None


class TheStartStopsWaitingAtOnce(unittest.TestCase):
    def test_a_refusal_ends_the_wait_in_well_under_the_timeout(self):
        host = WaitHost()
        refusal = deepgram_start_refusal(None, FIELD_401)
        threading.Timer(0.2, lambda: setattr(host, "_dg_start_refusal", refusal)).start()
        started = time.perf_counter()
        with self.assertRaises(DeepgramKeyRejected) as caught:
            host._wait_for_deepgram_sender(timeout_s=30.0)
        waited = time.perf_counter() - started
        self.assertLess(waited, 2.0, f"sat out {waited:.1f}s of a known refusal")
        self.assertIs(caught.exception, refusal, "the real reason was replaced")

    def test_a_ready_sender_wins_over_a_stale_refusal(self):
        host = WaitHost()
        host._latency_sender_loop_alive = True
        host._dg_start_refusal = deepgram_start_refusal(None, FIELD_401)
        host._wait_for_deepgram_sender(timeout_s=1.0)

    def test_a_stop_still_aborts_with_its_own_message(self):
        host = WaitHost()
        host._stop_event.set()
        with self.assertRaisesRegex(RuntimeError, "aborted before sender ready"):
            host._wait_for_deepgram_sender(timeout_s=1.0)

    def test_a_silent_socket_still_times_out_the_old_way(self):
        host = WaitHost()
        with self.assertRaisesRegex(RuntimeError, "sender not ready within"):
            host._wait_for_deepgram_sender(timeout_s=0.2)


class TheOperatorIsToldWhatIsWrong(unittest.TestCase):
    """`_explain_start_failure`, with only the dialogs replaced."""

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
        self.offer = offer.start()
        self.addCleanup(offer.stop)

    def _explain(self, error, *, keyless):
        with mock.patch.object(key_setup, "should_prompt", return_value=keyless):
            main_window._explain_start_failure(error)

    def test_a_keyed_build_names_the_file_to_fix(self):
        from alpha.config import PROJECT_ROOT as APP_ROOT

        self._explain(deepgram_client.deepgram_start_refusal(None, FIELD_401), keyless=False)
        self.assertEqual([kind for kind, _ in self.calls], ["error"])
        title, message = self.calls[0][1][:2]
        self.assertIn("Deepgram", title)
        self.assertIn("rejected", message)
        self.assertIn("401", message)
        self.assertIn(str(APP_ROOT / ".env"), message)

    def test_a_keyless_build_says_why_before_it_asks_again(self):
        """The first-run dialog alone looked like the app had forgotten the keys."""
        self._explain(deepgram_client.deepgram_start_refusal(None, FIELD_401), keyless=True)
        self.assertEqual([kind for kind, _ in self.calls], ["error", "offer"])
        self.assertIn("rejected", self.calls[0][1][1])
        self.assertIn("deepl", self.calls[1][1], "the working DeepL key was not carried over")

    def test_out_of_credits_is_reported_as_itself(self):
        """Item 30 changed two things here, both deliberately.

        A keyless build now offers the key dialog after the explanation: a key
        from an account that still has credit cures it. And the popup shows
        Deepgram's status and error code, not its English sentence -- that stays
        in the log, because the popup follows the display language.
        """
        self._explain(deepgram_client.deepgram_start_refusal(None, OUT_OF_CREDITS_402), keyless=True)
        self.assertEqual([kind for kind, _ in self.calls], ["error", "offer"])
        message = self.calls[0][1][1]
        self.assertIn("HTTP 402 ASR_PAYMENT_REQUIRED", message)
        self.assertIn("trial", message.lower())
        self.assertNotIn("internet", message.lower())

    def test_a_failure_that_is_not_deepgrams_raises_no_new_dialog(self):
        """A WASAPI failure already shows its own dialog; a second one contradicted it."""
        self._explain(OSError("No default WASAPI loopback device available."), keyless=True)
        self._explain(RuntimeError("Deepgram sender not ready within 30s"), keyless=True)
        self.assertEqual(self.calls, [])

    def test_success_raises_no_dialog(self):
        self._explain(None, keyless=True)
        self.assertEqual(self.calls, [])


class FinishHost:
    """The real `_finish_start_listening`, recording what happens in what order."""

    _finish_start_listening = AlphaApp._finish_start_listening

    def __init__(self, events):
        self.events = events
        self._starting_listening = True
        self.status_text_label = None

    def _write_startup_failure_summary(self, step, error):
        pass

    def _stop_listening(self, graceful=True):
        self.events.append(("stop", graceful))

    def _set_dynamic_text(self, *args, **kwargs):
        pass

    def after(self, ms, callback):
        self.events.append(("after", ms))
        callback()


class TheDialogComesAfterTheTeardown(unittest.TestCase):
    def test_order_on_a_refused_start(self):
        events = []
        with mock.patch.object(
            main_window,
            "_explain_start_failure",
            side_effect=lambda error: events.append(("explain", type(error).__name__)),
        ):
            FinishHost(events)._finish_start_listening(
                deepgram_start_refusal(None, FIELD_401)
            )
        self.assertEqual(
            events,
            [("stop", False), ("after", 0), ("explain", "DeepgramKeyRejected")],
            "the dialog must run after teardown, and outside the UI bus drain",
        )

    def test_the_explanation_is_reachable_only_from_the_error_branch(self):
        """Structural, so a dialog on a SUCCESSFUL start cannot slip in.

        Parsed rather than grepped: a comment mentioning the call must not
        satisfy this, and an equivalent spelling must not fail it.
        """
        tree = ast.parse(Path(main_window.__file__).read_text(encoding="utf-8"))
        finish = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_finish_start_listening"
        )
        branch = next(
            node
            for node in finish.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.ops[0], ast.IsNot)
        )

        def calls(nodes, name):
            found = []
            for node in nodes:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and sub.id == name:
                        found.append(sub.lineno)
                    if isinstance(sub, ast.Attribute) and sub.attr == name:
                        found.append(sub.lineno)
            return found

        outside = [n for n in finish.body if n is not branch]
        self.assertEqual(calls(outside, "_explain_start_failure"), [])
        inside = calls(branch.body, "_explain_start_failure")
        self.assertTrue(inside, "the error branch never explains the failure")
        self.assertLess(max(calls(branch.body, "_stop_listening")), min(inside))


class TheWiringIsPresent(unittest.TestCase):
    def test_the_start_worker_really_calls_the_shared_wait(self):
        tree = ast.parse(Path(main_window.__file__).read_text(encoding="utf-8"))
        worker = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_start_listening_worker"
        )
        called = [
            sub
            for sub in ast.walk(worker)
            if isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "_wait_for_deepgram_sender"
        ]
        self.assertEqual(len(called), 1)
        names = {sub.id for sub in ast.walk(worker) if isinstance(sub, ast.Name)}
        self.assertNotIn("sender_deadline", names, "the old inline wait is still there")

    def test_the_start_resets_the_previous_refusal(self):
        source = Path(main_window.__file__).read_text(encoding="utf-8")
        start = source[source.index("    def _start_listening(self):"):]
        start = start[: start.index("\n    def ", 10)]
        self.assertIn("self._dg_start_refusal = None", start)


def _tk_available():
    try:
        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_tk_available(), "Tk display unavailable in this environment")
class TheKeyDialogReturnsWithAMainWindowOpen(unittest.TestCase):
    """The review's high finding, on the real dialog.

    `ask_for_keys` makes a second `tk.Tk()` and runs `mainloop()` on it. With
    `destroy()` alone that loop keeps going as long as ANY Tk main window
    exists -- and during a Start failure the app's own window always does.
    Measured before the fix: the dialog closed, `ask_for_keys` did not return
    until something else ended the loop.
    """

    def _run(self, act):
        main = tkinter.Tk()
        main.withdraw()
        self.addCleanup(lambda: _safe_destroy(main))
        outcome = {}
        real_tk = tkinter.Tk

        class Dialog(real_tk):
            def __init__(inner, *a, **kw):
                super().__init__(*a, **kw)
                inner.after(150, lambda: act(inner))

        def open_dialog():
            tkinter.Tk = Dialog
            try:
                with tempfile.TemporaryDirectory() as folder:
                    outcome["result"] = key_setup.ask_for_keys(Path(folder) / ".env")
                    outcome["env"] = (Path(folder) / ".env").is_file()
            finally:
                tkinter.Tk = real_tk
                main.after(0, main.quit)

        def watchdog():
            # Ending the main loop from here also ends a stuck nested loop, and
            # `ask_for_keys` then returns normally -- so the result alone cannot
            # tell a working dialog from a stuck one. This flag can.
            outcome["watchdog"] = True
            main.quit()

        main.after(20, open_dialog)
        guard = main.after(3000, watchdog)
        main.mainloop()
        try:
            main.after_cancel(guard)
        except Exception:
            pass
        self.assertNotIn(
            "watchdog", outcome, "ask_for_keys did not return while the main window was open"
        )
        return outcome

    def test_escape_returns(self):
        def press_escape(dialog):
            # A real Escape only arrives while the dialog has keyboard focus, and
            # a test process's window usually does not: measured, `focus_get()`
            # was None and a bare `event_generate` on the root was dropped. Give
            # it focus first, as a person pressing the key would have.
            entry = next(w for w in _descendants(dialog) if isinstance(w, tkinter.Entry))
            entry.focus_force()
            dialog.update()
            entry.event_generate("<Escape>", when="tail")

        outcome = self._run(press_escape)
        self.assertIs(outcome["result"], False)

    def test_the_quit_button_returns(self):
        outcome = self._run(lambda d: _button(d, "Quit").invoke())
        self.assertIs(outcome["result"], False)

    def test_the_window_close_box_returns(self):
        outcome = self._run(lambda d: d.tk.call(d.protocol("WM_DELETE_WINDOW")))
        self.assertIs(outcome["result"], False)

    def test_saving_returns_and_writes(self):
        def fill_and_save(dialog):
            entries = [w for w in _descendants(dialog) if isinstance(w, tkinter.Entry)]
            entries[0].insert(0, "a" * 40)
            entries[1].insert(0, "b" * 36 + ":fx")
            _button(dialog, "Save and start").invoke()

        # `write_env` also sets os.environ; keep the fake keys out of this process.
        with mock.patch.dict("os.environ", {}, clear=False):
            outcome = self._run(fill_and_save)
        self.assertIs(outcome["result"], True)
        self.assertTrue(outcome["env"])


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def _button(dialog, text):
    return next(
        w for w in _descendants(dialog) if isinstance(w, tkinter.Button) and w.cget("text") == text
    )


def _safe_destroy(root):
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
