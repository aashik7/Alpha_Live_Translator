# Items 22, 23, 33s — cross-speaker merges — live working state

**Resume point.** New session: read this, continue from the first unchecked box
in §5. Delete this file in the commit that closes 33s.

Approved by the human 2026-08-12 ("Approve to work on item 22 and 23 after that
33s. Fix them properly"). 23 and 33s are `[gate]`-marked in the ledger; that
approval **satisfies the gate** — proceed, do not re-ask.

These are sprint problem **C** ("two speakers' turns can merge into one line").

---

## 1. Item 22 — unknown speakers compare equal (fail-open)

`alpha/transcription/utterance_lifecycle.py`, in `_compatible_with_active_locked`:

```python
if int(active.speaker or 1) != int(speaker or 1):
    return False
```

Anchor: `if int(active.speaker or 1) != int(speaker or 1):`

Two **unknown** speakers (`None`/`0`) both coerce to `1`, compare equal, and the
candidate is judged compatible — so an utterance from an unidentified speaker
merges into another unidentified speaker's line. Fail-open.

`speakers_confirmed_same` (`speaker_boundary_guard.py`) is the fail-closed
primitive already used in 4 other modules: it returns False when **either**
side is None. Item 22 = use it here.

Careful: it returns True for `0 == 0`, so the falsy-but-known case must still
normalise unknown (`0`/`None`) to `None` before calling, or `0` will keep
comparing equal to `0`. Do not just swap the call in.

## 2. Item 23 — Case B merges across speaker/channel

Same file, `_ingest`. Anchor: `# Case B — final chunk, utterance incomplete`.

```python
force_new=not same_active and active is not None and active.committed,
```

`same_active` is `_compatible_with_active_locked(...)` — speaker, channel,
timing, text. Case B only forces a new utterance when the active one is
**committed**. So when `not same_active` (different speaker or channel) and the
active utterance is *uncommitted*, `force_new` is False and
`_apply_active_update_locked` **merges the two speakers' text together**.

**Case C** (anchor `# Case C — speech_final true`) gates correctly: it merges
only when `same_active or active is None or not (active.text or "").strip()`.

Item 23 = align Case B to Case C's gating.

## 3. Item 33s — relabeled speaker feeds the same-speaker check

Not yet fully traced. Lead: `japanese_sentence_assembler.py`
`def _resolve_output_speaker` — with `_speaker_stability_lock_enabled` it can
**force** `final_speaker` to a different value (`JAPANESE_SPEAKER_STABILITY_LOCK_APPLIED`
fired 8x on run `...160529`). Scoped goal: that relabeled speaker must not then
be fed into the same-speaker-extension decision, or the relabel manufactures a
false same-speaker match.

**Find the exact feed path before changing anything.**

## 4. Danger to check for both 22 and 23

Making these stricter forces MORE new utterances. That is correct for
cross-speaker merges, but it must not fragment a single speaker's continuous
speech into many short lines. Measure line counts before/after on the recorded
corpus, not just unit tests.

## 5. Checklist — resume from the first unchecked box

- [ ] Harness: two unknown speakers must NOT merge (22); a speaker change on an
      uncommitted active utterance must NOT merge (23).
- [ ] Confirm both FAIL pre-fix.
- [ ] Implement 22, then 23.
- [ ] Confirm both PASS; confirm same-speaker continuous speech still merges
      (the anti-fragmentation property).
- [ ] Trace and implement 33s.
- [ ] Regression tests under `tests/`, proven to fail on reverted code.
- [ ] Full suite: **429 + new**, must stay 5F + 2E + 2S, same 7 names.
- [ ] Sprint §1 row C, §8 items 22/23/33s, §9.
- [ ] Commit, push, delete this file.

## 6. Facts — do not re-derive

- Baseline before these items: **429 tests, 5F + 2E + 2S, 7 stable names.**
- `speakers_confirmed_same(a, b)` → False if either is None, else `a == b`.
- English uses `utterance_lifecycle.py`; Japanese routes to
  `japanese_sentence_assembler.py` instead. Items 22/23 are in the **English /
  shared** lifecycle; 33s is in the **Japanese** assembler.
- Venv `<repo>/.venv/Scripts/python.exe`; `unittest discover` only.
- Never `sed -i` on `.py`. Use Edit.
- Local uncommitted and deliberate: `alpha/constants.py` audio-retention flags.
