# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(r"C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator\alpha\ui\main_window.py")
text = p.read_text(encoding="utf-8")

old = '''    def _set_starting_status(self):
        """Show immediate Starting feedback while Deepgram/audio initialize off-UI."""
        if self.status_text_label is not None:
            self.status_text_label.configure(
                text="Starting…",
                text_color=COLORS["text_primary"],
            )
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("start_ui_acknowledged_at")
            lpp.mark("listening_state_visible_at".replace(
                "listening_state_visible_at", "start_ui_acknowledged_at"
            ))
        except Exception:
            pass
        # Keep button responsive lock visible immediately.
        try:
            self._set_listen_button_state(False)
            if self.listen_button is not None:
                self.listen_button.configure(text="Starting…", state="disabled")
            if getattr(self, "listen_button_menu", None) is not None:
                self.listen_button_menu.configure(text="Starting…", state="disabled")
        except Exception:
            pass
'''

new = '''    def _set_starting_status(self):
        """Show immediate Starting feedback while Deepgram/audio initialize off-UI."""
        if self.status_text_label is not None:
            self.status_text_label.configure(
                text="Starting…",
                text_color=COLORS["text_primary"],
            )
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("start_ui_acknowledged_at")
        except Exception:
            pass
        try:
            if self.listen_button is not None:
                self.listen_button.configure(text="Starting…", state="disabled")
            if getattr(self, "listen_button_menu", None) is not None:
                self.listen_button_menu.configure(text="Starting…", state="disabled")
        except Exception:
            pass
'''

if old in text:
    text = text.replace(old, new, 1)
    print("fixed_starting_status")
else:
    print("OLD_START_STATUS_NOT_FOUND")

needle = "        self._starting_listening = True\n        self._set_starting_status()\n"
inject = """        try:
            from alpha.utils import live_pipeline_profile as lpp

            sid = f\"sess-{time.time_ns()}\"
            self._live_session_id = sid
            self._translation_loading_items = {}
            self._pending_translation_payload = None
            if getattr(self, \"_translation_debounce_after_id\", None) is not None:
                try:
                    self.after_cancel(self._translation_debounce_after_id)
                except Exception:
                    pass
            self._translation_debounce_after_id = None
            lpp.reset_session(sid)
            lpp.mark(\"start_button_clicked_at\")
        except Exception:
            self._live_session_id = f\"sess-{time.time_ns()}\"
            self._translation_loading_items = {}
        self._starting_listening = True
        self._set_starting_status()
"""
if "lpp.mark(\"start_button_clicked_at\")" in text or "lpp.mark('start_button_clicked_at')" in text:
    print("start_inject_already_present")
elif needle in text:
    text = text.replace(needle, inject, 1)
    print("injected_session_reset")
else:
    print("NEEDLE_START_NOT_FOUND")

mark_listen = '''        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_CALLBACK_EXIT")
        except Exception:
            pass

    def _write_startup_failure_summary'''
mark_listen_new = '''        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("listening_state_visible_at")
        except Exception:
            pass
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_CALLBACK_EXIT")
        except Exception:
            pass

    def _write_startup_failure_summary'''
if 'lpp.mark("listening_state_visible_at")' in text:
    print("listen_mark_present")
elif mark_listen in text:
    text = text.replace(mark_listen, mark_listen_new, 1)
    print("added_listen_mark")
else:
    print("LISTEN_MARK_ANCHOR_MISSING")

stop_old = '''    def _begin_graceful_stop(self):
        """Non-blocking stop: UI returns immediately; finalize runs in background worker."""
        from alpha.utils.stop_finalize_worker import begin_stop_from_ui
'''
stop_new = '''    def _begin_graceful_stop(self):
        """Non-blocking stop: UI returns immediately; finalize runs in background worker."""
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("stop_button_clicked_at")
        except Exception:
            pass
        # Flush any debounced Stable translation before stop-accepting.
        try:
            self._flush_pending_translation_submit()
        except Exception:
            pass
        from alpha.utils.stop_finalize_worker import begin_stop_from_ui
'''
if 'lpp.mark("stop_button_clicked_at")' in text:
    print("stop_click_present")
elif stop_old in text:
    text = text.replace(stop_old, stop_new, 1)
    print("added_stop_click")
else:
    print("STOP_ANCHOR_MISSING")

if 'lpp.mark("stop_ui_acknowledged_at")' not in text:
    anchor = '''            self.status_text_label.configure(
                text="Finalising…",
                text_color=COLORS["text_primary"],
            )
        if self.signal_label is not None:
            self.signal_label.configure(text="● Standby"'''
    repl = '''            self.status_text_label.configure(
                text="Finalising…",
                text_color=COLORS["text_primary"],
            )
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("stop_ui_acknowledged_at")
        except Exception:
            pass
        if self.signal_label is not None:
            self.signal_label.configure(text="● Standby"'''
    if anchor in text:
        text = text.replace(anchor, repl, 1)
        print("added_stop_ack")
    else:
        print("STOP_ACK_ANCHOR_MISSING")

fin = "def _finish_graceful_stop(self, timed_out=False):"
idx = text.find(fin)
if idx >= 0 and 'stop_completed_at' not in text[idx : idx + 1200]:
    body = text.find('"""', idx)
    if body > 0:
        body_end = text.find('"""', body + 3) + 3
        text = (
            text[:body_end]
            + '\n        try:\n            from alpha.utils import live_pipeline_profile as lpp\n\n            lpp.mark("stop_completed_at")\n        except Exception:\n            pass'
            + text[body_end:]
        )
        print("added_stop_completed")
else:
    print("stop_completed_skip")

p.write_text(text, encoding="utf-8")
print("wrote", p)
