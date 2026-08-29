# Changing the speaker mid-session — design guideline and risk register

**Goal.** Today, changing the Windows default playback device (the "speaker")
requires stopping Alpha and starting a new session. The transcript, the run
folder and the evidence all restart with it. The ask is to change the speaker
**while a session is running**, with the transcript continuous across the change.

**Status:** design only. No production code has been changed for this.

**Revision 2 — after driving the code instead of reading it.** Revision 1 was
written from reading. Executing it found **four defects in the plan itself**,
three of them critical, and one of them would have *caused* the worst bug the
plan exists to prevent. They are corrected below and listed here rather than
silently rewritten, because the next reader needs to know which parts were
verified by execution and which were not:

| # | What revision 1 got wrong | Consequence had it shipped |
|---|---|---|
| P1 | Treated the swap as able to drain `sys_audio_queue` and call `configure_sources()` from its own thread. **`TimelineMixer` has no lock at all** (`timeline_mixer.py` — zero `Lock`), and `audio_mixer_worker` is its sole owner and drains that queue itself (`main_window.py:8556` → `timeline_mixer.py:131-139`). | A data race on unsynchronised numpy buffers — which produces exactly the R1 corruption the plan is written to prevent. **The plan would have caused its own critical bug.** |
| P2 | `configure_sources` was described as re-callable. It is called **once**, at `main_window.py:8551`, *before* the mixer loop, reading `self._wasapi_channels` / `self._wasapi_rate` into locals. | Setting the attributes mid-session changes nothing. The swap would silently keep the old format. |
| P3 | Promised a rollback: "try the previous device again". **Not implementable.** `_get_wasapi_loopback_device()` resolves only `pa.get_default_wasapi_loopback()` (`wasapi.py:28-39`) — there is no open-by-id path — and the old PyAudio instance must be terminated *before* the new default is visible (C1). Once terminated, the previous device index is meaningless. | An operator promised a safety net that does not exist. The swap is **one-way**. |
| P4 | Ranked wrong-rate as the R1 example. **Wrong channel count is far worse**, and it was not measured. | Understated the severity of the one risk that matters most. |

P4 measured, by running the real `pcm_to_mono_16k_np` on a 440 Hz tone, 48 kHz
stereo, 1.000 s:

| Told | Samples out | Duration | Peak | Raised? |
|---|---|---|---|---|
| 48 kHz, 2 ch *(correct)* | 16000 | 1.000 s | 440.0 Hz | — |
| 44.1 kHz, 2 ch | 17414 | 1.088 s | 404.3 Hz | **no** |
| 48 kHz, **1 ch** | 32000 | **2.000 s** | **220.0 Hz** | **no** |

A wrong channel count halves the pitch and doubles the duration, and nothing
raises. That is not a degraded transcript; it is a confident, fluent, wrong one.

---

## 0. The one fact that makes this feasible

Before any risk analysis, the single architectural question that decides whether
this is a small feature or a rewrite:

> **Does a device change force a Deepgram reconnect?**

**No.** Verified:

* Deepgram's wire format is fixed and declared once, at connect:
  `"sample_rate": int(DEEPGRAM_SAMPLE_RATE)` — `alpha/transcription/deepgram_client.py:829`
  and `:1278`.
* The device's own rate and channel count never reach Deepgram. `TimelineMixer`
  resamples every system chunk on the way in:
  `pcm_to_mono_16k_np(chunk_bytes, self._wasapi_channels, self._wasapi_rate)` —
  `alpha/audio/timeline_mixer.py:55-57`, with those two values supplied by
  `configure_sources()` at `:44-47`.

So the socket, the transcript stream, the assembler, the canonical ledger and
the translation worker can all keep running untouched. **The change is confined
to the capture end.** Any design that reconnects Deepgram on a speaker change is
paying the transcript's continuity for nothing — that is the outcome this feature
exists to avoid.

Two more facts that narrow the blast radius:

