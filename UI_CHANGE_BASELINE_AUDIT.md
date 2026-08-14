# UI Change — Baseline Audit and Verification Contract

**Frozen at commit `ac25308e77d14b6fd0a3f0f367ba3540a53fcf58` (`ac25308`), 2026-08-14 17:04 +0900.**

---

## 0. READ THIS FIRST — instructions for Claude Code

You are being handed this file **after a UI redesign of Alpha Live Translator**.
Your job is to prove the redesigned UI still behaves exactly like the version
frozen above, and to fix it where it does not.

**Do this, in this order. Do not skip steps and do not reorder them.**

1. Read §1 to learn what the baseline is.
2. Run §7 (automated checks). Every number there is a hard expectation.
3. Read §3 and §4 and check each contract against the new code, one by one.
   These are the seams where UI code touches the transcription pipeline. They
   are the only places a UI change can silently break data correctness.
4. Run §8 (live test) and compare against §9's measured numbers.
5. Report using §10's format. State each contract as PASS or FAIL with evidence.

**Rules that override your normal judgement while doing this:**

- **Never "fix" a contract by changing the baseline number.** If a check fails,
  the new UI is wrong until proven otherwise.
- **The 7 failing tests in §7.2 are pre-existing.** They fail on the frozen
  baseline too. Do not investigate them, do not fix them, do not count them as
  regressions. An 8th distinct failing name IS a regression.
- **`test_task9_report` is a documented load-flake and is the one allowed
  exception to the rule above.** It is not in §7.2's list because it does not
  appear on every run. If it appears, run it alone 4 times before concluding
  anything; it fails ~3/4 in isolation both with and without changes. Any
  *other* name outside §7.2 is a regression.
- **Do not trust the UI's appearance as evidence.** Several defects in this
  project looked correct on screen and were losing data. Always check the
  exported files in `troubleshooting/runs/<run>/`.
- **Tk is not covered by the test suite.** Only 2 test files touch Tk and both
  are skipped via `SKIP_TK_INTEGRATION_TESTS=1`. Automated checks CANNOT catch
  a UI regression. §8's live test is mandatory, not optional.

---

## 1. Baseline identity

| Fact | Value |
|---|---|
| Commit | `ac25308e77d14b6fd0a3f0f367ba3540a53fcf58` |
| App version | `3.3.5.5.8.5.26.5.3` |
| Test suite | **674 tests**, 5 failures + 2 errors + 3 skipped |
| Distinct failing names | **7** (listed in §7.2) |
| Python | 3.14, venv at `.venv/` in repo root |
| Test runner | `unittest` only — **there is no pytest in this venv** |

To compare against the baseline, check it out **into a separate worktree**:

```bash
git worktree add ../alpha-baseline ac25308
```

**Do not `git checkout ac25308` in the working tree.** This file did not exist
at `ac25308` (it was added afterwards in `b2f65d4`), so checking it out deletes
the instructions you are reading, and it detaches HEAD — work committed there is
easy to lose. The worktree gives you both versions side by side. Remove it with
`git worktree remove ../alpha-baseline` when done.

---

## 2. Architecture in one paragraph

Audio → Deepgram WebSocket → `deepgram_client.py` → `utterance_lifecycle.py`
(English) or `japanese_sentence_assembler.py` (Japanese) → commit →
`_publish_final_transcript_segment` → `transcript_queue` → `duplicate_protection.
_display_transcript_item` → `TranscriptStore` **and** `canonical_transcript_ledger`
→ at Stop, the **frozen ledger** produces `Alpha output.txt`.

**The UI reads from two places and they are not the same:**

- **Live transcript pane** ← `TranscriptStore` (via `_render_transcript_from_store_now`)
- **`Alpha output.txt`** ← **frozen canonical ledger** (via `serialize_export_payload`)

A UI change that only touches rendering cannot corrupt the export. A UI change
that touches `TranscriptStore`, the queue, or the publish path **can**.

---

## 3. HARD CONTRACTS — a UI change must not break these

Each has an ID. Report each as PASS/FAIL in §10.

### C1 — Background threads must never touch Tk

