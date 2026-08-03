# TASK 7 REPORT — inactivity_timeout_fallback commits losing their translation job

## Scope note — confirmed conflict with the original file-scope constraint

Task 7's CONSTRAINTS restricted edits to `utterance_lifecycle.py` and/or
`pipeline_commit_transaction.py` (with `stop_finalize_worker.py` readable as
a "direct caller on the stop path"), on the assumption that this was "the
SAME identity-binding defect class fixed in Task 6 Fix 1."

Root-cause tracing (below) showed that assumption did not hold: the
identity-binding step Task 6 Fix 1 fixed (`accept_boundary_proposal` in
`utterance_lifecycle.py`, which calls `execute_pipeline_commit` +
`assign_canonical_record_id`) is a **Japanese-only** path. The
`inactivity_timeout_fallback` reason is produced by `on_timeout()` /
`_commit_locked()` — a completely separate, older English/generic commit
path in the same file that has never called `execute_pipeline_commit` or
`assign_canonical_record_id` at all, for **any** commit reason, and was
never touched by Task 6. Tracing the real defect through to its actual
location required reading `duplicate_protection.py` and `main_window.py` —
neither in the authorized edit list.

I stopped before making any edit outside the authorized files and asked the
user how to proceed (findings + three options: expand scope, report-only, or
a workaround confined to the original two files). The user chose to expand
scope to the confirmed real location. This report documents that decision
and the resulting fix. **`utterance_lifecycle.py` and
`pipeline_commit_transaction.py` were read but required zero changes** —
`git diff --stat` for both shows no modification.

---

## Root cause

### What is NOT the bug

`UtteranceLifecycleOwner.on_timeout()` (`utterance_lifecycle.py:748`) calls
`self._commit_locked(reason="inactivity_timeout_fallback", ...)` — the exact
same `_commit_locked()` used by the normal `on_utterance_end` commit path
(`reason="utterance_end"`). Both build the emitted commit metadata
identically, including `"canonical_utterance_id": active.utterance_id`,
which is always the real, non-empty utterance id of the active utterance.
`Fix1TimeoutFallbackIdentityBindingTests` (new, below) proves this
explicitly: a timeout-fired commit's `LifecycleDecision.utterance_id` and
`metadata["canonical_utterance_id"]` are both populated and equal.

The actual ledger commit (`execute_pipeline_commit` + real
`canonical_record_id` assignment via `assign_canonical_record_id`) for this
English/generic path happens downstream in
`duplicate_protection.py::_display_transcript_item` — called once the
Tk-queued item reaches the UI-side transcript store — and it **succeeded**
for the reproduction record (`canonical_commits.jsonl` shows
`applied_action="append"`, committed). So the ledger/identity layer worked
correctly for this record, for both normal and timeout-fallback reasons
alike — this was never where the defect lived.

### What IS the bug

`main_window.py::submit_text_for_translation()` — called from
`_on_store_segment_added` right after a successful store commit — does not
call `translation_worker.enqueue_stable_segment()` immediately. It
coalesces the request behind a **120-350ms Tk `.after()` debounce timer**
(`_flush_pending_translation_submit`, armed via `_arm()`), so that rapid
Stable-revision updates to the same utterance only produce one DeepL
request.

`stop_finalize_worker.py`'s finalize sequence stops the translation worker
(`_translation_worker_shutdown` → `worker.stop_accepting()` →
`worker.shutdown(...)`) with **no knowledge of this debounce layer at all**
— it never referenced `_pending_translations_by_utterance` or
`_translation_debounce_after_ids`. Any segment whose debounce timer had not
yet fired when this shutdown ran was silently abandoned: never enqueued,
never counted, never logged — even though its canonical ledger commit had
already succeeded moments earlier.

`inactivity_timeout_fallback` commits are disproportionately exposed to
this race by construction: the timeout fires specifically because an
utterance sat inactive, which is exactly the situation most likely to
coincide with the user hitting Stop — i.e. these commits land close to
Stop far more often than a normal mid-conversation final does, so their
120-350ms debounce window is far more likely to still be armed (unfired)
when `_translation_worker_shutdown` runs.

