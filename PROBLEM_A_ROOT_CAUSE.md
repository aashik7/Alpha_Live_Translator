# Problem A — root cause investigation (item 41)

**Date:** 2026-08-11 · **Model:** Opus 5 · **Item:** 41 of `CLIENT_DELIVERY_SPRINT_v5.md`

> **Status: ROOT CAUSE PROVEN. Updated 2026-08-12 — this supersedes the
> "strongly indicated, not proven" verdict this file carried on 2026-08-11.**
>
> That earlier verdict was correct at the time: Phase 5 had failed and no
> deterministic reproduction existed. It now does. **`tools/reproduce_problem_a.py`
> reproduces the loss headlessly with a single independent variable**, and the
> one structural fact that was missing — *why the revision-decision engine can
> never prevent it* — has been located and verified. §6 records both the
> original gap and how it closed; nothing has been quietly rewritten.
>
> **Item 42 landed 2026-08-12 — problem A is fixed.** The fix is *not* the one
> §5/§8 recommended: pointing the gate at `final_revision_action` was driven
> and proved insufficient on its own, because the authority is fail-closed to
> `append` (no `speaker` key), so that gate would always mint a new id and turn
> every genuine extension into a near-duplicate line. The landed fix requires a
> revise to be **non-destructive by content** instead. See sprint §9
> (2026-08-12) and `tests/test_problem_a_destructive_revise.py`.
>
> Location note: the item 41 prompt suggested `troubleshooting/problem_A_root_cause.md`.
> `troubleshooting/` is gitignored (`Alpha_Live_Translator/.gitignore:32`), so
> this document lives at repo root with `ROOT_CAUSE.md`,
> `CANONICAL_KEY_FIELDS_AUDIT.md` and the other tracked audits.

---

## 1. Phase 1 — the four populations

Run `Alpha_Live_Translator/troubleshooting/runs/v3.3.5.5.8.5.26.5.3-20260807-160529`.

| | population | artifact | count |
|---|---|---|---|
| P1 | assembler commit decisions | `logs/japanese_accuracy.log` → `STABLE_JAPANESE_COMMIT` | **10** |
| P2 | stable commit rows | `transcripts/stable_commits.jsonl` (`stable_commit_id` set) | **10** |
| P3 | canonical ledger records | `evidence_streams/canonical_commits.jsonl` | **9** |
| P4 | FINAL export lines | `transcripts/Alpha_output_FINAL.txt` (non-blank) | **9** |

Reproduce (from `Alpha_Live_Translator/`, `R` = the run folder):

```bash
# P1 — note the log is PIPE-DELIMITED "timestamp | {json}", not JSONL
grep -c '"event": "STABLE_JAPANESE_COMMIT"' "$R/logs/japanese_accuracy.log"
# P2
grep -c '"stable_commit_id"' "$R/transcripts/stable_commits.jsonl"
# P3
grep -c '"canonical_utterance_id"' "$R/evidence_streams/canonical_commits.jsonl"
# P4
grep -cve '^[[:space:]]*$' "$R/transcripts/Alpha_output_FINAL.txt"
```

**10 / 10 / 9 reproduces. The framing behind it does not.**

- Ids present in P2 but absent from P3: **none**. Nothing failed to reach the ledger.
- P2's 10 rows carry **9 distinct** `canonical_utterance_id`s —
  `jp-utt-19dbf8832ec0` appears twice.

The "missing 10th record" is a **duplicate id**, not a missing record.
§1's original wording ("never reach the canonical ledger") is **false** and was
already corrected under item 38.

---

## 2. Phase 2 — the lost records, and a correction to item 38's own count

Join key: `canonical_utterance_id` — `assembler_metadata.canonical_utterance_id`
in `stable_commits.jsonl`, top-level in `canonical_commits.jsonl`. Verified as
the only key spanning both populations and the key the ledger writes on; not
assumed.

### The true loss count is 10, not the 14 item 38 reported

Item 38's `_dropped_content` (in `tools/replay_run.py`) compares with
whitespace-only normalisation, which cannot see two legitimate rewrites. Both
false-positive classes were confirmed by reading the export directly:

1. **Leading-punctuation stripping** — `jp-utt-f8bf3bbf9fb2` (`...160529`):
   stable `。その人形を…` (42 ch) vs ledger `その人形を…` (41 ch). **In the export.**
