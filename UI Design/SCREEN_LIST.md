# Screen List — Alpha Live Translator UI Showcase

Each screen is accessible via sidebar navigation in `prototype/index.html`.

---

## 1. Main Meeting Screen

**ID:** `screen-meeting`  
**Purpose:** Primary workspace during an active meeting capture session.

### Components

| Component | Description |
|-----------|-------------|
| Header | App title "Alpha Live Translator" with version label |
| Status bar | Japanese Accuracy Mode, capture mode (WASAPI + Mic), recording timer |
| Control bar | Start, Pause, Stop buttons (visual UI only in prototype) |
| Live Japanese panel | Interim transcript with typing indicator |
| Stable Japanese panel | Corrected, finalized Japanese lines |
| English translation panel | Placeholder — "Coming later" |
| Meeting summary panel | Placeholder — "Coming later" |

### When Used

- During live meeting capture
- Default landing screen when prototype opens

---

## 2. Settings Preview

**ID:** `screen-settings`  
**Purpose:** Show current configuration and planned integrations without editable forms.

### Components

| Setting | Status in Prototype |
|---------|---------------------|
| Language | Japanese (active) |
| STT Engine | Deepgram Nova-3 (active) |
| Capture | System Audio + Mic (active) |
| Accuracy Mode | On (active) |
| UI Performance Mode | On (active) |
| DeepL Translation | Planned |
| Groq Cleanup | Planned |
| MeetingBaaS | Future optional |

### When Used

- Pre-meeting configuration review
- Stakeholder demos explaining tech stack and roadmap

---

## 3. Transcript Review Screen

**ID:** `screen-review`  
**Purpose:** Post-meeting comparison and export of transcript data.

### Components

| Component | Description |
|-----------|-------------|
| Raw Japanese transcript | Full interim-style transcript block |
| Stable Japanese transcript | Cleaned, accuracy-processed version |
| Export buttons | TXT, JSON, Copy — visual feedback only |
| Session metadata | Mock date, duration, line counts |

### When Used

- After meeting ends
- Before archiving or sharing transcripts

---

## 4. Summary Preview Screen

**ID:** `screen-summary`  
**Purpose:** Preview of future AI-generated meeting summary feature.

### Components

| Component | Description |
|-----------|-------------|
| Future feature banner | Clear label that this is not yet available |
| Meeting summary placeholder | Sample structure for narrative summary |
| Key points placeholder | Bullet list format preview |
| Action items placeholder | Checkbox-style action item preview |

### When Used

- Roadmap presentations
- UX planning for summary feature
- Stakeholder expectation setting

---

## Navigation

| Nav Item | Screen |
|----------|--------|
| 📋 Meeting | Main Meeting Screen |
| ⚙ Settings | Settings Preview |
| 📄 Review | Transcript Review Screen |
| 📝 Summary | Summary Preview Screen |

Navigation is implemented in the prototype sidebar. No routing library — simple show/hide via JavaScript.
