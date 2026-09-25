# Alpha Live Translator — code review, 2026-09-04

Reviewed at `29d61d5`, `origin/main` in sync.

## How to read this

Every item below carries a **verdict**, and the verdict means something specific:

| Verdict | What it means |
|---|---|
| **CONFIRMED** | The failure was *executed* through a path production actually takes. |
| **PLAUSIBLE** | The mechanism is real but was only reached by constructing the state by hand. A hazard, not yet a bug. |
| **REFUTED** | Driving the real code showed the claim does not hold as shipped. |

That distinction is the whole point of this pass. This project has been burned
four times by a diagnosis that looked solid from *reading* and was wrong the
moment the code was actually driven — and **it happened once more inside this
review** (item 6 below, and the retraction in §4). Nothing here is marked
CONFIRMED on the strength of reading the source.

Ranked most severe first. Items 1–7 are the inherited claim list, re-verified;
items 8–9 are new. Read **What this review did NOT cover** before treating
this as a complete picture — four subsystems were never reached.

---

## Status

| Shipped | Items | Package |
|---|---|---|
| Phase 1 | 1 | 26.5.5 |
| Phase 2 | 3 | 26.5.6 |
| Phase 3b | 10, 11, 13, 15 | 26.5.7 |
| Phase 4 | 2, 8, 9, 14, 4, 12 | 26.5.8 |
| Phase 5 | 5 | 26.5.9 |
| Phase 6 | 6, 7, 16, 17 + follow-tail leftovers | 26.5.10 |
| Phase 7 stage 1 | Per-chunk format stamp (R1, R2, R16, R17) | 26.5.11 |
| ↳ follow-up | Superseded reader; session-scoped cooldown | 26.5.12 |
| ↳ follow-up | Rebind modal, lost detector, unmeasured gap | 26.5.13 |
| ↳ follow-up | The microphone follows the default input device | 26.5.14 |
| ↳ follow-up | Rebind worker (R14), audio confirmation (R12), source liveness | 26.5.15 |
| Phase 7 stages 2-3 | Swap boundary (R11), seam measured (R3), swap counter (R13) | 26.5.16 |
| Item 18 | Stable reconstruction paired two streams by index | 26.5.17 |
| Audit 2026-09-14 | 19 (swap deleted the in-flight sentence), 20 (crash logs never listening) | 26.5.18 |
| Gap audit 2026-09-14 | 21 (a close during a meeting dropped item 16's cancels) | 26.5.19 |
| Review of 26.5.19 | 21's regression (the close timeout's autosave wrote nothing) | 26.5.20 |
| Review of 26.5.19 | 22 (a close killed the Stop worker mid-finalize) | 26.5.21 |
| Gap audit 2026-09-14 | 23 (keyterm latch, "400" substring, NameError), 24 (lifecycle rows pile up) | 26.5.22 |
| Follow-up to 22 | 25 (the waiting close looked finished) | 26.5.23 |
| External-monitor report | 26 (hidden widgets came back after a monitor move) | 26.5.24 |
| External monitor, live on 26.5.24 | 27 (Hide/Show did not re-flow at 100 % scaling) | 26.5.25 |

**All 27 items in this review are closed.** Items 6 and 7 were the two REFUTED
ones; what phase 6 shipped for each is the latent hazard and the missing event
respectively, not the reported defect — see their sections below.

> This table was stale for several packages: it stopped at phase 5 and still
> read "Still open: 6, 7, 16, 17" after phase 6 had shipped exactly those four
> (`42f0d29`, package 26.5.10, which adds a test file per item). Recorded
> because this repo's own rule is that **a ledger row saying OPEN is not
> evidence that work is outstanding — grep the git history first.** A session
> trusting that line would have redone four finished items.
>
> **It went stale a second time within a week.** It stopped at 26.5.15 and read
> "All 17 items" while item 18 already had a full section below it. Corrected
> at 26.5.18. Whoever adds an item: add its row here in the same commit.

## The whole review in one table

| # | Issue | Details | Risk | Severity | Importance |
|---|---|---|---|---|---|
| **1** | A full translation queue stops the translation pane for the rest of the session | `put_nowait` raises without evicting, so the **newest** line is dropped. The handler erases the sequence from `_accepted_sequences` but never rolls back `_next_translation_sequence`, leaving a permanent hole in the dense ordering key. The commit gate advances only by finding the next sequence in `_held`, so it parks on that hole forever. | Measured: 100/100 delivered, then one drop, then **200 later translations complete and none reach the pane**. Export reads the pane, so the delivered translated transcript ends at the drop. `degraded=False`, `status_message=''` — the indicator stays silent. Fires from slow-but-successful DeepL alone; no error needed. | **CRITICAL** | **SHIP-BLOCKER** |
| **2** | The wrong-language warning is a stub that always says "commit" | The four `LANGUAGE_*` flags are not the cause. `_evaluate_language_reliability` (`main_window.py:7261`) returns a constant dict — 96 varied inputs collapsed to one output. A working detector, `_language_script_warning`, sits two methods above with **zero call sites**. | Bengali at 0.99 confidence while the operator selected Japanese is committed, translated from the wrong source and exported, with nothing marking it. The evidence trail hardcodes `blocked_count=0`, so a post-mortem concludes nothing happened. | **HIGH** | FIX-SOON |
| **3** | The WASAPI reader thread dies permanently on one error | Plain `threading.Thread` at `wasapi.py:289`; only the *device watch* was converted to `SupervisedThread`. Two permanent exits: `break` at `:255` on any exception, `break` at `:226` on an inactive stream. Nothing restarts it. | Driven: one `OSError` and the thread is gone, `_stop_event` never set, queue frozen. All system audio — the far end of the meeting — is lost for the session. The stream still reports active, so the indicator keeps showing Signal OK over a dead capture path. | **HIGH** | FIX-SOON |
| **4** | Four log writers have no rotation; one file is unbounded across sessions | Rotation exists only in `evidence_jsonl._rotate_if_needed`. Measured: 0 `stat()` calls over 5,000 events for the other four writers vs 875 for `evidence_jsonl`. | ~34 MB per 2-hour meeting, ~136 MB per 8-hour day, ≈8 GB/year for one meeting a day. `runs/_pending/logs/japanese_accuracy.log` is never truncated **across** sessions — already 335 MB in 17 days on this machine. When the disk fills, every writer swallows the `OSError` and evidence silently stops. | MEDIUM | FIX-SOON |
| **5** | A default-audio-device change is detected but capture is never re-bound | The handler writes one accuracy row and raises a UI signal. It never closes, reopens or re-indexes the stream. PortAudio froze the device list at `Pa_Initialize()`, so only `terminate()` + a fresh `PyAudio()` picks up the new default. | System audio silently yields **zero frames** — no error, no thread death, stream still `is_active()`. The mic keeps producing transcript, so the session looks half-alive. The UI does say "Audio device changed" within ~4 s; recovery is manual only. | MEDIUM | BACKLOG |
| **6** | ~~Audio-retention self-deadlock~~ — **REFUTED** | The edge is real and I forced a permanent hang with it. But the only way in (`:272`) needs `put_nowait` to fail **twice**, which needs a competing producer — and `_enqueue_flush` is the sole producer and is serialised by the lock itself. | As shipped: nothing. 43 real overflows single-threaded and 1901 across 3 threads drove the branch and hit `:272` **zero** times. Latent only: one edit adding a second producer turns it into an unrecoverable audio hang. | LOW | BACKLOG |
| **7** | ~~English lifecycle bypasses the commit authority~~ — **REFUTED**, but the utterance is lost | The authority is not in `deepgram_client`; it is re-entered downstream at `main_window.py:8309` and **fails closed** correctly (`observe_identity` → `missing_identity_key`). Nothing escapes it. | The opposite failure: if the lifecycle block raises, the utterance vanishes from the ledger, the store **and** the screen. The only trace naming the cause is a bare `print()`; the two Japanese siblings each emit a structured event, the English one does not. Latent — needs the lifecycle to raise. | MEDIUM | FIX-SOON |
| **8** | Every DeepL placeholder key the project ships passes the Start check | `has_deepl_api_key()` is bare truthiness and never consults `PLACEHOLDER_API_KEYS`. That set's DeepL entry (`your_deepl_api_key_here`) matches **nothing the project ships** — `.env.example` uses `your_deepl_auth_key_here`. | No signal at Start for any non-empty-but-invalid DeepL key. It surfaces mid-session as `auth_failed`, and the indicator blames the **provider**, never the key — after segments have already been lost. | MEDIUM | FIX-SOON |
| **9** | The installer's own key template passes both the build gate and the runtime check | `read_keys()` rejects only an *empty* value, so `your-deepgram-api-key` compiles and is written into `{app}pp\.env`. `PLACEHOLDER_API_KEYS` does not know the hyphenated spelling either. | A build from an unedited template installs, launches, shows green, passes Start — then fails the Deepgram handshake with a 401 on the client's machine. Only a human reading `keys loaded (deepgram 21 chars…)` stands in the way. | MEDIUM | FIX-SOON |
| **10** | The UI says "Stopped" 5 s in while the deliverable has up to 66 s still to be written | `_stop_ui_watchdog_tick` force-restores at `>= 5.0` s and clears BOTH guards that keep a new session out (`_is_finalizing`, `_stop_finalize_started`). `toggle_listening` has no other guard. Measured from the step table: 20 steps, **66.0 s** of budget before `write_final_alpha`, 71.0 s for the whole worker. | A second session can begin while the previous one has not written its transcript. The two big budgets are real: `drain_audio_queue` 25 s spends it when audio is still queued, and `translation_worker_shutdown` 16 s is longest under exactly the slow-DeepL condition item 1 lives in. **PLAUSIBLE** — every link verified, but not driven to corruption. | **HIGH** | FIX-SOON |
| **11** | `SECOND_RUN_FOLDER_CREATION_BLOCKED` blocks nothing | The guard body has NO `return`/`raise` — it only logs — and `set_active_run_folder` at `:712` sits outside it. Verified by AST, not by eye. | With item 10, a second Start repoints every runtime writer at the new run's folder and force-closes the pending ones while the old worker is still writing. Worse than the direct cost: the event NAME asserts a guard that does not exist, so anyone reading a client's logs concludes the rebind was prevented and stops asking. | **HIGH** | FIX-SOON |
| **12** | Every rebind migrates the unbounded `_pending` pile | `rebind_all_runtime_writers` runs `migrate_pending_files_to_run_folder`, and item 4 established `_pending/logs/*` is the one file nothing rotates across sessions. | Measured today on this machine: **~457 MB** (`japanese_accuracy` 358 MB + `freeze_guard` 66 MB + `async_debug` 33 MB) copied on every rebind. A bare harness calling it twice **did not finish in 120 s**. This is item 4's second-order cost that item 4 did not name: not just disk, but latency on a path the user waits on at Start — growing without bound. | MEDIUM | FIX-SOON |
| **13** | The lifecycle's stale-session guard cannot fire | `session_id` is assigned `self._session_id` whenever that is truthy, so the comparison `session_id != self._session_id` is between one value and itself; when it is falsy the `and self._session_id` term already made the guard False. Evaluated over all three input shapes: never fires. | The commit authority still fails closed — `canonical_identity_registry.py:116` is a real comparison and does reject. But a stale event reaches the lifecycle's OWN state first, so a final from a previous session can extend or replace the CURRENT session's active utterance. Matters more with item 10, where a second session can begin while the first is finalizing. | MEDIUM | FIX-SOON |
| **14** | `rebind_all_runtime_writers` self-deadlocks, and the workaround hiding it is why `_pending` grows | The loop at `:742` calls `rebind_runtime_writer` while holding `_lock`; that function's first statement takes `_lock` again, and `_lock` is a non-reentrant `threading.Lock` confirmed at runtime. The registry is populated by ordinary use — `get_log_path` registers on every call. | **Driven through the real entry point**: did not return in 25 s, stack blocked at `:343` under `:742`, and the `JapaneseAccuracyLogWriter` thread taken down with it at `:337`. Start survives only because `STARTUP_RECOVERY_MODE`/`EVIDENCE_SAFE_MODE` defer the rebind — which is **why item 12's `_pending` pile reached 358 MB**: the writers are never moved out of it. Still reachable via `preflight_upload_evidence` on the evidence-packaging path. | **HIGH** | FIX-SOON |
| **15** | `TK_CALL_SITE_SAFE` counts a substring and calls it safe | `scan_tk_call_sites` returns `str.count(".after(")` over two files — comments and docstrings included — applies no test, and writes an event named `TK_CALL_SITE_SAFE`. Measured on the shipped tree: `{'safe': 31, 'refactored': 0}`. | Item 11's shape in the evidence stream: a name asserting a property nothing established. A reader of a client's log concludes the Tk call sites were audited and cleared; they were counted. The cost is a question retired without being asked — the same way the WASAPI reader sat behind a scan that could not see it. | MEDIUM | FIX-SOON |
| **16** | Four recurring `after` jobs are never cancelled, and `_on_close` cancels nothing | Of 31 `after`/`after_idle` calls, 16 store an id and 15 discard it; of 13 stored job attributes, `_jp_pipeline_hb_after_id`, `_transcript_ui_batch_after_id`, `_ui_event_bus_after_id` and `_ui_queue_defer_after_id` are never passed to `after_cancel`. `_on_close` (`:11031`) contains no `after_cancel` at all. | Small, and filed as such. Tk discards pending callbacks when the interpreter is torn down, so at a clean exit this is tidiness; the observable form is the `invalid command name … ("after" script)` teardown noise this project's own test runs produce. No user-visible failure was measured. | LOW | BACKLOG |
| **17** | A raise inside the continuity-hold tick discards the buffered sentence silently | `try_execute_continuity_hold`'s handler sets `self._buffer = None` under `JAPANESE_CONTINUITY_ASSEMBLER_SAFE_MODE` (True at runtime) and returns `True`. The sibling `_handle_assembler_exception` faces the same situation and instead emits `ASSEMBLER_EXCEPTION_CAUGHT`, then recovers the fragment and re-commits it. | **Driven on a real assembler** with a real buffered sentence: returned `True`, buffer `None`, and the only event was the crash logger's own `ASYNC_LOG_EMERGENCY_WRITE`. Nothing names the loss — no event, no counter. A spoken Japanese sentence never reaches the transcript, the ledger or the delivered file, and the evidence gives a reader no way to know one went missing. Trigger is abnormal (needs the inner call to raise), so the mechanism is proven and the frequency is not. | MEDIUM | FIX-SOON |

## 1. A full translation queue silently stops the translation pane for the rest of the session — ✅ FIXED (phase 1)

| | |
|---|---|
| **Verdict** | **CONFIRMED** — executed |
| **Severity** | **CRITICAL** |
| **Importance** | **SHIP-BLOCKER** |
| **Where** | `alpha/translation/translation_worker.py:519-543`, gate at `:1019-1071` |

**Issue.** When the translation queue is full, the dropped job's sequence number
is never rolled back, so it becomes a permanent hole in the dense `1..N`
ordering key. The commit gate advances only by finding
`_next_translation_sequence_to_commit` in `_held`, so it parks on that hole
**forever**.

**Details.** `put_nowait` on a full FIFO raises without evicting, so the line
just spoken is the one dropped — the 100 older queued lines survive. The handler
then erases its own tracks: it removes the sequence from `_accepted_sequences`,
which is exactly what the shutdown integrity check
(`MISSING_ACCEPTED_TRANSLATION_SEQUENCES`) reads, and it adds no drop counter, so
`stop_finalize_worker.py:571-596` classifies the rejection as `"unknown"`.
`shutdown()`'s bounded-drain rescue cannot heal it either — it only cancels
sequences still in `_accepted_sequences`. The worker meanwhile reports
`degraded=False` with an empty `status_message`, so the existing
"Translation degraded" indicator stays dark.

