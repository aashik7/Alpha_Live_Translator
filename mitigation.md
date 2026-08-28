# Unrecoverable-failure audit and mitigation plan

**Status:** steps 1, 2 and 3 implemented and verified. Step 4 remains.
Run `python tools/verify_mitigation_claims.py` for the current state — it now
asserts the FIXED shape (27/27), not the presence of the defects.
**Raised:** 2026-08-27, after item 94.
**Re-verified twice:** 2026-08-27 and again 2026-08-28 under
`tools/verify_mitigation_claims.py`, which re-checks all 14 structural claims
mechanically so they no longer rest on anyone's memory. Run it yourself: it
prints PASS/FAIL per claim.

**First re-verification, 2026-08-27:** All 25 code references checked line by
line against the tree; all correct. Five things were wrong and are corrected
below rather than silently replaced — the `log_exception` caller list named a
module that does not use it; the scanner count disagreed with the committed tool;
two read-confirmed findings did not say why they were not executed; the
one-way-state half of the tool output was never reconciled against the finding
list; and the test baseline was stated as a pass count, which varies with Tk
availability and would have raised a false alarm. **Step 3's A5 fix was also
wrong** and is corrected in place — see A5.
**Ownership:** steps 1 and 2 are being implemented from a second account; steps 3
and 4 follow, after step 1–2 is verified against the acceptance criteria below.

---

## 0. The class of bug

Item 94 was one instance of a shape this repo keeps producing:

> A component detects a failure, disables itself to protect integrity, and the
> path that would re-enable it is never reachable in that session.

In item 94 the assembler's commit gate scoped itself to
`_current_canonical_utterance_id`, but the only site that mints a new one sits
*below* the gate's own `return`. Clearing the gate required a new utterance id;
minting one required getting past the gate. One recoverable rejection cost 16.5
minutes of a client session, 85 discarded commits, and the run still reported
`completed`.

The audit below looked for every other instance of that shape. Eight were found: six threads that end on one exception (A1-A6) and two flags with no reachable clear (B1-B2). **A function is
allowed to fail. A function that cannot recover from failing for the rest of the
session is a design defect, not a bug report.**

### How the audit was done

Not by grep. Two AST scans over `alpha/**/*.py`, kept as
`tools/audit_unrecoverable_latches.py` so this list is reproducible:

1. **One-way state** — a `self.*` attribute or module global whose "bad" value is
   set on a live path, read from a *different* function, and cleared only in
   `__init__` / `reset*` / `start`, **or never cleared at all**. The first draft
   required a clear site to exist before reporting, which skipped the
   never-cleared case entirely — the worst one. Corrected before anything was
   reported.
2. **Thread targets** whose **outermost** loop body cannot swallow an exception.
   The first draft examined every loop in the function, including inner ones, so
   a correctly guarded outer loop containing a bare inner loop came back as a
   risk. Corrected the same way.

Findings marked **PROVEN** were reproduced by executing the real module, not by
reading it. Findings marked **read-confirmed** were not executed, and say why.

---

## 1. Findings

### A. Threads that die on one exception and can never be restarted

#### A1. `crash_guard_log._writer_loop` — PROVEN

`alpha/utils/crash_guard_log.py:102`

The loop has no `try` at all:

```python
def _writer_loop() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_LOG_FILE, "a", encoding="utf-8") as handle:
        while True:
            line = _log_queue.get()
            if line is None:
                break
            handle.write(line + "\n")   # <- one OSError ends the thread
            handle.flush()
```

Reproduced by injecting one `OSError` at the `write`:

```
writer alive after a normal line : True
writer alive after ONE failure   : False
_writer_started still True?      : True  -> _start_writer() is a no-op
silently dropped by queue.Full   : 101 of 2100 -> producer sees no error
```

Three separate failures compound:

* the thread dies,
* `_writer_started` (`:26`) stays `True`, so `_start_writer()` (`:113`) returns
  immediately forever — **restart is impossible**,
* the producer catches `queue.Full` and passes (`crash_guard_log:139`), so
  nothing anywhere reports the loss.

**Blast radius:** the crash guard log itself. `log_exception()` writes here from
`japanese_sentence_assembler.py`, `japanese_final_chunk_stabilizer.py` and
`language_pipeline_worker.py` -- checked, not assumed; the audio path does not
use it. If this dies, the evidence for the *next* failure does not exist.

