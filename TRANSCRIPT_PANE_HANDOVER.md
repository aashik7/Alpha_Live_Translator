# Hand-over: the reading panes break after the window moves to an external monitor

**Status: OPEN.** Three fixes have been shipped and none of them fixed it. Read
"What has already been tried" before writing any code, and read "Why you
probably cannot reproduce it" before trusting a green test.

Repo: `Alpha_Live_Translator/`. All line numbers below are
`alpha/ui/main_window.py` unless stated.

---

## 1. The symptom, in the reporter's words

- The app is opened on a laptop screen. Everything is fine there — **it has
  never failed on the laptop.**
- The window is dragged to a large external monitor.
- **Click "Hide" on the transcript** → the transcript section does not just
  hide, it is *gone*.
- **Then click "Show Transcript"** in the translation pane → the transcript
  does **not** come back, and the **Show button itself disappears too**, so
  there is now no way to get the pane back.
- Latest report: after the monitor change the **compact / narrow ("mobile")
  layout is completely broken**, not just the transcript toggle.
- Resizing the window by hand makes the UI look correct again. That is the
  reporter's own observation and it is a strong clue: the layout is right the
  next time it runs, just not at the moment of the click.

---

## 2. What the evidence proves

### 2a. The two machine states, from the app's own recording

`troubleshooting/runs/<run>/health/LAYOUT_SNAPSHOT.jsonl`, four consecutive
readings while the window was dragged across:

| Scaling | Window (device px) | Design width | Mode | Where |
| --- | --- | --- | --- | --- |
| 1.5 | 1350 × 975 | 900 | medium | laptop |
| 1.0 | 893 × 650 | 893 | medium | crossing |
| 1.0 | 978 × 650 | 978 | medium | crossing |
| 1.0 | 1121 × 650 | 1121 | **wide** | external, settled |

Two facts follow:

- The external monitor runs the app at **scaling 1.0**; the laptop at **1.5**.
- CustomTkinter **rewrites the window's device size** when the scaling changes
  — the same design geometry re-expressed at the new factor. The window is not
  resized by the user; it is resized by the toolkit.

### 2b. The failing toggle, from `logs/console-*.log`

The app prints one line per toggle (`_trace_transcript_toggle`, :5130):

```
TRANSCRIPT_TOGGLE True->False design_width=893 mode=medium
  transcript_column=mapped:0/w:252/req:1   initial_verse_frame=mapped:0/w:252/req:960
  hide_initial_button=mapped:0/w:64        show_initial_button=mapped:1/w:128

TRANSCRIPT_TOGGLE False->True design_width=884 mode=medium
  transcript_column=mapped:0/w:252/req:1   initial_verse_frame=mapped:0/w:252/req:960
  hide_initial_button=mapped:0/w:64        show_initial_button=mapped:0/w:128
```

Read that carefully:

- **Hide works.** `show_initial_button` ends `mapped:1`.
- **Show does not.** *Every* widget ends `mapped:0`, including
  `hide_initial_button` which `_place_toggle_button` had just gridded. A
  container that never maps takes all of its descendants with it, so this is
  one failure, not four.
- `w:252` with `mapped:0` means the column **had** been laid out at some point
  and is now unmapped — the signature of `grid_remove()`, not of a widget that
  was never placed.
- It fails at **884 / medium** and also at **1238 / wide** in the same log, so
  it is **not** specific to one layout mode.
- `req:1` on the column is the tell for scaling 1.0. The same field reads
  `req:2` on a 150 % display.

### 2c. What the screen capture shows

After the failing Show: the button is gone, no Hide button appears, the
transcript is absent, and the space to the right of the translation card **is
reclaimed but drawn empty**. The column gets its width; nothing renders in it.

---

## 3. Why you probably cannot reproduce it