**Risk.** Fires when 100 committed lines back up behind the single translation
thread — reachable **with no error at all**, purely from slow-but-successful
DeepL responses (10 s client timeout, up to 2 retries, one consumer thread),
which is also precisely the case where the circuit breaker never opens. Measured:
after the drop, **200 further lines translated successfully and none reached the
pane**. The translation pane simply stops updating while transcription keeps
scrolling — no message, no status change, no dialog. Because export reads the
pane, **the delivered translated transcript ends at the drop point.** The only
trace anywhere is one WARNING line in `logs/console-<ts>.log`.

**Independently reproduced** on the real worker, with the gate drained first so
the hole is isolated as the cause: 100 of 100 delivered and the gate sitting at
101; then the drop allocates 101 and does not roll it back; then 200 later
translations complete successfully and **not one of them is delivered** -- all
200 park in `_held` while the gate stays on 101. `degraded` is `False` and
`status_message` is empty throughout. (A first version of this probe did not
drain the gate first, which left it stuck at 1 for an unrelated reason and would
have proved nothing -- the same reachability trap as item 6.)

**Fix.** Two changes inside the `except queue.Full` block. (a) Record the dropped
sequence in a `_dropped_sequences` set and let the ordering loop advance past it,
which converts a session-ending stall into the loss of one line. Do *not* roll
back `_next_translation_sequence` — it is allocated under `_lock` while
`put_nowait` runs outside it, so a concurrent submit could re-issue the number.
(b) Add a `TRANSLATION_QUEUE_FULL_DROPS` counter, set a status message, and
include a recent-drop flag in the `degraded` property so the existing indicator
paints it with no new UI code. No test covers this today.

---

## 2. The wrong-language warning is a stub that always says "commit" — ✅ FIXED (phase 4)

| | |
|---|---|
| **Verdict** | **CONFIRMED** — executed |
| **Severity** | **HIGH** |
| **Importance** | **FIX-SOON** |
| **Where** | `alpha/ui/main_window.py:7261-7269`, call site `:8595-8607` |

**Issue.** `_evaluate_language_reliability` has been replaced by a constant dict
that always returns `decision="commit"`, `script_warning=None` — regardless of
input. 96 varied inputs collapsed to one output tuple.

**Details.** The four constants are not the cause and should be left alone:
`LANGUAGE_GATE_BLOCKING_MODE` has zero readers, `LANGUAGE_GATE_WARNING_ONLY` has
one that only writes it into a startup diagnostic, and the two `*_ENABLED` flags
have one control-flow reader each where `False` is a no-op. The real decision
point is the stub. A **working** detector, `_language_script_warning`, sits two
methods above and correctly flags both mismatch directions when called by hand —
with **zero call sites**. So do `_log_language_commit_warning` and
`_hold_unstable_language_candidate`.

One sub-clause of the inherited claim is **refuted**: `_log_detected_language`
returning `None` harms nothing. Its single call site
(`deepgram_client.py:2137`) is a bare expression statement; it is a pure logger
and `None` is its correct contract.

**Risk.** Silent wrong-language corruption with no user-visible signal and no
working forensic counter. A segment detected as Bengali at 0.99 confidence while
the operator selected Japanese is committed to the transcript, translated by
DeepL from the wrong source, and exported into the frozen ledger — indistinguish-
able from a clean line. Fires on mid-meeting code-switching, a mis-set dropdown,
or Deepgram misidentifying a short or noisy utterance. Second-order: the evidence
trail *understates* it — `_log_language_summary` hardcodes `blocked_count=0` and
every warning counter is dead at 0, so a post-mortem reading the logs concludes
nothing happened.

**Fix.** Un-stub `_evaluate_language_reliability` using the code already beside
it: pass the caller's real `metadata["allowed_languages"]` instead of the
hardcoded `["en"]`, set `script_warning = self._language_script_warning(...)`,
and return `decision="warn"` when it fires. At the call site, branch once on
`"warn"` to bump the two stats and call the existing
`_log_language_commit_warning` — then **commit anyway**, preserving today's rule
that the manual dropdown is authoritative and nothing is dropped. ~10 lines,
reusing three already-written functions. The disabled state was deliberate (the
`constants.py` comment cites an English-selection regression caused by
force-locking to `ja`) — but the decision was to stop *forcing* a language, not
to stop *warning*, and the warning went with it.

---

## 3. The WASAPI reader thread dies permanently on one error — ✅ FIXED (phase 2)

| | |
|---|---|
| **Verdict** | **CONFIRMED** — executed |
| **Severity** | **HIGH** |
| **Importance** | **FIX-SOON** |
| **Where** | `alpha/audio/wasapi.py:289` (spawn), `:255` and `:226` (exits) |

**Issue.** `_wasapi_reader_worker` runs on a plain `threading.Thread`. Its single
loop `break`s on any read exception (`:255`) and on a stream reporting itself
inactive (`:226`). Either exit ends the thread, and nothing restarts it. The
`SupervisedThread` class is imported **in the same file, at `:308`**, and used
only for the device watcher.

**Details.** The handler at `:246` catches `Exception`, so nothing propagates and
nothing outside learns the reader died: no supervisor, no liveness poll, no
re-spawn. `note_capture_error()` only bumps a counter. The stream object, the
PyAudio handle and `_stop_event` are all left in their healthy running state, so
`_close_wasapi_stream` never triggers either — the session stops receiving system
audio while believing it is capturing. Driven: one `OSError` on the third read
left the thread dead, `_stop_event` unset and the queue frozen; a stream merely
going inactive exited **silently**, with no print and no counter.

`tools/audit_unrecoverable_latches.py` does not report this. Its scan-2 question
is *"can an exception escape the outermost loop body?"*, implemented as *"does
the loop body contain a top-level `try` with an `except Exception`?"* — it never
inspects the handler's body, so `break`, `continue`, `return` and `pass` handlers
are measurably indistinguishable to it. The plain `break` at `:226` is invisible
for a second reason: the scan never looks at unconditional control flow at all.

**Risk.** The user loses all system audio — the loopback half of a bilingual
meeting — for the entire remaining session, with no dialog and no way back short
of Stop/Start. Because the stream still reports active, the connection indicator
keeps showing Signal OK: a healthy UI over a dead capture path. Honest firing
conditions: `stream.read()` is called with `exception_on_overflow=False`, so the
most frequent Windows transient is already suppressed; this is a device-removal /
driver-reset / format-change path, not an every-meeting one. That mixed
likelihood is why it is HIGH and not CRITICAL — the mechanism is certain, the
trigger rate is not.

**Fix.** Replace the `break` at `:255` with `time.sleep(0.05); continue` after the
existing print and `note_capture_error()`, optionally giving up after N
consecutive failures. **Keep** the `break` at `:226` — it is the clean-shutdown
exit `_close_wasapi_stream` relies on. If a belt-and-braces restart is wanted,
also spawn the reader through `SupervisedThread`, giving the supervisor its **own**
stop event, never `self._stop_event` (`SupervisedThread.start()` clears the event
it is given, and clearing the shared audio stop event would un-stop the reader
and mixer). Separately, fix the audit's blind spot at
`tools/audit_unrecoverable_latches.py:212-220`: `_body_swallows` should return
True only when the handler body also cannot leave the loop.

---

## 4. Four log writers have no rotation; one file is unbounded across sessions — ✅ FIXED (phase 4)

| | |
|---|---|
| **Verdict** | **CONFIRMED** — measured |
| **Severity** | **MEDIUM** |
| **Importance** | **FIX-SOON** |
| **Where** | `japanese_accuracy_log.py`, `async_debug_log.py`, `freeze_guard_log.py`, `diagnostic_test_log.py` |

**Issue.** Rotation lives in exactly one place —
`_JsonlWriter._rotate_if_needed` in `evidence_jsonl.py` — which stats the file
before every append and shifts `.1`…`.5` backups past `LOG_MAX_FILE_MB`. The
other four writers open in append mode and never consult size or age.

**Details.** Measured at runtime: **zero** `stat()` calls on those four paths
across 5,000 real events each, against 875 for `evidence_jsonl`.
`japanese_accuracy_log`'s single reference to `LOG_ROTATION_ENABLED` only emits
the string `"LOG_ROTATION_ACTIVE"` — which is why a grep-based read of this area
is misleading. Two of the four hold one file handle open for the whole session
and reopen only when `get_log_path()` changes, so a rotate-on-open fix would fire
at most once per session.

**Risk.** ~34 MB for a 2-hour meeting, ~136 MB for an 8-hour day (worst observed
315 MB). Nothing prunes finished run folders, so one 2-hour meeting per working
day accumulates roughly **8 GB/year**. The genuinely unbounded file is
`troubleshooting/runs/_pending/logs/japanese_accuracy.log`, which every session
appends to during the bootstrap window before `set_active_run_folder()` rebinds
the writers and which nothing ever truncates — **already 335 MB in 17 days** of
ordinary use on the development machine. Second-order: when the disk does fill,
every one of these writers swallows the `OSError` (`except Exception: pass`), so
the app keeps running and reports nothing while evidence stops being recorded.
There is no disk-free probe anywhere in the codebase.

**Fix.** Lift `_rotate_if_needed` out of `_JsonlWriter` into a module-level
`rotate_if_needed(path)` — same body, same constants — and call it before the
appends in `async_debug_log` and `freeze_guard_log` (they open per write, so a
pre-open check suffices). For the two long-lived handles, keep a running byte
counter and close/rotate/reopen when it crosses the cap. Separately, truncate or
rotate `runs/_pending/logs/*` once at startup in `get_pending_folder()` — that
file is the only one unbounded *across* sessions rather than merely within one.

---

## 5. A default-audio-device change is detected but capture is never re-bound — ✅ FIXED (phase 5)

| | |
|---|---|
| **Verdict** | **CONFIRMED** — executed |
| **Severity** | **MEDIUM** |
| **Importance** | **BACKLOG** |
| **Where** | `alpha/audio/wasapi.py:51` (`_report_default_device_changed`) |

**Issue.** Capture binds to one PortAudio device index at Start. On a confirmed
default-endpoint change the handler writes one `AUDIO_OUTPUT_DEVICE_CHANGED` row
and raises the `_audio_device_changed` signal — and never closes, reopens or
re-indexes the stream.

**Details.** `Pa_Initialize()` froze the device list, so the app polls the OS
directly over COM on a 2 s watcher thread. After the switch the old endpoint
yields zero frames, so the reader falls into its `idle_polls` branch, prints
"[WASAPI] No loopback audio yet" every ~5 s, raises nothing, never `break`s and
never enqueues. `note_capture_error()` is not reached and no Deepgram gap marker
fires. The microphone is a separate `sounddevice.InputStream` on a *capture*
endpoint and is unaffected.

**Risk.** System-audio capture **silently produces nothing** — zero bytes, no
error, no thread death, stream still reporting `is_active()`. It does not keep
flowing from the old device and it does not stop the session. The user loses all
far-end audio from the moment Windows moves the default output (headset plugged
in or out, a conferencing app grabbing the device, a monitor waking) — while the
local mic keeps producing transcript, so the session looks half-alive rather than
dead. The claim's "silent" is true at the audio layer but **not** at the UI
layer: within ~4 s the indicator shows "● Audio device changed" with the device
name and the remedy. Recovery is manual only.

**Fix — shipped in 26.5.9 (`c2bdab2`), with one correction to the plan above.**
The originally proposed rebind was "set `_stop_event`, close, clear it, start
again". That would have been **worse than the bug**: `self._stop_event` is the
session-wide stop event, read in 28 places including the microphone, the audio
mixer worker and the Deepgram sender and reconnect loops. Setting it stops the
whole session; clearing it brings none of those threads back.

What shipped instead: the capture owns its own `_wasapi_stop_event`, and
`_wasapi_stop_requested()` is true when either it or the session event is set —
so capture can be torn down and restarted alone. `_close_wasapi_stream` sets it,
`_start_wasapi_loopback` clears it. `_rebind_wasapi_to_default_device()` closes
then starts (`PyAudio.terminate()` plus a fresh `PyAudio()` is the only thing
that picks up the new default; a re-query on the live handle provably cannot —
measured, same index), is marshalled through `_run_on_ui_thread` so it never
runs on the watcher thread, and is single-flight with a 10 s cooldown so a
flapping device cannot storm. A failed rebind leaves the warning up.

Both predicted hazards were real, and the second needed more than the plan
said. Stopping the old watcher was not sufficient: it *waited* on the session
event, which a rebind leaves clear, so it could not wake inside the 1.0 s join —
the join timed out, the handle was dropped and the restart spawned a second
watcher beside the sleeping first. It now waits on the capture event, which
every stop path sets. Pinned at the production timings by
`TheWatcherWakesInsideTheJoinWindowTest`, proven to fail pre-fix.

Still resting on one untestable-without-hardware inference, unchanged from item
73: that a real default-device switch changes the endpoint ID, and that the
reopened stream binds to the new endpoint.

---

## 6. The audio-retention self-deadlock — REFUTED — ✅ latent edge removed (phase 6)

| | |
|---|---|
| **Verdict** | **REFUTED** as a live bug; the hazard is real and latent |
| **Severity** | **LOW** |
| **Importance** | **BACKLOG** |
| **Where** | `alpha/utils/audio_temp_capture.py:377-498`, `:272`, `:566` |

**Issue as claimed.** `_ingest_audio_chunk_impl` holds the non-reentrant `_lock`
across `:377-498` and calls `_enqueue_flush` at `:491`, which re-takes the lock
and deadlocks.

**What is true.** Every structural half. `_lock` is `threading.Lock()` — verified
non-reentrant at runtime. The `with _lock:` block really does end at `:498`, and
the call to `_enqueue_flush` at `:491` really is inside it.
`_flush_chunk_locked_from_item` really does take the same lock at `:566` — that
exact stack was forced and hung permanently.

**What is false.** The path from `:491` to `:566` runs through `:272`, which
executes only when `put_nowait` fails a **second** time at `:258`, immediately
after `:253` has just freed a slot. That needs a competing producer, and the app
has none: `_enqueue_flush` has exactly one call site in the whole repo and it is
under `_lock`, so it cannot race itself. The only other `put_nowait` is in
`reset_audio_temp_session`, which is called only from a repo-root regression
script, never from anything under `alpha/`. Two executed stress runs — 43 real
overflows single-threaded, **1901** across three concurrent callback threads —
drove the overflow branch nearly two thousand times and reached line `:272`
**zero times**, returning normally both times.

**What it does cause** is contention, not deadlock: the retention writer blocks
at `:566` while the ingest thread holds the lock, and the ingest thread discards
audio via drop-oldest (1901 dropped chunks in the 3-thread run). A backpressure /
data-loss characteristic, not a hang.

**Risk.** As shipped, none. The residual risk is future-facing: the code is one
edit away from a hard freeze. Wiring the already-written
`reset_audio_temp_session()` into a UI Start/Stop or session-restart path — which
is what that function exists for — introduces the second producer, and a lost
slot race then wedges the audio callback thread forever while holding `_lock`.