* **The microphone is not involved.** It runs on `sounddevice`, a different
  library, already at `DEEPGRAM_SAMPLE_RATE` — `alpha/audio/microphone.py:50-65`.
  Changing the speaker does not touch it.
* **Deepgram will not drop the socket during a short gap.** The sender loop emits
  a JSON `KeepAlive` every `DG_KEEPALIVE_INTERVAL_S` independently of whether
  audio is flowing — `deepgram_client.py:2269-2272`.

---

## 1. What actually blocks it today

Five hard constraints, each verified in the code:

| # | Constraint | Where |
|---|---|---|
| C1 | PortAudio **snapshots the device list at `Pa_Initialize()`**. A newly-selected default is invisible to the running `PyAudio` instance — a second `PyAudio()` returns the same frozen list. | `alpha/audio/default_endpoint.py:4-8` (measured and documented there) |
| C2 | The loopback stream is bound to a **fixed device index** at open. | `wasapi.py:286` `input_device_index=loopback["index"]` |
| C3 | `_start_wasapi_loopback()` has **exactly one caller**, at session start. There is no re-entry point. | `alpha/ui/main_window.py:10323` |
| C4 | `_close_wasapi_stream()` **terminates the whole PyAudio instance**, not just the stream. | `wasapi.py:368-372` |
| C5 | The reader thread **ends permanently on one read error** — it catches, then `break`s, and nothing restarts it. | `wasapi.py:246-255` |

C5 is not a consequence of this feature; it is a pre-existing defect this feature
would make far more likely to fire, because a device swap is exactly when a read
error happens. **See §5 — it must be fixed first, not alongside.**

---

## 2. The design

One new operation on the audio owner, and nothing else changes:

```
swap_system_audio_device(reason: str) -> SwapResult
```

It is **not** a start and a stop glued together. Start/stop tear down the run
folder, the ledger and the transcript; this must not. The only correct framing:

> Replace the *capture source* behind a live mixer, without the mixer, Deepgram,
> or the transcript ever learning that it happened.

### Ownership — decide this before ordering, or the ordering is a race

Revision 1 got this wrong (P1), so it is stated first and explicitly.

`TimelineMixer` has **no lock**. It is single-threaded by convention: the sole
owner is `audio_mixer_worker`, which drains `sys_audio_queue` itself
(`main_window.py:8556` → `timeline_mixer.py:131-139`) and mutates `_sys_buffer` /
`_mic_buffer` as plain numpy arrays. Any second thread touching the mixer or that
queue is a data race on unsynchronised buffers.

So the swap has **two owners and a hard line between them**:

| Owner | Does | Never touches |
|---|---|---|
| **Swap worker** (posted from the UI, off the Tk thread) | stop old stream, join reader, terminate PyAudio, re-enumerate, open new stream, start new reader | the mixer, `sys_audio_queue`, `_sys_buffer` |
| **`audio_mixer_worker`** (existing) | applies the new format at a frame boundary, where it is sole owner | PortAudio, streams, device enumeration |

### The format stamp removes the coordination entirely

Revision 1 tried to make the two owners hand off safely with ordering — drain,
flush, then reconfigure. That needs cross-thread coordination, which needs a lock
the mixer does not have.

**Stamp the format onto every chunk instead** and the problem disappears:

```
reader:   put_bounded(sys_audio_queue, (pcm_bytes, channels, rate))
mixer:    for (pcm, ch, rate) in drained:  push_system(pcm, ch, rate)
```

`push_system` then resamples with the values that **arrived with the chunk**, not
with whatever `configure_sources` was last told. Consequences, all good:

* old-device chunks still in flight resample **correctly**;
* new-device chunks resample **correctly**;
* **no ordering requirement at all** between the swap worker and the mixer;
* no drain step, no flush step, no lock, no race;
* R1 and R2 stop being risks-to-mitigate and become **impossible by construction**.

This is the single change that makes the feature safe. It is also worth doing on
its own merits, before any swap feature exists: today the mixer's format is a
piece of shared mutable state read on one thread and written on another, and the
only reason it is currently safe is that nothing writes it after startup.

