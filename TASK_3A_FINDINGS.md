# Task 3A — Translation Ownership Bug Map

Read-only audit. Context read: `REPAIR_PLAN.md` Phase 3 in full, `TASK_2D_REPORT.md`'s
closing notes (naming-collision lesson — applied explicitly, see Item 6).

## Search scope and file triage

Ran all 14 specified terms across `Alpha_Live_Translator/`. Every file a hit landed
in was opened; for the two largest hits, the file was read in full — for the two
enormous, clearly-irrelevant-by-content files below, a structural/context read
(function map + full-context grep of every hit) was substituted for a linear
line-by-line read, and that is disclosed here rather than silently done:

- `alpha/utils/accuracy_stage_capture.py` (2,260 lines) — every hit is
  export/evidence *comparison* reporting (`stable_final_text_hash_match`,
  `canonical_record_ids` in a validation-report dict), not a live decision point.
  Read via full-context grep of all matches, not linearly.
- `alpha/utils/diagnostic_test_log.py` (981 lines) — the one hit
  (`debounce`) is `_apply_responsive_layout_debounced`, a UI-layout render-debounce
  function name, unrelated to translation debounce. Not read further (falls under
  the "do not read ... UI-layout files unless a hit lands inside them" carve-out in
  spirit — the hit itself confirms irrelevance).

Also **not** read: `tools/validate_*.py`, `run_live_*_repair.py`,
`validate_translation_beta*.py`, `_patch_live_pipeline_ui.py`,
`build_live_bilingual_test_report.py`, `regression_eleven_issue_closure_852533.py`,
`verify_issue12_stage1_85261.py`, `run_multidomain_gate_85262.py`,
`_generate_python_inventory_csv.py` — these are one-off manual repair/validation
harness scripts (root-level and `tools/`), not part of the live production decision
path. Flagged here rather than silently skipped; if any is suspected to run in
production, say so and it will be read.

Files read in full and confirmed clean (config/data/formatting only, no decision
logic): `alpha/translation/deepl_client.py`, `alpha/translation/acceptance.py`,
`alpha/translation/language_map.py`, `alpha/translation/__init__.py`,
`alpha/utils/canonical_finalize.py`, `alpha/utils/transcript_evidence.py`,
`alpha/utils/ui_speaker_label.py`, `alpha/core/models.py`, `alpha/config.py`
(DeepL section), `alpha/constants.py` (TRANSLATION_* section).

---

## 1. Translation UI state updated/removed by position

- **`alpha/ui/main_window.py:1370`** (`_on_store_segment_updated`) — CONFIRMED.
  ```python
  lines = getattr(self, "_translation_display_lines", None)
  if isinstance(lines, list) and lines:
      lines.pop()
  ```
  Pops the *last* entry of a flat list with no canonical_record_id/
  canonical_utterance_id/source_version check. If the most-recently-appended
  translation line belongs to a *different* utterance than the one being revised
  (plausible, since translation results arrive asynchronously and can complete
  out of the order their source utterances were revised in), this deletes the
  wrong utterance's translation.

- **`alpha/ui/main_window.py:1375`** (same function, immediately after) — CONFIRMED.
  ```python
  tbox.delete("end-2l linestart", "end")
  ```
  Deletes the last two lines of the Tkinter translation widget by *text-widget
  position*, same defect as above, applied directly to the rendered widget in
  addition to the `_translation_display_lines` list.

- **`alpha/ui/main_window.py:6505`** (`_clear_translation_loading_item`) and
  **`:6770`** (`_append_translation_result`, non-`segment_id` fallback branch) —
  CONFIRMED. Both do `self._translation_display_lines.append(line)` — the append
  side of the same flat, unkeyed list the `.pop()` above operates on. Appending
  itself isn't wrong, but it's what makes the positional `.pop()`/`.delete()`
  above dangerous: order in the list reflects *arrival* order, not source
  identity.

