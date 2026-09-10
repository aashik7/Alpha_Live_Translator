# Alpha Live Translator — fix sequence

Companion to `CODE_REVIEW_20260904.md`. That file says *what* is broken; this
one says *in what order to fix it, and why that order*.

Reviewed at `29d61d5`. Item numbers below are the review's item numbers.

## The ordering rule

Three things decide the order, in this priority:

1. **What is losing the client's data right now**, if the fix is small and
   provable on its own.
2. **What unblocks later work** — a fix that turns a one-off into a class fix,
   or that a planned feature depends on, is worth more than its own severity.
3. **What we do not yet know.** Four subsystems were never audited. Spending a
   week on MEDIUM items while an unaudited area may hold another CRITICAL is
   the wrong bet, so the audit gap closes *before* the medium batch, not after.

Risky fixes ship **alone**, so a regression is attributable to one change.

## Rules that apply to every step

These are not optional; they are what has kept this repo's fixes from
regressing, and skipping them is how the last four wrong diagnoses happened.

- **Write the regression test first and prove it FAILS against the pre-fix
  code.** A test written after the fix proves only that the code does what it
  does.
- **Drive the real code.** Constructing a failing state by hand proves a
  hazard, not a bug — establish reachability through the real entry point,
  separately from the mechanism. Item 6 in the review is what happens when you
  do not.
- **Baseline is the SET of eight failing test NAMES, never a count.** After
  every fix:
  ```
  cd Alpha_Live_Translator
  py -m unittest discover -s tests -t tests -p "test_*.py"
  ```
  Anything new in that set is a regression; fix it before committing.
- **`git fetch origin` and check divergence before every push** — this repo is
  worked on from more than one account.
- **Commit and push each fix as soon as it is verified.** Do not batch several
  verified fixes into one uncommitted pile.
- **One update package per phase, not per fix.** Bump `APP_VERSION` in
  `alpha/constants.py`, then `py tools/build_update_package.py --zip`. Never
  rebuild at a version already shipped — the client cannot tell the packages
  apart, and neither can the run-log filenames.

---

## Phase 1 — Stop the silent truncation (item 1) — ✅ SHIPPED `49a5178`

**Ship this first, on its own.** It is the only CRITICAL, it needs no other
change, and it is losing delivered transcript content today.

`alpha/translation/translation_worker.py`, inside the `except queue.Full` block
at :520:

1. Record the discarded sequence — `self._dropped_sequences.add(seq)` — and in
   the ordering loop at :1022 advance past it as well as past `_held`. Do **not**
   roll back `_next_translation_sequence` instead: it is allocated under
   `_lock` while `put_nowait` runs outside it, so a concurrent submit could
   re-issue the number. This one change turns a session-ending stall into the
   loss of a single line.
2. Add a `TRANSLATION_QUEUE_FULL_DROPS` counter (this also lets
   `stop_finalize_worker.py:576` classify the rejection instead of reporting
   `"unknown"`), set `_status_message`, and include a recent drop in the
   `degraded` property at :216 so the existing indicator at
   `main_window.py:3954` paints it with no new UI code.

**Test.** After a forced `queue.Full`, the next successful translation must
still reach `on_translation_ready`. Drain the gate to a healthy state *before*
forcing the drop, or the test proves nothing — an undrained gate stalls for an
unrelated reason. `grep -i "queue full" tests/` currently returns nothing.

**Done when** the new test fails against pre-fix code, passes after, and the
suite's failing-name set is unchanged.

---

## Phase 2 — Stop system audio dying for the session (item 3, plus the scan) — ✅ SHIPPED `fd93e61`

Two changes, one commit. The second is what makes this a class fix instead of a
one-off.

1. `alpha/audio/wasapi.py:255` — replace the `break` in the `except Exception`
   handler with a bounded backoff that stays in the loop (`time.sleep(0.05);
   continue` after the existing print and `note_capture_error()`), optionally
   giving up only after N consecutive failures. **Keep the `break` at :226** —
   that one is the clean-shutdown exit `_close_wasapi_stream` relies on, and
   turning it into a `continue` would spin.
   If a belt-and-braces restart is wanted, additionally spawn the reader
   through `SupervisedThread` (already imported in the same file at :308) —
   giving the supervisor its **own** stop event, never `self._stop_event`,
   because `SupervisedThread.start()` clears the event it is given and clearing
   the shared audio stop event would un-stop the reader and the mixer.

2. `tools/audit_unrecoverable_latches.py:212-220` — `_body_swallows` must
   return True only when the handler body also cannot leave the loop, i.e. no
   `ast.Break` / `ast.Return` reachable in any `except` handler of that `try`,
   excluding nested inner loops and functions. Today `break`, `continue`,
   `return` and `pass` handlers are measurably indistinguishable to it.

