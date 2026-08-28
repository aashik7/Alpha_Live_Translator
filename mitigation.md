# Unrecoverable-failure audit and mitigation plan

**Status:** findings confirmed, fixes not yet implemented.
**Raised:** 2026-08-27, after item 94.
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

Not by grep. Three AST scanners over `alpha/**/*.py`, kept as `tools/audit_unrecoverable_latches.py`
so this list is reproducible:

1. **One-way boolean state** — `self.*` attributes and module globals whose
   "bad" value is set on a live path, read from a *different* function, and
   cleared only in `__init__` / `reset*` / `start`.
2. **Never-cleared state** — the same, but where no clear site exists at all.
   The first version of the scanner *skipped* these, which is the worst case; it
   was corrected.
3. **Thread targets** whose **outermost** loop body cannot swallow an exception.
   The first version counted inner loops too and produced false positives; it
   was corrected before anything was reported.

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
the assembler, the language pipeline worker and the audio path. If this dies,
the evidence for the *next* failure does not exist.

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

**Fix (step 3, not step 1–2):** call the existing function from the session
watchdog tick. No new mechanism — the mechanism is already written and tested by
its own startup path.

#### A6. `stop_finalize_worker._watchdog_loop` — read-confirmed

`alpha/utils/stop_finalize_worker.py:662`

`_host_snapshot(host)` and `freeze_guard_log(...)` are unguarded inside the
loop. If either raises, the stop-freeze watchdog dies — during stop, which is
exactly when a freeze needs reporting.

### B. Flags with no reachable clear

#### B1. `async_debug_log._degraded_mode` — read-confirmed

`alpha/utils/async_debug_log.py:31`

* `set_degraded_logging_mode(True)` is called from **three** sites
  (`async_debug_log.py:266`, `:269`, `session_watchdog.py:212`).
* `set_degraded_logging_mode(False)` is called from **zero** sites.

One queue spike degrades verbose logging permanently, and it stays degraded long
after the queue has drained.

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
| A3 | `wasapi.py:140` | Only the loop body needs supervising. `com_initialize_mta()` / `com_uninitialize()` are per-thread and must be re-run per restart, not hoisted. Do not lose the two-consecutive-reads debounce or the `_wasapi_device_change_reported` un-latch. |
| A4 | `performance_timeline.py:40` | Also fix the restart guard: `start_heartbeat` returning early on `_heartbeat is not None`, and `_heartbeat_stop` never being cleared. |
| A6 | `stop_finalize_worker.py:662` | Runs only during stop. Keep the `worker_done` break exact — a supervised restart must not resurrect the watchdog after finalize completed. |

A5 is deliberately **not** in this table. Its repair already exists and only needs
scheduling; that is step 3, and building a supervisor around it instead would
duplicate working code.

Additionally, delete the `_writer_started` latches in A1/A2 in favour of a
liveness check, or the supervisor cannot restart anything.

**Acceptance criteria:** for each of the five, a test injecting one exception
must show the loop still running afterwards, and the health snapshot must report
a non-zero `restart_count`.

### Step 3 — reachable clears, and one repair that only needs scheduling *(this account)*

* **B1 `_degraded_mode`.** Add the clear. Auto-recover when the async log queue
  has been healthy for a sustained window — the same degrade-then-recover shape
  as item 45's translation circuit breaker, which does re-close correctly. Log
  `DEGRADED_LOGGING_MODE_CLEARED` so the recovery is visible, exactly as the
  entry into degraded mode already is.
* **A5 `async_debug_log`.** Call the existing
  `ensure_async_logger_healthy_non_blocking()` from the session watchdog tick, not
  only from `main.py:59`. Two lines, no new mechanism. Its
  `_writer_started = False` reset is also the reference implementation for what
  step 1's supervisor must do, and for what A1 and A2 are missing.
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

`tools/audit_unrecoverable_latches.py` reports **6** unsupervised thread loops and
this document lists 6 findings (A1–A6) — they agree, but only after A5 was looked
at properly. The scanner cannot see that A5 has an external repair; a reader who
trusts the raw list without reading the code would either miss that A5 needs a
different fix from the others, or dismiss it as already handled. The tool's own
closing line says as much: neither list is a verdict.

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
3. Full suite compared against the baseline: **8 failed / 1132 passed / 20
   skipped**, on commit `cc4763b`. The 8 are pre-existing
   (`test_final_transcript_commit_v3_2_5` ×2, `test_keepalive_ping_thread_cannot_crash`,
   `test_package_glossary_flags_85253` ×4, `test_stop_finalize_v3_2_3`).
   `test_item48_audio_manifest_bounded` is a full-suite ordering flake that
   passes standalone.
4. `restart_count` and `gave_up` appear in `LAST_HEALTH_SNAPSHOT.json` on a real
   run — a supervisor whose state nothing reports is the item 94 stall detector
   again, which fired correctly and could not tell anyone.

---

## 4. The V2 lesson

Six of the eight findings are in **logging or watchdog code** — the components
whose entire job is to tell you something went wrong.

That is the same failure as item 94 itself, where the stall detector fired,
classified the stall as `confirmed`, and then could not report it: a
one-character type bug (`_component_history` annotated `list`, initialised `{}`)
raised on every call before the log line, so `COMPONENT_STALL_CLASSIFICATION`
appears **zero** times in a run holding a 16.5-minute confirmed stall.

The rule this produces for the V2 architecture:

> **The observability path must be at least as fault-tolerant as the product
> path, never less.** A logger that can die is worse than no logger, because the
> silence reads as health.

Corollary, and the specific rule that would have caught item 94, A1, A2 and A4
before any of them shipped:

> **A recovery path that has never executed in production is not implemented —
> it is untested code that happens to compile.** If a clear/restart/reset path
> cannot be shown running, it does not count as recovery.