- **`alpha/ui/main_window.py:8095`** (`_get_translated_transcript_for_copy_export`)
  — LIKELY. Reads `_translation_display_lines` directly for Copy/Export — so the
  positional-removal defect above also corrupts what the user copies/exports, not
  just the live display.

- **`alpha/utils/transcript_snapshot_store.py:100`** (`revise_last_transcript_snapshot`)
  — LIKELY (same anti-pattern, adjacent scope). `last = _segments[-1]` picks the
  revision target purely by list position in a module-level singleton list, with
  no canonical identity check — only `speaker` is tracked. This is the **source**
  transcript autosave snapshot, not translation display, so it's arguably outside
  item 1's literal "translation UI state" wording, but it is the identical bug
  pattern in a sibling store and is flagged per the Item 6 naming-collision
  instruction (see below — this is a *third* transcript-storage module).

---

## 2. Global/singleton pending-translation state

- **`alpha/ui/main_window.py:6324`** (`submit_text_for_translation`) — CONFIRMED,
  matches REPAIR_PLAN.md's literal description.
  ```python
  self._pending_translation_payload = {
      "text": cleaned, "speaker": speaker, ...
      "canonical_utterance_id": str(canonical_utterance_id or ""),
      "source_version": int(source_version or 1), ...
  }
  ```
  One instance attribute, not a dict keyed by `(session_id, canonical_utterance_id)`.
  Traced a concrete overwrite scenario: `_on_store_segment_added` (new utterance)
  always calls `_flush_pending_translation_submit()` before overwriting, so *that*
  path is safe. But `_on_store_segment_updated` (`replace_pending=True`, used for
  same-utterance revisions) does **not** flush first — it overwrites
  `_pending_translation_payload` unconditionally. If utterance B's revision fires
  while utterance A's *unrelated* payload is still sitting in the 120–350 ms
  debounce window (plausible with quick back-and-forth dialogue), A's payload is
  silently destroyed — never enqueued, never translated. This is worse than the
  "same-utterance debounce coalescing" REPAIR_PLAN describes: it is
  cross-utterance data loss.

- **`alpha/ui/main_window.py:6334-6340`** (same function) — CONFIRMED, same root
  cause. `self._translation_debounce_after_id` is a single shared Tk `after()`
  handle; a new submission always cancels and replaces it
  (`self.after_cancel(after_id)`), regardless of which utterance the pending timer
  belongs to.

- **`alpha/utils/session_runtime.py:62-65`** — CONFIRMED (context only, not itself
  the bug): `begin_live_session` resets `host._pending_translation_payload = None`
  and `host._translation_display_lines = []` at session start, confirming both are
  session-scoped singletons, correctly reset *between* sessions but not scoped
  *within* a session by utterance.

---

## 3. Translation deduplication scope

- **`alpha/translation/translation_worker.py:290`** (`enqueue_stable_segment`) —
  CONFIRMED, exactly the bug REPAIR_PLAN.md warns against.
  ```python
  if sid in self._seen_request_ids or text_hash in self._seen_source_hashes:
      self._counters["DUPLICATE_SUBMISSIONS_REJECTED"] += 1
      ...
      return False
  ```
  `self._seen_source_hashes: Set[str]` (line 165) is a single, session-wide,
  global set of SHA-256 hashes of submitted text — not scoped by
  `(session, canonical_utterance_id, source_version)`. Two genuinely different
  utterances that happen to say the same short phrase (REPAIR_PLAN's literal
  example: "Thank you." twice) will have the same `text_hash`, and the **second**
  legitimate utterance's translation submission will be silently rejected as a
  duplicate.
- By contrast, `self._latest_version_by_utterance: Dict[str, int]` (line 173,
  used at lines 294-310 and 712-738) is correctly scoped per
  `canonical_utterance_id` — the version-ordering logic (Item 4) is *not*
  affected by this bug; only the plain-duplicate-text rejection is.

---

## 4. Obsolete translation result overwriting a newer one

