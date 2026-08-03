# Task 4B — Finalisation & Evidence Integrity Repair

Scope: only files listed in `TASK_4A_FINDINGS.md`'s "Files touched in 4B"
list, plus the four `NEEDS_REVIEW` files (read in full, resolved below — no
code changes needed in any of them). No file already fixed in Task 1/2/3
was touched; none needed to be (see the scope-boundary note at the end).

## NEEDS_REVIEW resolutions (read in full this task, not deferred)

- **`alpha/utils/strict_stop_evidence.py`** — a well-built, fail-closed,
  post-hoc verification function (`evaluate_strict_stop_evidence`). Traced
  its only caller: `run_zero_issue_closure_85253322.py`, a root-level
  historical script — **not** part of the live `main_window.py` →
  `stop_finalize_worker.py` call graph. It also structurally can't gate the
  live decision even if wired in: it reads `FINAL_STATUS_RECONCILIATION.json`,
  which is only written *after* Stop completes. **Not modified** — legitimate
  offline audit tool, no conflict with this task's fix.
- **`alpha/utils/canonical_acceptance_state.py`** — an extensive offline
  "final acceptance bundle" builder (11-issue closure tracking,
  ACCEPTED/NOT_ACCEPTED verdicts). Traced every caller
  (`build_canonical_acceptance_state`, `hash_immutable_artifacts`): all are
  root-level `_852533*` historical scripts, plus one `alpha/utils/`
  consumer (`package_canonical_acceptance_staging.py`) that is *itself*
  only called by the same class of root-level scripts. **Not modified** —
  confirmed offline packaging toolchain, not the live path.
- **`alpha/utils/single_authority_packaging.py`** — an offline zip-bundle
  packaging/audit tool ("Layers A/B/C", outer-bundle assembly, acceptance
  sidecar hashing). No caller anywhere touches `stop_finalize_worker.py`;
  the two `alpha/utils/` files that reference its name
  (`artifact_role_classifier.py`, `phase1_normalization_engine.py`) only
  contain a string literal filename, not an import. **Not modified** —
  confirmed offline, not live.
- **`alpha/utils/session_forensics.py`** — genuinely live, but at **app
  startup** (crash/kill forensics on the *previous* run's leftover
  artifacts), not during Stop/finalize. It only *reads*
  `RUN_ARTIFACTS_INDEX.txt`'s `status=` field and pattern-matches specific
  known values (`"in_progress"`, `"started"`, `"started_no_finalize"`) to
  classify a likely crash cause; anything else (including this task's new
  `"failed"`/`"completed_pending_evidence_package"` values) falls through
  to its existing default classification
  (`"previous_artifact_incomplete"`), which is accurate, not broken, for a
  run that legitimately has `status=failed`. **Not modified** — compatible
  consumer, no conflict.

## Files changed

### `Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py`

The core fix. Every change tagged `fixes TASK_4A_FINDINGS.md item N` inline.

- **New required-step tracking** (`_REQUIRED_SYNC_STEPS`, `_required_step_ok`,
  `_mark_required_step`, `_reset_required_steps`, `compute_core_final_status`).
  Unlike the pre-existing `_step_completed` dict (set `True` merely because
  `run_timed_step` caught no exception), an entry here is only ever set by
  the step's own call site using that step's own real, already-computed
  result. A step that never gets marked is treated as **failed**, not
  success — the literal fail-closed requirement. `compute_core_final_status()`
  is the single place `final_status`/`stop_finalize_failed`/`failure_reason`
  are decided from these nine required steps: **any** unmarked-or-failed
  step → `final_status = "failed"`, `stop_finalize_failed = True`,
  `failure_reason = <first failing step name>`. All nine succeed →
  `"completed_pending_evidence_package"` (see the tenth-item note below —
  it deliberately never returns `"completed"` itself; that only happens
  once the async evidence-package pass also confirms success).
- **Nine required-step call sites now thread through real success
  signals** instead of a discarded `run_timed_step` return value:
  - `drain_audio_queue` → `audio_summary`: real drain result
    (`not audio_drain.get("timed_out")`), not "no exception."
  - `deepgram_graceful_stop` → `raw_event_persistence`: real result
    (`not dg_result.get("timed_out")`).
  - `japanese_assembler_flush` + `transcript_commit_confirm` →
    `utterance_reconstruction`: both must have actually completed.
  - `canonical_pipeline_finalize` → `canonical_ledger_validation` AND
    `stable_export`: `finalize_canonical_pipeline`'s own `result["ok"]`
    (previously computed, only ever logged, never gated anything).
  - `translation_worker_shutdown` → `translation_drain` AND
    `loading_state_drain`: `TRANSLATION_WORKER_STOPPED` and
    `TRANSLATION_QUEUE_PENDING_AT_EXIT == 0` / `loading_indicators_pending() == 0`
    (all pre-existing, real values, never previously read for this
    purpose). No worker present (translation not active this session) is
    treated as a confirmed, definite no-op success — not an unconfirmed
    gap.
  - `write_final_alpha` → `final_export`: the pre-existing
    `path is not None` check, now also gates status, not just a display flag.
  - The `run_manifest` write itself (`_write_minimal_runtime_artifacts`) →
    `run_manifest`: the write's own try/except outcome.
