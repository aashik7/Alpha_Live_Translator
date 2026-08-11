# Item 51 — problem F — live working state

**Purpose of this file:** item 51 was approved for implementation on
2026-08-11 in a session that might hit a token limit mid-work. This file
is the resume point. **If you are a new session picking this up: read
this file, then continue from the first unchecked box in §5.** Delete
this file in the same commit that closes item 51.

Authoritative context stays in `CLIENT_DELIVERY_SPRINT_v5.md` (§1 row F,
§8 item 51, §9). This file is working state only, not a second authority.

---

## 1. Approval status

**Human approved implementation 2026-08-11** ("Allow to fix 51"), after
being given the recommendation to do 51 before item 41. The `[gate]` on
item 51 is therefore **satisfied** — proceed to implement, do not re-ask.

Standing constraints that still apply (v5 §0): no architecture changes,
never leave two authorities alive, no drive-by fixes.

---

## 2. What problem F is

English commits self-concatenate. Deepgram sends successive texts for the
same growing utterance; when a later one is a *reformatted* variant of the
earlier ("50 percent" → "50%", "mister" → "Mr.", punctuation changes),
`utterance_lifecycle.py`'s `_merge_lexical()` fails to recognise them as
the same utterance and glues them together instead of replacing.

Corruption compounds every tick and reaches the canonical ledger, the
translation input, and the client-facing export identically.

Measured on run `...20260811-182940` (English live test, in
`Alpha_Live_Translator/troubleshooting/runs/`): 5 of 54 export lines are
over 400 characters and carry **85.9%** of the export's total characters.
Worst single line: 5039 characters from ~112 comma-joined fragments.

Pre-existing, not introduced by this sprint — same pattern on run
`...20260808-133236` (2026-08-08). Japanese is unaffected: it never
reaches this function (`should_use_utterance_lifecycle()` returns False
for `ja`, routing to `japanese_sentence_assembler.py` instead).

---

## 3. Root cause — CORRECTED 2026-08-11, read this before coding

The first write-up of problem F (commit `729bd62`, in v5 §1 row F and §9)
said two things. **One was right, one was wrong.**

**Right:** `_merge_lexical()` (`utterance_lifecycle.py` ~L91-131) is the
cause. Its containment checks (`curr_n.startswith(prev_n)`,
`prev_n in curr_n`, …) need near-literal substring matches. Its
word-overlap fallback — which exists precisely to catch reformatted
variants — compares tokens **with punctuation still attached**, because
`_norm_text()` only lowercases and collapses whitespace. So `"olympia."`
and `"olympia,"` count as different words, overlap falls below the 0.6
threshold, and control reaches the concatenation branch at the end of the
function that is meant for genuinely separate adjacent phrases.

**WRONG — retracted:** the claim that `_apply_active_update_locked()`
"correctly classifies some of these as `REPLACE_ACTIVE` but writes the
concatenated text regardless." It does not. That function *derives* its
decision from `_merge_lexical`'s return value:

```python
merged = _merge_lexical(previous_text, lexical)
if _norm_text(merged) != curr_n and _norm_text(merged) != prev_n:
    decision = EXTEND_ACTIVE          # <-- concatenation lands here
elif curr_n.startswith(prev_n) or prev_n in curr_n:
    decision = REPLACE_ACTIVE
else:
    decision = REPLACE_ACTIVE
```

Verified by driving the real function: both known corruption pairs
classify as `EXTEND_ACTIVE`, not `REPLACE_ACTIVE`. So
`active.text = merged` at ~L1544 is *consistent* with the decision, not a
contradiction of it. **There is one root cause, not two, and it is
entirely inside `_merge_lexical`.** Fixing `_merge_lexical` fixes the
decision label as a side effect.

This retraction must be reflected in v5 §1 row F and §9 — see §5 below.

---

## 4. Design (fill in as it is settled)

_Not yet finalised. See §5 step 2._

---

## 5. Checklist — resume from the first unchecked box

- [x] **1. Reproduce from real recorded input, not from the UI transcript.**
      Extract the actual ordered `(is_final, text)` event sequence the
      lifecycle received on run `...20260811-182940`, drive the real
      `UtteranceLifecycleOwner`, and confirm the recorded corrupted export
      lines come back out. The UI transcript is post-merge output and must
      not be used as the input fixture.
- [x] **2. Design the fix, write it into §4 above.** Must state: what
      changes, what triggers the bug, why this addresses the root cause,
      what could go wrong, what stays unchanged. Key risk to reason
      about explicitly: `_merge_lexical` must still concatenate genuinely
      separate adjacent chunks — over-correcting turns problem F into
      silent content loss, which is worse and harder to see.
- [x] **3. Implement.** Only `_merge_lexical` (and only more if step 2
      justifies it in writing). No architecture change.
- [x] **4. Prove the test catches the bug** (v5 §4 step 5, non-optional):
      write the regression test, temporarily revert the fix, confirm the
      test fails the way the original bug did, restore, confirm it passes.
- [x] **5. Verify no regression.** Full suite from
      `Alpha_Live_Translator/`:
      `SKIP_TK_INTEGRATION_TESTS=1 "<repo>/.venv/Scripts/python.exe" -m unittest discover -s tests -p "test_*.py"`
      Baseline before this item: **385 tests, 5 failures + 2 errors + 2
      skipped, 7 stable names.** One extra name
      (`test_task9_report…test_inactivity_timeout_fallback_survives_immediate_real_stop_5x`)
      is a known load-flake — if it is the *only* new name, re-run before
      concluding a regression.
- [x] **6. Re-score the English run** with the fix in place and record the
      new numbers next to the pre-fix ones (5/54 lines, 85.9% of chars).
- [x] **7. Update `CLIENT_DELIVERY_SPRINT_v5.md`:** §1 row F (fix landed +
      the §3 retraction above), §8 item 51 status, §9 session log.
- [x] **8. Commit and push** (one fix per commit), then **delete this
      file** in the same commit.

---

## 6. Facts already established — do not re-derive

- Baseline suite: 385 tests / 5F + 2E + 2S / 7 stable names.
- Venv: `<repo_root>/.venv/Scripts/python.exe`. No pytest; `unittest
  discover` only. Never `sed -i` on `.py` files (flips CRLF).
- `_norm_text()` = `" ".join(text.strip().lower().split())` — lowercase
  and whitespace only, **no punctuation stripping**. This is the crux.
- Two confirmed corruption pairs, taken from the real corrupted output,
  both currently returning a concatenation and classifying as
  `EXTEND_ACTIVE`:
  - `("So I'm mister Olympia.", "So I'm Mr. Olympia,")`
  - `("Okay. Easy. 50 percent", "Easy. 50%")`
- Runs to test against (under `Alpha_Live_Translator/troubleshooting/runs/`,
  gitignored, local only): `...20260811-182940` (en, this sprint's live
  test), `...20260808-133236` (en, older, same bug), `...20260811-175628`
  (ja, must stay clean — regression canary for "did I break Japanese").
- `analyze_alpha_vs_reference.py`'s duplicate detector **does not see this
  corruption** (`cumulative_duplicate_count: 0` on the corrupted run). Do
  not use it to judge whether the fix worked. Use direct line/char counts.
- Local uncommitted change, deliberate, leave alone unless asked:
  `alpha/constants.py` has `TEMP_AUDIO_RETENTION_ENABLED` and
  `TEMP_AUDIO_AUTO_DELETE_ENABLED` set to `False` at the user's request so
  live-test audio is not auto-deleted.
