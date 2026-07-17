# Alpha Live Translator — V1 Modern UI Notes

## Overview

Version 1 is a **UI-only modernization** of the V0 refactor. All audio capture, WASAPI, microphone, Deepgram WebSocket, speaker detection, duplicate protection, threading, and transcription logic are unchanged.

**Source (read-only):** `AlphaLiveTranslator_V0_Refactor/`  
**Work folder:** `AlphaLiveTranslator_V1_ModernUI/`

## Files Modified (V1)

| File | Changes |
|------|---------|
| `alpha/ui/theme.py` | Modern dark navy design system (colors, fonts, radii, spacing) |
| `alpha/ui/main_window.py` | Header, status bar, two-column layout, summary card, footer toolbar |

## UI Changes

### Window
- Size: 1180×760 (min 1000×650), resizable
- Dark navy background (`#07111F`)

### Header
- Logo + **Alpha AI** / **Live Translator** branding
- Source / target language dropdowns with swap button
- Meeting Summary button
- Always On Top switch
- No settings gear; listen control moved to footer
- Compact hamburger menu preserved for narrow windows

### Status Bar
- LIVE indicator (red when listening)
- Listening status text
- Animated waveform visualization
- Session timer (00:00 while idle, live count while listening)
- Signal indicator

### Main Content (70% / 30%)
- **Left:** Live Transcript card + Translation card (larger font, more space)
- **Right:** AI Meeting Summary card with key points placeholder and Show more button

### Footer
- Start / Stop Listening (existing `toggle_listening`)
- Stop (`_stop_listening`)
- Copy Translation (`copy_translation_to_clipboard`)
- Export placeholder (`export_transcript_placeholder`)
- Clear (`clear_text`)

## Preserved Backend Wiring

These attributes and methods remain intact:

- `self.initial_verse_box`, `self.translated_verse_box`
- `self.initial_verse_frame`, `self.translated_verse_frame`
- `self.listen_button`, `self.listen_button_menu`, `self.always_on_top_switch`
- `self.source_language`, `self.target_language`, `self.paned` (alias to `left_column`)
- `toggle_listening`, `_start_listening`, `_stop_listening`, `_set_listen_button_state`
- `_append_initial_transcript`, `_insert_formatted_text`, `process_ui_queue`, `clear_text`
- `show_meeting_summary`, `on_language_change`, `toggle_always_on_top`, `_on_close`

## Known Limitations (V1)

- AI Meeting Summary is a visual placeholder only (Stage 2)
- Export shows an informational dialog; no file export yet
- No Pause button (not implemented in backend)
- No Action Items, AI Assistant widget, or Share button
- Transcript hide/show uses grid layout instead of PanedWindow sash drag

## Run

```powershell
cd "c:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\AlphaLiveTranslator_V1_ModernUI"
python -m compileall -q .
python main.py
```
