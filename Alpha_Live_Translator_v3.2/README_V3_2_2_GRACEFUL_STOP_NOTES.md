# Alpha Live Translator v3.2.2 — Graceful Stop / Deepgram Finalize

## Problem

Clicking Stop immediately after a video ends could miss the last sentences because the WebSocket was closed before Deepgram flushed final results.

## Solution

On Stop, Alpha now:

1. Shows `Finalizing...` and disables the listen button
2. Stops WASAPI/microphone capture (no new audio)
3. Drains queued audio for up to 1 second
4. Sends `{"type": "Finalize"}`
5. Waits up to 3 seconds for final transcript events
6. Sends `{"type": "CloseStream"}`
7. Waits up to 2 seconds, then closes the socket
8. Shows `Stopped` and re-enables Start Listening

Total bounded graceful stop: about 6 seconds maximum.

## Version

- `APP_VERSION = 3.2.2`
- `APP_CODENAME = Graceful Stop Finalize`

## Tests

```bash
python -m compileall -q .
python tests/test_transcript_hotfix_v3_2.py
python tests/test_graceful_stop_v3_2_2.py
```

## Manual check

1. Play a short YouTube video to the end
2. Click Stop immediately when the video ends
3. Confirm `Finalizing...` appears, then `Stopped` within ~1–6 seconds
4. Copy transcript — final sentence should be present without duplication
