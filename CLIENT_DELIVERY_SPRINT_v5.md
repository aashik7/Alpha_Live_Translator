# Alpha Live Translator — Client Delivery Sprint

**Version:** v5 (2026-08-10). **Supersedes `BUG_FIX_ROADMAP.md` v4 for
execution order and scope.** v4 remains the historical evidence record —
read it only when an item below points you there.

**Deadline: client delivery 2026-08-24.** This is a client project, not a
personal tool. It has to keep working when nobody is sitting next to it.

---

## 0. Agent: read this section first, every session

You are continuing work on a Windows desktop app that live-translates
meeting audio between Japanese and English. A long bug-fix campaign has
already run (Batches 1–3 complete, Batch 4 partially). **Do not restart
that campaign.** This file replaces its ordering and scope.

**Three rules that override everything else in this sprint:**

1. **No architecture changes.** No rewrite, no controller redesign, no
   three-store consolidation, no capture-layer change. Those are real and
   necessary work, and they are deferred to after delivery
   (§6). Attempting them inside 2 weeks is the single most
   likely way to miss the date.
2. **Never leave two authorities alive.** If a fix would mean the old
   path and the new path both running, stop and escalate to the human.
   Every "patched the dangerous symptoms instead" note in the existing
   reports is a place this rule would have fired.
3. **No drive-by fixes.** Notice something else broken → one line in §9
   and keep going. This project's ledger shows six separate cases where
   one fix unmasked the next; inside a 2-week window that cascade is the
   main risk to manage, not a curiosity.

---

## 1. Where the project is right now

**It largely works.** Recent live runs in both languages complete with
`final_status: completed`, `stop_finalize_failed: false`, translation
jobs 31/31 and 12/12, zero duplicate submissions, watchdog firing down
from 83% to 2–3%, Start-button freeze gone (8.8 s → 0.0 s), and the
revise path working for the first time (was 0 of 138 commits, now
observed). Full suite: **354 tests**, baseline **5 failures + 2 errors +
2 skipped**, same 7 names throughout — those 7 predate this engagement.

**What is still genuinely broken.** F was found 2026-08-11 via the first
real live-audio test this sprint has had (item 40) and is **more severe
than A for any English-source session** — flagged first, not relabeled
into the A-E order below because that order predates it and reordering
it silently would hide that this is new information, not always-known
priority. Human call on where it slots into §5's day plan; see §9.

| | Problem | Evidence |
|---|---|---|
| **F** | ~~pending~~ **FIXED and CONFIRMED on a second live run, 2026-08-11, item 51.** English commits self-concatenated on every reformatting variant Deepgram sends for the same growing utterance ("50 percent" then "50%", "mister" then "Mr."), and again on every slid window that repeated the previous chunk's tail — instead of replacing or joining. Corruption compounded every tick and landed in the canonical ledger, translation input, and final export alike | Reproduced against the real function from the **verified real input sequence** of run `...182940` utterance `U-1` (9 chunks, recovered and confirmed by re-merging them pre-fix to get the recorded corrupted line back byte-for-byte). Pre-fix that folded to **472 characters** with "olympia" ×4, "bodybuilder" ×6; post-fix **127 characters**, each phrase once, matching the reference transcript. Export-wide pre-fix: 5 of 54 lines carried **85.9%** of all characters, worst 5039 chars from ~112 glued fragments. Pre-existing, not new — same pattern on run `...133236` (2026-08-08). Japanese never reaches this function. Tests: `test_utterance_lifecycle_merge_lexical.py` (20). See §9 |
| **A** | ~~pending~~ **FIXED 2026-08-12, item 42.** Japanese sentences committed by the assembler were **overwritten in the canonical ledger by a later, textually unrelated commit that reused the same `canonical_utterance_id`** | Root cause proven in item 41 (`PROBLEM_A_ROOT_CAUSE.md`): the id-mint gate read a flag computed *before* the revision authority ran and never recomputed, so a candidate the authority had just judged a **new sentence** still reused the previous id, and the ledger revise replaced `final_text` in place. 10 real sentences lost across the recorded corpus. Fixed by requiring a revise to be non-destructive by content; a disjoint follow-up now gets its own record. Both halves are pinned: no loss **and** no duplication — see §9 |
| **B** | ~~pending~~ **FIXED 2026-08-12, item 43.** Real speech quarantined as `noise_fragment` and never recovered | Corpus re-measured 2026-08-12 and it corrects this row's old "2 of 2": **4 distinct fragments were dropped, 3 of them real Japanese speech** — `寝れた、幸せ、`, `。忘れちゃうし、`, `最近また` — with only a bare `、` being true noise. Quarantine no longer deletes: expired and stop-flushed fragments are committed flagged. Verified end-to-end on the realistic path (expiry mid-session, then more speech): the recovered fragment now reaches the ledger. See §9 |
| **C** | ~~pending~~ **FIXED 2026-08-12, items 22 + 23 + 33s.** Two speakers' turns could merge into one line | Reproduced directly before fixing: two *unknown* speakers produced `'the first speaker said this, a totally different remark'` on one line, and a speaker change on a held chunk produced `'speaker one is talking here, speaker two interrupts now'`. Both now start their own utterance. Guarded against over-tightening by `SameSpeakerStillMergesTest` — one speaker's continuous speech must still merge across 3 chunks. Replay decision counts on all 7 runs are **unchanged**, so nothing fragmented. See §9 |
| **D** | No behaviour defined for network drop, DeepL quota exhaustion, device change, or invalid credentials | Not covered by any of v4's 37 items |
| **E** | Verification depends on a human running live sessions | Why v4 items 10/11 sat unverified across five sessions |

F is the most severe single defect found this sprint for any English-source
client session — a wall of unreadable repeated text on nearly every
longer utterance, not a rare edge case. A and B are content loss on the
Japanese side. C is visible on stage. D is what breaks on a client's
machine when you are not there. E is why everything takes days instead
of minutes.

---

## 2. Orientation — do this at the start of every session

Shortened from v4 §1 deliberately. Do not run v4's full ritual; it costs
20–40 minutes per session and this sprint cannot afford it.

