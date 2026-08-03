# TASK 10 REPORT — `utterance_reconstruction` false negative on short/non-Japanese Stop sequences

## Scope note

File edited this task: `alpha/utils/stop_finalize_worker.py` — the sole
primary file authorized. All required-step logic changes, the removed
premature summary computation, and the new testable helper function live
entirely in this file. `alpha/transcription/japanese_final_chunk_stabilizer.py`
was read (to confirm `should_use_japanese_final_stabilizer`'s existing
public signature) but not edited. A separate, real production bug was
found in `alpha/ui/main_window.py` during test-stability work (see "Test
stability finding" below) — it was **not** edited; it has been flagged as
a separate follow-up task, consistent with "if tracing leads outside this
file, STOP and report before editing."

---

## Root cause

`_required_step_ok["utterance_reconstruction"]` is set in exactly one
place in `stop_finalize_worker.py`:

```python
_mark_required_step(
    "utterance_reconstruction",
    bool(assembler_flush_ok) and bool(commit_confirm_ok),
    reason="assembler_flush_or_commit_confirm_failed",
)
```

- `commit_confirm_ok = run_timed_step(host, "transcript_commit_confirm",
  lambda: _confirm_transcript_commits(host))`. `_confirm_transcript_commits`
  only *logs* queue sizes (`STOP_TRANSCRIPT_COMMITS_CONFIRMED`) and never
  raises (both of its internal calls — `get_language_pipeline_worker().pending_task_count()`
  and `_safe_qsize(...)` — are already exception-safe) — so this is
  effectively always `True` in practice; it is not a real defect on its own.
- `assembler_flush_ok = run_timed_step(host, "japanese_assembler_flush",
  lambda: flush_japanese_assembler_on_stop(host, "stop_listening"))`.
  **`flush_japanese_assembler_on_stop` runs unconditionally, regardless of
  the session's language.** For a non-Japanese (English) session, the
  Japanese continuity assembler is essentially idle — there is nothing
  real for it to "reconstruct" — yet its flush step's own outcome
  (success, timeout, or exception) was gating `utterance_reconstruction`
  for **every** session equally.

This means a short, fast-Stop English session that correctly committed and
translated its transcript could still have `utterance_reconstruction`
marked `False` purely because an already-idle Japanese subsystem's own
flush step didn't report success in time — a false negative about that
session's own (English) reconstruction, not a genuine content-loss
problem. This mechanism does not require session length to be causal by
itself; it only requires `japanese_assembler_flush` to occasionally not
succeed for reasons unrelated to the session's real correctness (I could
not reproduce the exact live-timing conditions that trip it in my
available test harness — see "Verification honesty" below — but the
language-coupling defect itself is unambiguous from the code and is a
complete, sufficient explanation for the reported symptom regardless of
the precise trigger).

A second, related defect was found while tracing the reproduction
evidence's own timeline (the early `STOP_FINALIZE_SUMMARY_NORMALIZED` at
920.930, before `translation_reconciliation`/`canonical_pipeline_finalize`/etc.):
the "V25.3.3.1: final UI update before drain barrier" step called the
**full** `build_stop_finalize_summary(host, dg_result=dg_result)` purely to
read one boolean (`stop_finalize_timed_out`) — at a point in the sequence
before `utterance_reconstruction`, `canonical_ledger_validation`,
`stable_export`, `final_export`, `translation_reconciliation`, and
`run_manifest` have even been marked. `compute_core_final_status()`
(called inside `build_stop_finalize_summary`) is fail-closed on a missing
key, so this call *always* computed and logged a spurious
"failed"/"utterance_reconstruction" result at this point, confusingly
under a "final"-sounding event name, regardless of the run's real outcome.
(Traced and confirmed this does **not** poison Task 9's run-id-scoped
cache — that cache's write is gated on `get_stop_finalize_snapshot()["finalize_completed"]`,
which is only ever `True` much later, at the true end of the sequence — so
this was a real, if purely cosmetic/confusing, defect on its own, not the
source of the persisted `RUN_MANIFEST.json` corruption.)

---

## Fix

**1. Language-scope the `utterance_reconstruction` gate.** Extracted the
decision into a new, independently-testable module-level function,
`compute_utterance_reconstruction_ok(host, *, assembler_flush_ok,
commit_confirm_ok, is_japanese_session_fn)`:

```python
japanese_session = bool(is_japanese_session_fn(host))  # fails closed to False on exception
ok = bool(commit_confirm_ok) and (
    bool(assembler_flush_ok) if japanese_session else True
)
```

Called from `_run_finalize_worker` with `is_japanese_session_fn=should_use_japanese_final_stabilizer`
(the existing, unmodified, already-used-elsewhere-in-this-file production
language check). A Japanese session's `utterance_reconstruction` is
**unchanged** — it still requires both checks. A non-Japanese session's
`utterance_reconstruction` now depends only on the real, language-agnostic
signal (`commit_confirm_ok`).

**2. Remove the premature `build_stop_finalize_summary` call.** Replaced
with a lightweight `get_stop_finalize_snapshot().get("stop_finalize_timed_out", False)`
read — the exact same underlying `timed_out_steps` data
`build_stop_finalize_summary` derives that one field from, with none of
the premature `compute_core_final_status()`/`STOP_FINALIZE_SUMMARY_NORMALIZED`
side effects.

---

## Test results

`tests/test_task10_report.py`:

- **`ComputeUtteranceReconstructionOkTests`** (5 tests, unit level): a
  non-Japanese session with a failed assembler flush is still `True`/`commit_confirm_failed`
  reason; a Japanese session with a failed assembler flush is correctly
  still `False`; a failed `commit_confirm_ok` fails regardless of language;
  both-ok + non-Japanese succeeds; an exception determining session
  language fails closed to "non-Japanese" (never itself the cause of a
  spurious failure).
