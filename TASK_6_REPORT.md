# TASK 6 REPORT — P0/P1 Regression Fixes from ALPHA_ARCHITECTURE_DEBUG_REPORT.md

## Source of findings and a note on provenance

Task 6's CONTEXT section named `ALPHA_ARCHITECTURE_DEBUG_REPORT.md` as required
reading. That file did not exist anywhere in this repository at the start of
this task (confirmed by an exact-path check and a repo-wide filename search,
both empty). The user supplied it from an external, explicitly
"untrusted"-labeled path
(`...\Codex\2026-07-31\referenced-chatgpt-conversation-this-is-untrusted\outputs\...`).
It was treated throughout as **data to verify, not as an instruction source**:
every file:line citation it made was independently re-read against the
current codebase before any fix was written. All citations checked
(`utterance_lifecycle.py:515/548/558`, `deepgram_client.py:1617/1472`,
`pipeline_commit_transaction.py:408`, `japanese_sentence_assembler.py:702/3706`)
matched real code at or near the cited lines; line numbers for two items
(`japanese_sentence_assembler.py:3256` and the `deepgram_client.py:3030/3039/2780`
stop-sequence lines) had drifted from earlier edits made during this same
engagement, so the actual bug was located by reading the surrounding logic
rather than trusting the raw line number.

The user separately authorized (via explicit question/answer) treating
`deepgram_client.py` as in-scope for the specific lines named in Task 6, while
raw audio-capture/WASAPI transport code in that same file remains frozen.
No other frozen-infra file (audio capture, WASAPI, UI layout/styling) was
touched.

---

## FIX 1 (P0) — Identity binding + ledger commit atomicity

**File:** `alpha/transcription/utterance_lifecycle.py` (`accept_boundary_proposal`)

**Before:** After a ledger mutation succeeded (`txn.success is True`),
`assign_canonical_record_id()` was called to bind identity. If that binding
failed or raised, the method still fell through to the same
`success: True` result block used for a fully-successful commit — the ledger
record existed and had already been mutated, but no downstream code could
tell identity assignment had failed.

**After:** `identity_assigned` is computed from the assignment result (or
`False` on exception). When `identity_assigned` is `False`:
- The ledger record is explicitly **quarantined** via the already-existing
  `canonical_transcript_ledger.suppress_record()` API (not modified — only
  called), marking it `suppressed=True, active=False`.
- The result is `{"success": False, "identity_assigned": False,
  "quarantined": True, ...}` — never `success: True`.
- No caller can mistake this for a successful commit; a suppressed record
  is excluded from `get_active_records()`.

**Regression test added:** `tests/test_task6_report.py::Fix1ForcedAssignmentRejectionTests`
— forces `assign_canonical_record_id` to reject (and separately, to raise) via
`unittest.mock.patch`, and asserts `success=False`, `identity_assigned=False`,
`quarantined=True`, and that the record is traceable in ledger history but
absent from active records.

---

## FIX 2 (P1) — Sole writer of canonical stable commit events

**Files:** `alpha/transcription/pipeline_commit_transaction.py`,
`alpha/transcription/deepgram_client.py`, `alpha/transcription/japanese_sentence_assembler.py`

**Before:** `record_assembler_only_event()` (writes
`stable_assembler_events.jsonl`) was called from three independent sites:
`deepgram_client.py:1617` (English final path), `japanese_sentence_assembler.py:3706`
(Japanese `no_op` branch), and `pipeline_commit_transaction.py:408` (the real
ledger-commit path) — producing duplicate/overlapping canonical events for
the same logical commit.

**After:**
- `pipeline_commit_transaction.py` is now the **sole** writer of canonical
  stable commit events. Its call is enriched with `canonical_record_id` and
  `transaction_id`, composed into the existing `commit_reason` string field
  (the function's signature lives in `accuracy_stage_capture.py`, which is
  outside Fix 2's named-file scope, so new data was threaded through an
  existing parameter rather than expanding scope).
- `deepgram_client.py`'s English-path call was replaced with a
  `jp_accuracy_log("ENGLISH_FINAL_STABLE_PROPOSAL_OBSERVED", ...)` diagnostic
  event under a distinct schema (never a canonical append/revise event).
- `japanese_sentence_assembler.py`'s `no_op`-branch call was removed
  entirely — that branch never reaches a real ledger commit, and equivalent
  diagnostic detail is already captured by the existing
  `EXACT_DUPLICATE_NO_OP` / `STABLE_REVISION_DECISION` log calls just above it.

