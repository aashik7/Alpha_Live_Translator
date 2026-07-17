"""Modern design system for Alpha Live Translator (V3.1 UI Recovery)."""

# ---------------------------------------------------------------------------
# Color tokens
# ---------------------------------------------------------------------------
APP_BG = "#07111F"
HEADER_BG = "#07111F"
PANEL_BG = "#0B1220"
CARD_BG = "#101827"
CARD_BG_SOFT = "#142036"
INPUT_BG = "#111827"
INPUT_BG_HOVER = "#182236"
BORDER = "#26354F"
BORDER_SOFT = "#1E2A40"
ACCENT_BLUE = "#3B82F6"
ACCENT_BLUE_HOVER = "#2563EB"
ACCENT_RED = "#EF4444"
ACCENT_RED_HOVER = "#DC2626"
ACCENT_GREEN = "#22C55E"
TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#CBD5E1"
TEXT_MUTED = "#64748B"
TEXT_DISABLED = "#475569"

COLORS = {
    "app_bg": APP_BG,
    "header_bg": HEADER_BG,
    "panel_bg": PANEL_BG,
    "card_bg": CARD_BG,
    "card_bg_soft": CARD_BG_SOFT,
    "input_bg": INPUT_BG,
    "input_bg_hover": INPUT_BG_HOVER,
    "border": BORDER,
    "border_soft": BORDER_SOFT,
    "accent_blue": ACCENT_BLUE,
    "accent_blue_hover": ACCENT_BLUE_HOVER,
    "accent_green": ACCENT_GREEN,
    "accent_red": ACCENT_RED,
    "accent_red_hover": ACCENT_RED_HOVER,
    "accent_red_soft": "#7F1D1D",
    "accent_red_glow": "#F87171",
    "text_primary": TEXT_PRIMARY,
    "text_secondary": TEXT_SECONDARY,
    "text_muted": TEXT_MUTED,
    "text_disabled": TEXT_DISABLED,
    "placeholder": TEXT_DISABLED,
    "waveform_bar": "#60A5FA",
    "waveform_bar_mid": "#3B82F6",
    "waveform_bar_dim": "#1E3A5F",
    "live_glow": ACCENT_RED,
    "live_idle": TEXT_DISABLED,
    "status_active_bg": INPUT_BG,
    "status_chip_bg": INPUT_BG,
    "status_chip_hover": INPUT_BG_HOVER,
    # Legacy aliases (backend / shared widgets)
    "main_bg": APP_BG,
    "container_bg": CARD_BG,
    "dropdown_bg": INPUT_BG,
}

# Dark glass header / secondary buttons (V3.1 recovery)
GLASS_BUTTON_FG = CARD_BG
GLASS_BUTTON_HOVER = INPUT_BG_HOVER
GLASS_BUTTON_BORDER = BORDER
GLASS_BUTTON_TEXT = TEXT_PRIMARY

COLORS["glass_button_fg"] = GLASS_BUTTON_FG
COLORS["glass_button_hover"] = GLASS_BUTTON_HOVER
COLORS["glass_button_border"] = GLASS_BUTTON_BORDER
COLORS["glass_button_text"] = GLASS_BUTTON_TEXT

# Legacy ghost aliases (backward compatible imports)
GHOST_BUTTON_FG = GLASS_BUTTON_FG
GHOST_BUTTON_HOVER = GLASS_BUTTON_HOVER
GHOST_BUTTON_BORDER = GLASS_BUTTON_BORDER
GHOST_BUTTON_TEXT = GLASS_BUTTON_TEXT
COLORS["ghost_button_fg"] = GHOST_BUTTON_FG
COLORS["ghost_button_hover"] = GHOST_BUTTON_HOVER
COLORS["ghost_button_border"] = GHOST_BUTTON_BORDER
COLORS["ghost_button_text"] = GHOST_BUTTON_TEXT

SPEAKER_COLORS = {
    "speaker_1": "#3B82F6",
    "speaker_2": "#22C55E",
    "speaker_3": "#F59E0B",
    "speaker_4": "#EF4444",
}

