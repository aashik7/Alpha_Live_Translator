# Presentation Notes — Alpha Live Translator UI

Short talking points for demonstrating the UI/UX showcase to stakeholders, teammates, or reviewers.

---

## Opening (30 seconds)

> "This is a separate UI presentation — it does not connect to our working app. It shows how we want Alpha Live Translator to look and feel during a Japanese meeting, and what comes next on the roadmap."

Open: `UI Design/prototype/index.html`

---

## Why the UI Is Designed This Way

- **Dark, calm desktop layout** — Matches how users actually work: long meetings, focus on text, minimal distraction.
- **Panel-based architecture** — Each stage of the pipeline (live → stable → translation → summary) gets its own space so users always know what they are reading.
- **Sidebar navigation** — Four clear destinations: live meeting, settings, review, and future summary.
- **Enterprise tone** — Rounded panels, consistent spacing, status badges — professional enough for business demos.

---

## Why Japanese Accuracy Mode Is Visible

- It is our **core differentiator** — we optimize for correct Japanese, not fast English.
- Users need to **trust the mode they are in** — the badge confirms accuracy processing is active.
- Stakeholders should see that **Japanese quality is the priority**, not an afterthought.
- It sets expectations: this tool is for Japanese meetings first.

**Say:** "The badge isn't decoration — it tells the user and everyone in the room that we're in accuracy-first Japanese mode."

---

## Why Translation Is Marked as Planned

- **Honest roadmap** — English translation (DeepL) depends on stable Japanese being correct first.
- **No false demo** — We don't fake working translation in the UI; that would mislead stakeholders.
- **Pipeline order** — Raw → Stable → Translate. The UI mirrors this sequence visually.
- **Builds trust** — Showing "Coming later" is more credible than a broken or fake feature.

**Say:** "Translation is downstream. We won't ship it until stable Japanese is trustworthy — the UI makes that order explicit."

---

## Why Summary Is Marked as Planned

- Summaries require **high-quality stable transcripts** as input.
- Groq cleanup and summarization are **future integrations**, not current scope.
- The Summary Preview screen shows **structure and intent** without pretending it works today.
- Useful for planning: key points, action items, narrative summary sections are already laid out.

**Say:** "This screen is a blueprint. When we add summarization, users already know where it lives."

---

## Why Local WASAPI + Mic Is the Current Capture Method

- **WASAPI** captures system audio directly from the meeting app (Teams, Zoom) — no virtual cable setup for most users.
- **Microphone** captures the user's own voice when they speak.
- **Combined capture** gives the fullest picture of the meeting from the user's machine.
- Displaying this in the UI helps users **troubleshoot audio** and understand what is being transcribed.

**Say:** "We show capture mode on screen so users know exactly what audio sources are active — system audio plus their mic."

---

## Screen Walkthrough Order

1. **Main Meeting** — Start with the live experience. Click Start to show button states. Point at live vs. stable panels.
2. **Settings** — Quick scan of what's active vs. planned.
3. **Transcript Review** — Show post-meeting workflow and export buttons.
4. **Summary** — End with future vision; emphasize it's labeled as planned.

---

## Handling Questions

| Question | Response |
|----------|----------|
| "Does this connect to Deepgram?" | "No — this is mock UI only. The real app handles that separately." |
| "Can I export transcripts here?" | "Export buttons are visual only in this prototype. Export works in the production app." |
| "When is English coming?" | "After stable Japanese accuracy is validated. The UI reserves the panel now." |
| "Why not use the real app for demos?" | "This prototype is safe to share — no API keys, no private meeting data, no risk to production." |

---

## Closing (15 seconds)

> "This package documents our UX direction: Japanese accuracy first, honest about what's planned, and a professional meeting workspace. The production app continues separately — this folder is presentation only."
