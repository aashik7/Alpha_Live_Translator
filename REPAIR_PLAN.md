STATUS: Repair complete as of Task 14 (2026-08-04). All 5 REPAIR_PLAN.md
phases plus follow-up Tasks 6-14 resolved. Verified via 3-scenario
multi-session live test (English, Japanese, short-Stop English) — all
three completed cleanly with final_status='completed', zero
failed_required_steps. See TASK_1 through TASK_14 report files for full
history.

---

# Repair and Code-Optimisation Plan

We should **not continue with isolated patches**. We should perform a controlled refactor in stages, keeping the working audio, providers, session lifecycle, and ordering systems frozen.

The target architecture should be:

```text
Deepgram event
    ↓
Single canonical utterance controller
(session + channel + utterance_id + version)
    ↓
Atomic canonical ledger commit
    ↓
Source UI record keyed by canonical_record_id
    ↓
One translation coordinator
    ↓
Translation UI record keyed by the same canonical_record_id
    ↓
Fail-closed finalisation
```

Japanese and English may use different **boundary strategies**, but they must not use different commit, revision, translation, or UI ownership systems.

---

## Phase 0 — Protect the current project

Before changing code:

1. Create a repair branch from failed commit:

```text
6102c03f8fd40600d4bf9304d5199042100950f2
```

2. Preserve the current failed live-test package.
3. Create deterministic replay fixtures from:

   * the failed Japanese session;
   * the failed English session;
   * cumulative revision examples;
   * translation-disappearance examples.
4. Prevent live testing until every offline replay passes.

This gives us a repeatable test instead of spending tokens and time on repeated manual tests.

---

# Phase 1 — Establish one identity authority

This is the foundation. Nothing else should be repaired before this.

## Required canonical key

Every utterance must use:

```text
session_id
channel_index
provider_utterance_id
canonical_utterance_id
source_version
canonical_record_id
```

Create one session-scoped registry:

```text
(session_id, channel_index, canonical_utterance_id)
    → active state
    → current version
    → canonical_record_id
    → translation state
```

## Fixes in this phase

### 1. Remove global `_last_committed` as revision authority

`duplicate_protection.py` currently converts a `U-*` target to the global lifecycle `_last_committed` record without proving it is the same utterance. 

Replace this with exact lookup:

```text
(session, channel, utterance_id)
→ committed_record_id
```

If no exact target exists:

```text
reject revision
log identity mismatch
do not update another row
```

### 2. Make UtteranceEnd channel-safe

`on_utterance_end()` receives a channel but commits the active utterance without validating that the channel matches. 

Required:

```text
UtteranceEnd channel != active channel
→ ignore for that active utterance
```

### 3. Reject exact duplicate finals first

The correction path currently accepts an explicit matching target even when text relation is not meaningful because the condition includes `or True`. 

Required order:

```text
exact duplicate
→ IGNORE_DUPLICATE

real same-utterance extension
→ REPLACE/EXTEND

authoritative correction
→ SUPERSEDE

new utterance
→ CREATE_NEW
```

### 4. Make ledger commits truly atomic

The current transaction can report failure after the ledger was already modified, for example when evidence or runtime-counter recording fails. 

The caller must receive separate outcomes:

```text
canonical_commit_applied
evidence_write_failed
metrics_write_failed
```

A metrics or evidence failure must **never trigger a second transcript append**.

## Phase 1 acceptance gate

* Wrong-utterance revision test passes.
* Out-of-order revision test passes.
* Cross-channel UtteranceEnd test passes.
* Exact duplicate final creates zero new commits.
* One input decision creates at most one ledger mutation.
* No fallback append after an already-applied ledger mutation.

---

# Phase 2 — Simplify transcript ownership

## Main rule

Only the canonical utterance controller may decide:

* create;
* extend;
* replace;
* commit;
* supersede;
* ignore.

Other modules may make recommendations, but they cannot commit independently.

## Japanese path

The current Japanese assembler contains extensive phrase-specific, incomplete-tail, timing, sticky-speaker, soft-boundary, and concatenation logic. It can hold speaker changes for up to five seconds and concatenate non-overlapping fragments. 

Change its responsibility to:

```text
Japanese boundary strategy
→ propose HOLD / EXTEND / COMMIT
→ return candidate text and evidence
```

It must not independently:

* append to the ledger;
* revise the last UI row;
* start translation;
* write synthetic output back as provider/raw input.

### Japanese safety rules

