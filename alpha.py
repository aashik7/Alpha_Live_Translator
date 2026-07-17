"""
Alpha - Live Meeting Translation Desktop Application (UI Stage 1)
Professional dark-themed interface built with CustomTkinter.
"""

import customtkinter as ctk
from tkinter import Canvas, Menu, messagebox

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


class AlphaApp(ctk.CTk):
    """Main application window for Alpha live meeting translation."""

    def __init__(self):
        # Global CustomTkinter appearance
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        super().__init__()

        # -------------------------------------------------------------------
        # Main window setup
        # -------------------------------------------------------------------
        self.title("Alpha")
        self.geometry("1400x800")
        self.minsize(900, 600)
        self.configure(fg_color=COLORS["main_bg"])

        # Language selection state
        self.source_language = ctk.StringVar(value="English")
        self.target_language = ctk.StringVar(value="Spanish")
        self.source_language.trace_add(
            "write", lambda *_: self.on_language_change("source")
        )
        self.target_language.trace_add(
            "write", lambda *_: self.on_language_change("target")
        )

        # Widget references populated during layout
        self.initial_verse_box = None
        self.translated_verse_box = None
        self.always_on_top_switch = None

        # Build UI sections
        self._create_header()
        self._create_main_content()
        self._create_context_menu()

    # -----------------------------------------------------------------------
    # Header section (70px top bar)
    # -----------------------------------------------------------------------
    def _create_header(self):
        """Create the top header with logo, language controls, and actions."""
        header = ctk.CTkFrame(
            self,
            height=70,
            fg_color=COLORS["header_bg"],
            corner_radius=0,
            border_width=0,
        )
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        # Left: logo + brand name
        left_frame = ctk.CTkFrame(header, fg_color="transparent")
        left_frame.pack(side="left", padx=20, pady=15)

        logo_canvas = self.create_logo(left_frame)
        logo_canvas.pack(side="left", padx=(0, 12))

        brand_label = ctk.CTkLabel(
            left_frame,
            text="Alpha",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        brand_label.pack(side="left")

        # Center-right: language dropdowns
        lang_frame = ctk.CTkFrame(header, fg_color="transparent")
        lang_frame.pack(side="right", padx=(0, 10))

        listening_label = ctk.CTkLabel(
            lang_frame,
            text="Listening to:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
        )
        listening_label.grid(row=0, column=0, padx=(0, 8), pady=5)

        self.source_combo = ctk.CTkComboBox(
            lang_frame,
            values=SOURCE_LANGUAGES,
            variable=self.source_language,
            width=140,
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
        self.source_combo.grid(row=0, column=1, padx=(0, 20), pady=5)

        translate_label = ctk.CTkLabel(
            lang_frame,
            text="Translate to:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
        )
        translate_label.grid(row=0, column=2, padx=(0, 8), pady=5)

        self.target_combo = ctk.CTkComboBox(
            lang_frame,
            values=TARGET_LANGUAGES,
            variable=self.target_language,
            width=140,
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
        self.target_combo.grid(row=0, column=3, padx=(0, 20), pady=5)

        # Far right: action buttons
        actions_frame = ctk.CTkFrame(header, fg_color="transparent")
        actions_frame.pack(side="right", padx=20, pady=15)

        clear_button = ctk.CTkButton(
            actions_frame,
            text="Clear",
            width=70,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color=COLORS["dropdown_bg"],
            hover_color="#4d4d5d",
            text_color=COLORS["text_primary"],
            corner_radius=8,
            command=self.clear_text,
        )
        clear_button.pack(side="left", padx=(0, 12))

        summary_button = ctk.CTkButton(
            actions_frame,
            text="Meeting Summary",
            width=150,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["accent_blue"],
            hover_color="#3a8eef",
            text_color=COLORS["text_primary"],
            corner_radius=8,
            command=self.show_meeting_summary,
        )
        summary_button.pack(side="left", padx=(0, 16))

        self.always_on_top_switch = ctk.CTkSwitch(
            actions_frame,
            text="Always on Top",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["dropdown_bg"],
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["text_primary"],
            button_hover_color="#e0e0e0",
            command=self.toggle_always_on_top,
        )
        self.always_on_top_switch.pack(side="left")

    def create_logo(self, parent):
        """
        Draw a geometric 'A' logo using tkinter Canvas.
        Blue gradient triangle/arrow shape, approximately 40x40 pixels.
        """
        canvas = Canvas(
            parent,
            width=40,
            height=40,
            bg=COLORS["header_bg"],
            highlightthickness=0,
            bd=0,
        )

        # Gradient effect using layered blue polygons (left to right)
        canvas.create_polygon(
            4, 36, 20, 4, 20, 36,
            fill="#2d7dd2",
            outline="",
        )
        canvas.create_polygon(
            20, 4, 36, 36, 20, 36,
            fill="#4a9eff",
            outline="",
        )

        # Inner cutout to form the 'A' letter shape
        canvas.create_polygon(
            13, 28, 20, 12, 27, 28,
            fill=COLORS["header_bg"],
            outline="",
        )

        # Crossbar of the 'A'
        canvas.create_rectangle(
            15, 22, 25, 25,
            fill="#4a9eff",
            outline="",
        )

        return canvas

    # -----------------------------------------------------------------------
    # Main content area (35% initial verse / 65% translated verse)
    # -----------------------------------------------------------------------
    def _create_main_content(self):
        """Create the vertically split main content panels."""
        content = ctk.CTkFrame(self, fg_color=COLORS["main_bg"], corner_radius=0)
        content.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        content.grid_rowconfigure(0, weight=35)
        content.grid_rowconfigure(1, weight=65)
        content.grid_columnconfigure(0, weight=1)

        self._create_verse_section(
            parent=content,
            row=0,
            title="Initial verse",
            pady=(0, 8),
            font_size=12,
            text_color=COLORS["text_secondary"],
            attr_name="initial_verse_box",
        )

        self._create_verse_section(
            parent=content,
            row=1,
            title="Translated verse",
            pady=(8, 0),
            font_size=16,
            text_color=COLORS["text_primary"],
            attr_name="translated_verse_box",
        )

    def _create_verse_section(
        self,
        parent,
        row,
        title,
        pady,
        font_size,
        text_color,
        attr_name,
    ):
        """Build a labeled container with a styled text box."""
        outer = ctk.CTkFrame(
            parent,
            fg_color=COLORS["header_bg"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"],
        )
        outer.grid(row=row, column=0, sticky="nsew", pady=pady)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            outer,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        title_label.grid(row=0, column=0, sticky="w", padx=15, pady=(15, 8))

        textbox = ctk.CTkTextbox(
            outer,
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
        """Toggle window always-on-top behavior via the switch."""
        try:
            is_on = self.always_on_top_switch.get() == 1
            self.attributes("-topmost", is_on)
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
