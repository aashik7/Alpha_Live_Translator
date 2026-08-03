# Task 4A — Finalisation & Evidence Integrity Audit

Read-only investigation. No files modified.

**Context notes:**
- `TASK_1B_CHANGES.md` was readable this time (unlike the last several
  audits in this engagement, where it and several sibling docs were
  missing from disk). Its confirmation that `pipeline_commit_transaction.py`
  already correctly derives `success=ledger_applied` (line 473) as a
  per-record outcome flag, independent of evidence/metrics write failure,
  is used below as a positive contrast against this task's findings, which
  are about the higher-level, per-*run* `final_status` — a different,
  broken mechanism.
- `ROOT_CAUSE.md`'s current content is Task-1-scoped and does not use
  "P0/P1" section labels at all (no section is numbered that way). This is
  the same document-instability pattern flagged in every prior audit in
  this engagement. Read in full anyway; nothing in it bears directly on
  Phase 4 (it only covers identity/atomicity, Task 1 scope).
- `REPAIR_PLAN.md` Phase 4 was read in full (already known from this
  engagement's prior tasks); its exact requirements are quoted throughout
  below.

**Scope decision on the 60-file grep hit set:** the required grep terms
matched 60 files. Of those, ~35 are one-off, numbered root-level scripts
(`run_*_85253324.py`, `validate_*_852533.py`, `regression_*.py`,
`verify_*.py`, `build_*.py`, `score_*.py`, `package_*.py`, etc.) sitting
outside the `alpha/` package. Tracing the actual live entry point
(`alpha/utils/stop_finalize_worker.py`, confirmed as the only finalize
worker `main_window.py` imports — see Item 1) and everything it imports,
transitively, touches **zero** of those root-level scripts. They match the
grep terms because they are historical troubleshooting/validation tools
that reference the same vocabulary (`final_status`, `evidence package`,
etc.) for their own standalone checks, not because they participate in the
live Stop/finalize call graph. They were not read in full; this
determination is stated here rather than silently assumed. Everything
under `alpha/` that matched a grep term was read in full.

---

## 1. Required finalization steps — does failure block `final_status=completed`?

**CONFIRMED — no. Not one of the ten required steps can currently produce
`final_status = "failed"`. The live code path never writes that string as a
status value anywhere.**

The live entry point is `alpha/utils/stop_finalize_worker.py::begin_stop_from_ui`
(`stop_finalize_worker.py:1529`), called from `main_window.py:7701`
(`from alpha.utils.stop_finalize_worker import begin_stop_from_ui`) — the
only finalize-worker import in `main_window.py`. It spawns
`_run_finalize_worker` (`stop_finalize_worker.py:912`) on a background
thread.

`_run_finalize_worker` runs every required step through `run_timed_step`
(`stop_finalize_worker.py:145-220`), which has one job: **run a step,
catch any exception, record it, and always return a bool — never let a
step's failure propagate.** Almost none of the ~20 `run_timed_step(...)`
call sites in the live path capture that returned bool at all:

```python
run_timed_step(host, "stop_audio_capture", lambda: _block_audio_capture(host))
run_timed_step(host, "stop_audio_producers", lambda: _stop_audio_producers(host))
run_timed_step(host, "canonical_pipeline_finalize", _canonical_finalize)
run_timed_step(host, "write_final_alpha", _write_final_export)
```
(`stop_finalize_worker.py:963-1147`, representative sample) — the return
value is discarded every time. A step can time out or raise, get logged via
`freeze_guard_log("STOP_FINALIZE_STEP_FAILED"/"STOP_FINALIZE_STEP_TIMEOUT", ...)`,
and `_run_finalize_worker` proceeds to the next step regardless.

The step-level failure IS tracked internally (`_stop_state["failed_steps"]`,
`stop_finalize_worker.py:196-198`) and surfaced as a computed field,
`stop_finalize_failed`, inside `build_stop_finalize_summary()`
(`stop_finalize_worker.py:490`: `"stop_finalize_failed": len(failed_steps) > 0`).
**But that computed value is never used to gate what actually gets persisted
as `final_status`.** Tracing every place `final_status` is assigned a
literal value in the live path:

- `stop_finalize_worker.py:414` (`_write_minimal_runtime_artifacts`, called
  unconditionally at `stop_finalize_worker.py:1210`, i.e. on the live path
  of *every* Stop): `"final_status": "completed_with_warnings"` — **a hardcoded
  string literal**, not derived from `_stop_state["failed_steps"]` or
  `stop_finalize_failed` at all.
- `alpha/utils/run_artifacts.py:926-928` (`finalize_live_run_status_completed`,
  called from the live background path — see Item 2): `final_status = "completed"`
  then `if stop_summary and stop_summary.get("stop_finalize_timed_out"): final_status = "completed_with_warnings"`
  — checks **only** `stop_finalize_timed_out`, never `stop_finalize_failed`.
- `alpha/utils/evidence_pointer_finalize.py:59-61`
  (`finalize_evidence_pointers_completed`, scheduled from the live path —
  Item 2): identical pattern, `final_status` derived only from
  `stop_finalize_timed_out`.

A `grep` for the literal string `"failed"` as a `final_status`/`status`
value assignment inside `stop_finalize_worker.py` returns exactly one hit
(`stop_finalize_worker.py:493`, `"failed": len(failed_steps) > 0`) and it is
a diagnostic dict key inside `build_stop_finalize_summary`'s return value —
**never written into `LIVE_RUN_STATUS.json`, `RUN_MANIFEST.json`, or any
persisted artifact's `final_status`/`status` field.** The literal string
`final_status = "failed"` does not occur anywhere in the live path.

**Per-required-step breakdown** (REPAIR_PLAN.md's ten items):

| Required step | Where it runs | Failure currently... |
|---|---|---|
| Audio summary | `drain_audio_queue` step (`stop_finalize_worker.py:966-973`, `_drain_outgoing_audio_queue`, `:568-638`) | Adds to `failed_steps` on remaining-queue timeout (`:621-625`) — **but `failed_steps` never gates `final_status`** (see above). |
| Raw event persistence | `deepgram_graceful_stop` step (`:979-983`) + `accuracy_stage_capture.py`'s `record_raw_deepgram_final` (called from `japanese_final_chunk_stabilizer.py:148`, wrapped in its own `try/except`, `:173-179`) | Silently swallowed — write failure just logs `RAW_EVENT_ID_ASSIGNMENT_FAILED` and sets `force_append_only`; never fails the run. |
| Utterance reconstruction | Not a distinct named step in `_run_finalize_worker` — implicitly covered by the transcription pipeline itself, already frozen/Task-1/2 territory | N/A to this audit's file list. |
| Canonical ledger validation | `canonical_pipeline_finalize` step (`:1110-1119`) → `canonical_finalize.py::finalize_canonical_pipeline` | **Silently swallowed twice over** — see Item 2's #1. |
| Stable export | Inside `finalize_canonical_pipeline` (`write_stable_active_stage_artifacts`, `canonical_finalize.py:107`) | Wrapped in the same outer `try/except` as the whole function (`canonical_finalize.py:59-191`) — any exception anywhere in that ~140-line block is swallowed identically. |
| Final export | `write_final_alpha` step (`:1121-1147`, `_write_final_export`) | Actually checked at this one call site — `if path is not None: ... else: freeze_guard_log("FINAL_ALPHA_ATOMIC_WRITE_FAILED")` (`:1143-1145`) — **but this local failure flag is not threaded into `final_status` either**; it only sets `_evidence_flags["alpha_output_written"] = False` and logs. |
| Translation drain | `translation_worker_shutdown` step (`:1073-1108`) | Exception caught and logged (`:1101-1106`); never fails the run. |
| Loading-state drain | Same translation shutdown step; loading-state completion is internal to `TranslationWorker.shutdown()` (Task 3 territory, already validated separately in Task 3C) | Not separately checked here. |
| Run manifest | `finalize_run_manifest` (`troubleshooting_paths.py`, called from both the dead code and the live `evidence_pointer_finalize.py` path) | Manifest write itself can fail silently (see Item 2). |
| Evidence package | `_run_evidence_package_worker` (`stop_finalize_worker.py:808-909`) | **Never called at all — confirmed dead code, see Item 5.** The actual live "evidence package" work is the greatly reduced `_write_minimal_runtime_artifacts` (`:373-421`) plus the backgrounded `evidence_pointer_finalize.py` pass. |

**Confidence: CONFIRMED**, based on direct reading of every step's
implementation and its call site in the live path.

---

## 2. Broad try/except blocks that swallow failure instead of propagating it

**CONFIRMED — this is the dominant pattern throughout the finalize worker
and its collaborators; not an isolated case.**

1. **`canonical_finalize.py:59-191`** — `finalize_canonical_pipeline`'s
   entire body (ledger freeze, export payload, coverage report, lineage
   reconciliation, stage manifest write — i.e. most of REPAIR_PLAN's
   "canonical ledger validation" and "Stable export" requirements) is one
   `try: ... except Exception as exc: result["error"] = ...; return result`
   block (`:59`, `:188-190`). The caller
   (`stop_finalize_worker.py::_canonical_finalize`, `:1110-1119`) only logs
   `CANONICAL_LEDGER_FROZEN` vs `CANONICAL_LEDGER_FREEZE_FAILED` based on
   `result.get("ok")` — **it never raises, never appends to
   `_stop_state["failed_steps"]` itself** (that only happens if
   `finalize_canonical_pipeline` raises past its own try/except, which by
   construction it never does). So even a total canonical-validation
   failure is invisible to `run_timed_step`'s exception-catching mechanism
   two layers up — `run_timed_step` sees no exception and marks the step
   `_step_completed[...] = True`.
2. **`stop_finalize_worker.py::run_timed_step` itself, `:162-167`** — by
   design: `def _target(): try: func(); result["done"]=True except Exception as exc: result["error"]=exc`. This is REPAIR_PLAN's literal
   description: "the current finalisation worker contains broad
   exception-handling paths, so final status must be derived from explicit
   required-step results rather than simply reaching the end of Stop." The
   mechanism described as the *problem* in the plan is exactly what's
   still here, unchanged.
3. **`stop_finalize_worker.py:1503-1517`** — the outermost `try/except` in
   `_run_finalize_worker` around the *entire* ordered sequence: any
   uncaught exception anywhere in Stop just logs `STOP_MINIMAL_FAILED` /
   `STOP_FINALIZE_WORKER_FAILED` and calls `_queue_final_ui_update(host, timed_out=True)`
   — it does **not** write `final_status = "failed"` anywhere; the UI is
   simply told "timed out."
4. **`evidence_pointer_finalize.py:40-131`** — `finalize_evidence_pointers_completed`'s
   entire body wrapped in one `try/except Exception as exc: ... result["error"] = str(exc)`
   (`:125-131`); individual sub-writes inside it (`finalize_live_run_status_completed`,
   `finalize_run_manifest`) are *also* each individually wrapped in their
   own `try/except: pass` (`:72-83`, `:85-96`) — a failure in either is
   completely invisible even to this function's own `result["error"]`.
5. **`stop_finalize_worker.py:373` (`_write_minimal_runtime_artifacts`)** —
   not wrapped in a blanket try/except itself, but every helper it calls
   internally is (`_write_core_live_status`, `:348-370`, wraps its entire
   body in `try/except: pass`).
6. Numerous smaller instances throughout `_run_finalize_worker`
   (`stop_finalize_worker.py:1151-1160`, `:1162-1171`, `:1181-1201`,
   `:1203-1208`) — audio-temp flush, stall classification, alias-sync,
   accuracy-evidence export are each wrapped in `try/except: pass` or
   `try/except Exception: freeze_guard_log(...)`, none of which touch
   `final_status`.

**Confidence: CONFIRMED.**

---

## 3. Where `final_status` is actually decided

**CONFIRMED — it depends on reaching a hardcoded write, or at best on a
single flag (`stop_finalize_timed_out`), never on "ALL required steps
succeeded."**

Three separate, independent places compute a `final_status`-shaped value
for the *same* run, and none of them consult `_stop_state["failed_steps"]`
/ `stop_finalize_failed`:

1. **`stop_finalize_worker.py:414`** (live, synchronous, runs on every
   Stop) — hardcoded literal `"completed_with_warnings"`, no computation at
   all.
2. **`run_artifacts.py:905-950+` `finalize_live_run_status_completed`**
   (live, called from the background evidence-pointer pass — see below) —
   `final_status = "completed"` unless `stop_finalize_timed_out`, and
   separately **hardcodes** `"stop_finalize_failed": False` into the
   payload it writes (`run_artifacts.py:948`) regardless of the real value
   passed in via `stop_summary`.
3. **`evidence_pointer_finalize.py:59-61`** (live, same background pass) —
   identical `stop_finalize_timed_out`-only derivation, independently
   re-implemented.

The actual sequencing on a normal Stop: `_run_finalize_worker` runs its
ordered steps → calls `_write_minimal_runtime_artifacts` (hardcoded
`completed_with_warnings`, synchronous, `:1210`) → sets
`_stop_state["finalize_completed"] = True` (`:1211-1212`, this is *only*
"the worker function reached this line," not "all steps succeeded" — it is
set unconditionally, not inside any success check) → builds
`stop_summary` and logs it → **returns at `stop_finalize_worker.py:1258`**
(see Item 5 for why everything after this point, including the properly
`stop_finalize_timed_out`-aware `final_status` logic at `:1441-1444`, is
dead code) → schedules
`evidence_pointer_finalize.py::schedule_evidence_pointer_finalization_background`
in a **separate daemon thread** (`:1244-1253`), which independently
re-derives `final_status` from `stop_finalize_timed_out` alone and
overwrites `RUN_MANIFEST.json`/latest-pointers a second time, asynchronously,
sometime after the synchronous worker has already returned.

So: `final_status` is decided by "did any step *time out*" (a narrow
subset of "did every step succeed"), computed twice more, independently,
in a background thread that races with (and overwrites) the synchronous
worker's own hardcoded value — never by "ALL required steps succeeded,"
and never by "packaging completing" either, since the actual "evidence
package worker" (`_run_evidence_package_worker`) is dead code (Item 5) and
never runs.

**Confidence: CONFIRMED.**

---

## 4. Evidence stream separation — do the five required streams exist?

**CONFIRMED — they do not exist. A different, unrelated evidence
architecture exists instead.**

Grepping the exact filenames `provider_events.jsonl`, `utterance_decisions.jsonl`,
`canonical_commits.jsonl`, `translation_jobs.jsonl`, `ui_events.jsonl`
across the entire repository returns **zero matches** for four of the five;
`canonical_commits` appears only as an in-memory counter key
(`utterance_lifecycle.py:256`, `:1171`, `self._stats["canonical_commits"]`),
never as a filename. REPAIR_PLAN.md's Phase 4 requirement ("Use separate
immutable streams: provider_events.jsonl, utterance_decisions.jsonl,
canonical_commits.jsonl, translation_jobs.jsonl, ui_events.jsonl") was
never implemented under those names.

What exists instead is a differently-designed, accuracy-benchmarking-focused
evidence system in `accuracy_stage_capture.py`, with its own filename
mapping (`accuracy_stage_capture.py:111-134`):
```python
"raw_deepgram": "raw_deepgram.txt",
"raw_provider_events": "raw_provider_events.jsonl",   # closest analog to provider_events.jsonl
"stable_assembler_events": "stable_assembler_events.jsonl",
"stable_events": "stable_events.jsonl",
...
```
This is a *stage-comparison* system (raw vs. assembler vs. final, for CER
scoring) — not the *ownership-domain* separation (provider / decision /
commit / translation / UI) REPAIR_PLAN.md specifies. The two are not
interchangeable: e.g. there is no file that isolates "utterance decisions"
(HOLD/EXTEND/COMMIT choices) or "UI events" as their own stream anywhere.

**Synthetic-event leakage into the closest analog
(`raw_provider_events.jsonl`):** the one real ingestion path that writes to
it, `japanese_final_chunk_stabilizer.py:148`
(`record_raw_deepgram_final(...)`), **is** guarded — a synthetic-reentry
check runs first and short-circuits before the write:
```python
# japanese_final_chunk_stabilizer.py:105-115
if meta_check.get("synthetic_record") or meta_check.get("synthetic_lineage"):
    jp_accuracy_log("SYNTHETIC_REENTRY_BLOCKED", ...)
    return True
```
(this is the Task 2A/2B fix, re-confirmed still in place and still guarding
this call site.) So for the *files that do exist*, synthetic leakage into
the raw-events stream is closed. The broader Phase 4 requirement — five
named, ownership-separated streams — simply isn't there to leak into or
protect in the first place.

**Confidence: CONFIRMED** (both the absence of the required files, and the
synthetic-reentry guard's presence on the closest existing analog).

---

## 5. Second files with overlapping finalization/evidence responsibility

**CONFIRMED — multiple, including one case of dead code duplicating live
code inside the *same file*.**

1. **`stop_finalize_worker.py:808-909` `_run_evidence_package_worker`
   vs. the live `_write_minimal_runtime_artifacts` (`:373-421`) +
   `evidence_pointer_finalize.py` pair** — `_run_evidence_package_worker`
   is a complete, sophisticated evidence-package implementation (run
   consistency check, full artifacts-index write, upload zip creation,
   `finalize_live_run_status_completed`, `finalize_latest_pointers`,
   `finalize_run_manifest` — everything REPAIR_PLAN's "evidence package"
   step describes) **that is never called anywhere** (confirmed by grep:
   its only occurrence in the file is its own `def` line). The function
   that actually runs live, `_write_minimal_runtime_artifacts`, is a much
   thinner, hardcoded-status stand-in for it. Two implementations of the
   same responsibility, in the same file, one dead.
2. **`stop_finalize_worker.py:1260-1465` (the dead tail of
   `_run_finalize_worker`, after the `return` at `:1258`) vs. the live
   `evidence_pointer_finalize.py`** — the dead code's own `_run_artifacts`
   inner function (`:1304-1463`) independently re-implements essentially
   the same "write final export → reconcile segment counts → write
   artifacts index → create upload package → finalize live status →
   finalize manifest/pointers" sequence that `evidence_pointer_finalize.py`
   performs live, in the background. The dead version's `final_status`
   derivation (`:1442-1444`) is *more correct* than the live version's
   (it at least checks `stop_summary.get("stop_finalize_timed_out")` in
   the same place it computes the final write) — but it never runs.
3. **`run_artifacts.py::finalize_live_run_status_completed` vs.
   `evidence_pointer_finalize.py::finalize_evidence_pointers_completed`**
   — both independently compute `final_status` from
   `stop_finalize_timed_out` and both write overlapping
   fields (`stop_finalize_completed`, `stop_finalize_failed`) to
   `LIVE_RUN_STATUS.json`/`RUN_MANIFEST.json`, from two different call
   sites, with no single owner.
4. **`alpha/utils/final_status_reconciliation.py`** — a fourth, distinct
   file, explicitly documented as read-only ("does not rewrite
   LIVE_RUN_STATUS," `:1`). It independently *re-derives* a
   `stop_finalize_failed` value by reading it back out of
   `LIVE_RUN_STATUS.json` (`final_status_reconciliation.py:267`,
   `:340-346`) — but since every writer of that file hardcodes
   `stop_finalize_failed: False` (Items 1-3), this reconciliation tool is
   structurally unable to ever detect a real failure: it is downstream of
   three writers that already discarded the information it's trying to
   check. This is a naming/responsibility collision the same shape as the
   Task 2D lesson (a shared-sounding concept — "is this run's stop
   finalize considered failed" — computed independently in four separate
   files with no single source of truth, three of which discard the input
   the fourth needs).
5. **`alpha/utils/strict_stop_evidence.py`, `alpha/utils/canonical_acceptance_state.py`,
   `alpha/utils/single_authority_packaging.py`, `alpha/utils/session_forensics.py`** —
   matched the grep terms but were not read in full for this pass (see
   scope decision above); their names alone (`strict_stop_evidence`,
   `single_authority_packaging`) suggest they may be *further* overlapping
   authorities in the same space. **NEEDS_REVIEW** — flagged for the 4B
   implementation task to check before deciding whether Item 5's list
   above is exhaustive.

**Confidence: CONFIRMED** for items 1-4 above (each independently
verified by direct reading); **NEEDS_REVIEW** for item 5's unread files.

---

## Files touched in 4B

Expected files a follow-up implementation task will need to touch, based on
this audit — **not yet modified, read-only task**:

- `Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py` — the core
  fix belongs here: `run_timed_step`'s return value must actually gate
  `_stop_state["failed_steps"]`-driven `final_status` computation; the
  dead code after `stop_finalize_worker.py:1258` needs a decision (revive
  the more-correct logic it contains, or remove it — it should not stay as
  silently-unreachable code either way); `_run_evidence_package_worker`
  (`:808-909`) needs the same disposition decision.
- `Alpha_Live_Translator/alpha/utils/run_artifacts.py` —
  `finalize_live_run_status_completed`'s hardcoded `stop_finalize_failed: False`
  (`:948`) and `stop_finalize_timed_out`-only `final_status` derivation
  (`:926-928`) are a direct fix target.
- `Alpha_Live_Translator/alpha/utils/evidence_pointer_finalize.py` — same
  `stop_finalize_timed_out`-only derivation (`:59-61`); likely candidate to
  become the *single* authority once the duplication with `run_artifacts.py`
  is resolved, or vice versa — needs a decision, not two independent
  implementations.
- `Alpha_Live_Translator/alpha/utils/canonical_finalize.py` — the
  all-swallowing outer `try/except` (`:59-191`) needs its `result["ok"]`
  value actually threaded back into step failure tracking, not just logged.
- `Alpha_Live_Translator/alpha/utils/final_status_reconciliation.py` — not
  necessarily a fix target itself, but its `conflicts_with_live_status`
  mechanism is exactly the right shape to *detect* Items 1-3's bug once the
  writers stop hardcoding `stop_finalize_failed: False` — worth wiring
  in as a genuine automated check once the underlying writers are fixed.
- **NEEDS_REVIEW before 4B starts:** `strict_stop_evidence.py`,
  `canonical_acceptance_state.py`, `single_authority_packaging.py`,
  `session_forensics.py` — read these first; they may already implement
  part of the intended fix, or may be additional overlapping authorities
  that also need reconciling.

No code changes were made in this task. Stopping here per instruction.
