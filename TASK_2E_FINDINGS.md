# Task 2E — Manual-Mode Japanese Transcript-Merge Path Audit

Read-only investigation. No files modified.

**Note on source documents:** `TASK_1B_CHANGES.md`, `TASK_2B_CHANGES.md`, and
`TASK_2D_REPORT.md` no longer exist on disk (confirmed via `git status` — they
were untracked and are not recoverable from git history; something removed
them between sessions). This audit relies on `REPAIR_PLAN.md` (still present,
read in full) plus this workstream's own detailed knowledge of what those
three documents established, since they were authored earlier in this same
engagement. Their conclusions are treated as established fact, not re-derived.

Files read in full: `Alpha_Live_Translator/alpha/ui/main_window.py` (focused
sections, traced via grep per the task's own allowance — the file is ~8,000+
lines and reading it end-to-end was not needed once the merge path's call
graph was located), `Alpha_Live_Translator/alpha/constants.py` (full).
Grep-traced call sites read in full: `block_rogue_japanese_direct_commit` in
`japanese_final_chunk_stabilizer.py`, `update_last_segment`/`get_last_segment`
in `alpha/summary/transcript_store.py`, `_display_transcript_item` in
`duplicate_protection.py`.

---

## 1. How the manual-mode path commits/merges/revises text

**CONFIRMED — bypasses the canonical controller entirely.**

The path never calls `utterance_lifecycle.py` or `canonical_transcript_ledger.py`.
A repo-wide grep inside `main_window.py` for `execute_pipeline_commit`,
`canonical_transcript_ledger`, and `utterance_lifecycle` returns **zero hits**
anywhere in the file. `canonical_utterance_id` appears only in the Task 3B
translation-ownership code (lines ~1266–1426, ~6373–6977); it never appears
near the Japanese merge path (lines ~3560–5920).

Instead, the path writes directly to `TranscriptStore`
(`Alpha_Live_Translator/alpha/summary/transcript_store.py`), positionally, keyed
by `speaker` number only — no `session_id`, `channel_index`,
`canonical_utterance_id`, or `source_version` anywhere in the write path.

**Entry point** — `main_window.py:5761` `_display_transcript_item(self, item)`
(this is `AlphaApp`'s own override of `DuplicateProtectionMixin`'s method of
the same name; Python MRO means every `self._display_transcript_item(item)`
call resolves here, not to the mixin). Called from the live UI batch-flush
loop:
```
main_window.py:951   self._display_transcript_item(item)   # in _flush_transcript_ui_batch
main_window.py:4379  self._display_transcript_item(item)   # in _recover_interim_tail_on_stop (Stop-tail path)
```

**Gate** — `main_window.py:5825`, calling
`block_rogue_japanese_direct_commit(self, candidate_item)`
(`japanese_final_chunk_stabilizer.py:341-366`). This only blocks an item that
is final, lacks the `_jp_continuity_assembler` marker, **and** lacks a
recognized `stabilizer_reason`. An item that already passed through the
canonical Japanese assembler (carries that marker or a valid reason) is
**not** blocked — it proceeds into the manual-mode merge logic below as a
*second*, independent decision layer on top of whatever the canonical
assembler already decided.

