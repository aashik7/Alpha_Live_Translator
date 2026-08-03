# Task 2B — Surgical Fixes Applied (Phase 2)

Scope: only files listed in `TASK_2A_FINDINGS.md` "Files touched in 2B". No file
outside that list was modified. Audio capture, WASAPI, Deepgram/DeepL transport, UI
layout, and session-repair logic were not touched. No tests run, app not started, per
instructions.

Before editing, `japanese_sentence_assembler.py` (4,357 lines, un-read in Task 2A)
had to be read this task to locate the actual boundary/merge logic — targeted via
function-signature grep (`^class |^def |^    def `) and direct reads of the specific
functions implicated, not a full top-to-bottom read. `japanese_stable_accuracy.py`
and `japanese_boundary_stabilizer.py` (also listed as primary 2B targets) were **not**
read or modified — see Deviations.

## Files changed

### `Alpha_Live_Translator/alpha/transcription/japanese_sentence_assembler.py`

**1. `should_hold_speaker_continuation` (Fix 2 — Japanese hard speaker boundary)**

This was the actual "sticky-speaker" mechanism Task 2A's `sticky_speaker` grep missed
— it exists as `JAPANESE_SPEAKER_STICKY_MS = 5000` (a literal 5-second constant,
confirming REPAIR_PLAN.md's "hold speaker changes for up to five seconds"
description), not under the literal string searched for. Previously returned `True`
(hold/merge across a genuine speaker mismatch) in four separate cases: within the
5s sticky window with no clear buffer boundary, when the new fragment was short
(`<= 3` compact chars), when the new fragment looked incomplete and was short, or
whenever the *existing buffer* looked incomplete — this last one held regardless of
the new speaker's fragment at all.
```python
# fixes TASK_2A_FINDINGS.md Item 2: speaker change is a hard boundary by
# default. ...
return False
```
Comment tag: `# fixes TASK_2A_FINDINGS.md Item 2: ...`
`JAPANESE_SPEAKER_STICKY_MS` and `SPEAKER_CONTINUATION_MAX_COMPACT` are now unused
(only referenced inside this function's old body); left defined rather than removed,
consistent with the Task 1B precedent for `_channels_compatible()` — see Deviations.

**2. `_ingest_locked` speaker-mismatch block (Fix 2 & 5 — hard boundary, no two-turn merge)**

This is where the bug actually fired in production: `hold_speaker` was OR'd with
`buf_incomplete`, `frag_incomplete`, and `overlap_merge`, softening even a correctly
computed "don't hold" result, and the final `else: hold_speaker = True` fallback
(when neither a safe commit-boundary prefix nor a strong sentence end was found)
merged the new speaker's fragment into the old speaker's buffer *under the old
speaker's identity* — this is Task 2A's confirmed "two independent dialogue turns
end up in one committed record" bug. Replaced with an unconditional hard boundary:
```python
buf_text = buf.get("text", "")
prefix, tail, bname, btype = find_commit_boundary(buf_text)
if prefix and btype != "none":
    self._commit_partial(buf, prefix, tail, bname, btype, "speaker_change_safe_prefix")
if buf.get("text"):
    jp_accuracy_log("SPEAKER_CHANGE_HARD_BOUNDARY", ...)
    self._flush_locked("speaker_changed", force=True)
buf = None
```
The old buffer's safe prefix (if `find_commit_boundary` finds one — preserving
"strong punctuation can commit") is still split off and committed first; whatever
remains (the full buffer, or the leftover tail after a partial commit) is now always
force-flushed under its own speaker via the existing `_flush_locked(force=True)`
path, and `buf = None` makes the code fall through to the existing "open new buffer"
branch for the new speaker — so the new speaker's fragment can never land in the old
speaker's record. Comment tag: `# fixes TASK_2A_FINDINGS.md Item 2 & 5: ...`

**Timeout ("must never join a new speaker") — verified, no change needed.**
`_commit_safe_hold_timeout` (line ~1230) commits `buf.get("speaker")` and the
buffer's own text as-is; it does not merge anything from a different speaker at the
point of firing. With fix 2 above in place, a speaker change always closes the
buffer before a timeout could fire on mixed-speaker content, so this requirement is
satisfied as a consequence of fix 2, not a separate change.

### `Alpha_Live_Translator/alpha/transcription/japanese_final_chunk_stabilizer.py`

**3. `JapaneseFinalChunkStabilizer.ingest` (Fix 3 — block synthetic re-entry)**

Investigation found no existing violation: nothing in the reviewed call chain feeds
`_route_stable_publish`/`_flush_locked`/`_commit_partial` output back through
`ingest()`/`ingest_raw_final()`, and `_publish_sentence` (the function that actually
calls `execute_pipeline_commit`) already tags stop-derived/pre-flagged content as
`synthetic_record`/`synthetic_lineage` before it reaches the ledger. Rather than
leave this as an unenforced assumption, added an explicit guard at the one raw
ingress entry point in scope, directly implementing REPAIR_PLAN.md's literal rule
("Synthetic assembler output cannot re-enter Deepgram/raw ingress"):
```python
meta_check = metadata or {}
if meta_check.get("synthetic_record") or meta_check.get("synthetic_lineage"):
    jp_accuracy_log("SYNTHETIC_REENTRY_BLOCKED", ...)
    return True
```
Comment tag: `# fixes TASK_2A_FINDINGS.md Item 3: ...`. No live caller was found that
would currently trip this guard (defense-in-depth, not a fix for an observed defect).

