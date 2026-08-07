# Issue 1 — Permanent Interim Ghost Line (⏳) — Fix Report

**Status:** Fixed, committed, unit-verified. Live test pending (your side).
**Commit:** `78eb59e2a83532801b8ef551ea7dc6df11cf7b0c`
**Files changed:** `alpha/constants.py`, `alpha/ui/main_window.py`, `tests/test_interim_ghost_line.py` (new)

---

## 1. Symptom

After a final transcript line committed, the interim preview line (the
gray, in-progress "... ⏳" line shown while someone is still speaking)
sometimes never disappeared — it stayed on screen permanently, next to
the already-committed permanent line, showing stale/unrelated text from
an earlier utterance.

## 2. Root cause

**File:** `alpha/ui/main_window.py`
**Function:** `_apply_final_interim_comparison` (runs every time a final
transcript line commits, decides whether to clear the on-screen interim)

The function decided "should I clear the interim line?" purely by **text
containment** between the just-committed final and the currently-shown
interim:

```python
if norm_interim in norm_final:
    action = "clear_interim"       # interim fully covered by final
elif norm_final in norm_interim:
    action = "keep_interim"        # final is only part of a longer interim
elif not norm_interim:
    action = "no_interim"          # nothing shown
# else: no branch matches -> action stays "keep_interim" (the default)
```

When the committed final and the displayed interim were **genuinely
unrelated** — neither text contains the other, both non-empty — no
branch matched. The `action` variable never left its default,
`"keep_interim"`, and the interim line was never cleared.