#### A2. `diagnostic_test_log._writer_loop` — PROVEN

`alpha/utils/diagnostic_test_log.py:202`

Identical shape, identical `_writer_started` latch (`:231`). Reproduced:

```
writer alive after ONE failure  : False
after calling _start_writer()   : False <- no restart
```

Worse in one respect: `_enqueue_line` (`:105`) responds to a full queue by
**discarding the oldest line** to make room, so after the writer dies the module
loses data quietly and continuously rather than stopping.

#### A3. `wasapi._wasapi_device_watch_worker` — read-confirmed

`alpha/audio/wasapi.py:140`

`except Exception` sits **outside** the `while`:

```python
        try:
            ...
            while not self._stop_event.wait(poll_seconds):
                ...
                current = self._read_default_endpoint_id()   # COM call
                ...
        except Exception as exc:
            print(f"[WASAPI] Device watch stopped: {exc}")
```

One COM hiccup prints a line to a console nobody is reading and ends the thread.
It is started once at stream open (`wasapi.py:288`), cleared only at stop
(`:343`), and never health-checked or restarted.

Not executed — reproducing it needs a real COM failure on a live WASAPI stream.
The structure is not in doubt; the runtime frequency is.

**Blast radius: the largest of the seven.** This is item 73's default-audio-device
change detection. Once it is dead the app stops noticing that the default
endpoint moved — which is the item 80 audio-loss failure class, the one that
took word recall from 93.5% to 42.9%.

Note the irony worth carrying into V2: this same function very deliberately
un-latches its *flag* — "a sticky warning that outlives the problem is noise",
in its own comment — while the *thread that sets the flag* has no recovery at
all.

#### A4. `performance_timeline` heartbeat `_loop` — PROVEN

`alpha/utils/performance_timeline.py:40`

`self.progress("heartbeat")` is unguarded. And stopping it is irreversible even
deliberately: `start_heartbeat` (`:36`) returns early when `_heartbeat is not
None`, and `_heartbeat_stop` is never `.clear()`ed. Reproduced:

```
alive after start           : True
alive after stop            : False
alive after restart attempt : False
```

**Blast radius:** small — progress telemetry only. Listed because it is the same
shape and the fix is free once A1–A3 have a mechanism.

#### A5. `async_debug_log._writer_loop` — recovery exists, and is scheduled where it can never fire

`alpha/utils/async_debug_log.py:136`

This one is worth reading carefully, because the mistake is subtler than A1–A4
and the fix is two lines.

The module already has a **correct** repair, `ensure_async_logger_healthy_non_blocking()`
(`:214`). It does exactly the right thing — notices `writer_thread_alive` is
false, resets `_writer_started = False`, nulls the thread handle, calls
`_ensure_writer()`, and logs `ASYNC_LOGGER_RESTARTED` or
`ASYNC_LOGGER_SAFE_MODE_FALLBACK`. It is the piece A1 and A2 are missing.

It is called from exactly one place: `main.py:59`, during startup — **before the
writer has had any opportunity to die.** Nothing calls it again. Not the session
watchdog, not the health monitor, not the run heartbeat.

So a writer that dies at minute 20 stays dead, and the repair that would have
fixed it sits in the same file, working, unreachable.

This is a distinct sub-shape of the same class, and it deserves its own name:

> **Recovery implemented but scheduled where it cannot fire.**
> Item 94 was recovery that was structurally unreachable. This is recovery that
> is reachable, correct, and wired to the one moment it is guaranteed to be
> unnecessary.

It is worse than mis-scheduled, and better to fix, than it first looked.
`_check_queue_health()` (`:248`) runs on **every enqueue** and **already detects
this exact condition** at `:273`:

```python
    if _writer_started and _writer_thread is not None and not _writer_thread.is_alive():
        emergency_sync_write("ASYNC_LOG_WRITER_STALLED")
```

So the app already notices the writer is dead, already says so, and then stops —
sixty lines above the repair that would fix it.

**Fix (step 3, not step 1–2):** call `ensure_async_logger_healthy_non_blocking()`
from that branch. The detection already exists and already runs on a path that is
guaranteed to be hot; it just stops one line short.

