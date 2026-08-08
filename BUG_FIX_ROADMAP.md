# Alpha Live Translator — Bug Fix Roadmap & Cross-Session Execution Ledger

**Version:** v4 (2026-08-07) — verified against the live tree at commit `114391b`.
**Supersedes:** v1, v2, v3 of this file.

**What changed in v4 (why you should not use a cached copy of v3):**
- All `main_window.py` line numbers in v3 were stale by ~105-120 lines
  (this session's own fixes shifted them). All references are now
  **grep-anchored** instead of line-anchored — see §2.4.
- **Five audit items were missing entirely from v3's batches** (§1.3,
  §2.10, §3.1, §3.5, and dead-code cleanup). They are now items 20, 7b,
  23, 30, and 36.
- Added: rollback/abort procedure (§3.9), live-test protocol (§3.10),
  honest coverage assessment (§7), model-selection guidance (§8).

---

## 0. What this file is — read this section first, every time

This file is both a **playbook** (how to work) and a **live ledger**
(what is done, what is next). It exists so that **any** coding agent —
Claude Code or Codex, in any account or session, with **zero prior
conversation context** — can open this repo, read this one file, and
correctly continue fixing the known bugs without guessing, without
skipping steps, and without needing a human to re-explain anything
already written here.

**If you are an agent starting fresh:** complete §1 in full before
touching any code. Do not jump straight to the next pending item. The
orientation steps exist specifically to catch the case where this file
has drifted from reality — which has already happened twice in this
project:

- An audit document was reported as written but had never actually been
  committed to disk (recovery note inside `PROACTIVE_AUDIT_20260806.md`).
- v3 of *this* file shipped with ~10 stale line numbers and 5 missing
  items, found only because a human asked for a re-verification pass.

Trust this file's **structure and method**; verify its **specific
references** against the live tree before acting on them.

