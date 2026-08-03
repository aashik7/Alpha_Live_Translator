# TASK 12 REPORT — Exhaustive audit of the silent-cascade failure class

## Scope note

Production file edited this task: `alpha/utils/stop_finalize_worker.py` —
the sole primary/authorized file. Two test files were also edited:
`tests/test_task7_report.py` (a pre-existing test fixture gap exposed —
not caused — by an unrelated background fix to `main_window.py`; see
"Test stability notes" below) and the new `tests/test_task12_report.py`.
No other production file was touched. Two genuine, separate, out-of-scope
issues were found during this task's own testing and were **flagged, not
fixed**, per "if tracing leads outside this file, STOP and report before
editing" (see "Findings flagged separately").

---

## Audit: every `_mark_required_step` call site

| # | Required step | Depends on | Code between it and the previous `_mark_required_step` | Verdict |
|---|---|---|---|---|
| 1 | `audio_summary` | `run_timed_step(..., "drain_audio_queue", ...)` result + plain dict `.get()` on `audio_drain` | `run_timed_step` call only | **Already safe** — `run_timed_step` catches any exception in the wrapped function; the follow-up condition is pure dict access, cannot raise. |
| 2 | `raw_event_persistence` | `run_timed_step(..., "deepgram_graceful_stop", ...)` result + plain dict `.get()` on `dg_result` | `host._stop_event.set()`, `run_timed_step` call | **Already safe** — same pattern as #1. |
| 3 | `utterance_reconstruction` | `compute_utterance_reconstruction_ok(...)`, called **directly, unwrapped** | `close_transcript_gate`/`japanese_assembler_flush`/`cancel_scheduled_tasks`/`language_worker_stop` steps, `_queue_final_ui_update`, `ui_transcript_drain` step, `transcript_commit_confirm` step | **CONFIRMED SILENT-CASCADE BUG — FIXED.** The only required-step computation in the entire sequence with zero containment. Any exception here (regardless of source) propagated straight out of `_run_finalize_worker` to the outer exception handler, skipping every `_mark_required_step` call for the rest of the function — exactly the reported "utterance_reconstruction onward, all failing, `failed_steps=[]`/`timed_out_steps=[]`" pattern. |
| 4 | `translation_reconciliation` | `run_timed_step(..., "translation_reconciliation", ...)` result | `translation_unit_final_flush`/`flush_pending_translation_debounce` steps | **Already safe** — `run_timed_step` catches `reconcile_translation_gaps`'s exceptions (including the deliberate `TranslationReconciliationError`, Task 9); follow-up condition is `bool(reconciliation_ok)`, cannot raise. |
| 5 | `translation_drain` / `loading_state_drain` | Computed inside `_translation_worker_shutdown`, itself wrapped in `run_timed_step(..., "translation_worker_shutdown", ...)`, **plus** its own internal `try/except` that explicitly marks both `False` on any exception | — | **Already safe** — doubly protected: `run_timed_step` containment, and an inner `except Exception as exc:` block that marks both steps `False` with `reason="shutdown_exception"` even if the internal try body raises past the `worker is None` no-op branch. |
| 6 | `canonical_ledger_validation` / `stable_export` | Computed inside `_canonical_finalize`, wrapped in `run_timed_step(..., "canonical_pipeline_finalize", ...)`, **plus** an explicit external fallback (`if not canonical_finalize_ok: _mark_required_step(..., False, "step_timeout_or_exception")` for both) | — | **Already safe** — matches the established, correct pattern. |
| 7 | `final_export` | Computed inside `_write_final_export`, wrapped in `run_timed_step(..., "write_final_alpha", ...)`, **plus** an explicit external fallback | — | **Already safe** — same established pattern. |
| 8 | `run_manifest` | Marked as the last line of `_write_minimal_runtime_artifacts`, called **directly, unwrapped** | `flush_audio_temp_on_stop`, `finalize_stall_classifications`, host-flag resets, `sync_non_authoritative_aliases_from_sealed_final`/`verify_final_export_seal`, `export_alpha_evidence_on_stop`, `_write_translation_and_ui_evidence_streams` (each individually try/except-wrapped or otherwise safe) | **CONFIRMED SILENT-CASCADE BUG (latent) — FIXED.** Any exception anywhere in `_write_minimal_runtime_artifacts`'s body (writing `RUN_ARTIFACTS_INDEX.txt`, `LIVE_RUN_STATUS`, or `RUN_MANIFEST.json`, or in `get_current_run_identity()`/`get_artifact_path()` before any of those writes) would propagate out of `_run_finalize_worker` entirely, before ever reaching this step's own marking. Same class as #3, one step later, with `run_manifest` itself (rather than something after it) as the first casualty. Not the specific instance in this task's reproduction evidence (which reached `STOP_FINALIZE_COMPLETED`, proving `_write_minimal_runtime_artifacts` did complete in that run) but a confirmed, real, same-class latent defect. |

