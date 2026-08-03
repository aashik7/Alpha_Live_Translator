# Task 1B — Surgical Fixes Applied

Scope: only files listed in TASK_1A_FINDINGS.md "Files touched in 1B". No file
outside that list was modified. No tests run, app not started, per instructions.

## Files changed

### `Alpha_Live_Translator/alpha/transcription/canonical_transcript_ledger.py`

**1. `_suppress_record_unlocked` (Fix 1 — session-scoped canonical identity)**
Removed the positional `active[-1]` ("last active record") fallback that was used
as an implicit suppression target whenever `record_id` was empty. `record_id` is
now required; missing or unresolvable target is rejected with a distinct reason
and an `IDENTITY_REJECTION` log entry instead of silently guessing the target.
```python
if not record_id:
    _jp_log("IDENTITY_REJECTION", reason="suppress_missing_exact_target", transaction_id=transaction_id)
    return {"ok": False, "reason": "suppress_record_id_required"}
target = _find_record_unlocked(record_id)
if target is None:
    _jp_log("IDENTITY_REJECTION", reason="suppress_target_not_found", transaction_id=transaction_id, record_id=record_id)
    return {"ok": False, "reason": "no_target"}
```
Comment tag: `# fixes TASK_1A_FINDINGS.md Pattern 1: ...`
Signature unchanged (`record_id: str = ""` kept for existing callers); only the
empty-`record_id` runtime behavior changed, from "guess last active record" to
"reject explicitly."

**2. `apply_decision` lineage gate (Fix 3 — remove unconditional-pass logic)**
`RAW_EVENT_LINEAGE_REQUIRED` previously only ever blocked the `"revise"` action;
`"append"` logged `RAW_EVENT_LINEAGE_MISSING` but always proceeded regardless of
the flag — an unconditional pass-through equivalent to the `or True` pattern
named in the task. The inner gate now covers both actions symmetrically:
```python
if SINGLE_REVISION_AUTHORITY_ENABLED and applied in ("append", "revise"):
    return {"ok": False, "reason": f"missing_{applied}_lineage"}
```
Comment tag: `# fixes TASK_1A_FINDINGS.md Pattern 5: ...`

### `Alpha_Live_Translator/alpha/transcription/utterance_lifecycle.py`

**3. `_compatible_with_active_locked` (Fix 2 — channel-safe mutation)**
Swapped the permissive `_channels_compatible()` call for the strict
`_channel_matches_exactly()` when deciding whether an incoming chunk may extend
the active utterance. `_channels_compatible()` treated a `None` channel on
*either* side as an automatic match, so a candidate with a known channel could
silently merge into (and overwrite the channel of) an active utterance whose
channel was unset, or vice versa. Now both sides must match exactly (including
both-`None`, which still matches).
```python
if not _channel_matches_exactly(active.channel, channel):
    return False
```
Comment tag: `# fixes TASK_1A_FINDINGS.md Pattern 2: ...`
`_channels_compatible()` itself is left defined (now unused within this file) to
keep the diff minimal and avoid touching unrelated code; see Deviations below.

**4. `_is_correction_of_committed_locked` + new `_log_identity_mismatch` helper
(Fix 1 — log identity mismatch)**
The correction-target gate already required an *exact* channel/record/utterance
match before allowing a SUPERSEDE (this was already correct), but every
rejection branch returned `False` silently — no identity-mismatch log existed
anywhere in the function, which the task explicitly requires ("no exact match →
reject revision, log identity mismatch"). Added a small helper following the
existing `jp_accuracy_log` idiom already used elsewhere in this file, and call
it from the three identity-specific rejection points (channel mismatch, no
resolvable exact target, target record/utterance mismatch). The timing-only
rejection is left unlogged since it isn't an identity mismatch.
```python
def _log_identity_mismatch(self, reason, *, prev, channel) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log
        jp_accuracy_log("IDENTITY_REJECTION", reason=reason, session_id=self._session_id,
                         active_channel=prev.channel, observed_channel=channel,
                         canonical_utterance_id=prev.utterance_id,
                         committed_record_id=prev.committed_record_id)
    except Exception:
        pass
```
Comment tag: `# fixes TASK_1A_FINDINGS.md Pattern 1: ...`
No return-value/control-flow change — purely additive logging on existing
reject paths, wrapped in the same try/except-around-import pattern used
elsewhere in this module, so it can't introduce a new failure mode.

## Files reviewed, no change made

### `Alpha_Live_Translator/alpha/transcription/pipeline_commit_transaction.py`
Fix 4 (`canonical_commit_applied` / `evidence_write_failed` / `metrics_write_failed`
as distinct outcome flags, with `success` derived only from ledger application) is
already implemented exactly as required — see `PipelineCommitResult` fields and
`execute_pipeline_commit`'s `success=ledger_applied` construction. No fallback
append exists after evidence/metrics failure. No change needed.

**Correction to TASK_1A_FINDINGS.md:** that document stated a duplicate
`RAW_EVENT_LINEAGE_REQUIRED` gate existed in this file (mirroring the one in
`canonical_transcript_ledger.py`). On re-inspection while implementing, this file
does not import or reference `RAW_EVENT_LINEAGE_REQUIRED` at all — the similar-
looking block here (`SINGLE_REVISION_AUTHORITY_ENABLED` + `UNPROVEN_REVISION_DEFAULT_ACTION`)
is a separate, intentionally revise-only gate and is not the same bug. The actual
fix for the append/revise lineage asymmetry was applied only in
`canonical_transcript_ledger.py` (see above). Flagging this correction rather than
silently editing the wrong file.

### `Alpha_Live_Translator/alpha/transcription/duplicate_protection.py`
None of the four requested fixes required a change here:
- Fix 1/2/3 concern the lifecycle/ledger decision layer, not this UI-dispatch file.
- Fix 4's success/failure handling (`if not txn.success: ... return`, and
  `evidence_write_failed`/`metrics_write_failed` logged but not treated as
  failure) was already correct and required no edit.

