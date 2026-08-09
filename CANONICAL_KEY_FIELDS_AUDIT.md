# Canonical-key fields — what actually identifies an utterance

**Status: investigation complete, 2026-08-09.** Produced by
`BUG_FIX_ROADMAP.md` Batch 3 item 20 (audit §1.3). The human decision that
item required was: *fix the id, then audit* — the fix landed first, this
file is the audit half.

This file exists because two of `REPAIR_PLAN.md`'s six required
"canonical-key" fields carry **no per-utterance information**, and code
across the pipeline was written as if they did. It records what each field
really is, which consumers are affected, and what remains open.

---

## 1. What each field actually is

| Field | Reality | Per-utterance? |
|---|---|---|
| `session_id` | Real. One per listening session. | No (by design — it scopes, it does not identify) |
| `channel_index` | Deepgram's `[channel, total_channels]` pair. The session always requests **mono**, so it is **constant `[0, 1]`** for every event of every run. | **No — constant** |
| `event_id` / `deepgram_request_id` | Deepgram's **connection-level** `request_id`. One value per websocket connection, i.e. per session. | **No — constant** *(see §3: `event_id` no longer carries it)* |
| `canonical_utterance_id` | Real, minted locally per utterance. | **Yes** |
| `source_version` | Real, increments per revision of one utterance. | Yes (with the id) |
| `canonical_record_id` | Real, per ledger record. | Yes |

**Net:** of the six, only `canonical_utterance_id` + `source_version` +
`canonical_record_id` actually disambiguate. The key
`(session_id, channel_index, canonical_utterance_id)` is effectively
`(session_id, canonical_utterance_id)`.

### Evidence

Scanned every `.jsonl` under `Alpha_Live_Translator/troubleshooting/runs/`
(12,286 rows):

- `channel_index` distinct values across **all** runs: `[0, 1]` (list,
  1097×), `'0'` (string, 428×), `'[0, 1]'` (string, 44×), `None` (3×).
  Three serializations, one logical value, never varying per utterance.
- `request_id` / `deepgram_request_id`: **exactly 1 distinct value per
  run**, in every run examined.

---

## 2. `channel_index` — serialization is inconsistent but currently harmless

The three forms are **not** all equivalent as dictionary keys, so this was
checked rather than assumed:

| Value | `canonical_identity_registry._norm_channel` | `canonical_transcript_ledger._build_idempotency_key` |
|---|---|---|
| `[0, 1]` (list) | `'[0, 1]'` | `'[0, 1]'` |
| `'[0, 1]'` (str) | `'[0, 1]'` | `'[0, 1]'` |
| `'0'` (str) | `'0'` | `'0'` |

The two key builders agree with each other, and the two forms that reach
them (`[0, 1]` and `'[0, 1]'`) normalize identically — **so no key
mismatch occurs today.**

The `'0'` form appears **only** in the raw-capture streams
(`raw_deepgram_events.jsonl`, `raw_provider_events.jsonl`), never in
`canonical_commits.jsonl` or `provider_events.jsonl`. It originates from
sites that read `data.get("channel")` rather than
`data.get("channel_index")` — e.g. the `UtteranceEnd` path in
`deepgram_client.py`.

**Latent hazard, not a live bug:** if metadata carrying the `'0'` form
ever reaches a key builder, it produces `'0'` ≠ `'[0, 1]'` and the lookup
silently misses. Nothing routes it there today. Since the field is
constant anyway, the durable fix is to stop keying on it at all (§5),
not to normalize harder.

---

## 3. `event_id` — this one was doing real damage (fixed)

`_commit_final_transcript_segment` built the lifecycle's `event_id` as:

```python
meta.get("event_id") or meta.get("request_id") or f"dg-final-{time.time_ns()}"
```

`segment_metadata` never sets an `"event_id"` key, so the first term was
always `None`, `request_id` (the session constant) always won, and the
unique fallback was **unreachable dead code**.

### Why that mattered

`event_id` → `active.lineage_ids` (`utterance_lifecycle.py`) →
`source_raw_event_ids` on the ledger record →
`stable_revision_decision._lineage_overlap()`, which
`_same_revision_chain` uses as one half of its "is this the same
segment?" test.

With a session constant in every record, that overlap is **non-zero for
every pair of utterances in the session** — a constant-true check.

**Measured in production evidence:**

| Run | Records | Constant id present in |
|---|---|---|
| `...20260808-155842` (en) | 14 | **13** |
| `...20260808-133236` | 44 | **30** |

Someone had already noticed the symptom without finding the cause —
`stable_revision_decision.py` carries the comment *"Lineage overlap alone
is sticky and false-positive across adjacent utterances. Require textual
relatedness as well."* and added a text guard to compensate. That guard
was load-bearing precisely because the lineage half was inert.

**Japanese was never affected.** That path supplies per-event
`raw-NNNNNN` ids that genuinely vary (run `...155334`: 58 distinct ids
over 26 records, with real overlap only where a revision chain actually
shares source events).

### The fix

`event_id` no longer falls back to `request_id`; the per-event fallback
now applies. The connection id is **not lost** — it was already passed
separately as `deepgram_request_id` and stored on its own field.
Pinned by `tests/test_final_event_id_is_per_utterance.py`.

---

## 4. Consumers audited

| Consumer | Trusts | Verdict |
|---|---|---|
| `stable_revision_decision._lineage_overlap` / `_same_revision_chain` | `source_raw_event_ids` (from `event_id`) | **Was degraded to constant-true on the English path. Fixed in §3.** Its text-relatedness guard should now be re-evaluated: it was compensating for this. |
| `canonical_identity_registry._entry_key` | `(session_id, channel_index, canonical_utterance_id)` | Works, but `channel_index` contributes nothing. Effectively a 2-field key. Not a bug today (mono only). |
| `canonical_transcript_ledger._build_idempotency_key` | `session_id`, `channel_index`, `canonical_utterance_id`, `source_version`, decision | Same: `channel_index` is inert padding. The real uniqueness comes from the other fields. |
| `utterance_lifecycle._provider_utterance_id` | `metadata["request_id"]` before `event_id` | **Still returns the session constant — deliberately left as-is.** See §5. |
| `canonical_identity_registry` `provider_utterance_id` field | stored on the entry, exported in snapshots | **Store-only — never compared, never used as a lookup key.** So the constant value misleads evidence but drives no decision. |

---

## 5. Still open

1. **`_provider_utterance_id` still resolves to the connection id.**
   Deliberately not "fixed": the field is named for a *provider*-supplied
   id, and Deepgram does not expose a per-utterance one. Putting the
   locally-minted `dg-final-<ns>` value there would be misleading in a
   different direction. It is store-only (§4), so it costs evidence
   clarity, not correctness. **If it is ever promoted to a decision
   input, it must be fixed first.**
2. **`channel_index` should stop being part of any key.** It is constant
   on mono and adds nothing; keeping it in the key is what makes the
   `'0'` serialization a latent hazard (§2). Removing it is a
   cross-module change to two key builders and their persisted evidence
   format — larger than item 20's scope.
3. **`audit §2.7` (from item 17):** `TranscriptStore`'s `..._if_active`
   variants are keyed by **speaker only**, with no channel/session key.
   Related to (2) — both are "what should the identity key actually be?"
   and are worth deciding together.
4. **Re-evaluate `_textually_related_revision`'s role** in
   `_same_revision_chain` now that lineage overlap carries real
   information on the English path. It was tuned while the lineage half
   was inert.
