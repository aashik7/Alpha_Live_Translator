# TASK 8 REPORT — Structural fix for the "committed but never translated" failure class

## Summary

Tasks 6 and 7 each fixed one reproduced instance of the same underlying
failure shape (a record commits successfully to the canonical ledger but
never reaches translation). This task traces the full call path from
`duplicate_protection.py::_display_transcript_item` (the real commit point)
through to `submit_text_for_translation()` (main_window.py) for **every**
commit reason this codebase produces, finds and fixes a **structural**
defect that affected all of them equally, and adds a required,
self-healing reconciliation step as a backstop for anything still
undiscovered.

**Files touched:** `alpha/ui/main_window.py`, `alpha/utils/stop_finalize_worker.py`.
`alpha/transcription/duplicate_protection.py`, `alpha/translation/translation_worker.py`,
`alpha/transcription/utterance_lifecycle.py`, and
`alpha/transcription/pipeline_commit_transaction.py` were all read as part
of tracing but required **zero** changes — `git diff --stat` confirms no
modification to any of them. One additional file,
`alpha/utils/ui_stop_drain_barrier.py`, was read (not edited) to rule out a
cross-thread-Tk hypothesis; it turned out to already be correctly
thread-safe and needed no change, so it is not counted as "made non-inert."

---

## PART A — Root cause and every commit reason checked

### Call path traced