Two blocks (`translation_drain`/`loading_state_drain` and `canonical_ledger_validation`/`stable_export`/`final_export`) were already correctly protected — they served as the reference pattern for fixing #3 and #8.

---

## Fixes

### `utterance_reconstruction` (block #3)

The direct, unwrapped call was replaced with the same `run_timed_step` +
mutable-result-container idiom already used everywhere else in this file
(e.g. `audio_drain`/`dg_result`):

```python
utterance_reconstruction_result: dict[str, Any] = {}

def _compute_utterance_reconstruction() -> None:
    ok, reason = compute_utterance_reconstruction_ok(...)
    utterance_reconstruction_result["ok"] = ok
    utterance_reconstruction_result["reason"] = reason

utterance_reconstruction_step_ok = run_timed_step(
    host, "utterance_reconstruction_check", _compute_utterance_reconstruction
)
_mark_required_step(
    "utterance_reconstruction",
    bool(utterance_reconstruction_step_ok) and bool(utterance_reconstruction_result.get("ok")),
    reason=str(utterance_reconstruction_result.get("reason") or "step_timeout_or_exception"),
)
```

Added `"utterance_reconstruction_check": 500.0` to `_STEP_TIMEOUTS_MS`.

### `run_manifest` (block #8)

The direct call was wrapped in `run_timed_step`, with an explicit fallback
mirroring the `canonical_ledger_validation`/`stable_export`/`final_export`
pattern:

```python
write_minimal_artifacts_ok = run_timed_step(
    host,
    "write_minimal_runtime_artifacts",
    lambda: _write_minimal_runtime_artifacts(host, dg_result=dg_result),
)
if not write_minimal_artifacts_ok:
    _mark_required_step("run_manifest", False, reason="step_timeout_or_exception")
```

Added `"write_minimal_runtime_artifacts": 2000.0` to `_STEP_TIMEOUTS_MS`
(this step does real file I/O — `RUN_ARTIFACTS_INDEX.txt`, `LIVE_RUN_STATUS`,
`RUN_MANIFEST.json`).

Both fixes preserve the exact reasons/log events every other block already
produces (`STOP_FINALIZE_STEP_FAILED` with exception type/message/traceback,
via `run_timed_step`'s own existing mechanism) — no new failure-reporting
mechanism was invented; the two previously-unprotected blocks were simply
brought up to the same standard already used by the other six.

### Item 4 — does `compute_utterance_reconstruction_ok` (or
`should_use_japanese_final_stabilizer` inside it) actually raise in a real
running session?

Read in full: `compute_utterance_reconstruction_ok` already wraps its one
call to `is_japanese_session_fn(host)` in its own `try/except` (added in
Task 10) and is otherwise pure boolean logic on already-computed booleans
— it cannot raise as currently written, confirmed by direct testing
(`ComputeUtteranceReconstructionOkTests`, Task 10, still passing). The
**call site**, however, had zero containment of its own — a latent,
structural risk regardless of whether this exact function can raise today
(a future change to it, or to how its arguments are constructed, could
reintroduce exactly this failure mode with no warning). Fixing the call
site (rather than relying on the callee staying exception-safe forever)
is the correct, durable fix, and is what was implemented.

---

## VALIDATE

### Item 1 — exception-path tests for both fixed blocks

`tests/test_task12_report.py`:

