# The transcript pane bug: root cause, fix, and why it took five attempts

**Status: CLOSED.** Fixed in `61aa5cb` and `c0bda58`. Verified on the repo build
and on the installed build, at widths from 400 to 1920 design px. **A third cause,
reachable only through a monitor move, was found and fixed on 2026-09-15 — see §7.
A fourth, and the one underneath the original report, was reproduced on the physical
100 % monitor and fixed the same day — see §8.**

This replaces `TRANSCRIPT_PANE_HANDOVER.md`, which was written while the cause
was still unknown.

---

## 1. What was reported

Two symptoms, reported weeks apart and treated as one bug:

- **On a large external monitor.** Clicking *Hide* removed the transcript.
  Clicking *Show Transcript* afterwards brought nothing back **and removed its
  own button**, leaving no way to recover the pane.
- **At small "mobile" window sizes.** UI components appeared to vanish; the
  compact layout looked broken after the same toggle.

One observation from the reporter turned out to be the key to both:

> Clicking the button changes nothing — but if I resize the window, then after
> the first refresh the UI looks normal.

---

## 2. Two causes, not one

The two symptoms had **two different causes**, which is why every single-cause
theory failed.

### Cause A — the layout laid out panes inside a frame that was not on screen

`_apply_content_layout` owns the reading grid: it grids and un-grids
`left_column`, `transcript_column` and `right_column` inside `content_wrapper`,
and derives the column weights from what is visible.

It never asserted that **`content_wrapper` itself** was on the grid. Nothing
else did either, except `_apply_responsive_layout` — which only runs on a
resize, and re-grids the wrapper on its way past.

So the click path and the resize path were two different jobs:

    click  -> _place_toggle_button
              _apply_content_layout                    the reading grid only

    resize -> _apply_responsive_layout
              content_wrapper.grid_configure(padx)     the CONTAINER
              status bar, footer, brand block
              _apply_header_layout
              after(1) -> _apply_content_layout

Show computed a perfectly correct grid **inside a frame that was not mapped**.
Nothing appeared. Resizing "fixed" it because the resize path put the frame
back.

This also explains the strangest line in the reporter's log:

    TRANSCRIPT_TOGGLE False->True
      transcript_column   = mapped:0
      initial_verse_frame = mapped:0
      hide_initial_button = mapped:0     <- had JUST been gridded
      show_initial_button = mapped:0

A button gridded one line earlier reading `mapped:0` is not a fourth failure to
explain. It is what an **unmapped ancestor** does to everything beneath it. One
fault, not four — which is why three fixes aimed at the pane changed nothing.

**Fix (`61aa5cb`)** — in `_apply_content_layout`, before laying out anything:

```python
if self.winfo_ismapped() and not self.content_wrapper.winfo_ismapped():
    self.content_wrapper.grid()
```

`grid()` with **no arguments** is the pair to `grid_remove()`: it restores the
frame's own remembered row, column, sticky and padding. Passing them explicitly
imposes this window's layout on every caller — measured, that collapsed five
reading-grid tests whose fixture grids the wrapper its own way.

### Cause B — the rescue guard was breaking the small layout it was meant to save

`_ensure_transcript_pane_matches_flag` was added earlier (`463c941`) as a
postcondition net: if the flag says visible and the pane is not mapped, put it
back. It did that by **writing placement itself**:

```python
column.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)
content_wrapper.grid_columnconfigure(1, weight=CONTENT_REFERENCE_WEIGHT)
```

Row 0, column 1 is where the transcript belongs in the **column** layout. Below
`CONTENT_STACK_BREAKPOINT` (700 design px) the panes are **rows in column 0**,
and the stacked branch has just set column 1's weight to zero.

Measured on the real window at 640 design px:

| | grid | size | position |
| --- | --- | --- | --- |
| healthy | `row=1 col=0` | 940 × 327 | (10, 705) — below the translation |
| after the guard | `row=0 col=1` | 897 × 461 | (53, 252) — **over the translation** |

