# Pending tasks — Alpha Live Translator

Handoff for the next Claude Code session. Written 2026-09-25 at `3070dca`,
updated 2026-09-28 with the owner's two Japanese meetings (section 0) and
item 37 (`a83dbaa`), and 2026-09-29 with section 0's code done (items 38-43)
and the open defects (k)-(o) closed (items 44-48), and 2026-09-30 with items
49-54 from the owner's Japanese meeting of 2026-09-29 and items 55-61 (the
review of 26.5.52 and every open defect code can close), and 2026-10-01 with
the owner's first live meeting on 26.5.58 and items 62-70 from it, and
2026-10-02 with the second live meeting (26.5.65) and item 71 from it,
`APP_VERSION = "3.3.5.5.8.5.26.5.66"`. The owner writes Banglish, wants terse
answers, proof over plausible reads, and findings as a table with
issue / risk / severity / importance.

## Where things stand

* Every item through **71** in `CODE_REVIEW_20260904.md` is fixed and pushed,
  except item 53, withdrawn (measured worse; see there; item 57 fixed its real
  problem); number 66 is not used. `FIX_SEQUENCE.md` is the execution-order ledger; read both before
  any fix.
* **Item 71 (2026-10-02), the owner's second live Japanese meeting `...143752`
  on 26.5.65:** items 62-70 held live -- word latency p50 3.0 / p90 8.3 s,
  slowest 20.6 s (2 words over 20 s; 10 on 26.5.58), every line translated, no
  repeated line, nothing held as noise, the words at Stop kept, all 318 finals
  in the export. Open defect (v) was pinned live and fixed as item 71: the window
  no longer re-decides the assembler's lines by text (it overwrote 2 rows and hid
  2 lines; their English never showed). Replay on the fix: 0 mismatches.
* **Items 62-70 (2026-10-01), the owner's live Japanese meeting `...140533` on
  26.5.58:** healthy (0 errors, 0 reconnects, every line translated, the words at
  Stop kept) but one line grew for a minute, came 27 s and 56 s late, was
  exported three times, and 6 translations went unmatched. Causes, each proven
  on the real code: timers never armed or wiped (62, 63, 70), the noise
  quarantine (72 of 73 held fragments in 11 runs were speech; it delayed and
  reordered them -- off, 64), a revision looked up under the wrong channel (65),
  repeat cleanup eating Latin words and digits (67), merges after a long pause
  (69); and every run's boundary decisions held every run's since August (68).
  Replaying that meeting on 26.5.65: word latency p90 12.0 -> 9.8 s, slowest
  40.3 -> 21.3 s, words later than 20 s 11 -> 1, no repeated export line, no
  speech held as noise, the words in their spoken order; 4 translation
  mismatches left (defect v). Details in `CODE_REVIEW_20260904.md`.
