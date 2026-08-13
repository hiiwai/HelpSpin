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

## [0.1.2] — 2026-08-02

### Fixed
- **Recent ranges read backwards.** The stored pair is (high, low) because
  that is the order the descending ppm axis needs, but the list displayed that
  order literally, so "1 to 12 ppm" came back as "12.00 – 1.00". Entries are
  now shown low-to-high, the way a range is read aloud, while the plot keeps
  the NMR convention of high ppm on the left.
- **Spectrum names could not be moved.** Dragging alone was unreliable,
  particularly for a name sitting underneath a trace.

### Added
- **Explicit legend offset per spectrum** (Name x / y in the spectra panel).
  Reproducible, reaches names that are awkward to grab, clamped inside the
  plot, and preserved when the spectrum is rescaled. Dragging still works.
- **Pulse programme in the on-plot name** — two experiments on the same
  sample differ only by pulse programme, which is exactly when a bare name is
  ambiguous.
- **Difference spectra are marked** with a delta and a true minus sign
  ("Δ A − B") so they are unmistakable in the legend, and carry an
  `is_difference` flag.
- **Explicit 1D / 2D canvas mode.** The mode is derived from the data (no
  switch to remember) and shown in the title bar and status line. Dropping a
  2D spectrum onto a 1D canvas -- or the reverse -- is refused with a clear
  message rather than silently accepted: a contour map and a 1D trace share no
  meaningful vertical axis, so the mixed figure would look fine and mean
  nothing.
- **Separate F1 and F2 ranges in 2D**, each normalised to descending.

## [0.1.1] — 2026-08-02

### Fixed
- **Closing the detached explorer wrecked the main window.** `QDockWidget` was
  the wrong tool: a floated dock kept a stale geometry, re-docking left the
  central area not re-laid-out, and the tree could end up drawn over the main
  window. Replaced with explicit re-parenting between the splitter and a plain
  top-level window, which is predictable. Closing that window simply hands the
  explorer back and restores the three-panel proportions.
- **2D datasets could be listed but never opened.** Dimensionality was
  determined only from raw `fid`/`ser` files, which are routinely deleted or
  never copied off the spectrometer. A processed-only 2D dataset therefore
  appeared in the browser (it has `2rr`) and then failed to probe. It now
  falls back to the processed data -- which is what gets displayed anyway.

### Added
- **Difference spectra.** Select exactly two spectra and press **Subtract** to
  add A − B as a new spectrum. The two are INTERPOLATED onto a common ppm axis
  first: subtracting index-by-index is the classic silent error here, because
  two spectra rarely share a point count and the result then compares
  different chemical shifts. Each spectrum's Y scale is applied first, so a
  difference taken after "Same noise" reflects what is on screen.
- **Draggable spectrum names.** Drag a name to move it beside its trace.
  Positions are held in axes fractions, so a dragged name stays where it was
  put when the spectrum is rescaled.

### Note on 2D
There is no separate "2D mode" to switch to, by design: the app reads each
dataset's dimensionality and arranges accordingly. 2D spectra get contour
panels side by side, 1D spectra share a panel, and a mixed selection gets
both.

## [0.1.0] — 2026-08-02

**2D spectra display.** This was the milestone reserved for `0.1.0`: the app
can now show the data it reads, in both dimensionalities.

### Added
- **2D contour display.** Dropping a 2D dataset renders it as a contour map.
  Two or three 2D spectra are placed in **side-by-side panels with shared
  axes**, so zooming one moves all and peaks stay aligned for comparison.
  Overlaying contour maps was deliberately not done -- overlapping them is
  unreadable, which is why NMR software compares 2D spectra in adjacent
  panels.
- Contour levels follow a **geometric ladder from a noise-based floor** (MAD
  estimate). Evenly spaced levels either drown the plot in noise contours or
  show only the tallest peak. The Y scale control doubles as the usual
  "contour level" adjustment.
- **Mixed 1D and 2D** in one figure: each 2D spectrum gets a panel, all 1D
  spectra share one alongside. Hiding every 2D returns to the plain 1D view.
- 2D figures export through the same right-click Save image path.

### Fixed
- **The explorer did not return properly when its window was closed.** Qt
  hides a closed dock by default, so a floating explorer vanished with no
  obvious way back, and clearing the floating flag alone left it mis-sized.
  The dock now intercepts its own close and re-docks, and re-docking removes
  and re-adds it so Qt rebuilds the layout and restores a sane width.