`FORBID_BACKGROUND_TK_CALLS = True`, `TK_SAFE_PIPELINE_MODE = True`.

Any Tk call from a non-UI thread must go through `_run_on_ui_thread`
(`main_window.py:7697`). Violations log `BACKGROUND_TK_CALL_BLOCKED`.

**Why it matters:** an `AttributeError` on a background thread reaches the app's
thread excepthook and is recorded as a **crash** (`CRASH_HOOK_TRIGGERED`). This
already happened once this sprint from a websocket ping thread.

**Check:** new/moved widget code must not be called from the Deepgram, audio,
translation, or stop-finalize threads without `_run_on_ui_thread`.

### C2 — Widgets must be created eagerly, never lazily

Background code references widgets by attribute:
`self.initial_verse_box`, `self.translated_verse_box`, `self.summary_body_box`,
`self.status_text_label`, `self.transcript_queue`, `self.event_bus`.

**If you hide a panel, hide it with `grid_remove()` / `pack_forget()` — do NOT
defer its creation.** A missing attribute raises on a background thread → C1.

`show_meeting_summary` (`main_window.py:8897`) is **already** a button command,
so putting the summary behind a button needs no new lifecycle.

### C3 — `_publish_final_transcript_segment` must keep `is_final = True`

`deepgram_client.py`, after `queue_item.update(metadata)`.

**This exact line cost 8 of 9 utterances in one live run.** `_display_transcript_item`
opens with `if item.get("is_final") is False: return` — a silent drop before any
canonical commit. `None` passes; `False` does not.

**Check:** the re-assert after the blanket `update(metadata)` still exists.

### C4 — `lines` must stay 1:1 with `record_ids` in `serialize_export_payload`

`canonical_transcript_ledger.py`. One **entry** per canonical record. An entry
may contain embedded newlines (grouped sentences, gap markers) but must never be
split into separate list items.

**Why:** every downstream coverage gate pairs those two lists **by index**.

### C5 — The gap marker must never become a canonical record

`RAW_EVENT_LINEAGE_REQUIRED = True` means every canonical record traces to a real
provider event. The marker is synthetic. It is recorded in `_connection_gaps`
beside the ledger and rendered at export.

**Check:** `ledger._records` gains nothing when `record_connection_gap` is called.

### C6 — `TranscriptStore` is the authoritative live history; the pane is a bounded view

`MAX_RENDERED_UI_SEGMENTS = 500`, `TRANSCRIPT_RENDER_DEBOUNCE_MS = 100`.

The pane renders only the last 500 segments. The store keeps everything, and
copy/export read the store, not the pane.

**Check:** a redesigned pane must still cap its render and must not become the
source of truth for copy/export.

### C7 — `_readable_parts` memoisation must key on TEXT, not identity

`transcript_store.py:294`. `update_last_segment_if_active` rewrites a segment's
text **in place**, so identity-keyed caching serves stale lines.

**Cost if broken:** render cost returns to O(session) — measured 62 ms at 320
segments against a `UI_QUEUE_TIME_BUDGET_MS` of 10.

### C8 — Every UI-visible transcript path must group English text

**Four** paths render transcript text. All four must group — checking three and
declaring C8 PASS is how this bug survived the first time:

| Path | File | Function |
|---|---|---|
| Live pane (committed) | `main_window.py:4473` | `_render_transcript_from_store_now` |
| Live pane (interim ⏳) | `main_window.py:1332` | `_interim_preview_lines` |
| Copy / export text | `transcript_store.py:283` | `get_clean_text` |
| `Alpha output.txt` | `canonical_transcript_ledger.py` | `serialize_export_payload` |

**This is the exact bug found on 2026-08-14:** the first path rendered raw text
while the others grouped, so text visibly reflowed the moment it committed.

### C9 — Japanese must never be regrouped by English rules

`_readable_parts` returns `[text]` unchanged unless `source_language`
starts with `"en"`. Japanese has its own boundaries from the assembler.

### C10 — Grouping must never lose a word

`text_is_preserved(original, parts)` is asserted everywhere grouping is applied,
and every path falls back to the raw text on any failure.

---