- **`alpha/translation/translation_worker.py:711-738`** (`_handle_result`) —
  CONFIRMED CORRECT (not a bug) at this layer. Version is checked against
  `self._latest_version_by_utterance` *before* a result is allowed to reach
  `on_translation_ready`; an obsolete result is marked
  `terminal_state=TERMINAL_SUPERSEDED`, `translated_text=""`, and still delivered
  to the UI callback so the loading indicator clears (matching REPAIR_PLAN's
  "loading state for both versions reaches a terminal state"). Recorded here for
  completeness since Item 4 asks to confirm the check exists, not just find its
  absence.

- **`alpha/ui/main_window.py:3703-3722`** (`_commit_japanese_update_previous_segment`)
  and **`alpha/ui/main_window.py:4278-4289`** (interim-stop-tail
  `"append_missing_suffix"` handler) — CONFIRMED gap. Both call
  `self._on_store_segment_updated(speaker, merged_text)` with **only two
  positional arguments** — `canonical_utterance_id` is never passed, so it
  defaults to `""` inside `_on_store_segment_updated`, which forwards it to
  `submit_text_for_translation(..., canonical_utterance_id="")`. Empty string is
  falsy, so at `translation_worker.py:714` (`if utterance_key:`) the *entire*
  obsolete-version-rejection check in Item 4 is skipped for any translation
  submitted through these two call sites. These are the Japanese
  cross-segment-merge continuation path and the interim-stop-tail suffix-append
  path — both plausible live-traffic paths, not edge cases.

---

## 5. Japanese translation-unit builder

**What it groups:** `alpha/transcription/japanese_translation_unit_builder.py`
(read in full in Task 2A/2B) groups adjacent **same-speaker** stable commits into
"translation units," splitting on speaker change, a 480-char size cap, or a
strong-sentence-ending boundary at ≥320 chars. This part of the Task 2A finding is
unchanged.

**Whether it's a live second authority — CORRECTED from Task 2A.** Traced its
only call site this task: `alpha/transcription/japanese_sentence_assembler.py:4175`
(`_publish_sentence`) calls `self._translation_unit_builder.ingest_stable_commit(...)`
immediately after a successful commit, but:
- its return value is never captured (no `result =`, nothing branches on it);
- its only other use is `flush()`/`emit_final_live_session_summary` reading
  `summary_counts()`/`units_preview()` to populate **log fields only**
  (`translation_unit_count`, `TRANSLATION_UNIT_READY_RATIO`,
  `translation_unit_preview` inside a single `jp_accuracy_log("final_speaker_distribution", ...)`
  call at `japanese_sentence_assembler.py:1370-1420`).

The **actual** translation submission path (`duplicate_protection.py` →
`main_window.py::_on_store_segment_added`/`_on_store_segment_updated` →
`submit_text_for_translation` → `translation_worker.enqueue_stable_segment`) fires
once per canonical ledger commit, completely independently of the unit builder's
state. **Confidence: CONFIRMED** the unit builder does not currently gate, delay,
merge, or replace any real translation request — it computes metrics nobody
consumes for a decision.

**Recommendation: disable/remove the grouping call — small, safe, contained.**
Because nothing downstream branches on its output, this is *not* the "either
remove it, or make it the sole authority" architectural choice REPAIR_PLAN frames
it as — the system has already landed on option 1 (each canonical utterance
translated directly) for the real translation pipeline; the unit builder is inert
vestigial code computing an unused metric. Removing it means deleting one call
(`japanese_sentence_assembler.py:4175-4194`), the `self._translation_unit_builder`
instantiation/reset (2 lines), and the 3 log fields that read from it
(`japanese_sentence_assembler.py:1370-1417`) — no other file references it (see
Item 6). This is a small, contained, single-file change with no behavioral risk to
live translation, only to a log summary field. Keeping it as dead code (matching
the Task 1B/2B/2D precedent) is equally valid if minimal diff is preferred; either
way it is **not** currently causing the "second authority" harm the file's
existence architecturally suggests.

---

## 6. Naming-collision / overlapping-responsibility check

Applying the Task 2D lesson explicitly (a narrow term list previously missed
`stable_revision_decision.py` until traced from a match):