`configure_sources()` stays, demoted to supplying defaults for a chunk that
carries no stamp, and `mic_available`.

### Ordering — what is left after the stamp

```
 1. POST THE REQUEST        UI posts to the swap worker; UI thread returns
                            immediately                          -- R14
 2. MARK SWAPPING           _system_capture_swapping = True, in try/finally,
                            with a bounded auto-clear            -- R9
 3. STOP + JOIN THE READER  join with a bounded timeout. If the join times
                            out, ABORT -- do not terminate PortAudio under a
                            live reader                          -- R4
 4. TERMINATE PORTAUDIO     the only way to see the new device   -- C1
                            *** FROM HERE THERE IS NO WAY BACK -- see below ***
 5. RE-ENUMERATE + OPEN     new PyAudio(), resolve the default loopback,
                            open, start the reader with the new stamp values
 6. RE-BASELINE ITEM 73     baseline = new endpoint id
                            _wasapi_device_change_reported = False   -- R7
 7. CONFIRM POSITIVELY      first stamped chunk from the new device must
                            arrive within the budget             -- R12
 8. RECORD                  one evidence event: old/new device, old/new
                            format, measured gap, outcome        -- R6, R10
 9. CLEAR SWAPPING          in `finally`, on every path          -- R9
```

The mixer is not in that list. It needs no step: it keeps draining and mixing
throughout, resampling each chunk by its own stamp, and never learns a swap
happened. That is the correctness argument for the whole design.

### Failure policy — there is no rollback, and revision 1 was wrong to promise one

Revision 1 said "try the previous device again". **That cannot be implemented**
(P3). `_get_wasapi_loopback_device()` resolves only
`pa.get_default_wasapi_loopback()` (`wasapi.py:28-39`) — there is no open-by-id
path — and the old PyAudio instance must be terminated before the new default is
visible at all (C1). After step 4 the previous device index is meaningless and
the previous instance is gone.

**Step 4 is a one-way door.** The honest policy:

* **Before step 4**: any failure aborts cleanly. The old stream is still open, so
  abort and keep capturing. This is the only safe abort window, and steps 1-3 must
  do all the validation they can inside it.
* **After step 4**: retry `get_default_wasapi_loopback()` a bounded number of
  times with backoff, because the OS may still be settling. If it never opens,
  **degrade and report**: mic-only capture continues, the UI says system audio is
  unavailable, the session is NOT killed, and the operator is told plainly that a
  restart is needed to recover system audio.
* Never kill the session on a failed swap. The feature exists to avoid a restart;
  a failure mode that forces one is worse than not having the feature.

## 3. Risk register

Severity is about what the *client* loses, not about implementation difficulty.

### R1 — Wrong resample ratio across the swap · **CRITICAL** → *eliminated by the format stamp*

**Mechanism.** `push_system` resamples with `self._wasapi_rate` /
`self._wasapi_channels` (`timeline_mixer.py:55-57`). A chunk captured on one
device and resampled with the other device's parameters is pitch-shifted and
length-distorted. **Measured on the real function**, 440 Hz / 48 kHz stereo / 1.000 s:

| Told | Duration out | Peak | Raised? |
|---|---|---|---|
| 48 kHz, 2 ch *(correct)* | 1.000 s | 440.0 Hz | — |
| 44.1 kHz, 2 ch | 1.088 s | 404.3 Hz | **no** |
| 48 kHz, **1 ch** | **2.000 s** | **220.0 Hz** | **no** |

**Why it is the worst risk in this document.** Nothing raises, no counter moves,
the transcript keeps flowing — and Deepgram transcribes half-speed audio
fluently and wrongly. That is this project's signature failure: *looks healthy,
output wrong*. A wrong channel count is materially worse than a wrong rate, which
revision 1 had backwards.

**Mitigation — construction, not discipline.** Stamp `(pcm, channels, rate)` onto
every chunk at the reader and resample with the stamp. Then no chunk can ever be
resampled with another device's parameters, regardless of thread timing, ordering,
or a future refactor. See §2.