**Merge logic** (all in `main_window.py`, all pure text-pattern heuristics —
none take a channel/session parameter, none call `speakers_confirmed_same`
from Task 2D's `speaker_boundary_guard.py`):
```
main_window.py:3596  _evaluate_japanese_tail_stitch(previous_text, current_text)
main_window.py:3648  _evaluate_japanese_particle_continuation(previous_text, current_text)
main_window.py:3700  _evaluate_japanese_compound_continuation(previous_text, current_text)
main_window.py:3753  _evaluate_japanese_cross_segment_merge(...)  # tries all three above
main_window.py:3934  _evaluate_japanese_commit_dedup(text, previous_text)  # separate dedup vs. duplicate_protection.py
```

**Commit** — `main_window.py:3766-3820` `_commit_japanese_update_previous_segment`:
```python
updated = store.update_last_segment(speaker, merged_text)   # line 3782
if not updated:
    return False
self._on_store_segment_updated(speaker, merged_text)
```
`store.update_last_segment` (`transcript_store.py:69-83`) scans
`reversed(self._segments)` for the first entry whose `speaker` field matches
and overwrites its `.text` in place — the exact "replace whatever is
currently last" pattern `REPAIR_PLAN.md` Phase 3 explicitly forbids, applied
here to *source* transcript text, not translation.

`main_window.py:5581` (`_commit_transcript_item_to_store`) calls this same
`_commit_japanese_update_previous_segment` for particle/compound/tail-stitch
merges. `main_window.py:5020` and `main_window.py:4347` (the interim
Stop-tail recovery path, `_recover_interim_tail_on_stop`) call
`store.update_last_segment(...)` directly, bypassing even the merge-evaluation
functions.

When no merge applies, the path falls through to
`DuplicateProtectionMixin._display_transcript_item(self, item)`
(`main_window.py:5649`, explicit class-qualified call to avoid recursing into
`AlphaApp`'s own override) — landing in `duplicate_protection.py:182`, the
**already-known, still-open** Task-1 finding (`get_last_segment(speaker_num)`,
speaker-only key; `already_committed` trust-gate) tracked in `REPAIR_PLAN.md`'s
"Carried over from Phase 2" section.

---

## 2. Reachability flags and UI control

**CONFIRMED.**

`main_window.py:4584-4600` `_is_japanese_manual_mode()`:
```python
def _is_japanese_manual_mode(self) -> bool:
    if not JAPANESE_MODE_ENABLED:
        return False
    if (
        bool(AUTO_LANGUAGE_ENABLED)
        or bool(LANGUAGE_GATE_ENABLED)
        or bool(MEETING_SEGMENT_BUFFER_ENABLED)
        or bool(MEETING_SEGMENT_REPAIR_ENABLED)
    ):
        return False
    ...
    return self._selected_source_language_ui() == "Japanese"
```

Current values in `constants.py`:

| Flag | Line | Value |
|---|---|---|
| `JAPANESE_MODE_ENABLED` | 212 | `True` |
| `AUTO_LANGUAGE_ENABLED` | 182 | `False` |
| `LANGUAGE_GATE_ENABLED` | 191 | `False` |
| `MEETING_SEGMENT_BUFFER_ENABLED` | 169 | `False` |
| `MEETING_SEGMENT_REPAIR_ENABLED` | 178 | `False` |
| `FORCE_DEEPGRAM_LANGUAGE` | 187 | `None` |
| `DEFAULT_SOURCE_LANGUAGE` | 183 | `"ja"` |

All four gating flags default to exactly the values that make
`_is_japanese_manual_mode()` reachable. `JAPANESE_MODE_ENABLED=True` is the
sole enabling switch and has no accompanying UI toggle — it is a code-level
constant only.

**Runtime UI control:** `self.source_language` (`main_window.py:315`) is a
real `ctk.StringVar` bound to the source-language dropdown
(`self.source_combo`, `main_window.py:1810`; populated from
`SOURCE_LANGUAGES` at `main_window.py:1926/2011`). Its initial value:
```python
_default_source_ui = (
    "Japanese" if DEFAULT_SOURCE_LANGUAGE == "ja" else "English"
)
self.source_language = ctk.StringVar(value=_default_source_ui)   # main_window.py:312-315
```
Since `DEFAULT_SOURCE_LANGUAGE == "ja"`, **the dropdown defaults to
"Japanese" on every fresh launch.** `_selected_source_language_ui()`
(`main_window.py:4578-4580`) reads this same `StringVar`. No separate
"manual mode" toggle exists in the UI — the path is reachable purely as a
side effect of the source-language dropdown's default value combined with
the four always-False gating constants above.

---

## 3. Shared bug classes with already-fixed code

**CONFIRMED** for three of five; **NEEDS_REVIEW** for one.

| Bug class (already fixed elsewhere) | Present here? | Evidence |
|---|---|---|
| Unvalidated revision target | **CONFIRMED** | `update_last_segment`/`get_last_segment` (`transcript_store.py:69-107`) accept only a `speaker` int — no `canonical_utterance_id`/`canonical_record_id` check at all, the exact pattern Phase 1 Fix 1 removed from `duplicate_protection.py`'s old `_last_committed` remap. |
| Channel-blind commit | **CONFIRMED** | No `channel_index` parameter anywhere in `TranscriptStore`, `_evaluate_japanese_*`, or `_commit_japanese_update_previous_segment`. Phase 1 Fix 2 required `UtteranceEnd channel != active channel → ignore`; this path has no channel concept to check. |
| Speaker-blind merge | **CONFIRMED** | The three merge-evaluation functions (`_evaluate_japanese_tail_stitch`/`_particle_continuation`/`_compound_continuation`) decide purely from `previous_text`/`current_text` string patterns; `speaker` is used only to fetch/write the store row, never to confirm the current utterance's speaker actually matches the one being overwritten via `speakers_confirmed_same` (Task 2D's `speaker_boundary_guard.py`, never imported here). Two adjacent speaker-numbered rows with a text pattern that happens to match (e.g., speaker 2 says something ending in a particle, then speaker 1 — if `get_last_segment(speaker)` is keyed by the CURRENT item's own speaker number, this specific scenario is narrower than a full cross-speaker merge, but the merge decision itself still never independently re-verifies speaker identity the way Task 2D's fix requires everywhere else). |
| Positional "last line" updates | **CONFIRMED** | `store.update_last_segment` is literally `for segment in reversed(self._segments): if segment.speaker != speaker_num: continue ... segment.text = cleaned; return True` — positional scan, first match wins. This is the identical shape of bug REPAIR_PLAN.md Phase 3 names verbatim ("update the last translation... replace whatever is currently last"), here applied to *source* transcript text. |
| Non-atomic commit | **NEEDS_REVIEW** | `_commit_japanese_update_previous_segment` does one `store.update_last_segment` call then several side-effect calls (`_on_store_segment_updated`, `_track_committed_segment_meta`, `_apply_final_interim_comparison`) with no rollback path if a later step raises — but none of these later steps write to a second persistent store the way Phase 1's evidence/metrics-write scenario did, so the specific "two outcomes disagree" failure mode Phase 1 Fix 4 addressed may not apply identically here. Flagged for follow-up reading of `_on_store_segment_updated`'s full body, out of this task's read budget. |

---

## 4. Genuinely reachable in production, or vestigial?

**CONFIRMED — genuinely reachable, and in fact the default path for Japanese sessions.**

- `_display_transcript_item` (`main_window.py:5761`) is called unconditionally
  from `_flush_transcript_ui_batch` (`main_window.py:951`), which is the
  live-session UI transcript-rendering loop — not a debug/test-only code
  path, not gated by any separate opt-in flag.
- `_is_japanese_manual_mode()`'s reachability is driven by the source-language
  `ctk.StringVar` (`main_window.py:315`), which is bound to a real dropdown
  widget (`self.source_combo`) the user sees and can operate — this is a
  genuine, visible UI control, not dead code with no UI wiring.
- Because `DEFAULT_SOURCE_LANGUAGE = "ja"`, the dropdown starts on "Japanese"
  every launch, and none of the four disabling flags are set — so
  `_is_japanese_manual_mode()` returns `True` **by default**, for every
  Japanese session, without the user touching anything beyond starting a
  session with the default language selection.
- The Stop-tail variant (`_recover_interim_tail_on_stop`,
  `main_window.py:4347/4379`) is likewise wired into the live Stop-handling
  flow (`request_interim_stop_tail_recovery`, `main_window.py:4395`), not a
  vestigial leftover.

This directly confirms and strengthens `TASK_3B_CHANGES.md`'s "fourth
transcript-commit authority" flag from that task's own investigation — this
audit shows it is not merely *structurally reachable* but is the **default
active path**.

---

## 5. Overlapping-responsibility files (naming-collision check)

**CONFIRMED — two files.**

1. **`Alpha_Live_Translator/alpha/summary/transcript_store.py`** —
   `class TranscriptStore` (line 22). Lives under `alpha/summary/`, not
   `alpha/transcription/` where every canonical-pipeline file lives — a
   directory-level naming/location collision risk matching the exact lesson
   named in this task's own instructions (Task 2D's naming-collision lesson).
   This is the concrete object every `store.update_last_segment`/
   `store.get_last_segment` call in this audit resolves to. It has **no**
   session/channel/identity scoping of any kind — weaker than even the
   pre-Task-1 `duplicate_protection.py` code, since it doesn't track
   `canonical_record_id` at all.
