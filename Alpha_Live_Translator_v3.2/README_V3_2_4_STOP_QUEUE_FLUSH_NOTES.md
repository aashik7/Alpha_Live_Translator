# Alpha Live Translator v3.2.4 — Stop Queue Flush

## Problem

Stop finalization still cut off ending sentences because `Finalize` could be sent before all queued outgoing audio reached Deepgram.

## Fix

Stop now follows a bounded queue-flush-first sequence:

1. Enter `Finalizing...`
2. Stop accepting new audio from capture
3. Wait for outgoing audio queue to flush (max 5s, no queue clearing)
4. Wait 300ms settle
5. Send `{"type": "Finalize"}`
6. Keep receiver alive and wait for final transcripts (max 5s)
7. Send `{"type": "CloseStream"}`
8. Wait up to 1500ms and close socket safely
9. Enter `Stopped`

## Added methods

- `get_outgoing_audio_queue_size()`
- `wait_for_outgoing_audio_flush(timeout_seconds=5.0)`

## Logging

Added stop-path logs:

- `[STOP] finalizing started`
- `[STOP] stopped accepting new audio`
- `[STOP] outgoing queue size before flush: X`
- `[STOP] waiting for outgoing audio queue flush`
- `[STOP] outgoing audio queue flushed`
- `[STOP] outgoing audio queue flush timeout, remaining: X`
- `[STOP] sending Finalize`
- `[STOP] waiting for final transcript messages`
- `[STOP] final transcript received during finalize`
- `[STOP] sending CloseStream`
- `[STOP] socket closed`
- `[STOP] finalizing completed`
