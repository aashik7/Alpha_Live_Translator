# Items 60-63 — silent-failure landmines — live working state

Approved 2026-08-12. Delete this file in the closing commit.
All four were **verified against the code before any fix** — none were taken
on trust from the failed audit workflow (which returned zero findings).

---

## Verified findings

| # | Defect | Anchor | Verified how |
|---|---|---|---|
| 60 | `_assembler_commit_gate_failed` is set at 3 sites and cleared at exactly **one** — inside `reset()`. One failed proposal silently drops **every** remaining commit of the session. | `japanese_sentence_assembler.py` · `self._assembler_commit_gate_failed = True` | grep: 3 sets, 1 clear (line ~961, in `reset`) |
| 61 | Ledger disabled returns `{"ok": True, "skipped": True}` — success with nothing written, and no startup warning. | `canonical_transcript_ledger.py` · `if not CANONICAL_TRANSCRIPT_LEDGER_ENABLED` | read the return |
| 62 | `retry_pending` is discarded on the Japanese path: the call at `main_window.py` line ~6167 does not capture the return, so the handler at ~1036 never sees it. The item is **dropped instead of retried**. | `main_window.py` · `DuplicateProtectionMixin._display_transcript_item(self, item)` | grep: return not assigned |
| 63 | `_assembler_exception_recovery_buffer` is **written and never read** — grep shows init, reset, one write, zero reads. Text stashed there on an assembler exception is guaranteed lost. | `japanese_sentence_assembler.py` · `_assembler_exception_recovery_buffer` | grep: 3 hits, none a read |

## Severity, honestly

- **63** is the only guaranteed loss: a write-only variable.
- **60** is a latent landmine — never fired in the corpus (`ASSEMBLER_EXCEPTION_CAUGHT` count 0), but when it does it takes the rest of the session silently.
- **62** loses one item per occurrence (`REVISION_TARGET_RETRY_SCHEDULED` count 0 so far).
- **61** is a *visibility* problem, not a loss: it returns no `record_id`, so callers do not mistake it for a written record. Lower than first stated.

## Fix rules

The gate (60) must **not** simply be deleted — its integrity purpose is real:
do not keep committing into a broken transaction. Make it recoverable rather
than permanent, the same shape as item 45's circuit breaker. A **new utterance
is a clean slate**; the previous utterance's failed transaction must not taint
it.

63 must reuse item 43's proven pattern: stash under the lock, replay **outside**
it, guard re-entrancy. Do not invent a second recovery mechanism.

## Checklist

- [ ] 60: gate auto-clears on a new utterance; every rejection logged.
- [ ] 61: loud one-time warning when the ledger is disabled.
- [ ] 62: capture and propagate the return so the retry actually happens.
- [ ] 63: replay the recovery buffer instead of stranding it.
- [ ] Tests for each, proven to fail on reverted code.
- [ ] Full suite: **481 + new**, stay 5F + 2E + 2S, same 7 names.
- [ ] Commit, push, delete this file.

## Facts

- Baseline: **481 tests, 5F + 2E + 2S, 7 stable names.**
- Venv `<repo>/.venv/Scripts/python.exe`; `unittest discover` only. No `sed -i`.