EXTENDED_SPEAKER_COLORS = {
    1: "#3B82F6",
    2: "#22C55E",
    3: "#F59E0B",
    4: "#EF4444",
    5: "#9b59b6",
    6: "#1abc9c",
    7: "#e74c3c",
    8: "#3498db",
    9: "#f39c12",
    10: "#2ecc71",
    11: "#95a5a6",
    12: "#e91e63",
}

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI Variable"
FONT_FAMILY_FALLBACK = "Segoe UI"

APP_TITLE_FONT = (FONT_FAMILY, 25, "bold")
APP_SUBTITLE_FONT = (FONT_FAMILY, 12)
SECTION_TITLE_FONT = (FONT_FAMILY, 15, "bold")
TRANSCRIPT_BODY_FONT = (FONT_FAMILY, 16)
TRANSCRIPT_SPEAKER_FONT = (FONT_FAMILY, 14, "bold")
TRANSLATION_BODY_FONT = (FONT_FAMILY, 16)
BODY_FONT = (FONT_FAMILY, 13)
BODY_LARGE_FONT = (FONT_FAMILY, 16)
BUTTON_FONT = (FONT_FAMILY, 13, "bold")
SMALL_FONT = (FONT_FAMILY, 11)
PLACEHOLDER_FONT = (FONT_FAMILY, 13, "italic")
TIMESTAMP_FONT = (FONT_FAMILY, 12)

FONTS = {
    "brand": APP_TITLE_FONT,
    "brand_sub": APP_SUBTITLE_FONT,
    "brand_compact": (FONT_FAMILY, 18, "bold"),
    "section_title": SECTION_TITLE_FONT,
    "section_title_sm": SECTION_TITLE_FONT,
    "body": BODY_FONT,
    "body_lg": BODY_LARGE_FONT,
    "transcript_body": TRANSCRIPT_BODY_FONT,
    "transcript_speaker": TRANSCRIPT_SPEAKER_FONT,
    "translation_body": TRANSLATION_BODY_FONT,
    "caption": SMALL_FONT,
    "button": BUTTON_FONT,
    "button_secondary": (FONT_FAMILY, 13),
    "button_compact": (FONT_FAMILY, 12, "bold"),
    "status": (FONT_FAMILY, 12, "bold"),
    "timer": (FONT_FAMILY, 13, "bold"),
    "placeholder": PLACEHOLDER_FONT,
    "placeholder_lg": (FONT_FAMILY, 15, "italic"),
    "timestamp": TIMESTAMP_FONT,
}

# ---------------------------------------------------------------------------
# Spacing
# ---------------------------------------------------------------------------
OUTER_PADDING = 20
OUTER_PADDING_COMPACT = 10
SECTION_GAP = 16
CARD_PADDING = 18
CARD_PADDING_COMPACT = 12
BUTTON_GAP = 12

SPACING = {
    "window_pad_x": OUTER_PADDING,
    "window_pad_compact_x": OUTER_PADDING_COMPACT,
    "window_pad_y": 16,
    "card_pad": CARD_PADDING,
    "card_pad_compact": CARD_PADDING_COMPACT,
    "section_gap": SECTION_GAP,
    "section_gap_compact": 10,
    "footer_btn_gap": BUTTON_GAP,
    "footer_btn_gap_compact": 8,
    "footer_pad_y": 14,
    "footer_pad_y_compact": 10,
}

# ---------------------------------------------------------------------------
# Radii
# ---------------------------------------------------------------------------
CARD_RADIUS = 14
BUTTON_RADIUS = 12
GLASS_BUTTON_RADIUS = 12
INPUT_RADIUS = 10
CHIP_RADIUS = 10

RADII = {
    "card": CARD_RADIUS,
    "button": BUTTON_RADIUS,
    "glass_button": GLASS_BUTTON_RADIUS,
    "combo": INPUT_RADIUS,
    "chip": CHIP_RADIUS,
    "pill": 20,
    "status_pill": CHIP_RADIUS,
}

