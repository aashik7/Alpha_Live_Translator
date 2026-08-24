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
import uuid
from collections import deque
from types import SimpleNamespace

import customtkinter as ctk
import tkinter as tk
from PIL import Image
from tkinter import Menu, messagebox

from alpha.audio.microphone import MicrophoneCaptureMixin
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
    has_deepl_api_key,
)
from alpha.constants import (
    INTERIM_PREVIEW_LINE_GROUPING_ENABLED,
    MICROPHONE_CAPTURE_ENABLED_DEFAULT,
    TRANSLATION_PENDING_PLACEHOLDER_VISIBLE,
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
    MAX_RENDERED_UI_SEGMENTS,
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
    INTERIM_GHOST_TTL_MS,
    STOP_TAIL_MIN_CHARS_LATIN,
    STOP_TAIL_MIN_CHARS_CJK,
    UI_SPEAKER_LABEL,
    TRANSLATION_ENABLED,
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
    teams_commit_decision_from_dup_action_diagnostic_only,
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
from alpha.summary.transcript_store import TranscriptStore
from alpha.ui.theme import (
    APP_WINDOW_TITLE,
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
    FOOTER_ACTION_HEIGHT,
    FOOTER_ACTION_PAD_X,
    FOOTER_BTN_PAD_X,
    FOOTER_GROUP_GAP,
    FOOTER_PAD_X,
    FOOTER_PAD_X_STACKED,
    FOOTER_PAD_Y,
    FOOTER_PAD_Y_STACKED,
    FOOTER_ROW_GAP,
    FOOTER_STACK_BREAKPOINT,
    FONTS,
    HEADER_CONTROL_HEIGHT,
    INTERIM_FONT_PX,
    INTERIM_SPACE_ABOVE_PX,
    INTERIM_SPACE_BELOW_PX,
    LAYOUT_FOOTER_WRAP_BREAKPOINT,
    LAYOUT_HAMBURGER_BREAKPOINT,
    LAYOUT_MEDIUM_BREAKPOINT,
    LAYOUT_MIN_HEIGHT,
    LAYOUT_MIN_WIDTH,
    LAYOUT_STATUS_COMPACT_BREAKPOINT,
    DEFAULT_WINDOW_WIDTH,
    LAYOUT_WIDE_BREAKPOINT,
    LISTEN_BUTTON_LABELS,
    MEETING_SUMMARY_BUTTON_TEXT,
    PANE_BG,
    PLACEHOLDER_SUMMARY,
    PLACEHOLDER_TRANSCRIPT,
    PLACEHOLDER_TRANSLATION,
    CONTENT_STACKED_PRIMARY_WEIGHT,
    CONTENT_STACKED_REFERENCE_WEIGHT,
    RADII,
    READING_TYPOGRAPHY,
    RIGHT_COLUMN_MIN_WIDTH,
    SECTION_TRANSCRIPT_TITLE,
    SECTION_TRANSLATION_TITLE,
    SMALL_BUTTON_HEIGHT,
    SPACING,
    SPEAKER_LABEL_FONT_PX,
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
    UI_LANGUAGE_SHORT_LABELS,
    WAVEFORM_ANIMATION_MS,
    WAVEFORM_BAR_COUNT,
    WAVEFORM_BAR_COUNT_WIDE,
    WAVEFORM_CANVAS_HEIGHT,
    WAVEFORM_CANVAS_WIDTH,
    WAVEFORM_CANVAS_WIDTH_WIDE,
)
from alpha.ui.strings import (
    LANGUAGE_NAMES,
    available_languages,
    clear_saved_language,
    has_saved_language,
    get_language,
    save_language,
    set_language,
    t,
    translate_all,
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

# Header-only display abbreviation, item 71 Phase 3e. Below the header's
# "medium" mode (LAYOUT_HAMBURGER_BREAKPOINT), the language dropdowns
# already narrow to 128px (`_pack_header_controls`), and "Japanese"/"English"
# at that width was measured to overflow the header row far enough to push
# the swap toggle and Meeting Summary button off-screen -- reported with two
# screenshots. Only these two are needed: `SOURCE_LANGUAGES`/
# `TARGET_LANGUAGES` are Japanese/English only (constants.py's "Phase 1
# active UI scope").
LANGUAGE_ABBREVIATIONS = {
    "Japanese": "JP",
    "English": "EN",
}

# Item 71. Side-by-side reading grid, from the design's `.atf-reading-grid`
# (`minmax(0, 70fr) 8px minmax(220px, 30fr)`): translation is the primary pane
# and the original transcript is a reference pane beside it.
CONTENT_PRIMARY_WEIGHT = 70
CONTENT_REFERENCE_WEIGHT = 30
# Below this the design stacks the grid into rows
# (`@media (max-width: 700px)` -> `grid-template-columns: 1fr`). The app
# already had a 700 breakpoint for the same reason.
CONTENT_STACK_BREAKPOINT = LAYOUT_MEDIUM_BREAKPOINT

# The microphone control used to be gated here, on a width threshold, because
# it lived in the header. Item 88c moved it into the status strip, which is
# shown at every width, so it needs no threshold and the constant that carried
# one is gone. The lesson it recorded is kept in `theme.py`'s
# `LAYOUT_HAMBURGER_BREAKPOINT`: one threshold owning a whole surface, never
# two that have to agree.

# Retained: still the vertical split used when the grid stacks on a narrow
# window, where transcript and translation share one column as rows.
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
        self._apply_window_identity()
        perf_checkpoint("mainwindow_init_start")

        self._compact_mode = None
        self._header_mode = None
        self._layout_mode = None
        self._menu_visible = False
        # Item 71: the meeting summary must not be on screen when the app
        # opens -- only after its button is pressed.
        self.summary_panel_visible = False
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
        # Item 71: the original transcript is a reference pane, hidden until
        # asked for. The design's default is `.atf-original-hidden` -- a single
        # full-width translation column.
        # Item 81: the transcript reference pane is shown from the first paint.
        # It used to start hidden, so a fresh launch showed only the
        # translation and the user had to find the "Show Transcript" button
        # before seeing what was being transcribed at all.
        # `_sync_transcript_visibility` at the end of `create_main_content`
        # applies this to both the button and the grid.
        self._initial_verse_visible = True
        self.transcript_column = None
        # Which branch of the design's type scale is currently applied.
        # `None` until the panes are built, so the first refresh always runs.
        self._reading_typography_stacked = None
        self._first_map_handled = False
        # Whether the header's language combos currently show "JP"/"EN"
        # instead of "Japanese"/"English". `None` until `_pack_header_controls`
        # first runs, so the first call always applies (same reasoning as
        # `_reading_typography_stacked` above).
        self._header_lang_abbreviated = None
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
        self._latest_interim_utterance_id = ""
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
        # Default the target to the OTHER language. Both dropdowns opened on
        # "Japanese" / "Japanese" -- nothing stops the user picking the same
        # language twice, but defaulting there made every fresh launch look
        # like translation was pointed at itself.
        _default_target_ui = "English" if _default_source_ui == "Japanese" else "Japanese"
        self.source_language = ctk.StringVar(value=_default_source_ui)
        self.target_language = ctk.StringVar(value=_default_target_ui)
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
        self.copy_translation_btn_menu = None
        self.export_btn_menu = None
        self.clear_btn_menu = None
        self._hamburger_actions_visible = None
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
        self.ui_language_button = None
        self.ui_language_combo_menu = None
        # Microphone capture is a per-session choice, read at Start like the
        # language dropdown. Default OFF: Alpha transcribes ONE language per
        # session and merges mic with system audio before Deepgram, so in a
        # bilingual meeting the operator's own speech lands in the other
        # language's ASR. See MICROPHONE_CAPTURE_ENABLED_DEFAULT.
        self._microphone_capture_enabled = bool(MICROPHONE_CAPTURE_ENABLED_DEFAULT)
        self.mic_switch = None
        self.mic_switch_menu = None
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
        # Item 73. Declared at process scope, RESET in _close_wasapi_stream --
        # not only here. `_wasapi_rate`/`_wasapi_channels` above are the
        # cautionary example: they are set here and again in
        # _start_wasapi_loopback but cleared nowhere, so a second session whose
        # device acquisition raises silently inherits the first session's
        # values. Session-scoped state must be cleared where the session ends.
        self._wasapi_device_watch_thread = None
        self._wasapi_default_endpoint_baseline = ""
        self._wasapi_device_change_reported = False
        self._mix_thread = None
        self._mic_stream = None
        self._wasapi_channels = 1
        self._wasapi_rate = DEEPGRAM_SAMPLE_RATE
        self._wasapi_frames_per_buffer = WASAPI_FRAMES_PER_BUFFER
        self._dg_ws = None
        self._dg_thread = None
        self._dg_reconnect_lock = threading.Lock()  # CHANGED: reconnect serialization (fix 5)
        self._dg_reconnecting = False  # CHANGED: prevent parallel reconnects (fix 5)
        # Item 47 runtime half: a key that was valid at Start and is later
        # rejected must surface as a clear FAILED state, not an endless
        # reconnect loop the operator cannot interpret. Set in
        # `_deepgram_on_error`, cleared whenever the socket opens.
        self._dg_auth_failed = False
        # Item 73 sets this from the WASAPI device watcher's own thread; item
        # 47's indicator renders it. Windows moving the default output leaves
        # every connection signal healthy while the capture device records
        # something nothing is routed to, so it needs its own signal.
        self._audio_device_changed = False
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
        self._translation_segment_seq = 0
        # fixes TASK_3A_FINDINGS.md Item 1/2: identity-keyed translation state
        # replaces the flat positional list and single global pending payload.
        self._translation_items_by_utterance: dict[str, dict] = {}
        self._pending_translations_by_utterance: dict[tuple, dict] = {}
        self._translation_debounce_after_ids: dict[tuple, object] = {}
        self._translation_status_message = ""
        self._recent_displayed_texts = []
        self.transcript_store = TranscriptStore()
        self._summary_service = None

        self.event_bus = EventBus()
        self._setup_event_subscriptions()

        try:
            from alpha.utils import startup_perf as _startup_perf

            _startup_perf.mark("UI_widgets_construction_started")
        except Exception:
            pass
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
            from alpha.utils import startup_perf as _startup_perf

            _startup_perf.mark("UI_widgets_construction_completed")
        except Exception:
            pass

        try:
            from alpha.utils.ui_thread_guard import register_ui_main_thread

            register_ui_main_thread()
        except Exception:
            pass

        # Keep first paint responsive: delay layout ratio work until after the
        # real Alpha window has painted and become interactive.
        import os as _os_startup

        _profile = _os_startup.environ.get("ALPHA_STARTUP_PROFILE", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        # During startup profiling, push layout/log work past the settle window so
        # post-paint responsiveness reflects the interactive Alpha UI, not a
        # redundant first re-layout. Normal launches keep a short deferral.
        _layout_delay_ms = 3200 if _profile else 900
        _pane_delay_ms = 3300 if _profile else 1000
        _logs_delay_ms = 3500 if _profile else (2000 if UI_PERFORMANCE_MODE else 800)
        self.after(_layout_delay_ms, self._apply_responsive_layout_debounced)
        self.after(_pane_delay_ms, self._set_initial_pane_ratio)
        self._update_translation_title()
        self.after(0, self._deferred_post_show_init)
        self._is_finalizing = False
        self._is_stopping = False
        self._stop_finalize_started = False
        perf_checkpoint("ui_widgets_created")
        self.after(_logs_delay_ms, self._emit_deferred_startup_logs)
        perf_checkpoint("mainwindow_init_complete")

    @property
    def summary_service(self):
        """Lazy SummaryService — not required until the summary panel is used."""
        if self._summary_service is None:
            from alpha.summary.summary_service import SummaryService

            self._summary_service = SummaryService()
        return self._summary_service

    @summary_service.setter
    def summary_service(self, value):
        self._summary_service = value

    def _deferred_post_show_init(self):
        """Single after(0) hook: translation placeholder, runtime reset, UI queue loop."""
        try:
            from alpha.utils import startup_perf as _startup_perf

            _startup_perf.mark("background_initialization_started")
        except Exception:
            pass
        self._initialize_translation()
        try:
            from alpha.utils import startup_perf as _startup_perf

            _startup_perf.mark("translation_service_ready")
        except Exception:
            pass
        self._deferred_lightweight_init()
        self._start_ui_loops_once()
        try:
            from alpha.utils.session_watchdog import start_ui_heartbeat

            start_ui_heartbeat(self)
        except Exception:
            pass
        try:
            from alpha.utils.ui_event_bus import get_ui_event_bus

            self._ui_event_bus = get_ui_event_bus()
            self._start_ui_event_bus_drain_loop()
        except Exception:
            pass
        try:
            from alpha.utils import startup_perf as _startup_perf

            _startup_perf.mark("background_initialization_completed")
        except Exception:
            pass
        # Interactive-ready is marked from main.py after real Alpha first paint.

    def _mark_real_alpha_interactive_ready(self):
        """Mark interactive only after real Alpha first paint is recorded."""
        try:
            from alpha.utils import startup_perf as _startup_perf

            marks = _startup_perf.get_marks()
            if "real_alpha_first_paint" not in marks and "first_visible_paint" not in marks:
                self.after(20, self._mark_real_alpha_interactive_ready)
                return
            # Snapshot current geometry so the deferred first responsive-layout
            # pass can no-op when nothing changed (avoids a post-paint UI stall).
            # Must match what `_apply_responsive_layout` snapshots into these
            # same two attributes -- design px via `_design_width()`, not
            # `winfo_width()`'s device px -- or the very first skip-check
            # compares a device-px value against a design-px one.
            try:
                design_width = self._design_width()
                if design_width > 1:
                    mode = self._get_layout_mode(design_width)
                    self._last_layout_width = design_width
                    self._last_layout_mode_applied = mode
                    self._layout_mode = mode
            except Exception:
                pass
            _startup_perf.force_mark("real_alpha_interactive_ready")
            _startup_perf.force_mark("application_interactive_ready")
            _startup_perf.sample_threads("interactive_ready")
            _startup_perf.sample_memory("interactive_ready")

            def _start_hb():
                try:
                    if _startup_perf.profiling_enabled():
                        _startup_perf.start_ui_heartbeat(self, 100)
                except Exception:
                    pass

            # Let mainloop settle before measuring post-paint callback drift.
            self.after(250, _start_hb)
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
                from alpha.constants import (
                    HIGH_FREQUENCY_UI_DRAIN_LOGGING_ENABLED,
                    UI_EVENT_DRAIN_VERBOSE_LOGGING,
                )

                if HIGH_FREQUENCY_UI_DRAIN_LOGGING_ENABLED or UI_EVENT_DRAIN_VERBOSE_LOGGING:
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
        self._check_interim_ghost_watchdog()
        self._schedule_ui_queue_tick()

    def _check_interim_ghost_watchdog(self):
        """Remove an interim preview line that has stopped being refreshed.

        An interim ("... ⏳") line is by definition a preview of an
        utterance still in progress, so a live one keeps being fed by new
        interim events. If nothing has refreshed it for INTERIM_GHOST_TTL_MS,
        it is not live -- it is an orphan some commit/clear path left
        behind, and it must go.

        This is a liveness invariant, not a text/identity heuristic: it
        holds no matter which code path created the orphan, so a permanent
        ghost line is structurally impossible even if the comparison logic
        in _apply_final_interim_comparison is wrong, or a future code path
        forgets to clear. Firing is logged so a recurring miss upstream is
        visible rather than silently papered over.
        """
        if not (getattr(self, "_latest_interim_text", "") or "").strip():
            return
        last_at = getattr(self, "_last_interim_ui_at", 0.0) or 0.0
        if last_at <= 0.0:
            return
        stale_ms = (time.perf_counter() - last_at) * 1000.0
        if stale_ms < INTERIM_GHOST_TTL_MS:
            return
        stale_text = (getattr(self, "_latest_interim_text", "") or "").strip()
        stale_id = str(getattr(self, "_latest_interim_utterance_id", "") or "")
        stale_speaker = getattr(self, "_latest_interim_speaker", 1) or 1
        # fixes BUG_FIX_ROADMAP.md Batch 3 item 11b: this watchdog enforces a
        # *display* liveness invariant (see the docstring), but _clear_interim_tail
        # also wipes _latest_interim_text -- which is the ONLY source
        # _recover_interim_tail_on_stop reads. So a tail orphaned shortly before
        # Stop was destroyed by the display layer before the content-recovery
        # path could see it, making items 10 and 11 unreachable in exactly the
        # scenario they exist for. Confirmed live in run
        # ...20260809-033339: watchdog cleared a 10-char interim at +267.19s,
        # Stop ran at +268.25s and found nothing; that speech is absent from
        # the final export. Bug Report.md 4.3 predicted this interaction.
        #
        # Preserve the orphan for the Stop-time recovery path instead of
        # dropping it. This does NOT weaken the ghost fix: the visible line is
        # still removed immediately, right below. Whether the stash is safe to
        # commit is decided by _check_stop_tail_duplicate (item 10) and
        # _should_commit_interim_recovery (item 11), which is precisely what
        # those two were hardened to judge -- so an orphan that did later get
        # committed is filtered there rather than being pre-emptively lost here.
        self._watchdog_orphaned_interim_text = stale_text
        self._watchdog_orphaned_interim_speaker = stale_speaker
        self._watchdog_orphaned_interim_utterance_id = stale_id
        # Stamped so the Stop path can tell "nothing happened since this was
        # orphaned" from "the speaker carried on". See the supersession check
        # in _recover_interim_tail_on_stop.
        self._watchdog_orphaned_interim_at = last_at
        self._clear_interim_tail()
        self._interim_log(
            "[INTERIM] ghost watchdog cleared",
            {
                "stale_ms": round(stale_ms, 1),
                "ttl_ms": INTERIM_GHOST_TTL_MS,
                "text_len": len(stale_text),
                "text_preview": stale_text[:120],
                "interim_utterance_id": stale_id,
                "orphan_preserved_for_stop_recovery": True,
            },
        )
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "INTERIM_GHOST_LINE_CLEARED_BY_WATCHDOG",
                stale_ms=round(stale_ms, 1),
                ttl_ms=INTERIM_GHOST_TTL_MS,
                text_len=len(stale_text),
                interim_utterance_id=stale_id,
            )
        except Exception:
            pass

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
        retry_triggered = False
        # Item 74(c). The item cap alone bounds HOW MANY items are rendered,
        # never how long the tick takes, so a rise in per-item cost turns into
        # an unbounded freeze rather than a deferral. Measured today the render
        # is 0.009 ms per segment -- 8 items is 0.07 ms against a 10 ms budget,
        # about 2700x of headroom -- so this changes nothing now. It bites at
        # roughly 25 ms per item, which is the region a per-entry widget model
        # lands in (item 75 measured 35.6 ms per card), and at that point the
        # flush would outlast its own 200 ms interval and the buffer would stop
        # draining. Whatever is not rendered stays in the buffer and is
        # rescheduled below exactly as an over-cap batch already is.
        budget_s = UI_QUEUE_TIME_BUDGET_MS / 1000.0
        deferred_for_time = 0
        for idx, item in enumerate(batch[:max_inserts]):
            if idx and (time.perf_counter() - start) >= budget_s:
                # Preserve chronological order: everything still unrendered
                # goes back to the FRONT of the buffer, including items past
                # `max_inserts`, so the over-cap branch below has nothing left
                # to do and is correctly skipped by `retry_triggered`.
                self._transcript_ui_batch_buffer[:0] = batch[idx:]
                deferred_for_time = len(batch) - idx
                self._schedule_transcript_ui_batch_flush()
                retry_triggered = True
                # Never defer silently. A cap that trims work without a trace
                # reads as "kept up fine" in every later diagnosis.
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "TRANSCRIPT_UI_FLUSH_TIME_BUDGET_EXCEEDED",
                        rendered=idx,
                        deferred=deferred_for_time,
                        elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
                        budget_ms=UI_QUEUE_TIME_BUDGET_MS,
                    )
                except Exception:
                    pass
                break
            text_len = len((item.get("text") or ""))
            result = self._display_transcript_item(item)
            if result == "retry_pending":
                # Preserve chronological order: this item and everything
                # still unprocessed this tick go back to the FRONT of the
                # buffer, in original order. Nothing after it is ever
                # displayed before it resolves or falls back.
                remaining = [item] + batch[idx + 1:]
                self._transcript_ui_batch_buffer[:0] = remaining
                self._schedule_transcript_ui_batch_flush()
                retry_triggered = True
                break
            chars_inserted += text_len
        if not retry_triggered and len(batch) > max_inserts:
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
        # Cleared with the widget it describes: it records how many logical
        # lines each rendered segment wrote, and a stale entry would make the
        # render cap trim the wrong number of lines after a Clear.
        self._displayed_segment_lines = deque()
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

    def _interim_preview_lines(self, interim_text: str):
        """The live preview, split into readable lines. Item 69.

        Yields (text, is_last) so only the final line carries the hourglass --
        the earlier lines have settled enough to read, and repeating the glyph
        on each one reads as several pending utterances rather than one.

        English only, and it falls back to a single line on ANY failure: a
        preview that renders is always better than one that raises on the UI
        thread.
        """
        text = (interim_text or "").strip()
        if not text:
            return
        try:
            if not INTERIM_PREVIEW_LINE_GROUPING_ENABLED:
                raise RuntimeError("grouping disabled")
            lang = str(getattr(self, "_listen_language", "") or "").lower()
            if not lang:
                lang = str(self.source_language.get() or "").lower()
            if not lang.startswith("en"):
                raise RuntimeError("non-english preview")
            from alpha.utils.english_line_grouping import (
                group_sentences_into_lines,
                text_is_preserved,
            )

            parts = group_sentences_into_lines(text)
            if not parts or not text_is_preserved(text, parts):
                raise RuntimeError("grouping would change the preview text")
        except Exception:
            parts = [text]
        for index, part in enumerate(parts):
            yield part, index == len(parts) - 1

    def _remove_interim_line_from_display(self):
        box = self._transcript_box()
        if box is None:
            return
        # fixes BUG_FIX_ROADMAP.md Batch 2 item 4: box.compare("interim_anchor",
        # ...) raised TclError on the normal "nothing to remove" case (no
        # interim currently on screen) -- every one of those was caught and
        # logged as remove_exception, drowning any genuinely unexpected
        # exception in noise. Guard on the mark actually existing first;
        # zero behavior change, since "mark absent" already meant "nothing
        # to remove" either way.
        if "interim_anchor" not in box.mark_names():
            self._interim_log("[INTERIM] remove_attempt", {"has_mark": False})
            return
        try:
            box.configure(state="normal")
            box.delete("interim_anchor", "end")
            box.mark_unset("interim_anchor")
            box.configure(state="disabled")
            self._interim_log("[INTERIM] remove_success", {})
        except Exception as exc:
            self._interim_log("[INTERIM] remove_exception", {"error": str(exc)})

    def _ui_speaker_label_text(self) -> str:
        """UI-only speaker prefix; never part of Raw/Stable/Final lexical scoring."""
        from alpha.utils.ui_speaker_label import ui_speaker_prefix

        return ui_speaker_prefix()

    def _insert_speaker_segment_line(self, box, speaker, text: str):
        tag = self._speaker_tag(speaker)
        # `segment_anchor` exists so `_on_store_segment_updated` can replace the
        # last displayed segment when its text is corrected. It has to mark
        # where the segment STARTS.
        #
        # Setting it afterwards from `"insert linestart"` marked the wrong
        # place: `insert` is the cursor, not the write position, so the anchor
        # landed on the line AFTER the segment and
        # `delete("segment_anchor", "end")` removed only the trailing newline.
        # Driven through the real methods, a corrected segment did not replace
        # its predecessor -- it was appended to it, on one line, with no
        # separator:
        #
        #   'Speaker: the first version of this segmentSpeaker: the CORRECTED
        #    version of this segment'
        #
        # Same shape and same fix as `interim_anchor` in
        # `_update_interim_line_only`: re-establish an empty last line so the
        # anchor sits at a line start, mark the real insertion point
        # (`"end-1c"`, not `"end"` -- Tk keeps a trailing newline of its own),
        # and give it LEFT gravity so writing the segment past it does not
        # carry it along.
        if box.index("end-1c") != box.index("end-1c linestart"):
            box.insert("end", "\n")
        box.mark_set("segment_anchor", "end-1c")
        box.mark_gravity("segment_anchor", "left")
        # C8, item 82. This is the path the live pane actually uses: every
        # translation-eligible commit reaches it through
        # `duplicate_protection.py`'s `_on_store_segment_added` /
        # `_on_store_segment_updated`, and the grouped renderer
        # `_render_transcript_from_store` is only reached when a segment is NOT
        # translation-eligible. So the pane a user watches during a meeting was
        # the one path of the four C8 names that did not group.
        #
        # Confirmed against the live run of 2026-08-20 rather than argued: the
        # same session produced `Alpha output.txt` with 84 lines at a median of
        # 24 words and nothing over 400 characters, while the pane held 36 lines
        # -- exactly the record count -- at a median of 50 words and up to 1509
        # characters. One record, one unbroken line.
        #
        # `_readable_parts` is the same memoised grouping the store, the copy
        # path and the export all use, so the pane can no longer disagree with
        # them, and text no longer reflows the moment it commits (item 69).
        # It falls back to the raw text on any failure: an unreadable line is
        # better than a lost one.
        #
        # Item 75. The meta row and the highlight are `tk.Text` tag ranges, not
        # per-entry widgets. Measured on this machine before choosing: 430
        # entries as CTk cards cost 15.33 s, 5,161 widgets and +49.3 MB RSS,
        # against 38.46 ms for a full text rewrite -- 35.6 ms per card against
        # 0.042 ms incremental, so ONE card is 3.6x the entire 10 ms per-tick
        # budget.
        #
        # This is the transcript pane only. `_get_clean_transcript_for_copy_export`
        # builds the exported transcript from `transcript_store.get_all()` and
        # has no widget fallback, so a decorative row here can never reach the
        # client's file. The translation pane is the opposite --
        # `_get_translated_transcript_for_copy_export` falls back to
        # `box.get("1.0", "end")` -- which is why nothing decorative is ever
        # written there.
        # An empty commit must still write NOTHING -- not even a meta row.
        # Grouping an empty segment yields no parts, and a meta row on its own
        # would be a dangling `Speaker 1 · 14:32:05` with no transcript under
        # it, counted by the render cap as a line that carries no content.
        parts = self._readable_segment_parts(speaker, text)
        if not parts:
            return 0
        self._ensure_entry_tags(box)
        entry_start = box.index("end-1c")
        lines = 0
        meta = self._entry_meta_text(speaker)
        if meta:
            box.insert("end", meta + "\n", "entry_meta")
            lines += 1
        for part in parts:
            box.insert("end", self._ui_speaker_label_text(), tag)
            box.insert("end", part + "\n", "body")
            lines += 1
        # The count travels to the render cap, which trims by the lines each
        # segment actually wrote (item 74(a)). Counting the meta row here is
        # what keeps that arithmetic exact now that an entry is no longer one
        # logical line -- the single hazard the ledger flagged for this item.
        self._highlight_current_entry(box, entry_start)
        return lines

    def _ensure_entry_tags(self, box) -> None:
        """Configure item 75's tags on first use, on this widget only.

        Done lazily here rather than in `_create_styled_text` so the tags exist
        on the transcript widget and nowhere else. `_create_styled_text` builds
        both panes, and the translation pane must stay free of decoration.
        """
        try:
            names = box.tag_names()
        except Exception:
            return
        try:
            if "entry_meta" not in names:
                box.tag_configure(
                    "entry_meta",
                    foreground=COLORS.get("text_muted", "#94A3B8"),
                    spacing1=6,
                    spacing3=2,
                )
            if "current_entry" not in names:
                box.tag_configure(
                    "current_entry",
                    background=COLORS.get("card_bg_soft", COLORS.get("card_bg", "")),
                )
                # Lowest priority: it sets only a background, but keeping it
                # under the speaker/body tags means it can never win a conflict
                # over an attribute they also set.
                box.tag_lower("current_entry")
        except Exception:
            pass

    def _entry_meta_text(self, speaker) -> str:
        """`Speaker 2 - 14:32:05`, the per-entry meta row (item 75).

        Returns "" on any failure, and the caller then renders the entry
        exactly as it did before -- a missing meta row is cosmetic, a raised
        exception here would cost the committed segment its render.
        """
        try:
            label = (self._ui_speaker_label_text() or "").strip().rstrip(":").strip()
        except Exception:
            label = ""
        try:
            speaker_num = int(speaker)
            if 1 <= speaker_num <= 4:
                label = f"{label} {speaker_num}".strip()
        except (TypeError, ValueError):
            pass
        when = self._entry_meta_time_text()
        parts = [p for p in (label, when) if p]
        return " · ".join(parts)

    def _entry_meta_time_text(self) -> str:
        """Wall-clock time for the newest committed segment.

        Read from the store rather than threaded through the UI hooks:
        `_on_store_segment_added` / `_on_store_segment_updated` take no
        timestamp, and `duplicate_protection.py` writes the segment to the
        store BEFORE calling either hook, so the value is already there.
        Measured on run `...20260820-235820`: 126 of 126 rows carry it, as an
        epoch float.
        """
        stamp = None
        try:
            store = getattr(self, "transcript_store", None)
            if store is not None:
                segments = store.get_all()
                if segments:
                    stamp = getattr(segments[-1], "timestamp", None)
        except Exception:
            stamp = None
        try:
            if stamp is None:
                value = time.time()
            elif isinstance(stamp, (int, float)):
                value = float(stamp)
            else:
                value = float(str(stamp).strip())
            return time.strftime("%H:%M:%S", time.localtime(value))
        except Exception:
            try:
                return time.strftime("%H:%M:%S")
            except Exception:
                return ""

    def _highlight_current_entry(self, box, start_index) -> None:
        """Move the current-entry highlight to the entry starting at `start_index`.

        One tag range, moved, rather than per-entry state: the previous range is
        removed across the whole widget first, so the highlight cannot be left
        behind by a trim at the top or by a revision replacing the last entry.
        """
        try:
            box.tag_remove("current_entry", "1.0", "end")
            box.tag_add("current_entry", start_index, "end-1c")
        except Exception:
            pass

    def _delete_translation_entry(self, box, mark_name, item=None):
        """Remove a whole displayed translation entry, however many lines it is.

        Item 74(b): both removal sites deleted `mark -> mark lineend + 1 chars`,
        exactly ONE logical line. That was correct only while a translation was
        always one line, and item 83's grouping makes it one to three. Deleting
        one line from a three-line entry leaves two orphans that no later
        revision can reclaim -- and they reach the client's file, because
        `_get_translated_transcript_for_copy_export` falls back to reading the
        widget whenever the identity registry has no completed `line_text`.

        The line count is recorded on the registry entry when the translation is
        written. It falls back to one so a pending-row mark, which is genuinely
        a single line, still behaves exactly as before.
        """
        lines = 1
        try:
            if isinstance(item, dict):
                lines = max(1, int(item.get("entry_lines") or 1))
        except Exception:
            lines = 1
        # Resolve the mark to a concrete "line.char" index BEFORE composing the
        # end expression. A canonical utterance id is `jp-utt-<hex>`, so the
        # mark built from it -- `tr_done_jp-utt-e0dcbd1255fc_1` -- contains
        # hyphens, and Tk reads a `-`/`+` run inside an index as a modifier
        # operator. Probed against real Tk:
        #
        #   index("tr_done_jp-utt-A_1")                   -> "2.0"     ok
        #   compare("tr_done_jp-utt-A_1", ">=", "1.0")    -> True      ok
        #   index("tr_done_jp-utt-A_1 lineend + 1 chars") -> TclError
        #
        # The guard passes and the delete then raises. Both callers wrap this
        # in `except Exception: pass`, so it failed silently and the superseded
        # translation stayed on screen for the rest of the session -- and
        # reached the client's file through
        # `_get_translated_transcript_for_copy_export`'s widget-read fallback.
        # That made the identity-keyed removal (TASK_3A_FINDINGS.md Item 1) and
        # its multi-line replay (item 74(b)) dead code on the Japanese path,
        # where every id is hyphenated. A bare `box.index(mark)` is safe, and
        # the "line.char" it returns composes safely.
        start = box.index(mark_name)
        end = f"{start} lineend + 1 chars"
        for _ in range(lines - 1):
            end += " + 1 lines"
        box.delete(start, end)

    def _readable_translation_parts(self, text: str) -> list[str]:
        """Group one finished translation into readable lines. Item 83.

        The rule is chosen by what the text IS, not by the transcript's
        language: a translation's language is the target the user picked, and
        the transcript beside it is the other one. Japanese gets the `。！？`
        rule, anything else gets the English sentence rule, and text that
        matches neither is returned untouched.

        This is the TRANSLATION pane only. Contract C9 -- Japanese is never
        regrouped by a display rule -- is about the Japanese TRANSCRIPT, whose
        boundaries come from `japanese_sentence_assembler.py`. That path is not
        reached from here and is not changed.

        Falls back to the original single line on any failure: an over-long
        line is a readability problem, a lost one is a data problem.
        """
        raw = (text or "").strip()
        if not raw:
            return []
        try:
            from alpha.utils.japanese_line_grouping import (
                group_japanese_lines,
                japanese_text_is_preserved,
                looks_japanese,
            )

            if looks_japanese(raw):
                parts = group_japanese_lines(raw)
                if parts and japanese_text_is_preserved(raw, parts):
                    return parts
                return [raw]
        except Exception:
            return [raw]
        try:
            from alpha.utils.english_line_grouping import (
                group_sentences_into_lines,
                text_is_preserved,
            )

            parts = [p for p in group_sentences_into_lines(raw) if p and p.strip()]
            if parts and text_is_preserved(raw, parts):
                return parts
        except Exception:
            pass
        return [raw]

    def _readable_segment_parts(self, speaker, text: str) -> list[str]:
        """Group one committed segment into the design's readable lines.

        Kept separate from the insert so the render cap can ask how many lines a
        segment will occupy without writing anything, and so the fallback is in
        one place rather than repeated at each call site.
        """
        raw = (text or "").strip()
        if not raw:
            return []
        store = getattr(self, "transcript_store", None)
        if store is None or not hasattr(store, "_readable_parts"):
            return [raw]
        try:
            segment = SimpleNamespace(text=raw, source_language=self._segment_language())
            parts = [p for p in store._readable_parts(segment) if p and p.strip()]
            return parts or [raw]
        except Exception:
            return [raw]

    def _segment_language(self) -> str:
        """Language tag for grouping. Grouping is English-only by design --
        Japanese gets its boundaries from the assembler (C9)."""
        try:
            return str(getattr(self, "_listen_language", "") or "")
        except Exception:
            return ""

    def _on_store_segment_added(
        self,
        speaker,
        text: str,
        *,
        canonical_utterance_id: str = "",
        source_version: int = 1,
        source_record_id: str = "",
    ):
        self._last_operation_hint = "append_segment"
        t0 = time.perf_counter()
        try:
            self._remove_interim_line_from_display()
        except Exception:
            pass
        # A Japanese revision arrives at THIS hook, not at
        # `_on_store_segment_updated`, because `duplicate_protection.py`
        # promotes a commit to "update" only on the ENGLISH lifecycle's
        # vocabulary. Its superseded translation is therefore NOT removed here
        # -- it is removed in `_clear_translation_loading_item`, at the moment
        # the replacement text is actually written. Removing it here instead
        # would blank the utterance for the whole translation round-trip, and
        # lose it outright if the resubmitted job then failed. See the comment
        # there for the measured evidence.
        # fixes TASK_8_REPORT.md: translation submission used to run AFTER
        # all transcript-box rendering below, and after an unconditional
        # `if box is None: return`. Any commit reason funnels through this
        # one shared hook (English _commit_locked's 6 reasons, the Japanese
        # continuity assembler's commits, and Japanese manual-mode's direct
        # callers alike) -- so a torn-down/unavailable transcript box, or
        # any exception raised by the rendering code that used to run
        # first, silently dropped translation for an already-successfully-
        # committed, translation-eligible record with zero trace. Submit
        # first, render second, so translation delivery never depends on
        # UI-widget state.
        try:
            self.submit_text_for_translation(
                text,
                speaker=speaker,
                force_flush_previous=True,
                canonical_utterance_id=canonical_utterance_id,
                source_version=source_version,
                source_record_id=source_record_id,
            )
        except Exception:
            pass
        box = self._transcript_box()
        if box is None:
            return
        box.configure(state="normal")
        if hasattr(self, "_clear_text_placeholder"):
            self._clear_text_placeholder(box)
        inserted_lines = self._insert_speaker_segment_line(box, speaker, text) or 1
        # Bound rendered UI history; canonical TranscriptStore remains complete.
        #
        # Item 74(a), now live. This trim used to delete ONE logical line per
        # excess SEGMENT, which was only correct while a segment was always
        # exactly one line. Grouping (C8, above) makes a segment 1-3 lines, so
        # counting segments would under-trim by the lines-per-entry factor and
        # leave half-entries stranded at the top of the pane. The widget is now
        # trimmed by the lines each segment actually wrote, recorded in the same
        # order they were inserted.
        try:
            limit = int(MAX_RENDERED_UI_SEGMENTS)
            history = getattr(self, "_displayed_segment_lines", None)
            if history is None:
                history = deque()
                self._displayed_segment_lines = history
            history.append(int(inserted_lines))
            while len(history) > limit:
                stale_lines = int(history.popleft() or 1)
                for _ in range(max(1, stale_lines)):
                    try:
                        box.delete("1.0", "2.0")
                    except Exception:
                        break
                if not getattr(self, "_ui_render_limit_warned", False):
                    self._ui_render_limit_warned = True
                    print(
                        f"UI_RENDERED_SEGMENT_LIMIT_REACHED "
                        f"limit={limit} canonical_kept=true"
                    )
        except Exception:
            pass
        box.configure(state="disabled")
        self._maybe_scroll_transcript_box(box)
        self._displayed_segment_count = min(
            int(getattr(self, "_displayed_segment_count", 0) or 0) + 1,
            int(MAX_RENDERED_UI_SEGMENTS),
        )
        self._exported_ui_segment_count += 1
        insert_ms = (time.perf_counter() - t0) * 1000.0
        hist = getattr(self, "_ui_insert_durations_ms", None)
        if hist is None:
            hist = []
            self._ui_insert_durations_ms = hist
        hist.append(insert_ms)
        if len(hist) > 200:
            del hist[:-200]
        if insert_ms > 50.0:
            print(f"UI_TRANSCRIPT_INSERT_SLOW ms={insert_ms:.2f}")
        try:
            from alpha.utils.transcript_evidence import log_ui_exported_segment

            log_ui_exported_segment(
                speaker_label=str(UI_SPEAKER_LABEL or "Speaker:").rstrip(":"),
                ui_text=(text or "").strip(),
                ui_segment_id=f"ui-{self._exported_ui_segment_count}",
                source_stable_commit_id=f"stable-{self._exported_ui_segment_count}",
            )
        except Exception:
            pass
        self._refresh_transcript_scrollbar(box)

    def _on_store_segment_updated(
        self,
        speaker,
        text: str,
        *,
        canonical_utterance_id: str = "",
        source_version: int = 1,
        source_record_id: str = "",
    ):
        self._last_operation_hint = "update_segment"
        # fixes BUG_FIX_ROADMAP.md Batch 2 item 5: all 3 except blocks in
        # this function used to swallow failures with zero logging. The
        # store mutation this function reacts to has already committed by
        # the time these run (see the TASK_8_REPORT.md/TASK_3A_FINDINGS.md
        # comments below) -- a failure here previously left a committed
        # utterance with, e.g., its old translation UI item gone and no
        # new one requested, with no trace anywhere. Logging only; the
        # swallow behavior itself is intentionally unchanged (these must
        # not crash the store-update path over a UI-side failure).
        try:
            self._remove_interim_line_from_display()
        except Exception as exc:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "STORE_SEGMENT_UPDATE_INTERIM_REMOVAL_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    canonical_utterance_id=canonical_utterance_id,
                    source_version=source_version,
                )
            except Exception:
                pass
        # fixes TASK_8_REPORT.md: same rationale as _on_store_segment_added
        # -- translation submission (and the stale-translation-line removal
        # it depends on) used to run AFTER all transcript-box rendering
        # below, gated behind the same unconditional `if box is None:
        # return`. Neither depends on the transcript box at all (the
        # translated-verse widget is a separate box entirely), so both are
        # moved ahead of rendering and are never skipped by a rendering
        # failure or an unavailable transcript widget.
        #
        # fixes TASK_3A_FINDINGS.md Item 1: same utterance revised -- drop
        # the obsolete translated line by canonical_utterance_id lookup,
        # never by position. Fail-closed: if identity is missing or
        # unmatched, or the tracked item is already a newer source_version,
        # skip and log -- never guess-apply to "whatever is currently last".
        try:
            self._remove_translation_item_for_utterance(
                canonical_utterance_id=canonical_utterance_id,
                source_version=source_version,
            )
        except Exception as exc:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "STORE_SEGMENT_UPDATE_TRANSLATION_REMOVAL_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    canonical_utterance_id=canonical_utterance_id,
                    source_version=source_version,
                )
            except Exception:
                pass
        # Same utterance revised: debounce-replace pending translation (do not
        # enqueue a second permanent job for each provisional Stable update).
        try:
            self.submit_text_for_translation(
                text,
                speaker=speaker,
                replace_pending=True,
                canonical_utterance_id=canonical_utterance_id,
                source_version=source_version,
                source_record_id=source_record_id,
            )
        except Exception as exc:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "STORE_SEGMENT_UPDATE_TRANSLATION_RESUBMIT_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    canonical_utterance_id=canonical_utterance_id,
                    source_version=source_version,
                )
            except Exception:
                pass
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

    def _remove_translation_item_for_utterance(
        self, *, canonical_utterance_id: str, source_version: int
    ) -> bool:
        """Remove the displayed translation line for one canonical utterance.

        fixes TASK_3A_FINDINGS.md Item 1: identity-keyed removal, never
        positional. Fail-closed (Item 4 default applied here too): if the
        utterance id is empty, untracked, or the tracked item is already a
        newer source_version than the revision that triggered this call, do
        nothing and log -- never guess by deleting "whatever is last".
        """
        utterance_key = str(canonical_utterance_id or "")
        registry = getattr(self, "_translation_items_by_utterance", None)
        if not utterance_key or not isinstance(registry, dict):
            self._log_translation_display_skip(
                reason="missing_canonical_utterance_id", canonical_utterance_id=utterance_key
            )
            return False
        item = registry.get(utterance_key)
        if item is None:
            self._log_translation_display_skip(
                reason="no_tracked_translation_item", canonical_utterance_id=utterance_key
            )
            return False
        tracked_version = int(item.get("source_version") or 1)
        incoming_version = int(source_version or 1)
        if incoming_version < tracked_version:
            # A newer version's translation is already displayed; a stale
            # revision must never remove it.
            self._log_translation_display_skip(
                reason="stale_revision_ignored",
                canonical_utterance_id=utterance_key,
                tracked_version=tracked_version,
                incoming_version=incoming_version,
            )
            return False
        mark_name = item.get("mark")
        tbox = getattr(self, "translated_verse_box", None)
        if tbox is not None and mark_name:
            tbox.configure(state="normal")
            try:
                if tbox.compare(mark_name, ">=", "1.0"):
                    self._delete_translation_entry(tbox, mark_name, item)
                tbox.mark_unset(mark_name)
            except Exception:
                pass
            tbox.configure(state="disabled")
        registry.pop(utterance_key, None)
        loading = getattr(self, "_translation_loading_items", None)
        if isinstance(loading, dict):
            seg_id = item.get("segment_id")
            if seg_id is not None:
                loading.pop(int(seg_id), None)
        return True

    def _log_translation_display_skip(self, *, reason: str, **fields) -> None:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("TRANSLATION_DISPLAY_UPDATE_SKIPPED", reason=reason, **fields)
        except Exception:
            pass

    def _update_interim_line_only(self):
        box = self._transcript_box()
        interim_text = (getattr(self, "_latest_interim_text", "") or "").strip()
        if box is None:
            return
        self._interim_log(
            "[INTERIM] update_start",
            {"text_preview": interim_text[:80], "box_end_index": str(box.index("end"))},
        )
        self._remove_interim_line_from_display()
        if not interim_text:
            return
        speaker = getattr(self, "_latest_interim_speaker", 1) or 1
        box.configure(state="normal")
        if hasattr(self, "_clear_text_placeholder"):
            self._clear_text_placeholder(box)
        tag = self._speaker_tag(speaker)
        # The anchor has to sit exactly where the preview will be written, and
        # it has to stay there while the preview is written past it.
        #
        # `"end"` is NOT that position: Tk keeps a final newline of its own, so
        # `"end"` is one character past where `insert("end", ...)` actually
        # puts text. And a mark carries RIGHT gravity by default, which moves
        # it along with text inserted at its position instead of leaving it
        # behind. Between them the preview landed BEFORE the anchor, so
        # `_remove_interim_line_from_display`'s
        # `delete("interim_anchor", "end")` spanned an empty range and removed
        # nothing -- every tick stacked another row. Driven over four ticks
        # against a real tk.Text through these two methods, the pane held four
        # preview rows and four hourglasses where one is correct.
        #
        # `"end-1c"` is the real insertion point and LEFT gravity keeps the
        # mark there. Both halves are required: measured against real Tk, each
        # of `"end"`, `"end-1c"` alone, `"end"` + left, and `"end-1c"` + right
        # still produced three hourglasses over three ticks; only this pair
        # produced one.
        #
        # The newline guard is the third necessary piece, and it is not
        # obvious. `delete("interim_anchor", "end")` consumes Tk's own trailing
        # newline along with the preview, so on the NEXT tick the widget has no
        # empty last line and `"end-1c"` resolves into the middle of the last
        # committed line -- traced directly: index 2.0 on the first tick, 1.19
        # on the second. Anchoring there wrote the preview onto the end of the
        # committed sentence instead of below it. Re-establishing the empty
        # last line first keeps the anchor at a line start on every tick, and
        # costs nothing when one is already there, so no blank line
        # accumulates. Verified across five shapes -- committed text, an empty
        # tick between two previews, an empty widget, several committed lines,
        # and a multi-line preview replaced by a short one -- all giving one
        # hourglass and zero stray blank lines.
        #
        # `tests/test_interim_preview_is_replaced_not_stacked.py` pins the
        # mechanism as well as the symptom.
        if box.index("end-1c") != box.index("end-1c linestart"):
            box.insert("end", "\n")
        box.mark_set("interim_anchor", "end-1c")
        box.mark_gravity("interim_anchor", "left")
        # Item 69: the preview is one growing paragraph -- measured past 2000
        # characters before it settles. Render it as readable 2-3 sentence
        # lines instead. Display-only and inherently safe: the whole preview is
        # deleted (`interim_anchor` -> `end`) and rewritten on every tick, so
        # several lines are removed together and no committed text is touched.
        for _preview_line, _is_last in self._interim_preview_lines(interim_text):
            box.insert("end", self._ui_speaker_label_text(), tag)
            box.insert(
                "end",
                _preview_line + (" ⏳\n" if _is_last else "\n"),
                "body",
            )
        box.configure(state="disabled")
        box.see(tk.END)
        self._interim_log(
            "[INTERIM] update_done",
            {"new_box_end_index": str(box.index("end"))},
        )
        self._last_operation_hint = "interim_update"
        self._refresh_transcript_scrollbar(box)
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("interim_ui_rendered_at", text_len=len(interim_text))
        except Exception:
            pass

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
        # No version number: this is the client's window title, not a build stamp.
        # `APP_VERSION` stays in every diagnostic sink -- run ids, log filenames,
        # artifact manifests -- because that is how a delivered run is traced back
        # to the build it came from.
        self.title(t(APP_WINDOW_TITLE))
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
        self.bind("<Map>", self._on_first_map)

    def _on_first_map(self, event=None):
        """Run the full responsive-layout pass as soon as the window is
        actually on screen, instead of leaving everything at whatever
        `__init__`'s eager widget construction computed.

        Measured: right after `self.geometry("900x650")`, before the window
        manager has mapped the window, `winfo_width()` returns Tk's own
        placeholder size (200 device px on this machine) -- not the 900
        requested, and not the `<= 1` this file's other fallbacks guard
        against. 200 / 1.5 scaling is a 133 design-px window, which is what
        `create_footer()`, the reading panes' initial typography pass, and
        the header's initial (unabbreviated, un-narrowed) construction were
        ALL built for: wrong for whatever size the window actually opens at.

        `<Map>` is the first point `winfo_width()` is guaranteed correct --
        measured 1350 (900 design px) at the same instant this fires, where
        `after(0, ...)` and `after_idle(...)` still both read 200.

        Calling the full `_apply_responsive_layout()` here, not a hand-picked
        subset, is itself a fix: `_mark_real_alpha_interactive_ready` snapshots
        `_last_layout_width`/`_last_layout_mode_applied` from the window's
        real size without ever calling `_apply_header_layout`, and
        `_apply_responsive_layout`'s own `UI_PERFORMANCE_MODE` skip-check
        compares against exactly those two attributes -- so on a normal
        launch where nothing else resizes the window, the header's OWN first
        correction (medium-mode narrowing, the language abbreviation below)
        was skipped forever, not just delayed. An earlier revision of this
        method called `_apply_footer_layout`/`_refresh_reading_typography`
        directly and left the header on this path, which is why the header
        stayed in "wide" styling at any width the window opened at.
        """
        if self._first_map_handled or event is not None and event.widget is not self:
            return
        if self.winfo_width() <= 1:
            return
        self._first_map_handled = True
        try:
            # `_mark_real_alpha_interactive_ready` (main.py's startup-perf
            # path) can snapshot `_last_layout_width`/`_last_layout_mode_applied`
            # from the window's real size WITHOUT ever calling
            # `_apply_header_layout` -- it exists purely to stop this call's
            # OWN `UI_PERFORMANCE_MODE` skip-check from redoing a layout pass
            # that already happened. If that snapshot lands before this
            # handler does, the skip-check would see a "match" and skip the
            # one call that actually applies anything. Invalidating the
            # snapshot first guarantees at least one real pass runs.
            self._last_layout_width = -1
            self._last_layout_mode_applied = None
            self._apply_responsive_layout()
        except Exception as exc:
            print(f"Error correcting first-paint layout: {exc}")

    # -----------------------------------------------------------------------
    # UI style helpers (visual consistency only)
    # -----------------------------------------------------------------------
    def _design_width(self, fallback=DEFAULT_WINDOW_WIDTH):
        """Window width in DESIGN px, which is not what `winfo_width()` returns.

        The design document's breakpoints are CSS px. `winfo_width()` reports
        device pixels, and CustomTkinter runs the window at
        `ScalingTracker.get_widget_scaling` -- measured 1.5 on this display, so
        a window asked for as 900 reports **1350**. Comparing that 1350 against
        a 700 or 1050 threshold answers a different question than the design
        asked: it decides the layout from how many dots the window covers
        rather than from how much content fits, so the same physical window
        lays out differently on a 100% and a 150% machine. Dividing back out is
        what makes a breakpoint mean the same thing on the client's laptop as
        it does here.

        Before the first paint `winfo_width()` is 1, which would read as the
        narrowest possible window and stack everything at startup; the fallback
        is the geometry the window is actually created at.
        """
        try:
            width = self.winfo_width()
            if width <= 1:
                return fallback
            scaling = ctk.ScalingTracker.get_widget_scaling(self) or 1.0
        except Exception:
            return fallback
        if scaling <= 0:
            return fallback
        return int(round(width / scaling))

    def _design_px(self, css_px):
        """Convert a design px to a device pixel at the current display scaling.

        CustomTkinter scales every widget it owns by
        `ScalingTracker.get_widget_scaling`, but the two reading panes are raw
        `tk.Text` widgets and get none of it. Measured on a 150% display: one
        `CTkFont(size=16)` object renders at 18 pt inside a `CTkLabel` and at
        12 pt inside the `tk.Text` -- so today the app's primary content is a
        third smaller than the chrome around it, on exactly the kind of laptop
        the client will use. Applying the same factor here keeps them together.

        Falls back to 1.0 if the tracker is unavailable, which reproduces the
        current behaviour rather than failing.
        """
        try:
            scaling = ctk.ScalingTracker.get_widget_scaling(self)
        except Exception:
            scaling = 1.0
        if not scaling or scaling <= 0:
            scaling = 1.0
        return max(1, int(round(css_px * scaling)))

    def _apply_reading_typography(self, text_widget, role, stacked):
        """Style one reading pane from the design's type scale. Item 71.

        Every option written here -- `font`, `padx`, `pady`, `spacing1/2/3`,
        `background`, `foreground` -- changes only where glyphs land. None of
        them adds a logical line, so the render cap's `delete("1.0", "2.0")`
        and the revision paths' `mark lineend` arithmetic behave exactly as
        before; only wrap points move.

        `spacing2` (the gap between the wrapped display lines of one
        paragraph) is derived rather than tabulated. The design states a
        line-height *ratio*, and the space Tk needs to add on top is the
        difference between that target and the font's own measured linespace,
        which depends on the family, the size and the current DPI. Hardcoding
        it would be right on this machine only.
        """
        if text_widget is None:
            return
        spec = READING_TYPOGRAPHY.get((role, bool(stacked)))
        if spec is None:
            return
        try:
            # A CustomTkinter font size is already in pixels, so the design's
            # `font-size: 18px` is `size=18`; only display scaling is applied.
            font = self._ui_font(self._design_px(spec["font_px"]))

            linespace = 0
            try:
                linespace = int(font.metrics("linespace"))
            except Exception:
                linespace = 0
            target_line_px = self._design_px(spec["font_px"] * spec["line_height"])
            spacing2 = max(0, target_line_px - linespace) if linespace else 0

            pane_bg = PANE_BG.get(role, COLORS["card_bg_soft"])
            # Paragraph spacing goes on the WIDGET, not on the `body` tag. Tk
            # resolves `spacing1` from the tags on the first character of a
            # display line, and every rendered entry starts with the
            # `speaker_label` tag -- so a `body`-tagged spacing1 would silently
            # apply to nothing on exactly the lines it was meant for.
            text_widget.configure(
                font=font,
                padx=self._design_px(spec["pad_x_px"]),
                pady=self._design_px(spec["pad_top_px"]),
                bg=pane_bg,
                spacing1=self._design_px(spec["space_above_px"]),
                spacing2=spacing2,
                spacing3=self._design_px(spec["space_below_px"]),
            )
            # The tk.Text shares its rectangle with a CTkFrame and a CTkScrollbar.
            # Recolouring only the text would leave a visible band of the old
            # card colour down the right edge and around the border.
            for sibling in (
                getattr(text_widget, "_pane_frame", None),
                getattr(text_widget, "_scrollbar", None),
            ):
                if sibling is not None:
                    try:
                        sibling.configure(fg_color=pane_bg)
                    except Exception:
                        pass
            # The live "listening" preview is a quieter, smaller row in the
            # design (`.atf-incoming-entry`, 12px, tighter padding). It covers
            # its whole display line, so its own spacing1 does win here.
            text_widget.tag_configure(
                "interim",
                font=self._ui_font(self._design_px(INTERIM_FONT_PX)),
                foreground=COLORS["text_muted"],
                spacing1=self._design_px(INTERIM_SPACE_ABOVE_PX),
                spacing3=self._design_px(INTERIM_SPACE_BELOW_PX),
            )
            # Four sites build `speaker_label` lazily with a hardcoded
            # `("Segoe UI", 12, "bold")` -- a raw Tk tuple, so 12 *points*,
            # neither a design px nor scaled. It happened to match the old
            # body size exactly; now that the body follows the design it would
            # be left behind as a mismatched inline prefix. Configuring it here
            # wins because all four sites are guarded by
            # `if tag not in box.tag_names()`.
            text_widget.tag_configure(
                "speaker_label",
                font=self._ui_font(
                    self._design_px(SPEAKER_LABEL_FONT_PX), "bold"
                ),
                foreground=COLORS["text_primary"],
            )
        except Exception as exc:
            # Typography is cosmetic; a failure here must not stop the pane
            # from rendering text.
            print(f"Error applying reading typography ({role}): {exc}")

    def _refresh_reading_typography(self, design_width=None):
        """Re-apply the design's type scale after a width change.

        The design has a single mobile branch (`@media (max-width: 700px)`),
        which is the same threshold that turns the reading columns into rows,
        so one comparison decides both. It is made against the design width,
        not `winfo_width()`, for the reason spelled out in `_design_width`.
        """
        if design_width is None:
            design_width = self._design_width()
        stacked = design_width < CONTENT_STACK_BREAKPOINT
        if stacked == getattr(self, "_reading_typography_stacked", None):
            return
        self._reading_typography_stacked = stacked
        self._apply_reading_typography(
            getattr(self, "translated_verse_box", None), "translation", stacked
        )
        self._apply_reading_typography(
            getattr(self, "initial_verse_box", None), "transcript", stacked
        )

    def _ui_font(self, size, weight="normal", slant="roman"):
        """Return a CTkFont in the design's family, falling back if absent."""
        cache = getattr(self, "_font_cache", None)
        if cache is None:
            self._font_cache = {}
            cache = self._font_cache
        key = (size, weight or "normal", slant or "roman")
        cached = cache.get(key)
        if cached is not None:
            return cached
        for family in (FONT_FAMILY, FONT_FAMILY_FALLBACK, "Segoe UI"):
            try:
                kwargs = {"family": family, "size": size}
                if weight and weight != "normal":
                    kwargs["weight"] = weight
                if slant and slant != "roman":
                    kwargs["slant"] = slant
                font = ctk.CTkFont(**kwargs)
                cache[key] = font
                return font
            except Exception:
                continue
        font = ctk.CTkFont(family="Segoe UI", size=size)
        cache[key] = font
        return font

    def _scaled_design_font(self, font_spec):
        """Rebuild a `(family, size[, style])` theme tuple at display scaling.

        Only the reading panes need this: they are raw `tk.Text` widgets, so
        CustomTkinter never scales anything handed to them. Without it the
        placeholder would draw at its literal pixel size beside body text that
        has been scaled, which on a 150% display is the first mismatch the
        user sees -- the panes are empty at launch.
        """
        try:
            family = font_spec[0]
            size = self._design_px(font_spec[1])
            style = font_spec[2] if len(font_spec) > 2 else ""
            weight = "bold" if "bold" in style else "normal"
            slant = "italic" if "italic" in style else "roman"
            if family in (FONT_FAMILY, FONT_FAMILY_FALLBACK, "Segoe UI"):
                return self._ui_font(size, weight, slant)
            return ctk.CTkFont(
                family=family, size=size, weight=weight, slant=slant
            )
        except Exception:
            return font_spec

    def _language_flag_label(self, plain_language):
        """Return display label with country flag for a plain language name."""
        return LANGUAGE_FLAG_LABELS.get(plain_language, plain_language)

    def _header_language_label(self, plain_language, abbreviated=False):
        """Header combobox display text -- "JP"/"EN" when the header does not
        have room for the full name, otherwise the same label used elsewhere.

        Display-only. `self.source_language`/`self.target_language` -- read
        everywhere else in this file, and by translation routing -- always
        hold the full name; `on_select` inside `_make_language_combo`
        translates a header selection back to it via `_strip_language_flag`
        before writing to either variable.
        """
        if abbreviated and plain_language in LANGUAGE_ABBREVIATIONS:
            return LANGUAGE_ABBREVIATIONS[plain_language]
        return self._language_flag_label(plain_language)

    def _strip_language_flag(self, display_value):
        """Convert a flagged or abbreviated dropdown label back to the plain
        language name."""
        if not display_value:
            return display_value
        for plain, flagged in LANGUAGE_FLAG_LABELS.items():
            if display_value == flagged:
                return plain
        for plain, short in LANGUAGE_ABBREVIATIONS.items():
            if display_value == short:
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
        """Language dropdown with flag labels; returns (wrapper_frame, combo).

        Built at the default (non-abbreviated) width: this runs during
        `__init__`, before the window's real size is known, and the app opens
        wide enough that abbreviation is not needed. `_set_header_language_abbreviated`
        corrects it once the responsive layout knows better, the same
        eager-build-then-correct pattern as the footer.
        """
        combo_values = list(plain_values)

        def on_select(choice):
            plain = self._strip_language_flag(choice)
            if variable.get() != plain:
                # `source_language` / `target_language` carry a write trace that
                # calls `on_language_change` already, and a Tk trace fires
                # synchronously inside `set`. Calling it again here as well ran
                # the whole handler TWICE for every selection -- visible in the
                # 2nd-PC console log as paired `LANGUAGE_DROPDOWN_CHANGED` and
                # `Listening to dropdown:` lines, and re-resolving the Deepgram
                # language each time.
                variable.set(plain)
            else:
                # Re-picking the value already selected writes nothing, so the
                # trace stays quiet and this is the only thing that would run.
                self.on_language_change(changed_key)

        wrapper = ctk.CTkFrame(
            master=master,
            **self._language_dropdown_wrapper_config(width=DROPDOWN_WIDTH),
        )
        wrapper.pack_propagate(False)

        combo = ctk.CTkComboBox(
            master=wrapper,
            values=self._flagged_language_values(combo_values),
            command=on_select,
            **self._header_glass_combo_config(),
        )
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_rowconfigure(0, weight=1)
        inset = DROPDOWN_WRAPPER_BORDER_WIDTH
        combo.grid(row=0, column=0, sticky="nsew", padx=inset, pady=inset)
        combo.set(self._language_flag_label(variable.get()))
        combo._alpha_plain_values = combo_values
        return wrapper, combo

    def _sync_language_combo_displays(self):
        """Refresh header language dropdown text from the canonical variables,
        at whichever width mode (full name / abbreviated) is currently set."""
        abbreviated = getattr(self, "_header_lang_abbreviated", False)
        if hasattr(self, "source_combo") and self.source_combo is not None:
            self.source_combo.set(
                self._header_language_label(self.source_language.get(), abbreviated)
            )
        if hasattr(self, "target_combo") and self.target_combo is not None:
            self.target_combo.set(
                self._header_language_label(self.target_language.get(), abbreviated)
            )

    def _set_header_language_abbreviated(self, abbreviated):
        """Switch the header's two language combos between full names
        ("Japanese"/"English") and abbreviations ("JP"/"EN").

        Item 71 Phase 3e. Reported with two screenshots: shrinking the header
        into "medium" mode pushed the swap toggle and Meeting Summary button
        off-screen even after `_pack_header_controls`'s existing narrowing
        (128px combo boxes, "Meeting Summary" -> "Summary"). The combo boxes'
        OWN width is the fix -- "JP"/"EN" needs roughly a third of what
        "Japanese"/"English" needs, which is what actually frees the row.

        Only the two combo boxes' displayed VALUES change here.
        `self.source_language`/`self.target_language` are untouched --
        `on_select` inside `_make_language_combo` already translates a
        selection back to the full name via `_strip_language_flag` before
        writing to either, abbreviated or not, so nothing downstream (
        translation routing, `on_language_change`, export, summary) needs to
        know this state exists.
        """
        if abbreviated == getattr(self, "_header_lang_abbreviated", None):
            return
        self._header_lang_abbreviated = abbreviated
        for combo, variable in (
            (getattr(self, "source_combo", None), self.source_language),
            (getattr(self, "target_combo", None), self.target_language),
        ):
            if combo is None:
                continue
            plain_values = getattr(combo, "_alpha_plain_values", None)
            if plain_values:
                combo.configure(
                    values=[
                        self._header_language_label(name, abbreviated)
                        for name in plain_values
                    ]
                )
            combo.set(self._header_language_label(variable.get(), abbreviated))

    # -----------------------------------------------------------------------
    # Logo
    # -----------------------------------------------------------------------
    def _apply_window_identity(self):
        """Show Alpha's icon in the title bar and the taskbar, not Python's.

        The packaged app runs on `pythonw.exe`, so without this Windows shows
        the Python logo -- the operator has no reason to know Python is
        involved, and it looks like the wrong program.

        BOTH steps are needed. `iconbitmap` fixes the window; the taskbar button
        is grouped by Application User Model ID, which defaults to the host
        interpreter, so `pythonw.exe` keeps its own icon and grouping there
        until an explicit ID is set.

        Never raises: a missing icon is cosmetic, and this runs before the UI
        exists.
        """
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Wicresoft.AlphaLiveTranslator"
            )
        except Exception:
            pass
        try:
            icon_path = ASSETS_DIR / "alpha.ico"
            if icon_path.is_file():
                self.iconbitmap(default=str(icon_path))
        except Exception:
            pass

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
            text=t("Meeting Assistant"),
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
            text=t(MEETING_SUMMARY_BUTTON_TEXT),
            command=self.show_meeting_summary,
            **self._glass_button_config(width=SUMMARY_BUTTON_WIDTH),
        )
        self.summary_button.pack(side="left", padx=(0, 8))

        self.always_on_top_switch = ctk.CTkSwitch(
            master=self.right_header_cluster,
            text=t("Always on Top"),
            font=self._ui_font(FONTS["caption"][1]),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["input_bg"],
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["text_primary"],
            button_hover_color="#e2e8f0",
            command=self.toggle_always_on_top,
        )
        self.always_on_top_switch.pack(side="left")

        # The UI language, as a button rather than a combo box, and the reason
        # is measured: at 800 design px -- the width where the hamburger menu
        # takes over -- this cluster has 49 design px left in Japanese, while a
        # CTkComboBox asks for 72. This button asks for 28 ("EN") or 37
        # ("日本"). Clicking it posts a real dropdown, which Tk draws over the
        # window and so costs no layout space at all.
        ui_language_button_config = self._glass_button_config(width=1)
        ui_language_button_config["font"] = self._ui_font(FONTS["caption"][1])
        self.ui_language_button = ctk.CTkButton(
            master=self.right_header_cluster,
            text=UI_LANGUAGE_SHORT_LABELS.get(get_language(), "EN"),
            command=self._open_ui_language_menu,
            **ui_language_button_config,
        )
        self.ui_language_button.pack(side="left", padx=(8, 0))

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

        self.menu_listening_label = ctk.CTkLabel(
            master=self.menu_dropdown_frame,
            text=t("Listening to:"),
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.menu_listening_label.pack(fill="x", padx=15, pady=(12, 4))

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

        self.menu_translate_label = ctk.CTkLabel(
            master=self.menu_dropdown_frame,
            text=t("Translate to:"),
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.menu_translate_label.pack(fill="x", padx=15, pady=(4, 4))

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

        # The same setting as the header button, in the surface that replaces
        # the header below 800 px. There is room for the full names here -- the
        # panel is 260 wide -- so this one says "English" and "日本語" rather
        # than the header's abbreviations.
        self.menu_ui_language_label = ctk.CTkLabel(
            master=self.menu_dropdown_frame,
            text=t("Display language:"),
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.menu_ui_language_label.pack(fill="x", padx=15, pady=(4, 4))

        self.ui_language_combo_menu = ctk.CTkComboBox(
            master=self.menu_dropdown_frame,
            values=self._ui_language_combo_values(),
            command=self._on_ui_language_combo,
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
        self.ui_language_combo_menu.pack(fill="x", padx=15, pady=(0, 8))

        # Created eagerly like every other widget in this file (C2), but never
        # shown: Start/Stop lives in the footer only, at every width. This
        # used to be packed and visible alongside the footer's own Start
        # button whenever the hamburger menu was open -- the duplicate
        # control the user reported.
        self.listen_button_menu = ctk.CTkButton(
            master=self.menu_dropdown_frame,
            text=t("Start Listening"),
            height=FOOTER_BTN_HEIGHT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.toggle_listening,
        )
        self.listen_button_menu._alpha_text_source = "Start Listening"

        # Copy Translation / Export / Clear: visible here only when
        # `_apply_footer_layout` finds they no longer fit their half of the
        # footer row on one line and removes them from it instead of wrapping
        # a second line. `_sync_hamburger_action_buttons` packs them; start
        # hidden, matching the footer having enough room at the app's default
        # 900px width.
        self.copy_translation_btn_menu = ctk.CTkButton(
            master=self.menu_dropdown_frame,
            text=t("Copy Translation"),
            command=self.copy_translation_to_clipboard,
            **self._glass_button_config(),
        )
        self.copy_translation_btn_menu.pack(fill="x", padx=15, pady=(4, 8))
        self.copy_translation_btn_menu.pack_forget()

        self.export_btn_menu = ctk.CTkButton(
            master=self.menu_dropdown_frame,
            text=t("Export"),
            command=self.export_transcript_placeholder,
            **self._glass_button_config(),
        )
        self.export_btn_menu.pack(fill="x", padx=15, pady=(4, 8))
        self.export_btn_menu.pack_forget()

        self.clear_btn_menu = ctk.CTkButton(
            master=self.menu_dropdown_frame,
            text=t("Clear"),
            command=self.clear_text,
            **self._glass_button_config(),
        )
        self.clear_btn_menu.pack(fill="x", padx=15, pady=(4, 8))
        self.clear_btn_menu.pack_forget()
        self._hamburger_actions_visible = False

        self.always_on_top_switch_menu = ctk.CTkSwitch(
            master=self.menu_dropdown_frame,
            text=t("Always on Top"),
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["dropdown_bg"],
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["text_primary"],
            button_hover_color="#e0e0e0",
            command=self.toggle_always_on_top,
        )
        self.always_on_top_switch_menu.pack(anchor="w", padx=15, pady=(4, 12))

        self.mic_switch_menu = ctk.CTkCheckBox(
            master=self.menu_dropdown_frame,
            text=t("Mic off"),
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"],
            border_color=COLORS["border"],
            checkmark_color=COLORS["text_primary"],
            command=self.toggle_microphone_capture,
        )
        self.mic_switch_menu.pack(anchor="w", padx=15, pady=(0, 12))
        # ON means the microphone IS captured, which is off by default. Applied
        # to both at once, once both exist.
        self._sync_mic_switches()
        self._sync_ui_language_controls()

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
        """Apply header, content, footer, and status bar layout for current width.

        Every threshold this function and everything it calls compares
        against (`LAYOUT_WIDE_BREAKPOINT`, `LAYOUT_HAMBURGER_BREAKPOINT`,
        `LAYOUT_MEDIUM_BREAKPOINT`, the header's own 560/480/460) is a design
        px value, so the width fed into them must be too -- see
        `_design_width`. This used to read `self.winfo_width()` directly
        (device px), which is why the header's mode switches happened at the
        wrong physical window size on any scaled display: on this machine's
        150% display, `LAYOUT_WIDE_BREAKPOINT` (1050) was effectively being
        enforced at a 700 design-px window instead of 1050. The footer and
        reading grid had the identical bug and were fixed the same way
        earlier in item 71; the header was missed because it is driven by
        this function rather than calling `_design_width()` itself.
        """
        try:
            design_width = self._design_width()
            if design_width <= 1:
                return

            mode = self._get_layout_mode(design_width)
            if (
                UI_PERFORMANCE_MODE
                and design_width == getattr(self, "_last_layout_width", -1)
                and mode == getattr(self, "_last_layout_mode_applied", None)
            ):
                return
            self._last_layout_width = design_width
            if mode != self._layout_mode:
                self._layout_mode = mode
            self._last_layout_mode_applied = mode

            pad_x = (
                SPACING["window_pad_compact_x"]
                if design_width < LAYOUT_MEDIUM_BREAKPOINT
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

            # Slice layout across event-loop turns so post-paint UI stays under 500ms.
            self._apply_header_layout(design_width, mode)
            self.after(
                1,
                lambda m=mode, w=design_width: self._apply_responsive_layout_tail(m, w),
            )
            return
        except Exception as exc:
            print(f"Error applying responsive layout: {exc}")

    def _apply_responsive_layout_tail(self, mode, width):
        """Second slice of responsive layout (content/footer/status).

        `width` is `design_width` from the caller (`_apply_responsive_layout`)
        -- CSS px, not `winfo_width()`'s device px; see `_design_width`. The
        reading grid, the footer and the type scale each re-ask
        `_design_width()` directly anyway rather than trust the parameter,
        the same defensive redundancy `_apply_status_bar_layout` further down
        this chain does not have, since it is a single-hop call.
        """
        try:
            design_width = self._design_width()
            self._apply_content_layout(mode, design_width=design_width)
            self._refresh_reading_typography(design_width)
            self.after(
                1, lambda m=mode, w=width: self._apply_responsive_layout_tail2(m, w)
            )
        except Exception as exc:
            print(f"Error applying responsive layout: {exc}")

    def _apply_responsive_layout_tail2(self, mode, width):
        try:
            self._apply_footer_layout(self._design_width())
            self._apply_status_bar_layout(width)
            self._schedule_waveform_layout()
        except Exception as exc:
            print(f"Error applying responsive layout: {exc}")
        # Let Tk settle before measuring: reading widths in the same turn that
        # re-gridded them reports the old ones.
        try:
            self.after(120, lambda m=mode: self._record_layout_snapshot(m))
        except Exception:
            pass

    # Controls whose position or visibility a layout complaint is ever about,
    # and the container each one has to stay inside.
    _SNAPSHOT_CONTROLS = (
        ("header_frame", (
            "header_lang_frame", "summary_button", "always_on_top_switch",
            "ui_language_button", "hamburger_button", "brand_sub_label",
        )),
        ("status_bar_frame", ("live_indicator", "status_text_label", "mic_switch",
                              "signal_label", "timer_label")),
        ("footer_frame", ("listen_button", "copy_translation_btn", "export_btn",
                          "clear_btn")),
    )

    def _record_layout_snapshot(self, mode):
        """Write what the window ACTUALLY looks like into the run's evidence.

        Every layout complaint so far has cost several rounds of screenshots,
        because a diagnostic bundle carried no geometry at all -- grep a bundle
        for a window size, a screen size, a scaling factor or a layout mode and
        there is nothing. So a user who can see the problem has no way to hand
        it over, and this end can only re-measure widths it already believes are
        fine.

        This records the numbers instead: the screen, the window, the scaling
        CustomTkinter is running at, the design width the breakpoints were
        compared against, and for every control a layout bug could be about,
        whether it is on screen, how wide it is, how wide it asked to be, and
        how far past its container's right edge it sits. `past_edge > 0` or
        `w < req` IS the bug, stated as a number.

        Written once per real layout change -- `_apply_responsive_layout`
        already returns early when nothing changed -- so a long session leaves a
        handful of rows, not a stream.

        Never raises. Diagnostics that can break the UI they are diagnosing are
        worse than no diagnostics.
        """
        try:
            from alpha.utils.evidence_jsonl import append_jsonl_named

            try:
                scaling = float(ctk.ScalingTracker.get_widget_scaling(self) or 1.0)
            except Exception:
                scaling = 1.0

            payload = {
                "event": "layout_snapshot",
                "mode": mode,
                "design_width": self._design_width(),
                "scaling": scaling,
                "ui_language": get_language(),
                "screen": {
                    "width": self.winfo_screenwidth(),
                    "height": self.winfo_screenheight(),
                },
                "window": {
                    "device_width": self.winfo_width(),
                    "device_height": self.winfo_height(),
                    # Which monitor the window is on: a second screen starts
                    # beyond the first one's width, so x alone identifies it.
                    "x": self.winfo_rootx(),
                    "y": self.winfo_rooty(),
                    "state": str(self.state()),
                },
                "controls": {},
            }

            for container_name, control_names in self._SNAPSHOT_CONTROLS:
                container = getattr(self, container_name, None)
                try:
                    edge = container.winfo_rootx() + container.winfo_width()
                except Exception:
                    edge = None
                for name in control_names:
                    widget = getattr(self, name, None)
                    if widget is None:
                        continue
                    try:
                        mapped = bool(widget.winfo_ismapped())
                        entry = {
                            "in": container_name,
                            "mapped": mapped,
                            "w": widget.winfo_width(),
                            "req": widget.winfo_reqwidth(),
                        }
                        if mapped and edge is not None:
                            entry["past_edge"] = (
                                widget.winfo_rootx() + widget.winfo_width()
                            ) - edge
                        payload["controls"][name] = entry
                    except Exception:
                        continue

            # The registered key from `_HEALTH_NAME_MAP`, not the filename --
            # `get_health_path` raises KeyError on an unregistered name, and the
            # guard below would have swallowed it into silence.
            append_jsonl_named("health", "layout_snapshot", payload)
        except Exception:
            return

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
        """Layout header language controls for wide or medium layouts.

        `width` is design px (see `_apply_responsive_layout`).

        In "medium" mode, "Japanese"/"English" was measured to overflow the
        header row far enough to push the swap toggle and Meeting Summary
        button off-screen even after this function's existing narrowing
        (128px combo boxes, "Meeting Summary" -> "Summary") -- reported with
        two screenshots. `_set_header_language_abbreviated` switches the
        combo boxes to "JP"/"EN", which needs roughly a third of the space
        and is what actually frees the row; the combo box itself can then be
        narrower too.
        """
        if self.header_lang_frame is None:
            return

        abbreviated = mode != "wide"
        self._set_header_language_abbreviated(abbreviated)

        combo_width = DROPDOWN_WIDTH if mode == "wide" else (72 if abbreviated else 128)
        for wrapper, combo in (
            (self.source_combo_wrap, self.source_combo),
            (self.target_combo_wrap, self.target_combo),
        ):
            if wrapper is not None:
                wrapper.configure(width=combo_width, height=DROPDOWN_HEIGHT)

        if mode == "wide":
            self.summary_button.configure(
                text=t(MEETING_SUMMARY_BUTTON_TEXT),
                width=SUMMARY_BUTTON_WIDTH,
            )
        else:
            self.summary_button.configure(
                text=t("Summary"),
                width=108 if width < LAYOUT_MEDIUM_BREAKPOINT + 80 else SUMMARY_BUTTON_WIDTH - 20,
            )

        if width >= 560:
            self.always_on_top_switch.pack(side="left")
        else:
            self.always_on_top_switch.pack_forget()

        # No width test of its own. This function only runs above
        # `LAYOUT_HAMBURGER_BREAKPOINT`, and below it `show_compact_layout`
        # hides this button along with every other header control.
        #
        # An overlap was tried here -- the hamburger arriving before the button
        # left -- and MEASURED NOT TO FIT: with both packed the real window
        # wants 32 device px more header than it has at 900, and 62 at 880. It
        # only fits from 940 up, which would make the hamburger appear at 999
        # and vanish again at 939. One threshold stays.
        if self.ui_language_button is not None:
            self.ui_language_button.pack(side="left", padx=(8, 0))

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
            if self.ui_language_button is not None:
                self.ui_language_button.pack_forget()
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
            text=t("○ IDLE"),
            font=ctk.CTkFont(family=FONTS["status"][0], size=FONTS["status"][1], weight="bold"),
            text_color=COLORS["live_idle"],
        )
        self.live_indicator.pack(padx=10, pady=3)
        self.live_indicator._alpha_text_source = "○ IDLE"

        self.status_text_label = ctk.CTkLabel(
            master=live_wrap,
            text=t("Ready to listen"),
            font=ctk.CTkFont(family=FONTS["body"][0], size=FONTS["body"][1]),
            text_color=COLORS["text_secondary"],
        )
        self.status_text_label.pack(side="left")
        self.status_text_label._alpha_text_source = "Ready to listen"

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
            text=t("● Standby"),
            font=ctk.CTkFont(family=FONTS["caption"][0], size=FONTS["caption"][1]),
            text_color=COLORS["text_muted"],
        )
        self.signal_label.pack(side="right", padx=(0, 8))
        self.signal_label._alpha_text_source = "● Standby"

        # The microphone choice lives here rather than in the header. It was in
        # the header until item 88c, where it cost that row more width than it
        # had: at 800 design px in Japanese it was already 5 px past the right
        # edge before anything else was added. The status strip has room at
        # every width, so this control needs no breakpoint to hide behind, and
        # it now sits beside the session state it belongs with.
        # Packed LAST of the three, all side="right", which puts it leftmost --
        # to the left of the standby indicator.
        self.mic_switch = ctk.CTkCheckBox(
            master=self._status_right_cluster,
            text=t("Mic off"),
            font=self._ui_font(FONTS["caption"][1]),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"],
            border_color=COLORS["border"],
            checkmark_color=COLORS["text_primary"],
            checkbox_width=16,
            checkbox_height=16,
            width=1,
            command=self.toggle_microphone_capture,
        )
        self.mic_switch.pack(side="right", padx=(0, 12))

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
        self._sync_connection_indicator()
        self._timer_job = self.after(1000, self._update_timer)

    # Item 47's label text per state. `describe_connection` owns the DECISION;
    # this owns only how it is spelled in a narrow header label.
    _CONNECTION_INDICATOR_TEXT = {
        "connected": ("● Signal OK", "accent_green"),
        "reconnecting": ("● Reconnecting", "accent_red_glow"),
        "degraded": ("● Translation degraded", "accent_red_glow"),
        "failed": ("● Key rejected", "accent_red"),
    }

    def _sync_connection_indicator(self, *, force_idle: bool = False):
        """Single owner of `signal_label`, driven by `describe_connection`.

        Item 47, WIRED. `service_status.describe_connection` shipped 2026-08-12
        and was reopened 2026-08-16 because **nothing in the application ever
        called it** -- there was no status indicator at all. Meanwhile
        `signal_label` was written from four places with two hardcoded strings,
        "● Signal OK" and "● Standby", that reflected only whether a session was
        running: a dead socket, a reconnect in flight and a rejected API key all
        showed "Signal OK".

        Those four writers now route here, so there is exactly one writer
        (§0 rule 2), the same single-owner shape as item 81's
        `_sync_transcript_visibility`. `force_idle` is for the stop/finalise
        paths, where `is_listening` can still be True while the session is
        already winding down and "Connected." would be a lie.

        The severity ordering is deliberately NOT re-derived here -- it lives in
        `describe_connection`, where `reconnecting` outranks `degraded` because
        degraded means only translation is failing while the transcript still
        flows, whereas reconnecting means the transcript itself has stopped.
        """
        label = getattr(self, "signal_label", None)
        if label is None:
            return
        try:
            from alpha.utils.service_status import describe_connection

            listening = bool(getattr(self, "is_listening", False)) and not force_idle
            worker = getattr(self, "translation_worker", None)
            try:
                gap_seconds = float(self.deepgram_gap_seconds())
            except Exception:
                gap_seconds = 0.0
            status = describe_connection(
                listening=listening,
                # `_dg_disconnected_at` is the authoritative outage clock: set
                # on an UNEXPECTED close only, cleared once the socket is
                # genuinely back. Truthier than "is there a ws object".
                deepgram_connected=not float(
                    getattr(self, "_dg_disconnected_at", 0.0) or 0.0
                ),
                deepgram_reconnecting=bool(getattr(self, "_dg_reconnecting", False)),
                deepgram_auth_failed=bool(getattr(self, "_dg_auth_failed", False)),
                translation_degraded=bool(getattr(worker, "degraded", False)),
                translation_status_message=str(
                    getattr(worker, "status_message", "") or ""
                ),
                gap_seconds=gap_seconds,
                # Item 73's watcher sets this from its own 2s thread. It is
                # a signal here, not a paint: the socket stays healthy when
                # Windows moves the default output, so without it the
                # indicator reports "Signal OK" over a device nothing is
                # routed to.
                audio_device_changed=bool(
                    getattr(self, "_audio_device_changed", False)
                ),
                # Named so "switch back" is actionable. Capture binds at Start
                # and never follows, so the operator has to know WHICH device
                # this session is on -- without it the advice sent one live
                # test to the wrong device.
                audio_capture_device=str(
                    getattr(self, "_diag_wasapi_device_name", "") or ""
                ),
            )
        except Exception:
            # A status indicator must never be able to break the UI tick it
            # rides on. Leave whatever is on screen.
            return
        if not listening:
            text, color_key = "● Standby", "text_muted"
        else:
            text, color_key = self._CONNECTION_INDICATOR_TEXT.get(
                status.state, ("● Signal OK", "accent_green")
            )
            # A device change is ranked AT `reconnecting` severity, but nothing
            # is reconnecting -- the socket is fine and the capture device is
            # simply recording something nothing is routed to any more. Naming
            # the state after its severity would tell the operator to wait for
            # a recovery that will never come. The severity still lives in
            # `describe_connection`; only the wording is chosen here, and only
            # when that is the reason the state was raised.
            if status.state == "reconnecting" and status.detail.get(
                "audio_device_changed"
            ):
                text = "● Audio device changed"
        try:
            self._set_dynamic_text(label, text, text_color=COLORS[color_key])
        except Exception:
            return
        # Surface the actionable sentence once per TRANSITION, not once per
        # tick: this runs every second, and republishing an unchanged problem
        # would bury everything else in the error surface.
        previous = getattr(self, "_connection_indicator_state", None)
        if status.state != previous:
            self._connection_indicator_state = status.state
            if listening and status.state != "connected":
                try:
                    self.publish_error_event(
                        status.message,
                        source="connection",
                        recoverable=status.state != "failed",
                    )
                except Exception:
                    pass

    def _update_status_bar(self, listening=False):
        """Refresh status bar visuals for idle vs listening."""
        if self.live_indicator is None:
            return
        if listening:
            self._set_dynamic_text(
                self.live_indicator, "● LIVE", text_color=COLORS["live_glow"]
            )
            if self.live_pill is not None:
                self.live_pill.configure(
                    fg_color=COLORS["accent_red_soft"],
                    border_color=COLORS["accent_red"],
                )
            self._set_dynamic_text(
                self.status_text_label,
                "Listening — capturing audio",
                text_color=COLORS["text_primary"],
            )
            # Item 47: routed through the single owner. This used to say
            # "Signal OK" for the whole session regardless of what the
            # connection was actually doing.
            self._sync_connection_indicator()
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
            self._set_dynamic_text(
                self.live_indicator, "○ IDLE", text_color=COLORS["live_idle"]
            )
            if self.live_pill is not None:
                self.live_pill.configure(
                    fg_color=COLORS["status_active_bg"],
                    border_color=COLORS["border_soft"],
                )
            self._set_dynamic_text(
                self.status_text_label,
                "Ready to listen",
                text_color=COLORS["text_secondary"],
            )
            self._sync_connection_indicator(force_idle=True)
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
        self._apply_content_layout(design_width=self._design_width())

    def hide_summary_panel(self):
        """Instantly hide the right-side Meeting Summary column."""
        if self.right_column is None:
            return
        self.summary_panel_visible = False
        self._apply_content_layout(design_width=self._design_width())

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
            text=t(SUMMARY_PANEL_TITLE),
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
        self.summary_body_box.insert("1.0", t(PLACEHOLDER_SUMMARY))
        self.summary_body_box.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Main content
    # -----------------------------------------------------------------------
    def _build_content_column(self, column, padx):
        """One reading column whose width is decided by its weight, not its content.

        Two measured floors otherwise make the 70/30 ratio unreachable, and
        both are invisible in source:

        * A `CTkFrame` defaults to `width=200`, which CustomTkinter scales, so
          an "empty" column already demands 300 px on a 150% display. Two of
          them reserve 600 px before any weight is applied.
        * `_create_styled_text` builds its `tk.Text` with no `width=`, and Tk's
          default is **80 characters** -- measured `winfo_reqwidth()` of 644 px.

        Tk's grid gives a column at least its children's requested width and
        only distributes what is left over according to `weight`, so with those
        floors in place the panes measured **49/51** in a 900 px window -- the
        transcript as wide as the translation -- and **31/69** at 700 px, where
        the reference pane came out more than twice the primary one.

        `grid_propagate(False)` plus a 1 px request is what fixes it: the frame
        stops asking for its children's size and takes exactly what the grid
        assigns, so the weights alone decide. Measured after the change:
        **70.0/30.0 at 700, 900, 1200 and 1400 px.** The `tk.Text` default stops
        mattering entirely, which is why it is left alone.

        The children inside still resize normally -- every one of them is
        gridded `sticky="nsew"` under a weighted row and column.
        """
        frame = ctk.CTkFrame(
            self.content_wrapper, fg_color="transparent", width=1, height=1
        )
        frame.grid(row=0, column=column, sticky="nsew", padx=padx)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_propagate(False)
        return frame

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
        # Item 71. Three side-by-side columns instead of two stacked panes:
        #
        #   col 0  translation  weight 70  always visible   (primary)
        #   col 1  transcript   weight 30  hidden by default (reference)
        #   col 2  summary      weight 30  hidden by default
        #
        # Matches the design's `.atf-reading-grid`
        # (`minmax(0, 70fr) 8px minmax(220px, 30fr)`) and its
        # `.atf-original-hidden` state, which collapses to a single column.
        # Weights are re-derived in `_apply_content_layout`; the values here
        # only cover the first paint before any resize event arrives.
        self.content_wrapper.grid_columnconfigure(0, weight=CONTENT_PRIMARY_WEIGHT)
        self.content_wrapper.grid_columnconfigure(1, weight=0)
        self.content_wrapper.grid_columnconfigure(2, weight=0)
        self.content_wrapper.grid_rowconfigure(0, weight=1)

        # `left_column` keeps its name and its role as the primary pane, but
        # now holds ONLY the translation. Renaming it would touch 12 direct
        # dereferences for no behavioural gain.
        self.left_column = self._build_content_column(0, padx=(0, 8))
        self.transcript_column = self._build_content_column(1, padx=(8, 0))
        self.right_column = self._build_content_column(2, padx=(8, 0))

        self.paned = self.left_column

        # `attr_name` is deliberately unchanged on both. `_transcript_box()`
        # returns `initial_verse_box` and is the target of every transcript
        # insert; swapping the names here would route transcript text into the
        # translation widget, which the widget-read export fallback then writes
        # into the delivered file as if it were translation output.
        self.initial_verse_frame = self._create_verse_section(
            master=self.transcript_column,
            title=t(SECTION_TRANSCRIPT_TITLE),
            font_size=TRANSCRIPT_BODY_FONT[1],
            body_color=COLORS["text_secondary"],
            attr_name="initial_verse_box",
            is_initial=True,
            grid_row=0,
            placeholder_text=t(PLACEHOLDER_TRANSCRIPT),
            placeholder_font=FONTS["placeholder"],
        )
        self.translated_verse_frame = self._create_verse_section(
            master=self.left_column,
            title=t(SECTION_TRANSLATION_TITLE),
            font_size=TRANSLATION_BODY_FONT[1],
            body_color=COLORS["text_primary"],
            attr_name="translated_verse_box",
            is_initial=False,
            grid_row=0,
            placeholder_text=t(PLACEHOLDER_TRANSLATION),
            placeholder_font=FONTS["placeholder_lg"],
        )

        self._create_summary_card()

        # Reference panes are created eagerly and hidden with grid_remove, never
        # deferred (C2): three call sites dereference these widgets with no
        # guard, and a missing attribute raises AttributeError rather than
        # returning None. `_sync_transcript_visibility` below re-grids the
        # transcript if it is meant to start visible.
        self.transcript_column.grid_remove()
        self.right_column.grid_remove()

        # One place decides the button AND the geometry from the flag. Placing
        # the button inside `_create_verse_section` and the geometry somewhere
        # else is what let the two disagree: a live report on a large screen
        # showed both the "Show Transcript" and "Hide" buttons mapped at once,
        # which is only reachable if the button state was set without the
        # layout, or the other way round.
        self._sync_transcript_visibility()

    def _apply_content_layout(self, mode=None, design_width=None):
        """Reflow the reading grid. Item 71.

        Three columns -- translation (primary, always shown), transcript and
        summary (both reference panes, both hidden until their button is
        pressed). Weights are DERIVED from what is currently visible rather
        than hardcoded per branch, so adding or removing a pane cannot leave a
        stale weight behind on a column that is no longer gridded.

        Below `CONTENT_STACK_BREAKPOINT` the design stacks the grid into rows
        (`@media (max-width: 700px)` -> `grid-template-columns: 1fr`).

        `design_width` decides that, NOT the `_get_layout_mode` string this
        used to take. Two reasons, both measured. The mode string switches to
        "medium" below `LAYOUT_WIDE_BREAKPOINT` (1050), so stacking on
        `mode != "wide"` collapsed the reading grid at 1050 where the design
        keeps two columns down to 700. And the mode was computed from
        `winfo_width()`, which is device pixels: a window created at 900
        reports 1350 on this 150% display, so the threshold meant a different
        physical size on every machine. `mode` is still accepted because five
        call sites pass it and it remains the right input for the header's
        hamburger decision, but it no longer decides this.
        """
        if not hasattr(self, "content_wrapper") or self.content_wrapper is None:
            return
        if self.left_column is None:
            return

        if design_width is None:
            design_width = self._design_width()
        stacked = design_width < CONTENT_STACK_BREAKPOINT
        show_transcript = bool(
            getattr(self, "_initial_verse_visible", False)
            and getattr(self, "transcript_column", None) is not None
        )
        show_summary = bool(
            self.summary_panel_visible and self.right_column is not None
        )

        panes = [(self.left_column, 0, CONTENT_PRIMARY_WEIGHT, True)]
        if getattr(self, "transcript_column", None) is not None:
            panes.append(
                (self.transcript_column, 1, CONTENT_REFERENCE_WEIGHT, show_transcript)
            )
        if self.right_column is not None:
            panes.append(
                (self.right_column, 2, CONTENT_REFERENCE_WEIGHT, show_summary)
            )

        if stacked:
            # One column, panes become rows. Every column weight but the first
            # is zeroed so a leftover weight cannot reserve width for a pane
            # that is now a row. `minsize` is cleared with it: a 220px floor on
            # a column that no longer holds anything would reserve width beside
            # a full-width row.
            self.content_wrapper.grid_columnconfigure(0, weight=1, minsize=0)
            for _, index, _, _ in panes[1:]:
                self.content_wrapper.grid_columnconfigure(index, weight=0, minsize=0)
            row = 0
            for pane, _index, _weight, visible in panes:
                if not visible:
                    pane.grid_remove()
                    continue
                pane.grid(row=row, column=0, sticky="nsew", padx=0, pady=(0, 8))
                # The stacked split is NOT the column split. The design gives
                # `grid-template-rows: minmax(0, 1.15fr) minmax(0, .85fr)`, i.e.
                # 57.5/42.5 -- a reference pane needs proportionally more height
                # than it needs width to stay readable. Phase 3b carried the
                # 70/30 column weights over here; that was a deviation.
                self.content_wrapper.grid_rowconfigure(
                    row,
                    weight=(
                        CONTENT_STACKED_PRIMARY_WEIGHT
                        if row == 0
                        else CONTENT_STACKED_REFERENCE_WEIGHT
                    ),
                )
                row += 1
            # Zero any row this pass did not use, or a hidden pane keeps its
            # share of the height.
            for spare in range(row, len(panes)):
                self.content_wrapper.grid_rowconfigure(spare, weight=0)
            return

        self.content_wrapper.grid_rowconfigure(0, weight=1)
        for spare in range(1, len(panes)):
            self.content_wrapper.grid_rowconfigure(spare, weight=0)
        # What one reference pane's weighted share works out to, in design px.
        # Only used to decide whether the design's 220px floor is in play.
        visible_reference_panes = sum(
            1 for _, index, _, visible in panes if index != 0 and visible
        )
        reference_share = design_width * CONTENT_REFERENCE_WEIGHT / (
            CONTENT_PRIMARY_WEIGHT
            + CONTENT_REFERENCE_WEIGHT * max(1, visible_reference_panes)
        )
        for pane, index, weight, visible in panes:
            if not visible:
                self.content_wrapper.grid_columnconfigure(index, weight=0, minsize=0)
                pane.grid_remove()
                continue
            # `minmax(220px, 30fr)` on the reference panes. Tk's `minsize` is
            # NOT `minmax`: it is reserved BEFORE the weights are applied and
            # then the weighted share is added on top, so setting it
            # unconditionally does not clamp the column, it inflates it --
            # measured, a plain `minsize=220` turned a 70/30 split into 57/43
            # at 1200 design px, where the floor should not have been in play
            # at all. So the floor is only installed when it actually binds,
            # and it replaces the weight rather than adding to it.
            floor = 0
            if index != 0 and reference_share < RIGHT_COLUMN_MIN_WIDTH:
                floor = self._design_px(RIGHT_COLUMN_MIN_WIDTH)
                weight = 0
            self.content_wrapper.grid_columnconfigure(
                index, weight=weight, minsize=floor
            )
            pane.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0, 8) if index == 0 else (8, 0),
                pady=0,
            )

    def _apply_status_bar_layout(self, width):
        """Hide non-essential status elements on very narrow windows."""
        if self.waveform_canvas is None:
            return
        compact = width < LAYOUT_STATUS_COMPACT_BREAKPOINT
        if compact:
            self.waveform_canvas.pack_forget()
            if self.status_text_label is not None:
                self._set_dynamic_text(self.status_text_label, "Ready")
        else:
            if not self.waveform_canvas.winfo_ismapped():
                self.waveform_canvas.pack(
                    side="left",
                    padx=(16, 0),
                    before=self._status_right_cluster,
                )
            if self.status_text_label is not None and not self.is_listening:
                self._set_dynamic_text(self.status_text_label, "Ready to listen")

        if self.signal_label is not None:
            if compact:
                self.signal_label.pack_forget()
            elif not self.signal_label.winfo_ismapped():
                self.signal_label.pack(side="right", padx=(0, 10))

    def _footer_button_width(self, labels, pad_x):
        """Width in CustomTkinter units that fits the WIDEST of `labels`.

        The design sizes footer buttons by padding, not by a fixed width, and
        the fixed widths are what broke: on a 150% display the rendered button
        font is 20 px, so "Start Listening" needs 138 px of glyphs plus padding
        while `FOOTER_BTN_WIDTH_COMPACT` (88) buys 132 px -- the label came out
        as "Start Listen". Measuring the text removes that whole class of bug
        and survives a font, label or display-scaling change.

        A list is taken rather than one string so a button whose label changes
        at runtime is sized for its widest state once. Start/Stop must not
        resize the moment the session begins.
        """
        try:
            font = self._ui_font(FONTS["button"][1], "bold")
            # `CTkFont.measure` returns UNSCALED CustomTkinter units, the same
            # space a `width=` is expressed in -- measured: 90 for "Start
            # Listening" both before and after the font is attached to a live
            # button, against 138 device px on the rendered label. An earlier
            # revision of this function divided by the scaling factor on the
            # belief that `measure` reported device pixels; that was wrong and
            # returned 92 units where the label needs 93, which only escaped
            # notice because a CTkButton grows past its configured width.
            widest = max(int(font.measure(str(text))) for text in labels)
            return widest + 2 * pad_x
        except Exception:
            return FOOTER_BTN_WIDTH

    def _sync_hamburger_action_buttons(self, show):
        """Pack/unpack the hamburger menu's Copy/Export/Clear buttons.

        `show=True` (the footer's action group no longer fits its half of the
        row on one line) is the only state where these are the sole place
        those three actions are reachable, so this must run BEFORE
        `_apply_footer_layout` removes them from the footer, not after -- see
        the caller.

        The Start/Stop button is deliberately not included: it stays only in
        the footer at every width. It used to also exist here
        (`listen_button_menu`), visible at the same time as the footer's copy,
        which is the duplicate control the user reported.
        """
        if show == getattr(self, "_hamburger_actions_visible", None):
            return
        self._hamburger_actions_visible = show
        for btn in (
            getattr(self, "copy_translation_btn_menu", None),
            getattr(self, "export_btn_menu", None),
            getattr(self, "clear_btn_menu", None),
        ):
            if btn is None:
                continue
            if show:
                btn.pack(fill="x", padx=15, pady=(4, 8))
            else:
                btn.pack_forget()

    def _apply_footer_layout(self, design_width):
        """Reflow the footer. `design_width` is CSS px, not device px. Item 71 Phase 3b.

        Started from the design document's footer rules, since corrected and
        then partly overridden by explicit user request -- see below.

          >= 700   one row, `justify-content: space-between` -- start/stop
                   hard left, the action group hard right, both at their
                   natural width.
          <  700   The design stretches both groups to 50/50
                   (`.atf-stop-button { flex: 1 1 auto }`,
                   `.atf-listening-group, .atf-action-group { flex: 1 1 100% }`
                   in a `flex-wrap: nowrap` container) and lets the action
                   group wrap onto extra lines below 430
                   (`.atf-action-group button { flex: 1 1 auto }`,
                   `.atf-action-group` is the one element that DOES carry
                   `flex-wrap: wrap`). **This app no longer does either.**
                   Start/Stop stays left-aligned and the action group stays
                   right-aligned at their own natural widths all the way down
                   to the hamburger cutover, and a wrap is never rendered in
                   the footer -- it moves the action group to the hamburger
                   menu instead. Both were explicit user requests: uniform
                   button sizing (the 50/50 stretch made Start/Stop visibly
                   bigger than the others) and no wrapped shape in the footer
                   at any width (a screenshot showed the wrap once, at 400px,
                   and again at ~430px once the first fix only moved the
                   cutover instead of removing the wrap).

        An earlier revision of this method read `flex: 1 1 100%` as "each group
        takes a full row" and stacked them. That is corrected here rather than
        quietly rewritten: the two layouts look similar at a glance and only
        the CSS settles it.

        **The start/stop button is never hidden.** It used to be: the old
        hamburger branch called `left_controls_frame.grid_remove()` and gridded
        `clear_btn` alone, so below 800 px the footer offered Clear and no way
        to start or stop a session except through the hamburger menu. Nothing
        in the design hides it at any width, and it is the one control a
        meeting cannot proceed without.

        Cramming four buttons onto one narrow row is also what produced the
        overlap the user saw. A `CTkButton` does not clip itself -- it requests
        text + padding -- but when the requested widths exceed the row, Tk
        shrinks every one of them below that request and the labels are cut.
        Stacking is the fix, not smaller widths.

        The three action buttons leave the footer entirely -- moving to the
        hamburger menu, already the header's answer to the same width --
        whenever they would need MORE THAN ONE LINE to fit next to Start/Stop
        on the row. That is measured against the real button widths on every
        call rather than pinned to a single width like
        `HAMBURGER_ACTIONS_BREAKPOINT` used to be: measured, the wrap this
        replaces is not confined to a narrow band near that number, it ran
        from 400 design px up to ~550 -- "Copy Translation" alone is wider
        than half the row for most of that band, so a fixed cutoff either
        left the wrap active well past it (a user report, with a screenshot,
        showed the exact 400-430 two-line shape) or moved the cutoff so high
        it swallowed widths where a single line was in fact possible.

        **Start/Stop is always left-aligned at its own natural width, the
        action group always right-aligned at its own natural width, in every
        state up to the hamburger cutover -- neither ever stretches.** An
        earlier revision stretched both to share the row 50/50 below 700px,
        matching the design's `.atf-stop-button { flex: 1 1 auto }`; the user
        reported that as an unwanted height/size mismatch between Start/Stop
        and the action buttons, and asked for uniform natural sizing instead,
        which is what this does -- overriding the design's own stretch rule
        by explicit request. Both buttons also now share ONE height
        (`FOOTER_ACTION_HEIGHT`); the design's 4px difference between them
        was too small to read as intentional and looked like a bug.
        """
        if not self._footer_buttons or self.footer_btn_row is None:
            return

        stacked = design_width < FOOTER_STACK_BREAKPOINT
        gap = self._design_px(FOOTER_GROUP_GAP)
        pad_x_design = FOOTER_PAD_X_STACKED if stacked else FOOTER_PAD_X
        pad_y_design = FOOTER_PAD_Y_STACKED if stacked else FOOTER_PAD_Y
        self.footer_btn_row.grid_configure(
            padx=self._design_px(pad_x_design), pady=self._design_px(pad_y_design)
        )

        listen_btn, copy_btn, export_btn, clear_btn = self._footer_buttons
        actions = (copy_btn, export_btn, clear_btn)

        for btn in self._footer_buttons:
            btn.grid_forget()

        listen_btn.configure(
            height=self._design_px(FOOTER_ACTION_HEIGHT),
            width=self._footer_button_width(translate_all(LISTEN_BUTTON_LABELS), FOOTER_BTN_PAD_X),
        )
        for btn in actions:
            btn.configure(
                height=self._design_px(FOOTER_ACTION_HEIGHT),
                width=self._footer_button_width(
                    [btn.cget("text")], FOOTER_ACTION_PAD_X
                ),
            )

        # `.atf-action-group { flex-wrap: wrap }`. Tk's grid has no auto-wrap,
        # so the lines are computed from the space the group will actually
        # get -- the row's own padding and Start/Stop's natural width taken
        # out, since the two share one row rather than a 50/50 split. Computed
        # BEFORE deciding whether the group stays in the footer at all -- see
        # the docstring.
        available = max(
            1,
            design_width
            - 2 * pad_x_design
            - int(listen_btn.cget("width")),
        )
        line, used, lines = [], 0, []
        for btn in actions:
            needed = int(btn.cget("width"))
            extra = needed if not line else needed + FOOTER_GROUP_GAP
            if line and used + extra > available:
                lines.append(line)
                line, used = [btn], needed
            else:
                line.append(btn)
                used += extra
        if line:
            lines.append(line)

        narrow_hamburger = len(lines) > 1
        self._sync_hamburger_action_buttons(narrow_hamburger)

        left = getattr(self, "left_controls_frame", None)
        right = getattr(self, "right_actions_frame", None)
        for frame in (left, right):
            if frame is not None:
                for child in frame.winfo_children():
                    child.grid_forget()
                for column in range(len(actions)):
                    frame.grid_columnconfigure(column, weight=0)

        if narrow_hamburger:
            # No right-hand group at all: Start/Stop is the footer's only
            # content, so it takes every column instead of sharing with an
            # empty `right`.
            if right is not None:
                right.grid_remove()
            self.footer_btn_row.grid_columnconfigure(0, weight=1)
            self.footer_btn_row.grid_columnconfigure(1, weight=0, minsize=0)
            self.footer_btn_row.grid_columnconfigure(2, weight=0, minsize=0)
            if left is not None:
                left.grid_columnconfigure(0, weight=1)
                listen_btn.grid(row=0, column=0, sticky="ew")
                left.grid(row=0, column=0, columnspan=3, sticky="ew")
            return

        # `narrow_hamburger` is False here, which means `lines` has EXACTLY
        # one line (the definition of `narrow_hamburger` is `len(lines) > 1`)
        # -- the whole action group fits next to Start/Stop, so it is laid
        # out once, at natural width, right-aligned as a group.
        if left is not None:
            left.grid_columnconfigure(0, weight=0)
            listen_btn.grid(row=0, column=0, sticky="w")

        for column, btn in enumerate(actions):
            if right is not None:
                right.grid_columnconfigure(column, weight=0)
            btn.grid(
                row=0,
                column=column,
                padx=(0, gap) if column < len(actions) - 1 else 0,
                sticky="e",
            )

        # Column 1 is the elastic gap that produces `space-between` -- Start
        # left, actions right, at every width up to the hamburger cutover.
        self.footer_btn_row.grid_columnconfigure(0, weight=0)
        self.footer_btn_row.grid_columnconfigure(1, weight=1, minsize=0)
        self.footer_btn_row.grid_columnconfigure(2, weight=0)
        if left is not None:
            left.grid(row=0, column=0, columnspan=1, sticky="w", pady=0)
        if right is not None:
            right.grid(row=0, column=2, columnspan=1, sticky="e", pady=0)

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
            text=t("Start Listening"),
            command=self.toggle_listening,
            **self._primary_button_config(width=FOOTER_BTN_WIDTH),
        )
        # Recorded at construction as well as on every state change: without it
        # a language switch before the first Start would leave this button in
        # the old language, because nothing had written its text yet.
        self.listen_button._alpha_text_source = "Start Listening"

        self.footer_stop_button = None

        self.copy_translation_btn = ctk.CTkButton(
            master=self.right_actions_frame,
            text=t("Copy Translation"),
            command=self.copy_translation_to_clipboard,
            **self._secondary_button_config(width=FOOTER_BTN_WIDTH),
        )

        self.export_btn = ctk.CTkButton(
            master=self.right_actions_frame,
            text=t("Export"),
            command=self.export_transcript_placeholder,
            **self._secondary_button_config(width=FOOTER_BTN_WIDTH_SECONDARY),
        )

        self.clear_btn = ctk.CTkButton(
            master=self.right_actions_frame,
            text=t("Clear"),
            command=self.clear_text,
            **self._secondary_button_config(width=FOOTER_BTN_WIDTH_SECONDARY),
        )

        self._footer_buttons = [
            self.listen_button,
            self.copy_translation_btn,
            self.export_btn,
            self.clear_btn,
        ]
        self._apply_footer_layout(self._design_width())

    def _create_toggle_button(self, master, text, width):
        """Create a compact hide/show toggle button for the Initial verse panel."""
        return ctk.CTkButton(
            master=master,
            text=t(text),
            command=self.toggle_initial_verse,
            **self._secondary_button_config(width=width, height=SMALL_BUTTON_HEIGHT),
        )

    def _place_toggle_button(self, parent_row, text, width):
        """Show exactly one of the two toggle buttons.

        `parent_row` is accepted and ignored, and deliberately kept: the two
        buttons are built in two different title rows and never move between
        them, so the row is already implied by which button is being shown.
        Removing the parameter would touch every call site for no behavioural
        gain; documenting it stops the next reader assuming it re-parents
        anything.
        """
        if text == "Hide":
            self.show_initial_button.grid_remove()
            self.hide_initial_button.configure(text=t(text), width=width)
            self.hide_initial_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        else:
            self.hide_initial_button.grid_remove()
            self.show_initial_button.configure(text=t(text), width=width)
            self.show_initial_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def check_scrollbar_visibility(self, text_widget, scrollbar):
        """Show the right scrollbar only when text content overflows the visible area."""
        try:
            yview = text_widget.yview()
            if yview[0] == 0.0 and yview[1] == 1.0:
                scrollbar.pack_forget()
            else:
                # `before=text_widget` is what makes the scrollbar visible at
                # all. The text widget is packed first with `expand=True`
                # (`_create_styled_text`), so it claims the entire cavity;
                # anything packed AFTER it is allocated from what is left,
                # which is nothing. Measured on a realised window: packed after,
                # the scrollbar gets width 1 and `winfo_ismapped()` False;
                # packed before, width 16 and mapped, with the text widget
                # giving up exactly those pixels. That is why the transcript and
                # translation panes had no scrollbar while the summary panel --
                # which packs its scrollbar FIRST -- always had one.
                #
                # This has to be on the show path rather than only at creation,
                # because the auto-hide calls `pack_forget()` and re-packing
                # sends the widget back to the end of the pack order.
                scrollbar.pack(side="right", fill="y", before=text_widget)
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
        pane_role=None,
    ):
        """Create a read-only tk.Text widget with a right-side auto-hiding CTkScrollbar.

        `pane_role` opts the widget into the design's reading type scale
        (item 71 Phase 2). Without it the widget keeps the pre-redesign
        styling, so any future caller is unaffected.
        """
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
        text_widget._pane_frame = text_frame

        if pane_role:
            stacked = self._design_width() < CONTENT_STACK_BREAKPOINT
            self._reading_typography_stacked = stacked
            self._apply_reading_typography(text_widget, pane_role, stacked)

        if placeholder_text:
            pfont = placeholder_font or FONTS["placeholder"]
            if pane_role:
                pfont = self._scaled_design_font(pfont)
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
            self.initial_title_label = title_label
            self.hide_initial_button = self._create_toggle_button(title_row, "Hide", 64)
            # Both buttons are created eagerly and left UNGRIDDED (C2: created
            # eagerly, never deferred -- `_place_toggle_button` dereferences
            # both with no guard, so a missing attribute raises AttributeError
            # rather than returning None).
            #
            # Item 81: neither is gridded here any more. This function used to
            # grid "Show Transcript" while the visibility flag was decided
            # elsewhere, so the two could disagree -- a live report on a large
            # screen had BOTH buttons mapped at once. `_sync_transcript_visibility`
            # is now the only writer of button state, and it derives it from the
            # same flag the grid does.
            self.hide_initial_button.grid_remove()
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
            pane_role="transcript" if is_initial else "translation",
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

    def _sync_transcript_visibility(self):
        """Make the button and the reading grid agree with the flag. Item 81.

        Both halves used to be written at the toggle's call site, and the
        initial button was placed inside `_create_verse_section` instead. A
        live report on a large screen showed the failure that split allows: the
        button changed and the pane did not, and in another shot BOTH the
        "Show Transcript" and "Hide" buttons were mapped at once. Neither state
        is reachable if one function always writes both from one flag.

        The geometry itself is still owned by `_apply_content_layout`; this only
        guarantees it is asked, and that the button matches what it did.
        """
        visible = bool(getattr(self, "_initial_verse_visible", False))
        # The translation title is NOT hidden in either state. That belonged to
        # the old "full-screen translated" mode where translation was the only
        # pane; it is now always the primary pane and always labelled.
        if visible:
            self._place_toggle_button(self.initial_title_row, "Hide", 64)
        else:
            self._place_toggle_button(
                self.translated_title_row, "Show Transcript", 128
            )
        self._apply_content_layout(design_width=self._design_width())

    def toggle_initial_verse(self):
        """Show or hide the original-transcript reference pane. Item 71.

        The transcript is a COLUMN beside the translation, not a row above it,
        so this does not touch `left_column`'s row weights -- doing so would be
        a silent no-op, since `grid_rowconfigure` on a container whose children
        are in columns raises nothing and does nothing.
        """
        previous = bool(getattr(self, "_initial_verse_visible", False))
        try:
            self._initial_verse_visible = not previous
            self._sync_transcript_visibility()
        except Exception as exc:
            # A swallowed failure here is exactly the reported bug: the flag
            # flips, the button swaps, and the grid is never told -- so the
            # pane does not appear, or its space is never released. Put the
            # flag back so the next press is not a no-op, and make the failure
            # findable instead of leaving a lone print in a console nobody
            # reads during a meeting.
            self._initial_verse_visible = previous
            print(f"Error toggling initial verse: {exc}")
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "TRANSCRIPT_PANE_TOGGLE_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    requested_visible=not previous,
                    restored_visible=previous,
                )
            except Exception:
                pass
            try:
                self._sync_transcript_visibility()
            except Exception:
                pass

    def _apply_left_column_panel_weights(
        self,
        transcript_weight=TRANSCRIPT_PANEL_WEIGHT,
        translation_weight=TRANSLATION_PANEL_WEIGHT,
    ):
        """No longer applicable. Item 71.

        `left_column` used to stack transcript (row 0) over translation
        (row 1); it now holds the translation alone and the transcript is its
        own column. Writing row weights here would be a **silent** no-op --
        `grid_rowconfigure` on a container whose children are in columns
        neither raises nor does anything -- so the body is removed rather than
        left to look effective.

        Kept as a function because it has callers (`_set_initial_pane_ratio`)
        and because deleting it would be a wider change than this phase needs.
        The stacked-window split is derived in `_apply_content_layout`, which
        is now the single owner of the reading grid's geometry.
        """
        return

    def _set_initial_pane_ratio(self):
        """Apply the reading grid's first layout. Item 71.

        Was row weights on `left_column`; the panes are columns now, so it
        defers to `_apply_content_layout`, the one owner of that geometry.
        The `_initial_verse_visible` early return is gone deliberately: the
        transcript now starts hidden, so returning on that flag would skip the
        first layout entirely and leave the grid at its constructor defaults
        until the first resize event happened to arrive.
        """
        if self.left_column is None:
            return
        try:
            self._apply_content_layout(design_width=self._design_width())
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
        self._latest_interim_utterance_id = ""
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
        # Prefer the run's finalized listen language; never force-lock to ja.
        return str(getattr(self, "_listen_language", None) or FORCE_DEEPGRAM_LANGUAGE or "en")

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
        # fixes TASK_2E_FINDINGS.md item 3 (positional last-line write): only
        # this store's true last row, confirmed same speaker, may be
        # overwritten -- fail-closed instead of scanning backward for a
        # stale match from before an intervening speaker turn.
        updated = store.update_last_segment_if_active(speaker, merged_text)
        if not updated:
            return False
        # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 3: canonical_utterance_id/
        # source_version are now assigned by the caller
        # (_commit_transcript_item_to_store) before this function runs --
        # thread them through instead of the fail-closed skip
        # TASK_3B_CHANGES.md documented as the best available fix at the time
        # ("no canonical_utterance_id available at all in scope").
        self._on_store_segment_updated(
            speaker,
            merged_text,
            canonical_utterance_id=str(item.get("canonical_utterance_id") or ""),
            source_version=int(item.get("source_version") or 1),
            source_record_id=str(item.get("canonical_record_id") or ""),
        )
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
        self._apply_final_interim_comparison(
            merged_text,
            utterance_id=str((item or {}).get("canonical_utterance_id") or ""),
        )
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
        # deepgram_client.py delivers every interim tick here TWICE: once
        # via utterance_lifecycle._dispatch_interim (metadata carries
        # canonical_utterance_id) and once raw from
        # _handle_interim_deepgram_result (no identity at all). The raw
        # call lands last, so a plain overwrite here silently discards the
        # only identity the interim ever has -- which is what forced the
        # final/interim comparison to guess from text alone. Carry the
        # identity forward across the pair instead of dropping it.
        metadata = dict(metadata or {})
        if not str(metadata.get("canonical_utterance_id") or "").strip():
            pending = getattr(self, "_pending_interim", None)
            prev_meta = pending[2] if pending and len(pending) > 2 else None
            prev_id = str((prev_meta or {}).get("canonical_utterance_id") or "").strip()
            prev_text = (pending[1] if pending else "") or ""
            if prev_id and (prev_text or "").strip() == (text or "").strip():
                metadata["canonical_utterance_id"] = prev_id
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
        # Identity of the utterance this preview belongs to, when the
        # producer supplied one. Used by _apply_final_interim_comparison to
        # tell "this final IS my utterance" from "this final belongs to an
        # older utterance and mine is still live" without guessing from text.
        incoming_id = str((metadata or {}).get("canonical_utterance_id") or "").strip()
        if incoming_id:
            self._latest_interim_utterance_id = incoming_id
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
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("interim_ui_scheduled_at", text_len=len(interim_text))
        except Exception:
            pass
        self._update_interim_line_only()

    def _clear_interim_tail(self):
        self._latest_interim_text = ""
        self._latest_interim_speaker = 1
        self._latest_interim_utterance_id = ""
        self._remove_interim_line_from_display()

    def _discard_watchdog_orphaned_interim(self):
        """Drop the watchdog's preserved orphan (item 11b).

        Called once the orphan can no longer represent uncommitted speech:
        after the Stop-time recovery path has consumed or rejected it, and
        at session reset. Kept separate from _clear_interim_tail because
        that runs on the normal commit path too, where the orphan must
        survive -- the whole point of item 11b is that a display-layer
        clear must not destroy the content-recovery source.
        """
        self._watchdog_orphaned_interim_text = ""
        self._watchdog_orphaned_interim_speaker = 1
        self._watchdog_orphaned_interim_utterance_id = ""
        self._watchdog_orphaned_interim_at = 0.0

    def _append_pending_interim_to_display(self):
        """Re-attach the single mutable interim line after a store re-render.

        Must set interim_anchor (via _update_interim_line_only) so later
        revisions replace in place instead of appending permanent ⏳ lines.
        """
        if not (getattr(self, "_latest_interim_text", "") or "").strip():
            return
        self._update_interim_line_only()

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
        """Re-render a bounded window of store-backed transcript (no full-history scrape).

        Full-widget rewrite of unbounded history is blocked for long sessions.
        Canonical TranscriptStore remains the authoritative complete transcript.
        """
        self._transcript_render_job = None
        box = getattr(self, "initial_verse_box", None)
        if box is None or not hasattr(self, "transcript_store") or self.transcript_store is None:
            return
        segments = list(self.transcript_store.get_all() or [])
        limit = int(MAX_RENDERED_UI_SEGMENTS)
        window = segments[-limit:] if len(segments) > limit else segments
        if not window:
            DuplicateProtectionMixin._render_transcript_from_store(self)
            return
        # Incremental-style rebuild of the bounded window only (never entire unbounded history).
        lines: list[str] = []
        for seg in window:
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            # Item 65 reached copy/export and `Alpha output.txt`, but NOT this
            # pane -- the one actually watched during a meeting, which kept
            # rendering each committed segment as one raw paragraph. Item 69
            # grouped the live *interim* preview; this is its committed
            # counterpart, so a settled line and the preview above it now read
            # the same way instead of the text reflowing the moment it commits.
            #
            # Reuses the store's `_readable_parts`, which is memoised per
            # segment, so a bounded 500-segment window costs no extra
            # derivation on the UI thread. It falls back to the raw text on any
            # failure, and returns [text] unchanged for non-English.
            try:
                parts = self.transcript_store._readable_parts(seg)
            except Exception:
                parts = [text]
            for part in parts or [text]:
                part = (part or "").strip()
                if part:
                    lines.append(f"{self._ui_speaker_label_text()}{part}")
        content = "\n".join(lines)
        if content.strip():
            # Guard: refuse accidental full-history unbounded rewrite path.
            if len(segments) > limit and len(window) > limit:
                print("UI_FULL_REWRITE_BLOCKED reason=unbounded_window")
                return
            self._insert_formatted_text(box, content)
            box.configure(state="normal")
            if not content.endswith("\n"):
                box.insert("end", "\n")
            box.configure(state="disabled")
            box.see(tk.END)
            self._displayed_segment_count = len(window)
            scrollbar = getattr(box, "_scrollbar", None)
            if scrollbar is not None and hasattr(self, "check_scrollbar_visibility"):
                self.check_scrollbar_visibility(box, scrollbar)
        if (getattr(self, "_latest_interim_text", "") or "").strip():
            self._append_pending_interim_to_display()

    def _apply_final_interim_comparison(self, final_text: str, utterance_id: str = ""):
        final_text = (final_text or "").strip()
        if not final_text:
            return
        interim_text = (getattr(self, "_latest_interim_text", "") or "").strip()
        norm_final = self._normalize_compare(final_text)
        norm_interim = self._normalize_compare(interim_text)
        final_id = str(utterance_id or "").strip()
        interim_id = str(getattr(self, "_latest_interim_utterance_id", "") or "").strip()
        action = "keep_interim"
        # Order matters: check "interim is fully covered by final" FIRST.
        # This also correctly covers the equal-strings case (the most common
        # case — final == interim after a clean commit), which Python's
        # `in` operator matches on both sides. Checking the other direction
        # first previously caused equal/caught-up text to be wrongly kept.
        if norm_interim and norm_final and norm_interim in norm_final:
            action = "clear_interim"
            self._clear_interim_tail()
        elif norm_interim and norm_final and norm_final in norm_interim:
            action = "keep_interim"
        elif not norm_interim:
            action = "no_interim"
        elif norm_interim and norm_final:
            # Genuinely unrelated: neither text contains the other. Text
            # alone cannot distinguish "stale ghost left over from an
            # earlier utterance" (must clear) from "my utterance is still
            # live and this final belongs to an older one" (must keep) --
            # so decide on identity, which _handle_interim_transcript_ui
            # now preserves, and only fall back to clearing when identity
            # is genuinely unavailable (the confirmed real-world ghost
            # pattern). A wrongly-cleared live interim self-heals on the
            # next interim tick (~INTERIM_UI_THROTTLE_MS); a wrongly-kept
            # ghost used to persist for the rest of the session.
            if final_id and interim_id and final_id == interim_id:
                action = "clear_interim_same_utterance"
                self._clear_interim_tail()
            elif final_id and interim_id:
                # Different utterances: this interim is a live preview of a
                # newer one. Keep it -- the ghost watchdog is the backstop
                # if it turns out to be an orphan after all.
                action = "keep_interim_other_utterance"
            else:
                action = "clear_interim_unrelated"
                self._clear_interim_tail()
        self._last_final_text = final_text
        self._interim_log(
            "[INTERIM] final comparison",
            {
                "action": action,
                "final_len": len(final_text),
                "interim_len": len(interim_text),
                "final_preview": final_text[:120],
                "interim_preview": interim_text[:120],
                "final_utterance_id": final_id,
                "interim_utterance_id": interim_id,
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
        # fixes BUG_FIX_ROADMAP.md item 11c: this was a single inline `< 20`
        # applied to both scripts. Because the length is measured after
        # _normalize_compare -- which for CJK compacts away all spacing and
        # punctuation -- 20 sat below the English median interim (30) but at
        # more than double the Japanese median (9), rejecting 91% of every
        # Japanese interim ever recorded. All three interims ever genuinely
        # pending at Stop were Japanese and normalized to 7, 16 and 7, so each
        # one died here, on the last-chance path, after items 10/11/11b had
        # already been fixed to let them through. The floor is now per-script
        # and named, so the value is reviewable rather than folded into an
        # expression.
        #
        # Kept inline rather than extracted into a helper method on purpose:
        # several test hosts bind this function onto a stub without inheriting
        # AlphaApp, so a new method dependency raises AttributeError there.
        # `_is_japanese_manual_mode` is already required by _normalize_compare
        # two lines above, so it adds nothing new.
        min_chars = (
            STOP_TAIL_MIN_CHARS_CJK
            if (self._is_japanese_manual_mode() and JAPANESE_CHAR_DEDUP_ENABLED)
            else STOP_TAIL_MIN_CHARS_LATIN
        )
        if len(norm_interim) < min_chars:
            return False, "too_short"
        if norm_final and norm_final in norm_interim:
            if len(norm_interim) - len(norm_final) < 12:
                return False, "not_meaningfully_longer"
            return True, "interim_extends_final"
        # fixes BUG_FIX_ROADMAP.md Batch 3 item 11 (audit §3.4 row 3): this
        # used to drop the leftover interim whenever it appeared ANYWHERE
        # inside the last committed line (`norm_interim in norm_final`).
        # Same defect and same reasoning as item 10 in
        # _check_stop_tail_duplicate, one filter further down the same
        # Stop-time last-chance path -- both filters must pass for
        # uncommitted speech to survive Stop, so a false match here is
        # permanent loss just the same.
        #
        # An interim is the in-progress hypothesis building toward a final,
        # so the evidence that it is "already covered" by that final is
        # that the final *equals* it or *starts with* it. An interior or
        # suffix-only match is coincidence, not evidence: a speaker who
        # repeats an earlier phrase as a fresh closing remark had that
        # remark silently discarded.
        #
        # Note the asymmetry this removes -- the mirror-image case just
        # above (final contained in interim) already refuses to drop
        # anything without a 12-char meaningfulness margin, while this
        # branch had no such guard at all. Interior-only matches now fall
        # through to new_missing_tail, which commits and preserves the text.
        if norm_final and (
            norm_interim == norm_final or norm_final.startswith(norm_interim)
        ):
            return False, "interim_in_final"
        if norm_final and norm_interim and norm_interim != norm_final:
            return True, "new_missing_tail"
        # The old trailing `return False, "no_match"` was unreachable and is
        # removed rather than left as a dead drop-path in a function whose
        # every other drop-path loses speech permanently. Reaching this line
        # requires norm_final to be empty: if it were not, either it is
        # contained in norm_interim (returned above), or norm_interim equals
        # it or is its prefix (returned above), or the two differ and
        # new_missing_tail returns -- and they cannot be equal here, because
        # equality implies containment, which the first branch already
        # returns on. norm_interim is always non-empty by the length guard.
        return True, "no_prior_final"

    def _recover_interim_tail_on_stop(self):
        if getattr(self, "_latest_interim_committed", False):
            self._interim_log(
                "[INTERIM] stop tail skipped",
                {"reason": "already_committed"},
            )
            return
        interim_text = (getattr(self, "_latest_interim_text", "") or "").strip()
        recovered_speaker_override = None
        # fixes BUG_FIX_ROADMAP.md Batch 3 item 11b: if the ghost watchdog
        # cleared a still-uncommitted interim before Stop, _latest_interim_text
        # is empty here even though real speech was pending. Fall back to the
        # orphan the watchdog preserved, so items 10/11's filters get to judge
        # it instead of it being gone before they run.
        if not interim_text:
            orphan = (
                getattr(self, "_watchdog_orphaned_interim_text", "") or ""
            ).strip()
            # Supersession guard: only resurrect the orphan if no newer interim
            # arrived after it was stashed. Otherwise the speaker carried on and
            # the orphan is stale -- committing it would append old text at the
            # END of the transcript, out of order. This is a supersession rule,
            # not a time bound: the case the orphan exists for (watchdog clear
            # immediately followed by Stop, no further speech) is untouched.
            orphan_at = float(getattr(self, "_watchdog_orphaned_interim_at", 0.0) or 0.0)
            newest_interim_at = float(getattr(self, "_last_interim_ui_at", 0.0) or 0.0)
            if orphan and newest_interim_at > orphan_at:
                self._interim_log(
                    "[INTERIM] stop tail orphan superseded",
                    {"text_len": len(orphan), "text_preview": orphan[:120]},
                )
                orphan = ""
            if orphan:
                interim_text = orphan
                recovered_speaker_override = getattr(
                    self, "_watchdog_orphaned_interim_speaker", 1
                ) or 1
                self._interim_log(
                    "[INTERIM] stop tail using watchdog orphan",
                    {
                        "text_len": len(orphan),
                        "text_preview": orphan[:120],
                        "interim_utterance_id": str(
                            getattr(
                                self, "_watchdog_orphaned_interim_utterance_id", ""
                            )
                            or ""
                        ),
                    },
                )
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
            self._discard_watchdog_orphaned_interim()
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
            self._discard_watchdog_orphaned_interim()
            return
        if decision == "append_missing_suffix":
            merged_text = tail_check.get("commit_text")
            speaker = (
                tail_check.get("update_speaker")
                or recovered_speaker_override
                or getattr(self, "_latest_interim_speaker", 1)
                or 1
            )
            if store is not None and merged_text:
                # fixes BUG_FIX_ROADMAP.md Batch 3 item 17: `update_speaker`
                # in tail_check came from the store's true last row
                # (_check_stop_tail_duplicate reads last_segments[-1]), but
                # the write used to go through `update_last_segment`'s
                # reverse scan under a *separate* lock acquisition -- so an
                # append in between made this land on an older row. Unlike
                # the other two call sites this one **ignored the return
                # value**, so simply swapping in the strict variant would
                # silently drop the merged tail whenever it refused -- on
                # the Stop last-chance path, where a drop is permanent (the
                # exact loss class items 10/11/11b exist to prevent).
                # Append instead: a visible extra line is recoverable, a
                # lost one is not.
                if not store.update_last_segment_if_active(speaker, merged_text):
                    store.add_segment(speaker=speaker, text=merged_text)
                    jp_accuracy_log(
                        "STOP_TAIL_MERGE_APPENDED_NOT_UPDATED",
                        speaker=speaker,
                        merged_preview=_diag_text_preview(merged_text, 160),
                    )
                # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 3 (the second of
                # TASK_3B_CHANGES.md's two flagged call sites): this appends
                # a missing suffix onto the already-committed line, i.e. a
                # revision of that same utterance -- reuse its tracked
                # canonical_utterance_id when this is the Japanese
                # manual-mode path (English's interim recovery is untouched,
                # matching Task 2F's cross-language scope decision).
                stop_tail_utterance_id = ""
                stop_tail_source_version = 1
                if self._is_japanese_manual_mode():
                    stop_tail_utterance_id = str(
                        getattr(self, "_jp_manual_mode_current_utterance_id", "") or ""
                    )
                    if not stop_tail_utterance_id:
                        stop_tail_utterance_id = f"jpm-utt-{uuid.uuid4().hex[:12]}"
                        self._jp_manual_mode_current_utterance_id = stop_tail_utterance_id
                        self._jp_manual_mode_current_source_version = 0
                    stop_tail_source_version = int(
                        getattr(self, "_jp_manual_mode_current_source_version", 0) or 0
                    ) + 1
                    self._jp_manual_mode_current_source_version = stop_tail_source_version
                self._on_store_segment_updated(
                    speaker,
                    merged_text,
                    canonical_utterance_id=stop_tail_utterance_id,
                    source_version=stop_tail_source_version,
                )
                self._track_committed_segment_meta(
                    {"speaker": speaker, "text": merged_text}, merged_text
                )
                self._latest_interim_committed = True
                self._last_final_text = merged_text
                self._clear_interim_tail()
                self._discard_watchdog_orphaned_interim()
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
            self._discard_watchdog_orphaned_interim()
            return
        speaker = (
            recovered_speaker_override
            or getattr(self, "_latest_interim_speaker", 1)
            or 1
        )
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
        self._discard_watchdog_orphaned_interim()
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
            # UI metadata prefix only — segment.text remains the lexical content.
            lines.append(f"{self._ui_speaker_label_text()}{text}")
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
        from alpha.utils.language_routing import build_language_profile

        return build_language_profile(ui_label)

    def _resolve_deepgram_language(self, ui_label: str) -> str:
        """Map UI source language label to Deepgram language code.

        Manual dropdown is authoritative. FORCE_DEEPGRAM_LANGUAGE must not
        override a known selection (that caused English → ja).
        """
        from alpha.utils.language_routing import (
            UnknownLanguageSelectionError,
            resolve_ui_language_to_deepgram_code,
        )

        selected = self._strip_language_flag(ui_label or "")
        profile = self._build_language_profile(ui_label)
        self._language_profile_id = profile["profile_id"]
        self._allowed_languages = profile["allowed_languages"]
        self._profile_is_auto = bool(profile["is_auto"])
        self._selected_source_language_ui_label = selected
        if not profile.get("selection_supported", True):
            reason = profile.get("unsupported_reason") or "language_not_supported"
            raise UnknownLanguageSelectionError(
                f"Unsupported language selection: {selected!r} ({reason})"
            )
        # Never apply FORCE over a known manual selection.
        return resolve_ui_language_to_deepgram_code(
            selected,
            force_deepgram_language=FORCE_DEEPGRAM_LANGUAGE,
            allow_force_override=False,
        )

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
        # fixes BUG_FIX_ROADMAP.md Batch 3 item 12: this used to be
        # `if norm_curr in norm_prev` -- current counted as "nothing new,
        # skip the repair" whenever it was a literal substring ANYWHERE
        # inside previous, including the middle. A rewording of previous
        # that happens to share a verbatim chunk somewhere in the old text
        # was misclassified as non-continuation, so the correction never
        # got merged in -- previous stayed uncorrected and current still
        # committed separately downstream (not a content-loss bug like
        # items 10/11/19: current is never dropped here, only the merge
        # opportunity is missed). Narrowed to prefix-or-suffix of previous,
        # the only shapes that actually evidence current is a truncated
        # partial repeat rather than a coincidental substring match.
        if norm_prev.startswith(norm_curr) or norm_prev.endswith(norm_curr):
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
        # fixes BUG_FIX_ROADMAP.md Batch 3 item 17: `last_segment` above came
        # from the true-last `get_last_segment()`, but the write used to go
        # through `update_last_segment`'s reverse scan under a *separate*
        # lock acquisition -- so a row appended in between made this merge
        # overwrite an older row than the one it was computed from. The safe
        # variant refuses anything but the true last row; returning False
        # here is already the existing fail-safe (the caller then commits
        # `text` normally through the buffer/commit path, so nothing is
        # dropped).
        updated = store.update_last_segment_if_active(
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
            # fixes BUG_FIX_ROADMAP.md Batch 3 item 10 (audit §3.4 row 2):
            # this used to drop the leftover interim whenever it was ANY
            # substring of ANY of the last 5 committed segments
            # (`norm_interim in norm_seg`). This runs at Stop, on the
            # last-chance commit path, and returns commit_text=None -- so a
            # false match here loses the text permanently, with no second
            # chance.
            #
            # An interim is the in-progress hypothesis that was building
            # toward a final, so the evidence that it is "already
            # committed" is that the committed segment *equals* it or
            # *starts with* it. An arbitrary interior substring match is
            # coincidence, not evidence: a short but genuinely new closing
            # utterance ("Thank you.", "Okay.", "はい。") that happens to
            # appear somewhere inside an earlier committed line was being
            # silently discarded. Interior-only matches now fall through to
            # the suffix/merge logic below and ultimately to
            # commit_new_tail, which preserves the text.
            if norm_interim == norm_seg or norm_seg.startswith(norm_interim):
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
            # fixes BUG_FIX_ROADMAP.md Batch 3 item 17: this used
            # get_last_segment(speaker), whose reverse scan reaches back
            # PAST an intervening different-speaker turn and returns a row
            # this new final does not continue -- the positional "last line"
            # bug of TASK_2E_FINDINGS.md item 3. An earlier fix deliberately
            # left it alone (see the comment below about previous_text being
            # "untouched") because removing the unsafe method was out of its
            # scope; item 17 is that scope.
            #
            # previous_text now goes None when the store's last row belongs
            # to someone else. Every consumer of it fails safe in that
            # direction: decide_transcript_action(None, text) -> "add" (and
            # its result here is diagnostic only -- the real commit runs
            # inside DuplicateProtectionMixin._display_transcript_item),
            # and _evaluate_japanese_commit_dedup simply does not suppress.
            # Worst case is a visible duplicate line, never a wrong merge
            # into another speaker's row.
            segment = self.transcript_store.get_last_segment_if_active(speaker)
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
        # fixes TASK_2E_FINDINGS.md item 3 (speaker-blind merge): only attempt
        # a cross-segment merge when the store's true last row is confirmed to
        # belong to this same speaker -- refuses to reach back across an
        # intervening different-speaker turn. previous_text itself (used below
        # by decide_transcript_action for both languages) is left untouched.
        speaker_confirmed_active = bool(
            self._is_japanese_manual_mode()
            and previous_text
            and text
            and self.transcript_store is not None
            and self.transcript_store.get_last_segment_if_active(speaker) is not None
        )
        if speaker_confirmed_active:
            cross_segment = self._evaluate_japanese_cross_segment_merge(
                previous_text, text
            )
            if cross_segment:
                # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 3: this merge
                # continues the SAME utterance -- reuse its tracked
                # canonical_utterance_id (minted below the first time this
                # speaker's chain started) and bump source_version, instead
                # of the fail-closed skip Task 3B left in place because no
                # id was ever available here before this fix.
                continuation_id = str(
                    getattr(self, "_jp_manual_mode_current_utterance_id", "") or ""
                )
                if not continuation_id:
                    continuation_id = f"jpm-utt-{uuid.uuid4().hex[:12]}"
                    self._jp_manual_mode_current_utterance_id = continuation_id
                    self._jp_manual_mode_current_source_version = 0
                next_version = int(
                    getattr(self, "_jp_manual_mode_current_source_version", 0) or 0
                ) + 1
                self._jp_manual_mode_current_source_version = next_version
                item["canonical_utterance_id"] = continuation_id
                item["source_version"] = next_version
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
        if self._is_japanese_manual_mode() and not item.get("canonical_utterance_id"):
            # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 3: no merge applied --
            # this is a genuinely new committed segment (new speaker turn,
            # or text didn't match a continuation heuristic). Start a fresh
            # utterance identity rather than leaving canonical_utterance_id
            # unset, per TASK_2F_CHANGES.md's original recommendation
            # ("route through the canonical controller") -- item now carries
            # real identity for _display_transcript_item's already-fixed
            # (Fix 2) already_committed verification and for
            # _on_store_segment_added's translation-display keying.
            new_id = f"jpm-utt-{uuid.uuid4().hex[:12]}"
            self._jp_manual_mode_current_utterance_id = new_id
            self._jp_manual_mode_current_source_version = 1
            item["canonical_utterance_id"] = new_id
            item["source_version"] = 1
        dup_action, _result_text = decide_transcript_action(previous_text, text)
        predicted_decision, predicted_reason = teams_commit_decision_from_dup_action_diagnostic_only(
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
        # fixes item 62. This call discarded its return value, so a
        # "retry_pending" verdict never reached the handler in
        # _flush_transcript_ui_batch -- the item was DROPPED instead of
        # retried, silently, on the Japanese path only. The English path
        # has always honoured it.
        _dp_result = DuplicateProtectionMixin._display_transcript_item(self, item)
        if _dp_result == "retry_pending":
            return _dp_result
        store_count_after = self._diag_store_segment_count()
        if store_count_after == store_count_before:
            if dup_action == "skip":
                skip_decision, skip_reason = teams_commit_decision_from_dup_action_diagnostic_only(
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
        self._apply_final_interim_comparison(
            text,
            utterance_id=str((item or {}).get("canonical_utterance_id") or ""),
        )

    def _display_transcript_item(self, item):
        """Route finals through segment repair or legacy buffer before store commit."""
        speaker, text, preview = self._diag_transcript_item_fields(item)
        is_finalizing = bool(getattr(self, "_is_finalizing", False))
        is_listening = bool(self.is_listening)
        store_count_before = self._diag_store_segment_count()
        speech_final = item.get("speech_final")

        if item.get("is_final") is False:
            # Item 70 prerequisite. `duplicate_protection._display_transcript_item`
            # already logs COMMITTED_SEGMENT_DROPPED_AS_INTERIM for exactly this
            # case (item 65's 8-of-9 loss), but that copy is UNREACHABLE for it:
            # this method is the entry point every UI batch goes through
            # (`_flush_transcript_ui_batch` -> here), and this bare return fires
            # first -- the mixin's logged copy is only reached further down, at
            # the `DuplicateProtectionMixin._display_transcript_item(self, item)`
            # call. `tests/test_committed_segment_is_final.py` could not see the
            # gap because it binds the mixin method onto a bare host, so it
            # never executes this line.
            #
            # The drop itself stays correct -- interims must not reach the store
            # -- but an item carrying a commit reason is not an interim, it is a
            # commit whose is_final was clobbered upstream. Item 70 raises the
            # flush rate, so this path must stop being silent BEFORE it carries
            # more traffic.
            if item.get("lifecycle_commit_reason") or item.get("stabilizer_reason"):
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "COMMITTED_SEGMENT_DROPPED_AS_INTERIM",
                        reason="is_final_false_on_a_committed_segment",
                        gate="main_window._display_transcript_item",
                        commit_reason=str(
                            item.get("lifecycle_commit_reason")
                            or item.get("stabilizer_reason")
                            or ""
                        ),
                        canonical_utterance_id=str(
                            item.get("canonical_utterance_id") or ""
                        ),
                        text_preview=str(item.get("text") or "")[:120],
                    )
                except Exception:
                    pass
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

        from alpha.audio.timeline_mixer import DeepgramTimelineMixer

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
                            # V26.5.3: retain exact mixed Deepgram-delivery PCM.
                            # System/mic components are retained observably inside
                            # DeepgramTimelineMixer._build_frame (pre-mix).
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
            label = "Stop Listening"
            cfg = {
                "fg_color": COLORS["accent_red"],
                "hover_color": COLORS["accent_red_hover"],
            }
        else:
            label = "Start Listening"
            cfg = {
                "fg_color": COLORS["accent_blue"],
                "hover_color": COLORS["accent_blue_hover"],
            }
        for btn in (self.listen_button, self.listen_button_menu):
            if btn is not None:
                self._set_dynamic_text(btn, label, state="normal", **cfg)
        # The mic choice is read at Start, so it must not look changeable while
        # a session is running.
        self._set_mic_switch_enabled(not listening)
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
            self._set_dynamic_text(
                self.live_indicator, "○ IDLE", text_color=COLORS["live_idle"]
            )
        if self.live_pill is not None:
            self.live_pill.configure(
                fg_color=COLORS["status_active_bg"],
                border_color=COLORS["border_soft"],
            )
        idle_btn = {
            "text": "Finalising…",
            "state": "disabled",
            "fg_color": COLORS["accent_blue"],
            "hover_color": COLORS["accent_blue_hover"],
        }
        for btn in (self.listen_button, self.listen_button_menu):
            if btn is not None:
                btn.configure(**idle_btn)
        if self.status_text_label is not None:
            self._set_dynamic_text(
                self.status_text_label,
                "Finalising…",
                text_color=COLORS["text_primary"],
            )
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("stop_ui_acknowledged_at")
        except Exception:
            pass
        # `is_listening` can still be True while the session winds down, so
        # the indicator is forced idle rather than reporting "Connected."
        self._sync_connection_indicator(force_idle=True)
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
            self._set_dynamic_text(
                self.live_indicator, "○ IDLE", text_color=COLORS["live_idle"]
            )
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
            self._set_dynamic_text(
                self.status_text_label,
                "Finalizing...",
                text_color=COLORS["text_primary"],
            )
        # `is_listening` can still be True while the session winds down, so
        # the indicator is forced idle rather than reporting "Connected."
        self._sync_connection_indicator(force_idle=True)
        self._draw_waveform(idle=True)

    def _set_stopped_ui_state(self):
        """Restore idle controls and show Stopped after graceful shutdown."""
        self._set_listen_button_state(False)
        if self.status_text_label is not None:
            self._set_dynamic_text(
                self.status_text_label,
                "Stopped",
                text_color=COLORS["text_secondary"],
            )

    # -----------------------------------------------------------------------
    # DeepL translation (V4)
    # -----------------------------------------------------------------------
    def _initialize_translation(self):
        """Show translation status; worker starts with each listening session."""
        self.translation_enabled = bool(TRANSLATION_ENABLED) and has_deepl_api_key()
        self.translation_worker = None
        self._translation_segment_seq = 0
        # fixes TASK_3A_FINDINGS.md Item 1/2: identity-keyed state, not a flat list.
        self._translation_items_by_utterance = {}
        self._pending_translations_by_utterance = {}
        self._translation_debounce_after_ids = {}
        if self.translated_verse_box is None:
            return
        if not TRANSLATION_ENABLED:
            msg = "Translation disabled."
        elif not has_deepl_api_key():
            msg = "Translation unavailable (missing DEEPL_AUTH_KEY)."
        else:
            msg = ""
        self._translation_status_message = msg
        if msg:
            self.translated_verse_box._placeholder_text = msg
            self._show_text_placeholder(self.translated_verse_box)
        else:
            self.translated_verse_box._placeholder_text = ""
            self._show_text_placeholder(self.translated_verse_box)

    def _start_translation_session(self):
        """Start / reset the async DeepL worker for the current run."""
        from pathlib import Path

        from alpha.translation import TranslationWorker
        from alpha.utils.run_identity import get_run_folder, get_run_id

        run_id = str(get_run_id() or "")
        run_folder = get_run_folder()
        evidence = None
        if run_folder:
            evidence = Path(run_folder) / "translation"
        if self.translation_worker is not None:
            try:
                self.translation_worker.stop_accepting()
                self.translation_worker.shutdown(timeout_seconds=0.5)
            except Exception:
                pass
            self.translation_worker = None
        self._translation_segment_seq = 0
        # fixes TASK_3A_FINDINGS.md Item 1/2: identity-keyed state, not a flat list.
        self._translation_items_by_utterance = {}
        self._pending_translations_by_utterance = {}
        self._translation_debounce_after_ids = {}
        if not TRANSLATION_ENABLED:
            self.translation_enabled = False
            self._set_translation_status("Translation disabled.")
            return
        if not has_deepl_api_key():
            self.translation_enabled = False
            self._set_translation_status("Translation unavailable (missing DEEPL_AUTH_KEY).")
            return
        worker = TranslationWorker(
            run_id=run_id,
            evidence_dir=evidence,
            on_translation_ready=self._on_translation_worker_result,
        )
        started = worker.start()
        self.translation_worker = worker
        self.translation_enabled = bool(started)
        if started:
            self._translation_status_message = ""
            if self.translated_verse_box is not None:
                self.translated_verse_box._placeholder_text = ""
                self._clear_text_placeholder(self.translated_verse_box)
            try:
                from alpha.utils import live_pipeline_profile as lpp

                lpp.mark("translation_worker_ready_at")
            except Exception:
                pass
        else:
            self._set_translation_status(
                worker.status_message or "Translation unavailable."
            )

    def _set_translation_status(self, message: str):
        self._translation_status_message = message or ""
        box = self.translated_verse_box
        if box is None or not message:
            return
        box._placeholder_text = message
        self._show_text_placeholder(box)

    def submit_text_for_translation(
        self,
        text,
        speaker=None,
        timestamp=None,
        *,
        force_flush_previous: bool = False,
        replace_pending: bool = False,
        canonical_utterance_id: str = "",
        source_version: int = 1,
        source_record_id: str = "",
    ):
        """Enqueue a newly committed Stable segment for async DeepL translation.

        Debounces rapid Stable *updates* of the same utterance so only the
        latest accepted text creates one provider request. A new sentence
        (force_flush_previous) flushes any pending job first.

        fixes TASK_3A_FINDINGS.md Item 2: pending payload and debounce timer
        are keyed by (session_id, canonical_utterance_id) instead of one
        shared slot, so one utterance's submission can never overwrite
        another's. When canonical_utterance_id is unknown, a unique
        per-call key is used instead of a shared "" key, so unidentified
        submissions never collide with each other either (fail-closed).
        """
        worker = self.translation_worker
        if worker is None or not self.translation_enabled:
            return
        cleaned = (text or "").strip()
        if not cleaned:
            return

        session_id = str(getattr(self, "_live_session_id", "") or "")
        utterance_key = str(canonical_utterance_id or "")
        if utterance_key:
            key = (session_id, utterance_key)
        else:
            import uuid as _uuid

            key = (session_id, f"__unkeyed_{_uuid.uuid4().hex}")

        pending_map = self._pending_translations_by_utterance
        timer_map = self._translation_debounce_after_ids

        if force_flush_previous:
            self._flush_pending_translation_submit(key)

        pending_map[key] = {
            "text": cleaned,
            "speaker": speaker,
            "timestamp": timestamp,
            "replace_pending": bool(replace_pending),
            "session_id": session_id,
            "canonical_utterance_id": utterance_key,
            "source_version": int(source_version or 1),
            "source_record_id": str(source_record_id or ""),
        }
        after_id = timer_map.pop(key, None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass

        # Updates coalesce; first add of a sentence also uses a short debounce
        # so an immediate follow-up update can supersede before DeepL starts.
        delay_ms = 120 if force_flush_previous else 350

        def _arm() -> None:
            timer_map[key] = self.after(
                delay_ms, lambda: self._flush_pending_translation_submit(key)
            )

        try:
            from alpha.utils.ui_thread_guard import is_ui_main_thread

            if is_ui_main_thread():
                _arm()
            else:
                self._run_on_ui_thread(_arm)
        except Exception:
            self._flush_pending_translation_submit(key)

    def _flush_pending_translation_submit(self, key) -> None:
        """Actually enqueue the debounced Stable translation job (UI thread).

        fixes TASK_3A_FINDINGS.md Item 2: operates on the one payload
        identified by `key` -- never a single shared payload.
        """
        self._translation_debounce_after_ids.pop(key, None)
        payload = self._pending_translations_by_utterance.pop(key, None)
        if not payload:
            return
        worker = self.translation_worker
        if worker is None or not self.translation_enabled:
            return
        cleaned = (payload.get("text") or "").strip()
        if not cleaned:
            return
        session_id = str(payload.get("session_id") or "")
        if session_id and session_id != str(getattr(self, "_live_session_id", "") or ""):
            return
        self._translation_segment_seq = int(self._translation_segment_seq or 0) + 1
        segment_id = self._translation_segment_seq
        source_lang = getattr(self, "_listen_language", None) or self.source_language.get()
        try:
            from alpha.utils.run_identity import get_run_id

            run_id = str(get_run_id() or "")
        except Exception:
            run_id = ""
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark(
                "translation_job_accepted_at",
                session_id=session_id or None,
                segment_id=segment_id,
            )
        except Exception:
            pass
        canonical_utterance_id = str(payload.get("canonical_utterance_id") or "")
        source_version = int(payload.get("source_version") or 1)
        # Show queued loading line immediately (cleared on terminal UI result).
        self._show_translation_loading_item(
            segment_id=segment_id,
            session_id=session_id or str(getattr(self, "_live_session_id", "") or ""),
            canonical_utterance_id=canonical_utterance_id,
            source_version=source_version,
        )
        accepted = worker.enqueue_stable_segment(
            segment_id=segment_id,
            source_language=str(source_lang or ""),
            source_text=cleaned,
            stable_commit_timestamp=time.time(),
            is_interim=False,
            run_id=run_id,
            canonical_utterance_id=canonical_utterance_id,
            source_version=source_version,
            source_record_id=str(payload.get("source_record_id") or ""),
            session_id=session_id,
        )
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark(
                "translation_job_enqueued_at",
                session_id=session_id or None,
                segment_id=segment_id,
                accepted=bool(accepted),
            )
        except Exception:
            pass
        if not accepted:
            self._clear_translation_loading_item(
                segment_id=segment_id,
                terminal_state="rejected",
                session_id=session_id,
                canonical_utterance_id=canonical_utterance_id,
                source_version=source_version,
            )

    def flush_pending_translation_submissions(self, timeout_seconds: float = 2.0) -> int:
        """fixes TASK_7_REPORT.md: submit_text_for_translation() debounces a
        newly committed segment behind a 120-350ms Tk .after() timer before
        actually calling worker.enqueue_stable_segment(). Stop's finalize
        sequence (stop_finalize_worker.py) previously called
        translation_worker.stop_accepting()/shutdown() with no knowledge of
        this pending-but-unfired timer, so a segment committed right before
        Stop (e.g. via utterance_lifecycle.py's inactivity_timeout_fallback,
        which by definition tends to land close to Stop) could have its
        debounce timer abandoned -- never enqueued, never counted, never
        logged, even though its canonical ledger commit had already
        succeeded. This flushes every still-pending debounced submission
        synchronously before Stop stops accepting new translation jobs.

        Must run on the Tk thread (the flush touches translation-loading UI
        widgets), but is expected to be called from stop_finalize_worker.py's
        background finalize-step thread -- so unlike _run_on_ui_thread (which
        only schedules and returns), this blocks the calling thread until the
        Tk-thread flush actually completes or the bounded timeout elapses.
        """
        pending_map = getattr(self, "_pending_translations_by_utterance", None)
        if not pending_map:
            return 0
        keys = list(pending_map.keys())
        if not keys:
            return 0

        flushed_count = {"n": 0}

        def _do_flush(done=None) -> None:
            try:
                for key in keys:
                    if key in pending_map:
                        flushed_count["n"] += 1
                    self._flush_pending_translation_submit(key)
            finally:
                if done is not None:
                    done.set()

        try:
            from alpha.utils.ui_thread_guard import is_ui_main_thread

            if is_ui_main_thread():
                _do_flush()
            else:
                from alpha.utils.ui_event_bus import get_ui_event_bus

                done = threading.Event()
                get_ui_event_bus().post_schedule_after(0, lambda: _do_flush(done))
                done.wait(timeout=timeout_seconds)
        except Exception:
            try:
                _do_flush()
            except Exception:
                pass
        return int(flushed_count["n"])

    def _show_translation_loading_item(
        self,
        *,
        segment_id: int,
        session_id: str,
        canonical_utterance_id: str = "",
        source_version: int = 1,
    ):
        """Insert a temporary loading line for one translation job."""
        box = self.translated_verse_box
        if box is None:
            return
        registry = getattr(self, "_translation_loading_items", None)
        if registry is None:
            registry = {}
            self._translation_loading_items = registry
        if int(segment_id) in registry:
            return
        mark_name = f"tr_load_{int(segment_id)}"
        # Item 68: the pending row is a progress hint only. The completed
        # translation is appended at tk.END regardless of where this row sat,
        # and ordering comes from the worker's translation_sequence buffer, so
        # hiding it cannot reorder anything.
        #
        # The MARK is skipped along with the text, deliberately. A mark left at
        # "end" with nothing under it ends up positioned before the next
        # appended line, and this row's removal path deletes
        # `mark -> mark lineend + 1 chars` -- which would then delete a real
        # translation. Both removal sites already guard with `box.compare(...)`
        # inside `except Exception`, so an absent mark is a no-op there.
        if TRANSLATION_PENDING_PLACEHOLDER_VISIBLE:
            self._clear_text_placeholder(box)
            box.configure(state="normal")
            try:
                box.mark_set(mark_name, "end")
            except Exception:
                pass
            label = self._ui_speaker_label_text()
            box.insert(tk.END, label)
            box.insert(tk.END, "… ⏳\n", "body")
            box.configure(state="disabled")
        registry[int(segment_id)] = {
            "mark": mark_name,
            "session_id": session_id,
            "state": "queued",
            "created_at": time.perf_counter(),
            "canonical_utterance_id": str(canonical_utterance_id or ""),
            "source_version": int(source_version or 1),
        }
        # fixes TASK_3A_FINDINGS.md Item 1: track the loading item by
        # canonical_utterance_id too, so a later revision can find and
        # remove exactly this item instead of guessing by position.
        #
        # This must NEVER overwrite an entry that is already DISPLAYED. A
        # revision re-submits the same canonical_utterance_id while the previous
        # translation is still on screen, and this write used to replace that
        # entry's `tr_done_<id>_<n>` mark and its recorded line count with a
        # pending row's `tr_load_<segment_id>`. The displayed line then became
        # unreachable: nothing could remove it, so the superseded translation
        # stayed beside its replacement for the rest of the session, and the
        # removal added for item 84 never fired even once.
        #
        # Measured by replaying run `...20260820-162804`'s real timeline through
        # these exact methods: 144 completed translations, 11 utterances
        # translated two or three times, **12 superseded renderings still
        # visible** in the reproduced pane. The first utterance of the session
        # is one of them, which is why it reads as broken immediately.
        #
        # The pending job does not need this dict at all -- it is already
        # tracked in `_translation_loading_items`, keyed by segment_id, and that
        # is what `_clear_translation_loading_item` reads to find the pending
        # row. The entry here exists only so an utterance with NO displayed line
        # yet can still be found by identity.
        utterance_key = str(canonical_utterance_id or "")
        if utterance_key:
            displayed = self._translation_items_by_utterance.get(utterance_key)
            already_on_screen = (
                isinstance(displayed, dict) and displayed.get("state") == "completed"
            )
            if not already_on_screen:
                self._translation_items_by_utterance[utterance_key] = {
                    "segment_id": int(segment_id),
                    "mark": mark_name,
                    "source_version": int(source_version or 1),
                    "state": "loading",
                }
        try:
            box.see(tk.END)
        except Exception:
            pass

    def _clear_translation_loading_item(
        self,
        *,
        segment_id: int,
        terminal_state: str,
        session_id: str = "",
        replace_with_text: str | None = None,
        canonical_utterance_id: str = "",
        source_version: int = 1,
    ):
        """Remove loading glyph for one job; optionally write the final line."""
        box = self.translated_verse_box
        registry = getattr(self, "_translation_loading_items", None) or {}
        item = registry.pop(int(segment_id), None)
        if box is None:
            return
        mark_name = (item or {}).get("mark") or f"tr_load_{int(segment_id)}"
        utterance_key = str(canonical_utterance_id or (item or {}).get("canonical_utterance_id") or "")
        # fixes TASK_3A_FINDINGS.md Item 4: a stale (superseded) provider
        # result must never overwrite a newer version already displayed for
        # this utterance -- checked before any text is written.
        if replace_with_text and utterance_key:
            existing = self._translation_items_by_utterance.get(utterance_key)
            if existing is not None and int(source_version or 1) < int(
                existing.get("source_version") or 1
            ):
                self._log_translation_display_skip(
                    reason="stale_provider_result_ignored",
                    canonical_utterance_id=utterance_key,
                    tracked_version=existing.get("source_version"),
                    incoming_version=source_version,
                )
                return
        box.configure(state="normal")
        try:
            if box.compare(mark_name, ">=", "1.0"):
                # `mark_name` is the LOADING mark (`tr_load_<segment_id>`), and
                # a loading glyph is always exactly one row however many lines
                # the finished translation will occupy. This used to be handed
                # the COMPLETED entry for this utterance, so on a revision it
                # deleted that entry's line count starting at the loading
                # mark -- eating the rows that happened to follow the pending
                # row. The completed entry is a different entry with its own
                # mark, removed just below.
                self._delete_translation_entry(box, mark_name, {"entry_lines": 1})
            box.mark_unset(mark_name)
        except Exception:
            pass
        # A revision: this utterance already has a COMPLETED translation on
        # screen, and the write below would append the new one beside it and
        # then overwrite the registry entry -- losing the old mark, so nothing
        # could ever reclaim that line again. It stayed for the rest of the
        # session and reached the client's file through
        # `_get_translated_transcript_for_copy_export`'s widget-read fallback.
        #
        # Measured on run `v3.3.5.5.8.5.26.5.3-20260820-140328` (Japanese
        # source): 11 of 120 canonical commits carried `applied_action:
        # revise`, all 120 utterance decisions were CREATE_NEW with
        # `revision_target_id` unset, and those same 11 ids were translated two
        # or three times -- 133 jobs for 120 ids. Six near-duplicate pairs were
        # visible in the pane, about 4% of it shown twice, e.g. "...tiring to
        # say long sentences, isn't it?" beside "...tiring to say long words in
        # advance, isn't it?". The transcript pane was clean throughout, which
        # is why this reads as a translation-side defect only.
        #
        # Done HERE rather than at commit time so the superseded line survives
        # until its replacement actually exists: removing it when the revision
        # commits would blank the utterance for the whole translation
        # round-trip, and lose it outright if that job then failed.
        #
        # Identity, never position or similarity: only a matching
        # `canonical_utterance_id` causes a removal, which is the same proof
        # `duplicate_protection.py` requires before replacing anything. Two
        # genuinely distinct utterances can be near-identical in text.
        if replace_with_text and utterance_key:
            previous = (self._translation_items_by_utterance or {}).get(utterance_key)
            previous_mark = (previous or {}).get("mark") if isinstance(previous, dict) else None
            if previous_mark and previous.get("state") == "completed":
                try:
                    if box.compare(previous_mark, ">=", "1.0"):
                        self._delete_translation_entry(box, previous_mark, previous)
                    box.mark_unset(previous_mark)
                except Exception:
                    pass
        if replace_with_text:
            label = self._ui_speaker_label_text()
            cleaned = (replace_with_text or "").strip()
            # `tk.END` is one character past where `insert(tk.END, ...)`
            # actually writes, because Tk maintains a trailing newline of its
            # own. Capturing the start there put `completed_mark` on the line
            # AFTER this translation, so
            # `delete(mark, "mark lineend + 1 chars")` in
            # `_remove_translation_item_for_utterance` removed a newline and
            # left the stale translation on screen -- a revision added its new
            # text and the superseded line stayed above it. Probed directly
            # against real Tk: removing the middle of three completed
            # translations left all three. It also mis-sized the
            # `speaker_label` tag range for the same reason.
            #
            # Third instance of this shape in this file, after `interim_anchor`
            # and `segment_anchor`; same fix. Re-establish the empty last line
            # so the start is a line start, measure at `"end-1c"`, and give the
            # mark LEFT gravity so a later append at that position cannot carry
            # it forward.
            if box.index("end-1c") != box.index("end-1c linestart"):
                box.insert(tk.END, "\n")
            start_idx = box.index("end-1c")
            tag_name = "speaker_label"
            if tag_name not in box.tag_names():
                box.tag_configure(
                    tag_name,
                    foreground=COLORS.get("text_primary", "#111111"),
                    font=("Segoe UI", 12, "bold"),
                )
            # Item 83. The English pane reads as 2-3 sentence lines after item
            # 82; the translation was still one line per record -- measured on
            # a real run, 36 records at a median of 139 characters and up to
            # 769, holding 4.9 sentences each. Grouped by the same idea, using
            # the rule that fits the target language.
            parts = self._readable_translation_parts(cleaned) or [cleaned]
            for part in parts:
                part_start = box.index("end-1c")
                box.insert(tk.END, label)
                box.tag_add(tag_name, part_start, box.index("end-1c"))
                box.insert(tk.END, part + "\n", "body")
            # How many logical lines this entry occupies. The removal paths
            # delete a fixed ONE line (item 74(b)); grouping is what makes that
            # latent defect live, so the count travels with the entry and the
            # removal replays it. Without this a revised translation would
            # orphan every line but the first, and those orphans reach the
            # export through the widget-read fallback.
            entry_lines = len(parts)
            line = f"{label}{cleaned}"
            # fixes TASK_3A_FINDINGS.md Item 1: track this completed line by
            # canonical_utterance_id (with its own text mark) instead of a
            # flat positional list, so a later revision can remove exactly
            # this line -- never "whatever is currently last".
            if utterance_key:
                completed_mark = f"tr_done_{utterance_key}_{int(source_version or 1)}"
                try:
                    box.mark_set(completed_mark, start_idx)
                    # LEFT gravity: a later translation appended at this exact
                    # position must not carry this mark forward with it.
                    box.mark_gravity(completed_mark, "left")
                except Exception:
                    completed_mark = None
                self._translation_items_by_utterance[utterance_key] = {
                    "segment_id": int(segment_id),
                    "mark": completed_mark,
                    "source_version": int(source_version or 1),
                    "state": "completed",
                    "line_text": line,
                    # Item 74(b) / 83: how many logical lines to remove when a
                    # revision supersedes this entry. One is no longer a safe
                    # assumption now that a translation is grouped.
                    "entry_lines": int(entry_lines),
                }
            else:
                self._log_translation_display_skip(
                    reason="completed_translation_missing_canonical_utterance_id",
                    segment_id=int(segment_id),
                )
        elif utterance_key:
            # Job failed/rejected/superseded with no text written -- drop the
            # stale "loading" tracking entry so it can't be mistaken for a
            # live item by a later revision, but only if nothing newer has
            # already replaced it.
            tracked = self._translation_items_by_utterance.get(utterance_key)
            if tracked is not None and tracked.get("segment_id") == int(segment_id):
                self._translation_items_by_utterance.pop(utterance_key, None)
        box.configure(state="disabled")
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark(
                "loading_indicator_cleared_at",
                session_id=session_id or None,
                segment_id=int(segment_id),
                terminal_state=terminal_state,
            )
        except Exception:
            pass
        try:
            box.see(tk.END)
        except Exception:
            pass

    def loading_indicators_pending(self) -> int:
        return len(getattr(self, "_translation_loading_items", None) or {})

    def _on_translation_worker_result(self, result):
        """Marshal translation results onto the UI thread (no network here)."""
        session_id = str(getattr(self, "_live_session_id", "") or "")
        stats = getattr(self, "_ui_callback_stats", None)
        if isinstance(stats, dict):
            stats["scheduled"] = int(stats.get("scheduled", 0) or 0) + 1
        try:
            worker = getattr(self, "translation_worker", None)
            if worker is not None and hasattr(worker, "note_ui_update_scheduled"):
                worker.note_ui_update_scheduled(result)
        except Exception:
            pass
        self._run_on_ui_thread(
            lambda: self._handle_translation_worker_result(result, session_id=session_id)
        )

    def _handle_translation_worker_result(self, result, *, session_id: str = ""):
        """Display an ordered translation result in the translation panel."""
        if result is None:
            return
        stats = getattr(self, "_ui_callback_stats", None)
        if isinstance(stats, dict):
            stats["started"] = int(stats.get("started", 0) or 0) + 1
        try:
            from alpha.utils.session_runtime import session_accepts_callback

            if not session_accepts_callback(self, session_id):
                if isinstance(stats, dict):
                    stats["cancelled"] = int(stats.get("cancelled", 0) or 0) + 1
                return
        except Exception:
            current = str(getattr(self, "_live_session_id", "") or "")
            if session_id and current and session_id != current:
                return
        current = str(getattr(self, "_live_session_id", "") or "")
        status = getattr(result, "status", "")
        terminal = str(
            getattr(result, "terminal_state", None) or status or ""
        ).strip().lower()
        segment_id = int(getattr(result, "segment_id", 0) or 0)
        # fixes TASK_3A_FINDINGS.md Item 1/4: carry canonical identity through
        # to every display-clearing/writing call below.
        canonical_utterance_id = str(getattr(result, "canonical_utterance_id", "") or "")
        source_version = int(getattr(result, "source_version", 1) or 1)
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark(
                "ordered_commit_ready_at",
                session_id=current or None,
                segment_id=segment_id,
                status=status,
            )
            lpp.mark(
                "translation_ui_callback_started_at",
                session_id=current or None,
                segment_id=segment_id,
            )
        except Exception:
            pass
        if status == "quota_exceeded":
            self._set_translation_status("Translation paused (quota exceeded).")
        if bool(getattr(result, "obsolete_result_rejected", False)) or terminal in {
            "superseded",
            "cancelled",
        }:
            self._clear_translation_loading_item(
                segment_id=segment_id,
                terminal_state=terminal or "superseded",
                session_id=current or session_id,
                canonical_utterance_id=canonical_utterance_id,
                source_version=source_version,
            )
            if isinstance(stats, dict):
                stats["loading_cleared"] = int(stats.get("loading_cleared", 0) or 0) + 1
                stats["completed"] = int(stats.get("completed", 0) or 0) + 1
            try:
                worker = getattr(self, "translation_worker", None)
                if worker is not None and hasattr(worker, "mark_ui_update_completed"):
                    worker.mark_ui_update_completed(segment_id, result=result)
            except Exception:
                pass
            return
        translated = (getattr(result, "translated_text", "") or "").strip()
        if not translated:
            # Terminal failure / cancel: still clear loading indicator.
            if terminal in {
                "permanently_failed",
                "cancelled",
                "cancelled_during_bounded_shutdown",
                "quota_exceeded",
                "failed",
                "error",
                "superseded",
            } or status in {
                "permanently_failed",
                "cancelled",
                "cancelled_during_bounded_shutdown",
                "quota_exceeded",
                "failed",
                "error",
                "superseded",
            }:
                self._clear_translation_loading_item(
                    segment_id=segment_id,
                    terminal_state=terminal or status or "failed",
                    session_id=current or session_id,
                    canonical_utterance_id=canonical_utterance_id,
                    source_version=source_version,
                )
                if isinstance(stats, dict):
                    stats["loading_cleared"] = int(stats.get("loading_cleared", 0) or 0) + 1
                    stats["completed"] = int(stats.get("completed", 0) or 0) + 1
                try:
                    worker = getattr(self, "translation_worker", None)
                    if worker is not None and hasattr(worker, "mark_ui_update_completed"):
                        worker.mark_ui_update_completed(
                            segment_id, result=result
                        )
                except Exception:
                    pass
            return
        self._append_translation_result(
            speaker=None,
            original_text=getattr(result, "source_text", "") or "",
            translated_text=translated,
            timestamp=getattr(result, "completed_at", None)
            or getattr(result, "provider_completed_at", None),
            segment_id=segment_id,
            session_id=current or session_id,
            canonical_utterance_id=canonical_utterance_id,
            source_version=source_version,
        )
        if isinstance(stats, dict):
            stats["widget_updated"] = int(stats.get("widget_updated", 0) or 0) + 1
            stats["loading_cleared"] = int(stats.get("loading_cleared", 0) or 0) + 1
            stats["completed"] = int(stats.get("completed", 0) or 0) + 1
        try:
            worker = getattr(self, "translation_worker", None)
            if worker is not None and hasattr(worker, "mark_ui_update_completed"):
                worker.mark_ui_update_completed(segment_id, result=result)
        except Exception:
            pass
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark(
                "translation_ui_completed_at",
                session_id=current or session_id or None,
                segment_id=segment_id,
            )
        except Exception:
            pass

    def record_transcript_segment(self, speaker, text, timestamp=None):
        """Legacy hook; transcript store is updated by stabilization mixin."""
        return

    def _record_translation_segment(
        self,
        original_text,
        translated_text,
        speaker=None,
        timestamp=None,
        canonical_utterance_id="",
    ):
        """Attach a translation to a stored transcript segment when possible."""
        try:
            self.transcript_store.add_translation(
                original_text=original_text,
                translated_text=translated_text,
                speaker=speaker,
                timestamp=timestamp,
                canonical_utterance_id=canonical_utterance_id,
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
        *,
        segment_id: int | None = None,
        session_id: str = "",
        canonical_utterance_id: str = "",
        source_version: int = 1,
    ):
        """Insert translated text into the translation panel (UI thread only)."""
        try:
            from alpha.utils.session_runtime import session_accepts_callback

            if session_id and not session_accepts_callback(self, session_id):
                return
        except Exception:
            current = str(getattr(self, "_live_session_id", "") or "")
            if session_id and current and session_id != current:
                return
        current = str(getattr(self, "_live_session_id", "") or "")
        box = self.translated_verse_box
        if box is None:
            return

        cleaned = (translated_text or "").strip()
        if segment_id is not None:
            self._clear_translation_loading_item(
                segment_id=int(segment_id),
                terminal_state="completed",
                session_id=current or session_id,
                replace_with_text=cleaned,
                canonical_utterance_id=canonical_utterance_id,
                source_version=source_version,
            )
        else:
            # fixes TASK_3A_FINDINGS.md Item 4: check for a newer version
            # already displayed before writing a result with no segment_id.
            utterance_key = str(canonical_utterance_id or "")
            if utterance_key:
                existing = self._translation_items_by_utterance.get(utterance_key)
                if existing is not None and int(source_version or 1) < int(
                    existing.get("source_version") or 1
                ):
                    self._log_translation_display_skip(
                        reason="stale_provider_result_ignored",
                        canonical_utterance_id=utterance_key,
                        tracked_version=existing.get("source_version"),
                        incoming_version=source_version,
                    )
                    return
            self._clear_text_placeholder(box)
            box.configure(state="normal")
            label = self._ui_speaker_label_text()
            # A revision supersedes the entry already on screen for this
            # utterance. Same rule and same reasons as the `segment_id` branch
            # in `_clear_translation_loading_item`: identity only, never
            # position or similarity, and done here at write time so the old
            # line survives until its replacement actually exists.
            if utterance_key:
                previous = (self._translation_items_by_utterance or {}).get(
                    utterance_key
                )
                previous_mark = (
                    (previous or {}).get("mark")
                    if isinstance(previous, dict)
                    else None
                )
                if previous_mark and previous.get("state") == "completed":
                    try:
                        if box.compare(previous_mark, ">=", "1.0"):
                            self._delete_translation_entry(box, previous_mark, previous)
                        box.mark_unset(previous_mark)
                    except Exception:
                        pass
            # Same correction as the completed branch above: `tk.END` is one
            # character past the real write position, so a mark placed there
            # lands on the following line and cannot remove this translation
            # when a revision supersedes it.
            if box.index("end-1c") != box.index("end-1c linestart"):
                box.insert(tk.END, "\n")
            start_idx = box.index("end-1c")
            tag_name = "speaker_label"
            if tag_name not in box.tag_names():
                box.tag_configure(
                    tag_name,
                    foreground=COLORS.get("text_primary", "#111111"),
                    font=("Segoe UI", 12, "bold"),
                )
            # Item 83's grouping, which this branch used to skip -- it wrote the
            # whole translation as one raw line. That made it a fifth ungrouped
            # render path, the exact shape item 82 was filed for, and it also
            # recorded no line count, so a later revision could only ever remove
            # the first line of what it wrote.
            parts = self._readable_translation_parts(cleaned) or [cleaned]
            for part in parts:
                part_start = box.index("end-1c")
                box.insert(tk.END, label)
                box.tag_add(tag_name, part_start, box.index("end-1c"))
                box.insert(tk.END, part + "\n", "body")
            box.configure(state="disabled")
            entry_lines = len(parts)
            line = f"{label}{cleaned}"
            # fixes TASK_3A_FINDINGS.md Item 1: identity-keyed tracking
            # instead of appending to a flat positional list.
            if utterance_key:
                completed_mark = f"tr_done_{utterance_key}_{int(source_version or 1)}"
                try:
                    box.mark_set(completed_mark, start_idx)
                    # LEFT gravity: a later translation appended at this exact
                    # position must not carry this mark forward with it.
                    box.mark_gravity(completed_mark, "left")
                except Exception:
                    completed_mark = None
                self._translation_items_by_utterance[utterance_key] = {
                    "segment_id": segment_id,
                    "mark": completed_mark,
                    "source_version": int(source_version or 1),
                    "state": "completed",
                    "line_text": line,
                    "entry_lines": int(entry_lines),
                }
            else:
                self._log_translation_display_skip(
                    reason="completed_translation_missing_canonical_utterance_id",
                    segment_id=segment_id,
                )

        self.last_translation_speaker = "Speaker"
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark(
                "translation_ui_rendered_at",
                session_id=current or session_id or None,
                segment_id=segment_id,
            )
            lpp.mark(
                "translation_widget_updated_at",
                session_id=current or session_id or None,
                segment_id=segment_id,
            )
            lpp.mark(
                "loading_indicator_cleared_at",
                session_id=current or session_id or None,
                segment_id=segment_id,
            )
        except Exception:
            pass
        if hasattr(box, "_scrollbar"):
            self.check_scrollbar_visibility(box, box._scrollbar)
        try:
            box.see(tk.END)
        except Exception:
            pass
        self._record_translation_segment(
            original_text,
            translated_text,
            speaker=speaker,
            timestamp=timestamp,
            canonical_utterance_id=canonical_utterance_id,
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
                self._set_dynamic_text(self.status_text_label, payload.message)

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
                lambda: messagebox.showerror(t("Error"), t(payload.message))
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
        """Show immediate Starting feedback while Deepgram/audio initialize off-UI."""
        if self.status_text_label is not None:
            self._set_dynamic_text(
                self.status_text_label,
                "Starting…",
                text_color=COLORS["text_primary"],
            )
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("start_ui_acknowledged_at")
        except Exception:
            pass
        # Keep button responsive lock visible immediately.
        try:
            if self.listen_button is not None:
                self._set_dynamic_text(self.listen_button, "Starting…", state="disabled")
            if getattr(self, "listen_button_menu", None) is not None:
                self._set_dynamic_text(
                    self.listen_button_menu, "Starting…", state="disabled"
                )
        except Exception:
            pass

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

        # Item 46, WIRED. `service_status.preflight_credentials` is now the only
        # thing that decides whether credentials permit a Start.
        #
        # It shipped 2026-08-12 as library code with **no production caller at
        # all** and was reopened 2026-08-16 for exactly that. Meanwhile this
        # function answered the same question itself -- two authorities for one
        # decision (§0 rule 2) -- and only ever asked it about Deepgram, so a
        # missing DeepL key was never reported at Start in any form. The
        # operator started a session and found out there was no translation by
        # watching the pane stay empty.
        #
        # Deepgram blocks the Start; DeepL does not. A session with no
        # translation is degraded, not broken, and refusing to start would be
        # worse than running transcript-only. That asymmetry is why
        # `preflight_credentials` returns structured problems instead of
        # raising, and why the non-blocking one is reported WITHOUT a modal --
        # a dialog on every Start of a deliberately transcript-only setup would
        # train the operator to dismiss the one that matters.
        #
        # `MISSING_API_KEY_MSG` / `PLACEHOLDER_API_KEY_MSG` are left defined but
        # are no longer used here; the module's own messages say what the
        # consequence is, not just what is wrong.
        try:
            from alpha.utils.service_status import (
                blocking_problems,
                preflight_credentials,
            )

            credential_problems = preflight_credentials()
        except Exception as exc:
            # The preflight is a guard, not a gate on its own health: if it
            # cannot run, that must never be what stops a session starting.
            print(f"[Preflight] credential check failed: {exc}")
            credential_problems = []
        blocking = blocking_problems(credential_problems)
        if blocking:
            problem = blocking[0]
            print(problem.message)
            messagebox.showerror(f"{problem.service} API Key", problem.message)
            self.publish_error_event(
                problem.message,
                source="config",
                recoverable=True,
            )
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "START_BLOCKED_BY_CREDENTIAL_PREFLIGHT",
                    service=problem.service,
                    code=problem.code,
                )
            except Exception:
                pass
            return
        for problem in credential_problems:
            print(problem.message)
            self.publish_error_event(
                problem.message,
                source="config",
                recoverable=True,
            )
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "START_CREDENTIAL_WARNING",
                    service=problem.service,
                    code=problem.code,
                )
            except Exception:
                pass

        # Item 47 follow-up: clear the previous session's connection state
        # before this one starts.
        #
        # `_dg_disconnected_at` is set on an unexpected close and cleared ONLY
        # by `_mark_deepgram_gap_if_any`, which runs on `_deepgram_on_open`. A
        # session that is stopped while still disconnected therefore leaves it
        # set forever, and `_dg_auth_failed` survives the same way. Nothing read
        # either across a session boundary until the indicator did, so this was
        # latent: the next Start would immediately show "Reconnecting" -- with a
        # gap measured from the PREVIOUS session, so the message grows without
        # bound -- or "Key rejected" for a key the operator had already fixed.
        #
        # Resetting here loses nothing: an outage belonging to the previous
        # session cannot be marked by this one, and item 72 already covers a gap
        # with no record to attach to.
        self._dg_disconnected_at = 0.0
        self._dg_auth_failed = False
        self._audio_device_changed = False
        self._connection_indicator_state = None
        dropdown_lang = self._strip_language_flag(self.source_language.get())
        # Capture dropdown before Start Listening so it cannot be overwritten silently.
        self._start_listening_dropdown_snapshot = dropdown_lang
        try:
            profile = self._build_language_profile(dropdown_lang)
            if not profile.get("selection_supported", True):
                reason = profile.get("unsupported_reason") or "unsupported_language_profile"
                message = (
                    "Selected language profile is not supported by current Deepgram mapping.\n"
                    f"Selection: {dropdown_lang}\nReason: {reason}"
                )
                print(f"[LANGUAGE] unsupported profile: {reason} ({dropdown_lang})")
                messagebox.showerror(t("Language Profile"), message)
                self.publish_error_event(message, source="language", recoverable=True)
                return

            deepgram_lang = self._resolve_deepgram_language(dropdown_lang)
        except Exception as exc:
            message = f"Language routing failed: {exc}"
            print(f"[LANGUAGE] {message}")
            messagebox.showerror(t("Language Routing"), message)
            self.publish_error_event(message, source="language", recoverable=True)
            return
        self._listen_language = deepgram_lang
        self._run_language_finalized = {
            "display_value": dropdown_lang,
            "resolved_code": deepgram_lang,
        }
        print(
            f"LANGUAGE_ROUTING_FINALIZED display_value={dropdown_lang} "
            f"resolved_code={deepgram_lang} deepgram_request_language={deepgram_lang} "
            f"run_id=(pending)"
        )
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log("START_PRECHECK_DONE", language=deepgram_lang)
            jp_accuracy_log("START_RUN_FOLDER_READY")
            jp_accuracy_log("START_WRITER_REBIND_SAFE_MODE")
            jp_accuracy_log("VALIDATION_DEFERRED_UNTIL_AFTER_STOP")
            jp_accuracy_log("UPLOAD_PACKAGE_DEFERRED_UNTIL_AFTER_STOP")
        except Exception:
            pass
        # Central session factory: fresh session ID + writable ledger + registries.
        try:
            from alpha.utils.session_runtime import begin_live_session

            begin_live_session(self)
        except RuntimeError as exc:
            print(f"[Session] Start rejected: {exc}")
            return
        except Exception as exc:
            print(f"[Session] begin_live_session failed: {exc}")
            self._live_session_id = f"sess-{time.time_ns()}"
            self._translation_loading_items = {}
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("start_button_clicked_at")
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
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("start_background_task_started_at")
        except Exception:
            pass

    def _start_listening_worker(self, dropdown_lang: str, deepgram_lang: str):
        """Heavy audio device scan, WASAPI/mic open, and Deepgram worker startup."""
        try:
            # Prefer session factory identity created on the UI Start path.
            if getattr(self, "_run_identity", None) is None:
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
        # Guard: Start Listening must not overwrite the dropdown selection.
        try:
            current_dropdown = self._strip_language_flag(self.source_language.get())
            snap = getattr(self, "_start_listening_dropdown_snapshot", dropdown_lang)
            if current_dropdown != snap:
                print(
                    f"[LANGUAGE] WARNING dropdown changed during start: "
                    f"snapshot={snap!r} current={current_dropdown!r}; "
                    f"keeping resolved_code={deepgram_lang!r}"
                )
        except Exception:
            pass
        run_id = ""
        try:
            from alpha.utils.run_identity import get_run_id

            run_id = str(get_run_id() or "")
        except Exception:
            run_id = ""
        print(
            f"LANGUAGE_ROUTING_FINALIZED display_value={dropdown_lang} "
            f"resolved_code={deepgram_lang} deepgram_request_language={deepgram_lang} "
            f"run_id={run_id or '(pending)'}"
        )
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
        # The UI switch feeds the SAME branch the Stage 1 benchmark flag uses,
        # rather than introducing a second way to skip the microphone. Either
        # reason is enough, so the benchmark flag keeps winning when set.
        if not self._microphone_capture_enabled:
            _system_audio_only = True
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "MICROPHONE_CAPTURE_DISABLED_BY_USER",
                    note="meeting audio only; the operator's own speech is not "
                    "captured or transcribed",
                )
            except Exception:
                pass
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
                self._set_dynamic_text(
                    self.status_text_label,
                    "Stopped",
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
        try:
            self._start_translation_session()
        except Exception as exc:
            logger.debug("Translation session start failed: %s", exc)
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
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("listening_state_visible_at")
        except Exception:
            pass
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
        try:
            from alpha.utils.session_runtime import mark_session_finalizing

            mark_session_finalizing(self)
        except Exception:
            self._finalizing_session_id = str(getattr(self, "_live_session_id", "") or "")
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("stop_button_clicked_at")
        except Exception:
            pass
        # fixes [BUG_FIX_ROADMAP.md Batch 1 item 1]: this used to call
        # self._flush_pending_translation_submit() with zero arguments,
        # but that method requires a `key` (no default) and always raised
        # TypeError, silently swallowed below -- it never once flushed
        # anything. flush_pending_translation_submissions() (plural, no
        # args needed, iterates every pending key) already runs later in
        # this same Stop sequence via stop_finalize_worker.py, and is the
        # complete, correct replacement (see its own docstring / TASK_7_
        # REPORT.md) -- so the broken call here was redundant, not just
        # broken, and is removed rather than patched.
        from alpha.utils.stop_finalize_worker import begin_stop_from_ui
        try:
            worker = getattr(self, "translation_worker", None)
            if worker is not None:
                worker.stop_accepting()
        except Exception:
            pass
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
                self._set_dynamic_text(
                    self.status_text_label,
                    "Stopped. Diagnostics may still be saving.",
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
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark("stop_completed_at")
        except Exception:
            pass
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

        generic = self._ui_speaker_label_text().rstrip()
        match = re.match(
            rf"^({re.escape(generic)}|\[Speaker (\d+)\])(.*)$", text, re.DOTALL
        )
        if match:
            label = generic
            body = match.group(3)
            tag = "speaker_label"
            if tag not in box.tag_names():
                box.tag_configure(
                    tag,
                    foreground=COLORS.get("text_primary", "#111111"),
                    font=("Segoe UI", 12, "bold"),
                )
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
        """Insert text with colored generic Speaker: labels into a read-only tk.Text widget."""
        self._clear_text_placeholder(text_widget)
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")

        label = self._ui_speaker_label_text().rstrip()
        parts = re.split(rf"({re.escape(label)}|\[Speaker \d+\])", content)
        for part in parts:
            if not part:
                continue
            if part == label or re.fullmatch(r"\[Speaker \d+\]", part):
                tag = "speaker_label"
                if tag not in text_widget.tag_names():
                    text_widget.tag_configure(
                        tag,
                        foreground=COLORS.get("text_primary", "#111111"),
                        font=("Segoe UI", 12, "bold"),
                    )
                text_widget.insert(
                    "end", label if part.startswith("[") else part, tag
                )
            else:
                text_widget.insert("end", part, "body")

        text_widget.configure(state="disabled")
        self.check_scrollbar_visibility(text_widget, text_widget._scrollbar)

    # -----------------------------------------------------------------------
    # Context menu
    # -----------------------------------------------------------------------
    def _create_context_menu(self):
        """Right-click a pane to COPY it.

        This used to offer "Clear All Text", which throws the meeting away and
        sat one slip of the mouse from wherever the reader was looking. Copying
        is what a right-click on text is for, and Clear is still a labelled
        button in the footer where a destructive action belongs.

        The entry is relabelled per pane when the menu opens, so the transcript
        offers "Copy Transcript" and the translation offers "Copy Translation"
        rather than one ambiguous "Copy". The label goes through `t()` at that
        moment, which also keeps it correct after a language switch without the
        menu having to be rebuilt.
        """
        self.context_menu = Menu(self, tearoff=0)
        self.context_menu.add_command(label="Copy Transcript")

        for box, label_source, copier in (
            (self.initial_verse_box, "Copy Transcript",
             self.copy_live_transcript_to_clipboard),
            (self.translated_verse_box, "Copy Translation",
             self.copy_translation_to_clipboard),
        ):
            if box is None:
                continue
            box.bind(
                "<Button-3>",
                lambda event, source=label_source, action=copier: (
                    self._show_context_menu(event, source, action)
                ),
            )

    def _show_context_menu(self, event, label_source="Copy Transcript", action=None):
        """Display the context menu at the cursor, labelled for this pane."""
        if action is None:
            action = self.copy_live_transcript_to_clipboard
        try:
            self.context_menu.entryconfigure(
                0, label=t(label_source), command=action
            )
            self.context_menu.tk_popup(event.x_root, event.y_root)
        except Exception as exc:
            print(f"Error showing the context menu: {exc}")
        finally:
            try:
                self.context_menu.grab_release()
            except Exception:
                pass

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
            messagebox.showerror(t("Error"), f"Could not update window state:\n{exc}")

    def toggle_microphone_capture(self):
        """Turn the microphone on or off, keeping both switches in step.

        ON means the microphone IS captured. It is OFF by default because Alpha
        transcribes ONE language per session and merges mic with system audio
        before Deepgram, so in a bilingual meeting the operator's own speech is
        fed to the other language's ASR. Turning it on is the right choice for a
        single-language session where the operator wants their own voice in the
        transcript too.

        Read at Start, exactly like the language dropdown, so it never changes
        the audio graph of a session already running. The switches are disabled
        while listening so the UI cannot advertise a setting that will not take
        effect until the next session.
        """
        try:
            if self._compact_mode and self._menu_visible:
                source = self.mic_switch_menu
            else:
                source = self.mic_switch
            self._microphone_capture_enabled = bool(
                source is not None and source.get() == 1
            )
            self._sync_mic_switches()
            print(
                "Microphone capture: "
                + ("ON" if self._microphone_capture_enabled else "OFF (meeting audio only)")
            )
        except Exception as exc:
            print(f"Error toggling microphone capture: {exc}")

    # -----------------------------------------------------------------------
    # UI language
    # -----------------------------------------------------------------------
    # Every widget whose text is set once when it is built and never touched
    # again. The English string is the key `t()` looks up, exactly as at the
    # call site, so this table cannot drift into saying something the widget
    # never showed. Anything repainted by its own owner -- the listen button,
    # the status line, the connection indicator, the summary button's
    # wide/compact wording -- is deliberately absent: `_retranslate_ui` asks
    # those owners to repaint instead.
    _RETRANSLATABLE_LABELS = (
        ("brand_sub_label", "Meeting Assistant"),
        ("always_on_top_switch", "Always on Top"),
        ("copy_translation_btn", "Copy Translation"),
        ("export_btn", "Export"),
        ("clear_btn", "Clear"),
        ("copy_translation_btn_menu", "Copy Translation"),
        ("export_btn_menu", "Export"),
        ("clear_btn_menu", "Clear"),
        ("always_on_top_switch_menu", "Always on Top"),
        ("menu_listening_label", "Listening to:"),
        ("menu_translate_label", "Translate to:"),
        ("menu_ui_language_label", "Display language:"),
    )

    # Widgets whose text changes at runtime. `_retranslate_dynamic_labels`
    # re-renders exactly these and nothing else.
    _DYNAMIC_TEXT_WIDGETS = (
        "listen_button",
        "listen_button_menu",
        "live_indicator",
        "status_text_label",
        "signal_label",
    )

    def _set_dynamic_text(self, widget, source, **kwargs):
        """Set a widget's text, and remember the English string it came from.

        Runtime text cannot be re-translated from what is on screen -- reading
        "認識を開始" back gives no way to know it was "Start Listening" -- so the
        source is kept beside the widget as it is written.
        """
        if widget is None:
            return
        try:
            widget._alpha_text_source = source
            widget.configure(text=t(source), **kwargs)
        except Exception:
            pass

    def _retranslate_dynamic_labels(self):
        """Re-render runtime text in the current language, and NOTHING else.

        This exists because the obvious alternative is wrong. Repainting the
        primary button by calling `_set_listen_button_state` also runs
        `_update_status_bar`, which sets `_listen_start_time = time.time()` and
        re-arms the animation jobs -- so switching language mid-meeting reset
        the session clock to 00:00, measured at 600 s elapsed before and 0 s
        after. The same call forced `state="normal"`, re-enabling a button that
        was deliberately disabled during "Starting…". A repaint must not go
        through anything that owns session state.
        """
        for attr in self._DYNAMIC_TEXT_WIDGETS:
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            source = getattr(widget, "_alpha_text_source", None)
            if not source:
                continue
            try:
                widget.configure(text=t(source))
            except Exception:
                pass

    def _open_ui_language_menu(self):
        """Post a dropdown under the header's language button.

        A `tkinter.Menu` rather than a combo box, and the reason is measured:
        at 800 design px -- where the hamburger menu takes over -- the header's
        right cluster has 49 design px left in Japanese and a CTkComboBox asks
        for 72. Tk draws this menu over the window, so it occupies no layout
        space at any width.
        """
        button = getattr(self, "ui_language_button", None)
        if button is None:
            return
        try:
            # Built once and refilled. A fresh `Menu(self)` per click is never
            # destroyed -- it stays a child of the root for the life of the
            # process -- so opening this menu repeatedly leaked one Tk widget
            # each time.
            menu = getattr(self, "ui_language_menu", None)
            if menu is None:
                menu = Menu(self, tearoff=0)
                self.ui_language_menu = menu
            menu.delete(0, "end")
            current = get_language()
            following_system = not has_saved_language()
            # First, and marked when nothing has been chosen: this is the state
            # a fresh install is in, and the only way back to it once someone
            # has picked a language.
            menu.add_command(
                label=("• " if following_system else "   ") + t("System default"),
                command=self._use_system_language,
            )
            menu.add_separator()
            for code in available_languages():
                # The language's own name, never translated: it is the only
                # label someone who cannot read the current language can use to
                # find their way back out.
                menu.add_command(
                    label=(
                        ("• " if (code == current and not following_system) else "   ")
                        # `.get`, not `[]`: a language added to the table
                        # without a display name would otherwise raise inside
                        # this loop, and the guard below would turn that into
                        # "the menu silently never opens".
                        + LANGUAGE_NAMES.get(code, code)
                    ),
                    command=lambda chosen=code: self._apply_ui_language(chosen),
                )
            # At the pointer, not the button's left edge. Tk already knows where
            # the cursor is, so this needs no <Button-1> binding to carry an
            # event in.
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        except Exception as exc:
            print(f"Error opening the display-language menu: {exc}")
        finally:
            menu = getattr(self, "ui_language_menu", None)
            if menu is not None:
                try:
                    menu.grab_release()
                except Exception:
                    pass

    def _use_system_language(self):
        """Forget the saved choice so Windows decides the language again."""
        written = clear_saved_language()
        self._sync_ui_language_controls()
        self._retranslate_ui()
        if not written:
            try:
                messagebox.showwarning(
                    t("Error"),
                    t("The display language changed, but the choice could not be saved."),
                )
            except Exception:
                pass

    def _ui_language_combo_values(self):
        """"System default" first, then each language in its own name."""
        return [t("System default")] + [
            LANGUAGE_NAMES.get(code, code) for code in available_languages()
        ]

    def _on_ui_language_combo(self, choice):
        """The hamburger combo shows names; everything else works in codes."""
        if choice == t("System default"):
            self._use_system_language()
            return
        for code in available_languages():
            if LANGUAGE_NAMES.get(code) == choice:
                self._apply_ui_language(code)
                return
        # An unrecognised choice means the combo and the real state disagree.
        # Put the combo back rather than guessing what was meant.
        self._sync_ui_language_controls()

    def _apply_ui_language(self, code):
        """Switch language, remember it, and repaint what is already on screen."""
        if code == get_language():
            self._sync_ui_language_controls()
            return
        saved = save_language(code)
        if not saved:
            # `save_language` applies nothing when the write fails, so without
            # this the click would do NOTHING while the dialog below claimed
            # the language had changed. Apply it for this session; only the
            # remembering failed.
            set_language(code)
        self._sync_ui_language_controls()
        self._retranslate_ui()
        if not saved:
            # Silence would be worse: the window changes language and then
            # quietly goes back on the next launch with nothing said.
            try:
                messagebox.showwarning(
                    t("Error"),
                    t("The display language changed, but the choice could not be saved."),
                )
            except Exception:
                pass

    def _sync_ui_language_controls(self):
        """One writer for the header button and the hamburger combo.

        Two widgets showing one piece of state is how they end up disagreeing --
        the same reason `_sync_mic_switches` below exists.
        """
        code = get_language()
        button = getattr(self, "ui_language_button", None)
        if button is not None:
            try:
                button.configure(text=UI_LANGUAGE_SHORT_LABELS.get(code, "EN"))
            except Exception:
                pass
        combo = getattr(self, "ui_language_combo_menu", None)
        if combo is not None:
            try:
                # The option list carries a translated entry, so it is rebuilt
                # here rather than fixed at construction -- otherwise "System
                # default" keeps the wording of whatever language it was built
                # in.
                combo.configure(values=self._ui_language_combo_values())
                if has_saved_language():
                    combo.set(LANGUAGE_NAMES.get(code, LANGUAGE_NAMES["en"]))
                else:
                    combo.set(t("System default"))
            except Exception:
                pass

    def _retranslate_placeholder(self, text_widget, source_text):
        """Re-render an empty pane's placeholder, and only while it is showing.

        Never touches a pane holding real content: the transcript and
        translation boxes carry the marks the pane bookkeeping depends on, and
        this must not go near them.
        """
        if text_widget is None:
            return
        try:
            showing = self._is_placeholder_active(text_widget)
            text_widget._placeholder_text = t(source_text)
            if showing:
                self._show_text_placeholder(text_widget)
        except Exception:
            pass

    def _retranslate_ui(self):
        """Repaint every piece of chrome in the language now in force.

        Deliberately does NOT re-render the transcript or translation content:
        that is the user's own text, and those two widgets carry the Tk marks
        the pane bookkeeping depends on. Only their empty-state placeholders
        are refreshed, and only while a placeholder is what is showing.

        Each step is guarded on its own. A label that fails to repaint is a
        cosmetic problem; an exception escaping into the Tk callback that
        triggered it is not.
        """
        try:
            self.title(t(APP_WINDOW_TITLE))
        except Exception:
            pass

        for attr, source_text in self._RETRANSLATABLE_LABELS:
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                widget.configure(text=t(source_text))
            except Exception:
                pass

        for attr, source_text in (
            ("initial_title_label", SECTION_TRANSCRIPT_TITLE),
            ("summary_title_label", SUMMARY_PANEL_TITLE),
        ):
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                widget.configure(text=t(source_text))
            except Exception:
                pass

        self._retranslate_placeholder(
            getattr(self, "initial_verse_box", None), PLACEHOLDER_TRANSCRIPT
        )
        # Only when no status message has claimed the placeholder; a status
        # there outranks the empty-state copy and is written elsewhere.
        if not (getattr(self, "_translation_status_message", "") or "").strip():
            self._retranslate_placeholder(
                getattr(self, "translated_verse_box", None), PLACEHOLDER_TRANSLATION
            )

        # Ask the owners of the dynamic text to repaint, rather than writing
        # their labels from here and giving each one a second author.
        for repaint in (
            lambda: self._retranslate_dynamic_labels(),
            lambda: self._update_translation_title(),
            lambda: self._sync_connection_indicator(),
            lambda: self._sync_transcript_visibility(),
            lambda: self._sync_mic_switches(),
        ):
            try:
                repaint()
            except Exception:
                pass

        # Japanese labels are wider than English ones -- measured: the action
        # group leaves the footer at 450 design px instead of 410 -- so the
        # responsive rules have to run again against the text now on screen.
        #
        # `_apply_responsive_layout` returns immediately when the width and
        # mode are unchanged, and a language switch changes neither. That cache
        # is why the header used to keep the old language until the window was
        # resized: `summary_button`'s wording is set ONLY by
        # `_pack_header_controls`, which never ran. Clearing the cache keys is
        # what makes a language change count as a change.
        self._last_layout_width = -1
        self._last_layout_mode_applied = None
        try:
            self._apply_responsive_layout()
        except Exception:
            pass

    def _sync_mic_switches(self):
        """Make both switches show `_microphone_capture_enabled`.

        One writer for both, the same shape as item 81's
        `_sync_transcript_visibility`: two widgets showing one piece of state is
        how they end up disagreeing.
        """
        enabled = bool(self._microphone_capture_enabled)
        # The label says what the state IS, not what the control does. "Mic"
        # alone left the operator reading the tick box to find out, and the
        # tick is 16 px.
        label = t("Mic on") if enabled else t("Mic off")
        for switch in (
            getattr(self, "mic_switch", None),
            getattr(self, "mic_switch_menu", None),
        ):
            if switch is None:
                continue
            try:
                if enabled:
                    switch.select()
                else:
                    switch.deselect()
                switch.configure(text=label)
            except Exception:
                pass

    def _set_mic_switch_enabled(self, enabled: bool):
        """Lock the switches while a session runs; the value is read at Start."""
        for switch in (
            getattr(self, "mic_switch", None),
            getattr(self, "mic_switch_menu", None),
        ):
            if switch is None:
                continue
            try:
                switch.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass

    def show_meeting_summary(self):
        """Tell the operator the summary is not ready yet.

        The panel, `summary_service.generate_summary_from_store` and the
        `SUMMARY_UPDATED` event are all still here and still wired to each
        other -- only this entry point changed. Item 76 (the summary as a modal
        overlay) is open and the ledger records it as "not needed for the
        client's request", so shipping the button in a half-finished state is
        worse than saying plainly that it is coming.

        Deliberately NOT deleting the panel code with it: that would be a much
        larger change than the one asked for, and item 76 is the row that
        decides its future.
        """
        try:
            messagebox.showinfo(
                t(MEETING_SUMMARY_BUTTON_TEXT),
                t("This feature is coming soon."),
            )
        except Exception as exc:
            print(f"Error showing meeting summary notice: {exc}")

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
            self.translated_title_label.configure(text=f"{t('Translation')} ({lang})")

    def copy_live_transcript_to_clipboard(self):
        """Copy canonical transcript text from TranscriptStore."""
        try:
            if not hasattr(self, "transcript_store") or self.transcript_store is None:
                messagebox.showinfo(t("Copy Transcript"), t("No transcript store available."))
                return
            clean_text = self._get_clean_transcript_for_copy_export()
            if not clean_text.strip():
                messagebox.showinfo(t("Copy Transcript"), t("No transcript text to copy."))
                return
            self.clipboard_clear()
            self.clipboard_append(clean_text)
            segment_count = self.transcript_store.segment_count()
            self.log_copy_export_stats(clean_text, segment_count)
            self._log_transcript_copy_formatting(clean_text, segment_count)
            self._log_copy_export_transcript_diag(clean_text, segment_count)
            self._log_session_transcript_copied(clean_text)
            messagebox.showinfo(t("Copy Transcript"), t("Live transcript copied to clipboard."))
        except Exception as exc:
            logger.error("Error copying transcript: %s", exc)
            print(f"Error copying transcript: {exc}")
            messagebox.showerror(t("Copy Transcript"), f"Could not copy transcript:\n{exc}")

    def _get_translated_transcript_for_copy_export(self) -> str:
        # fixes TASK_3A_FINDINGS.md Item 1: derive export lines from the
        # identity-keyed registry (insertion order) instead of the removed
        # flat positional list.
        registry = getattr(self, "_translation_items_by_utterance", None) or {}
        lines = [
            item.get("line_text")
            for item in registry.values()
            if item.get("state") == "completed" and item.get("line_text")
        ]
        if lines:
            return "\n".join(lines)
        box = self.translated_verse_box
        if box is None:
            return ""
        try:
            text = box.get("1.0", "end").strip()
        except Exception:
            return ""
        status = (getattr(self, "_translation_status_message", "") or "").strip()
        if status and text == status:
            return ""
        placeholder = (PLACEHOLDER_TRANSLATION or "").strip()
        if text in (placeholder, (t(PLACEHOLDER_TRANSLATION) or "").strip()):
            return ""
        return text

    def copy_translation_to_clipboard(self):
        """Copy translated transcript from the translation panel."""
        try:
            clean_text = self._get_translated_transcript_for_copy_export()
            if not clean_text.strip():
                messagebox.showinfo(t("Copy Translation"), t("No translated transcript to copy."))
                return
            self.clipboard_clear()
            self.clipboard_append(clean_text)
            messagebox.showinfo(t("Copy Translation"), t("Translated transcript copied to clipboard."))
        except Exception as exc:
            logger.error("Error copying translation: %s", exc)
            print(f"Error copying translation: {exc}")
            messagebox.showerror(t("Copy Translation"), f"Could not copy translation:\n{exc}")

    def export_transcript_placeholder(self):
        """Export original and translated transcripts as clearly separated sections."""
        from tkinter import filedialog

        try:
            original = self._get_clean_transcript_for_copy_export()
            translated = self._get_translated_transcript_for_copy_export()
            if not original.strip() and not translated.strip():
                messagebox.showinfo(t("Export"), t("No transcript text to export."))
                return
            file_path = filedialog.asksaveasfilename(
                title="Export Transcript",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            )
            if not file_path:
                return
            parts = [
                "=== Original transcript ===",
                original.strip() or "(none)",
                "",
                "=== Translated transcript ===",
                translated.strip() or "(none)",
                "",
            ]
            export_text = "\n".join(parts)
            with open(file_path, "w", encoding="utf-8") as export_file:
                export_file.write(export_text)
            if hasattr(self, "transcript_store") and self.transcript_store is not None:
                segment_count = self.transcript_store.segment_count()
                self.log_copy_export_stats(original, segment_count)
                self._log_transcript_copy_formatting(original, segment_count)
                self._log_copy_export_transcript_diag(original, segment_count)
            messagebox.showinfo(t("Export"), f"Transcript exported to:\n{file_path}")
        except Exception as exc:
            print(f"Error exporting transcript: {exc}")
            messagebox.showerror(t("Export"), f"Could not export transcript:\n{exc}")

    def on_language_change(self, changed="both"):
        """Handle language dropdown changes and log selections to console."""
        try:
            # Two languages, and a session never translates one into itself, so
            # picking either dropdown decides the other. Acting ONLY when the two
            # already match is what ends the recursion: the write below makes
            # them differ, so the trace it fires finds nothing left to do.
            # "both" is `swap_languages`, which has already set the pair.
            if changed in ("source", "target"):
                source = self._strip_language_flag(self.source_language.get())
                target = self._strip_language_flag(self.target_language.get())
                if source == target:
                    other = next(
                        (lang for lang in SOURCE_LANGUAGES if lang != source), ""
                    )
                    if other:
                        if changed == "source":
                            self.target_language.set(other)
                        else:
                            self.source_language.set(other)
                        self._sync_language_combo_displays()

            if changed in ("source", "both"):
                dropdown = self._strip_language_flag(self.source_language.get())
                if dropdown != self.source_language.get():
                    self.source_language.set(dropdown)
                profile = self._build_language_profile(dropdown)
                try:
                    self._listen_language = self._resolve_deepgram_language(dropdown)
                except Exception as exc:
                    print(f"LANGUAGE_DROPDOWN_CHANGED display_value={dropdown} resolved_code=ERROR ({exc})")
                    print(
                        f"Listening to dropdown: '{dropdown}' -> Deepgram code: 'ERROR'"
                    )
                    return
                print(
                    f"LANGUAGE_DROPDOWN_CHANGED display_value={dropdown} "
                    f"resolved_code={self._listen_language}"
                )
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
            self._translation_segment_seq = 0
            # fixes TASK_3A_FINDINGS.md Item 1/2: reset identity-keyed state.
            self._translation_items_by_utterance = {}
            self._pending_translations_by_utterance = {}
            self._translation_debounce_after_ids = {}
            self._recent_displayed_texts = []
            self.last_speech_time = 0.0
            self.fallback_speaker = 1
            self._reset_interim_tail_state()
            self._reset_meeting_segment_buffer_state()
            self._reset_segment_repair_state()
            try:
                worker = getattr(self, "translation_worker", None)
                if worker is not None:
                    worker.reset_session(run_id="")
            except Exception:
                pass
            if self.translated_verse_box is not None:
                if self._translation_status_message:
                    self.translated_verse_box._placeholder_text = self._translation_status_message
                elif not self.translation_enabled:
                    self.translated_verse_box._placeholder_text = (
                        "Translation unavailable (missing DEEPL_AUTH_KEY)."
                        if TRANSLATION_ENABLED and not has_deepl_api_key()
                        else "Translation disabled."
                    )
                else:
                    self.translated_verse_box._placeholder_text = ""
            for box in (self.initial_verse_box, self.translated_verse_box):
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.configure(state="disabled")
                self._show_text_placeholder(box)
            if hasattr(self, "transcript_store") and self.transcript_store is not None:
                self.transcript_store.clear()
            self._set_summary_panel_text(t(PLACEHOLDER_SUMMARY))
            print("Text boxes cleared.")
        except Exception as exc:
            print(f"Error clearing text: {exc}")
            messagebox.showerror(t("Error"), f"Could not clear text:\n{exc}")
