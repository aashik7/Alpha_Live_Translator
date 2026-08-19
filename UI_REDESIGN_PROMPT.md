# Task: apply the "translation focus" design to Alpha Live Translator's UI

You are working in `C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0`.

Read `CLAUDE.md` and `CLIENT_DELIVERY_SPRINT_v5.md` first. Client delivery
deadline is **2026-08-24**. Everything below was verified against the real code
by an earlier session — the file:line anchors are accurate as of commit
`0edc1d6`. Re-verify anything you rely on, but do not re-derive the whole
analysis.

## The goal

Make the app **look like** the design at:

```
C:\Users\islamm\.codex\visualizations\2026\08\10\019fea0e-baf7-7dc2-a6d0-3a826a6dfca0\alpha-translation-focus-preview.html
```

Read that file completely before writing any code. Its `<style>` block is the
specification; its `<script>` block shows intended interactions.

## Phase map and risk

Six phases. Do them in order. Each row states what breaks if you get it wrong.

| Phase | What | Risk | Why that risk |
|---|---|---|---|
| **0** | Reproduce the suspected ungrouped-commit defect | **None to the app** — read-only investigation | Nothing is modified. The risk is in *skipping* it: you would restyle a pane that is already rendering wrong and not know which defect is yours. |
| **1** | Colours + font in `theme.py` | **~0%** | Additive tokens plus value swaps. No geometry, no data path. The font change is safe — see Phase 1. |
| **2** | Global tag restyle in `_create_styled_text` | **~0%** | Verified: `spacing1/2/3`, `lmargin1/2`, `rmargin`, `lmargincolor`, `background`, `font`, `foreground` add **zero logical lines**. Gets the accent bar and tint without touching arithmetic. |
| **3** | Arithmetic fixes (cap, single-line deletes, flush budget) | **Low, and net-negative risk** | These are existing defects. Fixing them removes risk you already carry. Required *before* Phase 4, optional if you stop at Phase 2. |
| **4** | Per-entry model: meta row, current-entry highlight | **Medium** | First phase that adds logical lines and needs per-entry index tracking. Depends on Phase 3. |
| **5** | Layout swap + summary overlay | **Medium** | Re-parents widgets. Callbacks unchanged, but three collapse/weight helpers hardcode row indices. |
| **6** | Divider, toast, interface-language toggle | **High** | Net-new code with no existing pattern. Optional before the deadline. |

**Phases 0→2 give roughly 65% of the visual change at near-zero risk.** If time
is short, stop after Phase 2 and ship that.

## Hard constraints — not negotiable

1. **Keep CustomTkinter 5.2.2.** No migration to any web/HTML renderer, no
   pywebview/Eel/Electron, no new UI framework.
2. **No functional change.** A control may move or be restyled. No control may
   lose its action, gain a new one, or change which data path it drives.
3. **No data-path regressions.** `UI_CHANGE_BASELINE_AUDIT.md` defines contracts
   C1–C10. They still apply.
4. **Do not touch anything under `_archive/`.**
5. Active app code is in `Alpha_Live_Translator/`.

## The single rule that decides most of the risk

**Adding a logical line is what breaks things. Adding pixels is not.**

Verified on Tcl/Tk 8.6.15:

- `box.delete("1.0", "2.0")` removes one **logical** line. A wrapped paragraph
  occupying 169 display lines was removed by a single such delete.
- Therefore **word wrap, font size and font family do not affect the cap
  arithmetic at all.**
- A two-logical-line entry does break it: 3 lines → one delete → 2 lines, i.e.
  half the entry survives as an orphan.
- These tag options add **zero** logical lines and are all safe:
  `spacing1`, `spacing2`, `spacing3`, `lmargin1`, `lmargin2`, `rmargin`,
  `lmargincolor`, `background`, `font`, `foreground`, `tabs`.

Anything that inserts its own `\n` — a meta row above the body, a separator, a
"✓ Translation ready" line — adds a logical line and requires Phase 3 first.

---

## Phase 0 — investigate before touching anything

**Risk: none to the app. This phase modifies no files.**

A suspected live defect would sit underneath the redesign. It was traced by
reading the call chain, **not** by executing it. `CLAUDE.md`'s verification rule
is explicit that this project has twice been burned by a plausible read that was
wrong. Reproduce it before believing it.