2. **Business-accuracy text correction** — `jp-utt-453c4fd9c80f` (`...155334`):
   stable `…悲しいなっいう自分が…` vs export `…悲しいなっていう自分が…`
   (a `て` inserted). **In the export.**

Neither is content loss. **Logged, not fixed** — repairing the detector means
editing a `.py`, which item 41 forbids. Carried to sprint §9.

### The rule, and it is exceptionless

> A committed sentence is lost **if and only if** its `canonical_utterance_id`
> is carried by more than one `stable_commits` row **and** those rows' texts are
> mutually **disjoint** (not nested). The **earliest** row's text is lost; the
> last row's text survives.

Measured with a correction-tolerant test (best-match ratio against every export
line, plus a distinctive mid-sentence probe). Lost rows scored **0.16–0.36**
with probe absent; surviving rows **0.89–0.97** with probe present. No grey zone.

| run | stable rows | dup ids | of which nested (harmless) | disjoint | **losses** |
|---|---|---|---|---|---|
| `...155922` | 3 | 0 | 0 | 0 | **0** |
| `...160130` | 4 | 1 | 0 | 1 | **1** |
| `...160529` | 10 | 1 | 0 | 1 | **1** |
| `...134815` | 29 | 5 | 2 | 3 | **3** |
| `...155334` | 32 | 5 | 0 | 5 | **5** |
| `...174516` | 36 | 0 | 0 | 0 | **0** |

The apparent "dup-id count ≠ loss count" asymmetry is entirely the
nested/disjoint split. Two of `...134815`'s five duplicates were genuine
revisions (earlier text nested in the final text) and lost nothing.

### The lost record on `...160529`

`jp-utt-19dbf8832ec0`, two rows, textually disjoint:

- **row 0 — LOST** (`safe_hold_timeout_incomplete_but_stable`, 47 ch):
  `ですよ。違いますねでやっぱりこっちにいると日本の行事の不倫っていうのを味わうことが難しいので、`
- row 1 — survives (`target_chunk_boundary`, 121 ch):
  `だろう楽しい雰囲気とかも…子供がいなかったらやってないですよね。`

The ledger record for that id is `applied_action: "revise"`, `source_version: 1`,
holding only row 1's text.

---

## 3. Phase 3 — the chain, by grep anchor

All anchors in `Alpha_Live_Translator/alpha/transcription/japanese_sentence_assembler.py`
unless stated. Japanese-only unless marked.

| # | anchor | what happens |
|---|---|---|
| 1 | `update_previous_requested = bool(` | Assigned **once**, from boundary-stabilizer signals, **before** the decision function runs. `grep -n "update_previous_requested\s*="` returns exactly one assignment; every other hit is a keyword argument. **Never reassigned.** |
| 2 | `revision_decision = decide_stable_revision_action` | The revision authority (`stable_revision_decision.py`), hardened by Batch 3 items 10/11/12/19/20c. Returns `final_action`. |
| 3 | `if final_revision_action == "append":` | On `append`, resets `stable_layer_update_previous`, `post_update_previous`, and clears `metadata["boundary_should_revise"]` — **but not `update_previous_requested`.** |
| 4 | `proposed_action = "revise_previous" if update_previous_requested` | **The id-mint gate reads the stale variable from step 1, not the verdict from step 2.** On `revise_previous` it keeps `self._current_canonical_utterance_id`; otherwise it mints a fresh uuid. |
| 5 | `final_revision_action = "revise_previous" if proposed_action ==` | The correct verdict from step 2 is **overwritten** by a value derived from the gate. The right answer is computed, then discarded. |
| 6 | `metadata["revision_target_id"] = (` | Item 20b's guard — tests `final_revision_action == "revise_previous"`, i.e. the **already-corrupted** value from step 5. |
| 7 | `canonical_transcript_ledger.py` (shared) | Keys on `canonical_utterance_id`; a second commit on an existing id lands as a revision and replaces the text. |

Japanese-only vs shared: steps 1–6 are Japanese-only — anchor
`def should_use_japanese_final_stabilizer` (in `japanese_final_chunk_stabilizer.py`)
returns True only for `lang == "ja"` or `ja-*`. English routes to
`utterance_lifecycle.py` and never enters this file. Step 7 is shared.

---

## 4. Phase 4 — falsification

### The controlled comparison (the strongest evidence obtained)

Every 2nd-or-later `stable_commits` row on an already-used id, cross-referenced
against its `STABLE_REVISION_DECISION` log event:

| texts disjoint? | `update_previous_requested` | `final_action` | count | lost? |
|---|---|---|---|---|
| **yes** | **True** | `append` | **9** | **all 9** |
| no | False | `append` | 3 | none |
| yes | (log match failed) | — | 2 | yes |

`final_action` is **`append` in every case**, with
`decision_reason: speaker_boundary_forced_new_line`. The revision authority
*always correctly identified a new sentence*. The id was reused anyway, and
reuse-with-loss occurred **exactly** when `update_previous_requested` was True.
The three rows where it was False reused the id harmlessly. Same run, same code
path, one differing variable, opposite outcomes — a natural control group.

### Candidates

| candidate | verdict | evidence |
|---|---|---|
| **Stale `update_previous_requested`** driving the id-mint gate | **SURVIVES** | 9/9 vs 3/3 separation above; step-4/5 anchors show the gate reads it and then overwrites the correct verdict with it |
| Record never reaches the ledger | **REFUTED** | Ids in P2 but not P3 = **none**, on every run |
| Dedup/containment false positive in the revision authority | **REFUTED** | It returned `append` — the correct answer — in **every** observed case. It is not the component that errs |
| Id-space mismatch | **REFUTED** | One key (`canonical_utterance_id`) spans both populations and joins cleanly; no orphans |
| Swallowed exception | **REFUTED** | `ASSEMBLER_COMMIT_GATE_FAILED` appears **zero** times across all six runs' logs; every commit is accounted for in P2 |
| Race / ordering | **REFUTED** | Item 38b: real-timer replay reproduces correct commit counts and **0** losses; loss is not timing-dependent |
| Boundary stabilizer wrong upstream | **UNDETERMINED** | It sets the signal that becomes `update_previous_requested`. Whether it is *wrong* to do so, or merely mis-consumed downstream, is not established — and matters for where item 42 fixes this |

### The four required facts

