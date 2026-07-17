"""
Alpha V2 - Live Meeting Translation Desktop Application
Responsive header, speaker-colored text, and Deepgram real-time STT.
"""

import json
import queue
import re
import threading
import time
from pathlib import Path

import customtkinter as ctk
import numpy as np
import pyaudiowpatch as pyaudio
import sounddevice as sd
import tkinter as tk
import websocket
from PIL import Image
from tkinter import Menu, messagebox

# ---------------------------------------------------------------------------
# Deepgram configuration
# ---------------------------------------------------------------------------
DEEPGRAM_API_KEY = "77dc0b90793074722dcae7875198e9cb24a68838"
DEEPGRAM_MODEL = "nova-2-general"
DEEPGRAM_SAMPLE_RATE = 16000
AUDIO_BLOCKSIZE = 4000  # 250ms chunks at 16 kHz (microphone path)
WASAPI_FRAMES_PER_BUFFER = 2048
MAX_AUDIO_QUEUE_SIZE = 10
AUDIO_PROCESS_WARN_MS = 50

AUDIO_SOURCES = [
    "System Audio (WASAPI Loopback)",
    "Microphone",
]

LANGUAGE_MAP = {
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Portuguese": "pt",
    "Italian": "it",
    "Chinese": "zh",
    "Japanese": "ja",
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
    "Spanish",
    "French",
    "German",
    "Portuguese",
    "Italian",
    "Chinese",
    "Japanese",
]

TARGET_LANGUAGES = [
    "Spanish",
    "French",
    "German",
    "Portuguese",
    "Italian",
    "Chinese",
    "Japanese",
    "English",
]

BASE_DIR = Path(__file__).resolve().parent
COMPACT_BREAKPOINT = 900


