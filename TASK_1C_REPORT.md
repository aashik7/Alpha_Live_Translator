# Task 1C — QA Validation of Phase 1 Repair

Read context used: `TASK_1B_CHANGES.md`, `REPAIR_PLAN.md` Phase 1 acceptance gate.
`ROOT_CAUSE.md` P0-1/P0-4/P0-6/P1-1 could not be read as instructed — as flagged in
Task 1A and 1B, that file still contains unrelated Japanese-study textbook content,
not a root-cause audit. `REPAIR_PLAN.md`'s Phase 1 fixes 1–4 and acceptance gate were
used as the reference instead.

## Test approach

`Alpha_Live_Translator/tests/test_task1_identity_repair.py` already existed (untracked,
pre-dating this task) and covers most of the 5 required scenarios at the ledger/registry
layer via `_display_transcript_item`. To close specific gaps — direct coverage of the
`utterance_lifecycle.py` `_last_committed` gate touched in Task 1B, cross-utterance
isolation during reordering, an owner-level exact-duplicate case, and the full
`duplicate_protection.py` caller chain for evidence-write failure — a new file,
`Alpha_Live_Translator/tests/test_task1c_acceptance_gate.py`, was added with 7 tests
(one per required item, plus a split duplicate case and one bonus test for the interim
fix below). All tests are synthetic/deterministic: no audio, no live Deepgram/DeepL
calls, no sleeps or wall-clock timing dependencies.

## Pass/fail table

| # | Requirement | New test(s) | Existing coverage | Result |
|---|---|---|---|---|
| 1 | Wrong-utterance revision rejected, not remapped to `_last_committed` | `test_1_wrong_utterance_revision_rejected_not_remapped_to_last_committed` (drives `owner.on_final_chunk` directly, exercises `_is_correction_of_committed_locked`) | `test_wrong_utterance_revision_is_rejected` (ledger/registry layer) | **PASS** |
| 2 | Out-of-order/reordered revision resolves correctly or is rejected; never corrupts another utterance | `test_2_out_of_order_revision_never_corrupts_another_utterance` (two interleaved utterances, explicit cross-utterance isolation assertion) | `test_out_of_order_version_is_rejected_as_stale` (single-utterance stale replay) | **PASS** |
| 3 | Cross-channel UtteranceEnd does not commit the active utterance | `test_3_cross_channel_utterance_end_does_not_commit_active_utterance` | `test_cross_channel_utterance_end_is_ignored` | **PASS** |
| 4 | Exact-duplicate final → `IGNORE_DUPLICATE`, zero new ledger commits | `test_4a_exact_duplicate_held_chunk_ignored_by_lifecycle` (owner-level, non-terminal chunk retransmit) + `test_4b_exact_duplicate_committed_final_replay_causes_zero_new_commits` (full identity-key replay) | `test_exact_duplicate_final_causes_zero_mutations` | **PASS** |
| 5 | One decision → at most one ledger mutation; evidence-write failure after successful apply causes no fallback append | `test_5_evidence_write_failure_causes_no_fallback_append` (drives the *full* `duplicate_protection.py` → `execute_pipeline_commit` → ledger chain, not just `execute_pipeline_commit` directly) | `test_canonical_commit_applied_when_evidence_write_fails`, `test_canonical_commit_applied_when_metrics_write_fails` | **PASS** |

Bonus (not one of the 5, added because it was explicitly requested in this turn):
`test_6_incompatible_interim_is_ignored_without_corrupting_held_utterance` — **PASS**.

**Test run (`tests.test_task1_identity_repair` + `tests.test_task1c_acceptance_gate`,
19 tests total): `OK`.**

## Regression check against Task 1B

Ran the entire `Alpha_Live_Translator/tests/` suite (61 tests, `unittest discover`) to
check for regressions from the Task 1B production-code edits. Result: **6 failures / 2
errors, all pre-existing and unrelated to Task 1B** — none touch
`utterance_lifecycle.py`, `canonical_transcript_ledger.py`,
`pipeline_commit_transaction.py`, `duplicate_protection.py`, or `revision_metadata.py`:

- `test_package_glossary_flags_85253.py` (2 errors, 2 failures) — `AttributeError:
  module 'package_latest_troubleshooting_run' has no attribute
  '_glossary_included_in_package'` and related `rc != 0` failures. A packaging script
  issue; the test file imports nothing from `alpha.transcription`.
- `test_stop_finalize_v3_2_3.py::test_phase_constants_match_spec` — `GRACEFUL_DRAIN_MAX_S`
  expected `1.5`, got `25.0`. A constants-drift issue in `alpha/constants.py`, which
  this task did not touch.
