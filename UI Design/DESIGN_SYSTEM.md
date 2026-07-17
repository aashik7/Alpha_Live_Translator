# Design System — Alpha Live Translator UI Showcase

This document defines the visual language used in the static prototype (`prototype/`). Emoji and text labels stand in for iconography — no icon font or image dependencies.

## Colors

### Backgrounds

| Token | Hex | Usage |
|-------|-----|-------|
| `--bg-base` | `#0f1117` | App background |
| `--bg-surface` | `#161b26` | Sidebar, header |
| `--bg-panel` | `#1c2333` | Content panels |
| `--bg-panel-hover` | `#222b3d` | Panel hover state |
| `--bg-input` | `#121820` | Text areas, inputs |

### Accent & Status

| Token | Hex | Usage |
|-------|-----|-------|
| `--accent-primary` | `#3b82f6` | Primary actions, active nav |
| `--accent-success` | `#22c55e` | Active/recording status |
| `--accent-warning` | `#f59e0b` | Pause, planned features |
| `--accent-muted` | `#6366f1` | Accuracy mode badge |
| `--accent-danger` | `#ef4444` | Stop action |

### Text

| Token | Hex | Usage |
|-------|-----|-------|
| `--text-primary` | `#f1f5f9` | Headings, body |
| `--text-secondary` | `#94a3b8` | Labels, metadata |
| `--text-muted` | `#64748b` | Placeholders, disabled |
| `--text-jp` | `#e2e8f0` | Japanese transcript text |

### Borders

| Token | Hex | Usage |
|-------|-----|-------|
| `--border-subtle` | `#2a3448` | Panel borders |
| `--border-accent` | `#3b82f640` | Focused/active borders |

## Typography

| Element | Font Stack | Size | Weight |
|---------|-----------|------|--------|
| App title | Segoe UI, system-ui, sans-serif | 18px | 600 |
| Screen title | Segoe UI, system-ui, sans-serif | 15px | 600 |
| Panel title | Segoe UI, system-ui, sans-serif | 13px | 600 |
| Body / transcript | Segoe UI, "Hiragino Sans", "Yu Gothic", sans-serif | 14px | 400 |
| Japanese transcript | Segoe UI, "Hiragino Sans", "Yu Gothic", Meiryo, sans-serif | 15px | 400 |
| Labels / badges | Segoe UI, system-ui, sans-serif | 11px | 500 |
| Timer / mono | Consolas, "Courier New", monospace | 13px | 400 |

Line height for transcript areas: **1.7** for readability of Japanese text.

## Button Styles

### Primary (Start)

- Background: `--accent-success`
- Text: white
- Border-radius: 8px
- Padding: 10px 20px
- Hover: slightly brighter green

### Secondary (Pause)

- Background: `--bg-panel`
- Border: 1px `--border-subtle`
- Text: `--text-primary`
- Hover: `--bg-panel-hover`

### Danger (Stop)

- Background: transparent
- Border: 1px `--accent-danger`
- Text: `--accent-danger`
- Hover: `--accent-danger` at 10% opacity background

### Ghost / Export

- Background: transparent
- Border: 1px `--border-subtle`
- Text: `--text-secondary`
- Hover: border `--accent-primary`, text `--text-primary`

## Panel Styles

- Background: `--bg-panel`
- Border: 1px solid `--border-subtle`
- Border-radius: 12px
- Padding: 16px 20px
- Box-shadow: none (flat, professional look)

### Panel Header

- Flex row with title left, badge/status right
- Bottom margin: 12px
- Optional accent left border (4px) for panel type:
  - Live: `--accent-primary`
  - Stable: `--accent-success`
  - Planned: `--accent-warning`

## Status Badges

Pill shape: `border-radius: 999px`, padding `4px 10px`, font-size `11px`.

| Badge | Background | Text | Example |
|-------|-----------|------|---------|
| Active | `#22c55e20` | `#22c55e` | ● Recording |
| Mode | `#6366f120` | `#a5b4fc` | Japanese Accuracy Mode |
| Capture | `#3b82f620` | `#93c5fd` | WASAPI + Mic |
| Planned | `#f59e0b20` | `#fbbf24` | Coming later |
| Future | `#64748b20` | `#94a3b8` | Future optional |

## Spacing Rules

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | 4px | Badge internal |
| `--space-sm` | 8px | Tight gaps |
| `--space-md` | 16px | Panel padding, grid gaps |
| `--space-lg` | 24px | Section separation |
| `--space-xl` | 32px | Screen margins |

Grid layout for main meeting screen: **2 columns** on wide viewports, **1 column** below 900px.

## Icons

Represented with emoji or short text labels only:

| Meaning | Representation |
|---------|---------------|
| Meeting | 📋 or "Meeting" |
| Settings | ⚙ or "Settings" |
| Review | 📄 or "Review" |
| Summary | 📝 or "Summary" |
| Audio | 🔊 |
| Mic | 🎤 |
| Export | ⬇ |
| Planned | ⏳ |

## Motion

- Transitions: `150ms ease` for hover, nav highlight, button states
- No heavy animations — lightweight and professional
- Typing indicator: subtle opacity pulse on live transcript cursor

## Accessibility Notes

- Minimum contrast ratio 4.5:1 for body text on panels
- Focus rings on interactive elements: 2px `--accent-primary`
- Japanese text uses adequate font size (15px) for readability
