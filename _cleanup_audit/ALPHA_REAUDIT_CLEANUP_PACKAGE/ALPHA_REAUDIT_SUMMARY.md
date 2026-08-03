# Alpha Live Translator — New Project Re-Audit

## Result

The new archive is complete and readable.

- Previous logical project size: **2.518 GiB**
- New logical project size: **303.89 MiB**
- Reduction already achieved: **2.221 GiB (88.21%)**
- Additional Delete Now recovery: **173.82 MiB**
- Additional recovery after validation/recreation: **82.32 MiB**
- Expected lean-development workspace: approximately **48 MiB**
- Expected clean source-share package: approximately **3.7–5 MiB**

## Main remaining clutter

1. `Alpha_Live_Translator\troubleshooting` — **183.13 MiB**
2. `.venv` — **66.2 MiB**
3. `.git` — **49.13 MiB**, but Git itself must remain
4. Cursor cache inside `.git\cursor` — removable
5. Versioned legacy runner/regression scripts and intermediate repair reports — remove after the current repair is committed and validated

## Recommended lean-development keep set

Keep:

```text
Alpha_Translator V 1.0\
├── .git\                         # keep Git, remove only cursor cache/stale worktree
├── .gitignore
├── .gitattributes
├── README.md
├── CLAUDE.md                      # repair incomplete placeholder
├── ROOT_CAUSE.md
├── REPAIR_PLAN.md
├── Alpha_Benchmark_References\
└── Alpha_Live_Translator\
    ├── main.py
    ├── alpha\
    ├── assets\
    ├── tests\
    ├── tools\
    ├── requirements.txt
    ├── requirements-lock.txt      # add deepl pin
    ├── .env                       # local only; rotate keys
    ├── .env.example
    ├── .python-version
    ├── ARCHITECTURE.md
    ├── README_CURRENT.md
    └── troubleshooting\
        ├── latest\
        ├── runs\v3.3.5.5.8.5.26.5.3-20260731-145342\
        ├── PROJECT_STATE.json
        ├── RETENTION_POLICY.json
        └── current evidence indexes
```

## Important decisions

- Delete all old run folders and old ZIP/evidence packages listed in the Excel `Delete Now` sheet.
- Keep only the current run `v3.3.5.5.8.5.26.5.3-20260731-145342` during the present debugging cycle.
- Delete the current run's `audio_temp` after its recorded **2026-07-31 16:53 JST** expiry when no further audio analysis is required.
- Delete `.venv` only after updating `requirements-lock.txt` with `deepl==1.30.0` and verifying a fresh environment.
- Remove `.cursor` and `.git\cursor`; they are not Alpha runtime dependencies.
- Run `git worktree prune` to remove the stale Trae worktree record.
- Do not bulk-delete modules under `alpha\`; only three unreferenced candidates are listed for Windows validation first.
- The active `.env` contains live-looking credentials and was uploaded again. Rotate Deepgram and DeepL credentials.