## 4. Function-by-function contract

**The line numbers below are `ac25308` positions and a UI redesign is precisely
what invalidates them.** They are navigation hints for the baseline worktree,
never identity. Locate every symbol by name:

```bash
grep -n "def _render_transcript_from_store_now" Alpha_Live_Translator/alpha/ui/main_window.py
```

If a function is **gone** rather than moved, that is a finding, not a stale
anchor — say which contract it carried and where the new code carries it.

### 4.1 `alpha/ui/main_window.py`

| Line | Function | Contract |
|---|---|---|
| 1317 | `_transcript_box` | Returns the transcript widget. If the redesign renames the widget, update here, not at call sites. |
| 1320 | `_speaker_tag` | Returns the Tk tag for a speaker. Tags must exist before use. |
| 1327 | `_refresh_transcript_scrollbar` | Called after inserts. |
| 1332 | `_interim_preview_lines` | Yields `(text, is_last)`. **Only the last line carries ⏳.** English only. Falls back to one line on ANY exception — it runs on the UI thread. |
| 1367 | `_remove_interim_line_from_display` | Deletes `interim_anchor` → `end`. **The anchor mark is what makes multi-line previews safe.** If the redesign changes how the preview is inserted, this must still remove all of it. |
| 1390 | `_ui_speaker_label_text` | The `Speaker: ` prefix. UI-only — never part of lexical scoring. |
| 1662 | `_update_interim_line_only` | Sets `interim_anchor`, inserts the grouped preview. |
| 3257 | `_clear_text_placeholder` | Clears the empty-state text. |
| 4460 | `_render_transcript_from_store` | Debounced scheduler (100 ms). Do not call the `_now` variant directly from hot paths. |
| 4473 | `_render_transcript_from_store_now` | Renders the last `MAX_RENDERED_UI_SEGMENTS`. Uses `transcript_store._readable_parts(seg)`. Refuses unbounded rewrite (`UI_FULL_REWRITE_BLOCKED`). |
| 7178 | `_show_translation_loading_item` | Pending row. Gated by `TRANSLATION_PENDING_PLACEHOLDER_VISIBLE` (**False**). **The Tk mark is gated with the text** — a mark with no row sits before the next appended line and its removal deletes a real translation. |
| 7243 | `_clear_translation_loading_item` | Deletes at the mark, appends the finished translation at `tk.END`. Tolerates a missing mark via `box.compare(...)` inside `except`. |
| 7697 | `_run_on_ui_thread` | The only sanctioned way for background code to touch Tk. |
| 7711 | `publish_transcript_event` | Puts on `transcript_queue` and publishes to `event_bus`. Never drops. |
| 8826 | `_insert_formatted_text` | Bulk insert into a text widget. |
| 8897 | `show_meeting_summary` | Already a button command. |

### 4.2 `alpha/summary/transcript_store.py`

| Function | Contract |
|---|---|
| `get_clean_text` | One line per readable part, `Speaker: ` prefixed. Source for copy/export. |
| `_readable_parts` | Memoised, **keyed on text**. English only. Falls back to `[text]`. |
| `_build_readable_parts` | `collapse_stutters` then `group_sentences_into_lines`, guarded by `text_is_preserved`. |
| `get_last_segment_if_active` | Fail-closed on speaker mismatch — never reaches past a speaker change. |
| `update_last_segment_if_active` | Rewrites the last segment **in place** (see C7). |

### 4.3 `alpha/utils/english_line_grouping.py`

| Symbol | Value / contract |
|---|---|
| `ENGLISH_LINE_MAX_SENTENCES` | `3` — always break here |
| `ENGLISH_LINE_MIN_SENTENCES` | `2` — never break before |
| `ENGLISH_LINE_TARGET_WORDS` | `20` — break at 2 sentences once reached |
| `_INITIAL_LETTER` | **Case-sensitive.** `re.IGNORECASE` would make `[A-Z]` match any letter. No trailing `\.?`. |
| `_ABBREVIATION` | Case-insensitive. |
| `collapse_stutters` | Readable copy only. Skips `_LEGITIMATE_DOUBLES` and **any token containing a digit**. |
| `text_is_preserved` | Word-sequence equality. |