Two related items were found here during the 1A audit but are **not** fixed,
because fixing them would require touching a file outside the approved list:
- `get_last_segment(speaker_num)` (line 199) keys off `speaker_num` only. Full
  verification/fix would require reading/modifying the `TranscriptStore` class,
  which is not in `utterance_lifecycle.py` / `duplicate_protection.py` /
  `pipeline_commit_transaction.py` / `canonical_transcript_ledger.py` /
  `revision_metadata.py` — not in scope for this task. **STOPPING** rather than
  guessing at that file's contents.
- The `already_committed` trust-gate (lines 222-226) delegates correctness to
  whatever upstream producer set `canonical_record_id` / `_jp_continuity_assembler`
  / `canonical_ledger_committed` on the queued item (e.g. the Japanese assembler),
  which is also outside the approved file list.

### `Alpha_Live_Translator/alpha/transcription/revision_metadata.py`
No bugs found in 1A; no change made.

## Deviations from plan

1. **`_channels_compatible()` left defined but unused** in `utterance_lifecycle.py`
   after fix #3 above (its only call site was replaced). Not deleted, to keep the
   diff to the smallest correct change and avoid any risk of an overlooked second
   call site. Recommend removing it in a later cleanup pass (Phase 5 territory
   per REPAIR_PLAN.md), not this surgical task.
2. **`_suppress_record_unlocked` behavior change is stricter than before**: any
   existing caller (within or outside the 5 audited files) that relied on
   "suppress the most recent active record" by omitting `record_id` will now get
   an explicit rejection (`suppress_record_id_required`) instead of a silent
   (and unsafe) positional match. No caller of `suppress_record()` /
   `_suppress_record_unlocked` with an empty `record_id` was found in the 5
   audited files, but callers outside this file's scope were not inspected.
3. **`canonical_transcript_ledger.py` append-lineage gate is now stricter**:
   live (non-synthetic, non-stop-flush) `"append"` commits with no
   `source_raw_event_ids` will now be rejected when `RAW_EVENT_LINEAGE_REQUIRED`
   and `SINGLE_REVISION_AUTHORITY_ENABLED` are both on, matching how `"revise"`
   already behaved. This is the correct fix for the confirmed unconditional-pass
   bug.

   **Verified (read-only check of `Alpha_Live_Translator/alpha/constants.py`,
   with explicit user permission to expand scope for this one lookup):** both
   flags are `True` in the running config, so this gate is live, not dormant.
   Traced whether that's a live regression risk: in `utterance_lifecycle.py`,
   every commit path builds `event_id` with an automatic fallback
   (`event_id or f"final-{time.time_ns()}"` / `f"interim-{time.time_ns()}"`),
   and that `event_id` is always appended into `active.lineage_ids`, which
   becomes `source_raw_event_ids` on commit (`_commit_locked`). So for the
   normal lifecycle-driven commit path audited in this task,
   `source_raw_event_ids` is effectively never empty — the new rejection has
   nothing to trigger on there; it only fires for genuinely lineage-less
   appends, which is exactly the bug it closes. Producers outside the 5
   audited files (e.g. the Japanese assembler) were not inspected and could
   behave differently — if Task 2 touches that path, re-verify this gate
   against it.
4. Not read or modified: `canonical_identity_registry.py`, the `TranscriptStore`
   backing class, and the Japanese assembler — all referenced by findings but
   outside the approved file list for this task.

## Reminder (carried over from Task 1A)

`ROOT_CAUSE.md` at repo root still does not contain a root-cause audit document —
it contains unrelated Japanese-study textbook content. This was not touched (not
in scope) but should be corrected before the next task references its P0/P1
sections directly, since none of those section numbers could be verified against
actual content in this or the prior task.

## Re-verification addendum

Explicitly re-checked on request:
1. `_is_correction_of_committed_locked` final line is `return _text_related(prev.text, lexical)` alone (utterance_lifecycle.py:905) -- no `or True`. Confirmed clean, no fix needed.
2. `execute_pipeline_commit` result: `success=ledger_applied` (pipeline_commit_transaction.py:473), independent of `evidence_write_failed`/`metrics_write_failed`. Distinct outcome flags confirmed present. No fix needed.

19/19 tests re-run: OK. No code changes made.
