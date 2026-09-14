"""While a close waits for the transcript, the window must say so.

WHY
---
Since 26.5.21 (item 22) a close waits for the Stop worker to finish writing the
transcript -- usually a few seconds, capped at 30. A second click on the close
button still force-closes at once, which is the escape hatch, and which also
kills the worker mid-write.

Before this change the window gave the user every reason to click again:

  * closing mid-meeting showed "Finalising…", and five seconds later the stop
    UI watchdog replaced it with "Stopped. Diagnostics may still be saving." and
    re-enabled Start Listening -- a window that looks finished and has not
    closed;
  * closing after Stop changed nothing on screen at all.

So while a close is waiting the status line says the transcript is being saved
and that the window closes by itself, the poll puts that back if anything
(the watchdog restore, a status event) overwrites it, the listen buttons stay
disabled, and Start is ignored.

Drives the real `_on_close`, `_poll_window_close_ready`, `_set_dynamic_text`,
`_restore_ui_after_stop_watchdog`, `_set_stopped_ui_state`,
`_set_listen_button_state` and `toggle_listening`.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui import main_window  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402
from alpha.utils import stop_finalize_worker as sfw  # noqa: E402


def _borrow(name):
    impl = getattr(AlphaApp, name, None)
    if impl is None:
        raise AttributeError("%s has not been written yet" % name)
    return impl


def _closing_text():
    text = getattr(main_window, "WINDOW_CLOSING_STATUS_TEXT", None)
    if text is None:
        raise AssertionError("no closing status text exists")
    return text


class _Widget:
    """Stands in for a CTk label or button: remembers what it was configured with."""

    def __init__(self, **options):
        self.options = dict(options)

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def cget(self, key):
        return self.options.get(key)


class _Host:
    def __init__(self, *, listening):
        self.is_listening = listening
        self._is_stopping = False
        self._is_finalizing = False
        self._starting_listening = False
        self._window_close_pending = False
        self._stop_finalize_started = False
        self.translation_worker = None
        self.live_indicator = None  # `_update_status_bar` returns early without it
        self.status_text_label = _Widget(text="Listening — capturing audio")
        self.listen_button = _Widget(text="Stop Listening", state="normal")
        self.listen_button_menu = _Widget(text="Stop Listening", state="normal")
        self.shutdown_threads = []
        self.pending_after = []
        self.starts = 0
        self._next_id = 0

    # real
    def _on_close(self):
        return _borrow("_on_close")(self)

    def _poll_window_close_ready(self, *args):
        return _borrow("_poll_window_close_ready")(self, *args)

    def _cancel_recurring_ui_jobs(self):
        return _borrow("_cancel_recurring_ui_jobs")(self)

    def _set_dynamic_text(self, widget, source, **kwargs):
        return _borrow("_set_dynamic_text")(self, widget, source, **kwargs)

    def _restore_ui_after_stop_watchdog(self, **kwargs):
        return _borrow("_restore_ui_after_stop_watchdog")(self, **kwargs)

    def _set_stopped_ui_state(self):
        return _borrow("_set_stopped_ui_state")(self)

    def _set_listen_button_state(self, listening):
        return _borrow("_set_listen_button_state")(self, listening)

    def _update_status_bar(self, listening=False):
        return _borrow("_update_status_bar")(self, listening=listening)

    def toggle_listening(self):
        return _borrow("toggle_listening")(self)

    # stubs
    def _set_mic_switch_enabled(self, enabled):
        pass

    def _begin_graceful_stop(self):
        """What the real stop leaves on screen: flags set, buttons disabled, "Finalising…"."""
        self.is_listening = False
        self._is_stopping = True
        self._is_finalizing = True
        for button in (self.listen_button, self.listen_button_menu):
            button.configure(text="Finalising…", state="disabled")
        self._set_dynamic_text(self.status_text_label, "Finalising…")

    def _start_listening(self):
        self.starts += 1

    def _note_start_blocked_by_finalize(self):
        pass

    def _shutdown_and_destroy(self):
        self.shutdown_threads.append(threading.current_thread())

    def after(self, ms, fn, *args):
        self._next_id += 1
        job = "after#%d" % self._next_id
        self.pending_after.append((job, fn, args))
        return job

    def after_cancel(self, job):
        self.pending_after = [p for p in self.pending_after if p[0] != job]

    def destroy(self):
        pass

    def pump(self):
        jobs, self.pending_after = self.pending_after, []
        for _job, fn, args in jobs:
            fn(*args)

    @property
    def status_source(self):
        return getattr(self.status_text_label, "_alpha_text_source", None)


class _WithAWorker(unittest.TestCase):
    def setUp(self):
        with sfw._state_lock:
            self._saved = sfw._stop_state.get("finalize_thread")
        self.release = threading.Event()
        started = threading.Event()
        self.worker = threading.Thread(
            target=lambda: (started.set(), self.release.wait(15.0)), name="StopFinalizeWorker", daemon=True
        )
        self.worker.start()
        started.wait(5.0)
        with sfw._state_lock:
            sfw._stop_state["finalize_thread"] = self.worker

    def tearDown(self):
        self.release.set()
        self.worker.join(timeout=5.0)
        with sfw._state_lock:
            sfw._stop_state["finalize_thread"] = self._saved


class TheWindowSaysItIsSavingTest(_WithAWorker):
    def test_closing_mid_meeting_says_the_transcript_is_saving(self):
        host = _Host(listening=True)
        host._on_close()
        self.assertEqual(host.shutdown_threads, [], "fixture: the close did not wait")
        self.assertEqual(
            host.status_source,
            _closing_text(),
            "the window gave no sign it was saving the transcript: %r" % host.status_source,
        )

    def test_closing_after_stop_says_the_transcript_is_saving(self):
        host = _Host(listening=False)
        host._set_dynamic_text(host.status_text_label, "Stopped. Diagnostics may still be saving.")
        host._on_close()
        self.assertEqual(host.shutdown_threads, [], "fixture: the close did not wait")
        self.assertEqual(host.status_source, _closing_text(), "closing after Stop changed nothing on screen")

    def test_the_message_comes_back_after_the_5s_watchdog_restore(self):
        host = _Host(listening=True)
        host._on_close()
        host._restore_ui_after_stop_watchdog(timed_out=True)  # the real 5 s restore
        self.assertEqual(
            host.status_source,
            "Stopped. Diagnostics may still be saving.",
            "fixture: the real restore did not overwrite the status line",
        )
        host.pump()  # one close-poll tick
        self.assertEqual(
            host.status_source,
            _closing_text(),
            "after the 5 s restore the window said 'Stopped' and stayed open",
        )

    def test_the_listen_buttons_stay_disabled_while_closing(self):
        host = _Host(listening=True)
        host._on_close()
        host._restore_ui_after_stop_watchdog(timed_out=True)
        self.assertEqual(host.listen_button.cget("state"), "normal", "fixture: the restore did not re-enable Start")
        host.pump()
        self.assertEqual(host.listen_button.cget("state"), "disabled", "Start looked usable in a closing window")
        self.assertEqual(host.listen_button_menu.cget("state"), "disabled")

    def test_the_message_is_translated(self):
        from alpha.ui import strings

        self.assertIn(_closing_text(), strings._TABLES["ja"], "the closing message has no Japanese text")


class StartIsIgnoredWhileClosingTest(unittest.TestCase):
    def test_start_does_nothing_once_a_close_is_pending(self):
        with sfw._state_lock:
            saved = sfw._stop_state.get("finalize_thread")
            sfw._stop_state["finalize_thread"] = None  # the worker has just finished
        try:
            host = _Host(listening=False)
            host._window_close_pending = True
            host.toggle_listening()
            self.assertEqual(host.starts, 0, "a new meeting started in a window that is closing")
        finally:
            with sfw._state_lock:
                sfw._stop_state["finalize_thread"] = saved

    def test_start_still_works_when_no_close_is_pending(self):
        with sfw._state_lock:
            saved = sfw._stop_state.get("finalize_thread")
            sfw._stop_state["finalize_thread"] = None
        try:
            host = _Host(listening=False)
            host.toggle_listening()
            self.assertEqual(host.starts, 1)
        finally:
            with sfw._state_lock:
                sfw._stop_state["finalize_thread"] = saved


class AnIdleCloseIsUnchangedTest(unittest.TestCase):
    def test_closing_an_idle_app_still_closes_at_once_without_a_message(self):
        with sfw._state_lock:
            saved = sfw._stop_state.get("finalize_thread")
            sfw._stop_state["finalize_thread"] = None
        try:
            host = _Host(listening=False)
            host._set_dynamic_text(host.status_text_label, "Ready to listen")
            host._on_close()
            self.assertEqual(host.shutdown_threads, [threading.main_thread()])
            self.assertEqual(host.status_source, "Ready to listen")
        finally:
            with sfw._state_lock:
                sfw._stop_state["finalize_thread"] = saved


if __name__ == "__main__":
    unittest.main()
