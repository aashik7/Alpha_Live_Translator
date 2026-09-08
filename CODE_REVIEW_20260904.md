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

## 1. A full translation queue silently stops the translation pane for the rest of the session

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

## 2. The wrong-language warning is a stub that always says "commit"

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

## 3. The WASAPI reader thread dies permanently on one error

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

## 4. Four log writers have no rotation; one file is unbounded across sessions

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

## 5. A default-audio-device change is detected but capture is never re-bound

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

**Fix.** After raising the signal, marshal a rebind through `_run_on_ui_thread`:
set `_stop_event`, `_close_wasapi_stream()`, clear the event,
`_start_wasapi_loopback()`. `PyAudio.terminate()` plus a fresh `PyAudio()` is what
actually picks up the new default — a re-query on the live handle provably cannot
(measured: same index). Two hazards in the same change: it must not run on the
watcher thread, and the old watcher must be stopped before the rebind, or the 1 s
join times out and a **second** watcher is spawned.

---

## 6. The audio-retention self-deadlock — REFUTED

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

## 7. "The English lifecycle bypasses the commit authority" — REFUTED, but it loses the utterance

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

## 8. Every DeepL placeholder key the project ships passes the Start check

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

## 9. The installer's own key template passes both the build gate and the runtime check

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

## 10. The UI says "Stopped" 5 s in, while the deliverable has up to 66 s still to be written

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

## 11. `SECOND_RUN_FOLDER_CREATION_BLOCKED` blocks nothing

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

## 12. Every rebind migrates the unbounded `_pending` pile

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

## What this review did NOT cover

Stated plainly rather than left as an implied clean bill of health. The
subsystem fan-out was killed twice by the session token limit, so **four areas
were never audited**:

| Area | Status |
|---|---|
| Commit-authority internals (`utterance_lifecycle`, `pipeline_commit_transaction`) | **not audited** — only reached indirectly via item 7 |
| Japanese assembler + stabilizers | **not audited** |
| UI threading (worker threads reaching Tk widgets, `after()` lifetime) | **not audited** |
| Stop / finalize internals | **not audited** |
| Global concurrency sweep | **not audited** |
| Config + startup | audited; items 8 and 9 came from it |

Items 8 and 9 came from the one area that finished in the original pass; items 10-12 came from stop/finalize, audited afterwards in phase 3. Their three-lens refutation
pass died with the rest, so unlike items 1–7 they carry **no adversarial
verification** — I re-ran both against the real config myself instead, which is
why they are marked CONFIRMED, but they have not been attacked the way items
1–7 were.

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

1239 tests. Eight fail, and the **set of eight names** — never the count — is the
baseline. All eight are stale tests, listed in the previous audit.
Runner (there is no `tests/__init__.py`, so `-t .` fails):

```bash
cd Alpha_Live_Translator && py -m unittest discover -s tests -t tests -p "test_*.py"
```
