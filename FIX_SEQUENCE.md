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

## Phase 4 — The provable batch (items 2, 8, 9, 14, 4 + 12)

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

## Phase 5 — The risky one, alone (item 5)

Device re-bind. Ship by itself so any regression is attributable.

In `_report_default_device_changed`, after raising the signal, marshal a rebind
through `_run_on_ui_thread`: set `_stop_event`, `_close_wasapi_stream()`, clear
the event, `_start_wasapi_loopback()`. `PyAudio.terminate()` plus a fresh
`PyAudio()` is what actually picks up the new default — `Pa_Initialize()`
re-snapshots the device list, and a re-query on the live handle provably cannot.

Two hazards to handle in the same change:

- It must **not** run on the watcher thread — `_close_wasapi_stream` joins that
  thread, and `wasapi.py:352-357` already guards
  `watch is not threading.current_thread()`.
- The old watcher must actually be stopped before the rebind.
  `_close_wasapi_stream` does not set `_stop_event` itself, so its 1 s join
  would time out, null the handle, and `_start_wasapi_loopback` would spawn a
  **second** watcher.

Re-baseline `_wasapi_default_endpoint_baseline` and clear
`_wasapi_device_change_reported` / `_audio_device_changed` on success; on
failure keep the current warning, which is a correct fallback.

---

## Phase 6 — Hygiene and attribution

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
phase 2. Start with **Stage 1, the per-chunk format stamp** — stamping
`(pcm, channels, rate)` on every chunk at the reader is behaviour-neutral today,
verifiable with a tone test, and it makes the two worst risks impossible by
construction rather than by discipline.

Four design questions in §8 of that guideline are still open and change the
design; answer them before scoping stages 2-4.

---

## Summary

| Phase | What | State |
|---|---|---|
| 1 | Item 1 — translation queue hole | ✅ shipped `49a5178`, package 26.5.5 |
| 2 | Item 3 — WASAPI reader + the audit-tool blind spot | ✅ shipped `fd93e61`, package 26.5.6 |
| 3 | Audit the five unreviewed subsystems | ✅ 5 of 5 (items 10-17) |
| 3b | Items 11, 10, 13 + 15 — the Stop/Start window and the naming fix | ✅ shipped, package 26.5.7 |
| **4** | **Items 2, 8, 9, then 14 → 4 + 12** | **next code change**, one package |
| 5 | Item 5 — device re-bind | alone |
| 6 | Items 17, 16, 6, 7 + the follow-tail leftovers | batch |
| 7 | Speaker hot-swap | after the rest is green |

Phase 3's four remaining audits are read-only and touch no code, so they can run
between the code phases rather than blocking them.