So at small sizes the rescue dropped the transcript on top of the translation,
in a column with no weight. That is the "components disappear at mobile size"
half of the report — **introduced by the guard, not found by it**.

Two authorities on where a pane goes: `_apply_content_layout` owns it for both
branches, and the postcondition check carried its own copy for one of them.

**Fix (`c0bda58`)** — the guard asks instead of placing:

```python
self._apply_content_layout(design_width=self._design_width())
self.update_idletasks()
if not column.winfo_ismapped():
    column.grid()          # the pane's OWN remembered position
```

`column.grid()` with no arguments restores whatever the layout last wrote — rows
or columns — without the guard knowing which. That keeps the guard's original
guarantee (it still rescues a layout that refuses to place the pane) without
inventing coordinates.

Verified, repaired position now identical to healthy:

    WIDE      healthy row=0 col=1  ==  repaired row=0 col=1
    STACKED   healthy row=1 col=0  ==  repaired row=1 col=0

---

## 3. Why it took five attempts

| Commit | Change | Why it did not fix it |
| --- | --- | --- |
| `57890c3` | Widget scaling added to the layout cache key | The key really did omit an input, but that was not the fault |
| `08264e0` | `update_idletasks()` after the toggle | Real behaviour change, wrong layer |
| `463c941` | The postcondition guard | Rescued the pane — and became Cause B |
| `61aa5cb` | Cause A fixed | Correct, but Cause B was still live |
| `c0bda58` | Cause B fixed | Closed |

Four things made it durable:

1. **The failing state was unreachable on the development machine.**
   `ctk.set_widget_scaling()` compounds with the display's own factor — asking
   for 1.5 on a 150 % display yields **2.25**, measured. The reporter's
   "scaling 1.0 at design width 1121" could not be produced locally at all.
2. **A test that calls `update()` between the click and the assertion cannot
   see this class of bug.** Several rounds of green tests proved nothing.
3. **The diagnostic recorded every button and none of the panes.** The reading
   grid was absent from `_SNAPSHOT_CONTROLS` until it was added deliberately.
4. **The second cause was created by the fix for the first.** Each round
   changed the symptom, which read as "still broken" rather than "different
   now".

The break came from the reporter saying it also happened at **small window
sizes** — the first version of the failure that was reproducible locally.

---

## 4. How it was proven

