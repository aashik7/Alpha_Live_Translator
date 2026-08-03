# Task 2G — Final QA Validation of the Task 2F Manual-Mode Fix

No production code was modified in this task — every test passed against
Task 2F's code as committed, so the "fix a genuine regression" clause was
never triggered. New tests were added in
`Alpha_Live_Translator/tests/test_task2g_acceptance_gate.py`, extending the
existing per-task test-file convention and reusing prior host-class patterns
(method-borrowing onto a lightweight, non-GUI class, same technique as
`test_task3c_acceptance_gate.py`'s `TranslationUIHost`).

Note on the "Test 2: if routed through canonical controller" instruction:
per Task 2F's documented, user-approved deviation, the actual fix applies
the fail-closed pattern (identity-scoped hard speaker boundary via
`speakers_confirmed_same`), not literal `utterance_lifecycle.py`/
`canonical_transcript_ledger.py` routing — Japanese has no working single
controller yet. Test 2 below validates the equivalent outcome against the
path that *was* actually fixed: a wrong-speaker merge is rejected by the
new `TranscriptStore` guard, exactly as `speakers_confirmed_same` already
does for the canonical Japanese assembler (Task 2D).

## Test design

Two layers, both deterministic (no real audio, no live provider calls, no
timers):

- **Set A** — direct unit tests against `TranscriptStore.get_last_segment_if_active`
  / `update_last_segment_if_active`, the actual Task 2F fix.
- **Set B** — integration tests against the real, unmodified
  `AlphaApp._commit_transcript_item_to_store` (the function containing
  Task 2F's two call-site edits), invoked via a method-borrowed host with
  no CustomTkinter GUI constructed, using a real `TranscriptStore` instance
  and real merge-heuristic code — only Tk-widget-touching methods
  (translation display, box rendering) were excluded, since they are
  outside what Task 2F changed.

## 1–3. New test results

| # | Test | Layer | Result | Observed behavior |
|---|------|-------|--------|--------------------|
| A1 | `get_last_segment_if_active` refuses cross-speaker jump | Set A | **PASS** | With store = [speaker1: "A", speaker2: "B"], `get_last_segment_if_active(1)` returns `None` (true last row is speaker 2's); the old `get_last_segment(1)` still returns "A" unchanged, confirming a real, deliberate behavior delta exists between the two methods. |
| A2 | `get_last_segment_if_active` matches true last | Set A | **PASS** | With store = [s1:"A", s2:"B", s1:"C"], returns "C" — correct when the querying speaker genuinely owns the last row. |
| A3 | `update_last_segment_if_active` refuses cross-speaker write | Set A | **PASS** | Attempting to write as speaker 1 when the true last row belongs to speaker 2 returns `False`; both existing segments are byte-for-byte unchanged afterward. |
| A4 | `update_last_segment_if_active` updates true last | Set A | **PASS** | Writing as speaker 2 when speaker 2 owns the true last row succeeds; only that row's text changes. |
| A5 | Unknown/`None` speaker never confirmed-same | Set A | **PASS** | A segment with `speaker=None` is never matched by `get_last_segment_if_active(None)`/`update_last_segment_if_active(None, ...)` — fail-closed via `speakers_confirmed_same`, not a raw `==` that would treat two `None`s as equal. |
| B1 | Cross-speaker interjection prevents bypass merge (item 1: **4th-authority-path test**) | Set B | **PASS** | Speaker 1 commits a compound-continuation-ending line; speaker 2 interjects; speaker 1 then commits a compound-continuation-starting line. Result: **3 independent segments**, not 2 — the third item was *not* silently merged backward into segment 1 across speaker 2's turn. Segment 1's text is confirmed unchanged (`assertNotIn` on the would-be merged string). This is the exact bypass `TASK_2E_FINDINGS.md` documented; it is now closed. |
| B2 | Wrong-speaker merge rejected, Task 2D two-speaker pattern reused (item 2) | Set B | **PASS** | Speaker 2's text, deliberately constructed to satisfy the compound-continuation pattern against speaker 1's prior line if speaker were ignored, is committed as its own independent segment 2; speaker 1's segment 1 text is confirmed unchanged. |
| B3 | Legitimate same-speaker compound continuation still merges (item 3: regression guard) | Set B | **PASS** | Same speaker, no interjection: two compound-continuation-shaped lines correctly merge into **one** segment, exactly as before Task 2F. `_on_store_segment_updated` is confirmed called for the merging speaker. |
| B4 | Legitimate same-speaker particle continuation still merges (item 3: regression guard, second heuristic) | Set B | **PASS** | A distinct merge heuristic (particle-ending continuation, not compound) also still merges correctly for the same speaker with no interjection — broader confirmation that `TASK_2E_FINDINGS.md` item 4's "real, currently necessary work" is preserved. |

**Result: 9/9 new tests PASS.**

## Full regression suite

| Suite | Tests | Result |
|---|---|---|
| `test_task1_identity_repair.py` | 12 | **12/12 PASS** |
| `test_task1c_acceptance_gate.py` | 7 | **7/7 PASS** |
| `test_task2c_acceptance_gate.py` | 7 | **7/7 PASS** |
| `test_task3c_acceptance_gate.py` | 6 | **6/6 PASS** |
| `test_task2g_acceptance_gate.py` (new) | 9 | **9/9 PASS** |
| **Total** | **41** | **41/41 PASS — zero regressions** |

Confirmed by a single combined `unittest` run (all five files together) and
re-confirmed by three standalone repeat runs of the new suite alone (9/9,
9/9, 9/9) — no flakiness observed.

## Frozen infrastructure — confirmed untouched (read-only check)

`git status --short` at the repo root shows exactly:

- **Modified** (all pre-existing from Tasks 1B/1C/2B/2D/3B, plus this
  task's two Task-2F files):
  `canonical_transcript_ledger.py`, `duplicate_protection.py`,
  `japanese_boundary_stabilizer.py`, `japanese_final_chunk_stabilizer.py`,
  `japanese_sentence_assembler.py`, `pipeline_commit_transaction.py`,
  `stable_line_revision.py`, `stable_revision_decision.py`,
  `utterance_lifecycle.py`, `translation_worker.py`, `main_window.py`,
  `session_runtime.py`, **and `transcript_store.py`** (Task 2F's new file).
  `duplicate_protection.py` and `japanese_final_chunk_stabilizer.py` were
  already modified before this session began (Task 1B/2B) — confirmed via
  this session's own git-status snapshot at start — and were explicitly
  **not** touched again in Task 2F or this task.
- **New**: `canonical_identity_registry.py`, `speaker_boundary_guard.py`
  (existing shared modules from Task 1/2D), all test files including this
  task's `test_task2g_acceptance_gate.py`, and the root-level planning/
  report docs including this report.

No file under WASAPI/mic capture, the audio mixer/normalization layer,
Deepgram/DeepL transport clients (`deepl_client.py` untouched), language
mappings (`language_map.py` untouched), or UI layout/styling files appears
anywhere in the diff. Frozen infrastructure is confirmed untouched.

## Final verdict

**4th-authority path closed: CONFIRMED**

- The specific bypass documented in `TASK_2E_FINDINGS.md` — the manual-mode
  Japanese merge path reaching backward across an intervening
  different-speaker turn and silently overwriting a stale, non-adjacent
  `TranscriptStore` row — is closed, verified by test B1 directly
  reproducing the exact scenario and confirming 3 independent segments
  result instead of a corrupted 2.
- The fix is symmetric on both the read side (`get_last_segment_if_active`)
  and write side (`update_last_segment_if_active`), both confirmed
  independently (Set A) and in the real production call path (Set B).
- The path's legitimate, currently-necessary function (same-speaker
  Japanese continuation merging, confirmed reachable/default in
  `TASK_2E_FINDINGS.md` item 4) is preserved — verified for both
  merge heuristics exercised (compound continuation, particle continuation)
  with zero regression.
- 41/41 total tests pass, including all previously-passing Task 1/2/3
  suites, with zero regressions.
- Frozen infrastructure confirmed untouched by read-only diff check.

Remaining, previously-documented, explicitly out-of-scope gaps (not
blockers for this verdict, already tracked elsewhere): channel-blind commit
(no channel concept exists anywhere in `TranscriptStore`, per
`TASK_2F_CHANGES.md`'s "Not addressed" section — architecture work tied to
the still-open Phase-4 single-controller item); the interim Stop-tail
recovery call site (`main_window.py:_recover_interim_tail_on_stop`),
deliberately left out of Task 2F's scope since it is shared with English,
not Japanese-manual-mode-specific.

Stopping here per instruction.
