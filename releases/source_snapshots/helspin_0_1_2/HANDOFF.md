# HelSpin — engineering handoff

Version at time of writing: **0.1.2**. Read this before changing anything;
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
pytest               # 625 tests
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
- **The ppm boxes keep exactly what was typed**; the plot interprets them as
  (high, low). Rewriting the boxes under the user was the confusing part.
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
| TopSpin `xcpy` bridge | Not implemented. |
| Difference spectra | **Done** (0.1.1): select two, press Subtract. Interpolates onto a common ppm axis. |
| PULPROG filter | Only matches already-probed rows. A full background index is the documented follow-up. |
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
4. **Background index** so the PULPROG filter reaches unexpanded samples.
