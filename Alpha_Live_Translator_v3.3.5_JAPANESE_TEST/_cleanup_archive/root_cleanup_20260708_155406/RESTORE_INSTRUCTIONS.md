# Restore Instructions

If cleanup caused a problem:

1. Copy files back from the archive subfolders to the project root.
2. Do not restore old validation scripts unless you specifically need a historical version.
3. Current active validation: `validate_accuracy_85232.py`
4. Current active smoke test: `runtime_smoke_start_stop_85232.py`
5. Current active app entry: `main.py`

Archive subfolders:
- `old_validation_scripts/` — historical validate_*.py files
- `old_runtime_smoke_scripts/` — historical runtime_smoke_*.py files
- `old_readmes/` — version README notes
- `old_reports_and_outputs/` — old root Alpha output, reports, Transcript.txt
- `old_indexes/` — legacy root index files
- `misc_empty_or_notes/` — empty notes

`troubleshooting/` was not removed. Live evidence remains under `troubleshooting/latest/`.
