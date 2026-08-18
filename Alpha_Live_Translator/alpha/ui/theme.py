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
    # ---- Item 71 / UI redesign Phase 1: design tokens -------------------
    # Additive only. Nothing above changes value, so any widget not opting in
    # renders exactly as before. The design was derived from this file, so the
    # existing tokens already match it hex-for-hex.
    "pane_translation_bg": "#0D1728",  # primary (translation) pane
    "pane_original_bg": "#091320",  # reference (transcript) pane
    "button_border": "#31415C",
    "toggle_border": "#3B4B67",
    "toast_bg": "#152238",
    # ACCENT_GREEN #22C55E is visibly darker than the design's success text --
    # do not substitute one for the other.
    "success_text": "#86EFAC",
    "speaker_two_dot": "#4ADE80",
    "label_muted": "#94A3B8",
    "button_text": "#E2E8F0",
    "scrollbar_thumb": "#1B4B7A",
    "scrollbar_track": "#050D19",
    "summary_body_text": "#DBE5F2",
    "toggle_on_bg": "#1D4ED8",
    # The design expresses these as rgba() over an opaque backdrop. Tk has no
    # alpha, so they are pre-composited to flat hex:
    #   rgba(59,130,246,.09) over #0D1728 -> #11213B
    #   rgba(59,130,246,.06) over #091320 -> #0C1A2D
    #   rgba(2,8,23,.72)     over #0B1220 -> #050B1A
    "entry_current_bg": "#11213B",
    "entry_current_bg_original": "#0C1A2D",
    "overlay_backdrop": "#050B1A",
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
# Item 71 / UI redesign Phase 1. This was "Segoe UI Variable", which Tk cannot
# resolve: `tkfont.Font(family="Segoe UI Variable").actual("family")` returns
# **Arial**, and Tk substitutes silently rather than raising, so no fallback
# ever fired and the whole app rendered in Arial. Re-verified on this machine:
#
#   Segoe UI Variable -> 'Arial'      Segoe UI -> 'Segoe UI'
#
# "Segoe UI" is registered and is what the design specifies (its stack is
# "Segoe UI Variable", "Segoe UI", "Yu Gothic UI", sans-serif -- the browser
# falls through, Tk does not).
#
# Safe despite touching every rendered string: a font change alters WRAP
# points, and wrapping produces *display* lines. Every piece of arithmetic in
# main_window.py -- `delete("1.0", "2.0")`, `mark lineend` -- operates on
# *logical* lines, which are unchanged.
FONT_FAMILY = "Segoe UI"
FONT_FAMILY_FALLBACK = "Yu Gothic UI"

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

