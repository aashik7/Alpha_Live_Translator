# Alpha Live Translator — Post-Delivery Roadmap

**Created 2026-08-11.** `CLIENT_DELIVERY_SPRINT_v5.md` §6 and item 38's
gate response both refer to this file as the destination for work
deferred past the 2026-08-24 client delivery, but no file by this name
existed anywhere in the tree. Created here, seeded only with what §6
already names plus the entries the sprint has explicitly routed here.
**No new scope was invented.** If a `ROADMAP_V5.md` exists on another
machine or account, merge that one into this rather than replacing it —
see the note in `CLIENT_DELIVERY_SPRINT_v5.md` §9 (2026-08-11).

Nothing in this file may be started before delivery. That restriction is
`CLIENT_DELIVERY_SPRINT_v5.md` §0 rule 1 and §6; this file does not
loosen it.

---

## 1. Highest value first

### Dual-channel capture

Named in sprint §6 as the highest-value item here. It makes speaker
identity a fact rather than a guess and removes most of the
cross-speaker bug family (v4 items 22, 23, 24, 33) at the source
instead of patching each symptom. Cost impact is roughly **$0.46 →
$0.92 per audio hour**; the reason for deferring is the two-week
window, not the money.

### Single canonical controller rewrite

v4 items 28–32. The three-store consolidation (item 32) belongs with
it — splitting them recreates the "two authorities alive" problem that
§0 rule 2 exists to prevent.

---

## 2. Deferred from the sprint

Carried verbatim from `CLIENT_DELIVERY_SPRINT_v5.md` §6 and §8.

| Item | What | Source |
|---|---|---|
| 25 | UI mints its own `canonical_utterance_id` | §8 Batch 4 |
| 26 | Ledger has no internal version guard | §8 Batch 4 |
| 27 | `_observe_identity` fail-open: decide from Batch 2 evidence | §8 Batch 4 |
| 27b | `_textually_related_revision` threshold tuned while lineage was constant-true | §8 Batch 4 |
| 28a, 28b, 30, 31, 32 | Controller rewrite, store consolidation, key redesign | §8 Batch 5 |
| 33 (full) | Full speaker-relabel rework — only the scoped `33s` is in the sprint | §8 Batch 5 |
| 35 | — | §6 |
| 36 | Dead-code removal | §6 |
| 37 | — | §6 |
| — | Japanese phrase-table cleanup | §6 |
| — | Translation quality work beyond item 50 | §6 |
| — | The summary feature | §6 |

Item 25 is worth pulling forward within this list: the sprint's item 38
measurement showed problem A is an identity-minting failure, and item 25
is a second, independent site where utterance ids are minted outside the
assembler. They are likely the same class of defect.

---

## 3. Routed here by the sprint

### English replay coverage — `raw_deepgram_finals.jsonl` fallback adapter

Opened by item 38's gate response, decision 4. `tools/replay_run.py`
is Japanese-only **by evidence, not by choice**: only
`japanese_final_chunk_stabilizer.py` calls `record_raw_deepgram_final`
on true ingress, so all 10 recorded English runs contain zero
genuine-ingress rows and nothing to replay.

A fallback adapter reading `raw_deepgram_finals.jsonl` was considered
and **rejected for the sprint**, for reasons that still apply here and
should be re-argued rather than assumed away:

- Two input adapters means two definitions of "what replay input is",
  which sprint §0 rule 2 forbids.
- The two files have different granularity, so run counts taken from
  them are not comparable.
- The runs that only have `raw_deepgram_finals.jsonl` predate
  `provider_events.jsonl` and are less representative of current code.

The better fix is upstream: record genuine provider ingress for English
sessions too, giving one input format for both languages. Revisit if
items 44 or 48 turn out to need English replay coverage.

### Item 38b — real-timer replay

Tracked in the sprint's §8 ledger (item 38b) because items 44 and 48 may
need it before delivery. Listed here so it is not lost if it slips:
fast-feed replay reproduces 0 of the 14 recorded content losses because
no timer fires mid-stream, so the assembler mints fresh ids where the
real run reused one. Driving the assembler through real
`LanguagePipelineWorker` scheduling is what would reproduce
timing-dependent behaviour.

---

## 4. Measurement, not a fix

**WER gap vs. raw Deepgram** — sprint §7 names this as the real accuracy
measure and explicitly does **not** gate the client date on it. Record
it at baseline and at delivery; improving it is work for this file.
