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
   (`ROADMAP_V5.md`). Attempting them inside 2 weeks is the single most
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

**What is still genuinely broken, in priority order:**

| | Problem | Evidence |
|---|---|---|
| **A** | Japanese sentences committed by the assembler never reach the canonical ledger — they exist in `stable_commits.jsonl` and are absent from both the ledger and the final export | `Bug_Report.md` §4.1. Run `...20260807-160529`: 10 `commit_new` decisions, 10 rows, **9** ledger records. Reproduced at higher volume on 08-08 and 08-09 |
| **B** | Real speech quarantined as `noise_fragment` and never recovered | `Bug_Report.md` §4.2. 2 of 2 quarantine events ever recorded were misclassified real Japanese speech; 0 recovered |
| **C** | Two speakers' turns can merge into one line | v4 items 22, 23, 33 / audit §2.2, §2.3, §3.1 |
| **D** | No behaviour defined for network drop, DeepL quota exhaustion, device change, or invalid credentials | Not covered by any of v4's 37 items |
| **E** | Verification depends on a human running live sessions | Why v4 items 10/11 sat unverified across five sessions |

A and B are content loss. C is visible on stage. D is what breaks on a
client's machine when you are not there. E is why everything takes days
instead of minutes — **fix E first**.

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

**Baseline:** 354 tests, 5 failures + 2 errors + 2 skipped. Same 7 names
every run. Any change to that set means you broke something.

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

- **38** replay harness — **Opus 5**
- **39** run scorer — **Sonnet 5**
- **40** reference corpus + baseline numbers — **Sonnet 5**

**Gate:** ~~all 27 recorded runs replay~~ — **corrected 2026-08-10, see §9.**
Only **6** runs are replayable from `provider_events.jsonl` at all; the
other 21 carry no genuine provider ingress (10 have the file but only
post-lifecycle re-emissions, 11 lack the file). All 6 are Japanese —
English sessions record no raw ingress by design.

Gate is therefore a content requirement, not a count: **all 6 runs with
genuine provider ingress replay, and those 6 must include
`...20260807-160529` and the two 08-08 higher-volume reproduction runs
(`...20260808-134815`, `...20260808-155334`).** All three are present.
Baseline numbers written into §9.

### Days 3–6 (Aug 12–15) · Content integrity — items 41, 42, 43, 22, 23, 33s

The bugs that lose the client's words.

- **41** prove problem A's root cause against the fixture — **Fable 5,
  `/effort xhigh`**
- **42** fix it: an assembler commit either lands in the ledger or fails
  loudly with the text in the log — never silently dropped — **Fable 5**
- **43** make quarantine non-destructive — **Opus 5**
- **22, 23** speaker fail-open and Case B cross-speaker merge — **Opus 5**
- **33s** scoped: stop the relabeled speaker feeding the
  same-speaker-extension check — **Opus 5**

**Gate:** run `...20260807-160529` replays with assembler decisions ==
ledger records == export lines. Zero cross-speaker merges on the
reference corpus.

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

All of it is in `ROADMAP_V5.md` for after delivery. **Dual-channel
capture is the highest-value item there** — it makes speaker identity a
fact rather than a guess and deletes most of the cross-speaker bug
family. Cost impact is roughly $0.46 → $0.92 per audio hour; the reason
to defer is the 2-week window, not the money.

---

## 7. Definition of done for delivery

All of these, on the reference corpus and on three consecutive 30-minute
sessions per direction:

| # | Gate | Target |
|---|---|---|
| 1 | assembler decisions == ledger records == export lines | equal, 3 runs |
| 2 | exact-duplicate lines in export | 0 |
| 3 | canonical records without a translation | 0 |
| 4 | lines containing two speakers' turns | 0 |
| 5 | quarantine events losing real speech | 0 |
| 6 | 60-min session with forced network drop | `completed`, zero loss |
| 7 | Start→Stop→Start, 5 cycles | no degradation |
| 8 | clean-machine install runs the full scenario | pass |