- **EMF removed from the export menu.** matplotlib cannot write EMF, and the
  external-Inkscape route silently produced nothing when Inkscape was absent.
  Offering a format that usually fails is worse than not offering it; SVG, PDF
  and EPS remain and are vector with editable text.

## [0.0.10] — 2026-08-02

### Fixed
- **The explorer could not be re-docked** once floated. The button's label was
  the only record of the state, so dragging the dock out by its title bar left
  label and reality disagreeing and the button then did the wrong thing. State
  is now read from the dock itself. Closing a floating explorer re-docks it
  rather than losing it.
- **Dead space around the plot.** The canvas now has an explicit expanding
  size policy, so it fills the window instead of keeping a stale size after
  the explorer was floated.
- **The cursor crosshair was baked into exported images.** It is a UI overlay
  and is cleared before every save.
- **Yellow was still in the default palette** (slot 5, from the domain
  palette, which the earlier change missed). Removed: it has the lowest
  contrast against white of the Okabe-Ito set and vanishes at publication line
  widths. Order is now by contrast on white, black then strong blue.

### Added
- **"Same noise" button** — scales every spectrum so their NOISE levels match,
  making peak heights directly comparable across different scan counts or
  receiver gain. Noise is estimated by median absolute deviation, not standard
  deviation, because the peaks are outliers and a plain sigma would measure
  signal rather than noise. Declines honestly (with a message) when noise
  cannot be estimated.
- **PowerPoint (.pptx) export** — one slide sized to the figure.
- **EMF export via Inkscape.** matplotlib genuinely cannot write EMF; the
  figure is exported as SVG and converted. Without Inkscape the error says
  exactly that instead of writing a mislabelled file.
- **ppm axis decimal places** in Preferences: automatic, or 1 / 1.0 / 1.00.
- **Spectrum-name size** in Preferences, which also spaces the names further
  apart so a long list stops overlapping the traces.

### Not done in this release
- **2D spectra still are not displayed** (side-by-side, stacked or overlay).
  This is a substantial piece of work and is the next milestone; shipping a
  half-working contour view would be worse than not shipping one.

## [0.0.9] — 2026-08-02

### Added
- **Save image, from a right-click on the spectrum** — PNG, JPEG, TIFF, SVG,
  PDF, EPS and PS. Written exactly as displayed: the tight-bounding-box option
  and any layout change are deliberately avoided, because they silently
  re-crop and re-scale, so the file would not match the canvas.
- **Vector exports keep text editable.** matplotlib converts text to outlines
  by default, which defeats the point of vector output; SVG now carries real
  `<text>` and PDF/PS/EPS embed TrueType, so labels can be edited downstream.
  Background is white by default, with a transparent option.
- **Detachable data explorer.** The browser is now a dock: "Detach Explorer"
  opens it as its own resizable window, so long Bruker sample names are
  readable without squeezing the plot.
- **ppm value shown at the cursor**, drawn on the plot above the crosshair
  rather than only in the status bar.

### Changed
- **Auto Y now fits every spectrum inside the canvas.** It scales each
  spectrum and then frames what is actually drawn, including offsets, in both
  overlay and stacked. Previously a scaled trace could run off the top.
