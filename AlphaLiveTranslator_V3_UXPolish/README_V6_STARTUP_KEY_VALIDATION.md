# Alpha Live Translator V6 — Startup Key Validation

## What Changed

- `has_deepgram_api_key()` now rejects `.env.example` placeholder values.
- `get_deepgram_key_status()` returns `missing`, `placeholder`, or `configured`.
- **Start Listening** validates the Deepgram key before any audio or WebSocket threads start.
- Safe startup diagnostics print cwd, `.env` detection, and key status (masked when configured).
- Health monitor prints a one-time hint when `chunks_sent > 0` but `transcripts == 0`.

## Required `.env`

```env
DEEPGRAM_API_KEY=your_real_deepgram_key_here
DEEPL_API_KEY=your_deepl_api_key_here
DEEPL_API_PLAN=auto
DEEPL_TIMEOUT_SECONDS=10
```

Replace placeholder values with real keys. The app will **not** start listening if `DEEPGRAM_API_KEY` is missing or still set to `your_deepgram_api_key_here`.

## DeepL Remains Optional

Missing or placeholder `DEEPL_API_KEY` does not block transcription.

## Run

```powershell
cd AlphaLiveTranslator_V3_UXPolish
python main.py
```