Neither fix was accepted on a green suite alone. Both were measured before and
after, on the real window:

    Cause A, ancestor unmapped once (the reporter's end state)
      PRE-FIX  after SHOW   wrapper=0 column=0 card=0 hide=0
      PRE-FIX  + idle       wrapper=0 column=0 card=0 hide=0
      FIXED    after SHOW   wrapper=1 column=1 card=1 hide=1

    Cause B, guard repairing a hidden pane at 640 design px
      PRE-FIX  row=0 col=1 at (53, 252)      over the translation
      FIXED    row=1 col=0 at (10, 705)      where the layout puts it

`tests/test_item91d_show_runs_the_full_layout.py` carries nine tests; six of
them fail against the code without these changes. Nothing in them calls
`update()` between the click and the assertion.

Ruled out first, by measurement rather than by reading:

- `mapped` is correct at device widths 900 / 800 / 750 / 700 / 660 / 640 / 600 /
  520 / 460, with and without a settle.
- Geometry is inside the window in both the column and the stacked branch.
- Driving it through real `<Configure>` events with a click inside the 200 ms
  resize debounce also behaves.

Live runs afterwards: 22 toggles from 1920 down to 400 design px on the repo
build, 12 toggles at 712 and 668 on the installed build. No errors, every
toggle correct, including crossing the 700 px column-to-row boundary.

---

## 5. What is still open

> **Resolved 2026-09-15 — see §8.** The reassert fired because Tk's grid skipped the
> re-arrange at 100 % scaling. On the physical monitor it fired once per Show before
> the fix and zero times after.

~~`TRANSCRIPT_PANE_REASSERTED` still appears on every Show in a live run, meaning
`_apply_content_layout` does not leave the column mapped on the first attempt
and the guard is carrying it. The result is correct and lands in the same frame,
so this is not user-visible — but the normal path relying on its safety net is a
loose thread worth pulling when there is time.~~

~~It was not reproducible in isolation: calling `_apply_content_layout` followed
by `update_idletasks()` maps the column every time outside the live app.~~

(The isolation attempt ran on the 150 % laptop, where the re-flow is never skipped.)

---

## 6. Verifying any future change here

Runner (there is **no pytest** in this venv), from inside `Alpha_Live_Translator\`:

```bash
SKIP_TK_INTEGRATION_TESTS=1 "<repo>/.venv/Scripts/python.exe" -m unittest discover -s tests -p "test_*.py"
```

The summary goes to **stderr** — redirect the two streams separately or it is
buried in the app's own `print()` output.

**Baseline: 1123 tests, 5 failures + 2 errors, 3 skipped — seven names.** Any
change to that set of names means something broke.
`test_item48_audio_manifest_bounded` is a documented intermittent eighth; it
passes in isolation.

Two traps that already cost time:

- **The installed build does not follow the repo.** Refresh it before testing
  there, and **never delete `<install>/app/.env`** — the installer wrote the API
  keys into it.
- **A higher version number is not a newer build.** `Setup-1.0.4.exe` sat in
  `build/installer/` dated two days before `Setup-1.0.3.exe`. Check timestamps,
  not filenames.

---

## 7. Addendum, 2026-09-15 — Cause C: hidden widgets came back after a monitor move

### What it is

CustomTkinter remembers each widget's last geometry call and replays it when the
monitor's DPI changes (`CTkBaseClass._set_scaling`), so paddings are re-scaled. In
CustomTkinter 5.2.2 it forgets that call on `grid_forget`, `pack_forget` and
`place_forget` — **but not on `grid_remove`**, which it inherits from tkinter. So a
widget the app hid with `grid_remove()` kept its old `grid(...)` call, and moving the
window to a monitor at another scale replayed it: the widget came back on screen while
the app still believed it hidden. No layout pass re-hides it, because every owner
already thinks its widget is hidden.

### How it was reached without the monitor

§4 judged the 100 % state unreachable on a 150 % laptop because
`ctk.set_widget_scaling()` compounds with the display factor. That is true of that
API, not of the path a monitor move takes. CustomTkinter polls
`ScalingTracker.get_window_dpi_scaling(window)` and, on a change, runs every widget's
rescale. Making that return 1.0 drives the real code on the real `AlphaApp`.

### What it did, measured on the real window

After one Hide/Show and, in compact layout, one open-and-close of the hamburger menu,
a monitor move changed the mapped state of **22 widgets across 8 layouts**:

- **compact:** `menu_dropdown_frame` opened by itself, with all nine menu controls —
  the "compact / mobile layout completely broken" half of the report;
- **every width, transcript shown:** `show_initial_button` appeared beside Hide — the
  live report item 81 recorded of both buttons on screen at once. Clicking that
  phantom "Show Transcript" *hides* the transcript.

The responsive pass a resize runs repaired neither.

### Fix (26.5.24)

`alpha/ui/ctk_grid_remove_fix.py` gives `grid_remove` the treatment CustomTkinter
already gives `grid_forget` — forget the replay record — installed once when
`main_window` is imported, before any widget exists. Tk still remembers a removed
widget's grid options, so `grid()` with no arguments (91d, 91e) restores it as before.

- Mapped-state changes after a monitor move: **22 → 0**.
- A widget hidden *during* a move and shown afterwards gets the same geometry as one
  that never moved (5 of 6 cases identical; the sixth differs by 1 px of column width
  at 1121 px, 1.0 → 1.5 — identical with the fix removed, so pre-existing rounding).
- Visible widgets are still re-scaled (padding 8 → 12 at 1.5).

Tests: `tests/test_hidden_widgets_stay_hidden_across_a_monitor_move.py`, 8 tests, 5
failing before the fix: the mechanism on plain CTk frames, buttons and scrollable
frames, and both reported symptoms on the real `AlphaApp`.

### What this does NOT show

The same simulation does **not** reproduce Cause A on the code before 91d
(`463c941`): the reading grid stayed mapped there too. Whatever unmapped
`content_wrapper` on the reporter's machine involves more than CustomTkinter's rescale
— most likely the Windows side of a real move (`WM_DPICHANGED` resizing the window).
Cause A's closure still rests on the injected end-state tests of 91d, and no fix here
has been confirmed on a physical 100 % monitor. The log line that would confirm it is
still `TRANSCRIPT_TOGGLE ... mapped:1` from that machine.

---

## 8. Addendum, 2026-09-15 — Cause D: Tk's grid skipped the re-flow at 100 % scaling

### The report, on 26.5.24

With the window on the external monitor: *hide the transcript and the translation
pane does not resize; drag the window by hand and it does.* The app's own
`LAYOUT_SNAPSHOT.jsonl`, window at x=2391 on the 100 % monitor:

| Action | Translation pane |
|---|---|
| Hide at 890 design px | **583 px** — should be ~845 |
| user drags by 3 px | 845 px |
| Hide again | **585 px** |
| drag | 863 px |

### Reproduced on the physical monitor

This machine has the 100 % monitor attached beside the 150 % laptop, so for the first
time the reporter's real state was reachable: the real `AlphaApp`, moved onto that
monitor by a real window move (Windows sends the DPI change). After Hide, even after a
full second of idle, grid column 0's bbox was **9 px** while the translation pane stayed
at **583**; a 1 px resize made it 851/843. On the laptop the same steps re-flowed at once.
The same result on 26.5.23, so it predates §7's fix.

### The cause, isolated in plain tkinter

No CustomTkinter, same result on either monitor. When a slave is added or removed, Tk
8.6's grid asks the master for its new requested size and re-arranges on the next idle
pass — **but only if that requested size is more than 1 px in both directions**. At 1 px
it returns and waits for a `<Configure>` that only a resize sends:

| Pane request | Master request after Hide | Result |
|---|---|---|
| w=1 h=1 padx=8 | (9, 1) | **stale** |
| w=1 h=2 padx=8 | (9, 2) | re-flows |
| w=1 h=5 padx=0 | (1, 5) | **stale** |
| w=2 h=5 padx=0 | (2, 5) | re-flows |

`_build_content_column` builds each reading pane as a 1×1 `CTkFrame` with
`grid_propagate(False)`, so the weights alone decide the 70/30 split. At 150 %
CustomTkinter scales 1 px to 2 and the grid re-flows — which is why the laptop never
failed. At 100 % it stays 1 px, `content_wrapper` requests (9, 1), and every Hide and
Show is left un-arranged until a resize.

That is very likely the reporter's original symptom too — every pane widget `mapped:0`
right after Show, and a resize fixing it — and it is why the reassert guard of §5 fired
on every Show there. Cause A's fix (91d) re-grids an unmapped wrapper, which does not
touch this.

### Fix (26.5.25)

`CONTENT_COLUMN_SEED = 4`: each pane requests 4 design px instead of 1, which stays at
least 2 device px down to CustomTkinter's 0.4 scaling floor. Still tiny, so the weights
still decide the split.

On the physical 100 % monitor, after a real move and with no resize anywhere:

| | Before | After |
|---|---|---|
| Hide | translation 583 of 850 px | **842** |
| Show | reassert guard fired | 582 / 253 (70/30), guard silent |
| Hide again | 584 | **843** |

Started directly on that monitor: the same. Tests:
`tests/test_reading_grid_reflows_at_100_percent_scaling.py` — Hide in columns and in the
stacked layout fail before the fix (583 of 850; 315 of 565 px); guards hold the 70/30
split at 900/1200/1400 px and the laptop's behaviour.