### `Alpha_Live_Translator/alpha/transcription/utterance_lifecycle.py`

**4. `_compatible_with_active_locked` (Fix 4/5 — English timing-gap merge bug)**

Task 2A's confirmed finding: when the active utterance was in `ACTIVE_FINAL_CHUNK`
(held, incomplete) and the incoming candidate had no timing data (`cand_start < 0`)
or the active utterance's own end time was unset, the function returned `True`
unconditionally — no timing check, no text-relation check — letting any chunk with
missing timing metadata merge into whatever was currently held, regardless of
content. `timing_ok`/`text_ok` above this point already cover every case that should
legitimately merge, so the branch was removed rather than narrowed:
```python
# fixes TASK_2A_FINDINGS.md Item 4/5: ...
return False
```
Comment tag: `# fixes TASK_2A_FINDINGS.md Item 4/5: ...`. This also removed a
provably-dead nested `_timing_compatible(...)` recheck (unreachable: `timing_ok`
using the identical inputs was already checked and would have returned `True`
earlier if it could).

`DEFAULT_COMMIT_FALLBACK_MS = 2000` / `_TIMING_GAP_MAX_S = 2.5` themselves were left
unchanged — Task 2A found no bug in the threshold values, only in this one
unconditional-match branch. The other English-path requirements (buffered on
`speech_final=false`, committed on `speech_final=true`/matching `UtteranceEnd`,
timeout as last-resort only, one active record updated per utterance) were verified
against the existing code and already hold — no further change needed.

## Deviations from plan

1. **Fix 1 (single canonical controller / "return a proposal instead of acting
   directly") was not implemented as a full architectural change.** Task 2A
   confirmed the Japanese path bypasses `utterance_lifecycle.py` entirely via
   `japanese_final_chunk_stabilizer.py` → `JapaneseContinuityAssembler.ingest()` →
   `execute_pipeline_commit()`. Making the assembler literally "return a proposal to
   the controller" would require: (a) redesigning `utterance_lifecycle.py`'s public
   interface to accept and act on Japanese HOLD/EXTEND/COMMIT proposals, which it has
   no concept of today; (b) rewiring the dispatch in `deepgram_client.py`
   (`should_use_utterance_lifecycle`/`should_use_japanese_final_stabilizer` routing),
   which is **not** in the approved 2B file list and is frozen-adjacent transport
   code; (c) touching a ~1,000-line `_publish_sentence` function and its ~15 call
   sites across a 4,357-line file. This is not a "surgical, minimal-diff repair" — it
   is the rewrite REPAIR_PLAN.md itself schedules as a dedicated task. Implementing
   it as a rushed patch risked breaking a working, business-critical Japanese
   pipeline for a purity goal, so I did not attempt it. Instead, fixes 2–4 close the
   concrete behavioral gaps (wrong-speaker merging, timing-based cross-utterance
   merging) that make the second-authority arrangement dangerous *today*, while both
   authorities still funnel through the same `execute_pipeline_commit` /
   `canonical_transcript_ledger.py` atomicity/identity safety net Task 1 fixed.
   Recommend the full single-controller rewrite as its own follow-up task.
2. **`japanese_stable_accuracy.py` and `japanese_boundary_stabilizer.py`** (listed as
   primary 2B targets in Task 2A) were not read or modified. Once the actual bugs
   were located directly in `japanese_sentence_assembler.py` and were fully
   addressable there, reading these two additional files was no longer necessary for
   the four specified fixes; they remain untouched.
3. **"Speaker/channel change" — only speaker is enforced.** The
   `JapaneseContinuityAssembler` buffer has no `channel` field anywhere in this file
   (verified: zero occurrences of `channel` in `japanese_sentence_assembler.py`) —
   Japanese sessions are apparently diarized by speaker id only in this pipeline, not
   tracked per input channel. Adding channel tracking would mean threading a new
   parameter through `ingest()` → `ingest_raw_final()` → the buffer dict → every
   commit call, well beyond a surgical fix, and `japanese_final_chunk_stabilizer.py`'s
   own `ingest()` call site doesn't have a channel to pass in either. Speaker-based
   hard boundary is implemented (fix 2); channel-based boundary for Japanese is not,
   and is flagged here rather than silently skipped.
4. **Dead code left in place** (matching Task 1B's precedent): `should_hold_speaker_continuation`
   is now unused (its only call site was replaced) but left defined rather than
   deleted; `JAPANESE_SPEAKER_STICKY_MS` / `SPEAKER_CONTINUATION_MAX_COMPACT` are now
   unused constants, also left defined. Recommend removing both in a later cleanup
   pass (REPAIR_PLAN.md Phase 5 territory), not this task.
5. **`allow_late_continuation_update_previous` observed, not touched.**
   `_commit_safe_hold_timeout` sets this metadata flag on certain safe-hold-timeout
   commits but no consumer of it was found anywhere in
   `japanese_sentence_assembler.py`, `pipeline_commit_transaction.py`, or
   `canonical_transcript_ledger.py` (the latter two from Task 1's read). It appears
   to currently be a no-op / logged-only flag. Not fixed because no live behavior
   traces to it; flagged for whoever next touches the revision-decision path.

## Files reviewed, no change made

- `Alpha_Live_Translator/alpha/transcription/japanese_translation_unit_builder.py` —
  confirmed in Task 2A as a second independent authority (translation-unit grouping),
  but explicitly Phase-3 (translation ownership) territory per REPAIR_PLAN.md, not
  Phase 2 ledger-mutation. Not touched.
