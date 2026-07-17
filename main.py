"""
Alpha - Live Meeting Translation Desktop Application (UI Stage 1)
Responsive header with hamburger menu for compact layouts.
"""

import os
from pathlib import Path

import customtkinter as ctk
import tkinter as tk
from PIL import Image
from tkinter import Menu, messagebox

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
COMPACT_BREAKPOINT = 600

# Deepgram API key (env var overrides this default when set)
DEEPGRAM_API_KEY = os.getenv(
    "DEEPGRAM_API_KEY",
    "77dc0b90793074722dcae7875198e9cb24a68838",
)


class AlphaApp(ctk.CTk):
    """Main application window for Alpha live meeting translation."""

    def __init__(self):
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        super().__init__()

        self._compact_mode = None
        self._menu_visible = False
        self._pane_initialized = False
        self.logo_image = None

        self.source_language = ctk.StringVar(value="English")
        self.target_language = ctk.StringVar(value="Spanish")
        self.source_language.trace_add(
            "write", lambda *_: self.on_language_change("source")
        )
        self.target_language.trace_add(
            "write", lambda *_: self.on_language_change("target")
        )

        self.initial_verse_box = None
        self.translated_verse_box = None
        self.always_on_top_switch = None
        self.always_on_top_switch_menu = None
        self.paned = None
        self.normal_header_widgets = []

        self.setup_window()
        self.create_solid_background()
        self.create_header_frame()
        self.create_hamburger_menu()
        self.create_main_content()
        self._create_context_menu()
        self.bind_resize_event()

        self.after(100, self.toggle_header_layout)
        self.after(150, self._set_initial_pane_ratio)

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

        top_pane = self._create_verse_section(
            master=self.paned,
            title="Initial verse",
            font_size=12,
            text_color=COLORS["text_secondary"],
            attr_name="initial_verse_box",
        )
        bottom_pane = self._create_verse_section(
            master=self.paned,
            title="Translated verse",
            font_size=16,
            text_color=COLORS["text_primary"],
            attr_name="translated_verse_box",
        )

        self.paned.add(top_pane, minsize=80, stretch="always")
        self.paned.add(bottom_pane, minsize=120, stretch="always")

    def _create_verse_section(self, master, title, font_size, text_color, attr_name):
        """Build a labeled container with a styled text box for a PanedWindow pane."""
        outer = ctk.CTkFrame(
            master=master,
            fg_color=COLORS["header_bg"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"],
        )
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            master=outer,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        title_label.grid(row=0, column=0, sticky="w", padx=15, pady=(15, 8))

        textbox = ctk.CTkTextbox(
            master=outer,
            font=ctk.CTkFont(family="Segoe UI", size=font_size),
            text_color=text_color,
            fg_color=COLORS["header_bg"],
            border_width=0,
            corner_radius=8,
            wrap="word",
            activate_scrollbars=True,
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["dropdown_bg"],
        )
        textbox.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        textbox.configure(state="normal")

        setattr(self, attr_name, textbox)
        return outer

    def _set_initial_pane_ratio(self):
        """Set the PanedWindow sash to a 35% / 65% starting split."""
        if self._pane_initialized or self.paned is None:
            return

        try:
            self.update_idletasks()
            height = self.paned.winfo_height()
            if height <= 2:
                self.after(50, self._set_initial_pane_ratio)
                return

            self.paned.sash_place(0, 0, int(height * 0.35))
            self._pane_initialized = True
        except Exception as exc:
            print(f"Error setting initial pane sizes: {exc}")

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
                print(f"Listening to: {self.source_language.get()}")
            if changed in ("target", "both"):
                print(f"Translate to: {self.target_language.get()}")
        except Exception as exc:
            print(f"Error updating language selection: {exc}")

    def clear_text(self):
        """Clear both the initial and translated verse text boxes."""
        try:
            for box in (self.initial_verse_box, self.translated_verse_box):
                box.configure(state="normal")
                box.delete("1.0", "end")
            print("Text boxes cleared.")
        except Exception as exc:
            print(f"Error clearing text: {exc}")
            messagebox.showerror("Error", f"Could not clear text:\n{exc}")


if __name__ == "__main__":
    app = AlphaApp()
    app.mainloop()
