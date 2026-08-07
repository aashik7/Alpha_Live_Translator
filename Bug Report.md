# Proactive Full-Codebase Audit — 2026-08-06

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
list; only the speaker-only-keying bullet remains open.

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

### 3.4 Cross-file anti-pattern instance count — same fragile prefix/containment revision-vs-new comparison, found in 7 places total
This is the recurring pattern the user explicitly asked to be hunted
across the whole codebase (Task 2). Consolidated list, most→least severe:

| # | File:line | Confirmed already-fixed? | Notes |
|---|---|---|---|
| 1 | `main_window.py:4288-4318` `_apply_final_interim_comparison` | **No — confirmed still broken by log evidence, §1.2** | The one named in the task brief as confirmed-but-unfixed. |
| 2 | `main_window.py:5492-5554` `_check_stop_tail_duplicate` | No | Stop-time last-chance commit; can silently classify a reworded tail as `skip_already_committed` and drop it entirely — highest severity of the `main_window.py` group. |
| 3 | `main_window.py:4332-4347` `_should_commit_interim_recovery` | No | Same pattern, Stop-time recovery path. |
| 4 | `main_window.py:4954-4997` `_should_repair_previous_segment` | No | Misclassifies reworded (not pure-substring) previous segments as non-continuation. |
| 5 | `utterance_lifecycle.py:159-174` `_text_related` | No | 8-char shared-prefix test treats any two utterances starting the same as "related" regardless of what follows — risks wrong SUPERSEDE within the 2.5s timing window. |
| 6 | `duplicate_protection.py:49-81` `decide_transcript_action` | **Yes, but residual gap** | `or True` bypass confirmed removed. Two dead `.startswith()` branches remain (unreachable, since `in` two lines earlier already subsumes them — cosmetic). Real gap: substitution-style correction with no authoritative `lifecycle_decision` signal still falls to `"add"` (duplicate line) rather than `"update"` — mitigated in the common case by the upstream lifecycle signal override, but live when that signal is absent. |
| 7 | `utterance_lifecycle.py:110-115` `_merge_lexical` | **Yes, but residual gap** | Just-fixed word-overlap threshold (0.6) is still order-blind — word-swap/reorder cases (e.g. "the cat sat on the mat" → "the mat sat on the cat") score ≥0.6 overlap and get silently merged, discarding the actual difference. |
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

### 3.6 Same decision (duplicate? valid revision target?) independently re-made in 3+ places
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

---

## Priority 4 — Dead code / stale documentation

### 4.1 Confirmed-dead functions and constants (safe to remove, zero callers via repo-wide grep of `alpha/`)