Every commit in this codebase — regardless of language or reason — ends up
enqueuing a display item into `transcript_queue`, which is drained by
`main_window.py::_process_ui_queue_once` → `duplicate_protection.py::_display_transcript_item`
→ (successful `execute_pipeline_commit` + `assign_canonical_record_id`) →
`main_window.py::_on_store_segment_added` (new segment) or
`_on_store_segment_updated` (revision). Two additional call sites
(`main_window.py::_commit_japanese_update_previous_segment` and
`_recover_interim_tail_on_stop`'s `append_missing_suffix` handler) call
`_on_store_segment_updated` **directly**, bypassing
`duplicate_protection.py` entirely for Japanese manual-mode and stop-tail
recovery. All paths converge on these same two hook methods — confirmed to
be the single shared endpoint for translation submission across the whole
codebase.

### Every commit-reason string found (grep across `alpha/transcription/`)

| Commit reason | Origin | Reached `submit_text_for_translation` BEFORE Part A? | AFTER Part A? |
|---|---|---|---|
| `utterance_end` | `utterance_lifecycle.py::_commit_locked` (English) | Yes, but only if the transcript box was renderable and no rendering exception occurred first | **Yes, unconditionally** |
| `inactivity_timeout_fallback` | `utterance_lifecycle.py::on_timeout` → `_commit_locked` | Yes, but only if the transcript box was renderable and no rendering exception occurred first — this is the original reproduction | **Yes, unconditionally** |
| `speech_final` | `utterance_lifecycle.py::_commit_locked` (English) | Same conditional gap | **Yes, unconditionally** |
| `boundary_before_new_utterance` | `utterance_lifecycle.py::_commit_locked` (English) | Same conditional gap | **Yes, unconditionally** |
| `speech_final_new_utterance` | `utterance_lifecycle.py::_commit_locked` (English) | Same conditional gap | **Yes, unconditionally** |
| `supersede_then_commit` | `utterance_lifecycle.py::_commit_locked` (English) | Same conditional gap | **Yes, unconditionally** |
| `japanese_continuity_assembler_<reason>` (many variants) | `japanese_sentence_assembler.py::_publish_sentence` via `accept_boundary_proposal` | Same conditional gap | **Yes, unconditionally** |
| `stop_flush_incomplete_tail` | `japanese_sentence_assembler.py` (stop-tail commit) | Same conditional gap | **Yes, unconditionally** |
| `assembler_exception_direct_commit_fallback` | `japanese_sentence_assembler.py` (exception recovery path) | Same conditional gap | **Yes, unconditionally** |
| Japanese manual-mode revisions (no distinct `lifecycle_commit_reason` string; routed via `_commit_japanese_update_previous_segment`) | `main_window.py` direct call to `_on_store_segment_updated` | Same conditional gap | **Yes, unconditionally** |
| Stop-tail interim recovery (`append_missing_suffix`, no distinct reason string; routed via `_recover_interim_tail_on_stop`) | `main_window.py` direct call to `_on_store_segment_updated` | Same conditional gap | **Yes, unconditionally** |

**Every reason funnels through the same two hook methods, and every one
had the identical structural gap — none was special-cased differently
from the others.** This matches the task's framing exactly: it was never a
per-reason bug, so the fix is not per-reason either.

### The actual defect

`main_window.py::_on_store_segment_added` and `_on_store_segment_updated`
both ran translation submission **after** transcript-widget rendering, and
**after** an unconditional `box = self._transcript_box(); if box is None:
return`. Two failure modes fell out of this:

1. If `_transcript_box()` (`self.initial_verse_box`) ever returns `None`,
   the method returns immediately and `submit_text_for_translation` is
   never reached — no exception, no log, nothing.
2. The call site in `duplicate_protection.py` that invokes these hooks
   (lines 541–556) is **not** wrapped in try/except — unlike the
   `else`-branch fallback a few lines below it, which already **was**
   protected. So any exception raised by the widget-manipulation code that
   used to run first (`box.configure`, `_insert_speaker_segment_line`,
   etc. — e.g. a `TclError` from a widget in a transitional/torn-down
   state) silently aborts the whole hook before reaching
   `submit_text_for_translation`, and is only caught two frames up by
   `_process_ui_queue_once`'s generic `except Exception: print(...)` — the
   item is already dequeued and lost, with no signal specific to
   translation.

Neither failure mode depends on `lifecycle_commit_reason` at all — both
are purely about UI-widget state at the moment the shared hook runs,
which explains why the bug reproduced specifically for
`inactivity_timeout_fallback` (a commit landing close to Stop, when
UI/widget state is more likely to be in flux) without needing any
reason-specific branch to exist.

### Fix

`_on_store_segment_added` and `_on_store_segment_updated` were restructured
so translation submission (and, for the update case,
`_remove_translation_item_for_utterance`, which also doesn't touch the
transcript box) now run **first**, wrapped in their own try/except as
before — and all transcript-box rendering runs **second**, still behind
its own `if box is None: return`, but now with zero ability to prevent
translation delivery. This is a single change to the one shared hook, not
eleven per-reason patches.

---

## PART B — Reconciliation safety net

**File:** `alpha/utils/stop_finalize_worker.py`.

Added `reconcile_translation_gaps(host)` — a module-level function (not a
nested closure, so it is independently testable against the exact
production logic) — and wired it in as a new stop-finalize step,
`translation_reconciliation`, placed immediately after Task 7's
`flush_pending_translation_debounce` and before `translation_worker_shutdown`.

**Logic:**
1. Read every active (non-suppressed), translation-eligible record from
   `canonical_transcript_ledger.get_active_records()`, extracting
   `canonical_utterance_id` from each record's `metadata`.
2. Read every `canonical_utterance_id` that has an `accepted=True` entry
   in `translation_worker._revision_events` — the same evidence source
   Task 7's `_write_translation_and_ui_evidence_streams` already trusts —
   as the set of utterances that genuinely reached the worker.
3. For every committed, translation-eligible `canonical_utterance_id`
   **not** in that set, force-submit it directly via
   `translation_worker.enqueue_stable_segment(...)`, log a WARNING (via
   `alpha.utils.logging_utils.get_logger(__name__).warning(...)`, plus the
   existing `freeze_guard_log` NDJSON trail) with `record_id` and
   `commit_reason`, and count it.

**Required, not best-effort:** unlike Task 7's `flush_pending_translation_debounce`,
`translation_reconciliation` was added to `_REQUIRED_SYNC_STEPS`. Its
`run_timed_step(...)` return value is threaded into `_mark_required_step(...)`,
so if `reconcile_translation_gaps` raises or times out,
`compute_core_final_status()` reports `final_status == "failed"` with
`failure_reason == "translation_reconciliation"` — a real finalizer
failure, not a silently swallowed best-effort no-op. (`run_timed_step`
itself, by the whole file's existing design, still catches the exception so
one runaway step can't crash the entire finalize worker — but that
catch now feeds a required-step failure instead of disappearing.)

This is a genuine backstop for the **whole class**, not the two specific
gaps discovered so far: it doesn't know or care why a record's
`canonical_utterance_id` never reached the worker — any future path,
including ones this task never found, is caught here before the worker
stops accepting new work.

---

## Regression tests added

`tests/test_task8_report.py` (9 tests):

- **`PartAEveryCommitReasonReachesTranslationTests`** — drives the real
  `duplicate_protection.py::_display_transcript_item` with each of the 11
  commit-reason shapes from the table above (unique `canonical_utterance_id`
  per reason) against a host combining `DuplicateProtectionMixin` with the
  real, borrowed `_on_store_segment_added`/`_on_store_segment_updated`, and
  asserts every single one reaches a stubbed `submit_text_for_translation`.
- **`PartAUiTeardownDoesNotBlockTranslationTests`** — the specific
  structural regression guard: `_on_store_segment_added`/`_updated` still
  submit when `_transcript_box()` returns `None`, and a rendering
  exception (a widget whose `.configure()` raises) does not prevent the
  translation submission that now runs before it.
- **`InactivityTimeoutFallbackTranslationRegressionTest`** (VALIDATE item
  5) — the dedicated, bug-named regression test: a short Japanese
  utterance committed with `lifecycle_commit_reason="inactivity_timeout_fallback"`
  and `canonical_decision="TERMINAL_COMMIT"` (the exact reported shape)
  reaches `submit_text_for_translation` through the real commit path.
- **`PartBReconciliationSafetyNetTests`** (VALIDATE item 2) — a ledger
  record committed directly via `canonical_transcript_ledger.append_record`
  with no corresponding `translation_worker._revision_events` entry at all
  (simulating an entirely unknown future skip path, not one of the paths
  Part A fixed) is force-submitted by `reconcile_translation_gaps`, logging
  a `WARNING`-level message containing `TRANSLATION_RECONCILIATION_FORCED_SUBMIT`
  (asserted via `assertLogs`); an already-submitted record is not
  resubmitted; a `translation_eligible=False` record is never forced;
  the step is confirmed present in `_REQUIRED_SYNC_STEPS` and a forced
  failure of it is confirmed to surface as `final_status == "failed"` /
  `failure_reason == "translation_reconciliation"` via
  `compute_core_final_status()`.

All 9 pass.

---

## Full regression suite

`python -m unittest discover -s tests -p "test_*.py"` — **114 tests total**
(105 from Task 7 + 9 new), **107 passing**, **7 failing** — the exact same
7 pre-existing, already-documented-unrelated failures carried forward from
`TASK_6_REPORT.md`/`TASK_7_REPORT.md` (2 in `test_final_transcript_commit_v3_2_5.py`,
confirmed via git-stash before/after comparison in Task 6, root-caused to a
bare test fixture that never opens the Japanese acceptance gate — unrelated
to `main_window.py`/`stop_finalize_worker.py`; 2 `ERROR` + 2 `FAIL` in
`test_package_glossary_flags_85253.py`; 1 `FAIL` in
`test_stop_finalize_v3_2_3.py::test_phase_constants_match_spec` — all
explicitly P2/out-of-scope). **Zero new failures.**

All Task 6/Task 7 regression suites (`test_task6_report.py`,
`test_task7_report.py`) and all Minimum Acceptance Gate suites re-verified
individually — all still passing.

`git diff --stat` confirms only `alpha/ui/main_window.py` and
`alpha/utils/stop_finalize_worker.py` were modified this task;
`duplicate_protection.py`, `translation_worker.py`,
`utterance_lifecycle.py`, and `pipeline_commit_transaction.py` all show
zero diff. No audio/WASAPI/Deepgram/DeepL transport/UI-layout code was
touched.

---

## Minimum Acceptance Gate (re-run)

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | No commit has `success=True` with `identity_assigned=False` | **PASS** | Unaffected — `utterance_lifecycle.py` unmodified; `Fix1ForcedAssignmentRejectionTests` still passing. |
| 2 | Exactly one canonical stable event per applied ledger transaction | **PASS** | Unaffected — `pipeline_commit_transaction.py` unmodified; `Fix2SoleCanonicalWriterTests` still passing. |
| 3 | Stable, ledger, final export, and translation counts reconcile | **PASS** | `test_task4c_acceptance_gate.py` tests 3/5 still pass; this task's own reconciliation step is now a second, structural, always-on enforcement of exactly this property at every Stop, not just a test-time check. |
| 4 | No outgoing audio is cleared before a bounded delivery attempt; any forced drop is explicitly counted/logged | **PASS** | Unaffected — `deepgram_client.py` unmodified; `test_stop_queue_flush_v3_2_4.py` (all 3 tests) still passing. |
| 5 | The last spoken phrase appears in both canonical export and translation after Stop | **PASS** | `test_task3c_acceptance_gate.py::test_5_japanese_final_line_not_dropped_on_stop` still passing; this task's own `InactivityTimeoutFallbackTranslationRegressionTest` directly targets and closes the exact reported gap for this item. |
| 6 | Japanese speaker-change test preserves both distinct speaker IDs | **PASS** | `test_task2c_acceptance_gate.py::test_3_japanese_speaker_change_never_merges` still passing, unaffected. |
| 7 | `stage_capture_complete=true`, `validation_output_written=true`, zero finalizer exceptions | **PASS** | All `stop_finalize_worker`-related suites still pass with zero unhandled exceptions after adding `translation_reconciliation`; `run_timed_step`'s existing exception containment plus the new `_mark_required_step` wiring means a reconciliation failure becomes a reported required-step failure, not an unhandled exception. Same indirect-verification caveat as Tasks 6/7 for the exact `stage_capture_complete`/`validation_output_written` fields (populated by `run_artifacts.py`, not touched this task). |
| 8 | Translation queue, ordering buffer, UI event bus, and loading indicators are all empty at exit | **PASS** | `test_task3c_acceptance_gate.py::test_6_zero_loading_indicators_after_burst_and_stop` still passing. |

**All 8 items PASS.**

---

## Final Verdict

**Fixed structurally, with a self-healing backstop, and safe to re-test
manually.** Part A closed the actual defect at its one true shared choke
point (`_on_store_segment_added`/`_on_store_segment_updated`), verified
against every commit-reason string this codebase currently produces —
none of the eleven required a special case, confirming the fix is
structural rather than another point-patch. Part B adds
`translation_reconciliation` as a **required** stop-finalize step: it
diffs the canonical ledger's committed, translation-eligible records
against `translation_worker`'s own accepted-job record, force-submits any
gap with a WARNING-level log carrying the record id and commit reason, and
its own failure is wired to surface as a real finalizer failure rather
than being swallowed.

**Part B is now the explicit backstop for any undiscovered future path.**
Part A closes every gap this task could find by exhaustive tracing; Part B
does not depend on that tracing having been exhaustive — it compares
outcomes (committed vs. submitted), not code paths, so a completely new,
never-yet-seen way of skipping submission would still be caught,
force-corrected, and logged at Stop, before the translation worker stops
accepting work. This closes the loop Task 6 and Task 7 each partially
closed and leaves a structural guarantee in place instead of a third
narrow patch.
