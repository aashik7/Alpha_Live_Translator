# Proactive Full-Codebase Audit — 2026-08-06

> **Working on this project? Start with `BUG_FIX_ROADMAP.md`, not this
> file.** That file is the execution plan and the live ledger of what is
> done vs. pending, and it tells you exactly which item to pick up next.
> **This file is the evidence base** — it explains *what is wrong and
> why* for each `§N.N` item the roadmap references, and carries a
> `STATUS:` line per item recording whether it is fixed, mitigated, or
> still open. Read the item here, then execute per the roadmap's method.
>
> Related: `Bug Report.md` is a frozen 2026-08-06 snapshot of this same
> audit with **no** STATUS updates (do not use it for current state), but
> it uniquely carries the appended **Japanese content loss** findings
> (items 4.1-4.4) from 2026-08-07.
>
> **Fixes landed after this audit was written** (each annotated in-place
> at its item below): `d7c1834`, `25a6623`, `1a32639`, `98a6fa0`,
> `432dea1`, `78eb59e`, `5c48847`, and `a5e2ac4` — the last of which fixed
> a *third* independent source of the interim ghost-line symptom found by
> live test after §1.2's fix shipped (see §1.2's status note).

STATUS: Analysis only. No code changed. Six parallel file-scoped audits
(main_window.py, deepgram_client.py+event_bus.py, utterance_lifecycle.py+
revision_metadata.py+speaker_boundary_guard.py, duplicate_protection.py+
canonical_transcript_ledger.py+pipeline_commit_transaction.py,
japanese_sentence_assembler.py+japanese_boundary_stabilizer.py,
transcript_store.py+transcript_snapshot_store.py+stop_finalize_worker.py)
cross-referenced against ROOT_CAUSE.md, REPAIR_PLAN.md, and live run logs
in `troubleshooting/runs/v3.3.5.5.8.5.26.5.3-20260806-020103/` (primary)
plus `-140752`, `-154313`, `-020102` (secondary). Findings below are
merged, deduplicated, and ranked by severity then confidence.

> **Recovery note (2026-08-07):** this file was found missing from disk
> when the user asked to add a deferred item to it — it had never
> actually been committed to git despite being reported written on
> 2026-08-06. Recreated verbatim from this session's own conversation
> history (the original file content is preserved in that transcript),
> then the new item below was appended. If anything else from the
> original file seems to have gone missing, flag it and it can be
> cross-checked against that same transcript.

