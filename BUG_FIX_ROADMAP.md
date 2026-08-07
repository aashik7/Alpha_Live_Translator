# Alpha Live Translator — Bug Fix Roadmap & Cross-Session Execution Ledger

**Version:** v3 (self-contained playbook + live ledger, 2026-08-07)
**Supersedes:** v1 and v2 of this file (5-phase draft, then merged-order draft).

---

## 0. What this file is — read this section first, every time

This file is both a **playbook** (how to work) and a **live ledger**
(what's done, what's next). It exists so that **any** coding agent —
Claude Code, Codex, in any account or session, with **zero prior
conversation context** — can open this repo, read this one file, and
correctly continue fixing the known bugs without guessing, without
skipping steps, and without needing a human to re-explain anything that's
already written down here.

**If you are an agent starting fresh on this file:** do section 1 below
in full before touching any code. Do not skip to "Batch 1, item 1" without
orienting first — the orientation steps exist specifically to catch the
case where this file has drifted from reality (it has happened before in
this project: an audit document was reported written but was never
actually committed to disk — see the recovery note inside
`PROACTIVE_AUDIT_20260806.md`). Trust but verify.

**The human collaborator on this project may switch between you and other
agents/sessions mid-task.** That is the entire reason this file's Ledger
(§6) must be updated, precisely and honestly, at the end of every single
item — not just when a batch finishes. If you finish investigating but
run out of turn before implementing, write that down too (see §6's
in-progress row format). Never leave this file in a state where the next
agent would have to guess what actually happened.

---

## 1. Orientation — do this before touching any item

