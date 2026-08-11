# Item 43 — quarantine becomes non-destructive — live working state

**Resume point.** New session: read this, continue from the first unchecked box
in §5. Delete this file in the commit that closes item 43.

Approved by the human 2026-08-12 ("Approve item 43 fix the issue properly").
Not `[gate]`-marked in the ledger, so implementation may proceed.

---

## 1. The defect (sprint problem B)

Quarantined Japanese fragments are **silently destroyed**. Three sites, all in
`alpha/transcription/japanese_sentence_assembler.py`:

| # | anchor | what it does |
|---|---|---|
| 1 | `def _drop_expired_quarantine_locked` | after `JAPANESE_NOISE_QUARANTINE_DROP_S` (8.0s) logs `NOISE_FRAGMENT_DROPPED` and discards the text |
| 2 | `def flush_quarantine_on_stop` | at stop, anything not a "valid short list term" logs `NOISE_FRAGMENT_DROPPED` and is discarded |
| 3 | `def _should_quarantine` | the classifier that sends text into quarantine at all — **not changed by item 43** (item 34, superseded: n is too small to tune a threshold) |

## 2. Corpus evidence, measured 2026-08-12

Across all recorded runs: **9 quarantine events, 8 drops, 1 release.**
Distinct dropped texts (the `_pending` folder mirrors the same 4):

| text | meaning | verdict |
|---|---|---|
| `寝れた、幸せ、` | "slept, happy," | **real speech** |
| `。忘れちゃうし、` | "and I forget," | **real speech** |
| `最近また` | "recently again" | **real speech** |
| `、` | a bare comma | genuine noise |

**This corrects sprint §1 problem B's "2 of 2".** It is 4 distinct drops, 3 of
them real speech. Sprint §9 updated.

## 3. HAZARD — do not commit empty/punctuation-only text

`accept_boundary_proposal` returns `success=False` with reason `"empty_text"`
when the cleaned text is blank. The assembler converts **any** proposal failure
into `self._assembler_commit_gate_failed = True`, which is cleared only in
`reset()` — so every subsequent commit for the rest of the session is silently
dropped. Feeding the bare `、` into the commit path would therefore trade a
1-fragment loss for a whole-session loss.

So "never drop" is implemented as: **commit everything that carries real
content; for zero-content fragments log a distinct, explicit event** rather than
the generic silent `NOISE_FRAGMENT_DROPPED`. Never a silent vanish either way.

## 4. Lock discipline

`_drop_expired_quarantine_locked` is called by
`language_pipeline_worker._run_quarantine_drop` **with `assembler._lock` already
held**. `_ingest_safe` acquires that lock, so committing inline would deadlock.

Use the pattern `flush_quarantine_on_stop` already uses: collect inside the
lock, commit outside it. Expired entries move to a pending list; the drain runs
unlocked from `_ingest_safe`'s entry and from `flush_quarantine_on_stop`.

## 5. Checklist — resume from the first unchecked box

- [ ] Harness: quarantine a real-speech fragment, expire it, assert the text
      reaches the ledger instead of vanishing.
- [ ] Confirm pre-fix the harness FAILS (text lost).
- [ ] Implement: expiry queues for recovery instead of dropping; unlocked drain
      commits with a `quarantine_recovered` flag; stop-flush commits all
      real-content entries.
- [ ] Confirm post-fix the harness PASSES, and that a punctuation-only fragment
      does **not** reach the commit path (hazard §3).
- [ ] Regression tests under `tests/`, proven to fail on reverted code.
- [ ] Full suite: **421 + new**, must stay 5F + 2E + 2S, same 7 names.
- [ ] Update sprint §1 row B, §8 item 43, §9 (including the 2-of-2 correction).
- [ ] Commit, push, delete this file.

## 6. Facts — do not re-derive

- Baseline before item 43: **421 tests, 5F + 2E + 2S, 7 stable names.**
- Constants: `JAPANESE_NOISE_QUARANTINE_DROP_S = 8.0`, `_SILENCE_S = 15.0`,
  `_MAX_COMPACT = 20`, `_RELEASE_COMPACT = 25`.
- Venv `<repo>/.venv/Scripts/python.exe`; `unittest discover` only.
- Never `sed -i` on `.py` (flips CRLF). Use Edit.
- `flush_quarantine_on_stop` already commits valid-short-terms via
  `_ingest_safe(..., bypass_quarantine=True, already_cleaned=True)` — reuse that
  call shape, do not invent a new one.
- Local uncommitted and deliberate: `alpha/constants.py` audio-retention flags.
