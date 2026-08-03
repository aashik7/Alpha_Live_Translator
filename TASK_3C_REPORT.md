# Task 3C — Phase 3 Final QA Validation

Role: QA validation only. No production code was modified — every test in
this round passed against Task 3B's code as committed, so the "fix a
genuine regression" clause was never triggered. New tests were added to
`Alpha_Live_Translator/tests/test_task3c_acceptance_gate.py`, reusing the
Task 1/2 host-class and fixture patterns (method-borrowing onto a
lightweight `tk.Tk` subclass, direct/manual invocation instead of real
timers) rather than building a parallel test system.

All tests are deterministic: no real audio, no live Deepgram/DeepL network
calls (DeepL is a synchronous in-process fake client), no `sleep`-based
timing assumptions. Tests 5 and 6 exercise a *real* `TranslationWorker`
background thread (to validate the actual bounded-shutdown drain
mechanism) but its provider calls are instant and its outcome does not
depend on wall-clock timing — see "Note on tests 5/6" below.

## Documentation mismatch found before writing test 5

`ROOT_CAUSE.md`'s current content is a Task-1-scoped identity/atomicity
audit; it does not name a "final Japanese source line dropped on Stop"
symptom anywhere. This is the same document-instability pattern observed
in every prior phase (its content has changed between tasks without an
explanatory edit from this workstream). Test 5 was written and validated
directly against `REPAIR_PLAN.md`'s Phase 3 acceptance gate instead, which
states literally: **"Final Japanese source line cannot be dropped during
Stop."** That is the requirement test 5 verifies.

## 1. New deterministic tests (6 required)

