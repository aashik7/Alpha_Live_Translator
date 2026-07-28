# Alpha Live Translator

Clean workspace for the **current** Alpha Live Translator desktop app (JA ↔ EN live speech translation).

## Active project (share this)

| Item | Path |
|------|------|
| App folder | [`Alpha_Live_Translator`](Alpha_Live_Translator/) |
| Entry point | `Alpha_Live_Translator/main.py` |
| Architecture | [`ARCHITECTURE.md`](Alpha_Live_Translator/ARCHITECTURE.md) |
| Run notes | [`README_CURRENT.md`](Alpha_Live_Translator/README_CURRENT.md) |
| Dependencies | `Alpha_Live_Translator/requirements.txt` |

### Run locally

```powershell
cd Alpha_Live_Translator
pip install -r requirements.txt
copy .env.example .env
# Put Deepgram + DeepL API keys in .env
python main.py
```

## Current file structure

```
Alpha_Translator V 1.0/
├── README.md                          ← you are here
├── .gitignore                         ← excludes secrets, runs, archive, audio, ZIPs
├── .gitattributes
│
├── Alpha_Live_Translator/             ← ONLY active app (share / Git this)
│   ├── main.py                        ← start here
│   ├── requirements.txt
│   ├── .env.example                   ← copy to .env (do not share .env)
│   ├── ARCHITECTURE.md
│   ├── README_CURRENT.md
│   ├── alpha/                         ← app source (UI, STT, translation, audio)
│   ├── assets/
│   ├── docs/
│   ├── tests/
│   ├── tools/
│   └── troubleshooting/               ← local run evidence (gitignored)
│
├── Alpha_Benchmark_References/        ← optional spoken-reference texts for scoring
│
└── _archive/                          ← local only — not for Git / boss share
    ├── legacy_versions/               ← old app trees (V0–V3.3, former long-named folder)
    ├── legacy_root_files/             ← early prototypes (alpha.py, alpha_V2–V4, …)
    ├── local_run_data/                ← old experiments, runs, ZIPs, logs
    └── README.md
```

### What changed

- Renamed active app to short path: **`Alpha_Live_Translator`**
- Former folder `Alpha_Live_Translator_v3.3.5_JAPANESE_TEST` was moved under `_archive/legacy_versions/`
- Older versions and bulky run data are under `_archive/` (same app behavior; use the short path)

## What to put in Git later

Track only source, tests, docs, and tools under **`Alpha_Live_Translator`**.

**Do not upload:** `.env`, `troubleshooting/` run evidence, audio, ZIPs, `graphify-out/`, or `_archive/`.

The root [`.gitignore`](.gitignore) already excludes those.

## Archive (local only)

Nothing in [`_archive/`](_archive/) is required to run the current app. You can delete `_archive/` later when you no longer need backups.

| Subfolder | Contents |
|-----------|----------|
| `legacy_versions/` | Old Alpha trees + archived `Alpha_Live_Translator_v3.3.5_JAPANESE_TEST` |
| `legacy_root_files/` | Early single-file scripts and old root leftovers |
| `local_run_data/` | Past experiments, accuracy dumps, old runs, ZIPs |

## Sharing checklist for your boss

1. Share **`Alpha_Live_Translator`** (+ this `README.md` / `.gitignore`).
2. Confirm `.env` is **not** included (share `.env.example` instead).
3. Skip `_archive/` and any `troubleshooting/` run folders.
4. Push to Git when you are ready (this cleanup does not create or push a remote).
