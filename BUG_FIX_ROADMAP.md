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

1. **Locate the repo.** Do **not** trust a hardcoded absolute path — this
   repo has already been moved between Windows user accounts once (see
   §2.1). Determine the root from your own working directory, and confirm
   `BUG_FIX_ROADMAP.md` + `Alpha_Live_Translator\` are both in it.
   Active app code is under `Alpha_Live_Translator\`. **Never modify
   anything under `_archive\`** (project convention, stated in the repo's
   own `CLAUDE.md`; note `_archive\` does not currently exist in the tree).
2. **Read `CLAUDE.md`** at the repo root for standing project restrictions.
3. **Read this entire file, top to bottom.**
4. **Reconcile the Ledger against git.** Run `git log --oneline -30` from
   the repo root and compare against §6a/§6b. If the Ledger claims
   something is done with no matching commit — or a commit exists that
   the Ledger does not reflect — **stop and fix this file first**, and
   log the discrepancy in §5. Do not start new work on an unreconciled
   ledger. **If `git` is not installed, read `.git\logs\HEAD` directly
   instead — see §2.7.**
5. **Establish the test baseline.** Run the full suite (§2.2) once before
   any new work and compare to §2.3. If it does not match exactly, **stop
   and tell the human** — something changed outside this roadmap's
   process and must be explained before anything is built on top of it.
6. **Go to §6d ("→ NEXT UP")**, then follow §3 for that item.

---

## 2. Project facts (established — not assumptions)

### 2.1 Paths
- Repo root: **machine-dependent — derive it, do not hardcode it.**
  The repo was authored under `C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0`
  and as of 2026-08-09 lives at
  `C:\Users\haquemdshafieh\Documents\Tariqul\Alpha_Translator V 1.0`.
  Any absolute path written in this file may be stale for the same reason
  line numbers are (§2.4). Treat `<repo_root>` as a placeholder you resolve
  yourself.
- Active project folder: `Alpha_Live_Translator\` — **all file paths in
  §6 are relative to this folder** unless stated otherwise.
- Python venv: `<repo_root>\.venv\Scripts\python.exe`. The project's real
  dependencies (customtkinter, deepl, PyAudioWPatch, websocket-client,
  numpy…) exist **only** here. The bare system `python` will fail with
  `ModuleNotFoundError`. Use the venv interpreter for anything that
  imports `alpha.*`. (Plain `python -c "import ast; ast.parse(...)"`
  syntax checks are fine with system Python since they import nothing.)
- **If the venv fails with `did not find executable at ...`:** `.venv` is
  not relocatable — `pyvenv.cfg` stores the absolute path of the Python
  that created it. After a move to a different user account, repoint
  `home` / `executable` in `<repo_root>\.venv\pyvenv.cfg` to the local
  Python **3.14** install (`py -0p` to find it). `site-packages` survives
  the move intact, so a full recreate is not needed as long as the local
  Python is the same 3.14.x. `.venv/` is gitignored, so this edit does not
  affect the working tree's git status. Done once on 2026-08-09 (§5).

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

**Re-verified 2026-08-09 at `0db8cbc` (HEAD, post-Batch-3-item-10):
220 tests, 5 failures + 2 errors + 2 skipped — same exact 7 names, skips
still 2.** The growth 175 → 220 is fully accounted for by the regression
tests each completed item added, so the baseline is intact and Batch 3
may continue from item 11.

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

**⚠️ The git CLI is not installed on every machine this repo lives on.**
On 2026-08-09 the machine had **no `git` executable at all** even though
`.git\` was present and intact. **Resolved the same day** — Git 2.55.0.3
installed via `winget install --id Git.Git -e`, and `user.name` /
`user.email` set **`--local`** (they were unset, so any commit would have
failed) to the identity the rest of this repo's history uses. Keep the
workaround below: the situation recurs on every fresh machine, and a new
install has no identity configured.

Two notes for a freshly-installed git on Windows:
- **PATH is not live in an already-running shell.** Refresh it in-process
  with `$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine")
  + ";" + [Environment]::GetEnvironmentVariable("Path","User")`, or call
  `C:\Program Files\Git\cmd\git.exe` by full path.
- **A `[graphify hook]` warning prints on every commit** ("could not
  locate a Python with graphify installed"). It is a pre-existing repo
  hook that cannot find its tool on this machine; it does **not** block
  the commit and is not caused by your change. Do not "fix" it as part of
  a roadmap item.

Consequences of having no git, and the workaround:

- **Reading history still works without git.** `.git\logs\HEAD` is the
  reflog in plain text — one line per HEAD movement, `<old> <new> <author>
  <unix-ts> <tz> <action>: <message>`. Reading its tail is an adequate
  substitute for `git log --oneline -30` for §1 step 4's reconciliation.
  `.git\refs\heads\main` holds the current commit hash. This is how the
  2026-08-09 reconciliation was performed.
- **Committing does not work without git.** Any item finished on such a
  machine cannot complete §3 step 7. **Do not silently skip it and mark
  the item done** — that is exactly the ledger drift §0 warns about.
  Instead: finish steps 1-6, record the item in §6 with the in-progress
  row format, note "commit pending — no git on this machine", and tell
  the human so they can install git or commit from another machine.

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
- 2026-08-09 — **Batch 3 items 14, 15, 16 were done out of order** (12
  and 13 skipped over on user request). §4's "do not reorder without
  writing the reason" — the reason: 12/13 need Opus-5-level judgment
  about which correction shapes must survive (per §8), 14/15/16 are
  independent, template-following, Sonnet-5-appropriate items in
  different files with no shared state, so skipping ahead carried no
  cross-item risk. 12 and 13 remain open in §6c, unaffected.
- 2026-08-09 — **New finding while doing item 13, deliberately NOT fixed
  (out of that item's scope): `decide_transcript_action`'s
  `if curr_n in prev_n: return ("skip", None)` is a 5th instance of the
  any-position containment anti-pattern, and this one DROPS the incoming
  final entirely.** `[core:2]`,
  `alpha/transcription/duplicate_protection.py` ·
  `grep: def decide_transcript_action`. Identical in shape to items 10,
  11, 12 and 19: a short new final that happens to be an interior
  substring of the previous committed line for that speaker is skipped,
  not committed. Item 13's write-up only covered the substitution gap and
  the two dead branches, so narrowing this was left alone rather than
  smuggled in as a drive-by (§3 step 4). **Two things to know before
  fixing it:** (a) item 13 removed a now-dead `prev_n.startswith(curr_n)`
  branch that this check used to subsume — narrowing this one makes that
  case fall through to `("add", ...)` instead of `("skip", None)`, which
  must be an intentional decision, not an accident; (b) unlike 10/11/19
  the fix direction here is unambiguous (skip = drop = loss), so
  prefix-or-suffix narrowing applies cleanly. **Propose as a new Batch 3
  item — Sonnet 5 is sufficient given 10/11/12/19 already set the
  template.** `[open]`
- 2026-08-09 — **`test_task9_report.Issue3RealThreadIntegrationTest.test_inactivity_timeout_fallback_survives_immediate_real_stop_5x` flaked once** during item 12's full-suite verification (real Tk root + real threads + real timers, explicitly designed to run 5x to catch timing flakiness — it's sensitive to system load when run inside the full ~250-test suite). Per §2.3's own rule ("if a different test fails, treat it as a real regression... never assume") this was **not** waved off on assumption: re-run in isolation (exit 0, pass) and the full suite was re-run once more clean (exactly the same 7 named baseline failures, nothing else). Confirmed flake, not a regression from item 12 (unrelated file/subsystem — item 12 only touches `main_window.py` text-comparison logic). **If this test starts failing consistently, treat that as real and investigate — this note does not grant it a permanent pass.**
- 2026-08-09 — **Batch 3 items 18, 19 also done out of order** (17
  skipped over this time, same reasoning: it's a multi-call-site refactor
  with a race condition, Opus-5-appropriate per §8, unlike 18/19's
  contained, single-module changes). 17 remains open in §6c, unaffected —
  it does not share state with either 18 (a different file,
  `transcript_store.py`'s translation matching) or 19 (a different file,
  the boundary stabilizer's ratio function).
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
- 2026-08-09 — **The same line-ending hazard has a second mechanism:
  Python's `pathlib.Path.write_text()` (and any `open(..., "w")`) on
  Windows translates every `\n` to `\r\n` on write.** Item 17 used a
  short Python script to do a mechanical rename across a few test files;
  `tests/test_task2g_acceptance_gate.py` happened to be **LF**, so it came
  back CRLF and commit `43374ad` showed a 604-line unreviewable diff for
  an 8-line edit. Fixed by a dedicated line-endings-only commit
  (`5d4578a`), content verified byte-identical. **This repo is
  mixed-ending — some files are CRLF, some LF — so there is no single
  "correct" setting to normalize to.** Prefer the Edit tool for `.py`
  edits. If a script really is needed, read and write in **binary**
  (`read_bytes`/`write_bytes`) so endings pass through untouched, and
  always check `git show --stat` before committing: a diff much larger
  than the edit is the tell. `[resolved, see 43374ad + 5d4578a]`
- 2026-08-09 — **The repo has moved to a different Windows user account**
  (`islamm` → `haquemdshafieh`), and three things broke as a result. None
  is an app bug; all three block this roadmap's own mandatory process, so
  they are recorded here rather than as §6c items.
  1. **`.venv` was dead.** `pyvenv.cfg` still named
     `C:\Users\islamm\AppData\Local\Python\pythoncore-3.14-64\python.exe`,
     so every invocation failed with `did not find executable at ...`.
     Since §2.2's test command *is* the venv interpreter, **§1 step 5 and
     §3 step 6 were both unrunnable** — i.e. no item could have been
     verified on this machine. Repointed `home`/`executable`/`command` to
     the local 3.14.6 install; `site-packages` was intact, so no recreate
     was needed. Procedure now written into §2.1 so the next move is a
     two-minute fix instead of a rediscovery. `[resolved]`
  2. **`git` was not installed on this machine at all.** History was
     reconciled by reading `.git\logs\HEAD` instead (see the
     reconciliation result below), which worked, but committing was
     impossible — item 11 sat finished-but-uncommitted for part of the
     session. **Resolved:** Git 2.55.0.3 installed via winget, identity
     set `--local`. The real `git log` was then re-run and **confirmed
     the reflog-derived reconciliation was correct in every particular**,
     so the fallback method in §2.7 is validated, not just plausible.
     `[resolved]`
  3. **Two absolute `C:\Users\islamm\...` paths in this file** (§1 step 1,
     §2.1) were stale and would have sent a fresh agent to a nonexistent
     directory. Replaced with "derive it yourself" guidance. **Same lesson
     as the 2026-08-07 line-number entry above, one level up: absolute
     paths in this repo's docs are as unreliable as line numbers.**
     `[resolved]`
- 2026-08-09 — **Ledger/git reconciliation result (§1 step 4): clean.**
  `refs/heads/main` = `0db8cbc` ("Update roadmap ledger: Batch 3 items 9c
  and 10 done"). Every commit §6a claims exists in the reflog with a
  matching message, in the stated order. The only reflog entries absent
  from §6a are this file's *own* ledger-maintenance commits (`df4c087`,
  `b64d723`, `fbf86b0`, `c8fb830`, `0db8cbc`) — self-referential by
  nature, not drift. One orphan, `21300bd`, was amended away into
  `153f8b8` by a prior session and is not in the branch. **Baseline
  re-verified the same day: 220 tests, the same exact 7 failures/errors,
  2 skips (§2.3).** State is exactly what §6d says it is: Batches 1-2
  complete, Batch 3 items 9c and 10 done, item 11 next.
- 2026-08-09 — **The repo's `CLAUDE.md` contains an unfilled placeholder.**
  Its last line reads: *"Do not spawn Explore subagents. Read only these
  exact files directly: `[file list]`. Do not search or explore the rest
  of the repo."* — `[file list]` was never substituted with actual
  filenames. As written the restriction is unfollowable (it names no
  files) and, read literally, forbids the repo-wide grepping that §2.4
  and §3 step 1 of *this* file require. Flagging rather than editing:
  `CLAUDE.md` is a tracked, human-owned instruction file. **The human
  should either fill in the intended file list or delete that sentence.**
  Until then, treat §2.4's grep-anchor method as the operative
  instruction, since every item in §6c depends on it. `[open — human
  decision]`
- 2026-08-09 — **New finding while investigating item 11, deliberately not
  fixed: `_should_commit_interim_recovery`'s `len(norm_interim) < 20`
  guard is an unmeasured threshold that silently drops short closing
  utterances at Stop.** `[core:4]`, `alpha/ui/main_window.py` ·
  `grep: return False, "too_short"`. It is not the containment
  anti-pattern item 11 names, so it was left alone per §3 step 4, but it
  is arguably the *more* damaging of the two drop-paths in that function:
  - English: `"thank you very much"` normalizes to 19 chars → **dropped**.
  - Japanese: `_normalize_compare` routes through
    `_compact_japanese_for_compare`, so 20 *compacted* chars is a
    substantial sentence. `ありがとうございました` compacts to 11 →
    **dropped**. This plausibly contributes to the Japanese content loss
    tracked in `Bug Report.md` §4.1/4.2.
  - It fires *after* item 10's filter has already returned
    `commit_new_tail`, i.e. it can discard a tail that the first filter
    explicitly judged worth committing.
  **Do not guess a replacement number** — same rule as item 34: derive it
  from `logs/async_debug.log`'s `[INTERIM] stop tail skipped
  {"reason": "too_short"}` events across real runs, per language. Schedule
  as its own item alongside item 34's threshold work. **2026-08-09 update:
  still no usable data.** Both of that day's runs logged `skip_too_short`
  with `latest_interim_len: 0` — a trivially correct skip of an empty
  interim, which tells us nothing about the 20-char cutoff. The
  measurement needs runs that actually end with a short non-empty tail.
  `[RESOLVED — see item 11c, c43f57b]` The measurement was obtained a
  different way: instead of waiting for `too_short` events (which never
  fire, because the tail is usually already empty), every `[INTERIM]
  received` event across all 27 run folders was normalized with the real
  `compact_cjk_for_compare` and tabulated per language. n=2210. That gave
  the distribution directly and showed the floor was mis-sized for CJK by
  roughly a factor of five. **Method note worth reusing: when the event you
  want to count never fires, measure the population it would have judged.**
- 2026-08-09 — **`test_task9_report.py::test_inactivity_timeout_fallback_survives_immediate_real_stop_5x`
  is flaky under CPU load. It is not in §2.3's baseline 7 and it is not a
  regression — do not chase it.** It failed twice while item 11b was being
  verified, which cost a full stash/control cycle to clear. Evidence: a 6×
  control run on stashed (pre-change) code passed 6/6; a 6× run *with* the
  change also passed 6/6; and two later full-suite runs were clean with
  the change in. The failures clustered immediately after a full-suite run,
  i.e. with the machine still loaded. It is a real-Tk-root, real-thread,
  real-`.after()`-timer test with bounded real-time waits
  (`_commit_fallback_ms = 60`, a 20s deadline), so it slips when the
  machine is busy. **If you see it fail: re-run it alone before assuming
  anything.** If it ever fails on an idle machine, that *is* worth
  investigating.
- 2026-08-09 — **NEW, pre-existing, not a regression: on English runs the
  canonical-ledger file is never written, and the export coverage gate
  then reports a perfect pass on a zero denominator.** `[core:3]`
  `transcripts/canonical_transcript_ledger.jsonl` is **0 lines** on every
  English run checked — today's `...034448` *and* the pre-item-10/11
  control `...155842`, so this predates Batch 3 and no roadmap item caused
  it. Japanese runs write it normally (25 and 26 lines respectively).
  The consequence is in `accuracy/export_coverage_report.json`, which for
  English reads:
  `source_commit_observed_count: 0`, `canonical_export_line_count: 0`,
  and nonetheless `coverage_ratio: 1.0`, `export_lossless: true`,
  `clean_export_ready_for_scoring: true`. **It is 0/0 reported as a
  perfect pass.** Meanwhile `evidence_streams/canonical_commits.jsonl`
  holds 12 real `append` commits and `Alpha_output_FINAL.txt` holds 12
  lines — the data exists, the gate is simply reading a file nothing
  populates on this path.
  `Bug Report.md` §4.1 already warns that `coverage_ratio: 1.0` only
  compares canonical → final and so cannot see a record lost *before* the
  ledger. For English it is weaker still: it compares **nothing at all**
  and still certifies the export lossless. This directly undercuts
  `REPAIR_PLAN.md` Phase 4's acceptance gate ("Raw counts, canonical
  counts, UI counts, and export counts reconcile") — for English that
  reconciliation is vacuous. Related to item 21, which fixes a different
  required-step check that also reports success without comparing what it
  computed. **Do not treat any English run's `coverage_passed` as
  evidence until this is fixed.** Needs its own item; likely belongs with
  Batch 4 or the Batch 5 store consolidation (item 32).

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

| `f3e251f` | 2026-08-08 | — | Ran Batch 1's outstanding live-test checkpoint (English + Japanese) — confirmed watchdog firing rate dropped to 2% (en) / 0% (ja) vs. 83% before the fix, in fresh live sessions (not re-scanned old data). Also documented (not fixed) a new, separately-reported issue as item **9b**: Start-button multi-second UI-thread freeze. Full evidence in `Bug Report.md` §4.5. | Claude Sonnet 5 |
| `ddeb67f` | 2026-08-08 | 3 | **Batch 2 item 4 DONE.** `_remove_interim_line_from_display` guarded on `"interim_anchor" in box.mark_names()` instead of catching the `TclError` `box.compare()` raised on the normal "nothing to remove" case (was logged as `remove_exception` 14×/run). Tests: `tests/test_remove_interim_line_no_exception_noise.py` (3) | Claude Sonnet 5 |
| `5181b8c` | 2026-08-08 | 3 | **Batch 2 item 5 DONE.** Logged all 3 previously-silent except blocks in `_on_store_segment_updated` (interim-line removal, stale-translation removal, translation resubmit) via `jp_accuracy_log`, 3 distinct event names. Swallow behavior unchanged. Tests: `tests/test_on_store_segment_updated_logs_failures.py` (5) | Claude Sonnet 5 |
| `38c6096` | 2026-08-08 | 3 | **Batch 2 item 6 DONE.** Logged `utterance_lifecycle.py`'s `reset_for_session` (identity-registry reset failure) and `_resolve_correction_target_locked` (falls back to unverified raw target on any exception) — both previously silent. Tests: `tests/test_utterance_lifecycle_logs_swallowed_failures.py` (4) | Claude Sonnet 5 |
| `07d5234` | 2026-08-08 | 3 | **Batch 2 item 7 DONE.** Logged the remaining 6 silent except-blocks across `deepgram_client.py`, `pipeline_commit_transaction.py`, `stop_finalize_worker.py`, `japanese_sentence_assembler.py` (×2), `japanese_boundary_stabilizer.py`. Tests: `tests/test_batch2_item7_silent_failure_logging.py` (6, 4 exercise the real call path, 2 verify the log-call/helper wiring directly — documented why in the test file). | Claude Sonnet 5 |
| `ec38779` | 2026-08-08 | 3 | **Batch 2 item 8 DONE.** Logged `_observe_identity`'s fail-open path (`OBSERVE_IDENTITY_FAILED_OPEN`). Fail-open behavior deliberately unchanged — flipping it is item 27, gated on evidence from this logging. Tests: `tests/test_observe_identity_logs_fail_open.py` (3, explicitly asserts the fail-open return value is unchanged) | Claude Sonnet 5 |
| (doc only, no commit hash yet — see next commit) | 2026-08-08 | 4 | **Batch 2 item 9 DONE (investigation only, no code change, per its own scope).** Scanned every `quarantine_decisions.jsonl` to date: 2 of 2 quarantine events ever recorded were `noise_fragment` misclassifications of real Japanese speech, 0 of 2 ever recovered. n=2 is too small to derive a replacement threshold — feeds item 34, threshold itself untouched. Findings appended to `Bug Report.md` §4.2. | Claude Sonnet 5 |

| `b220c86` | 2026-08-08 | — | **Batch 2 item 9b DONE.** Root cause: class-level monkey-patch (`install_japanese_stabilizer_hooks`, installed once at app startup via `main.py`) called `audio_temp_capture.cleanup_old_audio_temp(reason="start_listening")` directly/synchronously inside `_start_listening_worker` — iterates every historical run folder, cost grows with total run count (dozens accumulated). Swapped to the already-existing, already-Stop-tested `schedule_audio_cleanup_non_blocking`. `Bug Report.md` §4.5 updated with the precise root cause (supersedes the original "suspected `begin_live_session` on UI thread" hypothesis). Tests: `tests/test_start_listening_audio_cleanup_non_blocking.py` (1) | Claude Sonnet 5 |

| `6726f68` | 2026-08-08 | 1 | **Batch 2 item 7b DONE** (audit §2.10). Split the shared try/except in `_commit_final_transcript_segment` so neither a stabilizer-ingest failure nor a language-detection failure can fall through into the English/generic block — both now publish directly and log. Investigation corrected the audit's characterization: the *common* pre-fix outcome was bypassing the Japanese assembler (raw fragment committed), not entering the English lifecycle; the lifecycle-contamination path §2.10 describes was the rarer one (broken guard + unset `_listen_language`) and is what the detection-failure branch change actually closes. Tests: `tests/test_japanese_stabilizer_failure_no_english_fallthrough.py` (4) | Claude Opus 5 |

**BATCH 2 COMPLETE as of `6726f68`.** All items (4-9, 7b, 9b) done, each with a regression test proven to fail without its fix. Baseline unchanged throughout (183→209 tests). Next: Batch 3 (§6c).

| `a1c346d` | 2026-08-08 | — | Recorded the post-Batch-2 live-test results (§6b) and filed the new item 9c | Claude Opus 5 |
| `13f20ca` | 2026-08-08 | 3 | **Batch 3 item 9c DONE.** Translation-gap self-heal was inert: `reconcile_translation_gaps` passed the canonical ledger's `sequence_number` as the translation worker's `segment_id`, two unrelated counters occupying the same range, so a genuinely untranslated utterance was rejected as a "duplicate" of an unrelated job. Now allocates from `host._translation_segment_seq` like every other submitter, and distinguishes rejection causes via the worker's public `get_counters()` so "already delivered" counts as resolved while "worker shut down" still fails. **Also corrected my own earlier diagnosis in this file — the gap was real content loss, not the false positive I first recorded.** Tests: `tests/test_translation_reconciliation_segment_id.py` (4) | Claude Opus 5 |
| `b404c19` | 2026-08-08 | 2 | **Batch 3 item 10 DONE.** `_check_stop_tail_duplicate` dropped the Stop-time interim tail on ANY substring match against ANY of the last 5 segments (`commit_text=None` = permanent loss on the last-chance path). Narrowed to equality-or-prefix, the only shapes that actually evidence "already committed"; interior-only matches now fall through and commit. Tests: `tests/test_check_stop_tail_duplicate_containment.py` (7) | Claude Opus 5 |
| `b2b39de` | 2026-08-09 | 2 | **Batch 3 item 11 DONE.** `_should_commit_interim_recovery` refused the Stop-time tail whenever it appeared anywhere inside the last committed final (`norm_interim in norm_final`). Narrowed to equality-or-prefix, exactly mirroring item 10 — this is the *second* filter on the same last-chance path, so both had to be fixed for a tail to survive Stop. Also removed the same function's unreachable trailing `return False, "no_match"` (approved as part of this item, not a drive-by: a dead drop-path in a function whose every other drop-path is permanent loss). Tests: `tests/test_should_commit_interim_recovery_containment.py` (9), proven against pre-fix code — exactly the 2 containment tests fail, the other 7 pin unchanged branches. Full suite 229 tests, baseline unchanged. | Claude Opus 5 |
| `69605cc` | 2026-08-09 | 1 | **Batch 3 item 11b DONE** *(new — found by analyzing the Batch 3 checkpoint runs, not in the original audit)*. **This is what makes items 10 and 11 reachable at all.** The interim ghost watchdog cleared `_latest_interim_text` — the only source `_recover_interim_tail_on_stop` reads — so a tail orphaned shortly before Stop was destroyed by the **display** layer before the **content**-recovery path ran. Confirmed live: run `...033339` cleared a 10-char interim at +267.19s, Stop ran at +268.25s and found nothing; that speech is absent from the final export. `Bug Report.md` §4.3 predicted this interaction and asked that it be checked by whichever track touches Stop-time recovery. The watchdog now stashes the orphan (text/speaker/id/timestamp) before clearing and Stop falls back to it; the visible ghost line still goes immediately, so `78eb59e`/`a5e2ac4` are untouched. Whether the orphan is *safe* to commit stays items 10/11's job, by design. A supersession guard drops it if any newer interim arrived after it was stashed, so stale text can never be appended out of order. Tests: `tests/test_watchdog_orphan_stop_tail_recovery.py` (9), both halves proven separately against pre-fix code. Full suite 238, baseline unchanged | Claude Opus 5 |
| `5ffb18d` | 2026-08-09 | 14, 15, 16 | **Batch 3 items 14/15/16 DONE** (12, 13 intentionally skipped over — user approved 14/15/16 out of order; not a dependency issue, see §4). **14:** `merge_japanese_fragments`'s `prev.endswith(curr): return prev` fast path removed — proven a no-op vs. the overlap search for curr ≤32 chars, only mattered for curr >32 chars where it silently discarded the whole fragment with no trace; static fix, 0/114 real merge events scanned showed this firing. **15:** `_SPEAKER_LOCK_CONTINUATION_PREFIXES` had 5 ordinary connectives (なんだけど/それが/だから/でも/けど) misread as same-speaker evidence; removed, kept the one specific phrase. **16:** confirmed `teams_commit_decision_from_dup_action` diagnostic-only by full control-flow trace, renamed to `..._diagnostic_only`. Tests: `tests/test_batch3_items_14_15_16.py` (9) — before/after control caught my own first-draft item-14 test asserting `curr in merged`, which passes either way since curr is a literal substring of prev by construction; corrected to assert growth. Full suite 238 tests, baseline unchanged. | Claude Sonnet 5 |
| `0aa6a8f` | 2026-08-09 | 18, 19 | **Batch 3 items 18/19 DONE.** **18:** `TranscriptStore.add_translation` matched by exact text equality only — a segment revised between translation request and response silently dropped its translation, no log. `canonical_utterance_id` was already in scope at every real call site but never threaded through; added the field to `TranscriptSegment`, wired it from `main_window.py` through `duplicate_protection.py` into the store, id-match now tried first (falls back to the old text match when no id supplied; logs `TRANSLATION_STORE_ID_MATCH_NOT_FOUND` and returns, does not fall back, when an id is supplied but not found — avoids reintroducing the same silent-drop risk through the back door). **19:** `duplicate_continuation_ratio`'s `cur_c in prev_c: return 1.0` (full duplicate on ANY substring match, suppressed outright by callers at ratio≥0.95) narrowed to prefix-or-suffix-of-previous — the only shapes that evidence a genuine truncated re-send. No live occurrence found for 19 (0 `DUPLICATE_CONTINUATION_SUPPRESSED` events scanned), static fix like item 14. Tests: `tests/test_batch3_items_18_19.py` (7), proven against pre-fix code (3/7 fail: 2 errors from the new kwarg not existing yet, 1 failure from the containment case). Full suite 245 tests, baseline unchanged. | Claude Sonnet 5 |
| `af6781e` | 2026-08-09 | 12 | **Batch 3 item 12 DONE.** `_should_repair_previous_segment`'s `norm_curr in norm_prev` (any substring, anywhere) narrowed to prefix-or-suffix of previous. Traced the actual severity via `_try_segment_repair`'s caller: `current` is never dropped when `should_repair=False` — it still commits as its own segment downstream — so unlike 10/11/19 this was a missed-merge/transcript-quality bug (previous stays uncorrected as a separate duplicate-looking line), not silent content loss. Tests: `tests/test_should_repair_previous_segment_containment.py` (4), proven against pre-fix code (1/4 fails, the coincidental-middle-match case). Full suite 249 tests, baseline unchanged. | Claude Sonnet 5 |
| `8f19afe` | 2026-08-09 | 13 | **Batch 3 item 13 DONE.** A substitution-style correction ("...three million" → "...four million") is neither containment nor extension, so `decide_transcript_action` fell through to `"add"` and the transcript kept BOTH the wrong line and the correction. Fixed **identity-only, never text similarity**: converting `add`→`update` REPLACES the stored line, so a wrong guess destroys committed speech — the opposite and worse direction from 10/11/12/19. Two distinct utterances can be near-identical ("the first quarter was strong" / "the second quarter was strong"); only a matching `canonical_utterance_id` proves a revision. **This was only solvable now because item 18 (`0aa6a8f`) added that field to `TranscriptSegment`** — the call-site comment had explicitly named its absence as why a weaker registry check was used. Also removed the two dead `.startswith()` branches (proven no-ops: each returned the same value as the check that subsumed it). Tests: `tests/test_same_utterance_substitution_update.py` (9), proven against pre-fix code. Full suite 267 tests, baseline unchanged. | Claude Opus 5 |
| `43374ad` (+ `5d4578a`) | 2026-08-09 | 17 | **Batch 3 item 17 DONE.** Every write site decided from one lookup and wrote through `update_last_segment`'s reverse scan under a **separate** lock acquisition — an append in between made the write land on an older row than the one read. Fixed all three writes plus **one unsafe read the item did not list** (`_commit_transcript_item_to_store`'s `get_last_segment(speaker)`); all four now use `..._if_active`. Unsafe methods **renamed, not deleted** (`..._unsafe_speaker_scan`) — `test_task2g_acceptance_gate.py` deliberately pins them to document the safe/unsafe delta, so deleting would have destroyed that evidence; the rename still satisfies "not reachable by reflex". **The Stop-tail site ignored the return value**, so a bare swap would have silently dropped the merged tail on the last-chance path — it now appends and logs `STOP_TAIL_MERGE_APPENDED_NOT_UPDATED`. One real behavior change beyond the rename: `previous_text` goes `None` when the last row is another speaker's; every consumer fails safe (worst case a visible duplicate, never a wrong cross-speaker merge). Tests: `tests/test_transcript_store_unsafe_variants_retired.py` (8), incl. one that drives the old path to prove the race was real, not theoretical; 5/8 fail pre-fix. **`5d4578a` is a line-endings-only follow-up** — see §5's new note on `write_text()`. **audit §2.7 still OPEN.** Full suite 275 tests, baseline unchanged. | Claude Opus 5 |

| `3a59f6d` | 2026-08-09 | 20 | **Batch 3 item 20 DONE — BATCH 3 COMPLETE.** Human decision was *"fix the id, then audit"*; both halves landed. **Fix:** `_commit_final_transcript_segment`'s `event_id` fell back to `meta["request_id"]` — Deepgram's **connection-level** id — because `segment_metadata` never sets an `"event_id"` key, so the unique fallback beside it was unreachable dead code. That constant flowed into `lineage_ids` → `source_raw_event_ids` → `_lineage_overlap()`, making the lineage half of `_same_revision_chain` **constant-true**: measured at 13 of 14 canonical records in run `...155842` and 30 of 44 in `...133236`. `stable_revision_decision.py`'s existing "sticky and false-positive across adjacent utterances" comment was that symptom — this was its cause, and the text guard added to compensate was load-bearing because of it. Connection id is not lost (already passed separately as `deepgram_request_id`); Japanese unaffected (supplies real per-event `raw-NNNNNN` ids). **Audit:** new `CANONICAL_KEY_FIELDS_AUDIT.md`, backed by a scan of all 12,286 evidence rows. Two findings reported honestly rather than inflated: `channel_index`'s three serializations do **not** cause a live key mismatch (both key builders normalize the two forms that reach them identically; the `'0'` form is confined to raw-capture streams — a latent hazard only), and `_provider_utterance_id` was **deliberately left** resolving to the connection constant (it is store-only, never compared; injecting a locally-minted id into a field named for a *provider* id would mislead differently). Tests: `tests/test_final_event_id_is_per_utterance.py` (4), 2/4 fail pre-fix. Full suite 331, baseline unchanged (+1 known §5 task9 timing flake, verified passing in isolation). | Claude Sonnet 5 |

**BATCH 3 COMPLETE as of `3a59f6d`.** All items done (9c, 10, 11, 11b, 12, 13, 14, 15, 16, 17, 18, 19, 20). Four things were surfaced during the batch and **deliberately left open rather than fixed as drive-bys** — all are recorded, none are forgotten: the 5th containment instance in `decide_transcript_action` (§5, from item 13), audit §2.7's speaker-only store keying (from item 17), and two entries in `CANONICAL_KEY_FIELDS_AUDIT.md` §5 (drop `channel_index` from the keys; re-evaluate `_textually_related_revision` now that lineage overlap carries real information). **Batch 3's live-test checkpoint is still outstanding** — see §6d.

**Correction note on `5001275`:** the original draft of
`PROACTIVE_AUDIT_20260806.md` mislabeled
`main_window.py::_apply_final_interim_comparison` as "confirmed still
broken" by misreading this commit's BUG-B fix. The order-swap BUG-B
fixed here was **already correct** at audit time; what was genuinely
still broken (fixed in `78eb59e`) was a *different*, narrower defect —
the unrelated-text fallthrough default. See `PROACTIVE_AUDIT_20260806.md`
§1.2. Flagged here so no future agent re-litigates it from commit
messages alone.

| `ac8feb5` | 2026-08-09 | 1 | **Item 19b DONE** *(new — found by analyzing the Batch 3 checkpoint runs, not in the audit)*. `japanese_boundary_stabilizer.py::_map_output_contract` decided revise-vs-append by matching the ACTION NAME, listing `merge_pending_and_current` next to `merge_with_previous`. They are structurally different: the latter merges `_previous_line` (already committed downstream — revising is right, and its call site passes `update_previous=True`), the former merges `_pending` (this stabilizer's own never-committed buffer, so the result is a **brand-new utterance** and its call site deliberately does NOT pass the flag). Name-matching overrode that intent, writing the new utterance over an unrelated committed record in place. Live evidence, run `...050227`: 3 of 3 such events each destroyed a different sentence, 194 chars total; correlation was 9/9 across every run with data. **Not a Batch 3 regression** — normalized per 100 chars of raw speech the destructive rate was actually *lowest* in that run (0.19 vs 0.27/0.30); what rose was severity per event (64.7 vs 10.0 chars), because it is the corpus's only single-speaker monologue, so sentences accrete into one record before an overwrite wipes it. Tests: `tests/test_merge_pending_appends_not_revises.py` (10) | Claude Opus 5 |
| `c43f57b` | 2026-08-09 | 4 | **Item 11c DONE** *(new)*. `_should_commit_interim_recovery`'s inline `< 20` floor was one number for two scripts, but the length is measured *after* `_normalize_compare`, which for CJK compacts away all spacing and punctuation. Measured across all **2210 interims in 27 run folders**: en n=1188, median 30, 29.5% under 20 — ja n=1022, **median 9, 91.1% under 20, max 33**. So 20 sat below the English median but above the Japanese p90, i.e. for Japanese it meant "never recover a tail" (91 of 1022 admitted). All three interims ever genuinely pending at Stop were Japanese, normalizing to **7, 16 and 7** — every one killed here, *after* items 10/11/11b had been fixed precisely so they would reach this decision. Replaced with named `STOP_TAIL_MIN_CHARS_CJK = 4` / `STOP_TAIL_MIN_CHARS_LATIN = 20` (Latin unchanged — no English interim has ever been pending at Stop, so no evidence to move it). 4 is from the observed content distribution: norm 0-3 is punctuation and bare particles, meaning starts at 4. Tests: `tests/test_stop_tail_too_short_threshold.py` (15) | Claude Opus 5 |
| `9dae426` | 2026-08-09 | — | **Deterministic end-to-end harness for the whole Stop-tail chain** (`tests/test_stop_tail_recovery_end_to_end.py`, 17 tests). Items 10/11/11b/12 each had unit tests but nothing proved they compose, and three consecutive live sessions never exercised the path at all. Verified by mutation: reverting item 10 fails exactly the 2 interior-substring tests, reverting item 11 fails 1, removing item 11b's stash fails all 4 orphan tests. **This is now the primary verification route for that path — prefer extending it over waiting for a live session to reproduce the condition.** | Claude Opus 5 |

### 6b. Live-test verification status (tracked separately from code status)

| Fix | Live-tested | Result |
|---|---|---|
| `78eb59e` | Yes, 2026-08-07 | Permanent ghost gone; surfaced the `a5e2ac4` flicker case |
| `a5e2ac4` | Yes, 2026-08-07 | `sf=True` interim events confirmed at zero. Watchdog then fired 10/12 decisions — **that is item 2 (TTL miscalibration), not a defect in `a5e2ac4`** |
| Batch 1 (`9892cb1`, `38a92b5`) | Yes, 2026-08-08 | Watchdog 83% → 2% (en) / 0% (ja). Re-confirmed in the 15:53/15:58 runs: **PASS**, 1/32 (3%) and 0/14 |
| Batch 2 item 4 (`ddeb67f`) | Yes, 2026-08-08 | `remove_exception` log noise **92 and 58 per run → 0 and 0**. `remove_attempt` still logged normally |
| Batch 2 items 5-8 (`5181b8c`/`38c6096`/`07d5234`/`ec38779`) | Yes, 2026-08-08 | Zero occurrences of all 11 new failure-event names across all 3 runs — i.e. none of those silent paths were hit this session. Confirms no new log spam; does **not** confirm the handlers fire correctly in production (unit tests cover that) |
| Batch 2 item 7b (`6726f68`) | Yes, 2026-08-08 | No `JAPANESE_STABILIZER_INGEST_FAILED` / `JAPANESE_PATH_DETECTION_FAILED` — the failure path did not trigger. Japanese run committed normally, so the routing change caused no regression |
| Batch 2 item 9b (`b220c86`) | Yes, 2026-08-08 | **Start-button freeze fixed.** `TEMP_AUDIO_RETENTION_CLEANUP_STARTED` → `START_AUDIO_INIT_BEGIN` gap: **8.80s / 12.41s → 0.00s / 0.00s**. `..._COMPLETED` now lands *after* audio init (3.1s / 3.7s later), confirming it runs in the background |
| §1.1 (`98a6fa0`+`432dea1`) | Yes, 2026-08-08 | **First live evidence of `revise` working.** Japanese run 15:53: `applied_action = {append: 21, revise: 5}`. Previously **0 revises across 138 commits**. English run: 14 append / 0 revise (no correction opportunities arose) |
| Batch 3 item 9c (`13f20ca`) | Yes, 2026-08-09 | **CONFIRMED FIXED.** Runs `...033339` (ja) / `...034448` (en). Both now `final_status: completed`, `stop_finalize_failed: false` — the step that failed *both* post-Batch-2 runs. `DUPLICATE_SUBMISSIONS_REJECTED` **1 → 0** in both; `UNRESOLVED_TRANSLATION_SEQUENCES: []`, `MISSING_TRANSLATION_SEGMENT_IDS: 0`, jobs accepted/sent/completed 31/31/31 (ja) and 12/12/12 (en). No `TRANSLATION_RECONCILIATION_*` events fired at all — there was no gap left to heal |
| Batch 3 items 10 + 11 (`b404c19`, `b2b39de`) | **NO — code path never executed** | Both runs reached Stop with `latest_interim_len: 0`, so `_recover_interim_tail_on_stop` returned at `empty_interim` **before** either filter ran. Neither the item-10 nor the item-11 branch was exercised. Unit tests pass and the baseline is clean, but there is **zero live evidence** for either — the same "wired up, never fired" shape §7 warns about. **Still outstanding: a session Stopped mid-sentence** (see §6d) |
| Batch 3 interim-ghost regression check | Yes, 2026-08-09 | `tools/scan_interim_ghost_evidence.py`: **PASS** on both. Watchdog 1/31 (3%) ja, 0/12 en — far below item 3's 25% REVIEW threshold, consistent with the post-Batch-1 numbers. No duplicated closing line in either FINAL export (0 exact-duplicate lines), i.e. items 10/11's opposite failure mode did not appear — though that is weak evidence given the paths never ran |
| **Batch 3 post-completion checkpoint** — runs `...20260809-173846` (en) / `...20260809-174516` (ja) | Yes, 2026-08-09 | See the dedicated write-up below. Headline: **item 20 CONFIRMED FIXED live; item 18's new diagnostic fired 35× and surfaced a real, pre-existing plumbing gap; items 10/11/11b are STILL unexercised for the third run running** |

#### Batch 3 post-completion checkpoint — 2026-08-09, runs `...173846` (en) and `...174516` (ja)

Both runs `final_status: completed`.

**✅ Item 20 — CONFIRMED FIXED LIVE.** This is the first item in the batch
with unambiguous before/after live numbers. In the English run the most
frequent lineage id now appears in **1 of 11 canonical records (9%)**, and
the ids include `dg-final-1786264746658536100` — the per-event value the
fix made reachable. Before the fix the equivalent run had one
connection-level UUID in **13 of 14 records (93%)**. 367 distinct lineage
ids over 11 records. The Japanese run is unchanged as predicted
(`raw-NNNNNN`, max 4/36 = 11%, i.e. genuine revision-chain overlap). No
duplicated or split lines appeared in either FINAL export, so the
second-order risk — a stricter `_same_revision_chain` splitting a real
revision into two lines — did **not** materialise.

**✅ Item 9c — re-confirmed, and this time the self-heal actually ran.**
Both runs: `UNRESOLVED_TRANSLATION_SEQUENCES: []`, 0 duplicate/empty/
unsupported rejections, 0 pending at exit. Each run logged one
`TRANSLATION_RECONCILIATION_FORCED_SUBMIT` **followed by**
`TRANSLATION_RECONCILIATION_DONE` — a gap existed, was force-submitted
with a non-colliding id, and resolved. The 2026-08-09 checkpoint only
showed "no gap arose"; this is stronger evidence.

**⚠️ Item 18 — the diagnostic fired 35× and found a real bug. Not a
regression: it made an existing silent failure audible.**
`TRANSLATION_STORE_ID_MATCH_NOT_FOUND` fired 35 times in the Japanese run
(36 canonical records). Investigated in full:
- All 35 requested `jp-utt-*` ids **do exist** in the canonical ledger
  (35/35 overlap) — the ids are correct and the utterances are real.
- `transcripts/clean_active_transcript.jsonl` — the store-facing stream,
  **exactly 35 rows** — carries `canonical_utterance_id` on **0** of them,
  while `stable_commits.jsonl` carries it on 36. **The Japanese
  *assembler* path drops the id somewhere between the stable commit and
  the `TranscriptStore` write.** (The Japanese *manual-mode* path is fine:
  executing the real code via `ManualModeCommitHost` shows `jpm-utt-*` ids
  stored correctly.)
- **This is not something item 18 broke.** `final_export_records.jsonl`
  has `translated_text` on **0 rows in every run examined, including the
  two pre-item-18 baselines** (`...155334`, `...155842`). The attachment
  was already failing; item 18 replaced a silent text-match miss with a
  loud id-match miss. That is precisely what the item was for.
- **User-visible impact today: none.** Every `store.get_all()` consumer
  (`run_artifacts.py`, `main_window.py`, `segment_count_reconciliation.py`)
  reads only `segment.text`; nothing anywhere reads
  `TranscriptSegment.translated_text`. It is write-only state. This
  becomes real the moment a bilingual export or summary is built from the
  store — presumably the reason the field exists. **Filed as a new item
  in §6c.**

**❌ Items 10 / 11 / 11b — STILL NOT EXERCISED. Third consecutive run.**
Both sessions again reached Stop with `latest_interim_len: 0` and
`[INTERIM] stop tail skipped … reason: empty_interim`. No `orphan` events,
so 11b's hand-off is untested too. **The checkpoint asks for Stop pressed
*mid-sentence, while still speaking*; that has not happened yet in any of
the six sessions run so far.** Until it does, these three fixes have unit
tests and no live evidence.

**Fired zero times — no evidence either way, not a pass:**
`SAME_UTTERANCE_SUBSTITUTION_UPDATED` (item 13),
`STOP_TAIL_MERGE_APPENDED_NOT_UPDATED` (item 17 — expected: zero means the
race did not occur), `DUPLICATE_CONTINUATION_SUPPRESSED` (item 19, now
four runs with no occurrence), `IDENTITY_REJECTION`, `FALLBACK_BLOCKED`,
`STABLE_COMMIT_BEFORE_TRANSLATION_REJECTED`. No transcript line was
silently replaced by another speaker's text, which is item 17's own
failure mode.

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

#### BATCH 2 — Core bug 3: make silent failures loud (logging only) — ✅ COMPLETE (all of 4-9, 7b, 9b; see §6a: `ddeb67f`/`5181b8c`/`38c6096`/`07d5234`/`ec38779`/`b220c86`/`6726f68` + item 9's doc-only entry)

**Recommended model: Sonnet 5.** No dependency on Batch 1; may run in
parallel, but keep commits separate.

~~**4. `[core:3]`** `alpha/ui/main_window.py` · `grep: def _remove_interim_line_from_display`~~ **DONE, `ddeb67f`.**
Guarded on `mark_names()` instead of catching the `TclError` the old
`box.compare()` call raised on the normal case.

~~**5. `[core:3]`** `alpha/ui/main_window.py` · `grep: def _on_store_segment_updated`~~ **DONE, `5181b8c`.**
All 3 swallowed excepts now log.

~~**6. `[core:3]`** `alpha/transcription/utterance_lifecycle.py` · `reset_for_session` / `_resolve_correction_target_locked`~~ **DONE, `38c6096`.**

~~**7. `[core:3]`** — six locations, logging only~~ **DONE, `07d5234`.**
All six now log; swallow behavior unchanged everywhere.

~~**7b. `[core:1]` — Japanese stabilizer exception falls into the English commit path**~~ *(audit §2.10)* **DONE, `6726f68`.**
Split the shared try/except in `_commit_final_transcript_segment`: the
language-path decision and the stabilizer work no longer share a handler,
and neither failure falls through to the English/generic block — both
publish the final directly and log
(`JAPANESE_STABILIZER_INGEST_FAILED` / `JAPANESE_PATH_DETECTION_FAILED`).

**Audit §2.10's description was partly wrong and is corrected here for
anyone reading it later:** the *common* pre-fix outcome was **not**
entering the English lifecycle — `should_use_utterance_lifecycle()`
independently rejects Japanese via its own inner guard, so the
fall-through skipped the lifecycle and landed on the publish call at the
bottom, i.e. the Japanese **assembler was bypassed** and a raw fragment
committed. The lifecycle-contamination §2.10 describes was the *rarer*
path, needing the Japanese guard itself to be broken **and**
`_listen_language` unset (so `should_use_utterance_lifecycle()`'s
fallback lang check defaults to `""`, doesn't start with `"ja"`, and
returns True). Closing that one required the **detection-failure** branch
to stop falling through too — an earlier draft of this fix fixed only the
ingest branch while claiming it closed both; caught by running the new
tests against pre-fix code and re-reading the control flow.

~~**8. `[core:3]`** `alpha/transcription/utterance_lifecycle.py` · `_observe_identity`~~ **DONE, `ec38779`.**
Fail-open path now logged (`OBSERVE_IDENTITY_FAILED_OPEN`); fail-open
behavior itself deliberately unchanged (that's item 27).

~~**9. `[core:4]` — data collection only, no fix**~~ **DONE (investigation
only, as scoped).** `Bug Report.md` §4.2: 2 of 2 quarantine events ever
recorded were `noise_fragment` misclassifications, 0 of 2 recovered.
n=2 — too small for item 34 to act on yet, more runs needed. Threshold
untouched, as instructed.

~~**9b. `[core:—]` — Start button: multi-second UI-thread freeze before audio/Deepgram init**~~ **DONE, `b220c86`.**
*(new, found 2026-08-08 during Batch 1's live-test checkpoint — not part
of the original audit)* Real root cause turned out to be more precise
than the original hypothesis below (kept for the record): not
`begin_live_session()` on the UI thread, but a class-level monkey-patch
(`install_japanese_stabilizer_hooks`, `japanese_final_chunk_stabilizer.py`,
installed once at app startup via `main.py`) that called
`cleanup_old_audio_temp(reason="start_listening")` directly/synchronously
instead of the already-existing `schedule_audio_cleanup_non_blocking`
sibling already correctly used at Stop. `Bug Report.md` §4.5 has the
full corrected write-up.

*Original hypothesis, superseded, kept for the record:* `alpha/utils/session_runtime.py`
· `grep: def begin_live_session` (called synchronously from the
Start-button handler, before the background worker thread starts) —
suspected `begin_live_session()` blocking the Tk UI thread via
evidence/artifact-index filesystem work. Measured gap was real (8.8s
English / 12.5s Japanese) but the *cause* was the audio-cleanup call
described above, not this function.

**Checkpoint:** one live session per language. Confirm new log lines
appear, with no unexpected volume.

---

#### BATCH 3 — Core bug 2: identity & text-comparison hardening

**Recommended model: Opus 5** for items 10, 13, 17, 21 and **9c**;
Sonnet 5 is adequate for the rest (see §8). Depends on Batch 2's
visibility.

Each is its own commit + regression test. Use the already-shipped
`_merge_lexical` (`1a32639`), `_text_related` (`25a6623`) and interim
identity gate (`78eb59e`) fixes as templates for both the fix shape and
the test shape.

~~**9c. `[core:3]` — translation-gap safety net is broken by a segment-id collision**~~ **DONE, `13f20ca`.** *(found 2026-08-08 in the post-Batch-2 live test)* Full analysis kept below — it documents a diagnosis I got wrong first, which is worth preserving.
`alpha/utils/stop_finalize_worker.py` · `grep: def reconcile_translation_gaps` (raise at `grep: could not deliver`) and `alpha/translation/translation_worker.py` · `grep: def enqueue_stable_segment`
**Recommended model: Opus 5** (see §8 note below).

> ⚠️ **This entry was rewritten 2026-08-08 after a deeper investigation.
> The first version claimed "no translation was actually missing" and
> called it a false-positive gap. That was WRONG** — the gap is real and
> this IS content loss. The original wording is preserved at the bottom
> of this entry so the correction is auditable.

Both runs of the 2026-08-08 post-Batch-2 live test ended
`final_status: "failed"`, `stop_finalize_failed: true`, on this single
required step:

| Run | active | already_submitted | gap | forced | unresolved | accepted/sent/completed |
|---|---|---|---|---|---|---|
| `...155334` (ja) | 26 | 25 | 1 | 0 | **1** | 31 / 31 / 31 |
| `...155842` (en) | 14 | 13 | 1 | 0 | **1** | 14 / 14 / 14 |

**The gap is real.** In the English run the canonical ledger holds 14
records — `U-1`…`U-13` **plus `U-15`** — while
`evidence_streams/translation_jobs.jsonl` holds 14 jobs covering only
`U-1`…`U-13` (`U-13` twice, a revision). **`U-15` has no translation
job at all**, so its translation is genuinely absent from the output.
`final_status: "failed"` is therefore *correct*; the bug is that the
safety net designed to heal exactly this could not.

**Why the self-heal fails — segment-id space collision.**
`reconcile_translation_gaps` force-resubmits with
`segment_id=int(rec.get("sequence_number") or 0)`, where
`sequence_number` is the **canonical ledger's own record counter**
(`canonical_transcript_ledger.py`, `grep: "sequence_number": _sequence`
— 1, 2, 3 … per ledger record). But the translation worker's
`segment_id` space is a **completely separate counter**,
`main_window.py`'s `_translation_segment_seq` (`grep:
_translation_segment_seq`, also 1, 2, 3 …). The two are unrelated but
occupy the same integer range. `U-15` is ledger record #14, so
reconciliation submits `segment_id=14`; the worker already has 14 in
`_seen_request_ids` (`COMPLETED_TRANSLATION_SEGMENT_IDS = [1..14]`) and
rejects it as a **duplicate of an unrelated earlier job**. Hence
`DUPLICATE_SUBMISSIONS_REJECTED = 1` and `forced_count = 0`.

Two defects, both worth fixing:
1. **Colliding id space (primary).** Reconciliation must allocate a
   segment id from the *same* counter every other submitter uses
   (`host._translation_segment_seq`), not from the ledger's. This alone
   makes the safety net actually work.
2. **Rejection reasons are conflated (secondary).** `enqueue_stable_segment`
   returns a bare `False` for every cause (duplicate, not-accepting,
   quota-disabled, empty text, unsupported language, obsolete version).
   "Rejected because genuinely already delivered" should count as
   success for reconciliation; "rejected because the worker is shut
   down" is a real failure. The worker already tracks each cause in
   separate counters (`get_counters()` is public), so the reconciler can
   distinguish them by snapshotting before/after without changing the
   return type or affecting other callers.

**Severity: SILENT-LOSS** (one utterance's translation missing from the
output, and the mechanism meant to catch it is inert). Also undermines
the fail-closed contract in the same way as item 21 — but note the gate
itself behaved correctly here.

**Open question, out of 9c's scope:** *why* `U-15` never reached
`submit_text_for_translation` in the first place. Finalize already runs
`flush_pending_translation_debounce` before reconciliation, so `U-15`
was never even in `_pending_translations_by_utterance`. 9c fixes the
safety net so this self-heals regardless of cause; the primary-path miss
deserves its own investigation afterwards.

**Not new to Batch 2** — the same failure appears in run `...134815`
(2026-08-08 13:48), before any Batch 2 item landed. It is not a
regression from this work; it was simply not noticed until now.

<details><summary>Original (incorrect) diagnosis, kept for audit</summary>

> `TRANSLATION_RECONCILIATION_FORCED_SUBMIT_REJECTED` fired once per run,
> and `translation_summary.json` shows `DUPLICATE_SUBMISSIONS_REJECTED = 1`
> in both. So the sequence is: the reconciler believed one committed
> utterance had no translation, force-resubmitted it, and
> `enqueue_stable_segment` correctly rejected it as a **duplicate of work
> already accepted and completed** … **Severity: VISIBLE-BUG, not content
> loss** — the transcripts and translations are complete in both runs;
> `final_status` is simply wrong.
>
> The error was reading `STABLE_TRANSLATION_JOBS_ACCEPTED = 14` against
> 14 ledger records and concluding they must be the same 14 utterances,
> without checking the actual ids. They were not: one job was a duplicate
> revision of `U-13`, and `U-15` had none.

</details>

~~**10. `[core:2]`** `alpha/ui/main_window.py` · `grep: def _check_stop_tail_duplicate`~~ **DONE, `b404c19`.** Narrowed the already-committed match from any-substring to equality-or-prefix.
*(Original description kept:)* **Highest severity in this batch.** Stop-time last-chance commit decided
via `norm_interim == norm_seg or norm_interim in norm_seg` and
`.startswith()` only. A reworded (non-substring) tail can be classified
`skip_already_committed` and dropped entirely — at Stop, with no second
chance.

~~**11. `[core:2]`** `alpha/ui/main_window.py` · `grep: def _should_commit_interim_recovery`~~ **DONE, `b2b39de`.**
Narrowed `norm_interim in norm_final` to equality-or-prefix and removed the
function's unreachable `no_match` drop-path.

~~**11b. `[core:1]` — the ghost watchdog destroyed the Stop-tail recovery source**~~ **DONE, `69605cc`.**
*(new, found 2026-08-09 while checking why the Batch 3 checkpoint never
exercised items 10/11)* `alpha/ui/main_window.py` ·
`grep: def _check_interim_ghost_watchdog`. The watchdog cleared
`_latest_interim_text` via `_clear_interim_tail`, and that variable is the
only thing `_recover_interim_tail_on_stop` reads — so the display layer
deleted the content-recovery source. It now stashes the orphan first, and
Stop falls back to it, gated by a supersession check. **Read this before
touching any other Stop-path item:** it is the clearest example in this
codebase of a display-layer mechanism silently disabling a content-safety
mechanism, and `Bug Report.md` §4.3 had flagged the risk long before it
was measured.

**Worth knowing for items 12-19:** this function is the **second** of two
sequential filters on the same Stop-time last-chance path —
`_check_stop_tail_duplicate` (item 10) runs first, and a tail must pass
*both* to survive. Item 10's fix alone did not close the loss; the
identical anti-pattern one filter later still discarded it. When fixing
the remaining instances, check whether the one you are looking at is
itself chained behind another filter with the same defect.

**Left open on purpose (logged in §5, needs its own item):** the same
function's `len(norm_interim) < 20` guard is an unmeasured threshold that
drops short closing utterances at Stop — 19-char English, 11-char
Japanese after CJK compaction. Not containment, so out of item 11's
scope, and per item 34's rule the replacement value must be measured, not
guessed.

~~**12. `[core:2]`** `alpha/ui/main_window.py` · `grep: def _should_repair_previous_segment`~~ **DONE, `af6781e`.** Narrowed `norm_curr in norm_prev` to prefix-or-suffix. Traced severity: `current` is never dropped (still commits separately downstream via `_try_segment_repair`'s caller) — a missed-merge/quality bug, not content loss like 10/11/19.
*(Original description kept:)* Same pattern; misclassifies a reworded previous segment as
non-continuation.

~~**13. `[core:2]`** `alpha/transcription/duplicate_protection.py` · `grep: def decide_transcript_action`~~ **DONE, `8f19afe`.** Same-utterance substitution now upgrades `add`→`update`, gated on matching `canonical_utterance_id` only (never text similarity — a wrong guess here overwrites committed speech). Both dead `.startswith()` branches removed as proven no-ops.
*(Original description kept:)* Residual gap (the historical `or True` bypass is already gone): a
substitution-style correction arriving with **no** authoritative
`lifecycle_decision` signal still falls through to `"add"` (duplicate
line) instead of `"update"`. Mitigated in the common case by the upstream
signal override; live whenever that signal is absent. Also contains two
dead `.startswith()` branches (unreachable — the `in` test two lines
earlier already subsumes them); remove them as part of this fix.

~~**14. `[core:2]`** `alpha/transcription/japanese_sentence_assembler.py` · `grep: def merge_japanese_fragments`~~ **DONE, `5ffb18d`.** Removed the `prev.endswith(curr): return prev` fast path. No live occurrence found (0/114 real merge events scanned); a static fix, said so in the commit.
*(Original description kept:)* `curr.startswith(prev)` / `prev.endswith(curr)` accepted as proof of full
subsumption *before* the smarter overlap search runs — can drop a
corrected-but-shorter retranscription that happens to be a literal
suffix.

~~**15. `[core:2]`** `alpha/transcription/japanese_sentence_assembler.py` · `grep: def _looks_like_speaker_continuation_tail`~~ **DONE, `5ffb18d`.** Removed 5 ordinary connectives from `_SPEAKER_LOCK_CONTINUATION_PREFIXES`; kept the one specific retained phrase. **Item 23 still pending — re-check this dependency when 23 is picked up.**
*(Original description kept:)* Literal-prefix/phrase-containment list misclassifies a different
speaker's line that merely starts with an ordinary connective as a
continuation. **Do this before item 23** — that item's logic consumes
this function's output.

~~**16. `[core:2]`** `alpha/transcription/deepgram_client.py` · `grep: def teams_commit_decision_from_dup_action`~~ **DONE, `5ffb18d`.** Confirmed diagnostic-only by full control-flow trace, renamed to `teams_commit_decision_from_dup_action_diagnostic_only`.
*(Original description kept:)* A 4th independent instance of this anti-pattern class, living in the
ingestion layer. **Verify it is still diagnostic-only** (its output
should only feed logging, not a commit branch). Then rename or
explicitly mark it diagnostic-only so a future change cannot silently
wire it into a live decision path — which would reintroduce this bug
class a 5th time.

~~**17. `[core:2]`** — combined fix, same root cause: `alpha/summary/transcript_store.py`~~ **DONE, `43374ad`** (+ line-endings fix `5d4578a`). All **four** unsafe sites fixed — the three writes the item named, plus an unsafe *read* it did not (`_commit_transcript_item_to_store` derived `previous_text` from `get_last_segment(speaker)`). Renamed rather than deleted (`..._unsafe_speaker_scan`) because `tests/test_task2g_acceptance_gate.py` deliberately pins their behavior to document the delta; the rename still achieves "not reachable by reflex". `get_last_segment` lost its speaker filter (no-arg true-last form kept, legitimately used); dead `get_last_segment_for_speaker` alias deleted. **The Stop-tail site needed more than a swap — it ignored the return value, so the strict variant alone would have silently dropped the merged tail on the last-chance path; it now appends instead.** **audit §2.7 remains OPEN:** the `..._if_active` variants are still keyed by speaker only, with no channel/session key.
*(Original description kept:)* Delete or alias these unsafe speaker-only methods so only the safe
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

~~**18. `[core:2]`** `alpha/summary/transcript_store.py` · `grep: def add_translation`~~ **DONE, `0aa6a8f`.** Added `canonical_utterance_id` to `TranscriptSegment`, threaded it from `main_window.py` through `duplicate_protection.py` into the store; `add_translation` matches on it first when supplied, falls back to text-equality when not, logs `TRANSLATION_STORE_ID_MATCH_NOT_FOUND` rather than silently reusing the old text match if an id is given but not found.
*(Original description kept:)* Matches an incoming translation to its segment by **exact text
equality**, not record id. If the segment's text was revised between
request and response, the match silently fails and the translation is
dropped with no log. Move to record-id matching.

~~**19. `[core:2]`** `alpha/transcription/japanese_boundary_stabilizer.py` · `grep: def duplicate_continuation_ratio`~~ **DONE, `0aa6a8f`.** Narrowed the `cur_c in prev_c` full-duplicate check to prefix-or-suffix-of-previous. No live occurrence found, static fix like item 14.
*(Original description kept:)* Pure substring containment + positional character-match ratio, not
substitution-aware. Can silently drop a genuinely new short remark that
happens to be a literal substring of the previous line (e.g. a repeated
"ありがとうございました").

~~**20. `[core:2]` — canonical-key fields are decorative at the ingestion boundary**~~ **DONE, `3a59f6d`.** Decision taken (fix + audit). `event_id` no longer inherits the connection-level `request_id`, restoring `_lineage_overlap` as a real signal. Full write-up in **`CANONICAL_KEY_FIELDS_AUDIT.md`**, including what was deliberately *not* changed and why.
*(Original description kept:)* Two of `REPAIR_PLAN.md`'s six required canonical-key fields carry no
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

**20b. `[core:2]` — the Japanese assembler path never gets its
`canonical_utterance_id` into `TranscriptStore`** *(found by item 18's
diagnostic during the 2026-08-09 post-completion checkpoint — see §6b)*
`alpha/transcription/japanese_sentence_assembler.py` ·
`grep: metadata["canonical_utterance_id"] = self._current_canonical_utterance_id`
→ the chain down to
`alpha/transcription/duplicate_protection.py` · `grep: def _apply_transcript_to_store`
Measured live: `TRANSLATION_STORE_ID_MATCH_NOT_FOUND` fired **35 times in
one Japanese run**. All 35 `jp-utt-*` ids exist in the canonical ledger,
and `stable_commits.jsonl` carries the id on 36 rows — but
`clean_active_transcript.jsonl` (the store-facing stream, exactly 35 rows)
carries it on **0**. So the id is minted and survives into the ledger, and
is then lost before the store write. The Japanese **manual-mode** path
(`jpm-utt-*`) is unaffected — verified by executing the real code — so
this is specific to the assembler path.
**Impact today is zero and that is the trap:** nothing in the codebase
reads `TranscriptSegment.translated_text` (every `store.get_all()` consumer
reads only `.text`), so the field is write-only. This defect only bites
when a bilingual export or summary is built from the store — which is the
apparent purpose of the field. **Fix the plumbing before anything is built
on it.** Note `final_export_records.jsonl` has had `translated_text` on 0
rows in *every* run ever captured, pre- and post-item-18 — this is old, not
a regression. Recommended model: Sonnet 5 (tracing a metadata field through
a known chain).

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

**Batch 1 complete** (items 1-3). **Batch 2 complete** (items 4-9, 7b,
9b). **BATCH 3 COMPLETE** as of `3a59f6d` — items 9c, 10, 11, 11b, 12,
13, 14, 15, 16, 17, 18, 19, 20 (`13f20ca`, `b404c19`, `b2b39de`,
`69605cc`, `5ffb18d`, `0aa6a8f`, `af6781e`, `8f19afe`, `43374ad`,
`3a59f6d`).

**Four things were surfaced during Batch 3 and deliberately left open**
rather than fixed as drive-bys. None are forgotten; all are written down:
1. the 5th containment instance in `decide_transcript_action` (§5's
   2026-08-09 note, found during item 13) — Sonnet 5 is sufficient
2. audit §2.7 — `TranscriptStore`'s `..._if_active` variants are keyed by
   **speaker only**, no channel/session key (from item 17)
3. drop `channel_index` from the two key builders entirely — it is
   constant on mono, and keeping it is what makes its inconsistent
   serialization a latent hazard (`CANONICAL_KEY_FIELDS_AUDIT.md` §5)
4. re-evaluate `_textually_related_revision`'s role in
   `_same_revision_chain` — it was tuned to compensate for the lineage
   half being inert, which item 20 has now fixed (same file, §5)

(2) and (3) are both "what should the identity key actually be?" and are
worth deciding together.

**Batch 3 checkpoint — run 2026-08-09, PARTIALLY satisfied.** Full
results in §6b. Summary:
- ✅ **item 9c confirmed fixed live** — both runs `completed`, zero
  duplicate rejections, zero unresolved translation sequences. This was
  the checkpoint's primary criterion and the failure that motivated 9c.
- ✅ last utterance translated; no duplicated closing line; interim-ghost
  scan PASS on both.
- ❌ **items 10 and 11 are still unverified live.** Both sessions reached
  Stop with `latest_interim_len: 0`, so `_recover_interim_tail_on_stop`
  returned at `empty_interim` before either filter ran.
- 🔍 **…and investigating *why* it was empty found item 11b** (`69605cc`),
  a real content-loss bug: in the ja run the empty interim was **not**
  benign — the ghost watchdog had cleared a live 10-char interim 1.06s
  before Stop, and that speech is missing from the final export. In the en
  run it was benign (both interims committed normally via
  `action=clear_interim`). **Without 11b, items 10 and 11 could not have
  fired in production at all** in the case they were written for. This is
  the third time in this project that fixing one thing surfaced the next
  (§7's warning), and the first time the checkpoint itself caught it.

**→ Outstanding checkpoint action (needs a human, §3.10):** one more
session per language where **Stop is pressed mid-sentence, while still
speaking**, so an uncommitted interim tail actually exists at Stop. That
is the only way to exercise items 10, 11 and 11b end-to-end. Two variants
are worth doing, because they cover different halves:
- **(a) Stop immediately** after the last word (within ~5s) — the tail is
  still live, exercising items 10/11 directly.
- **(b) Stop after a ~10s pause** following the last word — long enough
  for the watchdog to fire first, exercising 11b's orphan hand-off. This
  is the exact shape that lost speech in run `...033339`.

In the resulting run check `logs/async_debug.log` for `[INTERIM] stop
tail` entries and confirm:
- `latest_interim_len` > 0, **or** a `stop tail using watchdog orphan`
  entry in variant (b). If neither appears the test did not reproduce the
  condition — repeat it.
- the spoken tail appears in `transcripts/Alpha_output_FINAL.txt` and is
  **not** dropped as `skip_already_committed` / `interim_in_final`
- it appears exactly once — no duplicated closing line
- no `stop tail orphan superseded` entry in variant (b) (that would mean
  a later interim arrived and the orphan was correctly discarded — valid,
  but it means the run did not test the hand-off either)
- the `reason` distribution of any `stop tail skipped` events, which also
  supplies the `too_short` measurement §5 is waiting on

**The same outstanding session should also cover items 12, 13, 14, 15, 16,
17, 18, 19** (all landed after this checkpoint ran, so none of them have
live confirmation yet):
- for Japanese sessions: no coincidentally-duplicated short phrase or
  misattributed speaker-turn line (items 14/15's theoretical fixes — no
  live occurrence was found for either, so this is the first real chance
  to observe them either way)
- translations still land on the right line even after a mid-session
  correction (item 18 — check for `TRANSLATION_STORE_ID_MATCH_NOT_FOUND`,
  which would mean a translation arrived for an id no segment carries)
- for Japanese sessions: no short remark that coincidentally repeats part
  of an earlier line goes missing (item 19 — also never observed live)
- a reworded/corrected previous segment now gets merged instead of
  staying as two separate lines (item 12 — check for `repair_merge` in
  the Teams commit-decision diagnostics; note item 12 cannot cause
  content loss even if wrong, only a missed merge, so this one is lower
  priority to verify)
- **items 13 and 17 are the two highest-priority things to watch in this
  run**, because both changed when a stored line gets **overwritten**:
  - item 13: check `SAME_UTTERANCE_SUBSTITUTION_UPDATED`. Every occurrence
    should correspond to a real correction of the same utterance. If a
    line the speaker actually said has disappeared and this event fired
    near it, that is the failure mode — report it rather than assuming
    the id match was sound.
  - item 17: check `STOP_TAIL_MERGE_APPENDED_NOT_UPDATED`. Each occurrence
    means the strict write refused and the tail was appended as its own
    line instead — content preserved, but it also means the race this
    item fixed was live in that run. Zero occurrences is the expected
    common case; a nonzero count is evidence worth recording, not a bug.
  - also confirm no transcript line was silently replaced by an unrelated
    speaker's text (the check-then-act race's original symptom).
- **item 20** — in `evidence_streams/canonical_commits.jsonl`, check
  `source_raw_event_ids`. Before the fix one connection-level UUID
  appeared in nearly every record (13 of 14 in run `...155842`). It
  should now hold **distinct per-utterance ids**; a single id repeated
  across most records means the fix did not take effect on that path.
  Then watch for the second-order effect: `_lineage_overlap` is a real
  signal again rather than constant-true, so `_same_revision_chain` is
  now strictly harder to satisfy — confirm no genuine revision started
  being treated as a new utterance (a duplicated, slightly-different
  line is the tell). This is also the moment to look at deferred
  finding (4).

**Batch 3's post-completion checkpoint HAS now been run** (2026-08-09,
runs `...173846` en / `...174516` ja) — full results in §6b. Item 20
confirmed fixed with clear before/after numbers, item 9c re-confirmed with
the self-heal actually firing, and item 18's diagnostic earned its keep by
exposing a real pre-existing gap (now filed as **item 20b**). Items 13, 17
and 19 never fired, so they remain unobserved rather than passed.

**→ NEXT, and it is a human task, not a code task: one session per
language Stopped MID-SENTENCE, while still speaking.** Items 10, 11 and
11b have now gone **three consecutive checkpoints without their code path
executing once** — every session so far has been Stopped after the speaker
finished, so `_recover_interim_tail_on_stop` returns at `empty_interim`
before any of the three fixes is reached. They have unit tests and zero
live evidence. The two variants and the exact log lines to check are
listed earlier in this section. **Do this before Batch 4** — it is the
oldest outstanding verification in the project.

**Then → item 20b** (§6c) — small, well-scoped, and it should be fixed
before anyone builds a bilingual export on `TranscriptStore`.

**Then → Batch 4** (items 21-27, Core bug 1: concurrency / state machine
/ fail-open policy). Recommended model: **Opus 5** for the whole batch
(§8) — race conditions and lock ordering, where tests alone do not
reliably catch the failure modes.

**The four deferred Batch 3 findings** (listed at the top of this
section) are unscheduled. (1) is small and safe to fold into any later
session. (2)+(3) should be decided together as one "what is the identity
key?" question. (4) should be looked at **during** Batch 3's live test,
since that is when the effect of item 20 first becomes observable.

**Still open from earlier live-test findings, not yet scheduled beyond
their existing batch** (both re-measured on the 2026-08-09 run and still
reproducing):
- **Japanese assembler → canonical ledger loss** (`Bug Report.md` §4.1 —
  Batch 5). Today: 34 assembler decisions (25 `commit_new`, 6
  `update_previous`, 2 `hold`) → 32 `stable_commits` → **25** canonical
  records, with **4 stable-committed sentences under 60% present** in the
  final export (worst 20.0%). The 2026-08-08 control was 6 such
  sentences, worst 10.5%. **Do not read that as improvement** — different
  speech content, same mechanism, still real loss.
- **`noise_fragment` quarantine** (item 34, Batch 5). Today quarantined
  `飲みます、食べます` ("I drink, I eat") — ordinary Japanese, again
  `later_committed_to_stable: false`. Running total is now **4
  quarantined, 0 recovered, 4/4 misclassified.**
- The open question from 9c: *why* the last utterance never reached
  `submit_text_for_translation` in the first place. Note today's runs had
  no gap at all, so this did not reproduce and remains uninvestigated.
- **New, from the 2026-08-09 analysis:** the English coverage gate is
  vacuous because the canonical-ledger file is never written on that path
  (§5). Pre-existing, needs its own item.

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
| **3** (items 9c, 10-20) | Text/identity comparison semantics | **Opus 5** for 9c, 10, 13, 17, 20; **Sonnet 5** for 11, 12, 14, 15, 16, 18, 19 | 9c spans two modules and needs cross-state reasoning about *why* two independent bookkeeping views of "already translated" disagree, plus an API change to distinguish rejection causes — wrong guesses here produce a fix that silences the symptom while leaving the false-positive gap. 10 and 13 require reasoning about which correction shapes must survive; 17 is a multi-call-site refactor with a race condition; 20 is an architectural decision. The rest follow templates already in the repo. |
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