def _process_audio_chunk(audio_chunk_bytes, channels=2, sample_rate=48000):
    """Convert WASAPI PCM (e.g. 48 kHz stereo int16) to 16 kHz mono for Deepgram."""
    if not audio_chunk_bytes:
        return b""

    audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16)

    if channels > 1:
        frame_count = len(audio_np) // channels
        if frame_count == 0:
            return b""
        audio_np = audio_np[: frame_count * channels].reshape(-1, channels)
        audio_np = audio_np.mean(axis=1).astype(np.int16)

    if sample_rate != DEEPGRAM_SAMPLE_RATE and len(audio_np) > 0:
        if sample_rate % DEEPGRAM_SAMPLE_RATE == 0:
            audio_np = audio_np[:: sample_rate // DEEPGRAM_SAMPLE_RATE]
        else:
            target_len = int(len(audio_np) * DEEPGRAM_SAMPLE_RATE / sample_rate)
            if target_len < 1:
                return b""
            indices = (
                np.arange(target_len, dtype=np.int64)
                * sample_rate
                // DEEPGRAM_SAMPLE_RATE
            )
            indices = np.clip(indices, 0, len(audio_np) - 1)
            audio_np = audio_np[indices]

    return audio_np.tobytes()


class AlphaApp(ctk.CTk):
    """Main application window for Alpha live meeting translation (V2)."""

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
        self.target_language = ctk.StringVar(value="Spanish")
        self.audio_source = ctk.StringVar(value=AUDIO_SOURCES[0])
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
        self.is_listening = False
        self._listen_language = LANGUAGE_MAP.get(self.source_language.get(), "en")
        self._audio_q = None
        self._raw_audio_q = None
        self._audio_stream = None
        self._pyaudio = None
        self._wasapi_stream = None
        self._wasapi_thread = None
        self._wasapi_process_thread = None
        self._wasapi_channels = 1
        self._wasapi_rate = DEEPGRAM_SAMPLE_RATE
        self._wasapi_frames_per_buffer = WASAPI_FRAMES_PER_BUFFER
        self._dg_ws = None
        self._dg_thread = None
        self._stop_event = threading.Event()
        self._speaker_lock = threading.Lock()
        self._last_assigned_speaker = 1
        self._last_speaker_change_time = 0.0
        self._ui_last_speaker = None

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
        self.title("Alpha")
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

        self.audio_source_label = ctk.CTkLabel(
            master=self.header_frame,
            text="Audio Source:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
        )

        self.audio_source_combo = ctk.CTkComboBox(
            master=self.header_frame,
            values=AUDIO_SOURCES,
            variable=self.audio_source,
            width=260,
            height=32,
            font=ctk.CTkFont(family="Segoe UI", size=12),
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
            self.audio_source_label,
            self.audio_source_combo,
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

        menu_audio_label = ctk.CTkLabel(
            master=self.menu_dropdown_frame,
            text="Audio Source:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        menu_audio_label.pack(fill="x", padx=15, pady=(4, 4))

        self.audio_source_combo_menu = ctk.CTkComboBox(
            master=self.menu_dropdown_frame,
            values=AUDIO_SOURCES,
            variable=self.audio_source,
            width=260,
            height=32,
            font=ctk.CTkFont(family="Segoe UI", size=12),
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
        self.audio_source_combo_menu.pack(fill="x", padx=15, pady=(0, 8))

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
        """Build the Deepgram live-listen WebSocket URL with accuracy options."""
        lang = self._listen_language
        params = (
            f"model={DEEPGRAM_MODEL}"
            f"&language={lang}"
            f"&punctuate=true"
            f"&smart_format=true"
            f"&diarize=true"
            f"&diarize_version=2"
            f"&numerals=true"
            f"&endpointing=300"
            f"&utterance_end_ms=1000"
            f"&encoding=linear16"
            f"&sample_rate={DEEPGRAM_SAMPLE_RATE}"
            f"&channels=1"
            f"&interim_results=true"
        )
        return f"wss://api.deepgram.com/v1/listen?{params}"

    def _is_system_audio_source(self, source):
        """Return True when the user selected WASAPI system audio capture."""
        return source.startswith("System Audio")

    def _convert_audio_for_deepgram(self, data, channels, sample_rate):
        """Convert captured PCM to mono 16 kHz int16 for Deepgram."""
        return _process_audio_chunk(data, channels, sample_rate)

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

    def _enqueue_pcm_for_deepgram(self, pcm):
        """Put processed PCM on the Deepgram queue, skipping if backlog is too large."""
        if not pcm or self._audio_q is None:
            return
        qsize = self._audio_q.qsize()
        if qsize >= MAX_AUDIO_QUEUE_SIZE:
            print(f"Queue size: {qsize} items — skipping chunk")
            return
        if not self._put_bounded(self._audio_q, pcm):
            print(f"Queue size: {qsize} items — could not enqueue chunk")

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

    def _wasapi_read_loop(self):
        """Background thread: read raw WASAPI PCM only (no conversion)."""
        print("WASAPI capture thread started")
        while not self._stop_event.is_set() and self._wasapi_stream is not None:
            try:
                raw = self._wasapi_stream.read(
                    self._wasapi_frames_per_buffer,
                    exception_on_overflow=False,
                )
                if raw and not self._put_bounded(self._raw_audio_q, raw):
                    print("Raw audio queue full — dropped oldest chunk")
            except Exception as exc:
                if not self._stop_event.is_set():
                    print(f"WASAPI read error: {exc}")
                continue
        print("WASAPI capture thread stopped")

    def _wasapi_process_loop(self):
        """Background thread: numpy conversion and enqueue for Deepgram."""
        print("WASAPI processing thread started")
        chunks_processed = 0
        while not self._stop_event.is_set():
            try:
                raw = self._raw_audio_q.get(timeout=0.25)
            except queue.Empty:
                continue

            t0 = time.perf_counter()
            pcm = _process_audio_chunk(
                raw, self._wasapi_channels, self._wasapi_rate
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if elapsed_ms > AUDIO_PROCESS_WARN_MS:
                print(f"Audio processing took {elapsed_ms:.1f} ms (slow)")
            elif chunks_processed and chunks_processed % 50 == 0:
                print(f"Audio processing took {elapsed_ms:.1f} ms")

            if pcm:
                self._enqueue_pcm_for_deepgram(pcm)
                chunks_processed += 1
                if chunks_processed == 1:
                    print(
                        f"First WASAPI chunk processed "
                        f"({len(pcm)} bytes @ {DEEPGRAM_SAMPLE_RATE} Hz mono)"
                    )
                elif chunks_processed % 50 == 0:
                    qsize = self._audio_q.qsize() if self._audio_q else 0
                    print(f"Queue size: {qsize} items")
        print("WASAPI processing thread stopped")

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
            )
            self._wasapi_stream.start_stream()

            self._wasapi_thread = threading.Thread(
                target=self._wasapi_read_loop, daemon=True
            )
            self._wasapi_process_thread = threading.Thread(
                target=self._wasapi_process_loop, daemon=True
            )
            self._wasapi_thread.start()
            self._wasapi_process_thread.start()
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

        self._wasapi_thread = None
        self._wasapi_process_thread = None

    def _start_microphone_capture(self):
        """Start capturing the default microphone via sounddevice."""
        try:
            device = sd.default.device[0]
            device_name = sd.query_devices(device).get("name", "unknown")
            print(f"Capturing from microphone: {device_name}")

            self._audio_stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=DEEPGRAM_SAMPLE_RATE,
                dtype="int16",
                blocksize=AUDIO_BLOCKSIZE,
                callback=self._audio_callback,
            )
            self._audio_stream.start()
            print("Microphone stream started successfully")
        except Exception as exc:
            print(f"Microphone capture error: {exc}")
            raise

    def _resolve_speaker(self, words):
        """Assign speaker number with 1s stability buffer; default to Speaker 1."""
        raw_speaker = None
        for word in words or []:
            if word.get("speaker") is not None:
                raw_speaker = int(word["speaker"])
                break

        candidate = (raw_speaker + 1) if raw_speaker is not None else None
        now = time.time()

        with self._speaker_lock:
            if candidate is None:
                return self._last_assigned_speaker

            if candidate != self._last_assigned_speaker:
                if now - self._last_speaker_change_time >= 1.0:
                    self._last_assigned_speaker = candidate
                    self._last_speaker_change_time = now
            return self._last_assigned_speaker

    def _audio_callback(self, indata, _frames, _time, status):
        """sounddevice callback — enqueue raw PCM (already 16 kHz mono)."""
        if status:
            print(status)
        if self._audio_q is not None and not self._stop_event.is_set():
            self._enqueue_pcm_for_deepgram(bytes(indata))

    def _deepgram_on_message(self, _ws, message):
        """Handle Deepgram transcript messages (background thread only)."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "unknown")

            if msg_type == "Results":
                is_final = data.get("is_final", False)
                channel = data.get("channel", {})
                alternatives = channel.get("alternatives", [])
                if not alternatives:
                    print(f"Deepgram Results (final={is_final}): no alternatives")
                    return

                alt = alternatives[0]
                transcript = alt.get("transcript", "").strip()
                if not transcript:
                    return

                words = alt.get("words") or []
                speaker_num = self._resolve_speaker(words)
                kind = "final" if is_final else "interim"
                if is_final:
                    print(f"Received final transcript: {transcript} (Speaker {speaker_num})")
                self.ui_queue.put(
                    {"kind": kind, "speaker": speaker_num, "text": transcript}
                )
            else:
                print(f"Deepgram message type: {msg_type}")
        except Exception as exc:
            print(f"Error parsing Deepgram message: {exc}")
            print(f"Raw message: {message[:200]}...")

    def _deepgram_on_open(self, ws):
        """Start streaming queued audio to Deepgram when the socket opens."""
        print("Connecting to Deepgram...")

        def stream_audio():
            chunks_sent = 0
            silence = np.zeros(AUDIO_BLOCKSIZE, dtype=np.int16).tobytes()
            last_send = time.perf_counter()
            while not self._stop_event.is_set():
                try:
                    chunk = self._audio_q.get(timeout=0.1)
                except queue.Empty:
                    if time.perf_counter() - last_send >= 1.0:
                        chunk = silence
                    else:
                        continue
                try:
                    ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                    chunks_sent += 1
                    last_send = time.perf_counter()
                    if chunks_sent == 1:
                        print(
                            f"First audio chunk sent to Deepgram ({len(chunk)} bytes)"
                        )
                except Exception as exc:
                    print(f"Error sending audio to Deepgram: {exc}")
                    break

        threading.Thread(target=stream_audio, daemon=True).start()

    def _deepgram_worker(self):
        """Run the Deepgram WebSocket connection in a background thread."""
        url = self._build_deepgram_url()
        print(f"Deepgram URL: {url}")
        try:
            ws = websocket.WebSocketApp(
                url,
                header={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
                on_message=self._deepgram_on_message,
                on_open=self._deepgram_on_open,
                on_error=lambda _ws, err: print(f"Deepgram WebSocket error: {err}"),
                on_close=lambda _ws, code, msg: print(f"Deepgram closed: {code} {msg}"),
            )
            self._dg_ws = ws
            ws.run_forever()
        except Exception as exc:
            print(f"Deepgram connection error: {exc}")

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
        """Open audio stream and Deepgram WebSocket."""
        dropdown_lang = self.source_language.get()
        deepgram_lang = LANGUAGE_MAP.get(dropdown_lang, "en")
        self._listen_language = deepgram_lang
        print(
            f"Listening to dropdown: '{dropdown_lang}' -> Deepgram code: '{deepgram_lang}'"
        )

        audio_source = self.audio_source.get()
        print(f"Audio source selected: '{audio_source}'")

        try:
            self._stop_event.clear()
            self._audio_q = queue.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
            self._raw_audio_q = queue.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
            with self._speaker_lock:
                self._last_assigned_speaker = 1
                self._last_speaker_change_time = 0.0
            self._ui_last_speaker = None

            print("Starting audio capture...")
            try:
                if self._is_system_audio_source(audio_source):
                    self._start_wasapi_loopback()
                else:
                    self._start_microphone_capture()
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
            print(f"Listening started (language={self._listen_language})")
        except Exception as exc:
            print(f"Error starting listening: {exc}")
            self._stop_listening()
            if not self._is_system_audio_source(audio_source):
                messagebox.showerror("Error", f"Could not start listening:\n{exc}")

    def _stop_listening(self):
        """Close audio stream and Deepgram WebSocket."""
        try:
            self._stop_event.set()

            if self._audio_stream is not None:
                self._audio_stream.stop()
                self._audio_stream.close()
                self._audio_stream = None

            self._close_wasapi_stream()

            if self._dg_ws is not None:
                try:
                    self._dg_ws.close()
                except Exception:
                    pass
                self._dg_ws = None

            self._audio_q = None
            self._raw_audio_q = None
            self.is_listening = False
            self._set_listen_button_state(False)
            print("Listening stopped")
        except Exception as exc:
            print(f"Error stopping listening: {exc}")

    def process_ui_queue(self):
        """Drain transcript queue on the main UI thread every 100ms."""
        try:
            pending = []
            while True:
                pending.append(self.ui_queue.get_nowait())
        except queue.Empty:
            pass

        if pending:
            latest_interim = None
            to_render = []
            for item in pending:
                if isinstance(item, dict) and item.get("kind") == "interim":
                    latest_interim = item
                else:
                    if latest_interim is not None:
                        to_render.append(latest_interim)
                        latest_interim = None
                    to_render.append(item)
            if latest_interim is not None:
                to_render.append(latest_interim)

            for item in to_render:
                if isinstance(item, dict):
                    self._handle_transcript_ui(item)
                else:
                    self._append_initial_transcript(item)

        self.after(100, self.process_ui_queue)

    def _handle_transcript_ui(self, item):
        """Update Initial verse with interim (gray) or final (colored) transcript."""
        box = self.initial_verse_box
        if box is None:
            return

        kind = item.get("kind")
        speaker = item.get("speaker", 1)
        text = item.get("text", "").strip()
        if not text:
            return

        box.configure(state="normal")
        speaker_tag = f"speaker_{speaker}" if 1 <= speaker <= 4 else "body"
        label = f"[Speaker {speaker}] "

        if kind == "interim":
            if "interim_live" in box.mark_names():
                box.delete("interim_live", tk.END)
            else:
                box.mark_set("interim_live", tk.END)
            box.insert(tk.END, label, speaker_tag)
            box.insert(tk.END, text, "interim")
        elif kind == "final":
            if "interim_live" in box.mark_names():
                box.delete("interim_live", tk.END)

            if self._ui_last_speaker is not None and speaker != self._ui_last_speaker:
                content = box.get("1.0", tk.END)
                if content.strip() and not content.endswith("\n"):
                    box.insert(tk.END, "\n")

            box.insert(tk.END, label, speaker_tag)
            box.insert(tk.END, text + "\n", "body")
            self._ui_last_speaker = speaker

        box.configure(state="disabled")
        self.check_scrollbar_visibility(box, box._scrollbar)
        box.see(tk.END)

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
            self._ui_last_speaker = None
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
    app = AlphaApp()
    app.mainloop()