**Fix.** Delete the synchronous fallback at `:271-275`. By the time control
reaches it the item has already been counted in `_retention_drop_count` and
logged, so returning is consistent with the function's own contract
("Non-blocking enqueue. On overflow drop the oldest retention item only"). That
removes the only edge from inside the lock back into the lock, at zero
behavioural cost. **Do not** reach for `RLock` instead: it would silently
legitimise re-entry at all 16 `with _lock` sites and mask this class of bug
rather than remove it.

> ### Retraction
> An earlier pass in this review reported this item as **CONFIRMED — "deadlock
> reproduced"**. That was wrong, and wrong in the exact way this project keeps
> being wrong. The probe held `_lock` by hand and called the inner function
> directly. That proves the **edge** exists; it says nothing about whether
> production takes it. Reachability has to be established separately, by driving
> the real public entry point — which is what refuted it.

---

## 7. "The English lifecycle bypasses the commit authority" — REFUTED — ✅ the loss is now named (phase 6)

| | |
|---|---|
| **Verdict** | **REFUTED** as a bypass; a real but different defect survives |
| **Severity** | **MEDIUM** |
| **Importance** | **FIX-SOON** |
| **Where** | `alpha/transcription/deepgram_client.py:1741` |

**Issue as claimed.** The `except Exception` around the English utterance-
lifecycle block falls through to `_publish_final_transcript_segment`, which calls
none of `observe_identity` / `execute_pipeline_commit` / `apply_decision` /
`assign_canonical_record_id` / `accept_boundary_proposal` — so the single commit
authority is bypassed.

**Why that reads the architecture inside out.**
`_publish_final_transcript_segment` is not a rival commit path — it is the shared
publish **sink** that sits *upstream* of the authority, and the healthy lifecycle
path reaches it too (`utterance_lifecycle.py:3112` calls the same publisher). The
authority lives one hop further down: publish → `transcript_queue` →
`_display_transcript_item` → `_commit_transcript_item_to_store` →
`DuplicateProtectionMixin._display_transcript_item` at `main_window.py:8309`,
where the authority functions are called. Driving the forced-exception path shows
the authority **is** re-entered and **fails closed**: with the lifecycle dead
there is no `canonical_utterance_id`, `observe_identity` returns not-accepted with
`missing_identity_key`, and the function returns before
`execute_pipeline_commit`. Nothing escapes.

**What is really wrong.** The opposite direction: the utterance is **dropped**.
Zero ledger commits — and because the store write and the render both sit after
the rejection return, **no stored segment and no on-screen line either**. The one
half of the claim that survives is the logging asymmetry: both Japanese siblings
emit a structured event naming the fallback
(`JAPANESE_PATH_DETECTION_FAILED`, `JAPANESE_STABILIZER_INGEST_FAILED`, each with
an explicit `fallback=` field), while the English handler emits a bare `print()`.

**Risk.** No unauthorized commit and no ledger corruption — the invariant holds.
But if anything inside the lifecycle block ever raises, every affected English
utterance vanishes completely while the app looks healthy, and the persisted
evidence shows a generic `IDENTITY_REJECTION / missing_identity_key`
indistinguishable from every other cause. Requires the lifecycle block to raise,
which is itself abnormal, so this is latent rather than routine. **The
ship-blocking reading of this item should be withdrawn.**

**Fix.** Mirror the two Japanese siblings: alongside the existing print, emit
`jp_accuracy_log("ENGLISH_LIFECYCLE_INGEST_FAILED", reason=..., text_preview=...,
fallback="published_without_lifecycle_identity_expect_identity_rejection")`,
wrapped in its own `try/except/pass`. That makes the downstream rejection
attributable. **Do not** "fix" it by minting a synthetic
`canonical_utterance_id` so the fall-through can commit — that would defeat the
fail-closed identity gate this measurement just showed working.

---

## 8. Every DeepL placeholder key the project ships passes the Start check — ✅ FIXED (phase 4)

**Verdict: CONFIRMED** · Severity **MEDIUM** · Importance **FIX-SOON**
`alpha/config.py:109`

`get_deepgram_key_status()` compares the Deepgram key against
`PLACEHOLDER_API_KEYS` and a placeholder blocks Start with a sentence the
operator can act on. `has_deepl_api_key()` is `bool(DEEPL_AUTH_KEY or
DEEPL_API_KEY)` — a bare truthiness test that never consults that set. For
DeepL the set is dead code.

Driven against the real `alpha.config` and the real
`service_status.preflight_credentials()` — the only credential authority on the
Start path:

```
DEEPL=paste_your_key_here          has_deepl=True  preflight=[]
DEEPL=replace_with_your_key        has_deepl=True  preflight=[]
DEEPL=your_api_key_here            has_deepl=True  preflight=[]
DEEPL=your_deepgram_api_key_here   has_deepl=True  preflight=[]
DEEPL=your_deepl_api_key_here      has_deepl=True  preflight=[]   <- exists for no other purpose
DEEPL=your_deepl_auth_key_here     has_deepl=True  preflight=[]   <- what .env.example ships
```

Worth naming precisely: `PLACEHOLDER_API_KEYS` contains `your_deepl_api_key_here`,
and **nothing the project ships uses that spelling**. `.env.example:2` ships
`DEEPL_AUTH_KEY=your_deepl_auth_key_here`. So the DeepL entry is dead twice
over — never consulted, and the wrong string anyway.

**Risk.** Item 46's contract is to turn a missing, placeholder or rejected key
into a sentence a non-technical user can act on, *at Start*. For DeepL only the
empty case is honoured. Any non-empty-but-invalid key — placeholder, typo,
revoked, rotated — gets no signal at Start. It surfaces per segment mid-session
as `auth_failed`, `retryable=False`, and once the item-45 breaker trips the
indicator reads *"Translation degraded (provider failing, retrying in …)"*. The
operator is told the **provider** is failing, never that their key is wrong —
and only after enough segments have already been lost to trip the breaker. This
fires on exactly the `.env` edit the app's own error text tells the user to make.

**Fix.** Add `get_deepl_key_status()` mirroring the Deepgram one, add the
shipped spellings to `PLACEHOLDER_API_KEYS`, and emit a
`deepl_key_placeholder` problem from `preflight_credentials()`. Keep
`blocks_start=False` — the Deepgram/DeepL asymmetry is deliberate; the point is
the sentence, not refusing to start.

---

## 9. The installer's own key template passes both the build gate and the runtime check — ✅ FIXED (phase 4)

**Verdict: CONFIRMED** · Severity **MEDIUM** · Importance **FIX-SOON**
`installer/keys.local.ini.example:7`, `installer/build_installer.py:85`

The build template ships `deepgram = your-deepgram-api-key` (hyphens).
`read_keys()` rejects only an **empty** value — `missing = [n for n, v in (...)
if not v]` — so a placeholder compiles fine, and Inno's `WriteEnvFile`
(`alpha.iss:184`) writes it into `{app}pp\.env`.

And the runtime check misses it too. Measured on the real config:

```
hyphenated deepgram placeholder -> status: configured | preflight: []
```

So the one placeholder guard that *does* work for Deepgram does not know the
spelling its own build template ships. Of the four placeholder strings the
project ships across its two templates, exactly **one** is actually caught.

**Risk.** A build made from an unedited keys template installs cleanly,
launches, shows a green idle status and passes Start with no dialog — then fails
the Deepgram WebSocket handshake with a 401. The only thing standing between
that and a client is a human reading `keys loaded (deepgram 21 chars, …)` and
knowing a real key is 40. `build_installer.py`'s own docstring says a silently
empty key would "surface on the target machine, which is the worst possible
place for it" — a placeholder produces exactly that outcome, and the check
written to prevent it does not cover this case.

**Fix.** Two cheap independent guards: (a) in `read_keys()`, reject a value
matching a placeholder pattern, or enforce a length floor for a Deepgram key;
(b) add the hyphenated spellings to `PLACEHOLDER_API_KEYS` so the app blocks
Start with the accurate message rather than a 401.

---

## 10. The UI says "Stopped" 5 s in, while the deliverable has up to 66 s still to be written — ✅ FIXED (phase 3b)

**Verdict: PLAUSIBLE** · Severity **HIGH** · Importance **FIX-SOON**
`alpha/ui/main_window.py:10756`, `alpha/utils/stop_finalize_worker.py:1961`

`_stop_ui_watchdog_tick` force-restores the UI once
`(time.monotonic() - started) >= 5.0`, and `_restore_ui_after_stop_watchdog`
clears **both** guards that keep a new session out: `_is_finalizing` and
`_stop_finalize_started`. `toggle_listening` (`:10116`) has no other guard.

Measured mechanically from the step table and the invocation order:

| | |
|---|---|
| steps invoked by `_run_finalize_worker` | 20 |
| budget before `write_final_alpha` | **66.0 s** |
| budget for the whole worker | **71.0 s** |
| UI force-restore | **5.0 s** |
| window where Start is unblocked and the worker still runs | up to **66 s** |

The two big budgets are real, not padding: `drain_audio_queue` is 25 s and
genuinely spends it when there is queued audio still to send, and
`translation_worker_shutdown` is 16 s -- which is longest under exactly the
condition item 1 lives in, a slow DeepL with a backlog.