**The claim:** the live transcript pane renders committed text ungrouped, so
item 69's grouping fix never runs on the normal commit path.

**The chain:**

- `alpha/transcription/duplicate_protection.py:707-723` dispatches a
  translation-eligible commit to `_on_store_segment_updated` (when
  `action == "update"`) or `_on_store_segment_added` — never to
  `_render_transcript_from_store`.
- Both hooks end at `alpha/ui/main_window.py:1396-1400`
  `_insert_speaker_segment_line`, which does
  `box.insert("end", (text or "").strip() + "\n", "body")` — raw, with no call
  to `transcript_store._readable_parts`.
- The grouped renderer is reached only on the `not translation_eligible` branch
  (`duplicate_protection.py:705`) or when neither hook exists (`:724`) — and
  `AlphaApp` defines both.

**What to do:** build a minimal host that borrows the real methods (follow the
`_Host` / `*TestHost` pattern in `tests/`), drive a real English commit through
`duplicate_protection`, and read back what actually landed in
`initial_verse_box`. Compare against `transcript_store.get_clean_text()` for the
same input.

- **If the pane text is one raw paragraph** while the store text is grouped into
  2–3 sentence lines: the defect is real. Fix it before Phase 4, and record in
  `CLIENT_DELIVERY_SPRINT_v5.md` that item 69 is not actually closed.
- **If the pane text is grouped:** the claim was wrong. Say so plainly and
  **retract it visibly** in the sprint log rather than deleting it
  (`CLAUDE.md` rule 4).

Either way, fix this independently-confirmed weakness:

`UI_CHANGE_BASELINE_AUDIT.md` §7.3's C8 check only greps
`_render_transcript_from_store_now`'s source for the string `_readable_parts`.
It cannot see `_insert_speaker_segment_line` at all, and passes vacuously if the
renderer is renamed. Extend it to assert **every** function that writes to a
transcript box calls `_readable_parts`. Same weakness applies to its C6 check.

---

## Phase 1 — colours and font

**Risk: ~0%. Additive tokens and value swaps only.**

The design was derived from this app's own `alpha/ui/theme.py`. These already
match exactly and need no change: `APP_BG #07111F`, `PANEL_BG #0B1220`,
`CARD_BG #101827`, `INPUT_BG #111827`, `BORDER #26354F`, `BORDER_SOFT #1E2A40`,
`ACCENT_BLUE #3B82F6`, `ACCENT_BLUE_HOVER #2563EB`, `ACCENT_RED #EF4444`,
`ACCENT_RED_HOVER #DC2626`, `TEXT_PRIMARY #F8FAFC`, `TEXT_SECONDARY #CBD5E1`,
`TEXT_MUTED #64748B`, `accent_red_glow #F87171`, `waveform_bar #60A5FA`.

Add these 13 tokens:

| Hex | Purpose |
|---|---|
| `#0D1728` | translation pane background |
| `#091320` | original/reference pane background |
| `#31415C` | button border |
| `#3B4B67` | toggle border |
| `#152238` | toast background |
| `#86EFAC` | success text — `ACCENT_GREEN #22C55E` is visibly darker, do not substitute |
| `#4ADE80` | speaker-2 dot |
| `#94A3B8` | muted label |
| `#E2E8F0` | button text |
| `#1B4B7A` | scrollbar thumb |
| `#050D19` | scrollbar track |
| `#DBE5F2` | summary body text |
| `#1D4ED8` | toggle "on" background |

The design's `rgba()` values sit on opaque backgrounds, so pre-composite them to
flat hex. Already computed:

- `.atf-current-entry` `rgba(59,130,246,.09)` over `#0d1728` → **`#11213B`**
- `.atf-original-current` `rgba(59,130,246,.06)` over `#091320` → **`#0C1A2D`**
- summary backdrop `rgba(2,8,23,.72)` over `#0b1220` → **`#050B1A`**

**The font.** `theme.py:107` sets `FONT_FAMILY = "Segoe UI Variable"`, which Tk
cannot resolve: `tkfont.Font(family="Segoe UI Variable").actual("family")`
returns `Arial`, and `_ui_font`'s try/except never fires because Tk substitutes
silently rather than raising. The whole app renders in Arial today.
`"Segoe UI"` **is** registered and is what the design specifies.