1. **Locate the repo.** Root: `C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0` (Windows path; on a POSIX-style shell inside the same environment this is `/c/Users/islamm/Documents/Tariqul/Alpha_Translator V 1.0`). Active app code lives under `Alpha_Live_Translator\`. Do not modify anything under `_archive\` (project convention, stated in the repo's own `CLAUDE.md`).
2. **Read `CLAUDE.md`** at the repo root for any standing project-specific restrictions.
3. **Read this entire file, top to bottom**, before doing anything else.
4. **Cross-check the Ledger against reality.** Run:
   ```
   git log --oneline -30
   ```
   from the repo root, and compare against §6's Completed table (commit
   hashes are listed there specifically so you can do this check). If the
   Ledger claims something is done but you can't find a matching commit —
   or vice versa, a commit exists that isn't reflected in the Ledger —
   **stop and reconcile this file first** (update it to match reality,
   note the discrepancy in §5) before starting new work. Do not assume
   the Ledger is correct just because it's written down.
5. **Establish the current baseline.** Run the full test suite (exact
   command in §2) once, before starting any new work, and compare the
   result against §2's "Known baseline." If it doesn't match exactly,
   **stop and flag this to the human** before proceeding — something
   changed outside this roadmap's own process and needs explanation
   before you build on top of it.
6. Only after 1-5 are done: go to §6, find the row marked **"→ NEXT UP,"**
   and begin the mandatory method in §3 for that item.

---

## 2. Project facts (no guessing — these are established, not assumed)

- **Repo root:** `C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0`
- **Active project folder:** `Alpha_Live_Translator\` (all file paths in this document are relative to this folder unless stated otherwise)
- **Python venv:** `<repo_root>\.venv\Scripts\python.exe` — this project's real dependencies (customtkinter, deepl, PyAudioWPatch, etc.) live only here; the bare system `python` will fail with `ModuleNotFoundError`.
- **Test command** (run from inside `Alpha_Live_Translator\`):
  ```bash
  SKIP_TK_INTEGRATION_TESTS=1 "<repo_root>/.venv/Scripts/python.exe" -m unittest discover -s tests -p "test_*.py"
  ```
  (On Windows PowerShell, set the env var first: `$env:SKIP_TK_INTEGRATION_TESTS=1` then run the same `-m unittest discover ...` command.)
- **Known baseline** (last verified: commit `a5e2ac4`, 2026-08-07): **175 tests total, 5 failures + 2 errors + 2 skipped.** This total will grow as each roadmap item adds its own regression test — that's expected and correct. What must **never** change is that these exact 7 tests are the only ones failing/erroring, and skip count stays 2:
  - FAIL `test_final_transcript_commit_v3_2_5.TestFinalTranscriptCommitV325.test_commit_allowed_while_finalizing`
  - FAIL `test_final_transcript_commit_v3_2_5.TestFinalTranscriptCommitV325.test_commit_allowed_while_listening`
  - FAIL `test_package_glossary_flags_85253.TestPackageGlossaryFlags85253.test_main_glossary_absent_no_unbound_local`
  - FAIL `test_package_glossary_flags_85253.TestPackageGlossaryFlags85253.test_main_glossary_present_after_successful_inclusion`
  - FAIL `test_stop_finalize_v3_2_3.TestStopFinalizeV323.test_phase_constants_match_spec`
  - ERROR `test_package_glossary_flags_85253.TestPackageGlossaryFlags85253.test_glossary_helper_absent`
  - ERROR `test_package_glossary_flags_85253.TestPackageGlossaryFlags85253.test_glossary_helper_present`

  All 7 are pre-existing, root-caused elsewhere, unrelated to this
  roadmap (documented in `ROOT_CAUSE.md`: glossary packaging script
  drift, a commit-gate test fixture that never opens the Japanese gate,
  a phase-constants spec mismatch). **If the suite ever shows a
  different failure/error than these 7, treat it as a real regression
  from the most recent change — do not assume it's "probably" one of
  these known ones without checking the test name matches exactly.**

- **Live-test verification tool:**
  ```bash
  python tools/scan_interim_ghost_evidence.py [optional_run_folder_path]
  ```
  No argument = auto-picks the most recent folder under
  `troubleshooting/runs/`. Produces `interim_ghost_report.html` in that
  run folder (self-contained, open in any browser). Use this after any
  live test to check interim/ghost-line health. **Known limitation as of
  this writing:** its verdict logic treats any nonzero watchdog-firing
  count as "PASS (working as designed)" without checking the firing
  ratio — this is itself a pending roadmap item (Batch 1, item 3). Until
  that's fixed, manually sanity-check the ratio yourself: watchdog
  firings should be a small minority of total decisions, not most of
  them.

- **Reference documents — read the relevant part before touching an
  item, do not re-derive from scratch:**
  - `PROACTIVE_AUDIT_20260806.md` — the master technical audit. Every
    `§N.N`-numbered item referenced in this roadmap has its full
    File:line, confidence rating, severity, and concrete failure
    scenario written out there. **This is the primary source of truth
    for "what exactly is wrong and why."**
  - `Bug Report.md` — same audit content, plus a section appended
    2026-08-07 titled "Japanese content loss" (items 4.1-4.4) with live
    log evidence for the Japanese-path findings referenced in Batch 5.
  - `ISSUE_1_INTERIM_GHOST_LINE_FIX_REPORT.md` — a **worked example** of
    the full methodology end-to-end: root cause investigation → fix
    design → implementation → before/after verification → live-test
    instructions for the human. **Model every future fix's write-up on
    this one** — same rigor, same structure.
  - `ROOT_CAUSE.md`, `REPAIR_PLAN.md` — the original architecture-level
    audit and repair plan this whole engagement builds on. Batch 5
    (§4) explicitly follows `REPAIR_PLAN.md`'s own phased validation
    methodology (Level 0-5) rather than this roadmap's normal
    single-commit pattern, because it's the one item too large for a
    surgical fix.

- **Git discipline (same as every fix so far in this project):** never
  push, never force-push, never amend a previous commit, never use
  `--no-verify`. One fix = one commit. Commit message states the root
  cause, the fix, and the verification evidence — look at
  `git log --oneline -15` for the existing style and match it.

---

## 3. Mandatory method for every single item — no exceptions, no shortcuts

This exact sequence has been used for every fix in this project so far.
It has caught real mistakes (see step 5). Do not skip steps to move
faster — every skipped step in this project's *earlier* history (before
this roadmap existed) is what caused one fix to repeatedly surface a new,
previously-hidden bug.

1. **INVESTIGATE.** Read the current code at the referenced file/function.
   **Line numbers in §4/§6 below may have drifted** from earlier fixes —
   `grep` the function name to find its current location; never trust a
   stale line number without confirming it first. Read any run logs the
   item's audit-doc entry references. Do not design a fix before this
   step is genuinely done — if the audit doc's description doesn't match
   what you actually find in the code, the audit doc is what's wrong;
   say so and investigate further rather than forcing a fix to match a
   possibly-stale description.
2. **DESIGN.** Propose the exact change to the human — concrete enough
   that "yes" or "no" is a meaningful answer, not a vague direction.
   State: what triggers the bug, why this fix addresses the root cause
   (not just a visible symptom), what could plausibly go wrong with this
   specific change, and what stays unchanged. If something is genuinely
   ambiguous (not just something you could guess at), ask the human a
   specific question rather than guessing and proceeding.
3. **APPROVAL GATE.** Wait for explicit approval of the design before
   writing any code. Do not implement on an assumption of approval, and
   do not treat silence or a topic change as approval.
4. **IMPLEMENT.** Make exactly the approved change. Touch only the
   files/functions the approved design named. If you notice something
   else wrong nearby while you're in there, **do not fix it as a
   drive-by** — append it to §5 ("Notes / newly found") instead and keep
   moving. Scope creep in one commit is exactly what makes a regression
   hard to attribute later.
5. **PROVE THE REGRESSION TEST ACTUALLY CATCHES THE BUG.** Write a
   regression test for the fix. Then temporarily disable your fix
   (comment it out, or revert the logic to its pre-fix state) with the
   new test still in place, run just that test file, and confirm it
   **fails** in the way the original bug would produce. Then restore your
   fix and confirm the test **passes**. This before/after control is not
   optional — it is what proves the test is actually testing the right
   thing, not just testing that the code runs.
6. **VERIFY NO REGRESSION.** Run the full suite (§2's command). The 7
   named baseline tests must still be exactly those 7 failing/erroring —
   no more, no fewer — and skip count must still be 2. Any other change
   in outcome is a stop-and-investigate signal, not something to explain
   away or dismiss as unrelated without checking.
7. **COMMIT.** One fix per commit. Message states root cause, fix, and
   verification evidence, matching the existing commit style
   (`git log --oneline -15` for examples).
8. **UPDATE THIS FILE — mandatory, before ending your turn.** Move the
   item from "Pending" to "Completed" in §6. Fill in every column: commit
   hash, date, one-sentence summary of the *actual* fix (not the
   proposed design, if they differ), the regression test file/name
   added, and your agent identity (e.g. "Claude Code," "Codex" — so a
   human reviewing history knows who did what). Update the **"→ NEXT
   UP"** marker to point at the next pending item in priority order. If
   you only completed some of steps 1-7 before running out of turn,
   still update §6 — use the "in-progress" row format shown there, and
   write down exactly which step you reached and what you found, so the
   next agent (possibly you in a new session, possibly someone else)
   picks up exactly where you left off instead of re-investigating from
   zero.

---

## 4. Why this specific order (read before reordering anything)

The ~25 open items reduce to 4 recurring root causes (call them **Core
bugs 1-4**, defined precisely in §6's item table via the `[core:N]` tag
on each row):

- **Core bug 1** — no single canonical controller; multiple code paths
  independently decide/commit/write the transcript.
- **Core bug 2** — weak per-event identity (constant `channel_index`,
  session-constant `event_id`), forcing code into text-guessing or
  positional ("last row") fallbacks.
- **Core bug 3** — silent failure paths (`except: pass`) and fail-open
  defaults where the project's own stated policy is fail-closed.
- **Core bug 4** — timing/threshold constants that were never measured
  against real observed latency.

The batches below are ordered so that **fixing an earlier batch never
requires re-touching a later one, but fixing a later batch benefits from
the earlier ones already being solid:**

1. **Batch 1** (trivial/standalone) — zero dependencies, do first
   regardless of severity, purely because it's fast and safe.
2. **Batch 2** (Core bug 3, logging only) — no behavior changes. Purpose:
   every batch after this fails *loudly* if it breaks something, instead
   of silently — this is what makes every later batch safer to attempt.
3. **Batch 3** (Core bug 2, identity/comparison) — needs Batch 2's
   visibility to be trustworthy when verifying "no regression" on
   comparison-logic changes.
4. **Batch 4** (Core bug 1, partial — concurrency/state-machine) — the
   merge-safety and store-write fixes here assume text/identity
   comparisons are already trustworthy (Batch 3).
5. **Batch 5** (Core bug 1, full — single canonical controller for
   Japanese) — the biggest, most architecturally invasive item. Goes
   **last**, deliberately, even though it fixes the single most severe,
   most measurable content-loss bug found so far (item 4.1, Japanese
   sentences silently dropped between the assembler and the canonical
   ledger). Fixing it first, out of order, is exactly the pattern that
   caused earlier fixes in this project's history to keep surfacing a
   new hidden bug — the ground under it needs to be stable first.

**Do not reorder items without writing the reason in §5.** If you believe
a different order is genuinely better, that's a legitimate call to make
— just document why, so the next agent (or the human) understands the
change was deliberate, not a shortcut.

---

## 5. Notes / newly found (append-only — never fix these as a drive-by, log them here instead)

*(Empty as of 2026-08-07. Add a dated bullet here whenever an item's
investigation step turns up something unexpected, something the audit
docs got wrong, or a new issue spotted in passing. Do not delete old
entries even after they're addressed — mark them `[resolved, see item
#N]` instead, so the history stays readable.)*

- 2026-08-07: none yet — this roadmap file itself was just created.

---

## 6. Ledger — THE SINGLE SOURCE OF TRUTH FOR CURRENT STATE

**Update this section every single time, per §3 step 8. Do not let it drift.**

### 6a. Completed (pre-roadmap work — done before this file existed, listed here for continuity so no agent rediscovers these)

| Commit | Date | What | Fixed by |
|---|---|---|---|
| `5001275` | 2026-08-05 | BUG-A..D: UtteranceEnd channel key, interim/final compare order (BUG-B — see note below), interim timeout arming, identity-linked post-commit corrections | prior session |
| `2012da8` | 2026-08-05 | BUG-E: append premature continuations instead of dropping them | prior session |
| `0a83a9c` | 2026-08-06 | BUG-G1/G2/H: forward interim results to lifecycle owner (`on_interim` wiring); introduced the raw+lifecycle double-delivery documented later in `5c48847` | prior session |
| `153f8b8` | 2026-08-06 | Fix `_merge_lexical` word-overlap gap surfaced by the interim wiring fix above | prior session |
| `d7c1834` | 2026-08-07 | **[core:3]** Log silently-swallowed `_dispatch_commit` callback failures (audit item §2.1) | Claude Code |
| `25a6623` | 2026-08-07 | **[core:2]** Tighten `_text_related` prefix-overlap threshold 8ch/50%→12ch/65% (audit item §3.4 row 5) | Claude Code |
| `1a32639` | 2026-08-07 | **[core:2]** Add word-order check to `_merge_lexical`'s overlap branch via `difflib.SequenceMatcher` (audit item §3.4 row 7) | Claude Code |
| `98a6fa0` | 2026-08-07 | **[core:2]** Bounded retry (3×, ~60ms, lock released) in `utterance_lifecycle.py::_resolve_correction_target_locked` (audit item §1.1, part 1/2) | Claude Code |
| `432dea1` | 2026-08-07 | **[core:1]** Non-blocking re-queue retry in `duplicate_protection.py::_display_transcript_item` (audit item §1.1, part 2/2 — §1.1 now fully mitigated) | Claude Code |
| `78eb59e` | 2026-08-07 | **[core:2]** Fix permanent interim ghost line: identity gate + liveness watchdog (`INTERIM_GHOST_TTL_MS`, `_check_interim_ghost_watchdog`) (audit item §1.2 / §3.4 row 1) | Claude Code |
| `f402cc0` | 2026-08-07 | Docs: `ISSUE_1_INTERIM_GHOST_LINE_FIX_REPORT.md` | Claude Code |
| `b738ab4` | 2026-08-07 | Tooling: `tools/scan_interim_ghost_evidence.py` (has a known verdict-logic bug, see Batch 1 item 3) | Claude Code |
| `5c48847` | 2026-08-07 | **[core:1]** Documented (not fixed) `deepgram_client.py`'s double interim delivery, audit item §3.9. Recreated `PROACTIVE_AUDIT_20260806.md` after discovering it had never actually been committed despite being reported written — **this is the incident referenced in §0/§1 as the reason to verify this Ledger against `git log` rather than trust it blindly.** | Claude Code |
| `a5e2ac4` | 2026-08-07 | **[core:1]** Stop `utterance_lifecycle.py` Case C's commit path from also publishing its own final text through the interim-preview channel (`emit_interim` param on `_apply_active_update_locked`). Found via a live controlled test *after* `78eb59e` shipped — a 3rd, independent source of the same visible symptom (brief ~1.5-1.9s flicker, not the original permanent ghost). Confirms this roadmap's own §4 ordering logic: fixing symptoms before the underlying identity/controller work keeps surfacing new instances of the same visible bug from different code paths. | Claude Code |

**Correction note on `5001275`:** `PROACTIVE_AUDIT_20260806.md`'s original
draft mislabeled `main_window.py::_apply_final_interim_comparison` as
"confirmed still broken" based on this commit's BUG-B fix being
misread — the order-swap BUG-B actually fixed was **already correct**
by the time of the audit; what was still genuinely broken (and got fixed
in `78eb59e`) was a *different*, narrower defect (the unrelated-text
fallthrough default). See `PROACTIVE_AUDIT_20260806.md` §1.2 for the full
correction — flagging here so no future agent re-litigates this by
half-reading old commit messages.

### 6b. Live-test verification status (separate from code-fix status — track here)

| Fix | Live-tested? | Result |
|---|---|---|
| `78eb59e` (interim ghost) | Yes, 2026-08-07 | Root symptom (permanent ghost) gone; found the `a5e2ac4` flicker case as a follow-up |
| `a5e2ac4` (commit-path emit) | Yes, 2026-08-07 | `sf=True` interim events confirmed at zero post-fix; watchdog now fires 10/12 decisions in one run — **this is item 4.4/Batch-1-item-2, not a sign `a5e2ac4` is wrong** (the TTL itself is mis-calibrated, see below) |
| Everything else in 6a | Not yet live-tested individually | — |

### 6c. Pending — in execution order (do not reorder without a §5 note)

Each row: `[core:N]` tag = which root cause this belongs to (§4). File/function
locations are **as last verified**; re-confirm via grep per §3 step 1 before
trusting them.

#### Batch 1 — trivial, standalone, near-zero risk (no dependencies on anything below)

| # | Item | `[core]` | File / function (last verified) | What's wrong |
|---|---|---|---|---|
| 1 | §3.7 | 3 | `alpha/ui/main_window.py:7850`, `_begin_graceful_stop` | `self._flush_pending_translation_submit()` called with zero args; the method requires a `key` param with no default (`main_window.py:6580`ish — re-verify). Every call raises `TypeError`, caught by a bare `except Exception: pass`. Currently harmless only because `stop_finalize_worker.py` separately calls the correct plural method later — but this specific safety net has never once executed. One-line fix: pass the correct argument(s), or remove the dead call if it's genuinely redundant (investigate which before deciding). |
| 2 | 4.4 | 4 | `alpha/constants.py`, `INTERIM_GHOST_TTL_MS` | Currently `1500`. Measured real gap between last interim update and matching final comparison: English 1635-1924ms across 5 runs, **Japanese up to 4063ms** (see `Bug Report.md` §4.4 table). TTL sits below both. Do NOT derive this from `DEEPGRAM_ENDPOINTING_MS` — Japanese's endpointing (500ms) is *lower* than English's (1200ms) yet shows the *longest* gaps, because the Japanese "final" comes from the assembler's own hold/timeout logic, not Deepgram endpointing directly. A formula derived from endpointing would size Japanese wrong. Recommended starting point from prior investigation: a flat `6000`ms (covers measured JA worst case 4063ms + ~2s margin) — but re-verify against any newer run data gathered since, and get explicit sign-off on the exact number before implementing, this was proposed but not yet approved. |
| 3 | (new) | 4 | `tools/scan_interim_ghost_evidence.py`, `build_verdict()` | Currently returns "PASS" whenever `anomalies` is empty, regardless of how many watchdog firings occurred relative to total decisions. A run with 10 watchdog firings out of 12 decisions currently reports PASS — should be REVIEW/WARN above some ratio threshold (e.g. >20-30% of decisions needing the watchdog is a sign Layer 1's identity gate isn't doing its job, not evidence the design is working). Fix the verdict function's logic; this is a tooling-only change, no production code touched. |

#### Batch 2 — Core bug 3, logging only, no behavior changes except item 8's explicit note

*(Depends on nothing; can run in parallel with Batch 1 if convenient, but keep as separate commits.)*

| # | `[core]` | File / function (last verified) | What to do |
|---|---|---|---|
| 4 | 3 | `alpha/ui/main_window.py:1301-1318`, `_remove_interim_line_from_display` | Replace the exception-driven no-op (`box.compare("interim_anchor", ...)` raises `TclError` on the normal "nothing to remove" case, caught and logged every time as `remove_exception`) with a `if "interim_anchor" not in box.mark_names(): return` guard. Removes log noise currently drowning real signal; zero behavior change. |
| 5 | 3 | `alpha/ui/main_window.py:1294-1304, 1395-1409`, `_on_store_segment_updated`'s 3 side effects | Add logging inside the 3 swallowed `except Exception: pass` blocks (interim-line removal, stale-translation removal, translation resubmit). |
| 6 | 3 | `alpha/transcription/utterance_lifecycle.py:313, 411-412`, `reset_for_session` / `_resolve_correction_target_locked` | Add logging on the swallowed-exception paths. Do not change fail-open/fail-closed behavior here. |
| 7 | 3 | `alpha/transcription/deepgram_client.py:855-856`; `alpha/transcription/pipeline_commit_transaction.py:85-125`; `alpha/utils/stop_finalize_worker.py:1906-1930`; `alpha/transcription/japanese_sentence_assembler.py:4245-4246, 2327-2328`; `alpha/transcription/japanese_boundary_stabilizer.py:906-907` | Same pattern across all 6 locations: add logging to the silent `except`, no behavior change. Can be one commit per location or grouped — your call, but keep each commit's diff reviewable. |
| 8 | 3 | `alpha/transcription/utterance_lifecycle.py:371-372`, `_observe_identity` | **Log only — do NOT flip fail-open (`return True, "unavailable", {}`) to fail-closed in this batch.** That's a real behavior change (could start rejecting things currently accepted) and needs its own investigation with logged evidence in hand first — that's Batch 4 item 25. |
| 9 | 4 | `alpha/transcription/japanese_sentence_assembler.py` (quarantine logic — investigate current location) | **Investigation only, no fix yet.** Collect more live-run evidence on how often `later_committed_to_stable: false` occurs in `accuracy/quarantine_decisions.jsonl` across multiple sessions (Japanese, real speech). This feeds the `noise_fragment` threshold decision folded into Batch 5 item 30 — do not touch the threshold itself in this batch, just gather data. |

#### Batch 3 — Core bug 2, identity & text-comparison hardening

*(Depends on Batch 2's visibility being in place. Ordered by severity within the batch — do the top of this list first.)*

| # | `[core]` | File / function (last verified) | What's wrong |
|---|---|---|---|
| 10 | 2 | `alpha/ui/main_window.py:5492-5554`, `_check_stop_tail_duplicate` | Highest severity in this batch: Stop-time last-chance commit decision made via `norm_interim == norm_seg or norm_interim in norm_seg` / `.startswith()` only — can silently classify a reworded (not pure-substring) tail as `skip_already_committed` and drop it entirely, with no second chance since this runs at Stop. |
| 11 | 2 | `alpha/ui/main_window.py:4332-4347`, `_should_commit_interim_recovery` | Same containment-only anti-pattern, Stop-time recovery path. |
| 12 | 2 | `alpha/ui/main_window.py:4954-4997`, `_should_repair_previous_segment` | Same pattern; misclassifies a reworded (not pure-substring) previous segment as non-continuation. |
| 13 | 2 | `alpha/transcription/duplicate_protection.py:49-81`, `decide_transcript_action` | Residual gap (main `or True` bypass already fixed): a substitution-style correction with no authoritative `lifecycle_decision` signal present still falls to `"add"` (creates a duplicate line) instead of `"update"`. Mitigated in the common case by the upstream signal override; live when that signal is absent. |
| 14 | 2 | `alpha/transcription/japanese_sentence_assembler.py:597-622`, `merge_japanese_fragments` | `curr.startswith(prev)` / `prev.endswith(curr)` accepted as proof of full subsumption before the smarter overlap-search fallback runs; can drop a corrected-but-shorter retranscription that happens to be a literal suffix. |
| 15 | 2 | `alpha/transcription/japanese_sentence_assembler.py:2573-2591`, `_looks_like_speaker_continuation_tail` | Literal-prefix/phrase-containment list can misclassify a different speaker's line starting with an ordinary connective as a continuation. **Do this before item 22 (§3.1 speaker relabeling in Batch 4) — that item's logic consumes this function's output.** |
| 16 | 2 | `alpha/transcription/deepgram_client.py:626-650`, `teams_commit_decision_from_dup_action` | A 4th independent instance of the same anti-pattern class, currently diagnostic-only (its output only feeds logging, not a real commit branch — verify this is still true before touching). Rename or explicitly mark diagnostic-only so a future change can't accidentally wire it into a live decision path (it would reintroduce this bug class a 5th time). |
| 17 | 2 | `alpha/transcription/duplicate_protection.py:182-234` + `alpha/summary/transcript_store.py:71-156` | Combined fix (same root cause): delete or alias the unsafe speaker-only `update_last_segment`/`get_last_segment` in `transcript_store.py` so only the safe `..._if_active` variants remain callable. Update the 3 call sites still using the unsafe version: `duplicate_protection.py:156`, `main_window.py:4407`, `main_window.py:5106`. |
| 18 | 2 | `alpha/summary/transcript_store.py:158-179`, `add_translation` | Matches an incoming translation to a segment by exact text equality, not record id. If the segment's text was revised between translation request and response, the match silently fails and the translation is dropped with no log. Move to record-id-based matching. |
| 19 | 2 | `alpha/transcription/japanese_boundary_stabilizer.py:233-248`, `duplicate_continuation_ratio` | Pure substring-containment + character-match-ratio suppression, not substitution-aware — can silently drop a genuinely new short remark that happens to be a literal substring of the previous line. Add a substitution-aware check on top of the existing ratio gate (same style as the `_merge_lexical`/`_text_related` fixes already done in `1a32639`/`25a6623` — use those as the template). |

**Checkpoint before Batch 4:** live-test both languages with deliberately
overlapping/back-to-back short utterances (the known stress case);
compare `IDENTITY_REJECTION`/`FALLBACK_BLOCKED`/quarantine rates in the
logs against the pre-Batch-3 baseline, confirm they've measurably
dropped.

#### Batch 4 — Core bug 1 (partial), concurrency & state-machine safety

*(Depends on Batch 3's identity/comparison work being trustworthy.)*

| # | `[core]` | File / function (last verified) | What's wrong |
|---|---|---|---|
| 20 | 1 | `alpha/utils/stop_finalize_worker.py:1116-1132`, `_confirm_transcript_commits` | Computes `transcript_remaining`/`batch_remaining` and only logs them — never compares to zero, so `commit_confirm_ok` is effectively always `True`. The real, correctly-computed boolean already exists a few hundred lines earlier in the same function: `ui_stop_drain_barrier.py`'s `passed` field. Wire that value in instead of the unused count. Smallest, most isolated item in this batch — do it first. |
| 21 | 1 | `alpha/transcription/utterance_lifecycle.py:1160` | `int(active.speaker or 1) != int(speaker or 1)` — defaults *both* unknown speakers to `1`, so two utterances with genuinely different unknown speakers register as the same speaker (fail-open). Every sibling module uses the shared `speakers_confirmed_same()` guard instead (fail-closed on unknown). Switch to that guard. **Do before item 22** — it needs the same speaker-safety primitive. |
| 22 | 1 | `alpha/transcription/utterance_lifecycle.py:1038-1057`, Case B `force_new` computation, contrast with Case C at `:1081-1132` | `force_new = not same_active and active is not None and active.committed` — when an active **uncommitted** utterance exists and the new candidate is on a different channel/speaker, `force_new` is `False`, so the merge branch runs unconditionally and overwrites `active.channel`/`active.speaker`. Case C correctly gates on `same_active` first; align Case B to match. |
| 23 | 1 | `alpha/utils/transcript_snapshot_store.py:79-145`, `revise_last_transcript_snapshot` | Revises `_segments[-1]` (literal last row) with zero speaker/session/channel check. Add the same speaker-confirmation check the store's other safe methods already use. |
| 24 | 1 | `alpha/ui/main_window.py:5556-5891, 5046-5140` | UI layer independently mints its own `canonical_utterance_id` via `uuid.uuid4()` (Japanese manual mode, `_commit_transcript_item_to_store`) and writes without any identity at all (`_try_segment_repair`, English path). Scope this fix **narrowly** — stop the independent minting/no-identity-write, do not attempt a full rewrite here; this is the UI-side bridge into Batch 5's controller work, not Batch 5 itself. |
| 25 | 3 | `alpha/transcription/utterance_lifecycle.py:371-372`, `_observe_identity` | **Revisit, deferred from Batch 2 item 8.** With logged evidence from Batches 2-4 now available, decide: flip fail-open to fail-closed outright, or use a narrower condition. This decision should be evidence-driven — check how often the log line from item 8 actually fired before deciding. |

**Checkpoint before Batch 5:** run `REPAIR_PLAN.md`'s Level 2
(component integration: lifecycle → ledger → UI store → translation, no
real APIs) and Level 3 (replay recorded event sequences) if a replay
harness exists in this repo; otherwise substitute a 15-30 minute live
session per language that deliberately includes Start→Stop→Start.

#### Batch 5 — Core bug 1 (full), single canonical controller for Japanese

*(This is where item 4.1 — the actual measured Japanese content loss —
gets fixed. Deliberately last. Treat as its own mini-project following
`REPAIR_PLAN.md`'s own phased methodology, not a single surgical commit
like every other item on this list.)*

| # | `[core]` | What to do |
|---|---|---|
| 26 | 1 | Capture deterministic replay fixtures from a real session that reproduces item 4.1 (a canonical-record shortfall vs. assembler commit-decision count — see `Bug Report.md` §4.1 for the exact reproduction: run `v3.3.5.5.8.5.26.5.3-20260807-160529`, 10 assembler `commit_new` decisions, 10 rows in `stable_commits.jsonl`, only 9 canonical ledger records, the sentence `ですよ。違いますねでやっぱりこっちにいると日本の行事の不倫っていうのを味わうことが難しいので、` present in `stable_commits.jsonl` but absent from the canonical ledger and final export). Do this **before** touching any code (`REPAIR_PLAN.md`'s own Phase 0). |
| 27 | 1 | Confirm 4.1's root cause concretely against the fixture. This roadmap's working assumption is audit item §3.2 (the Japanese assembler's Stop-tail direct-commit bypass and/or its post-decision persistence side-writes to `transcript_snapshot_store` running independently of the canonical controller) — but that must be **proven** against the fixture, not assumed. It's plausible the real cause is elsewhere in the handoff; investigate with the same rigor as every other item. |
| 28 | 1 | Design the assembler → controller handoff so every `commit_new`/`update_previous` decision goes through exactly one path (`accept_boundary_proposal`, already the pattern used for the main commit path per audit item §3.2's "partially resolved" finding) with **no side-write path that can silently diverge from it.** |
| 29 | 1 | Consolidate the 3-way store overlap (audit item §3.6): `canonical_transcript_ledger.py` / `alpha/summary/transcript_store.py` / `alpha/utils/transcript_snapshot_store.py` all independently "hold the transcript." Fold this into the same change as item 28 — they're touched by the same handoff. |
| 30 | 4 | Fold in the `noise_fragment` quarantine-threshold decision (item 9's data collection feeds this) — use the evidence gathered in Batch 2, don't guess at a new threshold without it. |
| 31 | 1 | Validate per `REPAIR_PLAN.md`: Level 3 (replay) → Level 4 (synthetic, 100+ utterances, repeated text, reordered callbacks, duplicate finals) → Level 5 (real live test, both languages, repeated Start/Stop, 15-30 minute session). |
| 32 | 1 | Re-run item 4.1's exact reproduction scenario last, as final proof the specific measured loss is gone. |

### 6d. → NEXT UP

**Batch 1, item 1** — `alpha/ui/main_window.py:7850`, `_begin_graceful_stop`'s zero-arg `_flush_pending_translation_submit()` call. Begin with §3 step 1 (investigate).