# ---------------------------------------------------------------------------
# Item 71 / UI redesign Phase 2: reading typography
# ---------------------------------------------------------------------------
# Every size below is a design px, taken verbatim from the CSS. No unit
# conversion is needed, and doing one would be wrong: a CustomTkinter font size
# is already **pixels**, not points. Measured on 5.2.2 --
# `CTkFont(family="Segoe UI", size=14)` and `tkinter.font.Font(size=-14)`
# produce byte-identical metrics (actual size 11 pt, linespace 19). So the
# design's `font-size: 18px` is `size=18`.
#
# What DOES need converting is display scaling, and only for these two panes.
# CustomTkinter scales its own widgets by `ScalingTracker.get_widget_scaling`,
# but a raw `tk.Text` is not a CTk widget and receives none of it: on a 150%
# display the same CTkFont object measures 18 pt inside a CTkLabel and 12 pt
# inside the tk.Text. That is applied at runtime -- see `_design_px` in
# main_window.py -- so the reading panes track the chrome around them.
#
# All of this is wrap-only. `spacing1/2/3`, `lmargin1/2`, `rmargin`, `font` and
# `background` add ZERO logical lines, so `delete("1.0", "2.0")` and every
# `mark lineend` arithmetic in main_window.py is untouched.
#
# Keys are (pane role, stacked). `stacked` mirrors the design's
# `@media (max-width: 700px)` branch, which is the same threshold the layout
# uses to turn the reading columns into rows.
#
# `line_height` is the design's CSS ratio, not a pixel count: the actual extra
# inter-line space depends on the font's measured linespace at the current DPI,
# so it is resolved at runtime rather than baked in here.
READING_TYPOGRAPHY = {
    # .atf-translation-entry p { font-size: 18px; line-height: 1.58 }
    # .atf-translation-entry  { padding: 16px 0 }
    # .atf-translation-content { padding: 5px 20px 18px }
    ("translation", False): {
        "font_px": 18,
        "line_height": 1.58,
        "space_above_px": 16,
        "space_below_px": 16,
        "pad_x_px": 20,
        "pad_top_px": 5,
        "pad_bottom_px": 18,
    },
    # .atf-mobile-preview .atf-translation-entry p { font-size: 17px }
    # .atf-mobile-preview .atf-translation-content { padding: 4px 14px 14px }
    ("translation", True): {
        "font_px": 17,
        "line_height": 1.58,
        "space_above_px": 16,
        "space_below_px": 16,
        "pad_x_px": 14,
        "pad_top_px": 4,
        "pad_bottom_px": 14,
    },
    # .atf-original-entry p { font-size: 14px; line-height: 1.55 }
    # .atf-original-entry   { padding: 14px 0 }
    # .atf-original-content { padding: 5px 15px 16px }
    ("transcript", False): {
        "font_px": 14,
        "line_height": 1.55,
        "space_above_px": 14,
        "space_below_px": 14,
        "pad_x_px": 15,
        "pad_top_px": 5,
        "pad_bottom_px": 16,
    },
    # The design gives the original pane no mobile font override, only tighter
    # padding: .atf-mobile-preview .atf-original-content { padding: 4px 13px 13px }
    ("transcript", True): {
        "font_px": 14,
        "line_height": 1.55,
        "space_above_px": 14,
        "space_below_px": 14,
        "pad_x_px": 13,
        "pad_top_px": 4,
        "pad_bottom_px": 13,
    },
}

# .atf-incoming-entry { margin-top: 8px; padding: 13px 0; color: #64748b;
#                       font-size: 12px }
INTERIM_FONT_PX = 12
INTERIM_SPACE_ABOVE_PX = 8 + 13
INTERIM_SPACE_BELOW_PX = 13

# .atf-entry-meta { font-size: 11px } / .atf-entry-meta strong { font-weight: 500 }
# The design puts the speaker name on its own meta row above the body; that
# needs a second logical line and therefore the render-cap fix first, so for
# now the label keeps its inline position and only takes the design's size.
SPEAKER_LABEL_FONT_PX = 11

# Pane backgrounds. The design tints the two panes differently so the primary
# one reads as the foreground surface; both tokens were added in Phase 1.
PANE_BG = {
    "translation": COLORS["pane_translation_bg"],
    "transcript": COLORS["pane_original_bg"],
}

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

# ---------------------------------------------------------------------------
# Item 71 Phase 3b: footer, from the design document
# ---------------------------------------------------------------------------
# The design sizes footer buttons by their PADDING, not by a fixed pixel width:
#
#   .atf-stop-button        { min-height: 40px; padding: 0 16px }
#   .atf-action-group button { min-height: 36px; padding: 0 12px }
#   .atf-footer             { justify-content: space-between; gap: 12px;
#                             min-height: 64px; padding: 11px 16px }
#   .atf-listening-group, .atf-action-group { gap: 8px }
#
# Fixed widths are what broke it. Measured on a 150% display, the rendered
# button font is 20 px, so "Start Listening" needs 138 px of glyphs plus
# padding = 162 px, while FOOTER_BTN_WIDTH_COMPACT (88) buys 132 px. Below
# 500 px the code applied that 88 to the PRIMARY button and the label was cut
# to "Start Listen". The same arithmetic clipped "Copy Translation" (needs 184)
# and even "Meeting Summary" (needs 198) against FOOTER_BTN_WIDTH_SECONDARY's
# 188. Deriving the width from the text removes the whole class of defect and
# survives any font, label or display-scaling change.
FOOTER_BTN_PAD_X = 16  # design px, each side, .atf-stop-button
FOOTER_ACTION_PAD_X = 12  # design px, each side, .atf-action-group button
FOOTER_ACTION_HEIGHT = 36  # design px, .atf-action-group button min-height
FOOTER_PRIMARY_HEIGHT = 40  # design px, .atf-stop-button min-height
FOOTER_GROUP_GAP = 8  # design px, gap inside a group
FOOTER_ROW_GAP = 12  # design px, .atf-footer gap between the two groups
FOOTER_PAD_X = 16  # design px, .atf-footer padding
FOOTER_PAD_Y = 11
FOOTER_PAD_X_STACKED = 12  # design px, the @media 700 override
FOOTER_PAD_Y_STACKED = 10

