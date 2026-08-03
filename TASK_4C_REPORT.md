# Task 4C — Final QA Validation of the Phase 4 Finalization/Evidence Repair

Two genuine regressions/gaps were found by the new tests and fixed this
task (minimal diffs, both documented below and re-tested) — everything
else passed against `TASK_4B_CHANGES.md`'s code as committed.

**Note on `ROOT_CAUSE.md` "P0-1 through P1-3"**: this instruction's section
labels do not exist in the current `ROOT_CAUSE.md` (same document-instability
pattern flagged in every prior audit this engagement — it currently
contains a Task-1-scoped "Original Defects" list numbered 1-5, no P0/P1
labeling anywhere). The project-status paragraph below is written against
`REPAIR_PLAN.md`'s phase structure and this engagement's actual task
history instead, which is verifiable against real files.

## Regressions found and fixed this task

1. **Empty-Stable-reconstruction gap (test 1)** — `canonical_finalize.py::finalize_canonical_pipeline`
   returned `ok=True` for a session with zero committed canonical records,
   as long as the freeze/export machinery itself didn't raise — directly
   violating REPAIR_PLAN.md's literal Phase 4 acceptance-gate line ("Empty
   Stable reconstruction cannot be marked completed"). **Fix**: added an
   explicit check immediately after `freeze_snapshot()` —
   `active_record_count == 0` now returns `{"ok": False, "error": "empty_stable_reconstruction"}`
   before any further work, unconditionally (no "was content expected"
   heuristic — the acceptance-gate line has no such qualifier). Re-tested:
   `test_1_empty_reconstruction_never_marked_completed` now passes.
2. **`canonical_utterance_id` never populated in evidence streams (test 5)** —
   `write_separated_evidence_streams` read `rec.get("canonical_utterance_id")`
   directly on each canonical record, but a real committed record (verified
   by dumping one from `ctl.get_active_records()`) carries that field
   inside `rec["metadata"]`, not at the top level — every row in
   `canonical_commits.jsonl`/`utterance_decisions.jsonl` silently had
   `canonical_utterance_id: null`. **Fix**: added a `_rec_meta()` helper and
   read `session_id`/`channel_index`/`canonical_utterance_id`/`source_version`/
   `canonical_decision` from the correct location. Also found and fixed a
   related gap in `stop_finalize_worker.py::_write_translation_and_ui_evidence_streams`:
   it only wrote an aggregate worker-shutdown summary, never a per-job
   `canonical_utterance_id` reference — fixed by reading
   `TranslationWorker._revision_events` (an existing internal list, already
   populated with `canonical_utterance_id`/`source_version`/`source_record_id`
   on every accepted job; read-only access, no change to `translation_worker.py`
   itself, so no Task 3 file was touched). Re-tested:
   `test_5_every_translation_references_existing_canonical_record` now passes.

## 1–6. New test results

| # | Test | Result | Observed behavior |
|---|------|--------|--------------------|
| 1 | Empty-Stable-reconstruction | **PASS** (after fix) | Zero committed records → `finalize_canonical_pipeline` returns `ok=False`; feeding that through the required-step marking confirms `final_status` never becomes `"completed"` or `"completed_pending_evidence_package"`. |
| 2 | Required-exception sweep (all 9 required steps, one at a time) | **PASS** | For every one of `audio_summary`, `raw_event_persistence`, `utterance_reconstruction`, `canonical_ledger_validation`, `stable_export`, `final_export`, `translation_drain`, `loading_state_drain`, `run_manifest`: marking that single step failed (others succeeding) yields `final_status == "failed"`, `stop_finalize_failed == True`, and `failure_reason` equal to exactly that step's name — no cross-contamination between steps. |
| 2b | Real exception propagation (bonus, not one of the 6 but directly validates the mechanism) | **PASS** | Monkeypatched `canonical_transcript_ledger.freeze_snapshot` to raise `RuntimeError`; `finalize_canonical_pipeline` correctly caught it and returned `ok=False` with the real exception message in `result["error"]` — confirms the exception-to-failure path works end to end, not just in the synthetic sweep. |
| 3 | Reconciliation (raw/canonical/UI/export counts) | **PASS** | Committed 3 records through the real production path (`host._display_transcript_item` → `execute_pipeline_commit`); after `finalize_canonical_pipeline`, `active_record_count == 3`, `TranscriptStore.segment_count() == 3`, `canonical_commits.jsonl` has exactly 3 rows, and `validate_internal_consistency()["ok"] is True` — all four counts agree. |
| 4 | Lineage validity | **PASS** | Two records committed with explicit `source_raw_event_ids`; every row in `canonical_commits.jsonl` carries a non-empty `source_raw_event_ids` (or is marked `synthetic_record`), and `validate_internal_consistency()["stable_records_without_lineage"] == 0`. |
| 5 | Translation references existing canonical record | **PASS** (after fix) | A real `TranslationWorker.enqueue_stable_segment` call for `canonical_utterance_id="U-1"` (already committed to the ledger) produces a `translation_jobs.jsonl` row referencing `"U-1"`; confirmed that ID is a subset of the IDs actually present in `canonical_commits.jsonl` — no reference to a nonexistent canonical record. |
| 6 | Evidence separation / no synthetic leakage | **PASS** | A raw-events source file containing one genuine event and one `metadata.synthetic_record=True` event, run through `write_separated_evidence_streams`, produces a `provider_events.jsonl` containing only the genuine event — the synthetic one is excluded by the redundant filter added in Task 4B (defense in depth on top of the already-existing Task 2A/2B upstream guard). |

