# Project Context

This project has known architectural bugs documented in:
- ROOT_CAUSE.md — confirmed root-cause audit
- REPAIR_PLAN.md — phased repair plan (follow task order strictly)

Active app code is in Alpha_Live_Translator/. Do not modify anything
under _archive/.

Do not spawn Explore subagents. Read only these exact files directly: [file list]. Do not search or explore the rest of the repo.

# graphify — supporting tool, use it for every bug-fix task

graphify (`graphifyy` on PyPI) is a codebase knowledge-graph tool. It is
installed as a **Claude Code skill** — on any machine/account where it is
set up, invoke it directly with `/graphify` inside Claude Code, or query
the CLI binary directly (path is per-machine, find it with
`where graphify` / `py -m pip show -f graphifyy`; do not hardcode a path
from a prior machine into this file — one already went stale once):

```
graphify query "<question>"
graphify path "<SymbolA>" "<SymbolB>"
graphify explain "<Symbol>"
```

**Use it proactively as a helping tool for every bug-fix task in this
repo** — before starting an investigation, to find the real call chain
for a symptom, to check what else calls a function you're about to
change, or to sanity-check a hypothesis about how two modules connect —
alongside, not instead of, the CLAUDE.md file-list restriction above and
alongside actually reading/running the code (see the verification rule
below — graphify shows structure, it does not prove runtime behavior).

If graphify is not yet installed on a machine/account, install it for
whichever AI coding tool is in use:
```
py -m pip install --user graphifyy
graphify install --platform claude   # or: codex, cursor, aider, etc.
```

Output: `graphify-out/graph.json` (GraphRAG-ready), `graph.html`
(interactive), `GRAPH_REPORT.md` (plain-language summary + community hubs).
A git post-commit hook auto-rebuilds the graph after every commit that
touches non-`graphify-out/` files — no manual step needed to keep it
current. Check `GRAPH_REPORT.md`'s "Graph Freshness" line against
`git rev-parse HEAD` if in doubt.

# Verification rule — do not trust a plausible evidence read; run the code

This project has already been burned twice by a diagnosis that *looked*
solid from reading logs/evidence files but turned out wrong the moment
the real code was actually driven:

- **Batch 3 item 9c**: an initial read of a run's evidence called a gap
  a "false positive." Running the real reconciliation code showed it was
  genuine content loss. Corrected in `BUG_FIX_ROADMAP.md`'s ledger rather
  than silently overwritten.
- **Batch 4 item 20b**: diagnosed as "the assembler drops
  `canonical_utterance_id` before the store write," based on one
  evidence file (`clean_active_transcript.jsonl`) showing 0/35 rows with
  the field. That file was never designed to carry that field at all
  (it uses a different id scheme, `canonical_line_id`) — the measurement
  proved nothing. The real bug (`revision_target_id` self-referential on
  every commit) was only found by driving the actual assembler code and
  reproducing the failure directly. See `CANONICAL_KEY_FIELDS_AUDIT.md`
  §5b for the full retraction.

**These are judgment calls, not hardcoded gates — apply the one that
fits the situation in front of you, skip the ones that don't:**
1. **When** a claim rests on a log/evidence file's field being present or
   absent (not on reading the writer's code directly), confirm that
   file's schema was ever *supposed* to carry that field first — read the
   code that writes it. Skip this when you already traced the field from
   its write site, or the file's schema is already well-established
   elsewhere in this repo's docs.
2. **When** about to declare a root cause fixed for a bug that involves
   cross-module state or a call chain you haven't executed, reproduce it
   by calling the real production code (a minimal host/harness borrowing
   real methods, like `_Host`/`*TestHost` in `tests/`) rather than relying
   only on reading the source. Skip this for a small, self-contained pure
   function where reading the code IS the verification (e.g. a one-line
   comparison bug) — items 10/11/12/19's fixes didn't need it, item 20b's
   did.
3. **When** a bug's real severity claim is about caller behavior (drops
   silently vs. falls back vs. logs) rather than the function's own
   return value, add an integration test through the real caller, not
   only a pure-function test. Skip this when the pure function's contract
   already fully determines the outcome.
4. If an earlier diagnosis in `BUG_FIX_ROADMAP.md` turns out wrong,
   **retract it visibly** (strike it, link to the correction) — never
   silently rewrite it as if the mistake never happened. Future sessions
   need to see what was tried and why it was wrong, not just the final
   answer.

# Git workflow — push once a fix is actually verified

**When** a fix (code + its regression test) has been verified against
the full test suite with the baseline unchanged — commit and push to
`origin/main` without waiting to be asked, rather than batching several
verified fixes into one uncommitted pile. Same for a roadmap/ledger
update once its content is finalized.

**Skip this** for work still in progress (investigation not yet landed
on a fix, a test not yet proven against pre-fix code, anything you'd
still revise this turn) — commit when the unit of work is actually done,
not on every intermediate edit.

Before pushing, always `git fetch origin` and check for divergence first
(this repo is worked on from multiple machines/accounts) — merge if
needed, resolve conflicts by keeping both sides' content rather than
discarding either, then push. Confirm success by checking
`git log HEAD..origin/main` and `git log origin/main..HEAD` are both
empty after the push.