- **Default colours reordered**: slot 2 is now a strong blue rather than
  orange, which was muddy against black both on screen and in print. Yellow
  (#F0E442) removed entirely -- nearly invisible as a thin line on white.

## [0.0.8] — 2026-08-02

### Fixed
- **Y-scale ceiling made strong/weak pairs impossible to compare.** The scale
  was clamped at 1000x, but a 5 mM reference beside a 50 uM sample is ~1000x
  on its own, so autoscale was silently clipped. Range widened to 1e-9..1e9.
- **Unticking a spectrum rescaled everything**, and re-ticking did not restore
  the previous view. Visibility is a viewing action and no longer re-fits the
  frame; only adding/removing spectra or switching arrangement does.
- **The grid drew horizontal lines too.** A horizontal grid cuts across every
  stacked spectrum and reads as part of the data. Now X (ppm) only.
- **The ppm boxes rewrote what you typed.** Enter "0 to 12" and they flipped
  to "12 to 0" under you. The boxes now keep exactly what was entered; the
  plot interprets the pair and always draws high ppm on the left, per NMR
  convention.

### Added
- **Auto Y** — scales each spectrum individually so they are all legible, and
  runs automatically when a second spectrum is dropped. Re-fitting the frame
  alone cannot rescue a weak spectrum, since the frame must fit the strong
  one. Fully adjustable afterwards.
- **Move to bottom**, per spectrum ("To bottom") and for all at once
  ("Bottom All"), putting spectra on a common baseline.
- **Grid spacing in Preferences** — a fixed interval in ppm (e.g. 0.5), or
  Automatic.

## [0.0.7] — 2026-08-02

Regression fixes. Three controls that 0.0.5/0.0.6 claimed to add were in fact
broken in the running application, despite passing tests — the tests drove
code paths the GUI does not use. Root causes and the lesson are recorded in
`HANDOFF.md`.

### Fixed
- **Y scale showed almost no signal.** The vertical range was computed at the
  FIRST drop and never recomputed, so a spectrum loaded afterwards with a much
  smaller intensity was drawn as a flat line at zero against the first one's
  scale. The range is now re-fitted whenever the set of spectra changes
  (autoscale on drop and on removal) while still being held fixed during
  scale/offset edits, which is what keeps those adjustments visible.
- **Scroll-wheel scaling never worked.** `canvas.wheelEvent = fn` was assigned
  on the *instance*; Qt dispatches virtual methods through the class, so it was
  ignored — and it shadowed the backend's own handler, killing matplotlib's
  `scroll_event` as well. Now handled through `scroll_event`. Verified with a
  real `QWheelEvent`, not a direct call.
- **Dragging a spectrum did nothing.** Motion events were not being delivered
  without a button held. The canvas subclass now sets `setMouseTracking(True)`.
  Verified with real `QMouseEvent`s.
- **`Full` did not update the ppm boxes**, leaving them showing a range that
  was not what was on screen.

### Added
- **Global line width in Preferences** — one value plus "Apply to all",
  instead of setting eight boxes by hand. Per-slot widths remain
  individually overridable afterwards.
- **Preferences are saved and reused as the default next run** (colour, line
  style and width for all eight slots), via `QSettings`. A corrupt stored
  value degrades to defaults rather than preventing startup.
- **`HANDOFF.md`** — architecture, hard-won decisions with the reasons, known
  bugs, repeated failure modes, and a delivery checklist, for continuing in a
  fresh session.

## [0.0.6] — 2026-08-02

Corrections to six issues reported against 0.0.5, several of them fixes from
that release that had not actually landed correctly.

### Fixed
- **Arrangement buttons read backwards.** A single button labelled with the
  *current* mode was ambiguous — it was impossible to tell whether the label
  named the state or the action. Replaced with two checkable buttons,
  Overlay and Stacked, with exactly one lit at any time.
- **Spectrum names moved when a spectrum was scaled.** The 0.0.5 labels were
  anchored to each trace's data maximum, so scaling dragged the name around
  the plot — the opposite of what was asked for. They are now pinned in
  axes-fraction coordinates, top-left, one per line, and never move.
- **Y offset could not be set.** Dragging was restricted to stacked mode, so
  in overlay it silently did nothing, and there was no way to type an exact
  value. Dragging now works in both arrangements, and a Y offset box sits
  beside Y scale in the spectra panel. Its range covers real NMR intensities
  (±1e12) rather than a token ±100.
- **The ppm range would not accept "0 to 10".** Editing either box applied a
  range immediately, built from that value plus whatever stale value was in
  the other box — so a two-box range could never be entered cleanly. Nothing
  is applied now until **Apply** is pressed, at which point both values are
  read together and normalised to the descending order the axis uses.

### Changed
- **Line style and width moved out of the canvas panel into Preferences**,
  where the rest of the appearance settings live.
- **Preferences now covers eight spectrum slots**, each with its own colour,
  line style (solid / dashed / dotted / dash-dot) and line width. Slot N
  applies to the Nth spectrum loaded, so appearance is configurable before
  anything is dropped, not only afterwards.
- The spectra panel keeps only what is genuinely per-loaded-spectrum:
  selection, visibility, Y scale, Y offset, an individual colour override,
  and Remove.

### Known limitations
- 2D spectra still are not displayed; dropping one says so.
- Preferences are not persisted between runs.

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
