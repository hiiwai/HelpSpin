# HelSpin — engineering handoff

Version at time of writing: **0.5.0**. Read this before changing anything;
several decisions below were arrived at by getting them wrong first.

---

## 1. What this is

A cross-platform (macOS/Windows) PySide6 desktop app for comparing Bruker NMR
spectra and producing publication figures — a focused replacement for doing
that in TopSpin.

Current shape: **drag spectra from the browser straight onto the canvas.**
There is no "define a figure layout first" step; that design existed up to
0.0.2 and was removed as too indirect. Arrangement (overlay/stacked) is a
control you flip afterwards.

---

## 2. Running it

```bash
conda create -n helspin python=3.11 && conda activate helspin
pip install -e ".[dev]"
helspin              # launch
helspin --version    # prints version, needs no display
pytest               # 734 tests
```

**Version discipline:** `pyproject.toml` is the single source of truth.
`helspin --version`, the delivered zip filename, and `CHANGELOG.md` must all
agree. **Every delivery bumps the version.**

---

## 3. Architecture

```
helspin/
  domain/          pure numpy/stdlib, no Qt. AST-enforced by test_architecture.py
    spectrum.py    AxisCalibration (ppm math), Spectrum1D/2D
    project.py     slots/blocks/boxes, DEFAULT_PALETTE (Okabe-Ito)
    paths.py       Bruker path detection, expnos_with_data(), has_processed_data()
    layout.py      NewFigureRequest -> Project  (LEGACY: not wired into the app)
    overlay.py     y-scaling modes, difference computation
    metadata.py    JCAMP stripping, barcode/title reading
  infrastructure/
    nmrglue_reader.py   probe() (cheap acqus), read_1d()/read_2d() (pdata)
  core/
    settings.py    QSettings persistence: data roots + slot styles
  ui/
    dataset_model.py     lazy tree model, async population, deferred probes
    dataset_filter.py    type-to-filter proxy
    browser.py           tree widget, drag source, refresh
    spectrum_canvas.py   THE MAIN CANVAS: matplotlib, drop target, all plotting
    spectrum_list_panel.py   loaded-spectra list: select/scale/offset/remove
    preferences_dialog.py    8 slots x (colour, line style, line width)
    adjustment_bar.py    ppm range with explicit Apply
  __main__.py      MainWindow: toolbar + browser | canvas | list panel
```

**Legacy, still present but NOT wired in:** `domain/layout.py`,
`ui/box_canvas.py`, `ui/new_figure_dialog.py`, `ui/canvas_placeholder.py`.
Kept for a possible future "precise layout" mode. Do not assume they run.

---

## 4. Hard-won decisions — do not undo without reading why

### Qt threading and model behaviour
- **`canFetchMore` MUST return True for unfetched nodes.**
  `QSortFilterProxyModel` derives `hasChildren()` from `rowCount()` when
  `canFetchMore()` is False. Returning False removed every expander arrow and
  made the tree unopenable (shipped broken in 0.0.2). The GUI-blocking concern
  is handled in `fetchMore`, which delegates to the async populator via
  `set_fetch_scheduler`.
- **Never mutate the model or emit `dataChanged` from a worker thread.** Qt
  item models are not thread-safe. Probing is split: `read_probe_result()` is
  pure I/O (worker-safe), `apply_probe_result()` mutates and signals (GUI
  thread only).
- **Hold a Python reference to in-flight `QRunnable`s.** `QThreadPool` does
  not, and a task carrying a result signal can be garbage-collected mid-flight,
  silently losing the result.
- **The browser reads from `core/dataset_index.py`, not the filesystem.**
  Cached to `~/.cache/helspin` (`%LOCALAPPDATA%\HelSpin\cache` on Windows;
  override with `HELSPIN_CACHE_DIR`). Tests MUST set `HELSPIN_CACHE_DIR`
  (conftest does this automatically) or they will read and write the
  developer's real cache.