**The user's own specification, which must keep reproducing exactly:**

```
Speaker: My name is Tariqul. I am from Bangladesh. I am a software developer.
Speaker: Currently I am working on Wicresoft Japan as a System Engineer. I have a dream to chase so I work so hard night and day.
Speaker: I live in Tokyo Japan right now. I use Bus and Train for come to office and it takes more than an hour to reach office.
```

Pinned by `test_reproduces_the_users_example`. **This test may never be edited
to match new behaviour.**

---

## 5. Feature flags at the UI/pipeline seam

| Constant | Value | Meaning if changed |
|---|---|---|
| `TRANSLATION_PENDING_PLACEHOLDER_VISIBLE` | `False` | `True` restores `Speaker: … ⏳` rows |
| `INTERIM_PREVIEW_LINE_GROUPING_ENABLED` | `True` | `False` = one long ⏳ paragraph |
| `ENGLISH_SENTENCE_FLUSH_ENABLED` | `True` | `False` = far fewer, longer records |
| `MAX_RENDERED_UI_SEGMENTS` | `500` | Pane render cap |
| `TRANSCRIPT_RENDER_DEBOUNCE_MS` | `100` | Render debounce |
| `UI_UPDATE_INTERVAL_MS` | `100` | Queue poll |
| `UI_MAX_UPDATES_PER_TICK` | `6` | Items drained per tick |
| `UI_QUEUE_TIME_BUDGET_MS` | `10` | Exceeding logs `UI_EVENT_DRAIN_TIME_BUDGET_EXCEEDED` |
| `INTERIM_UI_THROTTLE_MS` | `200` | Interim render throttle |
| `INTERIM_GHOST_TTL_MS` | `6000` | Orphan-preview watchdog |
| `TEMP_AUDIO_RETENTION_ENABLED` | `True` | **Both** must be True or cleanup is skipped |
| `TEMP_AUDIO_AUTO_DELETE_ENABLED` | `True` | ″ |
| `DG_WS_PING_INTERVAL_S` / `DG_WS_PING_TIMEOUT_S` | `10` / `5` | timeout MUST stay < interval |
| `DG_GAP_MARKER_MIN_S` | `2.0` | Minimum outage that gets a marker |
| `TRANSLATION_CONTEXT_LINES` | `3` | DeepL context |

---

## 6. Sprint regression tests (must all stay green)

| File | Tests | Guards |
|---|---|---|
| `test_english_line_grouping.py` | 34 | Item 65 grouping, stutter collapse, memoisation |
| `test_item66_resent_tail.py` | 21 | Cross-record duplicate tails |
| `test_english_line_quality.py` | 20 | Item 64 seam duplicates, flush |
| `test_item48_audio_manifest_bounded.py` | 17 | Manifest packet collapse |
| `test_item50_translation_context.py` | 17 | DeepL context + client compat |
| `test_items_68_69_live_ui.py` | 17 | Pending row, interim preview, **live pane grouping** |
| `test_item67_gap_marker_in_export.py` | 15 | Gap marker in export |
| `test_item44_commit_in_flight.py` | 13 | Reconnect in-flight commit |
| `test_reconnect_retries_and_translation_backoff.py` | 11 | Retry loop, DeepL retryability |
| `test_keepalive_ping_thread_cannot_crash.py` | 7 | Ping-thread crash guard |
| `test_committed_segment_is_final.py` | 7 | **C3** — the `is_final` clobber |

---

## 7. Automated verification

### 7.1 Run the suite

```bash
cd "C:/Users/islamm/Documents/Tariqul/Alpha_Translator V 1.0/Alpha_Live_Translator" && SKIP_TK_INTEGRATION_TESTS=1 "../.venv/Scripts/python.exe" -m unittest discover -s tests -p "test_*.py" 2>&1 | grep -E "^(FAIL|ERROR|Ran |FAILED|OK)" | sort -u
```

**Expected:** `Ran 674 tests` (or more if tests were added), `failures=5, errors=2, skipped=3`.

### 7.2 The 7 pre-existing failures — IGNORE THESE

