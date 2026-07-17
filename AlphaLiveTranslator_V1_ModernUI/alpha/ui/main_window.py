"""
Alpha Live Translator — main window (V1 Modern UI).
Nova-3 STT, dual audio capture (WASAPI + microphone), speaker diarization,
and optimized multilingual support (EN / JA / ZH / RU).

TESTING CHECKLIST (verify after each fix):
[ ] WebSocket connects without 400 error (fix 1)
[ ] Health monitor shows transcripts_received increasing (fixes 1-3)
[ ] Fast speech no longer gets split mid-sentence (fix 3)
[ ] Two speakers in same utterance show as separate lines (fix 4)
[ ] App recovers automatically after killing WiFi for 5s (fix 5)
[ ] No [Speaker X]: lines missing after a silence gap (fix 6)
[ ] Repeated phrases re-appear after 5+ minutes (fix 8)
[ ] Quiet remote speakers are no longer silenced (fix 7)
"""

import queue
import random
import re
import threading
import time

import customtkinter as ctk
import numpy as np
import tkinter as tk
from PIL import Image
from tkinter import Menu, messagebox

from alpha.audio.microphone import MicrophoneCaptureMixin
from alpha.audio.processing import pcm_to_mono_16k_np
from alpha.audio.wasapi import WasapiCaptureMixin
from alpha.config import (
    ASSETS_DIR,
    DEEPGRAM_SAMPLE_RATE,
    HEALTH_MONITOR_INTERVAL_MS,
    LANGUAGE_MAP,
    MAX_AUDIO_QUEUE_SIZE,
    MIC_NOISE_GATE_INITIAL_RMS,
    MIC_RMS_ROLLING_WINDOW_S,
    PROJECT_ROOT,
    WASAPI_FRAMES_PER_BUFFER,
)
from alpha.constants import (
    APP_CODENAME,
    APP_VERSION,
    COMPACT_BREAKPOINT,
    SOURCE_LANGUAGES,
    TARGET_LANGUAGES,
)
from alpha.transcription.deepgram_client import DeepgramClientMixin
from alpha.transcription.duplicate_protection import DuplicateProtectionMixin
from alpha.transcription.speaker_detection import SpeakerDetectionMixin
from alpha.ui.theme import (
    COLORS,
    FOOTER_BTN_HEIGHT,
    FOOTER_BTN_WIDTH,
    FONTS,
    RADII,
    SPACING,
    SPEAKER_COLORS,
)
from alpha.utils.queues import put_bounded


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

        self._compact_mode = None
        self._menu_visible = False
        self._pane_initialized = False
        self._initial_verse_visible = True
        self.logo_image = None

        self.source_language = ctk.StringVar(value="English")
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
        self.live_indicator = None
        self.status_text_label = None
        self.timer_label = None
        self.signal_label = None
        self.waveform_canvas = None
        self.summary_body_box = None
        self._listen_start_time = None
        self._timer_job = None
        self._waveform_job = None
        self.normal_header_widgets = []

        # Deepgram / audio state
        self.ui_queue = queue.Queue()
        self.transcript_queue = self.ui_queue
        self.is_listening = False
        self._listen_language = LANGUAGE_MAP.get(self.source_language.get(), "en")
        self._audio_q = None
        self.sys_audio_queue = None
        self.mic_audio_queue = None
        self._pyaudio = None
        self._wasapi_stream = None
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

        self.setup_window()
        self.create_solid_background()
        self.create_header_frame()
        self.create_hamburger_menu()
        self.create_status_bar()
        self.create_main_content()
        self.create_footer()
        self._create_context_menu()
        self.bind_resize_event()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(100, self.toggle_header_layout)
        self.after(150, self._set_initial_pane_ratio)
        self._update_translation_title()
        self.process_ui_queue()

    # -----------------------------------------------------------------------
    # Window setup
    # -----------------------------------------------------------------------
    def setup_window(self):
        """Configure the main application window."""
        self.title(f"Alpha AI — Live Translator V{APP_VERSION}")
        self.geometry("1180x760")
        self.minsize(1000, 650)

    def create_solid_background(self):
        """Apply a solid background color to the main window."""
        self.configure(fg_color=COLORS["main_bg"])

    def bind_resize_event(self):
        """Bind window resize events to responsive header logic."""
        self.bind("<Configure>", self.on_window_resize)

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

    def _make_combo(self, master, values, variable, width=150):
        """Styled language dropdown."""
        return ctk.CTkComboBox(
            master=master,
            values=values,
            variable=variable,
            width=width,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color=COLORS["card_bg_soft"],
            border_color=COLORS["border"],
            border_width=1,
            button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["accent_blue_hover"],
            dropdown_fg_color=COLORS["card_bg"],
            dropdown_hover_color=COLORS["border_soft"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["combo"],
            state="readonly",
        )

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    def create_header_frame(self):
        """Modern top header with branding and language controls."""
        self._load_logo()

        self.header_frame = ctk.CTkFrame(
            master=self,
            fg_color=COLORS["panel_bg"],
            corner_radius=0,
            border_width=0,
        )
        self.header_frame.pack(fill="x", side="top", padx=0, pady=0)

        brand_block = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        brand_block.pack(side="left", padx=(SPACING["window_pad_x"], 12), pady=14)

        if self.logo_image is not None:
            self.logo_label = ctk.CTkLabel(
                master=brand_block,
                text="",
                image=self.logo_image,
                width=36,
                height=36,
            )
            self.logo_label.pack(side="left", padx=(0, 10))

        titles = ctk.CTkFrame(brand_block, fg_color="transparent")
        titles.pack(side="left")
        self.brand_label = ctk.CTkLabel(
            master=titles,
            text="Alpha AI",
            font=ctk.CTkFont(family=FONTS["brand"][0], size=FONTS["brand"][1], weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.brand_label.pack(anchor="w")
        self.brand_sub_label = ctk.CTkLabel(
            master=titles,
            text="Live Translator",
            font=ctk.CTkFont(family=FONTS["brand_sub"][0], size=FONTS["brand_sub"][1]),
            text_color=COLORS["text_secondary"],
        )
        self.brand_sub_label.pack(anchor="w")

        controls = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        controls.pack(side="right", padx=(0, SPACING["window_pad_x"]), pady=12)

        self.listening_label = ctk.CTkLabel(
            master=controls,
            text="From",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLORS["text_muted"],
        )
        self.source_combo = self._make_combo(controls, SOURCE_LANGUAGES, self.source_language, 148)

        self.swap_button = ctk.CTkButton(
            master=controls,
            text="⇄",
            width=40,
            height=36,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=COLORS["card_bg_soft"],
            hover_color=COLORS["border_soft"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.swap_languages,
        )

        self.translate_label = ctk.CTkLabel(
            master=controls,
            text="To",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLORS["text_muted"],
        )
        self.target_combo = self._make_combo(controls, TARGET_LANGUAGES, self.target_language, 148)

        self.summary_button = ctk.CTkButton(
            master=controls,
            text="Meeting Summary",
            width=150,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.show_meeting_summary,
        )

        self.always_on_top_switch = ctk.CTkSwitch(
            master=controls,
            text="Always on Top",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["card_bg_soft"],
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["text_primary"],
            button_hover_color="#e2e8f0",
            command=self.toggle_always_on_top,
        )

        self.normal_header_widgets = [
            self.listening_label,
            self.source_combo,
            self.swap_button,
            self.translate_label,
            self.target_combo,
            self.summary_button,
            self.always_on_top_switch,
        ]
        for widget in self.normal_header_widgets:
            widget.pack(side="left", padx=6)

        self.hamburger_button = ctk.CTkButton(
            master=self.header_frame,
            text="≡",
            width=40,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            fg_color=COLORS["card_bg_soft"],
            hover_color=COLORS["border_soft"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.toggle_hamburger_menu,
        )
        self.hamburger_button.pack_forget()

    # -----------------------------------------------------------------------
    # Hamburger menu (compact view)
    # -----------------------------------------------------------------------
    def create_hamburger_menu(self):
        """Create compact dropdown menu panel (hamburger button created in header)."""
        self.menu_dropdown_frame = ctk.CTkFrame(
            master=self,
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

        self.summary_button_menu = ctk.CTkButton(
            master=self.menu_dropdown_frame,
            text="Meeting Summary",
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color="#3a8eef",
            text_color=COLORS["text_primary"],
            corner_radius=8,
            command=self.show_meeting_summary,
        )
        self.summary_button_menu.pack(fill="x", padx=15, pady=(4, 8))

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
        """React to window resize events and switch header layouts."""
        if event.widget is not self:
            return
        self.toggle_header_layout()

    def toggle_header_layout(self):
        """Switch between normal and compact header layouts based on window width."""
        try:
            width = self.winfo_width()
            if width <= 1:
                return

            is_compact = width <= COMPACT_BREAKPOINT
            if is_compact == self._compact_mode:
                return

            self._compact_mode = is_compact
            if is_compact:
                self.show_compact_layout()
            else:
                self.show_normal_layout()
        except Exception as exc:
            print(f"Error toggling header layout: {exc}")

    def show_normal_layout(self):
        """Show all header controls in a horizontal row; hide the hamburger menu."""
        try:
            self.hamburger_button.pack_forget()
            self._hide_hamburger_menu()

            for widget in self.normal_header_widgets:
                widget.pack_forget()

            for widget in self.normal_header_widgets:
                widget.pack(side="left", padx=5, pady=10)
        except Exception as exc:
            print(f"Error showing normal layout: {exc}")

    def show_compact_layout(self):
        """Show only logo, title, and hamburger button in the header."""
        try:
            for widget in self.normal_header_widgets:
                widget.pack_forget()

            self.hamburger_button.pack(side="right", padx=5, pady=10)
            self._hide_hamburger_menu()
        except Exception as exc:
            print(f"Error showing compact layout: {exc}")

    def toggle_hamburger_menu(self):
        """Toggle the compact dropdown menu below the header."""
        try:
            if self._menu_visible:
                self._hide_hamburger_menu()
            else:
                self.menu_dropdown_frame.pack(fill="x", side="top", after=self.status_bar_frame)
                self._menu_visible = True
        except Exception as exc:
            print(f"Error toggling hamburger menu: {exc}")

    def _hide_hamburger_menu(self):
        """Hide the compact dropdown menu."""
        self.menu_dropdown_frame.pack_forget()
        self._menu_visible = False

    # -----------------------------------------------------------------------
    # Status bar
    # -----------------------------------------------------------------------
    def create_status_bar(self):
        """Listening status strip with LIVE indicator, waveform, and timer."""
        self.status_bar_frame = ctk.CTkFrame(
            master=self,
            fg_color=COLORS["card_bg"],
            corner_radius=RADII["card"],
            border_width=1,
            border_color=COLORS["border"],
            height=52,
        )
        self.status_bar_frame.pack(
            fill="x",
            padx=SPACING["window_pad_x"],
            pady=(12, 0),
        )
        self.status_bar_frame.pack_propagate(False)

        inner = ctk.CTkFrame(self.status_bar_frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=8)

        live_wrap = ctk.CTkFrame(inner, fg_color="transparent")
        live_wrap.pack(side="left")

        self.live_indicator = ctk.CTkLabel(
            master=live_wrap,
            text="● LIVE",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=COLORS["text_muted"],
        )
        self.live_indicator.pack(side="left", padx=(0, 10))

        self.status_text_label = ctk.CTkLabel(
            master=live_wrap,
            text="🎤 Ready to listen",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLORS["text_secondary"],
        )
        self.status_text_label.pack(side="left")

        self.waveform_canvas = tk.Canvas(
            inner,
            width=120,
            height=28,
            bg=COLORS["card_bg"],
            highlightthickness=0,
            bd=0,
        )
        self.waveform_canvas.pack(side="left", padx=20)
        self._draw_waveform(idle=True)

        right_status = ctk.CTkFrame(inner, fg_color="transparent")
        right_status.pack(side="right")

        self.timer_label = ctk.CTkLabel(
            master=right_status,
            text="00:00",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        self.timer_label.pack(side="right", padx=(12, 0))

        self.signal_label = ctk.CTkLabel(
            master=right_status,
            text="● Signal OK",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLORS["accent_green"],
        )
        self.signal_label.pack(side="right")

    def _draw_waveform(self, idle=False):
        """Draw simple waveform bars on the status canvas."""
        if self.waveform_canvas is None:
            return
        self.waveform_canvas.delete("all")
        bar_w = 6
        gap = 4
        x = 4
        for i in range(12):
            h = 4 if idle else random.randint(6, 24)
            color = COLORS["waveform_bar_dim"] if idle else COLORS["waveform_bar"]
            y0 = 28 - h
            self.waveform_canvas.create_rectangle(
                x, y0, x + bar_w, 26, fill=color, outline=""
            )
            x += bar_w + gap

    def _animate_waveform(self):
        """Animate waveform while listening."""
        if not self.is_listening:
            return
        self._draw_waveform(idle=False)
        self._waveform_job = self.after(180, self._animate_waveform)

    def _update_timer(self):
        """Update session timer while listening."""
        if not self.is_listening or self._listen_start_time is None:
            return
        elapsed = int(time.time() - self._listen_start_time)
        mins, secs = divmod(elapsed, 60)
        if self.timer_label is not None:
            self.timer_label.configure(text=f"{mins:02d}:{secs:02d}")
        self._timer_job = self.after(1000, self._update_timer)

    def _update_status_bar(self, listening=False):
        """Refresh status bar visuals for idle vs listening."""
        if self.live_indicator is None:
            return
        if listening:
            self.live_indicator.configure(text_color=COLORS["live_glow"])
            self.status_text_label.configure(text="🎤 Listening — capturing audio")
            self.signal_label.configure(text="● Signal OK", text_color=COLORS["accent_green"])
            self._listen_start_time = time.time()
            self._animate_waveform()
            self._update_timer()
        else:
            self.live_indicator.configure(text_color=COLORS["text_muted"])
            self.status_text_label.configure(text="🎤 Ready to listen")
            self.signal_label.configure(text="● Standby", text_color=COLORS["text_muted"])
            if self.timer_label is not None:
                self.timer_label.configure(text="00:00")
            self._listen_start_time = None
            if self._waveform_job is not None:
                self.after_cancel(self._waveform_job)
                self._waveform_job = None
            if self._timer_job is not None:
                self.after_cancel(self._timer_job)
                self._timer_job = None
            self._draw_waveform(idle=True)

    # -----------------------------------------------------------------------
    # Main content (two-column layout)
    # -----------------------------------------------------------------------
    def create_main_content(self):
        """Modern two-column layout: transcript + translation | AI summary."""
        self.content_wrapper = ctk.CTkFrame(
            master=self,
            fg_color="transparent",
            corner_radius=0,
        )
        self.content_wrapper.pack(
            fill="both",
            expand=True,
            padx=SPACING["window_pad_x"],
            pady=(SPACING["section_gap"], SPACING["section_gap"]),
        )
        self.content_wrapper.grid_columnconfigure(0, weight=7)
        self.content_wrapper.grid_columnconfigure(1, weight=3)
        self.content_wrapper.grid_rowconfigure(0, weight=1)

        self.left_column = ctk.CTkFrame(self.content_wrapper, fg_color="transparent")
        self.left_column.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.left_column.grid_rowconfigure(0, weight=2)
        self.left_column.grid_rowconfigure(1, weight=3)
        self.left_column.grid_columnconfigure(0, weight=1)

        self.right_column = ctk.CTkFrame(self.content_wrapper, fg_color="transparent")
        self.right_column.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.right_column.grid_rowconfigure(0, weight=1)
        self.right_column.grid_columnconfigure(0, weight=1)

        self.paned = self.left_column

        self.initial_verse_frame = self._create_verse_section(
            master=self.left_column,
            title="Live Transcript",
            font_size=12,
            body_color=COLORS["text_secondary"],
            attr_name="initial_verse_box",
            is_initial=True,
            grid_row=0,
        )
        self.translated_verse_frame = self._create_verse_section(
            master=self.left_column,
            title="Translation",
            font_size=15,
            body_color=COLORS["text_primary"],
            attr_name="translated_verse_box",
            is_initial=False,
            grid_row=1,
        )

        self._create_summary_card()

    def _create_summary_card(self):
        """Right column AI Meeting Summary card."""
        outer = ctk.CTkFrame(
            master=self.right_column,
            fg_color=COLORS["card_bg"],
            corner_radius=RADII["card"],
            border_width=1,
            border_color=COLORS["border"],
        )
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_rowconfigure(2, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=SPACING["card_pad"], pady=(14, 6))
        ctk.CTkLabel(
            master=header,
            text="AI Meeting Summary",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")

        ctk.CTkLabel(
            master=outer,
            text="Key points",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        ).grid(row=1, column=0, sticky="w", padx=SPACING["card_pad"], pady=(4, 4))

        body_frame = ctk.CTkFrame(outer, fg_color=COLORS["card_bg_soft"], corner_radius=10)
        body_frame.grid(row=2, column=0, sticky="nsew", padx=SPACING["card_pad"], pady=(0, 8))

        self.summary_body_box = tk.Text(
            master=body_frame,
            bg=COLORS["card_bg_soft"],
            fg=COLORS["text_secondary"],
            font=("Segoe UI", 12),
            relief="flat",
            borderwidth=0,
            wrap="word",
            highlightthickness=0,
            padx=10,
            pady=10,
            state="disabled",
        )
        self.summary_body_box.pack(fill="both", expand=True)
        self.summary_body_box.configure(state="normal")
        self.summary_body_box.insert(
            "1.0",
            "Meeting summaries will appear here after a session.\n\n"
            "• Speaker highlights\n"
            "• Key decisions\n"
            "• Follow-up topics\n\n"
            "(Stage 2 feature — placeholder)",
        )
        self.summary_body_box.configure(state="disabled")

        ctk.CTkButton(
            master=outer,
            text="Show more",
            height=34,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color=COLORS["card_bg_soft"],
            hover_color=COLORS["border_soft"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.show_meeting_summary,
        ).grid(row=3, column=0, sticky="ew", padx=SPACING["card_pad"], pady=(0, 14))

    def create_footer(self):
        """Bottom toolbar with primary session controls."""
        self.footer_frame = ctk.CTkFrame(
            master=self,
            fg_color=COLORS["panel_bg"],
            corner_radius=0,
            border_width=1,
            border_color=COLORS["border"],
        )
        self.footer_frame.pack(fill="x", side="bottom")

        btn_row = ctk.CTkFrame(self.footer_frame, fg_color="transparent")
        btn_row.pack(side="left", padx=SPACING["window_pad_x"], pady=12)

        gap = SPACING["footer_btn_gap"]

        self.listen_button = ctk.CTkButton(
            master=btn_row,
            text="Start Listening",
            width=FOOTER_BTN_WIDTH,
            height=FOOTER_BTN_HEIGHT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.toggle_listening,
        )
        self.listen_button.pack(side="left", padx=(0, gap))

        self.footer_stop_button = ctk.CTkButton(
            master=btn_row,
            text="Stop",
            width=100,
            height=FOOTER_BTN_HEIGHT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["card_bg_soft"],
            hover_color=COLORS["border_soft"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self._stop_listening,
        )
        self.footer_stop_button.pack(side="left", padx=(0, gap))

        ctk.CTkButton(
            master=btn_row,
            text="Copy Translation",
            width=FOOTER_BTN_WIDTH,
            height=FOOTER_BTN_HEIGHT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color=COLORS["card_bg_soft"],
            hover_color=COLORS["border_soft"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.copy_translation_to_clipboard,
        ).pack(side="left", padx=(0, gap))

        ctk.CTkButton(
            master=btn_row,
            text="Export",
            width=100,
            height=FOOTER_BTN_HEIGHT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color=COLORS["card_bg_soft"],
            hover_color=COLORS["border_soft"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.export_transcript_placeholder,
        ).pack(side="left", padx=(0, gap))

        ctk.CTkButton(
            master=btn_row,
            text="Clear",
            width=100,
            height=FOOTER_BTN_HEIGHT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color=COLORS["card_bg_soft"],
            hover_color=COLORS["border_soft"],
            text_color=COLORS["text_primary"],
            corner_radius=RADII["button"],
            command=self.clear_text,
        ).pack(side="left")

    def _create_toggle_button(self, master, text, width):
        """Create a compact hide/show toggle button for the Initial verse panel."""
        return ctk.CTkButton(
            master=master,
            text=text,
            width=width,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color=COLORS["container_bg"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            border_width=0,
            corner_radius=6,
            height=28,
            command=self.toggle_initial_verse,
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
        """Show the left scrollbar only when text content overflows the visible area."""
        try:
            text_widget.update_idletasks()
            if text_widget.yview()[0] == 0.0 and text_widget.yview()[1] == 1.0:
                scrollbar.pack_forget()
            else:
                scrollbar.pack(side="left", fill="y", before=text_widget)
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

    def _create_styled_text(self, master, font_size, body_color):
        """Create a read-only tk.Text widget with a left-side auto-hiding CTkScrollbar."""
        text_frame = ctk.CTkFrame(master=master, fg_color=COLORS["card_bg_soft"], corner_radius=10)

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
            font=("Segoe UI", font_size),
            insertbackground=COLORS["text_primary"],
            relief="flat",
            borderwidth=0,
            wrap="word",
            highlightthickness=0,
            padx=8,
            pady=8,
            state="disabled",
        )
        text_widget.pack(side="left", fill="both", expand=True)

        scrollbar.configure(command=text_widget.yview)
        self._bind_scroll_autohide(text_widget, scrollbar)

        text_widget.tag_configure("body", foreground=body_color)
        text_widget.tag_configure("interim", foreground="#888899")
        for tag_name, color in SPEAKER_COLORS.items():
            text_widget.tag_configure(tag_name, foreground=color)

        text_widget._scrollbar = scrollbar

        return text_frame, text_widget

    def _create_verse_section(
        self,
        master,
        title,
        font_size,
        body_color,
        attr_name,
        is_initial,
        grid_row=0,
    ):
        """Build a labeled card with a styled tk.Text box."""
        outer = ctk.CTkFrame(
            master=master,
            fg_color=COLORS["card_bg"],
            corner_radius=RADII["card"],
            border_width=1,
            border_color=COLORS["border"],
        )
        outer.grid(row=grid_row, column=0, sticky="nsew", pady=(0, SPACING["section_gap"]))
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(master=outer, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=SPACING["card_pad"], pady=(14, 8))
        title_row.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            master=title_row,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        title_label.grid(row=0, column=0, sticky="w")

        if is_initial:
            self.initial_title_row = title_row
            self.hide_initial_button = self._create_toggle_button(title_row, "Hide", 56)
            self.hide_initial_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        else:
            self.translated_title_row = title_row
            self.translated_title_label = title_label
            self.show_initial_button = self._create_toggle_button(title_row, "Show Transcript", 120)
            self.show_initial_button.grid_remove()

        text_frame, text_widget = self._create_styled_text(outer, font_size, body_color)
        text_frame.grid(row=1, column=0, sticky="nsew", padx=SPACING["card_pad"], pady=(0, 14))

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
                self._place_toggle_button(self.translated_title_row, "Show Transcript", 120)
                self._initial_verse_visible = False
            else:
                self.initial_verse_frame.grid()
                self.left_column.grid_rowconfigure(0, weight=2)
                self.left_column.grid_rowconfigure(1, weight=3)
                self.translated_title_label.grid()
                self._place_toggle_button(self.initial_title_row, "Hide", 56)
                self._initial_verse_visible = True
        except Exception as exc:
            print(f"Error toggling initial verse: {exc}")

    def _set_initial_pane_ratio(self):
        """Set initial row weights for transcript vs translation."""
        if not self._initial_verse_visible or self.left_column is None:
            return
        try:
            self.left_column.grid_rowconfigure(0, weight=2)
            self.left_column.grid_rowconfigure(1, weight=3)
            if not self._pane_initialized:
                self._pane_initialized = True
        except Exception as exc:
            print(f"Error setting initial pane sizes: {exc}")

    # -----------------------------------------------------------------------
    # Deepgram real-time speech-to-text
    # -----------------------------------------------------------------------
    def audio_mixer_worker(self):
        """
        FIXED: Use blocking synchronized reads instead of non-blocking independent reads.
        This ensures continuous audio flow to Deepgram.
        """
        print("[Mixer] Started with SYNCHRONIZED mode")
        mix_start = time.time()  # CHANGED: adaptive gate warm-up anchor (fix 7)
        mic_rms_history = []  # CHANGED: rolling mic RMS samples (fix 7)

        while not self._stop_event.is_set():
            try:
                # Get system audio (blocking wait)
                sys_chunk = self.sys_audio_queue.get(timeout=0.1)

                # Try to get mic audio, but don't block too long
                try:
                    mic_chunk = self.mic_audio_queue.get(timeout=0.05)
                except queue.Empty:
                    mic_chunk = None

                sys_np_16k = pcm_to_mono_16k_np(  # CHANGED: unified linear resampling path (fix 2)
                    sys_chunk, self._wasapi_channels, self._wasapi_rate  # CHANGED: (fix 2)
                )  # CHANGED: (fix 2)

                if sys_np_16k.size == 0:
                    continue

                # Only mix mic if we have it AND it passes adaptive noise gate
                if mic_chunk is not None:
                    mic_np = np.frombuffer(mic_chunk, dtype=np.int16)
                    if mic_np.size > 0:
                        rms = float(np.sqrt(np.mean(mic_np.astype(np.float32) ** 2)))  # CHANGED: mic RMS (fix 7)
                        now = time.time()  # CHANGED: timestamp for rolling window (fix 7)
                        mic_rms_history.append((now, rms))  # CHANGED: track mic RMS (fix 7)
                        mic_rms_history[:] = [  # CHANGED: keep last 2 seconds only (fix 7)
                            (t, v) for t, v in mic_rms_history if now - t <= MIC_RMS_ROLLING_WINDOW_S  # CHANGED: (fix 7)
                        ]  # CHANGED: (fix 7)
                        if now - mix_start < MIC_RMS_ROLLING_WINDOW_S:  # CHANGED: warm-up period (fix 7)
                            rolling_avg = MIC_NOISE_GATE_INITIAL_RMS  # CHANGED: seed average at 200 (fix 7)
                        elif mic_rms_history:  # CHANGED: (fix 7)
                            rolling_avg = sum(v for _, v in mic_rms_history) / len(mic_rms_history)  # CHANGED: (fix 7)
                        else:  # CHANGED: (fix 7)
                            rolling_avg = MIC_NOISE_GATE_INITIAL_RMS  # CHANGED: (fix 7)
                        gate_threshold = rolling_avg * 0.2  # CHANGED: gate below 20% of rolling avg (fix 7)

                        if rms > gate_threshold:  # CHANGED: adaptive gate instead of fixed 100 (fix 7)
                            min_len = min(len(sys_np_16k), len(mic_np))
                            if min_len > 0:
                                sys_aligned = sys_np_16k[:min_len]
                                mic_aligned = mic_np[:min_len]

                                sys_rms = float(  # CHANGED: dynamic mix weight input (fix 9)
                                    np.sqrt(np.mean(sys_aligned.astype(np.float32) ** 2))  # CHANGED: (fix 9)
                                )  # CHANGED: (fix 9)
                                total = sys_rms + rms + 1e-6  # CHANGED: avoid div/0 (fix 9)
                                sys_w = max(0.4, min(0.8, sys_rms / total))  # CHANGED: dynamic sys weight (fix 9)
                                mic_w = 1.0 - sys_w  # CHANGED: dynamic mic weight (fix 9)
                                mixed = (  # CHANGED: replace hardcoded 0.7/0.3 (fix 9)
                                    sys_aligned.astype(np.float32) * sys_w  # CHANGED: (fix 9)
                                    + mic_aligned.astype(np.float32) * mic_w  # CHANGED: (fix 9)
                                )  # CHANGED: (fix 9)
                                mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
                                final_chunk = mixed.tobytes()
                                print(
                                    f"[MIX] Mixed chunk: sys_rms={sys_rms:.1f}, "
                                    f"mic_rms={rms:.1f}, sys_w={sys_w:.2f} -> {len(final_chunk)} bytes"
                                )
                            else:
                                final_chunk = sys_np_16k.tobytes()
                        else:
                            final_chunk = sys_np_16k.tobytes()
                    else:
                        final_chunk = sys_np_16k.tobytes()
                else:
                    final_chunk = sys_np_16k.tobytes()

                # CRITICAL FIX #2: avoid hard dropping on queue spikes
                try:
                    self._audio_q.put(final_chunk, block=True, timeout=0.5)
                except queue.Full:
                    print("[WARN] Audio queue full, dropping oldest chunk")
                    try:
                        self._audio_q.get_nowait()
                        self._audio_q.put(final_chunk, block=True, timeout=0.1)
                    except Exception:
                        pass

            except queue.Empty:
                pass
            except Exception as e:
                print(f"[MIXER ERROR] {e}")
                import traceback
                traceback.print_exc()

    def _set_listen_button_state(self, listening):
        """Sync Start/Stop Listening button appearance (footer + hamburger menu)."""
        if listening:
            cfg = {
                "text": "Stop Listening",
                "fg_color": COLORS["accent_red"],
                "hover_color": "#DC2626",
            }
        else:
            cfg = {
                "text": "Start Listening",
                "fg_color": COLORS["accent_blue"],
                "hover_color": COLORS["accent_blue_hover"],
            }
        for btn in (self.listen_button, self.listen_button_menu):
            if btn is not None:
                btn.configure(**cfg)
        self._update_status_bar(listening=listening)

    def toggle_listening(self):
        """Start or stop live transcription."""
        if self.is_listening:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self):
        """Open dual audio capture (WASAPI + gated mic) and Deepgram WebSocket."""
        dropdown_lang = self.source_language.get()
        deepgram_lang = LANGUAGE_MAP.get(dropdown_lang, "en")
        self._listen_language = deepgram_lang
        self._print_accuracy_startup(deepgram_lang)
        print(
            f"Listening to dropdown: '{dropdown_lang}' -> Deepgram code: '{deepgram_lang}'"
        )

        try:
            self._stop_event.clear()
            self._audio_q = queue.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
            self.sys_audio_queue = queue.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
            self.mic_audio_queue = queue.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
            with self._speaker_lock:
                self._last_assigned_speaker = 1
                self._last_speaker_change_time = 0.0
            self.last_transcript_hash = set()
            self._transcript_hash_order = []  # CHANGED: reset dedup order (fix 8)
            self._last_speaker_utterance = {}  # CHANGED: reset fuzzy dedup (fix 8)
            self.last_speaker = None
            self.last_speaker_id = None
            self.current_speaker = None
            self.last_displayed_speaker = None
            self.last_speech_time = 0.0
            self.fallback_speaker = 1
            self._chunks_sent_count = 0
            self._transcripts_received = 0
            self._dg_backoff_seconds = 1.0  # CHANGED: reset reconnect backoff (fix 5)
            self._dg_replay_buffer = []  # CHANGED: clear replay buffer (fix 5)
            self._dg_awaiting_transcript_reset = False  # CHANGED: (fix 5)

            print("Starting dual audio capture (WASAPI loopback + microphone)...")
            try:
                self._start_wasapi_loopback()
                self._start_microphone_capture()
                self._mix_thread = threading.Thread(
                    target=self.audio_mixer_worker, daemon=True
                )
                self._mix_thread.start()
                print("Audio mix worker thread started")
            except Exception as exc:
                print(f"Audio stream error: {exc}")
                raise

            try:
                self._dg_thread = threading.Thread(
                    target=self._deepgram_worker, daemon=True
                )
                self._dg_thread.start()
                print("Deepgram worker thread started")
            except Exception as exc:
                print(f"Deepgram thread error: {exc}")
                raise

            self.is_listening = True
            self._set_listen_button_state(True)
            self._start_health_monitor()
            print(f"Listening started (language={self._listen_language})")
        except Exception as exc:
            print(f"Error starting listening: {exc}")
            self._stop_listening()

    def _stop_listening(self):
        """Close audio stream and Deepgram WebSocket."""
        try:
            self._stop_event.set()
            self.is_listening = False  # CHANGED: prevent reconnect during intentional stop (fix 5)

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
            self._set_listen_button_state(False)
            print("Listening stopped")
        except Exception as exc:
            print(f"Error stopping listening: {exc}")

    def _append_initial_transcript(self, text):
        """Insert a speaker-tagged line into the Initial verse box (UI thread only)."""
        box = self.initial_verse_box
        if box is None:
            print("Warning: initial_verse_box is None, cannot insert text")
            return

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
        if self.is_listening:
            self._stop_listening()
        self.destroy()

    # -----------------------------------------------------------------------
    # Speaker-colored text helpers
    # -----------------------------------------------------------------------
    def _insert_formatted_text(self, text_widget, content):
        """Insert text with colored [Speaker N] tags into a read-only tk.Text widget."""
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
        """Placeholder for Stage 2 meeting summary feature."""
        try:
            print("Meeting Summary feature will be added in Stage 2")
            messagebox.showinfo(
                "Meeting Summary",
                "Summary feature coming in Stage 2",
            )
        except Exception as exc:
            print(f"Error showing meeting summary dialog: {exc}")

    def swap_languages(self):
        """Swap source and target language selections."""
        src = self.source_language.get()
        tgt = self.target_language.get()
        self.source_language.set(tgt)
        self.target_language.set(src)
        self.on_language_change("both")

    def _update_translation_title(self):
        """Update translation card title from target language dropdown."""
        if self.translated_title_label is not None:
            lang = self.target_language.get()
            self.translated_title_label.configure(text=f"Translation ({lang})")

    def copy_translation_to_clipboard(self):
        """Copy translated verse text to the system clipboard."""
        box = self.translated_verse_box
        if box is None:
            return
        try:
            text = box.get("1.0", "end-1c").strip()
            if not text:
                messagebox.showinfo("Copy Translation", "No translation text to copy.")
                return
            self.clipboard_clear()
            self.clipboard_append(text)
            print("Translation copied to clipboard.")
            messagebox.showinfo("Copy Translation", "Translation copied to clipboard.")
        except Exception as exc:
            print(f"Error copying translation: {exc}")
            messagebox.showerror("Copy Translation", f"Could not copy text:\n{exc}")

    def export_transcript_placeholder(self):
        """Placeholder for future export functionality."""
        messagebox.showinfo(
            "Export",
            "Export feature will be added in a later version.",
        )

    def on_language_change(self, changed="both"):
        """Handle language dropdown changes and log selections to console."""
        try:
            if changed in ("source", "both"):
                dropdown = self.source_language.get()
                self._listen_language = LANGUAGE_MAP.get(dropdown, "en")
                print(
                    f"Listening to dropdown: '{dropdown}' -> "
                    f"Deepgram code: '{self._listen_language}'"
                )
            if changed in ("target", "both"):
                print(f"Translate to: {self.target_language.get()}")
                self._update_translation_title()
        except Exception as exc:
            print(f"Error updating language selection: {exc}")

    def clear_text(self):
        """Clear both the initial and translated verse text boxes."""
        try:
            self.last_transcript_hash = set()
            self._transcript_hash_order = []  # CHANGED: reset dedup order (fix 8)
            self._last_speaker_utterance = {}  # CHANGED: reset fuzzy dedup (fix 8)
            self.last_speaker = None
            self.last_speaker_id = None
            self.current_speaker = None
            self.last_displayed_speaker = None
            self.last_speech_time = 0.0
            self.fallback_speaker = 1
            for box in (self.initial_verse_box, self.translated_verse_box):
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.configure(state="disabled")
                self.check_scrollbar_visibility(box, box._scrollbar)
            print("Text boxes cleared.")
        except Exception as exc:
            print(f"Error clearing text: {exc}")
            messagebox.showerror("Error", f"Could not clear text:\n{exc}")