# ---------------------------------------------------------------------------
# Control sizes
# ---------------------------------------------------------------------------
HEADER_CONTROL_HEIGHT = 42
FOOTER_BUTTON_HEIGHT = 42
SMALL_BUTTON_HEIGHT = 34
DROPDOWN_WIDTH = 155
DROPDOWN_HEIGHT = 42
DROPDOWN_BORDER_WIDTH = 0.5
DROPDOWN_WRAPPER_BORDER_WIDTH = 1
DROPDOWN_INNER_BORDER_WIDTH = 0
SWAP_BUTTON_SIZE = 42
SUMMARY_BUTTON_WIDTH = 170
FOOTER_PRIMARY_WIDTH = 155
FOOTER_ACTION_WIDTH = 125

FOOTER_BTN_HEIGHT = FOOTER_BUTTON_HEIGHT
FOOTER_BTN_WIDTH = FOOTER_PRIMARY_WIDTH
FOOTER_BTN_WIDTH_SECONDARY = FOOTER_ACTION_WIDTH
FOOTER_BTN_WIDTH_COMPACT = 88
FOOTER_BTN_HEIGHT_COMPACT = 36

# Visual-only waveform (not tied to real audio)
WAVEFORM_BAR_COUNT = 14
WAVEFORM_BAR_COUNT_WIDE = 35
WAVEFORM_ANIMATION_MS = 400
WAVEFORM_CANVAS_WIDTH = 140
WAVEFORM_CANVAS_WIDTH_WIDE = 285
WAVEFORM_CANVAS_HEIGHT = 30

# Empty-state copy
PLACEHOLDER_TRANSCRIPT = "Live meeting transcript will appear here..."
PLACEHOLDER_TRANSLATION = "This feature is coming soon."
PLACEHOLDER_SUMMARY = (
    "Meeting summary will appear here after summarization is enabled in a later version."
)

# Responsive layout breakpoints (window width in px)
LAYOUT_WIDE_BREAKPOINT = 1050
LAYOUT_MEDIUM_BREAKPOINT = 700
LAYOUT_HAMBURGER_BREAKPOINT = 800
LAYOUT_MIN_WIDTH = 400
LAYOUT_MIN_HEIGHT = 650
LAYOUT_STATUS_COMPACT_BREAKPOINT = 520
LAYOUT_FOOTER_WRAP_BREAKPOINT = 680

# Section / summary labels (icons always left of text)
SUMMARY_TITLE = "Meeting Summary"
SUMMARY_PANEL_TITLE = "Meeting Summary"
MEETING_SUMMARY_BUTTON_TEXT = "Meeting Summary"
SUMMARY_CLOSE_ICON = "×"
SECTION_TRANSCRIPT_TITLE = "Live Transcript"
SECTION_TRANSLATION_TITLE = "Translation"
TRANSLATION_TITLE_ICON = ""

# Legacy summary labels (kept for compatibility)
SUMMARY_EXPANDED_LABEL = f"{SUMMARY_TITLE} ▲"
SUMMARY_COLLAPSED_LABEL = f"{SUMMARY_TITLE} ▼"
SUMMARY_COMPACT_EXPANDED_LABEL = "Meeting Summary ▲"
SUMMARY_COMPACT_COLLAPSED_LABEL = "Meeting Summary ▼"
SUMMARY_CHIP_HEIGHT = 30
SUMMARY_CHIP_FONT = SMALL_FONT
SUMMARY_CHEVRON_EXPANDED = "˄"
SUMMARY_CHEVRON_COLLAPSED = "˅"
SUMMARY_PANEL_HEIGHT = 190
SUMMARY_ANIMATION_STEP_MS = 16

# Legacy alias used by older responsive helpers
CONTENT_COMPACT_BREAKPOINT = LAYOUT_WIDE_BREAKPOINT
RIGHT_COLUMN_MIN_WIDTH = 220