**An earlier draft of this plan said "call it from the session watchdog tick,
two lines". That was wrong and is recorded here rather than quietly replaced.**
The watchdog ticks every 2.0 s (`session_watchdog.py:22`), and
`ensure_async_logger_healthy_non_blocking()` opens with an unconditional
`emergency_sync_write(...)`, which is a *synchronous* disk write. That would have
added ~1100 needless synchronous writes to a 37-minute session and defeated the
point of an async logger — a performance regression shipped as a reliability
fix.

#### A6. `stop_finalize_worker._watchdog_loop` — read-confirmed

`alpha/utils/stop_finalize_worker.py:662`

`_host_snapshot(host)` and `freeze_guard_log(...)` are unguarded inside the
loop. If either raises, the stop-freeze watchdog dies — during stop, which is
exactly when a freeze needs reporting.

Not executed: reproducing it means driving a real stop-finalize to the point
where this loop is running, which needs a live session rather than a harness.
The structure is not in doubt.

### B. Flags with no reachable clear

#### B1. `async_debug_log._degraded_mode` — read-confirmed

`alpha/utils/async_debug_log.py:31`

* `set_degraded_logging_mode(True)` is called from **three** sites
  (`async_debug_log.py:266`, `:269`, `session_watchdog.py:212`).
* `set_degraded_logging_mode(False)` is called from **zero** sites.

One queue spike degrades verbose logging permanently, and it stays degraded long
after the queue has drained.

Not executed: the call-site count is the whole finding and `grep -c` answers it
exactly. There is no runtime behaviour left to check once the clear site is
known not to exist.

#### B2. `TranslationWorker._quota_disabled` and `_accepting` — PROVEN

`alpha/translation/translation_worker.py:874-875`

```
clears _quota_disabled: only __init__ / reset_session
sets _accepting=True  : only __init__ / start
re-arm method?        : []
```

For genuinely exhausted quota this is correct behaviour and is documented as
deliberate. The gap is that there is no operator path back at all: a transient
`quota_exceeded` from DeepL, or a mid-meeting top-up, both require restarting
the session. Treated as MEDIUM, not a defect in the decision — only in the
absence of a way out.

### C. Positive controls

These are what "correct" looks like in this repo, and they are also the evidence
that the scan discriminates rather than flagging everything:

| Component | Why it is fine |
|---|---|
| `LanguagePipelineWorker` | `start()` clears `_stop`. Verified by execution: dies on `stop_and_join`, comes back on `start()`. |
| `async_debug_log`'s repair | `ensure_async_logger_healthy_non_blocking()` is correct in isolation — it is only mis-scheduled. See A5. |
| `_dg_auth_failed` | Already fixed at `alpha/ui/main_window.py:10098`, with a comment explaining exactly this hazard. **The in-repo precedent — copy this shape.** |
| item 94's commit gate | Now a bounded consecutive-reject breaker with a reachable clear. |
| `main_window.translation_enabled` | Re-evaluated at every session start (`:8841`, `:8903`). Not a latch. |

---

## 2. Mitigation plan

Seven hand-rolled `try/except` blocks would leave the eighth instance to be
found in six months. The plan is one mechanism plus a guard that makes the class
non-recurring.

### Step 1 — `alpha/utils/supervised_thread.py` *(other account)*

A single supervisor for long-lived worker loops.

**Required behaviour**

* Runs the target inside a restart loop. An exception escaping the target is
  logged (`crash_guard_log` / `jp_accuracy_log`) and the target is restarted.
* **Bounded.** At most `N` restarts inside a rolling window (suggested: 5 in 60
  seconds). Beyond that, stop restarting and record the component as
  `degraded` — spinning on a permanent failure is its own bug.
* Exponential backoff between restarts, capped (suggested: 0.5 s → 5 s).
* Exposes, for the health snapshot: `alive`, `restart_count`,
  `last_error`, `last_restart_ts`, `gave_up`.
* A clean shutdown (the stop event) must **not** count as a failure and must not
  trigger a restart.
* Restarting must actually work: no `_started` boolean that makes the second
  `start()` a silent no-op. Gate on `thread is not None and thread.is_alive()`,
  which is the mistake A1, A2 and A4 all share.

**Acceptance criteria** — a test must show, against the real class:

1. an exception in the target does not end supervision; the target runs again;
2. `restart_count` increments and `last_error` carries the exception;
3. after the cap is exceeded, it stops restarting and reports `gave_up=True`
   rather than looping;
4. a normal stop leaves `gave_up=False` and does not restart;
5. `stop()` then `start()` genuinely restarts (the A4 defect).

### Step 2 — convert the five loops *(other account)*

A1, A2, A3, A4 and A6. A5 is step 3 (its repair already exists).

| # | File | Gotcha specific to this one |
|---|---|---|
| A1 | `crash_guard_log.py:102` | The file handle is opened **outside** the `while`. On restart it must be reopened, and the failing line must not be retried forever — drop it and count it. |
| A2 | `diagnostic_test_log.py:202` | Same. Also `_enqueue_line`'s full-queue path silently drops the oldest line; once the writer is supervised, that drop should be counted and surfaced. |
| A3 | `wasapi.py:140` | Supervise the whole target, not the loop body: COM apartment state is per-thread, and `com_initialize_mta()` / `com_uninitialize()` already sit inside this function, so a whole-function restart re-runs them correctly. Do not hoist them out. Do not lose the two-consecutive-reads debounce or the `_wasapi_device_change_reported` un-latch — that un-latch is the one piece of recovery this function already gets right. |
| A4 | `performance_timeline.py:40` | Also fix the restart guard: `start_heartbeat` returning early on `_heartbeat is not None`, and `_heartbeat_stop` never being cleared. |
| A6 | `stop_finalize_worker.py:662` | Runs only during stop. Keep the `worker_done` break exact — a supervised restart must not resurrect the watchdog after finalize completed. |

A5 is deliberately **not** in this table. Its repair already exists and only needs
scheduling; that is step 3, and building a supervisor around it instead would
duplicate working code.

Additionally, delete the `_writer_started` latches in A1/A2 in favour of a
liveness check, or the supervisor cannot restart anything.

**Acceptance criteria:** for each of the five, a test injecting one exception
must show the loop still running afterwards, and `restart_count` must be non-zero
on the supervisor. Reaching `LAST_HEALTH_SNAPSHOT.json` is required for A1–A4
only: A6 runs during stop-finalize, after the snapshot the run reports is
written, so demand it on the supervisor object there instead of in the artifact.

### Step 3 — reachable clears, and one repair that only needs scheduling — DONE

Shipped as described below, with tests that were confirmed to fail against the
pre-fix tree first (21 of 21, including the five host-level ones).

**A5** — `_check_queue_health()`'s dead-writer branch now calls
`ensure_async_logger_healthy_non_blocking()` through a new
`_repair_writer_if_dead()`. Throttled at `_WRITER_REPAIR_INTERVAL_S = 5.0`
because that branch runs on every enqueue and the repair opens with a
synchronous disk write; bounded at `_WRITER_REPAIR_MAX_ATTEMPTS = 5`, after
which it emits `ASYNC_LOG_WRITER_UNRECOVERABLE` once and stops rather than
spinning; and the budget re-arms when the writer comes back, so the second
outage of a long session gets the same allowance as the first.

**B1** — `_degraded_mode` clears in the same function that sets it, once the
queue has stayed under `_QUEUE_WARN` for `_DEGRADED_RECOVERY_S = 30.0`. A fresh
spike restarts the window. `DEGRADED_LOGGING_MODE_CLEARED` is emitted, because
entering degraded mode was already announced and a silent exit leaves the log
unreadable as to which lines were dropped.

**B2** — `TranslationWorker.resume_after_quota()` plus
`AlphaApp.resume_translation_after_quota()`. Gated on `_quota_disabled`
specifically: `_accepting` is also cleared by `stop_accepting()` and
`shutdown()`, and re-arming it there would restart a worker the session
deliberately stopped. Manual, never automatic.

**One thing deliberately NOT done, so it is a decision and not an omission.**
The plan said "plus a UI affordance". The quota status is rendered as
placeholder text inside the translated verse box, not a widget with controls, so
a button means editing either the Tk text placeholder path or the responsive
hamburger menu — the two areas items 71, 92 and 93 have churned most. What
shipped instead is the wiring plus a status line that names the way out
("Restart listening, or resume once the quota is topped up"). **The button is
open, and it is the user's call whether that risk is worth taking.**