**Do not rely on drain-then-reconfigure ordering.** That was revision 1's answer,
and it required the swap thread to touch a mixer that has no lock — a data race
that would have produced this very corruption. Ordering is a convention; a stamp
is a fact carried with the data.

**Verification.** The table above, as a test: a known tone through a simulated
48 kHz stereo → 44.1 kHz mono swap, asserting each half is bit-identical to
resampling it alone. Assert on samples, not duration — duration alone passes the
wrong-channel case at some rates.

---

### R2 — In-flight chunks in `sys_audio_queue` · **CRITICAL** → *eliminated by the format stamp*

**Mechanism.** `ingest_queues` drains the queue with `get_nowait()` until empty
(`timeline_mixer.py:133-139`), on the mixer thread. At a swap the queue holds
old-device bytes; unstamped, they are resampled with whatever format is current.

`MAX_AUDIO_QUEUE_SIZE = 100` (`alpha/config.py:47`) and `put_bounded` **drops the
oldest item** when full (`alpha/utils/queues.py:6-18`), so a swap that stalls the
mixer also loses old audio silently.

**Mitigation.** The same stamp. Every queued chunk carries the format it was
captured with, so draining order stops mattering entirely — which is what removes
the need for the swap thread to touch the queue at all, and with it the P1 race.

Count dropped chunks during a swap into the evidence event; a silent drop under
`put_bounded` is indistinguishable from quiet audio otherwise.

---

### R3 — The mixer's 3-second buffer straddles the swap · **HIGH**

**Mechanism.** `_sys_buffer` holds up to `MAX_BUFFER_SAMPLES = DEEPGRAM_SAMPLE_RATE * 3`
(`timeline_mixer.py:13`). Its contents are *already resampled* to 16 kHz, so they
are format-safe — but they are also **up to 3 seconds of the old device's audio**
that will be mixed into frames emitted after the swap.

**With the stamp, this is no longer a correctness risk** — the buffered samples
are already 16 kHz mono and resample-safe. It is purely a latency-vs-content
choice. Decide explicitly, and write the decision down:
* **Play it out** (preferred): keep draining `_sys_buffer` normally, accept up to
  3 s of latency across the swap, lose nothing.
* **Discard it**: lose up to 3 s of speech. Only acceptable if the swap is a
  response to the old device being *already dead*.

Whichever is chosen, do not leave it implicit — an implicit choice here is a
silent content loss or a silent 3-second lag, and neither is diagnosable later.

**Verification.** Measure `_sys_buffer` occupancy at swap entry and record it in
the swap evidence event.

---

### R4 — PortAudio termination races the reader · **HIGH**

**Mechanism.** Step 5 calls `self._pyaudio.terminate()` (`wasapi.py:368-372`). If
the reader thread is still inside `stream.read()`, terminating the instance under
it is a use-after-free at the C layer — a hard process crash, not a Python
exception.

**Mitigation.** Step 2 must **join the reader thread**, not merely signal it. The
existing close path joins with `timeout=1.0` (`wasapi.py:345-347`) and then
proceeds *regardless* — acceptable at shutdown, **not** acceptable mid-session.
If the join times out, abort the swap and keep the old device rather than
terminating PortAudio under a live reader.

**Verification.** Assert the reader thread is not alive before `terminate()` is
called. This must be an assertion in production code, not only in a test.

---

### R5 — The reader thread cannot be restarted · **HIGH** *(pre-existing)*

**Mechanism.** `_wasapi_reader_worker` catches its exception and then `break`s
(`wasapi.py:246-255`). The thread ends. `_start_wasapi_loopback` is called only at
session start (`main_window.py:10323`), so nothing brings it back. A read error at
any point today already means system audio is gone for the rest of the session,
silently.

**Why it matters here.** A device swap is precisely when a read error is most
likely — the device is being pulled out from under an open stream.

