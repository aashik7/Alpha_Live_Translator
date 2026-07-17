# Alpha Live Translator V4 — DeepL Translation Notes

## What Was Added

Version 4 integrates **DeepL** real-time translation into the existing V3 UX-polished app without changing audio capture, Deepgram transcription, speaker detection, or duplicate protection behavior.

New components:

- `alpha/translation/deepl_client.py` — DeepL REST client (`POST /v2/translate`)
- `alpha/translation/language_map.py` — UI language names → DeepL codes (EN / JA / ZH / RU)
- `alpha/translation/translation_worker.py` — background queue + daemon thread + small cache
- EventBus events: `TRANSLATION_STARTED`, `TRANSLATION_RECEIVED`, `TRANSLATION_ERROR`
- Extended `TranslationEvent` model with `speaker` and `error_message`
- Main window wiring: worker init, `submit_text_for_translation`, UI-thread result handling

Transcript lines that pass duplicate protection are submitted to the translation worker. Results appear in the **Translation** panel with speaker-colored labels matching the Live Transcript panel.

## Required `.env` Variables

```env
DEEPGRAM_API_KEY=your_deepgram_api_key_here
DEEPL_API_KEY=your_deepl_api_key_here
DEEPL_API_PLAN=auto
DEEPL_TIMEOUT_SECONDS=10
```

Copy `.env.example` to `.env` and fill in your keys. **Never commit `.env` or hardcode API keys.**

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPGRAM_API_KEY` | Yes (for transcription) | Deepgram Nova-3 STT |
| `DEEPL_API_KEY` | No (for translation) | DeepL translation; app runs without it |
| `DEEPL_API_PLAN` | No | `auto`, `free`, or `pro` (default: `auto`) |
| `DEEPL_TIMEOUT_SECONDS` | No | HTTP timeout for DeepL calls (default: `10`) |

## DeepL Free / Pro Endpoint Selection

| `DEEPL_API_PLAN` | Endpoint |
|------------------|----------|
| `free` | `https://api-free.deepl.com` |
| `pro` | `https://api.deepl.com` |
| `auto` | Free endpoint if API key ends with `:fx`, otherwise Pro endpoint |

## How to Run

```powershell
cd "AlphaLiveTranslator_V3_UXPolish"
pip install -r requirements.txt
python main.py
```

## Behavior Notes

- **Missing DeepL key:** App opens normally. Transcription works if `DEEPGRAM_API_KEY` is set. Translation panel shows a placeholder explaining that DeepL is disabled.
- **Same source/target language:** Original text is shown in the translation panel without calling DeepL.
- **Translation runs off the UI thread** via `TranslationWorker` (daemon thread + `queue.Queue`).
- **Worker stops on app close**; it remains idle when listening stops so sessions can restart.
- **Errors** are logged once in the translation panel; no repeated popups per segment.

## Supported Languages

| UI Name | DeepL Code |
|---------|------------|
| English | EN |
| Japanese | JA |
| Chinese (Mandarin) | ZH |
| Russian | RU |

## Known Limitations

- Translation is **segment-by-segment** (per finalized transcript line), not streaming word-by-word.
- No translation retry/backoff beyond queue drop-on-full (oldest job discarded).
- In-memory cache only (cleared when app restarts).
- **AI Meeting Summary / summarization is not implemented** — the summary panel remains a placeholder.
- No Share button, Action Items, or AI Assistant widget.

## What Was Not Changed

- WASAPI + microphone capture
- Deepgram WebSocket / Nova-3 transcription
- Speaker detection
- Duplicate protection logic (only a post-display hook to submit translation)
- UI layout and V3 UX polish (summary collapse, status bar, shortcuts, etc.)