* Speaker/channel change is a hard boundary by default.
* Never merge separate speaker turns merely because one fragment is short.
* Strong punctuation can commit.
* Timeout may commit a buffered utterance, but never join a new speaker.
* Synthetic assembler output cannot re-enter Deepgram/raw ingress.
* Remove transcript-specific phrase lists from authority decisions over time.

## English path

The current lifecycle uses a 2,000 ms fallback and allows timing compatibility up to 2.5 seconds, while non-overlapping chunks can be concatenated. 

Required:

* `is_final=true, speech_final=false` remains buffered.
* `speech_final=true` or matching UtteranceEnd commits.
* Timeout is only a final fallback.
* Timeout must not automatically treat every chunk as a full sentence.
* One active record is updated during the utterance.
* No repeated progressive lines remain permanent.

## Phase 2 acceptance gate

For both languages:

```text
My
My name
My name is Tariqul
```

must produce:

```text
My name is Tariqul
```

And:

```text
Sentence one.
Sentence two.
```

must remain two sentences.

Japanese dialogue between two speakers must never become one merged canonical line.

---

# Phase 3 — Replace translation ownership

Translation must become a direct child of a canonical record.

## Remove positional translation behaviour

No code should perform:

```text
remove the last translation
update the last translation
replace whatever is currently last
```

All operations must use:

```text
canonical_record_id
canonical_utterance_id
source_version
```

## Replace the global pending payload

The current UI stores one `_pending_translation_payload`, so a newer update can replace another utterance’s pending translation. 

Replace it with:

```text
pending_translations[
    session_id,
    canonical_utterance_id
]
```

Each utterance gets its own debounce state.

## Single translation rule

For every terminal source version:

```text
one canonical source commit
→ one translation request
→ one translation record
→ one UI translation item
```

When source version 2 supersedes version 1:

* version 1 provider result becomes obsolete;
* version 1 may not delete another utterance’s translation;
* version 2 updates only its linked translation item;
* loading state for both versions reaches a terminal state.

## Translation deduplication

Deduplication must use:

```text
session
+ canonical utterance
+ source version
```

Do not globally reject identical text, because two legitimate utterances may both say “Thank you.”

## Japanese translation-unit builder

The Japanese translation-unit builder should either:

1. be removed, with each canonical utterance translated directly; or
2. become the sole owner of grouping, producing one new canonical translation unit.

It must not run beside direct per-commit translation as a second authority. The existing builder is currently a distinct grouping component in the Japanese assembler path. 

My recommendation: **disable grouping initially** and translate one committed canonical utterance at a time. Reintroduce grouping later only if evidence shows it improves quality.

## Phase 3 acceptance gate

* Source revision never removes another source’s translation.
* Rapid revisions of two utterances both receive translations.
* Final Japanese source line cannot be dropped during Stop.
* Old provider response cannot overwrite a newer version.
* Translation request count equals eligible terminal canonical versions.
* Loading indicators pending at exit = 0.

---

# Phase 4 — Repair finalisation and evidence integrity

## Carried over from Phase 2 (deferred — address here or in Phase 5)

Phase 2's acceptance-gate tests pass (see `TASK_2D_REPORT.md`), but two items
classified as Phase 2 scope were never actually closed:

1. **Single canonical controller not yet real for Japanese.** Phase 2's main rule
   ("only the canonical utterance controller may decide create/extend/replace/
   commit/supersede/ignore; other modules may only recommend") still does not hold.
   The Japanese path (`japanese_sentence_assembler.py` → `japanese_final_chunk_stabilizer.py`)
   still commits independently via `execute_pipeline_commit`, rather than proposing
   HOLD/EXTEND/COMMIT to a single controller the way REPAIR_PLAN originally
   specified. Task 2B/2D deliberately patched the dangerous symptoms (cross-speaker
   merging, speaker-blind revision) without doing this larger rewrite, judging it
   too large for a surgical fix. The full HOLD/EXTEND/COMMIT proposal architecture
   for Japanese is still owed.
2. **Two Task 1 findings never revisited.** `ROOT_CAUSE.md`'s "Additional Findings"
   classified two items as Task 2 scope, but none of Tasks 2A–2D touched them (all
   four focused on the Japanese assembler/boundary-stabilizer chain):
   - `alpha/transcription/duplicate_protection.py::_display_transcript_item` —
     `self.transcript_store.get_last_segment(speaker_num)` keyed by speaker only,
     no channel/session key.
   - Same function's `already_committed` trust-gate (`canonical_record_id` /
     `_jp_continuity_assembler` / `canonical_ledger_committed` flags) — trusts an
     upstream producer without independent verification.