**`ctk.set_widget_scaling()` compounds with the display's own factor.** On a
150 % laptop, asking for 1.5 yields an effective **2.25** (measured). So
"scaling 1.0 at design width 1121" — the reporter's actual state — is not
reachable on a 150 % machine at all.

Every state that *is* reachable there behaves correctly: design widths 900,
1121, 1350, 1700, 1920, in medium and wide, before and after a scaling change,
with and without a resize between the two clicks.

**Consequence:** a green suite on a 150 % laptop is not evidence. Either test on
a 100 % display, or inject the failure (see §6).

---

## 4. What has already been tried, and did NOT fix it

Do not repeat these.

| Commit | Change | Outcome |
| --- | --- | --- |
| `57890c3` | Added widget scaling to `_apply_responsive_layout`'s early-return cache key (:3329) | Correct in itself — the key really did omit an input — but did not fix the report |
| `08264e0` | `update_idletasks()` at the end of `_sync_transcript_visibility` (:5047) | Its own test fails 4/4 without it, so it changed real behaviour. Report unchanged |
| `463c941` | `_ensure_transcript_pane_matches_flag` (:5084) — re-grid the column if the flag says visible and it is not mapped | Recovers when the failure is injected. Report unchanged |

The third one is a postcondition guard, not a root-cause fix. It was written
deliberately as a safety net because the cause was not identified. **It is still
in place and it is not enough** — which is itself information: re-gridding the
column alone does not restore the UI on that machine.

---

## 5. Where to look — ranked

The reporter now says the whole compact layout is broken after the monitor
change, not only the transcript toggle. That widens the suspect from the pane
toggle to the **layout pass as a whole**, and the most likely shape is that the
layout runs against stale or wrong geometry at the moment the scaling changes.

1. **`_design_width()` (:2398)** — divides `winfo_width()` by
   `ScalingTracker.get_widget_scaling`. If either side is read at the wrong
   moment during a DPI change, every breakpoint below is fed a wrong number.
   **Check what it returns during the transition, not after.**

2. **`_apply_responsive_layout` (:3329)** — early-returns when design width,
   mode and scaling all match the last pass. Verify this is not skipping the
   pass that should repair the layout. Note `_last_layout_width`,
   `_last_layout_scaling`, `_last_layout_mode_applied`.

3. **`_apply_content_layout` (:4299)** — owns the reading grid. Panes are
   `grid()`/`grid_remove()`d and column weights derived from what is visible.
   **The `TRANSCRIPT_TOGGLE` trace proves this returns without leaving the pane
   mapped on the failing machine.** Instrument inside it: log `stacked`,
   `show_transcript`, `visible_reference_panes`, `reference_share`, the `floor`
   and the final `weight` per column.

4. **`_build_content_column` (:4178)** — each column is a `CTkFrame(width=1,
   height=1)` with `grid_propagate(False)`. It relies entirely on column
   weights. At scaling 1.0 the requested size is 1 px, not 2. Check the
   `RIGHT_COLUMN_MIN_WIDTH` (220) floor branch — it sets `weight = 0` and uses
   `minsize` instead, and `_design_px(220)` differs by 50 % between the two
   machines.

5. **`show_compact_layout` / `_apply_header_layout` (:3652 / :3559)** — the
   "mobile version broken" part of the report. Compact mode starts below
   `LAYOUT_HAMBURGER_BREAKPOINT` (880, `theme.py:471`). Confirm which mode the
   window is actually in after the move, from `LAYOUT_SNAPSHOT.jsonl`, before
   assuming.

6. **CustomTkinter's rescale replay.** When widget scaling changes, CTk replays
   each widget's last geometry call. **Measured:** a pane that was
   `grid_remove()`d comes back **mapped at 2 px** afterwards, with the app's own
   visibility flag still `False`. Anything that relies on `grid_remove()`
   staying removed across a DPI change is unsafe.