#### Original plan, kept for the reasoning

* **B1 `_degraded_mode`.** The clear belongs in the same function that sets it,
  `_check_queue_health()` (`async_debug_log.py:248`) — the two sites that turn
  degraded mode ON are its `qsize > _QUEUE_CRITICAL` / `> _QUEUE_DEGRADED`
  branches (`:265`, `:268`). Add the `else`: once `qsize` has stayed under
  `_QUEUE_WARN` (1000) for a sustained window, `set_degraded_logging_mode(False)`.
  Same degrade-then-recover shape as item 45's translation circuit breaker, which
  does re-close correctly. Log `DEGRADED_LOGGING_MODE_CLEARED` so the recovery is
  visible, exactly as entering degraded mode already is.
  Require a sustained window, not a single sample: clearing on one low reading
  next to a queue oscillating around the threshold flaps degraded mode on and off
  and writes a synchronous line each time.
* **A5 `async_debug_log`.** Call the existing
  `ensure_async_logger_healthy_non_blocking()` from the dead-writer branch that
  already exists in `_check_queue_health()` (`async_debug_log.py:273`), which runs
  on every enqueue. Not from the session watchdog: that function opens with an
  unconditional synchronous disk write, and the watchdog ticks every 2.0 s.
  Its `_writer_started = False` reset is also the reference implementation for
  what step 1's supervisor must do, and for exactly what A1 and A2 are missing.
* **B2 `TranslationWorker`.** Add a public `resume_after_quota()` that clears
  `_quota_disabled` and `_accepting`, plus a UI affordance for it.
  **Deliberately not automatic:** exhausted quota is real, and silently retrying
  a paid API in a loop is a worse failure than staying paused. The operator gets
  a way back; the software does not guess.

### Step 4 — stop the class from recurring *(this account)*

The audit scanners become a repo test. A new one-way flag, or a new thread loop
whose outermost body cannot swallow an exception, turns the suite red.

* Wrap `tools/audit_unrecoverable_latches.py` in
  `tests/test_no_unrecoverable_latches.py`.
* Both need an explicit allowlist, with a written reason per entry — a latch that
  is genuinely correct (a one-shot startup guard, for instance) is allowed once
  someone has said why in the file.
* The test asserts the allowlist is exact: an entry that no longer matches
  anything must be removed, so the list cannot rot into a blanket suppression.

This step is the one that actually matters. Steps 1-3 fix these eight; step 4
is what stops the ninth.

### Priority

```
A3      (wasapi device watch)     - tied to the item 80 audio-loss class
A1, A2  (crash + diagnostic log)  - without these, the next bug has no evidence
A5      (async logger scheduling) - two lines, and it is already written
B1, B2  (flag clears)
A4, A6  (heartbeat, stop watchdog)
Step 4  (guard test)              - last, but not optional
```

### Reconciling the tool output with this list

Run the tool and you get more rows than this document has findings. That is
expected — the scan is deliberately over-inclusive — but the difference has to be
written down, or the next person either re-investigates all of it or trusts the
raw list as a verdict.

**Threads: 6 rows, 6 findings (A1–A6).** They agree, but only after A5 was read
properly. The scanner cannot see that A5 already has an external repair; taking
the raw row at face value would have meant either building a supervisor around
working code, or dismissing the row as already handled.

**One-way state: 9 rows, 2 findings (B1–B2).** The other rows, and why each is
not a defect:

| Tool row | Verdict |
|---|---|
| `async_debug_log._degraded_mode` | **B1.** No clear site exists anywhere. |
| `translation_worker._quota_disabled` | **B2.** |
| `translation_worker._accepting` | **B2** — same finding, second row. One defect, two flags. |
| `main_window.translation_enabled` | Not a latch. Re-assigned from config at `:8841` and `:8903` on every session start. The scan only sees literal `True`/`False` assignments, so it misses the non-literal ones that clear it. |
| `timeline_mixer._sys_source_available`, `._mic_source_available` | Not a failure latch — they latch *on*, not off, and `reset()` clears both. Worth a look if audio source selection ever misbehaves, but nothing here degrades after a failure. |
| `japanese_sentence_assembler._stop_boundary_active` | Checked, not assumed. Set only in `flush()`, and both wrappers that reach it (`flush_japanese_assembler_on_stop`, `flush_japanese_final_stabilizer`) close the transcript gate first and are stop-only; the first also calls `assembler.reset()`, which clears it. Cannot latch mid-session. |
| `tk_thread_guard._session_listening_active`, `._stop_finalize_active` | Module-level initialisers, flipped by their own setters as the session moves between phases. Normal state, not failure state. |