The finaliser must stop reporting success when required reconstruction fails.

## Required final state

A run is `completed` only when all required items succeed:

* audio summary;
* raw event persistence;
* utterance reconstruction;
* canonical ledger validation;
* Stable export;
* Final export;
* translation drain;
* loading-state drain;
* run manifest;
* evidence package.

Any required failure must produce:

```text
final_status = failed
stop_finalize_failed = true
failure_reason = exact failing stage
```

The current finalisation worker contains broad exception-handling paths, so final status must be derived from explicit required-step results rather than simply reaching the end of Stop. 

## Evidence separation

Use separate immutable streams:

```text
provider_events.jsonl
utterance_decisions.jsonl
canonical_commits.jsonl
translation_jobs.jsonl
ui_events.jsonl
```

Synthetic events must never be written into `provider_events.jsonl`.

## Phase 4 acceptance gate

* Empty Stable reconstruction cannot be marked completed.
* Any required exception gives non-zero validation exit code.
* Raw counts, canonical counts, UI counts, and export counts reconcile.
* Every canonical record has valid lineage.
* Every translation references an existing canonical record/version.

---

# Phase 5 — Optimise and remove conflicting code

Only after Phases 1–4 pass should we clean the architecture.

## Remove or retire

* global revision remapping;
* positional last-line updates;
* duplicate canonical commit fallbacks;
* global translation debounce payload;
* duplicate translation ownership;
* synthetic-event feedback paths;
* obsolete deterministic test-only logic;
* phrase-specific Japanese rules that no longer provide measurable value;
* parallel counters that disagree with the canonical registry.

## Keep frozen

Do not change:

* WASAPI capture;
* microphone capture;
* audio mixer;
* normalisation;
* PCM configuration;
* Deepgram transport/models;
* DeepL provider;
* current language mappings;
* repeated-session repair;
* sparse translation ordering;
* UI design.

Those areas were working in the failed test and are not the present root cause.

---

# Validation strategy

## Level 1 — Unit tests

Test individual decisions:

* channel ownership;
* duplicate final;
* extension;
* correction;
* new utterance;
* stale version;
* wrong revision target.

## Level 2 — Component integration

Test:

```text
lifecycle
→ ledger
→ UI store
→ translation coordinator
```

without real APIs.

## Level 3 — Failed-run replay

Replay the exact recorded Japanese and English event sequences.

Required:

* no Japanese cross-turn over-merging;
* no cumulative duplicate lines;
* no disappearing translation;
* no missing last translation;
* no wrong-record revision;
* no empty reconstruction.

## Level 4 — Synthetic long test

Run:

* 100+ utterances;
* repeated text;
* reordered callbacks;
* delayed translations;
* duplicate finals;
* Start→Stop→Start multiple times.

## Level 5 — Real live test

Only after all previous levels pass:

1. Short Japanese session.
2. Japanese→English.
3. English→Japanese.
4. Repeated sessions without reopening Alpha.
5. Longer 15–30 minute stability test.

---

# Recommended implementation sequence

Use **four controlled Cursor tasks**, not one enormous repair:

### Task 1 — Identity and atomic commit

Fix:

* utterance registry;
* channel validation;
* wrong `_last_committed` mapping;
* exact duplicate rejection;
* atomic ledger result handling.

### Task 2 — Transcript authority

Fix:

* one canonical controller;
* Japanese over-merging;
* English fragmentation;
* synthetic-event re-entry;
* source UI identity updates.

### Task 3 — Translation authority

Fix:

* one translation coordinator;
* per-utterance pending state;
* canonical source/translation linkage;
* obsolete-result handling;
* disappearing translations;
* Stop flush.

### Task 4 — Finalisation and cleanup

Fix:

* fail-closed status;
* evidence stream separation;
* reconciliation;
* remove obsolete parallel paths;
* production replay validator.

Each task must pass its own deterministic gate before the next begins.

---

# Expected end state

When complete, Alpha should guarantee:

```text
One spoken utterance
→ one canonical identity
→ one final transcript record
→ one linked translation record
→ one source UI row
→ one translation UI row
```

Revisions update that same identity. They do not append duplicates, target another row, delete another translation, or create parallel translation jobs.

## Overall decision

We do **not** need to rewrite the whole application.

We need a controlled replacement of the transcript/translation ownership layer while preserving the working infrastructure. The best route is four repair stages followed by one controlled live validation. This is the safest way to fix the current issues and then move forward to accuracy improvement, translation quality, and meeting summaries.