**Mitigation.** Fix this **before** the feature, not with it. The supervisor from
`mitigation.md` step 1 (`alpha/utils/supervised_thread.py`) already exists and is
the right mechanism; the reader must raise rather than `break` so the supervisor
can see the failure. See §5.

**Note for the audit.** This is a ninth instance of the unrecoverable-failure class
and `tools/audit_unrecoverable_latches.py` **did not report it**: scan 2 checks
whether the loop body *can swallow* an exception, and this one can — it just
`break`s afterwards. A handler that exits the loop is the same outcome as no
handler. The scan should treat `break`/`return` inside the handler as
"unsupervised". That is a real gap in the step-4 guard, independent of this
feature.

---

### R6 — The silent gap looks exactly like item 80 · **HIGH**

**Mechanism.** Between steps 2 and 8 no system audio is captured. Deepgram receives
silence (or nothing). Item 80 was precisely this failure mode arriving by accident,
and it cost word recall 93.5% → 42.9%.

**Mitigation.**
* **Measure the gap, do not estimate it.** Record `swap_gap_ms` from the last
  chunk enqueued before the swap to the first chunk after, and put it in the
  evidence stream.
* Set a budget (suggested: **250 ms**) and treat exceeding it as a reportable
  event, not a silent fact.
* The mic keeps running throughout — it is a different library and is not stopped
  — so in a meeting where the operator is speaking, the gap is partial, not total.

**Verification.** Against the retained WAVs, exactly as item 80 was measured:
compare delivered silence in `mixed_*` against the `system_*` capture across the
swap. Do not accept a synthetic measurement here; that mistake is already on the
record in `CLIENT_DELIVERY_SPRINT_v5.md`.

---

### R7 — Item 73 reports a false device change · **MEDIUM**

**Mechanism.** `_wasapi_device_watch_worker` compares the live default endpoint
against `_wasapi_default_endpoint_baseline`, set once at stream open
(`wasapi.py:268`). After a deliberate swap the baseline is the *old* device, so the
watcher immediately reports a device change that the operator just made
themselves — and latches `_wasapi_device_change_reported`.

**Mitigation.** Step 9: re-baseline and clear the reported latch as part of the
swap, inside the same critical section that opens the new stream. The un-latch
logic itself is already correct (`wasapi.py:200-206`) and must not be duplicated.

**Verification.** After a swap, assert no `device_changed` event is emitted for the
device that was just swapped *to*.

---

### R8 — The source gate misreads the swap as a source change · **MEDIUM**

**Mechanism.** `TeamsSourceGate` holds a source decision for `SOURCE_HOLD_MS = 500`
(`alpha/constants.py:249`, applied at `source_gate.py:112/126`). A swap that
produces a burst of mic-only frames followed by a burst of system frames can flip
the gate's source decision and hold the wrong one for half a second.

**Mitigation.** Suppress gate transitions while `_system_capture_swapping` is set,
and reset the gate (`self._source_gate.reset()`, already called from
`TimelineMixer.reset`, `timeline_mixer.py:39`) as part of step 10 rather than
letting it infer its way back.

**Verification.** Replay a swap through the gate with a simulated clock advancing
at the real cadence — **20 ms per frame**. A replay that passes real
`time.monotonic()` makes every hold and debounce behave differently and will
exonerate the gate incorrectly; that exact mistake is recorded in
`CLIENT_DELIVERY_SPRINT_v5.md` for the 2026-08-20 item 80 work.

---

### R9 — The swap flag becomes a latch · **MEDIUM**

**Mechanism.** `_system_capture_swapping` is consulted by the reader, the mixer
and the gate. If any path returns early — an exception between steps 5 and 8, an
operator stopping the session mid-swap — it stays `True` and system audio is off
for the rest of the session with every component reporting healthy.

**Mitigation.** This is the class `mitigation.md` documents. Non-negotiable:
* set and clear it in `try/finally`, never on the happy path only;
* give it a **bounded auto-clear** — if it has been set for more than N seconds,
  clear it and report, the same shape as `_COMMIT_GATE_MAX_CONSECUTIVE_REJECTS`;
