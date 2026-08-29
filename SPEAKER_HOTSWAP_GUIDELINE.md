# Changing the speaker mid-session — design guideline and risk register

**Goal.** Today, changing the Windows default playback device (the "speaker")
requires stopping Alpha and starting a new session. The transcript, the run
folder and the evidence all restart with it. The ask is to change the speaker
**while a session is running**, with the transcript continuous across the change.

**Status:** design only. No production code has been changed for this.

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

### Ordering — the whole feature is in the order

Every step below exists because doing it later, or not at all, corrupts
something specific. The rationale is in the risk register.

```
 1. ENTER SWAP STATE        set _system_capture_swapping = True
                            (readers stop enqueuing; mixer keeps draining)
 2. QUIESCE THE READER      stop the old stream, join the reader thread
                            with a bounded timeout
 3. DRAIN IN-FLIGHT AUDIO   empty sys_audio_queue COMPLETELY before any
                            format change  -- R2
 4. FLUSH THE MIXER         let _sys_buffer play out or discard it explicitly
                            -- never mix across a format change  -- R3
 5. RELEASE PORTAUDIO       terminate PyAudio  (C1: the only way to see the
                            new device)
 6. RE-ENUMERATE            new PyAudio(), resolve the new default loopback
 7. RECONFIGURE THE MIXER   configure_sources(new_channels, new_rate)
                            BEFORE a single new chunk is pushed  -- R1
 8. OPEN + START            open the stream, start the reader
 9. RE-BASELINE ITEM 73     _wasapi_default_endpoint_baseline = new id
                            _wasapi_device_change_reported = False   -- R7
10. LEAVE SWAP STATE        _system_capture_swapping = False
11. RECORD THE GAP          emit one evidence event with the measured
                            silent-gap duration  -- R6, R12
```

**Step 7 before step 8 is the single most important ordering constraint in this
document.** Reversing them means the first chunks of the new device are resampled
with the old device's rate — see R1.

### Failure policy

A swap can fail (the new device disappears between enumeration and open, PortAudio
refuses, the operator unplugs a dock). The policy must be decided up front:

* **Try the previous device again**, once, with the previous parameters.
* If that also fails: **stay in a clearly-reported degraded state** — mic-only
  capture continues, the UI says system audio is unavailable, and the session is
  NOT killed. Killing the session on a failed swap is worse than the problem the
  feature solves.
* Never leave `_system_capture_swapping = True` on any path. It is a latch, and
  this repo has spent a week on that exact class — see `mitigation.md`.

---

## 3. Risk register

Severity is about what the *client* loses, not about implementation difficulty.

### R1 — Wrong resample ratio across the swap · **CRITICAL**

**Mechanism.** `push_system` resamples using `self._wasapi_rate` /
`self._wasapi_channels` (`timeline_mixer.py:55-57`). If a chunk captured at 48 kHz
stereo is pushed after `configure_sources()` has been told 44.1 kHz mono — or the
reverse — the audio is pitch-shifted and length-distorted. It is **not** silence
and **not** an exception. Deepgram transcribes it confidently and wrongly.

**Why it is the worst one.** It is invisible. Every counter stays green, the
transcript keeps flowing, and the words are wrong. That is the same signature as
this project's top-5 defect list: *looks healthy, output wrong*.

**Mitigation.**
* Steps 3, 4 and 7 of the ordering — drain the queue and flush the mixer before
  the format changes, and reconfigure before the first new chunk.
* **Stamp the format onto the chunk.** Do not rely on ordering alone; enqueue
  `(chunk, channels, rate)` and have `push_system` resample with the values that
  came *with the chunk*. Ordering can be broken by a future refactor; a stamped
  chunk cannot be resampled with someone else's rate.
* Assert in `push_system` that the stamped rate matches the configured rate, and
  count mismatches into the health payload rather than silently accepting them.

**Verification.** Replay a known tone through a simulated swap from 48 kHz stereo
to 44.1 kHz mono and assert the output samples are bit-identical to resampling
each half separately. A duration check alone will not catch a channel-count error.

---

### R2 — In-flight chunks in `sys_audio_queue` · **CRITICAL**

**Mechanism.** The reader `put_bounded`s into `sys_audio_queue` (`wasapi.py:235`)
and the mixer drains it. At the moment of a swap the queue holds old-device bytes.
If the format changes while they are still queued, they are resampled wrongly —
R1 by another route.

**Mitigation.** Step 3: drain the queue to empty *before* step 7, and count what
was drained. Combined with the per-chunk format stamp (R1), draining becomes a
belt-and-braces measure rather than the only defence.

**Verification.** Assert `sys_audio_queue.qsize() == 0` at the entry to step 7, and
that the drained count is reported.

---

### R3 — The mixer's 3-second buffer straddles the swap · **HIGH**

**Mechanism.** `_sys_buffer` holds up to `MAX_BUFFER_SAMPLES = DEEPGRAM_SAMPLE_RATE * 3`
(`timeline_mixer.py:13`). Its contents are *already resampled* to 16 kHz, so they
are format-safe — but they are also **up to 3 seconds of the old device's audio**
that will be mixed into frames emitted after the swap.

**Mitigation.** Decide explicitly, and write the decision down:
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

**Mitigation.** The failure policy in §2: one retry on the previous device, then a
reported degraded state. Never a session kill.

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

---

## 7. Suggested sequencing

| Stage | Content | Why this order |
|---|---|---|
| 0 | R5 — supervised, restartable reader + the scan-2 fix | The feature's most likely failure lands here |
| 1 | Per-chunk format stamping (R1) with the assertion and counter | Makes the swap safe by construction rather than by ordering |
| 2 | `swap_system_audio_device()` on a worker thread, evidence event, no UI | Testable end-to-end without touching the UI |
| 3 | Positive confirmation (R12), debounce and cap (R13) | Turns "it swapped" into "it is working" |
| 4 | UI control | Last, and see the note below |

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
