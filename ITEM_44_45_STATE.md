# Items 44 + 45 — resilience — live working state

**Resume point.** New session: read this, continue from the first unchecked box
in §4. Delete this file in the commit that closes item 45.

Approved by the human 2026-08-12. Item 44 is `[gate]`-marked; that approval
**satisfies the gate**.

---

## 1. What already exists — do NOT rebuild it

Both items are **partially built already**. Investigated 2026-08-12:

**Item 44 — Deepgram reconnect** (`alpha/transcription/deepgram_client.py`),
shipped earlier as "fix 5":

| requirement | status |
|---|---|
| backoff | **exists** — `_dg_backoff_seconds`, capped by `DG_RECONNECT_BACKOFF_MAX_S`, reset on first transcript after reconnect (anchor `self._dg_backoff_seconds = 1.0`) |
| buffer / replay | **exists** — `_dg_replay_buffer`, snapshotted before reconnect (anchor `replay_chunks = list(getattr(self, "_dg_replay_buffer"`) |
| single-flight reconnect | **exists** — `_dg_reconnect_lock` / `_dg_reconnecting` (anchor `def _schedule_reconnect`) |
| auto-reconnect while listening | **exists** — anchor `if self.is_listening and not self._stop_event.is_set():` |
| **mark the gap visibly** | **MISSING** — no gap marker anywhere; the only `gap` hits in that file are unrelated text-diff code |

**Item 45 — DeepL** (`alpha/translation/deepl_client.py`, `translation_worker.py`):

| requirement | status |
|---|---|
| error classification | **exists** — `DeepLError` carries `code` + `retryable`: `http_429`/`temporary_server` retryable, `quota_exceeded`/`auth_failed`/`invalid_request` not |
| retry with backoff | **exists** — `translation_worker._translate_job`, anchor `if exc.retryable and retries < int(TRANSLATION_MAX_RETRIES)` with `time.sleep(min(2.0 ** retries, 4.0))` |
| **circuit-break after N** | **MISSING** — no circuit anywhere in either file |
| **degrade visibly** | **partial** — `status_message` exists; needs a real degraded/failed state for item 47's indicator to render |
| never block the transcript | **needs verifying**, not assuming |

## 2. Scope decision

Do **only** the missing pieces. Rebuilding working reconnect/retry logic would
be an architecture change (§0 rule 1) and would risk leaving two authorities
alive (§0 rule 2).

- **44** = make the audio gap visible in the transcript.
- **45** = circuit breaker + a degraded state worth rendering, and *prove* a
  translation failure never blocks a transcript commit.

## 3. Honest limit on verification

Item 44's own sprint gate is *"60-minute session with a deliberate network drop
ends `completed` with zero content loss"* — that **cannot** be verified from
recorded runs or unit tests. This session can build and unit-test the
behaviour; the gate needs a live human session. Say so plainly rather than
implying it is signed off.

## 4. Checklist — resume from the first unchecked box

- [ ] 45: circuit breaker — opens after N consecutive failures, cools down,
      half-opens, never blocks the transcript path.
- [ ] 45: prove a DeepL outage does not block or drop a transcript commit.
- [ ] 44: visible gap marker on reconnect, with the real outage duration.
- [ ] Regression tests under `tests/`, proven to fail on reverted code.
- [ ] Full suite: **442 + new**, must stay 5F + 2E + 2S, same 7 names.
- [ ] Sprint §1 row D, §8 items 44/45, §9 (including the live-gate caveat).
- [ ] Commit, push, delete this file.

## 5. Facts — do not re-derive

- Baseline before these items: **442 tests, 5F + 2E + 2S, 7 stable names.**
- `TRANSLATION_MAX_RETRIES` already exists in constants.
- Venv `<repo>/.venv/Scripts/python.exe`; `unittest discover` only.
- Never `sed -i` on `.py`. Use Edit.
- Local uncommitted and deliberate: `alpha/constants.py` audio-retention flags.