# The design's own two footer breakpoints, and what they actually mean.
#
# `@media (max-width: 700px)` gives
# `.atf-listening-group, .atf-action-group { flex: 1 1 100% }`. An earlier
# revision read that as "each group takes a full row" and stacked them. That
# was WRONG, and the correction matters because it changes the layout: the
# whole file contains `flex-wrap` on exactly four selectors -- `.atf-action-group`,
# `.atf-reading-toolbar` twice, and one other -- and `.atf-footer` is NOT among
# them, so the footer computes `flex-wrap: nowrap`. Two children with
# `flex-basis: 100%` in a nowrap container cannot wrap; they shrink against each
# other and settle at **50/50 on one row**.
#
# The wrapping happens one level down: `.atf-action-group` DOES carry
# `flex-wrap: wrap`, so the action buttons flow onto extra lines *inside* their
# half when they do not fit. `align-items: stretch` on the footer then makes
# the start/stop side as tall as whatever the action side needed.
#
# Same 700 the reading grid stacks at, so the window changes shape at one
# threshold instead of the three (680 / 700 / 800) it used to use.
FOOTER_STACK_BREAKPOINT = 700
# `@media (max-width: 430px)` gives `.atf-action-group button { flex: 1 1 auto }`
# -- the action buttons stretch to share the row equally.
FOOTER_ACTIONS_STRETCH_BREAKPOINT = 430

# Every label the primary button can display. It is sized for the widest of
# them once, so starting or stopping a session does not make the button jump
# width mid-meeting. Keep this in sync with `_set_listen_button_state` and the
# "Starting…" transitional state.
LISTEN_BUTTON_LABELS = ("Start Listening", "Stop Listening", "Starting…")

# The width the window is created at, in design px. Used as the responsive
# fallback before the first paint, when `winfo_width()` still reports 1 and a
# raw reading of it would stack the whole layout at startup.
DEFAULT_WINDOW_WIDTH = 900

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

# The OS window title. Deliberately carries no version number -- the client sees
# this string. `APP_VERSION` is still stamped into every diagnostic sink (run ids,
# log filenames, artifact manifests), which is where build traceability belongs.
APP_WINDOW_TITLE = "Alpha Meeting Assistant"

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
# `.atf-reading-grid { grid-template-columns: minmax(0, 70fr) 8px minmax(220px, 30fr) }`
# -- the reference pane has a 220px floor, so it stops being 30% of the window
# once 30% would be narrower than that. Defined here since the first UI pass but
# with no consumer until item 71 phase 3c; `_apply_content_layout` now sets it as
# the column `minsize`.
RIGHT_COLUMN_MIN_WIDTH = 220

# The design's STACKED row split is not the same as its column split:
#   @media (max-width: 700px) { grid-template-rows: minmax(0, 1.15fr) minmax(0, .85fr) }
# i.e. 57.5 / 42.5, not 70 / 30. Reading is harder in a short pane than in a
# narrow one, so the reference gets proportionally more height than it gets
# width. Item 71 phase 3b used 70/30 here by carrying the column weights over;
# that was a deviation from the design, corrected in 3c.
CONTENT_STACKED_PRIMARY_WEIGHT = 115
CONTENT_STACKED_REFERENCE_WEIGHT = 85
