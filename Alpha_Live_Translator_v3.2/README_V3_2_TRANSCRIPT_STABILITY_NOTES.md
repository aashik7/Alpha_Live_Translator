# Alpha Live Translator v3.2 — Transcript Stability Notes

## Version

- **Folder:** `Alpha_Live_Translator_v3.2`
- **APP_VERSION:** `3.2.0`
- **APP_CODENAME:** `Transcript Stability`

## Files changed

1. `alpha/constants.py` — version metadata
2. `alpha/transcription/duplicate_protection.py` — deterministic stabilization helpers and UI decision logic
3. `alpha/summary/transcript_store.py` — canonical segment store with `add_segment`, `update_last_segment`, `get_clean_text`
4. `alpha/ui/main_window.py` — transcript display sync, export from store, stability reset on clear/start

## Files added

5. `tests/test_transcript_stability_v3_2.py` — helper and sequence tests (no GUI)
6. `README_V3_2_TRANSCRIPT_STABILITY_NOTES.md` — this document

## What was fixed

- Removed unsafe naive fragment appending that produced glued text (`Let meWithout`, `it'sAnd`, etc.)
- Added deterministic decision order: skip → replace_last → merge_last → append_new
- Progressive Deepgram finals now **replace** the previous same-speaker line instead of duplicating
- Continuation fragments merge with `merge_with_safe_space` / `remove_overlap_and_merge`
- `TranscriptStore` holds the canonical transcript; UI updates stay synchronized
- **Export** writes `TranscriptStore.get_clean_text()` to a `.txt` file (not raw textbox content)
- Copy/export logs word count, segment count, and stability counters

## What was NOT changed

- Audio capture (WASAPI, microphone, mixer)
- Deepgram WebSocket connection and streaming
- `.env` / API key handling
- Translation worker / DeepL integration
- Summary generation logic
- UI theme, layout, panels, button design
- Translation panel placeholder: **"This feature is coming soon."**

## How to test manually

1. Run from the v3.2 folder:
   ```powershell
   cd Alpha_Live_Translator_v3.2
   python main.py
   ```
2. Click **Start Listening** and play speech (e.g. a 10+ minute YouTube video).
3. Confirm Live Transcript lines are not repeated cumulative blocks.
4. Click **Export** and save the transcript — word count should be roughly 900–1,300 words for a ~10 min video, not 4,000+.
5. Check the terminal for `[Transcript Export] words=..., segments=..., counters=...` after export.
6. Confirm Translation panel still shows: `This feature is coming soon.`

## Automated tests

```powershell
cd Alpha_Live_Translator_v3.2
python -m compileall -q .
python tests/test_transcript_stability_v3_2.py
```

## Known limitation

ASR may still misrecognize brand names (e.g. Quill, ChatGPT, Claude). v3.2 fixes **duplication and merge stability only**, not speech recognition accuracy.