```
ERROR: test_glossary_helper_absent            (test_package_glossary_flags_85253)
ERROR: test_glossary_helper_present           (test_package_glossary_flags_85253)
FAIL:  test_main_glossary_absent_no_unbound_local        (test_package_glossary_flags_85253)
FAIL:  test_main_glossary_present_after_successful_inclusion (test_package_glossary_flags_85253)
FAIL:  test_commit_allowed_while_finalizing   (test_final_transcript_commit_v3_2_5)
FAIL:  test_commit_allowed_while_listening    (test_final_transcript_commit_v3_2_5)
FAIL:  test_phase_constants_match_spec        (test_stop_finalize_v3_2_3)
```

**Any 8th distinct name is a regression caused by the UI change.**

### 7.3 Contract smoke test (paste and run)

```bash
cd "C:/Users/islamm/Documents/Tariqul/Alpha_Translator V 1.0/Alpha_Live_Translator" && "../.venv/Scripts/python.exe" - <<'PY'
import sys, inspect; sys.path.insert(0, ".")
from alpha import constants as C
from alpha.ui.main_window import AlphaApp
from alpha.summary.transcript_store import TranscriptStore
from alpha.transcription import deepgram_client as dc
from alpha.transcription import canonical_transcript_ledger as L

ok = True
def check(cid, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"  {cid}: {'PASS' if cond else 'FAIL'} {detail if not cond else ''}")

# C8 - the live pane must group committed segments
src = inspect.getsource(AlphaApp._render_transcript_from_store_now)
check("C8 live pane groups", "_readable_parts" in src)
# C6 - the pane render stays bounded
check("C6 render cap", "MAX_RENDERED_UI_SEGMENTS" in src)
# C3 - is_final re-assert after the blanket metadata update
pub = inspect.getsource(dc.DeepgramClientMixin._publish_final_transcript_segment)
u = pub.index("queue_item.update(metadata)")
check("C3 is_final re-asserted", pub.find('queue_item["is_final"] = True', u) != -1)
# C7 - memoisation keys on text
rp = inspect.getsource(TranscriptStore._readable_parts)
check("C7 cache keyed on text", "cached[0] == text" in rp)
# C5 - gap marker never becomes a record
L.reset_for_run("contract"); L.record_connection_gap(seconds=31.0, at=1.0)
check("C5 marker is not a record", len(L._records) == 0)
# C9 - Japanese is never regrouped by English rules
s = TranscriptStore(); s.add_segment(speaker=1, text="これは一です。これは二です。これは三です。", source_language="ja")
check("C9 japanese untouched", len([l for l in s.get_clean_text().splitlines() if l.strip()]) == 1)
# C10 - grouping loses no word, on the user's own pinned example
from alpha.utils.english_line_grouping import group_sentences_into_lines, text_is_preserved
EX = ("My name is Tariqul. I am from Bangladesh. I am a software developer. "
      "Currently I am working on Wicresoft Japan as a System Engineer. I have a dream "
      "to chase so I work so hard night and day. I live in Tokyo Japan right now. "
      "I use Bus and Train for come to office and it takes more than an hour to reach office.")
parts = group_sentences_into_lines(EX)
check("C10 no word lost", text_is_preserved(EX, parts))
check("C10 user example is 3 lines", len(parts) == 3, f"got {len(parts)}")
# C4 - one entry per canonical record, even with a gap marker embedded
L.reset_for_run("contract-c4")
for i, t in enumerate(("One.", "Two."), 1):
    L.apply_decision(speaker=1, assembler_text=t, final_text=t, requested_action="append",
                     applied_action="append", source_raw_event_ids=[f"raw-{i}"],
                     commit_reason="utterance_end",
                     metadata={"session_id": "s", "channel_index": 0,
                               "canonical_utterance_id": f"U{i}", "source_version": 1})
    if i == 1:
        L.record_connection_gap(seconds=31.0, at=1.0)
p = L.serialize_export_payload({"records": L._records})
check("C4 lines 1:1 record_ids", len(p["lines"]) == len(p["record_ids"]) == 2,
      f'{len(p["lines"])} lines vs {len(p["record_ids"])} record_ids')
check("C4 gap is embedded, not a line", "connection lost" in p["text"])
# flags
for name, want in [("TRANSLATION_PENDING_PLACEHOLDER_VISIBLE", False),
                   ("INTERIM_PREVIEW_LINE_GROUPING_ENABLED", True),
                   ("ENGLISH_SENTENCE_FLUSH_ENABLED", True),
                   ("MAX_RENDERED_UI_SEGMENTS", 500),
                   ("TEMP_AUDIO_RETENTION_ENABLED", True),
                   ("TEMP_AUDIO_AUTO_DELETE_ENABLED", True)]:
    got = getattr(C, name)
    check(f"flag {name}", got == want, f"expected {want}, got {got}")
print("\nRESULT:", "ALL CONTRACTS PASS" if ok else "*** CONTRACT FAILURE ***")
PY
```