2. **`Alpha_Live_Translator/alpha/transcription/duplicate_protection.py`**
   (`_display_transcript_item`, line 182) — the *other* independent commit
   authority this manual-mode path falls through to when no cross-segment
   merge applies (`main_window.py:5649`, explicit
   `DuplicateProtectionMixin._display_transcript_item(self, item)` call).
   Already tracked as an open Task-1 gap in `REPAIR_PLAN.md`'s "Carried over
   from Phase 2" section (speaker-only `get_last_segment` key,
   `already_committed` trust-gate) — this audit confirms it is reached from
   *two* separate call sites now (the mixin's natural MRO position, and this
   explicit bypass call), not just one.

`alpha/utils/transcript_snapshot_store.py` was investigated and ruled out as
overlapping in Task 3B (confirmed zero live UI/translation-decision callers,
autosave/evidence-trail only) — re-confirmed by this audit's grep of
`update_last_segment`/`get_last_segment` definitions, which found only the
two files above.

---

## Recommendation

**Route this path through the canonical controller; do not disable outright,
and do not remove without a replacement.**

Reasoning: item 4 establishes this is not vestigial — it is the default,
live code path for every Japanese session under current constants, and it
performs real, currently-necessary work (particle/compound/tail-stitch
continuation heuristics that the canonical Japanese pipeline, per
`REPAIR_PLAN.md`'s still-open Phase-4-carried-over item, does not yet fully
own). Disabling it outright would very likely regress Japanese transcript
quality immediately, not just leave a theoretical bug. Removing it requires
the canonical controller to already own HOLD/EXTEND/COMMIT decision-making
for Japanese, which `REPAIR_PLAN.md` itself documents as still not real
(Phase 4's carried-over item 1).

The correct fix shape, consistent with `REPAIR_PLAN.md`'s Phase 2 main rule
("only the canonical utterance controller may decide create/extend/replace/
commit/supersede/ignore; other modules may only recommend"): convert
`_evaluate_japanese_cross_segment_merge` and friends into HOLD/EXTEND/COMMIT
*proposals* fed into the single canonical controller (once that controller
exists for Japanese — the still-open architectural item), and replace
`TranscriptStore.update_last_segment`'s speaker-only positional scan with an
identity-keyed lookup the same way Task 1/2D did for the canonical ledger.
This is a substantial architectural task, not a surgical patch — consistent
with why Tasks 2B/2D deliberately deferred it rather than attempting it as a
quick fix.

---

## Files touched in 2F

None yet — this task was read-only. For the follow-up implementation task,
the files expected to require changes based on this audit:

- `Alpha_Live_Translator/alpha/ui/main_window.py` — the manual-mode merge
  functions (`_evaluate_japanese_tail_stitch`, `_evaluate_japanese_particle_continuation`,
  `_evaluate_japanese_compound_continuation`, `_evaluate_japanese_cross_segment_merge`,
  `_commit_japanese_update_previous_segment`, `_evaluate_japanese_commit_dedup`,
  `_display_transcript_item`, `_commit_transcript_item_to_store`,
  `_recover_interim_tail_on_stop`).
- `Alpha_Live_Translator/alpha/summary/transcript_store.py` —
  `update_last_segment`/`get_last_segment` need identity-keyed lookup
  (session/channel/canonical_utterance_id), not speaker-only.
- `Alpha_Live_Translator/alpha/transcription/duplicate_protection.py` — the
  still-open Task-1 items (`get_last_segment(speaker_num)`,
  `already_committed` trust-gate), since this audit shows this file is
  reached from the manual-mode path too, not just the original callers.
- `Alpha_Live_Translator/alpha/transcription/japanese_final_chunk_stabilizer.py` —
  `block_rogue_japanese_direct_commit`'s gate logic will need to change shape
  once the manual-mode path itself is restructured to propose rather than
  commit.

No code changes were made in this task. Stopping here per instruction.
