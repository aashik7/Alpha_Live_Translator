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

## Phase 1 — Stop the silent truncation (item 1)

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

## Phase 2 — Stop system audio dying for the session (item 3, plus the scan)

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

## Phase 3 — Close the audit gap (no code changes)

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

---

## Phase 4 — The provable batch (items 2, 8, 9, 4)

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

3. **Item 4 — log rotation.** Lift `_rotate_if_needed` out of `_JsonlWriter`
   (`evidence_jsonl.py:39-59`) into a module-level function and call it from
   `async_debug_log.py:213` and `:394` and `freeze_guard_log.py:69`. The two
   writers holding a long-lived handle (`japanese_accuracy_log`,
   `diagnostic_test_log`) need a running byte counter that closes, rotates and
   reopens — a rotate-on-path-change hook fires at most once per session and
   would not fix it. Separately, bound
   `troubleshooting/runs/_pending/logs/*` once at startup: that file is the
   only one unbounded *across* sessions.

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

1. **Item 6** — delete the synchronous fallback at
   `audio_temp_capture.py:271-275`. By the time control reaches it the item is
   already counted and logged, so returning is consistent with the function's
   own contract. That removes the only edge from inside the `:377` lock back
   into the lock. Do **not** reach for `threading.RLock` — it would legitimise
   re-entry at all 16 `with _lock` sites and mask this class of bug.
2. **Item 7** — in the `except Exception as exc` at `deepgram_client.py:1741`,
   emit a structured `ENGLISH_LIFECYCLE_INGEST_FAILED` event alongside the
   print, mirroring the two Japanese siblings at :1601-1611 and :1652-1662.
   That makes the downstream `IDENTITY_REJECTION` attributable. Do **not** mint
   a synthetic `canonical_utterance_id` to let the fall-through commit — that
   would defeat the fail-closed identity gate, which measurement showed working.
3. **From the follow-tail review** — `_render_transcript_from_store_now`
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

| Phase | What | Ship? |
|---|---|---|
| 1 | Item 1 — translation queue hole | **Yes, immediately** |
| 2 | Item 3 — WASAPI reader + the audit-tool blind spot | Yes, with or after phase 1 |
| 3 | Audit the five unreviewed subsystems | No code change |
| 4 | Items 2, 8, 9, 4 | Yes, one package |
| 5 | Item 5 — device re-bind | Yes, alone |
| 6 | Items 6, 7 + the follow-tail leftovers | Batch |
| 7 | Speaker hot-swap | After the rest is green |