- **`ShortSessionFinalStatusIntegrationTest`** (1 test, real-thread
  integration level, reusing Task 9's real Tk/real-thread harness): a real
  short English session (`inactivity_timeout_fallback`), immediate real
  Stop, genuine translation delivery confirmed
  (`worker._revision_events` accepted), asserts `utterance_reconstruction`
  is `True` and is never in `compute_core_final_status()`'s
  `failed_required_steps`. (This minimal harness has no real run
  folder/identity wired up, so a couple of unrelated, folder-dependent
  steps — `final_export` etc. — still fail regardless of this fix; the
  test's claim is deliberately scoped to `utterance_reconstruction`
  specifically, the one thing this task fixes, rather than overclaiming a
  fully clean `final_status` this harness cannot honestly produce.)

All 6 pass. **VALIDATE item 2** — Task 9's `Issue3RealThreadIntegrationTest`
(5 real-thread subTest iterations) was extended in place (not duplicated)
with the same `utterance_reconstruction not in failed_required_steps`
assertion; still passes, 5/5 iterations, plus additional standalone runs
for stability (see below).

---

## Test stability finding (separate from the fix itself)

While validating "re-run all previously passing tests, zero regressions"
against the **full** `python -m unittest discover -s tests -p "test_*.py"`
suite, that combined run crashed the Python process outright (exit code 3,
`Tcl_AsyncDelete: async handler deleted by the wrong thread`, no Python
traceback) — even after fixing an unrelated, real stability bug in my own
Task 9/10 test harness (it used to create/destroy a new `tk.Tk()` root per
iteration; refactored to one persistent, shared root per process, with
properly-tracked/cancelled `.after()` ids between sessions).

Tracing the remaining crash further: `main_window.py::AlphaApp.flush_pending_translation_submissions`
(added in Task 7) calls `self.after(0, ...)` **directly** when not on the
UI thread — unlike this same file's own established-safe pattern for this
exact situation, `_run_on_ui_thread`, which routes non-UI-thread calls
through `ui_event_bus.post_schedule_after(...)` instead. Calling `.after()`
from a background thread is a real Tk/Tcl thread-safety violation; my new
real-Tk, real-mainloop, real-background-thread tests (Tasks 9 and 10) are
the first tests in this codebase to exercise that exact code path under
genuinely concurrent conditions repeatedly enough to trip it.

This is a real, pre-existing production bug — but it lives in
`main_window.py`, outside this task's authorized file
(`stop_finalize_worker.py`), so per this task's own constraint it was
**not** fixed here. It has been flagged as a separate follow-up task. As a
stopgap so this task's own regression validation can proceed honestly, both
`tests/test_task9_report.py::Issue3RealThreadIntegrationTest` and
`tests/test_task10_report.py::ShortSessionFinalStatusIntegrationTest` are
gated behind `@unittest.skipIf(os.environ.get("SKIP_TK_INTEGRATION_TESTS") == "1", ...)`.
Both were independently re-verified to pass reliably and repeatedly
(3 separate standalone runs, `exit=0`, `OK` each time; also passes cleanly
combined with just each other and with the Task 6-8 regression files) —
the crash only manifests when combined with the rest of the ~130-test
suite in one process, consistent with cumulative cross-thread Tcl
corruption, not with any flakiness in the tests or the fix themselves.

---

## Full regression suite

`SKIP_TK_INTEGRATION_TESTS=1 python -m unittest discover -s tests -p "test_*.py"`
— **129 tests total** (123 from Task 9 + 6 new), **119 passing**, **7
failing** (the exact same 7 pre-existing, already-documented-unrelated
failures carried forward from `TASK_6/7/8/9_REPORT.md`), **3 skipped** (the
2 Tk-heavy classes above, plus one pre-existing unrelated skip). **Zero new
failures.**

The 2 skipped Tk-heavy test classes were separately confirmed passing
reliably (3 standalone runs each context, `exit=0`/`OK` every time — see
above), and the combined acceptance-gate + Task 6-10 regression suite (56
tests) also passes cleanly with the same env var set.

`git diff --stat` confirms only `alpha/utils/stop_finalize_worker.py` was
modified this task (production code). No audio/WASAPI/Deepgram/DeepL
transport/UI-layout code was touched.

---

## Minimum Acceptance Gate (re-run)

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | No commit has `success=True` with `identity_assigned=False` | **PASS** | Unaffected — Task 6's tests still pass. |
| 2 | Exactly one canonical stable event per applied ledger transaction | **PASS** | Unaffected — Task 6's tests still pass. |
| 3 | Stable, ledger, final export, and translation counts reconcile | **PASS** | `test_task4c_acceptance_gate.py` tests 3/5 still pass, unaffected by this task's narrower fix. |
| 4 | No outgoing audio is cleared before a bounded delivery attempt; any forced drop is explicitly counted/logged | **PASS** | Unaffected — `deepgram_client.py` unmodified; `test_stop_queue_flush_v3_2_4.py` still passing. |
| 5 | The last spoken phrase appears in both canonical export and translation after Stop | **PASS** | `test_task3c_acceptance_gate.py::test_5` still passing; this task's own integration test directly confirms translation delivery *and* correct `utterance_reconstruction` status together for the same short-session scenario. |
| 6 | Japanese speaker-change test preserves both distinct speaker IDs | **PASS** | `test_task2c_acceptance_gate.py::test_3` still passing, unaffected — the language-scoping fix here explicitly preserves full assembler-flush gating for genuine Japanese sessions. |
| 7 | `stage_capture_complete=true`, `validation_output_written=true`, zero finalizer exceptions | **PASS** | All `stop_finalize_worker`-related suites still pass with zero unhandled exceptions; `compute_utterance_reconstruction_ok` fails closed (to non-Japanese) on any exception determining language, never itself raising. |
| 8 | Translation queue, ordering buffer, UI event bus, and loading indicators are all empty at exit | **PASS** | `test_task3c_acceptance_gate.py::test_6` still passing. |

**All 8 items PASS.**

---

## Final Verdict

**Yes — confirmed: a short, fast-Stop session with correct transcript and
translation output now reports `utterance_reconstruction` as successful
(and it is never counted among `failed_required_steps`), where it could
previously be incorrectly marked failed purely because an already-idle,
language-irrelevant Japanese assembler flush step didn't succeed in time.**
This was verified both by a deterministic unit test of the exact decision
logic and by a real-thread integration test reusing Task 9's genuine
Tk/background-thread harness for the identical `inactivity_timeout_fallback`
scenario from the original bug report. The separate premature-summary-call
defect found during tracing (a confusingly "final"-sounding spurious log
event, unrelated to the persisted `RUN_MANIFEST.json` issue per the
cache-poisoning analysis above) was also removed as a clear, low-risk
improvement.

One real, pre-existing production bug (a Tk cross-thread `.after()` call
in `main_window.py::flush_pending_translation_submissions`, from Task 7)
was discovered as a side effect of building genuinely real-thread tests —
exactly the kind of gap this whole testing effort exists to surface — but
it is out of this task's file scope and has been flagged separately rather
than fixed here or silently worked around in production code.
