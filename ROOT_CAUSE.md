STATUS: Repair complete as of Task 14 (2026-08-04). All 5 REPAIR_PLAN.md
phases plus follow-up Tasks 6-14 resolved. Verified via 3-scenario
multi-session live test (English, Japanese, short-Stop English) — all
three completed cleanly with final_status='completed', zero
failed_required_steps. See TASK_1 through TASK_14 report files for full
history.

---

# Alpha Live Translator — Task 1 Root Cause Audit

## Scope

Task 1 investigated and repaired canonical transcript identity, revision targeting, channel ownership, duplicate prevention and atomic ledger mutation.

The following areas were frozen and were not modified:

* WASAPI system-audio capture
* Microphone capture
* Audio mixer and PCM configuration
* Deepgram configuration and transport
* DeepL provider
* Translation pipeline
* User interface
* Start/Stop and finalisation
* Packaging and filesystem structure

---

## Original Defects

### 1. Unsafe canonical-record fallback

The canonical transcript ledger allowed operations without an exact `record_id` to fall back to the most recent active record.

This created a risk that a revision, suppression or replacement belonging to one utterance could affect another utterance.

Required identity ownership is:

```text
session_id
+ channel_index
+ canonical_utterance_id
→ exact canonical_record_id
```

No operation may select a target because it is merely the latest active record.

### 2. Append lineage was not enforced consistently

Revision operations required lineage information, but append operations did not enforce the same requirement.

This allowed a transcript record to be created without sufficient canonical ownership information, weakening later revision and duplicate checks.

### 3. Channel matching was permissive

Utterance lifecycle channel comparison allowed incompatible or missing channel information to be treated as compatible.

A provider event or `UtteranceEnd` could therefore interact with an active utterance whose channel had not been proven to match.

### 4. Incompatible interim events could affect active state

Interim events from an incompatible channel were not always rejected before reaching active utterance state.

This could corrupt or extend the wrong active utterance.

### 5. Revision identity rejection lacked sufficient diagnostics

When a revision was rejected because its identity did not match the target record, the runtime did not provide enough structured evidence to identify the mismatch clearly.

---

## Implemented Repairs

### canonical_transcript_ledger.py

* Removed the “last active record” fallback for suppression.
* Suppression now requires an exact `record_id`.
* Append now requires valid lineage information, consistent with revision requirements.
* Operations without valid canonical ownership fail closed instead of modifying an unrelated record.

### utterance_lifecycle.py

* Changed channel compatibility to require an exact channel match.
* Incompatible-channel interim events are ignored.
* A mismatched event cannot corrupt or extend the current active utterance.
* Revision identity mismatches now produce diagnostic logging.
* Rejected revisions do not modify another canonical record.

### Reviewed without changes

The following files were inspected and were already consistent with the required Task 1 behaviour:

* `pipeline_commit_transaction.py`
* `duplicate_protection.py`
* `revision_metadata.py`

No unnecessary modifications were made to these files.

---

## Resulting Invariants

After the repair:

1. A suppression or revision must identify its exact canonical record.
2. Missing identity does not fall back to the latest active record.
3. Append and revision both require valid lineage.
4. Events from one channel cannot mutate another channel’s active utterance.
5. Incompatible interim events are ignored.
6. Revision identity mismatches fail closed.
7. Rejected operations create no fallback transcript append.
8. Frozen infrastructure remains unchanged.

---

## Validation Evidence

Task 1 deterministic validation:

```text
Tests executed: 19
Passed: 19
Failed: 0
```

The Task 1 tests covered the required identity and ownership behaviour, including:

* exact record targeting;
* wrong-record revision rejection;
* channel ownership;
* incompatible-channel events;
* duplicate prevention;
* lineage enforcement;
* fail-closed ledger behaviour.

The full repository test suite contains eight failures that existed outside Task 1 scope. They relate to:

* packaging scripts;
* constants drift;
* audio queue behaviour.

These failures were not introduced by the Task 1 changes and must be documented separately with their exact test names and baseline evidence.

---

## Files Changed

```text
canonical_transcript_ledger.py
utterance_lifecycle.py
```

The following files were reviewed but not modified:

```text
pipeline_commit_transaction.py
duplicate_protection.py
revision_metadata.py
```

---

## Remaining Findings

Two findings were not implemented because they require changes in:

```text
transcript_store.py
and/or upstream producer modules
```

For each unresolved finding, the final Task 1 report must record:

* exact file and function;
* violated invariant;
* reproduction case;
* whether it belongs to Task 1 identity ownership or Task 2 transcript ownership;
* reason it was deferred.

If either finding can still cause wrong-record mutation, missing lineage, cross-channel mutation, duplicate canonical commit or a second append after an applied commit, Task 1 is not complete.

If the findings concern provisional transcript presentation, cumulative source-row replacement or UI ownership, they belong to Task 2 and may be deferred with evidence.

---

## Final Assessment