### 7.4 Render performance (C7)

```bash
cd "C:/Users/islamm/Documents/Tariqul/Alpha_Translator V 1.0/Alpha_Live_Translator" && "../.venv/Scripts/python.exe" - <<'PY'
import sys, time; sys.path.insert(0, ".")
from alpha.summary.transcript_store import TranscriptStore
PARA = ("This is a sentence about something. And another sentence follows it here. "
        "A third one closes the group off. ") * 4
s = TranscriptStore()
for _ in range(320):
    s.add_segment(speaker=1, text=PARA, source_language="en")
s.get_clean_text()
t = time.perf_counter(); s.get_clean_text(); ms = (time.perf_counter() - t) * 1000
print(f"get_clean_text at 320 segments: {ms:.2f} ms   (baseline 1.4-3.3 ms; FAIL if > 25 ms)")
PY
```

---

## 8. Live test procedure (MANDATORY — automated checks cannot cover Tk)

Run **one** session, about 6 minutes:

1. Start the app, select **English**.
2. Play any speech (a YouTube video is fine).
3. At ~2 min: **turn WiFi off**.
4. Wait **35 seconds**.
5. **Turn WiFi on.**
6. Let it run 2 more minutes.
7. Speak/play a final sentence, then **stay silent for 10 seconds**.
8. Press **Stop**.

### While it runs, watch for

- Transcript pane shows **2–3 sentence lines**, not paragraphs.
- Exactly **one ⏳** at the bottom, on the last preview line only.
- Translation pane shows **no `Speaker: … ⏳` rows**.
- Text does **not visibly reflow** when a line commits.
- After WiFi returns, transcription **resumes**.

### Then analyse the run