* it must appear in the health payload, so a stuck swap is visible.

**Verification.** It must be **allowlisted with an executable reason** in
`tests/test_no_unrecoverable_latches.py`, or the step-4 guard will fail — which is
the guard doing its job. Do not add a prose-only entry.

---

### R10 — Evidence and audio-temp continuity · **MEDIUM**

**Mechanism.** The run writes per-run WAVs (`audio_temp/system_audio/`,
`mic_audio/`, `mixed_audio/`) in numbered chunks. A swap changes the source
mid-file. Nothing in the WAV or the manifest says so, and a later accuracy
investigation would compare across a boundary it cannot see — the same class of
mistake as `CANONICAL_KEY_FIELDS_AUDIT.md` §5b, where a measurement was taken from
a file that was never designed to carry the field being measured.

**Mitigation.** Write one `SYSTEM_AUDIO_DEVICE_SWAPPED` evidence record carrying:
old device id, new device id, old/new rate and channels, drained queue count,
`_sys_buffer` occupancy, measured `swap_gap_ms`, and the audio chunk index on both
sides. Then a future investigation can see the boundary instead of averaging
across it.

---

### R11 — Deepgram interim/final straddling the gap · **MEDIUM**

**Mechanism.** Deepgram may be mid-utterance when the audio stops. The utterance
completes with a truncated tail, and the next one starts from the new device. The
assembler's continuity logic (`japanese_sentence_assembler.py`) will try to merge
across a boundary that is acoustically discontinuous.

**Mitigation.** Do **not** try to make this invisible to the assembler — that is
exactly the "hold and hope" shape that produced item 94. Instead, at step 11, call
the assembler's existing boundary path so the swap is a deliberate utterance
boundary rather than an accidental merge. `flush_japanese_sentence_assembler` is
stop-only today; a non-stop boundary reason would need adding, and it must NOT set
`_stop_boundary_active` (see the allowlist entry for that flag — it is currently
safe *because* only stop paths reach it).

**Verification.** A swap mid-utterance must produce two records, not one merged
record containing audio from two devices.

---

### R12 — Nothing tells the operator what happened · **MEDIUM**

**Mechanism.** A swap that half-works — new device opens but produces no audio,
say, because it is a virtual device with no signal — looks identical to a quiet
room. This project's own §4 rule: *silence reads as health*.

**Mitigation.** After step 10, require **positive confirmation**: the first system
chunk from the new device must arrive within a budget (suggested 2 s). If it does
not, report it in the UI and in the evidence — do not wait for the operator to
notice the transcript has stopped.

---

### R13 — Repeated swaps · **LOW-MEDIUM**

**Mechanism.** An operator clicking through devices, or a dock renegotiating,
produces swaps back to back. Each one terminates and recreates PortAudio — an
expensive, C-level operation — and each one opens a gap.

**Mitigation.** Debounce at the entry point (suggested: ignore a swap request
within 2 s of the last one) and cap swaps per session with a reported counter.
The cap must be **bounded and reported**, never silent.

---

### R14 — The swap runs on the Tk main thread · **LOW-MEDIUM**

**Mechanism.** If triggered from a UI control, the whole sequence — including
`terminate()`, device enumeration and `open()` — would block the Tk main loop for
its full duration. `ui_heartbeat_age_ms` would spike and the freeze guard would
correctly report a UI stall.

**Mitigation.** Run the swap on a worker thread; the UI control only posts the
request. This repo already has the pattern and the guard for it
(`alpha/utils/tk_thread_guard.py`, `background_tk_call_blocked_count`), and the
worker must not touch Tk directly.

---

### R15 — Device disappears between enumeration and open · **LOW**

**Mechanism.** Steps 6 and 8 are not atomic. A device unplugged in between makes
`open()` fail.

**Mitigation.** The one-way-door policy in §2. Note that "retry the previous
device" is NOT available (P3) — after `terminate()` the only thing that can be
opened is whatever the OS now calls the default. Bounded retries of *that*, then
degrade and report. Never a session kill.

