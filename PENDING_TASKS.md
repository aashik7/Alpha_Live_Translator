# Pending tasks — Alpha Live Translator

Handoff for the next Claude Code session. Written 2026-09-25 at `3070dca`,
updated 2026-09-28 with the owner's Japanese meeting (section 0),
`APP_VERSION = "3.3.5.5.8.5.26.5.34"`. The owner writes Banglish, wants terse
answers, proof over plausible reads, and findings as a table with
issue / risk / severity / importance.

## Where things stand

* Every item through **36** in `CODE_REVIEW_20260904.md` is fixed and pushed.
  `FIX_SEQUENCE.md` is the execution-order ledger; read both before any fix.
* **Items 33–36 are committed but NOT packaged.** The last update package built
  is 26.5.30; the last share build is `build/share/*-1.4.*`, which is 26.5.29.
  The owner calls the final build — do not build unasked.
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

---

## 0. The owner's Japanese meeting of 2026-09-28 — do these first

Owner's report: "it seemed to stop mid-way, I restarted", "translation was very
slow", "accuracy was very poor". Runs, all at 26.5.34, in
`Alpha_Live_Translator/troubleshooting/runs/`:

| Run | Time | Captured |
|---|---|---|
| `v3.3.5.5.8.5.26.5.34-20260928-100031` | 10:00–10:14 | meeting audio until 10:11, mic until 10:07 |
| `v3.3.5.5.8.5.26.5.34-20260928-101440` (restart) | 10:14–10:29 | laptop mic only; meeting audio never |

Nothing crashed and Deepgram never disconnected. Measured findings:

* **"Stopped mid-way" was the audio, not Alpha.** Per-minute speech in the
  retained `audio_temp/*_audio/*.wav` (0.5 s windows, rms > 300): the system
  (meeting) track has speech until 10:11:41, then exact digital zero; the mic is
  exact zero from 10:07 (`MICROPHONE_CAPTURE_DISABLED_MID_SESSION` — the mic
  switch was turned off). At 10:11:26 Windows' default output moved from
  "soundcore R50i NC" (Bluetooth) to "Realtek"; Alpha followed the default
  (rebind OK, 0.26 s) but the meeting app was not playing there. In the restart
  the Realtek loopback was exact zero for all 15 minutes: the meeting played on
  another device (probably the "Jabra SPEAK 410" captured at 10:00:41).
* **Slow translation is the Japanese boundary hold, not DeepL.** DeepL p50
  0.4–0.5 s; commit → translation shown p50 0.5–0.7 s. Last Deepgram final of a
  line → commit: p50 3.2 s (by design: `SENTENCE_HOLD_MIN_MS/MAX_MS` =
  2000/3500 in `japanese_sentence_assembler.py`), p90 5.7–7.4 s, max 20–24 s.
  **Bug:** `JapaneseBoundaryStabilizer.process()` emits a timed-out pending line
  (`BOUNDARY_STABILIZER_HOLD_MS_MAX = 4000`) only when the NEXT final arrives;
  `flush_pending()` is called only at Stop. Log of run `...100031`: line in at
  10:09:21.789, 8 s max-hold timer fired at 10:09:29.801 with no emit, emitted at
  10:09:41.768 (`INCOMPLETE_ENDING_TIMEOUT_EMITTED`, reason
  `pending_timeout_emit`) when the next speaker's final arrived. 27 such lines
  in that run.
* **Accuracy.** Deepgram was confident (median 0.98, 8% under 0.7); dropped
  low-confidence finals were only 26 + 20 characters. Causes: the restart
  captured only the far-field laptop mic; Japanese/English code-switching by
  non-native speakers ("Javaのノーエクスプレイエクスペリエンス", "1gradish");
  names not in keyterms (シャフィー / シャーピー / シャンピー, タリク / ターリップ).
  **`language=multi` was measured and is worse** — on the same retained chunks it
  hallucinated Italian ("Sembra latte. Vabbè è camera ogni."), Hindi script and
  "Daigalguru" ×3. Do not switch to it.

Plan, in order:

| # | Task | Kind | Done when |
|---|---|---|---|
| 0a | Meeting setup: meeting app speaker = Windows default (or set the default to the meeting device before Start); no output-device switching mid-meeting; keep the mic on when the room speaks; prefer the speakerphone as both meeting output and room mic | Owner, no code | Next run's system track is not zero |
| 0b | Release a timed-out stabilizer pending line on a timer — schedule the check on the existing `language_pipeline_worker` heap, no new thread; emit through the same path as the `pending_timeout_emit` branch | Code, HIGH | Replay: a final, 20 s silence, another final — the first line commits ≤ 4 s after its final; re-measure the 27 lines of `...100031` |
| 0c | Visible warning when the system loopback is exact zero for 30–60 s while the mic has speech, naming the device ("Alpha is listening to Realtek; nothing is playing there"), and a notice when Alpha follows an output-device change. Item 31's "● No sound" fires only when BOTH tracks are silent and is a small status-bar label | Code, HIGH | Replaying run `...100031`'s audio shows the warning at ~10:12 |
| 0d | Why three lines in `...101440` got their translation 387 s, 327 s and 51 s after commit (translation segments 81–83) | Investigate | Root cause + regression test |
| 0e | Tune the 2–3.5 s sentence hold: less for lines ending in 。/？ with `speech_final` | Tuning, MEDIUM | Replay of retained runs: latency vs. extra line splits |
| 0f | Participant names and project terms as keyterms (`alpha/resources/keyterms/user_terms.json` exists) — needs the owner's name list; A/B on the retained chunks | Config + test | Same audio before/after |