* **Items 55-61 (2026-09-30):** a review of 26.5.52 found item 50 (one speaker)
  had removed an accidental gate: replies that repeat the line before were
  dropped in Japanese (55) and repeated words cut in English (56). Also fixed:
  a late pong no longer drops a socket that is still talking (57), the words in
  progress at Stop reach the export (58, 10 of 12 dropped texts were lost), the
  eight stale tests (59, the suite's failing set is now EMPTY), one run folder
  per Start (60), a buffer join ends its sentence (61). Each fails on 26.5.52.
* **Items 49-54 (2026-09-30), the owner's Japanese meeting `...140302`:** it ran
  26.5.34 (the main folder was 16 commits behind). Replaying its own recorded
  finals through the real app: cleanup no longer cuts words or numbers (ここ,
  二二七), no fake speaker changes without diarization, no 「。、」, the pending
  log pile no longer grows, and a revised line is never exported twice. Commit
  delay p50 8.6 -> 2.9 s, p90 24.3 -> 7.9 s, translation id mismatches 17 -> 0,
  lineage PASSED. Each item was then reviewed by an independent reviewer that
  had to reproduce every claim on the real code; that found item 53 wrong (a 9 s
  ping timeout found a dead socket after 35-90 s) and seven more defects on the
  same seams, all fixed with tests. Details and numbers in `CODE_REVIEW_20260904.md`.
* **Open defects (k)-(o) are closed** (items 44-47), plus item 48 found
  reviewing 0b/0e. Item 44 was proven through the real app with a local fake
  Deepgram: at `f1090d7` the sealed export repeats "he writes openly, I never
  considered"; at `1c16241` it does not.
* **Section 0's code is done** (items 38-43, 0b/0g/0d/0c/0e/0i). What is left of
  section 0 needs the owner: 0a (meeting setup), 0f (the names list), 0h (a
  reference transcript). See the plan table in section 0.
* **Item 35 was incomplete; item 37 finished it** (review of 2026-09-28, proven
  by driving the real code against `cb66936`): a re-opened line the provider
  went on extending lost its first words in the export AND the pane, and a
  correction or extend held open by `speech_final=False` still never reached
  the ledger. Item 35's ledger entries carry a visible correction.
* **Update package 26.5.58 built 2026-09-30** and applied to the owner's own
  installed app (`%LOCALAPPDATA%\Programs\Alpha Live Translator`, was 26.5.52,
  and 26.5.46 before that): 9 files, every one SHA-256 verified, `.env`,
  `user_settings.json` and `troubleshooting\` untouched, launches to the main
  window. Tested first on a synthetic copy of that install. Backups
  `app_backup_20260929-172652` (26.5.3), `app_backup_20260930-162550`
  (26.5.46), `app_backup_20260930-175247` (26.5.52): all can go once the owner
  has run a meeting. Not yet sent to any client. The last share build is still
  `build/share/*-1.4.*` (26.5.29); a new share build stays the owner's call.
* **graphify:** its post-commit hook skips worktrees by design, so the graph
  went 22 commits stale. A local `post-merge` copy of the hook (in the main
  folder's `.git/hooks`, not committed) now rebuilds it on every fast-forward.
* **Run Alpha from ONE place:** `Alpha_Translator V 1.0\Alpha_Live_Translator\main.py`
  (has `.env`) or the installed app. Never from `.claude\worktrees\...` (no
  keys). A push from a worktree does not update the main folder: fast-forward
  it (`git -C "<main folder>" pull --ff-only`) after every push.

### Pending work, in order

| # | What | Section | Kind |
|---|---|---|---|
| 1 | Owner: meeting audio setup (0a); names/terms list for keyterms (0f); a ~5 min reference transcript of `...140417` for the `multi` test (0h) | 0 | Owner |
| 2 | ~~One real Japanese meeting on 26.5.58~~ done 2026-10-01 (items 62-70); ~~on 26.5.65~~ done 2026-10-02 (62-70 held live; item 71). Next: one on 26.5.66 to see item 71 live | 1 | Test |
| 3 | One real English meeting to prove items 35, 37, 44 and 56 live, checked against the audio, not only the pane | 1 | Test |
| 4 | 0f and 0h once the owner's inputs arrive | 0 | Config / evaluate |
| 5 | Final build 1.5 / update package 26.5.58+, owner's call | 2 | Build |
| 6 | Deliver | 3 | Owner |
| 7 | Open defects left (section 4): each needs the owner, live data or a product choice | 4 | Owner / evidence |
* Deepgram: the owner's original account was deactivated ("Deactivated token")
  and blocked. `Alpha_Live_Translator/.env` now holds a working key from
  another account, verified live on 2026-09-25 (English and Japanese sockets
  open, Results returned). That key was pasted into a chat, so the owner will
  rotate it. Never print, log or commit a key; `.env` is gitignored.

| Commit | Package | What |
|---|---|---|
| `7affd62` | 26.5.31 | Item 33: meeting language locked while a session runs |
| `cb66936` | 26.5.32 | Item 34: keyterm 400 at Start retries once without keyterms |
| `f4784d8` | 26.5.33 | Item 35: one utterance = one line; English revisions reach the ledger |
| `3070dca` | 26.5.34 | Item 36: `numerals` no longer sent ("third quarter" was "3rd 0.25") |
| `a83dbaa` | 26.5.35 | Item 37: a re-opened line keeps its words; a held correction/extend reaches the ledger |
| `add7c11` | 26.5.36 | Item 38 (0b): a held Japanese line leaves on a 4 s timer, in order, with its own lineage |
| `b6b1890` | 26.5.37 | Item 39 (0g): held speech stays on screen as the grey pending line |
| `86b0209` | 26.5.38 | Item 40 (0d): an assembler line keeps its own row, id and translation |
| `6a2bd2a` | 26.5.39 | Item 41 (0c): "● Meeting audio silent" / "● Audio device switched", naming the device |
| `d8d17f6` | 26.5.40 | Item 42 (0e): a finished sentence on a `speech_final` final commits at once |
| `2b55484` | 26.5.41 | Item 43 (0i): export lineage matched by content, not position |
| `1c16241` | 26.5.42 | Item 44 (k): a line item 66 trimmed stays trimmed in every later version (re-open, correction, extend); an older guess no longer drops its words |
| `22786b5` | 26.5.43 | Item 45 (l): the accuracy experiment drops `numerals`; the validator refuses it |
| `437259f` | 26.5.44 | Item 46 (m, n): the device follow logged at INFO; item 73's sentence says Alpha is switching, translated |
| `45bb457` | 26.5.45 | Item 47 (o): lineage finds a commit past more than 6 other lines, guarded |
| `1bae6d1` | 26.5.46 | Item 48: 0e reads the last fragment's own `speech_final`; 0b's release tested on the real worker thread |
| `9e69529` | 26.5.47 | Item 49: Japanese cleanup keeps words, numbers and a sentence the next one echoes |
| `9d8ec93` | 26.5.48 | Item 50: no diarization means one speaker (pauses were speaker changes) |
| `d34acd2` | 26.5.49 | Item 51: no 「。、」/「。。」 at a join; the line before is never rewritten |
| `4938d87` | 26.5.50 | Item 52: the pending log pile stops rotating its own backups |
| `034b31a` | 26.5.51 | Item 53 withdrawn: ping timeout stays 5 s (9 s found a dead socket after 35-90 s) |
| `1975665` | 26.5.52 | Item 54: a held revision stays a revision on every way out; a 「。…」 final joins the newest line |
| `525562b` | 26.5.53 | Item 55: a reply that repeats the line before is kept (only a repeat within 1 s is a re-send) |
| `de1fde4` | 26.5.54 | Item 56: English words said again after a pause are not cut (only overlapping audio is a re-send) |
| `3a1c3f8` | 26.5.55 | Item 57: a message from Deepgram is proof of life (late pong no longer drops a talking socket) |
| `ee87ea4` | 26.5.56 | Item 58: the words in progress at Stop reach the export |
| `cd9c0ab` | (tests) | Item 59: the eight stale tests fixed or removed; the suite passes clean |
| `d30e3fa` | 26.5.57 | Item 60: one Start, one run folder |
| `7d32469` | 26.5.58 | Item 61: a buffer join ends its sentence; unused speaker state removed |
| `3c81d63` | 26.5.59 | Items 62-63: held text leaves on its timers (past 8 s; the stable layer's tail timer) |
| `601be66` | 26.5.60 | Item 64: noise quarantine off; punctuation alone is never a line |
| `402b485` | 26.5.61 | Item 65: a revision finds its line (the run-on line exported 3 times) |
| `0fb31bc` | 26.5.62 | Item 67: repeat cleanup keeps Latin words and numbers |
| `179afaa` | 26.5.63 | Item 68: a run's boundary decisions are its own |
| `a6bface` | 26.5.64 | Item 69: speech after a pause over 4 s starts a new line |
| `512d8e4` | 26.5.65 | Item 70: the rest of a buffer a timer split keeps its timer |
| `1a98337` | 26.5.66 | Item 71: the window keeps every assembler line as its own row (open defect v) |

---

## 0. The owner's Japanese meetings of 2026-09-28 — do these first

Runs, all at 26.5.34, in `Alpha_Live_Translator/troubleshooting/runs/`:

| Run | Time | Owner's report | Captured |
|---|---|---|---|
| `v3.3.5.5.8.5.26.5.34-20260928-100031` | 10:00–10:14 | "stopped mid-way, I restarted"; slow translation; poor accuracy | meeting audio until 10:11, mic until 10:07 |
| `v3.3.5.5.8.5.26.5.34-20260928-101440` | 10:14–10:29 | (the restart) | laptop mic only; meeting audio never |
| `v3.3.5.5.8.5.26.5.34-20260928-140417` | 14:04–14:40 | "seemed to disconnect mid-way; did not capture 100%" | meeting audio all 36 min; mic off by choice |

In none of them did Alpha crash or Deepgram disconnect. Measured causes:

### A. Audio routing (morning only)

Per-minute speech in the retained `audio_temp/*_audio/*.wav` (0.5 s windows,
rms > 300): in `...100031` the system (meeting) track is exact digital zero
after 10:11:41, the mic exact zero from 10:07
(`MICROPHONE_CAPTURE_DISABLED_MID_SESSION` — switched off). At 10:11:26 the
Windows default output moved from "soundcore R50i NC" (Bluetooth) to "Realtek";
Alpha followed the default (rebind OK, 0.26 s) but the meeting app was not
playing there. In `...101440` the Realtek loopback was exact zero for all 15
minutes — the meeting played on another device (probably the "Jabra SPEAK 410"
captured at 10:00:41). The afternoon run had continuous meeting audio.

### B. The screen goes quiet for 30+ s while people talk (all runs)

Lines are held by the Japanese boundary logic, and the in-progress (grey)
interim line is wiped meanwhile, so the window looks disconnected.

* Last Deepgram final of a line → commit: p50 3.2–3.3 s (by design:
  `SENTENCE_HOLD_MIN_MS/MAX_MS` = 2000/3500 in `japanese_sentence_assembler.py`),
  p90 5.7–7.6 s, max 20–24 s; first final → commit up to 41 s.
* **Bug:** `JapaneseBoundaryStabilizer.process()` emits a timed-out pending line
  (`BOUNDARY_STABILIZER_HOLD_MS_MAX = 4000`) only when the NEXT final arrives;
  `flush_pending()` runs only at Stop. `...100031`: line in at 10:09:21.789, 8 s
  max-hold timer fired at 10:09:29.801 with no emit, emitted at 10:09:41.768
  (`INCOMPLETE_ENDING_TIMEOUT_EMITTED`, reason `pending_timeout_emit`) when the
  next speaker's final arrived. 27 such lines in `...100031`, 69 in `...140417`.
* `INTERIM_GHOST_LINE_CLEARED_BY_WATCHDOG` wiped the interim during those holds
  (23 times in `...140417`). `...140417` at 14:09:40–14:10:14: commit, interim
  wiped at 14:09:49, final ending in が held, assembler released it at
  14:10:02.8 (`SAFE_HOLD_TIMEOUT_COMMIT`, 8.1 s), the stabilizer held it again,
  interim wiped at 14:10:01.9, line shown at 14:10:14.4 only when the next final
  arrived (`incomplete_previous`). Four such 30–34 s silent stretches in
  `...140417`, each with 12–25 s of speech.

### C. What was not captured or not translated

* **English speech in a Japanese session is dropped.** `...140417`
  14:37:41–14:38:05: 18.5 s of speech, Deepgram (`ja`) returned only
  "ブルンテクト、" / "はいいですか。". The same audio cut out and re-sent:
  `ja` → "僕、六ディスプレイ、ディスプレイ / はいいですか。 / ブルーテクト化可能";
  `en` → "I didn't know… I didn't understand it / whether mock text will be
  fine. / Is mock display fine? / Okay. / No. So display display / Blue text".
* **8 of 225 lines (3.6%) got no translation during the meeting.** In
  `...140417` they were committed 14:08–14:38 and queued for translation only at
  Stop, 14:39:58 (stop reconciliation). 15 `TRANSLATION_STORE_ID_MATCH_NOT_FOUND`
  in that run. Commit paths of the eight: `hold_timeout_sentence_end_punc` (×4),
  `safe_chunk_boundary_commit` (×2), `continuity_emergency`, `hold_timeout_safe_prefix`;
  six had `translation_ready: True`. The morning run `...101440` had three
  (segments 81–83: 387 s, 327 s, 51 s after commit).
* Duplicate lines: 2 of 204 in `...140417` (a line repeated as the head of the
  next).
* Accuracy is not a confidence problem: Deepgram median confidence 0.98–1.00,
  6–8% under 0.7; dropped low-confidence finals were 26 and 20 characters in
  the two morning runs.
  The rest: code-switching by non-native speakers
  ("Javaのノーエクスプレイエクスペリエンス"), names missing from keyterms
  (シャフィー / シャーピー / シャンピー), and in `...101440` the far-field laptop
  mic.

### D. `language=multi`, measured on retained audio

| Audio | Result with `multi` |
|---|---|
| `...101440` mic chunk 0020 (far-field room) | worse: Hindi and Korean script, "Daigalguru" ×3 |
| `...100031` system chunk 0005 | worse at the start: "Sembra latte. Vabbè è camera ogni." |
| `...140417` English stretch 14:37:40–14:38:07 | good: the English is captured |
| `...140417` system chunk 0012 (JA/EN mix) | mixed: English terms better ("this is the crmかな"), some Japanese worse ("ウカラも会うと") |

Not a safe switch. Using it would also need per-segment translation direction
(an English line must not go to DeepL as Japanese).

### E. False alarm in the evidence

`...140417` reports `LINEAGE_EXPORT_COVERAGE_FAILED` / `valid_segment_loss` for
16 commits (`stable-213`…`stable-228`); all 16 are in `Alpha_output_FINAL.txt`
verbatim, and the record-level `export_coverage_report.json` shows 204/204. The
lineage matcher is wrong, not the export.

### Plan, in order — status 2026-09-29

| # | Task | Kind | Status |
|---|---|---|---|
| 0a | Meeting setup: meeting app speaker = Windows default (or set the default to the meeting device before Start); no output-device switching mid-meeting; keep the mic on when the room speaks; prefer the speakerphone as both meeting output and room mic | Owner, no code | **OWNER.** Alpha now says when this goes wrong (item 41): "● Meeting audio silent" names the device it records, "● Audio device switched" names the new one |
| 0b | Release a timed-out stabilizer pending line on a timer | Code, HIGH | **DONE, item 38 (`add7c11`).** Replay of the three meetings' recorded stabilizer inputs: every held line out at 4.0 s (was p50 8 s, max 47-163 s). Also fixed: a newer line shown before an older held one (4/4/9), a new speaker's sentence held, a held line published with the wrong lineage |
| 0g | While a line is held, do not wipe the interim; show the held text as pending | Code, HIGH | **DONE, item 39 (`b6b1890`).** 26 of 35 wipes in the three meetings fell during a hold (blank median 5-19 s, up to 38 s); the held text now stays as the grey line |
| 0d | Lines translated at commit, not at Stop | Code, HIGH | **DONE, item 40 (`86b0209`).** Cause was not the commit paths: the UI re-merged an assembler line into the previous row under a session-wide `jpm-utt` id, so its translation missed the row (15 `TRANSLATION_STORE_ID_MATCH_NOT_FOUND`, one id) and the pane disagreed with the export |
| 0c | Warning when the meeting track is silent while the mic hears the room, naming the device; notice when Alpha follows a device change | Code, HIGH | **DONE, item 41 (`6a2bd2a`).** Replayed on the morning's per-stream audio timeline: `...101440` warned at 10:16:10 naming Realtek (was green all 15 min); `...100031` announced the switch to Realtek at 10:11:26, then item 31's "No sound" at 10:12:21 (mic was off) |
| 0h | English inside Japanese meetings: score `ja` vs `multi` on a reference transcript | Evaluate, then code | **WAITING FOR THE OWNER:** a hand-written transcript of ~5 minutes of `...140417` including 14:37-14:38. The retained WAVs expire after 2 h -- keep a copy of that run's `audio_temp/` (or record a new meeting) before scoring |
| 0e | Shorter sentence hold for lines ending in 。/？ with `speech_final` | Tuning, MEDIUM | **DONE, item 42 (`d8d17f6`).** 85 such lines waited p50 3.0 s; 3 (3.5%) were extended in the wait and now split instead. Lines without `speech_final`, or with a real ので/けど ending, keep the hold |
| 0f | Participant names and project terms as keyterms (`alpha/resources/keyterms/user_terms.json`) | Config + test | **WAITING FOR THE OWNER:** the correct spellings of the names heard as シャフィー / シャーピー / シャンピー and the other participants, plus project terms. Then A/B on retained audio (needs a kept copy, see 0h) |
| 0i | Lineage coverage matcher | Code, LOW | **DONE, item 43 (`2b55484`).** Matched by content: no false loss on the three runs, a real loss named as its own commit, and the lineage lock can no longer drop exported lines past the end of the chain |

---

## 1. Before the final build: one real Japanese meeting and one real English meeting — HIGH

### Japanese (items 38-43, 46-52 and 54, 26.5.52)

Proven on the real code and on the three retained meetings' own recorded
inputs, not yet live. In a 10-15 minute Japanese meeting with pauses and two or
more speakers, then in the newest run folder:

* The window never goes blank while people talk: `INTERIM_KEPT_WHILE_PIPELINE_HOLDS`
  appears where `INTERIM_GHOST_LINE_CLEARED_BY_WATCHDOG` used to; the latter
  only when nothing was held.
* `accuracy/boundary_stabilizer_decisions.jsonl` (this run's rows): no held line
  waits more than ~4.5 s (`BOUNDARY_STABILIZER_PENDING_RELEASED released_by=timer`
  in `logs/japanese_accuracy.log`), and no newer line is emitted while an older
  one is still held.
* `translation/translation_events.jsonl`: no segment queued more than a few
  seconds after its commit; zero `TRANSLATION_STORE_ID_MATCH_NOT_FOUND`.
* The pane and `transcripts/Alpha_output_FINAL.txt` have the same lines.
* `LINEAGE_EXPORT_COVERAGE_PASSED`, not `..._FAILED`.
* Unplug or switch the Windows output once mid-meeting: "● Audio device
  switched" names the new device; if the meeting then plays elsewhere,
  "● Meeting audio silent" names the device Alpha records within ~45 s.
  The switch is an INFO line in the log (`[connection] Windows changed…`,
  item 46), not an ERROR; during the switch a click on "● Audio device changed"
  says Alpha is switching, in the display language.
* Items 49-54, in `transcripts/Alpha_output_FINAL.txt`: no 「。、」/「。。」; no
  line that repeats the one before it; ここ / numbers (二二七, スリーセブン…) as
  said; a sentence is not split at a pause (without diarization everything is
  "Speaker 1" now -- item 50); `logs/japanese_accuracy.log` has no
  `SPEAKER_CHANGE_HARD_BOUNDARY`. Check the run was 26.5.52 first
  (`RUN_MANIFEST.json` / the run folder name): the meeting of 2026-09-29 ran
  stale code from a main folder 16 commits behind.

### English (items 35, 37 and 44)

Items 35 and 37 changed the commit pipeline in a way production has never
exercised: **before them, no English revision ever reached the canonical
ledger** (two gates dropped them), so the export kept an early guess while the
pane showed the correction. English revisions are now written as ledger
`revise` transactions. Item 35 is proven by replaying the owner's recorded
messages through the real app (see section 6), item 37 by driving the real
lifecycle, publisher, duplicate protection, registry and ledger -- neither yet
by a live meeting.

**Why the pane is not enough:** item 37's first defect removed the same words
from the pane AND the export ("I will send you" / "both today" became "both
today" in both), so an export-versus-pane comparison passed while speech was
lost. Compare against what was said.

Run one real English meeting of 5–10 minutes with two or more people, Stop, then
check the newest `Alpha_Live_Translator/troubleshooting/runs/<run>/`:

* `transcripts/Alpha_output_FINAL.txt` matches what the pane showed, line for line.
* `logs/japanese_accuracy.log`: every `LIFECYCLE_REVISION_PAST_REPLAY` is
  followed by a `PIPELINE_COMMIT_TRANSACTION_STARTED` with
  `"requested_action": "revise"`; no `STABLE_COMMIT_BEFORE_TRANSLATION_REJECTED`.
* `accuracy/visible_error_audit.txt`: `duplicate_line_continuation` hits should
  be rare now. Each remaining one: compare audio start times in
  `accuracy_stage_compare/raw_provider_events.jsonl` (`metadata.start_time`).
  Same start means a real duplicate; different start and speaker means two people
  (the audit does not look at speaker or timing — see 4g).
* The translation pane has one translation per transcript line.
* Listen to the retained audio (`audio_temp/*_audio/*.wav`) for at least 3
  minutes and check the export word for word: every spoken sentence is in it,
  none cut at the front. A Windows TTS script played into the meeting gives an
  exact reference.
* `logs/japanese_accuracy.log`: for every `RESENT_TAIL_TRIMMED`, the
  `removed_preview` words are in the export line just before the trimmed one
  -- the trim may only remove words already exported (item 37's first defect
  removed words that ended up in no line). That includes the ones with
  `reason=new_version_repeats_head_trimmed_at_creation` (item 44: a re-opened
  or corrected line cut the same way as when it was created). The lifecycle's
  own event log is not written in production (`set_event_log_path` has no
  caller), so this log is the record.

Acceptance: every spoken sentence is in the export once, no line in the export
that the pane does not show, and no line exported twice.

## 2. Final build — owner's call

From `Alpha_Live_Translator/`:

```
py tools/build_update_package.py --zip
py installer/build_installer.py --no-keys --rebuild --version 1.5 --output build/share
py installer/build_installer.py --no-keys --rebuild --version 1.5 --output build/share --portable
```

`--rebuild` is mandatory; without it the installer ships stale code. Move
`build/share/*-1.4.*` into `build/share/old-1.4/` first, as 1.2 and 1.3 were.
`installer/DELIVERY.md` documents the same commands (1.2 was built this way).

Verify the update package on a synthetic install of the previous package:

1. Copy `build/update/AlphaLiveTranslator_Update_3.3.5.5.8.5.26.5.30/app` to a
   temp folder `X/app`; create `X/python/pythonw.exe` (any stub file — the
   updater only checks it exists).
2. Put `X/app/.env`, `X/app/.needs-api-keys` and `X/app/user_settings.json` there.
3. From the new package folder: `py apply_update.py "X" --force`.
4. Expect "UPDATE COMPLETE -- every file verified by SHA-256", the three files
   listed under "kept untouched", and `X/app/alpha/constants.py` at 26.5.35 (or
   whatever the package was built at).

Also still owed: **`Setup-*.exe` has never been silently installed and
checked** (only the portable zip was unpacked and inspected). Do that once for
1.5.

## 3. Deliver

* Field user: send the new update package (26.5.52 or later — 26.5.33 and
  26.5.34 carry item 37's defects, 26.5.35-26.5.41 item 44's repeated
  half-sentence, everything before 26.5.47 item 49's word-cutting cleanup and
  item 50's fake speakers).
* **Never apply an update package of 26.5.26 or older to a keyless install** —
  those delete its `.needs-api-keys` marker.
* New recipients: `build/share/AlphaLiveTranslator-Setup-1.5.exe` or the
  portable zip. Both are keyless: the first Start asks for keys.

---

## 4. Open defects

| # | Issue | Risk | Severity | Importance |
|---|---|---|---|---|
| a | Sentence flush commits text Deepgram later revises. Owner's run: U-13 "Let's see. What is the task? If I can, I can?" then U-14 "If I can't, actually, ..." at the same start 55.39 | Stale tail sentence in the export | Medium | Medium |
| b | English revisions reaching the ledger are new in production (items 35, 37) | Untested live; section 1 | Medium | High |
| c | Japanese: one live probe returned 「十二パーセント」 as 「12」, dropping パーセント; identical with and without `numerals` | Wrong or missing unit in Japanese numbers | Unknown | Medium — measure on real speech first |
| d | English `smart_format`: "two point five million dollars" came back "US2.5 million dollars" | Odd currency text | Low | Low |
| e | `installer/keys.local.ini` still holds a different, probably deactivated, Deepgram key | A KEYED build would ship a dead key; keyless builds unaffected | Low | Medium before any keyed build |
| f | `Ctrl+L` toggles listening app-wide (`bind_all`) while Alpha has focus; a browser habit (Ctrl+L = address bar) can start or stop a meeting | Accidental Start/Stop | Low | Medium |
| g | The app's visible-error audit flags `duplicate_line_continuation` on two people ("Good afternoon." / "Good afternoon. How are you?", different speaker, 3.7 s apart) | False alarm in log analysis | Low | Low |
| h | Every transcript line reads "Speaker:" with no number under a "Speaker 2 · time" header | Looks unfinished; not checked whether new | Low | Low |
| ~~i~~ | ~~Each Japanese Start leaves an empty skeleton run folder next to the real one (e.g. `...-161849` + `...-161850`)~~ **FIXED, item 60** (one run folder per Start; the session runtime was bound to the empty one) | -- | -- | -- |
| ~~j~~ | ~~Eight stale failing tests in the baseline (section 7)~~ **FIXED, item 59** (the suite's failing set is empty) | -- | -- | -- |
| ~~k~~ | ~~Re-opening a line that item 66 trimmed at creation brings the trimmed head back~~ **FIXED, item 44** -- and five more paths to the same repeat, plus an older shorter guess that dropped words (see `CODE_REVIEW_20260904.md` item 44) | -- | -- | -- |
| ~~l~~ | ~~`run_english_accuracy_experiment.py` still sends `numerals=true`; the allowlist still accepts it~~ **FIXED, item 45** | -- | -- | -- |
| ~~m~~ | ~~"● Audio device switched" logged at ERROR~~ **FIXED, item 46** (INFO now) | -- | -- | -- |
| ~~n~~ | ~~Item 73's "cannot follow the change" text~~ **FIXED, item 46** (says Alpha is switching; translated) | -- | -- | -- |
| ~~o~~ | ~~Lineage matcher's 6-line window~~ **FIXED, item 47** (identical on all 84 retained runs) | -- | -- | -- |
| ~~p~~ | ~~A Japanese line the boundary stabilizer still holds at Stop is dropped if under 8 Japanese characters (`BOUNDARY_STABILIZER_STOP_FLUSH_DROPPED`, e.g. 「メインで」); by design, found by the item 50 review~~ **FIXED, item 58** (and the sentence in progress at Stop, which `SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA` dropped: 10 of 12 dropped texts were lost) | -- | -- | -- |
| q | Lines committed by `hold_timeout_safe_prefix` / `safe_chunk_boundary_commit` still wait 11-14 s (5 lines in the meeting replay, one 47 s). 2026-10-01: the live meeting gave the data -- timers never armed or wiped (items 62, 63, 70) and the quarantine (64); slowest word 40.3 -> 21.3 s. What is left is (x) | Some lines still late | Medium | Medium — see (x) |
| ~~r~~ | ~~A Deepgram pong more than 5 s late drops a live socket (once in the 2026-09-29 meeting, 2.2 s of audio). A longer timeout is NOT the fix (item 53, withdrawn): count a received data frame as proof of life instead~~ **FIXED, item 57** (a message is proof of life; dead sockets found as fast as before) | -- | -- | -- |
| s | Separated list items where the second extends the first collapse: 「日本、日本語」 -> 「日本語」 (the same rule as the restart 「Java、Javaの」, which it cannot tell apart); no retained final has one | A word dropped in a list | Low | Low |
| t | The in-app lineage report reads `stable_line_revision`'s active lines while the export comes from the frozen ledger, so it can report a loss the export does not have. 2026-10-01: FAILED live (8 "lost") and in both replays (8, 11); every one was in the export | False alarm in evidence | Low | Medium now -- it fails on every run of that meeting |
| ~~u~~ | ~~`last_speech_time` / `fallback_speaker` are still written (deepgram_client, main_window) though nothing reads them since item 50~~ **FIXED, item 61** | -- | -- | -- |
| ~~v~~ | ~~4 `TRANSLATION_STORE_ID_MATCH_NOT_FOUND` per meeting: a new line whose text contains or ends the line before~~ **FIXED, item 71** (the window re-decided the assembler's lines by text; the hypothesis was right, the 2026-10-01 test host had not registered the ids) | -- | -- | -- |
| w | Deepgram's 「。」 is kept before a particle when two finals join: 「これ。は石油腫と」 | Looks wrong | Low | Low -- 「分かりました。で…」 is correct, text alone cannot tell |
| x | The three holds stack on a sentence that never ends: assembler 8-12 s, stable layer 2-3.5 s, boundary stabilizer 4 s (items 62-63, 70 made each fire) | An unfinished sentence shows 14-20 s late | Medium | Medium -- a design choice; decide on live data |
| z | The Japanese display path drops a `retry_pending` verdict (`AlphaApp._display_transcript_item` calls `_commit_transcript_item_to_store` and returns None), so the item is never re-queued; found by item 71's review | Latent: no current Japanese producer reaches it | Low | Low |
| aa | 「…、」 then 「。…」 more than 8 s later becomes two ledger lines: the stabilizer's merge rewrites the committed 「、」 to 「。」, the revise is blocked as destructive and committed as a new line holding the old one (item 71's review) | The export (and now the window) repeats the line | Low | Medium -- fix in the stabilizer, as item 51 did for joins |
| y | `stable_line_revision._run_folder` has item 68's broken import, so its live writes are skipped (the files are written at Stop) | No clean transcript on disk if Alpha crashes mid-meeting | Low | Low |

Notes:

* **(a)** was excluded from item 35 on purpose and is pinned by
  `test_a_committed_flush_tail_is_never_reopened_with_its_head`: a flush tail
  legitimately shares its window's start, so re-opening on "same start" cannot
  tell the rest of the window from a revision. A fix needs a different signal —
  for example re-comparing the flushed head against the provider's next
  cumulative window while `_split_committed_prefix` is still set.
* **(k)-(o)** closed 2026-09-29 as items 44-47; item 48 closed two more found
  reviewing 0b/0e. Each was reproduced on the real code first and proven with a
  test that fails before the change; see `CODE_REVIEW_20260904.md`.
* **(c)**: the probe script used Windows TTS (Haruka). The owner's earlier
  Japanese TTS run through Alpha kept 「十二パーセント」, so this may be Deepgram
  variance. Collect evidence before touching anything.
* **2026-09-30, "fix all pending issues":** (i), (j), (p), (r), (u) closed as
  items 57-61. The rest were reviewed and left, each for a reason that is not
  code: (a) needs a new signal, (b) a live English meeting, (c) evidence, (d)
  Deepgram's own formatting, (e) the owner's key file, (f) a product choice, (h)
  cosmetic, (q) live timing data, (s) 「日本、日本語」 and 「Java、Javaの」 cannot
  be told apart by text; (g) the audit has no timing to tell two people from a
  re-send, and (t) has no failing case since 26.5.46 (every replay passes
  lineage). See `CODE_REVIEW_20260904.md`, items 55-61.

## 5. Deliberately NOT changed — do not "fix" without new evidence

* **Empty session shows `final_status: failed`.** REPAIR_PLAN Phase 4's gate in
  `alpha/utils/canonical_finalize.py`: an empty reconstruction must never read as
  completed, because from the ledger alone it cannot be told apart from total
  loss. No UI reads it; the operator sees "Stopped". Only worth changing
  together with a real "nobody spoke" signal (item 31 has one live).
* **Stop takes ~5 s.** Deepgram's graceful close waits ~3 s for final results
  (that keeps the last words), and `stop_core_completed_event` is set only after
  the run's evidence files are written, so the 5 s watchdog restores the window.
  Moving the signal touches the stop sequence that keeps a second Start from
  repointing writers while the old worker still writes.
* **Mid-meeting language change is locked (item 33), not made to work.**
  Deepgram is told the language only when the socket opens.
* **The keyless-build + stale machine variable case** is covered by item 32 plus
  the rejected-key dialog. When measuring `should_prompt`, do NOT set
  `ALPHA_NO_KEY_PROMPT=1` — it makes the gate answer False and fakes a defect.

## 6. How to verify transcription changes: replay through the real app

Unit tests were not enough for item 35: the lifecycle half passed its tests and
made the export worse. What found it was running the real app against a local
stand-in for Deepgram that replays recorded messages:

1. Start a `websockets` server (installed, version 16) on `127.0.0.1:0`. On
   connection, wait for the first audio frame, then send scripted JSON messages
   with delays; answer `{"type":"CloseStream"}` with a `Metadata` message.
2. Before importing the app:
   `DeepgramClientMixin._build_deepgram_url = lambda self: f"ws://127.0.0.1:{port}/v1/listen"`
3. Run the real `main.py` with `runpy.run_path(..., run_name="__main__")`, wrapping
   `AlphaApp.mainloop` to start a driver thread. The driver sets
   `source_language`, calls `toggle_listening` (Start), waits for
   `is_listening`, waits for the script, calls `toggle_listening` (Stop), waits
   for the button to read exactly "Start Listening", then reads
   `troubleshooting/runs/<newest>/transcripts/Alpha_output_FINAL.txt`.
4. UI calls from the driver go through `app._run_on_ui_thread(fn)`. Replace
   `tkinter.messagebox.show*/ask*` with recorders so a dialog cannot hang the run.
5. In a worktree there is no `.env`: set `DEEPGRAM_API_KEY` to any non-placeholder
   value (the stand-in ignores it), `DEEPL_AUTH_KEY=""` (the strip then reads
   "● No translation") and `ALPHA_NO_KEY_PROMPT=1`, before the app imports.
6. To compare with the code before a change, extract
   `git archive HEAD Alpha_Live_Translator` (~8 MB) outside the repo and run the
   same script against that copy. Done this way for item 44 on 2026-09-29:
   English and Japanese scripts, Stop 3.2 s, no dialog, lineage passed on both.

Message shapes the app parses:

```json
{"type": "Results", "channel_index": [0, 1], "start": 13.28, "duration": 0.8,
 "is_final": false, "speech_final": false,
 "channel": {"alternatives": [{"transcript": "I will send you a", "confidence": 0.9,
   "words": [{"word": "i", "punctuated_word": "I", "start": 13.28, "end": 13.44, "confidence": 0.9}]}]}}
{"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 14.08}
```

The lifecycle takes an utterance's start from `words[0].start`. Recorded
timings for real sessions are in each run's
`accuracy_stage_compare/raw_provider_events.jsonl`.

For live Deepgram checks, stream a WAV through `wss://api.deepgram.com/v1/listen`
with the parameters `_build_deepgram_url` produces; Windows TTS voices "Microsoft
Zira Desktop" (en-US) and "Microsoft Haruka Desktop" (ja-JP) are installed on
the owner's machine.

## 7. Working rules for this repo

* Tests, from `Alpha_Live_Translator/`:
  `py -m unittest discover -s tests -t tests -p "test_*.py"` (summary on
  stderr). After item 59 (2026-09-30) **nothing fails**: `Ran 1785 tests`
  (~6.5 min), `OK`. The eight stale tests that failed on every run before were
  fixed or removed (see item 59), so any failure now is news.

  Timing-sensitive, pass alone: `test_task12_report` (failed once with a meeting
  replay running at the same time), `test_item48_audio_manifest_bounded` and
  `test_item71_startup_and_hamburger::test_map_corrects_it_before_any_human_could_see_it`.
  Compare failing NAMES, never counts.
* A regression test must fail before the fix. Prove each part of a fix is
  load-bearing with mutants (remove it, a test must go red). Mutating source in
  place: set `PYTHONDONTWRITEBYTECODE=1` -- two mutants of equal size written in
  the same second re-use a stale `.pyc` and report the wrong test.
* A unit test on the lifecycle alone is not proof for a transcript change
  (items 35 and 37 both passed theirs while the export lost text). Drive the
  real publisher, duplicate protection, registry and ledger, as
  `tests/test_a_revision_reaches_the_ledger.py` does, and compare with the code
  before the change.
* One change set = one `APP_VERSION` bump; record it in `FIX_SEQUENCE.md` and
  `CODE_REVIEW_20260904.md`.
* Files are MOSTLY CRLF, not all: `alpha/utils/service_status.py`,
  `tests/test_interim_ghost_line.py`, `tests/test_task2g_acceptance_gate.py`,
  `tests/test_task5_final_cleanup.py` and `tests/test_deepgram_keepalive.py`
  are LF in HEAD. Check
  `git ls-files --eol -- <file>` and match it; never normalise a whole file.
  Never `sed -i` a `.py`; never write Python source through a bash heredoc —
  use a script or the Write tool.
* Before every push: `git fetch origin`, check both directions of divergence,
  and after pushing confirm `git log HEAD..origin/main` and
  `git log origin/main..HEAD` are both empty.
* A test that drives the real `_deepgram_worker` writes `RUN_MANIFEST.json` into
  the working directory unless `accuracy_stage_capture.write_deepgram_request_actual`
  and `troubleshooting_paths.update_run_manifest_deepgram_actual` are stubbed.