---

### R16 — The swap worker touches the mixer · **CRITICAL** *(the plan's own defect)*

**Mechanism.** `TimelineMixer` has no lock; `audio_mixer_worker` is its sole owner
and drains `sys_audio_queue` itself. Any swap implementation that drains the queue
or calls `configure_sources()` from the swap thread is racing unsynchronised numpy
buffer mutation — and the corruption it produces is R1.

This is recorded as a risk rather than just fixed silently because it was
**revision 1's own design**. The next person to write a "quick" version of this
feature will reach for the same shape.

**Mitigation.** The ownership split and the format stamp in §2. If a future change
does need cross-thread mixer access, the mixer needs a lock **first** — and adding
one is its own review, because `emit_due_frames` runs at 50 Hz.

**Verification.** A test that asserts no thread other than the mixer worker calls
`push_system` / `ingest_queues` / `configure_sources`, in the same shape as the
existing `background_tk_call_blocked_count` guard.

---

### R17 — `configure_sources` is read once, into locals · **HIGH**

**Mechanism.** `main_window.py:8551` calls it **before** the mixer loop, having
read `self._wasapi_channels` / `self._wasapi_rate` into local variables at
`:8548-8549`. Setting those attributes mid-session changes nothing — the worker
already captured them.

**Why it matters.** An implementation that "updates the format" by assigning the
attributes will appear to work, log nothing, and keep resampling with the old
device's parameters indefinitely: R1 with no swap-time symptom at all.

**Mitigation.** The stamp makes the attributes defaults-only, so this stops being
load-bearing. If they are kept for anything else, they must be read per chunk, not
hoisted.

---

## 4. What NOT to do

Recorded because each is a plausible shortcut that would cost more than the
feature is worth:

* **Do not reconnect Deepgram.** §0. It buys nothing and costs transcript
  continuity, which is the entire point of the feature.
* **Do not reuse `stop()` + `start()` internally.** They tear down the run folder,
  the canonical ledger and the evidence streams. The transcript would survive in
  the UI and not in the export — the worst of both.
* **Do not infer the swap from the item 73 watcher.** That watcher detects an
  *unrequested* change and is a diagnostic. A user-requested swap must be an
  explicit operation with an explicit result; conflating them means neither can be
  reported honestly.
* **Do not make the swap invisible to the assembler.** R11.
* **Do not drain the queue or call `configure_sources()` from the swap thread.**
  R16. It is the obvious implementation and it is a data race.
* **Do not "update the format" by assigning `self._wasapi_rate`.** R17. It is read
  once into a local before the loop and your assignment will do nothing.
* **Do not promise the operator a rollback.** P3 — after `terminate()` there is
  none. Say "system audio unavailable, restart to recover" rather than implying a
  retry that cannot happen.
* **Do not ship this before R5.** A feature whose most likely failure mode lands
  on a thread that cannot restart is a feature that turns a recoverable annoyance
  into a dead session.

---

## 5. Prerequisite — fix R5 first

The reader thread must be restartable before the swap feature is written, for the
reason in R5. Scope:

1. `_wasapi_reader_worker` raises instead of `break`ing on an unexpected read
   error (a clean stop via `_stop_event` must still return normally, or the
   supervisor will restart a thread that was deliberately stopped —
   `mitigation.md` A6 covers exactly this).
2. Spawn it through `SupervisedThread`, with its **own stop event** — the device
   watch thread already documents why sharing `self._stop_event` is wrong
   (`wasapi.py:300-307`): `start()` clears whatever event it is given, which would
   un-stop the mixer and the watcher too.
3. Restart count and `gave_up` into the health payload, as the other five
   supervised loops already are.
4. Separately, fix the scan-2 blind spot in
   `tools/audit_unrecoverable_latches.py`: a handler containing `break` or
   `return` ends the thread just as surely as no handler, and must be reported.

Expect that fix to surface other loops with the same shape. That is the guard
working, not new breakage.