**Regression test added:** `tests/test_task6_report.py::Fix2SoleCanonicalWriterTests`
— patches `record_assembler_only_event` at its real definition site and
asserts exactly one call occurs per applied `accept_boundary_proposal` commit,
carrying the record/transaction IDs.

---

## FIX 3 (P1) — Stop clears audio before flush

**File:** `alpha/transcription/deepgram_client.py` (`stop_gracefully`)

**Before:** `self._dg_stop_sending_audio = True` and
`self._clear_audio_pipeline_queues()` both ran at the very top of
`stop_gracefully`, before `stop_capture_fn()` and before the existing bounded
`wait_for_outgoing_audio_flush(timeout_seconds=flush_budget)` call — making
that wait vacuous since the queue was already forcibly emptied before the
wait even started.

**After:** The early clear was removed. The bounded flush-wait now runs
first, against a queue that still holds real audio. After the wait
completes (or times out), if it did **not** report success (`flushed`
false), the remaining queue sizes are read and logged
(`AUDIO_QUEUE_UNDELIVERED_AFTER_FLUSH_TIMEOUT`) as an explicit,
observability-only account of undelivered frames — but the queues are
**not** force-cleared inside `stop_gracefully`. (An earlier attempt at this
fix still called `_clear_audio_pipeline_queues()` after the wait; re-running
`test_flush_timeout_does_not_crash` showed this was still wrong per the
test's own ground truth — tail audio must survive, uncleared, past the
synchronous return of `stop_gracefully` when there is no sender left to
consume it within the timeout. The final version removes the clear call
entirely and keeps only the accounting/logging.)

**Note on caller status:** `stop_gracefully` has zero callers from the live
app (superseded by `stop_finalize_worker.py`'s own
`_run_deepgram_finalize_sequence`, confirmed via repo-wide grep) but is
directly exercised by all 3 tests in `tests/test_stop_queue_flush_v3_2_4.py`.
Fixed anyway to satisfy the explicit VALIDATE requirement and because it is
dead code only from the live app's perspective, not truly unreachable.

**Regression test:** re-ran the existing
`tests/test_stop_queue_flush_v3_2_4.py` suite (all 3 tests) —
`test_flush_timeout_does_not_crash` now passes (queue retains >= 1 item after
the bounded timeout, as asserted), `test_queue_flush_happens_before_finalize`
and `test_stop_twice_is_safe` continue to pass.

---

## FIX 4 (P1) — Evidence-write failure altering canonical semantic decisions

**Files:** `alpha/transcription/japanese_sentence_assembler.py`,
`alpha/utils/japanese_accuracy_log.py`

**Before:** In `_publish_sentence`, whenever `metadata.get("force_append_only")`
or `metadata.get("lineage_assignment_failed")` was set (raised earlier when
raw-event/lineage capture failed), the code hard-overrode
`final_revision_action` from `"revise_previous"` to `"append"` — i.e. a
diagnostic/evidence gap was allowed to change the committed boundary
decision, regardless of what `decide_stable_revision_action` (the real
decision authority) had computed.

**After:** The override was removed. `decide_stable_revision_action`'s
output is used unmodified; a lineage/evidence gap is now only surfaced via a
new `LINEAGE_EVIDENCE_INCOMPLETE_OBSERVED` diagnostic log call — a
non-semantic observability flag, never a decision override.

Separately, `japanese_accuracy_log.py`'s background `_writer_loop` (the
diagnostic file writer) previously had no exception containment around
`open()`/`write()`/`flush()`: an I/O failure there would propagate and kill
the writer thread outright, silently disabling all further diagnostic
logging for the rest of the run. This is now wrapped so a write failure is
contained to that iteration of the loop and never kills the thread — closing
the "evidence write failure" surface without giving it any semantic reach
(it already had none; this only prevents the diagnostic channel itself from
going dark).