- `test_stop_queue_flush_v3_2_4.py::test_flush_timeout_does_not_crash` — audio queue
  size assertion (`0 not >= 1`). Audio-queue/capture territory — explicitly "Keep
  frozen" per `REPAIR_PLAN.md`, not touched.
- `test_final_transcript_commit_v3_2_5.py::test_commit_allowed_while_listening` and
  `::test_commit_allowed_while_finalizing` — **the two most plausibly related to Task
  1B**, since they route through `utterance_lifecycle.py`. Verified directly: stashed
  the 5 Task-1B-touched files back to the committed HEAD baseline and re-ran this test
  file in isolation — **both failures reproduce identically against HEAD**, before any
  Task 1B or prior uncommitted repair work. Confirmed pre-existing, not a Task 1B
  regression. Working-tree changes were restored immediately after (`git stash pop`),
  verified present again before continuing.

**No production-code changes were made in response to the broader suite** — per this
task's constraint, only a genuine regression *from Task 1B* would justify a fix, and
none was found.

## Additional fix applied (explicitly requested this turn)

`utterance_lifecycle.py`, `_ingest()` Case A (interim-only events): previously called
`_apply_active_update_locked()` directly with no speaker/channel/timing compatibility
check at all — the one path in this file that bypassed the channel-safety fix from
Task 1B. An interim event on a different channel/speaker than the currently held
(uncommitted) utterance would silently merge into it, corrupting its text and
overwriting its channel.

Original plan (as described in this conversation before implementing) was to pass
`force_new=not same_active` — mirroring Case C's pattern for final chunks. During
implementation this was found to be unsafe: it would silently *discard* the held
utterance (replace `self._active` with a new one, with no commit), losing its content
outright, since this owner holds only one active utterance at a time and interim
events don't get the "commit-previous-then-start-new" flush that Case C performs.

**Fix actually applied:** an incompatible interim is now ignored (`IGNORE_DUPLICATE`,
reason `interim_incompatible_with_active_utterance`) rather than merged or allowed to
replace the held utterance. The held utterance is left completely untouched and can
still commit normally via its own final chunk / timeout / UtteranceEnd. Covered by
`test_6_incompatible_interim_is_ignored_without_corrupting_held_utterance` (PASS).

## Frozen infrastructure — zero diffs confirmed

`git status --short` (repository-wide) shows the complete set of changes; nothing
outside `alpha/transcription/{canonical_transcript_ledger,duplicate_protection,
pipeline_commit_transaction,utterance_lifecycle}.py`, `alpha/utils/session_runtime.py`
(pre-existing, not touched by this task), the new `canonical_identity_registry.py`
(pre-existing, not touched by this task), new test/fixture files, and root-level
markdown docs appears anywhere in the diff. Specifically confirmed absent from the
changed-file list, i.e. zero diffs:

- WASAPI / microphone capture (`alpha/audio/wasapi.py`)
- Audio mixer / normalization (`alpha/audio/timeline_mixer.py`)
- Deepgram/DeepL transport
- Language mappings
- Session-repair logic (only `session_runtime.py` shows as modified, and that
  modification predates this task — not made by Task 1B or 1C)
- UI layout files

## Final verdict

**Phase 1 acceptance gate: PASSED WITH EXCEPTIONS**

Exceptions (none blocking, none caused by Task 1B or this task):
1. `ROOT_CAUSE.md` still does not contain the root-cause audit document referenced by
   this and prior tasks' instructions — unresolved since Task 1A, not in scope to fix
   here.
2. 6 pre-existing failures / 2 pre-existing errors in the broader `tests/` suite
   (packaging script, a stop-phase constants mismatch, an audio-queue-flush test, and
   two `test_final_transcript_commit_v3_2_5.py` cases), all confirmed unrelated to
   Task 1B (verified against committed HEAD for the two most plausibly-related cases).
   These sit outside Phase 1's transcription-identity scope and were left as found,
   per this task's instruction to fix only regressions caused by Task 1B.

## Re-verification addendum

User explicitly asked to re-check two items post-report:
1. Possible unconditional `or True` in `_is_correction_of_committed_locked` -- not present; line 905 returns `_text_related(prev.text, lexical)` alone.
2. Possible missing distinct outcome flags in `execute_pipeline_commit` -- not missing; `success=ledger_applied` at line 473, with `evidence_write_failed`/`metrics_write_failed` tracked as separate fields, unaffected by `success`.

Both findings from TASK_1B_CHANGES.md/TASK_1C_REPORT.md confirmed accurate on re-inspection. No code changes. 19/19 deterministic tests re-run: OK.
