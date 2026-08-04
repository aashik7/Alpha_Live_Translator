# Task 13 Debug Report — Forensic Ground-Truth Investigation

Scope: `alpha/utils/stop_finalize_worker.py` only. Phase A is git archaeology
(read-only). Phase B adds temporary diagnostic logging only — **no
behavioral change was made in this task.** No fix is proposed here; that is
Task 14, after a live run captures the new diagnostic output.

---

## Phase A — Git archaeology

### A1/A2. Full commit history of `stop_finalize_worker.py`

`git log --follow --oneline -- alpha/utils/stop_finalize_worker.py` returns
exactly 7 commits. All are authored (as git author) by
`Md. Tariqul Islam Ashik <aashik7@gmail.com>` — author identity alone does
not distinguish tool origin here, since commits made through both Cursor
and Claude Code land under your account. Tool origin is instead visible via
each commit's `Co-authored-by` trailer and the shape/size of its diff.

| Commit | Date | Subject | Co-author trailer | Lines to this file |
|---|---|---|---|---|
| `ea193ed` | 2026-08-04 02:00:33 +0900 | Close silent required-step cascade class in stop_finalize_worker.py | Claude Sonnet 5 | +56/-5 |
| `f3cbd56` | 2026-08-03 18:03:44 +0900 | Fix off-thread Tk calls in translation flush/debounce and stabilize Tk-based tests | Claude Sonnet 5 | +402/-9 |
| `bf57ece` | 2026-08-03 09:40:31 +0900 | Publish current Alpha Live Translator repair state | **Cursor** | +322/-359 |
| `6102c03` | 2026-07-28 18:43:51 +0900 | Publish current Alpha Live Translator project as the active production tree. | **Cursor** | +1604/-0 (file added at this path) |
| `0b57b7a` | 2026-07-23 10:49:26 +0900 | Sync local project sources to GitHub, including V26.5.1 Japanese TEST repairs. | **Cursor** | (bulk sync, file present) |
| `8b8c00e` | 2026-07-22 14:37:53 +0900 | Upload Japanese TEST program source with architecture map. | none | (bulk upload) |
| `7b4c78a` | 2026-07-17 11:22:31 +0900 | Upload Alpha Translator project source and version history. | none | (bulk upload) |

### A3/A4. Cross-reference against the documented task trail — flagged commits

**Task-report-file provenance** (`git log --follow --oneline -- TASK_*.md`
for every report from `TASK_1A_FINDINGS.md` through `TASK_10_REPORT.md`):
every single one of those files — spanning Tasks 1A through 10, i.e. the
entire pre-Task-12 history — was added in **one commit, `f3cbd56`**. Commit
granularity in this repo is not 1:1 with "Task N": most of the documented
task trail was squashed into a small number of large publish/sync commits,
not committed individually. This matters for reading the table above: a
commit touching hundreds of lines is not automatically suspicious, since
that is how nearly all prior work was landed.

Given that, two commits are worth flagging explicitly, with different
verdicts:

**`bf57ece` (Cursor, 2026-08-03 09:40) — flagged, but appears accounted
for.** This is the commit that *introduces* the entire required-step
architecture this investigation revolves around: `_REQUIRED_SYNC_STEPS`,
`_mark_required_step`, `compute_core_final_status`. Confirmed via
`git show bf57ece -- .../stop_finalize_worker.py`:
```
+_REQUIRED_SYNC_STEPS = (
+def _mark_required_step(name: str, ok: bool, *, reason: str = "") -> None:
+def compute_core_final_status(*, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
+            "failed_required_steps": missing_or_failed,
```
It also rewrites large parts of `_run_finalize_worker` to call
`_mark_required_step` at each stage, and removes several stray
`build_stop_finalize_summary(...)` calls from mid-sequence positions. This
matches `REPAIR_PLAN.md`'s own **"Task 4 — Finalisation and cleanup: fail-
closed status; evidence stream separation; reconciliation"**, and
`REPAIR_PLAN.md` explicitly prescribes this work be done as one of "four
controlled Cursor tasks." So: a Cursor-native edit, not individually
attributable to a numbered commit, but its content matches a documented,
planned phase — not a rogue or accidental change. It is, however, the
**origin point of the exact architecture Tasks 9/10/12 have each found one
uncontained instance of a bug in**, so it deserves scrutiny in Phase B
regardless of being "accounted for."

**`6102c03`, `0b57b7a`, `8b8c00e`, `7b4c78a` — not flagged as suspicious.**
These are the earliest commits, predating `REPAIR_PLAN.md`'s own Phase 0
baseline (`REPAIR_PLAN.md` line 35 names `6102c03f8fd40600d4bf9304d5199042100950f2`
verbatim as "the failed commit" the whole repair plan branches from). They
represent the pre-repair source history, not part of the Task 1–12 repair
trail.

**No commit was found that is unaccounted for or looks like a stray/
accidental edit.** The one genuine gap is *attribution resolution*, not
content: `bf57ece` cannot be split into "which of Cursor's own internal
Task 1–4 sub-steps changed which line," because it was squashed before
being pushed. If a bug in the original fail-closed design (as opposed to a
bug introduced by any of the later point-fixes) is the real cause of the
recurring symptom, `bf57ece` is where to keep looking — but that requires
runtime evidence, which is what Phase B is for.