---

## 1. Before the final build: one real English meeting — HIGH

Item 35 changed the commit pipeline in a way production has never exercised:
**before it, no English revision ever reached the canonical ledger** (two gates
dropped them), so the export kept an early guess while the pane showed the
correction. English revisions are now written as ledger `revise` transactions.
It is proven by replaying the owner's recorded messages through the real app
(see section 6), not yet by a live meeting.

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

Acceptance: no line in the export that the pane does not show, and no line
exported twice.

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
   listed under "kept untouched", and `X/app/alpha/constants.py` at 26.5.34.

Also still owed: **`Setup-*.exe` has never been silently installed and
checked** (only the portable zip was unpacked and inspected). Do that once for
1.5.

## 3. Deliver

* Field user: send the new update package (26.5.34 or later).
* **Never apply an update package of 26.5.26 or older to a keyless install** —
  those delete its `.needs-api-keys` marker.
* New recipients: `build/share/AlphaLiveTranslator-Setup-1.5.exe` or the
  portable zip. Both are keyless: the first Start asks for keys.

---

## 4. Open defects

| # | Issue | Risk | Severity | Importance |
|---|---|---|---|---|
| a | Sentence flush commits text Deepgram later revises. Owner's run: U-13 "Let's see. What is the task? If I can, I can?" then U-14 "If I can't, actually, ..." at the same start 55.39 | Stale tail sentence in the export | Medium | Medium |
| b | English revisions reaching the ledger are new in production (item 35) | Untested live; section 1 | Medium | High |
| c | Japanese: one live probe returned 「十二パーセント」 as 「12」, dropping パーセント; identical with and without `numerals` | Wrong or missing unit in Japanese numbers | Unknown | Medium — measure on real speech first |
| d | English `smart_format`: "two point five million dollars" came back "US2.5 million dollars" | Odd currency text | Low | Low |
| e | `installer/keys.local.ini` still holds a different, probably deactivated, Deepgram key | A KEYED build would ship a dead key; keyless builds unaffected | Low | Medium before any keyed build |
| f | `Ctrl+L` toggles listening app-wide (`bind_all`) while Alpha has focus; a browser habit (Ctrl+L = address bar) can start or stop a meeting | Accidental Start/Stop | Low | Medium |
| g | The app's visible-error audit flags `duplicate_line_continuation` on two people ("Good afternoon." / "Good afternoon. How are you?", different speaker, 3.7 s apart) | False alarm in log analysis | Low | Low |
| h | Every transcript line reads "Speaker:" with no number under a "Speaker 2 · time" header | Looks unfinished; not checked whether new | Low | Low |
| i | Each Japanese Start leaves an empty skeleton run folder next to the real one (e.g. `...-161849` + `...-161850`) | Folder clutter | Low | Low |
| j | Eight stale failing tests in the baseline (section 7) | A new regression can hide in the noise | Low | Low |

Notes:

* **(a)** was excluded from item 35 on purpose and is pinned by
  `test_a_committed_flush_tail_is_never_reopened_with_its_head`: a flush tail
  legitimately shares its window's start, so re-opening on "same start" cannot
  tell the rest of the window from a revision. A fix needs a different signal —
  for example re-comparing the flushed head against the provider's next
  cumulative window while `_split_committed_prefix` is still set.
* **(c)**: the probe script used Windows TTS (Haruka). The owner's earlier
  Japanese TTS run through Alpha kept 「十二パーセント」, so this may be Deepgram
  variance. Collect evidence before touching anything.

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
  stderr). At `3070dca`: `Ran 1677 tests`, and exactly these eight fail (stale):
  * `test_final_transcript_commit_v3_2_5::test_commit_allowed_while_finalizing`
  * `test_final_transcript_commit_v3_2_5::test_commit_allowed_while_listening`
  * `test_keepalive_ping_thread_cannot_crash::test_the_crash_is_reproducible_on_the_unguarded_base_class`
  * `test_package_glossary_flags_85253::test_glossary_helper_absent`
  * `test_package_glossary_flags_85253::test_glossary_helper_present`
  * `test_package_glossary_flags_85253::test_main_glossary_absent_no_unbound_local`
  * `test_package_glossary_flags_85253::test_main_glossary_present_after_successful_inclusion`
  * `test_stop_finalize_v3_2_3::test_phase_constants_match_spec`

  Timing-sensitive, pass alone: `test_item48_audio_manifest_bounded` and
  `test_item71_startup_and_hamburger::test_map_corrects_it_before_any_human_could_see_it`.
  Compare failing NAMES, never counts.
* A regression test must fail before the fix. Prove each part of a fix is
  load-bearing with mutants (remove it, a test must go red).
* One change set = one `APP_VERSION` bump; record it in `FIX_SEQUENCE.md` and
  `CODE_REVIEW_20260904.md`.
* Files are CRLF. Match the file's line endings; never `sed -i` a `.py`; never
  write Python source through a bash heredoc — use a script or the Write tool.
* Before every push: `git fetch origin`, check both directions of divergence,
  and after pushing confirm `git log HEAD..origin/main` and
  `git log origin/main..HEAD` are both empty.
* A test that drives the real `_deepgram_worker` writes `RUN_MANIFEST.json` into
  the working directory unless `accuracy_stage_capture.write_deepgram_request_actual`
  and `troubleshooting_paths.update_run_manifest_deepgram_actual` are stubbed.