```bash
cd "C:/Users/islamm/Documents/Tariqul/Alpha_Translator V 1.0/Alpha_Live_Translator/troubleshooting/runs" && "../../../.venv/Scripts/python.exe" - <<'PY'
import json, re, glob, os, sys
from pathlib import Path
# Every read below is guarded: a UI change that breaks the pipeline produces a
# run with MISSING files, and an analyser that crashes on those tells you
# nothing about the failure you are trying to diagnose.
runs = sorted(glob.glob("*/"), key=os.path.getmtime)
if not runs:
    sys.exit("no runs found - did the session write to troubleshooting/runs/ ?")
R = Path(runs[-1].rstrip("/"))
rd = lambda f, d="": (R / f).read_text(encoding="utf-8", errors="replace") if (R / f).exists() else d
m = json.loads(rd("RUN_MANIFEST.json", "{}") or "{}")
log = rd("logs/japanese_accuracy.log")
n = lambda f: len([l for l in rd(f).splitlines() if l.strip()])
print("run:", R.name, "| status:", m.get("final_status"), "| stop_failed:", m.get("stop_finalize_failed"))
for ev in ("CRASH_HOOK_TRIGGERED", "THREAD_EXCEPTION_CAPTURED",
           "COMMITTED_SEGMENT_DROPPED_AS_INTERIM", "UI_FULL_REWRITE_BLOCKED",
           "BACKGROUND_TK_CALL_BLOCKED", "DEEPGRAM_AUDIO_GAP_MARKED",
           "RESENT_TAIL_TRIMMED", "UI_EVENT_DRAIN_TIME_BUDGET_EXCEEDED"):
    print(f"  {ev}: {len(re.findall(ev, log))}")
pub, canon, exp = n("evidence_streams/provider_events.jsonl"), n("evidence_streams/canonical_commits.jsonl"), n("transcripts/final_export_records.jsonl")
print(f"  pipeline: {pub} publishes -> {canon} canonical -> {exp} exported")
recs = [json.loads(l) for l in rd("transcripts/final_export_records.jsonl").splitlines() if l.strip()]
if not recs:
    sys.exit("  *** NO EXPORTED RECORDS - the export path is broken. Stop and fix this. ***")
dup = 0
for i in range(len(recs) - 1):
    a, b = recs[i]["text"].split(), recs[i + 1]["text"].split()
    for k in range(min(10, len(a), len(b)), 2, -1):
        if [w.lower().strip(".,") for w in a[-k:]] == [w.lower().strip(".,") for w in b[:k]]:
            dup += 1; break
print(f"  cross-record duplications: {dup}   (MUST be 0)")
txt = rd("transcripts/Alpha output.txt")
lines = [l for l in txt.splitlines() if l.strip()]
w = sorted(len(l.split()) for l in lines)
med = w[len(w) // 2] if w else 0
print(f"  readable: {len(recs)} records -> {len(lines)} lines, median {med} words"
      + ("   *** EMPTY EXPORT ***" if not lines else ""))
print(f"  hourglass rows in export: {txt.count(chr(0x23F3))}   (MUST be 0)")
print(f"  gap marker in export: {'YES' if 'connection lost' in txt else 'NO  <-- FAIL if WiFi was dropped'}")
ts = json.loads(rd("translation/translation_summary.json", "{}") or "{}")
print(f"  translation: {ts.get('successful_translations')}/{ts.get('STABLE_TRANSLATION_JOBS_ACCEPTED')} ok, failed={ts.get('failed_translations')}")
man = R / "audio_temp/audio_manifest.json"
if man.exists():
    print(f"  audio_manifest.json: {man.stat().st_size/1024/1024:.3f} MB   (FAIL if > 2 MB for a ~6 min run)")
PY
```

---

## 9. Baseline numbers to compare against

Measured on the frozen commit. **The new UI must match or beat every row.**

| Metric | Baseline | Source run | Fail if |
|---|---|---|---|
| `final_status` | `completed` | all | anything else |
| `CRASH_HOOK_TRIGGERED` | **0** | all | > 0 |
| `THREAD_EXCEPTION_CAPTURED` | **0** | all | > 0 |
| `COMMITTED_SEGMENT_DROPPED_AS_INTERIM` | **0** | all | > 0 |
| publishes → canonical → exported | equal (±1 for a revision) | `155844` 22→22→22 | a drop > 1 |
| cross-record duplications | **0** | `155844`, `114309` | > 0 |
| `… ⏳` rows in export | **0** | `155844` | > 0 |
| records → readable lines | 22 → 77 · 160 → 430 | `155844`, `114309` | ≈1:1 (means grouping is off) |
| median words per line | **17–24** | `155844`, `114309` | > 40 |
| translations succeeded | **100%** | 22/22, 161/161 | any failure |
| `audio_manifest.json` (8 min) | **0.263 MB** | `155844` | > 2 MB |
| manifest entries | **354** for 71,181 packets | `155844` | > 5,000 |
| gap marker accuracy | 30.7 s for a ~32 s drop | `154956` | absent, or off by > 20% |
| memory after warm-up | +13.5 MB over 74 min | `114309` | steady growth |
| queue depths | **0** at all snapshots | `114309` | sustained > 0 |
| `get_clean_text` @320 segs | **1.4–3.3 ms** | §7.4 | > 25 ms |

Reference runs are in `Alpha_Live_Translator/troubleshooting/runs/`:
`v3.3.5.5.8.5.26.5.3-20260814-114309` (99 min), `-155844` (8 min),
`-154956` (network drop), `-165109` (11b-LT).

### 9.1 Log events that are NOT failures

Read these correctly or you will chase ghosts:

| Event | Meaning | Baseline |
|---|---|---|
| `BACKGROUND_TK_CALL_BLOCKED` | The C1 guard **working** — a background Tk call was caught and rerouted to the UI thread. Normal. | 20 in a 33 s run; 777 in a 25 min run |
| `UI_EVENT_DRAIN_TIME_BUDGET_EXCEEDED` | A drain tick exceeded 10 ms. A few per minute is normal. | 2 in 33 s; 24 in 8 min; 162 in 99 min |
| `DEEPGRAM_AUDIO_GAP_MARKED: 0` | Correct when **no** WiFi drop happened. Only a failure if you dropped the network. | 0 without a drop; 1 per outage |
| `RESENT_TAIL_TRIMMED: 0` | Correct when the provider re-sent nothing. Not a failure. | 0–37 depending on speech |
| `THREE_STAGE_FINALIZER_EXCEPTION` | Pre-existing, appears on the baseline too. | 2 per run |

**Genuine failures** are: `CRASH_HOOK_TRIGGERED`, `THREAD_EXCEPTION_CAPTURED`,
`COMMITTED_SEGMENT_DROPPED_AS_INTERIM`, `UI_FULL_REWRITE_BLOCKED`,
`IN_FLIGHT_COMMIT_ON_DISCONNECT_FAILED` — all of which are **0** on the baseline.

---

## 10. Required report format

```
BASELINE: ac25308
NEW UI:   <commit>

AUTOMATED
  Suite:            <N> tests, <F> failures, <E> errors
  New failing names: <none | list>
  Contract smoke:   <ALL CONTRACTS PASS | list of FAILs>
  Render @320:      <X> ms

CONTRACTS
  C1 background Tk .......... PASS/FAIL  <evidence>
  C2 eager widgets .......... PASS/FAIL
  C3 is_final re-assert ..... PASS/FAIL
  C4 lines 1:1 record_ids ... PASS/FAIL
  C5 marker not a record .... PASS/FAIL
  C6 bounded pane render .... PASS/FAIL
  C7 cache keyed on text .... PASS/FAIL
  C8 all 4 paths group ...... PASS/FAIL
  C9 japanese untouched ..... PASS/FAIL
  C10 no word lost .......... PASS/FAIL

LIVE RUN <run id>
  <paste §8 analyser output>

VERDICT: <SAFE TO SHIP | REGRESSIONS FOUND>
  <for each regression: what broke, which contract, the fix>
```

---

## 11. Traps this codebase has already sprung

Do not rediscover these the hard way.

1. **A silent `return` is the most expensive bug here.** `is_final is False`
   dropped 8 of 9 utterances with nothing logged.
2. **The store and the ledger can disagree.** A lifecycle fix looked correct in
   both the lifecycle and the store while the ledger kept truncated text. Always
   check `Alpha output.txt`, not just the pane.
3. **Tk marks have right gravity.** A mark left at `"end"` with no text under it
   sits *before* the next appended line, and deleting at it removes real content.
4. **`re.IGNORECASE` on `[A-Z]`** matches lowercase too.
5. **Changing two properties of a regex at once** hid a second bug inside the
   fix for the first.
6. **A test whose fixture accidentally satisfies an unstated condition** passes
   for the wrong reason until that condition starts mattering.
7. **An item's own title is a checklist.** Item 44 read "backoff, buffer, commit
   in-flight, mark the gap" — "commit in-flight" had never been written.
8. **Probes that rebuild the code they test prove nothing.** Three probes cleared
   the wrong layers because each hand-built the data structure instead of letting
   real metadata flow through.

---

## 12. Still open (not caused by a UI change)

| Item | What | Owner |
|---|---|---|
| 49 | Clean-machine install verification | needs a second PC |
| 70 | English text settles ~1 line/min; highest risk, do last | dev |
| 71 | This UI change | user's spec |
| 27b, 25 | Deferred with recorded reasons | post-delivery |

Authoritative plan: `CLIENT_DELIVERY_SPRINT_v5.md` §8 (item ledger) and §9
(append-only log). **Read v5, not `BUG_FIX_ROADMAP.md`.**