- **(a) one loss on `...160529`** — exactly one disjoint duplicate id. ✔
- **(b) 08-08 volume** — 3 on `...134815`, 5 on `...155334`, matching disjoint
  duplicate counts exactly. ✔ (08-09's `...174516` has **zero**; §1's "and
  08-09" was retracted in §9 on 2026-08-10 and stays retracted.)
- **(c) English unaffected** — never enters this file (anchor in §3). ✔
- **(d) `coverage_ratio` read clean** — **and the contradicting number was in the
  same file.** `export_coverage_report.json` on `...160529` records
  `coverage_ratio: 1.0`, `coverage_passed: true`, while **in that same file**
  `source_commit_coverage_ratio`, `lineage_coverage_ratio`, `text_coverage_ratio`
  and `export_coverage_ratio` all read **0.9** — 9 of 10, precisely the lost
  commit. The gate keys on the one field comparing canonical→final, both sides
  already post-overwrite. ✔

---

## 5. What item 42 must be careful of

1. **Do not simply point the gate at `final_revision_action`.** That variable is
   *itself overwritten* one step later (§3 step 5) by a value derived from the
   gate. Fixing the read without removing the overwrite leaves two disagreeing
   notions of "was this a revision" alive on the same path — exactly what
   sprint §0 rule 2 forbids. **The overwrite and the gate must be resolved
   together, in one change.**
2. **Item 20b's `revision_target_id` guard sits downstream of that overwrite**
   (§3 step 6) and is therefore already keyed off a corrupted value. Any fix
   must re-verify item 20b's regression test still means what it claims.
3. **Decide where the authority lives.** Either the boundary stabilizer must
   stop signalling "revise" for a speaker-boundary-forced new line, or the
   assembler must stop consulting it once the revision authority has ruled.
   Doing both is two authorities; doing neither is this bug. This is the
   `[gate]` decision on item 42 and it is not settled by this document.
4. **Fix the measurement too.** `tools/replay_run.py`'s `_dropped_content`
   over-reports (§2). Any "problem A is fixed" claim measured with it will be
   wrong by ~2 in 12.

---

## 6. The gap — and how it closed on 2026-08-12

**The gap was real and is now closed. Both states are recorded here on
purpose; the failed attempt is what made the successful one findable.**

### 6a. What closed it — Phase 5 now succeeds

Two things were missing on 2026-08-11.

**(1) The wrong entry point.** The failed attempt drove
`_route_stable_publish`. That function merges a short follow-up into the held
buffer *before* publishing (anchor: the `_stable_hold_pending` block, then
`merge_short_fragments`), so the revise it produced was non-destructive — the
new text *contained* the old, which is precisely the harmless case that
accounts for 3 of the recorded revises losing nothing. Driving
`_publish_sentence` directly — one layer lower, where the defect actually
lives, with every function below it still the real production implementation
including the real ledger — reproduces the loss immediately.

**(2) The missing structural fact: `previous_record` has no `speaker` key.**
Verified directly at `japanese_sentence_assembler.py`, anchor
`previous_record = {`: the dict carries `line_id`, `text`,
`source_raw_event_ids`, `start_time`, `end_time`, `utterance_id`,
`segment_id` — **and no `speaker`**, even though `self._last_stable_commit`
has one. In `stable_revision_decision.py`, anchor
`previous_speaker = previous_record.get("speaker")` therefore reads `None`,
and `speakers_confirmed_same` (`speaker_boundary_guard.py`) is fail-closed:

```python
if active_speaker is None or candidate_speaker is None:
    return False
```

That guard sits immediately after the no-previous-record check and **before
every other rule**, so `decide_stable_revision_action` returns
`("append", "speaker_boundary_forced_new_line")` on every non-first commit.
Measured in the corpus: **105 of 111 decisions**, every non-first one. This is
why **Rules A–F, `REVISION_CONTENT_LOSS_GUARD_ENABLED`,
`REVISION_TERMINAL_SENTENCE_GUARD_ENABLED` and `_unique_content_lost` are
unreachable on the Japanese path** — the one guard designed to block a
destructive overwrite has never executed in the recorded corpus.

So the engine always says "append", and the stale flag always overrides it.
The engine cannot save you, which is why the loss is systematic rather than
occasional.

### 6b. The reproduction

`Alpha_Live_Translator/tools/reproduce_problem_a.py`, run headlessly:

| run | `boundary_should_revise` | ledger records | distinct ids | `applied_action` | sentence A |
|---|---|---|---|---|---|
| control | absent | **2** | 2 | `append`, `append` | **survives** |
| subject | `True` | **1** | 1 | `revise` | **destroyed** |

One variable. Everything else identical. The control proves the harness does
not itself lose text, so the subject's loss cannot be blamed on the fixture —
and the fixture exits `2` with an explicit "FIXTURE INVALID" message if the
control ever stops being clean.

### 6c. What is still NOT proven

Stated precisely, so item 42 does not over-claim:

- **Proven:** given `update_previous_requested=True` and a textually disjoint
  candidate, the previous sentence is destroyed in the canonical ledger, and
  the decision engine is structurally incapable of preventing it.
- **Not directly observed:** which of the four inputs set the flag in each
  individual live case. Log evidence narrows it — 12 of the 14 recorded ledger
  revises coincide with a `BOUNDARY_OUTPUT_REVISE_PREVIOUS_LINE`; the other 2
  came from `stable_layer_update_previous` / `post_update_previous`, and
  `apply_punctuation_start_post_correction` emits no log event, so the
  artifacts cannot separate those two. This does not affect the root cause —
  all four inputs feed the same variable through the same gate — but it does
  mean item 42 must fix the **gate**, not any single input.
- **Not reproduced:** the full live path end-to-end. `replay_run.py` still
  reproduces 0 of the losses (item 38b). The reproduction above isolates the
  defect; it does not simulate a whole session.

### 6d. The original 2026-08-11 record, kept verbatim

**Phase 5 (fixture) failed. There is no deterministic reproduction.**

Driving the real assembler headlessly through `_route_stable_publish` with the
two real disjoint sentences from `...160529`:

| setup | id reused? | first sentence lost? |
|---|---|---|
| same speaker (1, 1) | **yes** | **no** — ledger retained both |
| speaker change (1, 2) | no — two distinct ids | no |
| speaker change (2, 1) | no — two distinct ids | no |

Neither configuration reproduces the recorded combination (speaker-boundary
decision **and** id reuse **and** content loss). In the harness, a speaker
change produces *correct* behaviour. So at least one condition present in the
live path is still unidentified — candidates include boundary-stabilizer
internal state, `_last_stable_commit` lineage, or the point at which
`_current_canonical_utterance_id` is reset.

This is the same shape as item 38b's finding that full replay does not
reproduce the loss either. **Two independent harness attempts have now failed
to reproduce problem A**, which is itself a substantive result: the mechanism
depends on state that neither harness reconstructs.

Consequences, stated plainly:

- The root cause above rests on **correlation across 12 observations plus
  code structure**, not on a controlled reproduction.
- **Item 42 cannot satisfy sprint §4 step 5** ("prove the test catches the bug")
  until a reproduction exists, because there is nothing to make fail.
- Per item 41 §10, this is a stop-and-report outcome, not a failure to be
  papered over. The next step is to identify the missing condition — not to
  start fixing.

**What would close the gap:** instrument the live path to log
`update_previous_requested` and `proposed_action` at the id-mint gate, then
capture one live Japanese session. That requires editing a `.py`, which item 41
forbids — so it needs human approval before anyone attempts it.

> **Closed 2026-08-12 without needing any of that.** No instrumentation and no
> extra live session were required: the missing piece was the entry point
> (`_publish_sentence`, not `_route_stable_publish`) plus the missing `speaker`
> key. See §6a–6b. The proposed instrumentation is **no longer needed** — do
> not spend a live session on it.

---

## 7. Contradictions found

| where | what |
|---|---|
| sprint §1 (original) | "never reach the canonical ledger" — false; they reach it and are overwritten. Corrected under item 38. |
| item 38 / `replay_run.py` | Reported **14** losses; true count is **10**. Two false-positive classes (§2). Detector not fixed — item 41 forbids `.py` edits. |
| `stage_manifest.json` vs `export_coverage_report.json` | Same metric name `export_coverage_ratio` reported as **1.0** and **0.9** for the same run. |
| `coverage_ratio` gate | Passes on a run that four sibling fields in the same file show as 0.9. |

---

## 8. Item 42 execution brief — added 2026-08-12

Item 41 is closed. This section is what item 42 starts from, so that approving
item 42 is the only decision left to make.

### 8a. Run this first

```bash
cd Alpha_Live_Translator
"<repo_root>/.venv/Scripts/python.exe" tools/reproduce_problem_a.py
```

Exit `1` today, with sentence A destroyed. That command **is** sprint §4
step 5 ("prove the test catches the bug"): it already fails, for the proven
reason, before a line of the fix is written. Item 42 makes it exit `0`.

It is deliberately not under `tests/`, so the 410 / 5F + 2E + 2S baseline is
unaffected. Item 42 should additionally add a real regression test under
`tests/` once the fix exists.

### 8b. The `[gate]` decision — the one thing that needs a human

There are three places to intervene, and **the choice is not obvious**:

| # | where | effect | risk |
|---|---|---|---|
| 1 | the id-mint gate — stop it reading the stale `update_previous_requested` | narrowest, directly removes the proven mechanism | must remove the `final_revision_action` overwrite in the same change (§5.1), or two authorities remain |
| 2 | the boundary stabilizer — stop it signalling revise across a speaker boundary | fixes an input | 3 other inputs feed the same gate; 2 of the 14 recorded revises did **not** come from the stabilizer, so this alone is provably insufficient |
| 3 | add the missing `speaker` key to `previous_record` | lets the decision engine work as designed | **most dangerous — see 8c** |

Recommendation, not a decision: **#1, with the overwrite removed in the same
change.** It is the narrowest change that removes the proven mechanism, and it
leaves exactly one authority on the path.

### 8c. Why option 3 alone could make things worse

Adding the `speaker` key would make `speakers_confirmed_same` able to return
`True` for the first time in this corpus — which makes **Rules A–F reachable
for the first time in this corpus**. They have never executed in any recorded
run (0 of 111 decisions). Turning on a large block of never-exercised
decision logic while trying to fix a data-loss bug is how this project's
ledger already records six cases of one fix unmasking the next.

Worse, there is a known hole waiting behind that door: `_unique_content_lost`
returns `False` whenever `len(prev_n) < 8`, so `_content_loss_risk` clears and
a **short** previous record can be legally overwritten by disjoint text under
Rule C. Fixing the speaker key without first closing that hole would convert
a systematic loss into an intermittent one that is harder to detect.

If option 3 is chosen anyway, it must be a separate, separately-tested item —
not folded into item 42.

### 8d. Measurement caveat

Do not certify "problem A is fixed" with `tools/replay_run.py`'s
`_dropped_content` alone — §2 shows it over-reports by ~2 in 12. Use the
fixture for the mechanism and the corrected per-run counts in §2 for scale.

### 8e. Do not spend a live session on instrumentation

The 2026-08-11 plan to add temporary logging at the id-mint gate and capture a
live Japanese session is **obsolete**. The reproduction exists headlessly.
