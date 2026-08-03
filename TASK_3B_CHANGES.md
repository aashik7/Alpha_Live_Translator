# Task 3B — Translation Ownership Fixes

Scope: `TASK_3A_FINDINGS.md`'s "Files touched in 3B" list, plus one necessary
wiring change (`session_runtime.py`) discovered while implementing, disclosed
below per the constraint allowing it. No audio/WASAPI/Deepgram/DeepL-transport/
UI-layout-styling file touched; no Task 1/2 file touched.

## Files changed

### `Alpha_Live_Translator/alpha/ui/main_window.py`

Replaced the flat `_translation_display_lines: list[str]` and the single
`_pending_translation_payload` / `_translation_debounce_after_id` with
identity-keyed structures:

- `self._translation_items_by_utterance: dict[str, dict]` — keyed by
  `canonical_utterance_id`. Each entry tracks `{segment_id, mark, source_version,
  state, line_text}`. `mark` is a Tkinter text mark set at the exact insertion
  point of that utterance's translation line, so it can be found and removed
  later regardless of what else has been inserted after it (the same pattern
  already used for loading-indicator marks — extended to completed lines,
  which previously had no mark at all).
- `self._pending_translations_by_utterance: dict[tuple, dict]` and
  `self._translation_debounce_after_ids: dict[tuple, Any]` — both keyed by
  `(session_id, canonical_utterance_id)`. When `canonical_utterance_id` is
  unavailable, a unique per-call key is generated (`__unkeyed_<uuid>`) instead
  of falling back to a shared `""` key, so two unidentified submissions still
  can never collide with each other.

**Item 1 fix** — `_on_store_segment_updated`: replaced
```python
lines = getattr(self, "_translation_display_lines", None)
if isinstance(lines, list) and lines:
    lines.pop()
tbox.delete("end-2l linestart", "end")
```
with a call to a new helper, `_remove_translation_item_for_utterance(canonical_utterance_id=..., source_version=...)`,
which looks up the tracked item by `canonical_utterance_id`, deletes only that
mark's line via `tbox.delete(mark_name, f"{mark_name} lineend + 1 chars")`, and
does nothing (logs `TRANSLATION_DISPLAY_UPDATE_SKIPPED`) if the id is empty, not
tracked, or the tracked item is already a *newer* `source_version` than the
revision triggering the call — the fail-closed default required by this task.

**Item 2 fix** — `submit_text_for_translation` / `_flush_pending_translation_submit`:
both now operate on the payload/timer identified by
`(session_id, canonical_utterance_id)` instead of one shared slot.
`_flush_pending_translation_submit` now takes the key as a parameter (armed via
a closure passed to `self.after(...)`). One consequence, called out explicitly:
`force_flush_previous` no longer needs to flush *another* utterance's pending
payload before setting a new one, because different utterances no longer share
a slot — it still flushes the *same* key if re-armed rapidly, preserving intent.

**Item 4 fix (defense in depth on top of the worker-layer check)** —
`_clear_translation_loading_item` (when `replace_with_text` is given) and
`_append_translation_result` (the no-`segment_id` fallback branch) both now
compare the incoming `source_version` against `_translation_items_by_utterance`'s
tracked version *before* writing anything, and skip (log
`stale_provider_result_ignored`) if the incoming version is older. The worker
(`translation_worker.py`) already rejects stale results before they reach the
UI callback (confirmed CONFIRMED CORRECT in `TASK_3A_FINDINGS.md`); this is an
explicit second gate at the exact point the task's Item 4 describes ("before
applying any translation provider result"), not a fix for an observed defect at
this layer.

Also threaded `canonical_utterance_id`/`source_version` from the `TranslationResult`
through `_handle_translation_worker_result` → `_clear_translation_loading_item`
/ `_append_translation_result` (previously only `segment_id` was threaded).

`_show_translation_loading_item` and `_clear_translation_loading_item` gained
`canonical_utterance_id`/`source_version` parameters to populate/consult the new
registry; when a loading item is cleared *without* replacement text (rejected/
failed/superseded) its stale by-utterance tracking entry is removed too, so a
later revision can't mistake it for a live item.

