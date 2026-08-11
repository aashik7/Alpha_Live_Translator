# Item 41 — problem A root cause — live working state

**Resume point.** If you are a new session picking this up: read this file,
then continue from the first unchecked box in §6. Delete this file in the
commit that closes item 41.

Authoritative context is `CLIENT_DELIVERY_SPRINT_v5.md` (§1 row A, §8 item
41, §9). This file is working state only, not a second authority.

---

## 1. Approval and scope

Human approved item 41 on 2026-08-11 and asked for it to be carried to
completion. **Item 41 is investigation only** — the deliverable is a
written, falsified proof of problem A's root cause plus a reproducible
fixture. **Item 42 is the fix and is human-gated: do not start it**, no
matter how obvious the cause looks.

Hard constraints (from the item 41 prompt, still binding):
- **Do not modify any `.py` file.** Not a debug print, not a comment. If
  the fixture cannot be built without touching `.py`, stop and ask.
- No architecture changes, no drive-by fixes (log in §9 and move on).
- Locate code with grep anchors, never line numbers.
- Derive the repo root from the working directory; never hardcode it.
- Cannot run the app — everything comes from `troubleshooting/runs/`.

---

## 2. Orientation result (done 2026-08-11)

- Repo root: derived from cwd, contains both `CLIENT_DELIVERY_SPRINT_v5.md`
  and `Alpha_Live_Translator/`. Confirmed.
- Target run: `Alpha_Live_Translator/troubleshooting/runs/v3.3.5.5.8.5.26.5.3-20260807-160529`
- **Baseline: 410 tests, 5 failures + 2 errors + 2 skipped, the same 7
  names.** The item 41 prompt expects 354 — **the prompt is stale, the repo
  is fine.** 354 + 10 (item 38) + 4 (38b) + 17 (39) + 25 (51) = 410, and the
  7 failing names are unchanged. Not a stop condition.
- **`tools/replay_run.py` (item 38) exists and works.** Used throughout.

## 3. What items 38/38b already established — do NOT re-derive

Item 41 does not start from zero. Already proven and committed:

- **Problem A's mechanism is an id collision, not a failed ledger write.**
  The assembler commits two *textually disjoint* sentences under one
  `canonical_utterance_id`; the ledger keys on that id, so the second lands
  as a revision of the first and the first sentence's words never reach the
  export. Commit `2d34a41`.
- **§1's original wording ("never reach the canonical ledger") is already
  corrected** in the sprint file. Records *do* reach it, then get
  overwritten.
- **The 10/10/9 framing is a misleading metric.** 10 stable rows resolve to
  9 *distinct* utterance ids → 9 ledger records → 9 export lines. Nothing
  "failed to arrive". Real loss on this run is **2 sentences**, both
  `overwritten_by_id_collision`, both confirmed absent from the export.
- **Timing is NOT the mechanism.** Real-timer replay (item 38b, `--real-timer`)
  fixes decision-count divergence but still reproduces 0 of 14 losses.
  Commit `960f907`.
- **The leading candidate**, cross-validated against
  `logs/japanese_accuracy.log`'s `STABLE_REVISION_DECISION` events on 12 of
  13 observed id-reuse events across all 6 runs:
  `japanese_sentence_assembler.py` computes `update_previous_requested`
  (grep anchor: `update_previous_requested = bool(`) from boundary-stabilizer
  signals *before* `decide_stable_revision_action` runs. When that function
  returns `final_revision_action == "append"` (correctly: a new disjoint
  sentence), the code resets `stable_layer_update_previous` /
  `post_update_previous` and clears `metadata["boundary_should_revise"]` —
  but **never reassigns `update_previous_requested` itself**. The id-mint
  gate (grep anchor: `proposed_action = "revise_previous" if update_previous_requested`)
  reads that stale variable rather than `final_revision_action`.

**This is a lead, not a proof.** Item 41's real remaining work is Phase 4:
falsify it properly, and kill the alternatives with quoted evidence.

---

## 4. Phase 1 + 2 findings (done 2026-08-11)

### Phase 1 — four populations, run `...20260807-160529`

| | population | source artifact | count |
|---|---|---|---|
| P1 | assembler commit decisions | `logs/japanese_accuracy.log`, `STABLE_JAPANESE_COMMIT` events | **10** |
| P2 | stable commit rows | `transcripts/stable_commits.jsonl` (`stable_commit_id` present) | **10** |
| P3 | canonical ledger records | `evidence_streams/canonical_commits.jsonl` | **9** |
| P4 | FINAL export lines | `transcripts/Alpha_output_FINAL.txt` (non-blank) | **9** |

**10 / 10 / 9 reproduces numerically.** But the framing behind it is wrong:

- **ids in P2 but NOT in P3: NONE.** Zero records failed to reach the ledger.
- P2's 10 rows carry only **9 distinct** `canonical_utterance_id`s.
  `jp-utt-19dbf8832ec0` appears **twice**.

