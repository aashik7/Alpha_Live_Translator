# Task 14 Report — RUN_MANIFEST.json Stale-Write Root Cause and Fix

Forensic follow-up to `TASK_13_DEBUG_REPORT.md`'s ground-truth
instrumentation. No theory in this report is asserted without a matching
execution trace or line-number citation — see the "Ground truth" sections
for the raw evidence each claim is drawn from.

---

## Phase 1 — Confirmed mechanism

### 1. Is there caching, and exactly what does it do?

Yes — `build_stop_finalize_summary()` has a run-id-scoped cache, added by
Task 9's Issue 2 fix, at
[stop_finalize_worker.py:63-64](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:63):

```python
_last_completed_run_id = ""
_last_completed_summary: dict[str, Any] = {}
```

**Cache key**: the run id from `_current_run_id_for_cache()`
([stop_finalize_worker.py:67-74](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:67)),
which itself just calls `get_current_run_identity()` (a single, unscoped
module-level global in `run_identity.py`) and reads `.run_id`.

**Cache read** (`build_stop_finalize_summary`, lines 856-862):
```python
current_run_id = _current_run_id_for_cache()
if current_run_id:
    with _state_lock:
        cached_run_id = _last_completed_run_id
        cached_summary = dict(_last_completed_summary) if _last_completed_summary else None
    if cached_summary is not None and cached_run_id == current_run_id:
        return cached_summary
```

