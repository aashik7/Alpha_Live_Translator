# Alpha Translator V 1.0

Monorepo of Alpha Live Translator desktop app versions.

## Active project

Use **`Alpha_Live_Translator_v3.3.5_JAPANESE_TEST`** (current app version `3.3.5.5.8.5.26.4.1`).

- Architecture (what each file/group does): [`Alpha_Live_Translator_v3.3.5_JAPANESE_TEST/ARCHITECTURE.md`](Alpha_Live_Translator_v3.3.5_JAPANESE_TEST/ARCHITECTURE.md)
- Run guide: [`Alpha_Live_Translator_v3.3.5_JAPANESE_TEST/README_CURRENT.md`](Alpha_Live_Translator_v3.3.5_JAPANESE_TEST/README_CURRENT.md)
- Entry point: `Alpha_Live_Translator_v3.3.5_JAPANESE_TEST/main.py`

```powershell
cd Alpha_Live_Translator_v3.3.5_JAPANESE_TEST
pip install -r requirements.txt
copy .env.example .env
python main.py
```

## What is not in this repository

Logs, run evidence (`troubleshooting/`), audio captures, `.env` secrets, and generated graphs (`graphify-out/`) are intentionally excluded. Only program source, tests, tools, and docs are tracked.

## Historical folders

Older `AlphaLiveTranslator_V*` / `Alpha_Live_Translator_v3.2` / `v3.3` trees are retained as version history snapshots, not the active line.
