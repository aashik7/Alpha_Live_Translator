# Alpha Live Translator v3.2.3 — Stop Finalization Fix

## Problem

Stop could close the Deepgram WebSocket before final transcript messages arrived after `Finalize`, causing the last sentences to be missing.

## Fix

Bounded stop sequence (max ~7 seconds):

1. `Finalizing...` UI state
2. Stop new audio capture
3. Drain outgoing audio up to 1500 ms (receiver stays alive)
4. Send `{"type": "Finalize"}`
5. Keep receiver alive up to 4000 ms for final transcripts
6. Send `{"type": "CloseStream"}`
7. Wait up to 1500 ms
8. Close socket, then set stop flags
9. Flush transcript queue on UI thread, show `Stopped`

`_stop_event` is not set until after the post-Finalize wait completes.

## Version

- `APP_VERSION = 3.2.3`
- `APP_CODENAME = Stop Finalization Fix`

## Tests

```bash
python -m compileall -q .
python tests/test_transcript_hotfix_v3_2.py
python tests/test_stop_finalize_v3_2_3.py
```