---

## 3. Verification protocol for step 1–2

Before step 3 begins, step 1–2 will be checked against these, and the result
reported plainly either way:

1. Every acceptance criterion in steps 1 and 2 has a test that **fails against
   the pre-fix code**. A test that passes both before and after proves nothing —
   that is precisely how item 60's fix shipped with the latch intact
   (`test_a_new_utterance_clears_the_gate` hand-assigned a state production
   cannot produce).
2. The five conversions are driven by injecting a real exception into the real
   loop, not by asserting on the supervisor in isolation.
3. Full suite: **compare the set of FAILING TEST NAMES. Nothing else.**

   Not the pass count, not the skip count, and not the collected total. All three
   move for reasons that have nothing to do with whether the code is correct:

   * roughly a dozen `test_item71_*` cases skip or run depending on whether Tk
     has a display, so the same tree legitimately reported `1132 passed / 20
     skipped` and `1144 passed / 8 skipped` on different runs here;
   * the collected total moves whenever anyone adds a test — including you, in
     step 2;
   * an earlier draft of this document quoted a total anyway, and got it wrong
     twice. That is the evidence for this rule, not a hypothetical.

   The 8 pre-existing failures, by name — this list is the baseline:

   ```
   test_final_transcript_commit_v3_2_5  ::test_commit_allowed_while_finalizing
   test_final_transcript_commit_v3_2_5  ::test_commit_allowed_while_listening
   test_keepalive_ping_thread_cannot_crash::test_the_crash_is_reproducible_on_the_unguarded_base_class
   test_package_glossary_flags_85253    ::test_glossary_helper_absent
   test_package_glossary_flags_85253    ::test_glossary_helper_present
   test_package_glossary_flags_85253    ::test_main_glossary_absent_no_unbound_local
   test_package_glossary_flags_85253    ::test_main_glossary_present_after_successful_inclusion
   test_stop_finalize_v3_2_3            ::test_phase_constants_match_spec
   ```

   `test_item48_audio_manifest_bounded` sometimes joins them: a full-suite
   ordering flake that passes standalone 3/3 and shares no code with any of this.

5. **Run the suite twice, and concurrently with something else.** A test that
   passes alone and fails under load is not a regression test. This is not
   theoretical either: `test_the_updater_does_not_report_its_own_interpreter`,
   written for the updater in this same session, called the real
   `running_instances()` and asserted its own interpreter was absent from the
   result. True only while no other process happens to be running that
   interpreter — so it passed alone and failed the moment a second pytest run
   shared it, and it reported as a build failure. It now feeds the check a fixed
   synthetic process table instead. Do the same with anything you write that
   reads live machine state.
4. `restart_count` and `gave_up` appear in `LAST_HEALTH_SNAPSHOT.json` on a real
   run — a supervisor whose state nothing reports is the item 94 stall detector
   again, which fired correctly and could not tell anyone.

---

## 4. The V2 lesson

Six of the eight findings are in **logging or watchdog code** — the components
whose entire job is to tell you something went wrong.

That was the same failure as item 94 itself, where the stall detector fired,
classified the stall as `confirmed`, and then could not report it: a
one-character type bug (`_component_history` annotated `list`, initialised `{}`)
raised on every call before the log line, so `COMPONENT_STALL_CLASSIFICATION`
appears **zero** times in a run holding a 16.5-minute confirmed stall. That one
is already fixed, in `b04ec9f` — it is quoted here as the pattern, not as
outstanding work.

The rule this produces for the V2 architecture:

> **The observability path must be at least as fault-tolerant as the product
> path, never less.** A logger that can die is worse than no logger, because the
> silence reads as health.

Corollary, and the specific rule that would have caught item 94, A1, A2 and A4
before any of them shipped:

> **A recovery path that has never executed in production is not implemented —
> it is untested code that happens to compile.** If a clear/restart/reset path
> cannot be shown running, it does not count as recovery.