| File:line | Symbol | Note |
|---|---|---|
| `japanese_sentence_assembler.py:625-640` | `should_hold_speaker_continuation` | Always returns `False`; unused since Task 2B replaced its call site. |
| `japanese_sentence_assembler.py:88-89` | `SPEAKER_CONTINUATION_MAX_COMPACT`, `JAPANESE_SPEAKER_STICKY_MS` | Zero readers. |
| `japanese_sentence_assembler.py:59-61,100,756,908,1389-1390,1436,4268-4269` | `_translation_unit_builder`/`JapaneseTranslationUnitBuilder` | Still instantiated/flushed every run but data-feed dead-gated by `JAPANESE_TRANSLATION_UNIT_GROUPING_ENABLED = False`; session-summary metrics derived from it always read zero. |
| `japanese_boundary_stabilizer.py:283-285` | `set_previous_line` | Vestigial; real state update goes through `note_emitted()`. |
| `canonical_transcript_ledger.py:449-479, 545-573, 674-688` | `append_record`, `revise_record`, `suppress_record` | Only `apply_decision` (which wraps the internal `_..._unlocked` versions with idempotency/lineage/immutability checks) is actually called; these public wrappers skip all of that. **Not currently harmful (dead), but risky if ever wired** — a future editor reaching for the "obvious" name bypasses every Phase-1 guarantee. |
| `duplicate_protection.py:84-103` | `apply_transcript_sequence` | Test-only helper living in a production file. |
| `utterance_lifecycle.py:334-336, 330-332, 319-320` | `events()`, `stats()`, `set_event_log_path()` | `events()` fully dead; `stats()`/`set_event_log_path()` called only from an offline validator script, never from runtime. |
| `utterance_lifecycle.py:819-836` | `force_cancel_active()` | Zero callers; not wired to Stop or any error path. |
| `stop_finalize_worker.py:1344-1347` | `_run_evidence_package_worker` | **Re-verified genuinely removed** (ROOT_CAUSE.md's claim holds — only a removal comment remains). |
| `deepgram_client.py:1444-1446` | `_get_language_name` | Dead. |
| `main_window.py` (13 functions) | `_append_initial_transcript`, `_expand_summary_panel_if_collapsed`, `_get_text_content`, `_hold_unstable_language_candidate`, `_language_script_warning`, `_log_language_commit_warning`, `_make_combo`/`_combo_config`, `_normalize_japanese_display_line`, `_start_ui_lag_monitor`, `record_transcript_segment`, `_on_translation_worker_error`/`_handle_translation_worker_error`, `_merge_text_without_overlap`, `toggle_summary_panel` | See §4.3 — several of these are "silently unreachable but not dead," not merely unused. |

### 4.2 `ROOT_CAUSE.md` debt-list corrections (doc is stale on 2 items — worth updating the doc itself)
- **Stale:** `duplicate_protection.py`'s `already_committed` trust-gate bullet — this is now independently re-verified against the identity registry (`:281-325`), not a blind trust. Remove from debt list.
- **Stale on function name, not on substance:** `get_last_segment(speaker_num)` is now `get_last_segment_if_active(speaker_num)` — but the underlying speaker-only-keying defect (§2.7) is still real; update the doc's function name, keep the finding open.
- **Confirmed still accurate:** `_run_evidence_package_worker` genuinely removed (§4.1); `_channels_compatible()` genuinely removed, no regression (re-verified).

### 4.3 "Silently unreachable but not dead" — the category the user specifically asked about
These are reachable per static analysis but have **zero matching evidence** across all 4 sampled run logs — the same shape as the pre-fix `on_interim` bug:

- `utterance_lifecycle.py:753-783` cross-channel `UtteranceEnd` guard (BUG-A's own fix target) — zero `CROSS_CHANNEL_END_IGNORED` events in any sampled run, despite `utterance_end`-reasoned commits succeeding regularly in the same runs. Possible cause: `UtteranceEnd` uses key `"channel"` while Results use `"channel_index"` (`deepgram_client.py:1817-1826`) — a representation mismatch (scalar vs. `[idx,total]` array, compounded by §1.3's array/string type instability) could make the channel-match check spuriously pass/fail in ways never exercised by the sampled sessions.
- `utterance_lifecycle.py:432-656` `accept_boundary_proposal`'s rejection paths (`identity.accepted==False`, missing-target, commit failure, quarantine-on-assignment-failure) — `ASSEMBLER_COMMIT_GATE_FAILED` = 0 across all 4 runs. Only the happy path has ever been exercised live.
- `utterance_lifecycle.py:1227-1233` `prev.committed_record_id` cross-check inside `_is_correction_of_committed_locked` — permanently a no-op; `ActiveUtterance.committed_record_id` is never assigned anywhere in the class, not even by `_commit_locked`. Dead safety-net that gives false confidence a double-check exists.

---

## Task 3 — Pipeline map & responsibility-overlap summary

```
Deepgram WebSocket
    → deepgram_client.py            [ingest; §1.3: 2 of 6 canonical-key fields are near-constant]
    → utterance_lifecycle.py        [English canonical controller; §1.1 revise/supersede path
                                      never succeeds live; §2.2/§2.3 merge-safety asymmetries]
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
                                      times (§3.4 #1-4) and independently mints canonical
                                      identity in Japanese-manual mode (§2.6) — the "single
                                      controller" principle is violated here too, not just
                                      in the assembler]
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

1. §1.1 — revise/supersede subsystem 0% success rate (biggest gap between "repair complete" status and live behavior; likely single highest-value fix)
2. §1.2 — `_apply_final_interim_comparison` (user already knows this is confirmed; now log-verified)
3. §2.1 — `_dispatch_commit` silent swallow (one-line fix: log + don't let it look like a no-op)
4. §2.2 / §2.3 — utterance_lifecycle Case B asymmetry + speaker fail-open (two related, both in the same file, both in the merge-safety hot path)
5. §2.4 — wire the real drain-check boolean into `commit_confirm_ok` instead of discarding it (small, high-value, closes a REPAIR_PLAN Phase 4 gate gap)
6. §1.3 — decide whether `channel_index`/`event_id` need real per-utterance values, or whether downstream code needs to stop trusting them (architectural decision, not a quick patch — needs your call on scope)
7. §2.5 through §2.9 — the transcript_store/transcript_snapshot_store/main_window write-path races (cluster of related TOCTOU issues, likely fixable together)
8. Everything else in Priority 3, roughly in listed order
9. Priority 4 dead-code cleanup — safe to batch whenever convenient, zero urgency

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
  REPAIR_PLAN.md after each fix, not just at the end.

---

# Appended 2026-08-07 — Japanese content loss (separate track, NOT the interim-ghost issue)

## 4.1 Japanese: assembler-committed sentences never reach the canonical ledger

**Files:** `alpha/transcription/japanese_sentence_assembler.py` (commit
decisions), handoff into `canonical_transcript_ledger.py`
**Confidence: LOG — confirmed by live run evidence**
**Severity: SILENT-LOSS (real speech missing from the final transcript)**
**Status: OPEN — needs its own investigation, deliberately not fixed with
the interim-ghost work.**

Evidence, run `troubleshooting/runs/v3.3.5.5.8.5.26.5.3-20260807-160529`
(`selected_language: ja`, `final_status: completed`):

| stage | count |
|---|---|
| `accuracy/assembler_decisions.jsonl` commit decisions | 10 |
| `transcripts/stable_commits.jsonl` rows | 10 |
| `transcripts/canonical_transcript_ledger.jsonl` append events | 8 |
| `export_coverage_report.json` `canonical_active_record_count` | 9 |

The assembler decided `commit_new` ten times, all ten are in
`stable_commits.jsonl`, but only nine canonical records exist and the
final export contains nine lines. At least one full sentence is absent
from the final transcript despite being committed by the assembler:

```
ですよ。違いますねでやっぱりこっちにいると日本の行事の不倫っていうのを味わうことが難しいので、
```
assembler decision: `commit_new`,
reason `japanese_continuity_assembler_safe_hold_timeout_incomplete_b`.
Present in `stable_commits.jsonl` and `assembler_decisions.jsonl`;
`grep 味わう` returns 0 hits in `canonical_transcript_ledger.jsonl` and 0
in the final export.

Note that `export_coverage_report.json` reports `coverage_ratio: 1.0,
coverage_passed: true` for this run. That gate only compares
canonical -> final, so a record lost *before* it reaches the canonical
ledger is invisible to it. Coverage passing is therefore not evidence
that nothing was dropped.

This is very likely a concrete instance of audit item 3.2 (the Japanese
assembler's dual write paths — committing to its own stores and
side-writing persistence independently of the single canonical
controller). Worth confirming that specifically during the follow-up.

## 4.2 Japanese: real speech quarantined as `noise_fragment`

**File:** `alpha/transcription/japanese_sentence_assembler.py` (quarantine
path writing `accuracy/quarantine_decisions.jsonl`)
**Confidence: LOG** | **Severity: SILENT-LOSS** | **Status: OPEN**

Same run, `accuracy/quarantine_decisions.jsonl`:

```json
{"raw_text": "。忘れちゃうし、", "compact_length": 6,
 "quarantine_decision": "quarantined", "reason": "noise_fragment",
 "later_committed_to_stable": false}
```

`。忘れちゃうし、` is ordinary conversational Japanese, not noise. It was
dropped and never committed. The `later_committed_to_stable: false` field
shows the module already tracks whether a quarantined fragment was
recovered — here it was not. Worth checking how often
`later_committed_to_stable` is false across runs; that field is a
ready-made measure of how much this rule actually costs.

## 4.3 Why these are NOT the interim-ghost / watchdog issue

Recorded explicitly so the two tracks do not get conflated later:

* The ghost watchdog (`main_window.py::_check_interim_ghost_watchdog`)
  only clears `_latest_interim_text` and the on-screen preview line. It
  has no path into the assembler, `stable_commits`, or the canonical
  ledger.
* The lost sentence in 4.1 reached `stable_commits.jsonl` — i.e. it got
  past every UI-preview concern and was lost in the
  assembler -> canonical-ledger handoff.
* The quarantine in 4.2 happens inside the assembler, upstream of any UI
  display decision.

One genuine interaction is worth keeping in mind for the follow-up
though: `_recover_interim_tail_on_stop` reads `_latest_interim_text` as
its last-resort source for recovering an uncommitted tail at Stop. If the
watchdog has already cleared that variable, the recovery has nothing left
to recover. That is a display-layer mechanism able to affect a
content-recovery path, and it should be checked as part of whichever
track touches Stop-time recovery.

## 4.4 Interim-preview watchdog TTL is mis-calibrated for both languages

**File:** `alpha/constants.py` `INTERIM_GHOST_TTL_MS = 1500`
**Confidence: LOG** | **Severity: VISIBLE-BUG (preview clears early)**
**Status: OPEN — fix pending, blocked on choosing a value.**

Measured gap between the last interim update and the matching final
comparison, per run (`logs/async_debug.log`):

| run | lang | decisions | watchdog firings | max gap | p50 gap |
|---|---|---|---|---|---|
| `-132429` | en | 5 | 5 | 1875 ms | 1305 ms |
| `-132635` | en | 4 | 3 | 1924 ms | 1760 ms |
| `-150958` | en | 2 | 2 | 1702 ms | 1702 ms |
| `-153955` | en | 12 | 10 | 1891 ms | 1732 ms |
| `-160352` | en | 7 | 2 | 1493 ms | 1319 ms |
| `-155922` | ja | 1 | 4 | **3502 ms** | 3502 ms |
| `-160130` | ja | 4 | 7 | **4063 ms** | 834 ms |
| `-160529` | ja | 8 | 1 | 1004 ms | 828 ms |

A TTL of 1500 ms sits below the normal English gap (1635-1924 ms, driven
by the configured `endpointing=1200` plus transport) and far below the
Japanese worst case (4063 ms). The watchdog therefore clears legitimate,
still-pending previews rather than orphans.

Important for the fix: **do not derive the TTL from
`DEEPGRAM_ENDPOINTING_MS`.** Japanese uses `endpointing=500` — *lower*
than English's 1200 — yet shows the *longest* gaps, because on the
Japanese path the "final" is produced by the assembler's own
hold/timeout logic (`hold_timeout_*`, `safe_hold_timeout_*` reasons),
not by Deepgram endpointing. An endpointing-derived formula would size
Japanese the wrong way round.

Also note that after commit `a5e2ac4` removed the real ghost source, a
watchdog firing is no longer routine housekeeping — it is a signal that
something upstream failed. Sizing the TTL so it effectively never fires
in normal operation is the intended end state, and the scan tool's
verdict logic should treat a high firing rate as REVIEW rather than PASS.
