"""
Alpha V4 - Live Meeting Translation Desktop Application
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

APP_VERSION = "4.0.0"
APP_CODENAME = "Nova-3 Multilingual"

import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote  # CHANGED: URL-encode keyterm params (fix 11)

import customtkinter as ctk
import numpy as np
import pyaudiowpatch as pyaudio
import sounddevice as sd
import tkinter as tk
import websocket
from PIL import Image
from tkinter import Menu, messagebox

from main import DEEPGRAM_API_KEY as _MAIN_DEEPGRAM_API_KEY  # CHANGED: load key from main.py

# ---------------------------------------------------------------------------
# Deepgram configuration
# ---------------------------------------------------------------------------
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY") or _MAIN_DEEPGRAM_API_KEY  # CHANGED: env overrides main.py
if not DEEPGRAM_API_KEY:  # CHANGED: require key from env or main.py
    raise ValueError(  # CHANGED: (fix 10)
        "DEEPGRAM_API_KEY is not set. Define it in main.py or set the "  # CHANGED: (fix 10)
        "DEEPGRAM_API_KEY environment variable."  # CHANGED: (fix 10)
    )
DEEPGRAM_MODEL = "nova-3"
DEEPGRAM_SAMPLE_RATE = 16000
AUDIO_BLOCKSIZE = 4000  # silence padding chunk size at 16 kHz
WASAPI_FRAMES_PER_BUFFER = 2048
MIC_BLOCKSIZE = 1024  # microphone capture block size at 16 kHz
MIC_NOISE_GATE_INITIAL_RMS = 200.0  # CHANGED: adaptive gate seed for first 2s (fix 7)
MIC_RMS_ROLLING_WINDOW_S = 2.0  # CHANGED: rolling mic RMS window (fix 7)
MAX_AUDIO_QUEUE_SIZE = 100
MAX_TRANSCRIPT_HASH_HISTORY = 200  # CHANGED: prune dedup set size (fix 8)
FUZZY_DEDUP_JACCARD_THRESHOLD = 0.85  # CHANGED: fuzzy duplicate threshold (fix 8)
FUZZY_DEDUP_WINDOW_S = 3.0  # CHANGED: fuzzy duplicate time window (fix 8)
DG_KEEPALIVE_INTERVAL_S = 8.0  # CHANGED: WebSocket keepalive interval (fix 5/6)
DG_RECONNECT_BACKOFF_MAX_S = 30.0  # CHANGED: reconnect backoff cap (fix 5)
AUDIO_PROCESS_WARN_MS = 50
HEALTH_MONITOR_INTERVAL_MS = 5000

LANGUAGE_CONFIG = {
    "en": {
        "name": "English",
        "keyterms": ["Nova-3", "Alpha"],  # CHANGED: keyterm boosting (fix 11)
        "boost_phrases": [],
        "expected_wer": 0.03,
    },
    "ja": {
        "name": "Japanese",
        "keyterms": [],  # CHANGED: keyterm boosting (fix 11)
        "boost_phrases": [],
        "expected_wer": 0.03,
    },
    "zh-CN": {
        "name": "Chinese (Mandarin)",
        "keyterms": [],  # CHANGED: keyterm boosting (fix 11)
        "boost_phrases": [],
        "expected_wer": 0.03,
    },
    "ru": {
        "name": "Russian",
        "keyterms": [],  # CHANGED: keyterm boosting (fix 11)
        "boost_phrases": [],
        "expected_wer": 0.03,
    },
}

LANGUAGE_MAP = {
    cfg["name"]: code for code, cfg in LANGUAGE_CONFIG.items()
}

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
COLORS = {
    "main_bg": "#1e1e2e",
    "header_bg": "#252535",
    "container_bg": "#2d2d3d",
    "text_primary": "#ffffff",
    "text_secondary": "#b8b8d1",
    "accent_blue": "#4a9eff",
    "border": "#3d3d4d",
    "dropdown_bg": "#3d3d4d",
}

SPEAKER_COLORS = {
    "speaker_1": "#4a9eff",
    "speaker_2": "#50c878",
    "speaker_3": "#ffa500",
    "speaker_4": "#ff6b6b",
}

SOURCE_LANGUAGES = [
    "English",
    "Japanese",
    "Chinese (Mandarin)",
    "Russian",
]

TARGET_LANGUAGES = [
    "Japanese",
    "Chinese (Mandarin)",
    "Russian",
    "English",
]

BASE_DIR = Path(__file__).resolve().parent
COMPACT_BREAKPOINT = 900


def _pcm_to_mono_16k_np(audio_chunk_bytes, channels=2, sample_rate=48000):
    """Convert PCM bytes to a 16 kHz mono int16 numpy array (numpy-only, no audioop)."""
    if not audio_chunk_bytes:
        return np.array([], dtype=np.int16)

    audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16)

    if channels > 1:
        frame_count = len(audio_np) // channels
        if frame_count == 0:
            return np.array([], dtype=np.int16)
        audio_np = audio_np[: frame_count * channels].reshape(-1, channels)
        audio_np = audio_np.mean(axis=1).astype(np.int16)

    if sample_rate != DEEPGRAM_SAMPLE_RATE and len(audio_np) > 0:
        target_len = int(len(audio_np) * DEEPGRAM_SAMPLE_RATE / sample_rate)  # CHANGED: linear interp target (fix 2)
        if target_len < 1:  # CHANGED: guard empty output (fix 2)
            return np.array([], dtype=np.int16)  # CHANGED: (fix 2)
        indices = np.linspace(0, len(audio_np) - 1, target_len)  # CHANGED: linear interpolation (fix 2)
        audio_np = np.interp(  # CHANGED: replace integer stride resampling (fix 2)
            indices,  # CHANGED: (fix 2)
            np.arange(len(audio_np)),  # CHANGED: (fix 2)
            audio_np.astype(np.float32),  # CHANGED: (fix 2)
        ).astype(np.int16)  # CHANGED: (fix 2)

    return audio_np.astype(np.int16)


def _apply_noise_gate(audio_np, threshold=MIC_NOISE_GATE_INITIAL_RMS):
    """Silence microphone chunks below RMS threshold to prevent speaker echo."""
    if audio_np.size == 0:
        return audio_np
    rms = float(np.sqrt(np.mean(audio_np.astype(np.float64) ** 2)))
    if rms < threshold:
        return np.zeros_like(audio_np)
    return audio_np


def _mix_audio_chunks(system_np, mic_np):
    """Sum system + mic arrays (same length) with 16-bit clipping."""
    if system_np.size == 0 and mic_np.size == 0:
        return np.array([], dtype=np.int16)
    if system_np.size == 0:
        return mic_np.astype(np.int16)
    if mic_np.size == 0:
        return system_np.astype(np.int16)
    target_len = max(system_np.size, mic_np.size)
    if system_np.size < target_len:
        system_np = np.pad(system_np, (0, target_len - system_np.size))
    if mic_np.size < target_len:
        mic_np = np.pad(mic_np, (0, target_len - mic_np.size))
    mixed = system_np.astype(np.int32) + mic_np.astype(np.int32)
    return np.clip(mixed, -32768, 32767).astype(np.int16)


def _process_audio_chunk(audio_chunk_bytes, channels=2, sample_rate=48000):
    """Convert PCM bytes to 16 kHz mono int16 bytes for Deepgram."""
    return _pcm_to_mono_16k_np(audio_chunk_bytes, channels, sample_rate).tobytes()


class AlphaApp(ctk.CTk):
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
        logo_path = BASE_DIR / "logo.png"
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
    def _build_deepgram_url(self):
        """Build the Deepgram live-listen WebSocket URL with Nova-3 accuracy options."""
        lang = self._listen_language
        lang_cfg = LANGUAGE_CONFIG.get(lang, {})  # CHANGED: read keyterms for active lang (fix 11)
        keyterm_params = ""  # CHANGED: accumulate keyterm query params (fix 11)
        for term in lang_cfg.get("keyterms", []):  # CHANGED: append each keyterm (fix 11)
            keyterm_params += f"&keyterm={quote(term)}"  # CHANGED: URL-encode keyterm (fix 11)
        params = (
            f"model={DEEPGRAM_MODEL}"
            f"&language={lang}"
            f"&punctuate=true"
            f"&smart_format=true"
            f"&diarize_model=latest"  # CHANGED: replace deprecated diarize=true (fix 1)
            f"&numerals=true"
            f"&profanity_filter=false"
            f"&redact=false"
            f"&endpointing=650"  # CHANGED: less aggressive endpointing for fast speech (fix 3)
            f"&utterance_end_ms=3000"  # CHANGED: longer utterance window (fix 3)
            f"&encoding=linear16"
            f"&sample_rate={DEEPGRAM_SAMPLE_RATE}"
            f"&channels=1"
            # utterance_end_ms requires interim_results=true on Deepgram's API;
            # interim payloads are still ignored in _deepgram_on_message.
            f"&interim_results=true"
            f"{keyterm_params}"  # CHANGED: language keyterm boosting (fix 11)
        )
        return f"wss://api.deepgram.com/v1/listen?{params}"

    def _get_language_name(self, lang_code):
        """Return display name for a Deepgram language code."""
        return LANGUAGE_CONFIG.get(lang_code, {}).get("name", lang_code)

    def _print_accuracy_startup(self, lang_code):
        """Print expected accuracy target for the selected language."""
        cfg = LANGUAGE_CONFIG.get(lang_code, {})
        lang_name = cfg.get("name", lang_code)
        expected_wer = cfg.get("expected_wer", 0.03)
        expected_accuracy = (1 - expected_wer) * 100
        print(f"[Nova-3] Model activated - Enhanced accuracy mode")
        print(f"[Language] Set to: {lang_name} ({lang_code})")
        print(f"[Diarization] diarize_model=latest enabled")  # CHANGED: reflect new diarize param (fix 1)
        print(
            f"[Accuracy] Target: {expected_accuracy:.1f}% "
            f"(WER <= {expected_wer:.2f}) for {lang_name}"
        )

    def _put_bounded(self, audio_queue, item):
        """Enqueue audio; drop oldest item if the queue is full."""
        if audio_queue is None:
            return False
        try:
            audio_queue.put_nowait(item)
            return True
        except queue.Full:
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                audio_queue.put_nowait(item)
                return True
            except queue.Full:
                return False

    def _get_wasapi_loopback_device(self):
        """Get the default WASAPI loopback device via pyaudiowpatch."""
        pa = pyaudio.PyAudio()
        try:
            loopback = pa.get_default_wasapi_loopback()
            if loopback is None:
                raise RuntimeError("No default WASAPI loopback device available.")
            print(f"Found WASAPI loopback device: {loopback.get('name', 'unknown')}")
            return pa, loopback
        except Exception:
            pa.terminate()
            raise

    def _wasapi_stream_callback(self, in_data, _frame_count, _time_info, _status_flags):
        """PyAudio WASAPI callback — push raw system PCM to sys_audio_queue."""
        if self._stop_event.is_set():
            return (None, pyaudio.paComplete)
        if in_data and self.sys_audio_queue is not None:
            if not self._put_bounded(self.sys_audio_queue, in_data):
                print("System audio queue full — dropped oldest chunk")
        return (None, pyaudio.paContinue)

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

                sys_np_16k = _pcm_to_mono_16k_np(  # CHANGED: unified linear resampling path (fix 2)
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

    def _mic_callback(self, indata, _frames, _time, status):
        """sounddevice callback — push raw mic PCM bytes to mic_audio_queue."""
        if status:
            print(status)
        if self._stop_event.is_set() or self.mic_audio_queue is None:
            return
        raw = indata.tobytes()
        if raw and not self._put_bounded(self.mic_audio_queue, raw):
            print("Microphone audio queue full — dropped oldest chunk")

    def _start_microphone_capture(self):
        """Start capturing the default microphone via sounddevice (16 kHz mono)."""
        try:
            device = sd.default.device[0]
            device_name = sd.query_devices(device).get("name", "unknown")
            print(f"Capturing from microphone: {device_name}")

            self._mic_stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=DEEPGRAM_SAMPLE_RATE,
                dtype="int16",
                blocksize=MIC_BLOCKSIZE,
                callback=self._mic_callback,
            )
            self._mic_stream.start()
            print("Microphone stream started successfully")
        except Exception as exc:
            print(f"Microphone capture error: {exc}")
            raise

    def _close_microphone_stream(self):
        """Stop and release the sounddevice microphone stream."""
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception as exc:
                print(f"Error closing microphone stream: {exc}")
            self._mic_stream = None

    def _start_wasapi_loopback(self):
        """Start capturing all system audio via WASAPI loopback (zero user setup)."""
        try:
            self._pyaudio, loopback = self._get_wasapi_loopback_device()
            self._wasapi_channels = int(loopback["maxInputChannels"])
            self._wasapi_rate = int(loopback["defaultSampleRate"])
            self._wasapi_frames_per_buffer = WASAPI_FRAMES_PER_BUFFER

            print("Starting WASAPI loopback audio capture...")
            print(
                f"Capturing from device: {loopback.get('name', 'unknown')} "
                f"({self._wasapi_rate} Hz, {self._wasapi_channels} ch)"
            )

            self._wasapi_stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=self._wasapi_channels,
                rate=self._wasapi_rate,
                input=True,
                frames_per_buffer=self._wasapi_frames_per_buffer,
                input_device_index=loopback["index"],
                stream_callback=self._wasapi_stream_callback,
            )
            self._wasapi_stream.start_stream()
            print("WASAPI loopback stream started successfully")
        except Exception as exc:
            print(f"WASAPI loopback error: {exc}")
            self._close_wasapi_stream()
            messagebox.showerror(
                "WASAPI Loopback Error",
                "Could not capture system audio automatically.\n\n"
                "Please check Windows Sound settings and ensure your default "
                "playback/speaker device is working.\n\n"
                f"Details: {exc}",
            )
            raise

    def _close_wasapi_stream(self):
        """Stop and release WASAPI loopback resources."""
        if self._wasapi_stream is not None:
            try:
                if self._wasapi_stream.is_active():
                    self._wasapi_stream.stop_stream()
                self._wasapi_stream.close()
            except Exception as exc:
                print(f"Error closing WASAPI stream: {exc}")
            self._wasapi_stream = None

        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception as exc:
                print(f"Error terminating PyAudio: {exc}")
            self._pyaudio = None

        self._mix_thread = None

    def _words_to_segment_text(self, words):
        """Join Deepgram word objects into a single segment string."""
        parts = []
        for word in words:
            token = word.get("punctuated_word") or word.get("word") or ""
            if token:
                parts.append(token)
        return " ".join(parts).strip()

    def extract_speaker_from_nova3(self, data):
        """Split utterance by mid-segment speaker changes using word-level metadata."""
        try:
            alternatives = data.get("channel", {}).get("alternatives", [])
            if not alternatives:
                return []

            alt = alternatives[0]
            words = alt.get("words", [])
            full_transcript = alt.get("transcript", "").strip()

            if not words:
                if not full_transcript:
                    return []
                return [{"speaker": self._fallback_speaker_detection(), "text": full_transcript}]

            segments = []
            run_speaker = None
            run_words = []

            def flush_run():
                nonlocal run_words, run_speaker
                if run_speaker is not None and run_words:
                    text = self._words_to_segment_text(run_words)
                    if text:
                        segments.append({"speaker": run_speaker, "text": text})
                run_words = []

            for word in words:
                raw_sp = word.get("speaker")
                sp_num = int(raw_sp) + 1 if raw_sp is not None else run_speaker

                if run_speaker is not None and sp_num is not None and sp_num != run_speaker:
                    flush_run()  # CHANGED: speaker change mid-utterance split (fix 4)
                    run_speaker = sp_num
                    run_words = [word]
                else:
                    if run_speaker is None and sp_num is not None:
                        run_speaker = sp_num
                    run_words.append(word)

            flush_run()

            if len(segments) > 1:
                print(f"[Speaker] Split utterance into {len(segments)} speaker segments")  # CHANGED: (fix 4)

            if segments:
                return segments

            if full_transcript:
                return [{"speaker": self._fallback_speaker_detection(), "text": full_transcript}]
            return []

        except Exception as e:
            print(f"[ERROR] Extracting speaker: {e}")
            return [{"speaker": 1, "text": ""}]

    def _fallback_speaker_detection(self):
        """Time-based fallback ONLY when no diarization metadata exists."""
        current_time = time.time()

        if not hasattr(self, "last_speech_time"):
            self.last_speech_time = current_time
            self.fallback_speaker = 1
            return 1

        time_gap = current_time - self.last_speech_time
        self.last_speech_time = current_time

        if time_gap > 4.0:
            if not hasattr(self, "fallback_speaker"):
                self.fallback_speaker = 1
            self.fallback_speaker = (self.fallback_speaker % 4) + 1
            print(
                f"[Speaker] Fallback (gap {time_gap:.1f}s): "
                f"Speaker {self.fallback_speaker}"
            )
            return self.fallback_speaker

        return getattr(self, "fallback_speaker", 1)

    def _deepgram_on_message(self, _ws, message):
        """Handle Deepgram WebSocket messages - simplified final-only processing."""
        if not isinstance(message, str):
            return
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type in ("Metadata", "Open"):
                return

            if msg_type == "UtteranceEnd":
                print("[UtteranceEnd] Resetting speaker state")
                self.current_speaker = None
                self.fallback_speaker = 1
                self.last_speech_time = time.time()
                return

            if msg_type != "Results":
                return

            # ONLY process final results - IGNORE interim completely
            if not data.get("is_final", False):
                return

            alternatives = data.get("channel", {}).get("alternatives", [])
            if not alternatives:
                return

            transcript = alternatives[0].get("transcript", "").strip()
            if not transcript:
                return

            segments = self.extract_speaker_from_nova3(data)  # CHANGED: list of speaker segments (fix 4)
            if not segments:
                return

            if self._dg_awaiting_transcript_reset:  # CHANGED: reset backoff after reconnect transcript (fix 5)
                self._dg_backoff_seconds = 1.0  # CHANGED: (fix 5)
                self._dg_awaiting_transcript_reset = False  # CHANGED: (fix 5)
                print("[Reconnect] Backoff reset after first transcript")  # CHANGED: (fix 5)

            for segment in segments:  # CHANGED: enqueue one item per speaker run (fix 4)
                speaker_num = segment.get("speaker", 1)
                segment_text = segment.get("text", "").strip()
                if not segment_text:
                    continue
                self.transcript_queue.put(  # CHANGED: thread-safe UI queue (fix 4)
                    {
                        "speaker": speaker_num,
                        "text": segment_text,
                        "is_final": True,
                    }
                )
                self._transcripts_received += 1
                print(f"[FINAL] Speaker {speaker_num}: {segment_text}")

        except Exception as e:
            print(f"[ERROR] Processing Deepgram message: {e}")
            import traceback
            traceback.print_exc()

    def _deepgram_on_open(self, ws):
        """Start streaming queued audio to Deepgram when the socket opens."""
        print("Nova-3 connected — streaming audio to Deepgram")

        replay_chunks = list(getattr(self, "_dg_replay_buffer", []) or [])  # CHANGED: replay after reconnect (fix 5)
        self._dg_replay_buffer = []  # CHANGED: clear replay buffer (fix 5)

        def stream_audio():
            chunks_sent = 0
            last_keepalive = time.perf_counter()  # CHANGED: track keepalive timing (fix 5/6)
            for chunk in replay_chunks:  # CHANGED: send buffered audio first (fix 5)
                if self._stop_event.is_set():
                    return
                try:
                    ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)  # CHANGED: replay buffered PCM (fix 5)
                    chunks_sent += 1  # CHANGED: (fix 5)
                    self._chunks_sent_count = chunks_sent  # CHANGED: (fix 5)
                    last_keepalive = time.perf_counter()  # CHANGED: (fix 5)
                except Exception as exc:
                    print(f"Error replaying audio to Deepgram: {exc}")  # CHANGED: (fix 5)
                    return
            if replay_chunks:  # CHANGED: (fix 5)
                print(f"[Reconnect] Replayed {len(replay_chunks)} buffered audio chunks")  # CHANGED: (fix 5)

            while not self._stop_event.is_set():
                try:
                    chunk = self._audio_q.get(timeout=0.1)
                except queue.Empty:
                    if time.perf_counter() - last_keepalive >= DG_KEEPALIVE_INTERVAL_S:  # CHANGED: JSON keepalive (fix 5/6)
                        try:
                            ws.send(json.dumps({"type": "KeepAlive"}))  # CHANGED: replace silence injection (fix 6)
                            last_keepalive = time.perf_counter()  # CHANGED: (fix 5/6)
                        except Exception as exc:
                            print(f"Error sending Deepgram keepalive: {exc}")  # CHANGED: (fix 5)
                            break
                    continue
                try:
                    ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                    chunks_sent += 1
                    self._chunks_sent_count = chunks_sent
                    last_keepalive = time.perf_counter()  # CHANGED: audio counts as activity (fix 5)
                    if chunks_sent == 1:
                        print(
                            f"First audio chunk sent to Deepgram ({len(chunk)} bytes)"
                        )
                except Exception as exc:
                    print(f"Error sending audio to Deepgram: {exc}")
                    break

        threading.Thread(target=stream_audio, daemon=True).start()

    def _schedule_reconnect(self):
        """Queue a Deepgram reconnect attempt (daemon thread, no self.after)."""
        if not self.is_listening or self._stop_event.is_set():  # CHANGED: only while listening (fix 5)
            return
        with self._dg_reconnect_lock:  # CHANGED: (fix 5)
            if self._dg_reconnecting:  # CHANGED: avoid duplicate reconnect threads (fix 5)
                return
            self._dg_reconnecting = True  # CHANGED: (fix 5)
        threading.Thread(target=self._reconnect_deepgram, daemon=True).start()  # CHANGED: (fix 5)

    def _reconnect_deepgram(self):
        """Reconnect to Deepgram with exponential backoff and audio replay."""
        try:
            buffered = []  # CHANGED: snapshot queued audio before reconnect (fix 5)
            if self._audio_q is not None:  # CHANGED: (fix 5)
                while True:  # CHANGED: (fix 5)
                    try:
                        buffered.append(self._audio_q.get_nowait())  # CHANGED: (fix 5)
                    except queue.Empty:  # CHANGED: (fix 5)
                        break  # CHANGED: (fix 5)
            self._dg_replay_buffer = buffered  # CHANGED: replay on next on_open (fix 5)

            wait_s = min(self._dg_backoff_seconds, DG_RECONNECT_BACKOFF_MAX_S)  # CHANGED: backoff cap (fix 5)
            print(f"[Reconnect] Waiting {wait_s:.0f}s before reconnect (backoff)")  # CHANGED: (fix 5)
            time.sleep(wait_s)  # CHANGED: exponential backoff delay (fix 5)
            self._dg_backoff_seconds = min(self._dg_backoff_seconds * 2, DG_RECONNECT_BACKOFF_MAX_S)  # CHANGED: (fix 5)

            if not self.is_listening or self._stop_event.is_set():  # CHANGED: (fix 5)
                return

            if self._dg_ws is not None:  # CHANGED: close stale socket (fix 5)
                try:
                    self._dg_ws.close()  # CHANGED: (fix 5)
                except Exception:
                    pass  # CHANGED: (fix 5)
                self._dg_ws = None  # CHANGED: (fix 5)

            url = self._build_deepgram_url()  # CHANGED: fresh URL on reconnect (fix 5)
            print(f"[Reconnect] Connecting to Deepgram: {url}")  # CHANGED: (fix 5)
            self._dg_awaiting_transcript_reset = True  # CHANGED: reset backoff after transcript (fix 5)
            ws = websocket.WebSocketApp(  # CHANGED: new WebSocket session (fix 5)
                url,  # CHANGED: (fix 5)
                header={"Authorization": f"Token {DEEPGRAM_API_KEY}"},  # CHANGED: (fix 5)
                on_message=self._deepgram_on_message,  # CHANGED: (fix 5)
                on_open=self._deepgram_on_open,  # CHANGED: (fix 5)
                on_error=self._deepgram_on_error,  # CHANGED: (fix 5)
                on_close=self._deepgram_on_close,  # CHANGED: (fix 5)
            )  # CHANGED: (fix 5)
            self._dg_ws = ws  # CHANGED: (fix 5)
            ws.run_forever()  # CHANGED: blocking reconnect in daemon thread (fix 5)
        except Exception as exc:
            print(f"[Reconnect] Deepgram reconnect error: {exc}")  # CHANGED: (fix 5)
        finally:
            with self._dg_reconnect_lock:  # CHANGED: release reconnect lock (fix 5)
                self._dg_reconnecting = False  # CHANGED: (fix 5)

    def _deepgram_on_close(self, _ws, code, msg):
        """Handle WebSocket close; schedule reconnect while listening."""
        print(f"Deepgram closed: {code} {msg}")  # CHANGED: explicit close handler (fix 5)
        if self.is_listening and not self._stop_event.is_set():  # CHANGED: auto-reconnect (fix 5)
            self._schedule_reconnect()  # CHANGED: (fix 5)

    def _deepgram_on_error(self, _ws, err):
        """Handle WebSocket errors; reconnect on transient failures."""
        print(f"Deepgram WebSocket error: {err}")
        err_text = str(err)
        if "400" in err_text or "INVALID_QUERY_PARAMETER" in err_text:
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Deepgram Connection Error",
                    "Could not connect to Deepgram.\n\n"
                    f"{err_text}\n\n"
                    "Listening has been stopped.",
                ),
            )
            self.after(0, self._stop_listening)
            return
        if self.is_listening and not self._stop_event.is_set():  # CHANGED: reconnect transient errors (fix 5)
            self._schedule_reconnect()  # CHANGED: (fix 5)

    def _deepgram_worker(self):
        """Run the Deepgram WebSocket connection in a background thread."""
        url = self._build_deepgram_url()
        lang_code = self._listen_language
        print(f"\n{'=' * 60}")
        print("CONNECTING TO NOVA-3")
        print("Model: nova-3")
        print(f"Language: {lang_code}")
        print("Diarization: diarize_model=latest")  # CHANGED: reflect streaming diarize param (fix 1)
        print("Processing: Final results ONLY (interim ignored in UI)")
        print(f"{'=' * 60}\n")
        print(f"Deepgram URL: {url}")
        try:
            ws = websocket.WebSocketApp(
                url,
                header={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
                on_message=self._deepgram_on_message,
                on_open=self._deepgram_on_open,
                on_error=self._deepgram_on_error,
                on_close=self._deepgram_on_close,  # CHANGED: reconnect on close (fix 5)
            )
            self._dg_ws = ws
            ws.run_forever()
        except Exception as exc:
            print(f"Deepgram connection error: {exc}")

    def _health_monitor(self):
        """Log pipeline health every 5 seconds while listening."""
        if not self.is_listening:
            self._health_monitor_job = None
            return

        audio_qsize = self._audio_q.qsize() if self._audio_q else 0
        sys_qsize = self.sys_audio_queue.qsize() if self.sys_audio_queue else 0
        mic_qsize = self.mic_audio_queue.qsize() if self.mic_audio_queue else 0
        print(
            f"[Health] audio_q={audio_qsize}, sys_q={sys_qsize}, mic_q={mic_qsize}, "
            f"chunks_sent={self._chunks_sent_count}, "
            f"transcripts={self._transcripts_received}, "
            f"lang={self._listen_language}"
        )
        self._health_monitor_job = self.after(
            HEALTH_MONITOR_INTERVAL_MS, self._health_monitor
        )

    def _start_health_monitor(self):
        """Begin periodic health logging."""
        if self._health_monitor_job is not None:
            self.after_cancel(self._health_monitor_job)
        self._health_monitor_job = self.after(
            HEALTH_MONITOR_INTERVAL_MS, self._health_monitor
        )

    def _stop_health_monitor(self):
        """Stop periodic health logging."""
        if self._health_monitor_job is not None:
            self.after_cancel(self._health_monitor_job)
            self._health_monitor_job = None

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

    def _normalize_transcript_tokens(self, text):
        """Lowercase token set with punctuation stripped for fuzzy dedup."""
        cleaned = re.sub(r"[^\w\s]", "", text.lower())  # CHANGED: normalize for fuzzy match (fix 8)
        return set(cleaned.split())  # CHANGED: (fix 8)

    def _jaccard_similarity(self, tokens_a, tokens_b):
        """Jaccard index between two token sets."""
        if not tokens_a and not tokens_b:  # CHANGED: (fix 8)
            return 1.0  # CHANGED: (fix 8)
        union = tokens_a | tokens_b  # CHANGED: (fix 8)
        if not union:  # CHANGED: (fix 8)
            return 0.0  # CHANGED: (fix 8)
        return len(tokens_a & tokens_b) / len(union)  # CHANGED: (fix 8)

    def _prune_transcript_hashes(self, transcript_hash):
        """Keep only the most recent MAX_TRANSCRIPT_HASH_HISTORY hashes."""
        self.last_transcript_hash.add(transcript_hash)  # CHANGED: track hash (fix 8)
        self._transcript_hash_order.append(transcript_hash)  # CHANGED: ordered prune list (fix 8)
        while len(self._transcript_hash_order) > MAX_TRANSCRIPT_HASH_HISTORY:  # CHANGED: cap at 200 (fix 8)
            old_hash = self._transcript_hash_order.pop(0)  # CHANGED: drop oldest (fix 8)
            self.last_transcript_hash.discard(old_hash)  # CHANGED: (fix 8)

    def _is_fuzzy_duplicate(self, speaker_num, text):
        """Skip near-duplicate utterances from same speaker within 3 seconds."""
        now = time.time()  # CHANGED: fuzzy dedup timestamp (fix 8)
        tokens = self._normalize_transcript_tokens(text)  # CHANGED: (fix 8)
        prev = self._last_speaker_utterance.get(speaker_num)  # CHANGED: (fix 8)
        if prev:  # CHANGED: (fix 8)
            prev_tokens, prev_time = prev  # CHANGED: (fix 8)
            if (  # CHANGED: (fix 8)
                now - prev_time <= FUZZY_DEDUP_WINDOW_S  # CHANGED: within 3s window (fix 8)
                and self._jaccard_similarity(tokens, prev_tokens)  # CHANGED: (fix 8)
                > FUZZY_DEDUP_JACCARD_THRESHOLD  # CHANGED: >0.85 similarity (fix 8)
            ):  # CHANGED: (fix 8)
                return True  # CHANGED: (fix 8)
        self._last_speaker_utterance[speaker_num] = (tokens, now)  # CHANGED: update last utterance (fix 8)
        return False  # CHANGED: (fix 8)

    def _display_transcript_item(self, item):
        """Render one transcript dict into the Initial verse text box."""
        speaker_num = item.get("speaker", 1)
        text = item.get("text", "").strip()
        if not text:
            return

        transcript_key = f"spk{speaker_num}:{text}"
        transcript_hash = hash(transcript_key)

        if transcript_hash in self.last_transcript_hash:  # CHANGED: exact hash dedup (fix 8)
            print(f"[SKIP] Duplicate: Speaker {speaker_num}: {text[:30]}...")
            return

        if self._is_fuzzy_duplicate(speaker_num, text):  # CHANGED: fuzzy Jaccard dedup (fix 8)
            print(f"[SKIP] Fuzzy duplicate: Speaker {speaker_num}: {text[:30]}...")  # CHANGED: (fix 8)
            return

        # ==========================================
        # TODO: DEEPL TRANSLATION INTEGRATION
        # When DeepL is added, send 'text'
        # to the DeepL API here and put the result
        # into the 'Translated verse' text box.
        # ==========================================

        self.initial_verse_box.configure(state="normal")

        if self.last_displayed_speaker is not None:
            if speaker_num != self.last_displayed_speaker:
                self.initial_verse_box.insert(tk.END, "\n\n")

        label = f"[Speaker {speaker_num}] "
        start_idx = self.initial_verse_box.index(tk.END)
        self.initial_verse_box.insert(tk.END, label)

        tag_name = f"speaker_{speaker_num}"
        if tag_name not in self.initial_verse_box.tag_names():
            colors = {
                1: "#4a9eff",
                2: "#50c878",
                3: "#ffa500",
                4: "#ff6b6b",
                5: "#9b59b6",
                6: "#1abc9c",
                7: "#e74c3c",
                8: "#3498db",
                9: "#f39c12",
                10: "#2ecc71",
                11: "#95a5a6",
                12: "#e91e63",
            }
            color = colors.get(speaker_num, "#ffffff")
            self.initial_verse_box.tag_configure(
                tag_name,
                foreground=color,
                font=("Segoe UI", 12, "bold"),
            )

        end_idx = self.initial_verse_box.index(tk.END)
        self.initial_verse_box.tag_add(tag_name, start_idx, end_idx)
        self.initial_verse_box.insert(tk.END, text + "\n")
        self.initial_verse_box.configure(state="disabled")

        self._prune_transcript_hashes(transcript_hash)  # CHANGED: prune hash set to 200 (fix 8)
        self.last_displayed_speaker = speaker_num
        self.last_speaker_id = speaker_num
        self.last_speaker = speaker_num
        self.initial_verse_box.see(tk.END)
        self.check_scrollbar_visibility(
            self.initial_verse_box, self.initial_verse_box._scrollbar
        )
        print(f"[UI] Speaker {speaker_num}: {text[:50]}...")

    def process_ui_queue(self):
        """Process transcript queue with duplicate protection (UI thread via after)."""
        try:
            if not hasattr(self, "last_transcript_hash"):
                self.last_transcript_hash = set()
            if not hasattr(self, "last_displayed_speaker"):
                self.last_displayed_speaker = None
            if not hasattr(self, "_transcript_hash_order"):
                self._transcript_hash_order = []
            if not hasattr(self, "_last_speaker_utterance"):
                self._last_speaker_utterance = {}

            while not self.transcript_queue.empty():
                item = self.transcript_queue.get()

                if isinstance(item, list):  # CHANGED: support list-of-dicts format (fix 4)
                    items_to_process = item  # CHANGED: backward compatibility (fix 4)
                else:
                    items_to_process = [item]  # CHANGED: single dict format (fix 4)

                for sub_item in items_to_process:  # CHANGED: one line per speaker segment (fix 4)
                    self._display_transcript_item(sub_item)  # CHANGED: (fix 4)

        except Exception as e:
            print(f"[ERROR] Processing UI queue: {e}")
            import traceback
            traceback.print_exc()

        self.after(100, self.process_ui_queue)

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


if __name__ == "__main__":
    try:
        print(f"Alpha V{APP_VERSION} ({APP_CODENAME})")
        app = AlphaApp()
        app.mainloop()
    except ValueError as exc:  # CHANGED: surface missing API key at startup (fix 10)
        print(exc)  # CHANGED: (fix 10)
        sys.exit(1)  # CHANGED: (fix 10)
