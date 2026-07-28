# Alpha Live Translator — Architecture

**Active project:** `Alpha_Live_Translator`  
**Current version:** `3.3.5.5.8.5.26.4.1`  
**Entry point:** `main.py`  
**Frozen infrastructure baseline:** `3.3.5.5.8.5.25.3.3.2.8`

This document describes what each major part of the **current** Japanese TEST codebase does. Older folders under the monorepo root (`AlphaLiveTranslator_V*`, `Alpha_Live_Translator_v3.2`, etc.) are historical snapshots and are not the active product line.

---

## 1. Product overview

Alpha Live Translator is a Windows desktop app for **live Japanese speech-to-text** and optional translation:

1. Capture microphone + system (WASAPI) audio  
2. Mix to mono 16 kHz PCM  
3. Stream to **Deepgram Nova-3** (`language=ja`)  
4. Run a Japanese continuity / accuracy pipeline  
5. Show results in the UI and optionally translate via DeepL  
6. On Stop, finalize canonical transcripts and run artifacts  

Secrets stay in `.env` (never committed). Local runs and evidence live under `troubleshooting/` (also never committed).

---

## 2. Runtime data flow

```
Microphone (sounddevice) ──┐
                           ├──► DeepgramTimelineMixer ──► Deepgram WebSocket (Nova-3)
WASAPI loopback ───────────┘         (16 kHz mono)              │
                                                                ▼
                                         Japanese continuity / accuracy pipeline
                                                                │
                         ┌───────────────────┬──────────────────┼──────────────────┐
                         ▼                   ▼                  ▼                  ▼
                    UI (AlphaApp)     Final artifacts    Stop finalize     Benchmark evidence
                   + DeepL worker      (run folder)     (stop worker)     (gate harness only)
```

| Stage | Responsibility | Primary files |
|-------|----------------|---------------|
| Startup | Crash hooks, troubleshooting paths, recover incomplete runs, launch UI | `main.py` |
| Capture | Mic PCM and WASAPI system audio into queues | `alpha/audio/microphone.py`, `alpha/audio/wasapi.py` |
| Mix | Align sources onto one 16 kHz mono stream | `alpha/audio/timeline_mixer.py`, `processing.py`, `source_gate.py` |
| STT | Send PCM to Deepgram; receive interim/finals | `alpha/transcription/deepgram_client.py`, `alpha/ui/main_window.py` |
| Japanese pipeline | Continuity, boundaries, cleanup, ledger commit | `alpha/transcription/japanese_*.py`, `canonical_transcript_ledger.py` |
| UI / translation | Display text; DeepL off the UI thread | `alpha/ui/main_window.py`, `alpha/translation/*`, `alpha/core/*` |
| Stop | Non-blocking finalize → canonical export | `alpha/utils/stop_finalize_worker.py`, `run_artifacts.py` |

---

## 3. Top-level layout

| Path | Role |
|------|------|
| `main.py` | Application entry: logging hooks, troubleshooting bootstrap, `AlphaApp().mainloop()` |
| `requirements.txt` | Runtime Python dependencies |
| `requirements-lock.txt` | Pinned dependency set for reproducible installs |
| `.env.example` | Template for API keys (copy to `.env` locally) |
| `runtime_environment_contract.json` | Declared runtime expectations |
| `validate_runtime_environment.py` | Checks the machine against the contract |
| `README_CURRENT.md` | Short operational guide (version stamp may lag `constants.py`) |
| `ARCHITECTURE.md` | This file |
| `alpha/` | Application package (audio, STT, UI, utils, …) |
| `tests/` | Automated tests |
| `tools/` | Offline check runners and tool registry |
| `docs/` | Extra documentation / archives |
| `assets/` | Static assets |

**Not uploaded (local only):** `troubleshooting/`, `logs/`, `graphify-out/`, `.env`, `__pycache__/`, audio/PCM, ZIP evidence packages, smoke-test outputs.

