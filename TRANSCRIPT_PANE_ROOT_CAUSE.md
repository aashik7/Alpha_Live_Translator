# The transcript pane bug: root cause, fix, and why it took five attempts

**Status: CLOSED.** Fixed in `61aa5cb` and `c0bda58`. Verified on the repo build
and on the installed build, at widths from 400 to 1920 design px.

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

`TRANSCRIPT_PANE_REASSERTED` still appears on every Show in a live run, meaning
`_apply_content_layout` does not leave the column mapped on the first attempt
and the guard is carrying it. The result is correct and lands in the same frame,
so this is not user-visible — but the normal path relying on its safety net is a
loose thread worth pulling when there is time.

It was not reproducible in isolation: calling `_apply_content_layout` followed
by `update_idletasks()` maps the column every time outside the live app.

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