Changing it is **safe** — it alters wrap points, but wrap produces display
lines, not logical lines, and every piece of arithmetic in this file
(`delete("1.0","2.0")`, `mark lineend`) operates on logical lines. Verified
directly.

Also apply the pane background colours here: `fg_color` on the translation pane
frame → `#0D1728`, on the reference pane frame → `#091320`, and
`border_color` → `#31415C` on bordered controls. These are value swaps on
existing widgets; no geometry changes.

---

## Phase 2 — global tag restyle

**Risk: ~0%. No logical lines added, no data path touched.**

`_create_styled_text` (`main_window.py:3176-3237`) builds a **raw `tk.Text`**,
not a `CTkTextbox`, so the full tag option set is available. It already
configures `body`, `interim`, and the `SPEAKER_COLORS` tags at `:3221-3224`.

Restyle those existing tags. Verified available on Tcl/Tk 8.6.15:

| Tag option | Design feature |
|---|---|
| `background` | entry tint — use `#11213B` |
| `lmargincolor` | **the 3px left accent bar** |
| `lmargin1` / `lmargin2` | entry left padding, wrapped-line indent |
| `rmargin` | right padding |
| `spacing1` / `spacing2` / `spacing3` | padding-top / line-height / padding-bottom |
| `font` | 18px body, 20px emphasis |
| `foreground` | per-entry text colour |
| `tabs=(x, 'right')` | right-aligned timestamp (needs recompute on resize) |

Speaker dots: `SPEAKER_COLORS` (`theme.py:82`) already exists and is already
configured on both boxes.

This gets the palette, the reading rhythm, the accent bar and the tinted
background. **It does not get you a per-entry *differentiated* highlight** —
that needs to know which entry is current, which is Phase 4.

**Stop here if time is short.** Everything above is reversible by editing two
functions and one config file.

---

## Phase 3 — arithmetic fixes

**Risk: low. These are existing defects; fixing them reduces risk you already
carry. Required before Phase 4.**

**a) The render cap** — `main_window.py:1448-1471`.
`box.delete("1.0", "2.0")` runs `excess` times while `_displayed_segment_count`
increments by 1 per segment. It works only because `_insert_speaker_segment_line`
emits exactly one **logical** line. A meta row makes it two; the cap then
under-trims by the lines-per-entry factor, the widget grows unbounded across a
99-minute session, and partial deletes leave orphaned half-entries at the top.
`UI_RENDERED_SEGMENT_LIMIT_REACHED` still prints once and looks correct.

Fix by recording a per-entry start mark and trimming to that mark, or by
tracking inserted line counts per entry in a deque. Add a regression test that
appends `MAX_RENDERED_UI_SEGMENTS + 50` two-line entries through the real widget
and asserts `int(box.index('end').split('.')[0])` stays bounded.

**b) The translation single-line deletes** — `main_window.py:7280` and `:1641`.
Both delete `mark -> mark lineend + 1 chars`, exactly one logical line. A
two-line translation entry means a revision removes the body, orphans the second
line, then pops the registry at `:1646` so nothing can reclaim it.

**c) The batch flush has no time budget** — `main_window.py:1033-1048`.
`_flush_transcript_ui_batch` iterates `batch[:max_inserts]` (8, or 12 under
backpressure) with no elapsed check. The drain loop's budget test at `:873-877`
is evaluated *before* `transcript_queue.get()` at `:878`, so it bounds how many
items are started, not how long the tick runs. `UI_QUEUE_TIME_BUDGET_MS` is 10
and `TRANSCRIPT_UI_BATCH_FLUSH_MS` is 200; if per-item cost reaches ~25 ms the
flush outlasts its own interval and the queue never drains, at which point the
`>150` backpressure branch at `:819-838` *raises* `max_per_poll` to 24 and
deepens the stall. Add a real elapsed check inside the loop.

Confirm queue depth stays 0 before moving on.

---

## Phase 4 — per-entry model

**Risk: medium. First phase that adds logical lines and needs index tracking.**

**Render entries as `tk.Text` tag ranges. Do not build per-entry widgets.**

Measured on this machine, not estimated:

| | per-entry CTk widgets | current `tk.Text` |
|---|---|---|
| 430 entries | 15.33 s, 5,161 widgets, +49.3 MB RSS | 38.46 ms full rewrite |
| per entry | 35.6 ms | 0.042 ms incremental |
| Clear/destroy | 3.53 s | — |

One card costs 3.6× the entire 10 ms per-tick budget — ~400× the full-rewrite
cost and ~850× the incremental cost. `CTkScrollableFrame` also gets *slower* as
children accumulate (51.5 → 67.2 ms per card, because its `<Configure>` handler
calls `bbox('all')`, which is O(canvas items)); its `destroy()` only destroys the
inner frame, orphaning the `CTkFrame`, canvas, scrollbar and label; and its
constructor registers five `bind_all` mouse-wheel handlers that are
application-global and never unbound.

**Two things must not go inside the text flow.** `_update_interim_line_only`
sets `interim_anchor` at `"end"` (`:1679`) and
`_remove_interim_line_from_display` runs `delete("interim_anchor", "end")`
(`:1383`). Tk marks default to right gravity — no `mark_gravity` call exists
anywhere in the file — so anything appended after that `mark_set` is inside the
delete range and is destroyed on the next interim tick. Render the
"Listening for the next sentence…" row and any entry separator as **real widgets
below the text box**, not as characters in it.

**Keep decorative text out of `translated_verse_box` entirely.**
`_get_translated_transcript_for_copy_export` (`:8956-8977`) falls back to
`box.get("1.0", "end")` whenever no completed item carries `line_text`, and
`:7317-7321` shows completions without a `canonical_utterance_id` are skipped
from that registry in normal operation. That string is written verbatim into the
`=== Translated transcript ===` section of the export at `:9015-9021`. A
"✓ Translation ready" line rendered there ends up in the client's delivered file.

Whatever renders an entry **must** call `transcript_store._readable_parts(seg)`
and keep its `except: parts = [text]` fallback verbatim. Attach the meta row to
the **segment** and the grouped parts as its body. Never read `seg.text`
directly for rendering.

---

## Phase 5 — layout swap and summary overlay

**Risk: medium. Widget re-parenting; callbacks unchanged.**

Current: `left_column` holds the transcript (row 0, weight 65) above the
translation (row 1, weight 35); `right_column` holds the summary card.

Target: translation is the primary left pane (70), the original transcript is
the right reference pane (30), summary becomes a modal overlay.

**Change only `master=` and `grid_row=`/`grid_column=` at
`main_window.py:2897-2918`. Do not change the `attr_name=` arguments.**
`_transcript_box()` returns `getattr(self, "initial_verse_box", None)` (`:1317`)
and is the target of every transcript insert. Swapping the `attr_name` strings
routes transcript text into the translation widget, which the widget-read export
fallback then writes into the export as if it were translation output. If a
rename is wanted, change it in `_transcript_box` only, and grep the 12 direct
dereferences of both attributes first.

Code that silently stops working if you move the cards from rows to columns:
`toggle_initial_verse` hardcodes `left_column.grid_rowconfigure(0/1)` at
`:3373-3382`, and `_apply_left_column_panel_weights` writes rows 0/1 at
`:3388-3397`. `grid_rowconfigure` on a container whose children are now in
columns is a no-op that raises nothing.

**Widget lifecycle (C2):** create every widget eagerly in `__init__` exactly as
today; hide with `grid_remove()` / `place_forget()`. Never defer creation. Three
call sites dereference the boxes with no guard — `_create_context_menu`
(`:8862-8863`, runs at startup), `_insert_formatted_text`'s trailing
`text_widget._scrollbar` (`:8852`), and `clear_text` (`:9111-9115`). A
never-created attribute raises `AttributeError`, not `None`. Initialise every new
attribute to `None` in the `__init__` block at `:331-357`.

The summary overlay is the file's first use of `.place()` — currently zero
occurrences. **Do not add `grab_set()`.** A widget grab combined with the 18
remaining `tkinter.messagebox` call sites can wedge the Tk thread, stopping
`_process_ui_queue_once` draining `transcript_queue`; if a Stop happens in that
window, `flush_pending_translation_submissions` blocks on
`done.wait(timeout=2s)` (`:7168-7170`) and segments committed just before Stop
are never enqueued for translation.