---

## 4. Package map — `alpha/`

### 4.1 `alpha/audio/` — capture and mix

| File | What it does |
|------|----------------|
| `microphone.py` | Captures default microphone via sounddevice into a bounded queue |
| `wasapi.py` | Captures system/loopback audio via WASAPI (`pyaudiowpatch`) |
| `timeline_mixer.py` | Real-time timeline mix to mono 16 kHz for Deepgram (`DeepgramTimelineMixer`) |
| `processing.py` | PCM conversion helpers (channels / rate) |
| `source_gate.py` | Echo / overlap gate for meeting-source audio |

### 4.2 `alpha/transcription/` — STT and Japanese pipeline

**Core STT / commit path**

| File / group | What it does |
|--------------|----------------|
| `deepgram_client.py` | Deepgram Nova-3 WebSocket client, reconnect, health |
| `speaker_detection.py` | Speaker / diarization helpers |
| `duplicate_protection.py` | Conservative duplicate transcript filtering |
| `language_pipeline_base.py` | Tk-free language pipeline contract |
| `pipeline_commit_transaction.py` | Atomic commit of ledger + evidence |
| `canonical_transcript_ledger.py` | Authoritative active transcript ledger |
| `transcript_lineage.py` | Lineage tracking and final-export lock |
| `revision_metadata.py`, `stable_line_revision.py`, `stable_revision_decision.py` | Stable-line revision authority |

**Japanese modules**

| Group | Files (pattern) | What they do |
|-------|-----------------|--------------|
| Continuity / boundaries | `japanese_sentence_assembler.py`, `japanese_boundary_stabilizer.py`, `japanese_final_chunk_stabilizer.py`, `japanese_translation_unit_builder.py` | Build stable sentence units from streaming finals |
| Accuracy / cleanup | `japanese_stable_accuracy.py`, `japanese_business_accuracy.py`, `japanese_accuracy_cleaner.py`, `japanese_visible_error_audit.py`, `final_output_cleanup.py` | Business-Japanese cleanup without changing STT wire behavior |
| Domain / IR | `corporate_ir_glossary.py`, `corporate_ir_stable_corrector.py`, `financial_number_safety.py` | Glossary and number-safety helpers |

### 4.3 `alpha/ui/` — desktop UI

| File | What it does |
|------|----------------|
| `main_window.py` | `AlphaApp`: Start/Stop, mixer + Deepgram workers, pipeline hooks, panels |
| `theme.py` | UI theme / design tokens |

### 4.4 `alpha/translation/`, `summary/`, `core/`, config

| Area | Files | What they do |
|------|-------|----------------|
| Translation | `deepl_client.py`, `translation_worker.py`, `language_map.py` | DeepL client, background worker, language codes |
| Summary | `transcript_store.py`, `summary_service.py` | In-memory finalized segments; optional summary |
| Core | `event_bus.py`, `events.py`, `models.py` | Event bus and shared models |
| Resources | `resources/keyterms/default_ja_business.json` | Default Japanese business keyterms resource |
| Config | `config.py` | Loads runtime config and `.env` keys |
| Constants | `constants.py` | `APP_VERSION`, feature flags, modes |
| STT settings | `stt_settings.py` | Canonical Nova-3 timing settings for Japanese |

### 4.5 `alpha/utils/` — infrastructure (grouped)

There are many utility modules. Use this grouping when navigating:

