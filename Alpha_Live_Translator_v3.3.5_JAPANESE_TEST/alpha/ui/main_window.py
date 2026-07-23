"""
Alpha Live Translator — main window (V3 UX polish).
Nova-3 STT, dual audio capture (WASAPI + microphone), speaker diarization,
and optimized multilingual support (EN / JA / ZH / RU).
"""

import math
import os
import json
import queue
import re
import threading
import time

import customtkinter as ctk
import tkinter as tk
from PIL import Image
from tkinter import Menu, messagebox

from alpha.audio.microphone import MicrophoneCaptureMixin
from alpha.audio.timeline_mixer import DeepgramTimelineMixer
from alpha.audio.wasapi import WasapiCaptureMixin
from alpha.config import (
    ASSETS_DIR,
    DEEPGRAM_API_KEY,
    DEEPGRAM_SAMPLE_RATE,
    HEALTH_MONITOR_INTERVAL_MS,
    LANGUAGE_MAP,
    MAX_AUDIO_QUEUE_SIZE,
    MIC_NOISE_GATE_INITIAL_RMS,
    MIC_RMS_ROLLING_WINDOW_S,
    PROJECT_ROOT,
    WASAPI_FRAMES_PER_BUFFER,
    get_deepgram_key_status,
)
from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    AUTO_LANGUAGE_ENABLED,
    AUTO_SOURCE_LANGUAGE_UI,
    COMPACT_BREAKPOINT,
    DEBUG_DIAGNOSTICS,
    DEBUG_TEAMS_DIAGNOSTICS,
    DEFAULT_SOURCE_LANGUAGE,
    DEEPGRAM_BYTES_PER_SECOND,
    DEEPGRAM_EXPECTED_KBPS,
    LANGUAGE_CONFIDENCE_REJECT,
    LANGUAGE_CONFIDENCE_SAFE,
    LANGUAGE_CONFIDENCE_UNSTABLE,
    LANGUAGE_GATE_ENABLED,
    LANGUAGE_GATE_WARNING_ONLY,
    JAPANESE_MODE_ENABLED,
    JAPANESE_TEXT_NORMALIZATION_ENABLED,
    JAPANESE_CHAR_DEDUP_ENABLED,
    JAPANESE_INTERNAL_REPEAT_REMOVAL_ENABLED,
    JAPANESE_PARTIAL_OVERLAP_REMOVAL_ENABLED,
    JAPANESE_TAIL_STITCH_ENABLED,
    JAPANESE_SAFE_MERGE_GUARD_ENABLED,
    JAPANESE_PREFIX_REPEAT_REMOVAL_ENABLED,
    JAPANESE_COMPOUND_CONTINUATION_ENABLED,
    JAPANESE_KNOWN_TERM_CORRECTION_ENABLED,
    JAPANESE_GUARDED_KNOWN_CORRECTIONS_ENABLED,
    CJK_CLEANUP_ENABLED,
    CJK_LOCAL_REPEAT_FIX_ENABLED,
    CJK_PREFIX_OVERLAP_FIX_ENABLED,
    CJK_BOUNDARY_PUNCTUATION_FIX_ENABLED,
    CJK_POST_MERGE_CLEANUP_ENABLED,
    JAPANESE_STANDALONE_NO_MERGE,
    JAPANESE_COMPOUND_ENDINGS,
    JAPANESE_COMPOUND_STARTS,
    JAPANESE_KNOWN_TERM_CORRECTIONS,
    JAPANESE_KEYTERMS_ENABLED,
    JAPANESE_ACCURACY_MODE,
    JAPANESE_STT_PROFILE,
    MEETING_BUFFER_ENABLE_OVERLAP_MERGE,
    MEETING_BUFFER_FLUSH_ON_SOURCE_CHANGE,
    MEETING_BUFFER_FLUSH_ON_SPEAKER_CHANGE,
    MEETING_BUFFER_MAX_GAP_MS,
    MEETING_BUFFER_MAX_HOLD_MS,
    MEETING_BUFFER_MIN_FRAGMENT_WORDS,
    MEETING_SEGMENT_BUFFER_ENABLED,
    MEETING_SEGMENT_REPAIR_ENABLED,
    MEETING_SEGMENT_REPAIR_MAX_GAP_MS,
    MIC_ACTIVE_RMS_MIN,
    MIC_NOISE_MULTIPLIER,
    MIC_TO_SYSTEM_RATIO_MIN,
    OVERLAP_CONFIRM_FRAMES,
    SOURCE_HOLD_MS,
    SOURCE_LANGUAGES,
    FORCE_DEEPGRAM_LANGUAGE,
    SYSTEM_ACTIVE_RMS_MIN,
    SYSTEM_NOISE_MULTIPLIER,
    RESIZE_DEBOUNCE_MS,
    TRANSCRIPT_RENDER_DEBOUNCE_MS,
    UI_LAG_LOG_THROTTLE_MS,
    UI_LAG_MONITOR_ENABLED,
    UI_LAG_MONITOR_INTERVAL_MS,
    UI_LAG_SEVERE_MS,
    UI_LAG_WARN_MS,
    UI_MAX_UPDATES_PER_TICK,
    UI_PERFORMANCE_MODE,
    UI_QUEUE_DEFER_MS,
    UI_QUEUE_POLL_MS,
    UI_QUEUE_TIME_BUDGET_MS,
    UI_UPDATE_INTERVAL_MS,
    TRANSCRIPT_UI_BATCH_FLUSH_MS,
    TRANSCRIPT_UI_SCROLL_MAX_HZ,
    DEBUG_UI_LOOP_VERBOSE,
    DEBUG_AFTER_LOOP_VERBOSE,
    INTERIM_UI_THROTTLE_MS,
    INTERIM_LOG_THROTTLE_MS,
    DEFER_LOGO_MS,
    DEFER_WAVEFORM_DRAW_MS,
    CJK_CLEANUP_SLOW_MS,
    CJK_CLEANUP_MAX_CHARS,
    TARGET_LANGUAGES,
)
from alpha.utils.cjk_text import (
    compact_cjk_for_compare,
    fix_cjk_boundary_punctuation_with_log,
    is_cjk_mode,
    normalize_cjk_spacing,
    remove_cjk_local_repeats,
    remove_cjk_prefix_overlap,
)
from alpha.core.event_bus import EventBus
from alpha.core.events import EventType
from alpha.core.models import ErrorEvent, StatusEvent, TranscriptEvent, TranslationEvent
from alpha.transcription.deepgram_client import (
    DeepgramClientMixin,
    _audio_format_ndjson_log,
    _diag_ndjson_log,
    _diag_text_preview,
    _interim_ndjson_log,
    _session_ndjson_log,
    _speaker_ndjson_log,
    _segment_buffer_ndjson_log,
    _segment_repair_ndjson_log,
    _language_ndjson_log,
    _teams_diag_ndjson_log,
    get_debug_log_path,
    teams_commit_decision_from_dup_action,
    teams_log_quality_signals,
)
from alpha.transcription.japanese_final_chunk_stabilizer import (
    block_rogue_japanese_direct_commit,
)
from alpha.utils.japanese_accuracy_log import (
    get_japanese_accuracy_log_path,
    jp_accuracy_log,
)
from alpha.transcription.duplicate_protection import (
    DuplicateProtectionMixin,
    decide_transcript_action,
)
from alpha.transcription.speaker_detection import SpeakerDetectionMixin
from alpha.summary.summary_service import SummaryService
from alpha.summary.transcript_store import TranscriptStore
from alpha.ui.theme import (
    COLORS,
    CONTENT_COMPACT_BREAKPOINT,
    DROPDOWN_BORDER_WIDTH,
    DROPDOWN_HEIGHT,
    DROPDOWN_INNER_BORDER_WIDTH,
    DROPDOWN_WIDTH,
    DROPDOWN_WRAPPER_BORDER_WIDTH,
    FONT_FAMILY,
    FONT_FAMILY_FALLBACK,
    FOOTER_BTN_HEIGHT,
    FOOTER_BTN_HEIGHT_COMPACT,
    FOOTER_BTN_WIDTH,
    FOOTER_BTN_WIDTH_COMPACT,
    FOOTER_BTN_WIDTH_SECONDARY,
    FONTS,
    HEADER_CONTROL_HEIGHT,
    LAYOUT_FOOTER_WRAP_BREAKPOINT,
    LAYOUT_HAMBURGER_BREAKPOINT,
    LAYOUT_MEDIUM_BREAKPOINT,
    LAYOUT_MIN_HEIGHT,
    LAYOUT_MIN_WIDTH,
    LAYOUT_STATUS_COMPACT_BREAKPOINT,
    LAYOUT_WIDE_BREAKPOINT,
    MEETING_SUMMARY_BUTTON_TEXT,
    PLACEHOLDER_SUMMARY,
    PLACEHOLDER_TRANSCRIPT,
    PLACEHOLDER_TRANSLATION,
    RADII,
    SECTION_TRANSCRIPT_TITLE,
    SECTION_TRANSLATION_TITLE,
    SMALL_BUTTON_HEIGHT,
    SPACING,
    SUMMARY_BUTTON_WIDTH,
    SUMMARY_CLOSE_ICON,
    SUMMARY_PANEL_TITLE,
    SUMMARY_TITLE,
    TRANSLATION_TITLE_ICON,
    SWAP_BUTTON_SIZE,
    SPEAKER_COLORS,
    EXTENDED_SPEAKER_COLORS,
    TRANSCRIPT_BODY_FONT,
    TRANSLATION_BODY_FONT,
    WAVEFORM_ANIMATION_MS,
    WAVEFORM_BAR_COUNT,
    WAVEFORM_BAR_COUNT_WIDE,
    WAVEFORM_CANVAS_HEIGHT,
    WAVEFORM_CANVAS_WIDTH,
    WAVEFORM_CANVAS_WIDTH_WIDE,
)
from alpha.utils.logging_utils import get_logger, log_throttled, perf_checkpoint
from alpha.utils.queues import put_bounded


MISSING_API_KEY_MSG = (
    "Deepgram API key is missing. Please add DEEPGRAM_API_KEY to your .env file."
)

PLACEHOLDER_API_KEY_MSG = (
    "Deepgram API key is still set to the example placeholder. "
    "Please replace it with your real Deepgram API key in .env."
)

logger = get_logger(__name__)

# Root window grid rows (header / status / content / footer)
ROOT_ROW_HEADER = 0
ROOT_ROW_STATUS = 1
ROOT_ROW_MENU = 1
ROOT_ROW_CONTENT = 2
ROOT_ROW_FOOTER = 3

LANGUAGE_FLAG_LABELS = {
    "English": "English",
    "Japanese": "Japanese",
    "Chinese (Mandarin)": "Chinese (Mandarin)",
    "Russian": "Russian",
}

# Left column vertical split: Live Transcript (upper, larger) / Translation (lower, smaller)
TRANSCRIPT_PANEL_WEIGHT = 65
TRANSLATION_PANEL_WEIGHT = 35