---

## Phase 6 — new controls

**Risk: high. Net-new code, no existing pattern. Optional before the deadline —
confirm with the user first.**

**Draggable divider.** No `PanedWindow`, no `sash`, no `B1-Motion` anywhere;
`self.paned` is a decoy alias for `left_column`. Measured: `tk.PanedWindow.add()`
**rejects** a `CTkScrollableFrame` with a `TclError`, and
`sashcursor='col-resize'` is rejected — Tk wants `sb_h_double_arrow`.

The real obstacle is neither. `_apply_content_layout` hard-assigns the column
weights to 7/3 or 1/0 on every call (`:2936-2945`) and is invoked from
`_apply_responsive_layout_tail` (`:2390`) on every window resize and from
`show_summary_panel` / `hide_summary_panel` (`:2753-2780`). Any drag position
stored as grid weights is silently reset by the next resize. Teach
`_apply_content_layout` a persisted ratio, or do not build the divider.

The design's own sizing rules also conflict below ~528 px window width:
`minmax(220px, 30fr)` and a 24–44% clamp cannot both hold, and this app's
`minsize` is 400 (`theme.py:229`). Between 400 and 528 px Tk honours minsize over
weight and the primary pane is squeezed to ~152 px.

**Toast.** No mechanism exists (`grep toast` → 0). If you add one:
- Add `_toast_job` to `_stop_ui_loops` (`:1106-1123`) — it currently cancels only
  four handles by name, and `_shutdown_and_destroy` calls it at `:8809` then
  `destroy()` at `:8821`, so anything uncancelled fires across that window.
  `configure()` on a destroyed CTk widget raises `TclError: invalid command
  name`, which this app's excepthook records as `CRASH_HOOK_TRIGGERED`
  (baseline: 0).
- Open `_hide_toast` with a `winfo_exists()` guard — correct pattern at `:607`.
- Drive it from button commands only. The six event-bus subscribers at
  `:7773-7823` are log-only today and therefore thread-safe by accident; they
  run on whichever thread published. `self.after` is itself a Tk call, so a toast
  armed from a subscriber is a background Tk call (C1). Compounding it,
  `ui_event_bus._dispatch` runs delay-0 callbacks inline on the drain tick
  (`ui_event_bus.py:225-227`) specifically so they survive Stop cancelling
  `after()` jobs.

**Interface EN/日本語 toggle.** Does not exist. `_build_language_profile` is
translation routing, not UI labelling. Genuinely new feature.

---

## Do not attempt these — no Tk equivalent exists

- `box-shadow` (`.atf-app`, `.atf-summary-dialog`, `.atf-toast`). `grep shadow`
  over customtkinter 5.2.2 returns zero hits; a Tk widget cannot paint outside
  its own rectangle. Do not fake it with stacked frames — it bands visibly. The
  1px border already carries the elevation, which is why the design has both.
- Per-corner `border-radius` (`.atf-current-entry` uses `0 10px 10px 0`).
  `draw_engine.py:96-97` takes one scalar for all four corners. Square corners.
- CSS `transition` (140–180 ms). CTk's only timed behaviour is a 100 ms click
  flash. A 16 ms step loop is 25–56× the tick rate of anything in this app and
  will read as stutter, not ease.
- Negative margins (`.atf-current-entry` bleeds 10px wider than its siblings).
  Tag margins cannot paint outside the widget's own `padx=12`.
- The rounded, shadowed outer window frame and the Desktop/Mobile preview
  buttons — preview scaffolding, not product UI.

## Things the design drops that you must not silently delete

- `status_text_label` has no home in the design, but it is the only surface that
  reports a degraded Stop — the stop watchdog writes "Stopped. Diagnostics may
  still be saving." to it at `:8396-8400`. Keep it, or build the toast first.
- The hamburger menu has no design equivalent, but `swap`, `Meeting Summary` and
  `Export` have no menu twin, so they are already unreachable below 800 px.
  Removing the hamburger without first making every control reachable there is a
  functional regression. Scope it separately.
- Do not "just" set `self.waveform_canvas = None` to drop the waveform.
  `_apply_status_bar_layout` returns on that null check as its **first**
  statement (`:2949-2950`), which disables the entire status-bar responsive path
  at every width, including the "Ready" / "Ready to listen" swap at `:2954-2964`.