| Group | Representative files | What they do |
|-------|----------------------|----------------|
| Run identity / artifacts | `run_identity.py`, `run_artifacts.py`, `troubleshooting_paths.py`, `path_types.py`, `latest_completed_live_run.py` | Create/resolve run folders and write canonical outputs |
| Stop finalize | `stop_finalize_worker.py`, `canonical_finalize.py`, `final_artifact_authority.py`, `strict_stop_evidence.py` | Non-blocking Stop worker and finalize evidence |
| Accuracy / stage capture | `accuracy_stage_capture.py`, `accuracy_evidence_export.py`, `japanese_accuracy_log.py` | Three-stage (raw/stable/final) capture and logs |
| Multidomain gate evidence | `multidomain_gate_evidence.py` | Benchmark-only evidence helpers (audio delivery JSONL, request capture, pre-score gate) — inactive in normal runs |
| Logging / health | `logging_utils.py`, `crash_guard_log.py`, `flight_recorder.py`, `session_watchdog.py`, `live_runtime_metrics.py` | Diagnostics and health telemetry |
| UI thread safety | `ui_event_bus.py`, `tk_thread_guard.py`, `ui_thread_guard.py`, `queues.py` | Safe cross-thread UI updates |
| Text helpers | `cjk_text.py` | CJK-aware text utilities |

---

## 5. Root harness scripts (benchmark / offline)

These are **program files** for gates and regressions. They are not required to launch the live app, but they are part of the engineering toolchain.

| Prefix | Purpose |
|--------|---------|
| `prepare_*` | Build fixtures / prep benchmark inputs |
| `run_*` | Orchestrate a gate or offline pipeline |
| `score_*` | Score transcripts (CER / accuracy) vs reference |
| `verify_*` | Independently verify artifacts and seals |
| `regression_*` | Deterministic offline regression suites |

**Current multidomain gate family (85262 / 85264):**

| Script | Role |
|--------|------|
| `prepare_multidomain_gate_85262.py` | Prepare multidomain gate fixtures |
| `run_multidomain_gate_85262.py` | Live/offline multidomain gate orchestrator (child-run binding) |
| `score_multidomain_gate_85262.py` | Fail-closed scorer (pre-score evidence gate) |
| `verify_multidomain_gate_85262.py` | Gate verifier |
| `regression_multidomain_gate_85262.py` / `*_852622.py` / `*_85263.py` | Gate regressions / hard-fix |
| `run_multidomain_evidence_repair_85264.py` | Offline evidence-repair / pre-live orchestrator |
| `regression_multidomain_evidence_repair_85264.py` | Physical fixture regressions for evidence repair |
| `verify_multidomain_evidence_repair_85264.py` | Stdlib-only independent verifier |

Other numbered `852533*` / `85261*` scripts belong to earlier accuracy, packaging, and Phase-1 closure work.

---

## 6. How to run

```powershell
cd "Alpha_Live_Translator"
pip install -r requirements.txt
copy .env.example .env
# Edit .env: set DEEPGRAM_API_KEY and (optional) DEEPL_API_KEY
python main.py
```

Optional offline checks:

```powershell
python tools/run_all_current_checks.py
python validate_runtime_environment.py
```

---

## 7. GitHub upload policy (what belongs in the repo)

**Include**

- `main.py`, `alpha/**/*.py`, harness `*.py` scripts  
- `requirements*.txt`, `.env.example`, `.gitignore`  
- `tests/`, `tools/`, `docs/` (non-secret), `assets/`  
- This `ARCHITECTURE.md` and short READMEs  

**Exclude**

| Pattern | Reason |
|---------|--------|
| `.env` | API secrets |
| `troubleshooting/` | Run logs, evidence ZIPs, smoke fixtures |
| `*.log`, `logs/` | Runtime logs |
| `graphify-out/` | Regenerable knowledge graph |
| `*.wav`, `*.pcm`, other audio | Capture blobs |
| `*.zip`, large dumps | Evidence packages |
| `__pycache__/`, `.venv/` | Build/cache |

---

## 8. Related monorepo folders (historical)

| Folder | Status |
|--------|--------|
| `Alpha_Live_Translator` | **Active** — use this |
| `Alpha_Live_Translator_v3.3`, `v3.2`, `AlphaLiveTranslator_V3_*`, `V0`–`V2` | Historical / reference only |

When contributing, change the **Japanese TEST** tree unless you are intentionally maintaining an archive snapshot.