So the "missing 10th record" is a **duplicate id**, not a missing record.
§1's original claim ("never reach the canonical ledger") is falsified —
already corrected in the sprint file by item 38.

### Phase 2 — the authoritative join key and the true loss count

Join key is `canonical_utterance_id` (`assembler_metadata.canonical_utterance_id`
in `stable_commits.jsonl`, top-level in `canonical_commits.jsonl`) — verified,
not assumed: it is the only key present in both populations and it is what the
ledger keys on.

**TRUE LOSS COUNT = 10 across the 6 runs, not 14.** Two independent classes of
false positive were found in item 38's own `_dropped_content` detector, both
confirmed by reading the export directly:

1. **Leading-punctuation stripping.** `jp-utt-f8bf3bbf9fb2` (`...160529`):
   stable text `。その人形を…` (42 ch), ledger text `その人形を…` (41 ch) — a
   cleanup step dropped the leading `。`. The text **is** in the export.
2. **Text correction.** `jp-utt-453c4fd9c80f` (`...155334`): stable
   `…悲しいなっいう自分が…`, export `…悲しいなっていう自分が…` — a
   business-accuracy correction inserted `て`. The text **is** in the export.

Neither is content loss. The detector's whitespace-only normalisation cannot
see either. **Logged, not fixed — fixing it means editing a `.py`, which
item 41 forbids.** Carry to §9 of the sprint file.

### The exact rule, and it is exceptionless

Measured with a correction-tolerant test (best-match ratio against every export
line plus a distinctive mid-sentence probe):

> A sentence is lost **if and only if** its `canonical_utterance_id` is carried
> by more than one `stable_commits` row **and** those rows' texts are mutually
> disjoint (not nested). The **earliest** row's text is the one lost; the last
> row's text survives.

Per-run confirmation — dup ids, of which legitimately nested, losses:

| run | stable | dup ids | nested (legit) | disjoint | **losses** |
|---|---|---|---|---|---|
| `...155922` | 3 | 0 | 0 | 0 | **0** |
| `...160130` | 4 | 1 | 0 | 1 | **1** |
| `...160529` | 10 | 1 | 0 | 1 | **1** |
| `...134815` | 29 | 5 | 2 | 3 | **3** |
| `...155334` | 32 | 5 | 0 | 5 | **5** |
| `...174516` | 36 | 0 | 0 | 0 | **0** |

Every lost row scored 0.16–0.36 best-match against the export with its probe
absent; every surviving row scored 0.89–0.97 with its probe present. There is
no grey zone and no unexplained loss. The earlier "dup-id count ≠ loss count"
asymmetry is fully accounted for by the nested/disjoint split.

---

## 5. Phase 3 + 4 — the causal chain, positively evidenced

### The controlled natural experiment (this is the core proof)

Every 2nd-or-later `stable_commits` row on an already-used
`canonical_utterance_id` was cross-referenced against its
`STABLE_REVISION_DECISION` event in `logs/japanese_accuracy.log`:

| texts disjoint? | `update_previous_requested` | `final_action` | count | loss? |
|---|---|---|---|---|
| **True** | **True** | `append` | **9** | **yes, all 9** |
| False | False | `append` | 3 | no, all 3 |
| True | (log match failed) | — | 2 | yes |

`final_action` is **`append` in every single case** — with
`decision_reason: speaker_boundary_forced_new_line`.
`decide_stable_revision_action` *always correctly identified a new sentence*.
The id was reused anyway, and reuse-with-loss happened **exactly** when
`update_previous_requested` was `True`. The 3 rows where it was `False`
reused the id harmlessly (nested text, genuine revision). Same code path,
same run, differing in one variable, opposite outcomes — a control group.

### The chain, by grep anchor (never line numbers)

1. `update_previous_requested = bool(` — assigned **once**, from
   boundary-stabilizer signals, *before* the decision function runs.
   `grep -n "update_previous_requested\s*=" japanese_sentence_assembler.py`
   returns exactly **one** assignment; every other hit is a keyword argument.
   **It is never reassigned.**
2. `revision_decision = decide_stable_revision_action` — the authority that
   was specifically hardened by Batch 3 items 10/11/12/19/20c. Returns
   `final_action="append"` for these.
3. Anchor `proposed_action = "revise_previous" if update_previous_requested`
   — **the id-mint gate reads the stale variable, not `final_revision_action`.**
4. Anchor `final_revision_action = "revise_previous" if proposed_action ==` —
   the correct verdict from step 2 is then **overwritten** by a value derived
   from the stale variable. The right answer is computed, then discarded.
5. The ledger keys on `canonical_utterance_id`; the second commit lands as
   `applied_action="revise"` and replaces the first sentence's text.

### Consequential finding for item 42 (not fixed here)

Step 4's overwrite happens **before** the `revision_target_id` guard that
item 20b installed (anchor: `metadata["revision_target_id"] = (`). That guard
tests `final_revision_action == "revise_previous"` — i.e. it is now keyed off
the corrupted value. **Item 20b's fix is silently undermined on this path.**

