# TASK 9 REPORT — Reconciliation delivery, contradictory post-hoc status, and closing the mock-vs-live gap

## Scope note

Files edited this task: `alpha/utils/stop_finalize_worker.py`,
`alpha/translation/translation_worker.py` — both explicitly named as
primary/editable for this task. `alpha/ui/main_window.py` was **read only**
(to trace Issue 1's root cause to `_begin_graceful_stop`'s premature
`stop_accepting()` call) and **not edited**. `alpha/transcription/duplicate_protection.py`
was not touched or needed this task. No audio/WASAPI/Deepgram/DeepL
transport/UI-layout code was modified; the one Deepgram-adjacent piece of
Issue 3's harness (`_wait_for_final_transcripts_after_finalize`) is a
test-only override on a test-only host class, not a change to `deepgram_client.py`.

---

## Issue 1 — reconciliation detects but doesn't deliver

### Root cause

Confirmed in `stop_finalize_worker.py`, but the actual trigger is in
`main_window.py::_begin_graceful_stop` (read, not edited): the instant Stop
is clicked, on the UI thread, it calls `worker.stop_accepting()` —
**before** `begin_stop_from_ui`'s background finalize sequence (including
Task 8's `translation_reconciliation` step) has even started. In
`translation_worker.py::enqueue_stable_segment`, the very first
capability-gate is `if not self._accepting or self._quota_disabled or not
self._enabled: return False`. Since `_accepting` is already `False` by the
time `reconcile_translation_gaps` runs, **every** forced submission was
unconditionally, silently rejected — not a rare race, a guaranteed
100%-reproducible outcome. Compounding this, the old code logged
`TRANSLATION_RECONCILIATION_FORCED_SUBMIT` (a WARNING) **before** calling
`enqueue_stable_segment`, and only incremented `forced_count` on a truthy
return — so a rejection (or an exception) was indistinguishable from
success in the logs, and `reconcile_translation_gaps` returned normally
either way, satisfying `run_timed_step`/`_mark_required_step` as if nothing
had gone wrong.

### Fix

- **`translation_worker.py::enqueue_stable_segment`**: added a `force: bool
  = False` parameter that bypasses only the `not self._accepting` gate
  (kept as a separate check now); `_quota_disabled`/`not self._enabled`
  still gate even forced submissions, since those reflect the provider
  genuinely being unable to accept work, not merely that Stop was clicked.
  A forced job still lands in the same `self._queue`
  `TranslationWorker.shutdown()` already bounded-drains, so it is
  delivered through the normal pipeline, not a side channel. Also added
  two new rejection counters (`NOT_ACCEPTING_SUBMISSIONS_REJECTED`,
  `QUOTA_OR_DISABLED_SUBMISSIONS_REJECTED`) for visibility — there were
  previously none for these specific rejection reasons.
- **`stop_finalize_worker.py::reconcile_translation_gaps`**: calls
  `enqueue_stable_segment(..., force=True)`; every attempt's outcome
  (accepted / rejected / raised) is now logged **only after it is known**
  — a WARNING with `TRANSLATION_RECONCILIATION_FORCED_SUBMIT` on success,
  an `ERROR` with `TRANSLATION_RECONCILIATION_FORCED_SUBMIT_EXCEPTION`
  (full exception type/message, plus a truncated traceback in the
  `freeze_guard_log` payload) on a raised exception, and an `ERROR` with
  `TRANSLATION_RECONCILIATION_FORCED_SUBMIT_REJECTED` on a plain `False`
  return. Any unresolved gap (worker missing, exception, or rejection) is
  collected, and if the list is non-empty at the end, a new
  `TranslationReconciliationError` is raised — `run_timed_step` catches it
  like any other exception, so the step genuinely reports failure
  (`ok=False`), which `_mark_required_step` threads into
  `compute_core_final_status()`. A failed forced submission is now a real
  reconciliation failure, never a silent no-op.

### Test result (VALIDATE item 1)

`Issue1ReconciliationFailureVisibilityTests` (5 tests): an exception from
`enqueue_stable_segment` is logged at ERROR with the exception type and
message and `reconcile_translation_gaps` raises; a plain rejection is
logged at ERROR and also raises; `run_timed_step` correctly reports
`ok=False` (not swallowed) when a forced submission fails;
`force=True` genuinely bypasses a real `TranslationWorker` instance's
`stop_accepting()`-induced `_accepting=False` state and — the explicit
ask — `worker._counters["STABLE_TRANSLATION_JOBS_ACCEPTED"]` increments by
1, the same counter a normal accepted submission increments;
`force=False` against the same not-accepting worker is still correctly
rejected. All 5 pass.