**The human collaborator may switch between agents/accounts/sessions
mid-task.** That is the entire reason §6's Ledger must be updated
precisely and honestly at the end of **every single item** — not just
when a batch finishes. If you finish investigating but run out of turn
before implementing, write that down too (§6c's in-progress row format).
Never leave this file in a state where the next agent would have to guess
what actually happened.

---

## 1. Orientation — mandatory before touching any item

1. **Locate the repo.** Root: `C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0`
   (on a POSIX-style shell in the same environment:
   `/c/Users/islamm/Documents/Tariqul/Alpha_Translator V 1.0`).
   Active app code is under `Alpha_Live_Translator\`. **Never modify
   anything under `_archive\`** (project convention, stated in the repo's
   own `CLAUDE.md`).
2. **Read `CLAUDE.md`** at the repo root for standing project restrictions.
3. **Read this entire file, top to bottom.**
4. **Reconcile the Ledger against git.** Run `git log --oneline -30` from
   the repo root and compare against §6a/§6b. If the Ledger claims
   something is done with no matching commit — or a commit exists that
   the Ledger does not reflect — **stop and fix this file first**, and
   log the discrepancy in §5. Do not start new work on an unreconciled
   ledger.
5. **Establish the test baseline.** Run the full suite (§2.2) once before
   any new work and compare to §2.3. If it does not match exactly, **stop
   and tell the human** — something changed outside this roadmap's
   process and must be explained before anything is built on top of it.
6. **Go to §6d ("→ NEXT UP")**, then follow §3 for that item.

---

## 2. Project facts (established — not assumptions)

### 2.1 Paths
- Repo root: `C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0`
- Active project folder: `Alpha_Live_Translator\` — **all file paths in
  §6 are relative to this folder** unless stated otherwise.
- Python venv: `<repo_root>\.venv\Scripts\python.exe`. The project's real
  dependencies (customtkinter, deepl, PyAudioWPatch, websocket-client,
  numpy…) exist **only** here. The bare system `python` will fail with
  `ModuleNotFoundError`. Use the venv interpreter for anything that
  imports `alpha.*`. (Plain `python -c "import ast; ast.parse(...)"`
  syntax checks are fine with system Python since they import nothing.)

### 2.2 Test command
Run from inside `Alpha_Live_Translator\`:
```bash
SKIP_TK_INTEGRATION_TESTS=1 "<repo_root>/.venv/Scripts/python.exe" -m unittest discover -s tests -p "test_*.py"
```
PowerShell equivalent: `$env:SKIP_TK_INTEGRATION_TESTS=1` first, then the
same `-m unittest discover ...` invocation.
There is **no pytest** in this venv — `unittest discover` is the only
supported runner.

### 2.3 Known-good baseline (verified at commit `a5e2ac4`)
**175 tests, 5 failures + 2 errors + 2 skipped.**

The total will grow as each item adds its regression test — that is
expected. What must **never** change is that these exact 7 are the only
failing/erroring tests, and skips stay at 2:

- FAIL `test_final_transcript_commit_v3_2_5.TestFinalTranscriptCommitV325.test_commit_allowed_while_finalizing`
- FAIL `test_final_transcript_commit_v3_2_5.TestFinalTranscriptCommitV325.test_commit_allowed_while_listening`
- FAIL `test_package_glossary_flags_85253.TestPackageGlossaryFlags85253.test_main_glossary_absent_no_unbound_local`
- FAIL `test_package_glossary_flags_85253.TestPackageGlossaryFlags85253.test_main_glossary_present_after_successful_inclusion`
- FAIL `test_stop_finalize_v3_2_3.TestStopFinalizeV323.test_phase_constants_match_spec`
- ERROR `test_package_glossary_flags_85253.TestPackageGlossaryFlags85253.test_glossary_helper_absent`
- ERROR `test_package_glossary_flags_85253.TestPackageGlossaryFlags85253.test_glossary_helper_present`

All 7 are pre-existing and unrelated to this roadmap (root-caused in
`ROOT_CAUSE.md`: glossary packaging script drift, a commit-gate test
fixture that never opens the Japanese gate, a phase-constants spec
mismatch). **If a different test fails, treat it as a real regression
from your change** — never assume "probably one of the known ones"
without matching the test name exactly.

### 2.4 How to locate code — grep anchors, not line numbers
Line numbers in this file **will drift** as items are fixed (v3 proved
it: ~10 references were stale after a single session's work). Therefore:

- Every item in §6c carries a **`grep:`** field containing a distinctive,
  stable string.
- Line numbers are written as `≈NNNN` and are only valid **as of commit
  `114391b`**.
- **Always locate code by grepping the anchor first.** If the anchor no
  longer matches anything, the code has changed materially — investigate
  and update this file (§5) rather than guessing at the nearest similar
  code.
- After you fix an item, do **not** try to update every other item's line
  numbers. Only fix an anchor if you discover it is broken.

### 2.5 Live-test verification tool
```bash
python tools/scan_interim_ghost_evidence.py [optional_run_folder]
```
No argument = auto-picks the newest folder under
`troubleshooting/runs/`. Writes `interim_ghost_report.html` into that run
folder (self-contained; open in a browser).
**Known defect (this is roadmap item 3):** its verdict logic returns PASS
whenever no anomalies are flagged, regardless of how many watchdog
firings occurred. A run with 10 firings out of 12 decisions currently
reports PASS. Until item 3 is fixed, check the ratio manually — watchdog
firings should be a small minority of decisions, not most of them.

### 2.6 Reference documents — which file answers which question

**These files overlap and they are NOT equally current. Using the wrong
one will make you "fix" something that is already fixed.**

| Question | Use this file |
|---|---|
| What is still broken? What do I do next? | **This file**, §6 Ledger — the only authority on current state |
| What is the fix status of audit item §N.N? | **`PROACTIVE_AUDIT_20260806.md`** — carries per-item `STATUS:` lines, kept updated |
| What was the original evidence for a finding? | `PROACTIVE_AUDIT_20260806.md` (full detail, still current) |
| Japanese content loss (items 4.1-4.4) | **`Bug Report.md`**, bottom section — this content exists *only* there |
| How should a fix be written up? | `ISSUE_1_INTERIM_GHOST_LINE_FIX_REPORT.md` — worked example, model yours on it |
| Architecture intent / validation levels | `ROOT_CAUSE.md`, `REPAIR_PLAN.md` |

- **`PROACTIVE_AUDIT_20260806.md`** — the master technical audit. Every
  `§N.N` item referenced in §6 has its full File:line, confidence rating,
  severity, and concrete failure scenario there, **plus a `STATUS:` line
  recording whether it is fixed, mitigated, or still open.** Primary
  source of truth for "what exactly is wrong and why."
- **`Bug Report.md`** — ⚠️ **a frozen 2026-08-06 snapshot** of the same
  audit, with **no** `STATUS:` updates: it still describes §1.2 as
  "confirmed still broken" even though that was fixed in `78eb59e`. It
  carries a warning banner at the top saying so. **Its only unique,
  current content is the appended "Japanese content loss" section
  (4.1-4.4)** — referenced by items 2, 9, 28, and 34 below. Read that
  section; ignore the rest of that file in favour of
  `PROACTIVE_AUDIT_20260806.md`.
- **`ISSUE_1_INTERIM_GHOST_LINE_FIX_REPORT.md`** — a **worked example** of
  the full method end-to-end: investigation → design → implementation →
  before/after verification → live-test instructions. **Model every
  fix's write-up on this.**
- **`ROOT_CAUSE.md`, `REPAIR_PLAN.md`** — the original architecture audit
  and repair plan. Batch 5 explicitly follows `REPAIR_PLAN.md`'s Level
  0-5 validation methodology rather than this roadmap's normal
  single-commit pattern.

### 2.7 Git discipline
Never push. Never force-push. Never amend an existing commit. Never use
`--no-verify`. One fix = one commit. Match the existing message style
(`git log --oneline -15`): root cause, the fix, verification evidence.

---

## 3. Mandatory method — every item, no exceptions

This exact sequence has been used for every fix in this project and has
caught real mistakes (step 5 in particular). Every skipped step in this
project's *earlier* history is what caused one fix to keep surfacing a
new hidden bug.

1. **INVESTIGATE.** Grep the item's `grep:` anchor to find the code
   (§2.4). Read it. Read the run logs the item's audit entry references.
   If what you find contradicts the item's description, **the description
   is what is wrong** — say so, investigate further, and log it in §5.
   Never force a fix to match a possibly-stale description.
2. **DESIGN.** Propose the exact change to the human, concrete enough
   that "yes"/"no" is a meaningful answer. State: what triggers the bug,
   why this addresses the root cause (not just the visible symptom), what
   could plausibly go wrong with this specific change, and what stays
   unchanged. If something is genuinely ambiguous, ask a specific
   question rather than guessing.
3. **APPROVAL GATE.** Wait for explicit approval before writing code.
   Silence or a topic change is not approval.
4. **IMPLEMENT.** Make exactly the approved change, touching only the
   files/functions named. **If you notice something else wrong nearby, do
   not fix it as a drive-by** — append it to §5 and move on. Scope creep
   makes regressions impossible to attribute.
5. **PROVE THE TEST CATCHES THE BUG.** Write the regression test. Then
   temporarily disable your fix (with the new test in place), run that
   test file, and confirm it **fails** the way the original bug would.
   Restore the fix, confirm it **passes**. Delete any temporary
   scaffolding. This before/after control is **not optional** — it is the
   only thing that proves the test tests the right thing.
6. **VERIFY NO REGRESSION.** Run the full suite (§2.2). The 7 baseline
   tests in §2.3 must still be exactly those 7, and skips still 2.
7. **COMMIT.** One fix per commit, message per §2.7.
8. **UPDATE THIS FILE — before ending your turn.** Move the item to
   §6a/§6b, filling in every column: commit hash, date, one-sentence
   summary of the *actual* fix (not the proposed design, if they
   differed), regression test file/name, and your agent identity (e.g.
   "Claude Code (Opus 5)", "Codex"). Update **§6d "→ NEXT UP"**. If you
   completed only some of steps 1-7, still update §6 using the
   in-progress row format and record exactly which step you reached and
   what you found.

### 3.9 Rollback / abort procedure
If, at step 5 or 6, the change turns out to be wrong or to break the
baseline and you cannot resolve it within the same turn:

1. Restore the file(s): `git checkout -- <path>` if uncommitted, or if
   you already committed, **do not** amend or reset — make a new revert
   commit (`git revert <hash>`) so history stays honest.
2. Re-run the full suite and confirm the baseline is restored.
3. Record what happened in §5 (what you tried, why it failed, what you
   learned) and leave the item **pending** in §6c with an in-progress
   note. Do not mark it done.
4. Tell the human plainly what happened. Do not silently drop it.

### 3.10 Live-test protocol (agent cannot do this alone)
Several checkpoints require a **live app session**, which the agent
cannot run — the app needs real audio devices and a GUI. When a
checkpoint calls for a live test:

1. Tell the human exactly what to do, e.g.: *"Please run a session in
   English, speak 5-6 sentences with deliberate ~2s pauses between some
   of them, then Stop. Then repeat in Japanese."*
2. Ask them to report the run folder name (newest folder under
   `troubleshooting/runs/`).
3. Analyze it yourself: run `tools/scan_interim_ghost_evidence.py`, and
   read `logs/async_debug.log`, `logs/japanese_accuracy.log`,
   `evidence_streams/*.jsonl`, `accuracy/*.jsonl`, and
   `accuracy_stage_compare/export_coverage_report.json` in that folder.
4. **Do not treat `coverage_ratio: 1.0` as proof nothing was lost.** That
   gate only compares canonical → final; a record lost *before* reaching
   the canonical ledger is invisible to it. This is exactly how item 4.1
   went undetected. Compare assembler decision counts against canonical
   record counts directly.

---

## 4. Why this order (read before reordering anything)

The open items reduce to 4 recurring root causes. Each item in §6c is
tagged `[core:N]`:

- **Core bug 1** — no single canonical controller; multiple code paths
  independently decide/commit/write the transcript.
- **Core bug 2** — weak per-event identity (`channel_index` is constant
  `[0,1]` on mono; `event_id` is session-constant), forcing code into
  text-guessing or positional ("last row") fallbacks.
- **Core bug 3** — silent failure paths (`except: pass`) and fail-open
  defaults where the project's stated policy is fail-closed.
- **Core bug 4** — timing/threshold constants never measured against real
  observed latency.

Batches are ordered so that fixing an earlier batch never requires
re-touching a later one, but every later batch benefits from the earlier
ones being solid:

1. **Batch 1** — trivial/standalone, zero dependencies. Fast and safe.
2. **Batch 2** — Core bug 3, **logging only**. Makes every later batch
   fail *loudly* instead of silently. This is what makes Batches 3-5 safe
   to attempt at all.
3. **Batch 3** — Core bug 2. Needs Batch 2's visibility to trust "no
   regression" on comparison-logic changes.
4. **Batch 4** — Core bug 1 (partial): concurrency/state-machine. Assumes
   Batch 3's comparisons are trustworthy.
5. **Batch 5** — Core bug 1 (full): the single canonical controller.
   Goes **last** even though it fixes the single most severe measured
   content loss (item 4.1, Japanese sentences dropped between assembler
   and ledger). Fixing it first is exactly what caused earlier fixes to
   keep surfacing new hidden bugs.

**Do not reorder without writing the reason in §5.**

---

## 5. Notes / newly found (append-only)

Append a dated bullet whenever investigation turns up something
unexpected, something the audit docs got wrong, or a new issue spotted in
passing. **Never delete entries** — mark them `[resolved, see item #N]`.

- 2026-08-07 — v3 of this file had ~10 stale `main_window.py` line
  numbers (this session's own fixes shifted the file by ~116 lines) and
  omitted 5 audit items entirely. Fixed in v4 by switching to grep
  anchors (§2.4) and adding items 7b, 20, 23, 30, 36. **Lesson: line
  numbers in any doc in this repo are unreliable the moment a fix lands.**
- 2026-08-07 — Audit item §2.2's description says `force_new = not
  same_active and ...` as if it were an assignment statement. It is
  actually a **keyword argument** passed into `_apply_active_update_locked`
  (grep `force_new=not same_active`). The finding itself is unchanged and
  still valid; only the framing in the audit text is imprecise.
- 2026-08-08 — **Never use `sed -i` on this repo's `.py` files, even for
  a quick temporary toggle during §3 step 5's before/after test proof.**
  GNU sed (via Git Bash) silently rewrote `constants.py`'s CRLF line
  endings to LF while toggling `INTERIM_GHOST_TTL_MS` back to 1500 and
  forward again — the resulting commit (`9892cb1`) had a 713+/674- diff
  for a one-line content change, unreviewable, and had to be followed by
  a dedicated line-ending-fix commit (`1649e82`). Use the Edit tool
  (preserves line endings) or a full-file backup/restore (`cp` the file
  before toggling, `cp` it back after) instead — both of these were used
  safely for the same purpose on items 1 and 3 in the same session.
  `[resolved, see 9892cb1 + 1649e82]`

---

## 6. Ledger — SINGLE SOURCE OF TRUTH FOR CURRENT STATE

### 6a. Completed

| Commit | Date | `[core]` | What | Fixed by |
|---|---|---|---|---|
| `5001275` | 2026-08-05 | — | BUG-A..D: UtteranceEnd channel key, interim/final compare order, interim timeout arming, identity-linked post-commit corrections | prior session |
| `2012da8` | 2026-08-05 | — | BUG-E: append premature continuations instead of dropping them | prior session |
| `0a83a9c` | 2026-08-06 | — | BUG-G1/G2/H: forward interim results to lifecycle owner (`on_interim` wiring). Introduced the raw+lifecycle double delivery later documented in `5c48847` | prior session |
| `153f8b8` | 2026-08-06 | 2 | Fix `_merge_lexical` word-overlap gap surfaced by the interim wiring fix | prior session |
| `d7c1834` | 2026-08-07 | 3 | Log silently-swallowed `_dispatch_commit` callback failures (audit §2.1) | Claude Code |
| `25a6623` | 2026-08-07 | 2 | Tighten `_text_related` prefix overlap 8ch/50% → 12ch/65% (audit §3.4 row 5) | Claude Code |
| `1a32639` | 2026-08-07 | 2 | Add word-order check to `_merge_lexical`'s overlap branch (`difflib.SequenceMatcher`) (audit §3.4 row 7) | Claude Code |
| `98a6fa0` | 2026-08-07 | 2 | Bounded retry (3×, ~60ms, lock released) in `_resolve_correction_target_locked` (audit §1.1, 1/2) | Claude Code |
| `432dea1` | 2026-08-07 | 1 | Non-blocking re-queue retry in `duplicate_protection.py::_display_transcript_item` (audit §1.1, 2/2 — §1.1 now mitigated) | Claude Code |
| `78eb59e` | 2026-08-07 | 2 | Permanent interim ghost line: identity gate + liveness watchdog (audit §1.2 / §3.4 row 1). Tests: `tests/test_interim_ghost_line.py` (19) | Claude Code |
| `f402cc0` | 2026-08-07 | — | Docs: `ISSUE_1_INTERIM_GHOST_LINE_FIX_REPORT.md` | Claude Code |
| `b738ab4` | 2026-08-07 | — | Tooling: `tools/scan_interim_ghost_evidence.py` (has a known verdict-logic bug → item 3) | Claude Code |
| `5c48847` | 2026-08-07 | 1 | Documented (not fixed) `deepgram_client.py`'s double interim delivery (audit §3.9). Recreated `PROACTIVE_AUDIT_20260806.md` after finding it was never committed — **this is the incident §0 cites as the reason to verify this Ledger against git.** | Claude Code |
| `a5e2ac4` | 2026-08-07 | 1 | Stop `utterance_lifecycle.py` Case C's commit path from also publishing its final text through the interim-preview channel (`emit_interim` param). Found by live test *after* `78eb59e` — a 3rd independent source of the same visible symptom. Tests: `tests/test_commit_path_interim_emit.py` (6) | Claude Code |
| `114391b` | 2026-08-07 | — | Added this roadmap + `Bug Report.md` (Japanese findings 4.1-4.4) | Claude Code |
| `349ce1e` | 2026-08-07 | — | Rewrote roadmap to v4: grep anchors, restored 5 missing items, fixed cross-doc STATUS divergence | Claude Code |
| `6564b36` | 2026-08-07 | 3 | **Batch 1 item 1 DONE.** Removed dead zero-arg `_flush_pending_translation_submit()` call in `_begin_graceful_stop` — confirmed genuinely redundant vs. `flush_pending_translation_submissions()` (plural), not just broken. Tests: `tests/test_begin_graceful_stop_no_broken_flush.py` (1) | Claude Sonnet 5 |
| `9892cb1` | 2026-08-08 | 4 | **Batch 1 item 2 DONE.** `INTERIM_GHOST_TTL_MS` 1500 → 6000, per measured English (1924ms) and Japanese (4063ms) worst-case gaps. ⚠️ This commit's diff is a full-file rewrite of `constants.py` — see `1649e82` for why (process note, not a content issue). Tests: `TestInterimGhostTtlCalibration` added to `tests/test_interim_ghost_line.py` (2) | Claude Sonnet 5 |
| `1649e82` | 2026-08-08 | — | **Process fix, not a roadmap item.** Restored `constants.py`'s CRLF line endings, accidentally flipped to LF by a `sed -i` used mid-fix to toggle the TTL for before/after test verification. Zero content change (verified byte-identical after line-ending normalization). **Lesson recorded in §5: never use `sed -i` on this repo's `.py` files, even for a quick toggle — use Edit or a full-file backup/restore instead.** | Claude Sonnet 5 |
| `38a92b5` | 2026-08-08 | 4 | **Batch 1 item 3 DONE.** `tools/scan_interim_ghost_evidence.py::build_verdict()` now reports REVIEW (not PASS) when the watchdog-firings-to-decisions ratio ≥25% (`WATCHDOG_FIRING_RATIO_REVIEW_THRESHOLD`). Also resynced the tool's mirrored `INTERIM_GHOST_TTL_MS` literal (1500→6000, was left stale after item 2). Re-ran against the real run that had shown false PASS (`...20260807-153955`) — now correctly WARNs at 83%. Tests: `tests/test_scan_interim_ghost_evidence_verdict.py` (5) | Claude Sonnet 5 |

**BATCH 1 COMPLETE as of `38a92b5`.** All 3 items done, all with proven regression tests, baseline unchanged throughout (176→178→183 tests). Next: Batch 2 (§6c).

**Correction note on `5001275`:** the original draft of
`PROACTIVE_AUDIT_20260806.md` mislabeled
`main_window.py::_apply_final_interim_comparison` as "confirmed still
broken" by misreading this commit's BUG-B fix. The order-swap BUG-B
fixed here was **already correct** at audit time; what was genuinely
still broken (fixed in `78eb59e`) was a *different*, narrower defect —
the unrelated-text fallthrough default. See `PROACTIVE_AUDIT_20260806.md`
§1.2. Flagged here so no future agent re-litigates it from commit
messages alone.

### 6b. Live-test verification status (tracked separately from code status)

| Fix | Live-tested | Result |
|---|---|---|
| `78eb59e` | Yes, 2026-08-07 | Permanent ghost gone; surfaced the `a5e2ac4` flicker case |
| `a5e2ac4` | Yes, 2026-08-07 | `sf=True` interim events confirmed at zero. Watchdog then fired 10/12 decisions — **that is item 2 (TTL miscalibration), not a defect in `a5e2ac4`** |
| All others in 6a | Not individually live-tested | — |

### 6c. Pending — in execution order

Format per item: **`[core:N]`** · file · `grep:` anchor · `≈line` (as of
`114391b`) · what is wrong.

---

#### BATCH 1 — trivial, standalone, near-zero risk — ✅ COMPLETE (see §6a: `6564b36`, `9892cb1`, `1649e82`, `38a92b5`)

All 3 items done 2026-08-07/08. Kept below, struck through, for
traceability — do not re-do these.

~~**1. `[core:3]` — dead safety net that always throws**~~ **DONE, `6564b36`.**
~~`alpha/ui/main_window.py` · `grep: def _begin_graceful_stop`~~ Removed
the dead zero-arg call; confirmed genuinely redundant against
`flush_pending_translation_submissions()`.

~~**2. `[core:4]` — interim ghost watchdog TTL is miscalibrated**~~ **DONE, `9892cb1`.**
`INTERIM_GHOST_TTL_MS` 1500 → 6000.

~~**3. `[core:4]` — scan tool reports PASS when it should report REVIEW**~~ **DONE, `38a92b5`.**
Added `WATCHDOG_FIRING_RATIO_REVIEW_THRESHOLD = 0.25` to `build_verdict()`.

**Checkpoint (§3.10 live test) — NOT YET RUN.** Batch 1's code fixes are
in; the live-test confirmation that near-zero watchdog firings actually
occur in a *new* session (not a re-scan of old pre-fix log data) is
still outstanding. Whoever picks up Batch 2 should ask the human to run
one live session per language and check
`tools/scan_interim_ghost_evidence.py`'s output before assuming Batch
1's real-world effect is confirmed — the checkpoint is process, not
optional.

---

#### BATCH 2 — Core bug 3: make silent failures loud (logging only)

**Recommended model: Sonnet 5.** No dependency on Batch 1; may run in
parallel, but keep commits separate.

**4. `[core:3]`** `alpha/ui/main_window.py` · `grep: def _remove_interim_line_from_display` · ≈1301
`box.compare("interim_anchor", ...)` raises `TclError` on the normal
"nothing to remove" case, caught and logged as `remove_exception` every
time (14× in one short run). Replace with a
`if "interim_anchor" not in box.mark_names(): return` guard. Removes log
noise currently drowning real signal. Zero behavior change.

**5. `[core:3]`** `alpha/ui/main_window.py` · `grep: def _on_store_segment_updated` · ≈1426
Three swallowed `except Exception: pass` blocks around its side effects
(interim-line removal, stale-translation removal, translation resubmit).
Add logging to each. The store mutation has already committed by the time
these run, so a silent failure here leaves committed text with a
permanently missing translation.

**6. `[core:3]`** `alpha/transcription/utterance_lifecycle.py` ·
`grep: def reset_for_session` (≈309) and `grep: def _resolve_correction_target_locked` (≈391)
Both swallow exceptions with no logging at all. Add logging. **Do not
change fail-open/fail-closed behavior here.**

**7. `[core:3]`** — same pattern, six locations, add logging only:
- `alpha/transcription/deepgram_client.py` · `grep: def _normalize_and_send_pcm` (empty-bytes early return, no log)
- `alpha/transcription/pipeline_commit_transaction.py` · `grep: def _write_suppressed_stop_tail_candidate`
- `alpha/utils/stop_finalize_worker.py` · `grep: schedule_evidence_pointer_finalization_background`
- `alpha/transcription/japanese_sentence_assembler.py` · `grep: def _route_stable_publish` (stabilizer `process()` wrapped in silent except)
- `alpha/transcription/japanese_sentence_assembler.py` · the
  `transcript_snapshot_store` write block (`grep: append_transcript_snapshot`) — **zero logging today, not even to the Japanese accuracy log**, making it undiagnosable from the very evidence this project relies on
- `alpha/transcription/japanese_boundary_stabilizer.py` · `grep: def _update_evidence_index`

**7b. `[core:1]` — Japanese stabilizer exception falls into the English commit path** *(audit §2.10 — missing from v3)*
`alpha/transcription/deepgram_client.py` · `grep: stabilizer ingest error` · ≈1534
If `stabilizer.ingest(...)` raises for a Japanese-configured session, the
exception is caught and printed but **execution does not return** — it
falls through into the English/generic commit block below, feeding a
Japanese final into `utterance_lifecycle.on_final_chunk` (the
English-only controller). This is a behavior fix, not logging-only:
add the missing `return` (or an explicit fenced error path) so a
transient stabilizer failure cannot silently reroute a transcript into
the wrong language pipeline.

**8. `[core:3]`** `alpha/transcription/utterance_lifecycle.py` ·
`grep: return True, "unavailable"` · ≈388
`_observe_identity` fails **open** — on any exception it returns
"accepted", silently bypassing the one gate meant to prevent
duplicate/cross-utterance mutation, in a file whose entire stated design
is fail-closed. **Log only in this batch. Do NOT flip to fail-closed
here** — that is a real behavior change needing evidence first (item 27).

**9. `[core:4]` — data collection only, no fix**
`alpha/transcription/japanese_sentence_assembler.py` · quarantine logic
writing `accuracy/quarantine_decisions.jsonl`
Collect evidence across multiple Japanese live runs on how often
`later_committed_to_stable: false` occurs (confirmed real speech is being
dropped as `noise_fragment` — see `Bug Report.md` §4.2). Feeds item 34.
**Do not touch the threshold in this batch.**

**Checkpoint:** one live session per language. Confirm new log lines
appear, with no unexpected volume.

---

#### BATCH 3 — Core bug 2: identity & text-comparison hardening

**Recommended model: Opus 5** for items 10, 13, 17, 21; Sonnet 5 is
adequate for the rest (see §8). Depends on Batch 2's visibility.

Each is its own commit + regression test. Use the already-shipped
`_merge_lexical` (`1a32639`), `_text_related` (`25a6623`) and interim
identity gate (`78eb59e`) fixes as templates for both the fix shape and
the test shape.

**10. `[core:2]`** `alpha/ui/main_window.py` · `grep: def _check_stop_tail_duplicate` · ≈5610
**Highest severity in this batch.** Stop-time last-chance commit decided
via `norm_interim == norm_seg or norm_interim in norm_seg` and
`.startswith()` only. A reworded (non-substring) tail can be classified
`skip_already_committed` and dropped entirely — at Stop, with no second
chance.

**11. `[core:2]`** `alpha/ui/main_window.py` · `grep: def _should_commit_interim_recovery` · ≈4450
Same containment-only anti-pattern in the Stop-time recovery path.

**12. `[core:2]`** `alpha/ui/main_window.py` · `grep: def _should_repair_previous_segment` · ≈5072
Same pattern; misclassifies a reworded previous segment as
non-continuation.

**13. `[core:2]`** `alpha/transcription/duplicate_protection.py` · `grep: def decide_transcript_action` · ≈49
Residual gap (the historical `or True` bypass is already gone): a
substitution-style correction arriving with **no** authoritative
`lifecycle_decision` signal still falls through to `"add"` (duplicate
line) instead of `"update"`. Mitigated in the common case by the upstream
signal override; live whenever that signal is absent. Also contains two
dead `.startswith()` branches (unreachable — the `in` test two lines
earlier already subsumes them); remove them as part of this fix.

**14. `[core:2]`** `alpha/transcription/japanese_sentence_assembler.py` · `grep: def merge_japanese_fragments` · ≈597
`curr.startswith(prev)` / `prev.endswith(curr)` accepted as proof of full
subsumption *before* the smarter overlap search runs — can drop a
corrected-but-shorter retranscription that happens to be a literal
suffix.

**15. `[core:2]`** `alpha/transcription/japanese_sentence_assembler.py` · `grep: def _looks_like_speaker_continuation_tail` · ≈2573
Literal-prefix/phrase-containment list misclassifies a different
speaker's line that merely starts with an ordinary connective as a
continuation. **Do this before item 23** — that item's logic consumes
this function's output.

**16. `[core:2]`** `alpha/transcription/deepgram_client.py` · `grep: def teams_commit_decision_from_dup_action` · ≈626
A 4th independent instance of this anti-pattern class, living in the
ingestion layer. **Verify it is still diagnostic-only** (its output
should only feed logging, not a commit branch). Then rename or
explicitly mark it diagnostic-only so a future change cannot silently
wire it into a live decision path — which would reintroduce this bug
class a 5th time.

**17. `[core:2]`** — combined fix, same root cause:
`alpha/summary/transcript_store.py` · `grep: def update_last_segment` (≈71) and `grep: def get_last_segment` (≈97)
Delete or alias these unsafe speaker-only methods so only the safe
`..._if_active` variants (≈115, ≈135) remain callable, then update the
three call sites still using the unsafe versions:
`alpha/transcription/duplicate_protection.py` (`grep: transcript_store.update_last_segment`),
and two in `alpha/ui/main_window.py` (`grep: store.update_last_segment`).
The decision to write is made with the safe speaker-confirmed lookup but
the write itself goes through the unsafe reverse scan under a *separate*
lock acquisition — a check-then-act race that can silently revise a stale
row. Related: audit §2.7's note that even the "safe" variant is keyed by
speaker only with no channel/session key — record whether that remains
open after this fix.

**18. `[core:2]`** `alpha/summary/transcript_store.py` · `grep: def add_translation` · ≈158
Matches an incoming translation to its segment by **exact text
equality**, not record id. If the segment's text was revised between
request and response, the match silently fails and the translation is
dropped with no log. Move to record-id matching.

**19. `[core:2]`** `alpha/transcription/japanese_boundary_stabilizer.py` · `grep: def duplicate_continuation_ratio` · ≈233
Pure substring containment + positional character-match ratio, not
substitution-aware. Can silently drop a genuinely new short remark that
happens to be a literal substring of the previous line (e.g. a repeated
"ありがとうございました").

**20. `[core:2]` — canonical-key fields are decorative at the ingestion boundary** *(audit §1.3 — missing from v3)*
`alpha/transcription/deepgram_client.py` · `grep: segment_metadata = ` (≈1929) and `grep: def _commit_final_transcript_segment` (≈1472)
Two of `REPAIR_PLAN.md`'s six required canonical-key fields carry no
information: `channel_index` is Deepgram's `[channel, total_channels]`
pair and is **constant `[0, 1]`** for every event (the session always
requests mono) — and is serialized inconsistently (a real list in one
run, the *string* `"[0, 1]"` in another). `event_id` /
`deepgram_request_id` is Deepgram's **connection-level** `request_id`,
identical for every utterance in a session (confirmed: six different
utterances all carrying the same value). Any downstream code trusting
either as a disambiguator is trusting a constant. **This is the root
enabler of Core bug 2** — decide with the human whether to (a) mint a
real per-utterance provider id at ingestion, or (b) explicitly document
these as non-identifying and audit every consumer that trusts them.
Scope this as investigation + decision first; it may become its own
mini-batch.

**Checkpoint:** live test both languages with deliberately overlapping /
back-to-back short utterances. Compare
`IDENTITY_REJECTION` / `FALLBACK_BLOCKED` / quarantine rates against the
pre-Batch-3 baseline; confirm a measurable drop.

---

#### BATCH 4 — Core bug 1 (partial): concurrency & state-machine safety

**Recommended model: Opus 5** (see §8). Depends on Batch 3.

**21. `[core:1]`** `alpha/utils/stop_finalize_worker.py` · `grep: def _confirm_transcript_commits` · ≈1116
Computes `transcript_remaining` / `batch_remaining` and only **logs**
them — never compares to zero — so `commit_confirm_ok` is effectively
always `True` and a run can report `completed` with undrained transcript
items. The correctly-computed boolean already exists:
`alpha/utils/ui_stop_drain_barrier.py`'s `passed` field
(`grep: "passed":`), computed earlier in the same flow and then
discarded. Wire it in. **Smallest, highest-value item in this batch — do
it first.**

**22. `[core:1]`** `alpha/transcription/utterance_lifecycle.py` · `grep: int(active.speaker or 1) != int(speaker or 1)` · ≈1204
Defaults *both* unknown speakers to `1`, so two utterances with genuinely
different unknown speakers register as the same speaker (fail-**open**).
Every sibling module uses the shared `speakers_confirmed_same()` guard
(`alpha/transcription/speaker_boundary_guard.py`), which is fail-closed
on unknown. Switch to it. **Do before item 23** — that item needs the
same primitive.

**23. `[core:1]`** `alpha/transcription/utterance_lifecycle.py` · `grep: force_new=not same_active` · ≈1095 (Case B, `grep: # Case B` ≈1080; contrast Case C `grep: # Case C` ≈1101)
When an active **uncommitted** utterance exists and the new candidate is
on a different channel/speaker, `force_new` evaluates `False`, so the
merge branch runs unconditionally and overwrites `active.channel` /
`active.speaker` with the new event's values — silently concatenating two
speakers' text under one identity. Case C correctly gates on
`same_active` first; align Case B to match. Fires on ordinary
two-speaker overlap, which is common in real meetings.

**24. `[core:1]`** `alpha/utils/transcript_snapshot_store.py` · `grep: def revise_last_transcript_snapshot` · ≈79
Revises `_segments[-1]` (the literal last row) with **zero**
speaker/session/channel check — strictly weaker than `transcript_store`'s
equivalent, which at least filters by speaker. Add the same
speaker-confirmation guard.

**25. `[core:1]`** `alpha/ui/main_window.py` ·
`grep: def _commit_transcript_item_to_store` (≈5674) and `grep: def _try_segment_repair` (≈5164)
The UI layer independently mints its own `canonical_utterance_id` via
`uuid.uuid4()` (Japanese manual mode) and writes with no identity at all
(`_try_segment_repair`, English path — merges into "whatever is currently
last"). **Scope narrowly:** stop the independent minting and the
no-identity write. Do not attempt a full rewrite here — this is the
UI-side bridge into Batch 5, not Batch 5 itself.

**26. `[core:1]` — ledger has no internal staleness defense** *(audit §3.5 — missing from v3)*
`alpha/transcription/canonical_transcript_ledger.py` · `grep: def _revise_record_unlocked` · ≈482
Unconditionally overwrites `target["final_text"]` with no comparison
against `source_version`. Version-ordering protection exists **only** in
`canonical_identity_registry.observe_identity`, outside this file, called
by `duplicate_protection.py` *before* `execute_pipeline_commit`. Since at
least one direct-commit path still bypasses that flow (audit §3.2,
addressed in Batch 5), a late-arriving stale revision reaching the ledger
through that path would silently overwrite newer text. The module's own
docstring calls it the "single authoritative source" while delegating all
ordering safety to callers. Add an internal version guard.

**27. `[core:3]` — revisit, deferred from item 8**
`alpha/transcription/utterance_lifecycle.py` · `grep: return True, "unavailable"`
With logged evidence from Batches 2-4 now available, decide whether
`_observe_identity`'s fail-open can become fail-closed, or needs a
narrower condition. **Evidence-driven** — check how often item 8's log
line actually fired before deciding.

**Checkpoint:** `REPAIR_PLAN.md` Level 2 (component integration:
lifecycle → ledger → UI store → translation, no real APIs) and Level 3
(replay recorded event sequences) if a replay harness exists; otherwise a
15-30 minute live session per language including Start→Stop→Start.

---

#### BATCH 5 — Core bug 1 (full): single canonical controller for Japanese

**Recommended model: Opus 5 with extended thinking** (see §8). This is
where item **4.1** — the measured Japanese content loss — gets fixed.
Deliberately last. Treat as its own mini-project following
`REPAIR_PLAN.md`'s methodology, **not** a single surgical commit.

**28.** Capture deterministic replay fixtures from a session reproducing
item 4.1, **before touching any code** (`REPAIR_PLAN.md` Phase 0). Exact
reproduction on record (`Bug Report.md` §4.1): run
`v3.3.5.5.8.5.26.5.3-20260807-160529` — 10 assembler `commit_new`
decisions, 10 rows in `stable_commits.jsonl`, but only 9 canonical ledger
records; the sentence
`ですよ。違いますねでやっぱりこっちにいると日本の行事の不倫っていうのを味わうことが難しいので、`
is present in `stable_commits.jsonl` and absent from both the canonical
ledger and the final export.

**29.** Prove item 4.1's root cause against the fixture. The working
hypothesis is audit §3.2 (the assembler's Stop-tail direct
`execute_pipeline_commit` bypass and/or its post-decision persistence
side-writes running independently of the controller) — **prove it, do not
assume it.** It is entirely possible the real cause is elsewhere in the
handoff.

**30. `[core:1]` — accept or close the double interim delivery** *(audit §3.9 — disposition missing from v3)*
`alpha/transcription/deepgram_client.py` · `grep: def _handle_interim_deepgram_result`
Every interim is delivered to `on_interim_transcript` twice — once via
the lifecycle (carrying `canonical_utterance_id`) and once raw (carrying
none). Currently documented and worked around, not fixed. The raw call
**cannot simply be deleted**: Japanese sessions never use the lifecycle
path (`should_use_utterance_lifecycle()` is English/generic-only), and
even on the English path the lifecycle's `should_update_interim` is
`False` on several branches. A real fix means making one path the single
owner of interim delivery for both languages — the same "single
controller" change as items 31-32, applied to the preview path. Decide
here: fold it in, or formally accept it with the existing warning comment
as the permanent mitigation.

**31.** Design the assembler → controller handoff so every
`commit_new` / `update_previous` goes through exactly one path
(`accept_boundary_proposal`, already the pattern for the main commit
path) with **no side-write path that can silently diverge.**

**32.** Consolidate the three-way store overlap (audit §3.6):
`canonical_transcript_ledger.py` / `alpha/summary/transcript_store.py` /
`alpha/utils/transcript_snapshot_store.py` all independently "hold the
transcript." Same handoff touches all three — do it in the same change.

**33.** Also resolve audit §3.1 — `alpha/transcription/japanese_sentence_assembler.py` ·
`grep: def _resolve_output_speaker` (≈3256) *(missing from v3)*. It can
override the detected speaker with a "locked" dominant speaker when fewer
than 3 consecutive votes exist, including an explicit
`block_speaker2_to_speaker1_flip` rule; the **relabeled** speaker then
feeds the downstream same-speaker-extension check, so speaker B's turn
can be merged into speaker A's line — the exact outcome
`REPAIR_PLAN.md` forbids ("Japanese dialogue between two speakers must
never become one merged canonical line"). Depends on item 15.

**34. `[core:4]`** Decide the `noise_fragment` quarantine threshold using
item 9's collected evidence. Do not guess a new threshold without it.

**35.** Validate per `REPAIR_PLAN.md`: Level 3 (replay) → Level 4
(synthetic: 100+ utterances, repeated text, reordered callbacks,
duplicate finals, repeated Start→Stop→Start) → Level 5 (real live test,
both languages, 15-30 min).

**36. `[core:—]` Dead-code cleanup** *(audit Priority 4 — missing from v3)*.
Do this **last of all**, when everything above is green — removing code
is only safe once nothing else is in flight. `PROACTIVE_AUDIT_20260806.md`
§4.1 lists ~25 confirmed-dead functions/constants with zero callers
(verify each with a fresh repo-wide grep before deleting — several were
already removed by earlier work and the audit's list may be stale).
Highest-value entries: `canonical_transcript_ledger.py`'s public
`append_record` / `revise_record` / `suppress_record` wrappers, which
bypass every Phase-1 identity/lineage guarantee that `apply_decision`
enforces — dead today, but a trap for the next developer who reaches for
the obvious-looking name.

**37.** Re-run item 4.1's exact reproduction as final proof.

### 6d. → NEXT UP

**Batch 1 is complete** (items 1-3, commits `6564b36`/`9892cb1`/`1649e82`/`38a92b5`).
Its live-test checkpoint (§3.10) has **not** been run yet — consider asking
the human for one before or during Batch 2; it's not blocking, but it's
the only real-world confirmation that item 2's TTL recalibration reduces
watchdog firings in a fresh session (everything checked so far is either
unit tests or a re-scan of *old*, pre-fix log data).

**→ Batch 2, item 4** — `alpha/ui/main_window.py`,
`grep: def _remove_interim_line_from_display`. Start at §3 step 1
(investigate). See §6c Batch 2 for the full list (items 4-9, with 7b
flagged for Opus 5 per §8).

---

## 7. Definition of done — and an honest statement of what this guarantees

**When every item above is complete and its checkpoint passed, this
roadmap will have closed every bug that is currently *known* to exist**
— i.e. everything surfaced by the six-agent audit
(`PROACTIVE_AUDIT_20260806.md`), everything found in the live-test
follow-ups (`Bug Report.md` §4.1-4.4), and everything found while writing
this file.

**It cannot promise a percentage of "issue-free."** Stating a number like
97 % would be a guess, not a measurement. What can be said honestly:

- **What is covered:** every known transcript-loss, duplication,
  ghost-line, identity, ordering, and silent-failure defect on record,
  plus the architectural root causes behind them.
- **What is not covered — and why it cannot be:**
  - Bugs in code paths that have never fired in any captured run. This
    project has already been bitten by exactly this: `on_interim()`
    existed and looked correct for multiple sessions before anyone
    noticed it was never wired up. The audit explicitly flags several
    remaining branches with **zero** observed firings across all sampled
    runs (`accept_boundary_proposal`'s rejection paths, the cross-channel
    `UtteranceEnd` guard). They may be fine; nobody can currently tell.
  - Areas deliberately out of scope: audio capture/WASAPI, the mixer, the
    DeepL provider, packaging (frozen per `REPAIR_PLAN.md`).
  - Files never independently audited (`canonical_identity_registry.py`,
    `stable_line_revision.py`, `stable_revision_decision.py`,
    `language_pipeline_worker.py`, the translation coordinator) — they
    were referenced from other files' findings but not read end-to-end.
  - **Fixing any item can surface a new one.** This is not speculation:
    it has happened three times in this project already (`_merge_lexical`
    → `on_interim` wiring; `78eb59e` → `a5e2ac4`; `98a6fa0` →
    `432dea1`). Expect it, and re-run the checkpoints rather than
    assuming.

**The realistic claim:** completing this roadmap in order should take the
app from "known to silently lose content in both languages" to "no known
content-loss path, with the architecture that produced them removed and
observability in place to catch the next one quickly." That is a much
stronger position than a percentage — and it is verifiable, which a
percentage is not.

**To keep it true after this roadmap closes:** re-run the audit
methodology periodically (fresh log-vs-code cross-reference on new live
runs), and keep updating §5/§6 as new findings appear. This file is
designed to keep working as a living document, not to be archived when
item 37 is done.

---

## 8. Model selection guidance (Claude Code)

Recommended per batch, based on the reasoning depth each actually
requires. Opus 5 is the most capable model available; Sonnet 5 is faster
and cheaper and is genuinely sufficient for mechanical work. The
**method in §3 is what protects correctness** — model choice affects how
efficiently you get there, not whether the process is safe.

| Batch | Work type | Recommended | Why |
|---|---|---|---|
| **1** (items 1-3) | One-line fix, one constant, one tool function | **Sonnet 5** | Mechanical, fully specified here, immediately verifiable. Opus adds nothing. Item 2 needs a human sign-off on the number, not model depth. |
| **2** (items 4-9) | Add logging to known locations | **Sonnet 5** | Repetitive and mechanical. **Exception: item 7b** (the missing `return` that reroutes Japanese into the English pipeline) is a real behavior change — use **Opus 5** for that one. |
| **3** (items 10-20) | Text/identity comparison semantics | **Opus 5** for 10, 13, 17, 20; **Sonnet 5** for 11, 12, 14, 15, 16, 18, 19 | 10 and 13 require reasoning about which correction shapes must survive; 17 is a multi-call-site refactor with a race condition; 20 is an architectural decision. The rest follow templates already in the repo. |
| **4** (items 21-27) | Concurrency, state machine, fail-open policy | **Opus 5** | Race conditions, lock ordering, and asymmetric state transitions — the failure modes here are subtle and are not reliably caught by tests alone. Highest value per unit of model capability. |
| **5** (items 28-37) | Architectural rewrite + validation | **Opus 5, extended thinking** | Multi-file redesign of the commit path, three-store consolidation, and a full `REPAIR_PLAN.md` Level 3-5 validation campaign. This is the one place where model capability materially changes the outcome. |

**Practical guidance regardless of model:**
- Never let the model skip §3 step 5 (proving the test catches the bug).
  This has caught real mistakes and is cheap on any model.
- If a session's investigation contradicts this file, **stop and
  reconcile** rather than pushing forward — on any model.
- One item per session where practical. Long sessions accumulate context
  drift, and this file exists precisely so that a fresh session costs
  almost nothing to start.