### The four required facts

- **(a) 1 loss on `...160529`** — exactly one duplicated id with disjoint text.
- **(b) 08-08 volume** — 3 on `...134815` and 5 on `...155334`, matching the
  disjoint-duplicate counts exactly. (`...174516` on 08-09 has zero, so §1's
  "and 08-09" was already retracted in §9 on 2026-08-10 and stays retracted.)
- **(c) English unaffected** — anchor `def should_use_japanese_final_stabilizer`
  returns True only for `lang == "ja"` / `ja-*`. English never reaches this
  file at all; it uses `utterance_lifecycle.py` (that path had its own
  separate defect, problem F, fixed under item 51).
- **(d) `coverage_ratio` read clean** — and the contradicting evidence was in
  the same file. `export_coverage_report.json` on `...160529` records
  `coverage_ratio: 1.0` and `coverage_passed: true`, while **in the same
  file** `source_commit_coverage_ratio`, `lineage_coverage_ratio`,
  `text_coverage_ratio` and `export_coverage_ratio` all read **0.9** — i.e.
  9 of 10, precisely the lost commit. The gate keys on the one field that
  compares canonical→final (both post-overwrite) and ignores the four that
  compare commit→export. Additionally `stage_manifest.json` reports
  `export_coverage_ratio: 1.0` for the same metric name that
  `export_coverage_report.json` reports as 0.9 — an inconsistency worth its
  own line in §9.

---

## 6. Checklist — resume from the first unchecked box

- [x] **0. Orientation** — baseline, tooling, run path. Done, see §2.
- [x] **1. Phase 1 — four populations, independently derived.** P1 assembler
      `commit_new` decisions, P2 `stable_commits.jsonl` rows, P3 canonical
      ledger records, P4 FINAL export lines. Record the exact reproducible
      command for each. State whether 10/10/9 reproduces **and** whether that
      framing is meaningful (see §3 — it is not).
- [x] **2. Phase 2 — identify the lost records.** Authoritative join key
      (verify, do not assume it is `canonical_utterance_id`), the actual
      Japanese text, session position, and what distinguishes them from the
      survivors.
- [x] **3. Phase 3 — hop-by-hop trace**, grep anchors only, from "assembler
      emits commit_new" to "record exists in ledger". Every early return,
      swallow, guard, dedup, id-space conversion. Mark Japanese-only vs
      shared-with-English hops.
- [x] **4. Phase 4 — falsify.** ≥4 candidates (dedup/containment false
      positive, id-space mismatch, swallowed exception, race/ordering, plus
      any Phase 3 suggests). Predictions written BEFORE checking. Kill with
      quoted evidence. Survivor must explain all four of: the loss in
      `...160529`; the higher-volume 08-08 reproductions; why English does
      not show it; why `coverage_ratio` read clean. **If ≥2 survive, say so
      — do not pick the likelier one.**
- [x] **5. Phase 5 — fixture.** Minimal, deterministic, headless, committed.
      Must fail NOW against unfixed code for the proven reason. **Not** the
      fix's regression test. No `.py` edits — fixture goes in `tests/` or
      `troubleshooting/` as a new file, which is allowed (new file ≠
      modifying existing `.py`). If that reading is wrong, ask.
- [x] **6. Phase 6 — write-up** at `troubleshooting/problem_A_root_cause.md`
      (check that directory's existing naming convention first). Contents per
      the prompt §9, including **what item 42 must be careful of** re: §0
      rule 2 (two authorities alive).
- [x] **7. Update sprint file** §8 (item 41 status, model = Opus 5) and §9.
- [x] **8. Commit** — docs + fixture only, **zero `.py` changes** — and
      delete this file in that commit.

---

## 7. Facts established — do not re-derive

- Baseline 410 / 5F + 2E + 2S / 7 stable names. Venv at
  `<repo_root>/.venv/Scripts/python.exe`, `unittest discover` only, no pytest.
- 6 replayable Japanese runs: `...160529`, `...155922`, `...160130`,
  `...134815`, `...155334`, `...174516`. English runs record zero genuine
  provider ingress by design, so they cannot be replayed.
- Per-run dropped-sentence counts (item 38, `tools/replay_run.py`):
  `...155922` 1, `...160130` 2, `...160529` 2, `...134815` 3, `...155334` 6,
  `...174516` 0. Total 14, all `overwritten_by_id_collision`, 14/14 absent
  from their exports.
- `logs/japanese_accuracy.log` is **pipe-delimited** (`timestamp | {json}`),
  not plain JSONL. A naive `json.loads(line)` fails on every line.
- Local uncommitted, deliberate, leave alone: `alpha/constants.py` has
  `TEMP_AUDIO_RETENTION_ENABLED` / `TEMP_AUDIO_AUTO_DELETE_ENABLED` set
  `False` at the user's request so live-test audio is not auto-deleted.