## While you work

- `clear_text` (`:9070-9122`) calls `transcript_store.clear()` with no
  confirmation, and the compact footer branch (`:3025-3033`) makes Clear the only
  visible button. The design makes it full-width. Consider adding an `askyesno`;
  flag it to the user either way.
- `clear_text` also replaces `_translation_debounce_after_ids = {}` at `:9087`
  without cancelling those timers; jobs armed at `:7028` still fire into wiped
  state.
- `check_scrollbar_visibility` wraps its `scrollbar.pack(...)` in a bare
  `except: pass` (`:3150-3157`). A pack/grid mismatch from your restructure will
  not raise and will not log — the scrollbar just never appears again. Check
  visually after any geometry change.
- Three grid/pack boundaries are fragile: `status_bar_frame` → `inner` (pinned by
  `pack_propagate(False)`), the language-combo `wrapper`, and the verse
  `text_frame`. Flipping one side without the other raises `TclError`.
- `_deferred_apply_logo` packs into `brand_block` with `before=children[0]` after
  startup. If you convert `brand_block` to grid, change that path too or it
  raises a few hundred ms after launch.
- Seven structural frames are local variables with no `self.*` attribute
  (`titles`, status `inner` and `live_wrap`, summary `header` / `body_frame` /
  `text_shell` / `summary_scroll`). To restyle them you must edit their creating
  function.

## Verification — required before you call any phase done

Automated tests **cannot** catch a UI regression here.
`UI_CHANGE_BASELINE_AUDIT.md` §0 records that only 2 test files touch Tk and both
are skipped via `SKIP_TK_INTEGRATION_TESTS=1`. A green suite is not evidence.

1. Full suite — expect `Ran 674 tests` (or more), `failures=5, errors=2,
   skipped=3`. The 7 failing names are listed in §7.2 and are pre-existing;
   `test_task9_report` is a known flake. **Any other new failing name is your
   regression.**
2. §7.3 contract smoke test — must print `ALL CONTRACTS PASS`. Extend its C8 and
   C6 checks per Phase 0 first, or it is checking nothing.
3. §7.4 render benchmark — baseline 1.4–3.3 ms at 320 segments, fail above 25 ms.
   **This gate measures `get_clean_text` only and cannot see widget build cost.**
   If you change the render model, add a timing gate that measures the actual
   widget work.
4. §8 live test — mandatory from Phase 4 onward, ~6 minutes, including a
   35-second WiFi drop. Then run §8's analyser and compare against §9's table.
   For Phases 1–2 a short visual check is enough.
5. Read §9.1 before interpreting the log. `BACKGROUND_TK_CALL_BLOCKED` at 20–777
   is the C1 guard **working**, not a failure. The five genuine failures are
   `CRASH_HOOK_TRIGGERED`, `THREAD_EXCEPTION_CAPTURED`,
   `COMMITTED_SEGMENT_DROPPED_AS_INTERIM`, `UI_FULL_REWRITE_BLOCKED`,
   `IN_FLIGHT_COMMIT_ON_DISCONNECT_FAILED` — all 0 on the baseline.
6. Queue depth must stay 0 at all snapshots; memory growth near the
   +13.5 MB / 74 min baseline.

Compare against the baseline with a worktree, not a checkout — the audit file
does not exist at `ac25308`:

```bash
git worktree add ../alpha-baseline ac25308
```

## How to work and report

- Land one phase at a time. Do not batch phases into one commit.
- Commit and push each phase once its tests pass with the baseline unchanged.
  `git fetch origin` and check for divergence first — this repo is worked from
  multiple machines.
- Report each phase as PASS/FAIL per contract C1–C10 using §10's format, with
  evidence, not assertions.
- If something here turns out to be wrong, say so plainly and retract it visibly
  in `CLIENT_DELIVERY_SPRINT_v5.md`. Do not silently rewrite it.
- If a phase is blocked, finish every other phase and state exactly what you left
  out and why.

## Open items not caused by this change

Item 49 (clean-machine install, needs a second PC) and item 70 (English text
settles ~1 line/min — highest risk, explicitly scheduled last). Do not bundle
item 70 into this work.