- **`compute_core_final_status(*, exclude=())`** — supports excluding one
  step name so a step's own write function can compute "everything else so
  far" before its own outcome is knowable (used by the run-manifest write,
  which can't describe its own success inside its own content — same
  unavoidable bootstrapping limit any self-describing status write has).
- **`_write_core_live_status` / `_write_minimal_runtime_artifacts`
  rewritten**: `final_status`/`stop_finalize_failed`/`failure_reason` in
  `LIVE_RUN_STATUS.json`, `RUN_ARTIFACTS_INDEX.txt`, and `RUN_MANIFEST.json`
  are now `compute_core_final_status()`'s real values — the previous
  hardcoded `"completed_with_warnings"` / `"stop_finalize_failed": False`
  literals are gone.
- **`build_stop_finalize_summary`** now includes `final_status`,
  `failure_reason`, `failed_required_steps`, and derives
  `stop_finalize_failed` from `compute_core_final_status()` instead of the
  generic (non-required-step-aware) `failed_steps` list — this makes it the
  single authoritative source every downstream consumer reads, closing the
  "three independent implementations" duplication from item 5.
- **Dead code removed**: ~240 unreachable lines
  (`run_consistency_check` through the final `_queue_final_ui_update` call)
  that sat after an unconditional `return` in `_run_finalize_worker` and
  could never execute — replaced with a comment explaining the removal and
  pointing to `evidence_pointer_finalize.py` as the live replacement for
  the same responsibility. Removing genuinely dead code changes no runtime
  behavior (verified: `py_compile` + import + a live simulation all still
  pass — see Testing note below).
- **`_run_evidence_package_worker` disposition**: confirmed via repo-wide
  grep it has zero callers. **Left defined, not wired in, not deleted** —
  reviving ~100 lines of never-exercised code was judged more risk than
  value for a surgical pass once `evidence_pointer_finalize.py`'s async
  pass covers the same ground; a one-line-plus comment now documents this
  decision at the function so it isn't mistaken for live code later.
- **New**: `_write_translation_and_ui_evidence_streams` — writes
  `translation_jobs.jsonl` and `ui_events.jsonl` (two of the five required
  evidence streams, item 3/4) from data already collected during Stop
  (`TranslationWorker.shutdown()`'s own summary, the UI drain barrier's own
  result). Called once, right before the required-steps status write.

### `Alpha_Live_Translator/alpha/utils/canonical_finalize.py`

- **New**: `write_separated_evidence_streams(run_folder, snap)` — writes
  `canonical_commits.jsonl` (one line per active canonical record) and
  `utterance_decisions.jsonl` (one line per record's decision/action,
  derived from the same record data) — the remaining two of the five
  required evidence streams that this file has natural access to.
