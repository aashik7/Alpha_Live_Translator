# Alpha Live Translator — Release Notes

## Version 3.3.0 Stable System Audio Baseline

Clean stable source folder frozen from the validated V3.2.8 system-audio pipeline.

### Validated

- YouTube / system audio transcription
- Full ending capture after short post-video wait + Stop
- Deepgram mono 16 kHz linear16 stream alignment (~256 kbps)
- Interim tail recovery on Stop
- Duplicate protection
- Copy Transcript

### Known limitations

- Microphone + speaker separation not validated yet in this release
- Real Teams / Zoom meeting test not validated yet
- Translation is coming soon
- Meeting summary is coming soon

### Local setup

Copy `.env.example` to `.env` and set `DEEPGRAM_API_KEY`. Do not commit `.env`.

### Diagnostics

Set `DEBUG_DIAGNOSTICS = True` in `alpha/constants.py` to re-enable verbose NDJSON logs (`[LATENCY]`, `[AUDIO_FORMAT]`, `[INTERIM]`, etc.).