Relevant constants: `theme.py` — `LAYOUT_WIDE_BREAKPOINT` 1050 (:459),
`LAYOUT_MEDIUM_BREAKPOINT` 700 (:460), `LAYOUT_HAMBURGER_BREAKPOINT` 880
(:471), `LAYOUT_MIN_WIDTH` 400 (:472), `RIGHT_COLUMN_MIN_WIDTH` 220 (:520);
`main_window.py` — `CONTENT_STACK_BREAKPOINT` (:304).

---

## 6. How to get evidence without the monitor

Inject the failure instead of describing it. This is the pattern
`tests/test_item91_toggle_paints_on_click.py` already uses:

```python
real = app._apply_content_layout
def unmapping(*a, **k):
    result = real(*a, **k)
    app.transcript_column.grid_remove()      # the reporter's exact end state
    return result
app._apply_content_layout = unmapping
```

Ask the reporter for a log rather than a screenshot. One run reproducing the
bug, then:

```powershell
Select-String -Path "$env:LOCALAPPDATA\Programs\Alpha Live Translator\app\logs\console-*.log" -Pattern "TRANSCRIPT_TOGGLE|TRANSCRIPT_PANE_REASSERTED" | Select-Object -Last 6
```

`LAYOUT_SNAPSHOT.jsonl` records screen, window, scaling, design width, mode and
per-control `mapped` / `w` / `req` / `past_edge` for the header, status strip,
footer **and** the reading grid, on every real layout change.

---

## 7. Traps that have already cost time

- **The installed build does NOT follow the repo.** Several rounds of "still
  broken" were reported against a build that was 8 commits behind. Refresh it:
  ```bash
  cp -r <repo>/alpha "$LOCALAPPDATA/Programs/Alpha Live Translator/app/"
  cp <repo>/main.py <repo>/collect_logs.py "$LOCALAPPDATA/Programs/Alpha Live Translator/app/"
  ```
  then delete `__pycache__`. **Never delete `<install>/app/.env`** — the
  installer wrote the API keys there.
- **A test that calls `update()` between the click and its assertion cannot see
  this class of bug.** That is how it survived several rounds.
- **Host fixtures must carry every method the code under test calls.** Adding a
  method that `_sync_transcript_visibility` or `toggle_initial_verse` reaches
  means adding it to the `Host` class in
  `tests/test_transcript_pane_default_and_toggle.py` — otherwise all eight of
  its tests turn into `AttributeError`.
- **Line endings vary per file.** `alpha/**.py` is CRLF;
  `tests/test_item71_footer_responsive.py` and
  `tests/test_item71_startup_and_hamburger.py` are LF. Match HEAD per file.
- Never write Python or `.iss` through a bash heredoc — `\a` and `\U` have both
  been corrupted that way.

---

## 8. Verifying a fix

Runner (there is **no pytest** in this venv), from inside `Alpha_Live_Translator\`:

```bash
SKIP_TK_INTEGRATION_TESTS=1 "<repo>/.venv/Scripts/python.exe" -m unittest discover -s tests -p "test_*.py"
```

The summary goes to **stderr** — redirect the two streams separately or it is
buried in the app's own `print()` output.

**Baseline: 1114 tests, 5 failures + 2 errors, 3 skipped — seven names.** Any
change to that set of names means something broke.
`test_item48_audio_manifest_bounded` is a documented intermittent eighth; it
passes in isolation.

A fix is only proven by a log from the reporter's external monitor showing
`TRANSCRIPT_TOGGLE ... mapped:1` after Show. A green suite on a 150 % laptop is
not proof — see §3.

---

## 9. Rebuilding for hand-over

```bash
python installer/build_installer.py --rebuild --version 1.0.3           # installer
python installer/build_installer.py --portable --version 1.0.3          # no-install zip
```

Outputs land in `build/installer/` and `build/portable/` (both git-ignored).
The installer is **unsigned**, so a copy that arrives over the internet raises
SmartScreen; the portable zip avoids it when extracted with `tar -xf`. See
`installer/DELIVERY.md`.