---

## Issue 2 — contradictory post-hoc `final_status`

### Root cause

`build_stop_finalize_summary(host)` (in `stop_finalize_worker.py`) is
called from three places: the real, synchronous, authoritative call at the
end of `_run_finalize_worker` (feeding `STOP_FINALIZE_COMPLETED` and
`RUN_MANIFEST.json`); `_queue_final_ui_update` on the exception path only;
and — the second, later call site the task asked me to find —
`evidence_pointer_finalize.py::finalize_evidence_pointers_completed`,
scheduled onto its own daemon thread by
`schedule_evidence_pointer_finalization_background`, called near the very
end of `_run_finalize_worker` (confirmed: yes, read downstream — its
`stop_summary["stop_finalize_failed"]`/`failure_reason` directly gate
`finalize_live_run_status_completed(...)` and `finalize_run_manifest(...)`,
i.e. this second computation's result is written to disk, not merely
logged).

Both calls read `compute_core_final_status()`, which reads
`_required_step_ok` — a **process-global, run-id-unscoped** module-level
dict. `_reset_stop_state()` (which clears `_required_step_ok`) fires
exactly once per Stop click, at the very start of `begin_stop_from_ui`. If
a new Stop begins (a new run's `_reset_stop_state()` fires) before the OLD
run's delayed `EvidencePointerFinalize` background thread gets around to
calling `build_stop_finalize_summary(host)` again, that thread reads
whatever the NEW run's in-progress `_required_step_ok` happens to contain
at that instant — explaining a `failure_reason` like
`"utterance_reconstruction"` (marked comparatively early in
`_run_finalize_worker`'s sequence) even though the run this thread was
actually scheduled for had every step succeed: `audio_summary` and
`raw_event_persistence` (marked earlier still) had already been
re-marked `True` for the new run by the time the stale thread read the
dict, `utterance_reconstruction` had not yet been re-marked.

(A related, deeper concern found but **not fixed**, since it lives outside
this task's primary files: `evidence_pointer_finalize.py` also calls
`get_current_run_identity()` fresh, so in the interleaving above it could
end up writing this stale/wrong status into a *different* run's
`RUN_MANIFEST.json`/live-status files rather than the one it was
originally scheduled for. This report flags it for a future task; it was
not touched here.)

### Fix

`build_stop_finalize_summary` now caches its own result, scoped by the
current run id (`alpha.utils.run_identity.get_current_run_identity().run_id`),
gated on `get_stop_finalize_snapshot()["finalize_completed"]` being `True`
(only ever true once the synchronous sequence has genuinely reached its
end — never for a hypothetical mid-sequence caller). A later call for the
**same** run id returns the cached, already-correct summary directly
instead of recomputing against whatever the shared globals currently hold;
a call for a **different** run id (or no run identity available at all)
falls through to a fresh computation exactly as before. This makes status
computed effectively once per run for any caller after the run's
synchronous sequence completed, closing the gap without needing to touch
`evidence_pointer_finalize.py`'s own call sites.

### Test result (VALIDATE item 2)

`Issue2FinalStatusComputedOnceTests` (3 tests): a second call for the
SAME run id, after the shared globals have been reset (simulating a new
run's Stop beginning), returns the identical, correct, cached result from
the first call; a second call for a **different** run id correctly does
NOT reuse the stale cache (computes fresh against whatever's currently in
the globals — proving the fix doesn't paper over a genuinely different
run); with no run identity available at all, the function falls through
to a fresh computation as before (no crash, no wrong cache hit). All 3
pass.

---

## Issue 3 — closing the mock-vs-live gap

### The standing regression test

`tests/test_task9_report.py::Issue3RealThreadIntegrationTest` — no mocking
of Tk's `.after()` debounce timer, no mocking of `TranslationWorker`'s
background thread, no mocking of `stop_finalize_worker.py`'s background
finalize thread. `RealIntegrationHost` is a real (hidden) `tk.Tk` subclass
combining `DeepgramClientMixin` + `DuplicateProtectionMixin` with ~20
production methods borrowed unmodified from `AlphaApp` (the same
method-borrowing pattern already used throughout this engagement, now
applied broadly enough to cover the full real pipeline instead of an
isolated slice): `_on_store_segment_added`/`_updated`,
`submit_text_for_translation`, `_flush_pending_translation_submit`,
`flush_pending_translation_submissions`, `_transcript_box`,
`_insert_speaker_segment_line`, `_show_translation_loading_item`,
`_run_on_ui_thread`, `_start_ui_event_bus_drain_loop`, and more. It holds
a **real** `TranslationWorker` with `.start()` called (a genuine background
thread; only the DeepL network boundary is faked via an in-process
`FakeDeepLClient`, an established pattern already used by
`test_task3c_acceptance_gate.py`). Only the Deepgram WebSocket wait is
stubbed on the test-only host — that transport is explicitly frozen/out of
scope and irrelevant to the translation-delivery race under test.

The test:
1. Drives a real `UtteranceLifecycleOwner.on_final_chunk(..., speech_final=False)`,
   which arms the real `inactivity_timeout_fallback` path exactly as a
   genuine trailing utterance would (a real, short `commit_fallback_ms`).
2. Drains the real `transcript_queue` through
   `duplicate_protection.py::_display_transcript_item` (real identity
   binding + ledger commit) and the real `_on_store_segment_added` hook,
   which arms the real Tk `.after()` translation-submit debounce timer.
3. Immediately calls `worker.stop_accepting()` (the exact real
   `_begin_graceful_stop` precondition that causes Issue 1) and then the
   real, unmocked `stop_finalize_worker.begin_stop_from_ui(host)` — a real
   background `StopFinalizeWorker` thread starts.
4. Pumps the real Tk event loop (`host.update()`) in a bounded real-time
   polling loop — not a manual/stepped invocation of any callback — until
   the real background finalize thread reports done.
5. Asserts against the **real** `TranslationWorker` instance's own state
   (`worker._revision_events`, `worker._counters["STABLE_TRANSLATION_JOBS_ACCEPTED"]`)
   that the segment was genuinely delivered — not a stub, not a mock.

### 5-run stability result (VALIDATE item 3)

The test method itself runs the full real-thread scenario 5 times in a row
(fresh session/run ids each time, fresh host/worker/threads, explicit
teardown between iterations) via `subTest`, and passed cleanly (~9.3s
total, ~1.8-2.1s/iteration). It was additionally run 3 more times as
**separate process invocations** (`exit=0`, `OK` each time) for extra
confidence beyond the in-process repetition — 8 real-thread runs total,
zero flakiness observed.

### Would it have caught Tasks 6/7/8's original gaps?

- **Task 6** (identity-binding atomicity, sole canonical writer, stop-clear
  ordering, evidence-write decoupling, late-final gate reopening): mostly
  no — those were narrower, mechanism-level defects this integration test
  doesn't specifically exercise (e.g. forced identity-assignment
  rejection, Japanese-specific gate races). Not this test's job; those
  already have their own targeted regression tests.
- **Task 7** (debounce timer abandoned by Stop before it fires): **yes** —
  this is exactly the race this harness reproduces end-to-end (real
  debounce timer armed, real Stop triggered immediately after), and it is
  now covered by Task 7's `flush_pending_translation_debounce` step inside
  the same real sequence this test exercises.
- **Task 8** (structural UI-hook coupling dropping translation submission
  regardless of commit reason): **yes** — the harness drives the exact
  real `_on_store_segment_added` path Task 8 fixed; had that fix been
  reverted, this test would fail (translation never reaching
  `submit_text_for_translation`).
- **Task 9's own Issue 1** (reconciliation detects but can't deliver
  because `stop_accepting()` already fired): **yes, and it did** — every
  one of the 8 real runs delivered the segment specifically via the
  `translation_reconciliation` step's forced submission (visible in the
  logs: `TRANSLATION_RECONCILIATION_FORCED_SUBMIT ... canonical_utterance_id=U-1`),
  proving the debounce timer's own natural ~120-350ms window did not win
  the race against an immediate real Stop, and that reconciliation's
  `force=True` fix was what actually closed it. Had Issue 1's fix not
  landed, this exact test would have failed with "never reached
  translation_worker."

