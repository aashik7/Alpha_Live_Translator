# Item 42 — fix problem A — live working state

**Resume point.** New session: read this, then continue from the first
unchecked box in §5. Delete this file in the commit that closes item 42.

Approved by the human 2026-08-12 ("complete task 42 … fix all the issues
properly and completely accurately"). The `[gate]` on item 42 is **satisfied**
— proceed, do not re-ask.

Proof of the bug: `PROBLEM_A_ROOT_CAUSE.md` (item 41). Its §8 is the brief.

---

## 1. The bug, in one paragraph

`_publish_sentence` computes `update_previous_requested` **before**
`decide_stable_revision_action` runs, and never recomputes it. When the engine
returns `append`, the `append` branch clears that flag's four *inputs* but not
the variable. The id-mint gate then reads the stale variable, reuses the
previous `canonical_utterance_id`, and the ledger's revise replaces
`final_text` in place — destroying the earlier sentence. 10 real sentences
lost across the recorded corpus.

## 2. Anchors (line numbers rot — these are the anchors)

| what | anchor |
|---|---|
| the stale flag, assigned once | `update_previous_requested = bool(` |
| engine verdict | `final_revision_action = str(revision_decision.get("action")` |
| append branch clears inputs, not the flag | `if final_revision_action == "append":` |
| **the id-mint gate** | `proposed_action = "revise_previous" if update_previous_requested` |
| **the overwrite of the verdict** | `final_revision_action = "revise_previous" if proposed_action ==` |
| destructive ledger write | `canonical_transcript_ledger.py` → `target["final_text"] = text` |

## 3. The trap — do NOT take it

**Do not add the missing `speaker` key to `previous_record`.** It makes
Rules A–F reachable for the first time (0 of 111 decisions have ever executed
them), during a data-loss fix. Known hole behind that door:
`_unique_content_lost` returns `False` when `len(prev_n) < 8`, so a short
previous record can be legally overwritten by disjoint text under Rule C.
That converts a systematic loss into an intermittent one. Separate item if
ever wanted.

## 4. Design constraint that makes this non-trivial

Naively pointing the gate at `final_revision_action` makes it **always
`commit_new`**, because the engine always says `append` (the missing speaker
key). That removes the data loss but converts every *genuine* revision — the
3 nested/extension cases in the corpus that lost nothing — into a second
appended line, i.e. **visible duplication**, the Japanese twin of problem F.

So the fix must satisfy **both** properties at once:

- **P1 — no loss:** a disjoint follow-up must never destroy a committed
  sentence.
- **P2 — no duplication:** a genuine extension/restatement must still revise
  in place, not append a near-copy.

The empirical rule proven in item 41 §2 is the discriminator, and it is
exceptionless across the corpus: *lost iff the id is reused **and** the two
texts are mutually disjoint (not nested)*. A revise whose new text contains
the old is harmless.

**Chosen approach:** make the gate's revise decision **content-safe** — allow
`revise_previous` only when the candidate actually contains/extends the
previous record's text; otherwise mint a new id. And **remove the overwrite**
of `final_revision_action` in the same change, so one authority remains
(§0 rule 2).

## 5. Checklist — resume from the first unchecked box

- [ ] Build a two-property harness: disjoint case (P1) and nested/extension
      case (P2), both driven through the real `_publish_sentence`.
- [ ] Confirm pre-fix: P1 FAILS (loss), P2 PASSES (revise, no dup).
- [ ] Implement the content-safe gate + remove the verdict overwrite.
- [ ] Confirm post-fix: **both** P1 and P2 pass.
- [ ] `tools/reproduce_problem_a.py` must now exit **0** (was 1).
- [ ] Re-verify item 20b's regression test still means what it claims — its
      guard reads `final_revision_action`, whose lifetime this change alters.
- [ ] Add a regression test under `tests/`.
- [ ] Full suite: **410 + new**, must stay 5F + 2E + 2S, same 7 names.
      (`test_task9_report…real_stop_5x` is a known load-flake — if it is the
      ONLY new name, re-run before concluding a regression.)
- [ ] Re-run `tools/replay_run.py --all` and `tools/score_run.py --all`;
      remember `_dropped_content` over-reports by ~2 in 12 (item 41 §2), so
      do not certify with it alone.
- [ ] Update `CLIENT_DELIVERY_SPRINT_v5.md` §1 row A, §8 item 42, §9.
- [ ] Commit, push, delete this file.

## 6. Facts — do not re-derive

- Baseline before item 42: **410 tests, 5F + 2E + 2S, 7 stable names.**
- Venv `<repo>/.venv/Scripts/python.exe`; `unittest discover` only, no pytest.
- Never `sed -i` on `.py` (flips CRLF). Use Edit.
- Fixture: `Alpha_Live_Translator/tools/reproduce_problem_a.py` — exits 1 while
  the bug exists, 0 when fixed. Not under `tests/` on purpose.
- True corpus loss count is **10, not 14** — `replay_run.py::_dropped_content`
  over-reports (leading-punctuation strip + business-accuracy rewrite).
- Local uncommitted and deliberate, leave alone: `alpha/constants.py` has
  `TEMP_AUDIO_RETENTION_ENABLED` / `TEMP_AUDIO_AUTO_DELETE_ENABLED` = `False`.
