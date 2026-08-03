# Task 1A — Identity / Atomic-Commit Bug Map

Read-only audit. Scope limited to:
`utterance_lifecycle.py`, `duplicate_protection.py`, `pipeline_commit_transaction.py`,
`canonical_transcript_ledger.py`, `revision_metadata.py`.

**Note on context inputs:** `ROOT_CAUSE.md` at repo root does not currently contain a
root-cause audit — it contains unrelated Japanese-study textbook content (Chapter 2,
items 50–121). Its P0-1/P0-4/P0-6/P1-1 sections could not be read as instructed. This
audit instead used `REPAIR_PLAN.md` Phase 1 (which loaded correctly) as the reference
baseline for what the four target bug patterns look like. Flagging this discrepancy
for the user/task owner — findings below may not perfectly track the original
ROOT_CAUSE.md section numbering.

**Note on prior repair activity:** git status shows these files are already modified
and a new `canonical_identity_registry.py` module exists, uncommitted. Several Phase 1
items described in REPAIR_PLAN.md (`_last_committed` exact-match gating, channel-safe
`UtteranceEnd`, literal `or True`, ledger-success independent of evidence/metrics
writes) appear to already be substantially remediated in the current code. This map
records both what is already fixed (so it isn't re-broken) and what still matches the
bug patterns.

---

## 1. `_last_committed` / global "last record" used to target a revision without verifying identity

- **`canonical_transcript_ledger.py:632-637`** — `_suppress_record_unlocked` falls back
  to `active[-1]` (the last active record in the whole ledger) as the suppression
  target whenever `record_id` is empty, with no session/channel/utterance check at all.
  This is a positional "last record" authority, not an identity-verified one.
  ```python
  if record_id:
      target = _find_record_unlocked(record_id)
  else:
      active = _active_records_unlocked()
      target = active[-1] if active else None
  ```
  Confidence: **CONFIRMED**

- **`utterance_lifecycle.py:250,1119,1176-1177`** — `_last_committed` singleton still
  exists and is read as `prev = self._last_committed` inside
  `_supersede_committed_locked`. This method itself performs **no** identity check —
  it trusts that its only caller (`_ingest`, line 651-661) already validated via
  `_is_correction_of_committed_locked`. There is no defense-in-depth inside
  `_supersede_committed_locked` itself; if another code path ever called it directly,
  it would mutate `_last_committed` without re-validating channel/utterance identity.
  ```python
  prev = self._last_committed
  assert prev is not None
  original_id, target_utterance_id = self._resolve_correction_target_locked(...)
  ```
  Confidence: **NEEDS_REVIEW** (currently safe because caller pre-validates; fragile if reused)

- **`utterance_lifecycle.py:651-661,821-847`** — `_is_correction_of_committed_locked`
  (the gate in front of `_last_committed` use) *does* validate exact channel match
  (line 833) and resolves an exact `(session, channel, canonical_utterance_id)` record
  via `canonical_identity_registry.resolve_canonical_record_id` (line 835-847) before
  allowing supersede. This appears to be the Phase 1 §1 fix already applied.
  Confidence: **CONFIRMED** (fixed, not a live bug) — included for completeness/regression tracking.

- **`duplicate_protection.py:197-203`** — `previous_text` for
  `decide_transcript_action()` is fetched via
  `self.transcript_store.get_last_segment(speaker_num)`, keyed only by `speaker_num`.
  No session/channel/canonical_utterance_id is passed. `TranscriptStore` internals are
  out of scope for this read, so it's unknown whether `get_last_segment` is itself
  session-scoped. At this call site alone, the identity key is speaker-only.
  ```python
  segment = self.transcript_store.get_last_segment(speaker_num)
  if segment is not None:
      previous_text = segment.text
  ```
  Confidence: **NEEDS_REVIEW** (downstream lifecycle-decision override at lines 206-214
  narrows the blast radius, but the raw `decide_transcript_action` call itself has no
  channel/session gate)

- **`duplicate_protection.py:84-103`** (`apply_transcript_sequence`) — test-helper
  function keeps `last_by_speaker: dict[int, str]`, a per-speaker "last text" table with
  no channel/session/utterance key. Docstring marks it "test helper, no GUI," so
  production blast radius is likely nil, but it models the same anti-pattern.
  Confidence: **LIKELY** (test-only scope, but exact pattern match)

---

## 2. `channel` parameter accepted but not validated against owning utterance's channel

- **`utterance_lifecycle.py:437-504`** (`on_utterance_end`) — channel **is** validated:
  `_channel_matches_exactly(active.channel, channel)` at line 468; on mismatch, logs
  `CROSS_CHANNEL_END_IGNORED` and ignores. Confidence: **CONFIRMED** (fixed — Phase 1 §2 applied).

- **`utterance_lifecycle.py:98-104`** (`_channels_compatible`) — used by
  `_compatible_with_active_locked` (line 798) to decide if an incoming chunk belongs to
  the currently active utterance. It treats a `None` channel on *either* side as an
  automatic match:
  ```python
  def _channels_compatible(a, b) -> bool:
      if a is None or b is None:
          return True
      ...
  ```
  If channel metadata is missing/unset on either the active utterance or the incoming
  event, channel is effectively unvalidated — any channel is treated as compatible.
  This directly enables cross-channel merge when channel data is absent rather than
  explicitly equal.
  Confidence: **CONFIRMED**

- **`utterance_lifecycle.py:956`** (`_apply_active_update_locked`) — once
  `_compatible_with_active_locked` returns True (including via the `None`-permissive
  path above), the active utterance's channel is silently overwritten:
  ```python
  active.channel = channel if channel is not None else active.channel
  ```
  There is no check that the new channel equals the previous one before overwrite —
  it's a direct consequence of finding #2 above, but flagged separately because it's
  the actual mutation point.
  Confidence: **CONFIRMED**

- **`utterance_lifecycle.py:107-113`** (`_channel_matches_exactly`) — this is the
  *strict* variant (used correctly in `on_utterance_end` and
  `_is_correction_of_committed_locked`). Its existence alongside the permissive
  `_channels_compatible` means two different channel-equality semantics coexist in the
  same file, and callers must pick the right one. `_compatible_with_active_locked`
  (used for routine chunk merging) uses the permissive one.
  Confidence: **NEEDS_REVIEW** (design inconsistency, not a single-line bug)

- **`duplicate_protection.py:245,259-272`** (`_display_transcript_item`) —
  `channel_index` is read from the queued item and passed straight into
  `observe_identity(...)`, trusting whatever `channel_index`/`channel` value the caller
  attached to the dict. No local validation against an "active" channel exists at this
  layer; validation is fully delegated to `canonical_identity_registry.observe_identity`
  (out of scope for this read).
  Confidence: **NEEDS_REVIEW** (delegated, not verifiable from these 5 files alone)

---

## 3. Canonical ledger mutation point + later steps whose failure could flip caller's success to False

**Mutation points (canonical_transcript_ledger.py):**
- `_append_record_unlocked` — mutates `_records.append(rec)` at **line 431**.
- `_revise_record_unlocked` — mutates `target[...]` fields in place at **lines 519-531**.
- `_suppress_record_unlocked` — mutates `target["suppressed"]`/`target["active"]` at **lines 640-645**.
- `_suppress_candidate_unlocked` — history-only, does **not** mutate any active record (by design, line 584 docstring).

**Post-mutation steps in `pipeline_commit_transaction.py::execute_pipeline_commit`:**
- Ledger call + `ledger_applied = True` set at **line 341**, after `apply_decision` returns ok (line 306-341).
- Evidence/stage-event write: **lines 402-436**. Wrapped in `try/except`; on exception,
  sets `evidence_write_failed = True` (line 429) and logs, but does **not** return or
  raise — execution continues.
- Runtime-counter write: **lines 437-456**. Same shape; sets `metrics_write_failed = True`
  (line 448) on exception, does not return/raise.
- Final result construction: **line 473** — `success=ledger_applied`. Evidence/metrics
  failures are surfaced as separate boolean fields (`evidence_write_failed`,
  `metrics_write_failed`) but do **not** feed into `success`.

**Finding:** No place in these two files causes `success=False` after the ledger
mutation itself has already applied. This matches the REPAIR_PLAN Phase 1 §4
requirement ("A metrics or evidence failure must never trigger a second transcript
append") and appears to be an already-applied fix.
Confidence: **CONFIRMED** (fixed, not a live bug) — recorded for regression tracking.

**Residual concern:** `canonical_transcript_ledger.py` also exposes public
`append_record` (**lines 447-477**) and `revise_record` (**lines 543-571**) that call
the same `_append_record_unlocked`/`_revise_record_unlocked` mutators directly, with **no**
evidence-write or metrics-write step at all. Any caller outside the 5 audited files
that uses these public functions instead of `execute_pipeline_commit` would get a
ledger mutation with zero evidence/metrics recording and no atomic-result object.
Callers of these two public functions are outside the read scope (not in the 5 files),
so this cannot be confirmed as reachable/live, but the unguarded entry points exist.
Confidence: **NEEDS_REVIEW**

---

## 4. Fallback-append path executing after a transaction is reported failed

- **`duplicate_protection.py:367-382`** — on `txn.success is False`, the code
  increments a skip counter and `return`s. No fallback append/update of
  `transcript_store` occurs after a failed transaction.
  Confidence: **CONFIRMED** (no bug — verified the failure path returns before reaching
  `_apply_transcript_to_store` at line 444)

- **`duplicate_protection.py:412-441`** — `PipelineIntegrityError` and generic
  `Exception` handlers around the commit block both `return` immediately after logging;
  neither falls through to `_apply_transcript_to_store`.
  Confidence: **CONFIRMED** (no bug)

- **`duplicate_protection.py:155-171`** (`_apply_transcript_to_store`, action="update"
  branch) — this *is* a fallback-append, but it triggers on UI-store-level failure, not
  canonical-ledger failure: if `transcript_store.update_last_segment(...)` returns
  falsy, the code falls back to `transcript_store.add_segment(...)` (a fresh append):
  ```python
  updated = self.transcript_store.update_last_segment(...)
  if updated:
      self._transcript_stability_counters.updated += 1
  else:
      self.transcript_store.add_segment(...)
      self._transcript_stability_counters.added += 1
  ```
  This function is only reached after a successful canonical commit (or an
  already-committed item), so it's not "fallback after ledger failure" in the strict
  sense asked — but it **does not check** whether the "no update target" condition is a
  legitimate new-utterance case vs. an identity mismatch; it has no
  channel/session/canonical_utterance_id awareness of its own, and `update_last_segment`
  internals are out of scope (`transcript_store.py` not in read list).
  Confidence: **NEEDS_REVIEW**

---

## 5. `or True` / unconditional-pass conditions

- **No literal `or True` (or `True or`, `and True`) construct was found in any of the 5
  files.** REPAIR_PLAN.md's description of an `or True` bug in the correction-target
  path matches `_is_correction_of_committed_locked`
  (`utterance_lifecycle.py:821-847`), whose current final line is
  `return _text_related(prev.text, lexical)` — a real boolean check, not a
  hardcoded pass. This specific bug appears already remediated.
  Confidence: **CONFIRMED** (fixed, not a live bug)

- **`pipeline_commit_transaction.py:254-256`** and the equivalent block in
  **`canonical_transcript_ledger.py:254-258`** — functionally an unconditional-pass for
  the `"append"` action despite `RAW_EVENT_LINEAGE_REQUIRED`:
  ```python
  if RAW_EVENT_LINEAGE_REQUIRED and applied in ("append", "revise") and not stop_flush and not synthetic:
      if not ids:
          _jp_log("RAW_EVENT_LINEAGE_MISSING", applied_action=applied, transaction_id=txn_id)
          if SINGLE_REVISION_AUTHORITY_ENABLED and applied == "revise":
              return {"ok": False, "reason": "missing_revision_lineage"}
  ```
  When `applied == "append"`, a missing-lineage event is logged but the function falls
  through and the append proceeds anyway — the "required" flag only actually blocks the
  `"revise"` branch. For `"append"`, the check is pass-through regardless of the
  logged violation, i.e., equivalent in effect to an `or True` on the append path.
  Confidence: **CONFIRMED**

- **`utterance_lifecycle.py:1263`** — `"translation_eligible": speech_final is not False`
  treats any value that isn't exactly `False` (including `None`, non-boolean truthy
  garbage) as eligible. Not literally unconditional, but a permissive
  default-allow rather than an explicit default-deny.
  Confidence: **LIKELY**

- **`utterance_lifecycle.py:98-104`** (`_channels_compatible`) — see Pattern 2 finding;
  functionally a pass-through whenever either side's channel is `None`.
  Confidence: **CONFIRMED** (cross-referenced with Pattern 2)

---

## 6. Additional instances of the four bug patterns (not separately listed above)

- **`canonical_transcript_ledger.py:632-637`** — already listed under Pattern 1
  (positional `active[-1]` suppression fallback) — this is simultaneously an instance
  of "global singleton state as authority" (the whole active-records list, last entry)
  used without identity validation.

- **`duplicate_protection.py:222-226`** — `already_committed` gate:
  ```python
  already_committed = bool(
      item.get("canonical_record_id")
      or item.get("_jp_continuity_assembler")
      or item.get("canonical_ledger_committed")
  )
  ```
  If any of these three flags is set on the incoming dict (by any upstream producer —
  e.g. the Japanese assembler, out of scope), the entire identity-check +
  atomic-commit block (lines 227-441) is skipped unconditionally. This is a trust-the-
  caller pass-through: correctness depends entirely on upstream code setting these
  flags accurately, with no independent verification at this layer.
  Confidence: **NEEDS_REVIEW** (upstream producers not in read scope)

- **`utterance_lifecycle.py:116-139`** (`_timing_compatible`) — five separate `return
  True` branches with a fairly wide tolerance (`_TIMING_GAP_MAX_S = 2.5`s) and a
  no-data-available fallback (`if cand_start < 0 and prev_start < 0: return True`). Not
  a literal unconditional pass, but a permissive multi-branch OR-of-heuristics gate
  that, combined with the channel `None`-permissive check (Pattern 2/5), broadens the
  conditions under which two unrelated chunks are treated as the same utterance.
  Confidence: **NEEDS_REVIEW**

- **`revision_metadata.py`** — no bugs matching any of the 4 patterns found. This file
  is a pure metadata-normalization/assertion module
  (`normalize_applied_metadata`, `assert_metadata_consistency`); it raises
  `PipelineIntegrityError` on inconsistent flag combinations (lines 93-101) rather than
  silently passing. Confidence: **CONFIRMED** (clean)

---

## Files touched in 1B

Based on where CONFIRMED/LIKELY findings landed:

- `Alpha_Live_Translator/alpha/transcription/utterance_lifecycle.py`
  (`_channels_compatible`, `_apply_active_update_locked` channel overwrite,
  `_supersede_committed_locked` defense-in-depth, `translation_eligible` default)
- `Alpha_Live_Translator/alpha/transcription/canonical_transcript_ledger.py`
  (`_suppress_record_unlocked` positional fallback, `RAW_EVENT_LINEAGE_REQUIRED`
  append-path pass-through)
- `Alpha_Live_Translator/alpha/transcription/pipeline_commit_transaction.py`
  (duplicate/mirrored `RAW_EVENT_LINEAGE_REQUIRED` gate; unguarded public
  `append_record`/`revise_record` entry points)
- `Alpha_Live_Translator/alpha/transcription/duplicate_protection.py`
  (`get_last_segment(speaker_num)` speaker-only key, `already_committed` trust-gate,
  `update_last_segment`/`add_segment` fallback)

Not modified by findings, no action expected:
- `Alpha_Live_Translator/alpha/transcription/revision_metadata.py`

Out-of-scope modules referenced by findings above but not read (would need separate
authorization to inspect for full verification):
- `alpha/transcription/canonical_identity_registry.py`
- transcript store module backing `self.transcript_store` (`get_last_segment`,
  `update_last_segment`, `add_segment`)
- Japanese assembler / `_jp_continuity_assembler` producer