- **`UtteranceReconstructionCascadeFixTests`** — forces
  `compute_utterance_reconstruction_ok` to raise during a real Stop
  sequence (real Tk root, real background finalize thread, real
  translation worker — the Task 9 harness). Confirms: (a) `STOP_FINALIZE_STEP_FAILED`
  is logged for `utterance_reconstruction_check` with `exception_type="RuntimeError"`
  and the exact exception message; (b) `utterance_reconstruction` is
  marked `False`; (c) **every other required step**
  (`canonical_ledger_validation`, `stable_export`, `final_export`,
  `translation_reconciliation`, `translation_drain`, `loading_state_drain`,
  `run_manifest`) is still correctly marked `True` — `failed_required_steps`
  has exactly one entry, not eight.
- **`RunManifestCascadeFixTests`** — forces `_write_minimal_runtime_artifacts`
  to raise during a real Stop sequence. Confirms the same three things for
  `run_manifest`, plus that every step running **before** it
  (`audio_summary` through `loading_state_drain`) is unaffected, and that
  the sequence still reaches its natural end (`worker_done=True`) instead
  of aborting into the outer exception handler.
- **`RunTimedStepContainmentGuaranteeTests`** — the foundational property
  every already-safe block (1, 2, 4, 5, 6, 7 in the table) depends on:
  a function that raises inside `run_timed_step` never propagates past it,
  and the caller can still mark that step `False` and continue.

All pass.

### Item 2 — combined three-scenario `final_status="completed"` test

`ThreeReproductionScenariosCompletedStatusTest` drives, in the same test
run: a **Japanese** session (real `accept_boundary_proposal` commit +
real `enqueue_stable_segment`), a **short English** session with
`inactivity_timeout_fallback` (the exact original Task 7-9 reproduction
shape), and a **longer, multi-sentence English** session (two immediate
`speech_final=True` commits) — each with a genuine committed,
translation-eligible record and a genuinely delivered translation job, and
a real `run_folder` (a real temp directory, patched in via
`get_current_run_identity`) so the folder-dependent steps
(`canonical_ledger_validation`/`stable_export`/`final_export`/`run_manifest`)
can genuinely succeed rather than being scoped around, as Task 10's own
test had to do. All three assert `final_status == "completed_pending_evidence_package"`
with `failed_required_steps == []`. All three pass.

Each scenario runs in its **own fresh subprocess** rather than reusing one
shared in-process host across all three — see "Test stability notes"
below for why.

---

## Test stability notes

**1. Cross-scenario `final_export` state leak (found, not fixed — out of
`stop_finalize_worker.py`'s scope).** Running two or more real Stop
sequences back-to-back in the *same process/host* (matching a real user
doing Start→Stop→Start→Stop without restarting the app) was found to make
the **second and every later** session's `final_export` step fail —
`write_final_alpha_output_from_snapshot` returns `None` even with a
genuinely valid, non-empty frozen ledger snapshot for that session.
Confirmed via instrumentation: no exception is raised anywhere in the
chain; the failure is `write_final_once()` (in
`final_artifact_authority.py`) itself reporting `ok=False` for reasons not
yet identified, in `run_artifacts.py`/`final_artifact_authority.py` — both
outside this task's primary file. Calling
`final_artifact_authority.reset_final_export_authority()` between
sessions did **not** fix it, ruling out the obvious suspect. This is
**flagged as a separate follow-up task**, not fixed here. All of this
task's own multi-scenario/multi-exception tests were restructured to run
each real-Stop-sequence scenario in its own fresh subprocess specifically
to avoid this confound and prove the actual claims this task needs
(the cascade fix; three genuinely-completed session shapes) without
depending on that unrelated gap being fixed first.

**2. Task 7's `Fix2PendingTranslationFlushTests` regression (found and
fixed — a test-fixture gap, not a `stop_finalize_worker.py` issue).**
While validating the full suite, `test_task7_report.py`'s
`test_1_pending_debounced_job_is_flushed_exactly_once` and
`test_3_two_pending_jobs_both_flushed_independently` were failing — caused
by an unrelated, already-completed background fix to
`main_window.py::flush_pending_translation_submissions` (the cross-thread
Tk `.after()` call flagged after Task 10, fixed by a separately-run
background task before this task began). That fix correctly routes the
off-UI-thread branch through the real `ui_event_bus` instead of calling
`self.after()` directly — but Task 7's lightweight, non-Tk test host never
ran an event-bus drain loop, so the flush's background-thread branch now
blocks for its full timeout and returns 0. Fixed by calling
`register_ui_main_thread()` in that test class's `setUp()`, so
`flush_pending_translation_submissions` takes its synchronous same-thread
branch (the event-bus-routing branch itself is exercised for real by this
task's own and Tasks 9/10's real-Tk integration tests, which do run a
genuine drain loop). This is a test-only change; `main_window.py` was not
touched by this task.