Legend — Confidence: **LOG** (confirmed by run-log evidence) / **STATIC**
(confirmed by code read, no matching log line either way) / **SUSPECTED**
(plausible, not verified). Severity: **SILENT-LOSS** (content
disappears/corrupts with no trace) / **VISIBLE-BUG** (wrong but the user
can see something's off) / **COSMETIC** (dead code, complexity, doc drift).

---

## Priority 1 — Confirmed by live logs, silent content loss

### 1.1 `utterance_lifecycle.py` revise/supersede subsystem: 0% success rate in every sampled session
**File:** `alpha/transcription/utterance_lifecycle.py:983-1036, 1204-1273, 1275-1341, 1541-1656, 1658-1793, 1795-1920`
**Confidence: LOG** (all 3 runs with commit data, 138 total canonical commits)
**Severity: SILENT-LOSS — highest-impact finding of the whole audit**

`evidence_streams/canonical_commits.jsonl`'s `applied_action` is `"append"`
100% of the time across 138 commits — **never once `"revise"`**. This is
the exact scenario REPAIR_PLAN.md's Phase 2 acceptance gate names
explicitly ("My" / "My name" / "My name is Tariqul" must collapse to one
line) — and it is failing on every sampled session, despite five prior
targeted fixes (BUG-D, BUG-E, DIAGNOSTIC-H, BUG-G1, `_merge_lexical`
overlap fix) landing on this exact code.

Root cause: `_commit_locked` registers identity via `observe_identity` but
**never calls `assign_canonical_record_id`** — that call only happens in
`accept_boundary_proposal` (line 582) and downstream in
`duplicate_protection.py:484`. `resolve_canonical_record_id` can therefore
return empty for a window after commit if the downstream ledger-assignment
call hasn't landed yet. Direct evidence: run `-020103`, utterance `U-32`,
`02:07:20.118` — `IDENTITY_REJECTION reason=no_exact_revision_target` and
`reason=missing_exact_extend_target_falling_back_to_new` both fired for
the *same* utterance, immediately before it was re-created as a brand-new
append instead of being corrected/extended. `japanese_accuracy.log` run
`-154313` corroborates: `CORRECTION_GATE_TEXT_MISMATCH`×91,
`CORRECTION_GATE_TIMING_MISMATCH`×28,
`CONTINUATION_GATE_REASON_MISMATCH`×24,
`CONTINUATION_GATE_TIMING_MISMATCH`×14.

**Failure scenario:** every timeout-fallback commit or premature chunk
that should be corrected/extended into the previous line instead silently
becomes its own new, separate visible line — progressive-line duplication
the repair history was specifically trying to eliminate.

**STATUS (2026-08-07): mitigated in two stages, both committed.**
- `utterance_lifecycle.py::_resolve_correction_target_locked` — bounded
  retry (3 attempts, ~60ms apart, `self._lock` released/reacquired during
  each sleep) — commit `98a6fa0`.
- `duplicate_protection.py::_display_transcript_item` — a second,
  independent `resolve_canonical_record_id` call in the real English-path
  ledger commit had no retry at all; new live evidence showed it as the
  actual remaining bottleneck (38/38 commits still `"append"`, one
  utterance reached `source_version=22`). Fixed with a non-blocking
  re-queue (this call runs on the Tk main thread's ~200ms batch tick, so
  blocking sleep would freeze the UI) — item carries its own retry state,
  reinserted at the front of `main_window.py`'s
  `_transcript_ui_batch_buffer` to preserve chronological order — commit
  `432dea1`.

### 1.2 `main_window.py::_apply_final_interim_comparison` — confirmed still broken, stale ghost line
**File:** `alpha/ui/main_window.py:4288-4318`
**Confidence: LOG** (`troubleshooting/runs/.../logs/async_debug.log`, 8 of 59 sampled comparisons hit this path)
**Severity: VISIBLE-BUG (user-visible transcript corruption)**

This is the CONFIRMED-but-unfixed function named in the task brief.
Decision logic is pure containment (`in`), with `keep_interim` as the
fallback default whenever final and interim are unrelated text — there is
no "does this final belong to the same utterance as this stale interim"
check. Real captured example: a fresh final `"The complete"` commits while
a 291-char stale interim from a *previous* utterance
(`"So this conversation is a casual..."`) remains displayed, because
neither containment check matches and the function falls through to
`keep_interim`. Same anti-pattern class already fixed in
`duplicate_protection.py::decide_transcript_action()` and
`utterance_lifecycle.py::_merge_lexical()` — this is the third and last
of the three files the user named as confirmed instances, still open.

**Correction (2026-08-07):** re-investigation found the *specific*
mechanism originally suspected here — a wrong branch-check order causing
even the equal-text case to be mishandled — had actually already been
fixed by commit `5001275` (BUG-B) before this audit ran. The finding
above about the *fallthrough default for genuinely unrelated text* was
independently real and log-confirmed, and is a narrower, different defect
than the one originally named. Both are now resolved: identity-gated
unrelated-branch logic plus a liveness watchdog, commit `78eb59e` (see
`ISSUE_1_INTERIM_GHOST_LINE_FIX_REPORT.md` for full detail). Regression
tests: `tests/test_interim_ghost_line.py` (19 tests).

**Follow-up (2026-08-07, commit `a5e2ac4`):** a live controlled test
after `78eb59e` shipped found the *same visible symptom* still occurring
briefly (~1.5-1.9s, self-healing via the watchdog rather than permanent)
from a **third, independent source**: `utterance_lifecycle.py`'s Case C
commit path called `_apply_active_update_locked(...)`, which
unconditionally emitted an interim carrying the utterance's *final* text,
microseconds before committing that same text. Fixed with an
`emit_interim` parameter suppressed on the two commit call sites.
Regression tests: `tests/test_commit_path_interim_emit.py` (6 tests).
**This is the third distinct code path found producing one visible
symptom** (the others: this item's fallthrough default, and
`deepgram_client.py`'s raw+lifecycle double delivery, §3.9) — concrete
evidence for why `BUG_FIX_ROADMAP.md` orders the underlying
identity/controller work ahead of further symptom fixes.

**Remaining, related, still open:** the watchdog's
`INTERIM_GHOST_TTL_MS = 1500` is mis-calibrated — measured last-interim-
to-final gaps are 1635-1924ms (English) and up to 4063ms (Japanese), so
it now clears *legitimate* in-flight previews. See `Bug Report.md` §4.4
and `BUG_FIX_ROADMAP.md` Batch 1 item 2.

### 1.3 `deepgram_client.py` — canonical-key fields are mostly decorative at the ingestion boundary
**File:** `alpha/transcription/deepgram_client.py:1911-1934 (segment_metadata), 1547-1566 (event_id/deepgram_request_id)`
**Confidence: LOG** (`evidence_streams/provider_events.jsonl`, run `-020103`, records 1-6)
**Severity: VISIBLE-BUG / structural, feeds every downstream identity bug above**

Two of REPAIR_PLAN.md's six required canonical-key fields are effectively
constant, not per-event, despite superficially looking populated:
- `channel_index` is Deepgram's raw `[channel, total_channels]` pair.
  Session always requests `channels=1` (mono), so every event in every
  session carries the identical value — confirmed `[0, 1]` in run
  `-020103`, and inconsistently serialized as the *string* `"[0, 1]"` in
  run `-140752` (type instability on top of zero discriminating power).
- `event_id`/`deepgram_request_id`, passed to every final chunk, is
  Deepgram's **connection-level** `metadata.request_id` — constant for
  the whole WebSocket session. Confirmed directly: six genuinely
  different utterances (`U-1`...`U-6`, different speakers/timestamps) all
  carry the identical `deepgram_request_id` =
  `"019fd2df-2c63-7f50-88f2-750e90129afe"`.

`session_id`, `provider_utterance_id`, `canonical_utterance_id`,
`canonical_record_id`, `source_version` — zero grep matches for any of
these literal names anywhere in `deepgram_client.py`; none are minted or
forwarded here at all.

**Failure scenario:** any downstream code that trusts `channel_index` or
`event_id`/`provider_utterance_id` as a real disambiguator is trusting a
near-constant. This directly undermines the "exact channel match" safety
fix ROOT_CAUSE.md recorded for `utterance_lifecycle.py` (§1.6 below) and
is a plausible contributing cause to §1.1's identity-resolution race.

**STATUS: still open, not part of any fix in this engagement so far.**

---

## Priority 2 — Static-confirmed, silent content loss, high plausibility

### 2.1 `utterance_lifecycle.py::_dispatch_commit` — publish failure after commit is recorded as success
**File:** `alpha/transcription/utterance_lifecycle.py:2016-2047` (bare `except Exception: pass` at 2046)
**Confidence: STATIC** | **Severity: SILENT-LOSS**

By the time `_dispatch_commit` runs, `_commit_locked` has already
incremented `self._stats["canonical_commits"]` and marked the utterance
`committed=True` (lines 1608-1613) — internal bookkeeping believes the
commit succeeded. If the publish callback into host code (which does real
ledger work in `duplicate_protection.py::_display_transcript_item` and
can raise) throws, **the entire committed utterance vanishes from the
UI/translation pipeline with zero error, zero log entry, and no stats
correction.** No log evidence either way — this is a terminal step with
no second chance to recover the text if it fires.

**STATUS: fixed, commit `d7c1834`.** Added a best-effort
`jp_accuracy_log("DISPATCH_COMMIT_CALLBACK_FAILED", ...)` on failure;
swallow behavior unchanged (still non-fatal), only observability added.

### 2.2 `utterance_lifecycle.py` Case B — cross-channel/cross-speaker merge on ordinary overlap
**File:** `alpha/transcription/utterance_lifecycle.py:1038-1057` (`force_new` computation), contrast with Case C at `:1081-1132`
**Confidence: STATIC** | **Severity: SILENT-LOSS**

`force_new = not same_active and active is not None and active.committed`.
When an active **uncommitted** utterance exists and the new candidate is
on a different channel/speaker (`same_active=False`, `active.committed=
False`), `force_new` is `False` — so the merge branch runs unconditionally,
calling `_merge_lexical` and **overwriting `active.channel`/`active.
speaker` with the new event's values**. Case C (speech_final=True path)
correctly gates on `same_active` first; Case B does not — an asymmetry
between the two final-chunk cases the state machine is supposed to treat
symmetrically. Not a rare edge case: this fires on ordinary two-speaker
overlap in real meetings, which is common.

**STATUS: still open.**

### 2.3 `utterance_lifecycle.py:1160` — speaker-identity comparison fails open, inverting project-wide policy
**File:** `alpha/transcription/utterance_lifecycle.py:1160`
**Confidence: STATIC** | **Severity: SILENT-LOSS**

`int(active.speaker or 1) != int(speaker or 1)` — defaults *both* unknown
speakers to `1`, so two utterances with genuinely unset/different unknown
speakers are treated as the same speaker. Every sibling module
(`transcript_store.py`, `japanese_boundary_stabilizer.py`,
`stable_line_revision.py`, `stable_revision_decision.py`) instead uses the
shared `speakers_confirmed_same()` guard, which is documented fail-closed
("if either speaker is unknown, NOT considered confirmed-same" —
`TASK_2C_REPORT.md`). This file never imports that guard and implements
the opposite policy.

**STATUS: still open.**

### 2.4 `stop_finalize_worker.py::_confirm_transcript_commits` — drain check computed, never enforced
**File:** `alpha/utils/stop_finalize_worker.py:1116-1132` (feeds `commit_confirm_ok`, consumed at `:1520-1522`)
**Confidence: STATIC** | **Severity: SILENT-LOSS (finalization can report success on an undrained session)**

Computes `transcript_remaining`/`batch_remaining` and only **logs** them —
never compares to zero, never returns a value reflecting them, never
raises. `run_timed_step` judges success purely by "no exception, no
timeout," so `commit_confirm_ok` is effectively always `True`. Meanwhile
the correctly-computed real answer exists a few hundred lines earlier:
`ui_stop_drain_barrier.py:88-89` computes `"passed": transcript_remaining
== 0 and ui_bus_queue_remaining == 0` — but that boolean is discarded;
`"ui_transcript_drain"` isn't even in `_REQUIRED_SYNC_STEPS`
(`stop_finalize_worker.py:132-143`).

**Failure scenario:** a burst of pending transcript items at Stop leaves
the queue non-empty. The real drain check correctly computes `passed=
False` and logs `UI_TRANSCRIPT_DRAIN_INCOMPLETE`; the required-step check
logs the non-zero count but reports success anyway →
`compute_core_final_status()` can report `completed_pending_evidence_
package` with real un-drained transcript items still pending — directly
violating REPAIR_PLAN.md Phase 4's own acceptance gate ("Empty Stable
reconstruction cannot be marked completed"). Note the overall finalization
architecture is otherwise well-hardened (see §5.4) — this is one bad input
into an otherwise-sound gate.

**STATUS: still open.**

### 2.5 `transcript_snapshot_store.py::revise_last_transcript_snapshot` — positional revision, zero speaker check
**File:** `alpha/utils/transcript_snapshot_store.py:79-145`
**Confidence: STATIC, matches ROOT_CAUSE.md's own still-open flag verbatim** | **Severity: SILENT-LOSS**

Revises `_segments[-1]` — the literal last array element — with **no**
speaker/session/channel/identity check before overwriting it. Strictly
worse than `transcript_store.py`'s `update_last_segment`, which at least
filters by speaker while scanning. **Failure scenario:** Speaker A's line
commits (now last row); Speaker B's turn starts, and a stray/late revision
event for B fires before B's own append lands — the function silently
rewrites A's `stable_text`, marks A's row `"revised"`, chains a new row to
B via `revised_from_segment_id`. This is the "third transcript-storage
module" ROOT_CAUSE.md flagged `NEEDS_REVIEW` and never resolved.

**STATUS: still open.**

### 2.6 `main_window.py` — two independent UI-layer paths re-mint canonical identity outside the controller
**File:** `alpha/ui/main_window.py:5556-5891` (`_commit_transcript_item_to_store`, Japanese manual mode), `:5046-5140` (`_try_segment_repair`, English path)
**Confidence: STATIC** | **Severity: SILENT-LOSS / architectural**

`_commit_transcript_item_to_store` independently mints
`canonical_utterance_id` via `uuid.uuid4()` (lines 5689, 5721) in Japanese
manual mode and decides continuation-vs-new itself — never delegated to a
canonical controller. `_try_segment_repair` merges into
`store.get_last_segment()` ("whatever is currently last") scoped only by
a speaker check, with **no** `canonical_utterance_id`/`source_version`
passed to `store.update_last_segment` at all. This is the exact "last
active record" pattern ROOT_CAUSE.md says was fixed in
`canonical_transcript_ledger.py`, reappearing as an independent UI-layer
decision path that never touches canonical identity. Previously
undocumented — not covered by ROOT_CAUSE.md's existing debt notes.

**STATUS: still open.**

### 2.7 `duplicate_protection.py::_display_transcript_item` — speaker-only lookup, cross-channel collision risk (re-confirmed, doc is stale on function name)
**File:** `alpha/transcription/duplicate_protection.py:182-234`
**Confidence: STATIC** | **Severity: SILENT-LOSS**

ROOT_CAUSE.md's debt note names `get_last_segment(speaker_num)` — that
literal call is gone (replaced by `get_last_segment_if_active`, "Task 2F"),
**but the underlying defect is unfixed**: `get_last_segment_if_active`
(`transcript_store.py:115`) has the same speaker-only signature, no
channel/session key. With two channels both producing `speaker=1` (e.g.
system audio + mic, or two Teams participants), the lookup can return the
*other* channel's segment as `previous_text`. The `allow_previous_lookup`
gate mitigates partially but doesn't verify the returned segment's
channel/session. **Correction to ROOT_CAUSE.md:** the doc's *second* debt
bullet for this function (`already_committed` trust-gate "trusts an
upstream producer without independent verification") is **stale** — that
part is now independently re-verified via the identity registry
(`duplicate_protection.py:281-325`) and should be removed from the debt
list; only the speaker-only-keying bullet remains genuinely open.

**STATUS: still open.**

### 2.8 Unsafe store methods still called from 3 production sites despite safe variants existing
**File:** `alpha/summary/transcript_store.py:71-109` (unsafe `update_last_segment`/`get_last_segment`) vs `:115-156` (safe `..._if_active` variants)
**Call sites still using the unsafe version:** `duplicate_protection.py:156`, `main_window.py:4407` (interim-tail recovery), `main_window.py:5106` (`_try_segment_repair`)
**Confidence: STATIC** | **Severity: SILENT-LOSS (TOCTOU race)**

Decisions about *whether* to write are made using the safe,
speaker-confirmed lookup, but the actual writes go through the unsafe
reverse-scan-by-speaker method — a separate lock acquisition from the
check. Between check and write, another thread can append a segment for a
different speaker; the unsafe scan walks backward past it and silently
revises a stale, wrong row instead of failing closed. Simplest fix per
the auditing agent: delete/alias the unsafe methods so there's only one
safe code path (removes the whole bug class instead of requiring every
call site to remember to opt in).

**STATUS: still open.**

### 2.9 `main_window.py::_on_store_segment_updated` — the exact silent-drop bug the adjacent comment says was fixed
**File:** `alpha/ui/main_window.py:1362-1424`, called from `_commit_japanese_update_previous_segment:3805-3866`
**Confidence: STATIC** | **Severity: SILENT-LOSS**

The store mutation (line 3825) is already committed by the time this
function's three side effects run: `_remove_interim_line_from_display()`,
`_remove_translation_item_for_utterance(...)`, `submit_text_for_translation
(...)` — all three independently wrapped in `try/except: pass`, no
rollback. **Failure scenario:** old translation UI item is removed, then
`submit_text_for_translation` raises — the revised utterance is
committed, its old translation is gone, no new translation was ever
requested. This is precisely the "store.update_last_segment call followed
by side effects with no rollback" pattern ROOT_CAUSE.md flagged
`NEEDS_REVIEW` and left "pending a full read of
`_on_store_segment_updated`" — that read now shows the side effects don't
just lack rollback, they actively swallow the exceptions that would
reveal it. Ironic: the surrounding comment explicitly says this
restructuring was done *to avoid* silently dropping translations "with
zero trace."

**STATUS: still open.**

### 2.10 `deepgram_client.py` — Japanese stabilizer exception falls through into the English commit path
**File:** `alpha/transcription/deepgram_client.py:1472-1577`, specifically `:1533-1534`
**Confidence: STATIC** | **Severity: SILENT-LOSS (cross-language bleed)**

If `stabilizer.ingest(...)` raises for a Japanese-configured session, the
exception is caught and printed but execution does **not** return — it
falls through into the English/generic commit block a few lines later,
feeding the same Japanese final into `utterance_lifecycle.on_final_chunk`,
the English-only controller. A single transient stabilizer exception
silently reroutes one final transcript into the wrong language's pipeline
instead of being retried or fenced off.

**STATUS: FIXED, commit `6726f68` (2026-08-08).**

**Correction to the description above** — verified by control-flow trace
while fixing it, so no future reader takes the original wording at face
value. The fall-through was real, but the *common* outcome was **not**
reaching `utterance_lifecycle.on_final_chunk`:
`should_use_utterance_lifecycle()` independently rejects Japanese via its
own inner guard, so the fall-through skipped the lifecycle block and
landed on `_publish_final_transcript_segment` at the bottom — i.e. the
Japanese **continuity assembler was bypassed** and a raw, unassembled
Deepgram fragment was committed. The English-controller contamination
described above was the *rarer* path: it additionally required the
Japanese guard itself to be broken **and** `host._listen_language` to be
unset, so that `should_use_utterance_lifecycle()`'s own inner guard also
failed and its fallback lang check (defaulting to `""`, which does not
start with `"ja"`) returned True.

The fix splits the language-path decision and the stabilizer work into
separate try blocks; **neither** failure can now reach the
English/generic block — both publish the final directly (preserving the
spoken text) and log (`JAPANESE_STABILIZER_INGEST_FAILED` /
`JAPANESE_PATH_DETECTION_FAILED`). Closing the rarer path specifically
required the *detection-failure* branch to stop falling through as well.
Regression tests:
`tests/test_japanese_stabilizer_failure_no_english_fallthrough.py` (4).

---

## Priority 3 — Static/suspected, visible bugs and architectural gaps

### 3.1 Japanese speaker-relabeling can merge two speakers' turns (REPAIR_PLAN's explicit "never do this")
**File:** `alpha/transcription/japanese_sentence_assembler.py:3256-3329` (`_resolve_output_speaker`)
**Confidence: SUSPECTED** (zero `JAPANESE_SPEAKER_STABILITY_LOCK_APPLIED` events in any of the 3 sampled Japanese runs — enabled by default via `JAPANESE_STT_PROFILE = "no_diarize"`)
**Severity: VISIBLE-BUG, but exactly the failure mode REPAIR_PLAN forbids**

Can override the actually-detected speaker with a "locked"
baseline/dominant speaker when fewer than 3 consecutive votes exist for a
new speaker, or the previous speaker holds >80% share, or the fragment
"looks like a continuation tail" — including an explicit
`block_speaker2_to_speaker1_flip` rule. The *relabeled* speaker (not the
raw one) feeds the downstream same-speaker-extension check. In rapid
two-speaker dialogue, each of speaker B's individual turns may never
accumulate 3 consecutive votes, so B's turns get relabeled to dominant
speaker A at commit time — if the downstream extension check is satisfied
by the relabeled value, B's turn can be merged into A's previous line.
This is the specific outcome REPAIR_PLAN.md forbids ("Japanese dialogue
between two speakers must never become one merged canonical line"),
reached via speaker mislabeling rather than text concatenation. Not
independently confirmed — `stable_revision_decision.py` (out of scope)
might re-derive the true speaker downstream.

**STATUS: still open.**

### 3.2 Two direct-commit bypasses of the canonical controller still exist in the Japanese assembler
**File:** `alpha/transcription/japanese_sentence_assembler.py:3793-3831` (Stop-tail suppression path), plus persistence side-writes at `:4081-4246` that happen regardless of the controller's verdict
**Confidence: STATIC** | **Severity: VISIBLE-BUG / architectural**

Good news first: the main per-utterance commit path (`_publish_sentence`,
lines 3884-3904) **does now** propose to
`utterance_lifecycle.accept_boundary_proposal(...)` — REPAIR_PLAN.md
Phase 2's single-controller pattern is implemented for the primary path.
ROOT_CAUSE.md's blanket claim that "the assembler commits independently...
rather than proposing" is now only partially accurate.

But a narrower bypass remains exactly where ROOT_CAUSE.md said: the
Stop-listening incomplete-tail-suppression path (gated by `suppress_early`)
still calls `pipeline_commit_transaction.execute_pipeline_commit`
directly — comment in the code acknowledges this is intentional and
unchanged. Severity is limited in practice since this path's candidate is
usually suppressed and never published. Separately, even on the
"proposes to controller" path, the assembler still performs direct
persistence writes after the controller decision —
`transcript_snapshot_store` append/revise (§2.5's file, called from
`:4158-4246`, itself wrapped in a silent `except: pass`), stable-line
revision manager writes, and a direct call into host UI
(`_publish_final_transcript_segment`).

Cross-check: `accept_boundary_proposal` itself is confirmed to be a real
identity/lineage gatekeeper, not a rubber stamp (it enforces empty-text/
missing-identity rejection, channel/session/duplicate/stale-version checks,
exact-target resolution, and quarantines on identity-assignment failure)
— but it does **not** re-decide HOLD/EXTEND/COMMIT; that boundary
decision is made entirely upstream in the assembler. So this file is an
**identity/lineage gatekeeper**, not the **boundary-strategy decider**
REPAIR_PLAN.md's "single canonical controller" language calls for — this
matches and refines ROOT_CAUSE.md's existing debt item rather than
introducing a new one. Also note: `accept_boundary_proposal`'s rejection
paths have zero observed firings across all 4 sampled runs
(`ASSEMBLER_COMMIT_GATE_FAILED` = 0 everywhere) — whether it actually
blocks a bad proposal in practice is unverified by live evidence, only
its happy path is exercised.

**STATUS: still open (deferred architectural item, matches ROOT_CAUSE.md's own deferred-scope note).**

### 3.3 `japanese_boundary_stabilizer.py::duplicate_continuation_ratio` — substring-only suppression can drop genuinely new short remarks
**File:** `alpha/transcription/japanese_boundary_stabilizer.py:233-248`, consumed at `:583-618`
**Confidence: STATIC, no matching log event in 3 sampled runs** | **Severity: SILENT-LOSS**

Decides "is this just a duplicate continuation" purely via substring
containment (`cur_c in prev_c` / `prev_c in cur_c`) plus a positional
character-match ratio — not substitution-aware. ratio≥0.95, or (ratio≥0.7
and shorter), fully suppresses (`emit_now=False`). **Failure scenario:**
same speaker says a short but genuinely new remark whose compacted text
happens to be a literal substring of the previous line (e.g. previous
ends "...ありがとうございました", new remark is exactly
"ありがとうございました") → ratio=1.0 → new utterance silently dropped,
no recovery path.

**STATUS: still open.**

### 3.4 Cross-file anti-pattern instance count — same fragile prefix/containment revision-vs-new comparison, found in 7 places total
This is the recurring pattern the user explicitly asked to be hunted
across the whole codebase (Task 2). Consolidated list, most→least severe:

| # | File:line | Confirmed already-fixed? | Notes |
|---|---|---|---|
| 1 | `main_window.py:4288-4318` `_apply_final_interim_comparison` | **Yes — fixed 2026-08-07, commit `78eb59e`, see §1.2 correction above** | Was the one named in the task brief as confirmed-but-unfixed; the specific defect (unrelated-text fallthrough) is fixed via an identity gate + liveness watchdog. |
| 2 | `main_window.py:5492-5554` `_check_stop_tail_duplicate` | No | Stop-time last-chance commit; can silently classify a reworded tail as `skip_already_committed` and drop it entirely — highest severity of the `main_window.py` group. |
| 3 | `main_window.py:4332-4347` `_should_commit_interim_recovery` | No | Same pattern, Stop-time recovery path. |
| 4 | `main_window.py:4954-4997` `_should_repair_previous_segment` | No | Misclassifies reworded (not pure-substring) previous segments as non-continuation. |
| 5 | `utterance_lifecycle.py:159-174` `_text_related` | **Partially — threshold tightened 2026-08-07, commit `25a6623`** | 8-char shared-prefix / 50% overlap floor raised to 12 chars / 65% overlap, cutting the "two unrelated same-speaker/channel utterances share a common opening phrase" false-positive class. Still a text-only heuristic, not identity-based — residual ambiguity remains for genuinely same-speaker cases. |
| 6 | `duplicate_protection.py:49-81` `decide_transcript_action` | **Yes, but residual gap** | `or True` bypass confirmed removed. Two dead `.startswith()` branches remain (unreachable, since `in` two lines earlier already subsumes them — cosmetic). Real gap: substitution-style correction with no authoritative `lifecycle_decision` signal still falls to `"add"` (duplicate line) rather than `"update"` — mitigated in the common case by the upstream lifecycle signal override, but live when that signal is absent. |
| 7 | `utterance_lifecycle.py:110-115` `_merge_lexical` | **Yes, but residual gap → now closed, commit `1a32639`** | Just-fixed word-overlap threshold (0.6) was still order-blind — word-swap/reorder cases (e.g. "the cat sat on the mat" → "the mat sat on the cat") scored ≥0.6 overlap and got silently merged. Fixed 2026-08-07: added a `difflib.SequenceMatcher`-based word-order check on top of the existing overlap gate; reorder cases now fall through to the safe comma/period-join fallback instead of silently picking one variant. |
| 8 | `japanese_sentence_assembler.py:597-622` `merge_japanese_fragments` | No | Prefix/suffix containment accepted as proof of full subsumption before the smarter overlap-search fallback; a corrected-but-shorter retranscription that happens to be a literal suffix can be dropped. |
| 9 | `japanese_sentence_assembler.py:2573-2591` `_looks_like_speaker_continuation_tail` | No | Feeds §3.1's speaker relabeling; literal-prefix/phrase-containment list can misclassify a different speaker's line starting with an ordinary connective. |
| 10 | `deepgram_client.py:626-650` `teams_commit_decision_from_dup_action` | N/A — currently diagnostic-only | A **fourth independent classifier** of this exact bug class, living in the ingestion file, reimplementing containment logic and importing `normalize_for_compare` from `duplicate_protection.py` to build a competing verdict. Traced both call sites (`main_window.py:5727, 5785`): output currently only feeds diagnostic logging, not the real commit path. Risk: unlike its sibling diagnostic-only functions in the same file, this one's name doesn't signal "diagnostic-only" — a future change wiring its output into a real branch would reintroduce the confirmed root-cause bug pattern a fifth time. |

### 3.5 Ledger has no internal staleness defense — trusts callers entirely
**File:** `alpha/transcription/canonical_transcript_ledger.py:482-542` (`_revise_record_unlocked`)
**Confidence: STATIC** | **Severity: VISIBLE-BUG, contingent on §3.2's bypasses**

Unconditionally overwrites `target["final_text"]` — no comparison against
`source_version` inside the ledger itself. Version-ordering protection
exists only in `canonical_identity_registry.observe_identity` (outside
this file), called by `duplicate_protection.py` *before*
`execute_pipeline_commit`. Given §3.2 confirms at least one direct-commit
path still bypasses the proposal flow, a late-arriving stale revision
reaching the ledger through that path would silently overwrite newer text
with no internal defense — the module's own docstring calls it the
"single authoritative source," but it fully delegates ordering safety to
callers.

**STATUS: still open.**

### 3.6 Same decision (duplicate? valid revision target?) independently re-decided in 3+ places
**Files:** `duplicate_protection.py`, `canonical_transcript_ledger.py`, `pipeline_commit_transaction.py`
**Confidence: STATIC** | **Severity: COSMETIC today, architectural risk**

- **"Is this a duplicate?"** decided independently in `duplicate_protection.py` (text-equality/substring `skip`, plus the separate `already_committed` trust-gate) **and again** in `canonical_transcript_ledger.py`'s idempotency-index replay check **and again** upstream in `canonical_identity_registry.observe_identity`.
- **"Is this revision target/lineage valid?"** decided in `pipeline_commit_transaction.py` (`SINGLE_REVISION_AUTHORITY_ENABLED` gate) **and again** in `canonical_transcript_ledger.py::apply_decision` **and again** via `RAW_EVENT_LINEAGE_REQUIRED` in `_append_record_unlocked`.
- **"Stop-tail candidate cannot suppress an existing record"** enforced in `pipeline_commit_transaction.py` **and again** verbatim inside `canonical_transcript_ledger.py::apply_decision`.

None of these are contradictory today — they're redundant fail-safes, not
fail-opens — but the "single canonical controller" principle from
REPAIR_PLAN Phase 2 is not structurally true even within these three
files; `duplicate_protection.py` still makes real create/extend/ignore
decisions of its own rather than purely relaying an upstream verdict.

**STATUS: still open.**

### 3.7 `main_window.py::_begin_graceful_stop` — dead safety net, always throws, silently swallowed
**File:** `alpha/ui/main_window.py:7834-7872`, specifically line 7850
**Confidence: STATIC (arity mismatch, unconditional)** | **Severity: VISIBLE-BUG (currently masked, not currently causing loss)**

`self._flush_pending_translation_submit()` called with **zero arguments**,
but the method requires `key` (`main_window.py:6580`, no default) — this
call unconditionally raises `TypeError`, caught by a bare
`except Exception: pass`. The "flush any debounced Stable translation
before stop-accepting" safety step described in the adjacent comment has
never executed, on any Stop, ever. Currently non-fatal only because
`stop_finalize_worker.py:1600` separately calls the correct plural method
`flush_pending_translation_submissions()` later in the sequence — but
this is dead weight that fails every single time it runs, and if the
later correct call is ever removed/reordered, the safety net silently
isn't there.

**STATUS: still open.**

### 3.8 `transcript_store.py::add_translation` — exact-text match, silent drop on any revision race
**File:** `alpha/summary/transcript_store.py:158-179`
**Confidence: STATIC** | **Severity: SILENT-LOSS (translation, not source text)**

Matches an incoming translation to a segment by **exact text equality**
(reverse scan) plus optional speaker filter — not by record id. If the
segment's text is revised (by any of the update paths in this report)
between translation request and translated result arriving, the exact-
string match silently fails and the function returns with no log — the
translation is dropped, no exception, no trace. Two speakers saying an
identical short phrase ("Thank you.") with `speaker=None` can also
misattribute the translation to the wrong segment.

**STATUS: still open.**

### 3.9 `deepgram_client.py::_handle_interim_deepgram_result` — undocumented double delivery of every interim event
**File:** `alpha/transcription/deepgram_client.py:1683-1741` (as of 2026-08-07; `on_interim(...)` call ~1710-1717, raw `handler(...)` call ~1739-1741)
**Confidence: STATIC, traced via `git show 0a83a9c`** | **Severity: COSMETIC today, real latent risk**
**Found: 2026-08-07, during the Issue-1 interim-ghost-line fix — not part of the original 6-agent audit above.**

Every Deepgram interim result is forwarded to `on_interim_transcript`
**twice** per tick: once through `get_utterance_lifecycle(self).on_interim(
...)` (which attaches a real `canonical_utterance_id` via
`utterance_lifecycle.py::_dispatch_interim`), and once again directly via
the raw `handler(speaker_num, transcript, metadata=metadata)` call a few
lines later, whose `metadata` carries no identity at all.

`git show 0a83a9c` (BUG-G1/G2/H + interim wiring, an earlier session in
this engagement) confirms this was not designed as a duplicate-delivery
mechanism — the raw call already existed; that commit added the
`on_interim(...)` call *on top of it*, without removing or gating the
original. It is an artifact of an incremental fix layering on top of
earlier code, the same pattern that produced several other findings in
this audit.

**Why it can't simply be deleted:** the raw call is load-bearing for two
reasons, both confirmed in code:
1. **Japanese sessions never go through the lifecycle path at all** —
   `should_use_utterance_lifecycle()` (`utterance_lifecycle.py:2196-2210`)
   is English/generic-only by design ("Japanese keeps its stabilizer
   path"), so the raw call is Japanese's *only* route to
   `on_interim_transcript`.
2. **Even on the English path, the lifecycle's own interim dispatch is
   conditional** — `_dispatch_interim` only fires when a
   `LifecycleDecision.should_update_interim` is `True`, which several
   internal branches set to `False` (`utterance_lifecycle.py:1443, 1524,
   1665`) — so lifecycle alone does not guarantee delivery of every
   interim to the UI either.

**Currently harmless, but fragile:** `on_interim_transcript` only
overwrites `self._pending_interim` (idempotent), and
`INTERIM_UI_THROTTLE_MS` collapses the pair down to one actual render, so
there is no user-visible symptom today. It became directly relevant to
the Issue-1 fix (§1.2/commit `78eb59e`): the raw, identity-less call
landing *second* was silently discarding the identity the lifecycle call
had just attached, which is exactly what forced
`_apply_final_interim_comparison` to fall back to text-only comparison in
the first place. The fix preserves identity across the pair rather than
letting the second call erase it — see `on_interim_transcript`,
`deepgram_client.py:4219-4237` (post-fix line numbers).

**Failure scenario (latent, not yet triggered):** if a future change adds
a counter, metric, or any other side effect to `on_interim_transcript` /
`_handle_interim_transcript_ui`, it will silently double-count for every
English interim tick where lifecycle delivery also fires, because nothing
in the code signals that this is a known, intentional-by-necessity
duplicate path rather than a single clean delivery.

**STATUS: documented, not restructured.** A source-level fix would mean
making one path (not two) the single owner of UI interim delivery for
both languages — the same "single canonical controller" rewrite
ROOT_CAUSE.md already defers for the commit path (§3.2), just for the
interim-preview path instead. Out of scope for a minimal fix. Mitigated
2026-08-07 with an explanatory comment at the raw call site
(`deepgram_client.py`, immediately before the `handler(...)` call)
warning against adding side effects there without an explicit guard.

---

## Task 3 — Pipeline map & responsibility-overlap summary

```
Deepgram WebSocket
    → deepgram_client.py            [ingest; §1.3: 2 of 6 canonical-key fields are near-constant;
                                      §3.9: every interim delivered twice, one path identity-less]
    → utterance_lifecycle.py        [English canonical controller; §1.1 revise/supersede path
                                      never succeeded live pre-fix, now mitigated; §2.2/§2.3 merge-
                                      safety asymmetries]
    → japanese_sentence_assembler.py + japanese_boundary_stabilizer.py
                                     [Japanese path: TWO chained independent HOLD/EXTEND/COMMIT-
                                      style engines (§Task5 below), §3.1 speaker relabeling,
                                      §3.2 one remaining direct-commit bypass + side-write persistence]
    → canonical_transcript_ledger.py / pipeline_commit_transaction.py / duplicate_protection.py
                                     [commit + redundant re-validation, §3.6; ledger has no
                                      internal staleness defense, §3.5]
    → transcript_store.py / transcript_snapshot_store.py
                                     [THIRD, Japanese-only storage module with its own
                                      positional-revision bug, §2.5 — English path never
                                      touches this store]
    → main_window.py (UI display + translation submission)
                                     [independently re-decides revision-vs-new FOUR separate
                                      times (§3.4 #1-4, #1 now fixed) and independently mints
                                      canonical identity in Japanese-manual mode (§2.6) — the
                                      "single controller" principle is violated here too, not
                                      just in the assembler]
    → stop_finalize_worker.py       [fail-closed architecture is otherwise sound (§5.4) but
                                      one required-step input, §2.4, doesn't check what it logs]
```

**English vs. Japanese divergence beyond intended "different boundary
strategy":** REPAIR_PLAN.md explicitly requires Japanese and English to
share commit/revision/translation/UI ownership even while using different
boundary heuristics. In practice the Japanese path has a **third storage
backend** (`transcript_snapshot_store.py`) that the English path never
touches at all — this is ownership divergence, not boundary-strategy
divergence, and is exactly what REPAIR_PLAN.md said must not happen.

**Single-controller principle — current real state:** genuinely upheld for
the Japanese assembler's main commit path (confirmed via
`accept_boundary_proposal`, §3.2) and for `duplicate_protection.py`'s
`already_committed` verification (§4.2). Violated in: the Stop-tail direct-
commit bypass (§3.2), the assembler's post-decision persistence side-writes
(§3.2), `main_window.py`'s four independent revision-vs-new deciders
(§3.4), `main_window.py`'s Japanese-manual-mode identity minting and
English `_try_segment_repair` (§2.6), and the triplicated duplicate/
lineage-validity checks across the three commit-layer files (§3.6).

---

## Task 4 — Silent failure paths (full list, beyond those already detailed above)

| File:line | What's swallowed | Log evidence? |
|---|---|---|
| `utterance_lifecycle.py:371-372` | `_observe_identity`'s registry call — **fails OPEN** (`return True, "unavailable", {}`), inverting this file's own fail-closed design principle | STATIC — highest-severity silent-failure in this list, since it's a bypass of a safety gate, not just a lost side-effect |
| `utterance_lifecycle.py:313` | `reset_for_session`'s registry reset | STATIC — new session could inherit stale identity entries |
| `utterance_lifecycle.py:411-412` | `_resolve_correction_target_locked` | STATIC — falls back to unverified raw target id |
| `main_window.py:819-822` | UI-queue drain loop, only prints if `DEBUG_DIAGNOSTICS` | STATIC — truncates a tick's processing, already-dequeued items lost |
| `main_window.py:1294-1304, 1395-1409` | Translation submit/removal inside store-segment handlers | STATIC — directly contradicts the adjacent comment's stated intent (§2.9) |
| `main_window.py:4171-4172` | Cross-thread event-bus post in `on_interim_transcript` | STATIC — interim update from background thread dropped with no trace |
| `deepgram_client.py:1533-1534` | Japanese stabilizer `ingest()` exception | STATIC — falls through into English commit path (§2.10), not a clean no-op |
| `deepgram_client.py:855-856` | `_normalize_and_send_pcm` empty-bytes case | STATIC — audio (frozen area), lower priority |
| `duplicate_protection.py:145-153` | `source_language.get()`/`target_language.get()` | STATIC — low severity, defaults to `None` |
| `duplicate_protection.py:224-225` | Previous-text lookup failure | STATIC — fails closed (safe default) but masks *why* lookups start failing at scale |
| `pipeline_commit_transaction.py:85-125` | `_write_suppressed_stop_tail_candidate`, both the `run_identity` fallback and the early-return | STATIC — evidence-trail loss only, not transcript corruption |
| `stop_finalize_worker.py:1906-1930` | Evidence-package scheduling | STATIC — run permanently stuck at `completed_pending_evidence_package` with nothing to promote it, no log of why |
| `japanese_sentence_assembler.py:4245-4246` | Entire `transcript_snapshot_store` write + `notify_stable_commit` + `record_flight_event` | STATIC — **zero logging, not even to the dedicated Japanese accuracy log** — undiagnosable via the exact evidence this audit was told to rely on |
| `japanese_sentence_assembler.py:2327-2328` | `stabilizer.process(...)` call in `_route_stable_publish` | STATIC — exception looks identical in logs to "stabilizer decided to emit unchanged" |
| `japanese_boundary_stabilizer.py:906-907` | `_update_evidence_index` | STATIC — cosmetic, secondary evidence index only |

---

## Task 5 — Unnecessary complexity (conceptual simplification only, no code given)

- **`main_window.py::_commit_transcript_item_to_store`** (~335 lines): diagnostic logging + accept/reject gating + Japanese cross-segment merge/id-minting + `decide_transcript_action` dispatch + dedup override + post-commit bookkeeping, all in one deeply-nested function. Split into a language-agnostic gate, an English committer, and a Japanese committer that owns its own id lifecycle.
- **`duplicate_protection.py::_display_transcript_item`** (~400 lines, 6 responsibilities): previous-text resolution, local decision, authoritative-signal override, already-committed verification, full commit orchestration, UI/translation dispatch. Extract each into its own method so each becomes independently testable.
- **`deepgram_client.py::_deepgram_on_message`** (~200 lines, 6 responsibilities): JSON parsing, type routing, interim delivery, latency/health telemetry, per-segment iteration, commit. Extract a `_route_results_message` classifier and a single `_record_message_telemetry` call.
- **`deepgram_client.py::_commit_final_transcript_segment`**: mixes permission gating, language routing, and a large embedded raw-evidence-recording aside — extract evidence recording to run once regardless of route.
- **`utterance_lifecycle.py::_supersede_committed_locked` vs `_extend_committed_locked`** (~135 lines combined, near-duplicate structure differing only in merge strategy and reason strings): unify into one `_revise_committed_locked(*, merge_strategy, reason, ...)`.
- **`utterance_lifecycle.py::_ingest`** (~300 lines, 5 cases via deeply nested conditionals sharing mutable closure state): rewrite as an ordered list of guard-clause helpers, each returning `Optional[LifecycleDecision]`, first non-`None` wins — the current structure is exactly what let §2.2's Case B/C asymmetry go unnoticed.
- **`japanese_sentence_assembler.py`** (~15 hand-tuned literal-phrase tables feeding 6+ overlapping "is this text complete/safe" functions, including session-specific literal strings like `"リーフが浮かんでこない"` that are verbatim STT-error fragments from one past transcript, not general grammar): collapse to one grammatical completeness signal (particle-class + punctuation), per REPAIR_PLAN's own stated target of removing transcript-specific phrase lists from authority decisions.
- **Two independent HOLD/EXTEND/COMMIT-shaped engines chained in series** (`JapaneseContinuityAssembler`'s buffer/hold/commit state machine, followed by `JapaneseBoundaryStabilizer`'s separate pending/hold/merge state machine, invoked mid-flow) — each maintains its own "previous line"/"pending"/"speaker" state and can disagree (confirmed: `has_strong_terminal_boundary`/`has_incomplete_ending` in the stabilizer vs. `has_strong_sentence_end`/`looks_incomplete_japanese_fragment` in the assembler use different literal suffix tables). Collapsing to one boundary engine with one buffer removes the compounding-disagreement risk.
- **Three near-identical merge helpers in `main_window.py`** (`_merge_text_with_overlap_info`, `_merge_text_without_overlap` [dead], `_merge_text_without_overlap_counted`) — collapse to one function with an optional overlap-count return flag.
- **`main_window.py::_display_transcript_item` name collision**: `AlphaApp._display_transcript_item` (line 5893) explicitly bypasses Python's normal MRO override resolution to call `DuplicateProtectionMixin._display_transcript_item` (line 5781) under the identical name — easy to misread as recursion. Rename the `AlphaApp`-level router (e.g. `_route_transcript_item_for_commit`) since that's what it actually does.

---

## Full prioritized fix order (my recommendation for what to tackle first)

1. §1.1 — revise/supersede subsystem 0% success rate (biggest gap between "repair complete" status and live behavior; likely single highest-value fix) — **mitigated, commits `98a6fa0` + `432dea1`**
2. §1.2 — `_apply_final_interim_comparison` (user already knows this is confirmed; now log-verified) — **fixed, commit `78eb59e`**
3. §2.1 — `_dispatch_commit` silent swallow (one-line fix: log + don't let it look like a no-op) — **fixed, commit `d7c1834`**
4. §2.2 / §2.3 — utterance_lifecycle Case B asymmetry + speaker fail-open (two related, both in the same file, both in the merge-safety hot path) — **still open**
5. §2.4 — wire the real drain-check boolean into `commit_confirm_ok` instead of discarding it (small, high-value, closes a REPAIR_PLAN Phase 4 gate gap) — **still open**
6. §1.3 — decide whether `channel_index`/`event_id` need real per-utterance values, or whether downstream code needs to stop trusting them (architectural decision, not a quick patch — needs your call on scope) — **still open**
7. §2.5 through §2.9 — the transcript_store/transcript_snapshot_store/main_window write-path races (cluster of related TOCTOU issues, likely fixable together) — **still open**
8. Everything else in Priority 3, roughly in listed order — **§3.4 #1 fixed (commit `78eb59e`), §3.4 #5/#7 mitigated (`25a6623`, `1a32639`), rest still open**
9. Priority 4 dead-code cleanup — safe to batch whenever convenient, zero urgency — **still open**

---

## Limitations of this audit — read before trusting it fully

This audit substantially reduces risk, but no static-analysis or
LLM-based review can guarantee zero remaining bugs — especially not for
defects hiding behind a code path that has never yet fired live. Several
findings above are explicitly in that category (§4.3) precisely because
they match the shape of the `on_interim` bug that motivated this audit in
the first place: correct-looking code, wired up, that simply has never
been exercised by any of the 4 sampled sessions. There is no way to
distinguish "genuinely safe, just rare" from "silently broken, just
unlucky" without either forcing that path in a controlled test or waiting
for it to occur live and checking the resulting evidence.

Specific residual risks:

- **Log coverage is partial.** Only 4 run folders were sampled, all
  recent. Older code paths, rare Deepgram event shapes, or
  network-failure/reconnect scenarios are not represented in any sampled
  log — findings about those paths are static-only by necessity.
- **Cross-file interactions beyond the audited file set.** Each audit was
  scoped to specific files per the project's CLAUDE.md restriction;
  `canonical_identity_registry.py`, `stable_line_revision.py`,
  `stable_revision_decision.py`, `language_pipeline_worker.py`, and the
  translation coordinator were referenced by name from findings in other
  files but not independently read — a bug whose root cause lives
  primarily in one of those files would not have been caught here.
- **This audit cannot prove the absence of a bug, only the presence of
  ones it found.** Confidence levels are honest about what's log-verified
  vs. inferred vs. suspected, but "suspected" findings could be wrong in
  either direction — some may not manifest in practice due to a
  mitigating check elsewhere that wasn't traced.
- **Fixing any single finding above may change which paths are live**,
  potentially surfacing yet another dormant issue the way `_merge_lexical`'s
  fix surfaced the need for the on_interim wiring fix. Recommend
  re-running the deterministic/replay test levels described in
  REPAIR_PLAN.md after each fix, not just at the end. **This is exactly
  what happened with §1.1 (one round of mitigation surfaced a second,
  deeper bottleneck) and with §1.2/§3.9 (fixing the ghost line surfaced
  the double-delivery identity leak) — treat this limitation as
  confirmed, not hypothetical.**