Gate 5 in `ROADMAP_V5.md` (WER gap vs. raw Deepgram) is the real accuracy
measure. Record it at baseline and at delivery, but do not gate the
client date on it — improving it is post-delivery work.

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
| 22 | Unknown speakers both default to `1` (fail-open); use `speakers_confirmed_same()` | — | **Opus 5** | **SPRINT — day 5** |
| 23 | Case B merges across channel/speaker; align to Case C's gating | — | **Opus 5** | **SPRINT — day 5** `[gate]` |
| 24 | `revise_last_transcript_snapshot` has zero speaker check | — | **Opus 5** | **SPRINT — day 6** |
| 11b-LT | Verification only: Stop after ~10 s pause, never executed live | — | human session | **SPRINT — day 10** |
| 25 | UI mints its own `canonical_utterance_id` | — | Opus 5 | DEFERRED — post-delivery |
| 26 | Ledger has no internal version guard | — | Opus 5 | DEFERRED — post-delivery |
| 27 | `_observe_identity` fail-open: decide from Batch 2 evidence | — | Opus 5 | DEFERRED — post-delivery |
| 27b | `_textually_related_revision` threshold tuned while lineage was constant-true | — | Opus 5 | DEFERRED — post-delivery |

### Batch 5 — single canonical controller · DEFERRED, except the scoped items below

| Item | What | Model | Status |
|---|---|---|---|
| 33s | **Scoped**: stop relabeled speaker feeding same-speaker extension | **Opus 5** | **SPRINT — day 6** `[gate]` |
| 34 | `noise_fragment` threshold from evidence | — | SUPERSEDED by item 43 |
| 28, 29 | Fixture capture + root-cause proof for problem A | — | ABSORBED into items 41, 42 |
| 28a, 28b, 30, 31, 32, 33 (full), 35, 36, 37 | Controller rewrite, store consolidation, key redesign, dead code | Fable 5 / Opus 5 | DEFERRED — see `ROADMAP_V5.md` |

### New items — this sprint

| Item | What | Model | Day | Status |
|---|---|---|---|---|
| 38 | `tools/replay_run.py` — replay a recorded run's `provider_events.jsonl` headlessly, diff against its FINAL export | **Opus 5** | 1–2 | **INVESTIGATED, BLOCKED** — design approved with 5 conditions; conditions 1 and 2 both returned findings that change the design. Three decisions needed before coding, see §9 (2026-08-10 rows) |
| 39 | `tools/score_run.py` — pass/fail on the §7 gates plus latency percentiles | **Sonnet 5** | 2 | TODO |
| 40 | Reference corpus: 10 min ja + 10 min en, hand-written expected transcript, baseline recorded | **Sonnet 5** | 2 | TODO |
| 41 | Prove problem A's root cause against run `...20260807-160529` — prove, do not assume | **Fable 5** `xhigh` | 3–4 | TODO |
| 42 | Fix problem A: assembler commit lands in the ledger or fails loudly. No silent drop | **Fable 5** | 4–5 | TODO `[gate]` |
| 43 | Quarantine becomes non-destructive: flag and commit, never drop. Supersedes item 34 (n=2 is too small to tune a threshold) | **Opus 5** | 6 | TODO |
| 44 | Deepgram websocket reconnect mid-session: backoff, buffer, commit in-flight, mark the gap visibly | **Opus 5** | 7 | TODO `[gate]` |
| 45 | DeepL failure/quota: degrade visibly, retry with backoff, circuit-break after N, never block the transcript | **Opus 5** | 8 | TODO |
| 46 | Invalid/expired credentials produce a clear message at Start, not a stack trace | **Sonnet 5** | 9 | TODO |
| 47 | Status indicator: connected / reconnecting / degraded / failed | **Sonnet 5** | 9 | TODO |
| 48 | 60-min stability: bounded queues, ledger memory, Tk rendered-line cap with full history retained | **Opus 5** | 8–9 | TODO |
| 49 | Clean-machine install verification | **Sonnet 5** | 11 | TODO |
| 50 | DeepL `context` parameter — previous 2–3 committed lines, unbilled | **Sonnet 5** | 12 | OPTIONAL |

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
| 2026-08-10 | Opus 5 | **Item 38 condition 2 — the host path diverges from production; reporting before writing, as instructed.** Production: `stabilizer.ingest()` → `assembler.ingest()` → assembler posts a *deferred* flush via `LanguagePipelineWorker.schedule_flush(assembler, due_mono, generation, reason)`, executed by a background thread against wall-clock `due_mono`. `JapaneseTestHost` (test_task2c) deliberately bypasses that: its own docstring says timeout scenarios use "the assembler's synchronous `try_execute_continuity_hold` entry point **instead of real timers**", and tests call `assembler.flush(...)` by hand. So *when* a flush happens — which decides what the assembler batches into one commit — is production-timed but harness-manual. Whether problem A survives that change is unknown and is exactly what condition 4 says to report rather than tune around. |

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
