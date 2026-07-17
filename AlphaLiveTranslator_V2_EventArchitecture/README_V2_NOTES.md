# Alpha Live Translator — V2 Event Architecture Notes

## Overview

Version 2 adds **security hygiene** and begins **event-driven separation** between backend and UI. The V1 modern UI is unchanged. All audio, WASAPI, microphone, Deepgram, speaker detection, duplicate protection, and threading behavior are preserved.

**Source (read-only):** `AlphaLiveTranslator_V1_ModernUI/`  
**Work folder:** `AlphaLiveTranslator_V2_EventArchitecture/`

---

## Stage 2A — Security + Project Hygiene

### API key configuration

1. Copy the example environment file:
   ```powershell
   copy .env.example .env
   ```
2. Edit `.env` and set your real key:
   ```
   DEEPGRAM_API_KEY=your_actual_key_here
   ```
3. **Never commit `.env`** — it is listed in `.gitignore`.

The application does **not** crash at import if the key is missing. When you click **Start Listening** without a key, you will see:

> Deepgram API key is missing. Please create a .env file with DEEPGRAM_API_KEY.

### Other hygiene

- `__pycache__/` and `*.pyc` files should not be committed (see `.gitignore`)
- `python-dotenv` loads `.env` from the project root
- API keys are never printed in logs (see `alpha/utils/logging_utils.py`)

---

## Stage 2B — Event Architecture

New module: `alpha/core/`

| File | Purpose |
|------|---------|
| `events.py` | `EventType` enum |
| `models.py` | `TranscriptEvent`, `TranslationEvent`, `StatusEvent`, `ErrorEvent` |
| `event_bus.py` | Thread-safe `EventBus` (subscribe / publish / unsubscribe) |

`AlphaApp` creates `self.event_bus` and subscribes UI handlers in `_setup_event_subscriptions()`.

Adapter methods on `AlphaApp`:

- `publish_transcript_event(...)` — enqueues for `process_ui_queue` **and** publishes `TRANSCRIPT_RECEIVED`
- `publish_translation_event(...)` — placeholder for future DeepL
- `publish_status_event(...)` — session status
- `publish_error_event(...)` — backend errors

The existing `process_ui_queue` loop is **unchanged** and still drives transcript display.

---

## Run

```powershell
cd "c:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\AlphaLiveTranslator_V2_EventArchitecture"
pip install -r requirements.txt
python -m compileall -q .
python main.py
```

---

## TODO V3 (event migration)

Locations still using direct UI updates or dual paths:

- `duplicate_protection._display_transcript_item` — direct `initial_verse_box` writes
- `deepgram_client._deepgram_on_error` — `messagebox` via `self.after`
- `main_window._on_transcript_received` — display deferred to V3
- `main_window._on_translation_received` — `translated_verse_box` updates deferred
- `duplicate_protection` DeepL block — translation placeholder
- Audio capture loops (`wasapi.py`, `microphone.py`) — unchanged by design
