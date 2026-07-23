# Alpha Live Translator — Version 3.1 UI Recovery

Recovered from `AlphaLiveTranslator_V3_UXPolish` backend with right-side Meeting Summary panel restored.

## Run

```powershell
cd AlphaLiveTranslator_V3_1_UIRecovery
python main.py
```

## UI changes (main_window.py + theme.py only)

- Right-side Meeting Summary panel (~30% width)
- Dark glass header/secondary buttons (no transparent controls)
- Icons left of text (flags, ▤, ◉, ◎)
- Instant show/hide via `grid()` / `grid_remove()` — no slide animation
- Product name: **Alpha**