class AlphaApp(
    ctk.CTk,
    WasapiCaptureMixin,
    MicrophoneCaptureMixin,
    DeepgramClientMixin,
    SpeakerDetectionMixin,
    DuplicateProtectionMixin,
):
    """Main application window for Alpha live meeting translation (V4)."""

    def __init__(self):
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        super().__init__()
        perf_checkpoint("mainwindow_init_start")

        self._compact_mode = None
        self._header_mode = None
        self._layout_mode = None
        self._menu_visible = False
        self.summary_panel_visible = True
        self.summary_outer = None
        self.summary_card = None
        self.summary_panel_close_btn = None
        self.summary_title_label = None
        self._status_right_cluster = None
        self.summary_key_points_label = None
        self.left_header_cluster = None
        self.right_header_cluster = None
        self.header_lang_frame = None
        self.header_controls = None
        self.brand_block = None
        self.footer_btn_row = None
        self.footer_btn_row2 = None
        self._footer_buttons = []
        self._pane_initialized = False
        self._initial_verse_visible = True
        self.logo_image = None
        self._font_cache = {}
        self._resize_layout_job = None
        self._transcript_render_job = None
        self._starting_listening = False
        self._displayed_segment_count = 0
        self._exported_ui_segment_count = 0
        self._transcript_events_posted = 0
        self._transcript_events_drained = 0
        self._transcript_ui_batch_buffer: list = []
        self._transcript_ui_batch_after_id = None
        self._transcript_ui_last_flush_mono = 0.0
        self._transcript_ui_scroll_last_mono = 0.0
        self._ui_queue_defer_after_id = None
        self._last_interim_ui_at = 0.0
        self._last_interim_log_at = 0.0
        self._interim_after_id = None
        self._pending_interim = None
        self._last_operation_hint = "idle"
        self._ui_lag_expected_at = 0.0
        self._ui_queue_after_id = None
        self._lag_monitor_after_id = None
        self._ui_loops_started = False
        self._ui_queue_backpressure_active = False
        self._ui_queue_max_snapshot = 0
        self._window_close_pending = False
        self._last_layout_width = -1
        self._last_layout_mode_applied = None

        _default_source_ui = (
            "Japanese" if DEFAULT_SOURCE_LANGUAGE == "ja" else "English"
        )
        self.source_language = ctk.StringVar(value=_default_source_ui)
        self.target_language = ctk.StringVar(value="Japanese")
        self.source_language.trace_add(
            "write", lambda *_: self.on_language_change("source")
        )
        self.target_language.trace_add(
            "write", lambda *_: self.on_language_change("target")
        )

        self.initial_verse_box = None
        self.translated_verse_box = None
        self.initial_verse_frame = None
        self.translated_verse_frame = None
        self.translated_title_row = None
        self.translated_title_label = None
        self.initial_title_row = None
        self.hide_initial_button = None
        self.show_initial_button = None
        self.always_on_top_switch = None
        self.always_on_top_switch_menu = None
        self.listen_button = None
        self.listen_button_menu = None
        self.footer_stop_button = None
        self.paned = None
        self.left_column = None
        self.right_column = None
        self.status_bar_frame = None
        self.live_pill = None
        self.live_indicator = None
        self.status_text_label = None
        self.timer_label = None
        self.signal_label = None
        self.waveform_canvas = None
        self.summary_body_box = None
        self._listen_start_time = None
        self._timer_job = None
        self._waveform_job = None
        self._waveform_phase = 0
        self._live_pulse_job = None
        self.normal_header_widgets = []

        # Deepgram / audio state
        self.ui_queue = queue.Queue()
        self.transcript_queue = self.ui_queue
        self.is_listening = False
        self._selected_source_language_ui_label = self._strip_language_flag(
            self.source_language.get()
        )
        self._language_profile_id = None
        self._allowed_languages = None
        self._profile_is_auto = False
        self._listen_language = self._resolve_deepgram_language(self.source_language.get())
        self._audio_q = None
        self.sys_audio_queue = None
        self.mic_audio_queue = None
        self._pyaudio = None
        self._wasapi_stream = None
        self._wasapi_reader_thread = None
        self._mix_thread = None
        self._mic_stream = None
        self._wasapi_channels = 1
        self._wasapi_rate = DEEPGRAM_SAMPLE_RATE
        self._wasapi_frames_per_buffer = WASAPI_FRAMES_PER_BUFFER
        self._dg_ws = None
        self._dg_thread = None
        self._dg_reconnect_lock = threading.Lock()  # CHANGED: reconnect serialization (fix 5)
        self._dg_reconnecting = False  # CHANGED: prevent parallel reconnects (fix 5)
        self._dg_backoff_seconds = 1.0  # CHANGED: exponential backoff start (fix 5)
        self._dg_awaiting_transcript_reset = False  # CHANGED: reset backoff on transcript (fix 5)
        self._dg_replay_buffer = []  # CHANGED: buffered audio for reconnect replay (fix 5)
        self._stop_event = threading.Event()
        self._speaker_lock = threading.Lock()
        self._last_assigned_speaker = 1
        self._last_speaker_change_time = 0.0
        self.last_transcript_hash = set()
        self._transcript_hash_order = []  # CHANGED: ordered hash prune list (fix 8)
        self._last_speaker_utterance = {}  # CHANGED: fuzzy dedup per speaker (fix 8)
        self.last_speaker = None
        self.last_speaker_id = None
        self.current_speaker = None
        self.last_displayed_speaker = None
        self.last_speech_time = 0.0
        self.fallback_speaker = 1
        self._health_monitor_job = None
        self._chunks_sent_count = 0
        self._transcripts_received = 0
        self._health_no_transcript_hint_shown = False
        self.translation_worker = None
        self.translation_enabled = False
        self.translation_error_shown = False
        self.last_translation_speaker = None
        self._recent_displayed_texts = []
        self.transcript_store = TranscriptStore()
        self.summary_service = SummaryService()

        self.event_bus = EventBus()
        self._setup_event_subscriptions()

        self.setup_window()
        self.create_solid_background()
        self.create_header_frame()
        self.create_hamburger_menu()
        self.create_status_bar()
        self.create_main_content()
        self.create_footer()
        self._create_context_menu()
        self.bind_resize_event()
        self._bind_keyboard_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            from alpha.utils.ui_thread_guard import register_ui_main_thread

            register_ui_main_thread()
        except Exception:
            pass

        self.after(250, self._apply_responsive_layout_debounced)
        self.after(300, self._set_initial_pane_ratio)
        self._update_translation_title()
        self.after(0, self._deferred_post_show_init)
        self._is_finalizing = False
        self._is_stopping = False
        self._stop_finalize_started = False
        perf_checkpoint("ui_widgets_created")
        defer_logs_ms = 2000 if UI_PERFORMANCE_MODE else 0
        self.after(defer_logs_ms, self._emit_deferred_startup_logs)
        perf_checkpoint("mainwindow_init_complete")

    def _deferred_post_show_init(self):
        """Single after(0) hook: translation placeholder, runtime reset, UI queue loop."""
        self._initialize_translation()
        self._deferred_lightweight_init()
        self._start_ui_loops_once()
        try:
            from alpha.utils.session_watchdog import start_ui_heartbeat

            start_ui_heartbeat(self)
        except Exception:
            pass
        try:
            from alpha.utils.ui_event_bus import get_ui_event_bus, get_ui_event_bus_poll_ms

            self._ui_event_bus = get_ui_event_bus()
            self._start_ui_event_bus_drain_loop()
        except Exception:
            pass

    def _start_ui_event_bus_drain_loop(self):
        if getattr(self, "_ui_event_bus_drain_started", False):
            return
        self._ui_event_bus_drain_started = True
        from alpha.utils.ui_event_bus import get_ui_event_bus_poll_ms

        poll_ms = get_ui_event_bus_poll_ms()
        self._register_ui_event_bus_handlers()

        def _tick():
            self._ui_event_bus_after_id = None
            try:
                from alpha.utils.ui_event_bus import get_ui_event_bus

                bus = get_ui_event_bus()
                result = bus.drain(self)
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "UI_EVENT_DRAIN_TICK",
                    processed=result.get("processed", 0),
                    remaining=result.get("remaining", 0),
                )
                if result.get("remaining", 0):
                    jp_accuracy_log(
                        "UI_EVENT_DRAIN_REMAINING_QUEUE",
                        remaining=result.get("remaining", 0),
                    )
            except Exception:
                pass
            if getattr(self, "winfo_exists", lambda: False)():
                self._ui_event_bus_after_id = self.after(poll_ms, _tick)

        self._ui_event_bus_after_id = self.after(poll_ms, _tick)

    def _register_ui_event_bus_handlers(self):
        from alpha.utils.ui_event_bus import get_ui_event_bus

        bus = get_ui_event_bus()

        def _on_partial_error(payload: dict):
            title = str(payload.get("title") or "Error")
            message = str(payload.get("message") or "")
            if payload.get("action") == "stop_listening":
                try:
                    from tkinter import messagebox

                    messagebox.showerror(title, message)
                except Exception:
                    pass
                self._stop_listening(graceful=False)

        bus.register_handler("partial_error_notice", _on_partial_error)

        def _on_interim_flush(_payload: dict):
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("INTERIM_UI_FLUSH_DRAINED_ON_MAIN_THREAD")
            self._schedule_interim_flush_main_thread()

        bus.register_handler("interim_flush_requested", _on_interim_flush)

        def _on_cancel_ui_timer(payload: dict):
            after_id = payload.get("after_id")
            if after_id is not None and hasattr(self, "after_cancel"):
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass

        bus.register_handler("cancel_ui_timer_requested", _on_cancel_ui_timer)

        def _on_stop_ui_flush(_payload: dict):
            self._flush_pending_transcript_queue()

        bus.register_handler("stop_ui_flush_requested", _on_stop_ui_flush)

        def _on_stop_ui_recover(_payload: dict):
            self._recover_interim_tail_on_stop()

        bus.register_handler("stop_ui_recover_requested", _on_stop_ui_recover)

        def _on_deepgram_close_ui_cleanup(_payload: dict):
            if hasattr(self, "_stop_health_monitor"):
                self._stop_health_monitor()

        bus.register_handler("deepgram_close_ui_cleanup_requested", _on_deepgram_close_ui_cleanup)

    def _start_japanese_pipeline_heartbeat_loop(self):
        if getattr(self, "_jp_pipeline_hb_started", False):
            return
        self._jp_pipeline_hb_started = True
        from alpha.transcription.japanese_sentence_assembler import (
            get_japanese_continuity_assembler,
        )

        def _tick():
            self._jp_pipeline_hb_after_id = None
            try:
                asm = get_japanese_continuity_assembler(self)
                asm.emit_heartbeat_from_ui()
            except Exception:
                pass
            listening = bool(getattr(self, "is_listening", False))
            if listening or getattr(self, "_jp_continuity_assembler", None):
                from alpha.transcription.japanese_sentence_assembler import (
                    JapaneseContinuityAssembler,
                )

                self._jp_pipeline_hb_after_id = self.after(
                    JapaneseContinuityAssembler.HEARTBEAT_MS, _tick
                )

        from alpha.transcription.japanese_sentence_assembler import (
            JapaneseContinuityAssembler,
        )

        self._jp_pipeline_hb_after_id = self.after(
            JapaneseContinuityAssembler.HEARTBEAT_MS, _tick
        )

    def _deferred_lightweight_init(self):
        """Reset runtime state after the window is on screen (non-blocking)."""
        self._reset_interim_tail_state()
        self._reset_meeting_segment_buffer_state()
        self._reset_segment_repair_state()

    def _start_ui_loops_once(self):
        """Start a single UI queue loop (and optional lag monitor) after mainloop is live."""
        if self._ui_loops_started:
            return
        self._ui_loops_started = True
        self._run_ui_queue_tick()
        if UI_LAG_MONITOR_ENABLED:
            self._ui_lag_expected_at = time.perf_counter() + (
                UI_LAG_MONITOR_INTERVAL_MS / 1000.0
            )
            self._schedule_lag_monitor_tick()

    def _cancel_ui_queue_tick(self):
        job = getattr(self, "_ui_queue_after_id", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._ui_queue_after_id = None

    def _schedule_ui_queue_tick(self):
        if not getattr(self, "_ui_loops_started", False):
            return
        self._cancel_ui_queue_tick()
        self._ui_queue_after_id = self.after(
            UI_UPDATE_INTERVAL_MS, self._run_ui_queue_tick
        )

    def _run_ui_queue_tick(self):
        self._ui_queue_after_id = None
        if not getattr(self, "_ui_loops_started", False):
            return
        self._process_ui_queue_once()
        self._schedule_ui_queue_tick()

    def _process_ui_queue_once(self):
        """Drain transcript queue with per-tick item and time budgets."""
        import traceback

        tick_start = time.perf_counter()
        budget_s = UI_QUEUE_TIME_BUDGET_MS / 1000.0
        queued_items = 0
        processed_items = 0
        chars_added = 0
        deferred_items = 0
        try:
            self._ensure_stability_state()
            if not hasattr(self, "last_displayed_speaker"):
                self.last_displayed_speaker = None

            queued_items = self.transcript_queue.qsize()
            if queued_items > self._ui_queue_max_snapshot:
                self._ui_queue_max_snapshot = queued_items
            max_per_poll = UI_MAX_UPDATES_PER_TICK
            if queued_items > 150:
                max_per_poll = min(24, max(UI_MAX_UPDATES_PER_TICK * 2, queued_items // 4))
                if not self._ui_queue_backpressure_active:
                    self._ui_queue_backpressure_active = True
                    try:
                        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                        jp_accuracy_log(
                            "UI_QUEUE_BACKPRESSURE_DETECTED",
                            queue_size=queued_items,
                            max_per_poll=max_per_poll,
                        )
                    except Exception:
                        pass
            elif self._ui_queue_backpressure_active and queued_items < 50:
                self._ui_queue_backpressure_active = False
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "UI_QUEUE_BACKPRESSURE_RECOVERED",
                        queue_size=queued_items,
                    )
                except Exception:
                    pass
            if self._ui_queue_backpressure_active:
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "UI_TRANSCRIPT_BATCH_SIZE_ADJUSTED",
                        max_per_poll=max_per_poll,
                        queue_size=queued_items,
                    )
                except Exception:
                    pass
            while (
                not self.transcript_queue.empty()
                and processed_items < max_per_poll
                and (time.perf_counter() - tick_start) < budget_s
            ):
                item = self.transcript_queue.get()
                if isinstance(item, list):
                    for sub_item in item:
                        self._enqueue_transcript_ui_batch(sub_item)
                        chars_added += len((sub_item.get("text") or ""))
                        self._transcript_events_drained += 1
                        try:
                            from alpha.utils.live_runtime_metrics import note_ui_event_drained

                            note_ui_event_drained()
                        except Exception:
                            pass
                    processed_items += 1
                else:
                    self._enqueue_transcript_ui_batch(item)
                    chars_added += len((item.get("text") or ""))
                    processed_items += 1
                    self._transcript_events_drained += 1

            deferred_items = self.transcript_queue.qsize() + len(
                self._transcript_ui_batch_buffer
            )
            if deferred_items > 0:
                self._schedule_transcript_ui_batch_flush()
                self._schedule_ui_queue_deferred_tick()
        except Exception as e:
            if DEBUG_DIAGNOSTICS:
                print(f"[ERROR] Processing UI queue: {e}")
                traceback.print_exc()

        elapsed_ms = round((time.perf_counter() - tick_start) * 1000, 1)
        if elapsed_ms > 50:
            self._perf_log_ui_update_batch(
                queued_items=queued_items,
                processed_items=processed_items,
                elapsed_ms=elapsed_ms,
                transcript_chars_added=chars_added,
                skipped_or_deferred_items=deferred_items,
            )
            try:
                from alpha.utils.diagnostic_test_log import diag_log

                diag_log(
                    "ui_queue",
                    "UI_QUEUE_TICK_SLOW",
                    {
                        "duration_ms": elapsed_ms,
                        "processed_items": processed_items,
                        "deferred_items": deferred_items,
                    },
                )
            except Exception:
                pass
        elif UI_PERFORMANCE_MODE and processed_items > 0 and deferred_items > 0:
            try:
                from alpha.utils.diagnostic_test_log import diag_log_throttled

                diag_log_throttled(
                    "ui_queue_budget",
                    "ui_queue",
                    "UI_QUEUE_BUDGET_APPLIED",
                    {
                        "duration_ms": elapsed_ms,
                        "processed_items": processed_items,
                        "deferred_items": deferred_items,
                        "budget_ms": UI_QUEUE_TIME_BUDGET_MS,
                    },
                )
                diag_log_throttled(
                    "ui_queue_deferred",
                    "ui_queue",
                    "UI_QUEUE_DEFERRED_REMAINING_WORK",
                    {"deferred_items": deferred_items},
                )
            except Exception:
                pass
        elif elapsed_ms > UI_QUEUE_TIME_BUDGET_MS:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "UI_INSERT_TIME_BUDGET_EXCEEDED",
                    duration_ms=elapsed_ms,
                    budget_ms=UI_QUEUE_TIME_BUDGET_MS,
                )
            except Exception:
                pass
            try:
                from alpha.utils.diagnostic_test_log import diag_log_throttled

                diag_log_throttled(
                    "ui_queue_budget",
                    "ui_queue",
                    "UI_QUEUE_BUDGET_APPLIED",
                    {
                        "duration_ms": elapsed_ms,
                        "processed_items": processed_items,
                        "deferred_items": deferred_items,
                        "budget_ms": UI_QUEUE_TIME_BUDGET_MS,
                    },
                )
                diag_log_throttled(
                    "ui_queue_deferred",
                    "ui_queue",
                    "UI_QUEUE_DEFERRED_REMAINING_WORK",
                    {"deferred_items": deferred_items},
                )
            except Exception:
                pass

    def _schedule_ui_queue_deferred_tick(self):
        if self._ui_queue_defer_after_id is not None:
            return
        self._ui_queue_defer_after_id = self.after(
            UI_QUEUE_DEFER_MS, self._run_deferred_ui_queue_tick
        )

    def _run_deferred_ui_queue_tick(self):
        self._ui_queue_defer_after_id = None
        if not getattr(self, "_ui_loops_started", False):
            return
        self._process_ui_queue_once()

    def _enqueue_transcript_ui_batch(self, item):
        self._transcript_ui_batch_buffer.append(item)
        self._schedule_transcript_ui_batch_flush()

    def _schedule_transcript_ui_batch_flush(self, *, force: bool = False):
        if force:
            self._flush_transcript_ui_batch(force=True)
            return
        if self._transcript_ui_batch_after_id is not None:
            return
        elapsed_ms = (time.monotonic() - self._transcript_ui_last_flush_mono) * 1000.0
        delay_ms = max(0, int(TRANSCRIPT_UI_BATCH_FLUSH_MS - elapsed_ms))
        self._transcript_ui_batch_after_id = self.after(
            delay_ms, self._flush_transcript_ui_batch
        )

    def _flush_transcript_ui_batch(self, force: bool = False):
        self._transcript_ui_batch_after_id = None
        if not self._transcript_ui_batch_buffer:
            return
        if not force:
            elapsed_ms = (time.monotonic() - self._transcript_ui_last_flush_mono) * 1000.0
            if (
                elapsed_ms < TRANSCRIPT_UI_BATCH_FLUSH_MS
                and len(self._transcript_ui_batch_buffer) < 6
            ):
                self._schedule_transcript_ui_batch_flush()
                return
        batch = list(self._transcript_ui_batch_buffer)
        self._transcript_ui_batch_buffer.clear()
        start = time.perf_counter()
        chars_inserted = 0
        max_inserts = 12 if self._ui_queue_backpressure_active else 8
        for item in batch[:max_inserts]:
            text_len = len((item.get("text") or ""))
            self._display_transcript_item(item)
            chars_inserted += text_len
        if len(batch) > max_inserts:
            self._transcript_ui_batch_buffer.extend(batch[max_inserts:])
            self._schedule_transcript_ui_batch_flush()
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        self._transcript_ui_last_flush_mono = time.monotonic()
        try:
            from alpha.utils.session_progress import touch_progress

            touch_progress("last_ui_commit")
            try:
                from alpha.utils.flight_recorder import record_flight_event

                record_flight_event("ui_commit", host=self, force=True)
            except Exception:
                pass
        except Exception:
            pass
        if duration_ms > 50:
            try:
                from alpha.utils.diagnostic_test_log import diag_log

                diag_log(
                    "ui_transcript",
                    "TRANSCRIPT_UI_BATCH_SLOW",
                    {
                        "duration_ms": duration_ms,
                        "items_flushed": len(batch),
                        "chars_inserted": chars_inserted,
                    },
                )
            except Exception:
                pass

    def _maybe_scroll_transcript_box(self, box):
        now = time.monotonic()
        min_interval = 1.0 / max(1, TRANSCRIPT_UI_SCROLL_MAX_HZ)
        if now - self._transcript_ui_scroll_last_mono >= min_interval:
            box.see(tk.END)
            self._transcript_ui_scroll_last_mono = now

    def _cancel_lag_monitor_tick(self):
        job = getattr(self, "_lag_monitor_after_id", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._lag_monitor_after_id = None

    def _schedule_lag_monitor_tick(self):
        if not UI_LAG_MONITOR_ENABLED:
            return
        self._cancel_lag_monitor_tick()
        self._lag_monitor_after_id = self.after(
            UI_LAG_MONITOR_INTERVAL_MS, self._ui_lag_monitor_tick
        )

    def _stop_ui_loops(self):
        self._ui_loops_started = False
        self._cancel_ui_queue_tick()
        self._cancel_lag_monitor_tick()
        interim_job = getattr(self, "_interim_after_id", None)
        if interim_job is not None:
            try:
                self.after_cancel(interim_job)
            except Exception:
                pass
            self._interim_after_id = None
        job = getattr(self, "_waveform_layout_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._waveform_layout_job = None

    def _emit_deferred_startup_logs(self):
        """Emit session/config logs off the UI thread after the window is visible."""
        threading.Thread(
            target=self._emit_deferred_startup_logs_worker,
            name="StartupLogs",
            daemon=True,
        ).start()

    def _emit_deferred_startup_logs_worker(self):
        selected_language = DEFAULT_SOURCE_LANGUAGE
        debug_log_path = str(get_debug_log_path())
        accuracy_log_path = str(get_japanese_accuracy_log_path())
        try:
            if hasattr(self, "source_language") and self.source_language is not None:
                selected_language = self._strip_language_flag(self.source_language.get())
        except Exception:
            selected_language = DEFAULT_SOURCE_LANGUAGE
        _session_ndjson_log(
            location="main_window.py:__init__",
            message="DEBUG_LOG_FOLDER_READY",
            data={
                "debug_log_path": debug_log_path,
                "accuracy_log_path": accuracy_log_path,
            },
        )
        jp_accuracy_log(
            "DEBUG_LOG_FOLDER_READY",
            debug_log_path=debug_log_path,
            accuracy_log_path=accuracy_log_path,
        )
        _session_ndjson_log(
            location="main_window.py:__init__",
            message="[SESSION] app runtime started",
            data={
                "app_version": APP_VERSION,
                "app_codename": APP_CODENAME,
                "session_id": "46ae0c",
                "log_file_path": debug_log_path,
                "debug_log_path": debug_log_path,
                "accuracy_log_path": accuracy_log_path,
                "timestamp": int(time.time() * 1000),
                "DEBUG_DIAGNOSTICS": DEBUG_DIAGNOSTICS,
                "DEBUG_TEAMS_DIAGNOSTICS": DEBUG_TEAMS_DIAGNOSTICS,
                "MIC_ACTIVE_RMS_MIN": MIC_ACTIVE_RMS_MIN,
                "SYSTEM_ACTIVE_RMS_MIN": SYSTEM_ACTIVE_RMS_MIN,
                "MIC_NOISE_MULTIPLIER": MIC_NOISE_MULTIPLIER,
                "SYSTEM_NOISE_MULTIPLIER": SYSTEM_NOISE_MULTIPLIER,
                "MIC_TO_SYSTEM_RATIO_MIN": MIC_TO_SYSTEM_RATIO_MIN,
                "OVERLAP_CONFIRM_FRAMES": OVERLAP_CONFIRM_FRAMES,
                "SOURCE_HOLD_MS": SOURCE_HOLD_MS,
                "MEETING_SEGMENT_BUFFER_ENABLED": MEETING_SEGMENT_BUFFER_ENABLED,
                "MEETING_BUFFER_MAX_GAP_MS": MEETING_BUFFER_MAX_GAP_MS,
                "MEETING_BUFFER_MAX_HOLD_MS": MEETING_BUFFER_MAX_HOLD_MS,
                "MEETING_SEGMENT_REPAIR_ENABLED": MEETING_SEGMENT_REPAIR_ENABLED,
                "AUTO_LANGUAGE_ENABLED": AUTO_LANGUAGE_ENABLED,
                "DEFAULT_SOURCE_LANGUAGE": DEFAULT_SOURCE_LANGUAGE,
                "LANGUAGE_GATE_ENABLED": LANGUAGE_GATE_ENABLED,
                "LANGUAGE_GATE_WARNING_ONLY": LANGUAGE_GATE_WARNING_ONLY,
                "LANGUAGE_CONFIDENCE_SAFE": LANGUAGE_CONFIDENCE_SAFE,
                "LANGUAGE_CONFIDENCE_UNSTABLE": LANGUAGE_CONFIDENCE_UNSTABLE,
                "LANGUAGE_CONFIDENCE_REJECT": LANGUAGE_CONFIDENCE_REJECT,
            },
        )
        _session_ndjson_log(
            location="main_window.py:__init__",
            message="[SESSION] RUN_STARTED",
            data={
                "app_version": APP_VERSION,
                "app_codename": APP_CODENAME,
                "run_start_timestamp": int(time.time() * 1000),
                "selected_language": selected_language,
                "JAPANESE_STT_PROFILE": JAPANESE_STT_PROFILE,
                "log_file_path": debug_log_path,
                "debug_log_path": debug_log_path,
                "accuracy_log_path": accuracy_log_path,
            },
        )
        if JAPANESE_ACCURACY_MODE:
            _session_ndjson_log(
                location="main_window.py:__init__",
                message="JAPANESE_ACCURACY_MODE_ENABLED",
                data={
                    "japanese_accuracy_mode": True,
                    "stable_over_speed": True,
                },
            )
            jp_accuracy_log(
                "JAPANESE_ACCURACY_MODE_ENABLED",
                stable_over_speed=True,
            )
        if UI_PERFORMANCE_MODE:
            _session_ndjson_log(
                location="main_window.py:__init__",
                message="UI_PERFORMANCE_MODE_ENABLED",
                data={
                    "ui_performance_mode": True,
                    "ui_queue_time_budget_ms": UI_QUEUE_TIME_BUDGET_MS,
                    "transcript_ui_batch_flush_ms": TRANSCRIPT_UI_BATCH_FLUSH_MS,
                },
            )
            jp_accuracy_log(
                "UI_PERFORMANCE_MODE_ENABLED",
                ui_performance_mode=True,
                transcript_ui_batch_flush_ms=TRANSCRIPT_UI_BATCH_FLUSH_MS,
            )
            if not DEBUG_UI_LOOP_VERBOSE and not DEBUG_AFTER_LOOP_VERBOSE:
                _session_ndjson_log(
                    location="main_window.py:__init__",
                    message="DEBUG_VERBOSE_UI_LOOP_DISABLED",
                    data={
                        "debug_ui_loop_verbose": False,
                        "debug_after_loop_verbose": False,
                    },
                )
                jp_accuracy_log(
                    "DEBUG_VERBOSE_UI_LOOP_DISABLED",
                    debug_ui_loop_verbose=False,
                    debug_after_loop_verbose=False,
                )
        _session_ndjson_log(
            location="main_window.py:__init__",
            message="[CJK] hardcoded correction disabled",
            data={
                "japanese_known_term_correction_enabled": JAPANESE_KNOWN_TERM_CORRECTION_ENABLED,
                "reason": "script_aware_cleanup_engine_active",
            },
        )
        _session_ndjson_log(
            location="main_window.py:__init__",
            message="[JAPANESE] safe merge guard active",
            data={
                "app_version": APP_VERSION,
                "app_codename": APP_CODENAME,
                "japanese_safe_merge_guard_enabled": JAPANESE_SAFE_MERGE_GUARD_ENABLED,
                "japanese_prefix_repeat_removal_enabled": JAPANESE_PREFIX_REPEAT_REMOVAL_ENABLED,
                "japanese_compound_continuation_enabled": JAPANESE_COMPOUND_CONTINUATION_ENABLED,
                "japanese_known_term_correction_enabled": JAPANESE_KNOWN_TERM_CORRECTION_ENABLED,
                "force_deepgram_language": FORCE_DEEPGRAM_LANGUAGE,
                "auto_language_enabled": AUTO_LANGUAGE_ENABLED,
                "language_gate_enabled": LANGUAGE_GATE_ENABLED,
                "meeting_segment_repair_enabled": MEETING_SEGMENT_REPAIR_ENABLED,
            },
        )
        _session_ndjson_log(
            location="main_window.py:__init__",
            message="[JAPANESE] manual Japanese mode active",
            data={
                "app_version": APP_VERSION,
                "app_codename": APP_CODENAME,
                "force_deepgram_language": FORCE_DEEPGRAM_LANGUAGE,
                "japanese_mode_enabled": JAPANESE_MODE_ENABLED,
                "japanese_text_normalization_enabled": JAPANESE_TEXT_NORMALIZATION_ENABLED,
                "japanese_char_dedup_enabled": JAPANESE_CHAR_DEDUP_ENABLED,
                "japanese_internal_repeat_removal_enabled": JAPANESE_INTERNAL_REPEAT_REMOVAL_ENABLED,
                "japanese_keyterms_enabled": JAPANESE_KEYTERMS_ENABLED,
                "auto_language_enabled": AUTO_LANGUAGE_ENABLED,
                "language_gate_enabled": LANGUAGE_GATE_ENABLED,
                "meeting_segment_repair_enabled": MEETING_SEGMENT_REPAIR_ENABLED,
            },
        )
        _audio_format_ndjson_log(
            location="main_window.py:__init__",
            message="[AUDIO_FORMAT] deepgram stream config",
            data={
                "declared_sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                "declared_channels": 1,
                "declared_encoding": "linear16",
                "target_bytes_per_second": int(DEEPGRAM_BYTES_PER_SECOND),
                "expected_kbps": int(DEEPGRAM_EXPECTED_KBPS),
            },
        )
        perf_checkpoint("deferred_startup_logs_complete")

    # -----------------------------------------------------------------------
    # UI thread performance (incremental transcript + lag monitor)
    # -----------------------------------------------------------------------
    def _reset_incremental_display_state(self):
        self._displayed_segment_count = 0
        self._exported_ui_segment_count = 0
        self._transcript_ui_batch_buffer.clear()
        self._transcript_ui_last_flush_mono = 0.0
        self._transcript_ui_scroll_last_mono = 0.0
        self._last_interim_ui_at = 0.0
        self._last_operation_hint = "idle"
        box = self._transcript_box()
        if box is not None:
            try:
                box.mark_unset("segment_anchor")
                box.mark_unset("interim_anchor")
            except Exception:
                pass

    def _transcript_box(self):
        return getattr(self, "initial_verse_box", None)

    def _speaker_tag(self, speaker) -> str:
        try:
            speaker_num = int(speaker)
        except (TypeError, ValueError):
            return "body"
        return f"speaker_{speaker_num}" if 1 <= speaker_num <= 4 else "body"

    def _refresh_transcript_scrollbar(self, box):
        scrollbar = getattr(box, "_scrollbar", None)
        if scrollbar is not None:
            self.check_scrollbar_visibility(box, scrollbar)

    def _remove_interim_line_from_display(self):
        box = self._transcript_box()
        if box is None:
            return
        try:
            if box.compare("interim_anchor", ">=", "1.0"):
                box.configure(state="normal")
                box.delete("interim_anchor", "end")
                box.mark_unset("interim_anchor")
                box.configure(state="disabled")
        except Exception:
            pass

    def _insert_speaker_segment_line(self, box, speaker, text: str):
        tag = self._speaker_tag(speaker)
        try:
            speaker_num = int(speaker)
        except (TypeError, ValueError):
            speaker_num = speaker
        box.insert("end", f"[Speaker {speaker_num}] ", tag)
        box.insert("end", (text or "").strip() + "\n", "body")
        box.mark_set("segment_anchor", "insert linestart")

    def _on_store_segment_added(self, speaker, text: str):
        self._last_operation_hint = "append_segment"
        self._remove_interim_line_from_display()
        box = self._transcript_box()
        if box is None:
            return
        box.configure(state="normal")
        if hasattr(self, "_clear_text_placeholder"):
            self._clear_text_placeholder(box)
        self._insert_speaker_segment_line(box, speaker, text)
        box.configure(state="disabled")
        self._maybe_scroll_transcript_box(box)
        self._displayed_segment_count += 1
        self._exported_ui_segment_count += 1
        try:
            from alpha.utils.transcript_evidence import log_ui_exported_segment

            log_ui_exported_segment(
                speaker_label=f"Speaker {speaker}",
                ui_text=(text or "").strip(),
                ui_segment_id=f"ui-{self._exported_ui_segment_count}",
                source_stable_commit_id=f"stable-{self._exported_ui_segment_count}",
            )
        except Exception:
            pass
        self._refresh_transcript_scrollbar(box)

    def _on_store_segment_updated(self, speaker, text: str):
        self._last_operation_hint = "update_segment"
        self._remove_interim_line_from_display()
        box = self._transcript_box()
        if box is None:
            return
        box.configure(state="normal")
        try:
            if box.compare("segment_anchor", ">=", "1.0"):
                box.delete("segment_anchor", "end")
            else:
                box.delete("end-2l linestart", "end")
        except Exception:
            box.delete("end-2l linestart", "end")
        self._insert_speaker_segment_line(box, speaker, text)
        box.configure(state="disabled")
        self._maybe_scroll_transcript_box(box)
        self._refresh_transcript_scrollbar(box)

    def _update_interim_line_only(self):
        box = self._transcript_box()
        interim_text = (getattr(self, "_latest_interim_text", "") or "").strip()
        if box is None:
            return
        self._remove_interim_line_from_display()
        if not interim_text:
            return
        speaker = getattr(self, "_latest_interim_speaker", 1) or 1
        box.configure(state="normal")
        if hasattr(self, "_clear_text_placeholder"):
            self._clear_text_placeholder(box)
        tag = self._speaker_tag(speaker)
        box.mark_set("interim_anchor", "end")
        try:
            speaker_num = int(speaker)
        except (TypeError, ValueError):
            speaker_num = speaker
        box.insert("end", f"[Speaker {speaker_num}] ", tag)
        box.insert("end", interim_text + " ⏳\n", "body")
        box.configure(state="disabled")
        box.see(tk.END)
        self._last_operation_hint = "interim_update"
        self._refresh_transcript_scrollbar(box)

    def _perf_log_ui_update_batch(
        self,
        *,
        queued_items: int,
        processed_items: int,
        elapsed_ms: float,
        transcript_chars_added: int,
        skipped_or_deferred_items: int,
    ):
        log_throttled(
            "ui_update_batch",
            "[PERF] ui update batch",
            {
                "queued_items": queued_items,
                "processed_items": processed_items,
                "elapsed_ms": elapsed_ms,
                "transcript_chars_added": transcript_chars_added,
                "skipped_or_deferred_items": skipped_or_deferred_items,
            },
            interval_ms=1000,
        )

    def _start_ui_lag_monitor(self):
        """Legacy entry — use _start_ui_loops_once instead."""
        if UI_LAG_MONITOR_ENABLED:
            self._schedule_lag_monitor_tick()

    def _ui_lag_monitor_tick(self):
        now = time.perf_counter()
        expected = getattr(self, "_ui_lag_expected_at", now)
        actual_delay_ms = round((now - expected) * 1000, 1)
        lag_ms = max(0.0, actual_delay_ms - UI_LAG_MONITOR_INTERVAL_MS)
        if lag_ms > UI_LAG_WARN_MS:
            level = "severe" if lag_ms > UI_LAG_SEVERE_MS else "warn"
            log_throttled(
                f"ui_lag_{level}",
                "[PERF] ui mainloop lag",
                {
                    "expected_interval_ms": UI_LAG_MONITOR_INTERVAL_MS,
                    "actual_delay_ms": actual_delay_ms,
                    "lag_ms": lag_ms,
                    "current_state": level,
                    "listening_active": bool(getattr(self, "is_listening", False)),
                    "last_operation_hint": getattr(self, "_last_operation_hint", "idle"),
                },
                interval_ms=UI_LAG_LOG_THROTTLE_MS,
            )
        self._ui_lag_expected_at = now + (UI_LAG_MONITOR_INTERVAL_MS / 1000.0)
        self._schedule_lag_monitor_tick()

    def _prepare_final_transcript_for_queue(self, text: str) -> str:
        """Run CJK cleanup on STT worker thread before UI queueing."""
        if not self._is_japanese_manual_mode():
            return (text or "").strip()
        return self._apply_japanese_final_cleanup_timed(text, source="stt_worker")

    def _apply_japanese_final_cleanup_timed(
        self, text: str, source: str = "ui"
    ) -> str:
        segment = (text or "").strip()
        if not segment:
            return segment
        start = time.perf_counter()
        cleaned = self._apply_japanese_final_cleanup(segment)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        perf_guard = len(segment) > CJK_CLEANUP_MAX_CHARS
        if elapsed_ms > CJK_CLEANUP_SLOW_MS:
            self._cjk_log(
                "[CJK] cleanup timing",
                {
                    "text_len": len(segment),
                    "elapsed_ms": elapsed_ms,
                    "cleanup_steps_used": "japanese_final_cleanup",
                    "perf_guard_triggered": perf_guard,
                    "source": source,
                },
            )
        return cleaned

    # -----------------------------------------------------------------------
    # Window setup
    # -----------------------------------------------------------------------
    def setup_window(self):
        """Configure the main application window."""
        self.title(f"Alpha — Meeting Assistant V{APP_VERSION}")
        self.geometry("900x650")
        self.minsize(LAYOUT_MIN_WIDTH, LAYOUT_MIN_HEIGHT)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(ROOT_ROW_HEADER, weight=0)
        self.grid_rowconfigure(ROOT_ROW_STATUS, weight=0)
        self.grid_rowconfigure(ROOT_ROW_CONTENT, weight=1)
        self.grid_rowconfigure(ROOT_ROW_FOOTER, weight=0)

    def create_solid_background(self):
        """Apply a solid background color to the main window."""
        self.configure(fg_color=COLORS["main_bg"])

    def bind_resize_event(self):
        """Bind window resize events to responsive header logic."""
        self.bind("<Configure>", self.on_window_resize)

    # -----------------------------------------------------------------------
    # UI style helpers (visual consistency only)
    # -----------------------------------------------------------------------
    def _ui_font(self, size, weight="normal"):
        """Return CTkFont using Segoe UI Variable with Segoe UI fallback."""
        cache = getattr(self, "_font_cache", None)
        if cache is None:
            self._font_cache = {}
            cache = self._font_cache
        key = (size, weight or "normal")
        cached = cache.get(key)
        if cached is not None:
            return cached
        for family in (FONT_FAMILY, FONT_FAMILY_FALLBACK, "Segoe UI"):
            try:
                kwargs = {"family": family, "size": size}
                if weight and weight != "normal":
                    kwargs["weight"] = weight
                font = ctk.CTkFont(**kwargs)
                cache[key] = font
                return font
            except Exception:
                continue
        font = ctk.CTkFont(family="Segoe UI", size=size)
        cache[key] = font
        return font

    def _language_flag_label(self, plain_language):
        """Return display label with country flag for a plain language name."""
        return LANGUAGE_FLAG_LABELS.get(plain_language, plain_language)

    def _strip_language_flag(self, display_value):
        """Convert a flagged dropdown label back to the plain language name."""
        if not display_value:
            return display_value
        for plain, flagged in LANGUAGE_FLAG_LABELS.items():
            if display_value == flagged:
                return plain
        for plain in LANGUAGE_FLAG_LABELS:
            if plain in display_value:
                return plain
        return display_value.strip()

    def _flagged_language_values(self, plain_values):
        """Build flagged dropdown values from plain language names."""
        return [self._language_flag_label(name) for name in plain_values]

    def _card_config(self):
        """Shared card frame styling."""
        return {
            "fg_color": COLORS["card_bg"],
            "corner_radius": RADII["card"],
            "border_width": 1,
            "border_color": COLORS["border"],
        }

    def _glass_button_config(self, width=None, height=None):
        """Dark glass buttons for header and secondary footer actions."""
        cfg = {
            "height": height or HEADER_CONTROL_HEIGHT,
            "font": self._ui_font(FONTS["button"][1], "bold"),
            "fg_color": COLORS["glass_button_fg"],
            "hover_color": COLORS["glass_button_hover"],
            "text_color": COLORS["glass_button_text"],
            "border_width": 1,
            "border_color": COLORS["glass_button_border"],
            "corner_radius": RADII["glass_button"],
        }
        if width is not None:
            cfg["width"] = width
        return cfg

    def _language_dropdown_wrapper_config(self, width=None):
        """Bordered frame wrapping header language dropdowns for a full outline."""
        cfg = {
            "fg_color": COLORS["glass_button_fg"],
            "border_width": DROPDOWN_WRAPPER_BORDER_WIDTH,
            "border_color": COLORS["glass_button_border"],
            "corner_radius": RADII["glass_button"],
            "height": DROPDOWN_HEIGHT,
        }
        if width is not None:
            cfg["width"] = width
        return cfg

    def _header_glass_combo_config(self, width=None):
        """Header language combo inside wrapper — no border; inset within wrapper outline."""
        cfg = {
            "font": self._ui_font(13),
            "fg_color": COLORS["glass_button_fg"],
            "border_width": DROPDOWN_INNER_BORDER_WIDTH,
            "border_color": COLORS["glass_button_border"],
            "button_color": COLORS["glass_button_fg"],
            "button_hover_color": COLORS["glass_button_hover"],
            "dropdown_fg_color": COLORS["card_bg"],
            "dropdown_hover_color": COLORS["input_bg_hover"],
            "text_color": COLORS["text_primary"],
            "corner_radius": 0,
            "state": "readonly",
        }
        if width is not None:
            cfg["width"] = width
        return cfg

    def _glass_combo_config(self, width=None):
        """Glass-styled language dropdown for the header."""
        return {
            "width": width or DROPDOWN_WIDTH,
            "height": DROPDOWN_HEIGHT,
            "font": self._ui_font(13),
            "fg_color": COLORS["glass_button_fg"],
            "border_color": COLORS["glass_button_border"],
            "border_width": DROPDOWN_BORDER_WIDTH,
            "button_color": COLORS["input_bg"],
            "button_hover_color": COLORS["input_bg_hover"],
            "dropdown_fg_color": COLORS["card_bg"],
            "dropdown_hover_color": COLORS["input_bg_hover"],
            "text_color": COLORS["text_primary"],
            "corner_radius": RADII["combo"],
            "state": "readonly",
        }

    def _glass_icon_button_config(self, size=None, font_size=16):
        """Square glass icon button (swap, close, hamburger)."""
        button_size = size or SWAP_BUTTON_SIZE
        cfg = self._glass_button_config(width=button_size, height=button_size)
        cfg["font"] = self._ui_font(font_size, "bold")
        return cfg

    def _primary_button_config(self, width=None, height=None):
        """Primary action buttons: Start Listening (footer)."""
        cfg = {
            "height": height or FOOTER_BTN_HEIGHT,
            "font": self._ui_font(FONTS["button"][1], "bold"),
            "fg_color": COLORS["accent_blue"],
            "hover_color": COLORS["accent_blue_hover"],
            "text_color": COLORS["text_primary"],
            "corner_radius": RADII["button"],
        }
        if width is not None:
            cfg["width"] = width
        return cfg

    def _secondary_button_config(self, width=None, height=None):
        """Secondary actions: Copy, Export, Clear — dark glass style."""
        cfg = {
            "height": height or FOOTER_BTN_HEIGHT,
            "font": self._ui_font(FONTS["button"][1], "bold"),
            "fg_color": COLORS["glass_button_fg"],
            "hover_color": COLORS["glass_button_hover"],
            "text_color": COLORS["text_primary"],
            "border_width": 1,
            "border_color": COLORS["glass_button_border"],
            "corner_radius": RADII["glass_button"],
        }
        if width is not None:
            cfg["width"] = width
        return cfg

    def _icon_button_config(self, size=None, font_size=16):
        """Legacy icon button config (footer/compact controls)."""
        button_size = size or SWAP_BUTTON_SIZE
        return {
            "width": button_size,
            "height": button_size,
            "font": self._ui_font(font_size, "bold"),
            "fg_color": COLORS["input_bg"],
            "hover_color": COLORS["input_bg_hover"],
            "text_color": COLORS["text_primary"],
            "border_width": 1,
            "border_color": COLORS["border"],
            "corner_radius": RADII["button"],
        }

    def _combo_config(self, width=None):
        """Language dropdown styling (compact menu)."""
        return {
            "width": width or DROPDOWN_WIDTH,
            "height": DROPDOWN_HEIGHT,
            "font": self._ui_font(13),
            "fg_color": COLORS["input_bg"],
            "border_color": COLORS["border"],
            "border_width": DROPDOWN_BORDER_WIDTH,
            "button_color": COLORS["accent_blue"],
            "button_hover_color": COLORS["accent_blue_hover"],
            "dropdown_fg_color": COLORS["card_bg"],
            "dropdown_hover_color": COLORS["input_bg_hover"],
            "text_color": COLORS["text_primary"],
            "corner_radius": RADII["combo"],
            "state": "readonly",
        }

    def _make_language_combo(self, master, plain_values, variable, changed_key):
        """Language dropdown with flag labels; returns (wrapper_frame, combo)."""
        flagged_values = self._flagged_language_values(plain_values)

        def on_select(choice):
            plain = self._strip_language_flag(choice)
            if variable.get() != plain:
                variable.set(plain)
            self.on_language_change(changed_key)

        wrapper = ctk.CTkFrame(
            master=master,
            **self._language_dropdown_wrapper_config(width=DROPDOWN_WIDTH),
        )
        wrapper.pack_propagate(False)

        combo = ctk.CTkComboBox(
            master=wrapper,
            values=flagged_values,
            command=on_select,
            **self._header_glass_combo_config(),
        )
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_rowconfigure(0, weight=1)
        inset = DROPDOWN_WRAPPER_BORDER_WIDTH
        combo.grid(row=0, column=0, sticky="nsew", padx=inset, pady=inset)
        combo.set(self._language_flag_label(variable.get()))
        return wrapper, combo

    def _sync_language_combo_displays(self):
        """Refresh flagged labels on header language dropdowns."""
        if hasattr(self, "source_combo") and self.source_combo is not None:
            self.source_combo.set(self._language_flag_label(self.source_language.get()))
        if hasattr(self, "target_combo") and self.target_combo is not None:
            self.target_combo.set(self._language_flag_label(self.target_language.get()))

    # -----------------------------------------------------------------------
    # Logo
    # -----------------------------------------------------------------------
    def _load_logo(self):
        """Load logo.png from assets as a 36x36 CTkImage."""
        logo_path = ASSETS_DIR / "logo.png"
        try:
            pil_logo = Image.open(logo_path).resize((36, 36), Image.Resampling.LANCZOS)
            self.logo_image = ctk.CTkImage(
                light_image=pil_logo,
                dark_image=pil_logo,
                size=(36, 36),
            )
        except Exception as exc:
            print(f"Warning: Could not load logo.png ({exc}). Logo will be omitted.")
            self.logo_image = None

    def _deferred_apply_logo(self):
        """Load header logo after the window is interactive."""
        if getattr(self, "logo_label", None) is not None:
            return
        self._load_logo()
        brand_block = getattr(self, "brand_block", None)
        if self.logo_image is None or brand_block is None:
            return
        try:
            children = list(brand_block.winfo_children())
            self.logo_label = ctk.CTkLabel(
                master=brand_block,
                text="",
                image=self.logo_image,
                width=36,
                height=36,
            )
            if children:
                self.logo_label.pack(side="left", padx=(0, 10), before=children[0])
            else:
                self.logo_label.pack(side="left", padx=(0, 10))
        except Exception:
            self.logo_label = None

    def _make_combo(self, master, values, variable, width=None):
        """Styled language dropdown (compact hamburger menu)."""
        return ctk.CTkComboBox(
            master=master,
            values=values,
            variable=variable,
            **self._combo_config(width=width),
        )

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    def create_header_frame(self):
        """Top header: brand + language controls left, summary actions right."""
        if UI_PERFORMANCE_MODE:
            self.logo_image = None
        else:
            self._load_logo()

        self.header_frame = ctk.CTkFrame(
            master=self,
            fg_color=COLORS["header_bg"],
            corner_radius=0,
            border_width=0,
        )
        self.header_frame.grid(row=ROOT_ROW_HEADER, column=0, sticky="ew", padx=0, pady=0)
        self.header_frame.grid_columnconfigure(1, weight=1)
        self.header_frame.grid_rowconfigure(1, weight=0)

        pad_x = SPACING["window_pad_x"]

        self.left_header_cluster = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.left_header_cluster.grid(row=0, column=0, sticky="w", padx=(pad_x, 8), pady=10)

        self.brand_block = ctk.CTkFrame(self.left_header_cluster, fg_color="transparent")
        self.brand_block.pack(side="left", padx=(0, 12))

        if self.logo_image is not None:
            self.logo_label = ctk.CTkLabel(
                master=self.brand_block,
                text="",
                image=self.logo_image,
                width=36,
                height=36,
            )
            self.logo_label.pack(side="left", padx=(0, 10))
        elif UI_PERFORMANCE_MODE:
            self.logo_label = None
            self.after(DEFER_LOGO_MS, self._deferred_apply_logo)

        titles = ctk.CTkFrame(self.brand_block, fg_color="transparent")
        titles.pack(side="left")
        self.brand_label = ctk.CTkLabel(
            master=titles,
            text="Alpha",
            font=self._ui_font(FONTS["brand"][1], "bold"),
            text_color=COLORS["text_primary"],
        )
        self.brand_label.pack(anchor="w")
        self.brand_sub_label = ctk.CTkLabel(
            master=titles,
            text="Meeting Assistant",
            font=self._ui_font(FONTS["brand_sub"][1]),
            text_color=COLORS["text_secondary"],
        )
        self.brand_sub_label.pack(anchor="w")

        self.header_lang_frame = ctk.CTkFrame(self.left_header_cluster, fg_color="transparent")
        self.header_lang_frame.pack(side="left")

        self.source_combo_wrap, self.source_combo = self._make_language_combo(
            self.header_lang_frame, SOURCE_LANGUAGES, self.source_language, "source"
        )
        self.source_combo_wrap.pack(side="left", padx=(0, 6))

        self.swap_button = ctk.CTkButton(
            master=self.header_lang_frame,
            text="⇄",
            command=self.swap_languages,
            **self._glass_icon_button_config(),
        )
        self.swap_button.pack(side="left", padx=(0, 6))

        self.target_combo_wrap, self.target_combo = self._make_language_combo(
            self.header_lang_frame, TARGET_LANGUAGES, self.target_language, "target"
        )
        self.target_combo_wrap.pack(side="left")

        self.right_header_cluster = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.right_header_cluster.grid(row=0, column=2, sticky="e", padx=(8, pad_x), pady=10)

        self.summary_button = ctk.CTkButton(
            master=self.right_header_cluster,
            text=MEETING_SUMMARY_BUTTON_TEXT,
            command=self.show_meeting_summary,
            **self._glass_button_config(width=SUMMARY_BUTTON_WIDTH),
        )
        self.summary_button.pack(side="left", padx=(0, 8))

        self.always_on_top_switch = ctk.CTkSwitch(
            master=self.right_header_cluster,
            text="Always on Top",
            font=self._ui_font(FONTS["caption"][1]),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["input_bg"],
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["text_primary"],
            button_hover_color="#e2e8f0",
            command=self.toggle_always_on_top,
        )
        self.always_on_top_switch.pack(side="left")

        self.listening_label = None
        self.translate_label = None
        self.header_controls = self.left_header_cluster
        self.normal_header_widgets = [
            self.source_combo,
            self.swap_button,
            self.target_combo,
            self.summary_button,
            self.always_on_top_switch,
        ]

        self.hamburger_button = ctk.CTkButton(
            master=self.right_header_cluster,
            text="≡",
            command=self.toggle_hamburger_menu,
            **self._glass_icon_button_config(font_size=22),
        )
        self.hamburger_button.pack_forget()

    # -----------------------------------------------------------------------
    # Hamburger menu (compact view)
    # -----------------------------------------------------------------------
    def create_hamburger_menu(self):
        """Create compact dropdown menu panel below the header."""
        self.menu_dropdown_frame = ctk.CTkFrame(
            master=self.header_frame,
            fg_color=COLORS["card_bg"],
            corner_radius=0,
            border_width=1,
            border_color=COLORS["border"],
        )

        menu_listening_label = ctk.CTkLabel(
            master=self.menu_dropdown_frame,
            text="Listening to:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        menu_listening_label.pack(fill="x", padx=15, pady=(12, 4))

        self.source_combo_menu = ctk.CTkComboBox(
            master=self.menu_dropdown_frame,
            values=SOURCE_LANGUAGES,
            variable=self.source_language,
            width=260,
            height=32,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color=COLORS["dropdown_bg"],
            border_color=COLORS["border"],
            border_width=DROPDOWN_BORDER_WIDTH,
            button_color=COLORS["accent_blue"],
            button_hover_color="#3a8eef",
            dropdown_fg_color=COLORS["dropdown_bg"],
            dropdown_hover_color="#4d4d5d",
            text_color=COLORS["text_primary"],
            corner_radius=8,
            state="readonly",
        )
        self.source_combo_menu.pack(fill="x", padx=15, pady=(0, 8))

        menu_translate_label = ctk.CTkLabel(
            master=self.menu_dropdown_frame,
            text="Translate to:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        menu_translate_label.pack(fill="x", padx=15, pady=(4, 4))

        self.target_combo_menu = ctk.CTkComboBox(
            master=self.menu_dropdown_frame,
            values=TARGET_LANGUAGES,
            variable=self.target_language,
            width=260,
            height=32,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color=COLORS["dropdown_bg"],
            border_color=COLORS["border"],
            border_width=DROPDOWN_BORDER_WIDTH,
            button_color=COLORS["accent_blue"],
            button_hover_color="#3a8eef",
            dropdown_fg_color=COLORS["dropdown_bg"],
            dropdown_hover_color="#4d4d5d",
            text_color=COLORS["text_primary"],
            corner_radius=8,
            state="readonly",
        )
        self.target_combo_menu.pack(fill="x", padx=15, pady=(0, 8))

        self.listen_button_menu = ctk.CTkButton(
            master=self.menu_dropdown_frame,
            text="Start Listening",
            height=FOOTER_BTN_HEIGHT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.toggle_listening,
        )
        self.listen_button_menu.pack(fill="x", padx=15, pady=(4, 8))

        self.copy_translation_btn_menu = ctk.CTkButton(
            master=self.menu_dropdown_frame,
            text="Copy Translation",
            command=self.copy_translation_to_clipboard,
            **self._glass_button_config(),
        )
        self.copy_translation_btn_menu.pack(fill="x", padx=15, pady=(4, 8))

        self.always_on_top_switch_menu = ctk.CTkSwitch(
            master=self.menu_dropdown_frame,
            text="Always on Top",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["dropdown_bg"],
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["text_primary"],
            button_hover_color="#e0e0e0",
            command=self.toggle_always_on_top,
        )
        self.always_on_top_switch_menu.pack(anchor="w", padx=15, pady=(4, 12))

    # -----------------------------------------------------------------------
    # Responsive layout switching
    # -----------------------------------------------------------------------
    def on_window_resize(self, event):
        """React to window resize events and apply responsive layout (debounced)."""
        if event.widget is not self:
            return
        job = getattr(self, "_resize_layout_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._resize_layout_job = self.after(
            RESIZE_DEBOUNCE_MS, self._apply_responsive_layout_debounced
        )

    def _apply_responsive_layout_debounced(self):
        self._resize_layout_job = None
        self._apply_responsive_layout()

    def _get_layout_mode(self, width):
        """Return wide, medium, or compact based on window width."""
        if width >= LAYOUT_WIDE_BREAKPOINT:
            return "wide"
        if width >= LAYOUT_HAMBURGER_BREAKPOINT:
            return "medium"
        return "compact"

    def _apply_responsive_layout(self):
        """Apply header, content, footer, and status bar layout for current width."""
        try:
            width = self.winfo_width()
            if width <= 1:
                return

            mode = self._get_layout_mode(width)
            if (
                UI_PERFORMANCE_MODE
                and width == getattr(self, "_last_layout_width", -1)
                and mode == getattr(self, "_last_layout_mode_applied", None)
            ):
                return
            self._last_layout_width = width
            if mode != self._layout_mode:
                self._layout_mode = mode
            self._last_layout_mode_applied = mode

            pad_x = (
                SPACING["window_pad_compact_x"]
                if width < LAYOUT_MEDIUM_BREAKPOINT
                else SPACING["window_pad_x"]
            )
            if hasattr(self, "content_wrapper") and self.content_wrapper is not None:
                self.content_wrapper.grid_configure(padx=pad_x)
            if hasattr(self, "status_bar_frame") and self.status_bar_frame is not None:
                self.status_bar_frame.grid_configure(padx=pad_x)
            if hasattr(self, "footer_frame") and self.footer_frame is not None:
                self.footer_frame.grid_configure(padx=0)
                for child in self.footer_frame.winfo_children():
                    if isinstance(child, ctk.CTkFrame):
                        child.grid_configure(padx=pad_x)
            if self.brand_block is not None:
                self.brand_block.pack_configure(padx=(pad_x, 8 if mode == "compact" else 12))

            self._apply_header_layout(width, mode)
            self._apply_content_layout(mode)
            self._apply_footer_layout(width)
            self._apply_status_bar_layout(width)
            self._schedule_waveform_layout()
        except Exception as exc:
            print(f"Error applying responsive layout: {exc}")

    def _schedule_waveform_layout(self):
        if not UI_PERFORMANCE_MODE:
            self._apply_waveform_layout()
            return
        job = getattr(self, "_waveform_layout_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._waveform_layout_job = self.after(400, self._apply_waveform_layout_debounced)

    def _apply_waveform_layout_debounced(self):
        self._waveform_layout_job = None
        self._apply_waveform_layout()

    def _apply_header_layout(self, width, mode):
        """Switch header between wide, medium inline, and hamburger layouts."""
        is_hamburger = mode == "compact"

        if is_hamburger:
            if self._compact_mode is not True:
                self._compact_mode = True
                self.show_compact_layout()
        else:
            if self._compact_mode is not False:
                self._compact_mode = False
                self.show_normal_layout()
            self._pack_header_controls(mode, width)

    def _pack_header_controls(self, mode, width):
        """Layout header language controls for wide or medium layouts."""
        if self.header_lang_frame is None:
            return

        combo_width = DROPDOWN_WIDTH if mode == "wide" else 128
        for wrapper, combo in (
            (self.source_combo_wrap, self.source_combo),
            (self.target_combo_wrap, self.target_combo),
        ):
            if wrapper is not None:
                wrapper.configure(width=combo_width, height=DROPDOWN_HEIGHT)

        if mode == "wide":
            self.summary_button.configure(
                text=MEETING_SUMMARY_BUTTON_TEXT,
                width=SUMMARY_BUTTON_WIDTH,
            )
        else:
            self.summary_button.configure(
                text="Summary",
                width=108 if width < LAYOUT_MEDIUM_BREAKPOINT + 80 else SUMMARY_BUTTON_WIDTH - 20,
            )

        if width >= 560:
            self.always_on_top_switch.pack(side="left")
        else:
            self.always_on_top_switch.pack_forget()

        if self.brand_sub_label is not None:
            if width < 480:
                self.brand_sub_label.pack_forget()
            else:
                self.brand_sub_label.pack(anchor="w")

        if self.brand_label is not None and width < 460:
            self.brand_label.configure(font=self._ui_font(18, "bold"))
        elif self.brand_label is not None:
            self.brand_label.configure(font=self._ui_font(FONTS["brand"][1], "bold"))

    def show_normal_layout(self):
        """Show header controls inline; hide the hamburger menu."""
        try:
            self.hamburger_button.pack_forget()
            self._hide_hamburger_menu()
            if self.header_lang_frame is not None and not self.header_lang_frame.winfo_ismapped():
                self.header_lang_frame.pack(side="left")
            if self.summary_button is not None:
                self.summary_button.pack(side="left", padx=(0, 8))
        except Exception as exc:
            print(f"Error showing normal layout: {exc}")

    def show_compact_layout(self):
        """Show logo/title and hamburger button in compact header."""
        try:
            if self.header_lang_frame is not None:
                self.header_lang_frame.pack_forget()
            if self.summary_button is not None:
                self.summary_button.pack_forget()
            if self.always_on_top_switch is not None:
                self.always_on_top_switch.pack_forget()
            self.hamburger_button.pack(side="left", padx=(8, 0))
            self._hide_hamburger_menu()
        except Exception as exc:
            print(f"Error showing compact layout: {exc}")

    def toggle_hamburger_menu(self):
        """Toggle the compact dropdown menu below the header."""
        try:
            if self._menu_visible:
                self._hide_hamburger_menu()
            else:
                pad_x = SPACING["window_pad_x"]
                self.menu_dropdown_frame.grid(
                    row=1,
                    column=0,
                    columnspan=3,
                    sticky="ew",
                    padx=pad_x,
                    pady=(0, 8),
                )
                self._menu_visible = True
        except Exception as exc:
            print(f"Error toggling hamburger menu: {exc}")

    def _hide_hamburger_menu(self):
        """Hide the compact dropdown menu."""
        self.menu_dropdown_frame.grid_remove()
        self._menu_visible = False

    # -----------------------------------------------------------------------
    # Status bar
    # -----------------------------------------------------------------------
    def create_status_bar(self):
        """Listening status strip with LIVE indicator, waveform, and timer."""
        self.status_bar_frame = ctk.CTkFrame(
            master=self,
            height=54,
            **self._card_config(),
        )
        self.status_bar_frame.grid(
            row=ROOT_ROW_STATUS,
            column=0,
            sticky="ew",
            padx=SPACING["window_pad_x"],
            pady=(SPACING["section_gap"], 0),
        )
        self.status_bar_frame.pack_propagate(False)

        inner = ctk.CTkFrame(self.status_bar_frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=SPACING["card_pad"], pady=10)

        live_wrap = ctk.CTkFrame(inner, fg_color="transparent")
        live_wrap.pack(side="left")

        self.live_pill = ctk.CTkFrame(
            master=live_wrap,
            fg_color=COLORS["input_bg"],
            corner_radius=RADII["status_pill"],
            border_width=1,
            border_color=COLORS["border_soft"],
        )
        self.live_pill.pack(side="left", padx=(0, 10))

        self.live_indicator = ctk.CTkLabel(
            master=self.live_pill,
            text="○ IDLE",
            font=ctk.CTkFont(family=FONTS["status"][0], size=FONTS["status"][1], weight="bold"),
            text_color=COLORS["live_idle"],
        )
        self.live_indicator.pack(padx=10, pady=3)

        self.status_text_label = ctk.CTkLabel(
            master=live_wrap,
            text="Ready to listen",
            font=ctk.CTkFont(family=FONTS["body"][0], size=FONTS["body"][1]),
            text_color=COLORS["text_secondary"],
        )
        self.status_text_label.pack(side="left")

        self.waveform_canvas = tk.Canvas(
            inner,
            width=WAVEFORM_CANVAS_WIDTH,
            height=WAVEFORM_CANVAS_HEIGHT,
            bg=COLORS["card_bg"],
            highlightthickness=0,
            bd=0,
        )
        self.waveform_canvas.pack(side="left", padx=(12, 0))
        if UI_PERFORMANCE_MODE:
            self.after(DEFER_WAVEFORM_DRAW_MS, lambda: self._draw_waveform(idle=True))
        else:
            self._draw_waveform(idle=True)

        self._status_right_cluster = ctk.CTkFrame(inner, fg_color="transparent")
        self._status_right_cluster.pack(side="right")

        self.timer_label = ctk.CTkLabel(
            master=self._status_right_cluster,
            text="00:00",
            font=ctk.CTkFont(
                family=FONTS["timer"][0],
                size=FONTS["timer"][1],
                weight="bold",
            ),
            text_color=COLORS["text_secondary"],
        )
        self.timer_label.pack(side="right", padx=(12, 0))

        self.signal_label = ctk.CTkLabel(
            master=self._status_right_cluster,
            text="● Standby",
            font=ctk.CTkFont(family=FONTS["caption"][0], size=FONTS["caption"][1]),
            text_color=COLORS["text_muted"],
        )
        self.signal_label.pack(side="right", padx=(0, 8))

    def _get_waveform_bar_count(self):
        """14 bars in hamburger layout; 35 bars when header controls are visible."""
        if self._compact_mode is True:
            return WAVEFORM_BAR_COUNT
        return WAVEFORM_BAR_COUNT_WIDE

    def _get_waveform_canvas_width(self):
        """Match canvas width to bar count for the current layout."""
        if self._compact_mode is True:
            return WAVEFORM_CANVAS_WIDTH
        return WAVEFORM_CANVAS_WIDTH_WIDE

    def _apply_waveform_layout(self):
        """Resize waveform canvas and redraw when hamburger layout toggles."""
        if self.waveform_canvas is None:
            return
        self.waveform_canvas.configure(width=self._get_waveform_canvas_width())
        self._draw_waveform(idle=not self.is_listening)

    def _draw_waveform(self, idle=False):
        """Draw subtle animated bars on the status canvas (visual only)."""
        if self.waveform_canvas is None:
            return
        self.waveform_canvas.delete("all")
        bar_w = 5
        gap = 3
        x = 4
        max_h = WAVEFORM_CANVAS_HEIGHT - 4
        base_y = WAVEFORM_CANVAS_HEIGHT - 2

        for i in range(self._get_waveform_bar_count()):
            if idle:
                h = 3
                color = COLORS["waveform_bar_dim"]
            else:
                phase = self._waveform_phase + (i * 0.55)
                h = int(5 + (max_h - 8) * (0.35 + 0.65 * abs(math.sin(phase))))
                color = (
                    COLORS["waveform_bar"]
                    if h > max_h * 0.45
                    else COLORS["waveform_bar_mid"]
                )
            y0 = base_y - h
            self.waveform_canvas.create_rectangle(
                x, y0, x + bar_w, base_y, fill=color, outline=""
            )
            x += bar_w + gap

    def _animate_waveform(self):
        """Animate waveform only while listening; low-frequency refresh."""
        if not self.is_listening:
            self._waveform_job = None
            self._draw_waveform(idle=True)
            return
        self._waveform_phase += 0.35
        self._draw_waveform(idle=False)
        self._waveform_job = self.after(WAVEFORM_ANIMATION_MS, self._animate_waveform)

    def _animate_live_pulse(self):
        """Subtle LIVE pill pulse while listening."""
        if not self.is_listening or self.live_indicator is None:
            self._live_pulse_job = None
            return
        current = self.live_indicator.cget("text_color")
        next_color = (
            COLORS["accent_red_glow"]
            if current == COLORS["live_glow"]
            else COLORS["live_glow"]
        )
        self.live_indicator.configure(text_color=next_color)
        self._live_pulse_job = self.after(900, self._animate_live_pulse)

    def _update_timer(self):
        """Update session timer while listening."""
        if not self.is_listening or self._listen_start_time is None:
            return
        elapsed = int(time.time() - self._listen_start_time)
        mins, secs = divmod(elapsed, 60)
        hours, mins = divmod(mins, 60)
        if self.timer_label is not None:
            if hours > 0:
                self.timer_label.configure(text=f"{hours:02d}:{mins:02d}:{secs:02d}")
            else:
                self.timer_label.configure(text=f"{mins:02d}:{secs:02d}")
        self._timer_job = self.after(1000, self._update_timer)

    def _update_status_bar(self, listening=False):
        """Refresh status bar visuals for idle vs listening."""
        if self.live_indicator is None:
            return
        if listening:
            self.live_indicator.configure(text="● LIVE", text_color=COLORS["live_glow"])
            if self.live_pill is not None:
                self.live_pill.configure(
                    fg_color=COLORS["accent_red_soft"],
                    border_color=COLORS["accent_red"],
                )
            self.status_text_label.configure(
                text="Listening — capturing audio",
                text_color=COLORS["text_primary"],
            )
            self.signal_label.configure(text="● Signal OK", text_color=COLORS["accent_green"])
            self.timer_label.configure(text_color=COLORS["text_primary"])
            self._listen_start_time = time.time()
            self._waveform_phase = 0
            if self._waveform_job is not None:
                self.after_cancel(self._waveform_job)
            if self._live_pulse_job is not None:
                self.after_cancel(self._live_pulse_job)
            self._animate_waveform()
            self._animate_live_pulse()
            self._update_timer()
        else:
            self.live_indicator.configure(text="○ IDLE", text_color=COLORS["live_idle"])
            if self.live_pill is not None:
                self.live_pill.configure(
                    fg_color=COLORS["status_active_bg"],
                    border_color=COLORS["border_soft"],
                )
            self.status_text_label.configure(
                text="Ready to listen",
                text_color=COLORS["text_secondary"],
            )
            self.signal_label.configure(text="● Standby", text_color=COLORS["text_muted"])
            if self.timer_label is not None:
                self.timer_label.configure(text="00:00", text_color=COLORS["text_secondary"])
            self._listen_start_time = None
            if self._waveform_job is not None:
                self.after_cancel(self._waveform_job)
                self._waveform_job = None
            if self._timer_job is not None:
                self.after_cancel(self._timer_job)
                self._timer_job = None
            if self._live_pulse_job is not None:
                self.after_cancel(self._live_pulse_job)
                self._live_pulse_job = None
            self._draw_waveform(idle=True)

    # -----------------------------------------------------------------------
    # Right-side Meeting Summary panel
    # -----------------------------------------------------------------------
    def show_summary_panel(self):
        """Instantly show the right-side Meeting Summary column."""
        if self.right_column is None:
            return
        self.summary_panel_visible = True
        width = self.winfo_width()
        mode = self._layout_mode or self._get_layout_mode(
            width if width > 1 else LAYOUT_WIDE_BREAKPOINT
        )
        self._apply_content_layout(mode)

    def hide_summary_panel(self):
        """Instantly hide the right-side Meeting Summary column."""
        if self.right_column is None:
            return
        self.summary_panel_visible = False
        width = self.winfo_width()
        mode = self._layout_mode or self._get_layout_mode(
            width if width > 1 else LAYOUT_WIDE_BREAKPOINT
        )
        self._apply_content_layout(mode)

    def toggle_summary_panel(self):
        """Toggle right-side Meeting Summary visibility."""
        if self.summary_panel_visible:
            self.hide_summary_panel()
        else:
            self.show_summary_panel()

    def _create_summary_card(self):
        """Right column Meeting Summary card with close control."""
        self.summary_outer = ctk.CTkFrame(
            master=self.right_column,
            **self._card_config(),
        )
        self.summary_outer.grid(row=0, column=0, sticky="nsew")
        self.summary_outer.grid_rowconfigure(1, weight=1)
        self.summary_outer.grid_columnconfigure(0, weight=1)
        self.summary_card = self.summary_outer

        header = ctk.CTkFrame(self.summary_outer, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=SPACING["card_pad"], pady=(14, 8))
        header.grid_columnconfigure(0, weight=1)

        self.summary_title_label = ctk.CTkLabel(
            master=header,
            text=SUMMARY_PANEL_TITLE,
            font=self._ui_font(FONTS["section_title"][1], "bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        self.summary_title_label.grid(row=0, column=0, sticky="w")

        self.summary_panel_close_btn = ctk.CTkButton(
            master=header,
            text=SUMMARY_CLOSE_ICON,
            command=self.hide_summary_panel,
            **self._glass_icon_button_config(size=SMALL_BUTTON_HEIGHT, font_size=18),
        )
        self.summary_panel_close_btn.grid(row=0, column=1, sticky="e")

        body_frame = ctk.CTkFrame(
            self.summary_outer,
            fg_color=COLORS["card_bg_soft"],
            corner_radius=RADII["button"],
            border_width=1,
            border_color=COLORS["border_soft"],
        )
        body_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=SPACING["card_pad"],
            pady=(0, SPACING["card_pad"]),
        )
        body_frame.grid_rowconfigure(0, weight=1)
        body_frame.grid_columnconfigure(0, weight=1)

        text_shell = ctk.CTkFrame(body_frame, fg_color="transparent")
        text_shell.grid(row=0, column=0, sticky="nsew")

        summary_scroll = ctk.CTkScrollbar(
            master=text_shell,
            orientation="vertical",
            button_color=COLORS["border"],
            button_hover_color=COLORS["border_soft"],
            fg_color=COLORS["card_bg_soft"],
        )
        summary_scroll.pack(side="right", fill="y")

        self.summary_body_box = tk.Text(
            master=text_shell,
            bg=COLORS["card_bg_soft"],
            fg=COLORS["text_disabled"],
            font=FONTS["placeholder"],
            relief="flat",
            borderwidth=0,
            wrap="word",
            highlightthickness=0,
            padx=12,
            pady=12,
            state="disabled",
            yscrollcommand=summary_scroll.set,
        )
        self.summary_body_box.pack(side="left", fill="both", expand=True)
        summary_scroll.configure(command=self.summary_body_box.yview)

        self.summary_body_box.configure(state="normal")
        self.summary_body_box.insert("1.0", PLACEHOLDER_SUMMARY)
        self.summary_body_box.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Main content
    # -----------------------------------------------------------------------
    def create_main_content(self):
        """Two-column layout: transcript + translation | Meeting Summary."""
        self.content_wrapper = ctk.CTkFrame(
            master=self,
            fg_color="transparent",
            corner_radius=0,
        )
        self.content_wrapper.grid(
            row=ROOT_ROW_CONTENT,
            column=0,
            sticky="nsew",
            padx=SPACING["window_pad_x"],
            pady=(SPACING["section_gap"], SPACING["section_gap_compact"]),
        )
        self.content_wrapper.grid_columnconfigure(0, weight=7)
        self.content_wrapper.grid_columnconfigure(1, weight=3)
        self.content_wrapper.grid_rowconfigure(0, weight=1)

        self.left_column = ctk.CTkFrame(self.content_wrapper, fg_color="transparent")
        self.left_column.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._apply_left_column_panel_weights()
        self.left_column.grid_columnconfigure(0, weight=1)

        self.right_column = ctk.CTkFrame(self.content_wrapper, fg_color="transparent")
        self.right_column.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.right_column.grid_rowconfigure(0, weight=1)
        self.right_column.grid_columnconfigure(0, weight=1)

        self.paned = self.left_column

        self.initial_verse_frame = self._create_verse_section(
            master=self.left_column,
            title=SECTION_TRANSCRIPT_TITLE,
            font_size=TRANSCRIPT_BODY_FONT[1],
            body_color=COLORS["text_secondary"],
            attr_name="initial_verse_box",
            is_initial=True,
            grid_row=0,
            placeholder_text=PLACEHOLDER_TRANSCRIPT,
            placeholder_font=FONTS["placeholder"],
        )
        self.translated_verse_frame = self._create_verse_section(
            master=self.left_column,
            title=SECTION_TRANSLATION_TITLE,
            font_size=TRANSLATION_BODY_FONT[1],
            body_color=COLORS["text_primary"],
            attr_name="translated_verse_box",
            is_initial=False,
            grid_row=1,
            placeholder_text=PLACEHOLDER_TRANSLATION,
            placeholder_font=FONTS["placeholder_lg"],
        )

        self._create_summary_card()

    def _apply_content_layout(self, mode):
        """Reflow left/right columns — ~70% transcript, ~30% summary when visible."""
        if not hasattr(self, "content_wrapper") or self.content_wrapper is None:
            return
        if self.left_column is None:
            return

        show_summary = (
            self.summary_panel_visible
            and self.right_column is not None
            and mode != "compact"
        )

        if show_summary:
            self.content_wrapper.grid_columnconfigure(0, weight=7)
            self.content_wrapper.grid_columnconfigure(1, weight=3)
            self.left_column.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)
            self.right_column.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)
        else:
            self.content_wrapper.grid_columnconfigure(0, weight=1)
            self.content_wrapper.grid_columnconfigure(1, weight=0)
            self.left_column.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
            if self.right_column is not None:
                self.right_column.grid_remove()

    def _apply_status_bar_layout(self, width):
        """Hide non-essential status elements on very narrow windows."""
        if self.waveform_canvas is None:
            return
        compact = width < LAYOUT_STATUS_COMPACT_BREAKPOINT
        if compact:
            self.waveform_canvas.pack_forget()
            if self.status_text_label is not None:
                self.status_text_label.configure(text="Ready")
        else:
            if not self.waveform_canvas.winfo_ismapped():
                self.waveform_canvas.pack(
                    side="left",
                    padx=(16, 0),
                    before=self._status_right_cluster,
                )
            if self.status_text_label is not None and not self.is_listening:
                self.status_text_label.configure(text="Ready to listen")

        if self.signal_label is not None:
            if compact:
                self.signal_label.pack_forget()
            elif not self.signal_label.winfo_ismapped():
                self.signal_label.pack(side="right", padx=(0, 10))

    def _apply_footer_layout(self, width):
        """Reflow footer: listen on the left, actions on the right."""
        if not self._footer_buttons or self.footer_btn_row is None:
            return

        compact = width < LAYOUT_FOOTER_WRAP_BREAKPOINT
        hamburger_layout = self._compact_mode is True
        gap = (
            SPACING["footer_btn_gap_compact"]
            if width < LAYOUT_MEDIUM_BREAKPOINT
            else SPACING["footer_btn_gap"]
        )
        btn_h = FOOTER_BTN_HEIGHT_COMPACT if width < 500 else FOOTER_BTN_HEIGHT
        btn_w_primary = (
            FOOTER_BTN_WIDTH_COMPACT if width < 500 else FOOTER_BTN_WIDTH
        )
        btn_w_secondary = (
            FOOTER_BTN_WIDTH_COMPACT if width < 500 else FOOTER_BTN_WIDTH_SECONDARY
        )
        pad_x = (
            SPACING["window_pad_compact_x"]
            if width < LAYOUT_MEDIUM_BREAKPOINT
            else SPACING["window_pad_x"]
        )
        pad_y = (
            SPACING["footer_pad_y_compact"]
            if compact
            else SPACING["footer_pad_y"]
        )
        self.footer_btn_row.grid_configure(padx=pad_x, pady=pad_y)

        listen_btn, copy_btn, export_btn, clear_btn = self._footer_buttons
        for btn in self._footer_buttons:
            btn.grid_forget()
            btn.configure(height=btn_h)

        listen_btn.configure(width=btn_w_primary)
        if not hamburger_layout:
            copy_btn.configure(width=btn_w_primary if not compact else btn_w_secondary)
            export_btn.configure(width=btn_w_secondary)
            clear_btn.configure(width=btn_w_secondary)

        if hasattr(self, "left_controls_frame") and self.left_controls_frame is not None:
            for child in self.left_controls_frame.winfo_children():
                child.grid_forget()
            if hamburger_layout:
                self.left_controls_frame.grid_remove()
            else:
                self.left_controls_frame.grid(row=0, column=0, sticky="w")
        if hasattr(self, "right_actions_frame") and self.right_actions_frame is not None:
            for child in self.right_actions_frame.winfo_children():
                child.grid_forget()

        if hamburger_layout:
            self.footer_btn_row.grid_columnconfigure(0, weight=1)
            self.footer_btn_row.grid_columnconfigure(1, weight=0)
            self.footer_btn_row.grid_columnconfigure(2, weight=0)
            if hasattr(self, "right_actions_frame") and self.right_actions_frame is not None:
                self.right_actions_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.right_actions_frame.grid_columnconfigure(0, weight=1)
            clear_btn.grid(row=0, column=0, sticky="ew")
            return

        self.footer_btn_row.grid_columnconfigure(1, weight=1)

        if not compact:
            if not hamburger_layout:
                listen_btn.grid(row=0, column=0, sticky="w")
            copy_btn.grid(row=0, column=0, padx=(0, gap), sticky="e")
            export_btn.grid(row=0, column=1, padx=(0, gap), sticky="e")
            clear_btn.grid(row=0, column=2, sticky="e")
            if hasattr(self, "right_actions_frame") and self.right_actions_frame is not None:
                self.right_actions_frame.grid(row=0, column=2, sticky="e")
            return

        if hasattr(self, "right_actions_frame") and self.right_actions_frame is not None:
            self.right_actions_frame.grid(
                row=0 if hamburger_layout else 1,
                column=0,
                columnspan=3,
                sticky="e",
                pady=(0 if hamburger_layout else 4, 0),
            )
        if not hamburger_layout:
            listen_btn.grid(row=0, column=0, sticky="w")
        copy_btn.grid(row=0, column=0, padx=(0, gap), sticky="e")
        export_btn.grid(row=0, column=1, padx=(0, gap), sticky="e")
        clear_btn.grid(row=0, column=2, sticky="e")

    def create_footer(self):
        """Bottom toolbar with primary session controls."""
        self.footer_frame = ctk.CTkFrame(
            master=self,
            fg_color=COLORS["panel_bg"],
            corner_radius=0,
            border_width=1,
            border_color=COLORS["border"],
        )
        self.footer_frame.grid(row=ROOT_ROW_FOOTER, column=0, sticky="ew")
        self.footer_frame.grid_columnconfigure(0, weight=1)

        self.footer_btn_row = ctk.CTkFrame(self.footer_frame, fg_color="transparent")
        self.footer_btn_row.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=SPACING["window_pad_x"],
            pady=SPACING["footer_pad_y"],
        )
        self.footer_btn_row.grid_columnconfigure(1, weight=1)
        self.footer_btn_row2 = None

        self.left_controls_frame = ctk.CTkFrame(self.footer_btn_row, fg_color="transparent")
        self.left_controls_frame.grid(row=0, column=0, sticky="w")

        self.right_actions_frame = ctk.CTkFrame(self.footer_btn_row, fg_color="transparent")
        self.right_actions_frame.grid(row=0, column=2, sticky="e")

        self.listen_button = ctk.CTkButton(
            master=self.left_controls_frame,
            text="Start Listening",
            command=self.toggle_listening,
            **self._primary_button_config(width=FOOTER_BTN_WIDTH),
        )

        self.footer_stop_button = None

        self.copy_translation_btn = ctk.CTkButton(
            master=self.right_actions_frame,
            text="Copy Translation",
            command=self.copy_translation_to_clipboard,
            **self._secondary_button_config(width=FOOTER_BTN_WIDTH),
        )

        self.export_btn = ctk.CTkButton(
            master=self.right_actions_frame,
            text="Export",
            command=self.export_transcript_placeholder,
            **self._secondary_button_config(width=FOOTER_BTN_WIDTH_SECONDARY),
        )

        self.clear_btn = ctk.CTkButton(
            master=self.right_actions_frame,
            text="Clear",
            command=self.clear_text,
            **self._secondary_button_config(width=FOOTER_BTN_WIDTH_SECONDARY),
        )

        self._footer_buttons = [
            self.listen_button,
            self.copy_translation_btn,
            self.export_btn,
            self.clear_btn,
        ]
        self._apply_footer_layout(self.winfo_width() if self.winfo_width() > 1 else 1180)

    def _create_toggle_button(self, master, text, width):
        """Create a compact hide/show toggle button for the Initial verse panel."""
        return ctk.CTkButton(
            master=master,
            text=text,
            command=self.toggle_initial_verse,
            **self._secondary_button_config(width=width, height=SMALL_BUTTON_HEIGHT),
        )

    def _place_toggle_button(self, parent_row, text, width):
        """Show the correct toggle button for the current transcript visibility."""
        if text == "Hide":
            self.show_initial_button.grid_remove()
            self.hide_initial_button.configure(text=text, width=width)
            self.hide_initial_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        else:
            self.hide_initial_button.grid_remove()
            self.show_initial_button.configure(text=text, width=width)
            self.show_initial_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def check_scrollbar_visibility(self, text_widget, scrollbar):
        """Show the right scrollbar only when text content overflows the visible area."""
        try:
            yview = text_widget.yview()
            if yview[0] == 0.0 and yview[1] == 1.0:
                scrollbar.pack_forget()
            else:
                scrollbar.pack(side="right", fill="y")
        except Exception:
            pass

    def _bind_scroll_autohide(self, text_widget, scrollbar):
        """Link scrollbar to text widget and refresh visibility on scroll/resize."""

        def yscroll_callback(first, last):
            scrollbar.set(first, last)
            self.check_scrollbar_visibility(text_widget, scrollbar)

        text_widget.configure(yscrollcommand=yscroll_callback)

        def on_configure(_event=None):
            self.check_scrollbar_visibility(text_widget, scrollbar)

        text_widget.bind("<Configure>", on_configure, add="+")
        text_widget._scroll_refresh = lambda: self.check_scrollbar_visibility(
            text_widget, scrollbar
        )

    def _create_styled_text(
        self,
        master,
        font_size,
        body_color,
        placeholder_text=None,
        placeholder_font=None,
        text_pad_y=12,
    ):
        """Create a read-only tk.Text widget with a right-side auto-hiding CTkScrollbar."""
        text_frame = ctk.CTkFrame(
            master=master,
            fg_color=COLORS["card_bg_soft"],
            corner_radius=RADII["button"],
            border_width=1,
            border_color=COLORS["border_soft"],
        )

        scrollbar = ctk.CTkScrollbar(
            master=text_frame,
            orientation="vertical",
            button_color=COLORS["border"],
            button_hover_color=COLORS["border_soft"],
            fg_color=COLORS["card_bg_soft"],
        )

        text_widget = tk.Text(
            master=text_frame,
            bg=COLORS["card_bg_soft"],
            fg=body_color,
            font=self._ui_font(font_size),
            insertbackground=COLORS["text_primary"],
            relief="flat",
            borderwidth=0,
            wrap="word",
            highlightthickness=0,
            padx=12,
            pady=text_pad_y,
            state="disabled",
        )
        text_widget.pack(side="left", fill="both", expand=True)

        scrollbar.configure(command=text_widget.yview)
        self._bind_scroll_autohide(text_widget, scrollbar)

        text_widget.tag_configure("body", foreground=body_color)
        text_widget.tag_configure("interim", foreground=COLORS["text_muted"])
        for tag_name, color in SPEAKER_COLORS.items():
            text_widget.tag_configure(tag_name, foreground=color)

        text_widget._scrollbar = scrollbar

        if placeholder_text:
            pfont = placeholder_font or FONTS["placeholder"]
            text_widget.tag_configure(
                "placeholder",
                foreground=COLORS["text_disabled"],
                font=pfont,
            )
            self._setup_text_placeholder(text_widget, placeholder_text)

        return text_frame, text_widget

    def _setup_text_placeholder(self, text_widget, placeholder_text):
        """Attach and show empty-state placeholder text."""
        text_widget._placeholder_text = placeholder_text
        text_widget._placeholder_active = False
        self._show_text_placeholder(text_widget)

    def _show_text_placeholder(self, text_widget):
        """Display placeholder copy when a text panel is empty."""
        if text_widget is None:
            return
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", text_widget._placeholder_text, "placeholder")
        text_widget.configure(state="disabled")
        text_widget._placeholder_active = True
        if hasattr(text_widget, "_scrollbar"):
            self.check_scrollbar_visibility(text_widget, text_widget._scrollbar)

    def _clear_text_placeholder(self, text_widget):
        """Remove placeholder text before real content is written."""
        if text_widget is None or not getattr(text_widget, "_placeholder_active", False):
            return
        text_widget.configure(state="normal")
        current = text_widget.get("1.0", "end-1c")
        if current.strip() == text_widget._placeholder_text:
            text_widget.delete("1.0", "end")
        text_widget._placeholder_active = False

    def _is_placeholder_active(self, text_widget):
        """Return True when the panel is showing empty-state placeholder text."""
        if text_widget is None:
            return False
        if getattr(text_widget, "_placeholder_active", False):
            return True
        content = text_widget.get("1.0", "end-1c").strip()
        placeholder = getattr(text_widget, "_placeholder_text", "")
        return bool(placeholder) and content == placeholder

    def _get_text_content(self, text_widget):
        """Return visible text, ignoring placeholder copy."""
        if text_widget is None or self._is_placeholder_active(text_widget):
            return ""
        return text_widget.get("1.0", "end-1c").strip()

    def _create_verse_section(
        self,
        master,
        title,
        font_size,
        body_color,
        attr_name,
        is_initial,
        grid_row=0,
        placeholder_text=None,
        placeholder_font=None,
    ):
        """Build a labeled card with a styled tk.Text box."""
        outer = ctk.CTkFrame(master=master, **self._card_config())
        if is_initial:
            outer.grid(
                row=grid_row,
                column=0,
                sticky="nsew",
                pady=(0, SPACING["section_gap_compact"]),
            )
            title_pady = (12, 4)
            body_pady = (0, SPACING["card_pad_compact"])
            text_pad_y = 8
        else:
            outer.grid(row=grid_row, column=0, sticky="nsew", pady=(0, 0))
            title_pady = (14, 6)
            body_pady = (0, SPACING["card_pad_compact"])
            text_pad_y = 8
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(master=outer, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=SPACING["card_pad"], pady=title_pady)
        title_row.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            master=title_row,
            text=title,
            font=self._ui_font(FONTS["section_title"][1], "bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        title_label.grid(row=0, column=0, sticky="w")

        if is_initial:
            self.initial_title_row = title_row
            self.hide_initial_button = self._create_toggle_button(title_row, "Hide", 64)
            self.hide_initial_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        else:
            self.translated_title_row = title_row
            self.translated_title_label = title_label
            self.show_initial_button = self._create_toggle_button(
                title_row, "Show Transcript", 128
            )
            self.show_initial_button.grid_remove()

        text_frame, text_widget = self._create_styled_text(
            outer,
            font_size,
            body_color,
            placeholder_text=placeholder_text,
            placeholder_font=placeholder_font,
            text_pad_y=text_pad_y,
        )
        text_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=SPACING["card_pad"],
            pady=body_pady,
        )

        if not is_initial:
            outer.bind("<Double-Button-1>", lambda _e: self._restore_if_hidden())
            title_row.bind("<Double-Button-1>", lambda _e: self._restore_if_hidden())
            text_widget.bind("<Double-Button-1>", lambda _e: self._restore_if_hidden())

        setattr(self, attr_name, text_widget)
        return outer

    def _restore_if_hidden(self):
        """Restore Initial verse on double-click when in full-screen translated mode."""
        if not self._initial_verse_visible:
            self.toggle_initial_verse()

    def toggle_initial_verse(self):
        """Hide or restore the Live Transcript panel."""
        try:
            if self._initial_verse_visible:
                self.initial_verse_frame.grid_remove()
                self.left_column.grid_rowconfigure(0, weight=0)
                self.left_column.grid_rowconfigure(1, weight=1)
                self.translated_title_label.grid_remove()
                self._place_toggle_button(self.translated_title_row, "Show Transcript", 128)
                self._initial_verse_visible = False
            else:
                self.initial_verse_frame.grid()
                self._apply_left_column_panel_weights()
                self.translated_title_label.grid()
                self._place_toggle_button(self.initial_title_row, "Hide", 64)
                self._initial_verse_visible = True
        except Exception as exc:
            print(f"Error toggling initial verse: {exc}")

    def _apply_left_column_panel_weights(
        self,
        transcript_weight=TRANSCRIPT_PANEL_WEIGHT,
        translation_weight=TRANSLATION_PANEL_WEIGHT,
    ):
        """Set vertical space split between Live Transcript and Translation."""
        if self.left_column is None:
            return
        self.left_column.grid_rowconfigure(0, weight=transcript_weight)
        self.left_column.grid_rowconfigure(1, weight=translation_weight)

    def _set_initial_pane_ratio(self):
        """Set initial row weights for transcript vs translation."""
        if not self._initial_verse_visible or self.left_column is None:
            return
        try:
            self._apply_left_column_panel_weights()
            if not self._pane_initialized:
                self._pane_initialized = True
        except Exception as exc:
            print(f"Error setting initial pane sizes: {exc}")

    def _should_accept_transcript_commit(self) -> bool:
        """Allow transcript commits while listening or during graceful finalize."""
        return bool(self.is_listening) or bool(getattr(self, "_is_finalizing", False))

    def _reset_interim_tail_state(self):
        """Reset in-memory interim tail tracking for a new listen session."""
        self._latest_interim_text = ""
        self._latest_interim_speaker = 1
        self._latest_interim_committed = False
        self._last_final_text = ""

    def _normalize_compare(self, text: str) -> str:
        """Normalize text for interim/final containment checks only."""
        if self._is_japanese_manual_mode() and JAPANESE_CHAR_DEDUP_ENABLED:
            return self._compact_japanese_for_compare(text)
        normalized = (text or "").strip().lower()
        normalized = re.sub(r"[^\w\s]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _is_japanese_char(self, ch: str) -> bool:
        return bool(re.match(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", ch or ""))

    def _normalize_japanese_display_line(self, text: str) -> str:
        segment = (text or "").replace("\u3000", " ")
        segment = re.sub(r"[ \t]+", " ", segment).strip()
        chars = list(segment)
        compact = []
        for idx, ch in enumerate(chars):
            if ch == " " and idx > 0 and idx < len(chars) - 1:
                prev_ch = chars[idx - 1]
                next_ch = chars[idx + 1]
                if self._is_japanese_char(prev_ch) and self._is_japanese_char(next_ch):
                    continue
            compact.append(ch)
        segment = "".join(compact)
        segment = re.sub(r"\s+([。、！？])", r"\1", segment)
        segment = re.sub(
            r"([。、！？])\s+(?=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff])",
            r"\1",
            segment,
        )
        segment = re.sub(r"([（「『［【｛〈《])\s+", r"\1", segment)
        return segment.strip()

    def _normalize_japanese_display_text(self, text: str) -> str:
        if not self._is_cjk_pipeline_active():
            return (text or "").strip()
        return normalize_cjk_spacing(text)

    def _compact_japanese_for_compare(self, text: str) -> str:
        if not self._is_cjk_pipeline_active():
            return (text or "").strip().lower()
        return compact_cjk_for_compare(text, self._cjk_language_code())

    def _trim_segment_to_compact_prefix(self, segment: str, target_compact_len: int) -> str:
        segment = (segment or "").strip()
        if not segment or target_compact_len <= 0:
            return ""
        compact = self._compact_japanese_for_compare(segment)
        if len(compact) <= target_compact_len:
            return segment
        target_compact = compact[:target_compact_len]
        for end in range(1, len(segment) + 1):
            prefix = segment[:end]
            if self._compact_japanese_for_compare(prefix) == target_compact:
                return prefix.strip()
        return segment

    _JAPANESE_NATURAL_SHORT_REPEATS = frozenset(
        {"はいはい", "うんうん", "そうそう", "まあまあ"}
    )
    _JAPANESE_SHORT_REPEAT_MIN_LEN = 7

    _JAPANESE_PARTIAL_OVERLAP_MIN_LEN = 6
    _JAPANESE_TAIL_STITCH_MAX_COMPACT_LEN = 8
    _JAPANESE_TAIL_INCOMPLETE_ENDINGS = (
        "お願いし",
        "いたし",
        "してい",
        "し",
        "を",
        "が",
        "に",
        "で",
        "と",
        "の",
        "へ",
    )
    _JAPANESE_PARTICLE_ENDINGS = ("を", "が", "に", "で", "と", "の", "へ")
    _JAPANESE_CONTINUATION_PREFIXES = (
        "テスト",
        "勉強",
        "確認",
        "お願いします",
        "しています",
    )
    _JAPANESE_PREFIX_REPEAT_MIN_LEN = 8

    def _cjk_language_code(self) -> str:
        return str(FORCE_DEEPGRAM_LANGUAGE or getattr(self, "_listen_language", None) or "ja")

    def _is_cjk_pipeline_active(self) -> bool:
        return bool(CJK_CLEANUP_ENABLED) and is_cjk_mode(self._cjk_language_code())

    def _is_japanese_pipeline_active(self) -> bool:
        return self._is_cjk_pipeline_active() and bool(JAPANESE_MODE_ENABLED)

    def _cjk_log(self, message: str, data: dict):
        _session_ndjson_log(
            location="main_window.py:_cjk_log",
            message=message,
            data=data,
        )

    def _cjk_log_fn(self, message: str, data: dict):
        preview_keys = (
            "original_preview",
            "cleaned_preview",
            "repeated_unit_preview",
            "suffix_preview",
            "prefix_preview",
            "before_preview",
            "after_preview",
        )
        safe_data = dict(data)
        for key in preview_keys:
            if key in safe_data and isinstance(safe_data[key], str):
                safe_data[key] = _diag_text_preview(safe_data[key], 160)
        self._cjk_log(message, safe_data)

    def _apply_cjk_post_merge_cleanup(self, text: str, merge_type: str) -> str:
        if not CJK_POST_MERGE_CLEANUP_ENABLED or not self._is_cjk_pipeline_active():
            return (text or "").strip()
        before = (text or "").strip()
        after = self._apply_japanese_final_cleanup(before)
        if after != before:
            self._cjk_log(
                "[CJK] post merge cleanup applied",
                {
                    "before_preview": _diag_text_preview(before, 160),
                    "after_preview": _diag_text_preview(after, 160),
                    "merge_type": merge_type,
                    "language_code": self._cjk_language_code(),
                    "reason": "post_merge_cjk_cleanup",
                },
            )
        return after

    def _apply_japanese_final_cleanup(self, text: str) -> str:
        segment = (text or "").strip()
        if not segment or not self._is_cjk_pipeline_active():
            return segment
        lang = self._cjk_language_code()
        log_fn = self._cjk_log_fn
        if JAPANESE_TEXT_NORMALIZATION_ENABLED:
            segment = normalize_cjk_spacing(segment)
        if JAPANESE_INTERNAL_REPEAT_REMOVAL_ENABLED:
            segment = self._remove_internal_japanese_repeat(segment)
            segment = self._remove_short_internal_japanese_repeat(segment)
        if CJK_LOCAL_REPEAT_FIX_ENABLED:
            segment = remove_cjk_local_repeats(segment, lang, log_fn)
        if CJK_PREFIX_OVERLAP_FIX_ENABLED:
            segment = remove_cjk_prefix_overlap(segment, lang, log_fn)
        elif JAPANESE_PREFIX_REPEAT_REMOVAL_ENABLED:
            segment = self._remove_japanese_prefix_repeat(segment)
        if JAPANESE_PARTIAL_OVERLAP_REMOVAL_ENABLED:
            segment = self._remove_japanese_partial_overlap_repeat(segment)
        if CJK_BOUNDARY_PUNCTUATION_FIX_ENABLED:
            segment = fix_cjk_boundary_punctuation_with_log(segment, lang, log_fn)
        if JAPANESE_KNOWN_TERM_CORRECTION_ENABLED and JAPANESE_GUARDED_KNOWN_CORRECTIONS_ENABLED:
            segment = self._apply_japanese_known_term_corrections(segment)
        return segment

    def _apply_japanese_known_term_corrections(self, text: str) -> str:
        if not self._is_japanese_pipeline_active():
            return (text or "").strip()
        segment = (text or "").strip()
        if not segment:
            return segment
        for wrong, right in sorted(
            JAPANESE_KNOWN_TERM_CORRECTIONS.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if wrong not in segment:
                continue
            corrected = segment.replace(wrong, right)
            if corrected != segment:
                _session_ndjson_log(
                    location="main_window.py:_apply_japanese_known_term_corrections",
                    message="[JAPANESE] known term corrected",
                    data={
                        "original_preview": _diag_text_preview(segment, 160),
                        "corrected_preview": _diag_text_preview(corrected, 160),
                        "correction_key": wrong,
                        "correction_value": right,
                    },
                )
                segment = corrected
        return segment

    def _log_prefix_repeat_removed(
        self,
        original: str,
        cleaned: str,
        prefix: str,
        suffix: str,
        reason: str,
    ):
        _session_ndjson_log(
            location="main_window.py:_remove_japanese_prefix_repeat",
            message="[JAPANESE] prefix repeat removed",
            data={
                "original_preview": _diag_text_preview(original, 160),
                "cleaned_preview": _diag_text_preview(cleaned, 160),
                "prefix_preview": _diag_text_preview(prefix, 80),
                "suffix_preview": _diag_text_preview(suffix, 80),
                "reason": reason,
            },
        )

    def _remove_japanese_prefix_repeat_once(self, text: str) -> str:
        segment = (text or "").strip()
        compact = self._compact_japanese_for_compare(segment)
        min_prefix = self._JAPANESE_PREFIX_REPEAT_MIN_LEN
        if len(compact) < min_prefix * 2:
            return segment
        for prefix_len in range(len(compact) // 2, min_prefix - 1, -1):
            prefix = compact[:prefix_len]
            if prefix in self._JAPANESE_NATURAL_SHORT_REPEATS:
                continue
            rest = compact[prefix_len:]
            if not rest.startswith(prefix):
                continue
            suffix = rest[prefix_len:]
            if not suffix:
                continue
            target_compact = prefix + suffix
            cleaned = self._segment_from_compact_target(segment, target_compact)
            if cleaned != segment:
                self._log_prefix_repeat_removed(
                    segment,
                    cleaned,
                    prefix,
                    suffix,
                    "prefix_plus_prefix_with_suffix",
                )
                return cleaned
        return segment

    def _remove_japanese_prefix_repeat(self, text: str) -> str:
        if not JAPANESE_PREFIX_REPEAT_REMOVAL_ENABLED or not self._is_japanese_pipeline_active():
            return (text or "").strip()
        segment = (text or "").strip()
        for _ in range(3):
            cleaned = self._remove_japanese_prefix_repeat_once(segment)
            if cleaned == segment:
                break
            segment = cleaned
        return segment

    def _segment_from_compact_target(self, segment: str, target_compact: str) -> str:
        segment = (segment or "").strip()
        target_compact = (target_compact or "").strip()
        if not segment or not target_compact:
            return segment
        for end in range(1, len(segment) + 1):
            if self._compact_japanese_for_compare(segment[:end]) == target_compact:
                return segment[:end].strip()
        return segment

    def _log_partial_overlap_removed(
        self, original: str, cleaned: str, overlap_unit: str, reason: str
    ):
        _session_ndjson_log(
            location="main_window.py:_remove_japanese_partial_overlap_repeat",
            message="[JAPANESE] partial overlap removed",
            data={
                "original_preview": _diag_text_preview(original, 160),
                "cleaned_preview": _diag_text_preview(cleaned, 160),
                "overlap_unit_preview": _diag_text_preview(overlap_unit, 80),
                "reason": reason,
            },
        )

    def _remove_japanese_partial_overlap_once(self, text: str) -> str:
        segment = (text or "").strip()
        compact = self._compact_japanese_for_compare(segment)
        min_unit = self._JAPANESE_PARTIAL_OVERLAP_MIN_LEN
        if len(compact) < min_unit * 2:
            return segment
        best_cleaned = None
        best_unit = None
        best_reason = None
        for unit_len in range(len(compact) // 2, min_unit - 1, -1):
            for first_idx in range(0, len(compact) - unit_len):
                unit = compact[first_idx : first_idx + unit_len]
                if unit in self._JAPANESE_NATURAL_SHORT_REPEATS:
                    continue
                second_idx = first_idx + unit_len
                if second_idx + unit_len > len(compact):
                    continue
                if compact[second_idx : second_idx + unit_len] != unit:
                    continue
                suffix = compact[second_idx + unit_len :]
                if not suffix:
                    continue
                target_compact = compact[: first_idx + unit_len] + suffix
                cleaned = self._segment_from_compact_target(segment, target_compact)
                if cleaned != segment and (
                    best_cleaned is None or len(cleaned) < len(best_cleaned)
                ):
                    best_cleaned = cleaned
                    best_unit = unit
                    best_reason = "adjacent_repeat_with_extra_suffix"
        if best_cleaned:
            self._log_partial_overlap_removed(
                segment, best_cleaned, best_unit or "", best_reason or "partial_overlap"
            )
            return best_cleaned
        return segment

    def _remove_japanese_partial_overlap_repeat(self, text: str) -> str:
        if (
            not JAPANESE_PARTIAL_OVERLAP_REMOVAL_ENABLED
            or not self._is_japanese_pipeline_active()
        ):
            return (text or "").strip()
        segment = (text or "").strip()
        for _ in range(3):
            cleaned = self._remove_japanese_partial_overlap_once(segment)
            if cleaned == segment:
                break
            segment = cleaned
        return segment

    def _normalize_japanese_tail_fragment(self, text: str) -> str:
        segment = (text or "").strip()
        if not segment:
            return segment
        compact = self._compact_japanese_for_compare(segment)
        if compact == "ますます" or compact.endswith("ますます"):
            return re.sub(r"ますます([。、！？]?)$", r"ます\1", segment)
        return segment

    def _is_japanese_standalone_no_merge(self, text: str) -> bool:
        if not text:
            return False
        stripped = (text or "").strip()
        core = stripped.rstrip("。、！？.!?")
        compact = self._compact_japanese_for_compare(stripped)
        for phrase in JAPANESE_STANDALONE_NO_MERGE:
            phrase_compact = self._compact_japanese_for_compare(phrase)
            if compact == phrase_compact or core == phrase or stripped == phrase:
                return True
        return False

    def _is_japanese_standalone_phrase(self, text: str) -> bool:
        return self._is_japanese_standalone_no_merge(text)

    def _previous_blocks_particle_merge(self, previous_text: str) -> tuple[bool, str]:
        prev = (previous_text or "").strip()
        if not prev:
            return True, "empty_previous"
        if self._is_japanese_standalone_no_merge(prev):
            return True, "standalone_greeting"
        if len(self._compact_japanese_for_compare(prev)) < 8:
            return True, "previous_too_short"
        if re.search(r"[。！？.!?]$", prev):
            return True, "strong_punctuation_ending"
        return False, ""

    def _log_particle_merge_blocked(
        self, previous_text: str, current_text: str, reason: str
    ):
        _session_ndjson_log(
            location="main_window.py:_evaluate_japanese_particle_continuation",
            message="[JAPANESE] particle merge blocked",
            data={
                "previous_preview": _diag_text_preview(previous_text, 120),
                "current_preview": _diag_text_preview(current_text, 120),
                "reason": reason,
            },
        )

    def _previous_segment_ends_incomplete_japanese(self, previous_text: str) -> tuple[bool, str]:
        prev = (previous_text or "").strip()
        if not prev:
            return False, ""
        prev_core = prev.rstrip("。、！？")
        for ending in self._JAPANESE_TAIL_INCOMPLETE_ENDINGS:
            if prev_core.endswith(ending) or prev.endswith(ending):
                return True, ending
        return False, ""

    def _log_tail_stitch_skipped(self, previous_text: str, current_text: str, reason: str):
        _session_ndjson_log(
            location="main_window.py:_evaluate_japanese_tail_stitch",
            message="[JAPANESE] tail stitch skipped",
            data={
                "previous_preview": _diag_text_preview(previous_text, 120),
                "current_preview": _diag_text_preview(current_text, 120),
                "reason": reason,
            },
        )

    def _evaluate_japanese_tail_stitch(
        self, previous_text: str | None, current_text: str
    ):
        if (
            not JAPANESE_TAIL_STITCH_ENABLED
            or not self._is_japanese_pipeline_active()
            or not self._is_japanese_manual_mode()
        ):
            return None
        prev = (previous_text or "").strip()
        curr = self._normalize_japanese_tail_fragment(current_text)
        if not prev or not curr:
            return None
        if self._is_japanese_standalone_phrase(curr):
            self._log_tail_stitch_skipped(prev, curr, "standalone_phrase")
            return None
        curr_compact = self._compact_japanese_for_compare(curr)
        if len(curr_compact) > self._JAPANESE_TAIL_STITCH_MAX_COMPACT_LEN:
            self._log_tail_stitch_skipped(prev, curr, "current_too_long")
            return None
        incomplete, ending = self._previous_segment_ends_incomplete_japanese(prev)
        if not incomplete:
            self._log_tail_stitch_skipped(prev, curr, "previous_not_incomplete")
            return None
        merged = self._apply_cjk_post_merge_cleanup(
            f"{prev.rstrip()}{curr.lstrip()}", "tail_stitched_update_previous"
        )
        _session_ndjson_log(
            location="main_window.py:_evaluate_japanese_tail_stitch",
            message="[JAPANESE] tail stitched",
            data={
                "previous_preview": _diag_text_preview(prev, 120),
                "current_preview": _diag_text_preview(curr, 120),
                "merged_preview": _diag_text_preview(merged, 160),
                "reason": f"previous_ends_with_{ending}",
            },
        )
        return {
            "decision": "tail_stitched_update_previous",
            "merged_text": merged,
            "reason": f"previous_ends_with_{ending}",
        }

    def _current_starts_japanese_continuation(self, current_text: str) -> bool:
        body = (current_text or "").strip()
        if not body:
            return False
        for prefix in self._JAPANESE_CONTINUATION_PREFIXES:
            if body.startswith(prefix):
                return True
        return bool(self._is_japanese_char(body[0]))

    def _evaluate_japanese_particle_continuation(
        self, previous_text: str | None, current_text: str
    ):
        if not self._is_japanese_pipeline_active() or not self._is_japanese_manual_mode():
            return None
        prev = (previous_text or "").strip()
        curr = (current_text or "").strip()
        if not prev or not curr:
            return None
        if self._is_japanese_standalone_phrase(curr):
            return None
        blocked, block_reason = self._previous_blocks_particle_merge(prev)
        if blocked:
            if JAPANESE_SAFE_MERGE_GUARD_ENABLED:
                self._log_particle_merge_blocked(prev, curr, block_reason)
            return None
        if not any(prev.endswith(p) for p in self._JAPANESE_PARTICLE_ENDINGS):
            return None
        if not self._current_starts_japanese_continuation(curr):
            return None
        merged = self._apply_cjk_post_merge_cleanup(
            f"{prev}{curr}", "particle_continuation_update_previous"
        )
        _session_ndjson_log(
            location="main_window.py:_evaluate_japanese_particle_continuation",
            message="[JAPANESE] particle continuation merged",
            data={
                "previous_preview": _diag_text_preview(prev, 120),
                "current_preview": _diag_text_preview(curr, 120),
                "merged_preview": _diag_text_preview(merged, 160),
                "reason": "particle_ending_continuation",
            },
        )
        return {
            "decision": "particle_continuation_update_previous",
            "merged_text": merged,
            "reason": "particle_ending_continuation",
        }

    def _log_compound_continuation_skipped(
        self, previous_text: str, current_text: str, reason: str
    ):
        _session_ndjson_log(
            location="main_window.py:_evaluate_japanese_compound_continuation",
            message="[JAPANESE] compound continuation skipped",
            data={
                "previous_preview": _diag_text_preview(previous_text, 120),
                "current_preview": _diag_text_preview(current_text, 120),
                "reason": reason,
            },
        )

    def _evaluate_japanese_compound_continuation(
        self, previous_text: str | None, current_text: str
    ):
        if (
            not JAPANESE_COMPOUND_CONTINUATION_ENABLED
            or not self._is_japanese_pipeline_active()
            or not self._is_japanese_manual_mode()
        ):
            return None
        prev = (previous_text or "").strip()
        curr = (current_text or "").strip()
        if not prev or not curr:
            return None
        if self._is_japanese_standalone_no_merge(prev) or self._is_japanese_standalone_no_merge(
            curr
        ):
            self._log_compound_continuation_skipped(prev, curr, "standalone_phrase")
            return None
        matched_ending = None
        for ending in sorted(JAPANESE_COMPOUND_ENDINGS, key=len, reverse=True):
            if prev.endswith(ending):
                matched_ending = ending
                break
        if not matched_ending:
            self._log_compound_continuation_skipped(prev, curr, "no_compound_ending")
            return None
        matched_start = None
        for start in sorted(JAPANESE_COMPOUND_STARTS, key=len, reverse=True):
            if curr.startswith(start):
                matched_start = start
                break
        if not matched_start:
            self._log_compound_continuation_skipped(prev, curr, "no_compound_start")
            return None
        merged = self._apply_cjk_post_merge_cleanup(
            f"{prev}{curr}", "compound_continuation_update_previous"
        )
        _session_ndjson_log(
            location="main_window.py:_evaluate_japanese_compound_continuation",
            message="[JAPANESE] compound continuation merged",
            data={
                "previous_preview": _diag_text_preview(prev, 120),
                "current_preview": _diag_text_preview(curr, 120),
                "merged_preview": _diag_text_preview(merged, 160),
                "reason": f"{matched_ending}+{matched_start}",
            },
        )
        return {
            "decision": "compound_continuation_update_previous",
            "merged_text": merged,
            "reason": f"compound_{matched_ending}_{matched_start}",
        }

    def _evaluate_japanese_cross_segment_merge(
        self, previous_text: str | None, current_text: str
    ):
        if not self._is_japanese_pipeline_active() or not self._is_japanese_manual_mode():
            return None
        particle = self._evaluate_japanese_particle_continuation(previous_text, current_text)
        if particle:
            return particle
        compound = self._evaluate_japanese_compound_continuation(previous_text, current_text)
        if compound:
            return compound
        return self._evaluate_japanese_tail_stitch(previous_text, current_text)

    def _commit_japanese_update_previous_segment(
        self,
        speaker,
        merged_text: str,
        decision: str,
        reason: str,
        previous_text: str,
        current_text: str,
        store_count_before: int,
        speech_final,
        item,
        is_finalizing: bool,
    ) -> bool:
        store = getattr(self, "transcript_store", None)
        if store is None:
            return False
        updated = store.update_last_segment(speaker, merged_text)
        if not updated:
            return False
        self._on_store_segment_updated(speaker, merged_text)
        store_count_after = self._diag_store_segment_count()
        preview = _diag_text_preview(merged_text)
        self._teams_log_commit_decision(
            decision,
            reason,
            speaker,
            merged_text,
            store_count_before,
            store_count_after,
            speech_final=speech_final,
        )
        self._diag_last_committed_preview = preview
        self.record_latency_commit()
        self.log_latency_transcript_committed(
            text=merged_text,
            is_finalizing=is_finalizing,
            store_segment_count=store_count_after,
        )
        _session_ndjson_log(
            location="main_window.py:_commit_transcript_item_to_store",
            message="[JAPANESE] commit decision",
            data={
                "decision": decision,
                "reason": reason,
                "text_preview": preview,
                "compact_len": len(self._compact_japanese_for_compare(merged_text)),
                "store_segment_count_before": store_count_before,
                "store_segment_count_after": store_count_after,
                "previous_preview": _diag_text_preview(previous_text, 120),
                "current_preview": _diag_text_preview(current_text, 120),
            },
        )
        self._track_committed_segment_meta(item, merged_text)
        self._apply_final_interim_comparison(merged_text)
        return True

    def _log_internal_repeat_removed(self, original: str, cleaned: str, reason: str):
        _session_ndjson_log(
            location="main_window.py:_remove_internal_japanese_repeat",
            message="[JAPANESE] internal repeat removed",
            data={
                "original_preview": _diag_text_preview(original, 160),
                "cleaned_preview": _diag_text_preview(cleaned, 160),
                "reason": reason,
            },
        )

    def _log_short_internal_repeat_removed(
        self, original: str, cleaned: str, repeat_unit: str, reason: str
    ):
        _session_ndjson_log(
            location="main_window.py:_remove_short_internal_japanese_repeat",
            message="[JAPANESE] short internal repeat removed",
            data={
                "original_preview": _diag_text_preview(original, 160),
                "cleaned_preview": _diag_text_preview(cleaned, 160),
                "repeat_unit_preview": _diag_text_preview(repeat_unit, 80),
                "reason": reason,
            },
        )

    def _remove_short_internal_japanese_repeat(self, text: str) -> str:
        if (
            not JAPANESE_INTERNAL_REPEAT_REMOVAL_ENABLED
            or not self._is_japanese_pipeline_active()
        ):
            return (text or "").strip()
        segment = (text or "").strip()
        compact = self._compact_japanese_for_compare(segment)
        min_unit = self._JAPANESE_SHORT_REPEAT_MIN_LEN
        if len(compact) < min_unit * 2:
            return segment

        half = len(compact) // 2
        if len(compact) % 2 == 0 and half >= 5:
            unit = compact[:half]
            if (
                unit not in self._JAPANESE_NATURAL_SHORT_REPEATS
                and compact[:half] == compact[half:]
            ):
                cleaned = self._trim_segment_to_compact_prefix(segment, half)
                if cleaned != segment:
                    self._log_short_internal_repeat_removed(
                        segment, cleaned, unit, "exact_adjacent_double"
                    )
                    return cleaned

        for unit_len in range(len(compact) // 2, min_unit - 1, -1):
            unit = compact[-unit_len:]
            if unit in self._JAPANESE_NATURAL_SHORT_REPEATS:
                continue
            if (
                len(compact) >= unit_len * 2
                and compact[-unit_len * 2 : -unit_len] == unit
                and compact.endswith(unit)
            ):
                target_len = len(compact) - unit_len
                cleaned = self._trim_segment_to_compact_prefix(segment, target_len)
                if cleaned != segment:
                    self._log_short_internal_repeat_removed(
                        segment, cleaned, unit, "adjacent_suffix_repeat"
                    )
                    return cleaned
        return segment

    def _remove_internal_japanese_repeat(self, text: str) -> str:
        if (
            not JAPANESE_INTERNAL_REPEAT_REMOVAL_ENABLED
            or not self._is_japanese_pipeline_active()
        ):
            return (text or "").strip()
        segment = (text or "").strip()
        compact = self._compact_japanese_for_compare(segment)
        if len(compact) < 12:
            return segment
        half = len(compact) // 2
        if len(compact) % 2 == 0 and half >= 12 and compact[:half] == compact[half:]:
            cleaned = self._trim_segment_to_compact_prefix(segment, half)
            if cleaned != segment:
                self._log_internal_repeat_removed(
                    segment, cleaned, "exact_double_repeat_compact"
                )
            return cleaned
        for unit_len in range(len(compact) // 2, 11, -1):
            unit = compact[-unit_len:]
            first_idx = compact.find(unit)
            if first_idx < 0:
                continue
            if compact.endswith(unit) and first_idx + unit_len <= len(compact) - unit_len:
                target_len = first_idx + unit_len
                cleaned = self._trim_segment_to_compact_prefix(segment, target_len)
                if cleaned != segment:
                    self._log_internal_repeat_removed(
                        segment, cleaned, "suffix_repeat_unit"
                    )
                    return cleaned
        return segment

    def _japanese_recent_compact_segments(self, limit: int = 5):
        store = getattr(self, "transcript_store", None)
        if store is None:
            return []
        return [
            self._compact_japanese_for_compare(segment.text or "")
            for segment in store.get_all()[-limit:]
            if (segment.text or "").strip()
        ]

    def _evaluate_japanese_commit_dedup(self, text: str, previous_text: str | None):
        curr_compact = self._compact_japanese_for_compare(text or "")
        if not curr_compact:
            return None, None
        for prev_compact in self._japanese_recent_compact_segments(5):
            if not prev_compact:
                continue
            if curr_compact == prev_compact:
                return "skip_duplicate", "japanese_compact_duplicate_recent"
            if curr_compact in prev_compact:
                return "skip_duplicate", "japanese_compact_already_committed"
        prev_compact = self._compact_japanese_for_compare(previous_text or "")
        if prev_compact and prev_compact in curr_compact and curr_compact != prev_compact:
            return "append_missing_suffix", "japanese_compact_has_missing_suffix"
        return None, None

    def _session_log(self, message: str, data=None):
        _session_ndjson_log(
            location="main_window.py:_session_log",
            message=message,
            data=data or {},
        )

    def _resolve_japanese_ui_commit_reason(self, item) -> str:
        reason = str(
            item.get("stabilizer_reason")
            or item.get("commit_reason")
            or item.get("assembler_reason")
            or ""
        ).strip()
        if item.get("_jp_continuity_assembler") and not reason:
            return "japanese_continuity_assembler_unknown_reason"
        if (
            item.get("_jp_continuity_assembler")
            and reason
            and not reason.startswith("japanese_continuity_assembler_")
            and reason not in {
                "stop_flush_incomplete_tail",
                "assembler_exception_direct_commit_fallback",
            }
        ):
            reason = f"japanese_continuity_assembler_{reason}"
        return reason

    def _log_session_transcript_copied(self, clean_text: str):
        """Write [SESSION] transcript copied after successful clipboard copy."""
        self._session_log(
            "[SESSION] transcript copied",
            {
                "clean_word_count": len(clean_text.split()) if clean_text else 0,
                "clean_text_len": len(clean_text) if clean_text else 0,
                "ending_preview": clean_text[-240:] if clean_text else "",
            },
        )

    def _teams_elapsed_sec(self):
        if hasattr(self, "_latency_elapsed_sec"):
            try:
                return self._latency_elapsed_sec()
            except Exception:
                pass
        return None

    def _teams_log_commit_decision(
        self,
        decision,
        reason,
        speaker,
        text,
        store_count_before,
        store_count_after=None,
        speech_final=None,
    ):
        if not DEBUG_TEAMS_DIAGNOSTICS:
            return
        _teams_diag_ndjson_log(
            location="main_window.py:_teams_log_commit_decision",
            message="[TEAMS_DIAG] commit decision",
            data={
                "elapsed_sec": self._teams_elapsed_sec(),
                "speaker_label": speaker,
                "text_len": len(text) if text else 0,
                "text_preview": _diag_text_preview(text, 160),
                "decision": decision,
                "reason": reason,
                "store_segment_count_before": store_count_before,
                "store_segment_count_after": store_count_after,
            },
        )
        if decision in ("commit_new", "update_previous", "merge_with_previous", "commit_interim_tail"):
            teams_log_quality_signals(
                location="main_window.py:_teams_log_commit_decision",
                elapsed_sec=self._teams_elapsed_sec(),
                speaker_label=speaker,
                text=text,
                speech_final=speech_final,
            )

    def _teams_log_source_energy(self, speaker_meta):
        if not DEBUG_TEAMS_DIAGNOSTICS:
            return
        now = time.monotonic()
        last = getattr(self, "_teams_last_source_energy_log_monotonic", None)
        if last is not None and (now - last) < 1.0:
            return
        self._teams_last_source_energy_log_monotonic = now
        chosen = speaker_meta.get("chosen_source") or speaker_meta.get("speaker_label") or "none"
        _teams_diag_ndjson_log(
            location="main_window.py:_teams_log_source_energy",
            message="[TEAMS_DIAG] source energy",
            data={
                "elapsed_sec": self._teams_elapsed_sec(),
                "system_rms": speaker_meta.get("sys_rms"),
                "mic_rms": speaker_meta.get("mic_rms"),
                "system_noise_floor": speaker_meta.get("system_noise_floor"),
                "mic_noise_floor": speaker_meta.get("mic_noise_floor"),
                "system_threshold": speaker_meta.get("system_threshold"),
                "mic_threshold": speaker_meta.get("mic_threshold"),
                "mic_to_system_ratio": speaker_meta.get("mic_to_system_ratio"),
                "system_active": speaker_meta.get("system_active"),
                "mic_active": speaker_meta.get("mic_active"),
                "overlap_candidate": speaker_meta.get("overlap_candidate"),
                "overlap_confirm_count": speaker_meta.get("overlap_confirm_count"),
                "overlap_detected": speaker_meta.get("overlap_detected"),
                "chosen_source": chosen,
                "previous_source": speaker_meta.get("previous_source"),
                "speaker_label": chosen,
                "decision_reason": speaker_meta.get("decision_reason"),
            },
        )

    def _teams_log_source_gate_summary(self):
        if not DEBUG_TEAMS_DIAGNOSTICS:
            return
        summary = getattr(self, "_teams_source_gate_summary", None) or {}
        _teams_diag_ndjson_log(
            location="main_window.py:_teams_log_source_gate_summary",
            message="[TEAMS_DIAG] source gate summary",
            data={
                "system_count": int(summary.get("system_count", 0)),
                "mic_count": int(summary.get("mic_count", 0)),
                "mixed_count": int(summary.get("mixed_count", 0)),
                "none_count": int(summary.get("none_count", 0)),
                "false_overlap_prevented_count": int(
                    summary.get("false_overlap_prevented_count", 0)
                ),
                "total_source_checks": int(summary.get("total_source_checks", 0)),
            },
        )

    def _interim_log(self, message: str, data=None):
        now = time.perf_counter()
        if (now - getattr(self, "_last_interim_log_at", 0.0)) * 1000 < INTERIM_LOG_THROTTLE_MS:
            if not DEBUG_DIAGNOSTICS:
                return
        self._last_interim_log_at = now
        _interim_ndjson_log(
            location="main_window.py:_interim_log",
            message=message,
            data=data or {},
        )

    def on_interim_transcript(self, speaker, text, metadata=None):
        """Deepgram worker callback — coalesce interim UI updates on main thread."""
        self._pending_interim = (speaker, text, metadata)
        from alpha.utils.ui_thread_guard import is_ui_main_thread

        if is_ui_main_thread():
            self._schedule_interim_flush_main_thread()
            return

        if getattr(self, "_interim_flush_posted", False):
            return
        self._interim_flush_posted = True
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log
            from alpha.utils.ui_event_bus import get_ui_event_bus

            get_ui_event_bus().post("interim_flush_requested", {})
            jp_accuracy_log("DEEPGRAM_INTERIM_FLUSH_REROUTED_TO_UI_EVENT_BUS")
            jp_accuracy_log("INTERIM_UI_FLUSH_EVENT_POSTED")
            jp_accuracy_log("BACKGROUND_TK_CALL_SOURCE_REMOVED", source="on_interim_transcript")
        except Exception:
            pass

    def _schedule_interim_flush_main_thread(self):
        if self._interim_after_id is not None:
            return
        self._interim_after_id = self.after(
            INTERIM_UI_THROTTLE_MS, self._flush_pending_interim_ui
        )

    def _flush_pending_interim_ui(self):
        self._interim_after_id = None
        self._interim_flush_posted = False
        pending = self._pending_interim
        if not pending:
            return
        speaker, text, metadata = pending
        self._handle_interim_transcript_ui(speaker, text, metadata)

    def _handle_interim_transcript_ui(self, speaker, text, metadata=None):
        if getattr(self, "_latest_interim_committed", False):
            return
        interim_text = (text or "").strip()
        if not interim_text:
            return
        speaker_num = speaker
        if speaker_num is not None and str(speaker_num).isdigit():
            speaker_num = int(speaker_num)
        self._latest_interim_text = interim_text
        self._latest_interim_speaker = speaker_num or 1
        self._interim_log(
            "[INTERIM] received",
            {
                "speaker": self._latest_interim_speaker,
                "text_len": len(interim_text),
                "text_preview": interim_text[:120],
                "speech_final": (metadata or {}).get("speech_final"),
            },
        )
        self._last_interim_ui_at = time.perf_counter()
        self._update_interim_line_only()

    def _clear_interim_tail(self):
        self._latest_interim_text = ""
        self._latest_interim_speaker = 1
        self._remove_interim_line_from_display()

    def _append_pending_interim_to_display(self):
        box = self.initial_verse_box
        interim_text = (getattr(self, "_latest_interim_text", "") or "").strip()
        if box is None or not interim_text:
            return
        speaker = getattr(self, "_latest_interim_speaker", 1) or 1
        pending_line = f"[Speaker {speaker}] {interim_text} ⏳"
        box.configure(state="normal")
        if hasattr(self, "_clear_text_placeholder"):
            self._clear_text_placeholder(box)
        box.insert("end", pending_line + "\n")
        box.configure(state="disabled")
        box.see(tk.END)
        scrollbar = getattr(box, "_scrollbar", None)
        if scrollbar is not None and hasattr(self, "check_scrollbar_visibility"):
            self.check_scrollbar_visibility(box, scrollbar)

    def _render_transcript_from_store(self):
        """Schedule a debounced transcript re-render to avoid UI thread stalls."""
        job = getattr(self, "_transcript_render_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._transcript_render_job = self.after(
            TRANSCRIPT_RENDER_DEBOUNCE_MS,
            self._render_transcript_from_store_now,
        )

    def _render_transcript_from_store_now(self):
        """Re-render store-backed transcript and append one pending interim line."""
        self._transcript_render_job = None
        box = getattr(self, "initial_verse_box", None)
        if box is not None and hasattr(self, "transcript_store") and self.transcript_store is not None:
            clean = self.transcript_store.get_clean_text()
            if clean.strip():
                self._insert_formatted_text(box, clean)
                box.configure(state="normal")
                if not clean.endswith("\n"):
                    box.insert("end", "\n")
                box.configure(state="disabled")
                box.see(tk.END)
                scrollbar = getattr(box, "_scrollbar", None)
                if scrollbar is not None and hasattr(self, "check_scrollbar_visibility"):
                    self.check_scrollbar_visibility(box, scrollbar)
            else:
                DuplicateProtectionMixin._render_transcript_from_store(self)
        else:
            DuplicateProtectionMixin._render_transcript_from_store(self)
        if (getattr(self, "_latest_interim_text", "") or "").strip():
            self._append_pending_interim_to_display()

    def _apply_final_interim_comparison(self, final_text: str):
        final_text = (final_text or "").strip()
        if not final_text:
            return
        interim_text = (getattr(self, "_latest_interim_text", "") or "").strip()
        norm_final = self._normalize_compare(final_text)
        norm_interim = self._normalize_compare(interim_text)
        action = "keep_interim"
        if norm_interim and norm_final and norm_final in norm_interim:
            action = "keep_interim"
        elif norm_interim and norm_final and norm_interim in norm_final:
            action = "clear_interim"
            self._clear_interim_tail()
        elif not norm_interim:
            action = "no_interim"
        self._last_final_text = final_text
        self._interim_log(
            "[INTERIM] final comparison",
            {
                "action": action,
                "final_len": len(final_text),
                "interim_len": len(interim_text),
                "final_preview": final_text[:120],
                "interim_preview": interim_text[:120],
            },
        )

    def _get_last_final_text_for_recovery(self) -> str:
        last_final = (getattr(self, "_last_final_text", "") or "").strip()
        if last_final:
            return last_final
        if hasattr(self, "transcript_store") and self.transcript_store is not None:
            clean = self.transcript_store.get_clean_text()
            if clean.strip():
                lines = [line.strip() for line in clean.splitlines() if line.strip()]
                if lines:
                    return lines[-1]
        return ""

    def _should_commit_interim_recovery(self, interim_text: str, last_final_text: str):
        norm_interim = self._normalize_compare(interim_text)
        norm_final = self._normalize_compare(last_final_text)
        if len(norm_interim) < 20:
            return False, "too_short"
        if norm_final and norm_final in norm_interim:
            if len(norm_interim) - len(norm_final) < 12:
                return False, "not_meaningfully_longer"
            return True, "interim_extends_final"
        if norm_final and norm_interim in norm_final:
            return False, "interim_in_final"
        if norm_final and norm_interim and norm_interim != norm_final:
            return True, "new_missing_tail"
        if not norm_final:
            return True, "no_prior_final"
        return False, "no_match"

    def _recover_interim_tail_on_stop(self):
        if getattr(self, "_latest_interim_committed", False):
            self._interim_log(
                "[INTERIM] stop tail skipped",
                {"reason": "already_committed"},
            )
            return
        interim_text = (getattr(self, "_latest_interim_text", "") or "").strip()
        if self._is_japanese_manual_mode() and JAPANESE_TEXT_NORMALIZATION_ENABLED:
            interim_text = self._apply_japanese_final_cleanup(interim_text)
        last_final_text = self._get_last_final_text_for_recovery()
        store = getattr(self, "transcript_store", None)
        last_segments_checked = (
            len(store.get_all()[-5:]) if store is not None else 0
        )
        tail_check = self._check_stop_tail_duplicate(interim_text)
        _interim_ndjson_log(
            location="main_window.py:_recover_interim_tail_on_stop",
            message="[INTERIM] stop tail duplicate check",
            data={
                "latest_interim_len": len(interim_text),
                "last_segments_checked": last_segments_checked,
                "decision": tail_check.get("decision"),
                "matched_segment_preview": tail_check.get("matched_segment_preview"),
                "missing_suffix_preview": tail_check.get("missing_suffix_preview"),
            },
        )
        self._interim_log(
            "[INTERIM] stop tail recovery check",
            {
                "interim_len": len(interim_text),
                "last_final_len": len(last_final_text),
                "interim_preview": interim_text[:120],
                "last_final_preview": last_final_text[:120],
                "duplicate_decision": tail_check.get("decision"),
            },
        )
        if not interim_text:
            self._interim_log("[INTERIM] stop tail skipped", {"reason": "empty_interim"})
            self._clear_interim_tail()
            return
        decision = tail_check.get("decision")
        if decision in ("skip_too_short", "skip_already_committed"):
            if decision == "skip_already_committed":
                stats = getattr(self, "_segment_repair_stats", None)
                if stats is None:
                    self._reset_segment_repair_state()
                    stats = self._segment_repair_stats
                stats["stop_tail_duplicate_skipped_count"] += 1
            self._interim_log("[INTERIM] stop tail skipped", {"reason": decision})
            self._clear_interim_tail()
            return
        if decision == "append_missing_suffix":
            merged_text = tail_check.get("commit_text")
            speaker = tail_check.get("update_speaker") or getattr(
                self, "_latest_interim_speaker", 1
            ) or 1
            if store is not None and merged_text:
                store.update_last_segment(speaker, merged_text)
                self._on_store_segment_updated(speaker, merged_text)
                self._track_committed_segment_meta(
                    {"speaker": speaker, "text": merged_text}, merged_text
                )
                self._latest_interim_committed = True
                self._last_final_text = merged_text
                self._clear_interim_tail()
                self._interim_log(
                    "[INTERIM] stop tail appended suffix",
                    {
                        "speaker": speaker,
                        "text_len": len(merged_text),
                        "text_preview": merged_text[:120],
                    },
                )
                return
        should_commit, reason = self._should_commit_interim_recovery(
            interim_text, last_final_text
        )
        if not should_commit:
            self._interim_log("[INTERIM] stop tail skipped", {"reason": reason})
            self._clear_interim_tail()
            return
        speaker = getattr(self, "_latest_interim_speaker", 1) or 1
        item = {
            "speaker": speaker,
            "text": interim_text,
            "is_final": True,
        }
        self._teams_pending_commit_override = ("commit_interim_tail", reason)
        try:
            self._display_transcript_item(item)
        finally:
            self._teams_pending_commit_override = None
        self._latest_interim_committed = True
        self._last_final_text = interim_text
        self._clear_interim_tail()
        self._interim_log(
            "[INTERIM] stop tail committed",
            {
                "reason": reason,
                "speaker": speaker,
                "text_len": len(interim_text),
                "text_preview": interim_text[:120],
            },
        )

    def request_interim_stop_tail_recovery(self, timeout_seconds=2.0):
        """Block until interim tail recovery runs on the UI thread."""
        from alpha.utils.ui_thread_guard import is_ui_main_thread

        if is_ui_main_thread():
            self._recover_interim_tail_on_stop()
            return
        done = threading.Event()

        def _recover():
            try:
                self._recover_interim_tail_on_stop()
            finally:
                done.set()

        from alpha.utils.ui_event_bus import get_ui_event_bus

        get_ui_event_bus().post_schedule_after(0, _recover)
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "STOP_WORKER_TK_CALL_REROUTED_TO_UI_EVENT_BUS",
                operation="stop_ui_recover",
            )
        except Exception:
            pass
        done.wait(timeout=max(0.1, float(timeout_seconds)))

    def _diag_store_segment_count(self):
        if hasattr(self, "transcript_store") and self.transcript_store is not None:
            return self.transcript_store.segment_count()
        return None

    def _diag_transcript_item_fields(self, item):
        speaker = item.get("speaker", 1)
        text = (item.get("text") or "").strip()
        return speaker, text, _diag_text_preview(text)

    def _get_clean_transcript_for_copy_export(self) -> str:
        """Return newline-joined transcript text; normalize per segment only."""
        if not hasattr(self, "transcript_store") or self.transcript_store is None:
            return ""
        lines = []
        for segment in self.transcript_store.get_all():
            text = (segment.text or "").strip()
            if not text:
                continue
            if self._is_japanese_manual_mode() and JAPANESE_TEXT_NORMALIZATION_ENABLED:
                text = self._normalize_japanese_display_text(text)
            prefix = f"[Speaker {segment.speaker}] " if segment.speaker is not None else ""
            lines.append(f"{prefix}{text}")
        return "\n".join(lines)

    def _log_transcript_copy_formatting(self, clean_text: str, segment_count: int):
        line_count = len([line for line in (clean_text or "").splitlines() if line.strip()])
        _session_ndjson_log(
            location="main_window.py:copy_export",
            message="[TRANSCRIPT] copy formatting",
            data={
                "segment_count": segment_count,
                "line_count": line_count,
                "joiner": "newline",
                "preview": _diag_text_preview(clean_text, 220),
            },
        )

    def _log_copy_export_transcript_diag(self, clean_text: str, segment_count: int):
        word_count = len(clean_text.split()) if clean_text else 0
        ending_preview = clean_text[-220:] if clean_text else ""
        _diag_ndjson_log(
            location="main_window.py:copy_export",
            message="[DIAG] copy/export transcript snapshot",
            data={
                "clean_text_len": len(clean_text) if clean_text else 0,
                "clean_word_count": word_count,
                "store_segment_count": segment_count,
                "ending_preview": ending_preview,
            },
        )

    _MEETING_BUFFER_SHORT_COMPLETE = frozenset(
        {
            "yes.",
            "no.",
            "okay.",
            "ok.",
            "perfect.",
            "thanks.",
            "thank you.",
            "bye.",
            "はい。",
            "いいえ。",
            "大丈夫です。",
            "ありがとうございます。",
        }
    )
    _MEETING_BUFFER_WEAK_ENDINGS = frozenset(
        {
            "and",
            "but",
            "or",
            "because",
            "if",
            "when",
            "while",
            "that",
            "which",
            "to",
            "the",
            "a",
            "an",
            "in",
            "on",
            "with",
            "for",
            "about",
            "of",
            "by",
            "as",
            "so",
            "although",
            "however",
            "is",
            "it",
            "my",
            "your",
            "i'll",
            "i'm",
            "we'll",
            "we're",
        }
    )
    _STRONG_SENTENCE_ENDINGS = ".!?。？！।"

    def _normalize_lang_code(self, lang):
        if not lang:
            return None
        code = str(lang).strip().lower().replace("_", "-")
        if code.startswith("ja"):
            return "ja"
        if code.startswith("en"):
            return "en"
        if code.startswith("bn"):
            return "bn"
        if code.startswith("zh"):
            return "zh"
        return code

    def _build_language_profile(self, ui_label: str):
        selected = self._strip_language_flag(ui_label or "")
        manual_code = LANGUAGE_MAP.get(selected)
        code = manual_code or "en"
        return {
            "profile_id": f"manual_{code.replace('-', '_')}",
            "is_auto": False,
            "deepgram_language": code,
            "allowed_languages": [self._normalize_lang_code(code) or code],
            "selection_supported": bool(manual_code),
            "unsupported_reason": None if manual_code else "language_not_supported",
        }

    def _resolve_deepgram_language(self, ui_label: str) -> str:
        """Map UI source language label to Deepgram language code."""
        selected = self._strip_language_flag(ui_label or "")
        profile = self._build_language_profile(ui_label)
        self._language_profile_id = profile["profile_id"]
        self._allowed_languages = profile["allowed_languages"]
        self._profile_is_auto = bool(profile["is_auto"])
        self._selected_source_language_ui_label = selected
        if FORCE_DEEPGRAM_LANGUAGE:
            return FORCE_DEEPGRAM_LANGUAGE
        return profile["deepgram_language"] or LANGUAGE_MAP.get(selected, "en")

    def _selected_source_language_ui(self) -> str:
        try:
            return self._strip_language_flag(self.source_language.get())
        except Exception:
            return "English"

    def _is_japanese_manual_mode(self) -> bool:
        if not JAPANESE_MODE_ENABLED:
            return False
        if (
            bool(AUTO_LANGUAGE_ENABLED)
            or bool(LANGUAGE_GATE_ENABLED)
            or bool(MEETING_SEGMENT_BUFFER_ENABLED)
            or bool(MEETING_SEGMENT_REPAIR_ENABLED)
        ):
            return False
        forced = str(FORCE_DEEPGRAM_LANGUAGE or "").lower()
        if forced in ("ja", "ja-jp"):
            return True
        listen_lang = str(getattr(self, "_listen_language", "") or "").lower()
        if listen_lang in ("ja", "ja-jp"):
            return True
        return self._selected_source_language_ui() == "Japanese"

    def _reset_segment_repair_state(self):
        self._last_committed_segment_meta = {}
        self._segment_repair_stats = {
            "repair_checked_count": 0,
            "repair_merged_count": 0,
            "repair_skipped_count": 0,
            "stop_tail_duplicate_skipped_count": 0,
        }
        self._latest_unstable_language_text = ""
        self._latest_unstable_language_metadata = None
        self._latest_unstable_language_created_at = None
        self._language_stats = {
            "stable_commit_count": 0,
            "warning_commit_count": 0,
            "unexpected_language_warning_count": 0,
            "low_confidence_warning_count": 0,
            "missing_metadata_count": 0,
            "blocked_count": 0,
        }

    def _has_strong_sentence_ending(self, text: str) -> bool:
        segment = (text or "").strip()
        if not segment:
            return False
        return segment[-1] in self._STRONG_SENTENCE_ENDINGS

    def _text_looks_english_or_romaji(self, text: str) -> bool:
        segment = (text or "").strip()
        if not segment:
            return False
        latin = len(re.findall(r"[A-Za-z]", segment))
        cjk = len(
            re.findall(r"[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf]", segment)
        )
        bengali = len(re.findall(r"[\u0980-\u09FF]", segment))
        if bengali > latin and bengali > cjk:
            return False
        if cjk > latin:
            return False
        return latin > 0

    def _is_standalone_short_reply(self, text: str) -> bool:
        segment = (text or "").strip()
        if not segment:
            return False
        lower = segment.lower()
        if lower in self._MEETING_BUFFER_SHORT_COMPLETE:
            return True
        if segment in self._MEETING_BUFFER_SHORT_COMPLETE:
            return True
        if self._has_strong_sentence_ending(segment) and len(segment) <= 24:
            words = segment.split()
            if len(words) <= 4:
                return True
        return False

    def _language_script_warning(self, text, allowed_languages, detected_language):
        segment = (text or "").strip()
        if not segment:
            return None
        normalized_detected = self._normalize_lang_code(detected_language)
        has_cjk = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf]", segment))
        has_bengali = bool(re.search(r"[\u0980-\u09FF]", segment))
        latin_count = len(re.findall(r"[A-Za-z]", segment))
        if allowed_languages is None:
            return None
        allowed = set(allowed_languages)
        if normalized_detected and normalized_detected not in allowed:
            return "detected_language_not_in_en_ja"
        if "bn" not in allowed and has_bengali:
            return "bengali_script_outside_profile"
        if "ja" not in allowed and has_cjk and "zh" not in allowed:
            return "cjk_script_outside_profile"
        if ("ja" in allowed or "en" in allowed) and not has_cjk and latin_count == 0 and len(segment) < 12:
            return "short_non_profile_script"
        return None

    def _log_language_reliability_decision(self, text, metadata, decision, reason, script_warning):
        _language_ndjson_log(
            location="main_window.py:_evaluate_language_reliability",
            message="[LANGUAGE] reliability decision",
            data={
                "text_preview": _diag_text_preview(text, 160),
                "detected_language": metadata.get("detected_language"),
                "language_confidence": metadata.get("language_confidence"),
                "transcript_confidence": metadata.get("transcript_confidence"),
                "allowed_languages": metadata.get("allowed_languages"),
                "selected_profile": metadata.get("selected_profile"),
                "decision": decision,
                "reason": reason,
                "script_warning": script_warning,
            },
        )
        if script_warning:
            _language_ndjson_log(
                location="main_window.py:_evaluate_language_reliability",
                message="[LANGUAGE] script warning",
                data={
                    "text_preview": _diag_text_preview(text, 160),
                    "selected_profile": metadata.get("selected_profile"),
                    "detected_language": metadata.get("detected_language"),
                    "script_warning": script_warning,
                    "decision": decision,
                },
            )

    def _evaluate_language_reliability(self, text, metadata, selected_profile):
        return {
            "decision": "commit",
            "reason": "language_gate_disabled",
            "detected_language": self._normalize_lang_code(metadata.get("detected_language")),
            "language_confidence": metadata.get("language_confidence"),
            "allowed_languages": ["en"],
            "script_warning": None,
        }

    def _log_language_commit_warning(self, text, metadata, reliability):
        _language_ndjson_log(
            location="main_window.py:_display_transcript_item",
            message="[LANGUAGE] warning",
            data={
                "text_preview": _diag_text_preview(text, 160),
                "detected_language": reliability.get("detected_language"),
                "language_confidence": reliability.get("language_confidence"),
                "allowed_languages": metadata.get("allowed_languages"),
                "reason": reliability.get("reason"),
                "action": "committed_warning_only",
                "selected_source_language_ui": metadata.get("selected_profile"),
            },
        )

    def _hold_unstable_language_candidate(self, text, metadata, reason):
        """Legacy hold path — unused in V3.3.4.3 warning-only gate."""
        self._latest_unstable_language_text = (text or "").strip()
        self._latest_unstable_language_metadata = dict(metadata or {})
        self._latest_unstable_language_created_at = time.monotonic()

    def _clear_unstable_language_candidate(self):
        self._latest_unstable_language_text = ""
        self._latest_unstable_language_metadata = None
        self._latest_unstable_language_created_at = None

    def _log_language_summary(self):
        if not DEBUG_TEAMS_DIAGNOSTICS:
            return
        stats = getattr(self, "_language_stats", {}) or {}
        _language_ndjson_log(
            location="main_window.py:_finish_graceful_stop",
            message="[LANGUAGE] summary",
            data={
                "stable_commit_count": int(stats.get("stable_commit_count", 0)),
                "warning_commit_count": int(stats.get("warning_commit_count", 0)),
                "unexpected_language_warning_count": int(
                    stats.get("unexpected_language_warning_count", 0)
                ),
                "low_confidence_warning_count": int(
                    stats.get("low_confidence_warning_count", 0)
                ),
                "missing_metadata_count": int(stats.get("missing_metadata_count", 0)),
                "blocked_count": 0,
            },
        )

    def _ends_with_english_connector(self, text: str) -> bool:
        if not self._text_looks_english_or_romaji(text):
            return False
        segment = (text or "").strip()
        if not segment:
            return False
        tail = re.sub(r"[^\w']", "", segment.split()[-1]).lower()
        return tail in self._MEETING_BUFFER_WEAK_ENDINGS

    def _segment_repair_gap_ms(self, previous_meta, current_meta):
        new_start = (current_meta or {}).get("start_time")
        prev_end = (previous_meta or {}).get("end_time")
        if new_start is not None and prev_end is not None:
            return max(0.0, (float(new_start) - float(prev_end)) * 1000.0)
        prev_mono = (previous_meta or {}).get("committed_monotonic")
        if prev_mono:
            return (time.monotonic() - float(prev_mono)) * 1000.0
        return 0.0

    def _item_to_segment_meta(self, item, speaker=None, text=None):
        speaker_val = speaker if speaker is not None else item.get("speaker", 1)
        if speaker_val is not None and str(speaker_val).isdigit():
            speaker_val = int(speaker_val)
        source = item.get("source")
        if not source:
            snap = getattr(self, "_teams_latest_source_snapshot", {}) or {}
            source = snap.get("chosen_source") or snap.get("speaker_label")
        return {
            "speaker": speaker_val,
            "source": source,
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "speech_final": item.get("speech_final"),
            "detected_language": item.get("detected_language"),
            "committed_monotonic": time.monotonic(),
            "text": (text if text is not None else (item.get("text") or "")).strip(),
        }

    def _track_committed_segment_meta(self, item, text):
        self._last_committed_segment_meta = self._item_to_segment_meta(
            item, text=text
        )

    def _log_segment_repair_check(
        self,
        previous_preview,
        current_preview,
        previous_meta,
        current_meta,
        previous_incomplete,
        decision,
        reason,
        gap_ms,
    ):
        selected = self._selected_source_language_ui()
        _segment_repair_ndjson_log(
            location="main_window.py:_try_segment_repair",
            message="[SEGMENT_REPAIR] check",
            data={
                "previous_preview": _diag_text_preview(previous_preview, 160),
                "current_preview": _diag_text_preview(current_preview, 160),
                "previous_speaker": (previous_meta or {}).get("speaker"),
                "current_speaker": (current_meta or {}).get("speaker"),
                "previous_source": (previous_meta or {}).get("source"),
                "current_source": (current_meta or {}).get("source"),
                "detected_language": (current_meta or {}).get("detected_language"),
                "selected_language": selected,
                "gap_ms": round(float(gap_ms), 1),
                "previous_incomplete": bool(previous_incomplete),
                "decision": decision,
                "reason": reason,
            },
        )

    def _log_segment_repair_skipped(self, reason, previous_preview, current_preview):
        self._segment_repair_stats["repair_skipped_count"] += 1
        _segment_repair_ndjson_log(
            location="main_window.py:_try_segment_repair",
            message="[SEGMENT_REPAIR] skipped",
            data={
                "reason": reason,
                "previous_preview": _diag_text_preview(previous_preview or "", 160),
                "current_preview": _diag_text_preview(current_preview or "", 160),
            },
        )

    def _segment_repair_log_summary(self):
        if not DEBUG_TEAMS_DIAGNOSTICS:
            return
        stats = getattr(self, "_segment_repair_stats", {}) or {}
        _segment_repair_ndjson_log(
            location="main_window.py:_segment_repair_log_summary",
            message="[SEGMENT_REPAIR] summary",
            data={
                "repair_checked_count": int(stats.get("repair_checked_count", 0)),
                "repair_merged_count": int(stats.get("repair_merged_count", 0)),
                "repair_skipped_count": int(stats.get("repair_skipped_count", 0)),
                "stop_tail_duplicate_skipped_count": int(
                    stats.get("stop_tail_duplicate_skipped_count", 0)
                ),
            },
        )

    def _should_repair_previous_segment(
        self, previous, current, previous_meta, current_meta
    ):
        prev = (previous or "").strip()
        curr = (current or "").strip()
        if not prev or not curr:
            return False, "empty_text"
        if self._is_standalone_short_reply(curr):
            return False, "current_standalone_reply"
        selected = self._selected_source_language_ui()
        prev_incomplete, _ = self._looks_incomplete_segment(
            prev,
            (previous_meta or {}).get("speech_final"),
            (previous_meta or {}).get("detected_language"),
            selected,
        )
        if not prev_incomplete and not self._ends_with_english_connector(prev):
            return False, "previous_complete"
        prev_speaker = (previous_meta or {}).get("speaker")
        curr_speaker = (current_meta or {}).get("speaker")
        if (
            prev_speaker is not None
            and curr_speaker is not None
            and prev_speaker != curr_speaker
        ):
            return False, "speaker_changed"
        gap_ms = self._segment_repair_gap_ms(previous_meta, current_meta)
        if gap_ms > MEETING_SEGMENT_REPAIR_MAX_GAP_MS:
            return False, "gap_exceeded"
        norm_prev = self._normalize_compare(prev)
        norm_curr = self._normalize_compare(curr)
        if norm_curr == norm_prev:
            return False, "exact_duplicate"
        if norm_curr in norm_prev:
            return False, "current_contained_in_previous"
        if norm_prev in norm_curr and len(norm_curr) - len(norm_prev) < 8:
            return False, "minor_extension_only"
        if prev_incomplete:
            return True, "previous_incomplete"
        if self._ends_with_english_connector(prev):
            return True, "previous_english_connector"
        if not self._has_strong_sentence_ending(prev):
            return True, "previous_no_punctuation"
        return False, "not_continuation"

    def _merge_text_with_overlap_info(self, previous, current):
        prev = (previous or "").rstrip()
        curr = (current or "").lstrip()
        if not prev:
            return curr, "none", 0
        if not curr:
            return prev, "none", 0

        use_word_overlap = self._text_looks_english_or_romaji(
            prev
        ) or self._text_looks_english_or_romaji(curr)
        if use_word_overlap and MEETING_BUFFER_ENABLE_OVERLAP_MERGE:
            prev_words = re.findall(r"\w+(?:'\w+)?", prev.lower())
            curr_words = re.findall(r"\w+(?:'\w+)?", curr.lower())
            max_size = min(10, len(prev_words), len(curr_words))
            for size in range(max_size, 2, -1):
                if prev_words[-size:] == curr_words[:size]:
                    pattern = (
                        r"\b"
                        + r"\s+".join(re.escape(w) for w in curr_words[:size])
                        + r"\b"
                    )
                    match = re.search(pattern, curr, flags=re.IGNORECASE)
                    if match:
                        suffix = curr[match.end() :].lstrip()
                        if suffix:
                            return f"{prev} {suffix}", "word", size
                        return prev, "word", size

        prev_chars = prev
        curr_chars = curr
        max_char = min(30, len(prev_chars), len(curr_chars))
        for size in range(max_char, 7, -1):
            if prev_chars[-size:].lower() == curr_chars[:size].lower():
                suffix = curr_chars[size:].lstrip()
                if suffix:
                    if use_word_overlap:
                        return f"{prev} {suffix}", "char", size
                    return f"{prev}{suffix}", "char", size
                return prev, "char", size

        if use_word_overlap:
            return f"{prev} {curr}", "none", 0
        if prev.endswith(" ") or curr.startswith(" "):
            return f"{prev}{curr}", "none", 0
        return f"{prev}{curr}", "none", 0

    def _try_segment_repair(self, item):
        """Merge current candidate into previous store segment when safe."""
        if not MEETING_SEGMENT_REPAIR_ENABLED:
            return False
        self._segment_repair_stats["repair_checked_count"] += 1
        speaker, text, _source = self._meeting_buffer_item_fields(item)
        if not text:
            self._log_segment_repair_skipped("empty_current", None, text)
            return False
        store = getattr(self, "transcript_store", None)
        if store is None or store.is_empty():
            self._log_segment_repair_skipped("no_previous_segment", None, text)
            return False
        last_segment = store.get_last_segment()
        if last_segment is None or not (last_segment.text or "").strip():
            self._log_segment_repair_skipped("no_previous_segment", None, text)
            return False
        previous_text = last_segment.text.strip()
        previous_meta = dict(getattr(self, "_last_committed_segment_meta", {}) or {})
        if not previous_meta:
            previous_meta = {
                "speaker": last_segment.speaker,
                "text": previous_text,
            }
        previous_meta.setdefault("speaker", last_segment.speaker)
        current_meta = self._item_to_segment_meta(item, speaker=speaker, text=text)
        selected = self._selected_source_language_ui()
        prev_incomplete, _ = self._looks_incomplete_segment(
            previous_text,
            previous_meta.get("speech_final"),
            previous_meta.get("detected_language"),
            selected,
        )
        gap_ms = self._segment_repair_gap_ms(previous_meta, current_meta)
        should_repair, reason = self._should_repair_previous_segment(
            previous_text, text, previous_meta, current_meta
        )
        self._log_segment_repair_check(
            previous_text,
            text,
            previous_meta,
            current_meta,
            prev_incomplete,
            "repair" if should_repair else "skip",
            reason,
            gap_ms,
        )
        if not should_repair:
            self._log_segment_repair_skipped(reason, previous_text, text)
            return False
        merged, overlap_type, overlap_units = self._merge_text_with_overlap_info(
            previous_text, text
        )
        if self._normalize_compare(merged) == self._normalize_compare(previous_text):
            self._log_segment_repair_skipped("merged_unchanged", previous_text, text)
            return False
        if self._normalize_compare(text) == self._normalize_compare(merged):
            self._log_segment_repair_skipped("no_new_content", previous_text, text)
            return False
        repair_speaker = last_segment.speaker if last_segment.speaker is not None else speaker
        updated = store.update_last_segment(
            repair_speaker,
            merged,
            timestamp=item.get("timestamp"),
        )
        if not updated:
            self._log_segment_repair_skipped("update_failed", previous_text, text)
            return False
        self._segment_repair_stats["repair_merged_count"] += 1
        _segment_repair_ndjson_log(
            location="main_window.py:_try_segment_repair",
            message="[SEGMENT_REPAIR] merged_previous",
            data={
                "previous_len": len(previous_text),
                "current_len": len(text),
                "merged_len": len(merged),
                "overlap_type": overlap_type,
                "overlap_units": overlap_units,
                "merged_preview": _diag_text_preview(merged, 160),
            },
        )
        self._teams_log_commit_decision(
            "repair_merge",
            reason,
            repair_speaker,
            merged,
            store.segment_count(),
            store.segment_count(),
            speech_final=item.get("speech_final"),
        )
        self._track_committed_segment_meta(item, merged)
        DuplicateProtectionMixin._render_transcript_from_store(self)
        self._last_final_text = merged
        return True

    def _reset_meeting_segment_buffer_state(self):
        self._meeting_segment_buffer = {
            "speaker": None,
            "source": None,
            "text": "",
            "start_time": None,
            "end_time": None,
            "speech_final": None,
            "created_monotonic": None,
            "updated_monotonic": None,
            "segment_count": 0,
        }
        self._meeting_buffer_stats = {
            "held_count": 0,
            "merged_count": 0,
            "flushed_count": 0,
            "committed_count": 0,
            "timeout_flush_count": 0,
            "speaker_change_flush_count": 0,
            "source_change_flush_count": 0,
        }

    def _meeting_buffer_has_content(self):
        return bool((getattr(self, "_meeting_segment_buffer", {}) or {}).get("text", "").strip())

    def _looks_incomplete_segment(
        self,
        text,
        speech_final=None,
        detected_language=None,
        selected_language=None,
    ):
        segment = (text or "").strip()
        if not segment:
            return False, "empty"
        lower = segment.lower()
        if lower in self._MEETING_BUFFER_SHORT_COMPLETE or segment in self._MEETING_BUFFER_SHORT_COMPLETE:
            return False, "complete_short_response"
        if self._has_strong_sentence_ending(segment):
            if speech_final is False and self._text_looks_english_or_romaji(segment):
                word_count = len(segment.split())
                if word_count >= MEETING_BUFFER_MIN_FRAGMENT_WORDS:
                    return False, "sentence_end_punctuation"
            return False, "sentence_end_punctuation"
        selected = selected_language or self._selected_source_language_ui()
        english_context = (
            detected_language in (None, "en", "en-US", "en-GB", "multi")
            and (
                selected == "English" or selected.startswith("Auto:")
                or self._text_looks_english_or_romaji(segment)
            )
        )
        if english_context and self._text_looks_english_or_romaji(segment):
            tail = re.sub(r"[^\w']", "", segment.split()[-1]).lower()
            if tail in self._MEETING_BUFFER_WEAK_ENDINGS:
                return True, "ends_with_connector"
            if len(segment.split()) < MEETING_BUFFER_MIN_FRAGMENT_WORDS:
                return True, "very_short_fragment"
        if speech_final is False:
            return True, "speech_final_false"
        if not self._has_strong_sentence_ending(segment):
            return True, "no_sentence_end_punctuation"
        return False, "complete"

    def _merge_text_without_overlap(self, previous, current):
        merged, _overlap_type, _overlap_units = self._merge_text_with_overlap_info(
            previous, current
        )
        return merged

    def _meeting_buffer_item_fields(self, item):
        speaker = item.get("speaker", 1)
        if speaker is not None and str(speaker).isdigit():
            speaker = int(speaker)
        text = (item.get("text") or "").strip()
        source = item.get("source")
        if not source:
            snap = getattr(self, "_teams_latest_source_snapshot", {}) or {}
            source = snap.get("chosen_source") or snap.get("speaker_label")
        return speaker, text, source

    def _meeting_buffer_gap_ms(self, item):
        buf = getattr(self, "_meeting_segment_buffer", {}) or {}
        new_start = item.get("start_time")
        buf_end = buf.get("end_time")
        if new_start is not None and buf_end is not None:
            return max(0.0, (float(new_start) - float(buf_end)) * 1000.0)
        updated = buf.get("updated_monotonic")
        if updated:
            return (time.monotonic() - float(updated)) * 1000.0
        return 0.0

    def _meeting_buffer_hold_ms(self):
        buf = getattr(self, "_meeting_segment_buffer", {}) or {}
        created = buf.get("created_monotonic")
        if not created:
            return 0.0
        return (time.monotonic() - float(created)) * 1000.0

    def _meeting_buffer_clear(self):
        if hasattr(self, "_meeting_segment_buffer"):
            self._meeting_segment_buffer["text"] = ""
            self._meeting_segment_buffer["segment_count"] = 0
            self._meeting_segment_buffer["created_monotonic"] = None
            self._meeting_segment_buffer["updated_monotonic"] = None

    def _meeting_buffer_hold_fragment(self, speaker, source, text, item, reason):
        now = time.monotonic()
        buf = self._meeting_segment_buffer
        if not self._meeting_buffer_has_content():
            buf["speaker"] = speaker
            buf["source"] = source
            buf["text"] = text
            buf["start_time"] = item.get("start_time")
            buf["end_time"] = item.get("end_time")
            buf["speech_final"] = item.get("speech_final")
            buf["created_monotonic"] = now
            buf["updated_monotonic"] = now
            buf["segment_count"] = 1
        else:
            buf["text"] = text
            buf["end_time"] = item.get("end_time") or buf.get("end_time")
            buf["speech_final"] = item.get("speech_final")
            buf["updated_monotonic"] = now
            buf["segment_count"] = int(buf.get("segment_count") or 0) + 1
        self._meeting_buffer_stats["held_count"] += 1
        _segment_buffer_ndjson_log(
            location="main_window.py:_meeting_buffer_hold_fragment",
            message="[SEGMENT_BUFFER] hold",
            data={
                "speaker_label": speaker,
                "source": source,
                "buffer_text_len": len(buf["text"]),
                "buffer_preview": _diag_text_preview(buf["text"], 160),
                "reason": reason,
            },
        )
        self._teams_log_commit_decision(
            "buffer_hold",
            reason,
            speaker,
            text,
            self._diag_store_segment_count(),
            self._diag_store_segment_count(),
            speech_final=item.get("speech_final"),
        )

    def _meeting_buffer_log_committed(self, speaker, source, text, store_before, store_after):
        self._meeting_buffer_stats["committed_count"] += 1
        _segment_buffer_ndjson_log(
            location="main_window.py:_meeting_buffer_log_committed",
            message="[SEGMENT_BUFFER] committed",
            data={
                "speaker_label": speaker,
                "source": source,
                "text_len": len(text),
                "text_preview": _diag_text_preview(text, 160),
                "store_segment_count_before": store_before,
                "store_segment_count_after": store_after,
            },
        )

    def _flush_meeting_segment_buffer(self, reason):
        if not self._meeting_buffer_has_content():
            return
        buf = self._meeting_segment_buffer
        text = (buf.get("text") or "").strip()
        if not text:
            self._meeting_buffer_clear()
            return
        speaker = buf.get("speaker") or 1
        source = buf.get("source")
        item = {
            "speaker": speaker,
            "text": text,
            "is_final": True,
            "speech_final": buf.get("speech_final"),
            "source": source,
            "start_time": buf.get("start_time"),
            "end_time": buf.get("end_time"),
        }
        stats = self._meeting_buffer_stats
        stats["flushed_count"] += 1
        if reason == "max_hold_timeout":
            stats["timeout_flush_count"] += 1
        elif reason == "speaker_changed":
            stats["speaker_change_flush_count"] += 1
        elif reason == "source_changed":
            stats["source_change_flush_count"] += 1
        _segment_buffer_ndjson_log(
            location="main_window.py:_flush_meeting_segment_buffer",
            message="[SEGMENT_BUFFER] flush",
            data={
                "speaker_label": speaker,
                "source": source,
                "text_len": len(text),
                "text_preview": _diag_text_preview(text, 160),
                "reason": reason,
            },
        )
        self._meeting_buffer_clear()
        store_before = self._diag_store_segment_count()
        self._commit_transcript_item_to_store(
            item,
            buffer_decision=("buffer_flush_commit", reason),
        )
        store_after = self._diag_store_segment_count()
        if store_after != store_before:
            self._meeting_buffer_log_committed(
                speaker, source, text, store_before, store_after
            )

    def _meeting_buffer_log_summary(self):
        if not DEBUG_TEAMS_DIAGNOSTICS:
            return
        stats = getattr(self, "_meeting_buffer_stats", {}) or {}
        _segment_buffer_ndjson_log(
            location="main_window.py:_meeting_buffer_log_summary",
            message="[SEGMENT_BUFFER] summary",
            data={
                "held_count": int(stats.get("held_count", 0)),
                "merged_count": int(stats.get("merged_count", 0)),
                "flushed_count": int(stats.get("flushed_count", 0)),
                "committed_count": int(stats.get("committed_count", 0)),
                "timeout_flush_count": int(stats.get("timeout_flush_count", 0)),
                "speaker_change_flush_count": int(
                    stats.get("speaker_change_flush_count", 0)
                ),
                "source_change_flush_count": int(
                    stats.get("source_change_flush_count", 0)
                ),
            },
        )

    def _meeting_buffer_process_candidate(self, item):
        """Return True when candidate is held and store commit should wait."""
        speaker, text, source = self._meeting_buffer_item_fields(item)
        speech_final = item.get("speech_final")
        incomplete, incomplete_reason = self._looks_incomplete_segment(text, speech_final)

        _segment_buffer_ndjson_log(
            location="main_window.py:_meeting_buffer_process_candidate",
            message="[SEGMENT_BUFFER] candidate",
            data={
                "elapsed_sec": self._teams_elapsed_sec(),
                "speaker_label": speaker,
                "source": source,
                "speech_final": speech_final,
                "text_len": len(text),
                "text_preview": _diag_text_preview(text, 160),
                "looks_incomplete": incomplete,
                "reason": incomplete_reason,
            },
        )

        if self._meeting_buffer_has_content():
            buf = self._meeting_segment_buffer
            if self._meeting_buffer_hold_ms() > MEETING_BUFFER_MAX_HOLD_MS:
                self._flush_meeting_segment_buffer("max_hold_timeout")
            elif (
                MEETING_BUFFER_FLUSH_ON_SPEAKER_CHANGE
                and buf.get("speaker") is not None
                and speaker != buf.get("speaker")
            ):
                self._flush_meeting_segment_buffer("speaker_changed")
            elif (
                MEETING_BUFFER_FLUSH_ON_SOURCE_CHANGE
                and buf.get("source")
                and source
                and source != buf.get("source")
                and source != "none"
                and buf.get("source") != "none"
            ):
                self._flush_meeting_segment_buffer("source_changed")
            elif self._meeting_buffer_gap_ms(item) > MEETING_BUFFER_MAX_GAP_MS:
                self._flush_meeting_segment_buffer("gap_exceeded")

        if not self._meeting_buffer_has_content():
            if incomplete:
                self._meeting_buffer_hold_fragment(
                    speaker, source, text, item, incomplete_reason
                )
                return True
            return False

        buf = self._meeting_segment_buffer
        same_speaker = speaker == buf.get("speaker")
        same_source = not source or not buf.get("source") or source == buf.get("source")
        gap_ok = self._meeting_buffer_gap_ms(item) <= MEETING_BUFFER_MAX_GAP_MS
        buf_incomplete, _ = self._looks_incomplete_segment(
            buf.get("text", ""), buf.get("speech_final")
        )

        if same_speaker and same_source and gap_ok and (buf_incomplete or incomplete):
            previous = buf.get("text", "")
            merged, overlap_words = self._merge_text_without_overlap_counted(
                previous, text
            )
            self._meeting_buffer_stats["merged_count"] += 1
            _segment_buffer_ndjson_log(
                location="main_window.py:_meeting_buffer_process_candidate",
                message="[SEGMENT_BUFFER] merge",
                data={
                    "speaker_label": speaker,
                    "source": source,
                    "previous_len": len(previous),
                    "current_len": len(text),
                    "merged_len": len(merged),
                    "overlap_words": overlap_words,
                    "merged_preview": _diag_text_preview(merged, 160),
                },
            )
            self._teams_log_commit_decision(
                "buffer_merge",
                "merge_with_buffer",
                speaker,
                merged,
                self._diag_store_segment_count(),
                self._diag_store_segment_count(),
                speech_final=speech_final,
            )
            merged_incomplete, merged_reason = self._looks_incomplete_segment(
                merged, speech_final
            )
            now = time.monotonic()
            buf["text"] = merged
            buf["end_time"] = item.get("end_time") or buf.get("end_time")
            buf["speech_final"] = speech_final
            buf["updated_monotonic"] = now
            buf["segment_count"] = int(buf.get("segment_count") or 0) + 1
            if merged_incomplete:
                self._meeting_buffer_hold_fragment(
                    speaker, source, merged, item, merged_reason
                )
                return True
            self._flush_meeting_segment_buffer("complete_sentence")
            return True

        self._flush_meeting_segment_buffer("unrelated_new_segment")
        if incomplete:
            self._meeting_buffer_hold_fragment(speaker, source, text, item, incomplete_reason)
            return True
        return False

    def _merge_text_without_overlap_counted(self, previous, current):
        merged, overlap_type, overlap_units = self._merge_text_with_overlap_info(
            previous, current
        )
        overlap = overlap_units if overlap_type == "word" else 0
        return merged, overlap

    def _check_stop_tail_duplicate(self, interim_text: str):
        """Compare interim tail against recent committed segments before Stop commit."""
        interim = (interim_text or "").strip()
        store = getattr(self, "transcript_store", None)
        last_segments = []
        if store is not None:
            last_segments = store.get_all()[-5:]
        norm_interim = self._normalize_compare(interim)
        if len(norm_interim) < 3:
            return {
                "decision": "skip_too_short",
                "matched_segment_preview": None,
                "missing_suffix_preview": None,
                "commit_text": None,
            }
        for segment in reversed(last_segments):
            seg_text = (segment.text or "").strip()
            norm_seg = self._normalize_compare(seg_text)
            if not norm_seg:
                continue
            if norm_interim == norm_seg or norm_interim in norm_seg:
                return {
                    "decision": "skip_already_committed",
                    "matched_segment_preview": _diag_text_preview(seg_text, 160),
                    "missing_suffix_preview": None,
                    "commit_text": None,
                }
        if last_segments:
            last_seg = last_segments[-1]
            last_text = (last_seg.text or "").strip()
            norm_last = self._normalize_compare(last_text)
            if norm_last and norm_interim.startswith(norm_last) and len(norm_interim) > len(
                norm_last
            ):
                merged, overlap_type, _ = self._merge_text_with_overlap_info(
                    last_text, interim
                )
                if self._normalize_compare(merged) == norm_last:
                    return {
                        "decision": "skip_already_committed",
                        "matched_segment_preview": _diag_text_preview(last_text, 160),
                        "missing_suffix_preview": None,
                        "commit_text": None,
                    }
                missing_suffix = merged[len(last_text) :].strip() if merged.startswith(
                    last_text[: min(len(last_text), len(merged))]
                ) else merged
                if overlap_type != "none" or len(self._normalize_compare(missing_suffix)) >= 3:
                    return {
                        "decision": "append_missing_suffix",
                        "matched_segment_preview": _diag_text_preview(last_text, 160),
                        "missing_suffix_preview": _diag_text_preview(
                            missing_suffix, 160
                        ),
                        "commit_text": merged,
                        "update_speaker": last_seg.speaker,
                    }
        return {
            "decision": "commit_new_tail",
            "matched_segment_preview": None,
            "missing_suffix_preview": None,
            "commit_text": interim,
        }

    def _commit_transcript_item_to_store(self, item, buffer_decision=None):
        """Commit one final item through duplicate protection into TranscriptStore."""
        speaker, text, preview = self._diag_transcript_item_fields(item)
        is_finalizing = bool(getattr(self, "_is_finalizing", False))
        is_listening = bool(self.is_listening)
        store_count_before = self._diag_store_segment_count()
        speech_final = item.get("speech_final")

        _diag_ndjson_log(
            location="main_window.py:_display_transcript_item",
            message="[DIAG] transcript commit attempt",
            data={
                "is_finalizing": is_finalizing,
                "is_listening": is_listening,
                "speaker": speaker,
                "text_len": len(text),
                "text_preview": preview,
                "store_count_before": store_count_before,
            },
        )

        if item.get("is_final") is False:
            _diag_ndjson_log(
                location="main_window.py:_display_transcript_item",
                message="[DIAG] transcript commit skipped",
                data={
                    "reason": "interim_not_final",
                    "is_finalizing": is_finalizing,
                    "is_listening": is_listening,
                    "speaker": speaker,
                    "text_len": len(text),
                    "text_preview": preview,
                },
            )
            return
        if not self._should_accept_transcript_commit():
            if is_finalizing:
                print(
                    "[STOP][ERROR] final transcript received during finalize "
                    "but commit was skipped"
                )
            if not is_listening and not is_finalizing:
                skip_reason = "not_listening_and_not_finalizing"
            elif not is_listening:
                skip_reason = "not_listening"
            else:
                skip_reason = "should_accept_transcript_commit_false"
            self._teams_log_commit_decision(
                "skip_duplicate",
                skip_reason,
                speaker,
                text,
                store_count_before,
                store_count_before,
                speech_final=speech_final,
            )
            _diag_ndjson_log(
                location="main_window.py:_display_transcript_item",
                message="[DIAG] transcript commit skipped",
                data={
                    "reason": skip_reason,
                    "is_finalizing": is_finalizing,
                    "is_listening": is_listening,
                    "speaker": speaker,
                    "text_len": len(text),
                    "text_preview": preview,
                },
            )
            return
        if not text:
            self._teams_log_commit_decision(
                "skip_too_short",
                "empty_text",
                speaker,
                text,
                store_count_before,
                store_count_before,
                speech_final=speech_final,
            )
            _diag_ndjson_log(
                location="main_window.py:_display_transcript_item",
                message="[DIAG] transcript commit skipped",
                data={
                    "reason": "empty_text",
                    "is_finalizing": is_finalizing,
                    "is_listening": is_listening,
                    "speaker": speaker,
                    "text_len": 0,
                    "text_preview": "",
                },
            )
            return

        previous_text = None
        if hasattr(self, "transcript_store") and self.transcript_store is not None:
            segment = self.transcript_store.get_last_segment(speaker)
            if segment is not None:
                previous_text = segment.text
        if self._is_japanese_manual_mode() and not item.get("_jp_cleaned"):
            cleaned_text = self._apply_japanese_final_cleanup_timed(text, source="ui_commit")
            item["text"] = cleaned_text
            item["_jp_cleaned"] = True
            text = cleaned_text
            preview = _diag_text_preview(text)
            if previous_text:
                previous_text = self._normalize_japanese_display_text(previous_text)
        if self._is_japanese_manual_mode() and previous_text and text:
            cross_segment = self._evaluate_japanese_cross_segment_merge(
                previous_text, text
            )
            if cross_segment:
                if self._commit_japanese_update_previous_segment(
                    speaker,
                    cross_segment["merged_text"],
                    cross_segment["decision"],
                    cross_segment["reason"],
                    previous_text,
                    text,
                    store_count_before,
                    speech_final,
                    item,
                    is_finalizing,
                ):
                    return
        dup_action, _result_text = decide_transcript_action(previous_text, text)
        predicted_decision, predicted_reason = teams_commit_decision_from_dup_action(
            dup_action, previous_text, text
        )
        if self._is_japanese_manual_mode() and JAPANESE_CHAR_DEDUP_ENABLED:
            jp_decision, jp_reason = self._evaluate_japanese_commit_dedup(text, previous_text)
            if jp_decision:
                predicted_decision = jp_decision
                predicted_reason = jp_reason
                if jp_decision == "skip_duplicate":
                    dup_action = "skip"
        commit_override = getattr(self, "_teams_pending_commit_override", None)
        if commit_override:
            predicted_decision, predicted_reason = commit_override
        elif buffer_decision:
            predicted_decision, predicted_reason = buffer_decision
        if self._is_japanese_manual_mode():
            forbidden_tokens = {
                "language_hold",
                "language_skip",
                "buffer_hold",
                "segment_repair_skip",
                "skip_low_confidence",
                "skip_no_punctuation",
            }
            if (
                str(predicted_decision) in forbidden_tokens
                or str(predicted_reason) in forbidden_tokens
            ):
                _session_ndjson_log(
                    location="main_window.py:_display_transcript_item",
                    message="[CJK] forbidden middleware detected",
                    data={
                        "forbidden_path": str(predicted_decision),
                        "action": str(predicted_reason),
                    },
                )
        if self._is_japanese_manual_mode():
            predicted_decision = {
                "commit_interim_tail": "commit_new",
                "commit_complete": "commit_new",
                "commit_candidate": "commit_new",
                "skip_duplicate": "skip_duplicate",
                "update_previous": "update_previous_safe",
                "tail_stitched_update_previous": "tail_stitched_update_previous",
                "particle_continuation_update_previous": (
                    "particle_continuation_update_previous"
                ),
                "compound_continuation_update_previous": (
                    "compound_continuation_update_previous"
                ),
            }.get(predicted_decision, "commit_new")

        if is_finalizing and DEBUG_DIAGNOSTICS:
            print("[STOP] committing final transcript during finalize")
        DuplicateProtectionMixin._display_transcript_item(self, item)
        store_count_after = self._diag_store_segment_count()
        if store_count_after == store_count_before:
            if dup_action == "skip":
                skip_decision, skip_reason = teams_commit_decision_from_dup_action(
                    dup_action, previous_text, text
                )
            elif self._is_japanese_manual_mode():
                predicted_decision = "append_missing_suffix"
                predicted_reason = "japanese_overlap_or_store_unchanged"
            else:
                skip_decision, skip_reason = (
                    "skip_duplicate",
                    "duplicate_or_store_unchanged",
                )
            if dup_action == "skip" or not self._is_japanese_manual_mode():
                self._teams_log_commit_decision(
                    skip_decision,
                    skip_reason,
                    speaker,
                    text,
                    store_count_before,
                    store_count_after,
                    speech_final=speech_final,
                )
                _diag_ndjson_log(
                    location="main_window.py:_display_transcript_item",
                    message="[DIAG] transcript commit skipped",
                    data={
                        "reason": "duplicate_or_store_unchanged",
                        "is_finalizing": is_finalizing,
                        "is_listening": is_listening,
                        "speaker": speaker,
                        "text_len": len(text),
                        "text_preview": preview,
                    },
                )
                if self._is_japanese_manual_mode():
                    _session_ndjson_log(
                        location="main_window.py:_display_transcript_item",
                        message="[JAPANESE] commit decision",
                        data={
                            "decision": "skip_duplicate",
                            "reason": skip_reason,
                            "text_preview": preview,
                            "compact_len": len(self._compact_japanese_for_compare(text)),
                            "store_segment_count_before": store_count_before,
                            "store_segment_count_after": store_count_after,
                        },
                    )
                return
        self._teams_log_commit_decision(
            predicted_decision,
            predicted_reason,
            speaker,
            text,
            store_count_before,
            store_count_after,
            speech_final=speech_final,
        )
        self._diag_last_committed_preview = preview
        self.record_latency_commit()
        self.log_latency_transcript_committed(
            text=text,
            is_finalizing=is_finalizing,
            store_segment_count=store_count_after,
        )
        _diag_ndjson_log(
            location="main_window.py:_display_transcript_item",
            message="[DIAG] transcript commit success",
            data={
                "speaker": speaker,
                "text_len": len(text),
                "text_preview": preview,
                "store_count_after": store_count_after,
            },
        )
        if self._is_japanese_manual_mode():
            if predicted_decision not in {
                "commit_new",
                "skip_duplicate",
                "append_missing_suffix",
                "update_previous_safe",
                "tail_stitched_update_previous",
                "particle_continuation_update_previous",
                "compound_continuation_update_previous",
            }:
                _session_ndjson_log(
                    location="main_window.py:_display_transcript_item",
                    message="[CJK] forbidden middleware detected",
                    data={
                        "forbidden_path": str(predicted_decision),
                        "action": str(predicted_reason),
                    },
                )
            _session_ndjson_log(
                location="main_window.py:_display_transcript_item",
                message="[JAPANESE] commit decision",
                data={
                    "decision": predicted_decision,
                    "reason": predicted_reason,
                    "text_preview": preview,
                    "compact_len": len(self._compact_japanese_for_compare(text)),
                    "store_segment_count_before": store_count_before,
                    "store_segment_count_after": store_count_after,
                },
            )
        if is_finalizing:
            print("[STOP] final transcript committed during finalize")
        self._track_committed_segment_meta(item, text)
        self._apply_final_interim_comparison(text)

    def _display_transcript_item(self, item):
        """Route finals through segment repair or legacy buffer before store commit."""
        speaker, text, preview = self._diag_transcript_item_fields(item)
        is_finalizing = bool(getattr(self, "_is_finalizing", False))
        is_listening = bool(self.is_listening)
        store_count_before = self._diag_store_segment_count()
        speech_final = item.get("speech_final")

        if item.get("is_final") is False:
            return
        if not self._should_accept_transcript_commit():
            if is_finalizing:
                print(
                    "[STOP][ERROR] final transcript received during finalize "
                    "but commit was skipped"
                )
            if not is_listening and not is_finalizing:
                skip_reason = "not_listening_and_not_finalizing"
            elif not is_listening:
                skip_reason = "not_listening"
            else:
                skip_reason = "should_accept_transcript_commit_false"
            self._teams_log_commit_decision(
                "skip_duplicate",
                skip_reason,
                speaker,
                text,
                store_count_before,
                store_count_before,
                speech_final=speech_final,
            )
            return
        if not text:
            self._teams_log_commit_decision(
                "skip_too_short",
                "empty_text",
                speaker,
                text,
                store_count_before,
                store_count_before,
                speech_final=speech_final,
            )
            return

        if self._is_japanese_manual_mode():
            if (
                str(getattr(self, "_listen_language", "")).lower() == "multi"
                or MEETING_SEGMENT_REPAIR_ENABLED
                or MEETING_SEGMENT_BUFFER_ENABLED
            ):
                _session_ndjson_log(
                    location="main_window.py:_display_transcript_item",
                    message="[CJK] forbidden middleware detected",
                    data={
                        "forbidden_path": "language=multi_or_segment_repair_or_buffer_hold",
                        "action": "logged_only",
                    },
                )
            raw_text = text
            resolved_reason = self._resolve_japanese_ui_commit_reason(item)
            candidate_item = dict(item)
            if resolved_reason:
                candidate_item.setdefault("commit_reason", resolved_reason)
                candidate_item.setdefault("stabilizer_reason", resolved_reason)
            if block_rogue_japanese_direct_commit(self, candidate_item):
                _session_ndjson_log(
                    location="main_window.py:_display_transcript_item",
                    message="[JAPANESE] commit blocked",
                    data={
                        "reason": "ROGUE_DIRECT_JAPANESE_COMMIT_BLOCKED",
                        "text_preview": preview,
                        "is_final": bool(item.get("is_final")),
                    },
                )
                return
            if resolved_reason:
                item.setdefault("commit_reason", resolved_reason)
                item.setdefault("stabilizer_reason", resolved_reason)
            if not item.get("_jp_cleaned"):
                _session_ndjson_log(
                    location="main_window.py:_display_transcript_item",
                    message="[JAPANESE] final candidate",
                    data={
                        "raw_text_preview": preview,
                        "raw_len": len(raw_text),
                        "speech_final": speech_final,
                        "is_final": bool(item.get("is_final")),
                        "is_finalizing": is_finalizing,
                    },
                )
                cleaned_text = self._apply_japanese_final_cleanup_timed(
                    raw_text, source="ui_fallback"
                )
                _session_ndjson_log(
                    location="main_window.py:_display_transcript_item",
                    message="[JAPANESE] final cleaned",
                    data={
                        "raw_text_preview": preview,
                        "cleaned_text_preview": _diag_text_preview(cleaned_text),
                        "raw_len": len(raw_text),
                        "cleaned_len": len(cleaned_text),
                    },
                )
                item["text"] = cleaned_text
                item["_jp_cleaned"] = True
            else:
                cleaned_text = text
            if getattr(self, "_teams_pending_commit_override", None):
                self._commit_transcript_item_to_store(item)
                return
            self._commit_transcript_item_to_store(
                item,
                buffer_decision=("commit_new", resolved_reason or "japanese_direct_commit"),
            )
            return

        if getattr(self, "_teams_pending_commit_override", None):
            self._commit_transcript_item_to_store(item)
            return

        language_meta = {
            "detected_language": self._normalize_lang_code(item.get("detected_language")),
            "language_confidence": item.get("language_confidence"),
            "transcript_confidence": item.get("transcript_confidence"),
            "allowed_languages": item.get("allowed_languages")
            if item.get("allowed_languages") is not None
            else self._allowed_languages,
            "selected_profile": item.get("selected_source_language_ui")
            or self._selected_source_language_ui(),
            "is_auto_profile": bool(
                str(item.get("language_profile_id") or self._language_profile_id).startswith("auto_")
            ),
        }
        reliability = self._evaluate_language_reliability(
            text=text,
            metadata=language_meta,
            selected_profile=language_meta["selected_profile"],
        )
        self._log_language_reliability_decision(
            text=text,
            metadata=language_meta,
            decision=reliability["decision"],
            reason=reliability["reason"],
            script_warning=reliability.get("script_warning"),
        )
        self._language_stats["stable_commit_count"] += 1
        self._clear_unstable_language_candidate()

        if MEETING_SEGMENT_REPAIR_ENABLED and self._try_segment_repair(item):
            return

        if MEETING_SEGMENT_BUFFER_ENABLED and self._meeting_buffer_process_candidate(item):
            return

        self._commit_transcript_item_to_store(
            item, buffer_decision=("commit_complete", "complete_sentence")
        )

    def _bind_keyboard_shortcuts(self):
        """Global shortcuts that avoid interfering with text box copy/select."""
        self.bind_all("<Control-l>", self._shortcut_toggle_listening)
        self.bind_all("<Control-L>", self._shortcut_toggle_listening)
        self.bind_all("<Control-k>", self._shortcut_clear_text)
        self.bind_all("<Control-K>", self._shortcut_clear_text)
        self.bind("<Escape>", self._shortcut_escape_stop)

    def _is_text_widget_focused(self):
        """True when focus is inside a transcript/translation text panel."""
        focused = self.focus_get()
        return isinstance(focused, tk.Text)

    def _shortcut_toggle_listening(self, _event=None):
        if self._is_text_widget_focused():
            return
        self.toggle_listening()
        return "break"

    def _shortcut_clear_text(self, _event=None):
        if self._is_text_widget_focused():
            return
        self.clear_text()
        return "break"

    def _shortcut_escape_stop(self, _event=None):
        if self._is_text_widget_focused():
            return
        if self.is_listening:
            self._stop_listening()
            return "break"
        return None

    # -----------------------------------------------------------------------
    # Deepgram real-time speech-to-text
    # -----------------------------------------------------------------------
    def audio_mixer_worker(self):
        """Wall-clock mono 16 kHz mixer: WASAPI + gated mic on one timeline (not concatenated)."""
        print("[Mixer] Started with real-time timeline mixer + Teams source gate (v3.3.3)")
        try:
            from alpha.utils.multidomain_gate_evidence import (
                activate_benchmark_evidence,
                is_multidomain_benchmark_mode,
                record_lifecycle_event,
            )

            if is_multidomain_benchmark_mode():
                activate_benchmark_evidence()
                record_lifecycle_event("capture_started")
        except Exception:
            pass
        mix_start = time.time()
        import numpy as np

        mic_rms_history = []
        mixer = DeepgramTimelineMixer()
        speaker_log_interval = 10.0
        last_speaker_log = mix_start
        speaker_logged_once = False

        def _mic_passes_gate(mic_np):
            rms = float(np.sqrt(np.mean(mic_np.astype(np.float32) ** 2)))
            now = time.time()
            mic_rms_history.append((now, rms))
            mic_rms_history[:] = [
                (t, v) for t, v in mic_rms_history if now - t <= MIC_RMS_ROLLING_WINDOW_S
            ]
            if now - mix_start < MIC_RMS_ROLLING_WINDOW_S:
                rolling_avg = MIC_NOISE_GATE_INITIAL_RMS
            elif mic_rms_history:
                rolling_avg = sum(v for _, v in mic_rms_history) / len(mic_rms_history)
            else:
                rolling_avg = MIC_NOISE_GATE_INITIAL_RMS
            gate_threshold = rolling_avg * 0.2
            return rms > gate_threshold

        wasapi_channels = getattr(self, "_wasapi_channels", 2)
        wasapi_rate = getattr(self, "_wasapi_rate", 48000)
        mic_available = getattr(self, "_mic_stream", None) is not None
        mixer.configure_sources(wasapi_channels, wasapi_rate, mic_available)
        mixer.set_mic_gate(_mic_passes_gate)

        while not self._stop_event.is_set():
            try:
                mixer.ingest_queues(self.sys_audio_queue, self.mic_audio_queue)
                for final_chunk, speaker_meta in mixer.emit_due_frames():
                    if self._stop_event.is_set() or getattr(
                        self, "_dg_stop_sending_audio", False
                    ):
                        continue
                    self._teams_source_gate_summary = mixer.get_source_gate_summary()
                    self._teams_latest_source_snapshot = {
                        "speaker_label": speaker_meta.get("speaker_label"),
                        "chosen_source": speaker_meta.get("chosen_source"),
                        "speaker_detection_method": speaker_meta.get(
                            "speaker_detection_method"
                        ),
                        "decision_reason": speaker_meta.get("decision_reason"),
                        "sys_rms": speaker_meta.get("sys_rms"),
                        "mic_rms": speaker_meta.get("mic_rms"),
                    }
                    self._teams_log_source_energy(speaker_meta)
                    if not speaker_logged_once and speaker_meta.get("speaker_label") != "none":
                        _speaker_ndjson_log(
                            location="main_window.py:audio_mixer_worker",
                            message="[SPEAKER] source detection preserved",
                            data={
                                "system_source_available": speaker_meta.get(
                                    "system_source_available"
                                ),
                                "mic_source_available": speaker_meta.get(
                                    "mic_source_available"
                                ),
                                "speaker_detection_method": speaker_meta.get(
                                    "speaker_detection_method"
                                ),
                                "speaker_label": speaker_meta.get("speaker_label"),
                                "used_pre_mix_audio": True,
                            },
                        )
                        speaker_logged_once = True
                    now = time.time()
                    if now - last_speaker_log >= speaker_log_interval:
                        _speaker_ndjson_log(
                            location="main_window.py:audio_mixer_worker",
                            message="[SPEAKER] source detection preserved",
                            data={
                                "system_source_available": speaker_meta.get(
                                    "system_source_available"
                                ),
                                "mic_source_available": speaker_meta.get(
                                    "mic_source_available"
                                ),
                                "speaker_detection_method": speaker_meta.get(
                                    "speaker_detection_method"
                                ),
                                "speaker_label": speaker_meta.get("speaker_label"),
                                "used_pre_mix_audio": True,
                            },
                        )
                        last_speaker_log = now
                    try:
                        if self._stop_event.is_set() or getattr(
                            self, "_dg_stop_sending_audio", False
                        ):
                            continue
                        try:
                            from alpha.utils.multidomain_gate_evidence import (
                                is_multidomain_benchmark_mode,
                                note_normalized_chunk_queued,
                            )

                            if is_multidomain_benchmark_mode():
                                note_normalized_chunk_queued(
                                    final_chunk, sample_rate=16000, channels=1
                                )
                        except Exception:
                            pass
                        self._audio_q.put(final_chunk, block=True, timeout=0.5)
                        try:
                            from alpha.utils.audio_temp_capture import ingest_audio_chunk

                            ingest_audio_chunk(final_chunk, stream_type="mixed")
                        except Exception:
                            pass
                        try:
                            from alpha.utils.session_progress import touch_progress

                            touch_progress("last_audio_frame_received")
                        except Exception:
                            pass
                    except queue.Full:
                        if self._stop_event.is_set() or getattr(
                            self, "_dg_stop_sending_audio", False
                        ):
                            _session_ndjson_log(
                                location="main_window.py:audio_mixer_worker",
                                message="AUDIO_QUEUE_OVERFLOW_AFTER_STOP_BLOCKED",
                                data={
                                    "queue_size": self._audio_q.qsize()
                                    if self._audio_q is not None
                                    else -1
                                },
                            )
                            try:
                                from alpha.utils.runtime_evidence import (
                                    get_ui_performance_counters,
                                )

                                get_ui_performance_counters().audio_queue_overflow_after_stop_count += 1
                            except Exception:
                                pass
                            continue
                        print("[WARN] Audio queue full, dropping oldest chunk")
                        try:
                            from alpha.utils.runtime_audio_counters import note_audio_queue_drop

                            note_audio_queue_drop()
                        except Exception:
                            pass
                        try:
                            self._audio_q.get_nowait()
                            try:
                                from alpha.utils.multidomain_gate_evidence import (
                                    note_queue_drop_discard_pending,
                                )

                                note_queue_drop_discard_pending()
                            except Exception:
                                pass
                            try:
                                from alpha.utils.multidomain_gate_evidence import (
                                    is_multidomain_benchmark_mode,
                                    note_normalized_chunk_queued,
                                )

                                if is_multidomain_benchmark_mode():
                                    note_normalized_chunk_queued(
                                        final_chunk, sample_rate=16000, channels=1
                                    )
                            except Exception:
                                pass
                            self._audio_q.put(final_chunk, block=True, timeout=0.1)
                        except Exception:
                            pass
                time.sleep(mixer.sleep_until_next_frame())
            except Exception as e:
                print(f"[MIXER ERROR] {e}")
                import traceback
                traceback.print_exc()
        try:
            from alpha.utils.multidomain_gate_evidence import (
                deactivate_benchmark_evidence,
                is_multidomain_benchmark_mode,
                record_lifecycle_event,
            )

            if is_multidomain_benchmark_mode():
                record_lifecycle_event("capture_stopped")
                deactivate_benchmark_evidence()
        except Exception:
            pass

    def _set_listen_button_state(self, listening):
        """Sync Start/Stop Listening button appearance (footer + hamburger menu)."""
        if listening:
            cfg = {
                "text": "Stop Listening",
                "fg_color": COLORS["accent_red"],
                "hover_color": COLORS["accent_red_hover"],
            }
        else:
            cfg = {
                "text": "Start Listening",
                "fg_color": COLORS["accent_blue"],
                "hover_color": COLORS["accent_blue_hover"],
            }
        for btn in (self.listen_button, self.listen_button_menu):
            if btn is not None:
                btn.configure(state="normal", **cfg)
        self._update_status_bar(listening=listening)

    def _set_stopping_ui_state(self):
        """Disable listen controls and show Stopping... while finalize runs in background."""
        if self._waveform_job is not None:
            self.after_cancel(self._waveform_job)
            self._waveform_job = None
        if self._timer_job is not None:
            self.after_cancel(self._timer_job)
            self._timer_job = None
        if self._live_pulse_job is not None:
            self.after_cancel(self._live_pulse_job)
            self._live_pulse_job = None
        if self.live_indicator is not None:
            self.live_indicator.configure(text="○ IDLE", text_color=COLORS["live_idle"])
        if self.live_pill is not None:
            self.live_pill.configure(
                fg_color=COLORS["status_active_bg"],
                border_color=COLORS["border_soft"],
            )
        idle_btn = {
            "text": "Stopping...",
            "state": "disabled",
            "fg_color": COLORS["accent_blue"],
            "hover_color": COLORS["accent_blue_hover"],
        }
        for btn in (self.listen_button, self.listen_button_menu):
            if btn is not None:
                btn.configure(**idle_btn)
        if self.status_text_label is not None:
            self.status_text_label.configure(
                text="Stopping...",
                text_color=COLORS["text_primary"],
            )
        if self.signal_label is not None:
            self.signal_label.configure(text="● Standby", text_color=COLORS["text_muted"])
        self._draw_waveform(idle=True)

    def _set_finalizing_ui_state(self):
        """Disable listen controls and show Finalizing... while flushing Deepgram."""
        if self._waveform_job is not None:
            self.after_cancel(self._waveform_job)
            self._waveform_job = None
        if self._timer_job is not None:
            self.after_cancel(self._timer_job)
            self._timer_job = None
        if self._live_pulse_job is not None:
            self.after_cancel(self._live_pulse_job)
            self._live_pulse_job = None
        if self.live_indicator is not None:
            self.live_indicator.configure(text="○ IDLE", text_color=COLORS["live_idle"])
        if self.live_pill is not None:
            self.live_pill.configure(
                fg_color=COLORS["status_active_bg"],
                border_color=COLORS["border_soft"],
            )
        idle_btn = {
            "text": "Finalizing...",
            "state": "disabled",
            "fg_color": COLORS["accent_blue"],
            "hover_color": COLORS["accent_blue_hover"],
        }
        for btn in (self.listen_button, self.listen_button_menu):
            if btn is not None:
                btn.configure(**idle_btn)
        if self.status_text_label is not None:
            self.status_text_label.configure(
                text="Finalizing...",
                text_color=COLORS["text_primary"],
            )
        if self.signal_label is not None:
            self.signal_label.configure(text="● Standby", text_color=COLORS["text_muted"])
        self._draw_waveform(idle=True)

    def _set_stopped_ui_state(self):
        """Restore idle controls and show Stopped after graceful shutdown."""
        self._set_listen_button_state(False)
        if self.status_text_label is not None:
            self.status_text_label.configure(
                text="Stopped",
                text_color=COLORS["text_secondary"],
            )

    # -----------------------------------------------------------------------
    # DeepL translation (V4)
    # -----------------------------------------------------------------------
    def _initialize_translation(self):
        """Keep translation disabled until DeepL integration is enabled in a later version."""
        self.translation_enabled = False
        self.translation_worker = None
        if self.translated_verse_box is not None:
            self.translated_verse_box._placeholder_text = PLACEHOLDER_TRANSLATION
            self._show_text_placeholder(self.translated_verse_box)

    def submit_text_for_translation(self, text, speaker=None, timestamp=None):
        """No-op until DeepL translation is enabled in a later version."""
        return

    def _on_translation_worker_result(self, result):
        """Marshal translation results onto the UI thread."""
        self._run_on_ui_thread(lambda: self._handle_translation_worker_result(result))

    def _handle_translation_worker_result(self, result):
        """Reserved for future DeepL UI updates; no Translation panel writes in this version."""
        return

    def record_transcript_segment(self, speaker, text, timestamp=None):
        """Legacy hook; transcript store is updated by stabilization mixin."""
        return

    def _record_translation_segment(
        self,
        original_text,
        translated_text,
        speaker=None,
        timestamp=None,
    ):
        """Attach a translation to a stored transcript segment when possible."""
        try:
            self.transcript_store.add_translation(
                original_text=original_text,
                translated_text=translated_text,
                speaker=speaker,
                timestamp=timestamp,
            )
        except Exception as exc:
            logger.debug("Failed to record translation segment: %s", exc)

    def _expand_summary_panel_if_collapsed(self):
        """Show the right-side summary panel when generating a meeting summary."""
        if not self.summary_panel_visible:
            self.show_summary_panel()

    def _set_summary_panel_text(self, text):
        """Replace summary panel body text."""
        if self.summary_body_box is None:
            return
        self.summary_body_box.configure(state="normal")
        self.summary_body_box.delete("1.0", "end")
        self.summary_body_box.insert("1.0", text or "")
        self.summary_body_box.configure(state="disabled")

    def _on_translation_worker_error(self, error_message):
        """Marshal translation errors onto the UI thread."""
        self._run_on_ui_thread(
            lambda: self._handle_translation_worker_error(error_message)
        )

    def _handle_translation_worker_error(self, error_message):
        """Log translation errors without updating the Translation panel."""
        if self.translation_error_shown:
            return
        self.translation_error_shown = True
        logger.warning("Translation error: %s", error_message)

    def _append_translation_result(
        self,
        speaker,
        original_text,
        translated_text,
        timestamp=None,
    ):
        """Insert translated text into the translation panel (UI thread only)."""
        box = self.translated_verse_box
        if box is None:
            return

        self._clear_text_placeholder(box)
        box.configure(state="normal")

        speaker_num = speaker if speaker is not None else 1
        if self.last_translation_speaker is not None and speaker_num != self.last_translation_speaker:
            box.insert(tk.END, "\n\n")

        label = f"[Speaker {speaker_num}] "
        start_idx = box.index(tk.END)
        box.insert(tk.END, label)

        tag_name = f"speaker_{speaker_num}"
        if tag_name not in box.tag_names():
            color = EXTENDED_SPEAKER_COLORS.get(speaker_num, COLORS["text_primary"])
            box.tag_configure(tag_name, foreground=color, font=("Segoe UI", 12, "bold"))

        end_idx = box.index(tk.END)
        box.tag_add(tag_name, start_idx, end_idx)
        box.insert(tk.END, (translated_text or "").strip() + "\n", "body")
        box.configure(state="disabled")
        self.last_translation_speaker = speaker_num
        box.see(tk.END)
        if hasattr(box, "_scrollbar"):
            self.check_scrollbar_visibility(box, box._scrollbar)
        self._record_translation_segment(
            original_text,
            translated_text,
            speaker=speaker,
            timestamp=timestamp,
        )

    # -----------------------------------------------------------------------
    # Event bus (V2 — conservative integration)
    # -----------------------------------------------------------------------
    def _setup_event_subscriptions(self):
        """Wire EventBus handlers; UI updates are marshalled to the main thread."""
        self.event_bus.subscribe(EventType.APP_STATUS_CHANGED, self._on_app_status_changed)
        self.event_bus.subscribe(EventType.LISTENING_STARTED, self._on_listening_started)
        self.event_bus.subscribe(EventType.LISTENING_STOPPED, self._on_listening_stopped)
        self.event_bus.subscribe(EventType.TRANSCRIPT_RECEIVED, self._on_transcript_received)
        self.event_bus.subscribe(EventType.TRANSLATION_STARTED, self._on_translation_started)
        self.event_bus.subscribe(EventType.TRANSLATION_RECEIVED, self._on_translation_received)
        self.event_bus.subscribe(EventType.TRANSLATION_ERROR, self._on_translation_error)
        self.event_bus.subscribe(EventType.SPEAKER_DETECTED, self._on_speaker_detected)
        self.event_bus.subscribe(EventType.ERROR_OCCURRED, self._on_error_occurred)
        self.event_bus.subscribe(EventType.SUMMARY_UPDATED, self._on_summary_updated)

    def _run_on_ui_thread(self, callback):
        """Schedule a callable on the Tk main loop (safe from worker threads)."""
        from alpha.utils.ui_thread_guard import is_ui_main_thread

        try:
            if is_ui_main_thread():
                self.after(0, callback)
            else:
                from alpha.utils.ui_event_bus import get_ui_event_bus

                get_ui_event_bus().post_schedule_after(0, callback)
        except Exception as exc:
            logger.error("Failed to schedule UI callback: %s", exc)

    def publish_transcript_event(
        self,
        text,
        speaker=None,
        timestamp=None,
        is_final=True,
        queue_item=None,
    ):
        """Publish transcript event and enqueue for existing process_ui_queue path."""
        try:
            from alpha.utils.live_runtime_metrics import note_ui_event_posted

            note_ui_event_posted()
        except Exception:
            pass
        if queue_item is not None:
            self.transcript_queue.put(queue_item)
            self._transcript_events_posted += 1
        elif speaker is not None:
            item = {
                "speaker": int(speaker) if str(speaker).isdigit() else speaker,
                "text": text,
                "is_final": is_final,
            }
            self.transcript_queue.put(item)
            self._transcript_events_posted += 1

        payload = TranscriptEvent(
            text=text,
            speaker=str(speaker) if speaker is not None else None,
            timestamp=timestamp,
            is_final=is_final,
        )
        self.event_bus.publish(EventType.TRANSCRIPT_RECEIVED, payload)

    def publish_translation_event(
        self,
        original_text,
        translated_text,
        source_language=None,
        target_language=None,
        speaker=None,
        timestamp=None,
        error_message=None,
    ):
        """Publish translation result event."""
        payload = TranslationEvent(
            original_text=original_text,
            translated_text=translated_text,
            source_language=source_language or self.source_language.get(),
            target_language=target_language or self.target_language.get(),
            speaker=str(speaker) if speaker is not None else None,
            timestamp=timestamp,
            error_message=error_message,
        )
        self.event_bus.publish(EventType.TRANSLATION_RECEIVED, payload)

    def publish_status_event(self, status, message=None):
        """Publish application/session status change."""
        payload = StatusEvent(status=status, message=message)
        self.event_bus.publish(EventType.APP_STATUS_CHANGED, payload)

    def publish_error_event(self, message, source=None, recoverable=True):
        """Publish backend error without blocking audio/transcription threads."""
        payload = ErrorEvent(message=message, source=source, recoverable=recoverable)
        self.event_bus.publish(EventType.ERROR_OCCURRED, payload)

    def _on_app_status_changed(self, payload: StatusEvent):
        """Update status bar message on the UI thread."""
        if not isinstance(payload, StatusEvent):
            return

        def update():
            if self.status_text_label is not None and payload.message:
                self.status_text_label.configure(text=payload.message)

        self._run_on_ui_thread(update)

    def _on_listening_started(self, payload: StatusEvent):
        logger.info("Listening started: %s", getattr(payload, "message", ""))

    def _on_listening_stopped(self, payload: StatusEvent):
        logger.info("Listening stopped: %s", getattr(payload, "message", ""))

    def _on_transcript_received(self, payload: TranscriptEvent):
        """Log transcript events; display still handled by process_ui_queue (V2)."""
        if not isinstance(payload, TranscriptEvent):
            return
        logger.debug(
            "Transcript received (speaker=%s, final=%s): %s",
            payload.speaker,
            payload.is_final,
            payload.text[:80] if payload.text else "",
        )
        # TODO V3: Move transcript display from process_ui_queue to this handler.

    def _on_translation_started(self, payload: TranslationEvent):
        if not isinstance(payload, TranslationEvent):
            return
        logger.debug(
            "Translation started (%s -> %s): %s",
            payload.source_language,
            payload.target_language,
            payload.original_text[:80] if payload.original_text else "",
        )

    def _on_translation_received(self, payload: TranslationEvent):
        """Log translation events; display handled by _append_translation_result."""
        if not isinstance(payload, TranslationEvent):
            return
        logger.debug(
            "Translation received (speaker=%s): %s",
            payload.speaker,
            payload.translated_text[:80] if payload.translated_text else "",
        )

    def _on_translation_error(self, payload: TranslationEvent):
        if not isinstance(payload, TranslationEvent):
            return
        logger.warning(
            "Translation error event: %s",
            payload.error_message or "unknown",
        )

    def _on_speaker_detected(self, payload):
        logger.debug("Speaker detected: %s", payload)

    def _on_error_occurred(self, payload: ErrorEvent):
        if not isinstance(payload, ErrorEvent):
            return
        logger.error("[%s] %s", payload.source or "app", payload.message)
        if not payload.recoverable:
            self._run_on_ui_thread(
                lambda: messagebox.showerror("Error", payload.message)
            )

    def _on_summary_updated(self, payload):
        logger.info("Summary updated event received")

    def toggle_listening(self):
        """Start or stop live transcription."""
        if getattr(self, "_is_finalizing", False):
            return
        if getattr(self, "_starting_listening", False):
            return
        if self.is_listening:
            self._begin_graceful_stop()
        else:
            self._start_listening()

    def _log_startup_diagnostics(self):
        """Print safe startup diagnostics without exposing full API keys."""
        env_path = PROJECT_ROOT / ".env"
        key_status = get_deepgram_key_status()
        print("[Startup] cwd=", os.getcwd())
        print("[Startup] project_root=", PROJECT_ROOT)
        print("[Startup] .env detected=", env_path.is_file(), f"({env_path})")
        print(f"[Startup] Deepgram key status: {key_status}")
        if key_status == "configured":
            key = (DEEPGRAM_API_KEY or "").strip()
            if len(key) > 8:
                print(f"[Startup] Deepgram key mask: {key[:4]}...{key[-4:]}")
            else:
                print("[Startup] Deepgram key mask: (configured)")

    def _set_starting_status(self):
        """Show immediate Connecting feedback while Deepgram/audio initialize off-UI."""
        if self.status_text_label is not None:
            self.status_text_label.configure(
                text="Connecting…",
                text_color=COLORS["text_primary"],
            )

    def _start_listening(self):
        """Validate config on UI thread; open audio/STT on a background worker."""
        if getattr(self, "_starting_listening", False):
            return
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_BUTTON_CLICKED")
            jp_accuracy_log("START_CALLBACK_ENTER")
            jp_accuracy_log("START_PRECHECK_BEGIN")
        except Exception:
            pass

        key_status = get_deepgram_key_status()
        if key_status == "missing":
            print(MISSING_API_KEY_MSG)
            messagebox.showerror("Deepgram API Key", MISSING_API_KEY_MSG)
            self.publish_error_event(
                MISSING_API_KEY_MSG,
                source="config",
                recoverable=True,
            )
            return
        if key_status == "placeholder":
            print(PLACEHOLDER_API_KEY_MSG)
            messagebox.showerror("Deepgram API Key", PLACEHOLDER_API_KEY_MSG)
            self.publish_error_event(
                PLACEHOLDER_API_KEY_MSG,
                source="config",
                recoverable=True,
            )
            return

        dropdown_lang = self._strip_language_flag(self.source_language.get())
        profile = self._build_language_profile(dropdown_lang)
        if not profile.get("selection_supported", True):
            reason = profile.get("unsupported_reason") or "unsupported_language_profile"
            message = (
                "Selected language profile is not supported by current Deepgram mapping.\n"
                f"Selection: {dropdown_lang}\nReason: {reason}"
            )
            print(f"[LANGUAGE] unsupported profile: {reason} ({dropdown_lang})")
            messagebox.showerror("Language Profile", message)
            self.publish_error_event(message, source="language", recoverable=True)
            return

        deepgram_lang = self._resolve_deepgram_language(dropdown_lang)
        self._listen_language = deepgram_lang
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_PRECHECK_DONE", language=deepgram_lang)
            jp_accuracy_log("START_RUN_FOLDER_READY")
            jp_accuracy_log("START_WRITER_REBIND_SAFE_MODE")
            jp_accuracy_log("VALIDATION_DEFERRED_UNTIL_AFTER_STOP")
            jp_accuracy_log("UPLOAD_PACKAGE_DEFERRED_UNTIL_AFTER_STOP")
        except Exception:
            pass
        self._starting_listening = True
        self._set_starting_status()
        perf_checkpoint("start_listening_clicked")

        def worker():
            error = None
            try:
                self._start_listening_worker(dropdown_lang, deepgram_lang)
            except Exception as exc:
                error = exc
            self._run_on_ui_thread(lambda: self._finish_start_listening(error))

        threading.Thread(target=worker, name="StartListening", daemon=True).start()

    def _start_listening_worker(self, dropdown_lang: str, deepgram_lang: str):
        """Heavy audio device scan, WASAPI/mic open, and Deepgram worker startup."""
        try:
            from alpha.utils.run_identity import init_live_run_from_host

            self._run_identity = init_live_run_from_host(self)
        except Exception:
            self._run_identity = None
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_AUDIO_INIT_BEGIN")
        except Exception:
            pass
        self._log_startup_diagnostics()
        self._print_accuracy_startup(deepgram_lang)
        self.log_deepgram_language_config()
        print(
            f"Listening to dropdown: '{dropdown_lang}' -> Deepgram code: '{deepgram_lang}'"
        )
        perf_checkpoint("deepgram_client_initialized")

        self._stop_event.clear()
        self.reset_graceful_stop_state()
        self._transcript_events_posted = 0
        self._transcript_events_drained = 0
        self._audio_q = queue.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
        self.sys_audio_queue = queue.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
        self.mic_audio_queue = queue.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
        with self._speaker_lock:
            self._last_assigned_speaker = 1
            self._last_speaker_change_time = 0.0
        self.last_transcript_hash = set()
        self._transcript_hash_order = []
        self._last_speaker_utterance = {}
        self._recent_displayed_texts = []
        self.last_speaker = None
        self.last_speaker_id = None
        self.current_speaker = None
        self.last_displayed_speaker = None
        self.last_speech_time = 0.0
        self.fallback_speaker = 1
        self._chunks_sent_count = 0
        self._transcripts_received = 0
        self.reset_transcript_stability_state()
        self._health_no_transcript_hint_shown = False
        self._dg_backoff_seconds = 1.0
        self._dg_replay_buffer = []
        self._dg_awaiting_transcript_reset = False
        self.reset_latency_session_state()
        self._reset_interim_tail_state()
        self._reset_meeting_segment_buffer_state()
        self._reset_segment_repair_state()

        # V26.5.1: connect Deepgram sender BEFORE starting capture/mixer so audio
        # is never queued (and never backpressure-dropped) while Connecting.
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_DEEPGRAM_INIT_BEGIN")
        except Exception:
            pass
        self._latency_sender_loop_alive = False
        self._dg_thread = threading.Thread(
            target=self._deepgram_worker, daemon=True
        )
        self._dg_thread.start()
        print("Deepgram worker thread started")
        sender_deadline = time.perf_counter() + 30.0
        while time.perf_counter() < sender_deadline:
            if bool(getattr(self, "_latency_sender_loop_alive", False)):
                break
            if self._stop_event.is_set():
                raise RuntimeError("Deepgram connection aborted before sender ready")
            time.sleep(0.05)
        else:
            raise RuntimeError("Deepgram sender not ready within 30s")
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_DEEPGRAM_SENDER_READY")
        except Exception:
            pass
        perf_checkpoint("deepgram_sender_ready")

        print("Starting dual audio capture (WASAPI loopback + microphone)...")
        try:
            self._start_wasapi_loopback()
        except Exception as exc:
            print(f"Audio stream error: {exc}")
            raise
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_AUDIO_INIT_DONE")
        except Exception:
            pass
        perf_checkpoint("audio_devices_scanned")
        _system_audio_only = False
        try:
            from alpha.utils.issue12_stage1_runtime import (
                build_benchmark_audio_source_record,
                is_system_audio_only_benchmark,
            )
            from alpha.utils.accuracy_stage_capture import write_benchmark_audio_source_record
            from alpha.utils.run_identity import get_run_id

            _system_audio_only = bool(is_system_audio_only_benchmark())
            if _system_audio_only:
                record = build_benchmark_audio_source_record(
                    run_id=str(get_run_id() or ""),
                    system_audio_enabled=True,
                    microphone_mix_enabled=False,
                    benchmark_mode=True,
                    audio_format="linear16",
                    sample_rate=16000,
                    channels=1,
                )
                write_benchmark_audio_source_record(record)
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "BENCHMARK_AUDIO_SOURCE_ACTIVE",
                        **record,
                    )
                except Exception:
                    pass
                print(
                    "[Benchmark] system_audio_only active — WASAPI only; microphone mix disabled"
                )
            else:
                # Explicit normal-mode record only when Stage 1 benchmark env is absent.
                pass
        except Exception:
            _system_audio_only = False
        if not _system_audio_only:
            try:
                self._start_microphone_capture()
            except Exception as exc:
                print(
                    f"Microphone capture unavailable, continuing with system audio only: {exc}"
                )
        self._mix_thread = threading.Thread(
            target=self.audio_mixer_worker, daemon=True
        )
        self._mix_thread.start()
        print("Audio mix worker thread started")
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_PIPELINE_WORKERS_BEGIN")
            from alpha.utils.language_pipeline_worker import start_language_pipeline_worker

            start_language_pipeline_worker()
            jp_accuracy_log("START_PIPELINE_WORKERS_DONE")
        except Exception:
            pass

    def _finish_start_listening(self, error):
        """Marshal listening startup result back onto the UI thread."""
        self._starting_listening = False
        if error is not None:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("START_FAILURE_DETECTED")
                jp_accuracy_log("START_FAILURE_STEP", step="start_worker")
                jp_accuracy_log("START_FAILURE_EXCEPTION_TYPE", value=type(error).__name__)
                jp_accuracy_log("START_FAILURE_EXCEPTION_MESSAGE", value=str(error))
                self._write_startup_failure_summary("start_worker", error)
                jp_accuracy_log("STARTUP_FAILURE_SUMMARY_WRITTEN")
                jp_accuracy_log("START_CALLBACK_EXIT")
            except Exception:
                pass
            print(f"Error starting listening: {error}")
            self._stop_listening(graceful=False)
            if self.status_text_label is not None:
                self.status_text_label.configure(
                    text="Stopped",
                    text_color=COLORS["text_secondary"],
                )
            return

        self.is_listening = True
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_LISTENING_STATE_TRUE")
        except Exception:
            pass
        try:
            from alpha.utils.tk_thread_guard import set_tk_guard_session_active

            set_tk_guard_session_active(True)
        except Exception:
            pass
        self._set_listen_button_state(True)
        self._start_health_monitor()
        try:
            from alpha.utils.component_stall_classifier import reset_stall_classification
            from alpha.utils.flight_recorder import record_flight_event
            from alpha.utils.partial_autosave_worker import start_partial_autosave_worker
            from alpha.utils.run_identity import get_current_run_identity
            from alpha.utils.session_progress import mark_listening_started
            from alpha.utils.session_watchdog import start_session_watchdog
            from alpha.utils.thread_dump import run_thread_dump_selftest
            from alpha.utils.transcript_snapshot_store import reset_transcript_snapshot_store

            reset_transcript_snapshot_store()
            reset_stall_classification()
            mark_listening_started()
            start_session_watchdog(self)
            start_partial_autosave_worker(self)
            record_flight_event("listen_started", host=self, force=True)
            identity = get_current_run_identity()
            if identity is not None:
                # Startup-safe mode: avoid heavy artifact folder setup on UI thread.
                try:
                    from alpha.utils.troubleshooting_paths import get_active_run_folder

                    folder = get_active_run_folder()
                    if folder is not None:
                        run_thread_dump_selftest(folder)
                except Exception:
                    pass
            self._start_japanese_pipeline_heartbeat_loop()
        except Exception:
            pass
        self.publish_status_event("listening", "Session started")
        self.event_bus.publish(
            EventType.LISTENING_STARTED,
            StatusEvent(status="listening", message="Session started"),
        )
        print(f"Listening started (language={self._listen_language})")
        try:
            from alpha.utils.async_debug_log import log_runtime_debug_event

            log_runtime_debug_event(
                "START_LISTENING",
                language=self._listen_language,
            )
        except Exception:
            pass
        self._session_log(
            "[SESSION] listening started",
            {
                "language": self._listen_language,
                "timestamp": int(time.time() * 1000),
            },
        )
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_CALLBACK_EXIT")
        except Exception:
            pass

    def _write_startup_failure_summary(self, failed_step: str, exc: Exception) -> None:
        try:
            from alpha.utils.troubleshooting_paths import get_active_run_folder

            folder = get_active_run_folder()
            if folder is None:
                return
            path = folder / "artifacts" / "STARTUP_FAILURE_SUMMARY.json"
            payload = {
                "run_id": getattr(getattr(self, "_run_identity", None), "run_id", ""),
                "app_version": APP_VERSION,
                "failed_step": failed_step,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "listening_state": bool(self.is_listening),
                "audio_started": bool(getattr(self, "_mix_thread", None)),
                "deepgram_started": bool(getattr(self, "_dg_thread", None)),
                "pipeline_workers_started": bool(getattr(self, "_mix_thread", None) and getattr(self, "_dg_thread", None)),
                "evidence_safe_mode": True,
                "pending_writer_warnings": [],
                "recovery_recommendation": "Retry Start; if failure repeats upload startup diagnostics bundle.",
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _begin_graceful_stop(self):
        """Non-blocking stop: UI returns immediately; finalize runs in background worker."""
        from alpha.utils.stop_finalize_worker import begin_stop_from_ui
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            self.stop_core_completed_event = threading.Event()
            self.stop_ui_restored_event = threading.Event()
            self.stop_started_at = time.monotonic()
            self.stop_ui_watchdog_active = True
            jp_accuracy_log("STOP_UI_WATCHDOG_STARTED")
            self._start_stop_ui_watchdog()
        except Exception:
            pass

        begin_stop_from_ui(self)

    def _start_stop_ui_watchdog(self) -> None:
        try:
            if not bool(getattr(self, "stop_ui_watchdog_active", False)):
                return
            self.after(150, self._stop_ui_watchdog_tick)
        except Exception:
            pass

    def _stop_ui_watchdog_tick(self) -> None:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            if not bool(getattr(self, "stop_ui_watchdog_active", False)):
                return
            core_done = bool(
                hasattr(self, "stop_core_completed_event")
                and self.stop_core_completed_event.is_set()
            )
            if core_done:
                jp_accuracy_log("STOP_UI_WATCHDOG_CORE_COMPLETED_DETECTED")
                self._restore_ui_after_stop_watchdog(timed_out=False)
                return
            started = float(getattr(self, "stop_started_at", time.monotonic()))
            if (time.monotonic() - started) >= 5.0:
                jp_accuracy_log("STOP_UI_FORCE_RESTORE_AFTER_TIMEOUT")
                self._restore_ui_after_stop_watchdog(timed_out=True)
                return
            self.after(150, self._stop_ui_watchdog_tick)
        except Exception:
            pass

    def _restore_ui_after_stop_watchdog(self, *, timed_out: bool) -> None:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            self.stop_ui_watchdog_active = False
            self._set_stopped_ui_state()
            self._is_finalizing = False
            self._is_stopping = False
            self._stop_finalize_started = False
            if timed_out and self.status_text_label is not None:
                self.status_text_label.configure(
                    text="Stopped. Diagnostics may still be saving.",
                    text_color=COLORS["text_secondary"],
                )
            if hasattr(self, "stop_ui_restored_event"):
                self.stop_ui_restored_event.set()
            jp_accuracy_log("STOP_UI_RESTORE_EXECUTED_ON_UI_THREAD")
            jp_accuracy_log("STOP_UI_RESTORE_CONFIRMED")
        except Exception:
            pass

    def _request_ui_transcript_queue_flush(self, timeout_seconds=1.0):
        """Block until pending transcript queue items are processed on the UI thread."""
        from alpha.utils.ui_thread_guard import is_ui_main_thread

        if is_ui_main_thread():
            self._flush_pending_transcript_queue()
            return
        done = threading.Event()

        def _flush():
            try:
                self._flush_pending_transcript_queue()
            finally:
                done.set()

        from alpha.utils.ui_event_bus import get_ui_event_bus

        get_ui_event_bus().post_schedule_after(0, _flush)
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "STOP_WORKER_TK_CALL_REROUTED_TO_UI_EVENT_BUS",
                operation="stop_ui_flush",
            )
        except Exception:
            pass
        done.wait(timeout=max(0.1, float(timeout_seconds)))

    def _stop_health_monitor_ui_safe(self):
        """Stop health monitor without Tk calls from background threads."""
        from alpha.utils.ui_thread_guard import is_ui_main_thread

        if is_ui_main_thread():
            self._stop_health_monitor()
            return
        from alpha.utils.ui_event_bus import get_ui_event_bus

        get_ui_event_bus().post("deepgram_close_ui_cleanup_requested", {})
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("DEEPGRAM_CLOSE_UI_CLEANUP_REROUTED")
            jp_accuracy_log("AFTER_CANCEL_REROUTED_TO_UI_THREAD")
        except Exception:
            pass

    def drain_transcript_queue_for_stop(self) -> dict[str, int]:
        """Bounded drain helper for Stop UI barrier — main thread only."""
        drained = 0
        while not self.transcript_queue.empty():
            try:
                item = self.transcript_queue.get_nowait()
            except Exception:
                break
            if isinstance(item, list):
                for sub_item in item:
                    self._enqueue_transcript_ui_batch(sub_item)
                    drained += 1
                    self._transcript_events_drained += 1
            else:
                self._enqueue_transcript_ui_batch(item)
                drained += 1
                self._transcript_events_drained += 1
        self._flush_transcript_ui_batch(force=True)
        remaining = self.transcript_queue.qsize() + len(self._transcript_ui_batch_buffer)
        return {"drained": drained, "remaining": remaining}

    def _flush_pending_transcript_queue(self):
        """Process any final transcript items still queued after graceful stop."""
        try:
            self._flush_transcript_ui_batch(force=True)
            while not self.transcript_queue.empty():
                item = self.transcript_queue.get_nowait()
                if isinstance(item, list):
                    for sub_item in item:
                        self._enqueue_transcript_ui_batch(sub_item)
                        self._transcript_events_drained += 1
                else:
                    self._enqueue_transcript_ui_batch(item)
                    self._transcript_events_drained += 1
            self._flush_transcript_ui_batch(force=True)
        except Exception as exc:
            print(f"[STOP] transcript queue flush error: {exc}")

    def _finish_graceful_stop(self, timed_out=False):
        """Lightweight UI-thread completion after background stop finalization."""
        try:
            from alpha.utils.tk_thread_guard import set_tk_guard_session_active

            set_tk_guard_session_active(False)
        except Exception:
            pass
        try:
            self._flush_pending_transcript_queue()
            self._audio_q = None
            self.sys_audio_queue = None
            self.mic_audio_queue = None
            self._is_finalizing = False
            self._is_stopping = False
            self._stop_finalize_started = False
            self._dg_receiver_allowed = False
            print("[STOP] receiver disabled after finalize")
            self._set_stopped_ui_state()
            if hasattr(self, "stop_ui_restored_event"):
                self.stop_ui_restored_event.set()
            self.publish_status_event("idle", "Session stopped")
            self.event_bus.publish(
                EventType.LISTENING_STOPPED,
                StatusEvent(status="idle", message="Session stopped"),
            )
            if timed_out:
                print("Graceful stop timed out; socket closed safely.")
            print("Listening stopped")
            self._session_log(
                "[SESSION] listening stopped",
                {"timestamp": int(time.time() * 1000)},
            )
            self._teams_log_source_gate_summary()
            self._segment_repair_log_summary()
            self._log_language_summary()
            if MEETING_SEGMENT_BUFFER_ENABLED:
                self._meeting_buffer_log_summary()
            stop_result = getattr(self, "_last_graceful_stop_result", {}) or {}
            clean_text = ""
            store_segment_count = None
            if hasattr(self, "transcript_store") and self.transcript_store is not None:
                clean_text = self._get_clean_transcript_for_copy_export()
                store_segment_count = self.transcript_store.segment_count()
            stop_snapshot = {
                "finalized": bool(stop_result.get("finalized")),
                "closed": bool(stop_result.get("closed")),
            }
            if store_segment_count is not None:
                stop_snapshot["store_segment_count"] = store_segment_count
            last_preview = getattr(self, "_diag_last_committed_preview", None)
            if last_preview:
                stop_snapshot["last_committed_preview"] = last_preview
            if clean_text:
                stop_snapshot["ending_preview"] = clean_text[-220:]
                stop_snapshot["clean_word_count"] = len(clean_text.split())
            _diag_ndjson_log(
                location="main_window.py:_finish_graceful_stop",
                message="[DIAG] graceful stop final transcript snapshot",
                data=stop_snapshot,
            )
            _session_ndjson_log(
                location="main_window.py:_finish_graceful_stop",
                message="[JAPANESE] final transcript snapshot",
                data={
                    "clean_char_count": len(clean_text or ""),
                    "ending_preview": stop_snapshot.get("ending_preview", ""),
                },
            )
            try:
                from alpha.utils.session_watchdog import stop_session_watchdog

                stop_session_watchdog()
            except Exception:
                pass
        except Exception as exc:
            print(f"Error finishing graceful stop: {exc}")

    def _stop_listening(self, graceful=True):
        """Close audio stream and Deepgram WebSocket (graceful by default)."""
        if graceful:
            if getattr(self, "_is_finalizing", False):
                return
            if self.is_listening:
                self._begin_graceful_stop()
                return
        self._stop_listening_immediate()

    def _stop_listening_immediate(self):
        """Immediately tear down audio and Deepgram without finalize."""
        try:
            self._stop_event.set()
            self.is_listening = False
            self._is_finalizing = False
            self._dg_stop_sending_audio = True

            self._stop_health_monitor()

            self._close_wasapi_stream()
            self._close_microphone_stream()

            if self._dg_ws is not None:
                try:
                    self._dg_ws.close()
                except Exception:
                    pass
                self._dg_ws = None

            self._audio_q = None
            self.sys_audio_queue = None
            self.mic_audio_queue = None
            self._set_stopped_ui_state()
            self.publish_status_event("idle", "Session stopped")
            self.event_bus.publish(
                EventType.LISTENING_STOPPED,
                StatusEvent(status="idle", message="Session stopped"),
            )
            print("Listening stopped")
        except Exception as exc:
            print(f"Error stopping listening: {exc}")

    def _append_initial_transcript(self, text):
        """Insert a speaker-tagged line into the Initial verse box (UI thread only)."""
        box = self.initial_verse_box
        if box is None:
            print("Warning: initial_verse_box is None, cannot insert text")
            return

        self._clear_text_placeholder(box)
        box.configure(state="normal")

        match = re.match(r"^(\[Speaker (\d+)\])(.*)$", text, re.DOTALL)
        if match:
            label = match.group(1)
            speaker_num = int(match.group(2))
            body = match.group(3)
            tag = f"speaker_{speaker_num}" if 1 <= speaker_num <= 4 else "body"
            box.insert(tk.END, label, tag)
            box.insert(tk.END, body, "body")
        else:
            box.insert(tk.END, text, "body")

        box.configure(state="disabled")
        self.check_scrollbar_visibility(box, box._scrollbar)
        box.see(tk.END)

    def _on_close(self):
        """Clean up audio/WebSocket resources before closing the window."""
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("APP_CLOSE_REQUESTED")
        except Exception:
            pass
        if getattr(self, "_window_close_pending", False):
            self.destroy()
            return
        listening = bool(self.is_listening) or bool(getattr(self, "_is_stopping", False))
        try:
            from alpha.utils.flight_recorder import flush_flight_recorder, record_flight_event
            from alpha.utils.session_progress import build_progress_payload
            from alpha.utils.session_watchdog import get_last_ui_heartbeat_mono

            payload = build_progress_payload(self)
            ui_stale = False
            last_hb = get_last_ui_heartbeat_mono()
            if last_hb > 0:
                import time as _time

                ui_stale = (_time.monotonic() - last_hb) > 10.0
            record_flight_event(
                "manual_window_close_requested",
                host=self,
                force=True,
                listening=listening,
                ui_heartbeat_stale=ui_stale,
            )
            jp_accuracy_log = None
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log as _jp

                jp_accuracy_log = _jp
                jp_accuracy_log(
                    "WINDOW_CLOSE_REQUESTED",
                    listening=listening,
                    ui_heartbeat_stale=ui_stale,
                    **{k: payload.get(k) for k in ("ui_heartbeat_age_ms", "internal_stable_commit_count")},
                )
            except Exception:
                pass
            flush_flight_recorder()
        except Exception:
            pass
        if listening:
            self._window_close_pending = True
            try:
                from alpha.utils.freeze_guard_log import freeze_guard_log_sync
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log
                from alpha.utils.run_artifacts import write_partial_alpha_output_from_snapshot

                jp_accuracy_log("WINDOW_CLOSE_REQUESTED_DURING_LISTENING")
                freeze_guard_log_sync("WINDOW_CLOSE_REQUESTED_DURING_LISTENING")
                write_partial_alpha_output_from_snapshot(reason="window_close", host=self)
                freeze_guard_log_sync("PARTIAL_ALPHA_OUTPUT_WRITTEN_ON_WINDOW_CLOSE")
            except Exception:
                pass
            try:
                from alpha.transcription.japanese_final_chunk_stabilizer import (
                    get_japanese_final_stabilizer,
                    should_use_japanese_final_stabilizer,
                )

                if should_use_japanese_final_stabilizer(self):
                    get_japanese_final_stabilizer(self).set_accepting(False)
            except Exception:
                pass
            if self.is_listening:
                try:
                    from alpha.utils.freeze_guard_log import freeze_guard_log_sync
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log("WINDOW_CLOSE_SAFE_STOP_STARTED")
                    freeze_guard_log_sync("WINDOW_CLOSE_SAFE_STOP_STARTED")
                except Exception:
                    pass
                self._begin_graceful_stop()

                def _wait_then_close():
                    import time as _time

                    deadline = _time.monotonic() + 12.0
                    while _time.monotonic() < deadline:
                        if not bool(getattr(self, "_is_stopping", False)) and not bool(
                            getattr(self, "_is_finalizing", False)
                        ):
                            try:
                                from alpha.utils.freeze_guard_log import freeze_guard_log_sync
                                from alpha.utils.japanese_accuracy_log import jp_accuracy_log
                                from alpha.utils.run_artifacts import (
                                    autosave_partial_artifacts,
                                    write_crash_safe_index,
                                )

                                jp_accuracy_log("WINDOW_CLOSE_SAFE_STOP_COMPLETED")
                                freeze_guard_log_sync("WINDOW_CLOSE_SAFE_STOP_COMPLETED")
                            except Exception:
                                pass
                            self._shutdown_and_destroy()
                            return
                        _time.sleep(0.25)
                    try:
                        from alpha.utils.freeze_guard_log import freeze_guard_log_sync
                        from alpha.utils.japanese_accuracy_log import jp_accuracy_log
                        from alpha.utils.run_artifacts import (
                            autosave_partial_artifacts_background,
                            write_crash_safe_index,
                        )

                        autosave_partial_artifacts_background(
                            reason="window_close", host=self
                        )
                        write_crash_safe_index(
                            status="incomplete_hang_suspected",
                            reason="window_close_timeout",
                        )
                        jp_accuracy_log("WINDOW_CLOSE_FORCED_AFTER_TIMEOUT")
                        freeze_guard_log_sync("WINDOW_CLOSE_FORCED_AFTER_TIMEOUT")
                    except Exception:
                        pass
                    self._shutdown_and_destroy()

                threading.Thread(
                    target=_wait_then_close, name="WindowCloseWait", daemon=True
                ).start()
                return
        try:
            from alpha.utils.crash_guard_log import handle_crash_event
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            if getattr(self, "_starting_listening", False):
                jp_accuracy_log("START_FAILED_USER_CLOSED_CLASSIFIED")
                handle_crash_event("START_FAILED_USER_CLOSED_CLASSIFIED", host=self)
            else:
                jp_accuracy_log("CLOSED_BEFORE_START_CLASSIFIED")
                handle_crash_event("CLOSED_BEFORE_START_CLASSIFIED", host=self)
        except Exception:
            pass
        self._shutdown_and_destroy()

    def _shutdown_and_destroy(self):
        """Final shutdown after safe stop or when idle."""
        try:
            from alpha.utils.stop_finalize_worker import request_evidence_package_cancel_on_exit
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("APP_CLOSE_DURING_EVIDENCE_PACKAGE")
            request_evidence_package_cancel_on_exit()
            jp_accuracy_log("CLOSE_COMPLETED_WITH_EVIDENCE_WARNING")
            jp_accuracy_log("APP_CLOSE_NON_BLOCKING")
            jp_accuracy_log("APP_CLOSE_ALLOWED")
        except Exception:
            pass
        self._stop_ui_loops()
        if self.is_listening:
            self._stop_listening(graceful=False)
        if self.translation_worker is not None:
            self.translation_worker.stop()
            self.translation_worker = None
        try:
            from alpha.utils.async_debug_log import shutdown_async_debug_logging

            shutdown_async_debug_logging()
        except Exception:
            pass
        self.destroy()

    # -----------------------------------------------------------------------
    # Speaker-colored text helpers
    # -----------------------------------------------------------------------
    def _insert_formatted_text(self, text_widget, content):
        """Insert text with colored [Speaker N] tags into a read-only tk.Text widget."""
        self._clear_text_placeholder(text_widget)
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")

        parts = re.split(r"(\[Speaker \d+\])", content)
        for part in parts:
            if not part:
                continue

            match = re.fullmatch(r"\[Speaker (\d+)\]", part)
            if match:
                speaker_num = int(match.group(1))
                tag = f"speaker_{speaker_num}" if 1 <= speaker_num <= 4 else "body"
                text_widget.insert("end", part, tag)
            else:
                text_widget.insert("end", part, "body")

        text_widget.configure(state="disabled")
        self.check_scrollbar_visibility(text_widget, text_widget._scrollbar)

    # -----------------------------------------------------------------------
    # Context menu
    # -----------------------------------------------------------------------
    def _create_context_menu(self):
        """Right-click context menu to clear both text areas."""
        self.context_menu = Menu(self, tearoff=0)
        self.context_menu.add_command(label="Clear All Text", command=self.clear_text)

        for box in (self.initial_verse_box, self.translated_verse_box):
            box.bind("<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event):
        """Display the context menu at the cursor position."""
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    # -----------------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------------
    def toggle_always_on_top(self):
        """Toggle window always-on-top and keep both switches in sync."""
        try:
            if self._compact_mode and self._menu_visible:
                is_on = self.always_on_top_switch_menu.get() == 1
            else:
                is_on = self.always_on_top_switch.get() == 1

            self.attributes("-topmost", is_on)

            if is_on:
                self.always_on_top_switch.select()
                self.always_on_top_switch_menu.select()
            else:
                self.always_on_top_switch.deselect()
                self.always_on_top_switch_menu.deselect()

            print(f"Always on Top: {'ON' if is_on else 'OFF'}")
        except Exception as exc:
            print(f"Error toggling always on top: {exc}")
            messagebox.showerror("Error", f"Could not update window state:\n{exc}")

    def show_meeting_summary(self):
        """Toggle right-side summary panel; refresh summary text when opening."""
        try:
            if self.summary_panel_visible:
                self.hide_summary_panel()
                return
            self.show_summary_panel()
            summary_text = self.summary_service.generate_summary_from_store(
                self.transcript_store
            )
            self._set_summary_panel_text(summary_text)
            self.event_bus.publish(EventType.SUMMARY_UPDATED, summary_text)
            print("Meeting summary updated.")
        except Exception as exc:
            logger.error("Error generating meeting summary: %s", exc)
            print(f"Error showing meeting summary: {exc}")

    def swap_languages(self):
        """Swap source and target language selections."""
        src = self._strip_language_flag(self.source_language.get())
        tgt = self._strip_language_flag(self.target_language.get())
        self.source_language.set(tgt)
        self.target_language.set(src)
        self._sync_language_combo_displays()
        self.on_language_change("both")

    def _update_translation_title(self):
        """Update translation card title from target language dropdown."""
        if self.translated_title_label is not None:
            lang = self._strip_language_flag(self.target_language.get())
            self.translated_title_label.configure(text=f"Translation ({lang})")

    def copy_live_transcript_to_clipboard(self):
        """Copy canonical transcript text from TranscriptStore."""
        try:
            if not hasattr(self, "transcript_store") or self.transcript_store is None:
                messagebox.showinfo("Copy Transcript", "No transcript store available.")
                return
            clean_text = self._get_clean_transcript_for_copy_export()
            if not clean_text.strip():
                messagebox.showinfo("Copy Transcript", "No transcript text to copy.")
                return
            self.clipboard_clear()
            self.clipboard_append(clean_text)
            segment_count = self.transcript_store.segment_count()
            self.log_copy_export_stats(clean_text, segment_count)
            self._log_transcript_copy_formatting(clean_text, segment_count)
            self._log_copy_export_transcript_diag(clean_text, segment_count)
            self._log_session_transcript_copied(clean_text)
            messagebox.showinfo("Copy Transcript", "Live transcript copied to clipboard.")
        except Exception as exc:
            logger.error("Error copying transcript: %s", exc)
            print(f"Error copying transcript: {exc}")
            messagebox.showerror("Copy Transcript", f"Could not copy transcript:\n{exc}")

    def copy_translation_to_clipboard(self):
        """Copy canonical live transcript from TranscriptStore."""
        try:
            if not hasattr(self, "transcript_store") or self.transcript_store is None:
                messagebox.showinfo("Copy Translation", "No transcript store available.")
                return
            clean_text = self._get_clean_transcript_for_copy_export()
            if not clean_text.strip():
                messagebox.showinfo("Copy Translation", "No transcript text to copy.")
                return
            self.clipboard_clear()
            self.clipboard_append(clean_text)
            segment_count = self.transcript_store.segment_count()
            self.log_copy_export_stats(clean_text, segment_count)
            self._log_transcript_copy_formatting(clean_text, segment_count)
            self._log_copy_export_transcript_diag(clean_text, segment_count)
            self._log_session_transcript_copied(clean_text)
            messagebox.showinfo("Copy Translation", "Live transcript copied to clipboard.")
        except Exception as exc:
            logger.error("Error copying transcript: %s", exc)
            print(f"Error copying transcript: {exc}")
            messagebox.showerror("Copy Translation", f"Could not copy transcript:\n{exc}")

    def export_transcript_placeholder(self):
        """Export canonical transcript text from TranscriptStore to a file."""
        from tkinter import filedialog

        try:
            if not hasattr(self, "transcript_store") or self.transcript_store is None:
                messagebox.showinfo("Export", "No transcript store available.")
                return
            clean_text = self._get_clean_transcript_for_copy_export()
            if not clean_text.strip():
                messagebox.showinfo("Export", "No transcript text to export.")
                return
            file_path = filedialog.asksaveasfilename(
                title="Export Live Transcript",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            )
            if not file_path:
                return
            with open(file_path, "w", encoding="utf-8") as export_file:
                export_file.write(clean_text)
            segment_count = self.transcript_store.segment_count()
            self.log_copy_export_stats(clean_text, segment_count)
            self._log_transcript_copy_formatting(clean_text, segment_count)
            self._log_copy_export_transcript_diag(clean_text, segment_count)
            messagebox.showinfo("Export", f"Transcript exported to:\n{file_path}")
        except Exception as exc:
            print(f"Error exporting transcript: {exc}")
            messagebox.showerror("Export", f"Could not export transcript:\n{exc}")

    def on_language_change(self, changed="both"):
        """Handle language dropdown changes and log selections to console."""
        try:
            if changed in ("source", "both"):
                dropdown = self._strip_language_flag(self.source_language.get())
                if dropdown != self.source_language.get():
                    self.source_language.set(dropdown)
                profile = self._build_language_profile(dropdown)
                self._listen_language = self._resolve_deepgram_language(dropdown)
                print(
                    f"Listening to dropdown: '{dropdown}' -> "
                    f"Deepgram code: '{self._listen_language}'"
                )
                if not profile.get("selection_supported", True):
                    print(
                        f"[LANGUAGE] unsupported profile: "
                        f"{profile.get('unsupported_reason') or 'unsupported_language_profile'}"
                    )
            if changed in ("target", "both"):
                target = self._strip_language_flag(self.target_language.get())
                if target != self.target_language.get():
                    self.target_language.set(target)
                print(f"Translate to: {target}")
                self._update_translation_title()
        except Exception as exc:
            print(f"Error updating language selection: {exc}")

    def clear_text(self):
        """Clear both the initial and translated verse text boxes."""
        try:
            self.reset_transcript_stability_state()
            self._reset_incremental_display_state()
            self.last_transcript_hash = set()
            self._transcript_hash_order = []
            self._last_speaker_utterance = {}
            self.last_speaker = None
            self.last_speaker_id = None
            self.current_speaker = None
            self.last_translation_speaker = None
            self.translation_error_shown = False
            self._recent_displayed_texts = []
            self.last_speech_time = 0.0
            self.fallback_speaker = 1
            self._reset_interim_tail_state()
            self._reset_meeting_segment_buffer_state()
            self._reset_segment_repair_state()
            if self.translated_verse_box is not None:
                self.translated_verse_box._placeholder_text = PLACEHOLDER_TRANSLATION
            for box in (self.initial_verse_box, self.translated_verse_box):
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.configure(state="disabled")
                self._show_text_placeholder(box)
            if hasattr(self, "transcript_store") and self.transcript_store is not None:
                self.transcript_store.clear()
            self._set_summary_panel_text(PLACEHOLDER_SUMMARY)
            print("Text boxes cleared.")
        except Exception as exc:
            print(f"Error clearing text: {exc}")
            messagebox.showerror("Error", f"Could not clear text:\n{exc}")