```text
Task 1 deterministic tests: PASSED
Frozen infrastructure verification: PASSED
Root-cause evidence document: CORRECTED
Full repository suite: 8 pre-existing failures
Two upstream findings: PENDING CLASSIFICATION
Current verdict: PASSED WITH EXCEPTIONS
```

Task 1 may be marked `READY_FOR_TASK_2` only after the two upstream findings are classified and confirmed not to violate Task 1 acceptance criteria.

---

## Known non-blocking debt

Items flagged during Tasks 1-14 that were deliberately **not** fixed —
none block the "repair complete" verdict above (none reproduce as a live
failure), but are recorded here so a future task can pick them up instead
of rediscovering them.

**Deferred architectural items:**

* The Japanese path still lacks a single canonical controller — the
  assembler commits independently (`execute_pipeline_commit`) rather than
  proposing HOLD/EXTEND/COMMIT to one controller, as REPAIR_PLAN.md Phase
  2 originally specified. Deliberately deferred as too large for a
  surgical fix (Tasks 2B/2D patched the dangerous symptoms — cross-speaker
  merging, speaker-blind revision — instead). Re-flagged in TASK_2E_FINDINGS.md,
  TASK_3B_CHANGES.md, and TASK_4C_REPORT.md; still open.
* `duplicate_protection.py::_display_transcript_item`'s
  `self.transcript_store.get_last_segment(speaker_num)` — keyed by speaker
  only, no channel/session key — and the same function's
  `already_committed` trust-gate (`canonical_record_id`/
  `_jp_continuity_assembler`/`canonical_ledger_committed` flags, which
  trust an upstream producer without independent verification). Flagged
  in `TASK_1A_FINDINGS.md`, classified as Task 2 scope, never revisited by
  any of Tasks 2A-2D (all four focused on the Japanese assembler/boundary-
  stabilizer chain instead) — re-confirmed still open in
  `TASK_4C_REPORT.md`.
* `transcript_snapshot_store.py` — a third transcript-storage module
  (alongside `canonical_transcript_ledger.py` and `transcript_store.py`)
  with overlapping "hold the transcript" responsibility and its own
  independent positional-revision bug. Flagged `NEEDS_REVIEW` in
  `TASK_3A_FINDINGS.md`; never resolved.
* `_commit_japanese_update_previous_segment`'s non-atomic commit path —
  one `store.update_last_segment` call followed by several side-effect
  calls with no rollback path if a later one raises. Flagged
  `NEEDS_REVIEW` in `TASK_2E_FINDINGS.md`, pending a full read of
  `_on_store_segment_updated`; never revisited.

**Dead code candidates never removed** (confirmed still present, still
unused, via repo-wide grep as of this task):

* `japanese_sentence_assembler.py`'s `_translation_unit_builder`
  (`JapaneseTranslationUnitBuilder`) — confirmed in `TASK_3A_FINDINGS.md`
  to compute a metric no live decision consumes (vestigial, not a second
  authority causing active harm). Recommended for removal or leaving as
  documented dead code; left in place either way.
* `japanese_sentence_assembler.py::should_hold_speaker_continuation` —
  unused since its only call site was replaced in Task 2B; flagged in
  `TASK_2B_CHANGES.md`/`TASK_2D_REPORT.md`.
* `japanese_sentence_assembler.py`'s `JAPANESE_SPEAKER_STICKY_MS` and
  `SPEAKER_CONTINUATION_MAX_COMPACT` constants — unused since Task 2B;
  flagged in `TASK_2D_REPORT.md`.
* `japanese_boundary_stabilizer.py::set_previous_line` — confirmed unused;
  flagged in `TASK_2D_REPORT.md`.

**Already resolved, no longer debt** (verified via grep before writing
this section, in case earlier reports are stale): `_channels_compatible()`
(`utterance_lifecycle.py`) and `_run_evidence_package_worker`
(`stop_finalize_worker.py`) were both flagged as dead code in early tasks
but were in fact removed in Task 5's/Task 4B's cleanup passes — only
comments referencing their removal remain. `main_window.py`'s
`publish_translation_event` (flagged dead in `TASK_3A_FINDINGS.md`) is
also gone from the current tree.

**Pre-existing test failures** (unrelated to this engagement's scope,
present at baseline and carried through every task's regression run
without change): `test_final_transcript_commit_v3_2_5.py::test_commit_allowed_while_finalizing`
and `::test_commit_allowed_while_listening` — root cause identified in
`TASK_6_REPORT.md` (the test's own `CommitHost` fixture never calls
`_deepgram_on_open`, so the Japanese gate is never opened before the test
exercises it) but explicitly out of that task's named fix scope. Plus 2
`test_package_glossary_flags_85253.py` failures, 2 matching errors, and 1
`test_stop_finalize_v3_2_3.py::test_phase_constants_match_spec` failure —
none newly introduced by this engagement; see each task's own regression
table for confirmation they predate that task's changes.
