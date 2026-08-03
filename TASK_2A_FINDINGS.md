# Task 2A — Transcript Ownership / Second-Authority Bug Map

Read-only audit. Context read: `REPAIR_PLAN.md` Phase 2 (main rule, Japanese path,
English path, acceptance gate), `TASK_1B_CHANGES.md`. `utterance_lifecycle.py` read
in full (already known from Task 1). Search limited to the six approved exact
strings: `japanese_final_chunk_stabilizer`, `japanese_translation_unit_builder`,
`sticky_speaker`, `_merge_lexical`, `HOLD_FINAL_CHUNK`, `commit_fallback_ms`.

**Scope-limiting fact, reported up front:** `sticky_speaker` (the exact term
REPAIR_PLAN.md uses to describe the Japanese assembler's speaker-hold behavior)
returns **zero hits anywhere** in `Alpha_Live_Translator/`. The module REPAIR_PLAN.md
is describing — the "continuity assembler" in `japanese_sentence_assembler.py` — is
4,357 lines and was reached only indirectly (one import line inside it references
`japanese_translation_unit_builder`, one of the six approved terms). None of the
other five approved strings hit meaningful content inside that file either. Per this
task's constraint ("You MAY use direct grep/search for these exact strings only —
not general exploration"), I did not invent additional search terms to locate the
speaker-boundary/concatenation logic inside that file, and did not read its 4,357
lines in full (out of proportion for a grep-scoped audit; would need its own
authorized task). Findings below distinguish what was **directly verified** (files
read in full: `utterance_lifecycle.py`, `japanese_final_chunk_stabilizer.py`,
`japanese_translation_unit_builder.py`) from what is **inferred from the import
surface** of `japanese_sentence_assembler.py` (names of functions it imports, not
their bodies) and flagged NEEDS_REVIEW accordingly.

---

## 1. Second authorities that can commit/revise/supersede without the Task 1 canonical controller

- **`alpha/transcription/japanese_final_chunk_stabilizer.py:207-230`** (`JapaneseFinalChunkStabilizer.ingest`) —
  Japanese finals never enter `utterance_lifecycle.py` at all. `utterance_lifecycle.py`'s
  own module docstring (lines 4-9) confirms this: *"Japanese finals continue through
  japanese_final_chunk_stabilizer and are not owned by this module."*
  `should_use_utterance_lifecycle()` (`utterance_lifecycle.py:1545-1559`) explicitly
  returns `False` for Japanese, deferring entirely to
  `should_use_japanese_final_stabilizer`. The stabilizer's `ingest()` does its own raw
  evidence capture, then hands text straight to the continuity assembler:
  ```python
  assembler = get_japanese_continuity_assembler(self._host)
  ...
  assembler.ingest(speaker, cleaned, assembler_metadata,
                    upstream_reason="deepgram_final", already_cleaned=True, raw_original=raw)
  ```
  None of Task 1's fixes (`canonical_identity_registry`, exact-channel gate,
  `_last_committed` exact-match gate, atomic `execute_pipeline_commit` outcome flags)
  are in this call chain at the point of `assembler.ingest()`. Whether the assembler
  itself eventually calls `execute_pipeline_commit`/`canonical_transcript_ledger.py`
  correctly could not be confirmed (assembler internals out of grep-approved scope —
  see NEEDS_REVIEW below), but the *decision to create/extend/hold/commit* is made by
  the assembler, not by `utterance_lifecycle.py`'s `UtteranceLifecycleOwner`.
  Confidence: **CONFIRMED** (second authority exists; matches REPAIR_PLAN Phase 2's
  literal problem statement — "Only the canonical utterance controller may decide...
  Other modules may make recommendations, but they cannot commit independently").

- **`alpha/transcription/japanese_translation_unit_builder.py:114-190`**
  (`JapaneseTranslationUnitBuilder.ingest_stable_commit`) — a second, independent
  grouping authority, this one downstream of a stable commit rather than upstream of
  one. It maintains its own `_open_unit` state machine (open / extend / flush) keyed
  on speaker and size, entirely separate from `utterance_lifecycle.py`'s
  `ActiveUtterance`/`_active` state:
  ```python
  if int(open_unit.get("speaker") or 0) != int(speaker or 0):
      should_split = True
  elif len(self._join_text(...)) > _MAX_UNIT_CHARS:
      should_split = True
  ```
  This does not mutate the canonical ledger itself (operates on already-committed
  text, per its docstring "Build translation-ready Japanese units from stable
  commits"), so it is a Phase-3-adjacent (translation ownership) authority rather
  than a Phase-1/2 ledger-mutation one — but it is explicitly named as a problem in
  REPAIR_PLAN.md Phase 3 ("must not run beside direct per-commit translation as a
  second authority"). Flagged here because it is a second *decision* authority
  (what counts as one translatable unit) running independently of any canonical
  controller.
  Confidence: **CONFIRMED** (exists, independent); classification as in/out of Phase
  2 ledger-mutation scope: **NEEDS_REVIEW**.

- **`alpha/transcription/japanese_final_chunk_stabilizer.py:329-354`**
  (`block_rogue_japanese_direct_commit`) — this is a *guard*, not a second authority,
  but it's evidence of a known second-commit-path risk: it exists specifically to
  block `_publish_final_transcript_segment` calls whose `commit_reason` doesn't start
  with an assembler-owned prefix (`japanese_continuity_assembler_`,
  `stop_flush_incomplete_tail`, `assembler_exception_direct_commit_fallback`). The
  fact this allowlist-by-string-prefix guard is needed at all confirms the codebase
  already assumes multiple things could try to publish a "final" Japanese segment.
  Confidence: **CONFIRMED** (guard exists); whether it is airtight (string-prefix
  matching, not identity-based) is **NEEDS_REVIEW**.

- **`alpha/transcription/canonical_transcript_ledger.py`** public `append_record()` /
  `revise_record()` (carried over from Task 1B's deviation log, not re-verified this
  task) — still unguarded entry points that bypass `execute_pipeline_commit`'s
  evidence/metrics wiring if called directly. No caller found within
  `utterance_lifecycle.py`; whether the Japanese assembler calls them directly is
  unknown (assembler internals not read this task).
  Confidence: **NEEDS_REVIEW** (carried over, not re-verified).

---

## 2. Japanese boundary logic — speaker-change hold/merge, non-overlapping concatenation

**Could not be directly verified within the approved search scope.** The relevant
code is almost certainly inside `japanese_sentence_assembler.py` (4,357 lines,
not read) and possibly `japanese_stable_accuracy.py` / `japanese_boundary_stabilizer.py`
(neither surfaced by any of the six approved strings, not read).

What **is** verifiable, from `japanese_sentence_assembler.py`'s import block
(`alpha/transcription/japanese_sentence_assembler.py:44-56`, reached via the
`japanese_translation_unit_builder` grep hit, import statements only — not the
functions' bodies):

```python
from alpha.transcription.japanese_stable_accuracy import (
    INCOMPLETE_TAIL_HOLD_MS_MAX,
    INCOMPLETE_TAIL_HOLD_MS_NORMAL,
    can_merge_punctuation_with_previous,
    has_incomplete_tail_for_hold,
    is_punctuation_start_fragment,
    merge_punctuation_fragment,
    merge_short_fragments,
    should_hold_incomplete_tail,
    should_hold_short_fragment,
)
```

This confirms the assembler imports named hold/merge machinery consistent with
REPAIR_PLAN.md's description ("incomplete-tail, timing... concatenation logic"), but:
- the literal term `sticky_speaker` is absent repo-wide, so either the
  speaker-hold behavior REPAIR_PLAN.md describes has already been renamed/removed, or
  it exists under terminology not covered by the six approved search strings;
- none of the imported names above concern *speaker* identity directly (they read as
  text/timing fragment logic, e.g. "incomplete tail," "short fragment," "punctuation
  merge") — whether/where a *speaker change* is treated as holdable rather than a hard
  boundary could not be located with the approved terms;
- the exact 5-second (or any) hold duration for a speaker change, and the exact
  conditions under which non-overlapping fragments get concatenated, are inside
  function bodies not read this task.

Confidence: **NEEDS_REVIEW** (entire item — requires a follow-up task with read
authorization for `japanese_sentence_assembler.py`, `japanese_stable_accuracy.py`,
and `japanese_boundary_stabilizer.py`).

---

## 3. Synthetic/assembler-generated text re-entering the pipeline as raw provider input

- **`alpha/transcription/japanese_final_chunk_stabilizer.py:132-145`** — the raw-side
  entry point captures evidence straight from Deepgram before any assembler
  processing: `record_raw_deepgram_final(run_id=..., speaker=speaker, raw_text=raw,
  is_final=True, ...)`. At this specific call site, the text logged as "raw" is the
  literal Deepgram output (`raw = (text or "").strip()`, line 101), not
  assembler-synthesized text. No re-entry at this point.
  Confidence: **CONFIRMED** (for this one ingress call site only).

- **`alpha/transcription/japanese_final_chunk_stabilizer.py:426-449`**
  (`patched_publish` wrapping `_publish_final_transcript_segment`) — every publish
  call, including ones the assembler itself originates, is routed through
  `block_rogue_japanese_direct_commit`, which permits it only if `commit_reason`
  starts with an assembler-owned prefix. This guards the *output/UI* side against a
  synthetic-text-as-final injection, but says nothing about whether the assembler's
  synthetic merged text is ever fed back as if it were a new raw Deepgram event
  (i.e., re-entering upstream ingestion, not just the publish gate).
  Confidence: **NEEDS_REVIEW** (guard confirmed for publish-side; upstream/raw-side
  re-entry risk unresolved — depends on `japanese_sentence_assembler.py` internals,
  not read).

- No evidence either way for the English path (`utterance_lifecycle.py`): nothing in
  the file writes assembler/synthetic output back into `on_interim`/`on_final_chunk`
  as if it were a new provider event; all mutation of `active.text` happens via
  `_merge_lexical` in-process, not via a round-trip back through provider ingress.
  Confidence: **CONFIRMED** (no re-entry path found in this file).

---

## 4. English lifecycle 2,000 ms fallback / 2.5 s timing-compatibility window

- **`utterance_lifecycle.py:54`** — `DEFAULT_COMMIT_FALLBACK_MS = 2000`. Used as the
  default for `UtteranceLifecycleOwner(commit_fallback_ms=...)` (line 234, 242) and
  overridable only via `alpha.constants.UTTERANCE_COMMIT_FALLBACK_MS`
  (`get_utterance_lifecycle`, lines 1526-1531; that constant's actual value was not
  checked this task — out of the five approved files/strings).
- **`utterance_lifecycle.py:1360-1382`** (`_arm_timeout_locked`) — arms a
  `threading.Timer(ms / 1000.0, _fire)` (or `host.after(ms, _fire)`) using
  `self._commit_fallback_ms`; on fire, calls `on_timeout()` →
  `_commit_locked(reason="inactivity_timeout_fallback", ...)`
  (lines 506-523). This is the literal "timeout may commit a buffered utterance"
  fallback REPAIR_PLAN.md flags — it commits *whatever text is currently active*,
  with no re-check that the buffered text is a complete sentence (matches
  REPAIR_PLAN's explicit requirement "Timeout must not automatically treat every
  chunk as a full sentence" — current code does not enforce that; it just commits).
  Confidence: **CONFIRMED**.
- **`utterance_lifecycle.py:48`** — `_TIMING_GAP_MAX_S = 2.5`. Used in
  `_timing_compatible()` (lines 116-139) in two separate branches:
  ```python
  if prev_end >= 0 and cand_start >= 0:
      gap = cand_start - prev_end
      if -0.25 <= gap <= _TIMING_GAP_MAX_S:
          return True
  if prev_start >= 0 and cand_start >= 0:
      gap = abs(cand_start - prev_start)
      if gap <= _TIMING_GAP_MAX_S:
          return True
  ```
  Confidence: **CONFIRMED**.
- **`utterance_lifecycle.py:845-853`** (inside `_compatible_with_active_locked`) —
  the non-overlapping-concatenation allowance REPAIR_PLAN.md describes, still present:
  ```python
  if active.state == ACTIVE_FINAL_CHUNK:
      if cand_start < 0 or active.end_time < 0:
          return True
      if _timing_compatible(active.start_time, active.end_time, cand_start, cand_end):
          return True
  ```
  When `cand_start < 0` (no timing data on the incoming chunk) and the active
  utterance is in `ACTIVE_FINAL_CHUNK`, this returns `True` **unconditionally** —
  no timing check, no text-relation check. Any incoming chunk with missing timing
  metadata will extend whatever utterance is currently held, regardless of content.
  Confidence: **CONFIRMED** — this is the single largest concrete merge-across-
  utterances risk found in the English path.

---

## 5. Two independent dialogue turns ending up in one committed record

**English (`utterance_lifecycle.py`) — CONFIRMED risk:**
- The `cand_start < 0 or active.end_time < 0: return True` branch above
  (`utterance_lifecycle.py:848-849`) is the clearest path: two genuinely separate
  utterances, if the second's chunk arrives without timing metadata while the first
  is still held (`ACTIVE_FINAL_CHUNK`, i.e. `is_final=true, speech_final=false`
  buffered), will be treated as compatible and merged via `_merge_lexical`
  (line 990), producing one committed record for two turns.
- Secondary path: `_text_related` fallback (`utterance_lifecycle.py:838-844`) allows
  a match on text overlap alone (substring/prefix relation) when timing is
  incompatible but the active utterance is non-terminal — two short, coincidentally
  overlapping fragments from different turns could satisfy this.
- Mitigating factor confirmed in the same file: when `_compatible_with_active_locked`
  correctly returns `False` (e.g. speaker or channel differ, or timing genuinely
  incompatible with data present), Case C explicitly commits the held utterance
  *before* starting a new one (`utterance_lifecycle.py:771-799`,
  `"boundary_before_new_utterance"`) — so the hard-boundary path itself is sound;
  the risk is specifically in `_compatible_with_active_locked` returning a false
  positive.

**Japanese — NEEDS_REVIEW.** REPAIR_PLAN.md's acceptance gate explicitly requires
"Japanese dialogue between two speakers must never become one merged canonical
line," which directly implies this has been an observed failure mode. The mechanism
(if still present) is inside `japanese_sentence_assembler.py`, not read this task —
see Item 2. Cannot confirm or rule out from the files available under this task's
grep constraint.

---

## Files touched in 2B

Primary (contains the unread core logic for items 2, 3, and the Japanese half of 5):
- `Alpha_Live_Translator/alpha/transcription/japanese_sentence_assembler.py`
  (4,357 lines — needs its own dedicated read/audit pass, ideally split by function
  group: speaker/channel boundary decision, timing/hold decision, concatenation,
  commit/publish call sites)
- `Alpha_Live_Translator/alpha/transcription/japanese_stable_accuracy.py`
  (imported hold/merge primitives: `should_hold_incomplete_tail`,
  `should_hold_short_fragment`, `merge_short_fragments`,
  `can_merge_punctuation_with_previous`, `merge_punctuation_fragment`,
  `has_incomplete_tail_for_hold`, `INCOMPLETE_TAIL_HOLD_MS_MAX/NORMAL`)
- `Alpha_Live_Translator/alpha/transcription/japanese_boundary_stabilizer.py`
  (name strongly suggests HOLD/EXTEND/COMMIT boundary-proposal logic per REPAIR_PLAN's
  target architecture; referenced from `flush_japanese_assembler_on_stop` but not
  otherwise inspected)

Confirmed second-authority / guard files (read this task):
- `Alpha_Live_Translator/alpha/transcription/japanese_final_chunk_stabilizer.py`
  (Item 1, 3 — Japanese ingress bypasses `utterance_lifecycle.py` entirely; houses
  `block_rogue_japanese_direct_commit`)
- `Alpha_Live_Translator/alpha/transcription/japanese_translation_unit_builder.py`
  (Item 1 — independent translation-unit grouping authority, Phase-3-adjacent)

Confirmed merge-risk file (read this task, already known from Task 1):
- `Alpha_Live_Translator/alpha/transcription/utterance_lifecycle.py` (Item 4, 5 —
  `_compatible_with_active_locked`'s missing-timing-data branch is the concrete
  English-path merge risk; `_TIMING_GAP_MAX_S` / `DEFAULT_COMMIT_FALLBACK_MS` confirmed
  live and unchanged since REPAIR_PLAN.md was written)

Not touched by 2A, referenced only in passing:
- `Alpha_Live_Translator/alpha/transcription/japanese_accuracy_cleaner.py` (imported
  by the assembler for text cleanup, not boundary/commit decisions — lower priority)
