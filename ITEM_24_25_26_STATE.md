# Items 24, 25, 26 — v4 leftovers — live working state

**Resume point.** Delete this file in the commit that closes the work.
Approved 2026-08-12: "check 24, 25 and 26 … if it's important and necessary
fix it completely and properly".

---

## Findings (investigated 2026-08-12, before any change)

### Item 24 — REAL, small, fix it
`alpha/utils/transcript_snapshot_store.py`, anchor
`def revise_last_transcript_snapshot`. It accepts a `speaker` argument but
**never compares it** to `_segments[-1]`'s speaker — the parameter is only used
as a *fallback value* when the caller passes none. So it revises whatever the
literal last row is, regardless of who spoke it.

Reachable: called from `japanese_sentence_assembler.py` (anchor
`revise_last_transcript_snapshot(`) whenever `boundary_revise` is true.

This is the third sibling of items 22/23, which were just fixed. v4's own note:
*"strictly weaker than `transcript_store`'s equivalent, which at least filters
by speaker."* `transcript_store.py` already uses `speakers_confirmed_same` in
two places — copy that, do not invent a new rule.

### Item 26 — REAL, worth fixing as defence in depth
`alpha/transcription/canonical_transcript_ledger.py`, anchor
`def _revise_record_unlocked`. Confirmed: `target["final_text"] = text` with
**no comparison against `source_version`**.

Item 42 fixed this class of loss at the **caller** (the assembler's id-mint
gate). The ledger itself is still undefended, so any other caller — English
path, UI path, a future one — can still overwrite in place. Given problem A
cost 10 real sentences, the authority should refuse a stale write itself.

**Scope it as an ordering guard, not a content guard:** reject a revise whose
`source_version` is *older* than the target's. That is exactly what item 26
asks for, and it is orthogonal to item 42's content check, so the two cannot
disagree. Do **not** duplicate item 42's containment rule here — that would be
two authorities on the same question (§0 rule 2).

### Item 25 — REAL but NOT a delivery blocker; recommend deferring
`alpha/ui/main_window.py` mints `jpm-utt-{uuid4}` ids in **Japanese manual
mode** (anchors: `new_id = f"jpm-utt-`, `continuation_id = f"jpm-utt-`,
`stop_tail_utterance_id = f"jpm-utt-`).

Why it is not urgent:
- Separate namespace (`jpm-utt-` vs the assembler's `jp-utt-`), so it does not
  collide with the ids item 42's gate manages.
- Manual-mode only, not the live-session path the client demo uses.
- The current code has a comment saying it was a *deliberate* fix (carry real
  identity rather than leaving it unset).

Changing where identity is minted is precisely the kind of change §0 rule 2
wants done carefully, and it is not needed to protect the delivery. **Report,
do not fix.**

## Checklist

- [ ] Fix 24: speaker-confirmation guard, matching `transcript_store`'s.
- [ ] Fix 26: stale-`source_version` guard in the ledger revise.
- [ ] Tests for both, proven to fail on reverted code.
- [ ] Full suite: **473 + new**, stay 5F + 2E + 2S, same 7 names.
- [ ] Sprint §8/§9; record item 25 as investigated-and-deferred with reasons.
- [ ] Commit, push, delete this file.

## Facts

- Baseline before this work: **473 tests, 5F + 2E + 2S, 7 stable names.**
- `speakers_confirmed_same` is fail-closed on `None`; `_known_speaker`
  (added by item 22, in `utterance_lifecycle.py`) normalises `0`/`""`/`None`.
- Venv `<repo>/.venv/Scripts/python.exe`; `unittest discover` only.