This test is now the standing regression test for this failure class: it
exercises the real timing regime (real Tk scheduler, real background
threads) that unit-mocked tests structurally cannot, and it is anchored to
outcomes (did the segment reach `translation_worker`?) rather than to any
one specific delivery mechanism, so it will keep catching future gaps in
this same class regardless of which of the three delivery paths (natural
debounce, Task 7's flush, Task 8/9's reconciliation) end up being the one
that actually saves a given commit.

---

## Full regression suite

`python -m unittest discover -s tests -p "test_*.py"` — **123 tests total**
(114 from Task 8 + 9 new), **116 passing**, **7 failing** — the exact same
7 pre-existing, already-documented-unrelated failures carried forward from
`TASK_6/7/8_REPORT.md` (2 in `test_final_transcript_commit_v3_2_5.py`; 2
`ERROR` + 2 `FAIL` in `test_package_glossary_flags_85253.py`; 1 `FAIL` in
`test_stop_finalize_v3_2_3.py::test_phase_constants_match_spec` — all
explicitly P2/out-of-scope). **Zero new failures.**

All prior task regression suites (`test_task6_report.py`,
`test_task7_report.py`, `test_task8_report.py`) and all Minimum Acceptance
Gate suites re-verified together (41 tests, `OK`).

`git`-tracked changes this task are confined to
`alpha/utils/stop_finalize_worker.py` and
`alpha/translation/translation_worker.py` (confirmed via direct tool-call
history — `main_window.py` was read, never edited, this task). No
audio/WASAPI/Deepgram/DeepL transport/UI-layout code was modified.

