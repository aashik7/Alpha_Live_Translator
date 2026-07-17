# Alpha Live Translator v3.2.5 — Final Transcript Commit Fix

## Problem

During Stop/Finalize, final Deepgram transcripts could arrive but not be committed to TranscriptStore/UI because:

- `is_listening` was set to `False` at the start of finalizing
- `_dg_receiver_allowed` was `False` until after Finalize was sent
- `_is_finalizing` was cleared in the worker thread before the UI flushed the transcript queue

## Fix

1. `receiver_allowed` stays `True` for the entire finalize window
2. Final transcript commit rule: `allow_commit = is_listening or is_finalizing`
3. Final transcripts during finalize are committed immediately on the UI thread via `after(0)`
4. Worker requests a UI queue flush before CloseStream
5. `_is_finalizing` is cleared only after UI flush in `_finish_graceful_stop`

## Version

- `APP_VERSION = 3.2.5`
- `APP_CODENAME = Final Transcript Commit Fix`

## Tests

```bash
python -m compileall -q .
python tests/test_transcript_hotfix_v3_2.py
python tests/test_stop_finalize_v3_2_3.py
python tests/test_stop_queue_flush_v3_2_4.py
python tests/test_final_transcript_commit_v3_2_5.py
```
