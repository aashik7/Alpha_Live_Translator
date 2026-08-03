# Task 2D — Speaker-Boundary Fix for the Diagnosed Phase 2 Defect

Read (not re-fixed): `TASK_2C_REPORT.md`'s "Blocking finding" and "Final verdict"
sections for the exact diagnosed defect and required rule before starting.

## Bounded secondary-check result

Grepped the exact terms `apply_boundary_output`, `speaker`, `revise`, `extend`,
`append_or_revise`, `candidate_directly_extends_previous` inside the three named
files only, per the bounded-check instruction (not a general audit):

| File | Pattern hits | Verdict |
|---|---|---|
| `japanese_sentence_assembler.py` | `apply_boundary_output` (1 call site, ~L4014), `speaker` (extensive), `candidate_directly_extends_previous` (2 log sites, traced to their source) | **Another speaker-blind decision point found** — traced through to `alpha/transcription/stable_revision_decision.py::decide_stable_revision_action`, imported and called from this file. Not one of the two originally-named primary files, but discovered directly via this bounded grep's own matches (the `candidate_directly_extends_previous` string is `decide_stable_revision_action`'s literal `reason` value). Fixed under the "if one is found, fix it under the same rule" instruction — see below. |
| `japanese_final_chunk_stabilizer.py` | `speaker` only (all hits are parameter pass-through: `ingest(self, speaker, ...)` forwards to `assembler.ingest(speaker, ...)`; `block_rogue_japanese_direct_commit`'s `speaker_num` is queue metadata only) | **Clean.** No decision point here compares speaker to anything; nothing to fix. |
| `japanese_translation_unit_builder.py` | `speaker` (8 hits) | **Clean, and already correct.** Line 141: `if int(open_unit.get("speaker") or 0) != int(speaker or 0): should_split = True` — this file already gates its own append/split decision on speaker match. Confirms the Task 2A finding that this module was already speaker-aware; no fix needed. |

One additional module was read in full because the `candidate_directly_extends_previous`
trace led directly to it: `alpha/transcription/stable_revision_decision.py` (418
lines, not previously read in any prior task). It is the actual source of the
literal string found by the bounded grep, and was genuinely speaker-blind (see fix
description below).

## Root-cause correction versus TASK_2C_REPORT.md

TASK_2C_REPORT.md attributed the merge to `stable_line_revision.py` /
`japanese_boundary_stabilizer.py`. On full investigation (reading both files in
full, then tracing actual runtime values with targeted instrumentation — not
guessing), the merge chain has **three** speaker-blind points, not the two
originally diagnosed:

1. **`japanese_boundary_stabilizer.py::process()`** — the actual place the two
   speakers' text got concatenated. Its `self._previous_line` / `self._pending`
   state is a module-level singleton with zero speaker awareness; `safe_merge_text`
   and `duplicate_continuation_ratio` operate on text alone. This is the primary,
   load-bearing bug.
2. **`stable_revision_decision.py::decide_stable_revision_action`** — receives
   whatever (already-merged, in the unfixed code) text the boundary stabilizer
   produced and correctly-but-blindly decides "revise" via Rule B
   (`_direct_extension`), because it has no speaker parameter at all. It is a
   real, independent speaker-blind decision point, not just a victim of #1 — a
   coincidental textual extension between two different speakers would have hit
   this same bug even without #1.
3. **`stable_line_revision.py::apply_boundary_output`** — feeds a *separate*
   downstream artifact (the "clean active transcript" export), also speaker-blind
   as originally diagnosed, but not reachable from the specific canonical-ledger
   test in TASK_2C_REPORT.md (it's a different consumer of the same
   `boundary_stab_result`).

All three are now fixed. Verified against the exact scenario from
TASK_2C_REPORT.md section 6 with runtime tracing (not just re-running the test) —
see "Verification traces" below.

## Exact fixes, per file

### New file: `alpha/transcription/speaker_boundary_guard.py`

Architectural decision (see justification below): one shared function used by all
three decision points instead of three separate copies of the same check.

```python
def speakers_confirmed_same(active_speaker: Any, candidate_speaker: Any) -> bool:
    if active_speaker is None or candidate_speaker is None:
        return False
    return active_speaker == candidate_speaker
```

### `alpha/transcription/stable_line_revision.py` (primary file)

`apply_boundary_output`: speaker check inserted as the first thing done inside the
revise-eligible branch, before `revise_active_line` is ever called.

Before:
```python
if should_revise or action in ("revise_previous_line", ...):
    row = self.revise_active_line(text, reason=..., boundary_action=action)
    ...
    return {"applied": "revise", ...}
```
After:
```python
if should_revise or action in ("revise_previous_line", ...):
    active = self._active_line()
    active_speaker = active.get("speaker") if active else None
    if not speakers_confirmed_same(active_speaker, speaker):
        row = self.create_line(text, speaker=speaker,
                                boundary_action="append_new_line",
                                boundary_reason="speaker_boundary_forced_new_line")
        return {"applied": "append", "export": True, "stable_line_id": row["stable_line_id"]}
    row = self.revise_active_line(text, reason=..., boundary_action=action)
    ...
    return {"applied": "revise", ...}
```

### `alpha/transcription/japanese_boundary_stabilizer.py` (primary file)

`reset()`: added `self._previous_speaker = None`, `self._pending_speaker = None`.

`process()` signature: added `speaker: Any = None, previous_speaker: Any = None`.
When `previous_line` is set, `self._previous_speaker` is now set alongside it.

Before (pending-merge block):
```python
if self._pending:
    pending_age_ms = ...
    merged, merge_reason, ok = safe_merge_text(self._pending, text)
    if ok and count_japanese_chars(merged) <= ...:
        ... merge and return ...
    if pending_age_ms >= BOUNDARY_STABILIZER_HOLD_MS_MAX:
        ... timeout-emit and return ...
```
After: a new speaker-mismatch branch runs **first**, before `safe_merge_text` is
even called:
```python
if self._pending:
    pending_age_ms = ...
    if not speakers_confirmed_same(self._pending_speaker, speaker):
        emit_pending = self._pending
        self._pending = text
        self._pending_speaker = speaker
        self._pending_since = now
        ... emit emit_pending as its own line, return ...
    merged, merge_reason, ok = safe_merge_text(self._pending, text)
    ...  # unchanged
```

Before (duplicate-suppression + merge-with-previous):
```python
if JAPANESE_DUPLICATE_CONTINUATION_GUARD_ENABLED and self._previous_line:
    ratio = duplicate_continuation_ratio(self._previous_line, text)
    ...
...
if (JAPANESE_SAFE_MERGE_ENABLED and self._previous_line and (is_leading or ...)):
    merged, merge_reason, ok = safe_merge_text(self._previous_line, text)
    ...
```
After: both gated by one speaker check computed once, before either text-similarity
check runs:
```python
previous_speaker_confirmed = speakers_confirmed_same(self._previous_speaker, speaker)

if (JAPANESE_DUPLICATE_CONTINUATION_GUARD_ENABLED and self._previous_line
        and previous_speaker_confirmed):
    ratio = duplicate_continuation_ratio(self._previous_line, text)
    ...
...
if (JAPANESE_SAFE_MERGE_ENABLED and previous_speaker_confirmed and self._previous_line
        and (is_leading or ...)):
    merged, merge_reason, ok = safe_merge_text(self._previous_line, text)
    ...
```

`note_emitted(self, text, speaker=None)`: now also sets `self._previous_speaker`.

`flush_pending()`: now captures and clears `self._pending_speaker`, and includes
`result["pending_speaker"] = pending_speaker` in its return value, so a caller can
correctly attribute the flushed fragment instead of guessing.

Both `self._pending = text` assignments inside `process()` (leading-fragment-hold,
incomplete-ending-hold) now also set `self._pending_speaker = speaker`, so the
pending-merge gate above has a real value to compare on the *next* call.

### `alpha/transcription/stable_revision_decision.py` (found via bounded check, fixed per instruction)

`decide_stable_revision_action`: added `candidate_speaker: Any = None` parameter.
Speaker check inserted immediately after the "no previous record" early return,
before Rule A (exact-duplicate/no_op) and every other text-based rule.

Before:
```python
if not previous_record or not str(previous_record.get("text") or "").strip():
    decision.update(action="append", allowed=True, reason="no_active_previous_record")
    return decision

previous_text = str(previous_record.get("text") or "")
...
# RULE A -- exact duplicate
if _safe_normalize(candidate) == _safe_normalize(previous_text):
    decision.update(action="no_op", ...)
    return decision
# RULE B -- extension of the same hypothesis
if extends:
    decision.update(action="revise_previous", reason="candidate_directly_extends_previous", ...)
    return decision
```
After:
```python
if not previous_record or not str(previous_record.get("text") or "").strip():
    decision.update(action="append", allowed=True, reason="no_active_previous_record")
    return decision

previous_speaker = previous_record.get("speaker")
if not speakers_confirmed_same(previous_speaker, candidate_speaker):
    decision.update(action="append", allowed=True, reason="speaker_boundary_forced_new_line")
    return decision

previous_text = str(previous_record.get("text") or "")
... # Rules A-F unchanged below this point
```
Note: the speaker check deliberately runs *before* Rule A (exact-duplicate), not
just before Rule B — a coincidental exact-text match from a different speaker
(e.g. both say "はい") must not be silently treated as a no-op duplicate either.

### `alpha/transcription/japanese_sentence_assembler.py` (plumbing only — not a "primary fix", but necessary for the above fixes to receive real values instead of always defaulting to fail-closed)

Three call sites updated to pass the speaker values the functions above now
require. Without this wiring, every one of the fixes above would fail-closed on
*every* call (never merging even same-speaker text), which would have broken
edge-case 5. Each is a single added keyword argument at an existing call site,
justified by the bounded check's "if found, fix it" instruction — the decision
point cannot be functionally fixed without its inputs being wired.

1. `_route_stable_publish` → `stabilizer.process(...)`: added
   `previous_speaker=self._last_reliable_speaker, speaker=speaker`.
2. `_publish_sentence` → `get_boundary_stabilizer().note_emitted(cleaned)`: added
   `speaker=speaker`.
3. `_publish_sentence` → `decide_stable_revision_action(...)`: added
   `candidate_speaker=speaker`.
4. `flush()`'s stop-listening stabilizer-pending re-publish (~L954-976): now reads
   `stab_flush.get("pending_speaker")` (added in the `japanese_boundary_stabilizer.py`
   fix above) instead of unconditionally reusing `self._last_stable_commit`'s
   speaker, which would have mislabeled a genuinely different pending speaker.
   Also marks this metadata `speaker_change_confirmed: True` when a real
   `pending_speaker` is known, since it is a definitive recorded value, not a
   guess — otherwise a separate, pre-existing, unrelated heuristic in
   `_resolve_output_speaker` (`is_stop_incomplete` speaker-preservation, not
   touched/modified) would override it back to the previous speaker. This
   heuristic itself was not modified; it was only given accurate evidence to work
   with — see "Residual gap" below for the case where it isn't given accurate
   evidence.

## Architectural decision (constraint 3)

**Collapsed into one shared function**, `speakers_confirmed_same()` in a new
`speaker_boundary_guard.py` module, imported by all three decision points
(`stable_line_revision.py`, `japanese_boundary_stabilizer.py`,
`stable_revision_decision.py`).

Justification: once the bounded check surfaced a third decision point beyond the
two originally named, three independent copies of "both speakers known and equal"
would have been required, with three chances to get the fail-closed semantics
subtly wrong (e.g., forgetting the `is None` check in one copy). A new dedicated
module — rather than making one of the three peer files an implicit dependency of
the other two — avoids an arbitrary ownership choice between three otherwise
unrelated modules (a "boundary stabilizer" has no obvious reason to own the
canonical speaker-check that a "stable revision decision" module also needs, or
vice versa). The function is intentionally trivial (4 lines) and dependency-free,
so it cannot introduce a new failure mode or circular import.

## Dead code candidates (not removed — for the user to act on)

Carried over from `TASK_2B_CHANGES.md` (still accurate, not touched this task):
- `japanese_sentence_assembler.py`: `should_hold_speaker_continuation` — unused
  since Task 2B (its only call site was replaced).
- `japanese_sentence_assembler.py`: `JAPANESE_SPEAKER_STICKY_MS`,
  `SPEAKER_CONTINUATION_MAX_COMPACT` — unused constants since Task 2B.
- `japanese_sentence_assembler.py`: `metadata["allow_late_continuation_update_previous"]`
  set in `_commit_safe_hold_timeout` (~L1252-1267) — no consumer found anywhere in
  this file, `pipeline_commit_transaction.py`, or `canonical_transcript_ledger.py`.

New, found this task:
- `japanese_boundary_stabilizer.py::set_previous_line` — confirmed unused
  repository-wide (grepped `Alpha_Live_Translator/` for `set_previous_line`; only
  its own definition matches). Its sibling `note_emitted` is the one actually
  called from `japanese_sentence_assembler.py`. Its signature was still extended
  with `speaker` for API consistency with `note_emitted`, in case a future caller
  uses it, but it remains dead today.

## Residual gap (not fixed — outside this task's diagnosed defect)

`_resolve_output_speaker`'s `is_stop_incomplete` speaker-preservation heuristic
(`japanese_sentence_assembler.py`, ~L3242-3260) will still override a genuinely
different speaker's label back to the previous speaker **whenever it is called
without `speaker_change_confirmed`/`speaker_strong_evidence` in metadata and
`is_stop_incomplete=True`**. This task fixed the one call site that reaches it with
a known pending speaker (the stop-flush stabilizer-pending republish, #4 above) by
supplying that evidence. Any *other* caller of `_publish_sentence` with
`is_stop_incomplete=True` and no strong-evidence metadata would still hit this
pre-existing, intentional (not text-merge-related) heuristic. It is a distinct
mechanism from the diagnosed defect (it relabels an already-separate line; it does
not merge two lines into one), so fixing it further was judged out of this task's
scope. Flagged here with its exact location rather than silently left for someone
to rediscover.

## Possible duplicate/unused files

- **`stable_line_revision.py` vs `stable_revision_decision.py`** — confusingly
  similar names, overlapping responsibility (both decide "does this text revise or
  append a Japanese line"), independently maintained, and — before this task — each
  had its own separate speaker-blind bug. They feed different downstream artifacts
  (`stable_line_revision.py` → "clean active transcript" export;
  `stable_revision_decision.py` → the canonical ledger revision action), which is
  presumably why both exist, but the naming makes it easy to edit one while
  believing you've covered both (as `TASK_2C_REPORT.md` did). Worth a rename or a
  consolidation pass, not attempted here (would be a larger, non-surgical change).
- `japanese_translation_unit_builder.py` — noted in Task 2A/2B as a second,
  independent grouping authority (translation-unit batching); confirmed this task
  to already be correctly speaker-aware (line 141), so it is not a duplicate of the
  buggy pattern, just a third module with adjacent "group adjacent same-speaker
  text" responsibility to the two above. Lower-confidence note, not a fix target.

## Verification traces (runtime, not just test pass/fail)

Confirmed via direct instrumentation of `decide_stable_revision_action` and
`_resolve_output_speaker` that, before the fix, the second speaker's `candidate_text`
arriving at the ledger-revision decision was already the two speakers' text
concatenated (`"...いいですね。はい、本当にそうですね。"`) — proving the merge
happened upstream in `japanese_boundary_stabilizer.py`, not in
`stable_revision_decision.py` itself, before the fix. After the fix, the same trace
shows two independent, correctly-speaker-labeled `_route_stable_publish` calls and
zero merge attempts across the speaker boundary.

## Test results

Extended `Alpha_Live_Translator/tests/test_task2c_acceptance_gate.py` (per
instruction, not a parallel test system): fixed tests 3 and 5 (both were failing on
the diagnosed defect, both now assert the corrected behavior including exact-text
equality, not just "not merged"), and added test 6 for the same-speaker
no-over-correction edge case. 7 tests total.

| # | Item | Test | Result |
|---|---|---|---|
| 1 | Japanese two-speaker boundary test (exact failing case, section 6) | `test_3_japanese_speaker_change_never_merges` | **PASS** |
| 2 | Japanese timeout-then-new-speaker test | `test_5_timeout_does_not_join_new_speaker` | **PASS** |
| 3 | Full Task 2 acceptance suite, all 5 original tests | `test_1_progressive_revision_single_record`, `test_2_sentence_boundary_stays_two_records`, `test_3_japanese_speaker_change_never_merges`, `test_4_synthetic_output_never_reenters_raw_ingress` (+`test_4b` control), `test_5_timeout_does_not_join_new_speaker` | **5/5 PASS** |
| 4 | All 19 Task 1 tests | `tests.test_task1_identity_repair` + `tests.test_task1c_acceptance_gate` | **19/19 PASS** |
| 5 | Same-speaker rapid-adjacent finals still merge (no over-correction) | `test_6_same_speaker_rapid_adjacent_finals_still_merge` | **PASS** |
| 6 | Frozen-infrastructure diff check | `git status --short` (repo-wide) | **PASS — zero diffs** in WASAPI/mic capture, audio mixer, Deepgram/DeepL transport, UI layout, session-repair logic (`session_runtime.py`'s modification predates all Phase 2 tasks) |

Full command run: `python -m unittest tests.test_task2c_acceptance_gate
tests.test_task1_identity_repair tests.test_task1c_acceptance_gate -v` →
26 tests, `OK`.

## Final verdict

**Phase 2 acceptance gate: PASSED**
