# Alpha Live Translator — UI Design Showcase

This folder is a **UI/UX presentation package only**. It exists to demonstrate the visual design, information architecture, and user experience direction for Alpha Live Translator.

## Important: Not Connected to the Real App

- This showcase does **not** affect the working CustomTkinter application.
- It does **not** connect to Deepgram, DeepL, Groq, WASAPI, or any backend services.
- It does **not** contain API keys, real logs, or private meeting content.
- All transcript data shown in the prototype is **mock UI data** for presentation purposes.

The production app lives in separate version folders (e.g. `Alpha_Live_Translator_v3.3.5_JAPANESE_TEST`). **No production files were modified** to create this showcase.

## How to Open the Prototype

1. Navigate to this folder: `UI Design/prototype/`
2. Double-click **`index.html`** to open it in any modern web browser (Chrome, Edge, Firefox).
3. No installation, build tools, or server required.

Alternatively, open the file directly:

```
Alpha_Live_Translator V 1.0/UI Design/prototype/index.html
```

## Screens Included

| Screen | Description |
|--------|-------------|
| **Main Meeting** | Live meeting view with Japanese transcript panels, control bar, and planned-feature placeholders |
| **Settings Preview** | Configuration overview showing current and planned integrations |
| **Transcript Review** | Post-meeting transcript review with visual-only export buttons |
| **Summary Preview** | Future meeting summary screen (clearly marked as planned) |

Use the sidebar navigation in the prototype to switch between screens.

## Mock vs. Planned Features

### Working in Production (shown for context, not functional here)

- Japanese Accuracy Mode
- Deepgram Nova-3 STT
- Local WASAPI + Mic capture
- Raw and stable Japanese transcript pipeline

### Mock in This Prototype

- All transcript text (sample Japanese meeting phrases)
- Start / Pause / Stop button states
- Timer and session duration
- Export button interactions (visual feedback only)

### Planned / Future (clearly labeled in UI)

- English translation (DeepL)
- Groq transcript cleanup
- Meeting summary and action items
- MeetingBaaS integration (optional future)

## Folder Structure

```
UI Design/
├── README_UI_DESIGN.md      ← You are here
├── UX_OVERVIEW.md           ← Product and UX rationale
├── DESIGN_SYSTEM.md         ← Colors, typography, components
├── USER_FLOW.md             ← End-to-end user journey
├── SCREEN_LIST.md           ← Screen inventory
├── prototype/
│   ├── index.html           ← Open this in a browser
│   ├── styles.css
│   └── app.js
├── assets/
│   └── README_ASSETS.md
└── notes/
    └── PRESENTATION_NOTES.md
```

## Related Documentation

- **UX_OVERVIEW.md** — Why the product is designed Japanese-first
- **DESIGN_SYSTEM.md** — Visual design tokens and component rules
- **USER_FLOW.md** — Step-by-step meeting workflow
- **SCREEN_LIST.md** — Purpose of each screen
- **notes/PRESENTATION_NOTES.md** — Talking points for demos
