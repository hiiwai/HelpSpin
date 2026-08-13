# Changelog

All notable changes to HelSpin are recorded here. Versions follow
[Semantic Versioning](https://semver.org/). The project is pre-1.0 and
pre-rendering: the spectrum rendering engine is not implemented yet, so
these are early releases, not a finished tool.

**Versioning convention:** the version in `pyproject.toml` is the single
source of truth; `helspin --version`, the delivered zip's filename, and this
file must always agree with it. **Every delivery bumps the version** so any
build can be traced back to a specific entry here.

- **patch** (`0.0.X`) — bug fixes and incremental improvements at the current
  pre-rendering stage.
- **minor** (`0.X.0`) — reserved for `0.1.0` = spectrum rendering working
  (the first genuinely usable release), then each substantial feature after.
- **major** (`X.0.0`) — a build ready to hand to others as finished.

> Earlier snapshots used a `0.1.0.devN` suffix. That was dropped in favour of
> plain `0.0.X` patch numbers, which are more honest about the project being
> pre-rendering — `0.1.0` (no suffix) is now reserved for when rendering
> works. `0.0.1` corresponds to what was previously built as `0.1.0.dev1`.

## [0.0.5] — 2026-08-02

Plot usability pass, addressing eight reported issues.

### Fixed
- **Y scale had no visible effect.** matplotlib autoscales the y-axis, so
  multiplying a trace's intensity simply grew the axis to match and the
  picture looked identical. Y limits are now pinned once established, so
  scaling actually changes what you see. "Reset Y" recomputes them.
- **The ppm range would not accept "0 to 12".** Typing the range in ascending
  order was rejected outright, which just looked broken. Either order is now
  accepted and normalised to the descending order the ppm axis uses, with the
  boxes updated to match what was applied.
- **Datasets that cannot be displayed are no longer listed.** The browser
  showed every expno, including ones with no processed data, which then failed
  with "cannot find" on drop. Expnos are now filtered on the presence of an
  actual processed-data file (`1r` for 1D, `2rr` for 2D) at the source.

### Added
- **Spectrum name drawn on the plot**, at each trace's own top-left in its own
  colour, so it travels with its spectrum instead of sitting in a legend.
- **Crosshair cursor** with a live ppm / intensity readout in the status bar.
- **Drag to reposition** the selected spectrum vertically in stacked mode.
  Deliberately inert in overlay, where which trace a drag refers to would be
  ambiguous.
- **Line style per spectrum** — solid, dashed, dotted, dash-dot — alongside
  the existing per-spectrum colour.
- **Grid toggle**, deliberately faint (alpha 0.25) so it reads as an aid
  rather than competing with the peaks.

### Known limitations
- 2D spectra still are not displayed; dropping one says so.
- Preferences are not persisted between runs.

## [0.0.4] — 2026-08-02

Usability pass on the spectrum canvas: spectra are identifiable, individually
scalable, and the appearance is configurable.

### Fixed
- **Legend showed a bare expno number** ("1", "30") with no sample name, which
  is useless when comparing — every sample has an expno 1. Labels are now
  "<sample>/<expno>", and the drag payload also carries the sample name, pulse
  program, and nucleus.
- **The ppm range boxes started at 0.000/0.000.** Typing into them before
  anything was loaded produced a degenerate near-zero-width view. They are now
  seeded from the loaded spectra's actual ppm span the first time data arrives.
- **Hiding every spectrum crashed the redraw** (`max()` on an empty sequence).
  The empty-state guard checked whether any traces existed rather than whether
  any were *visible*. Now both "nothing loaded" and "all hidden" render an
  empty canvas with an appropriate message.

### Added
- **Per-spectrum vertical scaling.** Select a spectrum and scroll the wheel
  over the plot, or type an exact value in the Y scale box. Scaling is
  per-trace, so a weak spectrum can be brought up without touching the others.
  Refuses zero, negative, and non-finite values, and is clamped so a fast
  scroll cannot drive it to zero or overflow.
- **Loaded-spectra panel** on the right: select which spectrum the scale
  controls act on, toggle visibility, change an individual colour, or remove
  it. The selected trace is drawn thicker so it is obvious which one is active.
- **Real Preferences dialog** — line width and the colour cycle, applied to
  spectra already drawn as well as new ones. Replaces the previous honest
  stub. Cancel genuinely changes nothing.
- **Per-trace y offset** (`set_y_offset` / `nudge_y_offset`) for moving a
  single trace vertically.

### Changed
- **One arrangement toggle instead of two buttons.** Its label always names
  the current mode (Overlay / Stacked) and the tooltip says what a click will
  do, rather than two checkable buttons where both could look unset.
- **Clear is now a true reset** — traces, selection, ppm range, and the range
  boxes. Leaving a stale range behind made the next drop appear on a
  nonsensical axis.
- **Stacked spacing accounts for scaling**, so a scaled-up trace no longer
  overlaps its neighbour.

### Known limitations
- 2D spectra still are not displayed (contour rendering to come); dropping one
  says so rather than failing silently.
- Preferences are not yet persisted between runs.

## [0.0.3] — 2026-08-02

**Spectra are now actually displayed.** The layout-first flow (define a figure
with N slots, then fill them) is gone: drag one or more datasets straight onto
the canvas and they are loaded and drawn. Arrangement is a toggle you flip
afterwards, not a decision forced up front.

### Fixed
- **REGRESSION from 0.0.2: samples could not be expanded at all.** Making
  `canFetchMore` always return False removed the expander arrows entirely --
  `QSortFilterProxyModel` derives `hasChildren()` from `rowCount()` when
  `canFetchMore()` is False, so every unexpanded sample looked childless.
  `canFetchMore` now correctly reports True for unloaded nodes; the
  GUI-thread-blocking concern it was meant to solve is handled in `fetchMore`
  instead, which delegates the scan to the async populator (and therefore
  still schedules the metadata probes that make rows draggable).

### Added
- **Real spectrum rendering.** `read_1d` / `read_2d` are implemented against
  nmrglue's processed-data reader (pdata -- what TopSpin shows), with the ppm
  calibration rebuilt into the domain's `AxisCalibration`.
- **`SpectrumCanvas`**: a matplotlib canvas that accepts dataset drops
  directly and plots them. Loading runs on a worker thread, so dropping
  several spectra does not block on the slowest one, and each appears as it
  arrives.
- **Overlay / Stacked toggle** and **Clear** on the toolbar; the ppm range
  controls are live from the start rather than gated behind creating a figure.
- Load failures are reported in the status bar instead of failing silently.
  Dropping a 2D dataset says plainly that 2D display is not implemented yet.

### Removed
- The **New Figure** dialog and the slot/box layout flow are no longer wired
  into the app. The modules remain in the tree for a future "more control"
  mode, but the default path is now just: drag spectra on, look at them.

### Known limitations
- **2D spectra are not displayed yet** (contour rendering is still to come);
  dropping one reports this rather than silently ignoring it.
- Export, undo, and the TopSpin bridge remain unbuilt.

## [0.0.2] — 2026-08-02

Bug-fix release. The instant-expansion feature added in 0.0.1 had a real
defect in the actual GUI (as opposed to the tests): metadata columns never
filled in, and datasets could not be dragged. Both are fixed here, along with
refresh no longer collapsing the tree. Still no spectrum **rendering** — that
remains the trigger for `0.1.0`.

### Fixed
- **Metadata columns stuck on "…" and drag not working.** Two compounding
  causes, both real:
  - Qt calls `fetchMore` synchronously the moment a node is expanded. That
    synchronous fetch populated the expno rows and marked the node fetched
    *before* the browser could schedule the background metadata probes — so
    the probes were never scheduled. Rows appeared but stayed unprobed, their
    columns stuck on the "…" placeholder, and because an unprobed row has
    unknown dimensionality it was not draggable. Fixed by driving all
    population explicitly through the async populator (`canFetchMore` is now
    always False) so expansion always schedules probes.
  - The background probe worker mutated the model and emitted `dataChanged`
    from the worker thread, which Qt item models forbid. Split into a
    pure-I/O half (safe on the worker) and an apply half (GUI thread only).
    Also fixed the probe task's result signal never being connected, and the
    task being garbage-collected mid-flight.
- **Refresh collapsed the whole tree.** Refresh now merges in place — adding
  new files, removing vanished ones, and keeping existing node objects — so
  expanded folders stay open and already-loaded metadata is not thrown away.
  Previously it cleared and rebuilt, collapsing everything, and (via a
  separate `_index_for` bug) refreshing a data root silently did nothing.

### Notes
- The empty-looking behaviour was GUI-only; the existing tests passed because
  they populated via a code path that bypassed Qt's synchronous fetch. A new
  regression test expands *through the tree*, the way a user does, and would
  fail without this fix (verified by reverting the fix — the PULPROG column
  stays "…").

## [0.0.1] — 2026-08-02

First release under the plain (non-`devN`) scheme. Browser-and-layout tool;
no spectrum **rendering** yet (a filled slot shows its colour and label, not
a plotted trace) — that remains the next milestone and the trigger for
`0.1.0`.

### Added
- **Resizable Name column.** The Name column is now user-widenable, fixing
  long Bruker sample names being clipped with no way to see them. Metadata
  columns size sensibly and `stretchLastSection` is off so Date does not
  re-squeeze Name.
- **Quit on the toolbar.** Quit is now an explicit toolbar button (far
  right) as well as in the File menu — on macOS a menu action literally
  named "Quit" gets auto-relocated into the system application menu, which
  made it look missing.
- **`CHANGELOG.md`** (this file) and **version-stamped zip filenames**
  (`helspin-<version>.zip`) so deliveries are traceable.

### Changed
- **Instant sample expansion.** Expanding a sample no longer reads every
  expno's `acqus` file up front. Rows appear from directory structure alone
  (zero file reads); PULPROG/nucleus/dimensionality/date columns fill in via
  background per-row probes. Measured ~2400x faster to first paint than the
  old probe-then-show path on a simulated 50 ms/read network share (30
  expnos: ~1.5 s to first row -> ~0.6 ms).
- **Faster drag start.** Dragging a dataset from the browser now uses a
  small custom drag pixmap instead of Qt's default full-row render, which
  re-rendered every column in the native style before the drag could begin.

### Fixed
- The browser's left/centre splitter inverted itself after a figure was
  created (handed most of the window to the browser); it now preserves the
  intended proportions.
- An emptiness check that used `mime.formats()` (always truthy, because the
  model always sets the format) is now a decoded-payload check, so a
  non-draggable selection no longer starts an empty drag.

## Baseline (pre-0.0.1)

Initial development, before the per-delivery version convention. Established
the core of the application.

### Working
- **Dataset browser** — data root -> sample -> expno tree over Bruker data,
  async population, type-to-filter (sample name / PULPROG).
- **Explicit refresh** — right-click a node, "Refresh All", or F5. No
  automatic file-watching (unreliable over SMB/NFS shares).
- **Application shell** — toolbar, browser (left), canvas (centre), ppm
  adjustment bar (bottom).
- **New Figure dialog** — 1D/2D counts and arrangement (Overlay / Stacked /
  Tiled / Subtracted for 1D; Overlay / Tiled for 2D) with live validation;
  generates an empty, colour-assigned slot layout.
- **Drag-and-drop** — drag one or more expnos onto slots; multi-select fills
  sequential slots.
- `helspin --version` prints the version without needing a display.

### Notable fixes during early development
See the "Bugs the tests (or a genuinely fresh install) caught" section of
`README.md` for the full list — including two real test-suite hangs from
blocking Qt modals, a `numpy<2.5` pin (nmrglue crash on fresh install), a
`pyproject.toml` entry point that referenced a non-existent module, and
`load_data_roots` crashing on malformed settings.

### Not yet implemented (as of 0.0.1)
- Spectrum **rendering** (`read_1d`/`read_2d`, matplotlib canvas) — the next
  milestone, and the trigger for version 0.1.0.
- Preferences dialog (honest stub), paste, export (SVG/EPS/PS/TIFF/JPEG),
  undo/redo, the TopSpin bridge, 2D contour rendering.