**Cache write gating condition** (lines 974-977) — this is the
`get_stop_finalize_snapshot()["finalize_completed"]` gate the task
referenced:
```python
if snap.get("finalize_completed") and current_run_id:
    with _state_lock:
        _last_completed_run_id = current_run_id
        _last_completed_summary = dict(summary)
```
`snap.get("finalize_completed")` reflects `_stop_state["finalize_completed"]`,
which is set to `True` exactly once, at
[stop_finalize_worker.py:1857-1858](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:1857)
(new line numbers after this task's edits), immediately after
`_write_minimal_runtime_artifacts` returns and just before the "real"
synchronous `build_stop_finalize_summary(host, dg_result=dg_result)` call
at line 1873 (below).

### 2. Was the premature call written to the cache? Is `finalize_completed` itself set too early?

**No, and no.** `finalize_completed` is not set too early — it is set at
exactly one place (line 1858, per above), correctly after the real
sequence finishes. The premature call the reproduction evidence cited
(`_queue_final_ui_update` at the old line 1485, calling
`build_stop_finalize_summary(host)` at the old line 658) runs **before**
`_write_minimal_runtime_artifacts` and therefore before `finalize_completed`
is set — so the cache-write gate at line 974 correctly blocks it from ever
polluting the cache. This was directly confirmed by ground-truth execution
(see "Ground truth #1" below): the premature call's own
`compute_core_final_status()` result was never written to
`_last_completed_summary`.

### 3. Did the second (complete) call read from a stale cache, or compute fresh — and did its result reach RUN_MANIFEST.json?

The "second" call in the reproduction evidence
(`_write_minimal_runtime_artifacts`, computing
`compute_core_final_status(exclude=("run_manifest",))`) **always computes
fresh** — `compute_core_final_status()` has no cache logic at all; only
`build_stop_finalize_summary()` does. This fresh computation correctly saw
all required steps as true, and `_write_minimal_runtime_artifacts` **did**
successfully write that correct result into RUN_MANIFEST.json (confirmed —
"Ground truth #2" below shows the file correctly holding
`"final_status": "completed_pending_evidence_package"` immediately after
this write, in every single-session reproduction run).

**So neither of the two calls the task's reproduction evidence named is
the source of the wrong final content.** The manifest was written
correctly by both call 1 (protected by the gate) and call 2 (fresh, always
correct). The corruption comes from a **third** call the evidence did not
capture.

### 4. What actually writes `final_status` into RUN_MANIFEST.json — trace to source

There are exactly two writers of RUN_MANIFEST.json in the whole
application (confirmed by grep across `alpha/`):

1. `troubleshooting_paths.py:960` — the Start-time seed, writing
   `"final_status": "in_progress"`. Not relevant to Stop.
2. `troubleshooting_paths.py:1017` `update_run_manifest()`, called from
   `troubleshooting_paths.py:1029` `finalize_run_manifest(run_folder, *,
   status, artifact_flags, stop_summary)`.

`finalize_run_manifest()` has exactly one caller in the whole app:
`evidence_pointer_finalize.py:132`, inside
`finalize_evidence_pointers_completed()`, which is the function
`schedule_evidence_pointer_finalization_background()` runs on its own
independent daemon thread
([stop_finalize_worker.py:1899-1908](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:1899),
pre-fix line numbers) — this is the **third read/write path**, exactly
the case the task's Phase 1 Q4 anticipated, and `_write_minimal_runtime_artifacts`'s
own direct write (source #2 above, called from `_run_finalize_worker`
synchronously) is not the last word — this background pass's write always
runs after it and unconditionally overwrites it.

`finalize_evidence_pointers_completed()` derives its `status`/`stop_summary`
from `build_stop_finalize_summary(host)`
([evidence_pointer_finalize.py:55](Alpha_Live_Translator/alpha/utils/evidence_pointer_finalize.py:55),
pre-fix), and derives `folder` (which run's manifest it writes) from
`get_current_run_identity()` — **the same single, unscoped global** used
for the cache key in question 1. This is a background thread with no
join/wait from the main Stop sequence — it can execute at any later,
unbounded wall-clock time.

### 5. Confirmed mechanism (one sentence)

**`schedule_evidence_pointer_finalization_background()` runs its finalize
pass on an independent, un-joined daemon thread that re-derives "which
run is this" via the single, unscoped `get_current_run_identity()` global
instead of the run it was actually scheduled for; if a real Start happens
before that thread gets OS-scheduled, it pairs the OLD run's `host`
argument with the NEW run's now-current identity/folder, and
`finalize_run_manifest()` (`troubleshooting_paths.py:1029`, called from
`evidence_pointer_finalize.py:132`) unconditionally overwrites the NEW
run's already-correct RUN_MANIFEST.json (written by
`_write_minimal_runtime_artifacts` at
[stop_finalize_worker.py:807](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:807))
with a status computed from that mismatched pairing.**

### Ground truth #1 — single-session reproduction (pre-fix)

Ran one real Stop sequence through the actual production code
(`RealIntegrationHost`, real threads, real `run_timed_step`/
`_run_finalize_worker`), with a stub `_finish_graceful_stop` so
`_queue_final_ui_update`'s premature call genuinely reaches
`build_stop_finalize_summary` (matching real `main_window.py`, where that
method is always callable). Captured every `compute_core_final_status()`
call via the Task 13 diagnostic:

```
call 1 (t=...490) @ _queue_final_ui_update -> build_stop_finalize_summary (line 658)
  missing_or_failed: ['utterance_reconstruction', 'canonical_ledger_validation',
    'stable_export', 'final_export', 'translation_drain', 'loading_state_drain',
    'run_manifest', 'translation_reconciliation']
call 2 (t=...276) @ _write_minimal_runtime_artifacts (line 764)
  missing_or_failed: []
call 3 (t=...288) @ _run_finalize_worker's "real" synchronous call (line 1874)
  missing_or_failed: []
```
Final RUN_MANIFEST.json (this single session, no second Start):
`"final_status": "completed"` — **correct**, even with the exact premature
call the task's evidence described. This proved the premature call alone,
in isolation, is not sufficient to corrupt the file — confirming
questions 2/3's "no" answers above and motivating the search for the true
third writer.

### Ground truth #2 — two-session race reproduction (pre-fix, confirms question 5)

Ran session 1's Stop to completion, deliberately delayed session 1's
`schedule_evidence_pointer_finalization_background` worker by 2.5s
(simulating realistic OS thread-scheduling delay), then ran session 2's
entire Start-equivalent-through-Stop inside that window, then let session
1's delayed worker fire:

```
SESSION 1 manifest immediately after its own Stop: final_status = "completed_pending_evidence_package"  (correct)
SESSION 2 manifest immediately after its own Stop: final_status = "completed_pending_evidence_package"  (correct)
--- session 1's delayed evidence-pointer thread fires now, with the global
    identity already reporting session 2 ---
SESSION 1 folder's manifest, after waiting: unchanged (correct)
SESSION 2 folder's manifest, after waiting:
  final_status = "failed"
  final_alpha_output_written = false
```
This is a direct, executed reproduction of the exact reported defect:
session 2's already-correct manifest is overwritten with "failed" by
session 1's stale background pass, which used session 1's `host` paired
with session 2's now-current identity/folder.

---

## Phase 2 — The fix

Two changes, both required by what Phase 1 actually found (no defensive
extras):

### Fix A — scope the evidence-pointer background pass to the run it was scheduled for

**`stop_finalize_worker.py`** — at the scheduling call site
(`_run_finalize_worker`), capture this run's identity now, while it is
still guaranteed correct, and pass it through explicitly instead of
letting the background pass re-derive it later from the mutable global:

```python
_this_run_identity = get_current_run_identity()
schedule_evidence_pointer_finalization_background(
    host,
    reason="after_minimal_stop",
    run_id=str(getattr(_this_run_identity, "run_id", "") or ""),
    run_folder=str(getattr(_this_run_identity, "run_folder", "") or ""),
)
```

**`evidence_pointer_finalize.py`** — `schedule_evidence_pointer_finalization_background`
and `finalize_evidence_pointers_completed` now accept `run_id`/`run_folder`.
When `run_id` is given, the pass checks the **current** global identity
against it before doing anything else:

```python
current_run_id = str(getattr(identity, "run_id", "") or "") if identity else ""
if run_id and current_run_id != run_id:
    _log("EVIDENCE_POINTER_FINALIZE_SKIPPED_STALE_RUN", ...)
    result["error"] = "stale_run_superseded"
    return result
```

If it still matches (the normal, fast case — nearly all real Stops), the
pass proceeds exactly as before, now using the explicitly-passed
`run_folder`/`run_id` as the source of truth for `folder`/log fields
instead of re-reading the global a second time. If a newer run has since
started, the pass makes **zero reads or writes** for the stale run —
`_write_minimal_runtime_artifacts`'s already-correct synchronous write is
left untouched, and the async pass just never gets to add the "completed"
upgrade for that one stale run (a safe, honest outcome — its manifest
stays at the already-valid `completed_pending_evidence_package`, never
corrupted to `failed`). Nothing else in the fix touches the underlying
single-slot cache from question 1, which the confirmed mechanism did not
implicate.

### Fix B — the premature call no longer computes (or logs) the full summary

`_queue_final_ui_update` gained a `use_lightweight_snapshot` parameter.
The one call site that is *always* premature (`_run_finalize_worker`,
before the drain barrier) now passes `use_lightweight_snapshot=True`,
which uses `get_stop_finalize_snapshot()` (the same lightweight read
Task 10 already switched the immediate caller to) instead of
`build_stop_finalize_summary(host)` — no full/spurious status is computed
or logged at that point anymore. The other call site (the outer exception
handler, a genuine crash) is unchanged and still gets the full,
authoritative summary, since the run really did fail there.

This directly satisfies the task's secondary instruction: the premature
call no longer needed the full computation, and it now uses the same
established lightweight-snapshot pattern Task 10 already introduced
elsewhere in this file.

### Post-fix validation of both ground-truth reproductions

Re-ran Ground truth #1 (single session): 3 `compute_core_final_status()`
calls became 2 — the premature call no longer invokes it at all. Final
manifest: `"final_status": "completed"`.

Re-ran Ground truth #2 (two-session race, same delay): session 2's
manifest after session 1's delayed pass fires:
```
final_status = "completed"
```
(Session 2 got its own, promptly-scheduled evidence-pointer pass, which
correctly upgraded it independently — proving the fix doesn't just avoid
the crash but leaves the legitimate finalize pass fully functional for
the run it actually belongs to.)

---

## VALIDATE results

**1. Deterministic reproduction test** — `tests/test_task14_report.py`:
- `QueueFinalUiUpdateLightweightModeTests` (2 tests): the lightweight call
  site never calls `build_stop_finalize_summary`; the genuine-failure call
  site still does.
- `StaleEvidencePointerPassSkipsMismatchedRunTests` (2 tests): a
  mismatched current identity causes a clean skip with zero reads/writes;
  a matching one proceeds and finalizes the manifest normally.
- `SecondSessionManifestNotCorruptedByStaleFirstSessionPassTests` (1 test,
  real-thread, subprocess-isolated): full reproduction of Ground truth #2
  end-to-end against the real, persisted RUN_MANIFEST.json — asserts
  session 2's file reflects its own true outcome (`"completed"`), not
  session 1's stale write.
- All 5 pass.

**2. Task 12's 3-scenario integration test** (Japanese / short-English /
longer multi-sentence English), re-run unchanged: all three report
`final_status="completed"` — `python -m unittest tests.test_task12_report`
→ **5/5 pass**, including `ThreeReproductionScenariosCompletedStatusTest`.