`_get_translated_transcript_for_copy_export` now derives export lines from
`_translation_items_by_utterance` (dict insertion order) filtered to
`state == "completed"`, instead of the removed flat list. `clear_text` and the
two `_initialize_translation`/`_start_translation_session` session-reset
functions now reset the three new dicts instead of the two removed attributes.

**Deviation, disclosed per instruction:** lines 3722 and 4285 (Task 3A's exact
citations) — `_commit_japanese_update_previous_segment` and the interim-stop-tail
`"append_missing_suffix"` handler — call `_on_store_segment_updated(speaker, merged_text)`
with no `canonical_utterance_id` at all. Investigated whether one is available in
scope: **it is not** — this is an older, string-heuristic-based "Japanese manual
mode" merge path (`_is_japanese_manual_mode()`), architecturally separate from
the canonical-identity system Task 1 built, and it operates on `previous_text`/
`current_text` strings with no canonical id anywhere in its call chain. No call-site
change was made at these two lines (there is nothing to pass). Because
`_on_store_segment_updated`'s new logic fail-closes on a missing id, these two
callers now correctly *skip* the translation-display update (logged) instead of
corrupting a wrong entry, which is what Item 1 requires — but they still cannot
benefit from Item 4's version-ordering protection, since there is no version to
check either. This is a real, only-partially-closed gap, not a bug I introduced.