The trade-off itself is deliberate and the message is honest ("Stopped.
Diagnostics may still be saving."). What is not accounted for is that the
restore also re-enables **Start**, so a second session can begin while the
previous one has not yet written its transcript. Item 11 is what makes that
harmful rather than merely untidy.

**Why PLAUSIBLE and not CONFIRMED.** Every link in the chain is verified --
the constant, the two cleared flags, the absence of any other guard, and the
66 s budget computed from the code. What has NOT been done is driving a real
Start during a real finalize and showing the corrupted output. Per this file's
own rule, that makes it a hazard with a proven mechanism, not a demonstrated
bug.

**Fix.** Keep the 5 s UI restore -- it exists for a good reason -- but do not
let it clear the Start guards. Gate `toggle_listening` on the worker instead of
on the UI flag: `_stop_state["finalize_thread"].is_alive()`, or the existing
`stop_core_completed_event`. The button can say "Finishing previous session…"
for the remainder.

---

## 11. `SECOND_RUN_FOLDER_CREATION_BLOCKED` blocks nothing — ✅ FIXED (phase 3b)

**Verdict: CONFIRMED** · Severity **HIGH** · Importance **FIX-SOON**
`alpha/utils/troubleshooting_paths.py:700-712`

`rebind_all_runtime_writers` increments `_run_rebind_count`, and on any rebind
after the first it logs `SECOND_RUN_FOLDER_CREATION_BLOCKED`. Then it calls
`set_active_run_folder(run_folder)` **unconditionally** and closes the pending
writers.

Verified by walking the AST rather than by eye:

```
guard test  : int(_run_rebind_count) > 1
body exits  : NONE -- it only logs
set_active_run_folder at :712  inside the guard? False
```

**Risk.** Two costs, and the second is worse than the first.

The direct one: with item 10, a second Start during the finalize window
repoints every runtime writer at the new run's folder and force-closes the
pending ones while the previous run's worker is still writing. The old
session's remaining evidence -- and, in the window before `write_final_alpha`,
potentially its transcript -- lands in the wrong folder or hits a closed
writer.

The indirect one: **the event name asserts a guard that does not exist.**
Anyone reading a client's logs sees `SECOND_RUN_FOLDER_CREATION_BLOCKED` and
concludes the second rebind was prevented. It was not. That is worse than
having no log line at all, because it retires the question.

**Fix.** Decide which the name should be. If a second rebind is genuinely
wrong, `return` from the guard. If it is legitimate, rename the event to
something that describes what happened (`RUN_FOLDER_REBOUND_AGAIN`) and carry
the previous folder in the payload. Do not leave a name that says one thing
while the code does another.

---

## 12. Every rebind migrates the unbounded `_pending` pile — ✅ FIXED (phase 4)

**Verdict: CONFIRMED** · Severity **MEDIUM** · Importance **FIX-SOON**
`alpha/utils/troubleshooting_paths.py:683`, compounds item 4

`rebind_all_runtime_writers` runs `migrate_pending_files_to_run_folder`, and
item 4 established that `troubleshooting/runs/_pending/logs/*` is the one
evidence file nothing ever rotates or truncates **across** sessions.

Measured on this machine today:

```
japanese_accuracy.log   358 MB
freeze_guard.log         66 MB
async_debug.log          33 MB
                        ------
                        ~457 MB migrated on every rebind
```

A bare harness calling `rebind_all_runtime_writers` twice **did not finish in
120 seconds** and had to be killed -- which is also why item 11's behaviour was
settled from the AST rather than from that probe.

**Risk.** This is the second-order cost of item 4 that item 4 did not name: the
pile is not merely disk, it is latency on a path the user waits on. It grows
without bound, so the wait grows with it, and it is paid at run binding -- at
Start, and again on any rebind. A client several months into daily use pays a
proportionally longer one every session.

**Fix.** Item 4's fix already bounds the `_pending` files; do that first and
this shrinks with it. Additionally, migration should be incremental or skipped
for files above a size threshold -- copying a 358 MB log into a run folder is
not evidence collection, it is an accident.

---

## 13. The lifecycle's stale-session guard cannot fire — ✅ FIXED (phase 3b)

**Verdict: CONFIRMED** · Severity **MEDIUM** · Importance **FIX-SOON**
`alpha/transcription/utterance_lifecycle.py:1503-1506`

```python
session_id = self._session_id or str(
    getattr(self._host, "_live_session_id", "") or ""
)
if session_id and self._session_id and session_id != self._session_id:
    ... return IGNORE_DUPLICATE / "session_mismatch"
```

`session_id` is assigned `self._session_id` **whenever that is truthy**, so by
the time the comparison runs the two are the same value. And when
`self._session_id` is falsy, the `and self._session_id` term has already made
the guard False. Evaluated over every input shape:

```
self._session_id='S1'  host='S2'  -> session_id='S1'  guard fires? False
self._session_id=''    host='S2'  -> session_id='S2'  guard fires? False
self._session_id='S1'  host='S1'  -> session_id='S1'  guard fires? False
```

The `session_mismatch` branch is unreachable. The host's `_live_session_id` --
the only value that could disagree -- is consulted **only** when the lifecycle
has no session of its own, which is exactly when there is nothing to disagree
with.

**Risk, stated precisely.** The commit authority still fails closed: an
independent guard in `canonical_identity_registry.py:116` compares the
incoming session against the registry's own and rejects with
`IDENTITY_REJECTION / session_mismatch`. That one is a real comparison of two
independent values and does fire. So a stale event cannot reach the ledger.

What it *can* do is reach the lifecycle's own state first. `_ingest` proceeds
into its interim/final cases and updates `self._active`, so a final belonging
to a previous session can extend or replace the **current** session's active
utterance before anything downstream refuses the write. The visible result is
corruption of a live utterance rather than a bad ledger record.

This matters more given item 10: a second session can begin while the previous
one is still finalizing, which is precisely when a stale event is in flight.

Second cost, the same shape as item 11: the log will say the *registry*
rejected a session mismatch and never that the *lifecycle* accepted one, so a
reader cannot tell the first guard did nothing.

**Fix.** Compare against the host, which is the only value that can differ:

```python
host_sid = str(getattr(self._host, "_live_session_id", "") or "")
if host_sid and self._session_id and host_sid != self._session_id:
    ... reject
session_id = self._session_id or host_sid
```

Regression test: drive `_ingest` with `self._session_id` set and a different
`_live_session_id` on the host, and assert the decision is `IGNORE_DUPLICATE`
with reason `session_mismatch`. It must fail against the current code, which
returns a normal decision.

---

## Correction to item 1

Item 1 said the delivered translated transcript ends at the drop "because the
pane is read back on export". **The mechanism was misstated.** Checked while
auditing the commit authority:

* The **final Alpha output** is written from the frozen ledger, not a widget --
  `FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY` is `True` at runtime and
  `write_final_alpha_output_from_snapshot` (`run_artifacts.py:694`) goes
  through `get_frozen_snapshot`. That invariant holds.
* The user-facing **Export Transcript** action
  (`main_window.py:11843`) takes its translated section from
  `_get_translated_transcript_for_copy_export`, which reads
  `_translation_items_by_utterance` first and only falls back to the widget.
  That registry's `line_text` is written by `_clear_translation_loading_item`,
  which runs only when a translation actually reaches the UI.

So the **consequence stands** -- a stalled ordering gate means the later
translations never reach the UI, never get a registry entry, and are absent
from the exported file -- but the reason is the registry the stall starves,
not a widget read. Item 1 is fixed either way; the file should not carry a
wrong mechanism.

---

## Commit authority: three invariants checked, all holding

Recorded because "no finding" is a result, and an audit that reports only what
it disliked is not an audit.

| Invariant | Verdict |
|---|---|
| One commit authority | **Holds.** An AST sweep of every caller of the ledger's write API across `alpha/` finds exactly one: `apply_decision` from `execute_pipeline_commit` (`pipeline_commit_transaction.py:324`). |
| Export from the frozen ledger, never a widget | **Holds** for the final output -- see the correction above. |
| A revise cannot address a bad target | **Holds.** `_revise_record_unlocked` (`canonical_transcript_ledger.py:541`) raises `PipelineIntegrityError` when the target is missing, inactive or suppressed, so the self-referential shape of the old item 20b fails closed at the ledger even if a caller regresses. A `source_version` ordering guard sits beside it. |

One apparent second writer was checked and cleared: `suppress_record` called
directly from `accept_boundary_proposal` (`utterance_lifecycle.py:1187`). It is
not a bypass -- it quarantines a record the authority had already committed,
when identity binding afterwards failed, and it fails closed (`success: False`,
`quarantined: True`), recording even a failed quarantine rather than swallowing
it.

---

## 14. `rebind_all_runtime_writers` self-deadlocks, and the workaround that hides it is why `_pending` grows — ✅ FIXED (phase 4)

**Verdict: CONFIRMED** · Severity **HIGH** · Importance **FIX-SOON**
`alpha/utils/troubleshooting_paths.py:739-742` and `:343`

```python
# rebind_all_runtime_writers, :739
with _lock:
    _writers_rebound = True
    for writer_name in list(_writer_registry.keys()):
        rebind_runtime_writer(writer_name, run_folder)   # :742

# rebind_runtime_writer, :342
def rebind_runtime_writer(writer_name, run_folder):
    with _lock:                                          # :343  -- same lock
```

`_lock` is `threading.Lock()` (`:40`) -- confirmed non-reentrant at runtime.
The loop body therefore blocks forever the moment the registry has a single
entry, and the registry is populated by ordinary use: `get_log_path`
(`:418`) registers a writer on **every call**.

**Driven through the real entry point**, with the registry populated by a real
`get_log_path` and the `_pending` roots pointed at an empty tree so a slow copy
could not be mistaken for a hang:

```
rebind_all_runtime_writers returned within 25 s: False
  troubleshooting_paths.py:343  in rebind_runtime_writer       <- blocked on _lock
  troubleshooting_paths.py:742  in rebind_all_runtime_writers  <- holds _lock
```

A second thread is taken down with it -- `JapaneseAccuracyLogWriter` is blocked
at `:337 register_runtime_writer`, also waiting on `_lock`. So the deadlock
stops the logging writer as well as the caller.

**Why the app still starts.** `create_run_folder` (`:976-984`) reaches the
rebind only in an `else` branch. Measured at runtime,
`STARTUP_RECOVERY_MODE` and `EVIDENCE_SAFE_MODE` are both `True`, so Start takes
the other branch and logs `PENDING_WRITER_REBIND_DEFERRED_NON_BLOCKING`. The
deadlock is skipped, not fixed.

**This is the real cause of item 12.** Item 12 recorded that
`troubleshooting/runs/_pending/logs/*` is the one evidence file nothing bounds
across sessions, and treated that as missing rotation. The deeper reason is
here: the rebind that would move the writers **out of** `_pending` and into the
run folder is the deadlocking function, so it was disabled by a safe-mode flag,
and every session since has kept appending to the shared pending file. That is
how it reached 358 MB.

**Still reachable.** `preflight_upload_evidence` (`:1223`) calls the same
function with only a UI-thread guard, from `run_artifacts.py:1442` -- the
evidence-packaging path. A client asked to produce a diagnostic bundle can hang
there.

**Fix.** Take the lock once, not twice. Snapshot the registry keys under the
lock, release it, then call `rebind_runtime_writer` for each -- it takes the
lock itself and is written to be called unlocked:

```python
with _lock:
    _writers_rebound = True
    names = list(_writer_registry.keys())
for writer_name in names:
    rebind_runtime_writer(writer_name, run_folder)
```

Do **not** switch `_lock` to an `RLock`: this module has 35 `with _lock` sites
and that would legitimise re-entry at all of them rather than remove one bug.
Once this is fixed, the safe-mode deferral can be revisited -- and only then
does item 12's pile stop refilling.

**Regression test.** Populate the registry through the real `get_log_path`,
call `rebind_all_runtime_writers` in a thread, and assert it returns within a
few seconds. It hangs against the current code.

---

## Concurrency sweep: what else the scan found, and what it cleared

An AST sweep over all of `alpha/` -- 35 non-reentrant `Lock()` sites, 10
`RLock()`, 1 `Condition`.

| Question | Result |
|---|---|
| Non-reentrant lock re-taken from inside its own `with` block | 4 candidates; **1 real** (item 14). The others: `audio_temp_capture` (already refuted, unreachable), `transcript_store.clear()` (the inner call is `self._segments.clear()`, a list method the name-based scan matched), `_start_watchdog` (the inner call is `t.start()`, so the lock is taken on a *different* thread, which blocks only until the parent exits its block). |
| Two locks held at once, in inconsistent order | **None.** No function in `alpha/` nests one lock inside another, so classic AB/BA deadlock is structurally absent. |
| Unbounded queues | 4: `main_window.py:481` (`Queue()`), and `SimpleQueue()` at `evidence_jsonl.py:21`, `multidomain_gate_evidence.py:41`, `ui_event_bus.py:57`. `SimpleQueue` **cannot** take a bound, so those three are unbounded by construction and would need a different type to fix; none is on the audio path. Not filed as findings -- no growth was measured on any of them. |

The first pass of this scan produced ~40 hits by asking whether a function
takes the lock *anywhere* and calls a lock-taking function *anywhere*. That is
the same loose test that made the `audio_temp_capture` deadlock a false
positive. Requiring the call to be lexically inside the `with` block cut it to
4, and driving each cut it to 1.

---

## 15. `TK_CALL_SITE_SAFE` counts a substring and calls it safe — ✅ FIXED (phase 3b)

**Verdict: CONFIRMED** · Severity **MEDIUM** · Importance **FIX-SOON**
`alpha/utils/tk_thread_guard.py:198-220`

```python
def scan_tk_call_sites(project_root=None) -> dict[str, int]:
    """Static scan of .after( in allowed modules — diagnostic only."""
    patterns = ("alpha/ui/main_window.py",
                "alpha/transcription/japanese_sentence_assembler.py")
    safe = 0
    for rel in patterns:
        if path.exists():
            safe += path.read_text(...).count(".after(")
    ...
    jp_accuracy_log("TK_CALL_SITE_SCAN_COMPLETED", safe_count=safe)
    jp_accuracy_log("TK_CALL_SITE_SAFE", count=safe)
```

Run against the shipped tree it returns `{'safe': 31, 'refactored': 0}`.

Nothing about those 31 was checked. `safe` is `str.count(".after(")` over **two**
files — it counts occurrences in comments and docstrings as readily as in code,
looks at no other module, and applies no test of any kind before writing an
event named `TK_CALL_SITE_SAFE`.

**Risk.** This is item 11's shape again, in the evidence stream rather than in
control flow: a name that asserts a property nothing established. A reader
working through a client's `japanese_accuracy.log` finds
`TK_CALL_SITE_SAFE count=31` and concludes the Tk call sites were audited and
cleared. They were counted. The cost is not a crash — it is that the question
gets retired without being asked, which is how the WASAPI reader sat unreported
behind a scan that could not see it (item 3).

**Fix.** Either make it a real check or make the name honest. The cheap honest
version is to rename the events to `TK_AFTER_CALL_SITE_COUNT` and drop the word
safe. The useful version is the sweep this audit ran: walk the AST from each
real thread target and report widget mutations reachable without a marshal
hop — which is a check, and which returns a defensible answer.

---

## 16. Four recurring `after` jobs are never cancelled, and `_on_close` cancels nothing — ✅ FIXED (phase 6; only fully since 26.5.19 — see item 21)

**Verdict: CONFIRMED** · Severity **LOW** · Importance **BACKLOG**
`alpha/ui/main_window.py`, `_on_close` at `:11031-11172`

`main_window.py` schedules 31 `after`/`after_idle` calls: 16 store the id so it
can be cancelled, 15 discard it. Of the 13 stored job attributes, four are never
passed to `after_cancel` anywhere in the module:

| Job attribute | Cancelled by |
|---|---|
| `_jp_pipeline_hb_after_id` | **never** |
| `_transcript_ui_batch_after_id` | **never** |
| `_ui_event_bus_after_id` | **never** |
| `_ui_queue_defer_after_id` | **never** |

and `_on_close()` contains no `after_cancel` call at all and names none of the
job attributes.

All four are self-rescheduling loops, so each keeps re-arming until the
interpreter goes away.

**Risk, stated small because it is small.** Tk discards pending `after`
callbacks when the interpreter is torn down, so at a clean exit this is
tidiness rather than a fault. The observable form is the
`invalid command name "..." while executing ("after" script)` noise this
project's own test runs produce on teardown. What has NOT been measured is a
user-visible failure, so this is filed LOW and BACKLOG rather than as a bug.
It is worth fixing mainly because a close path that cancels nothing gives the
next person no place to put a cancellation that does matter.

**Fix.** Give `_on_close` the same treatment `_stop_ui_loops` already gets:
cancel each stored job id, guarded, before teardown.

---

## UI threading: the sweep found nothing reachable, after its own blind spot was fixed

`main.py:324` installs `tk_thread_guard`, which patches `after` and
`after_cancel` and reroutes a background-thread `after` through the UI event
bus. It does **not** patch `insert`, `delete`, `configure`, `see`, `grid`,
`pack` or `destroy`, so those are unguarded and had to be checked directly.

An AST sweep took every real background-thread root in `alpha/` -- the targets
of `threading.Thread(target=...)` and `SupervisedThread(...)`, 20 of them -- and
looked for widget mutations reachable within four call hops without passing
through `after`, `after_idle`, `_run_on_ui_thread` or an event-bus publish.

**Result: none.**

That is the corrected answer, and the correction is worth recording. The first
run reported seven hits, all through
`worker() -> _finish_start_listening -> ...` in `main_window.py`. Every one was
a false positive: the real call is
`self._run_on_ui_thread(lambda: self._finish_start_listening(error))` at
`:10341`, and the scan only recognised a marshal when the target was passed as a
bare name, not when it was wrapped in a lambda. Teaching it to treat everything
inside a marshal call's arguments -- lambda bodies included -- as marshalled
took the count to zero.

Scope of that result, stated plainly: same-module reachability, depth four,
receivers identified by name shape (`*_box`, `*_label`, `*_button`, …). A
cross-module path, or a widget held in a variable that does not look like one,
would not be seen.

---

## 17. A raise inside the continuity-hold tick discards the buffered sentence silently — ✅ FIXED (phase 6)

**Verdict: CONFIRMED** (mechanism driven; trigger abnormal) · Severity **MEDIUM**
· Importance **FIX-SOON**
`alpha/transcription/japanese_sentence_assembler.py:3178-3193`

```python
except Exception as exc:
    log_exception(exc, source="continuity_hold_tick", ...)
    if JAPANESE_CONTINUITY_ASSEMBLER_SAFE_MODE:
        self._buffer = None          # the buffered sentence, gone
        self._cancel_timer()
    return True                      # ...and the tick reports success
```

`JAPANESE_CONTINUITY_ASSEMBLER_SAFE_MODE` is `True` at runtime. Driven on a real
`JapaneseContinuityAssembler` with a real buffered sentence, forcing
`_execute_continuity_hold_locked` to raise:

```
returned              : True   <- reports success
buffer after          : None
spoken text survived? : False
events emitted        : ['ASYNC_LOG_EMERGENCY_WRITE']
```

The only event is the crash logger's own plumbing. **Nothing names the content
loss** -- no `jp_accuracy_log` event, no counter; `grep` for a discard event in
that module returns nothing.

**What makes this a defect rather than a policy.** The sibling handler does it
correctly. `_handle_assembler_exception` (`:1193`) faces the same situation and
emits `ASSEMBLER_EXCEPTION_CAUGHT`, clears the buffer, and then **recovers the
fragment and re-commits it**. Two exception paths in one class, one preserving
the speech and naming the event, the other dropping both.

**Risk.** A Japanese sentence that was spoken, captured and buffered never
reaches the transcript, the ledger or the delivered file, and the run's own
evidence gives a reader no way to know a sentence went missing -- only that an
exception happened somewhere in a hold tick. Trigger is abnormal: it needs
`_execute_continuity_hold_locked` to raise. So the mechanism is proven and the
frequency is not, which is why this is MEDIUM rather than HIGH.

**Fix.** Mirror the sibling. Before clearing, emit
`jp_accuracy_log("CONTINUITY_HOLD_TICK_DISCARDED_BUFFER", text_preview=...,
exception_type=...)` and bump a counter, and hand the text to the same recovery
`_handle_assembler_exception` uses rather than dropping it. If dropping really
is the intended policy for this path, it still has to be named -- an unnamed
loss is the thing that makes a live report undiagnosable.

---

## 18. The Stable reconstruction paired two differently-sized streams by index — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — found by a live run, root-caused, fixed, and the failing run re-reconstructed |
| **Severity** | **MEDIUM** (diagnostics; the transcript is unaffected) |
| **Importance** | **FIX-SOON** |
| **Where** | `alpha/utils/persisted_run_evidence.py:275` (pairing), `:394` (the raise) |

**How it surfaced.** The 2026-09-14 test video ended at
`completed_pending_evidence_package`:

```
THREE_STAGE_FINALIZER_EXCEPTION  step=assembler_stage
PersistedEvidenceReconstructionError: Unresolved revision targets:
    ['missing_record_id_event_index_27', 'missing_record_id_event_index_28']
```

**Mechanism.** `reconstruct_active_stable_records` paired assembler events to
stable commits positionally:

```python
commit = commit_by_index[i] if i < len(commit_by_index) else {}
meta   = commit.get("assembler_metadata") ...
commit_rid = meta.get("revision_target_id") or meta.get("canonical_record_id")
```

`i` indexes the *events*; the list is the *commits*. They are different lengths
and different memberships — `no_op` and `suppress_candidate` events never become
commits, and they are skipped at `:281-290`, **after** this pairing is computed.

Measured on that run: **30 events (4 `suppress_candidate`) against 26 usable
commits.** Two distinct effects, and the quieter one is worse:

* **Silent:** every event after the first suppressed one paired with the **wrong**
  commit. A synthetic reproduction of the same shape loses a record entirely —
  `canon-000003` came back `None`.
* **Visible:** the trailing two events indexed past the end, resolved to `{}`,
  and were reported as having no record id.

**They all had one.** Every committed event carries its record id in its own
`commit_reason` (`"|canonical_record_id=canon-000022|transaction_id=…"`), and
**0 of 26 carried it as a top-level field** — which is why reading across to the
commit looked necessary. It is not; the event is self-describing.

**Risk.** Diagnostics, not content. That run's `Alpha_output_FINAL.txt` was
written and its 23 canonical records are intact. But when the finalizer raised,
validation, the health timeline, the memory trend, the artifacts index and the
upload package were all skipped — so a client sending a support bundle sends an
incomplete one, and the run reports a status that understates its own success.

**Pre-existing, not a phase regression.** The same exception appears in 26.5.3
runs from 2026-09-02, before phases 4-7.

**Fix.** The event is now the source of truth for its own id
(`_event_record_id`, which splits `commit_reason` on `|` and matches the exact
key — a looser `split("canonical_record_id=")[1]` would return
`transaction_id=…` for a suppressed tail and invent an id that never existed).
The commit is looked up **by that id** purely as a lineage fallback. No index
survives. Also removed the dead `if events and not events: pass` at `:259`.

**Proof.** `tests/test_item18_reconstruction_pairing.py` — 6 of 10 failed
pre-fix, including the silent-mispairing case. Fixtures rebuild the shape
synthetically rather than pointing at the run folder, because
`troubleshooting/runs/` is gitignored and retention-pruned and a test whose
guarantee disappears with its evidence is a pattern this project has already
been bitten by. The failing run folder itself was then re-reconstructed:
`unresolved_revision_targets: []`, `reconstruction_completed: True`,
**23 active records against 23 canonical ledger records**, `records_without_lineage: 0`.

---

## 19. A device swap mid-sentence deleted the words already spoken — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — found by the 2026-09-14 full-code audit, driven on the real assembler before and after |
| **Severity** | **HIGH** (silent content loss, in exactly the scenario phase 7 exists for) |
| **Importance** | **FIX-NOW** |
| **Where** | `alpha/transcription/japanese_sentence_assembler.py`, `flush()` — the incomplete-tail branch |
| **Introduced by** | phase 7, `e288c9f`, package 26.5.16 — **my own change** |

**Issue.** Phase 7 made a device swap a deliberate utterance boundary by calling
`flush(DEVICE_SWAP_BOUNDARY_REASON)`, and correctly kept it away from the latched
`_stop_boundary_active` flag. The rest of `flush()` was not read. Further down:

```python
incomplete, inc_reason = looks_incomplete_japanese_fragment(text)
if incomplete or reason == "stop_listening":
    self._flush_locked("stop_flush_incomplete_tail", stop_incomplete=incomplete, ...)
```

`incomplete` **alone** routed the buffered fragment to the stop-tail path,
whatever the reason. Stop-tail suppression then fired (`STOP_TAIL_CLEANUP_ENABLED`
and `SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA` both True), so the fragment was
classified `intentionally_suppressed` and never written — and marked synthetic,
since `_is_synthetic_stop_only_ingress` treats that reason as stop-only ingress.

**Proof.** Driven before the fix, with `is_listening` True, a swap and a Stop were
indistinguishable: both emitted `STOP_TAIL_CANDIDATE_SUPPRESSED`,
`CANONICAL_LEDGER_SUPPRESS_CANDIDATE` and
`SUPPRESSED_STOP_TAIL_CANDIDATE_WRITE_SKIPPED`. After the fix the swap's event
stream is identical to a complete fragment's and a normal commit's, and Stop
still suppresses its tail.

**Why phase 7's tests missed it.** Every one of them flushed an **empty buffer**.
They pinned the flag and the merge gate — both right — and never asked what
happens to a sentence in flight at the moment of the swap, which is the only case
that matters. A test looser than the claim, the recurring failure in this repo.

**Harness limit, stated rather than glossed.** With the minimal host the suite
uses, a normal commit reaches the commit authority and fails closed with
`IDENTITY_REJECTION` (no utterance identity on that host). So the tests pin the
**routing** — never the stop-tail path, never suppressed — and do not claim an
end-to-end write to the transcript.

**Fix.** A swap commits what arrived as an ordinary line and returns before the
incomplete-tail branch. Tests: `test_a_swap_never_takes_the_stop_tail_path.py`,
4 of 7 failing pre-fix; the other 3 are guards (Stop still suppresses; an empty
swap stays a no-op; an idle crash still records not-listening).

---

## 20. Crash forensics recorded every session as not listening — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — scan, then code read |
| **Severity** | LOW (diagnostics) |
| **Importance** | BACKLOG |
| **Where** | `alpha/utils/crash_guard_log.py:80`, `_host_context` |

**Issue.** `ctx["listening"] = bool(getattr(host, "listening", False))`. Nothing
in the app assigns `listening`; the attribute it maintains is `is_listening`. So
every crash context ever written said the session was idle — including crashes
mid-meeting, which is exactly when a reader relies on that field.

**How it was found.** A scan for `getattr` reads of names nothing in the app ever
defines — the same bug class as the phase 7 seam measurement that briefly read a
mixer the worker never published. 13 candidates: 5 library attributes, the rest
dead fallbacks that still yield correct values, and this one.

**Fix.** Read `is_listening`. Fixed in 26.5.18 alongside item 19.

---

## 21. A close during a meeting silently dropped item 16's cancellation — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — driven in production shape, before and after |
| **Severity** | MEDIUM (a shipped fix was a no-op on the most common close path) |
| **Importance** | FIX-SOON |
| **Where** | `alpha/ui/main_window.py` `_on_close`; `alpha/utils/tk_thread_guard.py` `guarded_cancel` |
| **Corrects** | item 16's "FIXED", which only ever held for a close with no session running |

**Issue.** `_on_close` during a session called `_begin_graceful_stop()` and then
started a background thread, `WindowCloseWait`, which called
`_shutdown_and_destroy()` **on that thread** — on the normal success path, not only
the timeout. That shutdown cancels the session loops and item 16's four
app-lifetime jobs. Off the UI thread every cancel went through `guarded_cancel`,
which counted, logged and **returned without cancelling**, while its sibling
`guarded_after` rerouted. The thread was also left stuck inside the marshalled
`destroy()`.

> **Retraction.** The gap audit first reported that this path **left the window
> open**. That was **wrong**. The probe behind it blocked the main thread in
> `join()` instead of running `mainloop()`; in production the main thread is in
> its loop, tkinter's threaded Tcl marshals the cross-thread `destroy()`, and the
> window closes. Kept here, struck, rather than deleted, because the wrong claim
> was published and a probe that doesn't run the event loop will reproduce it.

**Proof, production shape** (real guard, UI thread registered, main thread in
`mainloop()`):

| | Before | After |
|---|---|---|
| Shutdown ran on | `WindowCloseWait` | `MainThread` |
| Recurring ticks after item 16's cancel | **8** | **0** |
| Threads left behind | stuck `WindowCloseWait` | none |

**Fix.** The thread only existed to wait without blocking the UI, so it is replaced
by `_poll_window_close_ready`, a main-thread `after` poll that never sleeps and
runs the shutdown on the UI thread. Rejected: marshalling the shutdown back with
`_run_on_ui_thread` — from a worker that posts to the UI event bus, and if graceful
stop has already stopped the bus pump the close is never delivered, which would
create the very hang the retracted claim described. `guarded_cancel` now reroutes
like `guarded_after`, skipping an empty id (tkinter raises on `after_cancel("")`).
Tests: `test_closing_during_a_meeting_shuts_down_on_the_ui_thread.py`, 7 of 9
failing pre-fix.

**The fix shipped a regression, fixed in 26.5.20.** Reviewing 26.5.19 after it
shipped: moving the wait onto the UI thread also moved the timeout branch's
`autosave_partial_artifacts_background` there, and both writers inside it begin
with `guard_ui_thread_blocking_call`, which **refuses on the UI thread**. So in
26.5.19 a close whose stop never finished saved no partial transcript and no
partial index — only the crash-safe index, which has no guard. The old
`WindowCloseWait` thread passed that guard. The 26.5.19 tests never looked at the
timeout branch's writes, only at which thread shut down.

Driven in production shape (real Tk mainloop, real guard, UI thread registered,
real autosave wrappers, file writers recorded):

| | 26.5.19 | 26.5.20 |
|---|---|---|
| Partial transcript written | **no** | yes, on `WindowCloseTimeoutAutosave` |
| Partial index written | **no** | yes |
| Writes finished before shutdown | — | yes (0.41 s, shutdown 0.50 s) |
| Shutdown ran on | `MainThread` | `MainThread` |

Fix: only the writing moves off the UI thread. The timeout branch starts
`_write_window_close_timeout_artifacts` on its own thread and the same UI-thread
poll waits for it, bounded by `WINDOW_CLOSE_AUTOSAVE_WAIT_S` (5 s) so a hung writer
cannot hold the window, then shuts down on the UI thread. Waiting matters as much as
the thread: shutting down first ends the process, and the daemon writer with it.
Five tests in `TheCloseTimeoutAutosaveReallyWritesTest`, three failing against
26.5.19; the other two guard that the shutdown stays on the UI thread and that the
wait never blocks it.

---

## 22. A close ended the process while the Stop worker was still finalizing — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — driven end to end before and after the fix (first recorded as PLAUSIBLE, before it was driven) |
| **Severity** | HIGH — the deliverable itself can be lost: the worker has up to 66 s of step budget before `write_final_alpha` |
| **Importance** | FIX-NOW |
| **Where** | `main_window.py` `_stop_ui_watchdog_tick` / `_restore_ui_after_stop_watchdog`; `stop_finalize_worker.py` flag clear before the tail of finalize |
| **Pre-existing** | yes — the `WindowCloseWait` thread read the same flags; items 21 and its regression fix did not change this |

**Issue.** The close wait decides "the stop has finished" from `_is_stopping` /
`_is_finalizing`. Those are UI-state flags, not a finalize-done signal, and two
things clear them while the daemon `StopFinalizeWorker` is still working:

1. `_begin_graceful_stop` starts the stop UI watchdog, which after **5 s** calls
   `_restore_ui_after_stop_watchdog(timed_out=True)` and clears both flags
   regardless of the worker. The close poll then logs
   `WINDOW_CLOSE_SAFE_STOP_COMPLETED`, shuts down, `mainloop()` returns, and
   `main.py` exits with nothing joining the worker. In practice this also makes the
   12 s timeout branch unreachable unless the watchdog itself stalls.
2. On the normal path the worker clears both flags *before* its alias sync and seal
   verification, and before `stop_core_completed_event` is set.

**Evidence.** 26.5.3 run `20260902-112941` (a Stop, not a close — same watchdog):
`STOP_UI_FORCE_RESTORE_AFTER_TIMEOUT` at 11:31:42.224; the worker then carried on
through `FINAL_EXPORT_LOCK_STARTED/COMPLETED`, `LATEST_INDEX_FINAL_EXPORT_LOCK_FIELDS_UPDATED`
and two alias syncs to `STOP_FINALIZE_COMPLETED` at 11:31:43.458 — 1.23 s after the
flags said done. The seal itself landed before the restore in that run. Across the
evidence tree, `STOP_UI_FORCE_RESTORE_AFTER_TIMEOUT` appears in 42 log files and
`STOP_UI_WATCHDOG_CORE_COMPLETED_DETECTED` in 3.

**Wider than first recorded.** The commonest close there is — click Stop, then close
the window — never waited at all. `_on_close` only waited for an `is_listening`
session, so after Stop it fell through to the idle close; before the 5 s restore
too, because with `_is_stopping` still True it set `_window_close_pending`, wrote the
partial snapshot, found `is_listening` False and fell through anyway.

**Proof, end to end** (real `_on_close`, `_begin_graceful_stop`, `begin_stop_from_ui`,
stop UI watchdog and close poll; real Tk mainloop and thread guard; only the worker
body faked, writing its deliverable at 7 s — inside the real budget):

| Scenario | Before | After |
|---|---|---|
| Close mid-meeting | shutdown 5.29 s, worker alive, **deliverable lost** | shutdown 7.84 s, after the worker, deliverable written |
| Stop, then close at 2 s | shutdown 2.05 s, **deliverable lost** | shutdown 7.72 s, deliverable written |
| Stop, then close at 6 s | shutdown 6.06 s, **deliverable lost** | shutdown 7.83 s, deliverable written |

Field: of 2058 recorded stops, 208 took longer than 5 s; the slowest took 11.8 s.

**Fix (26.5.21).** The close waits on `finalize_in_progress()` — the same gate Start
already used for this reason — as well as on the flags, through
`_stop_worker_still_finalizing()`. `_on_close` waits that way when Stop was clicked
first, without starting a second stop. `WINDOW_CLOSE_WAIT_S` rises from 12 s to 30 s,
since a cap a slow stop reaches kills its worker just the same; the poll still ends
the moment the worker finishes, and a second close click still force-closes.
Tests: `test_closing_waits_for_the_finalize_worker_not_the_ui_flags.py`, 11 tests,
7 failing pre-fix.

---

## 23. One keyterm rejection turned keyterms off until restart, and "400" was a substring — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — driven through the real `_deepgram_on_error`, `_start_listening` and `_build_deepgram_url` |
| **Severity** | MEDIUM (keyterm accuracy lost silently for every later meeting; a transient error could end the meeting) |
| **Importance** | FIX-SOON |
| **Where** | `alpha/transcription/deepgram_client.py` `_deepgram_on_error`; `alpha/ui/main_window.py` `_start_listening` session reset |
| **Found** | gap audit 2026-09-14 (bug #2); two more defects in the same branch found while writing its tests |

**Issue — four defects in one error handler.**

1. **The latch.** A keyterm rejection set `_jp_keyterms_fallback_used = True` so the
   reconnect could succeed without keyterms. Nothing ever set it back — not Start,
   not a successful connect — so every later meeting ran without the business-term
   keyterms until the app was restarted. The log line says "retrying *once*".
2. **"400" was a substring.** The fallback, the recoverable flag and the decision to
   **stop listening** all tested `"400" in str(err)`. websocket-client formats a failed
   handshake as `Handshake status <code> <reason> -+-+- <headers> -+-+- <body>`, so a
   503's text carries Deepgram's `dg-request-id`, whose hex can contain "400", and
   other error text can carry such a number. That turned keyterms off, and once they
   were off the next such error **ended the meeting** ("Listening has been stopped")
   instead of reconnecting — against the continuity requirement.
3. **The same substring test for the auth flag** (item 47): a 503 whose request id
   contains "401" or "403" showed the operator "Key rejected" for a working key until
   the socket next opened.
4. **A NameError since the first commit.** The fallback branch logged
   `len(JAPANESE_KEYTERMS)`, a name defined nowhere. It raised right after setting
   the flag and before the print and the reconnect; websocket-client logged it and
   moved on. Found because the new tests errored on it before reaching their own
   assertions — so the NameError was fixed first and the tests re-run, to prove each
   remaining failure was its own defect and not that one.

**Fix (26.5.22).** `_deepgram_handshake_status()` reads the real status — the
exception's `status_code`, or the `Handshake status NNN` text for an error that arrived
as text only — and drives all three decisions. Start clears
`_jp_keyterms_fallback_used` in the per-session reset that already clears
`_dg_auth_failed`, so a rejection lasts one meeting. The log reports the keyterms
actually being sent. Tests: `test_keyterm_fallback_is_per_meeting_and_400_means_status_400.py`,
8 tests: against the pre-fix tree 6 errored on the NameError and 2 failed; with only
the NameError fixed, 5 failed on their own defects; 3 are guards (a genuine 400 still
falls back once, then stops; a text-only 400 is still recognised).

**Not changed:** `alpha/translation/deepl_client.py` classifies DeepL errors with the
same kind of substring test (`"400" in msg`, `"403" in msg`). Different subsystem and
retry semantics, and it checks exception types first; recorded, not fixed.

---

## 24. The lifecycle owner kept every decision row of every meeting in memory — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — measured through the real `on_interim` and `reset_for_session` |
| **Severity** | LOW-MEDIUM (unbounded memory growth for the life of the process) |
| **Importance** | BACKLOG, fixed with 23 |
| **Where** | `alpha/transcription/utterance_lifecycle.py` `UtteranceLifecycleOwner` |
| **Found** | gap audit 2026-09-14 (bug #3) |

**Issue.** `_record_decision` appends one dict per decision to `_events`. Nothing ever
removed one: the owner is a process singleton, and `reset_for_session` — run at every
Start — zeroed the stats but not the list. Nothing in the app reads it (`events()` has
no caller); the record that matters is the event log file. Before: 300 decisions gave
300 rows, and after the session reset all 300 rows of the previous meeting were still
there; tracemalloc measured 627 bytes a row.

**Fix (26.5.22).** `_events` is a `deque(maxlen=LIFECYCLE_EVENTS_KEPT)` (1000, newest
last), cleared in `reset_for_session`. Re-measured on the real singleton: 5000 decisions
keep 1000, the reset clears to 0, the next meeting caps at 1000. Tests:
`test_lifecycle_decision_rows_do_not_pile_up.py`, 3 tests, 2 failing pre-fix; the guard
checks the bound drops the oldest rows, not the newest.

---

## 25. A close that waits for the transcript looked finished, inviting the click that kills it — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — driven through the real close, watchdog restore and listen-button code |
| **Severity** | MEDIUM (a second close click force-closes and kills the Stop worker mid-write) |
| **Importance** | FIX-SOON — it protects item 22's fix |
| **Where** | `alpha/ui/main_window.py` close poll, `toggle_listening`; `alpha/ui/strings.py` |

**Issue.** Item 22 made the close wait for the Stop worker, up to 30 s. A second click
still force-closes at once. But closing mid-meeting showed "Finalising…", and five
seconds later the stop UI watchdog showed "Stopped. Diagnostics may still be saving."
and re-enabled Start — a window that looks done and has not closed. Closing after Stop
changed nothing on screen.

**Fix (26.5.23).** While a close waits, the status line reads "Saving transcript —
closes automatically" (Japanese: 文字起こしを保存中 — 自動で閉じます) and the listen buttons
stay disabled, re-applied on every close-poll tick because the watchdog restore, status
events and resizes overwrite them. `toggle_listening` ignores Start while a close is
pending. Measured on the real status strip: the text fits in both languages at
800–1280 design px, like the longest status text already shown (the probe was first
shown to report an oversized planted text). Tests:
`test_closing_tells_the_user_the_transcript_is_saving.py`, 8 tests, 6 failing before.

---

## 26. After a monitor move, widgets hidden with `grid_remove()` came back on screen — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — reproduced on the real `AlphaApp` through CustomTkinter's own DPI-change path, before and after |
| **Severity** | MEDIUM (the compact layout broken by an open menu; a phantom "Show Transcript" that hides the transcript) |
| **Importance** | FIX-SOON — the unexplained half of the external-monitor report |
| **Where** | CustomTkinter 5.2.2 `CTkBaseClass`; fixed in `alpha/ui/ctk_grid_remove_fix.py` |
| **Record** | `TRANSCRIPT_PANE_ROOT_CAUSE.md` §7 |

**Issue.** CustomTkinter replays each widget's last geometry call on a DPI change, to
re-scale padding, and forgets it on `grid_forget`/`pack_forget`/`place_forget` — but
not on `grid_remove`. A widget hidden with `grid_remove()` came back when the window
moved to a monitor at another scale, and no layout pass re-hid it. Measured across 8
layouts: 22 widgets changed mapped state — in compact layout the closed hamburger menu
opened by itself with its nine controls, and at every width "Show Transcript" appeared
beside "Hide" (item 81's field report of both buttons at once).

**Correction on the way in.** This was first listed as pending because
`TRANSCRIPT_PANE_HANDOVER.md` said OPEN. That file was stale: the original bug was closed
on 2026-08-25 by 91d/91e and `TRANSCRIPT_PANE_ROOT_CAUSE.md` replaced it the same day;
`0e72529` later committed the untracked hand-over as "still-OPEN". The hand-over is now
marked superseded, its old status struck rather than deleted.

**Fix (26.5.24).** `grid_remove` forgets the replay record, exactly as `grid_forget` does,
installed once at `main_window` import. After: 0 widgets change state across a move;
a widget hidden during a move comes back with the same geometry as one that never moved
(5 of 6 identical; the sixth's 1 px is identical without the fix); visible widgets still
re-scale; `grid()` with no arguments still restores. Tests:
`test_hidden_widgets_stay_hidden_across_a_monitor_move.py`, 8 tests, 5 failing before.

**Limit.** The same simulation does not reproduce the original `content_wrapper` cause on
pre-91d code, so that closure still rests on 91d's injected tests; nothing here is
confirmed on a physical 100 % monitor.

---

## 27. At 100 % scaling, hiding a reading pane did not re-flow the grid until a resize — ✅ FIXED

| | |
|---|---|
| **Verdict** | **CONFIRMED** — reproduced on the physical 100 % monitor and in plain tkinter, before and after |
| **Severity** | MEDIUM (on the client's monitor every Hide/Show left the panes wrong until the window was dragged) |
| **Importance** | FIX-NOW — reported live on 26.5.24 |
| **Where** | `alpha/ui/main_window.py` `_build_content_column`, `CONTENT_COLUMN_SEED` |
| **Record** | `TRANSCRIPT_PANE_ROOT_CAUSE.md` §8 |

**Issue.** Tk 8.6's grid re-arranges a master after a slave is added or removed only if
the master's new requested size is more than 1 px in both directions. The reading panes
were 1×1 frames with `grid_propagate(False)`; at 150 % CustomTkinter made them 2 px, at
100 % they stayed 1 px, `content_wrapper` requested (9, 1), and Hide/Show waited for a
resize. From the app's own snapshot on the external monitor: Hide left the translation
pane at 583 px of ~850 until a drag.

**Proof.** On the physical 100 % monitor (attached to the dev machine), after a real
window move: after Hide and a second of idle, column 0's bbox was 9 px and the pane 583;
a 1 px resize made it 851/843. Plain tkinter reproduced the 1 px condition with no
CustomTkinter at all. Identical on 26.5.23.

**Fix (26.5.25).** `CONTENT_COLUMN_SEED = 4` — at least 2 device px down to CTk's 0.4
scaling floor, still small enough that the weights decide the split. On the physical
monitor: Hide 583 → 842 px, Show 582/253 (70/30), the pane reassert guard 1 → 0 per Show.
Tests: `test_reading_grid_reflows_at_100_percent_scaling.py`, 6 tests (Hide in columns
and stacked, and the seed check fail before; 70/30 and the laptop are guarded).

---

## Japanese assembler: three hunts that came back empty

| Hunt | Result |
|---|---|
| **Unbounded buffer growth over a long session** | **None.** The assembler holds one `Optional[dict]` buffer, not a growing list, and it is bounded three ways: `JAPANESE_CONTINUITY_MAX_BUFFER_CHARS = 100`, `JAPANESE_CONTINUITY_MAX_PARTS`, and `JAPANESE_CONTINUITY_MAX_HOLD_MS = 8000`, all enforced in `_check_emergency_commit`. An AST pass for data containers grown without a trim, clear or length check found none in any of the four modules. |
| **A boundary proposal that can never be accepted** | **None found.** The quarantine path looked like a candidate -- `_schedule_quarantine_drop` (`:2127`) latches `_quarantine_drop_scheduled = True` and depends on `language_pipeline_worker` to run the drop that clears it. But that worker's loop has handlers at `:201` and `:206` that both stay in the loop, so it cannot die from an exception, and the fixed audit tool does not report it. The flag is cleared at `:2005` and `:2009`. |
| **The item-94 shape** (a guard testing state whose only write is below its own return) | 7 hits, **all benign**. Four are idempotency or emptiness guards (`_schedule_quarantine_drop`, `_drop_expired_quarantine_locked`, and the two `install_japanese_stabilizer_hooks` hook guards); two are the bounded commit-gate breaker added for item 94 itself. |

The one-way-flag scan's single hit, `JAPANESE_FINAL_STABILIZER_ENABLED`, is a
module-level constant, not runtime state.

---

## Coverage — all six areas now audited

The original pass left five subsystems unreached because the fan-out died twice
on the session token limit. They were then audited one per session, inline.

| Area | Status |
|---|---|
| Config + startup | audited (original pass) — items 8, 9 |
| Stop / finalize internals | audited (phase 3) — items 10, 11, 12 |
| Commit-authority internals (`utterance_lifecycle`, `pipeline_commit_transaction`) | audited (phase 3) — item 13, three invariants confirmed holding, and a correction to item 1 |
| Global concurrency sweep | audited (phase 3) — item 14; lock-ordering inversions structurally absent |
| UI threading (worker threads reaching Tk widgets, `after()` lifetime) | audited (phase 3) — items 15, 16; **no** unmarshalled widget mutation reachable |
| Japanese assembler + stabilizers | audited (phase 3) — item 17; three hunts came back empty |

**What that does and does not mean.** Every area has now been looked at with a
scan plus targeted driving, and the negatives are recorded alongside the
findings. It is not a proof of absence: each sweep states its own scope in its
section — same-module reachability, bounded depth, receivers matched by name
shape — and a defect outside those bounds would not have been seen.

**Items 8 and 9 remain the weakest entries.** Their three-lens adversarial
verification died with the original fan-out. I re-ran both against the real
config myself, which is why they are CONFIRMED, but they have not been attacked
the way items 1–7 were.

**A note on method, since it decided several verdicts.** Four of the sweeps in
this review first produced a large number of hits from a question looser than
the claim being made, and each time tightening the question to match the claim
cut them to a handful or to none:

| Sweep | Loose question | Tight question | Hits |
|---|---|---|---|
| Concurrency | takes the lock anywhere AND calls a lock-taker anywhere | the call is lexically inside the `with` block | ~40 → 4 → **1** |
| UI threading | a marshal is a bare-name target | a marshal is anything in its arguments, lambda bodies included | 7 → **0** |
| JP accumulators | any `+=` on an attribute | only attributes initialised as a container | ~35 → **0** |
| Audit tool (item 3) | does the loop body catch? | can the handler leave the loop? | missed the real one → **found it** |

That is the same failure as the audio-retention "deadlock" in item 6, and it is
worth stating because a looser test does not merely add noise — it hides the
real finding inside it.

---

## Also checked, and not defects

| Claim | Why it is not a bug |
|---|---|
| `japanese_accuracy_log` `_writer_started` latch (flagged by the repo's own auditor) | The writer loop body ends `except Exception: continue`. The thread cannot die. False positive. |
| `follow_tail` + Clear freezing a pane | `_sync_follow_tail_button` re-arms following whenever `yview() == (0.0, 1.0)`, `main_window.py:4861-4862`. |
| `print()` invisible in the shipped build (`pythonw.exe`, no console) | `main.py:15` installs `alpha/utils/console_capture.py`, teeing stdout **and** stderr into `logs/console-<ts>.log`, which `collect_logs.py:348` bundles. Prints are observable. |
| `_log_detected_language` returning `None` | Its single call site is a bare expression statement. It is a pure logger; `None` is the correct contract. |
| The 8 failing tests in the suite baseline | All triaged as stale tests asserting contracts that were deliberately superseded. No product bug behind any of them. |

## Test baseline

1503 tests at 26.5.25 (1239 when this review was written; the phases added the
rest). Eight fail, and the **set of eight names** — never the count — is the
baseline. All eight are stale tests, listed in the previous audit.
Runner (there is no `tests/__init__.py`, so `-t .` fails):

```bash
cd Alpha_Live_Translator && py -m unittest discover -s tests -t tests -p "test_*.py"
```

---

## Item 28 — the microphone switch could not be used during a meeting

**Reported live.** "session cholakali time a mic on kora jaina… stop kore abar
start kora lage."

`toggle_microphone_capture` set `_microphone_capture_enabled`, and
`_start_listening_worker` read that flag exactly once, at Start.
`_set_listen_button_state(listening)` therefore disabled both switches for the
whole session — honest about the old contract, but it meant an operator who
realised mid-meeting that their own voice was missing had to Stop and Start
again, losing the running transcript's continuity to get it.

### The audio graph never needed that restart

Measured on the real `DeepgramTimelineMixer`, driven for real time, with the
session configured at Start exactly as `main_window` configures it when the mic
is off — `configure_sources(2, 48000, mic_available=False)`:

| | frames | frames carrying mic | `mic_rms` | `chosen_source` |
|---|---|---|---|---|
| before the mic | 60 | 0 | 0.0 | `none` |
| after one push | 61 | 50 | 6320.6 | `mixed` |

Nothing was reconfigured between those rows. `mic_audio_queue` is created at
Start and lives until Stop, the mixer drains it every tick, and `push_mic`
latches `_mic_source_available` by itself — `configure_sources`'s `mic_available`
only *seeds* that flag. Opening and closing the stream mid-session was already
proven separately: that is what `_rebind_microphone_to_default_device` does on
every default-input change.

### The fix

`MicrophoneCaptureMixin._apply_microphone_capture_live` compares wanted against
actual and opens or closes one stream, on the shared `_schedule_audio_rebind`
worker — never the mainloop, where `sd.InputStream` open would block the UI.
The switches are no longer disabled, and `_set_mic_switch_enabled` is gone with
the contract it enforced.

Three things the first draft of that method got wrong, each now a test:

1. **A flip that landed mid-apply was dropped.** `_await_capture_confirmation`
   waits up to `REBIND_AUDIO_CONFIRM_S` (3.0 s), so ON-then-OFF inside that
   window hit the single-flight guard and returned — leaving the stream live
   while the switch read OFF, the worst direction for this control to fail. The
   apply now records a pending flip and re-checks it under the *same* lock
   acquisition that releases the claim.
2. **Flipping it on the idle window spawned a worker that did nothing.** The
   dispatcher refuses before dispatching; the apply still re-checks, because it
   runs on another thread where the session can end underneath it.
3. **A flip during "Starting…" was silently lost.** `is_listening` is False for
   that whole window, so the apply correctly refused; `_finish_start_listening`
   now runs it once after the session is live, which settles that window and
   does nothing otherwise.

Single-flight uses its own `_mic_capture_apply_in_progress` rather than
`_mic_rebind_in_progress`, deliberately: `_close_microphone_stream` skips its
baseline reset while that flag is set — right for a rebind that is about to
replace the baseline, wrong here where the microphone is genuinely going away.
No mutual exclusion with the device rebind is lost, because that rebind
requires *both* the switch on and a stream already open, which is exactly the
state in which the apply has nothing to do.

The seam is marked with `_mark_device_swap_boundary` in both directions: the
operator's voice appearing or disappearing mid-utterance is the same acoustic
discontinuity as a device swap, and the assembler must not glue across it.

### Verified on the real window

`AlphaApp` driven directly, with only the two device calls stubbed:

| Check | Result |
|---|---|
| switch state while listening | `normal` (both header and menu) |
| flip on the idle window | 0 opens |
| flip while listening | 1 open, on thread `AudioDeviceRebind` |
| ran on the mainloop | `False` |
| flip back off | 1 close, `_mic_stream` is `None` |

20 new tests in
`tests/test_the_microphone_switch_works_during_a_meeting.py`. The two tests that
encoded the lock as the contract were removed from `test_microphone_toggle.py`
with a pointer to their replacement. Suite: 1536 tests, the same **set of eight**
failing names.

### Not fixed here, and reported separately

The language dropdown has a worse version of the same problem and is **not**
touched by this change: it is not locked during a session, and
`on_language_change` writes `_listen_language` immediately, so a mid-session
change leaves the app on the new language while the open socket is still on the
old one. Measured: `listen_language` `ja` → `en` while the connected URL stays
`language=ja`. `should_use_japanese_final_stabilizer` re-reads that field on
every call, and `stop_finalize_worker` stamps every leftover record with the
host's *current* language — so the Japanese half of a switched session would be
submitted for translation as English. The canonical ledger carries no language
field at all (`grep` returns zero hits), so there is nothing to tell the two
halves apart. Fixing that needs a ledger stamp first; it is a separate change
set.

---

## Item 29 — "Start does nothing": a refused key was taken for a Stop

**Reported from the field**, with a log bundle from a shared build (26.5.25,
installed per-user). Six Start presses in half an hour, all the same:

```
09:37:37.051  start_listening_clicked
09:37:38.141  websocket: Handshake status 401 Unauthorized
              {"err_code":"INVALID_AUTH","err_msg":"Invalid credentials."}
09:38:07.642  Error starting listening: Deepgram sender not ready within 30s
              -> status "Stopped", no dialog
```

The request reached Deepgram (`dg-request-id` present) and Deepgram refused the
key. The key was the operator's own — its mask matches neither the delivery
`.env` nor `installer/keys.local.ini` — so nothing on the owner's side was
revoked. What made it look like a broken button was the app.

### Three defects, each measured

| # | Defect | Evidence |
|---|---|---|
| 1 | `_deepgram_on_error` counts `not is_listening` as a stop request, and `is_listening` is False for the whole of Start — so the 401 returned before item 47's auth check | operator's bundle: 6× `DEEPGRAM_CLOSE_NORMAL reason=stop_requested` carrying the 401 text, **0×** `DEEPGRAM_AUTH_REJECTED`. Real handler, Start state: `_dg_auth_failed = False`; listening state: `True` |
| 2 | The Start worker's sender wait watched only `_stop_event` | a refusal known at ~1 s sat out the full 30 s |
| 3 | `_finish_start_listening` printed and returned | no dialog; the keyless build's key dialog is offered by the preflight only for a MISSING key |

### The fix

* `deepgram_start_refusal(err, err_text)` turns a handshake status into
  `DeepgramRefusedStart(status, detail)` — or `DeepgramKeyRejected` for a 401/403
  that carries a Deepgram marker. `_deepgram_on_error` records it as
  `_dg_start_refusal` **before** the stop-request early return, only while
  starting; a live session is left to the reconnect loop exactly as before.
* `AlphaApp._wait_for_deepgram_sender` raises the recorded refusal at once.
* `_explain_start_failure` says what Deepgram said, and where to fix it — the
  `.env` path on a keyed build; on a keyless build the reason first, then the key
  dialog with the working DeepL key carried over.

**Against the real endpoint** (an all-zero key; no real key used): the same 401
as the field, `DeepgramKeyRejected` after **1.36 s** instead of ~30, one dialog
reading *HTTP 401 Unauthorized - Invalid credentials.* with the `.env` path.

### What the review of the first version found

A three-lens adversarial review ran against the first draft. Everything below
was reproduced here before being fixed, and each now has a test.

| Finding | Severity | Reproduced | Now |
|---|---|---|---|
| The key dialog runs `mainloop()` on a second `tk.Tk()` and closed with `destroy()` alone, so the loop never returned while the main window existed. Opened from inside the UI event bus drain it stopped the bus for good: the next Start hung on "Starting…" | **high** | real `ask_for_keys` under a running root: Escape, Quit, close box and Save all hit a 3 s watchdog | `close()` = `quit()` then `destroy()`, close box wired; the dialog is also scheduled with `after(0)`, outside the drain. Replayed on the real app, bus and dialog: returned in 10 ms, the next worker callback delivered |
| **Older than this item:** the same never-returning loop was already reached from the Start preflight since the keyless build shipped — `"API keys saved"` never appeared there | high | same test | fixed by the same change |
| A generic "check the internet connection" dialog followed WASAPI's own dialog on an audio failure — two dialogs, the second wrong | medium | reviewer's harness | generic dialog removed; only Deepgram refusals, which have no other voice, are explained |
| Every other refusal at Start (402 out of credits, 429, 400) still went through the stop branch and waited 30 s | medium | reviewer's harness, and a local 400 server | all handshake refusals recorded; reason and status shown |
| A proxy's 403 was blamed on the key | low | synthetic Zscaler text | 401/403 is a key problem only with a Deepgram marker |
| The exception always said "401", whatever the status | low | — | carries the real status and Deepgram's message |
| On a keyless build the dialog reopened blank, with no reason | low | — | reason first; DeepL carried over |
| Three new tests passed on mutants that broke the fix (a dialog on every Start; the dialog before teardown; the wait replaced by a comment) | low | mutants run | AST- and order-based tests; all three mutants now caught |

### Found while packaging: the updater made a keyless install keyed

Driving the 26.5.27 package against a synthetic keyless install printed
`remove  .needs-api-keys`. The marker is written by `build_installer.py
--no-keys` and exists in no payload, so `apply_update.py`'s "no longer part of
the app" sweep deleted it. After any update a shared-build install would stop
offering the key dialog — for a missing key and for a rejected one — with no
way back but editing a hidden file. **Every update package up to and including
26.5.26 has this defect; do not apply those to a keyless install.**

`.needs-api-keys` is now in `PRESERVE_FILES` beside `.env`, spelled out (the
updater runs without the app on its path) and pinned to `key_setup.MARKER_NAME`
by a test. Both new tests failed first. Re-driven against a keyless 26.5.25
install — the field operator's version — the package kept `.env`,
`.needs-api-keys` and `logs\`, and brought in both 26.5.26's microphone fix and
this item.

### Not fixed here

* A **400 keyterm rejection at Start** still does not take the keyterm fallback —
  the fallback lives past the same early return, and has since before this item.
  It now fails in about a second with Deepgram's reason instead of 30 s silence,
  but a retry without keyterms would be the right behaviour.
* A `DEEPGRAM_API_KEY` **Windows environment variable** overrides `.env`
  (`load_dotenv` does not override), so editing `.env` cannot fix a key set with
  `setx`. Pre-existing; not reproduced through the app itself.
* The runtime debug log still labels a refused Start `DEEPGRAM_CLOSE_NORMAL
  reason=stop_requested`. That label is what hid this bug in the bundle; it is
  left as it is only to keep this change to the behaviour the operator sees.

Suite: 1565 tests, the same **set of eight** failing names.

---

## Item 30 — every way Deepgram stops a meeting says so, in the operator's language

**Asked by the owner after item 29:** "deepgram er o key trial expired hoye jai.
Setar o error message lagbe", with every popup in English or Japanese by the
display-language setting.

### What Deepgram actually answers

From its error reference (developers.deepgram.com/docs/errors), verbatim:

| Status | `err_code` | `err_msg` | Meaning here |
|---|---|---|---|
| 401 | `INVALID_AUTH` | Invalid credentials. | wrong, deleted **or expired** key — the answer does not say which |
| 401 | `INSUFFICIENT_PERMISSIONS` | User does not have sufficient permissions. | the key may not transcribe |
| 402 | `ASR_PAYMENT_REQUIRED` | Project does not have enough credits for an ASR request and does not have an overage agreement. | **the free trial credit is gone** |
| 403 | `INSUFFICIENT_PERMISSIONS` | Project does not have access to the requested model. | plan has no Nova-3 |
| 429 | `TOO_MANY_REQUESTS` | Too many requests. Please try again later | busy |
| 503 | — | — | down |

Deepgram's forum also confirms that an already-open socket survives its key
expiring, so mid-meeting these arrive on the next reconnect, not at once.

### Measured before the change

| # | Case | Before |
|---|---|---|
| 1 | 402 on a mid-meeting reconnect | indicator `● Reconnecting` for the rest of the meeting; no flag existed |
| 2 | a genuine 400 mid-meeting | **two** modals for one error (the error event and the stop notice) |
| 3 | a socket that failed at once, at Start | waited out the full timeout (3.02 s of 3.0 in the probe; 30 s in the app) |
| 4 | 402 at Start | "Deepgram refused the connection" — no reason attribute, no credit wording |
| 5 | the preflight "no key" popup, the mid-meeting "key rejected" popup | English on a Japanese screen |

### The change

* `deepgram_start_refusal` gives every documented answer a `reason`
  (`service_status.REFUSAL_*`); `DeepgramUnreachable` covers a Start that got
  no answer at all, carrying the socket's own error.
* The words live in `service_status` as one English literal per sentence and
  are shown through `t()`; status, Deepgram's error code and the `.env` path are
  shown as they are. A keyless build offers its key dialog only where a
  different key cures it — rejected key, no permission, **used-up credit** —
  never for a busy service or a plan without the model.
* `_wait_for_deepgram_sender` fails as soon as the Deepgram thread has ended
  with no answer (`run_forever` returns only once the socket is gone).
* A 402 mid-meeting sets `_dg_credit_exhausted`: failed state,
  `● Deepgram credit used up`, one dialog; cleared when the socket opens, so a
  top-up recovers the meeting.
* The rejected-query stop notice owns its dialog; the error event is no longer
  published unrecoverable beside it.

### Verified on the real socket stack

A local server answering the websocket upgrade exactly as documented, the real
`AlphaApp` running its real `_deepgram_worker` (real websocket-client),
`_deepgram_on_error`, `_wait_for_deepgram_sender`, `_finish_start_listening` and
`_explain_start_failure`, in both languages:

| Answer | Reason | Failed after | Dialog (en / ja title) |
|---|---|---|---|
| 402 `ASR_PAYMENT_REQUIRED` | `credit_exhausted` | 0.05–0.10 s | Deepgram credit used up / Deepgram のクレジットを使い切りました |
| 401 `INVALID_AUTH` | `key_invalid` | 0.05 s | Deepgram API key rejected / Deepgram の API キーが拒否されました |
| 403 `INSUFFICIENT_PERMISSIONS` (model) | `no_model_access` | 0.05 s | Deepgram model not available / Deepgram のモデルを利用できません |
| 429 `TOO_MANY_REQUESTS` | `rate_limited` | 0.05 s | Deepgram is busy / Deepgram が混雑しています |
| 503 | `service_unavailable` | 0.05 s | Deepgram is unavailable / Deepgram を利用できません |
| closed port | `unreachable` | **2.07 s** (was 30) | Cannot reach Deepgram / Deepgram に接続できません, with `[WinError 10061]` |

Mid-meeting, the real `_reconnect_deepgram` against the documented 402: the
indicator went `● Reconnecting` → `● Deepgram credit used up` (`● Deepgram
クレジット切れ`), **one** dialog across two retries, the loop still retrying.

Five mutants (credit flag never set, no fast fail, the second modal back, 402
not told apart, popup lines untranslated) are each caught by the new tests.
Two older assertions were changed deliberately and say why: item 29's
out-of-credit test now expects the key offer, and the 400 test expects the
error event recoverable.

Suite: 1602 tests, the same **set of eight** failing names.

---

## Item 31 — a running meeting that is not working says what is wrong

The other half of the owner's request: warnings for everything that leaves a
meeting running while "Alpha is not working". None of these raises a modal —
a dialog stealing focus from the meeting, over a window that is always on top
and may be screen-shared, is worse than the problem. Each names itself in the
status strip, in the display language, and a click on the indicator shows the
whole sentence.

### Measured before

| Case | Before |
|---|---|
| a DeepL error text containing the id `7f403a91-4560-4000` | classified **quota exceeded** — translation off for the session (`"456" in msg`) |
| DeepL rejecting the key | `worker.degraded` stayed **False**: the indicator stayed green |
| DeepL quota used up | `● Translation degraded`, no reason |
| microphone switched on, failing to open | the switch went on saying **"Mic on"** |
| no DeepL key, Japanese display | pane: `Translation unavailable (missing DEEPL_AUTH_KEY).` |
| no sound / sound but no words | no signal existed at all |

### Where the thresholds come from

The retained runs keep a 20 ms packet manifest per stream and every
`COMMIT_APPLIED` with its time. Counting sound generously (any sample above
|50| — looser than the source gate's speech threshold, so an upper bound):

| Runs | Longest sound with no commit | Longest silence |
|---|---|---|
| healthy (2026-08-19 … 08-21, 6 runs, 11–153 commits) | **12.6 s** | 0.1 s |
| 2026-08-14 (4 runs) | **53–135 s** | 7.7 s |

The 2026-08-14 runs are not false alarms: in `…112901` finals kept arriving
(36) while commits stopped at 15 — the pipeline had stopped committing, exactly
what the hint is for. **Thirty seconds** of speech-level sound separates the two
with margin. Words are counted as the canonical ledger changing, not as finals
arriving, for that reason. **Sixty seconds** of no signal at all (rms < 1; item
73 measured an unrouted device as exact digital silence) is far above the 7.7 s
longest silence inside any retained run.

### The change

* `_note_audio_activity` (mixer thread): any signal, and speech-level seconds by
  the source gate's own activity decision. `_audio_attention_inputs` (UI tick):
  seconds without sound, speech seconds since the ledger last moved — the
  baseline reset only from the UI thread, and following the total during a
  Deepgram outage so the reconnect keeps that story.
* `describe_connection` gains `no_sound` / `no_speech` between `degraded` and
  `reconnecting`, and names a degraded translation's reason (`quota`, `auth`) or
  a translation that never started (`missing_key`).
* `DeepLClient`: the SDK's exception types and `http_status_code` first, words
  after, digits never. `TranslationWorker.degraded_reason`; a rejected key now
  degrades.
* The microphone switch reads **"Mic unavailable"** while a session runs and the
  stream failed to open, delivered nothing, or a rebind failed.
* The translation pane and every new sentence go through `t()`.

### Verified on real code

* **Real mixer, gate and indicator** (thresholds shortened to 4 s / 3 s for the
  run; production values pinned by tests), fed stamped 48 kHz audio in real
  time: `Signal OK` → 4.1 s `No sound` → sound starts, clears → 9.4 s `No speech
  recognised` → a line reaches the ledger, `Signal OK` → 3 s more sound without
  words, the hint again. Identical in Japanese (`● 音声なし`, `● 音声を認識できません`).
* **Real DeepL SDK** against a local server: 456 → `quota_exceeded`, reason
  `quota`, strip `● DeepL quota used up` / `● DeepL 上限到達`; 403 →
  `auth_failed`, reason `auth`, `● DeepL key rejected` / `● DeepL キー拒否`; the
  click shows the full sentence in each language.
* Seven mutants — speech never counted, words never resetting it, an outage
  counted against words, the digit substring back, a rejected DeepL key not
  recorded, a mic failure not shown, the no-sound rule ignored — all caught.

Found while wiring it, each fixed before shipping:

* the indicator called the new input method directly, and every existing
  indicator test host without it went **blank** (the indicator's own `except`
  returns without painting). The hints are now an addition that can never stop
  the older states from painting;
* the click binding read `self._explain_connection_state` at build time, and
  `create_status_bar` is also borrowed by item 88c's layout hosts — 37 of their
  tests errored. It is looked up at click time now, and the grep test for the
  binding was replaced by a real click on the real label (proven to fail when
  the binding moves to another button);
* the item 94 latch audit flagged `_auth_rejected` as a flag with no way back.
  A successful translation and `reset_session` now clear it, with a test.

Suite: 1647 tests, the same **set of eight** failing names, plus two known timing-sensitive tests that passed alone (item48's documented intermittent; item71's 5 ms/40 ms first-map snapshot, 3/3 in isolation and green in the previous full run of this change).

---

## Item 32 — the file the operator is told to edit is the one that is read

Found while reviewing item 30, not in a field log. Items 29 and 30 print the
`.env` path and tell the operator to put a valid key on its `DEEPGRAM_API_KEY`
line and start Alpha again. The first-run dialog writes that same file, and so
does the installer. On a machine with a `DEEPGRAM_API_KEY` left in the user
environment, every one of those instructions was a lie.

`alpha/config.py` read the file with `load_dotenv(PROJECT_ROOT / ".env")`.
python-dotenv skips every name already in `os.environ` -- its
`DotEnv.set_as_environment_variables` is `if k in os.environ and not
self.override: continue` -- so the variable won, silently, and the operator
edits the file, restarts, and gets the same rejected key with no hint that the
file is being ignored.

### Measured on the real module

Both against the real `alpha.config`, with this tree's own `.env` in place:

| | `DEEPGRAM_API_KEY` from the environment wins | `DEEPL_AUTH_KEY` wins |
|---|---|---|
| before | **True** (`get_deepgram_key_status()` = `configured`) | **True** |
| after | False | False |

With no `.env` at all, the environment is still the source (`True`) -- a
developer exporting a key in a shell, and the app's own "set
`DEEPGRAM_API_KEY` in your environment or .env file", both keep working. That
sentence only ever appears when no key is configured, so it stays true.

### The change

`load_dotenv(PROJECT_ROOT / ".env", override=True)`. One line, at the single
load site; nothing else in the app or the installers reads either key from the
environment, and nothing writes them there except the key dialog, which writes
the file first.

One test changed with it, deliberately: `_Env` in
`test_placeholder_keys_are_caught.py` injected keys through `os.environ` and
relied on the file NOT winning ("`load_dotenv` fills in any name that is NOT
already in os.environ"). It now reloads with no file loaded at all, so the
environment it builds is the whole truth; the file-versus-environment order is
pinned by the new test instead. Proven load-bearing both ways: without
`override=True` the new test fails, and without the harness change seven of
those nine tests fail.

### Still open, deliberately

On a keyless build with **no** `.env` and a stale variable, the key dialog
still never appears (the variable reads as a configured key) and item 30's
hint points at a file that is not the source. Reported, not fixed: the file
does not exist to be outranked, and inventing one to fight a variable is a
bigger change than the problem.

Full suite at this change: `Ran 1649 tests`, the same eight stale failures as
the recorded baseline and nothing else.

---

## Item 33 — the meeting's language is fixed for the run

The other half of the report that produced item 28: "changing the language
mid-session stops it working; I have to stop and start again." Item 28 made the
microphone switch work mid-meeting. This one cannot be made to work, so the
window now says so instead of letting the operator find out from a transcript.

Deepgram is told the language in the query string of the socket the Start
opens. `on_language_change` rewrites `self._listen_language`, but the live
socket cannot be told, so a switch left the OLD language transcribing while the
header claimed the new one. The canonical ledger carries no language of its own
either, so the finalize pass stamps whatever is selected at the end onto records
the previous language produced. Stopping and starting was already the only way
to change it.

### The change

* `_set_language_controls_enabled` locks the five controls that can change the
  meeting language: `source_combo`, `target_combo`, `source_combo_menu`,
  `target_combo_menu` and `swap_button`. All of them together -- there are two
  languages, so `on_language_change` makes picking either dropdown decide the
  other, and the swap button writes both.
* Locked from **`_set_starting_status`**, not from success: `_start_listening`
  has already snapshotted the dropdown by then, so a switch during
  "Starting…" would run the session on the snapshot while the header showed the
  other language.
* Released from **`_set_listen_button_state(False)`**, which only
  `_set_stopped_ui_state` calls -- so every Stop, and every Start that fails
  (`_stop_listening(graceful=False)`), releases it with no second code path.
  A combo comes back `readonly`, never `normal`: there is nothing in this window
  to type into.
* The UI's own **display** language is deliberately untouched. That one is
  re-rendered live and item 88e tests that path.

### Two leaks the lock had, both measured on the real window

| | before | after |
|---|---|---|
| text half of a locked combo posts the language list | **True** | False |
| a pick from a list already open when the lock landed | **True** -> English | False -> Japanese |

`_make_combos_fully_clickable` bound `<Button-1>` on each combo's entry
straight to `_open_dropdown_menu`, which checks no state at all, and a disabled
tk Entry still delivers user bindings (measured). It now calls `_clicked`, which
is what the arrow uses and refuses while disabled. And a list can already be
posted when the lock lands -- a Start completing while it is open -- so
`on_select`, the only way a header combo writes the language, refuses and puts
the label back.

### Verified on real code

* Real `AlphaApp`, mapped window, locked and released: arrow blocked/open,
  text half blocked/open, swap blocked/works, `readonly` restored, and the
  label still showing the run's language.
* Also fixed in the same pass, because the lock caused it:
  `CTkComboBox.set` writes through its entry and a disabled entry drops the
  write silently, so item 71's responsive "Japanese" -> "JP" narrowing stopped
  landing while a meeting ran. `_write_combo_text` lifts the lock around the
  write.
* Six mutants -- no lock while running, no lock during "Starting…", a combo
  restored as a text box, the write helper not lifting the lock, the
  whole-combo click calling the opener again, an already-open pick accepted
  again -- all caught.

Full suite at this change: `Ran 1657 tests`, the same eight stale failures as
the recorded baseline and nothing else. No build: the owner calls the final
one, and `APP_VERSION` is bumped so the next package carries this.

---

## Item 34 — a keyterm Deepgram refuses at Start costs the meeting

The keyterm fallback has existed since the 2026-09-14 audit: Deepgram refusing
the query turns the Japanese keyterms off and reconnects without them. It runs
only mid-meeting. `stop_requested` in `_deepgram_on_error` counts
`not is_listening` as a stop, and `is_listening` is False for the whole of
Start -- the same early return item 29 had to reach around for a rejected key.

### Measured before, through the real `_deepgram_on_error`

| | keyterms turned off | reconnect scheduled | next URL still sends keyterms |
|---|---|---|---|
| mid-meeting | True | 1 | False |
| at Start | **False** | **0** | **True**, and `_dg_start_refusal` = `DeepgramRefusedStart 400` |

So the one answer the fallback exists for ended the Start instead, and the
operator got "Deepgram refused the request (HTTP 400)" with nothing to act on.

### The change

* `_keyterm_fallback_would_help(host, err, err_text)` -- one function, both
  callers. Mid-meeting keeps its own `query_rejected` gate; nothing about that
  path changes.
* At Start the fallback turns the keyterms off and returns **without recording
  a refusal**: `_wait_for_deepgram_sender` raises one the moment it appears, so
  recording it would make the retry impossible.
* `_deepgram_worker` reopens the socket once, with a URL rebuilt without the
  keyterms. It is the only place that can: `_schedule_reconnect` returns
  immediately while `is_listening` is False, which is all of Start.

The retry is bounded twice -- by the keyterms-were-on transition (the flag is
cleared only by a new Start) and by a counter. The counter is unreachable
today and the mutant that raises it is not caught; it is kept because the loop
runs on the Start thread, where an unbounded reconnect would hang the Start
instead of failing it.

### Verified

* Four tests in the file that already owns this fallback, three of them red
  before the change. The socket test drives the real `_deepgram_worker` with
  the WebSocket class faked, and asserts two attempts, the first carrying
  `keyterm` and the second not.
* Four mutants: the Start never running the fallback, the Start swallowing
  every refusal, the retry reusing the refused URL, the retry unbounded. The
  first three are caught; the fourth is the unreachable counter described
  above.
* Neighbouring suites unchanged: items 29/30's 27 + 37 tests, the URL builder,
  and item 31's 45.

Full suite at this change: `Ran 1661 tests`, the same eight stale failures as
the recorded baseline and nothing else.

---

## Item 4 of the pending list — closed by item 32, no change

Raised in the item 32 report: on a keyless build with **no** `.env` and a stale
`DEEPGRAM_API_KEY` in the machine environment, the first-run dialog never
appears (the variable reads as a configured key) and item 30's hint was
believed to name a file that is not the source.

Driven through the real `key_setup.should_prompt` and the real
`_explain_start_failure`, on a synthetic keyless install with no `.env`:

```
first-run prompt with a stale variable supplying both keys : False
Deepgram API key rejected
Deepgram rejected the API key, so listening could not start.
The key may be mistyped, expired, deleted, or from a different account.

Deepgram: HTTP 401 INVALID_AUTH

Enter a valid Deepgram key in the next window.
key dialog offered : True
```

The hint is the keyless one, not the `.env` path, and the dialog opens. The
operator's key is written to `.env`, and since item 32 that file outranks the
stale variable, so the restart the dialog asks for cures it. The only residue
is that the problem surfaces at the first Start rather than at first run --
which is what a configured-but-wrong key looks like from inside the app.

An earlier run of this same measurement reported the wrong thing (the generic
refusal text, no dialog) because the harness had `ALPHA_NO_KEY_PROMPT=1` set,
which `should_prompt` honours. Recorded because the mistake is easy to repeat.

---

## Item 35 — one utterance is one line, and the ledger agrees with the pane

The owner's 1:45 English session (2026-09-25 16:40:43) exported one 1.2-second
sentence as three lines. The audio clock says it was one sentence:

| Record | Audio | Commit reason | Text |
|---|---|---|---|
| U-4 | **13.28**–14.08 | utterance_end | I will send you a |
| U-5 | **13.28**–14.40 | sentence_boundary_flush | I will send you both. |
| U-6 | **13.28**–14.48 | utterance_end | I'm sending you both. |

"Would I would" [69.76] / "I would I would, Why would those who are?" [69.84]
the same way. The app's own visible-error audit flagged "Good afternoon." /
"Good afternoon. How are you?" as well -- a false positive: different
speakers, starts 3.7 s apart. It is left alone.

### Two mechanisms, one cause

A later guess at the SAME audio was treated as new speech. Deepgram's interims
are cumulative per segment, so two interims whose first words start together
are two guesses at one span of audio.

1. **Across a commit.** `UtteranceEnd` committed the held interim 10 ms after
   it arrived; Deepgram had not finalised the segment and kept revising it
   (0.55 s, 1.5 s, 2.5 s later). Each revision started a new utterance. Item
   66's trim needs the committed tail verbatim, and a revised last word
   ("a" -> "both") defeats it.
2. **Inside one utterance.** "I will send you both." + "I'm sending you both."
   share 2 of 4 words, under `_merge_lexical`'s 0.6 similarity gate, so it
   concatenated them -- and the sentence flush split the glue into two records.

### The first fix made the export worse -- found by replaying, not by reading

The lifecycle half (re-open the committed utterance, commit it as a SUPERSEDE)
passed its unit tests. Replayed through the REAL app -- real Start, WASAPI,
`_deepgram_on_message`, UI queue, duplicate protection, registry, ledger,
Stop, sealed export -- against a local stand-in sending the owner's exact
messages, the pane read "I'm sending you both." and the **sealed export read
"I will send you a"**: the revision never reached the ledger, so the export
kept only the early wrong guess. Two gates, each written for another case:

* `_commit_locked` spreads the registry entry into the commit metadata; for a
  supersede its `canonical_record_id` is the record being REVISED, and
  duplicate protection reads that key as "the Japanese assembler already wrote
  the ledger" (`already_committed`) and skips the write. Item 66 measured this
  on the extend path and routed around it.
* With that fixed, the lifecycle's own observation of the commit makes duplicate
  protection's second one `idempotent_replay`. The registry excuses that for a
  first commit (`awaiting_canonical_commit`, no record yet) but not for a
  revision, so every English revision was dropped as a duplicate.

Neither was specific to the new path: **the existing final-correction
(`_supersede_committed_locked`) and extend (`_extend_committed_locked`) paths
never reached the ledger either.** For extend that was content loss -- the
continuation was in the pane and absent from the export.

### The change

* `_same_provider_segment` -- first-word starts within `_TIMING_START_MATCH_S`.
* **Re-open** (`_revises_committed_segment_locked`): an interim that would start
  a new utterance re-opens the last committed one -- same canonical id, next
  version -- when that commit was `utterance_end` or the inactivity timeout,
  same channel, same audio, and the record never absorbed a final and was not a
  sentence-flush tail. An older, shorter guess (contained in the record) is
  ignored rather than allowed to shrink it. Every gate fails closed onto the old
  path.
* **No glue**: inside an all-interim utterance, a same-segment interim replaces
  when `_merge_lexical` would have joined the two; growth and keep-longer are
  untouched.
* `_commit_locked`: a re-opened utterance commits as `SUPERSEDE_PREVIOUS`, and
  any supersede carries the registry record id as its revision target instead
  of an already-written claim. (Japanese commits through
  `accept_boundary_proposal`, never through here.)
* `duplicate_protection`: a lifecycle SUPERSEDE seen as `idempotent_replay` is a
  duplicate only if the target ledger record already holds that text; logged as
  `LIFECYCLE_REVISION_PAST_REPLAY`.

### Verified

* Replay through the real app, owner's messages, sealed export:

  | | before | after |
  |---|---|---|
  | dup A | I will send you a / I will send you both. / I'm sending you both. | I'm sending you both. |
  | dup B | Would I would / I would I would, Why would those who are? | I would I would, Why would those who are? |
  | two speakers | 2 lines | 2 lines |
  | two sentences, one window | 1 line | 1 line |

  Ledger: `revise` transactions targeting the early records. Translation pane:
  one line each -- the revised translation replaced the stale one.
* `test_one_utterance_is_one_line.py` (9) and `test_a_revision_reaches_the_ledger.py`
  (3, real lifecycle + real publisher + real duplicate protection + real
  ledger). The owner's sequences fail before the change exactly as observed
  (`3 != 1`, `2 != 1`).
* Nine mutants -- no re-open, glue again, re-open committed as an ordinary
  commit, the record id leaking again, the replay gate removed, the has-final
  guard, the flush-tail guard, the shrink guard, the channel guard -- all
  caught.
* Items 66 (21), 70 (33), line quality, cross-speaker guards, substitution
  update, commit-in-flight -- unchanged.

### Not fixed, deliberately

A sentence flush that commits text Deepgram later revises (the owner's U-13
"...If I can, I can?" / U-14 "If I can't, actually, ..." at the same start):
the flush is a confident boundary and its tail legitimately shares the window's
start, so re-opening there cannot tell a revision from the rest of the window.
Excluded, and pinned by a test.

Full suite at this change: `Ran 1673 tests`, the same eight stale failures as
the recorded baseline and nothing else.