---

## Full regression suite

`python -m unittest discover -s tests -p "test_*.py"` — **134 tests total**
(129 from Task 10 + 5 new), **126 passing**, **7 failing** — the exact same
7 pre-existing, already-documented-unrelated failures carried forward from
`TASK_6/7/8/9/10_REPORT.md`. **Zero new failures**, no process crash.

The combined acceptance-gate + all prior task regression suites (Tasks
2C/3C/4C, `test_stop_queue_flush_v3_2_4`, Tasks 6-10, 12 — 61 tests) also
run cleanly together (`OK`).

`git diff --stat` confirms only `alpha/utils/stop_finalize_worker.py` was
modified as production code this task. No audio/WASAPI/Deepgram/DeepL
transport/UI-layout code was touched.

---

## Minimum Acceptance Gate (re-run)

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | No commit has `success=True` with `identity_assigned=False` | **PASS** | Unaffected; Task 6's tests still pass. |
| 2 | Exactly one canonical stable event per applied ledger transaction | **PASS** | Unaffected; Task 6's tests still pass. |
| 3 | Stable, ledger, final export, and translation counts reconcile | **PASS** | `test_task4c_acceptance_gate.py` tests 3/5 still pass; this task's 3-scenario test additionally proves all four required steps genuinely reconcile end-to-end for three different session shapes with a real run folder. |
| 4 | No outgoing audio is cleared before a bounded delivery attempt; any forced drop is explicitly counted/logged | **PASS** | Unaffected; `test_stop_queue_flush_v3_2_4.py` still passing. |
| 5 | The last spoken phrase appears in both canonical export and translation after Stop | **PASS** | `test_task3c_acceptance_gate.py::test_5` still passing; this task's 3-scenario test proves it end-to-end for all three reproduction shapes. |
| 6 | Japanese speaker-change test preserves both distinct speaker IDs | **PASS** | `test_task2c_acceptance_gate.py::test_3` still passing, unaffected. |
| 7 | `stage_capture_complete=true`, `validation_output_written=true`, zero finalizer exceptions | **PASS** | All `stop_finalize_worker`-related suites pass with zero unhandled exceptions; this task's fix specifically strengthens this guarantee — an exception in any single required-step computation can no longer escape `_run_finalize_worker` unhandled. |
| 8 | Translation queue, ordering buffer, UI event bus, and loading indicators are all empty at exit | **PASS** | `test_task3c_acceptance_gate.py::test_6` still passing. |

**All 8 items PASS.**

---

## Final Verdict

**Confirmed and fixed: an exception in any single required-step
computation can no longer suppress marking of any other required step.**
Two genuine instances of the silent-cascade class were found by exhaustive
audit (`utterance_reconstruction`, matching the reported reproduction
exactly, and `run_manifest`, a latent instance of the same defect one step
later in the sequence) and both are now fixed using the same
`run_timed_step`-containment idiom already correctly used by the other six
blocks in this file. Two dedicated exception-injection tests prove, on a
real Stop sequence, that forcing either block to raise now marks only that
one step `False` (with a logged exception type/message) while every other
step is still correctly marked based on its own real condition — not
skipped. A combined three-scenario test (Japanese, short English
`inactivity_timeout_fallback`, and longer multi-sentence English) proves
all three now genuinely reach `final_status="completed_pending_evidence_package"`
end-to-end. The full suite shows zero regressions and all 8 Minimum
Acceptance Gate items pass. Two separate, genuine issues (a cross-session
`final_export` state leak, and — already resolved by a parallel background
fix — a cross-thread Tk call) were found along the way and handled
appropriately: flagged for follow-up rather than fixed inside this task's
authorized scope.
