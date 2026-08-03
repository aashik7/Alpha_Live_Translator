# Task 2C — QA Validation of Phase 2 Repair

Read context used: `TASK_2B_CHANGES.md`, `REPAIR_PLAN.md` Phase 2 acceptance gate.
`ROOT_CAUSE.md` was checked for a real failed-session Japanese two-speaker case to
seed test 3 — it contains only the audit summary (scope, defects, repairs,
invariants, findings), no raw session transcript data. A synthetic two-speaker
Japanese exchange was constructed instead; this is noted rather than silently
substituted.

New test file: `Alpha_Live_Translator/tests/test_task2c_acceptance_gate.py`, 7 tests
(5 required + 1 bonus control case + inherent split of test 3's scenario). All
synthetic/deterministic — no audio, no live Deepgram/DeepL calls. The timeout test
uses `JapaneseContinuityAssembler.try_execute_continuity_hold(...)`, the assembler's
own synchronous fallback-timeout entry point, instead of a real timer or `sleep`.

## Pass/fail table — 5 required tests

| # | Requirement | Test | Result |
|---|---|---|---|
| 1 | Progressive revision "My"→"My name"→"My name is Tariqul" commits as exactly one record | `test_1_progressive_revision_single_record` | **PASS** |
| 2 | "Sentence one." / "Sentence two." with normal gap commit as two separate records | `test_2_sentence_boundary_stays_two_records` | **PASS** |
| 3 | Japanese: two different speakers' fragments back-to-back never merge into one canonical line | `test_3_japanese_speaker_change_never_merges` | **FAIL** (genuine bug found, not a Task 2B regression — see below) |
| 4 | Synthetic assembler output never re-enters as raw provider input | `test_4_synthetic_output_never_reenters_raw_ingress` + `test_4b_non_synthetic_input_still_reaches_assembler` (control) | **PASS** |
| 5 | Forced fallback timeout does not merge a new speaker's speech into the timed-out record | `test_5_timeout_does_not_join_new_speaker` | **FAIL** (same root cause as #3) |

Test run: `python -m unittest tests.test_task2c_acceptance_gate` — 6 tests, 4 passed,
2 failed (both Japanese speaker-boundary cases).

## Task 1 regression check

Re-ran `tests.test_task1_identity_repair` + `tests.test_task1c_acceptance_gate` (19
tests): **all 19 PASS, `OK`.** No regression from Task 2B.

## Blocking finding: tests 3 and 5 (not a Task 2B regression — pre-existing, newly exposed)

**Observed:** feeding speaker 1's `"こんにちは、今日は天気がいいですね。"` then speaker
2's `"はい、本当にそうですね。"` through the real production entry point
(`JapaneseFinalChunkStabilizer.ingest()` → `JapaneseContinuityAssembler`) produces
**one** committed canonical record:
```
final_text: "こんにちは、今日は天気がいいですね。はい、本当にそうですね。"
applied_action: "revise"
revision_reason: "candidate_directly_extends_previous"
```
Speaker 2's sentence was merged into speaker 1's already-committed record. Test 5
(forced timeout) reproduces the identical merge.

**Root cause, traced and confirmed by reading code (not inferred):** Task 2B's fix
to `japanese_sentence_assembler.py::_ingest_locked` correctly prevents the *buffer*
from merging two speakers before a commit — that part works (each speaker's fragment
does get committed as its own `_route_stable_publish` call). The merge happens
**after** that, in a separate, previously-undiscovered layer:

- `alpha/transcription/stable_line_revision.py::StableLineRevisionManager.apply_boundary_output`
  (lines 271-332) decides `revise` vs `append` for the canonical line entirely from
  `stab` (a boundary-stabilizer result dict) and never compares the incoming
  `speaker` parameter against the active line's stored speaker before calling
  `revise_active_line()`. `speaker` is only used for `create_line()` (the append
  path); the revise path (line 315-321) has no speaker check at all.
- `alpha/transcription/japanese_boundary_stabilizer.py` (the module producing the
  `stab` dict / `revision_reason: "candidate_directly_extends_previous"`) uses
  `speaker` / `speaker_prefix` **only** for display-text formatting (`_with_speaker`,
  prepending a `"[Speaker N]"` label) — never as an input to the
  hold/append/revise decision itself (confirmed by grep: every `speaker` occurrence
  in that file is prefix-formatting, none is a comparison/condition).

So a third, independent authority — the boundary-stabilizer → stable-line-revision
pipeline, downstream of the commit `_ingest_locked` governs — can still splice two
different speakers' committed text into one canonical line based purely on text
adjacency, with no speaker gate anywhere in that path.

**Why this was not caught in Task 2A or fixed in Task 2B:** Task 2A's audit was
constrained to six exact grep strings and never surfaced `stable_line_revision.py`
or the decision-relevant parts of `japanese_boundary_stabilizer.py` (that file was
read for the reset-hook reference only, not its `speaker` handling). Task 2B's fix
was correctly scoped to the bug Task 2A actually found (`_ingest_locked`'s buffer
merge); this deeper, separate merge point was invisible until an end-to-end test
exercised the real production entry point with two different speakers, which is
exactly what test 3/5 do.

**Confirmed not a Task 2B regression:** `git status` shows Task 2B modified exactly
three files — `japanese_sentence_assembler.py`, `japanese_final_chunk_stabilizer.py`,
`utterance_lifecycle.py`. Neither `stable_line_revision.py` nor
`japanese_boundary_stabilizer.py` (where this bug lives) was touched by Task 2B, so
per this task's constraint ("only a genuine regression from Task 2B" justifies a
production fix), **no fix was applied**. This is reported as a failing test and a
new, more specific finding for a follow-up task, not silently patched.