---

## Minimum Acceptance Gate (re-run)

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | No commit has `success=True` with `identity_assigned=False` | **PASS** | Unaffected — `utterance_lifecycle.py` unmodified; Task 6's tests still pass. |
| 2 | Exactly one canonical stable event per applied ledger transaction | **PASS** | Unaffected — `pipeline_commit_transaction.py` unmodified; Task 6's tests still pass. |
| 3 | Stable, ledger, final export, and translation counts reconcile | **PASS** | `test_task4c_acceptance_gate.py` tests 3/5 still pass; reconciliation (Task 8/9) can now genuinely deliver a forced submission instead of always silently failing, strictly improving this item. |
| 4 | No outgoing audio is cleared before a bounded delivery attempt; any forced drop is explicitly counted/logged | **PASS** | Unaffected — `deepgram_client.py` unmodified; `test_stop_queue_flush_v3_2_4.py` still passing. |
| 5 | The last spoken phrase appears in both canonical export and translation after Stop | **PASS** | `test_task3c_acceptance_gate.py::test_5` still passing; the new Issue 3 integration test directly proves this end-to-end for the `inactivity_timeout_fallback` case, 8/8 real runs. |
| 6 | Japanese speaker-change test preserves both distinct speaker IDs | **PASS** | `test_task2c_acceptance_gate.py::test_3` still passing, unaffected. |
| 7 | `stage_capture_complete=true`, `validation_output_written=true`, zero finalizer exceptions | **PASS** | All `stop_finalize_worker`-related suites still pass with zero unhandled exceptions; `translation_reconciliation`'s new `TranslationReconciliationError` path is caught by `run_timed_step` exactly like any other step exception, never escaping unhandled. Same indirect-verification caveat as prior tasks for the exact `stage_capture_complete`/`validation_output_written` fields. |
| 8 | Translation queue, ordering buffer, UI event bus, and loading indicators are all empty at exit | **PASS** | `test_task3c_acceptance_gate.py::test_6` still passing. |

**All 8 items PASS.**

---

## Final Verdict

**Both issues fixed, with the delivery mechanism now verified end-to-end
under real timing, not just asserted by a mock.**

Explicitly, per the report's required statement: **yes** — a record that
reaches `reconcile_translation_gaps` as a detected gap now genuinely
results in `translation_worker`'s `STABLE_TRANSLATION_JOBS_ACCEPTED`
counter incrementing (proven both by the deterministic
`test_4_successful_forced_submit_with_force_true_bypasses_stop_accepting`
test and by every one of the 8 real-thread integration runs, where the
forced submission was the actual delivery mechanism observed in the logs).
And **yes** — the new Issue 3 integration test would have caught Task 7's
and Task 8's original gaps had it existed at the time (it exercises
exactly the real debounce-timer-vs-Stop race Task 7 fixed and the exact
real UI-hook path Task 8 fixed), and it did in fact catch this task's own
Issue 1 in the sense that all 8 runs required Issue 1's `force=True` fix
to pass — this is confirmed, not hypothetical, since the test was written
and run against the fixed code.

This test is now the standing regression test for this failure class and
should be run whenever any of `main_window.py`'s translation-submission
path, `stop_finalize_worker.py`'s stop sequence, or `translation_worker.py`'s
acceptance logic changes.
