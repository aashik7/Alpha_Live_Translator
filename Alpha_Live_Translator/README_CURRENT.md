# Alpha Live Translator — Current Project Guide

- **Phase 1 patch version:** `3.3.5.5.8.5.25.3.3.2.5` — Project Normalization & Offline Hardening
- **Authoritative registry:** `troubleshooting/PROJECT_STATE.json`
- **Authoritative run:** `troubleshooting/runs/v3.3.5.5.8.5.25.3.3.1-20260714-111519`
- **Authoritative reference:** `troubleshooting/accuracy_benchmark/reference_transcripts/test01.txt`
- **Authoritative Final SHA-256:** `6e70dd171862527da2f2de0305ab82154cf9c1591b73860e4fd75e06f570c178`
- **Main entry:** `main.py`
- **Phase 1 offline runner:** `run_phase1_project_normalization_85253325.py`
- **Phase 1 regression (60):** `regression_phase1_project_normalization_85253325.py`
- **Current tools registry:** `tools/TOOLS_CURRENT.json`
- **Offline checks:** `tools/run_all_current_checks.py`
- **Runtime contract:** `runtime_environment_contract.json` + `validate_runtime_environment.py`
- **Canonical STT settings:** `alpha/stt_settings.py`
- **Scoring:** pass `--run-folder` + `--reference` (or explicit `--raw/--stable/--final/--reference`); silent `latest_*` fallback removed

Historical README snapshots live under `docs/archive/`. Phase 2 findings remain pending (bounded queues/writer lifecycle; silent-exception remediation). Structural splits/monkey-patch replacement are deferred.
