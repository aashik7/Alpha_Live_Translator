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
