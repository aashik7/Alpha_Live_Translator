# User Flow — Alpha Live Translator

This document describes the intended end-to-end user journey. The prototype demonstrates this flow with mock data only.

## High-Level Flow

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│ Start       │ ──► │ Capture      │ ──► │ Show raw        │ ──► │ Stabilize        │
│ meeting     │     │ audio        │     │ Japanese        │     │ Japanese         │
└─────────────┘     └──────────────┘     └─────────────────┘     └──────────────────┘
                                                                            │
                    ┌──────────────┐     ┌─────────────────┐                │
                    │ Export /     │ ◄── │ Future:         │ ◄── ┌──────────┴─────────┐
                    │ save         │     │ meeting summary │     │ Future: English      │
                    └──────────────┘     └─────────────────┘     │ translation          │
                                                                 └──────────────────────┘
```

## Step-by-Step Flow

### 1. Start Meeting

**User action:** Opens Alpha Live Translator, confirms settings (Japanese, WASAPI + Mic, Accuracy Mode on), clicks **Start**.

**System response:** Capture begins. Status badge shows "Recording". Timer starts.

**UI screen:** Main Meeting Screen

---

### 2. Capture Audio

**User action:** Participates in the online meeting normally. No interaction required with the app during capture.

**System response:** WASAPI captures system audio (meeting output); microphone captures local voice. Audio streams to Deepgram Nova-3 (in production).

**UI screen:** Main Meeting Screen — capture mode indicator visible

---

### 3. Show Raw Japanese

**User action:** Glances at the live transcript panel during the meeting.

**System response:** Interim Japanese text appears in the "Live Japanese" panel. Text may update frequently as speech is recognized.

**UI screen:** Main Meeting Screen — left/top live panel

---

### 4. Stabilize Japanese

**User action:** Relies on the stable panel for accurate reading; ignores flickering interim text.

**System response:** Accuracy pipeline processes interim results into stable, corrected Japanese lines.

**UI screen:** Main Meeting Screen — stable Japanese panel

---

### 5. Future: English Translation

**User action:** (Planned) Reviews English translation alongside Japanese.

**System response:** (Planned) DeepL translates stabilized Japanese segments.

**UI screen:** Main Meeting Screen — English panel marked "Coming later"

**Status:** Not implemented. UI shows placeholder to set expectations.

---

### 6. Future: Meeting Summary

**User action:** (Planned) Reviews AI-generated summary, key points, and action items.

**System response:** (Planned) Groq or similar generates structured summary from stable transcript.

**UI screen:** Summary Preview Screen — marked as future feature

**Status:** Not implemented.

---

### 7. Export / Save

**User action:** After meeting ends (Stop), navigates to Transcript Review. Clicks export buttons.

**System response:** (In production) Saves raw and stable Japanese transcripts. Prototype: visual button feedback only.

**UI screen:** Transcript Review Screen

---

## Secondary Flows

### Settings Check (Pre-Meeting)

User opens Settings Preview to verify:
- Language: Japanese
- STT: Deepgram Nova-3
- Capture: System Audio + Mic
- Accuracy Mode: On

### Pause / Resume

User clicks Pause during a break. Timer pauses, status shows "Paused". Resume continues capture.

### Post-Meeting Review

User switches to Transcript Review to compare raw vs. stable text before exporting.

## Flow Principles

1. **Japanese before English** — Users always see Japanese working first
2. **Stable before summary** — Summary depends on accurate stable text
3. **Honest labeling** — Future steps are visible but marked "Planned"
4. **Minimal mid-meeting interaction** — Capture should not distract from the meeting