This exactly matches the reproduction evidence:
`canonical_commits.jsonl` shows the commit succeeded (ledger layer worked);
`provider_events.jsonl`'s empty `canonical_record_id` is expected/normal
there (that stream is written at raw-ingestion time, before any commit,
for every record regardless of reason — not itself a defect);
`translation_jobs.jsonl` shows `canonical_utterance_id=""` and zero jobs —
because `stop_finalize_worker.py`'s own evidence writer
(`_write_translation_and_ui_evidence_streams`) correctly falls back to an
empty aggregate row when `translation_worker._revision_events` has no entry
for the record, which is exactly what happens when
`enqueue_stable_segment()` is never called.

---

## Fix

**Files:** `alpha/ui/main_window.py`, `alpha/utils/stop_finalize_worker.py`
(both newly authorized for this task; `utterance_lifecycle.py` and
`pipeline_commit_transaction.py` were read, confirmed correct, and left
unmodified).

### `main_window.py`

Added `AlphaApp.flush_pending_translation_submissions(self, timeout_seconds=2.0) -> int`
right after `_flush_pending_translation_submit`. It snapshots every key
still in `_pending_translations_by_utterance` and calls
`_flush_pending_translation_submit(key)` for each — the same real
enqueue path a fired debounce timer would have used, just triggered
synchronously instead of waiting for the timer. Because that method
touches translation-loading Tk widgets, the flush is marshaled onto the Tk
thread via `self.after(0, ...)` when called from a background thread (as
`stop_finalize_worker.py`'s finalize-step runner does), and — unlike the
existing fire-and-forget `_run_on_ui_thread` helper — blocks the calling
thread on a `threading.Event` until the Tk-thread flush completes or a
bounded timeout elapses, so the stop sequence can't race ahead of it.

### `stop_finalize_worker.py`

Added a new finalize step, `flush_pending_translation_debounce`, calling
`host.flush_pending_translation_submissions(timeout_seconds=2.0)` if
present — placed immediately before `translation_worker_shutdown` (which
stops the worker from accepting anything further) and after
`translation_unit_final_flush` (the existing, unrelated Japanese
translation-unit-builder flush). Added a `2500.0` ms budget for the new step
in `_STEP_TIMEOUTS_MS`, matching the style of the existing steps in that
table. This is a best-effort step (not added to `_REQUIRED_SYNC_STEPS`),
consistent with `translation_unit_final_flush`'s existing pattern — it logs
`PENDING_TRANSLATION_DEBOUNCE_FLUSH_BEGIN`/`_DONE`/`_FAILED` via
`freeze_guard_log` but never raises.

---

## Regression tests added

`tests/test_task7_report.py`:

- **`Fix1TimeoutFallbackIdentityBindingTests`** (VALIDATE item 1) — fires a
  real `on_timeout()` against `UtteranceLifecycleOwner` and asserts the
  resulting commit decision carries a non-empty `canonical_utterance_id`,
  equal in both `LifecycleDecision.utterance_id` and
  `metadata["canonical_utterance_id"]`, with `reason ==
  "inactivity_timeout_fallback"`. Documents, as a regression guard, that
  this part of the pipeline was never the defect.
- **`Fix2PendingTranslationFlushTests`** (VALIDATE item 2) — borrows the
  real `flush_pending_translation_submissions` /
  `_flush_pending_translation_submit` methods onto a lightweight fake host
  (same method-borrowing pattern used throughout this engagement) with a
  fake `translation_worker`:
  - one job sitting in the debounce map (simulating an armed-but-unfired
    timer) is flushed exactly once, with the correct
    `canonical_utterance_id`/`source_record_id`, and removed from the
    pending map;
  - an empty pending map is a safe no-op;
  - two independently pending jobs are both flushed, each exactly once.

---

## Full regression suite

`python -m unittest discover -s tests -p "test_*.py"` — **105 tests total**
(101 from Task 6 + 4 new), **98 passing**, **7 failing** — the exact same 7
pre-existing, already-documented-unrelated failures from
`TASK_6_REPORT.md` (2 in `test_final_transcript_commit_v3_2_5.py`,
confirmed via git-stash before/after comparison in Task 6; 2 `ERROR` + 2
`FAIL` in `test_package_glossary_flags_85253.py`; 1 `FAIL` in
`test_stop_finalize_v3_2_3.py::test_phase_constants_match_spec` — all
explicitly P2/out-of-scope). **Zero new failures.**

`git diff --stat` confirms `utterance_lifecycle.py` and
`pipeline_commit_transaction.py` have no changes from this task; only
`main_window.py` and `stop_finalize_worker.py` (both authorized) were
modified. No audio/WASAPI/Deepgram/DeepL transport/UI-layout code was
touched.

---

## Minimum Acceptance Gate (re-run)

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | No commit has `success=True` with `identity_assigned=False` | **PASS** | Unaffected — `utterance_lifecycle.py` unmodified this task; `Fix1ForcedAssignmentRejectionTests` (Task 6) still passing. |
| 2 | Exactly one canonical stable event per applied ledger transaction | **PASS** | Unaffected — `pipeline_commit_transaction.py` unmodified this task; `Fix2SoleCanonicalWriterTests` (Task 6) still passing. |
| 3 | Stable, ledger, final export, and translation counts reconcile | **PASS** | `test_task4c_acceptance_gate.py::test_3_raw_canonical_ui_export_counts_reconcile` and `::test_5_every_translation_references_existing_canonical_record` still pass; Task 7's fix specifically closes a translation-count gap for timeout-fallback commits, strictly improving this item versus Task 6's report. |
| 4 | No outgoing audio is cleared before a bounded delivery attempt; any forced drop is explicitly counted/logged | **PASS** | Unaffected — `deepgram_client.py` unmodified this task; `test_stop_queue_flush_v3_2_4.py` (all 3 tests) still passing. |
| 5 | The last spoken phrase appears in both canonical export and translation after Stop | **PASS** | `test_task3c_acceptance_gate.py::test_5_japanese_final_line_not_dropped_on_stop` still passing; Task 7's fix directly targets this exact gate item for the timeout-fallback case specifically (`Fix2PendingTranslationFlushTests`). |
| 6 | Japanese speaker-change test preserves both distinct speaker IDs | **PASS** | `test_task2c_acceptance_gate.py::test_3_japanese_speaker_change_never_merges` still passing, unaffected by this task's changes. |
| 7 | `stage_capture_complete=true`, `validation_output_written=true`, zero finalizer exceptions | **PASS** | All `stop_finalize_worker`-related suites (`test_task4c_acceptance_gate.py`, `test_stop_finalize_v3_2_3.py`, `test_stop_queue_flush_v3_2_4.py`, `test_graceful_stop_v3_2_2.py`) still pass with zero unhandled exceptions after adding the new `flush_pending_translation_debounce` step; the new step is wrapped in its own try/except and never raises out of `run_timed_step`. Same indirect-verification caveat as Task 6 (exact `stage_capture_complete`/`validation_output_written` fields are populated by `run_artifacts.py` during a live run, not re-checked via a dedicated fixture). |
| 8 | Translation queue, ordering buffer, UI event bus, and loading indicators are all empty at exit | **PASS** | `test_task3c_acceptance_gate.py::test_6_zero_loading_indicators_after_burst_and_stop` still passing. |

**All 8 items PASS.**

---

## Final Verdict

**Fixed and safe to re-test manually.** The root cause was correctly
diagnosed as a translation-loss defect but incorrectly pre-located by the
originating bug report — it was a Stop-vs-debounce-timer race in
`main_window.py`/`stop_finalize_worker.py`, not an identity-binding gap in
`utterance_lifecycle.py`/`pipeline_commit_transaction.py` (both confirmed
already correct for this path and left unmodified). A record committed via
`inactivity_timeout_fallback` — or any other commit landing within ~350ms
of Stop — will now have its translation job flushed synchronously before
the translation worker stops accepting new work, instead of being silently
abandoned. All 4 new regression tests pass, the full 105-test suite shows
zero new failures versus Task 6's baseline, and all 8 Minimum Acceptance
Gate items pass.