**Larger discovery, not fixed, flagged prominently:** confirmed via
`alpha/constants.py` that `_is_japanese_manual_mode()`'s gating flags
(`JAPANESE_MODE_ENABLED=True`, `AUTO_LANGUAGE_ENABLED=False`,
`LANGUAGE_GATE_ENABLED=False`, `MEETING_SEGMENT_BUFFER_ENABLED=False`,
`MEETING_SEGMENT_REPAIR_ENABLED=False`) are **all currently set such that this
legacy path is structurally reachable** for Japanese sessions, at the same time
as the modern canonical Japanese pipeline (Task 2's `japanese_final_chunk_stabilizer.py`
→ `JapaneseContinuityAssembler`) is also active under the identical flag
combination. This reads as a *fourth* potential transcript-commit authority
(`main_window.py`'s own "manual mode" cross-segment-merge code, calling
`store.update_last_segment` directly), on top of the three already known from
Phase 2. This is a transcript-ownership question (Phase 2 territory — REPAIR_PLAN's
still-deferred "single canonical controller" item, already logged in
`REPAIR_PLAN.md`'s "Carried over from Phase 2" section), not a translation-ownership
one, and investigating/fixing it is out of this task's scope by file list and by
subject. Not touched. Strongly recommend a dedicated audit before Phase 4.

### `Alpha_Live_Translator/alpha/translation/translation_worker.py`

**Item 3 fix** — `enqueue_stable_segment`: renamed
`_seen_source_hashes: Set[str]` (a global, session-wide text-hash set) to
`_seen_text_hash_by_utterance_version: Dict[str, str]`, keyed by
`f"{canonical_utterance_id}|{source_version}"`. The dedup check
```python
if sid in self._seen_request_ids or text_hash in self._seen_source_hashes:
```
is now two separate checks: `sid in self._seen_request_ids` (unchanged —
catches literal re-submission of the same job id) and a lookup scoped to the
exact utterance+version key (catches exact resubmission of the *same* utterance's
*same* version, e.g. a caller retry) — not a bare global hash. When
`canonical_utterance_id` is empty, the scoped check is skipped entirely
(fail-closed: no identity to scope by, so no cross-utterance guess is made).
Two different utterances both saying "Thank you." now both get a
`translation_sequence` and both get translated. Updated the two other
`_seen_source_hashes` call sites (queue-full rollback, `reset_session`) to match.

### `Alpha_Live_Translator/alpha/transcription/japanese_sentence_assembler.py`

**Item 5 — grouping disabled, single reversible switch.** Added one module-level
constant, `JAPANESE_TRANSLATION_UNIT_GROUPING_ENABLED = False`, and wrapped the
one call that actually feeds the builder (`self._translation_unit_builder.ingest_stable_commit(...)`,
`_publish_sentence`) in `if JAPANESE_TRANSLATION_UNIT_GROUPING_ENABLED:`. Nothing
else was touched — the builder's instantiation (`__init__`), `reset()` call, and
the `flush()`/summary-log fields that read `summary_counts()`/`units_preview()`
are all left exactly as they were; with grouping disabled they simply report an
always-empty builder (0 units, ready ratio 0.0), which is accurate, not broken.
**To re-enable:** flip the one constant back to `True`. That is the entire
reversal — confirmed by inspection that no other code path branches on the
constant or assumes it is off.

This matches `TASK_3A_FINDINGS.md`'s explicit conclusion: the builder's output
was never consumed by any real translation decision (each canonical utterance is
already translated individually via the separate `duplicate_protection.py` →
`main_window.py` path), so "disabling grouping" here is a metrics-only change,
not a behavioral one for live translation — consistent with the "small, safe,
contained" assessment in the audit.

## NEEDS_REVIEW resolutions (Item 6)

- **`alpha/utils/transcript_snapshot_store.py`** — re-confirmed by finding its
  callers this task (not just re-reading the file, which was already done in
  3A): `japanese_sentence_assembler.py` (writer, for autosave),
  `alpha/utils/partial_autosave_worker.py`, and evidence/export tooling
  (`run_artifacts.py`, `accuracy_evidence_export.py`,
  `boundary_evidence_finalize.py`, `segment_count_reconciliation.py`). **Zero**
  live UI-display or translation-decision callers. **Determination: not a real
  duplicate authority** — it is a background autosave/evidence trail, confirmed
  by its own docstring and by every caller found. Its positional
  `_segments[-1]` revision bug (noted in 3A) can misattribute a revision within
  the *autosave file's own content*, but cannot affect what the user sees or
  what gets translated. **Not modified** — applying the full identity-keyed
  rule here would be disproportionate scope for an internal evidence trail with
  no live consumer, and the task's own instruction permits stating why instead
  of fixing when the answer is "no."
- **`StableTranslationJob`/`TranslationJob`/`TranslationResult`/`TranslationEvent`
  naming overlap** — already resolved in 3A (`TranslationEvent` and
  `publish_translation_event` confirmed to have zero callers repo-wide via grep;
  dead code, not a live second authority). No further action; re-stated here
  for completeness since Item 6 asks every NEEDS_REVIEW item be resolved in this
  task's report, not just 3A's.

## Deviations from plan (summary)

1. `session_runtime.py` modified (necessary wiring, not in the original 3B
   list) — its session-start reset referenced the exact attribute names being
   replaced; left alone, session restart would silently stop clearing
   translation state between sessions. Also had to reorder the reset so
   outstanding per-utterance debounce timers from the *previous* session's
   dict are cancelled before that dict is replaced with a fresh one (canceling
   after replacement would leave them orphaned and never cancelled).
2. Lines 3722/4285 in `main_window.py` — no canonical_utterance_id available at
   all in that legacy call path; fail-closed skip is the correct and complete
   fix available within this task's scope (see "Deviation, disclosed" above).
3. Discovered but not fixed: the "manual mode" legacy Japanese merge path in
   `main_window.py` appears structurally live alongside the canonical Japanese
   pipeline under current constants — flagged prominently above, not
   investigated further (Phase 2 subject matter, out of file-list and
   subject-matter scope for this task).

## Not modified (reviewed, confirmed correct or out of scope)

- `alpha/translation/deepl_client.py`, `acceptance.py`, `language_map.py`,
  `__init__.py`, `alpha/core/models.py`, `alpha/config.py`, `alpha/constants.py`
  — no decision logic, per 3A.
- Item 4's core check in `translation_worker.py::_handle_result` — already
  correct per 3A, re-verified this task, not touched.
