# Task 2F — Manual-Mode Japanese Transcript-Merge Path Repair

## Deviation from the literal instruction, agreed with the user before editing

`TASK_2E_FINDINGS.md`'s recommendation text says "route through the canonical
controller," but its own reasoning section states this "is a substantial
architectural task, not a surgical patch" — Japanese has no working single
canonical controller yet (`REPAIR_PLAN.md` Phase 4's still-open carried-over
item), and the items flowing through this call path carry no
`canonical_utterance_id` or other identity token to route with. Calling
`utterance_lifecycle.py`/`canonical_transcript_ledger.py` here would mean
writing a shell integration that doesn't actually run their validation logic
— not a real fix, and not achievable as a minimal single-pass diff.

This was surfaced to the user before any edit was made. Their explicit
choice: **apply the fail-closed pattern instead** (item 3's own permitted
alternative — "apply the identical fail-closed rule used there, whichever is
cleaner") rather than attempt real controller integration, disable the path,
or make no changes. The path is not disabled (per the audit's "do not
disable outright" reasoning — item 4 established it does real, currently
necessary work and is the default active path for Japanese sessions).

## Files changed

### `Alpha_Live_Translator/alpha/summary/transcript_store.py`

Added two new methods, `get_last_segment_if_active(speaker)` and
`update_last_segment_if_active(speaker, text, timestamp=None)`. Both:

- Only ever look at/write `self._segments[-1]` (the store's true last row) —
  never scan backward past an intervening different-speaker turn to find an
  earlier row that happens to match, which is what the old
  `get_last_segment`/`update_last_segment` do. A speaker change since is now
  a hard boundary: the caller gets `None`/`False` and must treat it as "no
  valid previous segment," not merge into a stale one.
- Use the existing `speakers_confirmed_same` helper (imported from
  `alpha.transcription.speaker_boundary_guard`, Task 2D's shared,
  already-fixed function — **routed through it, not reimplemented**) instead
  of a raw `==`/`!=` comparison, so two unknown/`None` speakers are never
  treated as confirmed-same (closes a gap the raw comparison would have left:
  `None != None` is `False`, i.e. "no mismatch," which is wrong).

The original `get_last_segment`/`update_last_segment` methods are
**unchanged** — every other existing caller (`duplicate_protection.py`, the
generic `decide_transcript_action` dedup path, `_try_segment_repair`, etc.)
keeps its current behavior exactly. This was a deliberate choice to keep the
change strictly additive and scoped to the one path this audit covers,
rather than changing shared semantics that other, already-validated code
depends on.

Each new method has a one-line-plus comment tagging
`fixes TASK_2E_FINDINGS.md item 3`.

### `Alpha_Live_Translator/alpha/ui/main_window.py`

Two call-site edits, both inside `_commit_transcript_item_to_store` /
`_commit_japanese_update_previous_segment` — the only two places in the file
that read or write the transcript store specifically for the manual-mode
Japanese cross-segment merge decision:

1. **Read side** (`_commit_transcript_item_to_store`, the
   `if self._is_japanese_manual_mode() and previous_text and text:` guard):
   added `and self.transcript_store.get_last_segment_if_active(speaker) is not None`
   to the condition. This does **not** change what `previous_text` itself
   holds (it is still computed from the original, unrestricted
   `get_last_segment(speaker)` a few lines above, and is still passed
   unchanged into `decide_transcript_action(previous_text, text)`, which is
   shared with the English path). It only gates whether the Japanese
   cross-segment-merge functions (`_evaluate_japanese_cross_segment_merge` →
   `_commit_japanese_update_previous_segment`) are attempted at all: if the
   store's true last row is not confirmed to belong to this same speaker,
   the merge is skipped and the item falls through to the normal
   new-segment commit path instead.
2. **Write side** (`_commit_japanese_update_previous_segment`):
   `store.update_last_segment(speaker, merged_text)` →
   `store.update_last_segment_if_active(speaker, merged_text)`. This function
   has exactly one caller in the file (verified by grep), reached only from
   inside the same `_is_japanese_manual_mode()`-gated block above, so this
   is zero-risk to the English path.

Both edits carry a one-line-plus comment tagging `fixes TASK_2E_FINDINGS.md
item 3`.

**No other function in main_window.py's merge path was changed** — the
three pure-text heuristics (`_evaluate_japanese_tail_stitch`,
`_evaluate_japanese_particle_continuation`,
`_evaluate_japanese_compound_continuation`) and
`_evaluate_japanese_commit_dedup` are untouched; only the identity boundary
around when they may run and where they may write was tightened.

## Recommendation path taken, and why

**Fail-closed pattern, not literal canonical-controller routing** — see the
deviation note above. Within that, for the item-3 shared-bug instruction
("either route it through the already-fixed shared function, or apply the
identical fail-closed rule used there, whichever is cleaner"): **routed
through the already-fixed shared function** (`speakers_confirmed_same`,
Task 2D's helper) for the speaker-identity comparison itself, and **applied
the identical fail-closed rule** used in Phase 1 Fix 1 / Phase 2's hard
speaker-boundary principle ("if no exact target exists: reject, do not
update another row" / "speaker change is a hard boundary by default") for
the positional-lookup shape of the bug, since there is no existing shared
function in the codebase that performs an identity-scoped store lookup the
way `TranscriptStore` needed — `speakers_confirmed_same` only compares two
speaker values, it doesn't know about `TranscriptStore`'s segment list.

## Scope correction versus `TASK_2E_FINDINGS.md`

Investigating the exact call sites to implement this revealed one imprecision
in the audit's item 1, corrected here rather than carried into the fix:

- **`main_window.py:5020`** (`_try_segment_repair`'s
  `store.update_last_segment(...)` call) — the audit's item 1 grouped this
  with the Stop-tail path as sharing the bug. On inspection, this function
  is gated by `if not MEETING_SEGMENT_REPAIR_ENABLED: return False` at its
  top, and `_is_japanese_manual_mode()` itself returns `False` whenever
  `MEETING_SEGMENT_REPAIR_ENABLED` is `True` — the two are mutually
  exclusive by construction. With `MEETING_SEGMENT_REPAIR_ENABLED = False`
  (current default, `constants.py:178`), this call site is dead code today
  and is not part of the manual-mode path at all. **Not touched.**
- **`main_window.py:4347`** (`_recover_interim_tail_on_stop`'s
  `store.update_last_segment(...)` call, the interim Stop-tail recovery
  path) — on inspection, this function is **not** gated by
  `_is_japanese_manual_mode()` at the top; it runs for both English and
  Japanese sessions (only one interior line conditionally applies Japanese
  text cleanup). Fixing its `update_last_segment` call would therefore
  change shared, cross-language Stop-handling behavior — outside this
  audit's Japanese-manual-mode scope and a larger blast radius than
  "surgical, minimal-diff." **Deliberately not touched in this pass.**
  Flagged here as a follow-up candidate for its own dedicated, English+
  Japanese-scoped review, not silently dropped.

## Dead code

None identified as a result of this change — no function was disabled or
unwired end-to-end (the old `get_last_segment`/`update_last_segment` remain
live for their other existing callers).

## Files NOT touched, and why

- `Alpha_Live_Translator/alpha/transcription/duplicate_protection.py` — no
  conflict found; this task's fix doesn't require changing it, so per the
  constraint against touching already-Task-1-fixed files without a direct
  conflict, it was left alone. The fallback path
  (`DuplicateProtectionMixin._display_transcript_item`, still called
  unchanged from `main_window.py:5649`) is unaffected by this change.
- `Alpha_Live_Translator/alpha/transcription/japanese_final_chunk_stabilizer.py` —
  same reasoning; `block_rogue_japanese_direct_commit`'s gate logic did not
  need to change for this fix (it governs whether an item reaches the
  manual-mode path at all, which is unrelated to the identity-boundary bug
  being fixed once it's there).

## Not addressed (explicitly out of scope, not silently skipped)

- **Non-atomic commit** (item 3, tagged `NEEDS_REVIEW` rather than
  `CONFIRMED` in `TASK_2E_FINDINGS.md`) — left alone. It was not confirmed
  as an actual bug in the audit, and fixing an unconfirmed concern would not
  be a minimal/surgical change.
- **Channel-blind commit** (item 3, `CONFIRMED`) — `TranscriptStore` has no
  channel concept anywhere (no field on `TranscriptSegment`, no channel
  parameter on any method), so there is nothing to key on without a larger
  schema change to the store itself. The speaker-identity fix in this pass
  closes the specific reachable bug (cross-speaker positional jump); adding
  full channel scoping is architecture work consistent with the
  "route through canonical controller" item that was deliberately deferred
  per the user's decision above.

No tests were run in this task, per instruction. Stopping here after
producing `TASK_2F_CHANGES.md`.
