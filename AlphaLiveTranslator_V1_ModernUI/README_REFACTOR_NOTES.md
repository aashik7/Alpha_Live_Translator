# Alpha Live Translator — V0 Refactor Notes

## Overview

Conservative structural refactor of `alpha_V4.py` into a multi-module package.
**Original `alpha_V4.py` was not modified.**

## How to run

```bash
cd AlphaLiveTranslator_V0_Refactor
python main.py
```

Set `DEEPGRAM_API_KEY` environment variable to override the default key in `alpha/config.py`.

## Files created

| File | Contents moved from alpha_V4.py |
|------|----------------------------------|
| `main.py` | Application startup only |
| `alpha/constants.py` | `APP_VERSION`, languages, `COMPACT_BREAKPOINT` |
| `alpha/config.py` | Deepgram settings, `LANGUAGE_CONFIG`, API key, paths |
| `alpha/ui/theme.py` | `COLORS`, `SPEAKER_COLORS` |
| `alpha/audio/processing.py` | `_pcm_to_mono_16k_np`, `_apply_noise_gate`, `_mix_audio_chunks`, `_process_audio_chunk` |
| `alpha/audio/wasapi.py` | WASAPI loopback capture methods (mixin) |
| `alpha/audio/microphone.py` | Microphone capture methods (mixin) |
| `alpha/transcription/deepgram_client.py` | Deepgram URL, WebSocket, reconnect, health monitor (mixin) |
| `alpha/transcription/speaker_detection.py` | Speaker split / fallback detection (mixin) |
| `alpha/transcription/duplicate_protection.py` | UI queue dedup and display (mixin) |
| `alpha/ui/main_window.py` | `AlphaApp` UI, mixer, listen/stop orchestration |
| `alpha/utils/queues.py` | `_put_bounded` |
| `alpha/utils/logging_utils.py` | Print-based log helpers (V0 placeholder) |
| `assets/logo.png` | Copied from parent project (if present) |

**Note:** If `logo.png` is missing from `assets/`, copy it from the parent project folder manually. The app will run without it (logo is omitted with a console warning).

## Intentionally left in main_window.py (V0)

- `audio_mixer_worker` — tightly coupled to app queues and state
- `_start_listening` / `_stop_listening` — orchestrates audio + Deepgram threads
- All CustomTkinter UI construction and layout
- Event handlers (`toggle_listening`, `clear_text`, etc.)

## Architecture (V0)

`AlphaApp` inherits mixins to preserve original `self.*` method behavior without rewriting logic.

## TODO — Version 1

- Extract `audio_mixer_worker` into `alpha/audio/mixer.py`
- Replace mixin pattern with explicit service classes where safe
- Add structured logging via `logging_utils.py`
- Move API key to `.env` only (remove default fallback)
- Unit tests for `processing.py` and duplicate protection
