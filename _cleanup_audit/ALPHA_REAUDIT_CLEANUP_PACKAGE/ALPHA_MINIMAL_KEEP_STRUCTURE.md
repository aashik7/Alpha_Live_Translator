# Alpha Minimal Keep Structure

## Lean development structure

This is the recommended daily-development structure. It retains Git, current source, tests and only the latest evidence.

```text
Alpha_Translator V 1.0/
├── .git/
├── .gitignore
├── .gitattributes
├── README.md
├── CLAUDE.md
├── ROOT_CAUSE.md
├── REPAIR_PLAN.md
├── Alpha_Benchmark_References/
└── Alpha_Live_Translator/
    ├── main.py
    ├── alpha/
    ├── assets/
    ├── tests/
    ├── tools/
    ├── requirements.txt
    ├── requirements-lock.txt
    ├── .env
    ├── .env.example
    ├── .python-version
    ├── ARCHITECTURE.md
    ├── README_CURRENT.md
    └── troubleshooting/
        ├── latest/
        ├── runs/
        │   └── v3.3.5.5.8.5.26.5.3-20260731-145342/
        ├── PROJECT_STATE.json
        ├── RETENTION_POLICY.json
        ├── latest_accuracy_evidence_index.json
        └── latest_accuracy_evidence_index.zip
```

## Remove from the daily project

- `.venv/` after a clean environment recreation test
- `.cursor/`
- `.git/cursor/`
- stale Git worktree metadata using `git worktree prune`
- root-level stray `troubleshooting/`
- all old troubleshooting runs and historical ZIPs
- generated debug/log folders
- generated Python inventory CSV
- stale Cursor report
- version-specific `_852...py` runners after the current repair commit
- one-off patch/repair scripts after validation
- intermediate `TASK_*` reports after consolidation
- Graphify `.agents/` only when Graphify is not used

## Source-share structure

For sharing source without Git history or local evidence, include only:

```text
README.md
.gitignore
.gitattributes
Alpha_Benchmark_References/
Alpha_Live_Translator/
  main.py
  alpha/
  assets/
  tests/
  tools/
  requirements.txt
  requirements-lock.txt
  .env.example
  .python-version
  ARCHITECTURE.md
  README_CURRENT.md
```

Never include `.env`, `.venv`, `.git`, troubleshooting evidence, logs, audio or ZIP packages.