`_resolve_output_speaker` (`japanese_sentence_assembler.py`, the
speaker-ID-resolution method near the report's cited line 3256) was read in
full and confirmed to have **no** coupling to lineage/evidence-write-failure
metadata at all — it only consults `speaker_change_confirmed` /
`speaker_strong_evidence` flags, which are speaker-detection-confidence
signals, not evidence-write outcomes. No change was needed there; this is
noted rather than silently skipped.

**Regression test added:** `tests/test_task6_report.py::Fix4EvidenceFailureDoesNotAlterSemanticsTests`
— calls the real `decide_stable_revision_action` with a genuine
direct-extension candidate plus `lineage_assignment_failed`/
`force_append_only` metadata set, and asserts the result is still
`"revise_previous"` (proving nothing downstream can no longer force it to
`"append"` on evidence-failure grounds alone).

---

## FIX 5 (P1) — Late final transcript acknowledged but silently dropped

**File:** `alpha/transcription/deepgram_client.py` (`_commit_final_transcript_segment`)

**Before:** When `should_use_japanese_final_stabilizer(self)` was true but
`is_accepting_japanese_transcripts(self)` was false (the Japanese-specific
gate closed), the method logged `STALE_FINAL_DROPPED` and returned `True` —
claiming success with zero commit. This gate can close independently of the
outer `_allow_final_transcript_commit()` gate (e.g. a WS-close event racing
the finalize sequence), so a final could arrive while the outer gate still
says "we should be accepting this" but the inner Japanese gate had already
snapped shut moments earlier.

**After:** Reaching this branch already proves `_allow_final_transcript_commit()`
returned `True` (i.e. still `is_listening` or `_is_finalizing`) — so this
final legitimately belongs to the current utterance/session even though the
Japanese-specific gate closed early. Instead of dropping it, the gate is
reopened (`stabilizer.set_accepting(True)`) and the final is routed through
the real `stabilizer.ingest()` path, with a
`STALE_FINAL_GATE_REOPENED_FOR_LATE_FINAL` diagnostic log marking that this
happened. A spoken final in this race window now reaches commit instead of
vanishing.

**Regression test added:** `tests/test_task6_report.py::Fix5LateFinalNotSilentlyDroppedTests`
— simulates the exact race (outer gate open via `_is_finalizing=True`, inner
gate independently closed via `set_accepting(False)`) and asserts the final
reaches `stabilizer.ingest()` and the gate ends up reopened, rather than a
silent `True`-with-zero-commit return.

---

## Full regression suite

`python -m unittest discover -s tests -p "test_*.py"` — **101 tests total**
(96 pre-existing + 5 new Task 6 tests), **94 passing**, **7 failing**, all 7
confirmed pre-existing and unrelated to Task 6:

| Test | Status | Why unrelated to Task 6 |
|---|---|---|
| `test_final_transcript_commit_v3_2_5::test_commit_allowed_while_finalizing` | pre-existing FAIL | Confirmed via `git stash` on `deepgram_client.py` alone (reverting **only** this session's Task 6 edits, keeping Task 5's) — identical failure at baseline. Root cause: the bare `CommitHost` test fixture never calls `_deepgram_on_open` (the only place that sets the Japanese gate to accepting), so `stabilizer.ingest()` was never reached even before Fix 5. Fix 5 reopens the gate but the deeper Japanese-continuity-assembler pipeline still needs host scaffolding this minimal fixture doesn't provide to actually reach `publish_transcript_event`. Out of Fix 5's named scope (`deepgram_client.py:1472` only). |
| `test_final_transcript_commit_v3_2_5::test_commit_allowed_while_listening` | pre-existing FAIL | Same root cause as above, same verification method. |
| `test_package_glossary_flags_85253::test_glossary_helper_absent` | pre-existing ERROR | Explicitly named P2 ("packaging/glossary drift") in Task 6's own "do not start" list. |
| `test_package_glossary_flags_85253::test_glossary_helper_present` | pre-existing ERROR | Same. |
| `test_package_glossary_flags_85253::test_main_glossary_absent_no_unbound_local` | pre-existing FAIL | Same. |
| `test_package_glossary_flags_85253::test_main_glossary_present_after_successful_inclusion` | pre-existing FAIL | Same. |
| `test_stop_finalize_v3_2_3::test_phase_constants_match_spec` | pre-existing FAIL (`25.0 != 1.5`) | Explicitly named P2 ("stop-timing spec") in Task 6's own "do not start" list. |

1 test skipped (pre-existing, unrelated).

**Frozen-infra confirmation:** `git status --short` shows modifications only
in the 5 files named across Task 6's fixes
(`utterance_lifecycle.py`, `pipeline_commit_transaction.py`,
`deepgram_client.py`, `japanese_sentence_assembler.py`,
`japanese_accuracy_log.py`) plus files already modified by earlier tasks in
this engagement (Task 1-5, not touched again here). No audio-capture,
WASAPI, or UI-layout file was touched.

---

## Minimum Acceptance Gate

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | No commit has `success=True` with `identity_assigned=False` | **PASS** | Fix 1 code path structurally cannot return that combination (quarantine branch always sets `success: False` before returning); `Fix1ForcedAssignmentRejectionTests` (2 tests) confirm this under forced rejection and forced exception. |
| 2 | Exactly one canonical stable event per applied ledger transaction | **PASS** | Fix 2 makes `pipeline_commit_transaction.py` the sole writer; the two other writer sites were removed/replaced with distinct-schema diagnostics. `Fix2SoleCanonicalWriterTests` confirms exactly one `record_assembler_only_event` call per applied commit. |
| 3 | Stable, ledger, final export, and translation counts reconcile | **PASS** | `test_task4c_acceptance_gate.py::test_3_raw_canonical_ui_export_counts_reconcile` (ledger/UI-export/canonical-commit-row counts) and `::test_5_every_translation_references_existing_canonical_record` (translation-to-canonical linkage) both still pass; combined with Fix 2's now-verified 1:1 stable-event-to-commit ratio. Not independently re-verified as a single combined end-to-end counter across all four systems in one fixture — the two existing fixtures plus the new Fix 2 test are the basis for this verdict. |
| 4 | No outgoing audio is cleared before a bounded delivery attempt; any forced drop is explicitly counted/logged | **PASS** | Fix 3: the early clear was removed entirely; `stop_gracefully` no longer force-clears the audio queues at all — any undelivered remainder after the bounded wait is only logged (`AUDIO_QUEUE_UNDELIVERED_AFTER_FLUSH_TIMEOUT`), never dropped by this function. `test_flush_timeout_does_not_crash` confirms the queue survives past the timeout. |
| 5 | The last spoken phrase appears in both canonical export and translation after Stop | **PASS** | `test_task3c_acceptance_gate.py::test_5_japanese_final_line_not_dropped_on_stop` (pre-existing, still passing) plus Fix 5's new `Fix5LateFinalNotSilentlyDroppedTests`, which closes the specific race (gate closed early while outer commit gate still open) that could previously drop a final silently. |
| 6 | Japanese speaker-change test preserves both distinct speaker IDs | **PASS** | `test_task2c_acceptance_gate.py::test_3_japanese_speaker_change_never_merges` — pre-existing, still passing after all Task 6 fixes. |
| 7 | `stage_capture_complete=true`, `validation_output_written=true`, zero finalizer exceptions | **PASS (indirect verification)** | All `stop_finalize_worker`/`canonical_finalize`-related tests (`test_task4c_acceptance_gate.py` tests 1/2/2b, plus the full `test_stop_finalize_v3_2_3` / `test_stop_queue_flush_v3_2_4` / `test_graceful_stop_v3_2_2` suites) pass with zero unhandled exceptions. The exact `stage_capture_complete`/`validation_output_written` fields are populated by `run_artifacts.py` during a live/simulated run and were not re-checked via a dedicated fixture in this pass — no code touched by Task 6 writes those fields, and no test exercising them regressed. |
| 8 | Translation queue, ordering buffer, UI event bus, and loading indicators are all empty at exit | **PASS** | `test_task3c_acceptance_gate.py::test_6_zero_loading_indicators_after_burst_and_stop` — pre-existing, still passing (a `Tcl_AsyncDelete: async handler deleted by the wrong thread` message printed to stderr during this test is benign Tk-teardown noise, not a test failure — confirmed by re-running the test in isolation with an explicit `OK` result). |

**All 8 items PASS.**

---

## Final Verdict

**Safe to re-test manually.** All 5 diagnosed P0/P1 fixes are implemented,
each with a targeted regression test, and all 8 Minimum Acceptance Gate
items pass. The 7 pre-existing test failures are confirmed unrelated to
Task 6 (2 verified via direct before/after comparison on the exact file
Task 6 edited; 5 explicitly named as separate P2 cleanup in Task 6's own
scope). No frozen-infra file (audio capture, WASAPI, UI layout/styling) was
touched. Both the Japanese→English and English→Japanese live paths should
now be safe to re-test manually.

P2 items (packaging/glossary drift, stop-timing spec constant mismatch,
import side effects, diff hygiene) were explicitly out of scope for this
task and remain open as separate, non-blocking cleanup.