**Confirmed with real log evidence** (`troubleshooting/runs/.../logs/async_debug.log`):
a short new final ("The complete") committed while a ~300-character
stale interim from a *previous, unrelated* utterance
("So this conversation is a casual, So this conversation is a casual
everyday...") was still displayed. Neither string contained the other,
so the ghost line stayed on screen indefinitely — until, by chance,
another interim update happened to arrive later and overwrite it (which
might not happen again for the rest of the session).

### Why text alone can never fully solve this

Text comparison cannot distinguish two situations that look identical
from the text alone:

- A **stale ghost**: leftover interim from an earlier utterance that
  finished committing — should be cleared.
- A **live, newer utterance**: someone has already started a new
  utterance and its interim is genuinely still in progress — clearing it
  would delete real, in-progress content from the screen.

A correct fix needs to know *whose* utterance the interim belongs to,
not just *what* it says.

## 3. Fix — two independent layers

### Layer 1 — Identity gate (the correct decision, most of the time)

We discovered `deepgram_client.py` delivers every interim update
**twice**: once through `utterance_lifecycle.py` (which attaches a real
`canonical_utterance_id`), and once raw, with no identity at all. The
raw call landed *second* and silently overwrote/discarded the identity
the first call had attached — so the UI layer never actually had the
information it needed, even though it existed one step upstream.

**Changes:**
- `on_interim_transcript` now preserves the identity across that pair of
  calls instead of letting the identity-less raw call erase it.
- The interim's utterance identity is now stored alongside its text
  (`_latest_interim_utterance_id`), and reset whenever the interim is
  cleared or a new listening session starts.
- `_apply_final_interim_comparison` now takes the committing item's
  `canonical_utterance_id` and, in the "genuinely unrelated" case:
  - **same utterance ID** → clear (the final legitimately supersedes this
    preview, text just changed via a merge/correction)
  - **different utterance ID, both known** → **keep** (this is a real,
    still-live newer utterance — do not delete it)
  - **identity unavailable on either side** → clear (the confirmed
    real-world ghost pattern — safe default, matches the evidence)

A live interim that gets wrongly cleared in a rare edge case self-heals
within ~200ms (the next interim update just redraws it). A ghost that
gets wrongly kept used to persist for the rest of the session — that
asymmetry is why "unknown → clear" is the safe default.

### Layer 2 — Liveness watchdog (the guarantee — this is what makes it "100%")

Layer 1 makes the *correct* decision whenever identity is available, but
it is still a decision, not a guarantee. So we added a second,
independent mechanism that doesn't depend on getting any decision right:

**An interim line, by definition, previews an utterance still being
spoken — so a genuinely live one keeps getting refreshed roughly every
~100-200ms.** A new watchdog check, hooked into the app's existing
100ms UI tick (no new timer or thread added), asks one question every
tick: *"has this interim line been refreshed recently?"* If not — if
`INTERIM_GHOST_TTL_MS` (1500ms) has passed with no update — it is not
live, it is an orphan, and it gets removed automatically, with a log
entry (`INTERIM_GHOST_LINE_CLEARED_BY_WATCHDOG`) so it's visible if it
ever fires.

This is a **structural invariant**, not a heuristic: it holds no matter
which code path created the orphan — a bug in Layer 1, a future code
change that forgets to call `_clear_interim_tail()`, anything. As long
as this watchdog runs, a permanent ghost line is not possible. This is
the piece that answers "will this bug definitely never come back" — even
if some future code change reintroduces a Layer-1-style mistake, the
watchdog still catches it within 1.5 seconds.

## 4. What we verified (before you run a live test)

1. **Syntax check** — both changed files parse cleanly.
2. **Direct behavioral verification** — bound the real `AlphaApp` methods
   (`_apply_final_interim_comparison`, `_clear_interim_tail`,
   `_check_interim_ghost_watchdog`, `_handle_interim_transcript_ui`,
   `on_interim_transcript`) onto a lightweight stub object with no real
   Tk widgets, and ran every branch directly, including the exact text
   from the confirmed bug evidence ("The complete" vs. the stale
   ~300-char interim). **19/19 checks passed.**
3. **Proved the tests actually catch the bug** — temporarily disabled the
   identity-gate branches (reverting to old behavior) and reran the new
   test file: **3 tests failed exactly as expected**
   (`test_unrelated_same_utterance_clears`,
   `test_unrelated_different_utterance_keeps_live_interim`,
   `test_watchdog_reaps_ghost_even_when_comparison_keeps_it`). Then
   restored the fix and confirmed the diff was back to exactly the
   intended change (+120/-3 in `main_window.py`). This is a live
   before/after control, not just "tests pass."
4. **New permanent regression test file**:
   `tests/test_interim_ghost_line.py` — 19 tests across 3 groups
   (comparison-branch coverage, watchdog behavior, identity plumbing
   across the double-delivery). These now run as part of the normal
   suite forever, so if anyone breaks this logic later, the suite fails
   immediately instead of the bug silently reappearing.
5. **Full existing suite** — 169 tests total (150 existing + 19 new).
   Result: **5 failures / 2 errors / 2 skipped — identical to the
   pre-existing baseline** documented in `ROOT_CAUSE.md` (glossary
   packaging script tests, a commit-gate test fixture gap, a
   phase-constants spec test — all pre-existing, unrelated to this
   change, present before this fix and unchanged by it). **No new
   regressions.**

## 5. Final changes (summary)

| File | Change |
|---|---|
| `alpha/constants.py` | Added `INTERIM_GHOST_TTL_MS = 1500` |
| `alpha/ui/main_window.py` | Identity preserved across the double interim delivery; interim now tracks its own `canonical_utterance_id`; `_apply_final_interim_comparison` gains an identity-aware "unrelated" branch; new `_check_interim_ghost_watchdog()` hooked into the existing 100ms tick |
| `tests/test_interim_ghost_line.py` | New — 19 regression tests |

Nothing else was touched: `utterance_lifecycle.py`, `duplicate_protection.py`,
Issue 2 (repeating/broken sentences), and every previous fix in this
engagement are untouched.

---

## 6. How to verify with a live test

**What to do:**
1. Start a live listening session (English or Japanese, whichever you
   normally test with).
2. Speak naturally for a few minutes, including some pauses and
   overlapping/back-to-back sentences if you can (two people talking in
   quick succession is the best stress case).
3. Watch the transcript panel while talking, and let the session run for
   a while — don't stop immediately after one sentence.

**What "working correctly" looks like:**
- The gray "... ⏳" preview line should always represent *whatever is
  currently being said*, and should disappear or update within roughly
  half a second to a second of a sentence finishing/committing.
- You should **never** see a "... ⏳" line sitting on screen showing old
  text while a completely different, already-committed permanent line is
  sitting right above/below it.
- If you deliberately pause mid-sentence for 2+ seconds, the preview line
  should not linger forever unrefreshed — it should clear itself within
  ~1.5 seconds if nothing new is coming (this is the watchdog).

**How to double-check it under the hood (optional, more thorough):**
After the session, check the run's log files under
`troubleshooting/runs/<latest run folder>/`:
- Search `logs/async_debug.log` (or wherever `[INTERIM] final comparison`
  entries are written) for the `action` field. You should see a healthy
  mix of `clear_interim`, `clear_interim_unrelated`,
  `clear_interim_same_utterance`, and occasionally
  `keep_interim_other_utterance` — and you should **not** see any
  `"action": "keep_interim"` entry that stays stuck for a long stretch of
  subsequent finals (that would indicate the ghost pattern recurring).
- Search for `INTERIM_GHOST_LINE_CLEARED_BY_WATCHDOG` in the Japanese
  accuracy log. Seeing this occasionally is fine/expected (it's the
  backstop doing its job on a genuinely stale case); seeing it very
  frequently during normal, continuous speech would suggest Layer 1 is
  missing identity more often than expected and would be worth flagging
  back to me.
- If you want, I can also write a small script to scan a completed run
  folder afterward and summarize the `[INTERIM] final comparison` action
  counts and any watchdog firings automatically — say the word if you'd
  like that instead of manually grepping.

If you see the bug reproduce despite this, the most useful thing to send
back is the specific run folder path (`troubleshooting/runs/<...>/`) so
I can check the logs directly rather than needing you to describe it.
