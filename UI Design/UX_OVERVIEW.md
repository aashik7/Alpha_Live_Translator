# UX Overview — Alpha Live Translator

## Product Purpose

Alpha Live Translator is a desktop meeting assistant that captures audio from online meetings and produces accurate Japanese transcripts in real time. It is built for professionals who participate in Japanese-language meetings and need reliable, reviewable text output — not flashy but inaccurate live translation.

## Target User

- Business professionals attending Japanese online meetings (Teams, Zoom, etc.)
- Team leads who need accurate meeting records for follow-up
- Bilingual teams where Japanese is the primary meeting language
- Users who value transcript accuracy over speed-of-translation

## Main UX Problem

Live meeting tools rarely provide trustworthy Japanese transcription. Users face:

1. **Inaccurate STT** — Generic speech-to-text misses business terms and conversational Japanese
2. **Noisy raw output** — Interim results flicker and confuse readers
3. **Translation before accuracy** — Tools rush to English before Japanese is correct
4. **No review workflow** — Hard to export, compare raw vs. stable text, or prepare summaries

## UX Goal

Deliver a **calm, accuracy-first meeting workspace** where users can:

- Start capture with one clear action
- See live Japanese text as it arrives
- Trust a stabilized Japanese transcript for review
- Understand what is working now vs. what is planned
- Export transcripts when the meeting ends

## Japanese-First Workflow

The interface prioritizes Japanese at every step:

```
Audio capture → Raw Japanese (live) → Stable Japanese → [Future: English] → [Future: Summary]
```

English translation and meeting summaries are **downstream** features. The UI makes this order explicit so users and stakeholders understand the product roadmap without confusion.

## Accuracy-First Design Principle

Every design decision reinforces accuracy over speed:

| Principle | UI Expression |
|-----------|---------------|
| **Show the pipeline** | Separate panels for raw live vs. stable Japanese |
| **Visible mode** | "Japanese Accuracy Mode" badge always shown during capture |
| **No false promises** | Translation and summary panels marked "Planned" |
| **Capture transparency** | WASAPI + Mic mode displayed so users know audio source |
| **Review before export** | Dedicated Transcript Review screen for post-meeting work |

## Emotional Tone

The UI should feel:

- **Professional** — Enterprise meeting tool, not a consumer toy
- **Calm** — Dark theme, generous spacing, no visual noise
- **Trustworthy** — Status indicators and mode labels are always visible
- **Honest** — Planned features are labeled, not hidden or faked

## Success Metrics (UX)

- User can start a meeting capture within 2 clicks
- User can distinguish live vs. stable Japanese at a glance
- User understands which features are available today
- User can navigate to transcript review without training
