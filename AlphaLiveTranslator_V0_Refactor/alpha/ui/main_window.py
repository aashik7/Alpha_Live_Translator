"""
Alpha Live Translator — main window (V0 refactor).
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
from alpha.ui.theme import COLORS, SPEAKER_COLORS
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
        self.paned = None
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
        self.create_main_content()
        self._create_context_menu()
        self.bind_resize_event()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(100, self.toggle_header_layout)
        self.after(150, self._set_initial_pane_ratio)
        self.process_ui_queue()

    # -----------------------------------------------------------------------
    # Window setup
    # -----------------------------------------------------------------------
    def setup_window(self):
        """Configure the main application window."""
        self.title(f"Alpha V{APP_VERSION}")
        self.geometry("1000x700")
        self.minsize(300, 400)

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
        """Load logo.png from the application directory as a 30x30 CTkImage."""
        logo_path = ASSETS_DIR / "logo.png"
        try:
            pil_logo = Image.open(logo_path).resize((30, 30), Image.Resampling.LANCZOS)
            self.logo_image = ctk.CTkImage(
                light_image=pil_logo,
                dark_image=pil_logo,
                size=(30, 30),
            )
        except Exception as exc:
            print(f"Warning: Could not load logo.png ({exc}). Logo will be omitted.")
            self.logo_image = None

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    def create_header_frame(self):
        """Create the header bar with logo, branding, and normal-view controls."""
        self._load_logo()

        self.header_frame = ctk.CTkFrame(
            master=self,
            fg_color=COLORS["header_bg"],
            corner_radius=0,
            border_width=0,
        )
        self.header_frame.pack(fill="x", side="top")

        if self.logo_image is not None:
            self.logo_label = ctk.CTkLabel(
                master=self.header_frame,
                text="",
                image=self.logo_image,
                width=30,
                height=30,
            )
            self.logo_label.pack(side="left", padx=5, pady=10)

        self.brand_label = ctk.CTkLabel(
            master=self.header_frame,
            text="Alpha",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.brand_label.pack(side="left", padx=5, pady=10)

        self.listening_label = ctk.CTkLabel(
            master=self.header_frame,
            text="Listening to:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
        )

        self.source_combo = ctk.CTkComboBox(
            master=self.header_frame,
            values=SOURCE_LANGUAGES,
            variable=self.source_language,
            width=120,
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

        self.translate_label = ctk.CTkLabel(
            master=self.header_frame,
            text="Translate to:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
        )

        self.target_combo = ctk.CTkComboBox(
            master=self.header_frame,
            values=TARGET_LANGUAGES,
            variable=self.target_language,
            width=120,
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

        self.listen_button = ctk.CTkButton(
            master=self.header_frame,
            text="Start Listening",
            width=130,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color="#3a8eef",
            text_color=COLORS["text_primary"],
            corner_radius=8,
            command=self.toggle_listening,
        )

        self.summary_button = ctk.CTkButton(
            master=self.header_frame,
            text="Meeting Summary",
            width=140,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color="#3a8eef",
            text_color=COLORS["text_primary"],
            corner_radius=8,
            command=self.show_meeting_summary,
        )

        self.always_on_top_switch = ctk.CTkSwitch(
            master=self.header_frame,
            text="Always on Top",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["dropdown_bg"],
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["text_primary"],
            button_hover_color="#e0e0e0",
            command=self.toggle_always_on_top,
        )

        self.normal_header_widgets = [
            self.listening_label,
            self.source_combo,
            self.translate_label,
            self.target_combo,
            self.listen_button,
            self.summary_button,
            self.always_on_top_switch,
        ]

    # -----------------------------------------------------------------------
    # Hamburger menu (compact view)
    # -----------------------------------------------------------------------
    def create_hamburger_menu(self):
        """Create the hamburger button and compact dropdown menu panel."""
        self.hamburger_button = ctk.CTkButton(
            master=self.header_frame,
            text="≡",
            width=40,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            fg_color=COLORS["dropdown_bg"],
            hover_color="#4d4d5d",
            text_color=COLORS["text_primary"],
            corner_radius=8,
            command=self.toggle_hamburger_menu,
        )

        self.menu_dropdown_frame = ctk.CTkFrame(
            master=self,
            fg_color=COLORS["container_bg"],
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
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color="#3a8eef",
            text_color=COLORS["text_primary"],
            corner_radius=8,
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
                self.menu_dropdown_frame.pack(fill="x", side="top", after=self.header_frame)
                self._menu_visible = True
        except Exception as exc:
            print(f"Error toggling hamburger menu: {exc}")

    def _hide_hamburger_menu(self):
        """Hide the compact dropdown menu."""
        self.menu_dropdown_frame.pack_forget()
        self._menu_visible = False

    # -----------------------------------------------------------------------
    # Main content (PanedWindow)
    # -----------------------------------------------------------------------
    def create_main_content(self):
        """Create resizable Initial verse and Translated verse sections."""
        self.content_wrapper = ctk.CTkFrame(
            master=self,
            fg_color="transparent",
            corner_radius=0,
        )
        self.content_wrapper.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        self.paned = tk.PanedWindow(
            self.content_wrapper,
            orient=tk.VERTICAL,
            sashwidth=2,
            sashrelief=tk.FLAT,
            showhandle=False,
            opaqueresize=True,
            bg=COLORS["border"],
            bd=0,
            sashpad=0,
        )
        self.paned.pack(fill="both", expand=True)

        self.initial_verse_frame = self._create_verse_section(
            master=self.paned,
            title="Initial verse",
            font_size=12,
            body_color=COLORS["text_secondary"],
            attr_name="initial_verse_box",
            is_initial=True,
        )
        self.translated_verse_frame = self._create_verse_section(
            master=self.paned,
            title="Translated verse",
            font_size=16,
            body_color=COLORS["text_primary"],
            attr_name="translated_verse_box",
            is_initial=False,
        )

        self.paned.add(self.initial_verse_frame, minsize=80, stretch="always")
        self.paned.add(self.translated_verse_frame, minsize=120, stretch="always")

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
        """Show the correct toggle button for the current Initial verse visibility."""
        if text == "👁":
            self.show_initial_button.grid_remove()
            self.hide_initial_button.configure(text=text, width=width, font=ctk.CTkFont(size=16))
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
        text_frame = ctk.CTkFrame(master=master, fg_color=COLORS["header_bg"], corner_radius=0)

        scrollbar = ctk.CTkScrollbar(
            master=text_frame,
            orientation="vertical",
            button_color=COLORS["container_bg"],
            button_hover_color=COLORS["border"],
            fg_color=COLORS["header_bg"],
        )

        text_widget = tk.Text(
            master=text_frame,
            bg=COLORS["header_bg"],
            fg=body_color,
            font=("Segoe UI", font_size),
            insertbackground=COLORS["text_primary"],
            relief="flat",
            borderwidth=0,
            wrap="word",
            highlightthickness=0,
            padx=4,
            pady=4,
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
    ):
        """Build a labeled container with a styled tk.Text box for a PanedWindow pane."""
        outer = ctk.CTkFrame(
            master=master,
            fg_color=COLORS["header_bg"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"],
        )
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(master=outer, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 8))
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
            self.hide_initial_button = self._create_toggle_button(title_row, "👁", 32)
            self.hide_initial_button.configure(font=ctk.CTkFont(size=16))
            self.hide_initial_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        else:
            self.translated_title_row = title_row
            self.translated_title_label = title_label
            self.show_initial_button = self._create_toggle_button(
                title_row, "Initial verse", 100
            )
            self.show_initial_button.grid_remove()

        text_frame, text_widget = self._create_styled_text(outer, font_size, body_color)
        text_frame.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))

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
        """Hide or restore the Initial verse panel."""
        try:
            if self._initial_verse_visible:
                self.paned.forget(self.initial_verse_frame)
                self.translated_title_label.grid_remove()
                self._place_toggle_button(self.translated_title_row, "Initial verse", 100)
                self._initial_verse_visible = False
            else:
                self.paned.add(
                    self.initial_verse_frame,
                    minsize=80,
                    stretch="always",
                    before=self.translated_verse_frame,
                )
                self.translated_title_label.grid()
                self._place_toggle_button(self.initial_title_row, "👁", 32)
                self._initial_verse_visible = True
                self.after(50, self._set_initial_pane_ratio)
        except Exception as exc:
            print(f"Error toggling initial verse: {exc}")

    def _set_initial_pane_ratio(self):
        """Set the PanedWindow sash to a 35% / 65% starting split."""
        if not self._initial_verse_visible:
            return

        try:
            self.update_idletasks()
            height = self.paned.winfo_height()
            if height <= 2:
                self.after(50, self._set_initial_pane_ratio)
                return

            self.paned.sash_place(0, 0, int(height * 0.35))
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
        """Sync Start/Stop Listening button appearance (header + hamburger menu)."""
        if listening:
            cfg = {
                "text": "Stop Listening",
                "fg_color": "#ff4444",
                "hover_color": "#cc3333",
            }
        else:
            cfg = {
                "text": "Start Listening",
                "fg_color": COLORS["accent_blue"],
                "hover_color": "#3a8eef",
            }
        for btn in (self.listen_button, self.listen_button_menu):
            if btn is not None:
                btn.configure(**cfg)

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
