# Alpha Live Translator — Version 3.1 UI Refinement

UI-only release. Backend (audio, transcription, translation) unchanged.

## Modified files

- `alpha/ui/theme.py` — Segoe UI Variable typography, ghost button tokens, section/summary labels
- `alpha/ui/main_window.py` — header layout, sliding Meeting Summary panel, transcript sizing

## Highlights

- Product name: **Alpha** (subtitle: Live Translator)
- Ghost/transparent header controls with language flags
- Meeting Summary slides open below the header (above status bar); close with **×**
- Status bar: listening info only (no Meeting Summary chip)
- Section titles: ◉ Live Transcript / Translation ◎
- Transcript body font: 16px
- Translation panel: "This feature is coming soon."

## Run

```powershell
cd AlphaLiveTranslator_V3_UXPolish
python main.py
```