1. Derive the repo root from your working directory. **Do not hardcode
   it** — this repo has moved between Windows user accounts once already.
   Confirm this file and `Alpha_Live_Translator\` are both in it.
2. Read the repo root's `CLAUDE.md` for standing restrictions.
3. Read **this file only**. Do not read `BUG_FIX_ROADMAP.md` unless an
   item sends you there.
4. `git log --oneline -15`. Compare against §8's ledger. If they
   disagree, **fix this file first** and note it in §9.
5. Run the test baseline (§3). If it is not 354 / 5F / 2E / 2S, stop and
   tell the human.
6. Go to the current day in §5.

---

## 3. Project facts

**Test command**, from inside `Alpha_Live_Translator\`:

```bash
SKIP_TK_INTEGRATION_TESTS=1 "<repo_root>/.venv/Scripts/python.exe" -m unittest discover -s tests -p "test_*.py"
```

There is no pytest in this venv; `unittest discover` is the only runner.

**Baseline:** **442 tests** (was 354; items 38/38b/39 added 31, item 51 added
25, item 42 added 11, item 43 added 8, items 22/23/33s added 13), 5 failures +
2 errors + 2 skipped. Same 7 names. Any change to that set means you broke
something.

**One caveat, added 2026-08-11.** The 7 names are the *stable* set, but
an 8th can appear:
`test_task9_report…test_inactivity_timeout_fallback_survives_immediate_real_stop_5x`
is a real-thread timing test that failed two subtest iterations in one
full-suite run and passed the next, and passes 3 runs out of 3 when its
module runs alone. It is **intermittent, not a regression** — if it is
the only new name, re-run before concluding anything. Any *other* new
name is real. See §9.

**Venv:** `<repo_root>\.venv\Scripts\python.exe`. The bare system python
will fail with `ModuleNotFoundError`. If the venv reports
`did not find executable at ...`, repoint `home` / `executable` in
`pyvenv.cfg` to the local Python 3.14 — `.venv` is not relocatable and
this has already happened once.

**Never use `sed -i` on this repo's `.py` files.** It flips CRLF to LF and
produces full-file diffs. Use Edit.

**Locating code:** use grep anchors, never line numbers. v4's line
numbers went stale twice.

---

## 4. Method — every item, no exceptions

Carried from v4 §3, with two amendments marked **[v5]**.

1. **INVESTIGATE.** Grep the anchor, read the code, read the referenced
   run logs. If reality contradicts the item's description, **the
   description is wrong** — say so and log it in §9. Never force a fix to
   match a stale description.
2. **DESIGN.** Propose the exact change: what triggers the bug, why this
   addresses the root cause, what could go wrong, what stays unchanged.
3. **APPROVAL GATE. [v5]** Required for items marked `[gate]` in §8. For
   items following an established template, proceed without waiting.
4. **IMPLEMENT.** Only the approved change, only the named files.
5. **PROVE THE TEST CATCHES THE BUG.** Write the regression test, then
   temporarily disable your fix and confirm the test **fails** the way the
   original bug would. Restore, confirm it passes. **This step is not
   optional** — it caught a genuinely wrong test in item 14.
6. **VERIFY NO REGRESSION.** Full suite. Baseline must be exactly the
   same 7 names, skips still 2. **[v5]** Also run `tools/score_run.py`
   (item 39) against the reference runs.
7. **COMMIT.** One fix per commit, revertable on its own.
8. **UPDATE §8 AND §9 BEFORE ENDING YOUR TURN.** Commit hash, date, what
   the fix actually was, test file, model used. If you completed only
   some of steps 1–7, still record where you stopped and what you found.

**Rollback:** if step 5 or 6 fails and you cannot resolve it in the same
turn — `git checkout --` if uncommitted, `git revert <hash>` if
committed. Never amend or reset; history stays honest across agent
sessions. Record it in §9 and leave the item pending.

**Live-test protocol:** you cannot run the app. When a live session is
needed, tell the human exactly what to speak and for how long, ask for
the newest folder under `troubleshooting/runs/`, then analyse it
yourself. **Do not treat `coverage_ratio: 1.0` as proof nothing was
lost** — that gate only compares canonical → final, so a record lost
before reaching the ledger is invisible to it. That is exactly how
problem A hid. Compare assembler decision counts against ledger record
counts directly.

---

## 5. The plan — 14 days

### Days 1–2 (Aug 10–11) · Instrumentation — items 38, 39, 40

Nothing else starts until this is done. Right now every verification
costs a human live session plus manual log reading; that is why items
sat unverified for five sessions. Two days here buys back the other
twelve.

- **38** replay harness — **Opus 5** — DONE, `960f907` + `2d34a41`
- **39** run scorer — **Sonnet 5** — DONE, `410f6bf`
- **40** reference corpus + baseline numbers — **Sonnet 5** — **BLOCKED,
  needs one live session, see §8/§9.** Everything else in this phase
  does not wait on it.

**Gate:** ~~all 27 recorded runs replay~~ — **corrected 2026-08-10, see §9.**
Only **6** runs are replayable from `provider_events.jsonl` at all; the
other 21 carry no genuine provider ingress (10 have the file but only
post-lifecycle re-emissions, 11 lack the file). All 6 are Japanese —
English sessions record no raw ingress by design.

Gate is therefore a content requirement, not a count: **all 6 Japanese
runs with genuine provider ingress replay** — `...20260807-155922`,
`...20260807-160130`, `...20260807-160529`, `...20260808-134815`,
`...20260808-155334`, `...20260809-174516` — **and the recorded-side
loss measurement lands on all 6.** Measured 2026-08-11:

| Run | ingress fed | commits | dropped sentences |
|---|---|---|---|
| `...155922` | 3 | 3 | 1 |
| `...160130` | 11 | 4 | 2 |
| `...160529` | 25 | 10 | 2 |
| `...134815` | 50 | 29 | 3 |
| `...155334` | 71 | 32 | 6 |
| `...174516` | 82 | 36 | 0 |

~~"Loss pattern present: 160529 (−1), 134815 (−6), 155334 (−6). Zero loss:
155922, 174516."~~ — **superseded 2026-08-11 before it was written in.**
Those figures are the decisions-minus-ledger delta, which both
over-counts (a genuine `revise` is not a loss) and under-counts (an
overwritten id still has a ledger record). `...155922` was not zero-loss;
`...174516` is the only genuinely clean run. See §9.

**Replay does not reproduce any of the 14 losses** — that is a finding
under condition 4, not a gate failure. See §9 and item 38b.

### Days 3–6 (Aug 12–15) · Content integrity — items 41, 42, 43, 22, 23, 33s

The bugs that lose the client's words.

- **41** prove problem A's root cause against the fixture — **Fable 5,
  `/effort xhigh`**. Item 38 localised it: the assembler commits two
  textually disjoint sentences under one `canonical_utterance_id`, the
  ledger keys on that id, and the second commit overwrites the first.
  Item 38b then ruled out timing as the mechanism (real-timer replay
  fixes decision *counts* but still reproduces 0 of 14 losses) and left a
  specific, cross-validated lead in its place — **do not start 41 from
  scratch, start from this:**

  `japanese_sentence_assembler.py:3635` computes `update_previous_requested`
  from boundary-stabilizer signals *before* `decide_stable_revision_action`
  runs. That function's verdict (`final_revision_action`) can disagree —
  correctly recognise a candidate as a new, disjoint sentence
  (`"append"`) — and when it does, lines 3749–3756 reset
  `stable_layer_update_previous`/`post_update_previous` and even clear
  `metadata["boundary_should_revise"]`, but **`update_previous_requested`
  itself is never reassigned.** The id-mint gate at line ~3912
  (`proposed_action = "revise_previous" if update_previous_requested else
  "commit_new"`) reads that stale variable, not `final_revision_action`.
  Cross-checked against `logs/japanese_accuracy.log`'s
  `STABLE_REVISION_DECISION` events on all 6 runs: `update_previous_requested
  == True and final_action == "append"` matches 12 of 13 observed
  id-reuse events exactly (§9, 2026-08-11). **This is a lead, not a
  proof** — unverified: why the boundary stabilizer sets
  `update_previous_requested=True` on a candidate its own downstream
  check rejects, and whether fixing the read site (use
  `final_revision_action`) or the write site (boundary stabilizer) is
  correct. That determination is 41's actual job. Do not expect
  `replay_run.py`, real-timer or not, to reproduce this for you — drive
  `decide_stable_revision_action`/the boundary stabilizer directly, or
  work from `japanese_accuracy.log` on the recorded runs.
- **42** fix it: a commit that is not a revision of its target must
  never land on that target's id — no silent overwrite. An overwrite
  that is genuinely wanted must be a containment/extension of what it
  replaces, or it fails loudly with both texts in the log — **Fable 5**
- **43** make quarantine non-destructive — **Opus 5**
- **22, 23** speaker fail-open and Case B cross-speaker merge — **Opus 5**
- **33s** scoped: stop the relabeled speaker feeding the
  same-speaker-extension check — **Opus 5**

**Gate:** ~~run `...20260807-160529` replays with assembler decisions ==
ledger records == export lines~~ — **wrong equality, corrected
2026-08-11.** A genuine `revise` makes decisions exceed ledger records
legitimately (10 vs 9 on that run is one revision, not one loss), and an
overwritten id still produces a ledger record, so that equality is both
too strict and blind to the actual bug.

Replacement gate: **`tools/replay_run.py --all` reports 0 dropped
sentences on the recorded side of all 6 runs** (currently 14), each drop
independently confirmed against the export. Zero cross-speaker merges on
the reference corpus.

### Days 7–9 (Aug 16–18) · Resilience — items 44, 45, 46, 47, 48

This is the section that exists because it is a client project. On your
own machine you restart the app when something goes wrong. On theirs,
nobody will know what happened.

- **44** Deepgram reconnect mid-session — **Opus 5**
- **45** DeepL failure/quota degrade + circuit breaker — **Opus 5**
- **48** 60-minute stability: bounded queues, ledger memory, Tk line cap
  — **Opus 5**
- **46** credential failure surfaced at Start — **Sonnet 5**
- **47** connection status indicator — **Sonnet 5**

**Gate:** 60-minute session with a deliberate network drop ends
`completed` with zero content loss.

### Days 10–11 (Aug 19–20) · Validation and packaging — items 49, 11b-LT

- Full suite, replay all runs, score everything.
- **11b-LT**: the two Stop variants that have never executed live —
  (a) Stop immediately mid-sentence, (b) Stop after a ~10 s pause.
- **49** clean-machine install — **Sonnet 5**. The `.venv` has broken on
  a user-account move before; find that class of problem here.
- Three full sessions per direction **from the installed build**.

### Day 12 (Aug 21) · Code freeze

Tag `client-delivery-v1`. If buffer remains, item **50** (DeepL `context`
parameter) is the only optional addition — one API parameter,
unbilled characters, meaningful terminology consistency gain.

### Days 13–14 (Aug 22–23) · Rehearsal, zero code

Run the delivery scenario end to end on the actual machine. Two tagged
builds exist; if something breaks, demo the one that works.

### Before anything else, today

```bash
git tag -a known-good-v0 -m "Working build before sprint"
```

Package this commit and store the installer outside the working tree.
Fallback is not time — it is a tag. If at any point the tree is worse
than this and you cannot see why within 30 minutes, reset to it.

---

## 6. Explicitly out of scope until after 2026-08-24

Do not start these, do not partially start these, do not "just quickly"
do these:

Single canonical controller rewrite (v4 items 28–32) · three-store
consolidation (item 32) · dual-channel capture · dead-code removal
(item 36) · items 25, 27, 27b, 28a, 28b, 30, 35, 37 · Japanese
phrase-table cleanup · translation quality work beyond item 50 · the
summary feature.

**This list is the post-delivery backlog** — there is no separate
roadmap file, deliberately (see §9, 2026-08-11). Anything deferred gets
one line here, so there is exactly one place to look.

**Dual-channel capture is the highest-value item in it** — it makes
speaker identity a fact rather than a guess and deletes most of the
cross-speaker bug family. Cost impact is roughly $0.46 → $0.92 per audio
hour; the reason to defer is the 2-week window, not the money.

Also deferred here, added 2026-08-11:

- **English replay coverage.** `tools/replay_run.py` is Japanese-only by
  evidence, not choice: only `japanese_final_chunk_stabilizer.py` calls
  `record_raw_deepgram_final` on true ingress, so all 10 recorded English
  runs hold zero replayable rows. The `raw_deepgram_finals.jsonl`
  fallback adapter stays **rejected** — a second input adapter is two
  definitions of "replay input", which §0 rule 2 forbids. The real fix is
  upstream: record genuine ingress for English too, one format for both
  languages. Revisit only if items 44/48 turn out to need it.
- **WER gap vs. raw Deepgram.** The real accuracy measure. Record it at
  baseline and at delivery, but do not gate the client date on it —
  improving it is post-delivery work.

---

## 7. Definition of done for delivery

All of these, on the reference corpus and on three consecutive 30-minute
sessions per direction:

| # | Gate | Target |
|---|---|---|
| 1 | committed sentences dropped before the export (`replay_run.py`, recorded side) | 0, 3 runs |
| 2 | exact-duplicate lines in export | 0 |
| 3 | canonical records without a translation | 0 |
| 4 | lines containing two speakers' turns | 0 |
| 5 | quarantine events losing real speech | 0 |
| 6 | 60-min session with forced network drop | `completed`, zero loss |
| 7 | Start→Stop→Start, 5 cycles | no degradation |
| 8 | clean-machine install runs the full scenario | pass |

The WER gap vs. raw Deepgram is the real accuracy measure, and it is
deliberately **not** in the table above — record it at baseline and at
delivery, but do not gate the client date on it. See §6.

---

## 8. Item ledger — complete history and remaining work

Model column records what was actually used (completed) or is
recommended (pending). `[gate]` = human approval required before coding.

### Batch 1 — trivial, standalone · COMPLETE

| Item | What | Commit | Model | Status |
|---|---|---|---|---|
| 1 | Dead zero-arg `_flush_pending_translation_submit()` in `_begin_graceful_stop` | `6564b36` | Sonnet 5 | DONE |
| 2 | `INTERIM_GHOST_TTL_MS` 1500 → 6000, from measured 1924 ms en / 4063 ms ja | `9892cb1` | Sonnet 5 | DONE |
| 3 | `scan_interim_ghost_evidence.py` REVIEW verdict at ≥25% watchdog ratio | `38a92b5` | Sonnet 5 | DONE |

### Batch 2 — make silent failures loud · COMPLETE

| Item | What | Commit | Model | Status |
|---|---|---|---|---|
| 4 | `_remove_interim_line_from_display` mark guard (92→0 exceptions/run) | `ddeb67f` | Sonnet 5 | DONE |
| 5 | Log 3 silent excepts in `_on_store_segment_updated` | `5181b8c` | Sonnet 5 | DONE |
| 6 | Log `reset_for_session` + `_resolve_correction_target_locked` | `38c6096` | Sonnet 5 | DONE |
| 7 | Log remaining 6 silent excepts across 5 files | `07d5234` | Sonnet 5 | DONE |
| 7b | Japanese stabilizer failure no longer falls into English commit path | `6726f68` | Opus 5 | DONE |
| 8 | Log `_observe_identity` fail-open (behaviour unchanged) | `ec38779` | Sonnet 5 | DONE |
| 9 | Quarantine evidence scan (investigation only) | doc only | Sonnet 5 | DONE |
| 9b | Start-button freeze: audio cleanup made non-blocking (8.8 s → 0.0 s) | `b220c86` | Sonnet 5 | DONE |

### Batch 3 — identity and text-comparison hardening · COMPLETE

| Item | What | Commit | Model | Status |
|---|---|---|---|---|
| 9c | Translation-gap self-heal used the wrong id space | `13f20ca` | Opus 5 | DONE |
| 10 | `_check_stop_tail_duplicate` narrowed to equality-or-prefix | `b404c19` | Opus 5 | DONE |
| 11 | `_should_commit_interim_recovery` narrowed likewise | `b2b39de` | Opus 5 | DONE |
| 11b | Watchdog stashes orphaned interim before clearing | `69605cc` | Opus 5 | DONE |
| 11c | Stop-tail min length split: CJK 4 / Latin 20 (from 2210 interims) | `c43f57b` | Opus 5 | DONE |
| 12 | `_should_repair_previous_segment` containment narrowed | `af6781e` | Sonnet 5 | DONE |
| 13 | Substitution corrections update instead of duplicating, id-only | `8f19afe` | Opus 5 | DONE |
| 14 | `merge_japanese_fragments` silent whole-fragment discard removed | `5ffb18d` | Sonnet 5 | DONE |
| 15 | 5 ordinary connectives removed from speaker-lock prefixes | `5ffb18d` | Sonnet 5 | DONE |
| 16 | `teams_commit_decision_from_dup_action` confirmed diagnostic-only | `5ffb18d` | Sonnet 5 | DONE |
| 17 | Unsafe store read/write variants retired, 4 call sites | `43374ad` `5d4578a` | Opus 5 | DONE |
| 18 | `add_translation` matches on `canonical_utterance_id` | `0aa6a8f` | Sonnet 5 | DONE |
| 19 | `duplicate_continuation_ratio` narrowed to prefix-or-suffix | `0aa6a8f` | Sonnet 5 | DONE |
| 19b | `merge_pending_and_current` appends instead of revising | `ac8feb5` | Opus 5 | DONE |
| 20 | Per-utterance `event_id` (was the connection-level constant) | `3a59f6d` | Sonnet 5 | DONE |
| — | Stop-tail end-to-end harness, 17 tests | `9dae426` | Opus 5 | DONE |

### Batch 4 — concurrency and state-machine safety · PARTIAL

| Item | What | Commit | Model | Status |
|---|---|---|---|---|
| 20b | Japanese appends no longer set a self-referential `revision_target_id` | `2367285` | Opus 5 | DONE |
| 20c | 5th containment site in `decide_transcript_action` narrowed | `012695d` | Opus 5 | DONE |
| 21 | `_confirm_transcript_commits` verdict actually gates run status | `0ed9991` | Opus 5 | DONE |
| 22 | Unknown speakers both default to `1` (fail-open); use `speakers_confirmed_same()` | **Opus 5** | 5 | **DONE 2026-08-12.** `_compatible_with_active_locked` now normalises unknown speakers (`None`/`0`/`""`) to `None` via `_known_speaker` and compares with the fail-closed `speakers_confirmed_same`. Swapping the call in alone would **not** have been enough — it reads `0 == 0` as a confirmed match, so the normalisation is load-bearing |
| 23 | Case B merges across channel/speaker; align to Case C's gating | **Opus 5** | 5 | **DONE 2026-08-12.** `force_new` was `not same_active and active is not None and active.committed` — so an incompatible candidate merged whenever the active utterance was *uncommitted*, which is the normal state for a held final chunk. Now `active is not None and (active.committed or not same_active)`, matching Case C. **22 and 23 interlock** — see §9 |
| 24 | `revise_last_transcript_snapshot` has zero speaker check | — | **Opus 5** | **SPRINT — day 6** |
| 11b-LT | Verification only: Stop after ~10 s pause, never executed live | — | human session | **SPRINT — day 10** |
| 25 | UI mints its own `canonical_utterance_id` | — | Opus 5 | DEFERRED — post-delivery |
| 26 | Ledger has no internal version guard | — | Opus 5 | DEFERRED — post-delivery |
| 27 | `_observe_identity` fail-open: decide from Batch 2 evidence | — | Opus 5 | DEFERRED — post-delivery |
| 27b | `_textually_related_revision` threshold tuned while lineage was constant-true | — | Opus 5 | DEFERRED — post-delivery |

### Batch 5 — single canonical controller · DEFERRED, except the scoped items below

| Item | What | Model | Status |
|---|---|---|---|
| 33s | **Scoped**: stop relabeled speaker feeding same-speaker extension | **Opus 5** | 6 | **DONE 2026-08-12.** `_publish_sentence` captures `boundary_speaker` (the raw provider label) *before* `_resolve_output_speaker` relabels for display, and passes that to `decide_stable_revision_action`. Closes a feedback loop: one of the stabiliser's own lock reasons is "text reads like a continuation", so its output was manufacturing the same-speaker agreement the guard exists to test. **Not observable today** — the guard is already fail-closed (item 41) — this removes the trap |
| 34 | `noise_fragment` threshold from evidence | — | SUPERSEDED by item 43 |
| 28, 29 | Fixture capture + root-cause proof for problem A | — | ABSORBED into items 41, 42 |
| 28a, 28b, 30, 31, 32, 33 (full), 35, 36, 37 | Controller rewrite, store consolidation, key redesign, dead code | Fable 5 / Opus 5 | DEFERRED — see §6 |

### New items — this sprint

| Item | What | Model | Day | Status |
|---|---|---|---|---|
| 38 | `tools/replay_run.py` — replay a recorded run's `provider_events.jsonl` headlessly, diff against its FINAL export | **Opus 5** | 1–2 | **DONE** — gate resolved 2026-08-11, all 5 conditions met. Tests `tests/test_replay_run_verdict.py` (10). Recorded-side measurement is the deliverable and it proved problem A's mechanism (id collision, 14 sentences, 505 chars, 14/14 confirmed absent from export). Replay side does **not** reproduce it — finding under condition 4, carried to item 38b |
| 38b | Real-timer replay: drive the assembler through `LanguagePipelineWorker` scheduling instead of fast-feed | **Opus 5** | — | **DONE 2026-08-11.** `tools/replay_run.py --real-timer`, tests in `test_replay_run_verdict.py::RecordedGapsTest` (pure-function; the real-timer replay itself is a slow opt-in tool, not suite-run). Result: fixes decision-count divergence (was 13 vs 29 etc., now matches within 1 on every run) but **still reproduces 0 of 14 content losses.** Timing was not problem A's mechanism. Left a cross-validated lead for item 41, see §9 and item 41's rewritten description |
| 39 | `tools/score_run.py` — pass/fail on the §7 gates plus latency percentiles | **Sonnet 5** | 2 | **DONE 2026-08-11.** Tests in `test_score_run.py` (17). Gates 1–3 are real verdicts; gates 4–5 need `--reference` (item 40) or report `NOT_MEASURABLE`/`HEURISTIC`, never a fabricated pass; gates 6–8 always `NOT_MEASURABLE` (live-session/installer properties, not a run folder's). See §9 for what it found on the existing 27 runs |
| 40 | Reference corpus: 10 min ja + 10 min en, hand-written expected transcript, baseline recorded | **Sonnet 5** | 2 | **DONE 2026-08-11.** Human ran a live ja session (`...20260811-175628`) and a live en session (`...20260811-182940`), each played back a public YouTube video with its own accurate captions/transcript as the reference — a clean way to get verbatim ground truth without needing the reference hand-typed live. References installed at `Alpha_Benchmark_References/current_{japanese,english}_actual.txt` (stale 2026-07-24 ones archived to `archive_20260724/`, not deleted). Scored with the existing `analyze_alpha_vs_reference.py`. Japanese baseline: `unaligned_alpha_line_ratio` 0.31, `cumulative_duplicate_count` 0 — usable. **English scoring surfaced problem F** (new row in §1) before the reference-comparison numbers could even be trusted — `analyze_alpha_vs_reference.py`'s own duplicate detector reads 0/false on this run despite the corruption, so its English CER numbers are not reliable either; direct counts in §9 instead. See §9 for the full trail |
| 41 | Prove problem A's root cause against run `...20260807-160529` — prove, do not assume | **Opus 5** (actually used) | 3–4 | **DONE 2026-08-12 — ROOT CAUSE PROVEN.** Supersedes the 2026-08-11 "strongly indicated, not proven" verdict, which was correct when written. Proof: `PROBLEM_A_ROOT_CAUSE.md` (repo root; `troubleshooting/` is gitignored). Phase 5 fixture now exists: `Alpha_Live_Translator/tools/reproduce_problem_a.py` — deterministic, headless, single-variable, **exits 1 today**. Two things closed the gap: driving `_publish_sentence` instead of `_route_stable_publish` (the layer above merges the texts first, making the revise harmless), and finding that `previous_record` is built with **no `speaker` key**, which makes the revision-decision engine fail-closed to `append` on every non-first commit (105/111 measured) — so Rules A–F have never executed. **Item 42 brief is §8 of the proof doc.** No `.py` modified by item 41 |
| 42 | Fix problem A: a commit that is not a revision of its target must never land on that target's id | **Opus 5** (actually used) | 4–5 | **DONE 2026-08-12.** Gate approved before implementation. The id-mint gate no longer reads the stale `update_previous_requested` alone — a revise now also has to be **non-destructive by content** (`_revision_is_non_destructive`), which is the discriminator item 41 measured as exceptionless. Blocked revises log `DESTRUCTIVE_REVISE_BLOCKED_NEW_UTTERANCE_ID` and mint a fresh id instead of silently overwriting. Tests `tests/test_problem_a_destructive_revise.py` (11), proven to fail on reverted code. `tools/reproduce_problem_a.py` now exits **0** (was 1). Suite 421, same 7 names |
| 43 | Quarantine becomes non-destructive: flag and commit, never drop. Supersedes item 34 (n=2 is too small to tune a threshold) | **Opus 5** (actually used) | 6 | **DONE 2026-08-12.** Both silent drop sites now recover instead of discarding: expiry queues to `_quarantine_recovery_pending` and commits **outside the assembler lock** (the worker holds it, so an inline commit would deadlock); stop-flush commits every real-content fragment rather than only "valid short list terms". Flagged `quarantine_recovered`. **One class deliberately still not committed** — text with no word characters, because that trips `accept_boundary_proposal`'s `empty_text` failure which sets the session-killing commit gate; it is logged `NOISE_FRAGMENT_NOT_COMMITTABLE`, never silently dropped. Tests `tests/test_quarantine_non_destructive.py` (8), both sites proven independently on reverted code. Suite 429, same 7 names |
| 44 | Deepgram websocket reconnect mid-session: backoff, buffer, commit in-flight, mark the gap visibly | **Opus 5** | 7 | TODO `[gate]` |
| 45 | DeepL failure/quota: degrade visibly, retry with backoff, circuit-break after N, never block the transcript | **Opus 5** | 8 | TODO |
| 46 | Invalid/expired credentials produce a clear message at Start, not a stack trace | **Sonnet 5** | 9 | TODO |
| 47 | Status indicator: connected / reconnecting / degraded / failed | **Sonnet 5** | 9 | TODO |
| 48 | 60-min stability: bounded queues, ledger memory, Tk rendered-line cap with full history retained | **Opus 5** | 8–9 | TODO |
| 49 | Clean-machine install verification | **Sonnet 5** | 11 | TODO |
| 50 | DeepL `context` parameter — previous 2–3 committed lines, unbilled | **Sonnet 5** | 12 | OPTIONAL |
| 51 | Fix problem F: English commits self-concatenate on reformatting variants, slid windows and tail re-sends (`utterance_lifecycle.py` `_merge_lexical`) | **Opus 5** | — | **DONE 2026-08-11, confirmed on a second live run.** Gate approved before implementation. Four changes, all inside `_merge_lexical`: edge-punctuation-insensitive token comparison; exact boundary-run join placed *before* the fuzzy gate; order gate measured over the shorter side; bounded tail-resend splice. Both 0.6 thresholds unchanged. Tests `test_utterance_lifecycle_merge_lexical.py` (25). **Measured on the same 364 real input chunks: repeated 4-word phrases 935 → 9, and 8 of those 9 occur in the reference transcript itself (real speech, not merge duplication).** Live export: 17074 → 6288 chars, longest line 5039 → 1580 |

---

## 9. Notes and session log — append only

Record here: baseline numbers from item 40, anything found that is not
being fixed, every rollback, and every place this file turned out to be
wrong.

| Date | Agent / model | What happened |
|---|---|---|
| 2026-08-10 | — | v5 created. Batches 1–3 complete, Batch 4 items 20b/20c/21 complete. Sprint scope set for 2026-08-24 client delivery. |
| 2026-08-10 | Opus 5 | **Item 38 condition 1 — the proposed input filter was wrong.** The design filtered `provider_events.jsonl` on `metadata.source == "system"`. `metadata.source` is not a provenance field at all — it is the **speaker-source label**, written from `source_snapshot.chosen_source / speaker_label` in `deepgram_client.py`. Histogram across all 16 runs that have the file: `system` 199, `utterance_lifecycle_accept_boundary_proposal` 113, `none` 94, *missing* 52, `mic` 3, `mixed` 1. That filter would have discarded 98 genuine ingress rows (`none`/`mic`/`mixed`). The `none` row flagged in the approval is genuine provider ingress — `speech_final: true`, `confidence: 0.998`, real audio timing 98.93–100.85 s — with speaker label "none". Nothing to drop. |
| 2026-08-10 | Opus 5 | **Correct discriminator, verified across 462 rows / 16 runs: `metadata.raw_deepgram_text` presence (equivalently `confidence is not None`).** Perfectly bimodal — 242 rows have both, 220 have neither, **zero mixed**. The 220 are assembler commit re-emissions: `_publish_final_transcript_segment` also calls `record_raw_deepgram_final`, and the Japanese assembler publishes through it carrying lifecycle metadata. `canonical_finalize.py`'s comment claiming provider_events "can never carry a synthetic row" is true only of the `synthetic_record`/`synthetic_lineage` flags — assembler output that is not flagged synthetic does reach the file. |
| 2026-08-10 | Opus 5 | **Coverage is 6 runs, not 27 or 16.** English sessions record **zero** genuine ingress: only `japanese_final_chunk_stabilizer.py` calls `record_raw_deepgram_final` on true ingress, so an en run's provider_events contains re-emissions only (confirmed: all 10 en runs have 0 ingress rows **and** 0 `stable_commits`). Replay from this input is Japanese-only. The 6: `...160529` (25 ingress), `...155922` (3), `...160130` (11), `...134815` (50), `...155334` (71), `...174516` (82). Excluded 11 runs with no provider_events.jsonl: `_pending`, `...132428`, `...160518`, `...160519`, `...160528`, `...134809`, `...155841`, `...173845`, `...192450`, `...192501`, `...192502`. Excluded 10 more that have the file but no ingress (all en): `...132429`, `...132635`, `...150958`, `...153955`, `...160352`, `...133236`, `...155842`, `...173846`, `...192516`, `...134258`. |
| 2026-08-10 | Opus 5 | **§1's "reproduced at higher volume on 08-08 and 08-09" is half wrong.** Measured stable_commits vs ledger records on every run that has an assembler stream: `...160130` 4/3 (−1), `...160529` 10/9 (−1), `...134815` 29/23 (**−6**), `...155334` 32/26 (**−6**), `...155922` 3/3 (0), `...174516` 36/36 (**0**). 08-08 reproduces at volume as claimed. **08-09 does not reproduce at all** — the only 08-09 run with an assembler stream is clean. There is no 08-09 reproduction run to include in the gate. |
| 2026-08-11 | Opus 5 | **Item 38 gate resolved; harness run. Problem A's mechanism is proven and it is not what §1 said it was.** §1 claimed committed sentences "never reach the canonical ledger." They reach it. The assembler commits two **textually disjoint** sentences under one `canonical_utterance_id`; the ledger keys on that id, so the second commit lands as a revision of the first and the first sentence's words are overwritten. Example, run `...160529`, id `jp-utt-19dbf8832ec0`: commit 1 `ですよ。違いますねでやっぱりこっちにいると日本の行事の…` (47 ch), commit 2 `だろう楽しい雰囲気とかも…` (121 ch) — unrelated text, one ledger record, and commit 1 is absent from `Alpha_output_FINAL.txt`. Totals across the 6 replayable runs: **14 dropped sentences, 505 characters, 14/14 independently confirmed absent from the export.** |
| 2026-08-11 | Opus 5 | **The decisions-vs-ledger delta used throughout §1/§5/§7 and the 2026-08-10 rows below is the wrong metric — retracted, not overwritten.** It over-counts (a genuine `revise` writes a second `stable_commits` row for an id that already has its record; that is not a loss) and under-counts (an overwritten id still has a ledger record, so the loss is invisible to it). Corrected per-run drops: `...155922` **1** (delta said 0), `...160130` **2** (said 1), `...160529` **2** (said 1), `...134815` **3** (said 6), `...155334` **6** (said 6), `...174516` **0** (said 0 — the only genuinely clean run). §1's evidence row, §5's Days 1–2 and Days 3–6 gates, and §7 gate 1 all rewritten. This is the third time on this project that a count-based read of evidence files was contradicted by driving the code — same shape as the item 20b retraction in `CANONICAL_KEY_FIELDS_AUDIT.md` §5b. |
| 2026-08-11 | Opus 5 | **The harness's own first verdict was a false pass — recording it because it nearly shipped.** `_unreached_utterances` matched committed utterances to the ledger on `canonical_utterance_id` only, with a comment arguing text comparison would produce false positives. On that measure all 6 runs scored 0 losses and the tool exited 0, reporting "6 replayed, 6 reproduced." The id *did* reach the ledger — carrying the wrong text. An instrument built to find disappearing records was blind to the exact way they disappear. Fixed to condition 2 as approved (match on text **and** id) plus an independent export cross-check; `_dropped_content` now carries that reasoning in its docstring so it is not re-simplified later. |
| 2026-08-11 | Opus 5 | **Decision 5b resolved: the loss does NOT reproduce under fast-feed.** Replay drops 0 of 14. The segmentation diverges outright — identical ingress produces 13 replayed commit decisions against 29 recorded on `...134815`, 19 vs 32 on `...155334`, 18 vs 36 on `...174516` — because no timer fires mid-stream, so the assembler mints a fresh id where the real run reused one, and the collision never occurs. Per condition 4 the harness was **not** adjusted to make the numbers agree. Timing is therefore a determining factor for problem A; opened item **38b** (real `LanguagePipelineWorker` scheduling) rather than retrofitting it into 38. Item 41 does not need 38b — 38's recorded-side measurement already localises the bug to id minting in the assembler. Items 44/48 do. |
| 2026-08-11 | Opus 5 | **English replay coverage — closed, not reopened.** All 10 English runs record zero genuine-ingress rows (only `japanese_final_chunk_stabilizer.py` calls `record_raw_deepgram_final` on true ingress), so replay is Japanese-only by evidence rather than by choice. The `raw_deepgram_finals.jsonl` fallback adapter stays rejected — a second input adapter is two definitions of "replay input", which §0 rule 2 forbids. Logged in **§6** as a candidate if items 44/48 later need English coverage. |
| 2026-08-11 | Opus 5 | **The baseline holds at 7 names; one 8th name is intermittent. Recording the wrong intermediate conclusion too, because it is instructive.** First full-suite run of the session reported `Ran 354 tests … FAILED (failures=7, errors=2, skipped=2)` and this file was briefly edited to say §3's 5+2 baseline was stale. **That was wrong** — the very next full run, `Ran 364 tests … FAILED (failures=5, errors=2, skipped=2)`, produced exactly the 7 documented names. The difference is `test_task9_report.Issue3RealThreadIntegrationTest.test_inactivity_timeout_fallback_survives_immediate_real_stop_5x`, a real-thread timing test: two subtest iterations failed in run 1, none in run 2, and its module passes 9/9 tests in 3 runs out of 3 alone. It is flaky under full-suite load, not a regression, and not caused by this session (nothing in `tests/` imports `tools/replay_run.py`; the only other edits were markdown). **Lesson for future sessions: one deviating full-suite run is not evidence of a broken baseline — re-run before editing §3.** Test count is now 364 (354 + item 38's 10). The flake itself is not fixed here (§0 rule 3); stabilise or quarantine is a human call. |
| 2026-08-11 | Opus 5 | **`ROADMAP_V5.md` did not exist; it was created, then deleted on the human's call. This file is the only plan.** §0 rule 1, §6, §7 and §8 all pointed at a `ROADMAP_V5.md` that was nowhere in the tree. It was created at repo root (commit `2d34a41`), and the human rejected that: the sprint follows `CLIENT_DELIVERY_SPRINT_v5.md` and nothing else. Removed, and every reference repointed to **§6**, which now *is* the post-delivery backlog — the deferrals it already listed, plus the English-replay entry and the WER-gap measure that had been routed to the missing file. Two authorities for "what is deferred" is the same mistake §0 rule 2 forbids for code; one list, in this file. |
| 2026-08-10 | Opus 5 | **Item 38 condition 2 — the host path diverges from production; reporting before writing, as instructed.** Production: `stabilizer.ingest()` → `assembler.ingest()` → assembler posts a *deferred* flush via `LanguagePipelineWorker.schedule_flush(assembler, due_mono, generation, reason)`, executed by a background thread against wall-clock `due_mono`. `JapaneseTestHost` (test_task2c) deliberately bypasses that: its own docstring says timeout scenarios use "the assembler's synchronous `try_execute_continuity_hold` entry point **instead of real timers**", and tests call `assembler.flush(...)` by hand. So *when* a flush happens — which decides what the assembler batches into one commit — is production-timed but harness-manual. Whether problem A survives that change is unknown and is exactly what condition 4 says to report rather than tune around. |
| 2026-08-11 | Opus 5 | **Item 38b done. ~~"Timing is therefore a determining factor for problem A"~~ (2026-08-11 row above) is retracted — it explained the decision-*count* divergence, not the content-loss mechanism, and those turned out to be two different things.** Built `replay_events_real_timer` in `tools/replay_run.py` (`--real-timer`): starts the real `LanguagePipelineWorker` singleton via `start_language_pipeline_worker()`/`stop_and_join()` (the same pair Start/Stop call in production) and sleeps each row's real recorded gap (`metadata.timestamp` deltas) instead of feeding back-to-back, so the deferred flush fires against real wall-clock time. Ran all 6 replayable runs at real speed (span 64–300s each; run in parallel background processes, ~300s wall total instead of the ~956s serial sum). Result, decisions recorded→replayed→(fast-feed for comparison): `...155922` 3→3 (fast-feed 3), `...160130` 4→4 (4), `...160529` 10→10 (8), `...134815` 29→29 (13), `...155334` 32→33 (19), `...174516` 36→35 (18). **Decision counts now match or land within 1 on every run — real timing fully explains the segmentation divergence.** Content loss: recorded→replayed drops `...155922` 1→0, `...160130` 2→0, `...160529` 2→0, `...134815` 3→0, `...155334` 6→0, `...174516` 0→0. **Still 0 of 14 reproduced.** Timing was necessary to explain *how many* commits happen, not *why two disjoint ones share an id*. |
| 2026-08-11 | Opus 5 | **Incidental to building 38b, not a drive-by fix (§0 rule 3) — logged for item 41, not implemented.** Read `japanese_sentence_assembler.py`'s id-mint path end to end while diagnosing why 38b still doesn't reproduce the loss. `update_previous_requested` (line 3635) is computed once, from boundary-stabilizer signals, *before* `decide_stable_revision_action` runs. That call (line 3673) can return `final_revision_action="append"` — a considered verdict that the candidate is a new, disjoint sentence — and when it does, lines 3749–3756 correctly reset `stable_layer_update_previous`/`post_update_previous` and clear `metadata["boundary_should_revise"]`. **`update_previous_requested` itself is never reassigned.** The id-mint gate at line ~3912 (`proposed_action = "revise_previous" if update_previous_requested else "commit_new"`) reads that stale, pre-decision variable — not `final_revision_action`, the function that was specifically hardened by items 10/11/12/19/20c to tell a revision from a new sentence. Cross-checked against real evidence, not left as a code-reading theory: parsed `logs/japanese_accuracy.log`'s `STABLE_REVISION_DECISION` events (pipe-delimited `timestamp \| {json}`, not plain JSONL) on all 6 runs and matched `update_previous_requested == True and final_action == "append"` against the actual id-reuse events in each run's `stable_commits.jsonl`. **12 of 13 id-reuse events match exactly**; the one miss (`...160130`) is plausibly a text-normalisation artifact in the comparison script (a business-glossary correction shifted the text between the two logs by a few characters), not a predicate failure — not chased further, out of this item's scope. Two structural explanations for why real-timer replay still doesn't reproduce this were checked and ruled out: the interim-Deepgram-stream hypothesis (`japanese_boundary_stabilizer.py` has no `interim`/`is_final` reference) and the bypassed-component hypothesis (`get_boundary_stabilizer()` is called from inside the assembler's own `_ingest_locked` chain at line 2299, which replay does exercise). **This is a lead for item 41, not a proof** — unresolved: why the boundary stabilizer disagrees with `decide_stable_revision_action` in the first place, whether the fix belongs at the read site or the write site, and why replay still can't reproduce it despite having the mechanism. Item 41's description in §5 rewritten to start here instead of from scratch. |
| 2026-08-11 | Opus 5 | **Item 39 done, run against all 27 recorded runs.** Gate 1 reuses `replay_run.py`'s own verdict function rather than re-implementing it, so the two tools cannot silently drift apart on what a loss is — confirmed, its per-run counts on the 6 replayable runs are exactly item 38's (1, 2, 2, 3, 6, 0). New findings, from gates that item 38 doesn't run: **gate 2** found an *actual* duplicate line on run `...155922` — `Speaker: 。` (a degenerate near-empty commit) appears twice in `Alpha_output_FINAL.txt`, the only exact-duplicate line across all 27 runs. **Gate 3** found one isolated, credible miss — run `...134815` has 22 of 23 canonical records translated; `jp-utt-5b8f6f0a9300` (`あっ意外とそんなに疲れたっていう感じじゃなくて…`) has zero `translation_jobs.jsonl` rows referencing it at all, not a rejected job, just none submitted. |
| 2026-08-11 | Opus 5 | **Gate 3's English-run failures are flagged UNVERIFIED, on purpose — this is the exact mistake the verification rule in CLAUDE.md exists to prevent, caught before it was asserted as a bug.** Two English runs (`...133236`, `...155842`) score gate 3 as a near-total failure (39 of 44 records, 1 of a smaller set) — but `translation_jobs.jsonl` is **completely empty** for `...133236` (0 rows against 44 canonical commits), not partially populated. A wholesale-empty stream is a different shape from `...134815`'s isolated single miss and reads as either a genuine session-level translation gap on these two runs, or gate 3 checking the wrong evidence file for English sessions (v5 §1 already claims working translation counts elsewhere — "translation jobs 31/31 and 12/12" — so the mechanism evidently works in *some* runs). Not resolved here: whether English sessions are expected to populate `translation_jobs.jsonl` the same way Japanese ones do is unknown to this session and needs a direct answer, not an inference from one field's absence, before gate 3's English-run verdicts are trusted. `score_run.py`'s own output does not silently assert PASS or FAIL past what it measured; this note carries the same caution into the doc. |
| 2026-08-11 | Opus 5 | **Item 40 investigated and blocked — not a scope call, a physical one. Two shortcuts were checked and both are dead ends, not just "not tried."** (1) `Alpha_Benchmark_References/current_japanese_actual.txt` + `current_english_actual.txt` already exist, with a README describing exactly item 40's protocol ("place verbatim spoken references here before scoring... same spoken order as the audio"), and `analyze_alpha_vs_reference.py` already consumes them via `--reference`/`--run-folder`. **Unusable as-is**: both files date 2026-07-24, over two weeks before this sprint's earliest run (`...20260807`); content confirmed mismatched by reading it directly — the Japanese file is a formal リンクブリッジ project-status meeting, none of the 6 replayable runs are (they're casual conversations about sleep habits, Hinamatsuri, dolls); the English file is an unrelated real-sounding internal meeting (names: Alan, Ellen, Phil, Thomas, Neil, Thiago, Bill, Juliana — worth the next session's caution about reproducing this verbatim beyond what scoring needs, it reads as a genuine third-party recording). (2) Checked whether any of the 27 runs' own `audio_temp/{mic,mixed,system}_audio/` captures could be transcribed after the fact instead of a fresh recording. **Dead end**: `AUDIO_TEMP_RETENTION_HOURS: 2` (RUN_MANIFEST) already expired every run's actual audio — `audio_temp_summary.txt` survives (e.g. `...174516`: 36 chunks, 57.8 MB, `expires_at: 2026-08-09T19:38:55`) but the chunk files themselves are gone (`find .../mic_audio/ -type f` → 0, checked on 3 runs). No audio exists anywhere in this repo for anything item 40 could transcribe. **What's ready for whoever runs the live session**: the existing `Alpha_Benchmark_References/` convention and `analyze_alpha_vs_reference.py` are the established tooling — reuse them, do not build a third. `score_run.py --reference` (item 39) already reads the same plain-text format `analyze_alpha_vs_reference.py`'s `_reference_lines_with_hints` expects, confirmed by reading both. Next step is one live 10-min-ja + 10-min-en session per §4's protocol, with the reference hand-written from what was actually spoken (by the human, during or immediately after, while it's still verifiable) — not delegable to this or any agent session. |
| 2026-08-11 | Opus 5 | **The blocker resolved within the hour — human ran both live sessions** (`...20260811-175628` ja, `...20260811-182940` en, both `final_status: completed`) **and supplied a reference the smart way: play a public YouTube video with its own accurate transcript through Alpha, rather than hand-typing a reference live.** Ja source: "Japanese with Yuka" N1 vocabulary lesson. En source: a Mr. Olympia bodybuilding-technique interview. Both are public educational/entertainment content — the privacy caution logged in the row above was about the stale 07-24 reference, which stays archived and unused; it does not apply here. References archived (`Alpha_Benchmark_References/archive_20260724/`) and replaced with the new ones. Japanese scored clean against reference via `analyze_alpha_vs_reference.py`: `cumulative_duplicate_count: 0`, `unaligned_alpha_line_ratio: 0.31` — a usable baseline, item 40 genuinely done for the Japanese half. |
| 2026-08-11 | Opus 5 | **English did not score cleanly — because English output turned out to be badly broken, independent of anything this sprint had looked at. New problem F, see §1.** Reading `Alpha_output_FINAL.txt` for `...182940` directly (not just the scorer's summary numbers) showed every longer utterance as a wall of comma-joined, self-repeating text — e.g. one 5039-character line built from ~112 fragments for what should be a few sentences. Root-caused, not assumed: `utterance_lifecycle.py`'s `_merge_lexical()` (~L91-131) is the function that decides whether two successive texts for the same active utterance are "the same thing, take the newer one" or "two separate adjacent chunks, glue them together." Its containment checks (`curr.startswith(prev)`, `prev in curr`, etc.) require near-literal substring matches. When Deepgram sends a reformatted variant of the same growing hypothesis — smart-format vs verbatim numerals ("50 percent" → "50%"), title casing ("mister" → "Mr.") — none of the containment checks fire, and it falls to the last-resort branch meant for genuinely separate phrases, gluing them with a comma. Confirmed against the real function, not by reading alone: `_merge_lexical("So I'm mister Olympia.", "So I'm Mr. Olympia,")` returns `"So I'm mister Olympia. So I'm Mr. Olympia,"` — concatenated, using text pulled directly from the corrupted output. `_apply_active_update_locked()` (~L1439-1544) compounds it: it correctly classifies some of these as `REPLACE_ACTIVE` (its own logic recognizes intent to replace, not merge) but writes `active.text = merged` unconditionally at L1544 regardless of which decision was chosen, so even the correctly-classified cases still get the concatenated text. Every English commit goes through this path — `should_use_utterance_lifecycle()` returns true for any non-Japanese session — so it reaches the canonical ledger, the translation input (explaining the garbled Japanese translation the human also supplied), and the client-facing export identically. |
| 2026-08-11 | Opus 5 | **Confirmed pre-existing, not introduced by anything this sprint touched, and confirmed Japanese-unaffected.** Run `...20260808-133236` (English, already read once for item 39's gate 3) shows the identical pattern — this has been silently corrupting every English session's output the whole time; nothing in this sprint's tooling had looked at English output *content* before today (`replay_run.py`/`score_run.py` gate 1 both skip English entirely — zero genuine ingress in `provider_events.jsonl` by design — so neither ever read an English export closely). Japanese confirmed clean by direct reading of `...175628`'s export and because Japanese never reaches this function (`should_use_japanese_final_stabilizer` routes it to `japanese_sentence_assembler.py` instead). Severity, measured directly rather than trusting `analyze_alpha_vs_reference.py`'s summary (its `cumulative_duplicate_count`/`alpha_output_cumulative_duplicate_suspected` read `0`/`False` on this run despite the corruption — its detector is tuned for a different duplication shape and has a blind spot here, worth someone's attention separately): on `...182940`, 5 of 54 export lines are over 400 characters, and those 5 lines carry **85.9% of the export's total characters**. A client reading this transcript sees mostly unreadable repeated text, not occasional glitches. Not fixed — outside this item's scope and this is a shared function underlying every English commit, exactly the kind of change that needs the gate process; opened as item 51, `[gate]`, recommended (not decided) to jump ahead of items 41-43 given it's more severe than problem A for any English-source client use. |
| 2026-08-11 | Opus 5 | **Item 51 — half of the previous row's root cause is RETRACTED before it misled anyone.** That row said `_apply_active_update_locked()` "correctly classifies some of these as `REPLACE_ACTIVE` but writes the concatenated text regardless", implying two independent defects. **It does not, and there are not two.** That function *derives* its decision from `_merge_lexical`'s return value — `if _norm_text(merged) != curr_n and != prev_n: decision = EXTEND_ACTIVE`. Driving the real function on both known corruption pairs returns `EXTEND_ACTIVE`, not `REPLACE_ACTIVE`, so `active.text = merged` is *consistent* with the decision rather than contradicting it. One root cause, entirely inside `_merge_lexical`; fixing it corrects the decision label as a side effect and `_apply_active_update_locked` needed no edit. Same failure shape as the item 20b retraction — a plausible read of the code that the code itself contradicts once driven. |
| 2026-08-11 | Opus 5 | **Item 51 done. Worked from the real recorded input, not from the UI transcript.** English records no genuine provider ingress (item 38's finding), so `provider_events.jsonl` holds only the *already-merged* corrupted text — useless as an input fixture. Recovered the true 9-chunk input sequence for run `...182940` utterance `U-1` by decomposing the recorded corrupted line and **verifying the reconstruction by re-merging it through the pre-fix function and getting the recorded 472-character line back byte-for-byte.** That sequence exposed **two** failure modes, not one: **(a)** reformatted re-sends of the same span (`"So I'm mister Olympia."` / `"So I'm Mr. Olympia,"`) — the word-overlap gate that exists to catch these compared tokens with punctuation glued on (`_norm_text` only lowercases), so `"olympia."` ≠ `"olympia,"` and the score fell under 0.6; and **(b)** slid windows repeating the previous chunk's tail (`"…the best bodybuilder in"` + `"bodybuilder in the world…"`), which the concatenation branch duplicated despite its own comment claiming it handles "non-overlapping lexical spans". Fixing only (a) leaves (b) visible, so both were in scope — same defect class, same function, not scope creep. |
| 2026-08-11 | Opus 5 | **Item 51 — the fix nearly introduced silent content loss, caught by driving the real sequence rather than trusting the unit pairs.** First working version put the new boundary-run join *after* the similarity gate. On the real sequence that scored a slid window carrying new tail content as "same utterance re-said", and the gate returns whichever side is *longer* — which is the accumulator, because it still holds the earlier text — so `"lifting weights. Come on."` was silently dropped. Output looked clean (101 chars, no duplication) and was **wrong**. Reordered so the exact boundary-run check runs first: literal evidence outranks a fuzzy score. Pinned by `test_slid_window_with_new_tail_keeps_the_tail`. Silent loss is strictly worse than visible duplication, and the only reason it surfaced is that the end-to-end fixture is real recorded input with a known-correct answer. Third change, also needed: the order gate used `SequenceMatcher.ratio()`, which is symmetric and so penalises length difference — a 4-token chunk against the 8-token growing version of itself scored 0.50 and was rejected despite 3 of its 4 tokens matching in order. Switched to in-order matched tokens over the *shorter* side, the same asymmetric convention the set-overlap gate immediately above it already used; the 0.6 threshold itself is untouched. |
| 2026-08-11 | Opus 5 | **Item 51 verification, and what is still not verified.** Method step 5 done properly: the 20 new tests were run against pre-fix code — 8 of 8 bug-detecting assertions failed, reproducing the recorded corruption exactly (472 chars, "olympia" ×4, "bodybuilder" ×6), while the disjoint-chunk negative control passed *both* before and after, proving that control is not the fix doing the work. Post-fix the verified real sequence folds to **127 characters, each phrase once, matching the reference transcript**. Full suite **405 tests, 5 failures + 2 errors + 2 skipped, the same 7 names** — no regression, and `test_bugfix_spec_regression.py`'s existing concatenation expectation still passes untouched. **Not yet verified: the export-wide number.** The 85.9%-of-characters figure came from a live run, and re-measuring it needs one more live English session — this session cannot run the app. That is the one open confirmation on item 51; everything measurable without the app is measured. |
| 2026-08-11 | Opus 5 | **The human ran the confirming live English session (`...20260811-212531`) — the fix held on the first utterance and exposed a third shape of problem F that the first three changes did not reach.** Export-wide: 17074 → 6288 characters, longest line 5039 → 1580, and the first utterance came out clean and matching the reference. But 5 lines were still over 400 characters. Cause is a **tail re-send**: once an utterance has accumulated, Deepgram re-sends only its most recent span, revised and extended — `"…You lower it. So even though you failed, positively, we're"` followed by `"So even though you failed positively, we can do a couple of extra reps"`. Whole-against-whole comparison cannot see it (the shared run is a small fraction of a long accumulator, so the similarity gate scores 0.46) and `_overlap_join` cannot either (the run is not a clean suffix of `prev` — `prev` ends in a partial `"we're"` that `curr` revises away). Both fell through to concatenation. Added `_tail_resend_splice`: find the longest run of `curr`'s leading tokens occurring contiguously in `prev`, latest occurrence first, and keep everything before it. Placed **after** the similarity gate, because it is the only step in this function that can discard text. |
| 2026-08-11 | Opus 5 | **Item 51 final measurement — like-for-like, both versions folding the same 364 real recorded input chunks from run `...212531`.** Original pre-fix code: 13401 characters, **935** repeated 4-word phrases, 1.29× the reference length. After all four changes: 4589 characters, **9** repeated phrases, 0.44× the reference. **8 of those 9 remaining repeats appear in the reference transcript itself** — the speaker genuinely said them — leaving one artifact that is a Deepgram mis-transcription (`"just showed the stress"`), not a merge defect. The splice's safety property is that it can never discard more than `max_orphan` (4) tokens regardless of accumulator length, which is what bounds the risk of trading duplication for silent loss; `test_splice_can_never_discard_more_than_the_orphan_bound` pins it. Suite **410 tests, 5 failures + 2 errors + 2 skipped, same 7 names**. Problem F is closed. |
| 2026-08-11 | Opus 5 | **Item 41 — investigated, and the honest verdict is NOT PROVEN. Full write-up in `PROBLEM_A_ROOT_CAUSE.md`** (repo root, because `troubleshooting/` is gitignored — the item 41 prompt's suggested path could not be committed). Phase 1: 10/10/9 reproduces, but **zero ids are in `stable_commits` and absent from the ledger** — the "missing record" is a *duplicate id*, not a missing one. Phase 2 established an exceptionless rule across all 6 runs: a sentence is lost **iff** its `canonical_utterance_id` is carried by more than one stable row **and** those rows' texts are disjoint rather than nested; the earliest row is the one lost. Phase 4's strongest evidence is a natural control group — of the 14 id-reuse events, the 9 with disjoint text all had `update_previous_requested=True`, the 3 with nested text all had it `False`, and `decide_stable_revision_action` returned `final_action="append"` (reason `speaker_boundary_forced_new_line`) in **every single case**. The revision authority always got it right; the id-mint gate ignored it. |
| 2026-08-11 | Opus 5 | **Item 41 — correction to item 38's own measurement: the true loss count is 10, not 14.** `tools/replay_run.py`'s `_dropped_content` normalises whitespace only, so it cannot see two legitimate rewrites and counted both as losses: (1) a cleanup step stripping a leading `。` (`jp-utt-f8bf3bbf9fb2`, `...160529`), and (2) a business-accuracy correction inserting `て` (`jp-utt-453c4fd9c80f`, `...155334`). Both texts **are** in the export; confirmed by reading it directly. Per-run true losses: `...155922` 0, `...160130` 1, `...160529` 1, `...134815` 3, `...155334` 5, `...174516` 0. **Not fixed — repairing the detector means editing a `.py`, which item 41 forbids.** Any future "problem A is fixed" claim measured with that tool will be wrong by ~2 in 12. |
| 2026-08-11 | Opus 5 | **Item 41 — Phase 5 FAILED, and that is the reportable outcome, not something to paper over.** Driving the real assembler headlessly with the two real disjoint sentences from `...160529`: same speaker reuses the id but **loses nothing** (the ledger retains both); a speaker change mints **two distinct ids** and also loses nothing. Neither reproduces the recorded combination of speaker-boundary decision **and** id reuse **and** loss. **This is the second independent harness to fail** — item 38b's real-timer replay also reproduced 0 of the losses. The mechanism depends on live state neither harness reconstructs (candidates: boundary-stabilizer internal state, `_last_stable_commit` lineage, when `_current_canonical_utterance_id` is reset). **Consequence: item 42 cannot satisfy §4 step 5 yet — there is nothing to make fail.** Closing the gap needs one instrumented live Japanese session logging `update_previous_requested` and `proposed_action` at the gate; that is a `.py` edit and needs human approval first. |
| 2026-08-11 | Opus 5 | **Item 41 — found and not fixed (§0 rule 3).** (a) `stage_manifest.json` and `export_coverage_report.json` report the **same** metric name `export_coverage_ratio` as **1.0** and **0.9** for run `...160529`. (b) The `coverage_ratio` gate passes at 1.0 on that run while four sibling fields *in the same file* — `source_commit_coverage_ratio`, `lineage_coverage_ratio`, `text_coverage_ratio`, `export_coverage_ratio` — all read **0.9**, i.e. 9 of 10, precisely the lost commit. The evidence of problem A was recorded next to the gate that declared the run clean. (c) Item 20b's `revision_target_id` guard is keyed off `final_revision_action` *after* that variable is overwritten by a value derived from the stale gate, so 20b's fix is silently undermined on this path — item 42 must re-verify 20b's regression test still means what it claims. |

| 2026-08-12 | Opus 5 | **Item 41 closed — root cause PROVEN, upgrading the 2026-08-11 "strongly indicated, not proven" verdict.** That verdict was honest when written: Phase 5 had genuinely failed. Two things closed it, neither requiring the live-session instrumentation that was proposed and is now **obsolete**. **(1) Wrong entry point.** The failed attempt drove `_route_stable_publish`, which merges a short follow-up into the held buffer *before* publishing, so the revise it produced was non-destructive — the new text contained the old, which is exactly the harmless class that accounts for 3 of the recorded revises losing nothing. Driving `_publish_sentence` — one layer lower, where the defect lives, with every function below it still production code including the real ledger — reproduces the loss immediately. **(2) The missing structural fact.** `previous_record` is built with `line_id`/`text`/`source_raw_event_ids`/`start_time`/`end_time`/`utterance_id`/`segment_id` and **no `speaker`**, though `_last_stable_commit` carries one. `previous_speaker` therefore reads `None`, `speakers_confirmed_same` is fail-closed on `None`, and that guard sits before every other rule — so `decide_stable_revision_action` returns `append` on every non-first commit (**105 of 111** measured across the corpus). **Rules A–F, `REVISION_CONTENT_LOSS_GUARD_ENABLED`, `REVISION_TERMINAL_SENTENCE_GUARD_ENABLED` and `_unique_content_lost` have never executed on the Japanese path.** The engine always says append; the stale flag always overrides it; the loss is systematic, not occasional. |
| 2026-08-12 | Opus 5 | **Item 41 Phase 5 fixture: `Alpha_Live_Translator/tools/reproduce_problem_a.py`.** Controlled, single independent variable — `boundary_should_revise` absent gives **2 ledger records, 2 distinct ids, both sentences intact**; set to `True` gives **1 record, 1 id, `applied_action: revise`, and the first sentence destroyed**. Nothing else differs. The control is asserted too: the fixture exits `2` with `FIXTURE INVALID` if the control ever stops being clean, so a loss can never be blamed on the harness. Deliberately **not** under `tests/` — it is designed to fail while the bug exists, and putting it in the discovered suite would move the 410 / 5F + 2E + 2S baseline. It exits **1** today, for the proven reason, which is sprint §4 step 5 already satisfied for item 42 before that item starts. Baseline re-confirmed unchanged after adding it. |
| 2026-08-12 | Opus 5 | **Found while proving item 41, logged not fixed (§0 rule 3) — a trap for item 42.** Adding the missing `speaker` key looks like the clean fix and is the most dangerous of the three options. It would let `speakers_confirmed_same` return `True` for the first time in this corpus, making **Rules A–F reachable for the first time** — a large block of never-exercised decision logic switched on during a data-loss fix, which is precisely how this ledger already records six cases of one fix unmasking the next. There is also a known hole waiting behind it: `_unique_content_lost` returns `False` whenever `len(prev_n) < 8`, so `_content_loss_risk` clears and a **short** previous record can be legally overwritten by disjoint text under Rule C — converting a systematic loss into an intermittent one that is harder to detect. If that option is taken it must be its own separately-tested item, not folded into 42. Full item 42 brief: `PROBLEM_A_ROOT_CAUSE.md` §8. |
| 2026-08-12 | Opus 5 | **Item 41's falsification workflow: 1 of 8 agents survived.** The trace agent completed; all six falsification agents and the adjudicator died on a session limit. Its trace was treated as a **lead, not a finding** — every load-bearing claim above was re-verified in-session against the source before being written down (the missing `speaker` key, the fail-closed guard, the guard's position ahead of all rules, and the destructive `target["final_text"] = text` in `_revise_record_unlocked`). Phase 4's candidate verdicts in the proof doc §4 were reached independently of the workflow, from direct evidence, and the workflow did not contradict them. |

| 2026-08-12 | Opus 5 | **Item 42 done — problem A fixed, and the obvious fix was NOT the right one.** Pointing the id-mint gate at `final_revision_action` (the revision authority's verdict) is the fix item 41 §5 recommended, and driving it proved it **insufficient on its own**: because `previous_record` carries no `speaker` key the authority is fail-closed to `append` on every non-first commit, so that gate would be *always* `commit_new` — which removes the data loss by converting every genuine extension into a second, near-duplicate line. That is problem F's failure mode on the Japanese side. Measured before writing the fix, with a two-property harness driving the real `_publish_sentence`: **P1 (no loss) FAILED and P2 (no duplication) PASSED** pre-fix, so a naive fix would simply have swapped which property was broken. |
| 2026-08-12 | Opus 5 | **The fix: the revise must be non-destructive by content.** New `_revision_is_non_destructive(previous_text, candidate_text)` — a revise is allowed only when the candidate still contains the previously committed text (whitespace-insensitive; punctuation deliberately **not** stripped, because `。`/`、` carry sentence-boundary meaning and ignoring them would let a genuine rewrite pass as an extension). This is exactly the discriminator item 41 measured as exceptionless across the corpus: lost **iff** the id is reused **and** the texts are mutually disjoint. A blocked revise now logs `DESTRUCTIVE_REVISE_BLOCKED_NEW_UTTERANCE_ID` with both texts and mints a fresh id — the sprint's "lands in the ledger or fails loudly, never silently dropped" requirement, satisfied without an architecture change. Post-fix both properties pass; `tools/reproduce_problem_a.py` flipped 1 → 0. |
| 2026-08-12 | Opus 5 | **§0 rule 2 (never two authorities) — how it was satisfied.** Item 41 §5.1 warned that fixing the gate's *read* without addressing the later `final_revision_action = … if proposed_action == …` overwrite would leave two disagreeing notions of "was this a revision". After this change the gate is the **only** place the Japanese path decides append-vs-revise, and that later line is now just a relabel of that single decision — it cannot disagree with the gate, whereas before it claimed to carry the authority's verdict while the gate was driven by a stale pre-decision flag. The line is kept (item 20b's `revision_target_id` guard consumes it) and carries a comment saying exactly that. Item 20b's own regression test still passes untouched. |
| 2026-08-12 | Opus 5 | **Method step 5 done properly, and what the evidence does and does not cover.** The 11 new tests were run against reverted code: the two loss tests failed with the exact recorded signature (first sentence destroyed, 1 record where 2 were expected) while `GenuineRevisionStillWorksTest` **passed on both sides**, proving that half was pre-existing behaviour the fix preserves rather than something the fix invented. Suite **421 tests, 5 failures + 2 errors + 2 skipped, the same 7 names**. **`tools/replay_run.py` cannot certify this fix** — its replayed side was already 0 before the change (item 38b), so it does not discriminate; the fixture and the regression tests are the evidence. Also surfaced while re-measuring: the 2026-08-11 Japanese live run `...20260811-175628` shows **6 recorded drops**, confirming problem A was still occurring live right up to this fix (subject to `_dropped_content`'s known ~2-in-12 over-report). |

| 2026-08-12 | Opus 5 | **Item 43 done — and problem B's headline number was wrong.** §1 said "2 of 2 quarantine events ever recorded were misclassified real Japanese speech". Re-measured across every recorded run: **9 quarantine events, 8 drops (4 distinct fragments), 1 release.** The distinct dropped texts are `寝れた、幸せ、` ("slept, happy"), `。忘れちゃうし、` ("and I forget"), `最近また` ("recently again") — all real speech — and `、`, a bare comma, which is genuine noise. So **3 of 4, not 2 of 2**. §1 row B corrected. The direction of the original claim holds and item 34's supersession stands: 4 observations is still far too small to tune a noise threshold on, which is exactly why item 43 makes the path non-destructive instead of trying to classify better. |
| 2026-08-12 | Opus 5 | **Item 43's real constraint was the lock, not the policy.** `_drop_expired_quarantine_locked` is invoked by `language_pipeline_worker._run_quarantine_drop` **with the assembler lock already held**, and the commit path re-acquires it — so committing inline would deadlock. Expiry therefore queues to `_quarantine_recovery_pending` and `_drain_quarantine_recovery` commits outside the lock, the same collect-inside/commit-outside shape `flush_quarantine_on_stop` already used. The drain is hooked at `_ingest_safe`'s entry (the first unlocked point after the worker's timer) and at stop, and is re-entrancy-guarded because it calls back into `_ingest_safe`. Stop also drains first, so a fragment that expired seconds before Stop is not stranded in the queue. |
| 2026-08-12 | Opus 5 | **A hazard found while implementing item 43, and the one deliberate exception to "never drop".** Committing text with no word characters would fail `accept_boundary_proposal` with `"empty_text"`, and the assembler converts **any** proposal failure into `_assembler_commit_gate_failed`, which is cleared only in `reset()` — so every remaining commit in the session is silently dropped. Feeding the corpus's bare `、` into the commit path would therefore have traded a one-fragment loss for a whole-session loss. Fragments with no word characters are consequently logged `NOISE_FRAGMENT_NOT_COMMITTABLE` rather than committed — explicit and distinct from the old silent `NOISE_FRAGMENT_DROPPED`, so it stays visible in evidence. `test_punctuation_only_does_not_trip_the_session_killing_commit_gate` pins it. |
| 2026-08-12 | Opus 5 | **Item 43 — what recovery does and does not reach, stated honestly.** Verified on the realistic path (quarantine expires mid-session, more speech follows): the recovered fragment reaches the canonical ledger, merged into the following sentence. Verified on the stop path: the fragment now leaves quarantine into the commit path instead of vanishing. **But a recovered fragment that is an incomplete tail at Stop is then suppressed by the pre-existing stop-tail path** (`suppress_early` → `STOP_TAIL_CANDIDATE_SUPPRESSED`), so it still will not appear in that one case. That is a separate, documented behaviour outside item 43's scope — logged here rather than fixed, per §0 rule 3. Method step 5 done per site: reverting the expiry recovery failed 3 tests, reverting the stop-flush recovery failed 1, each with the original signature. Suite **429 tests, 5F + 2E + 2S, same 7 names**. |

| 2026-08-12 | Opus 5 | **Items 22, 23 and 33s done — problem C closed.** Both defects were reproduced against the real `UtteranceLifecycleOwner` before any code changed. Item 22: two speakers with **no identified label** produced `'the first speaker said this, a totally different remark'` as a single line, because `_compatible_with_active_locked` compared `int(active.speaker or 1) != int(speaker or 1)` and every unknown coerced to `1`. Item 23: a genuine speaker change produced `'speaker one is talking here, speaker two interrupts now'` as a single line, because Case B forced a new utterance **only when the active one was already committed** — and a held final chunk is uncommitted by definition. Case C had always gated correctly; Case B is now aligned to it. |
| 2026-08-12 | Opus 5 | **22 and 23 interlock — neither is sufficient alone, which the revert proof surfaced.** Reverting item 22 alone failed 3 tests; reverting item 23 alone failed **4** — the same 3 plus its own. Item 22 is what makes `same_active` False for an unknown speaker; item 23 is what makes Case B actually act on `same_active`. With either reverted the unknown-speaker turns merge again. Worth knowing before anyone reverts one of them in isolation believing it is independent. |
| 2026-08-12 | Opus 5 | **`speakers_confirmed_same` alone would not have fixed item 22.** The ledger's wording is "use `speakers_confirmed_same()`", but that primitive is fail-closed only on `None` — it reads `0 == 0` as a *confirmed* match, and this codebase uses `0`, `None` and `""` interchangeably for "no speaker". So `_known_speaker` normalises every unknown form to `None` first, and that normalisation is load-bearing, not tidying: `test_speaker_zero_is_treated_as_unknown_not_as_a_match` fails without it. |
| 2026-08-12 | Opus 5 | **Item 33s is a latent-trap removal, not an observable fix — stated plainly so nobody claims a behaviour change that is not there.** `_resolve_output_speaker` stabilises the speaker *for display* when evidence is weak, and one of its own lock reasons is `_looks_like_speaker_continuation_tail` — "this text reads like a continuation". Feeding its output into `decide_stable_revision_action` closed a loop where the relabel manufactured the same-speaker agreement the guard exists to test. `_publish_sentence` now captures the raw provider label as `boundary_speaker` before the relabel and passes that instead. **This changes nothing today**, because item 41 proved the guard is already fail-closed (`previous_record` carries no `speaker` key), so the comparison never reaches a match either way. It matters the moment anyone adds that key — which item 42's notes already flag as the dangerous option. |
| 2026-08-12 | Opus 5 | **Anti-fragmentation checked, not assumed.** These guards make merging strictly harder, and over-tightening would trade a cross-speaker merge for a transcript chopped into fragments — a different visible-on-stage defect. `SameSpeakerStillMergesTest` pins that one speaker's continuous speech still merges across 3 chunks, and `tools/replay_run.py --all` shows replayed decision counts on all 7 runs **identical** to before the change (3/4/8/13/19/18/57). Problem A's fixture still exits 0. Suite **442 tests, 5F + 2E + 2S, same 7 names**. |

---

## 10. Model selection reference

| Work | Model | How |
|---|---|---|
| Ambiguous root-cause investigation, long multi-file work (41, 42) | Claude Fable 5 | `/model fable` then `/effort xhigh`. Describe the outcome, not the steps — it investigates before acting and verifies itself, so verification reminders are wasted tokens |
| Architecture, concurrency, threshold judgment | Opus 5 | `claude --model opus`; add `ultrathink` in the prompt for one-off deeper reasoning |
| Template fixes, tooling, tests, packaging | Sonnet 5 | `claude --model sonnet` |
| Mixed planning + execution | `opusplan` | Opus while planning, Sonnet for execution |

`/model` opens the picker, `/status` shows what is active, and `--model`
applies per session — so a Fable session and a Sonnet session can run in
two terminals at the same time.
Reference: https://code.claude.com/docs/en/model-config