**Result: 6/6 required tests PASS** (7/7 counting the bonus real-exception
test). Confirmed deterministic via 3 repeated standalone runs of the new
suite (no flakiness).

## Full regression suite

Per this task's scope ("all tests from Tasks 1, 2, 2D, 2E-2G, 3"):

| Suite | Tests | Result |
|---|---|---|
| `test_task1_identity_repair.py` (Task 1) | 12 | **12/12 PASS** |
| `test_task1c_acceptance_gate.py` (Task 1) | 7 | **7/7 PASS** |
| `test_task2c_acceptance_gate.py` (Task 2A–2D) | 7 | **7/7 PASS** |
| `test_task2g_acceptance_gate.py` (Task 2E–2G) | 9 | **9/9 PASS** |
| `test_task3c_acceptance_gate.py` (Task 3) | 6 | **6/6 PASS** |
| `test_task4c_acceptance_gate.py` (Task 4, this task) | 7 | **7/7 PASS** |
| **Total** | **48** | **48/48 PASS — zero regressions** |

Confirmed via one combined run of all six files together, and 3 standalone
repeat runs of the new suite for flakiness.

**Bonus check (not part of the requested count, but directly relevant
since `stop_finalize_worker.py`/`run_artifacts.py` were heavily modified):**
also ran the older, pre-repair-engagement version-pinned test suite
(`test_stop_finalize_v3_2_3.py`, `test_stop_queue_flush_v3_2_4.py`,
`test_final_transcript_commit_v3_2_5.py`, `test_package_glossary_flags_85253.py`,
and 5 others). Result: 33/41 pass, 6 failures + 2 errors. **Traced every
failure's imports and confirmed none touch any file this task or Task 4B
modified** — they import exclusively from `alpha.transcription.deepgram_client`
(frozen, e.g. `test_phase_constants_match_spec` expects
`GRACEFUL_DRAIN_MAX_S == 1.5`, but the current, long-standing frozen value
is `25.0` — a stale v3.2.3-era spec constant, not something from this
engagement) or from a root-level packaging script
(`package_latest_troubleshooting_run.py`, itself outside the live app,
per Task 4A's scope findings). One additional file
(`test_transcript_stability_v3_2.py`) raises `SkipTest` at import time by
its own design (docstring: "Obsolete... Replaced by..."). **Not fixed** —
pre-existing, out of this task's scope, and touching them would mean
either modifying frozen `deepgram_client.py` or updating stale test
expectations, neither of which this task's regression-count instruction
asked for. Excluded from the "48/48" total above, which is the exact
count the task requested.

## Frozen infrastructure — confirmed untouched (read-only check)

`git status --short` at the repo root shows only:

- **Modified**: the twelve files already accounted for by Tasks 1B/1C,
  2B/2D, 3B (`canonical_transcript_ledger.py`, `duplicate_protection.py`,
  `japanese_boundary_stabilizer.py`, `japanese_final_chunk_stabilizer.py`,
  `japanese_sentence_assembler.py`, `pipeline_commit_transaction.py`,
  `stable_line_revision.py`, `stable_revision_decision.py`,
  `utterance_lifecycle.py`, `translation_worker.py`, `main_window.py`,
  `session_runtime.py`, `transcript_store.py`) **plus** this task's four
  Task 4B/4C files: `canonical_finalize.py`, `evidence_pointer_finalize.py`,
  `run_artifacts.py`, `stop_finalize_worker.py`.
- **New**: existing shared modules (`canonical_identity_registry.py`,
  `speaker_boundary_guard.py`), all test files including this task's
  `test_task4c_acceptance_gate.py`, and root-level planning/report docs.

No file under WASAPI/mic capture, the audio mixer/normalization layer,
Deepgram/DeepL transport clients, or language mappings appears anywhere in
the diff. `deepgram_client.py` and `deepl_client.py` are both absent from
the change list. Frozen infrastructure is confirmed untouched.

## Final verdict

**Phase 4 acceptance gate: PASSED**

- All 6 required tests pass (7/7 including the bonus real-exception test).
- 48/48 total regression tests pass across Tasks 1, 2 (2A-2D), 2 (2E-2G), 3,
  and 4 — zero regressions.
- Two genuine gaps found by this QA pass were fixed with minimal, documented
  diffs and re-verified (empty-reconstruction blocking, translation
  evidence referencing real canonical IDs).
- Frozen infrastructure confirmed untouched.

## Overall project status

Per `REPAIR_PLAN.md`'s phase structure (used instead of `ROOT_CAUSE.md`'s
P0/P1 labels, which don't exist in its current content — see the note at
the top):

