# Task 5 — Final Cleanup: Single Controller, Identity Verification, Manual-Mode Routing, Dead Code

Single pass. All four fixes implemented and validated. **Fix 1 turned out to
be a precondition for Fix 2 and Fix 3, not independent** — this was
discovered empirically via test failures during implementation, not assumed
up front; see "How the fixes interact" below.

## Fix 1 — Japanese single canonical controller

**Files changed**: `alpha/transcription/utterance_lifecycle.py`,
`alpha/transcription/japanese_sentence_assembler.py`.

**What was actually true before this fix** (read directly, not assumed):
`utterance_lifecycle.py` never called `execute_pipeline_commit` itself —
only `japanese_sentence_assembler.py:3782` (identified via grep, single
call site) called it directly, and only `duplicate_protection.py`/
`utterance_lifecycle.py` ever called `canonical_identity_registry.observe_identity`/
`assign_canonical_record_id` — the Japanese assembler never did. So
Japanese utterances were committed to the canonical ledger but never
registered in the identity registry at all.

**Change**: added `UtteranceLifecycleOwner.accept_boundary_proposal()` — a
new public method that performs identity observation
(`observe_identity`), duplicate detection, exact revision-target resolution
(reusing the existing `_resolve_correction_target_locked`), the actual
`execute_pipeline_commit` call, and `assign_canonical_record_id` on
success. This mirrors exactly what `duplicate_protection.py`'s fallback
path already does for English — one implementation, not two.
`japanese_sentence_assembler.py::_publish_sentence` now calls this method
with its own already-decided action ("commit_new" or "revise_previous",
derived from the existing `final_revision_action`/`update_previous_requested`
values `decide_stable_revision_action` computes) instead of calling
`execute_pipeline_commit` directly. The Japanese boundary/timing DECISION
logic itself (`decide_stable_revision_action`, the particle/tail-stitch/
compound heuristics) is **unchanged** — REPAIR_PLAN.md explicitly allows
different boundary strategies per language; only who performs identity
registration + the ledger commit changed.

**New state added**: `self._current_canonical_utterance_id` /
`self._current_source_version` on `JapaneseContinuityAssembler` — Japanese
utterances never had a persistent identity distinct from their record id
before this fix. Minted fresh (`uuid4`) on every "append"-shaped commit,
reused with an incremented version on every "revise_previous"-shaped
commit, reset in `reset()`.

**One deliberately unchanged case**: the stop-tail suppression path
(`suppress_early=True`, when an incomplete trailing fragment is discarded
rather than published) still calls `execute_pipeline_commit` directly,
unchanged. Nothing is ever published for this case (verified: it returns
before reaching any downstream publish/UI code), so there is no committed
identity to register — routing a no-op through the controller would add
risk for zero benefit.

## Fix 2 — `duplicate_protection.py` identity verification

**File changed**: `alpha/transcription/duplicate_protection.py`.