**Re-run the scan after change 2.** That is what proves the fix, and it will
probably surface sibling threads with the same shape — triage those before
moving on.

**Why here.** Second-worst user-visible loss: the far end of the meeting goes
silent for the rest of the session while the indicator still reads Signal OK.
It is also **Stage 0 of `SPEAKER_HOTSWAP_GUIDELINE.md`** — the speaker
hot-swap feature cannot be built on an unsupervised reader, so this phase pays
for itself twice.

**Ship phases 1 and 2 together** as one update package if phase 2 lands within
a day of phase 1; otherwise ship phase 1 alone and phase 2 second.

---

## Phase 3 — Close the audit gap (no code changes) — ✅ COMPLETE, 5 of 5

Audit the five areas the review never reached, listed in its
*What this review did NOT cover* section:

- commit-authority internals (`utterance_lifecycle`, `pipeline_commit_transaction`)
- Japanese assembler and its stabilizers
- UI threading — worker threads reaching Tk widgets, `after()` job lifetime
- stop / finalize internals
- global concurrency sweep

**Why before the medium batch, not after.** None of items 2, 4, 5, 8, 9 is
bleeding. If stop/finalize or the commit authority holds another CRITICAL —
and stop/finalize is exactly where "the last utterances never reach the
delivered file" would live — you want to know that before spending the week on
log rotation. Planning the medium batch once, with the full list in hand, is
cheaper than planning it twice.

**Practical note.** The fan-out for this died twice on the session token limit.
Run it as **one subsystem per session**, not five in parallel.

**Done so far — stop / finalize** (`3d9ff34`, review items 10-12). It produced
two HIGH findings that are cheap to fix and share one failure, so they are
promoted to their own phase below rather than waiting for the other four
audits. Its third finding (item 12) folds into the log-rotation work, because
the same change fixes both.

