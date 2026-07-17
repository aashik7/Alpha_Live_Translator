# Alpha Live Translator V5 — Meeting Summary Foundation Notes

## What Was Added

Version 5 adds a **local meeting summary foundation** without external AI APIs.

New module `alpha/summary/`:

- `transcript_store.py` — thread-safe `TranscriptStore` for finalized transcript segments
- `summary_service.py` — `SummaryService` for lightweight local summary generation
- `__init__.py` — package exports

Main window integration:

- `record_transcript_segment()` stores accepted transcript lines after duplicate protection
- Translation results are linked in the store when available
- **Meeting Summary** / **Show more** updates the AI Meeting Summary panel with a local MVP summary
- **Clear** also clears the transcript store and summary panel placeholder

## Startup Window Size

Default geometry changed to **900×650**:

```python
self.geometry("900x650")
```

Minimum size is unchanged: **400px** minimum width (`LAYOUT_MIN_WIDTH`).

## Summary Behavior (Local MVP)

- No OpenAI, Claude, Gemini, or other external LLM is used.
- Summary is generated from stored transcript text on the UI thread.
- Empty transcript message:
  `No transcript is available yet. Start listening first.`
- Non-empty transcript summary includes:
  - Key points (first distinct segments)
  - Speakers detected
  - Conversation length estimate (word count)
  - Important repeated terms (simple frequency)

## How to Run

```powershell
cd "AlphaLiveTranslator_V3_UXPolish"
pip install -r requirements.txt
python main.py
```

## Known Limitations

- Summary is **not** true AI summarization — it is a structured local preview.
- No background summary worker yet.
- Translations are matched to transcript lines by text/speaker when possible.
- Action Items, Share, and AI Assistant are **not** included.

## Future Plan

Replace `SummaryService.generate_summary()` with a real LLM summary provider (config-driven API key from `.env`) while keeping `TranscriptStore` as the data source.