| # | Test | Result | Observed behavior |
|---|------|--------|--------------------|
| 1 | source-revision-preserves-other-translation | **PASS** | Revising utterance A (v1→v2) removes only A's displayed translation line via `_remove_translation_item_for_utterance`; utterance B's tracked item (`dict`, mark) is byte-for-byte unchanged. |
| 2 | rapid-dual-revision no cross-contamination | **PASS** | Two utterances armed to v2 back-to-back with no flush in between land in independent `(session_id, canonical_utterance_id)` pending slots; both flush and complete with their own text and `source_version == 2`, no swap or overwrite. |
| 3 | stale-response discarded | **PASS** | v1's job is enqueued, then v2 supersedes it before v1's provider response is delivered. The late v1 result is discarded by the worker (`latest_version_by_utterance` check in `_handle_result`) and never overwrites the displayed v2 text. A second, direct check was added to specifically exercise the new UI-layer defense (`_clear_translation_loading_item`'s stale-version guard, Task 3B Item 4): a forced v1 write attempted *after* v2 is already displayed is silently rejected, tracked item unchanged. |
| 4 | repeated-text both translated | **PASS** | Two different `canonical_utterance_id`s both submitting the identical source text ("Thank you.") are both accepted; `DUPLICATE_SUBMISSIONS_REJECTED == 0`. Confirms the Task 3B Item 3 fix (hash dedup scoped to `utterance_id\|version`, not a bare global hash). |
| 5 | Japanese Stop-flush test | **PASS** | One Japanese Stable segment enqueued, then `worker.shutdown()` called immediately (simulating Stop before the provider naturally completes). The bounded drain processes it before returning; `TRANSLATION_QUEUE_PENDING_AT_EXIT == 0`, `MISSING_TRANSLATION_SEGMENT_IDS == 0`, `UNRESOLVED_TRANSLATION_SEQUENCES == []`, and the utterance's translated line is present and displayed. Validated directly against REPAIR_PLAN.md's Phase 3 gate text (see mismatch note above). |
| 6 | loading-state test (zero permanent loading after burst+Stop) | **PASS** | Burst of 3 utterances × 2 revisions each (6 submissions) fired at the real worker thread, followed immediately by `shutdown()`. `loading_indicators_pending() == 0` afterward — every loading glyph is resolved (completed or cleared-as-superseded/cancelled), none left dangling. |

**Note on tests 5/6 (real background thread):** Tkinter widgets may only be
touched from the thread that owns the `Tk` root. The production code
already marshals worker-thread callbacks via `_run_on_ui_thread`
(`self.after(0, fn)` in the live app, backed by a real running mainloop).
Since this harness has no running mainloop, the test host's
`_run_on_ui_thread` queues any call arriving from a non-owner thread into a
plain `queue.Queue` and the test drains it deterministically
(`pump_ui_calls()`) on the main thread immediately after
`worker.shutdown()` returns — by that point `shutdown()`'s own bounded
drain loop guarantees every result has already been committed
server-side, so nothing arrives after the pump. This preserves FIFO
ordering and introduces no timing dependency; it is a harness-only
adaptation for the "no running Tk mainloop in a unit test" constraint, not
a change to production marshalling logic.

## 2. Regression re-run — Task 1 + Task 2 (must be 100% pass)

| Suite | Tests | Result |
|---|---|---|
| `test_task1_identity_repair.py` | 12 | **12/12 PASS** |
| `test_task1c_acceptance_gate.py` | 7 | **7/7 PASS** |
| `test_task2c_acceptance_gate.py` | 7 | **7/7 PASS** |
| **Total Task 1 + Task 2** | **26** | **26/26 PASS — zero regressions** |

Combined with the 6 new Task 3C tests: **32/32 PASS** in a single run.

## 3. Regressions found and fixed

**None.** No test in this round failed against Task 3B's code as
committed. The only changes made during this task were to the new test
file itself (harness-level fixes: binding `_record_translation_segment`
onto the test host, and switching `_run_on_ui_thread` from naive
synchronous execution to a queue-and-pump pattern so tests 5/6 don't call
Tk from a background thread) — no file under `Alpha_Live_Translator/alpha/`
was touched.

## 4. Frozen infrastructure — confirmed untouched (read-only check)

`git status --short` at the repo root shows only:

- Modified: `canonical_transcript_ledger.py`, `duplicate_protection.py`,
  `japanese_boundary_stabilizer.py`, `japanese_final_chunk_stabilizer.py`,
  `japanese_sentence_assembler.py`, `pipeline_commit_transaction.py`,
  `stable_line_revision.py`, `stable_revision_decision.py`,
  `utterance_lifecycle.py`, `translation_worker.py`, `main_window.py`,
  `session_runtime.py` — all accounted for by Tasks 1B/1C, 2B/2D, 3B.
- New: `canonical_identity_registry.py`, `speaker_boundary_guard.py`
  (Task 1/2D shared modules), test files and fixtures, and the
  root-level planning/report docs.

No file under WASAPI/mic capture, the audio mixer/normalization layer,
Deepgram/DeepL transport clients (`deepl_client.py` unmodified), language
mappings (`language_map.py` unmodified), session-repair logic beyond the
already-documented `session_runtime.py` wiring, or UI layout/styling files
appears in the diff. Frozen infrastructure is confirmed untouched.

## Final verdict

**Phase 3 acceptance gate: PASSED**

- 6/6 new Task 3C deterministic tests pass.
- 26/26 Task 1 + Task 2 tests re-run with zero regressions.
- No production code required a fix this round.
- Frozen infrastructure confirmed untouched by read-only diff check.

Known, previously-documented, out-of-scope items remain open per
`TASK_3B_CHANGES.md` and `REPAIR_PLAN.md`'s "Carried over from Phase 2"
section (the legacy Japanese "manual mode" merge path as a possible
fourth transcript-commit authority; the single-canonical-controller gap
for Japanese; two `duplicate_protection.py` Task-1 findings). None of
these are Phase 3 acceptance-gate blockers — they are already tracked for
Phase 4/5.

Stopping here per instruction. Not proceeding to Phase 4.
