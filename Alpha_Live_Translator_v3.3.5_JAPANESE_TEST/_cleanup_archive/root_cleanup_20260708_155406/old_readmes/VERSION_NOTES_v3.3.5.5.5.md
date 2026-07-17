# Alpha Live Translator — Checkpoint

## V3.3.5.5.5 — UI Responsiveness Recovery Stable

**Active folder:** `Alpha_Live_Translator_v3.3.5_JAPANESE_TEST`  
**Date:** 2026-07-03  
**Status:** Working checkpoint — UI responsiveness recovery validated for daily use

---

### Summary

Stable checkpoint after targeted CustomTkinter UI responsiveness fixes. Startup and window interaction are materially improved; Start Listening no longer blocks the UI thread for heavy audio/device/Deepgram work. Japanese manual transcription settings are unchanged.

---

### Validated (this checkpoint)

- **Startup speed improved** — window appears quickly; heavy logo/waveform draw and startup NDJSON logging deferred off the critical path
- **Window movement smooth before Start** — no pixel break / repaint lag during idle drag (~10s move test)
- **Window movement smoother after Start** — UI remains movable during audio/device/Deepgram startup
- **Start Listening no longer freezes badly** — button returns immediately; blocking work runs on background worker with queue-based UI updates

---

### Locks preserved (unchanged)

| Item | State |
|------|--------|
| English lock V3.3.4.5 | **Untouched** (`Alpha_Live_Translator_v3.3` baseline not modified) |
| Japanese mode | **Preserved** — `language=ja` only (manual Japanese mode) |
| Auto language | **Disabled** (`AUTO_LANGUAGE_ENABLED = False`) |
| `language=multi` | **Disabled** (not used) |
| Language gate | **Disabled** (`LANGUAGE_GATE_ENABLED = False`) |
| Old multilingual segment repair | **Disabled** (`MEETING_SEGMENT_REPAIR_ENABLED = False`) |

---

### UI responsiveness work in this line (3.3.5.5.x)

| Version | Codename |
|---------|----------|
| 3.3.5.5 | Script-Aware CJK Cleanup Engine |
| 3.3.5.5.1 | EventBus Startup Error |
| 3.3.5.5.2 | Startup UI Responsiveness Hotfix |
| 3.3.5.5.3 | Deep UI Thread Performance Fix |
| 3.3.5.5.4 | Hard UI Responsiveness Recovery |
| **3.3.5.5.5** | **UI Responsiveness Recovery Stable** (this checkpoint) |

Key 3.3.5.5.5 techniques (no transcription rule changes):

- Deferred logo load, waveform idle draw, and startup NDJSON logs
- Single coalesced post-show `after(0)` init hook
- Interim transcript UI coalesced to ~200ms (no per-fragment `after(0)` flood)
- UI queue drain with item cap (12/tick) and time budget (25ms/tick) at 100ms interval
- Start Listening diagnostics moved to background worker
- UI lag monitor off by default; verbose console logging suppressed in performance mode

---

### Files touched in 3.3.5.5.5 (reference only)

- `alpha/constants.py`
- `alpha/ui/main_window.py`
- `alpha/utils/logging_utils.py`
- `main.py` *(entry-point perf path only; no STT/audio/Deepgram behavior changes)*

---

### Not in scope / known remaining limits

- English baseline folder (`Alpha_Live_Translator_v3.3` / V3.3.4.5) — separate lock; do not merge without explicit approval
- First-launch import cost (`customtkinter` + mixin chain) may still add ~200–300ms before window paint
- Responsive layout recalculation on resize can still spike briefly on mode breakpoints
- CJK cleanup rules unchanged; accuracy tuning is a separate track from this UI checkpoint

---

### Quick verify

```text
python main.py
```

1. Window opens quickly; move window 10s before Start — smooth  
2. Press Start Listening — UI stays responsive; window movable during startup  
3. Confirm constants: `FORCE_DEEPGRAM_LANGUAGE = "ja"`, auto/gate/repair all `False`