**Phase 1 (identity/atomicity) — CLOSED.** Task 1B fixed the four confirmed
bugs (unsafe canonical-record fallback, inconsistent append lineage,
permissive channel matching, missing identity-mismatch diagnostics); Task
1C validated with 6 additional deterministic tests. 19/19 Task 1 tests
still pass in every regression run through this task, including this one.

**Phase 2 (transcript ownership) — CLOSED for every confirmed bug, with two
items still explicitly open and tracked, not silently dropped.** Task 2B/2D
closed the Japanese cross-speaker merge bug (Task 2C found it, Task 2D
traced the real root cause to `stable_revision_decision.py` and fixed it,
26/26 tests passing at the time). Task 2E/2F/2G additionally found and
closed a second, independent bug in this same phase — the manual-mode
Japanese path in `main_window.py` bypassing canonical identity/channel/speaker
checks via `TranscriptStore`'s positional lookup — verdict "4th-authority
path closed: CONFIRMED." **Still open** (deliberately deferred, tracked in
`REPAIR_PLAN.md`'s "Carried over from Phase 2" section): the single
canonical controller is not yet real for Japanese (HOLD/EXTEND/COMMIT
proposal architecture, judged too large for a surgical fix across every
task that touched this area); and `duplicate_protection.py`'s
`get_last_segment(speaker_num)` speaker-only key plus its `already_committed`
trust-gate were never revisited after Task 1 first flagged them.

**Phase 3 (translation ownership) — CLOSED, with one documented partial
gap.** Task 3B fixed positional UI translation updates, the global pending
payload, dedup scoping, and disabled the unused Japanese translation-unit
grouping; Task 3C validated with 6/6 deterministic tests including the
Japanese Stop-flush requirement. **Documented, not closed**: two legacy
"manual mode" call sites in `main_window.py` (`_commit_japanese_update_previous_segment`'s
Stop-tail sibling) have no `canonical_utterance_id` available at all in
their call chain — Task 3B's fix fail-closes (skips) rather than corrupts,
but cannot fully close this gap without the Phase 2 single-controller work
above.

**Phase 4 (finalization/evidence integrity) — CLOSED this task.** Task 4A
found that `final_status` could never actually become `"failed"` anywhere
in the live code path regardless of how many required steps failed, that
the five required evidence streams didn't exist under their required
names, and that a fully-built evidence-package implementation sat dead and
uncalled alongside ~240 lines of unreachable code. Task 4B built explicit,
fail-closed tracking for all nine synchronous required steps plus the
async-resolved tenth (evidence package), removed the dead code, and
materialized all five evidence streams. This task (4C) found and closed
two remaining gaps the 4B implementation had introduced (empty
reconstruction not blocking completion; translation evidence not actually
referencing canonical IDs), re-validated everything, and confirms the
phase's acceptance gate now passes in full.

Per instruction, this report does not propose or start Phase 5. Stopping
here after producing `TASK_4C_REPORT.md`.