---

## 6. Test plan

Mirroring `mitigation.md` §3, because those criteria were written after this repo
shipped a green test over a dead code path twice.

1. **Every new test must fail against the pre-fix tree.** A test green on both
   sides proves nothing — that is how item 60 shipped with its latch intact.
2. **Format correctness (R1) by replay, not by inspection.** A known tone through
   a 48 kHz stereo → 44.1 kHz mono swap, output compared sample-for-sample against
   each half resampled separately.
3. **Gap measurement (R6) against the retained WAVs**, the way item 80 was
   measured. Not a synthetic estimate.
4. **The gate replay (R8) must advance a simulated clock at 20 ms per frame.**
5. **Swap under load**: run it while the mixer is at maximum buffer occupancy and
   the assembler is mid-utterance. The interesting failures are all at the
   boundary, not at idle.
6. **Failure paths**: device vanishes between enumerate and open; `open()` throws;
   reader join times out. Each must leave a working session and a cleared
   `_system_capture_swapping`.
7. **Full suite twice, concurrently**, comparing the SET OF FAILING NAMES only.
8. **The step-4 guard must stay green**, with `_system_capture_swapping`
   allowlisted with an executable reason.
9. **Assert on samples, not durations** (R1). The wrong-channel case produces a
   signal of a plausible length at some rate pairs; only a sample-level or
   spectral comparison catches it. The measured table in R1 is the fixture.
10. **A thread-ownership test** (R16): nothing but the mixer worker may call
    `push_system` / `ingest_queues` / `configure_sources`.
11. **The abort window** (§2): a failure injected *before* `terminate()` must
    leave the old stream capturing, and a failure injected *after* it must leave
    a live session with system audio reported unavailable — not a dead session and
    not a silent one.

---

## 7. Suggested sequencing

| Stage | Content | Why this order | Shippable alone? |
|---|---|---|---|
| 0 | **R5** — supervised, restartable reader; plus the scan-2 blind-spot fix in the audit tool | The feature's most likely failure lands on a thread that currently dies for good | **Yes** — a real reliability fix with no swap feature at all |
| 1 | **Per-chunk format stamp** (R1, R2, R16, R17) | Makes the swap correct *by construction*. Also fixes a live latent hazard: the mixer's format is shared mutable state read on one thread and written on another, safe today only because nothing writes it after startup | **Yes** — behaviour-neutral today, verifiable by the tone test |
| 2 | `swap_system_audio_device()` on a worker thread, evidence event, no UI | Testable end to end without touching the UI | Yes |
| 3 | Positive confirmation (R12), debounce and cap (R13) | Turns "it swapped" into "it is working" | Yes |
| 4 | UI control | Last, and see the note below | — |

**Stages 0 and 1 are worth shipping even if the swap feature is cancelled.** That
is the test of whether the sequencing is right: each stage stands on its own, and
nothing is a stub waiting for the next stage to justify it.

**On the UI**: the same caution as the B2 quota button in `mitigation.md` applies —
the responsive hamburger and the reading-pane layout are the areas items 71, 92 and
93 have churned most. Stage 2 delivers the capability and is fully testable without
it; whether the control is worth that risk is a separate decision, and should be
taken separately.

---

## 8. Open questions — these change the design, so answer before stage 2

1. **Follow the OS default automatically, or an explicit picker in Alpha?**
   Automatic is what the item 73 watcher already detects and needs no UI, but it
   means the app changes capture when Windows does — including for a notification
   sound routing change the operator did not intend. A picker is explicit but needs
   stage 4.
2. **R3 — play out the 3-second buffer, or discard it?** Latency versus content
   loss. My recommendation is play it out; the answer differs if the usual trigger
   is a device that has already failed.
3. **Should a swap force an utterance boundary (R11)?** My recommendation is yes.
   It costs one extra record and removes a whole class of merged-across-devices
   accuracy questions.
4. **Is the mic in scope?** This document is speaker-only. The mic path is a
   different library and would be a separate design; nothing here covers it.