- **Three transcript-storage modules, not two.** `canonical_transcript_ledger.py`
  (Task 1, source of truth), `transcript_store.py` (referenced from
  `duplicate_protection.py`, Task 1, UI-facing display store — not re-read this
  task, not a grep hit here), and **`alpha/utils/transcript_snapshot_store.py`**
  (new to this task's search, "background autosave" store) are three separate
  modules with overlapping "hold the transcript" responsibility. The third one
  has its own independent positional-revision bug (Item 1). **NEEDS_REVIEW** —
  not confirmed whether `transcript_snapshot_store.py`'s output is ever surfaced
  to the user (autosave-only per its docstring) or just another parallel,
  low-consequence duplicate; worth checking before 3B decides whether to fix it.

- **A second, dead translation-event mechanism inside `main_window.py` itself.**
  `publish_translation_event` (`main_window.py:6871`) constructs a `TranslationEvent`
  (`alpha/core/models.py`) and publishes it on an event bus, feeding
  `_on_translation_started`/`_on_translation_received`/`_on_translation_error`
  (`main_window.py:6932-6959`). Grepped `publish_translation_event(` repo-wide:
  **zero callers anywhere.** This is dead code — not a file-naming collision, but
  the same *pattern* Task 2D flagged: a second, differently-named mechanism
  (`TranslationEvent`/event-bus vs. the real `TranslationResult`/
  `_handle_translation_worker_result` callback path) sitting unused alongside the
  live one. `_on_translation_received`'s own docstring even says "display handled
  by `_append_translation_result`" — confirming awareness that this path is
  vestigial. **CONFIRMED dead**, not currently harmful, but exactly the kind of
  parallel-authority shape that caused the Task 2C misdiagnosis when it *was*
  live. Recommend removal alongside Item 5's cleanup, or at minimum flagging so a
  future task doesn't wire something into it believing it's the real path.

- **`StableTranslationJob` / `TranslationJob` / `TranslationResult` / `TranslationEvent`**
  — four similarly-named data types across `translation_worker.py` (`TranslationJob`
  is a bare alias of `StableTranslationJob`, line 129) and `core/models.py`
  (`TranslationEvent`, dead per above). **NEEDS_REVIEW**, low severity — naming
  overlap worth a rename pass, not a functional bug beyond what's already covered.

---

## Files touched in 3B

- `Alpha_Live_Translator/alpha/translation/translation_worker.py` — Item 3: scope
  `_seen_source_hashes` (or the whole duplicate check) by
  `(canonical_utterance_id, source_version)` instead of a bare global text hash.
- `Alpha_Live_Translator/alpha/ui/main_window.py` — Item 1: replace
  `_translation_display_lines.pop()` / `tbox.delete("end-2l linestart", "end")`
  with identity-keyed lookup/removal. Item 2: replace
  `_pending_translation_payload` / `_translation_debounce_after_id` (both single
  instance attributes) with dicts keyed by `(session_id, canonical_utterance_id)`.
  Also thread `canonical_utterance_id`/`source_version` through the two call sites
  at lines 3722 and 4285 that currently call `_on_store_segment_updated` with only
  `(speaker, text)`.
- `Alpha_Live_Translator/alpha/transcription/japanese_sentence_assembler.py` —
  Item 5: remove (or leave as documented dead code) the
  `_translation_unit_builder` wiring at lines 745, 891, 1370-1417, 4175-4194.
- `Alpha_Live_Translator/alpha/utils/transcript_snapshot_store.py` — Item 1/6,
  lower priority: `revise_last_transcript_snapshot`'s positional `_segments[-1]`
  access, pending confirmation of how consequential this store actually is.

Not touched in 3B (read, confirmed clean or out of scope): `deepl_client.py`,
`language_map.py`, `translation/__init__.py`, `acceptance.py`, `canonical_finalize.py`,
`transcript_evidence.py`, `ui_speaker_label.py`, `core/models.py` (except noting the
dead `TranslationEvent` type), `session_runtime.py` (context only), `config.py`,
`constants.py`.