- **New**: `provider_events.jsonl` materialization — copies from the
  existing `raw_provider_events.jsonl`/`raw_deepgram_events.jsonl`
  accuracy-stage files (already protected by the Task 2A/2B
  synthetic-reentry guard in `japanese_final_chunk_stabilizer.py`, verified
  still in place), with an **additional, redundant filter** in this new
  code checking `synthetic_record`/`synthetic_lineage` on every row before
  it's written — defense in depth, matching the established pattern in
  this engagement (Task 3B's stale-version double-check is the precedent),
  so `provider_events.jsonl` itself can never carry a synthetic row even if
  the upstream guard were ever bypassed by a future change.
- `write_separated_evidence_streams` is called once inside
  `finalize_canonical_pipeline`, right after `write_stable_active_stage_artifacts`.
  No change to `finalize_canonical_pipeline`'s own exception handling
  shape — its `result["ok"]` was already correctly computed (per
  `TASK_1B_CHANGES.md`'s confirmation that `pipeline_commit_transaction.py`'s
  `success=ledger_applied` pattern is the right shape); the bug was that
  nothing *read* `result["ok"]` for status purposes, which is fixed in
  `stop_finalize_worker.py` above, not here.

### `Alpha_Live_Translator/alpha/utils/run_artifacts.py`

`finalize_live_run_status_completed`: `final_status`/`stop_finalize_failed`
now prefer the authoritative value threaded through `stop_summary["final_status"]`
/`stop_summary["stop_finalize_failed"]` (present on every live call now that
`build_stop_finalize_summary` sets them) instead of hardcoding
`"stop_finalize_failed": False` and deriving `final_status` from
`stop_finalize_timed_out` alone. Falls back to the old timeout-only rule
only if a caller passes a `stop_summary` without the new keys (defensive,
not the expected live path — kept for robustness against any caller this
audit didn't find).

### `Alpha_Live_Translator/alpha/utils/evidence_pointer_finalize.py`

This is where the tenth REPAIR_PLAN.md required item — **evidence
package** — is actually resolved. It cannot be decided synchronously during
Stop (`STOP_CORE_NEVER_BLOCKS_ON_EVIDENCE`/`EVIDENCE_PACKAGE_WORKER_DEFERRED`
are deliberate constants reflecting an intentional non-blocking design,
preserved rather than reverted — see the architecture note below); this
background pass *is* the evidence-package work.

- `final_status` is now `"completed"` only when **both** the nine
  synchronous required steps already succeeded (read from
  `stop_summary["stop_finalize_failed"]`, fail-closed if that key is
  missing — `bool(summary.get("stop_finalize_failed", True))`) **and**
  this pass's own writes (`LIVE_RUN_STATUS.json`, `RUN_ARTIFACTS_INDEX.txt`)
  succeed, tracked via their real return values instead of assumed.
- `evidence_flags` passed to `finalize_live_run_status_completed`/
  `finalize_run_manifest` are no longer a hardcoded all-`True` dict — each
  flag reflects whether that specific write actually succeeded.
- `_finalize_run_artifacts_index_status` now **returns `bool`** (previously
  implicit `None`, meaning a caller could never learn whether it worked) —
  `True` only if at least one index file existed and was rewritten without
  exception; `False` if neither file exists (the synchronous writer in
  `stop_finalize_worker.py` should have already created one — if it hasn't,
  that earlier write never happened, and this fails closed rather than
  silently reporting success).

### `Alpha_Live_Translator/alpha/utils/final_status_reconciliation.py`

**No code change.** This file already does the right thing structurally —
it reads `stop_finalize_failed`/`final_status`-shaped fields back out of
`LIVE_RUN_STATUS.json`/`POST_RUN_EXIT_SUMMARY.json` and flags
`conflicts_with_live_status` when they disagree with independently
re-derived evidence. It was downstream of three writers that hardcoded
`stop_finalize_failed: False`, so it could never previously detect a real
failure — now that those writers (fixed above) report real values, this
file's existing logic starts working correctly with no changes of its own
required.

## Requirement 3 — evidence stream separation: scope boundary (read this before assuming full coverage)

All five required streams (`provider_events.jsonl`, `utterance_decisions.jsonl`,
`canonical_commits.jsonl`, `translation_jobs.jsonl`, `ui_events.jsonl`) are
now written under those exact names, in `<run_folder>/evidence_streams/`.
**They are finalize-time materializations from data already collected
during the session, not true live per-event streams.** A genuine live
stream — writing `provider_events.jsonl` at the moment each raw Deepgram
event arrives, `utterance_decisions.jsonl` at the moment each HOLD/EXTEND/
COMMIT decision is made, etc. — would require adding write calls at the
actual emission points, which live inside `utterance_lifecycle.py`,
`canonical_transcript_ledger.py`, `translation_worker.py`, and
`main_window.py`. Those are already-fixed Task 1/2/3 files and were **not**
in `TASK_4A_FINDINGS.md`'s "Files touched in 4B" list — per this task's own
constraint ("do not modify files already fixed in Task 1/2/3 unless a
direct conflict — if so, STOP and report first"), this is exactly that
conflict, reported here rather than touched. The finalize-time
materialization implemented instead satisfies the *separation* and
*synthetic-exclusion* requirements honestly (each stream is real data,
correctly separated by ownership domain, with the synthetic guard verified
in place), just not as a live, continuously-written stream. Building true
live streaming is future work requiring explicit authorization to touch
those four files.

## Testing performed this task (compile/import only, per instruction)

- `py_compile` on all five changed files: clean.
- `import` of all five changed modules plus `alpha.ui.main_window`: clean,
  no circular imports, no missing attributes.
- Manual simulation of `compute_core_final_status()`: all-nine-steps-True →
  `"completed_pending_evidence_package"`; one step False → `"failed"` with
  the correct `failure_reason`; nothing marked (simulating the dead-code
  removal changed nothing about reachability) → `"failed"`, fail-closed as
  required.

No test suite was run (Task 1/2/3 regression suites, Task 4-specific
tests) — per instruction, that is the next task's job. Stopping here after
producing `TASK_4B_CHANGES.md`.