- **The index has THREE TIERS and they must stay separate.** Discovery (which
  directories are samples) → detail (one sample's experiments) → metadata
  (PULPROG etc). 0.3.0 did all three in one bulk walk, which cost a round trip
  per EXPERIMENT before the first row could be drawn: ~9600 of them on a real
  root, i.e. minutes of blank tree. Anything that walks into experiment
  directories during discovery re-creates that bug.
- **Discovery streams.** `on_batch` puts rows on screen while the walk runs.
  Collecting the whole list first is the same multi-minute blank tree with
  extra steps.
- **A row is draggable before its metadata is read.** Structural flags from
  the listing give the dimensionality. Requiring the probe first is what "I
  cannot drag" was. Unknown (processed-only) reports 0 and `read_auto`
  settles it at load time.
- **`probe_row`, not `probe`, for browser rows.** One read versus nine round
  trips. `test_browser_speed.py` asserts the count; if it starts failing,
  something has added a stat back.
- **Three queues, never one.** Interactive (expansions, visible rows),
  background (the indexer), and the global pool (canvas loads). Sharing one
  put a drop behind hundreds of directory listings.
- **Every QRunnable sets `setAutoDelete(False)`.** Qt deletes a runnable when
  `run()` returns; we hold Python references to in-flight tasks, so leaving
  the default is a double free — a segfault with no traceback, whenever a
  close races real work. This bit both the browser and the canvas.
- **Populators are held strongly until shut down** (`_LIVE_POPULATORS`) and
  `shutdown()` drains the pools. Without it the collector frees Node objects
  while a worker is still reading their paths. Tests get this from an autouse
  conftest fixture; the app gets it from the browser's and main window's
  `closeEvent`.
- **Do NOT connect to the model's `destroyed` signal** to detect teardown. It
  segfaults on PySide6 6.11, and it is unnecessary: the populator holds a
  strong reference to the model, so the model cannot die first.
- **A filtered sample must still open.** Qt's recursive filtering propagates
  matches upwards only; the children of a matched row are still judged on
  their own, so filtering by sample name hid every experiment under it. See
  `ui/dataset_filter.py` — an experiment whose sample matched BY NAME is
  always shown.
- **Sample listing uses `scan_for_samples`, NOT `scan_for_datasets`.** A
  directory with any integer-named child is a sample; the walk stops there.
  Enumerating expnos to find their parents cost two stats per experiment and,
  with an expno-level cap, hid most samples (400-sample root showed 25). Use
  `os.scandir` -- its DirEntry carries the type flag with no extra stat.
- **Expansion is instant by design:** `_scan_sample` lists directory structure
  only (zero `acqus` reads); metadata fills in via per-row background probes.
  Measured ~2400x faster to first paint on a simulated 50 ms/read share.
- **Refresh MERGES in place** — adds new, removes vanished, keeps existing node
  objects. Clearing and rebuilding collapsed every expanded folder.
- **Refresh is explicit, never automatic.** `inotify`/`FSEvents` are unreliable
  over the SMB/NFS shares Bruker data lives on.
- **`_index_for` must return a real index for data-root nodes.** They have no
  parent Node but are NOT the invisible root; returning `QModelIndex()` made
  refreshing a data root silently do nothing.

### Vertical scale comes from the VISIBLE window
- **Every vertical calculation goes through `_window_values()`.** Frames
  (overlay and stacked), stack lane height, bottoming floor, and
  `fit_to_drawn` all measure only the data inside the current ppm range. A 19F
  spectrum spans -29 to -180 ppm and is read over a 10 ppm slice: a peak
  outside the view once set the scale for everything and compressed the region
  of interest to 0.9% of the canvas.
- **Changing the ppm range clears `_y_limits`.** The vertical frame is derived
  from the data in view, so a new horizontal window needs a new vertical one.

### Crosshair
- **Both lines carry their value**, x above the frame and y at the right edge.
- **RIGHT_MARGIN is reserved in tight_layout** for the y label. It is drawn
  outside the axes and tight_layout runs before it exists, so without the
  reservation matplotlib clips it off the figure. Assert with
  `get_window_extent`, not by eye.

### Undo/redo
- **Bursts must coalesce.** A spin box fires valueChanged per keystroke;
  without a coalescing key, typing one value makes five undo steps and undo
  looks broken. Pass a key to push_undo for anything a control can emit
  repeatedly.
- **Opening a session clears the history.** Otherwise Ctrl+Z swaps the
  restored document for whatever preceded it.
- **Snapshots hold trace objects by REFERENCE and copy only display fields.**
  Never reuse session_state()/restore_session() for undo: it re-reads every
  file, so undoing a scale change would cost a round trip per spectrum.
- **Snapshot before the mutation, and only after the guards** -- a rejected
  call must not leave an empty step on the stack.
- remove_trace snapshots before removing so the discarded Trace stays alive in
  the stack; that is what makes undo instant and data-exact.

### Zoom and axis orientation
- **F1's low ppm is at the TOP** (`set_ylim(high, low)`). The "top" box holds
  the low value; `f1_range()` returns `(high, low)` for the canvas. Renaming
  the labels without moving the values inverted the meaning once already.
- **Any zoom done on the plot must emit `viewChanged`**, or the range boxes
  keep showing a range that is no longer displayed and the next Apply jumps
  the view back.
- **Zoom is a toggle, not a modifier.** Wheel-to-scale and wheel-to-zoom are
  both wanted often; a held key while scrolling is awkward.

### 2D arrangement
- **Overlay superimposes, Stacked puts maps side by side.** Both drew panels
  until 0.4.9, which made superimposing impossible -- and superimposing is how
  chemical-shift perturbation is read. Do not "simplify" this back.
- Superimposed maps carry corner labels in their trace colour; panels carry
  titles. A 1D trace always gets its own panel (intensity cannot share a ppm
  vertical axis).

### Stacked mode
- **The lane step is REMEMBERED (`_stack_step`), not recomputed per redraw.**
  Deriving it from the tallest scaled span each time meant scaling one
  spectrum re-laid the whole stack: the middle of three at x20 squashed its
  neighbours from 29% of the canvas to 2%. Scaling must leave neighbours
  untouched and let the scaled trace clip.
- **`_stack_step` is cleared wherever `_y_limits` is**, plus in fit_to_drawn
  (which re-lays then fits, in that order). They describe one layout.
- **Frame stacked mode from the DRAWN positions**, never from raw data. The
  lane spacing is itself a scaled quantity, so mixing it with a raw envelope
  puts the frame and the traces on different scales and a spectrum vanishes.
  Overlay keeps the raw-data frame (so scaling stays visible); stacked cannot.
- **"To bottom" needs no stacked special case.** The drawing adds
  `offset_step * i`, so `y_offset = anchor - floor` already lands each trace on
  its own lane floor. Adding a case would double-count the step.
- **Scaling in stacked mode changes the LAYOUT** (lane height is the tallest
  scaled span), so `_y_limits` must be cleared on scale change there.
- **Changing arrangement must `fit_to_drawn()`.** Offsets that suit a stack do
  not suit an overlay, and vice versa.

### Intensity is not comparable by default
- **NS and RG must travel with the data.** They only exist once the spectrum
  has been read -- the drag payload cannot know them -- so the load signal
  carries them. They were parsed and stored for three versions and dropped at
  the worker boundary, which is why `acquisition_scale()` was never callable.
- **Raw intensity comparison compares the ACQUISITION.** NS 16/RG 101 vs
  NS 512/RG 2050 is ~640x. NS and RG are shown in the list tooltip so the
  reason for a height difference is visible.
- **A "Match NS-RG" button existed in 0.4.5 and was removed.** It overwrote
  y_scale, so it destroyed manual scaling, and for spectra from one series
  (identical NS/RG) it did nothing at all. If reinstated, multiply the
  existing scale rather than replacing it, and only when the values differ.
- NC_proc IS handled (nmrglue `scale_data=True`, verified: ratio 1.000 across
  NC_proc 0 vs 8). If `procs` is unparseable nmrglue silently returns unscaled
  data -- a 2^NC_proc error -- and that warning is not yet surfaced.

### Vertical positioning
- **Spin-box ranges must cover real NMR magnitudes.** Intensities of 1e12 are
  ordinary. A spin box does not reject an out-of-range value, it DISPLAYS the
  clamped one -- so a too-narrow range shows a number that is not the one in
  force, and the next click applies the lie. The offset range is sized from
  the data; the scale range matches MIN/MAX_Y_SCALE.
- **Anchor "to bottom" to the RAW-data frame, never the current axis.**
  Anchoring to the axis makes each press move the trace to wherever the last
  press left the frame: the spectrum creeps downwards, and a loop over traces
  gives each one a different offset.
- **Bottom-all computes ONE anchor and applies it to every trace**, then
  redraws once. Looping over the single-trace version refits between traces.
- **Baselines come from the visible ppm window, with y_scale applied.** The
  global minimum can be an artefact hundreds of ppm outside what is displayed.
- **Finish with `fit_to_drawn()`.** The frame is derived from raw data, so a
  scaled-up trace lands correctly and is still off-screen without it.

### Sessions
- **A session stores paths and RECIPES, never arrays.** A difference has no
  file behind it, so it stores its two source paths, the operator, and the
  y-scales the sources had AT THE TIME. Re-deriving with the sources' current
  scales gives a different spectrum from the one that was saved.
- **`combine_arrays()` is the single implementation** of the interpolate-then-
  combine maths, shared by Subtract/Add and by restore. Two copies would drift
  and the symptom would be a session that reopens subtly wrong.
- **Restore fills a slot per saved entry, then compacts.** Appending as it
  goes reorders the traces, because a difference need not sit below its
  sources once Bottom All has been used.

### Reporting failures to the user
- **The cursor readout must never use `showMessage()`.** It fires on every
  mouse move over the plot, so it shares the status bar's message slot with
  every warning and wipes them within milliseconds. Failures then read as "the
  app does nothing", with a clean terminal. It has its own permanent widget;
  keep it that way.
- **Check EVERY procno, never just pdata/1.** An STD difference writes its
  on/off/difference results into separate procnos, so an empty pdata/1 says
  nothing about whether the experiment has data. Stopping there marked those
  datasets unusable.
- **"Could not read" is not "nothing there".** `inspect_processed` is
  tri-state (True/False/**None**) precisely because a cached False from one
  share hiccup greys a good dataset out permanently.
- **Never carry `has_processed` across a refresh.** Processing writes inside
  `<expno>/pdata`, which does not change the sample directory mtime, so the
  refresh is the ONLY thing that can revisit the verdict.
- **Dimming warns; it must not block.** The check has been wrong three
  different ways already. A dimmed row stays draggable and the load attempt is
  the final word.
- **`helspin --check <path>`** reports what is actually on disk, per
  experiment. Reach for it before arguing with a user about their data -- it
  found the non-default-procno bug on its first run.
- **`pdata` existing is NOT a spectrum.** An acquired-but-unprocessed
  experiment has `pdata/1` with no `1r`. `ExpnoEntry.has_processed` records
  the real answer, checked in the BACKGROUND pass only. Rows that fail it are
  dimmed and undraggable, not hidden -- an experiment vanishing is more
  confusing than one that explains itself.
- **A failed drop marks its browser row.** `mark_expno_failed`. A message
  alone is not feedback if the user has already looked away.

### Start-up cost is imports, not I/O
- **Never import nmrglue at module level.** ~0.8 s, and it pulls scipy in
  behind it. Nothing on the start-up or browsing path needs it --
  `read_acqus_fast` is stdlib. `test_reported_issues_040.py` asserts neither
  nmrglue nor scipy is in `sys.modules` after importing the main window.

### Qt event handling
- **Never assign `widget.wheelEvent = fn` on an instance.** Qt dispatches
  virtuals through the class, so it is ignored — AND it shadows the backend's
  own handler, which killed matplotlib's `scroll_event` too. Wheel scaling was
  dead from 0.0.4 to 0.0.6 because of this. Use `mpl_connect("scroll_event")`.
- `_PlotCanvas` subclasses `FigureCanvasQTAgg` purely to guarantee
  `setMouseTracking(True)` so motion events arrive without a button held.
- **Never call a real modal `exec()` in a test.** `QDialog.exec`,
  `QMenu.exec`, `QDrag.exec` all block forever and hang the suite (happened
  twice). Split construction from showing: test `_build_context_menu`,
  `_build_drag`, `dialog.request()` directly.

### Plotting
- **The vertical frame comes from RAW intensities** (`_frame_y_limits`),
  deliberately ignoring each trace's `y_scale`/`y_offset`. Those are
  adjustments made *within* the frame; folding them into the frame would
  cancel out the very effect they exist to produce.
- **Y limits are re-fitted whenever the SET of traces changes** (add, remove,
  arrangement switch) but held fixed during scale/offset edits. Pinning them
  at first load (0.0.5-0.0.6) made a later, weaker spectrum render as a flat
  line at zero.
- **Y-scale limits span 1e-9..1e9.** A 1000x ceiling silently clamped
  autoscale: a 5 mM reference beside a 50 uM sample is ~1000x on its own, so
  a tighter range makes strong/weak pairs impossible to compare.
- **`autoscale_traces()` scales each spectrum individually** to a common
  fraction of the frame. Re-fitting the frame alone cannot rescue a weak
  spectrum, because the frame must still fit the strong one.
- **Toggling visibility must NOT re-fit the frame.** Hiding is a viewing
  action; refitting made everything jump scale on untick and did not restore
  on re-tick.
- **The grid is X-only.** A horizontal grid cuts across every stacked spectrum
  and reads as part of the data.
- **A setting read from a dialog is not a setting applied.** Three
  preferences shipped doing nothing because the handler never called the
  setters. Always drive a preference end to end (open, accept, assert the
  plot changed), not just the dialog's getter.
- **ppm order: the BOXES follow the axis (left = higher ppm); the recent LIST
  is range notation (low -> high).** Boxes, recent list and plot must
  all agree. Mixed orders across components is what repeatedly read as
  "reversed". Either typing order is accepted, then normalised.
- **Never emit `tracesChanged` from `select_trace`.** It rebuilds the spectra
  list, which wipes a multi-selection and silently breaks Subtract/Add.
- **2D traces carry EMPTY ppm/intensity arrays.** Their data is in
  matrix/ppm_f1/ppm_f2. Any 1D-only routine MUST filter them out via
  `_visible_1d()` -- `np.nanmax` on an empty array raises "zero-size array to
  reduction operation", which crashed the app on every 2D drop in 0.1.0-0.1.2.
- **Trace labels are in axes-FRACTION coordinates.** Anchoring them to each
  trace's data maximum made them move when a spectrum was scaled.
- **ppm axes descend** (high ppm on the left) everywhere.
- **`Full` range is the UNION** of all traces, not the intersection.
- **The ppm range applies only on `Apply`.** Applying on each box's
  `editingFinished` combined the newly typed value with the stale value in the
  other box, so a two-value range could never be entered cleanly.
- `loc="best"` and `bbox_inches="tight"` are **banned** — they silently change
  output. Enforced by `test_architecture.py`.

### Data
- **Only expnos with real processed data are listed** (`1r` for 1D, `2rr` for
  2D). Listing raw-only expnos produced "cannot find" errors on drop.
- **`read_1d`/`read_2d` read `pdata`, not the FID** — that is what TopSpin
  displays and what a figure should show.
- **Coerce every acqus value to `str`.** nmrglue parses `##$HOLDER= 5` as a
  Python `int`.
- **`numpy<2.5` is pinned.** numpy 2.5 hard-crashes nmrglue's (unused)
  `tecmag.py` on import. Only surfaces on a genuinely fresh install.

---

## 5. Known bugs / limitations (as of 0.0.7)

| Issue | Status |
|---|---|
| 2D spectra | **Done** (0.1.0): contour panels, side by side with shared axes. Overlaying contour maps is deliberately not offered -- it is unreadable. |
| Export | **Done** (0.0.9): right-click the spectrum. Vector formats keep editable text. |
| Undo/redo | Not implemented. |
| Sessions | **Done** (0.2.0); differences re-derived from a stored recipe since 0.4.2. `.helspin` files store paths + view state, not arrays. |
| TopSpin `xcpy` bridge | Not implemented. |
| Difference spectra | **Done** (0.1.1): select two, press Subtract. Interpolates onto a common ppm axis. |
| PULPROG filter | **Done** (0.4.0): the background index reaches samples that were never expanded. |
| Preferences persistence | Slot styles persist; window geometry and ppm range do not. |
| Pseudo-2D | Not handled. |

### Repeated failure modes in this project — read before "fixing" anything
1. **Verify in the real GUI, not only in tests.** Several bugs (probes never
   scheduled, wheel never firing) passed the test suite because the tests
   drove code paths the GUI does not use. When testing a UI behaviour, drive
   it the way a user does (`tree.expand()`, real `QWheelEvent`), not via
   internal helpers.
2. **Check `pixmap.save()` return values** and never truncate diagnostic
   output — both produced false conclusions here.
3. **Test-harness mistakes have masqueraded as app bugs.** A `QSettings` mock
   that generated a fresh temp file per call made saves and loads hit
   different files, and looked exactly like a broken app.
4. **Run the suite under a hard `timeout` with output redirected to a file.**
   Modal-dialog hangs are a proven risk.

---

## 6. Delivery checklist

```bash
# 1. bump version in pyproject.toml, add a CHANGELOG entry
# 2. clean, sync, verify
find . -name "__pycache__" -type d -exec rm -rf {} +
rm -rf .pytest_cache .coverage build helspin.egg-info dist .venv *.png
timeout 200 python3 -m pytest tests/          # must be green
# 3. zip, excluding caches
zip -r -q "helspin-<version>.zip" helspin \
  -x "*.pytest_cache*" -x "*__pycache__*" -x "*.png" -x "*.pyc" -x "*.venv*"
# 4. VERIFY FROM THE ZIP, not the working copy
unzip -q helspin-<version>.zip && cd helspin
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/helspin --version                    # must match the filename
QT_QPA_PLATFORM=offscreen .venv/bin/pytest     # must be green
```

**Troubleshooting the user hits repeatedly:**
- `QT_QPA_PLATFORM=offscreen` left set in a shell → window silently never
  appears. `unset` it.
- Nested conda+venv (`(helspin) ((.venv))`) → `which python` and
  `which helspin` must point into the same environment.
- On macOS a Python-launched window can open *behind* everything; `main()`
  calls `raise_()`/`activateWindow()` for this.
- The terminal not returning after `helspin` is normal — `app.exec()` blocks.

---

## 7. Suggested next steps, in order

1. **2D contour rendering** — `read_2d` already returns a `Spectrum2D`. Needs a
   contour draw path in `spectrum_canvas.py` and a sensible default level set.
   This unlocks `0.1.0`.
2. **Export** — SVG/EPS/PS/TIFF/JPEG via matplotlib, honouring the banned-args
   rule above.
3. **Persist window geometry and the last ppm range** alongside slot styles.
4. ~~Background index~~ — done in 0.4.0.
5. **Sort by column** in the browser (date especially); the index now holds
   the values, so it is a proxy-level change.
6. **Cancel an in-flight drop.** A 2D load of a large matrix is still
   uninterruptible once started.