## Frozen infrastructure — zero diffs confirmed

`git status --short` (repository-wide): changes are limited to
`alpha/transcription/{canonical_transcript_ledger,duplicate_protection,
japanese_final_chunk_stabilizer,japanese_sentence_assembler,
pipeline_commit_transaction,utterance_lifecycle}.py`, `alpha/utils/session_runtime.py`
(pre-existing, not from this task), `canonical_identity_registry.py` (pre-existing),
new test/fixture files, and root-level markdown docs. Confirmed absent from the
changed-file list:

- WASAPI / microphone capture
- Audio mixer / normalization
- Deepgram/DeepL transport
- Language mappings
- Session-repair logic (`session_runtime.py`'s modification predates this task)
- UI layout files

## Final verdict

**Phase 2 acceptance gate: FAILED**

Blocking item:
1. **Japanese two-speaker dialogue can still merge into one canonical line**
   (tests 3 and 5), via `stable_line_revision.py` / `japanese_boundary_stabilizer.py`
   — a speaker-blind revision path independent of the buffer-level fix Task 2B
   applied. This directly violates REPAIR_PLAN.md's Phase 2 acceptance gate:
   "Japanese dialogue between two speakers must never become one merged canonical
   line." Not fixed here — out of this task's "regression from Task 2B only" scope;
   needs a dedicated follow-up (add a speaker-identity check to
   `StableLineRevisionManager.apply_boundary_output` / the boundary-stabilizer
   decision, likely alongside the still-deferred Item 1 single-controller rewrite
   from `TASK_2B_CHANGES.md`).

Non-blocking, confirmed passing:
- Progressive-revision and sentence-boundary requirements (English) — PASS.
- Synthetic re-entry guard — PASS, including a control case proving it isn't a
  blanket block.
- Timeout-safety requirement is satisfied *at the buffer level* (Task 2B's fix);
  the failure in test 5 is entirely attributable to the same downstream
  stable-line-revision defect as test 3, not a new timeout-specific bug.
- All 19 of Task 1's tests still pass — no regression.
- Frozen infrastructure — zero diffs.