1. **`get_last_segment`'s speaker-only key**: `TranscriptStore` itself has
   no channel/`canonical_utterance_id` concept (confirmed again this task —
   fixing that fully would mean modifying `transcript_store.py`, not named
   in this task's file list). Implemented the strongest fix available
   within scope: `previous_text` is now only borrowed from the store when
   either (a) the item carries no `canonical_utterance_id` (nothing to
   verify against), or (b) the identity registry confirms this
   `canonical_utterance_id` has been observed before at this exact
   `(session_id, channel_index)` — a first-time utterance never gets a
   stale positional match. When a lookup is allowed, it now uses
   `get_last_segment_if_active` (Task 2F's hard-speaker-boundary method)
   instead of the plain positional one.
2. **`already_committed` trust-gate**: no longer trusts
   `canonical_record_id`/`_jp_continuity_assembler`/`canonical_ledger_committed`
   outright. When any of those flags is set, the claim is now verified
   against `canonical_identity_registry.resolve_canonical_record_id` —
   accepted only if the registry has a matching entry (and, if a specific
   `canonical_record_id` was claimed, that it matches exactly). No
   `canonical_utterance_id` at all on the item → unverifiable → fails
   closed (`already_committed` stays `False`), exactly the rule Task 1
   applied to canonical record targeting ("no operation may select a
   target because it is merely the latest active record").

**This is only safe because of Fix 1**: before Fix 1, the registry was
never populated for Japanese, so this verification would have rejected
every genuine Japanese commit and double-committed everything. Confirmed
this ordering dependency directly — implementing Fix 2 before Fix 1 landed
would have broken Japanese transcription outright (caught by the 48-test
regression run, not assumed).

## Fix 3 — `main_window.py` legacy manual-mode routing

**File changed**: `alpha/ui/main_window.py`.

Both call sites `TASK_3B_CHANGES.md` originally flagged as fail-closed-skip
(no `canonical_utterance_id` available at all in scope) now assign one:

1. **`_commit_transcript_item_to_store`** (the manual-mode merge/commit
   decision point): a new `self._jp_manual_mode_current_utterance_id` /
   `self._jp_manual_mode_current_source_version` pair, minted fresh
   (`jpm-utt-<uuid>`) whenever a cross-segment merge does **not** apply
   (a genuinely new committed segment — new speaker turn or non-matching
   text) and reused with an incremented version whenever the merge **does**
   apply (a continuation of the same utterance) — the identical
   mint-vs-reuse pattern used in Fix 1, applied to this separate legacy
   path since it has its own separate state (it is not routed through the
   canonical Japanese assembler at all — see `TASK_2E_FINDINGS.md`'s
   original finding that this is a structurally independent path).
2. **`_commit_japanese_update_previous_segment`**: now threads the
   caller-assigned `canonical_utterance_id`/`source_version` into
   `_on_store_segment_updated` instead of calling it with only
   `(speaker, text)` — closing the translation-display identity gap
   `TASK_3B_CHANGES.md` left open.
3. **`_recover_interim_tail_on_stop`**'s `"append_missing_suffix"` handler
   (the second of `TASK_3B_CHANGES.md`'s two flagged call sites): now
   mints/reuses a `canonical_utterance_id` the same way, but **only** when
   `_is_japanese_manual_mode()` is true — this function is shared with
   English (confirmed again this task, unchanged from Task 2F's finding),
   so English's interim-recovery path is untouched, avoiding the
   cross-language scope Task 2F deliberately declined.

**Also only safe because of Fix 1's registry-population + Fix 2's
verification working correctly together** — an item now carrying a real
`canonical_utterance_id` is what lets Fix 2's `already_committed`
verification (and the fallback `execute_pipeline_commit` path) actually
succeed for the manual-mode path too, instead of silently skipping.

## Fix 4 — Dead code removal

Grepped every candidate repo-wide before deleting; confirmed zero call
sites for each (only the `def` line and, for `_channels_compatible`, one
comment referencing it — not a call):

| Removed | File | Confirmed via |
|---|---|---|
| `_channels_compatible()` | `utterance_lifecycle.py` | `TASK_1B_CHANGES.md`'s own note ("left defined but unused... after fix #3") + repo-wide grep, zero call sites |
| `_run_evidence_package_worker` | `stop_finalize_worker.py` | `TASK_4A_FINDINGS.md`/`TASK_4B_CHANGES.md`'s confirmation + repo-wide grep, zero call sites |
| `publish_translation_event` | `main_window.py` | `TASK_3A_FINDINGS.md` item 6 ("zero callers anywhere") + repo-wide grep confirming `EventType.TRANSLATION_RECEIVED` is published from nowhere else |

**Not removed, deliberately**: `japanese_sentence_assembler.py`'s
`_translation_unit_builder` wiring — `TASK_3A_FINDINGS.md` explicitly
offered "keep as documented dead code (matching the Task 1B/2B/2D
precedent)" as an equally valid alternative to removal, and Task 3B already
took that option (`JAPANESE_TRANSLATION_UNIT_GROUPING_ENABLED = False`,
a reversible switch, not dead code with zero callers — its gated call site
still exists and would run if the flag flipped). Also left in place:
`_on_translation_started`/`_on_translation_received`/`_on_translation_error`/
`TranslationEvent` in `main_window.py` — `TASK_3A_FINDINGS.md` named
`publish_translation_event` specifically as confirmed-dead; these
surrounding handlers/dataclass were not independently confirmed dead by
that audit (they are wired to the event bus, just never triggered now that
their only publisher is gone) and removing them was judged beyond the
"explicitly marked" scope of this instruction.

## How the fixes interact (found empirically, not planned in advance)

Implementing in the order given (Fix 1, then 2, then 3, then 4) turned out
to be **required**, not incidental:

- Fix 2 implemented before Fix 1 would land: every genuine Japanese commit
  would fail `already_committed` verification (registry never populated)
  and get silently double-processed. Caught by re-running the 48-test
  suite immediately after Fix 1 and before touching Fix 2 — it passed
  clean at that checkpoint, isolating the later Fix-2-caused failures to
  Fix 2 itself.
- Fix 3 implemented before/without Fix 2's registry-verification path
  actually working: manual-mode items would still have no
  `canonical_utterance_id`, and the four `ManualModeIntegrationTests` in
  `test_task2g_acceptance_gate.py` immediately went from 4/4 pass to 4/4
  fail (`records=[]` — nothing committed at all) the moment Fix 2 landed,
  confirming this dependency directly rather than by inspection alone.
- A **test-fixture gap**, not a production bug, caused two further
  regressions caught by the same re-runs: `test_task2c_acceptance_gate.py`'s
  `JapaneseTestHost` never initialized `utterance_lifecycle`'s session
  (Japanese never touched that module before Fix 1), and
  `test_task2g_acceptance_gate.py`'s `ManualModeCommitHost` never reset the
  canonical ledger/identity-registry state between test instances
  (manual-mode commits never reached the real ledger before Fix 2/3). Both
  fixed to match the same session/ledger reset pattern every other test
  host in this suite already uses (`IdentityTestHost`/`EnglishTestHost`).
  Neither fix touched production code.

## New tests (1-2 per fix, `tests/test_task5_final_cleanup.py`)

| # | Test | Fix | Result |
|---|------|-----|--------|
| 1 | `accept_boundary_proposal` registers identity and commits | 1 | **PASS** — confirms `resolve_canonical_record_id` returns the exact record id after a proposal, proving the registry gap Fix 1 closes is actually closed. |
| 2 | Missing `canonical_utterance_id` fails closed | 1 | **PASS** — `accept_boundary_proposal` rejects with `"missing_canonical_utterance_id"`, never guesses. |
| 3 | Unverified `already_committed` claim is not trusted | 2 | **PASS** — an item claiming `_jp_continuity_assembler=True` with no matching registry entry falls through to the real commit path (proven by the registry having a real entry afterward, which only the real path populates). |
| 4 | Verified `already_committed` claim is trusted, no double-commit | 2 | **PASS** — replaying the same item with a matching `canonical_record_id` after a real prior commit produces zero additional ledger actions and no duplicate segment. |
| 5 | New manual-mode segment gets a real `canonical_utterance_id` | 3 | **PASS** — the assigned id resolves in the identity registry (not just present on the item dict). |
| 6 | Merged continuation reuses the same utterance id, bumped version | 3 | **PASS** — same-speaker compound continuation keeps the identity, increments `source_version`, and still merges into one `TranscriptStore` segment (no regression to the merge behavior itself). |

**Result: 6/6 PASS.**

## Full regression suite

| Suite | Tests | Result |
|---|---|---|
| `test_task1_identity_repair.py` (Task 1) | 12 | **12/12 PASS** |
| `test_task1c_acceptance_gate.py` (Task 1) | 7 | **7/7 PASS** |
| `test_task2c_acceptance_gate.py` (Task 2A-2D) | 7 | **7/7 PASS** |
| `test_task2g_acceptance_gate.py` (Task 2E-2G) | 9 | **9/9 PASS** |
| `test_task3c_acceptance_gate.py` (Task 3) | 6 | **6/6 PASS** |
| `test_task4c_acceptance_gate.py` (Task 4) | 7 | **7/7 PASS** |
| `test_task5_final_cleanup.py` (Task 5, this task) | 6 | **6/6 PASS** |
| **Total** | **54** | **54/54 PASS — zero regressions** |

Confirmed via one combined run of all seven files and 3 standalone repeat
runs of the full 54-test combined set for flakiness — identical result
every time.

Two test-fixture updates were required to keep the suite green (documented
above under "How the fixes interact") — both are test-infrastructure
changes (missing session/ledger resets in test hosts), not production code
changes, and both were necessary because the fixes genuinely changed which
code paths those hosts now exercise.

## Frozen infrastructure — confirmed untouched (read-only check)

`git status --short` at the repo root shows only the seven production files
this report names as changed (`utterance_lifecycle.py`,
`japanese_sentence_assembler.py`, `duplicate_protection.py`,
`main_window.py`, plus the pre-existing Task 1-4 changes already on disk),
the new `test_task5_final_cleanup.py`, and the two test-fixture updates. No
file under WASAPI/mic capture, the audio mixer/normalization layer,
Deepgram/DeepL transport clients, or language mappings appears anywhere in
the diff. `deepgram_client.py` and `deepl_client.py` are both absent from
the change list.

## Final verdict

**All four fixes implemented, validated, zero regressions: PASSED.**

- Fix 1: Japanese assembler proposes to the canonical controller; identity
  registry is now populated for Japanese commits (previously never was).
- Fix 2: `duplicate_protection.py` verifies identity before trusting an
  already-committed claim, and before borrowing a positional previous-line
  match — fail-closed on anything unverifiable.
- Fix 3: both legacy manual-mode call sites now carry and propagate a real
  `canonical_utterance_id`, closing the translation-display identity gap
  `TASK_3B_CHANGES.md` left open.
- Fix 4: three confirmed-dead code paths removed after re-verifying zero
  callers each; one additional candidate deliberately left in its existing
  documented-dead-but-reversible state per `TASK_3A_FINDINGS.md`'s own
  stated alternative.
- 54/54 tests pass, including all 48 pre-existing tests from Tasks 1
  through 4 with zero regressions, plus 6 new deterministic tests directly
  exercising Fixes 1-3.
- Frozen infrastructure confirmed untouched.

Stopping here after producing `TASK_5_FINAL_CLEANUP_REPORT.md`.