**`f3cbd56` (Claude Sonnet 5, 2026-08-03 18:03) — checked, fully
accounted for.** Despite its subject line being about the Tk `.after()`
threading fix, its diff to this file is large (+402/-9) because it also
carries Task 9's Issue 1/Issue 2 fixes and Task 10's fix, each with
explicit in-code comments citing their own report:
```
+# fixes TASK_9_REPORT.md Issue 2: build_stop_finalize_summary() is called
+class TranslationReconciliationError(Exception):
+def compute_utterance_reconstruction_ok(
+        # fixes TASK_9_REPORT.md Issue 2: build_stop_finalize_summary() now
```
This confirms Tasks 9 and 10's own code changes, and the Tk fix, were
committed together (this is consistent with the earlier-confirmed
git-merge task: `f3cbd56` was the tip of `agent/share-current-code-state`,
fast-forward-merged into `main` with no conflicts). No unaccounted content
found in this commit.

---

## Phase B — Runtime instrumentation (in place, no behavior changed)

### B1. Where `failed_required_steps` is assembled

There is exactly **one** point in the file where this list is derived from
the underlying per-step state — `compute_core_final_status()`,
[stop_finalize_worker.py:160-192](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:160):

```python
missing_or_failed = [
    name
    for name in _REQUIRED_SYNC_STEPS
    if name not in exclude and not _required_step_ok.get(name, False)
]
```

Every other place the literal key `"failed_required_steps"` appears
(`build_stop_finalize_summary`, ~line 897) reads it back out of this
function's return value — it is never independently recomputed elsewhere.
So this is the single correct instrumentation point: whatever produces the
live symptom (every step from `utterance_reconstruction` onward reported
failed) must be visible here as the true state of the module-level
`_required_step_ok` dict at the moment this function runs.

### B2/B3/B4. Diagnostic logging added

Inserted immediately after `missing_or_failed` is computed and before the
`if missing_or_failed:` branch, so it fires on *every* call regardless of
outcome — [stop_finalize_worker.py:180-197](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:180):

```python
# TASK_13_DEBUG_REPORT.md Phase B: temporary ground-truth diagnostic.
# Captures the exact underlying state and call stack at the single
# point failed_required_steps is derived, so the next live failure can
# be traced to a real cause instead of another guess. Remove once
# Task 14 has a confirmed root cause.
try:
    freeze_guard_log(
        "TASK13_DIAG_CORE_FINAL_STATUS_SNAPSHOT",
        marker="TASK13_DIAG",
        required_step_ok_snapshot=dict(_required_step_ok),
        required_sync_steps=list(_REQUIRED_SYNC_STEPS),
        exclude=list(exclude),
        missing_or_failed=list(missing_or_failed),
        call_stack="".join(traceback.format_stack()),
    )
except Exception:
    pass
```

What this captures, matching the spec:
- **(a) Full call stack** — `traceback.format_stack()`, so every future log
  line shows exactly which caller (`build_stop_finalize_summary`,
  `_run_finalize_worker`, a test, etc.) invoked
  `compute_core_final_status()` and from where.
- **(b) Real underlying state before transformation** —
  `required_step_ok_snapshot=dict(_required_step_ok)` is a copy of the raw
  module-level dict *as it actually stood* at that instant, before it's
  reduced to the `missing_or_failed` list. This is the ground truth: if the
  live symptom recurs, this snapshot will show directly whether the steps
  from `utterance_reconstruction` onward are truly missing from the dict
  (confirming the "exception skipped every subsequent `_mark_required_step`
  call" theory) or whether they're present but `False` for some other
  reason (which would disprove that theory and point elsewhere).
- **(c) Distinguishing marker** — both the event name
  `TASK13_DIAG_CORE_FINAL_STATUS_SNAPSHOT` and the redundant `marker=
  "TASK13_DIAG"` field make this trivially greppable in the evidence
  package log, separate from all pre-existing `freeze_guard_log` events.
- Routed through `freeze_guard_log` (already imported at the top of this
  file — no new dependency), so it lands in the same evidence package you
  already know how to collect; no separate debug console or attached
  debugger required.
- Wrapped in a bare `try/except: pass` so the diagnostic itself can never
  become a new failure source or change any return value.

### Verification that no behavior changed

- `ast.parse()` on the file after the edit — syntax OK.
- Re-ran `tests/test_task9_report.py` and `tests/test_task10_report.py`
  (15 tests) — all still pass, same as before this change. The new logging
  fires silently on every existing pass/fail path with no assertion
  differences.

`git diff --stat` for this task: 1 file changed
(`alpha/utils/stop_finalize_worker.py`), 17 insertions, 0 deletions — pure
addition, no existing line altered, no production logic changed.

---

## Next step (not part of this task)

Run one more live session that reproduces the `STOP_FINALIZE_COMPLETED`
failure (every required step from `utterance_reconstruction` onward
reported failed, `failed_steps=[]`/`timed_out_steps=[]` empty). Pull the
new `TASK13_DIAG_CORE_FINAL_STATUS_SNAPSHOT` entries from that run's
evidence package/freeze-guard log and hand them back — the
`required_step_ok_snapshot` and `call_stack` fields from the specific call
where `missing_or_failed` first contains all 8 trailing steps will show
the real code path and real state, which Task 14 will fix against.
