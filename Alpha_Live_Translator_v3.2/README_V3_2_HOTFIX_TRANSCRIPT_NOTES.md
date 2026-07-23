# Alpha Live Translator v3.2.1 — Transcript Hotfix

## Problem

v3.2 transcript stabilization merged partial fragments into cumulative lines (`previous + current`), producing repeated glued text and inflated word counts (for example ~6,260 words instead of ~927).

## Fix

Conservative decision logic only:

1. Empty text → skip
2. No previous same-speaker segment → add
3. Normalized equal → skip
4. Current contained in previous → skip
5. Previous contained in current → update last segment
6. Current starts with previous (normalized) → update last segment
7. Previous starts with current → skip
8. Otherwise → add new segment

No overlap merge, no fuzzy matching, no `previous + current` concatenation.

## Source of truth

`TranscriptStore` holds finalized segments. The UI textbox is re-rendered from `get_clean_text()` after each accepted segment. Copy and Export also use `get_clean_text()`.

## Version

- `APP_VERSION = 3.2.1`
- `APP_CODENAME = Transcript Hotfix`

## Test

```bash
python -m compileall -q .
python tests/test_transcript_hotfix_v3_2.py
```