**3. Full suite**: `python -m unittest discover -s tests -p "test_*.py"`
→ **139 tests, 5 failures + 2 errors + 1 skipped** (`test_final_transcript_commit_v3_2_5`
x2, `test_package_glossary_flags_85253` x4, `test_stop_finalize_v3_2_3`
x1) — the same 7 pre-existing, unrelated failures carried forward from
Task 12's baseline (134 tests then; +5 new Task 14 tests now, all
passing). **Zero regressions.**

**4. Minimum Acceptance Gate** (re-run against Task 12's own 8-item table):

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | No commit has `success=True` with `identity_assigned=False` | **PASS** | Unaffected; Task 6's tests still pass. |
| 2 | Exactly one canonical stable event per applied ledger transaction | **PASS** | Unaffected; Task 6's tests still pass. |
| 3 | Stable, ledger, final export, and translation counts reconcile | **PASS** | `test_task4c_acceptance_gate.py` all 7 tests pass. |
| 4 | No outgoing audio cleared before a bounded delivery attempt; forced drops counted/logged | **PASS** | `test_stop_queue_flush_v3_2_4.py` all 3 tests pass. |
| 5 | The last spoken phrase appears in both canonical export and translation after Stop | **PASS** | `test_task3c_acceptance_gate.py::test_5` passes; Task 12's 3-scenario test additionally proves this end-to-end with this task's fix in place. |
| 6 | Japanese speaker-change test preserves both distinct speaker IDs | **PASS** | `test_task2c_acceptance_gate.py::test_3` passes. |
| 7 | `stage_capture_complete=true`, `validation_output_written=true`, zero finalizer exceptions | **PASS** | Full suite: zero unhandled exceptions in any `stop_finalize_worker`/`evidence_pointer_finalize` path. |
| 8 | Translation queue, ordering buffer, UI event bus, and loading indicators all empty at exit | **PASS** | `test_task3c_acceptance_gate.py::test_6` passes. |

**All 8 items PASS.**

---

## Final confirmation

`git diff --stat` for this task: 3 files changed —
`alpha/utils/stop_finalize_worker.py`, `alpha/utils/evidence_pointer_finalize.py`,
and the new `tests/test_task14_report.py`. The TASK13_DIAG diagnostic
logging added in Task 13 has been removed (Phase B's purpose — locating
this exact defect — is complete; the earlier diagnostic edit's net effect
on `stop_finalize_worker.py` is now zero).

For the reproduction scenario this task set out to fix — a real user
doing Start → Stop → Start → Stop, where the first Stop's background
evidence-pointer pass is still in flight when the second Stop finishes —
RUN_MANIFEST.json, LIVE_RUN_STATUS, and the STOP_FINALIZE_COMPLETED log
event now all reflect the true, fully-reconciled state of the run they
actually belong to, confirmed by direct re-execution of the exact
scenario that previously reproduced the defect (Ground truth #2, post-fix,
above).