**Done — commit-authority internals** (review item 13, plus three invariants
confirmed holding and a correction to item 1's stated mechanism). The single
commit authority, the frozen-ledger export and the revise-target guard all
hold; the one finding is a dead stale-session guard, which folds into phase 3b
because it is the same failure window.

**Done — global concurrency sweep** (review item 14). One real defect out of
four candidates: `rebind_all_runtime_writers` takes a non-reentrant lock and
then calls a function that takes it again, reproduced through the real entry
point. It also explains item 12 — the rebind that would move writers out of
`_pending` is the deadlocking one, so it was disabled by a safe-mode flag and
the pile has been refilling ever since. Lock-ordering inversions are
structurally absent: no function in `alpha/` nests one lock inside another.

**Done — UI threading** (review items 15, 16). The headline is a negative and
it is the useful part: an AST sweep from all 20 real background-thread roots
found **no** widget mutation reachable without a marshal hop. The two findings
are an evidence event that asserts safety it never checked (item 15) and four
`after` jobs that are never cancelled (item 16, LOW).

**Done — Japanese assembler + stabilizers** (review item 17). Three hunts came
back empty and are recorded as such: no unbounded buffer (bounded three ways at
100 chars / MAX_PARTS / 8000 ms), no boundary proposal that cannot be accepted,
and all seven item-94-shaped guards benign. The one finding is a silent
discard: a raise inside the continuity-hold tick throws the buffered Japanese
sentence away and returns success, while the sibling handler in the same class
recovers and re-commits it. It joins phase 6, since the trigger is abnormal.

**All five areas are now audited.** Each sweep states its own scope; none of
them is a proof of absence.

---

## Phase 3b — The Stop/Start window (items 10, 11, 13) + the naming fix (15) — ✅ SHIPPED

These two are one failure wearing two hats, and both fixes are a few lines.
They jump ahead of the old phase 4 because they are HIGH rather than MEDIUM,
they risk the delivered transcript rather than a log, and neither depends on
the four audits still outstanding — so nothing is gained by waiting.

**Item 11 first**, because it is the one that turns item 10 from untidy into
harmful, and because it is three lines. In `rebind_all_runtime_writers`
(`troubleshooting_paths.py:700-712`), decide what the name should mean. If a
second rebind is genuinely wrong, `return` from the guard. If it is legitimate,
rename the event to describe what happened (`RUN_FOLDER_REBOUND_AGAIN`) and put
the previous folder in the payload. What must not survive is an event called
`SECOND_RUN_FOLDER_CREATION_BLOCKED` sitting above code that blocks nothing —
that retires the question for anyone reading a client's logs.

**Item 10 second.** Keep the 5 s UI restore; it exists for a good reason and its
message is honest. Stop it clearing the guards that keep a second session out.
Gate `toggle_listening` (`main_window.py:10116`) on the worker rather than on
the UI flag — `stop_core_completed_event` is already set at
`stop_finalize_worker.py:2060`, after the last artifact write, and is exactly
the right signal. The button can read "Finishing previous session…" until then.

**Item 13 third**, because it is the same window seen from the other side: a
stale event arriving while a second session has begun. The lifecycle's guard
against that cannot fire — `session_id` is compared against itself. Compare
against the host instead, which is the only value that can differ:

```python
host_sid = str(getattr(self._host, "_live_session_id", "") or "")
if host_sid and self._session_id and host_sid != self._session_id:
    ... reject
session_id = self._session_id or host_sid
```

The ledger already refuses the write (`canonical_identity_registry.py:116`),
so this is about the lifecycle's own state being mutated by a stale final
before anything downstream says no.

**Item 15 rides along**, because it is the same defect in a different place: a
name asserting something nothing checked. `scan_tk_call_sites` writes
`TK_CALL_SITE_SAFE` after counting the substring `.after(` in two files.
Rename the events to `TK_AFTER_CALL_SITE_COUNT` and drop the word safe, or
replace the body with the sweep this audit ran. Either is small; leaving a
log line that retires a question nobody asked is not.

**Test.** Drive a Start during a finalize that is deliberately slowed, and
assert the second session cannot begin until the worker is done. That is also
what would raise item 10 from PLAUSIBLE to CONFIRMED, and it should be written
either way: the fix is correct regardless, but the file should stop carrying an
unproven claim once it is cheap to prove.

---

## Phase 4 — The provable batch (items 2, 8, 9, 14, 4 + 12) — ✅ SHIPPED

One update package. Ordered by severity within the phase.

1. **Item 2 — the language warning.** Un-stub `_evaluate_language_reliability`
   (`main_window.py:7261-7269`) using the code already beside it: pass through
   the caller's real `metadata["allowed_languages"]` instead of the hardcoded
   `["en"]`, set `script_warning = self._language_script_warning(...)`, return
   `decision="warn"` when it fires. At the call site (8595-8607) branch once:
   on `"warn"`, bump the two stats and call the existing
   `_log_language_commit_warning`, **then commit anyway** — preserving today's
   behaviour that the manual dropdown is authoritative and nothing is dropped.
   Also replace the hardcoded `"blocked_count": 0` at :7314 with the stat.
   Leave the four `LANGUAGE_*` constants False; they are not the cause.
   Verify through a `*Host` fixture on the real commit path, not the pure
   function alone — the severity claim is about caller behaviour.

2. **Items 8 + 9 — credential placeholders.** One change, one test. Add
   `get_deepl_key_status()` mirroring the Deepgram one; add the spellings the
   project actually ships (`your_deepl_auth_key_here`, and the hyphenated
   `your-deepgram-api-key` / `your-deepl-auth-key` from
   `keys.local.ini.example`) to `PLACEHOLDER_API_KEYS`; emit a
   `deepl_key_placeholder` problem from `preflight_credentials()` with
   `blocks_start=False`. Separately, make `build_installer.read_keys()` reject a
   placeholder, not only an empty value.

3. **Item 14 first, then items 4 and 12 — the rebind deadlock, then log
   rotation and what the unbounded pile costs at Start.** Item 14 comes
   first because it is the reason the pile exists: snapshot the registry
   keys under `_lock`, release it, then call `rebind_runtime_writer` for
   each — it takes the lock itself. Do NOT switch `_lock` to an `RLock`;
   this module has 35 `with _lock` sites and that would legitimise re-entry
   at all of them. Only once the rebind works can the safe-mode deferral be
   revisited, and only then does the pile stop refilling. Lift `_rotate_if_needed` out of `_JsonlWriter`
   (`evidence_jsonl.py:39-59`) into a module-level function and call it from
   `async_debug_log.py:213` and `:394` and `freeze_guard_log.py:69`. The two
   writers holding a long-lived handle (`japanese_accuracy_log`,
   `diagnostic_test_log`) need a running byte counter that closes, rotates and
   reopens — a rotate-on-path-change hook fires at most once per session and
   would not fix it. Separately, bound
   `troubleshooting/runs/_pending/logs/*` once at startup: that file is the
   only one unbounded *across* sessions.
   That last part is item 12: `rebind_all_runtime_writers` migrates the whole
   pile on every rebind — measured ~457 MB on the dev machine, and a bare
   harness calling it twice did not finish in 120 s. Bounding the files fixes
   most of it; also skip or chunk the migration above a size threshold, since
   copying a 358 MB log into a run folder is an accident rather than evidence
   collection.

---

## Phase 5 — The risky one, alone (item 5) — ✅ SHIPPED

Device re-bind. Shipped by itself, as planned, so any regression is
attributable. Package `3.3.5.5.8.5.26.5.9`, commit `c2bdab2`.

> ⚠️ **The original instruction in this section was wrong and was not
> followed.** It said to "set `_stop_event`, `_close_wasapi_stream()`, clear
> the event, `_start_wasapi_loopback()`". `self._stop_event` is the
> **session-wide** stop event — created once in `main_window`, cleared at
> Start, set at Stop, and read in **28 places** including the microphone, the
> audio mixer worker and the Deepgram sender and reconnect loops. Setting it to
> rebind one audio device would have stopped the whole session, and clearing it
> would have brought none of those threads back. Same shared-event hazard phase
> 2 recorded for `SupervisedThread.start()`. Kept here rather than deleted so a
> future session does not re-derive it as a good idea.

What actually shipped:

- The capture owns its **own** stop event (`_wasapi_stop_event`, created on
  first use by `_wasapi_capture_stop_event()`). `_wasapi_stop_requested()` is
  true when **either** that or the session event is set; the reader and the
  watcher both exit on it. `_close_wasapi_stream` sets it,
  `_start_wasapi_loopback` clears it before spawning.
- `_rebind_wasapi_to_default_device()` — close, then start, so
  `PyAudio.terminate()` plus a fresh `PyAudio()` picks up the new default
  (`Pa_Initialize()` snapshots the device list; a re-query on the live handle
  provably cannot see the change: measured 0.048 ms, identical index).
- `_report_default_device_changed` **schedules** it through `_run_on_ui_thread`
  and never runs it inline, because `_close_wasapi_stream` skips its watcher
  join when called FROM the watcher (`watch is not
  threading.current_thread()`) and then nulls the handle.
- Single-flight plus a cooldown (`WASAPI_REBIND_COOLDOWN_S = 10.0`) so a
  flapping device — a headset reconnecting, a conferencing app grabbing and
  releasing the endpoint — cannot start a rebind storm, since each rebind tears
  capture down.
- On success `_audio_device_changed` and `_wasapi_device_change_reported` are
  cleared and the indicator repainted; on failure the warning stays up rather
  than reporting success.

**The second-watcher hazard this section predicted was real, and needed one
more fix than expected.** Stopping the watcher was not enough: it *waited* on
the session event, which a rebind leaves clear, so it could not wake inside
`_close_wasapi_stream`'s 1.0 s join — the join timed out, the handle was
dropped, and the restart spawned a second watcher beside the sleeping first.
The watcher now waits on the **capture** event, which every stop path sets, and
still checks the session event each pass. Pinned at the production timings
(2.0 s poll / 1.0 s join) by
`tests/test_device_change_rebinds_capture.py::TheWatcherWakesInsideTheJoinWindowTest`,
proven to fail pre-fix.

Two existing tests were measuring the wrong thing once the report began
scheduling a rebind. Both were **fixed, not weakened**: item 73's modal test
counted the marshal queue (`len(marshalled) == 1`) instead of naming the paint
it wanted, and the A3 supervised-loop host borrows one method off the mixin and
now borrows `_wasapi_capture_stop_event` / `_wasapi_stop_requested` with it —
host fidelity, since the real object is a mixin subclass and always has them.
**A counted-queue assertion is the recurring trap in this area; assert by
name.**

Verified: full suite 1313 tests, failing-name set identical to the eight-name
baseline; `verify_mitigation_claims.py` 27/27; latch audit unchanged at its 4
allowlisted loops; the update package driven end-to-end against a synthetic
26.5.8 install (3 files updated, every file SHA-256 verified, existing
`troubleshooting/` evidence kept).

Not covered by any test, and untestable without hardware: that a real
default-device switch changes the endpoint ID (certain by API contract) and
that the reopened stream binds to the new endpoint. That inference is the one
assumption item 5 still rests on.

---

## Phase 6 — Hygiene and attribution — SHIPPED `42f0d29`, package 26.5.10

Cheap, low risk, batch with whatever else is shipping.

1. **Item 17** — mirror the sibling handler: before
   `try_execute_continuity_hold` clears the buffer, name the loss
   (`CONTINUITY_HOLD_TICK_DISCARDED_BUFFER` + a counter) and hand the text
   to the same recovery `_handle_assembler_exception` uses. If dropping is
   really the policy, it still has to be named — an unnamed loss is what
   makes a live report undiagnosable.
2. **Item 16** — give `_on_close` the cancellation `_stop_ui_loops` already
   has: four recurring `after` jobs are never cancelled anywhere, and the
   close path cancels nothing at all. Small, and worth doing mainly so the
   next person has somewhere to put a cancellation that does matter.
3. **Item 6** — delete the synchronous fallback at
   `audio_temp_capture.py:271-275`. By the time control reaches it the item is
   already counted and logged, so returning is consistent with the function's
   own contract. That removes the only edge from inside the `:377` lock back
   into the lock. Do **not** reach for `threading.RLock` — it would legitimise
   re-entry at all 16 `with _lock` sites and mask this class of bug.
4. **Item 7** — in the `except Exception as exc` at `deepgram_client.py:1741`,
   emit a structured `ENGLISH_LIFECYCLE_INGEST_FAILED` event alongside the
   print, mirroring the two Japanese siblings at :1601-1611 and :1652-1662.
   That makes the downstream `IDENTITY_REJECTION` attributable. Do **not** mint
   a synthetic `canonical_utterance_id` to let the fall-through commit — that
   would defeat the fail-closed identity gate, which measurement showed working.
5. **From the follow-tail review** — `_render_transcript_from_store_now`
   (`main_window.py:6596`) wipes the widget via `_insert_formatted_text`, so a
   reader who has scrolled up now lands at the *top* of the pane. Preserve the
   reader's index across the re-render. Also add a behavioural test through a
   real *translation* writer; today only the transcript writer has one, and the
   translation pane is the one the reader complained about.

---

## Phase 7 — Only after everything above is green

Speaker hot-swap (`SPEAKER_HOTSWAP_GUIDELINE.md`). Its Stage 0 was completed in
phase 2.

### Stage 1 — the per-chunk format stamp — ✅ SHIPPED `c33f5f6`, package 26.5.11

Behaviour-neutral today, and it closes R1, R2, R16 and R17 by construction.

The reader stamps `(pcm, channels, rate)` on every chunk; `push_system`
resamples with the values that arrived WITH the chunk. `configure_sources` is
demoted to supplying the default for an unstamped chunk, and bare bytes still
work on the queue.

Checked before changing the queue's item shape: **one producer** (`wasapi.py`),
**one consumer** (`ingest_queues` on the mixer worker). Every other
`sys_audio_queue` reference in the app is `qsize()` — shape-agnostic.

The stamp is read **once at reader start**, not per iteration. Per-iteration
would recreate exactly the cross-thread read this removes; once is correct
because a device change tears the reader down and `_start_wasapi_loopback` sets
the attributes before starting the next one, so each reader stamps its own
device's format for its whole life.

R17 was confirmed live at today's line numbers, not quoted from the guideline:
`main_window.py:8800-8801` hoists `_wasapi_channels` / `_wasapi_rate` into
locals and `:8803` calls `configure_sources` once before the loop, so assigning
those attributes mid-session never did anything. After the stamp they are
defaults only.

Tests (`tests/test_audio_chunks_carry_their_format.py`, 12) **assert on samples,
never on durations** — duration alone passes the wrong-channel case at some rate
pairs, which is how this class of bug hides. Six fail against the pre-fix tree.
R16 is pinned statically: no call to `push_system` / `ingest_queues` /
`configure_sources` anywhere in `alpha/` outside `audio_mixer_worker`.

Verified: full suite 1361 tests, failing set = the eight-name baseline **plus
the documented item48 intermittent**, which fired in this run and passes in
isolation; `verify_mitigation_claims.py` 27/27; latch audit unchanged at its 4
allowlisted loops; the package driven end-to-end against a synthetic 26.5.10
install.

> ⚠️ **A note on suite timing, so the next run is not misread.** The first full
> run of this stage took **5994 s and showed two extra failures**
> (`test_item88_ui_strings` — both of them file-write assertions). The
> post-commit graphify rebuild was running through it. A clean re-run took
> **834 s** and both passed. If the suite is suddenly 20× slow, check
> `~/.cache/graphify-rebuild.log` before believing the failures.

### Stage 1 follow-up — the hole the stamp could not see — ✅ SHIPPED `1a83c25`, package 26.5.12

Auditing what phase 5 and stage 1 actually deliver against the owner's own
requirement — *headphones plugged or unplugged mid-meeting, no stop, no break,
no Stop/Start, same quality and quantity* — found the stamp had a hole through
it, and it was **proved by driving the real reader, not by reading it**.

`_close_wasapi_stream` joins the reader with `timeout=1.0`, never checks the
result, and nulls the handle either way. `_start_wasapi_loopback` then CLEARS
the capture stop event before spawning the next reader. A reader still wedged in
a blocking `read()` therefore woke to `_wasapi_stop_requested()` False and
carried on — and since the loop re-read `stream = self._wasapi_stream` every
pass while its stamp was captured once at its own start, it picked up the **new**
device's stream and stamped it with the **old** device's format. Measured:

    join timed out after 1.01s: True
    old reader still alive after the rebind: True
    NEW-stream-bytes   stamped 48000 Hz 2 ch   x999

Two readers draining one stream, so the corrupted chunks interleaved with good
ones. Fixed by capturing **identity, stream and format together, once**, at the
top of the worker: `_start_wasapi_loopback` bumps `_wasapi_reader_generation`
before spawning, a reader whose generation is stale exits, and binding the
stream is what makes the stamp describe the bytes — correct by construction
rather than by timing, which was the point of stage 1. The generation is
deliberately **not** `stream.is_active()`: what a closed PortAudio stream does
on that call is not worth betting correctness on.

Also fixed: the rebind cooldown was process-scoped and cleared nowhere, so it
survived Stop/Start and silently refused the first device change of a session
begun within 10 s of the previous session's rebind. `_close_wasapi_stream` now
clears it — **guarded by `_wasapi_rebind_in_progress`**, because that function is
called BY the rebind between stamping the cooldown and reopening, and clearing
unconditionally would have reset the cooldown on every rebind and disabled the
anti-storm guard entirely. That near-miss has its own test.

Four new tests fail against the pre-fix tree. A fifth was written and **deleted**
for passing on both sides; the deletion and its reason are recorded in the file.

> **STILL OPEN, and deliberately not fixed here.** `_sys_source_available` and
> `_mic_source_available` (`timeline_mixer.py:83, :93`) are **latches, not
> liveness** — set True on the first chunk, cleared only in `__init__`/`reset()`.
> Every frame's meta reports them as availability, so a source that has
> contributed nothing for minutes (a rebind gap, a dead mic) still reads
> available in the evidence. Confirmed by reading both assignment sites. Not
> changed in this pass because the source gate and the evidence writers both
> consume those fields, so altering their meaning is its own review — the same
> reasoning `SPEAKER_HOTSWAP_GUIDELINE.md` applies to adding a mixer lock.
> Whoever takes it: add a liveness signal, do not silently flip these two.

### Continuity audit, second pass — findings 2, 5 and 6 — ✅ SHIPPED `0604b21`, package 26.5.13

* **The modal.** `_show_wasapi_error` calls `messagebox.showerror` synchronously
  when already on the main thread, and phase 5 put the rebind ON that thread —
  so a failed rebind froze the whole mainloop mid-meeting until someone clicked
  OK, re-opening the exact hole item 73's own test forbids. Fixed with
  `_start_wasapi_loopback(show_error_dialog=True)`: the rebind passes `False`,
  **Start keeps its dialog**, because there the user pressed a button and is
  waiting for an answer. Both branches of `_show_wasapi_error` are suppressed,
  not just the synchronous one.
* **No detector left.** The failure path calls `_close_wasapi_stream` (clearing
  the baseline, nulling the watch thread) and re-raises, and the watcher was
  only ever started inside `_start_wasapi_loopback` — so one failed rebind ended
  detection for the session and plugging the original device back in went
  unnoticed. The spawn is now `_start_device_watch()`, shared by both paths; the
  failure path re-baselines to the endpoint it just failed to bind and restarts
  it. An unreadable `""` endpoint still starts nothing — `""` is UNKNOWN, and
  baselining on it would make every later poll read as a change.
* **The gap.** `capture_gap_seconds` on `AUDIO_DEVICE_REBIND_COMPLETED`, and
  `seconds_until_failure` + `detection_restarted` on `_FAILED`.

Eight of eleven new tests fail pre-fix. The three that pass are deliberate
guards on behaviour the fix had to preserve. Worth remembering from this pass:
the test module first bound `_start_device_watch` at class-definition time,
which made the whole file error at **collection** so no per-test pre-fix signal
existed at all — a call-time lookup fixed it. And the neighbours caught a stub
whose signature had drifted, where the rebind's broad `except` logged a
`TypeError` as an ordinary device-rebind failure: **a signature bug wearing a
plausible teardown message.**

### Continuity audit, third pass — the microphone follows the device — ✅ SHIPPED `a4de0c0`, package 26.5.14

The largest remaining gap against the owner's requirement, and the one a user
notices first: phase 5 taught SYSTEM audio to follow the default output, and the
microphone was never taught anything. `_start_microphone_capture` read
`sd.default.device[0]` once at session start and nothing rebound it, and
`default_endpoint.py` only read the RENDER endpoint — so an input change was not
even detected. A headset plug moves BOTH defaults, so system audio followed the
headset while the mic stayed bound to a device that may no longer exist, with
`mic_available` still True and the operator's own voice gone for the session.

Three things worth keeping from how it was built:

* **`default_endpoint.py` was render-only by exactly one constant.** The body is
  now parameterised on dataflow with two thin wrappers — not a second copy of
  fifty lines of ctypes COM plumbing.
* **The debounce/latch rules now serve two device flavours**, so they were
  extracted into the pure `evaluate_endpoint_change()` rather than hand-copied.
  Two copies is how the rules hold on one device and silently rot on the other.
* **One watcher thread polls both endpoints.** Safe to couple because session
  start re-raises if WASAPI capture fails (`main_window.py:10616`) and the mic
  is started only afterwards, so that thread exists whenever a mic stream does.

The rebind is close → `sd._terminate()` → `sd._initialize()` → reopen. The
re-init is the load-bearing step: PortAudio snapshots the device list at
initialisation, so close-and-reopen alone would rebind the OLD device and look
like a success. Measured 22.2 ms — cheap enough for the UI thread, where it is
marshalled so two PortAudio inits cannot run concurrently. A mic that is not
running is never started by a device change: the UI switch and the
system-audio-only benchmark are both supported, deliberate absences.

**Measured on this machine, with the COM apartment the watcher thread holds:**
render `{0.0.0.00000000}.{57b9f110-…}`, capture `{0.0.1.00000000}.{53b96938-…}`
— distinct, and the `0.0.0` / `0.0.1` prefix is the dataflow distinction itself.
Without `com_initialize_mta()` **both** readers return `""`; a probe that skips
it proves nothing about either.

Still not proven without hardware, and said so in the code: that PortAudio
reports the new default only after a re-init. Same inference item 73 rests on.

21 of 22 new tests fail pre-fix. Two bugs caught during implementation, both
recorded because both were nearly shipped: the watch loop first **re-read the
endpoint to build the report payload** — a second COM call that can disagree
with the one the decision was made on — and calling the mic poll directly
**killed the whole watcher** on hosts without the microphone mixin, trading the
bug the thread exists to notice for a worse one.

### Continuity audit, fourth pass — the last three — ✅ SHIPPED `5e45e00`, package 26.5.15

* **R14 — the rebind left the mainloop.** Phase 5 put it there for a real
  reason (the watcher-thread join hazard), but `_run_on_ui_thread` is
  `after(0, ...)`, and an `after` callback owns the mainloop until it returns —
  so the whole teardown and re-enumeration ran on the thread that paints the
  transcript. `_schedule_audio_rebind()` spawns a short-lived worker: not the
  watcher, so the join runs; not the mainloop, so nothing freezes.
* **R12 — "opened" is not "working".** The warning used to come down as soon as
  `_start_wasapi_loopback()` returned, but opening a WASAPI loopback stream
  succeeds whether or not the endpoint delivers — that IS the item 73
  condition. The capture threads now count chunks **where the device delivers
  them**, not where the mixer drains (a drain counter cannot tell a producing
  device from a running mixer), and the rebind waits for that count to move.
  No movement leaves the warning up and logs `..._NO_AUDIO`. Only affordable
  because R14 landed first.
* **The availability latches** — fixed additively. `system_source_live` /
  `mic_source_live` are new keys; `*_available` keeps its meaning because the
  source gate and the evidence writers consume it. A device-stopped signal, not
  a silence detector: a quiet room still delivers chunks of zeros.

Moving the rebind off the mainloop exposed two latent hazards, both fixed
rather than left: the single-flight claim was **read-then-set as two
statements** — safe only while the caller was the single-threaded mainloop, and
from a worker two rebinds could interleave two teardowns — and a rebind in
flight **at Stop would resurrect capture**, which the UI-marshalled version was
equally exposed to since an `after(0, ...)` can fire after Stop.

10 of 13 new tests fail pre-fix.

> **A claim of mine, withdrawn.** The microphone rebind's docstring said the two
> rebinds must be serialised so "two PortAudio inits can never run concurrently".
> That is not true: pyaudiowpatch and sounddevice are separate libraries with
> separate PortAudio instances and do not contend. They share a dispatch path
> because one path is easier to reason about. The comment now says that instead
> of asserting a constraint that does not exist.

**Every finding from the device-change continuity audit is now closed.**

> ⚠️ The multi-agent audit that surfaced these **died on the session limit — 53
> of 61 agents errored**, so its "refuted" bucket is *unverified*, not refuted.
> These claims from it were never checked and must not be repeated as fact:
> the Deepgram `_dg_replay_buffer` overwrite on reconnect, `pcm_to_mono_16k_np`
> `int()` truncation (~58.6 ms/min claimed), and the transcript seam across the
> gap.

### Stage 2 + 3 — ✅ CLOSED, mostly by a different route than planned

The sequencing table planned stage 2 as an explicit `swap_system_audio_device()`
and stage 3 as positive confirmation plus debounce. Almost all of that shipped
already, driven by the device-continuity audit rather than by the swap feature:
the rebind runs on its own worker (R14), it is single-flight with a cooldown
(R13), it proves audio resumed before reporting success (R12), and it writes
`AUDIO_DEVICE_REBIND_STARTED / _COMPLETED / _FAILED / _NO_AUDIO` with the gap.
Said plainly because the stage numbering would otherwise imply work that is
done.

What the owner's §8 answers added — ✅ SHIPPED `e288c9f`, package 26.5.16:

* **Q3 = yes, a swap forces an utterance boundary (R11).** The obvious
  `flush("device_swap")` was rejected: `flush()` sets `_stop_boundary_active`
  for any reason and that flag is cleared only by `reset()`, so a swap would
  latch it mid-meeting and silently disable punctuation merging for the rest of
  the session — and falsify the allowlist entry that records the flag as safe
  *because* only stop paths reach it. Clearing `_last_stable_commit` was
  rejected too: it feeds the revision lineage record and the item 41
  non-destructive revise check, so using it to win a merge decision would
  disarm a correctness gate. Shipped instead: a one-shot
  `_merge_boundary_pending`, read through a helper that mutates nothing and
  consumed at the single stable-commit point.
* **Q2 = play the buffer out (R3).** Already the behaviour, so the work was the
  evidence: `buffered_system_seconds` on the completion event, read *before* the
  teardown. **On shortening the 3 s cap: it is not a free knob.**
  `MAX_BUFFER_SAMPLES` is also the maximum catch-up burst for `emit_due_frames`,
  and it was sized to fix a permanent-lag regression (a slow caller emitting 10
  frames/s against 50 due). Shortening it trades swap latency for recovery
  headroom. The reasoning is recorded at the constant; it needs a measurement,
  not a hunch.
* **R13** — swaps per session counted and reported (`swap_index`).

Also fixed on the way: the seam measurement read the mixer off the host, but the
mixer was a **local** in `audio_mixer_worker` and never published — the number
would have been `0.0` for the life of the app. Evidence that looks like an
answer and is not.

### Stage 4 (a UI device picker) — deliberately NOT built

Recorded as a decision rather than left as an open row. Q1 asked whether to
follow the OS default automatically or offer a picker; automatic follow is now
shipped for **both** the speakers and the microphone, which is what the owner's
requirement actually asked for. The guideline itself says the UI is a separate
decision, and warns that the responsive header is the area items 71, 92 and 93
churned most. A picker would be a *new capability* — choosing a non-default
device — not a completion of this one. Revisit only if someone asks for that.

> **§8 status:** Q1 and Q4 were answered by shipping (automatic follow; the
> microphone is in scope and follows the device as of 26.5.14). Q2 and Q3 were
> answered by the owner and are implemented above. No open design questions
> remain.

### Stages 2-4 — original text, superseded

Four design questions in §8 of that guideline are open and change the design;
answer them before scoping stages 2-4. Question 1 (follow the OS default
automatically, or an explicit picker) is **partly answered by phase 5 already
shipping the automatic follow** — that decision needs confirming rather than
taking.

---

## Summary

| Phase | What | State |
|---|---|---|
| 1 | Item 1 — translation queue hole | ✅ shipped `49a5178`, package 26.5.5 |
| 2 | Item 3 — WASAPI reader + the audit-tool blind spot | ✅ shipped `fd93e61`, package 26.5.6 |
| 3 | Audit the five unreviewed subsystems | ✅ 5 of 5 (items 10-17) |
| 3b | Items 11, 10, 13 + 15 — the Stop/Start window and the naming fix | ✅ shipped, package 26.5.7 |
| 4 | Items 2, 8, 9, then 14 → 4 + 12 | ✅ shipped, package 26.5.8 |
| 5 | Item 5 — device re-bind | ✅ shipped `c2bdab2`, package 26.5.9 |
| 6 | Items 17, 16, 6, 7 + the follow-tail leftovers | shipped `42f0d29`, package 26.5.10 |
| 7 stage 1 | Per-chunk format stamp — R1, R2, R16, R17 | ✅ shipped `c33f5f6`, package 26.5.11 |
| 7 stage 1 follow-up | Superseded reader stops; cooldown is session-scoped | ✅ shipped `1a83c25`, package 26.5.12 |
| 7 stage 1 follow-up | Continuity audit findings 2, 5, 6 — modal, lost detector, unmeasured gap | ✅ shipped `0604b21`, package 26.5.13 |
| 7 stage 1 follow-up | The microphone follows the default input device | ✅ shipped `a4de0c0`, package 26.5.14 |
| 7 stage 1 follow-up | R14 worker, R12 confirmation, source liveness | ✅ shipped `5e45e00`, package 26.5.15 |
| — | Continuity audit — **all 10 findings closed** | ✅ done |
| 7 stages 2-3 | Swap boundary (R11), seam measured (R3), swap counter (R13) | ✅ shipped `e288c9f`, package 26.5.16 |
| 7 stage 4 | UI device picker | ⛔ deliberately not built — see the section above |
| — | **Phase 7 complete. No open §8 design questions.** | ✅ done |

Phase 3's audits are complete — 5 of 5, as the table above says. (This line used
to read "Phase 3's four remaining audits", contradicting the table two rows
up.)
